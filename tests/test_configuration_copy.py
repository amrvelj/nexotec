"""KAN-220 — copy configuration from a sister Betrieb (D-A-01, FR-A-01; Anto
2026-11-03, option 1): standard texts, the document template's header notes
and number-range patterns, one-shot, through each entity's own write path,
independent copies with no link, manager on both sides or 404, a dry run
that lists exactly what the commit does.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core.audit_model import AuditEvent
from app.core.auth import AccessRole
from app.core.permissions import ModuleCode
from app.platform.models.module_entitlement import ModuleEntitlement
from app.platform.models.number_range import NumberRange, NumberRangeDocumentType
from app.platform.models.standard_text import StandardText
from app.platform.services import configuration_copy as copy_service
from tests.membership_support import bearer, create_dealership, create_user, grant, platform_admin, token

OFFER = NumberRangeDocumentType.OFFER


def _entitle(engine, *dealerships, modules=(ModuleCode.SALES,)):
    session = sessionmaker(bind=engine)()
    try:
        for dealership in dealerships:
            held = set(
                session.scalars(
                    select(ModuleEntitlement.module).where(ModuleEntitlement.dealership_id == uuid.UUID(dealership))
                ).all()
            )
            for module in modules:
                if module not in held:
                    session.add(ModuleEntitlement(dealership_id=uuid.UUID(dealership), module=module))
        session.commit()
    finally:
        session.close()


def _pair(client, engine):
    """Two Betriebe and a user who manages both (home flag + membership flag);
    headers for acting in the target (the sister) and in the source (home)."""

    source = create_dealership(client, "Garage Quelle AG")
    target = create_dealership(client, "Garage Ziel AG", group_of=source)
    user = create_user(client, source)
    granted = grant(client, source, user, target, is_dealer_manager=True)
    assert granted.status_code == 201, granted.text
    _entitle(engine, source, target)
    in_target = bearer(token(tenant_id=target, user_id=user["id"], is_dealer_manager=True))
    in_source = bearer(token(tenant_id=source, user_id=user["id"], is_dealer_manager=True))
    return source, target, user, in_target, in_source


def _copy(
    client, target, headers, source, *, dry_run, subsets=("standard_texts", "document_template", "number_ranges")
):
    return client.post(
        f"/v1/dealerships/{target}/copy-configuration",
        json={"sourceDealershipId": source, "subsets": list(subsets), "dryRun": dry_run},
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )


def _create_text(client, dealership, headers, payload, category="document"):
    response = client.post(
        f"/v1/dealerships/{dealership}/standard-texts/{category}",
        json=payload,
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _set_header_note(client, dealership, headers, note):
    current = client.get(f"/v1/dealerships/{dealership}/document-template", headers=headers).json()
    response = client.patch(
        f"/v1/dealerships/{dealership}/document-template",
        json={"headerNoteDe": note},
        headers={**headers, "If-Match": str(current["version"])},
    )
    assert response.status_code == 200, response.text


def _set_offer_pattern(client, dealership, headers, pattern, *, start_value=1):
    ranges = client.get(f"/v1/dealerships/{dealership}/number-ranges", headers=headers).json()["items"]
    offer = next(r for r in ranges if r["documentType"] == "offer")
    response = client.patch(
        f"/v1/dealerships/{dealership}/number-ranges/offer",
        json={"pattern": pattern, "startValue": start_value},
        headers={**headers, "If-Match": str(offer["version"])},
    )
    assert response.status_code == 200, response.text


def _rows(engine, model, **where):
    session = sessionmaker(bind=engine)()
    try:
        query = select(model)
        for name, value in where.items():
            query = query.where(getattr(model, name) == value)
        return list(session.scalars(query).all())
    finally:
        session.close()


def _configure_source(client, source, in_source):
    adapted = _create_text(
        client,
        source,
        in_source,
        {"code": "offer.intro.standard", "language": "de", "title": "Unsere Einleitung", "body": "Grüezi und danke."},
    )
    own_de = _create_text(
        client,
        source,
        in_source,
        {"language": "de", "title": "Hauseigen", "body": "Eigener Text.", "appliesTo": "offer", "slot": "closing"},
    )
    own_fr = _create_text(
        client,
        source,
        in_source,
        {"code": own_de["code"], "language": "fr", "title": "Maison", "body": "Texte propre."},
    )
    _set_header_note(client, source, in_source, "Ihr Partner seit 1972")
    _set_offer_pattern(client, source, in_source, "OF-{counter:5}", start_value=500)
    return adapted, own_de, own_fr


# --- who may copy --------------------------------------------------------------------------


def test_the_copy_is_404_unless_the_caller_manages_both_betriebe(client, engine, standard_text_seeds):
    source, target, user, in_target, _ = _pair(client, engine)
    # A manager of the target only: the source does not exist for them.
    target_only = create_user(client, target)
    their_headers = bearer(token(tenant_id=target, user_id=target_only["id"], is_dealer_manager=True))
    assert _copy(client, target, their_headers, source, dry_run=True).status_code == 404
    # A colleague without the flag: 404 on the target, never a 403.
    seller = bearer(token(AccessRole.SALES, tenant_id=target, user_id=user["id"]))
    assert _copy(client, target, seller, source, dry_run=True).status_code == 404
    # The path names a Betrieb other than the active one: 404 (writes need the switch).
    in_source = bearer(token(tenant_id=source, user_id=user["id"], is_dealer_manager=True))
    assert _copy(client, target, in_source, source, dry_run=True).status_code == 404
    # Copying a Betrieb onto itself, or from one that does not exist: 404.
    assert _copy(client, target, in_target, target, dry_run=True).status_code == 404
    assert _copy(client, target, in_target, str(uuid.uuid4()), dry_run=True).status_code == 404
    # The manager of both may.
    assert _copy(client, target, in_target, source, dry_run=True).status_code == 200


def test_a_membership_without_the_manager_flag_is_not_enough(client, engine, standard_text_seeds):
    source = create_dealership(client, "Garage Quelle AG")
    target = create_dealership(client, "Garage Ziel AG", group_of=source)
    user = create_user(client, target)  # manager of the target (home)
    assert grant(client, target, user, source, is_dealer_manager=False).status_code == 201
    headers = bearer(token(tenant_id=target, user_id=user["id"], is_dealer_manager=True))
    assert _copy(client, target, headers, source, dry_run=True).status_code == 404
    listed = client.get(f"/v1/dealerships/{target}/copy-configuration/sources", headers=headers)
    assert listed.status_code == 200 and listed.json()["items"] == []


def test_the_sources_are_the_betriebe_the_caller_manages_besides_the_target(client, engine, standard_text_seeds):
    source, target, user, in_target, _ = _pair(client, engine)
    unrelated = create_dealership(client, "Garage Fremd AG")
    listed = client.get(f"/v1/dealerships/{target}/copy-configuration/sources", headers=in_target)
    assert listed.status_code == 200, listed.text
    assert listed.json()["items"] == [{"id": source, "legalName": "Garage Quelle AG"}]
    assert unrelated not in {item["id"] for item in listed.json()["items"]}
    seller = bearer(token(AccessRole.SALES, tenant_id=target, user_id=user["id"]))
    assert client.get(f"/v1/dealerships/{target}/copy-configuration/sources", headers=seller).status_code == 404


def test_platform_staff_may_copy_between_any_two_betriebe(client, engine, standard_text_seeds):
    source, target, _, _, in_source = _pair(client, engine)
    _configure_source(client, source, in_source)
    response = _copy(client, target, platform_admin(), source, dry_run=False, subsets=("document_template",))
    assert response.status_code == 200, response.text
    assert response.json()["counts"]["created"] == 1


# --- what is copied ----------------------------------------------------------------------------


def test_the_dry_run_lists_exactly_what_the_commit_then_does_and_writes_nothing(client, engine, standard_text_seeds):
    source, target, _, in_target, in_source = _pair(client, engine)
    _configure_source(client, source, in_source)
    audit_before = len(_rows(engine, AuditEvent, tenant_id=uuid.UUID(target)))

    preview = _copy(client, target, in_target, source, dry_run=True)
    assert preview.status_code == 200, preview.text
    assert preview.json()["dryRun"] is True
    # Nothing was written: no text, no template, no audit row, the range unchanged.
    assert _rows(engine, StandardText, dealership_id=uuid.UUID(target)) == []
    assert len(_rows(engine, AuditEvent, tenant_id=uuid.UUID(target))) == audit_before
    offer_before = [r for r in _rows(engine, NumberRange, tenant_id=uuid.UUID(target)) if r.document_type == OFFER]
    assert all(r.pattern != "OF-{counter:5}" for r in offer_before)

    committed = _copy(client, target, in_target, source, dry_run=False)
    assert committed.status_code == 200, committed.text
    strip = lambda body: {k: v for k, v in body.items() if k != "dryRun"}
    assert strip(committed.json()) == strip(preview.json())

    outcomes = {(i["subset"], i["key"], i["language"]): i["outcome"] for i in committed.json()["items"]}
    assert outcomes[("standard_texts", "offer.intro.standard", "de")] == "created"
    assert outcomes[("document_template", "header_notes", None)] == "created"
    assert outcomes[("number_ranges", "offer", None)] == "overwritten"


def test_copies_are_independent_and_point_at_the_seed_never_at_the_source(client, engine, standard_text_seeds):
    source, target, _, in_target, in_source = _pair(client, engine)
    adapted, own_de, own_fr = _configure_source(client, source, in_source)
    assert _copy(client, target, in_target, source, dry_run=False).status_code == 200

    copied = {(r.code, r.language.value): r for r in _rows(engine, StandardText, dealership_id=uuid.UUID(target))}
    intro = copied[("offer.intro.standard", "de")]
    # The adaptation points at the platform seed — the same seed the source's does.
    assert str(intro.overridden_from_id) == adapted["overriddenFromId"]
    assert intro.body == "Grüezi und danke."
    source_ids = {adapted["id"], own_de["id"], own_fr["id"]}
    for row in copied.values():
        assert str(row.id) not in source_ids
        assert row.overridden_from_id is None or str(row.overridden_from_id) not in source_ids
    # The Betrieb-own text keeps its code in both languages, as the target's own.
    assert copied[(own_de["code"], "de")].body == "Eigener Text."
    assert copied[(own_de["code"], "fr")].body == "Texte propre."
    assert copied[(own_de["code"], "fr")].overridden_from_id is None

    # A later edit on the source reaches nothing in the target.
    patched = client.patch(
        f"/v1/dealerships/{source}/standard-texts/document/{adapted['id']}",
        json={"body": "Neu in der Quelle."},
        headers={**in_source, "If-Match": "1"},
    )
    assert patched.status_code == 200, patched.text
    again = {(r.code, r.language.value): r for r in _rows(engine, StandardText, dealership_id=uuid.UUID(target))}
    assert again[("offer.intro.standard", "de")].body == "Grüezi und danke."


def test_a_second_copy_overwrites_the_targets_texts_instead_of_duplicating_them(client, engine, standard_text_seeds):
    source, target, _, in_target, in_source = _pair(client, engine)
    adapted, own_de, _ = _configure_source(client, source, in_source)
    assert _copy(client, target, in_target, source, dry_run=False).status_code == 200
    count = len(_rows(engine, StandardText, dealership_id=uuid.UUID(target)))

    client.patch(
        f"/v1/dealerships/{source}/standard-texts/document/{adapted['id']}",
        json={"body": "Zweite Fassung."},
        headers={**in_source, "If-Match": "1"},
    )
    second = _copy(client, target, in_target, source, dry_run=False, subsets=("standard_texts",))
    assert second.status_code == 200, second.text
    outcomes = {(i["key"], i["language"]): i["outcome"] for i in second.json()["items"]}
    assert outcomes[("offer.intro.standard", "de")] == "overwritten"
    assert outcomes[(own_de["code"], "de")] == "unchanged"
    rows = _rows(engine, StandardText, dealership_id=uuid.UUID(target))
    assert len(rows) == count
    assert next(r for r in rows if r.code == "offer.intro.standard").body == "Zweite Fassung."


def test_counters_are_never_copied_and_a_frozen_range_is_skipped(client, engine, standard_text_seeds):
    source, target, _, in_target, in_source = _pair(client, engine)
    _configure_source(client, source, in_source)
    session = sessionmaker(bind=engine)()
    try:
        source_offer = session.scalars(
            select(NumberRange).where(NumberRange.tenant_id == uuid.UUID(source), NumberRange.document_type == OFFER)
        ).one()
        source_offer.last_value = 812
        source_offer.last_year = 2026
        session.commit()
    finally:
        session.close()

    assert _copy(client, target, in_target, source, dry_run=False, subsets=("number_ranges",)).status_code == 200
    target_offer = next(r for r in _rows(engine, NumberRange, tenant_id=uuid.UUID(target)) if r.document_type == OFFER)
    assert (target_offer.pattern, target_offer.start_value) == ("OF-{counter:5}", 500)
    assert target_offer.last_value is None and target_offer.last_year is None and target_offer.frozen_at is None

    # Once the target's range has handed out a number it is frozen: skipped, reported, untouched.
    _set_offer_pattern(client, source, in_source, "AN-{counter:6}")
    session = sessionmaker(bind=engine)()
    try:
        row = session.get(NumberRange, target_offer.id)
        from app.core.base import utcnow

        row.frozen_at = utcnow()
        row.last_value = 1
        session.commit()
    finally:
        session.close()
    report = _copy(client, target, in_target, source, dry_run=False, subsets=("number_ranges",)).json()
    offer_item = next(i for i in report["items"] if i["key"] == "offer")
    assert (offer_item["outcome"], offer_item["reason"]) == ("skipped", "number_range_frozen")
    frozen = next(r for r in _rows(engine, NumberRange, tenant_id=uuid.UUID(target)) if r.document_type == OFFER)
    assert frozen.pattern == "OF-{counter:5}" and frozen.last_value == 1


def test_only_the_ranges_of_the_modules_the_target_holds_are_copied(client, engine, standard_text_seeds):
    source, target, _, in_target, _ = _pair(client, engine)
    report = _copy(client, target, in_target, source, dry_run=True, subsets=("number_ranges",)).json()
    keys = {item["key"] for item in report["items"]}
    assert keys == {"offer", "order_confirmation", "sales_contract", "purchase_contract", "stock_number"}


def test_every_copied_entity_is_audited_and_one_summary_row_names_the_source(client, engine, standard_text_seeds):
    source, target, user, in_target, in_source = _pair(client, engine)
    _configure_source(client, source, in_source)
    assert _copy(client, target, in_target, source, dry_run=False).status_code == 200

    events = _rows(engine, AuditEvent, tenant_id=uuid.UUID(target))
    summaries = [e for e in events if e.action == copy_service.SUMMARY_ACTION]
    assert len(summaries) == 1
    summary = summaries[0]
    assert (summary.entity_type, str(summary.entity_id), str(summary.actor_id)) == ("dealership", target, user["id"])
    assert summary.after["sourceDealershipId"] == source
    assert summary.after["counts"]["created"] >= 4
    assert {e.entity_type for e in events if e.action != copy_service.SUMMARY_ACTION} >= {
        "standard_text",
        "document_template",
        "number_range",
    }
    created_texts = [e for e in events if e.entity_type == "standard_text" and e.action == "create"]
    assert len(created_texts) == 3


def test_a_location_variant_is_not_copied(client, engine, standard_text_seeds):
    source, target, _, in_target, in_source = _pair(client, engine)
    locations = client.get(f"/v1/dealerships/{source}/locations", headers=in_source).json()["items"]
    _create_text(
        client,
        source,
        in_source,
        {
            "code": "offer.intro.standard",
            "language": "de",
            "title": "Filiale",
            "body": "Nur hier.",
            "locationId": locations[0]["id"],
        },
    )
    report = _copy(client, target, in_target, source, dry_run=False, subsets=("standard_texts",)).json()
    assert report["items"] == []
    assert _rows(engine, StandardText, dealership_id=uuid.UUID(target)) == []


def test_a_text_the_target_cannot_take_is_skipped_and_the_rest_is_copied(client, engine, standard_text_seeds):
    from app.core.i18n import SwissLanguage
    from app.platform.models.standard_text import (
        StandardTextAppliesTo,
        StandardTextScope,
        StandardTextSlot,
        StandardTextSource,
    )
    from app.platform.models.standard_text_delegation import StandardTextCategory

    source, target, _, in_target, in_source = _pair(client, engine)
    _set_header_note(client, source, in_source, "Kopfzeile")
    # A row an older release saved with a token the closed registry now refuses.
    session = sessionmaker(bind=engine)()
    try:
        session.add(
            StandardText(
                dealership_id=uuid.UUID(source),
                scope=StandardTextScope.DEALERSHIP,
                code="offer.closing.legacy",
                category=StandardTextCategory.DOCUMENT,
                applies_to=StandardTextAppliesTo.OFFER,
                slot=StandardTextSlot.CLOSING,
                language=SwissLanguage.DE,
                title="Alt",
                body="Bis {{nicht.bekannt}}",
                source=StandardTextSource.DEALER,
                version=1,
            )
        )
        session.commit()
    finally:
        session.close()

    report = _copy(client, target, in_target, source, dry_run=False, subsets=("standard_texts", "document_template"))
    assert report.status_code == 200, report.text
    items = {i["key"]: i for i in report.json()["items"]}
    assert items["offer.closing.legacy"]["outcome"] == "skipped"
    assert items["offer.closing.legacy"]["reason"] == "standard_text_placeholder_invalid"
    assert items["header_notes"]["outcome"] == "created"
    assert _rows(engine, StandardText, dealership_id=uuid.UUID(target)) == []


def test_a_copied_default_that_replaces_the_targets_own_is_listed_in_the_preview(client, engine, standard_text_seeds):
    source, target, _, in_target, in_source = _pair(client, engine)
    in_target_own = _create_text(
        client,
        target,
        in_target,
        {
            "language": "de",
            "title": "Ziel-Schluss",
            "body": "Ziel.",
            "appliesTo": "offer",
            "slot": "closing",
            "isDefault": True,
        },
    )
    _create_text(
        client,
        source,
        in_source,
        {
            "language": "de",
            "title": "Quelle-Schluss",
            "body": "Quelle.",
            "appliesTo": "offer",
            "slot": "closing",
            "isDefault": True,
        },
    )
    preview = _copy(client, target, in_target, source, dry_run=True, subsets=("standard_texts",)).json()
    replaced = [i for i in preview["items"] if i["reason"] == "default_replaced"]
    assert [(i["key"], i["outcome"]) for i in replaced] == [(in_target_own["code"], "overwritten")]
    committed = _copy(client, target, in_target, source, dry_run=False, subsets=("standard_texts",)).json()
    assert committed["items"] == preview["items"]
    own = next(
        r for r in _rows(engine, StandardText, dealership_id=uuid.UUID(target)) if r.code == in_target_own["code"]
    )
    assert own.is_default is False


def test_an_empty_source_note_never_clears_the_targets(client, engine, standard_text_seeds):
    source, target, _, in_target, _ = _pair(client, engine)
    _set_header_note(client, target, in_target, "Eigene Kopfzeile")
    report = _copy(client, target, in_target, source, dry_run=False, subsets=("document_template",)).json()
    assert [(i["key"], i["outcome"]) for i in report["items"]] == [("header_notes", "unchanged")]
    kept = client.get(f"/v1/dealerships/{target}/document-template", headers=in_target).json()
    assert kept["headerNoteDe"] == "Eigene Kopfzeile"


def test_the_review_state_of_an_adaptation_travels_with_the_copy(client, engine, standard_text_seeds):
    from app.platform.services import standard_text as text_service

    source, target, _, in_target, in_source = _pair(client, engine)
    adapted, _, _ = _configure_source(client, source, in_source)
    # The platform edits the seed after the source adapted it: the source's text reads "changed".
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        seed = text_service.get_platform_seed_or_404(session, uuid.UUID(adapted["overriddenFromId"]), for_update=True)
        text_service.update_platform_seed(
            session,
            seed=seed,
            changes={"body": "Neue Einleitung der Plattform."},
            if_match_version=seed.version,
            actor_id=None,
        )
    finally:
        session.close()
    assert _copy(client, target, in_target, source, dry_run=False, subsets=("standard_texts",)).status_code == 200
    listed = client.get(f"/v1/dealerships/{target}/standard-texts", headers=in_target).json()["items"]
    copied = next(i for i in listed if i["code"] == "offer.intro.standard" and i["source"] == "dealer")
    assert copied["seedChanged"] is True


def test_a_copy_that_changes_no_range_writes_no_range_row(client, engine, standard_text_seeds):
    source, target, _, in_target, _ = _pair(client, engine)
    session = sessionmaker(bind=engine)()
    try:
        for row in session.scalars(select(NumberRange).where(NumberRange.tenant_id == uuid.UUID(target))).all():
            session.delete(row)
        session.commit()
    finally:
        session.close()
    report = _copy(client, target, in_target, source, dry_run=False, subsets=("number_ranges",)).json()
    assert {i["outcome"] for i in report["items"]} == {"unchanged"}
    assert _rows(engine, NumberRange, tenant_id=uuid.UUID(target)) == []
