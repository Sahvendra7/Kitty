# Kitty build journey

Chronological engineering journal. All times are IST. Entries are appended as work happens; failed
experiments stay in. Status words mean: **done** (ran, output recorded), **planned** (not yet done),
**unverified** (written or read, not run).

## A. Origin

The problem: after a group trip everyone paid for different things and nobody kept a spreadsheet.
Splitters need someone to type expenses as they happen, and bank-import features move one person's
data into a shared app.

Why Kitty: chosen by the project owner on 6 Nov 2026 (about 22:45) over Readback. Full evidence is in
the research folder that sits beside this repository (`research/switch-hackathon/`, files 09, 10, 12).
Rejected: co-parenting (safe version already exists; the distinctive version could alter records),
tabletop RPG (weak need; Switch not essential), Demarc (`martinvibes/airlock` had already built that design).

Architecture hypothesis at kickoff: private personal agent per person, a deterministic no-LLM clerk,
and a group room that personal agents cannot join. The hypothesis that Switch can enforce that
membership is what Gate 1 exists to test.

Demand is **unverified**: no user research was done.

## C. Development log

### Fri 6 Nov, 23:04 - environment inspection (done)

| Item | Finding |
|---|---|
| Working folder | `~/Desktop/Switch`: `HANDOFF.md` and `research/`. Not a git repo |
| Kitty repo | `github.com/Sahvendra7/Kitty`, private, empty. Cloned to `~/Desktop/Switch/Kitty`, branch `feat/kitty-mvp` |
| "Downloaded Switch" | Only the Console app (`/Applications/Switch Console.app`, from a DMG). **No source checkout existed**, contrary to the kickoff brief's assumption |
| Switch source | Cloned from `sandbox-quantum/switch` to `~/Desktop/Switch/switch-src`, local branch `kitty-pin` at `fa933012b` (5 Nov), 92 commits behind `main` at clone time |
| Tools | node 22.16, pnpm 9.15, python 3.13, uv 0.12, just, docker 29.7 (7.7 GB allocated), gh authenticated as Sahvendra7. Claude Code 2.1.278 |

### Fri 6 Nov, 23:05-23:12 - Switch startup (done)

Commands: `just init-env`, `just up` (1m36s), `just migrate` (3m06s, mostly waiting on the emulated
Mattermost and Postgres on a 16 GB laptop), `just run`.

`GET /health` returned `{"status":"ok"}`. The one-shot `setup` container then registered the bundled
Mattermost as the default bridge and linked `admin@switch.local` to a Mattermost account.

### Fri 6 Nov, 23:12-23:20 - Gate 1A: isolation between separately owned agents (done)

`scripts/gate1_spike.py` creates three non-admin owners through the Gateway, each with their own
registration key, then attacks the room boundary. Evidence: `docs/evidence/gate1a_isolation.json`.

Failed approaches, kept because they taught us something:

1. **The stream test returned 200 for a non-member.** The repo's own protocol doc says a stream opened
   on a non-member room is a 403. We first read this as a leak. It was our bug: without
   `Accept: text/event-stream` the endpoint falls back to a long poll of the agent's own events. The live
   transport at this commit is the WebSocket at `/agents/{id}/connection/ws`, which the synced doc does
   not describe. Rewritten on the WebSocket, the open is refused with `status: 403`.
2. **`rooms: []` in `connection_state`** made a coverage check fail. An `all`-scope connection covers
   rooms without claiming them, so that list is not what we assumed. We now assert on delivered events.
3. **The clerk not seeing its own relay** failed a check. Agents do not get echoes of their own posts.
   We now assert on `read_context`.

### Fri 6 Nov, about 23:18 - design change found by testing: the clerk must leave private rooms

The research plan put `kitty-clerk` in every private room. Switch's documented behaviour is that all
agents in a room read all of it, and the spike confirmed the clerk can `read_context` a private room it
belongs to. So the clerk would have received merchant names from the "Zomato, 11 PM" conversation,
which breaks the requirement that it never does. See ADR-002 in `docs/DECISIONS.md`.

### Fri 6 Nov, by 23:22 - Gate 1B and 1C

**1B, real people (done, 26/26).** `scripts/gate1b_humans.py` makes real Mattermost accounts, links each to
its own Switch owner by that account's own session token, and uses the two-room layout. The clerk gets 403
on private rooms for read, post and self-invite. Arjun's Mattermost account gets 403 on Meera's channels.
A release typed by Meera's agent carries the agent's sender id, not hers. Evidence: `docs/evidence/gate1b_humans.json`.

