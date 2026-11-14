---
paths:
  - "app/platform/api/dealership*.py"
  - "app/platform/api/branding.py"
  - "app/platform/api/locations.py"
  - "app/platform/reference/api/reference_data.py"
  - "app/platform/services/dealership*.py"
  - "app/platform/services/entitlement.py"
  - "app/platform/services/bank_account.py"
  - "app/platform/services/branding.py"
  - "app/platform/services/brand_colors.py"
  - "app/platform/services/logo_validation.py"
  - "app/platform/services/location.py"
  - "app/platform/services/holidays.py"
  - "app/platform/reference/services/reference_data.py"
  - "app/platform/models/dealership*.py"
  - "app/platform/models/module_entitlement.py"
  - "app/platform/models/branding_asset.py"
  - "app/platform/models/location_schedule.py"
  - "app/platform/models/holiday_calendar.py"
  - "app/platform/reference/models/reference_data.py"
  - "app/platform/reference_data_seed/**"
  - "app/platform/schemas/bank_account.py"
  - "app/platform/schemas/branding.py"
  - "app/platform/schemas/dealership*.py"
  - "app/platform/schemas/entitlement.py"
  - "app/platform/schemas/location.py"
  - "app/platform/reference/schemas/reference_data.py"
  - "tests/test_dealership.py"
  - "tests/dealership_support.py"
  - "tests/test_entitlements.py"
  - "tests/test_branding.py"
  - "tests/test_kan197_dealership_migration.py"
  - "tests/test_kan203_business_master_data.py"
  - "tests/test_kan204_locations*.py"
  - "tests/test_platform_location_last_active_race.py"
  - "tests/architecture/test_sales_routers_carry_entitlement_gate.py"
  - "scripts/migrate_logo_urls_to_branding_assets.py"
  - "frontend/apps/dms/src/pages/BusinessPage*"
  - "frontend/apps/dms/src/pages/BrandingPage*"
  - "frontend/apps/dms/src/pages/Location*"
  - "frontend/apps/dms/src/components/business/**"
  - "frontend/apps/dms/src/components/branding/**"
  - "frontend/apps/dms/src/components/locations/**"
  - "frontend/apps/dms/src/**/RequireEntitlement*"
  - "frontend/apps/dms/src/layout/navEntitlements.ts"
  - "frontend/apps/dms/src/**/ReferenceDataPage*"
  - "frontend/apps/dms/src/utils/googlePlaces.ts"
  - "frontend/apps/dms/src/components/AddressSearchField.tsx"
---
<!-- Maintainer note (stripped before Claude sees it). Summarises the Dealer Administration PRD v1.2
(D-A-01…D-A-41, A-RULE-0…10) and ADR-100, ADR-101. Cut from platform.md by KAN-614 (2026-11-10) to
the invariants; platform.md was verified against main@73baf3f on 2026-10-25 - this cut was not
re-verified against the code. -->

# Dealer Administration (PRD v1.2; rulings D-A-01…D-A-41)

Decided rulings live in the PRD; built only where a bullet says so — check the code.

## The rulings a session most often gets wrong
- **One administration surface (A-RULE-0):** every setting a Betrieb owns is configured here; a
  module reads configuration, never owns it. The same for mail (A-RULE-9) and customer messaging
  (A-RULE-10): no module composes a sender or calls a messaging provider; it registers a document
  type with the platform.
- **Access = entitlement × role (D-A-04) — built.** `module_entitlement` per Betrieb (`sales`,
  `aftersales`, `parts`, `finance`, `reporting`), written by `platform_admin` only, read by the
  manager; claims `entitlements` + `ent_v`. `require_entitlement(ModuleCode.X)` is a
  **router-level** dependency on every sales, inventory, valuation and configurator router, runs
  before any role check and answers **404 for everyone**, staff included
  (`test_sales_routers_carry_entitlement_gate.py`). A changed set bumps
  `dealership.entitlements_version`; a token with another `ent_v` gets 401 and signs in again.
  Sales-licensed data inside another module's response is gated per field with
  `holds_entitlement(db, principal, ModuleCode.X)` — never per route.
