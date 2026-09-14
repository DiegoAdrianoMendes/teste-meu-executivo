"""Truthfulness, provenance and native-experience contracts for OSS capabilities."""

import gzip
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

import app.services.oss_capability_registry as registry_module
from app.services.oss_capability_registry import (
    NATIVE_MODES,
    native_capability_registry,
    oss_capability_registry,
)
from app.services.oss_native_bottlecrm_service import (
    _CAPABILITIES as BOTTLECRM_CAPABILITIES,
)
from app.services.oss_native_bottlecrm_service import (
    CAPABILITY_KEYS as BOTTLECRM_KEYS,
)
from app.services.oss_native_chattermate_service import (
    CAPABILITY_SPECS as CHATTERMATE_CAPABILITIES,
)
from app.services.oss_native_chattermate_service import (
    CHATTERMATE_NATIVE_CAPABILITY_KEYS,
)
from app.services.oss_native_chatwoot_service import (
    _CAPABILITIES as CHATWOOT_CAPABILITIES,
)
from app.services.oss_native_chatwoot_service import (
    CHATWOOT_CAPABILITY_KEYS,
)
from app.services.oss_native_rocketchat_service import (
    _CAPABILITY_ACTIONS as ROCKETCHAT_CAPABILITY_ACTIONS,
)
from app.services.oss_native_rocketchat_service import (
    ROCKETCHAT_NATIVE_CAPABILITY_KEYS,
)

ROOT = Path(__file__).resolve().parents[2]


def test_runtime_source_digest_is_lazy_and_fixed_for_the_process(monkeypatch) -> None:
    calls = 0

    def digest(*, refresh: bool = False) -> str:
        nonlocal calls
        calls += 1
        assert refresh is True
        return "a" * 64

    monkeypatch.setattr(registry_module, "_RUNTIME_SOURCE_DIGEST", None)
    monkeypatch.setattr(registry_module, "_verification_files_digest", digest)

    assert registry_module._runtime_source_digest() == "a" * 64
    assert registry_module._runtime_source_digest() == "a" * 64
    assert calls == 1


def test_product_registry_uses_only_native_domains_and_public_routes() -> None:
    registry = native_capability_registry()
    rendered = json.dumps(registry, ensure_ascii=False).casefold()

    assert registry["schemaVersion"] == "3.0"
    assert registry["scope"] == "klm-native-capabilities"
    assert registry["summary"]["byDomain"] == {
        "automation": 27,
        "collaboration": 33,
        "communications": 39,
        "crm": 36,
    }
    assert all("provider" not in item for item in registry["capabilities"])
    assert all(
        item["id"].startswith(f"{item['domain']}.")
        and item["nativeRoute"].startswith(
            f"/app/capacidades/{item['domain']}/"
        )
        for item in registry["capabilities"]
    )
    assert not re.search(
        r"chatwoot|rocket[ ._-]?chat|chattermate|bottlecrm",
        rendered,
    )


