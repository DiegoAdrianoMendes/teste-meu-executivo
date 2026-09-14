"""Provisionamento idempotente da biblioteca UTILITY por conexão/WABA."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.klm_runtime import external_delivery_allowed
from app.models.klm_operations import KlmAuditEvent
from app.models.whatsapp import ConnectionType, WaConnectionStatus, WhatsAppConnection
from app.models.whatsapp_campaign import MetaTemplateStatus, WhatsAppMetaTemplate
from app.schemas.whatsapp_campaign import (
    TemplateComponentsIn,
    UtilityTemplateCatalogItemRead,
    UtilityTemplateCatalogRead,
    UtilityTemplateVariableRead,
)
from app.services.whatsapp.connection_service import WhatsAppConnectionService
from app.services.whatsapp.errors import WhatsAppProviderError
from app.services.whatsapp.meta_cloud_client import MetaCloudClient
from app.services.whatsapp.template_management_service import (
    TemplateManagementService,
    _build_meta_components,
)
from app.services.whatsapp.utility_template_catalog import (
    CATALOG_VERSION,
    UTILITY_TEMPLATE_CATALOG,
    UtilityTemplateDefinition,
)

CATALOG_SOURCE = "platform_utility_catalog"
_RETRYABLE_PROVIDER_CODES = {"WA-CONNECT", "WA-DELIVERY-UNCERTAIN", "WA-500"}
_RESERVED_NAME_MARKERS = (
    "already exists",
    "already been used",
    "já existe",
    "30 day",
    "30-day",
    "30 dias",
    "deleted",
    "excluído",
    "reservado",
)


def _catalog_payload(template: WhatsAppMetaTemplate) -> dict:
    raw = template.raw_payload or {}
    payload = raw.get("utility_catalog")
    return payload if isinstance(payload, dict) else {}


def _is_catalog_template(template: WhatsAppMetaTemplate) -> bool:
    return bool(_catalog_payload(template).get("key"))


def _is_reserved_name_error(error: WhatsAppProviderError) -> bool:
    normalized = (error.message or "").casefold()
    return any(marker in normalized for marker in _RESERVED_NAME_MARKERS)


class UtilityTemplateProvisioningService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        connection_service: WhatsAppConnectionService,
        meta_client: MetaCloudClient,
        inter_request_delay_seconds: float = 0.25,
    ) -> None:
        self.db = db
        self.connection_service = connection_service
        self.meta_client = meta_client
        self.inter_request_delay_seconds = max(0.0, inter_request_delay_seconds)
        self.template_service = TemplateManagementService(
            db,
            connection_service=connection_service,
            meta_client=meta_client,
        )

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    async def resolve_connection(
        self, workspace_id: UUID, connection_id: UUID | None
    ) -> WhatsAppConnection | None:
        if connection_id is not None:
            return await self.connection_service.get_connection(connection_id, workspace_id)
        return await self.connection_service.get_default_connection(
            workspace_id, connected_only=True
        )

    async def _templates(
        self, workspace_id: UUID, connection_id: UUID
    ) -> list[WhatsAppMetaTemplate]:
        return list(
            (
                await self.db.scalars(
                    select(WhatsAppMetaTemplate)
                    .where(
                        WhatsAppMetaTemplate.workspace_id == workspace_id,
                        WhatsAppMetaTemplate.whatsapp_connection_id == connection_id,
                    )
                    .order_by(
                        WhatsAppMetaTemplate.created_at.desc(),
                        WhatsAppMetaTemplate.id.desc(),
                    )
                )
            ).all()
        )

    @staticmethod
    def _attempt_for(template: WhatsAppMetaTemplate) -> int:
        try:
            return max(1, int(_catalog_payload(template).get("attempt") or 1))
        except (TypeError, ValueError):
            return 1

    @staticmethod
    def _components(definition: UtilityTemplateDefinition) -> TemplateComponentsIn:
        return TemplateComponentsIn(
            header_type="NONE",
            body_text=definition.body,
            body_examples=definition.examples,
            footer_text="Meu Executivo · comunicação do seu escritório",
            buttons=[],
        )

    def _new_template(
        self,
        *,
        workspace_id: UUID,
        connection_id: UUID,
        definition: UtilityTemplateDefinition,
        attempt: int,
        local_status: str,
    ) -> WhatsAppMetaTemplate:
        components = self._components(definition)
        actual_name = definition.meta_name if attempt == 1 else f"{definition.meta_name}_r{attempt}"
        now = self._now()
        return WhatsAppMetaTemplate(
            workspace_id=workspace_id,
            whatsapp_connection_id=connection_id,
            external_template_id=None,
            name=actual_name,
            category="utility",
            language=definition.language,
            status=MetaTemplateStatus.pending,
            quality_rating=None,
            components=_build_meta_components(components),
            raw_payload={
                "source": "local_draft",
                "local_status": local_status,
                "validation_errors": [],
                "builder_components": components.model_dump(),
                "utility_catalog": {
                    "source": CATALOG_SOURCE,
                    "key": definition.key,
                    "catalog_version": CATALOG_VERSION,
                    "item_version": definition.version,
                    "attempt": attempt,
                    "practice_areas": list(definition.practice_areas),
                    "prepared_at": now.isoformat(),
                },
            },
            last_synced_at=now,
        )

    async def prepare(
        self,
        *,
        workspace_id: UUID,
        connection_id: UUID,
        only_keys: set[str] | None = None,
        retry_rejected: bool = False,
    ) -> list[WhatsAppMetaTemplate]:
        """Materializa intenções locais sem executar chamada externa."""
        connection = await self.resolve_connection(workspace_id, connection_id)
        if (
            connection is None
            or connection.status != WaConnectionStatus.connected
            or connection.connection_type == ConnectionType.QR_CODE
        ):
            return []

        templates = await self._templates(workspace_id, connection.id)
        prepared: list[WhatsAppMetaTemplate] = []
        gate_open = external_delivery_allowed()
        for definition in UTILITY_TEMPLATE_CATALOG:
            if only_keys is not None and definition.key not in only_keys:
                continue
            catalog_attempts = [
                item
                for item in templates
                if _catalog_payload(item).get("key") == definition.key
                and int(_catalog_payload(item).get("item_version") or 0) == definition.version
            ]
            latest = catalog_attempts[0] if catalog_attempts else None
            if latest is not None:
                latest_local_status = str((latest.raw_payload or {}).get("local_status") or "")
                requires_new_name = bool(
                    retry_rejected
                    and (
                        latest.status == MetaTemplateStatus.rejected
                        or latest_local_status == "catalog_name_reserved"
                    )
                )
                if not requires_new_name:
                    prepared.append(latest)
                    continue
            elif any(
                item.name == definition.meta_name and item.language == definition.language
                for item in templates
            ):
                # Nome ocupado por item alheio ao catálogo: a listagem expõe o
                # conflito, sem criar uma segunda linha que jamais poderia ser enviada.
                continue

            # V33: versão nova do catálogo (corpo reescrito) deixa as tentativas antigas sem
            # id externo marcadas como superadas, para não ficarem submetíveis à mão com o
            # corpo que a Meta recusou.
            for stale in templates:
                stale_catalog = _catalog_payload(stale)
                if (
                    stale_catalog.get("key") != definition.key
                    or int(stale_catalog.get("item_version") or 0) >= definition.version
                    or stale.external_template_id
                ):
                    continue
                stale_raw = dict(stale.raw_payload or {})
                if stale_raw.get("local_status") == "catalog_superseded":
                    continue
                stale_raw["local_status"] = "catalog_superseded"
                stale.raw_payload = stale_raw
                self.db.add(stale)
            attempt = max([self._attempt_for(item) for item in catalog_attempts], default=0) + 1
            template = self._new_template(
                workspace_id=workspace_id,
                connection_id=connection.id,
                definition=definition,
                attempt=attempt,
                local_status="catalog_queued" if gate_open else "catalog_pending_gate",
            )
            self.db.add(template)
            prepared.append(template)
        if any(item.id is None for item in prepared):
            await self.db.commit()
            for item in prepared:
                if item.id is not None:
                    await self.db.refresh(item)
        return prepared

    async def provision(
        self,
        *,
        workspace_id: UUID,
        connection_id: UUID,
        only_keys: set[str] | None = None,
        retry_rejected: bool = False,
    ) -> dict[str, int | bool]:
        templates = await self.prepare(
            workspace_id=workspace_id,
            connection_id=connection_id,
            only_keys=only_keys,
            retry_rejected=retry_rejected,
        )
        if not external_delivery_allowed():
            return {"prepared": len(templates), "submitted": 0, "failed": 0, "retryable": False}

        submitted = failed = 0
        retryable = False
        candidates = [
            template
            for template in templates
            if not template.external_template_id
            and template.status
            not in {
                MetaTemplateStatus.approved,
                MetaTemplateStatus.rejected,
                MetaTemplateStatus.paused,
                MetaTemplateStatus.disabled,
            }
        ]
        for index, template in enumerate(candidates):
            raw = dict(template.raw_payload or {})
            if raw.get("local_status") == "submission_prepared" and raw.get("submitted_at"):
                continue
            raw["local_status"] = "catalog_queued"
            template.raw_payload = raw
            self.db.add(template)
            await self.db.commit()
            template_id = template.id
            try:
                result = await self.template_service.submit(workspace_id, template_id)
                submitted += int(result.submitted_to_meta)
            except WhatsAppProviderError as error:
                failed += 1
                retryable = retryable or error.code in _RETRYABLE_PROVIDER_CODES
                await self.db.rollback()
                current = await self.db.get(WhatsAppMetaTemplate, template_id)
                if current is None:
                    continue
                payload = dict(current.raw_payload or {})
                catalog = dict(payload.get("utility_catalog") or {})
                catalog.update(
                    {
                        "last_error_code": (
                            "WA-TPL-CATALOG-NAME-RESERVED"
                            if _is_reserved_name_error(error)
                            else error.code
                        ),
                        "last_error_message": error.message,
                        "last_failed_at": self._now().isoformat(),
                    }
                )
                payload["utility_catalog"] = catalog
                payload["local_status"] = (
                    "catalog_name_reserved"
                    if _is_reserved_name_error(error)
                    else "catalog_failed"
                )
                current.raw_payload = payload
                self.db.add(current)
                self.db.add(
                    KlmAuditEvent(
                        workspace_id=workspace_id,
                        actor_type="system",
                        actor_label="Provisionamento de templates",
                        event_type="whatsapp_utility_template_provision_failed",
                        entity_type="whatsapp_meta_template",
                        entity_id=current.id,
                        summary=f"Falha ao provisionar o template de utilidade '{current.name}'.",
                        after_data={
                            "catalogKey": catalog.get("key"),
                            "errorCode": catalog.get("last_error_code"),
                        },
                    )
                )
                await self.db.commit()
            finally:
                if index < len(candidates) - 1 and self.inter_request_delay_seconds:
                    await asyncio.sleep(self.inter_request_delay_seconds)
        return {
            "prepared": len(templates),
            "submitted": submitted,
            "failed": failed,
            "retryable": retryable,
        }

    async def list_catalog(
        self, *, workspace_id: UUID, connection_id: UUID | None = None
    ) -> UtilityTemplateCatalogRead:
        connection = await self.resolve_connection(workspace_id, connection_id)
        templates = (
            await self._templates(workspace_id, connection.id) if connection is not None else []
        )
        items: list[UtilityTemplateCatalogItemRead] = []
        for definition in UTILITY_TEMPLATE_CATALOG:
            attempts = [
                template
                for template in templates
                if _catalog_payload(template).get("key") == definition.key
                and int(_catalog_payload(template).get("item_version") or 0) == definition.version
            ]
            template = attempts[0] if attempts else None
            collision = next(
                (
                    item
                    for item in templates
                    if item.name == definition.meta_name
                    and item.language == definition.language
                    and not _is_catalog_template(item)
                ),
                None,
            )
            raw = dict(template.raw_payload or {}) if template is not None else {}
            catalog = dict(raw.get("utility_catalog") or {})
            if collision is not None and template is None:
                provisioning_status = "name_in_use"
                error_code = "WA-TPL-CATALOG-NAME-IN-USE"
                error_message = "O nome padrão já está em uso nesta WABA por outro template."
            elif template is None:
                provisioning_status = "not_prepared"
                error_code = error_message = None
            elif template.external_template_id:
                provisioning_status = template.status.value
                error_code = error_message = None
            else:
                provisioning_status = str(raw.get("local_status") or "draft")
                error_code = catalog.get("last_error_code")
                error_message = catalog.get("last_error_message")

            submitted_raw = raw.get("submitted_at")
            submitted_at = None
            if submitted_raw:
                try:
                    submitted_at = datetime.fromisoformat(str(submitted_raw))
                except ValueError:
                    submitted_at = None
            items.append(
                UtilityTemplateCatalogItemRead(
                    key=definition.key,
                    catalog_version=CATALOG_VERSION,
                    item_version=definition.version,
                    meta_name=template.name if template is not None else definition.meta_name,
                    body=definition.body,
                    variables=[
                        UtilityTemplateVariableRead(
                            position=index,
                            key=variable.key,
                            example=variable.example,
                        )
                        for index, variable in enumerate(definition.variables, start=1)
                    ],
                    practice_areas=list(definition.practice_areas),
                    template_id=template.id if template is not None else None,
                    external_template_id=(
                        template.external_template_id if template is not None else None
                    ),
                    status=template.status if template is not None else None,
                    provisioning_status=provisioning_status,
                    rejection_reason=raw.get("rejected_reason"),
                    error_code=error_code,
                    error_message=error_message,
                    attempt=self._attempt_for(template) if template is not None else 0,
                    submitted_at=submitted_at,
                    updated_at=template.updated_at if template is not None else None,
                    can_retry=bool(
                        connection is not None
                        and external_delivery_allowed()
                        and (
                            template is None
                            or template.status == MetaTemplateStatus.rejected
                            or provisioning_status
                            in {"catalog_failed", "catalog_name_reserved", "not_prepared"}
                        )
                    ),
                )
            )
        return UtilityTemplateCatalogRead(
            catalog_version=CATALOG_VERSION,
            connection_id=connection.id if connection is not None else None,
            gate_open=external_delivery_allowed(),
            items=items,
        )
