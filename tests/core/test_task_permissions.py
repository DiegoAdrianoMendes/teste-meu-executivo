from app.core.rbac import Permission, get_effective_permissions, known_permission_keys
from app.models.workspace_membership import WorkspaceRole


def _keys(role: WorkspaceRole, *, grants=(), denials=()) -> set[tuple[str, str]]:
    return {
        (permission.module, permission.action)
        for permission in get_effective_permissions(role, grants=grants, denials=denials)
    }


def test_task_permission_catalog_exposes_every_granular_action() -> None:
    expected = {
        ("tasks", "view"),
        ("tasks", "create"),
        ("tasks", "edit"),
        ("tasks", "assign"),
        ("tasks", "review"),
        ("tasks", "complete"),
        ("tasks", "comment"),
    }
    assert expected <= known_permission_keys()


def test_legacy_kanban_permissions_are_derived_without_widening_viewers() -> None:
    operator = _keys(WorkspaceRole.operator)
    viewer = _keys(WorkspaceRole.viewer)

    assert ("tasks", "view") in operator
    assert ("tasks", "create") in operator
    assert ("tasks", "assign") in operator
    assert ("tasks", "review") in operator
    assert ("tasks", "complete") in operator
    assert ("tasks", "comment") in operator
    assert ("tasks", "view") in viewer
    assert ("tasks", "edit") not in viewer
    assert ("tasks", "review") not in viewer


def test_task_and_legacy_denials_keep_precedence() -> None:
    no_kanban_view = _keys(
        WorkspaceRole.operator,
        denials=(Permission("kanban", "view"),),
    )
    no_review = _keys(
        WorkspaceRole.operator,
        denials=(Permission("tasks", "review"),),
    )

    assert ("tasks", "view") not in no_kanban_view
    assert ("tasks", "edit") in no_kanban_view
    assert ("tasks", "review") not in no_review
    assert ("tasks", "edit") in no_review
