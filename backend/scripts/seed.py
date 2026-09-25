"""Seed a reviewable demo: one user, two workspaces, sample corpora.

Run with::

    uv run python -m scripts.seed

Idempotent end to end — re-running reuses the existing user and workspaces, and
ingestion dedupes by content hash, so no duplicate chunks or vectors.

Failures are COUNTED, not just printed. A run where every document failed to
embed must not exit 0 with "seed complete": a seed script that reports success
while producing an empty corpus is worse than one that crashes.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from app.auth.password import hash_password
from app.auth.workspace import create_workspace, list_workspaces
from app.db.mongo import close_client, collections
from app.db.repositories import WorkspaceRepository
from app.ingest.pipeline import ingest_document
from app.vector.qdrant import close_client as close_qdrant

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_DOCS = REPO_ROOT / "sample-docs"

DEMO_EMAIL = os.environ.get("SEED_EMAIL", "reviewer@example.com").lower()
DEMO_PASSWORD = os.environ.get("SEED_PASSWORD", "groundwork-demo-2026")

WORKSPACES = [
    ("Northwind Logistics", "workspace-a-northwind"),
    ("Meridian Health", "workspace-b-meridian"),
]

failures = 0


def summarise(message: str) -> str:
    """Pull the actionable sentence out of a wall of nested JSON."""
    match = re.search(r'"message"\s*:\s*"([^"]+)"', message)
    return (match.group(1) if match else message)[:160]


async def ensure_user() -> str:
    users = collections().users

    existing = await users.find_one({"email": DEMO_EMAIL})
    if existing is not None:
        # Reset the password so the README's credentials always work, even if a
        # previous run used a different one.
        await users.update_one(
            {"_id": existing["_id"]},
            {"$set": {"password_hash": await hash_password(DEMO_PASSWORD)}},
        )
        print(f"  reusing user {DEMO_EMAIL}")
        return existing["_id"]

    user_id = str(uuid.uuid4())
    await users.insert_one(
        {
            "_id": user_id,
            "email": DEMO_EMAIL,
            "password_hash": await hash_password(DEMO_PASSWORD),
            "display_name": DEMO_EMAIL.split("@")[0],
            "created_at": datetime.now(UTC),
        }
    )
    print(f"  created user {DEMO_EMAIL}")
    return user_id


async def ensure_workspace(name: str, user_id: str) -> str:
    for workspace in await list_workspaces(user_id):
        if workspace.name == name:
            print(f'  reusing workspace "{name}"')
            return workspace.id

    created = await create_workspace(name, user_id)
    print(f'  created workspace "{name}"')
    return created.id


async def ingest_folder(folder: str, workspace_id: str, user_id: str) -> None:
    global failures

    directory = SAMPLE_DOCS / folder
    repo = WorkspaceRepository(workspace_id)

    for path in sorted(directory.glob("*.md")):
        try:
            result = await ingest_document(
                repo=repo,
                user_id=user_id,
                filename=path.name,
                mime_type="text/markdown",
                data=path.read_bytes(),
            )
            label = (
                "already ingested"
                if result.outcome == "duplicate"
                else f"{result.chunk_count} chunks"
            )
            print(f"    ✓ {path.name} — {label}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"    ✗ {path.name} — {summarise(str(exc))}", file=sys.stderr)


async def main() -> int:
    print("\nSeeding Groundwork demo data\n")

    try:
        print("User")
        user_id = await ensure_user()

        for name, folder in WORKSPACES:
            print(f"\nWorkspace: {name}")
            workspace_id = await ensure_workspace(name, user_id)
            await ingest_folder(folder, workspace_id, user_id)
    finally:
        await close_client()
        await close_qdrant()

    if failures:
        print(
            f"\n✗ Seed INCOMPLETE — {failures} document(s) failed to ingest.\n\n"
            "  The workspaces exist but their corpora are empty, so retrieval will\n"
            '  find nothing and every answer will be "I don\'t know".\n\n'
            "  If the errors above mention an invalid API key, set a real\n"
            "  GEMINI_API_KEY in .env.local and re-run.\n",
            file=sys.stderr,
        )
        return 1

    print("\n✓ Seed complete.\n")
    print(f"  Email:    {DEMO_EMAIL}")
    print(f"  Password: {DEMO_PASSWORD}\n")
    print("  Isolation check:")
    print('    In "Northwind Logistics" ask: What is Project FALCON-7? -> answers, with citations')
    print('    Switch to "Meridian Health" and ask the same -> must say it does not know\n')
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
