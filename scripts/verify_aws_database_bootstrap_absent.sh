#!/usr/bin/env bash
set -euo pipefail

: "${AWS_REGION:?AWS_REGION is required}"
: "${DEPLOY_ENVIRONMENT:?DEPLOY_ENVIRONMENT is required}"
: "${AWS_ECS_CLUSTER:?AWS_ECS_CLUSTER is required}"
: "${AWS_SFN_MIGRATION_STATE_MACHINE_ARN:?AWS_SFN_MIGRATION_STATE_MACHINE_ARN is required}"

case "$AWS_SFN_MIGRATION_STATE_MACHINE_ARN" in
  arn:*:states:*:*:stateMachine:*) ;;
  *)
    printf 'AWS_SFN_MIGRATION_STATE_MACHINE_ARN is not a state machine ARN.\n' >&2
    exit 1
    ;;
esac

command -v jq >/dev/null 2>&1 || {
  printf 'jq is required to verify bootstrap resource absence.\n' >&2
  exit 1
}

temporary_dir="$(mktemp -d "${TMPDIR:-/tmp}/meu-executivo-bootstrap-absence.XXXXXXXX")"
cleanup() {
  if [[ -n "$temporary_dir" && -d "$temporary_dir" ]]; then
    rm -rf -- "$temporary_dir"
  fi
}
trap cleanup EXIT INT TERM

assert_aws_resource_absent() {
  local label="$1"
  local expected_error_code="$2"
  shift 2
  local error_path="$temporary_dir/error"

  if "$@" >/dev/null 2>"$error_path"; then
    printf 'BLOCK: ephemeral bootstrap resource still exists: %s.\n' "$label" >&2
    return 1
  fi

  local actual_error_code
  actual_error_code="$(
    sed -nE 's/^(aws: \[ERROR\]: )?An error occurred \(([^)]+)\).*/\2/p' "$error_path" | head -n 1
  )"
  if [[ "$actual_error_code" != "$expected_error_code" ]]; then
    printf 'BLOCK: could not prove %s absent; expected %s, received %s.\n' \
      "$label" "$expected_error_code" "${actual_error_code:-unrecognized-error}" >&2
    return 1
  fi
}

assert_aws_json_array_empty() {
  local label="$1"
  shift
  local output_path="$temporary_dir/array.json"
  local error_path="$temporary_dir/array-error"

  if ! "$@" >"$output_path" 2>"$error_path"; then
    printf 'BLOCK: could not prove %s absent; AWS inspection failed.\n' "$label" >&2
    return 1
  fi
  if ! jq -e 'type == "array" and length == 0' "$output_path" >/dev/null; then
    printf 'BLOCK: ephemeral bootstrap resource still exists: %s.\n' "$label" >&2
    return 1
  fi
}

bootstrap_prefix="meu-executivo-${DEPLOY_ENVIRONMENT}-db-bootstrap"
assert_aws_resource_absent \
  "SSM parameter /meu-executivo/${DEPLOY_ENVIRONMENT}/database-bootstrap-active" \
  ParameterNotFound \
  aws ssm get-parameter \
    --region "$AWS_REGION" \
    --name "/meu-executivo/${DEPLOY_ENVIRONMENT}/database-bootstrap-active"

for suffix in exec task sfn operator; do
  role_name="${bootstrap_prefix}-${suffix}-ephemeral"
  assert_aws_resource_absent \
    "IAM role ${role_name}" \
    NoSuchEntity \
    aws iam get-role --role-name "$role_name"
done

bootstrap_state_machine_name="meu-executivo-${DEPLOY_ENVIRONMENT}-database-bootstrap-ephemeral"
bootstrap_state_machine_arn="${AWS_SFN_MIGRATION_STATE_MACHINE_ARN%:stateMachine:*}:stateMachine:${bootstrap_state_machine_name}"
assert_aws_resource_absent \
  "Step Functions state machine ${bootstrap_state_machine_name}" \
  StateMachineDoesNotExist \
  aws stepfunctions describe-state-machine \
    --region "$AWS_REGION" \
    --state-machine-arn "$bootstrap_state_machine_arn"

bootstrap_task_family="meu-executivo-${DEPLOY_ENVIRONMENT}-database-bootstrap"
assert_aws_json_array_empty \
  "ACTIVE ECS task definition family ${bootstrap_task_family}" \
  aws ecs list-task-definitions \
    --region "$AWS_REGION" \
    --family-prefix "$bootstrap_task_family" \
    --status ACTIVE \
    --query taskDefinitionArns \
    --output json

for desired_status in RUNNING PENDING; do
  assert_aws_json_array_empty \
    "${desired_status} ECS bootstrap tasks in ${AWS_ECS_CLUSTER}" \
    aws ecs list-tasks \
      --region "$AWS_REGION" \
      --cluster "$AWS_ECS_CLUSTER" \
      --family "$bootstrap_task_family" \
      --desired-status "$desired_status" \
      --query taskArns \
      --output json
done

assert_aws_json_array_empty \
  "database-bootstrap security groups tagged for ${DEPLOY_ENVIRONMENT}" \
  aws ec2 describe-security-groups \
    --region "$AWS_REGION" \
    --filters \
      Name=tag:Project,Values=meu-executivo \
      "Name=tag:Environment,Values=${DEPLOY_ENVIRONMENT}" \
      Name=tag:Ephemeral,Values=database-bootstrap \
    --query SecurityGroups \
    --output json

printf 'Ephemeral database bootstrap absence contract passed.\n'
