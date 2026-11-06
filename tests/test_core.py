"""Claims, ledger and clerk. No Switch, no LLM. Runs in seconds."""
from __future__ import annotations

import json
from datetime import date

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from kitty import claims as C
from kitty import ledger as L
from kitty.clerk import Clerk, Person

TRIP = C.Trip("goa-2026", date(2026, 10, 9), date(2026, 10, 12), ("priya", "arjun", "meera"))
GROUP = "room-group"


def make_clerk() -> Clerk:
    people = {
        pid: Person(pid, pid.title(), f"@human-{pid}", f"@agent-{pid}", f"room-desk-{pid}")
        for pid in TRIP.participants
    }
    return Clerk(TRIP, people, GROUP, now=lambda: "2026-11-07T00:00:00+00:00")


def expense(owner="priya", ref="a1", amount=90000, shares=None, day="2026-10-10", category="food", **over) -> str:
    shares = shares if shares is not None else {"priya": 30000, "arjun": 30000, "meera": 30000}
    obj = {"v": 1, "type": "expense", "ref": ref, "trip_id": TRIP.trip_id, "owner_id": owner, "payer_id": owner, "date": day, "amount_paise": amount, "currency": "INR", "category": category, "participant_ids": list(shares), "shares": shares}
    obj.update(over)
    return C.PREFIX + json.dumps(obj)


def transfer(owner="meera", to="arjun", ref="t1", amount=150000, day="2026-10-10") -> str:
    return C.PREFIX + json.dumps({"v": 1, "type": "transfer", "ref": ref, "trip_id": TRIP.trip_id, "owner_id": owner, "from_id": owner, "to_id": to, "date": day, "amount_paise": amount, "currency": "INR"})


def answer(owner, tid, received, ref="r1") -> str:
    return C.PREFIX + json.dumps({"v": 1, "type": "transfer_answer", "ref": ref, "trip_id": TRIP.trip_id, "owner_id": owner, "transfer_id": tid, "received": received})


def desk(pid: str) -> str:
    return f"room-desk-{pid}"


def from_agent(clerk, pid, body):
    return clerk.handle(desk(pid), f"@agent-{pid}", body)


def from_owner(clerk, pid, body):
    return clerk.handle(desk(pid), f"@human-{pid}", body)


def group_texts(posts) -> list[str]:
    return [p.text for p in posts if p.room_id == GROUP]


def released(clerk, pid="priya", **kw) -> str:
    posts = from_agent(clerk, pid, expense(owner=pid, **kw))
    cid = next(i for i in clerk.expenses if clerk.expenses[i].status == "held")
    assert posts
    from_owner(clerk, pid, f"release {cid}")
    return cid


# ------ schema -------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "code"),
    [
        (expense(merchant="ZOMATO"), "unknown_field"),
        (expense(note="villa deposit"), "unknown_field"),
        (expense(description="x"), "unknown_field"),
        (expense(raw_row="x"), "unknown_field"),
        (expense(category="jewellery"), "bad_category"),
        (expense(day="2026-11-01"), "date_outside_trip"),
        (expense(day="not-a-date"), "bad_date"),
        (expense(amount=10**12, shares={"priya": 10**12}), "bad_amount"),
        (expense(amount=0, shares={"priya": 0}), "bad_amount"),
        (expense(amount=90000.5), "bad_amount"),
        (expense(amount=True), "bad_amount"),
        (expense(currency="USD"), "bad_currency"),
        (expense(owner_id="arjun"), "owner_mismatch"),
        (expense(payer_id="arjun"), "payer_not_owner"),
        (expense(trip_id="other"), "wrong_trip"),
        (expense(shares={"priya": 1, "arjun": 2}), "shares_do_not_sum"),
        (expense(amount=3, shares={"priya": 4, "arjun": -1}), "bad_shares"),
        (expense(amount=100, shares={"stranger": 100}), "unknown_participant"),
        (expense(ref="Has Spaces!"), "bad_ref"),
        (expense(ref="<@all> gift"), "bad_ref"),
        ("KITTY/1 {not json", "bad_json"),
        ("KITTY/1 []", "bad_json"),
        ("hello there", "not_a_claim"),
        (C.PREFIX + json.dumps({"v": 2}), "bad_version"),
        (C.PREFIX + json.dumps({"v": 1, "type": "gift"}), "bad_type"),
    ],
)
def test_malformed_claims_are_rejected_with_a_code(body, code):
    with pytest.raises(C.ClaimError) as err:
        C.parse(body, TRIP, "priya")
    assert err.value.code == code


