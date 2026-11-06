# Pot Trip Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `treasurer` trip setting, so that a member's payment to one organiser counts only after that organiser's own account confirms it, without changing shared-expense trips at all.

**Architecture:** New pure module `kitty/pot.py` holds claims, decisions, status, figures, hub settlement and the chain. New `kitty/pot_agent.py` subclasses `PersonalAgent` for the private check. `clerk.py` gains a few hooks that do nothing when `pot` is `None`. `ledger.py` is reused unchanged, with confirmed contributions projected into `L.Payment`.

**Tech Stack:** Python, FastAPI, pytest, hypothesis, uv, Switch and Mattermost from source at `fa933012b`, vanilla JS pages.

**Spec:** `docs/POT_TRIP_SPEC.md`. Read it first. §5 (state machine), §7 (ledger) and §9 (data model) are binding, narrowed by the Core scope below.

## Core scope

| In | Out, per spec §19 |
|---|---|
| Transitions 1–4 and 6–11, 13, 14 in §5 | Transition 5 and `several`/`selected`, so a parent's payment falls to manual confirm |
| `pot_match` results `exact`, `amount_differs`, `none` | Transition 12, `dispute` |
| Tokens, decision chain, replay with real times | Crash resync and post-failure retries |
| `call`, `cancel`, `pot`, hub plan, pot figures | The prompt after a reversal that follows a confirmation |
| Minimal UI, `verify` extension, dataset, live script | §10 proof on request, so **`send_proof` is left out of `ask`'s codes** |
| All of acceptance A, B, C and D | `cover` |

## Global Constraints

- **Zero diff at any commit:** `kitty/claims.py`, `kitty/ledger.py`, `kitty/statements.py`, `kitty/statement_import.py`, `kitty/llm.py`, `kitty/llm_brain.py`, `kitty/agent.py`.
- `feat/kitty-mvp` stays submittable. All work goes on `feat/pot-trip`, in a **separate worktree at `~/Desktop/Switch/Kitty-pot`**. The owner's home app and trip run from the main checkout and read pages from disk, so never switch branches there. **Nothing merges without the owner's say-so.**
- Money is integer paise. No floats anywhere.
- Claims have exact keys and closed values. No free-text field. Refusals carry fixed codes and never echo input.
- Authority comes only from the server-set sender id and the room. Agents get `agent_cannot_approve` on any decision command.
- Banned in UI and templates: "verified", "bank-confirmed", "fake", "fraud", "suspicious", "proven".
- Group room may carry only: member identity, claimed amount, contribution status, confirmed amount, confirmation time, and an opted-in last four of a reference.
- `clerk.json` never holds `pot_key`. `pot.py` imports from `clerk.py`, and `clerk.py` never imports `pot.py`, so Mode A can't reach the new code.
- Every negative test needs a positive control.
- Synthetic data only. No video. Don't touch `trips/dp-break-trip-a2c0`. Never `lsof -ti tcp:PORT | xargs kill`, and clean up only trips you made.
- Keep `BUILD_JOURNEY.md` honest: real times from `date`, failures kept in.
- Stop rule: if a live six-person Pot run hasn't passed `verify` by **12:00 IST**, stop and report. Nothing new after **15:00 IST**.

## Review Focus

1. **A settling payment dated after the trip's last day.** Refused today with `date_outside_trip`. The person should see a plain sentence, with nothing lost. Test in Task 2.
2. **Messy typed amounts in "I paid":** `₹5,000`, `5,000.50`, `5000 rs`, `abc`, `0`, a date like `9 Oct`. Expect a claim for the first two and a plain request to retype otherwise, never a model call. Test in Task 4.
3. **Removing the chosen treasurer from the roster before opening.** The setting clears, and the page says so. Opening with no valid treasurer is refused. Test in Task 5.
4. **A treasurer adds a later export and rechecks.** The new credit is found on `recheck` with no restart. An export missing old lines gets the existing refusal. Test in Task 4.
5. **A credit from a parent's account.** Name differs, so `none`. The chit must say "Nothing yet", never "didn't arrive". Manual confirm still works. Test in Task 4.

---

### Task 1: Baseline: branch and golden Mode A test

**Files:**
- Create: `tests/test_mode_a_golden.py`, `tests/golden/three_person.json`, `tests/golden/six_person.json`

**Interfaces:**
- Produces: a frozen transcript that every later task must keep passing.

- [ ] **Step 1: Commit the documents to `feat/kitty-mvp`, then create the worktree**

```bash
git add docs/POT_TRIP_SPEC.md docs/DECISIONS.md docs/BUILD_JOURNEY.md docs/superpowers/plans/2026-11-07-pot-trip-core.md
git commit -m "Pot trip: specification, ADR-016 and Core plan. Nothing built"
git worktree add -b feat/pot-trip ../Kitty-pot
cd ../Kitty-pot && uv sync
```

It sits beside `switch-src`, so scripts still find Switch. Its `trips/`, `state/` and `people/` start empty. All later steps run from `Kitty-pot`.

- [ ] **Step 2: Write `tests/test_mode_a_golden.py`**

Drive the existing fake-Switch worlds: `tests/test_scenario.py` (`build`, `play_until_settlement`, then the same steps as its whole-trip test) and `tests/test_six_person.py` (`World`, `to_conflict`, then the rest of its story). Use a `RecordingBrain(RulesBrain)` that stores the arguments of `classify` and `read_answer`.

