"""
saaz/scripts/refresh.py

Weekly job (run via Replit Scheduled Deployments):
  1. Re-enrich any artist whose data is older than 60 days.
  2. Ask Perplexity / Anthropic web search to surface 1-3 *new* upcoming
     Persian jazz / indie / traditional artists worth adding, append them as
     status='upcoming' rows for human review.

This is intentionally conservative: it doesn't auto-publish anything except
data linked to a clear source URL, and it caps new additions at 3/week to
avoid runaway growth.

Usage:
    python -m scripts.refresh                    # full run
    python -m scripts.refresh --skip-discovery   # just re-enrich
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import httpx
import psycopg

from .enrich import (
    Artist,
    enrich_artist,
    PERPLEXITY_API_KEY,
    ANTHROPIC_API_KEY,
    PERPLEXITY_API,
    PERPLEXITY_MODEL,
    ANTHROPIC_API,
    ANTHROPIC_MODEL,
    _safe_json_extract,
)

DATABASE_URL = os.environ.get("DATABASE_URL")
MAX_NEW_PER_RUN = 3


DISCOVERY_PROMPT = """Suggest 1-3 emerging or recently active Persian/Iranian
musicians who fit one of these categories: contemporary Persian jazz, indie
Persian jazz/rock, traditional Persian music. Prefer artists active in the
last 12 months with verifiable online presence.

Return ONLY a JSON object with this shape (no markdown fence):

  {{
    "candidates": [
      {{
        "name_en": "...",
        "name_fa": "..." or null,
        "genre": "persian_jazz" | "indie_persian_jazz" | "traditional",
        "based_in": "city, country" or null,
        "why_notable": "one sentence",
        "source_url": "URL that supports your claim, e.g. a recent article or label page"
      }}
    ]
  }}

Skip anyone already very famous (Shajarian, Kalhor, Alizadeh, Namjoo, Sevdaliza,
Kiosk, Pallett, Rana Farhan, Sote, Quartet Diminished). Focus on lesser-known
or newer voices. Do not fabricate URLs."""


async def discover_new_artists(client: httpx.AsyncClient) -> list[dict]:
    """Ask Perplexity (preferred) or Anthropic to surface new candidates."""
    if PERPLEXITY_API_KEY:
        r = await client.post(
            PERPLEXITY_API,
            headers={
                "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": PERPLEXITY_MODEL,
                "messages": [{"role": "user", "content": DISCOVERY_PROMPT}],
                "max_tokens": 800,
            },
            timeout=60,
        )
        if r.status_code == 200:
            text = r.json()["choices"][0]["message"]["content"]
            parsed = _safe_json_extract(text)
            if parsed and "candidates" in parsed:
                return parsed["candidates"][:MAX_NEW_PER_RUN]

    if ANTHROPIC_API_KEY:
        r = await client.post(
            ANTHROPIC_API,
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": ANTHROPIC_MODEL,
                "max_tokens": 1024,
                "tools": [{"type": "web_search_20250305", "name": "web_search"}],
                "messages": [{"role": "user", "content": DISCOVERY_PROMPT}],
            },
            timeout=90,
        )
        if r.status_code == 200:
            data = r.json()
            text = "\n".join(
                b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
            )
            parsed = _safe_json_extract(text)
            if parsed and "candidates" in parsed:
                return parsed["candidates"][:MAX_NEW_PER_RUN]

    return []


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", name.lower()).strip("-")
    return s[:60]


async def insert_upcoming_candidate(
    conn: psycopg.AsyncConnection, cand: dict
) -> bool:
    """Insert a discovery candidate as status='upcoming'. Returns True on insert."""
    name_en = cand.get("name_en")
    if not name_en or len(name_en) > 200:
        return False
    genre = cand.get("genre")
    if genre not in ("persian_jazz", "indie_persian_jazz", "traditional"):
        return False
    slug = _slugify(name_en)

    async with conn.cursor() as cur:
        # Skip if a row with this slug or similar name already exists
        await cur.execute(
            "SELECT id FROM artist WHERE slug = %s OR lower(name_en) = lower(%s) LIMIT 1",
            (slug, name_en),
        )
        if await cur.fetchone():
            return False

        await cur.execute(
            """INSERT INTO artist (
                   slug, name_en, name_fa, genre, era, status, based_in, bio, bio_source
               ) VALUES (%s, %s, %s, %s, 'emerging', 'upcoming', %s, %s, 'discovery')
               RETURNING id""",
            (
                slug,
                name_en,
                cand.get("name_fa"),
                genre,
                cand.get("based_in"),
                cand.get("why_notable"),
            ),
        )
        new_id = (await cur.fetchone())[0]

        if cand.get("source_url", "").startswith(("http://", "https://")):
            await cur.execute(
                """INSERT INTO data_provenance
                       (fact_table, fact_id, fact_column, source, source_url, confidence, notes)
                   VALUES ('artist', %s, 'bio', 'discovery', %s, 0.5, 'discovered by weekly refresh; needs human review')""",
                (new_id, cand["source_url"]),
            )
        return True


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-discovery", action="store_true")
    args = parser.parse_args()

    if not DATABASE_URL:
        sys.exit("DATABASE_URL is required")

    cutoff = datetime.now(timezone.utc) - timedelta(days=60)

    async with await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True) as conn:
        async with httpx.AsyncClient() as client:

            # 1. Re-enrich stale rows
            async with conn.cursor() as cur:
                await cur.execute(
                    """SELECT id, slug, name_en, name_fa, genre, bio, bio_source
                       FROM artist
                       WHERE updated_at < %s OR bio_source = 'seed'
                       ORDER BY updated_at NULLS FIRST
                       LIMIT 5""",
                    (cutoff,),
                )
                stale = [Artist(**dict(zip(
                    ("id", "slug", "name_en", "name_fa", "genre", "bio", "bio_source"), row
                ))) for row in await cur.fetchall()]

            print(f"Re-enriching {len(stale)} stale artist(s)")
            for artist in stale:
                try:
                    await enrich_artist(conn, client, artist, dry_run=False)
                except Exception as e:  # noqa: BLE001
                    print(f"  ✗ {artist.slug}: {e}", file=sys.stderr)

            # 2. Discover new upcoming artists
            if not args.skip_discovery:
                candidates = await discover_new_artists(client)
                print(f"\nDiscovery returned {len(candidates)} candidate(s)")
                added = 0
                for c in candidates:
                    if await insert_upcoming_candidate(conn, c):
                        print(f"  + added: {c['name_en']} ({c['genre']})")
                        added += 1
                    else:
                        print(f"  · skipped: {c['name_en']} (exists or invalid)")
                print(f"Added {added} new upcoming artist(s) for human review.")


if __name__ == "__main__":
    asyncio.run(main())
