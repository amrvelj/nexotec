---
paths:
  - "app/core/auth*.py"
  - "app/core/nul_guard.py"
  - "app/core/config.py"
  - "app/main.py"
  - "app/platform/consumers.py"
  - "app/platform/schemas/auth.py"
  - "app/platform/schemas/recovery.py"
  - "frontend/apps/dms/src/components/ProtectedRoute.tsx"
  - "app/platform/session_guard.py"
  - "app/platform/api/auth.py"
  - "app/platform/api/recovery.py"
  - "app/platform/services/identity.py"
  - "app/platform/services/auth_*.py"
  - "app/platform/services/mfa.py"
  - "app/platform/services/password_*.py"
  - "app/platform/services/recovery.py"
  - "app/platform/services/mailer.py"
  - "app/platform/services/mail_texts.py"
  - "app/platform/services/connected_account.py"
  - "app/platform/models/auth_*.py"
  - "app/platform/models/credential.py"
  - "app/platform/models/mfa.py"
  - "app/platform/models/connected_account.py"
  - "app/platform/models/dealership_auth_config.py"
  - "tests/test_auth.py"
  - "tests/test_login.py"
  - "tests/test_identity_*.py"
  - "tests/identity_support.py"
  - "tests/test_platform_account_lock_order.py"
  - "tests/test_platform_recovery.py"
  - "tests/test_platform_connected_account_provider_revocation.py"
  - "tests/architecture/test_no_password_credential_storage.py"
  - "tests/architecture/test_connected_account_is_never_an_identity.py"
  - "tests/architecture/test_recovery_never_touches_a_password.py"
  - "tests/architecture/test_token_minting_passes_entitlements.py"
  - "frontend/apps/dms/src/auth/**"
  - "frontend/apps/dms/src/pages/Auth*"
  - "frontend/apps/dms/src/pages/auth/**"
  - "frontend/apps/dms/src/pages/InvitationAcceptPage*"
  - "frontend/apps/dms/src/pages/PasswordReset*"
  - "frontend/apps/dms/src/**/*ogin*"
  - "frontend/apps/dms/src/components/recovery/**"
  - "scripts/benchmark_argon2.py"
  - "WORKSHOP_TERMINAL_AUTH_DESIGN.md"
---
<!-- Maintainer note. Auth & Identity PRD v1.1; D-A-07…D-A-37; ADR-028/099/110/118. Cut from platform.md
(verified against main@73baf3f, 2026-10-25) by KAN-614, 2026-11-10; the cut was not re-verified. -->

# Identity and sign-in (Authentication & Identity; D-A-07…D-A-16, D-A-36)

## One minting path
- Every session token is minted by `_mint_session` (`app/platform/api/auth.py`): login, the MFA
  step, invitation acceptance, switch-dealership. It refuses a suspended or deactivated user (401),
  authorises the target Betrieb from the home dealership plus `dealership_membership` rows — never
  the token's `memberships` claim (403) — and writes the `session` row the guard checks. No second
  minting path, no direct `create_access_token` call (Auth & Identity risk 4). Every production
  token carries an explicit entitlement set (`test_token_minting_passes_entitlements.py`).
- The workshop terminal (ADR-110, not built — KAN-230, `WORKSHOP_TERMINAL_AUTH_DESIGN.md`) mints
  through it with realm `terminal`: the same checks, a 15-minute idle window, `roles={TECHNICIAN}`,
  no manager flag, the device's Betrieb as the only membership — never the person's office roles.
- `get_current_principal` does not check `status`: a deactivated user's session ends because the
  status change revokes its `session` row. A token without `sid` (tests, bootstrap) is not covered.

## Login
- Nexotec owns authentication (D-A-07). `LocalPasswordProvider` behind the `IdentityProvider` seam
  is the only implementation (D-A-08, D-A-11; Entra OIDC is [P3]).
