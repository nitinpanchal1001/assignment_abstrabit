"""Hybrid retrieval: fusion maths, and the tenant scoping of both arms.

Offline — the Qdrant and Mongo arms are replaced with fixtures, so what is under
test is this module's own logic rather than either database. The live,
end-to-end proof that isolation holds is ``scripts/isolation_test.py``, which
needs a seeded cluster; these are the parts that can be pinned in CI.

The property that matters most here is negative: there is no code path that
filters by workspace *after* ranking. Both arms are asked for tenant-scoped
candidates, and the fusion step never sees a foreign chunk to drop.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from app.db.schema import Chunk
from app.rag import retrieve as retrieve_module
from app.rag.retrieve import RRF_K, retrieve_chunks
from app.vector.qdrant import VectorHit

WORKSPACE = "11111111-1111-4111-8111-111111111111"


class FakeRepo:
    """Stands in for a WorkspaceRepository bound to one workspace."""

    def __init__(self, keyword_hits: list[Chunk] | None = None) -> None:
        self.workspace_id = WORKSPACE
        self._keyword_hits = keyword_hits or []
        self.text_search_calls: list[tuple[str, int]] = []

    async def text_search(self, query: str, limit: int) -> list[tuple[Chunk, float]]:
        self.text_search_calls.append((query, limit))
        return [(c, 1.0) for c in self._keyword_hits]


def chunk(chunk_id: str, *, content: str = "Body text.", section: str | None = None) -> Chunk:
    return Chunk(
        _id=chunk_id,
        workspace_id=WORKSPACE,
        document_id=f"doc-{chunk_id}",
        chunk_index=0,
        content=content,
        section=section,
        char_start=0,
        char_end=len(content),
        token_estimate=3,
        filename=f"{chunk_id}.md",
        created_at=datetime.now(UTC),
    )


def hit(chunk_id: str, score: float) -> VectorHit:
    return VectorHit(
        chunk_id=chunk_id,
        score=score,
        payload={
            "workspace_id": WORKSPACE,
            "document_id": f"doc-{chunk_id}",
            "chunk_index": 0,
            "filename": f"{chunk_id}.md",
            "section": "Freight",
            "content": "Body text.",
        },
    )


@pytest.fixture
def arms(monkeypatch: pytest.MonkeyPatch):
    """Replace the embedding call and the dense arm; record their arguments."""
    recorded: dict[str, Any] = {"search_kwargs": None, "embedded": None}

    async def fake_embed_query(text: str) -> list[float]:
        recorded["embedded"] = text
        return [0.1] * 8

    async def fake_search_chunks(**kwargs: Any) -> list[VectorHit]:
        recorded["search_kwargs"] = kwargs
        return recorded.get("dense", [])

    monkeypatch.setattr(retrieve_module, "embed_query", fake_embed_query)
    monkeypatch.setattr(retrieve_module, "search_chunks", fake_search_chunks)
    return recorded


# --- tenant scoping --------------------------------------------------------


async def test_both_arms_are_asked_for_tenant_scoped_candidates(arms) -> None:
    """Neither arm is allowed to return cross-tenant rows for fusion to sort out."""
    arms["dense"] = [hit("a", 0.9)]
    repo = FakeRepo(keyword_hits=[chunk("b")])

    await retrieve_chunks(repo=repo, query="freight surcharge")

    # Dense arm: the workspace is passed into the vector query itself.
    assert arms["search_kwargs"]["workspace_id"] == WORKSPACE
    # Keyword arm: delegated to the repository, which is bound to the workspace.
    assert repo.text_search_calls == [("freight surcharge", retrieve_module.CANDIDATE_POOL)]


async def test_the_query_is_embedded_not_the_documents(arms) -> None:
    """Asymmetric prefixes differ for queries and documents; this is the query path."""
    arms["dense"] = []
    await retrieve_chunks(repo=FakeRepo(), query="what is the surcharge")
    assert arms["embedded"] == "what is the surcharge"


async def test_every_returned_chunk_is_stamped_with_the_workspace(arms) -> None:
    arms["dense"] = [hit("a", 0.9)]
    outcome = await retrieve_chunks(repo=FakeRepo(keyword_hits=[chunk("b")]), query="q")

    assert outcome.chunks
    assert all(c.workspace_id == WORKSPACE for c in outcome.chunks)
    assert outcome.debug.workspace_id == WORKSPACE


# --- fusion ----------------------------------------------------------------


async def test_a_chunk_found_by_both_arms_outranks_one_found_by_either(arms) -> None:
    """The reason RRF is worth having at all.

    `both` is second in each arm; `dense_only` is first in the vector arm. Two
    mediocre ranks beat one good one, which is what agreement between an
    embedding and a lexical match is supposed to buy.
    """
    arms["dense"] = [hit("dense_only", 0.95), hit("both", 0.80)]
    repo = FakeRepo(keyword_hits=[chunk("kw_only"), chunk("both")])

    outcome = await retrieve_chunks(repo=repo, query="q")

    assert [c.chunk_id for c in outcome.chunks][0] == "both"
    top = outcome.chunks[0]
    assert top.vector_rank == 2
    assert top.keyword_rank == 2
    assert top.score == pytest.approx(2 / (RRF_K + 2), abs=1e-5)


async def test_rrf_scores_match_the_formula(arms) -> None:
    arms["dense"] = [hit("a", 0.9)]
    outcome = await retrieve_chunks(repo=FakeRepo(), query="q")

    only = outcome.chunks[0]
    assert only.vector_rank == 1
    assert only.keyword_rank is None
    assert only.score == pytest.approx(1 / (RRF_K + 1), abs=1e-5)


async def test_a_chunk_in_both_arms_is_not_returned_twice(arms) -> None:
    arms["dense"] = [hit("shared", 0.9)]
    outcome = await retrieve_chunks(repo=FakeRepo(keyword_hits=[chunk("shared")]), query="q")

    assert [c.chunk_id for c in outcome.chunks] == ["shared"]
    assert outcome.chunks[0].vector_rank == 1
    assert outcome.chunks[0].keyword_rank == 1


# --- thresholding ----------------------------------------------------------


async def test_weak_vector_hits_are_dropped(arms) -> None:
    arms["dense"] = [hit("strong", 0.80), hit("weak", 0.01)]
    outcome = await retrieve_chunks(repo=FakeRepo(), query="q", min_similarity=0.5)

    assert [c.chunk_id for c in outcome.chunks] == ["strong"]


async def test_keyword_only_hits_survive_the_similarity_floor(arms) -> None:
    """They were never scored on cosine, so judging them by it would be wrong.

    An exact identifier match that the dense arm missed entirely is precisely
    the hit hybrid search exists to recover; discarding it for lacking a score
    it could not have would undo the second arm.
    """
    arms["dense"] = []
    outcome = await retrieve_chunks(
        repo=FakeRepo(keyword_hits=[chunk("FALCON-7")]), query="q", min_similarity=0.9
    )

    assert [c.chunk_id for c in outcome.chunks] == ["FALCON-7"]
    assert outcome.chunks[0].similarity == 0.0
    assert outcome.chunks[0].keyword_rank == 1


async def test_keyword_arm_payload_is_populated_from_mongo(arms) -> None:
    """A keyword-only hit has no Qdrant payload, so its text comes from the row."""
    arms["dense"] = []
    row = chunk("k", content="Emergency surcharge is 14.5%.", section="Surcharges")
    outcome = await retrieve_chunks(repo=FakeRepo(keyword_hits=[row]), query="q")

    found = outcome.chunks[0]
    assert found.content == "Emergency surcharge is 14.5%."
    assert found.section == "Surcharges"
    assert found.filename == "k.md"
    assert found.document_id == "doc-k"


# --- output shape ----------------------------------------------------------


async def test_match_count_caps_the_result(arms) -> None:
    arms["dense"] = [hit(f"c{i}", 0.9 - i / 100) for i in range(20)]
    outcome = await retrieve_chunks(repo=FakeRepo(), query="q", match_count=3)

    assert len(outcome.chunks) == 3
    assert len(outcome.citations) == 3
    assert outcome.debug.returned_count == 3
    # Candidates considered before the cap are still reported.
    assert outcome.debug.candidate_count == 20


async def test_citations_are_one_indexed_and_aligned_with_chunks(arms) -> None:
    """The model cites `[1]`, and the UI resolves it positionally."""
    arms["dense"] = [hit("a", 0.9), hit("b", 0.8)]
    outcome = await retrieve_chunks(repo=FakeRepo(), query="q")

    assert [c.index for c in outcome.citations] == [1, 2]
    for citation, matched in zip(outcome.citations, outcome.chunks, strict=True):
        assert citation.chunk_id == matched.chunk_id
        assert citation.document_id == matched.document_id
        assert citation.filename == matched.filename


async def test_no_matches_yields_an_empty_but_well_formed_outcome(arms) -> None:
    """The honest-refusal path. It must not raise, and must not invent sources."""
    arms["dense"] = []
    outcome = await retrieve_chunks(repo=FakeRepo(), query="capital of France")

    assert outcome.chunks == []
    assert outcome.citations == []
    assert outcome.debug.returned_count == 0
    assert outcome.debug.candidate_count == 0
    assert outcome.debug.workspace_id == WORKSPACE


async def test_debug_view_reports_which_arm_found_each_chunk(arms) -> None:
    """This is what the retrieval panel renders to prove isolation is holding."""
    arms["dense"] = [hit("both", 0.9)]
    outcome = await retrieve_chunks(repo=FakeRepo(keyword_hits=[chunk("both")]), query="q")

    entry = outcome.debug.chunks[0]
    assert entry.vector_rank == 1
    assert entry.keyword_rank == 1
    assert entry.workspace_id == WORKSPACE
    assert entry.preview
    assert outcome.debug.latency_ms >= 0
