---
paths:
  - "app/setting_registry.py"
  - "app/platform/organisation/services/setting_*.py"
  - "app/platform/organisation/models/setting_value.py"
  - "app/platform/administration/*/area_settings.py"
  - "tests/platform/test_kan805_*.py"
  - "tests/setting_store_support.py"
  - "tests/architecture/test_setting_keys_declared_and_read.py"
  - "frontend/apps/dms/src/components/settings/**"
  - "frontend/apps/dms/src/pages/Settings*Page*.tsx"
---
<!-- Maintainer note. ADR-214, ADR-216; Anto's rulings of 2026-11-24 on KAN-805 (restore for the store
only, history read by every role, checklist settings declared after activation only notify, the
per-location column while any location holds a value). Split out of platform-admin.md by KAN-805 PR-2.
Last verified against main@d2fef39 (2026-11-27); O6/O7 and `divides` added by KAN-808 PR-1c; the multiple's
screen hint and check by KAN-808 PR-2; the check made the store's exactly (newly broken scope, stale slot, clear) by KAN-842. -->

# The settings store (KAN-805) — built: store, API, checklist, copy, screen

- An area **declares** each setting it reads (`SettingDeclaration`: key `<area>.<name>`, kind switch ·
  number · amount · choice · text, what «not set» does — `quiet`, `refuses`, `acts_as`, never for an
  amount or a text, D-A-06 —, checklist item) with `platform.public.register_setting`, called from the
  composition root `app/setting_registry.py`; it **reads** with `platform.public.read_setting(db,
  dealership_id=, location_id=, key="<literal>")`: the location's value, else the Betrieb's, else «not
  set». `test_setting_keys_declared_and_read.py` fails on a key read and not declared, declared and
  not read, or read without a literal: a declaration ships with its reader.
- `setting_value` (one slot per Betrieb and per location; «not set» a state) and the append-only
  `setting_value_change`; every change audited. **Restore is kept for the store only** (Anto,
  2026-11-24; D-A-39 / KAN-219 dropped elsewhere); the history is read by every role. Lists and rules
  (labour rates, the service catalogue) are their area's data, not settings. `dealership_settings`
  stays as it is.
