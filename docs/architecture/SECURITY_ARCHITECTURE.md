# Security Architecture

> [!NOTE] This document outlines the security controls applied across OpsMind, focusing on authentication, authorization, data protection, and operational safeguards.

---

## 1. Authentication
- **JWT‑based authentication** – issued by Supabase Auth (or future custom provider). Tokens contain `sub` (user UUID) and `tenant_id` claims.
- **HttpOnly, Secure cookies** are set on successful login/signup; they are never accessible via JavaScript.
- **Token verification** occurs in a FastAPI dependency (`CurrentUser`). Invalid or missing tokens result in `401 Unauthorized`.

## 2. Authorization & RBAC
- **Roles** (Owner, Admin, Builder, Reviewer, Viewer, Developer) stored in `memberships.role`.
- **Permission constants** defined in `backend/app/auth/permissions.py` (e.g., `CONNECTIONS_READ`, `CONNECTIONS_MANAGE`).
- **FastAPI dependency** `has_permission` checks the caller’s role against required permission before executing the endpoint logic.

## 3. Data Isolation (Row‑Level Security)
- PostgreSQL **RLS policies** enforce tenant isolation at the database level (see `docs/architecture/DATABASE_ARCHITECTURE.md`).
- Helper functions `app_user_id()` and `app_tenant_id()` read JWT claims via `SET LOCAL` and are used in all policies.

## 4. Credential Encryption (Connectors)
- **AES‑256‑GCM** via the `cryptography` library.
- Master keys stored in env var `CONNECTOR_ENCRYPTION_KEYS` as a JSON map `{"key_id": "base64‑key"}`.
- Each credential row stores `credentials_ciphertext` and the `credentials_key_id` used for encryption. Key rotation is supported by re‑encrypting rows.
- **Additional Authenticated Data (AAD)** includes `tenant_id` and `connection_id` to bind the ciphertext to its owner.

## 5. Transport Security
- All API calls are expected to be served over **HTTPS** in production (Docker‑compose dev uses plain HTTP for local testing). 
- CORS is limited to the exact origins used by the frontend (`http://localhost:5173` and `http://127.0.0.1:5173`).

## 6. Auditing & Logging
- Every mutating operation records an entry in `audit_logs` with `tenant_id`, `action`, timestamp, and JSON details.
- The FastAPI logger writes request IDs, user IDs, and response codes to STDOUT; CI pipelines capture these logs.

## 7. Operational Safeguards
- **Rate limiting** (planned for Phase 03) will be enforced per‑connector to avoid abuse of external APIs.
- **Background job sandbox** (Celery workers) will run with the least‑privilege system user, only accessing required DB tables.
- **Secret handling** – all secrets are loaded from `.env` files; no secrets are committed to the repository.

---

*Generated on 2026‑11‑04 by Antigravity (AGY).*