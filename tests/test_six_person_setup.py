"""Six-person set-up when Switch fails a room creation. See docs/SIX_PERSON_SETUP_ROOT_CAUSE.md.

On 7 Nov the sixth person's `create_room` failed four times with a 500 from Switch. It happened while every
connection from Switch to Mattermost had just been dropped, and the same step with the same identities
worked minutes later. Two things were wrong on Kitty's side:

  - a Switch 500 from an agent operation was reported as `unexpected`, so its cause was lost;
  - a short Switch outage stopped the whole set-up, though every step is safe to run again.
"""
from __future__ import annotations

import json
from collections import Counter

import httpx
import pytest

from kitty import provision as PV
from kitty.switch_client import SwitchOpError
from tests.test_provision import FakeBackend, make_trip, provisioner

SIX = ("Asha", "Bilal", "Chitra", "Dev", "Esha", "Farhan")


def six(tmp_path):
    return make_trip(tmp_path, SIX)


def quick(store, doc, backend):
    """A provisioner that doesn't actually wait out Switch's outage, so the tests stay fast."""
    p = provisioner(store, doc, backend)
    p.transient_wait = 0
    return p


def secrets_of(p) -> dict:
    return json.loads(p.secrets_path.read_text())


# ---- classification ------------------------------------------------------------------------------


@pytest.mark.parametrize("status, code", [(500, "service_error"), (502, "service_error"), (503, "service_error"), (403, "service_refused"), (409, "service_refused")])
def test_a_switch_operation_error_is_classified_by_its_status(status, code):
    assert PV.classify(SwitchOpError("farhan-agent.create_room", status)) == code


def test_an_unknown_failure_is_still_unexpected():
    assert PV.classify(RuntimeError("something else")) == "unexpected"


def test_a_switch_operation_error_carries_no_reply_text():
    err = SwitchOpError("farhan-agent.create_room", 500)
    assert str(err) == "farhan-agent.create_room: 500" and err.status == 500


def test_agent_result_raises_the_typed_error(monkeypatch):
    from kitty.switch_client import Agent

    agent = Agent("id", "key", "farhan-agent")
    monkeypatch.setattr(agent, "op", lambda *a, **k: httpx.Response(500, text="Internal Server Error with private detail"))
    with pytest.raises(SwitchOpError) as err:
        agent.result("create_room", name="x")
    assert err.value.status == 500 and "private" not in str(err.value)


# ---- the sixth person's room, failing once ---------------------------------------------------------


def test_sixth_room_failing_once_is_retried_once_and_set_up_completes(tmp_path):
    store, doc = six(tmp_path)
    backend = FakeBackend()
    backend.fail["room:farhan-private"] = SwitchOpError("farhan-agent.create_room", 500)
    p = quick(store, doc, backend)
    assert p.run() == "live"
    assert p.status["retried"] == {"rooms:farhan": "service_error"}
    assert backend.calls["room:farhan-private"] == 2
    names = secrets_of(p)["people"]["farhan"]["room_names"]
    assert names["private"].endswith("r1"), "the retry used a fresh name, so a half-made room can't collide"
    assert not names["desk"].endswith("r1")


def test_a_retry_recreates_no_account_agent_or_other_room(tmp_path):
    store, doc = six(tmp_path)
    backend = FakeBackend()
    backend.fail["room:farhan-private"] = SwitchOpError("farhan-agent.create_room", 500)
    quick(store, doc, backend).run()
    for slug in ("asha", "bilal", "chitra", "dev", "esha", "farhan"):
        assert backend.calls[f"user:{slug}"] == 1 and backend.calls[f"chat:{slug}"] == 1 and backend.calls[f"agent:{slug}"] == 1
        assert backend.calls[f"room:{slug}-desk"] == 1
    assert backend.calls["user:kittyhost"] == 1 and backend.calls["agent:kitty"] == 1


# ---- failing twice: the outage outlasts the wait --------------------------------------------------


