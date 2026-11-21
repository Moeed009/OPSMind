# OpsMind — Complete Real-Time Frontend & Application Audit Report

**Audit Date:** 2026-11-17  
**Target Environment:** Frontend (`https://localhost:5173/`), Backend (`http://127.0.0.1:8000`), PostgreSQL / Supabase Auth, Redis  
**Audit Method:** Live browser execution + backend/frontend test suites + static code & security review (read-only; no source code modified during audit).

---

## 1. Executive Summary

OpsMind demonstrates strong foundational engineering in multi-tenant isolation (`APP_DATABASE_URL` non-superuser role + PostgreSQL RLS), AES-256-GCM connector credential encryption, PKCE OAuth state protection, lazy-loaded React 19 UI, and department-scoped workflow templates & scheduling.

However, **live browser testing and deep code auditing identified 2 Critical/High security & functional issues and several High/Medium integration defects** that block production readiness:
1. **Unredacted Outbound AI Payloads (`CRITICAL`):** `DepartmentAiService` redacts PII/secrets only in DB `input_preview`, sending raw unredacted data to Groq/Gemini.
2. **Broken Global Workflow Templates Proxy (`HIGH`):** `GET /workflow-templates` returns `404` on `https://localhost:5173` because `/workflow-templates` is missing from `frontend/vite.config.ts` proxy.
3. **Live AI Test Failure & Missing Fallback (`HIGH`):** Clicking **Test AI node** in `DeptWorkflowBuilderPage` failed with `HTTP 503: Groq rejected request (404)` because `AIMalformedResponseError` does not trigger Gemini fallback.
4. **Connector Permanent Delete Skips Workflow Pause (`HIGH`):** `DELETE /connections/{id}?permanent=true` does not call `pause_workflows_for_connection`, and `WorkflowDefinition` provider-based nodes lack `config.connection_id` matching.
5. **Mobile Header Overflow (`MEDIUM`):** At `390x844`, the `User menu` button overflows `14px` past the viewport and clips its chevron.
6. **Backend Test Suite (`FAIL` — 2/364):** 2 stale/unisolated backend tests fail in `uv run pytest -q`.

---

## 2. Application Startup Results

| Component | Command / Endpoint | Result |
|---|---|---|
| Backend API | `http://127.0.0.1:8000` (`uvicorn`) | **PASS** — Serving OpenAPI & API v1 routes |
| Frontend Dev Server | `https://localhost:5173` (`vite`) | **PASS** — HTTPS dev server serving React 19 SPA |
| Database & Migrations | `uv run alembic current` | **PASS** — Synced at `a8b3c6d5e2f7 (head)` |
| Browser Console | Live Chrome session across all pages | **PASS** — `0` uncaught JS console errors |

---

## 3. Login and Authentication Results

| Check | Outcome | Evidence |
|---|---|---|
| Unauthenticated Route Guard | **PASS** | Visiting `/departments` while logged out redirected to `/login` (expected `401` on `/current_user` & `/auth/refresh`) |
| Login Flow | **PASS** | Authenticated via `/login` using authorized test account; HttpOnly session cookies set |
| Session Persistence (Reload) | **PASS** | `page.reload()` preserved session with `0` HTTP 4xx/5xx errors |
| Onboarding Exit / Logout Gap | **PARTIAL** | `/onboarding` has no header User Menu or Logout button; users with 0 companies cannot sign out from `/onboarding` |
| Logout Flow | **PASS** | Header `User menu -> Log out` redirected to `/login`; revisiting `/` redirected back to `/login` |

---

## 4. Complete Page Inventory

