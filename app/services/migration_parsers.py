"""
Parsers universais do Importador de Migração (Onda 3, Plano Mestre §12).

Só a metade AGNÓSTICA: reconhece formato de arquivo, não formato de
concorrente. CSV e XLSX são a mesma "tabela universal" — uma linha por
registro, com uma coluna `dominio` (aceita também `domain`) dizendo a qual
dos 13 domínios (`app.models.migration_job.MigrationDomain`) a linha
pertence; sem essa coluna, o chamador informa um domínio-padrão para o
arquivo inteiro (upload de uma única planilha por domínio). ZIP é tratado
como lote de documentos brutos (cada entrada vira uma linha `domain =
documentos` com nome/tamanho/hash, nunca o conteúdo interpretado — DMS
jurídico é Onda 7). JSON aceita `{"<dominio>": [ {...}, ... ], ...}` ou uma
lista plana de objetos com uma chave `dominio`/`domain` cada.

XLSX segue o MESMO racional honesto de `app/services/finance_document_parser.py`
(F14) para PDF: biblioteca opcional checada via `importlib.util.find_spec`
— se `openpyxl` não estiver no venv, nenhuma linha é inventada, o
`ParseResult` volta com `warning` e zero linhas, e o job vira `erro` com a
mensagem honesta em vez de fingir que leu a planilha.
"""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import io
import json as json_module
import zipfile
from dataclasses import dataclass, field
from typing import Any, Optional

from app.core.config import settings
from app.core.request_body import (
    ArchiveSafetyLimits,
    UnsafeArchiveError,
    inspect_zip_archive,
)

XLSX_PARSER_UNAVAILABLE_MESSAGE = (
    "Arquivo XLSX recebido, mas a biblioteca openpyxl não está disponível "
    "neste ambiente — nenhuma linha foi inventada. Reenvie em CSV ou "
    "instale openpyxl (aguardando lib)."
)

_DOMAIN_COLUMN_ALIASES = ("dominio", "domain")


def openpyxl_available() -> bool:
    return importlib.util.find_spec("openpyxl") is not None


