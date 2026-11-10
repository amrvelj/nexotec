"""KAN-209 — UI translations served at runtime (D-A-23, FR-A-30…32).

Pins: the runtime map's ETag round trip and invalidation by a publish; that
only platform values are served (the bundle is the fallback for the rest);
platform-admin-only writes and no dealer endpoint at all; the panel's
listing; the bundle import; and the missing-key log.
"""

import json
import uuid

import pytest
from sqlalchemy import func, select

from app.core.audit_model import AuditEvent
from app.core.auth import AccessRole, create_access_token
from app.core.i18n import SwissLanguage
from app.main import app
from app.platform.models.ui_translation import MissingTranslation, UiTranslation, UiTranslationSource
from app.platform.services import ui_translation as service
from scripts import import_ui_translation_bundles

BUNDLES = {
    SwissLanguage.DE: {
        "shell": {"nav": {"business": "Betrieb", "users": "Benutzer"}},
        "common": {"items": "{{count, number}} Einträge"},
    },
    SwissLanguage.FR: {
        "shell": {"nav": {"business": "Entreprise", "users": "Utilisateurs"}},
        "common": {"items": "{{count, number}} entrées"},
    },
    SwissLanguage.IT: {
        "shell": {"nav": {"business": "Azienda", "users": "Utenti"}},
        "common": {"items": "{{count, number}} voci"},
    },
    SwissLanguage.EN: {
        "shell": {"nav": {"business": "Business", "users": "Users"}},
        "common": {"items": "{{count, number}} entries"},
    },
}


def _bearer(role: AccessRole | None, *, is_dealer_manager: bool = False, user_id: uuid.UUID | None = None) -> dict:
    token = create_access_token(
        user_id=user_id or uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        group_id=uuid.uuid4(),
        roles=frozenset({role}) if role is not None else frozenset(),
        is_dealer_manager=is_dealer_manager,
    )
    return {"Authorization": f"Bearer {token}"}


ADMIN = AccessRole.PLATFORM_ADMIN


@pytest.fixture()
def seeded(db_session):
    service.import_bundles(db_session, BUNDLES)
    db_session.commit()


def _row(db_session, namespace: str, key: str, language: SwissLanguage) -> UiTranslation:
    db_session.expire_all()
    return db_session.scalar(
        select(UiTranslation).where(
            UiTranslation.namespace == namespace, UiTranslation.key == key, UiTranslation.language == language
        )
    )


def _publish(client, namespace, key, language, value, version, headers=None):
    return client.put(
        f"/v1/platform/ui-translations/{namespace}/{key}/{language}",
        json={"value": value},
        headers={**(headers if headers is not None else _bearer(ADMIN)), "If-Match": str(version)},
    )


# --- the runtime map: ETag and cache ---------------------------------------------------------


def test_runtime_map_is_unauthenticated_and_serves_only_platform_values(client, seeded):
    response = client.get("/v1/i18n/fr")
    assert response.status_code == 200, response.text
    assert response.json() == {"language": "fr", "version": 0, "values": {}}
    assert response.headers["ETag"] == '"ui-0-fr"'
    assert response.headers["Cache-Control"] == "public, no-cache"

    assert _publish(client, "shell", "nav.business", "fr", "Société", 1).status_code == 200
    body = client.get("/v1/i18n/fr").json()
    # The seed rows (users, items) are not served: the bundle is their fallback.
    assert body == {"language": "fr", "version": 1, "values": {"shell.nav.business": "Société"}}
    # Another language is untouched by a French edit, though its ETag moves.
    assert client.get("/v1/i18n/de").json()["values"] == {}