def test_valid_expense_parses():
    item = C.parse(expense(), TRIP, "priya")
    assert isinstance(item, C.Expense)
    assert item.amount_paise == 90000
    assert item.participant_ids == ("priya", "arjun", "meera")


def test_rejection_never_echoes_the_offending_text():
    clerk = make_clerk()
    secret = "SECRET-MERCHANT"
    posts = from_agent(clerk, "priya", expense(merchant=secret))
    assert posts and all(secret not in p.text for p in posts)
    posts = from_agent(clerk, "priya", f"{secret} was a gift for Arjun")
    assert posts and all(secret not in p.text for p in posts)
    assert secret not in json.dumps(clerk.audit)
    assert not clerk.expenses


@given(st.integers(1, C.MAX_AMOUNT_PAISE), st.integers(1, 12))
def test_even_shares_always_sum(amount, n):
    people = [f"p{i}" for i in range(n)]
    shares = C.even_shares(amount, people)
    assert sum(shares.values()) == amount
    assert max(shares.values()) - min(shares.values()) <= 1


def test_rupee_format():
    assert C.rupees(150000) == "₹1,500.00"
    assert C.rupees(12345678) == "₹1,23,456.78"
    assert C.rupees(-5) == "-₹0.05"


# ------ ledger ------------------------------------------------------------

PEOPLE = [f"p{i}" for i in range(6)]
lines_strategy = st.lists(
    st.tuples(st.sampled_from(PEOPLE), st.integers(1, 10_000_000), st.lists(st.sampled_from(PEOPLE), min_size=1, max_size=6, unique=True)),
    max_size=30,
)


def to_lines(raw) -> list[L.Line]:
    return [L.Line(payer, amount, tuple(C.even_shares(amount, members).items())) for payer, amount, members in raw]


@given(lines_strategy)
@settings(max_examples=200)
def test_balances_net_to_zero_and_settle_exactly(raw):
    net = L.balances(PEOPLE, to_lines(raw), [])
    assert sum(net.values()) == 0
    plan = L.settle(net)
    assert len(plan) <= max(len(PEOPLE) - 1, 0)
    assert all(p.amount_paise > 0 for p in plan)
    assert not any(L.balances(PEOPLE, to_lines(raw), plan).values())


def test_settle_prefers_exact_matches():
    plan = L.settle({"a": -500, "b": -300, "c": 500, "d": 300})
    assert {(p.from_id, p.to_id, p.amount_paise) for p in plan} == {("a", "c", 500), ("b", "d", 300)}


def test_lines_that_do_not_sum_are_refused():
    with pytest.raises(ValueError):
        L.obligations(["a"], [L.Line("a", 100, (("a", 99),))])


# ------ release enforcement ------------------------------------------------


def test_nothing_reaches_the_group_before_the_owner_releases():
    clerk = make_clerk()
    posts = from_agent(clerk, "priya", expense())
    assert not group_texts(posts)
    assert clerk.expenses["c1"].status == "held"
    assert clerk.lines() == []


def test_owner_release_publishes_it():
    clerk = make_clerk()
    from_agent(clerk, "priya", expense())
    posts = from_owner(clerk, "priya", "release c1")
    assert len(group_texts(posts)) == 1
    rec = clerk.expenses["c1"]
    assert (rec.status, rec.released_by) == ("released", "@human-priya")
    assert rec.released_at


def test_agent_cannot_release_its_own_claim():
    clerk = make_clerk()
    from_agent(clerk, "priya", expense())
    posts = from_agent(clerk, "priya", "release c1")
    assert not group_texts(posts)
    assert clerk.expenses["c1"].status == "held"
    assert "Only" in posts[0].text


