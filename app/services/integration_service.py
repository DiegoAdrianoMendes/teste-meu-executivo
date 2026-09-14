"""
Service: IntegrationService
Camada de catálogo, API aberta e webhooks genéricos por provider.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.klm_runtime import EXTERNAL_ACTION_BLOCKED_CODE, external_delivery_allowed
from app.db.tenant import set_workspace_context
from app.models.finance import IntegrationConnection, IntegrationProvider, IntegrationStatus
from app.models.matter import Matter
from app.models.timeline_event import TimelineEvent, TimelineEventType
from app.models.workspace import Workspace
from app.schemas.integration import (
    IntegrationCatalogProviderRead,
    IntegrationSetupHintRead,
    OpenApiResourceRead,
    OpenApiSurfaceRead,
    WebhookReceiptRead,
)


PROVIDER_CATALOG = {
    "asaas": {
        "label": "Asaas",
        "category": "finance",
        "description": "Cobrança, assinaturas, PIX, boletos e eventos financeiros.",
        "capabilities": ["cobranca", "pix", "boleto", "assinaturas", "webhooks", "conciliacao"],
        "docs_url": "https://docs.asaas.com/",
        "base_url": "https://api.asaas.com/v3",
        "api_version": "v3",
        "setup_hint": {
            "auth_type": "api_key",
            "webhook_security": "token_validacao_asaas_ou_segundo_fator_por_secret",
            "required_settings": ["apiKeySecretName"],
            "setup_steps": [
                "Crie ou reutilize um segredo no Secrets Manager com a API key do Asaas.",
                "Cadastre o nome do segredo em apiKeySecretName.",
                "Configure o webhook do Asaas apontando para a URL pública do workspace.",
                "Cadastre webhookSecret para a assinatura HMAC no endpoint genérico.",
            ],
        },
    },
    "conta_azul": {
        "label": "Conta Azul",
        "category": "finance",
        "description": "Sincronização contábil, financeiro, clientes e faturamento.",
        "capabilities": ["financeiro", "faturamento", "clientes", "webhooks", "conciliacao"],
        "docs_url": "https://developers.contaazul.com/",
        "base_url": "https://api.contaazul.com",
        "api_version": "v1",
        "setup_hint": {
            "auth_type": "oauth2",
            "webhook_security": "oauth2_e_webhook_assinado_pelo_provider",
            "required_settings": ["clientIdSecretName", "clientSecretSecretName", "redirectUri"],
            "setup_steps": [
                "Crie as credenciais OAuth no portal de desenvolvedores da Conta Azul.",
                "Salve clientId e clientSecret no Secrets Manager.",
                "Cadastre os nomes desses segredos e a redirectUri da integração.",
                "Conclua a autorização OAuth para obter refresh token antes de marcar como conectado.",
            ],
        },
    },
    "zapsign": {
        "label": "ZapSign",
        "category": "signature",
        "description": "Assinatura eletrônica com acompanhamento de documentos e callbacks.",
        "capabilities": ["assinatura", "documentos", "webhooks"],
        "docs_url": "https://developers.zapsign.com.br/",
        "base_url": "https://api.zapsign.com.br/api/v1",
        "api_version": "v1",
        "setup_hint": {
            "auth_type": "bearer_token",
            "webhook_security": "bearer_token_na_rota_dedicada",
            "required_settings": ["apiTokenSecretName"],
            "setup_steps": [
                "Crie um token de API da ZapSign.",
                "Salve o token no Secrets Manager.",
                "Cadastre apiTokenSecretName no workspace.",
                "Aponte o webhook da ZapSign para a rota dedicada exibida no catálogo.",
            ],
        },
    },
    "docusign": {
        "label": "DocuSign",
        "category": "signature",
        "description": "Assinatura eletrônica, envelopes e callbacks de status.",
        "capabilities": ["assinatura", "envelopes", "webhooks"],
        "docs_url": "https://developers.docusign.com/",
        "base_url": "https://demo.docusign.net/restapi",
        "api_version": "v2.1",
        "setup_hint": {
            "auth_type": "oauth2",
            "webhook_security": "oauth2_connect_hmac_ou_chave_do_provider",
            "required_settings": ["integrationKeySecretName", "userId", "accountId"],
            "setup_steps": [
                "Crie a integration key no DocuSign.",
                "Salve as credenciais no Secrets Manager.",
                "Cadastre integrationKeySecretName, userId e accountId.",
                "Configure o Connect/Webhook no DocuSign com a URL pública do workspace.",
            ],
        },
    },
    "custom": {
        "label": "API aberta / Custom",
        "category": "open_platform",
        "description": "Webhook e integração sob medida para outros CRMs, ERPs ou motores internos.",
        "capabilities": ["webhooks", "open_api", "sync_custom"],
        "docs_url": None,
        "base_url": None,
        "api_version": None,
        "setup_hint": {
            "auth_type": "custom",
            "webhook_security": "assinatura_hmac_x_klm_signature",
            "required_settings": [],
            "setup_steps": [
                "Defina o provider externo e o contrato de eventos.",
                "Cadastre webhookSecret para a assinatura HMAC obrigatória em X-KLM-Signature.",
                "Use a OpenAPI do workspace para sincronização bidirecional.",
            ],
        },
    },
    "pluga": {
        "label": "Pluga",
        "category": "automation",
        "description": "Automações no-code que conectam mais de 100 ferramentas brasileiras por webhook, sem código.",
        "capabilities": ["webhooks", "automation", "sync_leads"],
        "docs_url": "https://github.com/PlugaDotCo/pluga-docs-developers",
        "base_url": None,
        "api_version": None,
        "setup_hint": {
            "auth_type": "webhook",
            "webhook_security": "assinatura_hmac_x_klm_signature",
            "required_settings": ["webhookSecret"],
            "setup_steps": [
                "Na Pluga, crie uma automação (Pluga) escolhendo a ferramenta de origem como gatilho.",
                "Adicione a ação \"Webhook\" e cole a URL do webhook de leads deste workspace (exibida abaixo).",
                "Mapeie os campos de nome, e-mail e telefone do gatilho para o payload do webhook.",
                "Cadastre webhookSecret para a assinatura HMAC e envie um evento de teste para confirmar o recebimento.",
            ],
        },
    },
    "zapier": {
        "label": "Zapier",
        "category": "automation",
        "description": "Automações no-code com milhares de apps internacionais, conectando qualquer origem ao workspace por webhook.",
        "capabilities": ["webhooks", "automation", "sync_leads"],
        "docs_url": "https://zapier.com/apps/webhook/integrations",
        "base_url": None,
        "api_version": None,
        "setup_hint": {
            "auth_type": "webhook",
            "webhook_security": "assinatura_hmac_x_klm_signature",
            "required_settings": ["webhookSecret"],
            "setup_steps": [
                "No Zapier, crie um novo Zap com o app de origem desejado como trigger.",
                "Escolha a ação \"Webhooks by Zapier\" → \"POST\" e cole a URL do webhook de leads deste workspace.",
                "Mapeie os campos do trigger para o corpo JSON esperado pelo endpoint.",
                "Cadastre webhookSecret para a assinatura HMAC, ative o Zap e confirme o primeiro evento recebido.",
            ],
        },
    },
    "google_ads": {
        "label": "Google Ads",
        "category": "ads",
        "description": "Leads de formulários instantâneos e conversões offline via MCC do Meu Executivo, com Data Manager API.",
        "capabilities": ["lead_form_webhook", "conversions", "oauth2"],
        "docs_url": "https://developers.google.com/google-ads/api",
        "base_url": "https://googleads.googleapis.com",
        "api_version": "v25",
        "setup_hint": {
            "auth_type": "oauth2_developer_token",
            "webhook_security": "google_key_constant_time_e_dedupe_por_lead_id",
            "required_settings": ["googleKeySecretName", "developerTokenSecretName", "loginCustomerId"],
            "setup_steps": [
                "Na sua conta Google Ads, vá em Ferramentas → Acesso e segurança e vincule a conta à MCC do Meu Executivo.",
                "Aceite o convite de vinculação enviado pela MCC para liberar developer token e API.",
                "No formulário de lead da campanha, configure o Lead Form Webhook com a URL deste workspace e o google_key gerado.",
                "Cadastre googleKeySecretName (validação em tempo constante) e developerTokenSecretName no Secrets Manager.",
                "Para conversões offline, use a Data Manager API (ClickConversionService está bloqueado desde 15/06/2026).",
            ],
        },
    },
    "google_tag_manager": {
        "label": "Google Tag Manager",
        "category": "tracking",
        "description": "Contêiner de tags web e server-side com Consent Mode v2 para o funil de leads e conversões.",
        "capabilities": ["tag_management", "consent_mode_v2", "server_side_tagging"],
        "docs_url": "https://developers.google.com/tag-platform/tag-manager",
        "base_url": None,
        "api_version": None,
        "setup_hint": {
            "auth_type": "none",
            "webhook_security": "nao_aplicavel",
            "required_settings": ["containerId"],
            "setup_steps": [
                "Crie um contêiner GTM do tipo Web em tagmanager.google.com e instale o snippet no site.",
                "Configure o banner de consentimento (CMP/LGPD) para gravar o Consent Mode v2 (ad_storage, analytics_storage, ad_user_data, ad_personalization).",
                "Adicione a tag de configuração do GA4 e os eventos de formulário do site.",
                "Publique o contêiner e confirme os sinais de consentimento no modo de visualização antes de liberar em produção.",
            ],
        },
    },
    "ga4": {
        "label": "Google Analytics 4",
        "category": "tracking",
        "description": "Eventos de funil server-side (lead recebido, proposta enviada, contrato assinado, cobrança paga) via Measurement Protocol.",
        "capabilities": ["measurement_protocol", "server_side_events"],
        "docs_url": "https://developers.google.com/analytics/devguides/collection/protocol/ga4/sending-events",
        "base_url": "https://www.google-analytics.com/mp/collect",
        "api_version": None,
        "setup_hint": {
            "auth_type": "measurement_id_api_secret",
            "webhook_security": "segredo_de_servidor_nunca_no_cliente",
            "required_settings": ["measurementIdSecretName", "apiSecretSecretName"],
            "setup_steps": [
                "No GA4, abra o data stream desejado e copie o Measurement ID.",
                "Em Measurement Protocol API secrets, crie um novo api_secret para esse stream.",
                "Cadastre measurementIdSecretName e apiSecretSecretName no Secrets Manager deste workspace.",
                "Eventos retroativos são aceitos em até 3 dias; nomes de eventos reservados da coleta automática não podem ser enviados por aqui.",
            ],
        },
    },
    "meta_ads": {
        "label": "Meta Ads (Lead Ads)",
        "category": "ads",
        "description": "Leads de formulários instantâneos do Facebook/Instagram via webhook leadgen assinado pela página conectada.",
        "capabilities": ["lead_form_webhook", "webhooks"],
        "docs_url": "https://developers.facebook.com/docs/marketing-api/guides/lead-ads/retrieving",
        "base_url": "https://graph.facebook.com",
        "api_version": settings.META_GRAPH_API_VERSION,
        "setup_hint": {
            "auth_type": "meta_app_page_token",
            "webhook_security": "verify_token_e_assinatura_x_hub_signature",
            "required_settings": ["appSecretSecretName", "pageAccessTokenSecretName", "verifyToken"],
            "setup_steps": [
                "No Meta for Developers, vincule o app à página do Facebook e à conta de anúncios que publica os formulários.",
                "Em Webhooks, cadastre a URL deste workspace e o verify token e assine o campo leadgen.",
                "Cadastre appSecretSecretName e pageAccessTokenSecretName no Secrets Manager.",
                "Envie um lead de teste pelo formulário e confirme campanha, conjunto, anúncio e formulário no recibo.",
            ],
        },
    },
    "meta_messenger": {
        "label": "Meta Messenger",
        "category": "channels",
        "description": "Mensagens e postbacks de páginas Meta consolidados na mesma conversa do lead.",
        "capabilities": ["messaging", "webhooks"],
        "docs_url": "https://developers.facebook.com/docs/messenger-platform/webhooks",
        "base_url": "https://graph.facebook.com",
        "api_version": settings.META_GRAPH_API_VERSION,
        "setup_hint": {
            "auth_type": "meta_app_page_token",
            "webhook_security": "verify_token_e_assinatura_x_hub_signature",
            "required_settings": ["appSecretSecretName", "pageAccessTokenSecretName", "verifyToken"],
            "setup_steps": [
                "Vincule a página Meta ao aplicativo e confirme o administrador responsável.",
                "Em Webhooks, cadastre a URL deste workspace, o verify token e assine messages, messaging_postbacks e message_deliveries.",
                "Cadastre appSecretSecretName e pageAccessTokenSecretName no Secrets Manager.",
                "Conclua a revisão do aplicativo antes de atender contas externas e teste uma mensagem de entrada.",
            ],
        },
    },
    "meta_instagram": {
        "label": "Meta Instagram",
        "category": "channels",
        "description": "Mensagens, comentários e menções de uma conta profissional do Instagram na identidade e conversa canônicas.",
        "capabilities": ["messaging", "comments", "webhooks"],
        "docs_url": "https://developers.facebook.com/docs/instagram-platform/webhooks",
        "base_url": "https://graph.facebook.com",
        "api_version": settings.META_GRAPH_API_VERSION,
        "setup_hint": {
            "auth_type": "meta_app_page_token",
            "webhook_security": "verify_token_e_assinatura_x_hub_signature",
            "required_settings": ["appSecretSecretName", "pageAccessTokenSecretName", "verifyToken"],
            "setup_steps": [
                "Vincule a conta profissional do Instagram à página e ao aplicativo Meta exigidos pelo fluxo escolhido.",
                "Em Webhooks, cadastre a URL deste workspace, valide o challenge e assine messages, comments e mentions.",
                "Cadastre appSecretSecretName e pageAccessTokenSecretName no Secrets Manager.",
                "Conclua as permissões e a revisão do aplicativo antes de atender contas externas.",
            ],
        },
    },
    "whatsapp_cloud": {
        "label": "WhatsApp Cloud API",
        "category": "channels",
        "description": "Número oficial WABA conectado via Meta Cloud API para mensagens, templates e campanhas oficiais.",
        "capabilities": ["messaging", "templates", "campaigns", "webhooks"],
        "docs_url": "https://developers.facebook.com/docs/whatsapp/cloud-api",
        "base_url": "https://graph.facebook.com",
        "api_version": settings.META_GRAPH_API_VERSION,
        "setup_hint": {
            "auth_type": "waba_permanent_token",
            "webhook_security": "verify_token_e_assinatura_x_hub_signature",
            "required_settings": ["wabaId", "phoneNumberId", "accessTokenSecretName", "verifyToken"],
            "setup_steps": [
                "Use o fluxo nativo de conexão do WhatsApp já existente no produto: Configurações → Integrações → WhatsApp.",
                "Cadastre a WABA, o número, o token permanente e o verify token do webhook por essa mesma tela — não duplique aqui.",
                "Conclua a validação local exibida na tela antes de sincronizar templates aprovados pela Meta.",
                "Após validar, acompanhe campanhas e status de entrega diretamente na Central de WhatsApp do produto.",
            ],
        },
    },
}

DEDICATED_WEBHOOK_PATHS = {
    IntegrationProvider.zapsign: "/api/v1/webhooks/zapsign",
    # WhatsApp Cloud usa o webhook único da Meta (roteado por phone_number_id),
    # não o webhook genérico por workspace/provider — ver app/api/v1/endpoints/whatsapp.py.
    IntegrationProvider.whatsapp_cloud: "/api/v1/whatsapp/webhook",
}
DEDICATED_WEBHOOK_PROVIDERS = frozenset(DEDICATED_WEBHOOK_PATHS)
WEBHOOK_EVENT_ID_KEYS = (
    "event_id",
    "eventId",
    "webhook_id",
    "webhookId",
    "delivery_id",
    "deliveryId",
    "id",
)


class IntegrationService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_catalog(self, workspace_id: UUID, workspace_slug: str) -> list[IntegrationCatalogProviderRead]:
        items: list[IntegrationCatalogProviderRead] = []
        for key, provider in PROVIDER_CATALOG.items():
            provider_enum = IntegrationProvider(key)
            items.append(
                IntegrationCatalogProviderRead(
                    key=key,
                    label=provider["label"],
                    category=provider["category"],
                    description=provider["description"],
                    capabilities=provider["capabilities"],
                    native=key != "custom",
                    default_webhook_path=self._webhook_url(workspace_slug, provider_enum),
                    docs_url=provider["docs_url"],
                    setup_hint=IntegrationSetupHintRead(
                        auth_type=provider["setup_hint"]["auth_type"],
                        webhook_security=provider["setup_hint"]["webhook_security"],
                        required_settings=provider["setup_hint"]["required_settings"],
                        recommended_base_url=provider.get("base_url"),
                        recommended_api_version=provider.get("api_version"),
                        setup_steps=provider["setup_hint"]["setup_steps"],
                    ),
                )
            )
        return items

    async def get_open_api_surface(self, workspace_id: UUID) -> OpenApiSurfaceRead:
        workspace = await self._get_workspace_by_id(workspace_id)
        base_url = settings.PAPERCLIP_PUBLIC_URL.rstrip("/")
        docs_enabled = not settings.is_production
        openapi_enabled = docs_enabled or settings.OPENAPI_ENABLED_IN_PRODUCTION
        docs_url = f"{base_url}/docs" if docs_enabled else None
        return OpenApiSurfaceRead(
            workspace_id=workspace.id,
            workspace_slug=workspace.slug,
            base_url=base_url,
            docs_url=docs_url,
            openapi_url=f"{base_url}/openapi.json" if openapi_enabled else None,
            webhook_base_url=self._webhook_base(workspace.slug),
            resources=[
                OpenApiResourceRead(
                    key="matters_list",
                    label="Listar cards",
                    path="/api/v1/matters",
                    method="GET",
                    description="Consulta cards, leads e fases com filtros tenant-aware.",
                ),
                OpenApiResourceRead(
                    key="finance_entries",
                    label="Lançamentos financeiros",
                    path="/api/v1/finance/entries",
                    method="GET",
                    description="Consulta cobrança, receitas, despesas e pendências financeiras.",
                ),
                OpenApiResourceRead(
                    key="reports_metrics",
                    label="KPIs e relatórios",
                    path="/api/v1/reports/metrics",
                    method="GET",
                    description="Retorna KPIs, funil, receita por origem e sinais analíticos.",
                ),
                OpenApiResourceRead(
                    key="whatsapp_campaigns",
                    label="Campanhas oficiais de WhatsApp",
                    path="/api/v1/whatsapp/campaigns",
                    method="GET",
                    description="Lista campanhas oficiais, métricas de entrega e estágio operacional por workspace.",
                ),
                OpenApiResourceRead(
                    key="whatsapp_templates_sync",
                    label="Templates oficiais da Meta",
                    path="/api/v1/whatsapp/meta-templates/sync",
                    method="POST",
                    description="Sincroniza templates aprovados do WABA para uso em campanhas e mensagens oficiais.",
                ),
                OpenApiResourceRead(
                    key="whatsapp_drawer",
                    label="Drawer do lead no inbox",
                    path="/api/v1/whatsapp/conversations/{contact_id}/drawer",
                    method="GET",
                    description="Carrega o contexto completo do lead, card, reuniões e notas internas do atendimento.",
                ),
                OpenApiResourceRead(
                    key="public_api_contacts",
                    label="API externa: contatos",
                    path="/api/v1/public-api/contacts",
                    method="GET",
                    description="Lista contatos do workspace via token externo X-KLM-API-Key, com escopo e rate limit.",
                ),
                OpenApiResourceRead(
                    key="public_api_leads",
                    label="API externa: criar lead",
                    path="/api/v1/public-api/leads",
                    method="POST",
                    description="Cria lead/card no workspace e vincula contato WhatsApp quando houver linha padrão conectada.",
                ),
                OpenApiResourceRead(
                    key="public_api_template_send",
                    label="API externa: disparar template oficial",
                    path="/api/v1/public-api/templates/send",
                    method="POST",
                    description="Dispara template oficial aprovado da Meta com auditoria, escopo e trilha externa por token.",
                ),
                OpenApiResourceRead(
                    key="public_api_campaigns",
                    label="API externa: criar campanha",
                    path="/api/v1/public-api/campaigns",
                    method="POST",
                    description="Cria campanhas oficiais de WhatsApp por token externo, com opt-in, segmentação e governança por workspace.",
                ),
                OpenApiResourceRead(
                    key="provider_webhook",
                    label="Webhook genérico por provider",
                    path=f"/api/v1/integrations/webhooks/{workspace.slug}/{{provider}}",
                    method="POST",
                    description="Recebe eventos nativos ou custom e injeta no card/timeline quando houver vínculo.",
                ),
            ],
        )

    async def ingest_provider_webhook(
        self,
        *,
        provider: str,
        workspace_slug: str,
        payload: dict[str, Any],
        payload_bytes: bytes = b"",
        signature: str = "",
    ) -> WebhookReceiptRead:
        provider_enum = self._require_generic_provider(provider)
        workspace = await self._get_workspace_by_slug(workspace_slug)
        await set_workspace_context(self.db, workspace.id)
        connection = await self._find_connection_for_provider(workspace.id, provider_enum)
        if connection is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conexão ativa para o provider não encontrada.",
            )
        from app.services.financial_upgrade_service import enforce_financial_plan_only

        enforce_financial_plan_only(
            provider=connection.provider,
            connection_status=connection.status,
            label=connection.label,
            base_url=connection.base_url,
            external_account_id=connection.external_account_id,
            webhook_url=connection.webhook_url,
            capabilities=connection.capabilities,
            settings=connection.settings,
            notes=connection.notes,
            last_sync_at=connection.last_sync_at,
        )
        self._require_external_webhook_delivery()
        if not payload_bytes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Corpo bruto do webhook ausente.",
            )
        self._verify_signature(connection, payload_bytes, signature)

        matter = await self._match_matter(workspace.id, payload)
        provider_key = provider_enum.value
        title = self._event_title(provider_key, payload)
        delivery_key = self._webhook_delivery_key(payload)
        canonical_payload = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        payload_digest = hashlib.sha256(canonical_payload).hexdigest()
        existing_event = None
        deterministic_event_id = None
        if matter and delivery_key:
            deterministic_event_id = uuid5(
                NAMESPACE_URL,
                f"klm:integration:{matter.id}:{provider_key}:{delivery_key}",
            )
            existing_event = await self.db.scalar(
                select(TimelineEvent).where(TimelineEvent.id == deterministic_event_id)
            )
        if matter and delivery_key and existing_event is None:
            existing_event = await self._find_webhook_event(
                matter.id,
                provider_key,
                delivery_key,
            )
        if existing_event:
            self._validate_webhook_replay(existing_event, payload_digest)
        event_data = {
            "provider": provider_key,
            "raw": payload,
            "connectionId": str(connection.id),
            "deliveryKey": delivery_key,
            "payloadDigest": payload_digest,
        }

        if matter and existing_event is None:
            event = TimelineEvent(
                matter_id=matter.id,
                event_type=TimelineEventType.webhook_received,
                title=title,
                description=str(payload.get("description") or payload.get("event") or title)[:2000],
                event_data=event_data,
            )
            if deterministic_event_id is not None:
                event.id = deterministic_event_id
            try:
                async with self.db.begin_nested():
                    self.db.add(event)
                    await self.db.flush()
            except IntegrityError:
                concurrent_event = await self.db.scalar(
                    select(TimelineEvent)
                    .where(TimelineEvent.id == deterministic_event_id)
                    .execution_options(populate_existing=True)
                )
                if concurrent_event is None:
                    raise
                self._validate_webhook_replay(concurrent_event, payload_digest)

        connection.last_sync_at = self._utc_now()

        await self.db.flush()

        return WebhookReceiptRead(
            provider=provider_key,
            workspace_slug=workspace.slug,
            accepted=True,
            connection_found=True,
            matched_matter_id=matter.id if matter else None,
            event_title=title,
            received_at=self._utc_now(),
        )

    async def ensure_default_connection_metadata(
        self,
        workspace_id: UUID,
        provider: IntegrationProvider,
        label: str,
        base_url: Optional[str],
        webhook_url: Optional[str],
        capabilities: list[str],
    ) -> dict[str, Any]:
        workspace = await self._get_workspace_by_id(workspace_id)
        provider_key = provider.value
        provider_defaults = PROVIDER_CATALOG.get(provider_key, {})
        return {
            "provider": provider_key,
            "label": label,
            "baseUrl": base_url or provider_defaults.get("base_url"),
            "webhookUrl": webhook_url or self._webhook_url(workspace.slug, provider),
            "apiVersion": provider_defaults.get("api_version"),
            "capabilities": capabilities or provider_defaults.get("capabilities", []),
        }

    def validate_connection_payload(
        self,
        *,
        provider: IntegrationProvider,
        status: IntegrationStatus,
        settings_payload: Optional[dict[str, Any]],
    ) -> None:
        provider_defaults = PROVIDER_CATALOG.get(provider.value, {})
        required_settings = provider_defaults.get("setup_hint", {}).get("required_settings", [])
        if status != IntegrationStatus.connected:
            return

        settings_payload = settings_payload or {}
        missing = [key for key in required_settings if not settings_payload.get(key)]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "INT-001",
                    "message": "Integração marcada como conectada sem os segredos obrigatórios configurados.",
                    "missingSettings": missing,
                    "provider": provider.value,
                },
            )

    async def _get_workspace_by_slug(self, workspace_slug: str) -> Workspace:
        result = await self.db.execute(
            select(Workspace).where(Workspace.slug == workspace_slug, Workspace.deleted_at.is_(None))
        )
        workspace = result.scalar_one_or_none()
        if not workspace:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace não encontrado.")
        return workspace

    async def _get_workspace_by_id(self, workspace_id: UUID) -> Workspace:
        workspace = await self.db.get(Workspace, workspace_id)
        if not workspace or workspace.deleted_at is not None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace não encontrado.")
        return workspace

    async def _find_connection_for_provider(
        self,
        workspace_id: UUID,
        provider: IntegrationProvider,
    ) -> Optional[IntegrationConnection]:
        result = await self.db.execute(
            select(IntegrationConnection)
            .where(
                IntegrationConnection.workspace_id == workspace_id,
                IntegrationConnection.provider == provider,
                IntegrationConnection.status == IntegrationStatus.connected,
            )
            .order_by(IntegrationConnection.updated_at.desc())
        )
        return result.scalars().first()

    def _verify_signature(
        self,
        connection: IntegrationConnection,
        payload_bytes: bytes,
        signature: str,
    ) -> None:
        from app.services.integration_secrets import reveal_integration_secret

        webhook_secret = reveal_integration_secret(connection.settings, "webhookSecret")
        if not webhook_secret.strip():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Webhook não configurado para esta conexão.",
            )
        expected = hmac.new(
            webhook_secret.encode("utf-8"),
            payload_bytes,
            hashlib.sha256,
        ).hexdigest()
        normalized_signature = signature.strip().removeprefix("sha256=")
        if not hmac.compare_digest(expected, normalized_signature):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Assinatura do webhook inválida.")

    @staticmethod
    def _require_external_webhook_delivery() -> None:
        if not external_delivery_allowed():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": EXTERNAL_ACTION_BLOCKED_CODE,
                    "message": (
                        "Webhook externo bloqueado enquanto entregas externas estiverem "
                        "desabilitadas."
                    ),
                },
            )

    @staticmethod
    def _require_generic_provider(provider: str) -> IntegrationProvider:
        try:
            provider_enum = IntegrationProvider(provider)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conexão ativa para o provider não encontrada.",
            ) from None
        if provider_enum in DEDICATED_WEBHOOK_PROVIDERS:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Este provider exige o handler de webhook dedicado.",
            )
        return provider_enum

    async def _find_webhook_event(
        self,
        matter_id: UUID,
        provider: str,
        delivery_key: str,
    ) -> Optional[TimelineEvent]:
        result = await self.db.execute(
            select(TimelineEvent).where(
                TimelineEvent.matter_id == matter_id,
                TimelineEvent.event_type == TimelineEventType.webhook_received,
            )
        )
        for event in result.scalars():
            event_data = event.event_data or {}
            if (
                event_data.get("provider") == provider
                and event_data.get("deliveryKey") == delivery_key
            ):
                return event
        return None

    @staticmethod
    def _validate_webhook_replay(
        event: TimelineEvent,
        payload_digest: str,
    ) -> None:
        existing_digest = str((event.event_data or {}).get("payloadDigest") or "")
        if not existing_digest or not hmac.compare_digest(existing_digest, payload_digest):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="ID de webhook reutilizado com payload diferente.",
            )

    async def _match_matter(self, workspace_id: UUID, payload: dict[str, Any]) -> Optional[Matter]:
        matter_id = payload.get("matter_id") or payload.get("matterId")
        case_number = payload.get("case_number") or payload.get("caseNumber")
        external_ref = payload.get("external_ref") or payload.get("externalRef")

        conditions = []
        if matter_id:
            conditions.append(Matter.id == matter_id)
        if case_number:
            conditions.append(Matter.case_number == case_number)
        if external_ref:
            conditions.append(Matter.case_number == external_ref)
        if not conditions:
            return None

        result = await self.db.execute(
            select(Matter).where(
                Matter.workspace_id == workspace_id,
                Matter.deleted_at.is_(None),
                or_(*conditions),
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    def _event_title(provider: str, payload: dict[str, Any]) -> str:
        return (
            payload.get("title")
            or payload.get("event")
            or payload.get("type")
            or f"Evento recebido de {provider}"
        )

    @staticmethod
    def _webhook_delivery_key(payload: dict[str, Any]) -> Optional[str]:
        event_id = next(
            (
                payload.get(key)
                for key in WEBHOOK_EVENT_ID_KEYS
                if payload.get(key) is not None
            ),
            None,
        )
        if isinstance(event_id, bool) or not isinstance(event_id, (str, int)):
            return None
        event_type = payload.get("event") or payload.get("type") or "event"
        if isinstance(event_type, bool) or not isinstance(event_type, (str, int)):
            event_type = "event"
        return f"{event_type}:{event_id}"

    @staticmethod
    def _webhook_base(workspace_slug: str) -> str:
        return f"{settings.PAPERCLIP_PUBLIC_URL.rstrip('/')}/api/v1/integrations/webhooks/{workspace_slug}"

    @classmethod
    def _webhook_url(cls, workspace_slug: str, provider: IntegrationProvider) -> str:
        dedicated_path = DEDICATED_WEBHOOK_PATHS.get(provider)
        if dedicated_path:
            return f"{settings.PAPERCLIP_PUBLIC_URL.rstrip('/')}{dedicated_path}"
        return f"{cls._webhook_base(workspace_slug)}/{provider.value}"

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)
