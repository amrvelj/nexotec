"""KAN-387: a NUL character (U+0000) in a request string is the taxonomy's
422, never a 500 — refused once, in `app/core/nul_guard.py`, for the path,
the query string and the body (Anto, 2026-10-19: refuse, never strip; beside
CamelModel, not in it). The login front door keeps its uniform 401
(FR-AU-07/16): an address with a NUL names no account.

Postgres is what refuses NUL in `text`; on the SQLite lane the routes would
not 500 without the guard, so the "not a 500" half proves itself on Postgres.
"""

import statistics
import time

from app.core.nul_guard import NUL_REFUSED, contains_nul
from tests.dealership_support import valid_uid
from tests.identity_support import (
    STRONG_PASSWORD,
    PasswordLogin,
    create_dealership,
    create_user,
    platform_admin_headers,
)


def _nul_refused(response, location: str) -> None:
    assert response.status_code == 422, response.text
    assert response.json() == {
        "error": {
            "code": "unprocessable_entity",
            "message": NUL_REFUSED,
            "details": {"reason": "nul_character", "location": location},
        }
    }


def test_a_nul_in_a_body_string_is_a_422(client):
    payload = {
        "legalName": "Garage\u0000 Muster AG",
        "franchiseType": "independent",
        "address": {
            "street": "Bahnhofstrasse",
            "houseNumber": "1",
            "postalCode": "8001",
            "locality": "Zürich",
            "canton": "ZH",
        },
        "phone": "+41441234567",
        "uid": valid_uid(),
    }
    _nul_refused(client.post("/v1/dealerships", json=payload, headers=platform_admin_headers()), "body")


def test_a_nul_deep_in_a_body_or_in_a_key_is_a_422(client):
    headers = platform_admin_headers()
    nested = {"legalName": "Garage", "address": {"street": "Bahnhof\u0000strasse"}}
    _nul_refused(client.post("/v1/dealerships", json=nested, headers=headers), "body")
    keyed = {"legalName": "Garage", "tags": [{"a\u0000": "b"}]}
    _nul_refused(client.post("/v1/dealerships", json=keyed, headers=headers), "body")


def test_a_nul_in_a_path_string_on_a_write_route_is_a_422(client):
    response = client.post(
        "/v1/reference-data/fuel_type%00",
        json={
            "valueCode": "hydrogen",
            "labelDe": "Wasserstoff",
            "labelFr": "Hydrog\u00e8ne",
            "labelIt": "Idrogeno",
            "labelEn": "Hydrogen",
        },
        headers=platform_admin_headers(),
    )
    _nul_refused(response, "path")


def test_a_nul_in_the_query_is_a_422(client):
    _nul_refused(
        client.get("/v1/reference-data/fuel_type?active=true&x=%00", headers=platform_admin_headers()), "query"
    )


def test_the_front_door_refuses_a_nul_with_a_422_not_a_500(client):
    # The reproductions from the ticket: the reset request and the passkey step.
    _nul_refused(client.post("/v1/auth/password-reset/request", json={"email": "a\u0000b@example.ch"}), "body")
    response = client.post(
        "/v1/auth/mfa/passkey/verify",
        json={"challengeId": "challenge", "credential": {"rawId": "abc\u0000", "id": "abc"}},
    )
    _nul_refused(response, "body")


def test_a_nul_free_body_reaches_the_route_unchanged(client):
    """The guard reads the body and hands the same bytes on: an ordinary
    write still lands, escaped characters and all."""

    dealership = create_dealership(client, legalName='Garage éè \\u0001 "Muster" AG')
    response = client.get(f"/v1/dealerships/{dealership}", headers=platform_admin_headers())
    assert response.status_code == 200, response.text
    assert response.json()["legalName"] == 'Garage éè \\u0001 "Muster" AG'


def test_a_body_that_is_not_json_is_left_to_the_route(client):
    response = client.post(
        "/v1/auth/password-reset/request", content=b"{not json\x00", headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"].get("reason") != "nul_character"


def test_contains_nul_walks_every_string():
    assert contains_nul({"a": [1, {"b": ["x", "y\u0000"]}]})
    assert contains_nul({"k\u0000": 1})
    assert not contains_nul({"a": [1, 2.5, None, True, {"b": "plain"}]})
    deep: list = []
    current = deep
    for _ in range(5000):
        nxt: list = []
        current.append(nxt)
        current = nxt
    current.append("\u0000")
    assert contains_nul(deep)


def test_login_with_a_nul_answers_the_uniform_401(client, mailbox):
    """FR-AU-07/16: the same body as any other refusal — the address with a
    NUL is an unknown address, a password with a NUL is a wrong password."""

    login = PasswordLogin(client, mailbox)
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    login.accept_invitation(anna)

    def attempt(email: str, password: str, ip: str) -> dict:
        response = client.post(
            "/v1/auth/login", json={"email": email, "password": password}, headers={"X-Forwarded-For": ip}
        )
        assert response.status_code == 401, response.text
        assert "dms_session=" not in response.headers.get("set-cookie", "")
        return response.json()

    bodies = [
        attempt("nobody@example.ch", STRONG_PASSWORD, "10.87.0.1"),
        attempt("a\u0000b@example.ch", STRONG_PASSWORD, "10.87.0.2"),
        attempt(anna["email"] + "\u0000", STRONG_PASSWORD, "10.87.0.3"),
        attempt(anna["email"], STRONG_PASSWORD + "\u0000", "10.87.0.4"),
    ]
    assert len({str(body) for body in bodies}) == 1, bodies


def test_login_with_a_nul_takes_the_time_of_any_other_refusal(client, mailbox):
    """The dummy argon2 verify runs for a NUL address too: twenty samples
    each, the medians within 50 % of an unknown address's."""

    def measure(email: str, n: int) -> float:
        started = time.perf_counter()
        response = client.post(
            "/v1/auth/login",
            json={"email": email, "password": "not-the-password-at-all"},
            headers={"X-Forwarded-For": f"10.88.{n // 250}.{n % 250 + 1}"},
        )
        assert response.status_code == 401, response.text
        return time.perf_counter() - started

    unknown = [measure(f"nobody-{i}@example.ch", i) for i in range(20)]
    nul = [measure(f"nobody-{i}\u0000@example.ch", 100 + i) for i in range(20)]
    ratio = statistics.median(nul) / statistics.median(unknown)
    assert 1 / 1.5 < ratio < 1.5, (statistics.median(nul), statistics.median(unknown))
