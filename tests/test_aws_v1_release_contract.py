from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
IMMUTABLE_BACKEND_IMAGE = (
    "registry.example/meu-executivo@sha256:"
    "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
)
CANDIDATE_IMAGE_GATE = ROOT / "scripts/verify_candidate_migration_image.sh"


def _candidate_image_probe_source() -> str:
    content = CANDIDATE_IMAGE_GATE.read_text(encoding="utf-8")
    start_marker = "    -c '\n"
    end_marker = "\n'\n)\""
    start = content.index(start_marker) + len(start_marker)
    end = content.index(end_marker, start)
    return content[start:end].replace("/app/", f"{ROOT}/")


def _deploy_environment() -> dict[str, str]:
    return {
        **os.environ,
        "AWS_REGION": "us-east-1",
        "DEPLOY_ENVIRONMENT": "production",
        "AWS_IMAGE_ROLE_TO_ASSUME": "arn:aws:iam::123456789012:role/image",
        "AWS_MIGRATION_ROLE_TO_ASSUME": "arn:aws:iam::123456789012:role/migration",
        "AWS_RUNTIME_ROLE_TO_ASSUME": "arn:aws:iam::123456789012:role/runtime",
        "AWS_FRONTEND_ROLE_TO_ASSUME": "arn:aws:iam::123456789012:role/frontend",
        "AWS_ECR_REPOSITORY": "meu-executivo-api",
        "AWS_ECS_CLUSTER": "meu-executivo",
        "AWS_ECS_SERVICE_TARGETS": json.dumps(
            [
                {"role": "api", "service": "api", "container": "api"},
                {"role": "worker", "service": "worker", "container": "worker"},
                {"role": "beat", "service": "beat", "container": "beat"},
            ]
        ),
        "AWS_ECS_MIGRATION_TASK_FAMILY": "migrations",
        "AWS_ECS_MIGRATION_TASK_DEFINITION_ARN": (
            "arn:aws:ecs:us-east-1:123456789012:task-definition/migrations:1"
        ),
        "AWS_ECS_MIGRATION_CONTAINER_NAME": "migrate",
        "AWS_ECS_LAUNCH_TYPE": "FARGATE",
        "AWS_SFN_MIGRATION_STATE_MACHINE_ARN": (
            "arn:aws:states:us-east-1:123456789012:stateMachine:migrations"
        ),
        "AWS_MIGRATION_LOG_GROUP_NAME": "/meu-executivo/production/migration",
        "AWS_RDS_DB_IDENTIFIER": "database",
        "AWS_APPLICATION_STACK_NAME": "meu-executivo-production",
        "AWS_APP_BUCKET": "app-bucket",
        "AWS_BLACKBOX_BUCKET": "blackbox-bucket",
        "AWS_PUBLIC_SITE_BUCKET": "public-site-bucket",
        "AWS_APP_CLOUDFRONT_DISTRIBUTION_ID": "APP123",
        "AWS_BLACKBOX_CLOUDFRONT_DISTRIBUTION_ID": "BOX123",
        "AWS_PUBLIC_SITE_CLOUDFRONT_DISTRIBUTION_ID": "SITE123",
        "APP_HOSTNAME": "app.meuexecutivo.com",
        "BLACKBOX_HOSTNAME": "blackbox.meuexecutivo.com",
        "API_HOSTNAME": "api.meuexecutivo.com",
        "GITHUB_SHA": "0123456789abcdef0123456789abcdef01234567",
        "KLM_RELEASE_MODE": "compatible-update",
        "KLM_PREVIOUS_SCHEMA_CONTRACT": "resolver-owner-v1",
    }


def _greenfield_environment() -> dict[str, str]:
    environment = _deploy_environment()
    environment["DEPLOY_ENVIRONMENT"] = "staging"
    environment["KLM_RELEASE_MODE"] = "greenfield-prepare"
    environment.pop("KLM_PREVIOUS_SCHEMA_CONTRACT")
    environment.pop("AWS_ECS_SERVICE_TARGETS")
    environment.pop("AWS_ECS_MIGRATION_TASK_DEFINITION_ARN")
    return environment


def _write_fake_edge_aws(path: Path) -> None:
    path.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys

args = sys.argv[1:]
operation = " ".join(args[:2])

def value(flag):
    return args[args.index(flag) + 1]

if operation == "cloudfront get-distribution-config":
    distribution_id = value("--id")
    if distribution_id == "APP123":
        hostname, bucket = "app.meuexecutivo.com", "app-bucket"
    else:
        hostname, bucket = "blackbox.meuexecutivo.com", "blackbox-bucket"
    origins = [{
        "Id": "static",
        "DomainName": f"{bucket}.s3.amazonaws.com",
        "OriginAccessControlId": "OAC123",
    }]
    cache_behaviors = {"Quantity": 0, "Items": []}
    if os.environ.get("FAKE_ADD_API_ORIGIN") == "1":
        origins.append({
            "Id": "api",
            "DomainName": "api.meuexecutivo.com",
            "OriginAccessControlId": "",
        })
        cache_behaviors = {
            "Quantity": 1,
            "Items": [{
                "PathPattern": "/api/*",
                "ViewerProtocolPolicy": "redirect-to-https",
                "AllowedMethods": {"Quantity": 7},
                "TargetOriginId": "api",
            }],
        }
    print(json.dumps({
        "Enabled": True,
        "Aliases": {"Items": [hostname]},
        "DefaultRootObject": "index.html",
        "WebACLId": "arn:aws:wafv2:us-east-1:123456789012:global/webacl/v1",
        "Origins": {"Quantity": len(origins), "Items": origins},
        "DefaultCacheBehavior": {
            "ViewerProtocolPolicy": "redirect-to-https",
            "ResponseHeadersPolicyId": "HEADERS123",
        },
        "CacheBehaviors": cache_behaviors,
    }))
elif operation == "cloudfront get-response-headers-policy":
    print(json.dumps({"SecurityHeadersConfig": {
        "ContentSecurityPolicy": {
            "Override": True,
            "ContentSecurityPolicy": (
                "default-src 'self'; frame-ancestors 'none'; object-src 'none'"
            ),
        },
        "ContentTypeOptions": {"Override": True},
        "FrameOptions": {"Override": True, "FrameOption": "DENY"},
        "ReferrerPolicy": {"Override": True, "ReferrerPolicy": "no-referrer"},
        "StrictTransportSecurity": {
            "Override": True,
            "AccessControlMaxAgeSec": 31536000,
            "IncludeSubdomains": True,
        },
    }}))