def test_another_person_cannot_release_it_from_their_own_desk():
    clerk = make_clerk()
    from_agent(clerk, "priya", expense())
    posts = from_owner(clerk, "arjun", "release c1")
    assert not group_texts(posts)
    assert clerk.expenses["c1"].status == "held"


def test_a_stranger_in_a_desk_is_refused():
    clerk = make_clerk()
    from_agent(clerk, "priya", expense())
    posts = clerk.handle(desk("priya"), "@human-arjun", "release c1")
    assert "Refused" in posts[0].text
    assert clerk.expenses["c1"].status == "held"


def test_release_in_the_group_room_is_refused():
    clerk = make_clerk()
    from_agent(clerk, "priya", expense())
    posts = clerk.handle(GROUP, "@human-priya", "release c1")
    assert "Refused" in posts[0].text
    assert clerk.expenses["c1"].status == "held"


def test_release_of_unknown_or_already_released_claim_is_refused():
    clerk = make_clerk()
    assert "Refused" in from_owner(clerk, "priya", "release c99")[0].text
    cid = released(clerk)
    posts = from_owner(clerk, "priya", f"release {cid}")
    assert "Refused" in posts[0].text
    assert not group_texts(posts)


def test_agent_in_a_room_the_clerk_does_not_serve_is_ignored():
    clerk = make_clerk()
    assert clerk.handle("room-private-meera", "@agent-meera", expense(owner="meera")) == []
    assert not clerk.expenses


def test_group_posts_never_contain_anything_but_typed_fields():
    clerk = make_clerk()
    from_agent(clerk, "priya", expense(ref="zomato1"))
    posts = from_owner(clerk, "priya", "release c1")
    text = group_texts(posts)[0]
    assert "zomato" not in text.lower()
    assert text == "**c1** ₹900.00 · food · 10 Oct · paid by Priya · Priya ₹300.00, Arjun ₹300.00, Meera ₹300.00"


# ------ duplicates and conflicts ---------------------------------------------


def test_duplicate_claim_is_rejected():
    clerk = make_clerk()
    from_agent(clerk, "priya", expense(ref="a1"))
    posts = from_agent(clerk, "priya", expense(ref="a2"))
    assert "duplicate_claim" in posts[0].text
    assert len(clerk.expenses) == 1


def test_duplicate_ref_is_rejected():
    clerk = make_clerk()
    from_agent(clerk, "priya", expense(ref="a1"))
    posts = from_agent(clerk, "priya", expense(ref="a1", amount=60000, shares={"priya": 30000, "arjun": 30000}))
    assert "duplicate_ref" in posts[0].text


def test_two_people_claiming_one_expense_is_a_conflict_that_counts_for_neither():
    clerk = make_clerk()
    released(clerk, "priya", amount=90000)
    posts = from_agent(clerk, "arjun", expense(owner="arjun", ref="b1", amount=90000, shares={"arjun": 45000, "meera": 45000}))
    cid = list(clerk.expenses)[-1]
    assert posts
    posts = from_owner(clerk, "arjun", f"release {cid}")
    assert any("conflict" in t.lower() for t in group_texts(posts))
    assert clerk.lines() == []
    posts = from_owner(clerk, "arjun", f"drop {cid}")
    assert len(clerk.lines()) == 1
    assert clerk.expenses[cid].status == "dropped"


# ------ transfers: the Meera and Arjun story ---------------------------------


def start_transfer(clerk):
    from_agent(clerk, "meera", transfer())
    return from_owner(clerk, "meera", "release t1")


def test_a_released_transfer_is_not_counted_and_asks_the_receiver():
    clerk = make_clerk()
    posts = start_transfer(clerk)
    assert clerk.transfers["t1"].status == "awaiting_receiver"
    assert clerk.confirmed() == []
    assert any(p.room_id == desk("arjun") and p.text.startswith("KITTY_CHECK t1") for p in posts)
    assert any("Not counted" in t for t in group_texts(posts))


