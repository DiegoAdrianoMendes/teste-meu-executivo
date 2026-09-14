"""
Router principal da API v1.
Agrega todos os sub-routers de endpoints.
"""

from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.routing import APIRoute
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.admin import router as admin_router
from app.api.v1.endpoints.admin_automation_rules import router as admin_automation_rules_router
from app.api.v1.endpoints.agenda import router as agenda_router
from app.api.v1.endpoints.agent_platform import router as agent_platform_router
from app.api.v1.endpoints.agent_runtime import router as agent_runtime_router
from app.api.v1.endpoints.approvals import router as approvals_router
from app.api.v1.endpoints.asaas_webhook import router as asaas_webhook_router
from app.api.v1.endpoints.auth import router as auth_router
from app.api.v1.endpoints.bank_reconciliation import router as bank_reconciliation_router
from app.api.v1.endpoints.client_contracts import router as client_contracts_router
from app.api.v1.endpoints.clients import router as clients_router
from app.api.v1.endpoints.commercial_self_service import (
    platform_router as commercial_self_service_platform_router,
)
from app.api.v1.endpoints.commercial_self_service import (
    public_router as commercial_self_service_public_router,
)
from app.api.v1.endpoints.commercial_self_service import (
    workspace_router as commercial_self_service_workspace_router,
)
from app.api.v1.endpoints.configurations import router as configurations_router
from app.api.v1.endpoints.contract_billing import router as contract_billing_router
from app.api.v1.endpoints.conversation_collaboration import (
    router as conversation_collaboration_router,
)
from app.api.v1.endpoints.conversation_response_metrics import (
    router as conversation_response_metrics_router,
)
from app.api.v1.endpoints.documents import router as documents_router
from app.api.v1.endpoints.error_logs import router as error_logs_router
from app.api.v1.endpoints.exception_center import router as exception_center_router
from app.api.v1.endpoints.executives import router as executives_router
from app.api.v1.endpoints.fee_arrangements import router as fee_arrangements_router
from app.api.v1.endpoints.finance import router as finance_router
from app.api.v1.endpoints.finance_charges import router as finance_charges_router
from app.api.v1.endpoints.finance_dimensional import router as finance_dimensional_router
from app.api.v1.endpoints.finance_dunning import router as finance_dunning_router
from app.api.v1.endpoints.finance_executive import router as finance_executive_router
from app.api.v1.endpoints.finance_expenses import router as finance_expenses_router
from app.api.v1.endpoints.finance_exports import router as finance_exports_router
from app.api.v1.endpoints.finance_harmonization import router as finance_harmonization_router
from app.api.v1.endpoints.finance_recurrences import router as finance_recurrences_router
from app.api.v1.endpoints.finance_reports import router as finance_reports_router
from app.api.v1.endpoints.flows import router as flows_router
from app.api.v1.endpoints.google_lead_webhooks import (
    config_router as google_lead_webhooks_config_router,
)
from app.api.v1.endpoints.google_lead_webhooks import router as google_lead_webhooks_router
from app.api.v1.endpoints.instagram import router as instagram_router
from app.api.v1.endpoints.integrations import router as integrations_router
from app.api.v1.endpoints.klm_operations import router as klm_operations_router
from app.api.v1.endpoints.knowledge import router as knowledge_router
from app.api.v1.endpoints.lead_webhooks import config_router as lead_webhooks_config_router
from app.api.v1.endpoints.lead_webhooks import router as lead_webhooks_router
from app.api.v1.endpoints.legal_batches import router as legal_batches_router
from app.api.v1.endpoints.legal_bi import router as legal_bi_router
from app.api.v1.endpoints.legal_case_strategy import router as legal_case_strategy_router
from app.api.v1.endpoints.legal_controladoria import router as legal_controladoria_router
from app.api.v1.endpoints.legal_demands import router as legal_demands_router
from app.api.v1.endpoints.legal_documents import router as legal_documents_router
from app.api.v1.endpoints.legal_editor import router as legal_editor_router
from app.api.v1.endpoints.local_billing import router as local_billing_router
from app.api.v1.endpoints.local_provisioning import router as local_provisioning_router
from app.api.v1.endpoints.local_storage import router as local_storage_router
from app.api.v1.endpoints.matters import router as matters_router
from app.api.v1.endpoints.medications import router as medications_router
from app.api.v1.endpoints.meetings import router as meetings_router
from app.api.v1.endpoints.messenger import router as messenger_router
from app.api.v1.endpoints.meta_groups import router as meta_groups_router
from app.api.v1.endpoints.migration import router as migration_router
from app.api.v1.endpoints.notifications import router as notifications_router
from app.api.v1.endpoints.omnichannel_public_webhooks import (
    router as omnichannel_public_webhooks_router,
)
from app.api.v1.endpoints.omnichannel_v6 import router as omnichannel_v6_router
from app.api.v1.endpoints.operational_copilot import (
    router as operational_copilot_router,
)
from app.api.v1.endpoints.oss_experiences import router as oss_experiences_router
from app.api.v1.endpoints.oss_function_units import router as oss_function_units_router
from app.api.v1.endpoints.oss_native import router as oss_native_router
from app.api.v1.endpoints.paid_acquisition import router as paid_acquisition_router
from app.api.v1.endpoints.platform_admin import router as platform_admin_router
from app.api.v1.endpoints.platform_ai_credentials import (
    router as platform_ai_credentials_router,
)
from app.api.v1.endpoints.platform_audit_events import router as platform_audit_events_router
from app.api.v1.endpoints.platform_blackbox_studios import (
    router as platform_blackbox_studios_router,
)
from app.api.v1.endpoints.platform_blog import router as platform_blog_router
from app.api.v1.endpoints.platform_commercial_catalog import (
    router as platform_commercial_catalog_router,
)
from app.api.v1.endpoints.platform_configuration_studio import (
    router as platform_configuration_studio_router,
)
from app.api.v1.endpoints.platform_dashboard import router as platform_dashboard_router
from app.api.v1.endpoints.platform_exports import router as platform_exports_router
from app.api.v1.endpoints.platform_governance import router as platform_governance_router
from app.api.v1.endpoints.platform_growth import router as platform_growth_router
from app.api.v1.endpoints.platform_inbox import (
    public_email_router,
    public_meta_router,
    public_webchat_router,
)
from app.api.v1.endpoints.platform_inbox import (
    router as platform_inbox_router,
)
from app.api.v1.endpoints.platform_official_whatsapp import (
    router as platform_official_whatsapp_router,
)
from app.api.v1.endpoints.platform_operations import router as platform_operations_router
from app.api.v1.endpoints.platform_organization import router as platform_organization_router
from app.api.v1.endpoints.platform_plan_policies import (
    router as platform_plan_policies_router,
)
from app.api.v1.endpoints.platform_restorations import router as platform_restorations_router
from app.api.v1.endpoints.platform_search import router as platform_search_router
from app.api.v1.endpoints.platform_site import router as platform_site_router
from app.api.v1.endpoints.platform_support import router as platform_support_router
from app.api.v1.endpoints.platform_support_access import router as platform_support_access_router
from app.api.v1.endpoints.platform_tasks import router as platform_tasks_router
from app.api.v1.endpoints.platform_work_items import router as platform_work_items_router
from app.api.v1.endpoints.playbooks import router as playbooks_router
from app.api.v1.endpoints.process_monitoring import router as process_monitoring_router
from app.api.v1.endpoints.protocol_folder import router as protocol_folder_router
from app.api.v1.endpoints.public_api import router as public_api_router
from app.api.v1.endpoints.public_api_tokens import router as public_api_tokens_router
from app.api.v1.endpoints.public_blog import router as public_blog_router
from app.api.v1.endpoints.public_site import router as public_site_router
from app.api.v1.endpoints.referrals import router as referrals_router
from app.api.v1.endpoints.reports import router as reports_router
from app.api.v1.endpoints.saas_benefits import (
    platform_router as platform_saas_benefits_router,
)
from app.api.v1.endpoints.saas_benefits import router as saas_benefits_router
from app.api.v1.endpoints.saas_billing_webhooks import router as saas_billing_webhooks_router
from app.api.v1.endpoints.sdr import router as sdr_router
from app.api.v1.endpoints.staff_availability import router as staff_availability_router
from app.api.v1.endpoints.staff_reassignment import router as staff_reassignment_router
from app.api.v1.endpoints.status import router as status_router
from app.api.v1.endpoints.stripe_billing import router as stripe_billing_router
from app.api.v1.endpoints.stripe_webhook import router as stripe_webhook_router
from app.api.v1.endpoints.super_admin_notices import router as super_admin_notices_router
from app.api.v1.endpoints.support_tickets import router as support_tickets_router
from app.api.v1.endpoints.task_external_shares import router as task_external_shares_router
from app.api.v1.endpoints.task_reminders import router as task_reminders_router
from app.api.v1.endpoints.tasks import router as tasks_router
from app.api.v1.endpoints.tech_providers import router as tech_providers_router
from app.api.v1.endpoints.users import router as users_router
from app.api.v1.endpoints.webhooks import router as webhooks_router
from app.api.v1.endpoints.whatsapp import router as whatsapp_router
from app.api.v1.endpoints.whatsapp_campaigns import router as whatsapp_campaigns_router
from app.api.v1.endpoints.whatsapp_collaboration import router as whatsapp_collaboration_router
from app.api.v1.endpoints.workspace_ai_credentials import (
    router as workspace_ai_credentials_router,
)
from app.api.v1.endpoints.workspace_ai_models import (
    router as workspace_ai_models_router,
)
from app.api.v1.endpoints.workspace_support_access import router as workspace_support_access_router
from app.api.v1.endpoints.workspaces import router as workspaces_router
from app.api.v1.entitlement_errors import enforce_workspace_feature_or_http
from app.core.deps import (
    get_current_workspace_id,
    get_db,
    require_subscription_access,
)
from app.core.platform_audit import audit_platform_mutation
from app.models.entitlement import EntitlementKey

