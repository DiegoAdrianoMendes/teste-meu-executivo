#!/usr/bin/env python3
"""Benchmark KLM hot GET endpoints against a loopback-only local runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import socket
import statistics
import time
from collections.abc import Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / ".local/qa-evidence/saas-v5-p5/hot-endpoints.json"
DEFAULT_BASE_URL = "http://127.0.0.1:18080"
DEFAULT_ENDPOINTS = (
    "/health",
    "/api/v1/status",
    "/api/v1/status/history",
    "/api/v1/klm/runtime",
)


class BenchmarkError(RuntimeError):
    """Raised when the benchmark cannot remain local, authenticated, and valid."""


def _is_loopback_host(host: str | None) -> bool:
    return host in {"127.0.0.1", "localhost", "::1"}


def _validate_base_url(value: str) -> str:
    parsed = urlsplit(value.rstrip("/"))
    if parsed.scheme not in {"http", "https"} or not _is_loopback_host(parsed.hostname):
        raise BenchmarkError("The benchmark base URL must be loopback-only.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise BenchmarkError("The benchmark base URL cannot contain credentials or parameters.")
    return value.rstrip("/")


def _validate_endpoint(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or not value.startswith("/") or value.startswith("//"):
        raise BenchmarkError(f"Endpoint must be a local absolute path: {value!r}")
    return value


@contextmanager
def _loopback_socket_guard() -> Iterator[dict[str, int]]:
    original = socket.create_connection
    counters = {"loopbackConnections": 0, "externalNetworkRequests": 0}
    lock = Lock()

    def guarded(address: tuple[str, int], *args: Any, **kwargs: Any) -> socket.socket:
        host = str(address[0])
        if not _is_loopback_host(host):
            with lock:
                counters["externalNetworkRequests"] += 1
            raise BenchmarkError(f"Blocked non-loopback socket connection: {host}")
        with lock:
            counters["loopbackConnections"] += 1
        return original(address, *args, **kwargs)

    socket.create_connection = guarded
    try:
        yield counters
    finally:
        socket.create_connection = original


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        raise BenchmarkError("Cannot calculate a percentile without samples.")
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * percentile) - 1)
    return ordered[index]


def _summary(values: Sequence[float]) -> dict[str, float]:
    return {
        "meanMs": round(statistics.fmean(values), 3),
        "p50Ms": round(_percentile(values, 0.50), 3),
        "p95Ms": round(_percentile(values, 0.95), 3),
        "p99Ms": round(_percentile(values, 0.99), 3),
        "maxMs": round(max(values), 3),
    }


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _source_digest() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def run_benchmark(
    *,
    base_url: str = DEFAULT_BASE_URL,
    endpoints: Sequence[str] = DEFAULT_ENDPOINTS,
    requests_per_endpoint: int = 80,
    warmup_requests: int = 5,
    workers: int = 8,
    threshold_p95_ms: float = 300.0,
    timeout_seconds: float = 10.0,
    authenticated: bool = False,
) -> dict[str, Any]:
    base_url = _validate_base_url(base_url)
    endpoint_paths = tuple(_validate_endpoint(value) for value in endpoints)
    if not endpoint_paths or len(set(endpoint_paths)) != len(endpoint_paths):
        raise BenchmarkError("At least one unique endpoint is required.")
    if requests_per_endpoint < 20 or warmup_requests < 0:
        raise BenchmarkError("Use at least 20 measured requests and a non-negative warmup.")
    if workers < 1 or workers > 32:
        raise BenchmarkError("Workers must stay between 1 and 32.")
    if threshold_p95_ms <= 0 or timeout_seconds <= 0:
        raise BenchmarkError("Threshold and timeout must be positive.")

    limits = httpx.Limits(max_connections=workers, max_keepalive_connections=workers)
    timeout = httpx.Timeout(timeout_seconds)
    started = time.perf_counter()
    with (
        _loopback_socket_guard() as network,
        httpx.Client(
            base_url=base_url,
            timeout=timeout,
            limits=limits,
            trust_env=False,
            follow_redirects=False,
        ) as client,
    ):
        headers: dict[str, str] = {}
        if authenticated:
            session = client.post("/api/v1/klm/local/session")
            if session.status_code != 200:
                raise BenchmarkError(f"Local session returned HTTP {session.status_code}.")
            session_payload = session.json()
            token = session_payload.get("accessToken") or session_payload.get("access_token")
            if not isinstance(token, str) or not token:
                raise BenchmarkError("Local session did not return an access token.")
            headers = {"Authorization": f"Bearer {token}"}

        endpoint_results: list[dict[str, Any]] = []
        all_values: list[float] = []
        for endpoint in endpoint_paths:
            for _ in range(warmup_requests):
                warmup = client.get(endpoint, headers=headers)
                if warmup.status_code != 200:
                    raise BenchmarkError(
                        f"Warmup for {endpoint} returned HTTP {warmup.status_code}."
                    )

            def measured_request(_index: int) -> float:
                request_started = time.perf_counter_ns()
                response = client.get(endpoint, headers=headers)
                elapsed_ms = (time.perf_counter_ns() - request_started) / 1_000_000
                if response.status_code != 200:
                    raise BenchmarkError(
                        f"Measured request for {endpoint} returned HTTP {response.status_code}."
                    )
                return elapsed_ms

            with ThreadPoolExecutor(max_workers=workers) as pool:
                values = list(pool.map(measured_request, range(requests_per_endpoint)))
            metrics = _summary(values)
            metrics.update(
                {
                    "path": endpoint,
                    "samples": len(values),
                    "thresholdP95Ms": threshold_p95_ms,
                    "passed": metrics["p95Ms"] < threshold_p95_ms,
                }
            )
            endpoint_results.append(metrics)
            all_values.extend(values)

    aggregate = _summary(all_values)
    aggregate.update(
        {
            "samples": len(all_values),
            "thresholdP95Ms": threshold_p95_ms,
            "passed": aggregate["p95Ms"] < threshold_p95_ms,
        }
    )
    passed = (
        all(item["passed"] for item in endpoint_results)
        and aggregate["passed"]
        and network["externalNetworkRequests"] == 0
    )
    return {
        "schemaVersion": "1.0",
        "kind": "klm-saas-v5-local-hot-endpoint-benchmark",
        "status": "passed" if passed else "failed",
        "generatedAt": datetime.now(UTC).isoformat(),
        "localOnly": True,
        "baseUrl": base_url,
        "profile": {
            "workers": workers,
            "requestsPerEndpoint": requests_per_endpoint,
            "warmupRequestsPerEndpoint": warmup_requests,
            "timeoutSeconds": timeout_seconds,
            "readOnlyMeasuredRequests": True,
            "authenticated": authenticated,
        },
        "network": network,
        "durationMs": round((time.perf_counter() - started) * 1_000, 3),
        "aggregate": aggregate,
        "endpoints": endpoint_results,
        "sourceDigest": _source_digest(),
        "claims": {
            "localLoadProfileVerified": passed,
            "externalProviderVerified": False,
            "productionVerified": False,
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--endpoint", action="append", default=[])
    parser.add_argument("--requests", type=int, default=80, dest="requests_per_endpoint")
    parser.add_argument("--warmup", type=int, default=5, dest="warmup_requests")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--threshold-p95-ms", type=float, default=300.0)
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    parser.add_argument("--authenticated", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = run_benchmark(
            base_url=args.base_url,
            endpoints=args.endpoint or DEFAULT_ENDPOINTS,
            requests_per_endpoint=args.requests_per_endpoint,
            warmup_requests=args.warmup_requests,
            workers=args.workers,
            threshold_p95_ms=args.threshold_p95_ms,
            timeout_seconds=args.timeout_seconds,
            authenticated=args.authenticated,
        )
    except (BenchmarkError, httpx.HTTPError) as error:
        print(f"LOCAL_LOAD_BENCHMARK_REFUSED: {error}")
        return 2
    output = args.output if args.output.is_absolute() else ROOT / args.output
    _atomic_write(output, payload)
    print(
        json.dumps(
            {
                "status": payload["status"],
                "samples": payload["aggregate"]["samples"],
                "p95Ms": payload["aggregate"]["p95Ms"],
                "externalNetworkRequests": payload["network"]["externalNetworkRequests"],
                "output": str(output),
            },
            sort_keys=True,
        )
    )
    return 0 if payload["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
