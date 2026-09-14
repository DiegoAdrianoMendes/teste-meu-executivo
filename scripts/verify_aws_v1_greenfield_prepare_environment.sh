#!/usr/bin/env bash
set -euo pipefail

required=(
  AWS_REGION
  AWS_IMAGE_ROLE_TO_ASSUME
  AWS_MIGRATION_ROLE_TO_ASSUME
  AWS_RUNTIME_ROLE_TO_ASSUME
  DEPLOY_ENVIRONMENT
  AWS_ECR_REPOSITORY
  AWS_ECS_CLUSTER
  AWS_ECS_MIGRATION_TASK_FAMILY
  AWS_ECS_MIGRATION_CONTAINER_NAME
  AWS_SFN_MIGRATION_STATE_MACHINE_ARN
  AWS_RDS_DB_IDENTIFIER
  KLM_RELEASE_MODE
)

missing=()
for name in "${required[@]}"; do
  if [[ -z "${!name:-}" ]]; then
    missing+=("$name")
  fi
done
if ((${#missing[@]})); then
  printf 'Variáveis obrigatórias ausentes: %s\n' "${missing[*]}" >&2
  exit 1
fi

if [[ "$DEPLOY_ENVIRONMENT" != "staging" ]]; then
  printf 'DEPLOY_ENVIRONMENT deve ser staging na preparação greenfield.\n' >&2
  exit 1
fi

oidc_roles=(
  "$AWS_IMAGE_ROLE_TO_ASSUME"
  "$AWS_MIGRATION_ROLE_TO_ASSUME"
  "$AWS_RUNTIME_ROLE_TO_ASSUME"
)
if [[ "$(printf '%s\n' "${oidc_roles[@]}" | sort -u | wc -l | tr -d ' ')" != "3" ]]; then
  printf 'Imagem, migração e snapshot precisam usar três roles OIDC distintas.\n' >&2
  exit 1
fi

"$(dirname "$0")/verify_greenfield_v1_mode.sh"

printf '%s\n' \
  'Contrato de preparação greenfield AWS V1 aprovado; RuntimeState permanece PREPARED.'
