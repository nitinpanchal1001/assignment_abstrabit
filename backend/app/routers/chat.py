"""Chat: streamed, grounded, workspace-scoped answers."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app import errors
from app.api_models import ChatHistoryOut, ChatIn, ConversationOut, MessageOut
from app.auth.workspace import require_workspace
from app.config import settings
from app.db.repositories import WorkspaceRepository
from app.deps import CurrentUser, WorkspaceDep
from app.limits import SlidingWindowLimiter
from app.rag.agent import run_agent

log = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

#: Keyed by workspace, not by user: the resource being protected is the shared
#: free-tier Gemini quota, which a workspace consumes regardless of which of its
#: members is asking.
_chat_limiter = SlidingWindowLimiter(limit=settings.chat_rate_limit_per_minute, window_seconds=60.0)


@router.get("/history", response_model=ChatHistoryOut)
async def history(ctx: WorkspaceDep) -> ChatHistoryOut:
    """Most recent conversation in this workspace, so a reload resumes it."""
    conversation = await ctx.repo.latest_conversation()

    messages = await ctx.repo.list_messages(conversation.id) if conversation is not None else []

    return ChatHistoryOut(
        conversation=(
            ConversationOut(
                id=conversation.id,
                title=conversation.title,
                messages=[MessageOut.of(m) for m in messages],
            )
            if conversation is not None
            else None
        ),
        document_count=await ctx.repo.count_documents("ready"),
    )


async def _resolve_conversation_id(
    repo: WorkspaceRepository, requested_id: str | None, user_id: str, title: str
) -> str:
    """Return the conversation to append to, creating one when needed.

    A requested id that does not belong to this workspace is treated as absent
    rather than as an error: it is almost always a stale tab, and silently
    starting a fresh conversation in the correct workspace is both friendlier
    and non-disclosing. (The repository could not return it anyway; it is
    scoped.)
    """
    if requested_id:
        existing = await repo.get_conversation(requested_id)
        if existing is not None:
            return existing.id

    created = await repo.create_conversation(title, user_id)
    return created.id


@router.post("")
async def chat(payload: ChatIn, user: CurrentUser) -> StreamingResponse:
    """Stream an answer as SSE.

    Durability: the user's message and an assistant placeholder are both
    written BEFORE the model is called. If the upstream is slow, rate-limited,
    or the connection drops mid-stream, the question is already saved and the
    placeholder is marked failed, so a refresh shows what happened instead of
    losing the turn.
    """
    # Authorisation before any tenant data is touched. `repo` is bound to this
    # workspace, so nothing downstream can reach another.
    ctx = await require_workspace(user, payload.workspace_id)
    repo = ctx.repo

    message = payload.message.strip()
    if not message:
        raise errors.validation("Type a question before sending.")

    # Rate limit AFTER authorising — an unauthenticated caller must not be able
    # to consume a workspace's allowance, or to learn from a 429 that the
    # workspace exists — and BEFORE the writes below, so a throttled request
    # leaves no half-finished turn in the transcript.
    retry_after = _chat_limiter.check(ctx.workspace.id)
    if retry_after is not None:
        log.warning("workspace %s rate limited (retry in %.1fs)", ctx.workspace.id, retry_after)
        raise errors.workspace_rate_limited(retry_after)

    conversation_id = await _resolve_conversation_id(
        repo, payload.conversation_id, user.id, message[:60]
    )

    await repo.insert_message(
        conversation_id=conversation_id, role="user", content=message, status="complete"
    )
    placeholder = await repo.insert_message(
        conversation_id=conversation_id, role="assistant", content="", status="streaming"
    )

    async def event_stream() -> AsyncIterator[bytes]:
        def frame(event: dict) -> bytes:
            return f"data: {json.dumps(event)}\n\n".encode()

        yield frame(
            {"type": "meta", "conversationId": conversation_id, "messageId": placeholder.id}
        )

        try:
            async for event in run_agent(
                repo=repo,
                workspace_name=ctx.workspace.name,
                user_id=user.id,
                conversation_id=conversation_id,
                question=message,
                assistant_message_id=placeholder.id,
            ):
                yield frame(event)
        except Exception as exc:  # noqa: BLE001
            # run_agent handles its own failures; reaching here means the
            # stream itself broke. Classify anyway so the client gets a code.
            app_error = errors.to_app_error(exc)
            log.error("chat stream failed (%s): %s", app_error.code, app_error.log_message)
            yield frame(
                {
                    "type": "error",
                    "code": app_error.code,
                    "message": app_error.user_message,
                    "retryable": app_error.retryable,
                    "messageId": placeholder.id,
                }
            )
        finally:
            yield b"data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # Stops nginx-style reverse proxies buffering the stream into one blob.
            "X-Accel-Buffering": "no",
        },
    )
