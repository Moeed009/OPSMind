# Phase 03 – Connectors (Design Only)

> [!NOTE] This document records **design‑only** work for Phase 03. No code has been written yet; the implementation will follow the specifications below.

---

## 1. Phase Overview
The Connectors phase introduces a **provider‑agnostic framework** that lets tenant admins link external services (starting with Gmail) to OpsMind. It defines encryption, a unified connector interface, registration/registry mechanisms, APIs, and the UI needed to manage connections.

## 2. Objectives (SRS FR‑6.1 → FR‑6.6, UC‑02, AC‑5, AC‑6)
| # | Objective | SRS Ref | Status |
|---|-----------|---------|--------|
| 3.1 | Connector foundation – encrypted credentials, abstract interface, registry, CRUD API, audit entries | FR‑6.1 | ✅ Design complete |
| 3.2 | Generic OAuth flow – authorize URL, signed state, callback, encrypted token store | FR‑6.2 | ✅ Design complete |
| 3.3 | Gmail connector – token refresh, message read, history import | FR‑6.3 | ✅ Design complete (requires real Google account) |
| 3.4 | Event ingestion – polling (fallback), dedup, tenant resolution | FR‑6.4 | ✅ Design complete |
| 3.5 | Rate‑limit / back‑off, health monitoring, status badges | FR‑6.5 | ✅ Design complete (implementation deferred) |
| 3.6 | Frontend – Connections page (list, connect, disconnect, status) | FR‑6.6 | ✅ Design complete |
| 3.7+| Additional connectors (minimum 10) – Slack, Calendar, Drive, HubSpot, Helpdesk, Billing, … | – | ⏳ Planned |

## 3. Existing Foundations
- **`connections` table** (already in the DB) with columns:
  - `id`, `tenant_id`, `provider`, `external_account_id`, `credentials_ciphertext`, `credentials_key_id`, `status`, `sync_cursor`, `webhook_expires_at`, `last_error`
  - **UNIQUE** (`tenant_id`, `provider`, `external_account_id`).
- **`events` table** with a deduplication key (`dedup_key`).
- **Permissions** already defined:
  - `CONNECTIONS_READ` – all roles.
  - `CONNECTIONS_MANAGE` – only Owner/Admin.

## 4. Design Decisions
### 4.1 Credentials Encryption
- **Algorithm:** AES‑256‑GCM via the `cryptography` Python library.
- **Master key storage:** `CONNECTOR_ENCRYPTION_KEYS` env var – a JSON mapping `{ "key_id": "base64‑key" }`.
- **Key rotation:** `credentials_key_id` column references the key used for that row; rotation is handled by re‑encrypting rows with a new key (offline migration script).
- **Scope:** Encryption happens **application‑side**; we do **not** rely on Supabase Vault to keep the design zero‑cost and provider‑agnostic.
- **Binding:** Ciphertext is bound to the `tenant_id` + `connection_id` via additional AAD (Additional Authenticated Data) – the GCM tag includes these IDs, preventing ciphertext replay across tenants.

### 4.2 Connector Architecture
```
app/
├── connectors/
│   ├── base.py          # abstract Connector class (init, authorize, refresh, fetch_events, health)
│   ├── registry.py      # global registry mapping provider name → Connector subclass
│   ├── crypto.py        # encrypt/decrypt helpers using master keys
│   └── gmail/
│       ├── __init__.py
│       └── connector.py # GmailConnector implements base methods
├── api/v1/connections.py # FastAPI router exposing CRUD endpoints
└── models/connection.py  # SQLAlchemy model (already exists)
```
- **`Connector` interface** defines: `authorize_url(state)`, `handle_callback(params)`, `refresh_token()`, `fetch_events(since_cursor)`, `health()`.
- **Registry** is a singleton dict populated at import time; new connectors just subclass `Connector` and register via `@register("gmail")`.
- **Audit entries** recorded in `audit_logs` table (existing) with `action="connection_*"` and JSON payload.

### 4.3 OAuth Flow (Generic, FakeConnector for tests)
1. **Client → `/connections/oauth/authorize?provider=gmail`** → server builds **Google auth URL** with:
   - `client_id`, `redirect_uri` (`http://localhost:5173/api/v1/connections/oauth/callback`), `scope`, `state`.
2. **`state`** is a signed JWS (HS256) containing:
   - `tenant_id`, `user_id`, `provider`, `nonce`, `exp` (10 min).
3. User authenticates with Google → redirect to **callback URI**.
4. Server verifies JWS, ensures the logged‑in user matches `user_id` and has `CONNECTIONS_MANAGE` permission.
5. Exchange `code` for `access_token`/`refresh_token`.
6. **Encrypt** tokens with `crypto.encrypt()` using the current master key; store ciphertext and `key_id`.
7. Return **connection ID** and status `CONNECTED`.

