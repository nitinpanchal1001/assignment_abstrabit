"""Workspace-scoped hybrid retrieval.

Two arms, fused with Reciprocal Rank Fusion:

DENSE
    Qdrant vector search, filtered to the workspace during the HNSW traversal
    (see ``app/vector/qdrant.py``), so the top-k is the best k *within* the
    tenant rather than whatever survives a post-filter.
KEYWORD
    MongoDB's text index, with the workspace predicate in the same query
    document so both conditions are evaluated server-side together.

RRF fuses by *rank* rather than score, which is what makes combining a cosine
similarity with a Mongo textScore meaningful at all — the two are on
incomparable scales. It earns its place because exact tokens a dense model
smooths over (identifiers, product codes, proper nouns) are precisely what
users cite when asking about their own documents.

Both arms are independently tenant-scoped. There is no code path here that
filters by workspace *after* ranking.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from app.db.repositories import WorkspaceRepository
from app.db.schema import Citation, MatchedChunk, RetrievalChunk, RetrievalDebug
from app.gemini.embed import embed_query
from app.sharing import owner_workspace_names
from app.vector.qdrant import search_chunks

DEFAULT_MATCH_COUNT = 8
#: Cosine floor. Deliberately low: the honest-refusal decision is made by the
#: model reading the passages, not by a threshold. A threshold high enough to
#: reject off-topic text also rejects correctly retrieved passages phrased
#: differently from the question.
DEFAULT_MIN_SIMILARITY = 0.15
#: Candidates pulled from each arm before fusion.
CANDIDATE_POOL = 40
#: RRF damping constant, from the original paper.
RRF_K = 60


@dataclass(frozen=True)
class RetrievalOutcome:
    chunks: list[MatchedChunk]
    citations: list[Citation]
    debug: RetrievalDebug


@dataclass
class _Candidate:
    chunk_id: str
    similarity: float | None = None
    vector_rank: int | None = None
    keyword_rank: int | None = None
    payload: dict | None = None


async def retrieve_chunks(
    *,
    repo: WorkspaceRepository,
    query: str,
    match_count: int = DEFAULT_MATCH_COUNT,
    min_similarity: float = DEFAULT_MIN_SIMILARITY,
) -> RetrievalOutcome:
    started = time.monotonic()
    workspace_id = repo.workspace_id

    embedding = await embed_query(query)

    # Both arms concurrently — they hit different databases, so the round trips
    # overlap instead of adding up.
    dense, keyword = await asyncio.gather(
        search_chunks(workspace_id=workspace_id, embedding=embedding, limit=CANDIDATE_POOL),
        repo.text_search(query, CANDIDATE_POOL),
    )

    candidates: dict[str, _Candidate] = {}

    for index, hit in enumerate(dense, start=1):
        candidates[hit.chunk_id] = _Candidate(
            chunk_id=hit.chunk_id,
            similarity=hit.score,
            vector_rank=index,
            payload=hit.payload,
        )

    for index, (chunk, _score) in enumerate(keyword, start=1):
        existing = candidates.get(chunk.id)
        if existing is not None:
            existing.keyword_rank = index
            continue
        candidates[chunk.id] = _Candidate(
            chunk_id=chunk.id,
            keyword_rank=index,
            payload={
                # Carried so a keyword-only hit is attributed to its owner too.
                # Without it, a shared document found by the text arm rather
                # than the vector arm would silently look locally owned.
                "workspace_id": chunk.workspace_id,
                "document_id": chunk.document_id,
                "filename": chunk.filename,
                "section": chunk.section,
                "chunk_index": chunk.chunk_index,
                "content": chunk.content,
            },
        )

    def rrf(candidate: _Candidate) -> float:
        score = 0.0
        if candidate.vector_rank:
            score += 1.0 / (RRF_K + candidate.vector_rank)
        if candidate.keyword_rank:
            score += 1.0 / (RRF_K + candidate.keyword_rank)
        return score

    fused = sorted(candidates.values(), key=rrf, reverse=True)

    results: list[MatchedChunk] = []
    for candidate in fused:
        payload = candidate.payload or {}
        similarity = candidate.similarity or 0.0

        # Keyword-only hits have no cosine score; keep them rather than judging
        # them against a threshold they were never scored on.
        if candidate.vector_rank is not None and similarity < min_similarity:
            continue

        results.append(
            MatchedChunk(
                chunk_id=candidate.chunk_id,
                document_id=str(payload.get("document_id", "")),
                # The OWNER, read from the stored payload — not the workspace
                # doing the searching. Stamping the searcher here made a
                # borrowed chunk indistinguishable from an owned one, which is
                # precisely the distinction the retrieval-debug view exists to
                # show. Falls back to the searcher only if the payload somehow
                # lacks the field; `search_chunks` would already have raised.
                workspace_id=str(payload.get("workspace_id") or workspace_id),
                filename=str(payload.get("filename", "")),
                section=payload.get("section"),
                chunk_index=int(payload.get("chunk_index", 0)),
                content=str(payload.get("content", "")),
                similarity=round(similarity, 4),
                vector_rank=candidate.vector_rank,
                keyword_rank=candidate.keyword_rank,
                score=round(rrf(candidate), 5),
            )
        )

        if len(results) >= match_count:
            break

    # Label anything that arrived through a share with its owner's name, so a
    # reader can see that a claim rests on another workspace's document. One
    # batched lookup for the whole result set, and none at all in the common
    # case where nothing was shared.
    foreign = {row.workspace_id for row in results if row.workspace_id != workspace_id}
    if foreign:
        names = await owner_workspace_names(foreign)
        for row in results:
            if row.workspace_id != workspace_id:
                row.shared_from = names.get(row.workspace_id, "another workspace")

    citations = [
        Citation(
            index=i,
            chunk_id=row.chunk_id,
            document_id=row.document_id,
            filename=row.filename,
            section=row.section,
            chunk_index=row.chunk_index,
            similarity=row.similarity,
            # Longer than the retrieval preview: this one is read by a person
            # checking whether the answer is actually supported, not skimmed as
            # a debug line.
            snippet=row.content[:700],
            shared_from=row.shared_from,
        )
        for i, row in enumerate(results, start=1)
    ]

    debug = RetrievalDebug(
        workspace_id=workspace_id,
        query=query,
        candidate_count=len(candidates),
        returned_count=len(results),
        min_similarity=min_similarity,
        latency_ms=int((time.monotonic() - started) * 1000),
        chunks=[
            RetrievalChunk(
                chunk_id=row.chunk_id,
                document_id=row.document_id,
                workspace_id=row.workspace_id,
                shared_from=row.shared_from,
                filename=row.filename,
                section=row.section,
                chunk_index=row.chunk_index,
                similarity=row.similarity,
                vector_rank=row.vector_rank,
                keyword_rank=row.keyword_rank,
                score=row.score,
                preview=row.content[:240],
            )
            for row in results
        ],
    )

    return RetrievalOutcome(chunks=results, citations=citations, debug=debug)
