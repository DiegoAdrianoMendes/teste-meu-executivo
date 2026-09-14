# ChatterMate v1.0.14 — KLM local runtime overlay

This directory runs the complete public ChatterMate v1.0.14 runtime without
changing the frozen checkout at `.vendor/upstreams/chattermate` or the root
`docker-compose.oss.yml`. The inspected source is tag `v1.0.14`, commit
`9770df5b4d4909084a3b3a00ff2404d25724de90`, Apache-2.0. The private gitlinks
`backend/app/enterprise` and `frontend/src/modules/enterprise` are excluded by
Dockerfile-specific build-context filters and removed defensively in the image.

## Runtime topology

| Service | Contract | Published | Healthcheck |
|---|---|---:|---|
| `chattermate-postgres` | custom `pgvector/pgvector:0.8.1-pg16`; DB/user `chattermate`; vector extension initialized | no | `pg_isready` plus installed `vector` extension |
| `chattermate-redis` | `redis:7.4.2-alpine`; AOF and password required | no | authenticated `redis-cli ping` |
| `chattermate-init` | backend image, command `migrate`; one-shot `alembic upgrade head` | no | successful exit |
| `chattermate-api` | backend image, command `api`; Gunicorn/Uvicorn on `8000` | no | `GET http://127.0.0.1:8000/health` reports `healthy` |
| `chattermate-worker` | same backend image, command `worker`; knowledge and FAQ loops | no | PID command contains `app.workers.knowledge_processor` |
| `chattermate-frontend` | rebuilt Vue SPA, widget, and webclient; Nginx on `8080` | no | `GET /healthz` |
| `chattermate-gateway` | same-origin Nginx on `8080` | `127.0.0.1:${KLM_OSS_CHATTERMATE_PORT:-3300}` | `GET /healthz` |

The exact service, volume, network, environment, dependency, and healthcheck
contract to copy into the root Compose file is in
`compose.contract.yml`. Use profile `automation`. Only the gateway publishes a
host port, and it binds to loopback. The default internal and loopback network
names deliberately reuse the existing KLM networks; the three optional
`KLM_OSS_CHATTERMATE_*_NETWORK` overrides are reserved for isolated smoke tests.

## Same-origin and local cookies

The gateway preserves `/api/v1` verbatim, proxies `/socket.io/` with upgrade
headers, serves the SPA/widget/webclient from the same browser origin, and
forwards the original host to ChatterMate's help-center dispatcher. Requests
for `localhost` and `127.0.0.1` receive the SPA; other hostnames reach the
public help-center ASGI app, allowing names such as
`acme.chattermate.localhost:3300`.

Upstream login responses deliberately emit `Secure; SameSite=None` cookies for
cross-domain hosted/Shopify deployments. Browsers reject those cookies over
local HTTP. The local gateway applies:

```nginx
proxy_cookie_flags ~ nosecure samesite=lax;
```

It preserves `HttpOnly` where upstream set it, removes `Secure`, and changes
all response cookies to `SameSite=Lax`. This rewrite is safe only for this
loopback HTTP overlay; do not reuse it for an internet-facing TLS deployment.
The API has no directly published port, so clients cannot bypass the rewrite.

## Model and crawler behavior

The stock `backend/scripts/start.sh` calls `preload_models.py`, which downloads
SentenceTransformer and FastEmbed models before serving. This overlay never
calls either script. The API and worker log that startup preloading is skipped.
Models remain available and download lazily when a knowledge feature first
needs them, into the `chattermate-model-cache` volume. Crawl4AI's browser is
installed at image build time so website knowledge features are not amputated.
Its writable database, cache, and browser profiles are directed to the shared
`chattermate-config` volume, keeping the backend root filesystem read-only.
Upstream also unconditionally creates a rotating `logs/app.log`; a bounded
64 MiB UID-owned tmpfs satisfies that behavior while stdout remains the
durable operator log stream.

The API and worker join an outbound-only bridge network because public
ChatterMate features include BYO model APIs, knowledge crawling, OAuth, and
messaging channels. No provider credential is set by default and neither
service publishes an inbound port. Remove that egress network only if degraded
external/model features are acceptable.

## Secrets and environment

`env.example` contains deliberately invalid sentinels for Compose rendering.
Generate real local values outside Git, for example:

```bash
openssl rand -hex 32                         # PostgreSQL password
openssl rand -hex 32                         # Redis password
openssl rand -hex 48                         # JWT secret
openssl rand -hex 48                         # conversation-token secret
openssl rand -base64 32 | tr '+/' '-_'       # Fernet ENCRYPTION_KEY
```

The final Fernet value must retain its `=` padding and decode to 32 bytes.
The backend preflight rejects short/default secrets, malformed service URLs,
invalid CORS JSON, disabled Redis, and non-production mode before migrations or
process startup. ChatterMate v1.0.14 incorrectly decodes a valid Fernet key a
second time; the image-only `backend/local-runtime.patch` corrects that defect
and removes the upstream fallback's secret-bearing log message.

Optional Firebase browser values are rendered at frontend container startup
into `/tmp`, so the frontend image is reusable and remains read-only. A Firebase
Admin credential file, if used, must be mounted read-only and
`KLM_OSS_CHATTERMATE_FIREBASE_CREDENTIALS` set to its container path. Jira,
Slack, Meta, Shopify, SMTP, S3, and model-provider credentials remain optional;
their public code paths are not removed. External webhook/OAuth integrations
also require an explicitly configured public HTTPS callback URL, which is
outside the default local-only contract.

## Build and verification

Build contexts must be the repository root so the Dockerfiles can read the
frozen public source; their adjacent `.dockerignore` files reduce each context
and exclude private gitlinks.

```bash
docker build --file docker/oss/chattermate/Dockerfile.postgres --tag klm/chattermate-postgres:1.0.14-local .
docker build --file docker/oss/chattermate/Dockerfile.frontend --tag klm/chattermate-frontend:1.0.14-local .
docker build --file docker/oss/chattermate/Dockerfile.backend --tag klm/chattermate-backend:1.0.14-local .
docker build --file docker/oss/chattermate/Dockerfile.gateway --tag klm/chattermate-gateway:1.0.14-local .
```

After wiring, start `chattermate-init` before API/worker (the dependency
contract does this), then open `http://127.0.0.1:3300`. The first visit routes
to ChatterMate's public organization setup. Do not use real legal/client data:
this local overlay has no TLS, HA, backup policy, or production operations
controls.
