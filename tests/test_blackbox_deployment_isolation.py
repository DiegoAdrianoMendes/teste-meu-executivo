"""Contratos estáticos que impedem mistura entre app e Blackbox no deploy."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_nginx_receives_surface_specific_route_denials() -> None:
    dockerfile = _read("Dockerfile.frontend")
    nginx = _read("docker/frontend/nginx.conf")

    assert "location = /blackbox { return 404; }" in dockerfile
    assert "location ^~ /blackbox/ { return 404; }" in dockerfile
    assert "location = /index.html { internal; }" in dockerfile
    assert "location / { return 404; }" in dockerfile
    assert "include /etc/nginx/surface-routes.conf;" in nginx
    assert "surface-deny.conf" not in nginx


def test_production_uses_distinct_images_hosts_and_negative_smokes() -> None:
    compose = _read("docker-compose.prod.yml")
    workflow = _read(".github/workflows/deploy.yml")
    playwright = _read("frontend/playwright.config.ts")
    publisher = _read("scripts/publish_aws_frontends_v1.sh")
    environment_gate = _read("scripts/verify_aws_v1_deploy_environment.sh")

    assert "FRONTEND_APP_IMAGE" in compose
    assert "FRONTEND_BLACKBOX_IMAGE" in compose
    assert "APP_HOSTNAME" in compose
    assert "BLACKBOX_HOSTNAME" in compose
    assert 'KLM_E2E_PRODUCTION: "1"' in workflow
    assert "npm run test:e2e" in workflow
    assert "npm run build:app" in playwright
    assert "npm run build:blackbox" in playwright
    assert "frontend/dist-app" in publisher
    assert "frontend/dist-blackbox" in publisher
    assert "AWS_APP_BUCKET" in publisher
    assert "AWS_BLACKBOX_BUCKET" in publisher
    assert 'APP_HOSTNAME" != "app.meuexecutivo.com"' in environment_gate
    assert 'BLACKBOX_HOSTNAME" != "blackbox.meuexecutivo.com"' in environment_gate
    assert "./scripts/publish_aws_frontends_v1.sh" not in workflow
    assert "stop before ENABLED" in workflow


def test_staging_candidate_qualification_is_manual_oidc_and_does_not_touch_public_site() -> None:
    workflow = _read(".github/workflows/deploy.yml")
    environment_gate = _read("scripts/verify_aws_v1_deploy_environment.sh")

    assert "workflow_dispatch:" in workflow
    assert "qualify-candidate:" in workflow
    assert "QUALIFY_MEU_EXECUTIVO_V1_GREENFIELD" in workflow
    assert "github.ref == 'refs/heads/codex/v1-responsive-finance-agents'" in workflow
    assert "environment: staging" in workflow
    assert "DEPLOY_ENVIRONMENT: staging" in workflow
    assert "id-token: write" in workflow
    assert (
        "aws-actions/configure-aws-credentials@"
        "ff717079ee2060e4bcee96c4779b553acc87447c"
    ) in workflow
    assert "AWS_ACCESS_KEY_ID" not in workflow
    assert "AWS_SECRET_ACCESS_KEY" not in workflow
    assert "./scripts/publish_aws_frontends_v1.sh" not in workflow
    assert "AWS_PUBLIC_SITE_BUCKET" in environment_gate
    assert "AWS_PUBLIC_SITE_CLOUDFRONT_DISTRIBUTION_ID" in environment_gate


def test_edge_contract_requires_isolated_api_and_security_headers() -> None:
    edge_gate = _read("scripts/verify_aws_v1_edge_contract.sh")
    nginx = _read("docker/frontend/nginx.conf")

    assert "não pode encaminhar API" in edge_gate
    assert "(.Origins.Quantity == 1)" in edge_gate
    assert "OriginAccessControlId" in edge_gate
    assert "ResponseHeadersPolicyId" in edge_gate
    assert 'Cross-Origin-Opener-Policy "same-origin"' in nginx
    assert 'Cross-Origin-Resource-Policy "same-origin"' in nginx
    assert "Strict-Transport-Security" in nginx


def test_blackbox_login_requires_real_mfa_in_production() -> None:
    auth_endpoint = _read("app/api/v1/endpoints/auth.py")
    mfa_service = _read("app/services/mfa_service.py")

    assert "platform_identity and settings.is_production" in auth_endpoint
    assert "AUTH-BLACKBOX-MFA-ENROLLMENT-REQUIRED" in auth_endpoint
    assert "_create_totp_challenge" in mfa_service
    assert "IntegrationSecretCipher" in mfa_service