| Route | Page Component | Status | Notes |
|---|---|---|---|
| `/login`, `/signup`, `/forgot-password`, `/reset-password` | Auth Pages | **PASS** | Supabase URL Configuration must allowlist `https://localhost:5173/reset-password` to avoid `localhost:3000` fallback |
| `/accept-invite`, `/accept-invitation` | `AcceptInvitePage` | **PASS** | Both routes mapped in `App.tsx` |
| `/onboarding` | `OnboardingPage` | **PARTIAL** | Works, but lacks Logout / Switch Account control |
| `/` | `DashboardPage` | **PASS** | Displays live workspace metrics, RLS status, Presidio/Policy telemetry |
| `/departments` | `DepartmentsPage` | **PASS** | Lists & creates departments from starter templates |
| `/departments/:id` | `DepartmentDetailPage` | **PASS** | Overview, Members, Connections, Templates (2 dept + 12 all), Approvals tabs |
| `/departments/:id/workflows/:wfId` | `DeptWorkflowBuilderPage` | **PARTIAL** | React Flow canvas, ScheduleEditor, and inspectors work; AI test returned `503` |
| `/workflows` | `WorkflowsListPage` | **PARTIAL** | Lists drafts; `Create New Workflow` modal failed `GET /workflow-templates -> 404` |
| `/workflows/:id` | `WorkflowEditorPage` | **NOT TESTED** | Legacy workflow editor coexists with `DeptWorkflowBuilderPage` |
| `/reviews` | `ReviewInboxPage` | **PASS** | Renders queue & empty state cleanly (`0` errors) |
| `/knowledge` | `KnowledgeBasePage` | **PASS** | Renders RAG Context Store empty state & `Add Document` (`0` errors) |
| `/connectors` | `ConnectionsPage` | **PASS** | Department-scoped filter (`All`, `Unassigned`, `Human Resources`), `0` errors |
| `/members` | `MembersPage` | **PASS** | Lists members, pending invitations, Invite modal, `Assign to department` |
| `/settings` | `SettingsPage` | **PARTIAL** | Only shows `Notification Preferences` (no Company/Email SMTP sections on `/settings`) |

---

## 5. Feature-by-Feature Audit

| Feature | Status | Details |
|---|---|---|
| Auth & Session Cookies | **PASS** | HttpOnly cookies + `/auth/refresh` automatic retry in `api.ts` |
| Onboarding Workspace Creation | **PARTIAL** | Button entered spinning/disabled state during click before settling on `/` |
| Department CRUD & Templates | **PASS** | Created `Human Resources`; `Templates` tab + `Show all departments (12)` returned `200` |
| Global Workflow Template Picker (`/workflows`) | **FAIL** | `GET /workflow-templates` returned `404` due to missing Vite proxy entry |
| Dept Workflow Builder & ScheduleEditor | **PASS** | Instantiated `finance_invoice_intake` (`201`), edited Schedule (`Asia/Karachi`, next 3 runs) |
| Live AI Node Test (`/departments/:id/ai/test`) | **FAIL** | Returned `HTTP 503` (`Groq rejected request (404)`) without Gemini fallback |
| Approvals & Review Inbox | **PARTIAL** | Empty states verified in UI; approval resume tested in backend `pytest`, not live UI |
| Connectors & OAuth | **BLOCKED** | External OAuth provider credentials (Gmail/Slack) not connected in test tenant |
| Mobile Responsiveness (`390px`) | **FAIL** | `14px` horizontal overflow caused by header `User menu` button |

---

## 6. Department Architecture Review

- **Company vs Department Roles (`PASS` in backend):** `departments.py` and `department_workflows.py` enforce `_require_dept_role` / `require_department_permission`; plain company `Member`s have no access without assignment.
- **Department Isolation (`PASS`):** Templates, connections, workflows, and approvals are filtered by `(tenant_id, department_id)`.
- **Gap (`MEDIUM`):** Legacy `GET /workflows` (`backend/app/api/v1/workflows.py:256-264`) still permits reading `department_id IS NULL` company-wide workflows.

---

## 7. Connector Review

- **Encryption & OAuth (`PASS`):** `backend/app/connectors/crypto.py` uses AES-256-GCM with AAD (`{tenant_id}:{connection_id}`); `oauth.py` enforces PKCE S256, signed JWS state, and single-use DB nonces.
- **Bug (`HIGH`):** `DELETE /connections/{id}?permanent=true` in `backend/app/api/v1/connectors.py:713-744` collects `paused` rows but never calls `pause_workflows_for_connection`, and `pause_workflows_for_connection` does not match `WorkflowDefinition` provider-prefixed actions (`gmail.*`, `slack.*`).
- **Live OAuth (`BLOCKED`):** Live third-party OAuth handshake not executed without interactive external account consent.

---

## 8. Workflow Builder Review

