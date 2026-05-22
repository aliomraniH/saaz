"""
saaz/scripts/verify.py

Sanity-check the database after seed + enrich + embed. Prints row counts,
provenance coverage, and a sample row per genre.

Usage:
    python -m scripts.verify
"""

from __future__ import annotations

import asyncio
import os
import sys

import psycopg
from psycopg.rows import dict_row

DATABASE_URL = os.environ.get("DATABASE_URL")


async def main() -> None:
    if not DATABASE_URL:
        sys.exit("DATABASE_URL is required")

    async with await psycopg.AsyncConnection.connect(DATABASE_URL) as conn:
        async with conn.cursor(row_factory=dict_row) as cur:

            print("\n=== Row counts ===")
            for table in ("artist", "artist_link", "artist_image", "song",
                          "enrichment_run", "data_provenance"):
                await cur.execute(f"SELECT count(*) AS n FROM {table}")
                row = await cur.fetchone()
                print(f"  {table:20s} {row['n']:>5}")

            print("\n=== Artists by genre ===")
            await cur.execute("""
                SELECT genre, count(*) AS n,
                       count(*) FILTER (WHERE bio IS NOT NULL) AS with_bio,
                       count(*) FILTER (WHERE embedding IS NOT NULL) AS with_embedding
                FROM artist
                GROUP BY genre ORDER BY n DESC
            """)
            for r in await cur.fetchall():
                print(f"  {r['genre']:20s} n={r['n']:>3} bio={r['with_bio']:>3} emb={r['with_embedding']:>3}")

            print("\n=== Bio sources ===")
            await cur.execute("""
                SELECT bio_source, count(*) AS n
                FROM artist
                WHERE bio_source IS NOT NULL
                GROUP BY bio_source ORDER BY n DESC
            """)
            for r in await cur.fetchall():
                print(f"  {r['bio_source']:20s} {r['n']:>3}")

            print("\n=== Enrichment cost (last 7d) ===")
            await cur.execute("""
                SELECT source, count(*) AS calls,
                       COALESCE(sum(cost_usd), 0) AS total_usd
                FROM enrichment_run
                WHERE ran_at > now() - interval '7 days'
                GROUP BY source
            """)
            for r in await cur.fetchall():
                print(f"  {r['source']:20s} calls={r['calls']:>3}  ${r['total_usd']:.4f}")

            print("\n=== Sample artists ===")
            await cur.execute("""
                SELECT DISTINCT ON (genre) genre, slug, name_en,
                       length(bio) AS bio_len,
                       (SELECT count(*) FROM artist_link l WHERE l.artist_id = a.id) AS n_links
                FROM artist a
                WHERE bio IS NOT NULL
                ORDER BY genre, name_en
            """)
            for r in await cur.fetchall():
                print(f"  [{r['genre']}] {r['name_en']:30s} bio={r['bio_len']} links={r['n_links']}")


if __name__ == "__main__":
    asyncio.run(main())
