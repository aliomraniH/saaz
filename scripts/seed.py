"""
saaz/scripts/seed.py

Load seed_data/artists.json into the database. Idempotent (UPSERTs on slug).

Usage:
    python -m scripts.seed
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import psycopg

DATABASE_URL = os.environ.get("DATABASE_URL")
SEED_PATH = Path(__file__).parent.parent / "seed_data" / "artists.json"


async def main() -> None:
    if not DATABASE_URL:
        sys.exit("DATABASE_URL is required")
    if not SEED_PATH.exists():
        sys.exit(f"Seed file not found: {SEED_PATH}")

    seed = json.loads(SEED_PATH.read_text())
    artists = seed.get("artists", [])
    print(f"Loading {len(artists)} artist(s) from {SEED_PATH}")

    async with await psycopg.AsyncConnection.connect(DATABASE_URL) as conn:
        async with conn.cursor() as cur:
            for a in artists:
                await cur.execute(
                    """
                    INSERT INTO artist (
                        slug, name_en, name_fa, name_translit,
                        genre, sub_genres, era, status,
                        country, based_in, born, died,
                        bio, bio_source
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'seed')
                    ON CONFLICT (slug) DO UPDATE SET
                        name_en = EXCLUDED.name_en,
                        name_fa = EXCLUDED.name_fa,
                        genre = EXCLUDED.genre,
                        sub_genres = EXCLUDED.sub_genres,
                        era = EXCLUDED.era,
                        status = EXCLUDED.status,
                        based_in = EXCLUDED.based_in,
                        updated_at = now()
                    RETURNING id
                    """,
                    (
                        a["slug"],
                        a["name_en"],
                        a.get("name_fa"),
                        a.get("name_translit"),
                        a["genre"],
                        a.get("sub_genres", []),
                        a.get("era"),
                        a.get("status", "active"),
                        a.get("country", "IR"),
                        a.get("based_in"),
                        a.get("born"),
                        a.get("died"),
                        a.get("bio_seed"),
                    ),
                )
                artist_id = (await cur.fetchone())[0]

                # Provenance for seed bio
                if a.get("bio_seed"):
                    await cur.execute(
                        """INSERT INTO data_provenance
                               (fact_table, fact_id, fact_column, source, confidence, notes)
                           VALUES ('artist', %s, 'bio', 'seed', 0.6, 'hand-curated demo seed')""",
                        (artist_id,),
                    )

                # Links
                for link in a.get("links", []):
                    await cur.execute(
                        """INSERT INTO artist_link (artist_id, kind, url, verified)
                           VALUES (%s, %s, %s, TRUE)
                           ON CONFLICT (artist_id, url) DO NOTHING""",
                        (artist_id, link["kind"], link["url"]),
                    )

                print(f"  ✓ {a['slug']}")

        await conn.commit()
    print(f"Seeded {len(artists)} artists.")


if __name__ == "__main__":
    asyncio.run(main())
