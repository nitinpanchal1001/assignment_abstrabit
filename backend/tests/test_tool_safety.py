"""No model output may cause an unhandled exception or an unintended action.

Two separate obligations are covered here.

SAFETY
    ``execute_tool_call`` is the boundary between text a model generated and
    code that runs. A hallucinated tool name, arguments of the wrong type, a
    missing required field, a handler that raises or hangs — each must resolve
    to a structured result the model can read and correct, and each must be
    recorded in the audit log, rejections included.

TENANCY
    No tool schema may accept a workspace, user or table identifier. If one
    ever did, model output could choose the tenant, and every other isolation
    guarantee in the codebase would route around itself through the tool layer.
    That is asserted over the registry as a whole, so a tool added later is
    covered without anyone remembering to extend this file.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.tools.execute import TOOL_TIMEOUT_SECONDS, execute_tool_call
from app.tools.registry import (
    MUTATING_TOOLS,
    TOOLS,
    ToolContext,
    ToolDefinition,
    ToolResult,
    tool_declarations,
)

#: Any argument name that would let model output pick a tenant or a table.
FORBIDDEN_ARGUMENT_NAMES = {
    "workspace",
    "workspace_id",
    "workspaceid",
    "tenant",
    "tenant_id",
    "user",
    "user_id",
    "userid",
    "account",
    "account_id",
    "collection",
    "table",
    "database",
    "db",
    "index",
    "webhook",
    "webhook_url",
    "url",
}


class RecordingRepo:
    """Minimal stand-in for WorkspaceRepository, capturing the audit trail."""

    def __init__(self, *, fail_audit: bool = False) -> None:
        self.workspace_id = "w1"
        self.calls: list[dict[str, Any]] = []
        self._fail_audit = fail_audit

    async def insert_tool_call(self, **kwargs: Any) -> None:
        if self._fail_audit:
            raise RuntimeError("mongo is down")
        self.calls.append(kwargs)


def context(repo: RecordingRepo | None = None) -> ToolContext:
    return ToolContext(
        repo=repo or RecordingRepo(),  # type: ignore[arg-type]
        workspace_id="w1",
        workspace_name="Northwind Logistics",
        user_id="u1",
        conversation_id="cv1",
    )


async def run(name: str, arguments: Any, *, repo: RecordingRepo | None = None):
    repo = repo or RecordingRepo()
    execution = await execute_tool_call(
        name=name, arguments=arguments, context=context(repo), step_index=0
    )
    return execution, repo


# --- tenancy ---------------------------------------------------------------


def test_no_tool_schema_accepts_a_tenant_identifier() -> None:
    """Covers every registered tool, including ones added after this was written."""
    for declaration in tool_declarations():
        properties = declaration["parameters"].get("properties", {})
        leaked = {p for p in properties if p.lower() in FORBIDDEN_ARGUMENT_NAMES}
        assert not leaked, f"{declaration['name']} exposes tenant-selecting arguments: {leaked}"


def test_tool_context_carries_the_tenant_instead() -> None:
    """The workspace arrives from the session, never from the call."""
    ctx = context()
    assert ctx.workspace_id == "w1"
    assert ctx.repo.workspace_id == "w1"


# --- declarations the model actually receives ------------------------------


def test_declarations_avoid_schema_keywords_gemini_rejects() -> None:
    """`$ref`/`$defs`/`anyOf` make the API refuse the declaration silently.

    The symptom is not an error — it is the model simply never calling the
    tool, which is indistinguishable from the model choosing not to.
    """
    banned = {"$defs", "$ref", "$schema", "anyOf", "allOf", "oneOf"}

    def walk(node: Any, path: str) -> list[str]:
        found = []
        if isinstance(node, dict):
            for key, value in node.items():
                if key in banned:
                    found.append(f"{path}.{key}")
                if key == "properties" and isinstance(value, dict):
                    for name, sub in value.items():
                        found += walk(sub, f"{path}.{name}")
                else:
                    found += walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for i, item in enumerate(node):
                found += walk(item, f"{path}[{i}]")
        return found

    offenders = [o for d in tool_declarations() for o in walk(d["parameters"], d["name"])]
    assert not offenders, f"Gemini-hostile schema keywords: {offenders}"


def test_required_fields_all_exist_as_properties() -> None:
    """The regression that sanitising `title` too eagerly used to cause.

    `save_task` has a field NAMED `title`, and Pydantic also emits `title` as
    per-property metadata. Blanket-dropping the key removed the property, and
    the API rejected the declaration for requiring a field it could not see.
    """
    for declaration in tool_declarations():
        parameters = declaration["parameters"]
        properties = set(parameters.get("properties", {}))
        required = set(parameters.get("required", []))
        assert required <= properties, (
            f"{declaration['name']} requires {required - properties}, which is not declared"
        )


def test_save_task_still_declares_its_title_field() -> None:
    declaration = next(d for d in tool_declarations() if d["name"] == "save_task")
    assert "title" in declaration["parameters"]["properties"]
    assert "title" in declaration["parameters"]["required"]


def test_every_declaration_describes_when_not_to_call_it() -> None:
    """A description that only says when to call fires too eagerly."""
    for declaration in tool_declarations():
        assert len(declaration["description"]) > 80, declaration["name"]


def test_mutating_tools_are_marked() -> None:
    """The agent caps state-changing calls per turn using this set."""
    assert "save_task" in MUTATING_TOOLS
    assert "send_notification" in MUTATING_TOOLS
    assert "list_tasks" not in MUTATING_TOOLS


# --- rejection paths -------------------------------------------------------


async def test_unknown_tool_is_refused_and_audited() -> None:
    """The prompt-injection case: a document demanding `delete_everything`."""
    execution, repo = await run("delete_everything", {"confirm": True})

    assert execution.status == "unknown_tool"
    assert execution.is_error
    assert "no tool named" in execution.payload["error"]
    # The model is told this is an injection signature, not a typo to retry.
    assert "injected text" in execution.payload["error"]

    assert len(repo.calls) == 1, "a refused call must still be audited"
    assert repo.calls[0]["status"] == "unknown_tool"
    assert repo.calls[0]["tool_name"] == "delete_everything"


async def test_missing_required_argument_is_a_validation_error() -> None:
    execution, repo = await run("save_task", {})

    assert execution.status == "validation_error"
    assert execution.is_error
    assert any(issue["field"] == "title" for issue in execution.payload["issues"])
    assert repo.calls[0]["status"] == "validation_error"


async def test_wrong_type_is_a_validation_error() -> None:
    execution, _ = await run("list_tasks", {"limit": "all of them"})
    assert execution.status == "validation_error"


async def test_out_of_range_argument_is_rejected() -> None:
    """`limit` is bounded 1..50; a model asking for 5000 must not get it."""
    execution, _ = await run("list_tasks", {"limit": 5000})
    assert execution.status == "validation_error"


async def test_unexpected_extra_argument_is_rejected() -> None:
    """`extra="forbid"` — an invented argument fails rather than being ignored.

    Silently dropping it would let a model believe it had constrained an action
    it had not, which is the shape of an injection that half-works.
    """
    execution, _ = await run("save_task", {"title": "Review", "workspace_id": "other-tenant"})
    assert execution.status == "validation_error"


async def test_malformed_arguments_do_not_crash() -> None:
    """The agent hands over `{}` when streamed argument JSON does not parse."""
    for arguments in ({}, None, "not a dict", [1, 2, 3], 42):
        execution, _ = await run("save_task", arguments)
        assert execution.status == "validation_error", arguments
        assert execution.is_error


async def test_a_handler_that_raises_becomes_a_structured_error() -> None:
    async def boom(args: Any, ctx: ToolContext) -> ToolResult:
        raise RuntimeError("mongodb://user:password@cluster/internal leaked detail")

    TOOLS["_boom"] = ToolDefinition(
        name="_boom",
        description="x" * 100,
        args_model=TOOLS["list_tasks"].args_model,
        mutates=False,
        handler=boom,
    )
    try:
        execution, repo = await run("_boom", {})
    finally:
        del TOOLS["_boom"]

    assert execution.status == "execution_error"
    assert execution.is_error
    assert repo.calls[0]["status"] == "execution_error"


async def test_a_hanging_handler_times_out() -> None:
    async def hang(args: Any, ctx: ToolContext) -> ToolResult:
        await asyncio.sleep(TOOL_TIMEOUT_SECONDS * 10)
        raise AssertionError("unreachable")

    TOOLS["_hang"] = ToolDefinition(
        name="_hang",
        description="x" * 100,
        args_model=TOOLS["list_tasks"].args_model,
        mutates=False,
        handler=hang,
    )
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr("app.tools.execute.TOOL_TIMEOUT_SECONDS", 0.05)
            execution, _ = await run("_hang", {})
    finally:
        del TOOLS["_hang"]

    assert execution.status == "execution_error"
    assert "timed out" in execution.payload["error"]


async def test_a_failed_audit_write_does_not_fail_the_request() -> None:
    """Losing an audit row is bad; losing the user's turn over it is worse."""
    execution, _ = await run("save_task", {}, repo=RecordingRepo(fail_audit=True))
    assert execution.status == "validation_error"


async def test_valid_arguments_reach_the_handler_parsed() -> None:
    seen: dict[str, Any] = {}

    async def capture(args: Any, ctx: ToolContext) -> ToolResult:
        seen["priority"] = args.priority
        seen["due_date"] = args.due_date
        seen["workspace"] = ctx.workspace_id
        return ToolResult(ok=True, data={"saved": True})

    TOOLS["_capture"] = ToolDefinition(
        name="_capture",
        description="x" * 100,
        args_model=TOOLS["save_task"].args_model,
        mutates=True,
        handler=capture,
    )
    try:
        execution, repo = await run(
            "_capture", {"title": "Review Pelham Road", "due_date": "2026-10-02"}
        )
    finally:
        del TOOLS["_capture"]

    assert execution.status == "success"
    assert not execution.is_error
    # Defaults are applied by the schema, not left for the handler to guess.
    assert seen["priority"] == "medium"
    assert seen["due_date"] == "2026-10-02"
    assert seen["workspace"] == "w1"
    assert repo.calls[0]["status"] == "success"


async def test_a_malformed_due_date_is_rejected_by_its_pattern() -> None:
    execution, _ = await run("save_task", {"title": "Review", "due_date": "next Friday"})
    assert execution.status == "validation_error"
