"""
Template Management (W2.2 do Plano Meta Máximo).

Escopo:
- criar/editar rascunhos de template Meta (name/language/category/components)
  com validação das regras de composição da Meta (placeholders sequenciais,
  limites de tamanho, botões);
- `submit()` honesto: com o gate de entregas externas fechado, marca o
  rascunho como "submission_prepared" (nunca finge aprovado); com o gate
  aberto, chama a Business Management API via `MetaCloudClient.create_message_template`;
- listagem unificada (rascunhos locais + templates sincronizados), pois ambos
  vivem na mesma tabela `whatsapp_meta_templates` — nenhuma migration foi
  necessária: o rascunho é apenas uma linha sem `external_template_id`, com
  `raw_payload["source"] == "local_draft"`.

O status REAL de aprovação (approved/pending/rejected/paused/disabled) na
Meta continua vindo exclusivamente do sync existente em
`WhatsAppCampaignService.sync_meta_templates` — este serviço nunca escreve
esses status como se fossem aprovação da Meta.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime
from typing import Any, Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.klm_runtime import external_delivery_allowed
from app.core.rbac import has_permission
from app.db.post_commit import queue_realtime_event
from app.models.klm_operations import KlmAuditEvent
from app.models.notification import NotificationType
from app.models.whatsapp import ConnectionType, WhatsAppConnection
from app.models.whatsapp_campaign import MetaTemplateStatus, WhatsAppMetaTemplate
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership, WorkspaceRole
from app.schemas.whatsapp_campaign import (
    TemplateButtonIn,
    TemplateComponentsIn,
    TemplateDraftCreate,
    TemplateDraftRead,
    TemplateDraftUpdate,
    TemplateSubmitResult,
    UnifiedTemplateRead,
)
from app.services.notification_service import NotificationService
from app.services.whatsapp.connection_service import WhatsAppConnectionService
from app.services.whatsapp.meta_cloud_client import MetaCloudClient

# W2.2 — aprovação interna antes da Meta. Reaproveita o módulo/ação RBAC
# "aprovacoes:approve" já existente (owner/admin/legal_manager) em vez de
# criar um sistema de permissão paralelo; o estado da intenção de aprovação
# vive inteiramente no raw_payload do rascunho (nenhuma migration/tabela
# nova — ApprovalQueueItem não serve aqui pois exige matter_id, e um
# template Meta não pertence a um caso).
INTERNAL_APPROVAL_MODULE = "aprovacoes"
INTERNAL_APPROVAL_ACTION = "approve"

_PLACEHOLDER_RE = re.compile(r"\{\{\s*(\d+)\s*\}\}")
_NAME_INVALID_CHARS_RE = re.compile(r"[^a-z0-9_]+")

MAX_HEADER_TEXT_LEN = 60
MAX_FOOTER_TEXT_LEN = 60
MAX_BODY_TEXT_LEN = 1024
MAX_BUTTON_TEXT_LEN = 25
MAX_NAME_LEN = 512
MAX_TOTAL_BUTTONS = 10
MAX_URL_BUTTONS = 2
MAX_PHONE_BUTTONS = 1
MAX_QUICK_REPLY_BUTTONS = 3

VALID_CATEGORIES = {"marketing", "utility", "authentication"}
VALID_HEADER_TYPES = {"NONE", "TEXT", "IMAGE", "VIDEO", "DOCUMENT"}
VALID_BUTTON_TYPES = {"QUICK_REPLY", "URL", "PHONE_NUMBER", "COPY_CODE"}


class TemplateValidationError(Exception):
    """Erros de validação das regras de composição de template da Meta."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


def normalize_template_name(raw_name: str) -> str:
    """Normaliza o nome do template para o formato exigido pela Meta.

    Meta exige: apenas minúsculas, dígitos e underscore, sem espaços/acentos.
    """
    decomposed = unicodedata.normalize("NFKD", raw_name or "")
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_only.strip().lower().replace(" ", "_").replace("-", "_")
    normalized = _NAME_INVALID_CHARS_RE.sub("_", lowered)
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return normalized


def _check_sequential_placeholders(text: str, *, field: str, errors: list[str]) -> None:
    """Valida que os placeholders {{n}} de um campo são sequenciais a partir de 1."""
    numbers = [int(match) for match in _PLACEHOLDER_RE.findall(text or "")]
    if not numbers:
        return
    expected = set(range(1, len(set(numbers)) + 1))
    if set(numbers) != expected or sorted(set(numbers)) != list(expected):
        errors.append(
            f"Os placeholders de {field} precisam ser sequenciais a partir de {{{{1}}}} "
            f"sem pular números. Encontrado: {sorted(set(numbers))}."
        )


