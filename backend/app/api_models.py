"""Wire models.

Python is snake_case and TypeScript is camelCase, and making either side adopt
the other's convention produces noise at every call site. These models keep the
internal code idiomatic while emitting camelCase JSON, so the frontend never
translates field names.

**Every model that crosses the network must be declared here**, including ones
that only ever appear nested inside another. Pydantic applies
``alias_generator`` per model, not down the tree: a storage model embedded in an
``ApiModel`` serialises with its own field names, so the response comes out
camelCase at the top level and snake_case inside. That failure is quiet — the
JSON parses, the shape looks right, and the mismatched fields simply read
``undefined`` in the browser. ``tests/test_wire_contract.py`` fails the build if
a snake_case key reaches the wire at any depth.

The storage models in ``db.schema`` deliberately do NOT get the alias
generator. ``MongoModel.to_mongo()`` dumps ``by_alias=True`` (it needs ``_id``),
and aliases apply to nested models on the way out, so aliasing ``Citation``
would start writing camelCase keys into Mongo while every reader still expects
snake_case.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field
from pydantic.alias_generators import to_camel

from app.db.schema import (
    Citation,
    Document,
    Message,
    RetrievalChunk,
    RetrievalDebug,
    Task,
    ToolCall,
    UsageStats,
    Workspace,
)
from app.observability import ObservabilityReport, RequestRecord, ToolHealth
from app.sharing import SharedDocumentView, ShareView


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


def wire(model: ApiModel) -> dict[str, Any]:
    """Serialise for hand-built JSON — SSE frames, mainly.

    FastAPI applies ``by_alias=True`` to anything returned through a
    ``response_model``; a payload assembled by hand gets no such treatment, and
    a plain ``model_dump()`` silently emits snake_case. ``mode="json"`` is what
    makes the result safe for ``json.dumps`` (datetimes become strings).
    """
    return model.model_dump(by_alias=True, mode="json")


# --- requests --------------------------------------------------------------


class CredentialsIn(ApiModel):
    email: EmailStr
    password: str = Field(..., min_length=1, max_length=200)


class SignupIn(ApiModel):
    email: EmailStr
    #: Length only. Composition rules push people toward `Passw0rd!`, which is
    #: weaker than a longer passphrase.
    password: str = Field(..., min_length=8, max_length=200)


class CreateWorkspaceIn(ApiModel):
    name: str = Field(..., min_length=1, max_length=80)


class SwitchWorkspaceIn(ApiModel):
    workspace_id: str


class ChatIn(ApiModel):
    workspace_id: str
    conversation_id: str | None = None
    message: str = Field(..., min_length=1, max_length=4000)


# --- responses -------------------------------------------------------------


class UserOut(ApiModel):
    id: str
    email: str
    display_name: str


class WorkspaceOut(ApiModel):
    id: str
    name: str
    created_at: datetime

    @classmethod
    def of(cls, workspace: Workspace) -> WorkspaceOut:
        return cls(id=workspace.id, name=workspace.name, created_at=workspace.created_at)


class DocumentOut(ApiModel):
    id: str
    filename: str
    mime_type: str
    byte_size: int
    status: str
    error_message: str | None
    chunk_count: int
    created_at: datetime

    @classmethod
    def of(cls, document: Document) -> DocumentOut:
        return cls(
            id=document.id,
            filename=document.filename,
            mime_type=document.mime_type,
            byte_size=document.byte_size,
            status=document.status,
            error_message=document.error_message,
            chunk_count=document.chunk_count,
            created_at=document.created_at,
        )


class CreateShareIn(ApiModel):
    #: The workspace to grant read access to. Note what this is NOT: a document
    #: id or a user id. The document comes from the URL and is authorised
    #: against the caller's active workspace.
    target_workspace_id: str


class ShareOut(ApiModel):
    """One grant this workspace has given on one of its documents."""

    id: str
    document_id: str
    target_workspace_id: str
    target_workspace_name: str
    created_at: datetime

    @classmethod
    def of(cls, view: ShareView) -> ShareOut:
        return cls(
            id=view.share.id,
            document_id=view.share.document_id,
            target_workspace_id=view.share.target_workspace_id,
            target_workspace_name=view.target_workspace_name,
            created_at=view.share.created_at,
        )


class SharedDocumentOut(ApiModel):
    """A document another workspace has granted to this one.

    Deliberately a different shape from DocumentOut: it has no delete or
    re-share affordance, and it names its owner. A borrowed document rendered
    identically to an owned one is how a UI ends up offering actions the API
    will refuse.
    """

    id: str
    filename: str
    mime_type: str
    byte_size: int
    chunk_count: int
    owner_workspace_name: str
    shared_at: datetime

    @classmethod
    def of(cls, view: SharedDocumentView) -> SharedDocumentOut:
        return cls(
            id=view.document.id,
            filename=view.document.filename,
            mime_type=view.document.mime_type,
            byte_size=view.document.byte_size,
            chunk_count=view.document.chunk_count,
            owner_workspace_name=view.owner_workspace_name,
            shared_at=view.shared_at,
        )


class TaskOut(ApiModel):
    id: str
    title: str
    details: str | None
    priority: str
    due_date: str | None
    status: str
    created_by_tool: bool
    created_at: datetime

    @classmethod
    def of(cls, task: Task) -> TaskOut:
        return cls(
            id=task.id,
            title=task.title,
            details=task.details,
            priority=task.priority,
            due_date=task.due_date,
            status=task.status,
            created_by_tool=task.created_by_tool,
            created_at=task.created_at,
        )


class ToolCallOut(ApiModel):
    id: str
    tool_name: str
    arguments: dict[str, Any]
    result: Any
    status: str
    error_message: str | None
    latency_ms: int | None
    step_index: int
    created_at: datetime

    @classmethod
    def of(cls, call: ToolCall) -> ToolCallOut:
        return cls(
            id=call.id,
            tool_name=call.tool_name,
            arguments=call.arguments,
            result=call.result,
            status=call.status,
            error_message=call.error_message,
            latency_ms=call.latency_ms,
            step_index=call.step_index,
            created_at=call.created_at,
        )


class CitationOut(ApiModel):
    #: 1-based; matches the [n] marker in the assistant's text.
    index: int
    chunk_id: str
    document_id: str
    filename: str
    section: str | None
    chunk_index: int
    similarity: float
    snippet: str
    #: Owning workspace name when the passage came from a shared document.
    shared_from: str | None

    @classmethod
    def of(cls, citation: Citation) -> CitationOut:
        return cls(
            index=citation.index,
            chunk_id=citation.chunk_id,
            document_id=citation.document_id,
            filename=citation.filename,
            section=citation.section,
            chunk_index=citation.chunk_index,
            similarity=citation.similarity,
            snippet=citation.snippet,
            shared_from=citation.shared_from,
        )


class RetrievalChunkOut(ApiModel):
    chunk_id: str
    document_id: str
    #: The OWNING workspace. Equal to the workspace being searched unless the
    #: chunk arrived through an explicit share.
    workspace_id: str
    #: Owning workspace name when this came from a share, else None.
    shared_from: str | None
    filename: str
    section: str | None
    chunk_index: int
    similarity: float
    vector_rank: int | None
    keyword_rank: int | None
    score: float
    preview: str

    @classmethod
    def of(cls, chunk: RetrievalChunk) -> RetrievalChunkOut:
        return cls(
            chunk_id=chunk.chunk_id,
            document_id=chunk.document_id,
            workspace_id=chunk.workspace_id,
            shared_from=chunk.shared_from,
            filename=chunk.filename,
            section=chunk.section,
            chunk_index=chunk.chunk_index,
            similarity=chunk.similarity,
            vector_rank=chunk.vector_rank,
            keyword_rank=chunk.keyword_rank,
            score=chunk.score,
            preview=chunk.preview,
        )


class RetrievalDebugOut(ApiModel):
    workspace_id: str
    query: str
    strategy: str
    candidate_count: int
    returned_count: int
    min_similarity: float
    latency_ms: int
    chunks: list[RetrievalChunkOut]

    @classmethod
    def of(cls, debug: RetrievalDebug) -> RetrievalDebugOut:
        return cls(
            workspace_id=debug.workspace_id,
            query=debug.query,
            strategy=debug.strategy,
            candidate_count=debug.candidate_count,
            returned_count=debug.returned_count,
            min_similarity=debug.min_similarity,
            latency_ms=debug.latency_ms,
            chunks=[RetrievalChunkOut.of(c) for c in debug.chunks],
        )


class UsageOut(ApiModel):
    model: str
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    latency_ms: int
    tool_steps: int

    #: ``model_`` is Pydantic's own namespace, and a field literally named
    #: ``model`` trips its shadow warning. The field is part of the contract, so
    #: the namespace is cleared rather than the field renamed.
    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, protected_namespaces=()
    )

    @classmethod
    def of(cls, usage: UsageStats) -> UsageOut:
        return cls(
            model=usage.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            total_tokens=usage.total_tokens,
            latency_ms=usage.latency_ms,
            tool_steps=usage.tool_steps,
        )


class MessageOut(ApiModel):
    id: str
    role: str
    content: str
    citations: list[CitationOut]
    retrieval: RetrievalDebugOut | None
    usage: UsageOut | None
    status: str
    error_message: str | None
    created_at: datetime

    @classmethod
    def of(cls, message: Message) -> MessageOut:
        return cls(
            id=message.id,
            role=message.role,
            content=message.content,
            citations=[CitationOut.of(c) for c in message.citations],
            retrieval=RetrievalDebugOut.of(message.retrieval) if message.retrieval else None,
            usage=UsageOut.of(message.usage) if message.usage else None,
            status=message.status,
            error_message=message.error_message,
            created_at=message.created_at,
        )


class VectorStatsOut(ApiModel):
    workspace: int | None
    total: int | None
    available: bool
    reason: str | None


class OverviewOut(ApiModel):
    workspace: WorkspaceOut
    documents: int
    messages: int
    tool_calls: int
    open_tasks: int
    vectors: VectorStatsOut
    recent_tool_calls: list[ToolCallOut]


class RequestRecordOut(ApiModel):
    message_id: str
    created_at: datetime
    status: str
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    latency_ms: int | None
    tool_steps: int
    retrieval_hit: bool
    chunks_retrieved: int
    citations: int
    preview: str

    @classmethod
    def of(cls, record: RequestRecord) -> RequestRecordOut:
        return cls(
            message_id=record.message_id,
            created_at=record.created_at,  # type: ignore[arg-type]
            status=record.status,
            model=record.model,
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            total_tokens=record.total_tokens,
            latency_ms=record.latency_ms,
            tool_steps=record.tool_steps,
            retrieval_hit=record.retrieval_hit,
            chunks_retrieved=record.chunks_retrieved,
            citations=record.citations,
            preview=record.preview,
        )


class ToolHealthOut(ApiModel):
    tool_name: str
    total: int
    success: int
    failure: int
    success_rate: float
    avg_latency_ms: int | None
    by_status: dict[str, int]

    @classmethod
    def of(cls, health: ToolHealth) -> ToolHealthOut:
        return cls(
            tool_name=health.tool_name,
            total=health.total,
            success=health.success,
            failure=health.failure,
            success_rate=round(health.success_rate, 4),
            avg_latency_ms=health.avg_latency_ms,
            by_status=health.by_status,
        )


class ObservabilityOut(ApiModel):
    """Everything the operations view needs, in one round trip."""

    sample_size: int
    p50_latency_ms: int | None
    p95_latency_ms: int | None
    max_latency_ms: int | None
    total_tokens: int
    input_tokens: int
    output_tokens: int
    retrieval_hits: int
    retrieval_misses: int
    retrieval_hit_rate: float
    failed_requests: int
    requests: list[RequestRecordOut]
    tools: list[ToolHealthOut]
    recent_failures: list[ToolCallOut]

    @classmethod
    def of(cls, report: ObservabilityReport) -> ObservabilityOut:
        return cls(
            sample_size=report.sample_size,
            p50_latency_ms=report.latency.p50_ms,
            p95_latency_ms=report.latency.p95_ms,
            max_latency_ms=report.latency.max_ms,
            total_tokens=report.total_tokens,
            input_tokens=report.input_tokens,
            output_tokens=report.output_tokens,
            retrieval_hits=report.retrieval_hits,
            retrieval_misses=report.retrieval_misses,
            retrieval_hit_rate=round(report.retrieval_hit_rate, 4),
            failed_requests=report.failed_requests,
            requests=[RequestRecordOut.of(r) for r in report.requests],
            tools=[ToolHealthOut.of(t) for t in report.tools],
            recent_failures=[ToolCallOut.of(c) for c in report.recent_failures],
        )


class ConversationOut(ApiModel):
    id: str
    title: str
    messages: list[MessageOut]


class ChatHistoryOut(ApiModel):
    conversation: ConversationOut | None
    document_count: int


class SessionOut(ApiModel):
    user: UserOut
    workspaces: list[WorkspaceOut]
    active_workspace_id: str | None
