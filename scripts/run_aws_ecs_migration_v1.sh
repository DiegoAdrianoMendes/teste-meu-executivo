#!/usr/bin/env bash
set -euo pipefail

: "${BACKEND_IMAGE:?BACKEND_IMAGE é obrigatório}"
: "${AWS_ECS_MIGRATION_TASK_FAMILY:?AWS_ECS_MIGRATION_TASK_FAMILY é obrigatório}"
: "${AWS_ECS_MIGRATION_TASK_DEFINITION_ARN:?AWS_ECS_MIGRATION_TASK_DEFINITION_ARN é obrigatório}"
: "${AWS_ECS_MIGRATION_CONTAINER_NAME:?AWS_ECS_MIGRATION_CONTAINER_NAME é obrigatório}"
: "${AWS_SFN_MIGRATION_STATE_MACHINE_ARN:?AWS_SFN_MIGRATION_STATE_MACHINE_ARN é obrigatório}"
: "${AWS_MIGRATION_LOG_GROUP_NAME:?AWS_MIGRATION_LOG_GROUP_NAME é obrigatório}"
: "${DEPLOY_ENVIRONMENT:?DEPLOY_ENVIRONMENT é obrigatório}"
: "${GITHUB_SHA:?GITHUB_SHA é obrigatório}"

if [[ ! "$BACKEND_IMAGE" =~ @sha256:[0-9a-f]{64}$ ]]; then
  echo "BACKEND_IMAGE precisa referenciar um digest sha256 imutável." >&2
  exit 1
fi

if [[ ! "$GITHUB_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  echo "GITHUB_SHA precisa ser um SHA-1 Git completo em minúsculas." >&2
  exit 1
fi

temporary_dir="$(mktemp -d "${TMPDIR:-/tmp}/meu-executivo-migration.XXXXXXXX")"
cleanup() {
  if [[ -n "$temporary_dir" && -d "$temporary_dir" ]]; then
    rm -rf -- "$temporary_dir"
  fi
}
trap cleanup EXIT INT TERM

"$(dirname "$0")/verify_aws_database_bootstrap_absent.sh"

prepared_task_definition="$(aws ecs describe-task-definition \
  --task-definition "$AWS_ECS_MIGRATION_TASK_DEFINITION_ARN" \
  --query taskDefinition \
  --output json)"
prepared_task_contract="$(
  jq -r \
    --arg task_definition_arn "$AWS_ECS_MIGRATION_TASK_DEFINITION_ARN" \
    --arg image "$BACKEND_IMAGE" \
    --arg container "$AWS_ECS_MIGRATION_CONTAINER_NAME" '
      [.containerDefinitions[]? | select(.name == $container)] as $targets |
      .taskDefinitionArn == $task_definition_arn and
      .status == "ACTIVE" and
      ($targets | length) == 1 and
      $targets[0].image == $image and
      $targets[0].command == ["python", "-m", "app.db.aws_database_roles", "migrate"]
    ' <<<"$prepared_task_definition"
)"
if [[ "$prepared_task_contract" != "true" ]]; then
  echo "A task PREPARED não corresponde ao ARN, imagem e comando aprovados." >&2
  exit 1
fi
migration_revision="$AWS_ECS_MIGRATION_TASK_DEFINITION_ARN"

# Close the race between the initial proof and Step Functions start. The job
# cannot register or select a task revision; CloudFormation pins it in PREPARED.
"$(dirname "$0")/verify_aws_database_bootstrap_absent.sh"

correlation_id="${GITHUB_SHA}/${GITHUB_RUN_ID:-manual}"
migration_input="$(jq -cn \
  --arg correlation_id "$correlation_id" \
  '{correlationId: $correlation_id}')"
migration_execution_name="meu-executivo-${GITHUB_RUN_ID:-manual}-${GITHUB_RUN_ATTEMPT:-1}-$(date +%s)"
migration_execution_arn="$(aws stepfunctions start-execution \
  --state-machine-arn "$AWS_SFN_MIGRATION_STATE_MACHINE_ARN" \
  --name "$migration_execution_name" \
  --input "$migration_input" \
  --query 'executionArn' \
  --output text)"
if [[ -z "$migration_execution_arn" || "$migration_execution_arn" == "None" ]]; then
  echo "A execução bloqueante da migração não foi iniciada." >&2
  exit 1
fi

migration_timeout_seconds="${AWS_SFN_MIGRATION_TIMEOUT_SECONDS:-1800}"
if [[ ! "$migration_timeout_seconds" =~ ^[0-9]+$ ]] || \
  ((migration_timeout_seconds < 60 || migration_timeout_seconds > 7200)); then
  echo "AWS_SFN_MIGRATION_TIMEOUT_SECONDS precisa estar entre 60 e 7200." >&2
  exit 1
fi
migration_deadline=$((SECONDS + migration_timeout_seconds))
while true; do
  migration_execution="$(aws stepfunctions describe-execution \
    --execution-arn "$migration_execution_arn" \
    --output json)"
  migration_status="$(jq -r '.status' <<<"$migration_execution")"
  case "$migration_status" in
    RUNNING)
      if ((SECONDS >= migration_deadline)); then
        echo "A execução da migração excedeu o limite; nenhum serviço foi atualizado." >&2
        exit 1
      fi
      sleep 5
      ;;
    SUCCEEDED)
      break
      ;;
    FAILED|TIMED_OUT|ABORTED)
      echo "Migração terminou com status $migration_status; nenhum serviço foi atualizado." >&2
      exit 1
      ;;
    *)
      echo "Status inesperado da execução de migração: $migration_status" >&2
      exit 1
      ;;
  esac