def test_etag_round_trip_304_until_a_publish_invalidates(client, seeded):
    first = client.get("/v1/i18n/de")
    etag = first.headers["ETag"]

    unchanged = client.get("/v1/i18n/de", headers={"If-None-Match": etag})
    assert unchanged.status_code == 304
    assert unchanged.content == b""
    assert unchanged.headers["ETag"] == etag
    # A proxy may weaken the tag; a list of tags is honoured too.
    assert client.get("/v1/i18n/de", headers={"If-None-Match": f"W/{etag}"}).status_code == 304
    assert client.get("/v1/i18n/de", headers={"If-None-Match": f'"other", {etag}'}).status_code == 304

    assert _publish(client, "shell", "nav.business", "de", "Garage", 1).status_code == 200

    after = client.get("/v1/i18n/de", headers={"If-None-Match": etag})
    assert after.status_code == 200
    assert after.headers["ETag"] != etag
    assert after.json()["values"] == {"shell.nav.business": "Garage"}
    assert client.get("/v1/i18n/de", headers={"If-None-Match": after.headers["ETag"]}).status_code == 304


def test_revert_takes_the_value_back_and_invalidates(client, seeded):
    published = _publish(client, "shell", "nav.users", "it", "Persone", 1).json()
    etag = client.get("/v1/i18n/it").headers["ETag"]

    reverted = client.post(
        "/v1/platform/ui-translations/shell/nav.users/it/revert",
        headers={**_bearer(ADMIN), "If-Match": str(published["version"])},
    )
    assert reverted.status_code == 200, reverted.text
    assert reverted.json()["value"] == "Utenti"
    assert reverted.json()["source"] == "bundle_seed"

    again = client.get("/v1/i18n/it", headers={"If-None-Match": etag})
    assert again.status_code == 200
    assert again.json()["values"] == {}


def test_publishing_the_seed_text_is_a_revert_and_an_unchanged_publish_changes_nothing(client, db_session, seeded):
    _publish(client, "shell", "nav.business", "en", "Company", 1)
    back = _publish(client, "shell", "nav.business", "en", "Business", 2)
    assert back.json()["source"] == "bundle_seed"
    version = service.current_revision(db_session)

    same = _publish(client, "shell", "nav.business", "en", "Business", 3)
    assert same.status_code == 200
    assert same.json()["version"] == 3
    db_session.expire_all()
    assert service.current_revision(db_session) == version


def test_unknown_language_is_422(client):
    assert client.get("/v1/i18n/es").status_code == 422


# --- writes: platform_admin only, If-Match, validation ---------------------------------------


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param(lambda: _bearer(AccessRole.SALES), id="sales"),
        pytest.param(lambda: _bearer(AccessRole.SALES, is_dealer_manager=True), id="dealer-manager"),
        pytest.param(lambda: _bearer(AccessRole.AUDITOR), id="auditor"),
    ],
)
def test_only_platform_admin_writes(client, db_session, seeded, headers):
    put = _publish(client, "shell", "nav.business", "de", "Garage", 1, headers=headers())
    assert put.status_code == 403, put.text
    revert = client.post(
        "/v1/platform/ui-translations/shell/nav.business/de/revert", headers={**headers(), "If-Match": "1"}
    )
    assert revert.status_code == 403
    row = _row(db_session, "shell", "nav.business", SwissLanguage.DE)
    assert (row.value, row.source, row.version) == ("Betrieb", UiTranslationSource.BUNDLE_SEED, 1)


def test_writes_need_a_session(client, seeded):
    response = client.put(
        "/v1/platform/ui-translations/shell/nav.business/de", json={"value": "Garage"}, headers={"If-Match": "1"}
    )
    assert response.status_code == 401


def test_no_dealer_endpoint_writes_ui_strings():
    """FR-A-32: dealers cannot override UI strings — no such endpoint exists.
    Every write on a UI-translation path is one of these three, and the two
    that change a string are platform_admin's (the tests above)."""

    writes = {
        (method.upper(), path)
        for path, item in app.openapi()["paths"].items()
        if "i18n" in path or "ui-translation" in path or "translation" in path
        for method in item
        if method != "get"
    }
    assert writes == {
        ("PUT", "/v1/platform/ui-translations/{namespace}/{key}/{language}"),
        ("POST", "/v1/platform/ui-translations/{namespace}/{key}/{language}/revert"),
        ("POST", "/v1/i18n/missing-translations"),
    }


