# Kitty code review and release hardening

Branch `fix/kitty-release-hardening`, started from `feat/kitty-mvp` at `6fd0204`, which is tagged `checkpoint/pre-hardening-review`. Reviewed on Sat 7 Nov 2026, 19:50 to 20:30 IST, in a separate worktree (`~/Desktop/Switch/Kitty-hardening`), so the apps already running from `~/Desktop/Switch/Kitty` were not touched.

This is a review of a hackathon build, done in about forty minutes of focused work plus two read-only reviewers. It is not a professional security assessment, and it does not show that Kitty is production-ready.

## 1. Executive summary

- **Baseline reproduced.** At `6fd0204`, `uv run pytest` gave **660 passed**. The browser check gave 0 problems across 35 states × 3 widths, with axe.
- **Found and fixed:**
  - two **financial integrity bugs** in the clerk (F1 and F2), where a withdrawn payment could be confirmed and one expense could count twice;
  - one **replay idempotency bug** (F3);
  - one **forgery-in-the-view bug** on shared-expense trips (F4);
  - one **privacy leak into logs** (F5);
  - one **frontend error-handling bug** that made failed sends look successful (F6);
  - one small file-permission race (F14).
- Each fix has a regression test that failed first.
- **Now:** **736 tests pass**: the 660 originals, 76 new, and 1 original assertion extended. The frozen Mode A transcripts are unchanged. The browser check gives 0 problems, with a new failure-path interaction.
- **Deferred** (documented below, not fixed):
  - the model's "shared" guess files a claim without naming it on the desk;
  - the prompt is on the process command line;
  - the home app has no login;
  - posts are lost if the clerk crashes between deciding and posting, because there is no outbox;
  - several design limitations: released expenses can't be retracted, and there's no treasurer hand-over.
- **Not re-run:** none of the live Switch and Mattermost runs (`verify.py`, `six_person.py`, `rehearse.sh`, `pot_trip.py`, the gate scripts) were re-run on this branch. That avoided new Mattermost accounts against the 250 active-user limit. The fixes touch code those runs exercise, so **re-run them before calling this branch verified end to end.**

## 2. Repository state found

| | |
|---|---|
| Submission branch | `feat/kitty-mvp` at `6fd0204`. It equals `origin/feat/kitty-mvp` and has a clean working tree |
| Pot trip | Merged: `feat/pot-trip` (`8fd29c2`) is an ancestor of `6fd0204`. (A memory note saying it was unmerged was stale.) |
| UI | Merged: `feat/kitty-ui` is at `6fd0204` |
| Worktrees | `Kitty` (`feat/kitty-mvp`), `Kitty-ui` (`feat/kitty-ui`), `Kitty-pot` (`feat/pot-trip`), and now `Kitty-hardening` |
| Tags | `checkpoint/pre-product-sprint`, and the new `checkpoint/pre-hardening-review` on `6fd0204` |
| Remote | `origin` = `github.com/Sahvendra7/Kitty`, private. Nothing was pushed |

## 3. Architecture, as the code implements it

- **People and rooms.** Each person has a private room (the person and their agent) and a desk (the person, their agent and the clerk). The group room holds the people and the clerk.
- **Clerk.** `kitty/clerk.py`, plus `kitty/pot.py` on a Pot trip. A pure state machine.
  - Its authority comes only from the sender id Switch sets and the room a message arrived in.
  - It accepts typed `KITTY/1` claims from agents and plain commands from owners.
  - `kitty/clerk_daemon.py` connects it to Switch, writes state-changing messages to an append-only log, and rebuilds by replay.
- **Money.** Integer minor units throughout (`kitty/ledger.py`, `kitty/claims.py`). The validator rejects floats and bools, and amounts are capped at ₹10,00,000.
- **Agents.** `kitty/agent.py` and `kitty/pot_agent.py`.
  - They read the owner's statement locally.
  - They call the model only through `kitty/llm.py`: no tools, no MCP, no settings, an empty working directory.
  - Amounts and dates in claims always come from the parsed statement or from what the person typed, never from the model.
- **Person app** (`kitty/person_app.py`). Each person's view.
  - It reads their Mattermost channels with their own session, behind a single-use invite and then an HttpOnly cookie, with Host and Origin checks.
  - The board (`/api/board` on the clerk daemon) shows the clerk's own figures.
- **Home app** (`kitty/home_app.py`). The organiser's view, on localhost. It creates trips and invites, and has **no login of its own**.

## 4. What was reviewed

