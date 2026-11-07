"""Thin clients for the three Switch-side surfaces Kitty touches.

Gateway: admin and owner sessions, which create users, keys and identity links.
Agent: one registered agent's HTTP operations and its WebSocket connection.
Mattermost: the humans' chat accounts, used for setup and for scripted demo humans.

No business logic lives here. Nothing here reads a statement.
"""
from __future__ import annotations

import json
import logging
import queue
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

log = logging.getLogger("kitty.switch")

SWITCH_URL = "http://localhost:8000"
SWITCH_WS = "ws://localhost:8000"
MATTERMOST_URL = "http://localhost:8065"


class Gateway:
    def __init__(self, email: str, password: str, base: str = SWITCH_URL) -> None:
        self.http = httpx.Client(base_url=base, timeout=30)
        r = self.http.post("/gateway/auth/login", json={"email": email, "password": password})
        r.raise_for_status()
        self.email = email

    def create_user(self, email: str, name: str, password: str) -> dict:
        r = self.http.post("/gateway/users", json={"email": email, "name": name, "password": password, "role": "user"})
        r.raise_for_status()
        return r.json()

    def registration_key(self, label: str) -> str:
        r = self.http.post("/gateway/api-keys", json={"label": label})
        r.raise_for_status()
        return r.json()["key"]

    def default_bridge(self) -> str:
        r = self.http.get("/gateway/collaborations")
        r.raise_for_status()
        bridges = r.json()
        bridges = bridges if isinstance(bridges, list) else bridges.get("bridges", bridges.get("collaborations", []))
        if not bridges:
            raise RuntimeError("No collaboration bridge registered. Has the setup container finished?")
        first = bridges[0]
        return first.get("id") or first.get("bridge_id") or first["config_id"]

    def link_mattermost(self, bridge_id: str, external_user_id: str, username: str, mm_token: str) -> None:
        """Link this Gateway user to a Mattermost account, proven by that account's own session."""
        for attempt in range(30):
            r = self.http.get(f"/gateway/collaborations/{bridge_id}/directory", params={"query": username})
            if r.is_success and any(u.get("external_user_id") == external_user_id for u in r.json().get("users", [])):
                break
            time.sleep(2)
        else:
            raise RuntimeError(f"{username} never appeared in the bridge directory")
        r = self.http.post(
            f"/gateway/collaborations/{bridge_id}/identities",
            json={"external_user_id": external_user_id, "username": username, "expects_confirmation": True},
        )
        r.raise_for_status()
        identity = r.json()
        if identity.get("link_request") is not None:
            r = self.http.post(f"/gateway/collaborations/{bridge_id}/identities/{identity['id']}/verify-session", json={"token": mm_token})
            r.raise_for_status()


class SwitchOpError(RuntimeError):
    """An agent operation Switch answered with an error status. It keeps the status, never the reply, which could hold anything."""

    def __init__(self, operation: str, status: int) -> None:
        super().__init__(f"{operation}: {status}")
        self.status = status


@dataclass
class Event:
    kind: str
    room_id: str
    sender: str
    sender_name: str
    body: str
    message_id: str
    raw: dict


class Agent:
    def __init__(self, agent_id: str, api_key: str, name: str, base: str = SWITCH_URL, ws_base: str = SWITCH_WS) -> None:
        self.id = agent_id
        self.key = api_key
        self.name = name
        self.ws_base = ws_base
        self.http = httpx.Client(base_url=base, headers={"Authorization": f"Bearer {api_key}"}, timeout=30)

    @classmethod
    def register(cls, reg_key: str, name: str, description: str, display_name: str | None = None, base: str = SWITCH_URL) -> "Agent":
        r = httpx.post(
            f"{base}/agents",
            headers={"Authorization": f"Bearer {reg_key}"},
            json={
                "name": name,
                "description": description,
                "display_name": display_name,
                "connector_type": "external",
                "integration_profile": {
                    "connection_model": "always_on",
                    "message_exchange": True,
                    "pre_invocation_mediation": [],
                    "post_invocation_mediation": [],
                    "event_reporting": [],
                },
            },
            timeout=30,
        )
        r.raise_for_status()
        return cls(r.json()["id"], r.json()["api_key"], name, base)

    def op(self, operation: str, /, **args: Any) -> httpx.Response:
        return self.http.post(f"/agents/{self.id}/ops/{operation}", json=args)

    def result(self, operation: str, /, **args: Any) -> Any:
        r = self.op(operation, **args)
        if r.status_code != 200:
            raise SwitchOpError(f"{self.name}.{operation}", r.status_code)
        return r.json()["result"]

    def post(self, room_id: str, content: str) -> httpx.Response:
        return self.http.post(f"/agents/{self.id}/message", json={"room_id": room_id, "content": content})

    def say(self, room_id: str, content: str) -> str:
        r = self.post(room_id, content)
        if r.status_code != 200:
            raise RuntimeError(f"{self.name} post: {r.status_code} {r.text[:300]}")
        return r.json()["event_id"]

    def listen(self, after: int | None = None) -> "Connection":
        """`after` resumes from a saved position, so messages sent while this process was down still arrive."""
        conn = Connection(self)
        conn.cursor = after
        conn.start()
        return conn


