"""Choosing who holds the money, and what each process is handed as a result."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kitty import agent_runner as R
from kitty.agent import PersonalAgent, RulesBrain
from kitty.home_app import Home, create_app
from kitty.pot_agent import PotAgent
from kitty.trips import Invalid, clean_pot
from tests.test_provision import FakeBackend, make_trip, provisioner

DOC = {"start": "2026-10-16", "end": "2026-10-19", "people": {"asha": {}, "rohan": {}}}


def test_empty_means_no_treasurer():
    assert clean_pot({}, DOC) == {} and clean_pot({"treasurer": ""}, DOC) == {}


def test_a_good_choice_is_kept():
    assert clean_pot({"treasurer": "asha", "collect_from": "2026-09-25"}, DOC) == {"treasurer": "asha", "collect_from": "2026-09-25"}
    assert clean_pot({"treasurer": "asha", "collect_from": "2026-10-16"}, DOC)["collect_from"] == "2026-10-16"


@pytest.mark.parametrize("body, field", [
    ({"treasurer": "nobody", "collect_from": "2026-09-25"}, "treasurer"),
    ({"treasurer": "asha"}, "collect_from"),
    ({"treasurer": "asha", "collect_from": "soon"}, "collect_from"),
    ({"treasurer": "asha", "collect_from": "2026-10-17"}, "collect_from"),
    ({"treasurer": "asha", "collect_from": "2026-01-01"}, "collect_from"),
])
def test_clean_pot_validation(body, field):
    with pytest.raises(Invalid) as err:
        clean_pot(body, DOC)
    assert field in err.value.fields


@pytest.fixture
def client(tmp_path):
    home = Home(tmp_path / "trips", backend_factory=FakeBackend, starter=lambda folder, trip: None, faults=set())
    return home, TestClient(create_app(home, 7000), base_url="http://127.0.0.1:7000")


def new_trip(c) -> str:
    tid = c.post("/api/trips", json={"name": "Goa", "start": "2026-10-16", "end": "2026-10-19", "currency": "INR"}).json()["id"]
    for name in ("Asha", "Rohan"):
        assert c.post(f"/api/trips/{tid}/people", json={"name": name}).status_code == 201
    return tid


def test_choosing_a_treasurer_shows_in_the_trip(client):
    _, c = client
    tid = new_trip(c)
    got = c.put(f"/api/trips/{tid}/pot", json={"treasurer": "asha", "collect_from": "2026-09-25"}).json()
    assert got["treasurer"] == "asha" and got["collect_from"] == "2026-09-25"
    cleared = c.put(f"/api/trips/{tid}/pot", json={}).json()
    assert cleared["treasurer"] is None and cleared["collect_from"] is None


def test_a_bad_choice_is_refused_with_its_field(client):
    _, c = client
    r = c.put(f"/api/trips/{new_trip(c)}/pot", json={"treasurer": "nobody", "collect_from": "2026-09-25"})
    assert r.status_code == 422 and "treasurer" in r.json()["fields"]


def test_treasurer_removed_from_roster_clears_the_setting(client):
    home, c = client
    tid = new_trip(c)
    c.post(f"/api/trips/{tid}/people", json={"name": "Meera"})
    c.put(f"/api/trips/{tid}/pot", json={"treasurer": "asha", "collect_from": "2026-09-25"})
    got = c.delete(f"/api/trips/{tid}/people/asha").json()
    assert got["treasurer"] is None and got["collect_from"] is None
    assert "treasurer" not in home.store.get(tid)


def test_removing_someone_else_keeps_the_setting(client):
    _, c = client
    tid = new_trip(c)
    c.post(f"/api/trips/{tid}/people", json={"name": "Meera"})
    c.put(f"/api/trips/{tid}/pot", json={"treasurer": "asha", "collect_from": "2026-09-25"})
    assert c.delete(f"/api/trips/{tid}/people/meera").json()["treasurer"] == "asha"


def test_open_refuses_a_setting_that_no_longer_fits(client):
    home, c = client
    tid = new_trip(c)
    c.put(f"/api/trips/{tid}/pot", json={"treasurer": "asha", "collect_from": "2026-10-16"})
    assert c.patch(f"/api/trips/{tid}", json={"name": "Goa", "start": "2026-10-10", "end": "2026-10-19", "currency": "INR"}).status_code == 200
    r = c.post(f"/api/trips/{tid}/open")
    assert r.status_code == 422 and "collect_from" in r.json()["fields"]


def wait_open(home, c, tid) -> None:
    assert c.post(f"/api/trips/{tid}/open").status_code == 202, "control: a valid setting opens"
    home.running[tid].join(10)


def test_treasurer_is_fixed_after_group_opens(client):
    home, c = client
    tid = new_trip(c)
    c.put(f"/api/trips/{tid}/pot", json={"treasurer": "asha", "collect_from": "2026-09-25"})
    wait_open(home, c, tid)
    assert c.get(f"/api/trips/{tid}").json()["editable"] is False
    assert c.put(f"/api/trips/{tid}/pot", json={"treasurer": "rohan", "collect_from": "2026-09-25"}).status_code == 409
    assert home.store.get(tid)["treasurer"] == "asha"


def finished(tmp_path, **pot) -> Path:
    store, doc = make_trip(tmp_path)
    doc.update(pot)
    store.save(doc)
    assert provisioner(store, doc, FakeBackend()).run() == "live"
    return store.folder(doc["trip_id"])


def test_pot_key_reaches_agents_and_not_the_clerk(tmp_path):
    folder = finished(tmp_path, treasurer="asha", collect_from="2026-09-25")
    keys = {json.loads((folder / "state" / f"{p}.json").read_text())["pot_key"] for p in ("asha", "bilal", "chitra")}
    assert len(keys) == 1 and len(next(iter(keys))) == 64
    assert "pot_key" not in (folder / "state" / "clerk.json").read_text()


def test_trip_with_no_treasurer_has_no_pot_key(tmp_path):
    folder = finished(tmp_path)
    assert "pot_key" not in (folder / "state" / "asha.json").read_text()


TRIP_DOC = {"trip_id": "x", "name": "Goa", "start": "2026-10-16", "end": "2026-10-19", "currency": "INR", "people": {"asha": {"name": "Asha", "statement_name": "ASHA M"}, "rohan": {"name": "Rohan", "statement_name": "ROHAN K"}}}
CONFIG = {"owner_sender": "@o", "clerk_sender": "@c"}


def test_runner_uses_the_plain_agent_with_no_treasurer():
    core = R.build_core("rohan", TRIP_DOC, CONFIG, [], RulesBrain())
    assert type(core) is PersonalAgent


def test_runner_uses_the_pot_agent_with_a_treasurer():
    doc = {**TRIP_DOC, "treasurer": "asha", "collect_from": "2026-09-25"}
    core = R.build_core("rohan", doc, {**CONFIG, "pot_key": "ab" * 32}, [], RulesBrain())
    assert isinstance(core, PotAgent) and core.pot_key == bytes.fromhex("ab" * 32) and core.cfg.treasurer == "asha"


@pytest.mark.parametrize("body, pot, blocked", [
    ("start", False, True), ("paid 5000 2026-10-09", False, True),
    ("start", True, True), ("paid 5000 2026-10-09", True, False), ("received t1", True, False), ("not received t1", True, False), ("PAID 5 2026-10-09", True, False),
])
def test_statement_guard(body, pot, blocked):
    assert R.needs_statement(body, pot) is blocked


def test_home_page_states_the_host_limit():
    page = (Path(__file__).parent.parent / "app" / "home.html").read_text()
    assert "Whoever runs this Kitty server can read every room, including private ones." in page
    assert "Who holds the group's money?" in page and "Nobody, everyone pays and we settle up" in page
