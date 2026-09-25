"""Automated proof of tenant isolation against the SHARED Qdrant collection.

Run with::

    uv run python -m scripts.isolation_test    # after seeding

Postgres would give row-level security as a database-enforced second layer.
Qdrant and MongoDB do not, so isolation here is enforced entirely by
application code — which makes an automated check of that code the load-bearing
evidence rather than a nice-to-have. This asserts:

1. Both workspaces' vectors really do live in ONE Qdrant collection.
   (Otherwise "isolation" would be trivially true and meaningless.)
2. Retrieval in the owning workspace returns that workspace's chunks.
3. Asking workspace B for a fact that exists only in workspace A returns
   nothing from A — the cross-tenant leak test.
4. The raw vector search is filtered server-side, not by the fusion step.
5. A user with no membership cannot obtain a scoped repository at all.
6. Explicit sharing is opt-in, scoped to ONE document, attributed to its owner,
   and fully reversible — and grants no other access while it is in force.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import UTC, datetime

from app import errors
from app.auth.workspace import list_workspaces, require_workspace
from app.db.mongo import close_client, collections
from app.db.repositories import WorkspaceRepository
from app.db.schema import Document, PublicUser
from app.gemini.embed import embed_query
from app.rag.retrieve import retrieve_chunks
from app.sharing import grant_share, revoke_share
from app.vector.qdrant import close_client as close_qdrant
from app.vector.qdrant import count_all_points, count_workspace_points, search_chunks

EMAIL = os.environ.get("SEED_EMAIL", "reviewer@example.com").lower()

#: A phrase that exists only in the Northwind corpus.
SECRET_QUERY = "What is Project FALCON-7 and what are its rules?"

failures = 0


def check(passed: bool, label: str, detail: str = "") -> None:
    global failures
    mark = "✓" if passed else "✗"
    print(f"  {mark} {label}" + (f" — {detail}" if detail else ""))
    if not passed:
        failures += 1


async def main() -> int:
    print("\nWorkspace isolation test\n")

    users = collections().users
    user_row = await users.find_one({"email": EMAIL})
    if user_row is None:
        print(f"\n✗ No seeded user {EMAIL}. Run the seed first.\n", file=sys.stderr)
        return 1

    user = PublicUser(
        id=user_row["_id"], email=user_row["email"], display_name=user_row["display_name"]
    )

    workspaces = await list_workspaces(user.id)
    northwind = next((w for w in workspaces if "Northwind" in w.name), None)
    meridian = next((w for w in workspaces if "Meridian" in w.name), None)

    if northwind is None or meridian is None:
        print("\n✗ Expected both seeded workspaces. Run the seed first.\n", file=sys.stderr)
        return 1

    # --- 1. The store really is shared -------------------------------------
    print("Shared vector store")
    a_points = await count_workspace_points(northwind.id)
    b_points = await count_workspace_points(meridian.id)
    total = await count_all_points()

    check(
        a_points > 0 and b_points > 0,
        "both workspaces have vectors in the same Qdrant collection",
        f"Northwind={a_points}, Meridian={b_points}, total={total}",
    )
    check(
        total >= a_points + b_points,
        "the collection holds both workspaces at once",
        f"{total} >= {a_points} + {b_points}",
    )

    # --- 2. Own-workspace retrieval works ----------------------------------
    print("\nRetrieval in the owning workspace")
    own = await retrieve_chunks(repo=WorkspaceRepository(northwind.id), query=SECRET_QUERY)

    check(len(own.chunks) > 0, "Northwind returns its own chunks", f"{len(own.chunks)} hits")
    check(
        all(c.workspace_id == northwind.id for c in own.chunks),
        "every returned chunk is stamped with the Northwind workspace id",
    )
    check(
        any("FALCON-7" in c.content.upper() for c in own.chunks),
        "the FALCON-7 passage is actually retrievable",
    )

    # --- 3. The leak test ---------------------------------------------------
    print("\nCross-workspace leak test")
    cross = await retrieve_chunks(repo=WorkspaceRepository(meridian.id), query=SECRET_QUERY)

    leaked = [c for c in cross.chunks if c.workspace_id != meridian.id]
    check(
        not leaked,
        "asking Meridian for a Northwind-only fact leaks nothing",
        f"{len(leaked)} foreign chunk(s)",
    )
    check(
        not any("FALCON-7" in c.content.upper() for c in cross.chunks),
        "no returned chunk mentions FALCON-7",
    )

    # --- 4. The filter is server-side, not a post-filter --------------------
    dense = await search_chunks(
        workspace_id=meridian.id, embedding=await embed_query(SECRET_QUERY), limit=20
    )
    check(
        all(h.payload.get("workspace_id") == meridian.id for h in dense),
        "the raw Qdrant search is filtered server-side",
        f"{len(dense)} hits, all Meridian",
    )

    # --- 5. Non-member access ------------------------------------------------
    print("\nNon-member access")
    outsider_id = str(uuid.uuid4())
    await users.insert_one(
        {
            "_id": outsider_id,
            "email": f"outsider+{outsider_id[:8]}@example.com",
            "password_hash": "x",
            "display_name": "outsider",
            "created_at": datetime.now(UTC),
        }
    )
    outsider = PublicUser(id=outsider_id, email="outsider@example.com", display_name="outsider")

    try:
        check(not await list_workspaces(outsider_id), "an outsider belongs to no workspaces")

        refused = False
        try:
            await require_workspace(outsider, northwind.id)
        except errors.AppError as exc:
            refused = exc.code == "workspace/not-found"
        check(refused, "require_workspace() refuses a non-member with 404")
    finally:
        await users.delete_one({"_id": outsider_id})

    # --- 6. Explicit sharing: opt-in, effective, and revocable ---------------
    #
    # The riskiest feature in the app gets the strictest check. The order below
    # is the argument: nothing is visible by default, a grant makes exactly one
    # document visible and nothing else, and revoking puts it back.
    print("\nCross-workspace sharing")

    shared_doc = await collections().documents.find_one(
        {"workspace_id": northwind.id, "status": "ready"}
    )
    if shared_doc is None:
        check(False, "a ready Northwind document exists to share")
    else:
        document = Document.model_validate(shared_doc)
        probe = f"What does {document.filename} say?"

        before = await retrieve_chunks(repo=WorkspaceRepository(meridian.id), query=probe)
        check(
            not [c for c in before.chunks if c.document_id == document.id],
            "before any grant, Meridian cannot retrieve the Northwind document",
        )

        await grant_share(document=document, target_workspace_id=meridian.id, granted_by=user.id)
        try:
            during = await retrieve_chunks(repo=WorkspaceRepository(meridian.id), query=probe)
            granted = [c for c in during.chunks if c.document_id == document.id]

            check(
                bool(granted),
                "after the grant, Meridian can retrieve the shared document",
                f"{len(granted)} chunk(s)",
            )
            check(
                all(c.workspace_id == northwind.id for c in granted),
                "shared chunks stay attributed to the OWNING workspace",
            )
            check(
                all(c.shared_from == northwind.name for c in granted),
                "shared chunks are labelled with the owner's name for citation",
            )

            # The grant must not widen anything except the one document.
            still_secret = await retrieve_chunks(
                repo=WorkspaceRepository(meridian.id), query=SECRET_QUERY
            )
            foreign = [
                c
                for c in still_secret.chunks
                if c.workspace_id != meridian.id and c.document_id != document.id
            ]
            check(
                not foreign,
                "the grant widens access to that document ONLY, not the workspace",
                f"{len(foreign)} other foreign chunk(s)",
            )

            # Ownership semantics are unchanged: a borrowed document is not
            # counted as, or deletable by, the borrowing workspace.
            meridian_owned = await count_workspace_points(meridian.id)
            check(
                meridian_owned == b_points,
                "a shared document does not count towards the borrower's vectors",
                f"{meridian_owned} == {b_points}",
            )
            check(
                await WorkspaceRepository(meridian.id).get_document(document.id) is None,
                "the borrowing workspace cannot resolve the document as its own",
            )
        finally:
            await revoke_share(document=document, target_workspace_id=meridian.id)

        after = await retrieve_chunks(repo=WorkspaceRepository(meridian.id), query=probe)
        check(
            not [c for c in after.chunks if c.document_id == document.id],
            "after revoking, Meridian can no longer retrieve the document",
        )

    # --- 7. Unknown workspace returns nothing --------------------------------
    print("\nFail-loudly guarantee")
    bogus = await search_chunks(
        workspace_id=str(uuid.uuid4()),
        embedding=[0.0] * int(os.environ.get("EMBEDDING_DIMENSIONS", "1536")),
        limit=5,
    )
    check(not bogus, "a workspace with no vectors returns nothing", f"{len(bogus)} hits")

    print(
        "\n✓ All isolation checks passed.\n"
        if failures == 0
        else f"\n✗ {failures} isolation check(s) FAILED.\n"
    )
    return 0 if failures == 0 else 1


async def run() -> int:
    try:
        return await main()
    finally:
        await close_client()
        await close_qdrant()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
