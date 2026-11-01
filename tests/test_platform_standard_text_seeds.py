"""KAN-209 on KAN-208's library — the standard-text seeds in Platform
Administration › Translations (D-A-23, FR-A-29): listed by code with each
language's live and shipped wording; edited and reverted by platform_admin
only, If-Match on the seed row, audited, placeholders checked; a seed edit
flags every Betrieb's override of it "the standard text has changed, review
yours" and never touches the override."""

import uuid

import pytest
from sqlalchemy import select

from app.core.audit_model import AuditEvent
from app.core.auth import AccessRole
from app.core.i18n import SwissLanguage
from app.platform.models.standard_text import StandardText
from app.platform.services.standard_text_seed import SEED_CODES, SEED_TEXTS
from tests.membership_support import bearer, create_dealership, create_user, manager_of, platform_admin, token

CODE = "offer.footer.validity"
SHIPPED_FR = next(s for s in SEED_TEXTS if s.code == CODE).bodies[SwissLanguage.FR]


def _list(client, headers, **params):
    response = client.get("/v1/platform/standard-text-seeds", params=params, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["items"]


def _row(client, code=CODE, language="fr"):
    return next(g for g in _list(client, platform_admin()) if g["code"] == code)[language]


def _put(client, row, payload, headers=None, version=None):
    return client.put(
        f"/v1/platform/standard-text-seeds/{row['id']}",
        json=payload,
        headers={**(headers or platform_admin()), "If-Match": str(version if version is not None else row["version"])},
    )


def _revert(client, row, headers=None, version=None):
    return client.post(
        f"/v1/platform/standard-text-seeds/{row['id']}/revert",
        headers={**(headers or platform_admin()), "If-Match": str(version if version is not None else row["version"])},
    )


def test_the_library_is_listed_by_code_with_four_languages_and_the_shipped_wording(client, standard_text_seeds):
    items = _list(client, bearer(token(AccessRole.SALES)))  # any role reads (the matrix row)
    assert [g["code"] for g in items] == sorted(SEED_CODES)
    group = next(g for g in items if g["code"] == CODE)
    assert (group["category"], group["appliesTo"], group["slot"]) == ("document", "offer", "footer")
    fr = group["fr"]
    assert fr["body"] == fr["shippedBody"] == SHIPPED_FR
    assert fr["edited"] is False and fr["version"] == 1
    assert all(group[lang] for lang in ("de", "fr", "it", "en"))


def test_search_and_category_filter(client, standard_text_seeds):
    headers = platform_admin()
    assert [g["code"] for g in _list(client, headers, q="FOOTER.VALID")] == [CODE]
    email_codes = [g["code"] for g in _list(client, headers, category="email")]
    assert email_codes and all(code.startswith("email.") for code in email_codes)
    # A text in one language finds its code with all four languages.
    found = _list(client, headers, q=SHIPPED_FR[:25])
    assert [g["code"] for g in found] == [CODE] and found[0]["de"] is not None


def test_the_listing_needs_a_session(client, standard_text_seeds):
    assert client.get("/v1/platform/standard-text-seeds").status_code == 401


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param(lambda: bearer(token(AccessRole.SALES)), id="sales"),
        pytest.param(lambda: bearer(token(AccessRole.SALES, is_dealer_manager=True)), id="dealer-manager"),
        pytest.param(lambda: bearer(token(AccessRole.AUDITOR)), id="auditor"),
    ],
)
def test_only_platform_admin_edits_or_reverts_a_seed(client, standard_text_seeds, headers):
    row = _row(client)
    assert _put(client, row, {"body": "Gültig."}, headers=headers()).status_code == 403
    assert _revert(client, row, headers=headers()).status_code == 403
    assert _row(client)["version"] == 1


