from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.billing import BillingInterval, Plan, PlanTier, Subscription, SubscriptionStatus
from app.models.entitlement import EntitlementKey, PlanEntitlement, WorkspaceMonthlyUsage
from app.models.flow import Flow
from app.models.omnichannel_v6 import OmnichannelChannelConnection
from app.models.platform_audit_event import PlatformAuditEvent
from app.models.storage_object import StorageObject
from app.models.user import User
from app.models.whatsapp import (
    ConnectionType,
    WaConnectionStatus,
    WhatsAppConnection,
)
from app.models.whatsapp_campaign import (
    CampaignRecipientStatus,
    CampaignStatus,
    WhatsAppCampaign,
    WhatsAppCampaignDeliveryEvent,
    WhatsAppCampaignRecipient,
)
from app.models.workspace import Workspace
from app.schemas.playbook import PlaybookCreate, PlaybookScope
from app.services.platform_audit_event_service import PlatformAuditEventService
from tests.identity_helpers import invitation_headers


def _onboarding_playbook_payload(area_key: str) -> dict:
    return PlaybookCreate(
        name=f"Política inicial - {area_key}",
        description="Política publicada para governar o onboarding e os agentes.",
        scope=PlaybookScope(
            areas=[area_key],
            channels=["whatsapp"],
            stages=["triagem", "qualificacao", "proposta", "fechamento"],
            agents=["orquestrador", "sdr", "juridico", "closer", "hermes"],
            offers=["servicos_advocaticios"],
        ),
        autonomy_mode="ai_triage_human_meeting",
    ).model_dump(mode="json", by_alias=True)


async def _promote_platform_root(db_session, auth_context) -> User:
    actor = await db_session.get(User, auth_context.user_id)
    assert actor is not None
    actor.is_internal_staff = True
    actor.is_platform_admin = True
    actor.platform_role_package = "platform_owner"
    actor.platform_access_active = True
    await db_session.flush()
    return actor


async def _publish_playbook(client, area_key: str) -> str:
    created = await client.post(
        "/api/v1/playbooks",
        json=_onboarding_playbook_payload(area_key),
    )
    assert created.status_code == 201, created.text
    playbook_id = created.json()["id"]
    simulation = await client.post(
        f"/api/v1/playbooks/{playbook_id}/simulations",
        json={
            "scenario": "Triagem governada do onboarding",
            "customerMessage": "Preciso entender meu caso e os próximos passos.",
            "context": {"channel": "whatsapp", "localOnly": True},
        },
    )
    assert simulation.status_code == 200, simulation.text
    assert simulation.json()["status"] == "passed"
    for action in ("submit", "approve", "publish"):
        response = await client.post(
            f"/api/v1/playbooks/{playbook_id}/{action}",
            json={"reason": f"Política de onboarding: {action}."},
        )
        assert response.status_code == 200, response.text
    return playbook_id


@pytest.mark.asyncio
async def test_get_admin_control_plane_returns_practice_areas_and_gemma(
    client,
    active_subscription,
):
    response = await client.get("/api/v1/admin/settings/control-plane")

    assert response.status_code == 200
    payload = response.json()
    assert payload["practiceAreas"]
    assert payload["aiGovernance"]["gemmaEnabled"] is True
    assert any(
        model["key"] == "google/gemma3@gemma-3-4b-it"
        for model in payload["aiGovernance"]["availableModels"]
    )


@pytest.mark.asyncio
async def test_branding_control_plane_updates_versioned_effective_identity(
    client,
    active_subscription,
):
    updated = await client.patch(
        "/api/v1/admin/settings/control-plane",
        json={
            "branding": {
                "brandName": "KLM Premium",
                "logoUrl": "/brand/logo-premium.svg",
                "iconUrl": "/brand/icon-premium.svg",
                "primaryColor": "#C9A227",
                "accentColor": "#111111",
                "layoutDensity": "compact",
            }
        },
    )
    assert updated.status_code == 200, updated.text

    effective = await client.get("/api/v1/configurations/brand.identity/effective")
    assert effective.status_code == 200, effective.text
    brand = effective.json()["effective"]
    assert brand["name"] == "KLM Premium"
    assert brand["assets"]["logoLightUrl"] == "/brand/logo-premium.svg"
    assert brand["assets"]["faviconUrl"] == "/brand/icon-premium.svg"
    assert brand["colors"]["primary"] == "#C9A227"
    assert brand["colors"]["accent"] == "#111111"
    assert brand["cssVariables"]["--primary"] == "46 68% 47%"
    assert [layer["scope"] for layer in effective.json()["layers"]] == [
        "contract",
        "workspace",
    ]


