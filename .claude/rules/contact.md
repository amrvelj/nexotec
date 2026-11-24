---
paths:
  - "app/contact/**"
  - "app/core/postal_codes.py"
  - "alembic/versions/customer/**"
  - "tests/contact/test_contact*.py"
  - "tests/contact/test_legal_basis.py"
  - "tests/contact/test_*preferred_channel*.py"
  - "tests/contact/test_contact_groups.py"
  - "tests/contact/test_consent_scope.py"
  - "tests/contact/test_history_reference_keys.py"
  - "tests/contact/test_kan722_offboarded_history_hidden.py"
  - "app/core/audit_attribution.py"
  - "frontend/apps/dms/src/modules/contact/pages/Contact*"
  - "frontend/apps/dms/src/modules/contact/components/contact-detail/ContactDetailContent*"
  - "frontend/apps/dms/src/modules/contact/pages/contactScreens*"
  - "frontend/apps/dms/src/modules/contact/components/Contact*"
  - "frontend/apps/dms/src/modules/contact/components/contactRowMenu*"
  - "frontend/apps/dms/src/modules/contact/components/DuplicateWarningPanel.tsx"
  - "frontend/apps/dms/src/components/PhoneInput.tsx"
  - "frontend/apps/dms/src/modules/contact/components/contact-detail/Blocking*"
  - "frontend/apps/dms/src/modules/contact/components/contact-detail/blocking*"
  - "frontend/apps/dms/src/modules/contact/components/contact-detail/Contact*"
  - "frontend/apps/dms/src/modules/contact/components/contact-detail/contact*"
  - "frontend/apps/dms/src/modules/contact/components/contact-detail/CreditBlock*"
  - "frontend/apps/dms/src/modules/contact/components/contact-detail/creditBlock*"
  - "frontend/apps/dms/src/modules/contact/components/contact-detail/ExternalIds*"
  - "frontend/apps/dms/src/modules/contact/components/contact-detail/History*"
  - "frontend/apps/dms/src/modules/contact/components/contact-detail/history*"
  - "frontend/apps/dms/src/modules/contact/components/contact-detail/MergeContact*"
  - "frontend/apps/dms/src/modules/contact/components/contact-detail/Overview*"
  - "frontend/apps/dms/src/modules/contact/components/contact-detail/address*"
  - "frontend/apps/dms/src/modules/contact/components/contact-detail/commercial*"
  - "frontend/apps/dms/src/modules/contact/components/contact-detail/country*"
  - "frontend/apps/dms/src/modules/contact/contactOptions*"
  - "frontend/apps/dms/src/shared/displayNames.ts"
  - "frontend/apps/dms/src/hooks/useAdvisorOptions.ts"
  - "frontend/packages/ui-kit/src/components/ContactTypeBadge.tsx"
---
<!-- Maintainer note. PRD-Contacts v2.2 (its §Conformance review, D-03…D-26); ADR-014, ADR-030,
ADR-065, ADR-067, ADR-093; D-A-29. KAN-615 (2026-11-13) cut it to the invariants; vehicle parties,
trade-in holders and merges are contact-vehicle-party.md, screens contact-screens.md (KAN-751).
Frontend under src/modules/contact/ (KAN-636, ADR-161), listed by file: its vehicle-party and
sales files belong to other rules. Last verified against main@3b2bf69 (2026-11-18). -->

# Contacts

## Ownership
- **Contacts are owned by the dealer group** (ADR-014): one record per group, opened and
  deduplicated group-wide; a standalone dealership is a group of one — one dedupe path. The scope
  column is `group_id`, never `tenant_id`. Numbers come from a per-group sequence inside the
  caller's transaction.
- Sharing a contact between sister dealerships shares it between separate legal entities. The
  evidence record exists (`LegalBasis`, ADR-030, append-only; `enable_group_read` refuses without
  one); whether a real group may share data is the open legal question — raise it, never decide it.

## The rename from customers (KAN-464, ADR-210)
- Kept: the Alembic chain and history; old audit rows' `entity_type` `customer` (read);
  `{{customer.salutation}}`. Until PR-5: `customer.merged` stays subscribed (`sales.`/`inventory.customer_merged`), old payload keys, old-name events reported, `/customers/*` redirect.
- PR-4 guards: `test_kan464_no_customer_naming.py`, `no-customer-wording.test.ts`

## Contact channels (ADR-067) — child records, not columns
- `contact_phone`, `contact_email`, `contact_address`: one row per value, with a type, a label,
  `isPrimary` with **exactly one primary per type-group** (kept transactionally on every create,
  update and delete — re-elect when a primary goes), `validFrom`/`validTo`, `doNotUse` with a
  reason, and **consent per channel** with source and timestamp. One helper for all three,
  `_prepare_primary_change` (`services/contact_channels.py`).
- **Changing a row's type settles both type-groups** (Anto): the group it leaves re-elects; in the
  group it joins, `isPrimary: true` in the same PATCH makes it primary, otherwise the existing
  primary stays — a type change never silently demotes another row; a group with none elects it. A
  row the same PATCH closes or flags `doNotUse` never takes the flag.
- A create elects its row when the group has no **usable** primary; an existing usable primary is
  never demoted by a create that does not ask. **Reopening** a row (`validTo: null`, `doNotUse:
  false`) settles its group the same way — the existing usable primary keeps the flag unless the
  PATCH says `isPrimary: true` (Anto, KAN-349). Usability is judged after the PATCH:
  `isPrimary: true` on a closed or `doNotUse` row never unsets the group's usable primary.
- **A contact keeps at least one usable phone or email** (FR-03): closing, flagging or deleting
  the last usable one is refused; a row already unusable is never refused.