```python
def test_three_person_transcript_is_unchanged():
    assert run_three() == json.loads((GOLD / "three_person.json").read_text())

def test_six_person_transcript_is_unchanged():
    assert run_six() == json.loads((GOLD / "six_person.json").read_text())
```

`run_*` returns `{"rooms": world.log, "brain_calls": brain.calls, "balances": clerk.balances()}`. With `KITTY_WRITE_GOLDEN=1`, write the files instead of comparing.

- [ ] **Step 3: Generate at `4ba9d82`, with no other change**

Run: `KITTY_WRITE_GOLDEN=1 uv run pytest tests/test_mode_a_golden.py -q`, then `uv run pytest -q`
Expected: 186 passed. Confirm the JSON holds real story text, such as `Everyone's square`, so it can't pass while empty.

- [ ] **Step 4: Positive control**

Temporarily change one word in a clerk post, run the golden test, see it FAIL, and revert.

- [ ] **Step 5: Commit**

```bash
git add tests/test_mode_a_golden.py tests/golden
git commit -m "Freeze Mode A transcripts and prompts before any Pot trip change"
```

---

### Task 2: `kitty/pot.py`: claims, tokens, figures, hub, chain

**Files:**
- Create: `kitty/pot.py`
- Test: `tests/test_pot_claims.py`, `tests/test_pot_ledger.py`, `tests/test_pot_chain.py`

**Interfaces:**
- Consumes: `C.ClaimError`, `C.PREFIX`, `C.Trip`, `C.money`, `L.Payment`, `L.Line`, `L.balances`.
- Produces:

```python
MATCH_RESULTS = ("exact", "amount_differs", "none")
ASK_CODES = ("amount_differs", "date_differs", "not_found_yet")
REJECT_CODES = ("not_received", "duplicate", "reversed")
CORRECT_OUT_CODES = ("reversed", "not_received", "mistaken")

@dataclass(frozen=True)
class PotConfig: treasurer: str; collect_from: date

@dataclass(frozen=True)
class Contribution: ref: str; trip_id: str; owner_id: str; to_id: str; date: date
    amount_paise: int; currency: str; ref_token: str | None; ref_last4: str | None

@dataclass(frozen=True)
class PotMatch: ref: str; trip_id: str; owner_id: str; contribution_id: str
    result: str; credit_token: str | None

def parse_claim(body: str, trip: C.Trip, cfg: PotConfig, desk_owner: str) -> Contribution | PotMatch | None
    # None when the type isn't a pot type, so the clerk's normal path carries on.
def token(key: bytes, value: str) -> str       # HMAC-SHA256, value stripped and upper-cased, 32 hex chars
def settle_via(net: dict[str, int], hub: str) -> list[L.Payment]
def chain_next(prev: str, text: str) -> str    # sha256(prev + text), first 12 hex. Genesis prev is ""
def chain_tag(text: str, seq: int, prev: str) -> tuple[str, str]   # (tagged text "… [d7 hash]", new hash)
def chain_check(lines: list[str]) -> str | None   # first bad decision id, or None. Skips untagged lines

@dataclass(frozen=True)
class PotFigures: asked: dict[str, int]; credited: dict[str, int]; outstanding: dict[str, int]
    above: dict[str, int]; collected: int; spent: int; paid_out: int; position: int
    reimbursed: dict[str, int]; refunded: dict[str, int]

def figures(cfg: PotConfig, people: Sequence[str], calls: Iterable[tuple[int, tuple[str, ...]]],
            contributions: Iterable[L.Payment], lines: Iterable[L.Line], transfers: Iterable[L.Payment]) -> PotFigures
```

Formulas are in spec §7. Only `transfers` with `from_id == treasurer` count toward `paid_out`, and `figures` ignores the others.

- [ ] **Step 1: Write the failing tests**

```python
# test_pot_claims.py. A trip 16–19 Oct, collect_from 25 Sep, treasurer "asha".
@pytest.mark.parametrize("change, code", [
    ({"extra": 1}, "unknown_field"), ({"to_id": "rohan"}, "not_treasurer"), ({"owner_id": "asha", "to_id": "asha"}, "self_transfer"),
    ({"date": "2026-09-24"}, "date_outside_trip"), ({"date": "2026-10-20"}, "date_outside_trip"),
    ({"amount_paise": 0}, "bad_amount"), ({"amount_paise": 100.0}, "bad_amount"), ({"currency": "USD"}, "bad_currency"),
    ({"ref_token": "XYZ"}, "bad_token"), ({"ref_last4": "12345"}, "bad_last4"), ({"ref_last4": "1 34"}, "bad_last4")])
def test_bad_contributions_are_refused_with_a_code(change, code): ...

def test_pre_trip_date_is_accepted_when_collection_has_started(): ...   # 2026-09-25, also the positive control
def test_rejection_never_echoes_the_offending_text(): ...
def test_other_types_return_none(): ...                                  # expense, transfer, transfer_answer
def test_match_exact_needs_a_token_and_others_refuse_one(): ...          # code bad_token both ways
def test_match_from_someone_other_than_the_treasurer_is_refused(): ...   # not_treasurer
def test_settling_date_after_the_last_day_gives_a_plain_refusal(): ...   # Review Focus 1: date_outside_trip

# test_pot_ledger.py
@given(histories)
def test_any_history_keeps_every_invariant(h):
    net = L.balances(PEOPLE, h.lines, h.contributions + h.transfers)
    f = P.figures(CFG, PEOPLE, h.calls, h.contributions, h.lines, h.transfers)
    assert sum(net.values()) == 0
    assert f.position == -net["t"] - share_of("t", h.lines)
    plan = P.settle_via(net, "t")
    assert not any(L.balances(PEOPLE, h.lines, h.contributions + h.transfers + plan).values())
    assert all("t" in (p.from_id, p.to_id) for p in plan) and len(plan) <= len(PEOPLE) - 1

def test_spec_worked_example_to_the_paise():
    # Net: asha 390000, rohan -110000, esha -110000, farhan -110000, meera -310000, dev 250000. Position -1000000.
    # With Meera's 300000 removed: asha 690000, meera -610000, others unchanged.
def test_part_payment_and_above_ask(): ...   # asked 500000: credited 300000 gives outstanding 200000; 1500000 gives above 1000000
def test_reimbursed_then_refunded_split(): ...  # member spent 60000, treasurer paid back 100000: reimbursed 60000, refunded 40000
def test_a_call_never_changes_a_balance(): ...

# test_pot_chain.py
def test_chain_verifies_and_catches_an_edit_and_a_gap(): ...   # clean returns None; altered text returns that id; removed line returns next id
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_pot_claims.py tests/test_pot_ledger.py tests/test_pot_chain.py -q`
Expected: FAIL, `No module named kitty.pot`.

