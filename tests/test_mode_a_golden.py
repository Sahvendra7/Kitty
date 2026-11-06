"""Frozen transcripts of the shared-expense stories, captured at 4ba9d82 before any Pot trip code.

Every message in every room, every call to the brain, and the final balances. With KITTY_WRITE_GOLDEN=1 this
writes the files instead of comparing, which is only right on the commit named above.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from kitty import ledger as L
from kitty import statements as S
from kitty.agent import RulesBrain
from tests import test_scenario as three
from tests import test_six_person as six

GOLD = Path(__file__).parent / "golden"


class RecordingBrain(RulesBrain):
    """The rules brain, plus a note of exactly what a model would have been given."""

    def __init__(self) -> None:
        self.calls: list[list] = []

    def classify(self, rows, owner_name, trip_name):
        self.calls.append(["classify", rows, owner_name, trip_name])
        return super().classify(rows, owner_name, trip_name)

    def read_answer(self, question, answer, people):
        self.calls.append(["read_answer", question, answer, people])
        return super().read_answer(question, answer, people)


def run_three() -> dict:
    brain = RecordingBrain()
    world = three.build()
    for agent in world.agents.values():
        agent.brain = brain
    three.play_until_settlement(world)
    three.run_dispute(world)
    three.run_settlement(world)
    return {"rooms": world.log, "brain_calls": brain.calls, "balances": world.clerk.balances()}


def run_six(tmp: Path) -> dict:
    brain = RecordingBrain()
    world = six.World()
    for agent in world.agents.values():
        agent.brain = brain
    six.to_conflict(world)
    six.finish_dispute(world)
    names = {v: k for k, v in six.DIRECTORY.items()}
    for i, pay in enumerate(L.settle(world.clerk.balances())):
        amount = f"{pay.amount_paise // 100}.{pay.amount_paise % 100:02d}"
        lines = (
            (pay.from_id, f"2026-10-19,20:0{i},UPI/DR/{names[pay.to_id]}/88{i}/GPAY,U88{i},{amount},,,XXXXXX0000"),
            (pay.to_id, f"2026-10-19,20:0{i},UPI/CR/{names[pay.from_id]}/88{i}/GPAY,U88{i},,{amount},,XXXXXX0000"),
        )
        for who, line in lines:
            path = tmp / f"{who}.csv"
            text = path.read_text() if path.exists() else (six.DATA / f"{who}.csv").read_text()
            path.write_text(text + line + "\n")
            world.agents[who].rows = S.parse(path)
        world.human(pay.from_id, "private", "rescan")
        tid = next(t for t, r in world.clerk.transfers.items() if r.status == "held" and r.claim.owner_id == pay.from_id)
        world.human(pay.from_id, "desk", f"release {tid}")
        world.human(pay.to_id, "desk", f"release {tid}")
    return {"rooms": world.log, "brain_calls": brain.calls, "balances": world.clerk.balances()}


def compare(name: str, got: dict) -> None:
    got = json.loads(json.dumps(got, ensure_ascii=False))
    path = GOLD / name
    if os.environ.get("KITTY_WRITE_GOLDEN") == "1":
        GOLD.mkdir(exist_ok=True)
        path.write_text(json.dumps(got, ensure_ascii=False, indent=1, sort_keys=True))
    assert got == json.loads(path.read_text())


def test_three_person_transcript_is_unchanged():
    compare("three_person.json", run_three())


def test_six_person_transcript_is_unchanged(tmp_path):
    compare("six_person.json", run_six(tmp_path))
