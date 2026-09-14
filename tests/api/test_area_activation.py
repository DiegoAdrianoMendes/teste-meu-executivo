"""Contratos de desbloqueio governado das areas juridicas do KLM."""
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.models.klm_operations import (
    AreaBlueprint,
    AreaBlueprintStatus,
    ExternalActionIntent,
    KlmAuditEvent,
)
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus

pytestmark = pytest.mark.usefixtures("active_subscription")


async def _area(client, area_key: str) -> dict:
    response = await client.get("/api/v1/klm/areas")
    assert response.status_code == 200, response.text
    return next(item for item in response.json() if item["areaKey"] == area_key)


async def _command(client, area_id: str, command: str):
    return await client.post(f"/api/v1/klm/areas/{area_id}/{command}")


async def _set_activation(client, area_id: str, enabled: bool, *, force: bool = False):
    return await client.patch(
        f"/api/v1/klm/areas/{area_id}/activation",
        json={"enabled": enabled, "forceOverPlan": force},
    )


async def _promote_to_platform_root(db_session, auth_context) -> User:
    actor = await db_session.get(User, auth_context.user_id)
    assert actor is not None
    actor.is_internal_staff = True
    actor.is_platform_admin = True
    actor.platform_role_package = "platform_owner"
    actor.platform_access_active = True
    await db_session.flush()
    return actor


async def _grant_platform_package(db_session, auth_context, package: str) -> User:
    actor = await db_session.get(User, auth_context.user_id)
    assert actor is not None
    actor.is_internal_staff = True
    actor.is_platform_admin = False
    actor.platform_access_active = True
    actor.platform_role_package = package
    actor.platform_permission_grants = []
    actor.platform_permission_denials = []
    await db_session.flush()
    return actor


@pytest.mark.asyncio
async def test_creation_rejects_deeply_empty_custom_area(client):
    created = await client.post(
        "/api/v1/klm/areas",
        json={
            "areaKey": f"custom_vazio_{uuid4().hex[:8]}",
            "name": "Area personalizada vazia",
            "blueprint": {
                "taxonomy": {"subthemes": [], "requiredFields": []},
                "documents": [],
                "funnel": [],
                "agentTeam": [],
                "gates": {},
            },
        },
    )

    assert created.status_code == 409
    detail = created.json()["detail"]
    assert "Blueprint incompleto" in detail
    assert "taxonomy.subthemes" in detail
    assert "documents" in detail
    assert "gates.externalActionsDisabled" in detail


@pytest.mark.asyncio
async def test_validation_rejects_blueprint_with_external_actions_enabled(client):
    dormant = await _area(client, "tributario")
    blueprint = dormant["blueprint"]
    blueprint["gates"]["externalActionsDisabled"] = False
    updated = await client.patch(
        f"/api/v1/klm/areas/{dormant['id']}", json={"blueprint": blueprint}
    )
    assert updated.status_code == 409
    assert "gates.externalActionsDisabled" in updated.json()["detail"]


@pytest.mark.asyncio
async def test_blueprint_cannot_replace_secretary_as_hermes_gateway(client):
    dormant = await _area(client, "bancario")
    blueprint = dormant["blueprint"]
    blueprint["routing"]["ownerBridgeDelegatorKey"] = "closer_bancario"

    response = await client.patch(
        f"/api/v1/klm/areas/{dormant['id']}", json={"blueprint": blueprint}
    )

    assert response.status_code == 409
    assert "routing.ownerBridgeDelegatorKey" in response.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("policyVersion", "agent-governance-v1.0.0"),
        ("maxDelegationDepth", 8),
    ],
)
async def test_blueprint_cannot_downgrade_governance_contract(client, field, value):
    dormant = await _area(client, "trabalhista")
    blueprint = dormant["blueprint"]
    blueprint["agentGovernance"][field] = value

    response = await client.patch(
        f"/api/v1/klm/areas/{dormant['id']}", json={"blueprint": blueprint}
    )

    assert response.status_code == 409
    assert f"agentGovernance.{field}" in response.json()["detail"]


