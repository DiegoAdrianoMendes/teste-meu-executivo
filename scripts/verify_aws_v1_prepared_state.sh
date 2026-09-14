#!/usr/bin/env bash
set -euo pipefail

: "${AWS_APPLICATION_STACK_NAME:?AWS_APPLICATION_STACK_NAME is required}"
: "${AWS_ECS_CLUSTER:?AWS_ECS_CLUSTER is required}"
: "${AWS_ECS_MIGRATION_TASK_FAMILY:?AWS_ECS_MIGRATION_TASK_FAMILY is required}"
: "${AWS_ECS_MIGRATION_CONTAINER_NAME:?AWS_ECS_MIGRATION_CONTAINER_NAME is required}"
: "${AWS_SFN_MIGRATION_STATE_MACHINE_ARN:?AWS_SFN_MIGRATION_STATE_MACHINE_ARN is required}"
: "${AWS_MIGRATION_LOG_GROUP_NAME:?AWS_MIGRATION_LOG_GROUP_NAME is required}"
: "${BACKEND_IMAGE:?BACKEND_IMAGE is required}"

if [[ ! "$BACKEND_IMAGE" =~ @sha256:[0-9a-f]{64}$ ]]; then
  echo "BACKEND_IMAGE must identify an immutable sha256 digest." >&2
  exit 1
fi
expected_digest="${BACKEND_IMAGE##*@}"

stack_json="$(
  aws cloudformation describe-stacks \
    --stack-name "$AWS_APPLICATION_STACK_NAME" \
    --query 'Stacks[0]' \
    --output json
)"

prepared_contract="$(
  jq -r \
    --arg expected_digest "$expected_digest" \
    --arg expected_state_machine "$AWS_SFN_MIGRATION_STATE_MACHINE_ARN" \
    --arg expected_log_group "$AWS_MIGRATION_LOG_GROUP_NAME" '
      def parameter($name):
        [.Parameters[]? | select(.ParameterKey == $name) | .ParameterValue];
      def output($name):
        [.Outputs[]? | select(.OutputKey == $name) | .OutputValue];
      (.StackStatus == "CREATE_COMPLETE" or .StackStatus == "UPDATE_COMPLETE") and
      parameter("RuntimeState") == ["PREPARED"] and
      parameter("RuntimeImageDigest") == [$expected_digest] and
      output("MigrationStateMachineArn") == [$expected_state_machine] and
      output("MigrationLogGroupName") == [$expected_log_group] and
      (output("MigrationTaskDefinitionArn") | length) == 1
    ' <<<"$stack_json"
)"
if [[ "$prepared_contract" != "true" ]]; then
  echo "CloudFormation is not PREPARED with the exact approved image digest and migration outputs." >&2
  exit 1
fi

prepared_task_definition_arn="$(
  jq -er '
    [.Outputs[]? |
      select(.OutputKey == "MigrationTaskDefinitionArn") |
      .OutputValue] |
    if length == 1 then .[0] else error("MigrationTaskDefinitionArn output is not unique") end
  ' <<<"$stack_json"
)"
if [[ ! "$prepared_task_definition_arn" =~ :task-definition/([^/:]+):[1-9][0-9]*$ ]] || \
  [[ "${BASH_REMATCH[1]}" != "$AWS_ECS_MIGRATION_TASK_FAMILY" ]]; then
  echo "PREPARED migration task definition output does not match the approved family/revision." >&2
  exit 1
fi
if [[ -n "${AWS_ECS_MIGRATION_TASK_DEFINITION_ARN:-}" ]] && \
  [[ "$AWS_ECS_MIGRATION_TASK_DEFINITION_ARN" != "$prepared_task_definition_arn" ]]; then
  echo "PREPARED migration task definition changed between gates." >&2
  exit 1
fi

service_arns="$(
  aws ecs list-services \
    --cluster "$AWS_ECS_CLUSTER" \
    --query serviceArns \
    --output json
)"
if [[ "$(jq 'length' <<<"$service_arns")" != "0" ]]; then
  echo "PREPARED must contain zero ECS services." >&2
  exit 1
fi

for desired_status in RUNNING PENDING; do
  task_arns="$(
    aws ecs list-tasks \
      --cluster "$AWS_ECS_CLUSTER" \
      --desired-status "$desired_status" \
      --query taskArns \
      --output json
  )"
  if [[ "$(jq 'length' <<<"$task_arns")" != "0" ]]; then
    echo "PREPARED must contain zero $desired_status ECS tasks." >&2
    exit 1
  fi
done

task_definition="$(
  aws ecs describe-task-definition \
    --task-definition "$prepared_task_definition_arn" \
    --query taskDefinition \
    --output json
)"
task_contract="$(
  jq -r \
    --arg container "$AWS_ECS_MIGRATION_CONTAINER_NAME" \
    --arg image "$BACKEND_IMAGE" \
    --arg task_definition_arn "$prepared_task_definition_arn" '
      [.containerDefinitions[]? | select(.name == $container)] as $targets |
      .taskDefinitionArn == $task_definition_arn and
      ($targets | length) == 1 and
      $targets[0].image == $image and
      $targets[0].command == ["python", "-m", "app.db.aws_database_roles", "migrate"]
    ' <<<"$task_definition"
)"
if [[ "$task_contract" != "true" ]]; then
  echo "PREPARED migration task does not use the approved image and exact command." >&2
  exit 1
fi

if [[ -n "${GITHUB_ENV:-}" ]]; then
  printf 'AWS_ECS_MIGRATION_TASK_DEFINITION_ARN=%s\n' \
    "$prepared_task_definition_arn" >> "$GITHUB_ENV"
fi

"$(dirname "$0")/verify_aws_database_bootstrap_absent.sh"

printf 'PREPARED state verified: approved digest, zero services/tasks and no bootstrap plane.\n'
