# Scheduler design

The weekly refresh job (`scripts/refresh.py`) does two things:

1. **Re-enrich stale rows.** Any artist whose `updated_at` is older than 60 days, or whose `bio_source` is still `seed`, is re-fetched. Capped at 5 per run to keep cost predictable.

2. **Discover upcoming artists.** Calls Perplexity (preferred) or Anthropic with a discovery prompt that asks for 1–3 emerging Persian jazz / indie / traditional artists with verifiable online presence. Inserts them as `status='upcoming'` for human review.

## Why pull-only

The job never deletes, never overwrites a `confirmed_by_user=TRUE` row, and never publishes an artist without a `source_url`. New rows land with `status='upcoming'` so they're visible-but-flagged in any downstream UI. A human (you, or someone you trust) reviews the `data_provenance.source_url` before promoting them to `active`.

## Cost ceiling

- Re-enrich: 5 artists × ~$0.01 (Perplexity) + ~$0.02 (Anthropic) = **~$0.15/week**
- Discovery: 1 Perplexity call + maybe 1 Anthropic call = **~$0.05/week**
- Total: **~$0.20/week**, or ~$10/year.

If costs spike past $1 in any single run, the job logs a warning to `enrichment_run` but continues. Future improvement: hard cap via `MNEME_MAX_RUN_USD`.

## Schedule

In Replit's Deploy tab:
- **Type:** Scheduled Deployment
- **Schedule:** `0 6 * * 1` (Mondays 06:00 UTC)
- **Command:** `make refresh`

Per Replit's docs, Scheduled Deployments cap at 11 hours per run, which is far more than we need.

## What an upcoming row looks like

```sql
SELECT slug, name_en, genre, status, bio, bio_source
FROM artist WHERE status = 'upcoming' AND created_at > now() - interval '14 days';
```

Each upcoming row has:
- `status = 'upcoming'`
- `era = 'emerging'`
- `bio_source = 'discovery'`
- One row in `data_provenance` with `source = 'discovery'`, a `source_url`, and `confidence ~= 0.5`

## Promoting an upcoming artist

```sql
UPDATE artist SET status = 'active' WHERE slug = '<slug>';
INSERT INTO data_provenance (fact_table, fact_id, fact_column, source, confidence, notes)
VALUES ('artist', '<id>', 'status', 'user', 1.0, 'human-reviewed and approved');
```

Or simpler: re-run `python -m scripts.enrich --slug <slug>` to get a real bio for them.

## Rejecting an upcoming artist

```sql
DELETE FROM artist WHERE slug = '<slug>' AND status = 'upcoming';
```

The cascade clears their links, images, and provenance.

## Caps and limits (constants in `refresh.py`)

| Constant | Value | Why |
|---|---|---|
| `MAX_NEW_PER_RUN` | 3 | Keeps the queue reviewable |
| stale cutoff | 60 days | Persian music scene moves slowly; weekly is overkill but cheap |
| re-enrich batch | 5 / run | Cost cap |
