# Workshop terminal authentication — design (ADR-028)

**Status: designed, not built.** The minting path is decided by **ADR-110** (Anto,
2026-11-02, KAN-254): technicians on their own phones sign in like office staff; a shared
workshop device signs in through the one session-minting path, `_mint_session`, in a
terminal mode. This document exists so the eventual build isn't a
retrofit onto a live staff login — ADR-028's own rationale for doing this now. (It
was written against WP-4's OIDC front door; since KAN-196 that login is Nexotec's
own, described under Context.)
Nothing in this file is implemented; there is no `device_terminal` table, no PIN
endpoint, no code path that issues a technician-scoped token. It is prose and a
schema sketch, reviewed for shape now so the actual build (KAN-230)
has fewer open questions.

## Context

Nexotec owns staff authentication (D-A-07; KAN-196 removed WP-4's Zitadel front
door): an office user signs in with e-mail and password at `POST /v1/auth/login`
(`app/platform/api/auth.py`), checked by `LocalPasswordProvider`
(`app/platform/identity/services/identity.py`), with the second factor in the `mfa_*` tables
and the signed-in session in `session`. Shared workshop terminals are a
deliberately **separate, local, device-scoped** realm (D-A-13) — ADR-028's own text
never mentions an IdP, and nothing about the design below routes through the staff
password check or its credential store (D-A-14: realms never share one): the
terminal path issues an `aud=dms` token with `roles=[technician]`, from a device
credential plus a PIN — minted, like every session, by `_mint_session` (ADR-110). Worth stating plainly so a future reader doesn't
wonder why a shop-floor terminal isn't "just another staff login": a technician
standing at a shared terminal with a 4–6 digit PIN is not the same trust model as an office
worker signing into their own browser, and forcing the same flow onto both would
make the terminal case worse without making the office case better.

Reason it exists: technicians will not type an e-mail address, a password and a
second factor with oily hands between jobs. Force them to and they'll share one login,
at which point labour-time data — the entire point of the `technician` role
(Roles & Permissions RP-4) — becomes worthless. Because the device itself is
already authenticated (see below), the PIN only has to be a **second factor**, not
a password, so 4–6 digits is an accepted tradeoff, not a shortcut.

## Two ways a technician signs in (ADR-110)

**Decided (Anto, 2026-11-02, KAN-254: «most technicians will use mobile»):**

- **Their own phone.** A technician on a device of their own signs in through the
  ordinary staff sign-in — their own e-mail, password and, where the Betrieb enforces it,
  second factor — and gets their own roles, like any office user. No terminal mode, no PIN,
  no device credential is involved; nothing new is needed beyond a mobile-usable SPA.
- **A shared workshop device** (a phone, tablet or terminal passed between technicians):
  the realm this document designs. The device authenticates itself first, then the
  technician's PIN or badge, and the session is minted by `_mint_session` in its
  `terminal` realm (below).

The rest of this document is about the shared device.

## Device credential provisioning

