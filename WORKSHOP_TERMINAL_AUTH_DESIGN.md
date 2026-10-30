# Workshop terminal authentication — design (ADR-028)

**Status: designed, not built.** This document exists so the eventual build isn't a
retrofit onto a live staff login — ADR-028's own rationale for doing this now. (It
was written against WP-4's OIDC front door; since KAN-196 that login is Nexotec's
own, described under Context.)
Nothing in this file is implemented; there is no `device_terminal` table, no PIN
endpoint, no code path that issues a technician-scoped token. It is prose and a
schema sketch, reviewed for shape now so the actual build (a future work package)
has fewer open questions.

## Context

Nexotec owns staff authentication (D-A-07; KAN-196 removed WP-4's Zitadel front
door): an office user signs in with e-mail and password at `POST /v1/auth/login`
(`app/platform/api/auth.py`), checked by `LocalPasswordProvider`
(`app/platform/services/identity.py`), with the second factor in the `mfa_*` tables
and the signed-in session in `session`. Shared workshop terminals are a
deliberately **separate, local, device-scoped** realm (D-A-13) — ADR-028's own text
never mentions an IdP, and nothing about the design below routes through the staff
login or its credential store (D-A-14: realms never share one): the terminal
path issues an `aud=dms` token with `roles=[technician]`, from a device credential
plus a PIN. Worth stating plainly so a future reader doesn't
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

The resulting session is hardcoded to `roles=frozenset({AccessRole.TECHNICIAN})`,
`is_dealer_manager=False` on the `create_access_token()` call — **not** whatever
roles the matched person's own `User` row otherwise holds, and not a filter
applied after the fact. If the same person also has a manager account for normal
office use, the terminal path must never be able to produce a token carrying that
authority. This is a hardcoded parameter at the call site, not a business rule
that has to be remembered and re-checked elsewhere.

## Session scope: 15-minute idle timeout

Documenting the real gap rather than assuming an answer: `create_access_token`
today issues a pure fixed-TTL JWT with no concept of "idle" — a token is valid
until `exp`, full stop, regardless of activity. Two ways to get a genuine
15-minute *idle* timeout when this is built:

- **(a) Short fixed TTL, refreshed by re-authenticating.** Issue the token with a
  short TTL approximating the idle window, and have the terminal UI silently
  re-run the PIN/badge check on the next interaction if the token has expired.
  Reuses the existing stateless token model completely unchanged — no new
  server-side state. **Recommended starting point** — simplest, and "the
  technician re-taps their badge every so often" is an acceptable UX cost for a
  shared terminal in a way it would not be for an office worker.
- **(b) Real idle tracking.** A revocable session record (last-activity
  timestamp), checked alongside the JWT's own `exp` on every request. Genuinely
  correct idle-timeout semantics, but a materially bigger lift — a new table, a
  write on every authenticated request (or a cheaper approximation of one), and
  a second place session validity can fail. Only worth it if (a) proves
  insufficient in practice.

Neither is implemented. This is a decision for whoever builds this package, not
one this design makes on their behalf — recorded here so it doesn't get
re-litigated from scratch.

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

The only thing this reuses from the staff login is the session layer's token,
`create_access_token()` — the same preserved-file guarantee
(`app/core/auth.py::get_current_principal`'s contract) holds for this future work
as it did when WP-4's Zitadel front door issued the staff session: this design's
whole job is "arrive at a `user_id` + a hardcoded `{TECHNICIAN}` role set, then
mint a token with it." Net-new work is entirely additive: a PIN/badge service
**parallel to**, not a modification of, the staff identity service
(`app/platform/services/identity.py`, `LocalPasswordProvider`); the two
device-model tables above; and whichever idle-timeout mechanism is chosen. Nothing
about the staff login itself needs to change to make room for this, which is the
entire reason ADR-028 asked for this design to exist now rather than being
discovered as a retrofit problem later. Open (KAN-254): every staff token is
minted through `_mint_session` (`app/platform/api/auth.py`), which checks the
user's status and writes the session row; how the terminal path gets the same
status check needs an ADR.
