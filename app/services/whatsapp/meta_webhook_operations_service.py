from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.klm_operations import KlmAuditEvent
from app.models.oss_bridge import sanitize_oss_json
from app.models.platform_audit_event import PlatformAuditEvent
from app.models.platform_official_whatsapp import PlatformOfficialWhatsAppConnection
from app.models.whatsapp import WhatsAppConnection, WhatsAppConnectionTiers
from app.schemas.platform_audit_event import PlatformAuditEventCreate
from app.services.message_evidence_service import ProviderEnvelopeContext, canonical_sha256
from app.services.platform_audit_event_service import PlatformAuditEventService

# A assinatura no painel Meta e o suporte do produto são conceitos diferentes.
# Este catálogo é a fonte de verdade do que o runtime sabe ingerir e exibir.
META_WEBHOOK_FIELD_CATALOG: dict[str, dict[str, Any]] = {
    "messages": {"category": "messages", "severity": "info", "native": True, "recommended": True},
    "message_echoes": {
        "category": "messages",
        "severity": "info",
        "native": True,
        "recommended": True,
    },
    "smb_message_echoes": {
        "category": "coexistence",
        "severity": "info",
        "native": True,
        "recommended": True,
    },
    "history": {"category": "coexistence", "severity": "info", "native": True, "recommended": True},
    "smb_app_state_sync": {
        "category": "coexistence",
        "severity": "info",
        "native": True,
        "recommended": True,
    },
    "user_preferences": {
        "category": "coexistence",
        "severity": "info",
        "native": False,
        "recommended": True,
    },
    "message_template_status_update": {
        "category": "templates",
        "severity": "warning",
        "native": True,
        "recommended": True,
    },
    "template_category_update": {
        "category": "templates",
        "severity": "warning",
        "native": True,
        "recommended": True,
    },
    "message_template_quality_update": {
        "category": "templates",
        "severity": "warning",
        "native": True,
        "recommended": True,
    },
    "message_template_components_update": {
        "category": "templates",
        "severity": "warning",
        "native": True,
        "recommended": True,
    },
    "template_correct_category_detection": {
        "category": "templates",
        "severity": "warning",
        "native": True,
        "recommended": True,
    },
    "phone_number_quality_update": {
        "category": "health",
        "severity": "warning",
        "native": True,
        "recommended": True,
    },
    "phone_number_name_update": {
        "category": "health",
        "severity": "warning",
        "native": True,
        "recommended": True,
    },
    "account_alerts": {
        "category": "health",
        "severity": "critical",
        "native": False,
        "recommended": True,
    },
    "account_review_update": {
        "category": "health",
        "severity": "warning",
        "native": False,
        "recommended": True,
    },
    "account_settings_update": {
        "category": "health",
        "severity": "warning",
        "native": False,
        "recommended": True,
    },
    "account_update": {
        "category": "health",
        "severity": "warning",
        "native": False,
        "recommended": True,
    },
    "business_capability_update": {
        "category": "health",
        "severity": "warning",
        "native": False,
        "recommended": True,
    },
    "business_status_update": {
        "category": "health",
        "severity": "critical",
        "native": False,
        "recommended": True,
    },
    "business_username_updates": {
        "category": "health",
        "severity": "info",
        "native": False,
        "recommended": True,
    },
    "security": {
        "category": "security",
        "severity": "critical",
        "native": False,
        "recommended": True,
    },
    "messaging_handovers": {
        "category": "routing",
        "severity": "warning",
        "native": False,
        "recommended": True,
    },
    "standby": {"category": "routing", "severity": "info", "native": False, "recommended": True},
    "flows": {"category": "flows", "severity": "info", "native": False, "recommended": True},
    "calls": {"category": "calls", "severity": "info", "native": False, "recommended": True},
    "group_lifecycle_update": {
        "category": "groups",
        "severity": "warning",
        "native": False,
        "recommended": True,
    },
    "group_participants_update": {
        "category": "groups",
        "severity": "info",
        "native": False,
        "recommended": True,
    },
    "group_settings_update": {
        "category": "groups",
        "severity": "warning",
        "native": False,
        "recommended": True,
    },
    "group_status_update": {
        "category": "groups",
        "severity": "warning",
        "native": False,
        "recommended": True,
    },
    "payment_configuration_update": {
        "category": "payments",
        "severity": "critical",
        "native": False,
        "recommended": True,
    },
    "partner_solutions": {
        "category": "partner",
        "severity": "warning",
        "native": False,
        "recommended": True,
    },
    "automatic_events": {
        "category": "automation",
        "severity": "info",
        "native": False,
        "recommended": True,
    },
    "tracking_events": {
        "category": "tracking",
        "severity": "info",
        "native": False,
        "recommended": True,
    },
}

