"""Workspace listing, creation, and the dashboard overview."""

from __future__ import annotations

from fastapi import APIRouter

from app.api_models import (
    CreateWorkspaceIn,
    ObservabilityOut,
    OverviewOut,
    TaskOut,
    ToolCallOut,
    VectorStatsOut,
    WorkspaceOut,
)
from app.auth.workspace import create_workspace, list_workspaces
from app.deps import CurrentUser, WorkspaceDep
from app.observability import build_report
from app.vector.qdrant import vector_store_stats

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.get("", response_model=list[WorkspaceOut])
async def get_workspaces(user: CurrentUser) -> list[WorkspaceOut]:
    return [WorkspaceOut.of(w) for w in await list_workspaces(user.id)]


@router.post("", response_model=WorkspaceOut, status_code=201)
async def post_workspace(payload: CreateWorkspaceIn, user: CurrentUser) -> WorkspaceOut:
    # Workspace and owner membership are written together — see
    # create_workspace() for why that has to be atomic.
    return WorkspaceOut.of(await create_workspace(payload.name, user.id))


@router.get("/observability", response_model=ObservabilityOut)
async def observability(ctx: WorkspaceDep) -> ObservabilityOut:
    """Cost, latency, retrieval quality and tool health for this workspace.

    Workspace-scoped like everything else: the repository is bound to the
    session's workspace, so these figures cannot aggregate across tenants even
    by accident. An operations view is exactly the kind of screen where a
    "just this once" global query gets written, which is why it goes through
    the same gate as the chat path.
    """
    return ObservabilityOut.of(await build_report(ctx.repo))


@router.get("/overview", response_model=OverviewOut)
async def overview(ctx: WorkspaceDep) -> OverviewOut:
    repo = ctx.repo

    documents = await repo.count_documents()
    messages = await repo.count_messages()
    tool_calls = await repo.count_tool_calls()
    open_tasks = await repo.count_tasks("open")
    recent = await repo.list_tool_calls(limit=5)

    # Degrades instead of raising: these tiles are informational, and a failure
    # here should cost the statistics, not the page.
    stats = await vector_store_stats(ctx.workspace.id)

    return OverviewOut(
        workspace=WorkspaceOut.of(ctx.workspace),
        documents=documents,
        messages=messages,
        tool_calls=tool_calls,
        open_tasks=open_tasks,
        vectors=VectorStatsOut(
            workspace=stats.workspace,
            total=stats.total,
            available=stats.available,
            reason=stats.reason,
        ),
        recent_tool_calls=[ToolCallOut.of(c) for c in recent],
    )


@router.get("/tasks", response_model=list[TaskOut])
async def tasks(ctx: WorkspaceDep) -> list[TaskOut]:
    return [TaskOut.of(t) for t in await ctx.repo.list_tasks()]


@router.get("/tool-calls", response_model=list[ToolCallOut])
async def tool_calls(ctx: WorkspaceDep) -> list[ToolCallOut]:
    return [ToolCallOut.of(c) for c in await ctx.repo.list_tool_calls(limit=100)]
