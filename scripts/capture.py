"""Screenshots and screen recordings of the real running demo, through a throwaway headless Chrome.

Logins come from state/, and they stay out of the command line and the shell history.

    uv run python scripts/capture.py mattermost meera private out.png
    uv run python scripts/capture.py page http://127.0.0.1:7100/stage out.png
    uv run python scripts/capture.py record http://127.0.0.1:7100/stage out.mp4 --seconds 120
"""
from __future__ import annotations

import argparse
import base64
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx
from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from kitty.switch_client import Mattermost  # noqa: E402

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PORT = 9333


class Browser:
    def __init__(self, width: int, height: int) -> None:
        self.dir = tempfile.mkdtemp(prefix="kitty-chrome-")
        self.proc = subprocess.Popen([CHROME, "--headless=new", f"--remote-debugging-port={PORT}", f"--user-data-dir={self.dir}", "--disable-gpu", "--hide-scrollbars", "--no-first-run", f"--window-size={width},{height}", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            try:
                targets = httpx.get(f"http://127.0.0.1:{PORT}/json", timeout=1).json()
                page = next(t for t in targets if t["type"] == "page")
                break
            except (httpx.HTTPError, StopIteration):
                time.sleep(0.25)
        else:
            raise RuntimeError("Chrome did not start")
        self.ws = connect(page["webSocketDebuggerUrl"], max_size=None)
        self.id = 0
        self.lock = threading.Lock()
        self.send("Page.enable")
        self.send("Network.enable")
        self.send("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=1, mobile=False)

    def send(self, method: str, **params):
        self.id += 1
        mid = self.id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg["result"]

    def goto(self, url: str, settle: float = 4) -> None:
        self.send("Page.navigate", url=url)
        time.sleep(settle)

    def shot(self, path: Path) -> None:
        path.write_bytes(base64.b64decode(self.send("Page.captureScreenshot", format="png")["data"]))

    def close(self) -> None:
        self.ws.close()
        self.proc.terminate()
        self.proc.wait(timeout=10)
        shutil.rmtree(self.dir, ignore_errors=True)


def mattermost(person: str, room: str, out: Path) -> None:
    cfg = json.loads((ROOT / "state" / f"{person}.json").read_text())["mattermost"]
    mm = Mattermost(cfg["username"], cfg["password"])
    channel = mm.channel(cfg["channels"][room]).json()
    team = mm.http.get(f"/api/v4/teams/{channel['team_id']}").json()
    b = Browser(1440, 900)
    try:
        for name, value in (("MMAUTHTOKEN", mm.token), ("MMUSERID", mm.user_id)):
            b.send("Network.setCookie", name=name, value=value, domain="localhost", path="/")
        b.goto(f"http://localhost:8065/{team['name']}/channels/{channel['name']}", settle=9)
        b.shot(out)
    finally:
        b.close()


def page(url: str, out: Path, width: int, height: int) -> None:
    b = Browser(width, height)
    try:
        b.goto(url)
        b.shot(out)
    finally:
        b.close()


def record(url: str, out: Path, seconds: int, width: int, height: int, fps: int = 10) -> None:
    """Page screencast to mp4. Frames are placed at their real times, so nothing is sped up here."""
    b = Browser(width, height)
    frames = tempfile.mkdtemp(prefix="kitty-frames-")
    stamps: list[tuple[str, float]] = []
    try:
        b.goto(url, settle=3)
        b.send("Page.startScreencast", format="jpeg", quality=88, maxWidth=width, maxHeight=height, everyNthFrame=1)
        end = time.time() + seconds
        n = 0
        while time.time() < end:
            try:
                msg = json.loads(b.ws.recv(timeout=1))
            except TimeoutError:
                continue
            if msg.get("method") == "Page.screencastFrame":
                params = msg["params"]
                path = Path(frames) / f"{n:06d}.jpg"
                path.write_bytes(base64.b64decode(params["data"]))
                stamps.append((path.name, params["metadata"]["timestamp"]))
                n += 1
                b.id += 1
                b.ws.send(json.dumps({"id": b.id, "method": "Page.screencastFrameAck", "params": {"sessionId": params["sessionId"]}}))
        stamps_end = time.time()
    finally:
        b.close()
    if not stamps:
        raise RuntimeError("no frames captured")
    lines = []
    for (name, t), (_, nxt) in zip(stamps, stamps[1:] + [("", stamps[-1][1] + 0.5)]):
        lines.append(f"file '{frames}/{name}'\nduration {max(nxt - t, 0.01):.3f}\n")
    lst = Path(frames) / "list.txt"
    lst.write_text("".join(lines))
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-vf", f"fps={fps},format=yuv420p", "-c:v", "libx264", "-crf", "20", str(out)], check=True)
    shutil.rmtree(frames, ignore_errors=True)
    print(f"{out}: {n} frames, {stamps_end - stamps[0][1]:.0f}s")


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("mattermost")
    m.add_argument("person")
    m.add_argument("room")
    m.add_argument("out", type=Path)
    p = sub.add_parser("page")
    p.add_argument("url")
    p.add_argument("out", type=Path)
    p.add_argument("--width", type=int, default=1600)
    p.add_argument("--height", type=int, default=900)
    r = sub.add_parser("record")
    r.add_argument("url")
    r.add_argument("out", type=Path)
    r.add_argument("--seconds", type=int, default=120)
    r.add_argument("--width", type=int, default=1920)
    r.add_argument("--height", type=int, default=1080)
    a = ap.parse_args()
    if a.cmd == "mattermost":
        mattermost(a.person, a.room, a.out)
    elif a.cmd == "page":
        page(a.url, a.out, a.width, a.height)
    else:
        record(a.url, a.out, a.seconds, a.width, a.height)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
