---
paths:
  - "app/platform/**"
  - "app/core/auth*.py"
  - "app/core/permissions.py"
  - "app/core/tenancy.py"
  - "app/core/config.py"
  - "app/main.py"
  - "tests/architecture/test_no_password_credential_storage.py"
  - "tests/architecture/test_no_layout_code_outside_document_render.py"
  - "frontend/apps/dms/src/auth/**"
  - "frontend/apps/dms/src/**/*ignIn*"
  - "frontend/apps/dms/src/**/ProtectedRoute*"
  - "frontend/apps/dms/src/**/ReferenceDataPage*"
  - "frontend/apps/dms/src/**/*ogin*"
  - "frontend/apps/dms/src/layout/DmsShell.tsx"
  - "tests/architecture/test_no_ambient_group_read.py"
  - "frontend/packages/ui-kit/src/components/DocumentPreview.tsx"
  - "tests/test_auth.py"
  - "tests/test_user.py"
  - "tests/test_tenancy.py"
  - "tests/test_login.py"
  - "alembic/versions/platform/**"
  - "WORKSHOP_TERMINAL_AUTH_DESIGN.md"
---
<!-- Maintainer note (stripped before Claude sees it). Summarises ADR-014, ADR-051, the Dealer
Administration PRD v1.2 and Authentication & Identity v1.1 (rulings D-A-01…D-A-41, 2026-09-20 and
2026-10-07; they absorb Roles & Permissions v0.2 and supersede ADR-027). Verified against
main@046634d on 2026-10-17 (KAN-195: every present-tense claim checked against the code, every
"not built" claim grepped for). "Not built" lines are re-checked weekly by /drift-audit. -->

# Platform: organisation, access, administration, authentication

## Organisation (ADR-014, D-A-10)

- `dealer_group` gives visibility and owns nothing → `dealership` is **the tenant**, a legal
  entity with its own stock, sales, accounting and VAT → `location` is a physical site: an
  attribute, **never a scoping boundary** (owned by platform, not inventory).
- **The UI word for the tenant is "Betrieb"** in German (D-A-10) — *entreprise* in French, *azienda*
  in Italian, *business* in English (KAN-381, Anto 2026-10-18), in the bundles and in the backend's
  sign-in and mail texts (API error `message`s still say "dealership"): service-only garages are customers from
  day one, so `sales` is a module entitlement like `aftersales` (`module_entitlement`, KAN-198),
  and a Betrieb without it is a normal configuration. `dealership` stays the table and code name.
