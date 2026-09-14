"""Bounded request-body reads for public ingress and file-import boundaries."""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from fastapi import HTTPException, Request, UploadFile, status
from starlette.types import ASGIApp, Message, Receive, Scope, Send

DEFAULT_PUBLIC_BODY_LIMIT_BYTES = 2 * 1024 * 1024
DEFAULT_UPLOAD_READ_CHUNK_BYTES = 1024 * 1024


class _BodyLimitExceededError(Exception):
    pass


class UnsafeArchiveError(ValueError):
    """Raised before any compressed member is expanded or parsed."""


@dataclass(frozen=True)
class ArchiveSafetyLimits:
    max_entries: int
    max_uncompressed_bytes: int
    max_compression_ratio: float
    max_entry_uncompressed_bytes: int | None = None

    def __post_init__(self) -> None:
        if self.max_entries <= 0:
            raise ValueError("max_entries must be positive")
        if self.max_uncompressed_bytes <= 0:
            raise ValueError("max_uncompressed_bytes must be positive")
        if self.max_compression_ratio <= 0:
            raise ValueError("max_compression_ratio must be positive")
        if self.max_entry_uncompressed_bytes is not None and self.max_entry_uncompressed_bytes <= 0:
            raise ValueError("max_entry_uncompressed_bytes must be positive")


@dataclass(frozen=True)
class ArchiveSafetyInspection:
    entry_count: int
    compressed_bytes: int
    uncompressed_bytes: int
    names: frozenset[str]


def _path_matches(pattern: str, path: str) -> bool:
    """Match literal paths and FastAPI-style ``/{parameter}`` segments."""

    if pattern == path:
        return True
    pattern_segments = pattern.strip("/").split("/")
    path_segments = path.strip("/").split("/")
    if len(pattern_segments) != len(path_segments):
        return False
    return all(
        bool(actual) if expected.startswith("{") and expected.endswith("}") else expected == actual
        for expected, actual in zip(pattern_segments, path_segments, strict=True)
    )


