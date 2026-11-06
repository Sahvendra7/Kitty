"""Kitty's front door: where an organiser creates a trip and hands out invites.

    uv run python -m kitty.home_app      # http://127.0.0.1:7000

This process holds the Switch admin login, because creating accounts needs it. It never sends a
password, key, room id or sender id to the browser, and it never opens a statement. It sees only
"has joined" and "has added a statement", as yes or no.

Set KITTY_FAULT=agent:arjun to fail a step once on purpose, for testing recovery.
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse

from kitty import access, provision, runtime
from kitty.claims import CURRENCIES
from kitty.paths import ROOT, for_trip, trips_root
from kitty.provision import Provisioner
from kitty.trips import MAX_PEOPLE, MIN_PEOPLE, Invalid, TripStore, clean_person, clean_trip, read_json, write_json

log = logging.getLogger("kitty.home")
PORT = int(os.environ.get("KITTY_HOME_PORT", "7000"))


class Home:
    """The service behind the routes. The backend and the app starter can be swapped in tests."""

    def __init__(self, root: Path, backend_factory=None, starter=None, faults: set[str] | None = None) -> None:
        self.store = TripStore(root)
        self.root = root
        self.backend_factory = backend_factory or (lambda: provision.SwitchBackend(provision.load_switch_env()))
        self.starter = starter or (lambda folder, trip: runtime.start(folder, trip, root))
        self.faults = faults if faults is not None else set(filter(None, os.environ.get("KITTY_FAULT", "").split(",")))
        self.running: dict[str, threading.Thread] = {}
        self.lock = threading.Lock()
        for trip in self.store.all():
            p = self.provisioner(trip)
            if p.status.get("state") == "running":  # Nothing can be running, as this process has just started.
                p.mark_interrupted()

    def provisioner(self, trip: dict) -> Provisioner:
        folder = self.store.folder(trip["trip_id"])
        return Provisioner(self.backend_factory(), folder, trip, start_apps=lambda: self.starter(folder, self.store.get(trip["trip_id"])), faults=self.faults)

    def is_running(self, trip_id: str) -> bool:
        thread = self.running.get(trip_id)
        return bool(thread and thread.is_alive())

    def begin(self, trip: dict) -> bool:
        with self.lock:
            if self.is_running(trip["trip_id"]):
                return False
            self.running[trip["trip_id"]] = provision.start_in_background(self.provisioner(trip))
        return True

    # ---- views: nothing here comes from secrets.json ---------------------------------------------------

    def stage(self, trip: dict) -> str:
        return self.provisioner(trip).view()["state"]

    def editable(self, trip: dict) -> bool:
        view = self.provisioner(trip).view()
        return view["state"] in ("idle", "failed") and not view["roster_locked"] and not self.is_running(trip["trip_id"])

    def summary(self, trip: dict) -> dict:
        return {"id": trip["trip_id"], "name": trip["name"], "start": trip["start"], "end": trip["end"], "currency": trip["currency"], "people": [p["name"] for p in trip["people"].values()], "state": self.stage(trip)}

    def detail(self, trip: dict) -> dict:
        folder = self.store.folder(trip["trip_id"])
        where = for_trip(folder)
        prov = self.provisioner(trip).view()
        rt = runtime.status(folder)
        live = prov["state"] == "live"
        up = live and runtime.healthy(rt)
        invites = (read_json(where.state / "invites.json") or {}) if live else {}
        people = []
        for slug, doc in trip["people"].items():
            steps = [s for s in prov["steps"] if s["person"] == slug]
            done = sum(s["state"] == "done" for s in steps)
            joined = access.joined(where.access(slug))
            progress = read_json(where.person_status(slug)) or {}
            item = {"id": slug, "name": doc["name"], "statement_name": doc["statement_name"], "steps_done": done, "steps_total": len(steps), "failed": any(s["state"] == "failed" for s in steps), "joined": joined, "statement_added": bool(progress.get("statement"))}
            if up and not joined and slug in invites and slug in rt.get("apps", {}):
                item["invite_url"] = f"http://127.0.0.1:{rt['apps'][slug]}/join?t={invites[slug]}"
            if up and joined and slug in rt.get("apps", {}):
                item["app_url"] = f"http://127.0.0.1:{rt['apps'][slug]}/"
            people.append(item)
        out = {**self.summary(trip), "people": people, "provision": prov, "editable": self.editable(trip), "running": self.is_running(trip["trip_id"]), "apps_up": up, "limits": {"min": MIN_PEOPLE, "max": MAX_PEOPLE}}
        if up:
            out["board_url"] = f"http://127.0.0.1:{rt['board']}/"
            try:
                out["board"] = httpx.get(f"http://127.0.0.1:{rt['board']}/api/board", timeout=3).json()
            except (httpx.HTTPError, ValueError):
                out["board"] = None
        return out


def create_app(home: Home, port: int = PORT) -> FastAPI:
    app = FastAPI(title="Kitty", docs_url=None, redoc_url=None, openapi_url=None)
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    origins = {f"http://{h}" for h in hosts}

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.headers.get("host") not in hosts:
            return PlainTextResponse("Not here.", status_code=421)
        origin = request.headers.get("origin")
        if request.method not in ("GET", "HEAD") and origin is not None and origin not in origins:
            return JSONResponse({"error": "That request came from somewhere else."}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.exception_handler(KeyError)
    async def unknown(request: Request, exc: KeyError) -> JSONResponse:
        return JSONResponse({"error": "There's no such trip or person."}, status_code=404)

    @app.exception_handler(Invalid)
    async def invalid(request: Request, exc: Invalid) -> JSONResponse:
        return JSONResponse({"error": "Check the highlighted fields.", "fields": exc.fields}, status_code=422)

    def locked() -> JSONResponse:
        return JSONResponse({"error": "The group room is open, so this trip's people and dates are fixed."}, status_code=409)

    @app.get("/")
    @app.get("/trips/{any_path:path}")
    def page(any_path: str = "") -> FileResponse:
        return FileResponse(ROOT / "app" / "home.html")

    @app.get("/api/meta")
    def meta() -> JSONResponse:
        return JSONResponse({"currencies": [{"code": c, "symbol": s} for c, s in CURRENCIES.items()], "limits": {"min": MIN_PEOPLE, "max": MAX_PEOPLE}})

    @app.get("/api/trips")
    def trips() -> JSONResponse:
        return JSONResponse({"trips": [home.summary(t) for t in home.store.all()]})

    @app.post("/api/trips", status_code=201)
    async def new_trip(request: Request) -> JSONResponse:
        body = await request.json()
        key = body.get("create_key")
        doc = home.store.create(clean_trip(body), str(key)[:64] if key else None)
        return JSONResponse(home.detail(doc), status_code=201)

    @app.get("/api/trips/{trip_id}")
    def trip(trip_id: str) -> JSONResponse:
        return JSONResponse(home.detail(home.store.get(trip_id)))

    @app.patch("/api/trips/{trip_id}")
    async def edit_trip(trip_id: str, request: Request) -> JSONResponse:
        doc = home.store.get(trip_id)
        if not home.editable(doc):
            return locked()
        fields = clean_trip(await request.json())
        doc.update(fields)
        home.store.save(doc)
        return JSONResponse(home.detail(doc))

    @app.delete("/api/trips/{trip_id}")
    def delete_trip(trip_id: str) -> JSONResponse:
        doc = home.store.get(trip_id)
        if home.stage(doc) != "idle" or home.is_running(trip_id):
            return JSONResponse({"error": "Only a trip that hasn't been opened can be deleted."}, status_code=409)
        home.store.delete(trip_id)
        return JSONResponse({"ok": True})

    @app.post("/api/trips/{trip_id}/people", status_code=201)
    async def add_person(trip_id: str, request: Request) -> JSONResponse:
        doc = home.store.get(trip_id)
        if not home.editable(doc):
            return locked()
        if len(doc["people"]) >= MAX_PEOPLE:
            return JSONResponse({"error": f"A trip can have up to {MAX_PEOPLE} people."}, status_code=422)
        slug, person = clean_person(await request.json(), doc["people"])
        doc["people"][slug] = person
        home.store.save(doc)
        return JSONResponse(home.detail(doc), status_code=201)

    @app.delete("/api/trips/{trip_id}/people/{slug}")
    def remove_person(trip_id: str, slug: str) -> JSONResponse:
        doc = home.store.get(trip_id)
        if not home.editable(doc):
            return locked()
        if slug not in doc["people"]:
            raise KeyError(slug)
        del doc["people"][slug]
        home.store.save(doc)
        home.provisioner(doc).forget_person(slug)
        return JSONResponse(home.detail(doc))

    @app.post("/api/trips/{trip_id}/open", status_code=202)
    def open_trip(trip_id: str) -> JSONResponse:
        """Starts set-up, or carries on from where it stopped. Safe to press twice."""
        doc = home.store.get(trip_id)
        if not MIN_PEOPLE <= len(doc["people"]) <= MAX_PEOPLE:
            return JSONResponse({"error": f"A trip needs at least {MIN_PEOPLE} people."}, status_code=422)
        if home.stage(doc) == "live":
            return JSONResponse(home.detail(doc))
        if not home.begin(doc):
            return JSONResponse({"error": "Set-up is already running."}, status_code=409)
        return JSONResponse(home.detail(doc), status_code=202)

    @app.post("/api/trips/{trip_id}/restart", status_code=202)
    def restart(trip_id: str) -> JSONResponse:
        doc = home.store.get(trip_id)
        if home.stage(doc) != "live":
            return JSONResponse({"error": "This trip isn't open yet."}, status_code=409)
        runtime.start(home.store.folder(trip_id), doc, home.root)
        return JSONResponse(home.detail(doc))

    @app.post("/api/trips/{trip_id}/people/{slug}/invite")
    def new_invite(trip_id: str, slug: str) -> JSONResponse:
        """A new link. The old link and any open session stop working."""
        doc = home.store.get(trip_id)
        if slug not in doc["people"]:
            raise KeyError(slug)
        if home.stage(doc) != "live":
            return JSONResponse({"error": "This trip isn't open yet."}, status_code=409)
        where = for_trip(home.store.folder(trip_id))
        invites = read_json(where.state / "invites.json") or {}
        invites[slug] = access.new_invite(where.access(slug))
        write_json(where.state / "invites.json", invites)
        return JSONResponse(home.detail(doc))

    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    root = trips_root()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    print(f"Kitty: http://127.0.0.1:{PORT}", flush=True)
    uvicorn.run(create_app(Home(root)), host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
