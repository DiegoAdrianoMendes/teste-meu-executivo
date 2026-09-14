# Identity Security: Forward-Only Recovery

## Scope

This runbook applies after migration `c8f5d2a9b714` has written any of the
following security state:

- a non-zero `users.auth_epoch`;
- an `identity_outbox_messages.request_id`;
- invitation failure or lock state;
- an `auth_revocation_intents` row.

At that point the migration is operationally forward-only. Its downgrade
intentionally fails instead of discarding revocation, delivery-binding, or
lockout evidence.

## Recovery rule

Do not force the Alembic downgrade or delete security state to make it pass.
Repair the defect with a new forward migration based on the current head. Keep
the affected runtime in maintenance mode until the repair and verification
steps below pass.

## Before changing schema

1. Stop identity writes and asynchronous authentication workers.
2. Capture a database backup and record the current Alembic revision.
3. Export counts for pending revocation intents, unexpired identity outbox
   messages, locked invitations, and users with a non-zero auth epoch.
4. Confirm that the repair migration preserves those rows or transforms them
   explicitly and reversibly.

## Session containment

If the incident may have exposed or incorrectly accepted a credential:

1. Increment `auth_epoch` for every affected user in the same transaction as
   the containment record.
2. Run `auth_revocation_tasks.retry_pending` until no pending intent remains.
3. Purge expired identity outbox messages with the bounded cleanup task.
4. Do not rotate the shared `SECRET_KEY` as an ad hoc fix: it also protects the
   encrypted local identity outbox. Key rotation requires a planned re-encrypt
   or purge procedure.

## Forward repair

1. Create a new Alembic revision whose `down_revision` is the current head.
2. Add a preflight that reports actionable row counts before every lossy or
   constraining operation.
3. Preserve tenant-bound foreign keys, forced RLS, narrow security-definer
   resolvers, canonical email enforcement, delivery binding, and auth epochs.
4. Make the repair idempotent where operational retries are possible.
5. Add SQLite migration tests and PostgreSQL runtime-role tests for the exact
   incident path.

## Verification gate

Resume traffic only after all of the following pass on a clean local database
and on the PostgreSQL QA database:

```bash
.venv/bin/pytest -q tests/migrations/test_identity_security_migration.py
bash scripts/verify_postgresql_qa.sh
.venv/bin/python scripts/verify_saas_v5_p1.py
```

The final verifier must report `status=passed` and `sourceStable=true`. Record
the repair revision, backup identifier, test evidence, and remaining pending
revocation count in the incident log.
