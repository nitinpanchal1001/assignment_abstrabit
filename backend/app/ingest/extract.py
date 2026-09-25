"""Turn an uploaded file into plain text segments.

A "segment" is the coarsest natural division the format offers — a PDF page, or
the whole file for text formats. Segments exist so a citation can point at
"Page 4" rather than only a filename; the chunker refines them further using
document structure.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass

from app import errors

TEXT_EXTENSIONS = {"txt", "md", "markdown", "csv", "json", "log", "yaml", "yml"}
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | {"pdf", "docx"}


@dataclass(frozen=True)
class Segment:
    #: Human-readable location, e.g. "Page 3". None when the format has none.
    label: str | None
    text: str


@dataclass(frozen=True)
class ExtractedDocument:
    segments: list[Segment]
    total_chars: int


def extension_of(filename: str) -> str:
    _, _, ext = filename.rpartition(".")
    return ext.lower() if ext and ext != filename else ""


def extract_document(data: bytes, filename: str, mime_type: str) -> ExtractedDocument:
    """Dispatch on extension first, MIME type second.

    Browsers are inconsistent about MIME for .md (variously text/markdown,
    application/octet-stream, or empty), so the filename is more reliable.
    """
    ext = extension_of(filename)

    if ext == "pdf" or mime_type == "application/pdf":
        segments = _extract_pdf(data)
    elif (
        ext == "docx"
        or mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ):
        segments = _extract_docx(data)
    elif ext in TEXT_EXTENSIONS or mime_type.startswith("text/"):
        segments = [Segment(label=None, text=data.decode("utf-8", errors="replace"))]
    else:
        raise errors.unsupported_file_type(
            ext or mime_type or "unknown", sorted(SUPPORTED_EXTENSIONS)
        )

    cleaned = [Segment(label=s.label, text=_normalise_whitespace(s.text)) for s in segments]
    cleaned = [s for s in cleaned if s.text]

    total = sum(len(s.text) for s in cleaned)
    if total == 0:
        raise errors.no_extractable_text(filename)

    return ExtractedDocument(segments=cleaned, total_chars=total)


def _extract_pdf(data: bytes) -> list[Segment]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    return [
        Segment(label=f"Page {i + 1}", text=page.extract_text() or "")
        for i, page in enumerate(reader.pages)
    ]


def _extract_docx(data: bytes) -> list[Segment]:
    import docx

    document = docx.Document(io.BytesIO(data))
    text = "\n\n".join(p.text for p in document.paragraphs)
    return [Segment(label=None, text=text)]


_ZERO_WIDTH = re.compile(r"[​-‍﻿]")


def _normalise_whitespace(text: str) -> str:
    """Collapse extraction artefacts without destroying paragraph structure.

    The chunker relies on blank lines to find boundaries, so runs of newlines
    are capped at two rather than collapsed to one.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\xa0", " ")
    text = _ZERO_WIDTH.sub("", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
