from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError

from app.core.config import settings
from app.services.s3_service import S3Service, StorageUnavailableError


class FakeS3Client:
    def __init__(self, config: dict, *, overrides: dict | None = None):
        self.config = config
        self.overrides = overrides or {}

    def get_public_access_block(self, **_kwargs):
        return {"PublicAccessBlockConfiguration": self.config}

    def get_bucket_policy_status(self, **_kwargs):
        return self.overrides.get(
            "policy_status", {"PolicyStatus": {"IsPublic": False}}
        )

    def get_bucket_encryption(self, **_kwargs):
        return self.overrides.get(
            "encryption",
            {
                "ServerSideEncryptionConfiguration": {
                    "Rules": [
                        {
                            "ApplyServerSideEncryptionByDefault": {
                                "SSEAlgorithm": "aws:kms",
                                "KMSMasterKeyID": "alias/test-private-storage",
                            },
                            "BucketKeyEnabled": True,
                        }
                    ]
                }
            },
        )

    def get_bucket_versioning(self, **_kwargs):
        return self.overrides.get("versioning", {"Status": "Enabled"})

    def get_bucket_ownership_controls(self, **_kwargs):
        return self.overrides.get(
            "ownership",
            {
                "OwnershipControls": {
                    "Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]
                }
            },
        )

    def get_bucket_lifecycle_configuration(self, **_kwargs):
        return self.overrides.get(
            "lifecycle",
            {
                "Rules": [
                    {
                        "ID": "expire-quarantine",
                        "Filter": {"Prefix": "quarantine/"},
                        "Status": "Enabled",
                        "Expiration": {"Days": 7},
                    }
                ]
            },
        )


class FailingPresignClient:
    def generate_presigned_url(self, *_args, **_kwargs):
        raise ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "denied"}},
            "GeneratePresignedUrl",
        )


def allow_remote_aws(monkeypatch):
    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", "production")
    monkeypatch.setattr(settings, "KLM_ONLINE_APPROVED", True)
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", True)
    monkeypatch.setattr(settings, "KLM_AWS_ENABLED", True)
    monkeypatch.setattr(settings, "KLM_PRIVATE_AWS_STORAGE_ENABLED", True)
    monkeypatch.setattr(settings, "S3_KMS_KEY_ID", "alias/test-private-storage")


def test_private_bucket_gate_requires_every_public_access_block(monkeypatch):
    service = S3Service()
    allow_remote_aws(monkeypatch)
    monkeypatch.setattr(
        service,
        "_client",
        FakeS3Client(
            {
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": False,
            }
        ),
    )
    with pytest.raises(RuntimeError, match="bloqueio de acesso público completo"):
        service.verify_private_bucket()


def test_private_bucket_gate_accepts_complete_block(monkeypatch):
    service = S3Service()
    allow_remote_aws(monkeypatch)
    monkeypatch.setattr(
        service,
        "_client",
        FakeS3Client(
            {
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            }
        ),
    )
    service.verify_private_bucket()


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"policy_status": {"PolicyStatus": {"IsPublic": True}}}, "não pode ser pública"),
        (
            {
                "encryption": {
                    "ServerSideEncryptionConfiguration": {
                        "Rules": [
                            {
                                "ApplyServerSideEncryptionByDefault": {
                                    "SSEAlgorithm": "AES256"
                                }
                            }
                        ]
                    }
                }
            },
            "SSE-KMS",
        ),
        ({"versioning": {"Status": "Suspended"}}, "versionamento"),
        ({"ownership": {"OwnershipControls": {"Rules": []}}}, "BucketOwnerEnforced"),
        (
            {
                "lifecycle": {
                    "Rules": [
                        {
                            "Filter": {"Prefix": "quarantine/"},
                            "Status": "Enabled",
                            "Expiration": {"Days": 30},
                        }
                    ]
                }
            },
            "até 7 dias",
        ),
    ],
)
def test_private_bucket_gate_rejects_missing_required_control(
    monkeypatch,
    overrides,
    message,
):
    service = S3Service()
    allow_remote_aws(monkeypatch)
    monkeypatch.setattr(
        service,
        "_client",
        FakeS3Client(
            {
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            },
            overrides=overrides,
        ),
    )

    with pytest.raises(RuntimeError, match=message):
        service.verify_private_bucket()