- **API** (`administration/api/area_settings.py`, matrix row `dealership_settings`: every role reads,
  the manager writes): `GET …/setting-areas`, `…/setting-areas/{area}` (`showsLocations`: more than one
  active location, or any location value — Anto; `fitsDeclaration: false`: a stored value that no
  longer fits reads as not set), `PUT …/setting-values/{key}` and
  `…/{key}/locations/{location_id}` (If-Match on the slot, 0 never written; `{"value": null}` clears; a number travels as a decimal string),
  `GET …/{key}/history` (newest first, cursor; each change with `valueBefore`/`isSetBefore` and
  `createdByName` for a user the Betrieb knows — its own or a member), `POST …/setting-changes/{id}/restore` (If-Match). A
  setting of a module the Betrieb does not hold is 404 (a non-manager's write is 403 first, the role check runs first); a value that does not fit is 422 with its
  reason, a deactivated location's 409 `setting_location_inactive`.
- **A rule across two keys** (KAN-806, check 49): `SettingDeclaration.choice_requires = ((choice, key),)` —
  choosing the choice needs `key` in force; enforced in `change_setting` (restore too) under the Betrieb's lock
  for the Betrieb and every active location holding a slot of either key (`setting_rules.py`): 422
  `setting_choice_requires_setting` / `setting_required_by_choice`, with the keys and location in `details`.
  `check_declaration_references()` (start-up) refuses a rule naming an undeclared key; the area API shows
  `choiceRequires`. The copy from a sister writes a required key before the choice that needs it
  (`declared_settings_in_write_order`; a test copies O19 + O18 «work units» whole). Shipped declarations: O16, O18, O19
  (`platform-labour-rates.md`); O6 `planning.grid_minutes` and O7 `planning.slot_minutes` (KAN-808, `planning.md`).
- **A divisor rule** (KAN-808): `SettingDeclaration.divides = Decimal(n)` — a whole number with a minimum above
  zero whose value must divide `n` (O6: a divisor of 60); else 422 `setting_value_not_a_divisor` (`details.divides`).
- **A multiple** (KAN-808; Anto, 2026-11-25): `SettingDeclaration.multiple_of = "<key>"` — a whole number that is a
  multiple of that key's value wherever both are in force (O7 of O6), checked like check 49 (same lock, scopes, «never
  worse»): 422 `setting_not_a_multiple_of_setting` writing the multiple, `setting_no_longer_divides_setting` writing the
  other (`details`: `setting`, `multipleOf`, `locationId`); «not set» on either side breaks nothing.
  `check_declaration_references()` refuses an undeclared key, another module's, or one that is no whole number above
  zero; the copy writes the pair as one change (KAN-839, below); platform's reconciliation reports a scope a
  reactivated location left broken. The area API names both on the declaration (`divides`, `multipleOf`; KAN-808 PR-2); `SettingCell` hints
  them with the info icon beside the value and checks them before the write (`components/settings/numberRules.ts`): a
  `divides` number offers only its divisors within the bounds, a multiple is checked against the other number in force
  in that column (the Betrieb's column: also at each active location taking the Betrieb's value), and a divisor whose
  multiple would break is offered disabled — as `unavailableAt` mirrors check 49; the API stays the authority. The
  check is `numberRules.multipleRuleViolation`, `check_multiple_rules` line for line (KAN-842): the same scopes, only a
  scope the write newly breaks refuses (a location already broken does not block the Betrieb's change), a stale slot
  (`fitsDeclaration: false`) reads as not set, and a clear is checked too.
- **Setup checklist** (`administration/services/setup_checklist.py`): a declaration with
  `checklist_item` is an item, code = its key (a contributed code never has a dot), applying with
  its module, linked to `/administration/settings/<area>`, complete while **the Betrieb's own value**
  is in force (`setting_store.is_set_for_dealership`; a location's value overrides it, never stands
  in for it — a read without a location would answer «not set»; a value that no longer fits does
  not count; ADR-216). It blocks only the `BlockedAction` its declaration names
  (`platform.public.register_setting` refuses an unknown one at start-up); otherwise an active
  Betrieb is only notified by the daily regression job, whose notice carries `blocks` only for an
  item blocking one — the SPA then says the action is blocked (`body_blocked`; Anto, 2026-11-24).
  The SPA names it by the setting's label (`settings.keys.<area>.<name>.label`,
  `utils/checklistItem.ts`); `test_declared_settings_have_labels.py` fails on a declared setting
  without its label in the four languages, on a shipped checklist setting while the SPA does not
  serve `/administration/settings/:area`, and on a static `/administration/settings/<area>` route
  that would hide an area from the generic screen (criterion 6; KAN-806's topic screen lives at its
  own path and embeds the rows).
- **Copy from a sister Betrieb** (`administration/services/configuration_copy.py`, subset `settings`):
  the source's own value of each setting the target configures (module held, or none), written through
  the store (`change_setting`, a `multiple_of` pair `change_settings`) as origin `copy` (history, restore); «not set» in the source keeps the target's
  value; a location's value is never copied; a source value that no longer fits is skipped
  (`setting_value_stale`). A pair tied by `multiple_of` (O6/O7) goes through `setting_store.change_settings` as
  one change (KAN-839): the rules judge the state after both keys, each key keeps its own history and audit row,
  so source 10/40 lands whole on target 15/45; refused as a whole (a target location's own value), each key is
  tried alone.
- **Screen** (`/administration/settings`, the areas; `/administration/settings/:area`): one row
  component, `components/settings/SettingRows.tsx`, which a topic screen embeds with `keys` (ticket
  criterion 6, 2026-11-25). «Not set» is a slate badge (never empty, zero, «off» or the empty-field
  italic; ADR-220, Anto 2026-11-25), with what it acts as or refuses; a location without its own
  value shows the Betrieb's, readable, with a «vom Betrieb» badge; a stale value is removed by its
  own button, never by an untouched blur; a choice whose `choiceRequires` key is not in force in that
  column (the location's own value, else the Betrieb's, else its `acts_as`) is offered disabled (KAN-806,
  check 49; a required key of another area is not in the area's data, so there the API's refusal shows); the
  history opens under its row: who, when, before → after, restore (manager) as a new entry. No design
  is drawn for it (ticket, «Design»). An area needs `areas.<area>` and a unit `units.<unit>` in the
  `settings` bundles (`test_declared_settings_have_labels.py`).
