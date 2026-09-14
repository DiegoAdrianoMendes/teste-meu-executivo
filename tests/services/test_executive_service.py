from __future__ import annotations

import pytest

from app.models.workspace_membership import WorkspaceRole
from app.schemas.executive import ExecutiveKey, ExecutiveThreadCreate, ExecutiveThreadMode
from app.services.executive_service import ExecutivePolicyViolation, ExecutiveService


def test_catalog_contains_eight_distinct_consultative_executives() -> None:
    profiles = ExecutiveService.list_profiles()
    assert [profile.key for profile in profiles] == list(ExecutiveKey)
    assert len(profiles) == 8
    assert all(profile.consultative_only for profile in profiles)
    assert all(profile.mention == f"@{profile.key.value.upper()}" for profile in profiles)
    assert all("send_message" in profile.forbidden_capabilities for profile in profiles)


@pytest.mark.parametrize("role", [WorkspaceRole.workspace_owner, "workspace_admin"])
def test_owner_and_admin_are_authorized(role) -> None:
    ExecutiveService.require_owner_or_admin(role)


@pytest.mark.parametrize("role", [None, "workspace_member", "workspace_operator"])
def test_non_admin_roles_fail_closed(role) -> None:
    with pytest.raises(ExecutivePolicyViolation) as captured:
        ExecutiveService.require_owner_or_admin(role)
    assert captured.value.code == "EXECUTIVE_ACCESS_DENIED"


def test_ceo_is_always_present_and_mentions_add_executives() -> None:
    selection = ExecutiveService.normalize_participants(
        [ExecutiveKey.cro], message_body="Revise com @CFO e @cio."
    )
    assert selection.participants == (
        ExecutiveKey.ceo,
        ExecutiveKey.cfo,
        ExecutiveKey.cro,
        ExecutiveKey.cio,
    )
    assert selection.mentioned == (ExecutiveKey.cfo, ExecutiveKey.cio)


def test_todos_selects_the_whole_council_without_duplicates() -> None:
    selection = ExecutiveService.normalize_participants(
        [ExecutiveKey.ceo, ExecutiveKey.cfo], message_body="Quero ouvir @todos e @CFO."
    )
    assert selection.participants == tuple(ExecutiveKey)
    assert selection.all_requested is True


def test_direct_thread_rejects_more_than_one_specialist() -> None:
    payload = ExecutiveThreadCreate(
        mode=ExecutiveThreadMode.direct,
        selected_executives=[ExecutiveKey.cfo, ExecutiveKey.cro],
    )
    with pytest.raises(ExecutivePolicyViolation) as captured:
        ExecutiveService.validate_thread_create(payload)
    assert captured.value.code == "DIRECT_THREAD_PARTICIPANTS_INVALID"


def test_thread_normalization_injects_ceo() -> None:
    payload = ExecutiveThreadCreate(selected_executives=[ExecutiveKey.cfo])
    validated = ExecutiveService.validate_thread_create(payload)
    assert validated.selected_executives == [ExecutiveKey.ceo, ExecutiveKey.cfo]
    assert validated.visibility.value == "private"


@pytest.mark.parametrize(
    "action", ["send_message", "alter-budget", "protocol petition", "move_money"]
)
def test_mutating_actions_always_require_a_human(action: str) -> None:
    with pytest.raises(ExecutivePolicyViolation) as captured:
        ExecutiveService.require_consultative_action(action)
    assert captured.value.code == "EXECUTIVE_ACTION_REQUIRES_HUMAN"


def test_policy_forbids_fake_provider_fallback() -> None:
    snapshot = ExecutiveService.policy_snapshot()
    assert snapshot["ceoAlwaysPresent"] is True
    assert snapshot["externalActionsAllowed"] is False
    assert snapshot["providerFallbackAllowed"] is False
    assert snapshot["simulatedResponsesAllowed"] is False


def test_response_schema_serializes_camel_case() -> None:
    profile = ExecutiveService.get_profile("cfo")
    payload = profile.model_dump(by_alias=True)
    assert "domainManifest" in payload
    assert "allowedReadCapabilities" in payload
    assert payload["consultativeOnly"] is True


def test_share_target_must_be_active_admin_in_same_workspace() -> None:
    ExecutiveService.require_share_target(
        actor_workspace_id="workspace-a",
        target_workspace_id="workspace-a",
        target_membership_status="active",
        target_role="workspace_admin",
    )
    with pytest.raises(ExecutivePolicyViolation) as cross_workspace:
        ExecutiveService.require_share_target(
            actor_workspace_id="workspace-a",
            target_workspace_id="workspace-b",
            target_membership_status="active",
            target_role="workspace_admin",
        )
    assert cross_workspace.value.code == "EXECUTIVE_CROSS_WORKSPACE_SHARE_DENIED"

    with pytest.raises(ExecutivePolicyViolation) as inactive:
        ExecutiveService.require_share_target(
            actor_workspace_id="workspace-a",
            target_workspace_id="workspace-a",
            target_membership_status="invited",
            target_role="workspace_admin",
        )
    assert inactive.value.code == "EXECUTIVE_SHARE_TARGET_DENIED"
