"""MongoDB connection and index management."""

from __future__ import annotations

from dataclasses import dataclass

from pymongo import ASCENDING, DESCENDING, TEXT, AsyncMongoClient
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.asynchronous.database import AsyncDatabase

from app.config import settings

_client: AsyncMongoClient | None = None


def get_client() -> AsyncMongoClient:
    """Process-wide client.

    The driver maintains its own connection pool and is safe to share, so one
    client is created for the lifetime of the process. Creating one per request
    would exhaust Atlas's connection limit almost immediately.
    """
    global _client
    if _client is None:
        _client = AsyncMongoClient(
            settings.mongodb_uri,
            # Atlas M0 allows 500 connections cluster-wide, so each instance
            # keeps a deliberately small pool.
            maxPoolSize=10,
            minPoolSize=0,
            serverSelectionTimeoutMS=10_000,
            retryWrites=True,
            tz_aware=True,
        )
    return _client


def get_database() -> AsyncDatabase:
    return get_client()[settings.mongodb_db]


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None


@dataclass(frozen=True)
class Collections:
    users: AsyncCollection
    workspaces: AsyncCollection
    workspace_members: AsyncCollection
    documents: AsyncCollection
    chunks: AsyncCollection
    conversations: AsyncCollection
    messages: AsyncCollection
    tool_calls: AsyncCollection
    tasks: AsyncCollection
    document_shares: AsyncCollection


def collections() -> Collections:
    """Typed collection handles.

    Nothing outside ``repositories.py`` should use these directly for tenant
    data — see the note there about why every tenant query goes through a
    workspace-scoped wrapper.
    """
    db = get_database()
    return Collections(
        users=db["users"],
        workspaces=db["workspaces"],
        workspace_members=db["workspaceMembers"],
        documents=db["documents"],
        chunks=db["chunks"],
        conversations=db["conversations"],
        messages=db["messages"],
        tool_calls=db["toolCalls"],
        tasks=db["tasks"],
        document_shares=db["documentShares"],
    )


async def ensure_indexes() -> list[str]:
    """Create every index the app relies on.

    Idempotent — ``create_index`` is a no-op when an equivalent index already
    exists — so this doubles as the migration step and is safe on every deploy.
    """
    c = collections()
    created: list[str] = []

    async def run(label: str, coro) -> None:
        await coro
        created.append(label)

    await run("users.email (unique)", c.users.create_index([("email", ASCENDING)], unique=True))

    await run("workspaces.createdBy", c.workspaces.create_index([("created_by", ASCENDING)]))

    await run(
        "workspaceMembers.workspaceId+userId (unique)",
        c.workspace_members.create_index(
            [("workspace_id", ASCENDING), ("user_id", ASCENDING)], unique=True
        ),
    )
    await run("workspaceMembers.userId", c.workspace_members.create_index([("user_id", ASCENDING)]))

    # IDEMPOTENT INGESTION: the same bytes cannot be ingested twice into one
    # workspace. Enforced by the database rather than a check-then-insert in
    # application code, which would race under concurrent uploads.
    await run(
        "documents.workspaceId+contentHash (unique)",
        c.documents.create_index(
            [("workspace_id", ASCENDING), ("content_hash", ASCENDING)], unique=True
        ),
    )
    await run(
        "documents.workspaceId+createdAt",
        c.documents.create_index([("workspace_id", ASCENDING), ("created_at", DESCENDING)]),
    )

    await run("chunks.workspaceId", c.chunks.create_index([("workspace_id", ASCENDING)]))

    # The keyword arm's access predicate is `workspace_id == ws OR shared_with
    # contains ws`. Mongo cannot use one index for both arms of an $or, so the
    # second arm needs its own — a multikey index over the array. Without it,
    # every keyword search in a workspace that has been shared anything degrades
    # to a collection scan.
    await run("chunks.sharedWith", c.chunks.create_index([("shared_with", ASCENDING)]))
    await run(
        "chunks.documentId+chunkIndex (unique)",
        c.chunks.create_index(
            [("document_id", ASCENDING), ("chunk_index", ASCENDING)], unique=True
        ),
    )

    # Keyword arm of hybrid retrieval. MongoDB permits only ONE text index per
    # collection, so this single compound index is the whole lexical surface.
    await run(
        "chunks.$text(content, section)",
        c.chunks.create_index(
            [("content", TEXT), ("section", TEXT)],
            weights={"content": 10, "section": 4},
            name="chunks_text",
            default_language="english",
        ),
    )

    await run(
        "conversations.workspaceId+updatedAt",
        c.conversations.create_index([("workspace_id", ASCENDING), ("updated_at", DESCENDING)]),
    )
    await run(
        "messages.conversationId+createdAt",
        c.messages.create_index([("conversation_id", ASCENDING), ("created_at", ASCENDING)]),
    )
    await run("messages.workspaceId", c.messages.create_index([("workspace_id", ASCENDING)]))
    await run(
        "toolCalls.workspaceId+createdAt",
        c.tool_calls.create_index([("workspace_id", ASCENDING), ("created_at", DESCENDING)]),
    )
    await run(
        "tasks.workspaceId+createdAt",
        c.tasks.create_index([("workspace_id", ASCENDING), ("created_at", DESCENDING)]),
    )

    # A document may be granted to a given workspace at most once. Enforced by
    # the database so a double-click cannot create two grants whose revocation
    # then has to be applied twice to actually remove access.
    await run(
        "documentShares.documentId+targetWorkspaceId (unique)",
        c.document_shares.create_index(
            [("document_id", ASCENDING), ("target_workspace_id", ASCENDING)], unique=True
        ),
    )
    await run(
        "documentShares.targetWorkspaceId",
        c.document_shares.create_index([("target_workspace_id", ASCENDING)]),
    )
    await run(
        "documentShares.ownerWorkspaceId",
        c.document_shares.create_index([("owner_workspace_id", ASCENDING)]),
    )

    return created
