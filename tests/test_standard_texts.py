"""KAN-208: the standard-text library — the closed placeholder registry that
fails the render on anything it cannot resolve (A-RULE-4), the language and
scope fallback, the four-language seed library, dealer overrides with the
"seed changed — review yours" flag (FR-A-29), the API with its
delegation-aware gate (FR-A-28) and the editor's preview (FR-A-07)."""

import datetime as dt
import uuid

import pytest
from sqlalchemy import select

from app.core.auth import AccessRole
from app.core.base import utcnow
from app.core.errors import UnprocessableEntityError
from app.core.i18n import SwissLanguage
from app.platform.models.dealership import DealerGroup, Dealership, FranchiseType, Location
from app.platform.models.standard_text import (
    StandardText,
    StandardTextAppliesTo,
    StandardTextScope,
    StandardTextSlot,
    StandardTextSource,
)
from app.platform.models.standard_text_delegation import StandardTextCategory
from app.platform.public import PLACEHOLDER_UNRESOLVED, render_standard_text
from app.platform.services import standard_text as service
from app.platform.services.standard_text_placeholders import (
    PLACEHOLDER_INVALID,
    TARGETS,
    Placeholder,
    render_body,
    target_for,
    validate_body,
)
from app.platform.services.standard_text_seed import SEED_CODES, SEED_TEXTS
from tests.dealership_support import valid_uid
from tests.membership_support import bearer, create_dealership, create_user, manager_of, platform_admin, token

_DE, _FR, _IT, _EN = SwissLanguage.DE, SwissLanguage.FR, SwissLanguage.IT, SwissLanguage.EN
_DOC = StandardTextCategory.DOCUMENT
_OFFER = StandardTextAppliesTo.OFFER
_OFFER_TARGET = target_for(_DOC, _OFFER)


# --- the closed registry and A-RULE-4 -------------------------------------------------


def test_the_registry_is_the_prds_eight_tokens():
    assert {token.value for token in Placeholder} == {
        "dealership.name",
        "location.phone",
        "customer.salutation",
        "vehicle.make_model",
        "document.number",
        "document.date",
        "user.full_name",
        "payment.due_date",
    }
    # Every target offers a subset of them and at least one slot.
    for target in TARGETS:
        assert target.slots and target.placeholders <= set(Placeholder)
    # Per target: the offer document has no payment due date, the invoice does.
    assert Placeholder.PAYMENT_DUE_DATE not in _OFFER_TARGET.placeholders
    assert Placeholder.PAYMENT_DUE_DATE in target_for(_DOC, StandardTextAppliesTo.INVOICE).placeholders


def test_a_render_substitutes_every_token():
    out = render_body(
        "Offerte {{document.number}} vom {{ document.date }}.",
        target=_OFFER_TARGET,
        values={"document.number": "O-000001", "document.date": "31.10.2026"},
    )
    assert out == "Offerte O-000001 vom 31.10.2026."


@pytest.mark.parametrize(
    ("body", "values", "problem"),
    [
        ("Hallo {{customer.nickname}}", {}, "unknown"),
        # Known, but an offer document cannot supply it.
        ("Zahlbar bis {{payment.due_date}}", {"payment.due_date": "30.11.2026"}, "not_valid_here"),
        ("Offerte {{document.number}", {"document.number": "O-1"}, "malformed"),
        ("Ihr Berater: {{user.full_name}}", {"user.full_name": None}, "unresolved"),
        ("Ihr Berater: {{user.full_name}}", {"user.full_name": "   "}, "unresolved"),
        ("Ihr Berater: {{user.full_name}}", {}, "unresolved"),
    ],
)
def test_an_unknown_or_unresolvable_token_fails_the_render(body, values, problem):
    """Never blank, never the raw token: the render raises and names it."""

    with pytest.raises(UnprocessableEntityError) as exc:
        render_body(body, target=_OFFER_TARGET, values=values)
    details = exc.value.details
    assert details["reason"] == PLACEHOLDER_UNRESOLVED
    assert [p["problem"] for p in details["placeholders"]] == [problem]


