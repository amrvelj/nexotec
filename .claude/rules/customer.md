---
paths:
  - "app/customer/**"
  - "app/core/postal_codes.py"
  - "alembic/versions/customer/**"
  - "tests/test_customer*.py"
  - "tests/test_legal_basis.py"
  - "tests/test_*preferred_channel*.py"
  - "frontend/apps/dms/src/pages/Customer*"
  - "frontend/apps/dms/src/pages/customerScreens*"
  - "frontend/apps/dms/src/components/Customer*"
  - "frontend/apps/dms/src/components/customerRowMenu*"
  - "frontend/apps/dms/src/components/DuplicateWarningPanel.tsx"
  - "frontend/apps/dms/src/components/PhoneInput.tsx"
  - "frontend/apps/dms/src/components/customer-detail/Blocking*"
  - "frontend/apps/dms/src/components/customer-detail/blocking*"
  - "frontend/apps/dms/src/components/customer-detail/Contact*"
  - "frontend/apps/dms/src/components/customer-detail/contact*"
  - "frontend/apps/dms/src/components/customer-detail/CreditBlock*"
  - "frontend/apps/dms/src/components/customer-detail/creditBlock*"
  - "frontend/apps/dms/src/components/customer-detail/ExternalIds*"
  - "frontend/apps/dms/src/components/customer-detail/History*"
  - "frontend/apps/dms/src/components/customer-detail/history*"
  - "frontend/apps/dms/src/components/customer-detail/MergeCustomer*"
  - "frontend/apps/dms/src/components/customer-detail/Overview*"
  - "frontend/apps/dms/src/components/customer-detail/address*"
  - "frontend/apps/dms/src/components/customer-detail/commercial*"
  - "frontend/apps/dms/src/components/customer-detail/country*"
  - "frontend/apps/dms/src/customerOptions*"
  - "frontend/apps/dms/src/utils/customer.ts"
  - "frontend/apps/dms/src/hooks/useAdvisorOptions.ts"
  - "frontend/packages/ui-kit/src/components/CustomerTypeBadge.tsx"
---
<!-- Maintainer note. PRD-Customers v2.2 (its §Conformance review, D-03…D-26); ADR-014, ADR-030,
ADR-065, ADR-067, ADR-093, ADR-101. KAN-615 (2026-11-13) cut it to the invariants; vehicle parties,
trade-in holders and merges are customer-vehicle-party.md. Frontend listed by file until it has
module folders (KAN-611). Last verified against main@73baf3f (2026-10-25). -->

# Customers

## Ownership
- **Customers are owned by the dealer group** (ADR-014): one record per group, opened and
  deduplicated group-wide; a standalone dealership is a group of one — one dedupe path. The scope
  column is `group_id`, never `tenant_id`. Numbers come from a per-group sequence inside the
  caller's transaction.
- Sharing a customer between sister dealerships shares it between separate legal entities. The
  evidence record exists (`LegalBasis`, ADR-030, append-only; `enable_group_read` refuses without
  one); whether a real group may share data is the open legal question — raise it, never decide it.

## Contact channels (ADR-067) — child records, not columns
- `customer_phone`, `customer_email`, `customer_address`: one row per value, with a type, a label,
  `isPrimary` with **exactly one primary per type-group** (kept transactionally on every create,
  update and delete — re-elect when a primary goes), `validFrom`/`validTo`, `doNotUse` with a
  reason, and **consent per channel** with source and timestamp. One helper for all three,
  `_prepare_primary_change` (`services/contact_channels.py`).
- **Changing a row's type settles both type-groups** (Anto): the group it leaves re-elects; in the
  group it joins, `isPrimary: true` in the same PATCH makes it primary, otherwise the existing
  primary stays — a type change never silently demotes another row; a group with none elects it. A
  row the same PATCH closes or flags `doNotUse` never takes the flag.
- A create elects its row when the group has no **usable** primary; an existing usable primary is
  never demoted by a create that does not ask. **Reopening** a row settles its group the same way —
  the existing usable primary keeps the flag unless the PATCH says `isPrimary: true` (Anto,
  KAN-349). Usability is judged after the PATCH: `isPrimary: true` on a closed or `doNotUse` row
  never unsets the group's usable primary.
- **A customer keeps at least one usable phone or email** (FR-03): closing, flagging or deleting
  the last usable one is refused; a row already unusable is never refused.
