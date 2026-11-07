# Six-person set-up: why the sixth room failed

Investigated on Sat 7 Nov 2026, 22:30 to 23:00 IST, on branch `fix/six-person-setup` (worktree `~/Desktop/Switch/Kitty-sixfix`), from `feat/kitty-mvp` at `fbc0d0b`. Switch wasn't restarted, no Mattermost account was created or deactivated, and nothing was deleted.

## Verdict

**Mitigated, not fixed.**
- We found *what* fails and *why Kitty gave up*. Kitty now reports the right cause and waits out the outage once.
- We did **not** find what makes Switch lose every connection to Mattermost at once. That is in Switch, Mattermost, or Docker Desktop's port forwarding, not in Kitty.
- The new retry has **not been exercised by a live set-up**. A full new six-person set-up needs 7 more accounts, and Mattermost is at 246 of 250.
- The six-person **workflow** now passes on the current code, **61 of 61**, on a trip that was already set up, with no new accounts. That run doesn't test the set-up path.

## 1. The exact failure

Four set-ups failed at the same point, all at `rooms:farhan`, the sixth person's first room:

| IST | Trip | Switch log |
|---|---|---|
| 20:16:55-56 | `goa-pot-31c5` | A 500 on `create_room` |
| 20:18:25 | `goa-pot-4f19` | `Exception in ASGI application`, raised by `add_users_to_channel` |
| 22:17:41 | `coorg-weekend-53c8` | `Exception in ASGI application` |
| 22:24:55 | `coorg-weekend-96fa` | `Could not list Mattermost channels for agent farhan-agent-efec`, then `Exception in ASGI application` |

The error underneath, every time:

```
requests.exceptions.ConnectionError: ('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))
```

Switch's `mattermost/http_client.py` calls `requests.request(...)` with no session. So every call opens a **new** TCP connection, and a stale pooled connection is ruled out. Switch turns this `ConnectionError` into a 500 and doesn't retry it.

## 2. Evidence

The snapshots are kept locally, git-ignored, in `logs/runs/diag-223851/`: Switch's log (`switch-core.snapshot.log`) and Mattermost's log (`mattermost.snapshot.log`).

1. **Every failure sits inside a burst of connection failures.**
   - Switch logs about 255 `Failed to establish websocket connection` warnings in one second, again 5 and 10 seconds later, then recovers.
   - Switch keeps one websocket per bot for every agent ever registered, about 255 of them, mostly from finished test trips. So every one of them dropped at once and failed to reconnect for 10-15 seconds.
   - Bursts on 7 Nov: 06:08, 06:12, 06:14, 06:16, 20:16:55, 20:18:25, 22:17:41 and 22:24:55. The first four are the morning failures in journal section E. Every set-up failure is in one, and `create_room` fails only when it lands in one.
   - The errors are a mix of `RemoteDisconnected` and `ConnectionResetError(54)`.
2. **Each burst starts within a second of the newest agent being registered.** At 22:24:54 Switch logged `Registered agent: farhan-agent-efec`, and the burst began at 22:24:55.
   - **This doesn't happen on every registration:** the five earlier agents in the same set-up registered without one, and so did all seven in the Pot trip that passed at 22:13.
   - So registration is the moment it shows up, **not a proven cause**.
3. **Mattermost logs nothing at the failing seconds.** It shows no request, no error and no panic, either in `/mattermost/logs/mattermost.log` or on the container's output. That container output has been empty since 6 Nov.
4. **The same operation with the same identities works later.** At 22:4x, Kitty's Try again on `coorg-weekend-96fa` ran `rooms:farhan` again, with Farhan's existing account and agent.
   - It succeeded, and Switch logged no error.
   - Switch's connections to Mattermost stayed at 509.
   - The failed attempt left nothing behind: a channel search finds only `farhan-private-efecr1`, not the failed `farhan-private-efec`. The failure came before the channel was made.
   - The 20:16 failure also succeeded on Try again about 20 seconds later.

## 3. Hypotheses tested