def test_a_save_refuses_tokens_the_target_does_not_offer():
    validate_body("Offerte {{document.number}}", _OFFER_TARGET)
    with pytest.raises(UnprocessableEntityError) as exc:
        validate_body("{{customer.salutation}} — {{foo}}", _OFFER_TARGET)
    assert exc.value.details["reason"] == PLACEHOLDER_INVALID
    assert exc.value.details["placeholders"] == [
        {"token": "customer.salutation", "problem": "not_valid_here"},
        {"token": "foo", "problem": "unknown"},
    ]


# --- the seed library ------------------------------------------------------------------


def test_the_seed_library_covers_the_starter_set_in_four_languages():
    assert set(SEED_CODES) == {
        "offer.intro.standard",
        "offer.footer.validity",
        "offer.terms.trade_in",
        "contract.terms.delivery",
        "contract.closing.warranty",
        "contract.footer.data_protection",
        "invoice.footer.payment_terms",
        "invoice.terms.late_payment",
        "email.offer_sent",
        "email.appointment_reminder",
        "email.vehicle_ready",
        "email.invoice_enclosed",
    }
    for seed in SEED_TEXTS:
        target = target_for(seed.category, seed.applies_to)
        assert target is not None and seed.slot in target.slots, seed.code
        assert set(seed.titles) == set(seed.bodies) == set(SwissLanguage), seed.code
        for language in SwissLanguage:
            assert seed.titles[language].strip() and seed.bodies[language].strip()
            validate_body(seed.bodies[language], target)  # only tokens the target offers
        assert "ß" not in seed.bodies[_DE] + seed.titles[_DE], f"{seed.code}: Swiss German writes ss"
        # The four languages are four texts, not one copied.
        assert len({seed.bodies[language] for language in SwissLanguage}) == 4, seed.code
    # One default per (category, applies_to, slot).
    slots = [(s.category, s.applies_to, s.slot) for s in SEED_TEXTS if s.is_default]
    assert len(slots) == len(set(slots))


def test_installing_the_seeds_is_idempotent_and_marks_them_platform_seeds(db_session):
    assert service.install_platform_seeds(db_session) == len(SEED_TEXTS) * 4
    db_session.commit()
    assert service.install_platform_seeds(db_session) == 0
    rows = service.list_platform_seeds(db_session)
    assert len(rows) == 48
    assert {row.source for row in rows} == {StandardTextSource.PLATFORM_SEED}
    assert {row.dealership_id for row in rows} == {None}


# --- resolution: language and scope, deterministic -------------------------------------


def _dealership(db_session, *, default_language=_DE) -> Dealership:
    group = DealerGroup(name="Gruppe")
    db_session.add(group)
    db_session.flush()
    dealership = Dealership(
        dealer_group_id=group.id,
        legal_name="Garage Muster AG",
        franchise_type=FranchiseType.INDEPENDENT,
        address_street="Bahnhofstrasse",
        address_house_number="1",
        address_postal_code="8001",
        address_locality="Zürich",
        address_canton="ZH",
        phone="+41441234567",
        uid=valid_uid(),
        default_correspondence_language=default_language,
    )
    db_session.add(dealership)
    db_session.commit()
    return dealership


def _dealer_row(db_session, dealership, *, code, language, body, location=None, active=True, is_default=True, seed=None):
    row = StandardText(
        dealership_id=dealership.id,
        location_id=location.id if location else None,
        scope=StandardTextScope.LOCATION if location else StandardTextScope.DEALERSHIP,
        code=code,
        category=_DOC,
        applies_to=_OFFER,
        slot=StandardTextSlot.INTRO,
        language=language,
        title="t",
        body=body,
        source=StandardTextSource.DEALER,
        overridden_from_id=seed.id if seed else None,
        overridden_seed_version=seed.version if seed else None,
        is_default=is_default,
        active=active,
        sort_order=10,
        version=1,
    )
    db_session.add(row)
    db_session.commit()
    return row


def _intro(db_session, dealership, language, location=None):
    return service.resolve_text(
        db_session,
        dealership_id=dealership.id,
        category=_DOC,
        applies_to=_OFFER,
        slot=StandardTextSlot.INTRO,
        language=language,
        location_id=location.id if location else None,
    )