@pytest.mark.asyncio
async def test_catalog_materialization_preserves_frozen_areas_and_strict_governance(client):
    health_before = await _area(client, "saude_suplementar")
    dormant = await _area(client, "previdenciario")

    response = await _command(client, dormant["id"], "materialize")

    assert response.status_code == 200, response.text
    materialized = response.json()
    assert materialized["status"] == "blueprint"
    assert materialized["readiness"] >= 90
    assert materialized["isActive"] is False
    blueprint = materialized["blueprint"]
    assert blueprint["taxonomy"]["subthemes"]
    assert blueprint["products"]
    assert blueprint["documents"]
    assert len(blueprint["funnel"]) >= 2
    assert blueprint["routing"]["coordinationAgentKey"] == "orquestrador"
    assert blueprint["routing"]["ownerBridgeAgentKey"] == "hermes"
    assert blueprint["routing"]["ownerBridgeDelegatorKey"] == "secretaria"
    assert blueprint["gates"]["humanHandoffCoordinator"] == "orquestrador"
    assert blueprint["agentGovernance"]["policyVersion"] == "agent-governance-v1.4.0"
    assert blueprint["agentGovernance"]["hermesCannotOrchestrate"] is True
    assert blueprint["agentGovernance"]["manualOverrideWins"] is True
    assert blueprint["agentGovernance"]["crossWorkspaceAccessDenied"] is True
    assert blueprint["gates"]["externalActionsDisabled"] is True

    health_after = await _area(client, "saude_suplementar")
    assert health_after["blueprint"] == health_before["blueprint"]
    assert health_after["status"] == health_before["status"] == "blueprint"
    assert health_after["isActive"] is health_before["isActive"] is False


@pytest.mark.asyncio
async def test_workspace_owner_cannot_unlock_even_after_validation(client):
    dormant = await _area(client, "trabalhista")
    validated = await _command(client, dormant["id"], "validate")
    assert validated.status_code == 200, validated.text

    one_click = await _set_activation(client, dormant["id"], True)
    legacy = await _command(client, dormant["id"], "activate")

    for response in (one_click, legacy):
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "AREA_UNLOCK_RESTRICTED"
    frozen = await _area(client, "trabalhista")
    assert frozen["isActive"] is False


