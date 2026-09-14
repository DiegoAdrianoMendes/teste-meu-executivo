from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.api.v1.endpoints.platform_commercial_catalog import router
from app.core.deps import AuthContext, get_current_auth_context, get_db
from app.db.tenant import get_workspace_context
from app.models.billing import Subscription
from app.models.subscription_commercial import WorkspaceSubscription
from app.models.user import AgentType, User, UserRole
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus


@pytest_asyncio.fixture
async def commercial_client(db_session, platform_auth_context):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    async def override_db():
        yield db_session

    async def override_context():
        return platform_auth_context

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_auth_context] = override_context
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
async def platform_admin_commercial_client(db_session):
    user = User(
        name="Administrador operacional",
        email=f"platform-admin-{uuid4().hex[:8]}@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="AO",
        color="#0e3d5c",
        is_active=True,
        is_internal_staff=True,
        platform_access_active=True,
        platform_role_package="platform_admin",
        email_verified_at=datetime.now(UTC),
    )
    db_session.add(user)
    await db_session.flush()
    context = AuthContext(
        user_id=user.id,
        workspace_id=None,
        membership_id=None,
        role=None,
        is_internal_staff=True,
        surface="platform",
    )
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    async def override_db():
        yield db_session

    async def override_context():
        return context

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_auth_context] = override_context
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
async def sales_sdr_commercial_client(db_session):
    user = User(
        name="SDR sem gestão comercial",
        email=f"sales-sdr-{uuid4().hex[:8]}@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="SD",
        color="#155e75",
        is_active=True,
        is_internal_staff=True,
        platform_access_active=True,
        platform_role_package="sales_sdr",
        email_verified_at=datetime.now(UTC),
    )
    db_session.add(user)
    await db_session.flush()
    context = AuthContext(
        user_id=user.id,
        workspace_id=None,
        membership_id=None,
        role=None,
        is_internal_staff=True,
        surface="platform",
    )
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    async def override_db():
        yield db_session

    async def override_context():
        return context

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_auth_context] = override_context
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def _workspace(db_session) -> Workspace:
    workspace = Workspace(
        name="Conta API comercial",
        slug=f"api-commercial-{uuid4().hex[:10]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.assisted,
        is_active=True,
        settings={},
    )
    db_session.add(workspace)
    await db_session.flush()
    return workspace