elif operation == "s3api get-public-access-block":
    print(json.dumps({
        "BlockPublicAcls": True,
        "IgnorePublicAcls": True,
        "BlockPublicPolicy": True,
        "RestrictPublicBuckets": True,
    }))
elif operation == "s3api get-bucket-policy-status":
    print(json.dumps({"IsPublic": False}))
elif operation == "s3api get-bucket-website":
    raise SystemExit(255)
else:
    raise SystemExit("unsupported fake aws command: " + operation)
""",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _write_fake_rollout_aws(path: Path) -> None:
    path.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
operation = " ".join(args[:2])
log_path = Path(os.environ["AWS_FAKE_LOG"])
log_path.open("a", encoding="utf-8").write(" ".join(args) + "\\n")

def value(flag):
    return args[args.index(flag) + 1]

if operation == "ssm get-parameter":
    counter_path = Path(os.environ["AWS_FAKE_SSM_COUNTER"])
    count = int(counter_path.read_text() or "0") + 1 if counter_path.exists() else 1
    counter_path.write_text(str(count))
    if count >= int(os.environ["AWS_FAKE_BOOTSTRAP_AFTER"]):
        print(json.dumps({"Parameter": {"Name": value("--name")}}))
    else:
        print(
            "An error occurred (ParameterNotFound) when calling the GetParameter operation",
            file=sys.stderr,
        )
        raise SystemExit(255)
elif operation == "iam get-role":
    print("An error occurred (NoSuchEntity) when calling GetRole", file=sys.stderr)
    raise SystemExit(255)
elif operation == "stepfunctions describe-state-machine":
    print(
        "An error occurred (StateMachineDoesNotExist) when calling DescribeStateMachine",
        file=sys.stderr,
    )
    raise SystemExit(255)
elif operation in {"ecs list-task-definitions", "ecs list-tasks"}:
    print("[]")
elif operation == "ec2 describe-security-groups":
    print("[]")
elif operation == "ecs describe-services":
    service = value("--services")
    print(f"arn:aws:ecs:region:account:task-definition/{service}:1")
elif operation == "ecs describe-task-definition":
    task = value("--task-definition").split("/")[-1].split(":")[0]
    print(json.dumps({
        "family": task,
        "networkMode": "awsvpc",
        "requiresCompatibilities": ["FARGATE"],
        "cpu": "256",
        "memory": "512",
        "containerDefinitions": [{"name": task, "image": "old-image"}],
    }))
elif operation == "ecs register-task-definition":
    definition = json.loads(Path(value("--cli-input-json").removeprefix("file://")).read_text())
    print(f"arn:aws:ecs:region:account:task-definition/{definition['family']}:2")
elif operation in {"ecs update-service", "ecs wait"}:
    pass
else:
    raise SystemExit("unsupported fake aws command: " + operation)
""",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _write_fake_previous_app_curl(path: Path) -> None:
    path.write_text(
        """#!/usr/bin/env python3
import json
import sys

if sys.argv[-1].endswith("/health"):
    print(json.dumps({"status": "ok", "app": "Meu Executivo"}))
elif sys.argv[-1].endswith("/api/v1/status"):
    print(json.dumps({"components": {"database": {"status": "operational"}}}))
else:
    raise SystemExit("unsupported URL")
""",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _write_fake_bootstrap_absence_aws(path: Path) -> None:
    path.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys

args = sys.argv[1:]
operation = " ".join(args[:2])
present = os.environ.get("FAKE_PRESENT_BOOTSTRAP", "")

def value(flag):
    return args[args.index(flag) + 1]

if operation == "ssm get-parameter":
    print("An error occurred (ParameterNotFound) when calling GetParameter", file=sys.stderr)
    raise SystemExit(255)
if operation == "iam get-role":
    print("An error occurred (NoSuchEntity) when calling GetRole", file=sys.stderr)
    raise SystemExit(255)
if operation == "stepfunctions describe-state-machine":
    print(
        "An error occurred (StateMachineDoesNotExist) when calling DescribeStateMachine",
        file=sys.stderr,
    )
    raise SystemExit(255)
if operation == "ecs list-task-definitions":
    print(json.dumps(["arn:bootstrap:task-definition"] if present == "task-definition" else []))
    raise SystemExit(0)
if operation == "ecs list-tasks":
    desired = value("--desired-status").lower()
    print(json.dumps(["arn:bootstrap:task"] if present == desired else []))
    raise SystemExit(0)
if operation == "ec2 describe-security-groups":
    print(json.dumps([{"GroupId": "sg-bootstrap"}] if present == "security-group" else []))
    raise SystemExit(0)
raise SystemExit("unsupported fake aws command: " + operation)
""",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _write_fake_prepared_state_aws(path: Path) -> None:
    path.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys

args = sys.argv[1:]
operation = " ".join(args[:2])
scenario = os.environ.get("FAKE_PREPARED_SCENARIO", "ok")

def value(flag):
    return args[args.index(flag) + 1]

task_arn = "arn:aws:ecs:us-east-1:123456789012:task-definition/migrations:7"
if operation == "cloudformation describe-stacks":
    print(json.dumps({
        "StackStatus": (
            "UPDATE_ROLLBACK_COMPLETE" if scenario == "rollback" else "UPDATE_COMPLETE"
        ),
        "Parameters": [
            {"ParameterKey": "RuntimeState", "ParameterValue": "PREPARED"},
            {
                "ParameterKey": "RuntimeImageDigest",
                "ParameterValue": os.environ["BACKEND_IMAGE"].split("@", 1)[1],
            },
        ],
        "Outputs": [
            {"OutputKey": "MigrationTaskDefinitionArn", "OutputValue": task_arn},
            {
                "OutputKey": "MigrationStateMachineArn",
                "OutputValue": os.environ["AWS_SFN_MIGRATION_STATE_MACHINE_ARN"],
            },
            {
                "OutputKey": "MigrationLogGroupName",
                "OutputValue": os.environ["AWS_MIGRATION_LOG_GROUP_NAME"],
            },
        ],
    }))
elif operation == "ecs list-services":
    print("[]")
elif operation == "ecs list-tasks":
    print("[]")
elif operation == "ecs describe-task-definition":
    assert value("--task-definition") == task_arn
    described_arn = (
        "arn:aws:ecs:us-east-1:123456789012:task-definition/migrations:8"
        if scenario == "revision-drift" else task_arn
    )
    print(json.dumps({
        "taskDefinitionArn": described_arn,
        "family": "migrations",
        "containerDefinitions": [{
            "name": "migrate",
            "image": os.environ["BACKEND_IMAGE"],
            "command": ["python", "-m", "app.db.aws_database_roles", "migrate"],
        }],
    }))
elif operation == "ssm get-parameter":
    print("An error occurred (ParameterNotFound) when calling GetParameter", file=sys.stderr)
    raise SystemExit(255)
elif operation == "iam get-role":
    print("An error occurred (NoSuchEntity) when calling GetRole", file=sys.stderr)
    raise SystemExit(255)
elif operation == "stepfunctions describe-state-machine":
    print(
        "An error occurred (StateMachineDoesNotExist) when calling DescribeStateMachine",
        file=sys.stderr,
    )
    raise SystemExit(255)
elif operation == "ecs list-task-definitions":
    print("[]")
elif operation == "ec2 describe-security-groups":
    print("[]")
else:
    raise SystemExit("unsupported fake aws command: " + operation)
""",
        encoding="utf-8",
    )
    path.chmod(0o755)


