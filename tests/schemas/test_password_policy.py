import pytest
from pydantic import ValidationError

from app.core.password_policy import validate_password
from app.schemas.identity import (
    InvitationAcceptRequest,
    PasswordChangeRequest,
    ResetPasswordRequest,
)
from app.schemas.user import RegisterRequest, UserCreate
from app.schemas.workspace import TeamMemberCreate, WorkspaceSetupRequest


@pytest.mark.parametrize(
    "password",
    [
        "a" * 10,
        "a" * 72,
        "á" * 5,
        "á" * 36,
    ],
)
def test_password_policy_accepts_10_to_72_utf8_bytes(password: str) -> None:
    assert validate_password(password) == password


@pytest.mark.parametrize(
    "password",
    [
        "a" * 9,
        "a" * 73,
        "á" * 4,
        "á" * 37,
    ],
)
def test_password_policy_rejects_values_outside_utf8_byte_bounds(password: str) -> None:
    with pytest.raises(ValueError):
        validate_password(password)


@pytest.mark.parametrize(
    "build",
    [
        lambda password: RegisterRequest(
            name="Pessoa",
            email="pessoa@example.com",
            workspaceName="Workspace",
            password=password,
        ),
        lambda password: InvitationAcceptRequest(
            token="t" * 32,
            password=password,
        ),
        lambda password: ResetPasswordRequest(
            token="t" * 32,
            newPassword=password,
        ),
        lambda password: PasswordChangeRequest(
            currentPassword="senha-atual",
            newPassword=password,
        ),
        lambda password: WorkspaceSetupRequest(
            workspaceName="Workspace",
            adminName="Pessoa",
            adminEmail="pessoa@example.com",
            adminPassword=password,
        ),
        lambda password: UserCreate(
            name="Pessoa",
            email="pessoa@example.com",
            password=password,
        ),
    ],
)
def test_all_identity_definition_contracts_apply_central_policy(build) -> None:
    with pytest.raises(ValidationError):
        build("curta123")
    build("SenhaValida#2026")


def test_team_admin_contract_rejects_initial_password() -> None:
    with pytest.raises(ValidationError):
        TeamMemberCreate(
            name="Pessoa",
            email="pessoa@example.com",
            password="SenhaValida#2026",
        )
