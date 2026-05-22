"""
saaz/scripts/enrich.py

Enrichment pipeline. For each artist in the database whose bio is missing or
bio_source = 'seed', fetch:
  1. Wikipedia summary + infobox (free, no API key required)
  2. Perplexity Sonar (if PERPLEXITY_API_KEY set) — citations + recency
  3. Anthropic web search (if ANTHROPIC_API_KEY set) — fallback / cross-check

Every fact written carries a row in `data_provenance` with source URL and confidence.
Every external call carries a row in `enrichment_run` with cost.

Usage:
    python -m scripts.enrich                       # enrich all rows needing it
    python -m scripts.enrich --slug kayhan-kalhor  # one artist
    python -m scripts.enrich --limit 5             # cap the run
    python -m scripts.enrich --dry-run             # print, don't write

Safe to re-run. Wikipedia is idempotent (URL is a natural key); LLM-based
enrichers dedupe via prompt_hash in enrichment_run.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx
import psycopg
from psycopg.rows import dict_row

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DATABASE_URL = os.environ.get("DATABASE_URL")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
PERPLEXITY_API_KEY = os.environ.get("PERPLEXITY_API_KEY")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
PERPLEXITY_MODEL = os.environ.get("PERPLEXITY_MODEL", "sonar")
USER_AGENT = "saaz-enrichment/0.1 (https://github.com/YOU/saaz; demo)"

WIKIPEDIA_API = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
PERPLEXITY_API = "https://api.perplexity.ai/chat/completions"
ANTHROPIC_API = "https://api.anthropic.com/v1/messages"


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class Artist:
    id: str
    slug: str
    name_en: str
    name_fa: str | None
    genre: str
    bio: str | None
    bio_source: str | None


@dataclass
class EnrichResult:
    bio: str | None = None
    extra_links: list[dict[str, str]] | None = None
    image_url: str | None = None
    image_caption: str | None = None
    image_license: str | None = None
    source: str = ""
    source_url: str | None = None
    confidence: float = 0.0
    raw_response: dict[str, Any] | None = None
    cost_usd: float = 0.0


# ---------------------------------------------------------------------------
# Wikipedia (free, no key)
# ---------------------------------------------------------------------------

async def fetch_wikipedia(
    client: httpx.AsyncClient, artist: Artist, wiki_url: str | None
) -> EnrichResult | None:
    """Hit the Wikipedia REST summary endpoint. Returns None if no page found."""

    # If we have a wikipedia link from the seed, use its title. Otherwise guess
    # from the English name.
    if wiki_url:
        match = re.search(r"/wiki/(.+?)(?:[?#]|$)", wiki_url)
        title = match.group(1) if match else quote(artist.name_en.replace(" ", "_"))
    else:
        title = quote(artist.name_en.replace(" ", "_"))

    url = WIKIPEDIA_API.format(title=title)
    try:
        r = await client.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
    except httpx.TimeoutException:
        return None
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        return None

    data = r.json()
    if data.get("type") == "disambiguation":
        return None

    extract = data.get("extract")
    if not extract:
        return None

    page_url = data.get("content_urls", {}).get("desktop", {}).get("page") or wiki_url

    image_url = None
    image_caption = None
    image_license = None
    thumbnail = data.get("originalimage") or data.get("thumbnail")
    if thumbnail and thumbnail.get("source"):
        image_url = thumbnail["source"]
        image_caption = data.get("description")
        image_license = "wikipedia_check_individual"  # Wikipedia images vary; check each

    return EnrichResult(
        bio=extract,
        image_url=image_url,
        image_caption=image_caption,
        image_license=image_license,
        source="wikipedia",
        source_url=page_url,
        confidence=0.85,           # Wikipedia is generally reliable but not authoritative
        raw_response={"title": data.get("title"), "description": data.get("description")},
        cost_usd=0.0,
    )


# ---------------------------------------------------------------------------
# Perplexity Sonar — has built-in web search + citations
# ---------------------------------------------------------------------------

PERPLEXITY_PROMPT = """You are a careful music researcher. For the artist below,
return a JSON object (ONLY a JSON object, no markdown fence) with these fields:

  bio:           a 3-5 sentence biographical paragraph in English, factual and dry.
  links:         array of {{kind, url}}, where kind is one of
                 'youtube_channel', 'instagram', 'spotify', 'bandcamp',
                 'official_site', 'soundcloud'. Skip kinds you cannot verify.
  image_url:     direct URL to a photo of the artist if you find a clearly
                 reusable source (Wikipedia, official site, label page). Null otherwise.
  confidence:    your own 0..1 estimate of overall accuracy.

