from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.main import app
from app.models.platform_audit_event import PlatformAuditEvent
from app.models.saas_benefits import (
    BenefitGrant,
    BenefitLedgerEntry,
    BenefitStatus,
    BenefitUnit,
    Coupon,
    CouponRedemption,
    CouponRedemptionStatus,
    CouponStatus,
    CouponAuditEvent,
    PartnerMembership,
    PartnerStatus,
    ReferralPolicyStatus,
    ReferralProgramPolicy,
    SaasReferralAttribution,
    SaasReferralProfile,
    SaasReferralStatus,
)
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.services.platform_audit_event_service import PlatformAuditEventService


@pytest.mark.asyncio
async def test_owner_nao_acessa_superficie_blackbox(client) -> None:
    response = await client.get("/api/v1/platform/saas-benefits/overview")

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "BLACKBOX-IDENTITY-REQUIRED"


@pytest.mark.asyncio
async def test_super_administrador_acessa_studios_de_beneficios_e_cupons(
    platform_client,
) -> None:
    overview = await platform_client.get("/api/v1/platform/saas-benefits/overview")
    coupons = await platform_client.get("/api/v1/platform/saas-benefits/coupons")

    assert overview.status_code == 200, overview.text
    assert coupons.status_code == 200, coupons.text
    assert overview.json() == {
        "referrals": {},
        "benefits": {},
        "activePartners": 0,
        "activeCoupons": 0,
        "couponRedemptions": 0,
    }


@pytest.mark.asyncio
async def test_benefit_and_coupon_primary_lists_have_real_stable_pages(
    platform_client,
    db_session,
    auth_context,
) -> None:
    marker = uuid4().hex[:8]
    grants = [
        BenefitGrant(
            workspace_id=auth_context.workspace_id,
            benefit_type="support_credit",
            unit=BenefitUnit.tokens,
            amount=Decimal(index + 1),
            status=BenefitStatus.pending if index < 52 else BenefitStatus.available,
            source_type="pagination_test",
            source_ref=marker,
            reason=f"Benefício paginado {marker} {index:03d}",
            idempotency_key=f"grant-page-{marker}-{index:03d}",
            metadata_json={},
        )
        for index in range(55)
    ]
    coupons = [
        Coupon(
            code=f"PAGE-{marker}-{index:03d}".upper(),
            name=f"Cupom Paginado {marker} {index:03d}",
            description="Prova de paginação server-side.",
            status=CouponStatus.draft if index < 52 else CouponStatus.active,
            benefit_type="trial_extension",
            unit=BenefitUnit.days,
            amount=Decimal("5"),
            allowed_plan_codes=[],
            total_limit=None,
            per_workspace_limit=1,
            redeemed_count=0,
            auto_apply=True,
            version=1,
            metadata_json={},
        )
        for index in range(55)
    ]
    db_session.add_all([*grants, *coupons])
    await db_session.flush()
    now = datetime.now(UTC)
    db_session.add_all(
        [
            BenefitLedgerEntry(
                grant_id=grant.id,
                workspace_id=auth_context.workspace_id,
                event_type="registered",
                unit=grant.unit,
                delta=grant.amount,
                reason=grant.reason,
                metadata_json={},
                created_at=now,
            )
            for grant in grants
        ]
        + [
            CouponRedemption(
                coupon_id=coupon.id,
                workspace_id=auth_context.workspace_id,
                benefit_grant_id=grants[index].id,
                status=CouponRedemptionStatus.applied,
                idempotency_key=f"redemption-page-{marker}-{index:03d}",
                redeemed_at=now,
            )
            for index, coupon in enumerate(coupons)
        ]
    )
    await db_session.commit()

    requests = {
        "grants": (
            "/api/v1/platform/saas-benefits/grants/page",
            {"workspaceId": str(auth_context.workspace_id), "status": "pending"},
        ),
        "ledger": (
            "/api/v1/platform/saas-benefits/ledger/page",
            {"workspaceId": str(auth_context.workspace_id), "eventType": "registered"},
        ),
        "coupons": (
            "/api/v1/platform/saas-benefits/coupons/page",
            {"search": f"Cupom Paginado {marker}", "status": "draft"},
        ),
        "redemptions": (
            "/api/v1/platform/saas-benefits/coupons/redemptions/page",
            {"search": f"PAGE-{marker}"},
        ),
    }
    expected_totals = {"grants": 52, "ledger": 55, "coupons": 52, "redemptions": 55}
    for name, (path, filters) in requests.items():
        first = await platform_client.get(
            path,
            params={**filters, "limit": 20, "offset": 0},
        )
        last = await platform_client.get(
            path,
            params={**filters, "limit": 20, "offset": 40},
        )
        assert first.status_code == 200, first.text
        assert last.status_code == 200, last.text
        assert first.json()["total"] == expected_totals[name]
        assert len(first.json()["items"]) == 20
        assert first.json()["hasMore"] is True
        assert len(last.json()["items"]) == expected_totals[name] - 40
        assert last.json()["hasMore"] is False
        assert {item["id"] for item in first.json()["items"]}.isdisjoint(
            {item["id"] for item in last.json()["items"]}
        )


