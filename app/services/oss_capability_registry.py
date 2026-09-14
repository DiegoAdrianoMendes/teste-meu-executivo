"""Machine-readable disposition ledger for the pinned community OSS capabilities."""

# ruff: noqa: E501 -- evidence paths and reviewable disposition text are data records.

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import re
import tarfile
from collections import Counter
from pathlib import Path
from typing import Any

from app.services.oss_motor_routes import MOTOR_DESTINATIONS, motor_destination

DISPOSITIONS = {
    "discovered",
    "approved_native",
    "approved_embedded",
    "approved_full_app",
    "implemented",
    "not_applicable",
    "blocked_license",
    "blocked_dependency",
    "blocked_external_gate",
}

EXPOSURES = {"none", "bridge", "native", "microfrontend", "full_app"}
PROVIDERS = {"chatwoot", "rocketchat", "chattermate", "bottlecrm"}
NATIVE_DOMAINS = {
    "chatwoot": "communications",
    "rocketchat": "collaboration",
    "chattermate": "automation",
    "bottlecrm": "crm",
}
NATIVE_MODES = {
    "sidecar_round_trip",
    "sidecar_command",
    "klm_canonical",
    "klm_equivalent",
    "gated_configuration",
    "responsive_substitution",
}

VERIFICATION_LEVELS = {
    "contract_and_native_read",
    "local_equivalent",
    "configuration_only",
    "responsive_substitution",
}

_NATIVE_SERVICE_EVIDENCE = {
    "chatwoot": "app/services/oss_native_chatwoot_service.py",
    "rocketchat": "app/services/oss_native_rocketchat_service.py",
    "chattermate": "app/services/oss_native_chattermate_service.py",
    "bottlecrm": "app/services/oss_native_bottlecrm_service.py",
}

_NATIVE_TEST_EVIDENCE = {
    "chatwoot": "tests/services/test_oss_native_chatwoot_service.py",
    "rocketchat": "tests/services/test_oss_native_rocketchat_service.py",
    "chattermate": "tests/services/test_oss_native_chattermate_service.py",
    "bottlecrm": "tests/services/test_oss_native_bottlecrm_service.py",
}
_PROVIDERS_WITH_NATIVE_COMMAND_DISPATCHER = frozenset({"chatwoot"})

_OSS_NATIVE_EVIDENCE_DIR = (
    Path(__file__).resolve().parents[2] / ".local" / "qa-evidence" / "oss-native"
)
_VERIFICATION_MANIFEST = _OSS_NATIVE_EVIDENCE_DIR / "oss_native_verification.json"
_PROOF_ARTIFACTS = {
    suite: _OSS_NATIVE_EVIDENCE_DIR / "proofs" / f"{suite}.json"
    for suite in (
        "backend",
        "frontend-unit",
        "frontend-e2e",
        "local-runtime",
        "upstream-locks",
    )
}
_RAW_PROOF_DIR = _OSS_NATIVE_EVIDENCE_DIR / "proofs" / "raw"
_SOURCE_INVENTORY_ARTIFACT = (
    _OSS_NATIVE_EVIDENCE_DIR / "proofs" / "source-inventory.json"
)
_SOURCE_SNAPSHOT_ARTIFACT = (
    _OSS_NATIVE_EVIDENCE_DIR / "proofs" / "source-snapshot.tar.gz"
)
_SOURCE_BINDING_MODE = "content_snapshot"
_VERIFICATION_ROOTS = (
    ".github",
    "alembic",
    "app",
    "deploy",
    "docker",
    "frontend",
    "scripts",
    "tests",
)
_VERIFICATION_FILES = (
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
    "docs/oss-upstreams.lock.json",
)
_VERIFICATION_EXCLUDED_PREFIXES = (
    "app/data/oss_native_proofs/",
    "app/data/oss_native_verification.json",
    "app/data/oss_function_unit_dispositions.json",
    "app/data/oss_function_unit_proofs/",
    "app/data/oss_function_unit_verification.json",
    "app/data/native_architecture_superiority.json",
    "app/data/saas_v5/p5-verification.json",
)
_EXPECTED_ACTION_OCCURRENCES = 511
_EXPECTED_UNIQUE_COMMANDS = 360
_MIN_LOCAL_RUNTIME_CHECKS = 119
_VERIFICATION_DIGEST_CACHE: tuple[
    tuple[tuple[str, int, int, int], ...],
    str,
] | None = None
_QA_VERIFICATION_CACHE: tuple[tuple[Any, ...], dict[str, Any]] | None = None


def _legacy_native_mode(item: dict[str, Any]) -> str:
    disposition = item["disposition"]
    exposure = item["currentExposure"]
    if disposition in {"blocked_external_gate", "blocked_dependency"}:
        return "gated_configuration"
    if disposition == "blocked_license":
        return "klm_equivalent"
    if disposition == "not_applicable":
        return "responsive_substitution"
    if exposure in {"bridge", "microfrontend"}:
        return "sidecar_round_trip"
    if exposure == "full_app":
        return "sidecar_command"
    if exposure == "native":
        return "klm_canonical"
    return "klm_equivalent"


def _native_mode(item: dict[str, Any]) -> str:
    legacy_mode = _legacy_native_mode(item)
    if legacy_mode in {
        "gated_configuration",
        "klm_equivalent",
        "responsive_substitution",
    }:
        return legacy_mode
    return "klm_canonical"


def _native_reason(item: dict[str, Any], mode: str) -> str:
    if mode == "gated_configuration":
        return (
            "Configuração, validação, preview, fila e auditoria são nativos no KLM; "
            "o efeito de rede continua bloqueado até credenciais e aprovação expressa."
        )
    if mode == "klm_equivalent":
        return (
            "Equivalente KLM independente e auditável, sem copiar módulos proprietários "
            "ou depender de licença não incluída no checkout comunitário."
        )
    if mode == "responsive_substitution":
        return (
            "A experiência responsiva do KLM substitui o cliente upstream e mantém a "
            "mesma operação no desktop e no mobile."
        )
    if mode == "klm_canonical":
        return "Operação nativa com o KLM como fonte canônica e trilha de auditoria por workspace."
    if mode == "sidecar_round_trip":
        return (
            "Operação nativa reconciliada com o motor local, com leitura posterior, "
            "controle de versão e conflitos explícitos."
        )
    return (
        "Comando nativo do KLM para o motor local, com resultado coberto pela prova de QA, "
        "idempotência e diagnóstico no próprio documento KLM."
    )


def _external_execution_state(item: dict[str, Any]) -> str:
    disposition = item["disposition"]
    if disposition == "blocked_external_gate":
        return "blocked_pending_credentials_and_approval"
    if disposition == "blocked_dependency":
        return "blocked_pending_dependency"
    if disposition == "blocked_license":
        return "not_used_klm_equivalent"
    if disposition == "not_applicable":
        return "not_applicable"
    return "local_only"


def _verification_level(native_mode: str) -> str:
    if native_mode in {"sidecar_round_trip", "sidecar_command"}:
        return "contract_and_native_read"
    if native_mode in {"klm_canonical", "klm_equivalent"}:
        return "local_equivalent"
    if native_mode == "gated_configuration":
        return "configuration_only"
    return "responsive_substitution"


def _stable_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _verification_contract() -> dict[str, Any]:
    capabilities = [
        {
            "id": item["id"],
            "provider": item["provider"],
            "key": item["key"],
            "nativeMode": _native_mode(item),
            "nativeRoute": (
                f"/app/capacidades/{NATIVE_DOMAINS[item['provider']]}/{item['key']}"
            ),
        }
        for item in _CAPABILITIES
    ]
    contract_ids = sorted(
        row["id"]
        for row in capabilities
        if row["nativeMode"] not in {"gated_configuration", "responsive_substitution"}
    )
    native_ids = sorted(row["id"] for row in capabilities)
    action_ids = _verification_action_ids()
    return {
        "contractDigest": _stable_digest(capabilities),
        "nativeIdsDigest": _stable_digest(native_ids),
        "nativeCount": len(native_ids),
        "contractIdsDigest": _stable_digest(contract_ids),
        "contractCount": len(contract_ids),
        "actionIdsDigest": _stable_digest(action_ids),
        "actionCount": len(action_ids),
    }


def _verification_ids() -> tuple[list[str], list[str]]:
    rows = [
        (item["id"], _native_mode(item))
        for item in _CAPABILITIES
    ]
    native_ids = sorted(capability_id for capability_id, _mode in rows)
    contract_ids = sorted(
        capability_id
        for capability_id, mode in rows
        if mode not in {"gated_configuration", "responsive_substitution"}
    )
    return native_ids, contract_ids


def _verification_action_ids() -> list[str]:
    from app.services.oss_native_bottlecrm_service import (
        _CAPABILITIES as BOTTLECRM_CAPABILITIES,
    )
    from app.services.oss_native_chattermate_service import CAPABILITY_SPECS
    from app.services.oss_native_chatwoot_service import (
        _CAPABILITIES as CHATWOOT_CAPABILITIES,
    )
    from app.services.oss_native_rocketchat_service import _CAPABILITY_ACTIONS

    action_ids = [
        f"chatwoot.{capability}:{action}"
        for capability, spec in CHATWOOT_CAPABILITIES.items()
        for action in (*spec.actions, *spec.blocked_actions)
    ]
    action_ids.extend(
        f"rocketchat.{capability}:{action}"
        for capability, actions in _CAPABILITY_ACTIONS.items()
        for action in actions
    )
    action_ids.extend(
        f"chattermate.{capability}:{action}"
        for capability, spec in CAPABILITY_SPECS.items()
        for action in spec.commands
    )
    action_ids.extend(
        f"bottlecrm.{spec.key}:{action}"
        for spec in BOTTLECRM_CAPABILITIES
        for action in spec.actions
    )
    if len(action_ids) != _EXPECTED_ACTION_OCCURRENCES or len(set(action_ids)) != len(
        action_ids
    ):
        raise RuntimeError("O contrato estático das ações OSS nativas divergiu de 511 IDs.")
    return sorted(action_ids)


def _verification_source_files() -> tuple[Path, ...]:
    root = Path(__file__).resolve().parents[2]
    relative_paths: set[Path] = set()
    excluded_parts = {
        "__pycache__",
        ".pytest_cache",
        "coverage",
        "dist",
        "dist-app",
        "dist-blackbox",
        "node_modules",
        "playwright-report",
        "test-results",
    }
    for relative_root in _VERIFICATION_ROOTS:
        source_root = root / relative_root
        if not source_root.is_dir():
            continue
        for directory, directory_names, file_names in os.walk(source_root):
            directory_names[:] = [
                name for name in directory_names if name not in excluded_parts
            ]
            directory_path = Path(directory)
            for file_name in file_names:
                path = directory_path / file_name
                if not path.is_file():
                    continue
                relative_path = path.relative_to(root)
                relative_text = relative_path.as_posix()
                if any(
                    relative_text == prefix.rstrip("/") or relative_text.startswith(prefix)
                    for prefix in _VERIFICATION_EXCLUDED_PREFIXES
                ):
                    continue
                relative_paths.add(relative_path)
    relative_paths.update(Path(path) for path in _VERIFICATION_FILES)
    return tuple(sorted(relative_paths, key=lambda path: path.as_posix()))


