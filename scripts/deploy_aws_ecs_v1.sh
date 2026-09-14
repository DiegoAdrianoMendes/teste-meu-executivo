#!/usr/bin/env bash
set -Eeuo pipefail

: "${BACKEND_IMAGE:?BACKEND_IMAGE é obrigatório}"
: "${AWS_ECS_CLUSTER:?AWS_ECS_CLUSTER é obrigatório}"
: "${AWS_ECS_SERVICE_TARGETS:?AWS_ECS_SERVICE_TARGETS é obrigatório}"
: "${API_HOSTNAME:?API_HOSTNAME é obrigatório}"
: "${DEPLOY_ENVIRONMENT:?DEPLOY_ENVIRONMENT é obrigatório}"
: "${GITHUB_SHA:?GITHUB_SHA é obrigatório}"
: "${KLM_RELEASE_MODE:?KLM_RELEASE_MODE é obrigatório}"
: "${KLM_PREVIOUS_SCHEMA_CONTRACT:?KLM_PREVIOUS_SCHEMA_CONTRACT é obrigatório}"

if [[ "$KLM_RELEASE_MODE" != "compatible-update" ]]; then
  echo "O rollout ECS é proibido no greenfield-prepare; RuntimeState deve permanecer PREPARED." >&2
  exit 1
fi
if [[ "$KLM_PREVIOUS_SCHEMA_CONTRACT" != "resolver-owner-v1" ]]; then
  echo "A task anterior não comprovou o contrato resolver-owner-v1; restauração binária é proibida." >&2
  exit 1
fi
if [[ -n "${KLM_PREVIOUS_APP_REF:-}" ]]; then
  echo "KLM_PREVIOUS_APP_REF é proibido: a671ed1 não é compatível com este schema." >&2
  exit 1
fi

if [[ ! "$BACKEND_IMAGE" =~ @sha256:[0-9a-f]{64}$ ]]; then
  echo "BACKEND_IMAGE precisa referenciar um digest sha256 imutável." >&2
  exit 1
