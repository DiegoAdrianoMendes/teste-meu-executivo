"""Small, tenant-scoped primitives for Flow sequence stages.

Sequence stages intentionally reuse ``Flow``/``FlowRun``/``ScheduledMessage``.
This module contains the durable contract shared by graph validation, the
scheduled-message worker and the WhatsApp inbound interruption path.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select

from app.models.flow import FlowRun, ScheduledMessage
from app.models.klm_operations import KlmAuditEvent
from app.models.whatsapp_campaign import (
    WhatsAppCampaign,
    WhatsAppCampaignRecipient,
    WhatsAppMetaTemplate,
)
from app.schemas.flow import FlowCreate
from app.schemas.whatsapp_campaign import (
    CampaignAudienceFilter,
    CampaignCreate,
    CampaignLaunchRequest,
    WhatsAppSequenceStartCreate,
)
from app.services.flow_runtime_primitives import get_context_value
from app.services.whatsapp.helpers import phone_number_lookup_variants

SEQUENCE_TEMPLATE_STATUSES = frozenset({"approved", "pending"})
SEQUENCE_DELAY_UNITS = frozenset({"hours", "days"})
SEQUENCE_PENDING_TEMPLATE_GRACE = timedelta(minutes=15)


def _sequence_template_variables(template: WhatsAppMetaTemplate) -> list[int]:
    body = next(
        (
            item.get("text")
            for item in template.components or []
            if isinstance(item, dict) and str(item.get("type", "")).upper() == "BODY"
        ),
        "",
    )
    return sorted(
        {
            int(match.group(1))
            for match in re.finditer(r"\{\{\s*(\d+)\s*\}\}", str(body or ""))
        }
    )


def _sequence_has_non_body_variables(template: WhatsAppMetaTemplate) -> bool:
    placeholder = re.compile(r"\{\{\s*(\d+)\s*\}\}")
    for item in template.components or []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type", "")).upper()
        if kind == "HEADER" and placeholder.search(str(item.get("text") or "")):
            return True
        if kind == "BUTTONS":
            if any(
                isinstance(button, dict)
                and (
                    placeholder.search(str(button.get("url") or ""))
                    or placeholder.search(str(button.get("text") or ""))
                )
                for button in item.get("buttons", [])
            ):
                return True
    return False


def _sequence_body_for_flow(body: str, variable_positions: list[int]) -> str:
    """Convert Meta's positional body placeholders to flow variables."""
    allowed = set(variable_positions)

    def replace(match: re.Match[str]) -> str:
        position = int(match.group(1))
        return f"{{sequence_{position}}}" if position in allowed else match.group(0)

    return re.sub(r"\{\{\s*(\d+)\s*\}\}", replace, body)


