"""
WorkspacePolicyService
Configura e resolve políticas de automação por workspace/etapa.

Escopo atual do produto:
- entrada e qualificação
- fechamento comercial
- coleta de informações e documentos após fechamento
- secretaria, financeiro e pós-venda
- apoio pontual do assistente jurídico sem avançar para contencioso
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.data.legal_area_catalog import LEGAL_AREA_CATALOG
from app.models.flow import Flow
from app.models.klm_operations import AreaBlueprint
from app.models.workspace import Workspace
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership, WorkspaceRole
from app.schemas.agent_policy import (
    AutomationPreset,
    AutomationPresetRead,
    AutomationStageMode,
    ContextMode,
    CostProfile,
    StagePolicyRead,
    WorkspaceAutomationSettingsRead,
    WorkspaceAutomationSettingsUpdate,
)
from app.schemas.control_plane import (
    AdminControlPlaneRead,
    AdminControlPlaneUpdate,
    AgentPromptPolicyRead,
    AiGovernanceRead,
    BrandingSettingsRead,
    FlowTemplateReferenceRead,
    ModelCatalogItemRead,
    ModelTier,
    NotificationSettingsRead,
    PracticeAreaStagePromptRead,
    PracticeAreaTemplateRead,
    SecurityAuditSettingsRead,
    WorkspaceAreaActivationRead,
    WorkspaceOperationsRead,
    WorkspaceOperationsUpdate,
)

STAGE_CATALOG: list[dict[str, str]] = [
    {
        "stage_key": "entrada",
        "label": "Entrada e SDR",
        "description": "Recebe o lead, entende a dor inicial e qualifica o caso.",
    },
    {
        "stage_key": "proposta_fechamento",
        "label": "Proposta e Fechamento",
        "description": "Conduz o fechamento comercial com closer humano ou apoio assistido.",
    },
    {
        "stage_key": "secretaria_agendamento",
        "label": "Secretaria e Agendamento",
        "description": "Confirma horários, follow-up e reduz faltas após o avanço comercial.",
    },
    {
        "stage_key": "coleta_dados",
        "label": "Coleta de Informações e Documentos",
        "description": "Organiza checklist, cadastro e recebimento de documentos após o fechamento.",
    },
    {
        "stage_key": "assistente_juridico",
        "label": "Assistente Jurídico de Apoio",
        "description": "Atua apenas em dúvidas pontuais e estimativas preliminares durante o comercial/onboarding.",
    },
    {
        "stage_key": "financeiro_cobranca",
        "label": "Financeiro e Cobrança",
        "description": "Organiza honorários, cobranças e validação financeira do onboarding.",
    },
    {
        "stage_key": "pos_venda_relacionamento",
        "label": "Pós-venda e Relacionamento",
        "description": "Mantém o cliente acompanhado após assinatura e durante a coleta documental.",
    },
]


DEFAULT_STAGE_POLICIES: dict[str, dict[str, Any]] = {
    "entrada": {
        "mode": AutomationStageMode.ai_only.value,
        "agent_key": "klm_sdr",
        "model": settings.VERTEX_MODEL,
        "cost_profile": CostProfile.economic.value,
        "context_mode": ContextMode.balanced.value,
        "use_rag": True,
        "multimodal_enabled": True,
        "human_review_required": False,
    },
    "proposta_fechamento": {
        "mode": AutomationStageMode.human_only.value,
        "agent_key": "closer_humano",
        "model": None,
        "cost_profile": CostProfile.balanced.value,
        "context_mode": ContextMode.balanced.value,
        "use_rag": True,
        "multimodal_enabled": False,
        "human_review_required": True,
    },
    "secretaria_agendamento": {
        "mode": AutomationStageMode.human_in_the_loop.value,
        "agent_key": "secretaria_hibrida",
        "model": "gemini-3.1-flash-lite",
        "cost_profile": CostProfile.economic.value,
        "context_mode": ContextMode.essential.value,
        "use_rag": True,
        "multimodal_enabled": True,
        "human_review_required": False,
    },
    "coleta_dados": {
        "mode": AutomationStageMode.human_in_the_loop.value,
        "agent_key": "administrativo_ops",
        "model": "gemini-3.1-flash-lite",
        "cost_profile": CostProfile.balanced.value,
        "context_mode": ContextMode.balanced.value,
        "use_rag": True,
        "multimodal_enabled": True,
        "human_review_required": True,
    },
    "assistente_juridico": {
        "mode": AutomationStageMode.human_in_the_loop.value,
        "agent_key": "assistente_juridico",
        "model": settings.VERTEX_MODEL,
        "cost_profile": CostProfile.balanced.value,
        "context_mode": ContextMode.full.value,
        "use_rag": True,
        "multimodal_enabled": True,
        "human_review_required": True,
    },
    "financeiro_cobranca": {
        "mode": AutomationStageMode.human_in_the_loop.value,
        "agent_key": "financeiro_ops",
        "model": "gemini-3.1-flash-lite",
        "cost_profile": CostProfile.economic.value,
        "context_mode": ContextMode.balanced.value,
        "use_rag": True,
        "multimodal_enabled": False,
        "human_review_required": True,
    },
    "pos_venda_relacionamento": {
        "mode": AutomationStageMode.ai_only.value,
        "agent_key": "pos_venda_agent",
        "model": "gemini-3.1-flash-lite",
        "cost_profile": CostProfile.economic.value,
        "context_mode": ContextMode.balanced.value,
        "use_rag": True,
        "multimodal_enabled": True,
        "human_review_required": False,
    },
}


PRESET_CATALOG: dict[AutomationPreset, dict[str, str]] = {
    AutomationPreset.economic: {
        "label": "Operação econômica",
        "description": "Usa IA leve e concentra o esforço humano apenas no fechamento, financeiro e conferências críticas.",
    },
    AutomationPreset.balanced: {
        "label": "Equilíbrio recomendado",
        "description": "Mantém closer humano, coleta assistida e contexto mais forte onde a operação precisa de segurança.",
    },
    AutomationPreset.supervised: {
        "label": "Máxima supervisão",
        "description": "Prioriza revisão humana e contexto completo em quase todas as etapas do onboarding.",
    },
}


PRESET_STAGE_OVERRIDES: dict[AutomationPreset, dict[str, dict[str, Any]]] = {
    AutomationPreset.economic: {
        "entrada": {
            "mode": AutomationStageMode.ai_only.value,
            "model": "gemini-3.1-flash-lite",
            "cost_profile": CostProfile.economic.value,
            "context_mode": ContextMode.essential.value,
            "human_review_required": False,
        },
        "proposta_fechamento": {
            "mode": AutomationStageMode.human_only.value,
            "model": None,
            "human_review_required": True,
        },
        "secretaria_agendamento": {
            "mode": AutomationStageMode.ai_only.value,
            "cost_profile": CostProfile.economic.value,
            "context_mode": ContextMode.essential.value,
            "human_review_required": False,
        },
        "coleta_dados": {
            "mode": AutomationStageMode.human_in_the_loop.value,
            "model": "gemini-3.1-flash-lite",
            "cost_profile": CostProfile.economic.value,
            "context_mode": ContextMode.balanced.value,
            "human_review_required": True,
        },
        "assistente_juridico": {
            "mode": AutomationStageMode.human_in_the_loop.value,
            "model": settings.VERTEX_MODEL,
            "cost_profile": CostProfile.balanced.value,
            "context_mode": ContextMode.balanced.value,
            "human_review_required": True,
        },
        "financeiro_cobranca": {
            "mode": AutomationStageMode.ai_only.value,
            "model": "gemini-3.1-flash-lite",
            "cost_profile": CostProfile.economic.value,
            "context_mode": ContextMode.essential.value,
            "human_review_required": False,
        },
        "pos_venda_relacionamento": {
            "mode": AutomationStageMode.ai_only.value,
            "model": "gemini-3.1-flash-lite",
            "cost_profile": CostProfile.economic.value,
            "context_mode": ContextMode.essential.value,
            "human_review_required": False,
        },
    },
    AutomationPreset.balanced: {
        "entrada": {
            "mode": AutomationStageMode.ai_only.value,
            "model": "gemini-3.1-flash-lite",
            "cost_profile": CostProfile.balanced.value,
            "context_mode": ContextMode.balanced.value,
        },
        "proposta_fechamento": {
            "mode": AutomationStageMode.human_only.value,
            "model": None,
            "human_review_required": True,
        },
        "secretaria_agendamento": {
            "mode": AutomationStageMode.human_in_the_loop.value,
            "model": "gemini-3.1-flash-lite",
            "cost_profile": CostProfile.economic.value,
            "context_mode": ContextMode.balanced.value,
            "human_review_required": False,
        },
        "coleta_dados": {
            "mode": AutomationStageMode.human_in_the_loop.value,
            "model": "gemini-3.1-flash-lite",
            "cost_profile": CostProfile.balanced.value,
            "context_mode": ContextMode.balanced.value,
            "human_review_required": True,
        },
        "assistente_juridico": {
            "mode": AutomationStageMode.human_in_the_loop.value,
            "model": settings.VERTEX_MODEL,
            "cost_profile": CostProfile.balanced.value,
            "context_mode": ContextMode.full.value,
            "human_review_required": True,
        },
        "financeiro_cobranca": {
            "mode": AutomationStageMode.human_in_the_loop.value,
            "model": "gemini-3.1-flash-lite",
            "cost_profile": CostProfile.economic.value,
            "context_mode": ContextMode.balanced.value,
            "human_review_required": True,
        },
        "pos_venda_relacionamento": {
            "mode": AutomationStageMode.ai_only.value,
            "model": "gemini-3.1-flash-lite",
            "cost_profile": CostProfile.economic.value,
            "context_mode": ContextMode.balanced.value,
            "human_review_required": False,
        },
    },
    AutomationPreset.supervised: {
        "entrada": {
            "mode": AutomationStageMode.human_in_the_loop.value,
            "model": settings.VERTEX_MODEL,
            "cost_profile": CostProfile.quality.value,
            "context_mode": ContextMode.full.value,
            "human_review_required": True,
        },
        "proposta_fechamento": {
            "mode": AutomationStageMode.human_only.value,
            "model": None,
            "context_mode": ContextMode.full.value,
            "human_review_required": True,
        },
        "secretaria_agendamento": {
            "mode": AutomationStageMode.human_in_the_loop.value,
            "model": settings.VERTEX_MODEL,
            "cost_profile": CostProfile.quality.value,
            "context_mode": ContextMode.full.value,
            "human_review_required": True,
        },
        "coleta_dados": {
            "mode": AutomationStageMode.ai_with_human_approval.value,
            "model": settings.VERTEX_MODEL,
            "cost_profile": CostProfile.quality.value,
            "context_mode": ContextMode.full.value,
            "human_review_required": True,
        },
        "assistente_juridico": {
            "mode": AutomationStageMode.ai_with_human_approval.value,
            "model": settings.VERTEX_MODEL,
            "cost_profile": CostProfile.quality.value,
            "context_mode": ContextMode.full.value,
            "human_review_required": True,
        },
        "financeiro_cobranca": {
            "mode": AutomationStageMode.human_in_the_loop.value,
            "model": settings.VERTEX_MODEL,
            "cost_profile": CostProfile.quality.value,
            "context_mode": ContextMode.full.value,
            "human_review_required": True,
        },
        "pos_venda_relacionamento": {
            "mode": AutomationStageMode.human_in_the_loop.value,
            "model": settings.VERTEX_MODEL,
            "cost_profile": CostProfile.quality.value,
            "context_mode": ContextMode.full.value,
            "human_review_required": True,
        },
    },
}


DEFAULT_MODEL_CATALOG: list[dict[str, Any]] = [
    {
        "key": "google/gemma3@gemma-3-4b-it",
        "label": "Gemma 3 4B",
        "provider": "vertex",
        "tier": ModelTier.simple.value,
        "description": "Baixo custo para triagem, classificação e extração simples via endpoint Gemma no Vertex.",
        "experimental": True,
    },
    {
        "key": "google/gemma3@gemma-3-27b-it",
        "label": "Gemma 3 27B",
        "provider": "vertex",
        "tier": ModelTier.medium.value,
        "description": "Bom para RAG leve, qualificação e resumo com custo reduzido via endpoint Gemma no Vertex.",
        "experimental": True,
    },
    {
        "key": "gemini-3.1-flash-lite",
        "label": "Gemini 2.5 Flash-Lite",
        "provider": "vertex",
        "tier": ModelTier.complex.value,
        "description": "Padrão para análise operacional, insights e geração mais segura.",
        "experimental": False,
    },
    {
        "key": "gemini-2.5-pro",
        "label": "Gemini 2.5 Pro",
        "provider": "vertex",
        "tier": ModelTier.premium.value,
        "description": "Usado em revisão crítica, estratégia e tarefas premium.",
        "experimental": False,
    },
    {
        "key": "qwen3:4b",
        "label": "Qwen3 4B (Ollama)",
        "provider": "ollama",
        "tier": ModelTier.simple.value,
        "description": "Modelo local leve já validado para triagem, classificação e respostas rápidas.",
        "experimental": True,
    },
    {
        "key": "qwen3:14b",
        "label": "Qwen3 14B (Ollama)",
        "provider": "ollama",
        "tier": ModelTier.medium.value,
        "description": "Modelo local para qualificação, resumo e automações com raciocínio intermediário.",
        "experimental": True,
    },
    {
        "key": "gemma4:12b",
        "label": "Gemma 4 12B (Ollama)",
        "provider": "ollama",
        "tier": ModelTier.complex.value,
        "description": "Modelo local validado para respostas mais robustas, com suporte a tools, thinking e visão no Ollama.",
        "experimental": True,
    },
    {
        "key": "gemma4:e4b",
        "label": "Gemma 4 E4B (Ollama)",
        "provider": "ollama",
        "tier": ModelTier.simple.value,
        "description": "Modelo local leve para triagem quando essa tag estiver disponível no host.",
        "experimental": True,
    },
    {
        "key": "qwen3:30b",
        "label": "Qwen3 30B A3B (Ollama)",
        "provider": "ollama",
        "tier": ModelTier.complex.value,
        "description": "Modelo local MoE para tarefas agentic/coding e raciocínio mais forte em infra com GPU/memória suficiente.",
        "experimental": True,
    },
    {
        "key": "gemma4:31b",
        "label": "Gemma 4 31B (Ollama)",
        "provider": "ollama",
        "tier": ModelTier.premium.value,
        "description": "Modelo local premium para raciocínio e multimodalidade quando houver capacidade dedicada.",
        "experimental": True,
    },
]


DEFAULT_AGENT_PROMPTS: list[dict[str, str]] = [
    {
        "agent_key": "klm_sdr",
        "label": "SDR Jurídico",
        "prompt": "Qualifique o lead com objetividade, confirme a dor principal, extraia sinais de urgência e prepare o próximo passo comercial sem prometer tese jurídica definitiva.",
    },
    {
        "agent_key": "klm_analyst",
        "label": "Analista IA",
        "prompt": "Organize fatos, riscos e próximos passos com linguagem clara, usando o contexto do card e da conversa para apoiar a tomada de decisão do time.",
    },
    {
        "agent_key": "klm_drafter",
        "label": "Redator IA",
        "prompt": "Produza conteúdo estruturado, objetivo e auditável, sempre pronto para revisão humana quando a etapa exigir aprovação.",
    },
    {
        "agent_key": "administrativo_ops",
        "label": "Onboarding Ops",
        "prompt": "Conduza checklist, coleta de dados e documentos com mensagens claras, cobrança educada e foco em reduzir pendências.",
    },
    {
        "agent_key": "financeiro_ops",
        "label": "Financeiro Ops",
        "prompt": "Acompanhe cobrança, lembretes e confirmação financeira com clareza, educação e rastreabilidade.",
    },
]


DEFAULT_PRACTICE_AREA_LIBRARY: list[dict[str, Any]] = [
    {
        "key": "bpc",
        "label": "BPC",
        "description": "Onboarding e qualificação para casos de BPC/LOAS com foco documental e elegibilidade inicial.",
        "flow_practice_area": "previdenciario",
        "trigger_summary": "Novo lead com indício de BPC ou atendimento vindo da campanha previdenciária.",
        "base_prompt": "Você atende potenciais clientes de BPC/LOAS. Valide renda, composição familiar, impedimento e documentos prioritários antes de avançar o onboarding.",
        "default_follow_up_profile": "balanced",
        "human_intervention_mode": AutomationStageMode.human_in_the_loop.value,
        "default_agent_key": "klm_sdr",
        "model_tier": ModelTier.medium.value,
        "checklist": [
            "Documento pessoal com CPF",
            "Comprovante de residência",
            "Laudos e exames recentes",
            "Comprovante de renda familiar",
        ],
        "allowed_flow_template_ids": [],
        "stage_prompts": [
            {
                "stage_key": "entrada",
                "title": "Triagem inicial",
                "prompt": "Confirme perfil do cliente, composição familiar, renda e situação de impedimento com linguagem simples.",
            },
            {
                "stage_key": "coleta_dados",
                "title": "Coleta documental",
                "prompt": "Peça os documentos em sequência lógica, explicando o motivo de cada item para reduzir abandono.",
            },
        ],
    },
    {
        "key": "fraude_bancaria",
        "label": "Fraude bancária",
        "description": "Fluxos de qualificação e coleta para golpes, descontos indevidos e RMC/consignado.",
        "flow_practice_area": "bancario",
        "trigger_summary": "Lead vindo de campanha bancária, fraude, golpe ou desconto indevido.",
        "base_prompt": "Você atende clientes com suspeita de fraude bancária. Identifique rapidamente tipo de fraude, banco envolvido, valores e provas mínimas.",
        "default_follow_up_profile": "assertive",
        "human_intervention_mode": AutomationStageMode.ai_with_human_approval.value,
        "default_agent_key": "klm_sdr",
        "model_tier": ModelTier.complex.value,
        "checklist": [
            "Extrato ou comprovante do débito",
            "Prints da conversa/app",
            "Comprovante de identidade",
            "Contato do banco ou protocolo",
        ],
        "allowed_flow_template_ids": [],
        "stage_prompts": [
            {
                "stage_key": "entrada",
                "title": "Classificação da fraude",
                "prompt": "Classifique se é golpe, contratação indevida, RMC ou outro desconto não reconhecido antes de avançar.",
            },
            {
                "stage_key": "proposta_fechamento",
                "title": "Fechamento assistido",
                "prompt": "Prepare o closer humano com resumo, urgência e valor potencial para acelerar a conversão.",
            },
        ],
    },
    {
        "key": "tributario",
        "label": "Tributário",
        "description": "Onboarding inicial para demandas tributárias selecionadas, com follow-up e coleta direcionada.",
        "flow_practice_area": "tributario",
        "trigger_summary": "Lead com dor tributária, restituição, isenção ou passivo fiscal inicial.",
        "base_prompt": "Você conduz a triagem comercial de casos tributários, capturando tipo de tributo, documentação base e expectativa do cliente.",
        "default_follow_up_profile": "gentle",
        "human_intervention_mode": AutomationStageMode.human_in_the_loop.value,
        "default_agent_key": "assistente_juridico",
        "model_tier": ModelTier.complex.value,
        "checklist": [
            "Documento pessoal",
            "Comprovantes fiscais ou DARFs",
            "Laudos ou documentos do benefício tributário",
            "Procurações ou contratos anteriores",
        ],
        "allowed_flow_template_ids": [],
        "stage_prompts": [
            {
                "stage_key": "assistente_juridico",
                "title": "Apoio tributário",
                "prompt": "Explique os próximos passos com clareza e destaque o que ainda depende de revisão humana especializada.",
            },
        ],
    },
    {
        "key": "consumidor",
        "label": "Consumidor",
        "description": "Fluxos universais para onboarding consumerista, cobrança de documentos e follow-up comercial.",
        "flow_practice_area": "universal",
        "trigger_summary": "Lead consumerista vindo de campanhas gerais ou indicação.",
        "base_prompt": "Você atende casos consumeristas no onboarding. Organize a dor, provas iniciais e próxima ação comercial com simplicidade.",
        "default_follow_up_profile": "balanced",
        "human_intervention_mode": AutomationStageMode.human_in_the_loop.value,
        "default_agent_key": "klm_sdr",
        "model_tier": ModelTier.medium.value,
        "checklist": [
            "Documento pessoal",
            "Contrato ou comprovante da relação de consumo",
            "Prints, e-mails ou protocolos",
            "Comprovantes de pagamento ou dano",
        ],
        "allowed_flow_template_ids": [],
        "stage_prompts": [
            {
                "stage_key": "entrada",
                "title": "Resumo da dor",
                "prompt": "Resuma o problema de consumo em linguagem simples e confirme quais provas mínimas ainda faltam.",
            },
        ],
    },
]


def _canonical_practice_area_library() -> list[dict[str, Any]]:
    """Project the canonical legal catalog into the tenant onboarding contract."""
    rows: list[dict[str, Any]] = []
    for area in LEGAL_AREA_CATALOG:
        routing = area["routing"]
        product_labels = ", ".join(product["label"] for product in area["products"])
        rows.append(
            {
                "key": area["areaKey"],
                "label": area["name"],
                "description": area["description"],
                "flow_practice_area": area["areaKey"],
                "trigger_summary": (
                    f"O orquestrador classifica a demanda entre: {product_labels}."
                ),
                "base_prompt": (
                    f"Atue somente em {area['name']}, no produto identificado, "
                    "respeitando o playbook publicado, o contexto do mesmo cliente "
                    "e todas as aprovações humanas obrigatórias."
                ),
                "default_follow_up_profile": "balanced",
                "human_intervention_mode": AutomationStageMode.human_in_the_loop.value,
                "default_agent_key": routing["sdrAgentKey"],
                "model_tier": ModelTier.complex.value,
                "checklist": list(area["documents"]),
                "allowed_flow_template_ids": [],
                "stage_prompts": [
                    {
                        "stage_key": "entrada",
                        "title": "Triagem e roteamento",
                        "prompt": (
                            f"Classifique o produto de {area['name']} e delegue ao "
                            f"agente {routing['sdrAgentKey']} sem cruzar área ou cliente."
                        ),
                    },
                    {
                        "stage_key": "assistente_juridico",
                        "title": "Análise especializada",
                        "prompt": (
                            f"Use {routing['specialistAgentKey']} somente após validar "
                            "escopo, documentos, produto e precedência de mudança manual."
                        ),
                    },
                    {
                        "stage_key": "proposta_fechamento",
                        "title": "Fechamento governado",
                        "prompt": (
                            f"Use {routing['closerAgentKey']} com honorários e objeções "
                            "do playbook publicado; qualquer exceção exige aprovação humana."
                        ),
                    },
                ],
            }
        )
    return rows


# One source of truth for every surface: onboarding, Areas, routing and agents.
DEFAULT_PRACTICE_AREA_LIBRARY = _canonical_practice_area_library()


DEFAULT_AI_GOVERNANCE: dict[str, Any] = {
    "gemmaEnabled": True,
    "simpleModel": "google/gemma3@gemma-3-4b-it",
    "mediumModel": "google/gemma3@gemma-3-27b-it",
    "complexModel": settings.VERTEX_MODEL,
    "premiumModel": getattr(settings, "VERTEX_MODEL_PRO", "gemini-2.5-pro"),
    "agentPrompts": DEFAULT_AGENT_PROMPTS,
}


DEFAULT_NOTIFICATION_SETTINGS: dict[str, Any] = {
    "whatsappEnabled": True,
    "emailEnabled": True,
    "dailyDigestHour": 7,
    "recipients": [],
    "rules": [
        {
            "key": "contractSent",
            "enabled": True,
            "emailEnabled": True,
            "whatsappEnabled": True,
        },
        {
            "key": "contractSigned",
            "enabled": True,
            "emailEnabled": True,
            "whatsappEnabled": True,
        },
        {
            "key": "meetingScheduled",
            "enabled": True,
            "emailEnabled": True,
            "whatsappEnabled": True,
        },
        {
            "key": "meetingReminder",
            "enabled": True,
            "emailEnabled": True,
            "whatsappEnabled": True,
            "reminderMinutesBefore": 120,
        },
        {
            "key": "dailyDigest",
            "enabled": True,
            "emailEnabled": True,
            "whatsappEnabled": False,
        },
        {
            "key": "followUpDue",
            "enabled": True,
            "emailEnabled": True,
            "whatsappEnabled": True,
        },
    ],
}

NOTIFICATION_RULE_CATALOG: list[dict[str, Any]] = [
    {
        "key": "contractSent",
        "label": "Contrato enviado",
        "description": "Avisa quando o pedido de assinatura é disparado ao cliente.",
    },
    {
        "key": "contractSigned",
        "label": "Contrato assinado",
        "description": "Confirma que o contrato foi fechado e que o caso pode avançar.",
    },
    {
        "key": "meetingScheduled",
        "label": "Reunião agendada",
        "description": "Dispara quando uma reunião é registrada na agenda interna do workspace.",
    },
    {
        "key": "meetingReminder",
        "label": "Lembrete de reunião",
        "description": "Envia lembrete automático antes da reunião conforme a antecedência configurada.",
    },
    {
        "key": "dailyDigest",
        "label": "Resumo diário",
        "description": "Relatório das 7h com contratos, reuniões, KPIs e pendências do workspace.",
    },
    {
        "key": "followUpDue",
        "label": "Follow-up vencido",
        "description": "Avisa quando um follow-up da fila (WhatsApp/e-mail) vence sem resposta.",
    },
]


DEFAULT_BRANDING_SETTINGS: dict[str, Any] = {
    "brandName": "Meu Executivo",
    "shortName": "KLM",
    "tagline": "Operação jurídica inteligente",
    "legalName": "Meu Executivo",
    "domain": "klmadvogados.com",
    "defaultLocale": "pt-BR",
    "logoUrl": "/logo.svg",
    "logoDarkUrl": "/logo.svg",
    "iconUrl": "/logo-icon.png",
    "faviconUrl": "/favicon.svg",
    "appleTouchIconUrl": "/logo-icon.png",
    "letterheadUrl": None,
    "signatureUrl": None,
    "primaryColor": "#DCA74C",
    "accentColor": "#C69237",
    "foregroundColor": "#131720",
    "backgroundColor": "#FAFAF9",
    "sidebarColor": "#050505",
    "successColor": "#237A3B",
    "warningColor": "#D98E04",
    "dangerColor": "#B94A48",
    "sansFamily": "'Geist Variable', 'Geist', sans-serif",
    "legalFamily": "'Geist Variable', 'Geist', sans-serif",
    "defaultTitle": "Meu Executivo | Operação jurídica inteligente",
    "titleTemplate": "%s | Meu Executivo",
    "description": "Atendimento, CRM, operação jurídica e inteligência comercial.",
    "applicationName": "Meu Executivo",
    "author": "Meu Executivo",
    "themeColor": "#050505",
    "email": "contato@klmadvogados.com",
    "supportEmail": "contato@klmadvogados.com",
    "phone": None,
    "websiteUrl": "https://klmadvogados.com",
    "registeredName": "Meu Executivo",
    "registrationNumber": None,
    "barRegistration": None,
    "address": None,
    "socialProof": [],
    "layoutDensity": "comfortable",
}


DEFAULT_SECURITY_AUDIT_SETTINGS: dict[str, Any] = {
    "sessionTimeoutMinutes": 30,
    "requireHumanReviewForHighRisk": True,
    "requireStrongerReviewForNewPrompts": True,
    "auditRetentionDays": 90,
    "openApiEnabled": True,
}


def default_preset_for_onboarding(onboarding_mode: str) -> AutomationPreset:
    return AutomationPreset.supervised if onboarding_mode == "assisted" else AutomationPreset.balanced


def build_stage_policies_for_preset(preset: AutomationPreset) -> dict[str, dict[str, Any]]:
    stage_policies = deepcopy(DEFAULT_STAGE_POLICIES)
    for stage_key, override in PRESET_STAGE_OVERRIDES.get(preset, {}).items():
        stage_policies.setdefault(stage_key, {})
        stage_policies[stage_key].update(deepcopy(override))
    return stage_policies


def build_default_workspace_settings(
    onboarding_mode: str = "self_service",
    automation_preset: AutomationPreset | None = None,
) -> dict[str, Any]:
    preset = automation_preset or default_preset_for_onboarding(onboarding_mode)
    practice_area_library = deepcopy(DEFAULT_PRACTICE_AREA_LIBRARY)
    return {
        "onboardingMode": onboarding_mode,
        "automationPreset": preset.value,
        "channels": {"whatsapp": False, "email": False},
        "features": {"adminPanel": True, "workspaceSelection": True},
        "messagingCadence": {
            "enabled": True,
            "showTypingIndicator": True,
            "timezone": "America/Sao_Paulo",
            "preDelayMinMs": 800,
            "preDelayMaxMs": 2400,
            "charactersPerSecond": 5.0,
            "typingMinMs": 1200,
            "typingMaxMs": 18000,
            "jitterPercent": 18,
            "interMessageMinMs": 2500,
            "maxMessagesPerMinute": 6,
            "maxMessagesPerHour": 40,
            "cooldownSeconds": 120,
            "quietHoursEnabled": True,
            "quietHoursStart": 20,
            "quietHoursEnd": 8,
            "maxConsecutiveWithoutReply": 3,
            "stopOnBlockOrOptOut": True,
            "officialApiOnly": True,
            "requireValidConsent": True,
        },
        "calendar": {
            "timezone": "America/Sao_Paulo",
            "provider": "manual",
            "connectionStatus": "offline",
            "accountEmail": None,
            "calendarId": None,
            "defaultMeetingUrl": None,
            "defaultDurationMinutes": 45,
            "slotIntervalMinutes": 30,
            "minimumNoticeHours": 4,
            "bookingHorizonDays": 30,
            "bufferBeforeMinutes": 0,
            "bufferAfterMinutes": 15,
            "weeklyAvailability": [
                {
                    "weekday": weekday,
                    "enabled": weekday < 5,
                    "windows": [
                        {"start": "09:00", "end": "12:00"},
                        {"start": "14:00", "end": "18:00"},
                    ] if weekday < 5 else [],
                }
                for weekday in range(7)
            ],
            "bookingPage": {
                "enabled": True,
                "title": "Converse com a equipe KLM",
                "description": "Escolha um horário para sua primeira reunião on-line.",
                "confirmationMessage": "Agendamento confirmado. Nosso WhatsApp permanece disponível para você.",
                "showOfficePhone": True,
                "officePhone": None,
            },
        },
        "finance": {
            "remindersEnabled": True,
            "defaultReminderDaysBefore": 3,
            "defaultReminderChannel": "whatsapp",
            "allowClientNotification": True,
            "autoCreateReceivableOnClose": True,
            "openApiEnabled": True,
            "externalSyncMode": "manual",
        },
        "automation": {
            "stagePolicies": build_stage_policies_for_preset(preset),
        },
        "whatsappAutomation": {
            "enabled": False,
            "onboardingCompleted": False,
            "timezone": "America/Sao_Paulo",
            "businessHoursStart": 8,
            "businessHoursEnd": 19,
            "rules": [
                {
                    "id": "welcome-default",
                    "label": "Boas-vindas imediatas",
                    "enabled": False,
                    "triggerType": "welcome",
                    "replyText": "Recebi sua mensagem por aqui. Vou organizar seu atendimento e já sigo com você.",
                    "onlyFirstMessage": True,
                    "escalateToHuman": False,
                    "silenceWindowHours": 0,
                },
                {
                    "id": "off-hours-default",
                    "label": "Resposta fora do horário",
                    "enabled": False,
                    "triggerType": "off_hours",
                    "replyText": "Estamos fora do horário comercial agora, mas seu atendimento já foi registrado e a equipe retoma no próximo expediente.",
                    "targetQueueStatus": "follow_up",
                    "onlyFirstMessage": False,
                    "escalateToHuman": False,
                    "silenceWindowHours": 12,
                },
            ],
        },
        "practiceAreaLibrary": {
            "areas": practice_area_library,
        },
        "workspaceOperations": {
            "activeAreaKeys": [],
            "areaSelections": {
                area["key"]: {
                    "enabled": False,
                    "selectedFlowTemplateId": None,
                    "followUpProfile": area["default_follow_up_profile"],
                    "humanInterventionMode": area["human_intervention_mode"],
                }
                for area in practice_area_library
            },
        },
        "aiGovernance": deepcopy(DEFAULT_AI_GOVERNANCE),
        "notifications": deepcopy(DEFAULT_NOTIFICATION_SETTINGS),
        "branding": deepcopy(DEFAULT_BRANDING_SETTINGS),
        "securityAudit": deepcopy(DEFAULT_SECURITY_AUDIT_SETTINGS),
    }


def _notification_rule_catalog_map() -> dict[str, dict[str, Any]]:
    return {
        item["key"]: item
        for item in NOTIFICATION_RULE_CATALOG
    }


def _normalize_notification_settings_shape(raw: dict[str, Any] | None) -> dict[str, Any]:
    base = deepcopy(DEFAULT_NOTIFICATION_SETTINGS)
    source = deepcopy(raw or {})

    if source.get("digestFrequency") and "dailyDigestHour" not in source:
        source["dailyDigestHour"] = 7

    base["whatsappEnabled"] = bool(source.get("whatsappEnabled", base["whatsappEnabled"]))
    base["emailEnabled"] = bool(source.get("emailEnabled", base["emailEnabled"]))
    base["dailyDigestHour"] = int(source.get("dailyDigestHour", base["dailyDigestHour"]) or 7)

    allowed_roles = {"workspace_owner", "workspace_admin", "responsible_human", "custom"}
    base["recipients"] = [
        {
            "id": item.get("id") or str(uuid4()),
            "name": item.get("name") or "Contato operacional",
            "email": item.get("email"),
            "phone": item.get("phone"),
            "role": item.get("role") if item.get("role") in allowed_roles else "custom",
            "userId": item.get("userId") or item.get("user_id"),
        }
        for item in source.get("recipients", [])
        if item.get("name") or item.get("email") or item.get("phone")
    ]

    legacy_flags = {
        "notifyOnNewLead": "dailyDigest",
        "notifyOnPendingApproval": "contractSent",
        "notifyOnDocumentDelay": "meetingReminder",
        "notifyOnFinancialPending": "contractSigned",
    }
    rules_by_key = {
        item["key"]: {
            "key": item["key"],
            "enabled": item.get("enabled", True),
            "emailEnabled": item.get("emailEnabled", True),
            "whatsappEnabled": item.get("whatsappEnabled", True),
            "reminderMinutesBefore": item.get("reminderMinutesBefore"),
        }
        for item in base["rules"]
    }
    for item in source.get("rules", []):
        key = item.get("key")
        if key in rules_by_key:
            rules_by_key[key].update(
                {
                    "enabled": bool(item.get("enabled", rules_by_key[key]["enabled"])),
                    "emailEnabled": bool(item.get("emailEnabled", rules_by_key[key]["emailEnabled"])),
                    "whatsappEnabled": bool(item.get("whatsappEnabled", rules_by_key[key]["whatsappEnabled"])),
                    "reminderMinutesBefore": item.get(
                        "reminderMinutesBefore",
                        rules_by_key[key]["reminderMinutesBefore"],
                    ),
                }
            )
    for legacy_key, mapped_key in legacy_flags.items():
        if legacy_key in source:
            rules_by_key[mapped_key]["enabled"] = bool(source.get(legacy_key))
    base["rules"] = list(rules_by_key.values())
    return base


class WorkspacePolicyService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _get_workspace_owner_recipient(self, workspace_id: UUID) -> dict[str, Any] | None:
        result = await self.db.execute(
            select(WorkspaceMembership)
            .options(selectinload(WorkspaceMembership.user))
            .where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.role == WorkspaceRole.workspace_owner,
                WorkspaceMembership.status == MembershipStatus.active,
            )
            .limit(1)
        )
        membership = result.scalar_one_or_none()
        if not membership or not membership.user:
            return None
        return {
            "id": f"owner-{membership.user_id}",
            "name": membership.user.name,
            "email": membership.user.email,
            "phone": membership.user.phone,
            "role": "workspace_owner",
            "userId": membership.user_id,
            "locked": True,
        }

    async def _resolve_notification_settings(
        self,
        workspace: Workspace,
        raw_settings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized = _normalize_notification_settings_shape(raw_settings)
        owner_recipient = await self._get_workspace_owner_recipient(workspace.id)
        recipients = [item for item in normalized.get("recipients", []) if item.get("role") != "workspace_owner"]
        if owner_recipient:
            normalized["recipients"] = [owner_recipient, *recipients]
        else:
            normalized["recipients"] = recipients

        catalog = _notification_rule_catalog_map()
        normalized["rules"] = [
            {
                **catalog[item["key"]],
                **item,
            }
            for item in normalized.get("rules", [])
            if item.get("key") in catalog
        ]
        return normalized

    async def get_workspace_automation_settings(self, workspace_id: UUID) -> WorkspaceAutomationSettingsRead:
        workspace = await self.db.get(Workspace, workspace_id)
        if not workspace:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace não encontrado.")
        merged = self._merge_with_defaults(workspace.settings or {})
        onboarding_mode = merged.get("onboardingMode", "self_service")
        return WorkspaceAutomationSettingsRead(
            onboarding_mode=onboarding_mode,
            preset=AutomationPreset(
                merged.get("automationPreset", default_preset_for_onboarding(onboarding_mode).value)
            ),
            available_presets=self._available_presets(),
            stage_policies=self._serialize_stage_policies(merged),
        )

    async def update_workspace_automation_settings(
        self,
        workspace_id: UUID,
        payload: WorkspaceAutomationSettingsUpdate,
    ) -> WorkspaceAutomationSettingsRead:
        workspace = await self.db.get(Workspace, workspace_id)
        if not workspace:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace não encontrado.")

        settings_data = self._merge_with_defaults(workspace.settings or {})
        if payload.onboarding_mode is not None:
            settings_data["onboardingMode"] = payload.onboarding_mode

        if payload.preset:
            settings_data["automationPreset"] = payload.preset.value
            if payload.preset != AutomationPreset.custom:
                settings_data.setdefault("automation", {})
                settings_data["automation"]["stagePolicies"] = build_stage_policies_for_preset(payload.preset)

        stage_policies = settings_data.setdefault("automation", {}).setdefault(
            "stagePolicies",
            deepcopy(DEFAULT_STAGE_POLICIES),
        )
        for stage in payload.stage_policies:
            stage_policies[stage.stage_key] = {
                "mode": stage.mode.value,
                "agent_key": stage.agent_key,
                "model": stage.model,
                "cost_profile": stage.cost_profile.value,
                "context_mode": stage.context_mode.value,
                "use_rag": stage.use_rag,
                "multimodal_enabled": stage.multimodal_enabled,
                "human_review_required": stage.human_review_required,
            }

        if payload.stage_policies and payload.preset is None:
            current_preset = settings_data.get("automationPreset")
            if current_preset not in {AutomationPreset.custom.value, None}:
                preset_enum = AutomationPreset(current_preset)
                if stage_policies != build_stage_policies_for_preset(preset_enum):
                    settings_data["automationPreset"] = AutomationPreset.custom.value

        workspace.settings = settings_data
        await self.db.flush()

        return WorkspaceAutomationSettingsRead(
            onboarding_mode=settings_data.get("onboardingMode", "self_service"),
            preset=AutomationPreset(settings_data.get("automationPreset", AutomationPreset.balanced.value)),
            available_presets=self._available_presets(),
            stage_policies=self._serialize_stage_policies(settings_data),
        )

    async def resolve_stage_policy(self, workspace_id: UUID, stage_key: str) -> dict[str, Any]:
        workspace = await self.db.get(Workspace, workspace_id)
        if not workspace:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace não encontrado.")
        merged = self._merge_with_defaults(workspace.settings or {})
        stage_policies = merged.get("automation", {}).get("stagePolicies", {})
        base = deepcopy(DEFAULT_STAGE_POLICIES.get(stage_key, {}))
        override = stage_policies.get(stage_key, {})
        base.update(override)
        return base

    async def get_admin_control_plane(self, workspace_id: UUID) -> AdminControlPlaneRead:
        workspace = await self.db.get(Workspace, workspace_id)
        if not workspace:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace não encontrado.")

        merged = self._merge_with_defaults(workspace.settings or {})
        flows = await self._list_flow_template_references()
        practice_areas = self._serialize_practice_areas(merged, flows)

        ai_governance = merged.get("aiGovernance", deepcopy(DEFAULT_AI_GOVERNANCE))
        notification_settings = await self._resolve_notification_settings(
            workspace,
            merged.get("notifications", deepcopy(DEFAULT_NOTIFICATION_SETTINGS)),
        )
        return AdminControlPlaneRead(
            practice_areas=practice_areas,
            ai_governance=AiGovernanceRead(
                gemma_enabled=bool(ai_governance.get("gemmaEnabled", True)),
                simple_model=ai_governance.get("simpleModel", "google/gemma3@gemma-3-4b-it"),
                medium_model=ai_governance.get("mediumModel", "google/gemma3@gemma-3-27b-it"),
                complex_model=ai_governance.get("complexModel", settings.VERTEX_MODEL),
                premium_model=ai_governance.get("premiumModel", getattr(settings, "VERTEX_MODEL_PRO", "gemini-2.5-pro")),
                available_models=[
                    ModelCatalogItemRead(
                        key=model["key"],
                        label=model["label"],
                        provider=model["provider"],
                        tier=ModelTier(model["tier"]),
                        description=model["description"],
                        experimental=bool(model.get("experimental", False)),
                    )
                    for model in DEFAULT_MODEL_CATALOG
                ],
                agent_prompts=[
                    AgentPromptPolicyRead(
                        agent_key=item["agent_key"],
                        label=item["label"],
                        prompt=item["prompt"],
                    )
                    for item in ai_governance.get("agentPrompts", deepcopy(DEFAULT_AGENT_PROMPTS))
                ],
            ),
            notifications=NotificationSettingsRead.model_validate(
                notification_settings
            ),
            branding=BrandingSettingsRead.model_validate(
                merged.get("branding", deepcopy(DEFAULT_BRANDING_SETTINGS))
            ),
            security_audit=SecurityAuditSettingsRead.model_validate(
                merged.get("securityAudit", deepcopy(DEFAULT_SECURITY_AUDIT_SETTINGS))
            ),
        )

    async def update_admin_control_plane(
        self,
        workspace_id: UUID,
        payload: AdminControlPlaneUpdate,
    ) -> AdminControlPlaneRead:
        workspace = await self.db.get(Workspace, workspace_id)
        if not workspace:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace não encontrado.")

        settings_data = self._merge_with_defaults(workspace.settings or {})
        if payload.practice_areas:
            settings_data["practiceAreaLibrary"] = {
                "areas": [
                    {
                        "key": area.key,
                        "label": area.label,
                        "description": area.description,
                        "flow_practice_area": area.flow_practice_area,
                        "trigger_summary": area.trigger_summary,
                        "base_prompt": area.base_prompt,
                        "default_follow_up_profile": area.default_follow_up_profile.value,
                        "human_intervention_mode": area.human_intervention_mode.value,
                        "default_agent_key": area.default_agent_key,
                        "model_tier": area.model_tier.value,
                        "checklist": area.checklist,
                        "allowed_flow_template_ids": area.allowed_flow_template_ids,
                        "stage_prompts": [
                            {
                                "stage_key": prompt.stage_key,
                                "title": prompt.title,
                                "prompt": prompt.prompt,
                            }
                            for prompt in area.stage_prompts
                        ],
                    }
                    for area in payload.practice_areas
                ]
            }

        if payload.ai_governance is not None:
            settings_data["aiGovernance"] = {
                "gemmaEnabled": payload.ai_governance.gemma_enabled,
                "simpleModel": payload.ai_governance.simple_model,
                "mediumModel": payload.ai_governance.medium_model,
                "complexModel": payload.ai_governance.complex_model,
                "premiumModel": payload.ai_governance.premium_model,
                "agentPrompts": [
                    {
                        "agent_key": prompt.agent_key,
                        "label": prompt.label,
                        "prompt": prompt.prompt,
                    }
                    for prompt in payload.ai_governance.agent_prompts
                ],
            }

        if payload.notifications is not None:
            from app.services.workspace_service import WorkspaceService

            await WorkspaceService(self.db)._assert_operational_recipient_limit(
                workspace,
                payload.notifications.recipients,
            )
            raw_notifications = payload.notifications.model_dump(by_alias=False)
            raw_notifications["recipients"] = [
                item
                for item in raw_notifications.get("recipients", [])
                if item.get("role") != "workspace_owner"
            ]
            settings_data["notifications"] = raw_notifications

        if payload.branding is not None:
            settings_data["branding"] = payload.branding.model_dump(by_alias=False)

        if payload.security_audit is not None:
            settings_data["securityAudit"] = payload.security_audit.model_dump(by_alias=False)

        workspace.settings = settings_data
        await self.db.flush()
        return await self.get_admin_control_plane(workspace_id)

    async def get_workspace_operations(self, workspace_id: UUID) -> WorkspaceOperationsRead:
        workspace = await self.db.get(Workspace, workspace_id)
        if not workspace:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace não encontrado.")

        merged = self._merge_with_defaults(workspace.settings or {})
        flows = await self._list_flow_template_references()
        practice_areas = self._serialize_practice_areas(merged, flows)
        operations = merged.get("workspaceOperations", {})
        active_keys = await self._active_legal_area_keys(workspace_id)
        selections = operations.get("areaSelections", {})

        return WorkspaceOperationsRead(
            area_activations=[
                WorkspaceAreaActivationRead(
                    key=area.key,
                    label=area.label,
                    description=area.description,
                    enabled=self._operation_parent_area_key(area) in active_keys,
                    selected_flow_template_id=selections.get(area.key, {}).get("selectedFlowTemplateId"),
                    follow_up_profile=selections.get(area.key, {}).get("followUpProfile", area.default_follow_up_profile),
                    human_intervention_mode=selections.get(area.key, {}).get("humanInterventionMode", area.human_intervention_mode),
                    checklist=area.checklist,
                    flow_options=area.available_flows,
                )
                for area in practice_areas
            ]
        )

    async def update_workspace_operations(
        self,
        workspace_id: UUID,
        payload: WorkspaceOperationsUpdate,
    ) -> WorkspaceOperationsRead:
        workspace = await self.db.get(Workspace, workspace_id)
        if not workspace:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace não encontrado.")

        settings_data = self._merge_with_defaults(workspace.settings or {})
        flows = await self._list_flow_template_references()
        practice_areas = self._serialize_practice_areas(settings_data, flows)
        allowed_by_area = {
            area.key: {flow.id for flow in area.available_flows}
            for area in practice_areas
        }
        selections: dict[str, Any] = {}
        active_keys = await self._active_legal_area_keys(workspace_id)
        for area in payload.area_activations:
            if area.key not in allowed_by_area:
                continue
            if area.selected_flow_template_id and area.selected_flow_template_id not in allowed_by_area[area.key]:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Flow selecionado é inválido para a área {area.key}.",
                )
            persisted_enabled = (
                self._operation_parent_area_key(
                    next(item for item in practice_areas if item.key == area.key)
                )
                in active_keys
            )
            if area.enabled != persisted_enabled:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=(
                        "O desbloqueio de áreas é exclusivo do Super Admin do SaaS "
                        "ou de funcionário interno autorizado."
                    ),
                )
            selections[area.key] = {
                "enabled": persisted_enabled,
                "selectedFlowTemplateId": area.selected_flow_template_id,
                "followUpProfile": area.follow_up_profile.value,
                "humanInterventionMode": area.human_intervention_mode.value,
            }
        settings_data["workspaceOperations"] = {
            "activeAreaKeys": sorted(active_keys),
            "areaSelections": selections,
        }
        workspace.settings = settings_data
        await self.db.flush()
        return await self.get_workspace_operations(workspace_id)

    def _merge_with_defaults(self, settings_data: dict[str, Any]) -> dict[str, Any]:
        onboarding_mode = settings_data.get("onboardingMode", "self_service")
        current_preset = settings_data.get("automationPreset")
        preset_values = {
            preset.value for preset in AutomationPreset if preset != AutomationPreset.custom
        }
        preset = AutomationPreset(current_preset) if current_preset in preset_values else None
        merged = build_default_workspace_settings(onboarding_mode, preset)
        if settings_data:
            merged.update({k: v for k, v in settings_data.items() if k != "automation"})
            merged.setdefault("automation", {})
            merged["automation"].setdefault("stagePolicies", {})
            for stage_key, default_stage in DEFAULT_STAGE_POLICIES.items():
                merged["automation"]["stagePolicies"].setdefault(stage_key, deepcopy(default_stage))
            for stage_key, override in settings_data.get("automation", {}).get("stagePolicies", {}).items():
                merged["automation"]["stagePolicies"].setdefault(
                    stage_key,
                    deepcopy(DEFAULT_STAGE_POLICIES.get(stage_key, {})),
                )
                merged["automation"]["stagePolicies"][stage_key].update(override)
            merged.setdefault("practiceAreaLibrary", {"areas": deepcopy(DEFAULT_PRACTICE_AREA_LIBRARY)})
            merged["practiceAreaLibrary"].setdefault("areas", deepcopy(DEFAULT_PRACTICE_AREA_LIBRARY))
            persisted_area_rows = merged["practiceAreaLibrary"].get("areas") or []
            persisted_by_key = {
                str(area.get("key")): area
                for area in persisted_area_rows
                if isinstance(area, dict) and area.get("key")
            }
            canonical_keys = {area["key"] for area in DEFAULT_PRACTICE_AREA_LIBRARY}
            legacy_product_keys = {"bpc", "fraude_bancaria"}
            reconciled_area_rows: list[dict[str, Any]] = []
            for canonical in DEFAULT_PRACTICE_AREA_LIBRARY:
                row = deepcopy(canonical)
                persisted = persisted_by_key.get(canonical["key"])
                if persisted:
                    row.update(persisted)
                    row["key"] = canonical["key"]
                reconciled_area_rows.append(row)
            reconciled_area_rows.extend(
                deepcopy(area)
                for area in persisted_area_rows
                if isinstance(area, dict)
                and area.get("key") not in canonical_keys
                and area.get("key") not in legacy_product_keys
            )
            merged["practiceAreaLibrary"]["areas"] = reconciled_area_rows
            merged.setdefault(
                "workspaceOperations",
                {
                    "activeAreaKeys": [],
                    "areaSelections": {},
                },
            )
            merged["workspaceOperations"].setdefault("activeAreaKeys", [])
            merged["workspaceOperations"].setdefault("areaSelections", {})
            area_selections = merged["workspaceOperations"]["areaSelections"]
            legacy_selection_parents = {
                "bpc": "previdenciario",
                "fraude_bancaria": "bancario",
            }
            for legacy_key, parent_key in legacy_selection_parents.items():
                if legacy_key in area_selections and parent_key not in area_selections:
                    area_selections[parent_key] = deepcopy(area_selections[legacy_key])
            for area in DEFAULT_PRACTICE_AREA_LIBRARY:
                area_selections.setdefault(
                    area["key"],
                    {
                        "enabled": False,
                        "selectedFlowTemplateId": None,
                        "followUpProfile": area["default_follow_up_profile"],
                        "humanInterventionMode": area["human_intervention_mode"],
                    },
                )
            merged.setdefault("aiGovernance", deepcopy(DEFAULT_AI_GOVERNANCE))
            merged["notifications"] = _normalize_notification_settings_shape(
                merged.get("notifications", deepcopy(DEFAULT_NOTIFICATION_SETTINGS))
            )
            merged.setdefault("branding", deepcopy(DEFAULT_BRANDING_SETTINGS))
            merged.setdefault("securityAudit", deepcopy(DEFAULT_SECURITY_AUDIT_SETTINGS))
        return merged

    def _serialize_stage_policies(self, settings_data: dict[str, Any]) -> list[StagePolicyRead]:
        stage_policies = settings_data.get("automation", {}).get("stagePolicies", {})
        rows: list[StagePolicyRead] = []
        for stage in STAGE_CATALOG:
            merged = deepcopy(DEFAULT_STAGE_POLICIES.get(stage["stage_key"], {}))
            merged.update(stage_policies.get(stage["stage_key"], {}))
            rows.append(
                StagePolicyRead(
                    stage_key=stage["stage_key"],
                    label=stage["label"],
                    description=stage["description"],
                    mode=AutomationStageMode(merged["mode"]),
                    agent_key=merged["agent_key"],
                    model=merged.get("model"),
                    cost_profile=CostProfile(merged["cost_profile"]),
                    context_mode=ContextMode(merged["context_mode"]),
                    use_rag=bool(merged.get("use_rag", True)),
                    multimodal_enabled=bool(merged.get("multimodal_enabled", True)),
                    human_review_required=bool(merged.get("human_review_required", False)),
                )
            )
        return rows

    def _available_presets(self) -> list[AutomationPresetRead]:
        return [
            AutomationPresetRead(
                key=preset,
                label=data["label"],
                description=data["description"],
            )
            for preset, data in PRESET_CATALOG.items()
        ]

    async def _list_flow_template_references(self) -> list[FlowTemplateReferenceRead]:
        result = await self.db.execute(
            select(Flow)
            .where(Flow.is_template.is_(True), Flow.deleted_at.is_(None))
            .order_by(Flow.practice_area.asc(), Flow.name.asc())
        )
        refs: list[FlowTemplateReferenceRead] = []
        for flow in result.scalars().all():
            nodes = flow.nodes or []
            prompt_count = 0
            for node in nodes:
                config = node.get("config") or node.get("data") or {}
                if isinstance(config, dict) and config.get("prompt"):
                    prompt_count += 1
            refs.append(
                FlowTemplateReferenceRead(
                    id=str(flow.id),
                    name=flow.name,
                    description=flow.description,
                    practice_area=flow.practice_area,
                    trigger=flow.trigger,
                    is_active=flow.is_active,
                    node_count=len(nodes),
                    prompt_count=prompt_count,
                )
            )
        return refs

    async def _active_legal_area_keys(self, workspace_id: UUID) -> set[str]:
        result = await self.db.execute(
            select(AreaBlueprint.area_key).where(
                AreaBlueprint.workspace_id == workspace_id,
                AreaBlueprint.is_active.is_(True),
            )
        )
        return {str(value) for value in result.scalars().all()}

    @staticmethod
    def _operation_parent_area_key(area: PracticeAreaTemplateRead) -> str:
        legacy_product_parents = {
            "bpc": "previdenciario",
            "fraude_bancaria": "bancario",
        }
        if area.key in legacy_product_parents:
            return legacy_product_parents[area.key]
        if area.flow_practice_area != "universal":
            return area.flow_practice_area
        return area.key

    def _serialize_practice_areas(
        self,
        settings_data: dict[str, Any],
        flows: list[FlowTemplateReferenceRead],
    ) -> list[PracticeAreaTemplateRead]:
        rows: list[PracticeAreaTemplateRead] = []
        catalog = settings_data.get("practiceAreaLibrary", {}).get("areas", deepcopy(DEFAULT_PRACTICE_AREA_LIBRARY))
        for area in catalog:
            allowed_ids = set(area.get("allowed_flow_template_ids") or [])
            flow_practice_area = area.get("flow_practice_area", "universal")
            available_flows = [
                flow
                for flow in flows
                if (allowed_ids and flow.id in allowed_ids) or (not allowed_ids and flow.practice_area in {flow_practice_area, "universal"})
            ]
            rows.append(
                PracticeAreaTemplateRead(
                    key=area["key"],
                    label=area["label"],
                    description=area["description"],
                    flow_practice_area=flow_practice_area,
                    trigger_summary=area.get("trigger_summary", ""),
                    base_prompt=area.get("base_prompt", ""),
                    default_follow_up_profile=area.get("default_follow_up_profile", "balanced"),
                    human_intervention_mode=area.get("human_intervention_mode", AutomationStageMode.human_in_the_loop.value),
                    default_agent_key=area.get("default_agent_key", "klm_sdr"),
                    model_tier=area.get("model_tier", ModelTier.medium.value),
                    checklist=area.get("checklist", []),
                    allowed_flow_template_ids=list(allowed_ids),
                    stage_prompts=[
                        PracticeAreaStagePromptRead(
                            stage_key=prompt.get("stage_key", "entrada"),
                            title=prompt.get("title", "Prompt"),
                            prompt=prompt.get("prompt", ""),
                        )
                        for prompt in area.get("stage_prompts", [])
                    ],
                    available_flows=available_flows,
                )
            )
        return rows
