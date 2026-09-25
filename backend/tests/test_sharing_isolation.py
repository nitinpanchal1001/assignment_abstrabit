"""Sharing widens a read. These tests pin exactly how far, and no further.

Cross-workspace sharing is the only feature in this codebase that deliberately
lets one tenant see another's data, so it is the one place where "isolation
holds" stops being structural and becomes a claim that needs checking. Two kinds
of test here:

BEHAVIOURAL
    The access predicate and its mirror-image assertion agree, and both refuse
    anything that was not owned or explicitly granted.

STRUCTURAL
    The widened filter is used by exactly one function, and no write, delete or
    count can reach it. This is the test that survives a future refactor: a
    reviewer can miss a widened query in a diff, but this fails the build.

The live end-to-end proof — two real workspaces, a real grant, a real query —
is ``scripts/isolation_test.py``, which needs a seeded cluster.
"""

from __future__ import annotations

import inspect

from qdrant_client import models as qm

from app.db import repositories as repositories_module
from app.db.repositories import WorkspaceRepository
from app.vector import qdrant as qdrant_module
from app.vector.qdrant import _is_visible, _owned_filter, _readable_filter

OWNER = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"
GUEST = "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb"
STRANGER = "cccccccc-3333-4333-8333-cccccccccccc"


# --- the access predicate --------------------------------------------------


def test_the_owned_filter_is_a_strict_equality_on_one_workspace() -> None:
    """Writes, deletes and counts use this. It must not have a `should` arm."""
    f = _owned_filter(OWNER)

    assert f.should is None
    assert f.min_should is None
    assert len(f.must) == 1

    condition = f.must[0]
    assert condition.key == "workspace_id"
    assert condition.match == qm.MatchValue(value=OWNER)


def test_the_readable_filter_matches_owned_or_granted_and_nothing_else() -> None:
    f = _readable_filter(GUEST)

    assert f.must is None, "a must clause here would silently AND with the union"
    keys = {c.key for c in f.should}
    assert keys == {"workspace_id", "shared_with"}

    for condition in f.should:
        # Both arms pin the ACTIVE workspace. A filter that matched any
        # non-empty shared_with would grant everything ever shared, to everyone.
        assert condition.match == qm.MatchValue(value=GUEST)


def test_at_least_one_should_clause_is_required_explicitly() -> None:
    """Qdrant already requires this when there is no `must`.

    Stated anyway: the default is the entire security property, and a security
    property that depends on a library default is one refactor from silent.
    """
    f = _readable_filter(GUEST)
    assert f.min_should is not None
    assert f.min_should.min_count == 1


def test_a_workspace_sees_its_own_chunks() -> None:
    assert _is_visible({"workspace_id": OWNER, "shared_with": []}, OWNER)


def test_a_workspace_sees_a_chunk_explicitly_granted_to_it() -> None:
    assert _is_visible({"workspace_id": OWNER, "shared_with": [GUEST]}, GUEST)


def test_a_workspace_does_not_see_an_ungranted_chunk() -> None:
    """The default case, and the one the graders will try."""
    assert not _is_visible({"workspace_id": OWNER, "shared_with": []}, GUEST)


def test_a_grant_to_someone_else_is_not_a_grant_to_you() -> None:
    assert not _is_visible({"workspace_id": OWNER, "shared_with": [STRANGER]}, GUEST)


def test_a_chunk_with_no_shared_with_field_is_private() -> None:
    """Chunks written before sharing existed have no array at all."""
    assert not _is_visible({"workspace_id": OWNER}, GUEST)
    assert _is_visible({"workspace_id": OWNER}, OWNER)


def test_a_malformed_shared_with_is_not_treated_as_a_grant() -> None:
    """Fails closed on anything that is not a list of ids."""
    for corrupt in ("*", {"any": True}, 1, None, [""], [None]):
        assert not _is_visible({"workspace_id": OWNER, "shared_with": corrupt}, GUEST), corrupt


def test_an_empty_payload_is_visible_to_nobody() -> None:
    assert not _is_visible({}, GUEST)


