"""Contratos do roteamento de modelos de IA por classe de tarefa.

O GET expõe, por classe de tarefa do gateway, a rota efetiva (override do
workspace ou padrão da plataforma) e o catálogo de opções selecionáveis com
preço por 1M de tokens. O PATCH aceita apenas rotas presentes no catálogo do
gateway (fail-closed) e ``null`` para voltar ao padrão da plataforma.
"""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.base import CamelCaseSchema, WriteSchema

AiRouteSource = Literal["workspace", "platform", "unconfigured"]


class AiModelPriceRead(CamelCaseSchema):
    """Preço por 1 milhão de tokens (USD) do catálogo versionado do gateway."""

    input_per_million_usd: float = Field(ge=0)
    output_per_million_usd: float = Field(ge=0)
    cached_input_per_million_usd: float | None = Field(default=None, ge=0)


class AiModelOptionRead(CamelCaseSchema):
    """Opção selecionável do catálogo do gateway (provider + modelo + preço)."""

    provider: str
    model: str
    price: AiModelPriceRead | None = None


class AiTaskClassRouteRead(CamelCaseSchema):
    """Rota efetiva de uma classe de tarefa, com origem e padrão da plataforma."""

    task_class: str
    provider: str | None
    model: str | None
    source: AiRouteSource
    platform_provider: str | None = None
    platform_model: str | None = None
    price: AiModelPriceRead | None = None


class WorkspaceAiModelRoutingRead(CamelCaseSchema):
    catalog_version: str
    classes: list[AiTaskClassRouteRead] = Field(default_factory=list)
    options: list[AiModelOptionRead] = Field(default_factory=list)


class AiModelRouteWrite(WriteSchema):
    provider: str = Field(min_length=2, max_length=40)
    model: str = Field(min_length=1, max_length=200)


class WorkspaceAiModelRoutingUpdate(WriteSchema):
    """PATCH parcial: só as classes presentes mudam; ``null`` volta ao padrão."""

    routes: dict[str, AiModelRouteWrite | None]

    @model_validator(mode="after")
    def validate_patch(self) -> "WorkspaceAiModelRoutingUpdate":
        if not self.routes:
            raise ValueError(
                "Informe ao menos uma classe de tarefa no roteamento de modelos."
            )
        return self


class PlatformAiByokProviderRead(CamelCaseSchema):
    """Status redigido de uma credencial BYOK — nunca contém a chave."""

    provider: str
    is_active: bool
    api_key_masked: str
    updated_at: str | None = None


class PlatformAiByokWorkspaceRead(CamelCaseSchema):
    workspace_id: UUID
    workspace_name: str
    providers: list[PlatformAiByokProviderRead] = Field(default_factory=list)
    custom_route_classes: list[str] = Field(default_factory=list)


class PlatformAiModelsOverviewRead(CamelCaseSchema):
    catalog_version: str
    defaults: list[AiTaskClassRouteRead] = Field(default_factory=list)
    byok_workspaces: list[PlatformAiByokWorkspaceRead] = Field(default_factory=list)


__all__ = [
    "AiModelOptionRead",
    "AiModelPriceRead",
    "AiModelRouteWrite",
    "AiRouteSource",
    "AiTaskClassRouteRead",
    "PlatformAiByokProviderRead",
    "PlatformAiByokWorkspaceRead",
    "PlatformAiModelsOverviewRead",
    "WorkspaceAiModelRoutingRead",
    "WorkspaceAiModelRoutingUpdate",
]
