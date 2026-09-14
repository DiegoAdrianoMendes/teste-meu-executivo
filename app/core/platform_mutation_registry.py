"""Canonical OpenAPI descriptors for every administrative mutation."""

from __future__ import annotations

from typing import Any

_PARAM_RESOURCE = {
    "workspace_id": ("workspace", "workspace"),
    "user_id": ("user", "user"),
    "plan_id": ("plan", "plan"),
    "subscription_id": ("subscription", "subscription"),
    "ticket_id": ("ticket", "ticket"),
    "prospect_id": ("lead", "lead"),
    "incident_id": ("incident", "incident"),
    "release_id": ("release", "release"),
    "post_id": ("blog_post", "blog_post"),
    "asset_id": ("site_media_asset", "site_media_asset"),
    "namespace": ("platform_configuration", "platform_configuration"),
}

_RESTORABLE = {
    "workspace", "user", "staff", "plan", "subscription", "platform_configuration",
    "customer_membership",
    "platform_staff_reassignment",
}

_MODULE_BY_SEGMENT = {
    "audit-events": "audit", "dashboard": "dashboard", "exports": "exports",
    "restorations": "restorations", "work-items": "dashboard", "operations": "bulk_operations",
    "search": "dashboard", "inbox": "support", "growth": "sales", "support": "support",
    "staff": "staff", "plans": "plans", "benefits": "benefits", "coupons": "coupons",
    "governance": "settings", "configuration-studio": "workspace_configuration",
    "official-whatsapp": "whatsapp", "site": "content", "blog": "content",
    "ai-credentials": "ai_operations",
}


