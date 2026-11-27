---
paths:
  - "app/platform/organisation/models/labour_rate.py"
  - "app/platform/organisation/services/labour_rate*.py"
  - "app/platform/identity/**/user_labour_category.py"
  - "app/platform/administration/*/labour_rates.py"
  - "app/api/v1/labour_rate_agreements.py"
  - "tests/platform/test_kan806_*.py"
  - "app/labour_rate_agreement_merge.py"
  - "tests/cross_context/test_kan831_*.py"
  - "frontend/apps/dms/src/pages/LabourRatesPage*"
  - "frontend/apps/dms/src/components/labourRates/**"
---
<!-- Maintainer note. KAN-806 (2026-11-25); D-A-19 of the Dealer Administration PRD; Aftersales slice 1
sections 5, 7 (checks 11, 12, 15, 49), 9 (rulings 177, 178, 183), 10; Anto's answers on KAN-806 of
2026-11-25 (in use from the start date; agreements follow O16; no starting categories; the checklist item
counts a rate still to come). Binding text: the slice page and the ADR in the Target Architecture.
Last verified against main@dbf886d (2026-11-26, KAN-833). -->

# Labour rates (KAN-806, D-A-19) — built: backend (PR-1), screen and checklist items (PR-2)

- **Platform master data, not Aftersales'** (A-RULE-0): `labour_rate_category` (dealer-defined, a name
  with optional translations, ruling 176; deactivated, never deleted), `labour_rate` (category × location
  from a date), `labour_rate_agreement` (a contact's fixed rate for a category, Betrieb-wide, from a date;
  an `end` row stops it; the contact is the three-column pattern), `user_labour_category` (identity: a
  user who may act in the Betrieb, in several categories). No starting categories (Anto, 2026-11-25).
- **Ruling 178 — written once.** No PUT/PATCH on a rate or an agreement (a test walks the routes). A new
  price is a new row from today or later (`labour_rate_starts_in_the_past`); a row still to come may be
  withdrawn (`withdrawn_at`, If-Match), one whose date has come is in use from its first day
  (`labour_rate_in_use`). A withdrawn row is kept: a priced position's rate id never dangles.
- **Ruling 177 — `entered` net or gross,** checked against O16 `labour_rates.rates_entered` at the rate's
  location (an agreement: the Betrieb's own). Not set → 409 `labour_rates_entry_basis_not_set`; the client's
  `entered` differs → 409 `labour_rate_entry_basis_changed` — never relabelled. Platform carries no net
  amount (`test_net_line_only_where_ruled.py`): the other figure follows in Aftersales with `app.core.money.Price`.
- **Ruling 183 — always per hour.** O18 `labour_rates.time_unit` (hours | work units, not set = hours) and
  O19 `labour_rates.work_units_per_hour` (1–100, whole); work units need O19 (the store's `choice_requires`,
  check 49). Read through `platform.public.labour_time_unit`.
- **The question:** `platform.public.labour_rate_for(…, category_id, location_id, on, invoice_recipient_contact_id)`
  — agreement in force first, else the category rate at the location, else `found=False` (`no_rate` /
  `unknown_category`), never an error or a default; it names the source and the row. GET
  `…/labour-rate-answer` shows it. `labour_categories_of_user` for «the rate from who did the work».
- **API** (`administration/api/labour_rates.py`, matrix row `labour_rates`): every route 404s without the
  `aftersales` module (a service-level check, not a router gate: router gates are sales' and aftersales' own
  routers' and planning's, ADR-221); the role check runs
  first, so a role outside the row gets 403 first, as on the settings API. The agreement
  POST lives at the composition root (`app/api/v1/labour_rate_agreements.py`, it reads the contact; listed in
  `session_guard`'s administration prefixes). No outbox event (nothing consumes one). Reconciliation checks
  the agreement's contact (exists, not merged, in the group), the rate's location, and an O18 slot on work units
  with no O19 in force (a location deactivated, the Betrieb's O19 cleared, the location reactivated).
- **Screen** `/administration/labour-rates` (`LabourRatesPage`, nav entry shown with `aftersales`): it embeds the
  shared settings rows (`SettingRows` with `keys`) for O16, O18 and O19 — the same value, history and
  per-location column as the generic screen `/administration/settings/labour_rates` (KAN-805 criterion 6;
  ADR-219 addendum; no static settings route may shadow `:area`, a test says so) — then each category with its
  rates (status today, withdraw only when scheduled) and people, and the agreed rates (cursor-paged, withdrawn
  ones shown). The rate dialog enters a rate as O16 says at the chosen location (its own value, else the
  Betrieb's). Rates are typed to the Rappen (`ChfInput`); the API takes four decimals. The dialogs pre-fill
  the business's `today` from the API, the day the server checks against.
- **Checklist (Aftersales):** O16 (`labour_rates.rates_entered`, a setting item) and `labour_rates` — an active
  category with a rate at an active location, one still to come counting (Anto, 2026-11-25); neither blocks an
  action after activation yet. `tests/setup_checklist_support.complete_checklist` completes both for an
  Aftersales Betrieb.
- **A contact merge and a rename (KAN-831, ADR-232)** — composed in `app/labour_rate_agreement_merge.py`
  (contact sits above platform): `platform.labour_rate_agreements_on_contact_merged` moves the duplicate's rows
  to the end of the survivor's chain (`services/labour_rate_contacts.py`). **The survivor's agreement survives**
  (Anto, option A, 2026-11-27): once the survivor holds a live rate row of a category in the Betrieb (an end alone is none), every row
  of the duplicate for that category is **set aside** (`set_aside_at`: never an answer of `labour_rate_for`,
  `superseded` on the screen, listed with the withdrawn, not reported); otherwise all move. The POST and the
  consumer serialise on `lock_agreement_contact`. Nightly `platform.labour_rate_agreement_labels`.
- **Open:** copying categories from a sister Betrieb (KAN-220's subset).
