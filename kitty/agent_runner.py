"""Runs one person's agent as its own process.

    python -m kitty.agent_runner meera

Reads state/meera.json and people/meera/statement.csv, and nothing belonging to anyone else. The
model subprocess has no tools at all (see llm.py), so it cannot post, read files or reach Switch.
Only this code talks to Switch, and it talks only to this person's two rooms.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import sys
from datetime import date
from pathlib import Path

from kitty import claims as C
from kitty import statements as S
from kitty.agent import PersonalAgent, RulesBrain
from kitty.llm_brain import LlmBrain
from kitty.switch_client import Agent

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("kitty.agent")
GREETING = "Hi {name}. I'm your own agent, and I only work for you. Say `start` and I'll go through your statement for {trip}. Nothing leaves this room unless you release it on your desk."


def main(me: str) -> None:
    logging.basicConfig(level=logging.INFO, format=f"%(asctime)s {me} %(levelname)s %(message)s")
    config = json.loads((ROOT / "state" / f"{me}.json").read_text())
    trip_doc = json.loads((ROOT / "data/synthetic/trip.json").read_text())
    trip = C.Trip(trip_doc["trip_id"], date.fromisoformat(trip_doc["start"]), date.fromisoformat(trip_doc["end"]), tuple(trip_doc["people"]))
    names = {p: d["name"] for p, d in trip_doc["people"].items()}
    directory = {d["statement_name"]: p for p, d in trip_doc["people"].items()}
    brain = RulesBrain() if os.environ.get("KITTY_BRAIN") == "rules" else LlmBrain()
    statement = ROOT / "people" / me / "statement.csv"
    core = PersonalAgent(me, trip, names, directory, S.parse(statement), brain, config["owner_sender"], config["clerk_sender"], trip_doc["name"])
    agent = Agent(config["agent_id"], config["api_key"], config["name"])
    rooms = {config["private_room"]: "private", config["desk_room"]: "desk"}
    targets = {"private": config["private_room"], "desk": config["desk_room"]}

    conn = agent.listen()
    conn.ready.wait(15)
    agent.say(targets["private"], GREETING.format(name=names[me], trip=trip_doc["name"]))
    log.info("listening. Brain: %s", type(brain).__name__)
    while True:
        try:
            e = conn.events.get(timeout=1)
        except queue.Empty:
            continue
        room = rooms.get(e.room_id)
        if room is None:
            log.error("event from a room that is not mine; ignoring")
            continue
        core.rows = S.parse(statement)
        try:
            posts = core.handle(room, e.sender, e.body)
        except Exception:
            log.exception("could not handle a message")
            agent.say(targets["private"], "Something went wrong on my side, and nothing was shared. Try again?")
            continue
        for post in posts:
            agent.say(targets[post.room], post.text)


if __name__ == "__main__":
    main(sys.argv[1])
