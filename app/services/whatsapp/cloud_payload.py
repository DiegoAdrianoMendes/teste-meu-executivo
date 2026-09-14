"""Validation and rendering for native WhatsApp Cloud API message payloads."""

from __future__ import annotations

from typing import Any
from uuid import UUID


class WhatsAppPayloadError(ValueError):
    pass


def _text(value: Any, *, field: str, minimum: int = 1, maximum: int) -> str:
    normalized = str(value or "").strip()
    if not minimum <= len(normalized) <= maximum:
        raise WhatsAppPayloadError(
            f"{field} deve ter entre {minimum} e {maximum} caracteres."
        )
    return normalized


def _optional_text(value: Any, *, field: str, maximum: int) -> str | None:
    if value is None or not str(value).strip():
        return None
    return _text(value, field=field, maximum=maximum)


def _provider_media_receipt_id(value: Any) -> str:
    try:
        return str(UUID(str(value or "").strip()))
    except (TypeError, ValueError, AttributeError):
        raise WhatsAppPayloadError(
            "providerMediaId exige uma prova de upload válida."
        ) from None


def _normalize_provider_media(media: dict[str, Any], *, field: str) -> dict[str, Any]:
    provider_media_id = str(media.get("id") or "").strip()
    link = str(media.get("link") or "").strip()
    if not provider_media_id and not link:
        raise WhatsAppPayloadError(f"{field} exige id ou link.")
    normalized: dict[str, Any] = {}
    if provider_media_id:
        normalized["id"] = _text(
            provider_media_id,
            field=f"ID de {field.lower()}",
            maximum=255,
        )
        normalized["providerMediaReceiptId"] = _provider_media_receipt_id(
            media.get("providerMediaReceiptId")
        )
    else:
        normalized["link"] = _text(link, field=f"Link de {field.lower()}", maximum=2048)
    filename = _optional_text(media.get("filename"), field="Nome do arquivo", maximum=255)
    if filename:
        normalized["filename"] = filename
    return normalized