Failed check, then fixed: we asserted the word "user" appears in a human's sender id. It does not:
Mattermost people look like `@switch-mattermost-<bridge>-<name>:localhost`. The protocol doc says to treat ids
as opaque, so we now compare ids and never parse them.

**1C, the model process (inconclusive).** With `--tools ""`, `--strict-mcp-config`, and `--setting-sources ""`,
the CLI reports zero tools and zero MCP servers. Those two facts are real. But the first run also "passed" *no
tool calls* and *canary absent*, and those passes were hollow: the model replied "Not logged in". The positive
control is what exposed this. It reads the same file with `Read` enabled and should find the canary, and it
did not. The standalone CLI on this Mac is not logged in, because the desktop app holds its own auth. The script
now exits as INCONCLUSIVE unless the model really ran. **Blocked on the owner running `claude auth login`.**

### Fri 6 Nov, 23:22 - correction to this journal

The three headings above first carried times I estimated while writing (23:45, about 00:00). `date` then read
23:22, and the first commit landed at about that time. They are corrected above to what the clock supports. From here
on, stamps come from running `date`.

### Fri 6 Nov, 23:22 - deterministic core (done)

`kitty/claims.py`, `kitty/ledger.py`, `kitty/clerk.py`, `tests/test_core.py`. 53 tests, 0.5 s, no Switch and no LLM.

One failure on the first run: IDs shared a counter, so the first transfer after an expense was `t2`, not `t1`. Fixed
with one counter per prefix.

### Fri 6 Nov, 23:27 - statements, private agent, whole story on a fake Switch (done, no model)

`kitty/statements.py`, `kitty/agent.py`, `kitty/llm_brain.py`, `tests/test_scenario.py`. 68 tests in under a second.

The expected-balances test was worked out by hand before being compared with the code, and that found a real bug: the
no-model brain's exclusion regex read "Yes, but Meera had gone to bed. Just Arjun and me." as excluding Arjun as well,
so Priya carried the whole Zomato bill. The fix limits an exclusion to one clause.

The numbers show why the signature moment matters. Arjun ends up **owed** ₹391.67. If Meera's failed ₹1,500 had counted,
he would have **owed** ₹1,108.33. A failed payment would have flipped who owes whom.

Decision recorded here: the first pass looks only at outgoing money. Incoming lines are opened only when a dispute makes
them necessary, which keeps them out of the model prompt entirely. This is also why the failed payment gets claimed at
all: a production version could pre-check reversals on the payer's side.

### Fri 6 Nov, 23:29-23:31 - first live end-to-end run, verifier, Gate 1D (done)

`scripts/setup_trip.py` built a full trip in one command in under two minutes. `scripts/play_story.py` then played all three
people through real Mattermost accounts, with the no-model `RulesBrain`. It ended on `Everyone's square.` The first live run
worked with no code changes, which we credit to testing the whole story on a fake that enforces room membership.

**The verifier's first run was mostly hollow.** It printed 21/24, and most passes meant nothing: the parser read `messages` where
the API returns `root` and `replies`, so it scanned 0 messages and found no leaks. Same trap as Gate 1C. Verify now fails if it
parses no lines, or if any room has nothing to scan.

Then 33/33. To prove it can fail, `gate1d_release_live.py` planted `TANISHQ GOA JEWELLERS` in the group room and required verify to
fail. It did: `FOUND: ['JEWELLERS', 'TANISHQ']`. That run also attacked the live clerk with malformed claims, an agent typing its
own release, and Arjun releasing Meera's claim from two rooms. All refused, 10/10. Evidence in `docs/evidence/`.

**Gate 1 decision: PASS, with one open item.** Multiple owners, isolation, clerk relay and release enforcement are all demonstrated
live. Gate 1C's live attack on the model is still open because the CLI is not logged in. We continue because the model's zero-tools
state is reported by the CLI itself, and because the whole flow also runs with no model at all.

### Fri 6 Nov, 23:38 - "flaky" setup that was not flaky

The third setup run failed: `never saw "Hi, it's Priya"`. The server log showed the first error was
`Unable to create the new team membership because the team has reached the limit of members`. Mattermost defaults to 50
members per team, and every setup and spike run adds eight accounts. The bridge swallows that error
(`except Exception: pass` in `add_user_to_team`), so bots then fail to join channels and messages simply never arrive. Setup
now raises the limit on our local stack. Anyone re-running a Switch template many times on one Mattermost team could hit this.

### Fri 6 Nov, 23:31-23:41 - product UI (done, tested by clicking)

