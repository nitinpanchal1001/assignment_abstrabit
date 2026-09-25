"""The retrieval + tool-calling loop, driven by a scripted model.

``_stream_model_turn`` is replaced with a fixture that plays back a list of
turns, so the loop's own behaviour is under test rather than Gemini's. What
matters here is the part that is genuinely hard to get right and silently easy
to break:

- a tool result is fed BACK into the history, so the model's next turn can see
  what the tool returned — that is what makes a second, dependent call possible
  at all;
- the model's own steps are replayed verbatim, thought signatures included,
  because the API rejects a follow-up turn whose function_call has lost the
  thought that preceded it;
- the loop terminates, and the caps on steps and on state-changing calls hold.

A regression in any of these looks like "the model stopped chaining tools",
which is indistinguishable from the model simply choosing not to.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.db.schema import RetrievalDebug
from app.rag import agent as agent_module
from app.rag.agent import MAX_MUTATIONS, MAX_STEPS, run_agent
from app.rag.retrieve import RetrievalOutcome
from app.tools.execute import ToolExecution


class FakeRepo:
    def __init__(self) -> None:
        self.workspace_id = "w1"
        self.completed: dict[str, Any] | None = None
        self.failed: dict[str, Any] | None = None
        self.touched = False

    async def complete_message(self, message_id: str, **kwargs: Any) -> None:
        self.completed = {"message_id": message_id, **kwargs}

    async def fail_message(self, message_id: str, error: str, partial: str = "") -> None:
        self.failed = {"message_id": message_id, "error": error, "partial": partial}

    async def touch_conversation(self, conversation_id: str) -> None:
        self.touched = True


def turn(
    *,
    text: str = "",
    calls: list[tuple[str, dict[str, Any]]] | None = None,
    thought: str | None = None,
) -> dict[str, Any]:
    """One scripted model turn."""
    return {"text": text, "calls": calls or [], "thought": thought}


@pytest.fixture
def agent(monkeypatch: pytest.MonkeyPatch):
    """Script the model and the tool executor; record everything they see."""
    state: dict[str, Any] = {
        "turns": [],
        "histories": [],
        "executed": [],
        "results": {},
        "chunks": [],
    }

    async def fake_retrieve(*, repo, query, **kwargs):
        debug = RetrievalDebug(
            workspace_id=repo.workspace_id,
            query=query,
            candidate_count=len(state["chunks"]),
            returned_count=len(state["chunks"]),
            min_similarity=0.15,
            latency_ms=5,
            chunks=[],
        )
        return RetrievalOutcome(chunks=state["chunks"], citations=[], debug=debug)

    async def fake_stream(
        history: list[dict[str, Any]], system_prompt: str, tools: list, turn_obj
    ) -> AsyncIterator[str]:
        # Snapshot the history the model was given, so the test can assert what
        # it could actually see on each step.
        state["histories"].append(json.loads(json.dumps(history)))

        index = len(state["histories"]) - 1
        script = state["turns"][index] if index < len(state["turns"]) else turn(text="Done.")

        replay: list[dict[str, Any]] = []
        if script["thought"]:
            replay.append({"type": "thought", "signature": script["thought"]})

        for i, (name, args) in enumerate(script["calls"]):
            call_id = f"call-{index}-{i}"
            turn_obj.function_calls.append({"id": call_id, "name": name, "arguments": args})
            replay.append({"type": "function_call", "id": call_id, "name": name, "arguments": args})

        if script["text"]:
            replay.append(
                {"type": "model_output", "content": [{"type": "text", "text": script["text"]}]}
            )

        turn_obj.replay_steps = replay
        turn_obj.text = script["text"]
        turn_obj.input_tokens = 100
        turn_obj.output_tokens = 20

        if script["text"]:
            yield script["text"]

    async def fake_execute(*, name, arguments, context, step_index, message_id=None):
        state["executed"].append({"name": name, "arguments": arguments, "step": step_index})
        payload = state["results"].get(name, {"ok": True, "tool": name})
        is_error = "error" in payload
        return ToolExecution(
            name=name,
            status="execution_error" if is_error else "success",
            payload=payload,
            is_error=is_error,
            latency_ms=3,
            raw_arguments=arguments if isinstance(arguments, dict) else {},
        )

    monkeypatch.setattr(agent_module, "retrieve_chunks", fake_retrieve)
    monkeypatch.setattr(agent_module, "_stream_model_turn", fake_stream)
    monkeypatch.setattr(agent_module, "execute_tool_call", fake_execute)
    return state


async def collect(repo: FakeRepo) -> list[dict[str, Any]]:
    return [
        event
        async for event in run_agent(
            repo=repo,  # type: ignore[arg-type]
            workspace_name="Northwind Logistics",
            user_id="u1",
            conversation_id="cv1",
            question="What tasks exist? Create one if there is no carrier review.",
            assistant_message_id="m1",
        )
    ]


def kinds(events: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [e for e in events if e["type"] == kind]


# --- the multi-step property ----------------------------------------------


async def test_the_model_can_call_a_tool_read_it_and_call_another(agent) -> None:
    """tool -> result -> different tool -> answer, in one user turn."""
    agent["turns"] = [
        turn(calls=[("list_tasks", {"status": "open"})], thought="sig-1"),
        turn(calls=[("save_task", {"title": "Review Pelham Road"})], thought="sig-2"),
        turn(text="I checked your tasks and added one for the carrier review."),
    ]
    agent["results"] = {
        "list_tasks": {"count": 0, "tasks": []},
        "save_task": {"task": {"id": "t1", "title": "Review Pelham Road"}},
    }

    repo = FakeRepo()
    events = await collect(repo)

    assert [c["name"] for c in agent["executed"]] == ["list_tasks", "save_task"]
    assert [c["step"] for c in agent["executed"]] == [0, 1]

    calls = kinds(events, "tool_call")
    assert [c["name"] for c in calls] == ["list_tasks", "save_task"]

    results = kinds(events, "tool_result")
    assert all(r["status"] == "success" for r in results)

    assert repo.completed is not None
    assert repo.completed["usage"].tool_steps == 2
    assert "added one" in repo.completed["content"]


async def test_the_second_turn_can_see_the_first_tool_result(agent) -> None:
    """The point of chaining: the follow-up decision is informed by the result.

    Without the function_result in history the model is choosing blind, and the
    feature is multi-step in shape only.
    """
    agent["turns"] = [
        turn(calls=[("list_tasks", {"status": "open"})]),
        turn(text="You already have a carrier review saved."),
    ]
    agent["results"] = {"list_tasks": {"count": 1, "tasks": [{"title": "Carrier review"}]}}

    await collect(FakeRepo())

    second_history = agent["histories"][1]
    results = [step for step in second_history if step.get("type") == "function_result"]
    assert len(results) == 1
    assert results[0]["name"] == "list_tasks"
    assert "Carrier review" in results[0]["result"]
    assert results[0]["is_error"] is False


async def test_thought_signatures_are_replayed_with_the_function_call(agent) -> None:
    """Gemini 3 rejects a follow-up whose function_call lost its thought.

    Verified against the live API, where omitting it returns a bare
    "400 Request contains an invalid argument" — so this is pinned rather than
    trusted to survive a refactor of the replay logic.
    """
    agent["turns"] = [
        turn(calls=[("list_tasks", {})], thought="signature-abc"),
        turn(text="Done."),
    ]

    await collect(FakeRepo())

    second_history = agent["histories"][1]
    types = [step.get("type") for step in second_history]

    assert "thought" in types
    thought_index = types.index("thought")
    call_index = types.index("function_call")
    # Order matters as much as presence: the thought precedes its call.
    assert thought_index < call_index
    assert second_history[thought_index]["signature"] == "signature-abc"


async def test_a_failing_tool_still_feeds_its_error_back(agent) -> None:
    """A tool failure is information for the model, not the end of the turn."""
    agent["turns"] = [
        turn(calls=[("send_notification", {"title": "x", "message": "y"})]),
        turn(text="I could not send that — no webhook is configured."),
    ]
    agent["results"] = {"send_notification": {"error": "No webhook configured."}}

    repo = FakeRepo()
    events = await collect(repo)

    assert kinds(events, "tool_result")[0]["isError"] is True

    fed_back = [s for s in agent["histories"][1] if s.get("type") == "function_result"][0]
    assert fed_back["is_error"] is True
    assert "No webhook" in fed_back["result"]

    assert repo.completed is not None
    assert "could not send" in repo.completed["content"]


# --- guardrails ------------------------------------------------------------


async def test_the_loop_stops_at_max_steps(agent) -> None:
    """A model that asks for a tool forever must not loop forever."""
    agent["turns"] = [turn(calls=[("list_tasks", {})]) for _ in range(MAX_STEPS + 5)]

    repo = FakeRepo()
    events = await collect(repo)

    assert len(agent["histories"]) == MAX_STEPS
    assert len(agent["executed"]) == MAX_STEPS
    # The turn still resolves rather than hanging or erroring.
    assert kinds(events, "done")
    assert repo.completed is not None


async def test_state_changing_calls_are_capped_per_turn(agent) -> None:
    """Bounds the blast radius of a model — or an injection — gone wrong."""
    agent["turns"] = [turn(calls=[("save_task", {"title": f"Task {i}"})]) for i in range(MAX_STEPS)]

    repo = FakeRepo()
    events = await collect(repo)

    assert len(agent["executed"]) == MAX_MUTATIONS

    results = kinds(events, "tool_result")
    refused = [r for r in results if r["status"] == "execution_error"]
    assert len(refused) == MAX_STEPS - MAX_MUTATIONS
    assert repo.completed is not None


async def test_the_mutation_refusal_is_explained_to_the_model(
    agent, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refused call must come back as a readable reason, not as silence.

    Silence is something a model retries into; a stated reason is something it
    can summarise and stop on.

    The cap is lowered to 1 so the refusal happens early enough for a later
    model turn to be handed the history containing it — at the default cap the
    refusal lands on the final step, after which there is no further turn to
    observe it.
    """
    monkeypatch.setattr(agent_module, "MAX_MUTATIONS", 1)
    agent["turns"] = [
        turn(calls=[("save_task", {"title": "One"})]),
        turn(calls=[("save_task", {"title": "Two"})]),
        turn(text="I saved the first and stopped."),
    ]

    await collect(FakeRepo())

    assert len(agent["executed"]) == 1

    third_history = agent["histories"][2]
    refusals = [
        s
        for s in third_history
        if s.get("type") == "function_result" and "Refused" in str(s.get("result"))
    ]
    assert refusals, "the model was never told why its call did not run"
    assert "state-changing" in refusals[0]["result"]


