"""APIs nativas do programa de indicacao SaaS, beneficios, Partner e cupons."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth_context, get_db
from app.core.platform_access import require_platform_benefits_permission
from app.core.platform_audit import (
    record_canonical_platform_mutation,
    snapshot_platform_benefit_grant,
    snapshot_platform_coupon,
    snapshot_platform_partner_membership,
    snapshot_platform_referral_attribution,
    snapshot_platform_referral_policy,
)
from app.models.saas_benefits import (
    BenefitGrant,
    BenefitLedgerEntry,
    Coupon,
    CouponAuditEvent,
    CouponRedemption,
    CouponStatus,
    PartnerMembership,
    PartnerStatus,
    ReferralProgramPolicy,
    SaasReferralAttribution,
    SaasReferralProfile,
    SaasReferralStatus,
)
from app.models.user import User
from app.models.workspace import Workspace
from app.models.workspace_membership import WorkspaceRole
from app.schemas.saas_benefits import (
    BenefitDecisionRequest,
    BenefitGrantCreateRequest,
    BenefitGrantPageRead,
    BenefitGrantRead,
    BenefitLedgerEntryRead,
    BenefitLedgerPageRead,
    CouponPageRead,
    CouponAuditEntryRead,
    CouponAuditPageRead,
    CouponRead,
    CouponRedemptionPageRead,
    CouponRedemptionRead,
    PartnerMembershipPageRead,
    PartnerMembershipRead,
    PlatformReferralPageRead,
    PlatformReferralRead,
    ReferralPolicyHistoryPageRead,
    ReferralPolicyHistoryRead,
    CouponRedeemRequest,
    CouponStatusRequest,
    CouponUpdateRequest,
    CouponWrite,
    ReferralClaimRequest,
    ReferralFirstPaymentRequest,
    ReferralPolicyPublishRequest,
    ReferralProfileRead,
    WorkspaceBenefitsOverviewRead,
)
from app.services.saas_benefits_service import SaasBenefitsError, SaasBenefitsService


router = APIRouter(prefix="/saas-benefits", tags=["Beneficios do SaaS"])
platform_router = APIRouter(prefix="/platform/saas-benefits", tags=["Blackbox — Beneficios e cupons"])


def _raise_domain(error: SaasBenefitsError) -> None:
    raise HTTPException(
        status_code=error.status_code,
        detail={"code": error.code, "message": error.message},
    ) from error


async def require_workspace_owner(
    context: AuthContext = Depends(get_current_auth_context),
) -> AuthContext:
    if (
        context.surface != "workspace"
        or context.workspace_id is None
        or context.role != WorkspaceRole.workspace_owner
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "WORKSPACE-OWNER-REQUIRED",
                "message": "Somente o titular do escritorio pode acessar este programa.",
            },
        )
    return context


def _profile_read(profile: SaasReferralProfile) -> ReferralProfileRead:
    return ReferralProfileRead(
        id=profile.id,
        workspace_id=profile.workspace_id,
        code=profile.code,
        active=profile.active,
        link=f"/checkout?ref={profile.code}",
        created_at=profile.created_at,
    )


@router.get("/me", response_model=WorkspaceBenefitsOverviewRead)
async def workspace_benefits_overview(
    context: AuthContext = Depends(require_workspace_owner),
    db: AsyncSession = Depends(get_db),
):
    assert context.workspace_id is not None
    profile = await db.scalar(
        select(SaasReferralProfile).where(SaasReferralProfile.workspace_id == context.workspace_id)
    )
    referral_rows = (
        await db.execute(
            select(SaasReferralAttribution, Workspace.name)
            .join(Workspace, Workspace.id == SaasReferralAttribution.referred_workspace_id)
            .where(SaasReferralAttribution.referrer_workspace_id == context.workspace_id)
            .order_by(SaasReferralAttribution.created_at.desc())
        )
    ).all()
    benefits = (
        await db.execute(
            select(BenefitGrant)
            .where(BenefitGrant.workspace_id == context.workspace_id)
            .order_by(BenefitGrant.created_at.desc())
        )
    ).scalars().all()
    ledger = (
        await db.execute(
            select(BenefitLedgerEntry)
            .where(BenefitLedgerEntry.workspace_id == context.workspace_id)
            .order_by(BenefitLedgerEntry.created_at.desc())
            .limit(200)
        )
    ).scalars().all()
    partner = await db.scalar(
        select(PartnerMembership).where(PartnerMembership.workspace_id == context.workspace_id)
    )
    redemptions = (
        await db.execute(
            select(CouponRedemption, Coupon)
            .join(Coupon, Coupon.id == CouponRedemption.coupon_id)
            .where(CouponRedemption.workspace_id == context.workspace_id)
            .order_by(CouponRedemption.redeemed_at.desc())
        )
    ).all()
    policy = await SaasBenefitsService(db).current_policy()
    return WorkspaceBenefitsOverviewRead(
        profile=_profile_read(profile) if profile else None,
        referrals=[
            {
                "id": str(item.id),
                "referredWorkspaceId": str(item.referred_workspace_id),
                "referredWorkspaceName": workspace_name,
                "status": item.status.value,
                "planCode": item.plan_code,
                "firstPaymentAt": item.first_payment_at.isoformat() if item.first_payment_at else None,
                "holdUntil": item.hold_until.isoformat() if item.hold_until else None,
                "rewardedAt": item.rewarded_at.isoformat() if item.rewarded_at else None,
                "createdAt": item.created_at.isoformat(),
            }
            for item, workspace_name in referral_rows
        ],
        benefits=[BenefitGrantRead.model_validate(item) for item in benefits],
        ledger=[
            {
                "id": str(entry.id),
                "grantId": str(entry.grant_id),
                "eventType": entry.event_type,
                "unit": entry.unit.value,
                "delta": str(entry.delta),
                "reason": entry.reason,
                "ticketId": str(entry.ticket_id) if entry.ticket_id else None,
                "createdAt": entry.created_at.isoformat(),
            }
            for entry in ledger
        ],
        partner=(
            {
                "status": partner.status.value,
                "tier": partner.tier,
                "activeReferrals": partner.active_referrals,
                "referredMrrCents": partner.referred_mrr_cents,
                "nextReviewAt": partner.next_review_at.isoformat() if partner.next_review_at else None,
                "exemptionEndsAt": partner.exemption_ends_at.isoformat() if partner.exemption_ends_at else None,
            }
            if partner
            else None
        ),
        coupon_redemptions=[
            {
                "id": str(redemption.id),
                "code": coupon.code,
                "name": coupon.name,
                "status": redemption.status.value,
                "redeemedAt": redemption.redeemed_at.isoformat(),
            }
            for redemption, coupon in redemptions
        ],
        policy=policy,
    )


@router.post("/profile", response_model=ReferralProfileRead, status_code=status.HTTP_201_CREATED)
async def create_referral_profile(
    context: AuthContext = Depends(require_workspace_owner),
    db: AsyncSession = Depends(get_db),
):
    assert context.workspace_id is not None
    profile = await SaasBenefitsService(db).get_or_create_profile(
        workspace_id=context.workspace_id,
        actor_user_id=context.user_id,
    )
    await db.commit()
    await db.refresh(profile)
    return _profile_read(profile)


@router.post("/referrals/claim", status_code=status.HTTP_201_CREATED)
async def claim_referral(
    payload: ReferralClaimRequest,
    context: AuthContext = Depends(require_workspace_owner),
    db: AsyncSession = Depends(get_db),
):
    assert context.workspace_id is not None
    try:
        attribution = await SaasBenefitsService(db).claim_referral(
            code=payload.code,
            referred_workspace_id=context.workspace_id,
            idempotency_key=payload.idempotency_key,
        )
        await db.commit()
        return {"id": str(attribution.id), "status": attribution.status.value}
    except SaasBenefitsError as error:
        _raise_domain(error)


@router.post("/coupons/redeem", status_code=status.HTTP_201_CREATED)
async def redeem_coupon(
    payload: CouponRedeemRequest,
    context: AuthContext = Depends(require_workspace_owner),
    db: AsyncSession = Depends(get_db),
):
    assert context.workspace_id is not None
    user = await db.get(User, context.user_id)
    try:
        redemption = await SaasBenefitsService(db).redeem_coupon(
            code=payload.code,
            workspace_id=context.workspace_id,
            actor_user_id=context.user_id,
            idempotency_key=payload.idempotency_key,
            actor_label=user.name if user else "Titular do escritorio",
        )
        await db.commit()
        return {
            "id": str(redemption.id),
            "status": redemption.status.value,
            "benefitGrantId": str(redemption.benefit_grant_id),
        }
    except SaasBenefitsError as error:
        _raise_domain(error)


@platform_router.get("/overview")
async def platform_benefits_overview(
    _: User = Depends(require_platform_benefits_permission("benefits", "view")),
    db: AsyncSession = Depends(get_db),
):
    referral_counts = dict(
        (
            await db.execute(
                select(SaasReferralAttribution.status, func.count(SaasReferralAttribution.id)).group_by(
                    SaasReferralAttribution.status
                )
            )
        ).all()
    )
    benefit_counts = dict(
        (
            await db.execute(
                select(BenefitGrant.status, func.count(BenefitGrant.id)).group_by(BenefitGrant.status)
            )
        ).all()
    )
    return {
        "referrals": {key.value: value for key, value in referral_counts.items()},
        "benefits": {key.value: value for key, value in benefit_counts.items()},
        "activePartners": int(
            await db.scalar(
                select(func.count(PartnerMembership.id)).where(
                    PartnerMembership.status == PartnerStatus.active
                )
            )
            or 0
        ),
        "activeCoupons": int(
            await db.scalar(select(func.count(Coupon.id)).where(Coupon.status == CouponStatus.active))
            or 0
        ),
        "couponRedemptions": int(await db.scalar(select(func.count(CouponRedemption.id))) or 0),
    }


@platform_router.get("/policy")
async def get_referral_policy(
    _: User = Depends(require_platform_benefits_permission("benefits", "view")),
    db: AsyncSession = Depends(get_db),
):
    latest = await db.scalar(
        select(ReferralProgramPolicy)
        .order_by(ReferralProgramPolicy.version.desc())
        .limit(1)
    )
    return {
        "version": latest.version if latest else 0,
        "status": latest.status.value if latest else "default",
        "rules": (await SaasBenefitsService(db).current_policy()).model_dump(mode="json", by_alias=True),
        "reason": latest.reason if latest else None,
        "effectiveAt": latest.effective_at.isoformat() if latest and latest.effective_at else None,
    }


@platform_router.post("/policy")
async def publish_referral_policy(
    payload: ReferralPolicyPublishRequest,
    request: Request,
    context: AuthContext = Depends(get_current_auth_context),
    actor: User = Depends(require_platform_benefits_permission("benefits", "manage_policy")),
    db: AsyncSession = Depends(get_db),
):
    before = await snapshot_platform_referral_policy(db, for_update=True)
    policy = await SaasBenefitsService(db).publish_policy(
        rules=payload.rules,
        actor_user_id=actor.id,
        reason=payload.reason,
    )
    await db.flush()
    after = await snapshot_platform_referral_policy(db, policy.id)
    await record_canonical_platform_mutation(
        request, context, db,
        before_snapshot=before, after_snapshot=after,
        module="benefits", action="publish_referral_policy", reason=payload.reason,
    )
    return {"id": str(policy.id), "version": policy.version, "status": policy.status.value}


@platform_router.get("/policy/history", deprecated=True)
async def referral_policy_history(
    _: User = Depends(require_platform_benefits_permission("benefits", "view")),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.execute(
            select(ReferralProgramPolicy)
            .order_by(ReferralProgramPolicy.version.desc())
            .limit(100)
        )
    ).scalars().all()
    return [
        {
            "id": str(item.id),
            "version": item.version,
            "status": item.status.value,
            "rules": item.rules_json,
            "reason": item.reason,
            "effectiveAt": item.effective_at,
            "createdByUserId": str(item.created_by_user_id) if item.created_by_user_id else None,
            "createdAt": item.created_at,
        }
        for item in rows
    ]


@platform_router.get("/policy/history/page", response_model=ReferralPolicyHistoryPageRead)
async def referral_policy_history_page(
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(require_platform_benefits_permission("benefits", "view")),
    db: AsyncSession = Depends(get_db),
):
    total = int(await db.scalar(select(func.count(ReferralProgramPolicy.id))) or 0)
    rows = list(
        (
            await db.scalars(
                select(ReferralProgramPolicy)
                .order_by(
                    ReferralProgramPolicy.version.desc(),
                    ReferralProgramPolicy.id.desc(),
                )
                .offset(offset)
                .limit(limit)
            )
        ).all()
    )
    return ReferralPolicyHistoryPageRead(
        items=[
            ReferralPolicyHistoryRead(
                id=item.id,
                version=item.version,
                status=item.status.value,
                rules=item.rules_json,
                reason=item.reason,
                effective_at=item.effective_at,
                created_by_user_id=item.created_by_user_id,
                created_at=item.created_at,
            )
            for item in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(rows) < total,
        sort=[
            {"field": "version", "direction": "desc"},
            {"field": "id", "direction": "desc"},
        ],
    )


@platform_router.get("/referrals", deprecated=True)
async def list_saas_referrals(
    status_filter: SaasReferralStatus | None = Query(default=None, alias="status"),
    _: User = Depends(require_platform_benefits_permission("referrals", "manage")),
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(SaasReferralAttribution, Workspace.name)
        .join(Workspace, Workspace.id == SaasReferralAttribution.referred_workspace_id)
        .order_by(SaasReferralAttribution.created_at.desc())
    )
    if status_filter:
        query = query.where(SaasReferralAttribution.status == status_filter)
    rows = (await db.execute(query)).all()
    return [
        {
            "id": str(item.id),
            "referrerWorkspaceId": str(item.referrer_workspace_id),
            "referredWorkspaceId": str(item.referred_workspace_id),
            "referredWorkspaceName": workspace_name,
            "status": item.status.value,
            "planCode": item.plan_code,
            "firstPaymentAt": item.first_payment_at.isoformat() if item.first_payment_at else None,
            "holdUntil": item.hold_until.isoformat() if item.hold_until else None,
            "rewardedAt": item.rewarded_at.isoformat() if item.rewarded_at else None,
        }
        for item, workspace_name in rows
    ]


@platform_router.get("/referrals/page", response_model=PlatformReferralPageRead)
async def list_saas_referrals_page(
    status_filter: SaasReferralStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(require_platform_benefits_permission("referrals", "manage")),
    db: AsyncSession = Depends(get_db),
):
    filters = []
    if status_filter:
        filters.append(SaasReferralAttribution.status == status_filter)
    total = int(
        await db.scalar(select(func.count(SaasReferralAttribution.id)).where(*filters))
        or 0
    )
    rows = (
        await db.execute(
            select(SaasReferralAttribution, Workspace.name)
            .join(Workspace, Workspace.id == SaasReferralAttribution.referred_workspace_id)
            .where(*filters)
            .order_by(
                SaasReferralAttribution.created_at.desc(),
                SaasReferralAttribution.id.desc(),
            )
            .offset(offset)
            .limit(limit)
        )
    ).all()
    items = [
        PlatformReferralRead(
            id=item.id,
            referrer_workspace_id=item.referrer_workspace_id,
            referred_workspace_id=item.referred_workspace_id,
            referred_workspace_name=workspace_name,
            status=item.status.value,
            plan_code=item.plan_code,
            first_payment_at=item.first_payment_at,
            hold_until=item.hold_until,
            rewarded_at=item.rewarded_at,
        )
        for item, workspace_name in rows
    ]
    return PlatformReferralPageRead(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(items) < total,
        sort=[
            {"field": "createdAt", "direction": "desc"},
            {"field": "id", "direction": "desc"},
        ],
    )


@platform_router.post("/referrals/{attribution_id}/first-payment")
async def record_referral_first_payment(
    attribution_id: UUID,
    payload: ReferralFirstPaymentRequest,
    request: Request,
    context: AuthContext = Depends(get_current_auth_context),
    _: User = Depends(require_platform_benefits_permission("referrals", "manage")),
    db: AsyncSession = Depends(get_db),
):
    try:
        before = await snapshot_platform_referral_attribution(
            db, attribution_id, for_update=True
        )
        item = await SaasBenefitsService(db).record_first_payment(
            attribution_id=attribution_id,
            paid_at=payload.paid_at,
            plan_code=payload.plan_code,
            payment_reference=payload.payment_reference,
        )
        after = await snapshot_platform_referral_attribution(db, attribution_id)
        await record_canonical_platform_mutation(
            request, context, db,
            before_snapshot=before, after_snapshot=after,
            module="referrals", action="record_referral_first_payment",
            reason="Primeiro pagamento confirmado para a indicação SaaS.",
            reason_source="system",
        )
        return {"id": str(item.id), "status": item.status.value, "holdUntil": item.hold_until}
    except SaasBenefitsError as error:
        _raise_domain(error)


@platform_router.post("/referrals/process-eligible")
async def process_eligible_referrals(
    request: Request,
    context: AuthContext = Depends(get_current_auth_context),
    actor: User = Depends(require_platform_benefits_permission("referrals", "manage")),
    db: AsyncSession = Depends(get_db),
):
    try:
        rows = await SaasBenefitsService(db).process_eligible_referrals(
            actor_user_id=actor.id,
            actor_label=actor.name,
        )
        processed_ids = [str(item.id) for item in rows]
        audit_ids = processed_ids[:200]
        aggregate = (
            "saas_referral_processing",
            str(request.headers.get("X-Correlation-ID") or "eligible"),
            {
                "processed": len(rows),
                "attributionIds": audit_ids,
                "attributionIdsTruncated": len(processed_ids) > len(audit_ids),
            },
        )
        await record_canonical_platform_mutation(
            request, context, db,
            before_snapshot=(
                aggregate[0], aggregate[1],
                {"processed": 0, "attributionIds": [], "attributionIdsTruncated": False},
            ),
            after_snapshot=aggregate,
            module="referrals", action="process_eligible_referrals",
            reason="Processamento das indicações elegíveis executado.",
            reason_source="system",
        )
        return {"processed": len(rows), "ids": processed_ids}
    except SaasBenefitsError as error:
        _raise_domain(error)


@platform_router.get("/grants", response_model=list[BenefitGrantRead], deprecated=True)
async def list_benefit_grants(
    workspace_id: UUID | None = None,
    _: User = Depends(require_platform_benefits_permission("benefits", "view")),
    db: AsyncSession = Depends(get_db),
):
    query = select(BenefitGrant).order_by(BenefitGrant.created_at.desc())
    if workspace_id:
        query = query.where(BenefitGrant.workspace_id == workspace_id)
    rows = (await db.execute(query.limit(500))).scalars().all()
    return [BenefitGrantRead.model_validate(item) for item in rows]


@platform_router.get("/grants/page", response_model=BenefitGrantPageRead)
async def list_benefit_grants_page(
    workspace_id: UUID | None = Query(default=None, alias="workspaceId"),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(require_platform_benefits_permission("benefits", "view")),
    db: AsyncSession = Depends(get_db),
):
    filters = []
    if workspace_id:
        filters.append(BenefitGrant.workspace_id == workspace_id)
    if status_filter:
        filters.append(BenefitGrant.status == status_filter)
    total = int(await db.scalar(select(func.count(BenefitGrant.id)).where(*filters)) or 0)
    rows = list(
        (
            await db.scalars(
                select(BenefitGrant)
                .where(*filters)
                .order_by(BenefitGrant.created_at.desc(), BenefitGrant.id.desc())
                .offset(offset)
                .limit(limit)
            )
        ).all()
    )
    return BenefitGrantPageRead(
        items=[BenefitGrantRead.model_validate(item) for item in rows],
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(rows) < total,
        sort=[
            {"field": "createdAt", "direction": "desc"},
            {"field": "id", "direction": "desc"},
        ],
    )


@platform_router.get("/ledger", deprecated=True)
async def list_benefit_ledger(
    workspace_id: UUID | None = None,
    _: User = Depends(require_platform_benefits_permission("benefits", "view")),
    db: AsyncSession = Depends(get_db),
):
    query = select(BenefitLedgerEntry).order_by(BenefitLedgerEntry.created_at.desc())
    if workspace_id:
        query = query.where(BenefitLedgerEntry.workspace_id == workspace_id)
    rows = (await db.execute(query.limit(500))).scalars().all()
    return [
        {
            "id": str(item.id),
            "grantId": str(item.grant_id),
            "workspaceId": str(item.workspace_id),
            "eventType": item.event_type,
            "unit": item.unit.value,
            "delta": str(item.delta),
            "reason": item.reason,
            "ticketId": str(item.ticket_id) if item.ticket_id else None,
            "actorUserId": str(item.actor_user_id) if item.actor_user_id else None,
            "createdAt": item.created_at,
        }
        for item in rows
    ]


@platform_router.get("/ledger/page", response_model=BenefitLedgerPageRead)
async def list_benefit_ledger_page(
    workspace_id: UUID | None = Query(default=None, alias="workspaceId"),
    event_type: str | None = Query(default=None, alias="eventType"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(require_platform_benefits_permission("benefits", "view")),
    db: AsyncSession = Depends(get_db),
):
    filters = []
    if workspace_id:
        filters.append(BenefitLedgerEntry.workspace_id == workspace_id)
    if event_type:
        filters.append(BenefitLedgerEntry.event_type == event_type)
    total = int(
        await db.scalar(select(func.count(BenefitLedgerEntry.id)).where(*filters))
        or 0
    )
    rows = list(
        (
            await db.scalars(
                select(BenefitLedgerEntry)
                .where(*filters)
                .order_by(
                    BenefitLedgerEntry.created_at.desc(),
                    BenefitLedgerEntry.id.desc(),
                )
                .offset(offset)
                .limit(limit)
            )
        ).all()
    )
    return BenefitLedgerPageRead(
        items=[BenefitLedgerEntryRead.model_validate(item) for item in rows],
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(rows) < total,
        sort=[
            {"field": "createdAt", "direction": "desc"},
            {"field": "id", "direction": "desc"},
        ],
    )


@platform_router.post("/grants", response_model=BenefitGrantRead, status_code=status.HTTP_201_CREATED)
async def create_benefit_grant(
    payload: BenefitGrantCreateRequest,
    request: Request,
    context: AuthContext = Depends(get_current_auth_context),
    actor: User = Depends(require_platform_benefits_permission("benefits", "grant")),
    db: AsyncSession = Depends(get_db),
):
    try:
        existing = await db.scalar(
            select(BenefitGrant).where(
                BenefitGrant.idempotency_key == payload.idempotency_key
            )
        )
        before = (
            await snapshot_platform_benefit_grant(db, existing.id)
            if existing is not None
            else None
        )
        grant = await SaasBenefitsService(db).create_grant(
            workspace_id=payload.workspace_id,
            benefit_type=payload.benefit_type,
            unit=payload.unit,
            amount=payload.amount,
            source_type=payload.source_type,
            source_ref=payload.source_ref,
            reason=payload.reason,
            idempotency_key=payload.idempotency_key,
            actor_user_id=actor.id,
            ticket_id=payload.ticket_id,
            expires_at=payload.expires_at,
        )
        await db.flush()
        after = await snapshot_platform_benefit_grant(db, grant.id)
        await record_canonical_platform_mutation(
            request, context, db,
            before_snapshot=before, after_snapshot=after,
            module="benefits", action="create_benefit_grant", reason=payload.reason,
        )
        return BenefitGrantRead.model_validate(grant)
    except SaasBenefitsError as error:
        _raise_domain(error)


@platform_router.post("/grants/apply-now", response_model=BenefitGrantRead, status_code=status.HTTP_201_CREATED)
async def create_and_apply_benefit_grant(
    payload: BenefitGrantCreateRequest,
    request: Request,
    context: AuthContext = Depends(get_current_auth_context),
    grant_actor: User = Depends(require_platform_benefits_permission("benefits", "grant")),
    actor: User = Depends(require_platform_benefits_permission("benefits", "approve")),
    db: AsyncSession = Depends(get_db),
):
    if grant_actor.id != actor.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Identidade administrativa inconsistente.")
    try:
        existing = await db.scalar(
            select(BenefitGrant).where(
                BenefitGrant.idempotency_key == payload.idempotency_key
            )
        )
        before = (
            await snapshot_platform_benefit_grant(db, existing.id)
            if existing is not None
            else None
        )
        grant = await SaasBenefitsService(db).create_and_apply_grant(
            workspace_id=payload.workspace_id,
            benefit_type=payload.benefit_type,
            unit=payload.unit,
            amount=payload.amount,
            source_type=payload.source_type,
            source_ref=payload.source_ref,
            reason=payload.reason,
            idempotency_key=payload.idempotency_key,
            actor_user_id=actor.id,
            actor_label=actor.name,
            ticket_id=payload.ticket_id,
            expires_at=payload.expires_at,
        )
        await db.flush()
        after = await snapshot_platform_benefit_grant(db, grant.id)
        await record_canonical_platform_mutation(
            request, context, db,
            before_snapshot=before, after_snapshot=after,
            module="benefits", action="create_and_apply_benefit_grant",
            reason=payload.reason,
        )
        return BenefitGrantRead.model_validate(grant)
    except SaasBenefitsError as error:
        _raise_domain(error)


@platform_router.post("/grants/{grant_id}/approve", response_model=BenefitGrantRead)
async def approve_benefit_grant(
    grant_id: UUID,
    payload: BenefitDecisionRequest,
    request: Request,
    context: AuthContext = Depends(get_current_auth_context),
    actor: User = Depends(require_platform_benefits_permission("benefits", "approve")),
    db: AsyncSession = Depends(get_db),
):
    try:
        before = await snapshot_platform_benefit_grant(db, grant_id, for_update=True)
        grant = await SaasBenefitsService(db).approve_grant(
            grant_id=grant_id,
            actor_user_id=actor.id,
            reason=payload.reason,
            ticket_id=payload.ticket_id,
        )
        after = await snapshot_platform_benefit_grant(db, grant_id)
        await record_canonical_platform_mutation(
            request, context, db,
            before_snapshot=before, after_snapshot=after,
            module="benefits", action="approve_benefit_grant", reason=payload.reason,
        )
        return BenefitGrantRead.model_validate(grant)
    except SaasBenefitsError as error:
        _raise_domain(error)


@platform_router.post("/grants/{grant_id}/apply", response_model=BenefitGrantRead)
async def apply_benefit_grant(
    grant_id: UUID,
    payload: BenefitDecisionRequest,
    request: Request,
    context: AuthContext = Depends(get_current_auth_context),
    actor: User = Depends(require_platform_benefits_permission("benefits", "approve")),
    db: AsyncSession = Depends(get_db),
):
    try:
        before = await snapshot_platform_benefit_grant(db, grant_id, for_update=True)
        grant = await SaasBenefitsService(db).apply_grant(
            grant_id=grant_id,
            actor_user_id=actor.id,
            actor_label=actor.name,
            reason=payload.reason,
            ticket_id=payload.ticket_id,
        )
        after = await snapshot_platform_benefit_grant(db, grant_id)
        await record_canonical_platform_mutation(
            request, context, db,
            before_snapshot=before, after_snapshot=after,
            module="benefits", action="apply_benefit_grant", reason=payload.reason,
        )
        return BenefitGrantRead.model_validate(grant)
    except SaasBenefitsError as error:
        _raise_domain(error)


@platform_router.post("/grants/{grant_id}/reverse", response_model=BenefitGrantRead)
async def reverse_benefit_grant(
    grant_id: UUID,
    payload: BenefitDecisionRequest,
    request: Request,
    context: AuthContext = Depends(get_current_auth_context),
    actor: User = Depends(require_platform_benefits_permission("benefits", "reverse")),
    db: AsyncSession = Depends(get_db),
):
    try:
        before = await snapshot_platform_benefit_grant(db, grant_id, for_update=True)
        grant = await SaasBenefitsService(db).reverse_grant(
            grant_id=grant_id,
            actor_user_id=actor.id,
            actor_label=actor.name,
            reason=payload.reason,
            ticket_id=payload.ticket_id,
        )
        after = await snapshot_platform_benefit_grant(db, grant_id)
        await record_canonical_platform_mutation(
            request, context, db,
            before_snapshot=before, after_snapshot=after,
            module="benefits", action="reverse_benefit_grant", reason=payload.reason,
        )
        return BenefitGrantRead.model_validate(grant)
    except SaasBenefitsError as error:
        _raise_domain(error)


@platform_router.get("/partners", deprecated=True)
async def list_partner_memberships(
    _: User = Depends(require_platform_benefits_permission("partners", "manage")),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.execute(
            select(PartnerMembership, Workspace.name)
            .join(Workspace, Workspace.id == PartnerMembership.workspace_id)
            .order_by(PartnerMembership.referred_mrr_cents.desc())
        )
    ).all()
    return [
        {
            "workspaceId": str(item.workspace_id),
            "workspaceName": name,
            "status": item.status.value,
            "activeReferrals": item.active_referrals,
            "referredMrrCents": item.referred_mrr_cents,
            "nextReviewAt": item.next_review_at,
            "exemptionEndsAt": item.exemption_ends_at,
        }
        for item, name in rows
    ]


@platform_router.get("/partners/page", response_model=PartnerMembershipPageRead)
async def list_partner_memberships_page(
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(require_platform_benefits_permission("partners", "manage")),
    db: AsyncSession = Depends(get_db),
):
    filters = []
    if status_filter:
        filters.append(PartnerMembership.status == status_filter)
    total = int(
        await db.scalar(select(func.count(PartnerMembership.id)).where(*filters))
        or 0
    )
    rows = (
        await db.execute(
            select(PartnerMembership, Workspace.name)
            .join(Workspace, Workspace.id == PartnerMembership.workspace_id)
            .where(*filters)
            .order_by(
                PartnerMembership.referred_mrr_cents.desc(),
                PartnerMembership.id.desc(),
            )
            .offset(offset)
            .limit(limit)
        )
    ).all()
    items = [
        PartnerMembershipRead(
            workspace_id=item.workspace_id,
            workspace_name=name,
            status=item.status.value,
            active_referrals=item.active_referrals,
            referred_mrr_cents=item.referred_mrr_cents,
            next_review_at=item.next_review_at,
            exemption_ends_at=item.exemption_ends_at,
        )
        for item, name in rows
    ]
    return PartnerMembershipPageRead(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(items) < total,
        sort=[
            {"field": "referredMrrCents", "direction": "desc"},
            {"field": "id", "direction": "desc"},
        ],
    )


@platform_router.post("/partners/{workspace_id}/evaluate")
async def evaluate_partner(
    workspace_id: UUID,
    request: Request,
    context: AuthContext = Depends(get_current_auth_context),
    actor: User = Depends(require_platform_benefits_permission("partners", "manage")),
    db: AsyncSession = Depends(get_db),
):
    before = await snapshot_platform_partner_membership(
        db, workspace_id, for_update=True
    )
    item = await SaasBenefitsService(db).evaluate_partner(
        workspace_id=workspace_id,
        actor_user_id=actor.id,
        actor_label=actor.name,
    )
    workspace_name = await db.scalar(select(Workspace.name).where(Workspace.id == workspace_id))
    after = await snapshot_platform_partner_membership(db, workspace_id)
    await record_canonical_platform_mutation(
        request, context, db,
        before_snapshot=before, after_snapshot=after,
        module="partners", action="evaluate_partner",
        reason="Elegibilidade Partner reavaliada com as regras vigentes.",
        reason_source="system",
    )
    return {
        "workspaceId": str(item.workspace_id),
        "workspaceName": workspace_name or "Conta",
        "status": item.status.value,
        "activeReferrals": item.active_referrals,
        "referredMrrCents": item.referred_mrr_cents,
        "nextReviewAt": item.next_review_at,
        "exemptionEndsAt": item.exemption_ends_at,
    }


@platform_router.get("/coupons", response_model=list[CouponRead], deprecated=True)
async def list_coupons(
    _: User = Depends(require_platform_benefits_permission("coupons", "view")),
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.execute(select(Coupon).order_by(Coupon.created_at.desc()))).scalars().all()
    return [CouponRead.model_validate(item) for item in rows]


@platform_router.get("/coupons/page", response_model=CouponPageRead)
async def list_coupons_page(
    search: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(require_platform_benefits_permission("coupons", "view")),
    db: AsyncSession = Depends(get_db),
):
    filters = []
    normalized_search = (search or "").strip()
    if normalized_search:
        pattern = f"%{normalized_search.replace('%', r'\%').replace('_', r'\_')}%"
        filters.append(
            Coupon.code.ilike(pattern, escape="\\")
            | Coupon.name.ilike(pattern, escape="\\")
        )
    if status_filter:
        filters.append(Coupon.status == status_filter)
    total = int(await db.scalar(select(func.count(Coupon.id)).where(*filters)) or 0)
    rows = list(
        (
            await db.scalars(
                select(Coupon)
                .where(*filters)
                .order_by(Coupon.created_at.desc(), Coupon.id.desc())
                .offset(offset)
                .limit(limit)
            )
        ).all()
    )
    return CouponPageRead(
        items=[CouponRead.model_validate(item) for item in rows],
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(rows) < total,
        sort=[
            {"field": "createdAt", "direction": "desc"},
            {"field": "id", "direction": "desc"},
        ],
    )


@platform_router.post("/coupons", response_model=CouponRead, status_code=status.HTTP_201_CREATED)
async def create_coupon(
    payload: CouponWrite,
    request: Request,
    context: AuthContext = Depends(get_current_auth_context),
    actor: User = Depends(require_platform_benefits_permission("coupons", "manage")),
    db: AsyncSession = Depends(get_db),
):
    try:
        coupon = await SaasBenefitsService(db).create_coupon(payload=payload, actor_user_id=actor.id)
        await db.flush()
        after = await snapshot_platform_coupon(db, coupon.id)
        await record_canonical_platform_mutation(
            request, context, db,
            before_snapshot=None, after_snapshot=after,
            module="coupons", action="create_coupon", reason=payload.reason,
        )
        return CouponRead.model_validate(coupon)
    except SaasBenefitsError as error:
        _raise_domain(error)


@platform_router.put("/coupons/{coupon_id}", response_model=CouponRead)
async def update_coupon(
    coupon_id: UUID,
    payload: CouponUpdateRequest,
    request: Request,
    context: AuthContext = Depends(get_current_auth_context),
    actor: User = Depends(require_platform_benefits_permission("coupons", "manage")),
    db: AsyncSession = Depends(get_db),
):
    try:
        before = await snapshot_platform_coupon(db, coupon_id, for_update=True)
        coupon = await SaasBenefitsService(db).update_coupon(
            coupon_id=coupon_id,
            payload=CouponWrite.model_validate(payload.model_dump(exclude={"expected_version"})),
            expected_version=payload.expected_version,
            actor_user_id=actor.id,
        )
        after = await snapshot_platform_coupon(db, coupon_id)
        await record_canonical_platform_mutation(
            request, context, db,
            before_snapshot=before, after_snapshot=after,
            module="coupons", action="update_coupon", reason=payload.reason,
        )
        return CouponRead.model_validate(coupon)
    except SaasBenefitsError as error:
        _raise_domain(error)


@platform_router.post("/coupons/{coupon_id}/status", response_model=CouponRead)
async def change_coupon_status(
    coupon_id: UUID,
    payload: CouponStatusRequest,
    request: Request,
    context: AuthContext = Depends(get_current_auth_context),
    actor: User = Depends(require_platform_benefits_permission("coupons", "manage")),
    db: AsyncSession = Depends(get_db),
):
    try:
        before = await snapshot_platform_coupon(db, coupon_id, for_update=True)
        coupon = await SaasBenefitsService(db).set_coupon_status(
            coupon_id=coupon_id,
            status=payload.status,
            expected_version=payload.expected_version,
            actor_user_id=actor.id,
            reason=payload.reason,
            ticket_id=payload.ticket_id,
        )
        after = await snapshot_platform_coupon(db, coupon_id)
        await record_canonical_platform_mutation(
            request, context, db,
            before_snapshot=before, after_snapshot=after,
            module="coupons", action="change_coupon_status", reason=payload.reason,
        )
        return CouponRead.model_validate(coupon)
    except SaasBenefitsError as error:
        _raise_domain(error)


@platform_router.get("/coupons/redemptions", deprecated=True)
async def list_coupon_redemptions(
    _: User = Depends(require_platform_benefits_permission("coupons", "view")),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.execute(
            select(CouponRedemption, Coupon, Workspace.name)
            .join(Coupon, Coupon.id == CouponRedemption.coupon_id)
            .join(Workspace, Workspace.id == CouponRedemption.workspace_id)
            .order_by(CouponRedemption.redeemed_at.desc())
        )
    ).all()
    return [
        {
            "id": str(redemption.id),
            "couponId": str(coupon.id),
            "couponCode": coupon.code,
            "workspaceId": str(redemption.workspace_id),
            "workspaceName": workspace_name,
            "status": redemption.status.value,
            "redeemedAt": redemption.redeemed_at,
        }
        for redemption, coupon, workspace_name in rows
    ]


@platform_router.get(
    "/coupons/redemptions/page",
    response_model=CouponRedemptionPageRead,
)
async def list_coupon_redemptions_page(
    search: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(require_platform_benefits_permission("coupons", "view")),
    db: AsyncSession = Depends(get_db),
):
    filters = []
    normalized_search = (search or "").strip()
    if normalized_search:
        pattern = f"%{normalized_search.replace('%', r'\%').replace('_', r'\_')}%"
        filters.append(
            Coupon.code.ilike(pattern, escape="\\")
            | Workspace.name.ilike(pattern, escape="\\")
        )
    if status_filter:
        filters.append(CouponRedemption.status == status_filter)
    base = (
        select(CouponRedemption, Coupon, Workspace.name)
        .join(Coupon, Coupon.id == CouponRedemption.coupon_id)
        .join(Workspace, Workspace.id == CouponRedemption.workspace_id)
        .where(*filters)
    )
    total = int(
        await db.scalar(
            select(func.count(CouponRedemption.id))
            .join(Coupon, Coupon.id == CouponRedemption.coupon_id)
            .join(Workspace, Workspace.id == CouponRedemption.workspace_id)
            .where(*filters)
        )
        or 0
    )
    rows = (
        await db.execute(
            base.order_by(
                CouponRedemption.redeemed_at.desc(),
                CouponRedemption.id.desc(),
            )
            .offset(offset)
            .limit(limit)
        )
    ).all()
    items = [
        CouponRedemptionRead(
            id=redemption.id,
            coupon_id=coupon.id,
            coupon_code=coupon.code,
            workspace_id=redemption.workspace_id,
            workspace_name=workspace_name,
            status=redemption.status.value,
            redeemed_at=redemption.redeemed_at,
        )
        for redemption, coupon, workspace_name in rows
    ]
    return CouponRedemptionPageRead(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(items) < total,
        sort=[
            {"field": "redeemedAt", "direction": "desc"},
            {"field": "id", "direction": "desc"},
        ],
    )


@platform_router.get("/coupons/{coupon_id}/audit", deprecated=True)
async def coupon_audit(
    coupon_id: UUID,
    _: User = Depends(require_platform_benefits_permission("coupons", "view")),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.execute(
            select(CouponAuditEvent)
            .where(CouponAuditEvent.coupon_id == coupon_id)
            .order_by(CouponAuditEvent.created_at.desc())
        )
    ).scalars().all()
    return [
        {
            "id": str(item.id),
            "action": item.action,
            "actorUserId": str(item.actor_user_id) if item.actor_user_id else None,
            "ticketId": str(item.ticket_id) if item.ticket_id else None,
            "reason": item.reason,
            "before": item.before_json,
            "after": item.after_json,
            "createdAt": item.created_at,
        }
        for item in rows
    ]


@platform_router.get(
    "/coupons/{coupon_id}/audit/page",
    response_model=CouponAuditPageRead,
)
async def coupon_audit_page(
    coupon_id: UUID,
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(require_platform_benefits_permission("coupons", "view")),
    db: AsyncSession = Depends(get_db),
):
    filters = [CouponAuditEvent.coupon_id == coupon_id]
    total = int(
        await db.scalar(select(func.count(CouponAuditEvent.id)).where(*filters))
        or 0
    )
    rows = list(
        (
            await db.scalars(
                select(CouponAuditEvent)
                .where(*filters)
                .order_by(
                    CouponAuditEvent.created_at.desc(),
                    CouponAuditEvent.id.desc(),
                )
                .offset(offset)
                .limit(limit)
            )
        ).all()
    )
    return CouponAuditPageRead(
        items=[
            CouponAuditEntryRead(
                id=item.id,
                action=item.action,
                actor_user_id=item.actor_user_id,
                ticket_id=item.ticket_id,
                reason=item.reason,
                before=item.before_json,
                after=item.after_json,
                created_at=item.created_at,
            )
            for item in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(rows) < total,
        sort=[
            {"field": "createdAt", "direction": "desc"},
            {"field": "id", "direction": "desc"},
        ],
    )
