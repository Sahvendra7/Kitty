"""Money in a Pot trip. The ledger is unchanged, and these figures are read from it."""
from __future__ import annotations

from datetime import date

from hypothesis import given
from hypothesis import strategies as st

from kitty import claims as C
from kitty import ledger as L
from kitty import pot as P

PEOPLE = ["t", "a", "b", "c", "d", "e"]
MEMBERS = PEOPLE[1:]
CFG = P.PotConfig("t", date(2026, 9, 25))
SIX = ["asha", "rohan", "meera", "dev", "esha", "farhan"]
ASHA = P.PotConfig("asha", date(2026, 9, 25))
amounts = st.integers(1, 5_000_000)


def share_of(person, lines) -> int:
    return sum(dict(x.shares).get(person, 0) for x in lines)


def even(payer, amount, who=PEOPLE) -> L.Line:
    return L.Line(payer, amount, tuple(C.even_shares(amount, list(who)).items()))


histories = st.fixed_dictionaries({
    "contributions": st.lists(st.builds(L.Payment, st.sampled_from(MEMBERS), st.just("t"), amounts), max_size=10),
    "paid_back": st.lists(st.builds(L.Payment, st.just("t"), st.sampled_from(MEMBERS), amounts), max_size=6),
    "between": st.lists(st.tuples(st.sampled_from(MEMBERS), st.sampled_from(MEMBERS), amounts).filter(lambda x: x[0] != x[1]).map(lambda x: L.Payment(*x)), max_size=4),
    "lines": st.lists(st.tuples(st.sampled_from(PEOPLE), amounts, st.lists(st.sampled_from(PEOPLE), min_size=1, max_size=6, unique=True)).map(lambda x: even(x[0], x[1], x[2])), max_size=10),
})


@given(histories)
def test_any_history_keeps_every_invariant(h):
    moved = h["contributions"] + h["paid_back"] + h["between"]
    net = L.balances(PEOPLE, h["lines"], moved)
    f = P.figures(CFG, PEOPLE, [], h["contributions"], h["lines"], moved)
    assert sum(net.values()) == 0
    assert f.position == -net["t"] - share_of("t", h["lines"])
    plan = P.settle_via(net, "t")
    assert not any(L.balances(PEOPLE, h["lines"], moved + plan).values())
    assert all("t" in (p.from_id, p.to_id) for p in plan) and len(plan) <= len(PEOPLE) - 1


def worked_example():
    lines = [even("asha", 2400000, SIX), even("asha", 900000, SIX), even("dev", 360000, SIX)]
    money_in = [L.Payment(m, "asha", 500000) for m in ("rohan", "esha", "farhan", "dev")] + [L.Payment("meera", "asha", 300000)]
    return lines, money_in


def test_spec_worked_example_to_the_paise():
    lines, money_in = worked_example()
    net = L.balances(SIX, lines, money_in)
    assert net == {"asha": 390000, "rohan": -110000, "meera": -310000, "dev": 250000, "esha": -110000, "farhan": -110000}
    f = P.figures(ASHA, SIX, [(500000, ("rohan", "meera", "dev", "esha", "farhan"))], money_in, lines, money_in)
    assert (f.collected, f.spent, f.paid_out, f.position) == (2300000, 3300000, 0, -1000000)
    plan = {(p.from_id, p.to_id, p.amount_paise) for p in P.settle_via(net, "asha")}
    assert plan == {("rohan", "asha", 110000), ("esha", "asha", 110000), ("farhan", "asha", 110000), ("meera", "asha", 310000), ("asha", "dev", 250000)}


def test_spec_correction_changes_only_the_two_people_involved():
    lines, money_in = worked_example()
    net = L.balances(SIX, lines, money_in[:-1])
    assert net == {"asha": 690000, "rohan": -110000, "meera": -610000, "dev": 250000, "esha": -110000, "farhan": -110000}


def test_part_payment_and_above_ask():
    ask = [(500000, ("a", "b"))]
    f = P.figures(CFG, PEOPLE, ask, [L.Payment("a", "t", 300000), L.Payment("b", "t", 1500000)], [], [])
    assert (f.asked["a"], f.credited["a"], f.outstanding["a"], f.above["a"]) == (500000, 300000, 200000, 0)
    assert (f.outstanding["b"], f.above["b"]) == (0, 1000000)


def test_the_treasurer_is_never_asked():
    f = P.figures(CFG, PEOPLE, [(500000, tuple(MEMBERS))], [], [], [])
    assert f.asked["t"] == 0 and f.outstanding["t"] == 0 and f.asked["a"] == 500000


def test_calls_add_up():
    f = P.figures(CFG, PEOPLE, [(500000, tuple(MEMBERS)), (50000, ("a",))], [], [], [])
    assert f.asked["a"] == 550000 and f.asked["b"] == 500000


def test_reimbursed_then_refunded_split():
    spent = even("a", 60000, ["a", "b"])
    f = P.figures(CFG, PEOPLE, [], [], [spent], [L.Payment("t", "a", 100000)])
    assert (f.reimbursed["a"], f.refunded["a"], f.paid_out) == (60000, 40000, 100000)


def test_a_transfer_between_members_is_not_paid_out():
    f = P.figures(CFG, PEOPLE, [], [], [], [L.Payment("a", "b", 100000)])
    assert f.paid_out == 0 and f.position == 0


def test_a_call_never_changes_a_balance():
    lines = [even("t", 60000)]
    before = L.balances(PEOPLE, lines, [])
    P.figures(CFG, PEOPLE, [(500000, tuple(MEMBERS))], [], lines, [])
    assert L.balances(PEOPLE, lines, []) == before


def test_hub_plan_is_empty_when_everyone_is_square():
    assert P.settle_via({p: 0 for p in PEOPLE}, "t") == []