If you don't know something with reasonable confidence, leave it out or null.
Do NOT fabricate URLs. Cite your sources via Perplexity's citation mechanism.

Artist:
  Name (English): {name_en}
  Name (Persian/native): {name_fa}
  Genre: {genre}
"""


async def fetch_perplexity(
    client: httpx.AsyncClient, artist: Artist
) -> EnrichResult | None:
    if not PERPLEXITY_API_KEY:
        return None

    prompt = PERPLEXITY_PROMPT.format(
        name_en=artist.name_en,
        name_fa=artist.name_fa or "",
        genre=artist.genre,
    )
    body = {
        "model": PERPLEXITY_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 800,
    }
    try:
        r = await client.post(
            PERPLEXITY_API,
            headers={
                "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=60,
        )
    except httpx.TimeoutException:
        return None
    if r.status_code != 200:
        print(f"  perplexity error {r.status_code}: {r.text[:200]}", file=sys.stderr)
        return None

    data = r.json()
    text = data["choices"][0]["message"]["content"]
    citations = data.get("citations", [])

    parsed = _safe_json_extract(text)
    if not parsed:
        return None

    # Perplexity pricing (sonar small, May 2026): roughly $1/M input + $1/M output.
    # We don't have token counts in all responses; use a conservative flat estimate.
    cost = 0.005

    return EnrichResult(
        bio=parsed.get("bio"),
        extra_links=parsed.get("links") or [],
        image_url=parsed.get("image_url"),
        source="perplexity",
        source_url=citations[0] if citations else None,
        confidence=float(parsed.get("confidence") or 0.7),
        raw_response={"citations": citations, "parsed": parsed},
        cost_usd=cost,
    )


# ---------------------------------------------------------------------------
# Anthropic with web_search tool — fallback / cross-check
# ---------------------------------------------------------------------------

ANTHROPIC_PROMPT = """Look up the Persian/Iranian musician below and return ONLY a
JSON object (no markdown fence) with these fields:

  bio:        3-5 sentence factual English biography.
  links:      array of {{kind, url}}; kind in: 'youtube_channel', 'instagram',
              'spotify', 'bandcamp', 'official_site', 'soundcloud'.
  confidence: your own 0..1 estimate of overall accuracy.

Artist: {name_en} ({name_fa})
Genre tag: {genre}