| Hypothesis | Test | Result |
|---|---|---|
| A cap of about 512 connections to `localhost:8065` | Held 40 more idle connections open (550 in total) and checked a fresh ping after each one | **Refuted.** Every ping returned 200 |
| A stale keep-alive connection inside Switch | Read `http_client.py` | **Refuted.** No pooling: a new connection per request |
| Host ports, TIME_WAIT or file descriptors | Read `netstat`, `sysctl` and the descriptor counts | **Refuted.** 3 in TIME_WAIT, and the Switch process at 1,433 descriptors, far under its limit |
| Too many websocket connections | After 21 test agents were deleted, the count went from 501 to 243, and the 22:24 set-up still failed | **Not the cause by count alone** |
| Kitty's Mattermost config save | Timed the `config/patch` calls | **Refuted as the trigger.** It runs 49 seconds before the failure, once per trip |
| Docker Desktop's routine API polling | Read `com.docker.backend.log` and `monitor.log` | **Refuted.** It runs every 10 seconds all day, and nothing at the failure moments mentions port forwarding |
| The Postgres connection limit | Read Postgres `max_connections` and Mattermost `SqlSettings.MaxOpenConns`; counted `too many clients` in Mattermost's log | **A real misconfiguration, not shown to cause this.** See below |
| A broken or partly made room | Searched channels, and re-ran the step on the same identities | **Refuted.** Nothing was left behind, and the re-run worked |
| Duplicate set-up requests | `Home.begin` refuses a second press while one runs, now tested | **Refuted** |
| A test-harness fault | The keep-alive bug in the harness was fixed at 20:16. The failures since are in Switch's log | **Refuted** for these four |

**A separate infrastructure problem found on the way:**
- **What:** Mattermost may open up to 100 database connections (`SqlSettings.MaxOpenConns`), and the shared Postgres allows **100 in total**. Switch also uses Postgres, with 34 connections idle at 22:40.
- **Evidence:** Mattermost logged `pq: sorry, too many clients already` 41 times on 7 Nov, including about 15 seconds after two of the four failures (14:48:40 and 16:55:10 UTC).
- **Status:** it isn't shown to drop connections. Fixing it means lowering Mattermost's `MaxOpenConns`, or raising Postgres `max_connections`, and both need a restart. **Not done, because restarts weren't authorised.**

## 4. Root cause, as far as it's known

- **Immediate cause, shown:** Switch's `create_room` calls Mattermost while every Switch-to-Mattermost connection has just dropped. During those 10-15 seconds new connections are reset or closed with no reply. Switch turns that into a 500 and doesn't retry. The sixth person's room is simply the call that happens to run at that moment.
- **Kitty's part, shown and fixed:**
  1. Kitty labelled a Switch 500 from an agent operation as `unexpected` (`Agent.result` raised a bare `RuntimeError`). That hid the cause.
  2. Kitty stopped the whole set-up at the first such error, though every step is safe to run again.
- **Underlying trigger, not found:** what drops every connection at once. It comes right after a new agent's bot is set up, but not after most of them. Mattermost logs nothing, so the drop happens before Mattermost handles the request, or without Mattermost logging it.
  - **Candidates left:** Docker Desktop's port forwarding, Mattermost's websocket hub, or Switch's bridge reconnecting every bot at once.
  - **Evidence for the last:** all ~255 bots reconnect in lockstep every 5 seconds, so they collide with one another as well as with new requests.
- **Why six people and not three:** not established. A six-person set-up registers twice as many agents and makes twice as many rooms, which means more exposure. Three-person set-ups passed tonight, but that's a sample of one.

## 5. The fix in Kitty

The fix is in commit `d99a4c3`.

- **Typed error.** `switch_client.Agent.result` raises `SwitchOpError(operation, status)`. It keeps the status code, never the reply text.
- **Classification.** `provision.classify` maps a 5xx to `service_error` ("Switch hit a problem on its side") and a 4xx to `service_refused`. Before, both were `unexpected`.
- **One retry.** A step that fails with `service_error` is retried **once**, after **25 seconds**, which is longer than the 10-15 seconds the bursts lasted. The retry is logged and recorded in the status under `retried`.
  - Every step was already built to run again safely. A room retry takes a fresh name (`…r1`), so a half-made room can't collide.
  - Refusals, unknown failures, unreachable services, injected test faults and a second failure all stop as before, and Try again carries on from there.
