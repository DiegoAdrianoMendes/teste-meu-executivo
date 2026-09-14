from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.template_renderer import (
    TemplateValidationError,
    render_template,
    validate_template,
)
from app.schemas.whatsapp import MessageTemplateUpdate


def test_renderer_accepts_only_declared_placeholders_and_escaped_braces() -> None:
    rendered = render_template(
        "Olá, {client_name}. {{Protocolo}}: {next_action}.",
        {"client_name": "Ana", "next_action": "enviar a negativa", "ignored": "x"},
    )

    assert rendered == "Olá, Ana. {Protocolo}: enviar a negativa."
    assert validate_template("{client_name} / {workspace_name}") == (
        "client_name",
        "workspace_name",
    )


@pytest.mark.parametrize(
    "template",
    [
        "{client.__class__}",
        "{client_name[0]}",
        "{client_name!r}",
        "{client_name:>20}",
        "{unknown_field}",
        "{client_name",
    ],
)
def test_renderer_rejects_nested_unknown_or_malformed_fields(template: str) -> None:
    with pytest.raises(TemplateValidationError):
        render_template(template, {"client_name": "Ana"})


def test_message_template_schema_rejects_an_unsafe_template_before_persistence() -> None:
    with pytest.raises(ValidationError):
        MessageTemplateUpdate(label="Unsafe", body="{client_name.__class__}")
