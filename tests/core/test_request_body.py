from __future__ import annotations

import io
import zipfile
from collections.abc import Iterable

import pytest
from fastapi import HTTPException, UploadFile
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request

from app.core.request_body import (
    ArchiveSafetyLimits,
    RouteBodyLimitMiddleware,
    UnsafeArchiveError,
    inspect_zip_archive,
    read_bounded_body,
    read_bounded_upload,
)


def _request(chunks: Iterable[bytes], *, content_length: str | None = None) -> Request:
    pending = list(chunks)

    async def receive() -> dict[str, object]:
        body = pending.pop(0) if pending else b""
        return {
            "type": "http.request",
            "body": body,
            "more_body": bool(pending),
        }

    headers = []
    if content_length is not None:
        headers.append((b"content-length", content_length.encode("ascii")))
    return Request({"type": "http", "method": "POST", "path": "/", "headers": headers}, receive)


@pytest.mark.asyncio
async def test_bounded_body_preserves_exact_bytes_and_request_cache() -> None:
    request = _request([b'{"value":', b'"signed.raw"}'])

    body = await read_bounded_body(request, max_bytes=64)

    assert body == b'{"value":"signed.raw"}'
    assert await request.body() == body
    assert await request.json() == {"value": "signed.raw"}


@pytest.mark.asyncio
@pytest.mark.parametrize("declared", ["invalid", "-1"])
async def test_bounded_body_rejects_invalid_content_length(declared: str) -> None:
    request = _request([b"{}"], content_length=declared)

    with pytest.raises(HTTPException) as captured:
        await read_bounded_body(request, max_bytes=8)

    assert captured.value.status_code == 400


@pytest.mark.asyncio
async def test_bounded_body_rejects_declared_excess_before_consuming_stream() -> None:
    request = _request([b"small"], content_length="9")

    with pytest.raises(HTTPException) as captured:
        await read_bounded_body(request, max_bytes=8)

    assert captured.value.status_code == 413


@pytest.mark.asyncio
async def test_bounded_body_rejects_chunked_excess() -> None:
    request = _request([b"1234", b"56789"])

    with pytest.raises(HTTPException) as captured:
        await read_bounded_body(request, max_bytes=8)

    assert captured.value.status_code == 413


async def _invoke_limited_asgi(
    chunks: list[bytes],
    *,
    content_length: str | None = None,
) -> tuple[list[dict[str, object]], list[bytes]]:
    observed: list[bytes] = []

    async def inner(_scope, receive, send):
        while True:
            message = await receive()
            observed.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    app = RouteBodyLimitMiddleware(
        inner,
        route_limits={("POST", "/api/v1/legal/documents/upload"): 8},
    )
    pending = list(chunks)

    async def receive() -> dict[str, object]:
        body = pending.pop(0) if pending else b""
        return {
            "type": "http.request",
            "body": body,
            "more_body": bool(pending),
        }

    sent: list[dict[str, object]] = []

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    headers = []
    if content_length is not None:
        headers.append((b"content-length", content_length.encode("ascii")))
    await app(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/legal/documents/upload",
            "headers": headers,
        },
        receive,
        send,
    )
    return sent, observed


@pytest.mark.asyncio
async def test_route_body_limit_rejects_declared_size_before_app_reads() -> None:
    sent, observed = await _invoke_limited_asgi([b"tiny"], content_length="9")

    assert sent[0]["status"] == 413
    assert observed == []


@pytest.mark.asyncio
async def test_route_body_limit_rejects_chunked_size_before_parser_completes() -> None:
    sent, observed = await _invoke_limited_asgi([b"1234", b"56789"])

    assert sent[0]["status"] == 413
    assert observed == [b"1234"]


@pytest.mark.asyncio
async def test_route_body_limit_accepts_exact_limit() -> None:
    sent, observed = await _invoke_limited_asgi([b"1234", b"5678"], content_length="8")

    assert sent[0]["status"] == 204
    assert observed == [b"1234", b"5678"]


@pytest.mark.asyncio
async def test_route_body_limit_matches_fastapi_path_parameters() -> None:
    observed: list[bytes] = []

    async def inner(_scope, receive, send):
        observed.append((await receive()).get("body", b""))
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    app = RouteBodyLimitMiddleware(
        inner,
        route_limits={
            (
                "POST",
                "/api/v1/legal/demands/{demand_id}/artifacts/{artifact_id}/reimport",
            ): 4,
        },
    )
    sent: list[dict[str, object]] = []

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"12345", "more_body": False}

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    await app(
        {
            "type": "http",
            "method": "POST",
            "path": ("/api/v1/legal/demands/6ad3/artifacts/82cc/reimport"),
            "headers": [],
        },
        receive,
        send,
    )

    assert sent[0]["status"] == 413
    assert observed == []