def validate_template_draft(
    *,
    name: str,
    language: str,
    category: str,
    components: TemplateComponentsIn,
) -> list[str]:
    """Valida um rascunho de template contra as regras de composição da Meta.

    Retorna a lista de mensagens de erro em linguagem de gente (vazia se ok).
    Não levanta exceção: quem chama decide o que fazer (bloquear submit,
    exibir inline no builder, etc).
    """
    errors: list[str] = []

    normalized_name = normalize_template_name(name)
    if not normalized_name:
        errors.append("O nome do template não pode ficar vazio depois de normalizado.")
    elif len(normalized_name) > MAX_NAME_LEN:
        errors.append(f"O nome do template excede o limite de {MAX_NAME_LEN} caracteres.")

    if not language or not language.strip():
        errors.append("Informe o idioma do template (ex.: pt_BR).")

    if category not in VALID_CATEGORIES:
        errors.append("Categoria inválida. Use marketing, utility ou authentication.")

    header_type = (components.header_type or "NONE").upper()
    if header_type not in VALID_HEADER_TYPES:
        errors.append(f"Tipo de cabeçalho inválido: {header_type}.")

    if header_type == "TEXT":
        header_text = (components.header_text or "").strip()
        if not header_text:
            errors.append("O cabeçalho de texto não pode ficar vazio.")
        elif len(header_text) > MAX_HEADER_TEXT_LEN:
            errors.append(f"O cabeçalho excede o limite de {MAX_HEADER_TEXT_LEN} caracteres.")
        else:
            _check_sequential_placeholders(header_text, field="cabeçalho", errors=errors)
            numbers = [int(m) for m in _PLACEHOLDER_RE.findall(header_text)]
            if len(set(numbers)) > 1:
                errors.append("O cabeçalho aceita no máximo uma variável, {{1}}.")
            if numbers and not (components.header_example or "").strip():
                errors.append("Informe um exemplo realista para a variável {{1}} do cabeçalho.")
    elif header_type != "NONE" and components.header_text:
        errors.append("Cabeçalho de mídia (imagem/vídeo/documento) não usa texto de cabeçalho.")

    body_text = (components.body_text or "").strip()
    if not body_text:
        errors.append("O corpo da mensagem é obrigatório.")
    elif len(body_text) > MAX_BODY_TEXT_LEN:
        errors.append(f"O corpo excede o limite de {MAX_BODY_TEXT_LEN} caracteres.")
    else:
        _check_sequential_placeholders(body_text, field="corpo", errors=errors)
        body_numbers = [int(m) for m in _PLACEHOLDER_RE.findall(body_text)]
        expected_examples = max(body_numbers, default=0)
        supplied_examples = [item.strip() for item in components.body_examples]
        if expected_examples and len(supplied_examples) != expected_examples:
            errors.append(
                f"Informe exatamente {expected_examples} exemplo(s) para as variáveis do corpo."
            )
        elif any(not item for item in supplied_examples):
            errors.append("Os exemplos das variáveis do corpo não podem ficar vazios.")

    footer_text = (components.footer_text or "").strip()
    if footer_text:
        if len(footer_text) > MAX_FOOTER_TEXT_LEN:
            errors.append(f"O rodapé excede o limite de {MAX_FOOTER_TEXT_LEN} caracteres.")
        if _PLACEHOLDER_RE.search(footer_text):
            errors.append("O rodapé não pode conter variáveis ({{n}}).")

    buttons = components.buttons or []
    if len(buttons) > MAX_TOTAL_BUTTONS:
        errors.append(f"No máximo {MAX_TOTAL_BUTTONS} botões por template.")

    url_count = 0
    phone_count = 0
    quick_reply_count = 0
    dynamic_url_seen = False
    for index, button in enumerate(buttons, start=1):
        button_type = (button.type or "").upper()
        if button_type not in VALID_BUTTON_TYPES:
            errors.append(f"Botão {index}: tipo inválido ({button_type}).")
            continue
        text = (button.text or "").strip()
        if not text:
            errors.append(f"Botão {index}: o texto é obrigatório.")
        elif len(text) > MAX_BUTTON_TEXT_LEN:
            errors.append(f"Botão {index}: o texto excede {MAX_BUTTON_TEXT_LEN} caracteres.")

        if button_type == "QUICK_REPLY":
            quick_reply_count += 1
            if button.url or button.phone_number:
                errors.append(f"Botão {index}: resposta rápida não usa URL nem telefone.")
        elif button_type == "URL":
            url_count += 1
            url_value = (button.url or "").strip()
            if not url_value:
                errors.append(f"Botão {index}: URL é obrigatória.")
            else:
                has_placeholder = bool(_PLACEHOLDER_RE.search(url_value))
                if has_placeholder:
                    if not url_value.endswith("{{1}}"):
                        errors.append(
                            f"Botão {index}: a variável de URL dinâmica só pode ser {{{{1}}}} "
                            "no final do link."
                        )
                    if dynamic_url_seen:
                        errors.append(
                            "Só é permitido um botão de URL dinâmica (com variável) por template."
                        )
                    dynamic_url_seen = True
                # Botão de URL estática (sem variável) não pode ter placeholder
                # em nenhuma parte do link — regra explícita da diretriz do sprint.
        elif button_type == "PHONE_NUMBER":
            phone_count += 1
            if not (button.phone_number or "").strip():
                errors.append(f"Botão {index}: telefone é obrigatório.")
        elif button_type == "COPY_CODE":
            pass

    if quick_reply_count > MAX_QUICK_REPLY_BUTTONS:
        errors.append(f"No máximo {MAX_QUICK_REPLY_BUTTONS} botões de resposta rápida.")
    if url_count > MAX_URL_BUTTONS:
        errors.append(f"No máximo {MAX_URL_BUTTONS} botões de URL.")
    if phone_count > MAX_PHONE_BUTTONS:
        errors.append(f"No máximo {MAX_PHONE_BUTTONS} botão de telefone.")

    return errors