async def start_picker_sequence(
    db,
    *,
    workspace_id: UUID,
    actor_user_id: UUID,
    payload: WhatsAppSequenceStartCreate,
    picker,
    campaign_service,
) -> Any:
    """Persist and launch a picker sequence through the canonical campaign path.

    The first stage is materialized as the campaign's official Meta send.  The
    remaining stages are an immutable Flow revision linked through
    ``follow_up_flow_id`` and start after delivery, so every delayed stage uses
    the existing scheduled-message worker, interruption, pending-template and
    idempotency contracts.
    """
    if not payload.stages:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "WA-SEQUENCE-001", "message": "A sequência exige ao menos uma etapa."},
        )

    idempotency_key = (payload.idempotency_key or "").strip()
    if not idempotency_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "WA-SEQUENCE-IDEMPOTENCY",
                "message": "Idempotency-Key é obrigatório para iniciar uma sequência.",
            },
        )

    fingerprint_payload = payload.model_dump(mode="json", exclude_none=True)
    fingerprint_payload.pop("idempotency_key", None)
    for fingerprint_stage in fingerprint_payload.get("stages", []):
        # Stage text is always derived from the server-side Meta template
        # below; a client preview must not alter idempotency semantics.
        fingerprint_stage.pop("body", None)
        for fingerprint_binding in fingerprint_stage.get("bindings", []):
            fingerprint_binding.pop("value", None)
    sequence_fingerprint = sha256(
        json.dumps(
            fingerprint_payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        ).hexdigest()

    def require_single_recipient(campaign) -> None:
        total = int(getattr(campaign, "total_recipients", 0) or 0)
        eligible = int(getattr(campaign, "eligible_recipients", 0) or 0)
        if total != 1 or eligible != 1:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "WA-SEQUENCE-RECIPIENT-COUNT",
                    "message": (
                        "A sequência exige exatamente um destinatário elegível; "
                        f"prévia encontrou total={total}, elegíveis={eligible}."
                    ),
                },
            )

    async def require_first_delivery(campaign):
        if str(getattr(campaign.status, "value", campaign.status)) in {
            "failed",
            "paused",
        }:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "code": "WA-SEQUENCE-FIRST-SEND-NOT-ACCEPTED",
                    "message": "A campanha foi interrompida antes de aceitar a primeira etapa.",
                    "campaignStatus": str(
                        getattr(campaign.status, "value", campaign.status)
                    ),
                },
            )
        recipient = await db.scalar(
            select(WhatsAppCampaignRecipient)
            .where(
                WhatsAppCampaignRecipient.workspace_id == workspace_id,
                WhatsAppCampaignRecipient.campaign_id == campaign.id,
            )
            .order_by(WhatsAppCampaignRecipient.created_at.asc())
            .limit(1)
        )
        accepted_statuses = {"sent", "delivered", "read"}
        recipient_status = (
            str(getattr(recipient.status, "value", recipient.status))
            if recipient is not None
            else "missing"
        )
        if recipient is None or recipient_status not in accepted_statuses or not recipient.wamid:
            error_message = (
                getattr(recipient, "last_error", None)
                if recipient is not None
                else None
            )
            retryable = recipient_status in {
                "queued",
                "scheduled",
                "processing",
                "delivery_uncertain",
            }
            raise HTTPException(
                status_code=(
                    status.HTTP_503_SERVICE_UNAVAILABLE
                    if retryable
                    else status.HTTP_502_BAD_GATEWAY
                ),
                detail={
                    "code": "WA-SEQUENCE-FIRST-SEND-NOT-ACCEPTED",
                    "message": (
                        "A primeira etapa não foi aceita pelo provedor; "
                        "a sequência não foi confirmada."
                    ),
                    "recipientStatus": recipient_status,
                    "providerError": error_message,
                },
            )
        return recipient

    existing = await db.scalar(
        select(WhatsAppCampaign).where(
            WhatsAppCampaign.workspace_id == workspace_id,
            WhatsAppCampaign.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if existing.idempotency_fingerprint != sequence_fingerprint:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "WA-SEQUENCE-IDEMPOTENCY-MISMATCH",
                    "message": "A chave de idempotência já foi usada para outra sequência.",
                },
            )
        if str(getattr(existing.status, "value", existing.status)) == "draft":
            require_single_recipient(existing)
            launched = await campaign_service.launch_campaign(
                workspace_id,
                existing.id,
                CampaignLaunchRequest(dry_run=False),
            )
            require_single_recipient(launched)
            await require_first_delivery(launched)
            return launched
        require_single_recipient(existing)
        existing_read = await campaign_service.get_campaign(workspace_id, existing.id)
        await require_first_delivery(existing_read)
        return existing_read

    contact, matter = await picker._resolve_context(
        workspace_id,
        thread_id=payload.thread_id,
        # A production conversation sends both the omnichannel thread UUID
        # and the CRM Client UUID.  The thread/identity is authoritative for
        # the WhatsApp recipient; Client is used below only as a case-scope
        # consistency check.
        contact_id=None if payload.thread_id else payload.contact_id,
        matter_id=payload.matter_id,
    )
    if contact is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "WA-SEQUENCE-CONTACT-404",
                "message": "Contato da conversa não encontrado.",
            },
        )
    if matter is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "WA-SEQUENCE-MATTER-REQUIRED",
                "message": "Iniciar uma sequência exige um caso vinculado à conversa.",
            },
        )
    direct_contact = None
    direct_client = None
    if payload.contact_id:
        from app.models.client import Client
        from app.models.whatsapp import WhatsAppContact

        direct_contact = await db.scalar(
            select(WhatsAppContact).where(
                WhatsAppContact.id == payload.contact_id,
                WhatsAppContact.workspace_id == workspace_id,
            )
        )
        if direct_contact is None:
            direct_client = await db.scalar(
                select(Client).where(
                    Client.id == payload.contact_id,
                    Client.workspace_id == workspace_id,
                )
            )
    if direct_contact is not None and direct_contact.matter_id != matter.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "WA-SEQUENCE-CONTEXT-MISMATCH",
                "message": "Contato e caso não pertencem à mesma conversa.",
            },
        )
    if direct_client is not None and matter.client_id != direct_client.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "WA-SEQUENCE-CONTEXT-MISMATCH",
                "message": "Cliente e caso não pertencem à mesma conversa.",
            },
        )
    if payload.thread_id:
        from app.models.klm_operations import OmnichannelThread
        from app.models.whatsapp import WhatsAppContact

        thread_contact = await db.scalar(
            select(WhatsAppContact).where(
                WhatsAppContact.id == payload.thread_id,
                WhatsAppContact.workspace_id == workspace_id,
            )
        )
        thread = await db.scalar(
            select(OmnichannelThread).where(
                OmnichannelThread.id == payload.thread_id,
                OmnichannelThread.workspace_id == workspace_id,
            )
        )
        if thread_contact is None and thread is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "WA-SEQUENCE-THREAD-404",
                    "message": "A thread da conversa não foi encontrada neste workspace.",
                },
            )
        if thread_contact is not None and thread_contact.id != contact.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "WA-SEQUENCE-CONTEXT-MISMATCH",
                    "message": "Thread e contato não coincidem.",
                },
            )
        if thread is not None:
            raw_contact_id = (thread.thread_metadata or {}).get("contactId") or (
                thread.thread_metadata or {}
            ).get("contact_id")
            if thread_contact is None and not raw_contact_id:
                from app.models.omnichannel_v6 import OmnichannelEndpoint, OmnichannelIdentity
                from app.models.whatsapp import WhatsAppContact

                identity = (
                    await db.scalar(
                        select(OmnichannelIdentity).where(
                            OmnichannelIdentity.id == thread.identity_id,
                            OmnichannelIdentity.workspace_id == workspace_id,
                        )
                    )
                    if thread.identity_id
                    else None
                )
                identity_contacts = []
                identity_addresses: set[str] = set()
                if identity is not None:
                    if identity.canonical_phone:
                        identity_addresses.update(
                            phone_number_lookup_variants(identity.canonical_phone)
                        )
                    endpoint_addresses = list(
                        (
                            await db.scalars(
                                select(OmnichannelEndpoint.normalized_address).where(
                                    OmnichannelEndpoint.workspace_id == workspace_id,
                                    OmnichannelEndpoint.identity_id == identity.id,
                                    OmnichannelEndpoint.channel == "whatsapp",
                                    OmnichannelEndpoint.normalized_address.is_not(None),
                                )
                            )
                        ).all()
                    )
                    for address in endpoint_addresses:
                        identity_addresses.update(phone_number_lookup_variants(address))
                if identity_addresses:
                    identity_contacts = list(
                        (
                            await db.scalars(
                                select(WhatsAppContact).where(
                                    WhatsAppContact.workspace_id == workspace_id,
                                    WhatsAppContact.phone.in_(identity_addresses),
                                )
                            )
                        ).all()
                    )
                if len(identity_contacts) == 1:
                    contact = identity_contacts[0]
                else:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail={
                            "code": "WA-SEQUENCE-CONTEXT-MISMATCH",
                            "message": (
                                "A thread não possui um contato WhatsApp canônico "
                                "único para esta sequência."
                            ),
                        },
                    )
            if thread.matter_id is not None and thread.matter_id != matter.id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "WA-SEQUENCE-CONTEXT-MISMATCH",
                        "message": "Thread e caso não coincidem.",
                    },
                )
            if thread.client_id is not None and thread.client_id != matter.client_id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "WA-SEQUENCE-CONTEXT-MISMATCH",
                        "message": "Thread e cliente não coincidem.",
                    },
                )
        if thread_contact is not None and thread_contact.matter_id != matter.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "WA-SEQUENCE-CONTEXT-MISMATCH",
                    "message": "Thread e caso não coincidem.",
                },
            )
        if thread is not None:
            if raw_contact_id and str(raw_contact_id) != str(contact.id):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "WA-SEQUENCE-CONTEXT-MISMATCH",
                        "message": "Thread e contato não coincidem.",
                    },
                )

    stages = list(payload.stages)
    if stages[0].template_id is None and payload.template_id is not None:
        stages[0] = stages[0].model_copy(update={"template_id": payload.template_id})
    if any(stage.template_id is None for stage in stages):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "WA-SEQUENCE-TEMPLATE", "message": "Cada etapa exige templateId."},
        )
    template_ids = [stage.template_id for stage in stages]
    templates = list(
        (
            await db.scalars(
                select(WhatsAppMetaTemplate).where(
                    WhatsAppMetaTemplate.workspace_id == workspace_id,
                    WhatsAppMetaTemplate.id.in_(template_ids),
                    WhatsAppMetaTemplate.removed_at_source_at.is_(None),
                )
            )
        ).all()
    )
    by_id = {template.id: template for template in templates}
    if len(by_id) != len(set(template_ids)):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "WA-SEQUENCE-TEMPLATE-404",
                "message": "Um template não pertence a este workspace.",
            },
        )
    first_template = by_id[stages[0].template_id]
    connection_id = payload.connection_id or first_template.whatsapp_connection_id
    if first_template.whatsapp_connection_id != connection_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "WA-SEQUENCE-CONNECTION",
                "message": "Os templates devem pertencer à mesma conexão oficial.",
            },
        )

    for stage_index, stage in enumerate(stages):
        template = by_id[stage.template_id]
        if template.whatsapp_connection_id != connection_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "WA-SEQUENCE-CONNECTION",
                    "message": "Os templates devem pertencer à mesma conexão oficial.",
                },
            )
        actual_status = str(getattr(template.status, "value", template.status) or "").lower()
        if actual_status not in {"approved", "pending"}:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "WA-SEQUENCE-TEMPLATE-STATUS",
                    "message": "Todos os templates precisam estar aprovados ou pendentes.",
                },
            )
        if _sequence_has_non_body_variables(template):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "WA-SEQUENCE-NON-BODY-BINDING",
                    "message": (
                        f"Etapa {stage_index + 1} possui variável de header ou botão; "
                        "sequências aceitam apenas bindings do body."
                    ),
                },
            )
        if stage_index == 0 and actual_status != "approved":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "WA-SEQUENCE-FIRST-PENDING",
                    "message": "A primeira etapa precisa de um template aprovado pela Meta.",
                },
            )
        if stage.delay_value is not None and stage.delay_unit is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "WA-SEQUENCE-DELAY",
                    "message": "Cada prazo exige unidade em horas ou dias.",
                },
            )
        if stage_index > 0 and (stage.delay_value is None or stage.delay_unit is None):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "WA-SEQUENCE-DELAY",
                    "message": f"Etapa {stage_index + 1} exige prazo positivo em horas ou dias.",
                },
            )
        # Never trust a client-supplied body snapshot.  The synchronized Meta
        # component is the audit/source-of-truth body for every stage; this
        # prevents direct callers from changing text while retaining a valid
        # template binding contract.
        canonical_body = next(
            (
                item.get("text")
                for item in template.components or []
                if isinstance(item, dict) and str(item.get("type", "")).upper() == "BODY"
            ),
            "",
        )
        stage = stage.model_copy(update={"body": canonical_body})
        stages[stage_index] = stage
        if not canonical_body or not str(canonical_body).strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "WA-SEQUENCE-BODY",
                    "message": f"Etapa {stage_index + 1} exige o corpo do template.",
                },
            )
        variable_positions = _sequence_template_variables(template)
        if len(stage.bindings) != len(variable_positions):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "WA-SEQUENCE-BINDINGS",
                    "message": f"Bindings incompletos na etapa {stage_index + 1}.",
                },
            )

    variables = await picker.variables(
        workspace_id,
        contact_id=contact.id,
        matter_id=matter.id,
    )
    resolved_fields = {
        field.field_key: field.resolved_value
        for group in variables.groups
        for field in group.values
    }

    def resolved_stage_values(stage_index: int) -> list[str]:
        stage = stages[stage_index]
        values: list[str] = []
        for binding in stage.bindings:
            field_key = binding.field_key.strip()
            value = resolved_fields.get(field_key)
            if value is None or not str(value).strip():
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "code": "WA-SEQUENCE-BINDING-VALUE",
                        "message": f"O campo {field_key} não possui valor para este contato/caso.",
                    },
                )
            values.append(str(value))
        return values

    first_values = resolved_stage_values(0)
    # Reuse the campaign service's canonical Meta component validation instead
    # of reproducing header/button rules in this endpoint.
    campaign_service._validate_template_parameters(
        first_template,
        header_parameters=[],
        body_parameters=first_values,
        button_parameters=[],
    )

    audience_filter = CampaignAudienceFilter(
        contact_ids=[contact.id],
        include_crm_contacts=True,
        include_unlinked_contacts=True,
        include_blocked=False,
    )
    # Preflight through the same audience gate used by create_campaign before
    # compiling/persisting the Flow. A zero-recipient launch must fail closed;
    # otherwise the picker could report a successful first message that never
    # existed.
    audience_preview = await campaign_service._build_audience_preview(
        workspace_id,
        audience_filter,
    )
    preview_total = int(audience_preview.get("total", 0) or 0)
    preview_eligible = int(audience_preview.get("eligible", 0) or 0)
    if preview_total != 1 or preview_eligible != 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "WA-SEQUENCE-RECIPIENT-COUNT",
                "message": (
                    "A sequência exige exatamente um destinatário elegível; "
                    f"prévia encontrou total={preview_total}, elegíveis={preview_eligible}."
                ),
            },
        )

    # Flow stages start at the second message: stage one is sent exactly once
    # by the campaign recipient and the delivery event starts this flow.
    flow_nodes: list[dict[str, Any]] = [
        {
            "id": "sequence-trigger",
            "type": "trigger",
            "position": {"x": 0, "y": 0},
            "label": "Início da sequência",
            "config": {"event": "whatsapp_sequence", "source": "picker"},
        }
    ]
    flow_edges: list[dict[str, Any]] = []
    previous_id = "sequence-trigger"
    cumulative_delay_hours = 0.0
    for stage_index, stage in enumerate(stages[1:], start=1):
        template = by_id[stage.template_id]
        positions = _sequence_template_variables(template)
        # Validate values now and persist only field-backed bindings.  The
        # worker still keeps the template status snapshot for pending/skip.
        resolved_stage_values(stage_index)
        cumulative_delay_hours += float(stage.delay_value or 0) * (
            24 if stage.delay_unit == "days" else 1
        )
        node_id = f"sequence-stage-{stage_index + 1}"
        bindings = [
            {
                "variable": f"sequence_{position}",
                "fieldKey": binding.field_key.strip(),
                "scope": "body",
            }
            for position, binding in zip(positions, stage.bindings, strict=True)
        ]
        flow_nodes.append(
            {
                "id": node_id,
                "type": "scheduled_message",
                "position": {"x": stage_index * 240, "y": 0},
                "label": f"Etapa {stage_index + 1}",
                "config": {
                    "message": _sequence_body_for_flow(stage.body, positions),
                    "channel": "whatsapp",
                    "sequence_stage": True,
                    "template_id": str(template.id),
                    "template_name": template.name,
                    "template_language": template.language,
                    "whatsapp_connection_id": str(connection_id),
                    "template_status": str(getattr(template.status, "value", template.status)),
                    "bindings": bindings,
                    # The flow worker materializes all linear scheduled nodes
                    # during one traversal. Persist absolute offsets, matching
                    # the FlowBuilder serializer, so mixed hours/days remain
                    # cumulative from the start of the sequence.
                    "delay_value": cumulative_delay_hours,
                    "delay_unit": "hours",
                    "send_now": False,
                    "interrupt_on_reply": payload.interrupt_on_reply,
                },
            }
        )
        flow_edges.append(
            {"id": f"{previous_id}-{node_id}", "source": previous_id, "target": node_id}
        )
        previous_id = node_id
    end_id = "sequence-end"
    flow_nodes.append(
        {
            "id": end_id,
            "type": "end",
            "position": {"x": len(flow_nodes) * 240, "y": 0},
            "label": "Fim da sequência",
            "config": {"end_status": "completed"},
        }
    )
    flow_edges.append({"id": f"{previous_id}-{end_id}", "source": previous_id, "target": end_id})

    from app.services.flow_service import FlowService

    flow = await FlowService(db).create_flow(
        workspace_id,
        FlowCreate(
            name=payload.title or f"Sequência · {first_template.name}",
            description="Sequência iniciada no picker de templates aprovados.",
            practice_area="universal",
            trigger="manual",
            trigger_config={
                "source": "whatsapp_template_picker",
                "sequenceIdempotencyKeyHash": sha256(idempotency_key.encode()).hexdigest(),
                "interruptOnReply": payload.interrupt_on_reply,
            },
            nodes=flow_nodes,
            edges=flow_edges,
            is_active=True,
        ),
        actor_user_id=actor_user_id,
        commit=False,
    )

    campaign_payload = CampaignCreate(
        whatsapp_connection_id=connection_id,
        title=payload.title or f"Sequência · {first_template.name}",
        meta_template_id=first_template.id,
        body_parameters=first_values,
        audience_filter=audience_filter,
        follow_up_flow_id=flow.id,
        flow_start_mode="on_delivery",
    )
    campaign = await campaign_service.create_campaign(
        workspace_id,
        campaign_payload,
        acting_user_id=actor_user_id,
        idempotency_key=idempotency_key,
        idempotency_fingerprint_override=sequence_fingerprint,
    )
    require_single_recipient(campaign)
    if campaign.follow_up_flow_id != flow.id:
        # Another request won the unique campaign idempotency key while this
        # request was compiling its flow.  Do not commit this loser flow.
        await db.rollback()
        existing_campaign = await campaign_service.get_campaign(workspace_id, campaign.id)
        if str(getattr(existing_campaign.status, "value", existing_campaign.status)) == "draft":
            require_single_recipient(existing_campaign)
            launched = await campaign_service.launch_campaign(
                workspace_id,
                campaign.id,
                CampaignLaunchRequest(dry_run=False),
            )
            require_single_recipient(launched)
            await require_first_delivery(launched)
            return launched
        require_single_recipient(existing_campaign)
        await require_first_delivery(existing_campaign)
        return existing_campaign
    launched = await campaign_service.launch_campaign(
        workspace_id,
        campaign.id,
        CampaignLaunchRequest(dry_run=False),
    )
    require_single_recipient(launched)
    await require_first_delivery(launched)
    return launched


