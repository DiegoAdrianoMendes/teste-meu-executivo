from __future__ import annotations

import enum
from dataclasses import dataclass

from fastapi import HTTPException


class WhatsAppProviderError(Exception):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        status_code: int = 502,
        provider_status_code: int | None = None,
        provider_error_subcode: int | None = None,
        request_started: bool = False,
        delivery_outcome_known: bool = True,
        connection_fault: bool = False,
        http_status: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.provider_status_code = provider_status_code
        self.provider_error_subcode = provider_error_subcode
        self.request_started = request_started
        self.delivery_outcome_known = delivery_outcome_known
        self.connection_fault = connection_fault
        # S16: o status HTTP real da Meta era descartado (400/502); quem decide
        # retentativa precisa dele (429) e do Retry-After quando existir.
        self.http_status = http_status
        self.retry_after = retry_after

    @property
    def delivery_uncertain(self) -> bool:
        return self.request_started and not self.delivery_outcome_known

    @property
    def connection_unhealthy(self) -> bool:
        return self.connection_fault

    def to_http_exception(self) -> HTTPException:
        return HTTPException(
            status_code=self.status_code,
            detail={"code": self.code, "message": self.message},
        )


class MetaErrorDisposition(str, enum.Enum):
    """Como o motor de campanhas deve reagir a um código de erro da Graph API."""

    retry_job = "retry_job"  # comportamento padrão: retry do job com backoff
    retry_batch = "retry_batch"  # throughput: reagendar o LOTE inteiro com backoff
    skip_terminal = "skip_terminal"  # destinatário pulado em definitivo, sem retry
    fail_terminal = "fail_terminal"  # falha definitiva do destinatário, sem retry
    pause_campaign = "pause_campaign"  # problema de template: pausar a campanha
    halt_campaign = "halt_campaign"  # conta restrita/bloqueada: PARADA TOTAL


@dataclass(frozen=True)
class MetaErrorPolicy:
    """Política de tratamento para um código de erro numérico da Meta."""

    provider_code: int | None
    disposition: MetaErrorDisposition
    reason: str
    suppress_contact: bool = False  # gerar supressão permanente do telefone
    invalidate_member: bool = False  # invalidar o membro nas listas de audiência


# Mapa de códigos da Graph API (error.code / error.error_subcode) para política.
# Referência: auditoria Meta 23/07/2026 (Plano Meta Máximo, W1.2).
_META_ERROR_POLICIES: dict[int, MetaErrorPolicy] = {
    131047: MetaErrorPolicy(
        provider_code=131047,
        disposition=MetaErrorDisposition.fail_terminal,
        reason=(
            "Janela de 24 horas fechada: o envio livre não é permitido e "
            "exige template aprovado pela Meta."
        ),
    ),
    131049: MetaErrorPolicy(
        provider_code=131049,
        disposition=MetaErrorDisposition.skip_terminal,
        reason=(
            "A Meta limitou mensagens de marketing para este usuário "
            "(cap por destinatário). Destinatário pulado sem retry."
        ),
    ),
    131050: MetaErrorPolicy(
        provider_code=131050,
        disposition=MetaErrorDisposition.skip_terminal,
        reason=(
            "O destinatário optou por não receber mensagens de marketing. "
            "Telefone suprimido permanentemente."
        ),
        suppress_contact=True,
    ),
    131026: MetaErrorPolicy(
        provider_code=131026,
        disposition=MetaErrorDisposition.fail_terminal,
        reason=(
            "Destinatário inválido ou sem WhatsApp ativo. "
            "Membro marcado como inválido nas listas de audiência."
        ),
        invalidate_member=True,
    ),
    132000: MetaErrorPolicy(
        provider_code=132000,
        disposition=MetaErrorDisposition.pause_campaign,
        reason=(
            "O número de parâmetros do template não confere com o modelo "
            "aprovado (template inexistente ou desatualizado). Campanha pausada."
        ),
    ),
    132001: MetaErrorPolicy(
        provider_code=132001,
        disposition=MetaErrorDisposition.pause_campaign,
        reason=(
            "Template inexistente ou não aprovado para o idioma informado. "
            "Campanha pausada até a correção do template."
        ),
    ),
    130429: MetaErrorPolicy(
        provider_code=130429,
        disposition=MetaErrorDisposition.retry_batch,
        reason=(
            "Limite de vazão (throughput) da Meta atingido. "
            "Lote reagendado com backoff."
        ),
    ),
    # S16: os outros dois códigos de throttling documentados pela Meta tinham o
    # tratamento padrão (retry do job, consumindo tentativa). Mesma disposição
    # do 130429: reagendar o lote sem gastar tentativa.
    80007: MetaErrorPolicy(
        provider_code=80007,
        disposition=MetaErrorDisposition.retry_batch,
        reason=(
            "Limite de chamadas da conta do WhatsApp Business atingido (80007). "
            "Lote reagendado com backoff."
        ),
    ),
    4: MetaErrorPolicy(
        provider_code=4,
        disposition=MetaErrorDisposition.retry_batch,
        reason=(
            "Limite de chamadas da aplicação atingido (código 4). "
            "Lote reagendado com backoff."
        ),
    ),
    368: MetaErrorPolicy(
        provider_code=368,
        disposition=MetaErrorDisposition.halt_campaign,
        reason=(
            "Conta temporariamente restrita pela Meta por violação de política. "
            "Campanha interrompida imediatamente."
        ),
    ),
    131031: MetaErrorPolicy(
        provider_code=131031,
        disposition=MetaErrorDisposition.halt_campaign,
        reason=(
            "Conta do WhatsApp Business bloqueada ou restrita pela Meta. "
            "Campanha interrompida imediatamente."
        ),
    ),
    # 190/102 (auth/OAuth): já classificados como connection_fault no cliente;
    # o motor mantém o retry com backoff enquanto a conexão é sinalizada.
    190: MetaErrorPolicy(
        provider_code=190,
        disposition=MetaErrorDisposition.retry_job,
        reason="Token de acesso expirado ou inválido (OAuth). Conexão sinalizada.",
    ),
    102: MetaErrorPolicy(
        provider_code=102,
        disposition=MetaErrorDisposition.retry_job,
        reason="Sessão de API inválida (OAuth). Conexão sinalizada.",
    ),
}

_META_ERROR_POLICY_DEFAULT = MetaErrorPolicy(
    provider_code=None,
    disposition=MetaErrorDisposition.retry_job,
    reason="Erro não mapeado da Graph API. Retry com backoff do job.",
)


def classify_meta_error(
    provider_code: int | None,
    provider_subcode: int | None = None,
) -> MetaErrorPolicy:
    """Resolve a política de tratamento para um erro numérico da Meta.

    Consulta primeiro o subcódigo (error.error_subcode) e depois o código
    principal (error.code); códigos desconhecidos caem no retry padrão.
    """
    if provider_subcode is not None and provider_subcode in _META_ERROR_POLICIES:
        return _META_ERROR_POLICIES[provider_subcode]
    if provider_code is not None and provider_code in _META_ERROR_POLICIES:
        return _META_ERROR_POLICIES[provider_code]
    return _META_ERROR_POLICY_DEFAULT
