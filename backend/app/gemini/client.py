"""Shared Gemini access: the SDK for embeddings, raw HTTP for interactions.

Why the split. The Python SDK types ``interactions.create``'s request as
``Any`` — it is untyped passthrough — so it buys no safety for the chat
endpoint, while the streaming protocol needs event-level handling
(``step.delta`` text, ``arguments_delta`` fragments, ``thought_signature``)
that is easier to get exactly right against the wire format. Embeddings, by
contrast, have a stable typed SDK surface and are used unchanged.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import httpx
from google import genai

from app import errors
from app.config import settings

log = logging.getLogger(__name__)

T = TypeVar("T")

CHAT_MODEL = settings.gemini_chat_model
EMBEDDING_MODEL = settings.gemini_embedding_model
EMBEDDING_DIMENSIONS = settings.embedding_dimensions

API_BASE = "https://generativelanguage.googleapis.com/v1beta"

_genai_client: genai.Client | None = None
_http_client: httpx.AsyncClient | None = None


def genai_client() -> genai.Client:
    global _genai_client
    if _genai_client is None:
        _genai_client = genai.Client(api_key=settings.gemini_api_key)
    return _genai_client


def http_client() -> httpx.AsyncClient:
    """Shared HTTP client, so connections are pooled across requests.

    The API key travels in a header, never as a ``?key=`` query parameter.
    httpx logs the full request URL at INFO, so a key in the query string ends
    up written verbatim into application logs — and from there into any log
    aggregator. A header is not logged.
    """
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            base_url=API_BASE,
            headers={"x-goog-api-key": settings.gemini_api_key},
            # Generous read timeout: a streamed answer with tool steps can take
            # a while before the first byte, and cutting it off mid-thought is
            # worse than waiting.
            timeout=httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0),
        )
    return _http_client


async def close_clients() -> None:
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None


#: Statuses worth retrying: rate limiting and transient server faults.
#: 400/401/403/404 are permanent — retrying them just burns free-tier quota.
_RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


def status_of(error: BaseException) -> int | None:
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code
    status = getattr(error, "status_code", None)
    return status if isinstance(status, int) else None


def is_retryable(error: BaseException) -> bool:
    status = status_of(error)
    if status is not None:
        return status in _RETRYABLE_STATUS
    # Transport faults never reached the server.
    return isinstance(error, httpx.TransportError)


def error_message(error: BaseException) -> str:
    """Extract something a human can act on.

    The API reports failures as ``{"error": {"code", "message", "status"}}``,
    but an httpx error's ``str()`` is just the status line. Without digging the
    message out, a 400 is indistinguishable from any other 400 — which is
    exactly the debugging dead end this avoids.
    """
    if isinstance(error, httpx.HTTPStatusError):
        try:
            body = error.response.json()
            detail = body.get("error", {})
            message = detail.get("message")
            if message:
                status = detail.get("status")
                return f"{message} ({status})" if status else str(message)
        except Exception:  # noqa: BLE001 - body may not be JSON
            text = error.response.text[:300]
            if text:
                return text
    return errors.describe(error)


def to_llm_error(error: BaseException, label: str) -> errors.AppError:
    """Classify an upstream Gemini failure.

    Status alone is not enough: a 400 can mean "your API key is invalid" (an
    operator problem, permanent) or "your request shape is wrong" (a code
    problem). The key case is singled out because it is by far the most common
    first-deploy failure, and a generic message sends people looking in the
    wrong place.
    """
    status = status_of(error)
    detail = f"{label}: {error_message(error)}"

    if status == 429:
        return errors.llm_rate_limited(detail)
    if status is not None and status >= 500:
        return errors.llm_unavailable(detail)
    if status in {400, 401, 403} and re.search(
        r"api[ _-]?key|unauthenticated|permission", error_message(error), re.I
    ):
        return errors.llm_invalid_key(detail)
    if status is None and isinstance(error, httpx.TransportError):
        return errors.llm_unavailable(detail)

    return errors.llm_failed(detail)


async def with_retry[T](
    operation: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    base_delay: float = 0.5,
    rate_limit_delay: float = 4.0,
    label: str = "gemini",
) -> T:
    """Retry with exponential backoff and full jitter.

    Jitter matters more than it looks on a free tier: several embedding batches
    that hit a 429 together would otherwise retry in lockstep and trip the
    limit again at exactly the same moment.
    """
    last_error: BaseException | None = None
    made = 0

    for attempt in range(attempts):
        made = attempt + 1
        try:
            return await operation()
        except Exception as exc:  # noqa: BLE001 - classified below
            last_error = exc

            if not is_retryable(exc) or attempt == attempts - 1:
                break

            # A 429 on the free tier is a per-minute quota, not congestion, so
            # retrying in milliseconds just burns the remaining allowance.
            rate_limited = status_of(exc) == 429
            base = rate_limit_delay if rate_limited else base_delay
            ceiling = base * (2**attempt)
            delay = base + random.random() * ceiling if rate_limited else random.random() * ceiling
            await asyncio.sleep(delay)

    assert last_error is not None
    # `made`, not `attempts`: a non-retryable error breaks out after one try,
    # and reporting the configured maximum makes it look like three failures.
    raise to_llm_error(last_error, f"{label} failed after {made} attempt(s)")


def sse_params() -> dict[str, Any]:
    """Query params for a streamed call. Deliberately carries no credential."""
    return {"alt": "sse"}
