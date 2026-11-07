"""The private side of a Pot trip, with real agents and rooms enforced the way the server enforces them."""
from __future__ import annotations

import json
import re

import pytest

from kitty import claims as C
from kitty import statements as S
from tests.pot_helpers import ASHA_CSV, CFG, ROHAN_CSV, TRIP, PotWorld, rows_from


@pytest.fixture
def world(tmp_path) -> PotWorld:
    return PotWorld({"asha": rows_from(ASHA_CSV, tmp_path, "asha.csv"), "rohan": rows_from(ROHAN_CSV, tmp_path, "rohan.csv")})


def rohan_claims(world) -> str:
    world.say("rohan", "private", "paid 5000 2026-10-09 ref U441001")
    pid = next(iter(world.clerk.pot.records))
    world.say("rohan", "desk", f"release {pid}")
    return pid


def test_check_posts_a_typed_match_and_a_private_chit(world):
    pid = rohan_claims(world)
    rec = world.clerk.pot.records[pid]
    assert world.clerk.pot.status(pid) == "recipient_review" and rec.candidate == "exact" and rec.credit_token
    assert "Found it: 9 Oct." in world.texts("private-asha")
    world.say("asha", "desk", f"confirm {pid}")
    assert world.clerk.pot.status(pid) == "confirmed" and world.clerk.balances()["rohan"] == 500000


def test_no_narration_in_the_private_chit(world):
    rohan_claims(world)
    private = world.texts("private-asha")
    assert "UPI/CR" not in private and "441001" not in private and "SALARY" not in private


def test_none_never_says_not_received(world):
    world.say("meera", "private", "paid 3000 2026-10-11")
    pid = next(iter(world.clerk.pot.records))
    world.say("meera", "desk", f"release {pid}")
    assert world.clerk.pot.records[pid].candidate == "none"
    chit = world.texts("private-asha").lower()
    assert "nothing yet" in chit and "your statement ends 17 oct" in chit
    for words in ("not received", "did not", "didn't arrive", "nothing like that reached"):
        assert words not in chit
    assert "not received" not in world.texts("group").lower()
    world.say("asha", "desk", f"confirm {pid} manual")
    assert world.clerk.pot.status(pid) == "confirmed", "manual confirm still works"


def test_a_treasurer_with_no_statement_gets_a_clear_note():
    w = PotWorld({})
    w.say("rohan", "private", "paid 5000 2026-10-09")
    pid = next(iter(w.clerk.pot.records))
    w.say("rohan", "desk", f"release {pid}")
    assert "Nothing yet. You haven't added a statement." in w.texts("private-asha")


def test_recheck_finds_a_credit_added_later(tmp_path):
    w = PotWorld({"asha": rows_from(ASHA_CSV, tmp_path, "asha.csv")})
    w.say("meera", "private", "paid 3000 2026-10-18")
    pid = next(iter(w.clerk.pot.records))
    w.say("meera", "desk", f"release {pid}")
    assert w.clerk.pot.records[pid].candidate == "none"
    later = ASHA_CSV + "2026-10-18,09:00,UPI/CR/MEERA S/441003/GPAY,U441003,,3000.00,,XXXXXX2101\n"
    w.agents["asha"].rows = rows_from(later, tmp_path, "later.csv")
    w.say("asha", "desk", f"recheck {pid}")
    assert w.clerk.pot.records[pid].candidate == "exact"
    w.say("asha", "desk", f"confirm {pid}")
    assert w.clerk.pot.status(pid) == "confirmed"


def test_memory_wipe_keeps_the_clerk_check(world, tmp_path):
    first = rohan_claims(world)
    world.say("asha", "desk", f"confirm {first}")
    world.agents["asha"].bound.clear()
    world.say("rohan", "private", "paid 5000 2026-10-10")
    second = list(world.clerk.pot.records)[-1]
    world.say("rohan", "desk", f"release {second}")
    assert world.clerk.pot.records[second].candidate == "exact", "with no memory the agent offers the row again"
    world.say("asha", "desk", f"confirm {second}")
    assert "credit_already_used" in world.texts("desk-asha") and world.clerk.pot.status(second) == "recipient_review"


def test_a_confirmed_row_is_not_offered_again(world):
    first = rohan_claims(world)
    world.say("asha", "desk", f"confirm {first}")
    world.say("rohan", "private", "paid 5000 2026-10-10")
    second = list(world.clerk.pot.records)[-1]
    world.say("rohan", "desk", f"release {second}")
    assert world.clerk.pot.records[second].candidate == "none"