@pytest.mark.asyncio
async def test_benefit_auxiliary_lists_have_real_stable_pages(
    platform_client,
    platform_auth_context,
    db_session,
    auth_context,
) -> None:
    marker = uuid4().hex[:8]
    now = datetime.now(UTC)
    current_max = int(await db_session.scalar(select(func.max(ReferralProgramPolicy.version))) or 0)
    policies = [
        ReferralProgramPolicy(
            version=current_max + index + 1,
            status=ReferralPolicyStatus.archived,
            rules_json={"marker": marker, "index": index},
            reason=f"Política paginada {marker} {index:03d}",
            effective_at=now,
            created_by_user_id=platform_auth_context.user_id,
            created_at=now,
        )
        for index in range(55)
    ]
    profile = SaasReferralProfile(
        workspace_id=auth_context.workspace_id,
        code=f"REF-{marker}".upper(),
        active=True,
        created_by_user_id=platform_auth_context.user_id,
    )
    workspaces = [
        Workspace(
            name=f"Conta Indicada {marker} {index:03d}",
            slug=f"referred-{marker}-{index:03d}",
            status=WorkspaceStatus.active,
            setup_mode=WorkspaceSetupMode.assisted,
            is_active=True,
            onboarding_completed=True,
            settings={},
        )
        for index in range(55)
    ]
    coupon = Coupon(
        code=f"AUDIT-{marker}".upper(),
        name="Cupom com histórico paginado",
        description="Prova de paginação da auditoria.",
        status=CouponStatus.draft,
        benefit_type="trial_extension",
        unit=BenefitUnit.days,
        amount=Decimal("5"),
        allowed_plan_codes=[],
        per_workspace_limit=1,
        redeemed_count=0,
        auto_apply=True,
        version=1,
        metadata_json={},
    )
    db_session.add_all([*policies, profile, *workspaces, coupon])
    await db_session.flush()
    db_session.add_all(
        [
            SaasReferralAttribution(
                referral_profile_id=profile.id,
                referrer_workspace_id=auth_context.workspace_id,
                referred_workspace_id=workspace.id,
                status=SaasReferralStatus.registered,
                idempotency_key=f"ref-page-{marker}-{index:03d}",
                source="pagination_test",
                metadata_json={},
            )
            for index, workspace in enumerate(workspaces)
        ]
        + [
            PartnerMembership(
                workspace_id=workspace.id,
                status=PartnerStatus.candidate,
                tier="partner",
                active_referrals=index,
                referred_mrr_cents=index * 1000,
                metadata_json={},
            )
            for index, workspace in enumerate(workspaces)
        ]
        + [
            CouponAuditEvent(
                coupon_id=coupon.id,
                action=f"test_{index:03d}",
                actor_user_id=platform_auth_context.user_id,
                reason=f"Evento paginado {marker} {index:03d}",
                before_json={"index": index - 1},
                after_json={"index": index},
                created_at=now,
            )
            for index in range(55)
        ]
    )
    await db_session.commit()

    paths = {
        "policy": "/api/v1/platform/saas-benefits/policy/history/page",
        "referrals": "/api/v1/platform/saas-benefits/referrals/page",
        "partners": "/api/v1/platform/saas-benefits/partners/page",
        "audit": f"/api/v1/platform/saas-benefits/coupons/{coupon.id}/audit/page",
    }
    for name, path in paths.items():
        params = {"limit": 20, "offset": 0}
        if name == "referrals":
            params["status"] = "registered"
        if name == "partners":
            params["status"] = "candidate"
        first = await platform_client.get(path, params=params)
        last = await platform_client.get(path, params={**params, "offset": 40})
        assert first.status_code == 200, first.text
        assert last.status_code == 200, last.text
        if name == "policy":
            assert first.json()["total"] >= 55
        else:
            assert first.json()["total"] == 55
        assert len(first.json()["items"]) == 20
        assert first.json()["hasMore"] is True
        if name == "policy":
            assert len(last.json()["items"]) >= 15
        else:
            assert len(last.json()["items"]) == 15
        assert {item["id"] if "id" in item else item["workspaceId"] for item in first.json()["items"]}.isdisjoint(
            {item["id"] if "id" in item else item["workspaceId"] for item in last.json()["items"]}
        )


