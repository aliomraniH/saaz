"""
saaz/scripts/mcp_server.py

A read-only Postgres MCP server that exposes the saaz database over the
Model Context Protocol (Streamable-HTTP transport). Designed to be wrapped
by mneme via UPSTREAM_DB_MCP_URL.

Also runs the weekly `make refresh` job in-process via APScheduler so this
single Autoscale deployment covers both responsibilities.

Tools exposed (all read-only):
    list_tables          -> list of saaz tables
    describe_table       -> column / type info for a table
    query                -> run a single SELECT statement (read-only enforced)
    get_artist           -> fetch one artist with links/images/provenance
    search_artists       -> semantic search via pgvector (uses OpenAI embeddings)
    list_artists         -> filter + page through artists
    stats                -> row counts + bio-source breakdown

Usage:
    python -m scripts.mcp_server
    # binds 0.0.0.0:5000, serves Streamable-HTTP at /mcp
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import subprocess
import sys
from datetime import datetime
from typing import Any

import httpx
import psycopg
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from mcp.server.fastmcp import FastMCP
from psycopg.rows import dict_row

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("saaz.mcp")

DATABASE_URL = os.environ.get("DATABASE_URL")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "text-embedding-3-small")
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "5000"))
REFRESH_CRON = os.environ.get("REFRESH_CRON", "0 6 * * 1")

SAAZ_TABLES = {
    "artist",
    "artist_link",
    "artist_image",
    "song",
    "enrichment_run",
    "data_provenance",
}

mcp = FastMCP("saaz-db", host=HOST, port=PORT)


def _conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


_SELECT_RE = re.compile(r"^\s*(with\b.*?\bselect\b|select\b)", re.IGNORECASE | re.DOTALL)
_FORBIDDEN_RE = re.compile(
    r"\b(insert|update|delete|drop|truncate|alter|create|grant|revoke|copy|vacuum)\b",
    re.IGNORECASE,
)


def _assert_readonly(sql: str) -> None:
    if ";" in sql.rstrip(";").strip():
        raise ValueError("Multiple statements are not allowed")
    if not _SELECT_RE.match(sql):
        raise ValueError("Only SELECT (and WITH ... SELECT) statements are allowed")
    if _FORBIDDEN_RE.search(sql):
        raise ValueError("Write/DDL keywords are not allowed in this read-only endpoint")


def _jsonable(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, (bytes, bytearray)):
        return obj.decode("utf-8", errors="replace")
    if isinstance(obj, list):
        return [_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    return obj


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def list_tables() -> list[dict]:
    """List the saaz tables in this database (read-only)."""
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """SELECT table_name,
                      (SELECT reltuples::bigint FROM pg_class
                       WHERE relname = table_name) AS approx_rows
               FROM information_schema.tables
               WHERE table_schema = 'public' AND table_name = ANY(%s)
               ORDER BY table_name""",
            (list(SAAZ_TABLES),),
        )
        return _jsonable(cur.fetchall())


@mcp.tool()
def describe_table(table: str) -> dict:
    """Describe columns of a saaz table.

    Args:
        table: One of artist, artist_link, artist_image, song, enrichment_run,
               data_provenance.
    """
    if table not in SAAZ_TABLES:
        raise ValueError(f"Unknown saaz table: {table}. Allowed: {sorted(SAAZ_TABLES)}")
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """SELECT column_name, data_type, is_nullable, column_default
               FROM information_schema.columns
               WHERE table_schema='public' AND table_name = %s
               ORDER BY ordinal_position""",
            (table,),
        )
        cols = cur.fetchall()
        cur.execute(
            """SELECT count(*) AS n FROM information_schema.tables
               WHERE table_schema='public' AND table_name=%s""",
            (table,),
        )
        exists = cur.fetchone()["n"] > 0
        return _jsonable({"table": table, "exists": exists, "columns": cols})


@mcp.tool()
def query(sql: str, limit: int = 200) -> dict:
    """Run a single read-only SELECT statement against the saaz database.

    Args:
        sql: A SELECT or WITH ... SELECT statement. Multiple statements,
             writes, and DDL are rejected.
        limit: Hard cap on returned rows (default 200, max 1000).
    """
    _assert_readonly(sql)
    limit = max(1, min(int(limit), 1000))
    with _conn() as c, c.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = '10s'")
        cur.execute("SET LOCAL default_transaction_read_only = on")
        cur.execute(sql)
        rows = cur.fetchmany(limit)
        return _jsonable({
            "row_count": len(rows),
            "truncated": len(rows) == limit,
            "rows": rows,
        })


@mcp.tool()
def get_artist(slug: str) -> dict:
    """Fetch one artist by slug with links, images, and provenance."""
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """SELECT id, slug, name_en, name_fa, name_translit, genre, sub_genres,
                      era, status, country, based_in, born, died, bio, bio_source,
                      (embedding IS NOT NULL) AS has_embedding,
                      created_at, updated_at
               FROM artist WHERE slug = %s""",
            (slug,),
        )
        artist = cur.fetchone()
        if not artist:
            return {"error": f"no artist with slug={slug!r}"}
        aid = artist["id"]
        cur.execute(
            "SELECT kind, url, label, verified FROM artist_link WHERE artist_id=%s ORDER BY kind",
            (aid,),
        )
        artist["links"] = cur.fetchall()
        cur.execute(
            """SELECT url, mirror_url, caption, source, license, is_primary
               FROM artist_image WHERE artist_id=%s ORDER BY is_primary DESC""",
            (aid,),
        )
        artist["images"] = cur.fetchall()
        cur.execute(
            """SELECT source, source_url, confidence, retrieved_at
               FROM data_provenance WHERE fact_id=%s
               ORDER BY retrieved_at DESC""",
            (aid,),
        )
        artist["provenance"] = cur.fetchall()
        return _jsonable(artist)


@mcp.tool()
def list_artists(
    genre: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """List artists, optionally filtered by genre or status.

    Args:
        genre: persian_jazz | indie_persian_jazz | traditional | other
        status: active | upcoming | legacy | deceased
        limit: max rows (default 50, max 200)
    """
    limit = max(1, min(int(limit), 200))
    where, params = [], []
    if genre:
        where.append("genre = %s")
        params.append(genre)
    if status:
        where.append("status = %s")
        params.append(status)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            f"""SELECT slug, name_en, name_fa, genre, status, era, based_in,
                       length(bio) AS bio_len, bio_source
                FROM artist {clause}
                ORDER BY genre, name_en LIMIT %s""",
            (*params, limit),
        )
        return _jsonable(cur.fetchall())


@mcp.tool()
def search_artists(query: str, limit: int = 10) -> list[dict]:
    """Semantic search across artist bios using pgvector + OpenAI embeddings.

    Requires OPENAI_API_KEY to embed the query at call time.

    Args:
        query: natural-language search ("traditional setar players in California")
        limit: top-K (default 10, max 50)
    """
    if not OPENAI_API_KEY:
        return [{"error": "OPENAI_API_KEY not set; semantic search disabled"}]
    limit = max(1, min(int(limit), 50))
    with httpx.Client(timeout=20) as h:
        r = h.post(
            "https://api.openai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            json={"model": OPENAI_MODEL, "input": query, "dimensions": 1536},
        )
        r.raise_for_status()
        vec = r.json()["data"][0]["embedding"]
    vec_str = "[" + ",".join(f"{x:.7f}" for x in vec) + "]"
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """SELECT slug, name_en, name_fa, genre, status, bio,
                      1 - (embedding <=> %s::vector) AS similarity
               FROM artist
               WHERE embedding IS NOT NULL
               ORDER BY embedding <=> %s::vector
               LIMIT %s""",
            (vec_str, vec_str, limit),
        )
        return _jsonable(cur.fetchall())


@mcp.tool()
def stats() -> dict:
    """Row counts and high-level health stats for the saaz dataset."""
    out: dict[str, Any] = {}
    with _conn() as c, c.cursor() as cur:
        counts = {}
        for t in sorted(SAAZ_TABLES):
            cur.execute(f"SELECT count(*) AS n FROM {t}")
            counts[t] = cur.fetchone()["n"]
        out["row_counts"] = counts
        cur.execute(
            """SELECT genre, count(*) AS n,
                      count(bio) AS with_bio,
                      count(embedding) AS with_embedding
               FROM artist GROUP BY genre ORDER BY genre"""
        )
        out["by_genre"] = cur.fetchall()
        cur.execute("SELECT bio_source, count(*) AS n FROM artist GROUP BY bio_source")
        out["bio_sources"] = cur.fetchall()
        cur.execute(
            """SELECT source, count(*) AS calls, round(sum(cost_usd)::numeric, 4) AS cost_usd
               FROM enrichment_run GROUP BY source"""
        )
        out["enrichment_cost"] = cur.fetchall()
    return _jsonable(out)


# ---------------------------------------------------------------------------
# Background scheduler: run `make refresh` weekly inside this process.
# ---------------------------------------------------------------------------


def _parse_cron(expr: str) -> CronTrigger:
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError(f"REFRESH_CRON must be 5 fields, got: {expr!r}")
    minute, hour, dom, month, dow = parts
    return CronTrigger(minute=minute, hour=hour, day=dom, month=month, day_of_week=dow)


async def _run_refresh() -> None:
    log.info("scheduled refresh: starting make refresh")
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "scripts.refresh",
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        out, _ = await proc.communicate()
        log.info("scheduled refresh: exit=%s output=\n%s", proc.returncode, out.decode("utf-8", "replace"))
    except Exception as e:  # noqa: BLE001
        log.exception("scheduled refresh failed: %s", e)


_scheduler: AsyncIOScheduler | None = None


def _start_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        return
    try:
        trigger = _parse_cron(REFRESH_CRON)
    except Exception as e:  # noqa: BLE001
        log.warning("invalid REFRESH_CRON=%r (%s); scheduler disabled", REFRESH_CRON, e)
        return
    _scheduler = AsyncIOScheduler()
    _scheduler.add_job(_run_refresh, trigger, id="refresh", coalesce=True, max_instances=1)
    _scheduler.start()
    log.info("scheduler started; refresh cron=%r", REFRESH_CRON)


# Hook the scheduler onto FastMCP's underlying Starlette app lifespan.
_app = mcp.streamable_http_app()


@contextlib.asynccontextmanager
async def _combined_lifespan(app):
    async with mcp.session_manager.run():
        _start_scheduler()
        try:
            yield
        finally:
            if _scheduler:
                _scheduler.shutdown(wait=False)


_app.router.lifespan_context = _combined_lifespan


def main() -> None:
    import uvicorn

    if not DATABASE_URL:
        sys.exit("DATABASE_URL is required")
    log.info("saaz MCP server listening on http://%s:%d/mcp", HOST, PORT)
    uvicorn.run(_app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
