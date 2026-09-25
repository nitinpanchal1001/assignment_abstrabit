"""Document ingestion: extract → chunk → embed → store."""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from pymongo.errors import DuplicateKeyError

from app import errors
from app.db.repositories import WorkspaceRepository
from app.db.schema import Chunk, Document
from app.gemini.embed import embed_document_chunks
from app.ingest.chunk import chunk_document
from app.ingest.extract import extract_document
from app.sharing import delete_shares_for_document, resync_document
from app.vector.qdrant import delete_document_points, upsert_chunk_points

log = logging.getLogger(__name__)

Outcome = Literal["created", "duplicate", "retried"]


@dataclass(frozen=True)
class IngestResult:
    document: Document
    outcome: Outcome
    chunk_count: int


def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def ingest_document(
    *,
    repo: WorkspaceRepository,
    user_id: str,
    filename: str,
    mime_type: str,
    data: bytes,
) -> IngestResult:
    """Ingest one file into one workspace.

    IDEMPOTENCY
    -----------
    The content hash plus the unique ``(workspace_id, content_hash)`` index make
    re-uploading identical bytes a no-op that returns the existing document. The
    insert is ATTEMPTED and a duplicate-key error interpreted, rather than doing
    a read-then-write — the latter races under concurrent uploads of the same
    file, and the loser writes a second copy of every chunk.

    DUAL WRITE
    ----------
    Chunk text goes to Mongo and vectors to Qdrant, and no transaction spans
    both. The ordering is chosen so failure modes are recoverable: Mongo first,
    then Qdrant, and on any error BOTH are cleaned up before the document is
    marked failed. The worst case is a document marked ``failed`` with nothing
    retrievable, which a re-upload fixes — never a document that looks ready but
    answers from half its content, and never vectors left behind for text that
    no longer exists.
    """
    workspace_id = repo.workspace_id
    content_hash = hash_bytes(data)

    outcome: Outcome = "created"

    try:
        document = await repo.insert_document(
            filename=filename,
            mime_type=mime_type,
            byte_size=len(data),
            content_hash=content_hash,
            uploaded_by=user_id,
        )
    except DuplicateKeyError:
        existing = await repo.find_document_by_hash(content_hash)
        if existing is None:
            raise errors.internal(
                f"Duplicate key on ({workspace_id}, {content_hash}) but no matching document."
            ) from None

        # A healthy duplicate: return it untouched. No re-embedding.
        if existing.status == "ready" and existing.chunk_count > 0:
            return IngestResult(
                document=existing, outcome="duplicate", chunk_count=existing.chunk_count
            )

        # A failed or half-finished prior attempt: clear both stores and retry.
        await repo.delete_chunks_for_document(existing.id)
        await delete_document_points(workspace_id=workspace_id, document_id=existing.id)
        await repo.update_document(
            existing.id, status="processing", error_message=None, chunk_count=0
        )

        document = existing
        outcome = "retried"

    try:
        extracted = extract_document(data, filename, mime_type)
        pieces = chunk_document(extracted)

        if not pieces:
            raise errors.no_extractable_text(filename)

        embeddings = await embed_document_chunks(
            [(p.content, p.section) for p in pieces], title=filename
        )

        now = datetime.now(UTC)
        # Shared ids: the Mongo _id is also the Qdrant point id, so the two
        # stores are joinable without a mapping table.
        rows = [
            Chunk(
                _id=str(uuid.uuid4()),
                workspace_id=workspace_id,
                document_id=document.id,
                chunk_index=index,
                content=piece.content,
                section=piece.section,
                char_start=piece.char_start,
                char_end=piece.char_end,
                token_estimate=piece.token_estimate,
                filename=filename,
                created_at=now,
                # Empty on purpose. A re-ingest (the "retried" path above)
                # deletes and recreates every chunk, so grants must be re-applied
                # afterwards rather than assumed to survive — see the resync
                # below.
                shared_with=[],
            )
            for index, piece in enumerate(pieces)
        ]

        await repo.insert_chunks(rows)

        await upsert_chunk_points(
            workspace_id=workspace_id,
            points=[
                (
                    row.id,
                    embeddings[index],
                    {
                        "workspace_id": workspace_id,
                        "document_id": row.document_id,
                        "chunk_index": row.chunk_index,
                        "filename": row.filename,
                        "section": row.section,
                        "content": row.content,
                        # Present from the first write so the access filter's
                        # `shared_with` arm always has a field to match against
                        # rather than relying on missing-key semantics.
                        "shared_with": [],
                    },
                )
                for index, row in enumerate(rows)
            ],
        )

        await repo.update_document(
            document.id, status="ready", chunk_count=len(rows), error_message=None
        )

        # Re-apply any grants this document already had. The retry path wiped
        # its chunks and points, which took the denormalised `shared_with`
        # arrays with them — so without this, re-uploading a shared document
        # would silently revoke access that nobody withdrew. Rebuilt from
        # `documentShares`, which is unaffected by re-ingestion.
        if outcome == "retried":
            await resync_document(owner_workspace_id=workspace_id, document_id=document.id)

        document = document.model_copy(update={"status": "ready", "chunk_count": len(rows)})
        return IngestResult(document=document, outcome=outcome, chunk_count=len(rows))

    except Exception as exc:  # noqa: BLE001 - classified, then re-raised
        # Extraction problems already carry a precise, user-facing explanation;
        # anything else becomes a generic "could not process this file", with
        # the technical detail kept in the log.
        app_error = (
            exc
            if isinstance(exc, errors.AppError)
            else errors.ingest_failed(filename, errors.describe(exc))
        )

        log.error("ingest %s (%s): %s", filename, app_error.code, app_error.log_message)

        # Clean BOTH stores. Leaving chunks in one and not the other is the
        # failure mode that produces a half-searchable document.
        try:
            await repo.delete_chunks_for_document(document.id)
            await delete_document_points(workspace_id=workspace_id, document_id=document.id)
        except Exception:  # noqa: BLE001 - cleanup must not mask the original
            log.exception("cleanup after failed ingest also failed")

        try:
            # The Documents page renders this string, so it stores the
            # user-facing message. Persisting raw driver text would put
            # internal detail on a page the user reads.
            await repo.update_document(
                document.id,
                status="failed",
                error_message=app_error.user_message[:500],
                chunk_count=0,
            )
        except Exception:  # noqa: BLE001
            log.exception("could not mark document failed")

        raise app_error from exc


async def delete_document(*, repo: WorkspaceRepository, document_id: str) -> bool:
    """Delete from both stores. Vectors first — see note below."""
    existing = await repo.get_document(document_id)
    if existing is None:
        return False

    # Grants first: a grant row outliving its document would list a phantom in
    # the borrowing workspace's shared view, and would re-grant access if the id
    # were ever reused.
    await delete_shares_for_document(document_id)

    # Vectors next: if this succeeds and the Mongo delete then fails, the
    # document is merely un-searchable and can be deleted again. The reverse
    # order would leave vectors whose text no longer exists — content a tenant
    # believes they deleted, still retrievable.
    await delete_document_points(workspace_id=repo.workspace_id, document_id=document_id)
    await repo.delete_chunks_for_document(document_id)
    return await repo.delete_document(document_id)
