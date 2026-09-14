from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_meta_graph_version_defaults_to_v26() -> None:
    configured = Settings(_env_file=None)

    assert configured.META_GRAPH_API_VERSION == "v26.0"
    assert configured.WHATSAPP_API_VERSION == "v26.0"


def test_legacy_whatsapp_version_is_a_one_release_alias() -> None:
    configured = Settings(_env_file=None, WHATSAPP_API_VERSION="v26.0")

    assert configured.META_GRAPH_API_VERSION == "v26.0"
    assert configured.WHATSAPP_API_VERSION == "v26.0"


def test_empty_legacy_alias_does_not_diverge_from_canonical_version() -> None:
    configured = Settings(
        _env_file=None,
        META_GRAPH_API_VERSION="v26.0",
        WHATSAPP_API_VERSION="",
    )

    assert configured.META_GRAPH_API_VERSION == "v26.0"
    assert configured.WHATSAPP_API_VERSION == "v26.0"


def test_meta_graph_version_rejects_invalid_format() -> None:
    with pytest.raises(ValidationError, match="META_GRAPH_API_VERSION"):
        Settings(_env_file=None, META_GRAPH_API_VERSION="25")


def test_meta_graph_version_rejects_divergent_alias() -> None:
    with pytest.raises(ValidationError, match="diverge"):
        Settings(
            _env_file=None,
            META_GRAPH_API_VERSION="v26.0",
            WHATSAPP_API_VERSION="v23.0",
        )
