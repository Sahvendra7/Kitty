"""A person's private agent: the conversation logic, with the model behind a small interface.

Lives on that person's side. It reads only that person's rows. It talks to its owner in their
private room and sends the clerk typed claims on their desk, and it has no route to the group room.

The model proposes and this code decides: model output is validated against the real rows and
the closed category list, and no model text is ever put on the desk. Every line sent to the desk is
built here from validated integers and enums.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

from kitty import claims as C
from kitty import statements as S


@dataclass(frozen=True)
class Guess:
    row_id: str
    label: str  # shared | personal | unclear
    category: str
    title: str  # short human name, shown only in the private room


@dataclass(frozen=True)
class Decision:
    verdict: str  # shared | personal | unsure
    people: tuple[str, ...] = ()
    shares: tuple[tuple[str, int], ...] = ()


class Brain(Protocol):
    def classify(self, rows: list[dict], owner_name: str, trip_name: str) -> list[dict]: ...
    def read_answer(self, question: str, answer: str, people: dict[str, str]) -> dict: ...


@dataclass(frozen=True)
class Post:
    room: str  # "private" or "desk"
    text: str


@dataclass
class Item:
    row: S.Row
    title: str
    category: str
    state: str = "new"  # new | asked | filed | personal
    ref: str | None = None
    claim_id: str | None = None


STOPWORDS = {"it", "was", "with", "and", "the", "just", "only", "shared", "yes", "me"}
CARD_REF = re.compile(r"^\*\*([ct][0-9]+)\*\* ready \[ref ([a-z0-9]+)\]")
CHECK = re.compile(r"^KITTY_CHECK (t[0-9]+) from=([a-z0-9_-]+) amount_paise=([0-9]+) date=(\d{4}-\d{2}-\d{2})$")
RECHECK = re.compile(r"^KITTY_RECHECK (t[0-9]+)$")
CUSTOM_SHARE = re.compile(r"([a-zA-Z]+)\s*[=:]\s*[₹$€£]?\s*([0-9]+(?:\.[0-9]{1,2})?)")


def fmt_time(hhmm: str) -> str:
    hour, _, minute = hhmm.partition(":")
    h = int(hour)
    suffix = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return f"{h12}:{minute} {suffix}" if minute != "00" else f"{h12} {suffix}"


@dataclass
class PersonalAgent:
    me: str
    trip: C.Trip
    names: dict[str, str]
    directory: dict[str, str]  # statement name -> person id
    rows: list[S.Row]
    brain: Brain
    owner_sender: str
    clerk_sender: str
    trip_name: str = "the trip"
    items: dict[str, Item] = field(default_factory=dict)
    transfers: dict[str, S.Row] = field(default_factory=dict)  # ref -> row
    filed_transfers: dict[str, str] = field(default_factory=dict)  # transfer id -> ref
    queue: list[str] = field(default_factory=list)
    asked: str | None = None
    started: bool = False
    _n: int = 0

    # ---- remembering, so a restart doesn't lose a review -------------------------------

    def to_state(self) -> dict:
        """Row ids and decisions, plus the short titles already shown in the private room. Kept on the owner's side."""
        return {
            "items": {k: {"title": i.title, "category": i.category, "state": i.state, "ref": i.ref, "claim_id": i.claim_id} for k, i in self.items.items()},
            "transfers": {ref: row.row_id for ref, row in self.transfers.items()},
            "filed_transfers": self.filed_transfers,
            "queue": self.queue,
            "asked": self.asked,
            "started": self.started,
            "n": self._n,
        }

    def restore(self, state: dict) -> None:
        """Rows come from the statement, which only ever grows once a review has started."""
        rows = {r.row_id: r for r in self.rows}
        self.items = {k: Item(rows[k], d["title"], d["category"], d["state"], d["ref"], d["claim_id"]) for k, d in state["items"].items()}
        self.transfers = {ref: rows[rid] for ref, rid in state["transfers"].items()}
        self.filed_transfers = dict(state["filed_transfers"])
        self.queue = list(state["queue"])
        self.asked = state["asked"]
        self.started = state["started"]
        self._n = state["n"]

    # ---- routing ---------------------------------------------------------------

    def handle(self, room: str, sender: str, body: str) -> list[Post]:
        if room == "private":
            if sender != self.owner_sender:
                return []  # Only the owner instructs this agent.
            return self._from_owner(body)
        if room == "desk":
            if sender == self.clerk_sender:
                return self._from_clerk(body)
            if sender == self.owner_sender:
                return self._desk_owner(body)
        return []

    @property
    def window(self) -> list[S.Row]:
        return S.in_window(self.rows, self.trip.start, self.trip.end)

    def _money(self, minor: int) -> str:
        return C.money(minor, self.trip.currency)

    def _ref(self) -> str:
        self._n += 1
        return f"{self.me[:1]}{self._n}"

    # ---- the first pass ------------------------------------------------------------

    def start(self) -> list[Post]:
        self.started = True
        return self._scan()

    def _scan(self) -> list[Post]:
        known = {i.row.row_id for i in self.items.values()} | {r.row_id for r in self.transfers.values()}
        fresh = [r for r in self.window if r.row_id not in known]
        spend = S.spending_candidates(fresh, self.directory)
        moved = S.outgoing_transfers(fresh, self.directory)
        out: list[Post] = []
        kept: list[str] = []
        filed = 0
        if spend:
            valid = {r.row_id: r for r in spend}
            guesses = self._validated(self.brain.classify([S.model_view(r, self.trip.currency) for r in spend], self.names[self.me], self.trip_name), valid)
            for r in spend:
                g = guesses.get(r.row_id) or Guess(r.row_id, "unclear", "other", r.narration.split("*")[0].title()[:24])
                item = Item(r, g.title, g.category)
                self.items[r.row_id] = item
                if g.label == "personal":
                    item.state = "personal"
                    kept.append(g.title)
                elif g.label == "shared":
                    out.append(self._file_expense(item, tuple(self.trip.participants)))
                    filed += 1
                else:
                    self.queue.append(r.row_id)
        for row, who in moved:
            ref = self._ref()
            self.transfers[ref] = row
            out.append(Post("desk", self._transfer_claim(ref, who, row)))
            filed += 1
        summary = [f"I read {len(self.window)} lines from your statement for {self.trip_name}."]
        if filed:
            summary.append(f"Put {filed} on your desk for you to release.")
        if kept:
            summary.append(f"Kept private: {', '.join(kept)}. The group won't hear about these.")
        out.insert(0, Post("private", " ".join(summary)))
        if self.asked is not None:
            self.queue.insert(0, self.asked)  # A rescan mid-question mustn't lose the question.
        out.extend(self._ask_next())
        return out

    def _validated(self, raw: list[dict], valid: dict[str, S.Row]) -> dict[str, Guess]:
        out: dict[str, Guess] = {}
        for g in raw if isinstance(raw, list) else []:
            if not isinstance(g, dict) or g.get("row_id") not in valid or g.get("label") not in ("shared", "personal", "unclear"):
                continue
            category = g.get("category") if g.get("category") in C.CATEGORIES else "other"
            title = re.sub(r"[`*_\[\]<>@#]", "", str(g.get("title") or "")).strip()[:32]
            if not title:
                title = valid[g["row_id"]].narration.split("*")[0].title()[:24]
            out[g["row_id"]] = Guess(g["row_id"], g["label"], category, title)
        return out

    # ---- questions ---------------------------------------------------------------

    def _question(self, item: Item) -> str:
        return f"{item.title}, {fmt_time(item.row.time)}, {self._money(item.row.debit).removesuffix('.00')}. Was this shared with the group?"

    def _ask_next(self) -> list[Post]:
        self.asked = None
        while self.queue:
            item = self.items[self.queue.pop(0)]
            if item.state in ("new", "asked"):
                item.state = "asked"
                self.asked = item.row.row_id
                return [Post("private", f"{self._question(item)}\nTell me in your own words, or say `personal`, `shared`, or `{self._shared_example()}`.")]
        if self.started:
            return [Post("private", "That's everything for now. Check your desk to release what you're happy to share.")]
        return []

    def _shared_example(self) -> str:
        others = [p for p in self.trip.participants if p != self.me][:2]
        return "shared " + " ".join(others)

    def _from_owner(self, body: str) -> list[Post]:
        text = body.strip()
        lowered = text.lower()
        if lowered in ("start", "go", "review", "rescan"):
            return self.start() if not self.started or lowered == "rescan" else self._scan()
        if lowered == "status":
            return [Post("private", f"{len(self.items)} lines read. {sum(i.state == 'filed' for i in self.items.values())} on your desk, {sum(i.state == 'personal' for i in self.items.values())} kept private.")]
        if lowered.startswith("reopen "):
            return self._reopen(text[7:].strip())
        if self.asked is None:
            return [Post("private", "Nothing to decide right now. Say `start` to read your statement, or `rescan` for new lines.")]
        item = self.items[self.asked]
        decision = self._read_answer(item, text)
        if decision.verdict == "unsure":
            return [Post("private", f"I didn't catch that. {self._question(item)} Say `personal`, `shared`, or `{self._shared_example()}`.")]
        if decision.verdict == "personal":
            item.state = "personal"
            return [Post("private", f"Got it. {item.title} stays private."), *self._ask_next()]
        if decision.shares:
            post = self._file_expense(item, tuple(p for p, _ in decision.shares), decision.shares)
        else:
            post = self._file_expense(item, decision.people or tuple(self.trip.participants))
        who = ", ".join(self.names[p] for p, _ in self._last_shares)
        return [post, Post("private", f"Filed {item.title} for {who}. It's on your desk, and the merchant name stays here."), *self._ask_next()]

    def _reopen(self, title: str) -> list[Post]:
        """The model proposes and the person decides, so a line it kept private can always be asked about again."""
        wanted = title.lower()
        item = next((i for i in self.items.values() if i.state == "personal" and i.title.lower() == wanted), None)
        if item is None:
            return [Post("private", "I can't find that among the lines I kept private.")]
        item.state = "new"
        self.queue.insert(0, item.row.row_id)
        return self._ask_next() if self.asked is None else [Post("private", f"I'll ask you about {item.title} after this one.")]

    def _read_answer(self, item: Item, text: str) -> Decision:
        return self._parse_command(item, text) or self._ask_brain(item, text)

    def _parse_command(self, item: Item, text: str) -> Decision | None:
        lowered = text.lower().strip()
        if lowered in ("personal", "just me", "mine", "no", "nope", "reject", "skip"):
            return Decision("personal")
        customs = CUSTOM_SHARE.findall(text)
        if customs:
            shares = []
            for name, amount in customs:
                pid = self._person(name)
                if pid is None:
                    return None
                shares.append((pid, S.to_paise(amount)))
            if sum(v for _, v in shares) != item.row.debit:
                return Decision("unsure")
            return Decision("shared", shares=tuple(shares))
        words = re.findall(r"[a-z]+", lowered)
        if words and words[0] in ("shared", "yes", "everyone", "all"):
            named = [p for w in words if (p := self._person(w))]
            rest = [w for w in words if w not in STOPWORDS and w not in ("everyone", "all") and not self._person(w)]
            if not rest:
                people = list(dict.fromkeys(named))
                if people and self.me not in people:
                    people.insert(0, self.me)
                return Decision("shared", tuple(people) or tuple(self.trip.participants))
        return None

    def _person(self, word: str) -> str | None:
        word = word.lower()
        return next((p for p, n in self.names.items() if word in (p, n.lower())), None)

    def _ask_brain(self, item: Item, text: str) -> Decision:
        raw = self.brain.read_answer(self._question(item), text, self.names)
        if not isinstance(raw, dict) or raw.get("verdict") not in ("shared", "personal", "unsure"):
            return Decision("unsure")
        if raw["verdict"] != "shared":
            return Decision(raw["verdict"])
        people = raw.get("people")
        if not isinstance(people, list) or not people:
            return Decision("shared", tuple(self.trip.participants))
        if any(p not in self.trip.participants for p in people):
            return Decision("unsure")
        ordered = [p for p in self.trip.participants if p in people]
        return Decision("shared", tuple(ordered))

    # ---- filing: typed, built here from validated values --------------------------

    def _file_expense(self, item: Item, people: tuple[str, ...], shares: tuple[tuple[str, int], ...] = ()) -> Post:
        final = shares or tuple(C.even_shares(item.row.debit, list(people)).items())
        self._last_shares = final
        item.state = "filed"
        item.ref = self._ref()
        return Post("desk", C.PREFIX + json.dumps({
            "v": 1, "type": "expense", "ref": item.ref, "trip_id": self.trip.trip_id, "owner_id": self.me, "payer_id": self.me,
            "date": item.row.day.isoformat(), "amount_paise": item.row.debit, "currency": self.trip.currency, "category": item.category,
            "participant_ids": [p for p, _ in final], "shares": {p: v for p, v in final},
        }, ensure_ascii=False))

    def _transfer_claim(self, ref: str, to: str, row: S.Row) -> str:
        return C.PREFIX + json.dumps({"v": 1, "type": "transfer", "ref": ref, "trip_id": self.trip.trip_id, "owner_id": self.me, "from_id": self.me, "to_id": to, "date": row.day.isoformat(), "amount_paise": row.debit, "currency": self.trip.currency})

    # ---- messages from the clerk -------------------------------------------------------

    def _from_clerk(self, body: str) -> list[Post]:
        if m := CARD_REF.match(body):
            return self._card(m.group(1), m.group(2))
        if m := CHECK.match(body):
            return self._check(m.group(1), m.group(2), int(m.group(3)), date.fromisoformat(m.group(4)))
        if m := RECHECK.match(body):
            return self._recheck(m.group(1))
        return []

    def _card(self, claim_id: str, ref: str) -> list[Post]:
        if claim_id.startswith("t"):
            self.filed_transfers[claim_id] = ref
            return []
        for item in self.items.values():
            if item.ref == ref:
                item.claim_id = claim_id
        return []

    def _check(self, tid: str, sender: str, amount: int, around: date) -> list[Post]:
        hit = S.find_credit(self.rows, amount, around, sender, self.directory)
        ref = self._ref()
        answer = C.PREFIX + json.dumps({"v": 1, "type": "transfer_answer", "ref": ref, "trip_id": self.trip.trip_id, "owner_id": self.me, "transfer_id": tid, "received": hit is not None})
        who, money = self.names.get(sender, sender), self._money(amount)
        if hit is not None:
            note = f"{who} says they paid you {money}. I found it on your statement: {hit.day:%d %b}, {fmt_time(hit.time)}. Your desk has the answer. Only yes or no goes to the clerk."
        else:
            note = f"{who} says they paid you {money} around {around:%d %b}. I looked, and nothing like that reached you. Your desk has the answer. Only yes or no goes to the clerk."
        return [Post("desk", answer), Post("private", note)]

    def _recheck(self, tid: str) -> list[Post]:
        ref = self.filed_transfers.get(tid)
        row = self.transfers.get(ref) if ref else None
        if row is None:
            return []
        reversal = S.find_reversal(self.rows, row)
        if reversal is None:
            return [Post("private", f"The clerk asked me to re-check {tid}. I can see your {self._money(row.debit)} debit on {row.day:%d %b} and no refund, so on your side the money left. You two may want to talk. Nothing else is shared.")]
        return [Post("private", f"I re-checked {tid}, and I think I've found it. Your {self._money(row.debit)} left at {fmt_time(row.time)} on {row.day:%d %b}, then came back at {fmt_time(reversal.time)}, same reference. **The payment failed and was reversed. {self.names[self._to(row)]} never got it.** To correct it, say `withdraw {tid} payment_failed` on your desk. Only the words \"payment failed\" reach the group.")]

    def _to(self, row: S.Row) -> str:
        return S.counterparty(row, self.directory) or self.me

    def _desk_owner(self, body: str) -> list[Post]:
        m = re.match(r"^\s*drop\s+(c[0-9]+)\s*$", body, re.I)
        if not m:
            return []
        for item in self.items.values():
            if item.claim_id == m.group(1).lower():
                item.state, item.ref, item.claim_id = "new", None, None
                self.queue.insert(0, item.row.row_id)
                if self.asked is None:
                    return self._ask_next()
        return []


