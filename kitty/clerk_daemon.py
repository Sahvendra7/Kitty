"""Connects the clerk's state machine to Switch and serves the public trip board.

Reads its own clerk.json and the trip file, and nothing else. It has no path to any
statement, and Switch refuses it entry to private rooms.

Only messages that can change state are written to the event log. Anything else on a desk is never
stored, so a stray private note cannot end up on disk through the clerk.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import re
import threading
from datetime import date, datetime, timezone
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from kitty import claims as C
from kitty import paths as P
from kitty.clerk import Clerk, Person
from kitty.switch_client import Agent
from kitty.trips import read_json, write_json

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("kitty.clerk")
CLERK_LOCK = threading.Lock()
RELEVANT = re.compile(r"^\s*(release|drop|withdraw|balances|settle|status)\b", re.I)
POT_RELEVANT = re.compile(r"^\s*(?:(?:confirm|reject|ask|recheck|correct)\s+p[0-9]|cancel\s+k[0-9]|call\s+(?:rs\.?\s*|₹\s*)?[0-9]|pot\s*$)", re.I)


def build_clerk(config: dict, trip_doc: dict) -> Clerk:
    trip = C.Trip(trip_doc["trip_id"], date.fromisoformat(trip_doc["start"]), date.fromisoformat(trip_doc["end"]), tuple(trip_doc["people"]), trip_doc.get("currency", C.CURRENCY))
    people = {pid: Person(**p) for pid, p in config["people"].items()}
    clerk = Clerk(trip, people, config["group_room"])
    if trip_doc.get("treasurer"):
        from kitty.pot import PotBook, PotConfig

        clerk.pot = PotBook(PotConfig(trip_doc["treasurer"], date.fromisoformat(trip_doc["collect_from"])))
    return clerk


def should_log(body: str, pot: bool = False) -> bool:
    return body.startswith(C.PREFIX) or bool(RELEVANT.match(body)) or (pot and bool(POT_RELEVANT.match(body)))


def event_line(room: str, sender: str, body: str, raw: dict, pot: bool) -> dict:
    """On a Pot trip the server's time goes in the log, because decisions are shown with it and must survive a restart."""
    line = {"room": room, "sender": sender, "body": body}
    if pot:
        stamp = (raw.get("payload") or {}).get("timestamp")
        moment = datetime.fromtimestamp(stamp / 1000, timezone.utc) if stamp else datetime.now(timezone.utc)
        line["at"] = moment.isoformat(timespec="seconds")
    return line


def apply_event(clerk: Clerk, event: dict) -> list:
    if "at" in event:
        at = event["at"]
        clerk.now = lambda: at
    return clerk.handle(event["room"], event["sender"], event["body"])


def board(clerk: Clerk, trip_doc: dict) -> dict:
    """The public view: exactly what the group room already shows."""
    with CLERK_LOCK:
        name = {p: clerk.people[p].name for p in clerk.people}
        extra = {"pot": clerk.pot.board_view(clerk)} if clerk.pot is not None else {}
        return {
            **extra,
            "trip": {"name": trip_doc["name"], "start": trip_doc["start"], "end": trip_doc["end"], "currency": clerk.trip.currency},
            "people": [{"id": p, "name": name[p]} for p in clerk.trip.participants],
            "expenses": [
                {"id": r.id, "date": r.claim.date.isoformat(), "amount_paise": r.claim.amount_paise, "category": r.claim.category, "payer": name[r.claim.payer_id], "shares": [{"name": name[p], "paise": v} for p, v in r.claim.shares], "status": r.status}
                for r in clerk.expenses.values() if r.status in ("released", "conflict_hold")
            ],
            "transfers": [
                {"id": r.id, "date": r.claim.date.isoformat(), "amount_paise": r.claim.amount_paise, "from": name[r.claim.from_id], "to": name[r.claim.to_id], "status": r.status, "reason": r.withdraw_reason}
                for r in clerk.transfers.values() if r.status != "held" and r.status != "dropped"
            ],
            "balances": [{"name": name[p], "paise": v} for p, v in clerk.balances().items()],
            "plan": clerk_plan(clerk, name),
            "held_count": sum(r.status == "held" for r in clerk.expenses.values()) + sum(r.status == "held" for r in clerk.transfers.values()),
        }


def clerk_plan(clerk: Clerk, name: dict) -> list[dict]:
    from kitty import ledger as L

    plan = clerk.pot.plan(clerk) if clerk.pot is not None else L.settle(clerk.balances())
    return [{"from": name[p.from_id], "to": name[p.to_id], "paise": p.amount_paise} for p in plan]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    where = P.current()
    state = where.state
    port = int(os.environ.get("KITTY_BOARD_PORT", "7100"))
    config = json.loads((state / "clerk.json").read_text())
    trip_doc = json.loads(where.trip_json.read_text())
    clerk = build_clerk(config, trip_doc)
    agent = Agent(config["agent_id"], config["api_key"], config["name"])

    events_path = state / f"clerk-events-{config['run']}.jsonl"
    if events_path.exists():
        lines = [json.loads(line) for line in events_path.read_text().splitlines()]
        for e in lines:
            apply_event(clerk, e)  # Replay. Outputs were already posted.
        log.info("replayed %d events", len(lines))

    app = FastAPI(title="Kitty trip board")
    app.mount("/ui", StaticFiles(directory=ROOT / "app" / "ui"), name="ui")

    @app.get("/api/board")
    def api_board() -> JSONResponse:
        return JSONResponse(board(clerk, trip_doc))

    @app.get("/")
    def page() -> FileResponse:
        return FileResponse(ROOT / "app" / "board.html")

    @app.get("/film")
    def film() -> FileResponse:
        return FileResponse(ROOT / "app" / "film.html")

    @app.get("/assets/{name}")
    def asset(name: str) -> FileResponse:
        if name not in ("architecture.png", "mattermost-group.png"):
            raise HTTPException(status_code=404)
        return FileResponse(ROOT / "docs" / "assets" / name)

    @app.get("/stage")
    def stage() -> FileResponse:
        return FileResponse(ROOT / "app" / "stage.html")

    threading.Thread(target=lambda: uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning"), daemon=True).start()

    cursor_path = state / "clerk-cursor.json"
    saved = read_json(cursor_path) if where.in_app else None
    conn = agent.listen(saved["cursor"] if saved else None)
    conn.ready.wait(15)
    log.info("clerk %s listening. Board at http://127.0.0.1:%d", agent.name, port)
    while True:
        try:
            e = conn.events.get(timeout=1)
        except queue.Empty:
            continue
        if e.sender not in {p.human_sender for p in clerk.people.values()} | {p.agent_sender for p in clerk.people.values()}:
            continue
        is_pot = clerk.pot is not None
        if not should_log(e.body, is_pot):
            continue
        line = event_line(e.room_id, e.sender, e.body, e.raw, is_pot)
        with events_path.open("a") as handle:
            handle.write(json.dumps(line, ensure_ascii=False) + "\n")
        with CLERK_LOCK:
            posts = apply_event(clerk, line)
        for post in posts:
            try:
                agent.say(post.room_id, post.text)
            except RuntimeError:
                log.exception("clerk could not post")
        if where.in_app:
            write_json(cursor_path, {"cursor": e.raw.get("sequence")})


if __name__ == "__main__":
    main()
