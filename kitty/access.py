"""Who may open a person's app, on a trip made in the app.

The organiser gets one single-use link per person. Opening it swaps the link for a session cookie, so
a link forwarded later, or copied from history, opens nothing. Only hashes are kept on the person's
side. A new link cancels the old link and the old session.

The original three-person demo has no access file, and so keeps its open local ports for the stage.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from pathlib import Path

from kitty.trips import read_json, write_json


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def cookie_name(trip_id: str, person: str) -> str:
    # Cookies ignore ports, so every person on every trip needs their own name.
    return "kitty_" + hashlib.sha256(f"{trip_id}/{person}".encode()).hexdigest()[:16]


def new_invite(path: Path) -> str:
    token = secrets.token_urlsafe(24)
    write_json(path, {"invite_hash": _hash(token), "invite_used": False, "session_hash": None})
    return token


def claim(path: Path, token: str) -> str | None:
    """Swap a fresh invite for a session token, or return None."""
    doc = read_json(path)
    if not doc or doc.get("invite_used") or not hmac.compare_digest(doc["invite_hash"], _hash(token)):
        return None
    session = secrets.token_urlsafe(32)
    write_json(path, {**doc, "invite_used": True, "session_hash": _hash(session)})
    return session


def valid_session(path: Path, session: str | None) -> bool:
    doc = read_json(path)
    return bool(doc and session and doc.get("session_hash") and hmac.compare_digest(doc["session_hash"], _hash(session)))


def joined(path: Path) -> bool:
    doc = read_json(path)
    return bool(doc and doc.get("invite_used"))