@pytest.mark.parametrize("how", ["withdraw", "reject", "correct"])
def test_a_row_is_free_again_when_its_claim_stops_counting(world, how):
    first = rohan_claims(world)
    if how == "withdraw":
        world.say("rohan", "desk", f"withdraw {first}")
    elif how == "reject":
        world.say("asha", "desk", f"reject {first} not_received")
    else:
        world.say("asha", "desk", f"confirm {first}")
        world.say("asha", "desk", f"correct {first} mistaken")
    world.say("rohan", "private", "paid 5000 2026-10-10")
    second = list(world.clerk.pot.records)[-1]
    world.say("rohan", "desk", f"release {second}")
    assert world.clerk.pot.records[second].candidate == "exact"


def test_correct_back_in_holds_the_row_again(world):
    first = rohan_claims(world)
    world.say("asha", "desk", f"confirm {first}")
    world.say("asha", "desk", f"correct {first} mistaken")
    world.say("asha", "desk", f"correct {first} received")
    assert first in world.agents["asha"].bound


def test_bound_rows_survive_restore(world):
    first = rohan_claims(world)
    world.say("asha", "desk", f"confirm {first}")
    saved = json.loads(json.dumps(world.agents["asha"].to_state()))
    fresh = PotWorld({"asha": world.agents["asha"].rows}).agents["asha"]
    fresh.restore(saved)
    assert fresh.bound == world.agents["asha"].bound and fresh.bound


def test_member_statement_files_contribution_not_transfer(world):
    world.begin("rohan")
    contributions = [r.claim for r in world.clerk.pot.records.values()]
    assert [(c.amount_paise, c.date.isoformat()) for c in contributions] == [(500000, "2026-10-09")], "the pre-trip payment to Asha"
    assert contributions[0].ref_token, "its reference went as a token"
    assert [t.claim.to_id for t in world.clerk.transfers.values()] == ["meera"], "control: a payment to a friend stays a transfer"
    assert "U441001" not in world.texts("desk-rohan") and "441001" not in world.texts("desk-rohan")


def test_a_second_scan_does_not_file_twice(world):
    world.begin("rohan")
    world.say("rohan", "private", "rescan")
    assert len(world.clerk.pot.records) == 1


def test_typed_paid_with_no_statement():
    w = PotWorld({})
    w.say("meera", "private", "paid 3000 2026-10-11")
    rec = next(iter(w.clerk.pot.records.values()))
    assert rec.claim.amount_paise == 300000 and rec.claim.ref_token is None and rec.claim.ref_last4 is None
    assert w.clerk.pot.status(rec.id) == "held", "it waits for its owner"


def test_show_sends_only_the_last_four():
    w = PotWorld({})
    w.say("meera", "private", "paid 3000 2026-10-11 ref U441001 show")
    rec = next(iter(w.clerk.pot.records.values()))
    w.say("meera", "desk", f"release {rec.id}")
    assert rec.claim.ref_last4 == "1001"
    assert "ref …1001" in w.texts("group") and "U441001" not in w.not_private()


def test_without_show_nothing_of_the_reference_shows():
    w = PotWorld({})
    w.say("meera", "private", "paid 3000 2026-10-11 ref U441001")
    rec = next(iter(w.clerk.pot.records.values()))
    w.say("meera", "desk", f"release {rec.id}")
    assert rec.claim.ref_last4 is None and "1001" not in w.texts("group")


@pytest.mark.parametrize("text, paise", [
    ("paid ₹5,000 2026-10-09", 500000), ("paid 5,000.50 2026-10-09 ref U441001 show", 500050), ("PAID 5000 2026-10-09", 500000),
])
def test_typed_input_that_works(text, paise):
    w = PotWorld({})
    w.say("rohan", "private", text)
    assert next(iter(w.clerk.pot.records.values())).claim.amount_paise == paise


@pytest.mark.parametrize("text", [
    "paid 5000 rs 2026-10-09", "paid abc 2026-10-09", "paid 0 2026-10-09", "paid 5000 9 Oct", "paid 5000", "paid", "paid 5000 2026-13-40", "paid 5000 2026-10-09 ref a!", "paid 5000 2026-10-09 ref U441001 extra", "paid -5 2026-10-09",
])
def test_typed_input_that_does_not_gets_a_plain_answer(text):
    w = PotWorld({})
    w.say("rohan", "private", text)
    assert not w.clerk.pot.records and not w.brain_calls
    assert "paid 5000 2026-10-09" in w.texts("private-rohan")


