---
paths:
  - "frontend/packages/ui-kit/src/components/FormDialog*"
  - "frontend/packages/ui-kit/src/components/FieldHint*"
  - "frontend/packages/ui-kit/src/components/Picker.tsx"
  - "frontend/packages/ui-kit/src/components/RepeatableRowGroup*"
  - "frontend/packages/ui-kit/src/components/CounterTextarea.tsx"
  - "frontend/packages/ui-kit/src/components/StickyActionFooter*"
  - "frontend/packages/ui-kit/src/detail/InlineEditField*"
  - "frontend/packages/ui-kit/src/wizard/**"
  - "frontend/apps/dms/src/**/*Dialog*"
  - "frontend/apps/dms/src/**/*Modal*"
  - "frontend/apps/dms/src/**/*Create*"
  - "frontend/apps/dms/src/**/*Section.tsx"
  - "frontend/apps/dms/src/components/AddressSearchField.tsx"
  - "frontend/apps/dms/src/components/PhoneInput*"
  - "frontend/apps/dms/src/components/addressAutocomplete*"
  - "frontend/apps/dms/src/components/inlineEditRefusal*"
  - "frontend/apps/dms/src/modules/customer/components/customer-detail/OverviewTab.tsx"
  - "frontend/apps/dms/src/modules/customer/components/customer-detail/contactChannels*"
  - "frontend/apps/dms/src/modules/customer/components/customer-detail/address*"
  - "frontend/apps/dms/src/components/stock-detail/DetailsTab.tsx"
  - "frontend/apps/dms/src/components/vehicle-detail/IdentityTab.tsx"
  - "frontend/apps/dms/src/hooks/useFieldHint.ts"
  - "frontend/apps/dms/src/hooks/useInlineEditRefusal.ts"
  - "frontend/apps/dms/src/hooks/useAddressAutocompleteConfig.ts"
  - "frontend/apps/dms/src/hooks/useDebounced*"
  - "frontend/apps/dms/src/utils/googlePlaces*"
  - "frontend/apps/dms/src/architecture/address-autocomplete*"
  - "frontend/apps/dms/src/pages/account/**"
  - "frontend/apps/dms/src/pages/OnboardingPage*"
  - "frontend/apps/dms/src/pages/InvitationAcceptPage*"
---
<!-- Maintainer note. UI/UX Specification (forms, component contracts), ADR-101. Split out of ui.md
by KAN-615 (2026-11-13); forms are listed by file pattern until the frontend has module folders
(KAN-611). Last verified against main@73baf3f (2026-10-25). -->

# Forms and inline edit

- **A field's hint is an info icon beside the label** (Anto: "we want less text"): in the app
  `const hint = useFieldHint()` then `{...hint(t('label'), t('labelHint'))}` on any Mantine input,
  never a `description` line. The hint shows in a tooltip on hover and keyboard focus and reaches
  assistive technology as a visually hidden span — linked by Mantine for a wrapper input; a
  Checkbox or Switch passes `{ descriptionId: useId() }` as the third argument. The icon
  (`common.fieldHintIcon`) joins the field's accessible name, so a role query matches the label as
  a prefix — `{ name: (n) => n.startsWith(label) }`, never an unescaped `RegExp` (labels contain
  parentheses). Choice cards (the customer-type tiles) are not fields and keep their text.
- **The kit's `Picker` is the entity-selection gesture** ("which customer?": a ranked list in a
  panel, a create path, autofocus). A type-ahead that fills the form's own fields from an external
  service and selects no record — `components/AddressSearchField.tsx` (ADR-101), on Mantine's
  `Combobox` — is a form input like `PhoneInput` and lives in the app, every string from the
  bundles. A second such field reuses it, never a second local combobox.
- **Inline edit:** the kit's `InlineEditField` takes a required `describeError` and, beside an
  `onReload`, a `reloadLabel` — the app's `useInlineEditRefusal()`; only a stale If-Match 409
  (`isStaleVersion`) offers the reload. `RepeatableRowGroup` without a `describeError` shows its
  `genericError` label. A refusal's text follows `ui-i18n.md`. Inside a kit `DataGrid` cell the
  refusal and reload open in a popover outside the clipping cell (ADR-164, KAN-710) — no caller code.
- A CHF input is the kit's `ChfInput` (or `chfInlineEditor()` inside an `InlineEditField`), never a
  bare `NumberInput` (`ui-formats.md`).