def test_an_edit_takes_if_match_and_is_audited(client, db_session, standard_text_seeds):
    row = _row(client)
    missing = client.put(f"/v1/platform/standard-text-seeds/{row['id']}", json={"body": "x"}, headers=platform_admin())
    assert missing.status_code == 400

    admin_id = uuid.uuid4()
    headers = bearer(token(AccessRole.PLATFORM_ADMIN, user_id=admin_id))
    new_body = "Cette offre est valable 20 jours à compter du {{document.date}}."
    edited = _put(client, row, {"body": new_body}, headers=headers)
    assert edited.status_code == 200, edited.text
    body = edited.json()
    assert (body["body"], body["shippedBody"], body["edited"], body["version"]) == (new_body, SHIPPED_FR, True, 2)
    assert body["updatedBy"] == str(admin_id)

    stale = _put(client, row, {"body": "Encore."}, version=1)
    assert stale.status_code == 409

    event = db_session.scalar(select(AuditEvent).where(AuditEvent.entity_type == "standard_text"))
    assert (event.action, event.actor_id, event.tenant_id) == ("seed_update", admin_id, None)
    assert event.before["body"] == SHIPPED_FR
    assert event.after["body"] == new_body


def test_a_placeholder_the_target_does_not_offer_is_refused(client, standard_text_seeds):
    row = _row(client)
    response = _put(client, row, {"body": "Valable jusqu'au {{nonsense.token}}."})
    assert response.status_code == 422
    assert response.json()["error"]["details"]["reason"] == "standard_text_placeholder_invalid"
    assert _row(client)["version"] == 1


def test_revert_restores_the_shipped_wording_and_is_audited(client, db_session, standard_text_seeds):
    row = _row(client)
    edited = _put(client, row, {"title": "Validité", "body": "Valable 10 jours."}).json()
    reverted = _revert(client, edited)
    assert reverted.status_code == 200, reverted.text
    body = reverted.json()
    assert (body["body"], body["edited"], body["version"]) == (SHIPPED_FR, False, 3)
    assert body["title"] == body["shippedTitle"]
    actions = [e.action for e in db_session.scalars(select(AuditEvent).order_by(AuditEvent.created_at))]
    assert actions == ["seed_update", "seed_revert"]
    # Reverting the shipped wording again changes nothing.
    again = _revert(client, body)
    assert again.status_code == 200 and again.json()["version"] == 3
    assert _revert(client, body, version=2).status_code == 409


def test_a_seed_edit_flags_the_betriebs_override_and_never_touches_it(client, standard_text_seeds):
    home = create_dealership(client)
    manager = manager_of(home, create_user(client, home))
    override = client.post(
        f"/v1/dealerships/{home}/standard-texts/document",
        json={"code": CODE, "language": "fr", "title": "Validité", "body": "Valable chez nous."},
        headers={**manager, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert override.status_code == 201, override.text
    assert override.json()["seedChanged"] is False

    assert _put(client, _row(client), {"body": "Valable 25 jours."}).status_code == 200

    texts = client.get(f"/v1/dealerships/{home}/standard-texts", headers=manager).json()["items"]
    mine = next(t for t in texts if t["id"] == override.json()["id"])
    assert mine["seedChanged"] is True
    assert mine["body"] == "Valable chez nous."


def test_no_dealer_route_reaches_the_seed_library(client, standard_text_seeds):
    """A manager is refused by the platform route; their own routes write
    their overrides, never the seed (KAN-208's tests pin that side)."""

    home = create_dealership(client)
    manager = manager_of(home, create_user(client, home))
    assert _put(client, _row(client), {"body": "Valable."}, headers=manager).status_code == 403


def test_a_seed_the_release_does_not_ship_has_nothing_to_revert_to(client, db_session, standard_text_seeds):
    shipped = db_session.scalar(
        select(StandardText).where(StandardText.code == CODE, StandardText.language == SwissLanguage.FR)
    )
    extra = StandardText(
        **{
            column: getattr(shipped, column)
            for column in ("scope", "category", "applies_to", "slot", "language", "source", "sort_order")
        },
        code="offer.footer.kan209_extra",
        title="Extra",
        body="Extra.",
    )
    db_session.add(extra)
    db_session.commit()
    row = _row(client, code="offer.footer.kan209_extra")
    assert (row["shippedBody"], row["edited"]) == (None, False)
    refused = _revert(client, row)
    assert refused.status_code == 422
    assert refused.json()["error"]["details"]["reason"] == "standard_text_seed_not_shipped"
