
# OpsMind – AI-Native Operations Platform

Multi-tenant SaaS where companies connect their tools and build no-code AI workers that do office jobs automatically, safely, and with a human in the loop.

- Governing spec: **OpsMind SRS v1.0 (Oct 28, 2026)**. Every task traces back to an SRS ID (FR-x, UC-xx, AI-x, NFR-x).
- Author: Abdul Moeed
- Last updated: 2026-11-02

## Tech stack (SRS section 3.1 and 13)

| Area | Choice |
|---|---|
| Backend | Python 3.12, FastAPI, Pydantic, SQLAlchemy 2.x, Alembic, pytest, ruff |
| Dependency management | uv |
| Database | PostgreSQL + pgvector (local Docker now, Supabase planned) |
| Auth | Supabase Auth (planned, Phase 2) |
| Cache / queue | Redis, Celery workers (planned, Phase 5) |
| Frontend | React, TypeScript, Vite, Tailwind, React Flow, TanStack Query (planned, from Phase 2) |
| CI | GitHub Actions |
| Constraint | Free / open-source tools and free tiers only (SRS A-3, NFR-8) |