def _seed(db_session, code, language) -> StandardText:
    return db_session.scalar(
        select(StandardText).where(
            StandardText.dealership_id.is_(None), StandardText.code == code, StandardText.language == language
        )
    )


def test_the_language_chain_is_requested_then_the_betriebs_default_then_de():
    assert service.language_chain(_FR, _IT) == [_FR, _IT, _DE]
    assert service.language_chain(_IT, _IT) == [_IT, _DE]
    assert service.language_chain(_DE, _FR) == [_DE, _FR]
    assert service.language_chain(_EN, None) == [_EN, _DE]


def test_resolution_falls_back_through_the_language_chain(db_session):
    dealership = _dealership(db_session, default_language=_IT)
    # Only the Betrieb's own code, in Italian and German: an English request
    # gets the Betrieb's default (IT); once IT is gone, DE.
    italian = _dealer_row(db_session, dealership, code="offer.intro.own", language=_IT, body="Italiano")
    _dealer_row(db_session, dealership, code="offer.intro.own", language=_DE, body="Deutsch")
    assert _intro(db_session, dealership, _EN).body == "Italiano"
    assert _intro(db_session, dealership, _DE).body == "Deutsch"
    db_session.delete(italian)
    db_session.commit()
    assert _intro(db_session, dealership, _EN).body == "Deutsch"
    assert _intro(db_session, dealership, _FR).body == "Deutsch"


def test_resolution_prefers_location_then_dealership_then_seed(db_session, standard_text_seeds):
    dealership = _dealership(db_session)
    site = Location(tenant_id=dealership.id, name="Filiale Winterthur")
    db_session.add(site)
    db_session.commit()
    seed = _seed(db_session, "offer.intro.standard", _DE)

    assert _intro(db_session, dealership, _DE).id == seed.id
    override = _dealer_row(db_session, dealership, code=seed.code, language=_DE, body="Betrieb", seed=seed)
    assert _intro(db_session, dealership, _DE).id == override.id
    assert _intro(db_session, dealership, _DE, site).id == override.id
    variant = _dealer_row(db_session, dealership, code=seed.code, language=_DE, body="Filiale", location=site, seed=seed)
    assert _intro(db_session, dealership, _DE, site).id == variant.id
    assert _intro(db_session, dealership, _DE).id == override.id  # no location: the Betrieb's

    # A location text switched off falls back to the Betrieb's; the Betrieb's
    # switched off masks the seed — the slot has no text in DE, so the chain
    # moves on (the Betrieb's default language is DE, so nothing is left).
    variant.active = False
    db_session.commit()
    assert _intro(db_session, dealership, _DE, site).id == override.id
    override.active = False
    db_session.commit()
    assert _intro(db_session, dealership, _DE) is None
    # French still has its seed: the language is the outer chain.
    assert _intro(db_session, dealership, _FR).id == _seed(db_session, seed.code, _FR).id


def test_the_right_language_beats_the_narrower_scope(db_session, standard_text_seeds):
    dealership = _dealership(db_session)
    seed_de = _seed(db_session, "offer.intro.standard", _DE)
    _dealer_row(db_session, dealership, code=seed_de.code, language=_DE, body="Unser Deutsch", seed=seed_de)
    # A French customer gets the French seed, not the Betrieb's German wording.
    assert _intro(db_session, dealership, _FR).body == _seed(db_session, seed_de.code, _FR).body


def test_the_betriebs_default_wins_over_the_seeds_and_ties_are_deterministic(db_session, standard_text_seeds):
    dealership = _dealership(db_session)
    seed = _seed(db_session, "offer.intro.standard", _DE)
    own = _dealer_row(db_session, dealership, code="offer.intro.own", language=_DE, body="Eigene Einleitung")
    assert _intro(db_session, dealership, _DE).id == own.id
    own.is_default = False
    db_session.commit()
    assert _intro(db_session, dealership, _DE).id == seed.id
    # Asked by code, a non-default text resolves too.
    by_code = service.resolve_text(
        db_session,
        dealership_id=dealership.id,
        category=_DOC,
        applies_to=_OFFER,
        slot=StandardTextSlot.INTRO,
        language=_DE,
        code="offer.intro.own",
    )
    assert by_code.id == own.id
    # The same question gives the same answer every time.
    assert len({_intro(db_session, dealership, _DE).id for _ in range(5)}) == 1


