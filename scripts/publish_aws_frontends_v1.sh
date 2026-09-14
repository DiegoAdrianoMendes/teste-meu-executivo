#!/usr/bin/env bash
set -euo pipefail

: "${AWS_APP_BUCKET:?AWS_APP_BUCKET é obrigatório}"
: "${AWS_BLACKBOX_BUCKET:?AWS_BLACKBOX_BUCKET é obrigatório}"
: "${AWS_APP_CLOUDFRONT_DISTRIBUTION_ID:?distribuição App obrigatória}"
: "${AWS_BLACKBOX_CLOUDFRONT_DISTRIBUTION_ID:?distribuição Blackbox obrigatória}"
: "${AWS_FRONTEND_API_ORIGIN:?origem HTTPS da API compilada é obrigatória}"
: "${GITHUB_SHA:?GITHUB_SHA é obrigatório}"

app_dir=${APP_BUILD_DIR:-frontend/dist-app}
blackbox_dir=${BLACKBOX_BUILD_DIR:-frontend/dist-blackbox}
test -f "$app_dir/index.html"
test -f "$blackbox_dir/index.html"

# Bloqueia publicação de um SPA compilado sem a origem da API do ambiente.
# Sem este gate, chamadas como /api/v1/auth/login caem no fallback index.html
# do CloudFront e o navegador tenta interpretar <!doctype> como JSON.
EXPECTED_API_ORIGIN="$AWS_FRONTEND_API_ORIGIN" \
  node frontend/scripts/verify-production-bundle.mjs "$app_dir"
EXPECTED_API_ORIGIN="$AWS_FRONTEND_API_ORIGIN" \
  node frontend/scripts/verify-production-bundle.mjs "$blackbox_dir"

temporary_dir=$(mktemp -d)
app_previous=false
blackbox_previous=false
app_index_published=false
blackbox_index_published=false

cleanup() {
  rm -rf "$temporary_dir"
}

restore_previous_indexes() {
  local failed=$?
  if [[ "$failed" == "0" ]]; then
    cleanup
    return
  fi
  echo "Falha na publicação; restaurando os index.html anteriores." >&2
  if [[ "$app_index_published" == "true" ]]; then
    if [[ "$app_previous" == "true" ]]; then
      aws s3 cp "$temporary_dir/app-index.html" "s3://$AWS_APP_BUCKET/index.html" \
        --cache-control 'no-store' --content-type 'text/html' >/dev/null || true
    else
      aws s3 rm "s3://$AWS_APP_BUCKET/index.html" >/dev/null || true
    fi
  fi
  if [[ "$blackbox_index_published" == "true" ]]; then
    if [[ "$blackbox_previous" == "true" ]]; then
      aws s3 cp "$temporary_dir/blackbox-index.html" "s3://$AWS_BLACKBOX_BUCKET/index.html" \
        --cache-control 'no-store' --content-type 'text/html' >/dev/null || true
    else
      aws s3 rm "s3://$AWS_BLACKBOX_BUCKET/index.html" >/dev/null || true
    fi
  fi
  cleanup
  exit "$failed"
}
trap restore_previous_indexes EXIT

if aws s3 cp "s3://$AWS_APP_BUCKET/index.html" "$temporary_dir/app-index.html" >/dev/null 2>&1; then
  app_previous=true
fi
if aws s3 cp "s3://$AWS_BLACKBOX_BUCKET/index.html" "$temporary_dir/blackbox-index.html" >/dev/null 2>&1; then
  blackbox_previous=true
fi

# Assets têm hash no nome. Não use --delete durante o cutover; lifecycle rules
# removem releases antigos depois da janela de rollback.
aws s3 sync "$app_dir" "s3://$AWS_APP_BUCKET" \
  --exclude index.html \
  --cache-control 'public,max-age=31536000,immutable'
aws s3 sync "$blackbox_dir" "s3://$AWS_BLACKBOX_BUCKET" \
  --exclude index.html \
  --cache-control 'public,max-age=31536000,immutable'

aws s3 cp "$app_dir/index.html" "s3://$AWS_APP_BUCKET/_releases/$GITHUB_SHA/index.html" \
  --cache-control 'no-store' --content-type 'text/html'
aws s3 cp "$blackbox_dir/index.html" "s3://$AWS_BLACKBOX_BUCKET/_releases/$GITHUB_SHA/index.html" \
  --cache-control 'no-store' --content-type 'text/html'

aws s3 cp "$app_dir/index.html" "s3://$AWS_APP_BUCKET/index.html" \
  --cache-control 'no-store' --content-type 'text/html'
app_index_published=true
aws s3 cp "$blackbox_dir/index.html" "s3://$AWS_BLACKBOX_BUCKET/index.html" \
  --cache-control 'no-store' --content-type 'text/html'
blackbox_index_published=true

aws cloudfront create-invalidation \
  --distribution-id "$AWS_APP_CLOUDFRONT_DISTRIBUTION_ID" \
  --paths '/*' >/dev/null
aws cloudfront create-invalidation \
  --distribution-id "$AWS_BLACKBOX_CLOUDFRONT_DISTRIBUTION_ID" \
  --paths '/*' >/dev/null

trap cleanup EXIT
echo "App e Blackbox publicados para o commit $GITHUB_SHA."
