"""Builds a trip's accounts, agents and rooms, one recoverable step at a time.

The same result as scripts/setup_trip.py, which stays as the command-line fallback: for each person a
Switch owner, a linked Mattermost account, an agent, a private room and a desk; and a clerk who is the
only agent in the group room. Differences:

  - Progress is saved after every step, and a retry resumes from there. Anything already made is reused.
  - Passwords are written down before the call that uses them, so a crash can't strand an account.
  - `state/status.json` holds fixed codes and sentences only. It never holds a secret, an id or the text
    of a server's reply, so it's safe to show. Secrets stay in `state/secrets.json`, mode 600.
"""
from __future__ import annotations

import logging
import os
import queue
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

import httpx

from kitty import access
from kitty.paths import ROOT, for_trip
from kitty.switch_client import Agent, Gateway, Mattermost
from kitty.trips import read_json, write_json

log = logging.getLogger("kitty.provision")
CLERK_HELLO = "Kitty clerk here. Claims wait on this desk until you release them."

ERRORS = {
    "switch_unreachable": "Kitty can't reach Switch. Check that it's running, then try again.",
    "mattermost_unreachable": "Kitty can't reach Mattermost. Check that it's running, then try again.",
    "timed_out": "That took too long, and Switch or Mattermost may still be starting. Try again in a moment.",
    "service_refused": "Switch refused this step. Trying again is safe, because nothing is created twice.",
    "service_error": "Switch hit a problem on its side. Try again in a moment.",
    "start_failed": "The accounts are ready, but the trip's apps didn't start. Check logs in the trip's folder, then try again.",
    "interrupted": "Set-up stopped partway, because Kitty restarted. Try again to carry on from there.",
    "injected_fault": "A test failure was switched on for this step. Try again to carry on.",
    "unexpected": "Something went wrong on this step, and nothing was lost. Try again to carry on.",
}
STOP_EVERYTHING = {"switch_unreachable", "mattermost_unreachable"}


def classify(exc: Exception) -> str:
    if isinstance(exc, InjectedFault):
        return "injected_fault"
    if isinstance(exc, StartFailed):
        return "start_failed"
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
        try:
            return "mattermost_unreachable" if exc.request.url.port == 8065 else "switch_unreachable"
        except RuntimeError:
            return "switch_unreachable"
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return "timed_out"
    if isinstance(exc, httpx.HTTPStatusError):
        return "service_error" if exc.response.status_code >= 500 else "service_refused"
    if isinstance(exc, RefusedByService):
        return "service_refused"
    return "unexpected"


class InjectedFault(RuntimeError):
    pass


class StartFailed(RuntimeError):
    pass


class RefusedByService(RuntimeError):
    pass


class Backend(Protocol):
    def connect(self) -> None: ...
    def ensure_user(self, creds: dict) -> None: ...
    def ensure_chat_account(self, creds: dict) -> None: ...
    def link_identity(self, creds: dict) -> None: ...
    def registration_key(self, creds: dict) -> str: ...
    def register_agent(self, reg_key: str, name: str, description: str, display: str) -> tuple[str, str]: ...
    def create_room(self, agent: dict, name: str, description: str, agents: list[str], users: list[str]) -> str: ...
    def invite_agent(self, agent: dict, room_id: str, name: str) -> None: ...
    def find_channel(self, creds: dict, room_name: str) -> str: ...
    def learn_senders(self, person: dict, clerk: dict) -> dict: ...
    def announce(self, clerk: dict, room_id: str, text: str) -> None: ...


def _exists(exc: httpx.HTTPStatusError) -> bool:
    return exc.response.status_code in (400, 409)