def test_publish_takes_if_match(client, seeded):
    missing = client.put(
        "/v1/platform/ui-translations/shell/nav.business/de", json={"value": "Garage"}, headers=_bearer(ADMIN)
    )
    assert missing.status_code == 400
    assert _publish(client, "shell", "nav.business", "de", "Garage", 1).status_code == 200
    stale = _publish(client, "shell", "nav.business", "de", "Werkstatt", 1)
    assert stale.status_code == 409
    assert stale.json()["error"]["details"]["currentVersion"] == 2


def test_publish_unknown_key_is_404(client, seeded):
    assert _publish(client, "shell", "nav.nothing", "de", "x", 1).status_code == 404


def test_publish_must_keep_the_seed_placeholders(client, db_session, seeded):
    dropped = _publish(client, "common", "items", "de", "Einträge", 1)
    assert dropped.status_code == 422
    assert dropped.json()["error"]["details"]["reason"] == "ui_translation_placeholders_changed"
    renamed = _publish(client, "common", "items", "de", "{{n, number}} Einträge", 1)
    assert renamed.status_code == 422
    assert _publish(client, "common", "items", "de", "Einträge: {{ count, number }}", 1).status_code == 200


def test_blank_value_is_refused(client, seeded):
    blank = _publish(client, "shell", "nav.business", "de", "   ", 1)
    assert blank.status_code == 422
    assert blank.json()["error"]["details"]["reason"] == "ui_translation_blank"
    assert _publish(client, "shell", "nav.business", "de", "", 1).status_code == 422


def test_publish_and_revert_are_audited_with_before_and_after(client, db_session, seeded):
    admin_id = uuid.uuid4()
    headers = _bearer(ADMIN, user_id=admin_id)
    _publish(client, "shell", "nav.business", "fr", "Société", 1, headers=headers)
    client.post("/v1/platform/ui-translations/shell/nav.business/fr/revert", headers={**headers, "If-Match": "2"})
    events = db_session.scalars(
        select(AuditEvent).where(AuditEvent.entity_type == "ui_translation").order_by(AuditEvent.created_at)
    ).all()
    assert [e.action for e in events] == ["ui_translation_published", "ui_translation_reverted"]
    assert events[0].before["value"] == "Entreprise"
    assert events[0].after["value"] == "Société"
    assert events[0].actor_id == admin_id
    assert events[0].tenant_id is None
    assert _row(db_session, "shell", "nav.business", SwissLanguage.FR).updated_by == admin_id


# --- the panel's listing ----------------------------------------------------------------------


def test_listing_is_readable_by_any_role_and_groups_four_languages(client, seeded):
    response = client.get("/v1/platform/ui-translations", headers=_bearer(AccessRole.SALES))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 3
    assert [(i["namespace"], i["key"]) for i in body["items"]] == [
        ("common", "items"),
        ("shell", "nav.business"),
        ("shell", "nav.users"),
    ]
    business = body["items"][1]
    assert {lang: business[lang]["value"] for lang in ("de", "fr", "it", "en")} == {
        "de": "Betrieb",
        "fr": "Entreprise",
        "it": "Azienda",
        "en": "Business",
    }
    assert business["de"]["seedValue"] == "Betrieb"


def test_listing_needs_a_session(client, seeded):
    assert client.get("/v1/platform/ui-translations").status_code == 401