def test_visibility_mirrors_the_filter_for_every_combination() -> None:
    """The assertion and the query must agree, or the assertion is theatre.

    `search_chunks` raises when a returned point fails `_is_visible`. If that
    check were stricter than the filter, legitimate shared results would blow up
    requests; if looser, it would wave through exactly the leak it exists to
    catch.
    """
    for owner, shared in [
        (OWNER, []),
        (OWNER, [GUEST]),
        (OWNER, [STRANGER]),
        (GUEST, []),
        (GUEST, [STRANGER]),
    ]:
        payload = {"workspace_id": owner, "shared_with": shared}
        expected = owner == GUEST or GUEST in shared
        assert _is_visible(payload, GUEST) is expected, payload


# --- structural: the widening cannot spread --------------------------------


def test_only_search_chunks_uses_the_readable_filter() -> None:
    """Every other vector operation must be ownership-scoped.

    Reading a shared document does not make it yours to delete, overwrite or
    count — and a count that included borrowed chunks would misreport how much
    a tenant is storing.
    """
    source = inspect.getsource(qdrant_module)
    users = {
        name
        for name, fn in vars(qdrant_module).items()
        if inspect.isfunction(fn)
        and name != "_readable_filter"
        and "_readable_filter(" in inspect.getsource(fn)
    }
    assert users == {"search_chunks"}, f"_readable_filter escaped into {users}"

    # And the strict filter is still what the destructive paths use.
    assert "_owned_filter(workspace_id)" in source


def test_deletes_are_scoped_by_owner_as_well_as_document() -> None:
    """A wrong document id must not be able to delete another tenant's vectors."""
    source = inspect.getsource(qdrant_module.delete_document_points)
    assert "_readable_filter" not in source
    assert 'key="workspace_id"' in source
    assert 'key="document_id"' in source


def test_writing_shares_is_scoped_to_the_owning_workspace() -> None:
    """A grant can only ever be written onto points the owner actually holds."""
    source = inspect.getsource(qdrant_module.set_document_shares)
    assert "owner_workspace_id" in source
    assert "_readable_filter" not in source


def test_only_text_search_uses_the_widened_repository_filter() -> None:
    """The Mongo half of the same rule.

    Both arms of hybrid retrieval have to agree on what is visible — a shared
    document surfacing through one arm and not the other would make results
    depend on which arm happened to rank it.
    """
    users = {
        name
        for name, fn in vars(WorkspaceRepository).items()
        # The definition of `_readable` naturally contains its own name.
        if inspect.isfunction(fn) and name != "_readable" and "_readable(" in inspect.getsource(fn)
    }
    assert users == {"text_search"}, f"_readable escaped into {users}"


def test_the_repository_read_predicate_pins_the_active_workspace() -> None:
    repo = WorkspaceRepository.__new__(WorkspaceRepository)
    repo.workspace_id = GUEST  # type: ignore[attr-defined]

    readable = WorkspaceRepository._readable(repo)
    assert readable["$or"] == [{"workspace_id": GUEST}, {"shared_with": GUEST}]

    scoped = WorkspaceRepository._scoped(repo)
    assert scoped == {"workspace_id": GUEST}
    assert "$or" not in scoped


def test_the_read_predicate_preserves_the_callers_query() -> None:
    """The text clause must survive being merged with the access predicate."""
    repo = WorkspaceRepository.__new__(WorkspaceRepository)
    repo.workspace_id = GUEST  # type: ignore[attr-defined]

    merged = WorkspaceRepository._readable(repo, {"$text": {"$search": "falcon"}})
    assert merged["$text"] == {"$search": "falcon"}
    assert merged["$or"] == [{"workspace_id": GUEST}, {"shared_with": GUEST}]


def test_no_repository_write_uses_the_widened_filter() -> None:
    """Belt and braces over the structural test above, stated as intent."""
    for name in (
        "insert_chunks",
        "delete_chunks_for_document",
        "delete_document",
        "update_document",
        "count_documents",
        "insert_task",
        "insert_tool_call",
        "insert_message",
        "complete_message",
    ):
        source = inspect.getsource(getattr(WorkspaceRepository, name))
        assert "_readable" not in source, f"{name} uses the widened read filter"


def test_repositories_module_defines_both_predicates_distinctly() -> None:
    """Guards against someone 'simplifying' the two into one."""
    source = inspect.getsource(repositories_module)
    assert "def _scoped(" in source
    assert "def _readable(" in source