def test_public_asset_presign_fails_closed_on_remote_provider_error(monkeypatch):
    service = S3Service()
    allow_remote_aws(monkeypatch)
    monkeypatch.setattr(service, "_client", FailingPresignClient())

    with pytest.raises(StorageUnavailableError, match="autorizar o upload"):
        service.generate_presigned_upload_url(
            "public-site/media/private.pdf",
            bucket_name=settings.S3_PUBLIC_ASSET_BUCKET_NAME,
        )


def test_enabling_model_apis_does_not_enable_s3(monkeypatch):
    service = S3Service()
    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", "local_simulation")
    monkeypatch.setattr(settings, "KLM_MODEL_APIS_ENABLED", True)
    monkeypatch.setattr(settings, "KLM_ONLINE_APPROVED", True)
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", False)
    monkeypatch.setattr(settings, "KLM_AWS_ENABLED", False)

    with pytest.raises(StorageUnavailableError, match="StorageObject"):
        service.generate_presigned_upload_url("matters/local.pdf")
    assert service._client is None


@pytest.mark.parametrize(
    ("online_approved", "external_enabled", "aws_enabled"),
    [
        (False, True, True),
        (True, False, True),
        (True, True, False),
    ],
)
def test_production_denied_aws_matrix_fails_without_writable_fallback(
    monkeypatch,
    tmp_path,
    online_approved,
    external_enabled,
    aws_enabled,
):
    boto3_client = Mock(side_effect=AssertionError("boto3.client não deveria ser chamado"))
    monkeypatch.setattr("app.services.s3_service.boto3.client", boto3_client)
    monkeypatch.setattr(S3Service, "LOCAL_ROOT", tmp_path / "objects")
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", "production")
    monkeypatch.setattr(settings, "KLM_ONLINE_APPROVED", online_approved)
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", external_enabled)
    monkeypatch.setattr(settings, "KLM_AWS_ENABLED", aws_enabled)
    monkeypatch.setattr(settings, "KLM_PRIVATE_AWS_STORAGE_ENABLED", False)
    service = S3Service()
    key = "matters/local-contract.pdf"

    operations = (
        lambda: service.generate_presigned_upload_url(key),
        lambda: service.upload_bytes(b"contract", key, "application/pdf"),
        lambda: service.generate_presigned_download_url(key),
        lambda: service.get_public_asset_url("public-site/media/contract.pdf"),
        lambda: service.delete_object(key),
        service.verify_private_bucket,
        lambda: service.client,
    )
    for operation in operations:
        with pytest.raises(StorageUnavailableError, match="S3 é obrigatório"):
            operation()

    assert not S3Service.local_object_path(key).exists()
    assert service._client is None
    boto3_client.assert_not_called()


def test_local_mode_keeps_secure_internal_io_and_public_assets(monkeypatch, tmp_path):
    monkeypatch.setattr(S3Service, "LOCAL_ROOT", tmp_path / "objects")
    monkeypatch.setattr(settings, "APP_ENV", "development")
    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", "local_simulation")
    monkeypatch.setattr(settings, "KLM_ONLINE_APPROVED", False)
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", False)
    monkeypatch.setattr(settings, "KLM_AWS_ENABLED", False)
    monkeypatch.setattr(settings, "KLM_PRIVATE_AWS_STORAGE_ENABLED", False)
    service = S3Service()
    private_key = "matters/local-contract.pdf"
    public_key = "public-site/media/site-image.png"

    with pytest.raises(StorageUnavailableError, match="StorageObject"):
        service.generate_presigned_upload_url(private_key)
    with pytest.raises(StorageUnavailableError, match="StorageObject"):
        service.generate_presigned_download_url(private_key)

    with pytest.raises(StorageUnavailableError, match="StorageObject"):
        service.upload_bytes(b"contract", private_key, "application/pdf")
    assert not S3Service.local_object_exists(private_key)
    with pytest.raises(StorageUnavailableError, match="StorageObject"):
        service.delete_object(private_key)

    upload_url = service.generate_presigned_upload_url(
        public_key,
        mime_type="image/png",
        bucket_name=settings.S3_PUBLIC_ASSET_BUCKET_NAME,
    )
    download_url = service.get_public_asset_url(public_key)
    assert "/api/v1/local-storage/upload?ticket=" in upload_url
    assert "/api/v1/local-storage/download?ticket=" in download_url
    assert service._client is None


