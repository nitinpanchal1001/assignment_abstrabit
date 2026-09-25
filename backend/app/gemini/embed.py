"""Embeddings for ingestion and for queries.

Two properties of ``gemini-embedding-2`` drive this module's shape:

1. ASYMMETRIC PREFIXES. The model dropped the old ``task_type`` parameter in
   favour of instructions inlined into the text. Stored passages and search
   queries use *different* prefixes, and mixing them up degrades retrieval
   quietly — nothing errors, results just get worse. They are centralised here
   so the two call sites cannot drift apart.

2. MULTIPLE BARE STRINGS PRODUCE ONE AGGREGATED EMBEDDING. Passing
   ``contents=[a, b, c]`` returns a single vector describing all three, not
   three vectors. Every input is therefore sent as its own Content, and the
   returned count is asserted — writing an aggregated vector into per-chunk
   rows would corrupt the store in a way no smoke test would catch.
"""

from __future__ import annotations

import logging

from google.genai import types

from app import errors
from app.gemini.client import (
    EMBEDDING_DIMENSIONS,
    EMBEDDING_MODEL,
    genai_client,
    with_retry,
)

log = logging.getLogger(__name__)

#: Max inputs per request. Conservative, to stay clear of free-tier limits.
BATCH_SIZE = 16


def document_embedding_input(text: str, title: str | None = None) -> str:
    """Prefix for text being stored for later retrieval."""
    return f"title: {(title or '').strip() or 'none'} | text: {text}"


def query_embedding_input(query: str) -> str:
    """Prefix for a user's search query. Must differ from the document prefix."""
    return f"task: search result | query: {query}"


def _config() -> types.EmbedContentConfig:
    # gemini-embedding-2 auto-normalises truncated dimensions, so no manual L2.
    return types.EmbedContentConfig(output_dimensionality=EMBEDDING_DIMENSIONS)


def _assert_dimensions(values: list[float] | None, context: str) -> list[float]:
    if not values:
        raise errors.llm_failed(f"{context}: embedding response contained no values.")
    if len(values) != EMBEDDING_DIMENSIONS:
        # A misconfiguration rather than a transient fault: the collection was
        # built for a different width, so every write would be rejected.
        raise errors.internal(
            f"{context}: expected {EMBEDDING_DIMENSIONS}-dimension embedding, got "
            f"{len(values)}. EMBEDDING_DIMENSIONS must match the size the Qdrant "
            "collection was created with."
        )
    return list(values)


async def _embed_one(text: str, context: str) -> list[float]:
    async def call():
        return await genai_client().aio.models.embed_content(
            model=EMBEDDING_MODEL, contents=text, config=_config()
        )

    response = await with_retry(call, label=f"embed({context})")
    embeddings = response.embeddings or []
    values = embeddings[0].values if embeddings else None
    return _assert_dimensions(values, context)


async def _embed_batch(texts: list[str], context: str) -> list[list[float]]:
    """Embed a batch, asserting a 1:1 result.

    If the API ever returns a different number of embeddings than we sent — the
    aggregation behaviour described above — we do NOT guess at the alignment.
    We fall back to one call per input, which is slower but can only be correct.
    """
    if not texts:
        return []
    if len(texts) == 1:
        return [await _embed_one(texts[0], context)]

    async def call():
        return await genai_client().aio.models.embed_content(
            model=EMBEDDING_MODEL, contents=texts, config=_config()
        )

    response = await with_retry(call, label=f"embed_batch({context})")
    embeddings = response.embeddings or []

    if len(embeddings) != len(texts):
        log.warning(
            "embed %s: sent %d inputs but received %d embeddings; "
            "falling back to sequential embedding to guarantee 1:1 alignment",
            context,
            len(texts),
            len(embeddings),
        )
        return [await _embed_one(text, context) for text in texts]

    return [_assert_dimensions(e.values, f"{context}[{i}]") for i, e in enumerate(embeddings)]


async def embed_document_chunks(
    chunks: list[tuple[str, str | None]], title: str | None = None
) -> list[list[float]]:
    """Embed ``(content, section)`` pairs for storage.

    Batches run sequentially rather than concurrently: the free tier is
    rate-limited per minute, and a burst of parallel batches reliably trips a
    429 that costs more in backoff than the concurrency saved.
    """
    inputs = [
        document_embedding_input(content, f"{title or ''} {section}".strip() if section else title)
        for content, section in chunks
    ]

    results: list[list[float]] = []
    for start in range(0, len(inputs), BATCH_SIZE):
        window = inputs[start : start + BATCH_SIZE]
        results.extend(await _embed_batch(window, f"chunks {start}-{start + len(window) - 1}"))

    if len(results) != len(chunks):
        raise errors.internal(
            f"Embedding count mismatch: {len(chunks)} chunks produced {len(results)} vectors."
        )

    return results


async def embed_query(query: str) -> list[float]:
    trimmed = query.strip()
    if not trimmed:
        raise errors.validation("Type a question before sending.")
    return await _embed_one(query_embedding_input(trimmed), "query")