@pytest.mark.asyncio
async def test_workspace_operations_cannot_unlock_area_but_can_update_an_authorized_area(
    client, db_session, auth_context, active_subscription
):
    current = await client.get("/api/v1/workspaces/settings/operations")
    assert current.status_code == 200
    payload = current.json()
    assert payload["areaActivations"]

    unauthorized = payload["areaActivations"]
    unauthorized[0]["enabled"] = True

    denied = await client.patch(
        "/api/v1/workspaces/settings/operations",
        json={"areaActivations": unauthorized},
    )
    assert denied.status_code == 403

    await _promote_platform_root(db_session, auth_context)
    areas = (await client.get("/api/v1/klm/areas")).json()
    area = next(item for item in areas if item["areaKey"] == unauthorized[0]["key"])
    unlocked = await client.patch(
        f"/api/v1/klm/areas/{area['id']}/activation",
        json={"enabled": True},
    )
    assert unlocked.status_code == 200, unlocked.text

    refreshed = (await client.get("/api/v1/workspaces/settings/operations")).json()
    updated = refreshed["areaActivations"]
    updated[0]["followUpProfile"] = "assertive"
    updated[0]["humanInterventionMode"] = "ai_with_human_approval"

    response = await client.patch(
        "/api/v1/workspaces/settings/operations",
        json={"areaActivations": updated},
    )

    assert response.status_code == 200
    body = response.json()
    first = body["areaActivations"][0]
    assert first["enabled"] is True
    assert first["followUpProfile"] == "assertive"
    assert first["humanInterventionMode"] == "ai_with_human_approval"


@pytest.mark.asyncio
async def test_workspace_settings_exposes_plan_summary(client, db_session, auth_context):
    plan = Plan(
        name="Plano Pro",
        tier=PlanTier.pro,
        price_monthly_cents=69700,
        price_annually_cents=669120,
        max_users=3,
        max_matters=None,
        max_tokens_per_month=2_000_000,
        max_ai_agents=5,
        features={
            "maxConnectedWhatsapps": 2,
            "automationLimit": 4,
            "digitalSignature": True,
        },
        is_active=True,
        is_public=True,
        sort_order=1,
    )
    db_session.add(plan)
    await db_session.flush()

    subscription = Subscription(
        workspace_id=auth_context.workspace_id,
        plan_id=plan.id,
        status=SubscriptionStatus.active,
        interval=BillingInterval.monthly,
        current_period_start=datetime.now(timezone.utc),
        current_period_end=datetime.now(timezone.utc),
        amount_cents=69700,
        currency="BRL",
    )
    db_session.add(subscription)
    await db_session.flush()

    response = await client.get("/api/v1/admin/settings")

    assert response.status_code == 200
    payload = response.json()
    assert payload["planSummary"]["name"] == "Plano Pro"
    assert payload["planSummary"]["tier"] == "pro"
    assert payload["planSummary"]["maxUsers"] == 3
    assert payload["planSummary"]["maxConnectedWhatsapps"] == 2
    assert payload["planSummary"]["automationLimit"] == 4
    assert payload["planSummary"]["hasDigitalSignature"] is True
    assert payload["planSummary"]["seatsUsed"] == 1
    assert payload["planSummary"]["seatsRemaining"] == 2


