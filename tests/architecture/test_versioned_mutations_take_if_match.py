"""Every PATCH, PUT and DELETE takes If-Match (CLAUDE.md, API conventions:
"If-Match on every mutation of a versioned entity"; KAN-396).

A route that mutates a `VersionedMixin` entity without `require_if_match`
lets two saves from the same read both land — the second silently overwrites
the first. KAN-396 found three (stock-item options PUT, listing-text PATCH,
connection DELETE). The guard is by method: a PATCH/PUT/DELETE must depend on
`require_if_match`, unless it is listed below with the model it writes, and
that model must not be versioned — so an entry cannot excuse a versioned
entity, and a model that later gains a version fails its entry.

A POST on an existing entity (a path parameter: confirm, cancel, publish,
a child row, a copy, …) must take `require_if_match` too, unless it is listed
in POST_WITHOUT_IF_MATCH with why. A POST that creates a top-level record has
no version to match and is not walked. The POST list is declared by hand —
which rows a route writes cannot be read off the route — so each entry says
what it writes. Nine change a versioned row without If-Match, each by design and
each saying why: the hand-over, the system's catalogue fill, a connection
test's health stamp, a new bank account that takes the default, and the five
state transitions KAN-428 named — publish, unpublish, reserve, release and
enable group read — which Anto ruled stay without If-Match (2026-11-03,
KAN-428 option C; its sixth, the legacy custody event, went with the legacy
vehicle table, KAN-79). An entry states a reason,
never an open question: `test_every_exemption_states_a_ruled_reason` refuses
one that says a decision is pending.
"""

from app.core.base import VersionedMixin
from app.core.concurrency import require_if_match
from app.customer.models.customer import CustomerAddress, CustomerEmail, CustomerExternalId, CustomerPhone
from app.customer.models.vehicle_party import VehicleParty
from app.integration.models.secret_ref import IntegrationSecretRef
from app.inventory.models.stock_item_publishing import StockItemMedia
from app.main import app
from app.platform.models.auth_session import AuthSession
from app.platform.models.mfa import MfaPasskey, MfaTotp
from app.platform.models.notification import Notification, PushSubscription
from app.platform.models.user_preference import UserPreference
from app.vehicle.models.vehicle_history import VehicleAccessory
from tests.route_walk import api_routes

MUTATING = {"PATCH", "PUT", "DELETE"}

_NEW_ROW = "creates a new row; no existing versioned row changes"
_CHILD_ROW = "creates an unversioned child row; the parent's version does not move"
# KAN-428 (Anto, 2026-11-03, option C): five transitions (six until KAN-79) that bump a version
# stay without If-Match, each guarded by its own state rule instead.
_KAN_428 = "no If-Match by Anto's ruling (2026-11-03, KAN-428 option C): "