def normalize_sequence_delay(config: dict[str, Any]) -> tuple[float | None, str | None]:
    """Return a canonical delay while accepting the legacy ``delay_hours`` key."""
    if config.get("send_now") is True:
        return 0.0, "hours"
    if config.get("delay_value") is not None or config.get("delay_unit") is not None:
        value = config.get("delay_value")
        unit = str(config.get("delay_unit") or "").strip().lower()
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or float(value) < 0
        ):
            raise ValueError("delay_value deve ser um número não negativo")
        if unit not in SEQUENCE_DELAY_UNITS:
            raise ValueError("delay_unit deve ser hours ou days")
        if float(value) == 0 and config.get("send_now") is not True:
            raise ValueError("delay_value zero exige send_now=true")
        return float(value), unit

    # ``delay_hours`` predates sequence stages. Keep it readable and persist
    # the canonical pair for new rows without rewriting the Flow revision.
    if config.get("delay_hours") is not None:
        value = config.get("delay_hours")
        if isinstance(value, bool) or not isinstance(value, int | float) or float(value) <= 0:
            raise ValueError("delay_hours deve ser um número positivo")
        return float(value), "hours"
    return None, None


def sequence_template_status(config: dict[str, Any]) -> str | None:
    value = config.get("template_status")
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized not in SEQUENCE_TEMPLATE_STATUSES:
        raise ValueError("template_status deve ser approved ou pending")
    return normalized


