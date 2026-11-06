"""Records the demo video from a real run of the real system.

Everything inside the app frames is live: real Switch rooms, real Mattermost posts from each
person's own account, the real model. The people are scripted, and the video says so. Captions,
zooms and title cards are drawn in the page. Stretches that are sped up carry a visible badge.

Needs a fresh trip with run_all.py going.

    uv run python scripts/film.py docs/assets/kitty-demo.mp4
"""
from __future__ import annotations

import base64
import json
import re
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
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
from capture import CHROME, PORT  # noqa: E402
from play_story import ANSWERS, Human, board, open_cards  # noqa: E402

WIDTH, HEIGHT = 1920, 1080
FAST = 8


class Recorder:
    """Headless Chrome, with a reader thread so frames keep flowing while commands run."""

    def __init__(self) -> None:
        self.dir = tempfile.mkdtemp(prefix="kitty-film-")
        self.frames = Path(self.dir) / "frames"
        self.frames.mkdir()
        self.proc = subprocess.Popen([CHROME, "--headless=new", f"--remote-debugging-port={PORT}", f"--user-data-dir={self.dir}/profile", "--disable-gpu", "--hide-scrollbars", "--no-first-run", f"--window-size={WIDTH},{HEIGHT}", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            try:
                page = next(t for t in httpx.get(f"http://127.0.0.1:{PORT}/json", timeout=1).json() if t["type"] == "page")
                break
            except (httpx.HTTPError, StopIteration):
                time.sleep(0.25)
        else:
            raise RuntimeError("Chrome did not start")
        self.ws = connect(page["webSocketDebuggerUrl"], max_size=None)
        self.n = 0
        self.pending: dict[int, threading.Event] = {}
        self.results: dict[int, dict] = {}
        self.stamps: list[tuple[str, float]] = []
        self.fast: list[list[float]] = []
        self.send_lock = threading.Lock()
        self.alive = True
        threading.Thread(target=self._read, daemon=True).start()
        self.cmd("Page.enable")
        self.cmd("Emulation.setDeviceMetricsOverride", width=WIDTH, height=HEIGHT, deviceScaleFactor=1, mobile=False)

    def _read(self) -> None:
        while self.alive:
            try:
                msg = json.loads(self.ws.recv(timeout=1))
            except TimeoutError:
                continue
            except Exception:
                return
            if msg.get("method") == "Page.screencastFrame":
                p = msg["params"]
                name = f"{len(self.stamps):06d}.jpg"
                (self.frames / name).write_bytes(base64.b64decode(p["data"]))
                self.stamps.append((name, p["metadata"]["timestamp"]))
                self._send("Page.screencastFrameAck", {"sessionId": p["sessionId"]})
            elif "id" in msg and msg["id"] in self.pending:
                self.results[msg["id"]] = msg
                self.pending[msg["id"]].set()

    def _send(self, method: str, params: dict) -> int:
        with self.send_lock:
            self.n += 1
            self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
            return self.n

    def cmd(self, method: str, **params) -> dict:
        with self.send_lock:
            self.n += 1
            mid = self.n
            done = threading.Event()
            self.pending[mid] = done
            self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        if not done.wait(30):
            raise TimeoutError(method)
        msg = self.results.pop(mid)
        if "error" in msg:
            raise RuntimeError(f"{method}: {msg['error']}")
        return msg["result"]

    def js(self, code: str) -> None:
        r = self.cmd("Runtime.evaluate", expression=code)
        if r.get("exceptionDetails"):
            raise RuntimeError(f"page script failed: {r['exceptionDetails']}")

    def start(self, url: str) -> None:
        self.cmd("Page.navigate", url=url)
        time.sleep(4)
        self.cmd("Page.startScreencast", format="jpeg", quality=90, maxWidth=WIDTH, maxHeight=HEIGHT, everyNthFrame=1)

    def finish(self, out: Path) -> None:
        self.cmd("Page.stopScreencast")
        self.alive = False
        time.sleep(1.2)
        self.ws.close()
        self.proc.terminate()
        stamps = self.stamps
        rows = []
        total = 0.0
        for (name, t), (_, nxt) in zip(stamps, stamps[1:] + [("", stamps[-1][1] + 1.0)]):
            d = max(nxt - t, 0.001)
            if any(a <= t < b for a, b in self.fast):
                d /= FAST
            total += d
            rows.append(f"file '{self.frames}/{name}'\nduration {d:.4f}\n")
        lst = Path(self.dir) / "list.txt"
        lst.write_text("".join(rows))
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-vf", "fps=24,format=yuv420p", "-c:v", "libx264", "-crf", "21", "-preset", "medium", "-movflags", "+faststart", str(out)], check=True)
        shutil.rmtree(self.dir, ignore_errors=True)
        print(f"{out}: {len(stamps)} frames, {total:.0f}s")


def main(out: Path) -> int:
    humans = {slug: Human(slug) for slug in ("priya", "arjun", "meera")}
    priya, arjun, meera = humans["priya"], humans["arjun"], humans["meera"]
    index = {"priya": 0, "arjun": 1, "meera": 2, "board": 3}
    r = Recorder()
    log: list[tuple[float, str]] = []
    t0 = time.time()

    def cap(text: str, sub: str = "") -> None:
        log.append((time.time() - t0, text))
        r.js(f"film.caption({json.dumps(text, ensure_ascii=False)},{json.dumps(sub, ensure_ascii=False)})")

    def card(html: str, cls: str = "") -> None:
        r.js(f"film.card({json.dumps(html, ensure_ascii=False)},{json.dumps(cls)})")

    def zoom(who: str | None, anchor: str = "bottom", label: str = "") -> None:
        r.js(f"film.zoom({'null' if who is None else index[who]},{json.dumps(anchor)},{json.dumps(label, ensure_ascii=False)})")
        time.sleep(1)

    def tab(who: str, name: str) -> None:
        r.js(f"film.tab({index[who]},{json.dumps(name)})")
        time.sleep(0.5)

    class Fast:
        """Run a stretch at FAST times speed, with the badge on screen."""

        def __enter__(self):
            r.js(f"film.badge('×{FAST}')")
            time.sleep(0.2)
            self.start = time.time()

        def __exit__(self, *exc):
            r.fast.append([self.start, time.time()])
            r.js("film.badge(null)")
            time.sleep(0.2)

    def answer_all(h: Human, skip_amount: str | None = None, seconds: float = 180) -> bool:
        """Answers everything except skip_amount. True if it stopped at that question."""
        end = time.time() + seconds
        while time.time() < end:
            mine, text = h.last_private()
            if not mine and "That's everything for now" in text:
                return False
            if not mine and "Was this shared with the group?" in text:
                amount = re.search(r"₹[\d,]+(?:\.\d{2})?", text).group(0).removesuffix(".00")
                if amount == skip_amount:
                    return True
                h.say("private", ANSWERS[amount])
                time.sleep(1.5)
            time.sleep(0.4)
        raise TimeoutError(f"{h.slug}: agent never finished")

    r.start("http://127.0.0.1:7100/film")

    # ---- the problem -----------------------------------------------------------------------
    cap("Every group trip ends here.")
    card('<div class="chat"><div><i>Arjun</i>who paid for the scooters??</div><div><i>Priya</i>I\'ll do the Splitwise</div><div><i>Meera</i>didn\'t I already pay you back?</div><div><i>Priya</i>send me a screenshot of your bank app</div></div>', "ink")
    time.sleep(5.5)
    card("<h1>Back from a group trip.</h1><p style='margin-top:26px;font-size:44px'>Nobody typed an expense. Nobody saw anyone's bank statement. Everyone's square.</p>", "gold")
    cap("This is Kitty.", "A workflow on Switch: each friend gets their own private agent, and a clerk that isn't an AI does the sums.")
    time.sleep(6.5)
    card("<img src='/assets/architecture.png' alt='Architecture'>", "white")
    cap("Switch enforces the walls, not a prompt.", "Agents can't enter the group room. The clerk can't enter private rooms. The server answers 403.")
    time.sleep(8)
    r.js("film.uncard()")

    # ---- reading statements ---------------------------------------------------------------
    cap("Three friends. Three real Switch owners. Three agents.", "Synthetic statements. Scripted people, posting from their own Mattermost accounts. The model and rooms are real.")
    time.sleep(5)
    for slug in ("priya", "arjun", "meera"):
        humans[slug].say("private", "start")
    zoom("priya", label="Priya's private room")
    cap("Priya's agent reads her statement, on her side.", "Plain code strips balances, account numbers and anything outside the trip first.")
    with Fast():
        priya.wait("private", "I read ", 120)
    tab("priya", "private")
    cap("Tanishq Jewellers: a secret birthday gift for Arjun.", "It stays private. The group will never hear about it.")
    time.sleep(5)
    found = answer_all(priya, skip_amount="₹850")
    if not found:
        raise RuntimeError("The model didn't ask about Zomato on this run, so the key beat is missing. Re-run.")
    tab("priya", "private")
    cap("An AI can't know this line. It asks.")
    time.sleep(5)
    priya.say("private", ANSWERS["₹850"])
    time.sleep(1)
    tab("priya", "private")
    cap("Only Priya knows that.")
    priya.wait("private", "Filed Zomato", 60)
    tab("priya", "private")
    time.sleep(3)
    answer_all(priya)
    tab("priya", "desk")
    zoom("priya", label="Priya's desk")
    cap("On her desk it's a typed claim. No merchant.", "A date, an amount, a category, who shared. There is no field for anything else.")
    time.sleep(6.5)
    cards = [c for c in open_cards(priya) if c.startswith("c")]
    zoom(None)
    cap("Nothing reaches the group until Priya releases it.")
    time.sleep(3)
    priya.say("desk", f"release {cards[0]}")
    time.sleep(2.5)
    priya.say("desk", f"release {cards[1]}")
    time.sleep(1.5)
    for c in cards[2:]:
        priya.say("desk", f"release {c}")
    cap("Each release moves the balance bars.", "The clerk is ordinary code, working in integer paise.")
    time.sleep(3)

    # ---- everyone else --------------------------------------------------------------------
    tab("arjun", "private")
    tab("meera", "private")
    with Fast():
        cap("Arjun and Meera do the same.")
        answer_all(arjun)
        answer_all(meera)
        time.sleep(2)
        for h in (arjun, meera):
            for c in open_cards(h):
                if c.startswith("c"):
                    h.say("desk", f"release {c}")
                    time.sleep(0.8)
        time.sleep(3)

    # ---- an attack ------------------------------------------------------------------------
    tab("arjun", "group")
    victim = next(c for c in ("c1", "c2"))
    zoom("arjun", "top", "The group room")
    cap("Can Arjun release someone else's claim?", "Switch sets the sender on every message, so typing a name doesn't help.")
    time.sleep(3)
    arjun.say("group", f"release {victim}")
    time.sleep(2.5)
    cap("Refused. In a room everyone can see.")
    time.sleep(3)
    zoom(None)

    # ---- the transfers -------------------------------------------------------------------
    with Fast():
        cap("Arjun paid Priya ₹3,000. Both statements agree, so it counts.")
        t_arjun = next(c for c in open_cards(arjun) if c.startswith("t"))
        arjun.say("desk", f"release {t_arjun}")
        priya.wait("desk", f"release {t_arjun}", 30)
        priya.say("desk", f"release {t_arjun}")
        priya.wait("group", f"{t_arjun} confirmed by both sides", 30)
        time.sleep(2)

    cap("Now Meera says she paid Arjun ₹1,500.", "Her statement shows the money leaving. Not counted until Arjun confirms.")
    t = next(c for c in open_cards(meera) if c.startswith("t"))
    meera.say("desk", f"release {t}")
    time.sleep(5)
    arjun.wait("private", "says they paid you ₹1,500", 30)
    tab("arjun", "private")
    zoom("arjun", label="Arjun's private room")
    cap("Arjun's agent checks his statement. Nothing arrived.", "That \"no\" is a fact about his statement, so he decides whether to send it. Only yes or no crosses.")
    time.sleep(6.5)
    arjun.wait("desk", f"release {t}", 30)
    arjun.say("desk", f"release {t}")
    meera.wait("group", f"{t} NOT CONFIRMED", 30)
    zoom(None)
    cap("Two stories. Usually, this is where someone sends a screenshot.")
    time.sleep(4.5)
    meera.wait("private", "failed and was reversed", 30)
    tab("meera", "private")
    zoom("meera", label="Meera's private room")
    cap("Meera's agent re-checks, in a room only she can see.", "The money left at 3:02 PM and came back at 3:09 PM, with the same reference.")
    time.sleep(8)
    cap("The payment failed. Nobody showed a statement to find that out.")
    time.sleep(5)
    meera.say("desk", f"withdraw {t} payment_failed")
    meera.wait("group", f"{t} withdrawn by", 30)
    shot = subprocess.Popen([sys.executable, "scripts/capture.py", "mattermost", "priya", "group", "docs/assets/mattermost-group.png"], cwd=ROOT, env={"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin:/opt/homebrew/bin", "KITTY_CHROME_PORT": "9344", "PYTHONWARNINGS": "ignore"})
    zoom("board", "top", "The trip board")
    cap("The group sees one line: the payment failed.", "Counted, it would have made Arjun owe ₹1,108.33. He's actually owed ₹391.67.")
    time.sleep(7)
    zoom(None)

    # ---- settling -------------------------------------------------------------------------
    state = board()
    cap("Kitty suggests the payments. It never moves money.", "People pay in their own bank app. Here, a script adds them to the synthetic statements.")
    time.sleep(5)
    tab("meera", "private")
    with Fast():
        cap("A payment counts only once both people's sides confirm it.")
        for _ in state["plan"]:
            before = set(open_cards(meera))
            payee_name = board()["plan"][0]["to"].lower()
            subprocess.run([sys.executable, "scripts/demo_pay.py"], cwd=ROOT, check=True, capture_output=True)
            meera.say("private", "rescan")
            end = time.time() + 60
            while not (fresh := set(open_cards(meera)) - before) and time.time() < end:
                time.sleep(0.4)
            tid = next(iter(fresh))
            meera.say("desk", f"release {tid}")
            humans[payee_name].wait("desk", f"release {tid}", 30)
            humans[payee_name].say("desk", f"release {tid}")
            humans[payee_name].wait("group", f"{tid} confirmed by both sides", 30)
            time.sleep(2)
    zoom("board", "top", "The trip board")
    cap("Everyone's square.", "Every balance is zero, because of payments both sides confirmed.")
    time.sleep(5)
    zoom(None)
    if shot.wait(timeout=60) != 0:
        raise RuntimeError("could not capture Mattermost")
    card("<img src='/assets/mattermost-group.png?run=" + str(int(t0)) + "' alt='The group room in Mattermost'>", "white")
    cap("All of it happened in real team chat.", "Real Mattermost channels, connected through Switch. This is the group room from this run.")
    time.sleep(6)

    # ---- proof, using real output ---------------------------------------------------------
    v = subprocess.run([sys.executable, "scripts/verify.py"], cwd=ROOT, capture_output=True, text=True, env={"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin:/opt/homebrew/bin"})
    if v.returncode != 0:
        raise RuntimeError("verify failed during filming:\n" + v.stdout[-800:])
    summary = re.search(r"(\d+)/(\d+) checks passed", v.stdout)
    terms = re.search(r"\[(\d+) terms", v.stdout).group(1)
    ev = json.loads((ROOT / "docs/evidence/eval_agent.json").read_text())
    wrong = ev["fp"] + ev["fn"]
    asked = ev["unclear_shared"] + ev["unclear_personal"]
    rows = [
        ("Balances recomputed from the room's own text net to zero", "pass"),
        ("Every visible claim traces to its owner's own release", "pass"),
        (f"{terms} merchant words, references and account numbers in the group room", "0 found"),
        ("Agents in the group room", "clerk only"),
        ("Personal agents and the clerk, against rooms they aren't in", "403"),
        ("The reversed payment counted as paid", "never"),
        (f"Model on our synthetic answer key, {ev['lines_scored']} judgments", f"{wrong} wrong, asked on {asked}"),
    ]
    card(f"<h2>verify: {summary.group(1)} of {summary.group(2)} checks passed</h2><ul class='checks'>" + "".join(f"<li><span>{a}</span><b>{b}</b></li>" for a, b in rows) + "</ul>")
    cap("A script proves it, without trusting the clerk's own totals.", "This is the real output of this run.")
    time.sleep(7.5)
    card("<h2>What this doesn't do</h2><p>Synthetic data, on one laptop.<br>Statements still reach the model provider, after stripping.<br>Whoever hosts Switch can read private rooms.<br>We did no user research.</p>")
    cap("The limits, plainly.")
    time.sleep(6)
    card("<h1>Nobody typed.<br>Nobody saw.</h1><p style='margin-top:26px'>Kitty, built on Switch.</p>", "gold")
    cap("github.com/Sahvendra7/Kitty")
    time.sleep(5)
    r.finish(out)
    (ROOT / "docs/evidence/film_log.json").write_text(json.dumps([{"t": round(a, 1), "caption": b} for a, b in log], indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1])))
