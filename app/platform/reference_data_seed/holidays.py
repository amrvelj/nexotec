"""The cantonal holiday calendars (KAN-204) — one source for the seed
migration and the tests, like the business lists beside it.

**Source.** SECO, "Feiertage Schweiz" (Anhang zur Wegleitung zum
Arbeitsgesetz, Auszug ArG/ArGV 1–5), table "Stand am 12.12.2024":
https://www.seco.admin.ch/dam/de/sd-web/x5LtHPJdNis5/Anhang-Feiertage-Schweiz-Auszug-ArG-ArGV1-5-SECO-SB-2024-DE.pdf
It lists, per canton, the cantonal holidays equal to Sundays under the Labour
Act (Art. 20a para. 1 ArG) and the federal 1 August, with the sub-cantonal
exceptions on its last pages (Aargau, Appenzell I.Rh., Appenzell A.Rh.,
Fribourg, Neuchâtel, Solothurn). Every row below is transcribed from it; each
canton's set was checked against the eight-day ceiling of Art. 20a ArG.

The date rules the table names without a date:
- Näfelser Fahrt (Glarus): the first Thursday of April, one week later when
  that Thursday falls in Holy Week — the Glarus government's rule (published
  for 2026 as 9 April, Südostschweiz, "Gedenktag: Wieso das Datum der
  Näfelser Fahrt auf eurem Handy wahrscheinlich falsch ist").
- Jeûne genevois: "le jeudi qui suit le premier dimanche du mois de
  septembre" (RSG J 1 45, Loi sur les jours fériés, note a to art. 1 al. 1
  let. g).
- Lundi du Jeûne fédéral (Vaud): the Monday after the Federal Fast, which is
  the third Sunday of September.
- Appenzell I.Rh. "Der Stephanstag wird nur gefeiert, wenn dadurch nicht drei
  Ruhetage aufeinander folgen" — read by the Standeskommission as "not on a
  Saturday or a Tuesday" (Bericht Feiertage, 29.9.2015, ai.ch); Appenzell
  A.Rh. "nicht …, wenn der Weihnachtstag auf einen Montag oder Freitag
  fällt" — the same two days for 26 December.

**Frozen (KAN-517, ADR-133).** The seed migration `83d8634ff337` imports
these rows, and it is immutable, so an edit here would give a fresh database
other rows than a deployed one. `tests/architecture/test_migration_seed_sources_are_frozen.py`
pins them by digest. A change is a new data migration with literal rows, and
the new edition gets a name of its own.
"""

from __future__ import annotations

from typing import NamedTuple

SOURCE = (
    "SECO, Feiertage Schweiz (Anhang, Auszug ArG/ArGV 1-5), Stand am 12.12.2024 — "
    "kantonale Feiertage, die den Sonntagen gleichgestellt sind (Art. 20a Abs. 1 ArG), und der 1. August"
)


class HolidayDefinition(NamedTuple):
    rule: str  # HolidayRule value
    month: int | None
    day: int | None
    easter_offset_days: int | None
    label_de: str
    label_fr: str
    label_it: str
    label_en: str


def _fixed(month: int, day: int, de: str, fr: str, it: str, en: str) -> HolidayDefinition:
    return HolidayDefinition("fixed_date", month, day, None, de, fr, it, en)


def _easter(offset: int, de: str, fr: str, it: str, en: str) -> HolidayDefinition:
    return HolidayDefinition("easter_offset", None, None, offset, de, fr, it, en)


def _named(rule: str, de: str, fr: str, it: str, en: str) -> HolidayDefinition:
    return HolidayDefinition(rule, None, None, None, de, fr, it, en)


