"""Plays the humans' side of the Goa story through real Mattermost accounts, then stops.

Each line is posted with that person's own Mattermost session, so Switch sets the real sender id.
This is a stand-in for people typing, and it is labelled as such wherever its output is shown.

    uv run python scripts/play_story.py [--until dispute|settle|end]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from kitty import ledger as L  # noqa: E402
from kitty.switch_client import Mattermost  # noqa: E402

TRIP = json.loads((ROOT / "data/synthetic/trip.json").read_text())
PEOPLE = list(TRIP["people"])
BOARD = "http://127.0.0.1:7100/api/board"
ANSWERS = {"priya": "Yes, but Meera had gone to bed. Just Arjun and me.", "arjun": "That one was just me, personal."}


class Human:
    def __init__(self, slug: str) -> None:
        self.slug = slug
        cfg = json.loads((ROOT / "state" / f"{slug}.json").read_text())["mattermost"]
        self.mm = Mattermost(cfg["username"], cfg["password"])
        self.channels = cfg["channels"]

    def say(self, room: str, text: str) -> None:
        print(f"  {self.slug:6} in {room:7}: {text}", flush=True)
        self.mm.post(self.channels[room], text)

    def posts(self, room: str) -> list[str]:
        r = self.mm.channel_posts(self.channels[room])
        r.raise_for_status()
        d = r.json()
        return [d["posts"][i]["message"] for i in reversed(d["order"])]

    def wait(self, room: str, needle: str, seconds: float = 180) -> str:
        end = time.time() + seconds
        while time.time() < end:
            for text in reversed(self.posts(room)):
                if needle in text:
                    return text
            time.sleep(0.5)
        raise TimeoutError(f"{self.slug}: never saw {needle!r} in {room}")


def board() -> dict:
    return httpx.get(BOARD, timeout=10).json()


def open_cards(h: Human) -> list[str]:
    import re

    released = set()
    ids = []
    for text in h.posts("desk"):
        if m := re.match(r"^\*\*([ct][0-9]+)\*\* ready", text):
            ids.append(m.group(1))
        if m := re.match(r"^([ct][0-9]+) released\.", text):
            released.add(m.group(1))
    return [i for i in ids if i not in released]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--until", choices=["dispute", "settle", "end"], default="end")
    until = ap.parse_args().until
    humans = {slug: Human(slug) for slug in PEOPLE}
    print("Scripted humans, posting with their own Mattermost sessions.")

    for slug, h in humans.items():
        h.say("private", "start")
        h.wait("private", "I read ")
    for slug, h in humans.items():
        if slug in ANSWERS:
            h.wait("private", "Was this shared with the group?")
            h.say("private", ANSWERS[slug])
        h.wait("private", "That's everything for now")
    time.sleep(2)

    for slug, h in humans.items():
        for cid in open_cards(h):
            if cid.startswith("c"):
                h.say("desk", f"release {cid}")
                h.wait("desk", f"{cid} released.")
    print("\nThe payments people remember:")
    t_arjun = next(c for c in open_cards(humans["arjun"]) if c.startswith("t"))
    humans["arjun"].say("desk", f"release {t_arjun}")
    humans["priya"].wait("desk", f"KITTY_CHECK {t_arjun}")
    humans["priya"].wait("private", "says they paid you")
    humans["priya"].wait("desk", f"release {t_arjun}")
    humans["priya"].say("desk", f"release {t_arjun}")
    humans["priya"].wait("group", f"{t_arjun} confirmed by both sides")

    print("\nThe dispute:")
    t_meera = next(c for c in open_cards(humans["meera"]) if c.startswith("t"))
    humans["meera"].say("desk", f"release {t_meera}")
    humans["arjun"].wait("private", "says they paid you ₹1,500")
    humans["arjun"].wait("desk", f"release {t_meera}")
    humans["arjun"].say("desk", f"release {t_meera}")
    humans["meera"].wait("group", f"{t_meera} NOT CONFIRMED")
    print("  group room: NOT CONFIRMED")
    humans["meera"].wait("private", "failed and was reversed")
    print("  Meera's agent found the reversal, privately")
    if until == "dispute":
        return 0
    humans["meera"].say("desk", f"withdraw {t_meera} payment_failed")
    humans["meera"].wait("group", f"{t_meera} withdrawn by")
    humans["priya"].say("group", "balances")
    humans["priya"].wait("group", "| Person | Balance |")
    humans["priya"].say("group", "settle")
    humans["priya"].wait("group", "Suggested payments")
    if until == "settle":
        return 0

    print("\nSynthetic payments land on the statements:")
    state = board()
    ids = {p["name"]: p["id"] for p in state["people"]}
    names = {d["name"]: d["statement_name"] for d in TRIP["people"].values()}
    for i, pay in enumerate(state["plan"]):
        payer, payee = ids[pay["from"]], ids[pay["to"]]
        amount = f"{pay['paise'] / 100:.2f}"
        ref = f"U77{i}001"
        with (ROOT / "people" / payer / "statement.csv").open("a") as f:
            f.write(f"2026-10-12,20:0{i},UPI/DR/{names[pay['to']]}/77{i}001/GPAY,{ref},{amount},,,XXXXXX0000\n")
        with (ROOT / "people" / payee / "statement.csv").open("a") as f:
            f.write(f"2026-10-12,20:0{i},UPI/CR/{names[pay['from']]}/77{i}001/GPAY,{ref},,{amount},,XXXXXX0000\n")
        before = set(open_cards(humans[payer]))
        humans[payer].say("private", "rescan")
        end = time.time() + 60
        while not (fresh := set(open_cards(humans[payer])) - before) and time.time() < end:
            time.sleep(0.5)
        tid = next(iter(fresh))
        humans[payer].say("desk", f"release {tid}")
        humans[payee].wait("desk", f"KITTY_CHECK {tid}")
        humans[payee].wait("desk", f"release {tid}")
        humans[payee].say("desk", f"release {tid}")
        humans[payee].wait("group", f"{tid} confirmed by both sides")
    humans["priya"].wait("group", "Everyone's square")
    print("\nEveryone's square.")
    final = board()
    assert all(b["paise"] == 0 for b in final["balances"])
    assert L.settle({b["name"]: b["paise"] for b in final["balances"]}) == []
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
