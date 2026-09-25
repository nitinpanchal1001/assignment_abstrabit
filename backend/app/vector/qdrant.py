"""The single shared vector store.

ONE Qdrant collection holds every workspace's chunks. Tenancy is a
``workspace_id`` payload field plus a mandatory filter on every search — never
a collection per tenant.

ISOLATION CONTRACT
------------------
Postgres would give us row-level security as a second, database-enforced layer.
Qdrant has no equivalent, so isolation here rests entirely on application code,
which means the code must make the unsafe thing *impossible* rather than merely
discouraged. Four rules, all enforced by this module:

1. :func:`search_chunks` is the ONLY exported read path, it takes
   ``workspace_id`` as a required argument, and it builds the filter itself.
   There is no parameter through which a caller can pass a custom filter,
   weaken the existing one, or search unfiltered — so "forgot the filter" is
   not a mistake this API permits.
2. Every returned point is checked for visibility to the requesting workspace,
   and a violation RAISES rather than being filtered out. A silent filter would
   conceal exactly the bug most worth knowing about.
3. Writes and deletes are scoped STRICTLY to the owning workspace: deletes
   filter on ``workspace_id`` in addition to the document id, so a bug in the
   caller cannot remove another tenant's vectors.
4. There are TWO filters here and they are deliberately not interchangeable.
   :func:`_owned_filter` is ownership — the only thing a write, delete or count
   may use. :func:`_readable_filter` is ownership OR an explicit grant, and is
   used by exactly one function, :func:`search_chunks`. Widening a read must
   never widen a write.

SHARING
-------
A document can be explicitly granted to another workspace. Because the tenant
predicate has to be evaluated inside the vector query — and Qdrant cannot join
against a grants collection — the grant is materialised onto each point as a
``shared_with`` array, kept in step by ``app/sharing.py``.

The property that matters: ``shared_with`` is empty for every point until
somebody deliberately shares. With no grants, ``_readable_filter`` matches
exactly what ``_owned_filter`` matches, so default isolation is not "mostly
preserved" — it is the same query.

Nothing outside this file should import ``AsyncQdrantClient``.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from qdrant_client import AsyncQdrantClient
from qdrant_client import models as qm

from app import errors
from app.config import settings

log = logging.getLogger(__name__)

T = TypeVar("T")

_client: AsyncQdrantClient | None = None

COLLECTION = settings.qdrant_collection


def get_client() -> AsyncQdrantClient:
    global _client
    if _client is None:
        _client = AsyncQdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key,
            timeout=30,
            check_compatibility=False,
        )
    return _client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None


@dataclass(frozen=True)
class VectorHit:
    chunk_id: str
    score: float
    payload: dict[str, Any]


async def _wrap[T](operation: Callable[[], Awaitable[T]]) -> T:
    """Run a Qdrant call, converting driver errors into AppErrors.

    The client raises exceptions carrying an HTTP status, which would otherwise
    reach the UI as a bare "Not Found" with no indication of what was not
    found. ``to_app_error`` maps those to specific, actionable failures — a
    missing collection becomes "run the migration" rather than a 500.
    """
    try:
        return await operation()
    except Exception as exc:  # noqa: BLE001 - deliberately broad, then classified
        raise errors.to_app_error(exc) from exc


def _require_workspace_id(workspace_id: str) -> None:
    if not workspace_id or not isinstance(workspace_id, str):
        raise errors.internal("A workspace_id is required for every vector store operation.")


def _owned_filter(workspace_id: str) -> qm.Filter:
    """Points OWNED by this workspace. Private — callers cannot supply a filter.

    This is the filter for every write, delete and count. It must never be
    relaxed to include shared points: being allowed to read someone else's
    document does not make it yours to modify or to count as your own.
    """
    return qm.Filter(
        must=[qm.FieldCondition(key="workspace_id", match=qm.MatchValue(value=workspace_id))]
    )


def _readable_filter(workspace_id: str) -> qm.Filter:
    """Points this workspace may READ: its own, plus anything granted to it.

    ``should`` with ``min_should=1`` rather than two separate queries, so the
    union is formed inside the HNSW traversal and the top-k is the best k across
    everything visible — not the best k of one set stapled to the best k of
    another.

    ``MatchValue`` against ``shared_with`` matches when the array CONTAINS the
    value; that is Qdrant's semantics for a keyword array, not a coincidence of
    formatting.
    """
    conditions = [
        qm.FieldCondition(key="workspace_id", match=qm.MatchValue(value=workspace_id)),
        qm.FieldCondition(key="shared_with", match=qm.MatchValue(value=workspace_id)),
    ]
    return qm.Filter(
        should=conditions,
        # Explicit, though Qdrant already requires one `should` to match when
        # there is no `must`. The default is the whole security property here,
        # and a security property should not be implicit.
        min_should=qm.MinShould(conditions=conditions, min_count=1),
    )


def _is_visible(payload: dict[str, Any], workspace_id: str) -> bool:
    """Mirror of :func:`_readable_filter`, evaluated on a returned point.

    Kept deliberately close to the filter it checks: if the two ever disagree,
    the assertion in :func:`search_chunks` fires and the request is aborted
    rather than quietly serving something the filter should not have returned.
    """
    if payload.get("workspace_id") == workspace_id:
        return True
    shared = payload.get("shared_with")
    return isinstance(shared, list) and workspace_id in shared


async def _ensure_payload_indexes(client: AsyncQdrantClient) -> None:
    """Index every field the access filter touches.

    Without a payload index on the tenant keys, Qdrant filters by scanning —
    which is what tempts people into dropping the tenant filter for
    performance. These indexes are what make the filter cheap enough to apply
    unconditionally, which is what lets us apply it unconditionally.

    Idempotent: creating an index that already exists is accepted by Qdrant, so
    this runs on every migration rather than only at creation time.
    """
    for field in ("workspace_id", "document_id", "shared_with"):
        await _wrap(
            lambda f=field: client.create_payload_index(
                collection_name=COLLECTION,
                field_name=f,
                field_schema=qm.PayloadSchemaType.KEYWORD,
                wait=True,
            )
        )


async def ensure_collection() -> tuple[bool, int]:
    """Create the collection and payload indexes if absent; verify dimensions.

    The dimension check matters: Qdrant rejects mismatched vectors at upsert
    time with an opaque error, and silently re-creating the collection would
    destroy every tenant's embeddings.

    Returns ``(created, dimensions)``.
    """
    client = get_client()

    exists = await _wrap(lambda: client.collection_exists(COLLECTION))

    if not exists:
        await _wrap(
            lambda: client.create_collection(
                collection_name=COLLECTION,
                vectors_config=qm.VectorParams(
                    size=settings.embedding_dimensions, distance=qm.Distance.COSINE
                ),
            )
        )
        await _ensure_payload_indexes(client)
        return True, settings.embedding_dimensions

    # Indexes are ensured for an EXISTING collection too, not only a fresh one.
    # `shared_with` was added after the first deployments, and an index created
    # only in the not-exists branch would never appear on any collection that
    # already existed — leaving the sharing arm of every filter to be evaluated
    # by scan, on exactly the clusters with the most data.
    await _ensure_payload_indexes(client)

    info = await _wrap(lambda: client.get_collection(COLLECTION))
    vectors = info.config.params.vectors
    size = vectors.size if isinstance(vectors, qm.VectorParams) else None

    if size is not None and size != settings.embedding_dimensions:
        raise errors.internal(
            f'Qdrant collection "{COLLECTION}" stores {size}-dimension vectors but '
            f"EMBEDDING_DIMENSIONS is {settings.embedding_dimensions}. Change the setting "
            "back, or re-embed every document into a new collection. Refusing to continue."
        )

    return False, size or settings.embedding_dimensions


async def search_chunks(
    *, workspace_id: str, embedding: list[float], limit: int
) -> list[VectorHit]:
    """Dense vector search, unconditionally scoped to what one workspace may read.

    The filter is applied by Qdrant during the HNSW traversal, not to the
    results afterwards, so the returned top-k is genuinely the best k *within*
    the visible set rather than whatever survives a post-filter.

    Visible means owned, or explicitly granted. With no grants in existence the
    second arm matches nothing and this is precisely an own-workspace search.
    """
    _require_workspace_id(workspace_id)
    client = get_client()

    response = await _wrap(
        lambda: client.query_points(
            collection_name=COLLECTION,
            query=embedding,
            query_filter=_readable_filter(workspace_id),
            limit=limit,
            with_payload=True,
        )
    )

    hits: list[VectorHit] = []
    for point in response.points:
        payload = point.payload or {}

        # Rule 2: anything not visible here means the filter failed. Fail loudly
        # — this is the assertion the whole feature rests on, and a share is
        # exactly the change most likely to break it.
        if not _is_visible(payload, workspace_id):
            raise errors.isolation_violation(
                f"Vector store returned a chunk owned by {payload.get('workspace_id')!r} "
                f"(shared_with={payload.get('shared_with')!r}) while searching "
                f"{workspace_id}; request aborted."
            )

        hits.append(VectorHit(chunk_id=str(point.id), score=point.score or 0.0, payload=payload))

    return hits


async def set_document_shares(
    *, owner_workspace_id: str, document_id: str, shared_with: list[str]
) -> None:
    """Overwrite the grant list on every point of one document.

    Takes the WHOLE list rather than an add/remove delta. Qdrant has no atomic
    array append, so a delta would mean read-modify-write per point and would
    race with a concurrent grant on the same document. Passing the authoritative
    list computed from ``documentShares`` makes this convergent instead: running
    it twice, or after a partial failure, produces the same state.

    ``set_payload`` replaces only the named key, so content, filename and
    section are untouched. The selector is strictly ownership-scoped — a share
    can only ever be written onto points the owning workspace holds.
    """
    _require_workspace_id(owner_workspace_id)
    client = get_client()

    await _wrap(
        lambda: client.set_payload(
            collection_name=COLLECTION,
            wait=True,
            payload={"shared_with": shared_with},
            points=qm.FilterSelector(
                filter=qm.Filter(
                    must=[
                        qm.FieldCondition(
                            key="workspace_id", match=qm.MatchValue(value=owner_workspace_id)
                        ),
                        qm.FieldCondition(
                            key="document_id", match=qm.MatchValue(value=document_id)
                        ),
                    ]
                )
            ),
        )
    )


async def upsert_chunk_points(
    *, workspace_id: str, points: list[tuple[str, list[float], dict[str, Any]]]
) -> None:
    """Upsert points for one workspace. Point ids are the Mongo chunk ids."""
    _require_workspace_id(workspace_id)
    if not points:
        return

    for _, _, payload in points:
        if payload.get("workspace_id") != workspace_id:
            raise errors.isolation_violation(
                f"Refusing to upsert a point whose payload workspace_id "
                f"({payload.get('workspace_id')!r}) does not match the workspace being "
                f"written ({workspace_id})."
            )

    client = get_client()

    # Batched: Qdrant Cloud caps request bodies, and 1536 floats per point adds
    # up quickly.
    batch_size = 64
    for start in range(0, len(points), batch_size):
        batch = points[start : start + batch_size]
        await _wrap(
            lambda b=batch: client.upsert(
                collection_name=COLLECTION,
                wait=True,
                points=[
                    qm.PointStruct(id=pid, vector=vector, payload=payload)
                    for pid, vector, payload in b
                ],
            )
        )


async def delete_document_points(*, workspace_id: str, document_id: str) -> None:
    """Remove every point belonging to one document.

    Filters on ``workspace_id`` as well as ``document_id``. The document id
    alone would suffice if ids were never wrong — which is exactly the
    assumption a tenancy boundary should not make.
    """
    _require_workspace_id(workspace_id)
    client = get_client()

    await _wrap(
        lambda: client.delete(
            collection_name=COLLECTION,
            wait=True,
            points_selector=qm.FilterSelector(
                filter=qm.Filter(
                    must=[
                        qm.FieldCondition(
                            key="workspace_id", match=qm.MatchValue(value=workspace_id)
                        ),
                        qm.FieldCondition(
                            key="document_id", match=qm.MatchValue(value=document_id)
                        ),
                    ]
                )
            ),
        )
    )


async def count_workspace_points(workspace_id: str) -> int:
    """Count points in a workspace. Raises on failure.

    Used by the isolation test, where a missing collection or an unreachable
    store MUST fail the run rather than silently report zero — "0 foreign
    chunks" is a passing result, and a broken connection could manufacture one.
    """
    _require_workspace_id(workspace_id)
    client = get_client()
    result = await _wrap(
        lambda: client.count(
            collection_name=COLLECTION, count_filter=_owned_filter(workspace_id), exact=True
        )
    )
    return result.count


async def count_all_points() -> int:
    """Total across all tenants — proves the store really is shared."""
    client = get_client()
    result = await _wrap(lambda: client.count(collection_name=COLLECTION, exact=True))
    return result.count


@dataclass(frozen=True)
class VectorStoreStats:
    workspace: int | None
    total: int | None
    available: bool
    reason: str | None


async def vector_store_stats(workspace_id: str) -> VectorStoreStats:
    """Display-only counts that degrade instead of raising.

    The dashboard's vector tiles are informational, but an exception during
    page assembly takes down the ENTIRE page — which is how a deployment that
    simply had not run the migration yet turned into a blank 500 with no hint
    about the cause. Statistics failing should cost you the statistics, not the
    page.

    Deliberately separate from the raising variants above: correctness-critical
    callers must never receive a silent ``None``.
    """
    try:
        workspace = await count_workspace_points(workspace_id)
        total = await count_all_points()
        return VectorStoreStats(workspace=workspace, total=total, available=True, reason=None)
    except Exception as exc:  # noqa: BLE001
        app_error = errors.to_app_error(exc)
        log.warning("qdrant stats unavailable (%s): %s", app_error.code, app_error.log_message)
        return VectorStoreStats(
            workspace=None, total=None, available=False, reason=app_error.user_message
        )
