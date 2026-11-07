"""Creating a trip in the app, on a fake Switch that fails on request. No network."""
from __future__ import annotations

import json
import stat
from collections import Counter

import httpx
import pytest
from fastapi.testclient import TestClient

from kitty.home_app import Home, create_app
from kitty.paths import for_trip
from kitty.provision import Provisioner
from kitty.trips import Invalid, TripStore, clean_person, clean_trip

SECRET_WORDS = ("Kt1!", "api_key", "password", "agent-key-", "reg-key-", "room-id-", "@sender-", "channel-id-")
GOOD_TRIP = {"name": "Coorg long weekend", "start": "2026-10-16", "end": "2026-10-19", "currency": "INR"}


class FakeBackend:
    def __init__(self) -> None:
        self.calls: Counter = Counter()
        self.fail: dict[str, Exception] = {}
        self.users: set[str] = set()

    def _hit(self, name: str) -> None:
        self.calls[name] += 1
        if name in self.fail:
            raise self.fail.pop(name)

    def connect(self) -> None:
        self._hit("connect")

    def ensure_user(self, creds: dict) -> None:
        self._hit(f"user:{creds['slug']}")
        self.users.add(creds["email"])

    def ensure_chat_account(self, creds: dict) -> None:
        self._hit(f"chat:{creds['slug']}")

    def link_identity(self, creds: dict) -> None:
        self._hit(f"link:{creds['slug']}")

    def registration_key(self, creds: dict) -> str:
        self._hit(f"reg:{creds['slug']}")
        return f"reg-key-{creds['slug']}"

    def register_agent(self, reg_key: str, name: str, description: str, display: str) -> tuple[str, str]:
        self._hit(f"agent:{name.split('-')[0]}")
        return f"agent-id-{name}", f"agent-key-{name}"

    def create_room(self, agent: dict, name: str, description: str, agents: list[str], users: list[str]) -> str:
        self._hit(f"room:{name.rsplit('-', 1)[0]}")
        return f"room-id-{name}"

    def invite_agent(self, agent: dict, room_id: str, name: str) -> None:
        self._hit(f"invite:{agent['slug']}")

    def find_channel(self, creds: dict, room_name: str) -> str:
        self._hit(f"channel:{creds['slug']}")
        return f"channel-id-{room_name}"

    def learn_senders(self, person: dict, clerk: dict) -> dict:
        self._hit(f"senders:{person['slug']}")
        return {"human": f"@sender-{person['slug']}", "agent": f"@sender-agent-{person['slug']}", "clerk": "@sender-clerk"}

    def announce(self, clerk: dict, room_id: str, text: str) -> None:
        self._hit("announce")


def make_trip(tmp_path, people=("Asha", "Bilal", "Chitra")) -> tuple[TripStore, dict]:
    store = TripStore(tmp_path)
    doc = store.create(clean_trip(GOOD_TRIP))
    for name in people:
        slug, person = clean_person({"name": name}, doc["people"])
        doc["people"][slug] = person
    store.save(doc)
    return store, doc


def provisioner(store, doc, backend, **kw) -> Provisioner:
    return Provisioner(backend, store.folder(doc["trip_id"]), doc, **kw)


def test_full_run_writes_the_same_files_as_the_command_line(tmp_path):
    store, doc = make_trip(tmp_path)
    p = provisioner(store, doc, FakeBackend())
    assert p.run() == "live"
    where = for_trip(store.folder(doc["trip_id"]))
    clerk = json.loads((where.state / "clerk.json").read_text())
    assert set(clerk) == {"agent_id", "api_key", "name", "group_room", "people", "run"}
    assert set(clerk["people"]) == {"asha", "bilal", "chitra"}
    person = json.loads((where.state / "asha.json").read_text())
    assert set(person) == {"me", "agent_id", "api_key", "name", "private_room", "desk_room", "owner_sender", "clerk_sender", "mattermost", "run", "clerk_bot"}
    assert set(person["mattermost"]["channels"]) == {"private", "desk", "group"}
    assert person["owner_sender"] == "@sender-asha" and person["clerk_sender"] == "@sender-clerk"


def test_each_persons_file_holds_only_their_own_credentials(tmp_path):
    store, doc = make_trip(tmp_path)
    provisioner(store, doc, FakeBackend()).run()
    where = for_trip(store.folder(doc["trip_id"]))
    text = (where.state / "asha.json").read_text()
    assert "bilal" not in text and "chitra" not in text
    assert "agent-key-bilal" not in (where.state / "asha.json").read_text()