def administrative_mutation_descriptor(
    *, path: str, method: str, operation: dict[str, Any]
) -> dict[str, Any]:
    operation_id = str(operation.get("operationId") or f"{method}:{path}")
    segments = [segment for segment in path.split("/") if segment and not segment.startswith("{")]
    platform_index = segments.index("platform") if "platform" in segments else -1
    domain_segment = (
        segments[platform_index + 1]
        if platform_index >= 0 and len(segments) > platform_index + 1
        else "platform"
    )
    module = _MODULE_BY_SEGMENT.get(domain_segment, domain_segment.replace("-", "_"))
    resource_type = operation_id.split("_")[0] or "platform_resource"
    snapshot_adapter = "none"
    if "/subscriptions/{workspace_id}" in path:
        resource_type = "subscription"
        snapshot_adapter = "subscription_commercial"
    elif "/plans/{plan_key}" in path:
        resource_type = "plan"
        snapshot_adapter = "plan"
    elif path.startswith("/api/v1/platform/site/"):
        resource_type = "public_site_document"
        snapshot_adapter = "public_site_document"
    elif path.startswith("/api/v1/platform/blog/posts"):
        resource_type = "blog_post"
        snapshot_adapter = "blog_post"
    elif path.startswith("/api/v1/platform/blog/media/assets"):
        resource_type = "site_media_asset"
        snapshot_adapter = "site_media_asset"
    elif path.startswith("/api/v1/platform/blog/google-business/jobs"):
        resource_type = "google_business_publish_job"
        snapshot_adapter = "google_business_publish_job"
    elif path.startswith("/api/v1/platform/blog/google-business"):
        resource_type = "google_business_connection"
        snapshot_adapter = "google_business_connection"
    elif path.startswith("/api/v1/platform/blog/"):
        resource_type = "public_site_document"
        snapshot_adapter = "public_site_document"
    elif path in {
        "/api/v1/platform/support/availability-settings",
        "/api/v1/platform/support/escalation-settings",
    }:
        resource_type = "platform_configuration"
        snapshot_adapter = "platform_configuration"
    elif path == "/api/v1/platform/support/ws-ticket":
        resource_type = "support_websocket_ticket"
        snapshot_adapter = "support_websocket_ticket"
    elif path == "/api/v1/platform/support/tickets":
        resource_type = "ticket"
        snapshot_adapter = "ticket"
    elif path == "/api/v1/platform/support/saved-filters":
        resource_type = "support_saved_filter"
        snapshot_adapter = "support_saved_filter"
    elif path == "/api/v1/platform/support/migrate-technical-requests":
        resource_type = "support_technical_migration"
        snapshot_adapter = "support_technical_migration"
    elif path.startswith("/api/v1/platform/inbox/conversations"):
        resource_type = (
            "platform_inbox_message"
            if path.endswith("/messages")
            else "platform_inbox_conversation"
        )
        snapshot_adapter = resource_type
    elif path == "/api/v1/platform/saas-benefits/policy":
        resource_type = "referral_program_policy"
        snapshot_adapter = resource_type
    elif "/api/v1/platform/saas-benefits/referrals/{attribution_id}" in path:
        resource_type = "saas_referral_attribution"
        snapshot_adapter = resource_type
    elif path == "/api/v1/platform/saas-benefits/referrals/process-eligible":
        resource_type = "saas_referral_processing"
        snapshot_adapter = resource_type
    elif path.startswith("/api/v1/platform/saas-benefits/grants"):
        resource_type = "benefit_grant"
        snapshot_adapter = resource_type
    elif path.startswith("/api/v1/platform/saas-benefits/partners"):
        resource_type = "partner_membership"
        snapshot_adapter = resource_type
    elif path.startswith("/api/v1/platform/saas-benefits/coupons"):
        resource_type = "coupon"
        snapshot_adapter = resource_type
    elif path.startswith("/api/v1/platform/organization/teams/{team_id}/members"):
        resource_type = "platform_team_membership"
        snapshot_adapter = resource_type
    elif path.startswith("/api/v1/platform/organization/teams"):
        resource_type = "platform_team"
        snapshot_adapter = resource_type
    elif path.endswith("/organization/dashboard-preference"):
        resource_type = "dashboard_preference"
        snapshot_adapter = resource_type
    elif "/organization/staff-invitations" in path:
        resource_type = "staff_invitation"
        snapshot_adapter = resource_type
    elif path.endswith("/organization/operational-assignments"):
        resource_type = "operational_assignment"
        snapshot_adapter = resource_type
    elif path.endswith("/organization/reviews"):
        resource_type = "review_request"
        snapshot_adapter = resource_type
    elif path.endswith("/assignments") and "/organization/workspaces/" in path:
        resource_type = "workspace_assignment"
        snapshot_adapter = resource_type
    elif path.endswith("/milestones") and "/organization/workspaces/" in path:
        resource_type = "onboarding_milestone"
        snapshot_adapter = resource_type
    elif path == "/api/v1/platform/operations/selections":
        resource_type = "bulk_selection"
        snapshot_adapter = resource_type
    elif path == "/api/v1/platform/operations/bulk/previews":
        resource_type = "bulk_preview"
        snapshot_adapter = resource_type
    elif path == "/api/v1/platform/operations/bulk/jobs":
        resource_type = "bulk_job"
        snapshot_adapter = resource_type
    elif path.endswith("/cancel") and "/platform/operations/bulk/jobs/" in path:
        resource_type = "bulk_job"
        snapshot_adapter = resource_type
    elif path.endswith("/retry-failures") and "/platform/operations/bulk/jobs/" in path:
        resource_type = "bulk_preview"
        snapshot_adapter = resource_type
    elif path.endswith("/restoration-preview") and "/platform/operations/bulk/jobs/" in path:
        resource_type = "bulk_preview"
        snapshot_adapter = resource_type
    elif path.endswith("/restore") and "/platform/operations/bulk/jobs/" in path:
        resource_type = "bulk_job"
        snapshot_adapter = resource_type
    elif path.startswith("/api/v1/platform/governance/feature-flags/"):
        resource_type = "platform_configuration"
        snapshot_adapter = resource_type
    elif path.startswith("/api/v1/platform/governance/security/sessions/"):
        resource_type = "user_session"
        snapshot_adapter = resource_type
    elif path.startswith("/api/v1/platform/governance/security/incidents"):
        resource_type = "platform_incident"
        snapshot_adapter = resource_type
    elif path.startswith("/api/v1/platform/governance/releases/records"):
        resource_type = "platform_release"
        snapshot_adapter = resource_type
    elif path.startswith("/api/v1/platform/governance/"):
        resource_type = "platform_configuration"
        snapshot_adapter = resource_type
    elif path.startswith("/api/v1/platform/blackbox-studios/creative-library/items"):
        resource_type = "platform_configuration"
        snapshot_adapter = resource_type
    elif path.endswith("/assign") and "/platform/work-items/" in path:
        resource_type = "platform_work_item"
        snapshot_adapter = resource_type
    elif path.endswith("/snooze") and "/platform/work-items/" in path:
        resource_type = "platform_work_item"
        snapshot_adapter = resource_type
    elif "/platform/work-items/" in path and "/commands/" in path:
        resource_type = "work_item_source"
        snapshot_adapter = resource_type
    elif path == "/api/v1/platform/work-items/reconcile":
        resource_type = "platform_work_item_projection"
        snapshot_adapter = resource_type
    elif path.startswith("/api/v1/platform/exports"):
        resource_type = "platform_export"
        snapshot_adapter = resource_type
    elif path == "/api/v1/platform/restorations/previews":
        resource_type = "restoration_preview"
        snapshot_adapter = resource_type
    elif path.startswith("/api/v1/platform/restorations"):
        resource_type = "restoration_job"
        snapshot_adapter = resource_type
    elif path.startswith("/api/v1/platform/commercial/"):
        if path in {
            "/api/v1/platform/commercial/contracts/preview",
            "/api/v1/platform/commercial/contracts/legacy-preview",
        } or path.endswith(("/revision-preview", "/restore-preview", "/draft/preview")):
            resource_type = "non_mutating_query"
            snapshot_adapter = "not_applicable_query"
        elif path.endswith(("/draft", "/draft/discard")):
            module = "plans"
            resource_type = "commercial_offer_draft"
            snapshot_adapter = resource_type
        elif "/offers/" in path:
            module = "plans"
            resource_type = "commercial_offer_version"
            snapshot_adapter = resource_type
        elif "/adjustments" in path:
            module = "commercial_adjustments"
            resource_type = "commercial_adjustment"
            snapshot_adapter = resource_type
        elif "/bonuses" in path:
            module = "entitlement_bonuses"
            resource_type = "entitlement_bonus"
            snapshot_adapter = resource_type
        else:
            module = "commercial_contracts"
            resource_type = "workspace_commercial_contract"
            snapshot_adapter = resource_type
    elif path == "/api/v1/platform/workspaces":
        resource_type = "workspace"
        snapshot_adapter = resource_type
    elif path == "/api/v1/platform/staff":
        resource_type = "staff"
        snapshot_adapter = resource_type
    elif path == "/api/v1/platform/people/invitations":
        resource_type = "workspace_invitation"
        snapshot_adapter = resource_type
    elif path == "/api/v1/platform/growth/prospects":
        resource_type = "lead"
        snapshot_adapter = resource_type
    elif path.endswith("/people/{workspace_id}/{user_id}/block") or path.endswith(
        "/people/{workspace_id}/{user_id}/access-package"
    ):
        resource_type = "customer_membership"
        snapshot_adapter = resource_type
    elif path.endswith("/people/{workspace_id}/{user_id}/sessions/revoke"):
        resource_type = "customer_membership"
        snapshot_adapter = resource_type
    elif path.endswith("/people/{workspace_id}/{user_id}/transfer-ownership"):
        resource_type = "workspace_ownership"
        snapshot_adapter = resource_type
    elif path in {
        "/api/v1/platform/search/query",
        "/api/v1/platform/lookups/query",
        "/api/v1/platform/lookups/resolve",
    }:
        resource_type = "non_mutating_query"
        snapshot_adapter = "not_applicable_query"
    elif path == "/api/v1/platform/workspace-bulk-actions":
        resource_type = "deprecated_bulk_endpoint"
        snapshot_adapter = "not_applicable_deprecated"
    elif path == "/api/v1/platform/notices/dispatch-due":
        resource_type = "platform_notice_dispatch"
        snapshot_adapter = resource_type
    elif path.startswith("/api/v1/platform/notices"):
        resource_type = "platform_notice"
        snapshot_adapter = resource_type
    elif path.startswith("/api/v1/platform/support-access"):
        resource_type = "support_access_grant"
        snapshot_adapter = resource_type
    elif path == "/api/v1/platform/staff-reassignments/preview":
        resource_type = "non_mutating_query"
        snapshot_adapter = "not_applicable_query"
    elif path.startswith("/api/v1/platform/staff-reassignments"):
        resource_type = "platform_staff_reassignment"
        snapshot_adapter = resource_type
    elif path.startswith("/api/v1/platform/meta-commercial-rate-cards"):
        resource_type = "meta_commercial_rate_card"
        snapshot_adapter = resource_type
    elif path.startswith("/api/v1/platform/ai-credentials"):
        # Chaves dos provedores de IA da plataforma (lote 5): write-only,
        # auditoria canônica gravada pelo próprio endpoint sem a chave.
        resource_type = "platform_model_credential"
        snapshot_adapter = resource_type
    elif path.startswith("/api/v1/platform/ai-token-packages"):
        resource_type = "ai_token_package"
        snapshot_adapter = resource_type
    elif path.startswith("/api/v1/platform/official-whatsapp"):
        resource_type = "platform_official_whatsapp_connection"
        snapshot_adapter = resource_type
    elif path.startswith("/api/v1/error-logs/"):
        resource_type = "error_log"
        snapshot_adapter = resource_type
    elif path.startswith("/api/v1/agent-platform/"):
        module = "agents"
        if path.endswith("/validate"):
            resource_type = "non_mutating_query"
            snapshot_adapter = "not_applicable_query"
        elif "/decisions" in path:
            resource_type = "human_decision"
            snapshot_adapter = resource_type
        else:
            resource_type = "agent_deployment"
            snapshot_adapter = resource_type
    elif path.startswith("/api/v1/finance/dimensional/"):
        module = "finance"
        finance_queries = {
            "/api/v1/finance/dimensional/lookups/resolve",
            "/api/v1/finance/dimensional/cost-centers/{cost_center_id}/impact/preview",
            "/api/v1/finance/dimensional/analytics/query",
            "/api/v1/finance/dimensional/analytics/drilldown",
            "/api/v1/finance/dimensional/dre",
            "/api/v1/finance/dimensional/tax-reform/simulate",
            "/api/v1/finance/dimensional/valuations/calculate",
        }
        if path in finance_queries:
            resource_type = "non_mutating_query"
            snapshot_adapter = "not_applicable_query"
        elif "/cost-centers" in path:
            resource_type = "finance_cost_center"
            snapshot_adapter = resource_type
        elif "/budget-lines" in path:
            resource_type = "finance_budget_line"
            snapshot_adapter = resource_type
        elif "/allocations" in path:
            resource_type = "finance_allocation"
            snapshot_adapter = resource_type
        elif "/forecasts" in path:
            resource_type = "finance_forecast"
            snapshot_adapter = resource_type
        elif "/valuations" in path:
            resource_type = "finance_valuation"
            snapshot_adapter = resource_type
        elif path.endswith("/tax-profile"):
            resource_type = "finance_tax_profile"
            snapshot_adapter = resource_type
        elif path.endswith("/tax-reform/request-review"):
            resource_type = "human_decision"
            snapshot_adapter = resource_type
    elif path.startswith("/api/v1/finance/exports"):
        module = "finance"
        resource_type = "finance_export"
        snapshot_adapter = resource_type
    for parameter, (candidate_type, candidate_adapter) in _PARAM_RESOURCE.items():
        if snapshot_adapter != "none":
            break
        if f"{{{parameter}}}" in path:
            resource_type = (
                "staff"
                if parameter == "user_id" and "/staff" in path
                else candidate_type
            )
            snapshot_adapter = "staff" if resource_type == "staff" else candidate_adapter
            break
    parameters = operation.get("parameters") or []
    idempotent = any(
        str(parameter.get("name", "")).casefold() == "idempotency-key"
        for parameter in parameters if isinstance(parameter, dict)
    )
    sensitive_tokens = {
        "suspend", "archive", "restore", "rollback", "delete", "revoke", "publish",
        "transfer", "discount", "trial", "permission", "role", "plan", "flag",
    }
    lowered = f"{operation_id} {path}".casefold()
    external_effect = any(token in lowered for token in {
        "whatsapp", "email", "messenger", "instagram", "publish", "webhook", "invite", "billing",
    })
    reason_required = (
        path.startswith("/api/v1/platform/commercial/")
        and snapshot_adapter != "not_applicable_query"
    ) or path.startswith((
        "/api/v1/platform/site/", "/api/v1/platform/blog/"
    )) or path == "/api/v1/platform/saas-benefits/policy" or path.startswith(
        "/api/v1/platform/saas-benefits/grants"
    ) or path.startswith(
        "/api/v1/platform/saas-benefits/coupons"
    ) or path in {
        "/api/v1/platform/support/availability-settings",
        "/api/v1/platform/support/escalation-settings",
        "/api/v1/platform/support/tickets",
        "/api/v1/platform/support/migrate-technical-requests",
    } or (
        path.startswith("/api/v1/platform/organization/")
        and not path.endswith("/dashboard-preference")
    ) or path.startswith((
        "/api/v1/agent-platform/decisions",
        "/api/v1/finance/dimensional/tax-reform/request-review",
    )) or any(
        token in lowered for token in sensitive_tokens
    )
    restoration_forbidden = (
        "/sessions/revoke" in path
        or "/invitations" in path
        or "transfer-ownership" in path
    )
    return {
        "key": operation_id,
        "module": module,
        "action": operation_id,
        "resourceType": resource_type,
        "reasonPolicy": "required" if reason_required else "system_allowed",
        "snapshotAdapter": snapshot_adapter,
        "mutationKind": (
            "query"
            if snapshot_adapter == "not_applicable_query"
            else "deprecated"
            if snapshot_adapter == "not_applicable_deprecated"
            else "command"
        ),
        "restorationAdapter": (
            snapshot_adapter
            if resource_type in _RESTORABLE and not restoration_forbidden
            else None
        ),
        "idempotencyPolicy": "required" if idempotent else "optional",
        "externalEffect": external_effect,
        "auditSchemaVersion": 1,
    }
