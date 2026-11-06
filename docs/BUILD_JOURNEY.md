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

### Fri 6 Nov, 23:20-23:45 - Gate 1A: isolation between separately owned agents (done)

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

### Fri 6 Nov, 23:45 - design change found by testing: the clerk must leave private rooms

The research plan put `kitty-clerk` in every private room. Switch's documented behaviour is that all
agents in a room read all of it, and the spike confirmed the clerk can `read_context` a private room it
belongs to. So the clerk would have received merchant names from the "Zomato, 11 PM" conversation,
which breaks the requirement that it never does. See ADR-002 in `docs/DECISIONS.md`.

### Sat 7 Nov, about 00:00 - Gate 1B and 1C

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
