from __future__ import annotations

import copy
import time
from collections.abc import Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any, BinaryIO

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    stop_after_delay,
    wait_exponential,
)

from app.core.config import settings
from app.core.klm_runtime import external_delivery_allowed
from app.core.logging import logger
from app.services.whatsapp.errors import WhatsAppProviderError
from app.services.whatsapp.helpers import normalize_phone_number


@dataclass(frozen=True)
class MetaBusinessTokenGrant:
    """Token returned by Embedded Signup plus its provider-declared lifetime."""

    access_token: str
    expires_at: datetime | None


_CONNECT_PHASE_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
_CONNECT_RETRY_ATTEMPTS = 3
_RETRY_AFTER_CAP_SECONDS = 30.0


def _parse_retry_after(value: str | None) -> float | None:
    """`Retry-After` em segundos ou data HTTP; teto de 30 s. A Meta não documenta
    o header para o WhatsApp (o sinal documentado é o `X-Business-Use-Case-Usage`),
    então isto é informativo para chamadores avulsos, nunca a base da decisão."""
    if not value:
        return None
    raw = value.strip()
    try:
        seconds = float(raw)
    except ValueError:
        try:
            when = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        seconds = (when - datetime.now(UTC)).total_seconds()
    return max(0.0, min(seconds, _RETRY_AFTER_CAP_SECONDS))


