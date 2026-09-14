from __future__ import annotations

import time
from pathlib import Path
from threading import Lock
from typing import Any

import pytest

from scripts import benchmark_local_hot_endpoints as benchmark


class FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeClient:
    def __init__(self, *args: Any, delay_seconds: float = 0.0, **kwargs: Any) -> None:
        self.delay_seconds = delay_seconds
        self.paths: list[str] = []
        self._lock = Lock()

    def __enter__(self) -> FakeClient:
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def post(self, path: str) -> FakeResponse:
        assert path == "/api/v1/klm/local/session"
        return FakeResponse(200, {"accessToken": "local-test-token"})

    def get(self, path: str, *, headers: dict[str, str]) -> FakeResponse:
        assert headers == {"Authorization": "Bearer local-test-token"}
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        with self._lock:
            self.paths.append(path)
        return FakeResponse(200)


def test_percentile_uses_nearest_rank() -> None:
    assert benchmark._percentile([1.0, 2.0, 3.0, 4.0, 100.0], 0.95) == 100.0
    assert benchmark._summary([1.0, 2.0, 3.0])["p50Ms"] == 2.0


@pytest.mark.parametrize(
    "value",
    ("https://example.com", "http://10.0.0.1:8000", "file:///tmp/klm"),
)
def test_base_url_rejects_every_non_loopback_target(value: str) -> None:
    with pytest.raises(benchmark.BenchmarkError, match="loopback-only"):
        benchmark._validate_base_url(value)


def test_benchmark_covers_every_combination_without_external_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeClient()
    monkeypatch.setattr(benchmark.httpx, "Client", lambda *args, **kwargs: fake)

    result = benchmark.run_benchmark(
        base_url="http://127.0.0.1:18080",
        endpoints=("/health", "/api/v1/status"),
        requests_per_endpoint=20,
        warmup_requests=2,
        workers=4,
        threshold_p95_ms=300,
        authenticated=True,
    )

    assert result["status"] == "passed"
    assert result["aggregate"]["samples"] == 40
    assert result["network"]["externalNetworkRequests"] == 0
    assert fake.paths.count("/health") == 22
    assert fake.paths.count("/api/v1/status") == 22
    assert result["claims"]["productionVerified"] is False


def test_cli_writes_failed_evidence_when_p95_exceeds_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeClient(delay_seconds=0.004)
    monkeypatch.setattr(benchmark.httpx, "Client", lambda *args, **kwargs: fake)
    output = tmp_path / "load.json"

    exit_code = benchmark.main(
        (
            "--endpoint",
            "/health",
            "--requests",
            "20",
            "--warmup",
            "0",
            "--workers",
            "2",
            "--threshold-p95-ms",
            "1",
            "--authenticated",
            "--output",
            str(output),
        )
    )

    assert exit_code == 1
    assert output.is_file()
    assert '"status": "failed"' in output.read_text(encoding="utf-8")
