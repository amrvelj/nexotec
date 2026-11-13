---
paths:
  - "frontend/packages/ui-kit/src/format/**"
  - "frontend/packages/ui-kit/src/datagrid/FilterBuilder*"
  - "frontend/apps/dms/src/utils/format*"
  - "frontend/apps/dms/src/components/chfInputs*"
  - "frontend/apps/dms/src/components/PriceBuildUp.tsx"
  - "frontend/apps/dms/src/i18n/swissNumberInterpolation*"
  - "frontend/apps/dms/src/**/*dateOnly*"
  - "frontend/apps/dms/src/architecture/no-bare-money*"
  - "frontend/apps/dms/src/architecture/date-only*"
  - "frontend/apps/dms/src/architecture/no-utc-calendar-day*"
  - "frontend/apps/dms/src/architecture/no-locale-number-format*"
---
<!-- Maintainer note. ADR-076. Split out of ui.md by KAN-615 (2026-11-13). Last verified against
main@73baf3f (2026-10-25). -->

# Money, numbers and dates on screen

- **ADR-076 — every CHF amount reads `CHF 12'500.00`, inputs included:** a CHF input is the kit's
  `ChfInput` (or `chfInlineEditor()` inside an `InlineEditField`), never a bare `NumberInput` or
  `<input type="number">`; `no-bare-money-number-input.test.ts` names every NumberInput that is
  not money. A number filter's saved text formats its value through the field's `numberFormat`
  (`ui-grid.md`).
- **A date-only value** (a backend `dt.date`: birth date, purchase date, first registration, a
  plate's validity …) names a calendar day and renders through `formatDateOnly`; `formatDate` and
  `formatDateTime` are for timestamps, rendered in the browser's zone.
  `src/architecture/date-only-fields-format.test.ts` judges a field by its name (`…Date`, `…Since`,
  `…FollowUp`, `expectedDelivery`, `vehicleFirstRegistration`) inside `formatDate`/`formatDateTime`,
  in CustomersListPage's `date('…', …)` helper, as a raw JSX child and as a grid cell returning the
  bare field. **It is a source scan with known misses** (listed in its comment) and cannot judge
  `validFrom`/`validTo` at all (a date on a plate, an accessory, a user's access window, a
  valuation; a timestamp on a contact channel): the ones a screen renders, and every screen that
  renders a date-only field, are checked by that screen's render test under
  `TZ=Pacific/Honolulu` (`dateOnlyTabs`, `UsersListPage.dateOnly`, `AuthScreens`).
- **Today's date as a pre-fill is `todayLocalIsoDate()`** (`utils/format.ts`), the user's calendar
  day — never `toISOString().slice(0, 10)`, the UTC day, which is yesterday between midnight and
  01:00/02:00 Swiss time (`src/architecture/no-utc-calendar-day.test.ts`).
