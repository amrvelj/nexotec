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
  - "frontend/apps/dms/src/**/*ntegration*"
  - "frontend/apps/dms/src/**/*ogin*"
---
<!-- Maintainer note (stripped before Claude sees it). Summarises ADR-014, ADR-051, the Dealer
Administration PRD and Authentication & Identity (rulings D-A-01…D-A-09, 2026-09-20; they
absorb Roles & Permissions v0.2 and supersede ADR-027). Verified against main@568f416 on
2026-09-28 (every present-tense claim checked against the code). "Not built" lines are
re-checked weekly by /drift-audit. -->

# Platform: organisation, access, administration, authentication

## Organisation (ADR-014)

- `dealer_group` gives visibility and owns nothing → `dealership` is **the tenant**, a legal
  entity with its own stock, sales, accounting and VAT → `location` is a physical site: an
  attribute, **never a scoping boundary** (and owned by platform, not inventory).
- Tenant scope comes from the token. Group-scoped reads exist only in the files that
  `tests/architecture/test_no_ambient_group_read.py` allow-lists — `core/tenancy.py`
  (`get_group_read_or_404`), `customer/services/customer.py`, `customer/services/legal_basis.py`,
  `inventory/services/group_listing.py`, `platform/services/dealership.py`. The test covers
  `group_id` and `dealer_group_id` in every spelling. A new group read is an ADR plus an
  allowlist entry. Cross-tenant reads return 404, never 403.
- **No group-level administrator in v1** (D-A-01). The manager flag is held per dealership
  (KAN-98): `User.is_dealer_manager` for the home dealership, `dealership_membership.
  is_dealer_manager` for each sister dealership (default false, never copied from the home
  flag). `POST /v1/auth/switch-dealership` mints the target's flag via
  `user_service.is_dealer_manager_in`. The last-active-manager rule (FR-A-13) and the manager
  e-mail list count managers by membership too. No endpoint grants a membership or its flag
  yet.

## Access — what exists today

`User.access_roles` (a list) plus the `is_dealer_manager` flag; `platform_admin` is Nexotec
staff only, never a dealer flag. Permissions are capability-based: `Capability(read_roles,
write_roles)` in `app/core/permissions.py`; `require_read` / `require_write` check
platform_admin, then the roles, then the manager flag. An empty `write_roles` means platform
staff or the dealer manager only.

## Dealer Administration (D-A-01 … D-A-06) — decided, NOT built

None of the following exists in the code; do not code against it, and do not build it outside
a ticket for it.

- **One administration surface (A-RULE-0):** every setting a dealership owns is configured in
  Dealer Administration; modules read configuration, never own it.
- **Access = entitlement × role (D-A-04):** a `module_entitlement` per dealership, written by
  `platform_admin`, checked **before** the role check and carried as a JWT claim; an unlicensed
  module 404s for everyone, the manager included. Roles become one primary role (a
  platform-defined preset) + module toggles per user.
- **Permissions are platform-defined; limits are dealer-defined (D-A-03):** `dealer_policy_limit`
  rows (max discount without approval, trade-in overallowance, cash limit, who may release a
  document) change what a user may do *in amount*, never *in kind*.
- **Copy configuration from a sister dealership** (D-A-01): a one-shot, audit-logged copy —
  never inheritance.
- **Support access (D-A-05, supersedes ADR-027):** a Nexotec employee steps into the user's
  session; the token carries `sub` = staff and `actingFor` = the dealer user, and the audit
  shows both identities, never the dealer as the actor. 60-minute box, mandatory reason, a
  persistent banner, the manager notified, and either can end it. It can never touch
  credentials, the manager flag or integration secrets.
- **Onboarding (D-A-06):** the platform admin enters group, legal entity (UID uniqueness
  checked), module entitlements and one manager; an atomic wizard seeds a location, the
  four-language text library, number ranges and reference data. **Nexotec sets nothing
  commercial** — no VAT rate, no bank details.

## Document templates (ADR-051)

One shared template layer (`app/platform/models/document_template.py`). WeasyPrint is imported
only in `app/platform/services/document_render.py`, and no HTML-tag string literal (`<table`,
`<div`, `<style`, `<html`) appears anywhere else in `app/`
(`test_no_layout_code_outside_document_render.py`).
One template definition, two consumers — the PDF and the ui-kit `DocumentPreview` — never two
renderers.

## Authentication (D-A-07 … D-A-09) — decided, NOT built

- **Zitadel is out; Nexotec owns authentication (D-A-07).** The code still runs WP-4's Zitadel
  front door (`zitadel_*` settings in `app/core/config.py`, `/v1/auth/oidc/*`, Starlette
  `SessionMiddleware` in `app/main.py`, the live `userinfo` call). Do not extend Zitadel code.
  The replacement: a `credential` table with **argon2id** (never bcrypt), `POST /v1/auth/login`,
  lockout. Today `test_no_password_credential_storage.py` asserts no `credential` table and no
  password-shaped column anywhere; D-A-07 **inverts** it (password columns in `credential` and
  nowhere else) — never deletes it. The session layer (`create_access_token`, the cookie,
  `Principal`, `get_current_principal`, claims, switch-dealership) stays as it is.
- **D-A-08** — one `IdentityProvider` interface; `LocalPasswordProvider` is the only v1
  implementation. Federation, when it comes, is per dealership, never per user;
  `auth_identity_id` is the join.
- **D-A-09** — MFA is optional, configured per dealership; new dealerships default to
  enforcement on.

## Open questions — ask Anto, never decide in code

Service-only garages (A-15, blocking) · un-encrypting the public UID in `tax_id` (A-3) ·
replacing the US artefacts `dealer_license_number` / `license_state` (A-4) · step-up
re-authentication for dangerous actions (A-12) · number ranges per site or company-wide (A-26).