- **Every refusal is the same 401 body with the same argon2 work** (a dummy verify for an unknown
  address) — never a reason-specific message (`tests/test_identity_login_policy.py` counts the
  verifies). A NUL in path, query or JSON/form body is 422 `nul_character` before any route
  (`app/core/nul_guard.py`: refuse, never strip) — at the login door it is the same 401.
- Per-account backoff from the third failure, soft lockout after `auth_lockout_failures` (a reset
  clears it); per-IP backoff on the **last** X-Forwarded-For entry, never the first. Wrong
  second-factor answers count on the account axis and per challenge, never per IP.
- **Lock order: user row → token → factor → credential, on every path.** Every login step locks the
  user first (`identity.lock_user_for_login`) and judges password, throttle and status under the
  lock; the front door (invitation acceptance, reset confirm) locks the user before it consumes its
  token; the account's MFA routes take `user_service.get_own_user_for_update_or_404`, staff
  recovery `recovery.get_user_for_recovery_or_404`, the reset request `lock_user_for_login` (an
  address changed meanwhile is answered like an unknown one). `tests/test_platform_account_lock_order.py`.
  A single-row write (logout, revoking one's own session, the guard's touch) takes no user lock.
- `credential` holds the hash and the last five; **no other column may be password-shaped**
  (`test_no_password_credential_storage.py`; the terminal PIN's `device_technician_pin` is the one
  other allowed table, a `pin` column counts). Nobody sets another person's password (FR-AU-10):
  only a single-use invitation or reset token (stored as SHA-256); the staging seed's local path is
  the one bootstrap exception.
- Password policy: ≥ 12 characters, no composition rules; the Betrieb's and the user's names and the
  e-mail local part refused; Have I Been Pwned at set-time **fails open and flags**
  (`breach_check_pending`; tests: `tests.md`).
- argon2id parameters are settings (`DMS_ARGON2_*`; target ~250 ms a verify on the login instance,
  `scripts/benchmark_argon2.py`); a hash below them is rehashed on the next login.

## Sessions
- One `session` row per login. `enforce_live_session` (app-level in `app/main.py`) 401s a revoked,
  idle or expired row — the absolute end is **the next midnight Europe/Zurich** — and touches the row at most once a minute, never for `X-DMS-Background`
  requests (an unattended tab still idles out). Logout is the one route a dead session reaches.
- Every session of the user is revoked by: suspension or deactivation, a change of roles, manager
  flag or membership (the claims live for the day), a password reset, an accepted invitation,
  staff `revoke-sessions` — each a `session_revoked` event.
- KAN-229: `dealership_auth_config.session_*` (15–120 min, 1–12 h) shorten it; not staff.
- The reset request is 202 whether or not the address exists, its mail sent after the response.

## MFA (D-A-36)
- TOTP (a code good once, ±1 step) with ten hashed single-use recovery codes; passkeys (resident
  keys; the challenge derived from a single-use `auth_token`, never stored). The TOTP seed is the
  one secret in the database (`EncryptedString`, ADR-099).
- `dealership_auth_config.mfa_enforced` (default **on**, the manager's switch) decides per Betrieb;
  **platform staff are always enforced** — `require_mfa_session()` refuses a staff principal whose
  token's `mfa` claim is false (the recovery door has its own gate; the impersonation endpoints
  will carry it).
- A session that owes a factor reaches only `/v1/auth/*`, `/v1/me/preferences` and its own
  Betrieb's `auth-config` (never a staff session); the rest of `/v1/` is 403
  `details.mfaEnrolmentRequired`, and the SPA sends it to `/account/security`. The guard reads the
  factor from the **session row**, so the enrolling session is free at once; the token's `mfa`
  claim changes only at the next sign-in or switch.
- Five answers per challenge, spent on success only; every wrong answer counts on the account
  axis like a wrong password (a lock reached so is a `locked_out` event), the password step does not
  reset that counter and the MFA step honours the lock — fresh challenges buy nothing. A session that never proved a factor ends once its user holds one;
  removing the last factor ends the user's other sessions; staff's reset ends them all and always
  writes `mfa_removed` naming the actor (FR-AU-12); a user removing their own never-confirmed
  enrolment ends no session and writes none.