- [ ] **Step 3: Implement the interfaces above in `kitty/pot.py`**

Reuse `C._exact_keys`, `C._amount`, `C._ref` and `C._participant`. Date window is `cfg.collect_from` to `trip.end`, and `pot_match` has no date. Token regex `^[0-9a-f]{32}$`. `ref_last4` regex `^[A-Za-z0-9]{4}$`. Contribution id regex `^p[0-9]{1,6}$`.

- [ ] **Step 4: Run them to verify they pass, then run everything**

Run: `uv run pytest -q`
Expected: all pass, including golden.

- [ ] **Step 5: Commit**

```bash
git add kitty/pot.py tests/test_pot_claims.py tests/test_pot_ledger.py tests/test_pot_chain.py
git commit -m "Pot trip: typed claims, keyed tokens, figures, hub settlement and decision chain"
```

---

### Task 3: `PotBook` and clerk hooks

**Files:**
- Modify: `kitty/pot.py`, `kitty/clerk.py`
- Test: `tests/test_pot_state.py`, `tests/test_pot_duplicates.py`

**Interfaces:**
- Consumes: Task 2. `from kitty.clerk import Person, Post`.
- Produces:

```python
@dataclass(frozen=True)
class Decision: seq: int; at: str; actor: str; kind: str; target: str; basis: str | None; code: str | None
    # kind: call | cancel | submitted | confirm | ask | reject | withdraw | correct_out | correct_in

@dataclass
class ContributionRecord: id: str; claim: Contribution; held: bool = True; dropped: bool = False
    candidate: str | None = None; credit_token: str | None = None

@dataclass
class PotBook:
    cfg: PotConfig
    decisions: list[Decision]; records: dict[str, ContributionRecord]; calls: dict[str, tuple[int, tuple[str, ...]]]
    def handle(self, clerk, room_id: str, sender: str, body: str) -> list[Post] | None  # None: not ours, clerk carries on
    def status(self, pid: str) -> str   # held | dropped | submitted | recipient_review | needs_clarification | confirmed | rejected | withdrawn
    def confirmed_payments(self) -> list[L.Payment]
    def open_ids(self) -> list[str]     # submitted, recipient_review, needs_clarification, rejected
    def view(self, clerk) -> PotFigures
    def plan(self, clerk) -> list[L.Payment]        # settle_via(clerk.balances(), treasurer)
    def settle_text(self, clerk) -> str
    def square_posts(self, clerk) -> list[Post]
    def board_view(self, clerk) -> dict             # {"treasurer","roll":[{name,asked,credited,outstanding,above}],"position","contributions":[{id,from,amount_paise,status,basis}]}
```

`status` is computed by folding `decisions` over each id, and `candidate` only records the latest agent result. Clerk times come from `clerk.now()`.

**Exact templates** (spec §4, §5, §7). Decision posts pass through `chain_tag`:

```text
**k1** {T} asked {each person | A, B} for {money}.
**k1 cancelled by {T}.**
**p3** {M} says they paid {T} {money} on {9 Oct}{ · ref …1001}. Not counted yet.
**p3 confirmed received by {T}:** {M} paid {money} · checked against {T}'s records · {9 Oct, 14:02 IST}.
**p3 confirmed received by {T}:** {M} paid {money} · on {T}'s word · {9 Oct, 14:02 IST}.
**p3 needs clarification.** {T} can't confirm it yet. Not counted.
**p3 not received, says {T}.** Not counted.
**p3 withdrawn by {M}.** {existing "why" sentence} It isn't counted, so balances don't change.
**Correction of p3:** {T} no longer confirms it ({code}). {M}'s {money} no longer counts. {M}'s balance goes from {x} to {y}.
**Correction of p3:** {T} says it arrived after all. {M} paid {money}. {M}'s balance goes from {x} to {y}.
```

Desk posts, not chained. The `[ref …]` part is the claim's internal ref, used as today:

