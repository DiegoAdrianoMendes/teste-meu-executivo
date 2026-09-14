"""Serviço de roteamento de modelos de IA por classe de tarefa do workspace.

Segue o padrão da política ai-costs: leitura sempre disponível, escrita
auditada (KlmAuditEvent) e validada contra o catálogo versionado do gateway.
Nenhuma rota fora do catálogo/allowlist é aceita (fail-closed) e este
serviço NÃO abre nenhum gate externo — apenas configura preferências que o
gateway consome pelos próprios catálogos.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.canonical_catalog import (
    resolve_model_allowlist,
    resolve_price_catalog,
    resolve_route_catalog,
)
from app.ai.errors import AIConfigurationError
from app.ai.models import ProviderName, TaskClass
from app.db.tenant import TenantSession, set_workspace_context
from app.models.workspace import Workspace
from app.models.workspace_ai_model_route import WorkspaceAiModelRoute
from app.models.workspace_model_credential import WorkspaceModelCredential
from app.schemas.workspace_ai_models import (
    AiModelOptionRead,
    AiModelPriceRead,
    AiTaskClassRouteRead,
    PlatformAiByokProviderRead,
    PlatformAiByokWorkspaceRead,
    PlatformAiModelsOverviewRead,
    WorkspaceAiModelRoutingRead,
    WorkspaceAiModelRoutingUpdate,
)
from app.services.klm_operations_service import KlmOperationsService
from app.services.workspace_model_credential_service import redact_api_key


class AiModelRoutingError(Exception):
    """Base para falhas de roteamento de modelos."""


class AiModelRoutingWorkspaceNotFoundError(AiModelRoutingError):
    """Workspace inexistente ou removido."""


class UnknownAiTaskClassError(AiModelRoutingError):
    """Classe de tarefa fora do enum do gateway."""


class UnknownAiModelRouteError(AiModelRoutingError):
    """Par provider+modelo fora do catálogo do gateway (fail-closed)."""


@dataclass(frozen=True, slots=True)
class CatalogOption:
    provider: str
    model: str
    input_per_million_usd: float
    output_per_million_usd: float
    cached_input_per_million_usd: float | None

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.model}"


@dataclass(frozen=True, slots=True)
class GatewayCatalogView:
    """Visão imutável do catálogo do gateway para configuração de roteamento."""

    version: str
    options: dict[str, CatalogOption]
    defaults: dict[TaskClass, str | None]  # task_class -> chave "provider:model"


def load_gateway_catalog_view() -> GatewayCatalogView:
    """Deriva o catálogo selecionável SEM construir o gateway nem abrir gates.

    - Runtime local (AI_PROVIDER=fake): catálogo sintético determinístico com
      custo zero, idêntico ao usado por ``build_local_fake_model_gateway``.
    - Runtime API: parse puro dos catálogos versionados de configuração,
      interseção com a allowlist quando ela está configurada.
    """
    from app.core.config import settings

    if getattr(settings, "AI_PROVIDER", "api_gateway") == "fake":
        model = str(getattr(settings, "KLM_AI_FAKE_MODEL", "klm-fake-v1")).strip()
        option = CatalogOption(
            provider=ProviderName.FAKE.value,
            model=model or "klm-fake-v1",
            input_per_million_usd=0.0,
            output_per_million_usd=0.0,
            cached_input_per_million_usd=0.0,
        )
        return GatewayCatalogView(
            version="local-fake",
            options={option.key: option},
            defaults={task_class: option.key for task_class in TaskClass},
        )

    try:
        # Mesma resolução da factory: sentinela 'unconfigured' vira o catálogo
        # canônico versionado; env explícito vence; env inválido falha fechado.
        route_catalog = resolve_route_catalog(settings.KLM_AI_ROUTE_CATALOG_JSON)
        price_catalog = resolve_price_catalog(settings.KLM_AI_PRICE_CATALOG_JSON)
        allowlist = resolve_model_allowlist(
            settings.KLM_AI_MODEL_ALLOWLIST_JSON,
            route_catalog=route_catalog,
        )
    except AIConfigurationError:
        # Configuração inválida nunca vira valor inventado: catálogo vazio,
        # PATCH falha fechado e a UI mostra o estado real.
        return GatewayCatalogView(version="unconfigured", options={}, defaults={
            task_class: None for task_class in TaskClass
        })

    options: dict[str, CatalogOption] = {}
    for key in sorted(price_catalog.models):
        if allowlist and key not in allowlist:
            continue
        provider_raw, _, model = key.partition(":")
        if not model.strip():
            continue
        try:
            provider = ProviderName(provider_raw)
        except ValueError:
            continue
        price = price_catalog.models[key]
        options[key] = CatalogOption(
            provider=provider.value,
            model=model,
            input_per_million_usd=float(price.input_per_million_usd),
            output_per_million_usd=float(price.output_per_million_usd),
            cached_input_per_million_usd=(
                float(price.cached_input_per_million_usd)
                if price.cached_input_per_million_usd is not None
                else None
            ),
        )

    defaults: dict[TaskClass, str | None] = {}
    for task_class in TaskClass:
        candidates = route_catalog.classes.get(task_class, ())
        chosen = next(
            (candidate for candidate in candidates if candidate.key in options),
            None,
        )
        defaults[task_class] = chosen.key if chosen is not None else None

    return GatewayCatalogView(
        version=route_catalog.version,
        options=options,
        defaults=defaults,
    )


def _price_read(option: CatalogOption | None) -> AiModelPriceRead | None:
    if option is None:
        return None
    return AiModelPriceRead(
        input_per_million_usd=option.input_per_million_usd,
        output_per_million_usd=option.output_per_million_usd,
        cached_input_per_million_usd=option.cached_input_per_million_usd,
    )


class WorkspaceAiModelRoutingService:
    """Lê e altera apenas os overrides locais de roteamento do workspace."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_routing(self, workspace_id: UUID) -> WorkspaceAiModelRoutingRead:
        await self._require_workspace(workspace_id)
        catalog = load_gateway_catalog_view()
        overrides = await self._load_overrides(workspace_id)
        return self._build_read(catalog, overrides)

    async def update_routing(
        self,
        *,
        workspace_id: UUID,
        payload: WorkspaceAiModelRoutingUpdate,
        actor_user_id: UUID,
    ) -> WorkspaceAiModelRoutingRead:
        await self._require_workspace(workspace_id, for_update=True)
        catalog = load_gateway_catalog_view()

        normalized: dict[TaskClass, str | None] = {}
        for raw_class, route in payload.routes.items():
            try:
                task_class = TaskClass(str(raw_class).strip().lower())
            except ValueError as exc:
                raise UnknownAiTaskClassError(
                    f"Classe de tarefa desconhecida: {raw_class!r}. "
                    f"Aceitas: {', '.join(item.value for item in TaskClass)}."
                ) from exc
            if route is None:
                normalized[task_class] = None
                continue
            key = f"{route.provider.strip().lower()}:{route.model.strip()}"
            if key not in catalog.options:
                raise UnknownAiModelRouteError(
                    f"Rota {key!r} fora do catálogo do gateway "
                    f"(versão {catalog.version}). Nenhum modelo fora do "
                    "catálogo pode ser configurado."
                )
            normalized[task_class] = key

        overrides = await self._load_overrides(workspace_id)
        before = self._audit_snapshot(overrides)

        for task_class, key in normalized.items():
            existing = overrides.get(task_class.value)
            if key is None:
                if existing is not None:
                    await self.db.delete(existing)
                    overrides.pop(task_class.value, None)
                continue
            option = catalog.options[key]
            if existing is None:
                existing = WorkspaceAiModelRoute(
                    workspace_id=workspace_id,
                    task_class=task_class.value,
                    provider=option.provider,
                    model=option.model,
                )
                self.db.add(existing)
                overrides[task_class.value] = existing
            else:
                existing.provider = option.provider
                existing.model = option.model
        await self.db.flush()

        after_overrides = await self._load_overrides(workspace_id)
        await KlmOperationsService(self.db).record_audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            actor_type="human",
            actor_label="human",
            event_type="ai_model_routing_updated",
            entity_type="workspace_ai_model_route",
            entity_id=workspace_id,
            summary="Roteamento de modelos de IA por classe de tarefa atualizado.",
            before_data=before,
            after_data=self._audit_snapshot(after_overrides),
        )
        await self.db.flush()
        return self._build_read(catalog, after_overrides)

    async def _load_overrides(
        self, workspace_id: UUID
    ) -> dict[str, WorkspaceAiModelRoute]:
        rows = (
            (
                await self.db.execute(
                    select(WorkspaceAiModelRoute).where(
                        WorkspaceAiModelRoute.workspace_id == workspace_id
                    )
                )
            )
            .scalars()
            .all()
        )
        return {row.task_class: row for row in rows}

    def _build_read(
        self,
        catalog: GatewayCatalogView,
        overrides: dict[str, WorkspaceAiModelRoute],
    ) -> WorkspaceAiModelRoutingRead:
        classes: list[AiTaskClassRouteRead] = []
        for task_class in TaskClass:
            override = overrides.get(task_class.value)
            default_key = catalog.defaults.get(task_class)
            default_option = (
                catalog.options.get(default_key) if default_key is not None else None
            )
            if override is not None:
                option = catalog.options.get(f"{override.provider}:{override.model}")
                classes.append(
                    AiTaskClassRouteRead(
                        task_class=task_class.value,
                        provider=override.provider,
                        model=override.model,
                        source="workspace",
                        platform_provider=(
                            default_option.provider if default_option else None
                        ),
                        platform_model=default_option.model if default_option else None,
                        price=_price_read(option),
                    )
                )
                continue
            if default_option is not None:
                classes.append(
                    AiTaskClassRouteRead(
                        task_class=task_class.value,
                        provider=default_option.provider,
                        model=default_option.model,
                        source="platform",
                        platform_provider=default_option.provider,
                        platform_model=default_option.model,
                        price=_price_read(default_option),
                    )
                )
                continue
            classes.append(
                AiTaskClassRouteRead(
                    task_class=task_class.value,
                    provider=None,
                    model=None,
                    source="unconfigured",
                    platform_provider=None,
                    platform_model=None,
                    price=None,
                )
            )
        options = [
            AiModelOptionRead(
                provider=option.provider,
                model=option.model,
                price=_price_read(option),
            )
            for _, option in sorted(catalog.options.items())
        ]
        return WorkspaceAiModelRoutingRead(
            catalog_version=catalog.version,
            classes=classes,
            options=options,
        )

    async def _require_workspace(
        self,
        workspace_id: UUID,
        *,
        for_update: bool = False,
    ) -> None:
        statement = select(Workspace.id).where(
            Workspace.id == workspace_id,
            Workspace.deleted_at.is_(None),
        )
        if for_update:
            statement = statement.with_for_update()
        if await self.db.scalar(statement) is None:
            raise AiModelRoutingWorkspaceNotFoundError("Workspace não encontrado.")
        await set_workspace_context(self.db, workspace_id)

    @staticmethod
    def _audit_snapshot(
        overrides: dict[str, WorkspaceAiModelRoute],
    ) -> dict[str, object]:
        return {
            task_class.value: (
                f"{overrides[task_class.value].provider}:"
                f"{overrides[task_class.value].model}"
                if task_class.value in overrides
                else None
            )
            for task_class in TaskClass
        }


