"""Six people on a fake Switch that enforces rooms. The answers were worked by hand: data/synthetic/six/expected.md."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from kitty import claims as C
from kitty import ledger as L
from kitty import statement_import as SI
from kitty import statements as S
from kitty.agent import PersonalAgent, RulesBrain
from kitty.clerk import Clerk, Person

DATA = Path(__file__).resolve().parent.parent / "data/synthetic/six"
DOC = json.loads((DATA / "trip.json").read_text())
IDS = tuple(p["name"].lower() for p in DOC["people"])
TRIP = C.Trip("coorg-six", date(2026, 10, 16), date(2026, 10, 19), IDS)
NAMES = {p["name"].lower(): p["name"] for p in DOC["people"]}
DIRECTORY = {p["statement_name"]: p["name"].lower() for p in DOC["people"]}
EXPECTED = {"asha": 976666, "bilal": 626666, "chitra": -68333, "dev": -483333, "esha": -208333, "farhan": -843333}
ANSWERS = {
    2400000: "shared", 1250000: "shared", 900000: "Yes, but not Dev. Everyone else went rafting.", 110000: "Yes, but not Asha or Bilal or Dev or Farhan. Just Esha and me.",
    360000: "shared", 630000: "shared", 240000: "shared", 180000: "shared",
}


class World:
    def __init__(self) -> None:
        people = {p: Person(p, NAMES[p], f"@h-{p}", f"@a-{p}", f"desk-{p}") for p in IDS}
        self.clerk = Clerk(TRIP, people, "group", now=lambda: "2026-11-07T00:00:00+00:00")
        self.agents = {p: PersonalAgent(p, TRIP, NAMES, DIRECTORY, S.parse(DATA / f"{p}.csv"), RulesBrain(), f"@h-{p}", "@clerk", "Coorg weekend") for p in IDS}
        self.log: dict[str, list[str]] = {}

    def rooms(self, who: str) -> set[str]:
        return {"group", *(f"desk-{p}" for p in IDS)} if who == "clerk" else {f"private-{who}", f"desk-{who}"}

    def send(self, room: str, sender: str, text: str) -> None:
        self.log.setdefault(room, []).append(text)
        out = []
        if room in self.rooms("clerk") and sender != "@clerk":
            out += [("@clerk", p.room_id, p.text) for p in self.clerk.handle(room, sender, text)]
        for pid, agent in self.agents.items():
            if room in self.rooms(pid) and sender != f"@a-{pid}":
                out += [(f"@a-{pid}", f"{p.room}-{pid}", p.text) for p in agent.handle(room.split("-")[0], sender, text)]
        for who, target, body in out:
            self.send(target, who, body)

    def human(self, pid: str, room: str, text: str) -> None:
        self.send("group" if room == "group" else f"{room}-{pid}", f"@h-{pid}", text)

    def begin(self, pid: str) -> None:
        for p in self.agents[pid].start():
            self.send(f"{p.room}-{pid}", f"@a-{pid}", p.text)

    def answer_all(self, pid: str) -> None:
        agent = self.agents[pid]
        while agent.asked:
            debit = agent.items[agent.asked].row.debit
            self.human(pid, "private", ANSWERS.get(debit, "personal"))


def to_conflict(w: World) -> None:
    for p in IDS:
        w.begin(p)
        w.answer_all(p)
    for p in IDS:
        for cid, rec in list(w.clerk.expenses.items()):
            if rec.claim.owner_id == p and rec.status == "held":
                w.human(p, "desk", f"release {cid}")


@pytest.fixture
def world() -> World:
    w = World()
    to_conflict(w)
    return w


def owner_of(w: World, status: str, owner: str) -> str:
    return next(cid for cid, r in w.clerk.expenses.items() if r.status == status and r.claim.owner_id == owner)


def test_statements_pass_the_upload_checks():
    for p in IDS:
        SI.validate((DATA / f"{p}.csv").read_bytes())


def test_the_same_dinner_from_two_people_is_held_until_one_drops_it(world):
    assert any("Possible conflict" in t for t in world.log["group"])
    held = [r for r in world.clerk.expenses.values() if r.status == "conflict_hold"]
    assert {r.claim.owner_id for r in held} == {"esha", "farhan"} and all(r.claim.amount_paise == 630000 for r in held)
    assert sum(1 for r in world.clerk.expenses.values() if r.status == "released") == 7  # Nine claims, with two on hold.


def finish_dispute(w: World) -> None:
    w.human("farhan", "desk", f"drop {owner_of(w, 'conflict_hold', 'farhan')}")
    w.human("farhan", "private", "personal")  # His agent asks about the line again. It's covered by Esha's claim.
    tid_bilal = next(t for t, r in w.clerk.transfers.items() if r.claim.owner_id == "bilal")
    w.human("bilal", "desk", f"release {tid_bilal}")
    w.human("asha", "desk", f"release {tid_bilal}")
    tid_dev = next(t for t, r in w.clerk.transfers.items() if r.claim.owner_id == "dev")
    w.human("dev", "desk", f"release {tid_dev}")
    w.human("chitra", "desk", f"release {tid_dev}")
    assert w.clerk.transfers[tid_dev].status == "challenged"
    assert any("failed and was reversed" in t for t in w.log["private-dev"])
    w.human("dev", "desk", f"withdraw {tid_dev} payment_failed")


def test_balances_match_the_hand_worked_numbers(world):
    finish_dispute(world)
    assert world.clerk.balances() == EXPECTED
    assert sum(r.claim.amount_paise for r in world.clerk.expenses.values() if r.status == "released") == 6070000


def test_splits_with_a_leftover_are_exact(world):
    car = next(r for r in world.clerk.expenses.values() if r.claim.amount_paise == 1250000)
    assert dict(car.claim.shares) == {"asha": 208334, "bilal": 208334, "chitra": 208333, "dev": 208333, "esha": 208333, "farhan": 208333}


def test_the_person_left_out_owes_nothing_for_it(world):
    rafting = next(r for r in world.clerk.expenses.values() if r.claim.amount_paise == 900000)
    assert "dev" not in rafting.claim.participant_ids and len(rafting.claim.participant_ids) == 5
    delivery = next(r for r in world.clerk.expenses.values() if r.claim.amount_paise == 110000)
    assert set(delivery.claim.participant_ids) == {"chitra", "esha"}


def test_counting_the_failed_payment_would_change_who_owes_what(world):
    finish_dispute(world)
    wrong = L.balances(IDS, world.clerk.lines(), [*world.clerk.confirmed(), L.Payment("dev", "chitra", 200000)])
    assert wrong["chitra"] == -268333 and wrong["dev"] == -283333


def test_settling_up_reaches_zero_only_when_both_sides_confirm(world, tmp_path):
    finish_dispute(world)
    plan = L.settle(world.clerk.balances())
    assert len(plan) <= 5
    statement_names = {v: k for k, v in DIRECTORY.items()}
    for i, pay in enumerate(plan):
        amount = f"{pay.amount_paise // 100}.{pay.amount_paise % 100:02d}"
        for who, line in ((pay.from_id, f"2026-10-19,20:0{i},UPI/DR/{statement_names[pay.to_id]}/88{i}/GPAY,U88{i},{amount},,,XXXXXX0000"), (pay.to_id, f"2026-10-19,20:0{i},UPI/CR/{statement_names[pay.from_id]}/88{i}/GPAY,U88{i},,{amount},,XXXXXX0000")):
            path = tmp_path / f"{who}.csv"
            text = path.read_text() if path.exists() else (DATA / f"{who}.csv").read_text()
            path.write_text(text + line + "\n")
            world.agents[who].rows = S.parse(path)
        world.human(pay.from_id, "private", "rescan")
        tid = next(t for t, r in world.clerk.transfers.items() if r.status == "held" and r.claim.owner_id == pay.from_id)
        world.human(pay.from_id, "desk", f"release {tid}")
        assert world.clerk.balances()[pay.from_id] != 0 or i >= 0
        world.human(pay.to_id, "desk", f"release {tid}")
        assert world.clerk.transfers[tid].status == "confirmed"
    assert not any(world.clerk.balances().values())
    assert "**Everyone's square.**" in " ".join(world.log["group"])


def test_no_statement_text_reaches_the_group_or_any_desk(world):
    finish_dispute(world)
    words = set()
    for p in IDS:
        with (DATA / f"{p}.csv").open() as f:
            for line in list(f)[1:]:
                fields = line.split(",")
                words.update({fields[3], fields[7].strip()})
                words.update(w for w in fields[2].replace("/", " ").replace("*", " ").split() if len(w) > 3 and w.upper() not in {n.upper() for n in NAMES.values()} and not w.isdigit() and w not in ("GPAY", "PHONEPE", "NEFT", "SYNTHETIC", "ORDER", "TRANSFER", "INDIA", "GROUP"))
    joined = "\n".join(t for room, texts in world.log.items() if not room.startswith("private") for t in texts)
    assert joined and not sorted(w for w in words if w in joined)


def test_other_currencies_run_the_same_way():
    trip = C.Trip("t", date(2026, 10, 16), date(2026, 10, 19), ("asha", "bilal"), "USD")
    people = {p: Person(p, p.title(), f"@h-{p}", f"@a-{p}", f"desk-{p}") for p in trip.participants}
    clerk = Clerk(trip, people, "group", now=lambda: "x")
    body = C.PREFIX + json.dumps({"v": 1, "type": "expense", "ref": "a1", "trip_id": "t", "owner_id": "asha", "payer_id": "asha", "date": "2026-10-16", "amount_paise": 123456, "currency": "USD", "category": "food", "participant_ids": ["asha", "bilal"], "shares": {"asha": 61728, "bilal": 61728}})
    post = clerk.handle("desk-asha", "@a-asha", body)[0]
    assert "$1,234.56" in post.text
    wrong = body.replace('"USD"', '"INR"')
    assert "bad_currency" in clerk.handle("desk-asha", "@a-asha", wrong.replace('"a1"', '"a2"'))[0].text
