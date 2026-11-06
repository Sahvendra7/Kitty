"""Accepts one person's synthetic CSV statement. Local and deterministic: no model, no Switch, no network.

Every message here gives a line and a column, and never repeats a value from the file, so an error
can be shown, logged or forwarded without carrying a statement line along with it.

The file is rewritten from the validated fields, so nothing that failed to parse can reach the
agent. What the model provider may later receive is decided by `statements.model_view`, as before. This
module doesn't add to it, and `model_preview` calls that same code, so the preview can't drift from it.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from kitty import claims as C
from kitty import statements as S

HEADER = ("txn_date", "txn_time", "narration", "ref", "debit", "credit", "balance", "account")
MAX_BYTES = 256 * 1024
MAX_ROWS = 2000
MAX_SHOWN_ERRORS = 8
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_TIME = re.compile(r"([01]\d|2[0-3]):[0-5]\d")
_NARRATION = re.compile(r"[A-Za-z0-9][A-Za-z0-9 *./&'#:,()_-]{0,119}")
_REF = re.compile(r"[A-Za-z0-9-]{1,32}")
_AMOUNT = re.compile(r"\d{1,9}(\.\d{1,2})?")
_BALANCE = re.compile(r"-?\d{1,10}(\.\d{1,2})?")
_MASKED_ACCOUNT = re.compile(r"X{4,12}\d{4}")
_LONG_NUMBER = re.compile(r"\d{12,}")
NOT_CSV = (b"PK\x03\x04", b"%PDF", b"\xd0\xcf\x11\xe0", b"\x89PNG", b"\xff\xd8\xff", b"GIF8")
REAL_DATA_HINT = "Kitty takes made-up statements only."


@dataclass(frozen=True)
class Problem:
    code: str
    message: str
    line: int | None = None
    column: str | None = None

    def view(self) -> dict:
        return {"code": self.code, "message": self.message, "line": self.line, "column": self.column}


class Rejected(ValueError):
    def __init__(self, problems: list[Problem], total: int | None = None) -> None:
        super().__init__(problems[0].code)
        self.problems = problems
        self.total = total or len(problems)


@dataclass(frozen=True)
class Accepted:
    lines: tuple[tuple[str, ...], ...]  # Validated, in file order.

    @property
    def text(self) -> str:
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow(HEADER)
        writer.writerows(self.lines)
        return out.getvalue()

    @property
    def fingerprint(self) -> str:
        """Same lines in the same order means the same file, whatever its line endings or BOM."""
        return hashlib.sha256(self.text.encode()).hexdigest()


def _one(code: str, message: str) -> Rejected:
    return Rejected([Problem(code, message)])


def check_filename(name: str | None) -> None:
    if not name or not name.lower().endswith(".csv"):
        raise _one("not_csv_name", "Kitty needs a file ending in .csv.")


def validate(data: bytes) -> Accepted:
    if not data.strip():
        raise _one("empty", "That file is empty.")
    if len(data) > MAX_BYTES:
        raise _one("too_large", f"That file is over {MAX_BYTES // 1024} KB. A trip's statement is much smaller, so check that it's the right file.")
    if data.startswith(NOT_CSV) or b"\x00" in data[:4096]:
        raise _one("not_csv", "That doesn't look like a CSV file. Export the statement as CSV, then try again.")
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise _one("not_utf8", "Kitty couldn't read that file's text. Save it as UTF-8 CSV, then try again.") from None
    if "\x00" in text:
        raise _one("not_csv", "That doesn't look like a CSV file. Export the statement as CSV, then try again.")
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        records = list(reader)
    except csv.Error:
        raise _one("bad_csv", "That file's quoting is broken, so Kitty couldn't read its rows.") from None
    records = [(i + 1, rec) for i, rec in enumerate(records) if rec]
    if not records:
        raise _one("empty", "That file is empty.")
    first_line, header = records[0]
    if tuple(c.strip() for c in header) != HEADER:
        raise Rejected([Problem("bad_header", "The first row has to be exactly: " + ",".join(HEADER), first_line)])
    body = records[1:]
    if not body:
        raise _one("no_rows", "That file has its header, but no transactions.")
    if len(body) > MAX_ROWS:
        raise _one("too_many_rows", f"That file has more than {MAX_ROWS} rows. Kitty only needs the weeks around your trip.")
    problems: list[Problem] = []
    lines: list[tuple[str, ...]] = []
    seen: dict[tuple[str, ...], int] = {}
    for number, rec in body:
        if len(rec) != len(HEADER):
            problems.append(Problem("bad_columns", f"Line {number} has {len(rec)} columns, and needs {len(HEADER)}.", number))
            continue
        fields = tuple(c.strip() for c in rec)
        row_problems = _check_row(number, fields)
        if row_problems:
            problems.extend(row_problems)
            continue
        if fields in seen:
            problems.append(Problem("duplicate_line", f"Line {number} repeats line {seen[fields]} exactly. Remove one of them.", number))
            continue
        seen[fields] = number
        lines.append(fields)
    if problems:
        raise Rejected(problems[:MAX_SHOWN_ERRORS], len(problems))
    return Accepted(tuple(lines))


def _check_row(n: int, f: tuple[str, ...]) -> list[Problem]:
    day, time, narration, ref, debit, credit, balance, account = f
    out: list[Problem] = []

    def bad(code: str, column: str, message: str) -> None:
        out.append(Problem(code, f"Line {n}, {column}: {message}", n, column))

    if not _DATE.fullmatch(day):
        bad("bad_date", "txn_date", "use a date like 2026-10-16.")
    else:
        try:
            if not 2000 <= date.fromisoformat(day).year <= 2100:
                bad("bad_date", "txn_date", "that year doesn't look right.")
        except ValueError:
            bad("bad_date", "txn_date", "that date doesn't exist.")
    if not _TIME.fullmatch(time):
        bad("bad_time", "txn_time", "use a 24-hour time like 20:15.")
    if not _NARRATION.fullmatch(narration):
        bad("bad_narration", "narration", "use up to 120 letters, numbers and simple punctuation. It can't be empty.")
    if not _REF.fullmatch(ref):
        bad("bad_ref", "ref", "use letters, numbers and hyphens, up to 32 of them.")
    amounts = []
    for column, value in (("debit", debit), ("credit", credit)):
        if value and not _AMOUNT.fullmatch(value):
            bad("bad_amount", column, "use a plain number like 1250.50, with no currency sign or commas.")
        elif value:
            amounts.append(S.to_paise(value))
    if debit and credit:
        bad("debit_and_credit", "credit", "a line has a debit or a credit, not both.")
    elif not debit and not credit:
        bad("no_amount", "debit", "a line needs a debit or a credit.")
    elif amounts and not 1 <= amounts[0] <= C.MAX_AMOUNT_PAISE:
        bad("bad_amount", "credit" if credit else "debit", "that amount is outside what Kitty accepts.")
    if balance and not _BALANCE.fullmatch(balance):
        bad("bad_amount", "balance", "use a plain number, or leave it empty.")
    if not _MASKED_ACCOUNT.fullmatch(account):
        bad("unmasked_account", "account", f"this has to be masked, like XXXXXX1234. {REAL_DATA_HINT}")
    if _LONG_NUMBER.search(narration) or _LONG_NUMBER.search(ref):
        bad("long_number", "narration" if _LONG_NUMBER.search(narration) else "ref", f"that looks like a full account or card number. {REAL_DATA_HINT}")
    return out


def merge(old: Accepted, new: Accepted) -> tuple[Accepted, int]:
    """After a review has started, a new file may only add lines.

    Rows are known by position, and lines may already be on a desk or released, so every line stays
    in place and new ones go on the end.
    """
    missing = set(old.lines) - set(new.lines)
    if missing:
        raise _one("lines_removed", f"Your agent has started on this statement, and {len(missing)} of its lines aren't in the new file. Add a later export that still has every earlier line.")
    known = set(old.lines)
    fresh = tuple(line for line in new.lines if line not in known)
    return Accepted(old.lines + fresh), len(fresh)


def read_saved(path: Path) -> Accepted:
    return validate(path.read_bytes())


def _rows(accepted: Accepted, path_free: bool = True) -> list[S.Row]:
    return [
        S.Row(f"r{i}", date.fromisoformat(f[0]), f[1], f[2], f[3], S.to_paise(f[4]), S.to_paise(f[5]))
        for i, f in enumerate(accepted.lines, start=1)
    ]


def _window(accepted: Accepted, trip: C.Trip) -> list[S.Row]:
    return S.in_window(_rows(accepted), trip.start, trip.end)


def summary(accepted: Accepted, trip: C.Trip, directory: dict[str, str]) -> dict:
    """Counts for the owner's own screen. The same sort the agent will use."""
    rows, window = _rows(accepted), _window(accepted, trip)
    spending = S.spending_candidates(window, directory)
    transfers = S.outgoing_transfers(window, directory)
    incoming = [r for r in window if not r.is_debit]
    return {"lines": len(rows), "in_trip": len(window), "outside_trip": len(rows) - len(window), "spending": len(spending), "payments_to_friends": len(transfers), "incoming": len(incoming), "first": min(r.day for r in rows).isoformat(), "last": max(r.day for r in rows).isoformat()}


