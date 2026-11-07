"""The six-person Pot trip on rooms enforced the way the server does. The answers were worked by hand: data/synthetic/pot/expected.md."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from kitty import claims as C
from kitty import pot as P
from kitty import statement_import as SI
from kitty import statements as S
from kitty.agent import RulesBrain
from kitty.clerk import Clerk, Person
from kitty.pot_agent import PotAgent

DATA = Path(__file__).resolve().parent.parent / "data/synthetic/pot"
DOC = json.loads((DATA / "trip.json").read_text())
SETTLE = json.loads((DATA / "settle.json").read_text())
IDS = tuple(p["name"].lower() for p in DOC["people"])
NAMES = {p["name"].lower(): p["name"] for p in DOC["people"]}
DIRECTORY = {p["statement_name"]: p["name"].lower() for p in DOC["people"]}
TRIP = C.Trip("goa-pot", date(2026, 10, 16), date(2026, 10, 19), IDS)
CFG = P.PotConfig("asha", date(2026, 9, 25))
BEFORE = {"asha": 390000, "rohan": -110000, "meera": -310000, "dev": 250000, "esha": -110000, "farhan": -110000}
WITH_STATEMENT = ("asha", "rohan", "meera", "dev", "farhan")


class World:
    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        people = {p: Person(p, NAMES[p], f"@h-{p}", f"@a-{p}", f"desk-{p}") for p in IDS}
        self.clerk = Clerk(TRIP, people, "group", now=lambda: "2026-10-19T08:32:00+00:00", pot=P.PotBook(CFG))
        self.agents = {
            p: PotAgent(p, TRIP, NAMES, DIRECTORY, S.parse(DATA / f"{p}.csv") if p in WITH_STATEMENT else [], RulesBrain(), f"@h-{p}", "@clerk", "Goa pot", cfg=CFG, pot_key=b"story-key")
            for p in IDS
        }
        self.log: dict[str, list[str]] = {}

    def send(self, room: str, sender: str, text: str) -> None:
        self.log.setdefault(room, []).append(text)
        out = []
        if (room == "group" or room.startswith("desk-")) and sender != "@clerk":
            out += [("@clerk", p.room_id, p.text) for p in self.clerk.handle(room, sender, text)]
        for pid, agent in self.agents.items():
            if room in (f"private-{pid}", f"desk-{pid}") and sender != f"@a-{pid}":
                out += [(f"@a-{pid}", f"{p.room}-{pid}", p.text) for p in agent.handle(room.split("-")[0], sender, text)]
        for who, target, body in out:
            self.send(target, who, body)

    def say(self, who: str, room: str, text: str) -> None:
        self.send("group" if room == "group" else f"{room}-{who}", f"@h-{who}", text)

    def records(self, who: str, status: str) -> list[str]:
        return [r.id for r in self.clerk.pot.records.values() if r.claim.owner_id == who and self.clerk.pot.status(r.id) == status]

    def by_amount(self, who: str, amount: int) -> str:
        return next(r.id for r in reversed(self.clerk.pot.records.values()) if r.claim.owner_id == who and r.claim.amount_paise == amount)

    def collect(self) -> None:
        self.say("asha", "desk", "call 5000")
        for who in ("rohan", "dev", "meera"):
            self.say(who, "private", "start")
        self.say("farhan", "private", "paid 5000 2026-10-05")
        self.say("esha", "private", "paid 5000 2026-10-08 ref U441004 show")
        for who in ("rohan", "dev", "farhan", "esha", "meera"):
            for pid in self.records(who, "held"):
                self.say(who, "desk", f"release {pid}")
        for who in ("rohan", "dev", "farhan", "esha"):
            self.say("asha", "desk", f"confirm {self.by_amount(who, 500000)}")
        failed = self.by_amount("meera", 500000)
        self.say("asha", "desk", f"reject {failed} not_received")
        self.say("meera", "desk", f"withdraw {failed} payment_failed")
        self.say("asha", "desk", f"confirm {self.by_amount('meera', 300000)}")

    def spend(self) -> None:
        self.say("asha", "private", "start")
        for who in ("asha", "dev"):
            for cid, rec in list(self.clerk.expenses.items()):
                if rec.claim.owner_id == who and rec.status == "held":
                    self.say(who, "desk", f"release {cid}")

    def add_lines(self, who: str) -> None:
        path = self.tmp / f"{who}.csv"
        path.write_text((DATA / f"{who}.csv").read_text() + "\n".join(SETTLE[who]) + "\n")
        self.agents[who].rows = S.parse(path)

    def settle(self) -> None:
        for who in ("asha", "rohan", "meera", "dev"):
            self.add_lines(who)
        self.say("rohan", "private", "rescan")
        self.say("meera", "private", "rescan")
        for who, text in SETTLE["typed"].items():
            self.say(who, "private", text)
        for who in ("rohan", "meera", "esha", "farhan"):
            pid = self.records(who, "held")[0]
            self.say(who, "desk", f"release {pid}")
            self.say("asha", "desk", f"confirm {pid}")
        self.say("asha", "private", "rescan")
        tid = next(t for t, r in self.clerk.transfers.items() if r.status == "held")
        self.say("asha", "desk", f"release {tid}")
        self.say("dev", "desk", f"release {tid}")


def test_statements_pass_the_upload_checks():
    for who in WITH_STATEMENT:
        SI.validate((DATA / f"{who}.csv").read_bytes())
    for who, lines in SETTLE.items():
        if who != "typed":
            SI.validate(((DATA / f"{who}.csv").read_text() + "\n".join(lines) + "\n").encode())


def test_whole_story_matches_the_hand_worked_numbers(tmp_path):
    w = World(tmp_path)
    w.collect()
    assert w.clerk.pot.view(w.clerk).collected == 2300000
    w.spend()
    assert w.clerk.balances() == BEFORE
    assert w.clerk.pot.view(w.clerk).position == -1000000
    assert {(p.from_id, p.to_id, p.amount_paise) for p in w.clerk.pot.plan(w.clerk)} == {("rohan", "asha", 110000), ("esha", "asha", 110000), ("farhan", "asha", 110000), ("meera", "asha", 310000), ("asha", "dev", 250000)}
    w.settle()
    assert not any(w.clerk.balances().values()), w.clerk.balances()
    assert w.clerk.pot.view(w.clerk).position == -610000, "exactly Asha's own share"
    assert "**Everyone's square.**" in " ".join(w.log["group"])
    assert P.chain_check(w.log["group"]) is None


def test_the_correction_moves_only_the_two_people_involved(tmp_path):
    w = World(tmp_path)
    w.collect()
    w.spend()
    pid = w.by_amount("meera", 300000)
    w.say("asha", "desk", f"correct {pid} mistaken")
    assert w.clerk.balances() == {**BEFORE, "asha": 690000, "meera": -610000}
    w.say("asha", "desk", f"correct {pid} received")
    assert w.clerk.balances() == BEFORE


def test_the_failed_payment_never_counted(tmp_path):
    w = World(tmp_path)
    w.collect()
    assert any("failed and was reversed" in t for t in w.log["private-meera"])
    assert w.clerk.pot.status(w.by_amount("meera", 500000)) == "withdrawn"


def test_nothing_from_a_statement_reaches_the_group_or_a_desk(tmp_path):
    w = World(tmp_path)
    w.collect()
    w.spend()
    public = "\n".join(t for room, texts in w.log.items() if not room.startswith("private") for t in texts)
    for word in ("CANARY", "SALARY", "NEHACANARY", "AIRBNB", "NETFLIX", "U441001", "U700001", "132017", "XXXXXX"):
        assert word not in public, word
    assert "CANARY" in (DATA / "asha.csv").read_text(), "positive control"


def test_mode_a_trips_have_no_treasurer():
    for path in ("data/synthetic/trip.json", "data/synthetic/six/trip.json"):
        assert "treasurer" not in json.loads((DATA.parent.parent.parent / path).read_text())
