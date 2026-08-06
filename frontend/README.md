# DMS Customer UI (issue #8)

Minimal internal frontend to click through the shell as a real user: login
+ Customer list/create/edit only. No nav shell, no other modules, no visual
polish — React + TypeScript + Vite + Mantine, calling the backend REST API
directly. Not wired into CI yet (backend-only for now); this is a fast-follow
once there's something real here to lint/build-check.

Explicitly throwaway, same framing as the backend login endpoint it talks
to: replaced, not extended, once real dealer-staff SSO is a requirement.

## Local setup

Requires Node 20+. The backend (`app/`, repo root) must be running first —
see the root `README.md`.

```bash
cd frontend
npm install
cp .env.example .env   # points at http://localhost:8000/v1 by default
npm run dev
```

Open http://localhost:5173. The backend needs `DMS_CORS_ALLOWED_ORIGINS` to
include this dev server's origin for the session cookie to work cross-origin
(defaults to `["http://localhost:5173"]` — see `app/core/config.py`).

## Auth model

Session is an httpOnly cookie set by `POST /v1/auth/login` — this app never
reads, stores, or forwards the JWT itself (see `src/api/client.ts`:
`credentials: 'include'` on every request, nothing else). `GET /v1/auth/me`
restores "logged in as X" state after a page reload, since the login
response body's `user` object only lives in memory.

## Structure

```
src/api/        typed fetch client + response types (mirrors app/schemas/*.py)
src/auth/       AuthContext (session state, login/logout)
src/components/ ProtectedRoute (redirects to /login when logged out)
src/pages/      LoginPage, CustomersListPage, CustomerFormPage (create+edit)
```

## Known dependency note

`react-router-dom` is pinned to the 7.18.2 line rather than `npm audit`'s
suggested "fix" (downgrading to 7.11.0) — that downgrade would reintroduce
an earlier, more relevant CVE that 7.18.2 already patched, in exchange for
avoiding a flagged RSC-mode CSRF issue that doesn't apply here (this is a
plain client-side SPA, no React Router server/RSC features in use).
