---
paths:
  - "app/sales/services/document.py"
  - "app/sales/services/message_send.py"
  - "app/sales/api/documents.py"
  - "app/sales/schemas/document.py"
  - "app/sales/models/document.py"
  - "tests/sales/test_sales_document*.py"
  - "tests/sales/test_kan717_sales_document_location.py"
  - "tests/cross_context/test_kan217_*.py"
  - "tests/architecture/test_margin_never_in_rendered_document.py"
  - "frontend/apps/dms/src/modules/sales/components/DocumentSendDialog*"
---
<!-- Maintainer note. ADR-047, ADR-051, ADR-063, ADR-204; Dealer Administration D-A-41, FR-A-51–54.
Split out of sales.md by KAN-800 (2026-11-25) for the scenario budget (KAN-743's precedent).
Last verified against main@32ad509 (2026-11-25). -->

# Sales documents: rendering, wording, sending

- **ADR-063** — build, then review the rendered document in the **contact's correspondence
  language** with the seller-only margin panel beside it, never on it
  (`test_margin_never_in_rendered_document.py`). No contact language: the Betrieb's own default
  (Contacts FR-13), never a silent German.
- **The wording is the Betrieb's standard text**: offer intro and footer, contract footer, through
  `platform.public.render_standard_text` with Sales' facts; a token Sales cannot fill fails the
  generation before anything is written. Sales holds no text column (ADR-051,
  `platform-documents.md`).
- **Sending a document** (KAN-217, ADR-204): `POST /v1/sales/documents/{id}/send`
  (`services/message_send.py`) takes Contacts' recipient and verdict (`contact.public.message_recipient`;
  a refusal is 409 `recipient_refused`, nothing sent), the library's text in the contact's language
  (WhatsApp sends the approved template exactly, 422 `whatsapp_text_fixed`), attaches the PDF to an
  e-mail or a secure link (`platform.public.issue_document_link`) to SMS/WhatsApp, and delivers through
  `integration.public.deliver_message` (`integration-channels.md`). Each side commits its own work
  (ADR-047); Sales writes nothing, and an unused link expires in 14 days.
