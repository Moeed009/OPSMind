# OpsMind Supabase Auth API setup

The backend proxies authentication requests to Supabase Auth. Passwords are never stored in the local `users` table. The local row stores the Supabase Auth user UUID, email, profile name, and application status.

## Environment

Copy `backend/.env.example` to `backend/.env` and configure:

- `SUPABASE_URL`: Project URL from Supabase project settings.
- `SUPABASE_ANON_KEY`: The project's anon key or publishable key. Do not use the `service_role` secret.
- `SUPABASE_JWT_SECRET`: Only needed if the project's JWT signing algorithm is HS256. For asymmetric signing keys, the backend fetches the project's JWKS.
- `SUPABASE_PASSWORD_RESET_REDIRECT_URL`: Frontend reset-password page. Add this exact URL to Supabase Authentication URL Configuration's Redirect URLs allowlist.
- `FRONTEND_URL`: Exact frontend origin allowed by backend CORS.
- `AUTH_COOKIE_SECURE`: Set to `true` in HTTPS production; keep `false` only for local HTTP development.

`DATABASE_URL` and `APP_DATABASE_URL` still point to the same Supabase PostgreSQL database. The first is for Alembic migrations; the second is the least-privilege application role.

## Supabase dashboard

1. Authentication -> Providers -> Email: keep Email provider enabled.
2. Authentication -> Settings: Confirm email can remain OFF for the current development flow.
3. Authentication -> URL Configuration: set the Site URL and allow `http://localhost:5173/reset-password` for local development. Add the production reset URL before deployment.
4. Use the anon/publishable key only. Never expose the `service_role` key in frontend or normal backend auth requests.

## Endpoints

- `POST /api/v1/auth/signup` — creates a Supabase Auth user and synchronizes the local `users` profile.
- `POST /api/v1/auth/login` — validates credentials with Supabase Auth.
- `POST /api/v1/auth/refresh` — rotates the session using the HttpOnly refresh cookie.
- `POST /api/v1/auth/password/reset-request` — asks Supabase to send a recovery email. The response does not reveal whether the email exists.
- `POST /api/v1/auth/password/reset` — accepts the short-lived recovery `access_token` from the recovery redirect and updates the password.
- `POST /api/v1/auth/logout` — revokes the current Supabase session when possible and clears browser cookies.

Access and refresh tokens are stored in HttpOnly cookies and are not returned in JSON. The frontend must use `fetch(..., { credentials: "include" })` (or Axios `withCredentials: true`). The recovery page must read the Supabase `access_token` from the URL hash after the recovery link is verified, then send it with `new_password` to `/api/v1/auth/password/reset`. Do not log this token or store it in localStorage.

The current password policy enforced by the API is at least 12 characters. Supabase Auth also applies the project's configured password policy and rate limits.

## Notes before production

- Set `AUTH_COOKIE_SECURE=true` when serving over HTTPS.
- Keep frontend and API on the same site where possible. If they are truly cross-site, configure `AUTH_COOKIE_SAMESITE=none` together with secure HTTPS cookies and add CSRF protection for cookie-authenticated state-changing endpoints.
- Configure CORS with the exact trusted frontend origin; do not use `*` with credentials.
- Configure Supabase SMTP for reliable password recovery email delivery.
- Run the database migration and existing RLS tests before enabling signup for real users.
