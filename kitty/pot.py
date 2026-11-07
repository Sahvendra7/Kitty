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
from dataclasses import dataclass
from datetime import date

from kitty import claims as C
from kitty import ledger as L

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
