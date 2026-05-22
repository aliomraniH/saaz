# saaz

A small, curated demo dataset of Persian songwriters and artists (Persian Jazz, Indie Persian Jazz, traditional). Produces a Postgres + pgvector database of ~30 artists with biographies, links, images, and embeddings, **and serves it over MCP** for `mneme` to proxy.

## What this project is

This project does two things in one Repl:

1. **MCP server** (`scripts/mcp_server.py`) — exposes the saaz database over the Model Context Protocol via Streamable-HTTP on port 5000. This is the URL `mneme` proxies to (`UPSTREAM_DB_MCP_URL`).
2. **Weekly refresh job** — runs `make refresh` in-process via APScheduler on the cron schedule in `REFRESH_CRON` (default `0 6 * * 1` — Mondays 06:00 UTC).

There is no separate Scheduled Deployment any more; both responsibilities live in the same Autoscale deployment.

## MCP endpoint

- Dev:  `https://$REPLIT_DEV_DOMAIN/mcp`
- Prod: `https://<your-app>.replit.app/mcp` (after publishing)
- Transport: Streamable-HTTP (`POST /mcp` with `Accept: application/json, text/event-stream`)

### Tools exposed (all read-only)

| Tool | What it does |
|---|---|
| `list_tables` | Saaz tables and approximate row counts |
| `describe_table` | Columns / types for a saaz table |
| `query` | One SELECT statement, write/DDL blocked, 10s timeout, row cap |
| `get_artist` | Fetch one artist with links / images / provenance |
| `list_artists` | Filter by genre / status, paginated |
| `search_artists` | Semantic search via pgvector + OpenAI embeddings |
| `stats` | Row counts, by-genre coverage, enrichment cost |

## Layout

- `scripts/` — Python entry points (`mcp_server.py`, `seed.py`, `enrich.py`, `embed.py`, `verify.py`, `refresh.py`, `check_mneme.py`)
- `migrations/0001_init.sql` — Postgres schema (frozen)
- `seed_data/artists.json` — ~30 hand-curated artists (canonical seed)
- `docs/` — `CLAUDE_CODE_PROMPT.md`, `SCHEDULER.md`
- `Makefile` — entry-point commands
- `pyproject.toml` — Python deps (managed with `uv`)

## Environment

- Python 3.12 with `uv` (`.pythonlibs` virtualenv)
- Replit-provided PostgreSQL with `vector` + `pgcrypto` extensions
- `DATABASE_URL` is provided automatically by Replit

Optional secrets (pipeline degrades gracefully if missing):
- `ANTHROPIC_API_KEY` / `PERPLEXITY_API_KEY` — bio enrichment
- `OPENAI_API_KEY` / `VOYAGE_API_KEY` — embeddings (also needed by `search_artists`)
- `BLOB_READ_WRITE_TOKEN` — optional image mirror
- `REFRESH_CRON` — override the weekly schedule (default `0 6 * * 1`)
- `HOST` / `PORT` — override the server bind (default `0.0.0.0:5000`)

## Cohabitation with `mneme`

`saaz` and `mneme` share one Replit Helium Postgres instance. `saaz` owns `artist`, `artist_link`, `artist_image`, `song`, `enrichment_run`, `data_provenance`. `mneme` owns `query_episode`, `expertise_note`, etc. Neither project writes to the other's tables. The MCP server here only ever runs read-only SELECTs against the saaz tables.

## Running locally in this Repl

```bash
make migrate     # apply schema
make seed        # load 30 artists from seed_data/artists.json
make enrich      # bios from Wikipedia + Anthropic / Perplexity
make embed       # 1536-dim embeddings via Voyage / OpenAI
make verify      # row counts + sample
make refresh     # the weekly job (also runs automatically in-process)
make check-mneme # read-only diagnostic on mneme's tables (if present)
```

Current state: 30 artists, 30 embeddings, 22 wikipedia bios + 8 anthropic bios, 61 links, 14 images, 97 provenance rows.

## Deployment

**Autoscale** running `uv run python -m scripts.mcp_server` on port 5000. The weekly refresh runs inside that same process via APScheduler — no separate Scheduled Deployment.

## User preferences

(none recorded yet)