def test_secret_files_are_private_to_the_user(tmp_path):
    store, doc = make_trip(tmp_path)
    provisioner(store, doc, FakeBackend()).run()
    state = for_trip(store.folder(doc["trip_id"])).state
    for name in ("secrets.json", "clerk.json", "asha.json", "invites.json", "asha.access.json"):
        assert stat.S_IMODE((state / name).stat().st_mode) == 0o600, name


@pytest.mark.parametrize("call", ["user:bilal", "chat:bilal", "link:bilal", "reg:bilal", "agent:bilal", "room:bilal-private", "room:bilal-desk", "invite:bilal", "channel:bilal", "senders:bilal"])
def test_a_failure_at_any_point_for_one_person_resumes_without_redoing_anything_saved(tmp_path, call):
    store, doc = make_trip(tmp_path)
    backend = FakeBackend()
    backend.fail[call] = RuntimeError("boom Kt1!secret-in-error")
    p = provisioner(store, doc, backend)
    assert p.run() == "failed"
    view = p.view()
    failed = [s for s in view["steps"] if s["state"] == "failed"]
    assert len(failed) == 1 and failed[0]["person"] == "bilal"
    # Other people carried on. The group room waited.
    assert all(s["state"] == "done" for s in view["steps"] if s["person"] in ("asha", "chitra"))
    assert not backend.calls["announce"]
    before = Counter(backend.calls)
    assert provisioner(store, doc, backend).run() == "live"
    for name, count in before.items():
        # Channel lookups read and create nothing, and each person's group channel is found only on the second run.
        if name == call or name == "connect" or name.startswith("channel:") or name.startswith("room:bilal"):
            continue
        assert backend.calls[name] == count, f"{name} ran again after it had succeeded"
    assert backend.calls["announce"] == 1


def test_status_never_carries_an_error_text_or_a_secret(tmp_path):
    store, doc = make_trip(tmp_path)
    backend = FakeBackend()
    backend.fail["agent:bilal"] = RuntimeError("server said Kt1!private password=hunter2")
    p = provisioner(store, doc, backend)
    p.run()
    for blob in (json.dumps(p.view()), (p.status_path).read_text()):
        assert "hunter2" not in blob and "Kt1!" not in blob and "server said" not in blob
    provisioner(store, doc, backend).run()
    blob = json.dumps(provisioner(store, doc, backend).view()) + provisioner(store, doc, backend).status_path.read_text()
    for word in SECRET_WORDS:
        assert word not in blob


def test_unreachable_service_stops_at_once_with_a_useful_message(tmp_path):
    store, doc = make_trip(tmp_path)
    backend = FakeBackend()
    backend.fail["user:asha"] = httpx.ConnectError("refused", request=httpx.Request("POST", "http://localhost:8065/x"))
    p = provisioner(store, doc, backend)
    assert p.run() == "failed"
    failed = next(s for s in p.view()["steps"] if s["state"] == "failed")
    assert failed["error"]["code"] == "mattermost_unreachable"
    assert not backend.calls["user:bilal"]


def test_clerk_failure_blocks_people_until_retried(tmp_path):
    store, doc = make_trip(tmp_path)
    backend = FakeBackend()
    backend.fail["agent:kitty"] = httpx.ReadTimeout("slow")
    p = provisioner(store, doc, backend)
    assert p.run() == "failed"
    assert not backend.calls["user:asha"]
    assert provisioner(store, doc, backend).run() == "live"


def test_group_room_failure_is_retried_with_a_fresh_name_and_people_are_not_rebuilt(tmp_path):
    store, doc = make_trip(tmp_path)
    backend = FakeBackend()
    backend.fail["room:coorg-long-weekend"] = httpx.ReadTimeout("slow")
    p = provisioner(store, doc, backend)
    assert p.run() == "failed"
    users = backend.calls["user:asha"]
    assert provisioner(store, doc, backend).run() == "live"
    assert backend.calls["user:asha"] == users
    secrets_doc = json.loads(p.secrets_path.read_text())
    assert secrets_doc["people"]["__host__"]["room_names"]["group"].endswith("r1")