def test_listing_search_namespace_edited_and_cursor(client, seeded):
    headers = _bearer(ADMIN)

    def keys(**params):
        body = client.get("/v1/platform/ui-translations", params=params, headers=headers).json()
        return [f"{i['namespace']}.{i['key']}" for i in body["items"]]

    assert keys(q="nav.bus") == ["shell.nav.business"]
    assert keys(q="UTENTI") == ["shell.nav.users"]  # a value, any language, any case
    assert keys(q="%") == []  # a wildcard is a character, not a pattern
    assert keys(namespace="common") == ["common.items"]
    assert keys(edited="true") == []
    _publish(client, "shell", "nav.users", "de", "Personen", 1)
    assert keys(edited="true") == ["shell.nav.users"]
    assert keys(q="Personen") == ["shell.nav.users"]

    first = client.get("/v1/platform/ui-translations", params={"limit": 2}, headers=headers).json()
    assert len(first["items"]) == 2 and first["nextCursor"]
    second = client.get(
        "/v1/platform/ui-translations", params={"limit": 2, "cursor": first["nextCursor"]}, headers=headers
    ).json()
    assert [i["key"] for i in second["items"]] == ["nav.users"]
    assert second["nextCursor"] is None
    assert (
        client.get("/v1/platform/ui-translations", params={"cursor": "not-a-cursor"}, headers=headers).status_code
        == 400
    )

    namespaces = client.get("/v1/platform/ui-translations/namespaces", headers=headers).json()
    assert namespaces == {"items": ["common", "shell"]}


# --- the bundle import ------------------------------------------------------------------------


def test_import_is_idempotent_refreshes_seeds_and_never_touches_a_platform_value(client, db_session, seeded):
    _publish(client, "shell", "nav.business", "de", "Garage", 1)

    again = service.import_bundles(db_session, BUNDLES)
    assert (again.inserted, again.seed_refreshed, again.unchanged) == (0, 0, 12)

    changed = json.loads(json.dumps({lang.value: b for lang, b in BUNDLES.items()}))
    changed["de"]["shell"]["nav"]["business"] = "Unternehmen"
    changed["de"]["shell"]["nav"]["users"] = "Mitarbeitende"
    changed["de"]["shell"]["nav"]["new"] = "Neu"
    report = service.import_bundles(db_session, {SwissLanguage(k): v for k, v in changed.items()})
    db_session.commit()
    assert report.inserted == 1
    assert report.seed_refreshed == 2
    assert report.platform_rows_with_new_seed == ["de:shell.nav.business"]

    edited = _row(db_session, "shell", "nav.business", SwissLanguage.DE)
    assert (edited.value, edited.seed_value, edited.source) == ("Garage", "Unternehmen", UiTranslationSource.PLATFORM)
    seed = _row(db_session, "shell", "nav.users", SwissLanguage.DE)
    assert (seed.value, seed.seed_value, seed.source) == (
        "Mitarbeitende",
        "Mitarbeitende",
        UiTranslationSource.BUNDLE_SEED,
    )
    # Seeds are never served, so the runtime map did not move.
    assert client.get("/v1/i18n/de").json()["values"] == {"shell.nav.business": "Garage"}


def test_import_script_dry_runs_by_default_then_commits(tmp_path, engine, capsys):
    # KAN-616: locales/<language>/<namespace>.json, a top-level string a file of its own.
    for language, bundle in BUNDLES.items():
        folder = tmp_path / language.value
        folder.mkdir()
        for namespace, value in {**bundle, "stray": "a top-level string"}.items():
            (folder / f"{namespace}.json").write_text(json.dumps(value), encoding="utf-8")

    from sqlalchemy.orm import sessionmaker

    factory = sessionmaker(bind=engine)

    def count() -> int:
        with factory() as db:
            return db.scalar(select(func.count()).select_from(UiTranslation)) or 0

    assert import_ui_translation_bundles.main(["--bundles-dir", str(tmp_path)], session_factory=factory) == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "inserted bundle_seed rows: 12" in out and "de:stray" in out
    assert count() == 0

    import_ui_translation_bundles.main(["--bundles-dir", str(tmp_path), "--commit"], session_factory=factory)
    assert count() == 12
    import_ui_translation_bundles.main(["--bundles-dir", str(tmp_path), "--commit"], session_factory=factory)
    assert "unchanged:                 12" in capsys.readouterr().out
    assert count() == 12


