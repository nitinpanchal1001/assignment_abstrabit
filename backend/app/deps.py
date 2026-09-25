"""FastAPI dependencies."""

from __future__ import annotations

from typing import Annotated

from fastapi import Cookie, Depends, Query

from app import errors
from app.auth.session import SESSION_COOKIE, verify_session_token
from app.auth.workspace import WorkspaceContext, require_workspace
from app.db.mongo import collections
from app.db.schema import PublicUser


async def current_user(
    session: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> PublicUser:
    """Resolve the signed-in user, or raise 401.

    The user is re-read from Mongo rather than trusted from the token's claims:
    a deleted user holding a still-valid JWT must stop working immediately.
    """
    user_id = verify_session_token(session)
    if not user_id:
        raise errors.not_signed_in()

    row = await collections().users.find_one({"_id": user_id}, {"password_hash": 0})
    if row is None:
        raise errors.not_signed_in()

    return PublicUser(id=row["_id"], email=row["email"], display_name=row["display_name"])


CurrentUser = Annotated[PublicUser, Depends(current_user)]


async def workspace_context(
    user: CurrentUser,
    workspace_id: Annotated[str | None, Query(alias="workspaceId")] = None,
) -> WorkspaceContext:
    """Authorise a workspace taken from the query string."""
    return await require_workspace(user, workspace_id)


WorkspaceDep = Annotated[WorkspaceContext, Depends(workspace_context)]