async def test_read_only_tools_are_not_capped_as_mutations(agent) -> None:
    """Only state-changing calls count against the mutation budget."""
    agent["turns"] = [turn(calls=[("list_tasks", {})]) for _ in range(MAX_STEPS)]

    events = await collect(FakeRepo())

    assert len(agent["executed"]) == MAX_STEPS
    assert not [r for r in kinds(events, "tool_result") if r["isError"]]


async def test_a_turn_with_no_tool_calls_answers_immediately(agent) -> None:
    """The common case must not pay for the loop."""
    agent["turns"] = [turn(text="The surcharge is 14.5%.")]

    repo = FakeRepo()
    events = await collect(repo)

    assert len(agent["histories"]) == 1
    assert agent["executed"] == []
    assert kinds(events, "tool_call") == []
    assert repo.completed is not None
    assert repo.completed["usage"].tool_steps == 0


async def test_tokens_accumulate_across_every_step(agent) -> None:
    """Cost is per turn, not per model call — a chained answer costs more."""
    agent["turns"] = [
        turn(calls=[("list_tasks", {})]),
        turn(calls=[("save_task", {"title": "x"})]),
        turn(text="Done."),
    ]

    repo = FakeRepo()
    await collect(repo)

    usage = repo.completed["usage"]  # type: ignore[index]
    assert usage.input_tokens == 300
    assert usage.output_tokens == 60
    assert usage.total_tokens == 360


async def test_a_failure_marks_the_message_rather_than_losing_the_turn(agent) -> None:
    async def explode(*args: Any, **kwargs: Any):
        raise RuntimeError("upstream is down")
        yield  # pragma: no cover - makes this an async generator

    import pytest as _pytest

    with _pytest.MonkeyPatch.context() as patch:
        patch.setattr(agent_module, "_stream_model_turn", explode)
        repo = FakeRepo()
        events = await collect(repo)

    assert kinds(events, "error")
    assert repo.failed is not None
    assert repo.completed is None
