"""Workspace membership and creation."""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from app import errors
from app.db.mongo import collections, get_client
from app.db.repositories import WorkspaceRepository
from app.db.schema import PublicUser, Workspace

log = logging.getLogger(__name__)

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.I
)


def is_uuid(value: str) -> bool:
    return bool(_UUID_RE.match(value))


@dataclass(frozen=True)
class WorkspaceContext:
    user: PublicUser
    workspace: Workspace
    #: Pre-scoped to this workspace. The only sanctioned way to read tenant data.
    repo: WorkspaceRepository


async def require_workspace(user: PublicUser, workspace_id: str | None) -> WorkspaceContext:
    """The single gate for every workspace-scoped operation.

    Returns a repository already bound to the verified workspace, so callers
    receive an object that *cannot* query another tenant rather than an id they
    must remember to filter by.
    """
    # Reject malformed ids before they reach the database, where they would
    # surface as a driver error rather than a clean 404.
    if not workspace_id or not is_uuid(workspace_id):
        raise errors.workspace_not_found()

    c = collections()

    membership = await c.workspace_members.find_one(
        {"workspace_id": workspace_id, "user_id": user.id}
    )

    # Both branches return the same error: an attacker guessing ids must not be
    # able to tell "exists but forbidden" from "does not exist".
    if membership is None:
        raise errors.workspace_not_found()

    row = await c.workspaces.find_one({"_id": workspace_id})
    if row is None:
        raise errors.workspace_not_found()

    return WorkspaceContext(
        user=user,
        workspace=Workspace.model_validate(row),
        repo=WorkspaceRepository(workspace_id),
    )


async def list_workspaces(user_id: str) -> list[Workspace]:
    """Every workspace the user belongs to, oldest first."""
    c = collections()

    memberships = [m async for m in c.workspace_members.find({"user_id": user_id})]
    if not memberships:
        return []

    ids = [m["workspace_id"] for m in memberships]
    rows = [w async for w in c.workspaces.find({"_id": {"$in": ids}})]

    workspaces = [Workspace.model_validate(r) for r in rows]
    workspaces.sort(key=lambda w: w.created_at)
    return workspaces


async def create_workspace(name: str, user_id: str) -> Workspace:
    """Create a workspace and its owner membership together.

    Attempted in a transaction so a workspace can never exist without the
    membership that makes it reachable — an orphan would be invisible to its own
    creator and impossible to delete through the app.

    Transactions need a replica set. Atlas (including the free M0 tier) is one;
    a bare local ``mongod`` is not, so the fallback performs the same two writes
    and compensates by deleting the workspace if the membership fails.
    """
    trimmed = name.strip()
    if not trimmed:
        raise errors.invalid_workspace_name("Give the workspace a name.")

    now = datetime.now(UTC)
    workspace = Workspace(
        _id=str(uuid.uuid4()), name=trimmed[:80], created_by=user_id, created_at=now
    )
    membership = {
        "_id": str(uuid.uuid4()),
        "workspace_id": workspace.id,
        "user_id": user_id,
        "role": "owner",
        "created_at": now,
    }

    c = collections()

    async def _both_writes(session) -> None:
        await c.workspaces.insert_one(workspace.to_mongo(), session=session)
        await c.workspace_members.insert_one(membership, session=session)

    try:
        # with_transaction, rather than manual start/commit: it also retries
        # the callback on transient transaction errors, which a hand-rolled
        # version would have to reimplement.
        async with get_client().start_session() as session:
            await session.with_transaction(_both_writes)
        return workspace
    except Exception as exc:  # noqa: BLE001
        if not _is_transaction_unsupported(exc):
            raise

        log.info("transactions unavailable; falling back to compensated writes")
        await c.workspaces.insert_one(workspace.to_mongo())
        try:
            await c.workspace_members.insert_one(membership)
        except Exception:
            await c.workspaces.delete_one({"_id": workspace.id})
            raise
        return workspace


def _is_transaction_unsupported(error: BaseException) -> bool:
    return bool(re.search(r"transaction|replica set", str(error), re.I))
