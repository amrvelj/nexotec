import base64
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.core.auth import (
    AccessRole,
    Principal,
    create_access_token,
    get_current_principal,
    require_access_role,
)
from app.core.config import get_settings
from app.core.errors import register_error_handlers

settings = get_settings()


@pytest.fixture()
def auth_client() -> TestClient:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/whoami")
    def whoami(principal: Principal = Depends(get_current_principal)):
        return {"userId": str(principal.user_id), "tenantId": str(principal.tenant_id), "role": principal.access_role.value}

    @app.get("/sales-only")
    def sales_only(principal: Principal = Depends(require_access_role(AccessRole.SALES))):
        return {"ok": True}

    @app.get("/sales-or-dealer-admin")
    def sales_or_dealer_admin(
        principal: Principal = Depends(require_access_role(AccessRole.SALES, AccessRole.DEALER_ADMIN)),
    ):
        return {"ok": True}

    return TestClient(app)


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_missing_authorization_header_is_401(auth_client):
    response = auth_client.get("/whoami")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_malformed_authorization_header_is_401(auth_client):
    response = auth_client.get("/whoami", headers={"Authorization": "Token abc"})
    assert response.status_code == 401


def test_valid_token_resolves_principal(auth_client):
    user_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    token = create_access_token(user_id=user_id, tenant_id=tenant_id, access_role=AccessRole.SALES)
    response = auth_client.get("/whoami", headers=_bearer(token))
    assert response.status_code == 200
    body = response.json()
    assert body["userId"] == str(user_id)
    assert body["tenantId"] == str(tenant_id)
    assert body["role"] == "sales"


def test_expired_token_is_401(auth_client):
    token = create_access_token(
        user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), access_role=AccessRole.SALES, ttl_seconds=-10
    )
    response = auth_client.get("/whoami", headers=_bearer(token))
    assert response.status_code == 401


def test_token_signed_with_a_different_key_is_401(auth_client):
    """RS256's whole point: a token signed by anyone who isn't holding
    *this* process's private key must fail, even with an otherwise
    well-formed payload and the right algorithm/issuer.
    """
    impostor_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    bad_token = jwt.encode(
        {"sub": str(uuid.uuid4()), "tenant_id": str(uuid.uuid4()), "access_role": "sales",
         "iss": settings.jwt_issuer, "iat": 0, "exp": 9999999999},
        impostor_key,
        algorithm="RS256",
    )
    response = auth_client.get("/whoami", headers=_bearer(bad_token))
    assert response.status_code == 401


def test_role_gate_allows_matching_role(auth_client):
    token = create_access_token(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), access_role=AccessRole.SALES)
    response = auth_client.get("/sales-only", headers=_bearer(token))
    assert response.status_code == 200


def test_role_gate_rejects_other_roles(auth_client):
    token = create_access_token(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), access_role=AccessRole.INVENTORY)
    response = auth_client.get("/sales-only", headers=_bearer(token))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_role_gate_always_allows_platform_admin(auth_client):
    token = create_access_token(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), access_role=AccessRole.PLATFORM_ADMIN)
    response = auth_client.get("/sales-only", headers=_bearer(token))
    assert response.status_code == 200


@pytest.mark.parametrize(
    "role",
    [
        AccessRole.PLATFORM_ADMIN,
        AccessRole.DEALER_ADMIN,
        AccessRole.SALES,
        AccessRole.INVENTORY,
        AccessRole.AUDITOR,
    ],
)
def test_every_access_role_round_trips_through_a_token(auth_client, role):
    """All five roles from the CTO's access-control matrix (Round 3 addendum)
    must be mintable, decodable, and reported back correctly — not just the
    two or three exercised by the gating tests above.
    """
    user_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    token = create_access_token(user_id=user_id, tenant_id=tenant_id, access_role=role)
    response = auth_client.get("/whoami", headers=_bearer(token))
    assert response.status_code == 200
    assert response.json()["role"] == role.value


@pytest.mark.parametrize("role", [AccessRole.DEALER_ADMIN, AccessRole.AUDITOR])
def test_role_gate_rejects_roles_outside_the_allowed_set(auth_client, role):
    """dealer_admin and auditor aren't in /sales-only's allow-list (only
    sales + the always-allowed platform_admin are) — confirms the gate
    denies by default rather than allowing any recognized role through.
    """
    token = create_access_token(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), access_role=role)
    response = auth_client.get("/sales-only", headers=_bearer(token))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


@pytest.mark.parametrize("role", [AccessRole.SALES, AccessRole.DEALER_ADMIN, AccessRole.PLATFORM_ADMIN])
def test_role_gate_with_multiple_allowed_roles(auth_client, role):
    token = create_access_token(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), access_role=role)
    response = auth_client.get("/sales-or-dealer-admin", headers=_bearer(token))
    assert response.status_code == 200


@pytest.mark.parametrize("role", [AccessRole.INVENTORY, AccessRole.AUDITOR])
def test_role_gate_with_multiple_allowed_roles_still_rejects_others(auth_client, role):
    token = create_access_token(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), access_role=role)
    response = auth_client.get("/sales-or-dealer-admin", headers=_bearer(token))
    assert response.status_code == 403


# --- JWKS (WP-2 PR-1, ADR-007) -------------------------------------------------------


def _b64url_uint(value: str) -> int:
    padded = value + "=" * (-len(value) % 4)
    return int.from_bytes(base64.urlsafe_b64decode(padded), "big")


def test_jwks_endpoint_publishes_one_rsa_signing_key(client):
    response = client.get("/.well-known/jwks.json")
    assert response.status_code == 200
    body = response.json()
    assert len(body["keys"]) == 1
    key = body["keys"][0]
    assert key["kty"] == "RSA"
    assert key["alg"] == "RS256"
    assert key["use"] == "sig"
    assert key["kid"]
    assert key["n"] and key["e"]


def test_a_token_signed_with_the_private_key_verifies_against_the_published_jwks(client):
    """The WP-2 PR-1 exit criterion, as a test: mint a token the normal
    way, reconstruct the verification key purely from what
    /.well-known/jwks.json publishes — never touching app.core.auth's own
    key objects directly — and verify with that.
    """
    token = create_access_token(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), access_role=AccessRole.SALES)

    jwks_key = client.get("/.well-known/jwks.json").json()["keys"][0]
    public_key = RSAPublicNumbers(
        n=_b64url_uint(jwks_key["n"]), e=_b64url_uint(jwks_key["e"])
    ).public_key()

    claims = jwt.decode(
        token,
        public_key,
        algorithms=["RS256"],
        issuer=settings.jwt_issuer,
        options={"require": ["sub", "tenant_id", "access_role", "exp", "iat"]},
    )
    assert claims["access_role"] == "sales"
    assert jwt.get_unverified_header(token)["kid"] == jwks_key["kid"]
