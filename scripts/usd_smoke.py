"""A two-person trip in dollars, end to end, with the home app running. Checks that nothing assumes rupees."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
from six_person import HOME, Person, check  # noqa: E402

H = "txn_date,txn_time,narration,ref,debit,credit,balance,account\n"
ANA = H + "2026-10-16,19:30,HARBOR GRILL GROUP DINNER FOR TWO,C1,120.50,,,XXXXXX1111\n"
BEN = H


def main() -> int:
    home = httpx.Client(base_url=HOME, timeout=30)
    tid = home.post("/api/trips", json={"name": "Dollar test", "start": "2026-10-16", "end": "2026-10-18", "currency": "USD"}).json()["id"]
    for name, statement in (("Ana", "ANA L"), ("Ben", "BEN T")):
        assert home.post(f"/api/trips/{tid}/people", json={"name": name, "statement_name": statement}).status_code == 201
    home.post(f"/api/trips/{tid}/open")
    while True:
        d = home.get(f"/api/trips/{tid}").json()
        if d["state"] in ("live", "failed") and not d["running"]:
            break
        time.sleep(1)
    check(d["state"] == "live", "two people provisioned")
    ana, ben = (Person(p["name"], int(re.search(r":(\d+)/", p["invite_url"]).group(1)), p["invite_url"][p["invite_url"].index("/join"):]) for p in d["people"])
    for p, text in ((ana, ANA), (ben, BEN + "2026-10-16,10:00,CITY MUSEUM TICKET SOLO,C2,20.00,,,XXXXXX2222\n")):
        p.join()
        check(p.upload(text).json()["status"] == "accepted", f"{p.name}'s statement accepted")
    preview = ana.http.get("/api/statement/model-preview").json()["lines"]
    check("amount_usd" in preview[0] and "amount_rupees" not in preview[0], "the model's line says dollars, not rupees")
    ana.say("private", "start")
    end = time.time() + 120
    while time.time() < end:
        feed = ana.feed()
        if any(s["kind"] == "expense" for s in feed["desk"]["slips"]):
            break
        last = feed["private"][-1]
        if not last["mine"] and "Was this shared with the group?" in last["text"]:
            ana.say("private", "shared")
        time.sleep(1)
    slip = next(s for s in ana.feed()["desk"]["slips"] if s["kind"] == "expense")
    check(slip["amount"] == "$120.50" and {s["amount"] for s in slip["shares"]} == {"$60.25"}, f"the chit reads in dollars: {slip['amount']}")
    ana.act("desk", f"release {slip['id']}", "the release", lambda f: any(s["id"] == slip["id"] and s["state"] != "waiting" for s in f["desk"]["slips"]))
    board_url = home.get(f"/api/trips/{tid}").json()["board_url"]
    time.sleep(1)
    board = httpx.get(board_url + "api/board", timeout=10).json()
    check(board["trip"]["currency"] == "USD" and {b["name"]: b["paise"] for b in board["balances"]} == {"Ana": 6025, "Ben": -6025}, "balances are right in cents")
    ben.upload(BEN + "2026-10-16,10:00,CITY MUSEUM TICKET SOLO,C2,20.00,,,XXXXXX2222\n2026-10-17,09:00,UPI/DR/ANA L/9/GPAY,U9,60.25,,,XXXXXX2222\n")
    ana.upload(ANA + "2026-10-17,09:00,UPI/CR/BEN T/9/GPAY,U9,,60.25,,XXXXXX1111\n")
    known = {s["id"] for s in ben.slips()}
    ben.say("private", "start")
    fresh = ben.act("private", "rescan", "the chit", lambda f: [s["id"] for s in f["desk"]["slips"] if s["id"] not in known and s["kind"] == "transfer"], retry_after=40)[0]
    ben.act("desk", f"release {fresh}", "the release", lambda f: any(s["id"] == fresh and s["state"] != "waiting" for s in f["desk"]["slips"] if s["kind"] == "transfer"))
    ana.wait("the question", lambda f: any(s["kind"] == "answer" for s in f["desk"]["slips"]))
    ana.act("desk", f"release {fresh}", "the end", lambda f: any("Everyone's square" in x.get("text", "") for x in f["group"]))
    check(True, "everyone's square, in dollars")
    folder = ROOT / "trips" / tid
    code = subprocess.run([sys.executable, str(ROOT / "scripts/verify.py")], env={**os.environ, "KITTY_TRIP_DIR": str(folder), "PYTHONPATH": str(ROOT)}, cwd=ROOT, capture_output=True, text=True)
    print("\n".join(line for line in code.stdout.splitlines() if line.startswith("FAIL") or "checks passed" in line))
    from kitty import runtime

    runtime.stop(folder)
    return code.returncode


if __name__ == "__main__":
    raise SystemExit(main())
