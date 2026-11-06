"""Starts and stops the processes of one trip made in the app: one clerk, and an agent and an app per person.

Each process gets the trip's folder, and reads only its own files, as in scripts/run_all.py. They run in
their own session, so restarting the home app doesn't restart an agent and lose its place in a review.
"""
from __future__ import annotations

import logging
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

from kitty.paths import ROOT, for_trip
from kitty.provision import StartFailed
from kitty.trips import read_json, write_json

log = logging.getLogger("kitty.runtime")
FIRST_PORT = 7200
BLOCK = 20


def _free(port: int) -> bool:
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def allocate(trips_root: Path, people: list[str]) -> dict:
    """A block of ports not promised to any other trip, and free right now."""
    taken = set()
    for f in trips_root.glob("*/state/runtime.json"):
        doc = read_json(f) or {}
        taken.add(doc.get("board"))
        taken.update((doc.get("apps") or {}).values())
    base = FIRST_PORT
    while True:
        ports = list(range(base, base + 1 + len(people)))
        if not set(ports) & taken and all(_free(p) for p in ports):
            return {"board": ports[0], "apps": dict(zip(people, ports[1:]))}
        base += BLOCK
        if base > 9000:
            raise StartFailed("no free ports")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def stop(folder: Path) -> None:
    path = for_trip(folder).state / "runtime.json"
    doc = read_json(path) or {}
    for pid in doc.get("pids", []):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    if doc:
        write_json(path, {**doc, "pids": []})


def healthy(doc: dict) -> bool:
    if not doc or not doc.get("pids") or not all(_alive(p) for p in doc["pids"]):
        return False
    try:
        return httpx.get(f"http://127.0.0.1:{doc['board']}/api/board", timeout=2).status_code == 200 and all(httpx.get(f"http://127.0.0.1:{p}/healthz", timeout=2).status_code == 200 for p in doc["apps"].values())
    except httpx.HTTPError:
        return False


def status(folder: Path) -> dict:
    return read_json(for_trip(folder).state / "runtime.json") or {}


def start(folder: Path, trip: dict, trips_root: Path, wait_seconds: float = 45) -> dict:
    paths = for_trip(folder)
    path = paths.state / "runtime.json"
    existing = read_json(path) or {}
    if healthy(existing):
        return existing
    stop(folder)
    time.sleep(0.5)
    people = list(trip["people"])
    if existing.get("board") and all(_free(p) for p in (existing["board"], *existing["apps"].values())):
        ports = {"board": existing["board"], "apps": existing["apps"]}  # Keep links people already hold working.
    else:
        ports = allocate(trips_root, people)
    env = {**os.environ, "PYTHONPATH": str(ROOT), "KITTY_TRIP_DIR": str(folder), "KITTY_BOARD_PORT": str(ports["board"])}
    paths.logs.mkdir(parents=True, exist_ok=True, mode=0o700)

    def spawn(label: str, *args: str) -> int:
        handle = (paths.logs / f"{label}.log").open("a")
        return subprocess.Popen([sys.executable, "-m", *args], cwd=ROOT, stdout=handle, stderr=handle, env=env, start_new_session=True).pid

    pids = [spawn("clerk", "kitty.clerk_daemon")]
    time.sleep(2)
    for person in people:
        pids.append(spawn(person, "kitty.agent_runner", person))
        pids.append(spawn(f"{person}-app", "kitty.person_app", person, str(ports["apps"][person])))
    doc = {**ports, "pids": pids}
    write_json(path, doc, 0o644)
    end = time.time() + wait_seconds
    while time.time() < end:
        if not all(_alive(p) for p in pids):
            stop(folder)
            raise StartFailed("a process exited")
        if healthy(doc):
            return doc
        time.sleep(1)
    stop(folder)
    raise StartFailed("not healthy in time")
