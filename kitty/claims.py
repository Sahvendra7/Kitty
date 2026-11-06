"""The strict shape of everything that may cross from a person's side to the clerk.

There is no field that can carry a merchant name, a note or a statement line. Every string
is a member of a closed set or matches a narrow pattern, and unknown keys are rejected. The
clerk runs this on every message, whatever the agent's own code already checked.

Pure. No I/O and no LLM.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date

PREFIX = "KITTY/1 "
CATEGORIES = ("stay", "travel", "food", "groceries", "fuel", "activity", "shopping", "other")
CURRENCY = "INR"
# Two-decimal currencies only, because amounts are whole minor units. One trip uses one currency, and Kitty never converts.
CURRENCIES = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£"}
MAX_AMOUNT_PAISE = 100_000_000  # ₹10,00,000
REF = re.compile(r"^[a-z0-9]{1,12}$")
CLAIM_ID = re.compile(r"^[ct][0-9]{1,6}$")
WITHDRAW_REASONS = ("payment_failed", "mistaken", "duplicate")


class ClaimError(ValueError):
    """Carries a fixed code. Never carries the offending value, so a rejection can be posted safely."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Trip:
    trip_id: str
    start: date
    end: date
    participants: tuple[str, ...]
    currency: str = CURRENCY


@dataclass(frozen=True)
class Expense:
    ref: str
    trip_id: str
    owner_id: str
    payer_id: str
    date: date
    amount_paise: int
    currency: str
    category: str
    shares: tuple[tuple[str, int], ...]

    @property
    def participant_ids(self) -> tuple[str, ...]:
        return tuple(p for p, _ in self.shares)

    def fingerprint(self) -> tuple:
        return (self.owner_id, self.date, self.amount_paise, self.category, self.shares)


@dataclass(frozen=True)
class Transfer:
    ref: str
    trip_id: str
    owner_id: str
    from_id: str
    to_id: str
    date: date
    amount_paise: int
    currency: str


@dataclass(frozen=True)
class TransferAnswer:
    """A person's released answer to "did this reach you?". Yes or no, and nothing else."""

    ref: str
    trip_id: str
    owner_id: str
    transfer_id: str
    received: bool


def _exact_keys(obj: dict, required: set[str]) -> None:
    keys = set(obj)
    if keys - required:
        raise ClaimError("unknown_field")
    if required - keys:
        raise ClaimError("missing_field")


def _strict_int(value: object, code: str) -> int:
    # bool is a subclass of int, and JSON floats such as 100.0 must not pass as money.
    if isinstance(value, bool) or not isinstance(value, int):
        raise ClaimError(code)
    return value


def _amount(value: object) -> int:
    amount = _strict_int(value, "bad_amount")
    if not 1 <= amount <= MAX_AMOUNT_PAISE:
        raise ClaimError("bad_amount")
    return amount


