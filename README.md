# OpsMind

OpsMind uses **one managed Supabase project** for its PostgreSQL database, Supabase Auth, and (when implemented) Supabase Storage.

## Database architecture

There is only one application database: the PostgreSQL database inside Supabase.

- `DATABASE_URL` connects to that same Supabase database with the project `postgres`/migration role. Alembic uses it to create and alter schema.
- `APP_DATABASE_URL` connects to that same Supabase database with the restricted `opsmind_app` role. FastAPI uses it for runtime queries under PostgreSQL RLS.
- These are two database connection credentials/roles, **not two databases**.
- Redis remains a separate supporting service for cache/queue use. It is not a second SQL database.

## Supabase setup

1. Create a Supabase project.
2. In Supabase Dashboard → SQL Editor, run `supabase/01_create_app_role.sql` after replacing its password placeholder with a long random password.
3. Copy the Supabase Session Pooler connection string (port `5432`) into `DATABASE_URL`. Use the project database role for migrations.
4. Copy the Supabase Transaction Pooler connection string (port `6543`) into `APP_DATABASE_URL`, using username `opsmind_app.<PROJECT_REF>` and the password set in step 2.
5. Set `SUPABASE_URL` to `https://<PROJECT_REF>.supabase.co`.
6. Copy `backend/.env.example` to `backend/.env` and fill in the real values. Never commit `.env`.
7. Start only Redis locally if needed: `docker compose up -d redis`.
8. From `backend/`, run `uv sync`, then `uv run alembic upgrade head`.

The initial Alembic migration expects the `opsmind_app` role to exist. It enables and forces RLS, creates the helper functions, and grants the runtime role the table/function privileges it needs. Do not use the Supabase `anon` or `authenticated` roles as the FastAPI database connection role.

## Running the backend

From the `backend` directory:

```powershell
uv sync
uv run uvicorn app.main:app --reload
```

## Tests

Unit/schema tests can run without live Supabase credentials. Database integration tests are skipped unless both Supabase connection URLs are configured. Configure them against a dedicated test Supabase project or a disposable database; integration tests create and delete test rows.

## Supabase Auth API setup

See [`README_AUTH_SETUP.md`](README_AUTH_SETUP.md) for the Supabase Auth environment variables, dashboard configuration, password recovery redirect setup, cookie behavior, and authentication endpoints.

## Department Workflow System (5 Departments, 6 Workflows, 9 Node Types)

- **5 Approved Departments (`backend/app/services/department.py`, migration `b2c3d4e5f6a7`)**: `support` (Customer Support), `finance` (Finance & Accounts), `sales` (Sales & CRM), `legal` (Legal & Compliance), and `operations` (Internal Operations).
- **6 Approved Workflow Templates (`backend/app/workflows/template_seed.py`, `backend/scripts/seed_templates.py`)**:
  1. `support_reply` — Support Reply (Gmail + HubSpot Tickets + Slack)
  2. `finance_supplier_invoice` — Supplier Invoice Processing (Gmail + Zoho Invoice + Slack)
  3. `sales_lead_management` — Sales Lead Management (Gmail + HubSpot CRM + Slack)
  4. `legal_contract_review` — Contract Review (Google Drive + Slack)
  5. `ops_weekly_report` — Weekly Report (Knowledge Hub + Gmail + Slack)
  6. `ops_policy_qa` — Policy Q&A (Knowledge Hub + Slack, grounded with `require_sources`)
  - Seed/upsert idempotently via `uv run python scripts/seed_templates.py` (from `backend/`). Unapproved legacy templates are deactivated (`is_active=False`).
- **9 Approved Node Types (`backend/app/workflows/definition.py`, `backend/app/workflows/dept_executor.py`)**:
  `Trigger` (`schedule` / `event` / `manual`), `Classify`, `Retrieve` (Knowledge Hub / `aggregate_ops`), `Extract`, `Agent`, `Condition` (safe AST evaluator), `Human Approval`, `Action`, and `Wait`.
- **Strict Evaluation Gate & Replay (`backend/app/services/department_workflows.py`, `backend/app/api/v1/department_workflows.py`)**:
  - Each of the 6 templates ships with a built-in 3-case `EvalSet`.
  - `POST /departments/{id}/workflows/{wf_id}/eval` runs a **structural readiness gate (deterministic, 0 tokens)** — verifying DAG schema validity and department connector readiness without calling an LLM — and records `EvalResult`. Activation (`activate_department_workflow`) is blocked (`409`) unless the latest `EvalResult` for that `workflow_version_id` passed.
  - `POST /departments/{id}/runs/{run_id}/replay` and `POST /runs/{run_id}/replay` replay a prior run's input on the workflow.
- **Knowledge Hub Scoping & Connector Sync (`backend/app/services/knowledge_sync.py`, `backend/app/api/v1/knowledge.py`, migration `b2c3d4e5f6a7`)**:
  - Documents support `scope` (`company_shared` / `department_private`) and `department_id` so department-private data is isolated during `Retrieve` steps.
  - Manual uploads via `KnowledgeBasePage.tsx` and connector sync via `POST /knowledge/sync/{connection_id}` + Celery task `sync_connector_knowledge_task`.
- **AI Routing & Token Cost Limits (`backend/app/ai/services/dept_ai_service.py`, `backend/app/ai/routing/dept_router.py`)**:
  - `AI_PRIMARY_PROVIDER=groq`, `AI_FALLBACK_PROVIDER=gemini` with Redis rate limiting, `<UNTRUSTED_DATA>` prompt-injection guards, and per-tenant daily/monthly token caps (`AI_DAILY_TOKEN_LIMIT`, `AI_MONTHLY_TOKEN_LIMIT`) tracked via `ai_runs`.
- **Connectors (`backend/app/connectors/`, `backend/app/workflows/action_registry.py`)**:
  - Approved connectors: `gmail`, `google_drive`, `google_calendar`, `slack`, `hubspot` (CRM + Tickets API: `hubspot.create_ticket`, `hubspot.update_ticket`, `hubspot.create_contact`), and `zoho_invoice` (REST API v3 with rate limiting + `Semaphore(5)` concurrency cap: `zoho_invoice.create_invoice`).
  - All connections are strictly department-owned and isolated.
- **Frontend Builder (`frontend/src/pages/DeptWorkflowBuilderPage.tsx`, `frontend/src/pages/KnowledgeBasePage.tsx`)**:
  - React Flow canvas with the 8 canvas node types + top-level Trigger, live AI node testing, **Run Eval** gate before **Activate**, **Replay** in run history, and scoped Knowledge Hub management.
