"""Per-tenant rate limiting for the expensive path.

WHY THIS EXISTS
---------------
Every workspace in this deployment shares ONE free-tier Gemini quota. A single
busy tenant — or a script, or a reviewer holding down Enter — can exhaust it and
the failure lands on everybody else, as a 429 they did nothing to cause. The
isolation work elsewhere in this codebase keeps tenants from reading each
other's data; this keeps them from consuming each other's capacity, which is the
same boundary viewed from a different side.

WHAT IT IS NOT
--------------
The window lives in process memory. With more than one API instance the
effective limit multiplies by the instance count, and a restart forgets
everything. That is a deliberate trade for a deployment that runs one container:
a shared counter means Redis, which means another service, another credential
and another thing that can be down — and *its* outage would take the chat path
down with it. Should this ever scale horizontally, the fix is to move
:class:`SlidingWindowLimiter` behind the same interface backed by Redis; nothing
that calls it would change.

CONCURRENCY
-----------
:meth:`SlidingWindowLimiter.check` contains no ``await``. Under asyncio that
makes it atomic with respect to other requests — the event loop cannot interleave
another coroutine partway through — so the read-modify-write on the deque needs
no lock.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque


class SlidingWindowLimiter:
    """Allow ``limit`` events per ``window_seconds``, per key.

    A true sliding window rather than a fixed bucket: a fixed bucket lets a
    caller spend the whole allowance at 11:59:59 and the whole next allowance at
    12:00:00, which is exactly the burst the limit exists to prevent.
    """

    def __init__(self, *, limit: int, window_seconds: float) -> None:
        self._limit = limit
        self._window = window_seconds
        self._events: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> float | None:
        """Record an allowed event, or return seconds until one is allowed.

        Returns ``None`` when the caller may proceed. When rate limited, returns
        the retry-after delay and records nothing — a rejected request must not
        extend its own penalty, or a client that retries promptly would never
        recover.
        """
        now = time.monotonic()
        cutoff = now - self._window

        events = self._events[key]
        while events and events[0] <= cutoff:
            events.popleft()

        if len(events) >= self._limit:
            # The oldest event is the one whose expiry frees a slot.
            return max(0.0, events[0] - cutoff)

        events.append(now)
        return None

    def prune(self) -> None:
        """Drop keys with no events inside the window.

        Keys go quiet permanently — a workspace is deleted, a demo ends — and
        the empty deque left behind for each is a slow leak in a long-lived
        process. Not called on the request path, where it would add a full scan
        to every chat turn to reclaim a few hundred bytes; called by the caller
        that owns the limiter, on whatever schedule suits it.
        """
        cutoff = time.monotonic() - self._window
        for key in list(self._events):
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if not events:
                del self._events[key]
