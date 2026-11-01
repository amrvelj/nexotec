"""KAN-216: the SPA catch-all serves the Web Push service worker as itself.

A service worker answered with index.html (text/html) cannot register, so
push could never reach a browser on staging or production, where the API
serves the built SPA. Files Vite copies to the dist root are served as
themselves; nothing outside the dist root is reachable."""

from app.main import spa_file_response


def _dist(tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html>")
    (tmp_path / "sw.js").write_text("self.addEventListener('push', () => {})")
    (tmp_path / "favicon.svg").write_text("<svg/>")
    (tmp_path / "assets").mkdir()
    (tmp_path.parent / "secret.txt").write_text("outside")
    return tmp_path


def test_the_service_worker_is_served_as_javascript_and_never_cached(tmp_path):
    response = spa_file_response(_dist(tmp_path), "sw.js")
    assert str(response.path) == str(tmp_path / "sw.js")
    assert response.media_type in {"text/javascript", "application/javascript"}
    assert response.headers["cache-control"] == "no-cache"


def test_a_root_file_is_itself_and_every_route_is_the_spa(tmp_path):
    dist = _dist(tmp_path)
    assert str(spa_file_response(dist, "favicon.svg").path) == str(dist / "favicon.svg")
    for path in ("", "notifications", "account/notifications", "assets", "../secret.txt", "..", "index.html"):
        assert str(spa_file_response(dist, path).path) == str(dist / "index.html"), path