A physical terminal is provisioned once, by an admin, against a specific
dealership. The device holds a long-lived credential — **a reference into the
secrets manager, never the credential material itself, and never a row in the
application database** (the same "no secrets in the app database, only
references" rule as `Settings.jwt_private_key`/`tax_id_encryption_key` today).
Provisioning is a deliberately manual, low-frequency action (workshops don't
acquire new shared terminals often), not something that needs a self-service flow.

## The PIN/badge verification flow

Two steps, in order — the device authenticates itself before any technician does:

1. **Device authentication.** The terminal presents its own credential on every
   request (or once per local session) to prove "this is a real terminal
   provisioned for this dealership," resolved against the secrets-manager
   reference above. This step alone grants no access to anything — it only
   establishes which dealership's technicians may sign in at this device.
2. **Technician PIN/badge.** Once the device is authenticated, the technician
   supplies a short PIN or taps an NFC badge, checked against a per-technician
   record scoped to **that device's own dealership** — a technician's PIN from
   one dealership must never authenticate them at a terminal belonging to
   another, even within the same group. This is the second factor; because step 1
   already proved device+dealership, a 4–6 digit PIN is an acceptable second
   factor here in a way it would not be as a sole credential.

## Never a manager, by construction

**Decided by ADR-110 (KAN-254, option A):** the terminal session is minted by the
same `_mint_session` (`app/platform/api/auth.py`) every staff sign-in passes
through, called with the realm `terminal` (the office's is `staff`). In the
terminal realm it:

- applies the **same user-status, access-window and membership checks** as an
  office sign-in — a technician suspended or deactivated in Dealer Administration,
  or outside their access window, is refused (401) at the terminal exactly as at
  the office (Auth & Identity risk 4, FR-A-10). A check added to `_mint_session`
  later reaches the terminal without anyone remembering to add it;
- writes a `session` row with the terminal's 15-minute idle window (below);
- mints `roles=frozenset({AccessRole.TECHNICIAN})`, `is_dealer_manager=False` and
  `memberships=[the device's Betrieb]` — **not** whatever roles or manager flag the
  matched person's own `User` row holds, and not a filter applied after the fact. If
  the same person is a manager in the office, the terminal still never produces a
  token carrying that authority. (Whether a terminal session may switch Betrieb is
  not decided by ADR-110: switch-dealership authorises from `dealership_membership`
  rows, never the token's `memberships` claim — KAN-230 settles it.)

The fixed role set is set by the realm inside `_mint_session`, not by a business
rule someone has to remember at another call site. Rejected (ADR-110): a second
minting function sharing one status check (two places to keep aligned), and the
original design's direct `create_access_token()` call (a second minting path that
skips the checks and writes no session row).

## Session scope: 15-minute idle timeout

**Decided (Anto, 2026-10-31, KAN-508 option A): the terminal session is a row
in the same `session` table the staff sign-in writes, judged by the same
guard, with an idle window of its own.** Since KAN-196 every staff session is a
`session` row (`app/platform/identity/services/auth_sessions.py`), and the app-level
dependency `app/platform/session_guard.py::enforce_live_session` refuses a
token whose row is revoked, idle (`session_idle_minutes`, 120 for the office)
or past its absolute end, touching the row at most once a minute. The terminal
reuses that, nothing new:

- **A terminal session's row carries a 15-minute idle window** instead of the
  office's 120 minutes — a per-realm setting on the row or beside
  `session_idle_minutes`, chosen when this is built; the guard reads it the
  same way. The absolute end stays the office's (next midnight Europe/Zurich).
- **Deactivating or suspending a technician ends their terminal session
  exactly like an office session**: the status change revokes every `session`
  row of the user (FR-A-10), so there is no window in which a deactivated
  technician still works at a terminal, and session validity is judged in one
  place.
- The previous options — (a) a short fixed-TTL token refreshed by re-tapping
  the badge, (b) a revocable session record as "a new table" — predate that
  table; (a) is not taken, because its sessions could not be revoked when
  someone is deactivated.

How the terminal path *mints* that session is ADR-110: through `_mint_session`
in its `terminal` realm (above), which writes this row with the 15-minute window.

## Data model (prose sketch — explicitly not a migration)

Two tables, shaped roughly like this. Not created here; sketched so the shape is
reviewed before someone has to invent it under deadline pressure.

- **`device_terminal`** — `id`, `dealership_id` (the tenant this device belongs
  to), `label` (human-readable, e.g. "Bay 3 terminal"), a secrets-manager
  reference column (never the credential itself), `status`.
- **`device_technician_pin`** — `id`, `user_id` (the technician), `dealership_id`
  (redundant with `user_id`'s own tenant, kept explicit for the same reason
  `CustomerPhone`/`CustomerEmail` denormalize `group_id` rather than joining
  through their parent — a lookup on login should not need a join to prove tenant
  scope), `pin_hash`, `failed_attempts`, `locked_until` — the same lockout shape
  as the staff password store, `credential` (`failed_login_attempts`,
  `locked_until`; KAN-196), in a table of its own because the realms never share
  a credential store (D-A-14). A short PIN needs that brute-force protection even
  though the device has already authenticated itself.

## Bolt-on point

What this reuses from the staff login is the one session-minting path,
`_mint_session` (`app/platform/api/auth.py`), in its `terminal` realm (ADR-110) —
and through it the session layer's token, `create_access_token()`, which already
takes every parameter needed. `app/core/auth.py` is untouched: the preserved-file
guarantee (`get_current_principal`'s contract) holds for this future work as it
did when WP-4's Zitadel front door issued the staff session. This design's whole
job is "arrive at a `user_id` from a device credential plus a PIN, then hand it to
`_mint_session` with the realm `terminal`." Net-new work: a PIN/badge service
**parallel to**, not a modification of, the staff identity service
(`app/platform/identity/services/identity.py`, `LocalPasswordProvider`); the two
device-model tables above; the realm parameter on `_mint_session` and the
terminal's idle window on the `session` row. Nothing about the office sign-in's
own behaviour changes — its realm is `staff` — which is the reason ADR-028 asked
for this design to exist now rather than being discovered as a retrofit problem
later. The build is KAN-230, with tests that a deactivated technician's PIN is
refused and that a manager signing in at a terminal gets technician rights only.
