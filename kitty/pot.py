"""The Pot trip: one person holds the group's money, and only their confirmation counts.

Pure: no I/O, no model and no floats. Nothing in the shared-expense path imports this, so a trip
with no treasurer can't reach it. See docs/POT_TRIP_SPEC.md and ADR-016.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from kitty import claims as C
from kitty import ledger as L
from kitty.clerk import Post, day_month

MATCH_RESULTS = ("exact", "amount_differs", "none")
ASK_CODES = ("amount_differs", "date_differs", "not_found_yet")
REJECT_CODES = ("not_received", "duplicate", "reversed")
CORRECT_OUT_CODES = ("reversed", "not_received", "mistaken")
POT_TYPES = ("contribution", "pot_match")

_TOKEN = re.compile(r"^[0-9a-f]{32}$")
_LAST4 = re.compile(r"^[A-Za-z0-9]{4}$")
_CONTRIBUTION_ID = re.compile(r"^p[0-9]{1,6}$")
_TAG = re.compile(r"^(.*) \[d([0-9]+) ([0-9a-f]{12})\]$", re.S)


@dataclass(frozen=True)
class PotConfig:
    treasurer: str
    collect_from: date


@dataclass(frozen=True)
class Contribution:
    ref: str
    trip_id: str
    owner_id: str
    to_id: str
    date: date
    amount_paise: int
    currency: str
    ref_token: str | None
    ref_last4: str | None


@dataclass(frozen=True)
class PotMatch:
    ref: str
    trip_id: str
    owner_id: str
    contribution_id: str
    result: str
    credit_token: str | None


# ---- typed claims ----------------------------------------------------------------


def _optional(value: object, pattern: re.Pattern, code: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise C.ClaimError(code)
    return value


def _collect_date(value: object, trip: C.Trip, cfg: PotConfig) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise C.ClaimError("bad_date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise C.ClaimError("bad_date") from exc
    if not cfg.collect_from <= parsed <= trip.end:
        raise C.ClaimError("date_outside_trip")
    return parsed


def _contribution(obj: dict, trip: C.Trip, cfg: PotConfig, desk_owner: str) -> Contribution:
    C._exact_keys(obj, {"v", "type", "ref", "trip_id", "owner_id", "to_id", "date", "amount_paise", "currency", "ref_token", "ref_last4"})
    C._common(obj, trip, desk_owner)
    if desk_owner == cfg.treasurer:
        raise C.ClaimError("self_transfer")
    if obj["to_id"] != cfg.treasurer:
        raise C.ClaimError("not_treasurer")
    if obj["currency"] != trip.currency:
        raise C.ClaimError("bad_currency")
    return Contribution(
        C._ref(obj["ref"]), trip.trip_id, desk_owner, cfg.treasurer, _collect_date(obj["date"], trip, cfg), C._amount(obj["amount_paise"]), trip.currency,
        _optional(obj["ref_token"], _TOKEN, "bad_token"), _optional(obj["ref_last4"], _LAST4, "bad_last4"),
    )


def _pot_match(obj: dict, trip: C.Trip, cfg: PotConfig, desk_owner: str) -> PotMatch:
    C._exact_keys(obj, {"v", "type", "ref", "trip_id", "owner_id", "contribution_id", "result", "credit_token"})
    C._common(obj, trip, desk_owner)
    if desk_owner != cfg.treasurer:
        raise C.ClaimError("not_treasurer")
    cid = obj["contribution_id"]
    if not isinstance(cid, str) or not _CONTRIBUTION_ID.fullmatch(cid):
        raise C.ClaimError("bad_contribution_id")
    if obj["result"] not in MATCH_RESULTS:
        raise C.ClaimError("bad_result")
    credit = _optional(obj["credit_token"], _TOKEN, "bad_token")
    if (obj["result"] == "exact") != (credit is not None):
        raise C.ClaimError("bad_token")
    return PotMatch(C._ref(obj["ref"]), trip.trip_id, desk_owner, cid, obj["result"], credit)


def parse_claim(body: str, trip: C.Trip, cfg: PotConfig, desk_owner: str) -> Contribution | PotMatch | None:
    """One typed Pot message, or None when it's not one, so the clerk's normal path carries on."""
    if not body.startswith(C.PREFIX):
        return None
    try:
        obj = json.loads(body[len(C.PREFIX):])
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict) or obj.get("type") not in POT_TYPES:
        return None
    if obj.get("v") != 1:
        raise C.ClaimError("bad_version")
    return (_contribution if obj["type"] == "contribution" else _pot_match)(obj, trip, cfg, desk_owner)


def token(key: bytes, value: str) -> str:
    """Lets the clerk compare two references without ever learning either."""
    return hmac.new(key, value.strip().upper().encode(), hashlib.sha256).hexdigest()[:32]


# ---- money ------------------------------------------------------------------------


def settle_via(net: dict[str, int], hub: str) -> list[L.Payment]:
    """Everyone settles with the person holding the money. `L.settle` stays as it is."""
    return [L.Payment(p, hub, -v) if v < 0 else L.Payment(hub, p, v) for p, v in net.items() if p != hub and v]


@dataclass(frozen=True)
class PotFigures:
    asked: dict[str, int]
    credited: dict[str, int]
    outstanding: dict[str, int]
    above: dict[str, int]
    collected: int
    spent: int
    paid_out: int
    position: int
    reimbursed: dict[str, int]
    refunded: dict[str, int]


def figures(cfg: PotConfig, people: Sequence[str], calls: Iterable[tuple[int, tuple[str, ...]]], contributions: Iterable[L.Payment], lines: Iterable[L.Line], transfers: Iterable[L.Payment]) -> PotFigures:
    """Derived, never stored. A call changes who is asked for what, and never a balance."""
    lines, zero = list(lines), {p: 0 for p in people}
    asked, credited, spent_by, back = dict(zero), dict(zero), dict(zero), dict(zero)
    for amount, who in calls:
        for person in who:
            if person != cfg.treasurer:
                asked[person] += amount
    for pay in contributions:
        credited[pay.from_id] += pay.amount_paise
    for line in lines:
        spent_by[line.payer] += line.amount_paise
    for pay in transfers:
        if pay.from_id == cfg.treasurer:
            back[pay.to_id] += pay.amount_paise
    reimbursed = {p: min(spent_by[p], back[p]) for p in people}
    collected, spent, paid_out = sum(credited.values()), spent_by[cfg.treasurer], sum(back.values())
    return PotFigures(
        asked, credited,
        {p: max(0, asked[p] - credited[p]) for p in people},
        {p: max(0, credited[p] - asked[p]) for p in people},
        collected, spent, paid_out, collected - spent - paid_out,
        reimbursed, {p: back[p] - reimbursed[p] for p in people},
    )


# ---- the decision chain ---------------------------------------------------------------


def chain_next(prev: str, text: str) -> str:
    return hashlib.sha256((prev + text).encode()).hexdigest()[:12]


def chain_tag(text: str, seq: int, prev: str) -> tuple[str, str]:
    new = chain_next(prev, text)
    return f"{text} [d{seq} {new}]", new


def chain_check(lines: Iterable[str]) -> str | None:
    """The first decision that doesn't fit, or None. Tamper-evident, and not tamper-proof: see the spec, section 9."""
    prev, expected = "", 1
    for line in lines:
        m = _TAG.match(line)
        if not m:
            continue
        seq = int(m.group(2))
        if seq != expected or chain_next(prev, m.group(1)) != m.group(3):
            return f"d{seq}"
        prev, expected = m.group(3), expected + 1
    return None


