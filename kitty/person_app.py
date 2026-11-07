"""One person's own app. One process per person, on their own port.

    python -m kitty.person_app meera 7103

Holds only that person's Mattermost session, read from their own state file. It reads and posts in
their three channels as them, so anything it sends carries their real sender id. It never touches
Switch directly, and it has no credential that could read anyone else's room.

On a trip made in the app, this is also where the person opens their invite and adds their statement.
A statement goes from their browser to their own folder, through local validation. It goes nowhere
else from here: only the agent reads it, as before, and only when they ask it to start.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import unquote

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from pydantic import BaseModel

from kitty import access
from kitty import claims as C
from kitty import paths as P
from kitty import statement_import as SI
from kitty.switch_client import Mattermost
from kitty.trips import read_json, write_json

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("kitty.person_app")

READY_EXPENSE = re.compile(r"^\*\*(c\d+)\*\* ready \[ref [a-z0-9]+\]: ([₹$€£][\d,.]+) · (\w+) · (\d+ \w+) · paid by (\w+) · (.+?)\nReply", re.S)
READY_TRANSFER = re.compile(r"^\*\*(t\d+)\*\* ready \[ref [a-z0-9]+\]: you paid (\w+) ([₹$€£][\d,.]+) on (\d+ \w+)\.")
ANSWER = re.compile(r"^Your agent found that (t\d+) \(([₹$€£][\d,.]+) from (\w+)\) (reached you|did not reach you)\.")
GROUP_EXPENSE = re.compile(r"^\*\*(c\d+)\*\* ([₹$€£][\d,.]+) · (\w+) · (\d+ \w+) · paid by (\w+) · (.+)$")
SHARE = re.compile(r"(\w+) ([₹$€£][\d,]*\d\.\d{2})")
HIDDEN = re.compile(r"^(Hi, it's \w+\. \[|\w+'s agent checking in\. \[|Kitty clerk here\.|Hey! I'm on standby|Hi there! Just @)")
MACHINE = re.compile(r"^(KITTY/1 |KITTY_CHECK |KITTY_RECHECK |KITTY_POT_CHECK )")
READY_CONTRIBUTION = re.compile(r"^\*\*(p\d+)\*\* ready \[ref [a-z0-9]+\]: you paid (\w+) ([₹$€£][\d,.]+) on (\d+ \w+)\.")
TO_CHECK = re.compile(r"^\*\*(p\d+)\*\* to check: (\w+) says they paid you ([₹$€£][\d,.]+) on (\d+ \w+)\. Your agent: (found it|a different amount|nothing yet)\.")
ASKED_ABOUT = re.compile(r"^\w+ asks about (p\d+): \w+\. (.+)$")
DESK_COMMAND = re.compile(r"^\s*(release|drop|withdraw)\s", re.I)
REVIEW_COMMAND = re.compile(r"^\s*(start|go|review|rescan)\s*$", re.I)


def build_desk(posts: list[dict], group_text: list[str], pot_room: dict[str, dict] | None = None) -> dict:
    """`pot_room` is a Pot trip's contributions as the group room reads, and None otherwise."""
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
        elif pot_room is not None and (m := READY_CONTRIBUTION.match(text)):
            slips[m[1]] = {"id": m[1], "kind": "contribution", "to": m[2], "amount": m[3], "date": m[4], "state": "waiting"}
        elif pot_room is not None and (m := TO_CHECK.match(text)):
            slips[m[1]] = {"id": m[1], "kind": "pot_review", "from": m[2], "amount": m[3], "date": m[4], "candidate": m[5], "state": "waiting"}
        elif pot_room is not None and (m := ASKED_ABOUT.match(text)):
            if m[1] in slips:
                slips[m[1]]["note"] = m[2]
        elif pot_room is not None and (m := re.match(r"^(p\d+): asked\.", text)):
            if m[1] in slips:
                slips[m[1]]["asked"] = True
        elif pot_room is not None and (m := re.match(r"^(p\d+) dropped", text)):
            if m[1] in slips:
                slips[m[1]]["state"] = "dropped"
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
        if slip["kind"] in ("contribution", "pot_review") and slip["id"] in pot_room:
            status, basis = pot_room[slip["id"]]["status"], pot_room[slip["id"]]["basis"]
            slip["basis"] = basis
            if slip["kind"] == "contribution" or (status != "submitted" and (status != "needs_clarification" or slip.get("asked"))):
                slip["state"] = status
        if slip["kind"] == "transfer" and slip["state"] == "released":
            if f"**{slip['id']} confirmed by both sides" in joined:
                slip["state"] = "confirmed"
            elif f"**{slip['id']} NOT CONFIRMED" in joined:
                slip["state"] = "challenged"
        if slip["kind"] == "expense" and slip["state"] == "released":
            if re.search(rf"\*\*Possible conflict:\*\* (?:{slip['id']} and c\d+|c\d+ and {slip['id']}) ", joined) and not re.search(rf"Conflict cleared: \w+ withdrew c\d+\. \*\*{slip['id']}\*\* now counts", joined):
                slip["state"] = "conflict"
    return {"slips": list(slips.values()), "raw": raw[-40:], "notices": notices[-3:]}


