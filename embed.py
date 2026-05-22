"""
saaz/scripts/embed.py

Generate 1536-dim embeddings for any artist or song row that has a bio/title
but a NULL embedding. Uses Voyage AI (preferred — multilingual, handles Persian
well) or OpenAI text-embedding-3-small as fallback.

Usage:
    python -m scripts.embed
    python -m scripts.embed --table song
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

import httpx
import psycopg

DATABASE_URL = os.environ.get("DATABASE_URL")
VOYAGE_API_KEY = os.environ.get("VOYAGE_API_KEY")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
VOYAGE_MODEL = os.environ.get("VOYAGE_MODEL", "voyage-3-large")  # 1024 native, can pad
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "text-embedding-3-small")  # 1536


async def embed_openai(client: httpx.AsyncClient, texts: list[str]) -> list[list[float]]:
    r = await client.post(
        "https://api.openai.com/v1/embeddings",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
        json={"model": OPENAI_MODEL, "input": texts, "dimensions": 1536},
        timeout=60,
    )
    r.raise_for_status()
    return [d["embedding"] for d in r.json()["data"]]


async def embed_voyage(client: httpx.AsyncClient, texts: list[str]) -> list[list[float]]:
    # Voyage returns 1024-dim for voyage-3-large; we pad to 1536 to match schema
    r = await client.post(
        "https://api.voyageai.com/v1/embeddings",
        headers={"Authorization": f"Bearer {VOYAGE_API_KEY}"},
        json={"model": VOYAGE_MODEL, "input": texts, "input_type": "document"},
        timeout=60,
    )
    r.raise_for_status()
    embeddings = [d["embedding"] for d in r.json()["data"]]
    # Pad to 1536
    return [e + [0.0] * (1536 - len(e)) if len(e) < 1536 else e[:1536] for e in embeddings]


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--table", choices=["artist", "song"], default="artist")
    parser.add_argument("--batch", type=int, default=20)
    args = parser.parse_args()

    if not DATABASE_URL:
        sys.exit("DATABASE_URL is required")
    if not (VOYAGE_API_KEY or OPENAI_API_KEY):
        sys.exit("Either VOYAGE_API_KEY or OPENAI_API_KEY is required")

    embedder = embed_voyage if VOYAGE_API_KEY else embed_openai
    print(f"Using {'Voyage' if VOYAGE_API_KEY else 'OpenAI'} embeddings for {args.table}")

    if args.table == "artist":
        select_sql = "SELECT id, name_en || ' — ' || COALESCE(bio, '') AS text FROM artist WHERE embedding IS NULL AND bio IS NOT NULL LIMIT %s"
        update_sql = "UPDATE artist SET embedding = %s WHERE id = %s"
    else:
        select_sql = "SELECT id, title_en || ' ' || COALESCE(album, '') AS text FROM song WHERE embedding IS NULL LIMIT %s"
        update_sql = "UPDATE song SET embedding = %s WHERE id = %s"

    async with await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True) as conn:
        async with httpx.AsyncClient() as client:
            total = 0
            while True:
                async with conn.cursor() as cur:
                    await cur.execute(select_sql, (args.batch,))
                    rows = await cur.fetchall()
                if not rows:
                    break

                ids = [r[0] for r in rows]
                texts = [r[1] for r in rows]
                vectors = await embedder(client, texts)

                async with conn.cursor() as cur:
                    for vid, vec in zip(ids, vectors):
                        # pgvector accepts the string form '[v1,v2,...]'
                        await cur.execute(update_sql, (str(vec), vid))
                total += len(rows)
                print(f"  embedded {total} rows...")

    print(f"Done. Embedded {total} rows in {args.table}.")


if __name__ == "__main__":
    asyncio.run(main())
