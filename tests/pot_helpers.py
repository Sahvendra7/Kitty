"""A clerk with a Pot trip attached, driven the way the live rooms drive it. Shared by the Pot tests."""
from __future__ import annotations

import json
from datetime import date

from kitty import claims as C
from kitty import pot as P
from kitty.clerk import Clerk, Person

PEOPLE = ("asha", "rohan", "meera", "dev")
TRIP = C.Trip("goa-1", date(2026, 10, 16), date(2026, 10, 19), PEOPLE)
CFG = P.PotConfig("asha", date(2026, 9, 25))
GROUP = "room-group"
TOKEN_A, TOKEN_B = "a" * 32, "b" * 32
WHEN = "2026-10-09T08:32:00+00:00"  # 14:02 IST


def make() -> Clerk:
    people = {p: Person(p, p.title(), f"@human-{p}", f"@agent-{p}", f"desk-{p}") for p in PEOPLE}
    return Clerk(TRIP, people, GROUP, now=lambda: WHEN, pot=P.PotBook(CFG))


def agent(clerk, who, body):
    return clerk.handle(f"desk-{who}", f"@agent-{who}", body)


def human(clerk, who, body):
    return clerk.handle(f"desk-{who}", f"@human-{who}", body)


def group(clerk, who, body):
    return clerk.handle(GROUP, f"@human-{who}", body)


def in_group(posts) -> list[str]:
    return [p.text for p in posts if p.room_id == GROUP]


def on_desk(posts, who) -> list[str]:
    return [p.text for p in posts if p.room_id == f"desk-{who}"]


REFS = iter(range(1, 10**6))


def claim(who="rohan", amount=500000, day="2026-10-09", ref_token=None, ref_last4=None, to="asha") -> str:
    return C.PREFIX + json.dumps({"v": 1, "type": "contribution", "ref": f"r{next(REFS)}", "trip_id": "goa-1", "owner_id": who, "to_id": to, "date": day, "amount_paise": amount, "currency": "INR", "ref_token": ref_token, "ref_last4": ref_last4})


def match(pid, result="exact", credit=TOKEN_A) -> str:
    return C.PREFIX + json.dumps({"v": 1, "type": "pot_match", "ref": f"a{next(REFS)}", "trip_id": "goa-1", "owner_id": "asha", "contribution_id": pid, "result": result, "credit_token": credit if result == "exact" else None})


def transfer(frm="asha", to="rohan", amount=100000, day="2026-10-18") -> str:
    return C.PREFIX + json.dumps({"v": 1, "type": "transfer", "ref": f"t{next(REFS)}", "trip_id": "goa-1", "owner_id": frm, "from_id": frm, "to_id": to, "date": day, "amount_paise": amount, "currency": "INR"})


def expense(who="asha", amount=400000, day="2026-10-17") -> str:
    shares = C.even_shares(amount, list(PEOPLE))
    return C.PREFIX + json.dumps({"v": 1, "type": "expense", "ref": f"e{next(REFS)}", "trip_id": "goa-1", "owner_id": who, "payer_id": who, "date": day, "amount_paise": amount, "currency": "INR", "category": "stay", "participant_ids": list(shares), "shares": shares})


def held(clerk, who="rohan", **kw) -> str:
    before = set(clerk.pot.records)
    agent(clerk, who, claim(who, **kw))
    return (set(clerk.pot.records) - before).pop()


def submitted(clerk, who="rohan", **kw) -> str:
    pid = held(clerk, who, **kw)
    human(clerk, who, f"release {pid}")
    return pid


def review(clerk, who="rohan", result="exact", credit=TOKEN_A, **kw) -> str:
    pid = submitted(clerk, who, **kw)
    agent(clerk, "asha", match(pid, result, credit))
    return pid


def confirmed(clerk, who="rohan", credit=TOKEN_A, **kw) -> str:
    pid = review(clerk, who, "exact", credit, **kw)
    human(clerk, "asha", f"confirm {pid}")
    return pid