def build_group(posts: list[dict], clerk_uid: str | None = None) -> list[dict]:
    """On a Pot trip `clerk_uid` is given, and only the clerk's own posts are read as anything but chat."""
    feed = []
    for post in posts:
        text = post["text"]
        if HIDDEN.match(text):
            continue
        if clerk_uid is not None:
            is_clerk = post.get("uid") == clerk_uid
            if is_clerk and (m := GROUP_EXPENSE.match(text)):
                feed.append({"type": "expense", "id": m[1], "amount": m[2], "category": m[3], "date": m[4], "payer": m[5], "shares": [{"name": n, "amount": v} for n, v in SHARE.findall(m[6])]})
            else:
                feed.append({"type": "note", "text": text, "from": post["from"], "mine": post["mine"], "clerk": is_clerk})
            continue
        if m := GROUP_EXPENSE.match(text):
            feed.append({"type": "expense", "id": m[1], "amount": m[2], "category": m[3], "date": m[4], "payer": m[5], "shares": [{"name": n, "amount": v} for n, v in SHARE.findall(m[6])]})
        else:
            feed.append({"type": "note", "text": text, "from": post["from"], "mine": post["mine"]})
    return feed


def pot_view(group_text: list[str], me: str, people: dict[str, dict], treasurer: str) -> dict:
    """Asked, confirmed and outstanding, from the group room's own text."""
    from kitty.pot import parse_room

    room = parse_room(group_text, {d["name"]: p for p, d in people.items()})
    credited = {p: 0 for p in people}
    for pay in room.payments:
        credited[pay.from_id] += pay.amount_paise

    def line(person: str) -> dict:
        return {"asked": room.asked[person], "credited": credited[person], "outstanding": max(0, room.asked[person] - credited[person]), "above": max(0, credited[person] - room.asked[person])}

    return {
        "treasurer": people[treasurer]["name"],
        "is_treasurer": me == treasurer,
        "mine": line(me),
        "roll": [{"name": d["name"], **line(p)} for p, d in people.items() if p != treasurer],
    }


class Say(BaseModel):
    room: str
    text: str


def write_private_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(text)
    os.replace(tmp, path)


def page_message(title: str, body: str, status: int) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>Kitty</title>"
        "<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0E3B43;color:#E4F3EF;font:17px/1.5 system-ui,sans-serif;padding:24px}main{max-width:26rem}h1{font-size:28px;line-height:1.15;color:#fff;margin:0 0 10px}</style>"
        f"<main><h1>{title}</h1><p>{body}</p></main>",
        status_code=status,
    )


