"""The API must speak camelCase at every depth.

This exists because of a real bug, not a hypothetical one. ``MessageOut`` is an
``ApiModel`` and serialised its own fields as ``errorMessage`` / ``createdAt``,
but the ``Citation`` and ``RetrievalDebug`` models nested inside it came
straight from ``db.schema`` and carried no alias generator — Pydantic applies
``alias_generator`` per model, never down the tree. So the response was
camelCase on the outside and snake_case inside, and the frontend read
``citation.chunkId`` as ``undefined``. Nothing threw: the JSON was valid, the
list still rendered, and the only visible symptom was React complaining that
list items had no key.

A type mismatch across a language boundary cannot be caught by either side's
type checker, so it gets a test.

Offline by design — no database, no Gemini. Run with::

    uv run pytest
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from app.api_models import CitationOut, MessageOut, RetrievalDebugOut, UsageOut, wire
from app.db.schema import Citation, Message, RetrievalChunk, RetrievalDebug, UsageStats
from app.main import app

#: A wire field is one or more lowercase-led words, camel-joined. Rejects
#: `chunk_id`, allows `chunkId`, `id`, `_id`-free keys and all-caps-free names.
CAMEL = re.compile(r"^[a-z][a-zA-Z0-9]*$")


def offenders(node: Any, path: str = "") -> list[str]:
    """Every property name in a JSON Schema document that is not camelCase."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "properties" and isinstance(value, dict):
                found += [f"{path}.{name}" for name in value if not CAMEL.match(name)]
                for name, sub in value.items():
                    found += offenders(sub, f"{path}.{name}")
            else:
                found += offenders(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, item in enumerate(node):
            found += offenders(item, f"{path}[{i}]")
    return found


def test_every_declared_schema_is_camel_case() -> None:
    """Walks the generated OpenAPI document, so new endpoints are covered too.

    ``components.schemas`` contains every request and response model FastAPI
    knows about, flattened — including models that only ever appear nested,
    which is exactly where the original bug lived.
    """
    schemas = app.openapi()["components"]["schemas"]
    bad = offenders(schemas)
    assert not bad, "snake_case reached the wire: " + ", ".join(sorted(bad))


def sample_message() -> Message:
    citation = Citation(
        index=1,
        chunk_id="c1",
        document_id="d1",
        filename="handbook.md",
        section="Freight",
        chunk_index=0,
        similarity=0.87,
        snippet="FALCON-7 is the internal codename for the night-freight corridor.",
    )
    debug = RetrievalDebug(
        workspace_id="w1",
        query="q",
        candidate_count=8,
        returned_count=1,
        min_similarity=0.3,
        latency_ms=42,
        chunks=[
            RetrievalChunk(
                chunk_id="c1",
                document_id="d1",
                workspace_id="w1",
                filename="handbook.md",
                section="Freight",
                chunk_index=0,
                similarity=0.87,
                vector_rank=1,
                keyword_rank=2,
                score=0.03,
                preview="…",
            )
        ],
    )
    usage = UsageStats(
        model="gemini-3.5-flash-lite",
        input_tokens=100,
        output_tokens=20,
        total_tokens=120,
        latency_ms=900,
        tool_steps=0,
    )
    return Message(
        _id="m1",
        conversation_id="cv1",
        workspace_id="w1",
        role="assistant",
        content="Answer [1]",
        citations=[citation],
        retrieval=debug,
        usage=usage,
        status="complete",
        created_at=datetime.now(UTC),
    )


def test_message_payload_is_camel_case_at_every_depth() -> None:
    """The values, not just the declared schema."""
    payload = wire(MessageOut.of(sample_message()))

    assert payload["citations"][0]["chunkId"] == "c1"
    assert payload["retrieval"]["chunks"][0]["vectorRank"] == 1
    assert payload["usage"]["inputTokens"] == 100

    bad = [key for key in _all_keys(payload) if not CAMEL.match(key)]
    assert not bad, f"snake_case in the message payload: {sorted(bad)}"


def test_sse_frames_match_the_history_endpoint() -> None:
    """SSE bypasses ``response_model``, so its frames are serialised by hand.

    A citation streamed live and the same citation re-read from history have to
    be the same shape, or a page refresh changes what the UI can render.
    """
    message = sample_message()
    assert message.retrieval is not None and message.usage is not None

    streamed = {
        "citations": [wire(CitationOut.of(c)) for c in message.citations],
        "debug": wire(RetrievalDebugOut.of(message.retrieval)),
        "usage": wire(UsageOut.of(message.usage)),
    }
    persisted = wire(MessageOut.of(message))

    assert streamed["citations"] == persisted["citations"]
    assert streamed["debug"] == persisted["retrieval"]
    assert streamed["usage"] == persisted["usage"]


def test_mongo_documents_stay_snake_case() -> None:
    """The storage shape must NOT follow the wire shape.

    ``to_mongo()`` dumps ``by_alias=True`` to get ``_id``, and aliases apply to
    nested models on the way out. Adding the camelCase generator to
    ``db.schema.Citation`` would therefore have fixed the frontend by silently
    rewriting every key Mongo stores — and every existing row would stop
    matching. Keeping the two shapes separate is the point of ``api_models``.
    """
    stored = sample_message().to_mongo()

    assert "_id" in stored
    assert set(stored["citations"][0]) >= {"chunk_id", "document_id", "chunk_index"}
    assert "chunkId" not in stored["citations"][0]


def _all_keys(node: Any) -> list[str]:
    if isinstance(node, dict):
        return [k for k in node] + [k for v in node.values() for k in _all_keys(v)]
    if isinstance(node, list):
        return [k for item in node for k in _all_keys(item)]
    return []
