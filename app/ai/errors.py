"""Fail-closed errors for the API-first model runtime."""
from __future__ import annotations


class AIRuntimeError(RuntimeError):
    """Base class for errors safe to surface without provider payloads."""


class ModelAPIDisabledError(AIRuntimeError):
    """The independent model API gate is closed."""


class AIConfigurationError(AIRuntimeError):
    """A runtime catalog, allowlist, or provider setting is invalid."""


class ContextCompilationError(AIRuntimeError):
    """Model context could not be normalized into the versioned contract."""


class ContextScopeError(ContextCompilationError):
    """Context provenance does not match the requested workspace and matter."""


class ContextBudgetExceededError(ContextCompilationError):
    """The mandatory context envelope cannot fit within its token budget."""


class EvidenceRequiredError(ContextCompilationError):
    """A scoped retrieval could not supply the evidence required by the caller."""


class MissingWorkspaceContextError(AIRuntimeError):
    """A model request omitted the workspace required for budget isolation."""


class NoRouteAvailableError(AIRuntimeError):
    """No explicitly configured and policy-compliant model route is available."""


class ModelNotAllowedError(AIRuntimeError):
    """The requested provider/model pair is outside the model allowlist."""


class BaseURLNotAllowedError(AIRuntimeError):
    """A provider base URL is outside the exact URL allowlist."""


class CostUnknownError(AIRuntimeError):
    """A model has no entry in the versioned price catalog."""


class BudgetExceededError(AIRuntimeError):
    """A call or workspace daily cost ceiling would be exceeded."""


class BudgetResolutionConflictError(AIRuntimeError):
    """A durable reservation was already resolved with a different outcome."""


class UsageAccountingError(AIRuntimeError):
    """A billable response exists but durable token accounting needs reconciliation."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.request_started = True
        self.billing_unknown = False
        self.accounting_unknown = True
        self.provider_request_id = ""
        self.usage_reservation_id = ""
        self.usage_reserved_tokens = 0


class UsageAccountingUnavailableError(AIRuntimeError):
    """The canonical external path has no durable SQL accountant configured."""


class ModelRequestReplayError(AIRuntimeError):
    """A durable provider claim already owns or resolved this request id."""

    def __init__(self, *, request_id: str, state: str, divergent: bool = False) -> None:
        self.request_id = request_id
        self.state = state
        self.divergent = divergent
        detail = " com conteúdo divergente" if divergent else ""
        super().__init__(
            f"A execução {request_id} já possui claim SQL em estado {state}{detail}; "
            "novo envio ao provider foi bloqueado."
        )


class CircuitOpenError(AIRuntimeError):
    """A provider/model route is temporarily unavailable."""


class StructuredOutputError(AIRuntimeError):
    """The model output is not valid JSON matching the local schema."""


class ProviderNotConfiguredError(AIRuntimeError):
    """A route is missing credentials or required provider configuration."""


class ProviderCapabilityError(AIRuntimeError):
    """A provider route cannot satisfy a typed request capability."""


class ProviderCallError(AIRuntimeError):
    """Sanitized provider failure with retry and billing uncertainty metadata."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        request_started: bool = False,
        billing_unknown: bool = False,
        status_code: int | None = None,
        provider_request_id: str = "",
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.request_started = request_started
        self.billing_unknown = billing_unknown
        self.status_code = status_code
        self.provider_request_id = provider_request_id
        self.budget_reservation_id = ""
        self.budget_reservation_day = ""
        self.budget_reserved_amount_usd = ""
