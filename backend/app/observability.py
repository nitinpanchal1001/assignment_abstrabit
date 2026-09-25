"""Per-workspace operational view: cost, latency, retrieval quality, tool health.

Three questions a person actually asks about a running RAG app, and where each
is answered from:

WHAT DID THIS COST, AND HOW SLOW WAS IT?
    Every assistant turn persists a ``UsageStats`` — model, input/output tokens,
    wall-clock latency, tool steps. Those rows are the per-request record; this
    module summarises them.

IS RETRIEVAL ACTUALLY FINDING ANYTHING?
    A turn is a retrieval *hit* if the workspace-scoped search returned at least
    one chunk. The miss rate is the single most useful number in a RAG
    deployment: a workspace answering "I don't know" constantly is usually
    missing documents, not badly prompted, and the two look identical from the
    chat window.

ARE THE TOOLS WORKING?
    Every tool call is audited, successes and rejections alike, so success rate
    per tool is a straight aggregation. Rejections are kept visible rather than
    filtered out — a model repeatedly failing validation on one tool is a broken
    schema or a bad description, and it is invisible if you only count successes.

Percentiles are computed here rather than in Mongo. The sample is one page of
recent turns, which is small, already in memory, and needed in full for the
per-request table anyway; an aggregation stage would be a second round trip to
compute something from data already fetched.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.db.repositories import WorkspaceRepository
from app.db.schema import Message, ToolCall

#: How many recent turns the summary is computed over. Bounded on purpose: a
#: figure covering "all time" answers a question nobody asks, and hides a
#: regression that started this morning behind six months of healthy history.
SAMPLE_SIZE = 50


@dataclass(frozen=True)
class RequestRecord:
    """One answered question."""

    message_id: str
    created_at: object
    status: str
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    latency_ms: int | None
    tool_steps: int
    #: True when retrieval returned at least one chunk for this turn.
    retrieval_hit: bool
    chunks_retrieved: int
    #: Number of sources the finished answer actually cited.
    citations: int
    preview: str


@dataclass(frozen=True)
class LatencySummary:
    p50_ms: int | None
    p95_ms: int | None
    max_ms: int | None


@dataclass(frozen=True)
class ToolHealth:
    tool_name: str
    total: int
    success: int
    failure: int
    avg_latency_ms: int | None
    #: Failure counts keyed by status, so a validation problem is
    #: distinguishable from an outage.
    by_status: dict[str, int] = field(default_factory=dict)

    @property
    def success_rate(self) -> float:
        return self.success / self.total if self.total else 0.0


@dataclass(frozen=True)
class ObservabilityReport:
    sample_size: int
    requests: list[RequestRecord]
    latency: LatencySummary
    total_tokens: int
    input_tokens: int
    output_tokens: int
    #: Turns where retrieval returned nothing, over turns that ran retrieval.
    retrieval_hits: int
    retrieval_misses: int
    failed_requests: int
    tools: list[ToolHealth]
    recent_failures: list[ToolCall]

    @property
    def retrieval_hit_rate(self) -> float:
        total = self.retrieval_hits + self.retrieval_misses
        return self.retrieval_hits / total if total else 0.0


def _percentile(sorted_values: list[int], fraction: float) -> int | None:
    """Nearest-rank percentile.

    Nearest-rank rather than interpolated: with a sample of tens, interpolation
    invents a latency no request actually had, and "p95 = 2400ms" is only useful
    if some request really took 2400ms.
    """
    if not sorted_values:
        return None
    index = max(0, min(len(sorted_values) - 1, round(fraction * len(sorted_values)) - 1))
    return sorted_values[index]


def _preview(message: Message) -> str:
    text = (message.content or message.error_message or "").strip().replace("\n", " ")
    return text[:120]


def _to_record(message: Message) -> RequestRecord:
    usage = message.usage
    retrieval = message.retrieval

    return RequestRecord(
        message_id=message.id,
        created_at=message.created_at,
        status=message.status,
        model=usage.model if usage else None,
        input_tokens=usage.input_tokens if usage else None,
        output_tokens=usage.output_tokens if usage else None,
        total_tokens=usage.total_tokens if usage else None,
        latency_ms=usage.latency_ms if usage else None,
        tool_steps=usage.tool_steps if usage else 0,
        retrieval_hit=bool(retrieval and retrieval.returned_count > 0),
        chunks_retrieved=retrieval.returned_count if retrieval else 0,
        citations=len(message.citations),
        preview=_preview(message),
    )


async def build_report(repo: WorkspaceRepository) -> ObservabilityReport:
    messages = await repo.recent_assistant_messages(SAMPLE_SIZE)
    stats = await repo.tool_call_stats()
    failures = await repo.recent_tool_failures(10)

    records = [_to_record(m) for m in messages]

    latencies = sorted(r.latency_ms for r in records if r.latency_ms is not None)

    # A turn that failed before retrieval ran has no retrieval trace, and
    # counting it as a miss would blame retrieval for an upstream outage. Only
    # turns that actually searched are in the denominator.
    searched = [m for m in messages if m.retrieval is not None]
    hits = sum(1 for m in searched if m.retrieval and m.retrieval.returned_count > 0)

    tools = [_to_tool_health(row) for row in stats]

    return ObservabilityReport(
        sample_size=len(records),
        requests=records,
        latency=LatencySummary(
            p50_ms=_percentile(latencies, 0.50),
            p95_ms=_percentile(latencies, 0.95),
            max_ms=latencies[-1] if latencies else None,
        ),
        total_tokens=sum(r.total_tokens or 0 for r in records),
        input_tokens=sum(r.input_tokens or 0 for r in records),
        output_tokens=sum(r.output_tokens or 0 for r in records),
        retrieval_hits=hits,
        retrieval_misses=len(searched) - hits,
        failed_requests=sum(1 for r in records if r.status == "failed"),
        tools=tools,
        recent_failures=failures,
    )


def _to_tool_health(row: dict) -> ToolHealth:
    by_status: dict[str, int] = {}
    success = 0
    weighted_latency = 0.0
    counted = 0

    for entry in row.get("by_status", []):
        status = str(entry.get("status", "unknown"))
        count = int(entry.get("count", 0))
        by_status[status] = by_status.get(status, 0) + count

        if status == "success":
            success += count

        average = entry.get("avg_latency_ms")
        if average is not None:
            # Weighted by count, because averaging the per-status averages
            # would give one rejected call the same weight as fifty successes.
            weighted_latency += float(average) * count
            counted += count

    total = int(row.get("total", 0))

    return ToolHealth(
        tool_name=str(row.get("_id", "unknown")),
        total=total,
        success=success,
        failure=total - success,
        avg_latency_ms=round(weighted_latency / counted) if counted else None,
        by_status=by_status,
    )