- **Permissions are platform-defined (D-A-03); no policy limits in v1 (D-A-18).**
- **Copy configuration from a sister Betrieb** (D-A-01, FR-A-01): a one-shot, audit-logged copy —
  never inheritance.
- **Onboarding (D-A-06) — built (KAN-210):** staff enter group, legal entity (UID unique), entitlements and one
  manager; **Nexotec sets nothing commercial** — no VAT rate, no bank details.
  `POST /v1/platform/onboarding` (`services/onboarding.py`; staff with a factor) runs the create services with
  `commit=False` in one transaction — group, `create_dealership` (seat location, number ranges, settings),
  entitlements, the first manager invited, one `platform.dealership.onboarded` row — 409 `uid_already_onboarded`;
  texts, template and MFA-on come from the platform defaults. Screen: `/platform/onboarding` (five steps).
  `POST /v1/dealerships` is deprecated in OpenAPI.
- **Activation gate (D-A-21):** `pending_onboarding` until every applicable checklist item is done
  and the manager accepted the current ToS and DPA (D-A-34); until then only Dealer Administration
  and My account. Not enforced yet, and no route activates: the procedure is KAN-214.
- **`status` is staff's, `active` ↔ `suspended` only** (KAN-712, ADR-165): a manager naming it is
  422 `status_not_patchable`; activation and offboarding are procedures (`use_*_procedure`).
- **Mail (D-A-28):** platform mail is always Nexotec's sender (`platform-identity.md`);
  customer-facing mail resolves per document type through `sending_policy` to `user_mailbox` ·
  `business_mailbox` · `verified_domain` · `nexotec_on_behalf`.
- **Customer messaging (D-A-41):** one platform send dialog for e-mail, SMS, WhatsApp; the Betrieb
  brings its own SMS/WhatsApp provider; consent lives on the customer.
- **Not in v1, by ruling:** policy limits (D-A-18), external users (D-A-20), custom fields and tags
  (D-A-29; one exception, contact tags — Anto, 2026-10-26), Saldosteuersatz (D-A-27), customer logins (D-A-13), API clients (D-A-14), Zefix
  auto-fill, AI translations, training mode.

## Data export and offboarding (KAN-218)
- The manager's export is a job (`export_job`; the worker writes the ZIP), downloadable 7 days via
  the authenticated GET (signed URL: KAN-402).
- Staff offboard (`POST /v1/platform/dealerships/{id}/offboard`) only after an acknowledged export
  newer than the last write; the Betrieb's principals then get 404 (`session_guard.py`).
  Archive exports: staff only, with a reason. It then leaves every group read
  (`group_read_dealerships.py`, `group_labels.py`; Q2 A).
- `platform.dealership.offboarded` (Q3 A): Stock withdraws live listings (empty feed first,
  `listing_withdrawal.py`), stating `inventory.dealership_listings.withdrawn`; Integration
  disables and revokes every connection, the `marketplace_publish` ones only on it.
- **No automatic deletion** (Anto, 2026-11-14): past `retention_until` (offboarding + 10 years)
  reconciliation reports it nightly (`dealership.archive_due_for_manual_deletion`); staff delete it.

## Contact groups (KAN-466) — built
- Nexotec's list `contact_group` (reference data, staff-maintained); each Betrieb switches groups on at
  `/administration/contact-groups` (`dealership_contact_group`, a row = on, none by default; row
  `dealership_settings`); copied from a sister by the `contact_groups` subset (switches on, never off).
  A group staff withdrew cannot be switched on. Guard: `test_contact_group_setting_is_administrations.py`.

## Business master data (D-A-22, D-A-25, D-A-27, FR-A-25) — built
- `/administration/business` is the one screen for the Betrieb's own data: legal identity,
  addresses (with the Betrieb's `email` and `website`, KAN-206, for the documents' footer), address
  entry, banking, commercial and fiscal defaults, licences and memberships.
