"""The clerk: a deterministic state machine, not an LLM.

`Clerk.handle` takes one room event and returns the messages to post. It reads no statement,
calls no model and does no I/O, so every rule can be tested without Switch running.

Authority comes only from the server-set sender id and the room an event arrived in, never from
message text. A claim's owner is whoever's desk it arrived on, and only that person's human
account can release it. Refusals contain fixed codes, so the clerk can never echo private text
into any room.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from kitty import claims as C
from kitty import ledger as L



def day_month(d: date) -> str:
    return f"{d.day} {d:%b}"



@dataclass(frozen=True)
class Person:
    id: str
    name: str
    human_sender: str
    agent_sender: str
    desk_room: str


@dataclass(frozen=True)
class Post:
    room_id: str
    text: str


@dataclass
class ExpenseRecord:
    id: str
    claim: C.Expense
    status: str = "held"  # held -> released | dropped | conflict_hold
    released_by: str | None = None
    released_at: str | None = None
    conflict_with: str | None = None


@dataclass
class TransferRecord:
    id: str
    claim: C.Transfer
    # held -> awaiting_receiver -> confirmed | challenged -> withdrawn
    status: str = "held"
    released_by: str | None = None
    released_at: str | None = None
    answer: bool | None = None
    answer_status: str | None = None  # held -> released
    withdraw_reason: str | None = None


RELEASE = re.compile(r"^\s*release\s+([ct][0-9]{1,6})\s*$", re.I)
DROP = re.compile(r"^\s*drop\s+([ct][0-9]{1,6})\s*$", re.I)
WITHDRAW = re.compile(r"^\s*withdraw\s+(t[0-9]{1,6})(?:\s+(payment_failed|mistaken|duplicate))?\s*$", re.I)
GROUP_COMMAND = re.compile(r"^\s*(balances|settle|status)\s*$", re.I)
COMMAND_LIKE = re.compile(r"^\s*(release|drop|withdraw)\b", re.I)


@dataclass
class Clerk:
    trip: C.Trip
    people: dict[str, Person]
    group_room: str
    now: object = field(default=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    expenses: dict[str, ExpenseRecord] = field(default_factory=dict)
    transfers: dict[str, TransferRecord] = field(default_factory=dict)
    answers_pending: dict[str, str] = field(default_factory=dict)
    audit: list[dict] = field(default_factory=list)
    _counters: dict = field(default_factory=dict)
    _seen_refs: set = field(default_factory=set)

    # ---- routing -------------------------------------------------------------

    def handle(self, room_id: str, sender: str, body: str) -> list[Post]:
        if room_id == self.group_room:
            return self._group(sender, body)
        owner = next((p for p in self.people.values() if p.desk_room == room_id), None)
        if owner is None:
            return []  # Not a room the clerk serves. Nothing is read or relayed.
        if sender == owner.agent_sender:
            return self._from_agent(owner, body)
        if sender == owner.human_sender:
            return self._from_owner(owner, body)
        self._log("refused", room=room_id, reason="stranger_in_desk")
        return [Post(room_id, "Refused: only this desk's owner and their agent can act here.")]

    def _name(self, pid: str) -> str:
        return self.people[pid].name

    def _log(self, action: str, **fields: object) -> None:
        self.audit.append({"action": action, "at": self.now(), **fields})

    def _next(self, prefix: str) -> str:
        self._counters[prefix] = self._counters.get(prefix, 0) + 1
        return f"{prefix}{self._counters[prefix]}"

    # ---- from a personal agent: typed data only, and nothing here counts as approval --------

    def _from_agent(self, owner: Person, body: str) -> list[Post]:
        if COMMAND_LIKE.match(body):
            self._log("refused", owner=owner.id, reason="agent_cannot_approve")
            return [Post(owner.desk_room, f"Refused: {owner.name}'s agent can't do that. Only {owner.name} can.")]
        try:
            item = C.parse(body, self.trip, owner.id)
        except C.ClaimError as err:
            self._log("rejected", owner=owner.id, code=err.code)
            return [Post(owner.desk_room, f"Rejected: {err.code}. Nothing was recorded.")]
        key = (owner.id, item.ref)
        if key in self._seen_refs:
            self._log("rejected", owner=owner.id, code="duplicate_ref")
            return [Post(owner.desk_room, "Rejected: duplicate_ref. Already on this desk.")]
        self._seen_refs.add(key)
        if isinstance(item, C.Expense):
            return self._propose_expense(owner, item)
        if isinstance(item, C.Transfer):
            return self._propose_transfer(owner, item)
        return self._propose_answer(owner, item)

    def _propose_expense(self, owner: Person, item: C.Expense) -> list[Post]:
        for rec in self.expenses.values():
            if rec.status != "dropped" and rec.claim.fingerprint() == item.fingerprint():
                self._log("rejected", owner=owner.id, code="duplicate_claim", of=rec.id)
                return [Post(owner.desk_room, f"Rejected: duplicate_claim of {rec.id}. Nothing was recorded.")]
        cid = self._next("c")
        self.expenses[cid] = ExpenseRecord(cid, item)
        self._log("held", id=cid, owner=owner.id)
        return [Post(owner.desk_room, f"**{cid}** ready [ref {item.ref}]: {self._expense_line(item)}\nReply `release {cid}` to share it with the group, or `drop {cid}`.")]

    def _propose_transfer(self, owner: Person, item: C.Transfer) -> list[Post]:
        tid = self._next("t")
        self.transfers[tid] = TransferRecord(tid, item)
        self._log("held", id=tid, owner=owner.id)
        return [Post(owner.desk_room, f"**{tid}** ready [ref {item.ref}]: you paid {self._name(item.to_id)} {C.rupees(item.amount_paise)} on {day_month(item.date)}.\nReply `release {tid}` to tell the group, or `drop {tid}`.")]

    def _propose_answer(self, owner: Person, item: C.TransferAnswer) -> list[Post]:
        rec = self.transfers.get(item.transfer_id)
        if rec is None or rec.claim.to_id != owner.id or rec.status != "awaiting_receiver":
            self._log("rejected", owner=owner.id, code="no_such_question")
            return [Post(owner.desk_room, "Rejected: no_such_question. Nothing was recorded.")]
        rec.answer = item.received
        rec.answer_status = "held"
        said = "reached you" if item.received else "did not reach you"
        return [Post(owner.desk_room, f"Your agent found that {rec.id} ({C.rupees(rec.claim.amount_paise)} from {self._name(rec.claim.from_id)}) {said}.\nReply `release {rec.id}` to send that answer, or `drop {rec.id}` to keep it private.")]

    # ---- from the owner: the only source of approval ----------------------------------------

    def _from_owner(self, owner: Person, body: str) -> list[Post]:
        if m := RELEASE.match(body):
            return self._release(owner, m.group(1).lower())
        if m := DROP.match(body):
            return self._drop(owner, m.group(1).lower())
        if m := WITHDRAW.match(body):
            return self._withdraw(owner, m.group(1).lower(), (m.group(2) or "mistaken").lower())
        return []  # People may chat on their own desk. The clerk ignores it and never relays it.

    def _release(self, owner: Person, item_id: str) -> list[Post]:
        if item_id in self.expenses:
            return self._release_expense(owner, self.expenses[item_id])
        if item_id in self.transfers:
            rec = self.transfers[item_id]
            if rec.claim.to_id == owner.id and rec.answer_status == "held":
                return self._release_answer(owner, rec)
            return self._release_transfer(owner, rec)
        self._log("refused", owner=owner.id, reason="unknown_claim")
        return [Post(owner.desk_room, f"Refused: there is no {item_id} on this desk.")]

    def _release_expense(self, owner: Person, rec: ExpenseRecord) -> list[Post]:
        if rec.claim.owner_id != owner.id:
            self._log("refused", owner=owner.id, id=rec.id, reason="not_owner")
            return [Post(owner.desk_room, f"Refused: there is no {rec.id} on this desk.")]
        if rec.status != "held":
            self._log("refused", owner=owner.id, id=rec.id, reason=f"already_{rec.status}")
            return [Post(owner.desk_room, f"Refused: {rec.id} is already {rec.status}.")]
        rec.released_by, rec.released_at = owner.human_sender, self.now()
        posts = [Post(owner.desk_room, f"{rec.id} released.")]
        conflict = self._find_conflict(rec)
        if conflict is not None:
            rec.status, rec.conflict_with = "conflict_hold", conflict.id
            conflict.status, conflict.conflict_with = "conflict_hold", rec.id
            self._log("conflict", ids=[rec.id, conflict.id])
            posts.append(Post(self.group_room, f"**Possible conflict:** {conflict.id} and {rec.id} look like one expense paid by two people ({C.rupees(rec.claim.amount_paise)}, {rec.claim.category}, {day_month(rec.claim.date)}). Neither counts until one owner drops theirs."))
            return posts
        rec.status = "released"
        self._log("released", id=rec.id, by=owner.id)
        posts.append(Post(self.group_room, f"**{rec.id}** {self._expense_line(rec.claim)}"))
        return posts

    def _find_conflict(self, rec: ExpenseRecord) -> ExpenseRecord | None:
        for other in self.expenses.values():
            if other.id != rec.id and other.status == "released" and other.claim.owner_id != rec.claim.owner_id and (other.claim.date, other.claim.amount_paise, other.claim.category) == (rec.claim.date, rec.claim.amount_paise, rec.claim.category):
                return other
        return None

    def _release_transfer(self, owner: Person, rec: TransferRecord) -> list[Post]:
        if rec.claim.owner_id != owner.id:
            self._log("refused", owner=owner.id, id=rec.id, reason="not_owner")
            return [Post(owner.desk_room, f"Refused: there is no {rec.id} on this desk.")]
        if rec.status != "held":
            self._log("refused", owner=owner.id, id=rec.id, reason=f"already_{rec.status}")
            return [Post(owner.desk_room, f"Refused: {rec.id} is already {rec.status}.")]
        rec.status, rec.released_by, rec.released_at = "awaiting_receiver", owner.human_sender, self.now()
        self._log("released", id=rec.id, by=owner.id)
        receiver = self.people[rec.claim.to_id]
        return [
            Post(owner.desk_room, f"{rec.id} released."),
            Post(self.group_room, f"**{rec.id}** {self._name(rec.claim.from_id)} says they paid {receiver.name} {C.rupees(rec.claim.amount_paise)} on {day_month(rec.claim.date)}. Not counted until {receiver.name} confirms."),
            Post(receiver.desk_room, f"KITTY_CHECK {rec.id} from={rec.claim.from_id} amount_paise={rec.claim.amount_paise} date={rec.claim.date.isoformat()}"),
        ]

    def _release_answer(self, owner: Person, rec: TransferRecord) -> list[Post]:
        rec.answer_status = "released"
        self._log("answered", id=rec.id, by=owner.id, received=rec.answer)
        payer = self.people[rec.claim.from_id]
        if rec.answer:
            rec.status = "confirmed"
            self._log("confirmed", id=rec.id)
            return [
                Post(owner.desk_room, f"Answer for {rec.id} sent."),
                Post(self.group_room, f"**{rec.id} confirmed by both sides:** {payer.name} paid {owner.name} {C.rupees(rec.claim.amount_paise)}."),
                *self._square_check(),
            ]
        rec.status = "challenged"
        self._log("challenged", id=rec.id)
        return [
            Post(owner.desk_room, f"Answer for {rec.id} sent."),
            Post(self.group_room, f"**{rec.id} NOT CONFIRMED:** {payer.name} says they paid {owner.name} {C.rupees(rec.claim.amount_paise)}. {owner.name}'s side shows nothing. Both agents are re-checking privately. No statement is shared."),
            Post(payer.desk_room, f"KITTY_RECHECK {rec.id}"),
        ]

    def _drop(self, owner: Person, item_id: str) -> list[Post]:
        rec = self.expenses.get(item_id) or self.transfers.get(item_id)
        if isinstance(rec, TransferRecord) and rec.claim.to_id == owner.id and rec.answer_status == "held":
            rec.answer, rec.answer_status = None, None
            self._log("answer_dropped", id=rec.id, by=owner.id)
            return [Post(owner.desk_room, f"Your answer for {rec.id} stays private. {rec.id} remains unconfirmed.")]
        if rec is None or rec.claim.owner_id != owner.id:
            self._log("refused", owner=owner.id, reason="unknown_claim")
            return [Post(owner.desk_room, f"Refused: there is no {item_id} on this desk.")]
        if isinstance(rec, ExpenseRecord) and rec.status == "conflict_hold":
            return self._resolve_conflict(owner, rec)
        if rec.status != "held":
            return [Post(owner.desk_room, f"Refused: {rec.id} is already {rec.status}.")]
        rec.status = "dropped"
        self._log("dropped", id=rec.id, by=owner.id)
        return [Post(owner.desk_room, f"{rec.id} dropped. The group never saw it.")]

    def _resolve_conflict(self, owner: Person, rec: ExpenseRecord) -> list[Post]:
        other = self.expenses[rec.conflict_with]  # type: ignore[index]
        rec.status, other.status = "dropped", "released"
        rec.conflict_with = other.conflict_with = None
        self._log("conflict_resolved", dropped=rec.id, kept=other.id)
        return [
            Post(owner.desk_room, f"{rec.id} dropped."),
            Post(self.group_room, f"Conflict cleared: {owner.name} withdrew {rec.id}. **{other.id}** now counts: {self._expense_line(other.claim)}"),
        ]

    def _withdraw(self, owner: Person, tid: str, reason: str) -> list[Post]:
        rec = self.transfers.get(tid)
        if rec is None or rec.claim.owner_id != owner.id:
            self._log("refused", owner=owner.id, reason="unknown_claim")
            return [Post(owner.desk_room, f"Refused: there is no {tid} on this desk.")]
        if rec.status not in ("challenged", "awaiting_receiver"):
            return [Post(owner.desk_room, f"Refused: {tid} is {rec.status}, so it can't be withdrawn.")]
        rec.status, rec.withdraw_reason = "withdrawn", reason
        self._log("withdrawn", id=tid, by=owner.id, reason=reason)
        why = {"payment_failed": "The payment failed and never arrived.", "mistaken": "It was a mistake.", "duplicate": "It was a duplicate."}[reason]
        return [
            Post(owner.desk_room, f"{tid} withdrawn."),
            Post(self.group_room, f"**{tid} withdrawn by {owner.name}.** {why} It was never counted, so balances don't change. Neither statement left anyone's side."),
        ]

    # ---- group room ----------------------------------------------------------------------------

    def _group(self, sender: str, body: str) -> list[Post]:
        humans = {p.human_sender for p in self.people.values()}
        if sender not in humans:
            return []
        if COMMAND_LIKE.match(body):
            self._log("refused", reason="release_outside_desk")
            return [Post(self.group_room, "Refused: claims are released from their owner's own desk, by their owner.")]
        if not (m := GROUP_COMMAND.match(body)):
            return []
        command = m.group(1).lower()
        if command == "balances":
            return [Post(self.group_room, self.balances_text())]
        if command == "settle":
            return [Post(self.group_room, self.settle_text())]
        return [Post(self.group_room, self.status_text())]

    def _square_check(self) -> list[Post]:
        counted = [r for r in self.expenses.values() if r.status == "released"]
        if counted and not self.open_items() and not any(self.balances().values()):
            return [Post(self.group_room, "**Everyone's square.** Every balance is zero, and every payment was confirmed by both sides.")]
        return []

    # ---- reads -----------------------------------------------------------------------------------

    def lines(self) -> list[L.Line]:
        return [L.Line(r.claim.payer_id, r.claim.amount_paise, r.claim.shares) for r in self.expenses.values() if r.status == "released"]

    def confirmed(self) -> list[L.Payment]:
        return [L.Payment(r.claim.from_id, r.claim.to_id, r.claim.amount_paise) for r in self.transfers.values() if r.status == "confirmed"]

    def balances(self) -> dict[str, int]:
        return L.balances(self.trip.participants, self.lines(), self.confirmed())

    def open_items(self) -> list[str]:
        out = [r.id for r in self.expenses.values() if r.status in ("held", "conflict_hold")]
        out += [r.id for r in self.transfers.values() if r.status in ("held", "awaiting_receiver", "challenged")]
        return out

    def balances_text(self) -> str:
        net = self.balances()
        rows = "\n".join(f"| {self._name(p)} | {C.rupees(v)} | {'is owed' if v > 0 else 'owes' if v < 0 else 'square'} |" for p, v in net.items())
        pending = [r for r in self.transfers.values() if r.status in ("awaiting_receiver", "challenged")]
        note = f"\n\n{len(pending)} payment(s) still unconfirmed, and not counted." if pending else ""
        return f"| Person | Balance | |\n|---|---:|---|\n{rows}{note}"

    def settle_text(self) -> str:
        plan = L.settle(self.balances())
        if not plan:
            return "Nothing to settle. Everyone's square." if self.lines() else "No released expenses yet."
        rows = "\n".join(f"- {self._name(p.from_id)} pays {self._name(p.to_id)} **{C.rupees(p.amount_paise)}**" for p in plan)
        return f"Suggested payments ({len(plan)}):\n{rows}\n\nKitty never moves money. Each payment counts once both people's sides confirm it."

    def status_text(self) -> str:
        return f"{sum(r.status == 'released' for r in self.expenses.values())} expenses counted, {sum(r.status == 'confirmed' for r in self.transfers.values())} payments confirmed, {len(self.open_items())} open."

    def _expense_line(self, claim: C.Expense) -> str:
        shares = ", ".join(f"{self._name(p)} {C.rupees(v)}" for p, v in claim.shares)
        return f"{C.rupees(claim.amount_paise)} · {claim.category} · {day_month(claim.date)} · paid by {self._name(claim.payer_id)} · {shares}"
