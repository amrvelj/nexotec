---
paths:
  - "frontend/apps/dms/src/modules/customer/pages/Customer*"
  - "frontend/apps/dms/src/modules/customer/pages/customerScreens*"
  - "frontend/apps/dms/src/modules/customer/components/Customer*"
  - "frontend/apps/dms/src/modules/customer/components/customerRowMenu*"
  - "frontend/apps/dms/src/modules/customer/components/customer-detail/CustomerDetailContent*"
  - "frontend/apps/dms/src/modules/customer/components/customer-detail/MergeCustomer*"
  - "frontend/apps/dms/src/modules/customer/components/customer-detail/address*"
  - "frontend/apps/dms/src/components/AddressSearchField.tsx"
  - "frontend/apps/dms/src/utils/googlePlaces.ts"
  - "frontend/apps/dms/src/hooks/useAddressAutocompleteConfig.ts"
  - "frontend/apps/dms/src/architecture/address-autocomplete-never-reaches-the-api.test.ts"
  - "tests/platform/test_kan37_address_autocomplete.py"
---
<!-- Maintainer note. PRD-Customers v2.2 (FR-22 as amended by KAN-468); ADR-061 (as amended
2026-10-30), ADR-101. Split out of customer.md by KAN-751 (2026-11-18) to keep it within its
budget; the backend rules stay in customer.md, vehicle parties in customer-vehicle-party.md. Last
verified against main@3b2bf69 (2026-11-18). -->

# Customer screens

- **One row menu** (ADR-061): list and detail render their actions from `customerRowMenu`. The
  detail's primary is «Neue Offerte», its alternative «Fahrzeug verknüpfen»; without the sales
  module «Fahrzeug verknüpfen» is the primary and there is no alternative (KAN-377). **No
  «Bearbeiten»**: every field is edited inline (Anto, KAN-468). The list's row menu opens the
  record with «Öffnen». A contact card offers `tel:` / `mailto:` links and marks the preferred
  channel.
- **Address autocomplete** (ADR-101): the create wizard's address block and the detail's address
  editor carry one `AddressSearchField` while the Betrieb's switch is on and the platform holds a
  Google key. A pick fills street, house number, postal code, locality and country; every field
  stays editable, line 2 is never touched, the canton stays derived (Google's
  `administrative_area_level_1` is discarded). Suggestions are restricted to the form's country,
  never persisted or logged; the picked address is written through the same POST/PATCH as a typed
  one. Off, the forms are unchanged.
- **Refusals are said in the user's language** (`ui-i18n.md`): every one carries a
  `details.reason`. A new one adds its key to `REASON_KEYS` (`utils/apiRefusal.ts`, said through
  `apiRefusalText`) and its four translations — `apiRefusal.test.ts` and
  `tests/test_frontend_knows_backend_refusals.py` fail otherwise. The merge and link surfaces
  (`MergeCustomerModal`, `shared/partyAllocationRefusal.ts`) map their own reasons first.
