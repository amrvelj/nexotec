---
paths:
  - "app/main.py"
  - "app/core/nul_guard.py"
  - "app/core/auth*.py"
  - "app/platform/session_guard.py"
  - "app/platform/api/auth.py"
  - "app/platform/identity/services/identity.py"
  - "app/platform/identity/services/auth_*.py"
  - "app/platform/identity/models/auth_*.py"
  - "app/platform/identity/models/dealership_auth_config.py"
  - "tests/test_auth.py"
  - "tests/test_login.py"
  - "tests/test_identity_*.py"
  - "frontend/apps/dms/src/auth/**"
  - "frontend/apps/dms/src/components/ProtectedRoute.tsx"
---
<!-- Maintainer note. Auth & Identity PRD v1.1 (sessions), D-A-36. Moved out of platform-identity.md by
KAN-615 (2026-11-13) so that app/main.py loads only this. Cut from platform.md (verified against
main@73baf3f) by KAN-614; not re-verified. -->

# Sessions and the request guards

- **A NUL character** in path, query or JSON/form body is 422 `nul_character` before any route
  (`app/core/nul_guard.py`: refuse, never strip); at the login door it is the same 401 as every
  refusal (`platform-identity.md`).
- One `session` row per login. `enforce_live_session` (app-level in `app/main.py`) 401s a revoked,
  idle or expired row — the absolute end is **the next midnight Europe/Zurich** — and touches the
  row at most once a minute, never for `X-DMS-Background` requests (an unattended tab still idles
  out). Logout is the one route a dead session reaches.
- Every session of the user is revoked by: suspension or deactivation, a change of roles, manager
  flag or membership (the claims live for the day), a password reset, an accepted invitation,
  staff `revoke-sessions` — each a `session_revoked` event.
- A Betrieb's `dealership_auth_config.session_*` (15–120 min, 1–12 h) shortens them; never staff's.
