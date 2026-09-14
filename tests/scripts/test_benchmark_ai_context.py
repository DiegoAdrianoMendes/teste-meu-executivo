from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from app.ai.context_compiler import CONTEXT_COMPILER_VERSION
from scripts import benchmark_ai_context as benchmark


def test_dataset_is_synthetic_versioned_and_covers_all_health_denial_journeys() -> None:
    dataset = benchmark.load_dataset()

    assert dataset["datasetVersion"] == "klm-health-denials-context-v1"
    assert dataset["syntheticOnly"] is True
    assert dataset["containsRealClientData"] is False
    assert len(dataset["journeys"]) == 10
    assert {item["subtheme"] for item in dataset["journeys"]} == {
        "medicamento",
        "home_care",
        "cirurgia",
        "exame",
        "tratamento",
    }
    dimensions = {
        dimension for item in dataset["journeys"] for dimension in item["dimensions"]
    }
    assert set(dataset["requiredCoverage"]["dimensions"]) <= dimensions


def test_benchmark_uses_real_compiler_and_is_reproducible() -> None:
    first = benchmark.run_benchmark()
    second = benchmark.run_benchmark()

    assert first["status"] == "passed"
    assert first["reproducibility"]["compilerVersion"] == CONTEXT_COMPILER_VERSION
    assert first["reproducibility"]["digest"] == second["reproducibility"]["digest"]
    assert first["reproducibility"]["temperature"] == 0.0
    assert first["networkCallsExecuted"] == 0
    assert first["externalActionsExecuted"] == 0
    assert min(item["candidate"]["factCoverage"] for item in first["cases"]) == 1.0
    assert min(item["candidate"]["quality"] for item in first["cases"]) >= 0.95
    interval = first["method"]["statistics"]["structuralReduction"]
    assert interval["point"] >= 0.4
    assert interval["lower"] >= 0.4


def test_dataset_missing_a_mandatory_fact_fails_closed(tmp_path: Path) -> None:
    source = benchmark.load_dataset()
    tampered = {
        key: deepcopy(value)
        for key, value in source.items()
        if not key.startswith("_")
    }
    tampered["journeys"][0]["mandatoryFacts"].append(
        "fato impossível ausente do corpus sintético"
    )
    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(tampered, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="não contém todos os fatos obrigatórios"):
        benchmark.run_benchmark(path)


def test_bootstrap_interval_is_seeded_and_deterministic() -> None:
    values = [0.41, 0.48, 0.52, 0.59, 0.63]

    first = benchmark.bootstrap_mean_interval(
        values,
        seed=42,
        samples=500,
        confidence=0.95,
    )
    second = benchmark.bootstrap_mean_interval(
        values,
        seed=42,
        samples=500,
        confidence=0.95,
    )

    assert first == second
    assert first["lower"] <= first["point"] <= first["upper"]