def normalize_sequence_bindings(config: dict[str, Any]) -> list[dict[str, str]] | None:
    raw = config.get("bindings", config.get("variable_bindings"))
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise ValueError("Bindings de sequência devem ser uma lista")
    if not raw:
        return []
    result: list[dict[str, str]] = []
    variables: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("Binding de sequência inválido")
        raw_scope = item.get("scope", item.get("component", "body"))
        if isinstance(raw_scope, dict):
            raw_scope = raw_scope.get("type")
        scope = str(raw_scope or "body").strip().lower()
        if scope != "body":
            raise ValueError(
                "Sequência suporta apenas variáveis do body do template; "
                f"scope '{scope}' exige suporte de componente explícito"
            )
        variable = str(item.get("variable") or item.get("name") or "").strip()
        field_key = str(item.get("fieldKey") or item.get("field_key") or "").strip()
        if not variable or not field_key:
            raise ValueError("Cada binding exige variable e fieldKey")
        if variable in variables:
            raise ValueError(f"Binding de sequência duplicado: {variable}")
        variables.add(variable)
        result.append({"variable": variable, "fieldKey": field_key})
    return result


def resolve_sequence_message(
    template: str,
    config: dict[str, Any],
    context: dict[str, Any],
) -> str:
    """Resolve only explicitly bound fields for a sequence stage."""
    bindings = normalize_sequence_bindings(config)
    if not bindings:
        return template
    resolved: dict[str, Any] = {}
    sentinel = object()
    for binding in bindings:
        value = get_context_value(context, binding["fieldKey"], sentinel)
        if value is sentinel:
            raise ValueError(
                f"Variável '{binding['variable']}' sem valor para fieldKey "
                f"'{binding['fieldKey']}'"
            )
        resolved[binding["variable"]] = value
    placeholders = set(re.findall(r"\{([A-Za-z_][A-Za-z0-9_.]*)\}", template))
    missing = sorted(placeholders - set(resolved))
    if missing:
        raise ValueError(f"Variáveis sem binding na etapa agendada: {', '.join(missing)}")
    for key, value in resolved.items():
        template = template.replace("{" + key + "}", "" if value is None else str(value))
    return template