`app/person.html` and `kitty/person_app.py`: one process per person on its own port, holding only that person's Mattermost session.
It posts as them, so the sender id is real. `app/board.html`: the public trip board, fed only by the clerk's released facts.
`app/stage.html`: all four side by side, for recording. Design notes: color carries the boundary (teal is private, a paper chit is
your desk, marigold is the group), and one line at the top of every screen says who can see it.

Found by looking at screenshots, not by tests: `hidden` lost to `display:inline-block`, so badges read "0"; a loose regex left a
trailing comma on share amounts (`₹1,600.00,`), and dates read "09 Oct"; the Withdraw button stayed after use; small bar labels
overlapped their bars; Switch's own auto-greetings ("Hey! I'm on standby") leaked into the feed.

Full flow driven through the apps: withdraw, synthetic payments, re-scan, release, answer. Ended at zero balances with verify 33/33.

Read the official rules at this point. Best Blog is judged on "clarity, accuracy, and how much it helps a first-time Switch user",
so the blog has to teach setup, not only tell the story. Submission needs a repo, demo video and README. **Open question for the
owner:** judges ask whether someone can "clone the repo and reproduce it", and our repo is private.

### Fri 6 Nov, about 23:45 - README, privacy doc, blog draft, diagram, eval script (written)

Blog sections that depend on a real model are marked `[GAP]` rather than filled in. `scripts/eval_agent.py` is written and has **not run**.

(The status table that stood here was superseded by the entries below.)

### Sat 7 Nov, 00:00-00:02 - the owner logged in; Gate 1C and the model score (done)

**Gate 1C: 8 of 8, with the model really running.** Zero tools, zero tool calls, canary absent. The positive control gave the same model only
`Read`, run from Meera's folder, and it read the file in Arjun's folder. So the research plan's folder-and-tool-limits design would not have held.
The one first-run failure was naming: we asked for the alias `haiku` and the CLI reported the resolved model, an older one. We pinned `claude-haiku-5-5`.

**Model score:** 3 runs of 16 lines, 48 judgments: 35 correct decisions, 13 asked the person, 0 wrong. Small sample, on data we wrote and labelled.

The model asks about different lines on different runs, which `play_story.py` could not handle, as it only answered Zomato and Tito's. Scripted people now
answer whatever is asked, matched by amount.

### Sat 7 Nov, 00:03-00:08 - real model, end to end (done)

One rehearsal, then three in a row: each 33 of 33, about 46 s of story, balances identical and equal to the hand-worked numbers. Our first `rehearse.sh`
read `PIPESTATUS` after an `if` block, so it could not have detected a failed story. Fixed before the three-run streak. Log: `docs/evidence/rehearsals_real_model.log`.

Gate G4 in the research plan, three clean runs by 15:00 Saturday, passed at 00:08.

### Sat 7 Nov, 00:09-00:16 - Mattermost evidence, clean clone, field check (done)

Mattermost screenshots first captured its "open in desktop app?" page, three identical file sizes being the giveaway. Setup now turns that off, along with Switch's
auto-greetings, whose wording varies by run and had beaten our text filter. `verify.py` now also checks membership as Mattermost reports it (37 checks). That
output shows Mattermost's seeded `admin` and `user` accounts are in every channel, which is concrete evidence for "the host can read private rooms".

**Clean clone:** wiped the stack, cloned both repositories fresh, followed the README exactly. 68 tests, setup, real-model story, verify 37 of 37, in about 3.5 minutes. Docker
images and `uv` packages were already cached, so a new machine will take longer.

**Field check:** a code search over READMEs naming Switch found 39 projects (results are capped, and the earlier scan saw 93 hits). None does group settlement. Closest: `Kevrc/overruled`
(expense renewal review) and `ronkenx9/change-order` (two owners settle scope). This is limited by search, and does not prove nothing else exists. The first searches returned zero, and a positive
control showed those queries were simply too narrow.

### Sat 7 Nov, 00:17-00:24 - demo video tooling; recording stopped at the owner's request

`scripts/film.py` drives a real run and records it, drawing captions and zooms in the page, with a visible badge on sped-up stretches. Take one's review found a blank architecture card, captions
hidden behind title cards, run-on captions, zooms cropping the newest message, and no Mattermost at all. All were fixed in code. A second take was running when **the owner asked to hold the video
until the project has been improved further**, so it was stopped at 00:24. **The fixes are unverified, as no finished take has used them.** Take one is a local, untracked file with known defects, and is not a deliverable.
