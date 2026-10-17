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
- **The UI word for the tenant is "Betrieb"** (D-A-10): service-only garages are customers from
  day one, so `sales` becomes a module entitlement like `aftersales` (target; no entitlement
  exists yet, KAN-198), and a Betrieb without it is a normal configuration. `dealership` stays
  the table and code name.
- Tenant scope comes from the token. Group-scoped reads exist only in the files that
  `tests/architecture/test_no_ambient_group_read.py` allow-lists — `core/tenancy.py`
  (`get_group_read_or_404`), `customer/services/customer.py`, `customer/services/legal_basis.py`,
  `inventory/services/group_listing.py`, `platform/services/dealership.py`. The test catches
  `group_id` / `dealer_group_id` in `==`, `.in_(`, `filter_by(…=)` and single-line `text(…)`
  (a multi-line `text()` escapes it). A new group read is an ADR plus an allowlist entry.
  Cross-tenant reads return 404, never 403.
- **No group-level administrator** (D-A-01). The manager flag is held per dealership (KAN-98):
  `User.is_dealer_manager` for the home dealership, `dealership_membership.is_dealer_manager`
  per sister dealership (default false, never copied from the home flag).
  `POST /v1/auth/switch-dealership` mints the target's flag via `user_service.is_dealer_manager_in`.
  The last-active-manager rule (FR-A-13, `_assert_not_last_manager`) and the manager e-mail list
  count managers by membership too. No endpoint grants a membership or its flag yet (KAN-199).
  `LoginResponse.user.isDealerManager` is the home flag, not the active dealership's — never
  gate a screen on it; the session's own flag is the token claim.

## Access — what exists today

`User.access_roles` (a list) plus `is_dealer_manager`. `platform_admin` is Nexotec staff only;
it is an ordinary `AccessRole` value, so `app/platform/services/user.py` (`create_user` /
`update_user`, required `actor_roles`) guards it (KAN-97): only a `platform_admin` principal may
grant or remove it, or edit a user who holds it — anyone else gets 403. **Not yet true:** a
`platform_admin` can still be homed in a dealership; platform staff have no storage of their
own until the Platform users panel exists (KAN-199, not built, is to reject such memberships
with 422).

Permissions are capability-based: `Capability(read_roles, write_roles, manager_can_write=True)`
in `app/core/permissions.py`; `require_read` / `require_write` check platform_admin, then the
roles, then the manager flag (on writes only if `manager_can_write`). `read_roles=None` = any
role; an empty set = manager only. An empty `write_roles` = platform staff or the manager —
except `audit_logs`, which the manager cannot write.