def test_import_reads_every_shipped_bundle(db_session):
    """The real bundles flatten without a skipped entry, one row per string."""

    bundles = import_ui_translation_bundles.read_bundles(import_ui_translation_bundles.DEFAULT_BUNDLES_DIR)
    for language, bundle in bundles.items():
        flat, skipped = service.flatten_bundle(bundle)
        assert skipped == [], language
        assert len(flat) > 1000


def test_import_assembles_a_namespace_split_by_its_second_level(tmp_path):
    """`<namespace>.<key>.json` (a namespace above the size cap, KAN-616) lands under its namespace."""

    (tmp_path / "apiRefusal.json").write_text(json.dumps({"notFound": "Nicht gefunden"}), encoding="utf-8")
    (tmp_path / "apiRefusal.reasons.json").write_text(json.dumps({"vin": "FIN ungültig"}), encoding="utf-8")
    (tmp_path / "common.json").write_text(json.dumps({"retry": "Erneut"}), encoding="utf-8")
    assert import_ui_translation_bundles.read_bundle(tmp_path) == {
        "apiRefusal": {"notFound": "Nicht gefunden", "reasons": {"vin": "FIN ungültig"}},
        "common": {"retry": "Erneut"},
    }


# --- the missing-key log ----------------------------------------------------------------------


def test_missing_report_needs_a_session(client):
    response = client.post("/v1/i18n/missing-translations", json={"language": "de", "keys": ["a.b"]})
    assert response.status_code == 401


def test_missing_reports_are_deduplicated_and_counted(client, db_session):
    headers = _bearer(AccessRole.SALES)
    first = client.post(
        "/v1/i18n/missing-translations",
        json={"language": "fr", "keys": ["kan209.a", "kan209.b", "kan209.a"]},
        headers=headers,
    )
    assert first.status_code == 202, first.text
    assert first.json() == {"recorded": 2}
    client.post("/v1/i18n/missing-translations", json={"language": "fr", "keys": ["kan209.a"]}, headers=headers)
    client.post("/v1/i18n/missing-translations", json={"language": "de", "keys": ["kan209.a"]}, headers=headers)

    rows = {(r.key, r.language.value): r.report_count for r in db_session.scalars(select(MissingTranslation)).all()}
    assert rows == {("kan209.a", "fr"): 2, ("kan209.b", "fr"): 1, ("kan209.a", "de"): 1}

    listing = client.get("/v1/platform/ui-translations/missing", headers=_bearer(ADMIN)).json()
    assert {(i["key"], i["language"], i["reportCount"]) for i in listing["items"]} == {
        ("kan209.a", "fr", 2),
        ("kan209.b", "fr", 1),
        ("kan209.a", "de", 1),
    }


def test_missing_report_refuses_what_is_not_a_key_and_too_many(client):
    headers = _bearer(AccessRole.SALES)
    not_a_key = client.post(
        "/v1/i18n/missing-translations", json={"language": "de", "keys": ["<script>"]}, headers=headers
    )
    assert not_a_key.status_code == 422
    too_many = client.post(
        "/v1/i18n/missing-translations",
        json={"language": "de", "keys": [f"k.k{i}" for i in range(51)]},
        headers=headers,
    )
    assert too_many.status_code == 422


def test_missing_log_stops_growing_at_its_cap(client, db_session, monkeypatch):
    monkeypatch.setattr(service, "MISSING_TRANSLATION_ROW_CAP", 2)
    headers = _bearer(AccessRole.SALES)
    client.post(
        "/v1/i18n/missing-translations", json={"language": "de", "keys": ["k.a", "k.b", "k.c"]}, headers=headers
    )
    again = client.post(
        "/v1/i18n/missing-translations", json={"language": "de", "keys": ["k.a", "k.d"]}, headers=headers
    )
    assert again.json() == {"recorded": 1}
    assert db_session.scalar(select(func.count()).select_from(MissingTranslation)) == 2