```text
Member's desk: **p3** ready [ref r3]: you paid {T} {money} on {9 Oct}.\nReply `release p3` to share it, or `drop p3` to keep it private.
Member's desk: Asha asks about p3: {code}.                         # the code stays off the group room
Treasurer's desk: KITTY_POT_CHECK p3 from=rohan amount_paise=500000 date=2026-10-09 ref_token=<hex or ->
Treasurer's desk: **p3** to check: {M} says they paid you {money} on {9 Oct}. Your agent: {found it | a different amount | nothing yet}.
Member's desk on reject: KITTY_RECHECK p3
```

**Clerk hooks, the only changes to `kitty/clerk.py`:**

```python
pot: object | None = None      # last field on Clerk. No import of kitty.pot
# handle(), first line:
if self.pot is not None and (posts := self.pot.handle(self, room_id, sender, body)) is not None:
    return posts
# confirmed():   append self.pot.confirmed_payments() when set
# open_items():  append self.pot.open_ids() when set
# settle_text(): return self.pot.settle_text(self) when set
# _square_check(): return self.pot.square_posts(self) when set
```

- [ ] **Step 1: Write the failing tests**

Helpers mirror `test_core.py`: `make_book_clerk()`, with `from_agent`, `from_owner` and `group_texts`.

```python
# test_pot_state.py
def test_held_claim_reaches_nobody(): ...                       # Group empty, member's desk has the ready line
def test_release_posts_to_group_and_asks_the_treasurers_agent(): ...
def test_nothing_counts_until_confirmed(): ...                  # balances unchanged in held, submitted, review, needs_clarification
def test_confirm_needs_exact_and_counts(): ...                  # balances move, basis records, chain tag present
def test_confirm_refused_on_none_without_manual(): ...          # code no_candidate_use_manual
def test_manual_allowed_only_on_none(): ...                     # exact and amount_differs refuse; none gives basis word, "on Asha's word"
def test_amount_differs_blocks_confirm(): ...                   # recheck after a corrected result allows it
def test_ask_code_stays_off_the_group(): ...                    # group has status only, member's desk has the code
def test_reject_counts_nothing_and_rechecks_the_payer(): ...
def test_none_stays_private(): ...                              # pot_match none adds nothing to group
@pytest.mark.parametrize("actor", ["member human", "member agent", "treasurer agent"])
@pytest.mark.parametrize("command", ["confirm p1", "reject p1 not_received", "ask p1 not_found_yet", "recheck p1", "correct p1 mistaken", "call 500", "cancel k1"])
def test_only_the_treasurers_account_decides(actor, command): ...  # fixed refusal, status unchanged. Positive control: treasurer succeeds
def test_treasurer_command_in_group_room_is_refused(): ...
def test_member_cannot_withdraw_a_confirmed_contribution(): ...
def test_correction_is_append_only(): ...                       # correct out then in: decisions grow, both posts stay, balances right each time, no record edited
def test_correction_after_settlement_edits_nothing_else(): ...
def test_every_unlisted_transition_is_refused(): ...            # table over status × command, generated from §5
def test_retry_is_refused_with_a_fixed_code(): ...              # already_<status>
def test_confirm_crossing_withdraw_first_wins(): ...
def test_transfer_to_treasurer_is_refused(): ...                # use_contribution; treasurer to member still works
def test_call_validation(): ...                                 # 0, negative, unknown name, treasurer named: refused. "call 500 rohan meera" works
def test_cancelled_call_drops_from_asked(): ...
def test_hub_plan_replaces_settle_text(): ...                   # every line involves the treasurer
def test_square_needs_nothing_open_and_rejected_counts_as_open(): ...
def test_group_posts_match_templates_only(): ...                # Every group post matches the list above, with no extra text
def test_no_banned_words_in_templates(): ...

# test_pot_duplicates.py
def test_same_day_amount_without_reference_is_refused(): ...    # possible_duplicate. Positive: with a different reference, accepted
def test_same_reference_token_from_anyone_is_refused(): ...     # duplicate_reference, and the refusal names nobody
def test_credit_token_single_use_at_the_clerk(): ...            # credit_already_used, even with a second pot_match from a wiped agent
def test_token_free_after_correct_out(): ...                    # A corrected-out credit can be used again
def test_reference_mismatch_refuses_confirm(): ...              # reference_differs. Positive: equal tokens confirm
def test_withdrawn_claims_do_not_block_a_new_claim(): ...
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_pot_state.py tests/test_pot_duplicates.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement `PotBook` and the hooks**

`handle` routes in this order. Treasurer's commands and agent `pot_match` come from the treasurer's desk. Member commands come from their own desk. `pot` and refusals come from the group. A `transfer` claim to the treasurer returns `use_contribution` by peeking at the JSON. Anything else returns `None`. IST is `timezone(timedelta(hours=5, minutes=30))`.

- [ ] **Step 4: Run everything**

Run: `uv run pytest -q` and `git diff --stat 4ba9d82 -- kitty/claims.py kitty/ledger.py kitty/statements.py kitty/statement_import.py kitty/llm.py kitty/llm_brain.py kitty/agent.py`
Expected: all pass, golden included, and the diff prints nothing.

- [ ] **Step 5: Commit**

```bash
git add kitty/pot.py kitty/clerk.py tests/test_pot_state.py tests/test_pot_duplicates.py
git commit -m "Pot trip: decisions, status by fold, duplicate rules and clerk hooks. Mode A untouched"
```

---

### Task 4: Candidate search and `PotAgent`

**Files:**
- Create: `kitty/pot_agent.py`
- Test: `tests/test_pot_candidates.py`, `tests/test_pot_agent.py`, `tests/test_pot_privacy.py`

**Interfaces:**
- Consumes: `P.PotConfig`, `P.token`, `PersonalAgent`, `S.Row`, `S.counterparty`.
- Produces:

```python
@dataclass(frozen=True)
class Candidate: result: str; row: S.Row | None   # exact | amount_differs | none