def test_starting_the_apps_failing_keeps_everything_and_can_retry(tmp_path):
    from kitty.provision import StartFailed

    store, doc = make_trip(tmp_path)
    backend = FakeBackend()
    tries = []

    def starter():
        tries.append(1)
        if len(tries) == 1:
            raise StartFailed("no")

    p = provisioner(store, doc, backend, start_apps=starter)
    assert p.run() == "failed"
    assert next(s for s in p.view()["steps"] if s["id"] == "start")["error"]["code"] == "start_failed"
    calls = Counter(backend.calls)
    assert provisioner(store, doc, backend, start_apps=starter).run() == "live"
    assert backend.calls["announce"] == calls["announce"] == 1


def test_a_person_removed_after_a_failure_is_not_needed_for_the_group(tmp_path):
    store, doc = make_trip(tmp_path)
    backend = FakeBackend()
    backend.fail["agent:bilal"] = RuntimeError("boom")
    provisioner(store, doc, backend).run()
    del doc["people"]["bilal"]
    store.save(doc)
    provisioner(store, doc, backend).forget_person("bilal")
    assert provisioner(store, doc, backend).run() == "live"
    clerk = json.loads((for_trip(store.folder(doc["trip_id"])).state / "clerk.json").read_text())
    assert set(clerk["people"]) == {"asha", "chitra"}


# ---- forms ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("field,value", [("name", ""), ("name", "x"), ("name", "<b>trip</b>"), ("name", "**bold**"), ("start", "16/10/2026"), ("start", "2026-02-30"), ("end", "2026-10-01"), ("end", "2027-10-01"), ("currency", "BTC"), ("currency", None)])
def test_trip_form_rejects(field, value):
    with pytest.raises(Invalid) as err:
        clean_trip({**GOOD_TRIP, field: value})
    assert field in err.value.fields or "end" in err.value.fields


@pytest.mark.parametrize("name", ["", "A", "Mary Jane", "O'Neil", "shared", "All", "clerk", "verify", "<script>", "Priya1", "x" * 15])
def test_person_form_rejects(name):
    with pytest.raises(Invalid) as err:
        clean_person({"name": name}, {})
    assert "name" in err.value.fields


def test_person_names_and_statement_names_are_unique():
    slug, asha = clean_person({"name": "Asha", "statement_name": "asha m"}, {})
    assert slug == "asha" and asha == {"name": "Asha", "statement_name": "ASHA M"}
    with pytest.raises(Invalid):
        clean_person({"name": "ASHA"}, {slug: asha})
    with pytest.raises(Invalid) as err:
        clean_person({"name": "Asha2".replace("2", "b"), "statement_name": "ASHA M"}, {slug: asha})
    assert "statement_name" in err.value.fields
    with pytest.raises(Invalid):
        clean_person({"name": "Bilal", "statement_name": "UPI/DR/ME"}, {})


# ---- the routes ------------------------------------------------------------------------------------


def client_for(tmp_path, backend=None, faults=None, starter=None):
    backend = backend or FakeBackend()
    home = Home(tmp_path, backend_factory=lambda: backend, starter=starter or (lambda folder, trip: None), faults=faults or set())
    return TestClient(create_app(home, 7000), base_url="http://127.0.0.1:7000"), home, backend


def wait_finished(client, home, tid):
    for thread in list(home.running.values()):
        thread.join(5)
    return client.get(f"/api/trips/{tid}").json()


def build_via_routes(client, people=("Asha", "Bilal")):
    trip = client.post("/api/trips", json=GOOD_TRIP).json()
    for name in people:
        assert client.post(f"/api/trips/{trip['id']}/people", json={"name": name}).status_code == 201
    return trip["id"]


def test_routes_full_flow_and_browser_never_sees_a_credential(tmp_path):
    client, home, _ = client_for(tmp_path)
    tid = build_via_routes(client)
    assert client.post(f"/api/trips/{tid}/open").status_code == 202
    detail = wait_finished(client, home, tid)
    assert detail["state"] == "live"
    blob = json.dumps(detail) + json.dumps(client.get("/api/trips").json())
    for word in SECRET_WORDS:
        assert word not in blob, word


