---
paths:
  - "app/platform/identity/api/dealership*.py"
  - "app/platform/organisation/**"
  - "app/platform/documents/api/branding.py"
  - "app/platform/reference/api/reference_data.py"
  - "app/platform/reference/services/reference_data.py"
  - "app/platform/identity/*/dealership*.py"
  - "app/platform/reference/models/reference_data.py"
  - "app/platform/reference_data_seed/**"
  - "app/platform/reference/schemas/reference_data.py"
  - "tests/platform/test_*dealership*.py"
  - "tests/dealership_support.py"
  - "tests/platform/test_entitlements.py"
  - "tests/platform/test_branding.py"
  - "tests/platform/test_kan203_business_master_data.py"
  - "tests/platform/test_*location*.py"
  - "tests/architecture/test_sales_routers_carry_entitlement_gate.py"
  - "scripts/migrate_logo_urls_to_branding_assets.py"
  - "frontend/apps/dms/src/pages/BusinessPage*"
  - "frontend/apps/dms/src/pages/BrandingPage*"
  - "frontend/apps/dms/src/pages/MailAdministrationPage*"
  - "frontend/apps/dms/src/pages/Location*"
  - "frontend/apps/dms/src/components/business/**"
  - "frontend/apps/dms/src/components/branding/**"
  - "frontend/apps/dms/src/components/locations/**"
  - "frontend/apps/dms/src/**/RequireEntitlement*"
  - "frontend/apps/dms/src/layout/navEntitlements.ts"
  - "frontend/apps/dms/src/**/ReferenceDataPage*"
  - "frontend/apps/dms/src/utils/googlePlaces.ts"
  - "frontend/apps/dms/src/components/AddressSearchField.tsx"
  - "frontend/apps/dms/src/hooks/useAddressAutocompleteConfig.ts"
  - "frontend/apps/dms/src/architecture/address-autocomplete-never-reaches-the-api.test.ts"
  - "tests/platform/test_kan37_address_autocomplete.py"
  - "frontend/apps/dms/src/pages/ContactGroupsPage*"
  - "frontend/apps/dms/src/hooks/useContactGroups.ts"
  - "tests/contact/test_contact_groups.py"
  - "tests/architecture/test_contact_group_setting_is_administrations.py"
---
<!-- Maintainer note (stripped before Claude sees it). Summarises the Dealer Administration PRD v1.2
(D-A-01…D-A-41, A-RULE-0…10) and ADR-100, ADR-101, ADR-165. Cut from platform.md by KAN-614
(2026-11-10); the lifecycle moved to platform-administration.md (KAN-751). Last verified against
main@32ad509 (2026-11-25). -->

# Dealer Administration (PRD v1.2; rulings D-A-01…D-A-41)

Decided rulings live in the PRD; built only where a bullet says so — check the code.

## The rulings a session most often gets wrong
- **One administration surface (A-RULE-0):** every setting a Betrieb owns is configured here; a
  module reads configuration, never owns it. The same for mail (A-RULE-9) and contact messaging
  (A-RULE-10): no module composes a sender or calls a messaging provider; it registers a document
  type with the platform.
- **Access = entitlement × role (D-A-04) — built.** `module_entitlement` per Betrieb (`sales`,
  `aftersales`, `parts`, `finance`, `reporting`), written by `platform_admin` only, read by the
  manager; claims `entitlements` + `ent_v`. `require_entitlement(ModuleCode.X)` is a
  **router-level** dependency on every sales, inventory, valuation and configurator router (and,
  with `AFTERSALES`, on Aftersales' own routers, KAN-810), runs
  before any role check and answers **404 for everyone**, staff included
  (`test_sales_routers_carry_entitlement_gate.py`). A changed set bumps
  `dealership.entitlements_version`; a token with another `ent_v` gets 401 and signs in again.
  Sales-licensed data inside another module's response is gated per field with
  `holds_entitlement(db, principal, ModuleCode.X)` — never per route.
- **Permissions are platform-defined (D-A-03); no policy limits in v1 (D-A-18).**
- **Onboarding, activation, the publishing profile, configuration copy, export and offboarding,
  the status page:** `platform-administration.md`.
- **`status` is staff's, `active` ↔ `suspended` only** (KAN-712, ADR-165): a manager naming it is
  422 `status_not_patchable`; activation and offboarding are procedures (`use_*_procedure`).
- **Mail (D-A-28):** platform mail is always Nexotec's sender (`platform-identity.md`);
  contact mail leaves only through the dealer's own Microsoft 365 / Google Workspace mailbox
  (Anto, 2026-11-14): the author's own, else the Betrieb's shared one, else a refusal — no
  Nexotec sender (`integration.public.resolve_sender`, KAN-215, ADR-187; Administration › Kundenmails).
- **Contact messaging (D-A-41):** one platform send dialog for e-mail, SMS, WhatsApp; the Betrieb
  brings its own SMS/WhatsApp provider; consent lives on the contact (ADR-198; the send: ADR-204,
  `integration-channels.md`).
- **Not in v1, by ruling:** policy limits (D-A-18), external users (D-A-20), custom fields and tags
  (D-A-29; one exception, the contact's free per-group tags `contact_tag`, built under KAN-50 —
  Anto, 2026-10-26; the contact groups below are Nexotec's reference list, not tags), Saldosteuersatz (D-A-27), customer logins (D-A-13), API clients (D-A-14), Zefix
  auto-fill, AI translations, training mode.

## Contact groups (KAN-466) — built
- Nexotec's list `contact_group` (reference data, staff-maintained); each Betrieb switches groups on at
  `/administration/contact-groups` (`dealership_contact_group`, a row = on, none by default; matrix row
  `dealership_settings`); copied from a sister by the `contact_groups` subset (switches on, never off).
  A group staff withdrew cannot be switched on. Guard: `test_contact_group_setting_is_administrations.py`.

## Settings store (KAN-805, ADR-214, ADR-216) — built, the screen included
Declarations, the read, history and restore, the API, the checklist, the copy, the screen: `platform-settings.md`.

## Labour rates (KAN-806, D-A-19) — backend and screen built
Categories, rates per location from a date, agreed rates, allocations, «which rate applies here»:
`platform-labour-rates.md`.

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
  canton plus one per sub-cantonal region SECO lists; Aargau and Fribourg have region calendars
  only, so a new location there starts with none and the dealer picks the region; a holiday is a
  rule computed per year (`organisation/services/holidays.py`).
- **A published change is a fact** (KAN-226, ADR-189, FR-A-35): a location write changing a published
  field states `platform.location.updated`, an hours/closure/calendar change
  `platform.location.schedule_changed`, in the write's transaction (`organisation/services/location_facts.py`);
  the Betrieb's `website` states `platform.dealership.updated` (`dealership_facts.py`, KAN-785, ADR-201;
  one payload shape for every `platform.dealership.*`, ADR-202). A location's creation publishes
  nothing. A channel reads current state through `platform.public.location_channel_facts`.

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
