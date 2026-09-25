"""Prepare both datastores.

Run with::

    uv run python -m scripts.migrate           # create indexes + collection
    uv run python -m scripts.migrate --reset   # DESTROY and recreate everything

MongoDB
    Creates every index the app relies on, including the unique
    ``(workspace_id, content_hash)`` index that makes ingestion idempotent and
    the text index powering the keyword arm of hybrid retrieval.

Qdrant
    Creates the ONE shared collection and, critically, the payload index on
    ``workspace_id``. Without that index Qdrant filters by scanning, which is
    what tempts people into dropping the tenant filter for performance. With
    it, the filter is cheap enough to apply unconditionally.

Idempotent without ``--reset``: both index creation and the collection check
are no-ops when the target already exists, so this is safe on every deploy.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.config import settings
from app.db.mongo import close_client, ensure_indexes, get_database
from app.vector.qdrant import close_client as close_qdrant
from app.vector.qdrant import ensure_collection, get_client

#: Collections written by the application. Listed explicitly so --reset can
#: never drop something it does not own.
_APP_COLLECTIONS = [
    "users",
    "workspaces",
    "workspaceMembers",
    "documents",
    "chunks",
    "conversations",
    "messages",
    "toolCalls",
    "tasks",
    "documentShares",
]


async def reset() -> None:
    """Drop every application collection and the vector collection.

    Needed when field naming changes: documents written under a different
    convention are not readable by the current code, and leaving them in place
    produces a database that looks populated while every query returns nothing.
    """
    db = get_database()
    existing = await db.list_collection_names()

    for name in _APP_COLLECTIONS:
        if name in existing:
            await db.drop_collection(name)
            print(f"  dropped mongo collection {name}")

    client = get_client()
    if await client.collection_exists(settings.qdrant_collection):
        await client.delete_collection(settings.qdrant_collection)
        print(f"  dropped qdrant collection {settings.qdrant_collection}")


async def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare MongoDB and Qdrant.")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Drop all application data first. Destructive.",
    )
    args = parser.parse_args()

    try:
        if args.reset:
            print("\nResetting datastores (destructive)\n")
            await reset()

        print("\nPreparing datastores\n")

        print("MongoDB")
        for index in await ensure_indexes():
            print(f"  ✓ {index}")

        print("\nQdrant")
        created, dimensions = await ensure_collection()
        if created:
            print(
                f'  ✓ created collection "{settings.qdrant_collection}" '
                f"({dimensions} dims, cosine) + payload indexes"
            )
        else:
            print(f"  · collection already exists ({dimensions} dims) — dimensions match")

        print("\n✓ Ready.\n")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"\n✗ Migration failed: {exc}\n", file=sys.stderr)
        return 1
    finally:
        await close_client()
        await close_qdrant()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
