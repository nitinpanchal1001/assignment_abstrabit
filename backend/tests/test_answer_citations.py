"""Which sources get attached to a finished answer.

A citation is a claim that the answer *used* a passage. Attaching everything
retrieved would make that claim falsely, and the case where it matters most is
the one where the answer is "the documents don't cover that" — sources under a
refusal read as evidence for an answer that was never given.

This is the narrow question "what did it use". The broader "what did it look at"
is on the message's retrieval trace, which is unaffected by any of this.
"""

from __future__ import annotations

from app.db.schema import Citation
from app.rag.agent import _filter_used_citations


def citations(count: int) -> list[Citation]:
    return [
        Citation(
            index=i,
            chunk_id=f"c{i}",
            document_id=f"d{i}",
            filename=f"doc{i}.md",
            section=None,
            chunk_index=0,
            similarity=0.8,
            snippet=f"Passage {i}.",
        )
        for i in range(1, count + 1)
    ]


def test_a_refusal_carries_no_citations() -> None:
    """The case that prompted this: no markers means nothing was used."""
    answer = "The documents in this workspace don't cover that."
    assert _filter_used_citations(answer, citations(5)) == []


def test_an_empty_answer_carries_no_citations() -> None:
    assert _filter_used_citations("", citations(3)) == []


def test_only_the_cited_sources_are_kept() -> None:
    answer = "The surcharge is 14.5% [2] and is waived once per quarter [4]."
    kept = [c.index for c in _filter_used_citations(answer, citations(5))]
    assert kept == [2, 4]


def test_adjacent_markers_are_both_read() -> None:
    answer = "Both documents agree [1][3]."
    kept = [c.index for c in _filter_used_citations(answer, citations(4))]
    assert kept == [1, 3]


def test_a_source_cited_twice_appears_once() -> None:
    answer = "Lead times are five days [1], expedited to 48 hours [1]."
    kept = [c.index for c in _filter_used_citations(answer, citations(3))]
    assert kept == [1]


def test_a_marker_with_no_matching_source_is_ignored() -> None:
    """The model can hallucinate an index; it must not crash or invent a source.

    The UI renders such a marker as plain text rather than a link, so the two
    sides agree that it points at nothing.
    """
    answer = "See the appendix [9]."
    assert _filter_used_citations(answer, citations(3)) == []


def test_citation_order_follows_relevance_not_mention_order() -> None:
    """Indices are assigned by rank, and the returned list keeps that order.

    The numbers in the text are what tie a claim to a source, so reordering the
    list to match mention order would renumber nothing but would make the
    Sources row disagree with the ranking the debug trace shows.
    """
    answer = "Second first [3], then [1]."
    kept = [c.index for c in _filter_used_citations(answer, citations(4))]
    assert kept == [1, 3]


def test_retrieval_with_nothing_found_yields_nothing() -> None:
    assert _filter_used_citations("Anything [1].", []) == []
