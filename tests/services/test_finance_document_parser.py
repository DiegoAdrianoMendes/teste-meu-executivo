from __future__ import annotations

from datetime import date
from decimal import Decimal

import app.services.finance_document_parser as finance_document_parser
from app.services.finance_document_parser import (
    PDF_PARSER_UNAVAILABLE_MESSAGE,
    parse_csv_document,
    parse_document,
    parse_pdf_document,
    parse_xml_document,
)

CSV_SAMPLE = "date,value,description\n2026-07-02,250.00,Aluguel escritorio\n2026-07-03,1500,Honorarios cliente\n"

XML_SAMPLE = """<?xml version="1.0"?>
<extrato>
  <lancamento>
    <data>2026-07-02</data>
    <valor>250.00</valor>
    <descricao>Aluguel escritorio</descricao>
  </lancamento>
  <lancamento>
    <data>05/07/2026</data>
    <valor>1.500,00</valor>
    <descricao>Honorarios cliente</descricao>
  </lancamento>
</extrato>
"""


def test_parse_csv_document_normalizes_rows() -> None:
    parsed = parse_csv_document(CSV_SAMPLE)
    assert parsed.warning is None
    assert len(parsed.rows) == 2
    first = parsed.rows[0]
    assert first.date == date(2026, 7, 2)
    assert first.value == Decimal("250.00")
    assert first.description == "Aluguel escritorio"
    assert first.fingerprint  # hash determinístico, nunca vazio


def test_parse_csv_document_is_deterministic_for_same_row() -> None:
    parsed_a = parse_csv_document(CSV_SAMPLE)
    parsed_b = parse_csv_document(CSV_SAMPLE)
    assert parsed_a.rows[0].fingerprint == parsed_b.rows[0].fingerprint


def test_parse_xml_document_normalizes_br_and_iso_dates_and_values() -> None:
    parsed = parse_xml_document(XML_SAMPLE.encode("utf-8"))
    assert parsed.warning is None
    assert len(parsed.rows) == 2
    assert parsed.rows[0].value == Decimal("250.00")
    assert parsed.rows[1].date == date(2026, 7, 5)
    assert parsed.rows[1].value == Decimal("1500.00")


def test_parse_xml_document_malformed_returns_honest_warning_without_rows() -> None:
    parsed = parse_xml_document(b"<extrato><lancamento>")
    assert parsed.rows == []
    assert parsed.warning is not None


def test_parse_pdf_document_without_pypdf_is_honest_and_never_invents_rows(
    monkeypatch,
) -> None:
    monkeypatch.setattr(finance_document_parser, "_pypdf_available", lambda: False)
    parsed = parse_pdf_document(b"%PDF-1.4 fake content")
    assert parsed.rows == []
    assert parsed.warning == PDF_PARSER_UNAVAILABLE_MESSAGE


def test_parse_document_routes_by_source_type() -> None:
    parsed = parse_document(source_type="csv", filename="extrato.csv", raw_bytes=CSV_SAMPLE.encode("utf-8"))
    assert len(parsed.rows) == 2