def model_preview(accepted: Accepted, trip: C.Trip, directory: dict[str, str]) -> list[dict]:
    """Exactly the lines `PersonalAgent._scan` would hand to the model, for a statement with no review yet."""
    return [S.model_view(r, trip.currency) for r in S.spending_candidates(_window(accepted, trip), directory)]


SAMPLE_LINES = [
    ("{before}", "09:00", "SALARY NEFT EXAMPLE EMPLOYER", "N1001", "", "90000.00", "120000.00", "XXXXXX0000"),
    ("{d0}", "11:30", "EXAMPLE HOMESTAY BOOKING", "C2001", "12000.00", "", "108000.00", "XXXXXX0000"),
    ("{d0}", "20:40", "EXAMPLE RESTAURANT DINNER", "C2002", "3600.00", "", "104400.00", "XXXXXX0000"),
    ("{d0}", "22:10", "EXAMPLE PHARMACY", "C2003", "240.00", "", "104160.00", "XXXXXX0000"),
    ("{after}", "10:00", "EXAMPLE MONTHLY SUBSCRIPTION", "C2004", "499.00", "", "103661.00", "XXXXXX0000"),
]


def sample(start: date, end: date) -> str:
    """A made-up file with this trip's dates, so a new user can see the shape and try it."""
    from datetime import timedelta

    values = {"before": (start - timedelta(days=7)).isoformat(), "d0": start.isoformat(), "after": (end + timedelta(days=7)).isoformat()}
    return Accepted(tuple(tuple(c.format(**values) if c.startswith("{") else c for c in line) for line in SAMPLE_LINES)).text
