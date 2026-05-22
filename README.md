# saaz

> A small, curated demo dataset of Persian songwriters and artists — Persian Jazz, Indie Persian Jazz, and traditional music.

**saaz** (Persian: ساز, "instrument") is a tiny seed-and-enrich project that produces a Postgres + pgvector database of ~30–50 Persian artists with biographies, links (YouTube, Instagram, Wikipedia, Spotify, Bandcamp), images, and embeddings. Built to be the demo data for [mneme](https://github.com/YOU/mneme) — small enough to test middleware features against, varied enough to be interesting.

## What's in here

- A **starter seed** of ~30 hand-picked artists across three genres (`persian_jazz`, `indie_persian_jazz`, `traditional`)
- An **enrichment pipeline** that calls Wikipedia + Perplexity / Anthropic web-search to fill in bios, links, and images
- A **scheduled refresh** job (weekly via Replit Scheduled Deployments) that polls for new releases and adds upcoming artists
- A Postgres schema with images stored as URLs (we archive into Vercel Blob, not the database)

## Status

🌱 Demo seed only. Not a production music database. Don't trust the facts blindly — every enrichment write carries a `source` and `confidence` so downstream code (mneme) can reason about provenance.

## Quick start

```bash
git clone https://github.com/YOU/saaz.git
cd saaz
uv sync

cp .env.example .env
# fill in: DATABASE_URL, ANTHROPIC_API_KEY, PERPLEXITY_API_KEY (optional), BLOB_READ_WRITE_TOKEN

make migrate           # create schema
make seed              # load hand-curated artists from seed_data/
make enrich            # fetch bios + links from Wikipedia + Perplexity/Anthropic
make embed             # generate embeddings for semantic search
make verify            # sanity-check the resulting DB
```

The total runtime is ~5 minutes for the default 30 artists and costs ~$0.30 in API calls.

## Cohabitation with `mneme`

`saaz` and [`mneme`](https://github.com/YOU/mneme) share one Replit Helium Postgres instance. `saaz` owns the `artist`, `artist_link`, `artist_image`, `song`, `enrichment_run`, and `data_provenance` tables. `mneme` owns its own memory tables (`query_episode`, `expertise_note`, etc.). They coexist in the `public` schema without colliding. The saaz scripts only touch saaz tables; never run `DROP` or `TRUNCATE` against tables you don't recognize.

## Schema (at a glance)

```
artist                  → id, name (en/fa), genre, era, status (active/upcoming/legacy), bio, embedding
artist_link             → artist_id, kind (youtube/instagram/wikipedia/spotify/bandcamp), url, verified
artist_image            → artist_id, url, caption, source, is_primary
song                    → id, artist_id, title (en/fa), year, album, youtube_url, embedding
enrichment_run          → id, ran_at, source, model, artist_id, prompt_hash, cost_usd, status
data_provenance         → fact_table, fact_id, source, source_url, confidence, retrieved_at
```

Full schema in [`migrations/0001_init.sql`](migrations/0001_init.sql).

## Roadmap

This project is intentionally small. The roadmap is:

1. ✅ Seed schema and 30 hand-picked artists
2. ✅ Wikipedia + Perplexity enrichment
3. ✅ Embedding generation (pgvector)
4. ⏭ Weekly refresh job adding 1–3 upcoming artists
5. ⏭ Hook up to `mneme` as one of its tracked databases

See [`docs/SCHEDULER.md`](docs/SCHEDULER.md) for the refresh-job design.

## How to use it with Claude Code

A starter prompt for Claude Code Web is in [`docs/CLAUDE_CODE_PROMPT.md`](docs/CLAUDE_CODE_PROMPT.md). The short version:

1. Push this repo to GitHub
2. Import into Replit (or run locally)
3. Create a Vercel Postgres (or Neon) instance and set `DATABASE_URL`
4. Paste the prompt from `CLAUDE_CODE_PROMPT.md` into Claude Code Web
5. Claude Code wires up the migrations, runs the seed + enrichment, and reports back with the resulting row counts

## Licensing and data ethics

- **Code:** MIT.
- **Curated facts:** the seed file is hand-written from public sources and stored under CC0. Enriched data carries provenance to its original source.
- **Images:** we store **URLs only**, never re-host copyrighted images. The optional Vercel Blob mirror downloads thumbnails only and respects robots.txt.
- **Living people:** every biographical claim about a named living person carries a `source_url` in `data_provenance`. If anyone listed asks to be removed, delete the row — it's a demo, not a record of authority.
