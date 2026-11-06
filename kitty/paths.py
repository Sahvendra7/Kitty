"""Where one trip's files live.

The original three-person demo keeps its files where it always did: `state/`, `people/`, `logs/` and
`data/synthetic/trip.json`. A trip created in the app gets its own folder, and its processes are
started with KITTY_TRIP_DIR pointing at it. Nothing else about those processes changes.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class TripPaths:
    state: Path
    people: Path
    logs: Path
    trip_json: Path
    in_app: bool  # True for a trip created in the app, False for the original demo.

    def statement(self, person: str) -> Path:
        return self.people / person / "statement.csv"

    def statement_meta(self, person: str) -> Path:
        return self.people / person / "statement.meta.json"

    def person_status(self, person: str) -> Path:
        """Yes-or-no progress the organiser may see. Holds nothing read from a statement."""
        return self.people / person / "progress.json"

    def access(self, person: str) -> Path:
        return self.state / f"{person}.access.json"


def for_trip(folder: Path) -> TripPaths:
    return TripPaths(folder / "state", folder / "people", folder / "logs", folder / "trip.json", True)


def legacy() -> TripPaths:
    return TripPaths(ROOT / "state", ROOT / "people", ROOT / "logs", ROOT / "data/synthetic/trip.json", False)


def current() -> TripPaths:
    folder = os.environ.get("KITTY_TRIP_DIR")
    return for_trip(Path(folder)) if folder else legacy()


def trips_root() -> Path:
    return Path(os.environ.get("KITTY_TRIPS_DIR", ROOT / "trips"))
