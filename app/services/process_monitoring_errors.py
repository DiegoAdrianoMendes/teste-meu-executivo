"""Hierarquia de erros do módulo de monitoramento processual.

Mora em módulo próprio para que `djen_datajud_clients` possa levantar erros de
domínio sem importar `process_monitoring_service`, que puxaria a cadeia inteira
de entitlement, alerta de WhatsApp e pós-venda para dentro do cliente HTTP.
"""


class ProcessMonitoringError(RuntimeError):
    """Base de todo erro esperado do módulo."""


class ProcessMonitoringLiveDeliveryBlockedError(ProcessMonitoringError):
    """O gate fail-closed do módulo está fechado — nenhuma chamada real permitida."""


class ProcessMonitoringInvalidOabError(ProcessMonitoringError):
    """OAB fora do formato canônico `N/UF`: recusada na captura, nunca mangada."""


class ProcessMonitoringUpstreamUnavailableError(ProcessMonitoringError):
    """A fonte externa não respondeu depois das tentativas: transporte, timeout, 429 ou 5xx.

    É transitória por definição. Quem chama pode pular esta OAB ou este processo
    e seguir para o próximo, porque a próxima janela do beat tenta de novo.
    """


class ProcessMonitoringUpstreamRejectedError(ProcessMonitoringError):
    """A fonte externa respondeu 4xx: o pedido está errado e repetir não conserta.

    Carrega o `status` porque a causa muda a conduta: 404 costuma ser sigla de
    tribunal inválida (S50), 401 é chave, 400 é OAB mal formada. Repetir qualquer
    um deles é desperdício, e por isso este erro nunca dispara nova tentativa.
    """

    def __init__(self, message: str, *, status: int, detail: str = "") -> None:
        super().__init__(message)
        self.status = status
        # Trecho do corpo da resposta (até 300 caracteres). Em 09/09/2026 o DJEN
        # respondeu 403 ao IP da AWS e o log só dizia o código; sem o corpo não dá
        # para distinguir bloqueio de WAF de pedido malformado.
        self.detail = detail


__all__ = [
    "ProcessMonitoringError",
    "ProcessMonitoringInvalidOabError",
    "ProcessMonitoringLiveDeliveryBlockedError",
    "ProcessMonitoringUpstreamRejectedError",
    "ProcessMonitoringUpstreamUnavailableError",
]
