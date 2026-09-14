"""Provider payload adapters for the native KLM lead-capture boundary."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.models.omnichannel_v6 import OmnichannelChannelConnection
from app.schemas.omnichannel_v6 import LeadCaptureIngest

_SECRET_KEY = re.compile(
    r"(?:access|refresh|api|private|client)?[_-]?(?:token|secret|password|key|signature|cookie)$",
    re.IGNORECASE,
)
_CLICK_ID_KEYS = frozenset(
    {"fbclid", "gclid", "gbraid", "wbraid", "ttclid", "msclkid", "utm_id"}
)


def _canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if _SECRET_KEY.search(str(key)) else _scrub(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return value


def _normalized_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def _value_at(source: dict[str, Any], dotted_path: str | None) -> Any:
    if not dotted_path:
        return None
    current: Any = source
    for segment in dotted_path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(segment)
    return current


def _first(source: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = source.get(key)
        if value is not None and value != "":
            return value
    return None


def _parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, int | float):
        return datetime.fromtimestamp(float(value), tz=UTC)
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _provider_references(envelope: dict[str, Any]) -> dict[str, str]:
    """Return only stable, non-secret provider identifiers used for attribution."""

    references = {
        "accountId": _first(
            envelope,
            "account_id",
            "accountId",
            "advertiser_id",
            "advertiserId",
        ),
        "campaignId": _first(envelope, "campaign_id", "campaignId"),
        "adSetId": _first(
            envelope,
            "adset_id",
            "adSetId",
            "adgroup_id",
            "adgroupId",
        ),
        "adId": _first(envelope, "ad_id", "adId"),
        "creativeId": _first(envelope, "creative_id", "creativeId"),
        "formId": _first(envelope, "form_id", "formId", "instant_form_id"),
        "pageId": _first(envelope, "page_id", "pageId"),
    }
    return {
        key: str(value)
        for key, value in references.items()
        if isinstance(value, str | int) and str(value).strip()
    }


def _flatten_field_data(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {_normalized_key(str(key)): item for key, item in value.items()}
    flattened: dict[str, Any] = {}
    if not isinstance(value, list):
        return flattened
    for item in value:
        if not isinstance(item, dict):
            continue
        key = _first(item, "name", "key", "field", "field_name", "question")
        if not key:
            continue
        raw = _first(item, "values", "value", "answer", "answers")
        if isinstance(raw, list):
            raw = raw[0] if len(raw) == 1 else raw
        flattened[_normalized_key(str(key))] = raw
    return flattened


def _meta_payload(raw: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    envelope: dict[str, Any] = {}
    for entry in raw.get("entry", []) if isinstance(raw.get("entry"), list) else []:
        for change in entry.get("changes", []) if isinstance(entry, dict) else []:
            if isinstance(change, dict) and change.get("field") == "leadgen":
                envelope.update(change.get("value") or {})
    detail = raw.get("lead") if isinstance(raw.get("lead"), dict) else raw
    envelope.update({key: value for key, value in detail.items() if value is not None})
    fields = _flatten_field_data(
        _first(envelope, "field_data", "fields", "answers", "form_data")
    )
    return envelope, fields


def _tiktok_payload(raw: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    envelope = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    lead = envelope.get("lead") if isinstance(envelope.get("lead"), dict) else envelope
    fields = _flatten_field_data(
        _first(lead, "field_data", "fields", "answers", "lead_data", "form_data")
    )
    return lead, fields


@dataclass(frozen=True)
class LeadCaptureInspection:
    """Safe first-pass result for full forms and provider locator notifications."""

    provider: str
    external_lead_id: str
    external_form_id: str | None
    raw_payload_digest: str
    locator_payload: dict[str, Any]
    normalized: LeadCaptureIngest | None

    @property
    def requires_hydration(self) -> bool:
        return self.normalized is None


class LeadCaptureAdapterService:
    """Normalize provider-specific lead forms without calling provider APIs."""

    @staticmethod
    def inspect(
        connection: OmnichannelChannelConnection,
        raw_payload: dict[str, Any],
        *,
        proactive_message_template: str | None = None,
    ) -> LeadCaptureInspection:
        if connection.provider == "meta_ads":
            envelope, fields = _meta_payload(raw_payload)
        elif connection.provider == "tiktok_ads":
            envelope, fields = _tiktok_payload(raw_payload)
        else:
            envelope = (
                raw_payload.get("data")
                if isinstance(raw_payload.get("data"), dict)
                else raw_payload
            )
            fields = _flatten_field_data(
                _first(envelope, "fields", "answers", "form_data", "formData")
            )
            direct_fields = {
                _normalized_key(str(key)): value for key, value in envelope.items()
            }
            fields = {**direct_fields, **fields}

        configuration = connection.configuration or {}
        mapping = configuration.get("fieldMapping") or {}

        def mapped(name: str, *aliases: str) -> Any:
            mapped_path = mapping.get(name)
            mapped_value = _value_at(raw_payload, str(mapped_path)) if mapped_path else None
            if mapped_value is not None and mapped_value != "":
                return mapped_value
            return _first(fields, *aliases)

        name = mapped("name", "full_name", "name", "nome", "nome_completo")
        phone = mapped(
            "phone", "phone_number", "phone", "telefone", "whatsapp", "numero_de_telefone"
        )
        email = mapped("email", "email", "e_mail", "endereco_de_email")
        external_id = _first(
            envelope,
            "leadgen_id",
            "lead_id",
            "leadId",
            "id",
            "submission_id",
            "submissionId",
            "event_id",
        )
        payload_digest = _canonical_digest(raw_payload)
        external_id = str(external_id or f"digest:{payload_digest}")
        form_id = _first(envelope, "form_id", "formId", "instant_form_id", "form")
        form_id_value = str(form_id) if form_id else None
        provider_references = _provider_references(envelope)
        if form_id_value and "formId" not in provider_references:
            provider_references["formId"] = form_id_value
        locator_references = {
            key: value for key, value in provider_references.items() if key != "formId"
        }
        if not phone and not email:
            if connection.provider not in {"meta_ads", "tiktok_ads"}:
                raise ValueError("O lead precisa ter telefone ou e-mail para deduplicação.")
            if external_id.startswith("digest:"):
                raise ValueError(
                    "A notificação do provedor não possui um identificador de lead hidratável."
                )
            locator_payload = {
                "externalLeadId": external_id,
                "externalFormId": form_id_value,
                **locator_references,
            }
            return LeadCaptureInspection(
                provider=connection.provider,
                external_lead_id=external_id,
                external_form_id=form_id_value,
                raw_payload_digest=payload_digest,
                locator_payload={
                    key: str(value)
                    for key, value in locator_payload.items()
                    if value is not None and value != ""
                },
                normalized=None,
            )

        if not name:
            name = str(email).split("@", 1)[0] if email else f"Lead {str(phone or '')[-4:]}"

        campaign = _first(
            envelope, "campaign_name", "campaign", "campaign_id", "campaignId"
        )
        ad_set = _first(
            envelope, "adset_name", "ad_set", "adgroup_name", "adgroup_id", "adset_id"
        )
        ad = _first(envelope, "ad_name", "ad", "ad_id", "creative_name", "creative_id")
        source = _first(envelope, "source", "source_name", "platform") or connection.display_name
        click_ids = {
            key: str(value)
            for key, value in {**raw_payload, **envelope, **fields}.items()
            if _normalized_key(str(key)) in _CLICK_ID_KEYS
            and value is not None
            and value != ""
        }
        received_at = _parse_datetime(
            _first(
                envelope,
                "created_time",
                "create_time",
                "created_at",
                "submitted_at",
                "timestamp",
            )
        )

        health_subtheme = mapped(
            "healthSubtheme", "health_subtheme", "subtema", "tipo_de_negativa"
        )
        health_object_name = mapped(
            "healthObjectName",
            "health_object_name",
            "medicamento",
            "tratamento",
            "procedimento",
        )
        demand_type = mapped("demandType", "demand_type", "demanda", "assunto")
        insurer = mapped("healthInsurer", "health_insurer", "plano_de_saude", "operadora")
        denial_reason = mapped(
            "healthDenialReason", "health_denial_reason", "motivo_da_negativa", "negativa"
        )
        urgency = mapped(
            "medicalUrgencyHours", "medical_urgency_hours", "urgencia_horas", "prazo_horas"
        )
        try:
            urgency_hours = int(urgency) if urgency not in {None, ""} else None
        except (TypeError, ValueError):
            urgency_hours = None

        normalized = LeadCaptureIngest(
            provider=connection.provider,
            connection_key=connection.connection_key,
            external_lead_id=external_id,
            external_form_id=form_id_value,
            channel=connection.channel,
            name=str(name),
            phone=str(phone) if phone else None,
            email=str(email) if email else None,
            demand_type=str(demand_type or "Triagem de negativa de plano de saúde"),
            health_subtheme=str(health_subtheme) if health_subtheme else None,
            health_object_name=str(health_object_name) if health_object_name else None,
            health_insurer=str(insurer) if insurer else None,
            health_denial_reason=str(denial_reason) if denial_reason else None,
            medical_urgency_hours=urgency_hours,
            source=str(source),
            campaign=str(campaign) if campaign else None,
            ad_set=str(ad_set) if ad_set else None,
            ad=str(ad) if ad else None,
            provider_references=provider_references,
            click_ids=click_ids,
            fields={
                "providerFields": _scrub(fields),
                "providerReferences": provider_references,
                "mappingVersion": str(configuration.get("mappingVersion") or "v6"),
                "rawPayloadDigest": payload_digest,
            },
            received_at=received_at,
            proactive_message_template=(
                proactive_message_template
                or configuration.get("proactiveMessageTemplate")
                or None
            ),
        )
        return LeadCaptureInspection(
            provider=connection.provider,
            external_lead_id=external_id,
            external_form_id=form_id_value,
            raw_payload_digest=payload_digest,
            locator_payload={
                "externalLeadId": external_id,
                **({"externalFormId": form_id_value} if form_id_value else {}),
                **locator_references,
            },
            normalized=normalized,
        )

    @staticmethod
    def inspect_many(
        connection: OmnichannelChannelConnection,
        raw_payload: dict[str, Any],
        *,
        proactive_message_template: str | None = None,
    ) -> list[LeadCaptureInspection]:
        """Split batched Meta leadgen callbacks while preserving the signed envelope."""

        if connection.provider != "meta_ads":
            return [
                LeadCaptureAdapterService.inspect(
                    connection,
                    raw_payload,
                    proactive_message_template=proactive_message_template,
                )
            ]

        inspections: list[LeadCaptureInspection] = []
        entries = raw_payload.get("entry")
        if isinstance(entries, list):
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                changes = entry.get("changes")
                if not isinstance(changes, list):
                    continue
                for change in changes:
                    if not isinstance(change, dict) or change.get("field") != "leadgen":
                        continue
                    isolated = {
                        "object": raw_payload.get("object"),
                        "entry": [
                            {
                                key: value
                                for key, value in entry.items()
                                if key != "changes"
                            }
                            | {"changes": [change]},
                        ],
                    }
                    inspections.append(
                        LeadCaptureAdapterService.inspect(
                            connection,
                            isolated,
                            proactive_message_template=proactive_message_template,
                        )
                    )
        if inspections:
            return inspections
        return [
            LeadCaptureAdapterService.inspect(
                connection,
                raw_payload,
                proactive_message_template=proactive_message_template,
            )
        ]

    @staticmethod
    def normalize(
        connection: OmnichannelChannelConnection,
        raw_payload: dict[str, Any],
        *,
        proactive_message_template: str | None = None,
    ) -> LeadCaptureIngest:
        inspection = LeadCaptureAdapterService.inspect(
            connection,
            raw_payload,
            proactive_message_template=proactive_message_template,
        )
        if inspection.normalized is None:
            raise ValueError(
                "A notificação contém apenas o identificador do lead e exige hidratação."
            )
        return inspection.normalized
