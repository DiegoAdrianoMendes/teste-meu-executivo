"""Versioned routing and pricing catalogs loaded from configuration."""
from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from app.ai.errors import AIConfigurationError, CostUnknownError
from app.ai.models import ProviderName, RouteCandidate, TaskClass


def _load_json_object(raw: str, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise AIConfigurationError(f"{label} contém JSON inválido.") from exc
    if not isinstance(value, dict):
        raise AIConfigurationError(f"{label} deve ser um objeto JSON.")
    return value


def parse_json_string_set(raw: str, label: str) -> frozenset[str]:
    try:
        value = json.loads(raw or "[]")
    except json.JSONDecodeError as exc:
        raise AIConfigurationError(f"{label} contém JSON inválido.") from exc
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise AIConfigurationError(f"{label} deve ser uma lista JSON de strings.")
    return frozenset(item.strip() for item in value if item.strip())


@dataclass(frozen=True, slots=True)
class ModelPrice:
    input_per_million_usd: Decimal
    output_per_million_usd: Decimal
    cached_input_per_million_usd: Decimal | None = None

    @classmethod
    def from_dict(cls, value: Any, key: str) -> "ModelPrice":
        if not isinstance(value, dict):
            raise AIConfigurationError(f"Preço de {key} deve ser um objeto.")
        try:
            input_price = Decimal(str(value["input_per_million_usd"]))
            output_price = Decimal(str(value["output_per_million_usd"]))
            cached_raw = value.get("cached_input_per_million_usd")
            cached_price = Decimal(str(cached_raw)) if cached_raw is not None else None
        except (KeyError, InvalidOperation, ValueError) as exc:
            raise AIConfigurationError(f"Preço de {key} está incompleto ou inválido.") from exc
        if input_price < 0 or output_price < 0 or (cached_price is not None and cached_price < 0):
            raise AIConfigurationError(f"Preço de {key} não pode ser negativo.")
        return cls(input_price, output_price, cached_price)


@dataclass(frozen=True, slots=True)
class PriceCatalog:
    version: str
    currency: str
    effective_at: str | None
    models: dict[str, ModelPrice]

    @classmethod
    def from_json(cls, raw: str) -> "PriceCatalog":
        value = _load_json_object(raw, "KLM_AI_PRICE_CATALOG_JSON")
        version = str(value.get("version") or "").strip()
        currency = str(value.get("currency") or "USD").upper().strip()
        models_raw = value.get("models", {})
        if not version:
            raise AIConfigurationError("O catálogo de preços precisa de uma versão.")
        if currency != "USD":
            raise AIConfigurationError("O runtime de orçamento aceita catálogo em USD.")
        if not isinstance(models_raw, dict):
            raise AIConfigurationError("models do catálogo de preços deve ser um objeto.")
        models = {
            str(key): ModelPrice.from_dict(item, str(key))
            for key, item in models_raw.items()
        }
        return cls(
            version=version,
            currency=currency,
            effective_at=value.get("effective_at"),
            models=models,
        )

    def require_price(self, route: RouteCandidate) -> ModelPrice:
        price = self.models.get(route.key)
        if price is None:
            raise CostUnknownError(
                f"Custo desconhecido para {route.key} no catálogo {self.version}."
            )
        return price

    def compute_cost(
        self,
        route: RouteCandidate,
        *,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int = 0,
    ) -> Decimal:
        price = self.require_price(route)
        cached = min(max(cached_input_tokens, 0), max(input_tokens, 0))
        uncached = max(input_tokens - cached, 0)
        cached_rate = price.cached_input_per_million_usd
        if cached_rate is None:
            uncached += cached
            cached = 0
            cached_rate = Decimal("0")
        amount = (
            Decimal(uncached) * price.input_per_million_usd
            + Decimal(cached) * cached_rate
            + Decimal(max(output_tokens, 0)) * price.output_per_million_usd
        ) / Decimal(1_000_000)
        return amount.quantize(Decimal("0.00000001"))


@dataclass(frozen=True, slots=True)
class RouteCatalog:
    version: str
    classes: dict[TaskClass, tuple[RouteCandidate, ...]]
    agents: dict[str, tuple[RouteCandidate, ...]]

    @staticmethod
    def _parse_candidates(value: Any, label: str) -> tuple[RouteCandidate, ...]:
        if not isinstance(value, list):
            raise AIConfigurationError(f"Rotas de {label} devem ser uma lista.")
        candidates: list[RouteCandidate] = []
        seen: set[str] = set()
        for index, item in enumerate(value):
            if not isinstance(item, dict):
                raise AIConfigurationError(f"Rota {label}[{index}] deve ser um objeto.")
            try:
                provider = ProviderName(str(item["provider"]))
                model = str(item["model"]).strip()
            except (KeyError, ValueError) as exc:
                raise AIConfigurationError(f"Rota {label}[{index}] é inválida.") from exc
            if not model or ":" in model:
                raise AIConfigurationError(f"Modelo inválido em {label}[{index}].")
            timeout_raw = item.get("timeout_seconds")
            timeout = float(timeout_raw) if timeout_raw is not None else None
            if timeout is not None and timeout <= 0:
                raise AIConfigurationError(f"Timeout inválido em {label}[{index}].")
            candidate = RouteCandidate(provider=provider, model=model, timeout_seconds=timeout)
            if candidate.key in seen:
                raise AIConfigurationError(f"Rota duplicada em {label}: {candidate.key}.")
            seen.add(candidate.key)
            candidates.append(candidate)
        return tuple(candidates)

    @classmethod
    def from_json(cls, raw: str) -> "RouteCatalog":
        value = _load_json_object(raw, "KLM_AI_ROUTE_CATALOG_JSON")
        version = str(value.get("version") or "").strip()
        if not version:
            raise AIConfigurationError("O catálogo de rotas precisa de uma versão.")
        classes_raw = value.get("classes", {})
        agents_raw = value.get("agents", {})
        if not isinstance(classes_raw, dict) or not isinstance(agents_raw, dict):
            raise AIConfigurationError("classes e agents do catálogo de rotas devem ser objetos.")

        classes: dict[TaskClass, tuple[RouteCandidate, ...]] = {}
        for raw_class, routes in classes_raw.items():
            try:
                task_class = TaskClass(str(raw_class))
            except ValueError as exc:
                raise AIConfigurationError(f"Classe de tarefa desconhecida: {raw_class}.") from exc
            classes[task_class] = cls._parse_candidates(routes, f"classes.{raw_class}")

        agents: dict[str, tuple[RouteCandidate, ...]] = {}
        for raw_agent, routes in agents_raw.items():
            agent = str(raw_agent).strip().lower()
            if not agent:
                raise AIConfigurationError("Nome de agente vazio no catálogo de rotas.")
            agents[agent] = cls._parse_candidates(routes, f"agents.{agent}")

        return cls(version=version, classes=classes, agents=agents)

    def routes_for(self, task_class: TaskClass, agent_name: str) -> tuple[RouteCandidate, ...]:
        agent_routes = self.agents.get(agent_name.strip().lower())
        return agent_routes if agent_routes is not None else self.classes.get(task_class, ())


def parse_agent_cost_caps(raw: str) -> dict[str, Decimal]:
    value = _load_json_object(raw, "KLM_AI_AGENT_CALL_CAPS_JSON")
    result: dict[str, Decimal] = {}
    for agent, amount in value.items():
        try:
            parsed = Decimal(str(amount))
        except InvalidOperation as exc:
            raise AIConfigurationError(f"Teto inválido para o agente {agent}.") from exc
        if parsed <= 0:
            raise AIConfigurationError(f"Teto do agente {agent} deve ser positivo.")
        result[str(agent).strip().lower()] = parsed
    return result


def parse_workspace_budgets(raw: str) -> dict[str, Decimal]:
    value = _load_json_object(raw, "KLM_AI_WORKSPACE_DAILY_BUDGETS_JSON")
    result: dict[str, Decimal] = {}
    for workspace_id, amount in value.items():
        try:
            parsed = Decimal(str(amount))
        except InvalidOperation as exc:
            raise AIConfigurationError(f"Orçamento inválido para workspace {workspace_id}.") from exc
        if parsed <= 0:
            raise AIConfigurationError(f"Orçamento de {workspace_id} deve ser positivo.")
        result[str(workspace_id)] = parsed
    return result
