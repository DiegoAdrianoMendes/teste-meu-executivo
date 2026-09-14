"""
Configuração de logging estruturado com structlog.
"""
import logging
import sys

import structlog

from app.core.config import settings
from app.core.logging_privacy import RedactSensitiveUrlParameters

_configured = False


def setup_logging(*, force: bool = False) -> None:
    """Configura o structlog para logging estruturado em JSON (prod) ou colorido (dev).

    Idempotente: a API chama no startup e o worker/beat pelo sinal do Celery; um
    serviço que importe `celery_app` dentro da API não reconfigura."""
    global _configured
    if _configured and not force:
        return
    _configured = True
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    if settings.is_production:
        processors = shared_processors + [structlog.processors.JSONRenderer()]
    else:
        processors = shared_processors + [structlog.dev.ConsoleRenderer()]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )

    # O httpx inclui a URL completa no log de request. Alguns provedores OAuth
    # exigem credenciais em query string; o filtro impede que elas cheguem ao
    # stdout/CloudWatch sem remover a telemetria de método, host e status.
    httpx_logger = logging.getLogger("httpx")
    if not any(isinstance(item, RedactSensitiveUrlParameters) for item in httpx_logger.filters):
        httpx_logger.addFilter(RedactSensitiveUrlParameters())


logger = structlog.get_logger()
