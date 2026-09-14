"""Strict renderer for user-editable outbound message templates."""

from __future__ import annotations

from collections.abc import Mapping
from string import Formatter
from typing import Any

ALLOWED_TEMPLATE_FIELDS = frozenset(
    {
        "client_cpf",
        "client_email",
        "client_name",
        "client_phone",
        "contract_link",
        "demand_type",
        "document_list",
        "executive_summary",
        "next_action",
        "practice_area",
        "responsible_name",
        "workspace_name",
    }
)
MAX_TEMPLATE_CHARACTERS = 32_000
MAX_FIELD_CHARACTERS = 12_000
MAX_RENDERED_CHARACTERS = 64_000


class TemplateValidationError(ValueError):
    """Raised when a template attempts unsupported formatting behavior."""


def validate_template(template: str) -> tuple[str, ...]:
    if len(template) > MAX_TEMPLATE_CHARACTERS:
        raise TemplateValidationError("Template exceeds the allowed size.")
    fields: list[str] = []
    try:
        parsed = Formatter().parse(template)
        for _literal, field_name, format_spec, conversion in parsed:
            if field_name is None:
                continue
            if not field_name or field_name not in ALLOWED_TEMPLATE_FIELDS:
                raise TemplateValidationError(
                    f"Placeholder {{{field_name}}} is not allowed."
                )
            if "." in field_name or "[" in field_name or "]" in field_name:
                raise TemplateValidationError("Nested placeholder access is not allowed.")
            if conversion is not None or format_spec:
                raise TemplateValidationError(
                    "Placeholder conversions and format specifications are not allowed."
                )
            fields.append(field_name)
    except ValueError as exc:
        if isinstance(exc, TemplateValidationError):
            raise
        raise TemplateValidationError("Template contains malformed braces.") from exc
    return tuple(fields)


def render_template(template: str, context: Mapping[str, Any]) -> str:
    validate_template(template)
    values: dict[str, str] = {}
    for key in ALLOWED_TEMPLATE_FIELDS:
        value = context.get(key)
        rendered = "" if value is None else str(value)
        if len(rendered) > MAX_FIELD_CHARACTERS:
            raise TemplateValidationError(f"Value for {key} exceeds the allowed size.")
        values[key] = rendered
    try:
        result = template.format_map(values)
    except (KeyError, ValueError) as exc:
        raise TemplateValidationError("Template could not be rendered safely.") from exc
    if len(result) > MAX_RENDERED_CHARACTERS:
        raise TemplateValidationError("Rendered message exceeds the allowed size.")
    return result
