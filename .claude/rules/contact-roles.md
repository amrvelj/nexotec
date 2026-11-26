---
paths:
  - "app/contact/services/roles.py"
  - "app/contact/services/contact.py"
  - "app/contact/services/contact_refs.py"
  - "app/contact/services/contact_merge.py"
  - "app/contact/api/contacts.py"
  - "app/contact/reconciliation.py"
  - "app/contact/services/contact_search.py"
  - "app/contact/models/contact.py"
  - "app/contact/schemas/contact.py"
  - "frontend/apps/dms/src/modules/contact/pages/ContactsListPage.tsx"
  - "tests/contact/test_kan464_*.py"
  - "tests/contact/test_contact_credit_block.py"
  - "tests/contact/test_kan810_*.py"
  - "frontend/apps/dms/src/modules/contact/components/contactRowMenu*"
  - "frontend/apps/dms/src/modules/contact/components/contact-detail/Blocking*"
  - "frontend/apps/dms/src/modules/contact/components/contact-detail/blocking*"
  - "frontend/apps/dms/src/modules/contact/components/contact-detail/ContactBlock*"
  - "frontend/apps/dms/src/modules/contact/components/contact-detail/contactBlock*"
  - "frontend/apps/dms/src/modules/contact/components/contact-detail/CreditBlock*"
  - "frontend/apps/dms/src/modules/contact/components/contact-detail/creditBlock*"
  - "frontend/apps/dms/src/modules/contact/components/contact-detail/ContactRoles*"
  - "frontend/apps/dms/src/modules/contact/components/contact-detail/contactRoles*"
---
<!-- Maintainer note. Split from contact.md (KAN-810, 2026-11-26) when «Gesperrt» took it over its
budget; the two sections moved as they were, with «Gesperrt» added to them. KAN-464 (Q1 2026-11-14 A, Q3), ADR-065, KAN-810. -->

# Contacts — roles and blocks

## Roles (KAN-464; Anto, 2026-10-26 one register, roles; Q1 2026-11-14 A)
- `contact_role`: `customer` / `supplier`, one open row per role (`valid_to` closes it, never deleted);
  every contact that existed before was migrated with `customer`; a create without `roles` is a customer.
  `services/roles.py`.
- Lifecycle (prospect/active/inactive), credit block, «Gesperrt» and advisor are the **customer role's**: without it,
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
- **«Gesperrt» (`blocked`, KAN-810; Anto, 2026-11-26)** is a block of its own, beside the credit block:
  a workshop order refuses the contact as its orderer or invoice recipient, while the credit block
  only changes how such an order is paid (at pick-up). Sales does not read it. Shaped like the credit
  block — `POST /v1/contacts/{id}/block`, a reason to place it (409 `contact_block_reason_required`),
  `blockedAt`, audited `block_changed`, the customer role's data (409 `customer_role_blocked` when
  the role is taken away), `contact.public`'s `ContactRef.blocked`. One SPA dialog serves both
  blocks (`ContactBlockDialog`, kind `credit` or `contact`).
- **`vatStatusConfirmed`**: `vatRegistered = false` is also the default, so a second checkbox says
  someone checked it; every row started unconfirmed; the purchase booking's supplier VAT prefill reads it (KAN-465).
