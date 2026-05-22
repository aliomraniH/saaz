# saaz

A small, curated demo dataset of Persian songwriters and artists (Persian Jazz, Indie Persian Jazz, traditional). Produces a Postgres + pgvector database of ~30 artists with biographies, links, images, and embeddings.

## What this project is

This is a **CLI / data pipeline**, not a web app. It has no frontend and no long-running backend server. It is a set of Python scripts orchestrated by a `Makefile`, intended to run as a **Scheduled Deployment** on Replit (weekly refresh).

## Layout

- `scripts/` — Python entry points (`seed.py`, `enrich.py`, `embed.py`, `verify.py`, `refresh.py`)
- `migrations/0001_init.sql` — Postgres schema (frozen)
- `seed_data/artists.json` — ~30 hand-curated artists (canonical seed)
- `docs/` — `CLAUDE_CODE_PROMPT.md`, `SCHEDULER.md`
- `Makefile` — entry-point commands
- `pyproject.toml` — Python deps (managed with `uv`)

## Environment

- Python 3.12 with `uv` for dependency management (`.pythonlibs` virtualenv)
- Replit-provided PostgreSQL (with `vector` extension enabled by the migration)
- `DATABASE_URL` is provided automatically by Replit

Optional secrets (the pipeline degrades gracefully if missing):
- `ANTHROPIC_API_KEY` and/or `PERPLEXITY_API_KEY` — for bio enrichment beyond Wikipedia
- `VOYAGE_API_KEY` and/or `OPENAI_API_KEY` — for embeddings
- `BLOB_READ_WRITE_TOKEN` — optional Vercel Blob mirror for image thumbnails

## Running locally in this Repl

```bash
make migrate     # apply schema (idempotent-ish; CREATE TABLE without IF NOT EXISTS in some places)
make seed        # load 30 artists from seed_data/artists.json
make enrich      # requires API keys
make embed       # requires API keys
make verify      # row counts + sample
make refresh     # the scheduled job
```

Current bootstrap state: `make migrate` and `make seed` have been run successfully (30 artists, 30 provenance rows, 24 links). `enrich`/`embed` need API keys.

## Deployment

Configured as a **Scheduled Deployment** running `make refresh` (default schedule should be set in the Deploy tab — `0 6 * * 1`, weekly Mondays 06:00 UTC, per `docs/SCHEDULER.md`).

No port is bound; no workflow runs in the background.

## User preferences

(none recorded yet)