def test_both_sides_confirming_counts_the_transfer():
    clerk = make_clerk()
    start_transfer(clerk)
    posts = from_agent(clerk, "arjun", answer("arjun", "t1", True))
    assert not group_texts(posts), "an agent's answer alone must not publish anything"
    assert clerk.transfers["t1"].status == "awaiting_receiver"
    posts = from_owner(clerk, "arjun", "release t1")
    assert any("confirmed by both sides" in t for t in group_texts(posts))
    assert clerk.confirmed() == [L.Payment("meera", "arjun", 150000)]


def test_one_sided_transfer_is_challenged_and_not_counted():
    clerk = make_clerk()
    start_transfer(clerk)
    from_agent(clerk, "arjun", answer("arjun", "t1", False))
    posts = from_owner(clerk, "arjun", "release t1")
    assert clerk.transfers["t1"].status == "challenged"
    assert clerk.confirmed() == []
    assert any("NOT CONFIRMED" in t for t in group_texts(posts))
    assert any(p.room_id == desk("meera") and p.text == "KITTY_RECHECK t1" for p in posts)


def test_withdrawal_after_reversal_leaves_balances_unchanged():
    clerk = make_clerk()
    released(clerk, "priya")
    before = clerk.balances()
    start_transfer(clerk)
    from_agent(clerk, "arjun", answer("arjun", "t1", False))
    from_owner(clerk, "arjun", "release t1")
    posts = from_owner(clerk, "meera", "withdraw t1 payment_failed")
    assert clerk.transfers["t1"].status == "withdrawn"
    assert clerk.balances() == before
    assert any("withdrawn by Meera" in t and "never arrived" in t for t in group_texts(posts))


def test_only_the_transfers_owner_can_withdraw_it():
    clerk = make_clerk()
    start_transfer(clerk)
    posts = from_owner(clerk, "arjun", "withdraw t1 payment_failed")
    assert "Refused" in posts[0].text
    assert clerk.transfers["t1"].status == "awaiting_receiver"


def test_an_agent_cannot_withdraw():
    clerk = make_clerk()
    start_transfer(clerk)
    posts = from_agent(clerk, "meera", "withdraw t1 payment_failed")
    assert "Only" in posts[0].text
    assert clerk.transfers["t1"].status == "awaiting_receiver"


def test_receiver_can_keep_the_answer_private():
    clerk = make_clerk()
    start_transfer(clerk)
    from_agent(clerk, "arjun", answer("arjun", "t1", False))
    posts = from_owner(clerk, "arjun", "drop t1")
    assert not group_texts(posts)
    assert clerk.transfers["t1"].status == "awaiting_receiver"


def test_an_answer_for_someone_elses_transfer_is_rejected():
    clerk = make_clerk()
    start_transfer(clerk)
    posts = from_agent(clerk, "priya", answer("priya", "t1", True))
    assert "no_such_question" in posts[0].text
    assert clerk.confirmed() == []


# ------ end to end, with the full trip -----------------------------------------


def test_whole_trip_reaches_everyones_square():
    clerk = make_clerk()
    released(clerk, "priya", ref="v", amount=1200000, shares={"priya": 400000, "arjun": 400000, "meera": 400000}, category="stay")
    released(clerk, "arjun", ref="s", amount=300000, shares={"priya": 100000, "arjun": 100000, "meera": 100000}, category="travel", day="2026-10-09")
    assert clerk.balances() == {"priya": 700000, "arjun": -200000, "meera": -500000}

    plan = L.settle(clerk.balances())
    last = []
    for i, pay in enumerate(plan):
        from_agent(clerk, pay.from_id, transfer(owner=pay.from_id, to=pay.to_id, ref=f"s{i}", amount=pay.amount_paise))
        tid = list(clerk.transfers)[-1]
        from_owner(clerk, pay.from_id, f"release {tid}")
        from_agent(clerk, pay.to_id, answer(pay.to_id, tid, True, ref=f"a{i}"))
        last = from_owner(clerk, pay.to_id, f"release {tid}")
    assert all(v == 0 for v in clerk.balances().values())
    assert any("Everyone's square" in t for t in group_texts(last))