done

migration_output="$(jq -cer '.output | fromjson' <<<"$migration_execution")" || {
  echo "A saída da execução de migração não é JSON válido." >&2
  exit 1
}
migration_evidence_valid="$(jq -r \
  --arg task_definition_arn "$migration_revision" \
  --arg correlation_id "$correlation_id" '
    .correlationId == $correlation_id and
    .migration.taskDefinitionArn == $task_definition_arn and
    .migration.exitCode == 0 and
    (.migration.taskArn | type == "string" and length > 0) and
    (.migration.stoppedReason | type == "string")
  ' <<<"$migration_output")"
if [[ "$migration_evidence_valid" != "true" ]]; then
  echo "A evidência da migração não corresponde à task candidata com exit code zero." >&2
  exit 1
fi

migration_task_arn="$(jq -er '.migration.taskArn' <<<"$migration_output")"
migration_task_id="${migration_task_arn##*/}"
if [[ ! "$migration_task_id" =~ ^[A-Za-z0-9-]+$ ]]; then
  echo "A task ARN da migração não contém um identificador seguro." >&2
  exit 1
fi

migration_log_stream="migration/${AWS_ECS_MIGRATION_CONTAINER_NAME}/${migration_task_id}"
migration_contract_marker="Database migration and post-head runtime grant contract completed."
migration_log_timeout_seconds="${AWS_MIGRATION_LOG_TIMEOUT_SECONDS:-120}"
if [[ ! "$migration_log_timeout_seconds" =~ ^[0-9]+$ ]] || \
  ((migration_log_timeout_seconds < 15 || migration_log_timeout_seconds > 300)); then
  echo "AWS_MIGRATION_LOG_TIMEOUT_SECONDS precisa estar entre 15 e 300." >&2
  exit 1
fi
migration_log_deadline=$((SECONDS + migration_log_timeout_seconds))
while true; do
  marker_error_file="$temporary_dir/cloudwatch-marker-error"
  if marker_events="$(
    aws logs filter-log-events \
      --log-group-name "$AWS_MIGRATION_LOG_GROUP_NAME" \
      --log-stream-names "$migration_log_stream" \
      --filter-pattern "\"$migration_contract_marker\"" \
      --query events \
      --output json 2>"$marker_error_file"
  )"; then
    marker_query_succeeded=1
  else
    marker_query_succeeded=0
    marker_error="$(<"$marker_error_file")"
    case "$marker_error" in
      *ResourceNotFoundException*|*ThrottlingException*|*ServiceUnavailableException*) ;;
      *)
        echo "A consulta privada do marker falhou com erro não transitório; ENABLED continua bloqueado." >&2
        exit 1
        ;;
    esac
  fi
  if ((marker_query_succeeded == 1)) && jq -e --arg marker "$migration_contract_marker" '
    any(.[]?; .message? as $message |
      ($message | type == "string") and ($message | contains($marker)))
  ' <<<"$marker_events" >/dev/null; then
    break
  fi
  if ((SECONDS >= migration_log_deadline)); then
    echo "O marker pós-head não apareceu no log privado da task; ENABLED continua bloqueado." >&2
    exit 1
  fi
  sleep 5
done

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  {
    printf 'task_definition_arn=%s\n' "$migration_revision"
    printf 'execution_arn=%s\n' "$migration_execution_arn"
    printf 'task_arn=%s\n' "$migration_task_arn"
    printf 'log_stream=%s\n' "$migration_log_stream"
    printf 'contract_marker_observed=true\n'
    printf 'post_head_catalog_audit=true\n'
    printf 'database_head_observed=c9d0e1f2a3b4\n'
  } >> "$GITHUB_OUTPUT"
fi

echo "Migração e marker pós-head confirmados com evidência bloqueante válida."
