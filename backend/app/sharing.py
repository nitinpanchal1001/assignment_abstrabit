"""Explicit, opt-in cross-workspace document sharing.

A workspace can grant ONE of its documents to ONE other workspace, read-only
and revocably. Nothing is shared by default and nothing is shared implicitly;
absent a grant row, every query in this application behaves exactly as it did
before this module existed.

WHY THIS IS THE DELICATE PART
-----------------------------
Isolation everywhere else in the codebase is enforced by making the unsafe call
impossible to express. Sharing is the one feature that deliberately widens what
a workspace can read, so it is the one place where that property has to be
argued rather than assumed. Three decisions carry the weight:

1. **The grant is materialised, not joined.** The tenant predicate must be
   evaluated *inside* the vector query, and Qdrant cannot join against a
   collection of grants. So a grant is written onto the document's points and
   chunks as a ``shared_with`` array. ``documentShares`` remains the source of
   truth; the arrays are a derived index of it, and :func:`resync_document` can
   rebuild them from scratch at any time.

2. **Widening applies to READS only.** ``app/vector/qdrant.py`` keeps two
   filters — ownership for writes, deletes and counts; ownership-or-grant for
   search alone. A workspace that can read a shared document still cannot
   delete it, re-share it, count it as its own, or see it in its document list
   as anything but explicitly borrowed.

3. **Every partial failure is repaired, never left ambiguous.** The arrays are
   recomputed from the rows, so a grant is applied row-then-arrays and a revoke
   is row-then-arrays too — there is no ordering in which the array write comes
   first, because it reads the rows to know what to write.

   That makes the two failure modes asymmetric, and each is handled explicitly.
   A grant whose array write fails is *inert*: access never widened, and the row
   is rolled back so the UI does not show a share that is not in force. A revoke
   whose array write fails would leave access live with no row explaining it —
   the one direction that fails open — so the row is restored and the error
   surfaced. The caller is told the revoke did not happen, rather than being
   told it did while access quietly persists.

AUTHORITY
---------
Creating or revoking a grant requires membership of BOTH workspaces. Membership
of the owner is the real authority — it is that workspace's data. Requiring
membership of the target as well stops a user pushing their documents into
workspaces they have nothing to do with, and stops a grant being used to probe
whether a workspace id exists.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from app import errors
from app.db.mongo import collections
from app.db.schema import Document, DocumentShare, Workspace
from app.vector.qdrant import set_document_shares

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ShareView:
    """One grant, with the target workspace's name resolved for display."""

    share: DocumentShare
    target_workspace_name: str


@dataclass(frozen=True)
class SharedDocumentView:
    """A document another workspace has granted to this one."""

    document: Document
    owner_workspace_name: str
    shared_at: datetime


async def _target_workspace_ids(document_id: str) -> list[str]:
    """The authoritative grant list for one document, from the source of truth."""
    rows = [r async for r in collections().document_shares.find({"document_id": document_id})]
    # Sorted so the value written to both stores is deterministic — an
    # unordered array makes "did this converge?" needlessly hard to eyeball.
    return sorted({r["target_workspace_id"] for r in rows})


async def resync_document(*, owner_workspace_id: str, document_id: str) -> list[str]:
    """Rebuild both derived arrays from ``documentShares``.

    Convergent and idempotent, which is what makes the fail-closed ordering
    above recoverable rather than merely safe: any partial write is repaired by
    running this again.
    """
    targets = await _target_workspace_ids(document_id)

    # Mongo first, then Qdrant. Both are overwrites of a single field, so the
    # order only decides which store is briefly stale, not whether the result
    # is correct.
    await collections().chunks.update_many(
        {"workspace_id": owner_workspace_id, "document_id": document_id},
        {"$set": {"shared_with": targets}},
    )
    await set_document_shares(
        owner_workspace_id=owner_workspace_id, document_id=document_id, shared_with=targets
    )
    return targets


