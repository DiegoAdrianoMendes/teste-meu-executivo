"""
Service: S3Service
Abstração para operações com o Amazon S3.
Gera URLs pré-assinadas para upload/download direto pelo frontend.
"""
from __future__ import annotations

import hmac
import ipaddress
import os
import re
import stat
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Literal
from urllib.parse import quote, unquote, urlsplit, urlunsplit
from uuid import UUID

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import settings
from app.core.klm_runtime import aws_external_access_allowed, aws_private_storage_allowed
from app.core.logging import logger
from app.core.logging_privacy import opaque_ref, safe_error_type
from app.core.security import _encode_token, create_local_storage_ticket, verify_token


class StorageUnavailableError(RuntimeError):
    """O armazenamento configurado não conseguiu autorizar a operação."""


_OBJECT_TICKET_PURPOSES = {
    "object_upload": "content_upload",
    "object_download": "content_download",
    "media_download": "media_delivery",
}


@dataclass(frozen=True)
class IssuedLocalStorageTicket:
    url: str = field(repr=False)
    token: str = field(repr=False)
    jti: str = field(repr=False)
    expires_at: datetime


class S3Service:
    """Encapsula todas as operações com o Amazon S3."""

    LOCAL_ROOT = Path(".local/object-storage")

    def __init__(self):
        self._client = None

    @staticmethod
    def _require_storage_backend() -> bool:
        """Return whether remote S3 is active, rejecting production fallback."""
        if aws_external_access_allowed():
            if not settings.S3_BUCKET_NAME.strip() or not settings.AWS_REGION.strip():
                raise StorageUnavailableError("Configuração obrigatória do S3 está ausente.")
            return True
        if settings.is_production:
            raise StorageUnavailableError(
                "S3 é obrigatório em produção; o armazenamento local está desabilitado."
            )
        return False

    @staticmethod
    def _require_private_storage_backend() -> bool:
        """Gate private S3 independently from outbound/provider execution."""
        if aws_private_storage_allowed():
            if not settings.S3_BUCKET_NAME.strip() or not settings.AWS_REGION.strip():
                raise StorageUnavailableError("Configuração obrigatória do S3 privado ausente.")
            return True
        if settings.is_production:
            raise StorageUnavailableError(
                "S3 é obrigatório para armazenamento privado em produção; "
                "o armazenamento local está desabilitado."
            )
        return False

    @property
    def client(self):
        """Lazy initialization do cliente S3."""
        if not (aws_external_access_allowed() or aws_private_storage_allowed()):
            if settings.is_production:
                raise StorageUnavailableError(
                    "S3 é obrigatório em produção; os gates do runtime bloquearam o acesso."
                )
            raise StorageUnavailableError("S3 remoto bloqueado pelos gates do runtime.")
        if self._client is None:
            self._client = boto3.client(
                "s3",
                region_name=settings.AWS_REGION,
                aws_access_key_id=settings.AWS_ACCESS_KEY_ID or None,
                aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY or None,
            )
        return self._client

    def generate_presigned_upload_url(
        self,
        s3_key: str,
        mime_type: str = "application/octet-stream",
        expires_in: int = 3600,
        bucket_name: str | None = None,
    ) -> str:
        """
        Gera uma URL pré-assinada para S3 ou um ticket do armazenamento local.
        """
        remote_enabled = self._require_storage_backend()
        if bucket_name != settings.S3_PUBLIC_ASSET_BUCKET_NAME or not s3_key.startswith(
            "public-site/media/"
        ):
            raise StorageUnavailableError(
                "Uploads privados exigem autorização via StorageObject."
            )
        if not remote_enabled:
            ticket = create_local_storage_ticket(
                s3_key,
                operation="public_asset_upload",
                content_type=mime_type,
                expires_in=expires_in,
            )
            return self._local_ticket_url("/api/v1/local-storage/upload", ticket)
        try:
            target_bucket = bucket_name or settings.S3_BUCKET_NAME
            url = self.client.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": target_bucket,
                    "Key": s3_key,
                    "ContentType": mime_type,
                },
                ExpiresIn=expires_in,
            )
            return url
        except ClientError as e:
            logger.error(
                "s3_presign_error",
                error_type=safe_error_type(e),
                object_ref=opaque_ref(s3_key, kind="storage_object"),
            )
            raise StorageUnavailableError("Não foi possível autorizar o upload no S3.") from e

    def get_public_asset_url(self, s3_key: str) -> str:
        """URL permanente apenas para o bucket separado de mídia pública do site."""
        if not self._require_storage_backend():
            if not s3_key.startswith("public-site/media/"):
                raise StorageUnavailableError("Chave de mídia pública inválida.")
            ticket = create_local_storage_ticket(
                s3_key,
                operation="public_asset_download",
                expires_in=24 * 60 * 60,
            )
            return self._local_ticket_url("/api/v1/local-storage/download", ticket)
        bucket = settings.S3_PUBLIC_ASSET_BUCKET_NAME
        return f"https://{bucket}.s3.{settings.AWS_REGION}.amazonaws.com/{s3_key}"

    def generate_presigned_download_url(
        self, s3_key: str, expires_in: int = 3600
    ) -> str:
        """Reject private download links without a persisted purpose-bound ticket."""
        del s3_key, expires_in
        self._require_storage_backend()
        raise StorageUnavailableError(
            "Downloads privados exigem autorização via StorageObject."
        )

    @staticmethod
    def _validate_private_key(
        s3_key: str,
        *,
        state: Literal["quarantine", "clean"] | None = None,
    ) -> str:
        """Validate one logical private key without accepting URLs or public assets."""
        parts = S3Service._local_key_parts(s3_key)
        if parts[0] == "public-site":
            raise ValueError("Chave pública não pode ser usada no armazenamento privado.")
        if state == "quarantine" and parts[0] != "quarantine":
            raise ValueError("Objetos em quarentena exigem o prefixo quarantine/.")
        if state == "clean" and parts[0] in {"quarantine", "staging"}:
            raise ValueError("Objeto limpo não pode usar prefixo temporário ou de quarentena.")
        return "/".join(parts)

    @staticmethod
    def _validate_checksum(checksum_sha256: str | None) -> str | None:
        if checksum_sha256 is None:
            return None
        normalized = checksum_sha256.strip().lower()
        if len(normalized) != 64 or any(
            character not in "0123456789abcdef" for character in normalized
        ):
            raise ValueError("Checksum SHA-256 inválido.")
        return normalized

    @staticmethod
    def _validate_expiry(expires_in: int) -> int:
        if not 30 <= expires_in <= 900:
            raise ValueError("URLs privadas devem expirar entre 30 e 900 segundos.")
        return expires_in

    @staticmethod
    def _validate_content_disposition(value: str | None) -> str | None:
        if value is None:
            return None
        if any(character in value for character in ("\r", "\n", "\x00")):
            raise ValueError("Content-Disposition inválido.")
        return value

    @staticmethod
    def _validate_content_type(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if (
            not normalized
            or len(normalized) > 200
            or "/" not in normalized
            or any(character in normalized for character in ("\r", "\n", "\x00"))
        ):
            raise ValueError("Content-Type inválido.")
        return normalized

    @staticmethod
    def _private_metadata(
        *,
        state: Literal["quarantine", "clean"],
        checksum_sha256: str | None,
        metadata: dict[str, str] | None = None,
    ) -> dict[str, str]:
        values = {"storage-state": state}
        checksum = S3Service._validate_checksum(checksum_sha256)
        if checksum is not None:
            values["sha256"] = checksum
        for key, value in (metadata or {}).items():
            normalized_key = str(key).strip().lower()
            normalized_value = str(value).strip()
            if (
                not normalized_key
                or not normalized_value
                or normalized_key in {"storage-state", "sha256"}
                or len(normalized_key) > 64
                or len(normalized_value) > 512
                or not re.fullmatch(r"[a-z0-9-]+", normalized_key)
                or any(character in normalized_value for character in ("\r", "\n", "\x00"))
            ):
                raise ValueError("Metadado privado inválido.")
            values[normalized_key] = normalized_value
        return values

    @staticmethod
    def _private_encryption_params() -> dict[str, Any]:
        key_id = settings.S3_KMS_KEY_ID.strip()
        if not key_id:
            raise StorageUnavailableError(
                "S3_KMS_KEY_ID é obrigatório para objetos privados no S3."
            )
        return {
            "ServerSideEncryption": "aws:kms",
            "SSEKMSKeyId": key_id,
        }

    def generate_presigned_private_upload_url(
        self,
        s3_key: str,
        *,
        content_type: str,
        expires_in: int,
        authorization_granted: bool,
        expected_byte_size: int,
        checksum_sha256: str | None = None,
    ) -> str:
        """Issue a quarantine-only PUT URL after the caller persisted authorization."""
        if not authorization_granted:
            raise StorageUnavailableError("Upload privado exige autorização persistida.")
        key = self._validate_private_key(s3_key, state="quarantine")
        expiry = self._validate_expiry(expires_in)
        size = int(expected_byte_size)
        if size < 1 or size > settings.DOCUMENT_AV_MAX_BYTES:
            raise ValueError("Tamanho autorizado para upload privado é inválido.")
        if not self._require_private_storage_backend():
            raise StorageUnavailableError(
                "Presign privado remoto não é usado pelo adapter local."
            )
        params = {
            "Bucket": settings.S3_BUCKET_NAME,
            "Key": key,
            "ContentType": self._validate_content_type(content_type),
            # ContentLength becomes part of SigV4 SignedHeaders. S3 therefore
            # rejects a PUT whose actual body length differs before our later
            # receipt/AV verification is even reached.
            "ContentLength": size,
            "Metadata": self._private_metadata(
                state="quarantine",
                checksum_sha256=checksum_sha256,
            ),
            **self._private_encryption_params(),
        }
        try:
            return self.client.generate_presigned_url(
                "put_object",
                Params=params,
                ExpiresIn=expiry,
            )
        except (BotoCoreError, ClientError) as exc:
            logger.error(
                "s3_private_upload_presign_error",
                error_type=safe_error_type(exc),
                object_ref=opaque_ref(key, kind="storage_object"),
            )
            raise StorageUnavailableError(
                "Não foi possível autorizar o upload privado no S3."
            ) from exc

    def generate_authorized_private_download_url(
        self,
        s3_key: str,
        *,
        expires_in: int,
        authorization_granted: bool,
        response_content_type: str | None = None,
        response_content_disposition: str | None = None,
    ) -> str:
        """Issue a short GET URL only after the application reauthorized the actor."""
        if not authorization_granted:
            raise StorageUnavailableError("Download privado exige reautorização.")
        key = self._validate_private_key(s3_key, state="clean")
        expiry = self._validate_expiry(expires_in)
        if not self._require_private_storage_backend():
            raise StorageUnavailableError(
                "Download privado remoto não é usado pelo adapter local."
            )
        params: dict[str, Any] = {
            "Bucket": settings.S3_BUCKET_NAME,
            "Key": key,
        }
        if response_content_type:
            params["ResponseContentType"] = self._validate_content_type(
                response_content_type
            )
        disposition = self._validate_content_disposition(response_content_disposition)
        if disposition:
            params["ResponseContentDisposition"] = disposition
        try:
            return self.client.generate_presigned_url(
                "get_object",
                Params=params,
                ExpiresIn=expiry,
            )
        except (BotoCoreError, ClientError) as exc:
            logger.error(
                "s3_private_download_presign_error",
                error_type=safe_error_type(exc),
                object_ref=opaque_ref(key, kind="storage_object"),
            )
            raise StorageUnavailableError(
                "Não foi possível autorizar o download privado no S3."
            ) from exc

    def upload_authorized_private_file(
        self,
        source: Path | BinaryIO,
        s3_key: str,
        *,
        content_type: str,
        state: Literal["quarantine", "clean"],
        authorization_granted: bool,
        checksum_sha256: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> str:
        """Stream a trusted worker artifact into private S3 using mandatory SSE-KMS."""
        if not authorization_granted:
            raise StorageUnavailableError("Escrita privada exige autorização persistida.")
        key = self._validate_private_key(s3_key, state=state)
        if not self._require_private_storage_backend():
            raise StorageUnavailableError(
                "Upload privado remoto não é usado pelo adapter local."
            )
        extra_args = {
            "ContentType": self._validate_content_type(content_type),
            "Metadata": self._private_metadata(
                state=state,
                checksum_sha256=checksum_sha256,
                metadata=metadata,
            ),
            **self._private_encryption_params(),
        }
        handle: BinaryIO | None = None
        uploaded = False
        try:
            handle = source.open("rb") if isinstance(source, Path) else source
            self.client.upload_fileobj(
                handle,
                settings.S3_BUCKET_NAME,
                key,
                ExtraArgs=extra_args,
            )
            uploaded = True
            receipt = self.client.head_object(
                Bucket=settings.S3_BUCKET_NAME,
                Key=key,
            )
            receipt_metadata = receipt.get("Metadata", {})
            expected_checksum = extra_args["Metadata"].get("sha256")
            if expected_checksum and not hmac.compare_digest(
                str(receipt_metadata.get("sha256") or ""),
                expected_checksum,
            ):
                raise StorageUnavailableError(
                    "Recibo do S3 não confirmou o checksum lógico do objeto."
                )
            if receipt.get("ServerSideEncryption") != "aws:kms":
                raise StorageUnavailableError(
                    "Recibo do S3 não confirmou criptografia SSE-KMS."
                )
            if (
                isinstance(source, Path)
                and int(receipt.get("ContentLength", -1)) != source.stat().st_size
            ):
                raise StorageUnavailableError(
                    "Recibo do S3 não confirmou o tamanho do objeto."
                )
            return key
        except StorageUnavailableError:
            if uploaded:
                try:
                    self.client.delete_object(Bucket=settings.S3_BUCKET_NAME, Key=key)
                except (BotoCoreError, ClientError):
                    pass
            raise
        except (BotoCoreError, ClientError, OSError) as exc:
            if uploaded:
                try:
                    self.client.delete_object(Bucket=settings.S3_BUCKET_NAME, Key=key)
                except (BotoCoreError, ClientError):
                    pass
            logger.error(
                "s3_private_upload_error",
                error_type=safe_error_type(exc),
                object_ref=opaque_ref(key, kind="storage_object"),
            )
            raise StorageUnavailableError(
                "Não foi possível armazenar o objeto privado no S3."
            ) from exc
        finally:
            if isinstance(source, Path) and handle is not None:
                handle.close()

    def promote_authorized_quarantine_object(
        self,
        quarantine_key: str,
        clean_key: str,
        *,
        authorization_granted: bool,
        checksum_sha256: str,
        content_type: str,
    ) -> str:
        """Copy one verified quarantine object to its immutable clean logical key."""
        if not authorization_granted:
            raise StorageUnavailableError("Promoção privada exige veredito autorizado.")
        source_key = self._validate_private_key(quarantine_key, state="quarantine")
        destination_key = self._validate_private_key(clean_key, state="clean")
        checksum = self._validate_checksum(checksum_sha256)
        if checksum is None:
            raise ValueError("Checksum é obrigatório na promoção de quarentena.")
        if not self._require_private_storage_backend():
            raise StorageUnavailableError(
                "Promoção remota não é usada pelo adapter local."
            )
        copied = False
        try:
            source = self.client.head_object(
                Bucket=settings.S3_BUCKET_NAME,
                Key=source_key,
            )
            source_metadata = source.get("Metadata", {})
            if (
                not hmac.compare_digest(
                    str(source_metadata.get("sha256") or ""),
                    checksum,
                )
                or str(source_metadata.get("storage-state") or "") != "quarantine"
                or source.get("ServerSideEncryption") != "aws:kms"
            ):
                raise StorageUnavailableError(
                    "Recibo da quarentena diverge do veredito autorizado."
                )
            self.client.copy_object(
                Bucket=settings.S3_BUCKET_NAME,
                Key=destination_key,
                CopySource={"Bucket": settings.S3_BUCKET_NAME, "Key": source_key},
                ContentType=self._validate_content_type(content_type),
                MetadataDirective="REPLACE",
                Metadata=self._private_metadata(
                    state="clean",
                    checksum_sha256=checksum,
                ),
                **self._private_encryption_params(),
            )
            copied = True
            destination = self.client.head_object(
                Bucket=settings.S3_BUCKET_NAME,
                Key=destination_key,
            )
            destination_metadata = destination.get("Metadata", {})
            destination_valid = (
                hmac.compare_digest(
                    str(destination_metadata.get("sha256") or ""),
                    checksum,
                )
                and str(destination_metadata.get("storage-state") or "") == "clean"
                and destination.get("ServerSideEncryption") == "aws:kms"
                and int(destination.get("ContentLength", -1))
                == int(source.get("ContentLength", -2))
                and destination.get("ContentType")
                == self._validate_content_type(content_type)
            )
            if not destination_valid:
                raise StorageUnavailableError(
                    "Recibo do objeto promovido não confirmou integridade e SSE-KMS."
                )
            self.client.delete_object(Bucket=settings.S3_BUCKET_NAME, Key=source_key)
            return destination_key
        except StorageUnavailableError:
            if copied:
                try:
                    self.client.delete_object(
                        Bucket=settings.S3_BUCKET_NAME,
                        Key=destination_key,
                    )
                except (BotoCoreError, ClientError):
                    pass
            raise
        except (BotoCoreError, ClientError) as exc:
            if copied:
                try:
                    self.client.delete_object(
                        Bucket=settings.S3_BUCKET_NAME,
                        Key=destination_key,
                    )
                except (BotoCoreError, ClientError):
                    pass
            logger.error(
                "s3_private_promotion_error",
                error_type=safe_error_type(exc),
                object_ref=opaque_ref(destination_key, kind="storage_object"),
            )
            raise StorageUnavailableError(
                "Não foi possível promover o objeto limpo no S3."
            ) from exc

    def delete_authorized_private_object(
        self,
        s3_key: str,
        *,
        authorization_granted: bool,
    ) -> None:
        """Delete a private logical key only through an authorized lifecycle operation."""
        if not authorization_granted:
            raise StorageUnavailableError("Exclusão privada exige autorização persistida.")
        key = self._validate_private_key(s3_key)
        if not self._require_private_storage_backend():
            raise StorageUnavailableError(
                "Exclusão privada remota não é usada pelo adapter local."
            )
        try:
            self.client.delete_object(Bucket=settings.S3_BUCKET_NAME, Key=key)
        except (BotoCoreError, ClientError) as exc:
            logger.error(
                "s3_private_delete_error",
                error_type=safe_error_type(exc),
                object_ref=opaque_ref(key, kind="storage_object"),
            )
            raise StorageUnavailableError(
                "Não foi possível excluir o objeto privado no S3."
            ) from exc

    def verify_private_bucket(self) -> None:
        """Prove the production private-bucket invariants before serving documents.

        PublicAccessBlock alone is not enough: a private document bucket also needs
        an unambiguous non-public policy posture, default SSE-KMS, versioning,
        ownership enforcement and a bounded quarantine lifecycle.  This check is
        deliberately fail-closed because silently accepting a partially configured
        bucket would turn an infrastructure drift into a confidentiality or
        retention incident.
        """
        if not self._require_private_storage_backend():
            return
        if not settings.S3_PRIVATE_BUCKET_REQUIRED:
            return
        try:
            public_access = self.client.get_public_access_block(
                Bucket=settings.S3_BUCKET_NAME
            )
            policy_status = self.client.get_bucket_policy_status(
                Bucket=settings.S3_BUCKET_NAME
            )
            encryption = self.client.get_bucket_encryption(
                Bucket=settings.S3_BUCKET_NAME
            )
            versioning = self.client.get_bucket_versioning(
                Bucket=settings.S3_BUCKET_NAME
            )
            ownership = self.client.get_bucket_ownership_controls(
                Bucket=settings.S3_BUCKET_NAME
            )
            lifecycle = self.client.get_bucket_lifecycle_configuration(
                Bucket=settings.S3_BUCKET_NAME
            )
        except (BotoCoreError, ClientError) as exc:
            raise RuntimeError(
                "Não foi possível verificar todos os controles do bucket S3 privado."
            ) from exc

        block = public_access.get("PublicAccessBlockConfiguration", {})
        required = {
            "BlockPublicAcls",
            "IgnorePublicAcls",
            "BlockPublicPolicy",
            "RestrictPublicBuckets",
        }
        if not all(block.get(key) is True for key in required):
            raise RuntimeError("O bucket S3 deve manter o bloqueio de acesso público completo.")

        if policy_status.get("PolicyStatus", {}).get("IsPublic") is not False:
            raise RuntimeError("A política efetiva do bucket S3 privado não pode ser pública.")

        encryption_rules = encryption.get(
            "ServerSideEncryptionConfiguration", {}
        ).get("Rules", [])
        has_kms_default = any(
            rule.get("ApplyServerSideEncryptionByDefault", {}).get("SSEAlgorithm")
            == "aws:kms"
            and bool(
                rule.get("ApplyServerSideEncryptionByDefault", {}).get(
                    "KMSMasterKeyID"
                )
            )
            and rule.get("BucketKeyEnabled") is True
            for rule in encryption_rules
        )
        if not has_kms_default:
            raise RuntimeError(
                "O bucket S3 privado exige criptografia padrão SSE-KMS e Bucket Key."
            )

        if versioning.get("Status") != "Enabled":
            raise RuntimeError("O bucket S3 privado exige versionamento habilitado.")

        ownership_rules = ownership.get("OwnershipControls", {}).get("Rules", [])
        if not any(
            rule.get("ObjectOwnership") == "BucketOwnerEnforced"
            for rule in ownership_rules
        ):
            raise RuntimeError(
                "O bucket S3 privado exige Object Ownership BucketOwnerEnforced."
            )

        def _rule_prefix(rule: dict[str, Any]) -> str:
            direct = rule.get("Prefix")
            if isinstance(direct, str):
                return direct
            rule_filter = rule.get("Filter", {})
            if isinstance(rule_filter.get("Prefix"), str):
                return rule_filter["Prefix"]
            conjunction = rule_filter.get("And", {})
            if isinstance(conjunction.get("Prefix"), str):
                return conjunction["Prefix"]
            return ""

        has_quarantine_expiration = any(
            rule.get("Status") == "Enabled"
            and _rule_prefix(rule) == "quarantine/"
            and isinstance(rule.get("Expiration", {}).get("Days"), int)
            and 0 < rule["Expiration"]["Days"] <= 7
            for rule in lifecycle.get("Rules", [])
        )
        if not has_quarantine_expiration:
            raise RuntimeError(
                "O bucket S3 privado exige lifecycle de quarantine/ com expiração em até 7 dias."
            )

    def upload_bytes(
        self,
        data: bytes,
        key: str,
        content_type: str = "application/octet-stream",
    ) -> str:
        """Reject direct private writes that bypass the StorageObject lifecycle."""
        del data, key, content_type
        self._require_storage_backend()
        raise StorageUnavailableError(
            "Upload direto de bytes foi desabilitado; use o pipeline versionado StorageObject."
        )

    def delete_object(self, s3_key: str) -> bool:
        """Reject direct private deletes that bypass lifecycle and actor metadata."""
        del s3_key
        self._require_storage_backend()
        raise StorageUnavailableError(
            "Exclusões privadas exigem o ciclo de vida StorageObject."
        )

    def issue_local_object_ticket(
        self,
        *,
        operation: str,
        workspace_id: UUID,
        object_id: UUID,
        version_id: UUID,
        version: int,
        s3_key: str,
        content_type: str,
        expires_in: int,
        expected_byte_size: int | None = None,
        expected_sha256: str | None = None,
        ticket_epoch: int = 0,
    ) -> IssuedLocalStorageTicket:
        """Issue a short tenant/version-bound ticket for the local P1 backend."""
        if self._require_private_storage_backend():
            raise StorageUnavailableError("Tickets locais ficam desabilitados com S3 remoto ativo.")
        if operation not in _OBJECT_TICKET_PURPOSES:
            raise ValueError("Operação de ticket local inválida.")
        if not 30 <= expires_in <= 900:
            raise ValueError("Tickets locais devem expirar entre 30 e 900 segundos.")

        self.local_object_path(s3_key)
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=expires_in)
        jti = str(uuid.uuid4())
        payload = {
            "exp": expires_at,
            "iat": now,
            "jti": jti,
            "token_type": "local_storage",
            "sub": str(version_id),
            "operation": operation,
            "purpose": _OBJECT_TICKET_PURPOSES[operation],
            "workspace_id": str(workspace_id),
            "object_id": str(object_id),
            "version_id": str(version_id),
            "version": version,
            "s3_key": s3_key,
            "content_type": content_type,
            "expected_byte_size": expected_byte_size,
            "expected_sha256": expected_sha256,
            "ticket_epoch": ticket_epoch,
        }
        token = _encode_token(payload, "local_storage")
        decoded = verify_token(token)
        if decoded is None or decoded.get("jti") != jti:
            raise StorageUnavailableError("Não foi possível validar o ticket local emitido.")
        endpoint = {
            "object_upload": "/api/v1/local-storage/object-upload",
            "object_download": "/api/v1/local-storage/object-download",
            "media_download": "/api/v1/local-storage/media-download",
        }[operation]
        return IssuedLocalStorageTicket(
            url=self._local_ticket_url(endpoint, token),
            token=token,
            jti=jti,
            expires_at=expires_at,
        )

    @staticmethod
    def decode_local_object_ticket(ticket: str, *, operation: str) -> dict:
        """Validate a v2 local-object ticket and its required binding claims."""
        payload = verify_token(ticket)
        required = {
            "content_type",
            "workspace_id",
            "object_id",
            "version_id",
            "version",
            "s3_key",
            "jti",
            "sub",
            "purpose",
            "ticket_epoch",
        }
        expected_purpose = _OBJECT_TICKET_PURPOSES.get(operation)
        if (
            payload is None
            or expected_purpose is None
            or payload.get("token_type") != "local_storage"
            or payload.get("operation") != operation
            or payload.get("purpose") != expected_purpose
            or not required.issubset(payload)
            or payload.get("sub") != payload.get("version_id")
        ):
            raise ValueError("Ticket local inválido ou expirado.")
        try:
            UUID(str(payload["workspace_id"]))
            UUID(str(payload["object_id"]))
            UUID(str(payload["version_id"]))
            if int(payload["version"]) <= 0:
                raise ValueError
            if int(payload["ticket_epoch"]) < 0:
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise ValueError("Ticket local contém vínculos inválidos.") from exc
        S3Service.local_object_path(str(payload["s3_key"]))
        return payload

    @staticmethod
    def _local_ticket_url(endpoint: str, ticket: str) -> str:
        """Build, but never fetch, a configured same-service local URL."""
        if (
            not endpoint.startswith("/api/v1/local-storage/")
            or any(character in endpoint for character in ("?", "#", "\\"))
        ):
            raise StorageUnavailableError("Endpoint do armazenamento local inválido.")
        raw_base = settings.PAPERCLIP_PUBLIC_URL.strip()
        if any(ord(character) < 32 for character in raw_base):
            raise StorageUnavailableError("URL-base do armazenamento local inválida.")
        parsed = urlsplit(raw_base)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise StorageUnavailableError("URL-base do armazenamento local inválida.")

        hostname = parsed.hostname.casefold().rstrip(".")
        if hostname.isdecimal() or hostname in {
            "metadata.google.internal",
            "metadata.goog",
        }:
            raise StorageUnavailableError("Host de metadados não pode ser usado no armazenamento.")
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            address = None
        mapped_address = getattr(address, "ipv4_mapped", None)
        inspected_address = mapped_address or address
        if inspected_address and (
            inspected_address.is_link_local
            or inspected_address.is_multicast
            or inspected_address.is_reserved
            or inspected_address.is_unspecified
        ):
            raise StorageUnavailableError("Endereço inseguro na URL-base do armazenamento local.")

        base_path = parsed.path.rstrip("/")
        decoded_path = unquote(base_path)
        if "\\" in decoded_path or ".." in PurePosixPath(decoded_path).parts:
            raise StorageUnavailableError("Caminho inseguro na URL-base do armazenamento local.")
        normalized = urlunsplit((parsed.scheme, parsed.netloc, base_path, "", ""))
        return f"{normalized}{endpoint}?ticket={quote(ticket, safe='')}"

    @classmethod
    def local_object_path(cls, s3_key: str) -> Path:
        parts = cls._local_key_parts(s3_key)
        return cls.LOCAL_ROOT.absolute().joinpath(*parts)

    @staticmethod
    def _secure_fs_supported() -> bool:
        return bool(
            os.name == "posix"
            and hasattr(os, "O_NOFOLLOW")
            and hasattr(os, "O_DIRECTORY")
            and os.open in os.supports_dir_fd
            and os.mkdir in os.supports_dir_fd
            and os.unlink in os.supports_dir_fd
        )

    @classmethod
    def _require_secure_fs(cls) -> None:
        if not cls._secure_fs_supported():
            raise StorageUnavailableError(
                "Filesystem local não oferece openat/O_NOFOLLOW; operação bloqueada."
            )

    @staticmethod
    def _local_key_parts(s3_key: str) -> tuple[str, ...]:
        if not isinstance(s3_key, str) or not s3_key or "\x00" in s3_key:
            raise ValueError("Chave de armazenamento local inválida.")
        if "\\" in s3_key or "://" in s3_key or Path(s3_key).is_absolute():
            raise ValueError("Chave de armazenamento local inválida.")
        parts = tuple(s3_key.split("/"))
        if any(part in {"", ".", ".."} for part in parts):
            raise ValueError("Chave de armazenamento local inválida.")
        return parts

    @classmethod
    def _directory_flags(cls) -> int:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        return flags

    @classmethod
    def _open_root_fd(cls, *, create: bool) -> int:
        cls._require_secure_fs()
        configured_root = cls.LOCAL_ROOT.absolute()
        if configured_root.is_symlink():
            raise StorageUnavailableError("Raiz do armazenamento local não pode ser symlink.")
        root = configured_root.resolve(strict=False)
        if any(part == ".." for part in root.parts):
            raise StorageUnavailableError("Raiz do armazenamento local é inválida.")
        descriptor = os.open(root.anchor or "/", cls._directory_flags())
        try:
            for part in root.parts[1:]:
                try:
                    next_descriptor = os.open(part, cls._directory_flags(), dir_fd=descriptor)
                except FileNotFoundError:
                    if not create:
                        raise
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
                    next_descriptor = os.open(part, cls._directory_flags(), dir_fd=descriptor)
                os.close(descriptor)
                descriptor = next_descriptor
            return descriptor
        except Exception:
            os.close(descriptor)
            raise

    @classmethod
    @contextmanager
    def _open_parent_dir(
        cls,
        s3_key: str,
        *,
        create: bool,
    ) -> Iterator[tuple[int, str]]:
        parts = cls._local_key_parts(s3_key)
        descriptor = cls._open_root_fd(create=create)
        try:
            for part in parts[:-1]:
                try:
                    next_descriptor = os.open(part, cls._directory_flags(), dir_fd=descriptor)
                except FileNotFoundError:
                    if not create:
                        raise
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
                    next_descriptor = os.open(part, cls._directory_flags(), dir_fd=descriptor)
                os.close(descriptor)
                descriptor = next_descriptor
            yield descriptor, parts[-1]
        finally:
            os.close(descriptor)

    @classmethod
    def open_local_object(cls, s3_key: str) -> BinaryIO:
        flags = os.O_RDONLY | os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        with cls._open_parent_dir(s3_key, create=False) as (parent_fd, leaf):
            descriptor = os.open(leaf, flags, dir_fd=parent_fd)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise StorageUnavailableError("Objeto local não é um arquivo regular.")
            return os.fdopen(descriptor, "rb")
        except Exception:
            os.close(descriptor)
            raise

    @classmethod
    def create_local_object(cls, s3_key: str) -> BinaryIO:
        """Create one exclusive regular staging file and return its open handle."""
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        with cls._open_parent_dir(s3_key, create=True) as (parent_fd, leaf):
            descriptor = os.open(leaf, flags, 0o600, dir_fd=parent_fd)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise StorageUnavailableError("Staging local não é um arquivo regular.")
            return os.fdopen(descriptor, "wb")
        except Exception:
            os.close(descriptor)
            raise

    @classmethod
    def write_local_object_atomic(
        cls,
        s3_key: str,
        data: bytes | BinaryIO,
    ) -> None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        with cls._open_parent_dir(s3_key, create=True) as (parent_fd, leaf):
            temporary = f".{leaf}.{uuid.uuid4().hex}.tmp"
            descriptor = os.open(temporary, flags, 0o600, dir_fd=parent_fd)
            try:
                with os.fdopen(descriptor, "wb", closefd=False) as handle:
                    if isinstance(data, bytes):
                        handle.write(data)
                    else:
                        while chunk := data.read(1024 * 1024):
                            handle.write(chunk)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.close(descriptor)
                descriptor = -1
                os.replace(
                    temporary,
                    leaf,
                    src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd,
                )
                os.fsync(parent_fd)
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
                try:
                    os.unlink(temporary, dir_fd=parent_fd)
                except FileNotFoundError:
                    pass

    @classmethod
    def local_object_stat(cls, s3_key: str) -> os.stat_result:
        with cls.open_local_object(s3_key) as handle:
            return os.fstat(handle.fileno())

    @classmethod
    def local_object_exists(cls, s3_key: str) -> bool:
        try:
            cls.local_object_stat(s3_key)
        except FileNotFoundError:
            return False
        return True

    @classmethod
    def delete_local_object(cls, s3_key: str, *, missing_ok: bool = False) -> None:
        try:
            with cls._open_parent_dir(s3_key, create=False) as (parent_fd, leaf):
                item = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
                if not stat.S_ISREG(item.st_mode):
                    raise StorageUnavailableError("Objeto local não é um arquivo regular.")
                os.unlink(leaf, dir_fd=parent_fd)
                os.fsync(parent_fd)
        except FileNotFoundError:
            if not missing_ok:
                raise

    @classmethod
    def replace_local_object(cls, source_key: str, destination_key: str) -> None:
        with cls._open_parent_dir(source_key, create=False) as (source_fd, source_leaf):
            source_stat = os.stat(source_leaf, dir_fd=source_fd, follow_symlinks=False)
            if not stat.S_ISREG(source_stat.st_mode):
                raise StorageUnavailableError("Origem local não é um arquivo regular.")
            with cls._open_parent_dir(destination_key, create=True) as (
                destination_fd,
                destination_leaf,
            ):
                os.replace(
                    source_leaf,
                    destination_leaf,
                    src_dir_fd=source_fd,
                    dst_dir_fd=destination_fd,
                )
                os.fsync(destination_fd)

    @classmethod
    def iter_local_objects(cls, prefix: str) -> Iterator[tuple[str, os.stat_result]]:
        """Yield regular files below a validated prefix without following links."""
        prefix_parts = cls._local_key_parts(prefix)
        root_fd = cls._open_root_fd(create=False)
        descriptor = root_fd
        try:
            for part in prefix_parts:
                next_descriptor = os.open(part, cls._directory_flags(), dir_fd=descriptor)
                if descriptor != root_fd:
                    os.close(descriptor)
                descriptor = next_descriptor

            def walk(directory_fd: int, relative: tuple[str, ...]) -> Iterator[tuple[str, Any]]:
                with os.scandir(directory_fd) as entries:
                    for entry in entries:
                        if entry.is_symlink():
                            raise StorageUnavailableError(
                                "Symlink detectado dentro do armazenamento local."
                            )
                        child_parts = relative + (entry.name,)
                        if entry.is_dir(follow_symlinks=False):
                            child_fd = os.open(
                                entry.name,
                                cls._directory_flags(),
                                dir_fd=directory_fd,
                            )
                            try:
                                yield from walk(child_fd, child_parts)
                            finally:
                                os.close(child_fd)
                        elif entry.is_file(follow_symlinks=False):
                            key = "/".join(prefix_parts + child_parts)
                            yield key, entry.stat(follow_symlinks=False)

            yield from walk(descriptor, ())
        except FileNotFoundError:
            return
        finally:
            if descriptor != root_fd:
                os.close(descriptor)
            os.close(root_fd)