# ---- the book: every decision, in order, and nothing overwritten ---------------------------

IST = timezone(timedelta(hours=5, minutes=30))
OPEN = ("held", "submitted", "recipient_review", "needs_clarification", "rejected")
STATE_AFTER = {
    "submitted": "submitted", "match": "recipient_review", "recheck": "submitted", "confirm": "confirmed", "ask": "needs_clarification",
    "reject": "rejected", "withdraw": "withdrawn", "correct_out": "rejected", "correct_in": "confirmed",
}
WITHDRAW_WHY = {"payment_failed": "The payment failed and never arrived.", "mistaken": "It was a mistake.", "duplicate": "It was a duplicate."}
ASK_PLAIN = {
    "amount_differs": "What they can see doesn't match the amount you claimed.",
    "date_differs": "What they can see doesn't match the date you claimed.",
    "not_found_yet": "They can't find it yet.",
}
SAID = {"exact": "found it", "amount_differs": "a different amount", "none": "nothing yet"}

_RELEASE = re.compile(r"^\s*release\s+(p[0-9]{1,6})\s*$", re.I)
_DROP = re.compile(r"^\s*drop\s+(p[0-9]{1,6})\s*$", re.I)
_WITHDRAW = re.compile(r"^\s*withdraw\s+(p[0-9]{1,6})(?:\s+(.*?))?\s*$", re.I)
_CONFIRM = re.compile(r"^\s*confirm\s+(p[0-9]{1,6})(\s+manual)?\s*$", re.I)
_ASK = re.compile(r"^\s*ask\s+(p[0-9]{1,6})(?:\s+(.*?))?\s*$", re.I)
_REJECT = re.compile(r"^\s*reject\s+(p[0-9]{1,6})(?:\s+(.*?))?\s*$", re.I)
_RECHECK = re.compile(r"^\s*recheck\s+(p[0-9]{1,6})\s*$", re.I)
_CORRECT = re.compile(r"^\s*correct\s+(p[0-9]{1,6})(?:\s+(.*?))?\s*$", re.I)
_CALL = re.compile(r"^\s*call\s+[₹0-9]", re.I)
_CALL_LOOSE = re.compile(r"^\s*call\b(.*)$", re.I | re.S)
_CANCEL = re.compile(r"^\s*cancel\s+(k[0-9]{1,6})\s*$", re.I)
_MEMBER_COMMANDS = (_RELEASE, _DROP, _WITHDRAW)
_TREASURER_COMMANDS = (_CONFIRM, _ASK, _REJECT, _RECHECK, _CORRECT, _CALL, _CANCEL)
_AMOUNT = re.compile(r"^₹?[0-9][0-9,]*(\.[0-9]{1,2})?$")
_POT_COMMAND = re.compile(r"^\s*pot\s*$", re.I)


