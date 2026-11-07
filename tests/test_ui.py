"""What the interface must keep true: assets served, no third-party calls, and a cat with no way to act.

The pages themselves are looked at in a real browser by scripts/ui_check.py.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kitty.home_app import Home, create_app as home_app
from tests.test_pot_review_fixes import PORT, app_for

APP = Path(__file__).parent.parent / "app"
UI = APP / "ui"
PAGES = ["home.html", "person.html", "board.html", "stage.html"]
BANNED = ("verified", "bank-confirmed", "fake", "fraud", "suspicious", "proven")
MOODS = ("idle", "greeting", "curious", "thinking", "guiding", "reviewing", "filing", "waiting", "warning", "paid", "reconciled", "celebrating", "sleeping")


@pytest.fixture
def home(tmp_path):
    h = Home(tmp_path / "trips", backend_factory=lambda: None, starter=lambda folder, trip: None, faults=set())
    return TestClient(home_app(h, PORT), base_url=f"http://127.0.0.1:{PORT}")


@pytest.mark.parametrize("name", ["kitty.css", "cat.js", "companion.js", "guide.js", "icons.js", "fonts/anek-latin-latin-wdth-normal.woff2", "fonts/anek-latin-latin-ext-wdth-normal.woff2"])
def test_the_home_app_serves_the_assets(home, name):
    assert home.get(f"/ui/{name}").status_code == 200


def test_assets_can_only_come_from_the_ui_folder(home):
    for path in ("/ui/../home.html", "/ui/%2e%2e/%2e%2e/kitty/home_app.py", "/ui/..%2f..%2fstate/secrets.json"):
        assert home.get(path).status_code == 404, path
    assert home.get("/ui/kitty.css").status_code == 200, "control: a real file is served"


def test_a_persons_assets_stay_behind_their_invite(tmp_path):
    client, _ = app_for(tmp_path)
    assert client.get("/ui/cat.js").status_code == 200, "control: with a session"
    client.cookies.clear()
    assert client.get("/ui/cat.js").status_code == 401
    page = client.get("/")
    assert page.status_code == 401 and "someone's private Kitty" in page.text and "/ui/" not in page.text, "the refusal page stands alone"


def test_the_guard_on_a_persons_app_still_covers_their_data(tmp_path):
    client, _ = app_for(tmp_path)
    assert client.get("/api/feed").status_code == 200, "control: with a session"
    client.cookies.clear()
    assert client.get("/api/feed").status_code == 401


@pytest.mark.parametrize("page", PAGES)
def test_pages_load_nothing_from_anyone_else(page):
    """A private app shouldn't tell a font CDN or anyone else that it was opened."""
    text = (APP / page).read_text()
    assert not re.findall(r"""(?:src|href)=["']https?://(?!127\.0\.0\.1)""", text), "scripts, styles and fonts come from this app"
    assert "fonts.googleapis.com" not in text
    assert "/ui/kitty.css" in text, "control: it does load the design system"


def test_the_css_loads_nothing_from_anyone_else():
    assert not re.findall(r"url\(['\"]?https?:", (UI / "kitty.css").read_text())


@pytest.mark.parametrize("name", ["cat.js", "companion.js", "guide.js", "icons.js"])
def test_the_cat_has_no_way_to_act(name):
    """She reads what the page hands her. She can't reach a server, so she can't approve or change anything."""
    text = (UI / name).read_text()
    for reach in ("fetch(", "XMLHttpRequest", "WebSocket", "sendBeacon", "EventSource", ".submit(", "api/", "/say", ".click()"):
        assert reach not in text, reach
    for command in ("data-say", "data-release", "data-text="):
        assert command not in text, command
    assert not re.search(r"\b(release|drop|withdraw|confirm|reject|correct|recheck|cancel|call)\s+\$\{", text), "no way to build a command"


def test_those_checks_would_notice():
    """Control: the same words do appear in the page that really acts."""
    person = (APP / "person.html").read_text()
    assert "fetch(" in person and "data-say" in person and "data-release" in person
    assert re.search(r"\b(release|drop|withdraw|confirm|reject|correct|recheck|cancel|call)\s+\$\{", person)


@pytest.mark.parametrize("name", ["guide.js", "companion.js", "cat.js"])
def test_the_cat_keeps_the_products_careful_words(name):
    text = (UI / name).read_text().lower()
    for word in BANNED:
        assert word not in text, word


def test_the_cat_never_claims_money_moved_or_a_payment_counted_on_its_own():
    guide = (UI / "guide.js").read_text()
    assert "Kitty never moves money" in guide
    assert "possible match" in guide.lower() and "Only the treasurer's own press of Confirm makes it count" in guide


def test_every_mood_is_drawn():
    js, css = (UI / "cat.js").read_text(), (UI / "kitty.css").read_text()
    for mood in MOODS:
        assert f'"{mood}"' in js, mood
        assert f"kitty-cat[mood={mood}]" in css, mood


def test_reduced_motion_is_honoured():
    css = (UI / "kitty.css").read_text()
    assert "prefers-reduced-motion:reduce" in css
    assert "matchMedia(\"(prefers-reduced-motion: reduce)\")" in (UI / "cat.js").read_text()


def test_a_candidate_is_never_shown_as_confirmed():
    """The treasurer's queue separates what their agent found from what they've decided."""
    text = (APP / "person.html").read_text()
    assert '"found it":["maybe","Possible match"' in text
    assert 'pill=["wait","Not counted yet"]' in text


def test_the_fonts_licence_ships_with_the_fonts():
    assert "SIL Open Font License" in (UI / "fonts" / "OFL.txt").read_text()
