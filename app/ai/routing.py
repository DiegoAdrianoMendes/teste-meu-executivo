"""Deterministic model routing by agent and task class."""
from __future__ import annotations

from app.ai.catalog import RouteCatalog
from app.ai.errors import NoRouteAvailableError
from app.ai.models import ModelRequest, ProviderName, RouteCandidate, TaskClass


class ModelRouter:
    def __init__(
        self,
        catalog: RouteCatalog,
        *,
        prefer_maritaca_for_simple: bool = False,
    ) -> None:
        self.catalog = catalog
        # Preferência opcional: com a flag ligada, a classe SIMPLE reordena as
        # rotas colocando Maritaca (sabiazinho) primeiro, mantendo a ordem
        # relativa das demais. Default desligado = ordem do catálogo intocada.
        self.prefer_maritaca_for_simple = prefer_maritaca_for_simple

    def candidates(self, request: ModelRequest) -> tuple[RouteCandidate, ...]:
        routes = self.catalog.routes_for(request.task_class, request.agent_name)
        if (
            self.prefer_maritaca_for_simple
            and request.task_class is TaskClass.SIMPLE
            and len(routes) > 1
        ):
            routes = tuple(
                sorted(routes, key=lambda route: route.provider is not ProviderName.MARITACA)
            )
        if request.requested_model:
            routes = tuple(route for route in routes if route.model == request.requested_model)
        if not routes:
            suffix = f" e modelo {request.requested_model}" if request.requested_model else ""
            raise NoRouteAvailableError(
                f"Nenhuma rota configurada para {request.agent_name}/{request.task_class.value}{suffix}."
            )
        return routes