def _template_components(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 20:
        raise WhatsAppPayloadError("components do modelo deve ser uma lista com até 20 itens.")
    components: list[dict[str, Any]] = []
    for component in value:
        if not isinstance(component, dict):
            raise WhatsAppPayloadError("Cada componente do modelo deve ser um objeto.")
        component_type = str(component.get("type") or "").strip().lower()
        if component_type not in {"header", "body", "button"}:
            raise WhatsAppPayloadError("Tipo de componente de modelo não suportado.")
        parameters = component.get("parameters") or []
        if not isinstance(parameters, list) or len(parameters) > 20:
            raise WhatsAppPayloadError("parameters deve ser uma lista com até 20 itens.")
        normalized_parameters: list[dict[str, Any]] = []
        for parameter in parameters:
            if not isinstance(parameter, dict):
                raise WhatsAppPayloadError("Parâmetro de modelo inválido.")
            parameter_type = str(parameter.get("type") or "text").strip().lower()
            if parameter_type == "text":
                normalized_parameters.append(
                    {
                        "type": "text",
                        "text": _text(
                            parameter.get("text"),
                            field="Texto do parâmetro",
                            maximum=1024,
                        ),
                    }
                )
            elif parameter_type in {"image", "video", "document"}:
                media = parameter.get(parameter_type)
                if not isinstance(media, dict):
                    raise WhatsAppPayloadError("Parâmetro de mídia exige id ou link.")
                normalized_parameters.append(
                    {
                        "type": parameter_type,
                        parameter_type: _normalize_provider_media(
                            media,
                            field="Parâmetro de mídia",
                        ),
                    }
                )
            elif parameter_type == "payload":
                normalized_parameters.append(
                    {
                        "type": "payload",
                        "payload": _text(
                            parameter.get("payload"),
                            field="Payload do botão",
                            maximum=256,
                        ),
                    }
                )
            else:
                raise WhatsAppPayloadError("Tipo de parâmetro de modelo não suportado.")
        normalized: dict[str, Any] = {
            "type": component_type,
            "parameters": normalized_parameters,
        }
        if component_type == "button":
            sub_type = str(component.get("sub_type") or component.get("subType") or "").strip()
            index = str(component.get("index") or "").strip()
            if sub_type not in {"quick_reply", "url"} or not index.isdigit():
                raise WhatsAppPayloadError("Botão de modelo exige sub_type e index válidos.")
            normalized["sub_type"] = sub_type
            normalized["index"] = index
        components.append(normalized)
    return components


def normalize_internal_payload(
    value: dict[str, Any] | None,
    *,
    body: str | None = None,
    template_id: str | None = None,
    attachments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Normalize the KLM-owned message contract before it is persisted."""
    payload = dict(value or {})
    inferred_kind = "template" if template_id else "media" if attachments else "text"
    kind = str(payload.get("kind") or inferred_kind).strip().lower()

    if kind == "text":
        return {
            "kind": "text",
            "body": _text(payload.get("body", body), field="Mensagem", maximum=4096),
            "previewUrl": bool(payload.get("previewUrl", False)),
        }

    if kind == "template":
        name = _text(
            payload.get("name") or template_id,
            field="Nome do modelo",
            maximum=512,
        )
        language = _text(
            payload.get("language") or "pt_BR",
            field="Idioma do modelo",
            maximum=16,
        )
        return {
            "kind": "template",
            "name": name,
            "language": language,
            "components": _template_components(payload.get("components")),
        }

    if kind == "reply_buttons":
        buttons = payload.get("buttons")
        if not isinstance(buttons, list) or not 1 <= len(buttons) <= 3:
            raise WhatsAppPayloadError("A mensagem deve ter de 1 a 3 botões de resposta.")
        normalized_buttons = []
        seen_ids: set[str] = set()
        for button in buttons:
            if not isinstance(button, dict):
                raise WhatsAppPayloadError("Botão de resposta inválido.")
            button_id = _text(button.get("id"), field="ID do botão", maximum=256)
            if button_id in seen_ids:
                raise WhatsAppPayloadError("IDs de botões não podem se repetir.")
            seen_ids.add(button_id)
            normalized_buttons.append(
                {
                    "id": button_id,
                    "title": _text(button.get("title"), field="Título do botão", maximum=20),
                }
            )
        return {
            "kind": "reply_buttons",
            "body": _text(payload.get("body", body), field="Mensagem", maximum=1024),
            "header": _optional_text(payload.get("header"), field="Cabeçalho", maximum=60),
            "footer": _optional_text(payload.get("footer"), field="Rodapé", maximum=60),
            "buttons": normalized_buttons,
        }

    if kind == "list":
        sections = payload.get("sections")
        if not isinstance(sections, list) or not 1 <= len(sections) <= 10:
            raise WhatsAppPayloadError("A lista deve ter de 1 a 10 seções.")
        normalized_sections = []
        seen_ids: set[str] = set()
        total_rows = 0
        for section in sections:
            if not isinstance(section, dict):
                raise WhatsAppPayloadError("Seção da lista inválida.")
            rows = section.get("rows")
            if not isinstance(rows, list) or not rows:
                raise WhatsAppPayloadError("Cada seção precisa ter ao menos uma opção.")
            normalized_rows = []
            for row in rows:
                if not isinstance(row, dict):
                    raise WhatsAppPayloadError("Opção da lista inválida.")
                row_id = _text(row.get("id"), field="ID da opção", maximum=200)
                if row_id in seen_ids:
                    raise WhatsAppPayloadError("IDs das opções não podem se repetir.")
                seen_ids.add(row_id)
                total_rows += 1
                normalized_row = {
                    "id": row_id,
                    "title": _text(row.get("title"), field="Título da opção", maximum=24),
                }
                description = _optional_text(
                    row.get("description"), field="Descrição da opção", maximum=72
                )
                if description:
                    normalized_row["description"] = description
                normalized_rows.append(normalized_row)
            normalized_sections.append(
                {
                    "title": _optional_text(
                        section.get("title"), field="Título da seção", maximum=24
                    ),
                    "rows": normalized_rows,
                }
            )
        if total_rows > 10:
            raise WhatsAppPayloadError("A lista aceita no máximo 10 opções no total.")
        return {
            "kind": "list",
            "body": _text(payload.get("body", body), field="Mensagem", maximum=1024),
            "header": _optional_text(payload.get("header"), field="Cabeçalho", maximum=60),
            "footer": _optional_text(payload.get("footer"), field="Rodapé", maximum=60),
            "buttonText": _text(
                payload.get("buttonText"), field="Texto para abrir a lista", maximum=20
            ),
            "sections": normalized_sections,
        }

    if kind == "flow":
        action = str(payload.get("action") or "navigate").strip().lower()
        if action not in {"navigate", "data_exchange"}:
            raise WhatsAppPayloadError("Ação de WhatsApp Flow inválida.")
        normalized = {
            "kind": "flow",
            "body": _text(payload.get("body", body), field="Mensagem", maximum=1024),
            "header": _optional_text(payload.get("header"), field="Cabeçalho", maximum=60),
            "footer": _optional_text(payload.get("footer"), field="Rodapé", maximum=60),
            "flowId": _text(payload.get("flowId"), field="ID do Flow", maximum=200),
            "flowToken": _text(payload.get("flowToken"), field="Token do Flow", maximum=200),
            "flowCta": _text(payload.get("flowCta"), field="Chamada do Flow", maximum=20),
            "action": action,
            "actionPayload": dict(payload.get("actionPayload") or {}),
            "mode": str(payload.get("mode") or "published").strip().lower(),
        }
        if normalized["mode"] not in {"draft", "published"}:
            raise WhatsAppPayloadError("Modo do WhatsApp Flow inválido.")
        return normalized

    if kind == "media":
        attachment_source = (attachments or [{}])[0]
        source = {
            **(attachment_source if isinstance(attachment_source, dict) else {}),
            **payload,
        }
        media_type = str(
            source.get("type") or source.get("mediaType") or "document"
        ).strip().lower()
        if media_type not in {"image", "audio", "video", "document"}:
            raise WhatsAppPayloadError("Tipo de mídia não suportado pelo WhatsApp oficial.")
        provider_media_id = str(source.get("providerMediaId") or "").strip()
        provider_media_receipt_id = source.get("providerMediaReceiptId")
        external_attachment_id = str(
            source.get("externalAttachmentId") or source.get("id") or ""
        ).strip()
        external_provider = str(
            source.get("externalProvider") or source.get("sourceProvider") or ""
        ).strip()
        link = str(source.get("link") or source.get("url") or "").strip()
        local_reference = source.get("localReference")
        if local_reference is not None and not isinstance(local_reference, dict):
            raise WhatsAppPayloadError("Referência privada de mídia inválida.")
        local_reference = local_reference or {}
        document_id = str(
            source.get("documentId") or local_reference.get("documentId") or ""
        ).strip()
        storage_object_id = str(
            source.get("storageObjectId")
            or local_reference.get("storageObjectId")
            or ""
        ).strip()
        external_attachment_id = str(
            external_attachment_id
            or local_reference.get("externalAttachmentId")
            or ""
        ).strip()
        external_provider = str(
            external_provider
            or local_reference.get("externalProvider")
            or ("unverified_external" if external_attachment_id else "")
        ).strip()
        if (
            not provider_media_id
            and not link
            and not document_id
            and not storage_object_id
            and not external_attachment_id
        ):
            raise WhatsAppPayloadError(
                "A mídia exige providerMediaId, link ou uma referência privada do KLM."
            )
        normalized_media: dict[str, Any] = {"kind": "media", "type": media_type}
        if provider_media_id:
            normalized_media["providerMediaId"] = _text(
                provider_media_id,
                field="ID da mídia no provedor",
                maximum=255,
            )
            normalized_media["providerMediaReceiptId"] = _provider_media_receipt_id(
                provider_media_receipt_id
            )
        elif link:
            normalized_media["link"] = _text(link, field="Link da mídia", maximum=2048)
        else:
            normalized_media["localReference"] = {
                **({"documentId": document_id} if document_id else {}),
                **({"storageObjectId": storage_object_id} if storage_object_id else {}),
                **(
                    {"externalProvider": external_provider}
                    if external_provider
                    else {}
                ),
                **(
                    {"externalAttachmentId": external_attachment_id}
                    if external_attachment_id
                    else {}
                ),
            }
            normalized_media["requiresProviderUpload"] = True
        caption = _optional_text(source.get("caption"), field="Legenda", maximum=1024)
        if caption and media_type != "audio":
            normalized_media["caption"] = caption
        if media_type == "document":
            filename = _optional_text(source.get("filename"), field="Nome do arquivo", maximum=255)
            if filename:
                normalized_media["filename"] = filename
        return normalized_media

    raise WhatsAppPayloadError(f"Tipo de mensagem WhatsApp não suportado: {kind}.")


def provider_media_references(payload: dict[str, Any]) -> list[dict[str, str]]:
    """Return every provider ID and its tenant-bound upload receipt."""
    normalized = normalize_internal_payload(payload)
    references: list[dict[str, str]] = []
    if normalized["kind"] == "media" and normalized.get("providerMediaId"):
        references.append(
            {
                "providerMediaId": normalized["providerMediaId"],
                "receiptId": normalized["providerMediaReceiptId"],
                "mediaType": normalized["type"],
            }
        )
    elif normalized["kind"] == "template":
        for component in normalized["components"]:
            for parameter in component["parameters"]:
                parameter_type = parameter["type"]
                if parameter_type not in {"image", "video", "document"}:
                    continue
                media = parameter[parameter_type]
                if media.get("id"):
                    references.append(
                        {
                            "providerMediaId": media["id"],
                            "receiptId": media["providerMediaReceiptId"],
                            "mediaType": parameter_type,
                        }
                    )
    return references


def _require_verified_provider_media(
    normalized: dict[str, Any],
    verified_provider_media_receipt_ids: set[str] | None,
) -> None:
    verified = verified_provider_media_receipt_ids or set()
    for reference in provider_media_references(normalized):
        if reference["receiptId"] not in verified:
            raise WhatsAppPayloadError(
                "A mídia do provedor não possui prova de upload validada para esta conexão."
            )


def _render_template_components(components: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rendered_components: list[dict[str, Any]] = []
    for component in components:
        rendered_parameters: list[dict[str, Any]] = []
        for parameter in component["parameters"]:
            parameter_type = parameter["type"]
            if parameter_type not in {"image", "video", "document"}:
                rendered_parameters.append(dict(parameter))
                continue
            media = parameter[parameter_type]
            rendered_media = {
                key: value
                for key, value in media.items()
                if key in {"id", "link", "filename"}
            }
            rendered_parameters.append(
                {"type": parameter_type, parameter_type: rendered_media}
            )
        rendered_component = {
            "type": component["type"],
            "parameters": rendered_parameters,
        }
        if component["type"] == "button":
            rendered_component["sub_type"] = component["sub_type"]
            rendered_component["index"] = component["index"]
        rendered_components.append(rendered_component)
    return rendered_components


def render_cloud_api_message(
    payload: dict[str, Any],
    *,
    verified_provider_media_receipt_ids: set[str] | None = None,
    reply_to_provider_message_id: str | None = None,
) -> dict[str, Any]:
    """Render a validated KLM payload into the Meta messages object."""
    def with_reply_context(message: dict[str, Any]) -> dict[str, Any]:
        if not reply_to_provider_message_id:
            return message
        return {
            "context": {
                "message_id": _text(
                    reply_to_provider_message_id,
                    field="ID da mensagem respondida",
                    maximum=255,
                )
            },
            **message,
        }

    normalized = normalize_internal_payload(payload)
    _require_verified_provider_media(normalized, verified_provider_media_receipt_ids)
    kind = normalized["kind"]
    if kind == "text":
        return with_reply_context({
            "type": "text",
            "text": {
                "preview_url": normalized["previewUrl"],
                "body": normalized["body"],
            },
        })
    if kind == "template":
        template: dict[str, Any] = {
            "name": normalized["name"],
            "language": {"code": normalized["language"]},
        }
        if normalized["components"]:
            template["components"] = _render_template_components(normalized["components"])
        return with_reply_context({"type": "template", "template": template})
    if kind in {"reply_buttons", "list", "flow"}:
        interactive: dict[str, Any] = {
            "type": "button" if kind == "reply_buttons" else kind,
            "body": {"text": normalized["body"]},
        }
        if normalized.get("header"):
            interactive["header"] = {"type": "text", "text": normalized["header"]}
        if normalized.get("footer"):
            interactive["footer"] = {"text": normalized["footer"]}
        if kind == "reply_buttons":
            interactive["action"] = {
                "buttons": [
                    {
                        "type": "reply",
                        "reply": {"id": item["id"], "title": item["title"]},
                    }
                    for item in normalized["buttons"]
                ]
            }
        elif kind == "list":
            interactive["action"] = {
                "button": normalized["buttonText"],
                "sections": [
                    {
                        **({"title": section["title"]} if section.get("title") else {}),
                        "rows": section["rows"],
                    }
                    for section in normalized["sections"]
                ],
            }
        else:
            interactive["action"] = {
                "name": "flow",
                "parameters": {
                    "flow_message_version": "3",
                    "flow_token": normalized["flowToken"],
                    "flow_id": normalized["flowId"],
                    "flow_cta": normalized["flowCta"],
                    "flow_action": normalized["action"],
                    "flow_action_payload": normalized["actionPayload"],
                    "mode": normalized["mode"],
                },
            }
        return with_reply_context({"type": "interactive", "interactive": interactive})
    if kind == "media":
        media_type = normalized["type"]
        if normalized.get("localReference") and not (
            normalized.get("providerMediaId") or normalized.get("link")
        ):
            raise WhatsAppPayloadError(
                "A mídia privada ainda precisa ser publicada no provedor antes do envio externo."
            )
        media: dict[str, Any] = {}
        if normalized.get("providerMediaId"):
            media["id"] = normalized["providerMediaId"]
        else:
            media["link"] = normalized["link"]
        if normalized.get("caption"):
            media["caption"] = normalized["caption"]
        if normalized.get("filename"):
            media["filename"] = normalized["filename"]
        return with_reply_context({"type": media_type, media_type: media})
    raise WhatsAppPayloadError("Payload WhatsApp não renderizável.")