- **Why this isn't a blind retry:** it's limited to Switch's own 5xx, it happens once, it waits out the window we saw, it's visible, and the step is idempotent.
- **What it can't do:** if an outage lasts longer than 25 seconds, set-up still stops, now with the right reason.

**Also changed** (commits `e401ba6` and `eb9f7ce`):
- **`six_person.py --existing TRIP_ID`** plays the story on a trip it has already set up, after checking that the trip is this story's and unused. No accounts are made.
- **`rehearse.sh`** moves the demo's old logs into `logs/runs/rehearse-<time>/`. It used to `rm -rf logs`, which removed the running home app's log at 22:18 and lost the 22:24 failure's HTTP status.

## 6. Regression tests

`tests/test_six_person_setup.py` has 18 tests. The suite is now **754 passed**, up from 736, and nothing existing was weakened. The tests cover:

- a Switch 500, 502 or 503 is classified as `service_error`, and a 403 or 409 as `service_refused`. An unknown error stays `unexpected`;
- the typed error carries no reply text, and `Agent.result` raises it;
- the sixth room failing once: one retry with a fresh name, and set-up completes;
- the retry makes no account, agent or other room again;
- a failure that lasts past the retry: the real cause is reported, and Try again completes without remaking anything;
- what is never retried: unknown failures, refusals, and connection interruptions (which stop everything);
- at most one retry per step per run, and the 25-second wait is applied;
- the status shows the retry, and no error text;
- a second press while set-up runs starts nothing.

**Control:** with the retry turned off, 5 of the 18 fail.

## 7. Live validation

| What | Result |
|---|---|
| The one failed operation, `rooms:farhan` on `coorg-weekend-96fa`, re-run with existing identities through Try again | **Succeeded.** No Switch error, no new accounts, no orphan channel. This ran on the main checkout's code, before this fix |
| The whole six-person shared-expense story on that trip, real model, `six_person.py --existing coorg-weekend-96fa`, 22:48:06-22:48:52 | **`verify` 61 of 61**, and the script's own checks 102 passed, 0 failed |
| — balances | Equal to the hand-worked numbers: Asha +₹9,766.66, Bilal +₹6,266.66, Chitra −₹683.33, Dev −₹4,833.33, Esha −₹2,083.33, Farhan −₹8,433.33 |
| — resends and duplicates | 0 messages resent. The two-person dinner conflict was raised once, cleared once, and counted once |
| — privacy and boundaries | Every one of `verify`'s isolation and leak checks passed |
| — accounts | No new accounts. Mattermost is still at 246 of 250 |
| A fresh six-person set-up using the new retry | **Not run.** It needs 7 new accounts, and only 4 are free |

**What the 61/61 shows:** the six-person workflow works on the current code, once set-up is done. **It doesn't show that set-up is now reliable.**

## 8. What's left

1. **The trigger is unknown.** Recommended next steps, each needing the owner's go-ahead:
   - **(a)** Restart Switch to see whether ~255 sockets come back one by one, then watch a registration.
   - **(b)** In Switch, retry `requests.ConnectionError` with backoff, and return 503, not 500.
   - **(c)** Add jitter to the bots' reconnects, so they don't all retry in the same second.
   - **(d)** Use a pooled `requests.Session`.
   - **(e)** Retire the bots of agents from finished trips. Switch's websocket count grows with every test trip.
   - **(f)** Fix the Postgres limit: Mattermost's `MaxOpenConns` 100 against `max_connections` 100, shared.
2. **The retry is unproven live.** Prove it with one fresh six-person set-up once accounts are freed. Expect either no burst, or a `retried` entry in the status followed by `live`.
3. **An outage longer than 25 seconds** still stops set-up. It now names the right cause, and Try again works.
4. **The Mattermost account limit** (246 of 250) blocks any further six-person set-up until test accounts are deactivated.