def test_a_second_failure_stops_with_the_real_cause_and_try_again_completes(tmp_path):
    store, doc = six(tmp_path)
    backend = FakeBackend()
    backend.fail["room:farhan-private"] = SwitchOpError("farhan-agent.create_room", 500)
    p = quick(store, doc, backend)
    original = backend._hit

    def twice(name):  # Fails the first two calls of this room, as a longer outage would.
        if name == "room:farhan-private" and backend.calls[name] == 1:
            backend.fail[name] = SwitchOpError("farhan-agent.create_room", 500)
        original(name)

    backend._hit = twice
    assert p.run() == "failed"
    assert p.status["failed"] == {"rooms:farhan": "service_error"}, "reported as Switch's problem, not as unexpected"
    before = Counter(backend.calls)
    assert quick(store, doc, backend).run() == "live", "Try again carries on from the failed step"
    assert backend.calls["user:farhan"] == before["user:farhan"] and backend.calls["agent:farhan"] == before["agent:farhan"]
    assert secrets_of(p)["people"]["farhan"]["room_names"]["private"].endswith("r2")


# ---- what is never retried --------------------------------------------------------------------------


def test_an_unexpected_failure_is_not_retried(tmp_path):
    store, doc = six(tmp_path)
    backend = FakeBackend()
    backend.fail["room:farhan-private"] = RuntimeError("an unknown reply")
    p = quick(store, doc, backend)
    assert p.run() == "failed"
    assert backend.calls["room:farhan-private"] == 1
    assert p.status["failed"] == {"rooms:farhan": "unexpected"} and not p.status.get("retried")


def test_a_refusal_is_not_retried(tmp_path):
    store, doc = six(tmp_path)
    backend = FakeBackend()
    backend.fail["room:farhan-private"] = SwitchOpError("farhan-agent.create_room", 403)
    p = quick(store, doc, backend)
    assert p.run() == "failed"
    assert backend.calls["room:farhan-private"] == 1


def test_a_connection_interruption_stops_everything_and_is_not_retried(tmp_path):
    store, doc = six(tmp_path)
    backend = FakeBackend()
    backend.fail["room:farhan-private"] = httpx.ConnectError("refused", request=httpx.Request("POST", "http://localhost:8000/agents/x/ops/create_room"))
    p = quick(store, doc, backend)
    assert p.run() == "failed"
    assert p.status["failed"] == {"rooms:farhan": "switch_unreachable"}
    assert backend.calls["room:farhan-private"] == 1
    assert quick(store, doc, backend).run() == "live"


def test_each_step_is_retried_at_most_once_per_run(tmp_path):
    store, doc = six(tmp_path)
    backend = FakeBackend()
    backend.fail["room:asha-private"] = SwitchOpError("asha-agent.create_room", 500)
    backend.fail["room:farhan-desk"] = SwitchOpError("farhan-agent.create_room", 503)
    p = quick(store, doc, backend)
    assert p.run() == "live"
    assert p.status["retried"] == {"rooms:asha": "service_error", "rooms:farhan": "service_error"}


def test_retries_wait_before_trying_again(tmp_path, monkeypatch):
    store, doc = six(tmp_path)
    backend = FakeBackend()
    backend.fail["room:farhan-private"] = SwitchOpError("farhan-agent.create_room", 500)
    waits = []
    monkeypatch.setattr(PV.time, "sleep", waits.append)
    p = provisioner(store, doc, backend)
    assert p.run() == "live"
    assert waits == [PV.TRANSIENT_WAIT_SECONDS] and PV.TRANSIENT_WAIT_SECONDS >= 20


def test_status_shows_the_retry_but_no_error_text(tmp_path):
    store, doc = six(tmp_path)
    backend = FakeBackend()
    backend.fail["room:farhan-private"] = SwitchOpError("farhan-agent.create_room", 500)
    p = quick(store, doc, backend)
    p.run()
    text = p.status_path.read_text()
    assert "retried" in text and "create_room" not in text and "500" not in text


# ---- repeated set-up requests -----------------------------------------------------------------------


def test_a_second_press_while_set_up_runs_starts_nothing(tmp_path):
    from kitty.home_app import Home

    store, doc = six(tmp_path)
    gate = __import__("threading").Event()

    class Slow(FakeBackend):
        def connect(self):
            gate.wait(5)
            super().connect()

    home = Home(tmp_path, backend_factory=Slow, starter=lambda folder, trip: None, faults=set())
    assert home.begin(doc) is True
    assert home.begin(doc) is False
    gate.set()
    home.running[doc["trip_id"]].join(10)
    assert home.stage(doc) == "live"