def _verification_files_digest(*, refresh: bool = False) -> str:
    global _VERIFICATION_DIGEST_CACHE

    root = Path(__file__).resolve().parents[2]
    source_files = _verification_source_files()
    signature: list[tuple[str, int, int, int]] = []
    for relative_path in source_files:
        path = root / relative_path
        if not path.is_file():
            return "missing:" + relative_path.as_posix()
        stat = path.stat()
        signature.append(
            (relative_path.as_posix(), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        )
    signature_key = tuple(signature)
    if not refresh and _VERIFICATION_DIGEST_CACHE is not None:
        cached_signature, cached_digest = _VERIFICATION_DIGEST_CACHE
        if cached_signature == signature_key:
            return cached_digest

    digest = hashlib.sha256()
    for relative_path in source_files:
        path = root / relative_path
        digest.update(relative_path.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    result = digest.hexdigest()
    _VERIFICATION_DIGEST_CACHE = (signature_key, result)
    return result


def _verification_file_count() -> int:
    return len(_verification_source_files())


def _verification_source_inventory() -> list[dict[str, Any]]:
    root = Path(__file__).resolve().parents[2]
    inventory: list[dict[str, Any]] = []
    for relative_path in _verification_source_files():
        payload = (root / relative_path).read_bytes()
        inventory.append(
            {
                "path": relative_path.as_posix(),
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    return inventory


# Capture on first use instead of module import. Importing the API must not read
# the complete verification corpus; once captured, the digest remains fixed for
# the process and later source changes still fail closed.
_RUNTIME_SOURCE_DIGEST: str | None = None
_RUNTIME_SOURCE_FILE_COUNT = _verification_file_count()


def _runtime_source_digest() -> str:
    global _RUNTIME_SOURCE_DIGEST
    if _RUNTIME_SOURCE_DIGEST is None:
        _RUNTIME_SOURCE_DIGEST = _verification_files_digest(refresh=True)
    return _RUNTIME_SOURCE_DIGEST


def local_runtime_source_binding() -> dict[str, Any]:
    """Bind loopback QA to the source captured on the process's first proof use."""

    runtime_digest = _runtime_source_digest()
    current_digest = _verification_files_digest()
    current_file_count = _verification_file_count()
    return {
        "scope": "klm-local-source-binding",
        "runtimeSourceDigest": runtime_digest,
        "runtimeSourceFileCount": _RUNTIME_SOURCE_FILE_COUNT,
        "runtimeSourceMatchesCurrent": (
            runtime_digest == current_digest
            and _RUNTIME_SOURCE_FILE_COUNT == current_file_count
        ),
    }


def _verification_manifest_display_path() -> str:
    root = Path(__file__).resolve().parents[2]
    try:
        return str(_VERIFICATION_MANIFEST.relative_to(root))
    except ValueError:
        return str(_VERIFICATION_MANIFEST)


def _proof_artifact_relative_path(path: Path) -> str:
    try:
        return str(path.relative_to(Path(__file__).resolve().parents[2]))
    except ValueError:
        return str(path)


def _read_source_binding(
    manifest: dict[str, Any],
    *,
    source_digest: str,
    source_file_count: int,
) -> tuple[dict[str, Any] | None, str | None]:
    binding = manifest.get("sourceBinding")
    if not isinstance(binding, dict) or binding.get("mode") != _SOURCE_BINDING_MODE:
        return None, "Vínculo reproduzível do código-fonte ausente ou inválido."
    git_head = str(binding.get("gitHead", ""))
    worktree_dirty = binding.get("worktreeDirty")
    if re.fullmatch(r"[0-9a-f]{40}", git_head) is None or not isinstance(
        worktree_dirty, bool
    ):
        return None, "Proveniência Git do snapshot inválida."

    references = {
        "inventory": (_SOURCE_INVENTORY_ARTIFACT, binding.get("inventory")),
        "snapshot": (_SOURCE_SNAPSHOT_ARTIFACT, binding.get("snapshot")),
    }
    artifacts: dict[str, bytes] = {}
    for name, (path, reference) in references.items():
        if not isinstance(reference, dict):
            return None, f"Referência do {name} de código inválida."
        if reference.get("path") != _proof_artifact_relative_path(path):
            return None, f"Caminho do {name} de código divergente."
        try:
            raw = path.read_bytes()
        except OSError:
            return None, f"Artefato {name} do código ausente."
        if hashlib.sha256(raw).hexdigest() != reference.get("sha256"):
            return None, f"Hash do {name} de código divergente."
        artifacts[name] = raw

    try:
        inventory_payload = json.loads(artifacts["inventory"].decode("utf-8"))
    except (UnicodeError, ValueError, TypeError):
        return None, "Inventário do código inválido."
    current_inventory = _verification_source_inventory()
    if (
        not isinstance(inventory_payload, dict)
        or inventory_payload.get("artifactVersion") != "1.0"
        or inventory_payload.get("mode") != _SOURCE_BINDING_MODE
        or inventory_payload.get("sourceDigest") != source_digest
        or inventory_payload.get("sourceFileCount") != source_file_count
        or inventory_payload.get("gitHead") != git_head
        or inventory_payload.get("worktreeDirty") is not worktree_dirty
        or inventory_payload.get("snapshotSha256") != binding["snapshot"].get("sha256")
        or inventory_payload.get("files") != current_inventory
    ):
        return None, "Inventário do código não corresponde ao runtime atual."

    try:
        snapshot = gzip.decompress(artifacts["snapshot"])
    except (OSError, gzip.BadGzipFile):
        return None, "Snapshot reproduzível do código está corrompido."
    snapshot_reference = binding["snapshot"]
    if hashlib.sha256(snapshot).hexdigest() != snapshot_reference.get(
        "uncompressedSha256"
    ):
        return None, "Hash descompactado do snapshot diverge."

    expected = {item["path"]: item for item in current_inventory}
    try:
        with tarfile.open(fileobj=io.BytesIO(snapshot), mode="r:") as archive:
            members = archive.getmembers()
            if any(not member.isfile() for member in members):
                return None, "Snapshot contém entradas que não são arquivos regulares."
            if [member.name for member in members] != list(expected):
                return None, "Snapshot não contém exatamente o inventário testado."
            for member in members:
                stream = archive.extractfile(member)
                if stream is None:
                    return None, "Snapshot contém arquivo ilegível."
                payload = stream.read()
                item = expected[member.name]
                if len(payload) != item["size"] or hashlib.sha256(payload).hexdigest() != item[
                    "sha256"
                ]:
                    return None, f"Conteúdo divergente no snapshot: {member.name}."
    except (OSError, tarfile.TarError):
        return None, "Snapshot reproduzível do código é inválido."
    return binding, None


def _read_proof_artifacts(
    manifest: dict[str, Any],
    *,
    source_digest: str,
    source_binding: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]] | None, str | None]:
    references = manifest.get("proofArtifacts")
    if not isinstance(references, dict) or set(references) != set(_PROOF_ARTIFACTS):
        return None, "O manifesto não referencia exatamente as cinco provas obrigatórias."
    proofs: dict[str, dict[str, Any]] = {}
    for suite, path in _PROOF_ARTIFACTS.items():
        reference = references.get(suite)
        if not isinstance(reference, dict):
            return None, f"Referência de prova inválida: {suite}."
        if reference.get("path") != _proof_artifact_relative_path(path):
            return None, f"Caminho de prova divergente: {suite}."
        try:
            raw = path.read_bytes()
            proof = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeError, ValueError, TypeError):
            return None, f"Artefato de prova ausente ou inválido: {suite}."
        if hashlib.sha256(raw).hexdigest() != reference.get("sha256"):
            return None, f"Hash do artefato de prova divergente: {suite}."
        if (
            not isinstance(proof, dict)
            or proof.get("proofVersion") != "1.0"
            or proof.get("suite") != suite
            or proof.get("status") != "passed"
            or proof.get("exitCode") != 0
            or proof.get("sourceDigest") != source_digest
            or proof.get("worktreeDigest") != source_digest
            or proof.get("sourceFileCount") != _verification_file_count()
            or proof.get("sourceBindingMode") != _SOURCE_BINDING_MODE
            or proof.get("gitHead") != source_binding.get("gitHead")
            or proof.get("worktreeDirty") is not source_binding.get("worktreeDirty")
            or not isinstance(proof.get("command"), list)
            or not proof.get("command")
            or not str(proof.get("outputSha256", ""))
        ):
            return None, f"Contrato do artefato de prova inválido: {suite}."
        raw_artifacts = proof.get("rawArtifacts")
        if not isinstance(raw_artifacts, list) or not raw_artifacts:
            return None, f"Prova bruta ausente: {suite}."
        output_digest_found = False
        for raw_reference in raw_artifacts:
            if not isinstance(raw_reference, dict):
                return None, f"Referência de prova bruta inválida: {suite}."
            raw_path = Path(__file__).resolve().parents[2] / str(
                raw_reference.get("path", "")
            )
            try:
                raw_path.resolve().relative_to(_RAW_PROOF_DIR.resolve())
                raw_bytes = raw_path.read_bytes()
            except (OSError, ValueError):
                return None, f"Prova bruta fora do diretório permitido: {suite}."
            try:
                uncompressed = gzip.decompress(raw_bytes)
            except (OSError, gzip.BadGzipFile):
                return None, f"Prova bruta compactada inválida: {suite}."
            if hashlib.sha256(raw_bytes).hexdigest() != raw_reference.get("sha256"):
                return None, f"Hash da prova bruta divergente: {suite}."
            uncompressed_digest = hashlib.sha256(uncompressed).hexdigest()
            if uncompressed_digest != raw_reference.get("uncompressedSha256"):
                return None, f"Hash do conteúdo bruto divergente: {suite}."
            if raw_path.name.endswith("output.log.gz"):
                output_digest_found = uncompressed_digest == proof.get("outputSha256")
        if not output_digest_found:
            return None, f"Saída executada não vinculada à prova: {suite}."
        proofs[suite] = proof
    return proofs, None


def _qa_verification_uncached() -> dict[str, Any]:
    expected = _verification_contract()
    expected_native_ids, expected_contract_ids = _verification_ids()
    expected_action_ids = _verification_action_ids()
    source_digest = _verification_files_digest()
    source_file_count = _verification_file_count()
    runtime_digest = _runtime_source_digest()
    base = {
        "state": "unverified",
        "reason": "Manifesto de QA ausente ou incompatível com o código atual.",
        "manifestPath": _verification_manifest_display_path(),
        "generatedAt": None,
        "nativeIdsVerified": False,
        "contractIdsVerified": False,
        "actionIdsVerified": False,
        "verifiedNativeCapabilityIds": [],
        "verifiedContractCapabilityIds": [],
        "verifiedActionIdsDigest": None,
        "actionOccurrencesContractValidated": 0,
        "uniqueCommandsContractValidated": 0,
        "externalEffectsExecuted": None,
        "externalNetworkAssertionsValidated": 0,
        "externalEffectEvidenceScope": None,
        "externalEffectIndependentlyObserved": None,
        "verificationFileCount": source_file_count,
        "runtimeSourceDigest": runtime_digest,
        "runtimeSourceFileCount": _RUNTIME_SOURCE_FILE_COUNT,
        "runtimeSourceMatchesCurrent": (
            runtime_digest == source_digest
            and _RUNTIME_SOURCE_FILE_COUNT == source_file_count
        ),
        **expected,
    }
    if not base["runtimeSourceMatchesCurrent"]:
        return {
            **base,
            "reason": "O processo local executa uma versão anterior do código; reinicie o KLM.",
        }
    try:
        manifest = json.loads(_VERIFICATION_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return base
    required_matches = {
        **expected,
        "verificationFilesDigest": source_digest,
        "verificationFileCount": source_file_count,
        "actionOccurrencesContractValidated": _EXPECTED_ACTION_OCCURRENCES,
        "uniqueCommandsContractValidated": _EXPECTED_UNIQUE_COMMANDS,
        "externalEffectsExecuted": 0,
        "externalNetworkAssertionsValidated": _EXPECTED_ACTION_OCCURRENCES,
        "externalEffectEvidenceScope": "application_reported_dry_run",
        "externalEffectIndependentlyObserved": False,
    }
    if manifest.get("schemaVersion") != "1.2" or any(
        manifest.get(key) != value for key, value in required_matches.items()
    ):
        return {**base, "reason": "O código ou o contrato mudou após a última prova de QA."}
    source_binding, binding_error = _read_source_binding(
        manifest,
        source_digest=source_digest,
        source_file_count=source_file_count,
    )
    if source_binding is None:
        return {**base, "reason": binding_error or "Snapshot do código inválido."}
    proofs, proof_error = _read_proof_artifacts(
        manifest,
        source_digest=source_digest,
        source_binding=source_binding,
    )
    if proofs is None:
        return {**base, "reason": proof_error or "Cadeia de provas inválida."}

    backend = proofs["backend"]
    frontend_unit = proofs["frontend-unit"]
    frontend_e2e = proofs["frontend-e2e"]
    local_runtime = proofs["local-runtime"]
    upstream_locks = proofs["upstream-locks"]
    proof_contract_valid = all(
        (
            backend.get("failed") == 0,
            backend.get("errors") == 0,
            int(backend.get("passed", 0)) >= 1_435,
            backend.get("ruffPassed") is True,
            backend.get("diffCheckPassed") is True,
            frontend_unit.get("failed") == 0,
            int(frontend_unit.get("passed", 0)) >= 124,
            frontend_unit.get("typecheckPassed") is True,
            frontend_unit.get("lintPassed") is True,
            frontend_unit.get("buildPassed") is True,
            frontend_e2e.get("failed") == 0,
            int(frontend_e2e.get("passed", 0)) >= 203,
            frontend_e2e.get("actionOccurrencesExposed") == _EXPECTED_ACTION_OCCURRENCES,
            int(local_runtime.get("checks", 0)) >= _MIN_LOCAL_RUNTIME_CHECKS,
            local_runtime.get("externalActionsExecuted") == 0,
            int(local_runtime.get("nativeDocumentsValidated", 0)) >= 1,
            int(local_runtime.get("actionContractsValidated", 0)) >= 1,
            local_runtime.get("externalNetworkExecutions") == 0,
            local_runtime.get("externalNetworkAssertions")
            == _EXPECTED_ACTION_OCCURRENCES,
            local_runtime.get("externalEffectEvidenceScope")
            == "application_reported_dry_run",
            local_runtime.get("externalEffectIndependentlyObserved") is False,
            local_runtime.get("serverRuntimeSourceDigest") == source_digest,
            local_runtime.get("serverRuntimeSourceFileCount") == source_file_count,
            local_runtime.get("serverRuntimeSourceMatchesCurrent") is True,
            upstream_locks.get("upstreamLocksVerified") == 5,
            upstream_locks.get("sidecarProfilesConfigured") == 4,
            upstream_locks.get("sidecarPrimaryRequired") is False,
            upstream_locks.get("sidecarOffProofPassed") is True,
        )
    )
    if not proof_contract_valid:
        return {**base, "reason": "Uma prova não atingiu os gates quantitativos obrigatórios."}

    expected_native_set = set(expected_native_ids)
    expected_contract_set = set(expected_contract_ids)
    expected_action_set = set(expected_action_ids)
    expected_public_native_set = {
        _canonical_capability_id(value) for value in expected_native_set
    }
    e2e_native = set(frontend_e2e.get("nativeCapabilityIds") or [])
    runtime_native = set(local_runtime.get("nativeDocumentCapabilityIds") or [])
    runtime_contract = set(local_runtime.get("contractCapabilityIds") or [])
    runtime_action_ids = set(local_runtime.get("actionContractIds") or [])
    if any(
        (
            local_runtime.get("nativeDocumentsValidated") != len(runtime_native),
            local_runtime.get("contractCapabilitiesValidated") != len(runtime_contract),
            local_runtime.get("actionContractsValidated") != len(runtime_action_ids),
        )
    ):
        return {**base, "reason": "As contagens da prova HTTP divergem dos IDs exercitados."}
    if (
        not e2e_native <= expected_public_native_set
        or not runtime_native <= expected_native_set
        or not runtime_contract <= expected_contract_set
        or not runtime_action_ids <= expected_action_set
    ):
        return {**base, "reason": "Uma prova contém IDs fora do catálogo revisado."}
    e2e_verified_internal_ids = {
        value
        for value in expected_native_set
        if _canonical_capability_id(value) in e2e_native
    }
    verified_native_ids = sorted(
        expected_native_set & runtime_native & e2e_verified_internal_ids
    )
    verified_contract_ids = sorted(
        expected_contract_set & runtime_contract & e2e_verified_internal_ids
    )
    all_native_verified = verified_native_ids == expected_native_ids
    all_contracts_verified = verified_contract_ids == expected_contract_ids
    all_action_ids_verified = sorted(runtime_action_ids) == expected_action_ids
    proof_runs = [
        {
            "id": suite,
            "status": proof["status"],
            "command": proof["command"],
            "generatedAt": proof.get("generatedAt"),
            "outputSha256": proof["outputSha256"],
            "artifactSha256": manifest["proofArtifacts"][suite]["sha256"],
        }
        for suite, proof in proofs.items()
    ]
    return {
        **base,
        "state": (
            "verified"
            if all_native_verified and all_contracts_verified and all_action_ids_verified
            else "partial"
        ),
        "reason": (
            "Rotas, documentos e validações dry-run cobrem o contrato; as 511 ações declararam zero execução externa no nível da aplicação, sem observação independente de sockets."
            if all_native_verified and all_contracts_verified and all_action_ids_verified
            else "Artefatos íntegros cobrem apenas parte dos IDs do contrato atual."
        ),
        "generatedAt": manifest.get("generatedAt"),
        "nativeIdsVerified": all_native_verified,
        "contractIdsVerified": all_contracts_verified,
        "actionIdsVerified": all_action_ids_verified,
        "verifiedNativeCapabilityIds": verified_native_ids,
        "verifiedContractCapabilityIds": verified_contract_ids,
        "verifiedActionIdsDigest": (
            _stable_digest(sorted(runtime_action_ids)) if all_action_ids_verified else None
        ),
        "verificationFilesDigest": required_matches["verificationFilesDigest"],
        "sourceBindingMode": source_binding["mode"],
        "sourceGitHead": source_binding["gitHead"],
        "sourceWorktreeDirty": source_binding["worktreeDirty"],
        "sourceSnapshotSha256": source_binding["snapshot"]["sha256"],
        "actionOccurrencesContractValidated": _EXPECTED_ACTION_OCCURRENCES,
        "uniqueCommandsContractValidated": _EXPECTED_UNIQUE_COMMANDS,
        "externalEffectsExecuted": manifest.get("externalEffectsExecuted"),
        "externalNetworkAssertionsValidated": manifest.get(
            "externalNetworkAssertionsValidated"
        ),
        "externalEffectEvidenceScope": manifest.get("externalEffectEvidenceScope"),
        "externalEffectIndependentlyObserved": manifest.get(
            "externalEffectIndependentlyObserved"
        ),
        "proofRuns": proof_runs,
    }


def _qa_verification_cache_signature() -> tuple[Any, ...]:
    paths = {
        _VERIFICATION_MANIFEST,
        _SOURCE_INVENTORY_ARTIFACT,
        _SOURCE_SNAPSHOT_ARTIFACT,
        *_PROOF_ARTIFACTS.values(),
    }
    if _RAW_PROOF_DIR.is_dir():
        paths.update(path for path in _RAW_PROOF_DIR.rglob("*") if path.is_file())
    signature: list[tuple[str, bool, int, int, int]] = []
    for path in sorted(paths, key=lambda item: str(item)):
        try:
            stat = path.stat()
        except OSError:
            signature.append((str(path), False, 0, 0, 0))
            continue
        signature.append((str(path), True, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
    return tuple(signature)


def _qa_verification() -> dict[str, Any]:
    global _QA_VERIFICATION_CACHE

    signature = (
        _verification_files_digest(),
        _verification_file_count(),
        _qa_verification_cache_signature(),
    )
    if _QA_VERIFICATION_CACHE is not None and _QA_VERIFICATION_CACHE[0] == signature:
        return _QA_VERIFICATION_CACHE[1]
    result = _qa_verification_uncached()
    _QA_VERIFICATION_CACHE = (signature, result)
    return result


def _capability(
    provider: str,
    key: str,
    label: str,
    category: str,
    description: str,
    disposition: str,
    current_exposure: str,
    *,
    surface: str,
    route: str | None = None,
    target_route: str | None = None,
    reason: str,
    upstream_evidence: tuple[str, ...],
    klm_evidence: tuple[str, ...] = (),
    test_evidence: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "id": f"{provider}.{key}",
        "provider": provider,
        "key": key,
        "label": label,
        "category": category,
        "description": description,
        "disposition": disposition,
        "currentExposure": current_exposure,
        "surface": surface,
        "route": route,
        "targetRoute": target_route,
        "reason": reason,
        "upstreamEvidence": list(upstream_evidence),
        "klmEvidence": list(klm_evidence),
        "testEvidence": list(test_evidence),
    }


_CAPABILITIES: tuple[dict[str, Any], ...] = (
    # Chatwoot: external customer service and omnichannel transport.
    _capability(
        "chatwoot",
        "inboxes-routing",
        "Caixas de entrada e roteamento",
        "atendimento",
        "Opera inboxes, status, prioridade, distribuição e filas de atendimento.",
        "approved_native",
        "bridge",
        surface="Conversas",
        route="/app/omnichannel",
        target_route="/app/conversas",
        reason=(
            "O KLM lista inboxes e opções de roteamento e executa atualização de status, "
            "prioridade, equipe, responsável e rótulos em conversas locais vinculadas. "
            "A projeção de workspace e thread é auditada e não altera entrega externa."
        ),
        upstream_evidence=(
            ".vendor/upstreams/chatwoot/app/javascript/dashboard/routes/dashboard/inbox",
        ),
        klm_evidence=(
            "app/services/chatwoot_operations_service.py",
            "frontend/src/klm/components/NativeInboxRoutingPanel.tsx",
        ),
        test_evidence=("tests/services/test_chatwoot_operations_service.py",),
    ),
    _capability(
        "chatwoot",
        "contacts",
        "Contatos",
        "crm",
        "Pesquisa e cria contatos usados para abrir conversas.",
        "approved_native",
        "bridge",
        surface="Caso 360",
        route="/app/leads",
        target_route="/app/leads",
        reason="A ponte pesquisa/cria o contato mínimo e mantém pessoa e caso canônicos no KLM; não sincroniza empresas, segmentos ou campos arbitrários.",
        upstream_evidence=(
            ".vendor/upstreams/chatwoot/app/javascript/dashboard/routes/dashboard/contacts",
        ),
        klm_evidence=(
            "app/services/chatwoot_sync_service.py",
            "frontend/src/klm/pages/LeadPage.tsx",
        ),
        test_evidence=("tests/services/test_chatwoot_sync_service.py",),
    ),
    _capability(
        "chatwoot",
        "companies-segments",
        "Segmentos de contatos",
        "crm",
        "Organiza segmentos, filtros e públicos de contatos.",
        "implemented",
        "full_app",
        surface="Caso 360",
        route="/app/leads",
        target_route="/app/leads",
        reason="Segmentos possuem leitura e CRUD tipado no KLM, com validação e auditoria; empresas Cloud/Enterprise permanecem separadas por não existirem no sidecar comunitário.",
        upstream_evidence=(
            ".vendor/upstreams/chatwoot/app/javascript/dashboard/routes/dashboard/contacts/routes.js",
        ),
    ),
    _capability(
        "chatwoot",
        "conversation-composer",
        "Conversas, timeline e composer",
        "atendimento",
        "Exibe histórico e permite mensagens de texto e ações básicas da conversa.",
        "approved_native",
        "bridge",
        surface="Conversas",
        route="/app/conversas",
        target_route="/app/conversas",
        reason="O KLM compõe mensagens e notas, atualiza roteamento e reconcilia a conversa; anexos usam a capacidade documental própria para preservar autorização e RAG.",
        upstream_evidence=(
            ".vendor/upstreams/chatwoot/app/javascript/dashboard/routes/dashboard/conversation",
        ),
        klm_evidence=(
            "app/services/chatwoot_bridge_service.py",
            "frontend/src/klm/pages/ConversationsPage.tsx",
        ),
        test_evidence=(
            "tests/services/test_chatwoot_bridge_service.py",
            "frontend/src/test/conversations-tools.test.tsx",
        ),
    ),
    _capability(
        "chatwoot",
        "attachments-media",
        "Anexos e mídia",
        "documentos",
        "Envia, recebe, pré-visualiza e baixa anexos e mídia da conversa.",
        "implemented",
        "native",
        surface="Conversas",
        route="/app/conversas",
        target_route="/app/conversas",
        reason="Upload/download autorizado, metadados, armazenamento versionado, retenção por objeto e indexação explícita no RAG do caso são nativos; produção continua condicionada ao veredito antivírus real.",
        upstream_evidence=(
            ".vendor/upstreams/chatwoot/app/controllers/api/v1/accounts/contacts/attachments_controller.rb",
        ),
        klm_evidence=(
            "app/services/document_service.py",
            "app/services/knowledge_service.py",
            "app/services/local_object_storage_service.py",
        ),
        test_evidence=(
            "tests/api/test_document_storage_convergence.py",
            "tests/services/test_oss_native_document_matrix.py",
        ),
    ),
    _capability(
        "chatwoot",
        "private-notes",
        "Notas privadas",
        "colaboracao",
        "Registra observações internas que nunca podem ser enviadas ao cliente.",
        "approved_native",
        "native",
        surface="Conversas",
        route="/app/conversas",
        target_route="/app/conversas",
        reason="Notas privadas podem ser lidas, criadas e removidas no KLM, com autoria, vínculo da conversa e estado reconciliados pelo adapter local.",
        upstream_evidence=(
            ".vendor/upstreams/chatwoot/app/javascript/dashboard/components/widgets/conversation",
        ),
        klm_evidence=("frontend/src/klm/api.ts", "app/services/klm_operations_service.py"),
        test_evidence=("frontend/src/test/conversations-tools.test.tsx",),
    ),
    _capability(
        "chatwoot",
        "labels-status-assignment",
        "Rótulos, status, atribuição e equipes",
        "operacao",
        "Classifica, prioriza e define responsáveis individuais ou por time.",
        "implemented",
        "bridge",
        surface="Conversas",
        route="/app/omnichannel",
        target_route="/app/conversas",
        reason="O painel contextual lê e altera status, prioridade, labels, responsável e equipe no sidecar local, com permissão KLM e vínculo por thread.",
        upstream_evidence=(
            ".vendor/upstreams/chatwoot/app/javascript/dashboard/components",
            ".vendor/upstreams/chatwoot/app/javascript/dashboard/routes/dashboard/settings",
        ),
        klm_evidence=(
            "app/services/chatwoot_operations_service.py",
            "frontend/src/klm/components/NativeInboxRoutingPanel.tsx",
        ),
        test_evidence=(
            "tests/services/test_chatwoot_operations_service.py",
            "tests/api/test_oss_adapters.py",
        ),
    ),
    _capability(
        "chatwoot",
        "canned-responses-macros",
        "Respostas prontas e macros",
        "produtividade",
        "Biblioteca reutilizável e ações compostas para atendimento humano.",
        "approved_full_app",
        "full_app",
        surface="Conversas",
        route="/app/ferramentas",
        target_route="/app/conversas",
        reason="Respostas prontas e macros possuem leitura, CRUD e preview nativos no KLM; execução externa continua separada do preview local.",
        upstream_evidence=(".vendor/upstreams/chatwoot/config/routes.rb",),
        klm_evidence=("frontend/src/klm/pages/ToolsPage.tsx",),
    ),
    _capability(
        "chatwoot",
        "automation-rules",
        "Automações de atendimento",
        "automacao",
        "Executa regras por evento, condição e ação no atendimento.",
        "approved_full_app",
        "full_app",
        surface="Flows",
        route="/app/flows",
        target_route="/app/flows",
        reason=(
            "O KLM lista e administra regras Chatwoot inativas: cria, edita, exclui e clona "
            "com auditoria. Ativação e execução permanecem sob gate com configuração e "
            "dry-run local, sem despacho externo."
        ),
        upstream_evidence=(
            ".vendor/upstreams/chatwoot/app/javascript/dashboard/routes/dashboard/settings/automation",
        ),
        klm_evidence=("frontend/src/klm/pages/FlowsPage.tsx",),
    ),
    _capability(
        "chatwoot",
        "campaigns",
        "Campanhas de live chat",
        "marketing",
        "Cria campanhas comunitárias de live chat, segmentação e acompanhamento.",
        "approved_full_app",
        "full_app",
        surface="Disparos",
        route="/whatsapp/campaigns",
        target_route="/whatsapp/campaigns",
        reason="A rota comunitária de live chat pode ser administrada localmente; WhatsApp e outros transportes foram separados sob gate externo.",
        upstream_evidence=(
            ".vendor/upstreams/chatwoot/app/javascript/dashboard/routes/dashboard/campaigns",
        ),
        klm_evidence=("frontend/src/pages/Campaigns.tsx",),
        test_evidence=("tests/workers/test_whatsapp_campaign_tasks.py",),
    ),
    _capability(
        "chatwoot",
        "reports-csat",
        "Relatórios e CSAT",
        "analytics",
        "Mede volume, produtividade, tempo de resposta e satisfação.",
        "approved_full_app",
        "full_app",
        surface="KPIs",
        route="/app/kpis",
        target_route="/app/kpis",
        reason="Relatórios e CSAT estão disponíveis no motor; não existe coletor KLM para esses indicadores nem drill-down tipado nesta etapa.",
        upstream_evidence=(".vendor/upstreams/chatwoot/config/routes.rb",),
        klm_evidence=("frontend/src/klm/pages/KpisPage.tsx",),
    ),
    _capability(
        "chatwoot",
        "sla",
        "Políticas de SLA",
        "analytics",
        "Configura políticas de SLA e seus relatórios.",
        "blocked_license",
        "none",
        surface="KPIs",
        target_route="/app/kpis",
        reason="A versão comunitária fixada exibe paywall Enterprise para SLA; o KLM manterá seu próprio cálculo sem declarar esse módulo como incorporado.",
        upstream_evidence=(
            ".vendor/upstreams/chatwoot/app/javascript/dashboard/routes/dashboard/settings/sla/SLAPaywallEnterprise.vue",
        ),
    ),
    _capability(
        "chatwoot",
        "custom-attributes-contact-operations",
        "Campos, notas e operações de contato",
        "dados",
        "Administra campos customizados, notas, filtros e importação/exportação de contatos.",
        "implemented",
        "native",
        surface="CRM",
        route="/app/leads",
        target_route="/app/leads",
        reason="Campos e notas possuem CRUD tipado no KLM; importação e exportação têm validação e dry-run sem alterar o CRM jurídico automaticamente.",
        upstream_evidence=(
            ".vendor/upstreams/chatwoot/app/models/custom_attribute_definition.rb",
            ".vendor/upstreams/chatwoot/app/javascript/dashboard/api/contactNotes.js",
            ".vendor/upstreams/chatwoot/config/routes.rb",
        ),
    ),
    _capability(
        "chatwoot",
        "agents-teams-availability",
        "Agentes, equipes e disponibilidade",
        "operacao",
        "Administra atendentes, equipes, disponibilidade e capacidade de atendimento.",
        "approved_full_app",
        "full_app",
        surface="Configurações",
        route="/app/configuracoes",
        target_route="/app/configuracoes",
        reason="Agentes, equipes e disponibilidade possuem CRUD nativo e reconciliação local, sujeitos ao RBAC de gestão do KLM.",
        upstream_evidence=(
            ".vendor/upstreams/chatwoot/config/routes.rb",
            ".vendor/upstreams/chatwoot/app/javascript/dashboard/routes/dashboard/settings",
        ),
    ),
    _capability(
        "chatwoot",
        "agent-bots-integrations",
        "Bots, integrações e apps de painel",
        "integracoes",
        "Configura agent bots, integrações nativas e apps exibidos no painel.",
        "approved_full_app",
        "full_app",
        surface="Ferramentas",
        route="/app/ferramentas",
        target_route="/app/ferramentas",
        reason="Os cadastros locais estão preservados no motor; chamadas externas e bots com efeitos reais continuam sujeitos a gates específicos.",
        upstream_evidence=(
            ".vendor/upstreams/chatwoot/app/models/agent_bot.rb",
            ".vendor/upstreams/chatwoot/app/controllers/api/v1/accounts/integrations/apps_controller.rb",
        ),
    ),
    _capability(
        "chatwoot",
        "audit-log",
        "Log de auditoria upstream",
        "governanca",
        "Consulta trilha de auditoria nativa do Chatwoot.",
        "blocked_license",
        "none",
        surface="Atividade",
        target_route="/app/atividade",
        reason="A rota aparece no checkout comunitário, mas o controlador correspondente não está materializado; a auditoria canônica permanece no KLM.",
        upstream_evidence=(
            ".vendor/upstreams/chatwoot/config/routes.rb",
            ".vendor/upstreams/chatwoot/app/javascript/dashboard/api/auditLogs.js",
        ),
        klm_evidence=("app/models/klm_operations.py",),
    ),
    _capability(
        "chatwoot",
        "help-center",
        "Central de ajuda",
        "conteudo",
        "Publica artigos e coleções de autoatendimento.",
        "approved_full_app",
        "full_app",
        surface="Conteúdo",
        route="/app/ferramentas",
        target_route="/app/ferramentas",
        reason="Preservada para materiais educativos e iscas; não substitui o RAG jurídico por caso.",
        upstream_evidence=(
            ".vendor/upstreams/chatwoot/app/javascript/dashboard/routes/dashboard/helpcenter",
        ),
    ),
    _capability(
        "chatwoot",
        "api-webhooks-realtime",
        "Ponte de contato, conversa, texto e webhook",
        "plataforma",
        "Pesquisa/cria contatos, abre conversas, envia texto e ingere eventos idempotentes.",
        "implemented",
        "bridge",
        surface="Infraestrutura",
        route="/app/ferramentas",
        target_route="/app/ferramentas",
        reason="Este escopo estreito possui implementação e testes; não inclui roteamento, anexos, macros, relatórios ou administração do sidecar.",
        upstream_evidence=(
            ".vendor/upstreams/chatwoot/app/controllers/api/v1",
            ".vendor/upstreams/chatwoot/app/jobs/webhooks",
        ),
        klm_evidence=(
            "app/services/chatwoot_bridge_service.py",
            "app/services/chatwoot_sync_service.py",
        ),
        test_evidence=(
            "tests/services/test_chatwoot_bridge_service.py",
            "tests/services/test_chatwoot_sync_service.py",
        ),
    ),
    _capability(
        "chatwoot",
        "external-channels",
        "WhatsApp, e-mail e redes reais",
        "canais",
        "Conecta canais oficiais externos e entrega mensagens reais.",
        "blocked_external_gate",
        "none",
        surface="Integrações",
        route="/app/ferramentas",
        target_route="/app/configuracoes",
        reason="Depende de credenciais e aprovação expressa; nenhum efeito externo é permitido nesta meta.",
        upstream_evidence=(".vendor/upstreams/chatwoot/app/models/channel",),
        klm_evidence=("app/core/config.py",),
        test_evidence=("tests/services/test_remaining_external_sinks.py",),
    ),
    _capability(
        "chatwoot",
        "conversation-search-custom-views",
        "Busca, menções e visões de conversa",
        "produtividade",
        "Pesquisa conversas e abre visões por inbox, rótulo, equipe, menção, participação e filtros salvos.",
        "approved_full_app",
        "full_app",
        surface="Conversas",
        route="/app/conversas",
        target_route="/app/conversas",
        reason=(
            "O KLM pesquisa conversas e cria, edita ou exclui visões salvas no sidecar local, "
            "sempre no contexto autenticado do workspace e com auditoria das mutações."
        ),
        upstream_evidence=(
            ".vendor/upstreams/chatwoot/app/javascript/dashboard/modules/search/search.routes.js",
            ".vendor/upstreams/chatwoot/app/javascript/dashboard/routes/dashboard/conversation/conversation.routes.js",
        ),
    ),
    _capability(
        "chatwoot",
        "notifications",
        "Central de notificações",
        "operacao",
        "Consulta notificações internas do atendimento e navega ao item relacionado.",
        "approved_full_app",
        "full_app",
        surface="Atividade",
        route="/app/atividade",
        target_route="/app/atividade",
        reason="A central está disponível no sidecar local; o KLM mantém sua auditoria canônica e oferece acesso contextual ao motor.",
        upstream_evidence=(
            ".vendor/upstreams/chatwoot/app/javascript/dashboard/routes/dashboard/notifications/routes.js",
        ),
    ),
    _capability(
        "chatwoot",
        "companies",
        "Empresas",
        "crm",
        "Lista empresas e abre seus contatos e conversas associados.",
        "blocked_license",
        "none",
        surface="Caso 360",
        target_route="/app/leads",
        reason="A rota pública declara instalação Cloud/Enterprise e o runtime comunitário local rejeita a API; não é alegada como função OSS operacional.",
        upstream_evidence=(
            ".vendor/upstreams/chatwoot/app/javascript/dashboard/routes/dashboard/companies/routes.js",
        ),
    ),
    _capability(
        "chatwoot",
        "canned-responses",
        "Respostas prontas",
        "produtividade",
        "Administra textos reutilizáveis para o atendimento humano.",
        "approved_full_app",
        "full_app",
        surface="Conversas",
        route="/app/conversas",
        target_route="/app/conversas",
        reason="A biblioteca comunitária é distinta das macros e agora possui destino próprio no motor.",
        upstream_evidence=(
            ".vendor/upstreams/chatwoot/app/javascript/dashboard/routes/dashboard/settings/canned/canned.routes.js",
        ),
    ),
    _capability(
        "chatwoot",
        "teams",
        "Equipes de atendimento",
        "operacao",
        "Cria equipes, associa agentes e controla sua composição.",
        "approved_full_app",
        "full_app",
        surface="Configurações",
        route="/app/configuracoes",
        target_route="/app/configuracoes",
        reason="Equipes e membros possuem CRUD nativo e reconciliação no sidecar; o RBAC KLM continua como autoridade e torna divergências visíveis.",
        upstream_evidence=(
            ".vendor/upstreams/chatwoot/app/javascript/dashboard/routes/dashboard/settings/teams/teams.routes.js",
        ),
    ),
    _capability(
        "chatwoot",
        "agent-bots",
        "Bots de atendimento",
        "agentes",
        "Administra agent bots e sua associação a inboxes.",
        "approved_full_app",
        "full_app",
        surface="Agentes",
        route="/app/agentes",
        target_route="/app/agentes",
        reason="O cadastro local é preservado; execução de modelo ou entrega externa continua sujeita aos gates KLM.",
        upstream_evidence=(
            ".vendor/upstreams/chatwoot/app/javascript/dashboard/routes/dashboard/settings/agentBots/agentBot.routes.js",
        ),
    ),
    _capability(
        "chatwoot",
        "inbox-administration",
        "Administração de inboxes",
        "atendimento",
        "Cria e configura inboxes, canais, agentes e opções de cada caixa.",
        "approved_full_app",
        "full_app",
        surface="Omnichannel",
        route="/app/omnichannel",
        target_route="/app/omnichannel",
        reason="Inboxes e membros possuem administração nativa no KLM; transporte por canal real continua bloqueado até credenciais e aprovação.",
        upstream_evidence=(
            ".vendor/upstreams/chatwoot/app/javascript/dashboard/routes/dashboard/settings/inbox/inbox.routes.js",
        ),
    ),
    _capability(
        "chatwoot",
        "assignment-policy",
        "Políticas de atribuição",
        "operacao",
        "Configura regras comunitárias de atribuição e distribuição de conversas.",
        "approved_full_app",
        "full_app",
        surface="Omnichannel",
        route="/app/omnichannel",
        target_route="/app/omnichannel",
        reason="Políticas de atribuição possuem leitura e CRUD nativos; opções avançadas são exibidas somente quando a edição comunitária instalada as oferece.",
        upstream_evidence=(
            ".vendor/upstreams/chatwoot/app/javascript/dashboard/routes/dashboard/settings/assignmentPolicy/assignmentPolicy.routes.js",
        ),
    ),
    _capability(
        "chatwoot",
        "conversation-workflow",
        "Workflow de encerramento de conversa",
        "automacao",
        "Configura campos e etapas exigidos ao resolver uma conversa.",
        "approved_full_app",
        "full_app",
        surface="Flows",
        route="/app/flows",
        target_route="/app/flows",
        reason="Atributos obrigatórios do workflow de atendimento são administrados no KLM e permanecem isolados dos flows jurídicos versionados.",
        upstream_evidence=(
            ".vendor/upstreams/chatwoot/app/javascript/dashboard/routes/dashboard/settings/conversationWorkflow/conversationWorkflow.routes.js",
        ),
    ),
    _capability(
        "chatwoot",
        "profile-mfa",
        "Perfil e autenticação multifator",
        "seguranca",
        "Administra perfil do operador e MFA quando habilitado na instalação.",
        "approved_full_app",
        "full_app",
        surface="Configurações",
        route="/app/configuracoes",
        target_route="/app/configuracoes",
        reason="A sessão do sidecar mantém perfil próprio; identidade e permissões canônicas continuam no KLM.",
        upstream_evidence=(
            ".vendor/upstreams/chatwoot/app/javascript/dashboard/routes/dashboard/settings/profile/profile.routes.js",
        ),
    ),
    _capability(
        "chatwoot",
        "account-general",
        "Configurações gerais da conta",
        "administracao",
        "Configura nome, idioma, fuso e preferências gerais da conta local.",
        "approved_full_app",
        "full_app",
        surface="Configurações",
        route="/app/configuracoes",
        target_route="/app/configuracoes",
        reason="A configuração do sidecar fica no motor e não altera os dados canônicos do workspace KLM.",
        upstream_evidence=(
            ".vendor/upstreams/chatwoot/app/javascript/dashboard/routes/dashboard/settings/account/account.routes.js",
        ),
    ),
    _capability(
        "chatwoot",
        "labels-administration",
        "Administração de rótulos",
        "operacao",
        "Cria e organiza rótulos usados para classificar contatos e conversas.",
        "approved_full_app",
        "full_app",
        surface="Omnichannel",
        route="/app/omnichannel",
        target_route="/app/omnichannel",
        reason="O CRUD de rótulos possui tela própria; aplicar rótulos na conversa continua disponível no painel contextual KLM.",
        upstream_evidence=(
            ".vendor/upstreams/chatwoot/app/javascript/dashboard/routes/dashboard/settings/labels/labels.routes.js",
        ),
    ),
    _capability(
        "chatwoot",
        "dashboard-apps",
        "Apps do painel",
        "integracoes",
        "Administra aplicações exibidas dentro do painel de atendimento.",
        "approved_full_app",
        "full_app",
        surface="Ferramentas",
        route="/app/ferramentas",
        target_route="/app/ferramentas",
        reason="Apps locais podem ser configurados no motor; qualquer origem externa continua sujeita a allowlist e gate próprio.",
        upstream_evidence=(
            ".vendor/upstreams/chatwoot/app/javascript/dashboard/routes/dashboard/settings/integrations/integrations.routes.js",
        ),
    ),
    _capability(
        "chatwoot",
        "csat-report",
        "Relatório de satisfação",
        "analytics",
        "Consulta respostas e indicadores de satisfação do atendimento.",
        "approved_full_app",
        "full_app",
        surface="KPIs",
        route="/app/kpis",
        target_route="/app/kpis",
        reason="O KLM consulta o relatório CSAT autenticado em experiência nativa e o identifica como métrica do sidecar, sem misturá-lo ao KPI jurídico canônico.",
        upstream_evidence=(
            ".vendor/upstreams/chatwoot/app/javascript/dashboard/routes/dashboard/settings/reports/reports.routes.js",
        ),
    ),
    _capability(
        "chatwoot",
        "contact-notes-import-export",
        "Notas e movimentação de contatos",
        "dados",
        "Administra notas, importação, exportação e operações em contatos.",
        "approved_full_app",
        "full_app",
        surface="CRM",
        route="/app/leads",
        target_route="/app/leads",
        reason="As operações ficam na superfície de contatos; dados jurídicos e documentos continuam canônicos no KLM.",
        upstream_evidence=(
            ".vendor/upstreams/chatwoot/app/javascript/dashboard/routes/dashboard/contacts/routes.js",
            ".vendor/upstreams/chatwoot/app/javascript/dashboard/api/contactNotes.js",
        ),
    ),
    _capability(
        "chatwoot",
        "agent-capacity",
        "Capacidade avançada de agentes",
        "operacao",
        "Configura limites avançados de capacidade e distribuição por agente.",
        "blocked_license",
        "none",
        surface="Omnichannel",
        target_route="/app/omnichannel",
        reason="A rota usa a feature ADVANCED_ASSIGNMENT e não é alegada como capacidade comunitária operacional.",
        upstream_evidence=(
            ".vendor/upstreams/chatwoot/app/javascript/dashboard/routes/dashboard/settings/assignmentPolicy/assignmentPolicy.routes.js",
        ),
    ),
    _capability(
        "chatwoot",
        "whatsapp-campaigns",
        "Campanhas de WhatsApp do Chatwoot",
        "marketing",
        "Cria campanhas usando o canal oficial de WhatsApp configurado no Chatwoot.",
        "blocked_external_gate",
        "none",
        surface="Disparos",
        target_route="/whatsapp/campaigns",
        reason="A feature está desabilitada no runtime local e depende de inbox e credenciais do canal oficial; o simulador KLM permanece separado.",
        upstream_evidence=(
            ".vendor/upstreams/chatwoot/app/javascript/dashboard/routes/dashboard/campaigns/campaigns.routes.js",
        ),
    ),
    _capability(
        "chatwoot",
        "captain-ai",
        "Captain AI",
        "agentes",
        "Oferece assistentes, documentos, ferramentas, cenários e playground do Chatwoot.",
        "blocked_external_gate",
        "none",
        surface="Agentes",
        target_route="/app/agentes",
        reason="Depende de configuração de IA e pode consumir provedores externos; o runtime KLM permanece offline/API-gated nesta fase.",
        upstream_evidence=(
            ".vendor/upstreams/chatwoot/app/javascript/dashboard/routes/dashboard/captain/captain.routes.js",
        ),
    ),
    _capability(
        "chatwoot",
        "custom-roles-security",
        "Papéis customizados e segurança avançada",
        "seguranca",
        "Configura papéis customizados, SAML e controles de segurança da edição paga.",
        "blocked_license",
        "none",
        surface="Configurações",
        target_route="/app/configuracoes",
        reason="As rotas públicas declaram instalação Cloud/Enterprise e exibem paywall; não são alegadas como função comunitária operacional.",
        upstream_evidence=(
            ".vendor/upstreams/chatwoot/app/javascript/dashboard/routes/dashboard/settings/customRoles/customRole.routes.js",
            ".vendor/upstreams/chatwoot/app/javascript/dashboard/routes/dashboard/settings/security/security.routes.js",
        ),
    ),
    _capability(
        "chatwoot",
        "billing-cloud",
        "Billing Cloud",
        "administracao",
        "Exibe faturamento da oferta SaaS do Chatwoot.",
        "not_applicable",
        "none",
        surface="Configurações",
        target_route="/app/configuracoes",
        reason="A rota é exclusiva da instalação Cloud e não se aplica ao sidecar self-hosted local; permanece preservada no pin.",
        upstream_evidence=(
            ".vendor/upstreams/chatwoot/app/javascript/dashboard/routes/dashboard/settings/billing/billing.routes.js",
        ),
    ),
    # Rocket.Chat: internal collaboration and agent conversations.
    _capability(
        "rocketchat",
        "channels-case-rooms",
        "Salas privadas básicas por caso",
        "colaboracao",
        "Localiza canais, cria grupos privados e projeta texto vinculado ao Matter.",
        "approved_native",
        "bridge",
        surface="Equipe",
        route="/app/equipe",
        target_route="/app/equipe",
        reason="A ponte cria grupo privado e envia texto; não administra membros, teams, permissões ou ciclo completo da sala.",
        upstream_evidence=(
            ".vendor/upstreams/rocketchat/apps/meteor/client/sidebar",
            ".vendor/upstreams/rocketchat/apps/meteor/client/views/room",
        ),
        klm_evidence=(
            "app/services/rocketchat_bridge_service.py",
            "frontend/src/klm/pages/InternalPage.tsx",
        ),
        test_evidence=("tests/services/test_rocketchat_bridge_service.py",),
    ),
    _capability(
        "rocketchat",
        "direct-messages",
        "Mensagens diretas",
        "colaboracao",
        "Permite conversas individuais entre membros autorizados.",
        "approved_full_app",
        "full_app",
        surface="Equipe",
        route="/app/equipe",
        target_route="/app/equipe",
        reason="Disponível no workspace completo; entrada e retorno contextuais ainda serão incorporados.",
        upstream_evidence=(".vendor/upstreams/rocketchat/apps/meteor/client/sidebar/RoomList",),
    ),
    _capability(
        "rocketchat",
        "threads-discussions",
        "Threads e discussões",
        "colaboracao",
        "Mantém consultas e decisões encadeadas sem poluir a conversa principal.",
        "approved_full_app",
        "full_app",
        surface="Equipe",
        route="/app/equipe",
        target_route="/app/equipe",
        reason="Threads existem no motor, mas a ponte não cria discussões, responde em thread nem preserva identidade round-trip do encadeamento.",
        upstream_evidence=(
            ".vendor/upstreams/rocketchat/apps/meteor/client/views/room",
            ".vendor/upstreams/rocketchat/apps/meteor/client/components/CreateDiscussion",
        ),
        klm_evidence=("frontend/src/klm/pages/InternalPage.tsx",),
    ),
    _capability(
        "rocketchat",
        "mentions-agent-bots",
        "Menções, comandos e bots de agentes",
        "agentes",
        "Aciona pessoas e agentes identificáveis dentro da conversa interna.",
        "approved_full_app",
        "full_app",
        surface="Equipe",
        route="/app/equipe",
        target_route="/app/equipe",
        reason="Menções e comandos de agente possuem ações nativas auditadas e contexto de sala; qualquer agent run jurídico continua sujeito ao runtime e às permissões KLM.",
        upstream_evidence=(
            ".vendor/upstreams/rocketchat/apps/meteor/client/startup/slashCommands",
            ".vendor/upstreams/rocketchat/apps/meteor/client/components/message",
        ),
    ),
    _capability(
        "rocketchat",
        "reactions-pins-stars",
        "Reações, pins e favoritos",
        "colaboracao",
        "Sinaliza concordância, destaca decisões e salva itens importantes.",
        "approved_full_app",
        "full_app",
        surface="Equipe",
        route="/app/equipe",
        target_route="/app/equipe",
        reason="Reações, pins e favoritos possuem comandos nativos; promoção ao Card 360 continua uma ação explícita e auditável, nunca automática.",
        upstream_evidence=(
            ".vendor/upstreams/rocketchat/apps/meteor/client/components/message/toolbar",
        ),
    ),
    _capability(
        "rocketchat",
        "files-media",
        "Arquivos e mídia",
        "documentos",
        "Compartilha arquivos internos com autorização e vínculo de caso.",
        "approved_full_app",
        "full_app",
        surface="Equipe",
        route="/app/equipe",
        target_route="/app/equipe",
        reason="O KLM prepara uploads e remove arquivos com autorização da sala; promoção ao RAG usa a trilha documental do caso e exige ação explícita.",
        upstream_evidence=(
            ".vendor/upstreams/rocketchat/apps/meteor/client/views/room/ImageGallery",
        ),
    ),
    _capability(
        "rocketchat",
        "search-history",
        "Busca e histórico",
        "produtividade",
        "Pesquisa mensagens, canais, pessoas e histórico autorizado.",
        "approved_full_app",
        "full_app",
        surface="Equipe",
        route="/app/equipe",
        target_route="/app/equipe",
        reason="Busca e exportação de histórico possuem comandos nativos com contexto da sala e filtros preservados na experiência KLM.",
        upstream_evidence=(".vendor/upstreams/rocketchat/apps/meteor/client/navbar/NavBarSearch",),
    ),
    _capability(
        "rocketchat",
        "presence-notifications",
        "Presença e notificações",
        "operacao",
        "Mostra disponibilidade do time e entrega alertas internos.",
        "approved_full_app",
        "full_app",
        surface="Equipe",
        route="/app/equipe",
        target_route="/app/equipe",
        reason="Disponível localmente no motor; push externo continua bloqueado.",
        upstream_evidence=(
            ".vendor/upstreams/rocketchat/apps/meteor/client/components/UserStatus",
            ".vendor/upstreams/rocketchat/apps/meteor/client/hooks/notification",
        ),
    ),
    _capability(
        "rocketchat",
        "roles-permissions-admin",
        "Papéis, permissões e administração",
        "seguranca",
        "Controla membros, papéis, canais e configurações comunitárias.",
        "approved_full_app",
        "full_app",
        surface="Configurações",
        route="/app/configuracoes",
        target_route="/app/configuracoes",
        reason="Papéis, membros e salas possuem provisionamento e revogação nativos; o RBAC KLM permanece como autoridade e o resultado é auditado.",
        upstream_evidence=(".vendor/upstreams/rocketchat/apps/meteor/client/views/admin",),
        klm_evidence=("frontend/src/components/admin/WorkspaceTeamPanel.tsx",),
    ),
    _capability(
        "rocketchat",
        "rest-webhooks-bridge",
        "REST, webhooks e bridge bidirecional",
        "plataforma",
        "Provisiona salas e reconcilia mensagens internas com o KLM.",
        "blocked_dependency",
        "bridge",
        surface="Infraestrutura",
        route="/app/ferramentas",
        target_route="/app/ferramentas",
        reason="A ponte básica possui testes contra o runtime local 6.13.1, mas a fonte auditada está fixada em 8.6.1; compatibilidade com o pin ainda não foi provada.",
        upstream_evidence=(
            ".vendor/upstreams/rocketchat/apps/meteor/server/modules/listeners",
            ".vendor/upstreams/rocketchat/apps/meteor/app/api",
        ),
        klm_evidence=(
            "app/services/rocketchat_client.py",
            "app/services/rocketchat_bridge_service.py",
        ),
        test_evidence=("tests/services/test_rocketchat_bridge_service.py",),
    ),
    _capability(
        "rocketchat",
        "teams-membership",
        "Times, hierarquia e membros",
        "colaboracao",
        "Administra times, canais associados, membros e papéis por sala.",
        "approved_full_app",
        "full_app",
        surface="Equipe",
        route="/app/equipe",
        target_route="/app/equipe",
        reason="Times e memberships possuem criação, inclusão e remoção nativas, reconciliadas no sidecar local com auditoria KLM.",
        upstream_evidence=(
            ".vendor/upstreams/rocketchat/apps/meteor/client/views/teams/contextualBar/channels/TeamsChannels.tsx",
        ),
    ),
    _capability(
        "rocketchat",
        "native-integrations-webhook-history",
        "Integrações nativas e histórico de webhooks",
        "integracoes",
        "Configura integrações de entrada/saída e consulta suas execuções.",
        "approved_full_app",
        "full_app",
        surface="Ferramentas",
        route="/app/ferramentas",
        target_route="/app/ferramentas",
        reason="O motor oferece a administração; o KLM possui somente seu webhook específico e não espelha o catálogo de integrações.",
        upstream_evidence=(
            ".vendor/upstreams/rocketchat/apps/meteor/app/api/server/v1/integrations.ts",
        ),
    ),
    _capability(
        "rocketchat",
        "e2ee",
        "Criptografia ponta a ponta",
        "seguranca",
        "Criptografa mensagens de salas compatíveis no cliente.",
        "blocked_dependency",
        "none",
        surface="Governança",
        target_route="/app/configuracoes",
        reason="E2EE impede a projeção e auditoria server-side em texto claro exigidas pela ponte atual; requer uma decisão arquitetural separada.",
        upstream_evidence=(
            ".vendor/upstreams/rocketchat/apps/meteor/client/hooks/roomActions/useE2EERoomAction.ts",
        ),
    ),
    _capability(
        "rocketchat",
        "apps-engine-custom-apps",
        "Apps Engine e apps customizados",
        "plataforma",
        "Instala apps e comandos customizados no workspace interno.",
        "approved_full_app",
        "full_app",
        surface="Ferramentas",
        route="/app/ferramentas",
        target_route="/app/ferramentas",
        reason="O host comunitário está preservado; qualquer app externo ou comando com efeito jurídico precisa de capability e revisão próprias.",
        upstream_evidence=(
            ".vendor/upstreams/rocketchat/apps/meteor/client/apps/RealAppsEngineUIHost.ts",
            ".vendor/upstreams/rocketchat/apps/meteor/app/apps/server/bridges/commands.ts",
        ),
    ),
    _capability(
        "rocketchat",
        "message-lifecycle-export-emoji",
        "Ciclo de mensagem, exportação e emojis",
        "produtividade",
        "Edita, remove, exporta mensagens e administra emojis customizados.",
        "approved_full_app",
        "full_app",
        surface="Equipe",
        route="/app/equipe",
        target_route="/app/equipe",
        reason="Edição, exclusão, exportação de mensagens e remoção de emoji possuem contratos nativos e releitura posterior no KLM.",
        upstream_evidence=(
            ".vendor/upstreams/rocketchat/apps/meteor/client/views/admin/customEmoji/CustomEmoji.tsx",
            ".vendor/upstreams/rocketchat/apps/meteor/client/hooks/roomActions/useExportMessagesRoomAction.ts",
        ),
    ),
    _capability(
        "rocketchat",
        "oauth-tokens",
        "OAuth e tokens de integração",
        "seguranca",
        "Administra provedores OAuth e credenciais de integração do motor.",
        "approved_full_app",
        "full_app",
        surface="Configurações",
        route="/app/configuracoes",
        target_route="/app/configuracoes",
        reason="Administração local preservada; segredos e provedores externos não são sincronizados para o KLM.",
        upstream_evidence=(".vendor/upstreams/rocketchat/apps/meteor/client/views/admin",),
    ),
    _capability(
        "rocketchat",
        "room-threads",
        "Threads da sala",
        "colaboracao",
        "Abre a lista de threads da sala vinculada ao caso.",
        "approved_full_app",
        "full_app",
        surface="Equipe",
        route="/app/equipe",
        target_route="/app/equipe",
        reason="Entrada contextual separada para não esconder threads dentro da família agregada de discussões.",
        upstream_evidence=(
            ".vendor/upstreams/rocketchat/apps/meteor/client/views/room/contextualBar/Threads",
        ),
        test_evidence=("tests/services/test_oss_launch_service.py",),
    ),
    _capability(
        "rocketchat",
        "room-discussions",
        "Discussões da sala",
        "colaboracao",
        "Abre discussões estruturadas da sala vinculada ao caso.",
        "approved_full_app",
        "full_app",
        surface="Equipe",
        route="/app/equipe",
        target_route="/app/equipe",
        reason="A discussão recebe um destino próprio no motor e preserva o vínculo do Card 360.",
        upstream_evidence=(
            ".vendor/upstreams/rocketchat/apps/meteor/client/views/room/contextualBar/Discussions",
        ),
        test_evidence=("tests/services/test_oss_launch_service.py",),
    ),
    _capability(
        "rocketchat",
        "room-mentions",
        "Menções da sala",
        "produtividade",
        "Filtra menções dentro da sala do caso.",
        "approved_full_app",
        "full_app",
        surface="Equipe",
        route="/app/equipe",
        target_route="/app/equipe",
        reason="Entrada contextual separada, sem confundir menções com administração de bots.",
        upstream_evidence=(
            ".vendor/upstreams/rocketchat/apps/meteor/client/hooks/roomActions/useMentionsRoomAction.ts",
        ),
        test_evidence=("tests/services/test_oss_launch_service.py",),
    ),
    _capability(
        "rocketchat",
        "room-pinned-messages",
        "Mensagens fixadas",
        "produtividade",
        "Abre as decisões e mensagens fixadas na sala do caso.",
        "approved_full_app",
        "full_app",
        surface="Equipe",
        route="/app/equipe",
        target_route="/app/equipe",
        reason="Pins deixam de ficar escondidos dentro da família agregada de reações e favoritos.",
        upstream_evidence=(
            ".vendor/upstreams/rocketchat/apps/meteor/client/hooks/roomActions/usePinnedMessagesRoomAction.ts",
        ),
        test_evidence=("tests/services/test_oss_launch_service.py",),
    ),
    _capability(
        "rocketchat",
        "room-starred-messages",
        "Mensagens favoritas",
        "produtividade",
        "Abre os itens marcados como favoritos na sala do caso.",
        "approved_full_app",
        "full_app",
        surface="Equipe",
        route="/app/equipe",
        target_route="/app/equipe",
        reason="Favoritos recebem uma entrada auditável e distinta de pins.",
        upstream_evidence=(
            ".vendor/upstreams/rocketchat/apps/meteor/client/hooks/roomActions/useStarredMessagesRoomAction.ts",
        ),
        test_evidence=("tests/services/test_oss_launch_service.py",),
    ),
    _capability(
        "rocketchat",
        "room-files",
        "Arquivos da sala",
        "documentos",
        "Abre a biblioteca de arquivos da sala vinculada.",
        "approved_full_app",
        "full_app",
        surface="Equipe",
        route="/app/equipe",
        target_route="/app/equipe",
        reason="A biblioteca é acessível no motor; promoção ao RAG continua sendo uma ação explícita do KLM.",
        upstream_evidence=(
            ".vendor/upstreams/rocketchat/apps/meteor/client/hooks/roomActions/useUploadedFilesListRoomAction.ts",
        ),
        test_evidence=("tests/services/test_oss_launch_service.py",),
    ),
    _capability(
        "rocketchat",
        "room-search",
        "Busca na sala",
        "produtividade",
        "Pesquisa o histórico da sala vinculada ao caso.",
        "approved_full_app",
        "full_app",
        surface="Equipe",
        route="/app/equipe",
        target_route="/app/equipe",
        reason="A busca abre no escopo da sala em vez do diretório genérico.",
        upstream_evidence=(
            ".vendor/upstreams/rocketchat/apps/meteor/client/hooks/roomActions/useRocketSearchRoomAction.ts",
        ),
        test_evidence=("tests/services/test_oss_launch_service.py",),
    ),
    _capability(
        "rocketchat",
        "admin-users",
        "Usuários do workspace interno",
        "administracao",
        "Administra usuários do Rocket.Chat local.",
        "approved_full_app",
        "full_app",
        surface="Configurações",
        route="/app/configuracoes",
        target_route="/app/configuracoes",
        reason="A identidade KLM continua canônica; esta tela serve para reconciliar o usuário local do motor.",
        upstream_evidence=(".vendor/upstreams/rocketchat/apps/meteor/client/views/admin/users",),
    ),
    _capability(
        "rocketchat",
        "admin-permissions",
        "Permissões do workspace interno",
        "seguranca",
        "Consulta e administra permissões comunitárias do Rocket.Chat.",
        "approved_full_app",
        "full_app",
        surface="Configurações",
        route="/app/configuracoes",
        target_route="/app/configuracoes",
        reason="RBAC KLM mantém a autoridade e a tela do motor permite verificar drift local.",
        upstream_evidence=(
            ".vendor/upstreams/rocketchat/apps/meteor/client/views/admin/permissions",
        ),
    ),
    _capability(
        "rocketchat",
        "admin-rooms",
        "Administração de salas",
        "administracao",
        "Lista e administra salas do workspace interno.",
        "approved_full_app",
        "full_app",
        surface="Equipe",
        route="/app/equipe",
        target_route="/app/equipe",
        reason="A tela permite auditar salas órfãs e vínculos fora do fluxo canônico KLM.",
        upstream_evidence=(".vendor/upstreams/rocketchat/apps/meteor/client/views/admin/rooms",),
    ),
    _capability(
        "rocketchat",
        "emoji-administration",
        "Emojis customizados",
        "personalizacao",
        "Administra emojis do workspace interno.",
        "approved_full_app",
        "full_app",
        surface="Configurações",
        route="/app/configuracoes",
        target_route="/app/configuracoes",
        reason="A rota real do motor é exposta separadamente das operações de ciclo de mensagem.",
        upstream_evidence=(
            ".vendor/upstreams/rocketchat/apps/meteor/client/views/admin/customEmoji/CustomEmoji.tsx",
        ),
    ),
    _capability(
        "rocketchat",
        "oauth-providers",
        "Provedores OAuth do motor",
        "seguranca",
        "Configura provedores OAuth disponíveis no runtime local.",
        "approved_full_app",
        "full_app",
        surface="Configurações",
        route="/app/configuracoes",
        target_route="/app/configuracoes",
        reason="A tela pode ser inspecionada localmente; provedores externos permanecem sem credenciais.",
        upstream_evidence=(".vendor/upstreams/rocketchat/apps/meteor/server/settings/oauth.ts",),
    ),
    _capability(
        "rocketchat",
        "third-party-login",
        "Aplicações de login de terceiros",
        "seguranca",
        "Administra aplicações OAuth registradas no Rocket.Chat.",
        "approved_full_app",
        "full_app",
        surface="Configurações",
        route="/app/configuracoes",
        target_route="/app/configuracoes",
        reason="Entrada distinta dos provedores OAuth e dos tokens pessoais.",
        upstream_evidence=(
            ".vendor/upstreams/rocketchat/apps/meteor/client/views/admin/oauthApps",
        ),
    ),
    _capability(
        "rocketchat",
        "personal-access-tokens",
        "Tokens pessoais do motor",
        "seguranca",
        "Administra tokens pessoais do usuário Rocket.Chat local.",
        "approved_full_app",
        "full_app",
        surface="Configurações",
        route="/app/configuracoes",
        target_route="/app/configuracoes",
        reason="Tokens do motor não substituem tokens KLM e não são copiados para o CRM jurídico.",
        upstream_evidence=(
            ".vendor/upstreams/rocketchat/apps/meteor/client/views/account/routes.tsx",
        ),
    ),
    _capability(
        "rocketchat",
        "webdav",
        "Armazenamento WebDAV",
        "integracoes",
        "Publica e recupera arquivos em servidor WebDAV.",
        "blocked_external_gate",
        "none",
        surface="Integrações",
        target_route="/app/configuracoes",
        reason="Depende de um serviço e credenciais externos, indisponíveis enquanto a meta estiver restrita ao ambiente local.",
        upstream_evidence=(".vendor/upstreams/rocketchat/apps/meteor/client/views/admin",),
    ),
    _capability(
        "rocketchat",
        "enterprise-controls",
        "DLP, auditoria e retenção enterprise",
        "governanca",
        "Controles avançados presentes apenas em áreas proprietárias do upstream.",
        "blocked_license",
        "none",
        surface="Governança",
        target_route="/app/configuracoes",
        reason="Diretórios enterprise não foram materializados e exigem licença específica.",
        upstream_evidence=("docs/oss-upstreams.lock.json",),
    ),
    _capability(
        "rocketchat",
        "external-push-conference",
        "Push, conferência e marketplace externos",
        "integracoes",
        "Usa serviços externos de notificação, vídeo ou marketplace.",
        "blocked_external_gate",
        "none",
        surface="Integrações",
        route="/app/ferramentas",
        target_route="/app/configuracoes",
        reason="Requer serviços externos, credenciais e gate separado.",
        upstream_evidence=(
            ".vendor/upstreams/rocketchat/apps/meteor/client/views/conference",
            ".vendor/upstreams/rocketchat/apps/meteor/client/views/marketplace",
        ),
    ),
    # ChatterMate: visual automation, agents, handoff and knowledge.
    _capability(
        "chattermate",
        "agents-rbac",
        "Agentes, usuários e RBAC",
        "agentes",
        "Configura agentes, pessoas, funções e acesso por organização.",
        "implemented",
        "bridge",
        surface="Agentes",
        route="/app/agentes",
        target_route="/app/agentes",
        reason="Os 20 agentes canônicos são criados ou reconciliados no motor nativo com vínculo estável; usuários e RBAC administrativo continuam no Meu Executivo.",
        upstream_evidence=(
            ".vendor/upstreams/chattermate/frontend/src/components/agent",
            ".vendor/upstreams/chattermate/frontend/src/components/user",
        ),
        klm_evidence=(
            "app/services/chattermate_operations_service.py",
            "frontend/src/klm/components/NativeAgentOperationsPanel.tsx",
        ),
        test_evidence=("tests/services/test_chattermate_operations_service.py",),
    ),
    _capability(
        "chattermate",
        "visual-editor",
        "Editor visual de workflows",
        "automacao",
        "Edita nodes e connections com canvas visual.",
        "implemented",
        "native",
        surface="Flows",
        route="/app/flows",
        target_route="/app/flows",
        reason="O editor visual é uma experiência KLM nativa, com catálogo autoritativo, serialização tipada e runtime local auditável.",
        upstream_evidence=(".vendor/upstreams/chattermate/frontend/src/components/workflow",),
        klm_evidence=("frontend/src/klm/pages/FlowsPage.tsx", "frontend/src/pages/FlowBuilder.tsx"),
        test_evidence=("frontend/src/test/flow-editor.test.ts",),
    ),
    _capability(
        "chattermate",
        "nodes-forms-conditions",
        "Nós mapeados de mensagem, espera e condição",
        "automacao",
        "Oferece mensagens, entrada do usuário, espera, condições e guardrails.",
        "implemented",
        "native",
        surface="Flows",
        route="/app/flows",
        target_route="/app/flows",
        reason="Mensagens, espera, condições, rotas e guardrails têm contratos tipados e execução/preview determinísticos no runtime KLM.",
        upstream_evidence=(
            ".vendor/upstreams/chattermate/frontend/src/components/workflow/nodes",
            ".vendor/upstreams/chattermate/backend/app/core/processor",
        ),
        klm_evidence=("frontend/src/lib/flow-editor.ts", "app/services/flow_service.py"),
    ),
    _capability(
        "chattermate",
        "nodes-form-action-end-transfer-landing-llm",
        "Form, ação, fim, transferência, landing e LLM",
        "automacao",
        "Inclui famílias de nós sem equivalente completo no contrato atual do KLM.",
        "implemented",
        "native",
        surface="Flows",
        route="/app/flows",
        target_route="/app/flows",
        reason="Os seis tipos têm schemas fail-closed, catálogo visual, preview local e executor governado; ação externa permanece bloqueada até credencial e aprovação.",
        upstream_evidence=(
            ".vendor/upstreams/chattermate/frontend/src/components/workflow/nodes/FormNode.vue",
            ".vendor/upstreams/chattermate/frontend/src/components/workflow/nodes/ActionNode.vue",
            ".vendor/upstreams/chattermate/frontend/src/components/workflow/nodes/EndNode.vue",
            ".vendor/upstreams/chattermate/frontend/src/components/workflow/nodes/HumanTransferNode.vue",
            ".vendor/upstreams/chattermate/frontend/src/components/workflow/nodes/LandingPageNodeConfig.vue",
            ".vendor/upstreams/chattermate/frontend/src/components/workflow/nodes/LLMNode.vue",
        ),
        klm_evidence=("app/schemas/flow.py", "app/services/flow_node_registry.py", "app/services/flow_runtime_primitives.py", "app/workers/flow_tasks.py"),
        test_evidence=("tests/services/test_flow_advanced_node_contracts.py", "frontend/src/test/flow-editor.test.ts"),
    ),
    _capability(
        "chattermate",
        "preview-execution",
        "Preview e execução em tempo real",
        "observabilidade",
        "Executa uma prévia e mostra mensagens, estados e resultados intermediários.",
        "implemented",
        "native",
        surface="Flows",
        route="/app/flows",
        target_route="/app/flows",
        reason="Preview, trace, dry-run e estados intermediários são executados no grafo KLM com eventos locais e zero efeitos externos.",
        upstream_evidence=(
            ".vendor/upstreams/chattermate/frontend/src/components/workflow",
            ".vendor/upstreams/chattermate/frontend/src/services",
        ),
        klm_evidence=("frontend/src/klm/pages/FlowsPage.tsx", "app/services/flow_service.py", "app/services/flow_runtime_primitives.py"),
        test_evidence=("frontend/src/test/flow-builder-graph.test.ts", "tests/api/test_flows.py"),
    ),
    _capability(
        "chattermate",
        "draft-publish-version",
        "Rascunho, publicação e versão",
        "governanca",
        "Mantém estado de rascunho/publicado e número de versão.",
        "approved_native",
        "native",
        surface="Flows",
        route="/app/flows",
        target_route="/app/flows",
        reason="O upstream não oferece rollback; o KLM registra revisões, mas também não expõe restauração. Nenhuma das duas partes é declarada como rollback.",
        upstream_evidence=(".vendor/upstreams/chattermate/backend/app/models/workflow.py",),
        klm_evidence=("app/models/flow.py", "app/services/flow_service.py"),
        test_evidence=("tests/services/test_oss_runtime_service.py",),
    ),
    _capability(
        "chattermate",
        "mcp-tools-agent-association",
        "Ferramentas MCP por agente",
        "agentes",
        "Cadastra ferramentas MCP e associa capacidades a agentes.",
        "implemented",
        "native",
        surface="Agentes",
        route="/app/agentes",
        target_route="/app/agentes",
        reason="Vínculos locais por agente agora usam catálogo fechado, configuração versionada, auditoria append-only, controle de permissão e aplicação no runtime antes da governança.",
        upstream_evidence=(".vendor/upstreams/chattermate/backend/app/api/mcp_tool.py",),
        klm_evidence=(
            "app/mcp",
            "app/services/klm_agent_service.py",
            "app/api/v1/endpoints/klm_operations.py",
            "frontend/src/klm/pages/AgentsPage.tsx",
        ),
        test_evidence=("tests/api/test_agent_tool_bindings.py",),
    ),
    _capability(
        "chattermate",
        "lead-capture-people",
        "Captura de leads e pessoas",
        "crm",
        "Captura dados de visitantes e mantém perfis de pessoas atendidas.",
        "approved_native",
        "full_app",
        surface="Leads",
        route="/app/leads",
        target_route="/app/leads",
        reason="Captura, marcação de cliente e vínculo de pessoa possuem comandos tipados; o CRM jurídico permanece canônico e recebe somente vínculos explícitos.",
        upstream_evidence=(
            ".vendor/upstreams/chattermate/backend/app/api/lead_capture.py",
            ".vendor/upstreams/chattermate/backend/app/api/people.py",
        ),
        klm_evidence=("frontend/src/klm/pages/LeadPage.tsx",),
    ),
    _capability(
        "chattermate",
        "notifications-groups",
        "Notificações e grupos",
        "operacao",
        "Organiza grupos de usuários e entrega notificações da operação.",
        "approved_full_app",
        "full_app",
        surface="Agentes",
        route="/app/agentes",
        target_route="/app/agentes",
        reason="Grupos, membros, leitura de notificação e regras possuem CRUD nativo; notificações externas permanecem bloqueadas pelo gate.",
        upstream_evidence=(
            ".vendor/upstreams/chattermate/backend/app/api/notification.py",
            ".vendor/upstreams/chattermate/backend/app/api/user_groups.py",
        ),
    ),
    _capability(
        "chattermate",
        "ratings-feedback",
        "Avaliações e feedback",
        "qualidade",
        "Solicita nota, registra feedback e mede qualidade de bot e humano.",
        "approved_native",
        "full_app",
        surface="Qualidade IA",
        route="/app/qualidade",
        target_route="/app/qualidade",
        reason="Avaliações podem ser criadas e vinculadas nativamente à conversa, ao agente e ao caso, preservando a origem analítica do sidecar.",
        upstream_evidence=(
            ".vendor/upstreams/chattermate/backend/app/models/rating.py",
            ".vendor/upstreams/chattermate/frontend/src/components/analytics/AnalyticsDashboard.vue",
        ),
        klm_evidence=("frontend/src/klm/pages/QualityPage.tsx",),
    ),
    _capability(
        "chattermate",
        "widget-apps",
        "Apps dentro do widget",
        "canais",
        "Configura aplicativos e experiências auxiliares no widget web.",
        "approved_full_app",
        "full_app",
        surface="Ferramentas",
        route="/app/ferramentas",
        target_route="/app/ferramentas",
        reason="Administração preservada no sidecar; uso público será avaliado apenas depois dos fluxos locais e gates de publicação.",
        upstream_evidence=(".vendor/upstreams/chattermate/backend/app/api/widget_apps.py",),
    ),
    _capability(
        "chattermate",
        "file-upload",
        "Upload de arquivos",
        "documentos",
        "Recebe arquivos usados por agentes, knowledge e conversas.",
        "approved_native",
        "full_app",
        surface="Documentos / RAG",
        route="/app/leads",
        target_route="/app/leads",
        reason="Arquivos são registrados com metadados e texto extraído, vinculados ao caso e promovidos ao RAG sob autorização e retenção KLM.",
        upstream_evidence=(".vendor/upstreams/chattermate/backend/app/api/file_upload.py",),
        klm_evidence=("app/services/knowledge_service.py",),
    ),
    _capability(
        "chattermate",
        "jira-shopify",
        "Integrações Jira e Shopify",
        "integracoes",
        "Conecta tickets Jira e dados Shopify ao produto upstream.",
        "blocked_external_gate",
        "none",
        surface="Integrações",
        target_route="/app/configuracoes",
        reason="São integrações externas não necessárias ao fluxo jurídico inicial e exigem credenciais e aprovação separadas.",
        upstream_evidence=(
            ".vendor/upstreams/chattermate/backend/app/api/jira.py",
            ".vendor/upstreams/chattermate/backend/app/api/shopify.py",
        ),
    ),
    _capability(
        "chattermate",
        "handoff-inbox-transfer",
        "Handoff, inbox e transferência",
        "atendimento",
        "Transfere a execução para humano ou outro agente e preserva o contexto.",
        "approved_native",
        "full_app",
        surface="Conversas",
        route="/app/conversas",
        target_route="/app/conversas",
        reason="Handoff deve controlar Chatwoot, Rocket.Chat e os gates humanos do KLM.",
        upstream_evidence=(
            ".vendor/upstreams/chattermate/frontend/src/components/human-agent",
            ".vendor/upstreams/chattermate/frontend/src/components/conversations",
        ),
        klm_evidence=(
            "frontend/src/klm/pages/ConversationsPage.tsx",
            "app/services/agent_runtime_service.py",
        ),
    ),
    _capability(
        "chattermate",
        "structured-output",
        "Saída estruturada e transferência entre agentes",
        "agentes",
        "Produz contratos validados e encaminha tarefas para agentes especializados.",
        "implemented",
        "native",
        surface="Agentes",
        route="/app/agentes",
        target_route="/app/agentes",
        reason="JSON Schema é validado localmente e os handoffs especializados são persistidos, auditados e exibidos na operação de agentes.",
        upstream_evidence=(".vendor/upstreams/chattermate/backend/app/models/schemas",),
        klm_evidence=(
            "app/ai/structured_output.py",
            "app/services/agent_runtime_service.py",
            "app/services/klm_agent_service.py",
            "frontend/src/klm/pages/AgentsPage.tsx",
        ),
        test_evidence=(
            "tests/ai/test_gateway.py",
            "tests/api/test_klm_agents.py",
        ),
    ),
    _capability(
        "chattermate",
        "knowledge-faq",
        "Knowledge, FAQ e pesquisa",
        "rag",
        "Ingere conteúdo e pesquisa conhecimento para respostas e workflows.",
        "approved_embedded",
        "full_app",
        surface="Documentos / RAG",
        route="/app/leads",
        target_route="/app/leads",
        reason="RAG por caso continua canônico no KLM; UX e testes de knowledge serão reaproveitados.",
        upstream_evidence=(
            ".vendor/upstreams/chattermate/frontend/src/components/knowledge",
            ".vendor/upstreams/chattermate/frontend/src/components/faq",
        ),
        klm_evidence=("app/services/knowledge_service.py",),
    ),
    _capability(
        "chattermate",
        "widgets-help-center",
        "Widgets e help center",
        "canais",
        "Oferece widget web e superfícies públicas de autoatendimento.",
        "approved_full_app",
        "full_app",
        surface="Ferramentas",
        route="/app/ferramentas",
        target_route="/app/ferramentas",
        reason="Preservado para canal web futuro; não substitui WhatsApp nem a SPA interna.",
        upstream_evidence=(
            ".vendor/upstreams/chattermate/frontend/src/components/widget-app",
            ".vendor/upstreams/chattermate/frontend/src/webclient",
        ),
    ),
    _capability(
        "chattermate",
        "analytics",
        "Analytics de agentes e workflows",
        "analytics",
        "Mede conversas, execuções, handoffs e desempenho.",
        "approved_native",
        "full_app",
        surface="Qualidade IA",
        route="/app/qualidade",
        target_route="/app/qualidade",
        reason="Métricas serão projetadas nos KPIs e qualidade KLM com drill-down no motor.",
        upstream_evidence=(".vendor/upstreams/chattermate/frontend/src/components/analytics",),
        klm_evidence=("frontend/src/klm/pages/QualityPage.tsx",),
    ),
    _capability(
        "chattermate",
        "api-websocket",
        "API, worker e websocket",
        "plataforma",
        "Executa serviços e atualizações em tempo real do produto local.",
        "implemented",
        "bridge",
        surface="Infraestrutura",
        route="/app/ferramentas",
        target_route="/app/ferramentas",
        reason="API e websocket sustentam a projeção operacional já lida pelo KLM; não existe uma tela upstream honesta para abrir esta infraestrutura.",
        upstream_evidence=(
            ".vendor/upstreams/chattermate/backend/app",
            ".vendor/upstreams/chattermate/frontend/src/services",
        ),
        klm_evidence=(
            "app/services/chattermate_operations_service.py",
            "app/services/oss_runtime_service.py",
        ),
        test_evidence=(
            "tests/services/test_chattermate_operations_service.py",
            "tests/services/test_oss_runtime_service.py",
        ),
    ),
    _capability(
        "chattermate",
        "external-ai-channels",
        "Modelos, embeddings, crawling, voz e canais externos",
        "integracoes",
        "Usa provedores externos para geração, embeddings, web, voz ou entrega.",
        "blocked_external_gate",
        "none",
        surface="Integrações",
        route="/app/ferramentas",
        target_route="/app/configuracoes",
        reason="Depende de APIs e credenciais que somente serão fornecidas após aprovação local.",
        upstream_evidence=(
            ".vendor/upstreams/chattermate/backend/app/agents",
            ".vendor/upstreams/chattermate/frontend/src/components/integrations",
        ),
        klm_evidence=("app/services/agent_runtime_service.py",),
    ),
    _capability(
        "chattermate",
        "private-enterprise-modules",
        "Módulos enterprise privados",
        "licenca",
        "Gitlinks privados não presentes no checkout comunitário.",
        "blocked_license",
        "none",
        surface="Governança",
        target_route="/app/configuracoes",
        reason="Submódulos privados não foram inicializados e exigem licença/acesso próprios.",
        upstream_evidence=("docs/oss-upstreams.lock.json",),
    ),
    _capability(
        "chattermate",
        "human-agent-roster",
        "Equipe humana do handoff",
        "operacao",
        "Administra operadores humanos disponíveis para receber transferências.",
        "approved_full_app",
        "full_app",
        surface="Equipe",
        route="/app/equipe",
        target_route="/app/equipe",
        reason="A escala humana permanece no runtime nativo; o Orquestrador coloca a conversa na fila do atendente e o Hermes apenas informa o dono.",
        upstream_evidence=(
            ".vendor/upstreams/chattermate/frontend/src/views/HumanAgentView.vue",
            ".vendor/upstreams/chattermate/frontend/src/router/index.ts",
        ),
    ),
    _capability(
        "chattermate",
        "faq-management",
        "FAQ e central de ajuda",
        "conteudo",
        "Administra perguntas frequentes separadamente da base documental.",
        "approved_full_app",
        "full_app",
        surface="Documentos / RAG",
        route="/app/leads",
        target_route="/app/leads",
        reason="FAQ possui criação, edição, exclusão e operações em lote nativas; conteúdo jurídico específico do caso permanece no RAG canônico.",
        upstream_evidence=(
            ".vendor/upstreams/chattermate/frontend/src/views/FaqView.vue",
            ".vendor/upstreams/chattermate/frontend/src/router/index.ts",
        ),
    ),
    _capability(
        "chattermate",
        "user-settings",
        "Preferências do operador",
        "administracao",
        "Configura preferências e perfil do usuário no motor local.",
        "approved_full_app",
        "full_app",
        surface="Configurações",
        route="/app/configuracoes",
        target_route="/app/configuracoes",
        reason=(
            "O KLM atualiza os campos permitidos do perfil local e persiste preferências "
            "versionadas no workspace; identidade e permissões canônicas continuam no KLM."
        ),
        upstream_evidence=(
            ".vendor/upstreams/chattermate/frontend/src/views/UserSettingsView.vue",
            ".vendor/upstreams/chattermate/frontend/src/router/index.ts",
        ),
    ),
    _capability(
        "chattermate",
        "organization-settings",
        "Configurações da organização",
        "administracao",
        "Administra dados, regras e preferências da organização ChatterMate local.",
        "approved_full_app",
        "full_app",
        surface="Configurações",
        route="/app/configuracoes",
        target_route="/app/configuracoes",
        reason="Configurações e preferências da organização possuem edição nativa, mantendo o vínculo imutável com um único workspace KLM.",
        upstream_evidence=(
            ".vendor/upstreams/chattermate/frontend/src/views/settings/OrganizationSettings.vue",
            ".vendor/upstreams/chattermate/frontend/src/router/index.ts",
        ),
    ),
    _capability(
        "chattermate",
        "integration-settings",
        "Catálogo de integrações dos agentes",
        "integracoes",
        "Exibe o catálogo e o estado das integrações configuráveis no ChatterMate.",
        "approved_full_app",
        "full_app",
        surface="Ferramentas",
        route="/app/ferramentas",
        target_route="/app/ferramentas",
        reason="A tela é acessível localmente; qualquer conexão ou efeito externo continua bloqueado pelos gates KLM.",
        upstream_evidence=(
            ".vendor/upstreams/chattermate/frontend/src/views/settings/IntegrationsSettings.vue",
            ".vendor/upstreams/chattermate/frontend/src/router/index.ts",
        ),
    ),
    _capability(
        "chattermate",
        "ai-provider-settings",
        "Configuração de provedores de IA",
        "integracoes",
        "Configura modelos, chaves e provedores usados por agentes e workflows.",
        "blocked_external_gate",
        "none",
        surface="Configurações",
        target_route="/app/configuracoes",
        reason="A tela está preservada, mas credenciais e consumo de APIs somente serão habilitados após aprovação expressa.",
        upstream_evidence=(
            ".vendor/upstreams/chattermate/frontend/src/views/settings/AIConfigSettings.vue",
            ".vendor/upstreams/chattermate/frontend/src/router/index.ts",
        ),
    ),
    # BottleCRM: CRM patterns, operational modules, MCP, RLS and audit.
    _capability(
        "bottlecrm",
        "accounts-contacts-leads",
        "Contas, contatos e leads",
        "crm",
        "Gerencia organizações, pessoas, leads e informações comerciais.",
        "implemented",
        "bridge",
        surface="Leads",
        route="/app/leads",
        target_route="/app/leads",
        reason="Organização, contato e lead possuem sync idempotente, leitura contextual nativa e vínculo persistente; o KLM permanece canônico.",
        upstream_evidence=(
            ".vendor/upstreams/bottlecrm/backend/accounts",
            ".vendor/upstreams/bottlecrm/backend/contacts",
            ".vendor/upstreams/bottlecrm/backend/leads",
        ),
        klm_evidence=(
            "app/services/bottlecrm_sync_service.py",
            "app/services/bottlecrm_operations_service.py",
            "frontend/src/klm/pages/LeadPage.tsx",
            "frontend/src/klm/components/NativeCRMOperationsPanel.tsx",
        ),
        test_evidence=(
            "tests/services/test_bottlecrm_sync_contract.py",
            "tests/services/test_bottlecrm_operations_service.py",
            "frontend/e2e/capability-matrix.spec.ts",
        ),
    ),
    _capability(
        "bottlecrm",
        "opportunities-kanban",
        "Oportunidades e Kanban",
        "crm",
        "Opera pipeline, probabilidade, responsável, etapa e movimentação visual.",
        "implemented",
        "bridge",
        surface="Funil",
        route="/app/funil",
        target_route="/app/funil",
        reason="O funil jurídico KLM move, pontua e audita os cards; oportunidades são reconciliadas e o pipeline BottleCRM é lido nativamente.",
        upstream_evidence=(
            ".vendor/upstreams/bottlecrm/backend/opportunity",
            ".vendor/upstreams/bottlecrm/frontend/src/routes/(app)",
        ),
        klm_evidence=(
            "app/services/bottlecrm_sync_service.py",
            "app/services/bottlecrm_operations_service.py",
            "frontend/src/klm/pages/FunnelPage.tsx",
            "frontend/src/klm/components/NativeCRMOperationsPanel.tsx",
        ),
        test_evidence=(
            "tests/services/test_bottlecrm_sync_contract.py",
            "tests/services/test_bottlecrm_operations_service.py",
            "frontend/src/test/funnel-dnd.test.ts",
            "frontend/e2e/capability-matrix.spec.ts",
        ),
    ),
    _capability(
        "bottlecrm",
        "cases-tickets",
        "Casos e tickets básicos",
        "operacao",
        "Projeta solicitações básicas, status e responsável de um caso operacional.",
        "implemented",
        "bridge",
        surface="Caso 360",
        route="/app/leads",
        target_route="/app/leads",
        reason="O caso básico possui projeção idempotente e leitura contextual no Caso 360; Matter é canônico e recursos avançados permanecem em famílias separadas.",
        upstream_evidence=(".vendor/upstreams/bottlecrm/backend/cases/urls.py",),
        klm_evidence=(
            "app/services/bottlecrm_sync_service.py",
            "app/services/bottlecrm_operations_service.py",
            "frontend/src/klm/components/NativeCRMOperationsPanel.tsx",
        ),
        test_evidence=(
            "tests/services/test_bottlecrm_sync_contract.py",
            "tests/services/test_bottlecrm_operations_service.py",
            "frontend/e2e/capability-matrix.spec.ts",
        ),
    ),
    _capability(
        "bottlecrm",
        "case-solutions-kb",
        "Soluções, base de conhecimento e sugestões",
        "conteudo",
        "Publica soluções, vincula artigos ao caso e sugere conteúdo relevante.",
        "approved_native",
        "full_app",
        surface="Caso 360",
        route="/app/leads",
        target_route="/app/leads",
        reason="A família existe no motor; deve ser adaptada ao RAG jurídico sem duplicar documentos ou tornar sugestão uma decisão automática.",
        upstream_evidence=(".vendor/upstreams/bottlecrm/backend/cases/urls.py",),
        klm_evidence=("app/services/knowledge_service.py",),
    ),
    _capability(
        "bottlecrm",
        "case-mailboxes-watchers",
        "Mailboxes, seguidores e observação",
        "operacao",
        "Recebe e-mail em mailbox e permite acompanhar casos por usuário.",
        "approved_native",
        "full_app",
        surface="Omnichannel",
        route="/app/omnichannel",
        target_route="/app/omnichannel",
        reason="Mailboxes e watchers possuem comandos nativos; a ingestão de e-mail continua vinculada ao Card 360 pela trilha canônica de recibos KLM.",
        upstream_evidence=(".vendor/upstreams/bottlecrm/backend/cases/urls.py",),
        klm_evidence=("frontend/src/klm/pages/OmnichannelPage.tsx",),
    ),
    _capability(
        "bottlecrm",
        "case-merge-hierarchy",
        "Mesclagem e árvore de casos",
        "dados",
        "Mescla/desfaz mesclagem e relaciona casos pai e filho.",
        "approved_native",
        "full_app",
        surface="Caso 360",
        route="/app/leads",
        target_route="/app/leads",
        reason="A operação pode alterar identidade e histórico; requer contrato transacional e auditoria KLM antes de ser exposta nativamente.",
        upstream_evidence=(".vendor/upstreams/bottlecrm/backend/cases/urls.py",),
    ),
    _capability(
        "bottlecrm",
        "case-time-billing",
        "Tempo aplicado e faturamento",
        "financeiro",
        "Registra apontamentos, cronômetro, resumo de tempo e origem para cobrança.",
        "approved_native",
        "full_app",
        surface="Financeiro",
        route="/app/financeiro",
        target_route="/app/financeiro",
        reason="Timers e lançamentos de tempo possuem comandos nativos e resumo faturável; gerar cobrança continua uma decisão explícita no Financeiro KLM.",
        upstream_evidence=(".vendor/upstreams/bottlecrm/backend/cases/urls.py",),
        klm_evidence=("app/services/finance_service.py",),
    ),
    _capability(
        "bottlecrm",
        "case-analytics-bulk-import",
        "Analytics, exportação e operações em lote",
        "analytics",
        "Mede FRT, MTTR, backlog, agentes e SLA; importa, exporta e altera casos em lote.",
        "approved_native",
        "full_app",
        surface="KPIs",
        route="/app/kpis",
        target_route="/app/kpis",
        reason="FRT, MTTR, backlog, SLA e agentes possuem leitura nativa; bulk import valida e persiste somente staging local até confirmação específica.",
        upstream_evidence=(".vendor/upstreams/bottlecrm/backend/cases/urls.py",),
        klm_evidence=("frontend/src/klm/pages/KpisPage.tsx",),
    ),
    _capability(
        "bottlecrm",
        "case-pipelines-reopen",
        "Pipelines, etapas e política de reabertura",
        "workflow",
        "Configura Kanban de casos, etapas ordenadas e regra de reabertura.",
        "approved_full_app",
        "full_app",
        surface="CRM",
        route="/app/funil",
        target_route="/app/funil",
        reason="Pipelines, ordenação de etapas e política de reabertura possuem comandos nativos; o estágio jurídico canônico só muda por ação KLM explícita.",
        upstream_evidence=(".vendor/upstreams/bottlecrm/backend/cases/urls.py",),
    ),
    _capability(
        "bottlecrm",
        "tasks-calendar",
        "Tarefas e calendário",
        "produtividade",
        "Agenda tarefas, responsáveis, prazos e atividades.",
        "implemented",
        "bridge",
        surface="Agenda",
        route="/app/agenda",
        target_route="/app/agenda",
        reason="Agenda e tarefas KLM são canônicas, sincronizadas sem duplicidade e projetadas na Agenda com colunas e prazos do motor.",
        upstream_evidence=(".vendor/upstreams/bottlecrm/backend/tasks",),
        klm_evidence=(
            "app/services/bottlecrm_sync_service.py",
            "app/services/bottlecrm_operations_service.py",
            "frontend/src/klm/pages/AgendaPage.tsx",
            "frontend/src/klm/components/NativeCRMOperationsPanel.tsx",
        ),
        test_evidence=(
            "tests/services/test_bottlecrm_sync_contract.py",
            "tests/services/test_bottlecrm_operations_service.py",
            "frontend/e2e/capability-matrix.spec.ts",
        ),
    ),
    _capability(
        "bottlecrm",
        "task-boards-pipelines",
        "Boards e pipelines de tarefas",
        "produtividade",
        "Organiza tarefas em boards, colunas, Kanban e etapas configuráveis.",
        "approved_native",
        "bridge",
        surface="Agenda",
        route="/app/agenda",
        target_route="/app/agenda",
        reason=(
            "O KLM cria e atualiza boards, move tarefas e reordena colunas em estado nativo "
            "versionado. O movimento remoto exige opt-in, confirmação e digest esperado."
        ),
        upstream_evidence=(".vendor/upstreams/bottlecrm/backend/tasks/urls.py",),
        klm_evidence=(
            "app/services/bottlecrm_operations_service.py",
            "frontend/src/klm/pages/AgendaPage.tsx",
            "frontend/src/klm/components/NativeCRMOperationsPanel.tsx",
        ),
        test_evidence=(
            "tests/services/test_bottlecrm_operations_service.py",
            "frontend/e2e/capability-matrix.spec.ts",
        ),
    ),
    _capability(
        "bottlecrm",
        "products-estimates-quotes",
        "Produtos, estimativas e propostas",
        "comercial",
        "Modela catálogo, itens, estimativa/orçamento e conversão comercial.",
        "approved_native",
        "bridge",
        surface="Propostas",
        route="/app/contratos",
        target_route="/app/contratos",
        reason=(
            "O KLM cria e atualiza produtos e estimativas em estado nativo versionado; a "
            "conversão remota em proposta exige opt-in, confirmação e digest esperado."
        ),
        upstream_evidence=(".vendor/upstreams/bottlecrm/backend/invoices/api_urls.py",),
        klm_evidence=(
            "app/services/bottlecrm_operations_service.py",
            "frontend/src/klm/pages/ProposalPreviewPage.tsx",
            "frontend/src/klm/components/NativeCRMOperationsPanel.tsx",
        ),
        test_evidence=(
            "tests/services/test_bottlecrm_operations_service.py",
            "frontend/e2e/capability-matrix.spec.ts",
        ),
    ),
    _capability(
        "bottlecrm",
        "invoices-recurrence",
        "Faturas e recorrência",
        "financeiro",
        "Registra faturas, vencimentos, recorrência e estado de cobrança.",
        "approved_native",
        "bridge",
        surface="Financeiro",
        route="/app/financeiro",
        target_route="/app/financeiro",
        reason="Faturas e recorrências possuem leitura, criação, agendamento e cancelamento nativos; pagamentos reais continuam bloqueados até integração aprovada.",
        upstream_evidence=(".vendor/upstreams/bottlecrm/backend/invoices",),
        klm_evidence=(
            "frontend/src/klm/pages/OperationalModulePage.tsx",
            "frontend/src/klm/components/NativeCRMOperationsPanel.tsx",
            "app/services/bottlecrm_operations_service.py",
            "app/services/finance_service.py",
        ),
        test_evidence=(
            "tests/services/test_bottlecrm_operations_service.py",
            "frontend/e2e/capability-matrix.spec.ts",
        ),
    ),
    _capability(
        "bottlecrm",
        "orders",
        "Pedidos e itens comerciais",
        "comercial",
        "Registra pedidos, itens e estado comercial separado de estimativas e faturas.",
        "blocked_dependency",
        "none",
        surface="Propostas",
        target_route="/app/contratos",
        reason="O pin preserva models e admin Django de pedidos, mas não possui API nem rota de frontend comunitária; não há destino operacional seguro para expor.",
        upstream_evidence=(".vendor/upstreams/bottlecrm/backend/orders",),
    ),
    _capability(
        "bottlecrm",
        "templates-macros",
        "Templates e macros",
        "produtividade",
        "Padroniza ações, mensagens e registros repetitivos.",
        "approved_full_app",
        "full_app",
        surface="Ferramentas",
        route="/app/ferramentas",
        target_route="/app/ferramentas",
        reason="Templates e macros possuem criação, renderização e arquivamento nativos, com variáveis validadas antes da persistência.",
        upstream_evidence=(
            ".vendor/upstreams/bottlecrm/backend/macros",
            ".vendor/upstreams/bottlecrm/backend/tasks/templates",
        ),
    ),
    _capability(
        "bottlecrm",
        "opportunity-aging-goals",
        "Aging de oportunidades e metas",
        "analytics",
        "Configura aging por etapa, metas comerciais e leaderboard.",
        "approved_native",
        "bridge",
        surface="KPIs",
        route="/app/kpis",
        target_route="/app/kpis",
        reason=(
            "O KLM configura aging por etapa, cria ou encerra metas em estado nativo "
            "versionado e apresenta o leaderboard remoto como leitura, sem inferir escrita."
        ),
        upstream_evidence=(".vendor/upstreams/bottlecrm/backend/opportunity/urls.py",),
        klm_evidence=(
            "app/services/bottlecrm_operations_service.py",
            "frontend/src/klm/pages/FunnelPage.tsx",
            "frontend/src/klm/components/NativeCRMOperationsPanel.tsx",
        ),
        test_evidence=(
            "tests/services/test_bottlecrm_operations_service.py",
            "frontend/e2e/capability-matrix.spec.ts",
        ),
    ),
    _capability(
        "bottlecrm",
        "invoice-estimate-portals",
        "Portais tokenizados de fatura e estimativa",
        "experiencia",
        "Exibe fatura ou estimativa por token para destinatários externos.",
        "approved_full_app",
        "full_app",
        surface="Ferramentas",
        route="/app/ferramentas",
        target_route="/app/ferramentas",
        reason="Os portais existem apenas para invoice/estimate; publicação externa continua bloqueada e não equivale a um portal geral do cliente.",
        upstream_evidence=(
            ".vendor/upstreams/bottlecrm/frontend/src/routes/(no-layout)/portal/invoice/[token]/+page.svelte",
        ),
    ),
    _capability(
        "bottlecrm",
        "approvals-routing-escalation",
        "Aprovações, roteamento e escalonamento",
        "workflow",
        "Encaminha registros, exige decisão e escala itens fora do SLA.",
        "approved_native",
        "full_app",
        surface="Aprovações",
        route="/app/aprovacoes",
        target_route="/app/aprovacoes",
        reason="Aprovações respeitam a coordenação do Orquestrador e a ponte executiva do Hermes sem duplicar a auditoria jurídica.",
        upstream_evidence=(".vendor/upstreams/bottlecrm/backend/cases/urls.py",),
        klm_evidence=(
            "app/services/approval_service.py",
            "frontend/src/klm/pages/OperationalModulePage.tsx",
        ),
    ),
    _capability(
        "bottlecrm",
        "business-hours-sla-csat",
        "Horários, SLA e CSAT",
        "operacao",
        "Controla expediente, pausa de SLA, escalonamento e satisfação.",
        "approved_native",
        "bridge",
        surface="KPIs",
        route="/app/kpis",
        target_route="/app/kpis",
        reason=(
            "O KLM configura calendários e SLA, registra pausas e CSAT em estado nativo "
            "versionado e usa os agregados remotos somente como leitura auditável."
        ),
        upstream_evidence=(
            ".vendor/upstreams/bottlecrm/backend/business_hours",
            ".vendor/upstreams/bottlecrm/backend/cases",
        ),
        klm_evidence=(
            "app/services/bottlecrm_operations_service.py",
            "frontend/src/klm/components/NativeCRMOperationsPanel.tsx",
        ),
        test_evidence=("tests/services/test_bottlecrm_operations_service.py",),
    ),
    _capability(
        "bottlecrm",
        "custom-fields-duplicates",
        "Campos customizados e duplicidade",
        "dados",
        "Estende schemas e detecta registros potencialmente duplicados.",
        "approved_native",
        "full_app",
        surface="CRM",
        route="/app/leads",
        target_route="/app/leads",
        reason="Campos customizados, resolução e merge de duplicidades possuem comandos nativos, preservando os campos jurídicos tipados do KLM.",
        upstream_evidence=(
            ".vendor/upstreams/bottlecrm/backend/common",
            ".vendor/upstreams/bottlecrm/backend/contacts/services",
        ),
    ),
    _capability(
        "bottlecrm",
        "tags-documents",
        "Tags e documentos compartilhados",
        "dados",
        "Administra tags reutilizáveis e documentos associados aos registros do CRM.",
        "approved_native",
        "full_app",
        surface="CRM",
        route="/app/leads",
        target_route="/app/leads",
        reason="Tags e vínculos documentais possuem comandos nativos; anexar, remover e restaurar preservam autorização e promoção explícita ao Card 360/RAG.",
        upstream_evidence=(
            ".vendor/upstreams/bottlecrm/backend/common/views/tags_views.py",
            ".vendor/upstreams/bottlecrm/backend/common/views/document_views.py",
        ),
        klm_evidence=("app/services/knowledge_service.py",),
    ),
    _capability(
        "bottlecrm",
        "notifications-sse",
        "Notificações em tempo real por SSE",
        "operacao",
        "Entrega notificações, leitura e atualização em tempo real.",
        "approved_full_app",
        "bridge",
        surface="Atividade",
        route="/app/atividade",
        target_route="/app/atividade",
        reason="Notificações possuem leitura, marcação individual ou em lote e registro de assinatura nativos; a origem SSE permanece identificada como sidecar.",
        upstream_evidence=(
            ".vendor/upstreams/bottlecrm/backend/common/views/notification_views.py",
        ),
        klm_evidence=(
            "app/services/bottlecrm_operations_service.py",
            "frontend/src/klm/pages/AgendaPage.tsx",
            "frontend/src/klm/components/NativeCRMOperationsPanel.tsx",
        ),
        test_evidence=(
            "tests/services/test_bottlecrm_operations_service.py",
            "frontend/e2e/capability-matrix.spec.ts",
        ),
    ),
    _capability(
        "bottlecrm",
        "import-export",
        "Importação e exportação",
        "dados",
        "Importa e exporta cadastros com validação e rastreabilidade.",
        "approved_native",
        "full_app",
        surface="CRM",
        route="/app/ferramentas",
        target_route="/app/leads",
        reason="Importação precisa validar workspace, duplicidade e taxonomia antes de gravar no KLM.",
        upstream_evidence=(".vendor/upstreams/bottlecrm/backend/common",),
    ),
    _capability(
        "bottlecrm",
        "users-rls-rbac",
        "Usuários, RLS e RBAC",
        "seguranca",
        "Isola organizações e aplica permissões em API e banco.",
        "implemented",
        "native",
        surface="Configurações",
        route="/app/configuracoes",
        target_route="/app/configuracoes",
        reason="Padrões RLS/RBAC foram adaptados ao KLM e possuem provas PostgreSQL reais.",
        upstream_evidence=(
            ".vendor/upstreams/bottlecrm/backend/common/rls",
            ".vendor/upstreams/bottlecrm/backend/accounts",
        ),
        klm_evidence=(
            "app/db/tenant.py",
            "alembic/versions/b1c2d3e4f5a6_enable_klm_oss_workspace_rls.py",
        ),
        test_evidence=("tests/integration/test_postgresql_rls.py",),
    ),
    _capability(
        "bottlecrm",
        "audit-suspicious-activity",
        "Auditoria e atividade suspeita",
        "governanca",
        "Registra acessos, mudanças, tokens e sinais de atividade suspeita.",
        "implemented",
        "native",
        surface="Atividade",
        route="/app/atividade",
        target_route="/app/atividade",
        reason="Auditoria KLM e contratos de bridge já registram ações e isolamento.",
        upstream_evidence=(".vendor/upstreams/bottlecrm/backend/common",),
        klm_evidence=("app/models/klm_operations.py", "app/services/klm_operations_service.py"),
        test_evidence=("tests/integration/test_postgresql_rls.py",),
    ),
    _capability(
        "bottlecrm",
        "api-tokens-mcp",
        "API tokens e MCP",
        "plataforma",
        "Expõe operações escopadas para usuários, integrações e agentes.",
        "implemented",
        "native",
        surface="Integrações",
        route="/app/ferramentas",
        target_route="/app/ferramentas",
        reason="MCP local jurídico, tokens escopados, revogação e auditoria possuem implementação própria.",
        upstream_evidence=(".vendor/upstreams/bottlecrm/mcp_server",),
        klm_evidence=("app/mcp", "docs/KLM_MCP_LOCAL.md"),
        test_evidence=("tests/mcp",),
    ),
    _capability(
        "bottlecrm",
        "invoice-products",
        "Catálogo de produtos e serviços",
        "financeiro",
        "Administra itens reutilizáveis para estimativas, propostas e faturas.",
        "approved_full_app",
        "full_app",
        surface="Financeiro",
        route="/app/financeiro",
        target_route="/app/financeiro",
        reason="Entrada específica preservada no motor; a taxonomia jurídica do KLM continua canônica.",
        upstream_evidence=(
            ".vendor/upstreams/bottlecrm/frontend/src/routes/(app)/invoices/products/+page.svelte",
        ),
    ),
    _capability(
        "bottlecrm",
        "invoice-templates",
        "Templates de fatura e estimativa",
        "financeiro",
        "Administra modelos reutilizáveis de documentos financeiros.",
        "approved_full_app",
        "full_app",
        surface="Contratos",
        route="/app/contratos",
        target_route="/app/contratos",
        reason="A tela upstream é preservada sem substituir os templates jurídicos e de proposta do KLM.",
        upstream_evidence=(
            ".vendor/upstreams/bottlecrm/frontend/src/routes/(app)/invoices/templates/+page.svelte",
        ),
    ),
    _capability(
        "bottlecrm",
        "invoice-reports",
        "Relatórios financeiros do CRM",
        "analytics",
        "Consulta relatórios de faturas, recebimento e recorrência.",
        "approved_full_app",
        "full_app",
        surface="KPIs",
        route="/app/kpis",
        target_route="/app/kpis",
        reason="Relatórios do motor complementam, sem substituir, as métricas canônicas do KLM.",
        upstream_evidence=(
            ".vendor/upstreams/bottlecrm/frontend/src/routes/(app)/invoices/reports/+page.svelte",
        ),
    ),
    _capability(
        "bottlecrm",
        "ticket-approvals",
        "Aprovações de tickets",
        "workflow",
        "Lista aprovações operacionais relacionadas a tickets.",
        "approved_full_app",
        "full_app",
        surface="Aprovações",
        route="/app/aprovacoes",
        target_route="/app/aprovacoes",
        reason="A rota é distinta dos gates jurídicos canônicos e serve como apoio operacional.",
        upstream_evidence=(
            ".vendor/upstreams/bottlecrm/frontend/src/routes/(app)/tickets/approvals/+page.svelte",
        ),
    ),
    _capability(
        "bottlecrm",
        "ticket-routing",
        "Regras de roteamento de tickets",
        "workflow",
        "Configura regras comunitárias de distribuição de tickets.",
        "approved_full_app",
        "full_app",
        surface="Flows",
        route="/app/flows",
        target_route="/app/flows",
        reason="Entrada separada das aprovações e do escalonamento para evitar um botão agregado sem precisão.",
        upstream_evidence=(
            ".vendor/upstreams/bottlecrm/frontend/src/routes/(app)/settings/routing/+page.svelte",
        ),
    ),
    _capability(
        "bottlecrm",
        "ticket-escalation",
        "Escalonamento de tickets",
        "workflow",
        "Configura escalonamento por prazo e regra operacional.",
        "approved_full_app",
        "full_app",
        surface="Flows",
        route="/app/flows",
        target_route="/app/flows",
        reason="Escalonamento recebe uma rota própria e não executa efeitos jurídicos irreversíveis.",
        upstream_evidence=(
            ".vendor/upstreams/bottlecrm/frontend/src/routes/(app)/settings/escalation/+page.svelte",
        ),
    ),
    _capability(
        "bottlecrm",
        "organization-settings",
        "Organização do CRM local",
        "administracao",
        "Configura a organização vinculada ao workspace KLM.",
        "approved_full_app",
        "full_app",
        surface="Configurações",
        route="/app/configuracoes",
        target_route="/app/configuracoes",
        reason="O vínculo único é protegido no banco KLM e a tela permite inspecionar a organização do motor.",
        upstream_evidence=(
            ".vendor/upstreams/bottlecrm/frontend/src/routes/(app)/settings/organization/+page.svelte",
        ),
        test_evidence=("tests/services/test_bottlecrm_sync_contract.py",),
    ),
    _capability(
        "bottlecrm",
        "organization-switcher",
        "Seletor de organização do CRM",
        "administracao",
        "Exibe o seletor de organizações do BottleCRM local.",
        "approved_full_app",
        "full_app",
        surface="Configurações",
        route="/app/configuracoes",
        target_route="/app/configuracoes",
        reason="Disponível para inspeção, mas o KLM impede vincular uma organização externa a múltiplos workspaces.",
        upstream_evidence=(
            ".vendor/upstreams/bottlecrm/frontend/src/routes/(no-layout)/org/+page.svelte",
        ),
        test_evidence=("tests/services/test_bottlecrm_sync_contract.py",),
    ),
    _capability(
        "bottlecrm",
        "mobile-app",
        "Aplicativo móvel upstream",
        "mobile",
        "Cliente móvel Flutter preservado no checkout BottleCRM.",
        "not_applicable",
        "none",
        surface="Mobile",
        target_route="/app",
        reason="A SPA responsiva KLM é a experiência móvel canônica; o app upstream permanece preservado e não será distribuído nesta meta.",
        upstream_evidence=(".vendor/upstreams/bottlecrm/mobile",),
        klm_evidence=("frontend/src/klm",),
    ),
    _capability(
        "bottlecrm",
        "external-integrations",
        "E-mail, calendário, pagamento e notificações reais",
        "integracoes",
        "Conecta provedores externos e executa efeitos fora do ambiente local.",
        "blocked_external_gate",
        "none",
        surface="Integrações",
        route="/app/ferramentas",
        target_route="/app/configuracoes",
        reason="Depende de credenciais e aprovação expressa posterior.",
        upstream_evidence=(".vendor/upstreams/bottlecrm/backend",),
        klm_evidence=("app/core/config.py",),
        test_evidence=("tests/services/test_remaining_external_sinks.py",),
    ),
)


def oss_capability_registry() -> dict[str, Any]:
    """Return a validated, deterministic registry and its truthful coverage summary."""

    qa_verification = _qa_verification()
    native_action_counts = Counter(
        action_id.split(":", 1)[0] for action_id in _verification_action_ids()
    )
    verified_native_ids = set(qa_verification["verifiedNativeCapabilityIds"])
    verified_contract_ids = set(
        qa_verification["verifiedContractCapabilityIds"]
    )
    ids: set[str] = set()
    by_provider: Counter[str] = Counter()
    by_disposition: Counter[str] = Counter()
    by_exposure: Counter[str] = Counter()
    by_integration_level: Counter[str] = Counter()
    native_experience = 0
    native_experience_verified = 0
    native_routes = 0
    primary_deep_links = 0
    contract_eligible = 0
    contract_verified = 0
    external_effects_verified = 0
    gated_configuration = 0
    exported_capabilities: list[dict[str, Any]] = []
    for item in _CAPABILITIES:
        if item["provider"] not in PROVIDERS:
            raise RuntimeError(f"Unknown OSS provider in capability registry: {item['provider']}")
        if item["disposition"] not in DISPOSITIONS:
            raise RuntimeError(f"Unknown OSS disposition: {item['disposition']}")
        if item["currentExposure"] not in EXPOSURES:
            raise RuntimeError(f"Unknown OSS exposure: {item['currentExposure']}")
        if item["id"] in ids:
            raise RuntimeError(f"Duplicate OSS capability id: {item['id']}")
        if not item["upstreamEvidence"]:
            raise RuntimeError(f"Capability without upstream evidence: {item['id']}")
        destination = motor_destination(item["provider"], item["key"])
        is_blocked = (
            item["disposition"].startswith("blocked_") or item["disposition"] == "not_applicable"
        )
        if is_blocked and destination is not None:
            raise RuntimeError(f"Blocked capability has a motor destination: {item['id']}")
        native_mode = _native_mode(item)
        if native_mode not in NATIVE_MODES:
            raise RuntimeError(f"Unknown native mode: {native_mode}")
        verification_level = _verification_level(native_mode)
        if verification_level not in VERIFICATION_LEVELS:
            raise RuntimeError(f"Unknown verification level: {verification_level}")
        contract_is_eligible = native_mode not in {
            "gated_configuration",
            "responsive_substitution",
        }
        contract_is_verified = item["id"] in verified_contract_ids
        experience_verified = item["id"] in verified_native_ids
        native_action_count = native_action_counts[item["id"]]
        action_claimed = (
            item["disposition"]
            in {"approved_native", "approved_embedded", "approved_full_app", "implemented"}
            or item["currentExposure"] == "native"
            or experience_verified
        )
        if action_claimed and native_action_count == 0:
            raise RuntimeError(
                f"Capability claims native implementation without an action: {item['id']}"
            )
        domain = NATIVE_DOMAINS[item["provider"]]
        native_route = f"/app/capacidades/{domain}/{item['key']}"
        effective_exposure = "native" if experience_verified else item["currentExposure"]
        effective_target_route = native_route if experience_verified else item["targetRoute"]
        commands_endpoint_enabled = (
            item["provider"] in _PROVIDERS_WITH_NATIVE_COMMAND_DISPATCHER
        )
        klm_evidence = list(
            dict.fromkeys(
                [
                    *item["klmEvidence"],
                    "app/services/oss_native_state_service.py",
                    _NATIVE_SERVICE_EVIDENCE[item["provider"]],
                    "frontend/src/klm/pages/NativeCapabilityPage.tsx",
                    "frontend/src/klm/components/NativeCapabilityWorkspace.tsx",
                ]
            )
        )
        test_evidence = list(
            dict.fromkeys(
                [
                    *item["testEvidence"],
                    "tests/api/test_oss_native.py",
                    "tests/services/test_oss_native_document_matrix.py",
                    _NATIVE_TEST_EVIDENCE[item["provider"]],
                    "frontend/src/test/native-capability-workspace.test.tsx",
                    "frontend/e2e/native-capabilities.spec.ts",
                ]
            )
        )
        exported_capabilities.append(
            {
                **item,
                "sourceDisposition": item["disposition"],
                "sourceExposure": item["currentExposure"],
                "sourceRoute": item["route"],
                "sourceTargetRoute": item["targetRoute"],
                "sourceReason": item["reason"],
                "currentExposure": effective_exposure,
                "targetRoute": effective_target_route,
                "nativeExperience": experience_verified,
                "nativeExperienceVerified": experience_verified,
                "nativeRoute": native_route,
                "nativeMode": native_mode,
                "legacyNativeMode": _legacy_native_mode(item),
                "primaryEngine": "klm",
                "sidecarRole": "preserved_optional_fallback",
                "sidecarRequired": False,
                "primaryExperience": "native" if experience_verified else effective_exposure,
                "primaryDeepLink": False,
                "externalExecutionState": _external_execution_state(item),
                "roundTrip": False,
                "launchable": destination is not None,
                "motorPath": destination,
                "integrationLevel": effective_exposure,
                "contractEligible": contract_is_eligible,
                "contractVerified": contract_is_verified,
                "externalEffectVerified": False,
                "verificationLevel": verification_level,
                "verificationSource": (
                    "qa_route_document_adapter_intersection"
                    if experience_verified
                    else "unverified_contract"
                ),
                "commandsEndpoint": {
                    "enabled": commands_endpoint_enabled,
                    "state": (
                        "dispatcher_available"
                        if commands_endpoint_enabled
                        else "disabled_no_dispatcher"
                    ),
                    "path": (
                        f"/api/v1/capabilities/domains/{domain}/{item['key']}/commands"
                    ),
                    "replacementPath": (
                        f"/api/v1/capabilities/domains/{domain}/{item['key']}/actions"
                    ),
                },
                "reason": (
                    _native_reason(item, native_mode)
                    if experience_verified
                    else item["reason"]
                ),
                "klmEvidence": klm_evidence,
                "testEvidence": test_evidence,
            }
        )
        ids.add(item["id"])
        by_provider[item["provider"]] += 1
        by_disposition[item["disposition"]] += 1
        by_exposure[effective_exposure] += 1
        by_integration_level[effective_exposure] += 1
        native_experience += int(experience_verified)
        native_experience_verified += int(experience_verified)
        # Route existence is a structural fact. The separate nativeExperience
        # counters remain fail-closed until the five release proofs validate it.
        native_routes += int(bool(native_route))
        contract_eligible += int(contract_is_eligible)
        contract_verified += int(contract_is_verified)
        external_effects_verified += 0
        gated_configuration += int(native_mode == "gated_configuration")

    registry_ids = ids
    for provider, routes in MOTOR_DESTINATIONS.items():
        for key in routes:
            capability_id = f"{provider}.{key}"
            if capability_id not in registry_ids:
                raise RuntimeError(
                    f"Motor destination without capability classification: {capability_id}"
                )

    return {
        "schemaVersion": "2.0",
        "scope": "pinned-community-feature-families",
        "capabilities": exported_capabilities,
        "summary": {
            "total": len(_CAPABILITIES),
            "byProvider": dict(sorted(by_provider.items())),
            "byDisposition": dict(sorted(by_disposition.items())),
            "byExposure": dict(sorted(by_exposure.items())),
            "byIntegrationLevel": dict(sorted(by_integration_level.items())),
            "unclassified": by_disposition.get("discovered", 0),
            "nativeExperience": native_experience,
            "nativeExperienceVerified": native_experience_verified,
            "nativeRoute": native_routes,
            "primaryDeepLink": primary_deep_links,
            "contractEligible": contract_eligible,
            "contractVerified": contract_verified,
            "externalEffectVerified": external_effects_verified,
            "gatedConfiguration": gated_configuration,
        },
        "qaVerification": qa_verification,
    }


_PUBLIC_PRODUCT_NAMES = (
    (re.compile(r"chatwoot", re.IGNORECASE), "núcleo de atendimento"),
    (re.compile(r"rocket[ ._-]?chat", re.IGNORECASE), "núcleo de colaboração"),
    (re.compile(r"chattermate", re.IGNORECASE), "núcleo de agentes"),
    (re.compile(r"bottlecrm", re.IGNORECASE), "CRM KLM"),
)


def native_public_text(value: Any) -> str:
    """Translate imported product names into stable KLM product language."""

    rendered = str(value or "")
    for pattern, replacement in _PUBLIC_PRODUCT_NAMES:
        rendered = pattern.sub(replacement, rendered)
    return rendered


def _canonical_capability_id(value: str) -> str:
    provider, separator, key = str(value).partition(".")
    domain = NATIVE_DOMAINS.get(provider)
    if not separator or domain is None:
        return native_public_text(value)
    return f"{domain}.{key}"


def _scrub_public_values(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _scrub_public_values(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_scrub_public_values(child) for child in value]
    if isinstance(value, tuple):
        return tuple(_scrub_public_values(child) for child in value)
    if isinstance(value, str):
        return native_public_text(value)
    return value


def native_capability_registry() -> dict[str, Any]:
    """Return the product-facing registry without imported product vocabulary."""

    registry = oss_capability_registry()
    capabilities: list[dict[str, Any]] = []
    by_domain: Counter[str] = Counter()
    omitted = {
        "provider",
        "upstreamEvidence",
        "klmEvidence",
        "testEvidence",
        "launchable",
        "motorPath",
        "commandsEndpoint",
        "legacyNativeMode",
        "primaryEngine",
        "sidecarRole",
        "verificationSource",
    }
    for item in registry["capabilities"]:
        provider = str(item["provider"])
        domain = NATIVE_DOMAINS[provider]
        public = {key: value for key, value in item.items() if key not in omitted}
        public.update(
            {
                "id": f"{domain}.{item['key']}",
                "domain": domain,
                "contractEligible": bool(item["contractEligible"]),
                "nativeRoute": f"/app/capacidades/{domain}/{item['key']}",
                "reason": native_public_text(item["reason"]),
                "sourceReason": native_public_text(item["sourceReason"]),
                "evidence": {
                    "sourceReferences": len(item["upstreamEvidence"]),
                    "implementationFiles": len(item["klmEvidence"]),
                    "testFiles": len(item["testEvidence"]),
                },
            }
        )
        capabilities.append(public)
        by_domain[domain] += 1

    qa = dict(registry["qaVerification"])
    qa["reason"] = native_public_text(qa.get("reason"))
    qa["verifiedNativeCapabilityIds"] = [
        _canonical_capability_id(value)
        for value in qa.get("verifiedNativeCapabilityIds", [])
    ]
    qa["verifiedContractCapabilityIds"] = [
        _canonical_capability_id(value)
        for value in qa.get("verifiedContractCapabilityIds", [])
    ]
    qa["proofRuns"] = [
        {
            **proof,
            "command": ["klm-local-quality-gate"],
        }
        for proof in qa.get("proofRuns", [])
    ]

    summary = dict(registry["summary"])
    summary.pop("byProvider", None)
    summary["byDomain"] = dict(sorted(by_domain.items()))
    return _scrub_public_values({
        **registry,
        "schemaVersion": "3.0",
        "scope": "klm-native-capabilities",
        "capabilities": capabilities,
        "summary": summary,
        "qaVerification": qa,
    })