- **Visual Canvas & State (`PASS`):** React Flow renders nodes/edges, `Add Step` palette (`AI`, `Condition`, `Approval`, `Action`), and `ScheduleEditor` with timezone-aware next-3-runs preview.
- **Minor UX (`LOW`):** Initial node (`ai · ext_1`) loaded partially offscreen left until clicking **Fit View**.
- **Backend DAG Execution Bug (`HIGH`):** `_walk_from` in `backend/app/workflows/dept_executor.py:78-80` uses naive `visited` BFS rather than `in_degree` tracking, so diamond/converging nodes execute before all parent branches finish.
- **Sync Resume (`MEDIUM`):** `decide_workflow_approval` runs remaining DAG nodes synchronously inside the HTTP request (`dept_executor.py:341-349`).
- **Stubbed Actions (`MEDIUM`):** `sheets.append_row` and `sheets.read_rows` in `action_registry.py:40-50` have `available=False`.

---

## 9. AI Integration Review

- **Provider Abstraction (`PASS`):** `DepartmentAIRouter` + `GroqHttpProvider` / `GeminiHttpProvider` cleanly separate provider HTTP logic.
- **Live AI Test (`FAIL`):** `POST /departments/{id}/ai/test` returned `503: Groq rejected request (404)` because `dept_providers.py:120-121` raises `AIMalformedResponseError` on HTTP `400-499` (except `429`), which `DepartmentAIRouter` (`dept_router.py:93`) does not catch for Gemini fallback.
- **Cost Cap Gap (`HIGH`):** `DepartmentAiService.run()` logs to `ai_runs` but does not enforce `Department.monthly_ai_cost_cap_usd` or persist `UsageRecord` cost entries.
- **Privacy Vulnerability (`CRITICAL`):** `DepartmentAiService.run()` (`dept_ai_service.py:158-160`) applies `redact_preview()` only to DB `input_preview`, sending **unredacted** `input_data` via `build_delimited_user_content()` to external AI providers (Groq/Gemini).

---

## 10. Frontend Code Review

- **Strengths:** `React.lazy` route splitting (`App.tsx`), `oxlint` (`0` warnings/errors), strict TypeScript, cookie-based `fetch` with automatic `401 -> /auth/refresh` single-flight deduplication (`lib/api.ts`).
- **Issues:**
  - `frontend/vite.config.ts` omits `/workflow-templates` proxy rule.
  - `frontend/package.json` duplicates `react-router` (`^8.5.1`) alongside `react-router-dom` (`^7.18.5`).
  - Dual workflow editors (`WorkflowEditorPage.tsx` vs `DeptWorkflowBuilderPage.tsx`).

---

## 11. Backend Code Review

- **Strengths:** `uv run ruff check .` passes with `0` errors; Pydantic v2 `WorkflowDefinition` performs thorough static DAG + cron validation; `app_database_url` enforces non-superuser DB connections.
- **Issues:**
  - `workflows.py:174-278` N+1 version serialization and missing pagination.
  - `tasks.py:131,188` creates and disposes a new SQLAlchemy engine on every 60s/120s Beat tick.
  - Deprecated `HTTP_422_UNPROCESSABLE_ENTITY` in 3 files.

---

## 12. Security Findings

- **CRITICAL:** Unredacted PII/secrets sent to external LLM providers in `dept_ai_service.py:159`.
- **HIGH:** `OAUTH_STATE_SECRET` length not validated (`>= 32` bytes) in `Settings` for production (`InsecureKeyLengthWarning` observed in tests).
- **HIGH:** Permanent connector deletion (`permanent=true`) skips `pause_workflows_for_connection`.
- **HEALTHY:** HttpOnly auth cookies, PostgreSQL RLS context (`app.current_user_id`, `app.current_tenant`), AES-256-GCM credential encryption, safe AST/whitelisted expression evaluator (no `eval`/`exec`).

---

## 13. Responsive UI Findings

| Viewport | `/` (Dashboard) | `/departments` | Observations |
|---|---|---|---|
| `1440x900` | **PASS** (0px overflow) | **PASS** (0px overflow) | 240px sidebar + clean grid |
| `1280x800` | **PASS** (0px overflow) | **PASS** (0px overflow) | Clean layout |
| `768x1024` | **PASS** (0px overflow) | **PASS** (0px overflow) | 240px sidebar leaves ~525px main width |
| `390x844` | **FAIL** (14px overflow) | **FAIL** (14px overflow) | Mobile drawer works, but header `User menu` button reaches `397.7px` and clips right chevron |

