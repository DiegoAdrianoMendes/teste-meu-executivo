#!/usr/bin/env python3
"""Benchmark offline e reproduzível do ContextCompiler real do KLM Hub."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import random
import statistics
import sys
import unicodedata
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ai.context_compiler import (  # noqa: E402
    CONTEXT_COMPILER_VERSION,
    CONTEXT_SCHEMA_VERSION,
    CompiledContext,
    ContextCompiler,
    ContextEvidence,
    ContextMessage,
    ContextProvenance,
    ContextSummary,
)
from app.ai.token_budget import DeterministicTokenEstimator  # noqa: E402

SCRIPT_VERSION = "klm-ai-context-benchmark-v1"
DEFAULT_DATASET = ROOT / "app/data/saas_v5/ai-context-benchmark-v1.json"
DEFAULT_OUTPUT = ROOT / ".local/qa-evidence/saas-v5-p4/benchmark.json"
_FIXED_START = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

_HISTORY_NOISE = (
    "Mensagem administrativa sintética já consolidada no resumo; sem fato clínico novo.",
    "Confirmação sintética de recebimento, sem alteração da urgência ou dos documentos.",
    "Contato sintético de acompanhamento antigo, substituído pelas mensagens mais recentes.",
    "Registro sintético de tentativa de contato, sem resposta material para a análise.",
)
_CARD_NOISE = (
    "Registro sintético de navegação interna e conferência operacional sem impacto jurídico.",
    "Histórico administrativo artificial preservado apenas para simular um card maduro.",
    "Metadado sintético de campanha, fila e atividade sem relação com a negativa atual.",
    "Nota artificial antiga, já superada pelo resumo executivo e pela conversa recente.",
)
_DOCUMENT_NOISE = (
    "Página sintética de rodapé e controle administrativo sem informação clínica adicional.",
    "Trecho artificial repetitivo destinado exclusivamente à carga estrutural do benchmark.",
    "Metadado documental sintético sem novo fato, data clínica ou justificativa assistencial.",
)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_text(payload: str) -> str:
    return _sha256_bytes(payload.encode("utf-8"))


def _normalized(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    without_marks = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(without_marks.split())


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def load_dataset(path: Path = DEFAULT_DATASET) -> dict[str, Any]:
    raw = path.read_bytes()
    dataset = json.loads(raw)
    journeys = dataset.get("journeys")
    if dataset.get("schemaVersion") != "1.0" or not isinstance(journeys, list):
        raise ValueError("Dataset P4 possui schema inválido.")
    if (
        dataset.get("syntheticOnly") is not True
        or dataset.get("containsRealClientData") is not False
    ):
        raise ValueError("Dataset P4 deve declarar apenas dados sintéticos.")
    journey_ids = [str(item.get("id", "")) for item in journeys]
    if (
        not journeys
        or any(not item for item in journey_ids)
        or len(journey_ids) != len(set(journey_ids))
    ):
        raise ValueError("Jornadas P4 devem ter IDs únicos e não vazios.")
    for journey in journeys:
        required = {
            "workspaceId",
            "matterId",
            "subtheme",
            "dimensions",
            "card",
            "summary",
            "coreMessages",
            "documents",
            "mandatoryFacts",
            "latestMessageId",
        }
        missing = sorted(required - set(journey))
        if missing:
            raise ValueError(f"Jornada {journey['id']} incompleta: {', '.join(missing)}")
        if not journey["mandatoryFacts"] or not journey["documents"] or not journey["coreMessages"]:
            raise ValueError(f"Jornada {journey['id']} não possui fatos, documentos ou mensagens.")
    dataset["_path"] = path
    dataset["_sha256"] = _sha256_bytes(raw)
    return dataset


def _expanded_card(journey: dict[str, Any], dataset: dict[str, Any]) -> dict[str, Any]:
    card = deepcopy(journey["card"])
    padding_count = int(dataset["generation"]["cardPaddingParagraphs"])
    card.update(
        {
            "id": journey["matterId"],
            "case_number": f"BENCH-{journey['id']}",
            "source": "dataset_sintetico_p4",
            "synthetic_client_label": f"Pessoa sintética {journey['id']}",
            "objection": journey["objection"],
            "legacy_notes": "\n".join(
                f"{index + 1:02d}. {_CARD_NOISE[index % len(_CARD_NOISE)]}"
                for index in range(padding_count)
            ),
            "historic_tags": [
                f"sintetica-{index:02d}" for index in range(1, padding_count + 1)
            ],
            "marketing_touchpoints": [
                {
                    "campaign": f"campanha-sintetica-{index:02d}",
                    "channel": ("google", "meta", "organic")[index % 3],
                    "note": _CARD_NOISE[index % len(_CARD_NOISE)],
                }
                for index in range(padding_count)
            ],
        }
    )
    return card


def _expanded_history(journey: dict[str, Any], dataset: dict[str, Any]) -> list[dict[str, str]]:
    target = int(dataset["generation"]["historyTurnsPerJourney"])
    core = list(journey["coreMessages"])
    if target < len(core):
        raise ValueError("historyTurnsPerJourney não pode ser menor que coreMessages.")
    noise_count = target - len(core)
    result: list[dict[str, str]] = []
    for index in range(noise_count):
        result.append(
            {
                "id": f"{journey['id']}-noise-{index + 1:02d}",
                "role": "assistant" if index % 2 == 0 else "client",
                "content": (
                    f"{_HISTORY_NOISE[index % len(_HISTORY_NOISE)]} "
                    f"Referência artificial {journey['id']}-{index + 1:02d}."
                ),
            }
        )
    result.extend(core)
    for index, item in enumerate(result):
        item["occurredAt"] = (_FIXED_START + timedelta(minutes=index)).isoformat()
    return result


def _expanded_documents(
    journey: dict[str, Any], dataset: dict[str, Any]
) -> list[dict[str, Any]]:
    padding_count = int(dataset["generation"]["documentPaddingParagraphs"])
    documents: list[dict[str, Any]] = []
    for document in journey["documents"]:
        expanded = deepcopy(document)
        expanded["content"] = "\n".join(
            [
                str(document["content"]),
                *(
                    f"{index + 1:02d}. {_DOCUMENT_NOISE[index % len(_DOCUMENT_NOISE)]}"
                    for index in range(padding_count)
                ),
            ]
        )
        documents.append(expanded)
    return documents


def _baseline_payload(
    journey: dict[str, Any],
    card: dict[str, Any],
    history: list[dict[str, str]],
    documents: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "strategy": "naive-full-card-full-history-all-documents",
        "scope": {
            "workspace_id": journey["workspaceId"],
            "matter_id": journey["matterId"],
        },
        "card": card,
        "history": history,
        "documents": documents,
    }


def _compiler_inputs(
    journey: dict[str, Any],
    dataset: dict[str, Any],
    card: dict[str, Any],
    history: list[dict[str, str]],
    documents: list[dict[str, Any]],
) -> tuple[ContextCompiler, dict[str, Any]]:
    workspace_id = str(journey["workspaceId"])
    matter_id = str(journey["matterId"])
    dataset_version = str(dataset["datasetVersion"])
    messages = [
        ContextMessage(
            role=item["role"],
            content=item["content"],
            occurred_at=item["occurredAt"],
            provenance=ContextProvenance(
                workspace_id=workspace_id,
                matter_id=matter_id,
                source_type="conversation_message",
                source_id=item["id"],
                source_version=dataset_version,
            ),
        )
        for item in history
    ]
    evidence = [
        ContextEvidence(
            content=document["content"],
            title=document["title"],
            rank=int(document["rank"]),
            provenance=ContextProvenance(
                workspace_id=workspace_id,
                matter_id=matter_id,
                source_type="document",
                source_id=document["id"],
                source_version=dataset_version,
            ),
        )
        for document in documents
    ]
    compiler = ContextCompiler(
        recent_window_size=int(dataset["compiler"]["recentWindowSize"]),
        default_token_budget=int(dataset["compiler"]["tokenBudget"]),
    )
    return compiler, {
        "workspace_id": workspace_id,
        "matter_id": matter_id,
        "card": card,
        "recent_messages": messages,
        "summary": ContextSummary(
            content=journey["summary"],
            provenance=ContextProvenance(
                workspace_id=workspace_id,
                matter_id=matter_id,
                source_type="rolling_summary",
                source_id=f"summary:{journey['id']}",
                source_version=dataset_version,
            ),
        ),
        "evidence": evidence,
        "card_provenance": ContextProvenance(
            workspace_id=workspace_id,
            matter_id=matter_id,
            source_type="matter_card",
            source_id=matter_id,
            source_version=dataset_version,
        ),
    }


class DeterministicExtractiveFakeModel:
    """Oracle extrativo: mede preservação de contexto, não capacidade de um LLM."""

    version = "klm-deterministic-extractive-fake-v1"
    temperature = 0.0

    def infer(self, context: str, mandatory_facts: list[str]) -> dict[str, Any]:
        normalized_context = _normalized(context)
        found = [fact for fact in mandatory_facts if _normalized(fact) in normalized_context]
        return {
            "model": self.version,
            "temperature": self.temperature,
            "foundFacts": found,
            "missingFacts": [fact for fact in mandatory_facts if fact not in found],
            "contextDigest": _sha256_text(context),
        }


def _provenance_entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    entries = [payload["card"]["provenance"]]
    if payload.get("summary") is not None:
        entries.append(payload["summary"]["provenance"])
    entries.extend(item["provenance"] for item in payload.get("recent_messages", []))
    entries.extend(item["provenance"] for item in payload.get("evidence", []))
    return entries


def _score_candidate(
    journey: dict[str, Any],
    compiled: CompiledContext,
    model_result: dict[str, Any],
    rubric: dict[str, Any],
) -> dict[str, Any]:
    payload = compiled.payload
    facts = journey["mandatoryFacts"]
    fact_coverage = len(model_result["foundFacts"]) / len(facts)
    workspace_id = journey["workspaceId"]
    matter_id = journey["matterId"]
    provenance = _provenance_entries(payload)
    scope_integrity = float(
        payload["scope"] == {"workspace_id": workspace_id, "matter_id": matter_id}
        and all(
            item.get("workspace_id") == workspace_id and item.get("matter_id") == matter_id
            for item in provenance
        )
    )
    provenance_completeness = float(
        bool(provenance)
        and all(
            item.get("source_type")
            and item.get("source_id")
            and len(str(item.get("content_sha256", ""))) == 64
            for item in provenance
        )
    )
    recent_ids = {
        item["provenance"]["source_id"] for item in payload.get("recent_messages", [])
    }
    latest_preserved = float(journey["latestMessageId"] in recent_ids)
    relevant_ids = {item["id"] for item in journey["documents"] if item["relevant"]}
    included_evidence = {
        item["provenance"]["source_id"] for item in payload.get("evidence", [])
    }
    relevant_evidence_preserved = float(bool(relevant_ids & included_evidence))
    components = {
        "mandatoryFacts": fact_coverage,
        "scopeIntegrity": scope_integrity,
        "provenanceCompleteness": provenance_completeness,
        "latestMessagePreserved": latest_preserved,
        "relevantEvidencePreserved": relevant_evidence_preserved,
    }
    weights = rubric["weights"]
    quality = sum(float(weights[name]) * value for name, value in components.items())
    return {
        "quality": round(quality, 8),
        "components": components,
        "missingFacts": model_result["missingFacts"],
        "includedEvidenceIds": sorted(included_evidence),
        "includedRecentMessageIds": sorted(recent_ids),
    }


def _score_baseline(
    journey: dict[str, Any],
    baseline_text: str,
    model: DeterministicExtractiveFakeModel,
    rubric: dict[str, Any],
) -> dict[str, Any]:
    inferred = model.infer(baseline_text, journey["mandatoryFacts"])
    fact_coverage = len(inferred["foundFacts"]) / len(journey["mandatoryFacts"])
    weights = rubric["weights"]
    quality = float(weights["mandatoryFacts"]) * fact_coverage + sum(
        float(weights[name])
        for name in (
            "scopeIntegrity",
            "provenanceCompleteness",
            "latestMessagePreserved",
            "relevantEvidencePreserved",
        )
    )
    return {
        "quality": round(quality, 8),
        "factCoverage": fact_coverage,
        "missingFacts": inferred["missingFacts"],
    }


def _percentile(sorted_values: list[float], quantile: float) -> float:
    if not sorted_values:
        raise ValueError("Intervalo estatístico exige ao menos um valor.")
    position = (len(sorted_values) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = position - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


def bootstrap_mean_interval(
    values: list[float],
    *,
    seed: int,
    samples: int,
    confidence: float,
) -> dict[str, float | int | str]:
    if not values or samples <= 0 or not 0 < confidence < 1:
        raise ValueError("Configuração bootstrap inválida.")
    generator = random.Random(seed)
    means = sorted(
        statistics.fmean(generator.choice(values) for _ in values) for _ in range(samples)
    )
    tail = (1 - confidence) / 2
    return {
        "method": "deterministic-percentile-bootstrap-paired-means",
        "confidence": confidence,
        "samples": samples,
        "seed": seed,
        "lower": round(_percentile(means, tail), 8),
        "point": round(statistics.fmean(values), 8),
        "upper": round(_percentile(means, 1 - tail), 8),
    }


def _dataset_coverage(dataset: dict[str, Any]) -> dict[str, Any]:
    actual_subthemes = sorted({item["subtheme"] for item in dataset["journeys"]})
    actual_dimensions = sorted(
        {dimension for item in dataset["journeys"] for dimension in item["dimensions"]}
    )
    required = dataset["requiredCoverage"]
    missing_subthemes = sorted(set(required["subthemes"]) - set(actual_subthemes))
    missing_dimensions = sorted(set(required["dimensions"]) - set(actual_dimensions))
    return {
        "actualSubthemes": actual_subthemes,
        "actualDimensions": actual_dimensions,
        "missingSubthemes": missing_subthemes,
        "missingDimensions": missing_dimensions,
        "complete": not missing_subthemes and not missing_dimensions,
    }


def _memory_bytes() -> int | None:
    try:
        return int(os.sysconf("SC_PAGE_SIZE")) * int(os.sysconf("SC_PHYS_PAGES"))
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _dependency_version(distribution: str) -> str | None:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return None


def machine_profile() -> dict[str, Any]:
    return {
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "python": platform.python_version(),
        "pythonImplementation": platform.python_implementation(),
        "cpuCount": os.cpu_count(),
        "memoryBytes": _memory_bytes(),
        "dependencies": {
            "pydantic": _dependency_version("pydantic"),
            "sqlalchemy": _dependency_version("sqlalchemy"),
        },
    }


def _gate(name: str, observed: Any, requirement: str, passed: bool) -> dict[str, Any]:
    return {
        "name": name,
        "observed": observed,
        "requirement": requirement,
        "passed": bool(passed),
    }


def run_benchmark(dataset_path: Path = DEFAULT_DATASET) -> dict[str, Any]:
    dataset = load_dataset(dataset_path)
    estimator = DeterministicTokenEstimator()
    model = DeterministicExtractiveFakeModel()
    cases: list[dict[str, Any]] = []

    for journey in dataset["journeys"]:
        card = _expanded_card(journey, dataset)
        history = _expanded_history(journey, dataset)
        documents = _expanded_documents(journey, dataset)
        baseline_payload = _baseline_payload(journey, card, history, documents)
        baseline_text = _canonical_json(baseline_payload)
        baseline_tokens = estimator.count_text(baseline_text)
        compiler, compiler_inputs = _compiler_inputs(
            journey, dataset, card, history, documents
        )
        compiled = compiler.compile(**compiler_inputs)
        replay = compiler.compile(**compiler_inputs)
        fake_result = model.infer(compiled.content, journey["mandatoryFacts"])
        fake_replay = model.infer(replay.content, journey["mandatoryFacts"])
        candidate_score = _score_candidate(
            journey, compiled, fake_result, dataset["rubric"]
        )
        baseline_score = _score_baseline(
            journey, baseline_text, model, dataset["rubric"]
        )
        reduction = 1 - (compiled.estimated_tokens / baseline_tokens)
        deterministic = compiled.digest == replay.digest and fake_result == fake_replay
        if baseline_score["missingFacts"]:
            raise ValueError(
                f"Dataset {journey['id']} não contém todos os fatos obrigatórios no baseline."
            )
        cases.append(
            {
                "id": journey["id"],
                "subtheme": journey["subtheme"],
                "dimensions": sorted(journey["dimensions"]),
                "inputDigest": _sha256_text(baseline_text),
                "baseline": {
                    "strategy": baseline_payload["strategy"],
                    "estimatedTokens": baseline_tokens,
                    "historyItems": len(history),
                    "documentItems": len(documents),
                    "cardFields": len(card),
                    "quality": baseline_score["quality"],
                },
                "candidate": {
                    "strategy": "real-context-compiler",
                    "digest": compiled.digest,
                    "estimatedTokens": compiled.estimated_tokens,
                    "tokenBudget": compiled.token_budget,
                    "structuralReduction": round(reduction, 8),
                    "quality": candidate_score["quality"],
                    "qualityDelta": round(
                        candidate_score["quality"] - baseline_score["quality"], 8
                    ),
                    "factCoverage": candidate_score["components"]["mandatoryFacts"],
                    "missingFacts": candidate_score["missingFacts"],
                    "selection": {
                        "recentIncluded": compiled.recent_items_included,
                        "recentOmitted": compiled.recent_items_omitted,
                        "evidenceIncluded": compiled.evidence_items_included,
                        "evidenceOmitted": compiled.evidence_items_omitted,
                        "truncated": compiled.truncated,
                    },
                    "scoreComponents": candidate_score["components"],
                    "includedEvidenceIds": candidate_score["includedEvidenceIds"],
                    "latestMessagePreserved": bool(
                        candidate_score["components"]["latestMessagePreserved"]
                    ),
                },
                "deterministicReplay": deterministic,
            }
        )

    thresholds = dataset["thresholds"]
    seed = int(dataset["generation"]["seed"])
    reduction_values = [item["candidate"]["structuralReduction"] for item in cases]
    quality_values = [item["candidate"]["quality"] for item in cases]
    delta_values = [item["candidate"]["qualityDelta"] for item in cases]
    fact_values = [item["candidate"]["factCoverage"] for item in cases]
    reduction_interval = bootstrap_mean_interval(
        reduction_values,
        seed=seed,
        samples=int(thresholds["bootstrapSamples"]),
        confidence=float(thresholds["bootstrapConfidence"]),
    )
    quality_interval = bootstrap_mean_interval(
        quality_values,
        seed=seed + 1,
        samples=int(thresholds["bootstrapSamples"]),
        confidence=float(thresholds["bootstrapConfidence"]),
    )
    coverage = _dataset_coverage(dataset)
    gates = [
        _gate("synthetic-dataset", True, "syntheticOnly=true; realClientData=false", True),
        _gate(
            "required-dataset-coverage",
            coverage["complete"],
            "all required sets",
            coverage["complete"],
        ),
        _gate(
            "mean-structural-token-reduction",
            reduction_interval["point"],
            f">={thresholds['meanStructuralReduction']}",
            float(reduction_interval["point"]) >= float(thresholds["meanStructuralReduction"]),
        ),
        _gate(
            "lower-confidence-structural-token-reduction",
            reduction_interval["lower"],
            f">={thresholds['lowerConfidenceStructuralReduction']}",
            float(reduction_interval["lower"])
            >= float(thresholds["lowerConfidenceStructuralReduction"]),
        ),
        _gate(
            "mandatory-fact-coverage",
            min(fact_values),
            f">={thresholds['mandatoryFactCoverage']} for every journey",
            min(fact_values) >= float(thresholds["mandatoryFactCoverage"]),
        ),
        _gate(
            "deterministic-quality",
            min(quality_values),
            f">={thresholds['deterministicQuality']} for every journey",
            min(quality_values) >= float(thresholds["deterministicQuality"]),
        ),
        _gate(
            "quality-delta-vs-naive",
            min(delta_values),
            f">={thresholds['minimumQualityDelta']} for every journey",
            min(delta_values) >= float(thresholds["minimumQualityDelta"]),
        ),
        _gate(
            "deterministic-replay",
            sum(item["deterministicReplay"] for item in cases),
            f"={len(cases)}",
            all(item["deterministicReplay"] for item in cases),
        ),
    ]
    deterministic_core = {
        "scriptVersion": SCRIPT_VERSION,
        "datasetVersion": dataset["datasetVersion"],
        "datasetSha256": dataset["_sha256"],
        "compilerVersion": CONTEXT_COMPILER_VERSION,
        "contextSchemaVersion": CONTEXT_SCHEMA_VERSION,
        "tokenizerVersion": estimator.version,
        "fakeModelVersion": model.version,
        "seed": seed,
        "temperature": model.temperature,
        "cases": cases,
        "reductionInterval": reduction_interval,
        "qualityInterval": quality_interval,
        "coverage": coverage,
        "gates": gates,
    }
    passed = all(item["passed"] for item in gates)
    return {
        "schemaVersion": "1.0",
        "kind": "klm-saas-v5-ai-context-benchmark",
        "status": "passed" if passed else "failed",
        "generatedAt": datetime.now(UTC).isoformat(),
        "localOnly": True,
        "externalActionsExecuted": 0,
        "networkCallsExecuted": 0,
        "reproducibility": {
            "digest": _sha256_text(_canonical_json(deterministic_core)),
            "scriptVersion": SCRIPT_VERSION,
            "datasetVersion": dataset["datasetVersion"],
            "datasetPath": dataset_path.relative_to(ROOT).as_posix()
            if dataset_path.is_relative_to(ROOT)
            else str(dataset_path),
            "datasetSha256": dataset["_sha256"],
            "compilerVersion": CONTEXT_COMPILER_VERSION,
            "contextSchemaVersion": CONTEXT_SCHEMA_VERSION,
            "tokenizerVersion": estimator.version,
            "fakeModelVersion": model.version,
            "seed": seed,
            "temperature": model.temperature,
            "machineProfile": machine_profile(),
        },
        "method": {
            "baseline": "card completo + histórico completo + todos os documentos",
            "candidate": "ContextCompiler real com janela, proveniência e orçamento configurados",
            "scorer": dataset["rubric"],
            "statistics": {
                "structuralReduction": reduction_interval,
                "deterministicQuality": quality_interval,
            },
            "limitation": (
                "O fake model é extrativo e determinístico; mede preservação estrutural de fatos, "
                "não qualidade semântica de um provedor externo."
            ),
        },
        "datasetCoverage": coverage,
        "thresholds": thresholds,
        "gates": gates,
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--assert-gates", action="store_true")
    args = parser.parse_args()
    dataset_path = args.dataset if args.dataset.is_absolute() else ROOT / args.dataset
    output_path = args.output if args.output.is_absolute() else ROOT / args.output
    result = run_benchmark(dataset_path)
    _atomic_write_json(output_path, result)
    print(
        json.dumps(
            {
                "status": result["status"],
                "journeys": len(result["cases"]),
                "meanStructuralReduction": result["method"]["statistics"][
                    "structuralReduction"
                ]["point"],
                "lowerConfidenceReduction": result["method"]["statistics"][
                    "structuralReduction"
                ]["lower"],
                "minimumFactCoverage": min(
                    item["candidate"]["factCoverage"] for item in result["cases"]
                ),
                "minimumQuality": min(
                    item["candidate"]["quality"] for item in result["cases"]
                ),
                "reproducibilityDigest": result["reproducibility"]["digest"],
                "output": output_path.relative_to(ROOT).as_posix()
                if output_path.is_relative_to(ROOT)
                else str(output_path),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 1 if args.assert_gates and result["status"] != "passed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