def taken_back(rows: list[S.Row], row: S.Row) -> bool
    # A later line with the same ref that is a UPI/REV narration or a debit of the same amount.
def find_candidate(rows, amount, around, from_person, directory, used: set[str], want_token: str | None, key: bytes, slack_days: int = 2) -> Candidate
    # Credits only: UPI/CR/, right person, within slack, not taken back, row_id not in used.
    # Equal amount: exact, preferring want_token's row, else the earliest. Otherwise any credit left: amount_differs. Otherwise none.

@dataclass
class PotAgent(PersonalAgent):
    cfg: P.PotConfig | None = None
    pot_key: bytes = b""
    bound: dict[str, str] = field(default_factory=dict)  # contribution id -> row id. Saved by to_state
```

Overrides: `_transfer_claim` (payment to the treasurer becomes a contribution with `ref_token`), `_scan` (after `super()._scan()`, also files payments to the treasurer dated from `collect_from` to the day before the trip), `_from_clerk` (`KITTY_POT_CHECK`, `KITTY_RECHECK p…`, and `**p3** ready`), `_from_owner` (`paid …` and `received t4` / `not received t4`, parsed by plain code before anything else), `_check` (no rows: ask rather than auto-answer), `to_state`, `restore`.

**Command:** `paid <amount> <yyyy-mm-dd> [ref <value>] [show]`. Amount accepts `₹`, commas and up to 2 decimals, and is converted with `S.to_paise`. `ref` is 4–16 letters and digits. `show` sets `ref_last4`.

**Private chits, exact:** `Found it: {9 Oct}.` · `Something near this date, for a different amount.` · `Nothing yet. Your statement ends {8 Oct}.` · `Nothing yet. You haven't added a statement.` They hold no narration, and no counterparty beyond the claim's.

- [ ] **Step 1: Write the failing tests**

```python
# test_pot_candidates.py
def test_exact_match_returns_the_row(): ...
def test_reversed_credit_is_not_a_candidate(): ...     # credit then UPI/REV same ref: none. Positive: without the REV line, exact
def test_used_row_is_skipped(): ...                    # one credit, two claims: exact then none
def test_two_equal_credits_serve_two_claims(): ...     # separate refs both match, in order
def test_token_prefers_its_own_row(): ...
def test_different_amount_reports_differs(): ...
def test_other_persons_credit_is_none(): ...           # Review Focus 5
def test_debit_or_reversal_alone_is_never_a_credit(): ...
def test_outside_slack_is_none(): ...
def test_salary_with_the_same_amount_is_not_a_candidate(): ...

# test_pot_agent.py
def test_check_posts_a_typed_match_and_a_private_chit(): ...      # Desk message parses via parse_claim; no narration anywhere
def test_none_never_says_not_received(): ...                      # Review Focus 5: chit is "Nothing yet", no "did not", no "didn't arrive"
def test_recheck_finds_a_credit_added_later(): ...                # Review Focus 4: rows grow, KITTY_POT_CHECK again, exact
def test_memory_wipe_keeps_the_clerk_check(): ...                 # New PotAgent offers the row again, clerk refuses credit_already_used
def test_bound_rows_survive_restore(): ...
def test_member_statement_files_contribution_not_transfer(): ...  # including a pre-trip line. Positive: payment to another member stays a transfer
def test_typed_paid_with_no_statement(): ...
@pytest.mark.parametrize("text, ok", [("paid ₹5,000 2026-10-09", True), ("paid 5,000.50 2026-10-09 ref U441001 show", True),
    ("paid 5000 rs 2026-10-09", False), ("paid abc 2026-10-09", False), ("paid 0 2026-10-09", False), ("paid 5000 9 Oct", False)])
def test_typed_input(text, ok): ...                               # Review Focus 2: bad input gets a plain retype message, brain uncalled
def test_no_statement_member_is_asked_about_a_reimbursement(): ...# not auto "did not reach you"
def test_rejected_claim_finds_payers_reversal(): ...              # Reuses find_reversal, private note, `withdraw p3 payment_failed`

# test_pot_privacy.py
def test_collection_makes_no_model_call(): ...                    # RecordingBrain: whole collection flow, zero calls
def test_review_never_shows_credits_or_pre_trip_lines_to_model(): ... # treasurer `start`: only in-trip debits. Positive: an in-trip debit is seen
def test_desk_holds_no_statement_text(): ...                      # canaries for salary, rent, merchant, unrelated name, balance, full ref. Positive: planted one is detected
def test_reference_stays_a_token_on_desks(): ...
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_pot_candidates.py tests/test_pot_agent.py tests/test_pot_privacy.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement `kitty/pot_agent.py`**

The check must not require `started`. So a treasurer who never presses "Go through my statement" sends nothing to the model.

- [ ] **Step 4: Run everything, including the zero-diff check from Task 3**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add kitty/pot_agent.py tests/test_pot_candidates.py tests/test_pot_agent.py tests/test_pot_privacy.py
git commit -m "Pot trip: private credit check in plain code, typed claims, no model during collection"
```

---

### Task 5: Trip setting, provisioning, processes, replay