---

## 14. API Integration Findings

- `GET /workflow-templates` on `https://localhost:5173` → `HTTP 404` (missing in `vite.config.ts` proxy; backend route itself is registered in OpenAPI).
- `POST /departments/{id}/ai/test` → `HTTP 503` (`Groq rejected request (404)`).
- `GET /departments/{id}/workflow-templates?include_all=true` → `HTTP 200` (12 templates).
- `POST /departments/{id}/workflow-templates/{key}/use` → `HTTP 201`.

---

## 15. Database and Persistence Findings

- `alembic current` matches `a8b3c6d5e2f7 (head)` across all 16 migrations.
- `app.db.session.get_engine()` enforces `APP_DATABASE_URL` (non-superuser `opsmind_app`).
- `workflow_templates` contains all 12 active seeded templates across 6 department types.

---

## 16. Critical / High / Medium / Low Issues

| ID | Page / Feature | Severity | Description | Root Cause / File | Recommended Fix |
|---|---|---|---|---|---|
| **ISS-01** | AI Pipeline | **CRITICAL** | Unredacted PII/secrets sent to Groq/Gemini | `dept_ai_service.py:159`, `redaction.py:29` | Sanitize `input_data` via `InputGuardrail` / redaction before `build_delimited_user_content()` |
| **ISS-02** | `/workflows` Create Modal | **HIGH** | `GET /workflow-templates` returns `404` in dev | `frontend/vite.config.ts:66-86` missing `/workflow-templates` proxy | Add `'/workflow-templates': { target: BACKEND_URL, changeOrigin: false }` |
| **ISS-03** | Dept Workflow Builder AI Test | **HIGH** | `Test AI node` failed with `503: Groq rejected request (404)` | Invalid/unsupported Groq model or `dept_providers.py:120` raising non-fallback `AIMalformedResponseError` | Verify `GROQ_MODEL` config and fall back to Gemini on Groq `404`/endpoint errors |
| **ISS-04** | Connectors Delete | **HIGH** | `DELETE /connections/{id}?permanent=true` doesn't pause workflows; provider actions missed | `connectors.py:713-744`, `department.py` | Call `pause_workflows_for_connection` on permanent delete and match provider action prefixes |
| **ISS-05** | Dept DAG Executor | **HIGH** | Diamond/converging nodes execute before all parent branches finish | `dept_executor.py:78-80` | Track `remaining_in_degree` before enqueueing downstream nodes |
| **ISS-06** | AI Cost Governance | **HIGH** | Department `monthly_ai_cost_cap_usd` not enforced in `DepartmentAiService` | `dept_ai_service.py:133-251` | Enforce pre-call cap check and write `UsageRecord` entries |
| **ISS-07** | Backend Tests | **HIGH** | 2/364 backend tests fail (`test_departments_module.py`, `test_phase5_and_6_completion.py`) | Stale template count (`5` vs `6`) & unisolated tenant fixture | Update assertion to `6` and seed isolated Slack connection in test |
| **ISS-08** | Mobile Header (`390px`) | **MEDIUM** | `14px` horizontal overflow & clipped chevron on `User menu` | `frontend/src/components/AppShell.tsx` header sizing | Constrain header `User menu` max-width/truncation on `<640px` |
| **ISS-09** | `/onboarding` | **MEDIUM** | No Logout / Switch Account button on `/onboarding` | `frontend/src/pages/OnboardingPage.tsx` | Add a compact `Log out` action in `OnboardingPage` header |
| **ISS-10** | Approval Resume | **MEDIUM** | Remaining DAG runs synchronously inside HTTP request | `dept_executor.py:341-349` | Enqueue `run_department_workflow_task` on approval |
| **ISS-11** | Legacy `/workflows` | **MEDIUM** | N+1 version queries, no pagination, dual UI/API with dept workflows | `workflows.py:174-278`, `App.tsx:66-67` | Eager-load versions, paginate, and deprecate legacy `/workflows/:id` |
| **ISS-12** | `/settings` | **MEDIUM** | Only Notification Preferences shown (missing Company/Email sections if expected there) | `frontend/src/pages/SettingsPage.tsx` | Confirm intended scope or expose Email/Company tabs under `/settings` |
| **ISS-13** | Workflow Canvas | **LOW** | Initial node renders offscreen left until `Fit View` | `frontend/src/pages/DeptWorkflowBuilderPage.tsx` | Call `fitView` automatically on initial graph load |
| **ISS-14** | Dependencies & Warnings | **LOW** | Duplicate `react-router` + `react-router-dom`; `HTTP_422_UNPROCESSABLE_ENTITY` deprecation | `frontend/package.json`, `auth.py`, `department.py`, `department_workflows.py` | Remove unused `react-router` pkg; update to `HTTP_422_UNPROCESSABLE_CONTENT` |