@pytest.mark.asyncio
async def test_bounded_upload_reads_in_chunks_and_accepts_exact_limit() -> None:
    upload = UploadFile(filename="limite.csv", file=io.BytesIO(b"12345678"))

    content = await read_bounded_upload(upload, max_bytes=8, chunk_bytes=3)

    assert content == b"12345678"


@pytest.mark.asyncio
async def test_bounded_upload_rejects_stream_without_content_length() -> None:
    upload = UploadFile(filename="grande.csv", file=io.BytesIO(b"123456789"))

    with pytest.raises(HTTPException) as captured:
        await read_bounded_upload(upload, max_bytes=8, chunk_bytes=3)

    assert captured.value.status_code == 413
    assert captured.value.detail["code"] == "UPLOAD_FILE_TOO_LARGE"


def _zip(entries: list[tuple[str, bytes]], *, compression: int) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=compression) as archive:
        for name, payload in entries:
            archive.writestr(name, payload)
    return stream.getvalue()


def test_archive_safety_accepts_entry_total_and_ratio_exactly_at_limit() -> None:
    payload = _zip(
        [("a.txt", b"1234"), ("b.txt", b"5678")],
        compression=zipfile.ZIP_STORED,
    )

    result = inspect_zip_archive(
        payload,
        limits=ArchiveSafetyLimits(
            max_entries=2,
            max_uncompressed_bytes=8,
            max_compression_ratio=1.0,
            max_entry_uncompressed_bytes=4,
        ),
    )

    assert result.entry_count == 2
    assert result.uncompressed_bytes == 8


def test_archive_safety_rejects_entry_count_total_and_compression_bombs() -> None:
    with pytest.raises(UnsafeArchiveError, match="entradas"):
        inspect_zip_archive(
            _zip(
                [("a.txt", b"a"), ("b.txt", b"b")],
                compression=zipfile.ZIP_STORED,
            ),
            limits=ArchiveSafetyLimits(
                max_entries=1,
                max_uncompressed_bytes=16,
                max_compression_ratio=100,
            ),
        )

    with pytest.raises(UnsafeArchiveError, match="descompactado"):
        inspect_zip_archive(
            _zip([("a.txt", b"12345")], compression=zipfile.ZIP_STORED),
            limits=ArchiveSafetyLimits(
                max_entries=1,
                max_uncompressed_bytes=4,
                max_compression_ratio=100,
            ),
        )

    with pytest.raises(UnsafeArchiveError, match="compressão"):
        inspect_zip_archive(
            _zip([("bomb.txt", b"0" * 100_000)], compression=zipfile.ZIP_DEFLATED),
            limits=ArchiveSafetyLimits(
                max_entries=1,
                max_uncompressed_bytes=100_000,
                max_compression_ratio=2,
            ),
        )


def test_main_registers_pre_parser_limits_for_all_import_surfaces() -> None:
    from app.main import app

    middleware = next(item for item in app.user_middleware if item.cls is RouteBodyLimitMiddleware)
    routes = middleware.kwargs["route_limits"]

    assert ("POST", "/api/v1/finance/reconciliation/import/ofx") in routes
    assert ("POST", "/api/v1/finance/reconciliation/import/csv") in routes
    assert ("POST", "/api/v1/finance/harmonization/runs") in routes
    assert ("POST", "/api/v1/migration/jobs") in routes
    assert (
        "POST",
        "/api/v1/legal/demands/{demand_id}/artifacts/{artifact_id}/reimport",
    ) in routes


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,configured_path",
    [
        (
            "/api/v1/finance/reconciliation/import/ofx",
            "/api/v1/finance/reconciliation/import/ofx",
        ),
        (
            "/api/v1/finance/reconciliation/import/csv",
            "/api/v1/finance/reconciliation/import/csv",
        ),
        (
            "/api/v1/finance/harmonization/runs",
            "/api/v1/finance/harmonization/runs",
        ),
        ("/api/v1/migration/jobs", "/api/v1/migration/jobs"),
        (
            (
                "/api/v1/legal/demands/11111111-1111-1111-1111-111111111111/"
                "artifacts/22222222-2222-2222-2222-222222222222/reimport"
            ),
            "/api/v1/legal/demands/{demand_id}/artifacts/{artifact_id}/reimport",
        ),
    ],
)
async def test_main_rejects_declared_oversize_before_auth_or_multipart_parser(
    path: str,
    configured_path: str,
) -> None:
    from app.main import app

    middleware = next(item for item in app.user_middleware if item.cls is RouteBodyLimitMiddleware)
    limit = middleware.kwargs["route_limits"][("POST", configured_path)]
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            path,
            content=b"x",
            headers={"Content-Length": str(limit + 1)},
        )

    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "REQUEST_BODY_TOO_LARGE"