**FakeConnector** (used in unit tests) implements the same flow but skips external network calls and returns deterministic dummy tokens.

### 4.4 Gmail Connector Details
- **Token refresh**: `refresh_token()` calls Google token endpoint, updates encrypted storage.
- **Message fetch**: `list_messages(query, page_token)`; uses `users.messages.list` & `users.messages.get`.
- **History import**: `fetch_events(since_cursor)` calls `users.history.list` with `startHistoryId` (stored in `sync_cursor`).
- **Polling strategy**: initial implementation is a **manual endpoint** (`POST /connections/{id}/poll`) that triggers a one‑off fetch; later a background job (Celery) will call it on a schedule.
- **Unique‑row update**: If the same Gmail address is re‑connected, we **UPDATE** the existing row (unique constraint prevents duplicate rows).
- **Disconnect**: `POST /connections/{id}/revoke` → Google token revocation endpoint, then set `status="REVOKED"` and erase `credentials_ciphertext` (but keep the row for audit/event traceability).

### 4.5 Event Ingestion (Polling → Dedup → Tenant Resolve)
1. **Polling endpoint** returns raw Gmail messages/events.
2. For each item a **dedup key** (`provider:message_id`) is generated.
3. Insert into `events` table with `ON CONFLICT DO NOTHING` to guarantee idempotency.
4. **Tenant resolution** is performed via a **SECURITY DEFINER** Postgres function `app_find_tenant_by_connection(connection_id)` (similar to `app_find_invitation`). This function:
   - Looks up the `connections` row, extracts `tenant_id`.
   - Returns tenant_id for the trigger that inserts the event.
5. A migration will create this function and grant it to the `anon` role for webhook callbacks.

### 4.6 Rate‑limit / Back‑off & Health Monitoring
- **Rate‑limit**: Connector base class exposes `max_requests_per_minute`; a simple in‑memory token bucket is used for polling (future Celery jobs will respect it).
- **Health badge**: `GET /connections/{id}/health` returns JSON `{status: "ok"|"error", last_error: <msg>}`; UI shows a colored badge.
- Implementation will be added in later phases (Phase 5 background jobs).

### 4.7 Frontend – Connections Page
- **Route**: `/settings/connections` (React component `ConnectionsPage`).
- **Features**:
  - List all connections for current tenant (GET `/api/v1/connections`).
  - Show provider icon, status badge, last sync time.
  - **Connect** button (opens modal → selects provider → initiates OAuth flow).
  - **Disconnect** button (calls DELETE `/api/v1/connections/{id}` → revokes token). 
  - **Manual poll** button for Gmail (calls POST `/api/v1/connections/{id}/poll`).
- **Permissions**: UI only renders connect/disconnect controls if the user has `CONNECTIONS_MANAGE`.
- **State management**: TanStack Query caches the list; mutations invalidate the list on success.

### 4.8 Future Connectors (minimum 10)
After Gmail is stable, the same pattern will be used for:
1. Slack
2. Google Calendar
3. Google Drive
4. HubSpot
5. Zendesk (helpdesk)
6. Stripe (billing)
7. Microsoft Teams
8. Asana
9. Intercom
10. Freshdesk
Each will have its own subclass of `Connector` and a small declarative config (scopes, token endpoints).

## 5. Pending Diagnostic Outputs (404 errors)
We have launched three diagnostic commands to capture the current 404 responses:
- `curl -i http://localhost:8000/api/v1/auth/signup` → task‑431 log.
- `curl -i http://localhost:8000/api/v1/me` → task‑433 log.
- Frontend login flow verification (manual) – still pending.

The tasks are running in the background. Once completed, their logs will be linked here:
- **Signup 404 log:** [task‑431.log](file:///C:/Users/AMK/.gemini/antigravity/brain/ec5e2263-4357-41d1-94d8-5f47b5e408e5/.system_generated/tasks/task-431.log)
- **/me 404 log:** [task‑433.log](file:///C:/Users/AMK/.gemini/antigravity/brain/ec5e2263-4357-41d1-94d8-5f47b5e408e5/.system_generated/tasks/task-433.log)

When the logs are available we will embed the exact HTTP status, headers, and body to aid debugging.

## 6. Acceptance Criteria (for design phase)
- [x] Complete **connector encryption** design (key storage, AAD binding).
- [x] Define abstract `Connector` interface and registration mechanism.
- [x] Document **OAuth flow** with signed state and callback handling.
- [x] Provide a **Gmail connector** design spec (token refresh, message fetch, history import).
- [x] Outline **event ingestion** pipeline (polling → dedup → tenant resolve).
- [x] Sketch **frontend Connections page** wireframe and API contracts.
- [ ] Review design with team and get sign‑off before moving to implementation (Phase 3 code).

---

*Generated on 2026‑11‑04 by Antigravity (AGY).*