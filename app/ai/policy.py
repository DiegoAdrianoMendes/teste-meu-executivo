"""Independent gates and request-level safety policy for model APIs."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.ai.errors import (
    AIConfigurationError,
    BudgetExceededError,
    MissingWorkspaceContextError,
    ModelAPIDisabledError,
)
from app.ai.models import CostEstimate, ModelRequest, PromptCachePolicy


@dataclass(frozen=True, slots=True)
class ModelAPIGate:
    model_apis_enabled: bool
    online_approved: bool
    local_fake_enabled: bool = False

    def require_open(self) -> None:
        if self.local_fake_enabled:
            return
        if not self.model_apis_enabled or not self.online_approved:
            raise ModelAPIDisabledError(
                "APIs de modelos estão desativadas; KLM_MODEL_APIS_ENABLED e "
                "KLM_ONLINE_APPROVED precisam estar ativos."
            )


def require_model_api_enabled() -> None:
    """Protect legacy direct API providers with the same independent gate."""
    from app.core.config import settings

    ModelAPIGate(
        model_apis_enabled=settings.KLM_MODEL_APIS_ENABLED,
        online_approved=settings.KLM_ONLINE_APPROVED,
    ).require_open()


def require_legacy_direct_calls_enabled() -> None:
    require_model_api_enabled()
    raise ModelAPIDisabledError(
        "Chamadas diretas de provider legado foram encerradas; use o gateway de modelos."
    )


@dataclass(frozen=True, slots=True)
class RequestPolicy:
    max_input_tokens: int
    max_output_tokens: int
    max_cost_per_call_usd: Decimal
    default_workspace_daily_budget_usd: Decimal
    workspace_daily_budgets_usd: dict[str, Decimal]
    agent_call_caps_usd: dict[str, Decimal]
    max_media_parts: int = 8
    max_single_media_bytes: int = 25 * 1024 * 1024
    max_media_bytes: int = 50 * 1024 * 1024

    def validate_request(self, request: ModelRequest, estimated_input_tokens: int) -> None:
        if not request.workspace_id.strip():
            raise MissingWorkspaceContextError(
                "workspace_id é obrigatório para isolar orçamento e telemetria de IA."
            )
        if not request.agent_name.strip():
            raise AIConfigurationError("agent_name é obrigatório para roteamento econômico.")
        if not request.messages or not any(message.content.strip() for message in request.messages):
            raise AIConfigurationError("A chamada de modelo precisa de conteúdo.")
        invalid_roles = {
            message.role
            for message in request.messages
            if message.role not in {"system", "user", "assistant"}
        }
        if invalid_roles:
            raise AIConfigurationError("A chamada contém papel de mensagem não suportado.")
        if estimated_input_tokens > self.max_input_tokens:
            raise BudgetExceededError("A entrada excede o teto de tokens por chamada.")
        if request.max_output_tokens <= 0 or request.max_output_tokens > self.max_output_tokens:
            raise BudgetExceededError("A saída solicitada excede o teto de tokens por chamada.")
        if request.idempotent and not request.idempotency_key:
            raise AIConfigurationError(
                "Chamadas marcadas como idempotentes exigem idempotency_key."
            )
        if len(request.media_parts) > self.max_media_parts:
            raise BudgetExceededError("A chamada excede o teto de anexos multimodais.")
        if any(part.size_bytes > self.max_single_media_bytes for part in request.media_parts):
            raise BudgetExceededError("Um anexo multimodal excede o teto por arquivo.")
        if sum(part.size_bytes for part in request.media_parts) > self.max_media_bytes:
            raise BudgetExceededError("A chamada excede o teto total de mídia.")
        if request.media_parts and not request.contains_sensitive_data:
            raise AIConfigurationError("Entradas multimodais devem ser marcadas como sensíveis.")
        if request.prompt_cache_policy is not PromptCachePolicy.OFF:
            if not str(request.prompt_cache_key or "").strip():
                raise AIConfigurationError(
                    "Prompt caching exige uma chave estável e não sensível."
                )
            if len(str(request.prompt_cache_key)) > 240:
                raise AIConfigurationError("A chave de prompt caching é longa demais.")
            has_static_system = any(
                message.role == "system" and message.content.strip()
                for message in request.messages
            )
            if not has_static_system:
                raise AIConfigurationError(
                    "Prompt caching exige um prefixo system estático explícito."
                )

    def call_cap(self, request: ModelRequest) -> Decimal:
        caps = [self.max_cost_per_call_usd]
        if request.max_cost_usd is not None:
            caps.append(request.max_cost_usd)
        agent_cap = self.agent_call_caps_usd.get(request.agent_name.strip().lower())
        if agent_cap is not None:
            caps.append(agent_cap)
        return min(caps)

    def require_estimate_within_call_cap(
        self,
        request: ModelRequest,
        estimate: CostEstimate,
    ) -> None:
        cap = self.call_cap(request)
        if estimate.amount_usd > cap:
            raise BudgetExceededError(f"Estimativa da chamada excede o teto de USD {cap}.")

    def daily_budget(self, workspace_id: str) -> Decimal:
        return self.workspace_daily_budgets_usd.get(
            workspace_id,
            self.default_workspace_daily_budget_usd,
        )
