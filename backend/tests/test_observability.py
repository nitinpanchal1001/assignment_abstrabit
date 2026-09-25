"""The operational summary: cost, latency, retrieval quality, tool health.

The numbers on this page get read as facts, so the arithmetic is pinned. The
one that matters most is the retrieval hit rate — a workspace answering
"I don't know" constantly is usually missing documents rather than badly
prompted, and those two look identical from the chat window.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.db.schema import Citation, Message, RetrievalDebug, ToolCall, UsageStats
from app.observability import _percentile, _to_tool_health, build_report

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


def message(
    *,
    index: int = 0,
    status: str = "complete",
    latency_ms: int | None = 900,
    tokens: tuple[int, int] | None = (100, 20),
    retrieved: int | None = 3,
    citations: int = 1,
    tool_steps: int = 0,
) -> Message:
    usage = (
        UsageStats(
            model="gemini-3.5-flash-lite",
            input_tokens=tokens[0] if tokens else None,
            output_tokens=tokens[1] if tokens else None,
            total_tokens=(tokens[0] + tokens[1]) if tokens else None,
            latency_ms=latency_ms or 0,
            tool_steps=tool_steps,
        )
        if latency_ms is not None
        else None
    )

    retrieval = (
        RetrievalDebug(
            workspace_id="w1",
            query="q",
            candidate_count=retrieved,
            returned_count=retrieved,
            min_similarity=0.15,
            latency_ms=40,
            chunks=[],
        )
        if retrieved is not None
        else None
    )

    return Message(
        _id=f"m{index}",
        conversation_id="cv1",
        workspace_id="w1",
        role="assistant",
        content="An answer [1]",
        citations=[
            Citation(
                index=i + 1,
                chunk_id=f"c{i}",
                document_id="d1",
                filename="handbook.md",
                section=None,
                chunk_index=0,
                similarity=0.8,
            )
            for i in range(citations)
        ],
        retrieval=retrieval,
        usage=usage,
        status=status,  # type: ignore[arg-type]
        created_at=NOW - timedelta(minutes=index),
    )


class FakeRepo:
    def __init__(
        self,
        messages: list[Message],
        stats: list[dict[str, Any]] | None = None,
        failures: list[ToolCall] | None = None,
    ) -> None:
        self.workspace_id = "w1"
        self._messages = messages
        self._stats = stats or []
        self._failures = failures or []

    async def recent_assistant_messages(self, limit: int) -> list[Message]:
        return self._messages[:limit]

    async def tool_call_stats(self) -> list[dict[str, Any]]:
        return self._stats

    async def recent_tool_failures(self, limit: int = 20) -> list[ToolCall]:
        return self._failures[:limit]


# --- percentiles -----------------------------------------------------------


def test_percentile_of_an_empty_sample_is_none() -> None:
    assert _percentile([], 0.5) is None


def test_percentile_returns_a_value_that_actually_occurred() -> None:
    """Nearest-rank, not interpolated: p95 should name a real request."""
    values = [100, 200, 300, 400, 500]
    for fraction in (0.5, 0.95, 0.99):
        assert _percentile(values, fraction) in values


def test_percentiles_are_ordered() -> None:
    values = sorted([120, 340, 90, 1500, 600, 210, 880, 430, 2100, 75])
    p50 = _percentile(values, 0.50)
    p95 = _percentile(values, 0.95)
    assert p50 is not None and p95 is not None
    assert p50 <= p95 <= values[-1]


def test_a_single_sample_is_its_own_percentile() -> None:
    assert _percentile([42], 0.5) == 42
    assert _percentile([42], 0.95) == 42


# --- the report ------------------------------------------------------------


async def test_tokens_and_latency_are_summed_across_the_sample() -> None:
    repo = FakeRepo([message(index=i) for i in range(4)])
    report = await build_report(repo)  # type: ignore[arg-type]

    assert report.sample_size == 4
    assert report.input_tokens == 400
    assert report.output_tokens == 80
    assert report.total_tokens == 480
    assert report.latency.p50_ms == 900
    assert report.latency.max_ms == 900


async def test_retrieval_hit_rate_counts_turns_that_found_something() -> None:
    repo = FakeRepo(
        [
            message(index=0, retrieved=3),
            message(index=1, retrieved=0),
            message(index=2, retrieved=5),
            message(index=3, retrieved=0),
        ]
    )
    report = await build_report(repo)  # type: ignore[arg-type]

    assert report.retrieval_hits == 2
    assert report.retrieval_misses == 2
    assert report.retrieval_hit_rate == 0.5


async def test_a_turn_that_never_ran_retrieval_is_not_counted_as_a_miss() -> None:
    """Otherwise an upstream outage reads as a retrieval problem.

    A turn that failed before retrieval has no trace at all; blaming the
    retriever for it would send someone hunting for missing documents when the
    model API was down.
    """
    repo = FakeRepo(
        [
            message(index=0, retrieved=3),
            message(index=1, status="failed", retrieved=None, latency_ms=None),
        ]
    )
    report = await build_report(repo)  # type: ignore[arg-type]

    assert report.retrieval_hits == 1
    assert report.retrieval_misses == 0
    assert report.retrieval_hit_rate == 1.0
    assert report.failed_requests == 1


async def test_an_empty_workspace_reports_zeroes_rather_than_dividing_by_zero() -> None:
    report = await build_report(FakeRepo([]))  # type: ignore[arg-type]

    assert report.sample_size == 0
    assert report.retrieval_hit_rate == 0.0
    assert report.total_tokens == 0
    assert report.latency.p50_ms is None
    assert report.requests == []


async def test_a_turn_with_no_usage_does_not_break_the_summary() -> None:
    """Providers do not always report usage; the page must still render."""
    repo = FakeRepo([message(index=0, latency_ms=None), message(index=1)])
    report = await build_report(repo)  # type: ignore[arg-type]

    assert report.sample_size == 2
    assert report.total_tokens == 120
    assert report.latency.p50_ms == 900
    assert report.requests[0].latency_ms is None
    assert report.requests[0].model is None


async def test_each_request_record_carries_its_own_numbers() -> None:
    repo = FakeRepo([message(index=0, retrieved=4, citations=2, tool_steps=2)])
    report = await build_report(repo)  # type: ignore[arg-type]

    record = report.requests[0]
    assert record.message_id == "m0"
    assert record.retrieval_hit is True
    assert record.chunks_retrieved == 4
    assert record.citations == 2
    assert record.tool_steps == 2
    assert record.preview


# --- tool health -----------------------------------------------------------


def test_tool_health_splits_success_from_failure() -> None:
    health = _to_tool_health(
        {
            "_id": "save_task",
            "total": 10,
            "by_status": [
                {"status": "success", "count": 7, "avg_latency_ms": 20.0},
                {"status": "validation_error", "count": 2, "avg_latency_ms": 1.0},
                {"status": "execution_error", "count": 1, "avg_latency_ms": 5000.0},
            ],
        }
    )

    assert health.tool_name == "save_task"
    assert health.success == 7
    assert health.failure == 3
    assert health.success_rate == 0.7
    # Failure kinds stay distinguishable: a schema problem is not an outage.
    assert health.by_status["validation_error"] == 2
    assert health.by_status["execution_error"] == 1


def test_average_latency_is_weighted_by_call_count() -> None:
    """Averaging the per-status averages would let one call outvote fifty."""
    health = _to_tool_health(
        {
            "_id": "send_notification",
            "total": 11,
            "by_status": [
                {"status": "success", "count": 10, "avg_latency_ms": 100.0},
                {"status": "execution_error", "count": 1, "avg_latency_ms": 8000.0},
            ],
        }
    )

    # Weighted: (10*100 + 1*8000) / 11 = 818. Unweighted would be 4050.
    assert health.avg_latency_ms == 818


def test_a_tool_that_only_ever_failed_reports_a_zero_success_rate() -> None:
    health = _to_tool_health(
        {
            "_id": "unknown_tool",
            "total": 3,
            "by_status": [{"status": "unknown_tool", "count": 3, "avg_latency_ms": 0.0}],
        }
    )
    assert health.success == 0
    assert health.failure == 3
    assert health.success_rate == 0.0


def test_a_tool_with_no_latency_reported_is_not_a_zero() -> None:
    """None and 0ms mean different things; conflating them invents a number."""
    health = _to_tool_health(
        {
            "_id": "list_tasks",
            "total": 1,
            "by_status": [{"status": "success", "count": 1, "avg_latency_ms": None}],
        }
    )
    assert health.avg_latency_ms is None


async def test_tools_are_reported_alongside_recent_failures() -> None:
    failure = ToolCall(
        _id="tc1",
        workspace_id="w1",
        conversation_id="cv1",
        message_id="m1",
        tool_name="delete_everything",
        arguments={},
        result={"error": "no such tool"},
        status="unknown_tool",
        error_message="no such tool",
        latency_ms=1,
        step_index=0,
        created_at=NOW,
    )
    repo = FakeRepo(
        [message(index=0)],
        stats=[
            {
                "_id": "save_task",
                "total": 2,
                "by_status": [{"status": "success", "count": 2, "avg_latency_ms": 10.0}],
            }
        ],
        failures=[failure],
    )
    report = await build_report(repo)  # type: ignore[arg-type]

    assert [t.tool_name for t in report.tools] == ["save_task"]
    assert [f.tool_name for f in report.recent_failures] == ["delete_everything"]
