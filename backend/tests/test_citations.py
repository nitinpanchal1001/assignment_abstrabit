"""A citation must mean "the answer used this".

The filter used to fall back to returning every retrieved chunk when the answer
cited nothing, so a refusal — "the documents don't cover that" — arrived with a
full list of sources attached under it. That reads as evidence for an answer
that was never given.

Offline: pure function, no database, no Gemini.
"""

from __future__ import annotations

from app.db.schema import Citation
from app.rag.agent import _filter_used_citations


def citation(index: int) -> Citation:
    return Citation(
        index=index,
        chunk_id=f"chunk-{index}",
        document_id="doc-1",
        filename="handbook.md",
        section=None,
        chunk_index=index,
        similarity=0.8,
        snippet=f"passage {index}",
    )


SOURCES = [citation(1), citation(2), citation(3)]


def test_keeps_only_the_cited_sources() -> None:
    kept = _filter_used_citations("Rates rose in Q3 [2], but not in Q4 [3].", SOURCES)

    assert [c.index for c in kept] == [2, 3]


def test_a_refusal_carries_no_sources() -> None:
    """The case that motivated the change."""
    kept = _filter_used_citations("The documents in this workspace don't cover that.", SOURCES)

    assert kept == []


def test_markers_pointing_at_nothing_are_dropped() -> None:
    """A hallucinated [9] must not become a citation, or resolve to another."""
    kept = _filter_used_citations("See [9] for details.", SOURCES)

    assert kept == []


def test_repeated_markers_yield_one_source_each() -> None:
    kept = _filter_used_citations("Both [1] and [1] again, plus [2].", SOURCES)

    assert [c.index for c in kept] == [1, 2]


def test_snippet_survives_the_filter() -> None:
    """The UI shows this text when a marker is clicked, so it has to come through."""
    kept = _filter_used_citations("As stated [1].", SOURCES)

    assert kept[0].snippet == "passage 1"