def test_a_date_outside_collection_says_which_dates_work():
    w = PotWorld({})
    w.say("rohan", "private", "paid 5000 2026-10-25")
    assert "25 Sep to 19 Oct" in w.texts("private-rohan") and not w.clerk.pot.records


def test_the_treasurer_cannot_claim_to_have_paid_themselves():
    w = PotWorld({})
    w.say("asha", "private", "paid 5000 2026-10-09")
    assert not w.clerk.pot.records and "hold the money" in w.texts("private-asha")


def test_no_statement_member_is_asked_about_a_reimbursement():
    w = PotWorld({})
    w.clerk.handle("desk-asha", "@agent-asha", C.PREFIX + json.dumps({"v": 1, "type": "transfer", "ref": "t9", "trip_id": "goa-1", "owner_id": "asha", "from_id": "asha", "to_id": "meera", "date": "2026-10-18", "amount_paise": 100000, "currency": "INR"}))
    tid = next(iter(w.clerk.transfers))
    w.say("asha", "desk", f"release {tid}")
    # Clerk's post to Meera's desk goes through the world when it comes from release, so check what she was asked.
    assert f"received {tid}" in w.texts("private-meera")
    assert w.clerk.transfers[tid].answer is None, "no automatic no"
    w.say("meera", "private", f"received {tid}")
    w.say("meera", "desk", f"release {tid}")
    assert w.clerk.transfers[tid].status == "confirmed" and w.clerk.balances()["meera"] == -100000


def test_a_member_with_a_statement_still_gets_the_automatic_check(world):
    w = world
    w.clerk.handle("desk-asha", "@agent-asha", C.PREFIX + json.dumps({"v": 1, "type": "transfer", "ref": "t9", "trip_id": "goa-1", "owner_id": "asha", "from_id": "asha", "to_id": "rohan", "date": "2026-10-18", "amount_paise": 100000, "currency": "INR"}))
    tid = next(iter(w.clerk.transfers))
    w.say("asha", "desk", f"release {tid}")
    assert w.clerk.transfers[tid].answer is False and "nothing like that reached you" in w.texts("private-rohan")


def test_rejected_claim_finds_payers_reversal(tmp_path):
    rohan = ROHAN_CSV + "2026-10-09,14:09,UPI/REV/ASHA M/441001/GPAY,U441001,,5000.00,,XXXXXX3301\n"
    w = PotWorld({"asha": rows_from(ASHA_CSV.replace("2026-10-09,14:02,UPI/CR/ROHAN K/441001/GPAY,U441001,,5000.00,142000.00,XXXXXX2101\n", ""), tmp_path, "a.csv"), "rohan": rows_from(rohan, tmp_path, "r.csv")})
    w.begin("rohan")
    pid = next(iter(w.clerk.pot.records))
    w.say("rohan", "desk", f"release {pid}")
    w.say("asha", "desk", f"reject {pid} not_received")
    note = w.texts("private-rohan")
    assert "failed and was reversed" in note and f"withdraw {pid} payment_failed" in note
    w.say("rohan", "desk", f"withdraw {pid} payment_failed")
    assert w.clerk.pot.status(pid) == "withdrawn" and w.clerk.balances()["rohan"] == 0


def test_rejected_typed_claim_explains_there_is_no_line_to_check():
    w = PotWorld({})
    w.say("meera", "private", "paid 3000 2026-10-11")
    pid = next(iter(w.clerk.pot.records))
    w.say("meera", "desk", f"release {pid}")
    w.say("asha", "desk", f"reject {pid} not_received")
    assert "your UPI app" in w.texts("private-meera")


def test_the_ask_reason_reaches_the_member_only(world):
    pid = rohan_claims(world)
    world.say("asha", "desk", f"ask {pid} date_differs")
    assert "date_differs" in world.texts("desk-rohan") and "date_differs" not in world.texts("group")


def test_only_the_owner_instructs_the_agent(world):
    world.agents["rohan"].handle("private", "@human-meera", "paid 5000 2026-10-09")
    assert not world.clerk.pot.records
