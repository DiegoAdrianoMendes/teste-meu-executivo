#!/usr/bin/env python3
"""Fail closed when the tracked AWS V1 delivery contract becomes unsafe."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> int:
    workflow = read(".github/workflows/deploy.yml")
    compose = read("docker-compose.prod.yml")
    buildspec = read("buildspec.yml")
    settings = read("app/core/config.py")
    nginx = read("docker/frontend/nginx.conf")
    deploy_script = read("scripts/deploy_aws_ecs_v1.sh")
    migration_script = read("scripts/run_aws_ecs_migration_v1.sh")
    candidate_image_gate = read("scripts/verify_candidate_migration_image.sh")
    candidate_evidence_gate = read("scripts/verify_candidate_release_evidence.sh")
    prepared_state_gate = read("scripts/verify_aws_v1_prepared_state.sh")
    oidc_identity_gate = read("scripts/verify_aws_oidc_identity.sh")
    backend_dockerfile = read("Dockerfile")
    migration_role_module = read("app/db/aws_database_roles.py")
    bootstrap_absence_gate = read("scripts/verify_aws_database_bootstrap_absent.sh")
    compatible_image_gate = read("scripts/verify_schema_compatible_app_live.sh")
    greenfield_environment_gate = read("scripts/verify_aws_v1_greenfield_prepare_environment.sh")
    greenfield_mode_gate = read("scripts/verify_greenfield_v1_mode.sh")
    edge_gate = read("scripts/verify_aws_v1_edge_contract.sh")
    frontend_publish = read("scripts/publish_aws_frontends_v1.sh")

    active_delivery = "\n".join(
        (
            workflow,
            compose,
            buildspec,
            deploy_script,
            migration_script,
            candidate_image_gate,
            candidate_evidence_gate,
            prepared_state_gate,
            oidc_identity_gate,
            migration_role_module,
            bootstrap_absence_gate,
            greenfield_environment_gate,
            greenfield_mode_gate,
        )
    )
    for legacy in (
        "/var/www/desburocratize",
        "ghcr.io/kleislm/klm-legal-hub-backend",
        "Deploy VPS",
        "appleboy/ssh-action",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
    ):
        require(legacy not in active_delivery, f"Contrato legado/inseguro ativo: {legacy}")

    require("workflow_dispatch:" in workflow, "Deploy deve ser manual.")
    require(
        "inputs.release_phase == 'qualify_candidate'" in workflow
        and "inputs.confirmation == 'QUALIFY_MEU_EXECUTIVO_V1_GREENFIELD'" in workflow
        and "inputs.release_phase == 'migrate_prepared'" in workflow
        and "inputs.confirmation == 'MIGRATE_MEU_EXECUTIVO_V1_PREPARED'" in workflow,
        "Qualificação e migração PREPARED exigem fases e confirmações distintas.",
    )
    require(
        "github.ref == 'refs/heads/codex/v1-responsive-finance-agents'" in workflow,
        "A preparação staging precisa estar restrita ao branch candidato aprovado.",
    )
    require(
        "DEPLOY_ENVIRONMENT: staging" in workflow,
        "A preparação greenfield não pode apontar nomes ou recursos de produção.",
    )
    require("environment: staging" in workflow, "Environment staging protegido é obrigatório.")
    require("id-token: write" in workflow, "AWS deve autenticar por OIDC.")
    require("fetch-depth: 0" in workflow, "CI precisa inspecionar o histórico Git completo.")
    require(
        "timeout-minutes: 300" in workflow
        and "timeout-minutes: 240" in workflow,
        "O gate integral não pode expirar antes da suíte serial comprovadamente terminar.",
    )
    require(
        "zricethezav/gitleaks:v8.28.0@sha256:cdbb7c955abce02001a9f6c9f602fb195b7fadc1e812065883f695d1eeaba854"
        in workflow,
        "Scan de segredos precisa usar imagem Gitleaks imutável.",
    )
    require(
        "aws-actions/configure-aws-credentials@ff717079ee2060e4bcee96c4779b553acc87447c"
        in workflow,
        "A configuração OIDC da AWS é obrigatória.",
    )
    for action in (
        "actions/checkout",
        "actions/setup-python",
        "actions/setup-node",
        "actions/upload-artifact",
        "actions/download-artifact",
        "aws-actions/configure-aws-credentials",
        "aws-actions/amazon-ecr-login",
    ):
        uses = [line.strip() for line in workflow.splitlines() if f"uses: {action}@" in line]
        require(uses, f"Action obrigatória ausente: {action}")
        require(
            all(len(line.split("@", 1)[1].split()[0]) == 40 for line in uses),
            f"Action não fixada por SHA: {action}",
        )
    require(
        'docker push "$remote_image"' in workflow,
        "Backend precisa publicar a tag de conteúdo do commit.",
    )
    require(
        'candidate_tag="${GITHUB_SHA}-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"' in workflow
        and '--image-ids "imageTag=$candidate_tag"' in workflow
        and "^sha256:[0-9a-f]{64}$" in workflow
        and "@$digest" in workflow,
        "Task definitions precisam consumir o digest ECR resolvido após o push.",
    )
    require('docker logout "$ECR_REGISTRY"' in workflow, "Sessão Docker ECR deve ser encerrada.")
    require(":latest" not in buildspec, "Buildspec não pode publicar tag mutável latest.")
    require(
        "MEU_EXECUTIVO_MIGRATION_ENV_FILE" in compose
        and "MEU_EXECUTIVO_RUNTIME_ENV_FILE" in compose,
        "Fallback precisa separar segredos de migration e runtime.",
    )
    require(
        "MEU_EXECUTIVO_ENV_FILE" not in compose,
        "Fallback não pode compartilhar um env file entre todos os serviços.",
    )
    candidate_job_start = workflow.index("  qualify-candidate:")
    migration_job_start = workflow.index("  migrate-prepared:")
    require(
        candidate_job_start < migration_job_start,
        "Gate 1 da candidata precisa preceder o Gate 2 da migração.",
    )
    candidate_job = workflow[candidate_job_start:migration_job_start]
    migration_job = workflow[migration_job_start:]
    require(
        "needs: quality" in migration_job
        and "needs.qualify-candidate" not in migration_job
        and "run-id: ${{ inputs.candidate_workflow_run_id }}" in migration_job
        and "artifact-ids: ${{ inputs.candidate_artifact_id }}" in migration_job
        and "github-token: ${{ github.token }}" in migration_job,
        "Migração PREPARED precisa repetir o quality gate e consumir explicitamente "
        "o artifact imutável de um Gate 1 anterior.",
    )
    require(
        '--arg workflow_run_id "$CANDIDATE_WORKFLOW_RUN_ID"' in migration_job
        and "(.workflow_run.id | tostring) == $workflow_run_id" in migration_job
        and ".workflow_run.head_sha == $github_sha" in migration_job,
        "Gate 2 precisa vincular artifact, run anterior e SHA candidato.",
    )
    require(
        candidate_job.count("environment: staging") == 1
        and migration_job.count("environment: staging") == 1,
        "Os dois gates humanos da preparação precisam usar o environment staging.",
    )
    for role_variable in (
        "AWS_IMAGE_ROLE_TO_ASSUME",
        "AWS_MIGRATION_ROLE_TO_ASSUME",
        "AWS_RUNTIME_ROLE_TO_ASSUME",
    ):
        require(role_variable in workflow, f"Role OIDC dedicada ausente: {role_variable}")
    require(
        "AWS_FRONTEND_ROLE_TO_ASSUME:" not in workflow,
        "Preparação greenfield não pode assumir role de publicação frontend.",
    )
    require(
        "AWS_ROLE_TO_ASSUME:" not in workflow,
        "Deploy não pode reutilizar uma role OIDC única.",
    )
    require(
        workflow.count("unset-current-credentials: true") >= 4,
        "Cada transição de privilégio AWS precisa substituir credenciais.",
    )
    require(
        candidate_job.index("Configure image credentials through OIDC")
        < candidate_job.index("Log in to Amazon ECR")
        < candidate_job.index("Preserve immutable candidate evidence and stop"),
        "Gate 1 precisa publicar, provar e preservar a candidata antes de parar.",
    )
    first_aws_credentials = candidate_job.index("Configure image credentials through OIDC")
    require(
        candidate_job.index("Build backend image without AWS credentials") < first_aws_credentials,
        "A imagem precisa ser construída antes da primeira role AWS.",
    )
    require(
        "Create pre-migration RDS snapshot" not in candidate_job
        and "run_aws_ecs_migration_v1.sh" not in candidate_job
        and "AWS_MIGRATION_ROLE_TO_ASSUME" not in candidate_job
        and "AWS_RUNTIME_ROLE_TO_ASSUME" not in candidate_job,
        "Gate 1 precisa parar sem snapshot, migration ou roles runtime.",
    )
    require(
        migration_job.index("Download the exact Gate 1 candidate evidence")
        < migration_job.index("Verify candidate evidence without AWS credentials")
        < migration_job.index("Configure migration preflight credentials through OIDC")
        < migration_job.index("Prove PREPARED state before snapshot")
        < migration_job.index("Configure runtime snapshot credentials through OIDC")
        < migration_job.index("Create pre-migration RDS snapshot")
        < migration_job.index("Configure migration execution credentials through OIDC")
        < migration_job.index("Reprove PREPARED state immediately before migration")
        < migration_job.index("Run blocking ECS migration and require private log marker")
        < migration_job.index("Build final migration evidence"),
        "Gate 2 precisa verificar artifact/PREPARED, criar snapshot, reprovar "
        "drift e só então migrar.",
    )
    require(
        "./scripts/verify_aws_database_bootstrap_absent.sh\n"
        "          snapshot_id=" in migration_job,
        "Ausência do bootstrap deve ser provada imediatamente antes do snapshot.",
    )
    require(
        "meu-executivo-${DEPLOY_ENVIRONMENT}-predeploy-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"
        in workflow,
        "Prefixo do snapshot precisa convergir com a policy da RuntimeRole.",
    )
    for forbidden_greenfield_action in (
        "./scripts/deploy_aws_ecs_v1.sh",
        "./scripts/publish_aws_frontends_v1.sh",
        "Smoke isolated production surfaces",
        "AWS_ECS_SERVICE_TARGETS:",
        "KLM_PREVIOUS_APP_REF:",
    ):
        require(
            forbidden_greenfield_action not in workflow,
            f"Preparação greenfield não pode executar: {forbidden_greenfield_action}",
        )
    for candidate_evidence_marker in (
        "schemaVersion: 1",
        'evidenceType: "candidate"',
        "cleanCheckout: $clean_checkout",
        "githubSha: $github_sha",
        "ociRevisionLabel: $candidate_image_contract[0].ociRevisionLabel",
        "runtimeImageDigest: $runtime_image_digest",
        "alembicHeads: [$candidate_image_contract[0].alembicHead]",
        "releaseContractPassed: true",
        "postHeadCatalogAuditRequired: true",
        "postHeadContractMarkerRequired:",
        "workflowRunId: $workflow_run_id",
        'releaseMode: "greenfield-prepare"',
    ):
        require(
            candidate_evidence_marker in candidate_job,
            f"Evidência da candidata ausente: {candidate_evidence_marker}",
        )
    require(
        "candidate-release-evidence.json.sha256" in candidate_job
        and "steps.candidate-artifact.outputs.artifact-digest" in candidate_job
        and "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093" in migration_job,
        "Artifact da candidata precisa de checksum, digest e download fixado por SHA.",
    )
    require(
        '--build-arg "SOURCE_REVISION=$GITHUB_SHA"' in workflow
        and "verify_candidate_migration_image.sh" in workflow
        and 'docker pull "$backend_image"' in workflow,
        "Imagem ECR por digest deve provar o SHA OCI e o contrato de migration.",
    )
    for final_evidence_marker in (
        'evidenceType: "migration"',
        'runtimeStateObserved: "PREPARED"',
        "postHeadCatalogAudit: $catalog_audit_passed",
        "postHeadContractMarkerObserved: $marker_observed",
        "candidateArtifactDigest: $candidate_artifact_digest",
        "candidateArtifactId: $candidate_artifact_id",
        "candidateEvidenceSha256: $candidate_evidence_sha256",
        "rdsSnapshotStatusObserved: $rds_snapshot_status",
        "runtimeEnabled: false",
        "greenfield-migration-evidence.json.sha256",
        "steps.migration-artifact.outputs.artifact-digest",
    ):
        require(
            final_evidence_marker in migration_job,
            f"Evidência final da migration ausente: {final_evidence_marker}",
        )
    for image_contract_marker in (
        'org.opencontainers.image.revision="${SOURCE_REVISION}"',
        'io.meuexecutivo.database.alembic-head="c9d0e1f2a3b4"',
        'io.meuexecutivo.database.contract="resolver-owner-v1"',
    ):
        require(
            image_contract_marker in backend_dockerfile,
            f"Label OCI ausente: {image_contract_marker}",
        )
    for candidate_gate_marker in (
        "docker image inspect",
        "--network none",
        "f2c4d6e8a031_harden_tenant_resolver_owner_access.py",
        "a3d5e7f9b142_add_whatsapp_template_webhook_resolver.py",
        "b4e6f8a0c253_enforce_platform_owner_exclusivity.py",
        "c5f7a9b1d304_harden_whatsapp_webhook_processing.py",
        "d6a8c0e2f415_expand_commercial_workflow_rls.py",
        "e7b9d1f3a526_add_contact_stock_entitlement.py",
        "e8c0a2d4f617_widen_auth_rate_limit_event_scope.py",
        "wx5q8m1n2j64_add_task_external_shares.py",
        "xy6r9n2p3k75_link_tasks_to_flow_runs.py",
        "yz7s0p4q5l86_add_canonical_client_contracts.py",
        "za8t1q4r5m97_add_task_organization.py",
        "zb9u2r5s6n08_enforce_client_contracts_rls.py",
        "zc0v3s6t7p19_track_meta_token_expiration.py",
        "zd1w4t7u8q20_add_commercial_account_requests.py",
        "ze2x5u8v9r31_add_canonical_commercial_stripe_bridge.py",
        "zf3y6v9w0s42_add_meta_webhook_control_plane.py",
        "zg4z7w0x1t53_align_meta_webhook_rls_policy_names.py",
        "8f3c1a9d2b70_merge_v1_finance_agents.py",
        "jk2e5f6a8b31_add_finance_tax_reform_alerts.py",
        "_verify_resolver_owner_contract",
        "_verify_exact_runtime_dml",
        "Database migration and post-head runtime grant contract completed.",
    ):
        require(
            candidate_gate_marker in candidate_image_gate,
            f"Probe da imagem candidata incompleto: {candidate_gate_marker}",
        )
    require(
        "KLM_RELEASE_MODE: greenfield-prepare" in workflow
        and "verify_aws_v1_candidate_environment.sh" in candidate_job
        and "verify_aws_v1_migrate_prepared_environment.sh" in migration_job,
        "Cada fase precisa de gate de ambiente greenfield específico.",
    )
    for prepared_marker in (
        "aws cloudformation describe-stacks",
        'parameter("RuntimeState") == ["PREPARED"]',
        'parameter("RuntimeImageDigest") == [$expected_digest]',
        "MigrationTaskDefinitionArn",
        '--task-definition "$prepared_task_definition_arn"',
        ".taskDefinitionArn == $task_definition_arn",
        'StackStatus == "CREATE_COMPLETE"',
        'StackStatus == "UPDATE_COMPLETE"',
        "aws ecs list-services",
        "RUNNING PENDING",
        "verify_aws_database_bootstrap_absent.sh",
    ):
        require(
            prepared_marker in prepared_state_gate,
            f"Gate PREPARED incompleto: {prepared_marker}",
        )
    require(
        '(has("postHeadCatalogAudit") | not)' in candidate_evidence_gate
        and '(has("postHeadContractMarker") | not)' in candidate_evidence_gate
        and "EXPECTED_CANDIDATE_EVIDENCE_SHA256" in candidate_evidence_gate,
        "Artifact da candidata não pode alegar auditoria ou marker executados.",
    )
    require(
        "candidate_artifact_id" in workflow
        and "candidate_artifact_name" in workflow
        and "candidate_evidence_sha256" in workflow
        and "actions/artifacts/$CANDIDATE_ARTIFACT_ID" in workflow
        and ".workflow_run.head_sha == $github_sha" in workflow
        and "artifact-ids: ${{ inputs.candidate_artifact_id }}" in workflow
        and "run-id: ${{ inputs.candidate_workflow_run_id }}" in workflow,
        "Gate 2 precisa vincular metadata/id/digest/head SHA e checksum externo do Gate 1.",
    )
    require(
        workflow.count("verify_aws_oidc_identity.sh") >= 4
        and "aws sts get-caller-identity" in oidc_identity_gate
        and "assumed-role" in oidc_identity_gate,
        "Cada role AWS ativa precisa ter identidade STS fail-closed.",
    )
    require(
        "a671ed13e0b67d000cb726d70d3d4fee9e446bbe" in greenfield_mode_gate,
        "Gate greenfield precisa negar a revisão legada incompatível pelo SHA completo.",
    )

    require(
        "AUTH_REFRESH_COOKIE_DOMAIN deve permanecer vazio em produção" in settings,
        "Cookie host-only precisa ser obrigatório.",
    )
    require(
        "O aplicativo e a Blackbox devem usar hostnames distintos" in settings,
        "Hostnames distintos precisam ser obrigatórios no backend.",
    )
    require(
        "platform_identity and settings.is_production" in read("app/api/v1/endpoints/auth.py"),
        "MFA de plataforma ausente.",
    )

    require("frame-ancestors 'none'" in nginx, "CSP anti-frame ausente.")
    require("Cross-Origin-Opener-Policy" in nginx, "COOP ausente.")
    require("Cross-Origin-Resource-Policy" in nginx, "CORP ausente.")
    require("Strict-Transport-Security" in nginx, "HSTS ausente.")
    require("include /etc/nginx/surface-routes.conf" in nginx, "Rotas por superfície ausentes.")
    require(
        "location = /blackbox { return 404; }" in read("Dockerfile.frontend"),
        "App precisa negar Blackbox.",
    )
    require(
        "location / { return 404; }" in read("Dockerfile.frontend"),
        "Blackbox precisa negar App.",
    )

    require(
        "aws stepfunctions start-execution" in migration_script,
        "Migração ECS bloqueante via Step Functions ausente.",
    )
    require(
        "aws stepfunctions describe-execution" in migration_script,
        "Migração Step Functions deve ser aguardada.",
    )
    require(".migration.exitCode == 0" in migration_script, "Exit code não validado.")
    require(
        "A task PREPARED não corresponde ao ARN, imagem e comando aprovados" in migration_script
        and '["python", "-m", "app.db.aws_database_roles", "migrate"]' in migration_script,
        "Task de migration precisa usar o comando contratual exato.",
    )
    require(
        "{correlationId: $correlation_id}" in migration_script
        and "taskDefinitionArn"
        not in migration_script.split("migration_input=", 1)[1].split(")", 1)[0],
        "State machine deve escolher a task PREPARED; o job envia somente correlação.",
    )
    require(
        "aws ecs run-task" not in migration_script
        and "--network-configuration" not in migration_script
        and "aws ecs register-task-definition" not in migration_script
        and "aws ecs tag-resource" not in migration_script
        and "iam:PassRole" not in migration_script,
        "OIDC não pode executar, registrar, selecionar rede ou passar role da migration.",
    )
    for required_tag in (
        "key=Project,value=meu-executivo",
        "key=Environment,value=$DEPLOY_ENVIRONMENT",
        "key=SourceRevision,value=$GITHUB_SHA",
    ):
        require(
            required_tag in deploy_script,
            f"Tag obrigatória ausente no registro ECS: {required_tag}",
        )
    require(
        "key=ReleaseChannel,value=runtime" in deploy_script,
        "ReleaseChannel runtime precisa estar explícito no update compatível futuro.",
    )
    require(
        "verify_aws_database_bootstrap_absent.sh" in migration_script,
        "Ausência do bootstrap efêmero deve bloquear a migration.",
    )
    require(
        migration_script.count("verify_aws_database_bootstrap_absent.sh") >= 2
        and migration_script.index("aws ecs describe-task-definition")
        < migration_script.rindex("verify_aws_database_bootstrap_absent.sh")
        < migration_script.index("aws stepfunctions start-execution"),
        "Bootstrap precisa ser revalidado após validar a task PREPARED e antes da execução.",
    )
    require(
        "aws logs filter-log-events" in migration_script
        and '--log-group-name "$AWS_MIGRATION_LOG_GROUP_NAME"' in migration_script
        and "ResourceNotFoundException" in migration_script
        and "AccessDenied"
        not in migration_script.split('case "$marker_error"', 1)[1].split("esac", 1)[0]
        and "contract_marker_observed=true" in migration_script,
        "Marker privado pós-head precisa ser observado com retry transitório e falha fechada.",
    )
    require(
        '["alembic", "upgrade", "head"]' in migration_role_module
        and "reconcile_runtime_privileges_as_migrator(database_url, expected_role)"
        in migration_role_module,
        "Migration task precisa alcançar head e reconciliar grants como migrator.",
    )
    for post_head_marker in (
        "_verify_resolver_owner_contract(",
        "_verify_exact_runtime_dml(",
        "Could not identify exactly one safe runtime role from resolver ACLs",
        "Resolver-owner SELECT policies or FORCE RLS state drifted",
        '_GREENFIELD_HEAD_REVISION = "c9d0e1f2a3b4"',
        "Database migration and post-head runtime grant contract completed.",
    ):
        require(
            post_head_marker in migration_role_module,
            f"Auditoria pós-head incompleta: {post_head_marker}",
        )
    forward_update = deploy_script.index(
        "aws ecs update-service",
        deploy_script.index("while IFS= read -r target"),
    )
    require(
        'KLM_RELEASE_MODE" != "compatible-update' in deploy_script
        and 'KLM_PREVIOUS_SCHEMA_CONTRACT" != "resolver-owner-v1' in deploy_script
        and "a671ed1 não é compatível" in deploy_script,
        "Rollout futuro precisa negar greenfield e binário legado explicitamente.",
    )
    require(
        deploy_script.count("verify_bootstrap_absent") >= 3
        and deploy_script.index("if ! verify_bootstrap_absent")
        < deploy_script.index("verify_schema_compatible_app_live.sh")
        < forward_update,
        "Update futuro deve provar ausência do bootstrap antes de cada mutação.",
    )
    for script_text in (migration_script, deploy_script):
        require(
            "@sha256:[0-9a-f]{64}" in script_text,
            "Migration e rollout devem rejeitar imagens referenciadas apenas por tag.",
        )
    for error_code in (
        "ParameterNotFound",
        "NoSuchEntity",
        "StateMachineDoesNotExist",
    ):
        require(error_code in bootstrap_absence_gate, f"Gate de ausência incompleto: {error_code}")
    for bootstrap_absence_marker in (
        "ecs list-task-definitions",
        "--status ACTIVE",
        "ecs list-tasks",
        '--desired-status "$desired_status"',
        "RUNNING PENDING",
        "ec2 describe-security-groups",
        "Name=tag:Project,Values=meu-executivo",
        "Name=tag:Environment,Values=${DEPLOY_ENVIRONMENT}",
        "Name=tag:Ephemeral,Values=database-bootstrap",
    ):
        require(
            bootstrap_absence_marker in bootstrap_absence_gate,
            f"Gate de ausência não cobre: {bootstrap_absence_marker}",
        )
    require(
        deploy_script.index("verify_schema_compatible_app_live.sh") < forward_update,
        "Task schema-compatível precisa ser provada antes do primeiro update-service futuro.",
    )
    require(
        "/health" in compatible_image_gate
        and "/api/v1/status" in compatible_image_gate
        and "resolver-owner-v1" in compatible_image_gate
        and "KLM_PREVIOUS_APP_REF" in compatible_image_gate,
        "Gate live futuro não separa task compatível de binário legado.",
    )
    require(
        "restore_compatible_services" in deploy_script,
        "Restauração de task definitions schema-compatíveis ausente.",
    )
    require(
        "trap 'abort_compatible_update 130' INT" in deploy_script
        and "trap 'abort_compatible_update 143' TERM" in deploy_script
        and "RESTAURAÇÃO ECS INCOMPLETA" in deploy_script,
        "ERR/INT/TERM devem executar restauração verificável e falhar fechado.",
    )
    require("aws ecs wait services-stable" in deploy_script, "Estabilidade ECS não validada.")
    destructive_sync = any(
        "--delete" in line and not line.lstrip().startswith("#")
        for line in frontend_publish.splitlines()
    )
    require(not destructive_sync, "Cutover não pode apagar assets ativos.")
    require("restore_previous_indexes" in frontend_publish, "Rollback de index ausente.")
    require("_releases/$GITHUB_SHA" in frontend_publish, "Release frontend versionado ausente.")
    require(
        "não pode encaminhar API" in edge_gate,
        "CloudFront dos frontends deve permanecer somente S3, sem proxy da API.",
    )
    require("get-public-access-block" in edge_gate, "Public Access Block do S3 não validado.")
    require("get-bucket-policy-status" in edge_gate, "Policy pública do S3 não validada.")
    require("get-bucket-website" in edge_gate, "Website hosting do S3 não validado.")
    require("get-response-headers-policy" in edge_gate, "Headers de segurança não validados.")
    require("WebACLId" in edge_gate, "WAF do CloudFront não validado.")

    require(
        not (ROOT / "deploy/.env.production").exists(),
        "deploy/.env.production não pode existir no repositório.",
    )
    require(
        (ROOT / "deploy/.env.production.example").is_file(),
        "Template de produção sanitizado ausente.",
    )

    if raw_targets := os.environ.get("AWS_ECS_SERVICE_TARGETS"):
        targets = json.loads(raw_targets)
        require(isinstance(targets, list), "AWS_ECS_SERVICE_TARGETS deve ser lista JSON.")

    print("AWS V1 release contract verified.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"AWS V1 release contract failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