def test_static_aws_v1_release_contract_passes() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/verify_aws_v1_release_contract.py"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_candidate_image_probe_uses_fail_closed_ast_under_repo_alembic_layout(
    tmp_path: Path,
) -> None:
    assert (ROOT / "alembic/__init__.py").is_file()
    source = _candidate_image_probe_source()
    assert "runpy" not in source
    assert "ast.parse" in source
    assert "ast.literal_eval" in source
    assert 'migration_graph("' in source
    assert 'glob("*.py")' in source

    completed = subprocess.run(
        [sys.executable, "-c", source],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "alembicHead": "c9d0e1f2a3b4",
        "databaseContract": "resolver-owner-v1",
        "migrationCommand": ["python", "-m", "app.db.aws_database_roles", "migrate"],
        "migrationSuccessMarkerRequired": (
            "Database migration and post-head runtime grant contract completed."
        ),
        "postHeadCatalogAudit": True,
    }

    malformed = tmp_path / "duplicate_revision.py"
    malformed.write_text(
        'revision = "jk2e5f6a8b31"\n'
        'revision = "unexpected"\n'
        'down_revision = ("e8c0a2d4f617", "100finance_dimensional", "ab12cd34ef56")\n',
        encoding="utf-8",
    )
    head_migration = (
        ROOT / "alembic/versions/8f3c1a9d2b70_merge_v1_finance_agents.py"
    )
    malformed_source = source.replace(str(head_migration), str(malformed))
    rejected = subprocess.run(
        [sys.executable, "-c", malformed_source],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert rejected.returncode != 0
    assert "Duplicate or empty revision" in rejected.stderr

    def run_probe_with_versions(
        directory_name: str,
        files: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        versions = tmp_path / directory_name
        versions.mkdir()
        for filename, metadata in files.items():
            (versions / filename).write_text(metadata, encoding="utf-8")
        candidate_source = source.replace(
            str(ROOT / "alembic/versions"),
            str(versions),
        )
        return subprocess.run(
            [sys.executable, "-c", candidate_source],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

    forked_versions = tmp_path / "forked-versions"
    forked_versions.mkdir()
    (forked_versions / "base.py").write_text(
        'revision = "jk2e5f6a8b31"\n'
        "down_revision = None\n",
        encoding="utf-8",
    )
    (forked_versions / "unexpected_linear_head.py").write_text(
        'revision = "unexpected_head"\n'
        'down_revision = "jk2e5f6a8b31"\n',
        encoding="utf-8",
    )
    forked_source = source.replace(
        str(ROOT / "alembic/versions"),
        str(forked_versions),
    )
    forked = subprocess.run(
        [sys.executable, "-c", forked_source],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert forked.returncode != 0
    assert "must contain exactly head c9d0e1f2a3b4" in forked.stderr
    assert "unexpected_head" in forked.stderr

    duplicate_revision = run_probe_with_versions(
        "duplicate-revision-versions",
        {
            "first.py": (
                'revision = "jk2e5f6a8b31"\n'
                "down_revision = None\n"
            ),
            "second.py": (
                'revision = "jk2e5f6a8b31"\n'
                "down_revision = None\n"
            ),
        },
    )
    assert duplicate_revision.returncode != 0
    assert "Duplicate Alembic revision jk2e5f6a8b31" in duplicate_revision.stderr

    missing_parent = run_probe_with_versions(
        "missing-parent-versions",
        {
            "head.py": (
                'revision = "jk2e5f6a8b31"\n'
                'down_revision = "missing_parent"\n'
            ),
        },
    )
    assert missing_parent.returncode != 0
    assert "references missing revisions" in missing_parent.stderr
    assert "missing_parent" in missing_parent.stderr

    disconnected_cycle = run_probe_with_versions(
        "cycle-versions",
        {
            "expected_head.py": (
                'revision = "jk2e5f6a8b31"\n'
                "down_revision = None\n"
            ),
            "cycle_a.py": (
                'revision = "cycle_a"\n'
                'down_revision = "cycle_b"\n'
            ),
            "cycle_b.py": (
                'revision = "cycle_b"\n'
                'down_revision = "cycle_a"\n'
            ),
        },
    )
    assert disconnected_cycle.returncode != 0
    assert "Alembic graph contains a cycle" in disconnected_cycle.stderr

    merge_graph = run_probe_with_versions(
        "valid-merge-versions",
        {
            "root_a.py": (
                'revision = "root_a"\n'
                "down_revision = None\n"
            ),
            "root_b.py": (
                'revision = "root_b"\n'
                "down_revision = None\n"
            ),
            "e1b3c5d7e920_parent.py": (
                'revision = "e1b3c5d7e920"\n'
                'down_revision = ("root_a", "root_b")\n'
            ),
            "f2c4d6e8a031_harden_tenant_resolver_owner_access.py": (
                'revision = "f2c4d6e8a031"\n'
                'down_revision = "e1b3c5d7e920"\n'
            ),
            "a3d5e7f9b142_add_whatsapp_template_webhook_resolver.py": (
                'revision = "a3d5e7f9b142"\n'
                'down_revision = "f2c4d6e8a031"\n'
            ),
            "b4e6f8a0c253_enforce_platform_owner_exclusivity.py": (
                'revision = "b4e6f8a0c253"\n'
                'down_revision = "a3d5e7f9b142"\n'
            ),
            "c5f7a9b1d304_harden_whatsapp_webhook_processing.py": (
                'revision = "c5f7a9b1d304"\n'
                'down_revision = "b4e6f8a0c253"\n'
            ),
            "d6a8c0e2f415_expand_commercial_workflow_rls.py": (
                'revision = "d6a8c0e2f415"\n'
                'down_revision = "c5f7a9b1d304"\n'
            ),
            "e7b9d1f3a526_add_contact_stock_entitlement.py": (
                'revision = "e7b9d1f3a526"\n'
                'down_revision = "d6a8c0e2f415"\n'
            ),
            "e8c0a2d4f617_widen_auth_rate_limit_event_scope.py": (
                'revision = "e8c0a2d4f617"\n'
                'down_revision = "e7b9d1f3a526"\n'
            ),
            "100finance_dimensional.py": (
                'revision = "100finance_dimensional"\n'
                'down_revision = "e8c0a2d4f617"\n'
            ),
            "ab12cd34ef56_add_agent_platform_v3.py": (
                'revision = "ab12cd34ef56"\n'
                'down_revision = "e8c0a2d4f617"\n'
            ),
            "8f3c1a9d2b70_merge_v1_finance_agents.py": (
                'revision = "8f3c1a9d2b70"\n'
                'down_revision = ("e8c0a2d4f617", "100finance_dimensional", "ab12cd34ef56")\n'
            ),
            "b7d4e2f9a610_add_finance_budget_lines.py": (
                'revision = "b7d4e2f9a610"\n'
                'down_revision = "8f3c1a9d2b70"\n'
            ),
            "fg8b0c1d4e97_add_agent_run_v2_context_states.py": (
                'revision = "fg8b0c1d4e97"\n'
                'down_revision = "e8c0a2d4f617"\n'
            ),
            "gh9c1d2e5f08_add_agent_deployment_bindings.py": (
                'revision = "gh9c1d2e5f08"\n'
                'down_revision = "fg8b0c1d4e97"\n'
            ),
            "hi0d2e3f6a19_merge_finance_agent_run_v2.py": (
                'revision = "hi0d2e3f6a19"\n'
                'down_revision = ("b7d4e2f9a610", "gh9c1d2e5f08")\n'
            ),
            "ij1e4f5a7b20_agent_run_context_without_matter.py": (
                'revision = "ij1e4f5a7b20"\n'
                'down_revision = "hi0d2e3f6a19"\n'
            ),
            "jk2e5f6a8b31_add_finance_tax_reform_alerts.py": (
                'revision = "jk2e5f6a8b31"\n'
                'down_revision = "ij1e4f5a7b20"\n'
            ),
            "ij1e3f4a7b20_add_agent_run_deployment_link.py": (
                'revision = "ij1e3f4a7b20"\n'
                'down_revision = "hi0d2e3f6a19"\n'
            ),
            "kl3f6a9b8c42_merge_runtime_heads.py": (
                'revision = "kl3f6a9b8c42"\n'
                'down_revision = ("ij1e3f4a7b20", "jk2e5f6a8b31")\n'
            ),
            "lm4g7b0c9d53_add_agent_deployment_role_package.py": (
                'revision = "lm4g7b0c9d53"\n'
                'down_revision = "kl3f6a9b8c42"\n'
            ),
            "mn5h8c1e0f64_preserve_finance_tax_profile_history.py": (
                'revision = "mn5h8c1e0f64"\n'
                'down_revision = "lm4g7b0c9d53"\n'
            ),
            "no6h9d2f1a75_allow_workspace_knowledge_chunks.py": (
                'revision = "no6h9d2f1a75"\n'
                'down_revision = "mn5h8c1e0f64"\n'
            ),
            "op7i0e3g2b86_add_required_password_change.py": (
                'revision = "op7i0e3g2b86"\n'
                'down_revision = "no6h9d2f1a75"\n'
            ),
            "oq8j1f4h3c97_add_whatsapp_coexistence_sync_state.py": (
                'revision = "oq8j1f4h3c97"\n'
                'down_revision = "op7i0e3g2b86"\n'
            ),
            "pr9k2g5i4d08_add_whatsapp_automation_cooldowns.py": (
                'revision = "pr9k2g5i4d08"\n'
                'down_revision = "oq8j1f4h3c97"\n'
            ),
            "qs0l3h6j5e19_track_removed_whatsapp_templates.py": (
                'revision = "qs0l3h6j5e19"\n'
                'down_revision = "pr9k2g5i4d08"\n'
            ),
            "rt1m4i7j6e20_add_identity_email_delivery.py": (
                'revision = "rt1m4i7j6e20"\n'
                'down_revision = "qs0l3h6j5e19"\n'
            ),
            "su2n5j8k7f31_enforce_whatsapp_automation_dispatch_rls.py": (
                'revision = "su2n5j8k7f31"\n'
                'down_revision = "rt1m4i7j6e20"\n'
            ),
            "tv3o6k8l9g42_add_task_legal_checklists.py": (
                'revision = "tv3o6k8l9g42"\n'
                'down_revision = "su2n5j8k7f31"\n'
            ),
            "uv4p7l9m0h53_add_flexible_task_context.py": (
                'revision = "uv4p7l9m0h53"\n'
                'down_revision = "tv3o6k8l9g42"\n'
            ),
            "wx5q8m1n2j64_add_task_external_shares.py": (
                'revision = "wx5q8m1n2j64"\n'
                'down_revision = "uv4p7l9m0h53"\n'
            ),
            "xy6r9n2p3k75_link_tasks_to_flow_runs.py": (
                'revision = "xy6r9n2p3k75"\n'
                'down_revision = "wx5q8m1n2j64"\n'
            ),
            "yz7s0p4q5l86_add_canonical_client_contracts.py": (
                'revision = "yz7s0p4q5l86"\n'
                'down_revision = "xy6r9n2p3k75"\n'
            ),
            "za8t1q4r5m97_add_task_organization.py": (
                'revision = "za8t1q4r5m97"\n'
                'down_revision = "yz7s0p4q5l86"\n'
            ),
            "zb9u2r5s6n08_enforce_client_contracts_rls.py": (
                'revision = "zb9u2r5s6n08"\n'
                'down_revision = "za8t1q4r5m97"\n'
            ),
            "zc0v3s6t7p19_track_meta_token_expiration.py": (
                'revision = "zc0v3s6t7p19"\n'
                'down_revision = "zb9u2r5s6n08"\n'
            ),
            "zd1w4t7u8q20_add_commercial_account_requests.py": (
                'revision = "zd1w4t7u8q20"\n'
                'down_revision = "zc0v3s6t7p19"\n'
            ),
            "ze2x5u8v9r31_add_canonical_commercial_stripe_bridge.py": (
                'revision = "ze2x5u8v9r31"\n'
                'down_revision = "zd1w4t7u8q20"\n'
            ),
            "zf3y6v9w0s42_add_meta_webhook_control_plane.py": (
                'revision = "zf3y6v9w0s42"\n'
                'down_revision = "ze2x5u8v9r31"\n'
            ),
                "zg4z7w0x1t53_align_meta_webhook_rls_policy_names.py": (
                    'revision = "zg4z7w0x1t53"\n'
                    'down_revision = "zf3y6v9w0s42"\n'
                ),
                "wa_picker_20260819_add_whatsapp_template_intent.py": (
                    'revision = "wa_picker_20260819"\n'
                    'down_revision = "zg4z7w0x1t53"\n'
                ),
                "f6a7b8c9d0e2_add_flow_sequence_stage_contract.py": (
                    'revision = "f6a7b8c9d0e2"\n'
                    'down_revision = "wa_picker_20260819"\n'
                ),
                "d7c1a9f4b2e8_backfill_intent_catalogo_utilidade.py": (
                    'revision = "d7c1a9f4b2e8"\n'
                    'down_revision = "f6a7b8c9d0e2"\n'
                ),
                "e3f1a7c9d2b5_add_worker_discovery_select_policies.py": (
                    'revision = "e3f1a7c9d2b5"\n'
                    'down_revision = "d7c1a9f4b2e8"\n'
                ),
                "b8d2f4a6c9e1_add_asaas_webhook_resolver.py": (
                    'revision = "b8d2f4a6c9e1"\n'
                    'down_revision = "e3f1a7c9d2b5"\n'
                ),
                "c9d0e1f2a3b4_add_platform_model_credentials.py": (
                    'revision = "c9d0e1f2a3b4"\n'
                    'down_revision = "b8d2f4a6c9e1"\n'
                ),
        },
    )
    assert merge_graph.returncode == 0, merge_graph.stderr
    assert json.loads(merge_graph.stdout)["alembicHead"] == "c9d0e1f2a3b4"


def test_greenfield_workflow_stops_after_migration_evidence() -> None:
    workflow = (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")

    assert "KLM_RELEASE_MODE: greenfield-prepare" in workflow
    assert 'evidenceType: "candidate"' in workflow
    assert 'runtimeStateObserved: "PREPARED"' in workflow
    assert "deploymentMutations: {" in workflow
    assert "ecsServices: false" in workflow
    assert "frontends: false" in workflow
    assert "dnsCutover: false" in workflow
    assert "runtimeEnabled: false" in workflow
    assert "schemaVersion: 1" in workflow
    assert "cleanCheckout: $clean_checkout" in workflow
    assert "githubSha: $github_sha" in workflow
    assert "ociRevisionLabel: $candidate_image_contract[0].ociRevisionLabel" in workflow
    assert 'alembicHeads: [$candidate_image_contract[0].alembicHead]' in workflow
    assert "releaseContractPassed: true" in workflow
    assert "postHeadCatalogAudit: $catalog_audit_passed" in workflow
    assert ".postHeadCatalogAudit == true" in workflow
    assert "workflowRunId: $workflow_run_id" in workflow
    assert '.alembicHeads == ["c9d0e1f2a3b4"]' in workflow
    assert "./scripts/verify_candidate_migration_image.sh" in workflow
    assert "steps.candidate-artifact.outputs.artifact-digest" in workflow
    assert "steps.migration-artifact.outputs.artifact-digest" in workflow
    assert "Record final artifact digest and stop before ENABLED" in workflow
    assert "./scripts/deploy_aws_ecs_v1.sh" not in workflow
    assert "./scripts/publish_aws_frontends_v1.sh" not in workflow
    assert "KLM_PREVIOUS_APP_REF" not in workflow


def test_greenfield_prepare_environment_rejects_service_targets() -> None:
    environment = _greenfield_environment()
    completed = subprocess.run(
        ["bash", "scripts/verify_aws_v1_greenfield_prepare_environment.sh"],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr

    environment["AWS_ECS_SERVICE_TARGETS"] = "[]"
    completed = subprocess.run(
        ["bash", "scripts/verify_aws_v1_greenfield_prepare_environment.sh"],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0
    assert "forbidden in greenfield-prepare" in completed.stderr


def test_greenfield_mode_rejects_legacy_binary_reference() -> None:
    environment = _greenfield_environment()
    environment["KLM_PREVIOUS_APP_REF"] = "a671ed1"
    completed = subprocess.run(
        ["bash", "scripts/verify_greenfield_v1_mode.sh"],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0
    assert "a671ed1 is not compatible" in completed.stderr


def test_greenfield_mode_rejects_incompatible_source_revision() -> None:
    environment = _greenfield_environment()
    environment["GITHUB_SHA"] = "a671ed13e0b67d000cb726d70d3d4fee9e446bbe"
    completed = subprocess.run(
        ["bash", "scripts/verify_greenfield_v1_mode.sh"],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0
    assert "incompatible a671ed1 legacy revision" in completed.stderr


def test_candidate_evidence_rejects_payload_not_matching_trusted_gate_output(
    tmp_path: Path,
) -> None:
    environment = _greenfield_environment()
    sha = environment["GITHUB_SHA"]
    payload = {
        "schemaVersion": 1,
        "evidenceType": "candidate",
        "cleanCheckout": True,
        "githubSha": sha,
        "ociRevisionLabel": sha,
        "runtimeImageUri": IMMUTABLE_BACKEND_IMAGE,
        "runtimeImageDigest": IMMUTABLE_BACKEND_IMAGE.split("@", 1)[1],
        "alembicHeads": ["c9d0e1f2a3b4"],
        "releaseContractPassed": True,
        "postHeadCatalogAuditRequired": True,
        "postHeadContractMarkerRequired": (
            "Database migration and post-head runtime grant contract completed."
        ),
        "workflowRunId": "123",
        "candidateImageContract": {
            "sourceRevision": sha,
            "ociRevisionLabel": sha,
            "runtimeImageUri": IMMUTABLE_BACKEND_IMAGE,
            "runtimeImageDigest": IMMUTABLE_BACKEND_IMAGE.split("@", 1)[1],
            "alembicHead": "c9d0e1f2a3b4",
            "databaseContract": "resolver-owner-v1",
        },
    }
    evidence = tmp_path / "candidate-release-evidence.json"
    evidence.write_text(json.dumps(payload), encoding="utf-8")
    actual_checksum = hashlib.sha256(evidence.read_bytes()).hexdigest()
    checksum = tmp_path / "candidate-release-evidence.json.sha256"
    checksum.write_text(f"{actual_checksum}  {evidence.name}\n", encoding="utf-8")
    environment["EXPECTED_CANDIDATE_EVIDENCE_SHA256"] = "0" * 64

    completed = subprocess.run(
        ["bash", "scripts/verify_candidate_release_evidence.sh", str(evidence), str(checksum)],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "trusted Gate 1 checksum output" in completed.stderr


@pytest.mark.parametrize(
    ("scenario", "expected_error"),
    (
        ("rollback", "CloudFormation is not PREPARED"),
        ("revision-drift", "task does not use the approved image"),
    ),
)
def test_prepared_state_rejects_rollback_and_task_revision_drift(
    tmp_path: Path,
    scenario: str,
    expected_error: str,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_aws = fake_bin / "aws"
    _write_fake_prepared_state_aws(fake_aws)
    environment = _greenfield_environment()
    environment.update(
        {
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "BACKEND_IMAGE": IMMUTABLE_BACKEND_IMAGE,
            "FAKE_PREPARED_SCENARIO": scenario,
            "GITHUB_ENV": str(tmp_path / "github-env"),
        }
    )

    completed = subprocess.run(
        ["bash", "scripts/verify_aws_v1_prepared_state.sh"],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert expected_error in completed.stderr


def test_prepared_state_exports_exact_cloudformation_task_revision(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_aws = fake_bin / "aws"
    _write_fake_prepared_state_aws(fake_aws)
    github_env = tmp_path / "github-env"
    environment = _greenfield_environment()
    environment.update(
        {
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "BACKEND_IMAGE": IMMUTABLE_BACKEND_IMAGE,
            "GITHUB_ENV": str(github_env),
        }
    )

    completed = subprocess.run(
        ["bash", "scripts/verify_aws_v1_prepared_state.sh"],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert github_env.read_text(encoding="utf-8") == (
        "AWS_ECS_MIGRATION_TASK_DEFINITION_ARN="
        "arn:aws:ecs:us-east-1:123456789012:task-definition/migrations:7\n"
    )


def test_oidc_identity_gate_rejects_another_assumed_role(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_aws = fake_bin / "aws"
    fake_aws.write_text(
        """#!/usr/bin/env python3
import json
print(json.dumps({
    "Account": "123456789012",
    "Arn": "arn:aws:sts::123456789012:assumed-role/wrong-role/session",
    "UserId": "role-id:session",
}))
""",
        encoding="utf-8",
    )
    fake_aws.chmod(0o755)
    environment = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"}

    completed = subprocess.run(
        [
            "bash",
            "scripts/verify_aws_oidc_identity.sh",
            "arn:aws:iam::123456789012:role/expected-role",
        ],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "not the expected OIDC assumed role" in completed.stderr


def test_fallback_compose_separates_migration_and_runtime_secrets() -> None:
    compose = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    assert "MEU_EXECUTIVO_MIGRATION_ENV_FILE" in compose
    assert compose.count("MEU_EXECUTIVO_RUNTIME_ENV_FILE") == 3
    assert "MEU_EXECUTIVO_ENV_FILE" not in compose


def test_deploy_environment_accepts_three_isolated_services() -> None:
    completed = subprocess.run(
        ["bash", "scripts/verify_aws_v1_deploy_environment.sh"],
        cwd=ROOT,
        env=_deploy_environment(),
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_deploy_environment_rejects_public_site_resource_reuse() -> None:
    environment = _deploy_environment()
    environment["AWS_BLACKBOX_BUCKET"] = environment["AWS_PUBLIC_SITE_BUCKET"]
    completed = subprocess.run(
        ["bash", "scripts/verify_aws_v1_deploy_environment.sh"],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0
    assert "site público" in completed.stderr


def test_deploy_environment_requires_api_worker_and_beat() -> None:
    environment = _deploy_environment()
    environment["AWS_ECS_SERVICE_TARGETS"] = json.dumps(
        [{"role": "api", "service": "api", "container": "api"}]
    )
    completed = subprocess.run(
        ["bash", "scripts/verify_aws_v1_deploy_environment.sh"],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0
    assert "beat" in completed.stderr
    assert "worker" in completed.stderr


def test_edge_contract_accepts_private_oac_and_isolated_api_origin(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_aws = fake_bin / "aws"
    _write_fake_edge_aws(fake_aws)
    environment = _deploy_environment()
    environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
    completed = subprocess.run(
        ["bash", "scripts/verify_aws_v1_edge_contract.sh"],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_edge_contract_rejects_dynamic_api_proxy(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_aws = fake_bin / "aws"
    _write_fake_edge_aws(fake_aws)
    environment = _deploy_environment()
    environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
    environment["FAKE_ADD_API_ORIGIN"] = "1"
    completed = subprocess.run(
        ["bash", "scripts/verify_aws_v1_edge_contract.sh"],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0
    assert "somente o bucket" in completed.stderr


def test_ecs_deploy_runs_migration_before_three_services(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    aws_log = tmp_path / "aws.log"
    fake_aws = fake_bin / "aws"
    fake_aws.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
Path(os.environ["AWS_FAKE_LOG"]).open("a", encoding="utf-8").write(" ".join(args) + "\\n")

def value(flag):
    return args[args.index(flag) + 1]

operation = " ".join(args[:2])
query = value("--query") if "--query" in args else ""
if operation == "ecs describe-services":
    service = value("--services")
    print(f"arn:aws:ecs:region:account:task-definition/{service}:1")
elif operation == "ecs describe-task-definition":
    task = value("--task-definition").split("/")[-1].split(":")[0]
    if "taskDefinitionArn" in query:
        print(f"arn:aws:ecs:region:account:task-definition/{task}:1")
    else:
        container = "migrate" if "migration" in task else task
        print(json.dumps({
            "taskDefinitionArn": os.environ["AWS_ECS_MIGRATION_TASK_DEFINITION_ARN"],
            "status": "ACTIVE",
            "family": task,
            "networkMode": "awsvpc",
            "requiresCompatibilities": ["FARGATE"],
            "cpu": "256",
            "memory": "512",
            "containerDefinitions": [{
                "name": container,
                "image": os.environ["BACKEND_IMAGE"],
                "command": (
                    ["alembic", "upgrade", "head"]
                    if os.environ.get("FAKE_UNSAFE_MIGRATION_COMMAND") == "1"
                    else ["python", "-m", "app.db.aws_database_roles", "migrate"]
                ),
            }],
        }))
elif operation == "ecs register-task-definition":
    definition = json.loads(Path(value("--cli-input-json").removeprefix("file://")).read_text())
    assert definition["family"] != "migration"
    assert definition["containerDefinitions"][0]["image"] == os.environ["BACKEND_IMAGE"]
    tag_start = args.index("--tags") + 1
    tag_end = args.index("--query")
    assert args[tag_start:tag_end] == [
        "key=Project,value=meu-executivo",
        "key=Environment,value=production",
        "key=ReleaseChannel,value=runtime",
        "key=SourceRevision,value=" + os.environ["GITHUB_SHA"],
    ]
    print(f"arn:aws:ecs:region:account:task-definition/{definition['family']}:2")
elif operation == "ssm get-parameter":
    if os.environ.get("FAKE_BOOTSTRAP_ACTIVE") == "1":
        print(json.dumps({"Parameter": {"Name": value("--name")}}))
    else:
        print(
            "An error occurred (ParameterNotFound) when calling the GetParameter operation",
            file=sys.stderr,
        )
        raise SystemExit(255)
elif operation == "iam get-role":
    print(
        "An error occurred (NoSuchEntity) when calling the GetRole operation",
        file=sys.stderr,
    )
    raise SystemExit(255)
elif operation == "stepfunctions describe-state-machine":
    print(
        "An error occurred (StateMachineDoesNotExist) when calling the "
        "DescribeStateMachine operation",
        file=sys.stderr,
    )
    raise SystemExit(255)
elif operation in {"ecs list-task-definitions", "ecs list-tasks"}:
    print("[]")
elif operation == "ec2 describe-security-groups":
    print("[]")
elif operation == "stepfunctions start-execution":
    payload = json.loads(value("--input"))
    assert set(payload) == {"correlationId"}
    print("arn:aws:states:region:account:execution:migrations:123")
elif operation == "stepfunctions describe-execution":
    migration = {
        "taskDefinitionArn": os.environ["AWS_ECS_MIGRATION_TASK_DEFINITION_ARN"],
        "taskArn": "arn:aws:ecs:region:account:task/migration",
        "exitCode": 0,
        "stoppedReason": "Essential container exited",
    }
    output = {
        "correlationId": os.environ["GITHUB_SHA"] + "/123",
        "migration": migration,
    }
    print(json.dumps({"status": "SUCCEEDED", "output": json.dumps(output)}))
elif operation == "logs filter-log-events":
    assert value("--log-group-name") == os.environ["AWS_MIGRATION_LOG_GROUP_NAME"]
    assert value("--log-stream-names") == "migration/migrate/migration"
    counter = Path(os.environ["AWS_FAKE_MARKER_COUNTER"])
    if not counter.exists():
        counter.write_text("1", encoding="utf-8")
        print("ResourceNotFoundException: log stream is still propagating", file=sys.stderr)
        raise SystemExit(255)
    print(json.dumps([{
        "message": "Database migration and post-head runtime grant contract completed."
    }]))
elif operation in {"ecs wait", "ecs update-service"}:
    pass
else:
    raise SystemExit("unsupported fake aws command: " + operation)
""",
        encoding="utf-8",
    )
    fake_aws.chmod(0o755)
    fake_curl = fake_bin / "curl"
    fake_curl.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

url = sys.argv[-1]
Path(os.environ["AWS_FAKE_LOG"]).open("a", encoding="utf-8").write("curl " + url + "\\n")
if url.endswith("/health"):
    print(json.dumps({"status": "ok", "app": "Meu Executivo"}))
elif url.endswith("/api/v1/status"):
    print(json.dumps({"components": {"database": {"status": "operational"}}}))
else:
    raise SystemExit("unsupported fake curl URL: " + url)
""",
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)
    environment = _deploy_environment()
    environment.update(
        {
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "AWS_FAKE_LOG": str(aws_log),
            "AWS_FAKE_MARKER_COUNTER": str(tmp_path / "marker-counter"),
            "BACKEND_IMAGE": IMMUTABLE_BACKEND_IMAGE,
            "AWS_ECS_MIGRATION_TASK_FAMILY": "migration",
            "AWS_ECS_MIGRATION_TASK_DEFINITION_ARN": (
                "arn:aws:ecs:region:account:task-definition/migration:1"
            ),
            "GITHUB_RUN_ID": "123",
        }
    )
    environment["FAKE_UNSAFE_MIGRATION_COMMAND"] = "1"
    unsafe_migration = subprocess.run(
        ["bash", "scripts/run_aws_ecs_migration_v1.sh"],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert unsafe_migration.returncode != 0
    assert "task PREPARED não corresponde" in unsafe_migration.stderr
    assert "ecs register-task-definition" not in aws_log.read_text(encoding="utf-8")
    environment.pop("FAKE_UNSAFE_MIGRATION_COMMAND")
    aws_log.write_text("", encoding="utf-8")

    migration_completed = subprocess.run(
        ["bash", "scripts/run_aws_ecs_migration_v1.sh"],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert migration_completed.returncode == 0, migration_completed.stderr
    rollout_completed = subprocess.run(
        ["bash", "scripts/deploy_aws_ecs_v1.sh"],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert rollout_completed.returncode == 0, rollout_completed.stderr
    commands = aws_log.read_text(encoding="utf-8").splitlines()
    migration = next(
        index
        for index, command in enumerate(commands)
        if "stepfunctions start-execution" in command
    )
    compatibility = next(
        index for index, command in enumerate(commands) if command.endswith("/api/v1/status")
    )
    updates = [index for index, command in enumerate(commands) if "ecs update-service" in command]
    assert len(updates) == 3
    assert sum(command.startswith("ssm get-parameter") for command in commands) == 6
    assert all(migration < compatibility < update for update in updates)
    assert not any("ecs run-task" in command for command in commands)
    assert not any("--network-configuration" in command for command in commands)
    runtime_registrations = [
        index for index, command in enumerate(commands) if "ecs register-task-definition" in command
    ]
    assert len(runtime_registrations) == 3
    assert all(compatibility < registration for registration in runtime_registrations)


def test_migration_blocks_when_ephemeral_bootstrap_marker_exists(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_aws = fake_bin / "aws"
    fake_aws.write_text(
        """#!/usr/bin/env python3
import json
import sys

if sys.argv[1:3] == ["ssm", "get-parameter"]:
    print(json.dumps({"Parameter": {"Name": "bootstrap-active"}}))
    raise SystemExit(0)
raise SystemExit("AWS call occurred after active bootstrap marker")
""",
        encoding="utf-8",
    )
    fake_aws.chmod(0o755)
    environment = _deploy_environment()
    environment.update(
        {
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "BACKEND_IMAGE": IMMUTABLE_BACKEND_IMAGE,
            "AWS_ECS_MIGRATION_TASK_FAMILY": "migration",
            "AWS_ECS_MIGRATION_TASK_DEFINITION_ARN": (
                "arn:aws:ecs:region:account:task-definition/migration:1"
            ),
        }
    )

    completed = subprocess.run(
        ["bash", "scripts/run_aws_ecs_migration_v1.sh"],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "still exists" in completed.stderr


@pytest.mark.parametrize(
    "present",
    ("task-definition", "running", "pending", "security-group"),
)
def test_bootstrap_absence_gate_rejects_each_persistent_resource(
    tmp_path: Path,
    present: str,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_aws = fake_bin / "aws"
    _write_fake_bootstrap_absence_aws(fake_aws)
    environment = _deploy_environment()
    environment.update(
        {
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "FAKE_PRESENT_BOOTSTRAP": present,
        }
    )
    completed = subprocess.run(
        ["bash", "scripts/verify_aws_database_bootstrap_absent.sh"],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "ephemeral bootstrap resource still exists" in completed.stderr


def test_migration_rechecks_bootstrap_after_prepared_task_validation(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    aws_log = tmp_path / "aws.log"
    counter = tmp_path / "ssm-count"
    fake_aws = fake_bin / "aws"
    fake_aws.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
operation = " ".join(args[:2])
Path(os.environ["AWS_FAKE_LOG"]).open("a", encoding="utf-8").write(" ".join(args) + "\\n")

def value(flag):
    return args[args.index(flag) + 1]

if operation == "ssm get-parameter":
    path = Path(os.environ["AWS_FAKE_SSM_COUNTER"])
    count = int(path.read_text() or "0") + 1 if path.exists() else 1
    path.write_text(str(count))
    if count == 1:
        print("An error occurred (ParameterNotFound) when calling GetParameter", file=sys.stderr)
        raise SystemExit(255)
    print(json.dumps({"Parameter": {"Name": "bootstrap-reappeared"}}))
elif operation == "iam get-role":
    print("An error occurred (NoSuchEntity) when calling GetRole", file=sys.stderr)
    raise SystemExit(255)
elif operation == "stepfunctions describe-state-machine":
    print(
        "An error occurred (StateMachineDoesNotExist) when calling DescribeStateMachine",
        file=sys.stderr,
    )
    raise SystemExit(255)
elif operation in {"ecs list-task-definitions", "ecs list-tasks"}:
    print("[]")
elif operation == "ec2 describe-security-groups":
    print("[]")
elif operation == "ecs describe-task-definition":
    print(json.dumps({
        "taskDefinitionArn": os.environ["AWS_ECS_MIGRATION_TASK_DEFINITION_ARN"],
        "status": "ACTIVE",
        "family": "migration",
        "networkMode": "awsvpc",
        "requiresCompatibilities": ["FARGATE"],
        "cpu": "256",
        "memory": "512",
        "containerDefinitions": [{
            "name": "migrate",
            "image": os.environ["BACKEND_IMAGE"],
            "command": ["python", "-m", "app.db.aws_database_roles", "migrate"],
        }],
    }))
elif operation == "stepfunctions start-execution":
    raise SystemExit("start-execution must not run after bootstrap reappears")
else:
    raise SystemExit("unsupported fake aws command: " + operation)
""",
        encoding="utf-8",
    )
    fake_aws.chmod(0o755)
    environment = _deploy_environment()
    environment.update(
        {
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "AWS_FAKE_LOG": str(aws_log),
            "AWS_FAKE_SSM_COUNTER": str(counter),
            "BACKEND_IMAGE": IMMUTABLE_BACKEND_IMAGE,
            "AWS_ECS_MIGRATION_TASK_FAMILY": "migration",
            "AWS_ECS_MIGRATION_TASK_DEFINITION_ARN": (
                "arn:aws:ecs:region:account:task-definition/migration:1"
            ),
        }
    )
    completed = subprocess.run(
        ["bash", "scripts/run_aws_ecs_migration_v1.sh"],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    commands = aws_log.read_text(encoding="utf-8")
    assert "ecs describe-task-definition" in commands
    assert "ecs register-task-definition" not in commands
    assert "stepfunctions start-execution" not in commands
    assert "bootstrap resource still exists" in completed.stderr


def test_rollout_blocks_before_ecs_when_bootstrap_is_active(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    aws_log = tmp_path / "aws.log"
    fake_aws = fake_bin / "aws"
    _write_fake_rollout_aws(fake_aws)
    fake_curl = fake_bin / "curl"
    _write_fake_previous_app_curl(fake_curl)
    environment = _deploy_environment()
    environment.update(
        {
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "AWS_FAKE_LOG": str(aws_log),
            "AWS_FAKE_SSM_COUNTER": str(tmp_path / "ssm-count"),
            "AWS_FAKE_BOOTSTRAP_AFTER": "1",
            "BACKEND_IMAGE": IMMUTABLE_BACKEND_IMAGE,
        }
    )

    completed = subprocess.run(
        ["bash", "scripts/deploy_aws_ecs_v1.sh"],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "still exists" in completed.stderr
    assert "ecs update-service" not in aws_log.read_text(encoding="utf-8")


def test_bootstrap_reappearance_rolls_back_updated_service_and_waits(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    aws_log = tmp_path / "aws.log"
    fake_aws = fake_bin / "aws"
    _write_fake_rollout_aws(fake_aws)
    fake_curl = fake_bin / "curl"
    _write_fake_previous_app_curl(fake_curl)
    environment = _deploy_environment()
    environment.update(
        {
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "AWS_FAKE_LOG": str(aws_log),
            "AWS_FAKE_SSM_COUNTER": str(tmp_path / "ssm-count"),
            # Initial gate and API pre-update gate pass.  The marker appears
            # before worker, after API was changed.
            "AWS_FAKE_BOOTSTRAP_AFTER": "3",
            "BACKEND_IMAGE": IMMUTABLE_BACKEND_IMAGE,
        }
    )

    completed = subprocess.run(
        ["bash", "scripts/deploy_aws_ecs_v1.sh"],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    commands = aws_log.read_text(encoding="utf-8").splitlines()
    updates = [command for command in commands if command.startswith("ecs update-service")]
    assert len(updates) == 2
    assert "--service api" in updates[0] and "task-definition/api:2" in updates[0]
    assert "--service api" in updates[1] and "task-definition/api:1" in updates[1]
    assert not any("--service worker" in command for command in updates)
    rollback_update_index = commands.index(updates[1])
    assert any(
        command.startswith("ecs wait services-stable")
        for command in commands[rollback_update_index + 1 :]
    )
    assert "Restauração das tasks schema-compatíveis" in completed.stderr
