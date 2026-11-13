---
paths:
  - "app/core/permissions.py"
  - "app/platform/services/user.py"
  - "app/platform/services/user_common.py"
  - "app/platform/services/membership.py"
  - "app/platform/schemas/user.py"
  - "app/platform/schemas/auth.py"
  - "app/platform/api/auth.py"
  - "tests/test_permission*.py"
  - "tests/fixtures/permission_matrix.json"
  - "tests/test_user*.py"
  - "tests/test_memberships.py"
  - "tests/test_report_platform_admins_homed_in_dealerships.py"
  - "scripts/report_platform_admins_homed_in_dealerships.py"
  - "frontend/apps/dms/src/auth/gatedCapabilities.ts"
  - "frontend/apps/dms/src/**/useCanWrite*"
  - "frontend/apps/dms/src/pages/UsersListPage*"
  - "frontend/apps/dms/src/components/users/**"
---
<!-- Maintainer note. Dealer Administration D-A-03, D-A-18; the PRD's Roles & Permissions matrix.
Moved out of platform-users.md by KAN-615 (2026-11-13) so that app/core/permissions.py loads only
this. Cut from platform.md (verified against main@73baf3f) by KAN-614; not re-verified. -->

# Roles and permissions (D-A-03)

- `User.access_roles` plus the manager flag. `platform_admin` is Nexotec staff only: only a
  `platform_admin` principal may grant or remove it or edit a user who holds it (403 otherwise). A
  `platform_admin` gets no sister membership and a member cannot get the role (422);
  `scripts/report_platform_admins_homed_in_dealerships.py` reports the ones homed in a Betrieb (exit
  1), never fixes them.
- Capabilities, not role checks: `Capability(read_roles, write_roles, manager_can_write,
  manager_can_read)` in `app/core/permissions.py`; `require_read`/`require_write` check
  platform_admin, then the roles, then the manager flag. `read_roles=None` = any role; an empty set
  = manager only; an empty `write_roles` = staff or the manager, except the rows with
  `manager_can_write=False` (the "Nobody", platform-only and self-only rows).
  `tests/fixtures/permission_matrix.json` mirrors the PRD's matrix; `test_permission_matrix_mirrors_prd.py`
  fails on any difference (a row may declare `enforced_write` + a `deviation` ticket; none today).
- **The SPA learns what it may offer from the session** (Anto): `LoginResponse.writableCapabilities`
  (login, `/auth/me`, switch) lists the rows the user may write in the active Betrieb; `useCanWrite`
  **hides** an action outside it (disabled-with-reason stays for state, ADR-061) — the action only,
  never what the role may read. The names a screen asks about are `src/auth/gatedCapabilities.ts`,
  each a matrix row — never a role table in the frontend.
- Policy limits are not in v1 (D-A-18): no `dealer_policy_limit`, no screen, no row.
