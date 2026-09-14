#!/usr/bin/env bash
set -euo pipefail

required=(
  AWS_REGION
  AWS_IMAGE_ROLE_TO_ASSUME
  DEPLOY_ENVIRONMENT
  AWS_ECR_REPOSITORY
  KLM_RELEASE_MODE
)

missing=()
for name in "${required[@]}"; do
  [[ -n "${!name:-}" ]] || missing+=("$name")
done
if ((${#missing[@]})); then
  printf 'Required candidate variables are missing: %s\n' "${missing[*]}" >&2
  exit 1
fi

if [[ "$DEPLOY_ENVIRONMENT" != "staging" ]]; then
  echo "DEPLOY_ENVIRONMENT must be staging for candidate qualification." >&2
  exit 1
fi

"$(dirname "$0")/verify_greenfield_v1_mode.sh"

printf 'Candidate qualification environment approved; no runtime mutation is authorized.\n'
