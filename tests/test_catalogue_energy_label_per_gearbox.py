"""KAN-597 (Anto, 2026-11-11, option A; ADR-142): the identAPI's
Energieetikette per gearbox — class and PDF link — is stored beside the
one-class-per-year rating, and the configuration read (the stock detail's
technical data) carries it for the car's year. Over the real identAPI
adapter and the fake transport (`tests/identapi_support.py`)."""

from __future__ import annotations

import types

from app.vehicle.api.configuration import _energy_labels
from app.vehicle.models.energy_rating import ModelVariantEnergyLabel, ModelVariantEnergyRating
from tests import identapi_fixtures as fix
from tests.identapi_support import FakeAutoi
from tests.postgres_only import NEEDS_ITS_OWN_CONNECTION
from tests.test_catalogue_details_on_open import _connected, _fresh_adapter_state, _open

_fresh = _fresh_adapter_state


def _label(energy_class: str, gearbox: str | None, *, year: int = 2025, link: str | None = None) -> dict:
    row = fix.energy_label_body(energy_class=energy_class, year=year)["data"][0]
    row["gearbox"] = gearbox
    row["energyLabelLink"] = link
    return row


def _labels(db_session, variant_id):
    return [
        (row.label_year, row.gearbox, row.energy_efficiency_category, row.label_url)
        for row in db_session.query(ModelVariantEnergyLabel)
        .filter_by(model_variant_id=variant_id)
        .order_by(ModelVariantEnergyLabel.label_year, ModelVariantEnergyLabel.gearbox)
    ]


@NEEDS_ITS_OWN_CONNECTION
def test_each_gearbox_keeps_its_own_class_and_link_even_when_the_year_has_no_rating(db_session, monkeypatch):
    bodies = fix.detail_bodies()
    bodies[fix.ENERGY_LABEL_PATH] = fix.body(
        [
            _label("B", "Manuell", link="https://energielabel.example.test/m.pdf"),
            _label("C", "Automat", link="https://energielabel.example.test/a.pdf"),
            _label("D", "Automat", link="https://energielabel.example.test/again.pdf"),  # same key: first wins
            _label("Z", "Doppelkupplung"),  # a letter the list does not know: left out
        ]
    )
    s, variant = _connected(db_session, monkeypatch, FakeAutoi(api_bodies=bodies))

    _open(db_session, s, variant)

    assert _labels(db_session, variant.id) == [
        (2025, "Automat", "c", "https://energielabel.example.test/a.pdf"),
        (2025, "Manuell", "b", "https://energielabel.example.test/m.pdf"),
    ]
    # The rating keeps its rule: the gearboxes disagree, so no class for 2025 (ADR-042).
    assert db_session.query(ModelVariantEnergyRating).filter_by(model_variant_id=variant.id, rating_year=2025).count() == 0

    config = types.SimpleNamespace(catalogue_variant_id=variant.id, first_registration_date=None, model_year=2025)
    read = [label.model_dump(by_alias=True) for label in _energy_labels(db_session, config)]
    assert read == [
        {"year": 2025, "gearbox": "Automat", "category": "c", "labelUrl": "https://energielabel.example.test/a.pdf"},
        {"year": 2025, "gearbox": "Manuell", "category": "b", "labelUrl": "https://energielabel.example.test/m.pdf"},
    ]
    # Another year reads nothing — never a guess across years.
    other = types.SimpleNamespace(catalogue_variant_id=variant.id, first_registration_date=None, model_year=2024)
    assert _energy_labels(db_session, other) == []


@NEEDS_ITS_OWN_CONNECTION
def test_a_held_label_is_kept_and_only_gains_a_link_it_lacked(db_session, monkeypatch):
    bodies = fix.detail_bodies()
    bodies[fix.ENERGY_LABEL_PATH] = fix.body([_label("B", None)])
    s, variant = _connected(db_session, monkeypatch, FakeAutoi(api_bodies=bodies))
    _open(db_session, s, variant)
    assert _labels(db_session, variant.id) == [(2025, "", "b", None)]

    s.fake.api_bodies[fix.ENERGY_LABEL_PATH] = fix.body([_label("C", None, link="https://energielabel.example.test/x.pdf")])
    _open(db_session, s, variant, force=True)

    assert _labels(db_session, variant.id) == [(2025, "", "b", "https://energielabel.example.test/x.pdf")]


@NEEDS_ITS_OWN_CONNECTION
def test_a_link_that_is_not_https_is_left_out_and_the_label_kept(db_session, monkeypatch):
    """Every Betrieb's screen renders `label_url` as a link: only an https URL is stored."""

    bodies = fix.detail_bodies()
    bodies[fix.ENERGY_LABEL_PATH] = fix.body(
        [_label("B", "Manuell", link="javascript:alert(1)"), _label("C", "Automat", link="http://plain.example.test/a.pdf")]
    )
    s, variant = _connected(db_session, monkeypatch, FakeAutoi(api_bodies=bodies))

    _open(db_session, s, variant)

    assert _labels(db_session, variant.id) == [(2025, "Automat", "c", None), (2025, "Manuell", "b", None)]
