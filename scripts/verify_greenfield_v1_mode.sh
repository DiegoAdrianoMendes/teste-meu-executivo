#!/usr/bin/env bash
set -euo pipefail

release_mode="${KLM_RELEASE_MODE:-}"
incompatible_revision="a671ed13e0b67d000cb726d70d3d4fee9e446bbe"
if [[ "$release_mode" != "greenfield-prepare" ]]; then
  printf 'KLM_RELEASE_MODE must be greenfield-prepare for the initial V1 gate.\n' >&2
  exit 1
fi

if [[ "${GITHUB_SHA:-}" == "$incompatible_revision" ]]; then
  printf '%s\n' \
    'GITHUB_SHA is the incompatible a671ed1 legacy revision; greenfield preparation is blocked.' >&2
  exit 1
fi

if [[ -n "${KLM_PREVIOUS_APP_REF:-}" ]]; then
  printf '%s\n' \
    'KLM_PREVIOUS_APP_REF is forbidden: a671ed1 is not compatible with the strict resolver-owner contract.' >&2
  exit 1
fi

if [[ -n "${AWS_ECS_SERVICE_TARGETS:-}" ]]; then
  printf '%s\n' \
    'AWS_ECS_SERVICE_TARGETS is forbidden in greenfield-prepare; RuntimeState must remain PREPARED.' >&2
  exit 1
fi

printf '%s\n' \
  'Greenfield V1 mode approved: no legacy binary rollback, ECS service mutation, frontend publication or DNS cutover.'