def test_render_standard_text_fills_platforms_tokens_and_fails_on_the_callers_missing_one(db_session, standard_text_seeds):
    dealership = _dealership(db_session)
    seed = _seed(db_session, "offer.intro.standard", _DE)
    _dealer_row(
        db_session,
        dealership,
        code=seed.code,
        language=_DE,
        body="{{dealership.name}} ({{location.phone}}): Offerte {{document.number}}, Ihr Berater {{user.full_name}}.",
        seed=seed,
    )
    kwargs = {
        "dealership_id": dealership.id,
        "category": _DOC,
        "applies_to": _OFFER,
        "slot": StandardTextSlot.INTRO,
        "language": _DE,
    }
    rendered = render_standard_text(
        db_session, values={"document.number": "O-000007", "user.full_name": "Anna Beispiel"}, **kwargs
    )
    assert rendered is not None
    assert rendered.text == "Garage Muster AG (+41441234567): Offerte O-000007, Ihr Berater Anna Beispiel."
    assert rendered.code == seed.code and rendered.language == _DE

    with pytest.raises(UnprocessableEntityError) as exc:
        render_standard_text(db_session, values={"document.number": "O-000007", "user.full_name": None}, **kwargs)
    assert exc.value.details["reason"] == PLACEHOLDER_UNRESOLVED
    assert exc.value.details["placeholders"] == [{"token": "user.full_name", "problem": "unresolved"}]
    assert exc.value.details["code"] == seed.code


def test_location_phone_is_the_locations_own_and_falls_back_to_the_betriebs(db_session, standard_text_seeds):
    """{{location.phone}}: the phone of the location the render is scoped to
    (KAN-204's `location.phone`) when it has one, else the Betrieb's — also
    for a render without a location."""

    dealership = _dealership(db_session)
    with_phone = Location(tenant_id=dealership.id, name="Filiale Winterthur", phone="+41522223344")
    without_phone = Location(tenant_id=dealership.id, name="Filiale Uster", phone=None)
    blank_phone = Location(tenant_id=dealership.id, name="Filiale Wetzikon", phone="  ")
    db_session.add_all([with_phone, without_phone, blank_phone])
    db_session.commit()
    seed = _seed(db_session, "offer.intro.standard", _DE)
    _dealer_row(db_session, dealership, code=seed.code, language=_DE, body="Telefon {{location.phone}}", seed=seed)

    def rendered(location):
        result = render_standard_text(
            db_session,
            dealership_id=dealership.id,
            category=_DOC,
            applies_to=_OFFER,
            slot=StandardTextSlot.INTRO,
            language=_DE,
            values={},
            location_id=location.id if location else None,
        )
        assert result is not None
        return result.text

    assert rendered(with_phone) == "Telefon +41522223344"
    assert rendered(without_phone) == "Telefon +41441234567"
    assert rendered(blank_phone) == "Telefon +41441234567"
    assert rendered(None) == "Telefon +41441234567"
    # The editor's preview, scoped to a location, uses the same rule.
    preview = service.preview(
        db_session,
        dealership_id=dealership.id,
        category=_DOC,
        applies_to=_OFFER,
        language=_DE,
        body="{{location.phone}}",
        location_id=with_phone.id,
    )
    assert preview == "+41522223344"


# --- the API ------------------------------------------------------------------------


