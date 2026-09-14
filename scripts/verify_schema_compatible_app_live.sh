#!/usr/bin/env bash
set -euo pipefail

: "${AWS_ECS_CLUSTER:?AWS_ECS_CLUSTER is required}"
: "${AWS_ECS_PREVIOUS_APP_SERVICE:?AWS_ECS_PREVIOUS_APP_SERVICE is required}"
: "${AWS_ECS_PREVIOUS_APP_TASK_DEFINITION:?AWS_ECS_PREVIOUS_APP_TASK_DEFINITION is required}"
: "${API_HOSTNAME:?API_HOSTNAME is required}"
: "${KLM_RELEASE_MODE:?KLM_RELEASE_MODE is required}"
: "${KLM_PREVIOUS_SCHEMA_CONTRACT:?KLM_PREVIOUS_SCHEMA_CONTRACT is required}"

if [[ "$KLM_RELEASE_MODE" != "compatible-update" ]] || \
  [[ "$KLM_PREVIOUS_SCHEMA_CONTRACT" != "resolver-owner-v1" ]]; then
  printf '%s\n' \
    'Previous-task probing is allowed only for an explicit resolver-owner-v1 compatible update.' >&2
  exit 1
fi
if [[ -n "${KLM_PREVIOUS_APP_REF:-}" ]]; then
  printf '%s\n' 'Legacy KLM_PREVIOUS_APP_REF probing is forbidden in this release model.' >&2
  exit 1
fi

for tool in aws curl jq; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf '%s is required for the schema-compatible task gate.\n' "$tool" >&2
    exit 1
  }
done

active_task_definition="$(aws ecs describe-services \
  --cluster "$AWS_ECS_CLUSTER" \
  --services "$AWS_ECS_PREVIOUS_APP_SERVICE" \
  --query 'services[0].taskDefinition' \
  --output text)"
if [[ "$active_task_definition" != "$AWS_ECS_PREVIOUS_APP_TASK_DEFINITION" ]]; then
  printf 'The API task definition changed before the schema-compatible task gate.\n' >&2
  exit 1
fi

health_payload="$(curl --fail --silent --show-error \
  --connect-timeout 10 --max-time 30 \
  "https://${API_HOSTNAME}/health")"
jq -e '.status == "ok" and (.app | type == "string")' <<<"$health_payload" >/dev/null || {
  printf 'The schema-compatible application health contract failed.\n' >&2
  exit 1
}

status_payload="$(curl --fail --silent --show-error \
  --connect-timeout 10 --max-time 45 \
  "https://${API_HOSTNAME}/api/v1/status")"
jq -e '.components.database.status == "operational"' <<<"$status_payload" >/dev/null || {
  printf 'The schema-compatible application did not prove an operational database connection.\n' >&2
  exit 1
}

printf 'Previous resolver-owner-v1 application passed its live database contract.\n'