Use web search to verify. Do not fabricate URLs."""


async def fetch_anthropic(
    client: httpx.AsyncClient, artist: Artist
) -> EnrichResult | None:
    if not ANTHROPIC_API_KEY:
        return None

    prompt = ANTHROPIC_PROMPT.format(
        name_en=artist.name_en,
        name_fa=artist.name_fa or "",
        genre=artist.genre,
    )
    body = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": 1024,
        "tools": [{"type": "web_search_20250305", "name": "web_search"}],
        "messages": [{"role": "user", "content": prompt}],
    }
    try:
        r = await client.post(
            ANTHROPIC_API,
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=body,
            timeout=90,
        )
    except httpx.TimeoutException:
        return None
    if r.status_code != 200:
        print(f"  anthropic error {r.status_code}: {r.text[:200]}", file=sys.stderr)
        return None

    data = r.json()

    # Anthropic returns content blocks; concatenate text blocks
    text_parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
    text = "\n".join(text_parts)

    parsed = _safe_json_extract(text)
    if not parsed:
        return None

    # Rough cost estimate from usage
    usage = data.get("usage", {})
    in_tok = usage.get("input_tokens", 0)
    out_tok = usage.get("output_tokens", 0)
    # Sonnet 4.5 pricing: $3/M input, $15/M output (approx, May 2026)
    cost = (in_tok * 3 + out_tok * 15) / 1_000_000

    return EnrichResult(
        bio=parsed.get("bio"),
        extra_links=parsed.get("links") or [],
        source="anthropic_web",
        source_url=None,  # Anthropic citations are in a different block format; skip for MVP
        confidence=float(parsed.get("confidence") or 0.7),
        raw_response={"parsed": parsed, "usage": usage},
        cost_usd=cost,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_json_extract(text: str) -> dict | None:
    """Find the first JSON object in `text` and parse it. Returns None on failure."""
    # Strip markdown fences
    text = re.sub(r"^```(?:json)?\s*", "", text.strip())
    text = re.sub(r"\s*```$", "", text)
    # Find the outermost {...}
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i, ch in enumerate(text[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# DB I/O
# ---------------------------------------------------------------------------

async def fetch_artists_needing_enrichment(
    conn: psycopg.AsyncConnection, slug: str | None, limit: int | None
) -> list[Artist]:
    sql = """
        SELECT id, slug, name_en, name_fa, genre, bio, bio_source
        FROM artist
        WHERE (bio IS NULL OR bio_source = 'seed')
    """
    params: list = []
    if slug:
        sql += " AND slug = %s"
        params.append(slug)
    sql += " ORDER BY created_at"
    if limit:
        sql += " LIMIT %s"
        params.append(limit)

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(sql, params)
        rows = await cur.fetchall()
    return [Artist(**r) for r in rows]


async def get_existing_wiki_url(
    conn: psycopg.AsyncConnection, artist_id: str
) -> str | None:
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT url FROM artist_link WHERE artist_id = %s AND kind = 'wikipedia' LIMIT 1",
            (artist_id,),
        )
        row = await cur.fetchone()
    return row[0] if row else None


async def write_enrichment(
    conn: psycopg.AsyncConnection, artist: Artist, result: EnrichResult, prompt: str
) -> int:
    """Apply EnrichResult to the database. Returns number of rows written."""
    rows_written = 0
    async with conn.cursor() as cur:

        # Bio
        if result.bio and (not artist.bio or artist.bio_source == "seed"):
            await cur.execute(
                "UPDATE artist SET bio = %s, bio_source = %s, updated_at = now() WHERE id = %s",
                (result.bio, result.source, artist.id),
            )
            await cur.execute(
                """INSERT INTO data_provenance
                       (fact_table, fact_id, fact_column, source, source_url, confidence)
                   VALUES ('artist', %s, 'bio', %s, %s, %s)""",
                (artist.id, result.source, result.source_url, result.confidence),
            )
            rows_written += 1

        # Links
        for link in result.extra_links or []:
            kind = link.get("kind")
            url = link.get("url")
            if not kind or not url or not url.startswith(("http://", "https://")):
                continue
            try:
                await cur.execute(
                    """INSERT INTO artist_link (artist_id, kind, url, verified)
                       VALUES (%s, %s, %s, FALSE)
                       ON CONFLICT (artist_id, url) DO NOTHING""",
                    (artist.id, kind, url),
                )
                if cur.rowcount > 0:
                    await cur.execute(
                        """INSERT INTO data_provenance
                               (fact_table, fact_id, fact_column, source, source_url, confidence)
                           VALUES ('artist_link', (SELECT id FROM artist_link WHERE artist_id = %s AND url = %s),
                                   'url', %s, %s, %s)""",
                        (artist.id, url, result.source, result.source_url, result.confidence),
                    )
                    rows_written += 1
            except psycopg.errors.CheckViolation:
                # Invalid kind, skip
                pass

        # Image
        if result.image_url and result.image_url.startswith(("http://", "https://")):
            await cur.execute(
                """INSERT INTO artist_image (artist_id, url, caption, source, license, is_primary)
                   VALUES (%s, %s, %s, %s, %s,
                           NOT EXISTS (SELECT 1 FROM artist_image WHERE artist_id = %s AND is_primary))
                   ON CONFLICT (artist_id, url) DO NOTHING""",
                (
                    artist.id,
                    result.image_url,
                    result.image_caption,
                    result.source,
                    result.image_license,
                    artist.id,
                ),
            )
            if cur.rowcount > 0:
                rows_written += 1

        # Enrichment run audit
        await cur.execute(
            """INSERT INTO enrichment_run
                   (artist_id, source, model, prompt_hash, status, rows_written, cost_usd, raw_response)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                artist.id,
                result.source,
                ANTHROPIC_MODEL if result.source == "anthropic_web"
                else PERPLEXITY_MODEL if result.source == "perplexity"
                else None,
                _hash(prompt),
                "success" if rows_written > 0 else "partial",
                rows_written,
                result.cost_usd,
                json.dumps(result.raw_response or {})[:5000],
            ),
        )
    return rows_written


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