| Area | Files | By |
|---|---|---|
| Money and state machines | `claims.py`, `ledger.py`, `clerk.py`, `pot.py` (all lines) | Lead reviewer |
| Persistence and replay | `clerk_daemon.py`, `runtime.py`, `trips.py`, `paths.py`, `switch_client.py` | Lead reviewer |
| Web surface and auth | `home_app.py`, `person_app.py`, `access.py` | Lead reviewer |
| Model boundary | `llm.py`, `llm_brain.py`, `agent_runner.py` | Lead reviewer and reviewer B |
| Agents, CSV, provisioning, secrets, git history | `agent.py`, `pot_agent.py`, `statements.py`, `statement_import.py`, `provision.py`, `scripts/setup_trip.py`; secrets scan of all history | Reviewer B (read-only), key claims re-checked by the lead |
| Frontend and the cat | `app/*.html`, `app/ui/*.js`, `kitty.css`, `tests/test_ui.py` | Reviewer A (read-only), key claims re-checked by the lead |

**Not reviewed in depth:**
- `scripts/verify.py`, `six_person.py`, `pot_trip.py`, `play_story.py`, `film.py`, `capture.py`, `eval_agent.py`, `usd_smoke.py` and the `gate*` scripts. They are test and demo harnesses, not the product.
- `app/film.html` and `app/stage.html`, which are demo pages.
- Switch itself (`switch-src`), which is out of scope.

## 5. Findings

Confidence: **High** means reproduced by a test. **Medium** means read in the code and not reproduced.

### P0

None found.

### P1: fixed

**F1. A withdrawn transfer could become confirmed.** Confidence High.
- **Where:** `kitty/clerk.py`, `_release_answer` and `_withdraw`.
- **What happened:** the receiver's agent answers "it reached me" (held). The payer then withdraws (`payment_failed`), and the receiver releases the held answer. The clerk moved `withdrawn` to `confirmed` and changed balances by the full amount. This also affected Pot trips, through the ordinary transfer path.
- **Fix:** releasing an answer needs the transfer to still be `awaiting_receiver`. Withdrawal discards a held answer.
- **Tests:** `test_f1_*`, and a 42-case transfer state table (`test_transfer_transitions_are_listed_or_refused`).

**F2. One expense could count twice.** Confidence High.
- **Where:** `kitty/clerk.py`, `_find_conflict` and `_resolve_conflict`.
- **What happened:** when two owners released the same expense, both went to `conflict_hold`. A third owner's identical release looked only for *released* matches, so it went straight to `released`. When one of the held copies was then dropped, the other was released, and two copies counted. Reproduced: ₹900 counted twice.
- **Fix:** held conflicts count as matches, and a conflict clears only when exactly one copy is left. The two-copy case keeps its exact messages.
- **Tests:** `test_f2_*`, with balances worked out by hand.

**F4. On a shared-expense trip, anyone in the group room could pass as the clerk in other people's apps.** Confidence High.
- **Where:** `kitty/person_app.py` `feed()`, which read every group post by its text. The cat consumes it in `app/ui/guide.js` and `app/person.html`.
- **What happened:**
  - a friend typing `**t3 confirmed by both sides:** …` moved someone's transfer slip to "confirmed";
  - a typed expense line showed as an expense card;
  - the cat read out a typed "Everyone's square" or "Suggested payments".
- **Scope:** the clerk's ledger and the board were never affected. Pot trips already filtered by sender.
- **Fix:** every person config now names the clerk's chat account, written by both set-up routes. The app trusts only that sender. The front end's existing `f.clerk !== false` checks are correct once `clerk` is always a boolean.
- **Limit:** trips set up before this fix have no `clerk_bot` in their configs, so they keep the old behaviour until they are set up again. That includes the owner's running trip.
- **Tests:** `test_f4_*`, with a positive control: the real clerk's line still moves the slip.

**F5. A model timeout wrote the prompt, which holds statement lines, to the person's log.** Confidence High.
- **Where:** `kitty/llm.py` `complete()`.
- **What happened:** `subprocess.TimeoutExpired` quotes the whole argv, and the prompt is on it. `llm_brain` caught only JSON errors, so the exception reached `log.exception` in `agent_runner.py`, which writes to `logs/<person>.log`. `PRIVACY.md` said a failed model call logs a code only, which was false for this path.
- **Fix:** a timeout or a missing CLI becomes `LlmError` with no chained cause.
- **Tests:** `test_f5_*`.

### P2: fixed

