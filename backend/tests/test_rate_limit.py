"""The per-workspace limiter protecting the shared free-tier quota.

Time is injected via monkeypatched ``time.monotonic`` rather than slept through,
so the window behaviour is asserted exactly and the suite stays fast.
"""

from __future__ import annotations

import pytest

from app import errors
from app.limits import SlidingWindowLimiter


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch):
    """A controllable monotonic clock."""

    class Clock:
        now = 1000.0

        def advance(self, seconds: float) -> None:
            self.now += seconds

    c = Clock()
    monkeypatch.setattr("app.limits.time.monotonic", lambda: c.now)
    return c


def test_requests_under_the_limit_are_allowed(clock) -> None:
    limiter = SlidingWindowLimiter(limit=3, window_seconds=60.0)
    assert [limiter.check("w1") for _ in range(3)] == [None, None, None]


def test_the_request_over_the_limit_is_refused(clock) -> None:
    limiter = SlidingWindowLimiter(limit=3, window_seconds=60.0)
    for _ in range(3):
        limiter.check("w1")

    retry_after = limiter.check("w1")
    assert retry_after is not None
    assert retry_after == pytest.approx(60.0)


def test_workspaces_have_independent_allowances(clock) -> None:
    """The whole point: one tenant exhausting its share must not touch another."""
    limiter = SlidingWindowLimiter(limit=2, window_seconds=60.0)
    limiter.check("northwind")
    limiter.check("northwind")

    assert limiter.check("northwind") is not None
    assert limiter.check("meridian") is None


def test_the_window_slides_rather_than_resetting(clock) -> None:
    """A fixed bucket would allow a double burst across the boundary.

    Spend the allowance at t=0, and a fixed-window limiter refills everything at
    t=60 — letting a caller send 2x the limit back to back. Here each slot frees
    up on its own schedule.
    """
    limiter = SlidingWindowLimiter(limit=2, window_seconds=60.0)
    limiter.check("w1")  # t = 0
    clock.advance(30)
    limiter.check("w1")  # t = 30
    assert limiter.check("w1") is not None

    # At t=61 only the first event has aged out, so exactly one slot is free.
    clock.advance(31)
    assert limiter.check("w1") is None
    assert limiter.check("w1") is not None


def test_a_refused_request_does_not_extend_its_own_penalty(clock) -> None:
    """Otherwise a client that retries promptly could never recover."""
    limiter = SlidingWindowLimiter(limit=1, window_seconds=60.0)
    limiter.check("w1")

    for _ in range(10):
        clock.advance(1)
        assert limiter.check("w1") is not None

    # The single recorded event still ages out on its original schedule.
    clock.advance(51)
    assert limiter.check("w1") is None


def test_retry_after_shrinks_as_the_window_advances(clock) -> None:
    limiter = SlidingWindowLimiter(limit=1, window_seconds=60.0)
    limiter.check("w1")

    first = limiter.check("w1")
    clock.advance(45)
    second = limiter.check("w1")

    assert first is not None and second is not None
    assert second < first
    assert second == pytest.approx(15.0)


def test_prune_reclaims_idle_keys(clock) -> None:
    limiter = SlidingWindowLimiter(limit=5, window_seconds=60.0)
    for i in range(100):
        limiter.check(f"workspace-{i}")

    clock.advance(61)
    limiter.prune()
    assert limiter._events == {}


def test_prune_keeps_keys_still_inside_the_window(clock) -> None:
    limiter = SlidingWindowLimiter(limit=5, window_seconds=60.0)
    limiter.check("old")
    clock.advance(61)
    limiter.check("recent")

    limiter.prune()
    assert set(limiter._events) == {"recent"}


# --- the error the route raises --------------------------------------------


def test_the_error_is_distinguishable_from_an_upstream_429() -> None:
    """Same status, different cause — a user should be able to tell which."""
    ours = errors.workspace_rate_limited(12.0)
    upstream = errors.llm_rate_limited()

    assert ours.status == upstream.status == 429
    assert ours.code != upstream.code
    assert ours.code == "workspace/rate-limited"
    assert ours.retryable


def test_the_error_tells_the_user_how_long_to_wait() -> None:
    assert "12 seconds" in errors.workspace_rate_limited(12.0).user_message
    assert "1 second" in errors.workspace_rate_limited(0.4).user_message
    assert "1 seconds" not in errors.workspace_rate_limited(0.4).user_message


def test_the_error_leaks_nothing_about_other_tenants() -> None:
    message = errors.workspace_rate_limited(30.0).user_message
    assert "workspace" in message.lower()
    for leak in ("mongodb", "qdrant", "gemini", "api key", "http"):
        assert leak not in message.lower()