def sha256_hex(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_archive_entry(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> str:
    digest = hashlib.sha256()
    with archive.open(info) as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


class UnsupportedMigrationFormatError(ValueError):
    pass


def _inspect_import_archive(content: bytes) -> None:
    try:
        inspect_zip_archive(
            content,
            limits=ArchiveSafetyLimits(
                max_entries=settings.IMPORT_ARCHIVE_MAX_ENTRIES,
                max_uncompressed_bytes=settings.IMPORT_ARCHIVE_MAX_UNCOMPRESSED_BYTES,
                max_compression_ratio=settings.IMPORT_ARCHIVE_MAX_COMPRESSION_RATIO,
                max_entry_uncompressed_bytes=settings.MIGRATION_UPLOAD_MAX_BYTES,
            ),
        )
    except UnsafeArchiveError as error:
        raise UnsupportedMigrationFormatError(str(error)) from error


@dataclass
class ParsedRow:
    domain_hint: Optional[str]
    data: dict[str, Any]
    row_number: int


@dataclass
class ParseResult:
    format_detected: str
    rows: list[ParsedRow] = field(default_factory=list)
    warning: Optional[str] = None


def detect_format(filename: str, content_type: Optional[str], content: bytes) -> str:
    name = (filename or "").lower()
    if name.endswith(".csv"):
        return "csv"
    if name.endswith(".xlsx"):
        return "xlsx"
    if name.endswith(".zip"):
        return "zip"
    if name.endswith(".json"):
        return "json"
    ct = (content_type or "").lower()
    if "csv" in ct:
        return "csv"
    if "spreadsheet" in ct or "excel" in ct:
        return "xlsx"
    if "zip" in ct:
        return "zip"
    if "json" in ct:
        return "json"
    stripped = content.lstrip()
    if stripped[:1] in (b"{", b"["):
        return "json"
    if content[:2] == b"PK":
        return "zip"
    return "csv"


def _row_domain_hint(row: dict[str, Any], default_domain: Optional[str]) -> Optional[str]:
    for alias in _DOMAIN_COLUMN_ALIASES:
        if alias in row and str(row[alias]).strip():
            return str(row[alias]).strip().lower()
    return default_domain


def parse_csv(content: bytes, *, default_domain: Optional[str] = None) -> ParseResult:
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    rows: list[ParsedRow] = []
    for index, raw_row in enumerate(reader, start=1):
        normalized_row = {
            (key or "").strip().lower(): (value.strip() if isinstance(value, str) else value)
            for key, value in raw_row.items()
            if key is not None
        }
        rows.append(
            ParsedRow(
                domain_hint=_row_domain_hint(normalized_row, default_domain),
                data=normalized_row,
                row_number=index,
            )
        )
    return ParseResult(format_detected="csv", rows=rows)


def parse_xlsx(content: bytes, *, default_domain: Optional[str] = None) -> ParseResult:
    if not openpyxl_available():
        return ParseResult(format_detected="xlsx", rows=[], warning=XLSX_PARSER_UNAVAILABLE_MESSAGE)
    _inspect_import_archive(content)

    import openpyxl  # import local: só quando a lib está de fato disponível

    workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    sheet = workbook.worksheets[0]
    rows_iter = sheet.iter_rows(values_only=True)
    try:
        header_row = next(rows_iter)
    except StopIteration:
        return ParseResult(format_detected="xlsx", rows=[])
    header = [str(cell).strip().lower() if cell is not None else "" for cell in header_row]

    rows: list[ParsedRow] = []
    for index, values in enumerate(rows_iter, start=1):
        row_dict = {
            header[i]: ("" if values[i] is None else values[i])
            for i in range(min(len(header), len(values)))
            if header[i]
        }
        if not any(str(value).strip() for value in row_dict.values()):
            continue
        rows.append(
            ParsedRow(
                domain_hint=_row_domain_hint(row_dict, default_domain),
                data=row_dict,
                row_number=index,
            )
        )
    return ParseResult(format_detected="xlsx", rows=rows)


def parse_json(content: bytes, *, default_domain: Optional[str] = None) -> ParseResult:
    payload = json_module.loads(content.decode("utf-8"))
    rows: list[ParsedRow] = []
    counter = 0
    if isinstance(payload, dict):
        for domain_key, entries in payload.items():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                counter += 1
                rows.append(
                    ParsedRow(domain_hint=str(domain_key).lower(), data=entry, row_number=counter)
                )
    elif isinstance(payload, list):
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            counter += 1
            domain_hint = entry.get("dominio") or entry.get("domain") or default_domain
            data = {k: v for k, v in entry.items() if k not in ("dominio", "domain")}
            rows.append(
                ParsedRow(
                    domain_hint=str(domain_hint).lower() if domain_hint else None,
                    data=data,
                    row_number=counter,
                )
            )
    else:
        raise UnsupportedMigrationFormatError("JSON deve ser um objeto ou uma lista.")
    return ParseResult(format_detected="json", rows=rows)


def parse_zip_docs(content: bytes) -> ParseResult:
    rows: list[ParsedRow] = []
    warning = None
    try:
        _inspect_import_archive(content)
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            for index, info in enumerate(archive.infolist(), start=1):
                if info.is_dir():
                    continue
                rows.append(
                    ParsedRow(
                        domain_hint="documentos",
                        data={
                            "nome_arquivo": info.filename,
                            "tamanho_bytes": info.file_size,
                            "sha256": _sha256_archive_entry(archive, info),
                        },
                        row_number=index,
                    )
                )
    except zipfile.BadZipFile:
        warning = "Arquivo ZIP corrompido ou ilegível — nenhum documento foi extraído."
    return ParseResult(format_detected="zip", rows=rows, warning=warning)


def parse_content(
    *,
    filename: str,
    content_type: Optional[str],
    content: bytes,
    default_domain: Optional[str] = None,
) -> ParseResult:
    """Detecta o formato e devolve as linhas cruas — nenhuma normalização
    de domínio acontece aqui (fica em `MigrationService`, que conhece as
    regras de negócio por domínio)."""
    fmt = detect_format(filename, content_type, content)
    if fmt == "csv":
        return parse_csv(content, default_domain=default_domain)
    if fmt == "xlsx":
        return parse_xlsx(content, default_domain=default_domain)
    if fmt == "zip":
        return parse_zip_docs(content)
    if fmt == "json":
        return parse_json(content, default_domain=default_domain)
    raise UnsupportedMigrationFormatError(f"Formato não reconhecido: {fmt}")
