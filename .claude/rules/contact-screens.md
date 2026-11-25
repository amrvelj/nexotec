---
paths:
  - "frontend/apps/dms/src/modules/contact/pages/Contact*"
  - "frontend/apps/dms/src/modules/contact/pages/contactScreens*"
  - "frontend/apps/dms/src/modules/contact/components/Contact*"
  - "frontend/apps/dms/src/modules/contact/components/contactRowMenu*"
  - "frontend/apps/dms/src/modules/contact/components/contact-detail/ContactDetailContent*"
  - "frontend/apps/dms/src/modules/contact/components/contact-detail/MergeContact*"
  - "frontend/apps/dms/src/modules/contact/components/contact-detail/address*"
  - "frontend/apps/dms/src/modules/contact/components/contact-detail/Overview*"
  - "frontend/apps/dms/src/modules/contact/components/contact-detail/Contact*"
  - "frontend/apps/dms/src/modules/contact/components/contact-detail/contact*"
  - "frontend/apps/dms/src/components/AddressSearchField.tsx"
  - "frontend/apps/dms/src/utils/googlePlaces.ts"
  - "frontend/apps/dms/src/hooks/useAddressAutocompleteConfig.ts"
  - "frontend/apps/dms/src/architecture/address-autocomplete-never-reaches-the-api.test.ts"
  - "tests/platform/test_kan37_address_autocomplete.py"
---
<!-- Maintainer note. PRD-Contacts v2.2 (FR-22 as amended by KAN-468); ADR-061 (as amended
2026-10-30), ADR-101. Split out of contact.md by KAN-751 (2026-11-18) to keep it within its
budget; the backend rules stay in contact.md, vehicle parties in contact-vehicle-party.md. Last
verified against main@32ad509 (2026-11-25). -->

# Contact screens

- **One row menu** (ADR-061): list and detail render their actions from `contactRowMenu`. The
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
  `apiRefusalText`) and its four translations (the raising context's shard, `ui-i18n.md`) — `apiRefusal.test.ts` and
  `tests/test_frontend_knows_backend_refusals.py` fail otherwise. The merge and link surfaces
  (`MergeContactModal`, `shared/partyAllocationRefusal.ts`) map their own reasons first.
