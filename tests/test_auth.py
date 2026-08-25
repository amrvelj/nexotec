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
from app.core.permissions import CAPABILITY_MATRIX, require_read, require_write

settings = get_settings()


def _token(*roles: AccessRole, tenant_id: uuid.UUID | None = None, is_dealer_manager: bool = False) -> str:
    return create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id or uuid.uuid4(),
        group_id=uuid.uuid4(),
        roles=frozenset(roles),
        is_dealer_manager=is_dealer_manager,
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def auth_client() -> TestClient:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/whoami")
    def whoami(principal: Principal = Depends(get_current_principal)):
        return {
            "userId": str(principal.user_id),
            "tenantId": str(principal.tenant_id),
            "roles": sorted(role.value for role in principal.roles),
            "isDealerManager": principal.is_dealer_manager,
        }

    @app.get("/sales-only")
    def sales_only(principal: Principal = Depends(require_access_role(AccessRole.SALES))):
        return {"ok": True}

    @app.get("/sales-or-inventory")
    def sales_or_inventory(
        principal: Principal = Depends(require_access_role(AccessRole.SALES, AccessRole.INVENTORY)),
    ):
        return {"ok": True}

    @app.get("/customers/read")
    def customers_read(principal: Principal = Depends(require_read("customers"))):
        return {"ok": True}

    @app.get("/customers/write")
    def customers_write(principal: Principal = Depends(require_write("customers"))):
        return {"ok": True}

    @app.get("/dealershipship-users/read")
    def dealership_users_read(principal: Principal = Depends(require_read("dealership_users"))):
        return {"ok": True}

    @app.get("/audit-logs/write")
    def audit_logs_write(principal: Principal = Depends(require_write("audit_logs"))):
        return {"ok": True}

    return TestClient(app)


# --- token verification --------------------------------------------------------------


def test_missing_authorization_header_is_401(auth_client):
    response = auth_client.get("/whoami")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_malformed_authorization_header_is_401(auth_client):
    response = auth_client.get("/whoami", headers={"Authorization": "Token abc"})
    assert response.status_code == 401


def test_valid_token_resolves_principal(auth_client):
    user_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    token = create_access_token(
        user_id=user_id,
        tenant_id=tenant_id,
        group_id=uuid.uuid4(),
        roles=frozenset({AccessRole.SALES}),
        is_dealer_manager=False,
    )
    response = auth_client.get("/whoami", headers=_bearer(token))
    assert response.status_code == 200
    body = response.json()
    assert body["userId"] == str(user_id)
    assert body["tenantId"] == str(tenant_id)
    assert body["roles"] == ["sales"]
    assert body["isDealerManager"] is False


def test_a_principal_can_hold_more_than_one_role(auth_client):
    token = _token(AccessRole.SALES, AccessRole.AFTERSALES, is_dealer_manager=True)
    response = auth_client.get("/whoami", headers=_bearer(token))
    assert response.status_code == 200
    body = response.json()
    assert body["roles"] == ["aftersales", "sales"]
    assert body["isDealerManager"] is True


def test_a_principal_can_hold_zero_functional_roles_and_still_be_a_manager(auth_client):
    """RP-1/ADR-026: is_dealer_manager is orthogonal to functional roles —
    the flag alone is what carries write access within the dealership.
    """
    token = _token(is_dealer_manager=True)
    response = auth_client.get("/whoami", headers=_bearer(token))
    assert response.status_code == 200
    assert response.json()["roles"] == []
    assert response.json()["isDealerManager"] is True


def test_expired_token_is_401(auth_client):
    token = create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        group_id=uuid.uuid4(),
        roles=frozenset({AccessRole.SALES}),
        ttl_seconds=-10,
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
        {
            "sub": str(uuid.uuid4()),
            "tenant_id": str(uuid.uuid4()),
            "group_id": str(uuid.uuid4()),
            "roles": ["sales"],
            "is_dealer_manager": False,
            "iss": settings.jwt_issuer,
            "iat": 0,
            "exp": 9999999999,
        },
        impostor_key,
        algorithm="RS256",
    )
    response = auth_client.get("/whoami", headers=_bearer(bad_token))
    assert response.status_code == 401


# --- require_access_role: plain role-membership gate ----------------------------------


def test_role_gate_allows_matching_role(auth_client):
    response = auth_client.get("/sales-only", headers=_bearer(_token(AccessRole.SALES)))
    assert response.status_code == 200


def test_role_gate_rejects_other_roles(auth_client):
    response = auth_client.get("/sales-only", headers=_bearer(_token(AccessRole.INVENTORY)))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_role_gate_always_allows_platform_admin(auth_client):
    response = auth_client.get("/sales-only", headers=_bearer(_token(AccessRole.PLATFORM_ADMIN)))
    assert response.status_code == 200


def test_role_gate_ignores_is_dealer_manager(auth_client):
    """require_access_role is a plain role check — it doesn't know about
    is_dealer_manager at all; that's app.core.permissions's job. A manager
    with no functional roles must NOT pass a bare role gate.
    """
    response = auth_client.get("/sales-only", headers=_bearer(_token(is_dealer_manager=True)))
    assert response.status_code == 403