def _build_meta_components(components: TemplateComponentsIn) -> list[dict[str, Any]]:
    """Converte o formato do builder no formato de `components` da Graph API."""
    result: list[dict[str, Any]] = []
    header_type = (components.header_type or "NONE").upper()
    if header_type == "TEXT" and components.header_text:
        header: dict[str, Any] = {
            "type": "HEADER",
            "format": "TEXT",
            "text": components.header_text.strip(),
        }
        if _PLACEHOLDER_RE.search(components.header_text) and components.header_example:
            header["example"] = {"header_text": [components.header_example.strip()]}
        result.append(header)
    elif header_type in {"IMAGE", "VIDEO", "DOCUMENT"}:
        result.append({"type": "HEADER", "format": header_type})

    body: dict[str, Any] = {"type": "BODY", "text": (components.body_text or "").strip()}
    if _PLACEHOLDER_RE.search(components.body_text or "") and components.body_examples:
        body["example"] = {"body_text": [[item.strip() for item in components.body_examples]]}
    result.append(body)

    if components.footer_text and components.footer_text.strip():
        result.append({"type": "FOOTER", "text": components.footer_text.strip()})

    if components.buttons:
        button_items: list[dict[str, Any]] = []
        for button in components.buttons:
            item: dict[str, Any] = {"type": button.type.upper(), "text": button.text.strip()}
            if button.type.upper() == "URL" and button.url:
                item["url"] = button.url.strip()
                if button.example:
                    item["example"] = button.example
            if button.type.upper() == "PHONE_NUMBER" and button.phone_number:
                item["phone_number"] = button.phone_number.strip()
            button_items.append(item)
        result.append({"type": "BUTTONS", "buttons": button_items})

    return result


def _components_from_meta(components: list[dict[str, Any]]) -> TemplateComponentsIn:
    """Converte `components` no formato da Graph API de volta para o builder.

    Usado ao clonar um template sincronizado/rejeitado que nunca teve
    `builder_components` (veio direto do sync, não do builder local).
    """
    header_type = "NONE"
    header_text: str | None = None
    header_example: str | None = None
    body_text = ""
    body_examples: list[str] = []
    footer_text: str | None = None
    buttons: list[TemplateButtonIn] = []
    for component in components:
        kind = str(component.get("type") or "").upper()
        if kind == "HEADER":
            header_type = str(component.get("format") or "TEXT").upper()
            if header_type == "TEXT":
                header_text = component.get("text")
                raw_header_examples = (component.get("example") or {}).get("header_text", [])
                header_example = str(raw_header_examples[0]) if raw_header_examples else None
        elif kind == "BODY":
            body_text = component.get("text") or ""
            raw_body_examples = (component.get("example") or {}).get("body_text", [])
            if raw_body_examples and isinstance(raw_body_examples[0], list):
                body_examples = [str(value) for value in raw_body_examples[0]]
        elif kind == "FOOTER":
            footer_text = component.get("text")
        elif kind == "BUTTONS":
            for button in component.get("buttons", []):
                buttons.append(
                    TemplateButtonIn(
                        type=str(button.get("type") or "QUICK_REPLY").upper(),
                        text=button.get("text") or "",
                        url=button.get("url"),
                        phone_number=button.get("phone_number"),
                        example=button.get("example"),
                    )
                )
    return TemplateComponentsIn(
        header_type=header_type,
        header_text=header_text,
        header_example=header_example,
        body_text=body_text,
        body_examples=body_examples,
        footer_text=footer_text,
        buttons=buttons,
    )