class MetaCloudClient:
    def __init__(self) -> None:
        self._base_url = f"https://graph.facebook.com/{settings.META_GRAPH_API_VERSION}"
        self._pooled_client: httpx.AsyncClient | None = None
        self._pool_depth = 0

    @staticmethod
    def _timeout() -> httpx.Timeout:
        return httpx.Timeout(connect=3.0, read=20.0, write=20.0, pool=2.0)

    @staticmethod
    def _limits() -> httpx.Limits:
        return httpx.Limits(max_connections=20, max_keepalive_connections=10, keepalive_expiry=30.0)

    @asynccontextmanager
    async def request_scope(self):
        """Reuse one TLS connection pool across a single Meta operation."""
        self._pool_depth += 1
        if self._pooled_client is None:
            self._pooled_client = httpx.AsyncClient(
                timeout=self._timeout(),
                limits=self._limits(),
            )
        try:
            yield self
        finally:
            self._pool_depth -= 1
            if self._pool_depth == 0 and self._pooled_client is not None:
                client = self._pooled_client
                self._pooled_client = None
                await client.aclose()

    async def exchange_code_for_business_token(self, code: str) -> MetaBusinessTokenGrant:
        app_secret = settings.META_APP_SECRET or settings.WHATSAPP_APP_SECRET
        if not settings.META_APP_ID or not app_secret:
            raise WhatsAppProviderError(
                code="WA-010",
                message="META_APP_ID e META_APP_SECRET são obrigatórios para o Tech Provider.",
                status_code=503,
            )
        data = await self._request(
            "GET",
            "/oauth/access_token",
            params={
                "client_id": settings.META_APP_ID,
                "client_secret": app_secret,
                "code": code,
            },
        )
        token = data.get("access_token")
        if not token:
            raise WhatsAppProviderError(
                code="WA-011",
                message="A Meta não retornou business token para o code informado.",
                status_code=400,
            )
        expires_at: datetime | None = None
        expires_in = self._positive_int(data.get("expires_in"))
        provider_expires_at = self._positive_int(data.get("expires_at"))
        if expires_in is not None:
            expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)
        elif provider_expires_at is not None:
            expires_at = datetime.fromtimestamp(provider_expires_at, tz=UTC)
        return MetaBusinessTokenGrant(access_token=str(token), expires_at=expires_at)

    @staticmethod
    def _positive_int(value: object) -> int | None:
        try:
            parsed = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    async def resolve_waba_id_from_token(self, business_token: str) -> str | None:
        app_secret = settings.META_APP_SECRET or settings.WHATSAPP_APP_SECRET
        if not settings.META_APP_ID or not app_secret:
            return None
        data = await self._request(
            "GET",
            "/debug_token",
            params={
                "input_token": business_token,
                "access_token": f"{settings.META_APP_ID}|{app_secret}",
            },
        )
        granular = data.get("data", {}).get("granular_scopes", [])
        for scope_item in granular:
            if scope_item.get("scope") == "whatsapp_business_management":
                target_ids = scope_item.get("target_ids", [])
                if target_ids:
                    return str(target_ids[0])
        return None

    async def get_waba_phone_numbers(self, waba_id: str, access_token: str) -> list[dict[str, Any]]:
        data = await self._request(
            "GET",
            f"/{waba_id}/phone_numbers",
            access_token=access_token,
            params={"fields": "id,display_phone_number,verified_name"},
        )
        return data.get("data", [])

    async def get_message_templates(self, waba_id: str, access_token: str) -> dict[str, Any]:
        """Return the complete WABA template collection.

        Reconciliation may only interpret absence after every provider page was
        read. Returning the first 200 rows would incorrectly classify older
        templates as removed from Meta.
        """
        items: list[dict[str, Any]] = []
        after: str | None = None
        seen_cursors: set[str] = set()

        async with self.request_scope():
            for _page in range(100):
                params: dict[str, Any] = {"limit": 200}
                if after:
                    params["after"] = after
                payload = await self._request(
                    "GET",
                    f"/{waba_id}/message_templates",
                    access_token=access_token,
                    params=params,
                )
                page_items = payload.get("data", [])
                if not isinstance(page_items, list):
                    raise WhatsAppProviderError(
                        code="WA-TPL-SYNC-INCOMPLETE",
                        message="A Meta retornou uma página de templates inválida.",
                        status_code=502,
                        request_started=True,
                        delivery_outcome_known=False,
                    )
                items.extend(item for item in page_items if isinstance(item, dict))

                paging = payload.get("paging") or {}
                if not paging.get("next"):
                    return {"data": items}
                next_after = (paging.get("cursors") or {}).get("after")
                if not next_after or next_after in seen_cursors:
                    raise WhatsAppProviderError(
                        code="WA-TPL-SYNC-INCOMPLETE",
                        message="A paginação de templates da Meta não pôde ser concluída.",
                        status_code=502,
                        request_started=True,
                        delivery_outcome_known=False,
                    )
                seen_cursors.add(next_after)
                after = str(next_after)

        raise WhatsAppProviderError(
            code="WA-TPL-SYNC-INCOMPLETE",
            message="A WABA excedeu o limite seguro de páginas para sincronização.",
            status_code=502,
            request_started=True,
            delivery_outcome_known=False,
        )

    async def create_message_template(
        self,
        waba_id: str,
        access_token: str,
        *,
        name: str,
        language: str,
        category: str,
        components: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Ponto de extensão da submissão real de template à Business Management API.

        Só é alcançado quando o gate `external_delivery_allowed()` está aberto
        (verificado em `_request`) e o serviço de template management decide
        submeter de fato. Com o gate fechado, o serviço nunca chama este método:
        ele marca o rascunho como "submission_prepared" localmente.
        """
        return await self._request(
            "POST",
            f"/{waba_id}/message_templates",
            access_token=access_token,
            json={
                "name": name,
                "language": language,
                "category": category,
                "components": components,
            },
        )

    async def get_phone_number_details(
        self, phone_number_id: str, access_token: str
    ) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"/{phone_number_id}",
            access_token=access_token,
            params={
                "fields": (
                    "display_phone_number,verified_name,quality_rating,"
                    "messaging_limit_tier"
                )
            },
        )

    async def subscribe_app(self, waba_id: str, access_token: str) -> None:
        await self._request(
            "POST",
            f"/{waba_id}/subscribed_apps",
            access_token=access_token,
        )

    async def request_business_app_sync(
        self,
        phone_number_id: str,
        access_token: str,
        *,
        sync_type: str,
    ) -> str:
        if sync_type not in {"smb_app_state_sync", "history"}:
            raise ValueError("Tipo de sincronização de coexistência inválido.")
        data = await self._request(
            "POST",
            f"/{phone_number_id}/smb_app_data",
            access_token=access_token,
            json={"messaging_product": "whatsapp", "sync_type": sync_type},
        )
        request_id = str(data.get("request_id") or "").strip()
        if not request_id:
            raise WhatsAppProviderError(
                code="WA-COEX-001",
                message=(
                    "A Meta aceitou a conexão, mas não retornou o identificador "
                    f"da sincronização {sync_type}."
                ),
                status_code=502,
            )
        return request_id

    async def register_phone_number(
        self, phone_number_id: str, access_token: str, pin: str
    ) -> None:
        await self._request(
            "POST",
            f"/{phone_number_id}/register",
            access_token=access_token,
            json={"messaging_product": "whatsapp", "pin": pin},
        )

    async def send_text_message(
        self,
        *,
        phone_number_id: str,
        access_token: str,
        to: str,
        body: str,
    ) -> dict[str, Any]:
        return await self.send_message_payload(
            phone_number_id=phone_number_id,
            access_token=access_token,
            to=to,
            message={
                "type": "text",
                "text": {"preview_url": False, "body": body},
            },
        )

    async def send_message_payload(
        self,
        *,
        phone_number_id: str,
        access_token: str,
        to: str,
        message: dict[str, Any],
        opaque_callback_data: str | None = None,
    ) -> dict[str, Any]:
        recipient = normalize_phone_number(to)
        if recipient is None:
            raise WhatsAppProviderError(
                code="WA-PHONE-001",
                message=(
                    "Telefone inválido. Informe DDI, DDD e número; "
                    "por exemplo 5561999999999."
                ),
                status_code=422,
            )
        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient,
            **message,
        }
        if opaque_callback_data:
            payload["biz_opaque_callback_data"] = opaque_callback_data[:512]
        return await self._request(
            "POST",
            f"/{phone_number_id}/messages",
            access_token=access_token,
            json=payload,
        )

    @staticmethod
    def _group_payload_error(message: str) -> WhatsAppProviderError:
        return WhatsAppProviderError(
            code="WA-GROUP-MESSAGE-UNSUPPORTED",
            message=message,
            status_code=422,
        )

    @classmethod
    def build_group_message_payload(
        cls,
        *,
        group_id: str,
        message: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Build a strict official group payload without performing network I/O."""
        normalized_group_id = str(group_id or "").strip()
        if not normalized_group_id:
            raise cls._group_payload_error("O GROUP_ID é obrigatório para mensagens em grupo.")
        if not isinstance(message, Mapping):
            raise cls._group_payload_error("A mensagem de grupo deve ser um objeto.")

        candidate = copy.deepcopy(dict(message))
        unsupported_keys = {
            "authentication",
            "auth",
            "catalog",
            "catalog_id",
            "commerce",
            "context",
            "disappearing",
            "ephemeral",
            "interactive",
            "order",
            "otp",
            "product",
            "product_list",
            "quoted_message",
            "reply_to",
            "view_once",
        }

        def inspect_keys(value: Any) -> None:
            if isinstance(value, Mapping):
                for key, nested in value.items():
                    normalized_key = str(key).strip().casefold().replace("-", "_")
                    if normalized_key in unsupported_keys:
                        raise cls._group_payload_error(
                            f"O campo '{key}' não é suportado em mensagens de grupo."
                        )
                    inspect_keys(nested)
            elif isinstance(value, list):
                for nested in value:
                    inspect_keys(nested)

        inspect_keys(candidate)
        message_type = str(candidate.get("type") or "").strip().casefold()
        supported_types = {"text", "image", "video", "audio", "document", "template"}
        if message_type not in supported_types:
            raise cls._group_payload_error(
                "Apenas texto, mídia e modelos de texto/mídia são suportados em grupos."
            )

        provider_message: dict[str, Any]
        if message_type == "text":
            if set(candidate) != {"type", "text"} or not isinstance(
                candidate.get("text"), Mapping
            ):
                raise cls._group_payload_error("O payload de texto de grupo é inválido.")
            text_payload = dict(candidate["text"])
            if not set(text_payload) <= {"body", "preview_url"}:
                raise cls._group_payload_error("O payload de texto contém campos não permitidos.")
            body = str(text_payload.get("body") or "").strip()
            if not body:
                raise cls._group_payload_error("O texto da mensagem de grupo é obrigatório.")
            provider_message = {
                "type": "text",
                "text": {
                    "preview_url": bool(text_payload.get("preview_url", False)),
                    "body": body,
                },
            }
        elif message_type in {"image", "video", "audio", "document"}:
            if set(candidate) != {"type", message_type} or not isinstance(
                candidate.get(message_type), Mapping
            ):
                raise cls._group_payload_error("O payload de mídia de grupo é inválido.")
            media_payload = dict(candidate[message_type])
            allowed_media_keys = {"id", "link"}
            if message_type in {"image", "video", "document"}:
                allowed_media_keys.add("caption")
            if message_type == "document":
                allowed_media_keys.add("filename")
            if not set(media_payload) <= allowed_media_keys:
                raise cls._group_payload_error("A mídia contém campos não permitidos.")
            has_id = bool(str(media_payload.get("id") or "").strip())
            has_link = bool(str(media_payload.get("link") or "").strip())
            if has_id == has_link:
                raise cls._group_payload_error(
                    "A mídia exige exatamente um identificador Meta ou link HTTPS."
                )
            if has_link and not str(media_payload["link"]).startswith("https://"):
                raise cls._group_payload_error("Links de mídia de grupo devem usar HTTPS.")
            provider_message = {"type": message_type, message_type: media_payload}
        else:
            allowed_template_keys = {
                "type",
                "template",
                "template_category",
                "template_format",
            }
            if set(candidate) != allowed_template_keys or not isinstance(
                candidate.get("template"), Mapping
            ):
                raise cls._group_payload_error(
                    "Modelos de grupo exigem categoria e formato internos explícitos."
                )
            category = str(candidate["template_category"]).strip().casefold()
            template_format = str(candidate["template_format"]).strip().casefold()
            if category not in {"marketing", "utility"}:
                raise cls._group_payload_error(
                    "Modelos de autenticação não são suportados em grupos."
                )
            if template_format not in {"text", "media"}:
                raise cls._group_payload_error(
                    "Somente modelos de texto ou mídia são suportados em grupos."
                )
            template = dict(candidate["template"])
            if not set(template) <= {"name", "language", "components"}:
                raise cls._group_payload_error("O modelo contém campos não permitidos.")
            if not str(template.get("name") or "").strip():
                raise cls._group_payload_error("O nome do modelo é obrigatório.")
            language = template.get("language")
            if not isinstance(language, Mapping) or set(language) != {"code"}:
                raise cls._group_payload_error("O idioma do modelo é obrigatório.")
            components = template.get("components", [])
            if not isinstance(components, list):
                raise cls._group_payload_error("Os componentes do modelo devem ser uma lista.")
            if any(
                not isinstance(component, Mapping)
                or component.get("type") not in {"header", "body"}
                for component in components
            ):
                raise cls._group_payload_error(
                    "Botões e componentes interativos não são suportados em grupos."
                )
            provider_message = {"type": "template", "template": template}

        return {
            "messaging_product": "whatsapp",
            "recipient_type": "group",
            "to": normalized_group_id,
            **provider_message,
        }

    async def send_group_message_payload(
        self,
        *,
        phone_number_id: str,
        access_token: str,
        group_id: str,
        message: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Send an already gated group message using the official messages edge."""
        payload = self.build_group_message_payload(group_id=group_id, message=message)
        return await self._request(
            "POST",
            f"/{phone_number_id}/messages",
            access_token=access_token,
            json=payload,
        )

    @classmethod
    def build_group_creation_payload(
        cls,
        *,
        subject: str,
        description: str | None = None,
        join_approval_mode: str = "auto_approve",
    ) -> dict[str, Any]:
        """Build an official group-creation request without network I/O."""
        normalized_subject = str(subject or "").strip()
        if not normalized_subject or len(normalized_subject) > 128:
            raise cls._group_payload_error(
                "O assunto do grupo deve ter entre 1 e 128 caracteres."
            )
        normalized_description = str(description or "").strip() or None
        if normalized_description and len(normalized_description) > 2048:
            raise cls._group_payload_error(
                "A descrição do grupo deve ter no máximo 2048 caracteres."
            )
        if join_approval_mode not in {"auto_approve", "approval_required"}:
            raise cls._group_payload_error(
                "O modo de aprovação do grupo não é suportado."
            )
        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "subject": normalized_subject,
            "join_approval_mode": join_approval_mode,
        }
        if normalized_description:
            payload["description"] = normalized_description
        return payload

    async def create_group(
        self,
        *,
        phone_number_id: str,
        access_token: str,
        subject: str,
        description: str | None = None,
        join_approval_mode: str = "auto_approve",
    ) -> dict[str, Any]:
        payload = self.build_group_creation_payload(
            subject=subject,
            description=description,
            join_approval_mode=join_approval_mode,
        )
        return await self._request(
            "POST",
            f"/{phone_number_id}/groups",
            access_token=access_token,
            json=payload,
        )

    async def get_active_groups(
        self,
        *,
        phone_number_id: str,
        access_token: str,
        limit: int = 100,
        after: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": min(max(limit, 1), 1000)}
        if after:
            params["after"] = after
        return await self._request(
            "GET",
            f"/{phone_number_id}/groups",
            access_token=access_token,
            params=params,
        )

    async def get_group_info(
        self,
        *,
        group_id: str,
        access_token: str,
    ) -> dict[str, Any]:
        return await self._request("GET", f"/{group_id}", access_token=access_token)

    async def get_group_invite_link(
        self,
        *,
        group_id: str,
        access_token: str,
    ) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"/{group_id}/invite_link",
            access_token=access_token,
        )

    async def reset_group_invite_link(
        self,
        *,
        group_id: str,
        access_token: str,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/{group_id}/invite_link",
            access_token=access_token,
            json={"messaging_product": "whatsapp"},
        )

    async def send_document_message(
        self,
        *,
        phone_number_id: str,
        access_token: str,
        to: str,
        link: str,
        filename: str,
        caption: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "document",
            "document": {
                "link": link,
                "filename": filename,
            },
        }
        if caption:
            payload["document"]["caption"] = caption
        message = {
            key: value
            for key, value in payload.items()
            if key not in {"messaging_product", "recipient_type", "to"}
        }
        return await self.send_message_payload(
            phone_number_id=phone_number_id,
            access_token=access_token,
            to=to,
            message=message,
        )

    async def upload_media(
        self,
        *,
        phone_number_id: str,
        access_token: str,
        filename: str,
        content_type: str,
        file_content: BinaryIO,
    ) -> dict[str, Any]:
        """Upload the exact file stream to Meta and require its provider media ID."""
        response = await self._request(
            "POST",
            f"/{phone_number_id}/media",
            access_token=access_token,
            data={"messaging_product": "whatsapp"},
            files={"file": (filename, file_content, content_type)},
        )
        if not str(response.get("id") or "").strip():
            raise WhatsAppProviderError(
                code="WA-MEDIA-ID-MISSING",
                message="A Meta aceitou o upload sem retornar o ID da mídia.",
                status_code=502,
                request_started=True,
                delivery_outcome_known=False,
            )
        return response

    async def send_template_message(
        self,
        *,
        phone_number_id: str,
        access_token: str,
        to: str,
        template_name: str,
        language_code: str,
        header_parameters: list[str] | None = None,
        body_parameters: list[str] | None = None,
        button_parameters: list[str | dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        components: list[dict[str, Any]] = []
        if header_parameters:
            components.append(
                {
                    "type": "header",
                    "parameters": [{"type": "text", "text": value} for value in header_parameters],
                }
            )
        if body_parameters:
            components.append(
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": value} for value in body_parameters],
                }
            )
        if button_parameters:
            # Meta requires one component per dynamic URL button.  Packing
            # every suffix into index 0 makes multi-button templates invalid.
            for index, item in enumerate(button_parameters):
                if isinstance(item, dict):
                    try:
                        index = int(item.get("index", index))
                    except (TypeError, ValueError):
                        index = index
                    value = str(item.get("value") or "")
                else:
                    value = str(item)
                components.append(
                    {
                        "type": "button",
                        "sub_type": "url",
                        "index": str(index),
                        "parameters": [{"type": "text", "text": value}],
                    }
                )
        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language_code},
            },
        }
        if components:
            payload["template"]["components"] = components
        message = {
            key: value
            for key, value in payload.items()
            if key not in {"messaging_product", "recipient_type", "to"}
        }
        return await self.send_message_payload(
            phone_number_id=phone_number_id,
            access_token=access_token,
            to=to,
            message=message,
        )

    @staticmethod
    def _parse_provider_code(raw_code: Any) -> int | None:
        """Normaliza error.code / error.error_subcode da Graph API para inteiro."""
        try:
            return int(raw_code) if raw_code is not None else None
        except (TypeError, ValueError):
            return None

    async def _request(
        self,
        method: str,
        path: str,
        *,
        access_token: str | None = None,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        data: Mapping[str, Any] | None = None,
        files: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not external_delivery_allowed():
            raise WhatsAppProviderError(
                code="KLM-EXT-001",
                message=(
                    "Chamada à Meta bloqueada enquanto entregas externas estiverem desabilitadas."
                ),
                status_code=503,
            )

        url = f"{self._base_url}{path}"
        headers: dict[str, str] = {}
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"

        started_at = time.perf_counter()
        client = self._pooled_client
        owns_client = client is None

        async def _send_once() -> httpx.Response:
            if client is None:
                async with httpx.AsyncClient(
                    timeout=self._timeout(),
                    limits=self._limits(),
                ) as transient_client:
                    return await transient_client.request(
                        method,
                        url,
                        params=params,
                        json=json,
                        data=data,
                        files=files,
                        headers=headers,
                    )
            return await client.request(
                method,
                url,
                params=params,
                json=json,
                data=data,
                files=files,
                headers=headers,
            )

        try:
            # S16: retentar SÓ a fase de conexão (o corpo nunca saiu: sem risco de
            # duplicar envio), com orçamento curto para o worker de campanha, que
            # tem 60 s por lote, não ficar preso. 429/5xx não retentam aqui: o
            # throttling é política de lote (errors.py) e 5xx é entrega incerta.
            async for attempt in AsyncRetrying(
                retry=retry_if_exception_type(_CONNECT_PHASE_ERRORS),
                stop=(stop_after_attempt(_CONNECT_RETRY_ATTEMPTS) | stop_after_delay(5)),
                wait=wait_exponential(multiplier=0.5, max=2),
                reraise=True,
            ):
                with attempt:
                    if attempt.retry_state.attempt_number > 1:
                        logger.info(
                            "meta_graph.retry",
                            method=method,
                            path=path,
                            attempt=attempt.retry_state.attempt_number,
                        )
                    response = await _send_once()
        except _CONNECT_PHASE_ERRORS as exc:
            raise WhatsAppProviderError(
                code="WA-CONNECT",
                message="Não foi possível iniciar a conexão com a Meta.",
                status_code=503,
                request_started=False,
            ) from exc
        except httpx.RequestError as exc:
            raise WhatsAppProviderError(
                code="WA-DELIVERY-UNCERTAIN",
                message="A conexão com a Meta foi interrompida após o início da requisição.",
                status_code=502,
                request_started=True,
                delivery_outcome_known=False,
            ) from exc
        logger.info(
            "meta_graph.request_completed",
            method=method,
            path=path,
            status_code=response.status_code,
            duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
            api_version=settings.META_GRAPH_API_VERSION,
            pooled=not owns_client,
        )

        if response.status_code not in (200, 201):
            error = {}
            try:
                error = response.json().get("error", {})
            except Exception:
                error = {}
            message = error.get("message") or response.text or "Erro desconhecido na Graph API."
            provider_code = self._parse_provider_code(error.get("code"))
            provider_subcode = self._parse_provider_code(error.get("error_subcode"))
            title = "WA-002" if response.status_code < 500 else "WA-500"
            retry_after = _parse_retry_after(response.headers.get("Retry-After"))
            connection_fault = response.status_code in {401, 403} or (
                provider_code in {102, 190} and error.get("type") == "OAuthException"
            )
            raise WhatsAppProviderError(
                code=title,
                message=message,
                status_code=400 if response.status_code < 500 else 502,
                provider_status_code=provider_code,
                provider_error_subcode=provider_subcode,
                request_started=True,
                delivery_outcome_known=response.status_code < 500,
                connection_fault=connection_fault,
                http_status=response.status_code,
                retry_after=retry_after,
            )

        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise WhatsAppProviderError(
                code="WA-DELIVERY-UNCERTAIN",
                message="A Meta aceitou a requisição, mas retornou uma resposta inválida.",
                status_code=502,
                request_started=True,
                delivery_outcome_known=False,
            ) from exc


@asynccontextmanager
async def meta_request_scope(client: Any):
    """Pool real Meta calls while leaving injected test/provider doubles untouched."""
    if isinstance(client, MetaCloudClient):
        async with client.request_scope():
            yield client
        return
    yield client