**F3. A message delivered twice was applied twice.** Confidence High for the mechanism. The crash itself wasn't reproduced live.
- **Where:** `kitty/clerk_daemon.py`.
- **What happened:** the daemon logs an event, applies it, posts, and only then saves the cursor. A crash between the log and the cursor makes Switch deliver the message again. Most commands refuse a repeat, so they were harmless, but `call 5000` asked everyone a second time.
- **Fix:**
  - log lines carry Switch's `message_id`, and an id already in the log is skipped;
  - the log line is fsynced before it is applied;
  - posts that fail to *connect* are retried. Posts that may have reached Switch are not, so a decision can't appear twice and break the Pot trip's decision chain.
- **Tests:** `test_f3_*`.

**F6. A failed send looked like a success, and lost what was typed.** Confidence High.
- **Where:** `app/person.html`.
- **What happened:** the Pot forms cleared and said "Filed" before the send result was known. `say()` didn't catch network errors. The error it did show was erased by the 900 ms poll.
- **Fix:** forms clear only on success, and the send error stays up until a send works.
- **Also fixed:**
  - Delete trip now asks first (`home.html`);
  - the add-person form ignores a second press while one is in flight;
  - the redraw focus selector uses `CSS.escape`. A quote in agent text could make it throw, which stopped the page redrawing.
- **Test:** a new interaction in `scripts/ui_check.py`. It failed first, which is how the poll erasing the error was found.

### P2: deferred, with the recommended fix

**F7. The model's "shared" label files a claim without the human seeing which purchase it is.** Confidence High, from reviewer B's fake-brain simulation.
- **Where:** `agent.py`, around lines 166-180.
- **What happens:** a line the model labels "shared" goes straight to the desk, split evenly. The desk slip shows amount, category, date and split, and the private summary says only "Put N on your desk".
- **What still holds:** the amount comes from the statement, and the claim still needs the owner's own `release`. So a prompt-injected merchant line can't move money by itself. But it can make a private purchase into a group claim that a careless release approves.
- **Recommended fix:** name the filed titles in the private summary, or treat "shared" like "unclear", as a suggestion the person confirms.
- **Why not now:** it changes the frozen Mode A transcripts and the demo flow, which needs the owner's decision.

**F8. The prompt is on the command line** (`llm.py`, `claude -p <prompt>`).
- **Risk:** for up to 180 seconds, other local users can read the statement lines with `ps`.
- **Recommended fix:** send the prompt on stdin.
- **Why not now:** it changes how the sandboxed CLI is invoked, and needs a live model check, `gate1c_llm_sandbox.py`, to confirm the sandbox flags still hold. That wasn't run in this pass.

**F9. When the model reads a free-text reply, it can leave the owner out of the split** (`agent.py` `_ask_brain`).
- **What happens:** the private message names who it was filed for, so this is visible.
- **Recommended fix:** add the owner back unless the reply excludes them, as `_parse_command` does.

**F10. The home app has no login.**
- **Risk:** anything that can reach `127.0.0.1:7000` can read and regenerate invite links, and delete trips that haven't been opened. Host and Origin checks stop other websites, but not other local users or processes.
- **Status:** acceptable for a one-laptop demo. Must be fixed before any shared deployment.

**F11. No outbox for the clerk's posts.**
- **The gap:** if the clerk process dies after applying a decision and before posting it, the decision stands, but the room never hears it. On a Pot trip, every person's app reads its figures from the room, so they would differ from the board.
- **Recommended fix:** a transactional outbox. Write posts with an id next to the event, mark them sent, and resend unsent posts on start-up. Switch-side dedupe by id would make that safe.
- **Why not now:** it touches the "room is the record" design.

**F12. Messages lost between Mattermost and Switch can't be detected.**
- **Background:** the journal records one message in about 700 that reached Mattermost and never reached Switch.
- **What the code does:** the clerk can't detect a message it never received. The person app shows "Sending" for a while, then lets the person press again.
- **Why a resend is safe:** `release`, `drop`, `withdraw`, `confirm`, `reject`, `ask` and `correct` all refuse a repeat, and `call` is now deduplicated by message id. A resend is a *new* message.
- **What's missing:** an explicit "the clerk hasn't answered" state. **This issue is not fixed. It is surfaced only as "press again".**

**F13. Design limitations, kept as they are:**
- a released expense can't be retracted;
- a challenged transfer can't later be confirmed, so it can only be withdrawn and filed again;
- a treasurer can't be changed after the group opens;
- there's no split-payment allocation. A payment covering several members is confirmed only by the treasurer's manual word.

### P3: fixed

**F14.** `scripts/setup_trip.py` wrote credentials and then chmodded them, so they were briefly readable under the umask. It now uses the atomic, 600-from-creation write.

### P3: deferred

