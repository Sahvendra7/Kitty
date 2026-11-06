"""Deterministic statement reading. No LLM.

Parses a synthetic CSV and produces the smallest view each later step needs:

  - `Row` keeps everything, and never leaves the person's own process.
  - `model_view` is the only text a model ever sees: out-of-window lines, balances,
    account numbers, refs, incoming money and payments between friends are all removed.

Balance and account columns are read past and never stored.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

LONG_DIGITS = re.compile(r"\d{4,}")
UPI = re.compile(r"^UPI/(DR|CR|REV)/([^/]+)/")


@dataclass(frozen=True)
class Row:
    row_id: str
    day: date
    time: str
    narration: str
    ref: str
    debit: int
    credit: int

    @property
    def is_debit(self) -> bool:
        return self.debit > 0


def to_paise(text: str) -> int:
    text = text.strip()
    if not text:
        return 0
    if not re.fullmatch(r"\d+(\.\d{1,2})?", text):
        raise ValueError("amount is not a plain decimal")
    whole, _, frac = text.partition(".")
    return int(whole) * 100 + int((frac + "00")[:2])


def parse(path: Path) -> list[Row]:
    with path.open(newline="") as handle:
        return [
            Row(f"r{i}", date.fromisoformat(rec["txn_date"]), rec["txn_time"], rec["narration"].strip(), rec["ref"].strip(), to_paise(rec["debit"]), to_paise(rec["credit"]))
            for i, rec in enumerate(csv.DictReader(handle), start=1)
        ]


def in_window(rows: list[Row], start: date, end: date) -> list[Row]:
    return [r for r in rows if start <= r.day <= end]


def counterparty(row: Row, directory: dict[str, str]) -> str | None:
    """Which friend a UPI line names, if any. `directory` maps statement name to person id."""
    m = UPI.match(row.narration)
    return directory.get(m.group(2).strip()) if m else None


def spending_candidates(rows: list[Row], directory: dict[str, str]) -> list[Row]:
    """Outgoing, non-transfer lines. The only lines worth asking a model about."""
    return [r for r in rows if r.is_debit and counterparty(r, directory) is None]


def outgoing_transfers(rows: list[Row], directory: dict[str, str]) -> list[tuple[Row, str]]:
    return [(r, who) for r in rows if r.is_debit and (who := counterparty(r, directory)) is not None]


def model_view(row: Row) -> dict:
    """The line as a model may see it. Long digit runs are masked."""
    return {"row_id": row.row_id, "date": row.day.isoformat(), "time": row.time, "narration": LONG_DIGITS.sub("####", row.narration), "amount_rupees": row.debit / 100}


def find_credit(rows: list[Row], amount: int, around: date, from_person: str, directory: dict[str, str], slack_days: int = 2) -> Row | None:
    """Did this much arrive from this person near this date? A reversal is not a payment."""
    for r in rows:
        if r.credit == amount and abs((r.day - around).days) <= slack_days and r.narration.startswith("UPI/CR/") and counterparty(r, directory) == from_person:
            return r
    return None


def find_reversal(rows: list[Row], debit: Row, slack_days: int = 5) -> Row | None:
    """A later credit of the same amount and ref is what a failed payment looks like."""
    limit = debit.day + timedelta(days=slack_days)
    for r in rows:
        if r.credit == debit.debit and r.ref == debit.ref and debit.day <= r.day <= limit and (r.day, r.time) >= (debit.day, debit.time) and r.row_id != debit.row_id:
            return r
    return None
