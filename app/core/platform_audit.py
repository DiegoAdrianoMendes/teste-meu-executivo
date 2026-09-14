"""Piso transversal de auditoria para mutações autenticadas do Blackbox."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.deps import AuthContext, get_current_auth_context, get_db
from app.core.platform_rbac import is_platform_root
from app.models.auth_security import AuthSecurityEvent
from app.models.billing import Plan, PlanTier, Subscription
from app.models.blog_post import BlogPost
from app.models.configuration import ConfigDocument, PlatformConfigDocument
from app.models.entitlement import WorkspaceEntitlementOverride
from app.models.error_log import ErrorLog
from app.models.finance_harmonization import FinanceHarmonizationQuota
from app.models.google_business import (
    GoogleBusinessProfileConnection,
    GoogleBusinessPublishJob,
)
from app.models.growth_crm import GrowthActivity, GrowthProspect
from app.models.identity import UserSession, WorkspaceInvitation
from app.models.platform_inbox import PlatformInboxConversation, PlatformInboxMessage
from app.models.platform_incident import PlatformIncident
from app.models.platform_release import PlatformRelease
from app.models.public_site_document import PublicSiteDocument
from app.models.saas_benefits import (
    BenefitGrant,
    Coupon,
    PartnerMembership,
    ReferralProgramPolicy,
    SaasReferralAttribution,
)
from app.models.site_media_asset import SiteMediaAsset
from app.models.subscription_commercial import WorkspaceSubscription
from app.models.support_ticket import SupportSavedFilter, SupportTicket
from app.models.user import User
from app.models.workspace import Workspace
from app.models.workspace_membership import WorkspaceMembership
from app.schemas.blog import (
    BlogIndexingSettingsRead,
    BlogSettingsRead,
    BlogTaxonomyRead,
)
from app.schemas.platform_audit_event import PlatformAuditEventCreate
from app.schemas.public_site import (
    PublicFaqDocumentRead,
    PublicHomeDocumentRead,
    PublicLegalDocumentRead,
    PublicMediaLibraryRead,
    PublicSiteSettingsRead,
)
from app.services.platform_audit_event_service import PlatformAuditEventService

_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _digest(value: str) -> str | None:
    cleaned = value.strip()
    if not cleaned:
        return None
    return hashlib.sha256(f"{settings.SECRET_KEY}:{cleaned}".encode()).hexdigest()


async def audit_platform_mutation(
    request: Request,
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """Registra sucesso, negação e falha; eventos de domínio continuam complementares."""
    if request.method.upper() in _READ_METHODS or context.surface != "platform":
        yield
        return
    before_snapshot = await snapshot_platform_request_resource(request, db)
    outcome = "success"
    error_code: str | None = None
    try:
        yield
    except HTTPException as exc:
        outcome = "denied" if exc.status_code in {401, 403} else "failed"
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        error_code = str(detail.get("code") or f"HTTP-{exc.status_code}")[:80]
        # A failed command must be rolled back before its audit event is
        # persisted. Detaching the identity map first prevents rollback from
        # expiring objects that callers may still hold (notably sequential
        # request harnesses) while never retaining pending database writes.
        db.expunge_all()
        await db.rollback()
        await _record_platform_mutation(
            request, context, db, outcome, error_code,
            resource_snapshot=before_snapshot, after_snapshot=None,
        )
        await db.commit()
        raise
    except Exception as exc:
        outcome = "failed"
        error_code = type(exc).__name__[:80]
        db.expunge_all()
        await db.rollback()
        await _record_platform_mutation(
            request, context, db, outcome, error_code,
            resource_snapshot=before_snapshot, after_snapshot=None,
        )
        await db.commit()
        raise
    if not getattr(request.state, "canonical_platform_audit_recorded", False):
        after_snapshot = await snapshot_platform_request_resource(request, db)
        await _record_platform_mutation(
            request, context, db, outcome, error_code,
            resource_snapshot=before_snapshot, after_snapshot=after_snapshot,
        )


def _enum(value):
    return getattr(value, "value", value)


def _snapshot_version(snapshot: dict | None) -> int | None:
    if not snapshot:
        return None
    for key in ("version", "revision", "currentVersion", "authEpoch"):
        value = snapshot.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _snapshot_label(snapshot: dict | None) -> str | None:
    if not snapshot:
        return None
    for key in ("name", "title", "company", "publicNumber", "namespace", "key"):
        value = snapshot.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:240]
    return None


def _canonical_site_content(key: str, content: dict) -> dict:
    schema_by_key = {
        "settings": PublicSiteSettingsRead,
        "home": PublicHomeDocumentRead,
        "faq": PublicFaqDocumentRead,
        "media": PublicMediaLibraryRead,
        "terms": PublicLegalDocumentRead,
        "privacy": PublicLegalDocumentRead,
        "blog_settings": BlogSettingsRead,
        "blog_taxonomy": BlogTaxonomyRead,
        "blog_indexing": BlogIndexingSettingsRead,
    }
    schema = schema_by_key.get(key)
    if schema is None:
        return content
    return schema.model_validate(content).model_dump(by_alias=True)


def snapshot_platform_user(user: User) -> dict:
    """Snapshot seguro e estável da identidade e autoridade administrativa."""
    return {
        "name": user.name,
        "role": _enum(user.role),
        "rolePackage": user.platform_role_package,
        "grants": user.platform_permission_grants,
        "denials": user.platform_permission_denials,
        "platformAccessActive": user.platform_access_active,
        "isActive": user.is_active,
        "authEpoch": user.auth_epoch,
        "requiresPasswordChange": user.password_change_required,
    }


def snapshot_platform_workspace(workspace: Workspace) -> dict:
    """Snapshot seguro do estado operacional e comercial local da conta."""
    return {
        "name": workspace.name,
        "slug": workspace.slug,
        "legalName": workspace.legal_name,
        "description": workspace.description,
        "status": _enum(workspace.status),
        "setupMode": _enum(workspace.setup_mode),
        "isActive": workspace.is_active,
        "onboardingCompleted": workspace.onboarding_completed,
        "planId": str(workspace.subscription.plan_id) if workspace.subscription else None,
        "launchNotes": (workspace.settings or {}).get("platformLaunchNotes"),
    }


async def snapshot_platform_membership(
    db: AsyncSession,
    workspace_id,
    user_id,
    *,
    for_update: bool = False,
) -> tuple[str, str, dict] | None:
    query = select(WorkspaceMembership).where(
        WorkspaceMembership.workspace_id == workspace_id,
        WorkspaceMembership.user_id == user_id,
    )
    membership = await db.scalar(query.with_for_update() if for_update else query)
    if membership is None:
        return None
    user = await db.get(User, user_id)
    active_sessions = int((await db.scalar(select(func.count(UserSession.id)).where(
        UserSession.user_id == user_id,
        UserSession.revoked_at.is_(None),
        UserSession.expires_at > datetime.now(UTC),
    ))) or 0)
    return (
        "customer_membership",
        f"{workspace_id}:{user_id}",
        {
            "workspaceId": str(workspace_id),
            "userId": str(user_id),
            "status": _enum(membership.status),
            "role": _enum(membership.role),
            "accessPackage": membership.access_package,
            "grants": membership.permission_grants,
            "denials": membership.permission_denials,
            "isDefault": membership.is_default,
            "userActive": user.is_active if user else None,
            "authEpoch": user.auth_epoch if user else None,
            "activeSessions": active_sessions,
        },
    )


async def snapshot_platform_workspace_ownership(
    db: AsyncSession,
    workspace_id,
    *,
    for_update: bool = False,
) -> tuple[str, str, dict]:
    query = (
        select(WorkspaceMembership)
        .where(WorkspaceMembership.workspace_id == workspace_id)
        .order_by(WorkspaceMembership.user_id)
    )
    memberships = list(await db.scalars(query.with_for_update() if for_update else query))
    return (
        "workspace_ownership",
        str(workspace_id),
        {
            "workspaceId": str(workspace_id),
            "members": [
                {
                    "userId": str(item.user_id),
                    "status": _enum(item.status),
                    "role": _enum(item.role),
                    "accessPackage": item.access_package,
                    "grants": item.permission_grants,
                    "denials": item.permission_denials,
                }
                for item in memberships
            ],
        },
    )


async def snapshot_platform_invitation(
    db: AsyncSession,
    invitation_id,
    *,
    for_update: bool = False,
) -> tuple[str, str, dict] | None:
    query = select(WorkspaceInvitation).where(WorkspaceInvitation.id == invitation_id)
    invitation = await db.scalar(query.with_for_update() if for_update else query)
    if invitation is None:
        return None
    return (
        "workspace_invitation",
        str(invitation.id),
        {
            "workspaceId": str(invitation.workspace_id),
            "membershipId": str(invitation.membership_id),
            "userId": str(invitation.user_id),
            "email": invitation.invited_email,
            "name": invitation.invited_name,
            "status": _enum(invitation.status),
            "expiresAt": invitation.expires_at,
            "sendCount": invitation.send_count,
        },
    )


def _studio_definition_snapshot(value) -> object:
    """Keep ordinary diffs useful without duplicating unbounded config payloads."""
    definition = value if isinstance(value, dict | list) else {}
    encoded = json.dumps(
        definition,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    if len(encoded) <= 16 * 1024:
        return definition
    return {
        "omitted": True,
        "byteSize": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }


async def snapshot_platform_studio_draft(
    db: AsyncSession,
    workspace_id,
    document_id,
    *,
    for_update: bool = False,
) -> tuple[str, str, dict] | None:
    query = select(ConfigDocument).where(
        ConfigDocument.id == document_id,
        ConfigDocument.workspace_id == workspace_id,
    )
    document = await db.scalar(query.with_for_update() if for_update else query)
    if document is None:
        return None
    payload = dict(document.payload or {})
    return (
        "workspace_configuration_draft",
        str(document.id),
        {
            "workspaceId": str(document.workspace_id),
            "kind": payload.get("kind"),
            "key": payload.get("key"),
            "title": payload.get("title"),
            "status": payload.get("status") or "draft",
            "definition": _studio_definition_snapshot(payload.get("definition")),
            "publication": payload.get("publication"),
            "schemaVersion": document.schema_version,
            "currentVersion": document.current_version,
            "etag": document.etag,
        },
    )


async def snapshot_platform_entitlement_overrides(
    db: AsyncSession,
    workspace_id,
    *,
    for_update: bool = False,
) -> tuple[str, str, dict]:
    """Snapshot determinístico do bundle de exceções comerciais da conta."""
    query = (
        select(WorkspaceEntitlementOverride)
        .where(WorkspaceEntitlementOverride.workspace_id == workspace_id)
        .order_by(WorkspaceEntitlementOverride.entitlement)
    )
    rows = list(await db.scalars(query.with_for_update() if for_update else query))
    return (
        "workspace_entitlement_overrides",
        str(workspace_id),
        {
            "workspaceId": str(workspace_id),
            "overrides": [
                {
                    "entitlement": _enum(row.entitlement),
                    "mode": _enum(row.mode),
                    "quantity": row.quantity,
                    "reason": row.reason,
                    "createdByUserId": (
                        str(row.created_by_user_id) if row.created_by_user_id else None
                    ),
                }
                for row in rows
            ],
        },
    )


async def snapshot_platform_finance_harmonization_quota(
    db: AsyncSession,
    workspace_id,
    competence: str,
    *,
    for_update: bool = False,
) -> tuple[str, str, dict]:
    query = select(FinanceHarmonizationQuota).where(
        FinanceHarmonizationQuota.workspace_id == workspace_id,
        FinanceHarmonizationQuota.competence == competence,
    )
    quota = await db.scalar(query.with_for_update() if for_update else query)
    return (
        "finance_harmonization_quota",
        f"{workspace_id}:{competence}",
        {
            "workspaceId": str(workspace_id),
            "competence": competence,
            "exists": quota is not None,
            "used": quota.used_count if quota else 0,
            "bonusExtra": quota.bonus_extra if quota else 0,
            "grantedByUserId": (
                str(quota.granted_by) if quota and quota.granted_by else None
            ),
            "grantedAt": quota.granted_at if quota else None,
        },
    )


async def snapshot_platform_inbox_conversation(
    db: AsyncSession,
    conversation_id,
    *,
    for_update: bool = False,
) -> tuple[str, str, dict] | None:
    query = select(PlatformInboxConversation).where(
        PlatformInboxConversation.id == conversation_id
    )
    item = await db.scalar(query.with_for_update() if for_update else query)
    if item is None:
        return None
    return (
        "platform_inbox_conversation",
        str(item.id),
        {
            "channel": _enum(item.channel),
            "queue": _enum(item.queue),
            "status": _enum(item.status),
            "assignedToUserId": (
                str(item.assigned_to_user_id) if item.assigned_to_user_id else None
            ),
            "prospectId": str(item.prospect_id) if item.prospect_id else None,
            "supportTicketId": (
                str(item.support_ticket_id) if item.support_ticket_id else None
            ),
            "workspaceId": str(item.workspace_id) if item.workspace_id else None,
            "unreadCount": item.unread_count,
            "firstResponseDueAt": item.first_response_due_at,
            "firstRespondedAt": item.first_responded_at,
            "resolutionDueAt": item.resolution_due_at,
            "resolvedAt": item.resolved_at,
        },
    )


async def snapshot_platform_inbox_message(
    db: AsyncSession,
    message_id,
) -> tuple[str, str, dict] | None:
    item = await db.get(PlatformInboxMessage, message_id)
    if item is None:
        return None
    return (
        "platform_inbox_message",
        str(item.id),
        {
            "conversationId": str(item.conversation_id),
            "direction": _enum(item.direction),
            "deliveryStatus": _enum(item.delivery_status),
            "actorUserId": str(item.actor_user_id) if item.actor_user_id else None,
            "externalMessageConfigured": bool(item.external_message_id),
            "occurredAt": item.occurred_at,
        },
    )


async def snapshot_platform_growth_prospect(
    db: AsyncSession,
    prospect_id,
    *,
    for_update: bool = False,
) -> tuple[str, str, dict] | None:
    query = select(GrowthProspect).where(GrowthProspect.id == prospect_id)
    prospect = await db.scalar(query.with_for_update() if for_update else query)
    if prospect is None:
        return None
    metadata = dict(prospect.metadata_json or {})
    return (
        "growth_prospect",
        str(prospect.id),
        {
            "company": prospect.nome_escritorio,
            "stage": _enum(prospect.estagio),
            "source": _enum(prospect.origem),
            "ownerUserId": str(prospect.owner_user_id) if prospect.owner_user_id else None,
            "workspaceId": str(prospect.workspace_id) if prospect.workspace_id else None,
            "estimatedValueCents": prospect.valor_estimado_cents,
            "lossReason": prospect.motivo_perda,
            "score": metadata.get("score"),
            "nextAction": metadata.get("next_action"),
            "nextActionAt": metadata.get("next_action_at"),
            "campaign": metadata.get("campaign"),
            "utmSource": metadata.get("utm_source"),
            "utmMedium": metadata.get("utm_medium"),
            "utmCampaign": metadata.get("utm_campaign"),
            "proposalStatus": metadata.get("proposal_status"),
            "proposalReference": metadata.get("proposal_reference"),
            "proposalValueCents": metadata.get("proposal_value_cents"),
        },
    )


async def snapshot_platform_growth_activity(
    db: AsyncSession,
    activity_id,
) -> tuple[str, str, dict] | None:
    activity = await db.get(GrowthActivity, activity_id)
    if activity is None:
        return None
    return (
        "growth_activity",
        str(activity.id),
        {
            "prospectId": str(activity.prospect_id),
            "type": _enum(activity.tipo),
            "description": activity.descricao,
            "actorUserId": str(activity.actor_user_id) if activity.actor_user_id else None,
            "actorLabel": activity.actor_label,
            "occurredAt": activity.occurred_at,
        },
    )


async def snapshot_platform_config_document(
    db: AsyncSession,
    namespace: str,
    *,
    for_update: bool = False,
) -> tuple[str, str, dict] | None:
    query = select(PlatformConfigDocument).where(
        PlatformConfigDocument.namespace == namespace
    )
    document = await db.scalar(query.with_for_update() if for_update else query)
    if document is None:
        return None
    return (
        "platform_configuration",
        namespace,
        {
            "namespace": namespace,
            "payload": document.payload,
            "schemaVersion": document.schema_version,
            "currentVersion": document.current_version,
            "etag": document.etag,
        },
    )


async def snapshot_platform_site_document(
    db: AsyncSession,
    key: str,
    *,
    for_update: bool = False,
) -> tuple[str, str, dict] | None:
    """Captura conteúdo publicável sem duplicar payloads grandes sem limite."""
    query = select(PublicSiteDocument).where(PublicSiteDocument.key == key)
    document = await db.scalar(query.with_for_update() if for_update else query)
    if document is None:
        return None
    return (
        "public_site_document",
        str(document.id),
        {
            "key": document.key,
            "title": document.title,
            "description": document.description,
            "version": document.version,
            "draft": _studio_definition_snapshot(
                _canonical_site_content(document.key, document.draft_content)
            ),
            "published": _studio_definition_snapshot(
                _canonical_site_content(document.key, document.published_content)
            ),
            "updatedByUserId": (
                str(document.updated_by_user_id)
                if document.updated_by_user_id
                else None
            ),
            "publishedByUserId": (
                str(document.published_by_user_id)
                if document.published_by_user_id
                else None
            ),
            "publishedAt": document.published_at,
        },
    )


async def snapshot_platform_blog_post(
    db: AsyncSession,
    post_id,
    *,
    for_update: bool = False,
) -> tuple[str, str, dict] | None:
    query = select(BlogPost).where(BlogPost.id == post_id)
    post = await db.scalar(query.with_for_update() if for_update else query)
    if post is None:
        return None
    return (
        "blog_post",
        str(post.id),
        {
            "title": post.title,
            "slug": post.slug,
            "subtitle": post.subtitle,
            "excerpt": post.excerpt,
            "authorName": post.author_name,
            "authorRole": post.author_role,
            "status": _enum(post.status),
            "featured": post.featured,
            "categories": list(post.categories or []),
            "tags": list(post.tags or []),
            "contentBlocks": _studio_definition_snapshot(post.content_blocks),
            "coverMedia": _studio_definition_snapshot(post.cover_media),
            "ogMedia": _studio_definition_snapshot(post.og_media),
            "seo": _studio_definition_snapshot(post.seo),
            "indexing": _studio_definition_snapshot(post.indexing),
            "seoAudit": _studio_definition_snapshot(post.seo_audit),
            "aiConfig": _studio_definition_snapshot(post.ai_config),
            "readingTimeMinutes": post.reading_time_minutes,
            "publishedAt": post.published_at,
            "scheduledFor": post.scheduled_for,
            "updatedByUserId": (
                str(post.updated_by_user_id) if post.updated_by_user_id else None
            ),
            "revision": post.revision,
        },
    )


async def snapshot_platform_site_media_asset(
    db: AsyncSession,
    asset_id,
    *,
    for_update: bool = False,
) -> tuple[str, str, dict] | None:
    query = select(SiteMediaAsset).where(SiteMediaAsset.id == asset_id)
    asset = await db.scalar(query.with_for_update() if for_update else query)
    if asset is None:
        return None
    return (
        "site_media_asset",
        str(asset.id),
        {
            "title": asset.label,
            "key": asset.key,
            "mimeType": asset.mime_type,
            "fileKind": asset.file_kind,
            "extension": asset.extension,
            "status": _enum(asset.status),
            "source": asset.source,
            "storageConfigured": bool(asset.s3_key),
            "publicUrlConfigured": bool(asset.public_url),
            "altText": asset.alt_text,
            "caption": asset.caption,
            "width": asset.width,
            "height": asset.height,
            "sizeBytes": asset.size_bytes,
            "usageMetadata": _studio_definition_snapshot(asset.usage_metadata),
            "updatedByUserId": (
                str(asset.updated_by_user_id) if asset.updated_by_user_id else None
            ),
        },
    )


async def snapshot_platform_support_ticket(
    db: AsyncSession,
    ticket_id,
    *,
    for_update: bool = False,
) -> tuple[str, str, dict] | None:
    query = select(SupportTicket).where(SupportTicket.id == ticket_id)
    ticket = await db.scalar(query.with_for_update() if for_update else query)
    if ticket is None:
        return None
    return (
        "ticket",
        str(ticket.id),
        {
            "publicNumber": ticket.public_number,
            "title": ticket.subject,
            "workspaceId": str(ticket.workspace_id),
            "status": _enum(ticket.status),
            "queue": _enum(ticket.queue),
            "priority": _enum(ticket.priority),
            "severity": _enum(ticket.severity),
            "assignedToUserId": (
                str(ticket.assigned_to_user_id)
                if ticket.assigned_to_user_id
                else None
            ),
            "linkedIncidentId": (
                str(ticket.linked_incident_id) if ticket.linked_incident_id else None
            ),
            "firstResponseDueAt": ticket.first_response_due_at,
            "resolutionDueAt": ticket.resolution_due_at,
            "resolvedAt": ticket.resolved_at,
            "version": ticket.version,
        },
    )


async def snapshot_platform_support_saved_filter(
    db: AsyncSession,
    filter_id,
) -> tuple[str, str, dict] | None:
    saved_filter = await db.get(SupportSavedFilter, filter_id)
    if saved_filter is None:
        return None
    return (
        "support_saved_filter",
        str(saved_filter.id),
        {
            "title": saved_filter.name,
            "ownerUserId": str(saved_filter.owner_user_id),
            "filters": _studio_definition_snapshot(saved_filter.filters),
            "isDefault": saved_filter.is_default,
        },
    )


async def snapshot_platform_referral_policy(
    db: AsyncSession,
    policy_id=None,
    *,
    for_update: bool = False,
) -> tuple[str, str, dict] | None:
    query = select(ReferralProgramPolicy)
    if policy_id is not None:
        query = query.where(ReferralProgramPolicy.id == policy_id)
    else:
        query = query.order_by(ReferralProgramPolicy.version.desc()).limit(1)
    policy = await db.scalar(query.with_for_update() if for_update else query)
    if policy is None:
        return None
    return (
        "referral_program_policy",
        str(policy.id),
        {
            "version": policy.version,
            "status": _enum(policy.status),
            "rules": _studio_definition_snapshot(policy.rules_json),
            "reason": policy.reason,
            "effectiveAt": policy.effective_at,
            "createdByUserId": (
                str(policy.created_by_user_id) if policy.created_by_user_id else None
            ),
        },
    )


async def snapshot_platform_referral_attribution(
    db: AsyncSession,
    attribution_id,
    *,
    for_update: bool = False,
) -> tuple[str, str, dict] | None:
    query = select(SaasReferralAttribution).where(
        SaasReferralAttribution.id == attribution_id
    )
    item = await db.scalar(query.with_for_update() if for_update else query)
    if item is None:
        return None
    return (
        "saas_referral_attribution",
        str(item.id),
        {
            "referrerWorkspaceId": str(item.referrer_workspace_id),
            "referredWorkspaceId": str(item.referred_workspace_id),
            "status": _enum(item.status),
            "planCode": item.plan_code,
            "firstPaymentAt": item.first_payment_at,
            "holdUntil": item.hold_until,
            "qualifiedAt": item.qualified_at,
            "rewardedAt": item.rewarded_at,
            "paymentReferenceConfigured": bool(
                (item.metadata_json or {}).get("paymentReference")
            ),
        },
    )


async def snapshot_platform_benefit_grant(
    db: AsyncSession,
    grant_id,
    *,
    for_update: bool = False,
) -> tuple[str, str, dict] | None:
    query = select(BenefitGrant).where(BenefitGrant.id == grant_id)
    grant = await db.scalar(query.with_for_update() if for_update else query)
    if grant is None:
        return None
    return (
        "benefit_grant",
        str(grant.id),
        {
            "workspaceId": str(grant.workspace_id),
            "benefitType": grant.benefit_type,
            "unit": _enum(grant.unit),
            "amount": str(grant.amount),
            "status": _enum(grant.status),
            "sourceType": grant.source_type,
            "sourceRef": grant.source_ref,
            "reason": grant.reason,
            "ticketId": str(grant.ticket_id) if grant.ticket_id else None,
            "approvedByUserId": (
                str(grant.approved_by_user_id) if grant.approved_by_user_id else None
            ),
            "approvedAt": grant.approved_at,
            "appliedAt": grant.applied_at,
            "expiresAt": grant.expires_at,
            "reversedAt": grant.reversed_at,
        },
    )


async def snapshot_platform_coupon(
    db: AsyncSession,
    coupon_id,
    *,
    for_update: bool = False,
) -> tuple[str, str, dict] | None:
    query = select(Coupon).where(Coupon.id == coupon_id)
    coupon = await db.scalar(query.with_for_update() if for_update else query)
    if coupon is None:
        return None
    return (
        "coupon",
        str(coupon.id),
        {
            "title": coupon.name,
            "code": coupon.code,
            "description": coupon.description,
            "status": _enum(coupon.status),
            "benefitType": coupon.benefit_type,
            "unit": _enum(coupon.unit),
            "amount": str(coupon.amount),
            "allowedPlanCodes": list(coupon.allowed_plan_codes or []),
            "totalLimit": coupon.total_limit,
            "perWorkspaceLimit": coupon.per_workspace_limit,
            "redeemedCount": coupon.redeemed_count,
            "autoApply": coupon.auto_apply,
            "startsAt": coupon.starts_at,
            "endsAt": coupon.ends_at,
            "version": coupon.version,
        },
    )


async def snapshot_platform_partner_membership(
    db: AsyncSession,
    workspace_id,
    *,
    for_update: bool = False,
) -> tuple[str, str, dict] | None:
    query = select(PartnerMembership).where(
        PartnerMembership.workspace_id == workspace_id
    )
    item = await db.scalar(query.with_for_update() if for_update else query)
    if item is None:
        return None
    return (
        "partner_membership",
        str(item.id),
        {
            "workspaceId": str(item.workspace_id),
            "status": _enum(item.status),
            "tier": item.tier,
            "activeReferrals": item.active_referrals,
            "referredMrrCents": item.referred_mrr_cents,
            "reviewedAt": item.reviewed_at,
            "nextReviewAt": item.next_review_at,
            "exemptionEndsAt": item.exemption_ends_at,
            "reviewedByUserId": (
                str(item.reviewed_by_user_id) if item.reviewed_by_user_id else None
            ),
        },
    )


async def snapshot_platform_google_business_connection(
    db: AsyncSession,
    connection_id=None,
    *,
    for_update: bool = False,
) -> tuple[str, str, dict] | None:
    query = select(GoogleBusinessProfileConnection)
    if connection_id is not None:
        query = query.where(GoogleBusinessProfileConnection.id == connection_id)
    connection = await db.scalar(query.with_for_update() if for_update else query)
    if connection is None:
        return None
    return (
        "google_business_connection",
        str(connection.id),
        {
            "title": connection.business_name,
            "accountName": connection.account_name,
            "locationId": connection.location_id,
            "locationName": connection.location_name,
            "status": _enum(connection.status),
            "profileUrl": connection.profile_url,
            "clientIdConfigured": bool(connection.client_id),
            "clientSecretConfigured": bool(connection.client_secret),
            "refreshTokenConfigured": bool(connection.refresh_token),
            "accessTokenConfigured": bool(connection.access_token),
            "scopes": list(connection.scopes or []),
            "settings": _studio_definition_snapshot(connection.settings),
            "lastSyncAt": connection.last_sync_at,
            "updatedByUserId": (
                str(connection.updated_by_user_id)
                if connection.updated_by_user_id
                else None
            ),
        },
    )


async def snapshot_platform_google_business_job(
    db: AsyncSession,
    job_id,
) -> tuple[str, str, dict] | None:
    job = await db.get(GoogleBusinessPublishJob, job_id)
    if job is None:
        return None
    return (
        "google_business_publish_job",
        str(job.id),
        {
            "title": job.title,
            "connectionId": str(job.connection_id),
            "targetType": job.target_type,
            "targetId": str(job.target_id) if job.target_id else None,
            "status": _enum(job.status),
            "scheduledFor": job.scheduled_for,
            "publishedAt": job.published_at,
            "payload": _studio_definition_snapshot(job.payload),
            "providerResponse": _studio_definition_snapshot(job.provider_response),
            "errorMessage": job.error_message,
        },
    )


async def snapshot_platform_incident(
    db: AsyncSession,
    incident_id,
    *,
    for_update: bool = False,
) -> tuple[str, str, dict] | None:
    query = select(PlatformIncident).where(PlatformIncident.id == incident_id)
    incident = await db.scalar(query.with_for_update() if for_update else query)
    if incident is None:
        return None
    return (
        "incident",
        str(incident.id),
        {
            "title": incident.title,
            "summary": incident.summary,
            "severity": incident.severity,
            "status": incident.status,
            "components": incident.components or [],
            "ownerUserId": str(incident.owner_user_id) if incident.owner_user_id else None,
            "updates": incident.updates or [],
            "startedAt": incident.started_at,
            "resolvedAt": incident.resolved_at,
        },
    )


async def snapshot_platform_user_session(
    db: AsyncSession,
    session_id,
    *,
    for_update: bool = False,
) -> tuple[str, str, dict] | None:
    query = select(UserSession).where(UserSession.id == session_id)
    session = await db.scalar(query.with_for_update() if for_update else query)
    if session is None:
        return None
    return (
        "user_session",
        str(session.id),
        {
            "userId": str(session.user_id),
            "lastWorkspaceId": (
                str(session.last_workspace_id) if session.last_workspace_id else None
            ),
            "deviceLabel": session.device_label,
            "deviceType": session.device_type,
            "browser": session.browser,
            "operatingSystem": session.operating_system,
            "expiresAt": session.expires_at,
            "revokedAt": session.revoked_at,
            "revocationReason": session.revocation_reason,
        },
    )


async def snapshot_platform_request_resource(
    request: Request,
    db: AsyncSession,
) -> tuple[str, str, dict] | None:
    """Capture safe, domain-level state for legacy administrative mutations.

    The adapter is intentionally explicit. It never serializes ORM objects or
    secret-bearing columns and therefore remains safe even for platform root.
    """
    params = request.path_params
    route_path = str(getattr(request.scope.get("route"), "path", request.url.path))
    resource = None
    resource_type = ""
    resource_id = ""
    if params.get("workspace_id") and "/subscriptions/" in route_path:
        resource_type, resource_id = "subscription", str(params["workspace_id"])
        resource = await db.scalar(select(WorkspaceSubscription).where(
            WorkspaceSubscription.workspace_id == params["workspace_id"]
        ))
    elif params.get("workspace_id"):
        resource_type, resource_id = "workspace", str(params["workspace_id"])
        resource = await db.scalar(select(Workspace).options(
            selectinload(Workspace.subscription)
        ).where(Workspace.id == params["workspace_id"]))
    elif params.get("user_id"):
        resource_type, resource_id = (
            "staff" if "/staff" in route_path else "user",
            str(params["user_id"]),
        )
        resource = await db.get(User, params["user_id"])
    elif params.get("plan_id"):
        resource_type, resource_id = "plan", str(params["plan_id"])
        resource = await db.get(Plan, params["plan_id"])
    elif params.get("plan_key"):
        resource_type, resource_id = "plan", str(params["plan_key"])
        tier = {
            "basic": PlanTier.starter,
            "pro": PlanTier.pro,
            "scale": PlanTier.business,
        }.get(str(params["plan_key"]))
        resource = await db.scalar(select(Plan).where(Plan.tier == tier)) if tier else None
    elif params.get("subscription_id"):
        resource_type, resource_id = "subscription", str(params["subscription_id"])
        resource = await db.get(Subscription, params["subscription_id"])
    elif params.get("ticket_id"):
        resource_type, resource_id = "ticket", str(params["ticket_id"])
        resource = await db.get(SupportTicket, params["ticket_id"])
    elif params.get("prospect_id"):
        resource_type, resource_id = "lead", str(params["prospect_id"])
        resource = await db.get(GrowthProspect, params["prospect_id"])
    elif params.get("incident_id"):
        resource_type, resource_id = "incident", str(params["incident_id"])
        resource = await db.get(PlatformIncident, params["incident_id"])
    elif params.get("release_id"):
        resource_type, resource_id = "release", str(params["release_id"])
        resource = await db.get(PlatformRelease, params["release_id"])
    elif params.get("post_id"):
        return await snapshot_platform_blog_post(db, params["post_id"])
    elif params.get("asset_id"):
        return await snapshot_platform_site_media_asset(db, params["asset_id"])
    elif params.get("error_id"):
        resource_type, resource_id = "error_log", str(params["error_id"])
        resource = await db.get(ErrorLog, UUID(str(params["error_id"])))
    elif params.get("namespace") and "/configurations/" in route_path:
        resource_type, resource_id = "platform_configuration", str(params["namespace"])
        resource = await db.scalar(select(PlatformConfigDocument).where(
            PlatformConfigDocument.namespace == params["namespace"]
        ))
    if resource is None:
        return None
    if isinstance(resource, Workspace):
        snapshot = snapshot_platform_workspace(resource)
    elif isinstance(resource, User):
        snapshot = snapshot_platform_user(resource)
    elif isinstance(resource, Plan):
        snapshot = {
            "name": resource.name, "description": resource.description,
            "priceMonthlyCents": resource.price_monthly_cents,
            "priceAnnuallyCents": resource.price_annually_cents,
            "maxUsers": resource.max_users, "maxMatters": resource.max_matters,
            "maxTokensPerMonth": resource.max_tokens_per_month,
            "maxAiAgents": resource.max_ai_agents, "features": resource.features,
            "isActive": resource.is_active, "isPublic": resource.is_public,
            "sortOrder": resource.sort_order,
        }
    elif isinstance(resource, WorkspaceSubscription):
        snapshot = {
            "workspaceId": str(resource.workspace_id),
            "plan": _enum(resource.plano),
            "status": _enum(resource.status),
            "amountCents": resource.valor_mensal_cents,
            "interval": resource.ciclo,
            "discountPercent": resource.desconto_pct,
            "courtesy": resource.cortesia,
            "trialEnd": resource.trial_ends_at,
            "currentPeriodStart": resource.current_period_start,
            "currentPeriodEnd": resource.current_period_end,
            "cancelledAt": resource.cancelled_at,
        }
    elif isinstance(resource, Subscription):
        snapshot = {
            "planId": str(resource.plan_id), "status": _enum(resource.status),
            "trialEnd": resource.trial_end, "amountCents": resource.amount_cents,
            "cancelAtPeriodEnd": resource.cancel_at_period_end,
        }
    elif isinstance(resource, SupportTicket):
        snapshot = {
            "publicNumber": resource.public_number, "status": _enum(resource.status),
            "queue": _enum(resource.queue), "priority": _enum(resource.priority),
            "severity": _enum(resource.severity),
            "assignedToUserId": (
                str(resource.assigned_to_user_id) if resource.assigned_to_user_id else None
            ),
            "version": resource.version,
        }
    elif isinstance(resource, GrowthProspect):
        snapshot = {
            "company": resource.nome_escritorio, "stage": _enum(resource.estagio),
            "source": _enum(resource.origem),
            "ownerUserId": str(resource.owner_user_id) if resource.owner_user_id else None,
            "estimatedValueCents": resource.valor_estimado_cents,
        }
    elif isinstance(resource, PlatformIncident):
        snapshot = {
            "title": resource.title, "severity": resource.severity,
            "status": resource.status,
            "ownerUserId": str(resource.owner_user_id) if resource.owner_user_id else None,
            "updatedAt": resource.updated_at,
        }
    elif isinstance(resource, PlatformRelease):
        snapshot = {
            "version": resource.version, "environment": resource.environment,
            "status": resource.status, "commitSha": resource.commit_sha,
            "revision": resource.revision,
        }
    elif isinstance(resource, ErrorLog):
        snapshot = {
            "errorCode": resource.error_code,
            "severity": _enum(resource.severity),
            "status": _enum(resource.status),
            "occurrences": resource.occurrences,
            "resolvedAt": resource.resolved_at,
            "resolvedBy": resource.resolved_by,
            "resolutionNotes": resource.resolution_notes,
        }
    else:
        snapshot = {
            "namespace": resource.namespace, "payload": resource.payload,
            "schemaVersion": resource.schema_version,
            "currentVersion": resource.current_version, "etag": resource.etag,
        }
    return resource_type, resource_id, snapshot


async def _record_platform_mutation(
    request: Request,
    context: AuthContext,
    db: AsyncSession,
    outcome: str,
    error_code: str | None,
    *,
    resource_snapshot: tuple[str, str, dict] | None = None,
    after_snapshot: tuple[str, str, dict] | None = None,
    module: str = "platform",
    action: str | None = None,
    reason: str | None = None,
    reason_source: str = "system",
    session_type: str | None = None,
    administrative_session_id: UUID | None = None,
) -> None:
    actor = await db.get(User, context.user_id)
    route = request.scope.get("route")
    route_name = str(getattr(route, "name", "platform_mutation"))[:48]
    correlation_id = request.headers.get("X-Correlation-ID", "").strip() or str(uuid4())
    workspace_id = request.path_params.get("workspace_id")
    resource_id = next(
        (str(value) for key, value in request.path_params.items() if key.endswith("_id")),
        None,
    )
    resource_type = route_name
    before = None
    after = None
    if resource_snapshot:
        resource_type, resource_id, before = resource_snapshot
    if after_snapshot:
        after_type, after_id, after = after_snapshot
        resource_type = after_type
        resource_id = after_id
    # Some platform commands select the tenant through a query/body contract
    # rather than a ``{workspace_id}`` route segment (for example an agent
    # execution).  Preserve that ownership in the canonical event instead of
    # silently producing a platform-wide audit row.
    if workspace_id is None:
        for snapshot in (after, before):
            candidate = snapshot.get("workspaceId") if isinstance(snapshot, dict) else None
            if candidate:
                workspace_id = str(candidate)
                break
    route_path = str(getattr(route, "path", request.url.path))
    actor_role = actor.platform_role_package if actor else "unknown"
    actor_is_root = is_platform_root(actor)
    resolved_session_type = session_type or context.administrative_session_type
    resolved_administrative_session_id = (
        administrative_session_id
        if administrative_session_id is not None
        else context.administrative_session_id
    )
    workspace_label_snapshot = None
    if workspace_id:
        workspace_label_snapshot = await db.scalar(
            select(Workspace.name).where(Workspace.id == workspace_id)
        )
    await PlatformAuditEventService(db).record(
        PlatformAuditEventCreate(
            actor_id=context.user_id,
            actor_role_snapshot=actor_role or "unknown",
            is_platform_root=actor_is_root,
            workspace_id=workspace_id,
            workspace_label_snapshot=workspace_label_snapshot,
            administrative_session_id=resolved_administrative_session_id,
            session_type=resolved_session_type,
            module=module,
            action=action or route_name,
            resource_type=resource_type,
            resource_id=resource_id,
            resource_label_snapshot=_snapshot_label(after) or _snapshot_label(before),
            result=outcome,
            before=before,
            after=after,
            reason=reason or f"Mutação administrativa via {request.method.upper()} {route_path}",
            reason_source=reason_source,
            correlation_id=correlation_id,
            operation_id=correlation_id,
            resource_version_before=_snapshot_version(before),
            resource_version_after=_snapshot_version(after),
            error_code=error_code,
            route=route_path,
            method=request.method.upper(),
        )
    )
    db.add(AuthSecurityEvent(
        event_type=f"platform_mutation_{route_name}"[:80],
        outcome=outcome,
        actor_user_id=context.user_id,
        administrative_session_id=resolved_administrative_session_id,
        network_digest=_digest(request.client.host if request.client else ""),
        user_agent_digest=_digest(request.headers.get("user-agent", "")),
        details={
            "module": "platform",
            "action": request.method.upper(),
            "route": route_path,
            "workspace_id": str(workspace_id) if workspace_id else None,
            "session_type": resolved_session_type,
            "actor_role": actor_role,
            "is_platform_root": actor_is_root,
            "correlation_id": correlation_id,
            "error_code": error_code,
            "resource_id": resource_id,
        },
    ))


async def record_canonical_platform_mutation(
    request: Request,
    context: AuthContext,
    db: AsyncSession,
    *,
    before_snapshot: tuple[str, str, dict] | None,
    after_snapshot: tuple[str, str, dict] | None,
    module: str,
    action: str,
    reason: str,
    reason_source: str = "user",
    session_type: str | None = None,
    administrative_session_id: UUID | None = None,
) -> None:
    """Grave o evento ainda dentro do comando e da sua transação.

    Rotas críticas usam este caminho em vez de depender do finalizador
    transversal, pois finalizadores de dependências podem ocorrer depois do
    encerramento transacional de outra dependência compartilhada.
    """
    await _record_platform_mutation(
        request,
        context,
        db,
        "success",
        None,
        resource_snapshot=before_snapshot,
        after_snapshot=after_snapshot,
        module=module,
        action=action,
        reason=reason,
        reason_source=reason_source,
        session_type=session_type,
        administrative_session_id=administrative_session_id,
    )
    request.state.canonical_platform_audit_recorded = True
