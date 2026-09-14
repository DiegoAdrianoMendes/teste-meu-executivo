"""Regras canonicas do programa de indicacao SaaS, beneficios e cupons."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.saas_benefits import (
    BenefitGrant,
    BenefitLedgerEntry,
    BenefitStatus,
    BenefitUnit,
    Coupon,
    CouponAuditEvent,
    CouponRedemption,
    CouponRedemptionStatus,
    CouponStatus,
    PartnerMembership,
    PartnerStatus,
    ReferralPolicyStatus,
    ReferralProgramPolicy,
    SaasReferralAttribution,
    SaasReferralProfile,
    SaasReferralStatus,
)
from app.models.subscription_commercial import (
    SubscriptionEvent,
    SubscriptionEventType,
    WorkspaceSubscription,
    WorkspaceSubscriptionStatus,
)
from app.schemas.saas_benefits import CouponWrite, ReferralPolicyWrite


DEFAULT_REFERRAL_POLICY = ReferralPolicyWrite()


class SaasBenefitsError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _snapshot_subscription(subscription: WorkspaceSubscription) -> dict[str, Any]:
    return {
        "status": subscription.status.value,
        "plan": subscription.plano.value,
        "discountPct": str(subscription.desconto_pct),
        "trialEndsAt": subscription.trial_ends_at.isoformat() if subscription.trial_ends_at else None,
        "currentPeriodEnd": subscription.current_period_end.isoformat() if subscription.current_period_end else None,
        "metadata": dict(subscription.metadata_json or {}),
    }


def _coupon_snapshot(coupon: Coupon) -> dict[str, Any]:
    return {
        "code": coupon.code,
        "name": coupon.name,
        "description": coupon.description,
        "status": coupon.status.value,
        "benefitType": coupon.benefit_type,
        "unit": coupon.unit.value,
        "amount": str(coupon.amount),
        "allowedPlanCodes": list(coupon.allowed_plan_codes or []),
        "totalLimit": coupon.total_limit,
        "perWorkspaceLimit": coupon.per_workspace_limit,
        "redeemedCount": coupon.redeemed_count,
        "autoApply": coupon.auto_apply,
        "startsAt": coupon.starts_at.isoformat() if coupon.starts_at else None,
        "endsAt": coupon.ends_at.isoformat() if coupon.ends_at else None,
        "version": coupon.version,
    }


class SaasBenefitsService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    @staticmethod
    def _reject_idempotency_reuse() -> None:
        raise SaasBenefitsError(
            "IDEMPOTENCY-KEY-REUSED",
            "A chave de idempotencia ja foi usada em outra operacao.",
            status_code=409,
        )

    async def _serialize(self, key: str) -> None:
        """Serializa comandos concorrentes no PostgreSQL; SQLite ja serializa escritas."""
        if self.db.get_bind().dialect.name == "postgresql":
            await self.db.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": key},
            )

    async def current_policy(self) -> ReferralPolicyWrite:
        result = await self.db.execute(
            select(ReferralProgramPolicy)
            .where(ReferralProgramPolicy.status == ReferralPolicyStatus.published)
            .order_by(ReferralProgramPolicy.version.desc())
            .limit(1)
        )
        policy = result.scalar_one_or_none()
        if policy is None:
            return DEFAULT_REFERRAL_POLICY
        return ReferralPolicyWrite.model_validate(policy.rules_json)

    async def publish_policy(
        self,
        *,
        rules: ReferralPolicyWrite,
        actor_user_id: UUID,
        reason: str,
    ) -> ReferralProgramPolicy:
        await self._serialize("saas-benefits:policy:publish")
        latest = await self.db.scalar(select(func.max(ReferralProgramPolicy.version)))
        policy = ReferralProgramPolicy(
            version=int(latest or 0) + 1,
            status=ReferralPolicyStatus.published,
            rules_json=rules.model_dump(mode="json"),
            reason=reason.strip(),
            effective_at=_now(),
            created_by_user_id=actor_user_id,
        )
        self.db.add(policy)
        await self.db.flush()
        return policy

    async def get_or_create_profile(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
    ) -> SaasReferralProfile:
        await self._serialize(f"saas-benefits:referral-profile:{workspace_id}")
        profile = await self.db.scalar(
            select(SaasReferralProfile).where(SaasReferralProfile.workspace_id == workspace_id)
        )
        if profile is not None:
            return profile
        for _ in range(5):
            code = f"ME-{secrets.token_hex(5).upper()}"
            exists = await self.db.scalar(
                select(SaasReferralProfile.id).where(SaasReferralProfile.code == code)
            )
            if exists is None:
                profile = SaasReferralProfile(
                    workspace_id=workspace_id,
                    code=code,
                    active=True,
                    created_by_user_id=actor_user_id,
                )
                self.db.add(profile)
                await self.db.flush()
                return profile
        raise SaasBenefitsError(
            "REFERRAL-CODE-UNAVAILABLE",
            "Nao foi possivel gerar um codigo unico. Tente novamente.",
            status_code=503,
        )

    async def claim_referral(
        self,
        *,
        code: str,
        referred_workspace_id: UUID,
        idempotency_key: str,
    ) -> SaasReferralAttribution:
        normalized_code = code.strip().upper()
        await self._serialize(f"saas-benefits:referral-claim:{idempotency_key}")
        existing = await self.db.scalar(
            select(SaasReferralAttribution).where(
                SaasReferralAttribution.idempotency_key == idempotency_key
            )
        )
        if existing is not None:
            existing_profile = await self.db.get(
                SaasReferralProfile,
                existing.referral_profile_id,
            )
            if (
                existing.referred_workspace_id != referred_workspace_id
                or existing_profile is None
                or existing_profile.code != normalized_code
            ):
                self._reject_idempotency_reuse()
            return existing
        existing_for_workspace = await self.db.scalar(
            select(SaasReferralAttribution).where(
                SaasReferralAttribution.referred_workspace_id == referred_workspace_id
            )
        )
        if existing_for_workspace is not None:
            raise SaasBenefitsError(
                "REFERRAL-ALREADY-CLAIMED",
                "Este escritorio ja possui uma indicacao registrada.",
                status_code=409,
            )
        profile = await self.db.scalar(
            select(SaasReferralProfile).where(
                SaasReferralProfile.code == normalized_code,
                SaasReferralProfile.active.is_(True),
            )
        )
        if profile is None:
            raise SaasBenefitsError("REFERRAL-CODE-NOT-FOUND", "Codigo de indicacao invalido.", status_code=404)
        if profile.workspace_id == referred_workspace_id:
            raise SaasBenefitsError("REFERRAL-SELF-CLAIM", "O escritorio nao pode indicar a si mesmo.", status_code=409)
        attribution = SaasReferralAttribution(
            referral_profile_id=profile.id,
            referrer_workspace_id=profile.workspace_id,
            referred_workspace_id=referred_workspace_id,
            status=SaasReferralStatus.registered,
            idempotency_key=idempotency_key,
            source="referral_link",
        )
        self.db.add(attribution)
        await self.db.flush()
        return attribution

    async def record_first_payment(
        self,
        *,
        attribution_id: UUID,
        paid_at: datetime,
        plan_code: str,
        payment_reference: str,
    ) -> SaasReferralAttribution:
        attribution = await self.db.get(SaasReferralAttribution, attribution_id)
        if attribution is None:
            raise SaasBenefitsError("REFERRAL-NOT-FOUND", "Indicacao nao encontrada.", status_code=404)
        normalized_paid_at = paid_at.astimezone(UTC)
        normalized_plan_code = plan_code.strip().lower()
        if attribution.first_payment_at is not None:
            if attribution.metadata_json.get("paymentReference") == payment_reference:
                recorded_paid_at = attribution.first_payment_at
                if recorded_paid_at.tzinfo is None:
                    recorded_paid_at = recorded_paid_at.replace(tzinfo=UTC)
                if (
                    recorded_paid_at.astimezone(UTC) != normalized_paid_at
                    or attribution.plan_code != normalized_plan_code
                ):
                    self._reject_idempotency_reuse()
                return attribution
            raise SaasBenefitsError(
                "REFERRAL-PAYMENT-ALREADY-RECORDED",
                "O primeiro pagamento desta indicacao ja foi registrado.",
                status_code=409,
            )
        policy = await self.current_policy()
        attribution.first_payment_at = normalized_paid_at
        attribution.hold_until = normalized_paid_at + timedelta(days=policy.hold_days_after_first_payment)
        attribution.plan_code = normalized_plan_code
        attribution.status = SaasReferralStatus.payment_hold
        attribution.qualified_at = _now()
        attribution.metadata_json = {
            **dict(attribution.metadata_json or {}),
            "paymentReference": payment_reference,
        }
        await self.db.flush()
        return attribution

    async def _ledger(
        self,
        *,
        grant: BenefitGrant,
        event_type: str,
        delta: Decimal,
        actor_user_id: UUID | None,
        reason: str,
        ticket_id: UUID | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> BenefitLedgerEntry:
        entry = BenefitLedgerEntry(
            grant_id=grant.id,
            workspace_id=grant.workspace_id,
            event_type=event_type,
            unit=grant.unit,
            delta=delta,
            actor_user_id=actor_user_id,
            ticket_id=ticket_id or grant.ticket_id,
            reason=reason,
            metadata_json=metadata or {},
        )
        self.db.add(entry)
        await self.db.flush()
        return entry

    async def create_grant(
        self,
        *,
        workspace_id: UUID,
        benefit_type: str,
        unit: BenefitUnit,
        amount: Decimal,
        source_type: str,
        source_ref: str | None,
        reason: str,
        idempotency_key: str,
        actor_user_id: UUID | None,
        ticket_id: UUID | None = None,
        expires_at: datetime | None = None,
        preapproved: bool = False,
    ) -> BenefitGrant:
        await self._serialize(f"saas-benefits:grant:{idempotency_key}")
        existing = await self.db.scalar(
            select(BenefitGrant).where(BenefitGrant.idempotency_key == idempotency_key)
        )
        if existing is not None:
            existing_expires_at = existing.expires_at
            normalized_expires_at = expires_at
            if existing_expires_at is not None and existing_expires_at.tzinfo is None:
                existing_expires_at = existing_expires_at.replace(tzinfo=UTC)
            if normalized_expires_at is not None:
                normalized_expires_at = normalized_expires_at.astimezone(UTC)
            if (
                existing.workspace_id != workspace_id
                or existing.benefit_type != benefit_type
                or existing.unit != unit
                or existing.amount != amount
                or existing.source_type != source_type
                or existing.source_ref != source_ref
                or existing.reason != reason
                or existing.ticket_id != ticket_id
                or existing_expires_at != normalized_expires_at
                or bool((existing.metadata_json or {}).get("createdPreapproved", False)) != preapproved
            ):
                self._reject_idempotency_reuse()
            return existing
        now = _now()
        grant = BenefitGrant(
            workspace_id=workspace_id,
            benefit_type=benefit_type,
            unit=unit,
            amount=amount,
            status=BenefitStatus.available if preapproved else BenefitStatus.pending,
            source_type=source_type,
            source_ref=source_ref,
            reason=reason,
            idempotency_key=idempotency_key,
            ticket_id=ticket_id,
            created_by_user_id=actor_user_id,
            approved_by_user_id=actor_user_id if preapproved else None,
            approved_at=now if preapproved else None,
            expires_at=expires_at,
            metadata_json={"createdPreapproved": preapproved},
        )
        self.db.add(grant)
        await self.db.flush()
        await self._ledger(
            grant=grant,
            event_type="created",
            delta=Decimal("0"),
            actor_user_id=actor_user_id,
            reason=reason,
            ticket_id=ticket_id,
        )
        if preapproved:
            await self._ledger(
                grant=grant,
                event_type="approved",
                delta=grant.amount,
                actor_user_id=actor_user_id,
                reason=reason,
                ticket_id=ticket_id,
            )
        return grant

    async def create_and_apply_grant(
        self,
        *,
        workspace_id: UUID,
        benefit_type: str,
        unit: BenefitUnit,
        amount: Decimal,
        source_type: str,
        source_ref: str | None,
        reason: str,
        idempotency_key: str,
        actor_user_id: UUID,
        actor_label: str,
        ticket_id: UUID | None = None,
        expires_at: datetime | None = None,
    ) -> BenefitGrant:
        grant = await self.create_grant(
            workspace_id=workspace_id,
            benefit_type=benefit_type,
            unit=unit,
            amount=amount,
            source_type=source_type,
            source_ref=source_ref,
            reason=reason,
            idempotency_key=idempotency_key,
            actor_user_id=actor_user_id,
            ticket_id=ticket_id,
            expires_at=expires_at,
            preapproved=True,
        )
        return await self.apply_grant(
            grant_id=grant.id,
            actor_user_id=actor_user_id,
            actor_label=actor_label,
            reason=reason,
            ticket_id=ticket_id,
        )

    async def approve_grant(
        self,
        *,
        grant_id: UUID,
        actor_user_id: UUID,
        reason: str,
        ticket_id: UUID | None = None,
    ) -> BenefitGrant:
        grant = await self.db.scalar(
            select(BenefitGrant).where(BenefitGrant.id == grant_id).with_for_update()
        )
        if grant is None:
            raise SaasBenefitsError("BENEFIT-NOT-FOUND", "Beneficio nao encontrado.", status_code=404)
        if grant.status == BenefitStatus.available:
            return grant
        if grant.status != BenefitStatus.pending:
            raise SaasBenefitsError("BENEFIT-INVALID-STATE", "Este beneficio nao pode mais ser aprovado.", status_code=409)
        grant.status = BenefitStatus.available
        grant.approved_by_user_id = actor_user_id
        grant.approved_at = _now()
        await self._ledger(
            grant=grant,
            event_type="approved",
            delta=grant.amount,
            actor_user_id=actor_user_id,
            reason=reason,
            ticket_id=ticket_id,
        )
        return grant

    async def _subscription_for_workspace(
        self,
        workspace_id: UUID,
        *,
        lock: bool = False,
    ) -> WorkspaceSubscription:
        query = select(WorkspaceSubscription).where(WorkspaceSubscription.workspace_id == workspace_id)
        if lock:
            query = query.with_for_update()
        subscription = await self.db.scalar(query)
        if subscription is None:
            raise SaasBenefitsError(
                "BENEFIT-SUBSCRIPTION-NOT-FOUND",
                "A conta ainda nao possui uma assinatura comercial local.",
                status_code=409,
            )
        return subscription

    async def apply_grant(
        self,
        *,
        grant_id: UUID,
        actor_user_id: UUID | None,
        actor_label: str,
        reason: str,
        ticket_id: UUID | None = None,
    ) -> BenefitGrant:
        grant = await self.db.scalar(
            select(BenefitGrant).where(BenefitGrant.id == grant_id).with_for_update()
        )
        if grant is None:
            raise SaasBenefitsError("BENEFIT-NOT-FOUND", "Beneficio nao encontrado.", status_code=404)
        if grant.status == BenefitStatus.applied:
            return grant
        if grant.status != BenefitStatus.available:
            raise SaasBenefitsError("BENEFIT-NOT-AVAILABLE", "O beneficio precisa estar aprovado.", status_code=409)
        if grant.expires_at and _as_utc(grant.expires_at) <= _now():
            grant.status = BenefitStatus.expired
            raise SaasBenefitsError("BENEFIT-EXPIRED", "O beneficio expirou.", status_code=409)

        subscription = await self._subscription_for_workspace(grant.workspace_id, lock=True)
        before = _snapshot_subscription(subscription)
        now = _now()
        metadata = dict(subscription.metadata_json or {})
        if grant.unit in {BenefitUnit.days, BenefitUnit.months}:
            days = int(grant.amount) * (30 if grant.unit == BenefitUnit.months else 1)
            current_candidates = [now]
            if subscription.current_period_end:
                current_candidates.append(_as_utc(subscription.current_period_end))
            if subscription.status == WorkspaceSubscriptionStatus.trial and subscription.trial_ends_at:
                current_candidates.append(_as_utc(subscription.trial_ends_at))
            next_end = max(current_candidates) + timedelta(days=days)
            subscription.current_period_end = next_end
            if subscription.status == WorkspaceSubscriptionStatus.trial:
                subscription.trial_ends_at = next_end
            metadata["benefitAccessUntil"] = next_end.isoformat()
        elif grant.unit == BenefitUnit.tokens:
            metadata["bonusTokens"] = int(metadata.get("bonusTokens", 0)) + int(grant.amount)
        elif grant.unit == BenefitUnit.percentage:
            subscription.desconto_pct = min(Decimal("100"), Decimal(subscription.desconto_pct) + grant.amount)
        elif grant.unit == BenefitUnit.cents:
            metadata["creditCents"] = int(metadata.get("creditCents", 0)) + int(grant.amount)
        subscription.metadata_json = metadata
        after = _snapshot_subscription(subscription)
        grant.status = BenefitStatus.applied
        grant.applied_at = now
        grant.metadata_json = {
            **dict(grant.metadata_json or {}),
            "subscriptionBefore": before,
            "subscriptionAfter": after,
        }
        self.db.add(
            SubscriptionEvent(
                subscription_id=subscription.id,
                workspace_id=subscription.workspace_id,
                event_type=SubscriptionEventType.beneficio_aplicado,
                actor_user_id=actor_user_id,
                actor_label=actor_label,
                motivo=reason,
                before_json=before,
                after_json=after,
            )
        )
        await self._ledger(
            grant=grant,
            event_type="applied",
            delta=-grant.amount,
            actor_user_id=actor_user_id,
            reason=reason,
            ticket_id=ticket_id,
            metadata={"subscriptionAfter": after},
        )
        return grant

    async def reverse_grant(
        self,
        *,
        grant_id: UUID,
        actor_user_id: UUID | None,
        actor_label: str,
        reason: str,
        ticket_id: UUID | None = None,
    ) -> BenefitGrant:
        grant = await self.db.scalar(
            select(BenefitGrant).where(BenefitGrant.id == grant_id).with_for_update()
        )
        if grant is None:
            raise SaasBenefitsError("BENEFIT-NOT-FOUND", "Beneficio nao encontrado.", status_code=404)
        if grant.status == BenefitStatus.reversed:
            return grant
        if grant.status not in {BenefitStatus.available, BenefitStatus.applied}:
            raise SaasBenefitsError("BENEFIT-INVALID-STATE", "Este beneficio nao pode ser revertido.", status_code=409)
        subscription = await self._subscription_for_workspace(grant.workspace_id, lock=True)
        before = _snapshot_subscription(subscription)
        applied_after = dict((grant.metadata_json or {}).get("subscriptionAfter") or {})
        original = dict((grant.metadata_json or {}).get("subscriptionBefore") or {})
        restored = grant.status == BenefitStatus.available
        if grant.status == BenefitStatus.applied:
            if not applied_after or not original or before != applied_after:
                raise SaasBenefitsError(
                    "BENEFIT-REVERSAL-RECONCILIATION-REQUIRED",
                    "A assinatura mudou depois da aplicacao. Revise a conciliacao antes de reverter.",
                    status_code=409,
                )
            subscription.desconto_pct = Decimal(original.get("discountPct") or "0")
            subscription.trial_ends_at = (
                datetime.fromisoformat(original["trialEndsAt"]) if original.get("trialEndsAt") else None
            )
            subscription.current_period_end = (
                datetime.fromisoformat(original["currentPeriodEnd"]) if original.get("currentPeriodEnd") else None
            )
            subscription.metadata_json = dict(original.get("metadata") or {})
            restored = True
        after = _snapshot_subscription(subscription)
        grant.status = BenefitStatus.reversed
        grant.reversed_at = _now()
        self.db.add(
            SubscriptionEvent(
                subscription_id=subscription.id,
                workspace_id=subscription.workspace_id,
                event_type=SubscriptionEventType.beneficio_revertido,
                actor_user_id=actor_user_id,
                actor_label=actor_label,
                motivo=reason,
                before_json=before,
                after_json={**after, "restoredAutomatically": restored},
            )
        )
        await self._ledger(
            grant=grant,
            event_type="reversed",
            delta=-grant.amount if grant.applied_at is None else grant.amount,
            actor_user_id=actor_user_id,
            reason=reason,
            ticket_id=ticket_id,
            metadata={"restoredAutomatically": restored},
        )
        return grant

    async def process_eligible_referrals(
        self,
        *,
        actor_user_id: UUID | None,
        actor_label: str,
    ) -> list[SaasReferralAttribution]:
        now = _now()
        policy = await self.current_policy()
        rows = (
            await self.db.execute(
                select(SaasReferralAttribution).where(
                    SaasReferralAttribution.status == SaasReferralStatus.payment_hold,
                    SaasReferralAttribution.hold_until <= now,
                ).with_for_update(skip_locked=True)
            )
        ).scalars().all()
        processed: list[SaasReferralAttribution] = []
        for attribution in rows:
            attribution.status = SaasReferralStatus.eligible
            reward_months = policy.reward_months_by_plan.get(attribution.plan_code or "", 1)
            referral_grant = await self.create_grant(
                workspace_id=attribution.referrer_workspace_id,
                benefit_type="referral_free_months",
                unit=BenefitUnit.months,
                amount=Decimal(reward_months),
                source_type="saas_referral",
                source_ref=str(attribution.id),
                reason="Indicacao elegivel apos primeiro pagamento e periodo de confirmacao.",
                idempotency_key=f"referral:{attribution.id}:referrer",
                actor_user_id=actor_user_id,
                preapproved=True,
            )
            await self.apply_grant(
                grant_id=referral_grant.id,
                actor_user_id=actor_user_id,
                actor_label=actor_label,
                reason=referral_grant.reason,
            )
            welcome_unit = BenefitUnit.days if policy.welcome_benefit == "days" else BenefitUnit.tokens
            welcome_grant = await self.create_grant(
                workspace_id=attribution.referred_workspace_id,
                benefit_type="referral_welcome",
                unit=welcome_unit,
                amount=Decimal(policy.welcome_amount),
                source_type="saas_referral_welcome",
                source_ref=str(attribution.id),
                reason="Beneficio de boas-vindas do programa de indicacao.",
                idempotency_key=f"referral:{attribution.id}:welcome",
                actor_user_id=actor_user_id,
                preapproved=True,
            )
            await self.apply_grant(
                grant_id=welcome_grant.id,
                actor_user_id=actor_user_id,
                actor_label=actor_label,
                reason=welcome_grant.reason,
            )
            attribution.status = SaasReferralStatus.rewarded
            attribution.rewarded_at = now
            await self.evaluate_partner(
                workspace_id=attribution.referrer_workspace_id,
                actor_user_id=actor_user_id,
                actor_label=actor_label,
            )
            processed.append(attribution)
        await self.db.flush()
        return processed

    async def evaluate_partner(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID | None,
        actor_label: str = "Sistema Partner",
    ) -> PartnerMembership:
        await self._serialize(f"saas-benefits:partner:{workspace_id}")
        policy = await self.current_policy()
        referrals = (
            await self.db.execute(
                select(SaasReferralAttribution).where(
                    SaasReferralAttribution.referrer_workspace_id == workspace_id,
                    SaasReferralAttribution.status == SaasReferralStatus.rewarded,
                )
            )
        ).scalars().all()
        referred_ids = [row.referred_workspace_id for row in referrals]
        subscriptions = []
        if referred_ids:
            subscriptions = (
                await self.db.execute(
                    select(WorkspaceSubscription).where(
                        WorkspaceSubscription.workspace_id.in_(referred_ids),
                        WorkspaceSubscription.status == WorkspaceSubscriptionStatus.ativa,
                    )
                )
            ).scalars().all()
        referred_mrr = sum(subscription.mrr_cents for subscription in subscriptions)
        qualifies = (
            len(subscriptions) >= policy.partner_min_active_referrals
            and referred_mrr >= policy.partner_min_referred_mrr_cents
        )
        membership = await self.db.scalar(
            select(PartnerMembership).where(PartnerMembership.workspace_id == workspace_id)
        )
        was_active = membership is not None and membership.status == PartnerStatus.active
        if membership is None:
            membership = PartnerMembership(workspace_id=workspace_id)
            self.db.add(membership)
        now = _now()
        membership.active_referrals = len(subscriptions)
        membership.referred_mrr_cents = referred_mrr
        membership.status = PartnerStatus.active if qualifies else PartnerStatus.candidate
        membership.reviewed_at = now
        membership.next_review_at = now + timedelta(days=policy.partner_review_months * 30)
        membership.exemption_ends_at = membership.next_review_at if qualifies else None
        membership.reviewed_by_user_id = actor_user_id
        await self.db.flush()
        quarter = ((now.month - 1) // 3) + 1
        if qualifies:
            grant = await self.create_grant(
                workspace_id=workspace_id,
                benefit_type="partner_quarterly_exemption",
                unit=BenefitUnit.percentage,
                amount=Decimal("100"),
                source_type="partner_exemption",
                source_ref=str(membership.id),
                reason="Isencao Partner condicionada a reavaliacao trimestral.",
                idempotency_key=f"partner:{workspace_id}:{now.year}:q{quarter}",
                actor_user_id=actor_user_id,
                expires_at=membership.next_review_at,
                preapproved=True,
            )
            await self.apply_grant(
                grant_id=grant.id,
                actor_user_id=actor_user_id,
                actor_label=actor_label,
                reason=grant.reason,
            )
        elif was_active:
            active_grants = (
                await self.db.execute(
                    select(BenefitGrant)
                    .where(
                        BenefitGrant.workspace_id == workspace_id,
                        BenefitGrant.source_type == "partner_exemption",
                        BenefitGrant.status == BenefitStatus.applied,
                    )
                    .order_by(BenefitGrant.applied_at.desc())
                    .with_for_update()
                )
            ).scalars().all()
            for active_grant in active_grants:
                await self.reverse_grant(
                    grant_id=active_grant.id,
                    actor_user_id=actor_user_id,
                    actor_label=actor_label,
                    reason="Criterios Partner nao atendidos na revisao trimestral.",
                )
        await self.db.flush()
        return membership

    async def create_coupon(
        self,
        *,
        payload: CouponWrite,
        actor_user_id: UUID,
    ) -> Coupon:
        await self._serialize(f"saas-benefits:coupon-code:{payload.code}")
        existing = await self.db.scalar(select(Coupon.id).where(Coupon.code == payload.code))
        if existing is not None:
            raise SaasBenefitsError("COUPON-CODE-EXISTS", "Ja existe um cupom com este codigo.", status_code=409)
        coupon = Coupon(
            code=payload.code,
            name=payload.name.strip(),
            description=payload.description.strip(),
            status=CouponStatus.draft,
            benefit_type=payload.benefit_type.strip(),
            unit=payload.unit,
            amount=payload.amount,
            allowed_plan_codes=payload.allowed_plan_codes,
            total_limit=payload.total_limit,
            per_workspace_limit=payload.per_workspace_limit,
            auto_apply=payload.auto_apply,
            starts_at=payload.starts_at,
            ends_at=payload.ends_at,
            created_by_user_id=actor_user_id,
            updated_by_user_id=actor_user_id,
        )
        self.db.add(coupon)
        await self.db.flush()
        await self._coupon_audit(
            coupon=coupon,
            action="created",
            actor_user_id=actor_user_id,
            reason=payload.reason,
            ticket_id=payload.ticket_id,
            before={},
            after=_coupon_snapshot(coupon),
        )
        return coupon

    async def update_coupon(
        self,
        *,
        coupon_id: UUID,
        payload: CouponWrite,
        expected_version: int,
        actor_user_id: UUID,
    ) -> Coupon:
        coupon = await self.db.scalar(
            select(Coupon).where(Coupon.id == coupon_id).with_for_update()
        )
        if coupon is None:
            raise SaasBenefitsError("COUPON-NOT-FOUND", "Cupom nao encontrado.", status_code=404)
        if coupon.version != expected_version:
            raise SaasBenefitsError("COUPON-VERSION-CONFLICT", "O cupom foi alterado em outra sessao.", status_code=409)
        duplicate = await self.db.scalar(
            select(Coupon.id).where(Coupon.code == payload.code, Coupon.id != coupon.id)
        )
        if duplicate is not None:
            raise SaasBenefitsError("COUPON-CODE-EXISTS", "Ja existe um cupom com este codigo.", status_code=409)
        before = _coupon_snapshot(coupon)
        coupon.code = payload.code
        coupon.name = payload.name.strip()
        coupon.description = payload.description.strip()
        coupon.benefit_type = payload.benefit_type.strip()
        coupon.unit = payload.unit
        coupon.amount = payload.amount
        coupon.allowed_plan_codes = payload.allowed_plan_codes
        coupon.total_limit = payload.total_limit
        coupon.per_workspace_limit = payload.per_workspace_limit
        coupon.auto_apply = payload.auto_apply
        coupon.starts_at = payload.starts_at
        coupon.ends_at = payload.ends_at
        coupon.updated_by_user_id = actor_user_id
        coupon.version += 1
        await self._coupon_audit(
            coupon=coupon,
            action="updated",
            actor_user_id=actor_user_id,
            reason=payload.reason,
            ticket_id=payload.ticket_id,
            before=before,
            after=_coupon_snapshot(coupon),
        )
        return coupon

    async def set_coupon_status(
        self,
        *,
        coupon_id: UUID,
        status: CouponStatus,
        expected_version: int,
        actor_user_id: UUID,
        reason: str,
        ticket_id: UUID | None,
    ) -> Coupon:
        coupon = await self.db.scalar(
            select(Coupon).where(Coupon.id == coupon_id).with_for_update()
        )
        if coupon is None:
            raise SaasBenefitsError("COUPON-NOT-FOUND", "Cupom nao encontrado.", status_code=404)
        if coupon.version != expected_version:
            raise SaasBenefitsError("COUPON-VERSION-CONFLICT", "O cupom foi alterado em outra sessao.", status_code=409)
        if status not in {CouponStatus.active, CouponStatus.paused, CouponStatus.archived}:
            raise SaasBenefitsError("COUPON-INVALID-STATUS", "Situacao nao permitida nesta acao.")
        before = _coupon_snapshot(coupon)
        coupon.status = status
        coupon.version += 1
        coupon.updated_by_user_id = actor_user_id
        await self._coupon_audit(
            coupon=coupon,
            action=f"status:{status.value}",
            actor_user_id=actor_user_id,
            reason=reason,
            ticket_id=ticket_id,
            before=before,
            after=_coupon_snapshot(coupon),
        )
        return coupon

    async def _coupon_audit(
        self,
        *,
        coupon: Coupon,
        action: str,
        actor_user_id: UUID | None,
        reason: str,
        ticket_id: UUID | None,
        before: dict[str, Any],
        after: dict[str, Any],
    ) -> CouponAuditEvent:
        event = CouponAuditEvent(
            coupon_id=coupon.id,
            action=action,
            actor_user_id=actor_user_id,
            ticket_id=ticket_id,
            reason=reason,
            before_json=before,
            after_json=after,
        )
        self.db.add(event)
        await self.db.flush()
        return event

    async def redeem_coupon(
        self,
        *,
        code: str,
        workspace_id: UUID,
        actor_user_id: UUID,
        idempotency_key: str,
        actor_label: str,
    ) -> CouponRedemption:
        normalized_code = code.strip().upper()
        await self._serialize(f"saas-benefits:coupon-redemption:{idempotency_key}")
        existing = await self.db.scalar(
            select(CouponRedemption).where(CouponRedemption.idempotency_key == idempotency_key)
        )
        if existing is not None:
            existing_coupon = await self.db.get(Coupon, existing.coupon_id)
            if (
                existing.workspace_id != workspace_id
                or existing_coupon is None
                or existing_coupon.code != normalized_code
            ):
                self._reject_idempotency_reuse()
            return existing
        coupon = await self.db.scalar(
            select(Coupon)
            .where(Coupon.code == normalized_code)
            .with_for_update()
        )
        if coupon is None:
            raise SaasBenefitsError("COUPON-NOT-FOUND", "Cupom invalido.", status_code=404)
        now = _now()
        if coupon.status != CouponStatus.active:
            raise SaasBenefitsError("COUPON-NOT-ACTIVE", "Este cupom nao esta ativo.", status_code=409)
        if coupon.starts_at and _as_utc(coupon.starts_at) > now:
            raise SaasBenefitsError("COUPON-NOT-STARTED", "Este cupom ainda nao entrou em vigor.", status_code=409)
        if coupon.ends_at and _as_utc(coupon.ends_at) <= now:
            raise SaasBenefitsError("COUPON-EXPIRED", "Este cupom expirou.", status_code=409)
        workspace_redemptions = int(
            await self.db.scalar(
                select(func.count(CouponRedemption.id)).where(
                    CouponRedemption.coupon_id == coupon.id,
                    CouponRedemption.workspace_id == workspace_id,
                )
            )
            or 0
        )
        if workspace_redemptions >= coupon.per_workspace_limit:
            raise SaasBenefitsError(
                "COUPON-WORKSPACE-LIMIT-REACHED",
                "O limite de resgates deste escritorio foi atingido.",
                status_code=409,
            )
        if coupon.total_limit is not None and coupon.redeemed_count >= coupon.total_limit:
            raise SaasBenefitsError("COUPON-LIMIT-REACHED", "O limite de resgates foi atingido.", status_code=409)
        subscription = await self._subscription_for_workspace(workspace_id, lock=True)
        if coupon.allowed_plan_codes and subscription.plano.value not in coupon.allowed_plan_codes:
            raise SaasBenefitsError("COUPON-PLAN-NOT-ELIGIBLE", "O plano atual nao e elegivel para este cupom.", status_code=409)
        grant = await self.create_grant(
            workspace_id=workspace_id,
            benefit_type=coupon.benefit_type,
            unit=coupon.unit,
            amount=coupon.amount,
            source_type="coupon",
            source_ref=str(coupon.id),
            reason=f"Resgate do cupom {coupon.code}.",
            idempotency_key=f"coupon:{coupon.id}:{workspace_id}:{idempotency_key}",
            actor_user_id=actor_user_id,
            preapproved=True,
        )
        if coupon.auto_apply:
            await self.apply_grant(
                grant_id=grant.id,
                actor_user_id=actor_user_id,
                actor_label=actor_label,
                reason=f"Aplicacao automatica do cupom {coupon.code}.",
            )
        redemption = CouponRedemption(
            coupon_id=coupon.id,
            workspace_id=workspace_id,
            benefit_grant_id=grant.id,
            status=(
                CouponRedemptionStatus.applied
                if coupon.auto_apply
                else CouponRedemptionStatus.available
            ),
            redeemed_by_user_id=actor_user_id,
            idempotency_key=idempotency_key,
        )
        self.db.add(redemption)
        coupon.redeemed_count += 1
        coupon.version += 1
        await self.db.flush()
        await self._coupon_audit(
            coupon=coupon,
            action="redeemed",
            actor_user_id=actor_user_id,
            reason=f"Cupom resgatado pelo workspace {workspace_id}.",
            ticket_id=None,
            before={"redeemedCount": coupon.redeemed_count - 1},
            after={"redeemedCount": coupon.redeemed_count, "workspaceId": str(workspace_id)},
        )
        return redemption


__all__ = ["DEFAULT_REFERRAL_POLICY", "SaasBenefitsError", "SaasBenefitsService"]
