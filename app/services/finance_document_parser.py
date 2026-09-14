"""
Parser: documentos financeiros para Harmonização (F14) — ver
`app/services/reconciliation_service.py` (F7) para o parser irmão de
extrato bancário puro (OFX/CSV/Asaas), que esta harmonização ESTENDE em vez
de duplicar: aqui o alvo é qualquer documento financeiro (extrato ou DRE) em
PDF, XML (CNAB/OFX-XML) ou CSV, normalizado para comparação contra o
financeiro local (`FinanceEntry` + `BankTransaction`) em
`app/services/finance_harmonization_service.py`.

Fingerprint segue o mesmo padrão de `BankTransaction.external_fingerprint`
(F7): hash determinístico de (data, valor, descrição) quando o documento não
carrega um identificador nativo, e o identificador nativo (`xml:<id>`)
quando disponível — evita depender de outra linha do mesmo documento para
detectar duplicata em reimportação.

PDF: extração de texto via `pypdf`, se presente no venv (checar sempre por
`importlib.util.find_spec` — nunca assumir instalado). Sem a lib, o
documento entra como `aguardando_parser`: nenhuma linha é inventada, o run
guarda a mensagem honesta em `FinanceHarmonizationRun.parser_warning` e seue
para a verificação com zero linhas de documento (tudo vira `so_no_sistema`),
sem quebrar o upload.
"""
from __future__ import annotations

import csv
import hashlib
import importlib.util
import io
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

PDF_PARSER_UNAVAILABLE_MESSAGE = (
    "PDF recebido, mas nenhuma biblioteca de extração de texto (pypdf) está "
    "disponível neste ambiente — o documento foi registrado como "
    "'aguardando_parser' sem nenhuma linha inventada. Reenvie em CSV/XML ou "
    "instale `pypdf` para extrair o texto."
)

# Limite defensivo contra XML malformado/hostil (billion-laughs, arquivos
# gigantes) — documentos financeiros legítimos não chegam perto disso.
_MAX_XML_BYTES = 20 * 1024 * 1024
_MAX_XML_ELEMENTS = 200_000

_XML_TRANSACTION_TAGS = {
    "transacao",
    "transaction",
    "lancamento",
    "movimento",
    "mov",
    "entry",
    "trn",
    "stmttrn",
    "item",
    "lancamentoextrato",
}
_XML_DATE_TAGS = (
    "date",
    "data",
    "dtposted",
    "datamovimento",
    "datalancamento",
    "dataoperacao",
    "dt",
)
_XML_VALUE_TAGS = (
    "value",
    "valor",
    "amount",
    "trnamt",
    "vlrlancamento",
    "valorlancamento",
    "vlr",
)
_XML_DESCRIPTION_TAGS = (
    "description",
    "descricao",
    "historico",
    "memo",
    "name",
    "complemento",
)
_XML_ID_TAGS = ("id", "idlancamento", "numerodocumento", "fitid", "documento", "nsu")


@dataclass
class ParsedDocumentRow:
    date: date
    value: Decimal
    description: Optional[str]
    fingerprint: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedDocument:
    rows: list[ParsedDocumentRow]
    warning: Optional[str] = None


def _hash_fingerprint(row_date: date, value: Decimal, description: Optional[str]) -> str:
    """Mesmo esquema de `ReconciliationService._hash_fingerprint` (F7)."""
    payload = f"{row_date.isoformat()}|{value}|{(description or '').strip().lower()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _parse_flexible_date(raw: str) -> Optional[date]:
    raw = raw.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    digits = re.sub(r"[^0-9]", "", raw)
    if len(digits) >= 8:
        try:
            return date(int(digits[0:4]), int(digits[4:6]), int(digits[6:8]))
        except ValueError:
            return None
    return None


def _parse_decimal(raw: str) -> Optional[Decimal]:
    cleaned = raw.strip().replace("R$", "").strip()
    # Formato BR (1.234,56) vs formato US/OFX (1234.56) — decide pelo último
    # separador decimal presente.
    if "," in cleaned and cleaned.rfind(",") > cleaned.rfind("."):
        cleaned = cleaned.replace(".", "").replace(",", ".")
    else:
        cleaned = cleaned.replace(",", "")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


# ── CSV ──


def parse_csv_document(raw_text: str) -> ParsedDocument:
    rows: list[ParsedDocumentRow] = []
    reader = csv.DictReader(io.StringIO(raw_text))
    for record in reader:
        normalized = {(k or "").strip().lower(): (v or "").strip() for k, v in record.items()}
        raw_date = normalized.get("date") or normalized.get("data")
        raw_value = normalized.get("value") or normalized.get("valor")
        description = (
            normalized.get("description")
            or normalized.get("descricao")
            or normalized.get("descrição")
            or normalized.get("historico")
        )
        if not raw_date or not raw_value:
            continue
        parsed_date = _parse_flexible_date(raw_date)
        parsed_value = _parse_decimal(raw_value)
        if parsed_date is None or parsed_value is None:
            continue
        doc_id = normalized.get("id") or normalized.get("documento")
        fingerprint = f"csv-doc:{doc_id}" if doc_id else _hash_fingerprint(parsed_date, parsed_value, description)
        rows.append(
            ParsedDocumentRow(
                date=parsed_date,
                value=parsed_value,
                description=description,
                fingerprint=fingerprint,
                raw=dict(normalized),
            )
        )
    return ParsedDocument(rows=rows)