def _temporary_proof_bundle(
    tmp_path: Path,
    monkeypatch,
    *,
    remove_contract_id: bool = False,
    runtime_checks: int | None = None,
) -> tuple[Path, dict[str, Path]]:
    source_digest = registry_module._verification_files_digest()
    native_ids, contract_ids = registry_module._verification_ids()
    action_ids = registry_module._verification_action_ids()
    monkeypatch.setattr(registry_module, "_RUNTIME_SOURCE_DIGEST", source_digest)
    monkeypatch.setattr(registry_module, "_QA_VERIFICATION_CACHE", None)
    monkeypatch.setattr(
        registry_module,
        "_RUNTIME_SOURCE_FILE_COUNT",
        registry_module._verification_file_count(),
    )
    generated_at = "2026-07-18T12:00:00+00:00"
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    proof_paths: dict[str, Path] = {}
    proof_references: dict[str, dict[str, str]] = {}
    removed_id: str | None = None
    for suite in registry_module._PROOF_ARTIFACTS:
        raw_path = raw_dir / f"{suite}-output.log.gz"
        output = suite.encode()
        raw_path.write_bytes(gzip.compress(output, compresslevel=9, mtime=0))
        proof = {
            "proofVersion": "1.0",
            "suite": suite,
            "status": "passed",
            "exitCode": 0,
            "generatedAt": generated_at,
            "sourceDigest": source_digest,
            "worktreeDigest": source_digest,
            "sourceFileCount": registry_module._verification_file_count(),
            "gitHead": "0" * 40,
            "worktreeDirty": True,
            "sourceBindingMode": "content_snapshot",
            "command": ["test", suite],
            "durationMs": 1,
            "outputSha256": hashlib.sha256(output).hexdigest(),
            "rawArtifacts": [
                {
                    "path": str(raw_path),
                    "sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
                    "uncompressedSha256": hashlib.sha256(output).hexdigest(),
                }
            ],
        }
        if suite == "backend":
            proof.update(
                passed=2_000,
                failed=0,
                errors=0,
                ruffPassed=True,
                diffCheckPassed=True,
            )
        elif suite == "frontend-unit":
            proof.update(
                passed=124,
                failed=0,
                errors=0,
                typecheckPassed=True,
                lintPassed=True,
                buildPassed=True,
            )
        elif suite == "frontend-e2e":
            proof.update(
                passed=203,
                failed=0,
                errors=0,
                nativeCapabilityIds=[
                    registry_module._canonical_capability_id(value)
                    for value in native_ids
                ],
                actionOccurrencesExposed=511,
            )
        elif suite == "local-runtime":
            proof.update(
                checks=(
                    registry_module._MIN_LOCAL_RUNTIME_CHECKS
                    if runtime_checks is None
                    else runtime_checks
                ),
                externalActionsExecuted=0,
                externalNetworkExecutions=0,
                externalNetworkAssertions=511,
                externalEffectEvidenceScope="application_reported_dry_run",
                externalEffectIndependentlyObserved=False,
                nativeDocumentsValidated=135,
                actionContractsValidated=511,
                contractCapabilitiesValidated=121,
                nativeDocumentCapabilityIds=list(native_ids),
                contractCapabilityIds=list(contract_ids),
                actionContractIds=list(action_ids),
                serverRuntimeSourceDigest=source_digest,
                serverRuntimeSourceFileCount=registry_module._verification_file_count(),
                serverRuntimeSourceMatchesCurrent=True,
            )
        else:
            proof.update(
                upstreamLocksVerified=5,
                sidecarProfilesConfigured=4,
                sidecarPrimaryRequired=False,
                sidecarOffProofPassed=True,
            )
        if remove_contract_id and suite == "local-runtime":
            removed_id = proof["contractCapabilityIds"][0]
            proof["contractCapabilityIds"].remove(removed_id)
            proof["contractCapabilitiesValidated"] -= 1
        destination = tmp_path / f"{suite}.json"
        raw = (
            json.dumps(proof, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        destination.write_bytes(raw)
        proof_paths[suite] = destination
        proof_references[suite] = {
            "path": str(destination),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
    if remove_contract_id:
        assert removed_id is not None
    manifest = {
        "schemaVersion": "1.2",
        "generatedAt": generated_at,
        **registry_module._verification_contract(),
        "verificationFilesDigest": source_digest,
        "verificationFileCount": registry_module._verification_file_count(),
        "actionOccurrencesContractValidated": 511,
        "uniqueCommandsContractValidated": 360,
        "externalEffectsExecuted": 0,
        "externalNetworkAssertionsValidated": 511,
        "externalEffectEvidenceScope": "application_reported_dry_run",
        "externalEffectIndependentlyObserved": False,
        "sourceBinding": {
            "mode": "content_snapshot",
            "gitHead": "0" * 40,
            "worktreeDirty": True,
            "inventory": {"path": "source-inventory.json", "sha256": "a" * 64},
            "snapshot": {"path": "source-snapshot.tar.gz", "sha256": "b" * 64},
        },
        "proofArtifacts": proof_references,
    }
    manifest_path = tmp_path / "oss_native_verification.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(registry_module, "_VERIFICATION_MANIFEST", manifest_path)
    monkeypatch.setattr(registry_module, "_PROOF_ARTIFACTS", proof_paths)
    monkeypatch.setattr(registry_module, "_RAW_PROOF_DIR", raw_dir)
    monkeypatch.setattr(
        registry_module,
        "_read_source_binding",
        lambda *_args, **_kwargs: (manifest["sourceBinding"], None),
    )
    return manifest_path, proof_paths


def test_registry_classifies_all_135_families_as_native_without_duplicates(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _temporary_proof_bundle(tmp_path, monkeypatch)
    registry = oss_capability_registry()
    capabilities = registry["capabilities"]
    ids = [item["id"] for item in capabilities]

    assert registry["schemaVersion"] == "2.0"
    assert registry["scope"] == "pinned-community-feature-families"
    assert registry["summary"]["total"] == len(capabilities) == 135
    assert registry["summary"]["unclassified"] == 0
    assert registry["summary"]["nativeExperience"] == 135
    assert registry["summary"]["nativeRoute"] == 135
    assert registry["summary"]["primaryDeepLink"] == 0
    assert registry["summary"]["contractEligible"] == 121
    assert registry["summary"]["externalEffectVerified"] == 0
    assert registry["summary"]["gatedConfiguration"] == 12
    assert registry["summary"]["byExposure"] == {"native": 135}
    assert registry["summary"]["byIntegrationLevel"] == {"native": 135}
    assert len(ids) == len(set(ids))
    assert {item["provider"] for item in capabilities} == {
        "chatwoot",
        "rocketchat",
        "chattermate",
        "bottlecrm",
    }
    assert len(registry_module._verification_action_ids()) == 511


def test_verification_digest_invalidates_when_a_release_file_changes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    release_file = tmp_path / "Dockerfile.frontend"
    release_file.write_text("FROM nginx:one\n", encoding="utf-8")
    monkeypatch.setattr(registry_module, "_VERIFICATION_ROOTS", ())
    monkeypatch.setattr(registry_module, "_VERIFICATION_FILES", (str(release_file),))
    monkeypatch.setattr(registry_module, "_VERIFICATION_DIGEST_CACHE", None)

    before = registry_module._verification_files_digest()
    release_file.write_text("FROM nginx:two\n", encoding="utf-8")
    after = registry_module._verification_files_digest()

    assert before != after


def test_verification_digest_covers_every_release_entrypoint() -> None:
    covered = {path.as_posix() for path in registry_module._verification_source_files()}
    assert {
        ".env.example",
        ".env.oss.example",
        "Dockerfile",
        "Dockerfile.frontend",
        "alembic.ini",
        "buildspec.yml",
        "docker-compose.prod.yml",
        "docker-compose.yml",
        "docker-compose.oss.yml",
        "entrypoint.sh",
        "pytest.ini",
        "pyproject.toml",
        "requirements-test.txt",
        "requirements.txt",
    } <= covered
    assert "app/data/saas_v5/p5-verification.json" not in covered
    assert not any(path.startswith("frontend/dist-app/") for path in covered)
    assert not any(path.startswith("frontend/dist-blackbox/") for path in covered)


def test_registry_verification_is_bound_to_current_code_and_required_proof_runs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _manifest_path, proof_paths = _temporary_proof_bundle(tmp_path, monkeypatch)
    registry = oss_capability_registry()
    verification = registry["qaVerification"]
    frontend_e2e = json.loads(proof_paths["frontend-e2e"].read_text(encoding="utf-8"))

    assert verification["state"] == "verified"
    assert verification["nativeIdsVerified"] is True
    assert verification["contractIdsVerified"] is True
    assert verification["actionIdsVerified"] is True
    assert verification["actionOccurrencesContractValidated"] == 511
    assert verification["uniqueCommandsContractValidated"] == 360
    assert verification["externalEffectsExecuted"] == 0
    assert verification["externalNetworkAssertionsValidated"] == 511
    assert verification["externalEffectEvidenceScope"] == "application_reported_dry_run"
    assert verification["externalEffectIndependentlyObserved"] is False
    assert registry["summary"]["nativeExperienceVerified"] == 135
    assert registry["summary"]["contractVerified"] == 121
    assert all(
        capability_id.split(".", 1)[0]
        in {"communications", "collaboration", "automation", "crm"}
        for capability_id in frontend_e2e["nativeCapabilityIds"]
    )
    assert {run["id"] for run in verification["proofRuns"]} >= {
        "backend",
        "frontend-unit",
        "frontend-e2e",
        "local-runtime",
        "upstream-locks",
    }


def test_registry_fails_closed_when_runtime_check_coverage_regresses(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _temporary_proof_bundle(
        tmp_path,
        monkeypatch,
        runtime_checks=registry_module._MIN_LOCAL_RUNTIME_CHECKS - 1,
    )

    registry = oss_capability_registry()

    assert registry["qaVerification"]["state"] == "unverified"
    assert "gates quantitativos" in registry["qaVerification"]["reason"]
    assert registry["summary"]["nativeExperienceVerified"] == 0


def test_qa_verification_cache_revalidates_when_artifact_signature_changes(
    monkeypatch,
) -> None:
    calls = 0
    artifact_signature = ["first"]

    def verify_uncached() -> dict[str, int]:
        nonlocal calls
        calls += 1
        return {"generation": calls}

    monkeypatch.setattr(registry_module, "_QA_VERIFICATION_CACHE", None)
    monkeypatch.setattr(registry_module, "_verification_files_digest", lambda: "digest")
    monkeypatch.setattr(registry_module, "_verification_file_count", lambda: 1)
    monkeypatch.setattr(
        registry_module,
        "_qa_verification_cache_signature",
        lambda: tuple(artifact_signature),
    )
    monkeypatch.setattr(registry_module, "_qa_verification_uncached", verify_uncached)

    assert registry_module._qa_verification()["generation"] == 1
    assert registry_module._qa_verification()["generation"] == 1
    assert calls == 1

    artifact_signature[0] = "changed"
    assert registry_module._qa_verification()["generation"] == 2
    assert calls == 2


def test_registry_does_not_promote_native_claims_when_qa_verified_zero(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        registry_module,
        "_qa_verification",
        lambda: {
            "state": "unverified",
            "verifiedNativeCapabilityIds": [],
            "verifiedContractCapabilityIds": [],
        },
    )

    registry = oss_capability_registry()

    assert registry["summary"]["nativeExperience"] == 0
    assert registry["summary"]["nativeExperienceVerified"] == 0
    assert registry["summary"]["nativeRoute"] == 135
    for item in registry["capabilities"]:
        assert item["nativeExperience"] is False, item["id"]
        assert item["nativeExperienceVerified"] is False, item["id"]
        assert item["currentExposure"] == item["sourceExposure"], item["id"]
        assert item["integrationLevel"] == item["sourceExposure"], item["id"]
        assert item["targetRoute"] == item["sourceTargetRoute"], item["id"]
        assert "resultado verificado" not in item["reason"].lower(), item["id"]


def test_native_and_approved_claims_are_backed_by_real_adapter_actions() -> None:
    registry = oss_capability_registry()
    action_counts = Counter(
        action_id.split(":", 1)[0]
        for action_id in registry_module._verification_action_ids()
    )

    for item in registry["capabilities"]:
        claims_action = (
            item["sourceDisposition"]
            in {"approved_native", "approved_embedded", "approved_full_app", "implemented"}
            or item["sourceExposure"] == "native"
            or item["nativeExperience"] is True
        )
        if claims_action:
            assert action_counts[item["id"]] > 0, item["id"]


def test_audited_native_reasons_describe_klm_actions_instead_of_outsourcing_them() -> None:
    audited_fragments = {
        "chatwoot.inboxes-routing": "atualização de status",
        "chatwoot.automation-rules": "dry-run local",
        "chatwoot.conversation-search-custom-views": "cria, edita ou exclui",
        "chattermate.user-settings": "preferências versionadas",
        "bottlecrm.task-boards-pipelines": "move tarefas",
        "bottlecrm.products-estimates-quotes": "produtos e estimativas",
        "bottlecrm.opportunity-aging-goals": "configura aging",
        "bottlecrm.business-hours-sla-csat": "configura calendários",
    }
    rows = {item["id"]: item for item in oss_capability_registry()["capabilities"]}
    action_ids = registry_module._verification_action_ids()

    for capability_id, expected_fragment in audited_fragments.items():
        row = rows[capability_id]
        reason = row["sourceReason"].lower()
        assert expected_fragment in reason, capability_id
        assert "motor completo" not in reason, capability_id
        assert "produto completo" not in reason, capability_id
        assert any(
            action_id.startswith(f"{capability_id}:") for action_id in action_ids
        ), capability_id


def test_registry_fails_closed_when_the_api_process_has_stale_source(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _temporary_proof_bundle(tmp_path, monkeypatch)
    monkeypatch.setattr(registry_module, "_RUNTIME_SOURCE_DIGEST", "f" * 64)

    registry = oss_capability_registry()

    assert registry["qaVerification"]["state"] == "unverified"
    assert registry["qaVerification"]["runtimeSourceMatchesCurrent"] is False
    assert "reinicie" in registry["qaVerification"]["reason"].lower()
    assert registry["summary"]["nativeExperienceVerified"] == 0
    assert registry["summary"]["contractVerified"] == 0


def test_registry_fails_closed_when_manifest_does_not_match_code(
    tmp_path: Path,
    monkeypatch,
) -> None:
    manifest_path, _proof_paths = _temporary_proof_bundle(tmp_path, monkeypatch)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["verificationFilesDigest"] = "stale-code-digest"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    registry = oss_capability_registry()

    assert registry["qaVerification"]["state"] == "unverified"
    assert registry["summary"]["nativeExperienceVerified"] == 0
    assert registry["summary"]["contractVerified"] == 0


def test_registry_fails_closed_when_a_proof_artifact_hash_is_tampered(
    tmp_path: Path,
    monkeypatch,
) -> None:
    manifest_path, _proof_paths = _temporary_proof_bundle(tmp_path, monkeypatch)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["proofArtifacts"]["backend"]["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    registry = oss_capability_registry()

    assert registry["qaVerification"]["state"] == "unverified"
    assert registry["summary"]["nativeExperienceVerified"] == 0


def test_registry_fails_closed_when_raw_machine_output_is_tampered(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _temporary_proof_bundle(tmp_path, monkeypatch)
    raw_path = next((tmp_path / "raw").glob("backend-output.log.gz"))
    raw_path.write_bytes(raw_path.read_bytes() + b"tampered")

    registry = oss_capability_registry()

    assert registry["qaVerification"]["state"] == "unverified"
    assert "prova bruta" in registry["qaVerification"]["reason"].lower()


def test_registry_marks_partial_when_one_required_contract_id_is_missing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _manifest_path, _proof_paths = _temporary_proof_bundle(
        tmp_path,
        monkeypatch,
        remove_contract_id=True,
    )

    registry = oss_capability_registry()

    assert registry["qaVerification"]["state"] == "partial"
    assert registry["summary"]["nativeExperienceVerified"] == 135
    assert registry["summary"]["contractVerified"] == 120


def test_registry_evidence_paths_exist_for_every_native_claim() -> None:
    for item in oss_capability_registry()["capabilities"]:
        assert item["reason"].strip(), item["id"]
        assert item["sourceReason"].strip(), item["id"]
        assert item["upstreamEvidence"], item["id"]
        assert item["klmEvidence"], item["id"]
        assert item["testEvidence"], item["id"]
        for field in ("upstreamEvidence", "klmEvidence", "testEvidence"):
            for relative_path in item[field]:
                assert (ROOT / relative_path).exists(), f"{item['id']}: {relative_path}"


def test_approved_capabilities_do_not_describe_pre_native_gaps_as_current_state() -> None:
    stale_markers = (
        "ainda não",
        "não há contrato",
        "não possui contrato",
        "não possuem contrato",
        "faltam schema",
        "falta reconciliar",
        "serão expost",
        "adicionará",
        "permanece no motor",
        "permanecem no motor",
        "ainda não foram incorporad",
        "será reaproveitad",
    )

    for item in oss_capability_registry()["capabilities"]:
        if not (
            item["sourceDisposition"] == "implemented"
            or item["sourceDisposition"].startswith("approved_")
        ):
            continue
        reason = item["reason"].casefold()
        assert not [marker for marker in stale_markers if marker in reason], item["id"]


def test_registry_preserves_source_classification_without_making_it_primary(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _temporary_proof_bundle(tmp_path, monkeypatch)
    capabilities = oss_capability_registry()["capabilities"]

    assert any(item["sourceExposure"] == "bridge" for item in capabilities)
    assert any(item["sourceExposure"] == "full_app" for item in capabilities)
    assert any(item["sourceDisposition"] == "blocked_external_gate" for item in capabilities)
    assert any(item["sourceDisposition"] == "blocked_license" for item in capabilities)
    assert all(item["currentExposure"] == "native" for item in capabilities)
    assert all(item["integrationLevel"] == "native" for item in capabilities)
    assert all(item["primaryExperience"] == "native" for item in capabilities)
    assert all(item["primaryDeepLink"] is False for item in capabilities)


def test_every_capability_uses_the_dynamic_native_spa_route_without_iframe(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _temporary_proof_bundle(tmp_path, monkeypatch)
    capabilities = oss_capability_registry()["capabilities"]
    app_source = (ROOT / "frontend/src/App.tsx").read_text(encoding="utf-8")
    workspace_source = (
        ROOT / "frontend/src/klm/components/NativeCapabilityWorkspace.tsx"
    ).read_text(encoding="utf-8")

    assert 'path="capacidades/:domain/:capability"' in app_source
    assert "<iframe" not in workspace_source.lower()
    for item in capabilities:
        domain = registry_module.NATIVE_DOMAINS[item["provider"]]
        expected = f"/app/capacidades/{domain}/{item['key']}"
        assert item["nativeExperience"] is True, item["id"]
        assert item["nativeRoute"] == expected, item["id"]
        assert item["targetRoute"] == expected, item["id"]
        assert re.fullmatch(r"/app/capacidades/[a-z]+/[a-z0-9-]+", expected), item["id"]


def test_native_modes_and_external_gates_are_explicit_and_truthful(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _temporary_proof_bundle(tmp_path, monkeypatch)
    capabilities = oss_capability_registry()["capabilities"]

    assert {item["nativeMode"] for item in capabilities} <= NATIVE_MODES
    assert all(item["nativeExperience"] for item in capabilities)
    assert not any(item["externalEffectVerified"] for item in capabilities)
    assert sum(bool(item["contractEligible"]) for item in capabilities) == 121
    for item in capabilities:
        if item["nativeMode"] in {"gated_configuration", "responsive_substitution"}:
            assert item["contractEligible"] is False, item["id"]
        else:
            assert item["contractEligible"] is True, item["id"]
        if item["sourceDisposition"] in {"blocked_external_gate", "blocked_dependency"}:
            assert item["nativeMode"] == "gated_configuration", item["id"]
            assert item["externalExecutionState"].startswith("blocked_"), item["id"]
            assert item["verificationLevel"] == "configuration_only", item["id"]
        if item["sourceDisposition"] == "blocked_license":
            assert item["nativeMode"] == "klm_equivalent", item["id"]
            assert item["externalExecutionState"] == "not_used_klm_equivalent", item["id"]
        if item["sourceDisposition"] == "not_applicable":
            assert item["nativeMode"] == "responsive_substitution", item["id"]
            assert item["verificationLevel"] == "responsive_substitution", item["id"]


def test_commands_endpoint_is_disabled_for_providers_without_dispatcher() -> None:
    capabilities = oss_capability_registry()["capabilities"]
    disabled_providers: set[str] = set()

    for item in capabilities:
        endpoint = item["commandsEndpoint"]
        assert endpoint["path"].endswith(f"/{item['key']}/commands"), item["id"]
        assert endpoint["replacementPath"].endswith(f"/{item['key']}/actions"), item["id"]
        if item["provider"] == "chatwoot":
            assert endpoint == {
                "enabled": True,
                "state": "dispatcher_available",
                "path": (
                    f"/api/v1/capabilities/domains/communications/"
                    f"{item['key']}/commands"
                ),
                "replacementPath": (
                    f"/api/v1/capabilities/domains/communications/"
                    f"{item['key']}/actions"
                ),
            }
        else:
            disabled_providers.add(item["provider"])
            assert endpoint["enabled"] is False, item["id"]
            assert endpoint["state"] == "disabled_no_dispatcher", item["id"]

    assert disabled_providers == {"rocketchat", "chattermate", "bottlecrm"}


def test_provider_adapters_declare_exactly_the_same_135_keys() -> None:
    registry = oss_capability_registry()["capabilities"]
    registry_by_provider = {
        provider: {row["key"] for row in registry if row["provider"] == provider}
        for provider in {row["provider"] for row in registry}
    }
    adapter_keys = {
        "chatwoot": set(CHATWOOT_CAPABILITY_KEYS),
        "rocketchat": set(ROCKETCHAT_NATIVE_CAPABILITY_KEYS),
        "chattermate": set(CHATTERMATE_NATIVE_CAPABILITY_KEYS),
        "bottlecrm": set(BOTTLECRM_KEYS),
    }

    assert adapter_keys == registry_by_provider
    assert sum(len(keys) for keys in adapter_keys.values()) == 135


def test_provider_action_contract_contains_511_occurrences_and_360_unique_commands() -> None:
    action_groups = [
        [
            command
            for spec in CHATWOOT_CAPABILITIES.values()
            for command in (*spec.actions, *spec.blocked_actions)
        ],
        [
            command
            for commands in ROCKETCHAT_CAPABILITY_ACTIONS.values()
            for command in commands
        ],
        [
            command
            for spec in CHATTERMATE_CAPABILITIES.values()
            for command in spec.commands
        ],
        [command for spec in BOTTLECRM_CAPABILITIES for command in spec.actions],
    ]

    assert [len(group) for group in action_groups] == [211, 77, 105, 118]
    assert sum(len(group) for group in action_groups) == 511
    assert len({command for group in action_groups for command in group}) == 360