def resolve_sequence_template_parameters(
    bindings: list[dict[str, str]] | None,
    context: dict[str, Any],
) -> list[str]:
    """Resolve template body parameters in the persisted binding order.

    Meta templates are positional, so the sequence snapshot must carry the
    same order used when the stage was created.  Only explicit ``fieldKey``
    bindings are eligible; this prevents arbitrary context values from being
    sent to the provider.
    """
    if not bindings:
        return []
    sentinel = object()
    values: list[str] = []
    for binding in bindings:
        field_key = str(binding.get("fieldKey") or binding.get("field_key") or "").strip()
        if not field_key:
            raise ValueError("Cada binding de sequência exige fieldKey")
        value = get_context_value(context, field_key, sentinel)
        if value is sentinel or value is None:
            raise ValueError(
                f"Variável sem valor para fieldKey '{field_key}'"
            )
        values.append(str(value))
    return values


def sequence_tariff(config: dict[str, Any]) -> dict[str, Any]:
    """Expose tariff uncertainty without manufacturing a price.

    A tariff is verified only when the stage carries an explicit numeric
    amount. Missing, malformed or provider-derived values remain unverified.
    """
    raw = config.get("tariff")
    if isinstance(raw, dict):
        amount = raw.get("amount")
    else:
        amount = raw
    if isinstance(amount, bool) or not isinstance(amount, int | float) or float(amount) < 0:
        return {"status": "unverified", "amount": None}
    return {"status": "verified", "amount": float(amount)}