- The six contact projections (`phoneMobile`, `phoneLandline`, `phoneWork`, `email`,
  `emailSecondary`, `address`) are **read-model projections, never stored**: the flagged primary
  among usable rows, else the oldest usable, else null; `email` personal else work;
  `emailSecondary` work when distinct. **Never add a flat column.**
- `consentScope` (marketing / invoicing / service; required on a new grant, NULL on a legacy row
  reads as marketing) and `consentSource` (form / counter / web / phone) — FR-23.

## Rulings (all closed — do not re-open)
- **D-17 — a marketing send needs three gates:** `marketingConsent` (the revDSG legal basis) ·
  per-channel `consentGranted` with `consentScope = marketing` (`channel_authorises_marketing`; no
  caller — Marketing is not built) · `newsletter`.
- **D-20** — the address is optional at creation; an address-less customer is gated at contract
  **confirmation**.
- **D-21** — `preferredChannel` ∈ `email` / `phone` / `post` / `whatsapp`; the retired `message`
  is still an enum member and accepted on write — only the UI omits it. Report such rows, never
  map them to `whatsapp`.
- **D-22** — salutation `herr` / `frau` / `firma` / `neutral`; gender is independent and **never
  inferred from the salutation**. **D-23** — `website` is not business-only.
- **D-24** — `advisorId` defaults to the acting user on create only; nullable, clearable. **The
  advisor follows Sales' rule for a responsible seller** (Anto, KAN-410): someone who acts in the
  acting Betrieb (homed or an active member) and is active or invited
  (`platform.public.get_active_user_acting_in`), anyone else 422; a hand-over refuses a colleague
  who works in no Betrieb of the customer's group (`customer/ownership.py`). Checked only when the
  advisor changes, so a customer advised by a leaver stays editable.
- **D-25** — the list offers Export and Print; **no bulk anonymise**. **D-26** — `creditBlock`,
  with `creditBlockedAt`.
- **Legal form** is one of `ag`, `gmbh`, `einzelfirma`, `verein`, `genossenschaft`, `weitere`,
  labels localised; SA, Sàrl and Sagl are the French/Italian *names* of AG and GmbH, not separate
  forms; the registered name keeps its own suffix.
- **Canton is derived server-side from the postal code (D-13)**, never accepted from the client,
  and not removable (the list filter, a grid column and the audit trail depend on it).

## Blocks and flags
- **ADR-065 — a credit block stops contract confirmation, not the offer.** **Do-not-contact** is
  the `lifecycleStatus` value `do_not_contact`, not a flag, and stops the offer and the contract. A
  credit block is visible on the customer's own screens, not only inside the offer.
- **`vatStatusConfirmed`**: `vatRegistered = false` is also the default, so a second checkbox says
  someone checked it; every row started unconfirmed; live business does not read it yet.

## History and labels
- Group-wide customer history must be served from an event-built projection keyed by `group_id`
  (reporting), never by fan-out across dealerships (rule 11). **Not built:** the History tab shows
  the audit log, its ids named on read (ADR-093) — a sister Betrieb and its users only while the
  group's read is enabled (ADR-030); an id nothing names renders as a translated marker, never a
  bare UUID.
- `advisor_label` follows a rename within a day (`customer.advisor_labels`, `events-worker.md`);
  its batches skip a customer another writer holds, which could otherwise deadlock against a merge.

## Screens
- List and detail render their actions from the one `customerRowMenu` (ADR-061). A contact card
  offers `tel:` / `mailto:` links and marks the preferred channel.
- **Address autocomplete** (ADR-101): the create wizard's address block and the detail's address
  editor carry one `AddressSearchField` while the Betrieb's switch is on and the platform holds a
  Google key. A pick fills street, house number, postal code, locality and country; every field
  stays editable, line 2 is never touched, the canton stays derived (Google's
  `administrative_area_level_1` is discarded). Suggestions are restricted to the form's country,
  never persisted or logged; the picked address is written through the same POST/PATCH as a typed
  one. Off, the forms are unchanged.
- **Merge and link refusals are said in the user's language** (`ui-i18n.md`): every one carries a
  `details.reason`, mapped in `MergeCustomerModal` and `utils/vehiclePartyRefusal.ts`; a new
  refusal adds its reason and translations.