@pytest.mark.asyncio
async def test_platform_root_unlocks_and_freezes_in_one_click_with_single_audit(
    client, db_session, auth_context
):
    await _promote_to_platform_root(db_session, auth_context)
    area = await _area(client, "trabalhista")

    first_unlock = await _set_activation(client, area["id"], True)
    second_unlock = await _set_activation(client, area["id"], True)
    assert first_unlock.status_code == 200, first_unlock.text
    assert first_unlock.json() == second_unlock.json()
    assert first_unlock.json()["status"] == "active"
    assert first_unlock.json()["isActive"] is True
    assert first_unlock.json()["readiness"] == 100
    assert first_unlock.json()["validatedByUserId"] == str(auth_context.user_id)

    first_freeze = await _set_activation(client, area["id"], False)
    second_freeze = await _set_activation(client, area["id"], False)
    assert first_freeze.status_code == 200, first_freeze.text
    assert first_freeze.json() == second_freeze.json()
    assert first_freeze.json()["status"] == "blueprint"
    assert first_freeze.json()["isActive"] is False

    event_types = list(
        (
            await db_session.scalars(
                select(KlmAuditEvent.event_type).where(
                    KlmAuditEvent.workspace_id == auth_context.workspace_id,
                    KlmAuditEvent.entity_id == area["id"],
                    KlmAuditEvent.event_type.in_(
                        {"area_blueprint_activated", "area_blueprint_deactivated"}
                    ),
                )
            )
        ).all()
    )
    assert sorted(event_types) == [
        "area_blueprint_activated",
        "area_blueprint_deactivated",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("package", ["onboarding", "support", "engineering"])
async def test_authorized_saas_staff_packages_can_unlock(
    client, db_session, auth_context, package
):
    await _grant_platform_package(db_session, auth_context, package)
    area = await _area(client, "bancario")

    response = await _set_activation(client, area["id"], True)

    assert response.status_code == 200, response.text
    assert response.json()["isActive"] is True


@pytest.mark.asyncio
async def test_area_commands_are_idempotent_and_each_preparation_is_audited_once(
    client, db_session, auth_context
):
    suffix = uuid4().hex[:8]
    created = await client.post(
        "/api/v1/klm/areas",
        json={"areaKey": f"custom_{suffix}", "name": "Area customizada"},
    )
    assert created.status_code == 201, created.text
    area = created.json()

    first_materialize = await _command(client, area["id"], "materialize")
    second_materialize = await _command(client, area["id"], "materialize")
    assert first_materialize.json() == second_materialize.json()

    first_validate = await _command(client, area["id"], "validate")
    second_validate = await _command(client, area["id"], "validate")
    assert first_validate.json() == second_validate.json()

    event_types = list(
        (
            await db_session.scalars(
                select(KlmAuditEvent.event_type).where(
                    KlmAuditEvent.workspace_id == auth_context.workspace_id,
                    KlmAuditEvent.entity_id == area["id"],
                    KlmAuditEvent.event_type.in_(
                        {"area_blueprint_materialized", "area_blueprint_validated"}
                    ),
                )
            )
        ).all()
    )
    assert sorted(event_types) == [
        "area_blueprint_materialized",
        "area_blueprint_validated",
    ]


@pytest.mark.asyncio
async def test_area_commands_do_not_cross_workspace_boundary(
    client, db_session, auth_context
):
    await _promote_to_platform_root(db_session, auth_context)
    suffix = uuid4().hex[:8]
    other_workspace = Workspace(
        name="Outro escritorio",
        slug=f"outro-escritorio-{suffix}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(other_workspace)
    await db_session.flush()
    outside_area = AreaBlueprint(
        workspace_id=other_workspace.id,
        area_key=f"custom_externo_{suffix}",
        name="Area externa",
        status=AreaBlueprintStatus.draft,
        version="v0.1.0-local",
        readiness=0,
        is_active=False,
        blueprint={},
    )
    db_session.add(outside_area)
    await db_session.flush()

    for command in ("materialize", "validate", "activate"):
        response = await _command(client, str(outside_area.id), command)
        assert response.status_code == 404
    response = await _set_activation(client, str(outside_area.id), True)
    assert response.status_code == 404

    await db_session.refresh(outside_area)
    assert outside_area.workspace_id == other_workspace.id
    assert outside_area.blueprint == {}
    assert outside_area.status == AreaBlueprintStatus.draft
    assert outside_area.is_active is False
    audit_count = await db_session.scalar(
        select(func.count(KlmAuditEvent.id)).where(
            KlmAuditEvent.workspace_id == other_workspace.id,
            KlmAuditEvent.entity_id == outside_area.id,
        )
    )
    assert audit_count == 0
    assert auth_context.workspace_id != other_workspace.id


@pytest.mark.asyncio
async def test_local_unlock_never_creates_external_intent_or_checks_delivery(
    client, db_session, auth_context, monkeypatch
):
    await _promote_to_platform_root(db_session, auth_context)

    def unexpected_external_check() -> bool:
        raise AssertionError("area activation must not inspect or trigger external delivery")

    monkeypatch.setattr(
        "app.services.klm_operations_service.external_delivery_allowed",
        unexpected_external_check,
    )
    dormant = await _area(client, "consumidor")

    response = await _set_activation(client, dormant["id"], True)
    assert response.status_code == 200, response.text

    intent_count = await db_session.scalar(
        select(func.count(ExternalActionIntent.id)).where(
            ExternalActionIntent.workspace_id == auth_context.workspace_id
        )
    )
    assert intent_count == 0