- The six contact projections (`phoneMobile`, `phoneLandline`, `phoneWork`, `email`,
  `emailSecondary`, `address`) are **read-model projections, never stored**: the flagged primary
  among usable rows, else the oldest usable, else null; `email` personal else work;
  `emailSecondary` work when distinct. **Never add a flat column.**
- `consentScope` (marketing / invoicing / service; required on a new grant, NULL on a legacy row
  reads as marketing) and `consentSource` (form / counter / web / phone) — FR-23.

## Rulings (all closed — do not re-open)
- **D-17 — a marketing send needs three gates:** `marketingConsent` (the revDSG legal basis) ·
  per-channel `consentGranted` with `consentScope = marketing` (`channel_authorises_marketing`, called
  by the send dialog's read) · `newsletter`. That read is
  `contact.public.message_recipient` (KAN-217): merged refuses every send, «Nicht kontaktieren» marketing
  only (Anto, option 2); a transactional send needs a usable row (Q1 A), marketing the three gates.
  It also gives the letter opening (`letter_salutation.py`).
- **D-20** — the address is optional at creation; an address-less contact is gated at contract
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
  who works in no Betrieb of the contact's group (`contact/ownership.py`). Checked only when the
  advisor changes, so a contact advised by a leaver stays editable.
- **D-25** — the list offers Export and Print; **no bulk anonymise**. **D-26** — `creditBlock`,
  with `creditBlockedAt`.
- **Legal form** is one of `ag`, `gmbh`, `einzelfirma`, `verein`, `genossenschaft`, `weitere`,
  labels localised; SA, Sàrl and Sagl are the French/Italian *names* of AG and GmbH, not separate
  forms; the registered name keeps its own suffix.
- **Canton is derived server-side from the postal code (D-13)**, never accepted from the client,
  and not removable (the list filter, a grid column and the audit trail depend on it).

## Contact groups (KAN-466; Anto, 2026-10-26)
- `contact_group_assignment`: codes of the platform list `contact_group` (Privat, Gewerbe, Flotte, …),
  several per contact, `contactGroups` full-replace like tags; free tags (`contact_tag`) stay
  beside them, the one exception to D-A-29 (Anto, 2026-10-26). Only a
  code the **acting** Betrieb switched on (`platform.public.contact_group_codes`) may be added; any
  other code the contact carries (switched off, withdrawn, a sister's) is read-only there — dropping
  it is 422 `contact_group_read_only`, never silently kept. A merge moves them all. List filter
  `contactGroup` / `contactGroupNot`.

## Roles (KAN-464; Anto, 2026-10-26 one register, roles; Q1 2026-11-14 A)
- `contact_role`: `customer` / `supplier`, one open row per role (`valid_to` closes it, never deleted);
  every contact that existed before was migrated with `customer`; a create without `roles` is a customer.
  `services/roles.py`.
- Lifecycle (prospect/active/inactive), credit block and advisor are the **customer role's**: without it,
  stored `active` is neutral, no D-24 advisor, setting one is 422 `customer_role_required`; taking the
  role away resets them (audited), refused while credit-blocked (409 `customer_role_credit_blocked`).
  Lifecycle filters on those values and the unassigned queue match customers only. A merge unites roles.
- **Who gives which role** (Q3, 2026-11-14 A): `contacts` write is sales, aftersales and inventory; the
  matrix row `contact_customer_role` (sales, aftersales) writes the customer role's data — giving or
  taking the role, a changed prospect/active/inactive on a contact (lifting do-not-contact included;
  setting it is every writer's), a changed advisor, the credit block, and a merge where either contact holds the role —
  else 403 `customer_role_not_permitted`, judged under the role rules' locks. Inventory creates
  suppliers; one number sequence for every contact (Q2 A), labelled «Kontaktnummer». Anto confirmed the
  advisor, credit block, status and merge parts (2026-11-17, option 1: restrictive). The services'
  `may_write_customer_role` defaults to trusted for tests; every call in `app/` passes it
  (`test_kan464_contact_permissions.py`).

## Blocks and flags
- **ADR-065 — a credit block stops contract confirmation, not the offer.** **Do-not-contact** is
  the `lifecycleStatus` value `do_not_contact`, not a flag, and stops the offer and the contract. A
  credit block is visible on the contact's own screens, not only inside the offer.
- **`vatStatusConfirmed`**: `vatRegistered = false` is also the default, so a second checkbox says
  someone checked it; every row started unconfirmed; the purchase booking's supplier VAT prefill reads it (KAN-465).

## History and labels
- Group-wide contact history must be served from an event-built projection keyed by `group_id`
  (reporting), never by fan-out across dealerships (rule 11). **Not built:** the History tab shows
  the audit log, its ids named on read (ADR-093) — a sister Betrieb and its users only while the
  group's read is enabled (ADR-030); an id nothing names renders as a translated marker, never a
  bare UUID.
- **An offboarded Betrieb's entries leave the History tab** (KAN-722; Anto, 2026-11-15), and exactly
  those are in its archive export — one rule, `app/core/audit_attribution.py`: the row's
  `acting_dealership_id`, else (older rows) an author whose only Betrieb in the group is that one.
  Every contact History write passes `acting_dealership_id` by name (`None` only for a write no
  Betrieb made); `tests/contact/test_kan722_offboarded_history_hidden.py` refuses one that does not.
- `advisor_label` follows a rename within a day (`contact.advisor_labels`, `events-worker.md`);
  its batches skip a contact another writer holds, which could otherwise deadlock against a merge.

## Screens
- The row menu's actions, address autocomplete (ADR-101) and how refusals are said:
  `contact-screens.md`.