api_router = APIRouter(prefix="/api/v1")


def _include_subscription_gated_router(
    router: APIRouter,
    *,
    ungated_paths: frozenset[str] = frozenset(),
) -> None:
    for route in router.routes:
        has_gate = isinstance(route, APIRoute) and any(
            dependency.dependency is require_subscription_access
            for dependency in route.dependencies
        )
        if isinstance(route, APIRoute) and route.path not in ungated_paths and not has_gate:
            route.dependencies.insert(0, Depends(require_subscription_access))
    api_router.include_router(router)


async def require_legal_suite_access(
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Commercial gate shared by every Jurídico 360 route."""

    await enforce_workspace_feature_or_http(db, workspace_id, EntitlementKey.legal_suite)


def _include_legal_suite_gated_router(router: APIRouter) -> None:
    for route in router.routes:
        has_gate = isinstance(route, APIRoute) and any(
            dependency.dependency is require_legal_suite_access for dependency in route.dependencies
        )
        if isinstance(route, APIRoute) and not has_gate:
            route.dependencies.insert(0, Depends(require_legal_suite_access))
    _include_subscription_gated_router(router)


api_router.include_router(auth_router)
api_router.include_router(commercial_self_service_public_router)
api_router.include_router(commercial_self_service_workspace_router)
api_router.include_router(workspace_support_access_router)
api_router.include_router(support_tickets_router)
_include_subscription_gated_router(
    workspaces_router,
    ungated_paths=frozenset({"/workspaces/setup"}),
)
_include_subscription_gated_router(workspace_ai_credentials_router)
_include_subscription_gated_router(workspace_ai_models_router)
_include_subscription_gated_router(admin_router)
_include_subscription_gated_router(users_router)
_include_subscription_gated_router(
    whatsapp_router,
    ungated_paths=frozenset({"/whatsapp/webhook"}),
)
_include_subscription_gated_router(whatsapp_campaigns_router)
_include_subscription_gated_router(whatsapp_collaboration_router)
_include_subscription_gated_router(tech_providers_router)
_include_subscription_gated_router(matters_router)
_include_legal_suite_gated_router(legal_demands_router)
_include_legal_suite_gated_router(client_contracts_router)
_include_legal_suite_gated_router(legal_documents_router)
_include_legal_suite_gated_router(legal_editor_router)
_include_legal_suite_gated_router(protocol_folder_router)
_include_legal_suite_gated_router(legal_controladoria_router)
_include_legal_suite_gated_router(legal_case_strategy_router)
_include_legal_suite_gated_router(legal_batches_router)
_include_legal_suite_gated_router(legal_bi_router)
_include_subscription_gated_router(tasks_router)
_include_subscription_gated_router(
    task_external_shares_router,
    ungated_paths=frozenset({"/public/task-shares/redeem"}),
)
_include_subscription_gated_router(task_reminders_router)
_include_subscription_gated_router(documents_router)
_include_subscription_gated_router(approvals_router)
_include_subscription_gated_router(exception_center_router)
_include_subscription_gated_router(reports_router)
_include_subscription_gated_router(executives_router)
api_router.include_router(webhooks_router)
_include_subscription_gated_router(notifications_router)
_include_subscription_gated_router(knowledge_router)
_include_subscription_gated_router(sdr_router)
_include_subscription_gated_router(agent_runtime_router)
_include_subscription_gated_router(finance_router)
_include_subscription_gated_router(finance_charges_router)
_include_subscription_gated_router(finance_expenses_router)
_include_subscription_gated_router(finance_recurrences_router)
_include_subscription_gated_router(finance_dunning_router)
_include_subscription_gated_router(finance_reports_router)
_include_subscription_gated_router(finance_executive_router)
_include_subscription_gated_router(finance_dimensional_router)
_include_subscription_gated_router(finance_exports_router)
_include_subscription_gated_router(fee_arrangements_router)
_include_legal_suite_gated_router(process_monitoring_router)
_include_subscription_gated_router(contract_billing_router)
_include_subscription_gated_router(bank_reconciliation_router)
_include_subscription_gated_router(finance_harmonization_router)
_include_subscription_gated_router(migration_router)
_include_subscription_gated_router(referrals_router)
_include_subscription_gated_router(saas_benefits_router)
_include_subscription_gated_router(
    integrations_router,
    ungated_paths=frozenset({"/integrations/webhooks/{workspace_slug}/{provider}"}),
)
_include_subscription_gated_router(public_api_tokens_router)
_platform_audit_dependencies = [Depends(audit_platform_mutation)]
api_router.include_router(platform_admin_router, dependencies=_platform_audit_dependencies)
api_router.include_router(
    platform_ai_credentials_router, dependencies=_platform_audit_dependencies
)
api_router.include_router(platform_audit_events_router)
api_router.include_router(platform_exports_router, dependencies=_platform_audit_dependencies)
api_router.include_router(platform_restorations_router, dependencies=_platform_audit_dependencies)
api_router.include_router(platform_dashboard_router)
api_router.include_router(platform_growth_router, dependencies=_platform_audit_dependencies)
api_router.include_router(platform_governance_router, dependencies=_platform_audit_dependencies)
api_router.include_router(platform_inbox_router, dependencies=_platform_audit_dependencies)
api_router.include_router(public_webchat_router)
api_router.include_router(public_meta_router)
api_router.include_router(public_email_router)
api_router.include_router(platform_support_access_router, dependencies=_platform_audit_dependencies)
api_router.include_router(platform_support_router, dependencies=_platform_audit_dependencies)
api_router.include_router(platform_site_router, dependencies=_platform_audit_dependencies)
api_router.include_router(platform_blog_router, dependencies=_platform_audit_dependencies)
api_router.include_router(
    platform_blackbox_studios_router, dependencies=_platform_audit_dependencies
)
api_router.include_router(
    platform_configuration_studio_router, dependencies=_platform_audit_dependencies
)
api_router.include_router(
    platform_commercial_catalog_router, dependencies=_platform_audit_dependencies
)
api_router.include_router(
    commercial_self_service_platform_router, dependencies=_platform_audit_dependencies
)
api_router.include_router(
    platform_official_whatsapp_router, dependencies=_platform_audit_dependencies
)
api_router.include_router(platform_operations_router, dependencies=_platform_audit_dependencies)
api_router.include_router(platform_organization_router, dependencies=_platform_audit_dependencies)
api_router.include_router(platform_plan_policies_router, dependencies=_platform_audit_dependencies)
api_router.include_router(platform_search_router)
api_router.include_router(platform_tasks_router, dependencies=_platform_audit_dependencies)
api_router.include_router(platform_work_items_router, dependencies=_platform_audit_dependencies)
api_router.include_router(platform_saas_benefits_router, dependencies=_platform_audit_dependencies)
api_router.include_router(super_admin_notices_router, dependencies=_platform_audit_dependencies)
_include_subscription_gated_router(admin_automation_rules_router)
_include_subscription_gated_router(instagram_router)
_include_subscription_gated_router(messenger_router)
_include_subscription_gated_router(
    klm_operations_router,
    ungated_paths=frozenset(
        {
            "/klm/runtime",
            "/klm/tools",
            "/klm/local/session",
            "/klm/local/platform-session",
            "/klm/local/platform-session/{role_package}",
        }
    ),
)
_include_subscription_gated_router(omnichannel_v6_router)
_include_subscription_gated_router(conversation_response_metrics_router)
_include_subscription_gated_router(meta_groups_router)
_include_subscription_gated_router(conversation_collaboration_router)
_include_subscription_gated_router(staff_availability_router)
api_router.include_router(staff_reassignment_router)
api_router.include_router(omnichannel_public_webhooks_router)
_include_subscription_gated_router(
    local_storage_router,
    ungated_paths=frozenset(
        {
            "/local-storage/upload",
            "/local-storage/download",
            "/local-storage/object-upload",
            "/local-storage/object-download",
        }
    ),
)
api_router.include_router(local_billing_router)
# Não gateado por assinatura: um workspace inadimplente/sem assinatura ativa
# precisa continuar acessando o próprio checkout/portal Stripe para regularizar.
api_router.include_router(stripe_billing_router)
_include_subscription_gated_router(local_provisioning_router)
_include_subscription_gated_router(paid_acquisition_router)
# Source-adapter endpoints remain preserved in their module for provenance,
# migration and rollback analysis. Tenant traffic is served only by the native
# KLM capability, communication, collaboration, automation and CRM routes.
_include_subscription_gated_router(
    agenda_router,
    ungated_paths=frozenset({"/agenda/public/{token}"}),
)
_include_subscription_gated_router(medications_router)
_include_subscription_gated_router(oss_function_units_router)
_include_subscription_gated_router(oss_native_router)
_include_subscription_gated_router(oss_experiences_router)
api_router.include_router(stripe_webhook_router)
api_router.include_router(asaas_webhook_router)
api_router.include_router(saas_billing_webhooks_router)
api_router.include_router(lead_webhooks_router)
_include_subscription_gated_router(lead_webhooks_config_router)
api_router.include_router(google_lead_webhooks_router)
_include_subscription_gated_router(google_lead_webhooks_config_router)
_include_subscription_gated_router(flows_router)
_include_subscription_gated_router(agent_platform_router)
_include_subscription_gated_router(operational_copilot_router)
_include_subscription_gated_router(clients_router)
_include_subscription_gated_router(configurations_router)
_include_subscription_gated_router(playbooks_router)
api_router.include_router(status_router)
api_router.include_router(error_logs_router, dependencies=_platform_audit_dependencies)
api_router.include_router(public_site_router)
api_router.include_router(public_blog_router)
_include_subscription_gated_router(meetings_router)
api_router.include_router(public_api_router)