# path -> why the POST on an existing entity takes no If-Match.
POST_WITHOUT_IF_MATCH = {
    "/v1/catalogue/variants/{model_variant_id}/details/reload": "KAN-211 (ADR-123), «Neu laden»: what changes is "
    "the calling Betrieb's detail rows, replaced wholesale, and they carry no version; the shared variant row is "
    "only ever filled where it is NULL (KAN-83, a bump no client holds a version for) — one fresh request per "
    "detail, audited",
    "/v1/dealerships/{dealership_id}/users": _NEW_ROW,
    "/v1/dealerships/{dealership_id}/users/{user_id}/ownership/transfers": "the hand-over (D-A-35) bumps every "
    "moved offer's, contract's and customer's version, judged again under each row's lock — no single version "
    "to match",
    "/v1/dealerships/{dealership_id}/users/{user_id}/memberships": "a membership row; the user row is locked first "
    "and a second first grant is a 409 (KAN-380)",
    "/v1/dealerships/{dealership_id}/users/{user_id}/invitations": "a single-use token and the user's invited_at "
    "stamp (a timestamp, not an edit; the version does not move)",
    "/v1/dealer-groups/{dealer_group_id}/enable-group-read": _KAN_428 + "a platform admin's one-way switch "
    "(ADR-030) that only ever sets group read on, refused without a recorded legal basis; a repeat sets the same "
    "value again (it still bumps the version)",
    "/v1/platform/users/{user_id}/recovery/{action}": "tokens, factors, the lockout counter, sessions or the "
    "invited_at stamp — never a field a manager edits; change-email and grant-manager, which do, take If-Match",
    "/v1/reference-data/{list_code}": _NEW_ROW,
    "/v1/dealerships/{dealership_id}/bank-accounts": "a new bank-account row (KAN-203); with isDefault the previous "
    "default is demoted under the dealership row lock — a row the caller did not name, audited as its own update, "
    "so no If-Match applies to it (D-A-27; Anto's ruling 2026-10-24: the default is chosen explicitly and moves)",
    "/v1/dealerships/{dealership_id}/locations": _NEW_ROW,
    "/v1/dealerships/{dealership_id}/locations/{location_id}/closures": "a new closure row (KAN-204), versioned "
    "itself; the location's version does not move",
    "/v1/dealerships/{dealership_id}/standard-texts/{category}": "a new standard-text row (KAN-208); with isDefault "
    "the slot's previous default of the same Betrieb, scope and language is demoted in the same transaction — a row "
    "the caller did not name, its version bumped and audited as its own update (the bank account's precedent)",
    "/v1/dealerships/{dealership_id}/standard-text-previews": "the editor's preview (KAN-208): renders a body on "
    "sample data and writes nothing",
    "/v1/dealerships/{dealership_id}/number-range-previews": "the edit dialog's live example (KAN-207): renders "
    "the next number of an unsaved pattern and writes nothing",
    "/v1/configurations/{configuration_id}/copy": _NEW_ROW,
    "/v1/vehicle-mdm/mapping-gaps/{gap_id}/resolve": "the system's catalogue fill (KAN-240) bumps the filled "
    "configurations' versions — only NULL fields, never a value a user set",
    "/v1/vehicle-mdm/{vehicle_id}/allocate": _CHILD_ROW,
    "/v1/vehicle-mdm/{vehicle_id}/odometer-readings": _CHILD_ROW,
    "/v1/vehicle-mdm/{vehicle_id}/plates": "a new plate assignment (KAN-467), versioned itself; the other "
    "cars' assignments a move or a Wechselschild changes carry their version in the body (moveFrom / "
    "wechselschildWith: plateId + version, the If-Match of each row), checked under the plate's lock",
    "/v1/vehicle-mdm/{vehicle_id}/accessories": _CHILD_ROW,
    "/v1/customers/{customer_id}/phones": _CHILD_ROW,
    "/v1/customers/{customer_id}/emails": _CHILD_ROW,
    "/v1/customers/{customer_id}/addresses": _CHILD_ROW,
    "/v1/customers/{customer_id}/external-ids": _CHILD_ROW,
    "/v1/customers/{customer_id}/vehicles": _CHILD_ROW,
    "/v1/customers/{customer_id}/legal-basis": _CHILD_ROW,
    "/v1/inventory/stock-items/{stock_item_id}/reservations": _KAN_428 + "a car already held refuses a second "
    "hold (409); platform staff only (KAN-376) — Sales reserves through its own internal call",
    "/v1/inventory/reservations/{reservation_id}/release": _KAN_428 + "names the reservation it ends, so it "
    "cannot end a later one; platform staff only (KAN-376)",
    "/v1/inventory/stock-items/{stock_item_id}/ledger-entries": _CHILD_ROW,
    "/v1/inventory/stock-items/{stock_item_id}/publishing/{channel}/publish": _KAN_428 + "a state switch with "
    "the same result whoever sends it, refused while a blocking condition holds; its version bump makes another "
    "tab's next listing-text save a stale 409, which the ruling accepts",
    "/v1/inventory/stock-items/{stock_item_id}/publishing/{channel}/unpublish": _KAN_428 + "a state switch that "
    "needs confirm=true (it deletes the listing at the marketplace); same version effect as publish",
    "/v1/inventory/stock-items/{stock_item_id}/media": _CHILD_ROW,
    "/v1/inventory/stock-items/{stock_item_id}/media/reorder": "the photos' positions (unversioned rows)",
    "/v1/sales/offers/{offer_id}/copy": _NEW_ROW,
    "/v1/sales/offers/{offer_id}/documents": "a new document and the grid's deal row (unversioned)",
    "/v1/sales/contracts/{contract_id}/documents": "a new document and the grid's deal row (unversioned)",
    "/v1/integrations/connections/{connection_id}/test": "a provider call, its call-log row and the connection's "
    "health stamp (status, last error, last verified) — the gateway's own observation, not an edit; the version "
    "does not move",
}