# In calendar order; a calendar's rows keep this order (`sort_order`).
HOLIDAYS: dict[str, HolidayDefinition] = {
    "new_year": _fixed(1, 1, "Neujahrstag", "Nouvel an", "Capodanno", "New Year's Day"),
    "berchtold": _fixed(1, 2, "Berchtoldstag", "Saint-Berchtold", "San Bertoldo", "Berchtold's Day"),
    "epiphany": _fixed(1, 6, "Dreikönigstag", "Epiphanie", "Epifania", "Epiphany"),
    "republic_neuchatel": _fixed(
        3, 1, "Jahrestag der Republik Neuenburg", "Instauration de la République", "Instaurazione della Repubblica",
        "Proclamation of the Republic",
    ),
    "saint_joseph": _fixed(3, 19, "Josephstag", "Saint-Joseph", "San Giuseppe", "St Joseph's Day"),
    "naefelser_fahrt": _named(
        "naefelser_fahrt", "Näfelser Fahrt (Fahrtsfest)", "Näfelser Fahrt", "Näfelser Fahrt", "Näfels Pilgrimage"
    ),
    "good_friday": _easter(-2, "Karfreitag", "Vendredi saint", "Venerdì santo", "Good Friday"),
    "easter_monday": _easter(1, "Ostermontag", "Lundi de Pâques", "Lunedì di Pasqua", "Easter Monday"),
    "labour_day": _fixed(5, 1, "Tag der Arbeit", "Fête du travail", "Festa del lavoro", "Labour Day"),
    "ascension": _easter(39, "Auffahrt", "Ascension", "Ascensione", "Ascension Day"),
    "whit_monday": _easter(50, "Pfingstmontag", "Lundi de Pentecôte", "Lunedì di Pentecoste", "Whit Monday"),
    "corpus_christi": _easter(60, "Fronleichnam", "Fête-Dieu", "Corpus Domini", "Corpus Christi"),
    "national_day": _fixed(8, 1, "Bundesfeiertag", "Fête nationale", "Festa nazionale", "Swiss National Day"),
    "assumption": _fixed(8, 15, "Mariä Himmelfahrt", "Assomption", "Assunzione", "Assumption Day"),
    "jeune_genevois": _named("jeune_genevois", "Genfer Bettag", "Jeûne genevois", "Digiuno ginevrino", "Geneva Fast"),
    "federal_fast_monday": _named(
        "federal_fast_monday", "Bettagsmontag", "Lundi du Jeûne fédéral", "Lunedì del Digiuno federale",
        "Federal Fast Monday",
    ),
    "all_saints": _fixed(11, 1, "Allerheiligen", "Toussaint", "Ognissanti", "All Saints' Day"),
    "immaculate_conception": _fixed(
        12, 8, "Mariä Empfängnis", "Immaculée Conception", "Immacolata Concezione", "Immaculate Conception"
    ),
    "christmas": _fixed(12, 25, "Weihnachtstag", "Noël", "Natale", "Christmas Day"),
    "st_stephen": _fixed(12, 26, "Stephanstag", "Saint-Etienne", "Santo Stefano", "St Stephen's Day"),
    "restoration_geneva": _fixed(
        12, 31, "Wiederherstellung der Republik", "Restauration de la République", "Restaurazione della Repubblica",
        "Restoration of the Republic",
    ),
}

_ORDER = {code: index for index, code in enumerate(HOLIDAYS)}


class CalendarHoliday(NamedTuple):
    code: str
    condition: str = "always"  # HolidayCondition value
    afternoon_only: bool = False


class CalendarSeed(NamedTuple):
    code: str
    canton: str
    label_de: str
    label_fr: str
    label_it: str
    label_en: str
    holidays: tuple[CalendarHoliday, ...]


def _h(*codes: str) -> tuple[CalendarHoliday, ...]:
    return tuple(CalendarHoliday(code) for code in codes)


def _sorted(entries: tuple[CalendarHoliday, ...]) -> tuple[CalendarHoliday, ...]:
    return tuple(sorted(entries, key=lambda e: _ORDER[e.code]))


_ONLY_MONDAY = "only_on_monday"
_NOT_TUE_SAT = "not_on_tuesday_or_saturday"

_NE_COMMON = (
    CalendarHoliday("new_year"),
    CalendarHoliday("berchtold", _ONLY_MONDAY),
    CalendarHoliday("republic_neuchatel"),
    CalendarHoliday("good_friday"),
    CalendarHoliday("labour_day"),
    CalendarHoliday("ascension"),
    CalendarHoliday("national_day"),
    CalendarHoliday("christmas"),
    CalendarHoliday("st_stephen", _ONLY_MONDAY),
)

