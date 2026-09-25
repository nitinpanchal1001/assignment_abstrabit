"""Authentication endpoints."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Response
from pymongo.errors import DuplicateKeyError

from app import errors
from app.api_models import CredentialsIn, SessionOut, SignupIn, UserOut, WorkspaceOut
from app.auth.password import fake_verify, hash_password, verify_password
from app.auth.session import clear_session_cookie, set_session_cookie
from app.auth.workspace import create_workspace, list_workspaces
from app.db.mongo import collections
from app.deps import CurrentUser

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/signup", response_model=UserOut, status_code=201)
async def signup(payload: SignupIn, response: Response) -> UserOut:
    email = payload.email.strip().lower()
    users = collections().users

    user_id = str(uuid.uuid4())
    document = {
        "_id": user_id,
        "email": email,
        "password_hash": await hash_password(payload.password),
        "display_name": email.split("@")[0],
        "created_at": datetime.now(UTC),
    }

    try:
        await users.insert_one(document)
    except DuplicateKeyError:
        # The unique index arbitrates, rather than a check-then-insert which
        # would race two simultaneous signups for the same address.
        raise errors.email_taken() from None

    # Give every new account somewhere to work, so the first screen after
    # signup is usable rather than an empty state.
    await create_workspace("My Workspace", user_id)

    set_session_cookie(response, user_id)
    return UserOut(id=user_id, email=email, display_name=document["display_name"])


@router.post("/login", response_model=UserOut)
async def login(payload: CredentialsIn, response: Response) -> UserOut:
    email = payload.email.strip().lower()
    row = await collections().users.find_one({"email": email})

    if row is None:
        # Burn comparable time so "unknown email" and "wrong password" are not
        # distinguishable by response latency, which would be an account
        # enumeration oracle.
        await fake_verify()
        raise errors.invalid_credentials()

    if not await verify_password(payload.password, row["password_hash"]):
        raise errors.invalid_credentials()

    set_session_cookie(response, row["_id"])
    return UserOut(id=row["_id"], email=row["email"], display_name=row["display_name"])


@router.post("/logout", status_code=204)
async def logout(response: Response) -> Response:
    clear_session_cookie(response)
    response.status_code = 204
    return response


@router.get("/session", response_model=SessionOut)
async def session(user: CurrentUser) -> SessionOut:
    """Everything the shell needs on load: who you are and your workspaces."""
    workspaces = await list_workspaces(user.id)
    return SessionOut(
        user=UserOut(id=user.id, email=user.email, display_name=user.display_name),
        workspaces=[WorkspaceOut.of(w) for w in workspaces],
        active_workspace_id=workspaces[0].id if workspaces else None,
    )