@dataclass(frozen=True)
class Decision:
    """`seq` numbers what the group room shows. A private step, such as an agent's result, has seq 0."""

    seq: int
    at: str
    actor: str
    kind: str
    target: str
    basis: str | None = None
    code: str | None = None


@dataclass
class ContributionRecord:
    id: str
    claim: Contribution
    dropped: bool = False
    candidate: str | None = None
    credit_token: str | None = None


@dataclass
class PotBook:
    cfg: PotConfig
    decisions: list[Decision] = field(default_factory=list)
    records: dict[str, ContributionRecord] = field(default_factory=dict)
    calls: dict[str, tuple[int, tuple[str, ...]]] = field(default_factory=dict)
    cancelled: set[str] = field(default_factory=set)
    _seq: int = 0
    _hash: str = ""

    # ---- reads ------------------------------------------------------------------------

    def status(self, pid: str) -> str:
        """A fold over the decisions, so nothing can be overwritten."""
        if self.records[pid].dropped:
            return "dropped"
        state = "held"
        for d in self.decisions:
            if d.target == pid:
                state = STATE_AFTER[d.kind]
        return state

    def basis(self, pid: str) -> str | None:
        return next((d.basis for d in reversed(self.decisions) if d.target == pid and d.kind in ("confirm", "correct_in")), None)

    def confirmed_payments(self) -> list[L.Payment]:
        return [L.Payment(r.claim.owner_id, r.claim.to_id, r.claim.amount_paise) for r in self.records.values() if self.status(r.id) == "confirmed"]

    def open_ids(self) -> list[str]:
        return [pid for pid in self.records if self.status(pid) in OPEN]

    def view(self, clerk) -> PotFigures:
        calls = [call for kid, call in self.calls.items() if kid not in self.cancelled]
        transfers = [L.Payment(r.claim.from_id, r.claim.to_id, r.claim.amount_paise) for r in clerk.transfers.values() if r.status == "confirmed"]
        return figures(self.cfg, clerk.trip.participants, calls, self.confirmed_payments(), clerk.lines(), transfers)

    def plan(self, clerk) -> list[L.Payment]:
        return settle_via(clerk.balances(), self.cfg.treasurer)

    def settle_text(self, clerk) -> str:
        plan = self.plan(clerk)
        if not plan:
            return "Nothing to settle. Everyone's square." if clerk.lines() or self.confirmed_payments() else "Nothing has been counted yet."
        rows = "\n".join(f"- {clerk._name(p.from_id)} pays {clerk._name(p.to_id)} **{clerk._money(p.amount_paise)}**" for p in plan)
        return f"Suggested payments ({len(plan)}):\n{rows}\n\nKitty never moves money. Each payment counts once whoever receives it confirms it."

    def square_posts(self, clerk) -> list[Post]:
        counted = clerk.lines() or self.confirmed_payments()
        if counted and not clerk.open_items() and not any(clerk.balances().values()):
            return [Post(clerk.group_room, "**Everyone's square.** Every balance is zero, and every payment was confirmed by whoever received it.")]
        return []

    def _position_line(self, clerk, position: int) -> str:
        name = clerk._name(self.cfg.treasurer)
        if position < 0:
            return f"{name} has put in {clerk._money(-position)} of their own, by the records."
        return f"{name} should be holding {clerk._money(position)}, by the records."

    def pot_text(self, clerk) -> str:
        f = self.view(clerk)
        rows = "\n".join(
            f"| {clerk._name(p)} | {clerk._money(f.asked[p])} | {clerk._money(f.credited[p])} | {clerk._money(f.outstanding[p])} |"
            for p in clerk.trip.participants if p != self.cfg.treasurer
        )
        return f"| Person | Asked | Confirmed | Outstanding |\n|---|---:|---:|---:|\n{rows}\n\n{self._position_line(clerk, f.position)}"

    def board_view(self, clerk) -> dict:
        f = self.view(clerk)
        return {
            "treasurer": clerk._name(self.cfg.treasurer),
            "roll": [
                {"name": clerk._name(p), "asked": f.asked[p], "credited": f.credited[p], "outstanding": f.outstanding[p], "above": f.above[p]}
                for p in clerk.trip.participants if p != self.cfg.treasurer
            ],
            "position": f.position,
            "position_line": self._position_line(clerk, f.position),
            "contributions": [
                {"id": r.id, "from": clerk._name(r.claim.owner_id), "amount_paise": r.claim.amount_paise, "date": r.claim.date.isoformat(), "status": self.status(r.id), "basis": self.basis(r.id)}
                for r in self.records.values() if self.status(r.id) not in ("held", "dropped")
            ],
            "open": len(self.open_ids()),
        }

    # ---- routing ----------------------------------------------------------------------

    def handle(self, clerk, room_id: str, sender: str, body: str) -> list[Post] | None:
        """Returns None for anything that isn't ours, and the clerk's normal path carries on."""
        if room_id == clerk.group_room:
            return self._group(clerk, sender, body)
        owner = next((p for p in clerk.people.values() if p.desk_room == room_id), None)
        if owner is None:
            return None
        if sender == owner.agent_sender:
            return self._from_agent(clerk, owner, body)
        if sender == owner.human_sender:
            return self._from_human(clerk, owner, body)
        return None

    def _group(self, clerk, sender: str, body: str) -> list[Post] | None:
        if sender not in {p.human_sender for p in clerk.people.values()}:
            return None
        if _POT_COMMAND.match(body):
            return [Post(clerk.group_room, self.pot_text(clerk))]
        if any(rx.match(body) for rx in _TREASURER_COMMANDS):
            return [Post(clerk.group_room, "Refused: decisions are made on your own desk.")]
        return None

    def _from_agent(self, clerk, owner, body: str) -> list[Post] | None:
        if any(rx.match(body) for rx in (*_MEMBER_COMMANDS, *_TREASURER_COMMANDS)):
            clerk._log("refused", owner=owner.id, reason="agent_cannot_approve")
            return [Post(owner.desk_room, f"Refused: agent_cannot_approve. {owner.name}'s agent can't do that. Only {owner.name} can.")]
        try:
            item = parse_claim(body, clerk.trip, self.cfg, owner.id)
        except C.ClaimError as err:
            clerk._log("rejected", owner=owner.id, code=err.code)
            return [self._rejected(clerk, owner, err.code)]
        if item is None:
            return self._transfer_to_treasurer(clerk, owner, body)
        if isinstance(item, Contribution):
            return self._file(clerk, owner, item)
        return self._match(clerk, owner, item)

    def _transfer_to_treasurer(self, clerk, owner, body: str) -> list[Post] | None:
        if not body.startswith(C.PREFIX):
            return None
        try:
            obj = json.loads(body[len(C.PREFIX):])
        except json.JSONDecodeError:
            return None
        if isinstance(obj, dict) and obj.get("type") == "transfer" and obj.get("to_id") == self.cfg.treasurer and owner.id != self.cfg.treasurer:
            clerk._log("rejected", owner=owner.id, code="use_contribution")
            return [self._rejected(clerk, owner, "use_contribution")]
        return None

    def _rejected(self, clerk, owner, code: str) -> Post:
        plain = {
            "date_outside_trip": f"That date is outside this trip's collection dates, {day_month(self.cfg.collect_from)} to {day_month(clerk.trip.end)}.",
            "possible_duplicate": "You already have a claim for this amount on this day. Add the UPI reference to tell them apart.",
            "duplicate_reference": "That UPI reference has already been used on this trip.",
            "use_contribution": "A payment to the person holding the money is filed as a contribution.",
        }.get(code)
        return Post(owner.desk_room, f"Rejected: {code}. {plain + ' ' if plain else ''}Nothing was recorded.")

    def _from_human(self, clerk, owner, body: str) -> list[Post] | None:
        for rx, act in ((_RELEASE, self._release), (_DROP, self._drop), (_WITHDRAW, self._withdraw)):
            if m := rx.match(body):
                return act(clerk, owner, m)
        is_treasurer = owner.id == self.cfg.treasurer
        if is_treasurer and (m := _CALL_LOOSE.match(body)):
            return self._call(clerk, owner, m.group(1))
        for rx, act in ((_CONFIRM, self._confirm), (_ASK, self._ask), (_REJECT, self._reject), (_RECHECK, self._recheck), (_CORRECT, self._correct), (_CANCEL, self._cancel)):
            if m := rx.match(body):
                return act(clerk, owner, m) if is_treasurer else self._only_treasurer(clerk, owner)
        if _CALL.match(body):
            return self._only_treasurer(clerk, owner)
        return None

    def _only_treasurer(self, clerk, owner) -> list[Post]:
        clerk._log("refused", owner=owner.id, reason="only_treasurer")
        return [Post(owner.desk_room, f"Refused: only_treasurer. Only {clerk._name(self.cfg.treasurer)} can do that.")]

    def _refuse(self, clerk, owner, code: str, plain: str = "") -> list[Post]:
        clerk._log("refused", owner=owner.id, reason=code)
        return [Post(owner.desk_room, f"Refused: {code}.{' ' + plain if plain else ''}")]

    def _not_now(self, clerk, owner, pid: str) -> list[Post]:
        state = self.status(pid)
        code = f"already_{state}" if state in ("confirmed", "rejected", "withdrawn", "dropped", "submitted") else "not_available"
        return self._refuse(clerk, owner, code)

    # ---- writing one decision -----------------------------------------------------------

    def _private(self, clerk, sender: str, kind: str, target: str) -> None:
        self.decisions.append(Decision(0, clerk.now(), sender, kind, target))

    def _publish(self, clerk, text: str, sender: str, kind: str, target: str, basis: str | None = None, code: str | None = None) -> Post:
        """The room is the record, so a decision and its post are made together."""
        self._seq += 1
        tagged, self._hash = chain_tag(text, self._seq, self._hash)
        self.decisions.append(Decision(self._seq, clerk.now(), sender, kind, target, basis, code))
        return Post(clerk.group_room, tagged)

    def _when(self, clerk) -> str:
        moment = datetime.fromisoformat(clerk.now()).astimezone(IST)
        return f"{moment.day} {moment:%b}, {moment:%H:%M} IST"

    # ---- from a member's agent ----------------------------------------------------------

    def _file(self, clerk, owner, item: Contribution) -> list[Post]:
        key = (owner.id, item.ref)
        if key in clerk._seen_refs:
            clerk._log("rejected", owner=owner.id, code="duplicate_ref")
            return [Post(owner.desk_room, "Rejected: duplicate_ref. Already on this desk.")]
        clerk._seen_refs.add(key)
        for rec in self.records.values():
            if self.status(rec.id) in ("dropped", "withdrawn"):
                continue
            if item.ref_token and rec.claim.ref_token == item.ref_token:
                clerk._log("rejected", owner=owner.id, code="duplicate_reference")
                return [self._rejected(clerk, owner, "duplicate_reference")]
            same = (rec.claim.owner_id, rec.claim.date, rec.claim.amount_paise) == (owner.id, item.date, item.amount_paise)
            if same and not item.ref_token and not rec.claim.ref_token:
                clerk._log("rejected", owner=owner.id, code="possible_duplicate")
                return [self._rejected(clerk, owner, "possible_duplicate")]
        pid = clerk._next("p")
        self.records[pid] = ContributionRecord(pid, item)
        clerk._log("held", id=pid, owner=owner.id)
        return [Post(owner.desk_room, f"**{pid}** ready [ref {item.ref}]: you paid {clerk._name(item.to_id)} {clerk._money(item.amount_paise)} on {day_month(item.date)}.\nReply `release {pid}` to share it, or `drop {pid}` to keep it private.")]

    def _match(self, clerk, owner, item: PotMatch) -> list[Post]:
        rec = self.records.get(item.contribution_id)
        if rec is None or self.status(rec.id) != "submitted":
            clerk._log("rejected", owner=owner.id, code="no_such_question")
            return [Post(owner.desk_room, "Rejected: no_such_question. Nothing was recorded.")]
        rec.candidate, rec.credit_token = item.result, item.credit_token
        self._private(clerk, owner.agent_sender, "match", rec.id)
        how = f"`confirm {rec.id} manual`, " if item.result == "none" else (f"`confirm {rec.id}`, " if item.result == "exact" else "")
        return [Post(owner.desk_room, f"**{rec.id}** to check: {clerk._name(rec.claim.owner_id)} says they paid you {clerk._money(rec.claim.amount_paise)} on {day_month(rec.claim.date)}. Your agent: {SAID[item.result]}.\nReply {how}`ask {rec.id} <reason>` or `reject {rec.id} <reason>`.")]

    # ---- from a member -----------------------------------------------------------------

    def _mine(self, owner, pid: str) -> ContributionRecord | None:
        rec = self.records.get(pid.lower())
        return rec if rec is not None and rec.claim.owner_id == owner.id else None

    def _no_such(self, clerk, owner, pid: str) -> list[Post]:
        clerk._log("refused", owner=owner.id, reason="unknown_claim")
        return [Post(owner.desk_room, f"Refused: there is no {pid.lower()} on this desk.")]

    def _check_message(self, rec: ContributionRecord) -> str:
        c = rec.claim
        return f"KITTY_POT_CHECK {rec.id} from={c.owner_id} amount_paise={c.amount_paise} date={c.date.isoformat()} ref_token={c.ref_token or '-'}"

    def _release(self, clerk, owner, m) -> list[Post]:
        rec = self._mine(owner, m.group(1))
        if rec is None:
            return self._no_such(clerk, owner, m.group(1))
        if self.status(rec.id) != "held":
            return self._not_now(clerk, owner, rec.id)
        c = rec.claim
        last4 = f" · ref …{c.ref_last4}" if c.ref_last4 else ""
        treasurer = clerk.people[self.cfg.treasurer]
        text = f"**{rec.id}** {owner.name} says they paid {treasurer.name} {clerk._money(c.amount_paise)} on {day_month(c.date)}{last4}. Not counted yet."
        return [
            Post(owner.desk_room, f"{rec.id} released."),
            self._publish(clerk, text, owner.human_sender, "submitted", rec.id),
            Post(treasurer.desk_room, self._check_message(rec)),
        ]

    def _drop(self, clerk, owner, m) -> list[Post]:
        rec = self._mine(owner, m.group(1))
        if rec is None:
            return self._no_such(clerk, owner, m.group(1))
        if self.status(rec.id) != "held":
            return self._not_now(clerk, owner, rec.id)
        rec.dropped = True
        clerk._log("dropped", id=rec.id, by=owner.id)
        return [Post(owner.desk_room, f"{rec.id} dropped. The group never saw it.")]

    def _withdraw(self, clerk, owner, m) -> list[Post]:
        rec = self._mine(owner, m.group(1))
        if rec is None:
            return self._no_such(clerk, owner, m.group(1))
        if self.status(rec.id) not in ("submitted", "recipient_review", "needs_clarification", "rejected"):
            return self._not_now(clerk, owner, rec.id)
        reason = (m.group(2) or "mistaken").lower()
        if reason not in C.WITHDRAW_REASONS:
            return self._refuse(clerk, owner, "bad_reason")
        text = f"**{rec.id} withdrawn by {owner.name}.** {WITHDRAW_WHY[reason]} It isn't counted, so balances don't change."
        return [
            Post(owner.desk_room, f"{rec.id} withdrawn."),
            self._publish(clerk, text, owner.human_sender, "withdraw", rec.id, code=reason),
            Post(clerk.people[self.cfg.treasurer].desk_room, f"{rec.id} withdrawn."),
            *self.square_posts(clerk),
        ]

    # ---- from the treasurer, on their own desk ---------------------------------------------

    def _call(self, clerk, owner, rest: str) -> list[Post]:
        words = rest.split()
        if not words or not _AMOUNT.fullmatch(words[0]):
            return self._refuse(clerk, owner, "bad_amount", "Say how much, like `call 5000`.")
        whole, _, frac = words[0].replace("₹", "").replace(",", "").partition(".")
        amount = int(whole) * 100 + int((frac + "00")[:2])
        if not 1 <= amount <= C.MAX_AMOUNT_PAISE:
            return self._refuse(clerk, owner, "bad_amount", "Say how much, like `call 5000`.")
        by_name = {**{p: p for p in clerk.people}, **{clerk.people[p].name.lower(): p for p in clerk.people}}
        named = [by_name.get(w.lower()) for w in words[1:]]
        if None in named:
            return self._refuse(clerk, owner, "unknown_person", "Use names from this trip.")
        if self.cfg.treasurer in named:
            return self._refuse(clerk, owner, "not_asked", "You hold the money, so you aren't asked for any.")
        everyone = not named
        who = tuple(p for p in clerk.trip.participants if p != self.cfg.treasurer) if everyone else tuple(dict.fromkeys(named))
        kid = clerk._next("k")
        self.calls[kid] = (amount, who)
        whom = "each person" if everyone else ", ".join(clerk._name(p) for p in who)
        return [self._publish(clerk, f"**{kid}** {owner.name} asked {whom} for {clerk._money(amount)}.", owner.human_sender, "call", kid)]

    def _cancel(self, clerk, owner, m) -> list[Post]:
        kid = m.group(1).lower()
        if kid not in self.calls or kid in self.cancelled:
            return self._refuse(clerk, owner, "no_such_call")
        self.cancelled.add(kid)
        return [self._publish(clerk, f"**{kid} cancelled by {owner.name}.**", owner.human_sender, "cancel", kid)]

    def _credit_problem(self, rec: ContributionRecord) -> str | None:
        if rec.credit_token is None:
            return None
        if rec.claim.ref_token and rec.claim.ref_token != rec.credit_token:
            return "reference_differs"
        if any(o.id != rec.id and o.credit_token == rec.credit_token and self.status(o.id) == "confirmed" for o in self.records.values()):
            return "credit_already_used"
        return None

    def _confirmed_posts(self, clerk, owner, rec: ContributionRecord, basis: str) -> list[Post]:
        how = f"checked against {owner.name}'s records" if basis == "records" else f"on {owner.name}'s word"
        c = rec.claim
        text = f"**{rec.id} confirmed received by {owner.name}:** {clerk._name(c.owner_id)} paid {clerk._money(c.amount_paise)} · {how} · {self._when(clerk)}."
        post = self._publish(clerk, text, owner.human_sender, "confirm", rec.id, basis)
        return [
            Post(owner.desk_room, f"{rec.id} confirmed."),
            post,
            Post(clerk.people[c.owner_id].desk_room, f"{rec.id}: {owner.name} confirmed it arrived."),
            *self.square_posts(clerk),
        ]

    def _confirm(self, clerk, owner, m) -> list[Post]:
        pid = m.group(1).lower()
        rec = self.records.get(pid)
        if rec is None:
            return self._no_such(clerk, owner, pid)
        if self.status(pid) != "recipient_review":
            return self._not_now(clerk, owner, pid)
        if m.group(2):
            if rec.candidate != "none":
                return self._refuse(clerk, owner, "manual_only_when_nothing_found")
            return self._confirmed_posts(clerk, owner, rec, "word")
        if rec.candidate == "none":
            return self._refuse(clerk, owner, "no_candidate_use_manual", f"Your agent found nothing. If you can see it arrived, say `confirm {pid} manual`.")
        if rec.candidate == "amount_differs":
            return self._refuse(clerk, owner, "amount_differs", "Ask them to withdraw it and claim the right amount.")
        if problem := self._credit_problem(rec):
            return self._refuse(clerk, owner, problem)
        return self._confirmed_posts(clerk, owner, rec, "records")

    def _ask(self, clerk, owner, m) -> list[Post]:
        pid = m.group(1).lower()
        if pid not in self.records:
            return self._no_such(clerk, owner, pid)
        if self.status(pid) != "recipient_review":
            return self._not_now(clerk, owner, pid)
        code = (m.group(2) or "").lower()
        if code not in ASK_CODES:
            return self._refuse(clerk, owner, "bad_reason", f"Use one of: {', '.join(ASK_CODES)}.")
        rec = self.records[pid]
        text = f"**{pid} needs clarification.** {owner.name} can't confirm it yet. Not counted."
        return [
            Post(owner.desk_room, f"{pid}: asked."),
            self._publish(clerk, text, owner.human_sender, "ask", pid, code=code),
            Post(clerk.people[rec.claim.owner_id].desk_room, f"{owner.name} asks about {pid}: {code}. {ASK_PLAIN[code]}"),
        ]

    def _reject(self, clerk, owner, m) -> list[Post]:
        pid = m.group(1).lower()
        if pid not in self.records:
            return self._no_such(clerk, owner, pid)
        if self.status(pid) not in ("recipient_review", "needs_clarification"):
            return self._not_now(clerk, owner, pid)
        code = (m.group(2) or "").lower()
        if code not in REJECT_CODES:
            return self._refuse(clerk, owner, "bad_reason", f"Use one of: {', '.join(REJECT_CODES)}.")
        text = f"**{pid} not received, says {owner.name}.** Not counted."
        return [
            Post(owner.desk_room, f"{pid}: not counted."),
            self._publish(clerk, text, owner.human_sender, "reject", pid, code=code),
            Post(clerk.people[self.records[pid].claim.owner_id].desk_room, f"KITTY_RECHECK {pid}"),
        ]

    def _recheck(self, clerk, owner, m) -> list[Post]:
        pid = m.group(1).lower()
        if pid not in self.records:
            return self._no_such(clerk, owner, pid)
        if self.status(pid) not in ("recipient_review", "needs_clarification"):
            return self._not_now(clerk, owner, pid)
        rec = self.records[pid]
        rec.candidate, rec.credit_token = None, None
        self._private(clerk, owner.human_sender, "recheck", pid)
        return [Post(owner.desk_room, self._check_message(rec))]

    def _correct(self, clerk, owner, m) -> list[Post]:
        pid = m.group(1).lower()
        if pid not in self.records:
            return self._no_such(clerk, owner, pid)
        rec, state, code = self.records[pid], self.status(pid), (m.group(2) or "").lower()
        member = clerk._name(rec.claim.owner_id)
        money = clerk._money(rec.claim.amount_paise)
        before = clerk.balances()[rec.claim.owner_id]
        if state == "confirmed" and code in CORRECT_OUT_CODES:
            after = before - rec.claim.amount_paise
            text = f"**Correction of {pid}:** {owner.name} no longer confirms it ({code}). {member}'s {money} no longer counts. {member}'s balance goes from {clerk._money(before)} to {clerk._money(after)}."
            return [Post(owner.desk_room, f"{pid} corrected: not counted."), self._publish(clerk, text, owner.human_sender, "correct_out", pid, code=code)]
        if state == "rejected" and code == "received":
            if problem := self._credit_problem(rec):
                return self._refuse(clerk, owner, problem)
            after = before + rec.claim.amount_paise
            text = f"**Correction of {pid}:** {owner.name} says it arrived after all. {member} paid {money}. {member}'s balance goes from {clerk._money(before)} to {clerk._money(after)}."
            basis = "records" if rec.credit_token else "word"
            return [Post(owner.desk_room, f"{pid} corrected: counted."), self._publish(clerk, text, owner.human_sender, "correct_in", pid, basis, code), *self.square_posts(clerk)]
        return self._refuse(clerk, owner, "cannot_correct")