@pytest.mark.asyncio
async def test_workspace_membership_respects_plan_limit(client, db_session, auth_context):
    plan = Plan(
        name="Plano Starter",
        tier=PlanTier.starter,
        price_monthly_cents=29700,
        price_annually_cents=285120,
        max_users=1,
        max_matters=200,
        max_tokens_per_month=500_000,
        max_ai_agents=2,
        features={},
        is_active=True,
        is_public=True,
        sort_order=1,
    )
    db_session.add(plan)
    await db_session.flush()

    subscription = Subscription(
        workspace_id=auth_context.workspace_id,
        plan_id=plan.id,
        status=SubscriptionStatus.active,
        interval=BillingInterval.monthly,
        current_period_start=datetime.now(timezone.utc),
        current_period_end=datetime.now(timezone.utc),
        amount_cents=29700,
        currency="BRL",
    )
    db_session.add(subscription)

    response = await client.post(
        "/api/v1/admin/team-members",
        json={
            "name": "Novo Integrante",
            "email": "novo@example.com",
            "workspaceRole": "operator",
        },
        headers=invitation_headers("plan-limit"),
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "ENT-003"
    assert detail["entitlement"] == "users"
    assert detail["usage"] == detail["limit"] == 1
    assert detail["remaining"] == 0


@pytest.mark.asyncio
async def test_workspace_plan_summary_endpoint_returns_consumption(
    client, db_session, auth_context
):
    plan = Plan(
        name="Business",
        tier=PlanTier.business,
        price_monthly_cents=0,
        price_annually_cents=0,
        max_users=None,
        max_matters=None,
        max_tokens_per_month=500,
        max_ai_agents=12,
        features={
            "maxConnectedWhatsapps": 6,
            "maxAudioMinutesPerMonth": 90,
            "successManager": True,
        },
        is_active=True,
        is_public=True,
        sort_order=3,
    )
    db_session.add(plan)
    await db_session.flush()

    db_session.add(
        Subscription(
            workspace_id=auth_context.workspace_id,
            plan_id=plan.id,
            status=SubscriptionStatus.active,
            interval=BillingInterval.monthly,
            current_period_start=datetime.now(timezone.utc),
            current_period_end=datetime.now(timezone.utc),
            amount_cents=0,
            currency="BRL",
        )
    )
    await db_session.flush()

    response = await client.get("/api/v1/workspaces/plan-summary")

    assert response.status_code == 200
    payload = response.json()
    assert payload["tier"] == "scale"
    assert payload["name"] == "Scale"
    assert payload["maxConnectedWhatsapps"] == 6
    assert payload["hasSuccessManager"] is True


@pytest.mark.asyncio
async def test_workspace_plan_summary_uses_typed_limits_and_real_usage(
    client,
    db_session,
    auth_context,
):
    plan = Plan(
        name="Plano tipado",
        tier=PlanTier.pro,
        price_monthly_cents=69700,
        price_annually_cents=669120,
        max_users=99,
        max_matters=None,
        max_tokens_per_month=999999,
        max_ai_agents=99,
        features={
            "maxAiConversationsPerMonth": 999,
            "maxAudioMinutesPerMonth": 999,
            "maxStorageBytes": 999,
            "maxFilesPerMonth": 999,
            "automationLimit": 999,
            "maxCampaignRecipientsPerMonth": 3,
            "digitalSignature": True,
            "calendar": False,
        },
        is_active=True,
        is_public=True,
        sort_order=2,
    )
    db_session.add(plan)
    await db_session.flush()
    db_session.add(
        Subscription(
            workspace_id=auth_context.workspace_id,
            plan_id=plan.id,
            status=SubscriptionStatus.active,
            interval=BillingInterval.monthly,
            current_period_start=datetime.now(UTC),
            current_period_end=datetime.now(UTC),
            amount_cents=69700,
            currency="BRL",
        )
    )
    db_session.add_all(
        [
            PlanEntitlement(
                plan_id=plan.id, entitlement=EntitlementKey.users, enabled=True, limit_quantity=2
            ),
            PlanEntitlement(
                plan_id=plan.id,
                entitlement=EntitlementKey.ai_conversations,
                enabled=True,
                limit_quantity=10,
            ),
            PlanEntitlement(
                plan_id=plan.id,
                entitlement=EntitlementKey.audio_minutes,
                enabled=True,
                limit_quantity=30,
            ),
            PlanEntitlement(
                plan_id=plan.id,
                entitlement=EntitlementKey.storage_bytes,
                enabled=True,
                limit_quantity=1000,
            ),
            PlanEntitlement(
                plan_id=plan.id, entitlement=EntitlementKey.files, enabled=True, limit_quantity=3
            ),
            PlanEntitlement(
                plan_id=plan.id,
                entitlement=EntitlementKey.automations,
                enabled=True,
                limit_quantity=0,
            ),
            PlanEntitlement(
                plan_id=plan.id,
                entitlement=EntitlementKey.digital_signature,
                enabled=False,
                limit_quantity=None,
            ),
            PlanEntitlement(
                plan_id=plan.id,
                entitlement=EntitlementKey.calendar,
                enabled=True,
                limit_quantity=None,
            ),
        ]
    )
    month_start = datetime.now(UTC).date().replace(day=1)
    sent_at = datetime.now(UTC)
    previous_month = datetime(sent_at.year, sent_at.month, 1, tzinfo=UTC) - timedelta(seconds=1)
    connection = WhatsAppConnection(
        workspace_id=auth_context.workspace_id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        transport_mode="official_api",
        phone_number_id=f"plan-summary-{uuid4().hex[:12]}",
        status=WaConnectionStatus.connected,
    )
    db_session.add(connection)
    await db_session.flush()
    campaign = WhatsAppCampaign(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        title="Contagem mensal canônica",
        status=CampaignStatus.completed,
    )
    db_session.add(campaign)
    await db_session.flush()
    sent_recipient = WhatsAppCampaignRecipient(
        campaign_id=campaign.id,
        workspace_id=auth_context.workspace_id,
        phone="+5511900000001",
        status=CampaignRecipientStatus.delivered,
        sent_at=sent_at,
        send_context={},
    )
    delivered_recipient = WhatsAppCampaignRecipient(
        campaign_id=campaign.id,
        workspace_id=auth_context.workspace_id,
        phone="+5511900000002",
        status=CampaignRecipientStatus.read,
        sent_at=sent_at,
        send_context={},
    )
    db_session.add_all(
        [
            sent_recipient,
            delivered_recipient,
            WhatsAppCampaignRecipient(
                campaign_id=campaign.id,
                workspace_id=auth_context.workspace_id,
                phone="+5511900000003",
                status=CampaignRecipientStatus.queued,
                sent_at=None,
                send_context={},
            ),
            WhatsAppCampaignRecipient(
                campaign_id=campaign.id,
                workspace_id=auth_context.workspace_id,
                phone="+5511900000004",
                status=CampaignRecipientStatus.sent,
                sent_at=previous_month,
                send_context={},
            ),
            WorkspaceMonthlyUsage(
                workspace_id=auth_context.workspace_id,
                entitlement=EntitlementKey.ai_conversations,
                period_start=month_start,
                consumed_quantity=6,
                reserved_quantity=1,
                limit_quantity=10,
                hard_limit=True,
                limit_source="plan_entitlement",
            ),
            WorkspaceMonthlyUsage(
                workspace_id=auth_context.workspace_id,
                entitlement=EntitlementKey.audio_minutes,
                period_start=month_start,
                consumed_quantity=12,
                reserved_quantity=3,
                limit_quantity=30,
                hard_limit=True,
                limit_source="plan_entitlement",
            ),
            StorageObject(
                workspace_id=auth_context.workspace_id,
                object_id=uuid4(),
                version=1,
                original_filename="um.pdf",
                declared_content_type="application/pdf",
                byte_size=250,
                storage_key=f"tenants/{auth_context.workspace_id}/um.pdf",
            ),
            StorageObject(
                workspace_id=auth_context.workspace_id,
                object_id=uuid4(),
                version=1,
                original_filename="dois.pdf",
                declared_content_type="application/pdf",
                byte_size=300,
                storage_key=f"tenants/{auth_context.workspace_id}/dois.pdf",
            ),
        ]
    )
    await db_session.flush()
    db_session.add_all(
        [
            WhatsAppCampaignDeliveryEvent(
                campaign_id=campaign.id,
                recipient_id=sent_recipient.id,
                status=CampaignRecipientStatus.sent,
                payload={"attempt": 1},
                occurred_at=sent_at,
            ),
            WhatsAppCampaignDeliveryEvent(
                campaign_id=campaign.id,
                recipient_id=sent_recipient.id,
                status=CampaignRecipientStatus.delivered,
                payload={"attempt": 2},
                occurred_at=sent_at,
            ),
        ]
    )
    await db_session.flush()

    response = await client.get("/api/v1/workspaces/plan-summary")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["maxUsers"] == 2
    assert payload["maxAiConversationsPerMonth"] == 10
    assert payload["aiConversationsUsedThisMonth"] == 7
    assert payload["aiConversationsRemainingThisMonth"] == 3
    assert payload["audioMinutesUsedThisMonth"] == 15
    assert payload["audioMinutesRemainingThisMonth"] == 15
    assert payload["storageBytesUsed"] == 550
    assert payload["storageBytesRemaining"] == 450
    assert payload["filesUsed"] == 2
    assert payload["filesRemaining"] == 1
    assert payload["automationsRemaining"] == 0
    assert payload["maxCampaignRecipientsPerMonth"] == 3
    assert payload["campaignRecipientsUsedThisMonth"] == 2
    assert payload["campaignRecipientsRemainingThisMonth"] == 1
    assert payload["hasDigitalSignature"] is False
    assert payload["hasCalendar"] is True
    assert payload["isAtLimit"] is True


@pytest.mark.asyncio
async def test_workspace_onboarding_tracks_progress_until_activation(
    client,
    db_session,
    auth_context,
    active_subscription,
):
    db_session.add(
        Flow(
            workspace_id=None,
            name="Template BPC",
            description="Flow inicial para BPC",
            practice_area="previdenciario",
            is_template=True,
            is_active=True,
            trigger="manual",
            nodes=[{"id": "start"}, {"id": "finish"}],
            edges=[{"id": "edge-1"}],
        )
    )
    await db_session.flush()

    initial_response = await client.get("/api/v1/workspaces/onboarding")
    assert initial_response.status_code == 200
    initial_payload = initial_response.json()
    assert initial_payload["onboardingCompleted"] is False
    assert initial_payload["nextOnboardingStep"] == "welcome"

    await client.patch(
        "/api/v1/workspaces/onboarding",
        json={"markStepCompleted": "welcome", "currentStep": "whatsapp"},
    )
    db_session.add(
        OmnichannelChannelConnection(
            workspace_id=auth_context.workspace_id,
            channel="whatsapp",
            provider="meta_cloud",
            connection_key="whatsapp:onboarding-primary",
            display_name="WhatsApp principal",
            transport_mode="cloud_api",
            status="configured_local",
            health={"state": "local_configuration_complete"},
            configuration={
                "phoneNumberId": "phone-onboarding-001",
                "wabaId": "waba-onboarding-001",
                "businessAccountId": "business-onboarding-001",
            },
        )
    )
    await db_session.flush()
    await client.patch(
        "/api/v1/workspaces/onboarding",
        json={
            "integrations": [{"key": "whatsapp", "status": "connected"}],
            "currentStep": "areas",
        },
    )

    await _promote_platform_root(db_session, auth_context)
    catalog_areas = (await client.get("/api/v1/klm/areas")).json()
    previdenciario = next(item for item in catalog_areas if item["areaKey"] == "previdenciario")
    unlocked = await client.patch(
        f"/api/v1/klm/areas/{previdenciario['id']}/activation",
        json={"enabled": True},
    )
    assert unlocked.status_code == 200, unlocked.text

    onboarding_payload = (await client.get("/api/v1/workspaces/onboarding")).json()
    area_activations = onboarding_payload["areaActivations"]
    target_area = next(
        (area for area in area_activations if area["flowOptions"]), area_activations[0]
    )
    assert target_area["key"] == "previdenciario"
    assert target_area["enabled"] is True

    await client.patch(
        "/api/v1/workspaces/onboarding",
        json={
            "areaActivations": area_activations,
            "markStepCompleted": "areas",
            "currentStep": "template",
        },
    )

    target_area["selectedFlowTemplateId"] = target_area["flowOptions"][0]["id"]
    await client.patch(
        "/api/v1/workspaces/onboarding",
        json={
            "areaActivations": area_activations,
            "markStepCompleted": "template",
            "currentStep": "follow_up",
        },
    )

    target_area["followUpProfile"] = "balanced"
    target_area["humanInterventionMode"] = "human_in_the_loop"
    await client.patch(
        "/api/v1/workspaces/onboarding",
        json={
            "areaActivations": area_activations,
            "markStepCompleted": "follow_up",
            "currentStep": "playbook",
        },
    )

    playbook_id = await _publish_playbook(client, "previdenciario")
    playbook_response = await client.patch(
        "/api/v1/workspaces/onboarding",
        json={
            "playbookId": playbook_id,
            "markStepCompleted": "playbook",
            "currentStep": "integrations",
        },
    )
    assert playbook_response.status_code == 200, playbook_response.text
    assert playbook_response.json()["playbookId"] == playbook_id

    await client.patch(
        "/api/v1/workspaces/onboarding",
        json={"skipStep": "integrations", "currentStep": "review"},
    )

    final_response = await client.patch(
        "/api/v1/workspaces/onboarding",
        json={
            "reviewCompleted": True,
            "markStepCompleted": "review",
            "currentStep": "review",
        },
    )

    assert final_response.status_code == 200
    final_payload = final_response.json()
    assert final_payload["onboardingCompleted"] is True
    assert final_payload["onboardingProgress"] == 100
    assert final_payload["nextOnboardingStep"] is None


@pytest.mark.asyncio
async def test_workspace_onboarding_rejects_false_whatsapp_connection(
    client,
    active_subscription,
):
    response = await client.patch(
        "/api/v1/workspaces/onboarding",
        json={"integrations": [{"key": "whatsapp", "status": "connected"}]},
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == {
        "code": "WKS-008",
        "message": (
            "Valide uma conexão WhatsApp oficial no registro nativo antes de concluir esta etapa."
        ),
    }


@pytest.mark.asyncio
async def test_workspace_onboarding_reconciles_historical_false_whatsapp_state(
    client, db_session, auth_context, active_subscription
):
    workspace = await db_session.get(Workspace, auth_context.workspace_id)
    assert workspace is not None
    settings_data = dict(workspace.settings or {})
    settings_data["guidedOnboarding"] = {
        "currentStep": "review",
        "completedSteps": ["welcome", "whatsapp", "areas", "template", "follow_up", "review"],
        "skippedSteps": ["integrations"],
        "reviewCompleted": True,
        "integrations": {"whatsapp": "connected"},
        "progressPercent": 100,
        "nextStep": None,
    }
    workspace.settings = settings_data
    workspace.onboarding_completed = True
    await db_session.flush()

    response = await client.get("/api/v1/workspaces/onboarding")

    assert response.status_code == 200, response.text
    payload = response.json()
    whatsapp = next(item for item in payload["integrations"] if item["key"] == "whatsapp")
    assert whatsapp["status"] == "not_started"
    assert payload["onboardingCompleted"] is False
    assert payload["onboardingProgress"] < 100
    assert payload["currentStep"] == "whatsapp"
    assert payload["nextOnboardingStep"] == "whatsapp"

    await db_session.refresh(workspace)
    assert workspace.onboarding_completed is False
    assert workspace.settings["guidedOnboarding"]["currentStep"] == "whatsapp"
    assert workspace.settings["guidedOnboarding"]["integrations"]["whatsapp"] == "not_started"


@pytest.mark.asyncio
async def test_platform_admin_legacy_plan_editor_is_blocked_and_workspaces_remain_manageable(
    platform_client,
    db_session,
):
    client = platform_client

    plans_response = await client.get("/api/v1/platform/plans")
    assert plans_response.status_code == 200
    plans = plans_response.json()
    assert {item["key"] for item in plans} == {"basic", "pro", "scale"}

    legacy_payload = {
        "name": "Basic",
        "description": "Plano inicial",
        "recommended": False,
        "isActive": True,
        "limits": {
            "maxTokensPerMonth": 400000,
            "maxAiExecutionsPerMonth": 321,
            "maxUsers": 4,
            "maxWorkspaces": 1,
            "maxConnectedWhatsapps": 1,
            "maxAiAgents": 6,
            "maxAiConversationsPerMonth": 125,
            "maxAudioMinutesPerMonth": 20,
            "maxStorageBytes": 5368709120,
            "maxFiles": 1000,
            "maxFilesPerConversation": 10,
            "automationLimit": 6,
            "maxOperationalRecipients": 2,
            "maxCampaignRecipientsPerMonth": 1_500,
        },
        "features": {
            "digitalSignature": False,
            "calendar": False,
            "email": False,
            "webhooks": False,
            "advancedAudit": False,
            "successManager": False,
            "supportTier": "standard",
            "integrations": ["whatsapp"],
            "highlights": ["Plano inicial"],
        },
    }
    starter_plan = await db_session.scalar(select(Plan).where(Plan.tier == PlanTier.starter))
    if starter_plan is None:
        starter_plan = Plan(
            name="Basic preexistente",
            description="Snapshot anterior ao bloqueio do editor legado",
            tier=PlanTier.starter,
            price_monthly_cents=26_900,
            price_annually_cents=0,
            max_users=3,
            features={"source": "legacy-test"},
            is_active=True,
            is_public=False,
        )
        db_session.add(starter_plan)
        await db_session.flush()
        db_session.add(
            PlanEntitlement(
                plan_id=starter_plan.id,
                entitlement=EntitlementKey.users,
                enabled=True,
                limit_quantity=3,
                hard_limit=True,
            )
        )
        await db_session.flush()
    starter_plan_id = starter_plan.id
    await db_session.commit()
    plan_before = (
        starter_plan.name,
        starter_plan.description,
        starter_plan.max_users,
        starter_plan.features,
    )
    entitlements_before = sorted(
        (
            row.entitlement.value,
            row.enabled,
            row.limit_quantity,
        )
        for row in (
            await db_session.scalars(
                select(PlanEntitlement).where(PlanEntitlement.plan_id == starter_plan_id)
            )
        ).all()
    )
    plan_audits_before = len(
        (
            await db_session.scalars(
                select(PlatformAuditEvent).where(
                    PlatformAuditEvent.resource_type == "plan",
                    PlatformAuditEvent.resource_id == "basic",
                    PlatformAuditEvent.result == "success",
                )
            )
        ).all()
    )

    update_plan_response = await client.put(
        "/api/v1/platform/plans/basic",
        json=legacy_payload,
    )
    assert update_plan_response.status_code == 409
    assert update_plan_response.json()["detail"]["code"] == ("PLAN-LEGACY-EDITOR-DISABLED")
    assert "Estúdio de Planos" in update_plan_response.json()["detail"]["message"]
    starter_plan_after = await db_session.get(Plan, starter_plan_id)
    assert starter_plan_after is not None
    assert (
        starter_plan_after.name,
        starter_plan_after.description,
        starter_plan_after.max_users,
        starter_plan_after.features,
    ) == plan_before
    entitlements_after = sorted(
        (
            row.entitlement.value,
            row.enabled,
            row.limit_quantity,
        )
        for row in (
            await db_session.scalars(
                select(PlanEntitlement).where(PlanEntitlement.plan_id == starter_plan_id)
            )
        ).all()
    )
    assert entitlements_after == entitlements_before
    plan_audits_after = len(
        (
            await db_session.scalars(
                select(PlatformAuditEvent).where(
                    PlatformAuditEvent.resource_type == "plan",
                    PlatformAuditEvent.resource_id == "basic",
                    PlatformAuditEvent.result == "success",
                )
            )
        ).all()
    )
    assert plan_audits_after == plan_audits_before

    missing_plan_response = await client.put(
        "/api/v1/platform/plans/unknown-plan",
        json=legacy_payload,
    )
    assert missing_plan_response.status_code == 404
    assert missing_plan_response.json()["detail"]["code"] == "PLAN-CATALOG-NOT-FOUND"

    create_workspace_response = await client.post(
        "/api/v1/platform/workspaces",
        json={
            "name": "Novo Workspace Plataforma",
            "slug": "novo-workspace-plataforma",
            "planKey": "basic",
            "ownerName": "Gestor Plataforma",
            "ownerEmail": "gestor-plataforma@example.com",
            "ownerPassword": "Senha@123",
            "launchNotes": "Onboarding assistido",
        },
    )
    assert create_workspace_response.status_code == 201
    workspace_payload = create_workspace_response.json()
    assert workspace_payload["status"] == "provisioning"
    assert workspace_payload["planSummary"]["tier"] == "basic"
    assert workspace_payload["onboardingProgress"] == 0
    assert workspace_payload["nextOnboardingStep"] == "welcome"
    create_audit = await db_session.scalar(
        select(PlatformAuditEvent)
        .where(
            PlatformAuditEvent.resource_type == "workspace",
            PlatformAuditEvent.resource_id == workspace_payload["id"],
            PlatformAuditEvent.action == "create_platform_workspace",
        )
        .order_by(PlatformAuditEvent.occurred_at.desc())
    )
    assert create_audit is not None
    assert create_audit.before is None
    assert create_audit.after["status"] == "provisioning"
    assert create_audit.after["launchNotes"] == "Onboarding assistido"

    update_workspace_response = await client.patch(
        f"/api/v1/platform/workspaces/{workspace_payload['id']}",
        json={
            "reason": "Ativação aprovada após revisão operacional da conta.",
            "status": "active",
            "onboardingCompleted": True,
            "planKey": "pro",
            "launchNotes": "Aprovado",
        },
    )
    assert update_workspace_response.status_code == 200
    updated_workspace = update_workspace_response.json()
    assert updated_workspace["status"] == "active"
    assert updated_workspace["isActive"] is True
    assert updated_workspace["onboardingCompleted"] is True
    assert updated_workspace["planSummary"]["tier"] == "pro"
    update_audit = await db_session.scalar(
        select(PlatformAuditEvent)
        .where(
            PlatformAuditEvent.resource_type == "workspace",
            PlatformAuditEvent.resource_id == workspace_payload["id"],
            PlatformAuditEvent.action == "update_platform_workspace",
        )
        .order_by(PlatformAuditEvent.occurred_at.desc())
    )
    assert update_audit is not None
    assert update_audit.before["status"] == "provisioning"
    assert update_audit.after["status"] == "active"
    assert update_audit.after["onboardingCompleted"] is True
    assert {"status", "isActive", "onboardingCompleted", "planId", "launchNotes"}.issubset(
        update_audit.changed_paths
    )
    assert update_audit.reason == "Ativação aprovada após revisão operacional da conta."


@pytest.mark.asyncio
async def test_platform_workspace_update_rolls_back_when_audit_cannot_persist(
    platform_client,
    db_session,
    monkeypatch,
):
    created = await platform_client.post(
        "/api/v1/platform/workspaces",
        json={"name": "Conta auditável", "planKey": "basic"},
    )
    assert created.status_code == 201, created.text
    workspace_id = created.json()["id"]

    async def fail_audit(*_args, **_kwargs):
        raise RuntimeError("audit storage unavailable")

    monkeypatch.setattr(PlatformAuditEventService, "record", fail_audit)
    with pytest.raises(RuntimeError, match="audit storage unavailable"):
        await platform_client.patch(
            f"/api/v1/platform/workspaces/{workspace_id}",
            json={
                "reason": "Teste de rollback atômico da conta administrativa.",
                "status": "active",
                "onboardingCompleted": True,
            },
        )

    db_session.expire_all()
    workspace = await db_session.get(Workspace, workspace_id)
    assert workspace is not None
    assert workspace.status.value == "provisioning"
    assert workspace.is_active is False
    assert workspace.onboarding_completed is False
