# KLM local P4 stack

This compose file is an offline-first, loopback-only runtime for P4 items 6-7.
Its service definitions never import the repository `.env`; required local
secrets must be supplied by the invoking shell. Use `--env-file /dev/null` as
shown below to also disable Compose CLI interpolation from that file. All
containers share only the `klm_internal` network, which is marked
`internal: true` and has IP masquerading disabled.

## Profiles

| Profile | Services |
|---|---|
| default | PostgreSQL/pgvector, Redis, migration, API and reverse proxy |
| `agents` | Celery worker and beat |
| `storage` | MinIO and private bucket bootstrap |
| `mail` | Mailpit SMTP sink and UI |
| `fake` | deterministic OpenAI/Anthropic/Gemini-compatible HTTP simulator |
| `full` | all optional services above |

The local API itself uses `AI_PROVIDER=fake`; this is the in-process fake gateway
already enforced by the application. The `fake-provider` profile exists for HTTP
contract and integration exercises. Both paths remain deterministic and offline.

MinIO is provisioned as a private S3-compatible runtime. The application still
keeps `KLM_AWS_ENABLED=false`, so its current AWS-gated S3 client is not activated;
the application-owned local object volume remains the active storage path. Mailpit
is likewise an isolated SMTP sink; external Resend delivery remains disabled.

## Offline validation

Use distinct random values in a local shell. The verifier injects its own inert
values and does not display them:

```sh
./.venv/bin/python scripts/verify_local_stack.py
./.venv/bin/python -m pytest -q tests/runtime
```

The verifier runs `docker compose config`, inspects the resolved contract, and
checks the local Docker image cache. It never builds, pulls, starts containers, or
contacts a registry. A missing-image report is informational and deliberately
prevents this validation phase from becoming a network download.

Once every pinned base and service image has been loaded into the local cache by
an approved process, the full stack can be built and started without pulls:

```sh
docker compose --env-file /dev/null --profile full build --pull=false
docker compose --env-file /dev/null --profile full up --pull never --no-build -d
docker compose --env-file /dev/null --profile full ps
```

The reverse proxy is available at `http://127.0.0.1:8080` by default. Optional
MinIO, Mailpit and fake-provider ports are also bound only to `127.0.0.1`.
