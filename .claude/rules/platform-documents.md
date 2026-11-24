---
paths:
  - "app/platform/documents/api/document_template.py"
  - "app/platform/documents/api/standard_text*.py"
  - "app/platform/organisation/api/number_ranges.py"
  - "app/platform/documents/services/document_*.py"
  - "app/platform/documents/services/standard_text*.py"
  - "app/platform/services/standard_text_seed.py"
  - "app/platform/identity/services/text_delegation.py"
  - "app/platform/organisation/services/number_range.py"
  - "app/platform/documents/models/document_template.py"
  - "app/platform/documents/models/standard_text*.py"
  - "app/platform/documents/services/message_*.py"
  - "app/message_type_registry.py"
  - "app/sales/message_types.py"
  - "tests/platform/test_kan217_message_types.py"
  - "app/platform/identity/models/standard_text*.py"
  - "app/platform/organisation/models/number_range.py"
  - "app/platform/public.py"
  - "app/platform/documents/schemas/document_*.py"
  - "app/platform/documents/schemas/standard_text.py"
  - "app/platform/organisation/schemas/number_range.py"
  - "tests/platform/test_document_*.py"
  - "tests/platform/test_standard_texts.py"
  - "tests/platform/test_platform_standard_text_seeds.py"
  - "tests/platform/test_text_delegations.py"
  - "tests/platform/test_kan208_footer_migration.py"
  - "tests/platform/test_number_ranges.py"
  - "tests/architecture/test_no_layout_code_outside_document_render.py"
  - "tests/architecture/test_standard_texts_write_honours_delegations.py"
  - "frontend/apps/dms/src/pages/StandardTextsPage*"
  - "frontend/apps/dms/src/components/standardTexts/**"
  - "frontend/apps/dms/src/pages/NumberRangesPage*"
  - "frontend/packages/ui-kit/src/components/DocumentPreview.tsx"
  - "frontend/apps/dms/src/pages/LetterheadPage*"
  - "frontend/apps/dms/src/components/letterhead/**"
  - "tests/sales/test_sales_document_letterhead.py"
  - "tests/sales/test_kan717_sales_document_location.py"
---
<!-- Maintainer note (stripped before Claude sees it). Summarises ADR-047, ADR-051, ADR-109, ADR-159 and
the Dealer Administration PRD's standard texts (FR-A-28, D-A-23, A-RULE-4) and number ranges (D-A-17,
A-RULE-3). Cut from platform.md by KAN-614 (2026-11-10). Last verified against main@3b2bf69
(2026-11-18). -->

# Documents, standard texts and number ranges

## Document templates (ADR-051)
- One shared template layer. WeasyPrint only in `app/platform/documents/services/document_render.py`; no
  HTML-tag string literal (`<table`, `<div`, `<style`, `<html`) elsewhere in `app/`
  (`test_no_layout_code_outside_document_render.py`). One template, two consumers: the ui-kit
  `DocumentPreview` only displays the PDF the renderer produced — never a second renderer.
- `document_template` holds the header note and the layout (KAN-206): `header_layout`, `logo_size`
  (S/M/L = 30/40/50 mm, 20 mm high), `address_window` (left/right, default right), `footer_columns`
  (ordered, ≤ 4, `company` always — A-27), `draft_watermark`, `page_numbers`. A document's footer
  wording is its own standard text, frozen in `ContentDefinition.footer_text`; the footer's columns
  are read from master data **at render time** (`services/document_letterhead.py`), never copied.
- **The envelope window is Swiss Post's** (`document_letterhead.WINDOW_GEOMETRY`, ePost's C5 window,
  to confirm with a test print); `tests/platform/test_document_letterhead.py` measures WeasyPrint's laid-out
  box (±0.5 mm). Words a document prints (page numbers, «ENTWURF»/«MUSTER», footer labels) are in
  `document_letterhead.py`, four languages, in the document's language.
- **Per location: address block and logo only** (A-27): `location.letterhead_address_override` as
  written, `branding_asset.location_id`'s logo, and the site's own bank account in the banking column
  (ADR-051 addendum); the company column is always the legal entity. Sales prints for the responsible seller's primary site in the Betrieb, frozen on
  `sales_document.location_id` at generation; none prints for the seat (Anto, KAN-717).
- The header logo is the site's, else the primary branding asset, while `document_header` is on.
  A `SignatureBlock` freezes the signer's user id; the image is read at render time (Q2 A).
  `render_document(watermark=DRAFT)` prints the draft mark only while the template has it on.
- The preview (`GET …/document-template/preview?document=offer|invoice&location=`) goes through
  `render_document` with «MUSTER»; only `document_templates` writers may call it (it prints the IBAN).
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
- **Two shipped editions** (ADR-133): KAN-208's starter set (`services/standard_text_seed.py`,
  frozen) and KAN-217's message texts (`documents/services/message_text_seed.py`, literals in
  `a4d9e6c3b7f2`); `standard_text.SHIPPED_SEED_TEXTS` is both, for install, listing and revert.
- **Message types (FR-A-52/53, KAN-217):** `documents/services/message_types.py` — a module
  registers each document or reminder type it sends (purpose transactional | marketing, its text
  code per channel) through `platform.public.register_message_type`, from the composition root
  `app/message_type_registry.py`; a wrong or conflicting one fails at start-up. E-mail attaches the
  document, SMS and WhatsApp send a secure link the send service appends — no text names it.
  Sales registers `offer`, `order_confirmation`, `sales_contract`; it sends the first and last (ADR-204).
- **Secure links (KAN-217 PR-4, ADR-193):** `document_link` holds a link's SHA-256, never the token;
  14 days; issued on platform's own session (ADR-204). The URL is `{app}/d?lang=<lang>#<token>`: the fragment never reaches a server, so no proxy or access
  log keeps it; the SPA's public `/d` sends it in the `X-Document-Link` header (a secret field,
  `app/core/redact.py`) to `GET /v1/document-links/current`, which renders through the **document
  source** the owning module registers (`platform.public.register_document_source`), inside the
  link's own Betrieb only. Every refusal (unknown, expired, offboarded, another Betrieb's or type's
  document, > 30 opens an hour) is the same 404. Never put a token in a path or query.
  The page is the dealer's (KAN-217 PR-7, ADR-208): `…/current/sender` names the issuing Betrieb
  (trade name, else legal name) and `…/current/logo` serves its primary logo, both by the same header;
  an unusable link names nobody (the neutral text), a usable one whose document fails names its Betrieb.
  The sender read claims no read of the window; the logo and the document claim one each.
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
