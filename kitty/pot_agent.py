"""A person's own agent on a Pot trip. Lives on that person's side, like `PersonalAgent`.

Checking a payment is plain code on the treasurer's own rows, and no model is involved. Nothing from a
statement line crosses: the clerk gets a result and an opaque token, and the private chit carries a date.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date

from kitty import claims as C
from kitty import pot as P
from kitty import statements as S
from kitty.agent import PersonalAgent, Post
from kitty.clerk import day_month

POT_CHECK = re.compile(r"^KITTY_POT_CHECK (p[0-9]+) from=([a-z0-9_-]+) amount_paise=([0-9]+) date=(\d{4}-\d{2}-\d{2}) ref_token=([0-9a-f]{32}|-)$")
POT_RECHECK = re.compile(r"^KITTY_RECHECK (p[0-9]+)$")
POT_CARD = re.compile(r"^\*\*(p[0-9]+)\*\* ready \[ref ([a-z0-9]+)\]")
FREED = re.compile(r"^(p[0-9]+)(?: withdrawn\.|: not counted\.| corrected: not counted\.)$")
COUNTED_AGAIN = re.compile(r"^(p[0-9]+) corrected: counted\.$")
ANSWERED = re.compile(r"^(not\s+)?received\s+(t[0-9]+)$", re.I)
AMOUNT = re.compile(r"^₹?[0-9][0-9,]*(\.[0-9]{1,2})?$")
REFERENCE = re.compile(r"^[A-Za-z0-9]{4,16}$")
HOW = "I couldn't read that. Try `paid 5000 2026-10-09`, and add `ref U441001` if you have the UPI reference."


@dataclass(frozen=True)
class Candidate:
    result: str  # exact | amount_differs | none
    row: S.Row | None


def taken_back(rows: list[S.Row], row: S.Row) -> bool:
    """A credit counts for nothing if the same reference later went back out."""
    for r in rows:
        if r.row_id != row.row_id and r.ref == row.ref and (r.day, r.time) >= (row.day, row.time):
            if r.narration.startswith("UPI/REV/") or r.debit == row.credit:
                return True
    return False


def find_candidate(rows: list[S.Row], amount: int, around: date, from_person: str, directory: dict[str, str], used: set[str], want_token: str | None, key: bytes, slack_days: int = 2) -> Candidate:
    """Not `S.find_credit`, which accepts a credit later taken back and matches one row twice. That's right for its job."""
    credits = [
        r for r in rows
        if r.credit > 0 and r.narration.startswith("UPI/CR/") and S.counterparty(r, directory) == from_person
        and abs((r.day - around).days) <= slack_days and r.row_id not in used and not taken_back(rows, r)
    ]
    exact = [r for r in credits if r.credit == amount]
    if exact:
        wanted = [r for r in exact if want_token and credit_token(key, r) == want_token]
        return Candidate("exact", (wanted or exact)[0])
    return Candidate("amount_differs" if credits else "none", None)


def credit_token(key: bytes, row: S.Row) -> str:
    return P.token(key, row.ref or f"row:{row.row_id}")


