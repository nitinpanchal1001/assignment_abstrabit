"""The tool registry.

Every tool's arguments are a Pydantic model, and the JSON Schema shown to the
model is *generated from it*. One definition, so the contract the model is given
and the contract we enforce cannot drift apart — a class of bug that otherwise
appears only when a model sends a technically-valid argument the validator has
never seen.

Note what the schemas do NOT contain: no workspace id, no user id, no
collection name, no webhook URL. Those come from the authenticated context. A
tool call is a request to act *within* the caller's workspace; it can never be
a request to act on a different one.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field

from app.config import settings
from app.db.repositories import WorkspaceRepository

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolContext:
    """Execution context for a tool call.

    Everything security-relevant lives here and NOTHING in it is derived from
    model output. ``repo`` is already bound to the workspace resolved from the
    authenticated session, so a tool physically cannot reach another tenant —
    the tenant is not something it accepts as an argument.
    """

    repo: WorkspaceRepository
    workspace_id: str
    workspace_name: str
    user_id: str
    conversation_id: str | None


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    data: dict[str, Any] | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# save_task — the side-effecting tool, writing into the active workspace.
# ---------------------------------------------------------------------------


class SaveTaskArgs(BaseModel):
    model_config = {"extra": "forbid"}

    title: str = Field(
        ..., min_length=1, max_length=200, description="Short, action-oriented task title."
    )
    details: str | None = Field(
        default=None, max_length=2000, description="Optional longer description or context."
    )
    priority: Literal["low", "medium", "high"] = Field(
        default="medium", description="Task priority. Defaults to medium when the user doesn't say."
    )
    due_date: str | None = Field(
        default=None,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="Optional due date as YYYY-MM-DD.",
    )


async def _save_task(args: SaveTaskArgs, ctx: ToolContext) -> ToolResult:
    try:
        # The repository is already bound to the session's workspace, so the
        # model has no way to influence where this lands.
        task = await ctx.repo.insert_task(
            title=args.title.strip(),
            details=args.details.strip() if args.details else None,
            priority=args.priority,
            due_date=args.due_date,
            created_by_tool=True,
        )
        return ToolResult(
            ok=True,
            data={
                "task": {
                    "id": task.id,
                    "title": task.title,
                    "priority": task.priority,
                    "due_date": task.due_date,
                    "status": task.status,
                },
                "workspace": ctx.workspace_name,
            },
        )
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, error=f"Could not save the task: {exc}")


# ---------------------------------------------------------------------------
# list_tasks — read-only. Present so the model can chain: look up existing
# tasks, then decide whether a new one is warranted.
# ---------------------------------------------------------------------------


class ListTasksArgs(BaseModel):
    model_config = {"extra": "forbid"}

    status: Literal["open", "done", "all"] = Field(
        default="open", description="Filter by task status. Defaults to open tasks only."
    )
    limit: int = Field(default=20, ge=1, le=50, description="Maximum tasks to return (1-50).")


async def _list_tasks(args: ListTasksArgs, ctx: ToolContext) -> ToolResult:
    try:
        tasks = await ctx.repo.list_tasks(
            status=None if args.status == "all" else args.status, limit=args.limit
        )
        return ToolResult(
            ok=True,
            data={
                "count": len(tasks),
                "tasks": [
                    {
                        "id": t.id,
                        "title": t.title,
                        "details": t.details,
                        "priority": t.priority,
                        "due_date": t.due_date,
                        "status": t.status,
                    }
                    for t in tasks
                ],
                "workspace": ctx.workspace_name,
            },
        )
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, error=f"Could not list tasks: {exc}")


# ---------------------------------------------------------------------------
# send_notification — the external side effect.
# ---------------------------------------------------------------------------


class SendNotificationArgs(BaseModel):
    model_config = {"extra": "forbid"}

    title: str = Field(..., min_length=1, max_length=200, description="Short headline.")
    message: str = Field(
        ..., min_length=1, max_length=1800, description="Body of the notification."
    )


WEBHOOK_TIMEOUT_SECONDS = 8.0


def _detect_transport(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if host.endswith("slack.com"):
        return "slack"
    if host.endswith(("discord.com", "discordapp.com")):
        return "discord"
    return "generic"


async def _send_notification(args: SendNotificationArgs, ctx: ToolContext) -> ToolResult:
    webhook_url = settings.notification_webhook_url

    # An unconfigured webhook is a normal state, not a crash. Reporting it as a
    # tool-level failure lets the model tell the user something useful instead
    # of the request 500ing.
    if not webhook_url:
        return ToolResult(
            ok=False,
            error=(
                "No notification webhook is configured for this deployment, so nothing was "
                "sent. Tell the user an administrator needs to set NOTIFICATION_WEBHOOK_URL."
            ),
        )

    transport = _detect_transport(webhook_url)
    footer = f'\n\n_Sent from the "{ctx.workspace_name}" workspace._'
    body = (
        {"text": f"*{args.title}*\n{args.message}{footer}"}
        if transport == "slack"
        else {"content": f"**{args.title}**\n{args.message}{footer}"[:2000]}
    )

    try:
        async with httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT_SECONDS) as client:
            response = await client.post(webhook_url, json=body)

        if response.status_code >= 400:
            # Deliberately does not echo the response body or the URL: webhook
            # URLs are bearer credentials and must never reach a log or the model.
            return ToolResult(
                ok=False,
                error=(
                    f"The {transport} webhook rejected the message (HTTP {response.status_code})."
                ),
            )

        return ToolResult(
            ok=True,
            data={
                "delivered": True,
                "transport": transport,
                "title": args.title,
                "workspace": ctx.workspace_name,
            },
        )
    except httpx.TimeoutException:
        return ToolResult(
            ok=False,
            error=f"The {transport} webhook did not respond within {WEBHOOK_TIMEOUT_SECONDS:.0f}s.",
        )
    except httpx.HTTPError:
        return ToolResult(ok=False, error=f"Could not reach the {transport} webhook.")


# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    #: Shown to the model. Should say when NOT to call it, not just when to.
    description: str
    args_model: type[BaseModel]
    #: True if the tool changes state; the executor caps how often those fire.
    mutates: bool
    handler: Callable[[Any, ToolContext], Awaitable[ToolResult]]


TOOLS: dict[str, ToolDefinition] = {
    "save_task": ToolDefinition(
        name="save_task",
        description=(
            "Save a task or action item into the current workspace. Use when the user asks to "
            "create, add, record, or remember a task, to-do, action item or follow-up. Do not "
            "use it to answer questions, and do not call it for hypothetical tasks mentioned "
            "inside documents unless the user explicitly asks you to save one."
        ),
        args_model=SaveTaskArgs,
        mutates=True,
        handler=_save_task,
    ),
    "list_tasks": ToolDefinition(
        name="list_tasks",
        description=(
            "List tasks already saved in the current workspace. Use when the user asks what "
            "tasks exist, or to check for an existing task before creating a duplicate."
        ),
        args_model=ListTasksArgs,
        mutates=False,
        handler=_list_tasks,
    ),
    "send_notification": ToolDefinition(
        name="send_notification",
        description=(
            "Send a short notification or summary to the team chat channel (Slack or Discord) "
            "connected to this app. Use only when the user explicitly asks to send, post, "
            "notify or share something. Never send automatically, and never send content from "
            "a document unless the user asked you to share it."
        ),
        args_model=SendNotificationArgs,
        mutates=True,
        handler=_send_notification,
    ),
}

MUTATING_TOOLS = {name for name, tool in TOOLS.items() if tool.mutates}

#: JSON Schema keywords the Gemini function-calling parser rejects. ``$defs``
#: and ``$ref`` in particular cause the declaration to be refused outright,
#: which surfaces as the model simply never calling the tool — a silent failure
#: worth guarding against explicitly.
_DROP_KEYS = {"$defs", "$ref", "$schema", "additionalProperties", "title", "const", "examples"}


def _sanitise(node: Any) -> Any:
    """Strip schema keywords Gemini's parser rejects.

    Structure-aware on purpose. A naive recursive filter also deletes entries
    inside ``properties``, because a FIELD may legitimately be named after a
    schema keyword — ``save_task`` has a field called ``title``, and Pydantic
    also emits ``title`` as per-property metadata. Blanket-dropping the key
    removed the property itself, and the API rejected the declaration with
    "specifies required field 'title' which is not defined in properties".
    Keys inside ``properties`` are therefore never filtered.
    """
    if isinstance(node, list):
        return [_sanitise(item) for item in node]
    if not isinstance(node, dict):
        return node

    out: dict[str, Any] = {}
    for key, value in node.items():
        if key in _DROP_KEYS:
            continue
        if key == "properties" and isinstance(value, dict):
            out[key] = {name: _sanitise(sub) for name, sub in value.items()}
        else:
            out[key] = _sanitise(value)
    return out


def _inline_optionals(schema: dict[str, Any]) -> dict[str, Any]:
    """Flatten ``anyOf: [X, null]`` that Pydantic emits for optional fields.

    Gemini's schema parser does not accept anyOf, and an optional field is
    better expressed simply by being absent from ``required``.
    """
    properties = schema.get("properties", {})
    for key, prop in list(properties.items()):
        variants = prop.get("anyOf")
        if not variants:
            continue
        concrete = [v for v in variants if v.get("type") != "null"]
        if len(concrete) == 1:
            merged = {**concrete[0]}
            if "description" in prop:
                merged["description"] = prop["description"]
            properties[key] = merged
    return schema


def tool_declarations() -> list[dict[str, Any]]:
    """Model-facing declarations, generated from the Pydantic models."""
    declarations = []
    for tool in TOOLS.values():
        schema = _inline_optionals(_sanitise(tool.args_model.model_json_schema()))
        declarations.append(
            {
                "type": "function",
                "name": tool.name,
                "description": tool.description,
                "parameters": schema,
            }
        )
    return declarations
