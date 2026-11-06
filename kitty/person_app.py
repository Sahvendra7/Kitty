"""One person's own app. One process per person, on their own port.

    python -m kitty.person_app meera 7103

Holds only that person's Mattermost session, read from state/meera.json. It reads and posts in
their three channels as them, so anything it sends carries their real sender id. It never touches
Switch directly, and it has no credential that could read anyone else's room.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from kitty.switch_client import Mattermost

ROOT = Path(__file__).resolve().parent.parent

READY_EXPENSE = re.compile(r"^\*\*(c\d+)\*\* ready \[ref [a-z0-9]+\]: (₹[\d,.]+) · (\w+) · (\d+ \w+) · paid by (\w+) · (.+?)\nReply", re.S)
READY_TRANSFER = re.compile(r"^\*\*(t\d+)\*\* ready \[ref [a-z0-9]+\]: you paid (\w+) (₹[\d,.]+) on (\d+ \w+)\.")
ANSWER = re.compile(r"^Your agent found that (t\d+) \((₹[\d,.]+) from (\w+)\) (reached you|did not reach you)\.")
GROUP_EXPENSE = re.compile(r"^\*\*(c\d+)\*\* (₹[\d,.]+) · (\w+) · (\d+ \w+) · paid by (\w+) · (.+)$")
SHARE = re.compile(r"(\w+) (₹[\d,]*\d\.\d{2})")
HIDDEN = re.compile(r"^(Hi, it's \w+\. \[|\w+'s agent checking in\. \[|Kitty clerk here\.|Hey! I'm on standby|Hi there! Just @)")
MACHINE = re.compile(r"^(KITTY/1 |KITTY_CHECK |KITTY_RECHECK )")
DESK_COMMAND = re.compile(r"^\s*(release|drop|withdraw)\s", re.I)


def build_desk(posts: list[dict], group_text: list[str]) -> dict:
    slips: dict[str, dict] = {}
    raw: list[str] = []
    notices: list[str] = []
    for post in posts:
        text = post["text"]
        if HIDDEN.match(text):
            continue
        if MACHINE.match(text):
            raw.append(text)
            continue
        if post["mine"] and DESK_COMMAND.match(text):
            continue
        if m := READY_EXPENSE.match(text):
            slips[m[1]] = {"id": m[1], "kind": "expense", "amount": m[2], "category": m[3], "date": m[4], "shares": [{"name": n, "amount": v} for n, v in SHARE.findall(m[6])], "state": "waiting"}
        elif m := READY_TRANSFER.match(text):
            slips[m[1]] = {"id": m[1], "kind": "transfer", "to": m[2], "amount": m[3], "date": m[4], "state": "waiting"}
        elif m := ANSWER.match(text):
            slips[f"{m[1]}?"] = {"id": m[1], "kind": "answer", "amount": m[2], "from": m[3], "received": m[4] == "reached you", "state": "waiting"}
        elif m := re.match(r"^([ct]\d+) released\.", text):
            if m[1] in slips:
                slips[m[1]]["state"] = "released"
        elif m := re.match(r"^Answer for (t\d+) sent\.", text):
            if f"{m[1]}?" in slips:
                slips[f"{m[1]}?"]["state"] = "sent"
        elif m := re.match(r"^Your answer for (t\d+) stays private", text):
            if f"{m[1]}?" in slips:
                slips[f"{m[1]}?"]["state"] = "kept"
        elif m := re.match(r"^([ct]\d+) dropped", text):
            if m[1] in slips:
                slips[m[1]]["state"] = "dropped"
        elif m := re.match(r"^(t\d+) withdrawn\.", text):
            if m[1] in slips:
                slips[m[1]]["state"] = "withdrawn"
        elif text.startswith(("Refused", "Rejected")):
            notices.append(text)
    joined = "\n".join(group_text)
    for slip in slips.values():
        if slip["kind"] == "transfer" and slip["state"] == "released":
            if f"**{slip['id']} confirmed by both sides" in joined:
                slip["state"] = "confirmed"
            elif f"**{slip['id']} NOT CONFIRMED" in joined:
                slip["state"] = "challenged"
    return {"slips": list(slips.values()), "raw": raw[-40:], "notices": notices[-3:]}


def build_group(posts: list[dict]) -> list[dict]:
    feed = []
    for post in posts:
        text = post["text"]
        if HIDDEN.match(text):
            continue
        if m := GROUP_EXPENSE.match(text):
            feed.append({"type": "expense", "id": m[1], "amount": m[2], "category": m[3], "date": m[4], "payer": m[5], "shares": [{"name": n, "amount": v} for n, v in SHARE.findall(m[6])]})
        else:
            feed.append({"type": "note", "text": text, "from": post["from"], "mine": post["mine"]})
    return feed


class Say(BaseModel):
    room: str
    text: str


def main(me: str, port: int) -> None:
    config = json.loads((ROOT / "state" / f"{me}.json").read_text())
    trip = json.loads((ROOT / "data/synthetic/trip.json").read_text())
    login = config["mattermost"]
    channels = login["channels"]
    session = {"mm": Mattermost(login["username"], login["password"])}
    names: dict[str, str] = {}
    app = FastAPI(title=f"Kitty for {trip['people'][me]['name']}")

    def person_name(user_id: str) -> str:
        if user_id not in names:
            mm = session["mm"]
            r = mm.http.get(f"/api/v4/users/{user_id}")
            u = r.json() if r.status_code == 200 else {}
            names[user_id] = u.get("first_name") or u.get("username") or "Someone"
        return names[user_id]

    def read(room: str) -> list[dict]:
        mm = session["mm"]
        r = mm.http.get(f"/api/v4/channels/{channels[room]}/posts", params={"per_page": 200})
        r.raise_for_status()
        data = r.json()
        out = []
        for pid in reversed(data["order"]):
            post = data["posts"][pid]
            if post.get("type", "").startswith("system_") or post.get("delete_at"):
                continue
            mine = post["user_id"] == mm.user_id
            out.append({"id": pid, "text": post["message"], "mine": mine, "from": person_name(post["user_id"]), "at": post["create_at"]})
        return out

    @app.get("/api/feed")
    def feed() -> JSONResponse:
        try:
            private, desk, group = read("private"), read("desk"), read("group")
        except httpx.HTTPError as exc:
            return JSONResponse({"error": f"Can't reach Mattermost: {exc}"}, status_code=502)
        group_text = [p["text"] for p in group]
        return JSONResponse({
            "me": {"id": me, "name": trip["people"][me]["name"]},
            "people": [{"id": p, "name": d["name"]} for p, d in trip["people"].items()],
            "trip": trip["name"],
            "private": [{"text": p["text"], "mine": p["mine"]} for p in private if not HIDDEN.match(p["text"])],
            "desk": build_desk(desk, group_text),
            "group": build_group(group),
        })

    @app.post("/api/say")
    def say(body: Say) -> JSONResponse:
        if body.room not in channels:
            return JSONResponse({"error": "That room isn't yours."}, status_code=403)
        text = body.text.strip()[:600]
        if not text:
            return JSONResponse({"error": "Nothing to send."}, status_code=400)
        session["mm"].post(channels[body.room], text)
        return JSONResponse({"ok": True})

    @app.get("/")
    def page() -> FileResponse:
        return FileResponse(ROOT / "app" / "person.html")

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]))
