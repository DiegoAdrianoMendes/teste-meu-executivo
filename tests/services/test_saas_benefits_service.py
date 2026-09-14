from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.models.saas_benefits import (
    BenefitGrant,
    BenefitLedgerEntry,
    BenefitStatus,
    BenefitUnit,
    CouponAuditEvent,
    CouponRedemptionStatus,
    CouponStatus,
    PartnerStatus,
    SaasReferralStatus,
)
from app.models.subscription_commercial import (
    WorkspacePlanCode,
    WorkspaceSubscription,
    WorkspaceSubscriptionStatus,
)
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.schemas.saas_benefits import (
    BenefitGrantCreateRequest,
    CouponWrite,
    ReferralPolicyWrite,
)
from app.services.saas_benefits_service import SaasBenefitsError, SaasBenefitsService


async def _workspace(db_session, label: str) -> Workspace:
    workspace = Workspace(
        name=f"Escritorio {label}",
        slug=f"escritorio-{label.lower()}-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.assisted,
        is_active=True,
        settings={},
    )
    db_session.add(workspace)
    await db_session.flush()
    return workspace


async def _subscription(
    db_session,
    workspace_id,
    *,
    plan: WorkspacePlanCode = WorkspacePlanCode.performance,
    status: WorkspaceSubscriptionStatus = WorkspaceSubscriptionStatus.ativa,
) -> WorkspaceSubscription:
    subscription = WorkspaceSubscription(
        workspace_id=workspace_id,
        plano=plan,
        status=status,
        valor_mensal_cents=99_000,
        ciclo="mensal",
        desconto_pct=Decimal("0"),
        origem="teste",
        metadata_json={},
    )
    db_session.add(subscription)
    await db_session.flush()
    return subscription


@pytest.mark.asyncio
async def test_indicacao_pertence_ao_workspace_e_e_idempotente(
    db_session,
    auth_context,
) -> None:
    service = SaasBenefitsService(db_session)
    profile = await service.get_or_create_profile(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
    )
    replayed_profile = await service.get_or_create_profile(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
    )
    assert replayed_profile.id == profile.id

    referred = await _workspace(db_session, "Indicado")
    attribution = await service.claim_referral(
        code=profile.code,
        referred_workspace_id=referred.id,
        idempotency_key="indicacao-escritorio-001",
    )
    replayed = await service.claim_referral(
        code=profile.code,
        referred_workspace_id=referred.id,
        idempotency_key="indicacao-escritorio-001",
    )
    assert replayed.id == attribution.id
    assert attribution.referrer_workspace_id == auth_context.workspace_id
    assert attribution.referred_workspace_id == referred.id

    another_referred = await _workspace(db_session, "Outro indicado")
    with pytest.raises(SaasBenefitsError) as reused_referral_key:
        await service.claim_referral(
            code=profile.code,
            referred_workspace_id=another_referred.id,
            idempotency_key="indicacao-escritorio-001",
        )
    assert reused_referral_key.value.code == "IDEMPOTENCY-KEY-REUSED"

    with pytest.raises(SaasBenefitsError) as self_claim:
        await service.claim_referral(
            code=profile.code,
            referred_workspace_id=auth_context.workspace_id,
            idempotency_key="indicacao-propria-001",
        )
    assert self_claim.value.code == "REFERRAL-SELF-CLAIM"


