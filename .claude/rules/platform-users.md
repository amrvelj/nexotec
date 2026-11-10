---
paths:
  - "app/core/permissions.py"
  - "app/core/tenancy.py"
  - "app/core/ownership.py"
  - "app/ownership_registry.py"
  - "app/platform/api/dealerships.py"
  - "app/platform/api/dealer_groups.py"
  - "app/platform/api/me.py"
  - "app/platform/services/user.py"
  - "app/platform/services/membership.py"
  - "app/platform/services/ownership.py"
  - "app/platform/services/me.py"
  - "app/platform/services/photo.py"
  - "app/platform/services/signature.py"
  - "app/platform/services/user_location.py"
  - "app/platform/services/group_*.py"
  - "app/platform/models/user*.py"
  - "app/platform/models/dealership_membership.py"
  - "tests/test_user*.py"
  - "tests/test_memberships.py"
  - "tests/membership_support.py"
  - "tests/test_membership_across_dealer_groups.py"
  - "tests/test_my_account.py"
  - "tests/test_tenancy.py"
  - "tests/test_permission*.py"
  - "tests/fixtures/permission_matrix.json"
  - "tests/test_report_platform_admins_homed_in_dealerships.py"
  - "tests/architecture/test_no_ambient_group_read.py"
  - "tests/architecture/test_group_read_dealerships_callers.py"
  - "scripts/report_platform_admins_homed_in_dealerships.py"
  - "scripts/seed_staging_demo.py"
  - "scripts/retire_staging_shared_demo_user.py"
  - "frontend/apps/dms/src/auth/gatedCapabilities.ts"
  - "frontend/apps/dms/src/**/useCanWrite*"
  - "frontend/apps/dms/src/pages/UsersListPage*"
  - "frontend/apps/dms/src/components/users/**"
  - "frontend/apps/dms/src/pages/Account*"
  - "frontend/apps/dms/src/pages/account/**"
  - "frontend/apps/dms/src/components/account/**"
---
<!-- Maintainer note (stripped before Claude sees it). Summarises ADR-014, ADR-094, ADR-106 and the
Dealer Administration PRD (D-A-01, D-A-03…D-A-05, D-A-24, D-A-35; FR-A-02, FR-A-10…FR-A-17).
Cut from platform.md by KAN-614 (2026-11-10) to the invariants; platform.md was verified against
main@73baf3f on 2026-10-25 - this cut was not re-verified against the code. -->

# Users, roles and the organisation (ADR-014; Dealer Administration)

## Organisation
- `dealer_group` gives visibility and owns nothing; the `dealership` is **the tenant** (a legal
  entity: its own stock, sales, accounting, VAT); a `location` is a physical site — an attribute,
  **never a scoping boundary**, owned by platform.
- UI word for the tenant (D-A-10, CLAUDE.md): Betrieb · entreprise · azienda · business — in the
  bundles and in the backend's sign-in and mail texts. Older backend messages still say
  "dealership" (some "dealer" or "tenant"); the recovery service's own refusals say *Betrieb*
  (KAN-450 asks whether that is a defect).
- `sales` is a module entitlement like `aftersales`: a service-only garage without it is a normal
  configuration.
- Group-scoped reads live only in the files `tests/architecture/test_no_ambient_group_read.py`
  allow-lists; a new one is an ADR plus an entry. The test sees `group_id`/`dealer_group_id` in
  `==`, `.in_(`, `filter_by(…=)` and single-line `text(…)` — a multi-line `text()` escapes it.
  `platform.public.list_group_read_dealerships` has one ruled caller, Stock's group listing
  (`test_group_read_dealerships_callers.py`). Cross-tenant reads are 404, never 403.
- **A membership may cross dealer groups** (ADR-106): `grant_membership` does not ask for the
  target's group, and switch-dealership mints the **target's** `group_id` — while switched the
  person reads that group's customers, never the home group's
  (`tests/test_membership_across_dealer_groups.py`).

## Managers and memberships (D-A-01)
- No group-level administrator. The manager flag is held per Betrieb: `User.is_dealer_manager` for
  the home, `dealership_membership.is_dealer_manager` per sister (default false, never copied).
  Switch-dealership mints the target's flag (`user_service.is_dealer_manager_in`). The
  last-active-manager rule (FR-A-13) and the manager e-mail list count active memberships too.
- Memberships are granted and ended (`valid_to`; the row stays) by platform staff or by a manager of
  the home who also manages the target — a manager of one side gets 404; the home itself and a
  `platform_admin` are 422. Every write audits before/after and the granter, mirrored into the
  target. A manager sees only the sister Betriebe they manage themselves (the Users screen's
  «Weitere Betriebe»; a 404 reads «not yours to manage», an empty list never «no access»).
