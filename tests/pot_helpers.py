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


# ---- the same rooms the live server enforces, with real agents ----------------------------------------


STATEMENT_HEAD = "txn_date,txn_time,narration,ref,debit,credit,balance,account\n"
ASHA_CSV = STATEMENT_HEAD + """2026-10-01,09:00,SALARY NEFT SYNTHETIC CANARY-SALARY-ASHA,N200101,,85000.00,132000.00,XXXXXX2101
2026-10-05,18:00,UPI/CR/NEHACANARY K/700001/GPAY,U700001,,5000.00,137000.00,XXXXXX2101
2026-10-09,14:02,UPI/CR/ROHAN K/441001/GPAY,U441001,,5000.00,142000.00,XXXXXX2101
2026-10-12,10:30,RENT LANDLORD CANARY-RENT-ASHA,N200301,30000.00,,112000.00,XXXXXX2101
2026-10-17,20:00,NETFLIX CANARY-NETFLIX-ASHA,C200202,649.00,,111351.00,XXXXXX2101
"""
ROHAN_CSV = STATEMENT_HEAD + """2026-10-09,14:02,UPI/DR/ASHA M/441001/GPAY,U441001,5000.00,,20000.00,XXXXXX3301
2026-10-17,12:00,UPI/DR/MEERA S/441002/GPAY,U441002,700.00,,19300.00,XXXXXX3301
"""
DIRECTORY = {"ASHA M": "asha", "ROHAN K": "rohan", "MEERA S": "meera", "DEV P": "dev"}
NAMES = {p: p.title() for p in PEOPLE}
KEY = b"test-pot-key"


def rows_from(text: str, tmp_path, name: str = "s.csv"):
    from kitty import statements as S

    path = tmp_path / name
    path.write_text(text)
    return S.parse(path)


class PotWorld:
    """Rooms as the server enforces them. A message reaches a clerk or an agent only if it's in that room."""

    def __init__(self, statements: dict[str, list] | None = None) -> None:
        from kitty.agent import RulesBrain
        from kitty.pot_agent import PotAgent

        self.clerk = make()
        self.log: dict[str, list[str]] = {}
        self.brain_calls: list = []
        world = self

        class Recording(RulesBrain):
            def classify(self, rows, owner_name, trip_name):
                world.brain_calls.append(("classify", rows))
                return super().classify(rows, owner_name, trip_name)

            def read_answer(self, question, answer, people):
                world.brain_calls.append(("read_answer", question))
                return super().read_answer(question, answer, people)

        self.agents = {
            p: PotAgent(p, TRIP, NAMES, DIRECTORY, (statements or {}).get(p, []), Recording(), f"@human-{p}", "@clerk", "Goa", cfg=CFG, pot_key=KEY)
            for p in PEOPLE
        }

    def send(self, room: str, sender: str, text: str) -> None:
        self.log.setdefault(room, []).append(text)
        out = []
        if (room == GROUP or room.startswith("desk-")) and sender != "@clerk":
            out += [("@clerk", p.room_id, p.text) for p in self.clerk.handle(room, sender, text)]
        for pid, agent_ in self.agents.items():
            if room in (f"private-{pid}", f"desk-{pid}") and sender != f"@agent-{pid}":
                out += [(f"@agent-{pid}", f"{p.room}-{pid}", p.text) for p in agent_.handle(room.split("-")[0], sender, text)]
        for who, target, body in out:
            self.send(target, who, body)

    def say(self, who: str, room: str, text: str) -> None:
        self.send(GROUP if room == "group" else f"{room}-{who}", f"@human-{who}", text)

    def begin(self, who: str) -> None:
        for p in self.agents[who].start():
            self.send(f"{p.room}-{who}", f"@agent-{who}", p.text)

    def texts(self, *rooms: str) -> str:
        return "\n".join(t for r in rooms for t in self.log.get(GROUP if r == "group" else r, []))

    def not_private(self) -> str:
        return "\n".join(t for r, texts in self.log.items() if not r.startswith("private") for t in texts)
