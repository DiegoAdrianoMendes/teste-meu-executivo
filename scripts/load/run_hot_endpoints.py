#!/usr/bin/env python3
"""Load test em Python puro (asyncio + httpx) dos endpoints quentes do KLM Hub,
fallback local para k6/locust (nenhum dos dois estava instalado neste ambiente
em 2026-07-23: `which k6 locust` -> not found).

Mede p50/p95/p99 de:
  - POST /api/v1/auth/login
  - GET  /api/v1/klm/omnichannel/events           (inbox)
  - GET  /api/v1/klm/dashboard                    (dashboard)
  - GET  /api/v1/klm/tracking/summary             (tracking)

Valida a meta de p95 < 300ms por endpoint (--threshold-ms, default 300) e sai
com código != 0 se algum endpoint estourar a meta ou tiver erro HTTP.

Só aceita host loopback (127.0.0.1/localhost) — nunca aponta para produção.

Uso típico (contra a worktree isolada, NUNCA na porta 8010 se o repo
principal já estiver rodando lá):
    .venv/bin/python scripts/run_local.py --host 127.0.0.1 --port 8011 &
    .venv/bin/python scripts/seed.py
    .venv/bin/python scripts/load/set_local_test_password.py
    .venv/bin/python scripts/load/run_hot_endpoints.py --base-url http://127.0.0.1:8011
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / ".local/qa-evidence/ops-prod/load-test-hot-endpoints.json"


def _is_loopback(host: str | None) -> bool:
    return host in {"127.0.0.1", "localhost", "::1"}


def _validate_base_url(value: str) -> str:
    parsed = urlsplit(value.rstrip("/"))
    if parsed.scheme not in {"http", "https"} or not _is_loopback(parsed.hostname):
        raise SystemExit("--base-url precisa ser loopback (127.0.0.1/localhost).")
    return value.rstrip("/")


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return math.nan
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * pct) - 1)
    return ordered[index]


def _summary(values: list[float]) -> dict[str, float]:
    return {
        "count": len(values),
        "meanMs": round(statistics.fmean(values), 2) if values else math.nan,
        "p50Ms": round(_percentile(values, 0.50), 2),
        "p95Ms": round(_percentile(values, 0.95), 2),
        "p99Ms": round(_percentile(values, 0.99), 2),
        "maxMs": round(max(values), 2) if values else math.nan,
    }


@dataclass
class EndpointResult:
    name: str
    latencies_ms: list[float] = field(default_factory=list)
    errors: int = 0
    statuses: dict[int, int] = field(default_factory=dict)


async def _login_and_get_token(
    client: httpx.AsyncClient, email: str, password: str
) -> tuple[str, float]:
    start = time.perf_counter()
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    elapsed = (time.perf_counter() - start) * 1000
    resp.raise_for_status()
    return resp.json()["accessToken"], elapsed


async def _timed_get(client: httpx.AsyncClient, path: str, headers: dict[str, str]) -> tuple[int, float]:
    start = time.perf_counter()
    resp = await client.get(path, headers=headers)
    elapsed = (time.perf_counter() - start) * 1000
    return resp.status_code, elapsed


async def _worker(
    *,
    client: httpx.AsyncClient,
    email: str,
    password: str,
    iterations: int,
    login_result: EndpointResult,
    inbox_result: EndpointResult,
    dashboard_result: EndpointResult,
    tracking_result: EndpointResult,
    lock: asyncio.Lock,
) -> None:
    # Login uma vez por worker (identidade distinta por worker) para respeitar
    # o rate limiter de login por identidade (5 tentativas / 15 min, ver
    # app/services/auth_rate_limit_service.py) e simular usuários reais que
    # autenticam uma vez e navegam — não recarregam login a cada request.
    try:
        token, login_ms = await _login_and_get_token(client, email, password)
    except httpx.HTTPStatusError as error:
        async with lock:
            login_result.errors += 1
            login_result.statuses[error.response.status_code] = (
                login_result.statuses.get(error.response.status_code, 0) + 1
            )
        return
    async with lock:
        login_result.latencies_ms.append(login_ms)
        login_result.statuses[200] = login_result.statuses.get(200, 0) + 1

    headers = {"Authorization": f"Bearer {token}"}
    for _ in range(iterations):
        for path, result in (
            ("/api/v1/klm/omnichannel/events?limit=50", inbox_result),
            ("/api/v1/klm/dashboard", dashboard_result),
            ("/api/v1/klm/tracking/summary", tracking_result),
        ):
            status_code, elapsed_ms = await _timed_get(client, path, headers)
            async with lock:
                result.statuses[status_code] = result.statuses.get(status_code, 0) + 1
                if status_code == 200:
                    result.latencies_ms.append(elapsed_ms)
                else:
                    result.errors += 1


async def run(
    *,
    base_url: str,
    emails: list[str],
    password: str,
    concurrency: int,
    iterations_per_worker: int,
    timeout_seconds: float,
) -> dict[str, EndpointResult]:
    login_result = EndpointResult(name="login")
    inbox_result = EndpointResult(name="inbox")
    dashboard_result = EndpointResult(name="dashboard")
    tracking_result = EndpointResult(name="tracking")
    lock = asyncio.Lock()

    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    async with httpx.AsyncClient(
        base_url=base_url,
        timeout=httpx.Timeout(timeout_seconds),
        limits=limits,
        trust_env=False,
    ) as client:
        await asyncio.gather(
            *(
                _worker(
                    client=client,
                    email=emails[i % len(emails)],
                    password=password,
                    iterations=iterations_per_worker,
                    login_result=login_result,
                    inbox_result=inbox_result,
                    dashboard_result=dashboard_result,
                    tracking_result=tracking_result,
                    lock=lock,
                )
                for i in range(concurrency)
            )
        )

    return {
        "login": login_result,
        "inbox": inbox_result,
        "dashboard": dashboard_result,
        "tracking": tracking_result,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8011")
    parser.add_argument(
        "--email",
        action="append",
        default=None,
        help="Pode ser passado várias vezes para distribuir logins entre identidades "
        "distintas (recomendado, ver scripts/load/seed_load_test_users.py). "
        "Default: local@klmadvogados.com.br + load-test-user-001..025@local.klm.",
    )
    parser.add_argument("--password", default="LoadTestLocal!2026#klm")
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--iterations-per-worker", type=int, default=25)
    parser.add_argument("--threshold-ms", type=float, default=300.0)
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    base_url = _validate_base_url(args.base_url)
    emails = args.email or (
        ["local@klmadvogados.com.br"]
        + [f"load-test-user-{i:03d}@local.klm" for i in range(1, 26)]
    )

    started_at = time.time()
    results = asyncio.run(
        run(
            base_url=base_url,
            emails=emails,
            password=args.password,
            concurrency=args.concurrency,
            iterations_per_worker=args.iterations_per_worker,
            timeout_seconds=args.timeout_seconds,
        )
    )
    duration_seconds = round(time.time() - started_at, 2)

    report: dict[str, object] = {
        "baseUrl": base_url,
        "concurrency": args.concurrency,
        "iterationsPerWorker": args.iterations_per_worker,
        "totalRequestsPerEndpoint": args.concurrency * args.iterations_per_worker,
        "thresholdP95Ms": args.threshold_ms,
        "durationSeconds": duration_seconds,
        "endpoints": {},
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    overall_ok = True
    for name, result in results.items():
        summary = _summary(result.latencies_ms)
        passed = bool(result.latencies_ms) and result.errors == 0 and summary["p95Ms"] <= args.threshold_ms
        overall_ok = overall_ok and passed
        report["endpoints"][name] = {
            **summary,
            "errors": result.errors,
            "statuses": result.statuses,
            "passed": passed,
        }
        status_label = "OK" if passed else "FALHA"
        print(
            f"[{status_label}] {name:10s} p50={summary['p50Ms']:>7.2f}ms "
            f"p95={summary['p95Ms']:>7.2f}ms p99={summary['p99Ms']:>7.2f}ms "
            f"max={summary['maxMs']:>7.2f}ms erros={result.errors}"
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=True, sort_keys=True) + "\n", encoding="utf-8")
    print(f"\nRelatório salvo em {args.output}")
    print("RESULTADO GERAL:", "OK (p95 < threshold em todos os endpoints)" if overall_ok else "FALHA")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
