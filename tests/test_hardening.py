"""Regression tests from the release-hardening review (docs/CODE_REVIEW_REPORT.md).

Each test names the finding it pins down. Expected figures are worked out by hand or by the small,
separate oracle below, never by calling the production ledger to produce the expected answer.
"""
from __future__ import annotations

import json
from datetime import date

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from kitty import claims as C
from kitty import clerk_daemon as D
from kitty import ledger as L
from tests import pot_helpers as PH
from tests.test_core import TRIP, answer, desk, expense, from_agent, from_owner, group_texts, make_clerk, released, transfer


# ---- F1: a withdrawn transfer must never become confirmed --------------------------------------


def _answer_held(clerk, received=True):
    from_agent(clerk, "meera", transfer())
    from_owner(clerk, "meera", "release t1")
    from_agent(clerk, "arjun", answer("arjun", "t1", received))
    assert clerk.transfers["t1"].answer_status == "held"


def test_f1_withdrawn_transfer_cannot_be_confirmed_by_a_held_answer():
    clerk = make_clerk()
    _answer_held(clerk)
    before = clerk.balances()
    from_owner(clerk, "meera", "withdraw t1 payment_failed")
    posts = from_owner(clerk, "arjun", "release t1")
    assert clerk.transfers["t1"].status == "withdrawn"
    assert clerk.confirmed() == []
    assert clerk.balances() == before
    assert not any("confirmed" in t for t in group_texts(posts))
    assert any(p.room_id == desk("arjun") and p.text.startswith("Refused") for p in posts)


def test_f1_withdrawal_discards_the_receivers_held_answer():
    clerk = make_clerk()
    _answer_held(clerk, received=False)
    from_owner(clerk, "meera", "withdraw t1 mistaken")
    assert clerk.transfers["t1"].answer_status is None
    posts = from_owner(clerk, "arjun", "release t1")
    assert clerk.transfers["t1"].status == "withdrawn"
    assert not group_texts(posts)


def test_f1_positive_control_answer_still_confirms_an_open_transfer():
    clerk = make_clerk()
    _answer_held(clerk)
    from_owner(clerk, "arjun", "release t1")
    assert clerk.transfers["t1"].status == "confirmed"
    assert clerk.confirmed() == [L.Payment("meera", "arjun", 150000)]


def test_f1_same_hole_is_closed_on_a_pot_trip():
    clerk = PH.make()
    PH.agent(clerk, "asha", PH.transfer("asha", "rohan", 100000))
    PH.human(clerk, "asha", "release t1")
    clerk.handle("desk-rohan", "@agent-rohan", C.PREFIX + json.dumps({"v": 1, "type": "transfer_answer", "ref": "q1", "trip_id": "goa-1", "owner_id": "rohan", "transfer_id": "t1", "received": True}))
    PH.human(clerk, "asha", "withdraw t1 payment_failed")
    PH.human(clerk, "rohan", "release t1")
    assert clerk.transfers["t1"].status == "withdrawn"
    assert clerk.confirmed() == []


# ---- F2: one expense released by three people must not count twice ------------------------------

SAME = {"priya": 30000, "arjun": 30000, "meera": 30000}


def test_f2_third_identical_claim_joins_the_conflict_instead_of_counting():
    clerk = make_clerk()
    released(clerk, "priya", ref="a1")
    released(clerk, "arjun", ref="b1", shares=SAME)
    third = released(clerk, "meera", ref="m1", shares=SAME)
    assert clerk.expenses[third].status == "conflict_hold"
    assert clerk.lines() == []


def test_f2_dropping_one_of_three_keeps_the_rest_held():
    clerk = make_clerk()
    a = released(clerk, "priya", ref="a1")
    b = released(clerk, "arjun", ref="b1", shares=SAME)
    c = released(clerk, "meera", ref="m1", shares=SAME)
    from_owner(clerk, "arjun", f"drop {b}")
    assert {clerk.expenses[a].status, clerk.expenses[c].status} == {"conflict_hold"}
    assert clerk.lines() == []
    posts = from_owner(clerk, "meera", f"drop {c}")
    assert clerk.expenses[a].status == "released"
    assert len(clerk.lines()) == 1
    assert any(f"**{a}** now counts" in t for t in group_texts(posts))
    # By hand: Priya paid 900 and owes 300, so she is owed 600. Arjun and Meera owe 300 each.
    assert clerk.balances() == {"priya": 60000, "arjun": -30000, "meera": -30000}


