#!/usr/bin/env bash
set -euo pipefail

: "${GITHUB_SHA:?GITHUB_SHA is required}"

evidence_file="${1:?candidate evidence JSON path is required}"
checksum_file="${2:?candidate evidence checksum path is required}"
incompatible_revision="a671ed13e0b67d000cb726d70d3d4fee9e446bbe"

if [[ "$GITHUB_SHA" == "$incompatible_revision" ]]; then
  echo "The a671ed1 legacy revision cannot be prepared." >&2
  exit 1
fi
if [[ ! "$GITHUB_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  echo "GITHUB_SHA must be a full lowercase Git SHA-1." >&2
  exit 1
fi
if [[ ! -s "$evidence_file" || ! -s "$checksum_file" ]]; then
  echo "Candidate evidence or its checksum is missing." >&2
  exit 1
fi

actual_checksum="$(sha256sum "$evidence_file" | awk '{print $1}')"
if [[ -n "${EXPECTED_CANDIDATE_EVIDENCE_SHA256:-}" ]] && \
  [[ "$actual_checksum" != "$EXPECTED_CANDIDATE_EVIDENCE_SHA256" ]]; then
  echo "Candidate evidence does not match the trusted Gate 1 checksum output." >&2
  exit 1
fi
expected_checksum="$(awk 'NF == 2 {print $1}' "$checksum_file")"
if [[ ! "$expected_checksum" =~ ^[0-9a-f]{64}$ ]] || \
  [[ "$actual_checksum" != "$expected_checksum" ]]; then
  echo "Candidate evidence checksum mismatch." >&2
  exit 1
fi

jq -e --arg github_sha "$GITHUB_SHA" '
  .schemaVersion == 1 and
  .evidenceType == "candidate" and
  .cleanCheckout == true and
  .githubSha == $github_sha and
  .ociRevisionLabel == $github_sha and
  (.runtimeImageDigest | test("^sha256:[0-9a-f]{64}$")) and
  .runtimeImageUri == (.candidateImageContract.runtimeImageUri) and
  .runtimeImageDigest == (.candidateImageContract.runtimeImageDigest) and
  .runtimeImageUri == ((.runtimeImageUri | split("@")[0]) + "@" + .runtimeImageDigest) and
  .candidateImageContract.sourceRevision == $github_sha and
  .candidateImageContract.ociRevisionLabel == $github_sha and
  .alembicHeads == ["c9d0e1f2a3b4"] and
  .candidateImageContract.alembicHead == "c9d0e1f2a3b4" and
  .candidateImageContract.databaseContract == "resolver-owner-v1" and
  .releaseContractPassed == true and
  .postHeadCatalogAuditRequired == true and
  .postHeadContractMarkerRequired ==
    "Database migration and post-head runtime grant contract completed." and
  (.workflowRunId | type == "string" and length > 0) and
  (has("postHeadCatalogAudit") | not) and
  (has("postHeadContractMarker") | not) and
  (has("postHeadContractMarkerObserved") | not) and
  (has("rdsSnapshotId") | not) and
  (has("rdsSnapshotStatusObserved") | not) and
  (has("runtimeStateObserved") | not) and
  (has("migration") | not) and
  (has("deploymentMutations") | not)
' "$evidence_file" >/dev/null || {
  echo "Candidate release evidence violates the fail-closed schema." >&2
  exit 1
}

backend_image="$(jq -er '.runtimeImageUri' "$evidence_file")"
runtime_image_digest="$(jq -er '.runtimeImageDigest' "$evidence_file")"
workflow_run_id="$(jq -er '.workflowRunId' "$evidence_file")"

if [[ -n "${GITHUB_ENV:-}" ]]; then
  {
    printf 'BACKEND_IMAGE=%s\n' "$backend_image"
    printf 'CANDIDATE_EVIDENCE_SHA256=%s\n' "$actual_checksum"
    printf 'CANDIDATE_WORKFLOW_RUN_ID=%s\n' "$workflow_run_id"
  } >> "$GITHUB_ENV"
fi

printf 'Candidate release evidence verified by checksum and exact schema.\n'