fi
if [[ ! "$GITHUB_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  echo "GITHUB_SHA precisa ser um SHA-1 Git completo em minúsculas." >&2
  exit 1
fi

temporary_dir="$(mktemp -d "${TMPDIR:-/tmp}/meu-executivo-rollout.XXXXXXXX")"
cleanup() {
  if [[ -d "$temporary_dir" ]]; then
    rm -rf -- "$temporary_dir"
  fi
}

declare -a updated_services=()
declare -a previous_task_definitions=()

restore_compatible_services() {
  local index
  local failed=0
  declare -a restored_services=()
  if ((${#updated_services[@]} == 0)); then
    return 0
  fi
  echo "Falha no update compatível; restaurando apenas task definitions resolver-owner-v1." >&2
  for ((index=${#updated_services[@]} - 1; index >= 0; index--)); do
    if aws ecs update-service \
      --cluster "$AWS_ECS_CLUSTER" \
      --service "${updated_services[$index]}" \
      --task-definition "${previous_task_definitions[$index]}" \
      --force-new-deployment >/dev/null; then
      restored_services+=("${updated_services[$index]}")
    else
      failed=1
    fi
  done
  if ((${#restored_services[@]} > 0)) && ! aws ecs wait services-stable \
    --cluster "$AWS_ECS_CLUSTER" \
    --services "${restored_services[@]}"; then
    failed=1
  fi
  if ((failed)); then
    echo "RESTAURAÇÃO ECS INCOMPLETA: recuperação manual é obrigatória; o banco não foi revertido." >&2
    return 1
  fi
  echo "Restauração das tasks schema-compatíveis confirmada estável; o banco não foi revertido." >&2
}

abort_compatible_update() {
  local status="${1:-1}"
  local restore_status=0
  trap - ERR INT TERM EXIT
  set +e
  restore_compatible_services
  restore_status=$?
  cleanup
  if ((restore_status != 0)); then
    exit 1
  fi
  if ((status == 0)); then
    status=1
  fi
  exit "$status"
}

trap cleanup EXIT
trap 'abort_compatible_update $?' ERR
trap 'abort_compatible_update 130' INT
trap 'abort_compatible_update 143' TERM

render_task_definition() {
  local task_definition_arn="$1"
  local container_name="$2"
  local output_path="$3"

  aws ecs describe-task-definition \
    --task-definition "$task_definition_arn" \
    --query taskDefinition \
    --output json |
    jq --arg image "$BACKEND_IMAGE" --arg container "$container_name" '
      if any(.containerDefinitions[]; .name == $container) | not then
        error("Container ECS não encontrado: " + $container)
      else
        .containerDefinitions |= map(
          if .name == $container then .image = $image else . end
        )
      end |
      del(
        .taskDefinitionArn,
        .revision,
        .status,
        .requiresAttributes,
        .compatibilities,
        .registeredAt,
        .registeredBy
      )
    ' > "$output_path"
}

register_task_definition() {
  local definition_path="$1"
  aws ecs register-task-definition \
    --cli-input-json "file://$definition_path" \
    --tags \
      "key=Project,value=meu-executivo" \
      "key=Environment,value=$DEPLOY_ENVIRONMENT" \
      "key=ReleaseChannel,value=runtime" \
      "key=SourceRevision,value=$GITHUB_SHA" \
    --query 'taskDefinition.taskDefinitionArn' \
    --output text
}

verify_bootstrap_absent() {
  "$(dirname "$0")/verify_aws_database_bootstrap_absent.sh"
}

# The ephemeral bootstrap plane must be gone before the schema-compatible application is
# probed or any runtime task definition is registered. Re-check immediately
# before every service mutation to close the reappearance race between targets.
if ! verify_bootstrap_absent; then
  abort_compatible_update 1
fi

api_target_count="$(jq '[.[] | select(.role == "api")] | length' <<<"$AWS_ECS_SERVICE_TARGETS")"
if [[ "$api_target_count" != "1" ]]; then
  echo "AWS_ECS_SERVICE_TARGETS precisa conter exatamente um serviço API." >&2
  exit 1
fi
api_target="$(jq -c '.[] | select(.role == "api")' <<<"$AWS_ECS_SERVICE_TARGETS")"
api_service="$(jq -r '.service' <<<"$api_target")"
previous_api_task_definition="$(aws ecs describe-services \
  --cluster "$AWS_ECS_CLUSTER" \
  --services "$api_service" \
  --query 'services[0].taskDefinition' \
  --output text)"
if [[ -z "$previous_api_task_definition" || "$previous_api_task_definition" == "None" ]]; then
  echo "Serviço ECS API não encontrado: $api_service" >&2
  exit 1
fi

AWS_ECS_PREVIOUS_APP_SERVICE="$api_service" \
AWS_ECS_PREVIOUS_APP_TASK_DEFINITION="$previous_api_task_definition" \
  "$(dirname "$0")/verify_schema_compatible_app_live.sh"

while IFS= read -r target; do
  role=$(jq -r '.role' <<<"$target")
  service=$(jq -r '.service' <<<"$target")
  container=$(jq -r '.container' <<<"$target")
  current_task_definition=$(aws ecs describe-services \
    --cluster "$AWS_ECS_CLUSTER" \
    --services "$service" \
    --query 'services[0].taskDefinition' \
    --output text)
  if [[ -z "$current_task_definition" || "$current_task_definition" == "None" ]]; then
    echo "Serviço ECS não encontrado: $service" >&2
    exit 1
  fi

  render_task_definition \
    "$current_task_definition" \
    "$container" \
    "$temporary_dir/$role.json"
  next_task_definition=$(register_task_definition "$temporary_dir/$role.json")
  if ! verify_bootstrap_absent; then
    abort_compatible_update 1
  fi
  updated_services+=("$service")
  previous_task_definitions+=("$current_task_definition")
  aws ecs update-service \
    --cluster "$AWS_ECS_CLUSTER" \
    --service "$service" \
    --task-definition "$next_task_definition" \
    --force-new-deployment >/dev/null
done < <(jq -c '.[]' <<<"$AWS_ECS_SERVICE_TARGETS")

aws ecs wait services-stable \
  --cluster "$AWS_ECS_CLUSTER" \
  --services "${updated_services[@]}"

trap - ERR INT TERM
trap cleanup EXIT
echo "Aplicação anterior resolver-owner-v1 validada e serviços ECS atualizados para $BACKEND_IMAGE."
