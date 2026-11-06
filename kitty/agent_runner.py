"""Runs one person's agent as its own process.

    python -m kitty.agent_runner meera

Reads its own state file and statement, and nothing belonging to anyone else. The
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
from kitty import paths as P
from kitty import statements as S
from kitty.agent import PersonalAgent, RulesBrain
from kitty.llm import LlmError
from kitty.llm_brain import LlmBrain
from kitty.switch_client import Agent
from kitty.trips import read_json, write_json

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("kitty.agent")
MODEL_DOWN = "I can't reach the model right now, so I haven't read anything yet, and nothing was shared. Try again in a little while."
NO_STATEMENT = "I don't have your statement yet. Add it in the Statement tab, then I can start. Nothing has been read or shared."
GREETING_IN_APP = "Hi {name}. I'm your own agent, and I only work for you. Add your statement for {trip}, and I'll go through it with you. Nothing leaves this room unless you release it on your desk."
GREETING = "Hi {name}. I'm your own agent, and I only work for you. Say `start` and I'll go through your statement for {trip}. Nothing leaves this room unless you release it on your desk."


def main(me: str) -> None:
    logging.basicConfig(level=logging.INFO, format=f"%(asctime)s {me} %(levelname)s %(message)s")
    where = P.current()
    config = json.loads((where.state / f"{me}.json").read_text())
    trip_doc = json.loads(where.trip_json.read_text())
    trip = C.Trip(trip_doc["trip_id"], date.fromisoformat(trip_doc["start"]), date.fromisoformat(trip_doc["end"]), tuple(trip_doc["people"]), trip_doc.get("currency", C.CURRENCY))
    names = {p: d["name"] for p, d in trip_doc["people"].items()}
    directory = {d["statement_name"]: p for p, d in trip_doc["people"].items()}
    brain = RulesBrain() if os.environ.get("KITTY_BRAIN") == "rules" else LlmBrain()
    statement = where.statement(me)

    def rows() -> list[S.Row]:
        # A trip made in the app starts with no statement, until its owner adds one.
        return S.parse(statement) if statement.exists() else []

    core = PersonalAgent(me, trip, names, directory, rows(), brain, config["owner_sender"], config["clerk_sender"], trip_doc["name"])
    agent = Agent(config["agent_id"], config["api_key"], config["name"])
    memory_path = where.people / me / "agent_memory.json"
    memory = read_json(memory_path) if where.in_app else None
    if memory:
        core.restore(memory["agent"])
    rooms = {config["private_room"]: "private", config["desk_room"]: "desk"}
    targets = {"private": config["private_room"], "desk": config["desk_room"]}

    conn = agent.listen(memory["cursor"] if memory else None)
    conn.ready.wait(15)
    history = agent.result("read_context", room_id=targets["private"], limit=20)["threads"]
    if not any(t["root"]["body"].startswith(f"Hi {names[me]}. I'm your own agent") for t in history):
        agent.say(targets["private"], (GREETING_IN_APP if where.in_app else GREETING).format(name=names[me], trip=trip_doc["name"]))
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
        core.rows = rows()
        if room == "private" and e.sender == config["owner_sender"] and not statement.exists():
            agent.say(targets["private"], NO_STATEMENT)
            continue
        try:
            posts = core.handle(room, e.sender, e.body)
        except LlmError as exc:
            log.error("the model call failed: %s", exc)
            agent.say(targets["private"], MODEL_DOWN)
            continue
        except Exception:
            log.exception("could not handle a message")
            agent.say(targets["private"], "Something went wrong on my side, and nothing was shared. Try again?")
            continue
        for post in posts:
            agent.say(targets[post.room], post.text)
        if where.in_app:
            write_json(memory_path, {"agent": core.to_state(), "cursor": e.raw.get("sequence")})


if __name__ == "__main__":
    main(sys.argv[1])