- Passkeys are verified by the `webauthn` library over `mfa.PASSKEY_ALGORITHMS`; a builtin
  exception it raises at registration is 422 `passkey_not_verified`; `mfa.py`'s key policy (its
  comments say which check is the spec's and which Nexotec's) refuses weak or malformed keys. The
  suite runs a software ceremony only — never a hardware authenticator or a browser.

## Account recovery (D-A-16, FR-AU-25…27)
- A `platform_admin` recovers alone, through one door: `/v1/platform/users/{id}/recovery/…`
  (reissue-invitation, reset-mfa, clear-lockout, revoke-sessions, change-email) and
  `/v1/platform/dealerships/{id}/recovery/grant-manager`, behind `require_staff_recovery_session`
  (staff, a factor in the session, never an impersonation — `Principal.acting_for`). Each needs a
  **reason** and writes its own `recovery_*` auth event (the Betrieb's manager reads it), an audit
  row and a `security` notice to the user. Staff never recover their own account (403) nor a
  deactivated one (409); every refusal of the service carries a stable `details.rule`. Grant-manager
  takes an active home user at **any** time, not only after the last manager left. The screen
  (Administration › Account recovery) is never role-hidden.
- Nothing in recovery sets or reads a password (`test_recovery_never_touches_a_password.py`), and
  no `/v1/platform/` route carries a password-shaped field.
- **The e-mail address is the sign-in and recovery channel.** A manager corrects it only while the
  user is `invited` (open tokens and sessions end, the old address is mailed); an accepted
  account's address changes only through recovery's `change-email` (the PATCH answers 403 with
  `details.recovery`). An account never returns to `invited` by PATCH; re-inviting an accepted
  account is 403. Open with Anto: the new address is not confirmed before it takes over. No step-up
  re-authentication (A-12).

## Platform mail (D-A-28)
Invitations, resets and alerts leave through Nexotec's own sender (`PlatformMailer`): `logging`
(the link in the log; refused at startup outside `development`, ADR-099) or `smtp`
(`DMS_PLATFORM_MAIL_*`, verifying STARTTLS). A failed delivery is a `mail_delivery_failed` auth
event and an ERROR line. Texts in the user's UI language, else the home Betrieb's correspondence
language (`user_preference.ui_language_of`).

## Auth events (FR-AU-21)
Every login outcome, lock, token, factor and session change, read by the manager at
`GET …/auth-events` (`audit_logs`). Never a password, hash or token in `details`.

## Connected accounts are grants, not identities (FR-AU-23)
- Never used to sign in, never in the token (`test_connected_account_is_never_an_identity.py`); the
  row holds a `secret_ref`, never a token, never returned; `services/connected_account.py` alone
  writes it. The manager's read of a user's grants is provider, scopes and status only — never the
  address or the usage stamps.
- Every revoke (the user's, the manager's, deactivation's) keeps the row, audits and publishes
  `platform.connected_account.revoked` in the same transaction. Ending the grant at the provider is
  the consumer `platform.connected_account_provider_revocation` (ADR-118): a revoker registered with
  `platform.public.register_grant_revoker` **in the worker's composition root** (`app/worker.py`;
  the consumer runs only there), given plain `RevokedGrant` data; it must be idempotent, raise on a
  refusal, and never put token material in its exception text. None is registered yet: then the
  consumer records nothing (WARNING) — the grant ends in Nexotec only. An outage longer than the
  outbox's retries dead-letters it. Reconciliation reports a revoked grant holding a reference after
  24 h, **whatever its provider**; the daily `platform.connected_account.repair` (KAN-559) retries it.

## Realms (D-A-13, D-A-14)
staff (this) · terminal (ADR-028, ADR-110) · customer (reserved, `aud=portal`, never a `user`
row) · machine (not in v1). Never share a credential store.
