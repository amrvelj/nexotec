---
paths:
  - "app/platform/api/notifications.py"
  - "app/platform/api/ui_translations.py"
  - "app/platform/api/user_preferences.py"
  - "app/platform/services/notification*.py"
  - "app/platform/services/push_subscriptions.py"
  - "app/platform/services/web_push.py"
  - "app/platform/services/security_*.py"
  - "app/platform/services/ui_translation.py"
  - "app/platform/services/user_preference.py"
  - "app/platform/models/notification.py"
  - "app/platform/models/ui_translation.py"
  - "app/platform/models/user_preference.py"
  - "app/platform/consumers.py"
  - "app/platform/daily_jobs.py"
  - "app/platform/schemas/notification.py"
  - "app/platform/schemas/ui_translation.py"
  - "app/platform/schemas/user_preference.py"
  - "frontend/apps/dms/src/layout/DmsShell.tsx"
  - "tests/test_*notification*.py"
  - "tests/test_platform_ui_translations.py"
  - "tests/test_user_preference.py"
  - "tests/architecture/test_notif*.py"
  - "tests/architecture/test_notify_*.py"
  - "scripts/import_ui_translation_bundles.py"
  - "frontend/apps/dms/src/pages/Notifications*"
  - "frontend/apps/dms/src/hooks/useNotifications.ts"
  - "frontend/apps/dms/src/utils/webPush.ts"
  - "frontend/apps/dms/public/sw.js"
  - "frontend/apps/dms/src/**/TranslationsPage*"
  - "frontend/apps/dms/src/components/translations/**"
  - "frontend/apps/dms/src/utils/seedDiff*"
  - "frontend/apps/dms/src/i18n/runtimeTranslations*"
---
<!-- Maintainer note (stripped before Claude sees it). Summarises the Dealer Administration PRD's
notifications (D-A-40, FR-AU-26) and UI translations (FR-A-30…32), ADR-047. Cut from platform.md by
KAN-614 (2026-11-10) to the invariants; platform.md was verified against main@73baf3f on
2026-10-25 - this cut was not re-verified against the code. -->

# Notifications and UI translations

## Notifications (D-A-40) — built
- Staff notifications are **in-app and push only, never e-mail** (no notification module reaches the
  mailer: `test_notifications_never_reach_the_mail_port.py`); the user decides everything; the
  `security` family is always immediate and cannot be switched off. No preference row means
  immediate, in-app and push; `in_app` cannot be switched off while a family delivers (422
  `in_app_is_the_record`).
- `notification_event` is the catalogue: the code declaration (`services/notification_catalogue.py`)
  **and** a platform migration inserting its rows, in the PR that raises them
  (`tests/test_migrated_notification_catalogue.py` keeps the two equal). `notification` is the
  inbox and the record, kept 90 days.
- `platform.public.notify(db, code, user_id=…, subject=…)` runs in the caller's transaction and
  publishes `platform.notification.delivered`; an unknown code raises; every call site names a
  literal registered code (`test_notify_call_sites_use_registered_codes.py`).
- **`notify` is platform-internal today** (every caller is the security family). From another
  context it would write platform's tables in that context's transaction (rule 12, ADR-047): the
  first module to raise a notification needs a decided design and an ADR first — its own outbox
  event that platform consumes, or a call on a session of its own with a compensating action —
  classified in `test_adr_047_no_shared_transaction.py`, which refuses an unclassified cross-context
  `notify` by name.
- `digest` holds rows until the daily `platform.notifications.digest`, which publishes one release
  per user filed under no Betrieb (a digest may span sister Betriebe).
- The security family hangs on `auth_events.record` (`services/security_alerts.py`): new-device
  login, factor enrolled or removed, recovery code used, lockout, password reset or replaced, a
  session ended by someone else, connected account revoked, and staff recovery (the action as a
  closed code, never the reason). Connected-account linking and impersonation start/end are in the
  catalogue only — nothing raises them yet.
- **Web Push is a port with no working adapter**: no VAPID key is configured — its home is
  `app/core/config.py`, a preserved file, so adding it is Anto's decision — so
  `GET /v1/me/push/config` says unavailable and nothing is pushed. Pushes carry title + link only,
  and only to a **live** session's subscriptions; a revoked session's subscription is deleted.
  Whether the subscription keys may be stored (`EncryptedString`) is open with Anto — CLAUDE.md
  names the TOTP seed as the only secret.

## UI strings at runtime (FR-A-30…32) — built
- `ui_translation`: namespace = a bundle's top-level object, key = the dotted path below it,
  language, `value`, `seed_value`, `source` (`bundle_seed` / `platform`), version;
  `services/ui_translation.py` is its one writer. `scripts/import_ui_translation_bundles.py` seeds
  `bundle_seed` rows from the bundles (dry run by default; a script, not a migration — the bundles
  live in the frontend tree and keep changing).
- **Only `platform` values are served:** `GET /v1/i18n/{language}` is unauthenticated (the sign-in
  page needs it), answers with `ETag` = the one global revision and `Cache-Control: public,
  no-cache`; a seed row is never served, so a later bundle change is never shadowed by its seeded
  copy. A published value must keep the seed's placeholders (422
  `ui_translation_placeholders_changed`). Writes are `require_write("ui_translations")`, If-Match,
  audited, tenant null. Dealers never override UI strings.
- `POST /v1/i18n/missing-translations` logs keys that rendered the marker (a session, ≤ 50 keys,
  the log capped at 5000 rows).
- The screen is `/platform/translations`; its sources (`components/translations/translationSources.ts`)
  are `ui` and `standardTexts`, each a panel on the shared `FourLanguageEditor` (the AI-prompt seeds,
  [P3-7], join there). The SPA side is `ui-i18n.md`.
