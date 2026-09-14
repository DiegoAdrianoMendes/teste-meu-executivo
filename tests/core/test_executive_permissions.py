from app.core.rbac import ROLE_DEFINITIONS
from app.models.workspace_membership import WorkspaceRole


def test_executive_permissions_are_restricted_to_owner_and_admin() -> None:
    expected = {
        ("executives", "view"),
        ("executives", "chat"),
        ("executives", "share"),
        ("executives", "reports"),
    }

    for role in (WorkspaceRole.workspace_owner, WorkspaceRole.workspace_admin):
        actual = {
            (permission.module, permission.action)
            for permission in ROLE_DEFINITIONS[role].permissions
        }
        assert expected <= actual

    for role, definition in ROLE_DEFINITIONS.items():
        if role in {WorkspaceRole.workspace_owner, WorkspaceRole.workspace_admin}:
            continue
        assert not any(
            permission.module == "executives" for permission in definition.permissions
        )