# ── XML (CNAB/OFX-XML padrão de extrato) ──


def _local_tag(element: ET.Element) -> str:
    tag = element.tag
    if "}" in tag:
        tag = tag.split("}", 1)[1]
    return tag.strip().lower()


def _child_text_by_tag(element: ET.Element, candidates: tuple[str, ...]) -> Optional[str]:
    children = {_local_tag(child): (child.text or "").strip() for child in list(element)}
    for candidate in candidates:
        if candidate in children and children[candidate]:
            return children[candidate]
    # também aceita atributos (alguns extratos XML carregam data/valor como atributo)
    attrs = {k.lower(): v for k, v in element.attrib.items()}
    for candidate in candidates:
        if candidate in attrs and attrs[candidate]:
            return attrs[candidate]
    return None


def parse_xml_document(raw_bytes: bytes) -> ParsedDocument:
    if len(raw_bytes) > _MAX_XML_BYTES:
        return ParsedDocument(rows=[], warning="Arquivo XML excede o tamanho máximo suportado (20MB).")
    try:
        root = ET.fromstring(raw_bytes)
    except ET.ParseError as exc:
        return ParsedDocument(rows=[], warning=f"XML malformado — não foi possível interpretar o arquivo ({exc}).")

    rows: list[ParsedDocumentRow] = []
    element_count = 0
    for element in root.iter():
        element_count += 1
        if element_count > _MAX_XML_ELEMENTS:
            return ParsedDocument(
                rows=rows, warning="XML excede o número máximo de elementos suportado — leitura interrompida."
            )
        if _local_tag(element) not in _XML_TRANSACTION_TAGS:
            continue

        raw_date = _child_text_by_tag(element, _XML_DATE_TAGS)
        raw_value = _child_text_by_tag(element, _XML_VALUE_TAGS)
        if not raw_date or not raw_value:
            continue
        parsed_date = _parse_flexible_date(raw_date)
        parsed_value = _parse_decimal(raw_value)
        if parsed_date is None or parsed_value is None:
            continue

        description = _child_text_by_tag(element, _XML_DESCRIPTION_TAGS)
        doc_id = _child_text_by_tag(element, _XML_ID_TAGS)
        fingerprint = f"xml:{doc_id}" if doc_id else _hash_fingerprint(parsed_date, parsed_value, description)
        raw_payload = {_local_tag(child): (child.text or "").strip() for child in list(element)}
        rows.append(
            ParsedDocumentRow(
                date=parsed_date,
                value=parsed_value,
                description=description,
                fingerprint=fingerprint,
                raw=raw_payload,
            )
        )
    return ParsedDocument(rows=rows)


# ── PDF (extração de texto best-effort) ──


def _pypdf_available() -> bool:
    return importlib.util.find_spec("pypdf") is not None


_PDF_LINE_RE = re.compile(
    r"(?P<date>\d{2}[/-]\d{2}[/-]\d{2,4})\s+(?P<description>.+?)\s+(?P<value>-?R?\$?\s?-?\d{1,3}(?:[.,]\d{3})*[.,]\d{2})\s*$"
)


def parse_pdf_document(raw_bytes: bytes) -> ParsedDocument:
    if not _pypdf_available():
        return ParsedDocument(rows=[], warning=PDF_PARSER_UNAVAILABLE_MESSAGE)

    import pypdf  # import tardio: só quando a lib está confirmada presente

    try:
        reader = pypdf.PdfReader(io.BytesIO(raw_bytes))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:  # noqa: BLE001 — PDF corrompido/protegido não pode derrubar o upload
        return ParsedDocument(
            rows=[], warning=f"Não foi possível extrair texto do PDF ({exc}) — nenhuma linha foi inventada."
        )

    rows: list[ParsedDocumentRow] = []
    for line in text.splitlines():
        match = _PDF_LINE_RE.search(line.strip())
        if not match:
            continue
        parsed_date = _parse_flexible_date(match.group("date"))
        parsed_value = _parse_decimal(match.group("value"))
        if parsed_date is None or parsed_value is None:
            continue
        description = match.group("description").strip() or None
        rows.append(
            ParsedDocumentRow(
                date=parsed_date,
                value=parsed_value,
                description=description,
                fingerprint=_hash_fingerprint(parsed_date, parsed_value, description),
                raw={"line": line.strip()},
            )
        )
    warning = None if rows else "Nenhuma linha de transação reconhecida no texto extraído do PDF."
    return ParsedDocument(rows=rows, warning=warning)


def parse_document(*, source_type: str, filename: str, raw_bytes: bytes) -> ParsedDocument:
    """Roteia por formato (extensão/`source_type`) — porta única do parser F14."""
    if source_type == "csv":
        return parse_csv_document(raw_bytes.decode("utf-8", errors="ignore"))
    if source_type == "xml":
        return parse_xml_document(raw_bytes)
    if source_type == "pdf":
        return parse_pdf_document(raw_bytes)
    raise ValueError(f"Formato de documento não suportado: {source_type!r} ({filename})")