META_OPERATION_EVENT_PREFIX = "whatsapp_meta_webhook_"
PLATFORM_META_OPERATION_ACTION_PREFIX = "meta_webhook_"

# Contrato operacional explícito. Cada campo aceito pelo ingress possui um
# destino de produto e uma orientação humana; não existe mais o estado
# ambíguo "aceito pelo backend, mas dormente na interface".
META_WEBHOOK_SEMANTIC_CONTRACTS: dict[str, dict[str, str]] = {
    # Sprint 1 — conta, capacidade, chamadas e flows.
    "account_alerts": {
        "destination": "Saúde da conta",
        "description": "Alertas operacionais ou de conformidade emitidos para a WABA.",
        "operator_action": "Investigar o alerta e registrar a providência adotada.",
    },
    "account_review_update": {
        "destination": "Saúde da conta",
        "description": "Mudanças no processo de revisão da conta comercial.",
        "operator_action": "Revisar o novo estado e eventuais requisitos da Meta.",
    },
    "account_settings_update": {
        "destination": "Configuração da conta",
        "description": "Alterações observadas nas configurações da WABA.",
        "operator_action": "Comparar a configuração observada com a configuração esperada.",
    },
    "account_update": {
        "destination": "Saúde da conta",
        "description": "Atualização geral do estado da conta WhatsApp Business.",
        "operator_action": "Validar se a alteração afeta entregas ou permissões.",
    },
    "automatic_events": {
        "destination": "Automações",
        "description": "Eventos automáticos produzidos pela plataforma WhatsApp.",
        "operator_action": "Conferir a automação relacionada e sua execução.",
    },
    "business_capability_update": {
        "destination": "Capacidades do canal",
        "description": "Mudanças nas capacidades liberadas para o negócio ou número.",
        "operator_action": "Recalcular as capacidades disponíveis na operação.",
    },
    "business_status_update": {
        "destination": "Saúde da conta",
        "description": "Mudanças no status operacional do negócio na Meta.",
        "operator_action": "Interromper ações incompatíveis e tratar o novo status.",
    },
    "business_username_updates": {
        "destination": "Identidade do canal",
        "description": "Alterações de nome de usuário comercial observadas na Meta.",
        "operator_action": "Conferir a identidade exibida ao cliente.",
    },
    "calls": {
        "destination": "Chamadas WhatsApp",
        "description": "Eventos do ciclo de chamadas do WhatsApp Business.",
        "operator_action": "Acompanhar o estado da chamada e sua evidência operacional.",
    },
    "flows": {
        "destination": "WhatsApp Flows",
        "description": "Eventos de execução e estado de WhatsApp Flows.",
        "operator_action": "Conferir o Flow e a etapa reportada pela Meta.",
    },
    # Sprint 2 — grupos, coexistência, templates e mensagens.
    "group_lifecycle_update": {
        "destination": "Grupos oficiais",
        "description": "Criação, alteração ou encerramento do ciclo de vida de grupo.",
        "operator_action": "Sincronizar o estado do grupo oficial.",
    },
    "group_participants_update": {
        "destination": "Grupos oficiais",
        "description": "Entrada, saída ou mudança de participantes do grupo.",
        "operator_action": "Atualizar participantes e limites do grupo.",
    },
    "group_settings_update": {
        "destination": "Grupos oficiais",
        "description": "Mudanças de configuração de um grupo oficial.",
        "operator_action": "Comparar e refletir as configurações observadas.",
    },
    "group_status_update": {
        "destination": "Grupos oficiais",
        "description": "Mudança no status operacional de um grupo oficial.",
        "operator_action": "Atualizar o status do grupo e bloquear ações incompatíveis.",
    },
    "history": {
        "destination": "Conversas e histórico",
        "description": "Lote histórico entregue pela coexistência.",
        "operator_action": "Importar mensagens idempotentemente para a conversa canônica.",
    },
    "message_echoes": {
        "destination": "Conversas",
        "description": "Eco de mensagem enviada em outra superfície conectada.",
        "operator_action": "Conciliar o eco com a mensagem canônica.",
    },
    "message_template_components_update": {
        "destination": "Templates Meta",
        "description": "Alteração observada nos componentes de um template.",
        "operator_action": "Atualizar a revisão observada sem inventar aprovação.",
    },
    "message_template_quality_update": {
        "destination": "Templates Meta",
        "description": "Mudança na qualidade atribuída a um template.",
        "operator_action": "Exibir a qualidade e orientar correção quando degradada.",
    },
    "message_template_status_update": {
        "destination": "Templates Meta",
        "description": "Estado real de revisão do template na Meta.",
        "operator_action": "Refletir PENDING, APPROVED ou REJECTED e o motivo disponível.",
    },
    "messages": {
        "destination": "Conversas",
        "description": "Mensagens recebidas e recibos de entrega do canal oficial.",
        "operator_action": "Persistir na conversa canônica e atualizar entrega em tempo real.",
    },
    # Sprint 3 — roteamento, parceiros, pagamentos, número e segurança.
    "messaging_handovers": {
        "destination": "Roteamento de atendimento",
        "description": "Transferência de controle da conversa entre aplicações.",
        "operator_action": "Atualizar o controlador ativo e impedir envio concorrente.",
    },
    "partner_solutions": {
        "destination": "Integrações e parceiros",
        "description": "Mudanças em soluções parceiras associadas à conta.",
        "operator_action": "Revalidar o vínculo e as capacidades da solução.",
    },
    "payment_configuration_update": {
        "destination": "Pagamentos no WhatsApp",
        "description": "Mudança na configuração de pagamentos vinculada ao canal.",
        "operator_action": "Revisar a configuração antes de permitir uso financeiro.",
    },
    "phone_number_name_update": {
        "destination": "Identidade do número",
        "description": "Alteração do nome de exibição do número comercial.",
        "operator_action": "Atualizar o nome observado da conexão.",
    },
    "phone_number_quality_update": {
        "destination": "Qualidade do número",
        "description": "Mudança no quality rating do número comercial.",
        "operator_action": "Atualizar o indicador e alertar quando amarelo ou vermelho.",
    },
    "security": {
        "destination": "Segurança",
        "description": "Evento de segurança relacionado à conta ou ao número.",
        "operator_action": "Abrir investigação e preservar a evidência sanitizada.",
    },
    "smb_app_state_sync": {
        "destination": "Coexistência",
        "description": "Sincronização de contatos e estado do WhatsApp Business App.",
        "operator_action": "Conciliar contatos e o estado de sincronização.",
    },
    "smb_message_echoes": {
        "destination": "Conversas e coexistência",
        "description": "Mensagens enviadas pelo aplicativo ou aparelho conectado.",
        "operator_action": "Trazer o envio para a conversa canônica sem duplicar.",
    },
    "standby": {
        "destination": "Roteamento de atendimento",
        "description": "Evento recebido enquanto outra aplicação controla a conversa.",
        "operator_action": "Registrar o estado standby sem disparar resposta concorrente.",
    },
    "template_category_update": {
        "destination": "Templates Meta",
        "description": "Recategorização do template observada na Meta.",
        "operator_action": "Atualizar a categoria e sinalizar impacto de custo.",
    },
    # Sprint 4 — correção de categoria, tracking e preferências.
    "template_correct_category_detection": {
        "destination": "Templates Meta",
        "description": "Detecção ou correção automática da categoria de template.",
        "operator_action": "Registrar a correção e revisar impacto comercial.",
    },
    "tracking_events": {
        "destination": "Métricas e tracking",
        "description": "Eventos de rastreamento produzidos pela experiência WhatsApp.",
        "operator_action": "Associar o evento à métrica sem copiar conteúdo privado.",
    },
    "user_preferences": {
        "destination": "Coexistência e preferências",
        "description": "Mudanças de preferências do usuário no aplicativo conectado.",
        "operator_action": "Atualizar somente preferências suportadas e auditáveis.",
    },
}