async def build_platform_ai_models_overview(
    db: AsyncSession,
) -> PlatformAiModelsOverviewRead:
    """Visão do super admin: defaults globais + workspaces com chave própria.

    Percorre cada workspace ativo fixando o contexto de tenant antes de ler as
    tabelas protegidas por RLS. NUNCA expõe a chave — apenas a máscara.
    """
    catalog = load_gateway_catalog_view()
    defaults: list[AiTaskClassRouteRead] = []
    for task_class in TaskClass:
        default_key = catalog.defaults.get(task_class)
        option = catalog.options.get(default_key) if default_key is not None else None
        defaults.append(
            AiTaskClassRouteRead(
                task_class=task_class.value,
                provider=option.provider if option else None,
                model=option.model if option else None,
                source="platform" if option else "unconfigured",
                platform_provider=option.provider if option else None,
                platform_model=option.model if option else None,
                price=_price_read(option),
            )
        )

    workspaces = (
        (
            await db.execute(
                select(Workspace)
                .where(Workspace.deleted_at.is_(None))
                .order_by(Workspace.name.asc())
            )
        )
        .scalars()
        .all()
    )

    byok_workspaces: list[PlatformAiByokWorkspaceRead] = []
    tenant_session_factory = async_sessionmaker(
        bind=db.bind,
        class_=AsyncSession,
        sync_session_class=TenantSession,
        expire_on_commit=False,
        autoflush=False,
    )
    semaphore = asyncio.Semaphore(16)

    async def read_workspace(workspace: Workspace) -> PlatformAiByokWorkspaceRead | None:
        # The tenant guard intentionally forbids switching one ORM session
        # between workspaces. Every tenant read therefore gets its own session
        # and transaction while the root request remains attributable. Reads
        # are bounded-concurrent so a platform with hundreds of workspaces does
        # not turn this aggregate endpoint into a serial N+1 timeout.
        async with semaphore:
            async with tenant_session_factory() as tenant_db:
                await set_workspace_context(tenant_db, workspace.id)
                credentials = (
                    (
                        await tenant_db.execute(
                            select(WorkspaceModelCredential)
                            .where(
                                WorkspaceModelCredential.workspace_id == workspace.id,
                                WorkspaceModelCredential.is_active.is_(True),
                            )
                            .order_by(WorkspaceModelCredential.provider.asc())
                        )
                    )
                    .scalars()
                    .all()
                )
                custom_routes = (
                    (
                        await tenant_db.execute(
                            select(WorkspaceAiModelRoute.task_class)
                            .where(WorkspaceAiModelRoute.workspace_id == workspace.id)
                            .order_by(WorkspaceAiModelRoute.task_class.asc())
                        )
                    )
                    .scalars()
                    .all()
                )
            if not credentials and not custom_routes:
                return None
            return PlatformAiByokWorkspaceRead(
                workspace_id=workspace.id,
                workspace_name=workspace.name,
                providers=[
                    PlatformAiByokProviderRead(
                        provider=credential.provider,
                        is_active=bool(credential.is_active),
                        api_key_masked=redact_api_key(credential.api_key_hint),
                        updated_at=(
                            credential.updated_at.isoformat()
                            if credential.updated_at
                            else None
                        ),
                    )
                    for credential in credentials
                ],
                custom_route_classes=list(custom_routes),
            )

    workspace_results = await asyncio.gather(
        *(read_workspace(workspace) for workspace in workspaces)
    )
    byok_workspaces.extend(result for result in workspace_results if result is not None)

    return PlatformAiModelsOverviewRead(
        catalog_version=catalog.version,
        defaults=defaults,
        byok_workspaces=byok_workspaces,
    )


__all__ = [
    "AiModelRoutingError",
    "AiModelRoutingWorkspaceNotFoundError",
    "CatalogOption",
    "GatewayCatalogView",
    "UnknownAiModelRouteError",
    "UnknownAiTaskClassError",
    "WorkspaceAiModelRoutingService",
    "build_platform_ai_models_overview",
    "load_gateway_catalog_view",
]