_TEMPLATE_CATEGORIES = ("utility", "marketing", "authentication")


def _normalize_template_category(value: object) -> str:
    text = str(value or "").strip().lower()
    return text if text in _TEMPLATE_CATEGORIES else "utility"


def _is_local_draft(template: WhatsAppMetaTemplate) -> bool:
    return bool((template.raw_payload or {}).get("source") == "local_draft")


def _local_status(template: WhatsAppMetaTemplate) -> Optional[str]:
    return (template.raw_payload or {}).get("local_status") if _is_local_draft(template) else None


def _to_unified_read(template: WhatsAppMetaTemplate) -> UnifiedTemplateRead:
    is_draft = _is_local_draft(template)
    raw = template.raw_payload or {}
    return UnifiedTemplateRead(
        id=template.id,
        whatsapp_connection_id=template.whatsapp_connection_id,
        external_template_id=template.external_template_id,
        name=template.name,
        category=template.category,
        language=template.language,
        status=template.status,
        quality_rating=template.quality_rating,
        components=template.components or [],
        is_draft=is_draft,
        local_status=raw.get("local_status") if is_draft else None,
        validation_errors=raw.get("validation_errors", []) if is_draft else [],
        meta_submission_available=external_delivery_allowed(),
        rejected_reason=raw.get("rejected_reason"),
        previous_category=raw.get("previousCategory"),
        created_at=template.created_at,
        updated_at=template.updated_at,
        last_synced_at=None if is_draft else template.last_synced_at,
        removed_at_source_at=template.removed_at_source_at,
    )


