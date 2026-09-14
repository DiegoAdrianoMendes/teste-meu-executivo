# PostgreSQL workspace RLS

This release applies database-enforced workspace isolation to the complete
canonical inventory exported as `RLS_TENANT_TABLES` in `app/db/rls.py`.
`RLS_POLICY_SPECS` must contain exactly the same keys. CI derives and verifies
the inventory from those constants instead of relying on a manually maintained
table count. It does not claim RLS coverage for tenant-aware tables that are
deliberately absent from that reviewed inventory.

All protected tables use the `workspace_current_id` policy. Direct tables match
their own `workspace_id`. Child tables such as `documents`,
`approval_queue_items`, and `whatsapp_messages` use fail-closed `EXISTS`
predicates through `matters`, contacts, campaigns, recipients, or social
conversations. Tables with nullable or ambiguous tenant ownership remain out of
scope until their data model or bootstrap path can establish ownership safely.
In particular, `flows` also stores global read-only templates with
`workspace_id IS NULL`, `notifications` may be global, and public-token or
billing lookup tables must discover a workspace before a tenant context exists.
Those paths keep exact application-level filters. Runtime state that never has a
global owner (`flow_runs`, `scheduled_messages`, `agent_runs`, and
`human_overrides`) is protected directly by RLS.

## Runtime contract

- `DATABASE_URL` is the runtime connection and must use a non-superuser role
  without `BYPASSRLS` or ownership of any protected table.
- `DATABASE_URL_SYNC` is an administrative migration connection. Keep it in a
  separate migration process; do not expose it to the API or workers.
- Every transaction that accesses a protected table must call
  `set_workspace_context(session, workspace_id)` first.
- The helper runs
  `set_config('app.current_workspace_id', workspace_id_text, true)`. The final `true`
  makes the setting transaction-local. The session hook reapplies it after a
  commit or rollback.
- A session cannot switch workspaces. Open a new session for another tenant.
- Missing or empty context matches no rows and rejects inserts/updates.
- SQLite records the session invariant but runs no PostgreSQL SQL.

FastAPI membership dependencies set the context after confirming an active
membership. The local bootstrap explicitly adopts its newly created workspace.
Background jobs, signed webhooks, CLI scripts, and other direct session users
must adopt their workspace explicitly before touching protected tables.

## Runtime role gate

`app/db/rls.py` audits every physical PostgreSQL runtime connection. Startup
access is rejected when the role:

- is a superuser or has `BYPASSRLS`, `CREATEROLE`, `CREATEDB`, or replication;
- can assume a superuser, `BYPASSRLS`, or protected-table owner role;
- owns a protected table;
- can create objects in the application schema or lacks required DML grants;
- sees a missing, altered, or additional protected-table policy; or
- finds a protected table without both `ENABLE ROW LEVEL SECURITY` and
  `FORCE ROW LEVEL SECURITY`.

The greenfield head `e8c0a2d4f617` additionally requires all six tenant
resolvers to be owned by one deterministic `NOLOGIN NOBYPASSRLS` role, with no
schema `CREATE`, exact SQL body/search path/stability/strictness, and `EXECUTE`
granted only to that owner and the runtime role. Five lookup tables have an
exact owner-only `klm_resolver_owner_select` policy under `FORCE RLS` (the fifth,
`integration_connections`, arrived with `b8d2f4a6c9e1` for the Asaas webhook
resolver); the owner has only `SELECT` on the five lookup relations it needs.

The migration command does not need the runtime secret after bootstrap. After
`alembic upgrade head`, it discovers exactly one safe runtime role from the
resolver ACLs, removes DML that no RLS policy can authorize (including
`UPDATE`/`DELETE` on immutable ledgers), and re-audits owner, functions, ACLs,
policies, FORCE RLS, and final DML before returning success. `a671ed1` is not a
supported binary rollback because its guard requires a `BYPASSRLS` resolver
owner and exactly one policy per table.

The same head keeps signed WhatsApp envelopes append-only and grants the
runtime role mutable DML only on their separate processing-state projection.

The Compose stack provisions `klm_app` idempotently, runs Alembic separately as
the configured migrator, and gives API/worker containers only the `klm_app`
URL. The migrator is `NOSUPERUSER NOBYPASSRLS` and owns schema objects; it is
outside the runtime isolation claim and must remain confined to the one-shot
migration process.

## Security boundary

RLS protects every table in the canonical inventory when application code omits
a workspace filter or uses no workspace context. Authorization still decides
which workspace UUID may be adopted. Code execution as the runtime role, SQL
injection that can call `set_config`, or access to the migration credential is
outside this isolation boundary. The runtime gate prevents accidental
deployment with the migration role; it is not a substitute for secret isolation
and query safety.

## Verification

Run the portable tests:

```bash
.venv/bin/pytest -q \
  tests/db/test_tenant_rls_sqlite.py \
  tests/db/test_legal_crm_rls_postgresql.py
```

Run PostgreSQL integration tests against a migrated disposable database:

```bash
KLM_TEST_POSTGRES_ADMIN_URL=postgresql+asyncpg://klm_user:klm_password@127.0.0.1:5432/klm_legal_hub \
KLM_TEST_POSTGRES_APP_URL=postgresql+asyncpg://klm_app:klm_app_password@127.0.0.1:5432/klm_legal_hub \
.venv/bin/pytest -q \
  tests/db/test_legal_crm_rls_postgresql.py \
  tests/integration/test_postgresql_rls.py
```

The PostgreSQL suites verify the runtime and migration roles, the exact
`set(RLS_TENANT_TABLES) == set(RLS_POLICY_SPECS)` inventory, fail-closed behavior, tenant
A/B reads, cross-tenant writes, direct and parent-derived policies, transaction
reapplication, and pool reuse without context leakage.