def test_local_filesystem_rejects_root_and_internal_directory_symlinks(
    monkeypatch,
    tmp_path,
):
    outside = tmp_path / "outside"
    outside.mkdir()
    linked_root = tmp_path / "linked-root"
    linked_root.symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(S3Service, "LOCAL_ROOT", linked_root)

    with pytest.raises(StorageUnavailableError, match="symlink"):
        S3Service.write_local_object_atomic("tenant/payload", b"blocked")
    assert not (outside / "tenant" / "payload").exists()

    root = tmp_path / "objects"
    (root / "tenant").mkdir(parents=True)
    (root / "tenant" / "linked").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(S3Service, "LOCAL_ROOT", root)
    with pytest.raises(OSError):
        S3Service.write_local_object_atomic("tenant/linked/payload", b"blocked")
    assert not (outside / "payload").exists()


def test_local_filesystem_never_follows_leaf_symlink(monkeypatch, tmp_path):
    root = tmp_path / "objects"
    target = tmp_path / "outside-secret"
    target.write_bytes(b"secret")
    (root / "tenant").mkdir(parents=True)
    leaf = root / "tenant" / "payload"
    leaf.symlink_to(target)
    monkeypatch.setattr(S3Service, "LOCAL_ROOT", root)

    with pytest.raises(OSError):
        S3Service.open_local_object("tenant/payload")
    with pytest.raises(StorageUnavailableError, match="arquivo regular"):
        S3Service.delete_local_object("tenant/payload")
    assert target.read_bytes() == b"secret"

    S3Service.write_local_object_atomic("tenant/payload", b"replacement")
    assert target.read_bytes() == b"secret"
    assert not leaf.is_symlink()
    assert leaf.read_bytes() == b"replacement"


def test_local_filesystem_fails_closed_without_openat_support(monkeypatch, tmp_path):
    monkeypatch.setattr(S3Service, "LOCAL_ROOT", tmp_path / "objects")
    monkeypatch.setattr(S3Service, "_secure_fs_supported", staticmethod(lambda: False))

    with pytest.raises(StorageUnavailableError, match="openat/O_NOFOLLOW"):
        S3Service.write_local_object_atomic("tenant/payload", b"blocked")


def test_issued_ticket_repr_redacts_secret(monkeypatch, tmp_path):
    monkeypatch.setattr(S3Service, "LOCAL_ROOT", tmp_path / "objects")
    monkeypatch.setattr(settings, "APP_ENV", "development")
    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", "local_simulation")
    monkeypatch.setattr(settings, "KLM_ONLINE_APPROVED", False)
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", False)
    monkeypatch.setattr(settings, "KLM_AWS_ENABLED", False)
    monkeypatch.setattr(settings, "KLM_PRIVATE_AWS_STORAGE_ENABLED", False)
    version_id = uuid4()
    ticket = S3Service().issue_local_object_ticket(
        operation="object_download",
        workspace_id=uuid4(),
        object_id=uuid4(),
        version_id=version_id,
        version=1,
        s3_key=f"tenant/{version_id}/payload",
        content_type="application/pdf",
        expires_in=60,
    )

    rendered = repr(ticket)
    assert ticket.token not in rendered
    assert ticket.jti not in rendered
    assert ticket.url not in rendered


def test_remote_s3_allows_public_asset_presign_but_blocks_private_bypasses(
    monkeypatch,
):
    client = Mock()
    client.generate_presigned_url.return_value = "https://s3.example/upload"
    boto3_client = Mock(return_value=client)
    monkeypatch.setattr("app.services.s3_service.boto3.client", boto3_client)
    allow_remote_aws(monkeypatch)
    service = S3Service()

    upload_url = service.generate_presigned_upload_url(
        "public-site/media/remote.pdf",
        bucket_name=settings.S3_PUBLIC_ASSET_BUCKET_NAME,
    )
    with pytest.raises(StorageUnavailableError, match="StorageObject"):
        service.generate_presigned_upload_url("matters/remote.pdf")
    with pytest.raises(StorageUnavailableError, match="StorageObject"):
        service.generate_presigned_download_url("matters/remote.pdf")
    with pytest.raises(StorageUnavailableError, match="StorageObject"):
        service.upload_bytes(b"remote", "matters/remote.pdf", "application/pdf")
    with pytest.raises(StorageUnavailableError, match="StorageObject"):
        service.delete_object("matters/remote.pdf")

    assert upload_url == "https://s3.example/upload"
    boto3_client.assert_called_once_with(
        "s3",
        region_name=settings.AWS_REGION,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID or None,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY or None,
    )
    assert client.generate_presigned_url.call_count == 1
    client.put_object.assert_not_called()
    client.delete_object.assert_not_called()


