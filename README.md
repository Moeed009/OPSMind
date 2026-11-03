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