@dataclass
class PotAgent(PersonalAgent):
    cfg: P.PotConfig | None = None
    pot_key: bytes = b""
    bound: dict[str, str] = field(default_factory=dict)  # Contribution id to row id, while that claim could still count.
    offered: dict[str, str] = field(default_factory=dict)  # The last row offered for each, to bring back after a correction.

    @property
    def is_treasurer(self) -> bool:
        return self.me == self.cfg.treasurer

    # ---- remembering --------------------------------------------------------------------

    def to_state(self) -> dict:
        return {**super().to_state(), "pot": {"bound": self.bound, "offered": self.offered}}

    def restore(self, state: dict) -> None:
        super().restore(state)
        pot = state.get("pot") or {}
        self.bound, self.offered = dict(pot.get("bound", {})), dict(pot.get("offered", {}))

    # ---- a member's payments -----------------------------------------------------------------

    def _contribution(self, ref: str, row_day: date, amount: int, reference: str | None, last4: str | None) -> str:
        return C.PREFIX + json.dumps({
            "v": 1, "type": "contribution", "ref": ref, "trip_id": self.trip.trip_id, "owner_id": self.me, "to_id": self.cfg.treasurer,
            "date": row_day.isoformat(), "amount_paise": amount, "currency": self.trip.currency,
            "ref_token": P.token(self.pot_key, reference) if reference else None, "ref_last4": last4,
        })

    def _transfer_claim(self, ref: str, to: str, row: S.Row) -> str:
        if to == self.cfg.treasurer and not self.is_treasurer:
            return self._contribution(ref, row.day, row.debit, row.ref or None, None)
        return super()._transfer_claim(ref, to, row)

    def _scan(self) -> list[Post]:
        """People pay into a pot before a trip, so a member's look starts at `collect_from`. The model's window doesn't move."""
        out: list[Post] = []
        if not self.is_treasurer:
            known = {r.row_id for r in self.transfers.values()}
            before = S.in_window(self.rows, self.cfg.collect_from, self.trip.start)
            for row, who in S.outgoing_transfers(before, self.directory):
                if who == self.cfg.treasurer and row.row_id not in known and row.day < self.trip.start:
                    ref = self._ref()
                    self.transfers[ref] = row
                    out.append(Post("desk", self._transfer_claim(ref, who, row)))
        return out + super()._scan()

    def _from_owner(self, body: str) -> list[Post]:
        text = body.strip()
        words = text.split()
        if words and words[0].lower() == "paid":
            return self._typed_payment(words[1:])
        if m := ANSWERED.match(text):
            return self._typed_answer(m.group(2).lower(), not m.group(1))
        return super()._from_owner(body)

    def _typed_payment(self, words: list[str]) -> list[Post]:
        """Parsed here, and never by a model, because this is what the claim is built from."""
        if self.is_treasurer:
            return [Post("private", "You hold the money, so you don't pay into it. Your share is settled in the final balances.")]
        if len(words) < 2 or not AMOUNT.fullmatch(words[0]):
            return [Post("private", HOW)]
        rest = words[2:]
        reference = None
        if rest[:1] and rest[0].lower() == "ref":
            if len(rest) < 2 or not REFERENCE.fullmatch(rest[1]):
                return [Post("private", HOW)]
            reference, rest = rest[1], rest[2:]
        show = bool(rest) and rest[0].lower() == "show"
        if rest[1 if show else 0:]:
            return [Post("private", HOW)]
        amount = S.to_paise(words[0].replace("₹", "").replace(",", ""))
        try:
            day = date.fromisoformat(words[1])
        except ValueError:
            return [Post("private", HOW)]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", words[1]) or not amount:
            return [Post("private", HOW)]
        if not self.cfg.collect_from <= day <= self.trip.end:
            return [Post("private", f"That date is outside this trip's collection dates, {day_month(self.cfg.collect_from)} to {day_month(self.trip.end)}. Nothing was filed.")]
        last4 = reference[-4:] if show and reference else None
        return [
            Post("desk", self._contribution(self._ref(), day, amount, reference, last4)),
            Post("private", "It's on your desk. Release it when you're ready. It doesn't count until it's confirmed."),
        ]

    def _typed_answer(self, tid: str, received: bool) -> list[Post]:
        answer = C.PREFIX + json.dumps({"v": 1, "type": "transfer_answer", "ref": self._ref(), "trip_id": self.trip.trip_id, "owner_id": self.me, "transfer_id": tid, "received": received})
        return [Post("desk", answer), Post("private", "Your desk has the answer. Only yes or no goes to the clerk.")]

    def _check(self, tid: str, sender: str, amount: int, around: date) -> list[Post]:
        """With no statement there's nothing to look at, so ask. Saying "no" here would be making it up."""
        if not self.rows:
            who = self.names.get(sender, sender)
            return [Post("private", f"{who} says they paid you {self._money(amount)}. You haven't added a statement, so I can't look. Did it arrive? Say `received {tid}` or `not received {tid}`.")]
        return super()._check(tid, sender, amount, around)

    # ---- messages from the clerk ---------------------------------------------------------------

    def _from_clerk(self, body: str) -> list[Post]:
        if m := POT_CHECK.match(body):
            return self._pot_check(m.group(1), m.group(2), int(m.group(3)), date.fromisoformat(m.group(4)), None if m.group(5) == "-" else m.group(5))
        if m := POT_RECHECK.match(body):
            return self._pot_recheck(m.group(1))
        if m := POT_CARD.match(body):
            self.filed_transfers[m.group(1)] = m.group(2)
            return []
        if m := FREED.match(body):
            self.bound.pop(m.group(1), None)
            return []
        if m := COUNTED_AGAIN.match(body):
            if m.group(1) in self.offered:
                self.bound[m.group(1)] = self.offered[m.group(1)]
            return []
        return super()._from_clerk(body)

    def _pot_check(self, pid: str, sender: str, amount: int, around: date, want: str | None) -> list[Post]:
        self.bound.pop(pid, None)
        found = find_candidate(self.rows, amount, around, sender, self.directory, set(self.bound.values()), want, self.pot_key)
        match = {"v": 1, "type": "pot_match", "ref": self._ref(), "trip_id": self.trip.trip_id, "owner_id": self.me, "contribution_id": pid, "result": found.result, "credit_token": None}
        if found.row is not None:
            match["credit_token"] = credit_token(self.pot_key, found.row)
            self.bound[pid] = self.offered[pid] = found.row.row_id
        return [Post("desk", C.PREFIX + json.dumps(match)), Post("private", self._chit(sender, amount, around, found))]

    def _chit(self, sender: str, amount: int, around: date, found: Candidate) -> str:
        """Only what the claim already says. The line itself stays on this side."""
        head = f"{self.names.get(sender, sender)} says they paid you {self._money(amount)} around {day_month(around)}."
        if found.result == "exact":
            return f"{head} Found it: {day_month(found.row.day)}. Your desk has it for you to confirm."
        if found.result == "amount_differs":
            return f"{head} Something near this date, for a different amount. Your desk has it."
        if not self.rows:
            return f"{head} Nothing yet. You haven't added a statement."
        last = max(r.day for r in self.rows)
        return f"{head} Nothing yet. Your statement ends {day_month(last)}. Add a later export and check again, or decide on your desk."

    def _pot_recheck(self, pid: str) -> list[Post]:
        if self.transfers.get(self.filed_transfers.get(pid, "")) is None:
            return [Post("private", f"I don't have a line on your statement for {pid}. Check your UPI app to see whether it went through.")]
        return self._recheck(pid)
