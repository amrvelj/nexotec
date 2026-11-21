---
paths:
  - "app/core/impersonation.py"
  - "app/platform/identity/session_guard.py"
  - "app/platform/identity/api/support_sessions.py"
  - "app/platform/identity/services/support_sessions.py"
  - "app/platform/identity/schemas/support_session.py"
  - "app/platform/identity/models/support_session.py"
  - "app/platform/identity/api/platform_staff.py"
  - "app/platform/identity/services/platform_staff.py"
  - "app/platform/identity/schemas/platform_staff.py"
  - "tests/platform/test_kan221_*.py"
  - "frontend/apps/dms/src/shared/supportSession/**"
  - "frontend/apps/dms/src/components/supportSessions/**"
  - "frontend/apps/dms/src/pages/SupportSessionLogPage*"
  - "frontend/apps/dms/src/pages/PlatformUsersPage*"
  - "frontend/apps/dms/src/pages/SupportSessionScreens*"
  - "frontend/apps/dms/src/layout/DmsShell.supportSession*"
---
<!-- Maintainer note. Dealer Administration D-A-05, D-A-24, FR-A-15, FR-A-17, FR-A-18; ADR-188. Cut from
platform-users.md by KAN-221 PR-6 (2026-11-21), its budget being full. -->

# Support access and platform staff (KAN-221)

- **The record:** `support_session` — `POST /v1/platform/support-sessions` (staff with a second
  factor, never under `acting_for`; not a platform_admin, oneself or an inactive user; one running
  session per staff member; a reason is mandatory; 60 minutes), `…/{id}/end` (the staff member or
  the user; anyone else 404). Past `expires_at` every read says `timeout`; the nightly
  `platform.support_sessions.close_expired` writes it into the row. **Grants nothing before PR 3**
  (the `acting_for` token, preserved `auth.py`/`audit.py`: attended session only).
- **Logs:** `GET /v1/dealerships/{id}/support-sessions` (the manager, own Betrieb; another is 404)
  and `GET /v1/platform/support-sessions` (staff; `dealershipId`, `staffUserId`); `sort` over the
  session's own columns (`startedAt:desc` = newest first, the screens' order). Every start and end is
  also an audit row on the Betrieb — the staff member or the user as actor, a timeout a system write.
- **The banner:** `GET /v1/me/support-session` (the session running on the caller — the user it
  supports, or the staff member under `acting_for`), polled on focus and each minute as a
  background request (no Web Push adapter). A **persistent** banner in the shell's one notice slot
  (ADR-084, `ui-shell.md`): staff, reason, end time, «Sitzung beenden»; no dismiss; the user ends it.
  The read and the ending pass the confining gates (`session_guard._support_session_path`): a
  session that owes a factor and a manager under the legal hold still see and end it.
- **Platform users** (`/v1/platform/staff`, ADR-188): grant = a staff account in the platform
  tenant, invited; revoke (reason, If-Match) = its deactivation, or the role taken off a legacy holder
  (`staff_role_is_the_only_role` when it is their only one); never oneself
  (`platform_admin_self_revoke`), never the last active holder (`last_platform_admin`). Both need a
  second factor and are refused under a support session.
- **FR-A-17:** `core/impersonation.py` answers 403 `impersonation_excluded` on the routes
  `test_kan221_impersonation_exclusions.py` names (a new credential, MFA, manager-flag, secret,
  staff-role or impersonation route joins it), on a PATCH of `isDealerManager`/`email`, a new
  manager and a connection's `config`. The user gets `security.impersonation_started`/`_ended`, the
  manager nothing (D-A-24).
- **Ruling (Anto):** full write from the start, no elevation step; `sub` = staff, `actingFor` = the
  user, **both audited, never the dealer as actor**; 60 minutes; a mandatory reason; a persistent
  banner the user ends; no manager notification; no "View as".
