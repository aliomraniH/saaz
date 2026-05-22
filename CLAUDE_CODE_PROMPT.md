# Claude Code Web prompt — bootstrap `saaz`

This is the prompt to paste into Claude Code Web's first message after pushing this repo to GitHub and importing it into Replit (or running locally). It walks Claude Code through the migrations, seed, enrichment, and verification end-to-end.

---

You're bootstrapping `saaz`, a small demo dataset of Persian songwriters built to feed the `mneme` middleware project. Read these files before doing anything else:

1. `README.md` — overall purpose and constraints
2. `migrations/0001_init.sql` — the database schema (frozen; do not modify)
3. `seed_data/artists.json` — ~30 hand-curated artists; treat this as canonical for the seed pass
4. `scripts/enrich.py` and `scripts/refresh.py` — read them so you understand how data flows

After reading, do the following in order. Stop and report after each step.

**Step 1 — Verify environment.** Confirm these env vars are populated in Replit Secrets (or print which are missing):
- `DATABASE_URL` (required)
- At least one of: `ANTHROPIC_API_KEY`, `PERPLEXITY_API_KEY` (need at least one for bios beyond Wikipedia)
- At least one of: `VOYAGE_API_KEY`, `OPENAI_API_KEY` (for embeddings)

If anything is missing, stop and tell me what's needed before continuing.

**Step 2 — Install + migrate.** Run `make install`, then `make migrate`. Confirm via `psql` that all six tables exist (`artist`, `artist_link`, `artist_image`, `song`, `enrichment_run`, `data_provenance`) and that the `vector` extension is installed.

**Step 3 — Seed.** Run `make seed`. Verify that `select count(*) from artist` matches the count in `seed_data/artists.json` (should be ~30).

**Step 4 — Enrich.** Run `make enrich`. This will hit Wikipedia for every row and, if API keys are set, also Perplexity and/or Anthropic. Watch for:
- Wikipedia hits should be free and high-confidence (~0.85)
- Perplexity calls should cost <$0.01 each
- Anthropic calls should cost <$0.05 each
- Total run cost target: under $1 for ~30 artists

If you see repeated 4xx or 5xx errors from one provider, stop and report — don't burn budget.

**Step 5 — Embed.** Run `make embed`. This computes 1536-dim embeddings for every artist with a bio. Voyage is preferred (handles Persian better); OpenAI is the fallback.

**Step 6 — Verify.** Run `make verify` and paste the full output back to me. I want to see:
- Row counts per table
- Bios per genre
- Bio source breakdown (`seed` / `wikipedia` / `perplexity` / `anthropic_web`)
- Total enrichment cost in USD
- Sample artists by genre

**Step 7 — Smoke test a query.** Run this in psql and show me the result:

```sql
SELECT a.name_en, a.genre, length(a.bio) AS bio_len,
       (SELECT count(*) FROM artist_link l WHERE l.artist_id = a.id) AS n_links,
       (SELECT count(*) FROM data_provenance p WHERE p.fact_id = a.id) AS n_provenance,
       (a.embedding IS NOT NULL) AS has_embedding
FROM artist a
ORDER BY a.genre, a.name_en;
```

**Step 8 — Set up the scheduler.** In Replit, configure a Scheduled Deployment that runs `make refresh` weekly (Mondays 06:00 UTC is fine). Confirm the deployment is created and show me the next-run timestamp. Do **not** trigger it manually — let it run on schedule first.

## Hard rules
- Don't modify `migrations/0001_init.sql` or `seed_data/artists.json`. If something looks wrong, surface it and wait.
- Don't add new dependencies without asking. The whole project should run on what's in `pyproject.toml`.
- Don't add more than 30-ish seed artists. The point is *small and varied*, not comprehensive.
- If an enrichment provider returns a URL you can't verify, store it but set `verified=FALSE` and `confidence < 0.5`. Never invent URLs.
- Don't store copyrighted images. The schema stores image URLs only; if you mirror to Vercel Blob (optional), use only thumbnails.

## After everything is green
Report back the verify output, the smoke-test query result, and the scheduler config. I'll then connect this database into `mneme` as one of its tracked `db_namespace` values (probably `saaz_demo`).

---

## Optional later prompts

**Add more artists by hand:**
> Add the following artists to `seed_data/artists.json` with their Wikipedia links, then re-run `make seed enrich embed`: [list].

**Inspect what the discovery job would find without inserting:**
> Run `python -m scripts.refresh --skip-discovery` (re-enrich only). Then run a one-off `python -c "..."` that calls `discover_new_artists` and prints the candidates without inserting.

**Reset and reseed:**
> Truncate `artist`, `artist_link`, `artist_image`, `song`, `enrichment_run`, `data_provenance` and re-run `make all`. Confirm before truncating.