- **Desk buttons.** Most have no double-press guard. Confirm, reject, withdraw and Pot release are examples. The clerk refuses the repeat, so the only cost is a confusing "Refused" notice.
- **Polling.** Polls can overlap on a slow server (person app every 900 ms, board 800, home 1200). On a Pot trip the person app reads the whole group history on every poll.
- **Invites.** Invite links and sessions have no server-side expiry. The cookie lasts 30 days.
- **Temp file.** `person_app.write_private_text` uses a fixed `.tmp` name without `O_EXCL`.
- **Cosmetic bug.** `home_app.remove_person` reads the removed person's name after deleting them, so `pot_cleared` falls back to the slug, title-cased.
- **Bad request body.** A JSON body that is not an object gives a 500 rather than a 422.
- **Accessibility:**
  - the companion's `aria-live` region is rebuilt on every redraw;
  - the desk count badge has no label;
  - in sheet mode, `point()` returns focus to the cat button.
- **Currency.** `guide.js:130` formats one figure with a different currency field than the line above it.

## 6. What was checked and found sound

- **Money.**
  - All amounts are integer paise.
  - JSON floats and bools are rejected, and amounts are limited to 1 to 10⁸.
  - Shares must sum exactly.
  - Even splits give the leftover paise to the first people listed.
  - `balances()` asserts the ledger nets to zero.
  - An independent oracle, using paid − used + sent − received over 300 random cases, agrees with the ledger. Settlement plans clear every balance in at most n−1 payments.
  - The Pot hub plan clears every balance.
- **Pot integrity.**
  - Status is a fold over append-only decisions, and corrections add a decision rather than overwrite one.
  - A credit token can't back two confirmed contributions, `correct … received` included.
  - Manual confirmation needs the agent to have found nothing.
  - Only the treasurer's human account on the treasurer's desk can decide.
  - Everything runs under one lock, one event at a time.
- **Authority.**
  - Agents can't send commands.
  - Strangers on a desk are refused.
  - Commands in the group room are refused.
  - Agents obey only their owner's sender id, and the clerk's machine messages only from the clerk's sender id.
- **Web.**
  - All four `esc()` helpers escape `& < > " '`, and every traced sink of untrusted text is escaped.
  - The chat renderer escapes first, then adds `<b>` and `<code>`.
  - Host and Origin checks are in place, and CSV upload needs a preflight.
  - Cookies are HttpOnly and `SameSite=Strict`. Tokens are 24 and 32 bytes, stored hashed and compared in constant time.
  - No SQL, no shell with user input, and no path built from user input. Trip ids are pattern-checked.
- **CSV upload.**
  - Limits: 256 KB while streaming, 2,000 rows, UTF-8 only, no NUL bytes, strict quoting, an exact header, and a pattern for every field.
  - The narration must start with a letter or digit, which blocks formula injection.
  - The file name is never used as a path.
  - Errors carry a line and column, never the content.
- **Model sandbox:** `--tools ""`, `--strict-mcp-config`, `--setting-sources ""`, an empty temporary working directory, a filtered environment and stdin from `/dev/null`. The model can't post, read files, or set amounts.
- **Cat companion.** No fetch in `companion.js`, `guide.js` or `cat.js`. The only page callback is navigation, and the browser check confirms asking the cat sends nothing.
- **Secrets.** A scan of all git history found no credentials. One hit was a test fixture string. `state/`, `people/`, `trips/`, `logs/` and `.env*` are ignored and were never committed.
- **Dependencies.** `pip-audit` on the exported lockfile reports no known vulnerabilities. The database was reachable: a positive control, `jinja2==2.10`, was flagged.

## 7. Privacy data flow