class SwitchBackend:
    """The real services, reached the same way scripts/setup_trip.py reaches them."""

    def __init__(self, env: dict[str, str]) -> None:
        self.env = env

    def connect(self) -> None:
        self.admin = Gateway(self.env["GATEWAY_ADMIN_EMAIL"], self.env["GATEWAY_ADMIN_PASSWORD"])
        self.bridge = self.admin.default_bridge()
        self.mm_admin = Mattermost(self.env["MATTERMOST_ADMIN_USER"], self.env["MATTERMOST_ADMIN_PASSWORD"])
        self.team = self.env["MATTERMOST_TEAM_NAME"]
        # The same two settings setup_trip.py sets, for the same reasons.
        self.mm_admin.http.put("/api/v4/config/patch", json={"TeamSettings": {"MaxUsersPerTeam": 2000}, "ServiceSettings": {"EnableDesktopLandingPage": False}}).raise_for_status()
        self.admin.http.patch(f"/gateway/collaborations/{self.bridge}", json={"agent_greetings_enabled": False}).raise_for_status()

    def ensure_user(self, creds: dict) -> None:
        try:
            self.admin.create_user(creds["email"], creds["display"], creds["password"])
        except httpx.HTTPStatusError as exc:
            if not _exists(exc):
                raise
        Gateway(creds["email"], creds["password"])  # Proves that the saved password opens the account.

    def ensure_chat_account(self, creds: dict) -> None:
        self.mm_admin.create_user(creds["username"], creds["email"], creds["password"], creds["display"], self.team)

    def link_identity(self, creds: dict) -> None:
        mm = Mattermost(creds["username"], creds["password"])
        try:
            Gateway(creds["email"], creds["password"]).link_mattermost(self.bridge, mm.user_id, creds["username"], mm.token)
        except httpx.HTTPStatusError as exc:
            if not _exists(exc):
                raise
        except RuntimeError as exc:
            raise TimeoutError("the bridge's directory never listed this account") from exc

    def registration_key(self, creds: dict) -> str:
        return Gateway(creds["email"], creds["password"]).registration_key("kitty")

    def register_agent(self, reg_key: str, name: str, description: str, display: str) -> tuple[str, str]:
        agent = Agent.register(reg_key, name, description, display)
        return agent.id, agent.key

    def _agent(self, doc: dict) -> Agent:
        return Agent(doc["agent_id"], doc["api_key"], doc["agent_name"])

    def create_room(self, agent: dict, name: str, description: str, agents: list[str], users: list[str]) -> str:
        res = self._agent(agent).result("create_room", name=name, description=description, agent_names=agents, user_names=users, bridge_id=self.bridge, channel_type="channel_private", read_visibility="private", write_visibility="private")
        if res.get("failed_users"):
            raise RefusedByService("a person could not be added to a room")
        return res["id"]

    def invite_agent(self, agent: dict, room_id: str, name: str) -> None:
        self._agent(agent).result("invite_agent_to_room", room_id=room_id, agent_name=name)

    def find_channel(self, creds: dict, room_name: str) -> str:
        mm = Mattermost(creds["username"], creds["password"])
        end = time.time() + 45  # The bridge makes channels a moment after the room.
        while True:
            r = mm.http.get("/api/v4/users/me/channels")
            r.raise_for_status()
            for c in r.json():
                if room_name == c.get("name") or room_name in c.get("display_name", ""):
                    return c["id"]
            if time.time() > end:
                raise TimeoutError("channel never appeared")
            time.sleep(1.5)

    def learn_senders(self, person: dict, clerk: dict) -> dict:
        """Server-set ids, learned by watching real posts. Ids are compared later, and never parsed."""
        clerk_agent, own_agent = self._agent(clerk), self._agent(person)
        clerk_conn, own_conn = clerk_agent.listen(), own_agent.listen()
        try:
            clerk_conn.ready.wait(15)
            own_conn.ready.wait(15)
            time.sleep(1)
            hello = f"Hi, it's {person['display']}. [{secrets.token_hex(3)}]"
            Mattermost(person["username"], person["password"]).post(person["channels"]["desk"], hello)
            human = _await_sender(clerk_conn, hello)
            probe = f"{person['display']}'s agent checking in. [{secrets.token_hex(3)}]"
            own_agent.say(person["rooms"]["desk"], probe)
            agent_sender = _await_sender(clerk_conn, probe)
            clerk_agent.say(person["rooms"]["desk"], CLERK_HELLO)
            clerk_sender = _await_sender(own_conn, CLERK_HELLO)
        finally:
            clerk_conn.stop()
            own_conn.stop()
        return {"human": human, "agent": agent_sender, "clerk": clerk_sender}

    def announce(self, clerk: dict, room_id: str, text: str) -> None:
        self._agent(clerk).say(room_id, text)