**Session minting (KAN-141).** Today every session token is minted through `_mint_session` in
`app/platform/api/auth.py` — the OIDC callback and switch-dealership. It refuses a suspended
or deactivated user (401) and authorises the target dealership from the home dealership plus
`dealership_membership` rows, never the token's `memberships` claim (403). Auth & Identity
risk 4 puts such checks where every minting path passes, so KAN-196's `POST /v1/auth/login`
(not built) must mint through it. **Open, not settled here (KAN-254):** the ADR-028 terminal
path (`WORKSHOP_TERMINAL_AUTH_DESIGN.md`) is designed to call `create_access_token` directly
with fixed `{TECHNICIAN}` roles, which `_mint_session` cannot mint — how it gets the status
check needs an ADR. Not yet true: `get_current_principal` does not check `status`, so an
issued token works until it expires (FR-A-10; KAN-196's sessions step).

## Dealer Administration — decided, NOT built (check the code before relying on this list)

Rulings D-A-01…D-A-41 live in the PRD; the ones a session is most likely to get wrong:

- **One administration surface (A-RULE-0).** Modules read configuration, never own it. The
  same rule applies to mail (A-RULE-9) and customer messaging (A-RULE-10): no module composes
  a sender or calls a messaging provider; it registers a document type with the platform.
- **Access = entitlement × role (D-A-04).** `module_entitlement` per dealership, written by
  `platform_admin`, checked **before** the role check, a JWT claim. Unlicensed = 404 for all.
  No such table, claim or check exists today (KAN-198).
- **Permissions are platform-defined (D-A-03). Policy limits are NOT in v1 (D-A-18)** — no
  `dealer_policy_limit`, no screen, no matrix row.
- **Copy configuration from a sister dealership** (D-A-01, FR-A-01): a one-shot, audit-logged
  copy — never inheritance.
- **Onboarding (D-A-06):** platform staff enter group, legal entity (UID uniqueness checked),
  entitlements and one manager; **Nexotec sets nothing commercial** — no VAT rate, no bank details.
- **Standard texts are delegable per category** (`standard_text_delegation`, FR-A-28); the seed
  library and the UI strings (`ui_translation`, served at runtime) are edited by
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
- **Deactivation includes a reassignment step** (D-A-35): each module registers an ownership
  provider; open records are transferred or listed, never left silently with the user.
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

## Document templates (ADR-051)

One shared template layer (`app/platform/models/document_template.py`). WeasyPrint only in
`app/platform/services/document_render.py`; no HTML-tag string literal (`<table`, `<div`,
`<style`, `<html`) elsewhere in `app/` (`test_no_layout_code_outside_document_render.py`). One template, two consumers: the ui-kit
`DocumentPreview` only displays the PDF the renderer produced — never a second renderer.

## Authentication (D-A-07…D-A-12, D-A-16, D-A-36, D-A-37) — decided, NOT built

- **Zitadel is out; Nexotec owns authentication (D-A-07).** The code still runs WP-4's front
  door (`zitadel_*` and `session_secret_key` in `app/core/config.py`, `/v1/auth/oidc/*`,
  `SessionMiddleware` in `app/main.py`, live `userinfo` in `app/platform/services/oidc.py`).
  Do not extend it. **WP-4 is reverted, then upgraded** (D-A-12): `credential` returns, bcrypt is
  replaced by **argon2id** in the same package, `POST /v1/auth/login`, lockout.
  `test_no_password_credential_storage.py` (today: no `credential` table, no password-shaped
  column anywhere) is **inverted** (password columns in `credential` and nowhere else), never
  deleted. The session layer (`create_access_token` with claims `sub, tenant_id, group_id,
  memberships, active_location_id, roles, is_dealer_manager`, the `dms_session` cookie,
  `Principal`, `get_current_principal`, switch-dealership) stays as it is. None of the
  replacement exists yet: no argon2 / pyotp / webauthn dependency, no `IdentityProvider`,
  no `dealership_auth_config` (KAN-196).
- **D-A-08 / D-A-11** (target, none of it built) — one `IdentityProvider` interface;
  `LocalPasswordProvider` is the live one; `EntraOidcProvider` is **built and
  integration-tested but enabled for no dealership**. Federation
  is per dealership, never per user; `User.auth_identity_id` (unique, exists today) is the join.
- **Identity realms (D-A-13, D-A-14):** staff (this), terminal (ADR-028,
  `WORKSHOP_TERMINAL_AUTH_DESIGN.md`), customer (reserved, `aud=portal`, never a row in
  `user`), machine (not in v1). Never share a credential store.
- **Connected accounts are grants, not identities (FR-AU-23):** `user_connected_account` is
  never used to sign in and never appears in the token.
- **D-A-09 / D-A-36** — MFA optional per dealership, default on; TOTP and passkeys both in v1;
  **mandatory for every platform staff account**. No step-up re-authentication (A-12: no).
- **D-A-16** — a `platform_admin` recovers accounts alone (re-issue invitation, reset MFA,
  clear lockout, revoke sessions, grant manager flag); nobody ever sets a password (FR-AU-10).
- **D-A-37** — per-Betrieb session idle/absolute policy within a platform floor and ceiling.

## Open questions — ask Anto, never decide in code

A-6 and A-7 (legal review of financing wording and seeded texts) — legal, not build. Every
other question on both pages is answered; if a session finds a gap, it files a ticket.