| Data | Starts | Model provider | Switch and Mattermost | Clerk | Logs | On disk |
|---|---|---|---|---|---|---|
| Statement lines | Person's browser, then their folder | Spending lines inside the trip, masked, once the person presses Go | No | No | Not after F5. Before F5, on a model timeout | `people/<p>/statement.csv`, mode 600 |
| Merchant titles | Model output | It wrote them | Private room only | No | No | `agent_memory.json`, mode 600 |
| Payment references | Person types them | No | Private room, as typed | Keyed token only | No | Agent memory |
| Released claims | Agent, then the owner releases | No | Desk and group | Yes | Clerk event log | `clerk-events-*.jsonl` |
| Treasurer decisions | Treasurer's desk | No | Desk and group | Yes | Clerk event log | Same |
| Agent messages | Agent | Only the reply-reading prompt | Private room and desk | Desk only | No | Mattermost history, kept forever (Switch #1202) |

**Trust boundary.** Whoever hosts Switch and Mattermost can read every room, including private ones. On this one-laptop setup, every process runs as the same OS user. No claim is made against the host or the model provider beyond what `docs/PRIVACY.md` lists.

## 8. Test quality

**Strengths:**
- Mode A is pinned by frozen whole-transcript golden files.
- Every negative test in the clerk suites has a positive control nearby.
- The Pot suite has an exhaustive transition table (`test_pot_state.py`).

**Gaps found:**
- **No three-way conflict test (F2).**
- **No withdraw-while-answer-held test (F1).** The transfer state table now covers every command against every transfer state.
- **No replay redelivery test (F3).**
- **No Mode A sender-forgery test (F4).** The Pot path had one.
- **`tests/test_ui.py` only greps the pages for banned strings.** It would not catch markup or callbacks added to the cat later. A DOM test with crafted feed data would.
- **`ui_check.py` had no failure-path interactions.** One was added.

**Expected values.** The new financial tests use hand-worked figures and a separate oracle, never the production ledger.

**Weakened tests.** None. One parity assertion (`test_provision.py`) was extended to expect the new `clerk_bot` field on both set-up routes.

## 9. Verification run in this pass

| Check | Result |
|---|---|
| `uv run pytest` at `6fd0204` | 660 passed |
| `uv run pytest` at the branch tip | 736 passed |
| `tests/test_mode_a_golden.py` (Mode A unchanged) | Passes. Transcripts identical |
| `scripts/ui_check.py --axe`, 35 states × 3 widths plus interactions | 0 problems |
| `pip-audit` on the lockfile, with a positive control | No known vulnerabilities |
| `ruff check kitty/ scripts/setup_trip.py` | Clean |

## 10. Explicitly untested

- **Live runs:** `verify.py` (Pot 68/68), six people 61/61, three people 37/37, the live clerk attacks 10/10, and the gate scripts. **None were re-run on this branch.** The numbers in the README are from `6fd0204` and earlier.
- **Real model:** no real-model run. F5 and F8 were checked with a monkeypatched `subprocess`.
- **Crash timing:** the crash-between-log-and-cursor scenario was tested at the function level, not by killing a live daemon.
- **Platforms:** Safari, Firefox, a real phone and a screen reader.
- **The running apps:** the home app on 7000, the owner's trip and the 7100 demo still run `6fd0204` code. These fixes reach them only after a merge and restart.

## 11. Release readiness

- **Safe enough for a controlled hackathon demonstration?** Yes, on one laptop with synthetic data, provided the live runs pass on this branch. Use a trip set up after F4, so the person apps check the clerk's sender.
- **Are the financial calculations reliable within the tested scope?** Yes. Integer arithmetic, exact splits and zero-sum balances are covered by property tests against an independent oracle, and the two state-machine holes found are fixed. The remaining money-related gaps are workflow limitations (F13), not arithmetic errors.
- **Are the privacy claims accurate?** Mostly. One claim was false until F5 ("a failed model call logs a code only"). Command-line exposure of the prompt (F8) and the home app's lack of a login (F10) are now listed in `PRIVACY.md`. The doc correctly makes no claim against the host or the model provider.
- **What are the remaining security limitations?**
  - no organiser login;
  - every process runs as the same OS user, so there's no isolation between people on one machine;
  - the host can read every room;
  - the prompt is visible in `ps`;
  - the model's "shared" guess relies on the human reading the desk slip;
  - invites don't expire.
- **Before a real-world pilot:**
  - real authentication for organisers;
  - one machine or OS user per person;
  - TLS;
  - a clerk outbox, and a delivery state the person can see;
  - human confirmation of the model's "shared" label, with the merchant named;
  - stdin for prompts;
  - retraction of released expenses, and treasurer hand-over;
  - a model provider retention review;
  - the live suites on CI;
  - real-bank CSV formats.
- **Ready for public GitHub release?** The history scan found no secrets, and the ignored folders were never committed. Making the repository public is the owner's decision. Before doing it, re-run the live suites on the merged code, and check that `docs/evidence/` holds only synthetic data. It is tracked and was grepped for credentials only.

## 12. Live re-runs after the merge (added 22:20 IST)

| Suite | Result |
|---|---|
| Pot trip, `--attack --restart-midway`, real model | **68/68**, plus 31 in-script checks. Two earlier attempts failed at set-up, and a third on a harness keep-alive race, since fixed. See journal section G |
| Three people, `rehearse.sh 1`, real model | **37/37** |
| Six people | **Not completed.** Set-up failed on Switch's `create_room` 500. Not retried, because of the Mattermost account limit (246 of 250) |
