"""Casos de uso que sustentam a SPA do KLM Hub sem efeitos externos."""

from __future__ import annotations

import ast
import asyncio
import hashlib
import hmac
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from statistics import median
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import String, and_, case, cast, func, or_, select, text
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.klm_runtime import (
    aws_external_access_allowed,
    external_delivery_allowed,
    external_model_api_execution_allowed,
    get_runtime_status,
)
from app.data.legal_area_catalog import (
    LEGAL_AREA_CATALOG,
    LEGAL_AREA_CATALOG_VERSION,
    canonical_area_key,
    catalog_blueprint,
    infer_area,
    infer_product,
    route_for,
)
from app.db.advisory import acquire_transaction_advisory_lock
from app.models.ads_manager import AdsManagerEntity, AdsManagerImportReceipt
from app.models.approval import ApprovalPriority, ApprovalQueueItem, ApprovalStatus
from app.models.client import Client
from app.models.conversation_collaboration import (
    ConversationMessageReference,
    ConversationReferenceType,
)
from app.models.conversion_feedback import AdsConversionFeedbackRecord
from app.models.document import Document, DocumentStatus, DocumentType
from app.models.entitlement import EntitlementKey
from app.models.finance import (
    FinanceEntry,
    FinanceEntryStatus,
    FinanceEntryType,
    IntegrationConnection,
    IntegrationProvider,
    IntegrationStatus,
)
from app.models.flow import FlowRun
from app.models.honorarium_estimate import HonorariumEstimate
from app.models.klm_operations import (
    LEGAL_ARTIFACT_EVIDENCE_SCHEMA_VERSION,
    AcquisitionEvent,
    AreaBlueprint,
    AreaBlueprintStatus,
    EmailIngestReceipt,
    ExternalActionIntent,
    ExternalActionIntentStatus,
    FollowUpStatus,
    FollowUpTask,
    KlmAuditEvent,
    LegalArtifactState,
    LegalArtifactVersion,
    MessageAuthorType,
    MessageDeliveryStatus,
    MessageDirection,
    OmnichannelChannel,
    OmnichannelMessage,
    OmnichannelThread,
    ProcessMonitoringRecord,
    TechnicalRequest,
    ThreadStatus,
)
from app.models.knowledge_chunk import KnowledgeChunk
from app.models.matter import (
    HealthSubtheme,
    KanbanColumnId,
    Matter,
    MicroStatus,
    PracticeArea,
)
from app.models.meeting import Meeting, MeetingStatus
from app.models.omnichannel_v6 import (
    OmnichannelAuditFinding,
    OmnichannelChannelConnection,
    OmnichannelEndpoint,
    OmnichannelIdentity,
    OmnichannelThreadBinding,
    WorkspaceTeam,
)
from app.models.revenue_projection import RevenueProjection
from app.models.task import Task, TaskStatus
from app.models.user import User
from app.models.workspace import Workspace
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership
from app.schemas.approval import ApprovalCreate
from app.schemas.klm_operations import (
    AcquisitionEventCreate,
    AreaBlueprintCreate,
    AreaBlueprintDefinition,
    AreaBlueprintUpdate,
    AudioRequestCreate,
    EmailIngestRequest,
    ExternalActionIntentCreate,
    FollowUpCreate,
    FollowUpUpdate,
    LegalArtifactVersionCreate,
    MatterFinancialProjectionUpdate,
    OmnichannelMessageCreate,
    OmnichannelThreadCreate,
    OmnichannelThreadUpdate,
    ProcessMonitoringCreate,
    ProcessMonitoringEventCreate,
    ProcessMonitoringUpdate,
    TechnicalRequestCreate,
    TechnicalRequestUpdate,
    normalize_message_collaboration,
)
from app.services.acquisition_attribution_service import (
    AttributionEvidence,
    extract_attribution,
    feedback_click_ids,
    feedback_eligibility,
)
from app.services.ads_conversion_feedback_service import (
    build_provider_feedback_envelope,
    has_usable_first_party_identity,
    provider_feedback_config,
)
from app.services.approval_service import ApprovalService
from app.services.audit_actor_service import resolve_human_actor_label
from app.services.entitlement_service import EntitlementEnforcementService
from app.services.integration_secrets import (
    protect_integration_settings,
    reveal_integration_secret,
)
from app.services.knowledge_service import KnowledgeService
from app.services.medication_pricing_service import MedicationPricingService
from app.services.platform_blackbox_studios_service import (
    PlatformBlackboxStudiosService,
)

# `app.workers.ads_feedback_tasks` transitively imports `app.workers.celery_app`,
# which imports several services that in turn import `KlmOperationsService`
# from this module. Import lazily inside the methods below to avoid a
# circular import at module load time.
ADS_MEASUREMENT_LABEL = "meta_capi"
from app.services.message_evidence_service import MessageEvidenceService
from app.services.messaging_cadence_service import MessagingCadenceService
from app.services.oss_runtime_service import oss_runtime_status

logger = logging.getLogger(__name__)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


DEFAULT_HEALTH_BLUEPRINT: Dict[str, Any] = catalog_blueprint("saude_suplementar") or {}


AREA_MATERIALIZED_VERSION = "v0.9.0-local"
AREA_ACTIVE_VERSION = "v1.1.0-role-separated"
AREA_MATERIALIZED_READINESS = 80
AREA_VALIDATED_READINESS = 90
AREA_ACTIVE_READINESS = 100


def _generic_area_blueprint() -> Dict[str, Any]:
    blueprint = {
        "taxonomy": {
            "subthemes": ["consultivo", "contencioso", "urgente", "outro"],
            "requiredFields": [
                "demandType",
                "objectName",
                "urgency",
                "economicValue",
            ],
        },
        "documents": [
            "documento_identificacao",
            "relato_demanda",
            "instrumento_relacao_juridica",
            "evidencia_principal",
            "documentos_complementares",
        ],
        "funnel": [
            "novo_lead",
            "triagem_sdr",
            "documentos",
            "reuniao",
            "proposta",
            "contrato",
            "pagamento",
            "ganho",
            "perdido",
        ],
        "agentTeam": [
            "orquestrador",
            "hermes",
            "sdr",
            "juridico",
            "crm",
            "closer",
            "secretaria",
            "paralegal",
            "peticao",
            "andamento",
        ],
        "routing": {
            "coordinationAgentKey": "orquestrador",
            "ownerBridgeAgentKey": "hermes",
            "ownerBridgeDelegatorKey": "secretaria",
            "sdrAgentKey": "sdr",
            "specialistAgentKey": "juridico",
            "closerAgentKey": "closer",
            "fallbackAgentKey": "juridico",
            "manualOverrideWins": True,
            "productKeywordRouting": True,
        },
        "gates": {
            "humanRequestAgent": "orquestrador",
            "humanHandoffCoordinator": "orquestrador",
            "petitionAlwaysRequiresApproval": True,
            "externalActionsDisabled": True,
            "audioRequiresRepeatedRequest": True,
        },
    }
    return AreaBlueprintDefinition.model_validate(blueprint).model_dump(mode="json", by_alias=True)


def _merge_catalog_blueprint(existing: Any, canonical: Any, *, root: bool = True) -> Any:
    """Add catalog upgrades without discarding tenant-authored extensions."""
    if isinstance(canonical, dict):
        current = existing if isinstance(existing, dict) else {}
        merged = {
            key: _merge_catalog_blueprint(current.get(key), value, root=False)
            for key, value in canonical.items()
        }
        for key, value in current.items():
            if key not in merged:
                merged[key] = value
        if root:
            merged["catalogVersion"] = LEGAL_AREA_CATALOG_VERSION
            merged["catalogManaged"] = True
        return merged
    if isinstance(canonical, list):
        current = existing if isinstance(existing, list) else []
        if canonical and all(isinstance(item, dict) and item.get("key") for item in canonical):
            current_by_key = {
                str(item.get("key")): item
                for item in current
                if isinstance(item, dict) and item.get("key")
            }
            canonical_keys = {str(item["key"]) for item in canonical}
            return [
                _merge_catalog_blueprint(
                    current_by_key.get(str(item["key"])), item, root=False
                )
                for item in canonical
            ] + [
                item
                for item in current
                if not isinstance(item, dict) or str(item.get("key")) not in canonical_keys
            ]
        if all(not isinstance(item, (dict, list)) for item in [*canonical, *current]):
            return list(dict.fromkeys([*canonical, *current]))
        return canonical if not current else current
    return canonical if existing is None else existing


def _validate_area_blueprint(blueprint: Dict[str, Any]) -> AreaBlueprintDefinition:
    try:
        return AreaBlueprintDefinition.model_validate(blueprint or {})
    except ValidationError as error:
        paths = sorted(
            {
                ".".join(str(part) for part in issue["loc"])
                for issue in error.errors(include_url=False)
            }
        )
        invalid_sections = ", ".join(paths) or "blueprint"
        raise ValueError(
            f"Blueprint incompleto para validação; revise: {invalid_sections}."
        ) from error


DEFAULT_AREAS = [
    (
        area["areaKey"],
        area["name"],
        AreaBlueprintStatus.active if area["defaultActive"] else AreaBlueprintStatus.blueprint,
        100 if area["defaultActive"] else AREA_VALIDATED_READINESS,
    )
    for area in LEGAL_AREA_CATALOG
]


