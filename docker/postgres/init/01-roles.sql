-- Table privileges are granted per table by the migration. No default privileges on purpose:
-- they would give opsmind_app UPDATE/DELETE on the append-only tables.
CREATE ROLE opsmind_app LOGIN PASSWORD 'opsmind_app' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
GRANT CONNECT ON DATABASE opsmind TO opsmind_app;
GRANT USAGE ON SCHEMA public TO opsmind_app;