def _trip_date(value: object, trip: Trip) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ClaimError("bad_date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ClaimError("bad_date") from exc
    if not trip.start <= parsed <= trip.end:
        raise ClaimError("date_outside_trip")
    return parsed


def _ref(value: object) -> str:
    if not isinstance(value, str) or not REF.fullmatch(value):
        raise ClaimError("bad_ref")
    return value


def _participant(value: object, trip: Trip) -> str:
    if not isinstance(value, str) or value not in trip.participants:
        raise ClaimError("unknown_participant")
    return value


def _common(obj: dict, trip: Trip, desk_owner: str) -> None:
    if obj.get("trip_id") != trip.trip_id:
        raise ClaimError("wrong_trip")
    if obj.get("owner_id") != desk_owner:
        raise ClaimError("owner_mismatch")


def _expense(obj: dict, trip: Trip, desk_owner: str) -> Expense:
    _exact_keys(obj, {"v", "type", "ref", "trip_id", "owner_id", "payer_id", "date", "amount_paise", "currency", "category", "participant_ids", "shares"})
    _common(obj, trip, desk_owner)
    payer = _participant(obj["payer_id"], trip)
    if payer != desk_owner:
        raise ClaimError("payer_not_owner")
    amount = _amount(obj["amount_paise"])
    if obj["currency"] != trip.currency:
        raise ClaimError("bad_currency")
    if obj["category"] not in CATEGORIES:
        raise ClaimError("bad_category")
    raw_ids, raw_shares = obj["participant_ids"], obj["shares"]
    if not isinstance(raw_ids, list) or not isinstance(raw_shares, dict) or not raw_ids:
        raise ClaimError("bad_shares")
    if len(set(map(str, raw_ids))) != len(raw_ids) or set(raw_shares) != set(map(str, raw_ids)):
        raise ClaimError("bad_shares")
    shares = []
    for pid in raw_ids:
        pid = _participant(pid, trip)
        value = _strict_int(raw_shares[pid], "bad_shares")
        if value < 0:
            raise ClaimError("bad_shares")
        shares.append((pid, value))
    if sum(v for _, v in shares) != amount:
        raise ClaimError("shares_do_not_sum")
    return Expense(_ref(obj["ref"]), trip.trip_id, desk_owner, payer, _trip_date(obj["date"], trip), amount, trip.currency, obj["category"], tuple(shares))


def _transfer(obj: dict, trip: Trip, desk_owner: str) -> Transfer:
    _exact_keys(obj, {"v", "type", "ref", "trip_id", "owner_id", "from_id", "to_id", "date", "amount_paise", "currency"})
    _common(obj, trip, desk_owner)
    sender, receiver = _participant(obj["from_id"], trip), _participant(obj["to_id"], trip)
    if sender != desk_owner:
        raise ClaimError("payer_not_owner")
    if sender == receiver:
        raise ClaimError("self_transfer")
    if obj["currency"] != trip.currency:
        raise ClaimError("bad_currency")
    return Transfer(_ref(obj["ref"]), trip.trip_id, desk_owner, sender, receiver, _trip_date(obj["date"], trip), _amount(obj["amount_paise"]), trip.currency)


def _answer(obj: dict, trip: Trip, desk_owner: str) -> TransferAnswer:
    _exact_keys(obj, {"v", "type", "ref", "trip_id", "owner_id", "transfer_id", "received"})
    _common(obj, trip, desk_owner)
    tid = obj["transfer_id"]
    if not isinstance(tid, str) or not CLAIM_ID.fullmatch(tid):
        raise ClaimError("bad_transfer_id")
    if not isinstance(obj["received"], bool):
        raise ClaimError("bad_answer")
    return TransferAnswer(_ref(obj["ref"]), trip.trip_id, desk_owner, tid, obj["received"])


PARSERS = {"expense": _expense, "transfer": _transfer, "transfer_answer": _answer}


def parse(body: str, trip: Trip, desk_owner: str) -> Expense | Transfer | TransferAnswer:
    """Parse one typed message from a person's desk, or raise ClaimError with a fixed code."""
    if not body.startswith(PREFIX):
        raise ClaimError("not_a_claim")
    try:
        obj = json.loads(body[len(PREFIX):])
    except json.JSONDecodeError as exc:
        raise ClaimError("bad_json") from exc
    if not isinstance(obj, dict):
        raise ClaimError("bad_json")
    if obj.get("v") != 1:
        raise ClaimError("bad_version")
    parser = PARSERS.get(obj.get("type"))  # type: ignore[arg-type]
    if parser is None:
        raise ClaimError("bad_type")
    return parser(obj, trip, desk_owner)


def even_shares(amount_paise: int, participants: list[str]) -> dict[str, int]:
    """Split with whole paise. The leftover goes to the first people listed, so it always sums."""
    if not participants:
        raise ClaimError("bad_shares")
    base, extra = divmod(amount_paise, len(participants))
    return {p: base + (1 if i < extra else 0) for i, p in enumerate(participants)}


def rupees(paise: int) -> str:
    return money(paise, "INR")


def money(minor: int, currency: str = CURRENCY) -> str:
    """Integer minor units to display text. Rupees group in lakhs; the others group in thousands."""
    sign = "-" if minor < 0 else ""
    whole, frac = divmod(abs(minor), 100)
    if currency != "INR":
        return f"{sign}{CURRENCIES[currency]}{whole:,}.{frac:02d}"
    text = str(whole)
    if len(text) > 3:
        head, tail = text[:-3], text[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        text = ",".join(groups + [tail])
    return f"{sign}₹{text}.{frac:02d}"