- Tenant scope comes from the token. Group-scoped reads exist only in the files that
  `tests/architecture/test_no_ambient_group_read.py` allow-lists — `core/tenancy.py`
  (`get_group_read_or_404`), `customer/services/customer.py`, `customer/services/legal_basis.py`,
  `inventory/services/group_listing.py`, `platform/services/dealership.py` (staff-only listing),
  `platform/services/group_labels.py` (KAN-371, ADR-093: the History tab's names). The test catches
  `group_id` / `dealer_group_id` in `==`, `.in_(`, `filter_by(…=)` and single-line `text(…)`
  (a multi-line `text()` escapes it). A new group read is an ADR plus an allowlist entry.
  Cross-tenant reads return 404, never 403.
- **No group-level administrator** (D-A-01). The manager flag is held per dealership (KAN-98):
  `User.is_dealer_manager` for the home dealership, `dealership_membership.is_dealer_manager`
  per sister dealership (default false, never copied from the home flag).
  `POST /v1/auth/switch-dealership` mints the target's flag via `user_service.is_dealer_manager_in`.
  The last-active-manager rule (FR-A-13, `_assert_not_last_manager`) and the manager e-mail list
  count managers by membership too — active memberships only (`valid_to` NULL or future).
  **Built (KAN-199):** `POST/GET /v1/dealerships/{id}/users/{user_id}/memberships`,
  `DELETE …/memberships/{dealership_id}` (ends with `valid_to`, the row stays — Anto,
  2026-10-17), `PUT …/memberships/{dealership_id}/manager-flag` and `PUT …/users/{user_id}/manager-flag`
  (`app/platform/services/membership.py`): platform staff, or a manager of the home who also
  manages the target — a manager of one side gets 404 for the target; the home itself and a
  `platform_admin` user are 422. Every write audits with before/after and the granter: `membership_grant` /
  `membership_end` on the home tenant and mirrored into the target's, `manager_flag_set` in the
  dealership the flag is held in (the home for the user row, the target for a membership). The
  listing shows a dealer manager only the sister dealerships they manage themselves.
  Every user-administration route in `app/platform/api/dealerships.py` that writes a user's
  memberships, text delegations or the user row (PATCH user, membership grant/end, both
  manager-flag PUTs, delegation PUT/DELETE, since KAN-200 the deactivation step, the
  hand-over, the signature PUT/DELETE and the invitation re-send, since KAN-201 the manager's
  revoke of a connected account; My account's own writes take the caller's row the same way,
  pinned by their own source test) loads the user with
  `user_service.get_user_for_update_or_404` (FOR NO KEY UPDATE) **before** it touches a child row
  (KAN-380): two first grants are then a 201 and a 409, and a route that locked child rows first
  would deadlock against one that locked the user first (a source test pins the thirteen, the connected-account revoke included; platform staff's recovery routes take it the same way through `recovery.get_user_for_recovery_or_404`, pinned by their own source test — KAN-202). The
  front door (invitation acceptance, password-reset confirm) locks the user row **before** it
  consumes its single-use token (KAN-400: an e-mail change holds the user row, then ends the
  tokens), so every path that writes a user row takes user row → token → credential in that
  order; it touches no membership or delegation. **Every login path takes the user row first too**
  (`identity.lock_user_for_login`; since KAN-200 a login stamps `last_login_at`): the password step
  before it checks the password and issues a challenge (the credential re-read under the lock, so a
  reset that committed meanwhile is the one it meets; the throttle judged again), the MFA and
  passkey steps before they claim the challenge or touch a factor, and the mint (`_finish_login`)
  re-locks — so every login path and every path that writes the user row or the credential takes
  user row → token → factor → credential, and a login's status is judged under the lock: a login
  racing a deactivation is refused. **Not yet one order (KAN-411):** the paths that take no
  user-row lock — the reset request, the account's own MFA routes (`/auth/mfa/totp*`,
  `/auth/mfa/recovery-codes*`, `/auth/mfa/passkeys*`; the login's verify steps do lock) — still
  deadlock against each other or a revoke-all in known pairs. Staff's MFA and session resets
  lock the user row first since KAN-202; whether that closes the pairs KAN-411 names for them
  is not verified. The nightly
  `platform.user_expiry` re-reads each due user under the lock and judges it again, and its retry
  re-reads each listed user and hands over only one still deactivated by the job. The first auth-config PUT, the first TOTP enrolment and the first preference save
  insert in a savepoint and carry on with a concurrent winner's committed row (KAN-393).
  `LoginResponse.user.isDealerManager` is the home flag, not the active dealership's — never
  gate a screen on it; the session's own flag is the token claim.

## Access — what exists today

`User.access_roles` (a list) plus `is_dealer_manager`. `platform_admin` is Nexotec staff only;
it is an ordinary `AccessRole` value, so `app/platform/services/user.py` (`create_user` /
`update_user`, required `actor_roles`) guards it (KAN-97): only a `platform_admin` principal may
grant or remove it, or edit a user who holds it — anyone else gets 403. **Still true:** a
`platform_admin` can be homed in a dealership; platform staff have no storage of their own
until the Platform users panel exists. KAN-199 stops it spreading: a `platform_admin` user gets
no sister membership (422), a user with an active membership cannot be given the role (422),
and `scripts/report_platform_admins_homed_in_dealerships.py` reports the existing rows (exit 1
when any) — never fixes them.

Permissions are capability-based: `Capability(read_roles, write_roles, manager_can_write=True,
manager_can_read=True)` in `app/core/permissions.py`; `require_read` / `require_write` check
platform_admin, then the roles, then the manager flag (if `manager_can_read` / `manager_can_write`).
`read_roles=None` = any role; an empty set = manager only. An empty `write_roles` = platform staff
or the manager — except the "Nobody" rows (`audit_logs`, `wagenbuch`, `stock_reservation`, the
derived views, `module_entitlements`, `ui_translations`), which the manager cannot write.

**Session minting (KAN-141, KAN-196).** Every session token is minted through `_mint_session` in
`app/platform/api/auth.py` — login, the MFA step, invitation acceptance and switch-dealership.
It refuses a suspended or deactivated user (401), authorises the target dealership from the
home dealership plus `dealership_membership` rows, never the token's `memberships` claim (403),
and writes the `session` row the guard checks (a switch keeps the row and its `sid`). Auth &
Identity risk 4 puts such checks where every minting path passes. **Open, not settled here
(KAN-254):** the ADR-028 terminal path (`WORKSHOP_TERMINAL_AUTH_DESIGN.md`) is designed to call
`create_access_token` directly with fixed `{TECHNICIAN}` roles, which `_mint_session` cannot
mint — how it gets the status check needs an ADR. `get_current_principal` still does not check
`status`; what ends a deactivated user's session is the `session` row the status change revokes
(FR-A-10) — a token without `sid` is not covered.

## Dealer Administration — decided; built only where a bullet says so (check the code)

Rulings D-A-01…D-A-41 live in the PRD; the ones a session is most likely to get wrong:

- **One administration surface (A-RULE-0).** Modules read configuration, never own it. The
  same rule applies to mail (A-RULE-9) and customer messaging (A-RULE-10): no module composes
  a sender or calls a messaging provider; it registers a document type with the platform.
- **Access = entitlement × role (D-A-04) — built (KAN-198).** `module_entitlement` per
  dealership (`sales` · `aftersales` · `parts` · `finance` · `reporting`), written only by
  `platform_admin` (`PUT /v1/dealerships/{id}/entitlements`), read by the manager; token claims
  `entitlements` + `ent_v`; `require_entitlement(ModuleCode.X)` in `app/core/permissions.py` is a
  **router-level** dependency on every sales, inventory, valuation and configurator router, runs
  before any role check and returns **404** for everyone, platform staff included. A changed set
  bumps `dealership.entitlements_version`; a token with another `ent_v` gets 401 on its next
  gated request and signs in again. `CAPABILITY_MATRIX` carries every PRD matrix row; `tests/fixtures/permission_matrix.json`
  mirrors the Notion table and `test_permission_matrix_mirrors_prd.py` fails on any difference —
  except a row that declares `enforced_write` + a `deviation` ticket (today only `stock_items`,
  KAN-376), where the code deliberately enforces less than the matrix grants.
  `Capability.manager_can_read=False` marks the platform-only and self-only rows.
- **Permissions are platform-defined (D-A-03). Policy limits are NOT in v1 (D-A-18)** — no
  `dealer_policy_limit`, no screen, no matrix row.
- **Copy configuration from a sister dealership** (D-A-01, FR-A-01): a one-shot, audit-logged
  copy — never inheritance.
- **Onboarding (D-A-06):** platform staff enter group, legal entity (UID uniqueness checked),
  entitlements and one manager; **Nexotec sets nothing commercial** — no VAT rate, no bank details.
- **Standard texts are delegable per category** (`standard_text_delegation`, FR-A-28) — built
  (KAN-199): `PUT/GET/DELETE /v1/dealerships/{id}/users/{user_id}/text-delegations` (manager
  or platform staff; full replacement; audit `text_delegation_set`; ended by deactivation);
  categories `document · email · sms · internal_note · workshop`. The gate is
  `require_write_or_delegated("standard_texts")` in `app/core/permissions.py`, which asks the
  checker platform registers (`app/platform/services/text_delegation.py`) for the route's
  `{category}` — proven on a probe route; no standard-text endpoint exists yet. The seed
  library and the UI strings (`ui_translation`, served at runtime, not built) are edited by
  `platform_admin` through the UI (D-A-23). Dealers never override UI strings.
- **Number ranges are gapless per legal entity**, one sequence across all sites (D-A-17);
  `{location_code}` is a label. Frozen at activation (A-RULE-3).
- **Activation gate (D-A-21):** a Betrieb is `pending_onboarding` until every applicable
  checklist item is complete and the manager accepts the current ToS and DPA (D-A-34); until
  then only Dealer Administration and My account are reachable. Today only the status value
  exists (`DealershipStatus.PENDING_ONBOARDING`); nothing enforces the gate.
- **Column corrections (D-A-22, D-A-25) — built (KAN-197):** `dealership.uid` is plain,
  public, checksum-validated (`validate_swiss_uid`) and unique (`uq_dealership_uid`; NULL only
  on a row the migration reported), plus `vat_number`, `vat_liable`, `trade_name`, postal code
  12 and nullable `correspondence_address_*` / `billing_address_*` blocks. `tax_id`,
  `dealer_license_number` and `license_state` are gone; `uid` is required on create and a
  duplicate is 409.
- **Support access (D-A-05, D-A-24):** a Nexotec employee steps into the user's session with
  **full write from the start**; token carries `sub` = staff and `actingFor` = the user; audit
  shows both, never the dealer as actor; 60-minute box, mandatory reason, persistent banner;
  **the user ends it from the banner; no manager notification, no manager "View as."** It can
  never touch credentials, MFA, the manager flag or integration secrets (FR-A-17).
- **Deactivation includes a reassignment step** (D-A-35) — built (KAN-200): `OwnershipProvider`
  in `app/core/ownership.py`, one implementation per context registered by the composition root
  `app/ownership_registry.py` (customer: the customers a user advises; sales: open offers and
  pending contracts, which carry a responsible seller `owner_user_id` since KAN-200, Anto
  2026-10-19; inventory, aftersales, parts: nothing per user). A user id names one person
  platform-wide, so a provider counts what the user owns **everywhere** — a sister-Betrieb
  member's offers there included (`platform.public.get_user_acting_in` is how Sales admits a
  seller: homed or an active member, status active or invited) — and a hand-over to a colleague
  who cannot act in one of those Betriebe is refused as a whole (422 saying how many records
  are elsewhere, never which Betrieb — an id is a fact the caller may not hold): the manager
  picks the queue for that group, and that Betrieb hands them over from its own queue. **The
  home manager decides for every Betrieb the leaver worked in** (Anto, 2026-10-20, ADR-094) —
  not the sister's queue only, not a refusal until the sister handed over. `GET …/users/{id}/ownership`
  lists the groups; `POST …/users/{id}/deactivate` needs a decision for every group (an
  **active** colleague, or null = the module's own unassigned queue: `GET /sales/deals?unassigned=true`
  (open offers and pending contracts; an unassigned **draft** stays hidden like every draft on the
  Sales grid and is reached from its customer's tab — Anto, 2026-10-20; so does an offer freed by a
  cancelled contract, which the grid still shows as that cancelled contract — KAN-409), `GET /customers?unassigned=true`,
  both filterable on screen), cuts
  access first in platform's own commit, then each module hands over on a session of its own
  (ADR-047) with one `ownership_transferred` audit row per group carrying the manager's reason
  (Customers also writes one per customer with it; Sales bumps each offer's and contract's
  version and writes no audit row of its own — platform's row is the record); `POST …/ownership/transfers` (`reason` optional)
  is the retry and the plain hand-over — the row menu's "hand over" entry. A PATCH `status: deactivated` (or an employment
  `terminated` on a user who is not deactivated, whatever status the body carries beside it)
  is 409 `use_deactivate_endpoint`; the last-manager refusal is 400 with `reason: last_manager` (`last_manager_elsewhere` when the person is the last manager of a sister Betrieb — still no id). **The lifecycle is
  enforced** (Users → Lifecycle): `invited → active` happens only by accepting the invitation
  (PATCH `status: active` on an invited user is 409 `accept_invitation_required`), only an
  active user can be suspended (409 `not_active`), and **reactivation is allowed**: PATCH
  `status: active`, with `employmentStatus` when the employment ended with the user and a new
  `validFrom`/`validTo` when the old window expired — a
  deactivated user who holds no credential (deactivated while invited, or after a staff MFA/
  credential reset) comes back as `invited`, not `active`, and the manager resends the
  invitation; any other edit of a deactivated user stays 409. The one exception to "only by
  accepting" is `scripts/seed_staging_demo.py`, which activates its demo user on the row (nobody
  to mail), audited with reason `seed_staging_demo` — the same bootstrap exception as its
  password. A module that fails during a hand-over is answered, not raised: `failed` on the
  deactivation's and the transfer's response names the group with a stable `reason` (the
  module's own refusal code, or `module_failed`) and a message that is never an exception's
  text; the records stay with the user for the retry. The 422 of an incomplete step carries
  `details.problems[]` with the same shape (`reason`: `undecided`, `decided_twice`, `not_owned`,
  `self`, `colleague_not_active`, or a module's code such as Sales'
  `colleague_cannot_act_there`), which the SPA translates. The nightly
  `platform.user_expiry` job (FR-A-12, worker only — not on staging) deactivates a user whose
  `valid_to` has passed with platform's own authority (a staff account expires too), one user
  at a time (a failure on one is logged, the rest go on), releases their records to the queues,
  and retries a failed release for 30 days; each module's nightly reconciliation reports an open
  record whose owner is deactivated (rule 10). `valid_from`/`valid_to` refuse a sign-in outside
  the window (the validity is checked as it will be after a PATCH); an invitation can be
  accepted before `valid_from` (password set, account active), the acceptance answering 403
  `access_window_closed` with the dates instead of a session. `user_location` (primary +
  additional, FR-A-02) and `GET …/locations` (read-only: no Locations screen yet). The Users
  screen is `/administration/users` (one shared invite/edit dialog, the deactivation dialog;
  server-side sort; no delete). `LoginResponse.user` and `GET /auth/me` keep their pre-KAN-200
  shape and schema name (`UserRead`); the administration fields are `UserAdminRead`'s alone.
- **Mail (D-A-28):** platform mail (invites, resets, alerts) is always Nexotec's own sender;
  customer-facing mail resolves per document type through `sending_policy` to
  `user_mailbox` · `business_mailbox` · `verified_domain` · `nexotec_on_behalf`.
- **Notifications (D-A-40):** staff notifications are in-app and push only, never e-mail; the
  user decides everything; `security` family is always immediate and cannot be switched off.
- **Customer messaging (D-A-41):** one platform send dialog for e-mail, SMS, WhatsApp; the
  Betrieb brings its own SMS/WhatsApp provider; consent lives on the customer.
- **Not in v1, by ruling:** policy limits (D-A-18), external users (D-A-20), custom fields and
  tags (D-A-29), Saldosteuersatz (D-A-27), customer logins (D-A-13), API clients (D-A-14),
  Zefix auto-fill, AI translations, training mode.

- **My account (KAN-201)** is `/account/{profile,security,connected-accounts,signature}` for
  every user. Sessions and MFA stay under `/v1/auth/sessions` and `/v1/auth/mfa/*` (KAN-196) —
  Anto, 2026-10-20: one MFA surface, no moved route; `/v1/me` holds the rest: `GET/PATCH /v1/me`
  (names — Anto, 2026-10-20: the user may change their own — mobile, direct dial,
  `correspondence_language` (null = the Betrieb's default — the **home** Betrieb's, as `GET /v1/me`
  names it; which Betrieb's default a sister-Betrieb document uses is open until a document reads
  the field; never the UI language, which stays the `ui` preference) and `signature_block`; If-Match; the own row locked first; one
  `profile_updated` audit row; any other field, the e-mail address included, is a 422) and
  `PUT/GET/DELETE /v1/me/signature` (the manager's rules and key). A session that owes a factor
  reaches none of them (the guard's enrolment paths), and the SPA shows it the Security tab alone.
  Notification preferences are a placeholder until KAN-216.
- **Signature image (KAN-200; Anto, 2026-10-19: the manager sets it too):** `PUT/GET/DELETE
  …/users/{id}/signature`, the image as the raw body (PNG or JPEG ≤ 1 MiB, judged by its
  bytes), stored under `dealerships/<id>/users/<id>/signature` in the object store
  (`app/core/object_store.py`; `DMS_OBJECT_STORE_BACKEND=local` under
  `DMS_OBJECT_STORE_LOCAL_DIR`, the only store built; a bucket is a follow-up); the row holds
  the key (`signature_image_ref`), never bytes or base64. The local store is dev, test and
  staging only: `get_object_store` refuses it in production at startup (KAN-402 brings the
  bucket), as the mailer refuses its `logging` sender.

## Document templates (ADR-051)

One shared template layer (`app/platform/models/document_template.py`). WeasyPrint only in
`app/platform/services/document_render.py`; no HTML-tag string literal (`<table`, `<div`,
`<style`, `<html`) elsewhere in `app/` (`test_no_layout_code_outside_document_render.py`). One template, two consumers: the ui-kit
`DocumentPreview` only displays the PDF the renderer produced — never a second renderer.

## Authentication (D-A-07…D-A-12, D-A-16, D-A-36, D-A-37) — built by KAN-196, except where a bullet says so

- **Nexotec owns authentication (D-A-07); Zitadel is gone.** `POST /v1/auth/login` (e-mail +
  argon2id password, `app/platform/services/identity.py::LocalPasswordProvider`) answers the
  session cookie or an MFA step; `/auth/mfa/verify` (TOTP or recovery code), `/auth/mfa/passkey/
  options` + `/verify`; `/auth/password-reset/request` (202 whether or not the address exists) +
  `/confirm`; `/auth/invitations/accept`. The session layer stayed (`create_access_token`, the
  `dms_session` cookie, `Principal`, `get_current_principal`, switch-dealership) and gained the
  claims `sid`, `amr`, `mfa`; `exp` is the session's absolute end. **Every refusal is the same
  401 body with the same argon2 work behind it** (a dummy verify when the address names no
  account) — `tests/test_identity_login_policy.py` asserts body and timing; never add a
  reason-specific message. Per-account backoff from the third failure and soft lockout after
  `auth_lockout_failures` (cleared by a reset); per-IP backoff on the **last** X-Forwarded-For
  entry (the proxy's), never the first — wrong second-factor answers count on the account axis
  and per challenge, not on the IP axis. The address is resolved case-insensitively and stored
  lower-cased (`ck_user_email_lower`; the migration lower-cases existing rows and aborts with a
  `KAN-196 REPORT` on two spellings of one mailbox). `credential` holds the hash (+ the last five) and
  **nothing else in the schema may grow a password-shaped column**
  (`test_no_password_credential_storage.py`, inverted as D-A-07 ruled). Nobody sets another
  person's password (FR-AU-10): only an invitation or a reset token does, single-use, stored as
  SHA-256 (`auth_tokens.py`); the seed script is the one bootstrap exception.
- **Password policy** (`password_policy.py`): ≥ 12 characters, no composition rules, the
  dealership's names, the user's names and the e-mail local part refused, Have I Been Pwned
  range check at set-time that **fails open and flags** (`credential.breach_check_pending`,
  `breach_check_unavailable` event). The test suite turns HIBP off (autouse fixture) and
  patches `password_policy.hibp_breach_count` where it tests it.
- **Sessions** (`auth_sessions.py`, `app/platform/session_guard.py`): one `session` row per
  login; `enforce_live_session` is an **app-level dependency** in `app/main.py` that 401s a
  token whose row is revoked, idle (`session_idle_minutes`, 120) or past its absolute end
  (**next midnight Europe/Zurich** — Anto, 2026-10-17), and touches the row at most once a
  minute. A token without `sid` (tests, bootstrap) is left to the route. Logout revokes the
  row and is the one route the guard lets a dead session reach. A status change to suspended
  or deactivated, a change of roles, manager flag or membership (the claims live for the
  session's day), a password reset, an accepted invitation and platform staff's
  `revoke-sessions` recovery (D-A-16, KAN-202; a manager's lever is to suspend or deactivate the
  user) revoke every row of the user — each revocation is a
  `session_revoked` event; the reset request's mail is sent after the response (a background
  task) so its timing says nothing; the `logging` mail sender is refused at startup in production. Per-Betrieb idle/absolute policy (D-A-37) is **not built**: one
  platform setting.
- **MFA** (`mfa.py`): TOTP (`pyotp`, a code is good once, ±1 step) with ten hashed single-use
  recovery codes, passkeys (`webauthn`, resident keys; the challenge is derived from a
  single-use `auth_token` via `challenge_for`, never stored). `dealership_auth_config.mfa_enforced`
  (default **on**, the manager's `PUT /v1/dealerships/{id}/auth-config`, audit `auth_config_set`)
  decides for a Betrieb; **platform staff are always enforced** (D-A-36): `LoginResponse.
  mfaEnrolmentRequired` sends the SPA to `/account/security` before any other screen, and
  `require_mfa_session()` in `app/core/auth.py` refuses a staff principal whose session carries
  no factor — on `GET …/login-status`, `POST …/users/{id}/invitations`, the auth-config PUT and a probe route; the recovery door has its own gate (below); the impersonation
  endpoints will carry it. **And at the API as a whole:** the session guard confines a session
  that still owes a factor (every staff session without one; a dealer session in an enforcing
  Betrieb) to `/v1/auth/*`, `/v1/me/preferences` and the Betrieb's **own** `auth-config` switch
  (a dealer manager's lever — never a staff session, never another Betrieb's; the PUT itself
  carries `require_mfa_session`) — everything else under `/v1/` is 403 with
  `details.mfaEnrolmentRequired`; nothing outside the API is judged. The guard reads the factor
  from the **session row**: confirming a factor (TOTP confirm, passkey registration) records it
  on the row's `amr`, so the session that enrolled is free at once; the token's own `mfa` claim
  stays as minted until the next sign-in or dealership switch, which re-mint it from the row (the
  account routes are idempotent routes and cannot re-issue the cookie). A user removing their own
  never-confirmed enrolment changes no session and writes no `mfa_removed` (staff's reset below
  always writes it). The MFA step allows five answers per challenge (a guarded counter on the
  `auth_token` row); **every wrong answer counts on the account axis** like a wrong password, the
  password step does not clear that counter (only a completed login does) and the MFA step
  honours the account lock and backoff — so starting fresh challenges buys nothing. A challenge
  is spent on success only, where it is spent. A session that never proved a factor ends (401)
  once its user holds one (enrolled elsewhere; recorded as `session_revoked`); removing the last
  factor strips the session that removed it and ends the user's other sessions; platform staff's
  MFA reset ends the user's sessions. A lock reached through wrong MFA answers is a `locked_out`
  event like one reached through wrong passwords. Platform staff's `reset-mfa` recovery (KAN-202) removes every factor and always
  writes `mfa_removed` naming the actor (FR-AU-12). **Passkey registration and assertion are verified by the library** (`webauthn` 2.7.1, the
  version the `cryptography<46` pin installs; the shape checks it lacks sit in `mfa.py`, and the **key policy** refuses at registration a key
  whose type does not match its algorithm family, an RSA key under 2048 bits (SP 800-131A), with an
  even modulus, past 16384 bits or with an exponent past 64 bits, an Ed25519 encoding that is not a
  canonical point on the curve (RFC 8032 §5.1.3), and a credential id past 1023 bytes (the spec's
  limit) — partly keys no signature could ever verify, partly Nexotec's own policy; the comments in
  `mfa.py` say which for each check);
  the suite completes a **software** ceremony — real keys and signatures through the library —
  and none against a hardware authenticator or a browser.
- **Platform mail** (`mailer.py`, D-A-28; Anto, 2026-10-17): invitations and resets leave
  through the Nexotec mailbox behind `PlatformMailer` — `logging` (default: the link goes to the
  application log; **staging runs this today**) or `smtp` (`DMS_PLATFORM_MAIL_*`). Delivery is
  not fire-and-forget: a failure is a `mail_delivery_failed` auth event and an ERROR line. Texts
  in DE/FR/IT/EN from the user's `uiLanguage` preference (`mail_texts.py`). Tests override
  `get_mailer` with the `mailbox` fixture.
- **Auth events** (`auth_event`, FR-AU-21): every login outcome, lock, token, factor and
  session change, readable by the manager at `GET /v1/dealerships/{id}/auth-events`
  (`audit_logs` read). Never a password, hash or token in `details`.
- **D-A-08 / D-A-11:** `IdentityProvider` is the seam; `LocalPasswordProvider` is the only
  implementation; `provider_for` reads `dealership_auth_config.auth_method` (only `local`).
  `EntraOidcProvider` is **not built** ([P3]). `User.auth_identity_id` stays unique and non-null:
  a local user carries `local:<user id>`.
- **Identity realms (D-A-13, D-A-14):** staff (this), terminal (ADR-028,
  `WORKSHOP_TERMINAL_AUTH_DESIGN.md`), customer (reserved, `aud=portal`, never a row in
  `user`), machine (not in v1). Never share a credential store — `_CREDENTIAL_TABLES` in the
  architecture test names the two allowed (`credential`, `terminal_device`).
- **Connected accounts are grants, not identities (FR-AU-23):** `user_connected_account` is
  never used to sign in and never appears in the token. **Built (KAN-201):** the table
  (provider, external account id and label, scopes, `secret_ref` — a secrets-manager reference,
  never a token, never returned — status, linked/last-used/revoked stamps, a closed revoke reason),
  written and read only by `app/platform/services/connected_account.py` (nightly reconciliation
  reads its actor ids); `tests/architecture/test_connected_account_is_never_an_identity.py`
  keeps the identity and session layer from naming it. The user lists and revokes their own
  (`GET/DELETE /v1/me/connected-accounts`), the manager a user's (`GET/DELETE /v1/dealerships/{id}/users/{user_id}/connected-accounts`,
  capability `connected_accounts`; the manager's read is provider, scopes and status only —
  never the address or the usage stamps), deactivation revokes every one (FR-AU-24). A revoke keeps
  the row, records `connected_account_revoked` and an audit row; **after the commit** the caller
  runs the provider's registered revoker (`end_at_providers`; a provider failure is logged, never
  undoes the revoke or the deactivation) — **none is registered**: linking ships with the Outlook
  integration, so no row exists yet and a revoke ends the grant in Nexotec only, keeping the
  reference so the secret can still be removed. **Open (KAN-412):** the seam is not in
  `platform.public` (the revoker gets the ORM row) and a provider's refusal is not retried or
  reconciled — both before the Outlook integration registers the first revoker.
- **D-A-16** — a `platform_admin` recovers accounts alone (re-issue invitation, reset MFA,
  clear lockout, revoke sessions, grant manager flag). **Built by KAN-202, one door (Anto,
  2026-10-22):** `POST /v1/platform/users/{id}/recovery/{reissue-invitation|reset-mfa|clear-lockout|
  revoke-sessions}`, `POST /v1/platform/dealerships/{id}/recovery/grant-manager` (`userId`) and
  `POST /v1/platform/users/{id}/recovery/change-email` (`email`; Anto, 2026-10-22: the staff
  change of an accepted account's sign-in address is recovery too), each behind
  `require_staff_recovery_session` in `app/platform/api/recovery.py` — `platform_admin`, a
  second factor in the session, never an impersonation (`Principal.acting_for`, D-A-24's
  support-token shape, the one field Anto let into `app/core/auth.py` on 2026-10-22; nothing
  sets it until impersonation is built; FR-AU-27) — with a **mandatory reason** (422
  without; ≤500 characters), its own auth event kind (`recovery_*`, the staff member the actor,
  the reason in `details` — **the Betrieb's manager reads it** in `GET …/auth-events`, Anto
  2026-10-22), an audit row `recovery_<action>` (the reason in the audit row's `reason`), and
  a `security` notice through `services/security_notifications.py` — a seam KAN-216 fills;
  until then it logs that a notice is owed and **nothing reaches the user** (a re-issued
  invitation still goes by e-mail, as the flow it is). Staff never recover their own account
  (403) nor a deactivated one (409); clear-lockout on an account with no failures is a 409
  (`not_locked`); every refusal carries a stable `details.rule`. Grant-manager and change-email
  write the user row, so they take **If-Match** on its version (the search returns it).
  Grant-manager takes an `active` home user of that Betrieb at **any time** (Anto, 2026-10-22 —
  not only when the last manager has left; FR-AU-25 updated), 409 `already_manager` /
  `not_active`, and goes through the membership service (same audit rows, sessions end).
  Change-email runs KAN-196's consequences (open invitation and reset tokens end, every session
  ends, `email_changed`, the old address is mailed) beside `recovery_email_changed`; 409
  `same_email` / `email_taken`. `GET /v1/platform/users?email=&dealershipId=` finds the account
  (exact address, or by Betrieb; ≤200, `truncated` when more matched) with its lock, factor and
  open sessions — never a hash (the credential's counter and lock are read by column). The staff-only
  `DELETE …/users/{id}/mfa` and `…/sessions` are **gone**; `POST …/users/{id}/invitations` is
  the onboarding re-send to a still-`invited` user only — anyone, staff included, re-inviting
  an accepted account gets 403 with `details.recovery`. Nothing in recovery sets or reads a
  password (`tests/architecture/test_recovery_never_touches_a_password.py`). The page is
  Administration › Account recovery (`/administration/account-recovery`), never role-hidden.
  A manager reads `GET …/login-status` (FR-AU-10) and never touches a staff account (KAN-97);
  the manager flag is otherwise KAN-199's. **The e-mail address is the sign-in and
  recovery channel:** a manager corrects it through the PATCH only while the user is still
  `invited` (the change ends every open invitation and reset token and every session, records
  `email_changed` and mails the old address — in the PATCH's own transaction,
  `update_user(on_email_change=…)`; a taken address is a 409 first); for an accepted account the
  PATCH refuses everyone, staff included (403, `details.recovery: change-email`) — the change is
  the recovery door's `change-email` above. An account never returns to `invited` through a PATCH (409), so the
  onboarding path cannot be re-opened on an active user — the one way back is KAN-200's
  reactivation of a deactivated user who holds no credential, which lands on `invited` — also
  for an account that was active before KAN-196 and never set a password: the manager may then
  correct its e-mail and resend the invitation (Anto, 2026-10-20: kept as built, knowingly). **Open with Anto:** the new address is
  not confirmed before it takes over the channel. **Not built:** FR-AU-26's `security`
  notification family itself (KAN-216; the e-mail-change notice is the one notice sent). No step-up
  re-authentication (A-12: no).
- argon2id parameters are settings (`DMS_ARGON2_*`, default t=3 / 64 MiB / p=4, 48–80 ms a
  verify on the build machine, `scripts/benchmark_argon2.py`); a stored hash below them is rehashed on
  the next login. The Render instance is re-tuned in a follow-up ticket.

## Open questions — ask Anto, never decide in code

A-6 and A-7 (legal review of financing wording and seeded texts) — legal, not build. Every
other question on both pages is answered; if a session finds a gap, it files a ticket.
