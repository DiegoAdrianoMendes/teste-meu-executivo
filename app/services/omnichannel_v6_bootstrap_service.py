"""Idempotent local bootstrap and legacy convergence for omnichannel V6."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.client import Client
from app.models.klm_operations import (
    MessageDirection,
    OmnichannelChannel,
    OmnichannelMessage,
    OmnichannelThread,
)
from app.models.omnichannel_v6 import (
    OmnichannelAutomationRule,
    OmnichannelChannelConnection,
    OmnichannelChannelEvent,
    OmnichannelEndpoint,
    OmnichannelIdentity,
    OmnichannelThreadBinding,
    WorkspaceTeam,
    WorkspaceTeamMember,
)
from app.schemas.omnichannel_v6 import (
    AutomationRuleCreate,
    CanonicalChannelEventIngest,
    ChannelConnectionCreate,
    LeadCaptureIngest,
    WorkspaceTeamCreate,
    WorkspaceTeamMemberCreate,
)
from app.services.knowledge_service import KnowledgeService
from app.services.omnichannel_orchestration_service import OmnichannelOrchestrationService
from app.services.omnichannel_v6_service import OmnichannelV6Service

OMNICHANNEL_LOCAL_SEED_VERSION = "v6.1"


@dataclass
class OmnichannelBootstrapResult:
    connections_created: int = 0
    teams_created: int = 0
    rules_created: int = 0
    identities_created: int = 0
    threads_backfilled: int = 0
    messages_backfilled: int = 0
    rag_messages_reconciled: int = 0
    social_events_created: int = 0
    lead_captures_created: int = 0
    identity_conflicts: int = 0


def _now() -> datetime:
    return datetime.now(UTC)


def _phone(value: str | None) -> str | None:
    digits = re.sub(r"\D", "", value or "")
    if not digits:
        return None
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) in {10, 11}:
        digits = f"55{digits}"
    return f"+{digits}" if 10 <= len(digits) <= 15 else None


def _email(value: str | None) -> str | None:
    normalized = (value or "").strip().lower()
    return normalized if "@" in normalized else None


CONNECTION_SPECS = (
    {
        "channel": OmnichannelChannel.whatsapp,
        "provider": "meta_cloud",
        "connection_key": "whatsapp-cloud-principal",
        "display_name": "WhatsApp jurídico principal",
        "transport_mode": "cloud_api",
        "is_default": True,
        "configuration": {},
    },
    {
        "channel": OmnichannelChannel.whatsapp,
        "provider": "meta_cloud",
        "connection_key": "whatsapp-coexistence-comercial",
        "display_name": "WhatsApp comercial Coexistence",
        "transport_mode": "coexistence",
        "is_default": False,
        "configuration": {},
    },
    {
        "channel": OmnichannelChannel.instagram,
        "provider": "meta_instagram",
        "connection_key": "instagram-principal",
        "display_name": "Instagram Meu Executivo",
        "transport_mode": "official_api",
        "is_default": True,
        "configuration": {},
    },
    {
        "channel": OmnichannelChannel.facebook,
        "provider": "meta_messenger",
        "connection_key": "facebook-page-principal",
        "display_name": "Página Facebook Meu Executivo",
        "transport_mode": "official_api",
        "is_default": True,
        "configuration": {},
    },
    {
        "channel": OmnichannelChannel.messenger,
        "provider": "meta_messenger",
        "connection_key": "messenger-principal",
        "display_name": "Messenger Meu Executivo",
        "transport_mode": "official_api",
        "is_default": True,
        "configuration": {},
    },
    {
        "channel": OmnichannelChannel.tiktok,
        "provider": "tiktok_business",
        "connection_key": "tiktok-business-principal",
        "display_name": "TikTok Business Meu Executivo",
        "transport_mode": "official_api",
        "is_default": True,
        "configuration": {},
    },
    {
        "channel": OmnichannelChannel.email,
        "provider": "google_mail",
        "connection_key": "google-workspace-principal",
        "display_name": "Google Workspace atendimento",
        "transport_mode": "oauth",
        "is_default": True,
        "configuration": {"accountAddress": "atendimento@klmadvogados.local"},
    },
    {
        "channel": OmnichannelChannel.facebook,
        "provider": "meta_ads",
        "connection_key": "meta-ads-principal",
        "display_name": "Meta Ads",
        "transport_mode": "official_api",
        "is_default": False,
        "configuration": {},
    },
    {
        "channel": OmnichannelChannel.tiktok,
        "provider": "tiktok_ads",
        "connection_key": "tiktok-ads-principal",
        "display_name": "TikTok Ads",
        "transport_mode": "official_api",
        "is_default": False,
        "configuration": {},
    },
    {
        "channel": OmnichannelChannel.webchat,
        "provider": "site",
        "connection_key": "site-klm-principal",
        "display_name": "Formulários do site Meu Executivo",
        "transport_mode": "webhook",
        "is_default": True,
        "configuration": {"formSource": "site-klm"},
    },
    {
        "channel": OmnichannelChannel.webchat,
        "provider": "lovable",
        "connection_key": "lovable-site-principal",
        "display_name": "Site Lovable",
        "transport_mode": "webhook",
        "is_default": False,
        "configuration": {"formSource": "lovable-klm"},
    },
)


TEAM_SPECS = (
    ("Gestão", "gestao", "management", "#050505", "manual"),
    ("Triagem SDR", "triagem-sdr", "sales", "#1A9948", "least_loaded"),
    ("Jurídico Saúde", "juridico-saude", "legal", "#163A70", "skill_based"),
    ("Closer", "closer", "sales", "#B28A2E", "least_loaded"),
    ("Secretaria", "secretaria", "service", "#2A7D78", "round_robin"),
    ("Paralegal", "paralegal", "legal_ops", "#7A5C9E", "least_loaded"),
    ("Petições", "peticoes", "legal", "#A14C43", "skill_based"),
    ("Andamentos", "andamentos", "client_success", "#3D6A78", "round_robin"),
    ("Financeiro e cobrança", "financeiro-cobranca", "finance", "#8A5E0A", "least_loaded"),
    ("CRM e dados", "crm-dados", "operations", "#4F6673", "least_loaded"),
    ("Marketing e aquisição", "marketing-aquisicao", "marketing", "#9B4D78", "skill_based"),
    ("Tecnologia e suporte", "tecnologia-suporte", "technology", "#334155", "least_loaded"),
)


AUTOMATION_SPECS = (
    {
        "name": "Comentário saúde para conversa privada",
        "slug": "comentario-saude-para-conversa-privada",
        "description": "Responde publicamente, prepara DM e inicia a triagem sem duplicar o lead.",
        "trigger_type": "keyword_comment",
        "status": "active",
        "priority": 10,
        "coexist_with_ai": True,
        "stop_on_match": False,
        "trigger_config": {
            "keywords": ["negativa", "medicamento", "cirurgia", "home care", "exame"]
        },
        "conditions": [{"field": "event.visibility", "operator": "eq", "value": "public"}],
        "actions": [
            {
                "type": "public_reply",
                "body": "Enviei uma mensagem para entender seu caso com privacidade.",
            },
            {
                "type": "private_reply",
                "body": "Olá. O que foi negado pelo plano e quando você recebeu a negativa?",
            },
            {"type": "add_tag", "tag": "origem-comentario"},
            {"type": "start_flow", "flow": "triagem-saude"},
        ],
    },
    {
        "name": "Lead Ads para triagem imediata",
        "slug": "lead-ads-para-triagem-imediata",
        "description": (
            "Unifica o formulário, cria o card e agenda a primeira abordagem no número padrão."
        ),
        "trigger_type": "lead_form",
        "status": "active",
        "priority": 20,
        "coexist_with_ai": True,
        "stop_on_match": False,
        "trigger_config": {"providers": ["meta_ads", "tiktok_ads", "site", "lovable"]},
        "conditions": [],
        "actions": [
            {"type": "add_tag", "tag": "lead-form"},
            {"type": "update_card", "stage": "novo_lead"},
            {"type": "start_flow", "flow": "triagem-saude"},
            {"type": "schedule_message", "template": "triagem_saude_v1", "delaySeconds": 30},
        ],
    },
    {
        "name": "Follow-up comercial progressivo",
        "slug": "follow-up-comercial-progressivo",
        "description": (
            "Executa cadência auditável e interrompe quando há resposta, bloqueio ou contratação."
        ),
        "trigger_type": "scheduled",
        "status": "active",
        "priority": 30,
        "coexist_with_ai": True,
        "stop_on_match": False,
        "trigger_config": {
            "delaysHours": [24, 72, 168],
            "stopStates": ["responded", "blocked", "won"],
        },
        "conditions": [
            {"field": "thread.lastInboundAt", "operator": "older_than_hours", "value": 24}
        ],
        "actions": [
            {
                "type": "send_message",
                "body": (
                    "Oi, estou acompanhando seu caso. Conseguiu reunir a negativa e a prescrição?"
                ),
            },
            {"type": "create_task", "title": "Revisar lead sem resposta"},
        ],
    },
    {
        "name": "Áudio insistente com handoff",
        "slug": "audio-insistente-com-handoff",
        "description": (
            "Mantém texto como padrão e pede aprovação humana antes de migrar "
            "para áudio ou chamada."
        ),
        "trigger_type": "message.received",
        "status": "active",
        "priority": 40,
        "coexist_with_ai": False,
        "stop_on_match": True,
        "trigger_config": {"contentTypes": ["audio"], "minimumOccurrences": 2},
        "conditions": [{"field": "message.contentType", "operator": "eq", "value": "audio"}],
        "actions": [
            {"type": "pause_ai"},
            {"type": "notify_team", "team": "secretaria"},
            {"type": "request_human_approval", "reason": "Lead insiste em atendimento por áudio"},
        ],
    },
)


class OmnichannelV6BootstrapService:
    """Converge the local demo without overwriting operator-owned state."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.v6 = OmnichannelV6Service(db)
        self.orchestration = OmnichannelOrchestrationService(db)

    async def ensure_workspace(
        self,
        workspace_id: UUID,
        actor_user_id: UUID,
        *,
        include_demo_events: bool = True,
    ) -> OmnichannelBootstrapResult:
        result = OmnichannelBootstrapResult()
        result.connections_created = await self._ensure_connections(workspace_id, actor_user_id)
        result.teams_created = await self._ensure_teams(workspace_id, actor_user_id)
        result.rules_created = await self._ensure_rules(workspace_id, actor_user_id)
        await self._backfill_threads(workspace_id, result)
        if include_demo_events:
            result.social_events_created = await self._ensure_social_events(workspace_id)
            result.lead_captures_created = await self._ensure_demo_leads(
                workspace_id, actor_user_id
            )
        await self.db.flush()
        return result

    async def _ensure_connections(self, workspace_id: UUID, actor_user_id: UUID) -> int:
        created = 0
        for raw in CONNECTION_SPECS:
            existing = await self.db.scalar(
                select(OmnichannelChannelConnection).where(
                    OmnichannelChannelConnection.workspace_id == workspace_id,
                    OmnichannelChannelConnection.provider == raw["provider"],
                    OmnichannelChannelConnection.connection_key == raw["connection_key"],
                )
            )
            connection = existing
            if connection is None:
                payload = ChannelConnectionCreate(
                    **raw,
                    routing_policy={"canonicalConversation": True, "transferEnabled": True},
                    cost_policy={"ledgerEnabled": True, "currency": "BRL"},
                )
                connection = await self.orchestration.create_connection(
                    workspace_id, actor_user_id, payload
                )
                created += 1
            elif connection.display_name != raw["display_name"]:
                # Correção retroativa idempotente: bancos semeados antes do rebrand
                # ficaram com o display_name antigo (upsert é por provider+connection_key,
                # não atualiza display_name). Rodar 2x não duplica nem reverte.
                connection.display_name = raw["display_name"]
            if not connection.health or connection.health.get("state") == "not_verified":
                await self.orchestration.validate_connection_local(workspace_id, connection.id)
        return created

    async def _ensure_teams(self, workspace_id: UUID, actor_user_id: UUID) -> int:
        created = 0
        membership_slugs = {"gestao", "triagem-sdr", "juridico-saude"}
        for name, slug, team_type, color, routing_strategy in TEAM_SPECS:
            team = await self.db.scalar(
                select(WorkspaceTeam).where(
                    WorkspaceTeam.workspace_id == workspace_id,
                    WorkspaceTeam.slug == slug,
                )
            )
            if team is None:
                team = await self.v6.create_team(
                    workspace_id,
                    actor_user_id,
                    WorkspaceTeamCreate(
                        name=name,
                        slug=slug,
                        description=f"Equipe nativa Meu Executivo para {name.lower()}.",
                        team_type=team_type,
                        color=color,
                        capacity=0,
                        routing_strategy=routing_strategy,
                        schedule={
                            "timezone": "America/Sao_Paulo",
                            "businessHours": "configurable",
                        },
                    ),
                )
                created += 1
            if slug in membership_slugs:
                member = await self.db.scalar(
                    select(WorkspaceTeamMember.id).where(
                        WorkspaceTeamMember.workspace_id == workspace_id,
                        WorkspaceTeamMember.team_id == team.id,
                        WorkspaceTeamMember.user_id == actor_user_id,
                    )
                )
                if member is None:
                    await self.v6.add_team_member(
                        workspace_id,
                        team.id,
                        actor_user_id,
                        WorkspaceTeamMemberCreate(
                            user_id=actor_user_id,
                            member_role="lead",
                            is_available=True,
                        ),
                    )
        return created

    async def _ensure_rules(self, workspace_id: UUID, actor_user_id: UUID) -> int:
        created = 0
        for raw in AUTOMATION_SPECS:
            existing = await self.db.scalar(
                select(OmnichannelAutomationRule.id).where(
                    OmnichannelAutomationRule.workspace_id == workspace_id,
                    OmnichannelAutomationRule.slug == raw["slug"],
                )
            )
            if existing is None:
                await self.orchestration.create_automation_rule(
                    workspace_id,
                    actor_user_id,
                    AutomationRuleCreate(**raw),
                )
                created += 1
        return created

    async def _backfill_threads(
        self, workspace_id: UUID, result: OmnichannelBootstrapResult
    ) -> None:
        threads = list(
            (
                await self.db.execute(
                    select(OmnichannelThread)
                    .where(OmnichannelThread.workspace_id == workspace_id)
                    .order_by(OmnichannelThread.created_at, OmnichannelThread.id)
                )
            ).scalars()
        )
        for thread in threads:
            client = None
            if thread.client_id:
                client = await self.db.scalar(
                    select(Client).where(
                        Client.workspace_id == workspace_id,
                        Client.id == thread.client_id,
                    )
                )
            identity, identity_created = await self._identity_for_thread(
                workspace_id, thread, client
            )
            if identity is None:
                result.identity_conflicts += 1
                metadata = dict(thread.thread_metadata or {})
                metadata["canonicalIdentityReviewRequired"] = True
                thread.thread_metadata = metadata
                continue
            if identity_created:
                result.identities_created += 1
            if thread.identity_id is None:
                thread.identity_id = identity.id
                result.threads_backfilled += 1

            messages = list(
                (
                    await self.db.execute(
                        select(OmnichannelMessage)
                        .where(
                            OmnichannelMessage.workspace_id == workspace_id,
                            OmnichannelMessage.thread_id == thread.id,
                        )
                        .order_by(OmnichannelMessage.occurred_at, OmnichannelMessage.id)
                    )
                ).scalars()
            )
            external_channels = {
                message.channel
                for message in messages
                if message.channel != OmnichannelChannel.internal
            }
            if thread.primary_channel != OmnichannelChannel.internal:
                external_channels.add(thread.primary_channel)
            channel_names = sorted(channel.value for channel in external_channels)
            metadata = dict(thread.thread_metadata or {})
            metadata.update(
                {
                    "canonical": True,
                    "canonicalVersion": OMNICHANNEL_LOCAL_SEED_VERSION,
                    "channels": list(
                        dict.fromkeys([*(metadata.get("channels") or []), *channel_names])
                    ),
                    "relatedMatterIds": list(
                        dict.fromkeys(
                            [
                                *(metadata.get("relatedMatterIds") or []),
                                *([str(thread.matter_id)] if thread.matter_id else []),
                            ]
                        )
                    ),
                }
            )
            thread.thread_metadata = metadata
            thread.policy_state = {
                **(thread.policy_state or {}),
                "externalDeliveryGate": "closed",
                "humanVeto": True,
                "tenantScoped": True,
            }
            thread.cost_state = {
                **(thread.cost_state or {}),
                "currency": "BRL",
                "messageCount": len(messages),
                "externalCost": "0.000000",
            }
            thread.version = max(thread.version or 1, 1)
            inbound = [m.occurred_at for m in messages if m.direction == MessageDirection.inbound]
            outbound = [m.occurred_at for m in messages if m.direction == MessageDirection.outbound]
            if inbound:
                thread.last_inbound_at = max(
                    [*inbound, *([thread.last_inbound_at] if thread.last_inbound_at else [])]
                )
            if outbound:
                thread.last_outbound_at = max(
                    [*outbound, *([thread.last_outbound_at] if thread.last_outbound_at else [])]
                )
            if inbound:
                latest_inbound = max(
                    (m for m in messages if m.direction == MessageDirection.inbound),
                    key=lambda item: item.occurred_at,
                )
                thread.preferred_channel = latest_inbound.channel.value

            for channel in sorted(external_channels, key=lambda item: item.value):
                endpoint, binding, _created = await self._ensure_legacy_binding(
                    workspace_id,
                    thread,
                    identity,
                    channel,
                    exact_route=channel == OmnichannelChannel.whatsapp,
                )
                for message in messages:
                    if message.channel != channel:
                        continue
                    changed = message.endpoint_id is None or message.binding_id is None
                    message.endpoint_id = endpoint.id
                    message.binding_id = binding.id
                    message.audience = message.audience or "client_private"
                    message.content_type = message.content_type or str(
                        (message.message_metadata or {}).get("contentType") or "text"
                    )
                    message.language = message.language or "pt-BR"
                    message.provider_status = (
                        message.provider_status or message.delivery_status.value
                    )
                    message.transport = message.transport or binding.provider
                    message.message_metadata = {
                        **(message.message_metadata or {}),
                        "canonicalBindingId": str(binding.id),
                        "canonicalEndpointId": str(endpoint.id),
                        "backfillVersion": OMNICHANNEL_LOCAL_SEED_VERSION,
                    }
                    if changed:
                        result.messages_backfilled += 1
            if OmnichannelChannel.whatsapp in external_channels:
                await self._ensure_legacy_binding(
                    workspace_id,
                    thread,
                    identity,
                    OmnichannelChannel.whatsapp,
                    provider_override="meta_cloud",
                    connection_key_override="whatsapp-coexistence-comercial",
                    exact_route=True,
                )
            for message in messages:
                if message.channel == OmnichannelChannel.internal:
                    message.audience = "internal"
                    message.transport = message.transport or "klm_internal"
                    message.content_type = message.content_type or "text"
                    message.language = message.language or "pt-BR"
            if thread.matter_id:
                result.rag_messages_reconciled += len(
                    await KnowledgeService(self.db).index_thread_conversation(
                        workspace_id=workspace_id,
                        thread_id=thread.id,
                        matter_id=thread.matter_id,
                    )
                )
        await self.db.flush()

    async def _identity_for_thread(
        self,
        workspace_id: UUID,
        thread: OmnichannelThread,
        client: Client | None,
    ) -> tuple[OmnichannelIdentity | None, bool]:
        if thread.identity_id:
            return (
                await self.db.scalar(
                    select(OmnichannelIdentity).where(
                        OmnichannelIdentity.workspace_id == workspace_id,
                        OmnichannelIdentity.id == thread.identity_id,
                    )
                ),
                False,
            )
        phone = _phone(client.phone if client else None)
        email = _email(client.email if client else None)
        filters = []
        if thread.client_id:
            filters.append(OmnichannelIdentity.client_id == thread.client_id)
        if phone:
            filters.append(OmnichannelIdentity.canonical_phone == phone)
        if email:
            filters.append(OmnichannelIdentity.canonical_email == email)
        candidates: list[OmnichannelIdentity] = []
        if filters:
            candidates = list(
                (
                    await self.db.execute(
                        select(OmnichannelIdentity).where(
                            OmnichannelIdentity.workspace_id == workspace_id,
                            or_(*filters),
                        )
                    )
                ).scalars()
            )
        if len({candidate.id for candidate in candidates}) > 1:
            return None, False
        identity = candidates[0] if candidates else None
        if identity is not None:
            identity.client_id = identity.client_id or thread.client_id
            metadata = dict(identity.identity_metadata or {})
            metadata["relatedMatterIds"] = list(
                dict.fromkeys(
                    [
                        *(metadata.get("relatedMatterIds") or []),
                        *([str(thread.matter_id)] if thread.matter_id else []),
                    ]
                )
            )
            identity.identity_metadata = metadata
            return identity, False
        identity = OmnichannelIdentity(
            workspace_id=workspace_id,
            client_id=thread.client_id,
            display_name=(client.name if client else None) or thread.subject or "Contato local",
            canonical_phone=phone,
            canonical_email=email,
            identity_state="verified" if phone or email else "provisional",
            source="legacy_local_backfill",
            identity_metadata={
                "backfillVersion": OMNICHANNEL_LOCAL_SEED_VERSION,
                "relatedMatterIds": [str(thread.matter_id)] if thread.matter_id else [],
            },
        )
        self.db.add(identity)
        await self.db.flush()
        return identity, True

    @staticmethod
    def _channel_route(channel: OmnichannelChannel) -> tuple[str, str]:
        routes = {
            OmnichannelChannel.whatsapp: ("meta_cloud", "whatsapp-cloud-principal"),
            OmnichannelChannel.instagram: ("meta_instagram", "instagram-principal"),
            OmnichannelChannel.facebook: ("meta_messenger", "facebook-page-principal"),
            OmnichannelChannel.messenger: ("meta_messenger", "messenger-principal"),
            OmnichannelChannel.tiktok: ("tiktok_business", "tiktok-business-principal"),
            OmnichannelChannel.email: ("google_mail", "google-workspace-principal"),
            OmnichannelChannel.form: ("site", "site-klm-principal"),
            OmnichannelChannel.webchat: ("site", "site-klm-principal"),
            OmnichannelChannel.audio: ("meta_cloud", "whatsapp-cloud-principal"),
        }
        return routes.get(channel, ("local_fixture", f"legacy-{channel.value}"))

    async def _ensure_legacy_binding(
        self,
        workspace_id: UUID,
        thread: OmnichannelThread,
        identity: OmnichannelIdentity,
        channel: OmnichannelChannel,
        *,
        provider_override: str | None = None,
        connection_key_override: str | None = None,
        exact_route: bool = False,
    ) -> tuple[OmnichannelEndpoint, OmnichannelThreadBinding, bool]:
        default_provider, default_connection_key = self._channel_route(channel)
        provider = provider_override or default_provider
        connection_key = connection_key_override or default_connection_key
        binding_filters = [
            OmnichannelThreadBinding.workspace_id == workspace_id,
            OmnichannelThreadBinding.thread_id == thread.id,
            OmnichannelThreadBinding.binding_state == "active",
            OmnichannelEndpoint.channel == channel,
        ]
        if exact_route:
            binding_filters.extend(
                [
                    OmnichannelThreadBinding.provider == provider,
                    OmnichannelThreadBinding.connection_key == connection_key,
                ]
            )
        existing_binding = await self.db.execute(
            select(OmnichannelThreadBinding, OmnichannelEndpoint)
            .join(
                OmnichannelEndpoint,
                (OmnichannelEndpoint.workspace_id == OmnichannelThreadBinding.workspace_id)
                & (OmnichannelEndpoint.id == OmnichannelThreadBinding.endpoint_id),
            )
            .where(*binding_filters)
            .order_by(OmnichannelThreadBinding.created_at)
            .limit(1)
        )
        existing_pair = existing_binding.first()
        if existing_pair:
            return existing_pair[1], existing_pair[0], False

        external_event_id = f"legacy-thread:{thread.id}:{channel.value}:{connection_key}"
        existing_event = await self.db.scalar(
            select(OmnichannelChannelEvent).where(
                OmnichannelChannelEvent.workspace_id == workspace_id,
                OmnichannelChannelEvent.provider == provider,
                OmnichannelChannelEvent.connection_key == connection_key,
                OmnichannelChannelEvent.external_event_id == external_event_id,
            )
        )
        if existing_event and existing_event.binding_id:
            binding = await self.db.scalar(
                select(OmnichannelThreadBinding).where(
                    OmnichannelThreadBinding.workspace_id == workspace_id,
                    OmnichannelThreadBinding.id == existing_event.binding_id,
                )
            )
            endpoint = None
            if binding and binding.endpoint_id:
                endpoint = await self.db.scalar(
                    select(OmnichannelEndpoint).where(
                        OmnichannelEndpoint.workspace_id == workspace_id,
                        OmnichannelEndpoint.id == binding.endpoint_id,
                    )
                )
            if binding and endpoint:
                return endpoint, binding, False

        phone = identity.canonical_phone
        email = identity.canonical_email
        address = email if channel == OmnichannelChannel.email else phone
        external_user_id = address or f"client:{thread.client_id or identity.id}"
        payload = CanonicalChannelEventIngest(
            provider=provider,
            connection_key=connection_key,
            channel=channel,
            event_type="legacy.thread.materialized",
            external_event_id=external_event_id,
            external_thread_id=f"legacy:{thread.id}:{channel.value}:{connection_key}",
            external_user_id=external_user_id,
            display_name=identity.display_name,
            address=address,
            canonical_phone=phone,
            canonical_email=email,
            client_id=thread.client_id,
            matter_id=thread.matter_id,
            visibility="internal",
            actionability="informational",
            occurred_at=thread.created_at,
            correlation={"legacyThreadId": str(thread.id)},
            payload={"messageCount": 0},
            metadata={
                "backfillVersion": OMNICHANNEL_LOCAL_SEED_VERSION,
                "externalActionsExecuted": 0,
            },
        )
        resolution, _event, _message, deduplicated = await self.v6.ingest_event(
            workspace_id, payload
        )
        return resolution.endpoint, resolution.binding, not deduplicated

    async def _ensure_social_events(self, workspace_id: UUID) -> int:
        thread = await self.db.scalar(
            select(OmnichannelThread)
            .where(
                OmnichannelThread.workspace_id == workspace_id,
                OmnichannelThread.identity_id.is_not(None),
            )
            .order_by(OmnichannelThread.created_at)
        )
        if thread is None:
            return 0
        identity = await self.db.scalar(
            select(OmnichannelIdentity).where(
                OmnichannelIdentity.workspace_id == workspace_id,
                OmnichannelIdentity.id == thread.identity_id,
            )
        )
        if identity is None:
            return 0
        specs = (
            (
                OmnichannelChannel.instagram,
                "meta_instagram",
                "instagram-principal",
                "keyword_comment",
                "O plano pode negar um medicamento indicado pelo médico?",
            ),
            (
                OmnichannelChannel.facebook,
                "meta_messenger",
                "facebook-page-principal",
                "page_comment",
                "Preciso de ajuda com home care negado.",
            ),
            (
                OmnichannelChannel.tiktok,
                "tiktok_business",
                "tiktok-business-principal",
                "keyword_comment",
                "Como funciona uma liminar para cirurgia urgente?",
            ),
        )
        created = 0
        for index, (channel, provider, connection_key, event_type, body) in enumerate(specs, 1):
            external_event_id = f"local-social:{identity.id}:{channel.value}:{index}"
            existing = await self.db.scalar(
                select(OmnichannelChannelEvent.id).where(
                    OmnichannelChannelEvent.workspace_id == workspace_id,
                    OmnichannelChannelEvent.provider == provider,
                    OmnichannelChannelEvent.connection_key == connection_key,
                    OmnichannelChannelEvent.external_event_id == external_event_id,
                )
            )
            if existing:
                continue
            _resolution, _event, _message, deduplicated = await self.v6.ingest_event(
                workspace_id,
                CanonicalChannelEventIngest(
                    provider=provider,
                    connection_key=connection_key,
                    channel=channel,
                    event_type=event_type,
                    external_event_id=external_event_id,
                    external_thread_id=f"social:{identity.id}:{channel.value}",
                    external_user_id=f"social-user:{identity.id}:{channel.value}",
                    display_name=identity.display_name,
                    address=f"@{re.sub(r'[^a-z0-9]', '', identity.display_name.lower())[:24]}",
                    canonical_phone=identity.canonical_phone,
                    canonical_email=identity.canonical_email,
                    client_id=identity.client_id,
                    matter_id=thread.matter_id,
                    visibility="public",
                    actionability="reply_required",
                    occurred_at=_now(),
                    public_url=f"https://local.invalid/{channel.value}/comment/{index}",
                    payload={"comment": body, "fixture": True},
                    metadata={
                        "localOnly": True,
                        "externalActionsExecuted": 0,
                        "backfillVersion": OMNICHANNEL_LOCAL_SEED_VERSION,
                    },
                ),
            )
            if not deduplicated:
                created += 1
        return created

    async def _ensure_demo_leads(self, workspace_id: UUID, actor_user_id: UUID) -> int:
        specs = (
            LeadCaptureIngest(
                provider="meta_ads",
                connection_key="meta-ads-principal",
                external_lead_id="local-meta-lead-v6-001",
                external_form_id="form-negativa-saude",
                channel=OmnichannelChannel.facebook,
                name="Lead Meta demonstrativo",
                phone="+5511900000601",
                email="lead.meta.v6@local.klm",
                demand_type="Negativa de medicamento oncológico",
                health_subtheme="medicamento_alto_custo",
                health_object_name="Pembrolizumabe",
                health_insurer="Plano demonstrativo",
                health_denial_reason="Tratamento fora da diretriz interna",
                medical_urgency_hours=24,
                source="Meta Lead Ads",
                campaign="Saúde | Negativa urgente",
                ad_set="Medicamentos oncológicos",
                ad="Direito ao tratamento",
                click_ids={"fbclid": "local-fixture-meta-v6"},
                fields={"fixture": True, "consentimentoContato": True},
                received_at=datetime(2026, 7, 20, 12, 0, tzinfo=UTC),
                proactive_message_template="triagem_saude_v1",
            ),
            LeadCaptureIngest(
                provider="tiktok_ads",
                connection_key="tiktok-ads-principal",
                external_lead_id="local-tiktok-lead-v6-001",
                external_form_id="instant-form-saude",
                channel=OmnichannelChannel.tiktok,
                name="Lead TikTok demonstrativo",
                phone="+5511900000602",
                email="lead.tiktok.v6@local.klm",
                demand_type="Negativa de cirurgia urgente",
                health_subtheme="cirurgia",
                health_object_name="Cirurgia de coluna",
                health_insurer="Plano demonstrativo",
                health_denial_reason="Ausência de cobertura contratual alegada",
                medical_urgency_hours=48,
                source="TikTok Instant Form",
                campaign="Saúde | Cirurgia",
                ad_set="Cirurgia urgente",
                ad="Negativa do plano",
                click_ids={"ttclid": "local-fixture-tiktok-v6"},
                fields={"fixture": True, "consentimentoContato": True},
                received_at=datetime(2026, 7, 20, 12, 5, tzinfo=UTC),
                proactive_message_template="triagem_saude_v1",
            ),
        )
        created = 0
        for payload in specs:
            existing = await self.db.scalar(
                select(OmnichannelChannelEvent.id).where(
                    OmnichannelChannelEvent.workspace_id == workspace_id,
                    OmnichannelChannelEvent.provider == payload.provider,
                    OmnichannelChannelEvent.connection_key == payload.connection_key,
                    OmnichannelChannelEvent.external_event_id == f"lead:{payload.external_lead_id}",
                )
            )
            if existing is not None:
                continue
            await self.v6.ingest_lead_capture(workspace_id, actor_user_id, payload)
            created += 1
        return created