def test_mutacoes_de_beneficios_publicam_contratos_canonicos() -> None:
    operations = app.openapi()["paths"]
    mutation_paths = {
        "/api/v1/platform/saas-benefits/policy": ["post"],
        "/api/v1/platform/saas-benefits/referrals/{attribution_id}/first-payment": ["post"],
        "/api/v1/platform/saas-benefits/referrals/process-eligible": ["post"],
        "/api/v1/platform/saas-benefits/grants": ["post"],
        "/api/v1/platform/saas-benefits/grants/apply-now": ["post"],
        "/api/v1/platform/saas-benefits/grants/{grant_id}/approve": ["post"],
        "/api/v1/platform/saas-benefits/grants/{grant_id}/apply": ["post"],
        "/api/v1/platform/saas-benefits/grants/{grant_id}/reverse": ["post"],
        "/api/v1/platform/saas-benefits/partners/{workspace_id}/evaluate": ["post"],
        "/api/v1/platform/saas-benefits/coupons": ["post"],
        "/api/v1/platform/saas-benefits/coupons/{coupon_id}": ["put"],
        "/api/v1/platform/saas-benefits/coupons/{coupon_id}/status": ["post"],
    }
    for path, methods in mutation_paths.items():
        for method in methods:
            descriptor = operations[path][method]["x-platform-mutation"]
            assert descriptor["snapshotAdapter"] != "none", (path, method)


@pytest.mark.asyncio
async def test_cupom_tem_diff_canonico_e_rollback_atomico(
    platform_client,
    db_session,
    monkeypatch,
) -> None:
    payload = {
        "code": "AUDIT-BENEFIT-2026",
        "name": "Benefício auditável",
        "description": "Cupom local para validar a trilha canônica.",
        "benefitType": "trial_extension",
        "unit": "days",
        "amount": "5",
        "allowedPlanCodes": ["essencial"],
        "totalLimit": 20,
        "perWorkspaceLimit": 1,
        "autoApply": True,
        "reason": "Campanha interna aprovada para validação operacional.",
    }
    created = await platform_client.post(
        "/api/v1/platform/saas-benefits/coupons",
        json=payload,
    )
    assert created.status_code == 201, created.text
    coupon_id = created.json()["id"]
    updated = await platform_client.put(
        f"/api/v1/platform/saas-benefits/coupons/{coupon_id}",
        json={
            **payload,
            "name": "Benefício auditável revisado",
            "expectedVersion": created.json()["version"],
            "reason": "Revisão comercial aprovada e documentada.",
        },
    )
    assert updated.status_code == 200, updated.text

    events = list(
        (
            await db_session.scalars(
                select(PlatformAuditEvent)
                .where(
                    PlatformAuditEvent.resource_type == "coupon",
                    PlatformAuditEvent.resource_id == coupon_id,
                )
                .order_by(PlatformAuditEvent.occurred_at, PlatformAuditEvent.id)
            )
        ).all()
    )
    assert [event.action for event in events] == ["create_coupon", "update_coupon"]
    assert events[0].before is None
    assert events[0].after["title"] == "Benefício auditável"
    assert {"title", "version"} <= set(events[1].changed_paths)

    async def fail_audit(*_args, **_kwargs):
        raise RuntimeError("audit storage unavailable")

    monkeypatch.setattr(PlatformAuditEventService, "record", fail_audit)
    with pytest.raises(RuntimeError, match="audit storage unavailable"):
        await platform_client.put(
            f"/api/v1/platform/saas-benefits/coupons/{coupon_id}",
            json={
                **payload,
                "name": "Nome que não pode persistir",
                "expectedVersion": updated.json()["version"],
                "reason": "Mudança que deve ser revertida se a auditoria falhar.",
            },
        )

    db_session.expire_all()
    persisted = await db_session.get(Coupon, UUID(coupon_id))
    assert persisted is not None
    assert persisted.name == "Benefício auditável revisado"
    assert persisted.version == updated.json()["version"]


@pytest.mark.asyncio
async def test_concessao_idempotente_e_aprovacao_preservam_diff_canonico(
    platform_client,
    db_session,
    auth_context,
) -> None:
    payload = {
        "workspaceId": str(auth_context.workspace_id),
        "benefitType": "support_credit",
        "unit": "tokens",
        "amount": "250",
        "sourceType": "manual",
        "reason": "Crédito aprovado para compensação operacional documentada.",
        "idempotencyKey": "benefit-audit-idempotency-2026",
    }
    created = await platform_client.post(
        "/api/v1/platform/saas-benefits/grants",
        json=payload,
    )
    replay = await platform_client.post(
        "/api/v1/platform/saas-benefits/grants",
        json=payload,
    )
    assert created.status_code == 201, created.text
    assert replay.status_code == 201, replay.text
    assert replay.json()["id"] == created.json()["id"]

    approved = await platform_client.post(
        f"/api/v1/platform/saas-benefits/grants/{created.json()['id']}/approve",
        json={"reason": "Aprovação financeira confirmada para o crédito operacional."},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "available"

    events = list(
        (
            await db_session.scalars(
                select(PlatformAuditEvent)
                .where(
                    PlatformAuditEvent.resource_type == "benefit_grant",
                    PlatformAuditEvent.resource_id == created.json()["id"],
                )
                .order_by(PlatformAuditEvent.occurred_at, PlatformAuditEvent.id)
            )
        ).all()
    )
    assert [event.action for event in events] == [
        "create_benefit_grant",
        "create_benefit_grant",
        "approve_benefit_grant",
    ]
    assert events[0].before is None
    assert events[1].before == events[1].after
    assert events[1].changed_paths == []
    assert "status" in events[2].changed_paths