def test_private_worker_upload_uses_logical_key_clean_state_and_sse_kms(
    monkeypatch,
    tmp_path,
):
    allow_remote_aws(monkeypatch)
    monkeypatch.setattr(settings, "S3_KMS_KEY_ID", "alias/meu-executivo-private")
    client = Mock()
    client.head_object.return_value = {
        "Metadata": {"sha256": "a" * 64},
        "ServerSideEncryption": "aws:kms",
        "ContentLength": len(b"conta,status\nAcme,active\n"),
    }
    service = S3Service()
    monkeypatch.setattr(service, "_client", client)
    artifact = tmp_path / "export.csv"
    artifact.write_bytes(b"conta,status\nAcme,active\n")
    checksum = "a" * 64

    key = service.upload_authorized_private_file(
        Path(artifact),
        "platform-exports/job/export.csv",
        content_type="text/csv",
        state="clean",
        authorization_granted=True,
        checksum_sha256=checksum,
        metadata={"dataset": "accounts"},
    )

    assert key == "platform-exports/job/export.csv"
    _handle, bucket, stored_key = client.upload_fileobj.call_args.args
    extra_args = client.upload_fileobj.call_args.kwargs["ExtraArgs"]
    assert bucket == settings.S3_BUCKET_NAME
    assert stored_key == key
    assert extra_args == {
        "ContentType": "text/csv",
        "Metadata": {
            "storage-state": "clean",
            "sha256": checksum,
            "dataset": "accounts",
        },
        "ServerSideEncryption": "aws:kms",
        "SSEKMSKeyId": "alias/meu-executivo-private",
    }


def test_private_presigns_require_authorization_and_keep_quarantine_separate(
    monkeypatch,
):
    allow_remote_aws(monkeypatch)
    monkeypatch.setattr(settings, "S3_KMS_KEY_ID", "kms-key-id")
    client = Mock()
    client.generate_presigned_url.side_effect = [
        "https://s3.example/quarantine-upload",
        "https://s3.example/private-download",
    ]
    service = S3Service()
    monkeypatch.setattr(service, "_client", client)

    with pytest.raises(StorageUnavailableError, match="autorização"):
        service.generate_presigned_private_upload_url(
            "quarantine/workspace/object/payload",
            content_type="application/pdf",
            expires_in=120,
            authorization_granted=False,
            expected_byte_size=100,
        )
    with pytest.raises(ValueError, match="quarantine"):
        service.generate_presigned_private_upload_url(
            "tenants/workspace/object/payload",
            content_type="application/pdf",
            expires_in=120,
            authorization_granted=True,
            expected_byte_size=100,
        )

    upload_url = service.generate_presigned_private_upload_url(
        "quarantine/workspace/object/payload",
        content_type="application/pdf",
        expires_in=120,
        authorization_granted=True,
        expected_byte_size=100,
        checksum_sha256="b" * 64,
    )
    with pytest.raises(StorageUnavailableError, match="reautorização"):
        service.generate_authorized_private_download_url(
            "tenants/workspace/object/payload",
            expires_in=120,
            authorization_granted=False,
        )
    download_url = service.generate_authorized_private_download_url(
        "tenants/workspace/object/payload",
        expires_in=120,
        authorization_granted=True,
        response_content_type="application/pdf",
        response_content_disposition='attachment; filename="documento.pdf"',
    )

    assert upload_url.endswith("quarantine-upload")
    upload_params = client.generate_presigned_url.call_args_list[0].kwargs["Params"]
    assert upload_params["ContentLength"] == 100
    assert download_url.endswith("private-download")
    upload_call, download_call = client.generate_presigned_url.call_args_list
    assert upload_call.args[0] == "put_object"
    assert upload_call.kwargs["Params"]["Metadata"]["storage-state"] == "quarantine"
    assert upload_call.kwargs["Params"]["ServerSideEncryption"] == "aws:kms"
    assert download_call.args[0] == "get_object"
    assert "ServerSideEncryption" not in download_call.kwargs["Params"]