_MESSAGE_SIGNAL_FIELDS = {
    "messages",
    "message_echoes",
    "smb_message_echoes",
    "history",
    "smb_app_state_sync",
}

META_WEBHOOK_SPRINTS: tuple[tuple[str, ...], ...] = (
    (
        "account_alerts", "account_review_update", "account_settings_update",
        "account_update", "automatic_events", "business_capability_update",
        "business_status_update", "business_username_updates", "calls", "flows",
    ),
    (
        "group_lifecycle_update", "group_participants_update", "group_settings_update",
        "group_status_update", "history", "message_echoes",
        "message_template_components_update", "message_template_quality_update",
        "message_template_status_update", "messages",
    ),
    (
        "messaging_handovers", "partner_solutions", "payment_configuration_update",
        "phone_number_name_update", "phone_number_quality_update", "security",
        "smb_app_state_sync", "smb_message_echoes", "standby", "template_category_update",
    ),
    ("template_correct_category_detection", "tracking_events", "user_preferences"),
)

_SEMANTIC_FACT_KEYS = (
    "event", "event_type", "status", "state", "reason", "review_status",
    "quality_rating", "new_quality_rating", "current_quality_rating",
    "new_quality_score", "previous_category", "new_category", "category",
    "capability", "setting", "action", "result", "flow_status", "call_status",
)


