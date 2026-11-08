"""KAN-547 exit criterion 4 (as decided with Anto on 2026-11-07: no daily
job): «Test connection» stores the identAPI's captions in all four languages
— the German body the access check read, then French, Italian and English,
three more `caption_code` rows — globally, one row per (provider, group,
list, code); a second test refreshes the labels and never duplicates a row;
`integration.public.get_captions` hands them to another context as plain
data; a failure of the extra languages leaves the connection `connected`.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.core.base import utcnow
from app.integration import public
from app.integration.adapters.auto_i_dat_ident import OP_CAPTION_CODE
from app.integration.models.call_log import CallStatus, IntegrationCallLog
from app.integration.models.caption import IntegrationProviderCaption
from app.integration.models.connection import ConnectionStatus
from app.integration.services import gateway, resilience
from tests import identapi_fixtures as fix
from tests import identapi_support as fx
from tests.identapi_support import FakeAutoi


@pytest.fixture(autouse=True)
def _fresh_adapter_state():
    yield
    fx.uninstall()
    for connection_id in list(resilience._CIRCUITS):
        resilience.reset_circuit(connection_id)


class _LanguageAwareFake(FakeAutoi):
    """Answers `/caption/code` in the language asked for."""

    def _ident_api(self, request):
        if request.url.path == "/caption/code" and self.api_status.get("/caption/code", 200) == 200:
            lang = dict(request.url.params).get("lang", "de")
            self.api_bodies["/caption/code"] = fix.captions_body(lang)
        return super()._ident_api(request)


def _captions(db_session) -> dict[tuple[str, str, str], IntegrationProviderCaption]:
    return {
        (c.caption_group, c.kind, c.code): c
        for c in db_session.scalars(select(IntegrationProviderCaption).where(IntegrationProviderCaption.provider == "auto_i_dat"))
    }


def test_test_connection_stores_the_captions_in_all_four_languages(db_session, monkeypatch):
    s = fx.connected(db_session, monkeypatch, fake=_LanguageAwareFake())

    updated = gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)

    assert updated.status == ConnectionStatus.CONNECTED
    rows = _captions(db_session)
    assert set(rows) == {("type", "global", "01"), ("type", "global", "02"), ("fuel", "passenger", "3"), ("fuel", "moto", "3"), ("gearbox", "passenger", "5")}
    diesel = rows[("fuel", "passenger", "3")]
    assert (diesel.label_de, diesel.label_fr, diesel.label_it, diesel.label_en) == ("Diesel", "Diesel (fr)", "Diesel (it)", "Diesel (en)")
    assert diesel.label_short_de == "D"
    assert rows[("fuel", "moto", "3")].label_de == "Bleifrei Kat.", "the same code means something else per list"
    # one request per language: the access check's German answer is reused, not requested again
    assert [("lang", lang) for lang in ("de", "fr", "it", "en")] == [
        next(q for q in r.query if q[0] == "lang") for r in s.fake.api_requests_to("/caption/code")
    ]
    assert len(db_session.query(IntegrationCallLog).filter_by(connection_id=s.connection.id, capability=OP_CAPTION_CODE).all()) == 4


def test_a_second_test_refreshes_the_labels_and_duplicates_no_row(db_session, monkeypatch):
    s = fx.connected(db_session, monkeypatch, fake=_LanguageAwareFake())
    gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)
    before = db_session.scalar(select(func.count()).select_from(IntegrationProviderCaption))

    gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)

    assert db_session.scalar(select(func.count()).select_from(IntegrationProviderCaption)) == before == 5


def test_captions_are_read_by_another_context_as_plain_data(db_session, monkeypatch):
    s = fx.connected(db_session, monkeypatch, fake=_LanguageAwareFake())
    gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)

    fuel = public.get_captions(db_session, provider_code="auto_i_dat", caption_group="fuel", codes=["3"])

    assert [(c.kind, c.code, c.label_de, c.label_fr) for c in fuel] == [
        ("moto", "3", "Bleifrei Kat.", "Bleifrei Kat. (fr)"), ("passenger", "3", "Diesel", "Diesel (fr)"),
    ]
    assert all(isinstance(c, public.ProviderCaption) for c in fuel)
    assert public.get_captions(db_session, provider_code="auto_i_dat", caption_group="body") == []


def test_a_failure_on_the_extra_languages_keeps_the_connection_connected_and_the_german_captions(db_session, monkeypatch):
    fake = _LanguageAwareFake(api_fail_once={})
    s = fx.connected(db_session, monkeypatch, fake=fake)
    # The German access check passes; every later caption request fails (a 5xx twice).
    fake.api_bodies["/caption/code"] = fix.captions_body("de")

    original = fake._ident_api

    def failing_after_first(request):
        if request.url.path == "/caption/code" and dict(request.url.params).get("lang") != "de":
            fake.api_status["/caption/code"] = 503
        return original(request)

    monkeypatch.setattr(fake, "_ident_api", failing_after_first)
    updated = gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)

    assert updated.status == ConnectionStatus.CONNECTED
    rows = _captions(db_session)
    assert rows[("fuel", "passenger", "3")].label_de == "Diesel" and rows[("fuel", "passenger", "3")].label_fr is None
    assert len(rows) == 5, "the German vocabulary is committed before the other languages are asked for"
    # a label longer than the column is cut, not refused
    from app.integration.adapters.base import CaptionData
    from app.integration.services import captions as captions_service
    captions_service.store_captions(db_session, provider_code="auto_i_dat", lang="de", captions=[CaptionData(group="body", kind="passenger", code="9", label="x" * 300, label_short=None)])
    db_session.commit()
    assert len(_captions(db_session)[("body", "passenger", "9")].label_de) == 200
    statuses = [r.status for r in db_session.query(IntegrationCallLog).filter_by(connection_id=s.connection.id, capability=OP_CAPTION_CODE).order_by(IntegrationCallLog.created_at, IntegrationCallLog.id).all()]
    assert statuses[0] == CallStatus.SUCCESS and CallStatus.ERROR in statuses[1:]


def test_a_failure_on_one_language_keeps_every_language_stored_before_it(db_session, monkeypatch):
    """Each language is committed as it lands: Italian failing loses Italian
    and English, never the French already stored."""

    fake = _LanguageAwareFake()
    original = fake._ident_api

    def italian_fails(request):
        if request.url.path == "/caption/code" and dict(request.url.params).get("lang") == "it":
            fake.api_status["/caption/code"] = 503
        return original(request)

    monkeypatch.setattr(fake, "_ident_api", italian_fails)
    s = fx.connected(db_session, monkeypatch, fake=fake)

    updated = gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)

    assert updated.status == ConnectionStatus.CONNECTED
    diesel = _captions(db_session)[("fuel", "passenger", "3")]
    assert (diesel.label_de, diesel.label_fr, diesel.label_it, diesel.label_en) == ("Diesel", "Diesel (fr)", None, None)


def test_two_betriebe_testing_at_once_meet_on_the_shared_table_without_a_500(db_session, monkeypatch, engine):
    """The row another transaction inserted between the read and the insert
    is taken over in a savepoint (the unique constraint fires once)."""

    from sqlalchemy.orm import Session

    from app.integration.adapters.base import CaptionData
    from app.integration.services import captions as captions_service

    caption = CaptionData(group="fuel", kind="passenger", code="3", label="Diesel", label_short="D")
    real_find = captions_service._find
    calls = {"n": 0}

    def find_then_lose_the_race(db, provider_code, c):
        calls["n"] += 1
        if calls["n"] == 1:
            # Another Betrieb's test commits the same row while this one is between its read and its insert.
            with Session(bind=engine) as other:
                other.add(IntegrationProviderCaption(provider=provider_code, caption_group=c.group, kind=c.kind, code=c.code, label_fr="Diesel (fr)", fetched_at=utcnow()))
                other.commit()
            return None
        return real_find(db, provider_code, c)

    monkeypatch.setattr(captions_service, "_find", find_then_lose_the_race)
    written = captions_service.store_captions(db_session, provider_code="auto_i_dat", lang="de", captions=[caption])
    db_session.commit()

    assert written == 1 and calls["n"] == 2
    (row,) = db_session.scalars(select(IntegrationProviderCaption).where(IntegrationProviderCaption.provider == "auto_i_dat")).all()
    assert (row.label_de, row.label_fr, row.label_short_de) == ("Diesel", "Diesel (fr)", "D")
