from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

from scripts.verify_local_stack import (
    assert_compose_contract,
    assert_static_files,
    render_compose,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def compose_document() -> dict:
    return render_compose()


def _load_fake_provider() -> ModuleType:
    path = ROOT / "docker/fake-provider/server.py"
    spec = importlib.util.spec_from_file_location("klm_fake_provider", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_static_container_files_are_reproducible_and_non_root() -> None:
    assert_static_files()


def test_resolved_compose_is_local_only_and_resource_bounded(compose_document: dict) -> None:
    assert_compose_contract(compose_document)


def test_fake_provider_is_deterministic_and_does_not_echo_prompts() -> None:
    provider = _load_fake_provider()
    first_payload = {
        "model": "klm-fake-v1",
        "messages": [{"role": "user", "content": "private-client-fact"}],
        "temperature": 0.2,
    }
    reordered_payload = {
        "temperature": 0.2,
        "messages": [{"content": "private-client-fact", "role": "user"}],
        "model": "klm-fake-v1",
    }

    first = provider.build_openai_response(first_payload)
    second = provider.build_openai_response(reordered_payload)

    assert first == second
    assert first["id"].startswith("fake-")
    assert "private-client-fact" not in json.dumps(first)
    assert provider.build_anthropic_response(first_payload)["type"] == "message"
    assert provider.build_gemini_response(first_payload)["candidates"][0]["finishReason"] == (
        "STOP"
    )
