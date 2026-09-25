"""The application's error vocabulary.

Every failure that can reach a user becomes an :class:`AppError` carrying four
things:

``code``
    A stable machine-readable identifier, so clients and logs can branch on the
    failure without string-matching prose.
``status``
    The HTTP status to return.
``user_message``
    Text that is safe to show AND actually useful — it says what went wrong
    and, where possible, what to do about it.
``log_message``
    The full technical detail, which stays server-side.

The split between ``user_message`` and ``log_message`` is the important part.
Driver and upstream errors routinely embed connection strings, internal
hostnames and row contents, so they cannot be forwarded verbatim; but replacing
them all with "Something went wrong" makes the product unusable. Every code
below therefore has a hand-written user message.
"""

from __future__ import annotations

import re
import secrets
from typing import Any


class AppError(Exception):
    """A failure with a user-safe message and separate technical detail."""

    def __init__(
        self,
        *,
        code: str,
        status: int,
        user_message: str,
        log_message: str | None = None,
        details: Any = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(log_message or user_message)
        self.code = code
        self.status = status
        self.user_message = user_message
        self.log_message = log_message or user_message
        self.details = details
        self.retryable = retryable


# ---------------------------------------------------------------------------
# Constructors, so the message for a given failure is written exactly once.
# ---------------------------------------------------------------------------


def not_signed_in() -> AppError:
    return AppError(
        code="auth/not-signed-in",
        status=401,
        user_message="You need to sign in to continue.",
    )


def invalid_credentials() -> AppError:
    return AppError(
        code="auth/invalid-credentials",
        status=401,
        # Deliberately identical whether or not the email exists.
        user_message="Incorrect email or password.",
    )


def email_taken() -> AppError:
    return AppError(
        code="auth/email-taken",
        status=409,
        user_message="An account with that email already exists. Try signing in instead.",
    )


def workspace_not_found() -> AppError:
    return AppError(
        code="workspace/not-found",
        status=404,
        # Same response whether it does not exist or the caller is not a
        # member, so workspace ids cannot be probed for existence.
        user_message="That workspace doesn't exist, or you don't have access to it.",
    )


def invalid_workspace_name(reason: str) -> AppError:
    return AppError(code="workspace/invalid-name", status=400, user_message=reason)


def document_not_found() -> AppError:
    return AppError(
        code="document/not-found",
        status=404,
        user_message="That document is not in this workspace. It may have already been deleted.",
    )


def share_not_found() -> AppError:
    return AppError(
        code="share/not-found",
        status=404,
        user_message="That workspace does not currently have access to this document.",
    )


def document_too_large(filename: str, size: int, limit: int) -> AppError:
    return AppError(
        code="document/too-large",
        status=413,
        user_message=(
            f'"{filename}" is {size / 1024 / 1024:.1f} MB, which is over the '
            f"{limit / 1024 / 1024:.0f} MB limit. Try splitting it into smaller files."
        ),
    )


def unsupported_file_type(extension: str, supported: list[str]) -> AppError:
    return AppError(
        code="document/unsupported-type",
        status=415,
        user_message=(
            f'Files of type "{extension or "unknown"}" can\'t be read. '
            f"Supported formats: {', '.join(sorted(supported))}."
        ),
    )


def empty_document(filename: str) -> AppError:
    return AppError(
        code="document/empty",
        status=400,
        user_message=f'"{filename}" is empty, so there is nothing to ingest.',
    )


def no_extractable_text(filename: str) -> AppError:
    return AppError(
        code="document/no-text",
        status=422,
        user_message=(
            f'No readable text could be extracted from "{filename}". '
            "Scanned or image-only PDFs need OCR, which this app does not perform."
        ),
    )


def ingest_failed(filename: str, detail: str) -> AppError:
    return AppError(
        code="document/ingest-failed",
        status=422,
        user_message=(
            f'"{filename}" could not be processed. Nothing was saved, so you can try again.'
        ),
        log_message=f"Ingest failed for {filename}: {detail}",
    )


def llm_rate_limited(detail: str | None = None) -> AppError:
    return AppError(
        code="llm/rate-limited",
        status=429,
        user_message=(
            "The AI service is rate limited right now. Your question was saved — "
            "please try again in a minute."
        ),
        log_message=detail,
        retryable=True,
    )


def workspace_rate_limited(retry_after_seconds: float) -> AppError:
    """This workspace is asking too fast — distinct from the upstream's 429.

    Kept separate from :func:`llm_rate_limited` on purpose: that one means
    Google throttled us and waiting is all anyone can do, this one means we
    throttled the caller to protect the other tenants. Same status code, but a
    user reading the message should be able to tell which happened.
    """
    wait = max(1, round(retry_after_seconds))
    return AppError(
        code="workspace/rate-limited",
        status=429,
        user_message=(
            f"This workspace is sending questions faster than its share of the shared "
            f"AI quota allows. Try again in about {wait} second{'s' if wait != 1 else ''}."
        ),
        retryable=True,
    )


def llm_invalid_key(detail: str | None = None) -> AppError:
    return AppError(
        code="llm/invalid-key",
        status=502,
        user_message=(
            "The AI service rejected this deployment's credentials. "
            "An administrator needs to check GEMINI_API_KEY."
        ),
        log_message=detail,
    )


def llm_unavailable(detail: str | None = None) -> AppError:
    return AppError(
        code="llm/unavailable",
        status=503,
        user_message=(
            "The AI service is temporarily unavailable. Your question was saved — please try again."
        ),
        log_message=detail,
        retryable=True,
    )


def llm_failed(detail: str | None = None) -> AppError:
    return AppError(
        code="llm/failed",
        status=502,
        user_message=(
            "The AI service could not complete this request. "
            "Your question was saved — please try again."
        ),
        log_message=detail,
    )


def vector_not_provisioned(detail: str | None = None) -> AppError:
    return AppError(
        code="vector/not-provisioned",
        status=503,
        user_message=(
            "The vector store has not been set up for this deployment yet. "
            "An administrator needs to run the migration."
        ),
        log_message=detail,
    )


def vector_unavailable(detail: str | None = None) -> AppError:
    return AppError(
        code="vector/unavailable",
        status=503,
        user_message="The search index is unreachable right now. Please try again in a moment.",
        log_message=detail,
        retryable=True,
    )


def isolation_violation(detail: str) -> AppError:
    return AppError(
        code="vector/isolation-violation",
        status=500,
        user_message=(
            "A workspace isolation check failed and the request was stopped. "
            "This has been logged and no data was returned."
        ),
        log_message=detail,
    )


def database_unavailable(detail: str | None = None) -> AppError:
    return AppError(
        code="db/unavailable",
        status=503,
        user_message="The database is unreachable right now. Please try again in a moment.",
        log_message=detail,
        retryable=True,
    )


def database_auth_failed(detail: str | None = None) -> AppError:
    return AppError(
        code="db/auth-failed",
        status=503,
        user_message=(
            "This deployment cannot authenticate with its database. "
            "An administrator needs to check MONGODB_URI."
        ),
        log_message=detail,
    )


def conflict(user_message: str, detail: str | None = None) -> AppError:
    return AppError(code="db/conflict", status=409, user_message=user_message, log_message=detail)


def validation(user_message: str, details: Any = None) -> AppError:
    return AppError(
        code="validation/invalid", status=400, user_message=user_message, details=details
    )


def internal(detail: str | None = None) -> AppError:
    return AppError(
        code="internal",
        status=500,
        user_message="Something went wrong on our end. Please try again.",
        log_message=detail,
    )


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def describe(error: BaseException | str | None) -> str:
    """Best-effort readable description. Never raises."""
    if error is None:
        return "Unknown error"
    if isinstance(error, str):
        return error
    text = str(error)
    return text if text else error.__class__.__name__


def to_app_error(error: BaseException) -> AppError:
    """Turn anything raised anywhere into an AppError.

    Driver errors are recognised by exception type where possible, because
    messages change between versions. Where a message test is unavoidable it is
    kept narrow.
    """
    if isinstance(error, AppError):
        return error

    detail = describe(error)
    name = error.__class__.__name__

    # --- MongoDB -----------------------------------------------------------
    if name == "DuplicateKeyError":
        return conflict("That already exists.", detail)

    if name in {
        "ServerSelectionTimeoutError",
        "NetworkTimeout",
        "AutoReconnect",
        "ConnectionFailure",
    }:
        return database_unavailable(detail)

    if name == "OperationFailure" and re.search(r"bad auth|authentication failed", detail, re.I):
        return database_auth_failed(detail)

    # --- Qdrant ------------------------------------------------------------
    # UnexpectedResponse carries the HTTP status code.
    status = getattr(error, "status_code", None)
    if isinstance(status, int):
        if status == 404 and re.search(r"collection", detail, re.I):
            return vector_not_provisioned(detail)
        if status in {401, 403}:
            return AppError(
                code="vector/unavailable",
                status=503,
                user_message=(
                    "This deployment cannot authenticate with its search index. "
                    "An administrator needs to check QDRANT_API_KEY."
                ),
                log_message=detail,
            )
        if status >= 500 or status == 429:
            return vector_unavailable(detail)

    if name in {"ResponseHandlingException", "ConnectError", "ConnectTimeout", "ReadTimeout"}:
        return vector_unavailable(detail)

    return internal(f"{name}: {detail}")


def new_request_id() -> str:
    """Short id tying a user-visible failure to a server log line."""
    return secrets.token_hex(4)
