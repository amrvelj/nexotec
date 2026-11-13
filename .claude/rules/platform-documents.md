---
paths:
  - "app/platform/api/document_template.py"
  - "app/platform/api/standard_text*.py"
  - "app/platform/api/number_ranges.py"
  - "app/platform/services/document_*.py"
  - "app/platform/services/standard_text*.py"
  - "app/platform/services/text_delegation.py"
  - "app/platform/services/number_range.py"
  - "app/platform/models/document_template.py"
  - "app/platform/models/standard_text*.py"
  - "app/platform/models/number_range.py"
  - "app/platform/public.py"
  - "app/platform/schemas/document_*.py"
  - "app/platform/schemas/standard_text.py"
  - "app/platform/schemas/number_range.py"
  - "tests/test_document_*.py"
  - "tests/test_standard_texts.py"
  - "tests/test_platform_standard_text_seeds.py"
  - "tests/test_text_delegations.py"
  - "tests/test_kan208_footer_migration.py"
  - "tests/test_number_ranges.py"
  - "tests/architecture/test_no_layout_code_outside_document_render.py"
  - "tests/architecture/test_standard_texts_write_honours_delegations.py"
  - "frontend/apps/dms/src/pages/StandardTextsPage*"
  - "frontend/apps/dms/src/components/standardTexts/**"
  - "frontend/apps/dms/src/pages/NumberRangesPage*"
  - "frontend/packages/ui-kit/src/components/DocumentPreview.tsx"
---
<!-- Maintainer note (stripped before Claude sees it). Summarises ADR-047, ADR-051, ADR-109 and the
Dealer Administration PRD's standard texts (FR-A-28, D-A-23, A-RULE-4) and number ranges (D-A-17,
A-RULE-3). Cut from platform.md by KAN-614 (2026-11-10) to the invariants; platform.md was verified
against main@73baf3f on 2026-10-25 - this cut was not re-verified against the code. -->

# Documents, standard texts and number ranges

## Document templates (ADR-051)
- One shared template layer. WeasyPrint only in `app/platform/services/document_render.py`; no
  HTML-tag string literal (`<table`, `<div`, `<style`, `<html`) elsewhere in `app/`
  (`test_no_layout_code_outside_document_render.py`). One template, two consumers: the ui-kit
  `DocumentPreview` only displays the PDF the renderer produced — never a second renderer.
- `document_template` holds the header note only; a document's footer wording is its own standard
  text, frozen in `ContentDefinition.footer_text`. The header logo is the primary branding asset,
  inlined from the object store while the `document_header` placement is on.
- **WeasyPrint loads `data:` URIs and nothing else** (`document_render.local_url_fetcher`): every
  byte a document prints is in its own HTML, so no http(s), file or relative reference connects
  anywhere — no SSRF from a render.

## Standard texts — built
- `standard_text`: one row per scope × code × language — platform seeds (`dealership_id` NULL), a
  Betrieb's own texts and its overrides of seeds, and location variants. "The standard text has
  changed, review yours" is **derived on read** (the seed's `version` past the reviewed one), cleared
  by PATCH `seedReviewed: true`. `category` + `applies_to` + `slot` say where a text is used.
- **Placeholders are a closed registry** (`services/standard_text_placeholders.py`, the PRD's eight
  tokens per target): an unknown, invalid or malformed token is refused on save (422
  `standard_text_placeholder_invalid`), and **a render fails** on any of those or on a token without
  a value (422 `standard_text_placeholder_unresolved`, A-RULE-4) — never a blank.
- Resolution: language requested → `default_correspondence_language` → DE outside; location →
  dealership → seed inside. A dealership row masks its seed even switched off; a slot resolves to
  its default (narrowest scope, then `sort_order`, `code`) or to nothing. Other contexts call
  `platform.public.render_standard_text`.
- **Delegable per category** (FR-A-28): `standard_text_delegation`, categories `document · email ·
  sms · whatsapp · internal_note · workshop`, set by the manager or staff, ended by deactivation.
  Writes go through `require_write_or_delegated("standard_texts")`, only on a route whose path
  carries `{category}`, never the plain `require_write` (`test_standard_texts_write_honours_delegations.py`).
  Every lookup is bound to the path's category; a seed is never written there.
- The seed library and the UI strings are edited by `platform_admin` through the Translations panel
  (D-A-23), under the one matrix row `ui_translations`; every seed edit moves its version, so every
  override of it reads `seedChanged`; revert restores the shipped wording
  (`services/standard_text_seed.py`). **Dealers never override UI strings** — no endpoint exists.
- The texts screen edits the Betrieb's scope only; a location variant is written through the API
  alone (neither the texts nor the Locations screen offers it yet).
- Carried-over footers: each non-empty `document_template` footer became the Betrieb's **default**
  offer and contract footer (`offer|contract.footer.carried_over`) and an invoice text that is not
  the default (`invoice.footer.carried_over`), so documents keep printing what they printed; an
  unprintable one was removed and printing falls back to the seed footer (Anto).
  Sales prints the offer's intro and footer and the contract's footer.

## Number ranges (D-A-17, A-RULE-3, ADR-109) — built
- **Gapless per legal entity**, one sequence across all its sites; `{location_code}` is a label.
  One `number_range` row per Betrieb per document type (eleven, the PRD's table; customer numbers
  stay per group, not here), seeded unfrozen at `create_dealership` (`services/number_range.py::SEEDS`).
- Pattern: literals `A–Z a–z 0–9 - / . _`, `{year}` (Swiss year), `{location_code}`, exactly one
  `{counter:N}` (N 1–9), at most 16 characters rendered (422 `number_pattern_*`). A yearly reset
  needs `{year}`; an offer may not print `{location_code}` (it knows no site yet); a counter too
  short for the next value is refused.
- **Platform hands the number out inside the caller's transaction** —
  `platform.public.allocate_number(...)`, `SELECT … FOR UPDATE` on the range row, never a commit —
  so a document write that fails consumes no number. **ADR-109** is the ruled exception to ADR-047
  (CLAUDE.md rule 12), classified in `test_adr_047_no_shared_transaction.py`; number allocation is
  the only shared transaction it rules (ADR-091's backfill expired with its script, KAN-79).
- The first number freezes the range (audit `number_range_frozen`); after that a change is 409
  `number_range_frozen` for everyone but a `platform_admin`, who must give a reason.
- The screen shows the ranges of the modules the Betrieb holds, the next number as an example and
  the current value read-only, and states the freeze before the first number. **Only the offer draws
  from it today** (no `continue_after` since KAN-540, ADR-159); contracts, stock numbers and the rest switch in their own tickets.
