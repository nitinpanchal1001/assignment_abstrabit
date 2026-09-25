"""MongoDB document shapes.

Tenancy convention
------------------
Every collection holding tenant data carries a ``workspace_id``, including
documents (messages, tool calls) reachable by joining through a parent. There
is no row-level security in MongoDB, so that field plus the repository layer
IS the boundary — denormalising it means every query can filter directly and no
read depends on remembering to join correctly.

``_id`` is a UUID string rather than an ObjectId, for two reasons: Qdrant point
ids must be an unsigned integer or a UUID, so chunk ids can be shared verbatim
between the two stores; and UUIDs are safe to put in URLs.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

DocumentStatus = Literal["pending", "processing", "ready", "failed"]
MessageRole = Literal["user", "assistant"]
MessageStatus = Literal["streaming", "complete", "failed"]
TaskPriority = Literal["low", "medium", "high"]
TaskStatus = Literal["open", "done"]
WorkspaceRole = Literal["owner", "member"]
ToolCallStatus = Literal["success", "validation_error", "execution_error", "unknown_tool"]


class MongoModel(BaseModel):
    """Base that maps Python ``id`` to Mongo ``_id``."""

    model_config = {"populate_by_name": True, "extra": "ignore"}

    id: str = Field(alias="_id")

    def to_mongo(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True)


class User(MongoModel):
    email: str
    #: bcrypt hash. Never serialised into a response — see PublicUser.
    password_hash: str
    display_name: str
    created_at: datetime


class PublicUser(BaseModel):
    """The only user shape that may cross the network."""

    id: str
    email: str
    display_name: str


class Workspace(MongoModel):
    name: str
    created_by: str
    created_at: datetime


class WorkspaceMember(MongoModel):
    workspace_id: str
    user_id: str
    role: WorkspaceRole
    created_at: datetime


class Document(MongoModel):
    workspace_id: str
    filename: str
    mime_type: str
    byte_size: int
    #: sha256 of the raw upload. Unique per workspace — enforces idempotency.
    content_hash: str
    status: DocumentStatus
    error_message: str | None = None
    chunk_count: int = 0
    uploaded_by: str | None = None
    created_at: datetime


class Chunk(MongoModel):
    """Chunk text lives here; the embedding lives in Qdrant under the same id.

    Mongo is the source of truth for content — Qdrant holds a copy in its
    payload only so a search hit renders without a second round trip. If the
    two ever disagree, Mongo wins.
    """

    #: The OWNING workspace. Never changes, and is what a citation attributes to.
    workspace_id: str
    document_id: str
    chunk_index: int
    content: str
    section: str | None
    char_start: int
    char_end: int
    token_estimate: int
    filename: str
    created_at: datetime

    #: Workspaces granted read access to this chunk's document, denormalised
    #: from ``documentShares``.
    #:
    #: Denormalised on purpose, and it is the crux of the whole feature: the
    #: tenant predicate has to be evaluated INSIDE the vector query, and Qdrant
    #: cannot join against a grants collection. So the grant is materialised
    #: onto the points, and the two stores are kept in step by the share
    #: service. Empty for every chunk until somebody explicitly shares — which
    #: is what makes default isolation unchanged rather than merely unlikely.
    shared_with: list[str] = Field(default_factory=list)


class DocumentShare(MongoModel):
    """An explicit, opt-in grant of one document to one other workspace.

    Read-only, one document at a time, and revocable. The owning workspace is
    recorded separately from the document id so a revoke can be authorised
    without re-reading the document.
    """

    document_id: str
    #: The workspace that owns the document and is giving access away.
    owner_workspace_id: str
    #: The workspace being given read access.
    target_workspace_id: str
    granted_by: str
    created_at: datetime


class Citation(BaseModel):
    #: 1-based; matches the [n] marker in the assistant's text.
    index: int
    chunk_id: str
    document_id: str
    filename: str
    section: str | None
    chunk_index: int
    similarity: float
    #: The passage the answer was drawn from, so clicking a [n] marker can show
    #: the actual source text. Carried on the citation rather than joined from
    #: the retrieval trace at render time: the trace is nullable and holds every
    #: candidate, while this is self-contained and only ever the cited chunk.
    #: Defaulted because messages persisted before this field existed must
    #: still load.
    snippet: str = ""
    #: Name of the owning workspace when the passage came from a SHARED
    #: document, else None. A reader must be able to see that a claim rests on
    #: another workspace's document — a shared source that looks local is a
    #: silent widening of where an answer came from.
    shared_from: str | None = None


class RetrievalChunk(BaseModel):
    chunk_id: str
    document_id: str
    #: Echoed from the stored payload. Equal to the active workspace unless the
    #: chunk arrived through an explicit share, which the debug view labels.
    workspace_id: str
    #: Owning workspace name when this came from a share, else None.
    shared_from: str | None = None
    filename: str
    section: str | None
    chunk_index: int
    similarity: float
    vector_rank: int | None
    keyword_rank: int | None
    score: float
    preview: str


class RetrievalDebug(BaseModel):
    #: The workspace the vector query was actually filtered to.
    workspace_id: str
    query: str
    strategy: Literal["hybrid-rrf"] = "hybrid-rrf"
    candidate_count: int
    returned_count: int
    min_similarity: float
    latency_ms: int
    chunks: list[RetrievalChunk]


class UsageStats(BaseModel):
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    latency_ms: int
    tool_steps: int


class Conversation(MongoModel):
    workspace_id: str
    created_by: str | None
    title: str
    created_at: datetime
    updated_at: datetime


class Message(MongoModel):
    conversation_id: str
    workspace_id: str
    role: MessageRole
    content: str
    citations: list[Citation] = Field(default_factory=list)
    retrieval: RetrievalDebug | None = None
    usage: UsageStats | None = None
    status: MessageStatus
    error_message: str | None = None
    created_at: datetime


class ToolCall(MongoModel):
    workspace_id: str
    conversation_id: str | None
    message_id: str | None
    tool_name: str
    arguments: dict[str, Any]
    result: Any
    status: ToolCallStatus
    error_message: str | None
    latency_ms: int | None
    step_index: int
    created_at: datetime


class Task(MongoModel):
    workspace_id: str
    title: str
    details: str | None
    priority: TaskPriority
    due_date: str | None
    status: TaskStatus
    created_by_tool: bool
    created_at: datetime


class MatchedChunk(BaseModel):
    """One hybrid retrieval hit, assembled from Qdrant + Mongo."""

    chunk_id: str
    document_id: str
    #: The OWNING workspace, which is not necessarily the one that searched.
    workspace_id: str
    #: Owning workspace name when this came from a share, else None.
    shared_from: str | None = None
    filename: str
    section: str | None
    chunk_index: int
    content: str
    similarity: float
    vector_rank: int | None
    keyword_rank: int | None
    score: float
