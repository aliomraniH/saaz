"""
saaz/scripts/check_mneme.py

Sanity-check that mneme is observing queries against the saaz tables. Reads
mneme's `query_episode` table (read-only; we never write to it) and prints
how many calls have been routed to the `saaz_demo` namespace.

Use this after both projects are deployed and Claude Code has hit saaz
through mneme at least once.

Usage:
    python -m scripts.check_mneme
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

            # Is mneme even installed in this DB?
            await cur.execute(
                """SELECT count(*) AS n FROM information_schema.tables
                   WHERE table_schema = 'public' AND table_name = 'query_episode'"""
            )
            present = (await cur.fetchone())["n"] > 0
            if not present:
                print("✗ mneme tables not found in this Postgres.")
                print("  Either mneme hasn't been deployed yet, or it's pointed")
                print("  at a different database. Nothing to check.")
                return

            print("✓ mneme tables found.\n")

            print("=== Calls observed by mneme, by namespace ===")
            await cur.execute(
                """SELECT db_namespace, count(*) AS n,
                          max(ts) AS most_recent
                   FROM query_episode
                   GROUP BY db_namespace ORDER BY n DESC"""
            )
            rows = await cur.fetchall()
            if not rows:
                print("  (no calls observed yet — make a query via Claude Code → mneme)")
            else:
                for r in rows:
                    print(f"  {r['db_namespace']:20s} n={r['n']:>4}  last={r['most_recent']}")

            print("\n=== Calls routed to saaz_demo, recent first ===")
            await cur.execute(
                """SELECT tool_name, ts, duration_ms, error
                   FROM query_episode
                   WHERE db_namespace = 'saaz_demo'
                   ORDER BY ts DESC LIMIT 10"""
            )
            for r in await cur.fetchall():
                status = "✗" if r["error"] else "✓"
                print(f"  {status} {r['ts']}  {r['tool_name']:30s}  {r['duration_ms']}ms")

            print("\n=== Expertise notes mneme has learned about saaz ===")
            await cur.execute(
                """SELECT note, confidence, confirmed_by_user, created_at
                   FROM expertise_note
                   WHERE db_namespace = 'saaz_demo'
                   ORDER BY created_at DESC LIMIT 5"""
            )
            notes = await cur.fetchall()
            if not notes:
                print("  (none yet — expects Phase 2+ to populate)")
            else:
                for r in notes:
                    mark = "★" if r["confirmed_by_user"] else " "
                    print(f"  {mark} [{r['confidence']:.2f}] {r['note'][:80]}")


if __name__ == "__main__":
    asyncio.run(main())
