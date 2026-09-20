"""MockAutoIDatAdapter — the fourteen new Datennamen (Configurator C-0 /
KAN-38 PR 2). No dedicated mock-adapter test file existed before this PR;
its correctness had only ever been exercised indirectly through
consumers. These tests lock in the mock's own filtering/lookup logic for
the new methods, mirroring the real adapter's method signatures exactly
(`scripts/verify_auto_i_dat.py`'s shape diff depends on that).
"""

from decimal import Decimal

from app.integration.adapters.auto_i_dat_mock import MockAutoIDatAdapter


def test_list_vehicle_kinds_returns_all_three():
    adapter = MockAutoIDatAdapter()
    assert {k.code for k in adapter.list_vehicle_kinds()} == {"01", "02", "03"}


def test_list_brands_by_fz_art():
    adapter = MockAutoIDatAdapter()
    brands = adapter.list_brands(fz_art="01")
    assert {b.name for b in brands} == {"Alfa Romeo", "Volkswagen", "BMW"}
    assert adapter.list_brands(fz_art="02") == []


def test_list_model_groups_filters_by_marken_nr_then_marke_then_mod_grp_key():
    adapter = MockAutoIDatAdapter()
    by_code = adapter.list_model_groups(marken_nr="ALF")
    assert [g.name_de for g in by_code] == ["Giulietta"]

    by_name = adapter.list_model_groups(marke="Volkswagen")
    assert [g.name_de for g in by_name] == ["Golf"]

    key = by_name[0].model_group_key
    by_key = adapter.list_model_groups(mod_grp_key=key)
    assert by_key == by_name

    assert adapter.list_model_groups(marke="Ferrari") == []


def test_list_model_groups_with_no_filter_returns_every_group():
    adapter = MockAutoIDatAdapter()
    assert len(adapter.list_model_groups()) == 3


def test_list_model_groups_short_matches_the_full_search_scope():
    adapter = MockAutoIDatAdapter()
    short = adapter.list_model_groups_short(fz_art="01", marke="BMW")
    assert [g.short_name for g in short] == ["3er"]


def test_search_vehicles_by_fz_key_typ_sch_nr_or_werkscode():
    adapter = MockAutoIDatAdapter()
    assert [m.fz_key for m in adapter.search_vehicles({"FzKey": "FZ100002"})] == ["FZ100002"]
    assert [m.fz_key for m in adapter.search_vehicles({"TypSchNr": "3EF789"})] == ["FZ100003"]
    assert [m.fz_key for m in adapter.search_vehicles({"Werkscode": "ALF14TB"})] == ["FZ100001"]
    assert adapter.search_vehicles({"FzKey": "unknown"}) == []


def test_search_vehicles_with_no_criteria_returns_every_demo_vehicle():
    adapter = MockAutoIDatAdapter()
    assert len(adapter.search_vehicles({})) == 3


def test_fetch_vehicle_prices_filters_by_year_when_given():
    adapter = MockAutoIDatAdapter()
    all_prices = adapter.fetch_vehicle_prices("FZ100001")
    assert len(all_prices) == 2
    one_year = adapter.fetch_vehicle_prices("FZ100001", year=all_prices[0].year)
    assert one_year == [all_prices[0]]


def test_fetch_grouped_values_is_keyed_by_dimension_name():
    adapter = MockAutoIDatAdapter()
    assert adapter.fetch_grouped_values(fz_art="01", gruppiert="Aufbau") == ["4", "5", "6", "8"]
    assert adapter.fetch_grouped_values(fz_art="01", gruppiert="Antrieb") == ["1", "2"]
    assert adapter.fetch_grouped_values(fz_art="01", gruppiert="Unbekannt") == []


def test_fetch_type_approvals_reads_from_master_data():
    adapter = MockAutoIDatAdapter()
    assert adapter.fetch_type_approvals("FZ100001") == ["1AB234"]
    assert adapter.fetch_type_approvals("unknown") == []


def test_lookup_plate_returns_wechselschild_as_two_rows():
    adapter = MockAutoIDatAdapter()
    single = adapter.lookup_plate("ZH123456")
    assert len(single) == 1

    wechselschild = adapter.lookup_plate("ZH999999")
    assert len(wechselschild) == 2
    assert {p.brand_name for p in wechselschild} == {"Alfa Romeo", "Volkswagen"}


def test_lookup_plate_unknown_returns_empty_not_an_error():
    adapter = MockAutoIDatAdapter()
    assert adapter.lookup_plate("XX000000") == []