**Files:**
- Modify: `kitty/trips.py`, `kitty/home_app.py`, `kitty/provision.py`, `kitty/agent_runner.py`, `kitty/clerk_daemon.py`, `app/home.html`
- Test: `tests/test_pot_replay.py`, `tests/test_pot_setup.py`

**Interfaces:**
- Consumes: `PotBook`, `PotAgent`, `P.PotConfig`.
- Produces:

```python
# trips.py
def clean_pot(body: dict, doc: dict) -> dict   # {"treasurer": slug, "collect_from": iso} or {} for none.
    # Raises Invalid. Treasurer must be on the roster. collect_from <= start, and within MAX_DAYS before it.
# home_app.py
#   PUT /api/trips/{id}/pot   body as above. Locked once not editable, same as other edits. home.detail adds treasurer and collect_from.
#   Removing a person who is the treasurer clears both fields. open_trip refuses a treasurer who isn't on the roster.
# provision.py _finish: when trip has treasurer, add "pot_key": hex to each {slug}.json. Generated once, kept in secrets_doc. Never in clerk.json.
# clerk_daemon.py
def build_clerk(config, trip_doc) -> Clerk     # attaches PotBook only when trip_doc has treasurer
def should_log(body: str, pot: bool = False) -> bool  # pot adds ^\s*(confirm|reject|ask|recheck|correct|call|cancel|pot)\b
# board(): adds "pot": clerk.pot.board_view(clerk) only when set. clerk_plan() uses clerk.pot.plan when set.
# agent_runner.py: PotAgent when treasurer is set. NO_STATEMENT guard lets `paid`, `received` and `not received` through.
```

**Replay with real times, Mode B only:** the log line gains `"at"`, from the message's server timestamp (`e.raw["payload"]["timestamp"]`, milliseconds, converted to ISO). Before each `handle`, live and on replay, set `clerk.now = lambda: at`. Mode A log lines keep their exact shape.

**`app/home.html`:** on the review step, a "Who holds the group's money?" control, defaulting to "Nobody, everyone pays and we settle up". Choosing someone reveals the collection start date and this text:

> Whoever runs this Kitty server can read every room, including private ones. If that's the person holding the money, the others have only their word that they aren't reading.

- [ ] **Step 1: Write the failing tests**

```python
# test_pot_replay.py
def test_replay_restores_status_decisions_and_original_times(): ...  # Log a full flow, rebuild, replay: equal decisions including `at`
def test_every_state_changing_command_is_logged(): ...               # Enumerate each command and claim type against should_log(pot=True)
def test_mode_a_should_log_is_unchanged(): ...                       # "confirm p1" False without pot. Existing commands True
def test_mode_a_log_line_shape_is_unchanged(): ...

# test_pot_setup.py
def test_clean_pot_validation(): ...                                 # Off-roster, after start, too early refused. Empty means none
def test_treasurer_removed_from_roster_clears_the_setting(): ...     # Review Focus 3
def test_open_refuses_an_invalid_treasurer(): ...                    # Review Focus 3. Positive: valid one opens
def test_treasurer_is_fixed_after_group_opens(): ...
def test_pot_key_reaches_agents_and_not_the_clerk(): ...             # Fake backend from test_provision.py. Positive: agent has it
def test_trip_with_no_treasurer_has_no_pot_key_and_no_book(): ...
def test_home_page_states_the_host_limit(): ...
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_pot_replay.py tests/test_pot_setup.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement the changes listed in Interfaces**

- [ ] **Step 4: Run everything**

Run: `uv run pytest -q`
Expected: all pass, golden included.

- [ ] **Step 5: Commit**

```bash
git add kitty/trips.py kitty/home_app.py kitty/provision.py kitty/agent_runner.py kitty/clerk_daemon.py app/home.html tests/test_pot_replay.py tests/test_pot_setup.py
git commit -m "Pot trip: treasurer setting, per-trip key, processes and replay with real times"
```

---

### Task 6: Person app and board

**Files:**
- Modify: `kitty/pot.py`, `kitty/person_app.py`, `app/person.html`, `app/board.html`
- Test: `tests/test_pot_app.py`

**Interfaces:**
- Consumes: Task 3's templates and `board_view`.
- Produces:

```python
# pot.py: derived from room text alone, used by the person app and by verify.py
@dataclass(frozen=True)
class RoomPot: asked: dict[str, int]; contributions: dict[str, dict]  # id -> {from, amount, status, basis}
    payments: list[L.Payment]    # currently confirmed, after corrections