def create_app(me: str, port: int, where: P.TripPaths, trip: dict, config: dict, login_session) -> FastAPI:
    """`login_session()` returns this person's Mattermost session, opened with their own password."""
    channels = config["mattermost"]["channels"]
    session = {"mm": login_session()}
    names: dict[str, str] = {}
    people = trip["people"]
    my_name = people[me]["name"]
    currency = trip.get("currency", C.CURRENCY)
    trip_model = C.Trip(trip["trip_id"], date.fromisoformat(trip["start"]), date.fromisoformat(trip["end"]), tuple(people), currency)
    directory = {d["statement_name"]: p for p, d in people.items()}
    statement_path, meta_path = where.statement(me), where.statement_meta(me)
    access_path = where.access(me)
    protected = access_path.exists()
    cookie = access.cookie_name(trip["trip_id"], me)
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    origins = {f"http://{h}" for h in hosts}
    app = FastAPI(title=f"Kitty for {my_name}", docs_url=None, redoc_url=None, openapi_url=None)

    def signed_in(request: Request) -> bool:
        return not protected or access.valid_session(access_path, request.cookies.get(cookie))

    @app.middleware("http")
    async def guard(request: Request, call_next):
        # Host and Origin stop another website in the same browser from driving this app, and
        # the cookie stops anyone without this person's link.
        if request.headers.get("host") not in hosts:
            return PlainTextResponse("Not here.", status_code=421)
        origin = request.headers.get("origin")
        if request.method not in ("GET", "HEAD") and origin is not None and origin not in origins:
            return JSONResponse({"error": "That request came from somewhere else."}, status_code=403)
        path = request.url.path
        if path not in ("/healthz", "/join") and not signed_in(request):
            if path.startswith("/api/"):
                return JSONResponse({"error": "Open your invite link first."}, status_code=401)
            return page_message("This is someone's private Kitty.", "To open yours, use the invite link your organiser gave you.", 401)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    def person_name(user_id: str) -> str:
        if user_id not in names:
            mm = session["mm"]
            r = mm.http.get(f"/api/v4/users/{user_id}")
            u = r.json() if r.status_code == 200 else {}
            names[user_id] = u.get("first_name") or u.get("username") or "Someone"
        return names[user_id]

    def read(room: str, whole: bool = False) -> list[dict]:
        """One page, or on a Pot trip's group room the whole history, because the figures are read from it."""
        mm = session["mm"]
        order: list[str] = []
        posts: dict[str, dict] = {}
        for page in range(1000 if whole else 1):
            params = {"per_page": 200, "page": page} if whole else {"per_page": 200}
            r = mm.http.get(f"/api/v4/channels/{channels[room]}/posts", params=params)
            r.raise_for_status()
            data = r.json()
            order += data["order"]
            posts.update(data["posts"])
            if len(data["order"]) < 200:
                break
        out = []
        for pid in reversed(order):
            post = posts[pid]
            if post.get("type", "").startswith("system_") or post.get("delete_at"):
                continue
            mine = post["user_id"] == mm.user_id
            out.append({"id": pid, "text": post["message"], "mine": mine, "from": person_name(post["user_id"]), "at": post["create_at"], "uid": post["user_id"]})
        return out

    def clerk_uid() -> str:
        """The clerk's chat identity. Text can claim anything, and the sender can't."""
        if "clerk_uid" not in session:
            r = session["mm"].http.get(f"/api/v4/users/username/{config['clerk_bot']}")
            r.raise_for_status()
            session["clerk_uid"] = r.json()["id"]
        return session["clerk_uid"]

    def treasurer_locked() -> bool:
        """Once claims exist, the treasurer's agent knows statement lines by position, so the file may only grow."""
        if trip.get("treasurer") != me:
            return False
        from kitty.pot import parse_room

        uid = clerk_uid()
        lines = [p["text"] for p in read("group", whole=True) if p["uid"] == uid]
        return bool(parse_room(lines, {d["name"]: p for p, d in people.items()}).contributions)

    def review_started(private: list[dict] | None = None) -> bool:
        posts = read("private") if private is None else private
        return any(p["mine"] and REVIEW_COMMAND.match(p["text"]) for p in posts)

    def write_progress() -> None:
        """Only yes or no, and only what the upload screen tells the person the organiser sees."""
        write_json(where.person_status(me), {"joined": access.joined(access_path), "statement": statement_path.exists()}, 0o644)

    def statement_view(private: list[dict] | None = None) -> dict:
        if not statement_path.exists():
            return {"state": "none"}
        accepted = SI.read_saved(statement_path)
        meta = read_json(meta_path) or {}
        return {"state": "reviewing" if review_started(private) else "ready", "added_at": meta.get("added_at"), "summary": SI.summary(accepted, trip_model, directory)}

    @app.get("/healthz")
    def healthz() -> PlainTextResponse:
        return PlainTextResponse("ok")

    @app.get("/join", response_model=None)
    def join(request: Request, t: str = "") -> HTMLResponse | RedirectResponse:
        if not protected or access.valid_session(access_path, request.cookies.get(cookie)):
            return RedirectResponse("/", status_code=303)
        token = access.claim(access_path, t) if t else None
        if token is None:
            return page_message("This link has already been used.", "Invite links open once, so nobody can reuse yours. Ask your organiser for a new one.", 403)
        write_progress()
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(cookie, token, httponly=True, samesite="strict", max_age=60 * 60 * 24 * 30, path="/")
        return response

    @app.get("/api/feed")
    def feed() -> JSONResponse:
        try:
            treasurer = trip.get("treasurer")
            private, desk, group = read("private"), read("desk"), read("group", whole=bool(treasurer))
            uid = clerk_uid() if treasurer else None
        except httpx.HTTPError:
            return JSONResponse({"error": "Can't reach Mattermost. Check that it's running."}, status_code=502)
        extra: dict = {}
        built_desk = None
        if treasurer:
            from kitty.pot import parse_room, window_end

            # Only the clerk's own posts. A person can type anything in the group room.
            group_text = [p["text"] for p in group if p["uid"] == uid]
            room = parse_room(group_text, {d["name"]: p for p, d in people.items()})
            built_desk = build_desk(desk, group_text, room.contributions)
            if me == treasurer:
                shown = {s["id"] for s in built_desk["slips"]}
                built_desk["slips"] += [
                    {"id": cid, "kind": "pot_review", "from": people[c["from"]]["name"], "amount": C.money(c["amount"], currency), "date": "", "candidate": "not checked yet", "state": "waiting"}
                    for cid, c in room.contributions.items() if c["status"] == "submitted" and cid not in shown
                ]
            extra["pot"] = {
                **pot_view(group_text, me, people, treasurer),
                "window": {"from": trip["collect_from"], "to": window_end(trip_model).isoformat(), "spend_from": trip["start"], "spend_to": trip["end"]},
                "calls": [{"id": k, "amount": c["amount"], "who": c["label"]} for k, c in room.calls.items()],
            }
        else:
            group_text = [p["text"] for p in group]
        return JSONResponse({
            **extra,
            "me": {"id": me, "name": my_name},
            "people": [{"id": p, "name": d["name"]} for p, d in people.items()],
            "trip": trip["name"],
            "in_app": where.in_app,
            "dates": {"start": trip["start"], "end": trip["end"]},
            "currency": currency,
            "statement": statement_view(private) if where.in_app else None,
            "private": [{"text": p["text"], "mine": p["mine"]} for p in private if not HIDDEN.match(p["text"])],
            "desk": built_desk if treasurer else build_desk(desk, group_text),
            "group": build_group(group, uid),
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

    def refusal(rejected: SI.Rejected, status: int = 422) -> JSONResponse:
        return JSONResponse({"status": "rejected", "problems": [p.view() for p in rejected.problems], "total": rejected.total}, status_code=status)

    @app.get("/api/statement")
    def statement_status() -> JSONResponse:
        return JSONResponse(statement_view())

    @app.post("/api/statement")
    async def statement_upload(request: Request) -> JSONResponse:
        """Raw CSV in the body. Its content type also makes a cross-site request need a preflight, which fails."""
        if not where.in_app:
            return JSONResponse({"error": "The original demo's statements are set up by its script."}, status_code=404)
        if request.headers.get("content-type", "").split(";")[0].strip() != "text/csv":
            return refusal(SI.Rejected([SI.Problem("not_csv", "Kitty needs a CSV file.")]), 415)
        if request.headers.get("x-kitty-synthetic") != "yes":
            return refusal(SI.Rejected([SI.Problem("synthetic_required", "Confirm that this is a made-up statement first.")]), 400)
        try:
            SI.check_filename(unquote(request.headers.get("x-filename", "")))
        except SI.Rejected as rejected:
            return refusal(rejected, 415)
        too_large = SI.Rejected([SI.Problem("too_large", f"That file is over {SI.MAX_BYTES // 1024} KB. A trip's statement is much smaller, so check that it's the right file.")])
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > SI.MAX_BYTES:
            return refusal(too_large, 413)
        data = bytearray()
        async for chunk in request.stream():
            data += chunk
            if len(data) > SI.MAX_BYTES:
                return refusal(too_large, 413)
        try:
            accepted = SI.validate(bytes(data))
            existing = SI.read_saved(statement_path) if statement_path.exists() else None
            if existing is not None and existing.fingerprint == accepted.fingerprint:
                return JSONResponse({"status": "duplicate", "message": "You've already added this file, so nothing changed.", **statement_view()})
            added = None
            started = False
            if existing is not None:
                try:
                    started = review_started() or treasurer_locked()
                except httpx.HTTPError:
                    return JSONResponse({"status": "rejected", "problems": [{"code": "chat_unreachable", "message": "Kitty couldn't check where your review has got to. Try again in a moment.", "line": None, "column": None}], "total": 1}, status_code=503)
                if started:
                    accepted, added = SI.merge(existing, accepted)
                    if added == 0:
                        return JSONResponse({"status": "duplicate", "message": "Every line in that file is already here, so nothing changed.", **statement_view()})
        except SI.Rejected as rejected:
            return refusal(rejected)
        write_private_text(statement_path, accepted.text)
        write_json(meta_path, {"fingerprint": accepted.fingerprint, "added_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        write_progress()
        log.info("statement accepted")  # No file name, no count, no content.
        status = "added_lines" if started else "replaced" if existing is not None else "accepted"
        return JSONResponse({"status": status, "added": added, **statement_view()})

    @app.delete("/api/statement")
    def statement_remove() -> JSONResponse:
        if not statement_path.exists():
            return JSONResponse({"state": "none"})
        if review_started() or treasurer_locked():
            return JSONResponse({"error": "Your agent has already started on this statement, so it has to stay."}, status_code=409)
        statement_path.unlink()
        meta_path.unlink(missing_ok=True)
        write_progress()
        return JSONResponse({"state": "none"})

    @app.get("/api/statement/model-preview")
    def statement_preview() -> JSONResponse:
        if not statement_path.exists():
            return JSONResponse({"lines": []})
        return JSONResponse({"lines": SI.model_preview(SI.read_saved(statement_path), trip_model, directory)})

    @app.get("/api/statement/sample")
    def statement_sample() -> PlainTextResponse:
        return PlainTextResponse(SI.sample(trip_model.start, trip_model.end), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="kitty-made-up-statement.csv"'})

    @app.get("/")
    def page() -> FileResponse:
        return FileResponse(ROOT / "app" / "person.html")

    return app


def main(me: str, port: int) -> None:
    where = P.current()
    config = json.loads((where.state / f"{me}.json").read_text())
    trip = json.loads(where.trip_json.read_text())
    login = config["mattermost"]
    app = create_app(me, port, where, trip, config, lambda: Mattermost(login["username"], login["password"]))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]))
