"""A trip the organiser is creating: validation and files. No Switch, no statements, no model."""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
from datetime import date, datetime, timezone
from pathlib import Path

from kitty.claims import CURRENCIES

MAX_PEOPLE = 12
MIN_PEOPLE = 2
MAX_DAYS = 60
TRIP_NAME = re.compile(r"^[^\W_][^\W_ '&-]*(?:[ '&-]+[^\W_]+)*$")
PERSON_NAME = re.compile(r"^[A-Za-z]{2,14}$")
STATEMENT_NAME = re.compile(r"^[A-Z][A-Z .'-]{1,29}$")
# Words the agent reads as commands, and names its own files use. A person can't be called one.
RESERVED = {
    "it", "was", "with", "and", "the", "just", "only", "shared", "yes", "me", "everyone", "all", "personal", "mine", "no", "nope", "reject", "skip",
    "but", "except", "not", "without", "both", "group", "split", "together", "clerk", "verify", "runtime", "status", "secrets", "invites", "kittyhost", "start", "go", "review", "rescan",
}
SYNTHETIC_NOTICE = "All statements are invented. No real person, account or transaction."


class Invalid(ValueError):
    """Field-by-field problems, written for the person filling in the form."""

    def __init__(self, fields: dict[str, str]) -> None:
        super().__init__("invalid")
        self.fields = fields


def write_json(path: Path, doc: object, mode: int = 0o600) -> None:
    """Atomic, so a crash can't leave half a file, and never briefly world-readable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, "w") as handle:
        json.dump(doc, handle, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


def read_json(path: Path, default: object = None) -> object:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default


def _date(value: object) -> date | None:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def clean_trip(body: dict) -> dict:
    errors: dict[str, str] = {}
    name = str(body.get("name") or "").strip()
    if not 2 <= len(name) <= 40 or not TRIP_NAME.fullmatch(name):
        errors["name"] = "Give the trip a name of 2 to 40 letters or numbers, like Coorg long weekend."
    start, end = _date(body.get("start")), _date(body.get("end"))
    if start is None:
        errors["start"] = "Choose the day the trip started."
    if end is None:
        errors["end"] = "Choose the day the trip ended."
    if start and end:
        if end < start:
            errors["end"] = "The last day can't be before the first."
        elif (end - start).days > MAX_DAYS:
            errors["end"] = f"Kitty handles trips of up to {MAX_DAYS} days."
    currency = body.get("currency")
    if currency not in CURRENCIES:
        errors["currency"] = "Choose one of the listed currencies."
    if errors:
        raise Invalid(errors)
    return {"name": name, "start": start.isoformat(), "end": end.isoformat(), "currency": currency}


def clean_person(body: dict, existing: dict[str, dict]) -> tuple[str, dict]:
    errors: dict[str, str] = {}
    name = str(body.get("name") or "").strip()
    slug = name.lower()
    if not PERSON_NAME.fullmatch(name):
        errors["name"] = "Use one name of 2 to 14 letters. The group sees this."
    elif slug in RESERVED:
        errors["name"] = "Kitty uses that word as a command. Try a nickname."
    elif slug in existing:
        errors["name"] = "Someone on this trip already goes by that name."
    default = name.upper()
    statement = re.sub(r"\s+", " ", str(body.get("statement_name") or default)).strip().upper()
    if not STATEMENT_NAME.fullmatch(statement):
        errors["statement_name"] = "Use the name exactly as it shows on a payment, like PRIYA S."
    elif any(p["statement_name"] == statement for p in existing.values()):
        errors["statement_name"] = "Someone else on this trip shows up under that name."
    if errors:
        raise Invalid(errors)
    return slug, {"name": name[0].upper() + name[1:], "statement_name": statement}


def _short(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:20].strip("-") or "trip"


class TripStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def folder(self, trip_id: str) -> Path:
        if not re.fullmatch(r"[a-z0-9-]{1,40}", trip_id):
            raise KeyError(trip_id)
        path = self.root / trip_id
        if not (path / "trip.json").exists():
            raise KeyError(trip_id)
        return path

    def create(self, fields: dict, key: str | None = None) -> dict:
        """`key` comes from the page, so a double click or a resend can't make two trips."""
        if key:
            for trip in self.all():
                if trip.get("create_key") == key:
                    return trip
        trip_id = f"{_short(fields['name'])}-{secrets.token_hex(2)}"
        doc = {"trip_id": trip_id, **fields, "people": {}, "synthetic_notice": SYNTHETIC_NOTICE, "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "create_key": key}
        folder = self.root / trip_id
        for sub in ("", "state", "people", "logs"):
            (folder / sub).mkdir(parents=True, exist_ok=True, mode=0o700)
        write_json(folder / "trip.json", doc, 0o644)
        return doc

    def get(self, trip_id: str) -> dict:
        return json.loads((self.folder(trip_id) / "trip.json").read_text())

    def save(self, doc: dict) -> None:
        write_json(self.folder(doc["trip_id"]) / "trip.json", doc, 0o644)

    def all(self) -> list[dict]:
        if not self.root.exists():
            return []
        docs = []
        for f in self.root.glob("*/trip.json"):
            try:
                docs.append(json.loads(f.read_text()))
            except (OSError, json.JSONDecodeError):
                continue
        return sorted(docs, key=lambda d: d.get("created_at", ""), reverse=True)

    def delete(self, trip_id: str) -> None:
        shutil.rmtree(self.folder(trip_id))
