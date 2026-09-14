#!/usr/bin/env python3
"""Fail-closed runtime checks for the immutable ChatterMate public image."""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit


DEFAULT_OR_SENTINEL_PARTS = (
    "change-me",
    "not-a-secret",
    "run-start-script",
    "your-secret",
    "your_conversation",
    "your-jwt",
)

KNOWN_PUBLIC_DEFAULTS = {
    # ChatterMate's checked-in backend default. It must never become a usable
    # encryption key merely because it has the right Fernet shape.
    "RFQ4bCYZZ7iytJaR8OBOw3A-Qz5HmTt3UGXnKxVD7DQ=",
    "RFQ4SzhyRTVYdGtsLUxsc25SaDB0QlZpbTdQRmlVRlpsZUlCaFRlU2Vxbz0=",
}


def fail(message: str) -> None:
    raise RuntimeError(message)


def require_secret(name: str, minimum_length: int = 32) -> str:
    value = os.getenv(name, "")
    lowered = value.lower()
    if len(value) < minimum_length:
        fail(f"{name} must contain at least {minimum_length} characters")
    if any(part in lowered for part in DEFAULT_OR_SENTINEL_PARTS):
        fail(f"{name} contains a public/default sentinel")
    if value in KNOWN_PUBLIC_DEFAULTS:
        fail(f"{name} is a checked-in public default")
    return value


def validate_fernet_key() -> None:
    value = require_secret("ENCRYPTION_KEY", 43)
    try:
        decoded = base64.urlsafe_b64decode(value.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        fail(f"ENCRYPTION_KEY is not URL-safe base64: {exc}")
    if len(decoded) != 32:
        fail("ENCRYPTION_KEY must decode to exactly 32 bytes (Fernet key)")


def validate_url(
    name: str,
    schemes: set[str],
    *,
    require_credentials: bool = False,
    require_password: bool = False,
) -> str:
    value = os.getenv(name, "")
    parsed = urlsplit(value)
    if parsed.scheme not in schemes or not parsed.hostname:
        fail(f"{name} must be an absolute {sorted(schemes)} URL")
    if require_credentials and (not parsed.username or parsed.password is None):
        fail(f"{name} must include non-empty service credentials")
    if require_password and parsed.password is None:
        fail(f"{name} must include a service password")
    return value


def validate_positive_integer(name: str, default: str) -> None:
    raw = os.getenv(name, default)
    try:
        value = int(raw)
    except ValueError:
        fail(f"{name} must be an integer")
    if value < 1:
        fail(f"{name} must be positive")


def validate_environment() -> None:
    if os.getenv("ENVIRONMENT") != "production":
        fail("ENVIRONMENT must be production for this runtime image")
    if os.getenv("REDIS_ENABLED", "").lower() != "true":
        fail("REDIS_ENABLED must be true")

    require_secret("JWT_SECRET_KEY")
    require_secret("CONVERSATION_SECRET_KEY")
    validate_fernet_key()
    validate_url("DATABASE_URL", {"postgresql", "postgresql+psycopg"}, require_credentials=True)
    # Redis ACL usernames are optional; a password-only URL is the normal
    # contract for `redis-server --requirepass`.
    validate_url("REDIS_URL", {"redis", "rediss"}, require_password=True)

    for name in ("APP_BASE_URL", "BACKEND_URL", "FRONTEND_URL", "VITE_WIDGET_URL"):
        validate_url(name, {"http", "https"})

    cors_raw = os.getenv("CORS_ORIGINS", "")
    try:
        cors = json.loads(cors_raw)
    except json.JSONDecodeError as exc:
        fail(f"CORS_ORIGINS must be a JSON array: {exc}")
    if not isinstance(cors, list) or not cors or not all(isinstance(item, str) for item in cors):
        fail("CORS_ORIGINS must be a non-empty JSON array of origins")
    for origin in cors:
        parsed = urlsplit(origin)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.path not in {"", "/"}:
            fail(f"CORS_ORIGINS contains an invalid origin: {origin!r}")

    for name, default in (
        ("WORKERS", "1"),
        ("TIMEOUT", "120"),
        ("GRACEFUL_TIMEOUT", "30"),
        ("KEEP_ALIVE", "5"),
        ("CHATTERMATE_STARTUP_TIMEOUT", "180"),
    ):
        validate_positive_integer(name, default)


def redacted_endpoint(value: str) -> str:
    parsed = urlsplit(value)
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{parsed.hostname}{port}{parsed.path}"


def wait_for_dependencies() -> None:
    import psycopg
    import redis

    database_url = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
    redis_url = os.environ["REDIS_URL"]
    timeout = int(os.getenv("CHATTERMATE_STARTUP_TIMEOUT", "180"))
    deadline = time.monotonic() + timeout
    last_error = "not attempted"

    while time.monotonic() < deadline:
        try:
            with psycopg.connect(database_url, connect_timeout=3) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    cursor.fetchone()
            client = redis.Redis.from_url(
                redis_url,
                socket_connect_timeout=3,
                socket_timeout=3,
                decode_responses=True,
            )
            if client.ping() is not True:
                fail("Redis PING did not return true")
            print(
                "Dependencies ready: "
                f"{redacted_endpoint(os.environ['DATABASE_URL'])}, "
                f"{redacted_endpoint(redis_url)}",
                flush=True,
            )
            return
        except Exception as exc:  # startup retry boundary
            last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(2)

    fail(f"dependencies were not ready within {timeout}s ({last_error})")


def http_health(url: str) -> None:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            if response.status != 200:
                fail(f"health endpoint returned HTTP {response.status}")
            payload = json.load(response)
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        fail(f"health endpoint failed: {exc}")
    if payload.get("status") != "healthy":
        fail("health endpoint did not report healthy")


def worker_health() -> None:
    cmdline = Path("/proc/1/cmdline").read_bytes().replace(b"\x00", b" ")
    if b"app.workers.knowledge_processor" not in cmdline:
        fail("PID 1 is not the ChatterMate knowledge processor")


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate")
    subparsers.add_parser("wait")
    health_parser = subparsers.add_parser("http-health")
    health_parser.add_argument("url")
    subparsers.add_parser("worker-health")
    args = parser.parse_args()

    try:
        if args.command == "validate":
            validate_environment()
        elif args.command == "wait":
            wait_for_dependencies()
        elif args.command == "http-health":
            http_health(args.url)
        elif args.command == "worker-health":
            worker_health()
    except Exception as exc:
        print(f"chattermate preflight: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
