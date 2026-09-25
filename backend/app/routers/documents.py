"""Document upload, listing and deletion."""

from __future__ import annotations

from fastapi import APIRouter, File, UploadFile

from app import errors
from app.api_models import ApiModel, CreateShareIn, DocumentOut, SharedDocumentOut, ShareOut
from app.auth.workspace import is_uuid, require_workspace
from app.deps import WorkspaceDep
from app.ingest.extract import SUPPORTED_EXTENSIONS, extension_of
from app.ingest.pipeline import delete_document, ingest_document
from app.sharing import (
    ShareView,
    grant_share,
    list_documents_shared_with,
    list_shares_granted,
    revoke_share,
)

router = APIRouter(prefix="/documents", tags=["documents"])

#: Kept modest deliberately. Ingestion is synchronous, so a very large file
#: would hold a worker for the whole extract-embed cycle.
MAX_BYTES = 4 * 1024 * 1024


class UploadOut(ApiModel):
    document: DocumentOut
    outcome: str
    chunk_count: int
    message: str


@router.get("", response_model=list[DocumentOut])
async def list_documents(ctx: WorkspaceDep) -> list[DocumentOut]:
    return [DocumentOut.of(d) for d in await ctx.repo.list_documents()]


@router.post("", response_model=UploadOut, status_code=201)
async def upload_document(ctx: WorkspaceDep, file: UploadFile = File(...)) -> UploadOut:
    filename = file.filename or "untitled"

    extension = extension_of(filename)
    if extension not in SUPPORTED_EXTENSIONS:
        raise errors.unsupported_file_type(extension, sorted(SUPPORTED_EXTENSIONS))

    data = await file.read()

    if not data:
        raise errors.empty_document(filename)
    if len(data) > MAX_BYTES:
        raise errors.document_too_large(filename, len(data), MAX_BYTES)

    result = await ingest_document(
        repo=ctx.repo,
        user_id=ctx.user.id,
        filename=filename,
        mime_type=file.content_type or "",
        data=data,
    )

    message = (
        f'"{filename}" is already in this workspace — no duplicate chunks were created.'
        if result.outcome == "duplicate"
        else f'Ingested "{filename}" into {result.chunk_count} chunks.'
    )

    return UploadOut(
        document=DocumentOut.of(result.document),
        outcome=result.outcome,
        chunk_count=result.chunk_count,
        message=message,
    )


@router.delete("/{document_id}", status_code=204)
async def remove_document(document_id: str, ctx: WorkspaceDep) -> None:
    if not is_uuid(document_id):
        raise errors.document_not_found()

    # The repository is workspace-scoped, so a document belonging to another
    # tenant simply is not found — there is no cross-tenant delete to guard.
    deleted = await delete_document(repo=ctx.repo, document_id=document_id)
    if not deleted:
        raise errors.document_not_found()


# ---------------------------------------------------------------------------
# Cross-workspace sharing
#
# Opt-in, per document, per target workspace, read-only and revocable. See
# app/sharing.py for why the grant is materialised onto the chunks rather than
# joined at query time, and for the failure-ordering rules.
#
# AUTHORISATION, stated once because it is the whole security argument:
#
#   - The document is resolved through `ctx.repo`, which is bound to the
#     caller's ACTIVE workspace. A document belonging to anyone else is simply
#     not found, so you can only ever share something you own.
#   - The TARGET is authorised with a second `require_workspace()` call for the
#     same user. Membership of the target is not strictly needed to give access
#     away, but requiring it stops a user pushing their documents into
#     workspaces they have nothing to do with, and makes a non-member target
#     indistinguishable from a non-existent one.
#   - `target_workspace_id` arrives from the REQUEST BODY, never from model
#     output. No tool can reach these routes.
# ---------------------------------------------------------------------------


@router.get("/shared-with-me", response_model=list[SharedDocumentOut])
async def shared_with_me(ctx: WorkspaceDep) -> list[SharedDocumentOut]:
    """Documents other workspaces have granted to this one.

    A separate route from GET /documents on purpose — these are readable but
    not owned, and merging the two lists would imply they can be deleted here.
    """
    views = await list_documents_shared_with(ctx.workspace.id)
    return [SharedDocumentOut.of(v) for v in views]


@router.get("/{document_id}/shares", response_model=list[ShareOut])
async def list_shares(document_id: str, ctx: WorkspaceDep) -> list[ShareOut]:
    if not is_uuid(document_id):
        raise errors.document_not_found()

    # Ownership check: a document not in the active workspace is not found.
    if await ctx.repo.get_document(document_id) is None:
        raise errors.document_not_found()

    views = await list_shares_granted(workspace_id=ctx.workspace.id, document_id=document_id)
    return [ShareOut.of(v) for v in views]


@router.post("/{document_id}/shares", response_model=ShareOut, status_code=201)
async def create_share(document_id: str, payload: CreateShareIn, ctx: WorkspaceDep) -> ShareOut:
    if not is_uuid(document_id):
        raise errors.document_not_found()

    document = await ctx.repo.get_document(document_id)
    if document is None:
        raise errors.document_not_found()

    # Second gate: the caller must belong to the target too. Raises the same
    # 404 as a non-existent workspace, so this cannot be used to enumerate ids.
    target = await require_workspace(ctx.user, payload.target_workspace_id)

    share = await grant_share(
        document=document, target_workspace_id=target.workspace.id, granted_by=ctx.user.id
    )
    return ShareOut.of(ShareView(share=share, target_workspace_name=target.workspace.name))


@router.delete("/{document_id}/shares/{target_workspace_id}", status_code=204)
async def remove_share(document_id: str, target_workspace_id: str, ctx: WorkspaceDep) -> None:
    if not is_uuid(document_id) or not is_uuid(target_workspace_id):
        raise errors.document_not_found()

    document = await ctx.repo.get_document(document_id)
    if document is None:
        raise errors.document_not_found()

    # Revoking deliberately does NOT require membership of the target.
    # Withdrawing access to your own document must stay possible after you have
    # left the workspace you lent it to — otherwise a grant could outlive any
    # way to take it back.
    revoked = await revoke_share(document=document, target_workspace_id=target_workspace_id)
    if not revoked:
        raise errors.share_not_found()