@pytest.mark.asyncio
async def test_root_bootstraps_lists_and_replays_catalog(commercial_client) -> None:
    created = await commercial_client.post(
        "/api/v1/platform/commercial/offers/bootstrap",
        headers={"Idempotency-Key": "api-bootstrap-v1"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["idempotentReplay"] is False
    assert {item["sku"] for item in created.json()["items"]} == {
        "crm_start",
        "crm_advanced",
        "crm_evolution",
        "legal_360",
    }

    replay = await commercial_client.post(
        "/api/v1/platform/commercial/offers/bootstrap",
        headers={"Idempotency-Key": "api-bootstrap-v1"},
    )
    assert replay.status_code == 201, replay.text
    assert replay.json()["idempotentReplay"] is True

    page = await commercial_client.get("/api/v1/platform/commercial/offers?limit=2&offset=0")
    assert page.status_code == 200, page.text
    assert page.json()["total"] == 4
    assert len(page.json()["items"]) == 2
    assert page.json()["hasMore"] is True


@pytest.mark.asyncio
async def test_platform_admin_cannot_publish_or_bootstrap_root_catalog(
    platform_admin_commercial_client,
) -> None:
    response = await platform_admin_commercial_client.post(
        "/api/v1/platform/commercial/offers/bootstrap",
        headers={"Idempotency-Key": "forbidden-bootstrap"},
    )
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "PLATFORM-ROOT-REQUIRED"


@pytest.mark.asyncio
async def test_platform_admin_saves_and_previews_draft_but_only_root_publishes(
    commercial_client,
    platform_admin_commercial_client,
) -> None:
    catalog = await commercial_client.post(
        "/api/v1/platform/commercial/offers/bootstrap",
        headers={"Idempotency-Key": "api-draft-bootstrap"},
    )
    assert catalog.status_code == 201, catalog.text
    start = next(item for item in catalog.json()["items"] if item["sku"] == "crm_start")
    snapshot = {**start["snapshot"], "priceMonthlyCents": 29_900}

    saved = await platform_admin_commercial_client.put(
        "/api/v1/platform/commercial/offers/crm_start/draft",
        headers={"Idempotency-Key": "api-save-draft"},
        json={
            "expectedPublishedVersion": start["version"],
            "snapshot": snapshot,
            "reason": "Preparar nova oferta sem publicar.",
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["draft"]["draftVersion"] == 1
    assert saved.headers["etag"] == '"1"'

    preview = await platform_admin_commercial_client.post(
        "/api/v1/platform/commercial/offers/crm_start/draft/preview"
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["eligible"] is True
    assert any(item["path"] == "price_monthly_cents" for item in preview.json()["diff"])

    forbidden = await platform_admin_commercial_client.post(
        "/api/v1/platform/commercial/offers/crm_start/draft/publish",
        headers={"Idempotency-Key": "api-forbidden-draft-publish", "If-Match": '"1"'},
        json={"reason": "Administrador não pode publicar."},
    )
    assert forbidden.status_code == 403, forbidden.text
    published = await commercial_client.post(
        "/api/v1/platform/commercial/offers/crm_start/draft/publish",
        headers={"Idempotency-Key": "api-root-draft-publish", "If-Match": '"1"'},
        json={"reason": "Titular publica a revisão validada."},
    )
    assert published.status_code == 200, published.text
    assert published.json()["offer"]["version"] == 2

    history = await platform_admin_commercial_client.get(
        "/api/v1/platform/commercial/offers/crm_start/history"
    )
    assert history.status_code == 200, history.text
    assert [item["offer"]["version"] for item in history.json()["items"]] == [2, 1]


@pytest.mark.asyncio
async def test_public_ai_offer_preview_explains_and_backend_enforces_evidence_gate(
    commercial_client,
) -> None:
    catalog = await commercial_client.post(
        "/api/v1/platform/commercial/offers/bootstrap",
        headers={"Idempotency-Key": "api-unit-economics-bootstrap"},
    )
    assert catalog.status_code == 201, catalog.text
    start = next(item for item in catalog.json()["items"] if item["sku"] == "crm_start")
    assert start["snapshot"]["isPublic"] is False
    snapshot = {**start["snapshot"], "isPublic": True}
    saved = await commercial_client.put(
        "/api/v1/platform/commercial/offers/crm_start/draft",
        headers={"Idempotency-Key": "api-unit-economics-draft"},
        json={
            "expectedPublishedVersion": start["version"],
            "snapshot": snapshot,
            "reason": "Preparar visibilidade pública sem inventar custos.",
        },
    )
    assert saved.status_code == 200, saved.text

    preview = await commercial_client.post(
        "/api/v1/platform/commercial/offers/crm_start/draft/preview"
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["eligible"] is False
    assert preview.json()["unitEconomics"]["status"] == "insufficient_data"
    assert preview.json()["unitEconomics"]["evidenceSource"] == (
        "provider_confirmed_usage_plus_internal_rate_card"
    )
    assert preview.json()["unitEconomics"]["blockers"]

    blocked = await commercial_client.post(
        "/api/v1/platform/commercial/offers/crm_start/draft/publish",
        headers={
            "Idempotency-Key": "api-unit-economics-publish",
            "If-Match": '"1"',
        },
        json={"reason": "Publicar somente se o gate autorizar."},
    )
    assert blocked.status_code == 422, blocked.text
    assert blocked.json()["detail"]["code"] == "COMMERCIAL-INVALID"
    assert "unit economics" in blocked.json()["detail"]["message"]


@pytest.mark.asyncio
async def test_contract_bonus_adjustment_and_manifest_are_operational_over_api(
    commercial_client, db_session
) -> None:
    workspace = await _workspace(db_session)
    catalog = await commercial_client.post(
        "/api/v1/platform/commercial/offers/bootstrap",
        headers={"Idempotency-Key": "api-operational-bootstrap"},
    )
    assert catalog.status_code == 201, catalog.text
    start = next(item for item in catalog.json()["items"] if item["sku"] == "crm_start")

    created = await commercial_client.post(
        "/api/v1/platform/commercial/contracts",
        headers={"Idempotency-Key": "api-create-contract"},
        json={
            "workspaceId": str(workspace.id),
            "lines": [
                {
                    "sku": "crm_start",
                    "policyVersionId": start["id"],
                }
            ],
            "status": "active",
            "reason": "Contrato criado pelo Blackbox.",
        },
    )
    assert created.status_code == 201, created.text
    contract_id = created.json()["id"]
    assert created.json()["workspaceLabel"] == "Conta API comercial"
    assert created.json()["lines"][0]["contractedPriceCents"] == 26_900
    assert get_workspace_context(db_session) == workspace.id

    baseline_manifest = await commercial_client.get(
        f"/api/v1/platform/commercial/workspaces/{workspace.id}/manifest"
    )
    assert baseline_manifest.status_code == 200, baseline_manifest.text
    assert baseline_manifest.json()["capabilities"]["tokens"]["limit"] == 500_000
    assert get_workspace_context(db_session) == workspace.id

    preview = await commercial_client.post(
        "/api/v1/platform/commercial/contracts/preview",
        json={
            "workspaceId": str(workspace.id),
            "lines": [
                {
                    "sku": "crm_start",
                    "policyVersionId": start["id"],
                }
            ],
        },
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["price"]["amountDueCents"] == 26_900
    assert preview.json()["manifest"]["capabilities"]["tokens"]["limit"] == 500_000

    now = datetime.now(UTC)
    discount = await commercial_client.post(
        f"/api/v1/platform/commercial/contracts/{contract_id}/adjustments",
        headers={"Idempotency-Key": "api-discount"},
        json={
            "type": "percentage_discount",
            "value": 1_000,
            "startsAt": (now - timedelta(minutes=1)).isoformat(),
            "stackGroup": "manual",
            "reason": "Desconto individual autorizado.",
            "source": "manual",
        },
    )
    assert discount.status_code == 201, discount.text
    legacy_subscription = await db_session.scalar(
        select(Subscription).where(Subscription.workspace_id == workspace.id)
    )
    legacy_workspace_subscription = await db_session.scalar(
        select(WorkspaceSubscription).where(
            WorkspaceSubscription.workspace_id == workspace.id
        )
    )
    assert legacy_subscription is not None
    assert legacy_workspace_subscription is not None
    assert legacy_subscription.amount_cents == 24_210
    assert legacy_workspace_subscription.valor_mensal_cents == 24_210

    bonus = await commercial_client.post(
        f"/api/v1/platform/commercial/workspaces/{workspace.id}/bonuses",
        headers={"Idempotency-Key": "api-token-bonus"},
        json={
            "entitlement": "tokens",
            "mode": "quantity_delta",
            "quantity": 50_000,
            "startsAt": (now - timedelta(minutes=1)).isoformat(),
            "renewsEachPeriod": True,
            "reason": "Bônus sem alterar o preço.",
            "source": "manual",
        },
    )
    assert bonus.status_code == 201, bonus.text

    contract = await commercial_client.get(f"/api/v1/platform/commercial/contracts/{contract_id}")
    assert contract.status_code == 200, contract.text
    assert contract.json()["price"]["contractedPriceCents"] == 26_900
    assert contract.json()["price"]["recurringPriceCents"] == 24_210

    manifest = await commercial_client.get(
        f"/api/v1/platform/commercial/workspaces/{workspace.id}/manifest"
    )
    assert manifest.status_code == 200, manifest.text
    assert manifest.json()["capabilities"]["tokens"]["limit"] == 550_000
    assert [source["type"] for source in manifest.json()["capabilities"]["tokens"]["sources"]] == [
        "plan",
        "bonus",
    ]


@pytest.mark.asyncio
async def test_root_restores_contract_history_as_a_new_version(
    commercial_client, db_session
) -> None:
    workspace = await _workspace(db_session)
    catalog = await commercial_client.post(
        "/api/v1/platform/commercial/offers/bootstrap",
        headers={"Idempotency-Key": "api-restore-contract-bootstrap"},
    )
    assert catalog.status_code == 201, catalog.text
    by_sku = {item["sku"]: item for item in catalog.json()["items"]}
    created = await commercial_client.post(
        "/api/v1/platform/commercial/contracts",
        headers={"Idempotency-Key": "api-restore-contract-create"},
        json={
            "workspaceId": str(workspace.id),
            "lines": [{"sku": "crm_start", "policyVersionId": by_sku["crm_start"]["id"]}],
            "status": "active",
            "reason": "Contrato original Start.",
        },
    )
    assert created.status_code == 201, created.text
    contract_id = created.json()["id"]
    revised = await commercial_client.put(
        f"/api/v1/platform/commercial/contracts/{contract_id}",
        headers={"Idempotency-Key": "api-restore-contract-revise", "If-Match": '"1"'},
        json={
            "lines": [
                {
                    "sku": "crm_advanced",
                    "policyVersionId": by_sku["crm_advanced"]["id"],
                }
            ],
            "reason": "Troca para Advanced.",
        },
    )
    assert revised.status_code == 200, revised.text
    assert revised.json()["version"] == 2

    preview = await commercial_client.post(
        f"/api/v1/platform/commercial/contracts/{contract_id}/restore-preview",
        json={"targetVersion": 1, "expectedCurrentVersion": 2},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["revision"]["eligible"] is True
    assert preview.json()["preservesAdjustments"] is True
    assert preview.json()["preservesBonuses"] is True
    restored = await commercial_client.post(
        f"/api/v1/platform/commercial/contracts/{contract_id}/restore",
        headers={"Idempotency-Key": "api-restore-contract-v1"},
        json={
            "targetVersion": 1,
            "expectedCurrentVersion": 2,
            "reason": "Restaurar Start sem apagar exceções.",
        },
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["version"] == 3
    assert [line["sku"] for line in restored.json()["lines"] if line["status"] == "active"] == [
        "crm_start"
    ]


@pytest.mark.asyncio
async def test_contract_preview_allows_authorized_staff_and_binds_exact_workspace(
    commercial_client,
    platform_admin_commercial_client,
    db_session,
) -> None:
    workspace = await _workspace(db_session)
    catalog = await commercial_client.post(
        "/api/v1/platform/commercial/offers/bootstrap",
        headers={"Idempotency-Key": "api-staff-preview-bootstrap"},
    )
    start = next(item for item in catalog.json()["items"] if item["sku"] == "crm_start")

    preview = await platform_admin_commercial_client.post(
        "/api/v1/platform/commercial/contracts/preview",
        json={
            "workspaceId": str(workspace.id),
            "lines": [{"sku": "crm_start", "policyVersionId": start["id"]}],
        },
    )

    assert preview.status_code == 200, preview.text
    assert preview.json()["workspaceId"] == str(workspace.id)
    assert get_workspace_context(db_session) == workspace.id


@pytest.mark.asyncio
async def test_contract_preview_denies_unauthorized_staff_before_workspace_binding(
    commercial_client,
    sales_sdr_commercial_client,
    db_session,
) -> None:
    workspace = await _workspace(db_session)
    catalog = await commercial_client.post(
        "/api/v1/platform/commercial/offers/bootstrap",
        headers={"Idempotency-Key": "api-denied-preview-bootstrap"},
    )
    start = next(item for item in catalog.json()["items"] if item["sku"] == "crm_start")

    denied = await sales_sdr_commercial_client.post(
        "/api/v1/platform/commercial/contracts/preview",
        json={
            "workspaceId": str(workspace.id),
            "lines": [{"sku": "crm_start", "policyVersionId": start["id"]}],
        },
    )

    assert denied.status_code == 403, denied.text
    assert denied.json()["detail"] == {
        "code": "PLATFORM-PERMISSION-DENIED",
        "message": "Seu pacote de acesso não autoriza esta operação.",
        "module": "commercial_contracts",
        "action": "manage",
    }
    assert get_workspace_context(db_session) is None


@pytest.mark.asyncio
async def test_contract_preview_rejects_cross_workspace_session_reuse(
    commercial_client,
    db_session,
) -> None:
    workspace_a = await _workspace(db_session)
    workspace_b = await _workspace(db_session)
    catalog = await commercial_client.post(
        "/api/v1/platform/commercial/offers/bootstrap",
        headers={"Idempotency-Key": "api-cross-workspace-bootstrap"},
    )
    start = next(item for item in catalog.json()["items"] if item["sku"] == "crm_start")
    payload = {
        "lines": [{"sku": "crm_start", "policyVersionId": start["id"]}],
    }

    first = await commercial_client.post(
        "/api/v1/platform/commercial/contracts/preview",
        json={**payload, "workspaceId": str(workspace_a.id)},
    )
    conflict = await commercial_client.post(
        "/api/v1/platform/commercial/contracts/preview",
        json={**payload, "workspaceId": str(workspace_b.id)},
    )

    assert first.status_code == 200, first.text
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["detail"]["code"] == "COMMERCIAL-WORKSPACE-CONTEXT-CONFLICT"
    assert get_workspace_context(db_session) == workspace_a.id


@pytest.mark.asyncio
async def test_api_rejects_annual_new_sale_and_idempotency_reuse(
    commercial_client, platform_admin_commercial_client, db_session
) -> None:
    workspace = await _workspace(db_session)
    catalog = await commercial_client.post(
        "/api/v1/platform/commercial/offers/bootstrap",
        headers={"Idempotency-Key": "api-validation-bootstrap"},
    )
    advanced = next(item for item in catalog.json()["items"] if item["sku"] == "crm_advanced")
    annual = await commercial_client.post(
        "/api/v1/platform/commercial/contracts/preview",
        json={
            "workspaceId": str(workspace.id),
            "lines": [
                {
                    "sku": "crm_advanced",
                    "policyVersionId": advanced["id"],
                    "billingInterval": "annual",
                    "contractedPriceCents": 650_000,
                }
            ],
        },
    )
    assert annual.status_code == 422, annual.text
    assert annual.json()["detail"]["code"] == "COMMERCIAL-INVALID"

    legacy_payload = {
        "workspaceId": str(workspace.id),
        "lines": [
            {
                "sku": "crm_advanced",
                "policyVersionId": advanced["id"],
                "billingInterval": "annual",
                "contractedPriceCents": 650_000,
            }
        ],
    }
    forbidden = await platform_admin_commercial_client.post(
        "/api/v1/platform/commercial/contracts/legacy-preview",
        json=legacy_payload,
    )
    assert forbidden.status_code == 403, forbidden.text
    assert forbidden.json()["detail"]["code"] == "PLATFORM-ROOT-REQUIRED"

    forbidden_import = await platform_admin_commercial_client.post(
        "/api/v1/platform/commercial/contracts/legacy-import",
        headers={"Idempotency-Key": "admin-cannot-import-legacy"},
        json={
            **legacy_payload,
            "status": "active",
            "reason": "Administrador comum não pode importar legado.",
        },
    )
    assert forbidden_import.status_code == 403, forbidden_import.text
    assert forbidden_import.json()["detail"]["code"] == "PLATFORM-ROOT-REQUIRED"

    legacy = await commercial_client.post(
        "/api/v1/platform/commercial/contracts/legacy-preview",
        json=legacy_payload,
    )
    assert legacy.status_code == 200, legacy.text
    assert legacy.json()["lines"][0]["billingInterval"] == "annual"
    assert legacy.json()["warnings"]

    boolean_bypass = await commercial_client.post(
        "/api/v1/platform/commercial/contracts/preview",
        json={**legacy_payload, "legacyImport": True},
    )
    assert boolean_bypass.status_code == 422, boolean_bypass.text

    imported = await commercial_client.post(
        "/api/v1/platform/commercial/contracts/legacy-import",
        headers={"Idempotency-Key": "root-legacy-import"},
        json={
            **legacy_payload,
            "status": "active",
            "reason": "Contrato anual legado conferido pelo titular.",
        },
    )
    assert imported.status_code == 201, imported.text
    assert imported.json()["lines"][0]["billingInterval"] == "annual"
    replay = await commercial_client.post(
        "/api/v1/platform/commercial/contracts/legacy-import",
        headers={"Idempotency-Key": "root-legacy-import"},
        json={
            **legacy_payload,
            "status": "active",
            "reason": "Contrato anual legado conferido pelo titular.",
        },
    )
    assert replay.status_code == 201, replay.text
    assert replay.json()["id"] == imported.json()["id"]

    normal_command_cannot_replay_legacy_receipt = await commercial_client.post(
        "/api/v1/platform/commercial/contracts",
        headers={"Idempotency-Key": "root-legacy-import"},
        json={
            **legacy_payload,
            "status": "active",
            "reason": "Contrato anual legado conferido pelo titular.",
        },
    )
    assert normal_command_cannot_replay_legacy_receipt.status_code == 409


@pytest.mark.asyncio
async def test_contract_revision_requires_preview_etag_and_updates_same_contract(
    commercial_client, db_session
) -> None:
    workspace = await _workspace(db_session)
    catalog_response = await commercial_client.post(
        "/api/v1/platform/commercial/offers/bootstrap",
        headers={"Idempotency-Key": "api-revision-bootstrap"},
    )
    catalog = {item["sku"]: item for item in catalog_response.json()["items"]}
    created = await commercial_client.post(
        "/api/v1/platform/commercial/contracts",
        headers={"Idempotency-Key": "api-revision-create"},
        json={
            "workspaceId": str(workspace.id),
            "lines": [
                {
                    "sku": "crm_start",
                    "policyVersionId": catalog["crm_start"]["id"],
                }
            ],
            "reason": "Contrato inicial da revisão API.",
        },
    )
    assert created.status_code == 201, created.text
    contract_id = created.json()["id"]
    read = await commercial_client.get(f"/api/v1/platform/commercial/contracts/{contract_id}")
    assert read.headers["etag"] == '"1"'
    lines = [
        {
            "sku": "crm_advanced",
            "policyVersionId": catalog["crm_advanced"]["id"],
        }
    ]
    preview = await commercial_client.post(
        f"/api/v1/platform/commercial/contracts/{contract_id}/revision-preview",
        json={"expectedVersion": 1, "lines": lines},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["eligible"] is True
    assert preview.json()["price"]["recurringPriceCents"] == 67_900

    missing_etag = await commercial_client.put(
        f"/api/v1/platform/commercial/contracts/{contract_id}",
        headers={"Idempotency-Key": "api-revision-no-etag"},
        json={"lines": lines, "reason": "Não pode atualizar sem versão."},
    )
    assert missing_etag.status_code == 428, missing_etag.text

    revised = await commercial_client.put(
        f"/api/v1/platform/commercial/contracts/{contract_id}",
        headers={"Idempotency-Key": "api-revision-apply", "If-Match": '"1"'},
        json={"lines": lines, "reason": "Upgrade para Advanced aprovado."},
    )
    assert revised.status_code == 200, revised.text
    assert revised.json()["id"] == contract_id
    assert revised.json()["version"] == 2
    assert revised.headers["etag"] == '"2"'
