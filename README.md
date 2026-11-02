
## Local setup

Requirements: uv, Python 3.12 (uv installs it), Docker Desktop for the local Postgres/Redis.

```powershell
cd backend
uv sync
Copy-Item .env.example .env
uv run uvicorn app.main:app --reload
```

Fill `.env` yourself (never commit it): `DATABASE_URL` is the owner role used for migrations, `APP_DATABASE_URL` is the restricted `opsmind_app` role used at runtime, and `SUPABASE_URL` (plus `SUPABASE_JWT_SECRET` only for legacy HS256 tokens) enables token verification.

Checks, always from the `backend` folder:

```powershell
uv run ruff check .
uv run alembic check
uv run pytest -q
```

Local Docker database: `docker compose up -d` starts Postgres (host port 5433) and Redis (6379). If your Postgres volume was created before 2026-11-02, run `docker compose down -v` once so the fixed roles script is applied (this deletes local data).

## Key design decisions

1. Modular monolith on FastAPI; sync SQLAlchemy 2.x with psycopg 3.
2. Celery + Redis for background jobs (SRS section 13) instead of Temporal (section 3). Durability, replay and exactly-once come from the tables `runs`, `run_steps`, `action_logs`.
3. UUID primary keys. `users.id` equals the Supabase auth subject id, with no foreign key to the auth schema.
4. `users` and `tenants` are global; every other table carries `tenant_id`. Users reach tenants through `memberships`.
5. Cross-tenant integrity in the database: tenant-scoped parents have `UNIQUE (tenant_id, id)` and children reference `(tenant_id, parent_id)`; a test checks every foreign key.
6. Row-level security on all 20 tables with `FORCE ROW LEVEL SECURITY`. Policies read `app_tenant_id()` and `app_user_id()`, which return NULL when the setting is unset, so a missing context returns no rows.
7. The runtime role `opsmind_app` is not a superuser and cannot bypass RLS. `audit_logs`, `guardrail_events` and `usage_records` are append-only (SELECT, INSERT only).
8. One transaction per request: the user id is set for RLS, the tenant comes from the `X-Tenant-ID` header after a membership check, and the transaction commits before the response is sent (`Depends(..., scope="function")`). Endpoints call `db.flush()`, never `db.commit()`.
9. Exactly-once actions: `action_logs` unique on `(tenant_id, idempotency_key)` with statuses pending / succeeded / failed / unknown.
10. Deduplication: `events` unique on `(tenant_id, connection_id, dedup_key)`; `usage_records` unique on `(tenant_id, source_key)`.
11. One live subscription per tenant and one published version per workflow (partial unique indexes).
12. Search: `doc_chunks` supports keyword search (generated tsvector + GIN) and semantic search (`vector(384)` + HNSW cosine).
13. Hard delete for tenant data (NFR-7) through `ON DELETE CASCADE`.

## SRS deviations and open items

- `users` has no `tenant_id` (a user can belong to several companies); SRS 8.3 says every table has one.
- `teams` is not a table in SRS 8.2; in v1 the tenant is the team and members live in `memberships`.
- Supabase "Confirm email" is OFF for development. SRS FR-1.1 and UC-01 require email verification, so it must be ON before any real demo. Until then `users.email` is unverified and must never be used for authorization or invite acceptance.
- Data region is stored on the tenant but not enforced yet; all data is in one Supabase project (SRS A-6, Phase 11).
- Section 13 names the Anthropic API as the initial LLM provider, which conflicts with the zero-spend constraint. Decision deferred to Phase 6 behind a provider-agnostic Model Router.
- The trigger that makes a published `workflow_versions.definition` immutable is not implemented yet (needed before Phase 4).
- If the JWKS endpoint is unreachable, token verification fails with 401 instead of 503.
- Tests currently run against the configured database (the Supabase dev project locally, a clean Postgres in CI). They seed and delete only their own rows, but a crash mid-test can leave rows behind.
- `eval_sets.pass_threshold` (0.8) and `tenants.monthly_ai_cost_cap_usd` (5.0) are placeholders.

## Roadmap