async def interrupt_sequences_on_inbound(
    db,
    *,
    workspace_id: UUID,
    phone: str | None,
    inbound_message_id: UUID | None = None,
) -> int:
    """Cancel reply-interruptible sequence runs when a contact replies."""
    if not phone:
        return 0
    variants = phone_number_lookup_variants(phone)
    if not variants:
        return 0
    rows = await db.execute(
        select(ScheduledMessage, FlowRun)
        .join(FlowRun, FlowRun.id == ScheduledMessage.flow_run_id)
        .where(
            ScheduledMessage.workspace_id == workspace_id,
            ScheduledMessage.contact_phone.in_(variants),
            ScheduledMessage.interrupt_on_reply.is_(True),
            ScheduledMessage.status.in_(["pending", "processing"]),
            # A FlowRun may have reached its terminal checkpoint while later
            # sequence rows remain queued; the queued row is the authority for
            # whether a reply can still interrupt delivery.
            FlowRun.status.in_(["active", "paused", "waiting_human", "completed"]),
        )
        .with_for_update()
    )
    interrupted = 0
    now = datetime.now(UTC)
    for message, run in rows.all():
        message.status = "cancelled"
        message.lease_token = None
        message.lease_expires_at = None
        message.error_message = "Sequência interrompida por resposta inbound."
        run.status = "cancelled"
        run.completed_at = now
        run.paused_at = None
        run.pause_reason = "Sequência interrompida por resposta inbound."
        context = dict(run.context or {})
        context["sequenceInterrupted"] = {
            "at": now.isoformat(),
            "inboundMessageId": str(inbound_message_id) if inbound_message_id else None,
            "stageId": message.stage_id,
        }
        run.context = context
        correlation_id = (
            "flow-sequence:inbound-interrupt:"
            f"{message.id}:{inbound_message_id or 'unknown'}"
        )
        existing = await db.scalar(
            select(KlmAuditEvent.id).where(
                KlmAuditEvent.workspace_id == workspace_id,
                KlmAuditEvent.correlation_id == correlation_id,
            )
        )
        if existing is None:
            db.add(
                KlmAuditEvent(
                    workspace_id=workspace_id,
                    actor_user_id=None,
                    actor_type="system",
                    actor_label="Sequência de mensagens",
                    event_type="flow_sequence_interrupted_on_inbound",
                    entity_type="scheduled_message",
                    entity_id=message.id,
                    correlation_id=correlation_id,
                    summary="Etapa agendada interrompida porque o contato respondeu.",
                    after_data={"flowRunId": str(run.id), "stageId": message.stage_id},
                )
            )
        interrupted += 1
    if interrupted:
        await db.flush()
    return interrupted