# (method, path) -> (the model the route writes, why it takes no If-Match).
UNVERSIONED = {
    ("DELETE", "/v1/vehicle-mdm/{vehicle_id}/accessories/{accessory_id}"): (VehicleAccessory, "an accessory row"),
    ("PATCH", "/v1/customers/{customer_id}/phones/{phone_id}"): (CustomerPhone, "a contact channel row (ADR-067)"),
    ("DELETE", "/v1/customers/{customer_id}/phones/{phone_id}"): (CustomerPhone, "a contact channel row (ADR-067)"),
    ("PATCH", "/v1/customers/{customer_id}/emails/{email_id}"): (CustomerEmail, "a contact channel row (ADR-067)"),
    ("DELETE", "/v1/customers/{customer_id}/emails/{email_id}"): (CustomerEmail, "a contact channel row (ADR-067)"),
    ("PATCH", "/v1/customers/{customer_id}/addresses/{address_id}"): (CustomerAddress, "an address row"),
    ("DELETE", "/v1/customers/{customer_id}/addresses/{address_id}"): (CustomerAddress, "an address row"),
    ("PATCH", "/v1/customers/{customer_id}/external-ids/{external_id_row_id}"): (CustomerExternalId, "an external id"),
    ("DELETE", "/v1/customers/{customer_id}/external-ids/{external_id_row_id}"): (CustomerExternalId, "an external id"),
    ("PATCH", "/v1/customers/{customer_id}/vehicles/{party_id}"): (VehicleParty, "a vehicle party row"),
    ("DELETE", "/v1/customers/{customer_id}/vehicles/{party_id}"): (VehicleParty, "a vehicle party row"),
    ("DELETE", "/v1/inventory/stock-items/{stock_item_id}/media/{media_id}"): (StockItemMedia, "a photo row"),
    ("PUT", "/v1/integrations/connections/{connection_id}/secrets/{slot}"): (IntegrationSecretRef, "a secret slot"),
    ("DELETE", "/v1/integrations/connections/{connection_id}/secrets/{slot}"): (IntegrationSecretRef, "a secret slot"),
    ("PUT", "/v1/me/preferences/{scope}"): (UserPreference, "the caller's own preference, last write wins"),
    ("DELETE", "/v1/me/preferences/{scope}"): (UserPreference, "the caller's own preference"),
    ("DELETE", "/v1/auth/sessions/{session_id}"): (AuthSession, "revoking one's own session; one row, idempotent"),
    ("DELETE", "/v1/auth/mfa/totp"): (MfaTotp, "removing one's own factor"),
    ("DELETE", "/v1/auth/mfa/passkeys/{passkey_id}"): (MfaPasskey, "removing one's own passkey"),
    # KAN-216: the reader's own read/unread stamp on an inbox entry, idempotent.
    ("PATCH", "/v1/me/notifications/{notification_id}"): (Notification, "one's own read/unread stamp"),
    ("DELETE", "/v1/me/push-subscriptions/{subscription_id}"): (PushSubscription, "switching push off on one's device"),
}


def _dependencies(dependant):
    for sub in dependant.dependencies:
        yield sub.call
        yield from _dependencies(sub)


def _mutating_routes():
    for path, route in api_routes(app.routes):
        for method in sorted(route.methods & MUTATING):
            yield method, path, route


def test_the_walk_sees_the_mutating_routes():
    assert any(True for _ in _mutating_routes()), "the walk found no PATCH/PUT/DELETE route at all"


def test_every_mutation_takes_if_match_or_writes_an_unversioned_row():
    failures = []
    for method, path, route in _mutating_routes():
        if require_if_match in set(_dependencies(route.dependant)):
            continue
        if (method, path) not in UNVERSIONED:
            failures.append(f"{method} {path}: no require_if_match — add it and check_version (KAN-396)")
    assert not failures, "\n".join(failures)


def _posts_on_an_entity():
    for path, route in api_routes(app.routes):
        if "POST" in route.methods and "{" in path:
            yield path, route


def test_every_post_on_an_existing_entity_takes_if_match_or_says_why():
    failures = [
        f"POST {path}: no require_if_match — add it and check_version, or list it with why (KAN-396)"
        for path, route in _posts_on_an_entity()
        if require_if_match not in set(_dependencies(route.dependant)) and path not in POST_WITHOUT_IF_MATCH
    ]
    assert not failures, "\n".join(failures)


def test_a_post_exemption_names_a_live_route_without_if_match():
    without = {
        path for path, route in _posts_on_an_entity() if require_if_match not in set(_dependencies(route.dependant))
    }
    for path, reason in POST_WITHOUT_IF_MATCH.items():
        assert path in without, f"{path}: no such POST without If-Match any more — drop the entry"
        assert reason


def test_an_exemption_names_an_unversioned_model_and_a_live_route():
    live = {(method, path) for method, path, _ in _mutating_routes()}
    for key, (model, reason) in UNVERSIONED.items():
        assert key in live, f"{key}: no such route any more — drop the entry"
        assert not issubclass(model, VersionedMixin), f"{key}: {model.__name__} is versioned — the route needs If-Match"
        assert reason


_OPEN_QUESTION_MARKERS = ("pending", "question", "undecided", "to be decided", "tbd", "todo")


def test_every_exemption_states_a_ruled_reason():
    """KAN-428: six entries once read "Anto's decision pending", so the guard
    passed on a question nobody had answered. An exemption is a reason (or a
    ruling); one that names an open question is refused."""

    reasons = {path: why for path, why in POST_WITHOUT_IF_MATCH.items()}
    reasons |= {f"{method} {path}": why for (method, path), (_, why) in UNVERSIONED.items()}
    open_questions = {
        key: why for key, why in reasons.items() if any(marker in why.lower() for marker in _OPEN_QUESTION_MARKERS)
    }
    assert not open_questions, open_questions
