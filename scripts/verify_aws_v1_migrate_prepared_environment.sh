#!/usr/bin/env bash
set -euo pipefail

required=(
  AWS_REGION
  AWS_MIGRATION_ROLE_TO_ASSUME
  AWS_RUNTIME_ROLE_TO_ASSUME
  AWS_APPLICATION_STACK_NAME
  DEPLOY_ENVIRONMENT
  AWS_ECS_CLUSTER
  AWS_ECS_MIGRATION_TASK_FAMILY
  AWS_ECS_MIGRATION_CONTAINER_NAME
  AWS_SFN_MIGRATION_STATE_MACHINE_ARN
  AWS_MIGRATION_LOG_GROUP_NAME
  AWS_RDS_DB_IDENTIFIER
  KLM_RELEASE_MODE
)

missing=()
for name in "${required[@]}"; do
  [[ -n "${!name:-}" ]] || missing+=("$name")
done
if ((${#missing[@]})); then
  printf 'Required prepared-migration variables are missing: %s\n' "${missing[*]}" >&2
  exit 1
fi

if [[ "$DEPLOY_ENVIRONMENT" != "staging" ]]; then
  echo "DEPLOY_ENVIRONMENT must be staging for prepared migration." >&2
  exit 1
fi
if [[ "$AWS_MIGRATION_ROLE_TO_ASSUME" == "$AWS_RUNTIME_ROLE_TO_ASSUME" ]]; then
  echo "Migration and snapshot must use distinct OIDC roles." >&2
  exit 1
fi

"$(dirname "$0")/verify_greenfield_v1_mode.sh"

printf 'Prepared-migration environment approved; ENABLED, frontend and DNS remain out of scope.\n'