class Connection(threading.Thread):
    """The agent's WebSocket, with pings answered and reconnects handled.

    The connection id is kept across reconnects, which is what makes the server
    resume from the cursor rather than treat it as a new connection.
    """

    def __init__(self, agent: Agent) -> None:
        super().__init__(daemon=True, name=f"ws-{agent.name}")
        self.agent = agent
        self.connection_id = secrets.token_hex(16)
        self.events: "queue.Queue[Event]" = queue.Queue()
        self.ready = threading.Event()
        self.stopping = False
        self.cursor: int | None = None

    def stop(self) -> None:
        self.stopping = True

    def run(self) -> None:
        delay = 1.0
        while not self.stopping:
            try:
                self._session()
                delay = 1.0
            except (ConnectionClosed, OSError, TimeoutError) as exc:
                log.warning("%s: connection lost (%s); reconnecting in %.0fs", self.agent.name, exc, delay)
                time.sleep(delay)
                delay = min(delay * 2, 15)

    def _session(self) -> None:
        params: dict[str, Any] = {"connection_id": self.connection_id, "scope": "all", "filter": "all"}
        params["start_from"] = str(self.cursor) if self.cursor is not None else "head"
        url = f"{self.agent.ws_base}/agents/{self.agent.id}/connection/ws?{urlencode(params)}"
        with connect(url, additional_headers={"Authorization": f"Bearer {self.agent.key}"}) as ws:
            while not self.stopping:
                try:
                    frame = json.loads(ws.recv(timeout=1))
                except TimeoutError:
                    continue
                kind = frame.get("event") or frame.get("type")
                if kind == "ping":
                    ws.send(json.dumps({"type": "pong", "cursor": self.cursor or 0}))
                elif kind == "connection_state":
                    self.ready.set()
                elif kind == "refused":
                    raise RuntimeError(f"{self.agent.name}: connection refused: {frame['data']}")
                elif kind == "evicted":
                    raise OSError(f"evicted: {frame.get('data')}")
                elif kind == "message":
                    data = frame["data"]
                    self.cursor = data.get("sequence", self.cursor)
                    p = data["payload"]
                    self.events.put(Event("message", data["room_id"], p.get("sender", ""), p.get("sender_name", ""), p.get("body", ""), p.get("message_id", ""), data))
                elif isinstance(frame.get("data"), dict) and "sequence" in frame["data"]:
                    self.cursor = frame["data"]["sequence"]


class Mattermost:
    """One Mattermost account. Admin instance creates accounts; a person's instance posts as them."""

    def __init__(self, username: str, password: str, base: str = MATTERMOST_URL) -> None:
        self.http = httpx.Client(base_url=base, timeout=30)
        r = self.http.post("/api/v4/users/login", json={"login_id": username, "password": password})
        r.raise_for_status()
        self.token = r.headers["token"]
        self.http.headers["Authorization"] = f"Bearer {self.token}"
        self.user = r.json()
        self.username = username

    @property
    def user_id(self) -> str:
        return self.user["id"]

    def create_user(self, username: str, email: str, password: str, first: str, team_name: str) -> str:
        r = self.http.post("/api/v4/users", json={"username": username, "email": email, "password": password, "first_name": first})
        if r.status_code == 400 and "exists" in r.text.lower():
            r = self.http.get(f"/api/v4/users/username/{username}")
        r.raise_for_status()
        user_id = r.json()["id"]
        team = self.http.get(f"/api/v4/teams/name/{team_name}")
        team.raise_for_status()
        m = self.http.post(f"/api/v4/teams/{team.json()['id']}/members", json={"team_id": team.json()["id"], "user_id": user_id})
        m.raise_for_status()
        return user_id

    def post(self, channel_id: str, message: str) -> dict:
        r = self.http.post("/api/v4/posts", json={"channel_id": channel_id, "message": message})
        r.raise_for_status()
        return r.json()

    def channel(self, channel_id: str) -> httpx.Response:
        return self.http.get(f"/api/v4/channels/{channel_id}")

    def channel_posts(self, channel_id: str) -> httpx.Response:
        return self.http.get(f"/api/v4/channels/{channel_id}/posts")
