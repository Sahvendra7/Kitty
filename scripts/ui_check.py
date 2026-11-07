"""Looks at Kitty's pages in a real browser, in every state they can be in, without the stack.

    uv run --with playwright==1.58.0 python scripts/ui_check.py OUT_DIR [--axe path/to/axe.min.js] [--only name]

The pages and their assets come from disk. Their /api calls are answered from the FIXTURES below, in the
shapes the real servers return. These figures exist only here, to reach states that are slow to reach
for real (a stuck set-up, a very long name, a refused confirm). Nothing here reaches the product. The
real-run stills come from scripts/pot_trip.py --shots.

It fails on: a page wider than its screen, a console error, or a serious accessibility finding when axe is given.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"
SIZES = {"375": (375, 780), "768": (768, 1000), "1440": (1440, 900)}
TYPES = {".css": "text/css", ".js": "text/javascript", ".woff2": "font/woff2", ".html": "text/html"}
NAMES = ["Rohan", "Meera", "Dev", "Esha", "Farhan"]


def steps(states: dict, people: list[str]) -> list[dict]:
    out = [{"id": "connect", "person": None, "label": "Connecting to Switch", "state": states.get("connect", "done")}, {"id": "clerk", "person": None, "label": "Making the clerk", "state": states.get("clerk", "done")}]
    for p in people:
        for i, label in enumerate(["Making the account", "Making the agent", "Making the rooms", "Starting the app"]):
            state = states.get(p, ["done"] * 4)[i]
            step = {"id": f"{p}-{i}", "person": p, "label": label, "state": state}
            if state == "failed":
                step["error"] = {"message": "Mattermost didn't answer. Check that it's running, then try again."}
            out.append(step)
    out.append({"id": "group", "person": None, "label": "Opening the group room", "state": states.get("group", "pending")})
    return out


def person(slug, name, stmt=None, done=4, total=4, failed=False, joined=False, added=False, invite=False, app=False):
    p = {"id": slug, "name": name, "statement_name": stmt or name.upper(), "steps_done": done, "steps_total": total, "failed": failed, "joined": joined, "statement_added": added}
    if invite:
        p["invite_url"] = f"http://localhost:7999/join?t=x{slug}"
    if app:
        p["app_url"] = "http://localhost:7999/"
    return p


def pot_board(asked=500000, credits=(500000, 300000, 500000, 0, 0), square=False, contributions=None, plan=None, balances=None):
    roll = [{"name": n, "asked": asked, "credited": c, "outstanding": max(0, asked - c), "above": max(0, c - asked)} for n, c in zip(NAMES, credits)]
    return {
        "pot": {"treasurer": "Asha", "roll": roll, "position": sum(credits), "position_line": f"Asha should be holding ₹{sum(credits)//100:,}.00, by the records.", "contributions": contributions if contributions is not None else [
            {"id": "p1", "from": "Rohan", "amount_paise": 500000, "date": "2026-09-28", "status": "confirmed", "basis": "records"},
            {"id": "p2", "from": "Meera", "amount_paise": 300000, "date": "2026-09-29", "status": "confirmed", "basis": "word"},
            {"id": "p3", "from": "Dev", "amount_paise": 500000, "date": "2026-09-30", "status": "confirmed", "basis": "records"},
            {"id": "p4", "from": "Esha", "amount_paise": 500000, "date": "2026-10-01", "status": "recipient_review", "basis": None},
            {"id": "p5", "from": "Farhan", "amount_paise": 400000, "date": "2026-10-01", "status": "needs_clarification", "basis": None},
        ], "open": 2, "square": square},
        "trip": {"name": "Goa pot", "start": "2026-10-16", "end": "2026-10-19", "currency": "INR"},
        "people": [{"id": "asha", "name": "Asha"}] + [{"id": n.lower(), "name": n} for n in NAMES],
        "expenses": [{"id": "c1", "date": "2026-10-16", "amount_paise": 2400000, "category": "stay", "payer": "Asha", "shares": [{"name": n, "paise": 400000} for n in ["Asha"] + NAMES], "status": "released"}, {"id": "c2", "date": "2026-10-17", "amount_paise": 360000, "category": "groceries", "payer": "Dev", "shares": [{"name": n, "paise": 60000} for n in ["Asha"] + NAMES], "status": "released"}],
        "transfers": [],
        "balances": balances if balances is not None else [{"name": "Asha", "paise": 390000}, {"name": "Rohan", "paise": -110000}, {"name": "Meera", "paise": -310000}, {"name": "Dev", "paise": 250000}, {"name": "Esha", "paise": -110000}, {"name": "Farhan", "paise": -110000}],
        "plan": plan if plan is not None else [{"from": "Meera", "to": "Asha", "paise": 310000}, {"from": "Rohan", "to": "Asha", "paise": 110000}, {"from": "Asha", "to": "Dev", "paise": 250000}],
        "held_count": 0,
    }


SHARED_BOARD = {
    "trip": {"name": "Coorg weekend", "start": "2026-10-16", "end": "2026-10-19", "currency": "INR"},
    "people": [{"id": "priya", "name": "Priya"}, {"id": "arjun", "name": "Arjun"}, {"id": "meera", "name": "Meera"}],
    "expenses": [{"id": "c1", "date": "2026-10-16", "amount_paise": 1200000, "category": "stay", "payer": "Priya", "shares": [{"name": n, "paise": 400000} for n in ["Priya", "Arjun", "Meera"]], "status": "released"}, {"id": "c2", "date": "2026-10-17", "amount_paise": 345000, "category": "food", "payer": "Arjun", "shares": [{"name": n, "paise": 115000} for n in ["Priya", "Arjun", "Meera"]], "status": "released"}],
    "transfers": [{"id": "t1", "date": "2026-10-20", "amount_paise": 100000, "from": "Meera", "to": "Priya", "status": "challenged", "reason": None}, {"id": "t2", "date": "2026-10-20", "amount_paise": 285000, "from": "Arjun", "to": "Priya", "status": "confirmed", "reason": None}],
    "balances": [{"name": "Priya", "paise": 515000}, {"name": "Arjun", "paise": -170000}, {"name": "Meera", "paise": -345000}],
    "plan": [{"from": "Meera", "to": "Priya", "paise": 345000}, {"from": "Arjun", "to": "Priya", "paise": 170000}],
    "held_count": 1,
}
SQUARE = {**pot_board(credits=(500000, 500000, 500000, 500000, 500000), square=True, plan=[], balances=[{"name": n, "paise": 0} for n in ["Asha"] + NAMES], contributions=[{"id": f"p{i+1}", "from": n, "amount_paise": 500000, "date": "2026-09-28", "status": "confirmed", "basis": "records"} for i, n in enumerate(NAMES)]), "transfers": [{"id": "t1", "date": "2026-10-20", "amount_paise": 250000, "from": "Asha", "to": "Dev", "status": "confirmed", "reason": None}]}
HUGE = pot_board(asked=125000000, credits=(125000000, 98765432, 0, 125000000, 3100), contributions=[{"id": "p1", "from": "Maximilianovich", "amount_paise": 125000000, "date": "2026-09-28", "status": "confirmed", "basis": "records"}])
HUGE["balances"] = [{"name": "Maximilianovich", "paise": 998765432}, {"name": "Wolfeschlegels", "paise": -998765432}]
HUGE["trip"]["name"] = "The extremely long annual reunion in Goa"
HUGE["pot"]["roll"][0]["name"] = "Maximilianovich"
HUGE["pot"]["roll"][1]["name"] = "Wolfeschlegels"

LIVE_PEOPLE = [person("asha", "Asha", joined=True, added=True, app=True), person("rohan", "Rohan", joined=True, app=True), person("meera", "Meera", invite=True), person("dev", "Dev", invite=True)]


def trip_detail(tid, name, state, people, board=None, treasurer=None, up=True, editable=False, running=False, prov=None):
    return {"id": tid, "name": name, "start": "2026-10-16", "end": "2026-10-19", "currency": "INR", "state": state, "people": people, "provision": prov or {"state": state, "steps": [], "roster_locked": state == "live"}, "editable": editable, "running": running, "apps_up": up and state == "live", "limits": {"min": 2, "max": 12}, "treasurer": treasurer, "collect_from": "2026-09-25" if treasurer else None, "pot_cleared": None, **({"board_url": "http://localhost:7999/board", "board": board} if board is not None else {})}


TRIPS = {
    "goa": trip_detail("goa", "Goa pot", "live", LIVE_PEOPLE, pot_board(), "asha"),
    "coorg": trip_detail("coorg", "Coorg weekend", "live", LIVE_PEOPLE[:3], SHARED_BOARD),
    "draft": trip_detail("draft", "Hampi in January", "idle", [person("kavya", "Kavya", done=0, total=0), person("nikhil", "Nikhil", "NIKHIL S", done=0, total=0)], editable=True),
    "empty": trip_detail("empty", "Pondicherry", "idle", [], editable=True),
    "stuck": trip_detail("stuck", "Spiti", "failed", [person("kavya", "Kavya", failed=True, done=2), person("nikhil", "Nikhil", done=4)], editable=False, prov={"state": "failed", "steps": steps({"kavya": ["done", "done", "failed", "pending"]}, ["kavya", "nikhil"]), "roster_locked": True}),
    "building": trip_detail("building", "Ladakh", "running", [person("kavya", "Kavya", done=3), person("nikhil", "Nikhil", done=1)], running=True, prov={"state": "running", "steps": steps({"kavya": ["done", "done", "done", "running"], "nikhil": ["done", "running", "pending", "pending"]}, ["kavya", "nikhil"]), "roster_locked": True}),
    "square": trip_detail("square", "Hampi", "live", LIVE_PEOPLE[:2], SQUARE, "asha"),
    "down": trip_detail("down", "Ooty", "live", LIVE_PEOPLE[:2], None, "asha", up=False),
}


def summary(t):
    return {k: t[k] for k in ("id", "name", "start", "end", "currency", "state")} | {"people": [p["name"] for p in t["people"]]}


def feed(me="rohan", my_name="Rohan", tab_data=None, pot=None, private=None, slips=None, group=None, statement=None, notices=None):
    return {"pot": pot, **({} if pot else {}), "me": {"id": me, "name": my_name}, "people": [{"id": "asha", "name": "Asha"}] + [{"id": n.lower(), "name": n} for n in NAMES], "trip": "Goa pot", "in_app": True, "dates": {"start": "2026-10-16", "end": "2026-10-19"}, "currency": "INR", "statement": statement or {"state": "reviewing", "summary": {"lines": 41, "in_trip": 18, "outside_trip": 23, "payments_to_friends": 3, "incoming": 2, "spending": 13}}, "private": private or [], "desk": {"slips": slips or [], "raw": ['KITTY/1 {"v":1,"type":"contribution","amount_paise":500000}'] if slips else [], "notices": notices or []}, "group": group or []}


WINDOW = {"from": "2026-09-25", "to": "2026-11-18", "spend_from": "2026-10-16", "spend_to": "2026-10-19"}
ROLL = [{"name": n, "asked": 500000, "credited": c, "outstanding": max(0, 500000 - c), "above": 0} for n, c in zip(NAMES, (500000, 300000, 500000, 0, 0))]
POT_T = {"treasurer": "Asha", "is_treasurer": True, "mine": {"asked": 0, "credited": 0, "outstanding": 0, "above": 0}, "roll": ROLL, "window": WINDOW, "calls": [{"id": "k1", "amount": 500000, "who": "each person"}]}
POT_M = {"treasurer": "Asha", "is_treasurer": False, "mine": {"asked": 500000, "credited": 300000, "outstanding": 200000, "above": 0}, "roll": ROLL, "window": WINDOW, "calls": [{"id": "k1", "amount": 500000, "who": "each person"}]}
GROUP = [
    {"type": "note", "text": "**k1** Asha asks each person for ₹5,000.00.", "from": "Kitty clerk", "mine": False, "clerk": True},
    {"type": "note", "text": "**p1** Rohan says they paid Asha ₹5,000.00 on 28 Sep · ref …1001. Not counted yet.", "from": "Kitty clerk", "mine": False, "clerk": True},
    {"type": "note", "text": "**p1 confirmed received by Asha:** Rohan paid ₹5,000.00 · checked against Asha's records · 28 Sep, 14:02.", "from": "Kitty clerk", "mine": False, "clerk": True},
    {"type": "note", "text": "Paid! Check your account", "from": "Meera", "mine": False, "clerk": False},
    {"type": "expense", "id": "c1", "amount": "₹24,000.00", "category": "stay", "date": "16 Oct", "payer": "Asha", "shares": [{"name": "Asha", "amount": "₹4,000.00"}, {"name": "Rohan", "amount": "₹4,000.00"}]},
    {"type": "note", "text": "| Person | Asked | Confirmed | Outstanding |\n|---|---:|---:|---:|\n| Rohan | ₹5,000.00 | ₹5,000.00 | ₹0.00 |\n| Meera | ₹5,000.00 | ₹3,000.00 | ₹2,000.00 |\n\nAsha should be holding ₹8,000.00, by the records.", "from": "Kitty clerk", "mine": False, "clerk": True},
    {"type": "note", "text": "Suggested payments: Meera pays Asha ₹3,100.00. Rohan pays Asha ₹1,100.00.", "from": "Kitty clerk", "mine": False, "clerk": True},
]
T_SLIPS = [
    {"id": "p4", "kind": "pot_review", "from": "Esha", "amount": "₹5,000.00", "date": "1 Oct", "candidate": "found it", "state": "waiting"},
    {"id": "p5", "kind": "pot_review", "from": "Farhan", "amount": "₹4,000.00", "date": "1 Oct", "candidate": "a different amount", "state": "waiting"},
    {"id": "p6", "kind": "pot_review", "from": "Dev", "amount": "₹1,250.00", "date": "2 Oct", "candidate": "nothing yet", "state": "waiting"},
    {"id": "p7", "kind": "pot_review", "from": "Meera", "amount": "₹2,000.00", "date": "", "candidate": "not checked yet", "state": "waiting"},
    {"id": "p1", "kind": "pot_review", "from": "Rohan", "amount": "₹5,000.00", "date": "28 Sep", "candidate": "found it", "state": "confirmed", "basis": "records"},
    {"id": "p2", "kind": "pot_review", "from": "Meera", "amount": "₹3,000.00", "date": "29 Sep", "candidate": "nothing yet", "state": "confirmed", "basis": "word"},
    {"id": "p3", "kind": "pot_review", "from": "Dev", "amount": "₹800.00", "date": "30 Sep", "candidate": "nothing yet", "state": "rejected", "basis": None},
]
M_SLIPS = [
    {"id": "p8", "kind": "contribution", "to": "Asha", "amount": "₹1,000.00", "date": "3 Oct", "state": "waiting"},
    {"id": "p5", "kind": "contribution", "to": "Asha", "amount": "₹1,000.00", "date": "2 Oct", "state": "submitted"},
    {"id": "p4", "kind": "contribution", "to": "Asha", "amount": "₹1,000.00", "date": "1 Oct", "state": "needs_clarification", "note": "The amount they can see is different. Withdraw it and claim the right amount."},
    {"id": "p3", "kind": "contribution", "to": "Asha", "amount": "₹800.00", "date": "30 Sep", "state": "rejected"},
    {"id": "p2", "kind": "contribution", "to": "Asha", "amount": "₹3,000.00", "date": "29 Sep", "state": "confirmed", "basis": "records"},
]
SHARED_SLIPS = [
    {"id": "c3", "kind": "expense", "amount": "₹3,450.00", "category": "food", "date": "17 Oct", "shares": [{"name": "Priya", "amount": "₹1,150.00"}, {"name": "Arjun", "amount": "₹1,150.00"}, {"name": "Meera", "amount": "₹1,150.00"}], "state": "waiting"},
    {"id": "t1", "kind": "transfer", "to": "Priya", "amount": "₹1,000.00", "date": "20 Oct", "state": "challenged"},
    {"id": "c1", "kind": "expense", "amount": "₹12,000.00", "category": "stay", "date": "16 Oct", "shares": [{"name": "Priya", "amount": "₹4,000.00"}], "state": "released"},
]
CHAT = [
    {"text": "Hi Rohan. I'm your agent. Say `start` and I'll go through your statement.", "mine": False},
    {"text": "start", "mine": True},
    {"text": "I found 13 lines from the trip. ₹1,250.50 at SOME RESTAURANT on 16 Oct, 20:15. Was this shared with the group?", "mine": False},
]
FEEDS = {
    "treasurer": feed("asha", "Asha", pot=POT_T, slips=T_SLIPS, group=GROUP, notices=["Refused: reference_differs."]),
    "treasurer-new": feed("asha", "Asha", pot={**POT_T, "calls": [], "roll": [{**r, "asked": 0, "credited": 0, "outstanding": 0} for r in ROLL]}, group=[]),
    "member": feed(pot=POT_M, slips=M_SLIPS, group=GROUP),
    "member-new": feed(pot={**POT_M, "mine": {"asked": 500000, "credited": 0, "outstanding": 500000, "above": 0}}, statement={"state": "none"}, group=GROUP[:1]),
    "group": feed(pot=POT_M, group=GROUP),
    "group-shared": feed(group=[g for g in GROUP if g.get("clerk") is False or g["type"] == "expense"], slips=SHARED_SLIPS),
    "desk-shared": feed(slips=SHARED_SLIPS, group=[]),
    "desk-empty": feed(group=[]),
    "chat": feed(private=CHAT, slips=SHARED_SLIPS),
    "statement-none": feed(statement={"state": "none"}),
    "statement-ready": feed(statement={"state": "ready", "summary": {"lines": 41, "in_trip": 18, "outside_trip": 23, "payments_to_friends": 3, "incoming": 2, "spending": 13}}),
}


class Stage:
    def __init__(self, ctx):
        self.ctx = ctx
        self.state = {"trips": list(TRIPS), "board": pot_board(), "feed": FEEDS["member"]}
        ctx.route("http://localhost:7999/**", self.serve)

    def serve(self, route):
        path = re.sub(r"^http://localhost:7999", "", route.request.url).split("?")[0]
        if path.startswith("/ui/"):
            f = APP / path[1:]
            return route.fulfill(status=200, body=f.read_bytes(), headers={"content-type": TYPES.get(f.suffix, "application/octet-stream")})
        if path == "/api/trips":
            return route.fulfill(json={"trips": [summary(TRIPS[t]) for t in self.state["trips"]]})
        if m := re.match(r"^/api/trips/([\w-]+)$", path):
            return route.fulfill(json=TRIPS[m[1]]) if m[1] in TRIPS else route.fulfill(status=404, json={"error": "There's no such trip or person."})
        if path == "/api/board":
            return route.fulfill(json=self.state["board"]) if self.state["board"] else route.fulfill(status=500, json={})
        if path == "/api/feed":
            return route.fulfill(json=self.state["feed"])
        if path == "/api/statement/model-preview":
            return route.fulfill(json={"lines": []})
        if path in ("/api/say",):
            return route.fulfill(json={"ok": True})
        page = {"/board": "board.html", "/me": "person.html"}.get(path, "home.html")
        return route.fulfill(status=200, body=(APP / page).read_bytes(), headers={"content-type": "text/html"})


def scenes(stage):
    """name, url, what the fake server says, then what to do in the page."""
    yield "home", "/", {"trips": ["goa", "coorg", "draft", "stuck"]}, None
    yield "home-empty", "/", {"trips": []}, None
    yield "new", "/trips/new", {}, None
    yield "people-empty", "/trips/empty", {}, None
    yield "people", "/trips/draft", {}, None
    yield "review-shared", "/trips/draft", {}, "document.getElementById('toReview').click()"
    yield "review-pot", "/trips/draft", {}, "document.getElementById('toReview').click();document.querySelector('input[name=kind][value=pot]').click()"
    yield "building", "/trips/building", {}, None
    yield "stuck", "/trips/stuck", {}, None
    yield "live-pot", "/trips/goa", {}, None
    yield "live-shared", "/trips/coorg", {}, None
    yield "live-down", "/trips/down", {}, None
    yield "live-square", "/trips/square", {}, None
    yield "no-trip", "/trips/nope", {}, None
    for name in ("treasurer", "treasurer-new", "member", "member-new"):
        yield f"desk-{name}", "/me", {"feed": FEEDS[name]}, "document.querySelector('nav button[data-t=desk]').click()"
    yield "desk-shared", "/me", {"feed": FEEDS["desk-shared"]}, "document.querySelector('nav button[data-t=desk]').click()"
    yield "desk-empty", "/me", {"feed": FEEDS["desk-empty"]}, "document.querySelector('nav button[data-t=desk]').click()"
    yield "group", "/me", {"feed": FEEDS["group"]}, "document.querySelector('nav button[data-t=group]').click()"
    yield "chat", "/me", {"feed": FEEDS["chat"]}, "document.querySelector('nav button[data-t=private]').click()"
    yield "statement-none", "/me", {"feed": FEEDS["statement-none"]}, "document.querySelector('nav button[data-t=statement]').click()"
    yield "statement-ready", "/me", {"feed": FEEDS["statement-ready"]}, "document.querySelector('nav button[data-t=statement]').click()"
    yield "board-pot", "/board", {"board": pot_board()}, None
    yield "board-new", "/board", {"board": pot_board(asked=0, credits=(0, 0, 0, 0, 0), contributions=[], plan=[], balances=[{"name": n, "paise": 0} for n in ["Asha"] + NAMES]) | {"expenses": []}}, None
    yield "board-shared", "/board", {"board": SHARED_BOARD}, None
    yield "board-square", "/board", {"board": SQUARE}, None
    yield "board-huge", "/board", {"board": HUGE}, None
    yield "board-down", "/board", {"board": None}, None
    open_panel = "document.querySelector('.kc-btn').click();"
    yield "panel-home", "/", {"trips": ["goa", "coorg"]}, "document.getElementById('heroBtn').click()"
    yield "panel-treasurer", "/me", {"feed": FEEDS["treasurer"]}, "document.querySelector('nav button[data-t=desk]').click();" + open_panel + "setTimeout(()=>[...document.querySelectorAll('.kp-ask')].find(b=>/unmatched/.test(b.textContent)).click(),60)"
    yield "panel-member", "/me", {"feed": FEEDS["member"]}, "document.querySelector('nav button[data-t=desk]').click();" + open_panel + "setTimeout(()=>[...document.querySelectorAll('.kp-ask')].find(b=>/confirmations/.test(b.textContent)).click(),60)"
    yield "panel-board", "/board", {"board": pot_board()}, open_panel + "setTimeout(()=>[...document.querySelectorAll('.kp-ask')].find(b=>/outstanding/.test(b.textContent)).click(),60)"
    yield "panel-private", "/me", {"feed": FEEDS["chat"]}, "document.querySelector('nav button[data-t=statement]').click();" + open_panel + "setTimeout(()=>[...document.querySelectorAll('.kp-ask')].find(b=>/statement/.test(b.textContent)).click(),60)"


def interactions(browser, stage_for, problems: list[str]) -> None:
    """The things a screenshot can't show: keyboard, focus, what the cat sends, and what she reacts to."""
    def expect(ok, what):
        if not ok:
            problems.append(f"interaction: {what}")

    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    stage = stage_for(ctx)
    posts: list[str] = []
    ctx.on("request", lambda r: posts.append(r.url) if r.method != "GET" else None)

    # Keyboard: reach her, open her, ask, close, and land back where you were.
    stage.state.update(feed=FEEDS["treasurer"])
    page = ctx.new_page()
    page.goto("http://localhost:7999/me")
    page.wait_for_timeout(300)
    page.evaluate("document.querySelector('nav button[data-t=desk]').click()")
    page.wait_for_timeout(150)
    expect(page.locator("#catdock kitty-cat").get_attribute("mood") == "reviewing", "a waiting review makes her review")
    page.locator(".kc-btn").focus()
    expect(page.evaluate("document.activeElement.classList.contains('kc-btn')"), "the cat's button takes focus")
    page.keyboard.press("Enter")
    page.wait_for_timeout(150)
    expect(page.evaluate("document.getElementById('kittyPanel').open"), "Enter opens the panel")
    expect(page.evaluate("document.activeElement.id==='kittyPanelTitle'"), "focus moves into the panel")
    expect(page.locator(".kc-btn").get_attribute("aria-expanded") == "true", "aria-expanded follows")
    # The open panel must not sit on any button on the page.
    covered = page.evaluate("""(()=>{const k=document.getElementById('kittyPanel').getBoundingClientRect();return [...document.querySelectorAll('main button,main a,main input')].filter(e=>{const r=e.getBoundingClientRect();return r.width&&r.left<k.right&&r.right>k.left&&r.top<k.bottom&&r.bottom>k.top}).map(e=>e.textContent.trim().slice(0,30))})()""")
    expect(not covered, f"the open panel covers {covered}")
    for label in page.locator(".kp-ask").all_inner_texts():
        page.locator(".kp-ask", has_text=label).first.click()
        page.wait_for_timeout(40)
    expect(not posts, f"asking the cat sent something: {posts}")
    # She can point at the review queue, and doing so presses nothing.
    page.locator(".kp-ask", has_text="needs my attention").click()
    page.wait_for_timeout(100)
    expect(page.locator(".review-open.kc-point").count() == 1, "she points at the first payment that needs review")
    expect(not posts, f"pointing sent something: {posts}")
    page.keyboard.press("Escape")
    page.wait_for_timeout(100)
    expect(not page.evaluate("document.getElementById('kittyPanel').open"), "Escape closes the panel")
    expect(page.evaluate("document.activeElement.classList.contains('kc-btn')"), "focus returns to the cat")
    page.close()

    # Hide stays hidden across a reload, and can be undone.
    page = ctx.new_page()
    page.goto("http://localhost:7999/me")
    page.wait_for_timeout(200)
    page.locator(".kc-btn").click()
    page.locator("[data-hide]").click()
    expect(page.locator(".kc-show").is_visible(), "Show Kitty appears once she's hidden")
    page.reload()
    page.wait_for_timeout(200)
    expect(page.locator(".kc-show").is_visible() and not page.locator(".kc-btn").is_visible(), "hidden survives a reload")
    page.locator(".kc-show").click()
    expect(page.locator(".kc-btn").is_visible(), "Show Kitty brings her back")
    page.close()

    # No celebration for what was already true on load. One for what happens while you watch.
    stage.state.update(board=SQUARE)
    page = ctx.new_page()
    page.goto("http://localhost:7999/board")
    page.wait_for_timeout(1000)
    expect(page.locator(".kc-petals").count() == 0, "no petals on a board that was already square")
    expect(page.locator("#roadCat").get_attribute("mood") == "reconciled", "already square rests as reconciled")
    page.close()
    stage.state.update(board=pot_board())
    page = ctx.new_page()
    page.goto("http://localhost:7999/board")
    page.wait_for_timeout(500)
    expect(page.locator("#catdock kitty-cat").get_attribute("mood") == "waiting", "open claims make her wait")
    stage.state.update(board=SQUARE)
    page.wait_for_timeout(1100)
    expect(page.locator(".kc-petals").count() == 1, "petals when the board turns square")
    expect(page.locator("#catdock kitty-cat").get_attribute("mood") == "celebrating", "she celebrates when it turns square")
    expect(page.evaluate("document.body.classList.contains('square')"), "the page turns square")
    page.close()

    # A member's own payment being confirmed gets a coin. A refusal gets ears back.
    stage.state.update(feed=FEEDS["member"])
    page = ctx.new_page()
    page.goto("http://localhost:7999/me")
    page.wait_for_timeout(400)
    page.evaluate("document.querySelector('nav button[data-t=desk]').click()")
    moved = json.loads(json.dumps(FEEDS["member"]))
    next(s for s in moved["desk"]["slips"] if s["id"] == "p5")["state"] = "confirmed"
    stage.state.update(feed=moved)
    page.wait_for_timeout(1200)
    expect(page.locator("#catdock kitty-cat").get_attribute("mood") == "paid", "a confirmation makes her hold up a coin")
    page.close()

    # After a long quiet spell on a calm page, she naps. Touch the page and she's back.
    stage.state.update(trips=["goa"])
    page = ctx.new_page()
    page.clock.install()
    page.goto("http://localhost:7999/")
    page.clock.run_for(3000)
    page.clock.fast_forward(130000)
    page.clock.run_for(200)
    expect(page.locator("#catdock kitty-cat").get_attribute("mood") == "sleeping", "a calm page lets her nap")
    page.mouse.move(40, 40)
    page.mouse.down()
    page.mouse.up()
    page.clock.run_for(200)
    expect(page.locator("#catdock kitty-cat").get_attribute("mood") == "idle", "a touch wakes her")
    page.close()
    ctx.close()

    # A phone: real touch events, and targets big enough for a finger.
    ctx = browser.new_context(viewport={"width": 375, "height": 780}, has_touch=True, is_mobile=True)
    stage = stage_for(ctx)
    stage.state.update(feed=FEEDS["treasurer"])
    posts.clear()
    ctx.on("request", lambda r: posts.append(r.url) if r.method != "GET" else None)
    page = ctx.new_page()
    page.goto("http://localhost:7999/me")
    page.wait_for_timeout(300)
    page.locator("nav button[data-t=desk]").tap()
    page.wait_for_timeout(150)
    small = page.evaluate("""[...document.querySelectorAll('main button,nav button,.kc-btn,main summary,main input,main select')].filter(e=>{const r=e.getBoundingClientRect();return r.width&&e.type!=='checkbox'&&r.height<44}).map(e=>(e.textContent||e.name).trim().slice(0,24)+' '+Math.round(e.getBoundingClientRect().height))""")
    expect(not small, f"touch targets under 44px: {small}")
    page.locator(".kc-btn").tap()
    page.wait_for_timeout(200)
    expect(page.evaluate("document.getElementById('kittyPanel').matches(':modal')"), "on a phone she opens as a sheet")
    rect = page.evaluate("(()=>{const r=document.getElementById('kittyPanel').getBoundingClientRect();return [r.left,r.right,r.bottom]})()")
    expect(rect[0] == 0 and rect[1] == 375 and abs(rect[2] - 780) < 2, f"the sheet sits on the bottom edge: {rect}")
    page.locator(".kp-ask", has_text="unmatched").tap()
    page.wait_for_timeout(100)
    expect(page.locator(".kp-answer p").count() > 0, "tapping a question answers it")
    page.locator(".kp-x").tap()
    page.wait_for_timeout(100)
    expect(not page.evaluate("document.getElementById('kittyPanel').open"), "the close button works by touch")
    page.locator(".kc-btn").tap()
    page.wait_for_timeout(150)
    page.locator(".kp-ask", has_text="needs my attention").tap()
    page.wait_for_timeout(150)
    expect(not page.evaluate("document.getElementById('kittyPanel').open"), "pointing at something closes the sheet, so you can see it")
    expect(page.locator(".review-open.kc-point").count() == 1, "and the thing is pointed at")
    expect(not posts, f"tapping the cat sent something: {posts}")
    page.close()
    ctx.close()

    # With reduced motion nothing moves, and she still shows the right mood.
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    stage = stage_for(ctx)
    stage.state.update(board=pot_board())
    page = ctx.new_page()
    page.goto("http://localhost:7999/board")
    page.wait_for_timeout(400)
    running = page.evaluate("document.getAnimations().filter(a=>a.playState==='running'&&a.effect.getTiming().duration>50).length")
    expect(running == 0, f"{running} animations still run under reduced motion")
    expect(page.locator("#catdock kitty-cat").get_attribute("mood") == "waiting", "reduced motion keeps her mood")
    stage.state.update(board=SQUARE)
    page.wait_for_timeout(1100)
    expect(page.locator(".kc-petals").count() == 0, "no petals under reduced motion")
    page.close()
    ctx.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--axe")
    ap.add_argument("--only")
    ap.add_argument("--sizes", default="375,768,1440")
    ap.add_argument("--dark", action="store_true")
    ap.add_argument("--no-shots", action="store_true")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    axe = Path(args.axe).read_text() if args.axe else None
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for size in args.sizes.split(","):
            w, h = SIZES[size]
            ctx = browser.new_context(viewport={"width": w, "height": h}, color_scheme="dark" if args.dark else "light", reduced_motion="reduce")
            stage = Stage(ctx)
            for name, url, state, js in scenes(stage):
                if args.only and not re.search(args.only, name):
                    continue
                stage.state.update(state)
                page = ctx.new_page()
                errors: list[str] = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "500" not in m.text and "404" not in m.text else None)
                page.goto("http://localhost:7999" + url)
                page.wait_for_timeout(260)
                if js:
                    page.evaluate(js)
                    page.wait_for_timeout(240)
                wide = page.evaluate("document.documentElement.scrollWidth-document.documentElement.clientWidth")
                inner = page.evaluate("(()=>{const m=document.querySelector('main');return m?m.scrollWidth-m.clientWidth:0})()")
                if wide > 0 or inner > 0:
                    problems.append(f"{size} {name}: {max(wide, inner)}px too wide")
                for e in errors:
                    problems.append(f"{size} {name}: console: {e}")
                if axe:
                    page.evaluate(axe)
                    found = page.evaluate("axe.run(document,{resultTypes:['violations']}).then(r=>r.violations.filter(v=>['serious','critical'].includes(v.impact)).map(v=>v.id+': '+v.nodes.slice(0,2).map(n=>n.target.join(' ')+' '+(n.any[0]&&n.any[0].message||'')).join(' | ')))")
                    for f in found:
                        problems.append(f"{size} {name}: axe {f}")
                if not args.no_shots:
                    page.screenshot(path=str(out / f"{name}-{size}{'-dark' if args.dark else ''}.png"), full_page=size != "375")
                page.close()
            ctx.close()
        if not args.only:
            interactions(browser, Stage, problems)
        browser.close()
    print(f"{len(problems)} problems")
    for line in problems:
        print(" ", line)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