- `dealership.uid` is plain, public, checksum-validated (`validate_swiss_uid`) and unique; required
  on create, a duplicate is 409. There is no `tax_id`, `dealer_license_number` or `license_state`.
  `vat_rate` stays on `dealership` (ADR-057); `dealership_settings` holds the fiscal defaults and
  has no `vat_method`.
- A PATCH that changes legal name, UID or VAT needs `confirmation: true` (422
  `confirmation_required`) and the `dealership_legal_identity` write; every dealership, bank-account
  and settings write is audited with before/after.
- Bank accounts (D-A-27): IBAN mod-97 on save; a QR-IBAN (IID 30000–31999) only with `qrr`; at most
  one `is_default`, chosen explicitly — **never promoted** (Anto).
- **Banking is refused with 404, not 403 (ADR-100):** a read answers 404 to everyone but manager,
  finance, auditor and staff; a write 404 to everyone but manager and staff
  (`require_banking_read` / `require_banking_write`); the SPA then shows no Banking section.
- **Address entry (ADR-101):** `dealership_settings.address_autocomplete_enabled` (on by default,
  the manager switches it off) turns Google Places Autocomplete on for every address form. The key
  (`DMS_GOOGLE_PLACES_BROWSER_KEY`) is **public configuration, not a secret** — a plain env var,
  handed to the SPA only while the switch is on and the key set; Google's restriction to our
  origins and to Places API (New) protects it (verified on staging; production's own key is still
  to come). The SPA calls Places API (New) directly (`utils/googlePlaces.ts`); the
  suggestion list is never persisted, logged or sent to our API (an architecture scan pins it);
  only the picked address is written; canton is never taken from Google. `AddressSearchField` is an
  app-level input on Mantine's Combobox, not the kit's `Picker`.

## Locations (D-A-02, FR-A-03, A-RULE-1) — built
- List + one screen per site; **no delete** — `isActive: false` deactivates, refused for the last
  active location (409 `last_active_location`, under the dealership row lock). A new Betrieb gets
  its first location from its registered seat.
- `code` is unique per Betrieb and fixed once saved; a location is referenced by id only. Location
  is never a scoping boundary: nothing filters by it.
- `letterhead_address_override` is the site's letterhead address block, printed as written when a
  document is printed for it (KAN-206); its logo is a `branding_asset` row with its `location_id`
  (Letterhead screen). `bank_account_override_id` is read only by the banking roles (ADR-100). No
  `vat_override` — open with Anto (ADR-057's one VAT figure). `calendar_ref`/`warehouse_ref` stay
  empty for Aftersales and Parts.
- Opening hours are stored **per department always** (a `perDepartment: false` PUT is written for
  sales, service and parts). **Holiday calendars are platform reference data**: SECO's «Feiertage
  Schweiz» (cantonal holidays equal to Sundays, Art. 20a ArG, plus 1 August), one calendar per
  canton and per sub-cantonal region SECO lists; a holiday is a rule computed per year
  (`services/holidays.py`).

## Branding (PRD Logo and brand assets) — built
- `/administration/branding`: one `branding_asset` row per variant (`primary`, `mono_dark`,
  `favicon`; a missing variant falls back to `primary`) holding an object-store key, never bytes; a
  replaced file gets a new key. Files are judged by their bytes (`services/logo_validation.py`): SVG
  sanitised with the standard library (no entity declaration, an element allowlist, no CSS escape
  or nested `style`), or PNG within size limits; JPEG refused.
- Every write takes If-Match on the **dealership's** version and is audited. The WCAG AA contrast
  check (`services/brand_colors.py`, mirrored by `components/branding/contrast.ts`) **warns, it does
  not block** — block vs. warn is open with Anto.
- No public or signed URL: the SPA reads the file through the authenticated GET (private,
  no-store, CSP sandbox); a signed URL comes with the bucket (KAN-402). The old `logo_url` is never
  printed (`scripts/migrate_logo_urls_to_branding_assets.py` moves it, dry run by default).

## Open questions — ask Anto, never decide in code
A-6 and A-7 (legal review of financing wording and seeded texts) are legal, not build. Every other
question on the Dealer Administration and Auth pages is answered; a gap gets a ticket.
