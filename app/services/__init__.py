"""Exports lazy dos serviços de domínio.

Importar ``app.services`` não deve inicializar S3, documentos e a pilha de IA.
Os nomes históricos continuam disponíveis para consumidores legados.
"""
from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "AuthService": ("app.services.auth_service", "AuthService"),
    "MatterService": ("app.services.matter_service", "MatterService"),
    "TaskService": ("app.services.task_service", "TaskService"),
    "DocumentService": ("app.services.document_service", "DocumentService"),
    "ApprovalService": ("app.services.approval_service", "ApprovalService"),
    "ReportsService": ("app.services.reports_service", "ReportsService"),
    "S3Service": ("app.services.s3_service", "S3Service"),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    try:
        module_name, attribute_name = _EXPORTS[name]
    except KeyError as error:
        raise AttributeError(name) from error
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted([*globals(), *_EXPORTS])