def parse_room(lines: list[str], ids_by_name: dict[str, str]) -> RoomPot
# person_app.py
#   build_desk gains branches for "**pN** ready" (kind "contribution") and "**pN** to check" (kind "pot_review", with candidate).
#   /api/feed adds "pot": {"treasurer", "is_treasurer", "mine": {asked, credited, outstanding, above}, "roll": [...]} only when set.
#   MACHINE regex gains KITTY_POT_CHECK.
```

**No new routes.** Buttons and forms use the existing `/api/say`, which posts as the person.

**`app/person.html`:**

| Who | Adds |
|---|---|
| Member | "Your contribution" card with asked, confirmed, outstanding. "I paid" form: amount, date, optional UPI reference, and an unticked "Show the last 4 in the group". It sends `paid …` to `private` |
| Member's chits | Release and Drop as today. Status words from the table below. Withdraw on `needs_clarification` and `rejected` |
| Treasurer | "Ask for money" form, sending `call …` to `desk`. Review chits with Confirm, Not received and Ask. **Confirm by hand** appears only on `none`, with a one-line explanation. Check again. "Correct this" on confirmed or rejected, with a reason and the balance warning |
| Group tab | `plainGroup` strips `**p3…` ids and the `[d7 hash]` tag, as it does for `t` ids today. The `pot` button sits beside "Show balances" |

| Status | Member sees |
|---|---|
| `submitted`, `recipient_review` | Waiting for {T} |
| `needs_clarification` | {T} can't confirm this yet: {plain sentence for the code} |
| `confirmed` | {T} confirmed it arrived |
| `rejected` | {T} says it didn't arrive. It isn't counted |
| `withdrawn` | Withdrawn |

**`app/board.html`:** when `d.pot` exists, a roll-call table and the line `{T} should be holding {money}, by the records.` When position is negative: `{T} has put in {money} of their own, by the records.` The "Everyone's square" banner also needs no open contribution, and works with no expenses when `d.pot` exists.

- [ ] **Step 1: Write the failing tests**

```python
def test_parse_room_follows_corrections(): ...         # confirm, correct out, correct in: payments right at each point
def test_parse_room_matches_board_view(): ...          # On a full fake flow, independent route agrees with PotBook
def test_desk_builds_contribution_and_review_slips(): ...
def test_feed_has_no_pot_key_on_a_mode_a_trip(): ...
def test_review_slip_carries_no_statement_text(): ...
def test_pages_contain_no_banned_words(): ...          # person.html, board.html, home.html
def test_mode_a_build_desk_output_is_unchanged(): ...  # Golden three-person desk text gives the same slips
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_pot_app.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement as listed**

- [ ] **Step 4: Run everything**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add kitty/pot.py kitty/person_app.py app/person.html app/board.html tests/test_pot_app.py
git commit -m "Pot trip: member and treasurer screens, roll-call on the board"
```

---

### Task 7: Dataset, hand-worked numbers, `verify`, live script. **Gate 2**

**Files:**
- Create: `data/synthetic/pot/{trip.json,expected.md,asha.csv,rohan.csv,meera.csv,dev.csv,farhan.csv}`, `scripts/pot_trip.py`
- Modify: `scripts/verify.py`
- Test: `tests/test_pot_story.py`

**Interfaces:**
- Consumes: `P.parse_room`, `P.chain_check`, `P.settle_via`, `scripts.six_person.Person`, `board`, `check`.

**Dataset:** Goa, trip 16–19 Oct 2026, `collect_from` 25 Sep, treasurer Asha. Six people. **Esha has no statement,** and types the claim. Keep the numbers from spec §7's worked example. References are short, like `U441001`, as the importer refuses 12-digit runs.

| Person | Story |
|---|---|
| **Asha**, treasurer | Statement with canaries: `SALARY … CANARY-SALARY-ASHA`, `RENT … CANARY-RENT-ASHA`, an in-trip `NETFLIX` line, `UPI/CR/NEHACANARY K/…` from an unrelated person, unique balances. Credits for the people below. Spends `AIRBNB VILLA` ₹24,000 and `FISHERMANS WHARF` ₹9,000, which `RulesBrain` knows |
| **Rohan** | Pre-trip ₹5,000, from a statement. Exact, confirmed by records |
| **Esha** | Types ₹5,000 with `ref … show`. Exact. `…last4` appears in the group |
| **Farhan** | ₹5,000, no reference. Exact |
| **Dev** | ₹5,000. Spends `BIG BAZAAR` ₹3,600 |
| **Meera** | ₹5,000 fails: debit, then `UPI/REV`, and no lasting credit for Asha. `none`, rejected, Meera's agent finds the reversal, withdraws. Then ₹3,000 that lands. Confirmed, **corrected out, corrected back** |

**Write `expected.md` by hand before any live run,** using the same format as `six/expected.md`. Net before settling: Asha +3,900, Rohan −1,100, Esha −1,100, Farhan −1,100, Meera −3,100, Dev +2,500. Pot position −₹10,000. Settling: four people pay Asha, Asha pays Dev. Then Everyone's square.

**`verify.py`:** everything new sits under `if TRIP.get("treasurer"):`. Payments from `parse_room` join `payments` before `net`. `forbidden_terms` skips a missing statement and adds Asha's balance values, raw and via `C.money`, in Pot mode only. New checks, named:

```text
pot: balances recomputed from room text equal the board
pot: every confirmed contribution traces to the treasurer's account on the treasurer's desk
pot: no decision command from any agent took effect
pot: decision chain is unbroken
pot: no credit token was confirmed twice
pot: no full statement reference in any room
pot: no run of 5 or more digits in a group pot line
pot: clerk.json holds no pot_key, and each person's file does
pot: board pot position equals -net[treasurer] - treasurer's shares
pot: every hub plan payment involves the treasurer
pot: the treasurer is refused every other person's private room
```

**`scripts/pot_trip.py`:** drives the story through the home app's routes, as `six_person.py` does, reading no state files except under `--attack`. Honours `KITTY_HOME_URL`. Flags: `--restart-midway`, `--plant {word,ref,chain}`, `--shots <folder>`, and `--attack`, which reads agent keys the way `gate1d_release_live.py` does and tries `confirm` as a member and as each agent, live (B2). Prints `TRIP_DIR=` and ends by running `verify.py`. Exits non-zero on any failed check.

- [ ] **Step 1: Write `expected.md`, the CSVs and `trip.json`, and check them**

```python
# tests/test_pot_story.py, a fake-Switch world like test_six_person.py, with RulesBrain
def test_statements_pass_the_upload_checks(): ...
def test_whole_story_matches_the_hand_worked_numbers(): ...   # to the paise, before and after settling
def test_mode_a_verify_path_is_not_touched(): ...             # no pot key in the three and six trip docs
```

Run: `uv run pytest tests/test_pot_story.py -q`
Expected: PASS. If it doesn't, find out whether the numbers or the code are wrong, and fix what's wrong, not the check.

- [ ] **Step 2: Add the `verify.py` block and `scripts/pot_trip.py`**

- [ ] **Step 3: Confirm Switch, then start this worktree's own home app**

Run: `curl -s localhost:8000/health`
Expected: `{"status":"ok"}`. If not, follow `HANDOFF.md`'s restart steps.

Port 7000 belongs to the main checkout, and may be in the owner's use, so leave it alone. The brain is read by the processes the home app spawns, so it's set on the home app, not on the script. Start it through the app's preview tools, or as a background process:

`KITTY_BRAIN=rules KITTY_HOME_PORT=7010 uv run python -m kitty.home_app`

- [ ] **Step 4: First live run, no model**

Run: `KITTY_HOME_URL=http://127.0.0.1:7010 uv run python scripts/pot_trip.py`
Expected: story finishes, `verify` ends with all checks passed, and balances equal `expected.md`. **This is Gate 2. If it hasn't passed by 12:00 IST, stop and report.** Record each failure in the journal.

