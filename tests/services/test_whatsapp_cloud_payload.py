from __future__ import annotations

from uuid import uuid4

import pytest

from app.services.whatsapp.cloud_payload import (
    WhatsAppPayloadError,
    normalize_internal_payload,
    render_cloud_api_message,
)


def test_reply_buttons_are_bounded_and_rendered_to_cloud_api() -> None:
    payload = normalize_internal_payload(
        {
            "kind": "reply_buttons",
            "body": "Como prefere continuar?",
            "footer": "KLM Advogados",
            "buttons": [
                {"id": "schedule", "title": "Agendar"},
                {"id": "proposal", "title": "Ver proposta"},
                {"id": "human", "title": "Falar com advogado"},
            ],
        }
    )

    rendered = render_cloud_api_message(payload)

    assert rendered["type"] == "interactive"
    assert rendered["interactive"]["type"] == "button"
    assert [
        item["reply"]["id"] for item in rendered["interactive"]["action"]["buttons"]
    ] == ["schedule", "proposal", "human"]


def test_reply_context_uses_only_the_verified_provider_message_id() -> None:
    rendered = render_cloud_api_message(
        {"kind": "text", "body": "Respondendo à mensagem anterior."},
        reply_to_provider_message_id="wamid.provider.original.123",
    )

    assert rendered == {
        "context": {"message_id": "wamid.provider.original.123"},
        "type": "text",
        "text": {
            "preview_url": False,
            "body": "Respondendo à mensagem anterior.",
        },
    }


@pytest.mark.parametrize(
    "buttons",
    [
        [],
        [{"id": str(index), "title": str(index)} for index in range(4)],
        [{"id": "same", "title": "Um"}, {"id": "same", "title": "Dois"}],
    ],
)
def test_reply_buttons_reject_provider_invalid_shapes(buttons) -> None:
    with pytest.raises(WhatsAppPayloadError):
        normalize_internal_payload(
            {"kind": "reply_buttons", "body": "Escolha", "buttons": buttons}
        )


def test_list_accepts_ten_unique_rows_and_rejects_the_eleventh() -> None:
    rows = [
        {"id": f"slot-{index}", "title": f"Horário {index}"}
        for index in range(10)
    ]
    normalized = normalize_internal_payload(
        {
            "kind": "list",
            "body": "Escolha um horário",
            "buttonText": "Ver horários",
            "sections": [{"title": "Disponíveis", "rows": rows}],
        }
    )
    assert len(normalized["sections"][0]["rows"]) == 10

    with pytest.raises(WhatsAppPayloadError, match="no máximo 10"):
        normalize_internal_payload(
            {
                "kind": "list",
                "body": "Escolha",
                "buttonText": "Abrir",
                "sections": [
                    {
                        "title": "Disponíveis",
                        "rows": rows + [{"id": "slot-10", "title": "Horário 10"}],
                    }
                ],
            }
        )


def test_template_and_flow_render_without_arbitrary_provider_passthrough() -> None:
    template = render_cloud_api_message(
        {
            "kind": "template",
            "name": "retomar_atendimento_juridico",
            "language": "pt_BR",
            "components": [
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": "Ana"}],
                }
            ],
            "providerInjection": "must-not-pass",
        }
    )
    flow = render_cloud_api_message(
        {
            "kind": "flow",
            "body": "Preencha os dados para a análise.",
            "flowId": "flow-legal-intake",
            "flowToken": "opaque-lead-token",
            "flowCta": "Preencher",
            "action": "navigate",
            "actionPayload": {"screen": "INTAKE"},
            "mode": "published",
        }
    )

    assert template == {
        "type": "template",
        "template": {
            "name": "retomar_atendimento_juridico",
            "language": {"code": "pt_BR"},
            "components": [
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": "Ana"}],
                }
            ],
        },
    }
    assert flow["interactive"]["action"]["parameters"]["flow_id"] == "flow-legal-intake"
    assert "providerInjection" not in str(template)


def test_private_klm_media_reference_is_normalized_but_not_rendered_externally() -> None:
    normalized = normalize_internal_payload(
        {
            "kind": "media",
            "type": "document",
            "documentId": "document-local-123",
            "filename": "negativa-plano.pdf",
            "caption": "Documento vinculado ao caso.",
        }
    )

    assert normalized["localReference"] == {"documentId": "document-local-123"}
    assert normalized["requiresProviderUpload"] is True
    assert normalized["filename"] == "negativa-plano.pdf"

    with pytest.raises(WhatsAppPayloadError, match="publicada no provedor"):
        render_cloud_api_message(normalized)


def test_inferred_media_kind_keeps_unverified_attachment_as_local_reference() -> None:
    normalized = normalize_internal_payload(
        {"kind": "media"},
        body="Segue a negativa.",
        attachments=[
            {
                "id": "chatwoot-attachment-31",
                "fileType": "file",
                "name": "negativa.pdf",
            }
        ],
    )

    assert normalized == {
        "kind": "media",
        "type": "document",
        "localReference": {
            "externalProvider": "unverified_external",
            "externalAttachmentId": "chatwoot-attachment-31",
        },
        "requiresProviderUpload": True,
    }
    with pytest.raises(WhatsAppPayloadError, match="publicada no provedor"):
        render_cloud_api_message(normalized)


def test_provider_media_id_requires_a_verified_upload_receipt_before_rendering() -> None:
    with pytest.raises(WhatsAppPayloadError, match="prova de upload"):
        normalize_internal_payload(
            {
                "kind": "media",
                "type": "image",
                "providerMediaId": "meta-media-unproven-123",
            }
        )

    receipt_id = str(uuid4())
    normalized = normalize_internal_payload(
        {
            "kind": "media",
            "type": "image",
            "providerMediaId": "meta-media-verified-123",
            "providerMediaReceiptId": receipt_id,
            "caption": "Prescrição recebida.",
        }
    )

    assert normalized["providerMediaId"] == "meta-media-verified-123"
    assert normalized["providerMediaReceiptId"] == receipt_id
    assert "id" not in normalized
    with pytest.raises(WhatsAppPayloadError, match="não possui prova"):
        render_cloud_api_message(normalized)
    assert render_cloud_api_message(
        normalized,
        verified_provider_media_receipt_ids={receipt_id},
    ) == {
        "type": "image",
        "image": {
            "id": "meta-media-verified-123",
            "caption": "Prescrição recebida.",
        },
    }


def test_template_media_receipt_is_internal_and_never_leaks_to_meta_payload() -> None:
    receipt_id = str(uuid4())
    payload = {
        "kind": "template",
        "name": "documento_recebido",
        "language": "pt_BR",
        "components": [
            {
                "type": "header",
                "parameters": [
                    {
                        "type": "document",
                        "document": {
                            "id": "meta-document-verified-1",
                            "providerMediaReceiptId": receipt_id,
                            "filename": "negativa.pdf",
                        },
                    }
                ],
            }
        ],
    }

    rendered = render_cloud_api_message(
        payload,
        verified_provider_media_receipt_ids={receipt_id},
    )

    document = rendered["template"]["components"][0]["parameters"][0]["document"]
    assert document == {"id": "meta-document-verified-1", "filename": "negativa.pdf"}
    assert "providerMediaReceiptId" not in str(rendered)