---

## 17. Evidence & Screenshots

- Dashboard (`/` at `1440x900`): `C:\Users\AMK\.gemini\antigravity\brain\3914b05e-94cf-431b-927a-dd98d197b260\browser_sandbox\screenshots\step_22_shot_1_dashboard_1440.png`
- Department Templates (`/departments/:id` at `1440x900`): `C:\Users\AMK\.gemini\antigravity\brain\3914b05e-94cf-431b-927a-dd98d197b260\browser_sandbox\screenshots\step_36_shot_1_department_templates.png`
- Dept Workflow Builder (`/departments/:id/workflows/:wfId` at `1440x900`): `C:\Users\AMK\.gemini\antigravity\brain\3914b05e-94cf-431b-927a-dd98d197b260\browser_sandbox\screenshots\step_45_shot_1.png`
- Mobile (`390x844`) & Drawer: `C:\Users\AMK\.gemini\antigravity\brain\3914b05e-94cf-431b-927a-dd98d197b260\browser_sandbox\screenshots\step_63_shot_1.png`

---

## 18. Blocked Tests

- **Live Third-Party OAuth (Gmail / Slack):** `BLOCKED` — Requires interactive external OAuth consent and live mailbox/workspace tokens (not executed to avoid side effects).
- **Live Outbound Action Execution:** `BLOCKED` — Not executed in browser to avoid sending real emails/messages.

---

## 19. Overall Application Health

- **Frontend:** Clean UI, 0 console errors, 37/37 Vitest tests passing, 0 `oxlint` warnings; 2 live integration issues (`/workflow-templates` Vite proxy 404, `390px` header overflow).
- **Backend:** Clean code structure and strong RLS/crypto foundations; 362/364 pytest passing; 1 Critical AI PII sanitization gap (`PRIV-01`) and 1 live AI provider routing/model failure (`Groq 404` without Gemini fallback).

---

## 20. Prioritized Recommended Fixes

1. **Priority 1 (Critical Security):** Sanitize outbound LLM payloads in `backend/app/ai/services/dept_ai_service.py` before `build_delimited_user_content()`, and fix `permanent=true` workflow pausing in `backend/app/api/v1/connectors.py`.
2. **Priority 2 (Broken Live Integrations):** Add `'/workflow-templates'` to `frontend/vite.config.ts` proxy; fix Groq model / Gemini fallback for `404` in `backend/app/ai/providers/dept_providers.py` & `dept_router.py`; fix diamond DAG execution in `backend/app/workflows/dept_executor.py`; fix the 2 failing tests in `tests/test_departments_module.py` and `tests/test_phase5_and_6_completion.py`.
3. **Priority 3 (Missing Core Features):** Enforce `Department.monthly_ai_cost_cap_usd` + `UsageRecord` in `dept_ai_service.py`.
4. **Priority 4 (UX & Architecture):** Fix `390px` `User menu` overflow in `AppShell.tsx`; add `Log out` to `OnboardingPage.tsx`; auto-`fitView` on `DeptWorkflowBuilderPage.tsx`; offload approval resume to Celery.
5. **Priority 5 (Performance & Cleanup):** Fix N+1 in `workflows.py`, cache Beat SQLAlchemy engine in `tasks.py`, remove duplicate `react-router` dependency, and replace deprecated `HTTP_422_UNPROCESSABLE_ENTITY`.