- [ ] **Step 5: Attacks and negative controls**

Run with the same `KITTY_HOME_URL`: `scripts/pot_trip.py --attack`, then `--plant word`, `--plant ref` and `--plant chain`.
Expected: `--attack` passes, with every attempt refused and balances unmoved. Each `--plant` exits non-zero, and `verify` names what was planted.

- [ ] **Step 6: Commit**

```bash
git add data/synthetic/pot scripts/pot_trip.py scripts/verify.py tests/test_pot_story.py
git commit -m "Pot trip: six-person story, independent checks and negative controls"
```

---

### Task 8: Evidence, regressions, docs. **Gate 3**

**Files:**
- Modify: `README.md`, `docs/PRIVACY.md`, `docs/BUILD_JOURNEY.md`, `docs/DECISIONS.md`, `docs/evidence/`

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Restart run**

Run: `KITTY_HOME_URL=http://127.0.0.1:7010 uv run python scripts/pot_trip.py --restart-midway`
Expected: same balances, all checks pass, and decisions keep their original times (B7).

- [ ] **Step 2: One real-model Pot run**

Stop only the 7010 home app you started, and start it again without `KITTY_BRAIN`. Then run the same command without `--restart-midway`.
Expected: passes. Save the log to `docs/evidence/pot_trip_real_model.log`. **If usage runs out, say so, and label the result as rules-only. Don't claim a model run.**

- [ ] **Step 3: Mode A regressions**

**These use port 7100 and restart the three-person demo, so tell the owner first.** If the main checkout's demo holds that port, ask before stopping it. It can be set up again from `feat/kitty-mvp`.

Run, one at a time from `Kitty-pot`: `scripts/rehearse.sh 3`, `KITTY_HOME_URL=http://127.0.0.1:7010 scripts/rehearse_six.sh 3`, `KITTY_HOME_URL=http://127.0.0.1:7010 uv run python scripts/usd_smoke.py` (check it honours the variable, as `six_person.py` does), `uv run python scripts/gate1_spike.py`, `uv run python scripts/gate1b_humans.py`, `uv run python scripts/gate1c_llm_sandbox.py`, `uv run python scripts/gate1d_release_live.py`, then `uv run python scripts/setup_trip.py`, as gate 1D pollutes a trip.
Expected: 37/37 and 61/61 with the same balances, 28/28, 28/28, 26/26, 8/8, 10/10. **Any change is a stop.** Stop processes only through `runtime.stop` on trips you made. Log files these scripts append to under `docs/evidence/` are real evidence, so commit them in Step 6.

- [ ] **Step 4: Zero-diff check and full suite**

Run: `git diff --stat 4ba9d82 -- kitty/claims.py kitty/ledger.py kitty/statements.py kitty/statement_import.py kitty/llm.py kitty/llm_brain.py kitty/agent.py`, then `uv run pytest -q`
Expected: no diff, and all pass, with the original 184 unmodified. Confirm with `git diff --stat 4ba9d82 -- tests/test_core.py tests/test_scenario.py tests/test_six_person.py tests/test_statement_upload.py tests/test_provision.py`, which prints nothing.

- [ ] **Step 5: Update the docs from real results**

- `README.md`: a "The Pot trip" section with what ran, the numbers, and a limits list that includes confirmation being a person's word, the host, 12 people, synthetic only, and "one payment for several people".
- `docs/PRIVACY.md`: spec §11's table and host statement.
- `docs/DECISIONS.md`: ADR-016 status moves to "built, Core", listing what's out.
- `docs/BUILD_JOURNEY.md`: what happened, with real times, failures kept. Mark ADR-016's spec departures that changed during the build.

- [ ] **Step 6: Commit, then stop**

```bash
git add README.md docs/PRIVACY.md docs/BUILD_JOURNEY.md docs/DECISIONS.md docs/evidence
git commit -m "Pot trip Core: evidence, regressions, docs"
```

**Don't merge, and don't push beyond `feat/pot-trip`.** Report the results, and let the owner decide about merging and the video.