def _safe_operational_payload(field: str, value: dict[str, Any]) -> dict[str, Any]:
    """Keep operational evidence without copying message/contact content.

    Message bodies already belong to the canonical Inbox history.  Copying
    them into a control-plane audit stream would expand the privacy boundary,
    especially because platform owners can inspect the cross-tenant stream.
    Other Meta signals are reduced to bounded top-level scalar facts; nested
    provider payloads remain in the provider-specific domain handler when the
    product actually needs them (for example template reconciliation).
    """
    if field in _MESSAGE_SIGNAL_FIELDS:
        return {
            "messageCount": len(value.get("messages") or []),
            "echoCount": len(value.get("message_echoes") or []),
            "statusCount": len(value.get("statuses") or []),
            "contactCount": len(value.get("contacts") or []),
            "historyBatchCount": len(value.get("history") or []),
        }
    sanitized = sanitize_oss_json(value)
    facts = {
        str(key): item[:500] if isinstance(item, str) else item
        for key, item in sanitized.items()
        if key in _SEMANTIC_FACT_KEYS
        and (item is None or isinstance(item, str | bool | int | float))
    }
    facts["payloadHash"] = canonical_sha256(sanitized)
    facts["topLevelKeys"] = sorted(str(key)[:80] for key in sanitized)[:40]
    return facts


def supported_meta_webhook_fields() -> frozenset[str]:
    return frozenset(META_WEBHOOK_FIELD_CATALOG)


def meta_webhook_catalog() -> list[dict[str, Any]]:
    sprint_by_field = {
        field: sprint_number
        for sprint_number, fields in enumerate(META_WEBHOOK_SPRINTS, start=1)
        for field in fields
    }
    return [
        {
            "field": field,
            "category": config["category"],
            "severity": config["severity"],
            "implementation": "native" if config["native"] else "domain_projection",
            "recommended": bool(config["recommended"]),
            "sprint": sprint_by_field[field],
            **META_WEBHOOK_SEMANTIC_CONTRACTS[field],
            "observation_state": "not_observed",
            "event_count": 0,
            "last_observed_at": None,
            "last_summary": None,
        }
        for field, config in META_WEBHOOK_FIELD_CATALOG.items()
    ]


