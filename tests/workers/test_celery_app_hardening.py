"""S12 e S14: logging do worker e retentativa dos erros transitórios reais."""

from __future__ import annotations

import httpx
import structlog
from celery.signals import setup_logging as celery_setup_logging
from sqlalchemy.exc import IntegrityError

import app.core.logging as logging_module
from app.workers.celery_app import TRANSIENT_ERRORS, _configure_worker_logging, celery_app


def test_worker_logging_is_configured_by_the_celery_signal(monkeypatch):
    """S12: o sinal `setup_logging` do Celery está conectado e configura o structlog
    (sem o sinal, o Celery sequestra o root logger no boot do worker/beat)."""
    assert any(
        getattr(receiver, "__name__", "") == _configure_worker_logging.__name__
        for receiver in (
            (ref() if callable(ref) and not hasattr(ref, "__name__") else ref)
            for _key, ref in celery_setup_logging.receivers
        )
        if receiver is not None
    ) or _configure_worker_logging in [
        (ref() if callable(ref) and not hasattr(ref, "__name__") else ref)
        for _key, ref in celery_setup_logging.receivers
    ]
    structlog.reset_defaults()
    monkeypatch.setattr(logging_module, "_configured", False)
    assert structlog.is_configured() is False
    celery_setup_logging.send(sender=None, loglevel=20, logfile=None, format="", colorize=False)
    assert structlog.is_configured() is True


def test_setup_logging_is_idempotent(monkeypatch):
    chamadas: list[int] = []
    original = structlog.configure

    def contar(**kwargs):
        chamadas.append(1)
        return original(**kwargs)

    monkeypatch.setattr(logging_module, "_configured", False)
    monkeypatch.setattr(structlog, "configure", contar)
    logging_module.setup_logging()
    logging_module.setup_logging()
    assert chamadas == [1]


def test_transient_errors_cover_the_stack_but_not_data_integrity():
    assert issubclass(httpx.ConnectError, TRANSIENT_ERRORS)
    assert issubclass(ConnectionRefusedError, TRANSIENT_ERRORS)
    assert not issubclass(httpx.ReadTimeout, TRANSIENT_ERRORS), "pode ter sido processado"
    assert not issubclass(IntegrityError, TRANSIENT_ERRORS)


def test_base_task_retries_httpx_connect_error_eagerly(monkeypatch):
    """S14, prova comportamental: a classe base retenta erro de conexão do httpx
    (antes, só builtins) e não retenta IntegrityError."""
    monkeypatch.setitem(celery_app.conf, "task_always_eager", True)
    monkeypatch.setitem(celery_app.conf, "task_eager_propagates", False)
    chamadas: list[str] = []

    @celery_app.task(
        name="tests.hardening.retenta_conexao",
        max_retries=1,
        retry_backoff=False,
        default_retry_delay=0,
    )
    def retenta_conexao():
        chamadas.append("x")
        if len(chamadas) == 1:
            raise httpx.ConnectError("Meta fora")
        return "ok"

    retenta_conexao.apply()
    assert len(chamadas) == 2

    chamadas.clear()

    @celery_app.task(name="tests.hardening.nao_retenta_integridade", max_retries=1)
    def nao_retenta_integridade():
        chamadas.append("x")
        raise IntegrityError("insert", {}, Exception("dup"))

    resultado = nao_retenta_integridade.apply()
    assert len(chamadas) == 1
    assert resultado.failed()