# (code, canton, de, fr, it, en, holidays) — ordered by canton code, a
# canton's variants after it.
CALENDARS: tuple[CalendarSeed, ...] = tuple(
    CalendarSeed(code, canton, de, fr, it, en, _sorted(holidays))
    for code, canton, de, fr, it, en, holidays in (
        (
            "AG-AARAU", "AG",
            "Aargau – Bezirke Aarau, Brugg, Kulm, Lenzburg und Zofingen; Gemeinde Bergdietikon",
            "Argovie – districts d'Aarau, Brugg, Kulm, Lenzbourg et Zofingue; commune de Bergdietikon",
            "Argovia – distretti di Aarau, Brugg, Kulm, Lenzburg e Zofingen; comune di Bergdietikon",
            "Aargau – districts of Aarau, Brugg, Kulm, Lenzburg and Zofingen; municipality of Bergdietikon",
            _h("new_year", "berchtold", "good_friday", "easter_monday", "ascension", "whit_monday", "national_day",
               "christmas", "st_stephen"),
        ),
        (
            "AG-BADEN", "AG",
            "Aargau – Bezirk Baden (ohne Bergdietikon)",
            "Argovie – district de Baden (sans Bergdietikon)",
            "Argovia – distretto di Baden (senza Bergdietikon)",
            "Aargau – Baden district (excluding Bergdietikon)",
            _h("new_year", "good_friday", "easter_monday", "ascension", "whit_monday", "corpus_christi",
               "national_day", "christmas", "st_stephen"),
        ),
        (
            "AG-BREMGARTEN", "AG",
            "Aargau – Bezirk Bremgarten",
            "Argovie – district de Bremgarten",
            "Argovia – distretto di Bremgarten",
            "Aargau – Bremgarten district",
            _h("new_year", "good_friday", "ascension", "corpus_christi", "national_day", "assumption", "all_saints",
               "christmas", "st_stephen"),
        ),
        (
            "AG-LAUFENBURG-MURI", "AG",
            ("Aargau – Bezirke Laufenburg und Muri; Gemeinden Hellikon, Mumpf, Obermumpf, Schupfart, Stein und "
            "Wegenstetten"),
            ("Argovie – districts de Laufenburg et de Muri; communes de Hellikon, Mumpf, Obermumpf, Schupfart, Stein "
            "et Wegenstetten"),
            ("Argovia – distretti di Laufenburg e Muri; comuni di Hellikon, Mumpf, Obermumpf, Schupfart, Stein e "
            "Wegenstetten"),
            ("Aargau – Laufenburg and Muri districts; municipalities of Hellikon, Mumpf, Obermumpf, Schupfart, Stein "
            "and Wegenstetten"),
            _h("new_year", "good_friday", "ascension", "corpus_christi", "national_day", "assumption", "all_saints",
               "immaculate_conception", "christmas"),
        ),
        (
            "AG-RHEINFELDEN", "AG",
            "Aargau – Gemeinden Kaiseraugst, Magden, Möhlin, Olsberg, Rheinfelden, Wallbach, Zeiningen und Zuzgen",
            "Argovie – communes de Kaiseraugst, Magden, Möhlin, Olsberg, Rheinfelden, Wallbach, Zeiningen et Zuzgen",
            "Argovia – comuni di Kaiseraugst, Magden, Möhlin, Olsberg, Rheinfelden, Wallbach, Zeiningen e Zuzgen",
            ("Aargau – municipalities of Kaiseraugst, Magden, Möhlin, Olsberg, Rheinfelden, Wallbach, Zeiningen and "
            "Zuzgen"),
            _h("new_year", "good_friday", "easter_monday", "ascension", "whit_monday", "national_day", "all_saints",
               "christmas", "st_stephen"),
        ),
        (
            "AG-ZURZACH", "AG",
            "Aargau – Bezirk Zurzach",
            "Argovie – district de Zurzach",
            "Argovia – distretto di Zurzach",
            "Aargau – Zurzach district",
            _h("new_year", "berchtold", "good_friday", "ascension", "corpus_christi", "national_day", "all_saints",
               "christmas", "st_stephen"),
        ),
        (
            "AI", "AI", "Appenzell Innerrhoden", "Appenzell Rhodes-Intérieures", "Appenzello Interno",
            "Appenzell Innerrhoden",
            _h("new_year", "good_friday", "easter_monday", "ascension", "whit_monday", "corpus_christi",
               "national_day", "christmas") + (CalendarHoliday("st_stephen", _NOT_TUE_SAT),),
        ),
        (
            "AR", "AR", "Appenzell Ausserrhoden", "Appenzell Rhodes-Extérieures", "Appenzello Esterno",
            "Appenzell Ausserrhoden",
            _h("new_year", "good_friday", "easter_monday", "ascension", "whit_monday", "national_day", "christmas")
            + (CalendarHoliday("st_stephen", _NOT_TUE_SAT),),
        ),
        (
            "BE", "BE", "Bern", "Berne", "Berna", "Bern",
            _h("new_year", "berchtold", "good_friday", "easter_monday", "ascension", "whit_monday", "national_day",
               "christmas", "st_stephen"),
        ),
        (
            "BL", "BL", "Basel-Landschaft", "Bâle-Campagne", "Basilea Campagna", "Basel-Landschaft",
            _h("new_year", "good_friday", "easter_monday", "labour_day", "ascension", "whit_monday", "national_day",
               "christmas", "st_stephen"),
        ),
        (
            "BS", "BS", "Basel-Stadt", "Bâle-Ville", "Basilea Città", "Basel-Stadt",
            _h("new_year", "good_friday", "easter_monday", "labour_day", "ascension", "whit_monday", "national_day",
               "christmas", "st_stephen"),
        ),
        (
            "FR-CATHOLIC", "FR",
            "Freiburg – Gemeinden mit vorwiegend katholischer Bevölkerung",
            "Fribourg – communes à population majoritairement catholique romaine",
            "Friburgo – comuni a popolazione prevalentemente cattolica",
            "Fribourg – predominantly Catholic municipalities",
            _h("new_year", "good_friday", "ascension", "corpus_christi", "national_day", "assumption", "all_saints",
               "immaculate_conception", "christmas"),
        ),
        (
            "FR-REFORMED", "FR",
            "Freiburg – Gemeinden mit vorwiegend reformierter Bevölkerung",
            "Fribourg – communes à population majoritairement évangélique réformée",
            "Friburgo – comuni a popolazione prevalentemente riformata",
            "Fribourg – predominantly Reformed municipalities",
            _h("new_year", "berchtold", "good_friday", "easter_monday", "ascension", "whit_monday", "national_day",
               "christmas", "st_stephen"),
        ),
        (
            "GE", "GE", "Genf", "Genève", "Ginevra", "Geneva",
            _h("new_year", "good_friday", "easter_monday", "ascension", "whit_monday", "national_day",
               "jeune_genevois", "christmas", "restoration_geneva"),
        ),
        (
            "GL", "GL", "Glarus", "Glaris", "Glarona", "Glarus",
            _h("new_year", "naefelser_fahrt", "good_friday", "easter_monday", "ascension", "national_day",
               "all_saints", "christmas", "st_stephen"),
        ),
        (
            "GR", "GR", "Graubünden", "Grisons", "Grigioni", "Graubünden",
            _h("new_year", "good_friday", "easter_monday", "ascension", "whit_monday", "national_day", "christmas",
               "st_stephen"),
        ),
        (
            "JU", "JU", "Jura", "Jura", "Giura", "Jura",
            _h("new_year", "good_friday", "easter_monday", "labour_day", "ascension", "whit_monday",
               "corpus_christi", "national_day", "christmas"),
        ),
        (
            "LU", "LU", "Luzern", "Lucerne", "Lucerna", "Lucerne",
            _h("new_year", "good_friday", "ascension", "corpus_christi", "national_day", "assumption", "all_saints",
               "christmas", "st_stephen"),
        ),
        (
            "NE", "NE",
            "Neuenburg (ohne Le Landeron)",
            "Neuchâtel (sans Le Landeron)",
            "Neuchâtel (senza Le Landeron)",
            "Neuchâtel (excluding Le Landeron)",
            _NE_COMMON,
        ),
        (
            "NE-LANDERON", "NE",
            "Neuenburg – Gemeinde Le Landeron",
            "Neuchâtel – commune du Landeron",
            "Neuchâtel – comune di Le Landeron",
            "Neuchâtel – municipality of Le Landeron",
            _NE_COMMON + (CalendarHoliday("corpus_christi"),),
        ),
        (
            "NW", "NW", "Nidwalden", "Nidwald", "Nidvaldo", "Nidwalden",
            _h("new_year", "good_friday", "ascension", "corpus_christi", "national_day", "assumption", "all_saints",
               "immaculate_conception", "christmas"),
        ),
        (
            "OW", "OW", "Obwalden", "Obwald", "Obvaldo", "Obwalden",
            _h("new_year", "good_friday", "ascension", "corpus_christi", "national_day", "assumption", "all_saints",
               "immaculate_conception", "christmas"),
        ),
        (
            "SG", "SG", "St. Gallen", "Saint-Gall", "San Gallo", "St. Gallen",
            _h("new_year", "good_friday", "easter_monday", "ascension", "whit_monday", "national_day", "all_saints",
               "christmas", "st_stephen"),
        ),
        (
            "SH", "SH", "Schaffhausen", "Schaffhouse", "Sciaffusa", "Schaffhausen",
            _h("new_year", "good_friday", "easter_monday", "labour_day", "ascension", "whit_monday", "national_day",
               "christmas", "st_stephen"),
        ),
        (
            "SO", "SO",
            "Solothurn (ohne Bezirk Bucheggberg)",
            "Soleure (sans le district du Bucheggberg)",
            "Soletta (senza il distretto di Bucheggberg)",
            "Solothurn (excluding Bucheggberg district)",
            _h("new_year", "good_friday", "ascension", "corpus_christi", "national_day", "assumption", "all_saints",
               "christmas") + (CalendarHoliday("labour_day", afternoon_only=True),),
        ),
        (
            "SO-BUCHEGGBERG", "SO",
            "Solothurn – Bezirk Bucheggberg",
            "Soleure – district du Bucheggberg",
            "Soletta – distretto di Bucheggberg",
            "Solothurn – Bucheggberg district",
            _h("new_year", "good_friday", "ascension", "national_day", "christmas")
            + (CalendarHoliday("labour_day", afternoon_only=True),),
        ),
        (
            "SZ", "SZ", "Schwyz", "Schwytz", "Svitto", "Schwyz",
            _h("new_year", "saint_joseph", "good_friday", "ascension", "corpus_christi", "national_day",
               "assumption", "all_saints", "christmas"),
        ),
        (
            "TG", "TG", "Thurgau", "Thurgovie", "Turgovia", "Thurgau",
            _h("new_year", "berchtold", "good_friday", "easter_monday", "ascension", "whit_monday", "national_day",
               "christmas", "st_stephen"),
        ),
        (
            "TI", "TI", "Tessin", "Tessin", "Ticino", "Ticino",
            _h("new_year", "epiphany", "easter_monday", "ascension", "national_day", "assumption", "all_saints",
               "christmas", "st_stephen"),
        ),
        (
            "UR", "UR", "Uri", "Uri", "Uri", "Uri",
            _h("new_year", "good_friday", "ascension", "corpus_christi", "national_day", "assumption", "all_saints",
               "immaculate_conception", "christmas"),
        ),
        (
            "VD", "VD", "Waadt", "Vaud", "Vaud", "Vaud",
            _h("new_year", "berchtold", "good_friday", "easter_monday", "ascension", "whit_monday", "national_day",
               "federal_fast_monday", "christmas"),
        ),
        (
            "VS", "VS", "Wallis", "Valais", "Vallese", "Valais",
            _h("new_year", "saint_joseph", "ascension", "corpus_christi", "national_day", "assumption", "all_saints",
               "immaculate_conception", "christmas"),
        ),
        (
            "ZG", "ZG", "Zug", "Zoug", "Zugo", "Zug",
            _h("new_year", "good_friday", "ascension", "corpus_christi", "national_day", "assumption", "all_saints",
               "immaculate_conception", "christmas"),
        ),
        (
            "ZH", "ZH", "Zürich", "Zurich", "Zurigo", "Zurich",
            _h("new_year", "good_friday", "easter_monday", "labour_day", "ascension", "whit_monday", "national_day",
               "christmas", "st_stephen"),
        ),
    )
)