@pytest.mark.asyncio
async def test_indicacao_libera_recompensas_somente_apos_pagamento_e_carencia(
    db_session,
    auth_context,
    monkeypatch,
) -> None:
    reference_now = datetime(2030, 1, 1, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(
        "app.services.saas_benefits_service._now",
        lambda: reference_now,
    )
    service = SaasBenefitsService(db_session)
    referred = await _workspace(db_session, "Carencia")
    await _subscription(db_session, auth_context.workspace_id)
    await _subscription(db_session, referred.id)
    profile = await service.get_or_create_profile(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
    )
    attribution = await service.claim_referral(
        code=profile.code,
        referred_workspace_id=referred.id,
        idempotency_key="indicacao-carencia-001",
    )
    paid_at = reference_now
    await service.record_first_payment(
        attribution_id=attribution.id,
        paid_at=paid_at,
        plan_code="performance",
        payment_reference="pagamento-001",
    )
    assert attribution.status == SaasReferralStatus.payment_hold
    assert attribution.hold_until == paid_at + timedelta(days=30)
    assert await service.process_eligible_referrals(
        actor_user_id=auth_context.user_id,
        actor_label="Operador de teste",
    ) == []

    attribution.hold_until = reference_now - timedelta(seconds=1)
    processed = await service.process_eligible_referrals(
        actor_user_id=auth_context.user_id,
        actor_label="Operador de teste",
    )
    assert [item.id for item in processed] == [attribution.id]
    assert attribution.status == SaasReferralStatus.rewarded

    grants = list(
        (
            await db_session.scalars(
                select(BenefitGrant).where(BenefitGrant.source_ref == str(attribution.id))
            )
        ).all()
    )
    assert {(grant.workspace_id, grant.unit, grant.amount) for grant in grants} == {
        (auth_context.workspace_id, BenefitUnit.months, Decimal("3.00")),
        (referred.id, BenefitUnit.days, Decimal("14.00")),
    }
    assert all(grant.status == BenefitStatus.applied for grant in grants)
    assert await service.process_eligible_referrals(
        actor_user_id=auth_context.user_id,
        actor_label="Operador de teste",
    ) == []


@pytest.mark.asyncio
async def test_concessao_manual_aplica_reverte_e_mantem_razao_imutavel(
    db_session,
    auth_context,
) -> None:
    service = SaasBenefitsService(db_session)
    subscription = await _subscription(db_session, auth_context.workspace_id)
    grant = await service.create_grant(
        workspace_id=auth_context.workspace_id,
        benefit_type="bonus_tokens",
        unit=BenefitUnit.tokens,
        amount=Decimal("25000"),
        source_type="manual",
        source_ref="ticket-sac-001",
        reason="Bonificacao aprovada pelo atendimento.",
        idempotency_key="beneficio-manual-001",
        actor_user_id=auth_context.user_id,
    )
    replayed = await service.create_grant(
        workspace_id=auth_context.workspace_id,
        benefit_type="bonus_tokens",
        unit=BenefitUnit.tokens,
        amount=Decimal("25000"),
        source_type="manual",
        source_ref="ticket-sac-001",
        reason="Bonificacao aprovada pelo atendimento.",
        idempotency_key="beneficio-manual-001",
        actor_user_id=auth_context.user_id,
    )
    assert replayed.id == grant.id

    another_workspace = await _workspace(db_session, "Outro beneficio")
    with pytest.raises(SaasBenefitsError) as reused_grant_key:
        await service.create_grant(
            workspace_id=another_workspace.id,
            benefit_type="bonus_tokens",
            unit=BenefitUnit.tokens,
            amount=Decimal("25000"),
            source_type="manual",
            source_ref="ticket-sac-001",
            reason="Bonificacao aprovada pelo atendimento.",
            idempotency_key="beneficio-manual-001",
            actor_user_id=auth_context.user_id,
        )
    assert reused_grant_key.value.code == "IDEMPOTENCY-KEY-REUSED"

    await service.approve_grant(
        grant_id=grant.id,
        actor_user_id=auth_context.user_id,
        reason="Aprovacao comercial registrada.",
    )
    await service.apply_grant(
        grant_id=grant.id,
        actor_user_id=auth_context.user_id,
        actor_label="Super administrador",
        reason="Aplicacao autorizada.",
    )
    await service.apply_grant(
        grant_id=grant.id,
        actor_user_id=auth_context.user_id,
        actor_label="Super administrador",
        reason="Repeticao idempotente da aplicacao.",
    )
    assert subscription.metadata_json["bonusTokens"] == 25_000

    await service.reverse_grant(
        grant_id=grant.id,
        actor_user_id=auth_context.user_id,
        actor_label="Super administrador",
        reason="Reversao vinculada ao atendimento.",
    )
    assert subscription.metadata_json == {}
    assert grant.status == BenefitStatus.reversed
    ledger = list(
        (
            await db_session.scalars(
                select(BenefitLedgerEntry)
                .where(BenefitLedgerEntry.grant_id == grant.id)
                .order_by(BenefitLedgerEntry.created_at)
            )
        ).all()
    )
    assert [entry.event_type for entry in ledger] == [
        "created",
        "approved",
        "applied",
        "reversed",
    ]


@pytest.mark.asyncio
async def test_concessao_atomica_bloqueia_reversao_que_apagaria_mudanca_posterior(
    db_session,
    auth_context,
) -> None:
    service = SaasBenefitsService(db_session)
    subscription = await _subscription(db_session, auth_context.workspace_id)
    grant = await service.create_and_apply_grant(
        workspace_id=auth_context.workspace_id,
        benefit_type="bonus_tokens",
        unit=BenefitUnit.tokens,
        amount=Decimal("1000"),
        source_type="manual_blackbox",
        source_ref="ticket-sac-atomico",
        reason="Concessao atomica aprovada pelo atendimento.",
        idempotency_key="beneficio-atomico-001",
        actor_user_id=auth_context.user_id,
        actor_label="Super administrador",
    )
    assert grant.status == BenefitStatus.applied
    assert subscription.metadata_json["bonusTokens"] == 1_000

    subscription.metadata_json = {
        **dict(subscription.metadata_json or {}),
        "alteracaoPosterior": "preservar",
    }
    await db_session.flush()

    with pytest.raises(SaasBenefitsError) as reconciliation:
        await service.reverse_grant(
            grant_id=grant.id,
            actor_user_id=auth_context.user_id,
            actor_label="Super administrador",
            reason="Tentativa de reversao apos outra alteracao.",
        )
    assert reconciliation.value.code == "BENEFIT-REVERSAL-RECONCILIATION-REQUIRED"
    assert grant.status == BenefitStatus.applied
    assert subscription.metadata_json == {
        "bonusTokens": 1_000,
        "alteracaoPosterior": "preservar",
    }


@pytest.mark.asyncio
async def test_cupom_respeita_idempotencia_limite_e_auditoria(
    db_session,
    auth_context,
) -> None:
    service = SaasBenefitsService(db_session)
    subscription = await _subscription(
        db_session,
        auth_context.workspace_id,
        plan=WorkspacePlanCode.escritorio,
    )
    coupon = await service.create_coupon(
        payload=CouponWrite(
            code="boas-vindas-25",
            name="Boas-vindas com tokens",
            description="Beneficio inicial controlado pelo Blackbox.",
            benefit_type="coupon_tokens",
            unit=BenefitUnit.tokens,
            amount=Decimal("25000"),
            allowed_plan_codes=["escritorio"],
            total_limit=10,
            per_workspace_limit=1,
            auto_apply=True,
            reason="Cupom criado para teste governado.",
        ),
        actor_user_id=auth_context.user_id,
    )
    await service.set_coupon_status(
        coupon_id=coupon.id,
        status=CouponStatus.active,
        expected_version=1,
        actor_user_id=auth_context.user_id,
        reason="Ativacao aprovada.",
        ticket_id=None,
    )

    redemption = await service.redeem_coupon(
        code="BOAS-VINDAS-25",
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        idempotency_key="resgate-cupom-001",
        actor_label="Titular do escritorio",
    )
    replayed = await service.redeem_coupon(
        code="BOAS-VINDAS-25",
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        idempotency_key="resgate-cupom-001",
        actor_label="Titular do escritorio",
    )
    assert replayed.id == redemption.id
    assert coupon.redeemed_count == 1
    assert subscription.metadata_json["bonusTokens"] == 25_000

    another_workspace = await _workspace(db_session, "Outro cupom")
    await _subscription(
        db_session,
        another_workspace.id,
        plan=WorkspacePlanCode.escritorio,
    )
    with pytest.raises(SaasBenefitsError) as reused_coupon_key:
        await service.redeem_coupon(
            code="BOAS-VINDAS-25",
            workspace_id=another_workspace.id,
            actor_user_id=auth_context.user_id,
            idempotency_key="resgate-cupom-001",
            actor_label="Titular de outro escritorio",
        )
    assert reused_coupon_key.value.code == "IDEMPOTENCY-KEY-REUSED"

    with pytest.raises(SaasBenefitsError) as workspace_limit:
        await service.redeem_coupon(
            code="BOAS-VINDAS-25",
            workspace_id=auth_context.workspace_id,
            actor_user_id=auth_context.user_id,
            idempotency_key="resgate-cupom-002",
            actor_label="Titular do escritorio",
        )
    assert workspace_limit.value.code == "COUPON-WORKSPACE-LIMIT-REACHED"

    audit = list(
        (
            await db_session.scalars(
                select(CouponAuditEvent)
                .where(CouponAuditEvent.coupon_id == coupon.id)
                .order_by(CouponAuditEvent.created_at)
            )
        ).all()
    )
    assert [event.action for event in audit] == ["created", "status:active", "redeemed"]

    manual_coupon = await service.create_coupon(
        payload=CouponWrite(
            code="credito-manual-5k",
            name="Credito sujeito a aplicacao",
            description="O resgate disponibiliza o beneficio sem aplica-lo automaticamente.",
            benefit_type="coupon_tokens",
            unit=BenefitUnit.tokens,
            amount=Decimal("5000"),
            allowed_plan_codes=["escritorio"],
            total_limit=10,
            per_workspace_limit=1,
            auto_apply=False,
            reason="Cupom manual criado para prova de estado.",
        ),
        actor_user_id=auth_context.user_id,
    )
    await service.set_coupon_status(
        coupon_id=manual_coupon.id,
        status=CouponStatus.active,
        expected_version=1,
        actor_user_id=auth_context.user_id,
        reason="Ativacao do cupom manual.",
        ticket_id=None,
    )
    manual_redemption = await service.redeem_coupon(
        code=manual_coupon.code,
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        idempotency_key="resgate-cupom-manual-001",
        actor_label="Titular do escritorio",
    )
    manual_grant = await db_session.get(BenefitGrant, manual_redemption.benefit_grant_id)
    assert manual_redemption.status == CouponRedemptionStatus.available
    assert manual_grant is not None
    assert manual_grant.status == BenefitStatus.available
    assert subscription.metadata_json["bonusTokens"] == 25_000


@pytest.mark.asyncio
async def test_partner_conta_somente_escritorios_com_assinatura_ativa(
    db_session,
    auth_context,
) -> None:
    service = SaasBenefitsService(db_session)
    referrer_subscription = await _subscription(db_session, auth_context.workspace_id)
    referred = await _workspace(db_session, "Partner inativo")
    referred_subscription = await _subscription(
        db_session,
        referred.id,
        status=WorkspaceSubscriptionStatus.inadimplente,
    )
    await service.publish_policy(
        rules=ReferralPolicyWrite(partner_min_active_referrals=1),
        actor_user_id=auth_context.user_id,
        reason="Politica Partner de teste.",
    )
    profile = await service.get_or_create_profile(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
    )
    attribution = await service.claim_referral(
        code=profile.code,
        referred_workspace_id=referred.id,
        idempotency_key="indicacao-partner-001",
    )
    attribution.status = SaasReferralStatus.rewarded
    await db_session.flush()

    candidate = await service.evaluate_partner(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
    )
    assert candidate.status == PartnerStatus.candidate
    assert candidate.active_referrals == 0
    assert referrer_subscription.desconto_pct == Decimal("0")

    referred_subscription.status = WorkspaceSubscriptionStatus.ativa
    await db_session.flush()
    active = await service.evaluate_partner(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
    )
    assert active.status == PartnerStatus.active
    assert active.active_referrals == 1
    assert referrer_subscription.desconto_pct == Decimal("100")


def test_beneficio_manual_exige_fuso_na_data_de_expiracao() -> None:
    with pytest.raises(ValidationError, match="fuso horario"):
        BenefitGrantCreateRequest(
            workspace_id=uuid4(),
            benefit_type="bonus_tokens",
            unit=BenefitUnit.tokens,
            amount=Decimal("1000"),
            reason="Concessao vinculada ao atendimento.",
            idempotency_key="beneficio-manual-sem-fuso",
            expires_at=datetime(2026, 8, 1, 12, 0),
        )
