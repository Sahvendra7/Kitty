"""A restart rebuilds the clerk from its log. Pot decisions keep the time they really happened."""
from __future__ import annotations

import json

import pytest

from kitty import claims as C
from kitty import clerk_daemon as D
from kitty import pot as P
from kitty.clerk import Clerk, Person
from tests.pot_helpers import CFG, PEOPLE, TOKEN_A, TRIP, claim, match

TRIP_DOC = {"trip_id": "goa-1", "start": "2026-10-16", "end": "2026-10-19", "currency": "INR", "people": {p: {} for p in PEOPLE}, "treasurer": "asha", "collect_from": "2026-09-25"}
MODE_A_DOC = {k: v for k, v in TRIP_DOC.items() if k not in ("treasurer", "collect_from")}
CONFIG = {"group_room": "g", "people": {p: {"id": p, "name": p.title(), "human_sender": f"@human-{p}", "agent_sender": f"@agent-{p}", "desk_room": f"desk-{p}"} for p in PEOPLE}}


def flow() -> list[dict]:
    """Every message in the order the daemon would log it, a minute apart."""
    steps = [
        ("asha", "human", "call 5000"),
        ("rohan", "agent", claim("rohan")),
        ("rohan", "human", "release p1"),
        ("asha", "agent", match("p1", credit=TOKEN_A)),
        ("asha", "human", "confirm p1"),
        ("asha", "human", "correct p1 mistaken"),
    ]
    return [
        {"room": f"desk-{who}", "sender": f"@{kind}-{who}", "body": body, "at": f"2026-10-09T08:{i:02d}:00+00:00"}
        for i, (who, kind, body) in enumerate(steps)
    ]


def test_build_clerk_attaches_a_book_only_with_a_treasurer():
    assert D.build_clerk(CONFIG, MODE_A_DOC).pot is None
    book = D.build_clerk(CONFIG, TRIP_DOC).pot
    assert book.cfg == CFG


def test_replay_restores_status_decisions_and_original_times():
    first = D.build_clerk(CONFIG, TRIP_DOC)
    for event in flow():
        D.apply_event(first, event)
    assert first.pot.status("p1") == "rejected"
    again = D.build_clerk(CONFIG, TRIP_DOC)
    for event in json.loads(json.dumps(flow())):
        D.apply_event(again, event)
    assert again.pot.decisions == first.pot.decisions
    assert again.pot.decisions[-1].at == "2026-10-09T08:05:00+00:00", "the time it happened, not the time of the restart"
    assert again.balances() == first.balances() and again.pot.calls == first.pot.calls


def test_the_chain_comes_out_the_same_after_replay():
    posts, again = [], D.build_clerk(CONFIG, TRIP_DOC)
    first = D.build_clerk(CONFIG, TRIP_DOC)
    for event in flow():
        posts += [p.text for p in D.apply_event(first, event) if p.room_id == "g"]
    replayed = []
    for event in flow():
        replayed += [p.text for p in D.apply_event(again, event) if p.room_id == "g"]
    assert posts == replayed and P.chain_check(posts) is None and len(posts) == 4


@pytest.mark.parametrize("body", [
    "release p1", "drop p1", "withdraw p1 payment_failed", "confirm p1", "confirm p1 manual", "reject p1 not_received", "ask p1 date_differs",
    "recheck p1", "correct p1 mistaken", "correct p1 received", "call 5000", "call ₹500 rohan", "cancel k1", "pot", C.PREFIX + "{}",
])
def test_every_state_changing_message_is_logged(body):
    assert D.should_log(body, pot=True)


@pytest.mark.parametrize("body", ["call the hotel", "confirm with him", "ask me later", "correct", "I paid at the cafe"])
def test_private_chat_on_a_desk_is_not_written_to_disk(body):
    assert not D.should_log(body, pot=True)


@pytest.mark.parametrize("body, expected", [
    ("confirm p1", False), ("call 5000", False), ("pot", False), ("reject p1 not_received", False), ("release c1", True), ("withdraw t1", True), ("balances", True), (C.PREFIX + "{}", True), ("hello", False),
])
def test_mode_a_should_log_is_unchanged(body, expected):
    assert D.should_log(body) is expected


def test_mode_a_log_line_shape_is_unchanged():
    assert D.event_line("r", "s", "b", {"payload": {"timestamp": 1760000000000}}, pot=False) == {"room": "r", "sender": "s", "body": "b"}


def test_pot_log_line_keeps_the_servers_time():
    line = D.event_line("r", "s", "b", {"payload": {"timestamp": 1760000000000}}, pot=True)
    assert line["at"] == "2025-10-09T08:53:20+00:00" and set(line) == {"room", "sender", "body", "at"}


def test_a_mode_a_log_line_replays_without_touching_the_clock():
    clerk = D.build_clerk(CONFIG, MODE_A_DOC)
    now = clerk.now
    D.apply_event(clerk, {"room": "desk-asha", "sender": "@human-asha", "body": "hello"})
    assert clerk.now is now


def test_the_board_shows_pot_figures_only_on_a_pot_trip():
    doc = {"name": "Goa", **TRIP_DOC}
    assert "pot" not in D.board(D.build_clerk(CONFIG, {"name": "Goa", **MODE_A_DOC}), {"name": "Goa", **MODE_A_DOC})
    clerk = D.build_clerk(CONFIG, TRIP_DOC)
    for event in flow()[:5]:
        D.apply_event(clerk, event)
    view = D.board(clerk, doc)
    assert view["pot"]["treasurer"] == "Asha" and view["pot"]["position"] == 500000


def test_the_board_plan_goes_through_the_treasurer():
    doc = {"name": "Goa", **TRIP_DOC}
    clerk = D.build_clerk(CONFIG, TRIP_DOC)
    for event in flow()[:5]:
        D.apply_event(clerk, event)
    assert D.board(clerk, doc)["plan"] == [{"from": "Asha", "to": "Rohan", "paise": 500000}]
