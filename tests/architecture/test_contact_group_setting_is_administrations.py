"""KAN-466 (A-RULE-0): the contact-group setting is Dealer Administration's,
never a module's own settings screen.

Which contact groups a Betrieb uses is configured in one place: the platform
router `app.platform.organisation.api.contact_groups` (`/v1/dealerships/{id}/contact-groups`,
the «Kontaktgruppen» screen). Customers only read which codes they may set
(`platform.public.contact_group_codes`). The test that would have caught a
module-owned setting:

- every route whose path names contact groups is served by that one platform
  module — a `/v1/customers/contact-groups` settings route, say, fails here;
- the only module of `app/` that names the switch table
  (`DealershipContactGroup`) outside its model is the platform service (the
  model registry and platform's reconciliation list it, writing nothing).

The frontend half is `src/architecture/contact-group-setting-is-administrations.test.ts`.
"""

import pathlib

from app.main import app
from tests.route_walk import api_routes

APP = pathlib.Path(__file__).resolve().parents[2] / "app"
_SETTING_MODULE = "app.platform.organisation.api.contact_groups"
_SWITCH_WRITERS = {"app/platform/organisation/models/contact_group.py", "app/platform/organisation/services/contact_group.py"}
# Composition roots and monitors that name every table without writing it.
_REGISTRIES = {"app/model_registry.py", "app/platform/reconciliation.py"}


def test_every_contact_group_route_is_the_administration_one():
    routes = [(path, route) for path, route in api_routes(app.routes) if "contact-group" in path]
    # The walk saw the two routes it claims to guard (tests.route_walk, KAN-241).
    assert sorted((path, sorted(route.methods)) for path, route in routes) == [
        ("/v1/dealerships/{dealership_id}/contact-groups", ["GET"]),
        ("/v1/dealerships/{dealership_id}/contact-groups/{value_code}", ["PUT"]),
    ]
    assert {route.endpoint.__module__ for _, route in routes} == {_SETTING_MODULE}


def test_only_the_platform_service_names_the_switch_table():
    named = {
        str(path.relative_to(APP.parent))
        for path in APP.rglob("*.py")
        if "DealershipContactGroup" in path.read_text(encoding="utf-8")
        or "dealership_contact_group" in path.read_text(encoding="utf-8")
    }
    assert named - _REGISTRIES == _SWITCH_WRITERS