def meta_webhook_catalog_with_events(
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return every supported field, including fields never observed.

    Meta subscription is not inferred from silence.  An event proves that the
    field was observed; no event means only that this runtime has not observed
    one in the selected history window.
    """
    observed: dict[str, dict[str, Any]] = {}
    for event in events:
        field = str(event.get("field") or "")
        if field not in META_WEBHOOK_FIELD_CATALOG:
            continue
        rollup = observed.setdefault(
            field,
            {"event_count": 0, "last_observed_at": None, "last_summary": None},
        )
        rollup["event_count"] += 1
        if rollup["last_observed_at"] is None:
            rollup["last_observed_at"] = event.get("occurredAt")
            rollup["last_summary"] = event.get("summary")
    catalog = meta_webhook_catalog()
    for item in catalog:
        rollup = observed.get(item["field"])
        if rollup is None:
            continue
        item.update(rollup)
        item["observation_state"] = "observed"
    return catalog


def _summary(field: str, value: dict[str, Any]) -> str:
    status = str(
        value.get("event")
        or value.get("status")
        or value.get("quality_rating")
        or value.get("new_quality_score")
        or value.get("reason")
        or "recebido"
    ).strip()
    return f"Meta WhatsApp: {field} — {status[:160]}"


class MetaWebhookOperationsService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def record(
        self,
        *,
        workspace_id: UUID,
        field: str,
        value: dict[str, Any],
        connection: WhatsAppConnection | None,
        envelope: ProviderEnvelopeContext | None,
    ) -> KlmAuditEvent | None:
        config = META_WEBHOOK_FIELD_CATALOG.get(field)
        if config is None:
            return None
        safe_payload = _safe_operational_payload(field, value)
        payload_hash = canonical_sha256(sanitize_oss_json(value))
        correlation_id = (
            f"meta:{envelope.id}:{field}:{payload_hash[:16]}"
            if envelope
            else f"meta:{workspace_id}:{field}:{payload_hash[:16]}"
        )
        existing = await self.db.scalar(
            select(KlmAuditEvent.id).where(
                KlmAuditEvent.workspace_id == workspace_id,
                KlmAuditEvent.correlation_id == correlation_id,
            )
        )
        if existing is not None:
            return None
        event = KlmAuditEvent(
            workspace_id=workspace_id,
            actor_type="webhook",
            actor_label="Meta WhatsApp Business Platform",
            event_type=f"{META_OPERATION_EVENT_PREFIX}{field}",
            entity_type="whatsapp_connection",
            entity_id=connection.id if connection else None,
            summary=_summary(field, value),
            correlation_id=correlation_id,
            before_data=None,
            after_data={
                "field": field,
                "category": config["category"],
                "severity": config["severity"],
                "implementation": "native" if config["native"] else "domain_projection",
                "destination": META_WEBHOOK_SEMANTIC_CONTRACTS[field]["destination"],
                "payload": safe_payload,
                "receivedAt": envelope.received_at.isoformat() if envelope else None,
            },
        )
        self.db.add(event)
        await self.db.flush()
        return event

    async def apply_connection_projection(
        self,
        *,
        field: str,
        value: dict[str, Any],
        connection: WhatsAppConnection | None,
    ) -> None:
        """Apply only deterministic provider facts to the connection model.

        Every supported field is retained in the append-only operational event.
        This projection intentionally updates only facts with an unambiguous
        local home; alerts/security/payment events remain reviewable events and
        never mutate business state by inference.
        """
        if connection is None:
            return
        if field == "phone_number_name_update":
            name = next(
                (
                    str(value.get(key) or "").strip()
                    for key in ("verified_name", "display_name", "new_name", "name")
                    if str(value.get(key) or "").strip()
                ),
                "",
            )
            if name:
                connection.display_name = name[:200]
                self.db.add(connection)
        if field != "phone_number_quality_update":
            await self.db.flush()
            return
        raw_quality = next(
            (
                str(value.get(key) or "").strip().upper()
                for key in ("quality_rating", "new_quality_rating", "current_quality_rating")
                if str(value.get(key) or "").strip()
            ),
            "",
        )
        quality = {
            "GREEN": "verde",
            "YELLOW": "amarelo",
            "RED": "vermelho",
            "VERDE": "verde",
            "AMARELO": "amarelo",
            "VERMELHO": "vermelho",
        }.get(raw_quality)
        if quality is None:
            await self.db.flush()
            return
        tier = await self.db.scalar(
            select(WhatsAppConnectionTiers).where(
                WhatsAppConnectionTiers.connection_id == connection.id
            )
        )
        if tier is None:
            tier = WhatsAppConnectionTiers(
                workspace_id=connection.workspace_id,
                connection_id=connection.id,
            )
        tier.quality_rating = quality
        self.db.add(tier)
        await self.db.flush()

    async def record_platform(
        self,
        *,
        field: str,
        value: dict[str, Any],
        connection: PlatformOfficialWhatsAppConnection,
    ) -> PlatformAuditEvent | None:
        """Persist one sanitized signal for a platform-owned WhatsApp number."""
        config = META_WEBHOOK_FIELD_CATALOG.get(field)
        if config is None:
            return None
        safe_payload = _safe_operational_payload(field, value)
        payload_hash = canonical_sha256(sanitize_oss_json(value))
        correlation_id = f"platform-meta:{connection.id}:{field}:{payload_hash[:16]}"
        existing = await self.db.scalar(
            select(PlatformAuditEvent.id).where(PlatformAuditEvent.correlation_id == correlation_id)
        )
        if existing is not None:
            return None
        return await PlatformAuditEventService(self.db).record(
            PlatformAuditEventCreate(
                actor_role_snapshot="system",
                session_type="system",
                module="integrations",
                action=f"{PLATFORM_META_OPERATION_ACTION_PREFIX}{field}",
                resource_type="platform_official_whatsapp_connection",
                resource_id=str(connection.id),
                resource_label_snapshot=connection.role.value,
                result="success",
                before=None,
                after={
                    "field": field,
                    "category": config["category"],
                    "severity": config["severity"],
                    "implementation": ("native" if config["native"] else "domain_projection"),
                    "destination": META_WEBHOOK_SEMANTIC_CONTRACTS[field]["destination"],
                    "payload": safe_payload,
                },
                reason="Sinal autenticado recebido do webhook Meta da plataforma.",
                correlation_id=correlation_id,
                operation_id=correlation_id,
                route="/api/v1/whatsapp/webhook",
                method="POST",
            )
        )

    async def list_events(
        self,
        *,
        workspace_id: UUID | None,
        field: str | None = None,
        limit: int = 100,
    ) -> list[KlmAuditEvent]:
        statement = select(KlmAuditEvent).where(
            KlmAuditEvent.event_type.like(f"{META_OPERATION_EVENT_PREFIX}%")
        )
        if workspace_id is not None:
            statement = statement.where(KlmAuditEvent.workspace_id == workspace_id)
        if field:
            statement = statement.where(
                KlmAuditEvent.event_type == f"{META_OPERATION_EVENT_PREFIX}{field}"
            )
        return list(
            (
                await self.db.scalars(
                    statement.order_by(
                        desc(KlmAuditEvent.occurred_at),
                        desc(KlmAuditEvent.id),
                    ).limit(max(1, min(limit, 200)))
                )
            ).all()
        )

    async def list_platform_events(
        self,
        *,
        field: str | None = None,
        limit: int = 100,
    ) -> list[PlatformAuditEvent]:
        statement = select(PlatformAuditEvent).where(
            PlatformAuditEvent.module == "integrations",
            PlatformAuditEvent.action.like(f"{PLATFORM_META_OPERATION_ACTION_PREFIX}%"),
            PlatformAuditEvent.resource_type == "platform_official_whatsapp_connection",
        )
        if field:
            statement = statement.where(
                PlatformAuditEvent.action == f"{PLATFORM_META_OPERATION_ACTION_PREFIX}{field}"
            )
        return list(
            (
                await self.db.scalars(
                    statement.order_by(
                        desc(PlatformAuditEvent.occurred_at),
                        desc(PlatformAuditEvent.id),
                    ).limit(max(1, min(limit, 200)))
                )
            ).all()
        )

    @staticmethod
    def serialize_event(event: KlmAuditEvent) -> dict[str, Any]:
        data = dict(event.after_data or {})
        return {
            "id": str(event.id),
            "workspaceId": str(event.workspace_id) if event.workspace_id else None,
            "connectionId": str(event.entity_id) if event.entity_id else None,
            "field": str(
                data.get("field") or event.event_type.removeprefix(META_OPERATION_EVENT_PREFIX)
            ),
            "category": str(data.get("category") or "unknown"),
            "severity": str(data.get("severity") or "info"),
            "implementation": str(data.get("implementation") or "domain_projection"),
            "summary": event.summary,
            "payload": data.get("payload") if isinstance(data.get("payload"), dict) else {},
            "occurredAt": event.occurred_at,
        }

    @staticmethod
    def serialize_platform_event(event: PlatformAuditEvent) -> dict[str, Any]:
        data = dict(event.after or {})
        return {
            "id": str(event.id),
            "workspaceId": None,
            "connectionId": event.resource_id,
            "field": str(
                data.get("field")
                or event.action.removeprefix(PLATFORM_META_OPERATION_ACTION_PREFIX)
            ),
            "category": str(data.get("category") or "unknown"),
            "severity": str(data.get("severity") or "info"),
            "implementation": str(data.get("implementation") or "domain_projection"),
            "summary": f"Meta WhatsApp da plataforma: {data.get('field') or 'sinal recebido'}",
            "payload": data.get("payload") if isinstance(data.get("payload"), dict) else {},
            "occurredAt": event.occurred_at,
        }
