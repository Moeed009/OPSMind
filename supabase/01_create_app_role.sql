-- Run once in the Supabase SQL Editor as the project postgres/admin role.
-- Replace the placeholder with a strong, unique password before execution.
-- This creates a least-privilege LOGIN role for FastAPI; it is NOT a second database.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'opsmind_app') THEN
        CREATE ROLE opsmind_app
            LOGIN
            PASSWORD 'REPLACE_WITH_A_LONG_RANDOM_PASSWORD'
            NOSUPERUSER
            NOCREATEDB
            NOCREATEROLE
            NOINHERIT
            NOBYPASSRLS;
    END IF;
END
$$;

-- Ensure rerunning this script can rotate the password intentionally.
-- ALTER ROLE opsmind_app PASSWORD 'REPLACE_WITH_A_LONG_RANDOM_PASSWORD';

GRANT CONNECT ON DATABASE postgres TO opsmind_app;
GRANT USAGE ON SCHEMA public TO opsmind_app;
