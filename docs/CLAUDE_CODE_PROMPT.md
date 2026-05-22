# Claude Code Web prompt — bootstrap `saaz`

This is the prompt to paste into Claude Code Web's first message after pushing this repo to GitHub and importing it into Replit. It walks Claude Code through environment verification, migrations, seed, enrichment, embedding, verification, and scheduler setup — and it explicitly protects the coexisting `mneme` tables in the same Postgres.

---

You're bootstrapping `saaz`, a small demo dataset of Persian songwriters built to feed the `mneme` middleware project. Read these files before doing anything else:

1. `README.md` — overall purpose and constraints
2. `migrations/0001_init.sql` — the database schema (frozen; do not modify)
3. `seed_data/artists.json` — ~30 hand-curated artists; treat this as canonical for the seed pass
4. `scripts/enrich.py` and `scripts/refresh.py` — read them so you understand how data flows

After reading, do the following in order. Stop and report after each step.

**Step 0 — Verify the environment.** Confirm these are set in Replit Secrets and that the Postgres is reachable:

- `DATABASE_URL` — should be `postgresql://postgres:<password>@helium/heliumdb?sslmode=disable`. Internal hostname `helium` only resolves from inside Replit; this URL will NOT work from a laptop, so run all the steps below from inside the Repl.
- At least one of `ANTHROPIC_API_KEY`, `PERPLEXITY_API_KEY` (needed for bios beyond Wikipedia).
- At least one of `VOYAGE_API_KEY`, `OPENAI_API_KEY` (for embeddings).

Run `psql "$DATABASE_URL" -c "SELECT version(), current_database();"` to prove the Postgres is reachable. Then run `\dt` and tell me **all** the tables that exist, not just the saaz ones. If you see tables like `query_episode`, `expertise_note`, `store`, or `checkpoints` — that's `mneme` already living in this Postgres. That's expected and fine. **Do not drop, alter, or truncate any of those.** They belong to mneme.

If anything is missing, stop and tell me what's needed before continuing.

**Step 1 — Install + migrate.** Run `make install`, then `make migrate`. This applies `migrations/0001_init.sql`, which uses `CREATE TABLE IF NOT EXISTS` everywhere, so it's safe to run even if some saaz tables already exist. After it runs, confirm that all six saaz tables exist (`artist`, `artist_link`, `artist_image`, `song`, `enrichment_run`, `data_provenance`) and that the `vector` extension is installed. Print the result of `\dt` and tell me which tables in the output belong to saaz vs. mneme vs. LangGraph.

**Step 2 — Seed.** Run `make seed`. Verify that `select count(*) from artist` matches the count in `seed_data/artists.json` (should be 30).

**Step 3 — Enrich.** Run `make enrich`. This will hit Wikipedia for every row and, if API keys are set, also Perplexity and/or Anthropic. Watch for:
- Wikipedia hits should be free and high-confidence (~0.85)
- Perplexity calls should cost <$0.01 each
- Anthropic calls should cost <$0.05 each
- Total run cost target: under $1 for 30 artists

If you see repeated 4xx or 5xx errors from one provider, stop and report — don't burn budget.

**Step 4 — Embed.** Run `make embed`. This computes 1536-dim embeddings for every artist with a bio. Voyage is preferred (handles Persian better); OpenAI is the fallback.

**Step 5 — Verify.** Run `make verify` and paste the full output back to me. I want to see:
- Row counts per saaz table
- Bios per genre
- Bio source breakdown (`seed` / `wikipedia` / `perplexity` / `anthropic_web`)
- Total enrichment cost in USD
- Sample artists by genre

**Step 6 — Smoke test a query.** Run this in psql and show me the result:

```sql
SELECT a.name_en, a.genre, length(a.bio) AS bio_len,
       (SELECT count(*) FROM artist_link l WHERE l.artist_id = a.id) AS n_links,
       (SELECT count(*) FROM data_provenance p WHERE p.fact_id = a.id) AS n_provenance,
       (a.embedding IS NOT NULL) AS has_embedding
FROM artist a
ORDER BY a.genre, a.name_en;
```

**Step 7 — Confirm mneme tables are untouched.** Run:

```sql
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN ('query_episode', 'expertise_note', 'cache_event',
                     'db_schema_snapshot', 'column_doc', 'store', 'checkpoints');
```

Tell me which of these exist. If any exist, also report `select count(*) from <table>` for each — to prove saaz didn't accidentally modify mneme state. We expect saaz to have **never written** to any of these.

**Step 8 — Set up the scheduler.** In Replit, configure a Scheduled Deployment that runs `make refresh` weekly (Mondays 06:00 UTC is fine). Confirm the deployment is created and show me the next-run timestamp. Do **not** trigger it manually — let it run on schedule first.

## Hard rules

- The Postgres at `$DATABASE_URL` is **shared with mneme**. Never `DROP`, `TRUNCATE`, or `ALTER` any table whose name appears in this list: `query_episode`, `expertise_note`, `cache_event`, `db_schema_snapshot`, `column_doc`, `store`, `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations`. If you need to reset state, only touch saaz tables: `artist`, `artist_link`, `artist_image`, `song`, `enrichment_run`, `data_provenance`.
- Don't modify `migrations/0001_init.sql` or `seed_data/artists.json`. If something looks wrong, surface it and wait.
- Don't add new dependencies without asking. The whole project should run on what's in `pyproject.toml`.
- Don't add more than 30-ish seed artists. The point is *small and varied*, not comprehensive.
- If an enrichment provider returns a URL you can't verify, store it but set `verified=FALSE` and `confidence < 0.5`. Never invent URLs.
- Don't store copyrighted images. The schema stores image URLs only; if you mirror to Vercel Blob (optional), use only thumbnails.

## After everything is green
Report back the verify output, the smoke-test query result, the mneme-tables-untouched proof, and the scheduler config. I'll then point `mneme` at this same Postgres so it can observe queries against the saaz tables as the `saaz_demo` namespace.

---

## Optional later prompts

**Sanity-check saaz from inside the mneme repo (or vice versa):**
> Connect to `$DATABASE_URL` and run the health-check queries from saaz's CLAUDE_CODE_PROMPT.md § Step 6. Just confirm row counts and provenance look right; don't modify anything.

**Add more artists by hand:**
> Add the following artists to `seed_data/artists.json` with their Wikipedia links, then re-run `make seed enrich embed`: [list].

**Inspect what the discovery job would find without inserting:**
> Run `python -m scripts.refresh --skip-discovery` (re-enrich only). Then run a one-off `python -c "..."` that calls `discover_new_artists` and prints the candidates without inserting.

**Reset saaz state but leave mneme alone:**
> Truncate ONLY these tables: `artist`, `artist_link`, `artist_image`, `song`, `enrichment_run`, `data_provenance`. Then re-run `make seed enrich embed verify`. Confirm before truncating. Do NOT touch any other table in the database.
