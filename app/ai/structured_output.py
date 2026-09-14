"""Local JSON Schema validation applied after every structured model response."""
from __future__ import annotations

import json
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from app.ai.errors import AIConfigurationError, StructuredOutputError

MAX_SCHEMA_BYTES = 64 * 1024


def validate_response_schema(schema: dict[str, Any], *, contains_sensitive_data: bool) -> None:
    if not isinstance(schema, dict):
        raise AIConfigurationError("O schema de saída deve ser um objeto JSON Schema.")
    encoded = json.dumps(schema, ensure_ascii=False, sort_keys=True).encode("utf-8")
    if len(encoded) > MAX_SCHEMA_BYTES:
        raise AIConfigurationError("O schema de saída excede 64 KiB.")
    if contains_sensitive_data:
        raise AIConfigurationError(
            "Dados pessoais ou de saúde não podem ser incorporados ao schema de saída."
        )
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise AIConfigurationError("O JSON Schema de saída é inválido.") from exc


def parse_and_validate(text: str, schema: dict[str, Any]) -> Any:
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise StructuredOutputError("O provider não retornou JSON válido.") from exc
    try:
        Draft202012Validator(schema).validate(value)
    except ValidationError as exc:
        path = ".".join(str(item) for item in exc.absolute_path) or "$"
        raise StructuredOutputError(f"Saída estruturada inválida no caminho {path}.") from exc
    return value
