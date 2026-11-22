---
paths:
  - "app/platform/administration/**"
  - "app/platform/daily_jobs.py"
  - "app/platform/organisation/services/group_read_dealerships.py"
  - "app/platform/identity/*/onboarding.py"
  - "app/platform/identity/*/legal*.py"
  - "app/core/setup_checklist.py"
  - "app/core/status.py"
  - "app/status_registry.py"
  - "tests/platform/test_kan214_*.py"
  - "tests/platform/test_kan223_status_page.py"
  - "tests/platform/test_kan225_publishing_profile.py"
  - "tests/platform/test_kan716_*.py"
  - "tests/platform/test_data_export.py"
  - "tests/platform/test_offboarding.py"
  - "tests/platform/test_platform_onboarding.py"
  - "tests/cross_context/test_kan218_*.py"
  - "tests/cross_context/test_data_export_completeness.py"
  - "tests/platform/test_configuration_copy.py"
  - "tests/setup_checklist_support.py"
  - "tests/data_export_support.py"
  - "frontend/apps/dms/src/pages/OnboardingPage*"
  - "frontend/apps/dms/src/pages/Setup*"
  - "frontend/apps/dms/src/pages/PublishingPage*"
  - "frontend/apps/dms/src/pages/DataExportPage*"
  - "frontend/apps/dms/src/pages/PlatformOffboardingPage*"
  - "frontend/apps/dms/src/pages/LegalAcceptancePage.tsx"
  - "frontend/apps/dms/src/pages/PlatformLegalDocumentsPage*"
  - "frontend/apps/dms/src/pages/StatusPage*"
  - "frontend/apps/dms/src/pages/PlatformStatusPage*"
  - "frontend/apps/dms/src/hooks/useStatusPage.ts"
  - "frontend/apps/dms/src/components/business/CopyConfigurationSection*"
  - "frontend/apps/dms/src/components/publishing/**"
  - "frontend/apps/dms/src/components/dataExport/**"
  - "frontend/apps/dms/src/components/setup/**"
---
<!-- Maintainer note. Dealer Administration D-A-01, D-A-06, D-A-21, D-A-34, D-A-38; ADR-156, ADR-165,
ADR-174, ADR-175, ADR-180. Split out of platform-admin.md (and the status page out of
platform-notifications.md) by KAN-751, 2026-11-18, for platform's `administration` sub-package
(ADR-163) plus onboarding and the legal documents. Last verified against main@3b2bf69 (2026-11-18). -->

# A Betrieb's lifecycle: onboarding, activation, copy, export, offboarding, status

- **Onboarding (D-A-06) — built (KAN-210):** staff enter group, legal entity (UID unique),
  entitlements and one manager; **Nexotec sets nothing commercial** — no VAT rate, no bank details.
  `POST /v1/platform/onboarding` (`identity/services/onboarding.py`; staff with a factor) runs the
  create services with `commit=False` in one transaction — group, `create_dealership` (seat
  location, number ranges, settings), entitlements, the first manager invited, one
  `platform.dealership.onboarded` row — 409 `uid_already_onboarded`; texts, template and MFA-on
  come from the platform defaults. Screen: `/platform/onboarding` (five steps).
  `POST /v1/dealerships` is deprecated in OpenAPI.
- **Activation gate (D-A-21) — built (KAN-214):** `pending_onboarding` until every applicable
  item of the derived checklist (`administration/services/setup_checklist.py`, no stored ticks) is
  done and the manager accepted the current ToS and DPA (D-A-34); `POST /v1/dealerships/{id}/activation`
  freezes the ranges, sets `active` + `activated_at` and publishes `platform.dealership.activated`.
  Until then `session_guard` 404s every non-administration route. After activation a missing item
  blocks only its own action (`platform.public.require_setup_item_for`) and the nightly job raises
  `administration.checklist_item_regressed` — never for a Betrieb the migration set active
  (`activated_at` null). Production never asks for or activates on a draft (< 1.0) ToS/DPA.
- **Publishing profile — built (KAN-225):** `administration/services/publishing_profile.py`,
  `/administration/publishing`; the public stock projection is `reporting`'s (ADR-174, ADR-175).
- **Copy configuration from a sister Betrieb** (D-A-01, FR-A-01; ADR-156) — built (KAN-220): a
  manager of both Betriebe copies a chosen subset once (`POST /v1/dealerships/{id}/copy-configuration`:
  standard texts, the template's header notes, number-range patterns, contact groups; a dry run
  lists exactly what the commit does), each row through its own write path, audited as one
  `configuration_copied` row on the target — never inheritance, no link to the source.

## Data export and offboarding (KAN-218)
- The manager's export is a job (`export_job`; the web writes the ZIP), downloadable 7 days via
  the authenticated GET (signed URL: KAN-402).
- Staff offboard (`POST /v1/platform/dealerships/{id}/offboard`) only after an acknowledged export
  newer than the last write and than its id floor (KAN-716, `test_kan716_*`); the Betrieb's
  principals then get 404 (`session_guard.py`).
  Archive exports: staff only, with a reason. It then leaves every group read
  (`group_read_dealerships.py`, `group_labels.py`; Q2 A).
- `platform.dealership.offboarded` (Q3 A): Stock withdraws live listings (empty feed first,
  `listing_withdrawal.py`), stating `inventory.dealership_listings.withdrawn`; Integration
  disables and revokes every connection, the `marketplace_publish` ones only on it.
- **No automatic deletion** (Anto, 2026-11-14): past `retention_until` (offboarding + 10 years)
  reconciliation reports it nightly (`dealership.archive_due_for_manual_deletion`); staff delete it.

## Status page (D-A-38) — built (KAN-223)
`GET /v1/dealerships/{id}/status`, derived on read (`administration/services/status_page.py`;
every role reads, 404 cross-tenant): number ranges (warning 90 %, blocked 100 %), Nexotec's
`platform_status_incident` (staff, `/platform/status`) and contributions through
`app/core/status.py`, registered in `app/status_registry.py` (integration's connections; Google
Business Profile, KAN-776: one item for a Betrieb with a live binding, blocked while Nexotec's client
is interrupted — no client, breaker open, or a client/project refusal as its latest call — no link,
no cause, its own notification `administration.business_profile_interrupted` («nothing to do on your
side»); staff read the cause on `/platform/status` (`GET /v1/integrations/business-profile/client-status`) and
«Erneut prüfen» (`POST …/client-status/check`, one real Google call, staff with a factor) clears it once
Google answers for a location; a timeout, 5xx, quota or token-refresh row, or a 429 or 401/403 not naming the
client or project (KAN-780), neither raises nor clears it; drift, KAN-226/ADR-205: one item per checked
binding, warning on drift or a failed read). The daily
`platform.status_page.notifications` raises `administration.status_item_degraded` once per
degradation (`status_item_state`, under a per-Betrieb advisory lock): a warning the source really
announced is exempt, the move to blocked never; an unavailable source keeps its rows, its own
blocked item announced once (ADR-180).
