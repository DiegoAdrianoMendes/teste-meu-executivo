#!/usr/bin/env bash
set -euo pipefail

check_bucket() {
  local bucket="$1"
  local label="$2"
  local public_access policy_status

  public_access=$(aws s3api get-public-access-block \
    --bucket "$bucket" \
    --query PublicAccessBlockConfiguration \
    --output json)
  jq -e '
    .BlockPublicAcls == true and
    .IgnorePublicAcls == true and
    .BlockPublicPolicy == true and
    .RestrictPublicBuckets == true
  ' <<<"$public_access" >/dev/null || {
    echo "Bucket $label precisa bloquear todas as formas de acesso público." >&2
    exit 1
  }

  policy_status=$(aws s3api get-bucket-policy-status \
    --bucket "$bucket" \
    --query PolicyStatus \
    --output json)
  jq -e '.IsPublic == false' <<<"$policy_status" >/dev/null || {
    echo "A policy efetiva do bucket $label não pode ser pública." >&2
    exit 1
  }

  if aws s3api get-bucket-website --bucket "$bucket" >/dev/null 2>&1; then
    echo "Website hosting público precisa estar desativado no bucket $label." >&2
    exit 1
  fi
}

check_response_headers_policy() {
  local policy_id="$1"
  local label="$2"
  local policy

  policy=$(aws cloudfront get-response-headers-policy \
    --id "$policy_id" \
    --query ResponseHeadersPolicy.ResponseHeadersPolicyConfig \
    --output json)
  jq -e '
    .SecurityHeadersConfig as $security |
    ($security.ContentSecurityPolicy.Override == true) and
    ($security.ContentSecurityPolicy.ContentSecurityPolicy | contains("frame-ancestors '\''none'\''")) and
    ($security.ContentSecurityPolicy.ContentSecurityPolicy | contains("object-src '\''none'\''")) and
    ($security.ContentTypeOptions.Override == true) and
    ($security.FrameOptions.Override == true) and
    ($security.FrameOptions.FrameOption == "DENY") and
    ($security.ReferrerPolicy.Override == true) and
    ($security.StrictTransportSecurity.Override == true) and
    ($security.StrictTransportSecurity.AccessControlMaxAgeSec >= 31536000) and
    ($security.StrictTransportSecurity.IncludeSubdomains == true)
  ' <<<"$policy" >/dev/null || {
    echo "Response Headers Policy de $label não contém o baseline de segurança obrigatório." >&2
    exit 1
  }
}

check_distribution() {
  local distribution_id="$1"
  local hostname="$2"
  local expected_bucket="$3"
  local label="$4"
  local config response_headers_policy_id

  config=$(aws cloudfront get-distribution-config \
    --id "$distribution_id" \
    --query DistributionConfig \
    --output json)

  jq -e --arg hostname "$hostname" '
    (.Enabled == true) and
    ((.Aliases.Items // []) | index($hostname) != null) and
    (.DefaultRootObject == "index.html") and
    ((.WebACLId // "") | length > 0) and
    (.DefaultCacheBehavior.ViewerProtocolPolicy == "redirect-to-https") and
    ((.DefaultCacheBehavior.ResponseHeadersPolicyId // "") | length > 0)
  ' <<<"$config" >/dev/null || {
    echo "Distribuição $label não está ativa para $hostname com index.html." >&2
    exit 1
  }

  response_headers_policy_id=$(jq -r '.DefaultCacheBehavior.ResponseHeadersPolicyId' <<<"$config")
  check_response_headers_policy "$response_headers_policy_id" "$label"

  jq -e --arg bucket "$expected_bucket" '
    (.Origins.Quantity == 1) and
    ((.Origins.Items // []) | length == 1) and
    any(
      .Origins.Items[]?;
      (.DomainName | startswith($bucket + ".s3")) and
      ((.OriginAccessControlId // "") | length > 0)
    )
  ' <<<"$config" >/dev/null || {
    echo "Distribuição $label deve usar somente o bucket esperado $expected_bucket." >&2
    exit 1
  }

  jq -e '
    ((.CacheBehaviors.Quantity // 0) == 0) and
    (((.CacheBehaviors.Items // []) | length) == 0)
  ' <<<"$config" >/dev/null || {
    echo "Distribuição $label não pode encaminhar API nem possuir origins dinâmicas; a API usa hostname isolado." >&2
    exit 1
  }
}

check_distribution \
  "$AWS_APP_CLOUDFRONT_DISTRIBUTION_ID" \
  "$APP_HOSTNAME" \
  "$AWS_APP_BUCKET" \
  "App"
check_distribution \
  "$AWS_BLACKBOX_CLOUDFRONT_DISTRIBUTION_ID" \
  "$BLACKBOX_HOSTNAME" \
  "$AWS_BLACKBOX_BUCKET" \
  "Blackbox"

check_bucket "$AWS_APP_BUCKET" "App"
check_bucket "$AWS_BLACKBOX_BUCKET" "Blackbox"

echo "Contrato CloudFront/S3 isolado aprovado."