async def enrich_artist(
    conn: psycopg.AsyncConnection,
    client: httpx.AsyncClient,
    artist: Artist,
    dry_run: bool,
) -> None:
    print(f"  ▸ {artist.slug} ({artist.name_en})")

    wiki_url = await get_existing_wiki_url(conn, artist.id)

    # 1. Wikipedia first (free, highest signal-to-noise for known artists)
    result = await fetch_wikipedia(client, artist, wiki_url)
    if result and result.bio:
        print(f"    ✓ wikipedia: {len(result.bio)} chars, conf={result.confidence}")
        if not dry_run:
            n = await write_enrichment(conn, artist, result, prompt=f"wiki:{artist.slug}")
            print(f"      wrote {n} rows")
        # If wiki gave us a bio, we usually don't need to call paid APIs.
        # Still, hit the LLM enrichers for social links (which Wikipedia rarely lists).

    # 2. Perplexity for socials + recency
    if PERPLEXITY_API_KEY:
        prompt_text = PERPLEXITY_PROMPT.format(
            name_en=artist.name_en, name_fa=artist.name_fa or "", genre=artist.genre
        )
        # Skip if we've already run this exact prompt
        if not await _already_ran(conn, artist.id, "perplexity", _hash(prompt_text)):
            ppx = await fetch_perplexity(client, artist)
            if ppx:
                print(f"    ✓ perplexity: {len(ppx.extra_links or [])} links, cost=${ppx.cost_usd:.4f}")
                if not dry_run:
                    n = await write_enrichment(conn, artist, ppx, prompt=prompt_text)
                    print(f"      wrote {n} rows")

    # 3. Anthropic web search as a fallback if we still have no bio
    if ANTHROPIC_API_KEY and (not result or not result.bio):
        prompt_text = ANTHROPIC_PROMPT.format(
            name_en=artist.name_en, name_fa=artist.name_fa or "", genre=artist.genre
        )
        if not await _already_ran(conn, artist.id, "anthropic_web", _hash(prompt_text)):
            ant = await fetch_anthropic(client, artist)
            if ant:
                print(f"    ✓ anthropic: bio={bool(ant.bio)}, cost=${ant.cost_usd:.4f}")
                if not dry_run:
                    n = await write_enrichment(conn, artist, ant, prompt=prompt_text)
                    print(f"      wrote {n} rows")


async def _already_ran(
    conn: psycopg.AsyncConnection, artist_id: str, source: str, prompt_hash: str
) -> bool:
    async with conn.cursor() as cur:
        await cur.execute(
            """SELECT 1 FROM enrichment_run
               WHERE artist_id = %s AND source = %s AND prompt_hash = %s
                 AND ran_at > now() - interval '7 days'
               LIMIT 1""",
            (artist_id, source, prompt_hash),
        )
        return (await cur.fetchone()) is not None


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--slug", help="Enrich only this artist")
    parser.add_argument("--limit", type=int, help="Cap the number of artists processed")
    parser.add_argument("--dry-run", action="store_true", help="Print but do not write")
    args = parser.parse_args()

    if not DATABASE_URL:
        sys.exit("DATABASE_URL is required")

    async with await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True) as conn:
        artists = await fetch_artists_needing_enrichment(conn, args.slug, args.limit)
        print(f"Found {len(artists)} artist(s) needing enrichment")

        async with httpx.AsyncClient() as client:
            for artist in artists:
                try:
                    await enrich_artist(conn, client, artist, args.dry_run)
                except Exception as e:  # noqa: BLE001
                    print(f"  ✗ {artist.slug}: {e}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