def test_f2_two_way_conflict_is_unchanged():
    clerk = make_clerk()
    a = released(clerk, "priya", ref="a1")
    b = released(clerk, "arjun", ref="b1", shares=SAME)
    posts = from_owner(clerk, "priya", f"drop {a}")
    assert clerk.expenses[b].status == "released"
    assert group_texts(posts) == [f"Conflict cleared: Priya withdrew {a}. **{b}** now counts: ₹900.00 · food · 10 Oct · paid by Arjun · Priya ₹300.00, Arjun ₹300.00, Meera ₹300.00"]


# ---- F3: an event delivered twice must be applied once ------------------------------------------


def test_f3_event_line_carries_the_message_id_when_there_is_one():
    line = D.event_line("r", "s", "b", {"payload": {"message_id": "m-1"}}, pot=False)
    assert line == {"room": "r", "sender": "s", "body": "b", "mid": "m-1"}


def test_f3_seen_ids_come_from_the_log_and_skip_a_redelivery():
    lines = [{"room": "r", "sender": "s", "body": "call 5000", "mid": "m-1"}, {"room": "r", "sender": "s", "body": "x"}]
    assert D.seen_ids(lines) == {"m-1"}


def test_f3_redelivered_call_does_not_ask_twice(tmp_path):
    """Crash after the log line, before the cursor: the same message comes again and must be skipped."""
    from tests.test_pot_replay import CONFIG, TRIP_DOC

    clerk = D.build_clerk(CONFIG, TRIP_DOC)
    event = D.event_line("desk-asha", "@human-asha", "call 5000", {"payload": {"message_id": "m-1", "timestamp": 1760000000000}}, pot=True)
    D.apply_event(clerk, event)
    seen = D.seen_ids([event])
    again = D.event_line("desk-asha", "@human-asha", "call 5000", {"payload": {"message_id": "m-1", "timestamp": 1760000000000}}, pot=True)
    assert D.is_duplicate(again, seen)
    assert not D.is_duplicate(D.event_line("desk-asha", "@human-asha", "call 5000", {"payload": {"message_id": "m-2"}}, pot=True), seen)
    assert not D.is_duplicate({"room": "r", "sender": "s", "body": "b"}, seen), "an old log line with no id is never a duplicate"
    assert len(clerk.pot.calls) == 1


# ---- Mode A state table: every command against every transfer state ------------------------------

TRANSFER_STATES = ("held", "awaiting_receiver", "confirmed", "challenged", "withdrawn", "dropped")


def _transfer_in(state):
    clerk = make_clerk()
    from_agent(clerk, "meera", transfer())
    if state == "dropped":
        from_owner(clerk, "meera", "drop t1")
    elif state != "held":
        from_owner(clerk, "meera", "release t1")
        if state in ("confirmed", "challenged"):
            from_agent(clerk, "arjun", answer("arjun", "t1", state == "confirmed"))
            from_owner(clerk, "arjun", "release t1")
        elif state == "withdrawn":
            from_owner(clerk, "meera", "withdraw t1")
    assert clerk.transfers["t1"].status == state
    return clerk


ALLOWED = {
    ("held", "meera", "release t1"): "awaiting_receiver",
    ("held", "meera", "drop t1"): "dropped",
    ("awaiting_receiver", "meera", "withdraw t1"): "withdrawn",
    ("challenged", "meera", "withdraw t1"): "withdrawn",
}


@pytest.mark.parametrize("state", TRANSFER_STATES)
@pytest.mark.parametrize("who, command", [("meera", "release t1"), ("meera", "drop t1"), ("meera", "withdraw t1"), ("arjun", "release t1"), ("arjun", "drop t1"), ("arjun", "withdraw t1"), ("priya", "release t1")])
def test_transfer_transitions_are_listed_or_refused(state, who, command):
    clerk = _transfer_in(state)
    before = clerk.balances()
    from_owner(clerk, who, command)
    after = clerk.transfers["t1"].status
    assert after == ALLOWED.get((state, who, command), state)
    if after != "confirmed":
        assert clerk.balances() == before


