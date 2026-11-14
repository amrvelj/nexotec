---
paths:
  - "app/core/tenancy.py"
  - "app/core/ownership.py"
  - "app/ownership_registry.py"
  - "app/platform/__init__.py"
  - "app/platform/public.py"
  - "app/platform/reconciliation.py"
  - "app/platform/daily_jobs.py"
  - "app/platform/api/__init__.py"
  - "app/platform/api/health.py"
  - "app/platform/models/__init__.py"
  - "app/platform/services/__init__.py"
  - "app/platform/identity/services/actor_reference.py"
  - "app/platform/reference/services/system_writes.py"
  - "app/platform/schemas/__init__.py"
  - "app/platform/identity/schemas/user.py"
  - "app/platform/identity/schemas/membership.py"
  - "app/platform/identity/schemas/me.py"
  - "app/platform/reference/schemas/patch.py"
  - "alembic/versions/platform/**"
  - "tests/test_platform_concurrent_*.py"
  - "tests/test_platform_idempotency.py"
  - "tests/test_platform_chain_renders_offline.py"
  - "app/platform/api/dealership*.py"
  - "app/platform/identity/api/dealership*.py"
  - "app/platform/organisation/api/dealerships.py"
  - "app/api/v1/dealer_groups.py"
  - "app/platform/identity/api/me.py"
  - "app/platform/identity/services/user.py"
  - "app/platform/identity/services/user_common.py"
  - "app/platform/identity/services/user_reads.py"
  - "app/platform/identity/services/membership.py"
  - "app/platform/identity/services/ownership.py"
  - "app/platform/identity/services/me.py"
  - "app/platform/identity/services/photo.py"
  - "app/platform/identity/services/signature.py"
  - "app/platform/identity/services/user_location.py"
  - "app/platform/identity/services/group_*.py"
  - "app/platform/organisation/services/group_read_dealerships.py"
  - "app/platform/identity/models/user*.py"
  - "app/platform/identity/models/dealership_membership.py"
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
<!-- Maintainer note. ADR-014/094/106; Dealer Administration D-A-01…05, D-A-24, D-A-35, FR-A-02…17. Cut from
platform.md (verified against main@73baf3f, 2026-10-25) by KAN-614, 2026-11-10; not re-verified. -->

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
  (`user_reads_service.get_user_for_update_or_404`, FOR NO KEY UPDATE) before any child row —
  `tests/test_platform_concurrent_first_grants.py` pins them; a new such route joins it. (Lock order: `platform-identity.md`.)
- `LoginResponse.user.isDealerManager` is the **home** flag — never gate a screen on it; the
  session's own flag is `LoginResponse.isActiveDealershipManager`.

## Access (D-A-03)
Roles, capabilities, the permission matrix and what the SPA may offer: `platform-permissions.md`.

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
  audit row per group carrying the reason (Customers also write one per customer; Sales bumps each
  record's version and writes none — platform's row is the record). `POST …/ownership/transfers` is the retry and the plain hand-over.
- A hand-over to a colleague who cannot act in one of those Betriebe is refused **as a whole** (422
  with a count, never which Betrieb). Customers judge it by the customer's group
  (`platform.public.list_user_dealer_group_ids`), Sales by `colleague_cannot_act_there`, under the
  rows' locks. A module that fails is answered, not raised: `failed[]` with a stable `reason` and a
  message that is never an exception's text; its records stay for the retry.
- A PATCH to `deactivated` (or an employment `terminated`) is 409 `use_deactivate_endpoint`; the
  last manager is 400 `last_manager` / `last_manager_elsewhere`, never naming the sister.
- The nightly `platform.user_expiry` deactivates a user past `valid_to` (staff too), one at a time,
  except a last active manager (FR-A-13 outranks expiry: logged at ERROR, tried the next night),
  re-reading each user under the lock; its retry of a failed release (30 days) hands over only a
  user still deactivated by the job. Each module's reconciliation reports an open record owned
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
