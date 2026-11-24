---
paths:
  - "app/setting_registry.py"
  - "app/platform/organisation/services/setting_*.py"
  - "app/platform/organisation/models/setting_value.py"
  - "app/platform/administration/*/area_settings.py"
  - "tests/platform/test_kan805_*.py"
  - "tests/setting_store_support.py"
  - "tests/architecture/test_setting_keys_declared_and_read.py"
---
<!-- Maintainer note. ADR-214, ADR-216; Anto's rulings of 2026-11-24 on KAN-805 (restore for the store
only, history read by every role, checklist settings declared after activation only notify, the
per-location column while any location holds a value). Split out of platform-admin.md by KAN-805 PR-2. -->

# The settings store (KAN-805) — store and API built; checklist, copy and screen to come

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
  `GET …/{key}/history` (newest first, cursor), `POST …/setting-changes/{id}/restore` (If-Match). A
  setting of a module the Betrieb does not hold is 404 (a non-manager's write is 403 first, the role check runs first); a value that does not fit is 422 with its
  reason, a deactivated location's 409 `setting_location_inactive`.