# ---- independent oracle for balances and settlement ---------------------------------------------


def oracle_balances(people, expenses, payments):
    """Paid minus consumed, plus sent minus received. Written separately from kitty.ledger on purpose."""
    paid = dict.fromkeys(people, 0)
    used = dict.fromkeys(people, 0)
    sent = dict.fromkeys(people, 0)
    got = dict.fromkeys(people, 0)
    for payer, amount, shares in expenses:
        paid[payer] += amount
        for who, value in shares.items():
            used[who] += value
    for frm, to, amount in payments:
        sent[frm] += amount
        got[to] += amount
    return {p: paid[p] - used[p] + sent[p] - got[p] for p in people}


PEOPLE = ("a", "b", "c", "d")


@st.composite
def scenario(draw):
    expenses = []
    for _ in range(draw(st.integers(0, 8))):
        payer = draw(st.sampled_from(PEOPLE))
        who = draw(st.lists(st.sampled_from(PEOPLE), min_size=1, max_size=4, unique=True))
        amount = draw(st.integers(1, C.MAX_AMOUNT_PAISE))
        expenses.append((payer, amount, C.even_shares(amount, who)))
    payments = [(f, t, draw(st.integers(1, 10**7))) for f, t in draw(st.lists(st.tuples(st.sampled_from(PEOPLE), st.sampled_from(PEOPLE)).filter(lambda x: x[0] != x[1]), max_size=5))]
    return expenses, payments


@given(scenario())
@settings(max_examples=300, deadline=None)
def test_ledger_matches_an_independent_oracle_and_settles_exactly(case):
    expenses, payments = case
    for _, amount, shares in expenses:
        assert sum(shares.values()) == amount
        assert max(shares.values()) - min(shares.values()) <= 1
    lines = [L.Line(p, a, tuple(s.items())) for p, a, s in expenses]
    net = L.balances(PEOPLE, lines, [L.Payment(*p) for p in payments])
    assert net == oracle_balances(PEOPLE, expenses, payments)
    plan = L.settle(net)
    assert len(plan) <= len(PEOPLE) - 1
    assert all(p.amount_paise > 0 and p.from_id != p.to_id for p in plan)
    after = oracle_balances(PEOPLE, expenses, payments + [(p.from_id, p.to_id, p.amount_paise) for p in plan])
    assert not any(after.values())


@pytest.mark.parametrize("amount, n, expected", [
    (100000, 3, [33334, 33333, 33333]),   # ₹1,000 three ways: the extra paisa goes to the first person.
    (100, 3, [34, 33, 33]),
    (1, 3, [1, 0, 0]),
    (250001, 4, [62501, 62500, 62500, 62500]),
    (C.MAX_AMOUNT_PAISE, 12, [8333334, 8333334, 8333334, 8333334] + [8333333] * 8),
])
def test_even_shares_by_hand(amount, n, expected):
    assert list(C.even_shares(amount, [f"p{i}" for i in range(n)]).values()) == expected


@given(st.dictionaries(st.sampled_from(PEOPLE), st.integers(-10**9, 10**9), min_size=1))
def test_pot_hub_plan_clears_every_balance(raw):
    raw = dict(raw)
    hub = next(iter(raw))
    raw[hub] -= sum(raw.values())
    plan = __import__("kitty.pot", fromlist=["settle_via"]).settle_via(raw, hub)
    left = dict(raw)
    for p in plan:
        assert p.amount_paise > 0 and hub in (p.from_id, p.to_id)
        left[p.from_id] += p.amount_paise
        left[p.to_id] -= p.amount_paise
    assert not any(left.values())


@pytest.mark.parametrize("value", [1.5, 100.0, "100", True, None, -1, 0, C.MAX_AMOUNT_PAISE + 1, 2**63])
def test_money_must_be_a_positive_whole_number_of_paise(value):
    body = expense(amount=value, shares={"priya": value} if not isinstance(value, (list, dict)) else value)
    with pytest.raises(C.ClaimError):
        C.parse(body, TRIP, "priya")