def test_find_best_match_locates_the_vehicle_by_typ_sch_nr():
    adapter = MockAutoIDatAdapter()
    result = adapter.find_best_match(typ_sch_nr="2CD456", neupreis=40000, modell_bez="Golf GTI")
    assert result.vehicle.fz_key == "FZ100002"
    assert result.match_code == 1  # a ModellBez narrowed it to eindeutig


def test_find_best_match_without_modell_bez_is_best_effort():
    adapter = MockAutoIDatAdapter()
    result = adapter.find_best_match(typ_sch_nr="2CD456", neupreis=40000)
    assert result.match_code == 2


def test_find_best_match_raises_for_an_unknown_typ_sch_nr():
    adapter = MockAutoIDatAdapter()
    try:
        adapter.find_best_match(typ_sch_nr="nonexistent", neupreis=1)
    except KeyError:
        pass
    else:
        raise AssertionError("expected KeyError for an unmapped Typenschein")


def test_fetch_type_approval_data_only_euro_norm_without_getriebe_and_gaenge():
    adapter = MockAutoIDatAdapter()
    thin = adapter.fetch_type_approval_data("1AB234")
    assert thin.euro_norm == "6b"
    assert thin.co2 is None


def test_fetch_type_approval_data_full_shape_with_getriebe_and_gaenge():
    adapter = MockAutoIDatAdapter()
    full = adapter.fetch_type_approval_data("1AB234", getriebe="1", gaenge=6)
    assert full.euro_norm == "6b"
    assert full.co2 == 149
    assert full.fuel_consumption_mixed == Decimal("6.4")


def test_fetch_type_approval_data_unknown_typ_sch_nr_is_all_none():
    adapter = MockAutoIDatAdapter()
    empty = adapter.fetch_type_approval_data("unknown", getriebe="1", gaenge=6)
    assert empty.euro_norm is None
    assert empty.co2 is None


def test_fetch_option_package_contents():
    adapter = MockAutoIDatAdapter()
    contents = adapter.fetch_option_package_contents(100369)
    assert [c.description for c in contents] == ["Höhenverstellbarer Beifahrersitz", "Sitzheizung vorne"]
    assert adapter.fetch_option_package_contents(999999) == []


def test_fetch_option_exclusions():
    adapter = MockAutoIDatAdapter()
    assert adapter.fetch_option_exclusions("x", year=2020, opt_key=100017) == [100369, 100370]
    assert adapter.fetch_option_exclusions("x", year=2020, opt_key=999999) == []


def test_fetch_option_conditions_reads_aktion_code():
    adapter = MockAutoIDatAdapter()
    conditions = adapter.fetch_option_conditions("x", year=2020, opt_key=108181)
    assert [(c.description, c.aktion_code) for c in conditions] == [("Pack Family", "1")]


def test_fetch_codes_with_no_filter_returns_every_seeded_row():
    adapter = MockAutoIDatAdapter()
    assert len(adapter.fetch_codes()) == 8


def test_fetch_codes_filters_by_code_group():
    adapter = MockAutoIDatAdapter()
    codes = adapter.fetch_codes(code_groups=["112"])
    # Spec p32: 112 is 2 = 2 Takt, 4 = 4 Takt, 9 = Kein Takt. The code -> label PAIRING is pinned, not just the
    # two sets: PR 2a shipped code 2 labelled "4 Takt" with code 4 missing, and set-level assertions locked
    # that in (KAN-38 PR 2c).
    assert {c.code_nr: c.label_de for c in codes} == {"2": "2 Takt", "4": "4 Takt", "9": "Kein Takt"}


def test_fetch_codes_112_never_yields_a_drivetrain_style_label():
    # R-C-5's own trap, guarded at the fixture level: CodeGrpNr 112
    # (Antrieb for motorcycles) is a stroke count, never a drive type —
    # confirming the mock fixture itself doesn't reintroduce the mistake
    # this PR's real-adapter parsing keeps `engine_cycle` and `drivetrain`
    # as two separate spec-block columns to guard against.
    adapter = MockAutoIDatAdapter()
    codes_112 = {c.label_de for c in adapter.fetch_codes(code_groups=["112"])}
    assert codes_112 == {"2 Takt", "4 Takt", "Kein Takt"}
    assert not codes_112 & {"Hinten", "Vorne", "Allrad"}


def test_fetch_codes_carries_no_invented_short_labels():
    # The spec prints exactly one short label anywhere (010/1 "Gpw", p24); the mock slice does not contain that
    # code, so it must not invent any.
    adapter = MockAutoIDatAdapter()
    assert all(c.label_short_de is None for c in adapter.fetch_codes())