def test_private_worker_upload_deletes_object_when_s3_receipt_is_not_kms(
    monkeypatch,
    tmp_path,
):
    allow_remote_aws(monkeypatch)
    monkeypatch.setattr(settings, "S3_KMS_KEY_ID", "kms-key-id")
    client = Mock()
    client.head_object.return_value = {
        "Metadata": {"sha256": "d" * 64},
        "ServerSideEncryption": "AES256",
        "ContentLength": 7,
    }
    service = S3Service()
    monkeypatch.setattr(service, "_client", client)
    artifact = tmp_path / "private.bin"
    artifact.write_bytes(b"private")

    with pytest.raises(StorageUnavailableError, match="SSE-KMS"):
        service.upload_authorized_private_file(
            artifact,
            "platform-exports/job/private.bin",
            content_type="application/octet-stream",
            state="clean",
            authorization_granted=True,
            checksum_sha256="d" * 64,
        )

    client.delete_object.assert_called_once_with(
        Bucket=settings.S3_BUCKET_NAME,
        Key="platform-exports/job/private.bin",
    )


def test_quarantine_promotion_checks_hash_and_reencrypts_clean_object(monkeypatch):
    allow_remote_aws(monkeypatch)
    monkeypatch.setattr(settings, "S3_KMS_KEY_ID", "kms-key-id")
    checksum = "c" * 64
    client = Mock()
    client.head_object.side_effect = [
        {
            "Metadata": {"storage-state": "quarantine", "sha256": checksum},
            "ServerSideEncryption": "aws:kms",
            "ContentLength": 42,
            "ContentType": "application/pdf",
        },
        {
            "Metadata": {"storage-state": "clean", "sha256": checksum},
            "ServerSideEncryption": "aws:kms",
            "ContentLength": 42,
            "ContentType": "application/pdf",
        },
    ]
    service = S3Service()
    monkeypatch.setattr(service, "_client", client)

    result = service.promote_authorized_quarantine_object(
        "quarantine/workspace/version/payload",
        "tenants/workspace/objects/object/versions/version/payload",
        authorization_granted=True,
        checksum_sha256=checksum,
        content_type="application/pdf",
    )

    assert result.startswith("tenants/")
    copy_params = client.copy_object.call_args.kwargs
    assert copy_params["Metadata"]["storage-state"] == "clean"
    assert copy_params["Metadata"]["sha256"] == checksum
    assert copy_params["ServerSideEncryption"] == "aws:kms"
    client.delete_object.assert_called_once_with(
        Bucket=settings.S3_BUCKET_NAME,
        Key="quarantine/workspace/version/payload",
    )


def test_quarantine_promotion_keeps_source_when_clean_receipt_is_invalid(monkeypatch):
    allow_remote_aws(monkeypatch)
    monkeypatch.setattr(settings, "S3_KMS_KEY_ID", "kms-key-id")
    checksum = "e" * 64
    client = Mock()
    client.head_object.side_effect = [
        {
            "Metadata": {"storage-state": "quarantine", "sha256": checksum},
            "ServerSideEncryption": "aws:kms",
            "ContentLength": 42,
            "ContentType": "application/pdf",
        },
        {
            "Metadata": {"storage-state": "clean", "sha256": checksum},
            "ServerSideEncryption": "AES256",
            "ContentLength": 42,
            "ContentType": "application/pdf",
        },
    ]
    service = S3Service()
    monkeypatch.setattr(service, "_client", client)
    source = "quarantine/workspace/version/payload"
    destination = "tenants/workspace/objects/object/versions/version/payload"

    with pytest.raises(StorageUnavailableError, match="promovido"):
        service.promote_authorized_quarantine_object(
            source,
            destination,
            authorization_granted=True,
            checksum_sha256=checksum,
            content_type="application/pdf",
        )

    client.delete_object.assert_called_once_with(
        Bucket=settings.S3_BUCKET_NAME,
        Key=destination,
    )