class RouteBodyLimitMiddleware:
    """Reject selected HTTP bodies at the ASGI receive boundary.

    FastAPI resolves ``UploadFile`` only after Starlette has parsed and possibly
    spooled the complete multipart payload.  An endpoint-level ``file.read``
    limit is therefore too late to protect worker memory/disk.  This pure ASGI
    middleware counts network chunks before the multipart parser observes them.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        route_limits: Mapping[tuple[str, str], int],
    ) -> None:
        self.app = app
        self.route_limits = {
            (method.upper(), path): int(limit) for (method, path), limit in route_limits.items()
        }
        if any(limit <= 0 for limit in self.route_limits.values()):
            raise ValueError("route body limits must be positive")

    def _limit_for(self, method: str, path: str) -> int | None:
        exact = self.route_limits.get((method, path))
        if exact is not None:
            return exact
        matches = [
            limit
            for (configured_method, pattern), limit in self.route_limits.items()
            if configured_method == method and _path_matches(pattern, path)
        ]
        # A more permissive overlapping pattern must never weaken a narrower
        # route boundary.
        return min(matches) if matches else None

    @staticmethod
    async def _reject(
        send: Send,
        *,
        status_code: int,
        code: str,
        message: str,
        max_bytes: int | None = None,
    ) -> None:
        payload: dict[str, Any] = {"code": code, "message": message}
        if max_bytes is not None:
            payload["maxBytes"] = max_bytes
        body = json.dumps({"detail": payload}, separators=(",", ":")).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status_code,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        method = str(scope.get("method") or "").upper()
        path = str(scope.get("path") or "")
        limit = self._limit_for(method, path)
        if limit is None:
            await self.app(scope, receive, send)
            return

        declared_length: str | None = None
        for name, value in scope.get("headers", []):
            if name.lower() == b"content-length":
                declared_length = value.decode("ascii", errors="replace").strip()
                break
        if declared_length:
            try:
                parsed_length = int(declared_length)
            except ValueError:
                await self._reject(
                    send,
                    status_code=status.HTTP_400_BAD_REQUEST,
                    code="REQUEST_CONTENT_LENGTH_INVALID",
                    message="Content-Length inválido.",
                )
                return
            if parsed_length < 0:
                await self._reject(
                    send,
                    status_code=status.HTTP_400_BAD_REQUEST,
                    code="REQUEST_CONTENT_LENGTH_INVALID",
                    message="Content-Length inválido.",
                )
                return
            if parsed_length > limit:
                await self._reject(
                    send,
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    code="REQUEST_BODY_TOO_LARGE",
                    message="Payload excede o limite permitido.",
                    max_bytes=limit,
                )
                return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _BodyLimitExceededError
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _BodyLimitExceededError:
            await self._reject(
                send,
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                code="REQUEST_BODY_TOO_LARGE",
                message="Payload excede o limite permitido.",
                max_bytes=limit,
            )


async def read_bounded_upload(
    file: UploadFile,
    *,
    max_bytes: int,
    chunk_bytes: int = DEFAULT_UPLOAD_READ_CHUNK_BYTES,
    too_large_detail: Any | None = None,
) -> bytes:
    """Read an already-parsed upload in bounded chunks as defence in depth.

    ``RouteBodyLimitMiddleware`` is the pre-parser protection. This helper
    keeps direct endpoint/service tests and any future router composition from
    reintroducing an unbounded ``UploadFile.read()``.
    """

    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    if chunk_bytes <= 0:
        raise ValueError("chunk_bytes must be positive")
    detail = too_large_detail or {
        "code": "UPLOAD_FILE_TOO_LARGE",
        "message": "Arquivo excede o limite permitido.",
        "maxBytes": max_bytes,
    }
    payload = bytearray()
    while True:
        chunk = await file.read(min(chunk_bytes, max_bytes - len(payload) + 1))
        if not chunk:
            break
        payload.extend(chunk)
        if len(payload) > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=detail,
            )
    return bytes(payload)


def inspect_zip_archive(
    content: bytes,
    *,
    limits: ArchiveSafetyLimits,
) -> ArchiveSafetyInspection:
    """Inspect a ZIP/XLSX/DOCX central directory without expanding members."""

    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > limits.max_entries:
                raise UnsafeArchiveError("Arquivo compactado excede o limite de entradas.")

            names: set[str] = set()
            total_compressed = 0
            total_uncompressed = 0
            for entry in entries:
                normalized = entry.filename.replace("\\", "/")
                path = PurePosixPath(normalized)
                if (
                    not normalized
                    or normalized.startswith("/")
                    or path.is_absolute()
                    or ".." in path.parts
                ):
                    raise UnsafeArchiveError("Arquivo compactado contém caminho interno inseguro.")
                if normalized in names:
                    raise UnsafeArchiveError("Arquivo compactado contém entradas duplicadas.")
                names.add(normalized)
                if entry.flag_bits & 0x1:
                    raise UnsafeArchiveError("Arquivo compactado criptografado não é aceito.")
                if (
                    limits.max_entry_uncompressed_bytes is not None
                    and entry.file_size > limits.max_entry_uncompressed_bytes
                ):
                    raise UnsafeArchiveError("Arquivo compactado contém uma entrada grande demais.")

                total_compressed += entry.compress_size
                total_uncompressed += entry.file_size
                if total_uncompressed > limits.max_uncompressed_bytes:
                    raise UnsafeArchiveError("Arquivo compactado excede o limite descompactado.")
                if entry.file_size and entry.compress_size <= 0:
                    raise UnsafeArchiveError(
                        "Arquivo compactado possui razão de compressão insegura."
                    )
                if (
                    entry.compress_size > 0
                    and entry.file_size / entry.compress_size > limits.max_compression_ratio
                ):
                    raise UnsafeArchiveError(
                        "Arquivo compactado possui razão de compressão insegura."
                    )

            if total_uncompressed and total_compressed <= 0:
                raise UnsafeArchiveError("Arquivo compactado possui razão de compressão insegura.")
            if (
                total_compressed > 0
                and total_uncompressed / total_compressed > limits.max_compression_ratio
            ):
                raise UnsafeArchiveError("Arquivo compactado possui razão de compressão insegura.")
    except zipfile.BadZipFile as exc:
        raise UnsafeArchiveError("Arquivo compactado inválido ou corrompido.") from exc

    return ArchiveSafetyInspection(
        entry_count=len(entries),
        compressed_bytes=total_compressed,
        uncompressed_bytes=total_uncompressed,
        names=frozenset(names),
    )


async def read_bounded_body(
    request: Request,
    *,
    max_bytes: int = DEFAULT_PUBLIC_BODY_LIMIT_BYTES,
    too_large_detail: Any = "Payload excede o limite de 2 MiB.",
    invalid_length_detail: Any = "Content-Length inválido.",
) -> bytes:
    """Read exact raw bytes without allowing an unbounded in-memory body.

    The safely accumulated body is cached on Starlette's Request just as
    ``Request.body()`` would, so existing downstream ``json()`` and ``form()``
    consumers keep working without consuming the network stream twice.
    """
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")

    declared_length = request.headers.get("content-length", "").strip()
    if declared_length:
        try:
            parsed_length = int(declared_length)
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=invalid_length_detail,
            ) from error
        if parsed_length < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=invalid_length_detail,
            )
        if parsed_length > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=too_large_detail,
            )

    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=too_large_detail,
            )

    result = bytes(body)
    # Starlette's public Request API has no bounded-body primitive. Caching the
    # already-bounded bytes preserves the semantics of Request.body() for code
    # that subsequently calls request.json() or request.form().
    request._body = result  # type: ignore[attr-defined]
    return result