class RulesBrain:
    """No model. Keyword rules, for tests and for running with no provider at all."""

    SHARED = {"AIRBNB": ("stay", "Villa"), "WATERSPORTS": ("activity", "Watersports"), "SCOOTER": ("travel", "Scooters"), "BAZAAR": ("groceries", "Groceries"), "PETROL": ("fuel", "Petrol"), "FISHERMANS": ("food", "Fisherman's Wharf"), "MARTINS": ("food", "Martin's Corner")}
    PERSONAL = {"TANISHQ": "Jewellery", "PHARMACY": "Pharmacy", "NETFLIX": "Netflix", "SPOTIFY": "Spotify", "RECHARGE": "Phone recharge", "MYNTRA": "Myntra", "CAFE": "Café"}
    UNCLEAR = {"ZOMATO": ("food", "Zomato"), "TITOS": ("other", "Tito's")}

    def classify(self, rows: list[dict], owner_name: str, trip_name: str) -> list[dict]:
        out = []
        for row in rows:
            text = row["narration"].upper()
            hit = next(((c, t) for k, (c, t) in self.SHARED.items() if k in text), None)
            if hit:
                out.append({"row_id": row["row_id"], "label": "shared", "category": hit[0], "title": hit[1]})
                continue
            name = next((t for k, t in self.PERSONAL.items() if k in text), None)
            if name:
                out.append({"row_id": row["row_id"], "label": "personal", "category": "other", "title": name})
                continue
            hit = next(((c, t) for k, (c, t) in self.UNCLEAR.items() if k in text), None)
            out.append({"row_id": row["row_id"], "label": "unclear", "category": hit[0] if hit else "other", "title": hit[1] if hit else row["narration"][:24]})
        return out

    def read_answer(self, question: str, answer: str, people: dict[str, str]) -> dict:
        lowered = answer.lower()
        if not any(w in lowered for w in ("share", "yes", "both", "group", "split", "together", "all of us")):
            return {"verdict": "personal" if any(w in lowered for w in ("mine", "myself", "alone", "personal")) else "unsure"}
        excluded = [p for p, n in people.items() if re.search(rf"\b(but|except|not|without)\b[^.,;]*\b{n.lower()}\b", lowered)]
        return {"verdict": "shared", "people": [p for p in people if p not in excluded]}