class TemplateManagementService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        connection_service: WhatsAppConnectionService,
        meta_client: MetaCloudClient,
    ) -> None:
        self.db = db
        self.connection_service = connection_service
        self.meta_client = meta_client

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(UTC)

    async def _resolve_connection(
        self, workspace_id: UUID, connection_id: UUID | None
    ) -> WhatsAppConnection:
        connection = (
            await self.connection_service.get_connection(connection_id, workspace_id)
            if connection_id
            else await self.connection_service.get_default_connection(
                workspace_id, connected_only=True
            )
        )
        if connection is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "WA-TPL-001",
                    "message": "Nenhum número oficial conectado está disponível para templates.",
                },
            )
        if connection.connection_type == ConnectionType.QR_CODE:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "WA-TPL-002",
                    "message": "Templates oficiais exigem conexão oficial (não QR Code).",
                },
            )
        return connection

    async def _get_draft(self, workspace_id: UUID, template_id: UUID) -> WhatsAppMetaTemplate:
        result = await self.db.execute(
            select(WhatsAppMetaTemplate).where(
                WhatsAppMetaTemplate.id == template_id,
                WhatsAppMetaTemplate.workspace_id == workspace_id,
            )
        )
        template = result.scalars().first()
        if template is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "WA-TPL-003", "message": "Template não encontrado."},
            )
        if not _is_local_draft(template):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "WA-TPL-004",
                    "message": "Este template veio da Meta (sincronizado) e não é um rascunho local editável.",
                },
            )
        return template

    # --- listagem unificada -------------------------------------------------

    async def list_unified(
        self,
        workspace_id: UUID,
        *,
        connection_id: UUID | None = None,
        include_removed_source: bool = False,
    ) -> list[UnifiedTemplateRead]:
        stmt = select(WhatsAppMetaTemplate).where(
            WhatsAppMetaTemplate.workspace_id == workspace_id,
        )
        if not include_removed_source:
            stmt = stmt.where(WhatsAppMetaTemplate.removed_at_source_at.is_(None))
        if connection_id:
            stmt = stmt.where(WhatsAppMetaTemplate.whatsapp_connection_id == connection_id)
        stmt = stmt.order_by(
            WhatsAppMetaTemplate.updated_at.desc(), WhatsAppMetaTemplate.name.asc()
        )
        result = await self.db.execute(stmt)
        return [_to_unified_read(item) for item in result.scalars().all()]

    async def remove_local_record(
        self,
        workspace_id: UUID,
        template_id: UUID,
        *,
        confirm_history_loss: bool,
        actor_user_id: UUID | None = None,
        actor_label: str | None = None,
    ) -> None:
        if not confirm_history_loss:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "WA-TPL-LOCAL-DELETE-CONFIRMATION",
                    "message": "Confirme a perda do histórico local antes de remover o registro.",
                },
            )
        result = await self.db.execute(
            select(WhatsAppMetaTemplate).where(
                WhatsAppMetaTemplate.id == template_id,
                WhatsAppMetaTemplate.workspace_id == workspace_id,
            )
        )
        template = result.scalars().first()
        if template is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "WA-TPL-003", "message": "Template não encontrado."},
            )
        if not template.external_template_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "WA-TPL-LOCAL-DELETE-DRAFT",
                    "message": (
                        "Use a exclusão de rascunho para templates ainda não enviados à Meta."
                    ),
                },
            )
        self.db.add(
            KlmAuditEvent(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                actor_type="user" if actor_user_id else "system",
                actor_label=actor_label or "Operador",
                event_type="whatsapp_template_local_record_removed",
                entity_type="whatsapp_meta_template",
                entity_id=template.id,
                summary=(
                    f"Registro local do template '{template.name}' removido por decisão humana."
                ),
                before_data={
                    "templateId": str(template.id),
                    "externalTemplateId": template.external_template_id,
                    "name": template.name,
                    "language": template.language,
                    "status": template.status.value,
                    "removedAtSourceAt": (
                        template.removed_at_source_at.isoformat()
                        if template.removed_at_source_at
                        else None
                    ),
                },
                after_data=None,
            )
        )
        await self.db.delete(template)
        await self.db.commit()

    # --- CRUD de rascunhos ---------------------------------------------------

    async def create_draft(
        self, workspace_id: UUID, payload: TemplateDraftCreate
    ) -> TemplateDraftRead:
        connection = await self._resolve_connection(workspace_id, payload.whatsapp_connection_id)
        normalized_name = normalize_template_name(payload.name)
        errors = validate_template_draft(
            name=payload.name,
            language=payload.language,
            category=payload.category,
            components=payload.components,
        )
        if errors:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "WA-TPL-VALIDATION",
                    "message": "Rascunho inválido.",
                    "errors": errors,
                },
            )

        existing = await self.db.execute(
            select(WhatsAppMetaTemplate).where(
                WhatsAppMetaTemplate.workspace_id == workspace_id,
                WhatsAppMetaTemplate.whatsapp_connection_id == connection.id,
                WhatsAppMetaTemplate.name == normalized_name,
                WhatsAppMetaTemplate.language == payload.language,
            )
        )
        if existing.scalars().first() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "WA-TPL-005",
                    "message": "Já existe um template (rascunho ou sincronizado) com esse nome e idioma.",
                },
            )

        now = self._utc_now()
        template = WhatsAppMetaTemplate(
            workspace_id=workspace_id,
            whatsapp_connection_id=connection.id,
            external_template_id=None,
            name=normalized_name,
            category=payload.category,
            language=payload.language,
            status=MetaTemplateStatus.pending,
            quality_rating=None,
            components=_build_meta_components(payload.components),
            raw_payload={
                "source": "local_draft",
                "local_status": "draft",
                "validation_errors": [],
                "builder_components": payload.components.model_dump(),
            },
            last_synced_at=now,
        )
        self.db.add(template)
        await self.db.commit()
        await self.db.refresh(template)
        return TemplateDraftRead(
            **_to_unified_read(template).model_dump(exclude={"last_synced_at"})
        )

    async def update_draft(
        self, workspace_id: UUID, template_id: UUID, payload: TemplateDraftUpdate
    ) -> TemplateDraftRead:
        template = await self._get_draft(workspace_id, template_id)
        local_status = _local_status(template)
        if local_status != "draft":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "WA-TPL-006",
                    "message": "Só é possível editar rascunhos ainda não submetidos.",
                },
            )

        builder = TemplateComponentsIn(**(template.raw_payload or {}).get("builder_components", {}))
        previous_builder = builder.model_dump()
        previous_category = template.category
        previous_language = template.language
        if payload.components is not None:
            builder = payload.components
        new_category = payload.category or template.category
        new_language = payload.language or template.language

        errors = validate_template_draft(
            name=template.name,
            language=new_language,
            category=new_category,
            components=builder,
        )
        if errors:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "WA-TPL-VALIDATION",
                    "message": "Rascunho inválido.",
                    "errors": errors,
                },
            )

        material_changed = (
            builder.model_dump() != previous_builder
            or new_category != previous_category
            or new_language != previous_language
        )
        raw_payload = dict(template.raw_payload or {})
        invalidated_analysis = False
        if material_changed:
            for key in ("ai_analysis", "ai_analysis_at", "oab_risk_acknowledgement"):
                invalidated_analysis = (
                    raw_payload.pop(key, None) is not None or invalidated_analysis
                )

        template.category = new_category
        template.language = new_language
        template.components = _build_meta_components(builder)
        template.raw_payload = {
            **raw_payload,
            "builder_components": builder.model_dump(),
            "validation_errors": [],
        }
        self.db.add(template)
        if invalidated_analysis:
            self.db.add(
                KlmAuditEvent(
                    workspace_id=workspace_id,
                    actor_type="system",
                    actor_label="Sistema",
                    event_type="whatsapp_template_oab_analysis_invalidated",
                    entity_type="whatsapp_meta_template",
                    entity_id=template.id,
                    summary=(
                        f"Análise e aceite ético do template '{template.name}' foram "
                        "invalidados após edição material."
                    ),
                    before_data={
                        "category": previous_category,
                        "language": previous_language,
                    },
                    after_data={"category": new_category, "language": new_language},
                )
            )
        await self.db.commit()
        await self.db.refresh(template)
        return TemplateDraftRead(
            **_to_unified_read(template).model_dump(exclude={"last_synced_at"})
        )

    async def delete_draft(self, workspace_id: UUID, template_id: UUID) -> None:
        template = await self._get_draft(workspace_id, template_id)
        await self.db.delete(template)
        await self.db.commit()

    async def get_draft(self, workspace_id: UUID, template_id: UUID) -> TemplateDraftRead:
        template = await self._get_draft(workspace_id, template_id)
        return TemplateDraftRead(
            **_to_unified_read(template).model_dump(exclude={"last_synced_at"})
        )

    # --- submissão gated -----------------------------------------------------

    async def submit(
        self,
        workspace_id: UUID,
        template_id: UUID,
        *,
        requested_by_user_id: UUID | None = None,
        requester_role: WorkspaceRole | None = None,
        requester_label: str | None = None,
    ) -> TemplateSubmitResult:
        """Submete um rascunho, com aprovação interna prévia quando exigida.

        `requester_role=None` significa "chamador já autorizado" (usado por
        `approve_internal_submission` para concluir a submissão depois que um
        aprovador aprovou, e pelos testes de unidade existentes do serviço
        que não exercitam RBAC). Quando um papel é informado e não tem a
        permissão `aprovacoes:approve`, o rascunho vira uma intenção de
        aprovação interna em vez de seguir direto para a Meta.
        """
        template = await self._get_draft(workspace_id, template_id)
        local_status = _local_status(template)
        if local_status == "pending_internal_approval":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "WA-TPL-009",
                    "message": "Este rascunho já aguarda aprovação interna.",
                },
            )
        if local_status == "submission_prepared":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "WA-TPL-007",
                    "message": "Este rascunho já está preparado para submissão à Meta.",
                },
            )

        builder = TemplateComponentsIn(**(template.raw_payload or {}).get("builder_components", {}))
        errors = validate_template_draft(
            name=template.name,
            language=template.language,
            category=template.category or "utility",
            components=builder,
        )
        if errors:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "WA-TPL-VALIDATION",
                    "message": "Rascunho inválido.",
                    "errors": errors,
                },
            )

        if requester_role is not None and not has_permission(
            requester_role, INTERNAL_APPROVAL_MODULE, INTERNAL_APPROVAL_ACTION
        ):
            return await self._park_for_internal_approval(
                workspace_id,
                template,
                requested_by_user_id=requested_by_user_id,
                requester_label=requester_label,
            )

        return await self._perform_meta_submission_or_prepare(workspace_id, template)

    async def _park_for_internal_approval(
        self,
        workspace_id: UUID,
        template: WhatsAppMetaTemplate,
        *,
        requested_by_user_id: UUID | None,
        requester_label: str | None,
    ) -> TemplateSubmitResult:
        now = self._utc_now()
        template.raw_payload = {
            **(template.raw_payload or {}),
            "local_status": "pending_internal_approval",
            "internal_approval": {
                "requested_by_user_id": str(requested_by_user_id) if requested_by_user_id else None,
                "requested_by_label": requester_label,
                "requested_at": now.isoformat(),
            },
        }
        self.db.add(template)
        self.db.add(
            KlmAuditEvent(
                workspace_id=workspace_id,
                actor_type="user" if requested_by_user_id else "system",
                actor_label=requester_label or "Operador",
                event_type="whatsapp_template_internal_approval_requested",
                entity_type="whatsapp_meta_template",
                entity_id=template.id,
                summary=f"Submissão do template '{template.name}' aguarda aprovação interna.",
                after_data={"templateId": str(template.id), "name": template.name},
            )
        )
        approver_ids = await self.db.scalars(
            select(WorkspaceMembership.user_id).where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.role.in_(
                    (
                        WorkspaceRole.workspace_owner,
                        WorkspaceRole.workspace_admin,
                        WorkspaceRole.legal_manager,
                    )
                ),
                WorkspaceMembership.status == MembershipStatus.active,
            )
        )
        notification_service = NotificationService(self.db)
        requester_display = requester_label or "Um operador"
        for approver_id in approver_ids.all():
            await notification_service.create(
                approver_id,
                f"Template '{template.name}' aguarda sua aprovação",
                (
                    f"{requester_display} pediu envio do template '{template.name}' à Meta. "
                    "Revise e aprove na fila de templates para liberar a submissão."
                ),
                notification_type=NotificationType.info,
                workspace_id=workspace_id,
            )
        queue_realtime_event(
            self.db,
            workspace_id=workspace_id,
            event_type="WHATSAPP_TEMPLATE_INTERNAL_APPROVAL_REQUESTED",
            data={"templateId": str(template.id)},
        )
        await self.db.commit()
        await self.db.refresh(template)
        return TemplateSubmitResult(
            template=_to_unified_read(template),
            submitted_to_meta=False,
            message=(
                "Este template exige aprovação interna antes do envio à Meta. "
                "Um aprovador do workspace foi notificado."
            ),
        )

    async def list_pending_internal_approvals(
        self, workspace_id: UUID
    ) -> list[UnifiedTemplateRead]:
        items = await self.list_unified(workspace_id)
        return [item for item in items if item.local_status == "pending_internal_approval"]

    async def approve_internal_submission(
        self,
        workspace_id: UUID,
        template_id: UUID,
        *,
        approver_user_id: UUID,
        approver_label: str,
    ) -> TemplateSubmitResult:
        template = await self._get_draft(workspace_id, template_id)
        if _local_status(template) != "pending_internal_approval":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "WA-TPL-010",
                    "message": "Este rascunho não está aguardando aprovação interna.",
                },
            )
        internal_approval = dict((template.raw_payload or {}).get("internal_approval", {}))
        internal_approval["approved_by_user_id"] = str(approver_user_id)
        internal_approval["approved_by_label"] = approver_label
        internal_approval["approved_at"] = self._utc_now().isoformat()
        template.raw_payload = {
            **(template.raw_payload or {}),
            "internal_approval": internal_approval,
        }
        self.db.add(
            KlmAuditEvent(
                workspace_id=workspace_id,
                actor_user_id=approver_user_id,
                actor_type="user",
                actor_label=approver_label,
                event_type="whatsapp_template_internal_approval_granted",
                entity_type="whatsapp_meta_template",
                entity_id=template.id,
                summary=f"{approver_label} aprovou o envio do template '{template.name}' à Meta.",
                after_data={"templateId": str(template.id)},
            )
        )
        requester_id_raw = internal_approval.get("requested_by_user_id")
        if requester_id_raw:
            await NotificationService(self.db).create(
                UUID(requester_id_raw),
                f"Template '{template.name}' aprovado para envio",
                f"{approver_label} aprovou a submissão do template '{template.name}' à Meta.",
                notification_type=NotificationType.success,
                workspace_id=workspace_id,
            )
        await self.db.flush()
        return await self._perform_meta_submission_or_prepare(workspace_id, template)

    async def _perform_meta_submission_or_prepare(
        self, workspace_id: UUID, template: WhatsAppMetaTemplate
    ) -> TemplateSubmitResult:
        if not external_delivery_allowed():
            template.raw_payload = {
                **(template.raw_payload or {}),
                "local_status": "submission_prepared",
                "submission_prepared_at": self._utc_now().isoformat(),
            }
            self.db.add(template)
            await self.db.commit()
            await self.db.refresh(template)
            return TemplateSubmitResult(
                template=_to_unified_read(template),
                submitted_to_meta=False,
                message=(
                    "Rascunho preparado e validado. Aguardando aprovação para envio à Meta "
                    "(entregas externas estão desabilitadas neste ambiente)."
                ),
            )

        connection = await self.connection_service.get_connection(
            template.whatsapp_connection_id, workspace_id
        )
        access_token = await self.connection_service.resolve_meta_access_token(connection)
        waba_id = connection.waba_id or connection.business_account_id
        if not waba_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "WA-TPL-008",
                    "message": "A conexão oficial não possui WABA vinculada para submissão.",
                },
            )

        response = await self.meta_client.create_message_template(
            waba_id=waba_id,
            access_token=access_token,
            name=template.name,
            language=template.language,
            category=template.category or "utility",
            components=template.components,
        )
        template.external_template_id = response.get("id")
        template.status = MetaTemplateStatus.pending
        template.raw_payload = {
            **(template.raw_payload or {}),
            "local_status": "submission_prepared",
            "submitted_at": self._utc_now().isoformat(),
            "meta_create_response": response,
        }
        template.last_synced_at = self._utc_now()
        self.db.add(template)
        await self.db.commit()
        await self.db.refresh(template)
        return TemplateSubmitResult(
            template=_to_unified_read(template),
            submitted_to_meta=True,
            message="Template submetido à Meta. O status de aprovação chega pelo sync/webhook de status.",
        )

    # --- UX de rejeição: clonar como rascunho -------------------------------

    async def clone_rejected_as_draft(
        self, workspace_id: UUID, template_id: UUID
    ) -> TemplateDraftRead:
        """ "Corrigir e reenviar": clona um template rejeitado/sincronizado como
        um novo rascunho local editável, pronto para reabrir no builder."""
        result = await self.db.execute(
            select(WhatsAppMetaTemplate).where(
                WhatsAppMetaTemplate.id == template_id,
                WhatsAppMetaTemplate.workspace_id == workspace_id,
            )
        )
        source = result.scalars().first()
        if source is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "WA-TPL-003", "message": "Template não encontrado."},
            )
        if source.status != MetaTemplateStatus.rejected:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "WA-TPL-011",
                    "message": "Só é possível clonar templates rejeitados pela Meta.",
                },
            )

        raw = source.raw_payload or {}
        builder_payload = raw.get("builder_components")
        components = (
            TemplateComponentsIn(**builder_payload)
            if builder_payload
            else _components_from_meta(source.components or [])
        )

        base_name = normalize_template_name(f"{source.name}_v2")
        candidate_name = base_name
        suffix = 2
        while await self.db.scalar(
            select(WhatsAppMetaTemplate).where(
                WhatsAppMetaTemplate.workspace_id == workspace_id,
                WhatsAppMetaTemplate.whatsapp_connection_id == source.whatsapp_connection_id,
                WhatsAppMetaTemplate.name == candidate_name,
                WhatsAppMetaTemplate.language == source.language,
            )
        ):
            suffix += 1
            candidate_name = f"{base_name}_{suffix}"

        now = self._utc_now()
        clone = WhatsAppMetaTemplate(
            workspace_id=workspace_id,
            whatsapp_connection_id=source.whatsapp_connection_id,
            external_template_id=None,
            name=candidate_name,
            # V31: a Meta devolve "UTILITY"; o rascunho editável precisa da forma
            # minúscula que o PATCH aceita, senão "Corrigir e reenviar" morre em 422.
            category=_normalize_template_category(source.category),
            language=source.language,
            status=MetaTemplateStatus.pending,
            quality_rating=None,
            components=_build_meta_components(components),
            raw_payload={
                "source": "local_draft",
                "local_status": "draft",
                "validation_errors": [],
                "builder_components": components.model_dump(),
                "cloned_from_template_id": str(source.id),
                "cloned_from_rejection_reason": raw.get("rejected_reason"),
            },
            last_synced_at=now,
        )
        self.db.add(clone)
        self.db.add(
            KlmAuditEvent(
                workspace_id=workspace_id,
                actor_type="user",
                actor_label="Operador",
                event_type="whatsapp_template_cloned_from_rejection",
                entity_type="whatsapp_meta_template",
                entity_id=clone.id,
                summary=(
                    f"Template rejeitado '{source.name}' clonado como rascunho "
                    f"'{candidate_name}' para correção."
                ),
                before_data={"sourceTemplateId": str(source.id)},
                after_data={"cloneTemplateId": None},
            )
        )
        await self.db.commit()
        await self.db.refresh(clone)
        return TemplateDraftRead(**_to_unified_read(clone).model_dump(exclude={"last_synced_at"}))
