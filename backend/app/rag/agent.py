"""The retrieval + tool-calling loop.

Emitted as an async generator of events so the route handler can stream them as
SSE without this module knowing anything about HTTP responses.

Shape of a turn::

    retrieve (workspace-scoped)
      -> model turn (streamed)
         -> if the model requested tools: validate, execute, feed results back
         -> repeat, up to MAX_STEPS
      -> persist the assistant message with citations, retrieval debug, usage

Multi-step is genuinely supported: the model can call ``list_tasks``, read the
result, and then decide to call ``save_task`` before answering.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx

from app import errors
from app.api_models import CitationOut, RetrievalDebugOut, UsageOut, wire
from app.db.repositories import WorkspaceRepository
from app.db.schema import Citation, UsageStats
from app.gemini.client import (
    CHAT_MODEL,
    http_client,
    is_retryable,
    sse_params,
    status_of,
    to_llm_error,
)
from app.rag.prompt import build_system_prompt, build_user_turn
from app.rag.retrieve import retrieve_chunks
from app.tools.execute import execute_tool_call
from app.tools.registry import MUTATING_TOOLS, ToolContext, tool_declarations

log = logging.getLogger(__name__)

#: Model turns per user question. 4 allows tool -> result -> tool -> answer.
MAX_STEPS = 4
#: Cap on state-changing tool calls in a single turn, to bound blast radius.
MAX_MUTATIONS = 3

CITATION_PATTERN = __import__("re").compile(r"\[(\d{1,2})\]")


@dataclass
class _StreamedStep:
    """One step as it is being assembled from the stream."""

    index: int
    kind: str  # 'thought' | 'function_call' | 'model_output'
    signature: str | None = None
    call_id: str | None = None
    name: str | None = None
    arguments_text: str = ""
    text: str = ""


@dataclass
class _ModelTurn:
    text: str = ""
    function_calls: list[dict[str, Any]] = field(default_factory=list)
    #: The model's own steps, in emission order, ready to append to history
    #: verbatim. Includes ``thought`` steps and their signatures.
    replay_steps: list[dict[str, Any]] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0


async def run_agent(
    *,
    repo: WorkspaceRepository,
    workspace_name: str,
    user_id: str,
    conversation_id: str,
    question: str,
    assistant_message_id: str,
) -> AsyncIterator[dict[str, Any]]:
    started = time.monotonic()
    workspace_id = repo.workspace_id

    tool_context = ToolContext(
        repo=repo,
        workspace_id=workspace_id,
        workspace_name=workspace_name,
        user_id=user_id,
        conversation_id=conversation_id,
    )

    answer = ""
    tool_steps = 0
    mutations = 0
    input_tokens = 0
    output_tokens = 0
    citations: list[Citation] = []

    try:
        # --- Retrieval -----------------------------------------------------
        yield {"type": "status", "stage": "retrieving"}

        retrieval = await retrieve_chunks(repo=repo, query=question)
        citations = retrieval.citations

        yield {
            "type": "retrieval",
            # Through the wire models, not the storage models: SSE frames skip
            # FastAPI's response_model, so this is the only thing keeping the
            # stream's field names identical to those of GET /chat/history.
            "debug": wire(RetrievalDebugOut.of(retrieval.debug)),
            "citations": [wire(CitationOut.of(c)) for c in citations],
        }

        system_prompt = build_system_prompt(
            workspace_name=workspace_name, has_context=bool(retrieval.chunks)
        )

        # Stateless: we own the history and Google stores nothing. Keeping the
        # transcript in our database rather than a provider-side session is what
        # makes tenant data auditable and deletable on our terms.
        #
        # Each content part carries its own `type` discriminant; the API rejects
        # a bare {text} with "The 'type' parameter is required".
        history: list[dict[str, Any]] = [
            {
                "type": "user_input",
                "content": [{"type": "text", "text": build_user_turn(question, retrieval.chunks)}],
            }
        ]

        tools = tool_declarations()

        for step in range(MAX_STEPS):
            yield {"type": "status", "stage": "thinking" if step == 0 else "tools"}

            separator = "\n\n" if answer else ""
            streamed_text = ""
            turn = _ModelTurn()

            async for event in _stream_model_turn(history, system_prompt, tools, turn):
                delta = separator + event
                separator = ""
                streamed_text += event
                answer += delta
                yield {"type": "token", "text": delta}

            input_tokens += turn.input_tokens
            output_tokens += turn.output_tokens

            # Fallback: if the stream carried no text deltas but the completed
            # interaction did contain prose, emit it so it is never lost.
            if not streamed_text and turn.text:
                delta = ("\n\n" if answer else "") + turn.text
                answer += delta
                yield {"type": "token", "text": delta}

            if not turn.function_calls:
                break

            # Replay the model's own steps verbatim, in emission order, before
            # appending any results.
            #
            # This MUST include `thought` steps and their signatures. Gemini 3
            # reasoning models reject a follow-up turn containing a
            # function_call without the thought signature that preceded it —
            # verified against the live API, where omitting it returns a bare
            # "400 Request contains an invalid argument". Reconstructing the
            # steps by hand is what dropped them; replaying what actually
            # arrived cannot.
            history.extend(turn.replay_steps)

            for call in turn.function_calls:
                yield {
                    "type": "tool_call",
                    "name": call["name"],
                    "args": call["arguments"],
                    "stepIndex": tool_steps,
                }

                is_mutating = call["name"] in MUTATING_TOOLS
                if is_mutating and mutations >= MAX_MUTATIONS:
                    history.append(
                        {
                            "type": "function_result",
                            "call_id": call["id"],
                            "name": call["name"],
                            "is_error": True,
                            "result": json.dumps(
                                {
                                    "error": (
                                        f"Refused: this turn already performed {MAX_MUTATIONS} "
                                        "state-changing tool calls. Summarise what you have "
                                        "done and stop."
                                    )
                                }
                            ),
                        }
                    )
                    yield {
                        "type": "tool_result",
                        "name": call["name"],
                        "status": "execution_error",
                        "isError": True,
                        "latencyMs": 0,
                        "stepIndex": tool_steps,
                    }
                    tool_steps += 1
                    continue

                execution = await execute_tool_call(
                    name=call["name"],
                    arguments=call["arguments"],
                    context=tool_context,
                    step_index=tool_steps,
                    message_id=assistant_message_id,
                )

                if is_mutating and execution.status == "success":
                    mutations += 1

                history.append(
                    {
                        "type": "function_result",
                        "call_id": call["id"],
                        "name": call["name"],
                        "is_error": execution.is_error,
                        "result": json.dumps(execution.payload),
                    }
                )

                yield {
                    "type": "tool_result",
                    "name": execution.name,
                    "status": execution.status,
                    "isError": execution.is_error,
                    "latencyMs": execution.latency_ms,
                    "stepIndex": tool_steps,
                }

                tool_steps += 1

        # --- Persist -------------------------------------------------------
        yield {"type": "status", "stage": "saving"}

        if not answer.strip():
            answer = "I was not able to produce an answer for that. Please try rephrasing."

        used = _filter_used_citations(answer, citations)

        usage = UsageStats(
            model=CHAT_MODEL,
            input_tokens=input_tokens or None,
            output_tokens=output_tokens or None,
            total_tokens=(input_tokens + output_tokens) or None,
            latency_ms=int((time.monotonic() - started) * 1000),
            tool_steps=tool_steps,
        )

        await repo.complete_message(
            assistant_message_id,
            content=answer,
            citations=used,
            retrieval=retrieval.debug,
            usage=usage,
        )
        await repo.touch_conversation(conversation_id)

        yield {
            "type": "done",
            "messageId": assistant_message_id,
            "usage": wire(UsageOut.of(usage)),
            "citations": [wire(CitationOut.of(c)) for c in used],
        }

    except Exception as exc:  # noqa: BLE001 - classified below
        # Everything reaching here is already classified — rate limit, bad key,
        # vector store down, isolation violation — so the user gets the specific
        # message rather than a guess derived from string-matching.
        app_error = errors.to_app_error(exc)
        log.error("agent %s: %s", app_error.code, app_error.log_message[:300])

        # The user's question is already persisted and the assistant row
        # exists. Marking it failed means a refresh shows what happened
        # instead of a gap.
        try:
            await repo.fail_message(assistant_message_id, app_error.user_message, answer or "")
        except Exception:  # noqa: BLE001
            log.exception("could not mark message failed")

        yield {
            "type": "error",
            "code": app_error.code,
            "message": app_error.user_message,
            "retryable": app_error.retryable,
            "messageId": assistant_message_id,
        }


async def _stream_model_turn(
    history: list[dict[str, Any]],
    system_prompt: str,
    tools: list[dict[str, Any]],
    turn: _ModelTurn,
) -> AsyncIterator[str]:
    """Stream one model turn, yielding text deltas and filling ``turn`` in place.

    The API streams a typed SSE protocol — ``step.start`` / ``step.delta`` /
    ``step.stop`` / ``interaction.completed`` — rather than successive snapshots
    of the interaction. Two consequences shape this function:

    - Text arrives only as ``step.delta`` events of type ``text``. These are
      true increments, so they are appended, never diffed.
    - Function calls must be assembled from the stream: ``step.start``
      announces the call (id, name) and ``step.delta`` events of type
      ``arguments_delta`` carry the arguments as JSON *string fragments*.
      ``interaction.completed`` arrives with an EMPTY steps array when
      streaming, so it cannot be used as the source.
    """
    payload = {
        "model": CHAT_MODEL,
        "store": False,
        "system_instruction": system_prompt,
        "input": history,
        "tools": tools,
        "stream": True,
    }

    steps: dict[int, _StreamedStep] = {}
    completed_text = ""
    stream_error: str | None = None

    client = http_client()

    # Retry only the connection/status phase: once bytes are flowing the turn
    # cannot be replayed safely.
    attempts = 3
    response_cm = None
    for attempt in range(attempts):
        candidate = client.stream("POST", "/interactions", params=sse_params(), json=payload)
        response = await candidate.__aenter__()

        if response.status_code < 400:
            response_cm = (candidate, response)
            break

        await response.aread()
        error = httpx.HTTPStatusError(
            f"HTTP {response.status_code}", request=response.request, response=response
        )
        await candidate.__aexit__(type(error), error, error.__traceback__)

        if not is_retryable(error) or attempt == attempts - 1:
            raise to_llm_error(error, f"interactions.create failed after {attempt + 1} attempt(s)")

        rate_limited = status_of(error) == 429
        base = 4.0 if rate_limited else 0.5
        ceiling = base * (2**attempt)
        await asyncio.sleep(
            base + random.random() * ceiling if rate_limited else random.random() * ceiling
        )

    assert response_cm is not None
    candidate, response = response_cm

    try:
        async for line in response.aiter_lines():
            if not line.startswith("data:"):
                continue

            raw = line[5:].strip()
            if not raw or raw == "[DONE]":
                continue

            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue

            event_type = event.get("event_type")

            if event_type == "step.start":
                step = event.get("step") or {}
                index = event.get("index", 0)
                kind = step.get("type")
                if kind == "function_call" and step.get("name"):
                    steps[index] = _StreamedStep(
                        index=index,
                        kind="function_call",
                        call_id=step.get("id") or f"call_{index}",
                        name=step["name"],
                    )
                elif kind == "thought":
                    steps[index] = _StreamedStep(index=index, kind="thought")
                elif kind == "model_output":
                    steps[index] = _StreamedStep(index=index, kind="model_output")

            elif event_type == "step.delta":
                delta = event.get("delta") or {}
                index = event.get("index", 0)
                step = steps.get(index)
                delta_type = delta.get("type")

                if delta_type == "text" and delta.get("text"):
                    if step:
                        step.text += delta["text"]
                    yield delta["text"]
                elif delta_type == "arguments_delta" and delta.get("arguments"):
                    if step:
                        step.arguments_text += delta["arguments"]
                elif delta_type == "thought_signature" and delta.get("signature"):
                    # The signature that must be echoed back on the next turn.
                    if step:
                        step.signature = delta["signature"]

            elif event_type == "step.stop":
                usage = event.get("usage") or event.get("step_usage") or {}
                turn.input_tokens = usage.get("total_input_tokens", turn.input_tokens)
                turn.output_tokens = usage.get("total_output_tokens", turn.output_tokens)

            elif event_type == "interaction.completed":
                interaction = event.get("interaction") or {}
                usage = interaction.get("usage") or {}
                turn.input_tokens = usage.get("total_input_tokens", turn.input_tokens)
                turn.output_tokens = usage.get("total_output_tokens", turn.output_tokens)

                # Empty while streaming; populated only for a non-streamed call.
                for raw_step in interaction.get("steps") or []:
                    if raw_step.get("type") == "model_output":
                        for part in raw_step.get("content") or []:
                            if isinstance(part, dict) and part.get("text"):
                                completed_text += part["text"]

            elif event_type == "error":
                stream_error = (event.get("error") or {}).get("message") or "Model stream error."
    finally:
        await candidate.__aexit__(None, None, None)

    ordered = [steps[i] for i in sorted(steps)]

    turn.function_calls = [
        {
            "id": s.call_id,
            "name": s.name,
            "arguments": _parse_arguments(s.arguments_text, s.name or "?"),
        }
        for s in ordered
        if s.kind == "function_call"
    ]

    replay: list[dict[str, Any]] = []
    for s in ordered:
        if s.kind == "thought":
            # A thought step is only meaningful with its signature; one without
            # is dropped rather than sent as an empty step.
            if s.signature:
                replay.append({"type": "thought", "signature": s.signature})
        elif s.kind == "function_call":
            replay.append(
                {
                    "type": "function_call",
                    "id": s.call_id,
                    "name": s.name,
                    "arguments": _parse_arguments(s.arguments_text, s.name or "?"),
                }
            )
        elif s.text:
            replay.append({"type": "model_output", "content": [{"type": "text", "text": s.text}]})

    turn.replay_steps = replay

    streamed_any_text = any(s.text for s in ordered)
    turn.text = (
        "".join(s.text for s in ordered if s.kind == "model_output").strip()
        if streamed_any_text
        else completed_text.strip()
    )

    # Only fatal if the stream produced nothing usable. A late error after a
    # complete answer should not discard that answer.
    if stream_error and not turn.text and not turn.function_calls:
        raise errors.llm_failed(stream_error)


def _parse_arguments(text: str, tool_name: str) -> dict[str, Any]:
    """Parse accumulated argument fragments.

    Returns ``{}`` rather than raising on malformed JSON: an empty object then
    fails the tool's schema, which surfaces to the model as a structured
    validation error it can correct on the next step. Raising here would fail
    the whole turn over a truncated fragment.
    """
    trimmed = text.strip()
    if not trimmed:
        return {}
    try:
        parsed = json.loads(trimmed)
    except json.JSONDecodeError:
        log.warning("could not parse streamed arguments for %s: %s", tool_name, trimmed[:120])
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _filter_used_citations(answer: str, citations: list[Citation]) -> list[Citation]:
    """Keep only the citations the answer actually cites — and none otherwise.

    This used to fall back to returning every retrieved chunk when the answer
    cited nothing, which attached a full source list to "the documents don't
    cover that". Sources under a refusal read as evidence FOR an answer that
    was never given, which is the opposite of what a citation is for.

    Retrieval transparency is not lost by returning nothing here: everything
    that was searched and returned is still on the message's retrieval trace,
    which is where "what did it look at" belongs. This field answers the
    narrower question "what did it use".
    """
    referenced = {int(m) for m in CITATION_PATTERN.findall(answer)}
    return [c for c in citations if c.index in referenced]