def _texts(client, dealership, headers, **params):
    response = client.get(f"/v1/dealerships/{dealership}/standard-texts", params=params, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["items"]


def _create(client, dealership, category, payload, headers):
    return client.post(
        f"/v1/dealerships/{dealership}/standard-texts/{category}",
        json=payload,
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )


def _seed_item(items, code, language):
    return next(i for i in items if i["code"] == code and i["language"] == language and i["source"] == "platform_seed")


def test_the_library_lists_seeds_and_the_betriebs_own_rows_only(client, standard_text_seeds):
    home = create_dealership(client)
    other = create_dealership(client, "Garage Andere AG")
    manager = manager_of(home, create_user(client, home))
    other_manager = manager_of(other, create_user(client, other))
    created = _create(
        client,
        other,
        "document",
        {"language": "de", "title": "Fremd", "body": "Andere", "appliesTo": "offer", "slot": "intro"},
        other_manager,
    )
    assert created.status_code == 201, created.text

    items = _texts(client, home, manager)
    assert len(items) == 48 and {i["source"] for i in items} == {"platform_seed"}
    assert len(_texts(client, home, manager, category="email")) == 16
    # Any role reads the library; another Betrieb's path is 404.
    seller = bearer(token(AccessRole.SALES, tenant_id=home))
    assert len(_texts(client, home, seller)) == 48
    assert client.get(f"/v1/dealerships/{other}/standard-texts", headers=seller).status_code == 404


def test_a_dealer_edit_of_a_seed_creates_an_override_and_never_touches_the_seed(client, standard_text_seeds):
    home = create_dealership(client)
    manager = manager_of(home, create_user(client, home))
    seed = _seed_item(_texts(client, home, manager), "offer.intro.standard", "de")

    response = _create(
        client,
        home,
        "document",
        {"code": "offer.intro.standard", "language": "de", "title": "Unsere Einleitung", "body": "Grüezi und danke."},
        manager,
    )
    assert response.status_code == 201, response.text
    override = response.json()
    assert override["source"] == "dealer"
    assert override["overriddenFromId"] == seed["id"]
    assert (override["appliesTo"], override["slot"], override["isDefault"]) == ("offer", "intro", True)
    assert override["seedChanged"] is False

    items = _texts(client, home, manager)
    assert _seed_item(items, "offer.intro.standard", "de")["body"] == seed["body"]  # the seed is untouched
    # A second DE override of the same code at the same scope is refused.
    again = _create(
        client, home, "document", {"code": "offer.intro.standard", "language": "de", "title": "x", "body": "y"}, manager
    )
    assert again.status_code == 422 and again.json()["error"]["details"]["reason"] == "standard_text_exists"


def test_a_seed_change_flags_every_override_until_it_is_reviewed(client, engine, standard_text_seeds):
    from sqlalchemy.orm import sessionmaker

    home = create_dealership(client)
    manager = manager_of(home, create_user(client, home))
    override = _create(
        client, home, "document", {"code": "offer.footer.validity", "language": "fr", "title": "x", "body": "Valable."}, manager
    ).json()

    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        seed = service.get_platform_seed_or_404(session, uuid.UUID(override["overriddenFromId"]), for_update=True)
        service.update_platform_seed(
            session,
            seed=seed,
            changes={"body": "Cette offre est valable 20 jours à compter du {{document.date}}."},
            if_match_version=1,
            actor_id=None,
        )
        assert seed.version == 2
    finally:
        session.close()

    flagged = next(i for i in _texts(client, home, manager) if i["id"] == override["id"])
    assert flagged["seedChanged"] is True
    assert flagged["body"] == "Valable."  # the seed edit never overwrote the Betrieb's wording

    reviewed = client.patch(
        f"/v1/dealerships/{home}/standard-texts/document/{override['id']}",
        json={"seedReviewed": True},
        headers={**manager, "If-Match": str(flagged["version"])},
    )
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["seedChanged"] is False


def test_patch_takes_if_match_and_delete_returns_the_slot_to_the_seed(client, standard_text_seeds):
    home = create_dealership(client)
    manager = manager_of(home, create_user(client, home))
    row = _create(
        client, home, "document", {"code": "offer.intro.standard", "language": "de", "title": "x", "body": "Eins."}, manager
    ).json()
    url = f"/v1/dealerships/{home}/standard-texts/document/{row['id']}"

    assert client.patch(url, json={"body": "Zwei."}, headers=manager).status_code == 400  # no If-Match
    assert client.patch(url, json={"body": "Zwei."}, headers={**manager, "If-Match": "9"}).status_code == 409
    updated = client.patch(url, json={"body": "Zwei."}, headers={**manager, "If-Match": "1"})
    assert updated.status_code == 200 and updated.json()["version"] == 2
    invalid = client.patch(url, json={"body": "Bis {{payment.due_date}}"}, headers={**manager, "If-Match": "2"})
    assert invalid.status_code == 422
    assert invalid.json()["error"]["details"]["reason"] == PLACEHOLDER_INVALID

    assert client.delete(url, headers={**manager, "If-Match": "2"}).status_code == 204
    assert all(i["source"] == "platform_seed" for i in _texts(client, home, manager))


def test_seeds_and_other_categories_are_not_reachable_by_id(client, standard_text_seeds):
    home = create_dealership(client)
    manager = manager_of(home, create_user(client, home))
    seed = _seed_item(_texts(client, home, manager), "offer.intro.standard", "de")
    # A seed is not the Betrieb's row: 404 (FR-A-29 keeps it to platform staff).
    assert (
        client.patch(
            f"/v1/dealerships/{home}/standard-texts/document/{seed['id']}",
            json={"body": "Gehackt."},
            headers={**manager, "If-Match": "1"},
        ).status_code
        == 404
    )
    own = _create(
        client, home, "document", {"code": "offer.intro.standard", "language": "it", "title": "x", "body": "Ciao."}, manager
    ).json()
    # The same row under another category's path is 404 — the gate's category binds the lookup.
    assert (
        client.patch(
            f"/v1/dealerships/{home}/standard-texts/email/{own['id']}",
            json={"body": "x"},
            headers={**manager, "If-Match": "1"},
        ).status_code
        == 404
    )


def test_a_delegation_writes_its_one_category_only(client, standard_text_seeds):
    home = create_dealership(client)
    create_user(client, home)  # the manager
    user = create_user(client, home, is_dealer_manager=False)
    granted = client.put(
        f"/v1/dealerships/{home}/users/{user['id']}/text-delegations",
        json={"categories": ["email"]},
        headers={**platform_admin(), "If-Match": "1"},
    )
    assert granted.status_code == 200, granted.text
    delegate = bearer(token(AccessRole.SALES, tenant_id=home, user_id=user["id"]))

    email = _create(
        client, home, "email", {"code": "email.offer_sent", "language": "de", "title": "x", "body": "Hallo."}, delegate
    )
    assert email.status_code == 201, email.text
    document = _create(
        client, home, "document", {"code": "offer.intro.standard", "language": "de", "title": "x", "body": "y"}, delegate
    )
    assert document.status_code == 403
    # Naming a document seed's code on the e-mail path does not smuggle it in.
    smuggled = _create(
        client, home, "email", {"code": "offer.intro.standard", "language": "de", "title": "x", "body": "y"}, delegate
    )
    assert smuggled.status_code == 422
    assert smuggled.json()["error"]["details"]["reason"] == "standard_text_category_mismatch"
    # A plain user without the delegation writes nothing.
    nobody = bearer(token(AccessRole.SALES, tenant_id=home))
    assert _create(client, home, "email", {"code": "email.offer_sent", "language": "fr", "title": "x", "body": "y"}, nobody).status_code == 403

    # The library tells each caller what the gate will let through (the editor offers no more).
    def writable(headers):
        response = client.get(f"/v1/dealerships/{home}/standard-texts", headers=headers)
        return response.json()["writableCategories"]

    assert writable(delegate) == ["email"]
    assert writable(nobody) == []
    assert writable(platform_admin()) == ["document", "email", "sms", "whatsapp", "internal_note", "workshop"]


def test_whatsapp_is_a_delegable_category(client):
    home = create_dealership(client)
    user = create_user(client, home, is_dealer_manager=False)
    granted = client.put(
        f"/v1/dealerships/{home}/users/{user['id']}/text-delegations",
        json={"categories": ["whatsapp"]},
        headers={**platform_admin(), "If-Match": "1"},
    )
    assert granted.status_code == 200, granted.text
    assert granted.json()["categories"] == ["whatsapp"]


def test_another_betriebs_path_is_404_for_a_manager(client, standard_text_seeds):
    home = create_dealership(client)
    other = create_dealership(client, "Garage Andere AG")
    manager = manager_of(home, create_user(client, home))
    response = _create(
        client, other, "document", {"code": "offer.intro.standard", "language": "de", "title": "x", "body": "y"}, manager
    )
    assert response.status_code == 404


def test_a_new_default_demotes_the_previous_one_of_its_slot(client, standard_text_seeds):
    home = create_dealership(client)
    manager = manager_of(home, create_user(client, home))
    first = _create(
        client,
        home,
        "document",
        {"language": "de", "title": "A", "body": "Erste.", "appliesTo": "offer", "slot": "intro", "isDefault": True},
        manager,
    ).json()
    assert first["code"].startswith("offer.intro.") and first["code"] not in SEED_CODES
    second = _create(
        client,
        home,
        "document",
        {"language": "de", "title": "B", "body": "Zweite.", "appliesTo": "offer", "slot": "intro", "isDefault": True},
        manager,
    ).json()
    rows = {i["id"]: i for i in _texts(client, home, manager)}
    assert rows[second["id"]]["isDefault"] is True
    assert rows[first["id"]]["isDefault"] is False and rows[first["id"]]["version"] == 2


def test_a_location_variant_and_a_foreign_location(client, db_session, standard_text_seeds):
    home = create_dealership(client)
    other = create_dealership(client, "Garage Andere AG")
    manager = manager_of(home, create_user(client, home))
    site = Location(tenant_id=uuid.UUID(home), name="Filiale")
    foreign = Location(tenant_id=uuid.UUID(other), name="Fremd")
    db_session.add_all([site, foreign])
    db_session.commit()
    payload = {"code": "offer.intro.standard", "language": "de", "title": "x", "body": "Filiale."}
    variant = _create(client, home, "document", {**payload, "locationId": str(site.id)}, manager)
    assert variant.status_code == 201, variant.text
    assert variant.json()["scope"] == "location"
    assert _create(client, home, "document", {**payload, "locationId": str(foreign.id)}, manager).status_code == 404


def test_whatsapp_texts_carry_the_template_approval(client, standard_text_seeds):
    home = create_dealership(client)
    manager = manager_of(home, create_user(client, home))
    base = {"language": "de", "title": "Bereit", "body": "Ihr {{vehicle.make_model}} ist bereit.", "appliesTo": "vehicle_ready", "slot": "body"}
    missing_id = _create(client, home, "whatsapp", {**base, "templateApproved": True}, manager)
    assert missing_id.json()["error"]["details"]["reason"] == "standard_text_template_id_required"
    approved = _create(client, home, "whatsapp", {**base, "templateApproved": True, "providerTemplateId": "HX123"}, manager)
    assert approved.status_code == 201, approved.text
    assert (approved.json()["templateApproved"], approved.json()["providerTemplateId"]) == (True, "HX123")
    on_email = _create(
        client, home, "email", {**base, "appliesTo": "vehicle_ready", "templateApproved": True, "providerTemplateId": "x"}, manager
    )
    assert on_email.json()["error"]["details"]["reason"] == "standard_text_template_whatsapp_only"


def test_the_registry_and_the_preview(client, standard_text_seeds):
    home = create_dealership(client)
    seller = bearer(token(AccessRole.SALES, tenant_id=home))
    registry = client.get("/v1/standard-text-registry", headers=seller)
    assert registry.status_code == 200
    body = registry.json()
    assert len(body["placeholders"]) == 8
    offer = next(t for t in body["targets"] if (t["category"], t["appliesTo"]) == ("document", "offer"))
    assert "payment.due_date" not in offer["placeholders"] and "intro" in offer["slots"]

    def preview(text, language="de", applies_to="invoice"):
        return client.post(
            f"/v1/dealerships/{home}/standard-text-previews",
            json={"category": "document", "appliesTo": applies_to, "language": language, "body": text},
            headers=seller,
        )

    ok = preview("{{dealership.name}}: zahlbar bis {{payment.due_date}}.")
    assert ok.status_code == 200, ok.text
    due = (utcnow().date() + dt.timedelta(days=30)).strftime("%d.%m.%Y")
    assert ok.json()["text"] == f"Garage Musterbetrieb AG: zahlbar bis {due}."
    failed = preview("Bis {{payment.due_date}}", applies_to="offer")
    assert failed.status_code == 422
    assert failed.json()["error"]["details"]["placeholders"] == [{"token": "payment.due_date", "problem": "not_valid_here"}]