- **Every route that writes a user's memberships, delegations or user row locks the user row first**
  (`user_service.get_user_for_update_or_404`, FOR NO KEY UPDATE) before any child row — a source
  test pins them; a new such route joins it. (Lock order: `platform-identity.md`.)
- `LoginResponse.user.isDealerManager` is the **home** flag — never gate a screen on it; the
  session's own flag is `LoginResponse.isActiveDealershipManager`.

## Access (D-A-03; Roles & Permissions)
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

## Deactivation and hand-over (D-A-35, ADR-094)
- `OwnershipProvider` (`app/core/ownership.py`), one per context, registered in
  `app/ownership_registry.py` (customer: advised customers; sales: open offers and pending contracts
  by `owner_user_id`; the rest: nothing). A user id names one person platform-wide, so a provider
  counts what they own **in every Betrieb**. The **home manager decides for every Betrieb** the leaver
  worked in.
- `POST …/users/{id}/deactivate` needs a decision per group — an **active** colleague, or null =
  the module's unassigned queue (`GET /sales/deals?unassigned=true`, `GET /customers?unassigned=true`;
  an unassigned draft stays hidden like every draft). It cuts access first in platform's own commit,
  then each module hands over on a session of its own (ADR-047), with one `ownership_transferred`
  audit row per group. `POST …/ownership/transfers` is the retry and the plain hand-over.
- A hand-over to a colleague who cannot act in one of those Betriebe is refused **as a whole** (422
  with a count, never which Betrieb). Customers judge it by the customer's group
  (`platform.public.list_user_dealer_group_ids`), Sales by `colleague_cannot_act_there`, under the
  rows' locks. A module that fails is answered, not raised: `failed[]` with a stable `reason` and a
  message that is never an exception's text; its records stay for the retry.
- A PATCH to `deactivated` (or an employment `terminated`) is 409 `use_deactivate_endpoint`; the
  last manager is 400 `last_manager` / `last_manager_elsewhere`, never naming the sister.
- The nightly `platform.user_expiry` deactivates a user past `valid_to` (staff too), one at a time,
  except a last active manager (FR-A-13 outranks expiry: logged at ERROR, tried the next night), and
  retries a failed release for 30 days. Each module's reconciliation reports an open record owned
  by a deactivated user.

## Lifecycle (Users → Lifecycle)
- `invited → active` only by accepting the invitation (409 `accept_invitation_required`); only an
  active user is suspended; **reactivation is allowed** (PATCH `active`, with a new window when the
  old one expired) — a deactivated user with no credential comes back `invited`. Any other edit of a
  deactivated user is 409. `valid_from`/`valid_to` refuse a sign-in outside the window; an invitation
  can be accepted early (403 `access_window_closed` with the dates instead of a session).
- The one exception is `scripts/seed_staging_demo.py`'s local path, which activates its demo user on
  the row; its `create` audit rows are system writes (`actor_id=None` + `system_reason`, from
  `scripts/` only — `test_system_actor_only_from_scripts.py`). Any other environment but `staging`
  is refused. **Staging has no shared login** (ADR-113): the seed invites the first manager and keeps
  `mfa_enforced` on; README › Staging sign-in is the runbook.
- `user_location`: primary + additional (FR-A-02); a deactivated location is refused for a new
  assignment and kept where held.

## Support access (D-A-05, D-A-24) — not built
Full write from the start; the token carries `sub` = staff and `actingFor` = the user; the audit
shows both, never the dealer as actor; a 60-minute box, a mandatory reason, a persistent banner;
the user ends it; no manager notification, no manager "View as". It never touches credentials, MFA,
the manager flag or integration secrets (FR-A-17).

## My account
- `/account/{profile,security,connected-accounts,signature,notifications}` for every user. Sessions
  and MFA stay under `/v1/auth/*` (one MFA surface); `/v1/me` holds the rest. The user may change
  their own names, phones, `correspondence_language` (null = the home Betrieb's default; never the
  UI language) and signature block; the e-mail address is a 422 there.
- Signature image and photo (the manager sets them too): PNG or JPEG judged by their bytes, stored
  in the object store (`app/core/object_store.py`), the row holding only the key — never bytes or
  base64. The `local` store is refused in production at startup (the bucket is KAN-402). `hasPhoto`
  is the one field added to the session's frozen `UserRead` shape.
