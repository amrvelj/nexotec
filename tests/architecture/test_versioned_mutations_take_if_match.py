"""Every PATCH, PUT and DELETE takes If-Match (CLAUDE.md, API conventions:
"If-Match on every mutation of a versioned entity"; KAN-396).

A route that mutates a `VersionedMixin` entity without `require_if_match`
lets two saves from the same read both land — the second silently overwrites
the first. KAN-396 found three (stock-item options PUT, listing-text PATCH,
connection DELETE). The guard is by method: a PATCH/PUT/DELETE must depend on
`require_if_match`, unless it is listed below with the model it writes, and
that model must not be versioned — so an entry cannot excuse a versioned
entity, and a model that later gains a version fails its entry.

A POST transition (confirm, cancel, enable, …) is not walked here: POSTs are
creations as often as transitions, and the transitions carry If-Match by the
same convention, reviewed route by route.
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
from app.platform.models.user_preference import UserPreference
from app.vehicle.models.vehicle_history import VehicleAccessory
from tests.route_walk import api_routes

MUTATING = {"PATCH", "PUT", "DELETE"}

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


def test_an_exemption_names_an_unversioned_model_and_a_live_route():
    live = {(method, path) for method, path, _ in _mutating_routes()}
    for key, (model, reason) in UNVERSIONED.items():
        assert key in live, f"{key}: no such route any more — drop the entry"
        assert not issubclass(model, VersionedMixin), f"{key}: {model.__name__} is versioned — the route needs If-Match"
        assert reason
