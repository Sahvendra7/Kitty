"""The six-person trip, end to end, through the same HTTP routes the pages use.

Needs the home app running: uv run python -m kitty.home_app

Nothing here touches Mattermost, Switch or a state file. It creates the trip in the home app, opens each
person's invite with their own cookie jar, uploads their statement, and answers their agent, so the
whole product path is exercised. The people are scripted, and are labelled as such wherever this output
is shown. `scripts/verify.py` then checks the result on its own, from Switch's record.

    uv run python scripts/six_person.py [--leave-open]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from kitty import ledger as L  # noqa: E402

DATA = ROOT / "data/synthetic/six"
TRIP = json.loads((DATA / "trip.json").read_text())
HOME = os.environ.get("KITTY_HOME_URL", "http://127.0.0.1:7000")
EXPECTED = {"Asha": 976666, "Bilal": 626666, "Chitra": -68333, "Dev": -483333, "Esha": -208333, "Farhan": -843333}
EVERYONE = {"Asha", "Bilal", "Chitra", "Dev", "Esha", "Farhan"}
# What each line really was, by amount. None means it's personal. Written the way people talk.
TRUTH = {
    "₹24,000": (EVERYONE, "Yes, all six of us stayed there."),
    "₹12,500": (EVERYONE, "Yes, all six of us used the car."),
    "₹9,000": (EVERYONE - {"Dev"}, "Yes, but not Dev. Everyone else went rafting."),
    "₹1,100": ({"Chitra", "Esha"}, "Yes, but not Asha or Bilal or Dev or Farhan. Just Esha and me."),
    "₹3,600": (EVERYONE, "Yes, groceries for all six of us."),
    "₹6,300": (EVERYONE, "Yes, dinner for all six of us."),
    "₹2,400": (EVERYONE, "Yes, petrol for all six of us."),
    "₹1,800": (EVERYONE, "Yes, all six of us did the tour."),
    "₹1,299": (None, "That one was just me, personal."),
    "₹119": (None, "That one was just me, personal."),
    "₹380": (None, "That one was just me, personal."),
    "₹2,499": (None, "That one was just me, personal."),
    "₹299": (None, "That one was just me, personal."),
    "₹180": (None, "That one was just me, personal."),
}
SECRET_MARKS = ("Kt1!", "api_key", "password", "registration", '"agent_id"', "private_room", "desk_room", "sender", "statement.csv")


def log(text: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {text}", flush=True)


class Shots:
    """Stills of the real pages during a real run, in a throwaway headless Chrome. It never records video."""

    def __init__(self, out: str | None) -> None:
        self.out = Path(out) if out else None
        self.browser = None

    def take(self, name: str, url: str, width: int = 1440, height: int = 900, person: "Person | None" = None, js: str = "", settle: float = 1.5) -> None:
        if not self.out:
            return
        if self.browser is None:
            sys.path.insert(0, str(ROOT / "scripts"))
            from capture import Browser

            self.browser = Browser(1440, 900)
        b = self.browser
        b.send("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=2 if width < 600 else 1, mobile=False)
        b.send("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": "light"}])  # So stills don't depend on this laptop's setting.
        if person is not None:
            for key, value in person.http.cookies.items():
                b.send("Network.setCookie", name=key, value=value, domain="127.0.0.1", path="/", httpOnly=True, sameSite="Strict")
        b.goto(url, settle=settle)
        if js:
            b.send("Runtime.evaluate", expression=f"(async()=>{{{js}}})()", awaitPromise=True)
            time.sleep(settle)
        b.shot(self.out / f"{name}.png")

    def phone(self, name: str, person: "Person", tab: str = "", js: str = "", height: int = 860) -> None:
        pick = f"document.querySelector('nav button[data-t={tab}]').click();await new Promise(r=>setTimeout(r,400));" if tab else ""
        self.take(name, str(person.http.base_url), 430, height, person, pick + js)

    def close(self) -> None:
        if self.browser:
            self.browser.close()


LOST: list[str] = []  # Messages a person had to send again because nothing came back. Reported, not hidden.


class Person:
    def __init__(self, name: str, port: int, invite: str) -> None:
        self.name = name
        self.slug = name.lower()
        self.http = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30)
        self.invite = invite
        self.corrected: dict[str, int] = {}
        self.dropped_dinner = False

    def join(self) -> None:
        r = self.http.get(self.invite, follow_redirects=False)
        assert r.status_code == 303, f"{self.name}: invite returned {r.status_code}"

    def upload(self, text: str, name: str = "statement.csv") -> httpx.Response:
        return self.http.post("/api/statement", content=text.encode(), headers={"content-type": "text/csv", "x-filename": name, "x-kitty-synthetic": "yes"})

    def feed(self) -> dict:
        r = self.http.get("/api/feed")
        r.raise_for_status()
        return r.json()

    def say(self, room: str, text: str) -> None:
        log(f"  {self.name:6} in {room:7}: {text}")
        self.http.post("/api/say", json={"room": room, "text": text}).raise_for_status()

    def act(self, room: str, text: str, what: str, test, retry_after: float = 25, seconds: float = 180):
        """Do something and wait to see it happen. If nothing comes back, do what a person would: press it again."""
        self.say(room, text)
        end = time.time() + seconds
        again = time.time() + retry_after
        while time.time() < end:
            value = test(self.feed())
            if value:
                return value
            if time.time() > again:
                LOST.append(f"{self.name}: {text}")
                log(f"  nothing came back, so {self.name} sends it again")
                self.say(room, text)
                again = time.time() + retry_after
            time.sleep(0.7)
        raise TimeoutError(f"{self.name}: never saw {what}")

    def slips(self) -> list[dict]:
        return self.feed()["desk"]["slips"]

    def wait(self, what: str, test, seconds: float = 240):
        end = time.time() + seconds
        while time.time() < end:
            value = test(self.feed())
            if value:
                return value
            time.sleep(0.7)
        raise TimeoutError(f"{self.name}: never saw {what}")

    def answer_questions(self, seconds: float = 480) -> None:
        end = time.time() + seconds
        tries: dict[str, int] = {}
        while time.time() < end:
            private = self.feed()["private"]
            last = private[-1] if private else None
            if last and not last["mine"]:
                if "That's everything for now" in last["text"]:
                    return
                if "Was this shared with the group?" in last["text"]:
                    amount = re.search(r"₹[\d,]+(?:\.\d{2})?", last["text"]).group(0).removesuffix(".00")
                    if amount not in TRUTH:
                        raise RuntimeError(f"{self.name}: no scripted answer for {amount}")
                    tries[amount] = tries.get(amount, 0) + 1
                    if tries[amount] > 3:
                        raise RuntimeError(f"{self.name}: the agent keeps asking about {amount}")
                    answer = "personal" if amount == "₹6,300" and self.dropped_dinner else TRUTH[amount][1]
                    if last["text"].startswith("I didn't catch that"):
                        # The agent didn't follow, and it says which words it knows. A person would use them.
                        answer = "personal" if TRUTH[amount][0] is None or (amount == "₹6,300" and self.dropped_dinner) else "shared " + " ".join(n.lower() for n in sorted(TRUTH[amount][0]))
                    self.say("private", answer)
                    time.sleep(0.5)
            time.sleep(0.7)
        raise TimeoutError(f"{self.name}: the agent never finished")

    def fix_desk(self) -> None:
        """A real person reads each chit before releasing it, and drops what's wrong. Asked again, they say what's true."""
        for _ in range(6):
            wrong = []
            for slip in self.slips():
                if slip["kind"] != "expense" or slip["state"] != "waiting":
                    continue
                amount = slip["amount"].removesuffix(".00")
                truth = TRUTH.get(amount, (None, ""))[0]
                if truth is None or {s["name"] for s in slip["shares"]} != truth:
                    wrong.append(slip["id"])
            if not wrong:
                return
            for cid in wrong:
                log(f"  {self.name}'s chit {cid} is wrong, so they drop it")
                self.say("desk", f"drop {cid}")
                self.corrected[cid] = 1
                time.sleep(1.5)
            self.answer_questions()
        raise RuntimeError(f"{self.name}: chits never matched")

    def release_expenses(self) -> None:
        for slip in self.slips():
            if slip["kind"] == "expense" and slip["state"] == "waiting":
                self.act("desk", f"release {slip['id']}", f"{slip['id']} released", lambda d, i=slip["id"]: any(s["id"] == i and s["state"] != "waiting" for s in d["desk"]["slips"]))


def board(url: str) -> dict:
    return httpx.get(url + "api/board", timeout=10).json()


def net(url: str) -> dict:
    return {b["name"]: b["paise"] for b in board(url)["balances"]}


def check(ok: bool, text: str) -> None:
    log(f"{'PASS' if ok else 'FAIL'}  {text}")
    if not ok:
        raise AssertionError(text)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--leave-unsettled", action="store_true", help="Stop before the final payments, to look around.")
    ap.add_argument("--restart-midway", action="store_true", help="Stop every process mid-trip, send a release while they're down, then start them again.")
    ap.add_argument("--shots", help="Save stills of the pages to this folder, for the README and blog.")
    args = ap.parse_args()
    shots = Shots(args.shots)
    home = httpx.Client(base_url=HOME, timeout=30)
    began = time.time()
    log("Scripted people, through the same routes the pages use.")
    shots.take("app-home", HOME + "/")
    fill = "const set=(n,v)=>{const i=document.querySelector(`#tripForm [name=${n}]`);i.value=v;i.dispatchEvent(new Event('input',{bubbles:true}))};" + "".join(f"set('{k}','{TRIP[k]}');" for k in ("name", "start", "end"))
    shots.take("app-new-trip", HOME + "/trips/new", js=fill)
    trip = home.post("/api/trips", json={k: TRIP[k] for k in ("name", "start", "end", "currency")}).json()
    tid = trip["id"]
    log(f"trip {tid}")
    for p in TRIP["people"]:
        r = home.post(f"/api/trips/{tid}/people", json=p)
        assert r.status_code == 201, r.text
    shots.take("app-add-people", f"{HOME}/trips/{tid}")
    shots.take("app-ready-to-open", f"{HOME}/trips/{tid}", js="document.getElementById('toReview').click();")
    assert home.post(f"/api/trips/{tid}/open").status_code == 202
    time.sleep(12)
    shots.take("app-setting-up", f"{HOME}/trips/{tid}")
    while True:
        detail = home.get(f"/api/trips/{tid}").json()
        if detail["state"] in ("live", "failed") and not detail["running"]:
            break
        time.sleep(2)
    check(detail["state"] == "live", f"six people provisioned in {time.time() - began:.0f}s")
    blob = json.dumps(detail)
    check(not [m for m in SECRET_MARKS if m in blob], "the organiser's page gets no credentials, room ids or sender ids")
    folder = ROOT / "trips" / tid
    board_url = detail["board_url"]
    people = {p["name"]: Person(p["name"], int(re.search(r":(\d+)/", p["invite_url"]).group(1)), p["invite_url"][p["invite_url"].index("/join"):]) for p in detail["people"]}

    shots.take("app-trip-open", f"{HOME}/trips/{tid}")
    # Isolation at the app's door.
    for p in people.values():
        check(p.http.get("/api/feed").status_code == 401, f"{p.name}'s app refuses anyone without their invite")
    asha, bilal = people["Asha"], people["Bilal"]
    for p in people.values():
        p.join()
    stranger = httpx.Client(base_url=str(asha.http.base_url), timeout=10)
    check(stranger.get(asha.invite).status_code == 403, "an invite works only once")
    borrowed = httpx.Client(base_url=str(bilal.http.base_url), cookies=dict(asha.http.cookies), timeout=10)
    check(borrowed.get("/api/feed").status_code == 401, "Asha's session doesn't open Bilal's app")

    # Statements.
    shots.phone("app-statement-add", asha)
    bad_upload = "const a=document.getElementById('attest');a.click();const f=document.getElementById('file');const dt=new DataTransfer();dt.items.add(new File(['" + "txn_date,txn_time,narration,ref,debit,credit,balance,account\\n2026-13-16,10:00,SOME SHOP,C1,10.00,,,1234567812345678\\n2026-10-16,99:00,SOME CAFE,C2,1.00,2.00,,XXXXXX1234\\n" + "'],'mine.csv',{type:'text/csv'}));f.files=dt.files;f.dispatchEvent(new Event('change',{bubbles:true}));await new Promise(r=>setTimeout(r,800));"
    shots.phone("app-statement-errors", asha, js=bad_upload)
    bad = (DATA / "asha.csv").read_text().replace("XXXXXX2101", "1234567812345678", 1)
    r = asha.upload(bad)
    check(r.status_code == 422 and "1234567812345678" not in r.text, "a bad file is refused, and its content isn't echoed back")
    for p in people.values():
        r = p.upload((DATA / f"{p.slug}.csv").read_text())
        check(r.status_code == 200 and r.json()["status"] == "accepted", f"{p.name}'s statement accepted: {r.json().get('summary')}")
    r = asha.upload((DATA / "asha.csv").read_text())
    check(r.json()["status"] == "duplicate", "the same file twice is noticed")
    shots.phone("app-statement-ready", people["Chitra"], js="const d=document.getElementById('pv');d.open=true;await new Promise(r=>setTimeout(r,900));document.getElementById('pv').scrollIntoView();", height=1000)
    detail = home.get(f"/api/trips/{tid}").json()
    check(all(p["joined"] and p["statement_added"] for p in detail["people"]), "the organiser sees all six joined, with statements added")

    # Their agents go through it.
    for p in people.values():
        p.say("private", "start")
    for p in people.values():
        # The model can be slow, so this allows it a long time.
        reply = p.wait("the agent's reply", lambda d: next((m["text"] for m in d["private"] if not m["mine"] and (m["text"].startswith("I read ") or "can't reach the model" in m["text"] or "went wrong" in m["text"])), None), 420)
        if not reply.startswith("I read "):
            raise RuntimeError(f"{p.name}'s agent couldn't reach the model. Is the account within its usage limit?")
    if args.shots:
        people["Chitra"].wait("a question", lambda d: d["private"] and "Was this shared" in d["private"][-1]["text"], 60)
        shots.phone("app-agent-asks", people["Chitra"], "private")
    for p in people.values():
        p.answer_questions()
    for p in people.values():
        p.fix_desk()
    shots.phone("app-desk", people["Esha"], "desk")
    corrected = sum(len(p.corrected) for p in people.values())
    log(f"chits people had to correct: {corrected}")

    # Independent approvals. Esha first, so that Farhan's claim on the same dinner meets hers.
    for name in ("Asha", "Bilal", "Chitra", "Dev", "Esha", "Farhan"):
        people[name].release_expenses()
    farhan = people["Farhan"]
    check(any("Possible conflict" in f.get("text", "") for f in farhan.wait("the conflict", lambda d: [x for x in d["group"] if "Possible conflict" in x.get("text", "")])), "the same dinner from two people is flagged in the group")
    dinner = next(s for s in farhan.slips() if s["kind"] == "expense" and s["amount"] == "₹6,300.00")
    check(dinner["state"] == "conflict", "Farhan's chit shows the conflict")
    check(board(board_url)["held_count"] >= 0 and "Esha" in {e["payer"] for e in board(board_url)["expenses"] if e["status"] == "conflict_hold"}, "the board holds both until one is dropped")
    shots.phone("app-conflict", farhan, "desk")
    shots.phone("app-group-conflict", farhan, "group")
    farhan.dropped_dinner = True
    farhan.act("desk", f"drop {dinner['id']}", "conflict cleared", lambda d: any("Conflict cleared" in x.get("text", "") for x in d["group"]))
    farhan.answer_questions()

    # A payment both sides can see.
    tb = next(s["id"] for s in bilal.slips() if s["kind"] == "transfer")
    if args.restart_midway:
        from kitty import runtime
        from kitty.switch_client import Mattermost

        before = net(board_url)
        runtime.stop(folder)
        time.sleep(3)
        log("every process stopped. Bilal releases a payment while they're down, through his own chat account.")
        login = json.loads((folder / "state/bilal.json").read_text())["mattermost"]
        Mattermost(login["username"], login["password"]).post(login["channels"]["desk"], f"release {tb}")
        time.sleep(3)
        check(home.post(f"/api/trips/{tid}/restart", timeout=120).status_code == 200, "the home app starts the trip again")
        check(net(board_url) == before, "balances are the same after the restart")
        check(not home.get(f"/api/trips/{tid}").json()["editable"], "the roster stays fixed")
    else:
        bilal.act("desk", f"release {tb}", "the release", lambda d: any(s["id"] == tb and s["state"] != "waiting" for s in d["desk"]["slips"] if s["kind"] == "transfer"))
    answer = asha.wait("the question", lambda d: next((s for s in d["desk"]["slips"] if s["kind"] == "answer" and s["id"] == tb), None))
    check(answer["received"] is True, "Asha's agent finds Bilal's payment on her side")
    asha.act("desk", f"release {tb}", "confirmation", lambda d: any(f"{tb} confirmed by both sides" in x.get("text", "") for x in d["group"]))

    # A payment that failed and came back.
    dev, chitra = people["Dev"], people["Chitra"]
    td = next(s["id"] for s in dev.slips() if s["kind"] == "transfer")
    dev.act("desk", f"release {td}", "the release", lambda d: any(s["id"] == td and s["state"] != "waiting" for s in d["desk"]["slips"] if s["kind"] == "transfer"))
    answer = chitra.wait("the question", lambda d: next((s for s in d["desk"]["slips"] if s["kind"] == "answer" and s["id"] == td), None))
    check(answer["received"] is False, "Chitra's agent finds nothing, because it never arrived")
    chitra.act("desk", f"release {td}", "the dispute", lambda d: any(f"{td} NOT CONFIRMED" in x.get("text", "") for x in d["group"]))
    dev.wait("the reversal", lambda d: any("failed and was reversed" in m["text"] for m in d["private"]))
    shots.take("app-board-dispute", board_url)
    shots.phone("app-reversal", dev, "private")
    group = dev.feed()["group"]
    check(not any("reversed" in x.get("text", "").lower() and "CANARY" in x.get("text", "") for x in group), "the group hears nothing about Dev's reversal")
    dev.act("desk", f"withdraw {td} payment_failed", "the withdrawal", lambda d: any(f"{td} withdrawn by" in x.get("text", "") for x in d["group"]))
    time.sleep(1)
    now = net(board_url)
    log(f"balances: {now}")
    check(now == EXPECTED, "balances match the hand-worked numbers exactly")
    chitra.act("group", "balances", "balances", lambda d: any(x.get("text", "").startswith("| Person") for x in d["group"]))
    chitra.act("group", "settle", "settle", lambda d: any(x.get("text", "").startswith("Suggested payments") for x in d["group"]))
    shots.take("app-board-balances", board_url, height=1100)
    shots.take("app-trip-settling", f"{HOME}/trips/{tid}", height=1000)
    shots.take("app-trip-phone", f"{HOME}/trips/{tid}", 430, 1000)
    if args.leave_unsettled:
        log(f"left unsettled. Trip folder: {folder}")
        print(f"TRIP_DIR={folder}")
        return 0

    # The suggested payments land on later statements, and arrive through upload.
    plan = board(board_url)["plan"]
    log(f"payments to make: {len(plan)}")
    statement_of = {p["name"]: p["statement_name"] for p in TRIP["people"]}
    texts = {p.name: (DATA / f"{p.slug}.csv").read_text() for p in people.values()}
    for i, pay in enumerate(plan):
        payer, payee = people[pay["from"]], people[pay["to"]]
        amount = f"{pay['paise'] // 100}.{pay['paise'] % 100:02d}"
        ref = f"U88{i:03d}"
        texts[payer.name] += f"2026-10-19,20:{i:02d},UPI/DR/{statement_of[payee.name]}/88{i:03d}/GPAY,{ref},{amount},,,XXXXXX0000\n"
        texts[payee.name] += f"2026-10-19,20:{i:02d},UPI/CR/{statement_of[payer.name]}/88{i:03d}/GPAY,{ref},,{amount},,XXXXXX0000\n"
        for who in (payer, payee):
            r = who.upload(texts[who.name])
            assert r.status_code == 200 and r.json()["status"] == "added_lines", r.text
        known = {s["id"] for s in payer.slips()}
        fresh = payer.act("private", "rescan", "the new chit", lambda d: [s["id"] for s in d["desk"]["slips"] if s["id"] not in known and s["kind"] == "transfer"])[0]
        payer.act("desk", f"release {fresh}", "the release", lambda d, i=fresh: any(s["id"] == i and s["state"] != "waiting" for s in d["desk"]["slips"] if s["kind"] == "transfer"))
        answer = payee.wait("the question", lambda d: next((s for s in d["desk"]["slips"] if s["kind"] == "answer" and s["id"] == fresh and s["state"] == "waiting"), None))
        check(answer["received"] is True, f"{payee.name}'s agent finds {payer.name}'s {answer['amount']}")
        payee.act("desk", f"release {fresh}", "confirmation", lambda d, i=fresh: any(f"{i} confirmed by both sides" in x.get("text", "") for x in d["group"]))
    asha.wait("the end", lambda d: any("Everyone's square" in x.get("text", "") for x in d["group"]))
    final = board(board_url)
    check(all(b["paise"] == 0 for b in final["balances"]) and L.settle({b["name"]: b["paise"] for b in final["balances"]}) == [], "everyone's square")

    shots.take("app-board-square", board_url, height=1100)
    shots.close()

    # The same words, looked for where they should never be.
    words = set()
    for p in people.values():
        for line in (DATA / f"{p.slug}.csv").read_text().splitlines()[1:]:
            f = line.split(",")
            words.update(w for w in re.findall(r"[A-Z0-9-]{5,}", f[2]) if not w.isdigit() and w not in ("SYNTHETIC", "ORDER", "PHONEPE", "TRANSFER", "INDIA", "CHITRA", "BILAL", "FARHAN"))
    for p in people.values():
        feed = p.feed()
        others = {q.slug for q in people.values()} - {p.slug}
        group_blob = json.dumps(feed["group"]) + json.dumps(feed["desk"])
        hits = sorted(w for w in words if w in group_blob)
        check(not hits, f"{p.name}'s group feed and desk carry no statement words ({len(words)} checked)")
    log_hits = {}
    for f in (folder / "logs").glob("*.log"):
        text = f.read_text(errors="ignore")
        hits = sorted(w for w in words if w in text)
        if hits:
            log_hits[f.name] = hits[:5]
    check(not log_hits, f"no statement words in any process log {log_hits or ''}")
    check(any(w in (folder / "people/asha/statement.csv").read_text() for w in words), "positive control: those same words are in Asha's own statement")

    log(f"messages people had to resend: {len(LOST)} {LOST or ''}")
    log(f"story finished in {time.time() - began:.0f}s. Now the independent check.")
    print(f"TRIP_DIR={folder}", flush=True)
    return subprocess.run([sys.executable, str(ROOT / "scripts/verify.py")], env={**os.environ, "KITTY_TRIP_DIR": str(folder), "PYTHONPATH": str(ROOT)}, cwd=ROOT).returncode


if __name__ == "__main__":
    raise SystemExit(main())
