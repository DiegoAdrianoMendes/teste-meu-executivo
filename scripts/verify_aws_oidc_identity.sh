#!/usr/bin/env bash
set -euo pipefail

expected_role_arn="${1:?expected IAM role ARN is required}"
if [[ ! "$expected_role_arn" =~ ^arn:([a-z0-9-]+):iam::([0-9]{12}):role/(.+/)?([A-Za-z0-9+=,.@_-]+)$ ]]; then
  echo "Expected OIDC role ARN is invalid." >&2
  exit 1
fi
partition="${BASH_REMATCH[1]}"
expected_account="${BASH_REMATCH[2]}"
expected_role_name="${BASH_REMATCH[4]}"

identity="$(aws sts get-caller-identity --output json)"
identity_valid="$(
  jq -r \
    --arg account "$expected_account" \
    --arg arn_prefix "arn:${partition}:sts::${expected_account}:assumed-role/${expected_role_name}/" '
      .Account == $account and
      (.Arn | type == "string" and startswith($arn_prefix)) and
      ((.Arn | ltrimstr($arn_prefix)) | length > 0) and
      ((.Arn | ltrimstr($arn_prefix)) | contains("/") | not)
    ' <<<"$identity"
)"
if [[ "$identity_valid" != "true" ]]; then
  echo "Active AWS identity is not the expected OIDC assumed role/account." >&2
  exit 1
fi

printf 'Expected AWS OIDC assumed-role identity verified.\n'