def test_invite_links_appear_only_when_the_apps_are_up(tmp_path, monkeypatch):
    from kitty import home_app, runtime

    client, home, _ = client_for(tmp_path)
    tid = build_via_routes(client)
    client.post(f"/api/trips/{tid}/open")
    assert all("invite_url" not in p for p in wait_finished(client, home, tid)["people"])  # Fake: no apps running.
    monkeypatch.setattr(runtime, "healthy", lambda doc: True)
    monkeypatch.setattr(runtime, "status", lambda folder: {"board": 7200, "apps": {"asha": 7201, "bilal": 7202}, "pids": [1]})
    monkeypatch.setattr(home_app.httpx, "get", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("no")))
    people = client.get(f"/api/trips/{tid}").json()["people"]
    assert people[0]["invite_url"].startswith("http://127.0.0.1:7201/join?t=")
    old = people[0]["invite_url"]
    fresh = client.post(f"/api/trips/{tid}/people/asha/invite").json()["people"][0]["invite_url"]
    assert fresh != old


def test_double_create_with_the_same_key_makes_one_trip(tmp_path):
    client, _, _ = client_for(tmp_path)
    a = client.post("/api/trips", json={**GOOD_TRIP, "create_key": "k1"}).json()
    b = client.post("/api/trips", json={**GOOD_TRIP, "create_key": "k1"}).json()
    assert a["id"] == b["id"] and len(client.get("/api/trips").json()["trips"]) == 1


def test_open_needs_two_people_and_a_second_press_is_safe(tmp_path):
    client, home, backend = client_for(tmp_path)
    tid = build_via_routes(client, people=("Asha",))
    assert client.post(f"/api/trips/{tid}/open").status_code == 422
    client.post(f"/api/trips/{tid}/people", json={"name": "Bilal"})
    assert client.post(f"/api/trips/{tid}/open").status_code == 202
    second = client.post(f"/api/trips/{tid}/open").status_code
    assert second in (200, 202, 409)
    wait_finished(client, home, tid)
    assert client.post(f"/api/trips/{tid}/open").status_code == 200
    assert backend.calls["announce"] == 1


def test_people_lock_once_the_group_room_exists_but_stay_editable_after_an_early_failure(tmp_path):
    backend = FakeBackend()
    backend.fail["agent:bilal"] = RuntimeError("no")
    client, home, _ = client_for(tmp_path, backend)
    tid = build_via_routes(client)
    client.post(f"/api/trips/{tid}/open")
    detail = wait_finished(client, home, tid)
    assert detail["state"] == "failed" and detail["editable"]
    assert client.post(f"/api/trips/{tid}/people", json={"name": "Chitra"}).status_code == 201
    client.post(f"/api/trips/{tid}/open")
    detail = wait_finished(client, home, tid)
    assert detail["state"] == "live" and not detail["editable"]
    assert client.post(f"/api/trips/{tid}/people", json={"name": "Dev"}).status_code == 409
    assert client.delete(f"/api/trips/{tid}/people/asha").status_code == 409
    assert client.patch(f"/api/trips/{tid}", json=GOOD_TRIP).status_code == 409
    assert client.delete(f"/api/trips/{tid}").status_code == 409


def test_a_restart_during_set_up_is_reported_and_recoverable(tmp_path):
    store, doc = make_trip(tmp_path)
    p = provisioner(store, doc, FakeBackend())
    p.status.update(state="running", current="group", done=["connect"], failed={})
    p._write_status()
    home = Home(tmp_path, backend_factory=FakeBackend, starter=lambda f, t: None, faults=set())
    view = home.provisioner(store.get(doc["trip_id"])).view()
    assert view["state"] == "failed"
    assert next(s for s in view["steps"] if s["id"] == "group")["error"]["code"] == "interrupted"


def test_other_origins_and_hosts_are_refused(tmp_path):
    client, _, _ = client_for(tmp_path)
    assert client.post("/api/trips", json=GOOD_TRIP, headers={"origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/trips", headers={"host": "evil.example"}).status_code == 421
    assert client.get("/api/trips/../../etc").status_code in (200, 404)  # Page route or not found. Never a file.
    assert client.get("/api/trips/nope").status_code == 404


def test_validation_errors_are_field_by_field(tmp_path):
    client, _, _ = client_for(tmp_path)
    r = client.post("/api/trips", json={**GOOD_TRIP, "name": "", "end": "2020-01-01"})
    assert r.status_code == 422 and set(r.json()["fields"]) == {"name", "end"}
