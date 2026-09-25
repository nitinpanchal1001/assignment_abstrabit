"""FastAPI application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import errors
from app.config import settings
from app.db.mongo import close_client
from app.gemini.client import close_clients
from app.routers import auth, chat, documents, workspaces
from app.vector.qdrant import close_client as close_qdrant

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
# httpx/httpcore log every request URL at INFO. Even with the credential moved
# to a header, that is noise that buries the application's own lines.
for _noisy in ("httpx", "httpcore", "google_genai"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Connections are created lazily on first use and torn down here, so a
    # reload or a container stop does not leak sockets.
    log.info("starting (environment=%s)", settings.environment)
    yield
    await close_client()
    await close_qdrant()
    await close_clients()
    log.info("stopped")


app = FastAPI(
    title="Groundwork API",
    version="1.0.0",
    description=(
        "Workspace-scoped RAG: grounded retrieval with citations, tool calling, "
        "and strict tenant isolation over a single shared vector store."
    ),
    lifespan=lifespan,
    # Docs are genuinely useful for a reviewer, so they stay on.
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

# Normally empty: the Next.js frontend proxies /api/* to this service, so
# requests are same-origin. Populated only for a split deployment.
if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


# ---------------------------------------------------------------------------
# Error handling
#
# Every failure leaves through one of these handlers, so the response shape is
# identical whatever went wrong:
#
#     { "error": { "code", "message", "requestId", "details"? } }
#
# The same requestId is logged server-side, so a user can quote eight
# characters and the exact failure becomes findable.
# ---------------------------------------------------------------------------


def _error_response(app_error: errors.AppError, context: str) -> JSONResponse:
    request_id = errors.new_request_id()

    # 5xx means we broke; 4xx usually means the caller did. Both are logged,
    # but only the former warrants an error-level line.
    logger = log.error if app_error.status >= 500 else log.warning
    logger("[%s] %s %s: %s", context, request_id, app_error.code, app_error.log_message)

    body: dict = {
        "error": {
            "code": app_error.code,
            "message": app_error.user_message,
            "requestId": request_id,
        }
    }
    if app_error.details is not None:
        body["error"]["details"] = app_error.details

    return JSONResponse(status_code=app_error.status, content=body)


@app.exception_handler(errors.AppError)
async def handle_app_error(request: Request, exc: errors.AppError) -> JSONResponse:
    return _error_response(exc, request.url.path)


@app.exception_handler(RequestValidationError)
async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Report every invalid field at once.

    Returning only the first means a form with two mistakes takes two attempts
    to fix.
    """
    issues = [
        {
            # Drop the leading "body"/"query" segment: the client knows where
            # it sent the value, and "body.email" reads worse than "email".
            "field": ".".join(str(p) for p in err["loc"][1:]) or "(request)",
            "message": err["msg"],
        }
        for err in exc.errors()[:8]
    ]

    summary = (
        issues[0]["message"]
        if len(issues) == 1
        else f"{len(issues)} fields are invalid: {', '.join(i['field'] for i in issues)}."
    )

    return _error_response(errors.validation(summary, issues), request.url.path)


@app.exception_handler(StarletteHTTPException)
async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    mapped = errors.AppError(
        code="not-found" if exc.status_code == 404 else "internal",
        status=exc.status_code,
        user_message=str(exc.detail) if exc.detail else "That request could not be completed.",
    )
    return _error_response(mapped, request.url.path)


@app.exception_handler(Exception)
async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all.

    Nothing escapes as a raw stack trace: an unclassified exception becomes a
    generic 500 for the client while the full detail goes to the log.
    """
    log.exception("unhandled exception on %s", request.url.path)
    return _error_response(errors.to_app_error(exc), request.url.path)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

app.include_router(auth.router, prefix="/api")
app.include_router(workspaces.router, prefix="/api")
app.include_router(documents.router, prefix="/api")
app.include_router(chat.router, prefix="/api")


@app.get("/api/health", tags=["meta"])
async def health() -> dict[str, str]:
    """Liveness probe. Deliberately touches no dependency.

    A health check that queries the database turns a slow database into a
    failed deploy; readiness of dependencies is reported where it is used.
    """
    return {"status": "ok", "environment": settings.environment}