| Phase | Scope | SRS IDs | Status |
|---|---|---|---|
| 0 | Foundation: repo, FastAPI, Docker, DB session, Alembic, CI | NFR-11 | Completed (CI rewritten in 2.2c, first run pending) |
| 1 | Schema, initial migration, RLS, Supabase connection | Section 8, FR-1.5, NFR-6 | Completed |
| 2 | Auth, tenants, RBAC, React shell | FR-1, UC-01 | In Progress |
| 3 | Connectors, Gmail OAuth, webhooks | FR-6, UC-02 | Pending |
| 4 | Workflow versioning and builder | FR-3, UC-03 | Pending |
| 5 | Execution engine, Celery, idempotent actions | FR-4, AI-N5 | Pending |
| 6 | AI layer: model router, PII, guardrails, policy | AI-1..AI-8 | Pending |
| 7 | Human approval and review inbox | FR-5, UC-05 | Pending |
| 8 | Knowledge base | FR-7, UC-06 | Pending |
| 9 | Usage, cost caps, Stripe test mode | FR-2, AI-7 | Pending |
| 10 | Dashboards, audit, replay, public API and SDK | FR-8..FR-10 | Pending |
| 11 | Evals, release gate, load and failure tests, deployment | AI-9, AC-12..AC-19 | Pending |

### Phase 2 steps

| Step | Scope | Status | Evidence |
|---|---|---|---|
| 2.1 | Supabase JWT verification, current-user dependency | Completed | tests pass |
| 2.2 | Tenant-scoped DB dependency with RLS context | Completed | 10 tests, suite 73 passed |
| 2.2b | Commit before response, env example, cleanup | Completed | 74 passed, alembic check clean, pushed (aa68de0) |
| 2.2c | CI rewrite, roles script fix, .gitattributes, README | Testing | recipe rehearsed on clean Postgres 16 + pgvector: 72 passed, 2 skipped; GitHub run pending |
| 2.3 | POST /tenants, GET /me | Pending | |
| 2.4+ | RBAC role checks, member invites, React shell with signup and login | Pending | |

## Progress log

### 2026-10-29 to 2026-11-02
- Phase 0 completed: repo and uv project, FastAPI app factory, typed settings, health endpoints, Docker Compose, session layer, Alembic, first CI workflow.
- Phase 1 completed: 20 ORM models for all SRS 8.2 tables, one initial migration with RLS and grants, Supabase connection (migrations through the session pooler, runtime through the transaction pooler).
- Step 2.1: Supabase JWT verification and current-user dependency.
- Step 2.2: tenant-scoped database dependency (400 for missing or invalid header, 401 without token, 403 for non-member, unknown tenant or inactive user).
- Step 2.2b: found that the default dependency scope commits after the response is sent; switched the DB dependency to function scope and added a regression test. Removed the stray async connection script and unused async dependencies. Tests: 74 passed. Pushed as aa68de0 after rebasing on a README commit made on GitHub.
- Step 2.2c: while rehearsing CI on a clean Postgres, found that the local roles script granted UPDATE/DELETE on the append-only tables through default privileges; removed them. New CI creates the app role, applies migrations, checks drift, then runs the tests.
- Not confirmed yet: first GitHub Actions run of the new workflow.

## Next session handoff

- Continue at **Phase 2, Step 2.3** (SRS FR-1, FR-1.5, FR-2.1, UC-01). Design, pending approval of the per-user company limit:
  - No new migration: the existing RLS policies let the server create a tenant, an Owner membership and a free active subscription in one transaction by generating the tenant id server-side and setting it as the tenant context.
  - The user row is created on first call to `/me` or `POST /tenants` (insert if missing, update email if changed); no email claim returns 400; an email owned by another user id returns 409 (handled inside a savepoint).
  - `POST /tenants` takes `name` (1-200 chars) and `region` (eu or us, immutable), the server builds the slug with a collision suffix, and a user may own at most 3 companies (placeholder), enforced in code with an advisory transaction lock.
  - `GET /me` returns the profile plus all memberships and does not need `X-Tenant-ID`; inactive users get 403.
- Endpoint rules: `db.flush()` only, never `db.commit()`.
- Working style: Roman Urdu explanations, one major step at a time, complete files, exact PowerShell commands, expected results, wait for confirmation before the next step. Do not ask for secrets.
'@