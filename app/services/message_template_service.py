from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.template_renderer import render_template, validate_template
from app.models.workspace import Workspace
from app.schemas.whatsapp import (
    MessageTemplateRead,
    MessageTemplateSettingsRead,
    MessageTemplateSettingsUpdate,
)


SYSTEM_MESSAGE_TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "sys-welcome",
        "source": "system",
        "kind": "message",
        "category": "atendimento",
        "label": "Primeiro contato",
        "body": "Olá, {client_name}. Aqui é {responsible_name} da {workspace_name}. Vou seguir com o seu atendimento por aqui.",
        "format": "text",
        "favorite": True,
    },
    {
        "id": "sys-documents",
        "source": "system",
        "kind": "message",
        "category": "documentos",
        "label": "Cobrança de documentos",
        "body": "Perfeito, {client_name}. Para avançarmos, preciso destes documentos: {document_list}. Se preferir, posso organizar tudo por checklist.",
        "format": "text",
        "favorite": True,
    },
    {
        "id": "sys-followup",
        "source": "system",
        "kind": "message",
        "category": "follow_up",
        "label": "Follow-up cordial",
        "body": "Oi, {client_name}. Passando para confirmar se você conseguiu revisar os pontos combinados. Se quiser, eu retomo daqui com você.",
        "format": "text",
        "favorite": False,
    },
]

DEFAULT_PROPOSAL_TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "proposal-text-default",
        "source": "workspace",
        "kind": "proposal",
        "category": "comercial",
        "label": "Proposta comercial por texto",
        "body": "Olá, {client_name}. Segue a proposta inicial para o seu caso em {practice_area}:\\n\\nEscopo: {demand_type}\\nPróximo passo: {next_action}\\n\\nSe fizer sentido, eu também posso te enviar a versão em PDF.",
        "format": "text",
        "favorite": True,
        "title": "Proposta comercial",
    },
    {
        "id": "proposal-pdf-default",
        "source": "workspace",
        "kind": "proposal",
        "category": "comercial",
        "label": "Proposta comercial em PDF",
        "body": "Proposta comercial\\n\\nCliente: {client_name}\\nÁrea: {practice_area}\\nDemanda: {demand_type}\\nResumo: {executive_summary}\\nPróximo passo: {next_action}\\n\\nEmitido por {workspace_name}.",
        "format": "pdf",
        "favorite": False,
        "title": "Proposta Comercial",
        "footer": "Documento gerado pelo Meu Executivo para apresentação comercial.",
    },
]

DEFAULT_CONTRACT_TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "contract-default",
        "source": "workspace",
        "kind": "contract",
        "category": "assinatura",
        "label": "Contrato padrão",
        "body": "Contrato de prestação de serviços\\n\\nCliente: {client_name}\\nCPF: {client_cpf}\\nE-mail: {client_email}\\nTelefone: {client_phone}\\nDemanda: {demand_type}\\nÁrea: {practice_area}\\nResumo: {executive_summary}\\n\\nEmitido por {workspace_name}.",
        "format": "pdf",
        "favorite": True,
        "title": "Contrato de Prestação de Serviços",
        "footer": "Assinatura eletrônica via integrador configurado no workspace.",
    },
    {
        "id": "contract-whatsapp-default",
        "source": "workspace",
        "kind": "contract_message",
        "category": "assinatura",
        "label": "Mensagem de envio do contrato",
        "body": "Olá, {client_name}. Seu contrato está pronto para assinatura. Você pode concluir pelo link a seguir: {contract_link}",
        "format": "text",
        "favorite": True,
        "title": "Mensagem de assinatura",
    },
]


def _normalize_template(item: dict[str, Any], *, source: str) -> dict[str, Any]:
    body = item.get("body") or ""
    title = item.get("title")
    footer = item.get("footer")
    validate_template(body)
    if title:
        validate_template(title)
    if footer:
        validate_template(footer)
    return {
        "id": item.get("id") or str(uuid4()),
        "source": source,
        "kind": item.get("kind") or "message",
        "category": item.get("category") or "geral",
        "label": item.get("label") or "Template",
        "body": body,
        "format": item.get("format") or "text",
        "favorite": bool(item.get("favorite", False)),
        "title": title,
        "footer": footer,
    }


def _message_settings_from_workspace(workspace: Workspace) -> dict[str, Any]:
    settings = deepcopy((workspace.settings or {}).get("messageTemplates") or {})
    workspace_templates = [
        _normalize_template(item, source="workspace")
        for item in settings.get("workspaceTemplates", [])
    ]
    proposal_templates = [
        _normalize_template(item, source="workspace")
        for item in settings.get("proposalTemplates", DEFAULT_PROPOSAL_TEMPLATES)
    ]
    contract_templates = [
        _normalize_template(item, source="workspace")
        for item in settings.get("contractTemplates", DEFAULT_CONTRACT_TEMPLATES)
    ]
    return {
        "systemTemplates": [_normalize_template(item, source="system") for item in SYSTEM_MESSAGE_TEMPLATES],
        "workspaceTemplates": workspace_templates,
        "proposalTemplates": proposal_templates,
        "contractTemplates": contract_templates,
    }


class MessageTemplateService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_settings(self, workspace: Workspace) -> MessageTemplateSettingsRead:
        data = _message_settings_from_workspace(workspace)
        return MessageTemplateSettingsRead(
            system_templates=[MessageTemplateRead.model_validate(item) for item in data["systemTemplates"]],
            workspace_templates=[MessageTemplateRead.model_validate(item) for item in data["workspaceTemplates"]],
            proposal_templates=[MessageTemplateRead.model_validate(item) for item in data["proposalTemplates"]],
            contract_templates=[MessageTemplateRead.model_validate(item) for item in data["contractTemplates"]],
        )

    async def update_settings(
        self,
        workspace: Workspace,
        payload: MessageTemplateSettingsUpdate,
    ) -> MessageTemplateSettingsRead:
        settings = deepcopy(workspace.settings or {})
        settings["messageTemplates"] = {
            "workspaceTemplates": [
                _normalize_template(item.model_dump(by_alias=False), source="workspace")
                for item in payload.workspace_templates
            ],
            "proposalTemplates": [
                _normalize_template(item.model_dump(by_alias=False), source="workspace")
                for item in payload.proposal_templates
            ] or deepcopy(DEFAULT_PROPOSAL_TEMPLATES),
            "contractTemplates": [
                _normalize_template(item.model_dump(by_alias=False), source="workspace")
                for item in payload.contract_templates
            ] or deepcopy(DEFAULT_CONTRACT_TEMPLATES),
        }
        workspace.settings = settings
        await self.db.flush()
        return await self.get_settings(workspace)

    async def list_chat_templates(self, workspace: Workspace) -> list[MessageTemplateRead]:
        settings = await self.get_settings(workspace)
        return [
            *settings.system_templates,
            *settings.workspace_templates,
            *settings.proposal_templates,
            *settings.contract_templates,
        ]

    async def get_template_by_id(self, workspace: Workspace, template_id: str) -> MessageTemplateRead | None:
        settings = await self.get_settings(workspace)
        for item in [
            *settings.system_templates,
            *settings.workspace_templates,
            *settings.proposal_templates,
            *settings.contract_templates,
        ]:
            if item.id == template_id:
                return item
        return None

    @staticmethod
    def render_template(template: str, context: dict[str, Any]) -> str:
        return render_template(template, context)
