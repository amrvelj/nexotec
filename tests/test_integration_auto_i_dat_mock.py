"""MockAutoIDatAdapter — the spec-derived invariants only (Configurator C-0 /
KAN-38). The mock's fixtures are demo data; asserting that a fixture returns
what was just written into it pins nothing, so these tests cover only what the
specification itself dictates and a consumer would otherwise get wrong.
"""

from decimal import Decimal

from app.integration.adapters.auto_i_dat_mock import MockAutoIDatAdapter


def test_lookup_plate_returns_wechselschild_as_two_rows():
    """Spec p14: a plate can match more than one vehicle (Wechselschild), so
    the result is a list and a caller must never assume one row."""

    adapter = MockAutoIDatAdapter()
    assert len(adapter.lookup_plate("ZH123456")) == 1

    wechselschild = adapter.lookup_plate("ZH999999")
    assert len(wechselschild) == 2
    assert {p.brand_name for p in wechselschild} == {"Alfa Romeo", "Volkswagen"}


def test_fetch_type_approval_data_is_thin_without_getriebe_and_gaenge_and_full_with_them():
    """Spec p28: a Typenschein alone returns only EuroNorm; the consumption
    and CO2 figures need Getriebe and Gänge as well."""

    adapter = MockAutoIDatAdapter()
    thin = adapter.fetch_type_approval_data("1AB234")
    assert thin.euro_norm == "6b"
    assert thin.co2 is None

    full = adapter.fetch_type_approval_data("1AB234", getriebe="1", gaenge=6)
    assert full.co2 == 149
    assert full.fuel_consumption_mixed == Decimal("6.4")


def test_fetch_codes_pairs_each_112_code_with_its_own_label():
    """Spec p32: 112 is 2 = 2 Takt, 4 = 4 Takt, 9 = Kein Takt. The code -> label
    PAIRING is pinned, not just the two sets: PR 2a shipped code 2 labelled
    "4 Takt" with code 4 missing, and set-level assertions locked that in."""

    adapter = MockAutoIDatAdapter()
    codes = adapter.fetch_codes(code_groups=["112"])
    assert {c.code_nr: c.label_de for c in codes} == {"2": "2 Takt", "4": "4 Takt", "9": "Kein Takt"}


def test_fetch_codes_112_never_yields_a_drivetrain_style_label():
    """R-C-5, at the fixture level: 112 (Antrieb for motorcycles) is a stroke
    count, never a drive type."""

    adapter = MockAutoIDatAdapter()
    labels = {c.label_de for c in adapter.fetch_codes(code_groups=["112"])}
    assert not labels & {"Hinten", "Vorne", "Allrad"}
