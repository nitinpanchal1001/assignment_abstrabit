"""Chunking decides what retrieval can ever find, and what a citation reads like.

The properties worth pinning are the ones a rewrite would silently break:
chunks end on real boundaries, headings become sections, overlap never splits a
sentence, and no chunk exceeds the embedding budget. A regression in any of
these degrades answer quality without raising anything.
"""

from __future__ import annotations

from app.ingest.chunk import (
    MIN_CHARS,
    OVERLAP_CHARS,
    TARGET_CHARS,
    chunk_document,
    estimate_tokens,
)
from app.ingest.extract import ExtractedDocument, Segment


def doc(*segments: Segment) -> ExtractedDocument:
    return ExtractedDocument(
        segments=list(segments), total_chars=sum(len(s.text) for s in segments)
    )


def one(text: str, label: str | None = None) -> ExtractedDocument:
    return doc(Segment(label=label, text=text))


def paragraph(word: str, count: int) -> str:
    return " ".join(f"{word}{i}" for i in range(count))


# --- structure -------------------------------------------------------------


def test_short_document_is_one_chunk() -> None:
    chunks = chunk_document(one("Emergency freight surcharge is 14.5% of the base rate."))
    assert len(chunks) == 1
    assert chunks[0].content == "Emergency freight surcharge is 14.5% of the base rate."


def test_markdown_heading_becomes_the_section() -> None:
    chunks = chunk_document(
        one(
            "# Handbook\n\nIntro paragraph long enough to stand on its own as a chunk of text.\n\n"
            "## Emergency freight surcharge\n\n"
            "The surcharge is 14.5% and applies to the base rate only, never to accessorials."
        )
    )

    sections = [c.section for c in chunks]
    assert "Emergency freight surcharge" in sections

    surcharge = next(c for c in chunks if c.section == "Emergency freight surcharge")
    assert "14.5%" in surcharge.content


def test_heading_starts_a_new_chunk_so_sections_do_not_bleed() -> None:
    """Two adjacent short sections must not merge into one citation.

    Without a forced break they would fit comfortably in one chunk, and a
    citation would claim text from a section it did not come from.
    """
    body_a = paragraph("alpha", 40)
    body_b = paragraph("bravo", 40)
    chunks = chunk_document(one(f"## Section A\n\n{body_a}\n\n## Section B\n\n{body_b}"))

    a = next(c for c in chunks if c.section == "Section A")
    b = next(c for c in chunks if c.section == "Section B")
    assert "bravo0" not in a.content
    assert "alpha0" not in b.content


def test_segment_label_is_combined_with_the_heading() -> None:
    """A PDF citation should read "Page 3 · Expenses", not one or the other."""
    chunks = chunk_document(one("## Expenses\n\n" + paragraph("word", 60), label="Page 3"))
    assert chunks[0].section == "Page 3 · Expenses"


def test_segment_label_is_used_when_there_is_no_heading() -> None:
    chunks = chunk_document(one(paragraph("word", 60), label="Page 7"))
    assert all(c.section == "Page 7" for c in chunks)


def test_segments_are_chunked_independently() -> None:
    chunks = chunk_document(
        doc(
            Segment(label="Page 1", text=paragraph("first", 50)),
            Segment(label="Page 2", text=paragraph("second", 50)),
        )
    )
    labels = {c.section for c in chunks}
    assert labels == {"Page 1", "Page 2"}


# --- packing ---------------------------------------------------------------


def test_long_document_splits_into_several_bounded_chunks() -> None:
    chunks = chunk_document(one("\n\n".join(paragraph("word", 60) for _ in range(12))))

    assert len(chunks) > 1
    # Overlap is carried on top of the target, so the ceiling is target+overlap
    # plus the separators joining carried units.
    for c in chunks:
        assert len(c.content) <= TARGET_CHARS + OVERLAP_CHARS + 64, len(c.content)


def test_chunks_end_on_a_real_boundary() -> None:
    """The point of unit-aligned packing: no chunk ends mid-word."""
    sentences = " ".join(
        f"Sentence number {i} explains a distinct policy detail." for i in range(80)
    )
    chunks = chunk_document(one(sentences))

    assert len(chunks) > 1
    for c in chunks:
        assert not c.content.endswith(("Sentenc", "explain", "polic"))
        # Every chunk ends with a complete token, i.e. no trailing partial word.
        assert c.content == c.content.strip()


def test_consecutive_chunks_overlap() -> None:
    """An answer straddling a boundary must remain retrievable."""
    sentences = " ".join(f"Clause {i} sets out an obligation of the carrier." for i in range(90))
    chunks = chunk_document(one(sentences))

    assert len(chunks) > 1
    tail_words = set(chunks[0].content.split()[-8:])
    head_words = set(chunks[1].content.split()[:12])
    assert tail_words & head_words, "no overlap carried between consecutive chunks"


def test_a_runt_is_merged_backwards_rather_than_embedded_alone() -> None:
    """A 20-character chunk embeds to a vector dominated by noise."""
    body = paragraph("word", 200)
    chunks = chunk_document(one(f"{body}\n\nTiny tail."))

    assert all(len(c.content) >= MIN_CHARS for c in chunks), [len(c.content) for c in chunks]
    assert "Tiny tail." in chunks[-1].content


def test_a_short_section_is_not_merged_into_the_previous_one() -> None:
    """The runt rule stops at a section boundary.

    Merging here would file the second section's text under the first one's
    heading, and the citation would name a place the text did not come from.
    A noisy vector is a retrieval cost; a wrong citation is a correctness one.
    """
    chunks = chunk_document(one("## Waivers\n\nOne per quarter.\n\n## Appeals\n\nWithin 30 days."))

    sections = [c.section for c in chunks]
    assert sections == ["Waivers", "Appeals"]

    appeals = next(c for c in chunks if c.section == "Appeals")
    assert "One per quarter" not in appeals.content


def test_an_unbreakable_token_is_hard_sliced() -> None:
    """Minified JSON or a base64 blob has no separator at any level.

    Emitting it whole would overflow the embedding window; the fallback slices
    it rather than failing the document.
    """
    blob = "A" * (TARGET_CHARS * 3)
    chunks = chunk_document(one(blob))

    assert len(chunks) >= 3
    for c in chunks:
        assert len(c.content) <= TARGET_CHARS + OVERLAP_CHARS + 64


# --- bookkeeping -----------------------------------------------------------


def test_offsets_are_ordered_and_non_negative() -> None:
    chunks = chunk_document(one("\n\n".join(paragraph("word", 60) for _ in range(8))))

    for c in chunks:
        assert 0 <= c.char_start <= c.char_end


def test_empty_and_whitespace_documents_produce_nothing() -> None:
    """Callers treat an empty result as "no extractable text" and say so."""
    assert chunk_document(one("")) == []
    assert chunk_document(one("   \n\n \t ")) == []


def test_token_estimate_is_proportional_and_never_zero() -> None:
    assert estimate_tokens("") == 1
    assert estimate_tokens("a" * 400) == 100
    assert estimate_tokens("a" * 401) == 101
