#!/usr/bin/env bash
set -euo pipefail

required=(
  AWS_REGION
  AWS_IMAGE_ROLE_TO_ASSUME
  AWS_MIGRATION_ROLE_TO_ASSUME
  AWS_RUNTIME_ROLE_TO_ASSUME
  AWS_FRONTEND_ROLE_TO_ASSUME
  DEPLOY_ENVIRONMENT
  AWS_ECR_REPOSITORY
  AWS_ECS_CLUSTER
  AWS_ECS_SERVICE_TARGETS
  AWS_ECS_MIGRATION_TASK_FAMILY
  AWS_ECS_MIGRATION_CONTAINER_NAME
  AWS_SFN_MIGRATION_STATE_MACHINE_ARN
  AWS_RDS_DB_IDENTIFIER
  AWS_APP_BUCKET
  AWS_BLACKBOX_BUCKET
  AWS_APP_CLOUDFRONT_DISTRIBUTION_ID
  AWS_BLACKBOX_CLOUDFRONT_DISTRIBUTION_ID
  APP_HOSTNAME
  BLACKBOX_HOSTNAME
  API_HOSTNAME
  AWS_PUBLIC_SITE_BUCKET
  AWS_PUBLIC_SITE_CLOUDFRONT_DISTRIBUTION_ID
  KLM_RELEASE_MODE
  KLM_PREVIOUS_SCHEMA_CONTRACT
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

oidc_roles=(
  "$AWS_IMAGE_ROLE_TO_ASSUME"
  "$AWS_MIGRATION_ROLE_TO_ASSUME"
  "$AWS_RUNTIME_ROLE_TO_ASSUME"
  "$AWS_FRONTEND_ROLE_TO_ASSUME"
)
if [[ "$(printf '%s\n' "${oidc_roles[@]}" | sort -u | wc -l | tr -d ' ')" != "4" ]]; then
  echo "As quatro fases AWS precisam usar roles OIDC distintas." >&2
  exit 1
fi

if [[ "$APP_HOSTNAME" != "app.meuexecutivo.com" ]]; then
  echo "APP_HOSTNAME deve ser app.meuexecutivo.com na V1." >&2
  exit 1
fi
if [[ "$DEPLOY_ENVIRONMENT" != "production" ]]; then
  echo "DEPLOY_ENVIRONMENT deve ser production na V1." >&2
  exit 1
fi
if [[ "$KLM_RELEASE_MODE" != "compatible-update" ]] || \
  [[ "$KLM_PREVIOUS_SCHEMA_CONTRACT" != "resolver-owner-v1" ]]; then
  echo "O ambiente de update exige compatible-update sobre resolver-owner-v1." >&2
  exit 1
fi
if [[ -n "${KLM_PREVIOUS_APP_REF:-}" ]]; then
  echo "KLM_PREVIOUS_APP_REF é proibido; a671ed1 não é schema-compatible." >&2
  exit 1
fi
if [[ "$BLACKBOX_HOSTNAME" != "blackbox.meuexecutivo.com" ]]; then
  echo "BLACKBOX_HOSTNAME deve ser blackbox.meuexecutivo.com na V1." >&2
  exit 1
fi
if [[ "$API_HOSTNAME" != "api.meuexecutivo.com" ]]; then
  echo "API_HOSTNAME deve ser api.meuexecutivo.com na V1." >&2
  exit 1
fi
if [[ "$APP_HOSTNAME" == "$BLACKBOX_HOSTNAME" ]]; then
  echo "App e Blackbox precisam de hostnames distintos." >&2
  exit 1
fi
if [[ "$AWS_APP_BUCKET" == "$AWS_BLACKBOX_BUCKET" ]]; then
  echo "App e Blackbox precisam de buckets distintos." >&2
  exit 1
fi
if [[ "$AWS_APP_CLOUDFRONT_DISTRIBUTION_ID" == "$AWS_BLACKBOX_CLOUDFRONT_DISTRIBUTION_ID" ]]; then
  echo "App e Blackbox precisam de distribuições CloudFront distintas." >&2
  exit 1
fi
if {
  [[ "$AWS_APP_BUCKET" == "$AWS_PUBLIC_SITE_BUCKET" ]] ||
    [[ "$AWS_BLACKBOX_BUCKET" == "$AWS_PUBLIC_SITE_BUCKET" ]]
}; then
  echo "Os buckets da aplicação não podem reutilizar o bucket do site público." >&2
  exit 1
fi
if {
  [[ "$AWS_APP_CLOUDFRONT_DISTRIBUTION_ID" == "$AWS_PUBLIC_SITE_CLOUDFRONT_DISTRIBUTION_ID" ]] ||
    [[ "$AWS_BLACKBOX_CLOUDFRONT_DISTRIBUTION_ID" == "$AWS_PUBLIC_SITE_CLOUDFRONT_DISTRIBUTION_ID" ]]
}; then
  echo "As aplicações não podem reutilizar a distribuição do site público." >&2
  exit 1
fi

python3 - <<'PY'
import json
import os

targets = json.loads(os.environ["AWS_ECS_SERVICE_TARGETS"])
if not isinstance(targets, list) or not targets:
    raise SystemExit("AWS_ECS_SERVICE_TARGETS deve ser uma lista JSON não vazia.")
required_roles = {"api", "worker", "beat"}
roles = set()
services = set()
for target in targets:
    if not isinstance(target, dict):
        raise SystemExit("Cada target ECS deve ser um objeto.")
    role = str(target.get("role", "")).strip()
    service = str(target.get("service", "")).strip()
    container = str(target.get("container", "")).strip()
    if not role or not service or not container:
        raise SystemExit("Cada target ECS exige role, service e container.")
    if service in services:
        raise SystemExit(f"Serviço ECS duplicado: {service}")
    if role in roles:
        raise SystemExit(f"Role ECS duplicada: {role}")
    roles.add(role)
    services.add(service)
missing = sorted(required_roles - roles)
if missing:
    raise SystemExit("Targets ECS ausentes para: " + ", ".join(missing))
unexpected = sorted(roles - required_roles)
if unexpected:
    raise SystemExit("Targets ECS inesperados: " + ", ".join(unexpected))
PY

echo "Contrato de ambiente AWS V1 aprovado."
