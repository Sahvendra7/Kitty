"""What the treasurer's statement can and can't reach. Each scan has a positive control."""
from __future__ import annotations

import re

import pytest

from tests.pot_helpers import ASHA_CSV, ROHAN_CSV, PotWorld, rows_from

CANARIES = ["CANARY", "SALARY", "NEHACANARY", "RENT", "LANDLORD", "NETFLIX", "132000", "142000", "111351", "U441001", "N200101", "XXXXXX"]


@pytest.fixture
def world(tmp_path) -> PotWorld:
    return PotWorld({"asha": rows_from(ASHA_CSV, tmp_path, "asha.csv"), "rohan": rows_from(ROHAN_CSV, tmp_path, "rohan.csv")})


def collect(world) -> str:
    world.say("asha", "desk", "call 5000")
    world.say("rohan", "private", "paid 5000 2026-10-09 ref U441001 show")
    pid = next(iter(world.clerk.pot.records))
    world.say("rohan", "desk", f"release {pid}")
    world.say("asha", "desk", f"confirm {pid}")
    world.say("meera", "private", "paid 3000 2026-10-11")
    other = list(world.clerk.pot.records)[-1]
    world.say("meera", "desk", f"release {other}")
    world.say("asha", "desk", f"reject {other} not_received")
    world.say("asha", "desk", f"correct {pid} mistaken")
    return pid


def test_collection_makes_no_model_call(world):
    collect(world)
    assert world.brain_calls == []
    assert world.texts("group"), "control: the collection really ran"


def test_review_never_shows_credits_or_pre_trip_lines_to_model(world):
    world.begin("asha")
    rows = [r for name, batch in world.brain_calls if name == "classify" for r in batch]
    narrations = " ".join(r["narration"] for r in rows)
    assert "NETFLIX" in narrations, "control: an in-trip debit is what a review sends"
    for word in ("SALARY", "RENT", "UPI", "ROHAN", "NEHACANARY"):
        assert word not in narrations
    assert all(set(r) == {"row_id", "date", "time", "narration", "amount_rupees"} for r in rows)


def test_group_and_desks_hold_no_statement_text(world):
    collect(world)
    public = world.not_private()
    assert public
    for word in CANARIES:
        assert word not in public, word


def test_the_scan_would_catch_a_leak(world):
    collect(world)
    world.say("asha", "group", "RENT LANDLORD CANARY-RENT-ASHA")
    assert "CANARY" in world.not_private(), "the scan above looks in the right place"


def test_private_room_holds_no_unrelated_text(world):
    collect(world)
    private = world.texts("private-asha")
    assert private
    for word in ("SALARY", "NEHACANARY", "RENT", "NETFLIX", "441001", "UPI/"):
        assert word not in private, word


def test_full_reference_stays_a_token_on_desks(world):
    collect(world)
    desks = world.texts("desk-rohan", "desk-asha")
    assert "U441001" not in desks and "441001" not in desks
    assert re.search(r'"ref_token": "[0-9a-f]{32}"', desks), "control: the token is there"


def test_last_four_is_all_the_group_sees(world):
    collect(world)
    group = world.texts("group")
    assert "ref …1001" in group and not re.search(r"\d{5,}", re.sub(r"\[d\d+ [0-9a-f]{12}\]", "", group))


def test_clerk_never_receives_a_narration(world):
    collect(world)
    clerk_rooms = world.texts("desk-asha", "desk-rohan", "desk-meera", "group")
    assert '"narration"' not in clerk_rooms and "merchant" not in clerk_rooms