def _await_sender(conn, needle: str, seconds: float = 30) -> str:
    end = time.time() + seconds
    while time.time() < end:
        try:
            e = conn.events.get(timeout=0.5)
        except queue.Empty:
            continue
        if needle in e.body:
            return e.sender
    raise TimeoutError("a test message never arrived")


@dataclass(frozen=True)
class Step:
    id: str
    label: str
    person: str | None
    run: Callable[[], None]


@dataclass
class Provisioner:
    backend: Backend
    folder: Path
    trip: dict
    start_apps: Callable[[], None] = lambda: None
    faults: set[str] = field(default_factory=set)
    secrets_doc: dict = field(default_factory=dict)
    status: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.paths = for_trip(self.folder)
        self.secrets_path = self.paths.state / "secrets.json"
        self.status_path = self.paths.state / "status.json"
        self.secrets_doc = read_json(self.secrets_path) or {"run": secrets.token_hex(2), "people": {}, "attempts": 0}
        self.status = read_json(self.status_path) or {"state": "idle", "done": []}

    # ---- status: safe to show ---------------------------------------------------------------

    def _write_status(self) -> None:
        write_json(self.status_path, self.status, 0o644)

    def _save(self) -> None:
        write_json(self.secrets_path, self.secrets_doc)

    @property
    def run_suffix(self) -> str:
        return self.secrets_doc["run"]

    def steps(self) -> list[Step]:
        people = self.trip["people"]
        out = [Step("connect", "Connecting to Switch", None, self._connect), Step("clerk", "Hiring the trip's clerk", None, self._clerk)]
        for slug, doc in people.items():
            name = doc["name"]
            out += [
                Step(f"account:{slug}", f"Making {name}'s account", slug, lambda s=slug: self._account(s)),
                Step(f"agent:{slug}", f"Starting {name}'s private agent", slug, lambda s=slug: self._agent(s)),
                Step(f"rooms:{slug}", f"Opening {name}'s private room and desk", slug, lambda s=slug: self._rooms(s)),
                Step(f"identity:{slug}", f"Checking {name}'s identity", slug, lambda s=slug: self._identity(s)),
            ]
        out += [Step("group", "Opening the group room", None, self._group), Step("finish", "Handing everyone their keys", None, self._finish), Step("start", "Starting the trip's apps", None, self._start)]
        return out

    def view(self) -> dict:
        """What the organiser's page may see. Built from fixed labels and codes."""
        steps = []
        done, failed = set(self.status.get("done", [])), self.status.get("failed", {})
        for s in self.steps():
            state = "done" if s.id in done else "failed" if s.id in failed else "running" if self.status.get("current") == s.id else "pending"
            item = {"id": s.id, "label": s.label, "person": s.person, "state": state}
            if s.id in failed:
                item["error"] = {"code": failed[s.id], "message": ERRORS[failed[s.id]]}
            steps.append(item)
        return {"state": self.status.get("state", "idle"), "steps": steps, "roster_locked": "group" in done or "group" == self.status.get("current")}

    def forget_person(self, slug: str) -> None:
        """They were removed before the group opened. An unused account or room costs nothing, and holds no data."""
        self.secrets_doc["people"].pop(slug, None)
        self._save()
        prefixes = tuple(f"{kind}:{slug}" for kind in ("account", "agent", "rooms", "identity"))
        self.status["done"] = [d for d in self.status.get("done", []) if d not in prefixes]
        self.status["failed"] = {k: v for k, v in self.status.get("failed", {}).items() if k not in prefixes}
        self._write_status()

    def mark_interrupted(self) -> None:
        current = self.status.get("current")
        self.status.update(state="failed", current=None, failed={**self.status.get("failed", {}), **({current: "interrupted"} if current else {})})
        self._write_status()

    # ---- the run ---------------------------------------------------------------------------------

    def run(self) -> str:
        self.status.update(state="running", failed={}, current=None, started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        self._write_status()
        blocked: set[str] = set()
        failed_any = False
        connected = False
        for step in self.steps():
            if step.id != "connect" and step.id in self.status["done"] and step.id != "start":
                continue
            if step.person in blocked:
                continue
            if step.person is None and failed_any:
                continue  # Trip-wide steps wait until everyone is ready.
            if step.id == "connect" and connected:
                continue
            self.status["current"] = step.id
            self._write_status()
            try:
                if step.id in self.faults:
                    self.faults.discard(step.id)
                    raise InjectedFault(step.id)
                step.run()
            except Exception as exc:
                code = classify(exc)
                # The type only. A server's reply or a URL could hold something private.
                log.warning("step %s failed: %s (%s)", step.id, code, type(exc).__name__)
                self.status["failed"][step.id] = code
                self.status["current"] = None
                failed_any = True
                self._write_status()
                if step.person is None or code in STOP_EVERYTHING:
                    break
                blocked.add(step.person)
                continue
            if step.id not in self.status["done"]:
                self.status["done"].append(step.id)
            self.status["current"] = None
            self._write_status()
            connected = connected or step.id == "connect"
        self.status["state"] = "failed" if failed_any else "live"
        self._write_status()
        return self.status["state"]

    # ---- steps. Each one can run again safely, after failing at any point. --------------------------

    def _connect(self) -> None:
        self.backend.connect()

    def _creds(self, key: str, slug: str, display: str) -> dict:
        people = self.secrets_doc["people"]
        if key not in people:
            run = self.run_suffix
            people[key] = {"slug": slug, "display": display, "email": f"{slug}-{run}@kitty.test", "username": f"{slug}{run}", "password": "Kt1!" + secrets.token_urlsafe(16), "made": []}
            self._save()  # Before any call, so a crash can't leave an account whose password is lost.
        return people[key]

    def _make_account(self, creds: dict, linked: bool) -> None:
        for flag, call in (("user", self.backend.ensure_user), ("chat", self.backend.ensure_chat_account), ("link", self.backend.link_identity if linked else None)):
            if call is not None and flag not in creds["made"]:
                call(creds)
                creds["made"].append(flag)
                self._save()
        if "reg" not in creds:
            creds["reg"] = self.backend.registration_key(creds)
            self._save()

    def _register(self, creds: dict, base_name: str, description: str, display: str) -> None:
        if "agent_id" in creds:
            return
        tries = creds.get("agent_tries", 0)
        creds["agent_tries"] = tries + 1
        self._save()
        name = base_name if tries == 0 else f"{base_name}-r{tries}"  # An earlier try may have taken the name.
        creds["agent_id"], creds["api_key"] = self.backend.register_agent(creds["reg"], name, description, display)
        creds["agent_name"] = name
        self._save()

    def _clerk(self) -> None:
        host = self._creds("__host__", "kittyhost", "Kitty Host")
        self._make_account(host, linked=False)
        self._register(host, f"kitty-clerk-{self.run_suffix}", "The trip's clerk. Ordinary code, not an LLM. Holds claims until their owner releases them, and does the maths.", "Kitty Clerk")

    def _account(self, slug: str) -> None:
        self._make_account(self._creds(slug, slug, self.trip["people"][slug]["name"]), linked=True)

    def _agent(self, slug: str) -> None:
        name = self.trip["people"][slug]["name"]
        self._register(self.secrets_doc["people"][slug], f"{slug}-agent-{self.run_suffix}", f"{name}'s private agent.", f"{name}'s agent")

    def _room(self, creds: dict, kind: str, description: str, owner: dict, agents: list[str], users: list[str], base: str) -> None:
        rooms, names = creds.setdefault("rooms", {}), creds.setdefault("room_names", {})
        if kind in rooms:
            return
        tries = creds.setdefault("room_tries", {}).get(kind, 0)
        creds["room_tries"][kind] = tries + 1
        self._save()
        names[kind] = f"{base}-{self.run_suffix}" + (f"r{tries}" if tries else "")
        rooms[kind] = self.backend.create_room(owner, names[kind], description, agents, users)
        self._save()

    def _rooms(self, slug: str) -> None:
        creds = self.secrets_doc["people"][slug]
        name = creds["display"]
        self._room(creds, "private", f"{name} and their agent. Merchant names stay here.", creds, [creds["agent_name"]], [creds["username"]], f"{slug}-private")
        self._room(creds, "desk", f"Typed claims wait here until {name} releases them.", creds, [creds["agent_name"]], [creds["username"]], f"{slug}-desk")
        if not creds.get("clerk_invited"):
            self.backend.invite_agent(creds, creds["rooms"]["desk"], self.secrets_doc["people"]["__host__"]["agent_name"])
            creds["clerk_invited"] = True
            self._save()
        channels = creds.setdefault("channels", {})
        for kind in ("private", "desk"):
            if kind not in channels:
                channels[kind] = self.backend.find_channel(creds, creds["room_names"][kind])
                self._save()

    def _identity(self, slug: str) -> None:
        creds = self.secrets_doc["people"][slug]
        if "senders" not in creds:
            creds["senders"] = self.backend.learn_senders(creds, self.secrets_doc["people"]["__host__"])
            self._save()

    def _group(self) -> None:
        host = self.secrets_doc["people"]["__host__"]
        people = [self.secrets_doc["people"][slug] for slug in self.trip["people"]]
        slug = "".join(c if c.isascii() and c.isalnum() else "-" for c in self.trip["name"].lower()).strip("-")[:24].strip("-") or "trip"
        self._room(host, "group", "The trip's group room. Only owner-released claims appear here.", host, [host["agent_name"]], [p["username"] for p in people], slug)
        for person in people:
            channels = person["channels"]
            if "group" not in channels:
                channels["group"] = self.backend.find_channel(person, host["room_names"]["group"])
                self._save()
        if not host.get("announced"):
            self.backend.announce(host, host["rooms"]["group"], f"Kitty is open for **{self.trip['name']}**. Only claims their owner has released appear here, and they carry a date, an amount, a category and who shared it. Say `balances`, `settle` or `status`. All data in this demo is synthetic.")
            host["announced"] = True
            self._save()

    def _finish(self) -> None:
        """The same files setup_trip.py writes, so every later process works the same on both routes."""
        host = self.secrets_doc["people"]["__host__"]
        people = {slug: self.secrets_doc["people"][slug] for slug in self.trip["people"]}
        state = self.paths.state
        write_json(state / "clerk.json", {
            "agent_id": host["agent_id"], "api_key": host["api_key"], "name": host["agent_name"], "group_room": host["rooms"]["group"],
            "people": {slug: {"id": slug, "name": p["display"], "human_sender": p["senders"]["human"], "agent_sender": p["senders"]["agent"], "desk_room": p["rooms"]["desk"]} for slug, p in people.items()},
            "run": self.run_suffix,
        })
        invites = read_json(state / "invites.json") or {}
        pot = {}
        if self.trip.get("treasurer"):  # For personal agents only: the clerk compares tokens it can't read.
            pot = {"pot_key": self.secrets_doc.setdefault("pot_key", secrets.token_hex(32))}
            self._save()
        for slug, p in people.items():
            write_json(state / f"{slug}.json", {
                **pot, "me": slug, "agent_id": p["agent_id"], "api_key": p["api_key"], "name": p["agent_name"],
                "private_room": p["rooms"]["private"], "desk_room": p["rooms"]["desk"],
                "owner_sender": p["senders"]["human"], "clerk_sender": p["senders"]["clerk"],
                "mattermost": {"username": p["username"], "password": p["password"], "channels": p["channels"]},
                "run": self.run_suffix,
            })
            (self.paths.people / slug).mkdir(parents=True, exist_ok=True, mode=0o700)
            if slug not in invites:
                invites[slug] = access.new_invite(self.paths.access(slug))
        write_json(state / "invites.json", invites)
        write_json(state / "verify.json", {"run": self.run_suffix, "agents": {slug: p["agent_name"] for slug, p in people.items()}, "private_rooms": {slug: p["rooms"]["private"] for slug, p in people.items()}})

    def _start(self) -> None:
        self.start_apps()


def start_in_background(p: Provisioner, on_done: Callable[[str], None] | None = None) -> threading.Thread:
    def work() -> None:
        state = p.run()
        if on_done:
            on_done(state)

    thread = threading.Thread(target=work, daemon=True, name=f"provision-{p.trip['trip_id']}")
    thread.start()
    return thread


def load_switch_env() -> dict[str, str]:
    from scripts.switch_env import load

    return load(Path(os.environ.get("SWITCH_SRC", ROOT.parent / "switch-src")) / ".env")
