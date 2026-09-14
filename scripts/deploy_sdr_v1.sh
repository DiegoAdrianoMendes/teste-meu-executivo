#!/usr/bin/env bash
set -euo pipefail

cat >&2 <<'EOF'
BLOCKED: this legacy SDR deploy targeted infrastructure that does not belong to
the approved Meu Executivo V1 release path. It is intentionally fail-closed.

Use the reviewed GitHub Actions OIDC workflow only after the local, staging and
human approval gates are complete. This script performs no AWS operation.
EOF
exit 64
