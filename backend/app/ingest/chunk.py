"""Structure-aware chunking.

Why not a fixed-width character window: a blind split lands mid-sentence and
mid-table, which both degrades the embedding (the vector describes a fragment,
not an idea) and produces citations that are painful to read. So the text is
first broken into atomic units along the strongest boundary that fits —
paragraph, then line, then sentence, then word — and those units are packed up
to a target size. A chunk therefore always ends on a real boundary.

Two further behaviours matter for retrieval quality:

- Markdown headings force a chunk break and are recorded as the chunk's
  ``section``, so a citation reads "handbook.md § Expenses" rather than just
  "handbook.md".
- Consecutive chunks overlap by whole units. An answer whose supporting
  sentence straddles a boundary is still retrievable, and because the overlap
  is unit-aligned it never duplicates half a sentence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.ingest.extract import ExtractedDocument, Segment

#: ~1200 chars ≈ 300 tokens, comfortably inside the embedding window.
TARGET_CHARS = 1200
#: Trailing context carried into the next chunk.
OVERLAP_CHARS = 180
#: Chunks shorter than this are merged backwards rather than embedded alone.
MIN_CHARS = 120

#: Ordered strongest → weakest.
_SEPARATORS = [
    re.compile(r"\n{2,}"),  # paragraph
    re.compile(r"\n"),  # line
    re.compile(r"(?<=[.!?])\s+"),  # sentence
    re.compile(r"\s+"),  # word
]

_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(\S.*?)\s*#*\s*$")


@dataclass
class DocumentChunk:
    content: str
    #: Nearest enclosing heading, or the segment label (e.g. "Page 3").
    section: str | None
    char_start: int
    char_end: int
    token_estimate: int


@dataclass
class _Unit:
    text: str
    start: int
    end: int


def estimate_tokens(text: str) -> int:
    """Deliberately an estimate.

    Gemini's tokeniser is remote, and a network round trip per chunk would
    dominate ingestion latency for a number used only for display and
    budgeting. ~4 chars/token is a good approximation for English prose.
    """
    return max(1, -(-len(text) // 4))


def chunk_document(doc: ExtractedDocument) -> list[DocumentChunk]:
    chunks: list[DocumentChunk] = []
    offset = 0
    for segment in doc.segments:
        chunks.extend(_chunk_segment(segment, offset))
        # Segments are conceptually joined by a blank line; keep global offsets
        # consistent with that so char ranges stay comparable.
        offset += len(segment.text) + 2
    return chunks


def _chunk_segment(segment: Segment, base_offset: int) -> list[DocumentChunk]:
    units = _split_units(segment.text, base_offset, TARGET_CHARS, 0)
    chunks: list[DocumentChunk] = []

    buffer: list[_Unit] = []
    buffer_len = 0
    heading: str | None = segment.label

    def flush(carry_overlap: bool) -> None:
        nonlocal buffer, buffer_len

        if not buffer:
            return

        content = "\n".join(u.text.strip() for u in buffer if u.text.strip()).strip()

        if content:
            # Merge a runt into its predecessor instead of embedding a fragment
            # whose vector would be dominated by noise — but NEVER across a
            # section boundary. Merging a short section backwards files its text
            # under the previous section's heading, so the citation names a
            # place the text did not come from. A slightly noisy vector is a
            # retrieval-quality cost; a misattributed citation is a correctness
            # one, and this app's whole claim is that answers are traceable.
            if len(content) < MIN_CHARS and chunks and chunks[-1].section == heading:
                previous = chunks[-1]
                previous.content = f"{previous.content}\n{content}"
                previous.char_end = buffer[-1].end
                previous.token_estimate = estimate_tokens(previous.content)
            else:
                chunks.append(
                    DocumentChunk(
                        content=content,
                        section=heading,
                        char_start=buffer[0].start,
                        char_end=buffer[-1].end,
                        token_estimate=estimate_tokens(content),
                    )
                )

        if not carry_overlap:
            buffer, buffer_len = [], 0
            return

        # Carry trailing units as overlap. The loop stops before index 0, so at
        # least one unit is always consumed — that guarantees forward progress
        # and makes an infinite loop structurally impossible.
        carry: list[_Unit] = []
        carry_len = 0
        for unit in reversed(buffer[1:]):
            if carry_len + len(unit.text) > OVERLAP_CHARS:
                break
            carry.insert(0, unit)
            carry_len += len(unit.text)

        buffer, buffer_len = carry, carry_len

    for unit in units:
        detected = _detect_heading(unit.text)

        # A heading starts a new section: flush without overlap so the previous
        # section's text does not bleed into the new one's citation.
        if detected and buffer_len > 0:
            flush(carry_overlap=False)
        if detected:
            heading = f"{segment.label} · {detected}" if segment.label else detected

        if buffer_len + len(unit.text) > TARGET_CHARS and buffer_len > 0:
            flush(carry_overlap=True)

        buffer.append(unit)
        buffer_len += len(unit.text) + 1

    flush(carry_overlap=False)
    return chunks


def _split_units(text: str, start: int, max_chars: int, level: int) -> list[_Unit]:
    """Recursively break text into units no longer than ``max_chars``."""
    if not text.strip():
        return []

    # `level > 0` is load-bearing, not a micro-optimisation. Returning early at
    # level 0 whenever the whole segment fits means a short document is handed
    # to the packer as ONE unit — and `_detect_heading` only ever inspects a
    # unit's first line, so every heading after the first became invisible. A
    # two-section note under the target size collapsed into a single chunk
    # labelled with section one, and section two's text was then cited as
    # belonging to section one. Splitting on paragraphs unconditionally costs
    # nothing, because the packing loop re-merges whatever fits.
    if len(text) <= max_chars and level > 0:
        return [_Unit(text=text, start=start, end=start + len(text))]

    if level >= len(_SEPARATORS):
        # No separator left — a single unbroken token longer than the target
        # (minified JSON, a base64 blob). Hard-slice rather than emit something
        # that would overflow the embedding window.
        return [
            _Unit(
                text=text[i : i + max_chars],
                start=start + i,
                end=start + i + len(text[i : i + max_chars]),
            )
            for i in range(0, len(text), max_chars)
        ]

    parts = _split_keeping_offsets(text, start, _SEPARATORS[level])

    # Separator absent at this level — descend without emitting anything.
    if len(parts) <= 1:
        return _split_units(text, start, max_chars, level + 1)

    out: list[_Unit] = []
    for part in parts:
        if len(part.text) <= max_chars:
            out.append(part)
        else:
            out.extend(_split_units(part.text, part.start, max_chars, level + 1))
    return out


def _split_keeping_offsets(text: str, base: int, separator: re.Pattern[str]) -> list[_Unit]:
    out: list[_Unit] = []
    cursor = 0
    for match in separator.finditer(text):
        piece = text[cursor : match.start()]
        if piece.strip():
            out.append(_Unit(text=piece, start=base + cursor, end=base + match.start()))
        cursor = match.end()

    tail = text[cursor:]
    if tail.strip():
        out.append(_Unit(text=tail, start=base + cursor, end=base + len(text)))

    return out


def _detect_heading(text: str) -> str | None:
    first_line = text.split("\n", 1)[0]
    match = _HEADING.match(first_line)
    return match.group(2).strip()[:120] if match else None