async def grant_share(
    *, document: Document, target_workspace_id: str, granted_by: str
) -> DocumentShare:
    """Share one document with one other workspace.

    Callers must already have authorised the user against BOTH workspaces; this
    function enforces the rules that are properties of the data rather than of
    the session.
    """
    if target_workspace_id == document.workspace_id:
        raise errors.validation("That document already belongs to this workspace.")

    # Sharing a document that is still processing, or that failed, would grant
    # access to a chunk set that is about to be rewritten — and the ingest
    # retry path deletes and re-creates points, which would drop the arrays.
    if document.status != "ready":
        raise errors.validation(
            f'"{document.filename}" is not ready yet, so it cannot be shared. '
            "Wait for ingestion to finish, or re-upload it if it failed."
        )

    c = collections()

    existing = await c.document_shares.find_one(
        {"document_id": document.id, "target_workspace_id": target_workspace_id}
    )
    if existing is not None:
        # Idempotent rather than a 409: the user's intent is "this workspace
        # should have access", and it already does. Re-syncing costs one write
        # and repairs a grant whose arrays never landed.
        await resync_document(owner_workspace_id=document.workspace_id, document_id=document.id)
        return DocumentShare.model_validate(existing)

    share = DocumentShare(
        _id=str(uuid.uuid4()),
        document_id=document.id,
        owner_workspace_id=document.workspace_id,
        target_workspace_id=target_workspace_id,
        granted_by=granted_by,
        created_at=datetime.now(UTC),
    )

    # The ROW first: a crash after this leaves a grant that is recorded but not
    # yet effective, which is the safe direction to fail.
    await c.document_shares.insert_one(share.to_mongo())

    try:
        await resync_document(owner_workspace_id=document.workspace_id, document_id=document.id)
    except Exception:
        # The grant is inert, so access has not widened. Roll the row back so
        # the UI does not show a share that is not in force.
        log.exception("share %s recorded but not applied; rolling back", share.id)
        await c.document_shares.delete_one({"_id": share.id})
        raise

    log.info(
        "document %s shared by workspace %s with %s",
        document.id,
        document.workspace_id,
        target_workspace_id,
    )
    return share


async def revoke_share(*, document: Document, target_workspace_id: str) -> bool:
    """Withdraw one grant. Returns False if there was nothing to withdraw."""
    c = collections()

    existing = await c.document_shares.find_one(
        {"document_id": document.id, "target_workspace_id": target_workspace_id}
    )
    if existing is None:
        return False

    # The row has to go first: resync derives the array FROM the rows, so the
    # target must already be absent for the recomputed array to exclude it.
    # Access actually ends when the arrays land, a moment later.
    await c.document_shares.delete_one({"_id": existing["_id"]})

    try:
        await resync_document(owner_workspace_id=document.workspace_id, document_id=document.id)
    except Exception:
        # This is the one path that could fail open — row gone, access still
        # live, and nothing left to explain why. Restoring the row returns the
        # system to a consistent "still shared" state that the owner can see
        # and retry, which beats reporting success while access persists.
        log.exception(
            "revoke of %s -> %s failed to apply; restoring row",
            document.id,
            target_workspace_id,
        )
        await c.document_shares.insert_one(existing)
        raise

    log.info("document %s revoked from workspace %s", document.id, target_workspace_id)
    return True


async def delete_shares_for_document(document_id: str) -> None:
    """Drop every grant on a document being deleted.

    The points and chunks go with the document, so no array needs rewriting —
    but leaving grant rows behind would resurrect access if an id were ever
    reused, and would make the sharing UI list a document that no longer exists.
    """
    await collections().document_shares.delete_many({"document_id": document_id})


async def list_shares_granted(*, workspace_id: str, document_id: str) -> list[ShareView]:
    """Grants this workspace has given on one of its documents."""
    c = collections()
    rows = [
        r
        async for r in c.document_shares.find(
            {"document_id": document_id, "owner_workspace_id": workspace_id}
        )
    ]
    if not rows:
        return []

    names = await _workspace_names([r["target_workspace_id"] for r in rows])
    views = [
        ShareView(
            share=DocumentShare.model_validate(r),
            target_workspace_name=names.get(r["target_workspace_id"], "Unknown workspace"),
        )
        for r in rows
    ]
    views.sort(key=lambda v: v.target_workspace_name.lower())
    return views


async def list_documents_shared_with(workspace_id: str) -> list[SharedDocumentView]:
    """Documents other workspaces have granted to this one.

    Kept separate from ``list_documents`` rather than merged into it. A borrowed
    document is not this workspace's to delete or re-share, and presenting the
    two in one undifferentiated list is how a UI ends up offering actions that
    the API will then refuse.
    """
    c = collections()
    rows = [r async for r in c.document_shares.find({"target_workspace_id": workspace_id})]
    if not rows:
        return []

    by_document = {r["document_id"]: r for r in rows}
    documents = [
        d async for d in c.documents.find({"_id": {"$in": list(by_document)}, "status": "ready"})
    ]
    if not documents:
        return []

    names = await _workspace_names([d["workspace_id"] for d in documents])

    views = [
        SharedDocumentView(
            document=Document.model_validate(d),
            owner_workspace_name=names.get(d["workspace_id"], "Unknown workspace"),
            shared_at=by_document[d["_id"]]["created_at"],
        )
        for d in documents
    ]
    views.sort(key=lambda v: v.shared_at, reverse=True)
    return views


async def owner_workspace_names(workspace_ids: set[str]) -> dict[str, str]:
    """Public helper for retrieval, which labels shared citations by owner."""
    return await _workspace_names(list(workspace_ids))


async def _workspace_names(ids: list[str]) -> dict[str, str]:
    unique = list({i for i in ids if i})
    if not unique:
        return {}
    rows = [w async for w in collections().workspaces.find({"_id": {"$in": unique}})]
    return {w["_id"]: Workspace.model_validate(w).name for w in rows}