async def hold_or_skip_pending_template(
    db,
    message: ScheduledMessage,
    run: FlowRun | None,
    *,
    now: datetime | None = None,
) -> str:
    """Hold a pending template briefly, then skip it with durable evidence."""
    current = now or datetime.now(UTC)
    if message.pending_grace_until is None:
        message.pending_grace_until = current + SEQUENCE_PENDING_TEMPLATE_GRACE
        message.pending_retry_at = current + timedelta(minutes=1)
        message.status = "pending"
        message.lease_token = None
        message.lease_expires_at = None
        return "held"
    grace_until = message.pending_grace_until
    if grace_until.tzinfo is None:
        grace_until = grace_until.replace(tzinfo=UTC)
    if grace_until > current:
        message.status = "pending"
        message.pending_retry_at = min(
            grace_until,
            current + timedelta(minutes=1),
        )
        message.lease_token = None
        message.lease_expires_at = None
        return "held"

    message.status = "skipped"
    message.pending_retry_at = None
    message.error_message = "Template permaneceu pendente após a tolerância da etapa."
    message.lease_token = None
    message.lease_expires_at = None
    if run is not None:
        context = dict(run.context or {})
        skipped = dict(context.get("sequenceSkippedStages") or {})
        skipped[str(message.stage_id or message.id)] = {
            "reason": "template_pending_after_grace",
            "templateName": message.template_name,
            "at": current.isoformat(),
        }
        context["sequenceSkippedStages"] = skipped
        run.context = context

    correlation_id = f"flow-sequence:pending-template-skipped:{message.id}"
    existing = await db.scalar(
        select(KlmAuditEvent.id).where(
            KlmAuditEvent.workspace_id == message.workspace_id,
            KlmAuditEvent.correlation_id == correlation_id,
        )
    )
    if existing is None:
        db.add(
            KlmAuditEvent(
                workspace_id=message.workspace_id,
                actor_user_id=None,
                actor_type="system",
                actor_label="Sequência de mensagens",
                event_type="flow_sequence_stage_skipped_template_pending",
                entity_type="scheduled_message",
                entity_id=message.id,
                correlation_id=correlation_id,
                summary="Etapa pulada: o template não foi aprovado dentro da tolerância.",
                after_data={
                    "flowRunId": str(run.id) if run else None,
                    "stageId": message.stage_id,
                    "templateName": message.template_name,
                    "templateStatus": "pending",
                    "tariffStatus": message.tariff_status or "unverified",
                },
            )
        )
    # The audit event above is unconditional and is the source of truth. Route
    # the companion notification to the case owner, sequence initiator, or a
    # durable workspace administrator fallback.
    if run is not None:
        from app.models.matter import Matter
        from app.models.notification import Notification, NotificationType
        from app.models.workspace_membership import MembershipStatus, WorkspaceMembership
        from app.services.notification_service import NotificationService

        owner_id = await db.scalar(
            select(Matter.responsible_human_id).where(
                Matter.id == run.matter_id,
                Matter.workspace_id == message.workspace_id,
            )
        )
        if owner_id is None:
            # A sequence started from the picker still has a durable human
            # initiator in the recipient/FlowRun context.  Use that user when
            # the case has no responsible owner; this keeps the skip visible
            # without inventing a workspace-wide recipient.
            raw_initiator = dict(run.context or {}).get("sequenceInitiatorUserId")
            try:
                candidate = UUID(str(raw_initiator)) if raw_initiator else None
            except (TypeError, ValueError):
                candidate = None
            if candidate is not None:
                owner_id = await db.scalar(
                    select(WorkspaceMembership.user_id).where(
                        WorkspaceMembership.workspace_id == message.workspace_id,
                        WorkspaceMembership.user_id == candidate,
                        WorkspaceMembership.status == MembershipStatus.active,
                    )
                )
        if owner_id is None:
            owner_id = await db.scalar(
                select(WorkspaceMembership.user_id)
                .where(
                    WorkspaceMembership.workspace_id == message.workspace_id,
                    WorkspaceMembership.status == MembershipStatus.active,
                    WorkspaceMembership.role.in_(
                        ["workspace_owner", "workspace_admin", "legal_manager"]
                    ),
                )
                .order_by(WorkspaceMembership.created_at.asc())
                .limit(1)
            )
        if owner_id:
            stage_key = str(message.id)
            title = f"Etapa de sequência pulada · {stage_key}"
            body = (
                "Uma etapa foi pulada porque o template permaneceu pendente após a "
                f"tolerância (etapa {stage_key})."
            )
            already_notified = await db.scalar(
                select(Notification.id).where(
                    Notification.user_id == owner_id,
                    Notification.workspace_id == message.workspace_id,
                    Notification.matter_id == run.matter_id,
                    Notification.title == title,
                    Notification.message == body,
                    Notification.is_read.is_(False),
                )
            )
            if already_notified is not None:
                await db.flush()
                return "skipped"
            await NotificationService(db).create(
                owner_id,
                title,
                body,
                NotificationType.warning,
                matter_id=run.matter_id,
                workspace_id=message.workspace_id,
            )
    await db.flush()
    return "skipped"