TOOL_CATALOG = [
    (
        "whatsapp_official",
        "WhatsApp oficial e múltiplos números",
        "channels",
        "contract_local",
        True,
        True,
        ["inbox", "templates", "sla", "multi_number"],
    ),
    (
        "email_inbound",
        "E-mail inbound e RAG",
        "channels",
        "local_ready",
        False,
        False,
        ["thread_match", "attachments", "rag"],
    ),
    (
        "commercial_kanban",
        "Kanban comercial automático",
        "crm",
        "local_ready",
        False,
        True,
        ["stage", "owner", "score", "next_action"],
    ),
    (
        "saved_replies",
        "Respostas salvas e playbooks",
        "service",
        "local_ready",
        False,
        True,
        ["templates", "tone", "subtheme"],
    ),
    (
        "calendar",
        "Agenda e lembretes",
        "operations",
        "contract_local",
        True,
        True,
        ["meeting", "reminder", "no_show"],
    ),
    (
        "signature",
        "Contratos e procurações",
        "legal",
        "contract_local",
        True,
        True,
        ["zapsign", "paralegal", "signature_status"],
    ),
    (
        "process_monitor",
        "Monitoramento processual",
        "legal",
        "local_ready",
        True,
        True,
        ["events", "deadlines", "client_update"],
    ),
    (
        "campaigns",
        "Disparos e listas",
        "marketing",
        "local_ready",
        True,
        True,
        ["segments", "suppression", "ab_test", "roi"],
    ),
    (
        "audio",
        "Áudio e transcrição",
        "channels",
        "contract_local",
        True,
        True,
        ["repeated_request_gate", "transcript", "summary"],
    ),
    (
        "integrations",
        "Central de integrações",
        "system",
        "contract_local",
        True,
        True,
        ["api", "mcp", "webhook"],
    ),
    (
        "client_360",
        "Cliente 360",
        "crm",
        "local_ready",
        False,
        False,
        ["timeline", "documents", "finance", "meetings"],
    ),
    (
        "ai_quality",
        "Qualidade e treino IA",
        "governance",
        "local_ready",
        False,
        True,
        ["eval", "review", "version", "replay"],
    ),
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _resolved_matter_area_key(matter: Matter) -> str:
    """Resolve cadastros legados sem ignorar a taxonomia estruturada do caso."""
    practice_area = getattr(matter.practice_area, "value", matter.practice_area)
    configured_area = canonical_area_key(matter.legal_area_key or practice_area)
    has_health_taxonomy = any(
        (
            matter.health_subtheme,
            matter.health_subtheme_custom,
            matter.health_object_name,
            matter.health_insurer,
            matter.health_denial_reason,
            matter.medical_urgency_hours,
        )
    )
    if has_health_taxonomy and configured_area in {None, "civel", "triagem_geral"}:
        return PracticeArea.saude_suplementar.value
    return configured_area or PracticeArea.civel.value


def _matter_legal_routing(matter: Matter) -> dict[str, Any]:
    area_key = _resolved_matter_area_key(matter)
    product_key = matter.legal_product_key
    if not product_key:
        inference_text = " | ".join(
            str(value).strip()
            for value in (
                matter.demand_type,
                matter.legal_product_custom,
                matter.legal_object_name,
                matter.legal_issue_summary,
                matter.executive_summary,
                matter.health_subtheme_custom,
                matter.health_object_name,
                matter.health_denial_reason,
            )
            if value
        )
        inferred = infer_product(area_key, inference_text)
        product_key = inferred["key"] if inferred else None
    route = route_for(area_key, product_key)
    if product_key and not route.get("productKey"):
        route["productKey"] = product_key
        route["productLabel"] = (
            matter.legal_product_custom or product_key.replace("_", " ").title()
        )
    return route


def _current_alembic_heads() -> List[str]:
    revisions: set[str] = set()
    parents: set[str] = set()
    for path in sorted((_PROJECT_ROOT / "alembic" / "versions").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        values: dict[str, Any] = {}
        for node in tree.body:
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                targets = [node.target.id]
                value_node = node.value
            elif isinstance(node, ast.Assign):
                targets = [target.id for target in node.targets if isinstance(target, ast.Name)]
                value_node = node.value
            else:
                continue
            for target in targets:
                if target in {"revision", "down_revision"} and value_node is not None:
                    values[target] = ast.literal_eval(value_node)

        revision = values.get("revision")
        if not isinstance(revision, str) or not revision:
            raise ValueError(f"Migração sem revision válida: {path.name}")
        revisions.add(revision)
        down_revision = values.get("down_revision")
        if isinstance(down_revision, str):
            parents.add(down_revision)
        elif isinstance(down_revision, (list, tuple)):
            parents.update(str(parent) for parent in down_revision if parent)
    return sorted(revisions - parents)


def _normalized_moment(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


TRACKING_COVERAGE_EVENTS = (
    "page_view",
    "view_content",
    "contact",
    "lead",
    "schedule",
    "client",
)
TRACKING_REVENUE_EVENTS = frozenset({"payment_confirmed", "purchase"})
TRACKING_CONVERSION_EVENTS = frozenset(
    {"client", "contract_created", "payment_confirmed", "purchase"}
)
TRACKING_COMMERCIAL_EVENT_ID_FIELDS = (
    "commercialEventId",
    "commercial_event_id",
    "transactionId",
    "transaction_id",
    "paymentId",
    "payment_id",
    "orderId",
    "order_id",
    "chargeId",
    "charge_id",
    "invoiceId",
    "invoice_id",
)
TRACKING_REVENUE_EVENT_PRIORITY = {
    "purchase": 1,
    "payment_confirmed": 2,
}
TRACKING_STAGE_RANK = {
    "page_view": 0,
    "view_content": 1,
    "contact": 2,
    "lead": 3,
    "qualified_lead": 4,
    "schedule": 5,
}
TRACKING_EVENT_ALIASES = {
    "pageview": "page_view",
    "viewcontent": "view_content",
    "contact_created": "contact",
    "lead_captured": "lead",
    "lead_created": "lead",
    "qualifiedlead": "qualified_lead",
    "lead_qualified": "qualified_lead",
    "meeting": "schedule",
    "meeting_booked": "schedule",
    "meeting_scheduled": "schedule",
    "schedule_created": "schedule",
    "client_created": "client",
    "sale_detected": "purchase",
    "sale_confirmed": "purchase",
}
TRACKING_IDENTITY_METADATA_FIELDS = (
    ("lead_id", "lead"),
    ("leadId", "lead"),
    ("contact_id", "contact"),
    ("contactId", "contact"),
    ("session_id", "session"),
    ("sessionId", "session"),
    ("anonymous_id", "anonymous"),
    ("anonymousId", "anonymous"),
)


def _normalize_tracking_event_name(value: str) -> str:
    snake_case = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value.strip())
    snake_case = re.sub(r"[^a-zA-Z0-9]+", "_", snake_case).strip("_").lower()
    return TRACKING_EVENT_ALIASES.get(snake_case, snake_case)


def _commercial_event_identity(
    event: AcquisitionEvent,
    normalized_event: str,
    *,
    canonical_client_id: UUID | None = None,
) -> tuple[str, str, str, str] | None:
    """Return an explicit business identity only when the source supplied one."""

    if normalized_event not in TRACKING_REVENUE_EVENTS:
        return None
    metadata = event.event_metadata or {}
    external_id = next(
        (
            str(metadata.get(field) or "").strip()
            for field in TRACKING_COMMERCIAL_EVENT_ID_FIELDS
            if str(metadata.get(field) or "").strip()
        ),
        "",
    )
    if not external_id or len(external_id) > 240:
        return None
    if canonical_client_id:
        scope_type, scope_id = "client", str(canonical_client_id)
    elif event.client_id:
        scope_type, scope_id = "client", str(event.client_id)
    elif event.matter_id:
        scope_type, scope_id = "matter", str(event.matter_id)
    else:
        return None
    return "realized_revenue", scope_type, scope_id, external_id


def _commercial_event_identity_hash(
    workspace_id: UUID,
    identity: tuple[str, str, str, str],
) -> str:
    raw = ":".join((str(workspace_id), *identity))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _canonicalize_commercial_events(
    events: List[AcquisitionEvent],
    *,
    canonical_clients: Dict[UUID, Client] | None = None,
) -> tuple[List[AcquisitionEvent], set[UUID]]:
    """Collapse duplicate revenue signals while retaining their raw source records."""

    selected: Dict[tuple[str, str, str, str], AcquisitionEvent] = {}
    suppressed: set[UUID] = set()
    canonical_clients = canonical_clients or {}
    for event in events:
        normalized_event = _normalize_tracking_event_name(event.event_name)
        client = canonical_clients.get(event.id)
        identity = _commercial_event_identity(
            event,
            normalized_event,
            canonical_client_id=client.id if client else None,
        )
        if identity is None:
            continue
        current = selected.get(identity)
        if current is None:
            selected[identity] = event
            continue
        current_name = _normalize_tracking_event_name(current.event_name)
        if TRACKING_REVENUE_EVENT_PRIORITY.get(normalized_event, 0) > (
            TRACKING_REVENUE_EVENT_PRIORITY.get(current_name, 0)
        ):
            suppressed.add(current.id)
            selected[identity] = event
        else:
            suppressed.add(event.id)
    return [event for event in events if event.id not in suppressed], suppressed


def _conversion_consent_context(
    event: AcquisitionEvent, touch: AcquisitionEvent
) -> tuple[str | None, str | None]:
    state: str | None = None
    evidence_ref: str | None = None
    for metadata in (event.event_metadata or {}, touch.event_metadata or {}):
        raw_state = (
            metadata.get("adDataConsent")
            or metadata.get("ad_data_consent")
            or metadata.get("adsConsent")
            or metadata.get("ads_consent")
        )
        raw_evidence = (
            metadata.get("adDataConsentEvidenceRef")
            or metadata.get("ad_data_consent_evidence_ref")
            or metadata.get("consentEvidenceRef")
            or metadata.get("consent_evidence_ref")
        )
        if state is None and raw_state is not None:
            state = str(raw_state).strip().upper()
        if evidence_ref is None and raw_evidence is not None:
            evidence_ref = str(raw_evidence).strip() or None
    return state, evidence_ref


def _new_tracking_metric_bucket() -> Dict[str, Any]:
    return {
        "events": 0,
        "leads": 0,
        "qualified_leads": 0,
        "meetings": 0,
        "clients": 0,
        "ad_spend": Decimal("0"),
        "attributable_revenue": Decimal("0"),
        "ignored_value_events": 0,
    }


def _accumulate_tracking_event(bucket: Dict[str, Any], normalized_event: str, value: Any) -> None:
    bucket["events"] += 1
    if normalized_event == "lead":
        bucket["leads"] += 1
    elif normalized_event == "qualified_lead":
        bucket["qualified_leads"] += 1
    elif normalized_event == "schedule":
        bucket["meetings"] += 1
    # "clients" alimenta o CAC e precisa contar as mesmas conversões que
    # "vendas atribuídas" (TRACKING_CONVERSION_EVENTS), senão o CAC exibe
    # "0 clientes" ao lado de vendas atribuídas > 0 para o mesmo período.
    elif normalized_event in TRACKING_CONVERSION_EVENTS:
        bucket["clients"] += 1

    monetary_value = Decimal(str(value)) if value is not None else Decimal("0")
    if monetary_value <= 0:
        return
    if normalized_event == "ad_spend":
        bucket["ad_spend"] += monetary_value
    elif normalized_event in TRACKING_REVENUE_EVENTS:
        bucket["attributable_revenue"] += monetary_value
    else:
        bucket["ignored_value_events"] += 1


def _finalize_tracking_metrics(bucket: Dict[str, Any]) -> Dict[str, Any]:
    leads = bucket["leads"]
    clients = bucket["clients"]
    ad_spend = bucket["ad_spend"]
    attributable_revenue = bucket["attributable_revenue"]
    return {
        "events": bucket["events"],
        "leads": leads,
        "qualified_leads": bucket["qualified_leads"],
        "meetings": bucket["meetings"],
        "clients": clients,
        "ad_spend": round(float(ad_spend), 2),
        "attributable_revenue": round(float(attributable_revenue), 2),
        "ignored_value_events": bucket["ignored_value_events"],
        "conversion_rate": round(clients / leads * 100, 2) if leads else None,
        "cac": round(float(ad_spend / clients), 2) if clients else None,
        "roas": round(float(attributable_revenue / ad_spend), 2) if ad_spend else None,
    }


def _tracking_identity(event: AcquisitionEvent) -> Optional[Tuple[str, str]]:
    if event.client_id:
        return "client", str(event.client_id)
    if event.matter_id:
        return "matter", str(event.matter_id)

    metadata = event.event_metadata or {}
    for field, identity_type in TRACKING_IDENTITY_METADATA_FIELDS:
        value = str(metadata.get(field) or "").strip()
        if value:
            return identity_type, value

    for field, raw_value in sorted((event.click_ids or {}).items()):
        value = str(raw_value or "").strip()
        if value:
            return f"click:{field}", value
    return None


def _public_journey_id(workspace_id: UUID, identity: Tuple[str, str]) -> str:
    identity_type, value = identity
    raw_identity = f"{workspace_id}:{identity_type}:{value}"
    return hashlib.sha256(raw_identity.encode("utf-8")).hexdigest()[:16]


def _new_attribution_bucket(
    evidence: AttributionEvidence,
    happened_at: datetime,
) -> Dict[str, Any]:
    return {
        "evidence": evidence,
        "metrics": _new_tracking_metric_bucket(),
        "first_touch_at": happened_at,
        "last_touch_at": happened_at,
        "touch_count": 0,
        "journey_ids": set(),
        "sample_journey_id": None,
        "sample_thread_id": None,
        "sample_matter_id": None,
    }


def _accumulate_attribution_bucket(
    bucket: Dict[str, Any],
    *,
    evidence: AttributionEvidence,
    event_name: str,
    value: Any,
    happened_at: datetime,
    journey_id: str | None,
    is_touch: bool,
) -> None:
    _accumulate_tracking_event(bucket["metrics"], event_name, value)
    bucket["evidence"] = evidence
    bucket["first_touch_at"] = min(bucket["first_touch_at"], happened_at)
    bucket["last_touch_at"] = max(bucket["last_touch_at"], happened_at)
    if is_touch:
        bucket["touch_count"] += 1
    if journey_id:
        bucket["journey_ids"].add(journey_id)


def _finalize_attribution_bucket(bucket: Dict[str, Any]) -> Dict[str, Any]:
    evidence: AttributionEvidence = bucket["evidence"]
    return {
        **evidence.public_dict(),
        **_finalize_tracking_metrics(bucket["metrics"]),
        "attribution_model": "last_paid_touch",
        "first_touch_at": bucket["first_touch_at"],
        "last_touch_at": bucket["last_touch_at"],
        "touch_count": bucket["touch_count"],
        "journey_count": len(bucket["journey_ids"]),
        "sample_journey_id": bucket["sample_journey_id"],
        "sample_thread_id": bucket["sample_thread_id"],
        "sample_matter_id": bucket["sample_matter_id"],
    }


def _funnel_steps(
    journey_rows: Dict[Tuple[str, str], List[Tuple[AcquisitionEvent, str, str, datetime]]],
) -> List[Dict[str, Any]]:
    stages = ("contact", "lead", "qualified_lead", "schedule", "client")
    reached: Dict[str, set[Tuple[str, str]]] = {stage: set() for stage in stages}
    for identity, rows in journey_rows.items():
        normalized = {row[1] for row in rows}
        if normalized & TRACKING_CONVERSION_EVENTS:
            normalized.add("client")
        for stage in stages:
            if stage in normalized:
                reached[stage].add(identity)

    first_nonzero = next((len(reached[stage]) for stage in stages if reached[stage]), 0)
    output: List[Dict[str, Any]] = []
    previous_count: int | None = None
    for stage in stages:
        count = len(reached[stage])
        drop_off = max(0, (previous_count or 0) - count) if previous_count is not None else 0
        output.append(
            {
                "stage": stage,
                "journeys": count,
                "share_of_start": round(count / first_nonzero * 100, 2)
                if first_nonzero
                else None,
                "conversion_from_previous": round(count / previous_count * 100, 2)
                if previous_count
                else None,
                "drop_off_from_previous": drop_off,
                "drop_off_rate": round(drop_off / previous_count * 100, 2)
                if previous_count
                else None,
            }
        )
        if count or previous_count is not None:
            previous_count = count
    return output


def _required_rich_message_capability(
    payload: OmnichannelMessageCreate,
) -> str | None:
    if payload.direction != MessageDirection.outbound:
        return None
    if payload.content_type == "template":
        return "templates"
    if payload.content_type in {"attachment", "media"}:
        return "media" if payload.channel == OmnichannelChannel.whatsapp else "outbound_messages"
    if payload.content_type in {
        "interactive_buttons",
        "reply_buttons",
        "interactive_list",
        "list",
    }:
        return "outbound_messages"
    if payload.content_type in {"whatsapp_flow", "flow"}:
        return "flows"
    return None


def _require_rich_message_capability(
    connection: OmnichannelChannelConnection,
    payload: OmnichannelMessageCreate,
) -> None:
    required = _required_rich_message_capability(payload)
    if required is None:
        return
    capability = (connection.capabilities or {}).get(required)
    if capability is None and required == "media" and payload.channel != OmnichannelChannel.whatsapp:
        capability = (connection.capabilities or {}).get("outbound_messages")
    if isinstance(capability, bool):
        supported = enabled = capability
    elif isinstance(capability, dict):
        supported = capability.get("supported") is not False
        enabled = capability.get("enabled") is not False
    else:
        supported = enabled = False
    if not supported:
        raise ValueError(
            f"A rota selecionada não declara suporte para {required.replace('_', ' ')}."
        )
    if not enabled:
        raise ValueError(
            f"A capacidade {required.replace('_', ' ')} está desativada nesta rota."
        )


def _message_replay_matches(
    existing: OmnichannelMessage,
    *,
    thread_id: UUID,
    author_user_id: UUID | None,
    payload: OmnichannelMessageCreate,
) -> bool:
    existing_metadata = dict(existing.message_metadata or {})
    payload_cost_amount = getattr(payload, "cost_amount", None)
    payload_cost_currency = getattr(payload, "cost_currency", None)
    immediate_dispatch = existing_metadata.get("immediateDispatch")
    has_immediate_projection = isinstance(immediate_dispatch, dict) and bool(
        immediate_dispatch.get("state")
    )
    provider_status_matches = (
        has_immediate_projection and payload.provider_status is None
    ) or existing.provider_status == payload.provider_status
    external_message_id_matches = (
        has_immediate_projection and payload.external_message_id is None
    ) or (existing.external_message_id or None) == (payload.external_message_id or None)
    for server_key in (
        "immediateDispatch",
        "lastDeliveryReceiptId",
        "lastDeliveryReceiptAt",
        "lastProviderStatus",
    ):
        existing_metadata.pop(server_key, None)
    if (
        existing.thread_id != thread_id
        or existing.author_user_id != author_user_id
        or existing.channel != payload.channel
        or existing.direction != payload.direction
        or existing.author_type != payload.author_type
        or existing.author_label != payload.author_label
        or existing.body != payload.body
        or existing.endpoint_id != payload.endpoint_id
        or existing.binding_id != payload.binding_id
        or existing.audience != payload.audience
        or existing.content_type != payload.content_type
        or existing.language != payload.language
        or not provider_status_matches
        or existing.transport != payload.transport
        or (Decimal(str(existing.cost_amount)) if existing.cost_amount is not None else None)
        != (Decimal(str(payload_cost_amount)) if payload_cost_amount is not None else None)
        or existing.cost_currency != payload_cost_currency
        or existing.window_expires_at != payload.window_expires_at
        or existing.template_id != payload.template_id
        or not external_message_id_matches
        or existing_metadata != (payload.message_metadata or {})
    ):
        return False
    return payload.occurred_at is None or _normalized_moment(
        existing.occurred_at
    ) == _normalized_moment(payload.occurred_at)


def _require_matching_message_replay(
    existing: OmnichannelMessage,
    *,
    thread_id: UUID,
    author_user_id: UUID | None,
    payload: OmnichannelMessageCreate,
) -> None:
    if not _message_replay_matches(
        existing,
        thread_id=thread_id,
        author_user_id=author_user_id,
        payload=payload,
    ):
        raise ValueError("Chave de idempotência já utilizada por outra mensagem ou conversa.")


def _counter_value(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _money_value(value: Any) -> Decimal:
    try:
        return max(Decimal("0"), Decimal(str(value or "0")))
    except (ArithmeticError, TypeError, ValueError):
        return Decimal("0")


def _increment_counter(counter: Any, key: str) -> dict[str, int]:
    values = dict(counter) if isinstance(counter, dict) else {}
    values[key] = _counter_value(values.get(key)) + 1
    return values


def _updated_thread_cost_state(
    current: Any,
    *,
    payload: OmnichannelMessageCreate,
    delivery: MessageDeliveryStatus,
    connection_key: str | None,
) -> dict[str, Any]:
    state = dict(current) if isinstance(current, dict) else {}
    payload_cost_amount = getattr(payload, "cost_amount", None)
    payload_cost_currency = getattr(payload, "cost_currency", None)
    base_currency = str(state.get("currency") or payload_cost_currency or "BRL").upper()
    message_currency = str(payload_cost_currency or base_currency).upper()
    message_cost = _money_value(payload_cost_amount)

    state["currency"] = base_currency
    state["messageCount"] = _counter_value(state.get("messageCount")) + 1
    state[f"{payload.direction.value}Count"] = (
        _counter_value(state.get(f"{payload.direction.value}Count")) + 1
    )
    if payload.author_type == MessageAuthorType.agent:
        state["agentMessageCount"] = _counter_value(state.get("agentMessageCount")) + 1
    if delivery == MessageDeliveryStatus.local_draft:
        state["localDraftCount"] = _counter_value(state.get("localDraftCount")) + 1
    if message_cost > 0:
        state["chargeableMessageCount"] = _counter_value(state.get("chargeableMessageCount")) + 1

    state["byChannel"] = _increment_counter(state.get("byChannel"), payload.channel.value)
    state["byTransport"] = _increment_counter(state.get("byTransport"), payload.transport)
    if connection_key:
        state["byConnection"] = _increment_counter(state.get("byConnection"), connection_key)

    estimated_costs = (
        dict(state.get("estimatedCostsByCurrency"))
        if isinstance(state.get("estimatedCostsByCurrency"), dict)
        else {}
    )
    estimated_total = _money_value(estimated_costs.get(message_currency)) + message_cost
    estimated_costs[message_currency] = f"{estimated_total:.6f}"
    state["estimatedCostsByCurrency"] = estimated_costs
    state["estimatedExternalCost"] = f"{_money_value(estimated_costs.get(base_currency)):.6f}"

    actual_costs = (
        dict(state.get("actualCostsByCurrency"))
        if isinstance(state.get("actualCostsByCurrency"), dict)
        else {}
    )
    state["actualCostsByCurrency"] = actual_costs
    state["costsByCurrency"] = dict(actual_costs)
    state["externalCost"] = f"{_money_value(actual_costs.get(base_currency)):.6f}"
    state["lastTransport"] = payload.transport
    state["lastConnectionKey"] = connection_key
    state["updatedAt"] = _now().isoformat()
    return state


def _require_matching_acquisition_event_replay(
    existing: AcquisitionEvent,
    payload: AcquisitionEventCreate,
) -> None:
    existing_value = Decimal(str(existing.value)) if existing.value is not None else None
    requested_value = Decimal(str(payload.value)) if payload.value is not None else None
    if (
        existing.matter_id != payload.matter_id
        or existing.client_id != payload.client_id
        or existing.event_name != payload.event_name
        or existing.channel != payload.channel
        or existing.source != payload.source
        or existing.medium != payload.medium
        or existing.campaign != payload.campaign
        or existing.event_id != payload.event_id
        or existing_value != requested_value
        or existing.currency != payload.currency
        or (existing.click_ids or {}) != (payload.click_ids or {})
        or (existing.event_metadata or {}) != (payload.event_metadata or {})
        or (
            payload.happened_at is not None
            and _normalized_moment(existing.happened_at) != _normalized_moment(payload.happened_at)
        )
    ):
        raise ValueError("dedupeKey já utilizado por outro evento de aquisição.")


def _require_matching_external_intent_replay(
    existing: ExternalActionIntent,
    user_id: UUID,
    payload: ExternalActionIntentCreate,
) -> None:
    if (
        existing.requested_by_user_id != user_id
        or existing.matter_id != payload.matter_id
        or existing.action_type != payload.action_type
        or existing.provider != payload.provider
        or (existing.payload or {}) != (payload.payload or {})
    ):
        raise ValueError("idempotencyKey já utilizada por outra intenção externa.")


def _email_ingest_payload_hash(payload: EmailIngestRequest) -> str:
    canonical = json.dumps(
        payload.model_dump(mode="json", by_alias=True),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _email_message_payload(payload: EmailIngestRequest) -> OmnichannelMessageCreate:
    return OmnichannelMessageCreate(
        channel=OmnichannelChannel.email,
        direction=MessageDirection.inbound,
        author_type=MessageAuthorType.client,
        author_label=payload.sender_name or payload.sender_email,
        body=payload.body,
        external_message_id=payload.message_id,
        idempotency_key=f"email:{payload.message_id}",
        occurred_at=payload.received_at,
        message_metadata={
            "senderEmail": payload.sender_email.lower(),
            "subject": payload.subject,
        },
    )


def _require_matching_legacy_email_message(
    existing: OmnichannelMessage,
    payload: EmailIngestRequest,
) -> None:
    metadata = existing.message_metadata or {}
    if (
        existing.author_user_id is not None
        or existing.channel != OmnichannelChannel.email
        or existing.direction != MessageDirection.inbound
        or existing.author_type != MessageAuthorType.client
        or existing.author_label != (payload.sender_name or payload.sender_email)
        or existing.body != payload.body
        or (existing.external_message_id or None) != payload.message_id
        or metadata.get("senderEmail") != payload.sender_email.lower()
        or metadata.get("subject") != payload.subject
        or (
            payload.received_at is not None
            and _normalized_moment(existing.occurred_at) != _normalized_moment(payload.received_at)
        )
    ):
        raise ValueError("messageId de e-mail já utilizado com conteúdo diferente.")


class FunnelVersionConflict(ValueError):
    """Raised when a board command was produced from an obsolete snapshot."""

    def __init__(
        self, current_version: int, message: str = "O funil foi alterado por outro operador."
    ):
        super().__init__(message)
        self.current_version = current_version


@dataclass(frozen=True)
class FunnelMoveResult:
    matter_id: UUID
    column_id: KanbanColumnId
    micro_status: MicroStatus
    card_order: int
    board_version: int
    operation_id: str
    deduplicated: bool = False


class KlmOperationsService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def record_audit(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: Optional[UUID],
        actor_type: str,
        actor_label: str,
        event_type: str,
        entity_type: str,
        entity_id: Optional[UUID],
        summary: str,
        correlation_id: Optional[str] = None,
        before_data: Optional[dict] = None,
        after_data: Optional[dict] = None,
    ) -> KlmAuditEvent:
        if actor_type == "human":
            actor_label = await resolve_human_actor_label(
                self.db,
                actor_user_id,
            )
        event = KlmAuditEvent(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            actor_type=actor_type,
            actor_label=actor_label,
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            summary=summary,
            correlation_id=correlation_id,
            before_data=before_data,
            after_data=after_data,
        )
        self.db.add(event)
        await self.db.flush()
        return event

    async def _collect_diagnostic_check(
        self,
        key: str,
        label: str,
        probe: Callable[[], Awaitable[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        checked_at = _now()
        try:
            observation = await probe()
            status = observation.get("status")
            if status not in {"healthy", "degraded"}:
                raise ValueError("invalid diagnostic status")
            return {
                "key": key,
                "label": label,
                "status": status,
                "state": str(observation.get("state") or "unknown"),
                "detail": str(observation.get("detail") or "Verificação sem detalhe."),
                "checked_at": checked_at,
                "evidence": dict(observation.get("evidence") or {}),
            }
        except Exception:
            logger.warning("Local diagnostic check '%s' is unavailable.", key)
            return {
                "key": key,
                "label": label,
                "status": "degraded",
                "state": "unavailable",
                "detail": "Verificação indisponível no runtime local.",
                "checked_at": checked_at,
                "evidence": {},
            }

    async def _diagnose_database(self) -> Dict[str, Any]:
        async with self.db.begin_nested():
            selected = await self.db.scalar(text("SELECT 1"))
        bind = self.db.get_bind()
        dialect = bind.dialect.name if bind is not None else "unknown"
        healthy = selected == 1
        return {
            "status": "healthy" if healthy else "degraded",
            "state": "reachable" if healthy else "unexpected_response",
            "detail": (
                "SELECT 1 respondeu corretamente."
                if healthy
                else "SELECT 1 retornou uma resposta inesperada."
            ),
            "evidence": {"dialect": dialect},
        }

    async def _diagnose_external_effects_gate(self) -> Dict[str, Any]:
        enabled = external_delivery_allowed()
        return {
            "status": "degraded" if enabled else "healthy",
            "state": "open" if enabled else "closed",
            "detail": (
                "O gate efetivo permite efeitos externos."
                if enabled
                else "O gate efetivo bloqueia efeitos externos."
            ),
            "evidence": {"enabled": enabled},
        }

    async def _diagnose_aws_gate(self) -> Dict[str, Any]:
        enabled = aws_external_access_allowed()
        return {
            "status": "degraded" if enabled else "healthy",
            "state": "open" if enabled else "closed",
            "detail": (
                "O gate efetivo permite acesso à AWS."
                if enabled
                else "O gate efetivo bloqueia acesso à AWS."
            ),
            "evidence": {"enabled": enabled},
        }

    async def _diagnose_model_api_gate(self) -> Dict[str, Any]:
        enabled = external_model_api_execution_allowed()
        return {
            "status": "degraded" if enabled else "healthy",
            "state": "open" if enabled else "closed",
            "detail": (
                "O gate efetivo permite APIs externas de modelos."
                if enabled
                else "O gate efetivo bloqueia APIs externas de modelos."
            ),
            "evidence": {"enabled": enabled},
        }

    async def _diagnose_oss_sidecars(self) -> Dict[str, Any]:
        snapshot = await oss_runtime_status(force_refresh=True)
        services = snapshot.get("services")
        if not isinstance(services, list) or not services:
            return {
                "status": "degraded",
                "state": "unavailable",
                "detail": "O agregador OSS não retornou serviços locais.",
                "evidence": {
                    "total": 0,
                    "ready": 0,
                    "degraded": 0,
                    "stopped": 0,
                    "blocked": 0,
                    "unknown": 0,
                },
            }

        allowed_states = {"ready", "degraded", "stopped", "blocked"}
        counts: Counter[str] = Counter()
        for service in services:
            raw_state = service.get("state") if isinstance(service, dict) else None
            state = raw_state if raw_state in allowed_states else "unknown"
            counts[state] += 1

        total = len(services)
        ready = counts["ready"]
        healthy = ready == total
        return {
            "status": "healthy" if healthy else "degraded",
            "state": "ready" if healthy else "degraded",
            "detail": f"{ready} de {total} sidecars locais estão prontos.",
            "evidence": {
                "total": total,
                "ready": ready,
                "degraded": counts["degraded"],
                "stopped": counts["stopped"],
                "blocked": counts["blocked"],
                "unknown": counts["unknown"],
            },
        }

    async def _diagnose_migrations(self) -> Dict[str, Any]:
        current_heads = await asyncio.to_thread(_current_alembic_heads)
        if not current_heads:
            return {
                "status": "degraded",
                "state": "unavailable",
                "detail": "Nenhum head Alembic atual foi encontrado.",
                "evidence": {
                    "currentHeads": [],
                    "appliedHeads": [],
                    "appliedHeadsAvailable": False,
                },
            }

        async with self.db.begin_nested():
            connection = await self.db.connection()
            if connection.dialect.name == "sqlite":
                import app.models  # noqa: F401 - register every local ORM table
                from app.db.base import Base

                def inspect_local_schema(sync_connection):
                    inspector = sa_inspect(sync_connection)
                    existing_tables = set(inspector.get_table_names())
                    expected_tables = set(Base.metadata.tables)
                    missing_tables = sorted(expected_tables - existing_tables)
                    missing_columns: dict[str, list[str]] = {}
                    for table_name in sorted(expected_tables & existing_tables):
                        expected = set(Base.metadata.tables[table_name].columns.keys())
                        actual = {
                            str(column["name"]) for column in inspector.get_columns(table_name)
                        }
                        if missing := sorted(expected - actual):
                            missing_columns[table_name] = missing
                    return missing_tables, missing_columns

                missing_tables, missing_columns = await connection.run_sync(inspect_local_schema)
                current = not missing_tables and not missing_columns
                return {
                    "status": "healthy" if current else "degraded",
                    "state": "local_schema_current" if current else "local_schema_drifted",
                    "detail": (
                        "O schema SQLite local corresponde aos modelos atuais."
                        if current
                        else "O schema SQLite local possui tabelas ou colunas ausentes."
                    ),
                    "evidence": {
                        "currentHeads": current_heads,
                        "appliedHeads": [],
                        "appliedHeadsAvailable": False,
                        "schemaManagedBy": "local_bootstrap",
                        "missingTables": missing_tables[:25],
                        "missingColumns": dict(list(missing_columns.items())[:25]),
                    },
                }
            has_version_table = await connection.run_sync(
                lambda sync_connection: sa_inspect(sync_connection).has_table("alembic_version")
            )
            if not has_version_table:
                return {
                    "status": "degraded",
                    "state": "unavailable",
                    "detail": "Os heads aplicados não estão disponíveis neste banco.",
                    "evidence": {
                        "currentHeads": current_heads,
                        "appliedHeads": [],
                        "appliedHeadsAvailable": False,
                    },
                }
            result = await self.db.execute(text("SELECT version_num FROM alembic_version"))
            applied_heads = sorted({str(value) for value in result.scalars().all()})

        current = set(current_heads) == set(applied_heads)
        return {
            "status": "healthy" if current else "degraded",
            "state": "current" if current else "drifted",
            "detail": (
                "Os heads Alembic aplicados correspondem ao código atual."
                if current
                else "Os heads Alembic aplicados divergem do código atual."
            ),
            "evidence": {
                "currentHeads": current_heads,
                "appliedHeads": applied_heads,
                "appliedHeadsAvailable": True,
            },
        }

    async def run_local_diagnostics(
        self,
        workspace_id: UUID,
        user_id: UUID,
    ) -> Dict[str, Any]:
        run_id = uuid4()
        checked_at = _now()
        probes = (
            ("database", "Banco de dados", self._diagnose_database),
            (
                "external_effects_gate",
                "Gate de efeitos externos",
                self._diagnose_external_effects_gate,
            ),
            ("aws_gate", "Gate AWS", self._diagnose_aws_gate),
            (
                "model_api_gate",
                "Gate de APIs de modelos",
                self._diagnose_model_api_gate,
            ),
            ("oss_sidecars", "Sidecars OSS", self._diagnose_oss_sidecars),
            ("migrations", "Migrações Alembic", self._diagnose_migrations),
        )
        checks = [
            await self._collect_diagnostic_check(key, label, probe) for key, label, probe in probes
        ]
        healthy = sum(check["status"] == "healthy" for check in checks)
        degraded = len(checks) - healthy
        unavailable = sum(check["state"] == "unavailable" for check in checks)
        overall_status = "healthy" if degraded == 0 else "degraded"
        summary = {
            "status": overall_status,
            "total": len(checks),
            "healthy": healthy,
            "degraded": degraded,
            "unavailable": unavailable,
        }

        try:
            async with self.db.begin_nested():
                await self.record_audit(
                    workspace_id=workspace_id,
                    actor_user_id=user_id,
                    actor_type="human",
                    actor_label="Usuário KLM",
                    event_type="local_diagnostics_run",
                    entity_type="local_diagnostics",
                    entity_id=None,
                    summary=f"Diagnóstico local concluído com estado {overall_status}.",
                    correlation_id=str(run_id),
                    after_data={
                        "runId": str(run_id),
                        "status": overall_status,
                        "counts": {
                            "total": len(checks),
                            "healthy": healthy,
                            "degraded": degraded,
                            "unavailable": unavailable,
                        },
                        "degradedChecks": [
                            check["key"] for check in checks if check["status"] == "degraded"
                        ],
                    },
                )
        except Exception:
            logger.warning("Local diagnostics audit is unavailable.")

        return {
            "run_id": run_id,
            "status": overall_status,
            "checked_at": checked_at,
            "summary": summary,
            "checks": checks,
        }

    async def ensure_default_areas(self, workspace_id: UUID) -> None:
        existing_rows = list(
            (
                await self.db.scalars(
                    select(AreaBlueprint).where(AreaBlueprint.workspace_id == workspace_id)
                )
            ).all()
        )
        by_key = {row.area_key: row for row in existing_rows}

        # Migrate old built-in keys only when that workspace does not already own
        # the canonical module. Custom tenant keys remain untouched.
        for old_key, row in list(by_key.items()):
            normalized = canonical_area_key(old_key)
            if normalized and normalized != old_key and normalized not in by_key:
                row.area_key = normalized
                by_key[normalized] = row
                by_key.pop(old_key, None)

        catalog_rows = {area["areaKey"]: area for area in LEGAL_AREA_CATALOG}
        has_any_area = bool(existing_rows)
        for key, name, default_status, readiness in DEFAULT_AREAS:
            catalog_entry = catalog_rows[key]
            canonical_blueprint = catalog_blueprint(key) or {}
            area = by_key.get(key)
            if area is None:
                activate_by_default = default_status == AreaBlueprintStatus.active and not has_any_area
                area = AreaBlueprint(
                    workspace_id=workspace_id,
                    area_key=key,
                    name=name,
                    status=(
                        AreaBlueprintStatus.active
                        if activate_by_default
                        else AreaBlueprintStatus.blueprint
                    ),
                    version=(
                        AREA_ACTIVE_VERSION
                        if activate_by_default
                        else f"catalog-{LEGAL_AREA_CATALOG_VERSION}"
                    ),
                    readiness=AREA_ACTIVE_READINESS if activate_by_default else readiness,
                    is_active=activate_by_default,
                    description=catalog_entry["description"],
                    blueprint=canonical_blueprint,
                )
                self.db.add(area)
                by_key[key] = area
                continue

            area.name = catalog_entry["name"]
            if not area.description or area.description.startswith("Blueprint isolado"):
                area.description = catalog_entry["description"]
            area.blueprint = _merge_catalog_blueprint(
                area.blueprint or {}, canonical_blueprint
            )
            _validate_area_blueprint(area.blueprint)
            if area.is_active:
                area.status = AreaBlueprintStatus.active
                area.readiness = AREA_ACTIVE_READINESS
                area.version = AREA_ACTIVE_VERSION
            else:
                area.status = AreaBlueprintStatus.blueprint
                area.readiness = max(area.readiness, AREA_VALIDATED_READINESS)
                area.version = f"catalog-{LEGAL_AREA_CATALOG_VERSION}"
        await self.db.flush()

    async def list_areas(self, workspace_id: UUID) -> List[AreaBlueprint]:
        await self.ensure_default_areas(workspace_id)
        result = await self.db.execute(
            select(AreaBlueprint)
            .where(AreaBlueprint.workspace_id == workspace_id)
            .order_by(AreaBlueprint.is_active.desc(), AreaBlueprint.name.asc())
        )
        return list(result.scalars().all())

    async def _area_for_command(self, workspace_id: UUID, area_id: UUID) -> Optional[AreaBlueprint]:
        return await self.db.scalar(
            select(AreaBlueprint)
            .where(
                AreaBlueprint.id == area_id,
                AreaBlueprint.workspace_id == workspace_id,
            )
            .with_for_update()
        )

    async def create_area(
        self,
        workspace_id: UUID,
        user_id: UUID,
        payload: AreaBlueprintCreate,
    ) -> AreaBlueprint:
        if payload.status == AreaBlueprintStatus.active:
            raise ValueError("Novas áreas só podem nascer como draft ou blueprint.")
        existing = await self.db.scalar(
            select(AreaBlueprint.id).where(
                AreaBlueprint.workspace_id == workspace_id,
                AreaBlueprint.area_key == payload.area_key,
            )
        )
        if existing:
            raise ValueError("Já existe uma área com esta chave.")
        blueprint = dict(payload.blueprint or {})
        if not blueprint:
            blueprint = _generic_area_blueprint()
            blueprint.update(
                {
                    "catalogManaged": False,
                    "setupAssistant": {
                        "mode": "ai_assisted",
                        "scope": "basic",
                        "status": "ready_for_configuration",
                        "suggestionsRequireHumanConfirmation": True,
                    },
                    "communicationProfile": {
                        "tone": "balanced",
                        "formality": "formal",
                        "neutralLanguage": True,
                        "plainLanguage": True,
                        "signatureStyle": "office",
                        "preferredTerms": [],
                        "avoidedTerms": [],
                        "customInstructions": None,
                    },
                }
            )
        _validate_area_blueprint(blueprint)
        area = AreaBlueprint(
            workspace_id=workspace_id,
            **payload.model_dump(exclude={"blueprint"}),
            blueprint=blueprint,
            is_active=False,
        )
        self.db.add(area)
        await self.db.flush()
        await self.record_audit(
            workspace_id=workspace_id,
            actor_user_id=user_id,
            actor_type="human",
            actor_label="Usuário KLM",
            event_type="area_blueprint_created",
            entity_type="area_blueprint",
            entity_id=area.id,
            summary=f"Blueprint '{area.name}' criado localmente.",
            after_data={"areaKey": area.area_key, "status": area.status.value},
        )
        return area

    async def update_area(
        self,
        workspace_id: UUID,
        area_id: UUID,
        user_id: UUID,
        payload: AreaBlueprintUpdate,
    ) -> Optional[AreaBlueprint]:
        area = await self._area_for_command(workspace_id, area_id)
        if not area:
            return None
        changes = payload.model_dump(exclude_unset=True)
        if changes.get("status") == AreaBlueprintStatus.active and not area.is_active:
            raise ValueError("Use o comando de ativação depois de validar o blueprint.")
        protected_lifecycle_fields = {"status", "version", "readiness", "blueprint"}
        if area.is_active and protected_lifecycle_fields.intersection(changes):
            raise ValueError("O ciclo de vida de uma área ativa não pode ser alterado por edição.")
        blueprint_changed = "blueprint" in changes and changes["blueprint"] != area.blueprint
        if blueprint_changed:
            changes["blueprint"] = _validate_area_blueprint(
                changes["blueprint"] or {}
            ).model_dump(mode="json", by_alias=True)
        before = {key: getattr(area, key) for key in changes}
        for key, value in changes.items():
            setattr(area, key, value)
        if blueprint_changed:
            area.validated_at = None
            area.validated_by_user_id = None
            if not area.is_active:
                area.readiness = min(area.readiness, AREA_MATERIALIZED_READINESS)
        area.updated_at = _now()
        await self.db.flush()
        await self.record_audit(
            workspace_id=workspace_id,
            actor_user_id=user_id,
            actor_type="human",
            actor_label="Usuário KLM",
            event_type="area_blueprint_updated",
            entity_type="area_blueprint",
            entity_id=area.id,
            summary=f"Blueprint '{area.name}' atualizado localmente.",
            before_data={key: str(value) for key, value in before.items()},
            after_data={key: str(value) for key, value in changes.items()},
        )
        return area

    async def materialize_area(
        self, workspace_id: UUID, area_id: UUID, user_id: UUID
    ) -> Optional[AreaBlueprint]:
        area = await self._area_for_command(workspace_id, area_id)
        if not area:
            return None
        if area.area_key == "saude_suplementar" or area.is_active:
            return area

        generated = not bool(area.blueprint)
        catalog_contract = catalog_blueprint(area.area_key)
        if catalog_contract:
            materialized_blueprint = _merge_catalog_blueprint(
                area.blueprint or {}, catalog_contract
            )
            generated = not bool(area.blueprint)
        elif generated:
            materialized_blueprint = _generic_area_blueprint()
        else:
            _validate_area_blueprint(area.blueprint)
            materialized_blueprint = area.blueprint

        _validate_area_blueprint(materialized_blueprint)

        already_materialized = (
            not generated
            and area.status == AreaBlueprintStatus.blueprint
            and area.version == AREA_MATERIALIZED_VERSION
            and area.readiness >= AREA_MATERIALIZED_READINESS
        )
        if already_materialized:
            return area

        before = {
            "status": area.status.value,
            "version": area.version,
            "readiness": area.readiness,
            "blueprintEmpty": not bool(area.blueprint),
        }
        area.blueprint = materialized_blueprint
        area.status = AreaBlueprintStatus.blueprint
        area.version = AREA_MATERIALIZED_VERSION
        area.readiness = (
            AREA_MATERIALIZED_READINESS
            if generated
            else max(area.readiness, AREA_MATERIALIZED_READINESS)
        )
        if generated:
            area.validated_at = None
            area.validated_by_user_id = None
        area.updated_at = _now()
        await self.db.flush()
        await self.record_audit(
            workspace_id=workspace_id,
            actor_user_id=user_id,
            actor_type="human",
            actor_label="Usuário KLM",
            event_type="area_blueprint_materialized",
            entity_type="area_blueprint",
            entity_id=area.id,
            summary=f"Módulo jurídico '{area.name}' preparado para uso.",
            correlation_id=f"area:{area.id}:materialize",
            before_data=before,
            after_data={
                "status": area.status.value,
                "version": area.version,
                "readiness": area.readiness,
                "generated": generated,
                "externalActionsDisabled": True,
            },
        )
        return area

    async def _enforce_area_limit(self, workspace_id: UUID) -> None:
        async def active_area_count() -> int:
            return int(
                await self.db.scalar(
                    select(func.count(AreaBlueprint.id)).where(
                        AreaBlueprint.workspace_id == workspace_id,
                        AreaBlueprint.is_active.is_(True),
                    )
                )
                or 0
            )

        await EntitlementEnforcementService(self.db).enforce_stock(
            workspace_id=workspace_id,
            entitlement=EntitlementKey.legal_areas,
            load_current_quantity=active_area_count,
        )

    async def set_area_activation(
        self,
        workspace_id: UUID,
        area_id: UUID,
        user_id: UUID,
        *,
        enabled: bool,
        allow_over_plan: bool = False,
    ) -> Optional[AreaBlueprint]:
        area = await self._area_for_command(workspace_id, area_id)
        if not area:
            return None
        if area.is_active == enabled:
            return area

        before = {
            "status": area.status.value,
            "version": area.version,
            "readiness": area.readiness,
            "isActive": area.is_active,
        }
        if enabled:
            if not allow_over_plan:
                await self._enforce_area_limit(workspace_id)
            catalog_contract = catalog_blueprint(area.area_key)
            if catalog_contract:
                area.blueprint = _merge_catalog_blueprint(
                    area.blueprint or {}, catalog_contract
                )
            elif not area.blueprint:
                area.blueprint = _generic_area_blueprint()
            _validate_area_blueprint(area.blueprint)
            area.validated_at = _now()
            area.validated_by_user_id = user_id
            area.status = AreaBlueprintStatus.active
            area.is_active = True
            area.readiness = AREA_ACTIVE_READINESS
            area.version = AREA_ACTIVE_VERSION
            event_type = "area_blueprint_activated"
            summary = f"Área '{area.name}' ativada em um clique."
        else:
            area.status = AreaBlueprintStatus.blueprint
            area.is_active = False
            area.readiness = max(area.readiness, AREA_VALIDATED_READINESS)
            area.version = (
                f"catalog-{LEGAL_AREA_CATALOG_VERSION}"
                if catalog_blueprint(area.area_key)
                else AREA_MATERIALIZED_VERSION
            )
            event_type = "area_blueprint_deactivated"
            summary = f"Área '{area.name}' desativada; histórico preservado."
        area.updated_at = _now()
        await self.db.flush()
        await self.record_audit(
            workspace_id=workspace_id,
            actor_user_id=user_id,
            actor_type="human",
            actor_label="Usuário KLM",
            event_type=event_type,
            entity_type="area_blueprint",
            entity_id=area.id,
            summary=summary,
            correlation_id=f"area:{area.id}:activation",
            before_data=before,
            after_data={
                "status": area.status.value,
                "version": area.version,
                "readiness": area.readiness,
                "isActive": area.is_active,
                "catalogVersion": (area.blueprint or {}).get("catalogVersion"),
                "planOverride": allow_over_plan,
            },
        )
        return area

    async def validate_area(
        self, workspace_id: UUID, area_id: UUID, user_id: UUID
    ) -> Optional[AreaBlueprint]:
        area = await self._area_for_command(workspace_id, area_id)
        if not area:
            return None
        _validate_area_blueprint(area.blueprint)
        already_validated = (
            area.validated_at is not None
            and area.validated_by_user_id is not None
            and area.readiness >= AREA_VALIDATED_READINESS
            and (area.is_active or area.status == AreaBlueprintStatus.blueprint)
        )
        if already_validated:
            return area

        before = {
            "status": area.status.value,
            "version": area.version,
            "readiness": area.readiness,
            "validatedAt": area.validated_at.isoformat() if area.validated_at else None,
        }
        area.validated_at = _now()
        area.validated_by_user_id = user_id
        if area.is_active:
            area.readiness = max(area.readiness, AREA_VALIDATED_READINESS)
        else:
            area.status = AreaBlueprintStatus.blueprint
            area.version = AREA_MATERIALIZED_VERSION
            area.readiness = AREA_VALIDATED_READINESS
        area.updated_at = _now()
        await self.db.flush()
        await self.record_audit(
            workspace_id=workspace_id,
            actor_user_id=user_id,
            actor_type="human",
            actor_label="Usuário KLM",
            event_type="area_blueprint_validated",
            entity_type="area_blueprint",
            entity_id=area.id,
            summary=f"Blueprint '{area.name}' validado sem ativação.",
            correlation_id=f"area:{area.id}:validate",
            before_data=before,
            after_data={
                "status": area.status.value,
                "version": area.version,
                "readiness": area.readiness,
                "validatedAt": area.validated_at.isoformat(),
                "validatedByUserId": str(area.validated_by_user_id),
                "externalActionsDisabled": True,
            },
        )
        return area

    async def activate_area(
        self, workspace_id: UUID, area_id: UUID, user_id: UUID
    ) -> Optional[AreaBlueprint]:
        area = await self._area_for_command(workspace_id, area_id)
        if not area:
            return None
        if area.is_active and area.status == AreaBlueprintStatus.active:
            if area.area_key == "saude_suplementar":
                return area
            if area.validated_at is None or area.validated_by_user_id is None:
                raise ValueError("O blueprint precisa ser validado antes da ativação.")
            _validate_area_blueprint(area.blueprint)
            return area
        if area.validated_at is None or area.validated_by_user_id is None:
            raise ValueError("O blueprint precisa ser validado antes da ativação.")
        _validate_area_blueprint(area.blueprint)
        await self._enforce_area_limit(workspace_id)

        before = {
            "status": area.status.value,
            "version": area.version,
            "readiness": area.readiness,
            "isActive": area.is_active,
        }
        area.status = AreaBlueprintStatus.active
        area.is_active = True
        area.readiness = AREA_ACTIVE_READINESS
        area.version = AREA_ACTIVE_VERSION
        area.updated_at = _now()
        await self.db.flush()
        await self.record_audit(
            workspace_id=workspace_id,
            actor_user_id=user_id,
            actor_type="human",
            actor_label="Usuário KLM",
            event_type="area_blueprint_activated",
            entity_type="area_blueprint",
            entity_id=area.id,
            summary=f"Área '{area.name}' ativada somente na operação local.",
            correlation_id=f"area:{area.id}:activate",
            before_data=before,
            after_data={
                "status": area.status.value,
                "version": area.version,
                "readiness": area.readiness,
                "isActive": area.is_active,
                "validatedAt": area.validated_at.isoformat(),
                "validatedByUserId": str(area.validated_by_user_id),
                "externalActionsDisabled": True,
            },
        )
        return area

    @staticmethod
    def tool_catalog() -> List[Dict[str, Any]]:
        return [
            {
                "key": key,
                "name": name,
                "category": category,
                "state": state,
                "external": external,
                "humanGate": human_gate,
                "capabilities": capabilities,
            }
            for key, name, category, state, external, human_gate, capabilities in TOOL_CATALOG
        ]

    async def _matter(self, workspace_id: UUID, matter_id: UUID) -> Optional[Matter]:
        return await self.db.scalar(
            select(Matter).where(
                Matter.id == matter_id,
                Matter.workspace_id == workspace_id,
                Matter.deleted_at.is_(None),
            )
        )

    async def _client(self, workspace_id: UUID, client_id: UUID) -> Optional[Client]:
        return await self.db.scalar(
            select(Client).where(
                Client.id == client_id,
                Client.workspace_id == workspace_id,
                Client.deleted_at.is_(None),
            )
        )

    async def _workspace_member(self, workspace_id: UUID, user_id: UUID) -> bool:
        membership_id = await self.db.scalar(
            select(WorkspaceMembership.id)
            .join(User, User.id == WorkspaceMembership.user_id)
            .where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.user_id == user_id,
                WorkspaceMembership.status == MembershipStatus.active,
                User.is_active.is_(True),
            )
        )
        return membership_id is not None

    async def create_thread(
        self,
        workspace_id: UUID,
        user_id: Optional[UUID],
        payload: OmnichannelThreadCreate,
        *,
        actor_type: str = "human",
        actor_label: str = "Usuário KLM",
    ) -> OmnichannelThread:
        matter = await self._matter(workspace_id, payload.matter_id) if payload.matter_id else None
        client = await self._client(workspace_id, payload.client_id) if payload.client_id else None
        if payload.matter_id and not matter:
            raise LookupError("Caso não encontrado no workspace.")
        if payload.client_id and not client:
            raise LookupError("Cliente não encontrado no workspace.")
        if matter and client and matter.client_id != client.id:
            raise ValueError("Cliente não pertence ao caso informado.")
        identity = None
        if payload.identity_id:
            identity = await self.db.scalar(
                select(OmnichannelIdentity).where(
                    OmnichannelIdentity.id == payload.identity_id,
                    OmnichannelIdentity.workspace_id == workspace_id,
                )
            )
            if not identity:
                raise LookupError("Identidade omnichannel não encontrada no workspace.")
            if payload.client_id and identity.client_id and payload.client_id != identity.client_id:
                raise ValueError("Cliente diverge da identidade omnichannel.")
            if matter and identity.client_id and matter.client_id != identity.client_id:
                raise ValueError("Caso diverge do cliente da identidade omnichannel.")
        if payload.assigned_team_id:
            team = await self.db.scalar(
                select(WorkspaceTeam).where(
                    WorkspaceTeam.id == payload.assigned_team_id,
                    WorkspaceTeam.workspace_id == workspace_id,
                    WorkspaceTeam.is_active.is_(True),
                )
            )
            if not team:
                raise LookupError("Equipe não encontrada no workspace.")
        values = payload.model_dump()
        if identity:
            values["client_id"] = values.get("client_id") or identity.client_id
        if matter:
            route = _matter_legal_routing(matter)
            metadata = dict(values.get("thread_metadata") or {})
            metadata["legalRouting"] = {
                **route,
                "source": "thread_create",
                "resolvedAt": _now().isoformat(),
                "manualContextRevision": int(matter.manual_context_revision or 0),
            }
            metadata["autoOwnerAgentKey"] = route["sdrAgentKey"]
            values["thread_metadata"] = metadata
            values["owner_agent_key"] = (
                values.get("owner_agent_key") or route["sdrAgentKey"]
            )
        thread = OmnichannelThread(workspace_id=workspace_id, **values)
        self.db.add(thread)
        await self.db.flush()
        await self.record_audit(
            workspace_id=workspace_id,
            actor_user_id=user_id,
            actor_type=actor_type,
            actor_label=actor_label,
            event_type="omnichannel_thread_created",
            entity_type="omnichannel_thread",
            entity_id=thread.id,
            summary=f"Thread {thread.primary_channel.value} criada localmente.",
        )
        return thread

    async def list_threads(
        self,
        workspace_id: UUID,
        channel: Optional[OmnichannelChannel] = None,
        status: Optional[ThreadStatus] = None,
        search: Optional[str] = None,
        limit: int = 100,
        *,
        include_sensitive_search: bool = True,
        assigned_human_id: Optional[UUID] = None,
        assigned_team_id: Optional[UUID] = None,
        owner_agent_key: Optional[str] = None,
        queue: Optional[str] = None,
        score_min: Optional[int] = None,
        score_max: Optional[int] = None,
        thread_priority_min: Optional[int] = None,
        thread_priority_max: Optional[int] = None,
        matter_priority: Optional[str] = None,
        urgency: Optional[str] = None,
        classification: Optional[str] = None,
        demand_type: Optional[str] = None,
        health_subtheme: Optional[str] = None,
        column_id: Optional[str] = None,
        lead_source: Optional[str] = None,
        tag: Optional[str] = None,
        has_unread: Optional[bool] = None,
        is_favorite: Optional[bool] = None,
        flow_status: Optional[str] = None,
        sla_state: Optional[str] = None,
        last_message_after: Optional[datetime] = None,
        last_message_before: Optional[datetime] = None,
        sort_by: str = "recent",
        sort_direction: str = "desc",
    ) -> List[OmnichannelThread]:
        # Identity merges preserve the superseded thread because channel and
        # delivery evidence are append-only.  The merged identity is the
        # authoritative tombstone: it must not keep rendering a second inbox
        # card after its mutable messages/bindings move to the target thread.
        query = (
            select(OmnichannelThread)
            .outerjoin(
                OmnichannelIdentity,
                and_(
                    OmnichannelIdentity.workspace_id == OmnichannelThread.workspace_id,
                    OmnichannelIdentity.id == OmnichannelThread.identity_id,
                ),
            )
            .where(
                OmnichannelThread.workspace_id == workspace_id,
                or_(
                    OmnichannelThread.identity_id.is_(None),
                    OmnichannelIdentity.id.is_(None),
                    OmnichannelIdentity.identity_state != "merged",
                ),
            )
        )
        matter_filters = any(
            value is not None
            for value in (
                matter_priority,
                urgency,
                classification,
                demand_type,
                health_subtheme,
                column_id,
                lead_source,
                tag,
            )
        )
        needs_matter_join = matter_filters or bool(search)
        if needs_matter_join:
            query = query.outerjoin(
                Matter,
                and_(
                    Matter.id == OmnichannelThread.matter_id,
                    Matter.workspace_id == workspace_id,
                    Matter.deleted_at.is_(None),
                ),
            )
        if channel:
            query = query.where(OmnichannelThread.primary_channel == channel)
        if status:
            query = query.where(OmnichannelThread.status == status)
        if assigned_human_id:
            query = query.where(OmnichannelThread.assigned_human_id == assigned_human_id)
        if assigned_team_id:
            query = query.where(OmnichannelThread.assigned_team_id == assigned_team_id)
        if owner_agent_key:
            query = query.where(OmnichannelThread.owner_agent_key == owner_agent_key)
        if queue:
            query = query.where(OmnichannelThread.queue == queue)
        if score_min is not None:
            query = query.where(OmnichannelThread.score >= score_min)
        if score_max is not None:
            query = query.where(OmnichannelThread.score <= score_max)
        if thread_priority_min is not None:
            query = query.where(OmnichannelThread.priority >= thread_priority_min)
        if thread_priority_max is not None:
            query = query.where(OmnichannelThread.priority <= thread_priority_max)
        if matter_priority:
            query = query.where(Matter.priority == matter_priority)
        if urgency:
            query = query.where(Matter.urgency == urgency)
        if classification:
            query = query.where(Matter.classification == classification)
        if demand_type:
            query = query.where(
                or_(
                    Matter.demand_type.ilike(f"%{demand_type}%"),
                    cast(Matter.health_subtheme, String).ilike(f"%{demand_type}%"),
                )
            )
        if health_subtheme:
            query = query.where(Matter.health_subtheme == health_subtheme)
        if column_id:
            query = query.where(Matter.column_id == column_id)
        if lead_source:
            query = query.where(Matter.lead_source == lead_source)
        if tag:
            query = query.where(cast(Matter.tags, String).ilike(f"%{tag}%"))
        if has_unread is not None:
            query = query.where(OmnichannelThread.unread_count > 0 if has_unread else OmnichannelThread.unread_count == 0)
        if is_favorite is not None:
            query = query.where(OmnichannelThread.is_favorite.is_(is_favorite))
        if flow_status:
            flow_exists = select(FlowRun.id).where(
                FlowRun.workspace_id == workspace_id,
                FlowRun.matter_id == OmnichannelThread.matter_id,
                FlowRun.status == flow_status,
            ).exists()
            query = query.where(flow_exists)
        now = _now()
        if sla_state == "overdue":
            query = query.where(OmnichannelThread.sla_due_at.is_not(None), OmnichannelThread.sla_due_at < now)
        elif sla_state == "on_time":
            query = query.where(OmnichannelThread.sla_due_at.is_not(None), OmnichannelThread.sla_due_at >= now)
        elif sla_state == "without_sla":
            query = query.where(OmnichannelThread.sla_due_at.is_(None))
        if last_message_after:
            query = query.where(OmnichannelThread.last_message_at >= last_message_after)
        if last_message_before:
            query = query.where(OmnichannelThread.last_message_at <= last_message_before)
        if search:
            like = f"%{search}%"
            search_fields = [
                OmnichannelThread.subject.ilike(like),
                OmnichannelThread.object_name.ilike(like),
                OmnichannelThread.last_message_preview.ilike(like),
                Matter.demand_type.ilike(like),
                Matter.health_object_name.ilike(like),
                Matter.health_insurer.ilike(like),
                Matter.lead_source.ilike(like),
            ]
            if include_sensitive_search:
                query = query.outerjoin(
                    Client,
                    and_(
                        Client.id == OmnichannelThread.client_id,
                        Client.workspace_id == workspace_id,
                        Client.deleted_at.is_(None),
                    ),
                )
                search_fields.extend([Client.name.ilike(like), Client.email.ilike(like), Client.phone.ilike(like)])
            query = query.where(or_(*search_fields))

        sort_columns = {
            "recent": OmnichannelThread.last_message_at,
            "score": OmnichannelThread.score,
            "priority": OmnichannelThread.priority,
            "unread": OmnichannelThread.unread_count,
            "sla": OmnichannelThread.sla_due_at,
        }
        sort_column = sort_columns.get(sort_by, OmnichannelThread.last_message_at)
        primary_order = sort_column.asc() if sort_direction == "asc" else sort_column.desc()
        result = await self.db.execute(
            query.order_by(
                primary_order,
                OmnichannelThread.updated_at.desc(),
            ).limit(limit)
        )
        return list(result.scalars().all())

    async def get_thread(self, workspace_id: UUID, thread_id: UUID) -> Optional[OmnichannelThread]:
        return await self.db.scalar(
            select(OmnichannelThread).where(
                OmnichannelThread.id == thread_id,
                OmnichannelThread.workspace_id == workspace_id,
            )
        )

    async def update_thread(
        self,
        workspace_id: UUID,
        thread_id: UUID,
        user_id: UUID,
        payload: OmnichannelThreadUpdate,
    ) -> Optional[OmnichannelThread]:
        thread = await self.db.scalar(
            select(OmnichannelThread)
            .where(
                OmnichannelThread.id == thread_id,
                OmnichannelThread.workspace_id == workspace_id,
            )
            .with_for_update()
        )
        if not thread:
            return None
        changes = payload.model_dump(exclude_unset=True)
        expected_version = changes.pop("expected_version", None)
        if expected_version is not None and expected_version != thread.version:
            raise ValueError(
                f"Conversa alterada por outro operador; versão atual: {thread.version}."
            )
        if changes.get("status") == ThreadStatus.human_active:
            changes.setdefault("assigned_human_id", user_id)
        elif changes.get("status") == ThreadStatus.open:
            changes.setdefault("assigned_human_id", None)
        assigned_human_id = changes.get("assigned_human_id")
        if assigned_human_id and not await self._workspace_member(workspace_id, assigned_human_id):
            raise LookupError("Humano não encontrado no workspace.")
        assigned_team_id = changes.get("assigned_team_id")
        if assigned_team_id:
            team = await self.db.scalar(
                select(WorkspaceTeam).where(
                    WorkspaceTeam.id == assigned_team_id,
                    WorkspaceTeam.workspace_id == workspace_id,
                    WorkspaceTeam.is_active.is_(True),
                )
            )
            if not team:
                raise LookupError("Equipe não encontrada no workspace.")
        if "owner_agent_key" in changes:
            metadata = dict(changes.get("thread_metadata") or thread.thread_metadata or {})
            if changes["owner_agent_key"]:
                metadata["manualOwnerAgentOverride"] = True
                metadata["manualOwnerAgentUpdatedAt"] = _now().isoformat()
                metadata["manualOwnerAgentUpdatedBy"] = str(user_id)
            else:
                metadata.pop("manualOwnerAgentOverride", None)
                metadata.pop("manualOwnerAgentUpdatedAt", None)
                metadata.pop("manualOwnerAgentUpdatedBy", None)
                route = metadata.get("legalRouting") or {}
                automatic_owner = metadata.get("autoOwnerAgentKey") or route.get("sdrAgentKey")
                if automatic_owner:
                    changes["owner_agent_key"] = automatic_owner
            changes["thread_metadata"] = metadata
        if changes.get("status") == ThreadStatus.closed:
            changes.setdefault("closed_at", _now())
        elif changes.get("status") in {ThreadStatus.open, ThreadStatus.human_active}:
            changes.setdefault("closed_at", None)
            changes.setdefault("closed_reason", None)
        before = {key: getattr(thread, key) for key in changes}
        for key, value in changes.items():
            setattr(thread, key, value)
        if changes:
            thread.version += 1
        thread.updated_at = _now()
        await self.db.flush()
        await self.record_audit(
            workspace_id=workspace_id,
            actor_user_id=user_id,
            actor_type="human",
            actor_label="Usuário KLM",
            event_type="omnichannel_thread_updated",
            entity_type="omnichannel_thread",
            entity_id=thread.id,
            summary="Thread omnichannel atualizada.",
            before_data={key: str(value) for key, value in before.items()},
            after_data={key: str(value) for key, value in changes.items()},
        )
        return thread

    async def list_messages(
        self, workspace_id: UUID, thread_id: UUID, limit: int = 200
    ) -> List[OmnichannelMessage]:
        if not await self.get_thread(workspace_id, thread_id):
            raise LookupError("Thread não encontrada no workspace.")
        result = await self.db.execute(
            select(OmnichannelMessage)
            .where(
                OmnichannelMessage.workspace_id == workspace_id,
                OmnichannelMessage.thread_id == thread_id,
            )
            .order_by(OmnichannelMessage.occurred_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_internal_message_feed(
        self, workspace_id: UUID, limit: int = 500
    ) -> List[Tuple[OmnichannelMessage, OmnichannelThread]]:
        result = await self.db.execute(
            select(OmnichannelMessage, OmnichannelThread)
            .join(
                OmnichannelThread,
                and_(
                    OmnichannelThread.id == OmnichannelMessage.thread_id,
                    OmnichannelThread.workspace_id == workspace_id,
                ),
            )
            .where(
                OmnichannelMessage.workspace_id == workspace_id,
                or_(
                    OmnichannelMessage.direction == MessageDirection.internal,
                    OmnichannelMessage.channel == OmnichannelChannel.internal,
                ),
            )
            .order_by(OmnichannelMessage.occurred_at.desc())
            .limit(limit)
        )
        return list(reversed(result.all()))

    async def _materialize_message_collaboration(
        self,
        *,
        workspace_id: UUID,
        thread_id: UUID,
        user_id: UUID | None,
        message: OmnichannelMessage,
        payload: OmnichannelMessageCreate,
    ) -> None:
        collaboration = payload.collaboration or normalize_message_collaboration(
            (payload.message_metadata or {}).get("collaboration")
        )
        if collaboration is None or (
            collaboration.reply_to_message_id is None and not collaboration.mentions
        ):
            return
        if user_id is None:
            raise ValueError(
                "Colaboração estruturada exige um usuário responsável do workspace."
            )

        from app.schemas.conversation_collaboration import (
            ConversationMentionCreate,
            ConversationMessageMentionsCreate,
            ConversationReferenceCreate,
        )
        from app.services.conversation_collaboration_service import (
            ConversationCollaborationService,
        )
        from app.services.notification_service import NotificationService

        mentions = [
            ConversationMentionCreate(
                mention_type=item.mention_type,
                target_id=item.target_id,
            )
            for item in collaboration.mentions
        ]
        collaboration_service = ConversationCollaborationService(self.db)
        if collaboration.reply_to_message_id is not None:
            idempotency_key = collaboration.idempotency_key or (
                f"spa-reference:{thread_id}:{message.id}"
            )
            result = await collaboration_service.create_reference(
                workspace_id,
                thread_id,
                user_id,
                ConversationReferenceCreate(
                    source_message_id=message.id,
                    target_message_id=collaboration.reply_to_message_id,
                    reference_type=collaboration.reference_type,
                    mentions=mentions,
                    idempotency_key=idempotency_key,
                ),
            )
            resolved_mentions = result.mentions
        else:
            idempotency_key = collaboration.idempotency_key or (
                f"spa-mentions:{thread_id}:{message.id}"
            )
            result = await collaboration_service.attach_mentions(
                workspace_id,
                thread_id,
                user_id,
                ConversationMessageMentionsCreate(
                    source_message_id=message.id,
                    mentions=mentions,
                    idempotency_key=idempotency_key,
                ),
            )
            resolved_mentions = result.mentions

        if resolved_mentions:
            await NotificationService(self.db).create_for_structured_mentions(
                workspace_id=workspace_id,
                thread_id=thread_id,
                source_message_id=message.id,
                matter_id=message.matter_id,
                actor_label=payload.author_label,
                mentions=resolved_mentions,
            )

    async def _ensure_scheduled_message(
        self,
        *,
        workspace_id: UUID,
        thread_id: UUID,
        user_id: UUID | None,
        message: OmnichannelMessage,
        payload: OmnichannelMessageCreate,
    ) -> None:
        if payload.scheduled_at is None:
            return

        from app.schemas.omnichannel_v6 import ScheduledMessageCreate
        from app.services.omnichannel_v6_service import OmnichannelV6Service

        endpoint_id = message.endpoint_id
        connection_key = str((payload.message_metadata or {}).get("connectionKey") or "").strip()
        if message.binding_id is not None and (endpoint_id is None or not connection_key):
            binding = await self.db.scalar(
                select(OmnichannelThreadBinding).where(
                    OmnichannelThreadBinding.workspace_id == workspace_id,
                    OmnichannelThreadBinding.thread_id == thread_id,
                    OmnichannelThreadBinding.id == message.binding_id,
                )
            )
            if binding is not None:
                endpoint_id = endpoint_id or binding.endpoint_id
                connection_key = connection_key or binding.connection_key
        if message.connection_id is not None and not connection_key:
            connection_key = str(
                await self.db.scalar(
                    select(OmnichannelChannelConnection.connection_key).where(
                        OmnichannelChannelConnection.workspace_id == workspace_id,
                        OmnichannelChannelConnection.id == message.connection_id,
                    )
                )
                or ""
            ).strip()
        if endpoint_id is None or not connection_key:
            raise ValueError(
                "A mensagem agendada exige endpoint e conexão canônica da conversa."
            )

        metadata = dict(payload.message_metadata or {})
        structured_content = metadata.get("structuredContent")
        message_payload = (
            dict(structured_content)
            if isinstance(structured_content, dict) and structured_content
            else {"kind": "text", "body": payload.body}
        )
        collaboration = metadata.get("collaboration")
        if isinstance(collaboration, dict):
            message_payload["collaboration"] = collaboration
        attachments = metadata.get("attachments")
        attachments = list(attachments) if isinstance(attachments, list) else []
        schedule, _ = await OmnichannelV6Service(self.db).create_scheduled_message(
            workspace_id,
            thread_id,
            ScheduledMessageCreate(
                endpoint_id=endpoint_id,
                connection_key=connection_key,
                channel=payload.channel,
                body=payload.body,
                template_id=payload.template_id,
                attachments=attachments,
                message_payload=message_payload,
                collaboration=payload.collaboration,
                scheduled_at=payload.scheduled_at,
                timezone=payload.timezone,
                idempotency_key=f"scheduled:{payload.idempotency_key}",
            ),
            user_id=user_id,
            author_type=payload.author_type.value,
            author_label=payload.author_label,
        )
        if schedule.canonical_message_id not in {None, message.id}:
            raise ValueError(
                "O agendamento idempotente já está ligado a outra mensagem canônica."
            )
        schedule.canonical_message_id = message.id
        receipt = dict(schedule.provider_receipt or {})
        receipt.update(
            {
                "canonicalMessageId": str(message.id),
                "collaborationValidated": payload.collaboration is not None,
            }
        )
        schedule.provider_receipt = receipt
        await self.db.flush()

    async def _finalize_message_extensions(
        self,
        *,
        workspace_id: UUID,
        thread_id: UUID,
        user_id: UUID | None,
        message: OmnichannelMessage,
        payload: OmnichannelMessageCreate,
    ) -> None:
        await self._materialize_message_collaboration(
            workspace_id=workspace_id,
            thread_id=thread_id,
            user_id=user_id,
            message=message,
            payload=payload,
        )
        await self._ensure_scheduled_message(
            workspace_id=workspace_id,
            thread_id=thread_id,
            user_id=user_id,
            message=message,
            payload=payload,
        )

    async def resolve_provider_reply_context(
        self,
        *,
        workspace_id: UUID,
        thread_id: UUID,
        source_message_id: UUID,
        provider_message_id: str,
        connection_id: UUID | None,
        correlation_id: str,
    ) -> ConversationMessageReference | None:
        """Resolve a provider reply only inside its canonical tenant/thread/route."""

        source = await self.db.scalar(
            select(OmnichannelMessage).where(
                OmnichannelMessage.workspace_id == workspace_id,
                OmnichannelMessage.thread_id == thread_id,
                OmnichannelMessage.id == source_message_id,
                OmnichannelMessage.channel == OmnichannelChannel.whatsapp,
            )
        )
        if source is None:
            raise LookupError("Mensagem canônica de origem não encontrada no workspace.")
        effective_connection_id = source.connection_id or connection_id
        target_filters = [
            OmnichannelMessage.workspace_id == workspace_id,
            OmnichannelMessage.thread_id == thread_id,
            OmnichannelMessage.channel == OmnichannelChannel.whatsapp,
            OmnichannelMessage.external_message_id == provider_message_id,
            OmnichannelMessage.id != source_message_id,
        ]
        if effective_connection_id is not None:
            target_filters.append(
                OmnichannelMessage.connection_id == effective_connection_id
            )
        targets = list(
            (
                await self.db.execute(
                    select(OmnichannelMessage).where(*target_filters).limit(2)
                )
            ).scalars()
        )
        if len(targets) != 1:
            return None
        target = targets[0]

        from app.db.advisory import acquire_transaction_advisory_lock

        idempotency_key = "wa-context:" + hashlib.sha256(
            f"{workspace_id}:{correlation_id}".encode()
        ).hexdigest()
        await acquire_transaction_advisory_lock(
            self.db,
            namespace="whatsapp-provider-context",
            value=f"{workspace_id}:{idempotency_key}",
        )
        canonical_payload = {
            "threadId": str(thread_id),
            "sourceMessageId": str(source.id),
            "targetMessageId": str(target.id),
            "referenceType": ConversationReferenceType.reply.value,
            "mentions": [],
        }
        payload_sha256 = hashlib.sha256(
            json.dumps(
                canonical_payload,
                sort_keys=True,
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        existing = await self.db.scalar(
            select(ConversationMessageReference).where(
                ConversationMessageReference.workspace_id == workspace_id,
                ConversationMessageReference.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            if (
                existing.thread_id != thread_id
                or existing.source_message_id != source.id
                or existing.target_message_id != target.id
                or existing.payload_sha256 != payload_sha256
            ):
                raise ValueError(
                    "O contexto oficial já foi associado a outra referência canônica."
                )
            return existing

        reference = ConversationMessageReference(
            workspace_id=workspace_id,
            thread_id=thread_id,
            source_message_id=source.id,
            target_message_id=target.id,
            reference_type=ConversationReferenceType.reply,
            idempotency_key=idempotency_key,
            payload_sha256=payload_sha256,
            created_by_user_id=None,
        )
        self.db.add(reference)
        await self.db.flush()
        await self.record_audit(
            workspace_id=workspace_id,
            actor_user_id=None,
            actor_type="system",
            actor_label="Webhook oficial do WhatsApp",
            event_type="conversation_provider_reply_resolved",
            entity_type="conversation_message_reference",
            entity_id=reference.id,
            summary="Contexto oficial do WhatsApp ligado à mesma conversa canônica.",
            correlation_id=idempotency_key,
            after_data={
                "threadId": str(thread_id),
                "sourceMessageId": str(source.id),
                "targetMessageId": str(target.id),
                "providerMessageIdSha256": hashlib.sha256(
                    provider_message_id.encode()
                ).hexdigest(),
            },
        )
        return reference

    async def create_message(
        self,
        workspace_id: UUID,
        thread_id: UUID,
        user_id: UUID | None,
        payload: OmnichannelMessageCreate,
        *,
        trusted_external_receipt: bool = False,
    ) -> tuple[OmnichannelMessage, bool]:
        """Persist the canonical message and every collaboration extension atomically."""

        async with self.db.begin_nested():
            return await self._create_message_transactional(
                workspace_id,
                thread_id,
                user_id,
                payload,
                trusted_external_receipt=trusted_external_receipt,
            )

    async def _create_message_transactional(
        self,
        workspace_id: UUID,
        thread_id: UUID,
        user_id: UUID | None,
        payload: OmnichannelMessageCreate,
        *,
        trusted_external_receipt: bool = False,
    ) -> tuple[OmnichannelMessage, bool]:
        thread = await self.db.scalar(
            select(OmnichannelThread)
            .where(
                OmnichannelThread.id == thread_id,
                OmnichannelThread.workspace_id == workspace_id,
            )
            .with_for_update()
        )
        if not thread:
            raise LookupError("Thread não encontrada no workspace.")
        # A rollback de savepoint após uma corrida idempotente expira a
        # instância ORM no SQLite. Preserve os identificadores escalares para
        # que o caminho vencedor não provoque lazy-load síncrono em AsyncSession.
        locked_thread_id = thread.id
        locked_matter_id = thread.matter_id
        endpoint: OmnichannelEndpoint | None = None
        binding: OmnichannelThreadBinding | None = None
        if payload.endpoint_id:
            endpoint = await self.db.scalar(
                select(OmnichannelEndpoint).where(
                    OmnichannelEndpoint.id == payload.endpoint_id,
                    OmnichannelEndpoint.workspace_id == workspace_id,
                )
            )
            if not endpoint:
                raise LookupError("Endpoint não encontrado no workspace.")
            if thread.identity_id and endpoint.identity_id != thread.identity_id:
                raise ValueError("Endpoint não pertence à identidade da conversa.")
            if endpoint.channel != payload.channel.value:
                raise ValueError("Canal da mensagem diverge do endpoint selecionado.")
        if payload.binding_id:
            binding = await self.db.scalar(
                select(OmnichannelThreadBinding).where(
                    OmnichannelThreadBinding.id == payload.binding_id,
                    OmnichannelThreadBinding.workspace_id == workspace_id,
                    OmnichannelThreadBinding.thread_id == locked_thread_id,
                )
            )
            if not binding:
                raise LookupError("Vínculo de canal não pertence à conversa.")
            if payload.endpoint_id and binding.endpoint_id not in {None, payload.endpoint_id}:
                raise ValueError("Endpoint diverge do vínculo de canal.")
            if endpoint is None and binding.endpoint_id:
                endpoint = await self.db.scalar(
                    select(OmnichannelEndpoint).where(
                        OmnichannelEndpoint.id == binding.endpoint_id,
                        OmnichannelEndpoint.workspace_id == workspace_id,
                    )
                )
                if endpoint is None:
                    raise LookupError("Endpoint do vínculo não foi encontrado no workspace.")
                if thread.identity_id and endpoint.identity_id != thread.identity_id:
                    raise ValueError("Endpoint do vínculo não pertence à identidade da conversa.")
                if endpoint.channel != payload.channel.value:
                    raise ValueError("Canal da mensagem diverge do vínculo selecionado.")
            if endpoint and binding.connection_key != endpoint.connection_key:
                raise ValueError("Conexão do endpoint diverge do vínculo de canal.")

        route_connection_ids = {
            connection_id
            for connection_id in (
                endpoint.connection_id if endpoint else None,
                binding.connection_id if binding else None,
            )
            if connection_id is not None
        }
        if len(route_connection_ids) > 1:
            raise ValueError("Endpoint e vínculo apontam para conexões diferentes.")
        route_connection = None
        if route_connection_ids:
            route_connection_id = next(iter(route_connection_ids))
            route_connection = await self.db.scalar(
                select(OmnichannelChannelConnection).where(
                    OmnichannelChannelConnection.id == route_connection_id,
                    OmnichannelChannelConnection.workspace_id == workspace_id,
                )
            )
            if route_connection is None:
                raise LookupError("Conexão da rota não foi encontrada no workspace.")
            if route_connection.channel != payload.channel.value:
                raise ValueError("Canal da mensagem diverge da conexão selecionada.")
            if endpoint and route_connection.connection_key != endpoint.connection_key:
                raise ValueError("Conexão selecionada diverge do endpoint.")
            if binding and route_connection.connection_key != binding.connection_key:
                raise ValueError("Conexão selecionada diverge do vínculo.")
            if (
                payload.direction == MessageDirection.outbound
                and not trusted_external_receipt
                and payload.transport not in {"local", route_connection.transport_mode}
            ):
                raise ValueError("Transporte da mensagem diverge da conexão selecionada.")
            _require_rich_message_capability(route_connection, payload)

        requested_connection_key = (payload.message_metadata or {}).get("connectionKey")
        if requested_connection_key:
            expected_connection_key = (
                route_connection.connection_key
                if route_connection
                else binding.connection_key
                if binding
                else endpoint.connection_key
                if endpoint
                else None
            )
            if expected_connection_key and requested_connection_key != expected_connection_key:
                raise ValueError("connectionKey diverge da rota selecionada.")
        requested_connection_id = (payload.message_metadata or {}).get("connectionId")
        if requested_connection_id and route_connection_ids:
            if str(next(iter(route_connection_ids))) != str(requested_connection_id):
                raise ValueError("connectionId diverge da rota selecionada.")
        if trusted_external_receipt and route_connection is not None:
            payload = payload.model_copy(update={"transport": route_connection.transport_mode})
        author_user_id = (
            user_id
            if payload.author_type in {MessageAuthorType.human, MessageAuthorType.agent}
            else None
        )
        existing = await self.db.scalar(
            select(OmnichannelMessage).where(
                OmnichannelMessage.workspace_id == workspace_id,
                OmnichannelMessage.idempotency_key == payload.idempotency_key,
            )
        )
        if existing:
            _require_matching_message_replay(
                existing,
                thread_id=locked_thread_id,
                author_user_id=author_user_id,
                payload=payload,
            )
            if existing.matter_id is not None:
                await KnowledgeService(self.db).index_omnichannel_message(message=existing)
            if existing.evidence_event_count == 0:
                await MessageEvidenceService(self.db).record_projection_capture(existing)
            await self._finalize_message_extensions(
                workspace_id=workspace_id,
                thread_id=locked_thread_id,
                user_id=user_id,
                message=existing,
                payload=payload,
            )
            return existing, True

        values = payload.model_dump(exclude={"delivery_status", "occurred_at"})
        if (
            payload.direction == MessageDirection.outbound
            and payload.author_type == MessageAuthorType.agent
        ):
            cadence_service = MessagingCadenceService(self.db)
            cadence_settings = await cadence_service.get_settings(workspace_id)
            thread_flags = thread.thread_metadata or {}
            if cadence_settings.stop_on_block_or_opt_out and (
                thread.status == ThreadStatus.blocked
                or bool(thread_flags.get("blockedByContact"))
                or bool(thread_flags.get("optedOut"))
                or bool(thread_flags.get("suppressed"))
            ):
                raise ValueError(
                    "Mensagem automatizada bloqueada: o contato bloqueou, recusou ou está suprimido."
                )
            last_inbound_at = await self.db.scalar(
                select(func.max(OmnichannelMessage.occurred_at)).where(
                    OmnichannelMessage.workspace_id == workspace_id,
                    OmnichannelMessage.thread_id == locked_thread_id,
                    OmnichannelMessage.direction == MessageDirection.inbound,
                )
            )
            consecutive_filters = [
                OmnichannelMessage.workspace_id == workspace_id,
                OmnichannelMessage.thread_id == locked_thread_id,
                OmnichannelMessage.direction == MessageDirection.outbound,
                OmnichannelMessage.author_type == MessageAuthorType.agent,
            ]
            if last_inbound_at is not None:
                consecutive_filters.append(OmnichannelMessage.occurred_at > last_inbound_at)
            consecutive_count = int(
                await self.db.scalar(
                    select(func.count(OmnichannelMessage.id)).where(*consecutive_filters)
                )
                or 0
            )
            if consecutive_count >= cadence_settings.max_consecutive_without_reply:
                raise ValueError(
                    "Mensagem automatizada bloqueada: limite sem resposta atingido; aguarde o contato ou revisão humana."
                )
            cadence_plan = await cadence_service.plan(
                workspace_id,
                text=payload.body,
                idempotency_key=payload.idempotency_key,
            )
            message_metadata = dict(values.pop("message_metadata") or {})
            message_metadata["deliveryCadence"] = cadence_plan.model_dump(
                mode="json",
                by_alias=True,
            )
            message_metadata["deliveryCadence"]["consecutiveWithoutReply"] = consecutive_count
            message_metadata["externalDispatcherMustHonorNotBefore"] = True
            values["message_metadata"] = message_metadata
        if trusted_external_receipt:
            delivery = payload.delivery_status or MessageDeliveryStatus.received
        elif payload.direction == MessageDirection.inbound:
            delivery = MessageDeliveryStatus.received
        elif payload.direction == MessageDirection.internal:
            delivery = MessageDeliveryStatus.received
        elif payload.scheduled_at is not None:
            delivery = MessageDeliveryStatus.queued
        elif (
            external_delivery_allowed()
            and payload.channel == OmnichannelChannel.whatsapp
            and endpoint is not None
            and route_connection is not None
            and route_connection.provider == "meta_cloud"
            and route_connection.transport_mode
            in {"cloud_api", "official_api", "coexistence"}
        ):
            delivery = payload.delivery_status or MessageDeliveryStatus.queued
        else:
            delivery = MessageDeliveryStatus.local_draft
        message = OmnichannelMessage(
            workspace_id=workspace_id,
            thread_id=locked_thread_id,
            matter_id=locked_matter_id,
            connection_id=(route_connection.id if route_connection else None),
            provider=(
                route_connection.provider
                if route_connection
                else str((payload.message_metadata or {}).get("provider") or "") or None
            ),
            author_user_id=author_user_id,
            occurred_at=payload.occurred_at or _now(),
            delivery_status=delivery,
            **values,
        )
        try:
            async with self.db.begin_nested():
                self.db.add(message)
                if thread.last_message_at is None or _normalized_moment(
                    message.occurred_at
                ) >= _normalized_moment(thread.last_message_at):
                    thread.last_message_preview = payload.body[:500]
                    thread.last_message_at = message.occurred_at
                if payload.direction == MessageDirection.inbound:
                    thread.unread_count += 1
                    if thread.last_inbound_at is None or _normalized_moment(
                        message.occurred_at
                    ) >= _normalized_moment(thread.last_inbound_at):
                        thread.last_inbound_at = message.occurred_at
                        thread.preferred_channel = payload.channel.value
                elif payload.direction == MessageDirection.outbound:
                    if thread.last_outbound_at is None or _normalized_moment(
                        message.occurred_at
                    ) >= _normalized_moment(thread.last_outbound_at):
                        thread.last_outbound_at = message.occurred_at
                thread.cost_state = _updated_thread_cost_state(
                    thread.cost_state,
                    payload=payload,
                    delivery=delivery,
                    connection_key=(
                        route_connection.connection_key
                        if route_connection
                        else binding.connection_key
                        if binding
                        else endpoint.connection_key
                        if endpoint
                        else requested_connection_key
                    ),
                )
                thread.updated_at = _now()
                await self.db.flush()
        except IntegrityError:
            existing = await self.db.scalar(
                select(OmnichannelMessage).where(
                    OmnichannelMessage.workspace_id == workspace_id,
                    OmnichannelMessage.idempotency_key == payload.idempotency_key,
                )
            )
            if existing:
                _require_matching_message_replay(
                    existing,
                    thread_id=locked_thread_id,
                    author_user_id=author_user_id,
                    payload=payload,
                )
                if existing.matter_id is not None:
                    await KnowledgeService(self.db).index_omnichannel_message(message=existing)
                if existing.evidence_event_count == 0:
                    await MessageEvidenceService(self.db).record_projection_capture(existing)
                await self._finalize_message_extensions(
                    workspace_id=workspace_id,
                    thread_id=locked_thread_id,
                    user_id=user_id,
                    message=existing,
                    payload=payload,
                )
                return existing, True
            raise

        await MessageEvidenceService(self.db).record_projection_capture(message)

        if message.matter_id is not None:
            await KnowledgeService(self.db).index_omnichannel_message(message=message)

        if (
            payload.direction == MessageDirection.outbound
            and not trusted_external_receipt
            and payload.scheduled_at is None
            and not external_delivery_allowed()
        ):
            intent = ExternalActionIntent(
                workspace_id=workspace_id,
                requested_by_user_id=user_id,
                matter_id=locked_matter_id,
                action_type="send_message",
                provider=payload.channel.value,
                idempotency_key=f"message:{payload.idempotency_key}",
                status=ExternalActionIntentStatus.blocked_external,
                payload={
                    "threadId": str(locked_thread_id),
                    "messageId": str(message.id),
                    "channel": payload.channel.value,
                    "endpointId": str(payload.endpoint_id) if payload.endpoint_id else None,
                    "bindingId": str(payload.binding_id) if payload.binding_id else None,
                    "connectionKey": (payload.message_metadata or {}).get("connectionKey"),
                    "transport": payload.transport,
                },
            )
            self.db.add(intent)

        audit_actor_type = payload.author_type.value
        audit_actor_label = payload.author_label
        if trusted_external_receipt and message.author_user_id is None:
            source = str((message.message_metadata or {}).get("source") or "").lower()
            audit_actor_type = "system"
            audit_actor_label = {
                "chatwoot_webhook": "Chatwoot bridge",
                "rocketchat_webhook": "Rocket.Chat bridge",
            }.get(source, "Integração externa")

        await self.record_audit(
            workspace_id=workspace_id,
            actor_user_id=message.author_user_id,
            actor_type=audit_actor_type,
            actor_label=audit_actor_label,
            event_type="omnichannel_message_recorded",
            entity_type="omnichannel_message",
            entity_id=message.id,
            summary=(
                "Mensagem recebida e registrada."
                if payload.direction == MessageDirection.inbound
                else "Mensagem de saída salva localmente."
            ),
            correlation_id=payload.idempotency_key,
            after_data={"channel": payload.channel.value, "status": delivery.value},
        )
        await self._finalize_message_extensions(
            workspace_id=workspace_id,
            thread_id=locked_thread_id,
            user_id=user_id,
            message=message,
            payload=payload,
        )
        return message, False

    async def ingest_email(
        self,
        workspace_id: UUID,
        user_id: UUID,
        payload: EmailIngestRequest,
    ) -> Dict[str, Any]:
        receipt, owns_processing = await self._claim_email_ingest(
            workspace_id,
            payload,
        )
        if not owns_processing:
            return await self._replay_email_ingest(workspace_id, receipt)

        idem_key = f"email:{payload.message_id}"
        legacy_message = await self.db.scalar(
            select(OmnichannelMessage).where(
                OmnichannelMessage.workspace_id == workspace_id,
                OmnichannelMessage.idempotency_key == idem_key,
            )
        )
        if legacy_message:
            _require_matching_legacy_email_message(legacy_message, payload)
            legacy_thread = await self.get_thread(workspace_id, legacy_message.thread_id)
            if not legacy_thread or not legacy_thread.client_id or not legacy_thread.matter_id:
                raise RuntimeError("Mensagem de e-mail legada não possui card e cliente íntegros.")
            if payload.client_id and payload.client_id != legacy_thread.client_id:
                raise ValueError("Cliente informado diverge do e-mail já ingerido.")
            if payload.matter_id and payload.matter_id != legacy_thread.matter_id:
                raise ValueError("Caso informado diverge do e-mail já ingerido.")
            metadata = legacy_message.message_metadata or {}
            receipt.state = "completed"
            receipt.completed_at = _now()
            receipt.result = {
                "threadId": str(legacy_thread.id),
                "messageId": str(legacy_message.id),
                "clientId": str(legacy_thread.client_id),
                "matterId": str(legacy_thread.matter_id),
                "documentIds": list(metadata.get("documentIds", [])),
                "knowledgeChunkIds": list(metadata.get("knowledgeChunkIds", [])),
            }
            await self.db.flush()
            return await self._replay_email_ingest(workspace_id, receipt)

        client: Optional[Client] = None
        if payload.client_id:
            client = await self._client(workspace_id, payload.client_id)
            if not client:
                raise LookupError("Cliente não encontrado no workspace.")
        if not client:
            client = await self.db.scalar(
                select(Client).where(
                    Client.workspace_id == workspace_id,
                    Client.deleted_at.is_(None),
                    func.lower(Client.email) == payload.sender_email.lower(),
                )
            )
        if not client:
            client = Client(
                workspace_id=workspace_id,
                name=payload.sender_name or payload.sender_email.split("@")[0],
                email=payload.sender_email.lower(),
            )
            self.db.add(client)
            await self.db.flush()

        matter: Optional[Matter] = None
        if payload.matter_id:
            matter = await self._matter(workspace_id, payload.matter_id)
            if not matter:
                raise LookupError("Caso não encontrado no workspace.")
        if not matter:
            matter = await self.db.scalar(
                select(Matter)
                .where(
                    Matter.workspace_id == workspace_id,
                    Matter.client_id == client.id,
                    Matter.deleted_at.is_(None),
                )
                .order_by(Matter.updated_at.desc())
                .limit(1)
            )
        if matter and matter.client_id != client.id:
            raise ValueError("Cliente do e-mail não pertence ao caso informado.")
        if not matter:
            routing_text = " | ".join(
                value.strip()
                for value in (
                    payload.subject,
                    payload.body,
                    *(attachment.name for attachment in payload.attachments),
                    *(
                        attachment.content_text or ""
                        for attachment in payload.attachments
                    ),
                )
                if value and value.strip()
            )
            inferred_area = infer_area(routing_text)
            legal_area_key = (
                str(inferred_area["areaKey"]) if inferred_area else "triagem_geral"
            )
            inferred_product = infer_product(legal_area_key, routing_text)
            legal_product_key = (
                str(inferred_product["key"]) if inferred_product else None
            )
            try:
                practice_area = PracticeArea(legal_area_key)
            except ValueError:
                practice_area = PracticeArea.civel
            health_subtheme = None
            health_subtheme_custom = None
            if legal_area_key == "saude_suplementar" and legal_product_key:
                try:
                    health_subtheme = HealthSubtheme(legal_product_key)
                except ValueError:
                    health_subtheme_custom = str(
                        inferred_product.get("label") if inferred_product else legal_product_key
                    )
            matter = Matter(
                workspace_id=workspace_id,
                client_id=client.id,
                practice_area=practice_area,
                legal_area_key=legal_area_key,
                legal_product_key=legal_product_key,
                legal_issue_summary=payload.body.strip() or None,
                health_subtheme=health_subtheme,
                health_subtheme_custom=health_subtheme_custom,
                demand_type=payload.subject,
                lead_source="email_inbound",
                column_id=KanbanColumnId.novo_lead,
                micro_status=MicroStatus.lead_novo,
                next_action="Triagem inicial do e-mail e dos documentos.",
            )
            self.db.add(matter)
            await self.db.flush()

        from app.schemas.omnichannel_v6 import CanonicalChannelEventIngest
        from app.services.omnichannel_v6_service import OmnichannelV6Service

        resolution, _channel_event, message, _event_deduplicated = await OmnichannelV6Service(
            self.db
        ).ingest_event(
            workspace_id,
            CanonicalChannelEventIngest(
                provider="email_gateway",
                connection_key="email:default",
                channel=OmnichannelChannel.email,
                event_type="message.received",
                external_event_id=payload.message_id,
                external_thread_id=f"email-contact:{payload.sender_email.lower()}",
                external_user_id=payload.sender_email.lower(),
                display_name=payload.sender_name or payload.sender_email,
                address=payload.sender_email.lower(),
                canonical_email=payload.sender_email.lower(),
                client_id=client.id,
                matter_id=matter.id,
                direction=MessageDirection.inbound,
                author_type=MessageAuthorType.client,
                author_label=payload.sender_name or payload.sender_email,
                body=payload.body,
                content_type="email",
                visibility="client_private",
                actionability="reply_required",
                occurred_at=payload.received_at,
                correlation={"subject": payload.subject},
                payload={
                    "attachments": [
                        {
                            "name": attachment.name,
                            "mimeType": attachment.mime_type,
                            "documentType": attachment.document_type,
                        }
                        for attachment in payload.attachments
                    ]
                },
                metadata={
                    "senderEmail": payload.sender_email.lower(),
                    "subject": payload.subject,
                    "source": "native_email_ingest",
                },
            ),
        )
        if message is None:
            raise RuntimeError("Evento de e-mail não produziu uma mensagem canônica.")
        thread = resolution.thread
        thread.subject = payload.subject
        route = _matter_legal_routing(matter)
        thread.object_name = (
            thread.object_name or matter.legal_object_name or matter.health_object_name
        )
        metadata = dict(thread.thread_metadata or {})
        metadata["legalRouting"] = {
            **route,
            "source": "email_ingest",
            "resolvedAt": _now().isoformat(),
            "manualContextRevision": int(matter.manual_context_revision or 0),
        }
        metadata["autoOwnerAgentKey"] = route["sdrAgentKey"]
        thread.thread_metadata = metadata
        thread.owner_agent_key = thread.owner_agent_key or route["sdrAgentKey"]
        thread.sla_due_at = thread.sla_due_at or (_now() + timedelta(minutes=15))
        message.message_metadata = {
            **(message.message_metadata or {}),
            "senderEmail": payload.sender_email.lower(),
            "subject": payload.subject,
        }

        document_ids: List[UUID] = []
        knowledge_ids: List[UUID] = []
        attachment_states: List[Dict[str, Any]] = []
        knowledge = KnowledgeService(self.db)
        for index, attachment in enumerate(payload.attachments):
            try:
                doc_type = DocumentType(attachment.document_type)
            except ValueError:
                doc_type = DocumentType.outro
            attachment_ref = f"{message.id}:attachment:{index}"
            content_text_provided = bool(
                attachment.content_text and attachment.content_text.strip()
            )
            document = Document(
                matter_id=matter.id,
                created_by=user_id,
                name=attachment.name,
                doc_type=doc_type,
                status=DocumentStatus.aguardando,
                s3_key=None,
                s3_url=None,
                file_size=None,
                mime_type=attachment.mime_type,
            )
            self.db.add(document)
            await self.db.flush()
            document_ids.append(document.id)
            attachment_state = {
                "attachmentRef": attachment_ref,
                "documentId": str(document.id),
                "name": attachment.name,
                "mimeType": attachment.mime_type,
                "documentType": doc_type.value,
                "storageState": "awaiting_binary",
                "binaryProvided": False,
                "contentTextProvided": content_text_provided,
            }
            attachment_states.append(attachment_state)
            content = (
                attachment.content_text.strip()
                if content_text_provided and attachment.content_text
                else (
                    f"Anexo '{attachment.name}' recebido por e-mail; o arquivo binário e "
                    "o texto extraído ainda precisam ser recuperados da origem."
                )
            )
            chunk = await knowledge.upsert_chunk(
                workspace_id=workspace_id,
                matter_id=matter.id,
                source_type="email_attachment",
                source_ref=attachment_ref,
                title=attachment.name,
                content_text=content,
                summary=(
                    f"Texto recebido do anexo '{attachment.name}'; binário pendente."
                    if content_text_provided
                    else f"Referência do anexo '{attachment.name}'; binário pendente."
                ),
                tags=["email", "documento", doc_type.value],
                metadata={
                    "messageId": str(message.id),
                    **attachment_state,
                },
                rank_hint=4,
                created_by_user_id=user_id,
            )
            knowledge_ids.append(chunk.id)

        message.message_metadata = {
            **(message.message_metadata or {}),
            "documentIds": [str(item) for item in document_ids],
            "knowledgeChunkIds": [str(item) for item in knowledge_ids],
            "attachments": attachment_states,
        }
        emails = set((thread.thread_metadata or {}).get("emails", []))
        emails.add(payload.sender_email.lower())
        thread.thread_metadata = {**(thread.thread_metadata or {}), "emails": sorted(emails)}
        thread.updated_at = _now()

        pending_follow_up = await self.db.scalar(
            select(FollowUpTask.id).where(
                FollowUpTask.workspace_id == workspace_id,
                FollowUpTask.thread_id == thread.id,
                FollowUpTask.status.in_([FollowUpStatus.scheduled, FollowUpStatus.due]),
            )
        )
        if not pending_follow_up:
            self.db.add(
                FollowUpTask(
                    workspace_id=workspace_id,
                    matter_id=matter.id,
                    thread_id=thread.id,
                    channel=OmnichannelChannel.email,
                    due_at=_now() + timedelta(minutes=15),
                    reason="Responder o lead e confirmar documentos recebidos.",
                )
            )
        receipt.state = "completed"
        receipt.completed_at = _now()
        receipt.result = {
            "threadId": str(thread.id),
            "messageId": str(message.id),
            "clientId": str(client.id),
            "matterId": str(matter.id),
            "documentIds": [str(item) for item in document_ids],
            "knowledgeChunkIds": [str(item) for item in knowledge_ids],
        }
        await self.db.flush()
        return {
            "thread": thread,
            "message": message,
            "client_id": client.id,
            "matter_id": matter.id,
            "document_ids": document_ids,
            "knowledge_chunk_ids": knowledge_ids,
            "deduplicated": False,
        }

    async def _claim_email_ingest(
        self,
        workspace_id: UUID,
        payload: EmailIngestRequest,
    ) -> tuple[EmailIngestReceipt, bool]:
        payload_hash = _email_ingest_payload_hash(payload)
        existing = await self.db.scalar(
            select(EmailIngestReceipt).where(
                EmailIngestReceipt.workspace_id == workspace_id,
                EmailIngestReceipt.provider_message_id == payload.message_id,
            )
        )
        if existing:
            self._require_matching_email_receipt(existing, payload_hash)
            return existing, False

        receipt = EmailIngestReceipt(
            workspace_id=workspace_id,
            provider_message_id=payload.message_id,
            payload_hash=payload_hash,
            state="processing",
            result={},
        )
        try:
            async with self.db.begin_nested():
                self.db.add(receipt)
                await self.db.flush()
        except IntegrityError:
            existing = await self.db.scalar(
                select(EmailIngestReceipt)
                .where(
                    EmailIngestReceipt.workspace_id == workspace_id,
                    EmailIngestReceipt.provider_message_id == payload.message_id,
                )
                .execution_options(populate_existing=True)
            )
            if not existing:
                raise
            self._require_matching_email_receipt(existing, payload_hash)
            return existing, False
        return receipt, True

    @staticmethod
    def _require_matching_email_receipt(
        receipt: EmailIngestReceipt,
        payload_hash: str,
    ) -> None:
        if not hmac.compare_digest(receipt.payload_hash, payload_hash):
            raise ValueError("messageId de e-mail já utilizado com conteúdo diferente.")

    async def _replay_email_ingest(
        self,
        workspace_id: UUID,
        receipt: EmailIngestReceipt,
    ) -> Dict[str, Any]:
        result = receipt.result or {}
        if receipt.state != "completed" or not result:
            raise ValueError("A entrega deste e-mail ainda está em processamento; tente novamente.")
        thread_id = UUID(result["threadId"])
        message_id = UUID(result["messageId"])
        thread = await self.get_thread(workspace_id, thread_id)
        message = await self.db.scalar(
            select(OmnichannelMessage).where(
                OmnichannelMessage.workspace_id == workspace_id,
                OmnichannelMessage.id == message_id,
            )
        )
        if not thread or not message:
            raise RuntimeError("Recibo de e-mail concluído aponta para dados ausentes.")
        return {
            "thread": thread,
            "message": message,
            "client_id": UUID(result["clientId"]),
            "matter_id": UUID(result["matterId"]),
            "document_ids": [UUID(item) for item in result.get("documentIds", [])],
            "knowledge_chunk_ids": [UUID(item) for item in result.get("knowledgeChunkIds", [])],
            "deduplicated": True,
        }

    async def schedule_follow_up(
        self, workspace_id: UUID, user_id: UUID, payload: FollowUpCreate
    ) -> FollowUpTask:
        if not payload.thread_id and not payload.matter_id:
            raise ValueError("Informe thread_id ou matter_id.")
        thread = (
            await self.get_thread(workspace_id, payload.thread_id) if payload.thread_id else None
        )
        matter = await self._matter(workspace_id, payload.matter_id) if payload.matter_id else None
        if payload.thread_id and not thread:
            raise LookupError("Thread não encontrada no workspace.")
        if payload.matter_id and not matter:
            raise LookupError("Caso não encontrado no workspace.")
        if thread and matter and thread.matter_id and thread.matter_id != matter.id:
            raise ValueError("Thread não pertence ao caso informado.")
        task = FollowUpTask(workspace_id=workspace_id, **payload.model_dump())
        self.db.add(task)
        await self.db.flush()
        await self.record_audit(
            workspace_id=workspace_id,
            actor_user_id=user_id,
            actor_type="human",
            actor_label="Usuário KLM",
            event_type="follow_up_scheduled",
            entity_type="follow_up",
            entity_id=task.id,
            summary="Follow-up programado localmente.",
        )
        return task

    async def update_follow_up(
        self,
        workspace_id: UUID,
        follow_up_id: UUID,
        user_id: UUID,
        payload: FollowUpUpdate,
    ) -> FollowUpTask:
        task = await self.db.scalar(
            select(FollowUpTask)
            .where(
                FollowUpTask.workspace_id == workspace_id,
                FollowUpTask.id == follow_up_id,
            )
            .with_for_update()
        )
        if task is None:
            raise LookupError("Follow-up não encontrado no workspace.")
        if task.status not in {FollowUpStatus.scheduled, FollowUpStatus.due}:
            raise ValueError(
                "Somente follow-ups agendados ou vencidos podem ser editados."
            )

        changes = payload.model_dump(exclude_unset=True)
        target_status = changes.pop("status", None)
        if target_status not in {
            None,
            FollowUpStatus.scheduled,
            FollowUpStatus.completed,
            FollowUpStatus.cancelled,
        }:
            raise ValueError(
                "O status informado não pode ser definido manualmente."
            )

        now = _now()
        due_at = changes.get("due_at", task.due_at)
        comparable_due_at = (
            due_at
            if due_at.tzinfo is not None
            else due_at.replace(tzinfo=timezone.utc)
        )
        if target_status == FollowUpStatus.scheduled and comparable_due_at <= now:
            raise ValueError(
                "Um follow-up agendado precisa ter data futura."
            )

        next_status = task.status
        if target_status is not None:
            next_status = target_status
        elif "due_at" in changes:
            next_status = (
                FollowUpStatus.scheduled
                if comparable_due_at > now
                else FollowUpStatus.due
            )

        before_data: dict[str, Any] = {}
        after_data: dict[str, Any] = {}
        if "due_at" in changes and changes["due_at"] != task.due_at:
            before_data["dueAt"] = task.due_at.isoformat()
            task.due_at = changes["due_at"]
            after_data["dueAt"] = task.due_at.isoformat()
        if "reason" in changes and changes["reason"] != task.reason:
            before_data["reason"] = task.reason
            task.reason = changes["reason"]
            after_data["reason"] = task.reason
        if next_status != task.status:
            before_data["status"] = task.status.value
            task.status = next_status
            after_data["status"] = task.status.value

        if not after_data:
            return task

        task.updated_at = now
        await self.db.flush()
        await self.record_audit(
            workspace_id=workspace_id,
            actor_user_id=user_id,
            actor_type="human",
            actor_label="Usuário KLM",
            event_type="follow_up_updated",
            entity_type="follow_up",
            entity_id=task.id,
            summary="Follow-up atualizado localmente.",
            before_data=before_data,
            after_data=after_data,
        )
        return task

    async def list_follow_ups(
        self, workspace_id: UUID, status: Optional[FollowUpStatus] = None
    ) -> List[FollowUpTask]:
        query = select(FollowUpTask).where(FollowUpTask.workspace_id == workspace_id)
        if status:
            query = query.where(FollowUpTask.status == status)
        result = await self.db.execute(query.order_by(FollowUpTask.due_at.asc()))
        return list(result.scalars().all())

    async def list_due_follow_ups_for_notification(
        self, now: Optional[datetime] = None, limit: int = 200
    ) -> List[FollowUpTask]:
        """Follow-ups vencidos e ainda não notificados neste ciclo.

        Escopo por RLS (o worker chama `set_workspace_context` antes), mesmo
        padrão de `MeetingService.list_due_reminders`. `last_notified_at IS
        NULL` garante que um follow-up só é retornado uma vez por ciclo —
        `mark_follow_up_notified` fecha o ciclo depois do disparo real.
        """
        reference_now = now or _now()
        result = await self.db.execute(
            select(FollowUpTask)
            .where(
                FollowUpTask.status.in_([FollowUpStatus.scheduled, FollowUpStatus.due]),
                FollowUpTask.due_at <= reference_now,
                FollowUpTask.last_notified_at.is_(None),
            )
            .order_by(FollowUpTask.due_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True, of=FollowUpTask)
        )
        return list(result.scalars().all())

    async def mark_follow_up_notified(
        self, task: FollowUpTask, notified_at: Optional[datetime] = None
    ) -> None:
        notified_at = notified_at or _now()
        if task.status == FollowUpStatus.scheduled:
            task.status = FollowUpStatus.due
        task.last_notified_at = notified_at
        await self.db.flush()

    async def suppress_thread_follow_ups(
        self,
        workspace_id: UUID,
        thread_id: UUID,
        user_id: UUID,
        reason: str,
        blocked_by_client: bool,
    ) -> int:
        if not blocked_by_client:
            raise ValueError("Follow-up só pode parar quando o cliente bloquear.")
        thread = await self.get_thread(workspace_id, thread_id)
        if not thread:
            raise LookupError("Thread não encontrada no workspace.")
        thread.status = ThreadStatus.blocked
        thread.updated_at = _now()
        result = await self.db.execute(
            select(FollowUpTask).where(
                FollowUpTask.workspace_id == workspace_id,
                FollowUpTask.thread_id == thread_id,
                FollowUpTask.status.in_([FollowUpStatus.scheduled, FollowUpStatus.due]),
            )
        )
        tasks = list(result.scalars().all())
        for task in tasks:
            task.status = FollowUpStatus.suppressed
            task.reason = reason
            task.updated_at = _now()
        await self.db.flush()
        await self.record_audit(
            workspace_id=workspace_id,
            actor_user_id=user_id,
            actor_type="human",
            actor_label="Usuário KLM",
            event_type="follow_up_suppressed",
            entity_type="omnichannel_thread",
            entity_id=thread.id,
            summary=f"Follow-ups interrompidos após bloqueio do cliente: {reason}",
            after_data={"suppressedCount": len(tasks)},
        )
        return len(tasks)

    async def _conversion_reporting_touch(
        self,
        workspace_id: UUID,
        event: AcquisitionEvent,
    ) -> tuple[AcquisitionEvent, AttributionEvidence] | None:
        """Resolve the latest attributable touch without crossing tenant or person scope."""

        direct = extract_attribution(event)
        if direct.evidence_level != "unattributed":
            return event, direct

        if event.matter_id:
            scope = [AcquisitionEvent.matter_id == event.matter_id]
        elif event.client_id:
            scope = [AcquisitionEvent.client_id == event.client_id]
        else:
            return None
        candidates = list(
            (
                await self.db.scalars(
                    select(AcquisitionEvent)
                    .where(
                        AcquisitionEvent.workspace_id == workspace_id,
                        AcquisitionEvent.id != event.id,
                        AcquisitionEvent.happened_at <= event.happened_at,
                        *scope,
                    )
                    .order_by(
                        AcquisitionEvent.happened_at.desc(),
                        AcquisitionEvent.id.desc(),
                    )
                    .limit(100)
                )
            ).all()
        )
        for candidate in candidates:
            evidence = extract_attribution(candidate)
            if evidence.evidence_level != "unattributed":
                return candidate, evidence
        return None

    async def _conversion_attribution_touch(
        self,
        workspace_id: UUID,
        event: AcquisitionEvent,
        *,
        client: Client | None = None,
    ) -> tuple[AcquisitionEvent, AttributionEvidence, str] | None:
        if client is None:
            client = (await self._batch_conversion_clients(workspace_id, [event])).get(
                event.id
            )
        has_user_identifiers = bool(
            client
            and has_usable_first_party_identity(client.email, client.phone)
        )
        direct = extract_attribution(event)
        direct_eligible, direct_reason = feedback_eligibility(
            direct,
            event,
            has_user_identifiers=has_user_identifiers,
        )
        if direct_eligible:
            return event, direct, direct_reason

        scope = []
        if event.matter_id:
            scope.append(AcquisitionEvent.matter_id == event.matter_id)
        elif event.client_id:
            scope.append(AcquisitionEvent.client_id == event.client_id)
        else:
            return None
        candidates = list(
            (
                await self.db.scalars(
                    select(AcquisitionEvent)
                    .where(
                        AcquisitionEvent.workspace_id == workspace_id,
                        AcquisitionEvent.id != event.id,
                        AcquisitionEvent.happened_at <= event.happened_at,
                        *scope,
                    )
                    .order_by(
                        AcquisitionEvent.happened_at.desc(),
                        AcquisitionEvent.id.desc(),
                    )
                    .limit(100)
                )
            ).all()
        )
        for candidate in candidates:
            evidence = extract_attribution(candidate)
            eligible, reason = feedback_eligibility(
                evidence,
                candidate,
                has_user_identifiers=has_user_identifiers,
            )
            if eligible:
                return candidate, evidence, reason
        return None

    async def _batch_conversion_clients(
        self,
        workspace_id: UUID,
        conversions: List[AcquisitionEvent],
    ) -> Dict[UUID, Client]:
        """Resolve canonical clients without trusting event metadata contact fields."""

        if not conversions:
            return {}
        matter_ids = {event.matter_id for event in conversions if event.matter_id}
        matter_to_client: Dict[UUID, UUID] = {}
        if matter_ids:
            rows = (
                await self.db.execute(
                    select(Matter.id, Matter.client_id).where(
                        Matter.workspace_id == workspace_id,
                        Matter.id.in_(matter_ids),
                        Matter.deleted_at.is_(None),
                    )
                )
            ).all()
            matter_to_client = {matter_id: client_id for matter_id, client_id in rows}
        client_ids = {
            event.client_id or matter_to_client.get(event.matter_id)
            for event in conversions
        }
        client_ids.discard(None)
        if not client_ids:
            return {}
        clients = list(
            (
                await self.db.scalars(
                    select(Client).where(
                        Client.workspace_id == workspace_id,
                        Client.id.in_(client_ids),
                        Client.deleted_at.is_(None),
                    )
                )
            ).all()
        )
        by_id = {client.id: client for client in clients}
        return {
            event.id: by_id[client_id]
            for event in conversions
            if (
                client_id := event.client_id
                or matter_to_client.get(event.matter_id)
            )
            in by_id
        }

    async def _batch_conversion_touches(
        self,
        workspace_id: UUID,
        conversions: List[AcquisitionEvent],
        *,
        conversion_clients: Dict[UUID, Client] | None = None,
    ) -> tuple[
        Dict[UUID, tuple[AcquisitionEvent, AttributionEvidence]],
        Dict[UUID, tuple[AcquisitionEvent, AttributionEvidence, str]],
        Dict[UUID, Client],
    ]:
        """Resolve reporting and provider-match touches with one bounded query."""

        if not conversions:
            return {}, {}, {}
        conversion_clients = conversion_clients or await self._batch_conversion_clients(
            workspace_id, conversions
        )
        matter_ids = {event.matter_id for event in conversions if event.matter_id}
        client_ids = {
            event.client_id
            for event in conversions
            if event.matter_id is None and event.client_id
        }
        scope_filters = []
        if matter_ids:
            scope_filters.append(AcquisitionEvent.matter_id.in_(matter_ids))
        if client_ids:
            scope_filters.append(AcquisitionEvent.client_id.in_(client_ids))

        history: List[AcquisitionEvent] = []
        if scope_filters:
            history = list(
                (
                    await self.db.scalars(
                        select(AcquisitionEvent)
                        .where(
                            AcquisitionEvent.workspace_id == workspace_id,
                            AcquisitionEvent.happened_at
                            <= max(event.happened_at for event in conversions),
                            or_(*scope_filters),
                        )
                        .order_by(
                            AcquisitionEvent.happened_at.asc(),
                            AcquisitionEvent.id.asc(),
                        )
                    )
                ).all()
            )
        by_matter: Dict[UUID, List[AcquisitionEvent]] = {}
        by_client: Dict[UUID, List[AcquisitionEvent]] = {}
        for candidate in history:
            if candidate.matter_id:
                by_matter.setdefault(candidate.matter_id, []).append(candidate)
            if candidate.client_id:
                by_client.setdefault(candidate.client_id, []).append(candidate)

        reporting: Dict[UUID, tuple[AcquisitionEvent, AttributionEvidence]] = {}
        feedback: Dict[
            UUID, tuple[AcquisitionEvent, AttributionEvidence, str]
        ] = {}
        for conversion in conversions:
            client = conversion_clients.get(conversion.id)
            has_user_identifiers = bool(
                client
                and has_usable_first_party_identity(client.email, client.phone)
            )
            candidates = [conversion]
            scoped_history = (
                by_matter.get(conversion.matter_id, [])
                if conversion.matter_id
                else by_client.get(conversion.client_id, [])
                if conversion.client_id
                else []
            )
            candidates.extend(
                candidate
                for candidate in reversed(scoped_history)
                if candidate.id != conversion.id
                and candidate.happened_at <= conversion.happened_at
            )
            for candidate in candidates:
                evidence = extract_attribution(candidate)
                if (
                    conversion.id not in reporting
                    and evidence.evidence_level != "unattributed"
                ):
                    reporting[conversion.id] = (candidate, evidence)
                eligible, reason = feedback_eligibility(
                    evidence,
                    candidate,
                    has_user_identifiers=has_user_identifiers,
                )
                if conversion.id not in feedback and eligible:
                    feedback[conversion.id] = (candidate, evidence, reason)
                if conversion.id in reporting and conversion.id in feedback:
                    break
        return reporting, feedback, conversion_clients

    async def _ensure_conversion_feedback_record(
        self,
        *,
        workspace_id: UUID,
        intent: ExternalActionIntent,
        envelope: Dict[str, Any],
    ) -> AdsConversionFeedbackRecord:
        payload_sha256 = str(envelope.get("payloadSha256") or "")
        if len(payload_sha256) != 64:
            raise ValueError("Envelope de conversão sem hash canônico válido.")
        existing = await self.db.scalar(
            select(AdsConversionFeedbackRecord).where(
                AdsConversionFeedbackRecord.workspace_id == workspace_id,
                AdsConversionFeedbackRecord.intent_id == intent.id,
                AdsConversionFeedbackRecord.record_type == "prepared",
            )
            .order_by(AdsConversionFeedbackRecord.attempt_number.desc())
            .limit(1)
        )
        if existing:
            if existing.provider != intent.provider:
                raise ValueError(
                    "A prova imutável da conversão diverge do envelope preparado."
                )
            if (
                existing.payload_sha256 == payload_sha256
                and (existing.request_envelope or {}) == envelope
            ):
                return existing
        attempt_number = (existing.attempt_number + 1) if existing else 0
        record = AdsConversionFeedbackRecord(
            workspace_id=workspace_id,
            intent_id=intent.id,
            provider=intent.provider or "unknown",
            record_type="prepared",
            state="prepared_local",
            attempt_number=attempt_number,
            provider_event_id=str(envelope.get("providerEventId") or ""),
            idempotency_key=intent.idempotency_key,
            payload_sha256=payload_sha256,
            request_envelope=envelope,
            provider_receipt={},
            external_actions_executed=0,
        )
        try:
            async with self.db.begin_nested():
                self.db.add(record)
                await self.db.flush()
        except IntegrityError:
            existing = await self.db.scalar(
                select(AdsConversionFeedbackRecord).where(
                AdsConversionFeedbackRecord.workspace_id == workspace_id,
                AdsConversionFeedbackRecord.intent_id == intent.id,
                AdsConversionFeedbackRecord.record_type == "prepared",
                AdsConversionFeedbackRecord.attempt_number == attempt_number,
                )
            )
            if (
                existing is None
                or existing.payload_sha256 != payload_sha256
                or (existing.request_envelope or {}) != envelope
            ):
                raise
            return existing
        return record

    async def _record_conversion_feedback_duplicate(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID | None,
        intent: ExternalActionIntent,
    ) -> None:
        await self.record_audit(
            workspace_id=workspace_id,
            actor_user_id=user_id,
            actor_type="human" if user_id else "system",
            actor_label="Track KLM",
            event_type="ads_conversion_feedback_duplicate_prevented",
            entity_type="external_action_intent",
            entity_id=intent.id,
            summary=f"Repetição bloqueada para {intent.provider or 'provedor desconhecido'}.",
            correlation_id=intent.idempotency_key,
            after_data={
                "provider": intent.provider,
                "payloadSha256": (intent.payload or {}).get("payloadSha256"),
                "externalActionsExecuted": 0,
            },
        )

    async def _ensure_conversion_feedback_intent(
        self,
        workspace_id: UUID,
        user_id: UUID | None,
        event: AcquisitionEvent,
        attribution: tuple[AcquisitionEvent, AttributionEvidence, str] | None = None,
        *,
        client: Client | None = None,
        workspace_settings: Dict[str, Any] | None = None,
    ) -> tuple[ExternalActionIntent | None, bool]:
        normalized_event = _normalize_tracking_event_name(event.event_name)
        if normalized_event not in TRACKING_CONVERSION_EVENTS:
            return None, False
        attribution = attribution or await self._conversion_attribution_touch(
            workspace_id,
            event,
            client=client,
        )
        if attribution is None:
            return None, False
        touch, evidence, match_reason = attribution
        assert evidence.provider is not None
        provider = evidence.provider
        if client is None:
            client = (await self._batch_conversion_clients(workspace_id, [event])).get(
                event.id
            )
        business_identity = _commercial_event_identity(
            event,
            normalized_event,
            canonical_client_id=client.id if client else None,
        )
        business_identity_hash = (
            _commercial_event_identity_hash(workspace_id, business_identity)
            if business_identity
            else None
        )
        idempotency_suffix = business_identity_hash or str(event.id)
        idempotency_key = f"ads-conversion-feedback:{provider}:{idempotency_suffix}"
        match_keys = feedback_click_ids(touch, provider)
        if workspace_settings is None:
            workspace_settings = (
                await self.db.scalar(
                    select(Workspace.settings).where(Workspace.id == workspace_id)
                )
                or {}
            )
        consent_state, consent_evidence_ref = _conversion_consent_context(event, touch)
        provider_envelope = build_provider_feedback_envelope(
            provider=provider,
            conversion_event=normalized_event,
            source_event_id=event.id,
            happened_at=event.happened_at,
            value=event.value,
            currency=event.currency,
            match_keys=match_keys,
            workspace_id=workspace_id,
            client_id=client.id if client else None,
            email=client.email if client else None,
            phone=client.phone if client else None,
            config=provider_feedback_config(workspace_settings, provider),
            ad_data_consent=consent_state,
            consent_evidence_ref=consent_evidence_ref,
        ).public_dict()
        provider_event_id = str(provider_envelope["providerEventId"])
        payload = {
            "schemaVersion": "klm.ads.conversion-feedback.v2",
            "sourceEventId": str(event.id),
            "sourceEventDedupeKey": event.dedupe_key,
            "sourceTouchEventId": str(touch.id),
            "businessEventFamily": business_identity[0] if business_identity else None,
            "businessEventIdentityHash": business_identity_hash,
            "providerEventId": provider_event_id,
            "orderId": provider_event_id,
            "conversionEvent": normalized_event,
            "conversionHappenedAt": _normalized_moment(event.happened_at).isoformat(),
            "value": float(event.value) if event.value is not None else None,
            "currency": event.currency,
            "attributionModel": "last_paid_touch",
            "attribution": evidence.public_dict(),
            "providerMatchKeys": match_keys,
            "providerMatchKeyNames": provider_envelope["matchKeyNames"],
            "matchReason": match_reason,
            "matchingMode": provider_envelope["matchingMode"],
            "readyForDispatch": provider_envelope["readyForDispatch"],
            "missingConfiguration": provider_envelope["missingConfiguration"],
            "payloadSha256": provider_envelope["payloadSha256"],
            "privacyGuard": provider_envelope["privacyGuard"],
            "providerEnvelope": provider_envelope,
            "deduplication": {
                "internalKey": idempotency_key,
                "metaEventId": provider_event_id if provider == "meta_ads" else None,
                "googleOrderId": provider_event_id if provider == "google_ads" else None,
                "tiktokEventId": provider_event_id if provider == "tiktok_ads" else None,
                "linkedInEventId": provider_event_id if provider == "linkedin_ads" else None,
                "microsoftEventId": provider_event_id
                if provider == "microsoft_ads"
                else None,
            },
            "localOnly": True,
            "externalActionsExecuted": 0,
        }
        existing = await self.db.scalar(
            select(ExternalActionIntent).where(
                ExternalActionIntent.workspace_id == workspace_id,
                ExternalActionIntent.idempotency_key == idempotency_key,
            )
        )
        if existing:
            if (
                existing.action_type != "ads_conversion_feedback"
                or existing.provider != provider
                or existing.matter_id != event.matter_id
            ):
                raise ValueError(
                    "A chave deterministica da conversao ja pertence a outro payload."
                )
            existing_payload = existing.payload or {}
            envelope_changed = False
            if existing_payload != payload:
                same_business_event = bool(
                    business_identity_hash
                    and existing_payload.get("businessEventIdentityHash")
                    == business_identity_hash
                )
                different_source_event = (
                    str(existing_payload.get("sourceEventId") or "") != str(event.id)
                )
                existing_event_name = _normalize_tracking_event_name(
                    str(existing_payload.get("conversionEvent") or "")
                )
                if (
                    same_business_event
                    and different_source_event
                    and TRACKING_REVENUE_EVENT_PRIORITY.get(normalized_event, 0)
                    <= TRACKING_REVENUE_EVENT_PRIORITY.get(existing_event_name, 0)
                ):
                    await self._ensure_conversion_feedback_record(
                        workspace_id=workspace_id,
                        intent=existing,
                        envelope=existing_payload.get("providerEnvelope") or {},
                    )
                    await self._record_conversion_feedback_duplicate(
                        workspace_id=workspace_id,
                        user_id=user_id,
                        intent=existing,
                    )
                    return existing, True
                executed_evidence = await self.db.scalar(
                    select(func.count(AdsConversionFeedbackRecord.id)).where(
                        AdsConversionFeedbackRecord.workspace_id == workspace_id,
                        AdsConversionFeedbackRecord.intent_id == existing.id,
                        or_(
                            AdsConversionFeedbackRecord.external_actions_executed > 0,
                            AdsConversionFeedbackRecord.record_type == "provider_receipt",
                        ),
                    )
                )
                if (
                    int(existing_payload.get("externalActionsExecuted") or 0) != 0
                    or int(executed_evidence or 0) != 0
                ):
                    if same_business_event and different_source_event:
                        await self._record_conversion_feedback_duplicate(
                            workspace_id=workspace_id,
                            user_id=user_id,
                            intent=existing,
                        )
                        return existing, True
                    raise ValueError(
                        "Uma conversão já executada não pode ter o envelope substituído."
                    )
                before_payload = existing_payload
                existing.payload = payload
                existing.updated_at = _now()
                envelope_changed = True
                await self.db.flush()
                await self.record_audit(
                    workspace_id=workspace_id,
                    actor_user_id=user_id,
                    actor_type="human" if user_id else "system",
                    actor_label="Track KLM",
                    event_type="ads_conversion_feedback_envelope_superseded",
                    entity_type="external_action_intent",
                    entity_id=existing.id,
                    summary=f"Nova versão local do envelope de {provider} registrada.",
                    correlation_id=idempotency_key,
                    before_data={
                        "schemaVersion": before_payload.get("schemaVersion"),
                        "externalActionsExecuted": 0,
                    },
                    after_data={
                        "schemaVersion": payload["schemaVersion"],
                        "payloadSha256": payload["payloadSha256"],
                        "externalActionsExecuted": 0,
                    },
                )
            await self._ensure_conversion_feedback_record(
                workspace_id=workspace_id,
                intent=existing,
                envelope=provider_envelope,
            )
            if not envelope_changed:
                await self._record_conversion_feedback_duplicate(
                    workspace_id=workspace_id,
                    user_id=user_id,
                    intent=existing,
                )
            return existing, not envelope_changed

        intent = ExternalActionIntent(
            workspace_id=workspace_id,
            requested_by_user_id=user_id,
            matter_id=event.matter_id,
            action_type="ads_conversion_feedback",
            provider=provider,
            idempotency_key=idempotency_key,
            status=(
                ExternalActionIntentStatus.awaiting_approval
                if external_delivery_allowed()
                else ExternalActionIntentStatus.blocked_external
            ),
            payload=payload,
            reason=(
                "Conversao preparada com identificador estavel; envio externo depende "
                "do gate e da credencial do provedor."
            ),
        )
        try:
            async with self.db.begin_nested():
                self.db.add(intent)
                await self.db.flush()
        except IntegrityError:
            existing = await self.db.scalar(
                select(ExternalActionIntent).where(
                    ExternalActionIntent.workspace_id == workspace_id,
                    ExternalActionIntent.idempotency_key == idempotency_key,
                )
            )
            if existing is None or (existing.payload or {}) != payload:
                raise
            await self._ensure_conversion_feedback_record(
                workspace_id=workspace_id,
                intent=existing,
                envelope=provider_envelope,
            )
            await self._record_conversion_feedback_duplicate(
                workspace_id=workspace_id,
                user_id=user_id,
                intent=existing,
            )
            return existing, True
        await self._ensure_conversion_feedback_record(
            workspace_id=workspace_id,
            intent=intent,
            envelope=provider_envelope,
        )
        await self.record_audit(
            workspace_id=workspace_id,
            actor_user_id=user_id,
            actor_type="human" if user_id else "system",
            actor_label="Track KLM",
            event_type="ads_conversion_feedback_prepared",
            entity_type="external_action_intent",
            entity_id=intent.id,
            summary=f"Conversao preparada uma unica vez para {provider}.",
            correlation_id=idempotency_key,
            after_data={
                "sourceEventId": str(event.id),
                "sourceTouchEventId": str(touch.id),
                "provider": provider,
                "providerMatchKeyNames": provider_envelope["matchKeyNames"],
                "matchingMode": provider_envelope["matchingMode"],
                "readyForDispatch": provider_envelope["readyForDispatch"],
                "missingConfiguration": provider_envelope["missingConfiguration"],
                "payloadSha256": provider_envelope["payloadSha256"],
                "externalActionsExecuted": 0,
            },
        )
        return intent, False

    async def record_acquisition_event(
        self, workspace_id: UUID, user_id: UUID | None, payload: AcquisitionEventCreate
    ) -> Tuple[AcquisitionEvent, bool]:
        existing = await self.db.scalar(
            select(AcquisitionEvent).where(
                AcquisitionEvent.workspace_id == workspace_id,
                AcquisitionEvent.dedupe_key == payload.dedupe_key,
            )
        )
        if existing:
            _require_matching_acquisition_event_replay(existing, payload)
            await self._ensure_conversion_feedback_intent(workspace_id, user_id, existing)
            return existing, True
        matter = await self._matter(workspace_id, payload.matter_id) if payload.matter_id else None
        client = await self._client(workspace_id, payload.client_id) if payload.client_id else None
        if payload.matter_id and not matter:
            raise LookupError("Caso não encontrado no workspace.")
        if payload.client_id and not client:
            raise LookupError("Cliente não encontrado no workspace.")
        if matter and client and matter.client_id != client.id:
            raise ValueError("Cliente não pertence ao caso informado.")
        values = payload.model_dump(exclude={"happened_at"})
        event = AcquisitionEvent(
            workspace_id=workspace_id,
            happened_at=payload.happened_at or _now(),
            **values,
        )
        try:
            async with self.db.begin_nested():
                self.db.add(event)
                await self.db.flush()
        except IntegrityError:
            existing = await self.db.scalar(
                select(AcquisitionEvent).where(
                    AcquisitionEvent.workspace_id == workspace_id,
                    AcquisitionEvent.dedupe_key == payload.dedupe_key,
                )
            )
            if existing:
                _require_matching_acquisition_event_replay(existing, payload)
                await self._ensure_conversion_feedback_intent(workspace_id, user_id, existing)
                return existing, True
            raise
        await self.record_audit(
            workspace_id=workspace_id,
            actor_user_id=user_id,
            actor_type="human" if user_id else "system",
            actor_label="Track local" if user_id else "Ingresso omnichannel KLM",
            event_type="acquisition_event_recorded",
            entity_type="acquisition_event",
            entity_id=event.id,
            summary=f"Evento {event.event_name} registrado para {event.channel}.",
            correlation_id=event.dedupe_key,
        )
        await self._ensure_conversion_feedback_intent(workspace_id, user_id, event)
        return event, False

    async def list_acquisition_events(
        self, workspace_id: UUID, limit: int = 500
    ) -> List[AcquisitionEvent]:
        result = await self.db.execute(
            select(AcquisitionEvent)
            .where(AcquisitionEvent.workspace_id == workspace_id)
            .order_by(AcquisitionEvent.happened_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def tracking_summary(
        self, workspace_id: UUID, period_days: Optional[int] = None
    ) -> Dict[str, Any]:
        if period_days is not None and period_days < 1:
            raise ValueError("O período de tracking deve ter ao menos um dia.")

        period_end = _now()
        period_start = period_end - timedelta(days=period_days) if period_days else None
        filters = [
            AcquisitionEvent.workspace_id == workspace_id,
            AcquisitionEvent.happened_at <= _now(),
        ]
        if period_start is not None:
            filters.extend(
                [
                    AcquisitionEvent.happened_at >= period_start,
                    AcquisitionEvent.happened_at <= period_end,
                ]
            )
        result = await self.db.execute(
            select(AcquisitionEvent)
            .where(*filters)
            .order_by(AcquisitionEvent.happened_at.asc(), AcquisitionEvent.id.asc())
        )
        raw_events = list(result.scalars().all())
        raw_conversion_events = [
            event
            for event in raw_events
            if _normalize_tracking_event_name(event.event_name)
            in TRACKING_CONVERSION_EVENTS
        ]
        raw_conversion_clients = await self._batch_conversion_clients(
            workspace_id, raw_conversion_events
        )
        conversion_events, suppressed_business_event_ids = (
            _canonicalize_commercial_events(
                raw_conversion_events,
                canonical_clients=raw_conversion_clients,
            )
        )
        events = [
            event for event in raw_events if event.id not in suppressed_business_event_ids
        ]

        entity_rows = list(
            (
                await self.db.scalars(
                    select(AdsManagerEntity).where(
                        AdsManagerEntity.workspace_id == workspace_id
                    )
                )
            ).all()
        )
        entity_names = {
            (row.provider, row.entity_type, row.external_id): row.display_name
            for row in entity_rows
        }
        contains_local_fixture_data = bool(
            await self.db.scalar(
                select(func.count())
                .select_from(AdsManagerImportReceipt)
                .where(
                    AdsManagerImportReceipt.workspace_id == workspace_id,
                    AdsManagerImportReceipt.source == "local_fixture",
                )
            )
            or 0
        )
        if not contains_local_fixture_data:
            # Dados de aquisição também podem vir do seed local de demonstração
            # (klm_local_seed_service), que nunca grava um recibo de importação:
            # ele marca cada AcquisitionEvent com medium="local_fixture" e um
            # dedupe_key prefixado por "seed:". Sem essa checagem, o selo de
            # demonstração ficava restrito a dados de fixture do gestor de
            # anúncios e não cobria a aquisição semeada localmente.
            contains_local_fixture_data = bool(
                await self.db.scalar(
                    select(func.count())
                    .select_from(AcquisitionEvent)
                    .where(
                        AcquisitionEvent.workspace_id == workspace_id,
                        or_(
                            AcquisitionEvent.medium == "local_fixture",
                            AcquisitionEvent.dedupe_key.like("seed:%"),
                        ),
                    )
                )
                or 0
            )
        thread_rows = list(
            (
                await self.db.scalars(
                    select(OmnichannelThread)
                    .where(OmnichannelThread.workspace_id == workspace_id)
                    .order_by(OmnichannelThread.updated_at.desc())
                )
            ).all()
        )
        thread_by_matter: Dict[UUID, UUID] = {}
        thread_by_client: Dict[UUID, UUID] = {}
        workspace_thread_ids = {thread.id for thread in thread_rows}
        for thread in thread_rows:
            if thread.matter_id:
                thread_by_matter.setdefault(thread.matter_id, thread.id)
            if thread.client_id:
                thread_by_client.setdefault(thread.client_id, thread.id)

        conversion_clients = {
            event.id: raw_conversion_clients[event.id]
            for event in conversion_events
            if event.id in raw_conversion_clients
        }
        (
            raw_reporting_touches,
            feedback_touches,
            _conversion_clients,
        ) = await self._batch_conversion_touches(
            workspace_id,
            conversion_events,
            conversion_clients=conversion_clients,
        )
        reporting_touch_by_conversion: Dict[
            UUID, tuple[AcquisitionEvent, AttributionEvidence]
        ] = {
            conversion_id: (
                touch,
                extract_attribution(touch, entity_names=entity_names),
            )
            for conversion_id, (touch, _evidence) in raw_reporting_touches.items()
        }
        feedback_eligible_event_ids = set(feedback_touches)

        def sample_thread_id(event: AcquisitionEvent) -> UUID | None:
            metadata = event.event_metadata or {}
            raw_thread_id = metadata.get("threadId") or metadata.get("thread_id")
            if raw_thread_id:
                try:
                    candidate = UUID(str(raw_thread_id))
                    if candidate in workspace_thread_ids:
                        return candidate
                except (TypeError, ValueError):
                    pass
            if event.matter_id:
                return thread_by_matter.get(event.matter_id)
            if event.client_id:
                return thread_by_client.get(event.client_id)
            return None

        total_bucket = _new_tracking_metric_bucket()
        channel_buckets: Dict[str, Dict[str, Any]] = {}
        trend_buckets: Dict[Any, Dict[str, Any]] = {}
        daily_channel_buckets: Dict[Tuple[Any, str], Dict[str, Any]] = {}
        event_counts: Dict[str, int] = {}
        journey_events: Dict[
            Tuple[str, str], List[Tuple[AcquisitionEvent, str, str, datetime]]
        ] = {}
        direct_evidence: Dict[UUID, AttributionEvidence] = {}

        for event in events:
            normalized_event = _normalize_tracking_event_name(event.event_name)
            channel = (event.channel or "unknown").strip() or "unknown"
            attributed_touch = reporting_touch_by_conversion.get(event.id)
            metric_channel = (
                ((attributed_touch[0].channel or "unknown").strip() or "unknown")
                if normalized_event in TRACKING_CONVERSION_EVENTS
                and attributed_touch is not None
                else channel
            )
            happened_at = _normalized_moment(event.happened_at)
            direct_evidence[event.id] = extract_attribution(
                event, entity_names=entity_names
            )

            _accumulate_tracking_event(total_bucket, normalized_event, event.value)
            _accumulate_tracking_event(
                channel_buckets.setdefault(
                    metric_channel, _new_tracking_metric_bucket()
                ),
                normalized_event,
                event.value,
            )
            _accumulate_tracking_event(
                trend_buckets.setdefault(happened_at.date(), _new_tracking_metric_bucket()),
                normalized_event,
                event.value,
            )
            _accumulate_tracking_event(
                daily_channel_buckets.setdefault(
                    (happened_at.date(), metric_channel),
                    _new_tracking_metric_bucket(),
                ),
                normalized_event,
                event.value,
            )
            event_counts[normalized_event] = event_counts.get(normalized_event, 0) + 1

            identity = _tracking_identity(event)
            if identity:
                journey_events.setdefault(identity, []).append(
                    (event, normalized_event, channel, happened_at)
                )

        by_channel = []
        for channel, bucket in channel_buckets.items():
            metrics = _finalize_tracking_metrics(bucket)
            by_channel.append(
                {
                    "channel": channel,
                    **metrics,
                    "value": metrics["attributable_revenue"],
                }
            )
        by_channel.sort(key=lambda item: (-item["events"], item["channel"].casefold()))

        time_trend = [
            {"date": day, **_finalize_tracking_metrics(bucket)}
            for day, bucket in sorted(trend_buckets.items())
        ]
        daily_channels = []
        for (day, channel), bucket in sorted(daily_channel_buckets.items()):
            metrics = _finalize_tracking_metrics(bucket)
            daily_channels.append(
                {
                    "date": day,
                    "channel": channel,
                    "events": metrics["events"],
                    "leads": metrics["leads"],
                    "clients": metrics["clients"],
                    "attributable_revenue": metrics["attributable_revenue"],
                }
            )

        journeys = []
        remarketing_buckets: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for identity, rows in journey_events.items():
            journey_bucket = _new_tracking_metric_bucket()
            event_path: List[str] = []
            journey_channels: List[str] = []
            for event, normalized_event, channel, _ in rows:
                _accumulate_tracking_event(journey_bucket, normalized_event, event.value)
                event_path.append(normalized_event)
                if channel not in journey_channels:
                    journey_channels.append(channel)

            journey_metrics = _finalize_tracking_metrics(journey_bucket)
            converted = any(row[1] in TRACKING_CONVERSION_EVENTS for row in rows)
            journeys.append(
                {
                    "journey_id": _public_journey_id(workspace_id, identity),
                    "identity_type": identity[0],
                    "event_path": event_path,
                    "channels": journey_channels,
                    "started_at": rows[0][3],
                    "last_activity_at": rows[-1][3],
                    "converted": converted,
                    **journey_metrics,
                }
            )

            stage_rows = [row for row in rows if row[1] in TRACKING_STAGE_RANK]
            if converted or not stage_rows:
                continue
            furthest_stage = max(
                stage_rows,
                key=lambda row: (TRACKING_STAGE_RANK[row[1]], row[3]),
            )[1]
            remarketing_key = (furthest_stage, rows[-1][2])
            remarketing_bucket = remarketing_buckets.setdefault(
                remarketing_key,
                {
                    "stage": furthest_stage,
                    "channel": rows[-1][2],
                    "audience_size": 0,
                    "last_activity_at": rows[-1][3],
                },
            )
            remarketing_bucket["audience_size"] += 1
            remarketing_bucket["last_activity_at"] = max(
                remarketing_bucket["last_activity_at"], rows[-1][3]
            )

        journeys.sort(key=lambda item: item["last_activity_at"], reverse=True)
        remarketing = sorted(
            remarketing_buckets.values(),
            key=lambda item: (
                -item["audience_size"],
                -TRACKING_STAGE_RANK[item["stage"]],
                item["channel"].casefold(),
            ),
        )

        attribution_buckets: Dict[Tuple[str, ...], Dict[str, Any]] = {}
        tracked_journeys: set[Tuple[str, str]] = set()
        full_hierarchy_touches = 0
        partial_hierarchy_touches = 0
        for event in events:
            normalized_event = _normalize_tracking_event_name(event.event_name)
            identity = _tracking_identity(event)
            journey_id = _public_journey_id(workspace_id, identity) if identity else None
            source_event = event
            evidence = direct_evidence[event.id]
            if normalized_event in TRACKING_CONVERSION_EVENTS:
                attributed_touch = reporting_touch_by_conversion.get(event.id)
                if attributed_touch is not None:
                    source_event, evidence = attributed_touch
            if identity and direct_evidence[event.id].evidence_level != "unattributed":
                tracked_journeys.add(identity)
            is_touch = (
                normalized_event not in TRACKING_CONVERSION_EVENTS
                and normalized_event != "ad_spend"
                and evidence.evidence_level != "unattributed"
            )
            if is_touch:
                if evidence.hierarchy_completeness == 100:
                    full_hierarchy_touches += 1
                else:
                    partial_hierarchy_touches += 1
            bucket = attribution_buckets.setdefault(
                evidence.group_key,
                _new_attribution_bucket(evidence, _normalized_moment(source_event.happened_at)),
            )
            _accumulate_attribution_bucket(
                bucket,
                evidence=evidence,
                event_name=normalized_event,
                value=event.value,
                happened_at=_normalized_moment(event.happened_at),
                journey_id=journey_id,
                is_touch=is_touch,
            )
            if bucket["sample_journey_id"] is None and journey_id:
                bucket["sample_journey_id"] = journey_id
            if bucket["sample_thread_id"] is None:
                bucket["sample_thread_id"] = sample_thread_id(event) or sample_thread_id(
                    source_event
                )
            if bucket["sample_matter_id"] is None:
                bucket["sample_matter_id"] = event.matter_id or source_event.matter_id

        attribution_rows = [
            _finalize_attribution_bucket(bucket)
            for bucket in attribution_buckets.values()
        ]
        attribution_rows.sort(
            key=lambda item: (
                -item["attributable_revenue"],
                -item["clients"],
                -item["leads"],
                -item["touch_count"],
                item["provider_label"].casefold(),
            )
        )
        attributed_conversion_count = len(reporting_touch_by_conversion)
        conversation_count = len(journey_events)
        attribution_coverage = {
            "conversations": conversation_count,
            "tracked_conversations": len(tracked_journeys),
            "untracked_conversations": max(0, conversation_count - len(tracked_journeys)),
            "tracking_rate": round(len(tracked_journeys) / conversation_count * 100, 2)
            if conversation_count
            else None,
            "conversion_events": len(conversion_events),
            "attributed_conversions": attributed_conversion_count,
            "unattributed_conversions": max(
                0, len(conversion_events) - attributed_conversion_count
            ),
            "attributed_conversion_rate": round(
                attributed_conversion_count / len(conversion_events) * 100, 2
            )
            if conversion_events
            else None,
            "feedback_eligible_conversions": len(feedback_eligible_event_ids),
            "feedback_blocked_no_evidence": max(
                0, len(conversion_events) - len(feedback_eligible_event_ids)
            ),
            "business_duplicates_suppressed": len(suppressed_business_event_ids),
            "full_hierarchy_touches": full_hierarchy_touches,
            "partial_hierarchy_touches": partial_hierarchy_touches,
        }

        finding_filters = [OmnichannelAuditFinding.workspace_id == workspace_id]
        if period_start is not None:
            finding_filters.extend(
                [
                    OmnichannelAuditFinding.detected_at >= period_start,
                    OmnichannelAuditFinding.detected_at <= period_end,
                ]
            )
        audit_findings = list(
            (
                await self.db.scalars(
                    select(OmnichannelAuditFinding).where(*finding_filters)
                )
            ).all()
        )
        active_states = {"open", "retry_scheduled", "waiting_human"}
        active_findings = [
            finding for finding in audit_findings if finding.state in active_states
        ]
        critical_findings = [
            finding for finding in active_findings if finding.severity == "critical"
        ]
        unanswered_leads = sum(
            finding.finding_type == "unanswered_lead" for finding in active_findings
        )
        delivery_mismatches = sum(
            finding.finding_type
            in {"delivery_failed", "delivery_stalled", "projection_mismatch"}
            for finding in active_findings
        )
        duplicate_outbound = sum(
            finding.finding_type == "duplicate_outbound" for finding in active_findings
        )
        quality_penalty = (
            len(critical_findings) * 15
            + max(0, len(active_findings) - len(critical_findings)) * 5
            + duplicate_outbound * 10
        )
        service_quality = {
            "findings": len(audit_findings),
            "active_findings": len(active_findings),
            "critical_findings": len(critical_findings),
            "unanswered_leads": unanswered_leads,
            "delivery_mismatches": delivery_mismatches,
            "duplicate_outbound": duplicate_outbound,
            "resolved_findings": sum(
                finding.state == "resolved" for finding in audit_findings
            ),
            "audited_threads": len({finding.thread_id for finding in audit_findings}),
            "quality_score": max(0, 100 - min(100, quality_penalty)),
        }

        conversion_event_ids = {str(event.id) for event in conversion_events}
        conversion_business_hashes = {
            _commercial_event_identity_hash(workspace_id, identity)
            for event in conversion_events
            if (
                identity := _commercial_event_identity(
                    event,
                    _normalize_tracking_event_name(event.event_name),
                    canonical_client_id=(
                        conversion_clients[event.id].id
                        if event.id in conversion_clients
                        else None
                    ),
                )
            )
        }
        feedback_intents = list(
            (
                await self.db.scalars(
                    select(ExternalActionIntent).where(
                        ExternalActionIntent.workspace_id == workspace_id,
                        ExternalActionIntent.action_type == "ads_conversion_feedback",
                    )
                )
            ).all()
        )
        feedback_intents = [
            intent
            for intent in feedback_intents
            if period_start is None
            or str((intent.payload or {}).get("sourceEventId") or "")
            in conversion_event_ids
            or str((intent.payload or {}).get("businessEventIdentityHash") or "")
            in conversion_business_hashes
        ]
        feedback_records: List[AdsConversionFeedbackRecord] = []
        if feedback_intents:
            feedback_records = list(
                (
                    await self.db.scalars(
                        select(AdsConversionFeedbackRecord)
                        .where(
                            AdsConversionFeedbackRecord.workspace_id == workspace_id,
                            AdsConversionFeedbackRecord.intent_id.in_(
                                {intent.id for intent in feedback_intents}
                            ),
                        )
                        .order_by(
                            AdsConversionFeedbackRecord.recorded_at.asc(),
                            AdsConversionFeedbackRecord.id.asc(),
                        )
                    )
                ).all()
            )
        prepared_proof_by_intent = {
            record.intent_id: record
            for record in feedback_records
            if record.record_type == "prepared"
        }
        receipts_by_intent: Dict[UUID, List[AdsConversionFeedbackRecord]] = {}
        for record in feedback_records:
            if record.record_type == "provider_receipt":
                receipts_by_intent.setdefault(record.intent_id, []).append(record)
        duplicate_prevented = 0
        if feedback_intents:
            duplicate_filters = [
                KlmAuditEvent.workspace_id == workspace_id,
                KlmAuditEvent.event_type
                == "ads_conversion_feedback_duplicate_prevented",
                KlmAuditEvent.correlation_id.in_(
                    {intent.idempotency_key for intent in feedback_intents}
                ),
            ]
            if period_start is not None:
                duplicate_filters.append(KlmAuditEvent.occurred_at >= period_start)
            duplicate_prevented = int(
                await self.db.scalar(
                    select(func.count(func.distinct(KlmAuditEvent.correlation_id))).where(
                        *duplicate_filters
                    )
                )
                or 0
            )
        feedback_statuses: Counter[str] = Counter()
        feedback_providers: Counter[str] = Counter()
        feedback_events = []
        for intent in feedback_intents:
            payload = intent.payload or {}
            status = (
                intent.status.value if hasattr(intent.status, "value") else str(intent.status)
            )
            feedback_statuses[status] += 1
            feedback_providers[intent.provider or "unknown"] += 1
            source_event_id = str(payload.get("sourceEventId") or "")
            source_touch_event_id = str(payload.get("sourceTouchEventId") or "")
            if not source_event_id or not source_touch_event_id:
                continue
            happened_at_value = payload.get("conversionHappenedAt")
            try:
                happened_at = datetime.fromisoformat(str(happened_at_value))
            except (TypeError, ValueError):
                happened_at = intent.created_at
            provider_envelope = payload.get("providerEnvelope") or {}
            proof = prepared_proof_by_intent.get(intent.id)
            receipts = receipts_by_intent.get(intent.id, [])
            latest_receipt = receipts[-1] if receipts else None
            feedback_events.append(
                {
                    "intent_id": intent.id,
                    "provider": intent.provider or "unknown",
                    "source_event_id": source_event_id,
                    "source_touch_event_id": source_touch_event_id,
                    "conversion_event": str(payload.get("conversionEvent") or "client"),
                    "happened_at": happened_at,
                    "value": payload.get("value"),
                    "currency": str(payload.get("currency") or "BRL"),
                    "status": status,
                    "provider_event_id": str(payload.get("providerEventId") or ""),
                    "match_key_names": list(payload.get("providerMatchKeyNames") or []),
                    "payload_schema_version": str(
                        provider_envelope.get("schemaVersion")
                        or payload.get("schemaVersion")
                        or ""
                    ),
                    "matching_mode": str(payload.get("matchingMode") or "unmatched"),
                    "ready_for_dispatch": bool(payload.get("readyForDispatch")),
                    "missing_configuration": list(
                        payload.get("missingConfiguration") or []
                    ),
                    "privacy_guard": dict(payload.get("privacyGuard") or {}),
                    "payload_sha256": str(payload.get("payloadSha256") or ""),
                    "proof_state": proof.state if proof else "missing",
                    "receipt_state": latest_receipt.state if latest_receipt else None,
                    "receipt_count": len(receipts),
                    "dedupe_key": intent.idempotency_key,
                    "external_actions_executed": int(
                        payload.get("externalActionsExecuted") or 0
                    ),
                }
            )
        feedback_events.sort(key=lambda item: item["happened_at"], reverse=True)
        conversion_feedback = {
            "prepared": len(feedback_intents),
            "blocked_external": feedback_statuses["blocked_external"],
            "awaiting_approval": feedback_statuses["awaiting_approval"],
            "approved_local": feedback_statuses["approved_local"],
            "rejected": feedback_statuses["cancelled"],
            "duplicate_prevented": duplicate_prevented,
            "payload_ready": sum(
                bool((intent.payload or {}).get("readyForDispatch"))
                for intent in feedback_intents
            ),
            "configuration_blocked": sum(
                bool((intent.payload or {}).get("missingConfiguration"))
                for intent in feedback_intents
            ),
            "prepared_proofs": len(prepared_proof_by_intent),
            "provider_receipts": sum(len(items) for items in receipts_by_intent.values()),
            "failed_receipts": sum(
                record.state in {"partial_failure", "rejected", "retryable"}
                for items in receipts_by_intent.values()
                for record in items
            ),
            "providers": dict(sorted(feedback_providers.items())),
        }

        funnel_steps = _funnel_steps(journey_events)
        insights: List[Dict[str, Any]] = []
        tracking_rate = attribution_coverage["tracking_rate"]
        if tracking_rate is not None and tracking_rate < 90:
            insights.append(
                {
                    "key": "tracking_coverage_gap",
                    "severity": "critical" if tracking_rate < 70 else "warning",
                    "title": "Conversas sem origem comprovada",
                    "detail": (
                        f"{attribution_coverage['untracked_conversations']} jornadas do período "
                        "não possuem identificador ou hierarquia de mídia suficiente."
                    ),
                    "recommended_action": (
                        "Revisar UTMs, click IDs, formulários e webhooks nas origens "
                        "sem evidência."
                    ),
                    "metric": {"trackingRate": tracking_rate},
                    "evidence": {
                        "tracked": attribution_coverage["tracked_conversations"],
                        "untracked": attribution_coverage["untracked_conversations"],
                    },
                }
            )
        if attribution_coverage["unattributed_conversions"]:
            insights.append(
                {
                    "key": "conversion_without_attribution",
                    "severity": "critical",
                    "title": "Vendas sem origem atribuível",
                    "detail": (
                        f"{attribution_coverage['unattributed_conversions']} conversões não "
                        "podem ser devolvidas ao provedor com segurança."
                    ),
                    "recommended_action": (
                        "Corrigir a captura da origem antes de liberar o feedback externo."
                    ),
                    "metric": {
                        "unattributedConversions": attribution_coverage[
                            "unattributed_conversions"
                        ]
                    },
                    "evidence": {"periodDays": period_days},
                }
            )
        funnel_drop = max(
            (step for step in funnel_steps if step["drop_off_rate"] is not None),
            key=lambda step: step["drop_off_rate"],
            default=None,
        )
        if funnel_drop and funnel_drop["drop_off_from_previous"] > 0:
            insights.append(
                {
                    "key": "journey_bottleneck",
                    "severity": "opportunity",
                    "title": f"Gargalo antes de {funnel_drop['stage']}",
                    "detail": (
                        f"{funnel_drop['drop_off_from_previous']} jornadas não avançaram; "
                        f"queda de {funnel_drop['drop_off_rate']}%."
                    ),
                    "recommended_action": (
                        "Abrir as conversas da etapa e comparar origem, resposta, SLA "
                        "e objeções."
                    ),
                    "metric": funnel_drop,
                    "evidence": {"funnel": funnel_steps},
                }
            )
        top_attribution = next(
            (
                row
                for row in attribution_rows
                if row["evidence_level"] != "unattributed"
                and (row["clients"] or row["attributable_revenue"])
            ),
            None,
        )
        if top_attribution:
            insights.append(
                {
                    "key": "top_attribution",
                    "severity": "opportunity",
                    "title": "Origem com resultado comprovado",
                    "detail": (
                        f"{top_attribution['campaign_name'] or top_attribution['provider_label']} "
                        f"atribuiu {top_attribution['clients']} clientes e "
                        f"R$ {top_attribution['attributable_revenue']:.2f}."
                    ),
                    "recommended_action": (
                        "Comparar criativo, público, CAC e capacidade de atendimento "
                        "antes de escalar."
                    ),
                    "metric": {
                        "clients": top_attribution["clients"],
                        "revenue": top_attribution["attributable_revenue"],
                        "roas": top_attribution["roas"],
                    },
                    "evidence": {
                        "provider": top_attribution["provider"],
                        "campaignId": top_attribution["campaign_id"],
                        "creativeId": top_attribution["creative_id"],
                    },
                }
            )
        if active_findings:
            insights.append(
                {
                    "key": "service_quality_findings",
                    "severity": "critical" if critical_findings else "warning",
                    "title": "Falhas de atendimento afetam a leitura comercial",
                    "detail": f"O auditor mantém {len(active_findings)} achados ativos no período.",
                    "recommended_action": (
                        "Resolver leads sem resposta e divergências de entrega antes "
                        "de comparar campanhas."
                    ),
                    "metric": service_quality,
                    "evidence": {
                        "findingTypes": dict(
                            Counter(f.finding_type for f in active_findings)
                        )
                    },
                }
            )

        total_metrics = _finalize_tracking_metrics(total_bucket)
        total_events = total_metrics.pop("events")
        return {
            "period_days": period_days,
            "period_start": period_start,
            "total_events": total_events,
            **total_metrics,
            "identified_journeys": len(journeys),
            "by_channel": by_channel,
            "event_coverage": [
                {
                    "event": event_name,
                    "count": event_counts.get(event_name, 0),
                    "covered": event_counts.get(event_name, 0) > 0,
                }
                for event_name in TRACKING_COVERAGE_EVENTS
            ],
            "time_trend": time_trend,
            "daily_channels": daily_channels,
            "journeys": journeys,
            "remarketing": remarketing,
            "attribution_coverage": attribution_coverage,
            "attribution_rows": attribution_rows,
            "funnel_steps": funnel_steps,
            "insights": insights,
            "service_quality": service_quality,
            "conversion_feedback": conversion_feedback,
            "feedback_events": feedback_events,
            "contains_local_fixture_data": contains_local_fixture_data,
            "generated_at": period_end,
        }

    async def prepare_conversion_feedback(
        self,
        workspace_id: UUID,
        user_id: UUID,
        period_days: Optional[int] = None,
    ) -> Dict[str, Any]:
        if period_days is not None and period_days < 1:
            raise ValueError("O período deve ter ao menos um dia.")
        filters = [
            AcquisitionEvent.workspace_id == workspace_id,
            AcquisitionEvent.happened_at <= _now(),
        ]
        if period_days is not None:
            filters.append(AcquisitionEvent.happened_at >= _now() - timedelta(days=period_days))
        events = list(
            (
                await self.db.scalars(
                    select(AcquisitionEvent)
                    .where(*filters)
                    .order_by(AcquisitionEvent.happened_at.asc(), AcquisitionEvent.id.asc())
                )
            ).all()
        )
        raw_conversions = [
            event
            for event in events
            if _normalize_tracking_event_name(event.event_name) in TRACKING_CONVERSION_EVENTS
        ]
        raw_conversion_clients = await self._batch_conversion_clients(
            workspace_id, raw_conversions
        )
        conversions, suppressed_business_event_ids = _canonicalize_commercial_events(
            raw_conversions,
            canonical_clients=raw_conversion_clients,
        )
        conversion_clients = {
            event.id: raw_conversion_clients[event.id]
            for event in conversions
            if event.id in raw_conversion_clients
        }
        prepared = 0
        deduplicated = 0
        skipped = 0
        providers: Counter[str] = Counter()
        (
            _reporting_touches,
            feedback_touches,
            _resolved_clients,
        ) = await self._batch_conversion_touches(
            workspace_id,
            conversions,
            conversion_clients=conversion_clients,
        )
        workspace_settings = (
            await self.db.scalar(
                select(Workspace.settings).where(Workspace.id == workspace_id)
            )
            or {}
        )
        for event in conversions:
            attribution = feedback_touches.get(event.id)
            if attribution is None:
                skipped += 1
                continue
            intent, was_deduplicated = await self._ensure_conversion_feedback_intent(
                workspace_id,
                user_id,
                event,
                attribution=attribution,
                client=conversion_clients.get(event.id),
                workspace_settings=workspace_settings,
            )
            if intent is None:
                skipped += 1
                continue
            providers[intent.provider or "unknown"] += 1
            if was_deduplicated:
                deduplicated += 1
            else:
                prepared += 1
        return {
            "scanned": len(conversions),
            "prepared": prepared,
            "deduplicated": deduplicated,
            "skipped_without_evidence": skipped,
            "suppressed_business_duplicates": len(suppressed_business_event_ids),
            "providers": dict(sorted(providers.items())),
            "external_actions_executed": 0,
        }

    async def get_ads_measurement_config(self, workspace_id: UUID) -> Dict[str, Any]:
        """Read the workspace's Meta CAPI measurement config, secrets masked."""
        connection = await self.db.scalar(
            select(IntegrationConnection).where(
                IntegrationConnection.workspace_id == workspace_id,
                IntegrationConnection.provider == IntegrationProvider.custom,
                IntegrationConnection.label == ADS_MEASUREMENT_LABEL,
            )
        )
        if connection is None:
            return {
                "configured": False,
                "dataset_id": None,
                "pixel_id": None,
                "access_token_configured": False,
                "access_token_last4": None,
                "updated_at": None,
            }
        stored = connection.settings or {}
        token = reveal_integration_secret(stored, "apiToken")
        dataset_id = stored.get("datasetId") or None
        pixel_id = stored.get("pixelId") or None
        return {
            "configured": bool((dataset_id or pixel_id) and token),
            "dataset_id": dataset_id,
            "pixel_id": pixel_id,
            "access_token_configured": bool(token),
            "access_token_last4": token[-4:] if token and len(token) >= 4 else None,
            "updated_at": connection.updated_at,
        }

    async def save_ads_measurement_config(
        self,
        workspace_id: UUID,
        user_id: UUID | None,
        *,
        dataset_id: str | None,
        pixel_id: str | None,
        access_token: str | None,
    ) -> Dict[str, Any]:
        connection = await self.db.scalar(
            select(IntegrationConnection).where(
                IntegrationConnection.workspace_id == workspace_id,
                IntegrationConnection.provider == IntegrationProvider.custom,
                IntegrationConnection.label == ADS_MEASUREMENT_LABEL,
            )
        )
        raw_settings: Dict[str, Any] = {}
        if dataset_id is not None:
            raw_settings["datasetId"] = dataset_id.strip() or None
        elif connection is not None:
            raw_settings["datasetId"] = (connection.settings or {}).get("datasetId")
        if pixel_id is not None:
            raw_settings["pixelId"] = pixel_id.strip() or None
        elif connection is not None:
            raw_settings["pixelId"] = (connection.settings or {}).get("pixelId")
        if access_token:
            raw_settings["apiToken"] = access_token.strip()
        elif connection is not None:
            existing_token = reveal_integration_secret(connection.settings, "apiToken")
            if existing_token:
                raw_settings["apiToken"] = existing_token
        protected_settings = protect_integration_settings(raw_settings)
        if connection is None:
            connection = IntegrationConnection(
                workspace_id=workspace_id,
                provider=IntegrationProvider.custom,
                status=IntegrationStatus.connected,
                label=ADS_MEASUREMENT_LABEL,
                settings=protected_settings,
            )
            self.db.add(connection)
        else:
            connection.settings = protected_settings
            connection.status = IntegrationStatus.connected
            connection.updated_at = _now()
        await self.db.flush()
        await self.record_audit(
            workspace_id=workspace_id,
            actor_user_id=user_id,
            actor_type="human" if user_id else "system",
            actor_label="Track KLM",
            event_type="ads_measurement_config_saved",
            entity_type="integration_connection",
            entity_id=connection.id,
            summary="Configuração de mensuração Meta (CAPI) atualizada.",
            after_data={
                "datasetIdConfigured": bool(protected_settings.get("datasetId")),
                "pixelIdConfigured": bool(protected_settings.get("pixelId")),
                "accessTokenConfigured": bool(protected_settings.get("apiTokenEncrypted")),
            },
        )
        return await self.get_ads_measurement_config(workspace_id)

    async def dispatch_prepared_conversion_feedback(
        self, workspace_id: UUID, user_id: UUID | None
    ) -> Dict[str, Any]:
        """Consume prepared `ads_conversion_feedback` intents right now.

        Reuses the same dispatcher the periodic worker runs; gated by
        `external_delivery_allowed()` and local approval, never a real send
        unless both are true.
        """
        from app.workers.ads_feedback_tasks import dispatch_workspace_conversion_feedback

        counts = await dispatch_workspace_conversion_feedback(
            workspace_id, db=self.db
        )
        await self.record_audit(
            workspace_id=workspace_id,
            actor_user_id=user_id,
            actor_type="human" if user_id else "system",
            actor_label="Track KLM",
            event_type="ads_conversion_feedback_dispatch_requested",
            entity_type="external_action_intent",
            entity_id=None,
            summary=(
                f"Despacho manual: {counts.get('preparedProcessed', 0)} processada(s), "
                f"{counts.get('blockedSimulated', 0)} bloqueada(s)/simulada(s), "
                f"{counts.get('dispatchedReal', 0)} enviada(s) de verdade."
            ),
            after_data=counts,
        )
        return {
            "prepared_processed": counts.get("preparedProcessed", 0),
            "already_dispatched": counts.get("alreadyDispatched", 0),
            "blocked_simulated": counts.get("blockedSimulated", 0),
            "dispatched_real": counts.get("dispatchedReal", 0),
            "failed": counts.get("failed", 0),
            "external_actions_enabled": external_delivery_allowed(),
        }

    async def create_technical_request(
        self, workspace_id: UUID, user_id: UUID, payload: TechnicalRequestCreate
    ) -> TechnicalRequest:
        request = TechnicalRequest(
            workspace_id=workspace_id,
            created_by_user_id=user_id,
            **payload.model_dump(),
        )
        self.db.add(request)
        await self.db.flush()
        await self.record_audit(
            workspace_id=workspace_id,
            actor_user_id=user_id,
            actor_type="human",
            actor_label="Usuário KLM",
            event_type="technical_request_created",
            entity_type="technical_request",
            entity_id=request.id,
            summary=f"Solicitação técnica '{request.title}' criada.",
        )
        return request

    async def list_technical_requests(self, workspace_id: UUID) -> List[TechnicalRequest]:
        result = await self.db.execute(
            select(TechnicalRequest)
            .where(TechnicalRequest.workspace_id == workspace_id)
            .order_by(TechnicalRequest.created_at.desc())
        )
        return list(result.scalars().all())

    async def update_technical_request(
        self,
        workspace_id: UUID,
        request_id: UUID,
        user_id: UUID,
        payload: TechnicalRequestUpdate,
    ) -> Optional[TechnicalRequest]:
        request = await self.db.scalar(
            select(TechnicalRequest).where(
                TechnicalRequest.id == request_id,
                TechnicalRequest.workspace_id == workspace_id,
            )
        )
        if not request:
            return None
        changes = payload.model_dump(exclude_unset=True)
        assigned_to_user_id = changes.get("assigned_to_user_id")
        if assigned_to_user_id and not await self._workspace_member(
            workspace_id, assigned_to_user_id
        ):
            raise LookupError("Responsável técnico não encontrado no workspace.")
        before = {key: getattr(request, key) for key in changes}
        for key, value in changes.items():
            setattr(request, key, value)
        request.updated_at = _now()
        await self.db.flush()
        await self.record_audit(
            workspace_id=workspace_id,
            actor_user_id=user_id,
            actor_type="human",
            actor_label="Usuário KLM",
            event_type="technical_request_updated",
            entity_type="technical_request",
            entity_id=request.id,
            summary=f"Solicitação técnica '{request.title}' atualizada.",
            before_data={key: str(value) for key, value in before.items()},
            after_data={key: str(value) for key, value in changes.items()},
        )
        return request

    async def _legal_artifact_source_snapshot(
        self,
        workspace_id: UUID,
        matter: Matter,
    ) -> Dict[str, Any]:
        honorarium = await self.db.scalar(
            select(HonorariumEstimate).where(HonorariumEstimate.matter_id == matter.id)
        )
        projection = await self.db.scalar(
            select(RevenueProjection).where(RevenueProjection.matter_id == matter.id)
        )
        return {
            "capturedAt": _now().isoformat(),
            "matterId": str(matter.id),
            "workspaceId": str(workspace_id),
            "matterUpdatedAt": matter.updated_at.isoformat() if matter.updated_at else None,
            "demandType": matter.demand_type,
            "practiceArea": matter.practice_area.value,
            "stage": matter.column_id.value,
            "microStatus": matter.micro_status.value,
            "healthSubtheme": matter.health_subtheme.value if matter.health_subtheme else None,
            "healthObjectName": matter.health_object_name,
            "healthInsurer": matter.health_insurer,
            "healthDenialReason": matter.health_denial_reason,
            "medicalUrgencyHours": matter.medical_urgency_hours,
            "economicValueAnnual": str(matter.economic_value_annual)
            if matter.economic_value_annual is not None
            else None,
            "honorarium": {
                "fixedFee": str(honorarium.fixed_fee)
                if honorarium and honorarium.fixed_fee is not None
                else None,
                "quotaLitisPercentage": str(honorarium.quota_litis_percentage)
                if honorarium and honorarium.quota_litis_percentage is not None
                else None,
                "totalEstimatedValue": str(honorarium.total_estimated_value)
                if honorarium and honorarium.total_estimated_value is not None
                else None,
            },
            "projection": {
                "version": projection.projection_version if projection else None,
                "premises": dict(projection.projection_premises or {}) if projection else {},
            },
        }

    async def list_legal_artifacts(
        self,
        workspace_id: UUID,
        *,
        matter_id: Optional[UUID] = None,
        legal_demand_id: Optional[UUID] = None,
        kind: Optional[str] = None,
    ) -> List[LegalArtifactVersion]:
        conditions = [LegalArtifactVersion.workspace_id == workspace_id]
        if matter_id is not None:
            conditions.append(LegalArtifactVersion.matter_id == matter_id)
        if legal_demand_id is not None:
            conditions.append(LegalArtifactVersion.legal_demand_id == legal_demand_id)
        if kind is not None:
            conditions.append(LegalArtifactVersion.kind == kind)
        result = await self.db.execute(
            select(LegalArtifactVersion)
            .where(*conditions)
            .order_by(
                LegalArtifactVersion.created_at.desc(),
                LegalArtifactVersion.version.desc(),
            )
        )
        return list(result.scalars().all())

    async def get_legal_artifact(
        self, workspace_id: UUID, artifact_id: UUID
    ) -> Optional[LegalArtifactVersion]:
        return await self.db.scalar(
            select(LegalArtifactVersion).where(
                LegalArtifactVersion.id == artifact_id,
                LegalArtifactVersion.workspace_id == workspace_id,
            )
        )

    async def create_legal_artifact_version(
        self,
        workspace_id: UUID,
        user_id: UUID,
        payload: LegalArtifactVersionCreate,
    ) -> LegalArtifactVersion:
        matter = await self._matter(workspace_id, payload.matter_id)
        if not matter:
            raise LookupError("Caso não encontrado no workspace.")

        artifact_key = payload.artifact_key or uuid4()
        # LegalArtifactVersion is immutable and the runtime role intentionally
        # has no UPDATE privilege on it, so a row-level ``FOR UPDATE`` is both
        # semantically wrong and rejected by PostgreSQL. Serialize revisions
        # on the logical aggregate instead; the lock is released with the
        # surrounding transaction and is a no-op on SQLite.
        await acquire_transaction_advisory_lock(
            self.db,
            namespace="legal-artifact-version",
            value=f"{workspace_id}:{artifact_key}",
        )
        latest = await self.db.scalar(
            self._latest_legal_artifact_query(
                workspace_id=workspace_id,
                artifact_key=artifact_key,
            )
        )
        if payload.artifact_key is not None and latest is None:
            raise LookupError("Documento jurídico não encontrado no workspace.")
        if latest is not None:
            if latest.matter_id != matter.id or latest.kind != payload.kind:
                raise ValueError("A nova versão não corresponde ao caso e ao tipo originais.")
            if payload.expected_version != latest.version:
                raise ValueError(f"Versão desatualizada; a versão atual é {latest.version}.")
            version = latest.version + 1
        else:
            if payload.expected_version is not None:
                raise ValueError("expectedVersion só pode ser usado em uma nova versão.")
            version = 1

        source_snapshot = await self._legal_artifact_source_snapshot(workspace_id, matter)
        artifact = LegalArtifactVersion(
            workspace_id=workspace_id,
            matter_id=matter.id,
            artifact_key=artifact_key,
            version=version,
            kind=payload.kind,
            title=payload.title,
            content=payload.content,
            source_snapshot=source_snapshot,
            created_by_user_id=user_id,
            evidence_schema_version=LEGAL_ARTIFACT_EVIDENCE_SCHEMA_VERSION,
            evidence_sha256="",
            # ── Editor jurídico (Onda 6, §9 item 1) — metadado de
            # proveniência/consulta; ver comentário em app/models/klm_operations.py.
            legal_demand_id=getattr(payload, "legal_demand_id", None),
            author_type=getattr(payload, "author_type", "human") or "human",
            author_agent_key=getattr(payload, "author_agent_key", None),
            office_template_key=getattr(payload, "office_template_key", None),
            office_template_object_id=getattr(payload, "office_template_object_id", None),
            office_template_object_version=getattr(
                payload, "office_template_object_version", None
            ),
            protocolability_state=getattr(payload, "protocolability_state", "bloqueado")
            or "bloqueado",
            state=LegalArtifactState(
                workspace_id=workspace_id,
                matter_id=matter.id,
                status="draft",
            ),
        )
        try:
            async with self.db.begin_nested():
                self.db.add(artifact)
                await self.db.flush()
        except IntegrityError as error:
            raise ValueError(
                "Outra versão foi criada simultaneamente; recarregue o documento."
            ) from error

        await self.record_audit(
            workspace_id=workspace_id,
            actor_user_id=user_id,
            actor_type="human",
            actor_label="Usuário KLM",
            event_type="legal_artifact_version_created",
            entity_type="legal_artifact_version",
            entity_id=artifact.id,
            summary=f"{payload.kind} '{payload.title}' registrado na versão {version}.",
            after_data={
                "artifactKey": str(artifact_key),
                "version": version,
                "matterId": str(matter.id),
            },
        )
        return artifact

    @staticmethod
    def _latest_legal_artifact_query(
        *,
        workspace_id: UUID,
        artifact_key: UUID,
    ):
        return (
            select(LegalArtifactVersion)
            .where(
                LegalArtifactVersion.workspace_id == workspace_id,
                LegalArtifactVersion.artifact_key == artifact_key,
            )
            .order_by(LegalArtifactVersion.version.desc())
            .limit(1)
        )

    async def submit_legal_artifact_for_approval(
        self,
        workspace_id: UUID,
        user_id: UUID,
        artifact_id: UUID,
    ) -> Tuple[LegalArtifactVersion, ApprovalQueueItem]:
        artifact = await self.get_legal_artifact(workspace_id, artifact_id)
        if not artifact:
            raise LookupError("Documento jurídico não encontrado no workspace.")
        artifact_state = artifact.state
        if artifact_state is None:
            raise RuntimeError("Documento jurídico sem projeção de estado persistida.")
        if artifact_state.status == "review_pending" and artifact_state.approval_item_id:
            approval = await self.db.scalar(
                select(ApprovalQueueItem).where(
                    ApprovalQueueItem.id == artifact_state.approval_item_id,
                    ApprovalQueueItem.matter_id == artifact.matter_id,
                )
            )
            if approval:
                return artifact, approval
        newer_id = await self.db.scalar(
            select(LegalArtifactVersion.id)
            .where(
                LegalArtifactVersion.workspace_id == workspace_id,
                LegalArtifactVersion.artifact_key == artifact.artifact_key,
                LegalArtifactVersion.version > artifact.version,
            )
            .limit(1)
        )
        if newer_id:
            raise ValueError("Somente a versão mais recente pode ser enviada para aprovação.")
        if artifact_state.status != "draft":
            raise ValueError("Esta versão não está disponível para uma nova aprovação.")

        approval = await ApprovalService(self.db, workspace_id).create(
            ApprovalCreate(
                matter_id=artifact.matter_id,
                approval_type=f"legal_artifact:{artifact.kind}",
                title=f"Revisar {artifact.title} · v{artifact.version}",
                description="Versão jurídica persistida no Meu Executivo; nenhuma entrega externa foi executada.",
                priority=ApprovalPriority.alta
                if artifact.kind == "petition"
                else ApprovalPriority.media,
                requested_by=user_id,
                document_version_url=f"klm-artifact:{artifact.id}",
            )
        )
        artifact_state.status = "review_pending"
        artifact_state.approval_item_id = approval.id
        artifact_state.approval_matter_id = artifact.matter_id
        artifact_state.updated_at = _now()
        await self.db.flush()
        await self.record_audit(
            workspace_id=workspace_id,
            actor_user_id=user_id,
            actor_type="human",
            actor_label="Usuário KLM",
            event_type="legal_artifact_submitted",
            entity_type="legal_artifact_version",
            entity_id=artifact.id,
            summary=f"Versão {artifact.version} de '{artifact.title}' enviada para aprovação humana.",
            after_data={"approvalId": str(approval.id)},
        )
        return artifact, approval

    @staticmethod
    def _normalize_case_number(value: str) -> str:
        normalized = re.sub(r"[^A-Za-z0-9]", "", value).upper()
        if len(normalized) < 5:
            raise ValueError("Número do processo inválido.")
        return normalized

    async def list_process_monitoring_records(
        self, workspace_id: UUID, matter_id: Optional[UUID] = None
    ) -> List[ProcessMonitoringRecord]:
        conditions = [ProcessMonitoringRecord.workspace_id == workspace_id]
        if matter_id is not None:
            conditions.append(ProcessMonitoringRecord.matter_id == matter_id)
        result = await self.db.execute(
            select(ProcessMonitoringRecord)
            .where(*conditions)
            .order_by(
                ProcessMonitoringRecord.next_deadline_at.asc().nullslast(),
                ProcessMonitoringRecord.updated_at.desc(),
            )
        )
        return list(result.scalars().all())

    async def create_process_monitoring_record(
        self,
        workspace_id: UUID,
        user_id: UUID,
        payload: ProcessMonitoringCreate,
    ) -> ProcessMonitoringRecord:
        if not await self._matter(workspace_id, payload.matter_id):
            raise LookupError("Caso não encontrado no workspace.")
        now = _now()
        events = []
        if payload.last_event_summary:
            events.append(
                {
                    "id": str(uuid4()),
                    "occurredAt": now.isoformat(),
                    "title": "Movimentação inicial",
                    "description": payload.last_event_summary,
                    "source": "manual_local",
                    "recordedByUserId": str(user_id),
                }
            )
        record = ProcessMonitoringRecord(
            workspace_id=workspace_id,
            matter_id=payload.matter_id,
            created_by_user_id=user_id,
            case_number=payload.case_number,
            case_number_normalized=self._normalize_case_number(payload.case_number),
            court=payload.court,
            status=payload.status,
            next_deadline_at=payload.next_deadline_at,
            last_checked_at=now,
            last_event_summary=payload.last_event_summary,
            source_state="manual_local",
            events=events,
        )
        try:
            async with self.db.begin_nested():
                self.db.add(record)
                await self.db.flush()
        except IntegrityError as error:
            raise ValueError("Este processo já está monitorado no workspace.") from error
        await self.record_audit(
            workspace_id=workspace_id,
            actor_user_id=user_id,
            actor_type="human",
            actor_label="Usuário KLM",
            event_type="process_monitor_created",
            entity_type="process_monitoring_record",
            entity_id=record.id,
            summary=f"Processo {record.case_number} vinculado ao caso.",
            after_data={"matterId": str(record.matter_id), "sourceState": record.source_state},
        )
        return record

    async def update_process_monitoring_record(
        self,
        workspace_id: UUID,
        user_id: UUID,
        record_id: UUID,
        payload: ProcessMonitoringUpdate,
    ) -> Optional[ProcessMonitoringRecord]:
        record = await self.db.scalar(
            select(ProcessMonitoringRecord).where(
                ProcessMonitoringRecord.id == record_id,
                ProcessMonitoringRecord.workspace_id == workspace_id,
            )
        )
        if not record:
            return None
        changes = payload.model_dump(exclude_unset=True)
        before = {key: getattr(record, key) for key in changes}
        for key, value in changes.items():
            setattr(record, key, value)
        record.last_checked_at = _now()
        record.updated_at = _now()
        await self.db.flush()
        await self.record_audit(
            workspace_id=workspace_id,
            actor_user_id=user_id,
            actor_type="human",
            actor_label="Usuário KLM",
            event_type="process_monitor_updated",
            entity_type="process_monitoring_record",
            entity_id=record.id,
            summary=f"Monitoramento do processo {record.case_number} atualizado.",
            before_data={key: str(value) for key, value in before.items()},
            after_data={key: str(value) for key, value in changes.items()},
        )
        return record

    async def add_process_monitoring_event(
        self,
        workspace_id: UUID,
        user_id: UUID,
        record_id: UUID,
        payload: ProcessMonitoringEventCreate,
    ) -> Optional[ProcessMonitoringRecord]:
        record = await self.db.scalar(
            select(ProcessMonitoringRecord)
            .where(
                ProcessMonitoringRecord.id == record_id,
                ProcessMonitoringRecord.workspace_id == workspace_id,
            )
            .with_for_update()
        )
        if not record:
            return None
        occurred_at = payload.occurred_at or _now()
        event = {
            "id": str(uuid4()),
            "occurredAt": occurred_at.isoformat(),
            "title": payload.title,
            "description": payload.description,
            "source": payload.source,
            "recordedByUserId": str(user_id),
        }
        record.events = [*list(record.events or []), event]
        record.last_event_summary = payload.description or payload.title
        record.last_checked_at = _now()
        record.updated_at = _now()
        await self.db.flush()
        await self.record_audit(
            workspace_id=workspace_id,
            actor_user_id=user_id,
            actor_type="human",
            actor_label="Usuário KLM",
            event_type="process_event_added",
            entity_type="process_monitoring_record",
            entity_id=record.id,
            summary=f"Movimentação '{payload.title}' registrada no processo {record.case_number}.",
            after_data={"eventId": event["id"], "source": payload.source},
        )
        return record

    async def list_activity(self, workspace_id: UUID, limit: int = 200) -> List[KlmAuditEvent]:
        result = await self.db.execute(
            select(KlmAuditEvent)
            .where(KlmAuditEvent.workspace_id == workspace_id)
            .order_by(KlmAuditEvent.occurred_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def create_external_intent(
        self, workspace_id: UUID, user_id: UUID, payload: ExternalActionIntentCreate
    ) -> Tuple[ExternalActionIntent, bool]:
        existing = await self.db.scalar(
            select(ExternalActionIntent).where(
                ExternalActionIntent.workspace_id == workspace_id,
                ExternalActionIntent.idempotency_key == payload.idempotency_key,
            )
        )
        if existing:
            _require_matching_external_intent_replay(existing, user_id, payload)
            return existing, True
        if payload.matter_id and not await self._matter(workspace_id, payload.matter_id):
            raise LookupError("Caso não encontrado no workspace.")
        intent = ExternalActionIntent(
            workspace_id=workspace_id,
            requested_by_user_id=user_id,
            status=(
                ExternalActionIntentStatus.awaiting_approval
                if external_delivery_allowed()
                else ExternalActionIntentStatus.blocked_external
            ),
            **payload.model_dump(),
        )
        try:
            async with self.db.begin_nested():
                self.db.add(intent)
                await self.db.flush()
        except IntegrityError:
            existing = await self.db.scalar(
                select(ExternalActionIntent).where(
                    ExternalActionIntent.workspace_id == workspace_id,
                    ExternalActionIntent.idempotency_key == payload.idempotency_key,
                )
            )
            if existing:
                _require_matching_external_intent_replay(existing, user_id, payload)
                return existing, True
            raise
        await self.record_audit(
            workspace_id=workspace_id,
            actor_user_id=user_id,
            actor_type="human",
            actor_label="Usuário KLM",
            event_type="external_intent_created",
            entity_type="external_action_intent",
            entity_id=intent.id,
            summary=f"Intenção externa '{intent.action_type}' registrada sem execução.",
            correlation_id=intent.idempotency_key,
        )
        return intent, False

    async def list_external_intents(self, workspace_id: UUID) -> List[ExternalActionIntent]:
        result = await self.db.execute(
            select(ExternalActionIntent)
            .where(ExternalActionIntent.workspace_id == workspace_id)
            .order_by(ExternalActionIntent.created_at.desc())
        )
        return list(result.scalars().all())

    async def approve_intent_locally(
        self, workspace_id: UUID, intent_id: UUID, user_id: UUID
    ) -> Optional[ExternalActionIntent]:
        intent = await self.db.scalar(
            select(ExternalActionIntent).where(
                ExternalActionIntent.id == intent_id,
                ExternalActionIntent.workspace_id == workspace_id,
            )
        )
        if not intent:
            return None
        intent.status = ExternalActionIntentStatus.approved_local
        intent.reason = "Aprovado somente na simulação local; entrega externa continua bloqueada."
        intent.updated_at = _now()
        await self.db.flush()
        await self.record_audit(
            workspace_id=workspace_id,
            actor_user_id=user_id,
            actor_type="human",
            actor_label="Usuário KLM",
            event_type="external_intent_approved_local",
            entity_type="external_action_intent",
            entity_id=intent.id,
            summary="Intenção aprovada localmente sem execução externa.",
        )
        return intent

    async def reject_intent_locally(
        self, workspace_id: UUID, intent_id: UUID, user_id: UUID
    ) -> Optional[ExternalActionIntent]:
        intent = await self.db.scalar(
            select(ExternalActionIntent).where(
                ExternalActionIntent.id == intent_id,
                ExternalActionIntent.workspace_id == workspace_id,
            )
        )
        if not intent:
            return None
        intent.status = ExternalActionIntentStatus.cancelled
        intent.reason = "Rejeitado localmente; nenhuma entrega externa foi executada."
        intent.updated_at = _now()
        await self.db.flush()
        await self.record_audit(
            workspace_id=workspace_id,
            actor_user_id=user_id,
            actor_type="human",
            actor_label="Usuário KLM",
            event_type="external_intent_rejected_local",
            entity_type="external_action_intent",
            entity_id=intent.id,
            summary="Intenção rejeitada localmente sem execução externa.",
        )
        return intent

    async def dashboard(self, workspace_id: UUID) -> Dict[str, Any]:
        active_matter_filter = (
            Matter.workspace_id == workspace_id,
            Matter.deleted_at.is_(None),
        )
        now = _now()
        stats = (
            await self.db.execute(
                select(
                    select(func.count(Matter.id))
                    .where(*active_matter_filter)
                    .scalar_subquery()
                    .label("leads"),
                    select(func.coalesce(func.sum(case((Matter.lead_score >= 60, 1), else_=0)), 0))
                    .where(*active_matter_filter)
                    .scalar_subquery()
                    .label("qualified"),
                    select(func.count(OmnichannelThread.id))
                    .where(
                        OmnichannelThread.workspace_id == workspace_id,
                        OmnichannelThread.status.notin_(
                            [ThreadStatus.closed, ThreadStatus.blocked]
                        ),
                    )
                    .scalar_subquery()
                    .label("active_threads"),
                    select(func.count(ApprovalQueueItem.id))
                    .join(Matter, ApprovalQueueItem.matter_id == Matter.id)
                    .where(
                        *active_matter_filter,
                        ApprovalQueueItem.status == ApprovalStatus.pending,
                    )
                    .scalar_subquery()
                    .label("pending_approvals"),
                    select(func.count(FollowUpTask.id))
                    .where(
                        FollowUpTask.workspace_id == workspace_id,
                        FollowUpTask.status.in_([FollowUpStatus.scheduled, FollowUpStatus.due]),
                        FollowUpTask.due_at <= now,
                    )
                    .scalar_subquery()
                    .label("due_follow_ups"),
                )
            )
        ).one()
        stage_rows = await self.db.execute(
            select(Matter.column_id, func.count(Matter.id))
            .where(*active_matter_filter)
            .group_by(Matter.column_id)
        )
        leads = int(stats.leads or 0)
        qualified = int(stats.qualified or 0)
        thread_count = int(stats.active_threads or 0)
        pending_approvals = int(stats.pending_approvals or 0)
        due_follow_ups = int(stats.due_follow_ups or 0)
        stage_counts = {
            (stage.value if hasattr(stage, "value") else str(stage)): int(count)
            for stage, count in stage_rows.all()
        }
        decisions = []
        if pending_approvals:
            decisions.append({"type": "approval", "count": pending_approvals, "priority": "high"})
        if due_follow_ups:
            decisions.append({"type": "follow_up", "count": due_follow_ups, "priority": "high"})
        return {
            "metrics": {
                "leads": leads,
                "qualified": qualified,
                "activeThreads": thread_count,
                "pendingApprovals": pending_approvals,
                "dueFollowUps": due_follow_ups,
                "externalActions": 0,
            },
            "funnel": [
                {"stage": key, "count": value} for key, value in sorted(stage_counts.items())
            ],
            "decisions": decisions,
            "runtime": get_runtime_status(),
        }

    async def funnel(self, workspace_id: UUID) -> Dict[str, Any]:
        now = _now()
        workspace = await self.db.get(Workspace, workspace_id)
        board_version = int(
            (workspace.settings if workspace else {}).get("klmFunnelBoardVersion", 0)
        )
        result = await self.db.execute(
            select(Matter)
            .options(
                selectinload(Matter.client),
                selectinload(Matter.responsible_human),
                selectinload(Matter.honorarium_estimate),
                selectinload(Matter.revenue_projection),
            )
            .where(
                Matter.workspace_id == workspace_id,
                Matter.deleted_at.is_(None),
            )
            .order_by(Matter.column_id, Matter.card_order, Matter.updated_at.desc())
        )
        matters = list(result.scalars().all())
        document_rows = await self.db.execute(
            select(Document.matter_id, func.count(Document.id))
            .join(Matter)
            .where(
                Matter.workspace_id == workspace_id,
                Matter.deleted_at.is_(None),
            )
            .group_by(Document.matter_id)
        )
        document_counts = {matter_id: int(count) for matter_id, count in document_rows.all()}
        thread_rows = await self.db.execute(
            select(OmnichannelThread).where(OmnichannelThread.workspace_id == workspace_id)
        )
        threads_by_matter = {
            thread.matter_id: thread for thread in thread_rows.scalars().all() if thread.matter_id
        }
        matter_ids = [matter.id for matter in matters]
        open_task_counts: dict[UUID, int] = {}
        upcoming_meetings: dict[UUID, Meeting] = {}
        flow_runs_by_matter: dict[UUID, list[FlowRun]] = {}
        if matter_ids:
            task_rows = await self.db.execute(
                select(Task.matter_id, func.count(Task.id))
                .join(Matter, Matter.id == Task.matter_id)
                .where(
                    Matter.workspace_id == workspace_id,
                    Matter.deleted_at.is_(None),
                    Task.status != TaskStatus.done,
                    Task.matter_id.in_(matter_ids),
                )
                .group_by(Task.matter_id)
            )
            open_task_counts = {matter_id: int(count) for matter_id, count in task_rows.all()}

            meeting_rows = await self.db.execute(
                select(Meeting)
                .where(
                    Meeting.workspace_id == workspace_id,
                    Meeting.matter_id.in_(matter_ids),
                    Meeting.status == MeetingStatus.scheduled,
                    Meeting.starts_at >= now,
                )
                .order_by(Meeting.starts_at.asc())
            )
            for meeting in meeting_rows.scalars().all():
                if meeting.matter_id and meeting.matter_id not in upcoming_meetings:
                    upcoming_meetings[meeting.matter_id] = meeting

            flow_run_rows = await self.db.execute(
                select(FlowRun)
                .options(selectinload(FlowRun.flow), selectinload(FlowRun.revision))
                .where(
                    FlowRun.workspace_id == workspace_id,
                    FlowRun.matter_id.in_(matter_ids),
                )
                .order_by(FlowRun.started_at.desc())
            )
            for flow_run in flow_run_rows.scalars().all():
                flow_runs_by_matter.setdefault(flow_run.matter_id, []).append(flow_run)
        stages = [
            (KanbanColumnId.novo_lead, "Novo lead"),
            (KanbanColumnId.triagem_sdr, "Triagem"),
            (KanbanColumnId.documentos, "Documentos"),
            (KanbanColumnId.reuniao, "Reunião"),
            (KanbanColumnId.proposta, "Proposta"),
            (KanbanColumnId.contrato, "Contrato"),
            (KanbanColumnId.pagamento, "Pagamento"),
            (KanbanColumnId.ganho, "Fechado"),
            (KanbanColumnId.perdido, "Perdido"),
        ]
        cards: List[Dict[str, Any]] = []
        for matter in matters:
            thread = threads_by_matter.get(matter.id)
            estimate = matter.honorarium_estimate
            projection = matter.revenue_projection
            meeting = upcoming_meetings.get(matter.id)
            candidate_runs = flow_runs_by_matter.get(matter.id, [])
            flow_run = next(
                (
                    item
                    for item in candidate_runs
                    if item.status in {"active", "paused", "waiting_human"}
                ),
                candidate_runs[0] if candidate_runs else None,
            )
            flow_summary: dict[str, Any] | None = None
            if flow_run:
                revision_snapshot = flow_run.revision.snapshot if flow_run.revision else {}
                flow_nodes = revision_snapshot.get("nodes") or []
                context = flow_run.context or {}
                trace = context.get("trace") if isinstance(context.get("trace"), list) else []
                completed_node_ids = {
                    str(item.get("nodeId") or "")
                    for item in trace
                    if isinstance(item, dict) and item.get("status") in {"completed", "routed"}
                }
                node_count = max(1, len(flow_nodes))
                if flow_run.status == "completed":
                    progress = 100
                else:
                    progress = min(
                        99,
                        max(
                            5 if flow_run.status in {"active", "waiting_human"} else 0,
                            round(len(completed_node_ids) / node_count * 100),
                        ),
                    )
                current_node = next(
                    (
                        item
                        for item in flow_nodes
                        if str(item.get("id") or "") == str(flow_run.current_node_id or "")
                    ),
                    None,
                )
                current_data = (
                    current_node.get("data")
                    if isinstance((current_node or {}).get("data"), dict)
                    else {}
                )
                flow_summary = {
                    "runId": str(flow_run.id),
                    "flowId": str(flow_run.flow_id),
                    "name": flow_run.flow.name if flow_run.flow else "Fluxo",
                    "status": flow_run.status,
                    "progress": progress,
                    "completedSteps": len(completed_node_ids),
                    "totalSteps": len(flow_nodes),
                    "currentStep": (
                        current_data.get("label")
                        or (current_node or {}).get("label")
                        or flow_run.current_node_id
                    ),
                    "errorStatus": (
                        context.get("lastFlowError", {}).get("status")
                        if isinstance(context.get("lastFlowError"), dict)
                        else None
                    ),
                }
            cards.append(
                {
                    "id": str(matter.id),
                    "clientId": str(matter.client_id),
                    "name": matter.client.name,
                    "practiceArea": _resolved_matter_area_key(matter),
                    "subject": (
                        matter.health_subtheme_custom
                        or (
                            matter.health_subtheme.value
                            if matter.health_subtheme
                            else None
                        )
                        or matter.legal_product_custom
                        or matter.legal_product_key
                        or matter.demand_type
                    ),
                    "object": (
                        matter.health_object_name
                        or matter.legal_object_name
                        or matter.demand_type
                    ),
                    "source": matter.lead_source or "não informado",
                    "owner": (
                        thread.owner_agent_key
                        if thread and thread.owner_agent_key
                        else matter.responsible_human.name
                        if matter.responsible_human
                        else "Sem responsável"
                    ),
                    "probability": matter.lead_score,
                    "initialFee": float(estimate.fixed_fee or 0) if estimate else 0,
                    "projectedRevenue": (
                        float(projection.projected_revenue or 0) if projection else 0
                    ),
                    "slaDueAt": thread.sla_due_at if thread else None,
                    "documents": document_counts.get(matter.id, 0),
                    "documentScore": matter.doc_score,
                    "productionScore": matter.production_score,
                    "stage": matter.column_id.value,
                    "microStatus": matter.micro_status.value,
                    "cardOrder": matter.card_order,
                    "threadId": str(thread.id) if thread else None,
                    "threadStatus": thread.status.value if thread else None,
                    "channel": thread.primary_channel.value if thread else None,
                    "unreadMessages": thread.unread_count if thread else matter.unread_messages,
                    "lastMessagePreview": thread.last_message_preview if thread else None,
                    "lastMessageAt": thread.last_message_at if thread else matter.last_interaction,
                    "assignedHumanId": str(thread.assigned_human_id)
                    if thread and thread.assigned_human_id
                    else None,
                    "assignedTeamId": str(thread.assigned_team_id)
                    if thread and thread.assigned_team_id
                    else None,
                    "nextAction": matter.next_action,
                    "nextDeadline": matter.next_deadline,
                    "priority": matter.priority.value,
                    "urgency": matter.urgency.value,
                    "classification": matter.classification.value,
                    "tags": list(matter.tags or []),
                    "openTasks": open_task_counts.get(matter.id, matter.open_tasks),
                    "isBlocked": matter.is_blocked,
                    "needsApproval": matter.needs_approval,
                    "upcomingMeetingAt": meeting.starts_at if meeting else None,
                    "meetingUrl": meeting.meeting_url if meeting else None,
                    "activeFlow": flow_summary,
                    "updatedAt": matter.updated_at,
                }
            )
        counts = {stage.value: 0 for stage, _ in stages}
        for matter in matters:
            if matter.column_id.value in counts:
                counts[matter.column_id.value] += 1
        meetings_or_later = {
            KanbanColumnId.reuniao,
            KanbanColumnId.proposta,
            KanbanColumnId.contrato,
            KanbanColumnId.pagamento,
            KanbanColumnId.ganho,
        }
        return {
            "boardVersion": board_version,
            "stages": [
                {"id": stage.value, "label": label, "count": counts[stage.value]}
                for stage, label in stages
            ],
            "cards": cards,
            "metrics": {
                "conversations": len(matters),
                "qualified": sum(1 for item in matters if item.lead_score >= 60),
                "meetings": sum(1 for item in matters if item.column_id in meetings_or_later),
                "paid": sum(1 for item in matters if item.column_id == KanbanColumnId.ganho),
                "unread": sum(thread.unread_count for thread in threads_by_matter.values()),
                "openTasks": sum(open_task_counts.values()),
                "activeFlows": sum(
                    1
                    for flow_runs in flow_runs_by_matter.values()
                    if any(
                        item.status in {"active", "paused", "waiting_human"} for item in flow_runs
                    )
                ),
                "pipelineValue": sum(
                    float(
                        (
                            item.revenue_projection.projected_revenue
                            if item.revenue_projection
                            else 0
                        )
                        or (item.honorarium_estimate.fixed_fee if item.honorarium_estimate else 0)
                        or 0
                    )
                    for item in matters
                ),
                "overdueSla": sum(
                    1
                    for thread in threads_by_matter.values()
                    if thread.sla_due_at
                    and _normalized_moment(thread.sla_due_at) < now
                    and thread.status != ThreadStatus.closed
                ),
            },
        }

    async def move_funnel_matter(
        self,
        workspace_id: UUID,
        matter_id: UUID,
        user_id: UUID,
        column_id: KanbanColumnId,
        micro_status: Optional[MicroStatus],
        operation_id: str,
        expected_board_version: int,
        source_column_id: Optional[KanbanColumnId] = None,
        before_id: Optional[UUID] = None,
        after_id: Optional[UUID] = None,
        position: int = 0,
    ) -> Optional[FunnelMoveResult]:
        allowed = {
            KanbanColumnId.novo_lead,
            KanbanColumnId.triagem_sdr,
            KanbanColumnId.documentos,
            KanbanColumnId.reuniao,
            KanbanColumnId.proposta,
            KanbanColumnId.contrato,
            KanbanColumnId.pagamento,
            KanbanColumnId.ganho,
            KanbanColumnId.perdido,
        }
        if column_id not in allowed:
            raise ValueError("Etapa fora do funil comercial KLM.")

        # One workspace row is the serialization point for all board mutations.
        # PostgreSQL holds this lock until commit, preventing lost updates.
        workspace = await self.db.scalar(
            select(Workspace)
            .where(Workspace.id == workspace_id, Workspace.deleted_at.is_(None))
            .with_for_update()
        )
        if not workspace:
            return None
        workspace_settings = dict(workspace.settings or {})
        current_version = int(workspace_settings.get("klmFunnelBoardVersion", 0))

        previous_event = await self.db.scalar(
            select(KlmAuditEvent).where(
                KlmAuditEvent.workspace_id == workspace_id,
                KlmAuditEvent.event_type == "funnel_stage_changed",
                KlmAuditEvent.correlation_id == operation_id,
            )
        )
        if previous_event:
            previous_result = previous_event.after_data or {}
            return FunnelMoveResult(
                matter_id=previous_event.entity_id or matter_id,
                column_id=KanbanColumnId(previous_result["columnId"]),
                micro_status=MicroStatus(previous_result["microStatus"]),
                card_order=int(previous_result.get("cardOrder", 0)),
                board_version=int(previous_result.get("boardVersion", current_version)),
                operation_id=operation_id,
                deduplicated=True,
            )

        if expected_board_version != current_version:
            raise FunnelVersionConflict(current_version)

        matter = await self._matter(workspace_id, matter_id)
        if not matter:
            return None
        previous = matter.column_id
        if source_column_id is not None and source_column_id != previous:
            raise FunnelVersionConflict(
                current_version,
                "O lead já está em outra etapa. O quadro será atualizado.",
            )
        target_result = await self.db.execute(
            select(Matter)
            .where(
                Matter.workspace_id == workspace_id,
                Matter.deleted_at.is_(None),
                Matter.practice_area == PracticeArea.saude_suplementar,
                Matter.column_id == column_id,
                Matter.id != matter.id,
            )
            .order_by(Matter.card_order, Matter.updated_at.desc())
        )
        target_cards = list(target_result.scalars().all())
        target_ids = [card.id for card in target_cards]
        if before_id is not None:
            if before_id not in target_ids:
                raise FunnelVersionConflict(
                    current_version,
                    "A posição de destino mudou. O quadro será atualizado.",
                )
            insert_at = target_ids.index(before_id)
        elif after_id is not None:
            if after_id not in target_ids:
                raise FunnelVersionConflict(
                    current_version,
                    "A posição de destino mudou. O quadro será atualizado.",
                )
            insert_at = target_ids.index(after_id) + 1
        else:
            insert_at = min(max(position, 0), len(target_cards))
        target_cards.insert(insert_at, matter)

        default_micro_status = {
            KanbanColumnId.novo_lead: MicroStatus.lead_novo,
            KanbanColumnId.triagem_sdr: MicroStatus.triagem_incompleta,
            KanbanColumnId.documentos: MicroStatus.aguardando_docs,
            KanbanColumnId.reuniao: MicroStatus.reuniao_agendada,
            KanbanColumnId.proposta: MicroStatus.proposta_enviada,
            KanbanColumnId.contrato: MicroStatus.contrato_enviado,
            KanbanColumnId.pagamento: MicroStatus.pagamento_pendente,
            KanbanColumnId.ganho: MicroStatus.pagamento_confirmado,
            KanbanColumnId.perdido: MicroStatus.perdido_com_motivo,
        }
        matter.column_id = column_id
        matter.micro_status = micro_status or default_micro_status[column_id]
        matter.updated_at = _now()
        for index, target_card in enumerate(target_cards):
            target_card.card_order = index

        if previous != column_id:
            previous_result = await self.db.execute(
                select(Matter)
                .where(
                    Matter.workspace_id == workspace_id,
                    Matter.deleted_at.is_(None),
                    Matter.practice_area == PracticeArea.saude_suplementar,
                    Matter.column_id == previous,
                    Matter.id != matter.id,
                )
                .order_by(Matter.card_order, Matter.updated_at.desc())
            )
            for index, previous_card in enumerate(previous_result.scalars().all()):
                previous_card.card_order = index

        next_version = current_version + 1
        workspace.settings = {
            **workspace_settings,
            "klmFunnelBoardVersion": next_version,
        }
        await self.db.flush()
        await self.record_audit(
            workspace_id=workspace_id,
            actor_user_id=user_id,
            actor_type="human",
            actor_label="Usuário KLM",
            event_type="funnel_stage_changed",
            entity_type="matter",
            entity_id=matter.id,
            summary=f"Caso movido de {previous.value} para {column_id.value} localmente.",
            correlation_id=operation_id,
            before_data={"columnId": previous.value, "boardVersion": current_version},
            after_data={
                "columnId": column_id.value,
                "microStatus": matter.micro_status.value,
                "cardOrder": matter.card_order,
                "boardVersion": next_version,
            },
        )
        return FunnelMoveResult(
            matter_id=matter.id,
            column_id=matter.column_id,
            micro_status=matter.micro_status,
            card_order=matter.card_order,
            board_version=next_version,
            operation_id=operation_id,
        )

    async def update_matter_financial_projection(
        self,
        workspace_id: UUID,
        matter_id: UUID,
        user_id: UUID,
        payload: MatterFinancialProjectionUpdate,
    ) -> Dict[str, Any]:
        matter = await self._matter(workspace_id, matter_id)
        if not matter:
            raise LookupError("Caso não encontrado no workspace.")

        pricing_policy = await MedicationPricingService(self.db).get_pricing_policy(workspace_id)
        if payload.payment_mode == "success_only":
            if not pricing_policy.allow_success_only:
                raise ValueError("O modo integralmente no êxito está desativado.")
            if payload.fixed_fee != 0:
                raise ValueError("No modo integralmente no êxito, a entrada deve ser zero.")
        else:
            if payload.fixed_fee < pricing_policy.minimum_initial_fee:
                raise ValueError(
                    f"A entrada mínima configurada é R$ {pricing_policy.minimum_initial_fee:,.2f}."
                )
            if (
                pricing_policy.maximum_initial_fee is not None
                and payload.fixed_fee > pricing_policy.maximum_initial_fee
            ):
                raise ValueError("A entrada supera o teto configurado pelo escritório.")
        if payload.injunction_milestone_fee and not pricing_policy.allow_injunction_milestone_fee:
            raise ValueError("Honorários intermediários na liminar estão desativados.")
        if payload.obligation_fee and not payload.economic_obligation:
            raise ValueError(
                "Honorários pela obrigação de fazer exigem que a obrigação econômica esteja ativa."
            )

        estimate = await self.db.scalar(
            select(HonorariumEstimate).where(HonorariumEstimate.matter_id == matter.id)
        )
        if not estimate:
            estimate = HonorariumEstimate(matter_id=matter.id)
            self.db.add(estimate)

        projection = await self.db.scalar(
            select(RevenueProjection).where(RevenueProjection.matter_id == matter.id)
        )
        if not projection:
            projection = RevenueProjection(matter_id=matter.id)
            self.db.add(projection)

        success_base = (
            payload.moral_damages_estimate or 0
            if payload.success_base_type == "moral_damages"
            else payload.economic_value_annual
            if payload.economic_obligation
            else 50_000.0
        )
        expected_success_fee = success_base * payload.success_percentage / 100
        fixed_components = (
            payload.fixed_fee + payload.injunction_milestone_fee + payload.obligation_fee
        )
        projected_revenue = fixed_components + expected_success_fee
        probability = payload.probability / 100

        estimate.contract_type = payload.payment_mode
        estimate.fixed_fee = payload.fixed_fee
        estimate.quota_litis_percentage = payload.success_percentage
        estimate.total_estimated_value = projected_revenue
        estimate.notes = (
            "Projeção interna KLM; obrigação de fazer com conteúdo econômico "
            f"{'incluída' if payload.economic_obligation else 'excluída'}; "
            f"base de êxito {payload.success_base_type}; revisão {payload.review_status}."
        )

        projection.estimated_claim_value = payload.economic_value_annual
        projection.projected_revenue = projected_revenue
        projection.success_probability = probability
        projection.confidence_level = min(0.95, 0.5 + matter.doc_score / 220)
        projection.notes = (
            f"Projeção local em estado {payload.review_status}; sem decisão jurídica automatizada."
        )
        projection.projection_version = int(projection.projection_version or 0) + 1
        projection.projection_premises = {
            "fixedFee": payload.fixed_fee,
            "successPercentage": payload.success_percentage,
            "economicValueAnnual": payload.economic_value_annual,
            "probability": payload.probability,
            "economicObligation": payload.economic_obligation,
            "paymentMode": payload.payment_mode,
            "successBaseType": payload.success_base_type,
            "moralDamagesEstimate": payload.moral_damages_estimate,
            "injunctionMilestoneFee": payload.injunction_milestone_fee,
            "obligationFee": payload.obligation_fee,
            "reviewStatus": payload.review_status,
        }

        matter.economic_value_annual = payload.economic_value_annual
        matter.lead_score = round(payload.probability)
        matter.updated_at = _now()
        await self.db.flush()

        conservative = (fixed_components + success_base * 0.12) * probability
        likely = projected_revenue * probability
        aggressive = (fixed_components + success_base * 0.3) * min(0.9, probability + 0.12)
        without_economic_value = (
            fixed_components + 50_000 * payload.success_percentage / 100
        ) * probability

        await self.record_audit(
            workspace_id=workspace_id,
            actor_user_id=user_id,
            actor_type="human",
            actor_label="Usuário KLM",
            event_type="matter_financial_projection_updated",
            entity_type="matter",
            entity_id=matter.id,
            summary="Premissas econômicas do caso atualizadas para revisão humana.",
            after_data={
                "fixedFee": payload.fixed_fee,
                "successPercentage": payload.success_percentage,
                "economicValueAnnual": payload.economic_value_annual,
                "probability": payload.probability,
                "economicObligation": payload.economic_obligation,
                "paymentMode": payload.payment_mode,
                "successBaseType": payload.success_base_type,
                "moralDamagesEstimate": payload.moral_damages_estimate,
                "injunctionMilestoneFee": payload.injunction_milestone_fee,
                "obligationFee": payload.obligation_fee,
                "reviewStatus": payload.review_status,
                "projectionVersion": projection.projection_version,
            },
        )
        return {
            "matter_id": matter.id,
            "fixed_fee": payload.fixed_fee,
            "success_percentage": payload.success_percentage,
            "economic_value_annual": payload.economic_value_annual,
            "probability": payload.probability,
            "economic_obligation": payload.economic_obligation,
            "payment_mode": payload.payment_mode,
            "success_base_type": payload.success_base_type,
            "moral_damages_estimate": payload.moral_damages_estimate,
            "injunction_milestone_fee": payload.injunction_milestone_fee,
            "obligation_fee": payload.obligation_fee,
            "review_status": payload.review_status,
            "conservative": round(conservative, 2),
            "likely": round(likely, 2),
            "aggressive": round(aggressive, 2),
            "without_economic_value": round(without_economic_value, 2),
            "projection_version": projection.projection_version,
            "updated_at": matter.updated_at,
        }

    async def _trend_14d(
        self,
        workspace_id: UUID,
        *,
        matter_ids: Optional[set[UUID]] = None,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Séries diárias (14 dias) para alimentar sparklines/gráficos, sem migration.

        Computa a partir de dados já persistidos: Matter (leads/dia) e
        AcquisitionEvent com value preenchido (receita atribuída/dia).
        """
        period_end = _now()
        window_start = (period_end - timedelta(days=13)).date()
        days = [window_start + timedelta(days=offset) for offset in range(14)]

        lead_filters = [
            Matter.workspace_id == workspace_id,
            Matter.deleted_at.is_(None),
            Matter.created_at >= period_end - timedelta(days=14),
            Matter.created_at <= period_end,
        ]
        revenue_filters = [
            AcquisitionEvent.workspace_id == workspace_id,
            AcquisitionEvent.happened_at >= period_end - timedelta(days=14),
            AcquisitionEvent.happened_at <= period_end,
            AcquisitionEvent.value.is_not(None),
        ]
        if matter_ids is not None:
            if matter_ids:
                lead_filters.append(Matter.id.in_(matter_ids))
                revenue_filters.append(AcquisitionEvent.matter_id.in_(matter_ids))
            else:
                lead_filters.append(Matter.id.is_(None))
                revenue_filters.append(AcquisitionEvent.id.is_(None))

        lead_rows = (
            await self.db.execute(
                select(Matter.created_at).where(*lead_filters)
            )
        ).all()
        leads_by_day: Dict[Any, int] = {day: 0 for day in days}
        for (created_at,) in lead_rows:
            day = _normalized_moment(created_at).date()
            if day in leads_by_day:
                leads_by_day[day] += 1

        revenue_rows = (
            await self.db.execute(
                select(AcquisitionEvent.happened_at, AcquisitionEvent.value).where(
                    *revenue_filters
                )
            )
        ).all()
        revenue_by_day: Dict[Any, float] = {day: 0.0 for day in days}
        for happened_at, value in revenue_rows:
            day = _normalized_moment(happened_at).date()
            if day in revenue_by_day:
                revenue_by_day[day] += float(value or 0)

        return {
            "leads": [
                {"date": day.isoformat(), "value": leads_by_day[day]} for day in days
            ],
            "revenue": [
                {"date": day.isoformat(), "value": round(revenue_by_day[day], 2)}
                for day in days
            ],
        }

    async def kpis(
        self,
        workspace_id: UUID,
        *,
        period_days: Optional[int] = None,
        health_subtheme: Optional[HealthSubtheme] = None,
        legal_area_key: Optional[str] = None,
        legal_product_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        period_end = _now()
        period_start = period_end - timedelta(days=period_days) if period_days else None
        matter_filters = [
            Matter.workspace_id == workspace_id,
            Matter.deleted_at.is_(None),
        ]
        canonical_legal_area = canonical_area_key(legal_area_key)
        if health_subtheme is not None:
            canonical_legal_area = "saude_suplementar"
        if canonical_legal_area is not None:
            matter_filters.append(Matter.legal_area_key == canonical_legal_area)
        if legal_product_key is not None:
            matter_filters.append(Matter.legal_product_key == legal_product_key)
        if period_start is not None:
            matter_filters.append(Matter.created_at >= period_start)
        matter_filters.append(Matter.created_at <= period_end)
        if health_subtheme is not None:
            matter_filters.append(Matter.health_subtheme == health_subtheme)
        matter_rows = await self.db.execute(select(Matter).where(*matter_filters))
        matters = list(matter_rows.scalars().all())
        matter_ids = [matter.id for matter in matters]
        finance_filters = [
            FinanceEntry.workspace_id == workspace_id,
            FinanceEntry.deleted_at.is_(None),
        ]
        meeting_filters = [Meeting.workspace_id == workspace_id]
        message_filters = [OmnichannelMessage.workspace_id == workspace_id]
        if period_start is not None:
            finance_filters.append(FinanceEntry.created_at >= period_start)
            meeting_filters.extend(
                [Meeting.starts_at >= period_start, Meeting.starts_at <= period_end]
            )
            message_filters.extend(
                [
                    OmnichannelMessage.occurred_at >= period_start,
                    OmnichannelMessage.occurred_at <= period_end,
                ]
            )
        finance_filters.append(FinanceEntry.created_at <= period_end)
        if period_start is None:
            meeting_filters.append(Meeting.starts_at <= period_end)
            message_filters.append(OmnichannelMessage.occurred_at <= period_end)
        matter_scoped = any(
            value is not None
            for value in (health_subtheme, canonical_legal_area, legal_product_key)
        )
        if matter_scoped:
            if matter_ids:
                finance_filters.append(FinanceEntry.matter_id.in_(matter_ids))
                meeting_filters.append(Meeting.matter_id.in_(matter_ids))
                message_filters.append(OmnichannelMessage.matter_id.in_(matter_ids))
            else:
                finance_filters.append(FinanceEntry.id.is_(None))
                meeting_filters.append(Meeting.id.is_(None))
                message_filters.append(OmnichannelMessage.id.is_(None))
        finance_entries = list(
            (await self.db.execute(select(FinanceEntry).where(*finance_filters))).scalars().all()
        )
        meetings = list(
            (await self.db.execute(select(Meeting).where(*meeting_filters))).scalars().all()
        )
        messages = list(
            (
                await self.db.execute(
                    select(OmnichannelMessage)
                    .where(*message_filters)
                    .order_by(
                        OmnichannelMessage.thread_id.asc(),
                        OmnichannelMessage.occurred_at.asc(),
                    )
                )
            )
            .scalars()
            .all()
        )
        projections = list(
            (await self.db.execute(select(RevenueProjection).join(Matter).where(*matter_filters)))
            .scalars()
            .all()
        )
        lead_count = len(matters)
        qualified = sum(1 for matter in matters if matter.lead_score >= 60)
        paid_matters = sum(1 for matter in matters if matter.column_id == KanbanColumnId.ganho)
        contracts = sum(
            1
            for matter in matters
            if matter.column_id
            in {KanbanColumnId.contrato, KanbanColumnId.pagamento, KanbanColumnId.ganho}
        )
        meeting_matters = sum(
            1
            for matter in matters
            if matter.column_id
            in {
                KanbanColumnId.reuniao,
                KanbanColumnId.proposta,
                KanbanColumnId.contrato,
                KanbanColumnId.pagamento,
                KanbanColumnId.ganho,
            }
        )
        summary = await self.tracking_summary(workspace_id, period_days=period_days)
        spend = float(summary["ad_spend"])
        paid_revenue = sum(
            float(entry.amount or 0)
            for entry in finance_entries
            if entry.status == FinanceEntryStatus.paid
            and entry.entry_type == FinanceEntryType.receivable
        )
        pipeline = sum(float(item.projected_revenue or 0) for item in projections)
        completed_meetings = sum(
            1 for meeting in meetings if meeting.status == MeetingStatus.completed
        )
        no_show_meetings = sum(
            1 for meeting in meetings if meeting.status == MeetingStatus.no_show
        )
        decided_meetings = completed_meetings + no_show_meetings
        # Gasto de mídia não carrega, necessariamente, dimensão jurídica. Exibir
        # uma divisão global sob um filtro de área/produto criaria falsa precisão.
        cpl = None if matter_scoped or not lead_count else spend / lead_count
        cac = None if matter_scoped or not paid_matters else spend / paid_matters
        roas = None if matter_scoped or not spend else paid_revenue / spend
        docs_complete = sum(1 for matter in matters if matter.doc_score >= 80)
        response_times: List[float] = []
        pending_inbound: Dict[UUID, datetime] = {}
        for message in messages:
            occurred_at = _normalized_moment(message.occurred_at)
            if message.direction == MessageDirection.inbound:
                pending_inbound.setdefault(message.thread_id, occurred_at)
                continue
            if message.direction != MessageDirection.outbound:
                continue
            inbound_at = pending_inbound.pop(message.thread_id, None)
            if inbound_at is not None and occurred_at >= inbound_at:
                response_times.append((occurred_at - inbound_at).total_seconds())
        first_response_seconds = (
            round(median(response_times)) if response_times else None
        )
        top_metrics = [
            {"key": "new_leads", "label": "Leads novos", "value": lead_count, "unit": "count"},
            {
                "key": "qualified",
                "label": "Leads qualificados",
                "value": qualified,
                "unit": "count",
            },
            {
                "key": "cpl",
                "label": "CPL",
                "value": round(cpl, 2) if cpl is not None else None,
                "unit": "BRL",
            },
            {
                "key": "cac",
                "label": "CAC por caso ganho",
                "value": round(cac, 2) if cac is not None else None,
                "unit": "BRL",
            },
            {
                "key": "roas",
                "label": "ROAS",
                "value": round(roas, 2) if roas is not None else None,
                "unit": "ratio",
            },
            {
                "key": "lead_to_meeting",
                "label": "Lead para reunião",
                "value": round((meeting_matters / lead_count * 100) if lead_count else 0, 1),
                "unit": "percent",
            },
            {
                "key": "show_rate",
                "label": "Show rate",
                "value": (
                    round(completed_meetings / decided_meetings * 100, 1)
                    if decided_meetings
                    else None
                ),
                "unit": "percent",
            },
            {"key": "contracts", "label": "Contratos", "value": contracts, "unit": "count"},
            {
                "key": "paid",
                "label": "Casos ganhos",
                "value": paid_matters,
                "unit": "count",
            },
            {
                "key": "docs_complete",
                "label": "Docs completos",
                "value": round((docs_complete / lead_count * 100) if lead_count else 0, 1),
                "unit": "percent",
            },
            {
                "key": "first_response",
                "label": "Mediana da primeira resposta",
                "value": first_response_seconds,
                "unit": "seconds",
            },
            {
                "key": "pipeline",
                "label": "Pipeline projetado",
                "value": round(pipeline, 2),
                "unit": "BRL",
            },
        ]
        families = [
            {"key": "acquisition", "label": "Aquisição", "coverage": summary["total_events"]},
            {"key": "funnel", "label": "Funil", "coverage": lead_count},
            {"key": "service", "label": "Atendimento", "coverage": len(messages)},
            {"key": "documents", "label": "Docs e RAG", "coverage": docs_complete},
            {"key": "meetings", "label": "Reuniões", "coverage": len(meetings)},
            {"key": "fees", "label": "Honorários", "coverage": len(projections)},
            {"key": "finance", "label": "Financeiro", "coverage": len(finance_entries)},
            {"key": "quality", "label": "Qualidade IA", "coverage": 0},
        ]
        trend = await self._trend_14d(
            workspace_id,
            matter_ids=set(matter_ids) if matter_scoped else None,
        )
        team_performance = await PlatformBlackboxStudiosService(
            self.db
        ).workspace_team_performance(
            period_days=period_days or 30,
            workspace_id=workspace_id,
        )
        return {
            "top_metrics": top_metrics,
            "families": families,
            "channels": summary["by_channel"],
            "period_days": period_days,
            "subtheme": health_subtheme.value if health_subtheme else None,
            "legal_area_key": canonical_legal_area,
            "legal_product_key": legal_product_key,
            "generated_at": _now(),
            "source": "local_database",
            "trend": trend,
            "team_performance": team_performance.model_dump(mode="json"),
        }

    async def _message_count(self, workspace_id: UUID) -> int:
        return int(
            await self.db.scalar(
                select(func.count(OmnichannelMessage.id)).where(
                    OmnichannelMessage.workspace_id == workspace_id
                )
            )
            or 0
        )

    async def quality(self, workspace_id: UUID) -> Dict[str, Any]:
        matters = list(
            (
                await self.db.execute(
                    select(Matter).where(
                        Matter.workspace_id == workspace_id,
                        Matter.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        threads = list(
            (
                await self.db.execute(
                    select(OmnichannelThread).where(OmnichannelThread.workspace_id == workspace_id)
                )
            )
            .scalars()
            .all()
        )
        messages = list(
            (
                await self.db.execute(
                    select(OmnichannelMessage).where(
                        OmnichannelMessage.workspace_id == workspace_id
                    )
                )
            )
            .scalars()
            .all()
        )
        follow_ups = list(
            (
                await self.db.execute(
                    select(FollowUpTask).where(FollowUpTask.workspace_id == workspace_id)
                )
            )
            .scalars()
            .all()
        )
        document_count = int(
            await self.db.scalar(
                select(func.count(Document.id))
                .join(Matter)
                .where(
                    Matter.workspace_id == workspace_id,
                    Matter.deleted_at.is_(None),
                )
            )
            or 0
        )
        knowledge_count = int(
            await self.db.scalar(
                select(func.count(KnowledgeChunk.id)).where(
                    KnowledgeChunk.workspace_id == workspace_id
                )
            )
            or 0
        )
        linked_threads = sum(1 for thread in threads if thread.matter_id and thread.client_id)
        routing = round(linked_threads / len(threads) * 100) if threads else 100
        complete_fields = 0
        for matter in matters:
            fields = (
                matter.health_subtheme,
                matter.health_object_name,
                matter.health_insurer,
                matter.health_denial_reason,
            )
            complete_fields += sum(value is not None and value != "" for value in fields)
        crm = round(complete_fields / (len(matters) * 4) * 100) if matters else 100
        authored = sum(1 for message in messages if message.author_label and message.author_type)
        authorship = round(authored / len(messages) * 100) if messages else 100
        active_thread_ids = {
            thread.id
            for thread in threads
            if thread.status not in {ThreadStatus.closed, ThreadStatus.blocked}
        }
        covered_thread_ids = {
            follow_up.thread_id
            for follow_up in follow_ups
            if follow_up.thread_id
            and follow_up.status not in {FollowUpStatus.cancelled, FollowUpStatus.failed}
        }
        follow_up_score = (
            round(len(active_thread_ids & covered_thread_ids) / len(active_thread_ids) * 100)
            if active_thread_ids
            else 100
        )
        rag = min(100, round(knowledge_count / document_count * 100)) if document_count else 100
        external_sent = sum(
            1
            for message in messages
            if message.direction == MessageDirection.outbound
            and int((message.message_metadata or {}).get("externalActionsExecuted") or 0) > 0
        )
        guardrail = 100 if external_sent == 0 else 0
        audit_findings = list(
            (
                await self.db.scalars(
                    select(OmnichannelAuditFinding)
                    .where(
                        OmnichannelAuditFinding.workspace_id == workspace_id,
                        OmnichannelAuditFinding.state.in_(
                            ("open", "retry_scheduled", "waiting_human")
                        ),
                    )
                    .order_by(
                        OmnichannelAuditFinding.severity.asc(),
                        OmnichannelAuditFinding.last_seen_at.desc(),
                    )
                    .limit(100)
                )
            ).all()
        )
        audited_problem_threads = {finding.thread_id for finding in audit_findings}
        auditor_score = (
            round(
                max(0, len(active_thread_ids) - len(active_thread_ids & audited_problem_threads))
                / len(active_thread_ids)
                * 100
            )
            if active_thread_ids
            else 100
        )
        dimensions = [
            {"key": "routing", "label": "Roteamento", "score": routing},
            {"key": "crm", "label": "Precisão CRM", "score": crm},
            {"key": "authorship", "label": "Autoria", "score": authorship},
            {"key": "follow_up", "label": "Follow-up", "score": follow_up_score},
            {"key": "rag", "label": "Docs e RAG", "score": rag},
            {"key": "guardrail", "label": "Gate externo", "score": guardrail},
            {"key": "auditor", "label": "Auditor independente", "score": auditor_score},
        ]
        score = round(sum(item["score"] for item in dimensions) / len(dimensions))
        review_queue = [
            {
                "threadId": str(finding.thread_id),
                "matterId": str(finding.matter_id) if finding.matter_id else None,
                "reason": f"auditor:{finding.finding_type}",
                "score": 35 if finding.severity == "critical" else 70,
            }
            for finding in audit_findings[:20]
        ] + [
            {
                "threadId": str(thread.id),
                "matterId": str(thread.matter_id) if thread.matter_id else None,
                "reason": "handoff_humano"
                if thread.status == ThreadStatus.waiting_human
                else "score_baixo",
                "score": thread.score,
            }
            for thread in threads
            if thread.status == ThreadStatus.waiting_human or thread.score < 60
        ][: max(0, 20 - min(20, len(audit_findings)))]
        return {
            "score": score,
            "metrics": {
                "threads": len(threads),
                "messages": len(messages),
                "documents": document_count,
                "knowledgeChunks": knowledge_count,
                "externalMessagesSent": external_sent,
                "auditFindingsOpen": len(audit_findings),
                "auditCritical": sum(finding.severity == "critical" for finding in audit_findings),
            },
            "dimensions": dimensions,
            "review_queue": review_queue,
            "generated_at": _now(),
        }

    async def record_audio_request(
        self,
        workspace_id: UUID,
        user_id: UUID,
        payload: AudioRequestCreate,
    ) -> Dict[str, Any]:
        thread = await self.db.scalar(
            select(OmnichannelThread)
            .where(
                OmnichannelThread.id == payload.thread_id,
                OmnichannelThread.workspace_id == workspace_id,
            )
            .with_for_update()
        )
        if not thread:
            raise LookupError("Thread não encontrada no workspace.")
        message_idempotency_key = f"audio:{payload.idempotency_key}"
        existing = await self.db.scalar(
            select(OmnichannelMessage).where(
                OmnichannelMessage.workspace_id == workspace_id,
                OmnichannelMessage.idempotency_key == message_idempotency_key,
            )
        )
        if existing:
            existing_metadata = existing.message_metadata or {}
            request_count = int(existing_metadata.get("requestCount") or 0)
            spoken_by_label = str(
                existing_metadata.get("spokenByLabel") or "Contato do atendimento"
            )
            spoken_by_client_id = existing_metadata.get("spokenByClientId")
            actor_label = existing.author_label
        else:
            evidence_rows = (
                (
                    await self.db.execute(
                        select(OmnichannelMessage.message_metadata).where(
                            OmnichannelMessage.workspace_id == workspace_id,
                            OmnichannelMessage.thread_id == thread.id,
                            OmnichannelMessage.channel == OmnichannelChannel.audio,
                            OmnichannelMessage.direction == MessageDirection.internal,
                        )
                    )
                )
                .scalars()
                .all()
            )
            request_count = 1 + sum(
                1
                for metadata in evidence_rows
                if (metadata or {}).get("source") == "operator_audio_gate"
            )
            client = None
            if thread.client_id:
                client = await self.db.scalar(
                    select(Client).where(
                        Client.id == thread.client_id,
                        Client.workspace_id == workspace_id,
                        Client.deleted_at.is_(None),
                    )
                )
            spoken_by_label = client.name if client else "Contato do atendimento"
            spoken_by_client_id = str(client.id) if client else None
            actor_label = await resolve_human_actor_label(self.db, user_id)
        gate_passed = request_count >= 2
        message, _ = await self.create_message(
            workspace_id,
            thread.id,
            user_id,
            OmnichannelMessageCreate(
                channel=OmnichannelChannel.audio,
                direction=MessageDirection.internal,
                author_type=MessageAuthorType.human,
                author_label=actor_label,
                body=payload.transcript,
                idempotency_key=message_idempotency_key,
                message_metadata={
                    "requestCount": request_count,
                    "voiceGatePassed": gate_passed,
                    "summary": payload.summary,
                    "durationSeconds": payload.duration_seconds,
                    "transcriptionEnabled": payload.transcription_enabled,
                    "summaryEnabled": payload.summary_enabled,
                    "voiceReplyRequested": payload.voice_reply_requested,
                    "spokenByLabel": spoken_by_label,
                    "spokenByClientId": spoken_by_client_id,
                    "recordedByUserId": str(user_id),
                    "source": "operator_audio_gate",
                    "transcriptionMethod": "operator_reviewed",
                    "originalDirection": "inbound",
                    "externalActionsExecuted": 0,
                },
            ),
        )
        intent = None
        if gate_passed:
            intent, _ = await self.create_external_intent(
                workspace_id,
                user_id,
                ExternalActionIntentCreate(
                    matter_id=thread.matter_id,
                    action_type="voice_assist",
                    provider="elevenlabs",
                    idempotency_key=f"audio:{payload.idempotency_key}",
                    payload={
                        "threadId": str(thread.id),
                        "messageId": str(message.id),
                        "requestCount": request_count,
                        "durationSeconds": payload.duration_seconds,
                        "transcriptionEnabled": payload.transcription_enabled,
                        "summaryEnabled": payload.summary_enabled,
                        "voiceReplyRequested": payload.voice_reply_requested,
                    },
                ),
            )
        return {
            "message": message,
            "request_count": request_count,
            "gate_passed": gate_passed,
            "external_intent": intent,
            "provider_state": "offline",
        }