@pytest.mark.parametrize(
    "roles",
    [
        (AccessRole.PLATFORM_ADMIN,),
        (AccessRole.SALES,),
        (AccessRole.AFTERSALES,),
        (AccessRole.PARTS,),
        (AccessRole.INVENTORY,),
        (AccessRole.FINANCE,),
        (AccessRole.TECHNICIAN,),
        (AccessRole.AUDITOR,),
    ],
)
def test_every_access_role_round_trips_through_a_token(auth_client, roles):
    """Every role in the target model (Roles & Permissions) must be
    mintable, decodable, and reported back correctly — not just the two or
    three exercised by the gating tests above.
    """
    token = _token(*roles)
    response = auth_client.get("/whoami", headers=_bearer(token))
    assert response.status_code == 200
    assert response.json()["roles"] == sorted(r.value for r in roles)


@pytest.mark.parametrize("role", [AccessRole.INVENTORY, AccessRole.AUDITOR])
def test_role_gate_rejects_roles_outside_the_allowed_set(auth_client, role):
    """Neither role is in /sales-only's allow-list (only sales + the
    always-allowed platform_admin are) — confirms the gate denies by
    default rather than allowing any recognized role through.
    """
    response = auth_client.get("/sales-only", headers=_bearer(_token(role)))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


@pytest.mark.parametrize("role", [AccessRole.SALES, AccessRole.INVENTORY, AccessRole.PLATFORM_ADMIN])
def test_role_gate_with_multiple_allowed_roles(auth_client, role):
    response = auth_client.get("/sales-or-inventory", headers=_bearer(_token(role)))
    assert response.status_code == 200


@pytest.mark.parametrize("role", [AccessRole.AFTERSALES, AccessRole.AUDITOR])
def test_role_gate_with_multiple_allowed_roles_still_rejects_others(auth_client, role):
    response = auth_client.get("/sales-or-inventory", headers=_bearer(_token(role)))
    assert response.status_code == 403


# --- app.core.permissions: capability checks (WP-2 PR-2) ------------------------------


def test_capability_matrix_never_grants_auditor_write():
    """The module-load-time assertion in app.core.permissions is the real
    enforcement of rule 4; this just documents/pins it as a test too.
    """
    for capability in CAPABILITY_MATRIX.values():
        assert AccessRole.AUDITOR not in capability.write_roles


def test_require_read_any_role_capability_admits_every_authenticated_role(auth_client):
    for role in AccessRole:
        response = auth_client.get("/customers/read", headers=_bearer(_token(role)))
        assert response.status_code == 200, role


def test_require_write_functional_role_is_admitted(auth_client):
    response = auth_client.get("/customers/write", headers=_bearer(_token(AccessRole.SALES)))
    assert response.status_code == 200


def test_require_write_rejects_a_role_not_in_the_capability(auth_client):
    response = auth_client.get("/customers/write", headers=_bearer(_token(AccessRole.PARTS)))
    assert response.status_code == 403


def test_require_write_is_dealer_manager_alone_is_sufficient(auth_client):
    """RP-1/ADR-026: the manager flag grants write within the dealership
    with no functional role required at all.
    """
    response = auth_client.get("/customers/write", headers=_bearer(_token(is_dealer_manager=True)))
    assert response.status_code == 200


def test_require_write_platform_admin_always_passes(auth_client):
    response = auth_client.get("/customers/write", headers=_bearer(_token(AccessRole.PLATFORM_ADMIN)))
    assert response.status_code == 200


def test_require_read_manager_only_capability_rejects_a_bare_functional_role(auth_client):
    """dealership_users' read_roles is an empty set — the matrix marks it
    manager-only, not "Any role" — so holding an unrelated functional role
    must not be enough.
    """
    response = auth_client.get("/dealershipship-users/read", headers=_bearer(_token(AccessRole.SALES)))
    assert response.status_code == 403


def test_require_read_manager_only_capability_admits_a_manager_with_no_functional_role(auth_client):
    response = auth_client.get(
        "/dealershipship-users/read", headers=_bearer(_token(is_dealer_manager=True))
    )
    assert response.status_code == 200


def test_require_write_audit_logs_rejects_even_the_dealer_manager(auth_client):
    """audit_logs is manager_can_write=False — "Nobody — append-only by
    the system" in the matrix, no carve-out for the manager flag.
    """
    response = auth_client.get("/audit-logs/write", headers=_bearer(_token(is_dealer_manager=True)))
    assert response.status_code == 403


def test_require_write_audit_logs_admits_platform_admin_only(auth_client):
    response = auth_client.get("/audit-logs/write", headers=_bearer(_token(AccessRole.PLATFORM_ADMIN)))
    assert response.status_code == 200


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
    token = create_access_token(
        user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), group_id=uuid.uuid4(), roles=frozenset({AccessRole.SALES})
    )

    jwks_key = client.get("/.well-known/jwks.json").json()["keys"][0]
    public_key = RSAPublicNumbers(
        n=_b64url_uint(jwks_key["n"]), e=_b64url_uint(jwks_key["e"])
    ).public_key()

    claims = jwt.decode(
        token,
        public_key,
        algorithms=["RS256"],
        issuer=settings.jwt_issuer,
        options={"require": ["sub", "tenant_id", "group_id", "roles", "is_dealer_manager", "exp", "iat"]},
    )
    assert claims["roles"] == ["sales"]
    assert jwt.get_unverified_header(token)["kid"] == jwks_key["kid"]
