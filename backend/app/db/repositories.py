"""Workspace-scoped data access.

MongoDB has no row-level security, so this layer is the substitute: a
repository is CONSTRUCTED with a workspace id and every query it issues injects
that id into the filter. Callers never assemble a filter themselves, so "forgot
to scope the query" stops being a mistake that is possible to make — the same
reasoning as the vector-store choke point in ``app/vector/qdrant.py``.

Writes set ``workspace_id`` from the constructor argument, never from caller
input, so a mis-scoped write is equally impossible.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from app.db.mongo import collections
from app.db.schema import (
    Chunk,
    Citation,
    Conversation,
    Document,
    DocumentStatus,
    Message,
    MessageRole,
    MessageStatus,
    RetrievalDebug,
    Task,
    TaskPriority,
    TaskStatus,
    ToolCall,
    ToolCallStatus,
    UsageStats,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return str(uuid.uuid4())


class WorkspaceRepository:
    """Every method is implicitly filtered to one workspace."""

    def __init__(self, workspace_id: str) -> None:
        if not workspace_id:
            raise ValueError("WorkspaceRepository requires a workspace_id")
        self.workspace_id = workspace_id
        self._c = collections()

    def _scoped(self, query: dict[str, Any] | None = None) -> dict[str, Any]:
        """Merge the OWNERSHIP predicate into a filter.

        The default for everything. Reads, writes, deletes and counts all use
        it, and nothing that mutates data may use anything else.
        """
        return {**(query or {}), "workspace_id": self.workspace_id}

    def _readable(self, query: dict[str, Any] | None = None) -> dict[str, Any]:
        """Merge the READ predicate: owned, or explicitly granted to us.

        Used by exactly one method — :meth:`text_search` — and deliberately
        named differently from ``_scoped`` so that widening a read can never be
        mistaken for the default. Mirrors ``_readable_filter`` in
        ``app/vector/qdrant.py``; the two arms of hybrid retrieval must agree on
        what is visible, or a shared document would surface through one arm and
        not the other.

        With no grants anywhere, ``shared_with`` is ``[]`` on every chunk, the
        second clause matches nothing, and this is exactly ``_scoped``.
        """
        return {
            **(query or {}),
            "$or": [
                {"workspace_id": self.workspace_id},
                {"shared_with": self.workspace_id},
            ],
        }

    # --- documents ---------------------------------------------------------

    async def list_documents(self) -> list[Document]:
        cursor = self._c.documents.find(self._scoped()).sort("created_at", -1)
        return [Document.model_validate(d) async for d in cursor]

    async def get_document(self, document_id: str) -> Document | None:
        doc = await self._c.documents.find_one(self._scoped({"_id": document_id}))
        return Document.model_validate(doc) if doc else None

    async def find_document_by_hash(self, content_hash: str) -> Document | None:
        doc = await self._c.documents.find_one(self._scoped({"content_hash": content_hash}))
        return Document.model_validate(doc) if doc else None

    async def insert_document(
        self,
        *,
        filename: str,
        mime_type: str,
        byte_size: int,
        content_hash: str,
        uploaded_by: str | None,
    ) -> Document:
        doc = Document(
            _id=_new_id(),
            workspace_id=self.workspace_id,
            filename=filename,
            mime_type=mime_type or "application/octet-stream",
            byte_size=byte_size,
            content_hash=content_hash,
            status="processing",
            error_message=None,
            chunk_count=0,
            uploaded_by=uploaded_by,
            created_at=_now(),
        )
        await self._c.documents.insert_one(doc.to_mongo())
        return doc

    async def update_document(
        self,
        document_id: str,
        *,
        status: DocumentStatus | None = None,
        error_message: str | None = ...,  # type: ignore[assignment]
        chunk_count: int | None = None,
    ) -> None:
        patch: dict[str, Any] = {}
        if status is not None:
            patch["status"] = status
        if error_message is not ...:
            patch["error_message"] = error_message
        if chunk_count is not None:
            patch["chunk_count"] = chunk_count
        if patch:
            await self._c.documents.update_one(self._scoped({"_id": document_id}), {"$set": patch})

    async def delete_document(self, document_id: str) -> bool:
        result = await self._c.documents.delete_one(self._scoped({"_id": document_id}))
        return result.deleted_count == 1

    async def count_documents(self, status: DocumentStatus | None = None) -> int:
        return await self._c.documents.count_documents(
            self._scoped({"status": status} if status else None)
        )

    # --- chunks ------------------------------------------------------------

    async def insert_chunks(self, chunks: list[Chunk]) -> None:
        if not chunks:
            return
        await self._c.chunks.insert_many([c.to_mongo() for c in chunks])

    async def delete_chunks_for_document(self, document_id: str) -> None:
        await self._c.chunks.delete_many(self._scoped({"document_id": document_id}))

    async def text_search(self, query: str, limit: int) -> list[tuple[Chunk, float]]:
        """Keyword arm of hybrid retrieval, using MongoDB's text index.

        The access predicate is part of the same query document, so the text
        search and the tenant filter are evaluated together by the server rather
        than the tenant being applied to an already-ranked list.

        This is the one read in the repository that uses ``_readable`` rather
        than ``_scoped``, matching the vector arm's ``_readable_filter``.
        """
        trimmed = query.strip()
        if not trimmed:
            return []

        cursor = (
            self._c.chunks.find(
                self._readable({"$text": {"$search": trimmed}}),
                {"score": {"$meta": "textScore"}},
            )
            .sort([("score", {"$meta": "textScore"})])
            .limit(limit)
        )

        out: list[tuple[Chunk, float]] = []
        async for row in cursor:
            score = float(row.pop("score", 0.0))
            out.append((Chunk.model_validate(row), score))
        return out

    # --- conversations -----------------------------------------------------

    async def latest_conversation(self) -> Conversation | None:
        cursor = self._c.conversations.find(self._scoped()).sort("updated_at", -1).limit(1)
        async for row in cursor:
            return Conversation.model_validate(row)
        return None

    async def get_conversation(self, conversation_id: str) -> Conversation | None:
        row = await self._c.conversations.find_one(self._scoped({"_id": conversation_id}))
        return Conversation.model_validate(row) if row else None

    async def create_conversation(self, title: str, created_by: str | None) -> Conversation:
        now = _now()
        conversation = Conversation(
            _id=_new_id(),
            workspace_id=self.workspace_id,
            created_by=created_by,
            title=(title[:120] or "New chat"),
            created_at=now,
            updated_at=now,
        )
        await self._c.conversations.insert_one(conversation.to_mongo())
        return conversation

    async def touch_conversation(self, conversation_id: str) -> None:
        await self._c.conversations.update_one(
            self._scoped({"_id": conversation_id}), {"$set": {"updated_at": _now()}}
        )

    # --- messages ----------------------------------------------------------

    async def list_messages(self, conversation_id: str, limit: int = 100) -> list[Message]:
        cursor = (
            self._c.messages.find(self._scoped({"conversation_id": conversation_id}))
            .sort("created_at", 1)
            .limit(limit)
        )
        return [Message.model_validate(m) async for m in cursor]

    async def insert_message(
        self, *, conversation_id: str, role: MessageRole, content: str, status: MessageStatus
    ) -> Message:
        message = Message(
            _id=_new_id(),
            conversation_id=conversation_id,
            workspace_id=self.workspace_id,
            role=role,
            content=content,
            status=status,
            created_at=_now(),
        )
        await self._c.messages.insert_one(message.to_mongo())
        return message

    async def complete_message(
        self,
        message_id: str,
        *,
        content: str,
        citations: list[Citation],
        retrieval: RetrievalDebug | None,
        usage: UsageStats | None,
    ) -> None:
        await self._c.messages.update_one(
            self._scoped({"_id": message_id}),
            {
                "$set": {
                    "content": content,
                    "citations": [c.model_dump() for c in citations],
                    "retrieval": retrieval.model_dump() if retrieval else None,
                    "usage": usage.model_dump() if usage else None,
                    "status": "complete",
                    "error_message": None,
                }
            },
        )

    async def fail_message(
        self, message_id: str, error_message: str, partial_content: str = ""
    ) -> None:
        await self._c.messages.update_one(
            self._scoped({"_id": message_id}),
            {
                "$set": {
                    "status": "failed",
                    "error_message": error_message[:500],
                    "content": partial_content,
                }
            },
        )

    async def count_messages(self) -> int:
        return await self._c.messages.count_documents(self._scoped())

    # --- tool calls --------------------------------------------------------

    async def list_tool_calls(self, limit: int = 100) -> list[ToolCall]:
        cursor = self._c.tool_calls.find(self._scoped()).sort("created_at", -1).limit(limit)
        return [ToolCall.model_validate(t) async for t in cursor]

    async def insert_tool_call(
        self,
        *,
        conversation_id: str | None,
        message_id: str | None,
        tool_name: str,
        arguments: dict[str, Any],
        result: Any,
        status: ToolCallStatus,
        error_message: str | None,
        latency_ms: int | None,
        step_index: int,
    ) -> None:
        call = ToolCall(
            _id=_new_id(),
            workspace_id=self.workspace_id,
            conversation_id=conversation_id,
            message_id=message_id,
            tool_name=tool_name[:128],
            arguments=arguments,
            result=result,
            status=status,
            error_message=error_message,
            latency_ms=latency_ms,
            step_index=step_index,
            created_at=_now(),
        )
        await self._c.tool_calls.insert_one(call.to_mongo())

    async def count_tool_calls(self) -> int:
        return await self._c.tool_calls.count_documents(self._scoped())

    async def tool_call_stats(self) -> list[dict[str, Any]]:
        """Success/failure counts and latency per tool, for the whole workspace.

        Aggregated by the database rather than by fetching rows and counting in
        Python: the tool log is append-only and unbounded, and an observability
        page must not get slower the longer the workspace has been in use.
        """
        pipeline = [
            {"$match": self._scoped()},
            {
                "$group": {
                    "_id": {"tool": "$tool_name", "status": "$status"},
                    "count": {"$sum": 1},
                    "avg_latency_ms": {"$avg": "$latency_ms"},
                }
            },
            {
                "$group": {
                    "_id": "$_id.tool",
                    "total": {"$sum": "$count"},
                    "by_status": {
                        "$push": {
                            "status": "$_id.status",
                            "count": "$count",
                            "avg_latency_ms": "$avg_latency_ms",
                        }
                    },
                }
            },
            {"$sort": {"total": -1}},
        ]
        return [row async for row in self._c.tool_calls.aggregate(pipeline)]

    async def recent_tool_failures(self, limit: int = 20) -> list[ToolCall]:
        """Only the calls that did not succeed, newest first.

        A separate query from ``list_tool_calls`` because the question "what is
        going wrong" should not require paging through everything that went
        right.
        """
        cursor = (
            self._c.tool_calls.find(self._scoped({"status": {"$ne": "success"}}))
            .sort("created_at", -1)
            .limit(limit)
        )
        return [ToolCall.model_validate(t) async for t in cursor]

    # --- observability -----------------------------------------------------

    async def recent_assistant_messages(self, limit: int = 50) -> list[Message]:
        """Completed and failed assistant turns, newest first.

        The per-request record: one row per question answered, carrying its own
        token counts, latency and retrieval trace. Streaming rows are excluded —
        a turn still in flight has no usage to report yet.
        """
        cursor = (
            self._c.messages.find(
                self._scoped({"role": "assistant", "status": {"$ne": "streaming"}})
            )
            .sort("created_at", -1)
            .limit(limit)
        )
        return [Message.model_validate(m) async for m in cursor]

    # --- tasks -------------------------------------------------------------

    async def list_tasks(self, status: TaskStatus | None = None, limit: int = 100) -> list[Task]:
        cursor = (
            self._c.tasks.find(self._scoped({"status": status} if status else None))
            .sort("created_at", -1)
            .limit(limit)
        )
        return [Task.model_validate(t) async for t in cursor]

    async def insert_task(
        self,
        *,
        title: str,
        details: str | None,
        priority: TaskPriority,
        due_date: str | None,
        created_by_tool: bool,
    ) -> Task:
        task = Task(
            _id=_new_id(),
            workspace_id=self.workspace_id,
            title=title,
            details=details,
            priority=priority,
            due_date=due_date,
            status="open",
            created_by_tool=created_by_tool,
            created_at=_now(),
        )
        await self._c.tasks.insert_one(task.to_mongo())
        return task

    async def count_tasks(self, status: TaskStatus | None = None) -> int:
        return await self._c.tasks.count_documents(
            self._scoped({"status": status} if status else None)
        )
