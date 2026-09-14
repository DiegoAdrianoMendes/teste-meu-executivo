"""Contrato de execução local do KLM Hub."""

import os
from ipaddress import ip_address
from typing import Any

from app.core.config import settings

EXTERNAL_ACTION_BLOCKED_CODE = "KLM-EXT-001"

# Env vars removidas de Settings. Como extra="ignore", elas continuam sendo
# aceitas pelo ambiente sem efeito nenhum; warn_retired_flags() faz o silêncio
# virar aviso na subida.
_RETIRED_FLAGS: dict[str, tuple[str, ...]] = {
    "KLM_PROCESS_MONITORING_LIVE_ENABLED": (
        "KLM_DJEN_LIVE_ENABLED",
        "KLM_DATAJUD_LIVE_ENABLED",
    ),
}


class ExternalActionBlockedError(RuntimeError):
    """Raised before an integration can create an outbound network client."""

    code = EXTERNAL_ACTION_BLOCKED_CODE

    def __init__(self, action: str) -> None:
        super().__init__(
            f"{action} bloqueada enquanto as ações externas do Meu Executivo "
            "estiverem desabilitadas."
        )


def get_runtime_status() -> dict[str, Any]:
    external_actions_enabled = external_delivery_allowed()
    private_aws_storage_enabled = aws_private_storage_allowed()
    aws_enabled = aws_external_access_allowed()
    local_fake_enabled = local_fake_model_execution_allowed()
    return {
        "mode": settings.KLM_RUNTIME_MODE,
        "localOnly": settings.is_local_only,
        "localInfrastructure": settings.uses_local_infrastructure,
        "externalActionsEnabled": external_actions_enabled,
        "privateAwsStorageEnabled": private_aws_storage_enabled,
        "awsEnabled": aws_enabled,
        "modelApisEnabled": bool(settings.KLM_MODEL_APIS_ENABLED and settings.KLM_ONLINE_APPROVED),
        "onlineApproved": settings.KLM_ONLINE_APPROVED,
        "localBootstrapEnabled": settings.KLM_LOCAL_BOOTSTRAP_ENABLED,
        "djenLiveEnabled": djen_live_delivery_allowed(),
        "datajudLiveEnabled": datajud_live_delivery_allowed(),
        "providers": {
            "whatsapp": "offline",
            "email": "offline",
            "zapsign": "offline",
            "asaas": "offline",
            "googleAds": "offline",
            "metaAds": "offline",
            "tiktokAds": "offline",
            "elevenLabs": "offline",
            "googleWorkspace": "offline",
            "aws": "offline",
            "models": (
                "local_fake"
                if local_fake_enabled
                else (
                    "online_approved"
                    if settings.KLM_MODEL_APIS_ENABLED and settings.KLM_ONLINE_APPROVED
                    else "offline"
                )
            ),
        },
    }


def external_delivery_allowed() -> bool:
    return bool(
        not settings.uses_local_infrastructure
        and settings.KLM_ONLINE_APPROVED
        and settings.KLM_EXTERNAL_ACTIONS_ENABLED
    )


def require_external_delivery_allowed(action: str) -> None:
    """Fail closed at an external integration boundary."""
    if not external_delivery_allowed():
        raise ExternalActionBlockedError(action)


def aws_external_access_allowed() -> bool:
    """Require the complete external gate before any AWS client may be used."""
    return bool(settings.KLM_AWS_ENABLED and external_delivery_allowed())


def aws_private_storage_allowed() -> bool:
    """Allow only the application's private S3/KMS boundary.

    This gate deliberately does not depend on online/provider approval: private
    persistence is internal infrastructure, not an outbound business effect.
    It still fails closed in local simulation and without an explicit KMS key.
    """
    return bool(
        settings.KLM_PRIVATE_AWS_STORAGE_ENABLED
        and not settings.uses_local_infrastructure
        and settings.S3_KMS_KEY_ID.strip()
    )


def local_fake_model_execution_allowed() -> bool:
    """Allow only the in-memory fake while every online and external gate is closed."""
    return bool(
        settings.AI_PROVIDER == "fake"
        and settings.KLM_RUNTIME_MODE == "local_simulation"
        and not settings.KLM_ONLINE_APPROVED
        and settings.is_local_only
    )


def external_model_api_execution_allowed() -> bool:
    """Allow a real model API only after both explicit model gates are open."""
    return bool(settings.KLM_ONLINE_APPROVED and settings.KLM_MODEL_APIS_ENABLED)


def model_api_execution_allowed() -> bool:
    """Allow model reasoning without implicitly opening any delivery channel."""
    return bool(
        local_fake_model_execution_allowed()
        or external_model_api_execution_allowed()
    )


def djen_live_delivery_allowed() -> bool:
    """Fail closed unless the general external gate AND the DJEN flag are open.

    Separate from DataJud on purpose: they are independent sources, with
    independent credentials and availability. Opening one must never open
    the other.
    """
    return bool(external_delivery_allowed() and settings.KLM_DJEN_LIVE_ENABLED)


def datajud_live_delivery_allowed() -> bool:
    """Fail closed unless the external gate, the DataJud flag AND the API key
    are all present.

    The key belongs inside the gate, not beside it: same shape as
    aws_private_storage_allowed(), which requires S3_KMS_KEY_ID. A flag turned
    on without a key is a configuration mistake, and config.py refuses to boot
    on it rather than letting a 4am task discover it.
    """
    return bool(
        external_delivery_allowed()
        and settings.KLM_DATAJUD_LIVE_ENABLED
        and settings.DATAJUD_API_KEY.strip()
    )


def warn_retired_flags() -> None:
    """Warn once at boot when a retired KLM_* env var is still set.

    Settings uses extra="ignore", so a leftover variable is silently inert.
    Inert in the fail-closed direction, but invisible: whoever set it believes
    it still does something.
    """
    from app.core.logging import logger

    for aposentada, substitutas in _RETIRED_FLAGS.items():
        if aposentada in os.environ:
            logger.warning(
                "klm_runtime.retired_flag_ignored",
                extra={"flag": aposentada, "substitutas": ", ".join(substitutas)},
            )


def automation_worker_allowed() -> bool:
    """Run typed flows when either internal AI or an external channel is approved."""
    return bool(model_api_execution_allowed() or external_delivery_allowed())


def local_bootstrap_allowed(client_host: str) -> bool:
    if (
        not settings.is_development
        or not settings.is_local_only
        or not settings.KLM_LOCAL_BOOTSTRAP_ENABLED
    ):
        return False
    try:
        address = ip_address(client_host)
    except ValueError:
        return client_host in {"localhost", "testclient", "test"}
    # Private/LAN addresses are not trusted: the endpoint issues an owner
    # session without credentials and must remain process-local.
    return address.is_loopback
