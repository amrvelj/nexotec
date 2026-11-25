---
paths:
  - "app/integration/adapters/mail_*.py"
  - "app/integration/adapters/messaging_*.py"
  - "app/integration/adapters/google_business_profile.py"
  - "app/integration/api/mail.py"
  - "app/integration/api/google_business_profile.py"
  - "app/integration/models/mail.py"
  - "app/integration/models/google_business_profile.py"
  - "app/integration/schemas/mail.py"
  - "app/integration/schemas/google_business_profile.py"
  - "app/integration/services/mail_*.py"
  - "app/integration/services/messag*.py"
  - "app/integration/services/gbp_*.py"
  - "alembic/versions/integration/*_kan215_*.py"
  - "alembic/versions/integration/*_kan217_*.py"
  - "alembic/versions/integration/*_kan226_*.py"
  - "tests/integration/test_kan215_*.py"
  - "tests/integration/test_kan217_*.py"
  - "tests/integration/test_kan226_*.py"
  - "tests/integration/test_kan762_*.py"
  - "tests/integration/test_kan763_*.py"
  - "tests/integration/test_kan776_*.py"
  - "tests/integration/test_kan784_*.py"
  - "tests/integration/test_kan787_*.py"
  - "tests/integration/test_kan793_*.py"
  - "tests/integration/test_kan821_*.py"
  - "tests/architecture/test_kan217_delivery_takes_contacts_verdict.py"
  - "tests/architecture/test_notifications_never_reach_the_mail_port.py"
  - "frontend/apps/dms/src/pages/businessProfile/**"
  - "frontend/apps/dms/src/pages/BusinessProfileLinkCallbackPage*"
  - "frontend/apps/dms/src/pages/mail/**"
  - "frontend/apps/dms/src/pages/MailAdministrationPage*"
  - "frontend/apps/dms/src/pages/MailLinkCallbackPage*"
  - "frontend/apps/dms/src/pages/account/AccountMailboxPage*"
---
<!-- Maintainer note. ADR-187, ADR-191, ADR-195, ADR-196, ADR-197, ADR-198, ADR-199, ADR-204, ADR-205,
ADR-209; Dealer Administration D-A-28, D-A-32, D-A-41, FR-A-35, FR-A-40–54. Written by KAN-800 (drift
audit 2026-11-23, N2). Last verified against main@32ad509 (2026-11-25). -->

# Integration: contact mail, messaging, Google Business Profile

The registry, the call log and the sandbox rule are `vehicle-gateway.md`; the send route is Sales'
(`sales.md`). Every provider request here goes through `call_capability`: a call-log row, the breaker.

## Contact mail (KAN-215, ADR-187)
- **Only the dealer's own mailbox, never a Nexotec sender** (A-RULE-9): `resolve_sender` takes the
  author's own connected M365/Google mailbox in the Betrieb, else the Betrieb's shared one, else a
  409 naming both screens (`no_mailbox_connected`, `own_mailbox_in_error`,
  `business_mailbox_in_error`). An author whose own mailbox is in error is told so, never sent from the
  shared one. Identities are `SendingIdentity` rows (a second live one of a kind is 409
  `mailbox_already_connected`); the link round trip (`mail_link`) stores only the `state`'s hash.
- The mail providers are **platform-only** connections (Nexotec's app; 422 `mail_provider_platform_only`
  otherwise). An own mailbox's grant is platform's connected account; its refresh token sits in the
  secrets manager under the grant's id. `send_contact_mail` is not public: nothing above integration
  sends without the consent verdict.

## Messaging (KAN-217, ADR-198)
- SMS through the Betrieb's own Twilio, WhatsApp through its own Meta Cloud API account — tenant-only
  connections (422 `messaging_provider_tenant_only`), one per provider per Betrieb, behind
  `MessagingGateway` (`messaging.send`, not public). None enabled → 409 `messaging_not_connected`.
- Each request is metered under the provider's operation name; the call log keeps the provider's error
  **code** only — never the number or the text. **No retry** (a second attempt may send twice); a
  request refusal (bad number, unapproved template) never charges the breaker.

## `deliver_message` — the one door (ADR-204)
- Every contact message, on every channel, leaves through `integration.public.deliver_message`, which
  takes the verdict the caller read from `contact.public.message_recipient` and refuses without it
  (409 `recipient_refused`; `test_kan217_delivery_takes_contacts_verdict.py`), a channel the type is
  not sent on (422 `channel_not_offered`), and WhatsApp without an approved template (409
  `whatsapp_template_not_approved`; free text never sent). It writes **exactly one
  `integration_message_delivery` row per send** on its own session (ADR-047), failure included, and
  never raises after its refusals. Delivery reports are a later PR (KAN-217 PR-7).

## Google Business Profile (KAN-226)
- **One platform-only provider** (`google_business_profile`): Nexotec's own OAuth client is the one
  platform-scope connection; a location's link is a `GbpLocationBinding` row (pending → active /
  error / disconnected), one live per Nexotec location, a Google location bound once across Betriebe.
  Its refresh token is in the secrets manager under a reference derived from the binding id
  (`secret_ref_for`, slot `refresh_token`) — no `integration_secret_ref` row, never in the app database.
- **Disconnect never revokes at Google** (ADR-195 as amended, KAN-774). A refused grant puts the
  binding in `error`; Google refusing Nexotec's client or project is Nexotec's setup (409
  `business_profile_not_configured`, charged). Staff see the cause on the platform status view, each
  bound Betrieb one item naming nothing (ADR-196); «Erneut prüfen» makes one real request (ADR-197).
- **The push is one way and fact-driven** (ADR-199): `platform.location.updated`,
  `…schedule_changed`, `platform.dealership.updated` and `integration.gbp_location_binding.activated`;
  it reads `platform.public.location_channel_facts`, never the payload, and is debounced by the facts'
  hash (`integration_gbp_push_state.source_hash`). A field Nexotec pushed and now holds empty is
  cleared at Google, the address never. A deactivated location is skipped, never marked closed.
- **Photos** (KAN-784, ADR-209): the worker only queues; the web service uploads (the object store is
  its disk). Upload only, never deleted at Google.
- **Nightly**: the horizon push (KAN-793) and the drift check, which **reports and never repairs**
  (`gbp_drift`, ADR-205) — the two ruled exceptions in the bulk-catalogue guard (`tests-architecture.md`).
- None of these providers has a sandbox; platform-only ones are hidden from the dealer view
  (`platformOnly`, KAN-787).
