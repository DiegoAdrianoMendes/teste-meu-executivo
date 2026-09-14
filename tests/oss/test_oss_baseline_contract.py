from __future__ import annotations

from pathlib import Path

from app.services import oss_capability_registry as runtime_registry
from scripts.oss_census import baseline
from scripts.oss_census.python_ast import parse_python


def test_static_baseline_native_modes_match_runtime_contract() -> None:
    registry_path = Path(runtime_registry.__file__).resolve()
    tree = parse_python(registry_path.read_bytes(), registry_path.as_posix())
    static_capabilities = baseline._capabilities(tree)

    static_modes = {
        item["id"]: baseline._native_mode(item) for item in static_capabilities
    }
    runtime_modes = {
        item["id"]: runtime_registry._native_mode(item)
        for item in runtime_registry._CAPABILITIES
    }

    assert static_modes == runtime_modes
