# Nobody Entered an Expense: Building Private Agents for Group Settlement on Switch

> **Draft status.** Everything below happened and has evidence in the repo, except the items marked `[GAP]`, which must be filled from real runs before publishing. Best Blog is judged on "clarity, accuracy, and how much it helps a first-time Switch user", so sections 3 to 6 are a working walkthrough.

Meera says she paid Arjun ₹1,500. Arjun's bank shows nothing.

This is usually where a group trip's settling-up turns awkward. Somebody has to show a screenshot of their bank app, with their balance and everything else that month on it. Or someone quietly lets it go.

In the system we built, Meera's own agent goes back through her statement in a room only she can see, and tells her: the money left at 3:02 PM and came back at 3:09 PM, with the same reference. The payment failed. She withdraws the claim, and the group sees one line: "The payment failed and never arrived." Neither statement left anyone's side.

It matters more than it sounds. In our test trip, Arjun ends up owed ₹391.67. If that failed payment had counted, he'd have owed ₹1,108.33. A bank glitch would have flipped who owes whom.

This post covers how we built it on [Switch](https://github.com/sandbox-quantum/switch), what you need to do to run several separately owned agents on one machine, and the things that went wrong. Most of what we learned came from the failures.

## 1. The problem

Group trips are fun, and settling up isn't. Splitwise and its cousins work if somebody types every expense as it happens, and on most trips nobody does. Bank-import features exist, but they move one person's data into a shared app, and nobody on the trip can tell whether two people's stories agree.

Two honest caveats. The one number we found, that [45% of those who front money spend over a day settling up](https://mustsharenews.com/singaporeans-fight-group-trip-expenses/), comes from a survey by a company selling a split feature. And we did no user research. We built this because the mechanism interested us, and demand is unproven.

## 2. Why one shared chatbot can't do this

The obvious design is one assistant that reads everyone's statements and works out who owes what. But then one system holds everybody's bank data, and a persuasive prompt is all that stands between Priya's secret birthday gift for Arjun and Arjun.

We wanted three different things, with three different kinds of trust:

- **A personal agent per person.** It reads one statement and works for one person. It asks "Zomato, 11 PM, ₹850. Was this shared?" because only Priya knows that Meera had gone to bed.
- **A clerk that isn't an LLM.** Money arithmetic and "who approved this" shouldn't be probabilistic. It's about 350 lines of ordinary Python with integer paise.
- **A group room** that holds only what people have approved.

What makes this work is that the walls between them aren't instructions. They're enforced by a server. That's what Switch gives you, so that's the first thing we tested.

## 3. Running Switch with more than one owner

The first thing to know: a local server run by Switch Console has one owner, and all its agents belong to you. That's fine for most projects. For separately owned agents, run from source. [Airlock](https://github.com/martinvibes/airlock) reported this route working on a Mac, and we followed it.

```bash
git clone https://github.com/sandbox-quantum/switch.git switch-src
cd switch-src
git checkout fa933012b   # the commit we tested; main moves several times a day
just init-env
just up        # Postgres and Mattermost in Docker
just migrate   # easy to forget; `just run` won't do this for you
just run       # switch-core on :8000
```

Wait until `just up`'s setup container finishes. It registers Mattermost as your default bridge, and a room created before that won't have a channel.

Create real owners through the Gateway, logged in as the admin that `just init-env` printed. **Use `role: user`, not admin.** An admin bypasses the checks you're trying to test, and most things will appear to work.

```python
admin.post("/gateway/users", json={"email": email, "name": name, "password": pw, "role": "user"})
# Then log in as that user, and mint their own registration key:
owner.post("/gateway/api-keys", json={"label": "kitty"})  # returns {"key": ...}
```

Each owner registers their agents with their own key, so the agent inherits that owner's permissions:

```python
httpx.post(f"{BASE}/agents", headers={"Authorization": f"Bearer {reg_key}"}, json={
    "name": "meera-agent", "description": "Meera's private agent", "connector_type": "external",
    "integration_profile": {"connection_model": "always_on", "message_exchange": True,
        "pre_invocation_mediation": [], "post_invocation_mediation": [], "event_reporting": []},
})  # returns {"id": ..., "api_key": ...}, and the key is shown only once
```

## 4. Make your rooms actually private

This one matters if you take nothing else from this post: **`create_room` defaults to public visibility.** We pass private explicitly:

```python
agent.op("create_room", name="meera-private", description="...", agent_names=["meera-agent"],
         user_names=["meera"], bridge_id=bridge, channel_type="channel_private",
         read_visibility="private", write_visibility="private")
```

`user_names` takes the person's Mattermost username, not their Switch name.

Then test the boundary, because a room appearing in a UI doesn't prove anything. We wrote `scripts/gate1_spike.py` to try every door we could find:

| Attempt, as Meera's agent | Server's answer |
|---|---|
| Read the group room | 403 `Agent is not a member of this room` |
| Post to Arjun's private room | 403 |
| Invite herself into the group room | 403 `Not authorized to change this room's membership` |
| Open a connection on the group room | refused, status 403 |

## 5. Connecting a custom client

There's no client library, but the protocol is small.

**The transport surprised us.** The protocol doc in the repo at our commit describes a server-sent event stream, and so does the hackathon page. Our first listener sent a plain GET and got a **200**, on a room the agent wasn't in. We thought we'd found a leak. We hadn't: without `Accept: text/event-stream`, that endpoint falls back to a long poll of the agent's own events. The live connection is a WebSocket:

```python
url = f"ws://localhost:8000/agents/{agent_id}/connection/ws?connection_id={cid}&scope=all&filter=all&start_from=head"
with connect(url, additional_headers={"Authorization": f"Bearer {api_key}"}) as ws:
    while True:
        frame = json.loads(ws.recv())
        if frame["event"] == "ping":
            ws.send(json.dumps({"type": "pong", "cursor": cursor}))   # this is your heartbeat
```

Keep the `connection_id` across reconnects. Posting is one call, with no connection needed: `POST /agents/{id}/message` with `{room_id, content}`.

Two more things that cost us time:

- **Agents don't get an echo of their own posts.** Check with `read_context`.
- **Treat sender ids as opaque.** We asserted the word "user" appears in a person's id, and it doesn't: Mattermost people have ids like `@switch-mattermost-<bridge>-<name>:localhost`. Learn the id by watching a real post from that person, then compare ids.

To link a Mattermost account to its owner, post to `/gateway/collaborations/{bridge}/identities`, then confirm with that account's own session token at `/identities/{id}/verify-session`. Until then, Switch can't tell who that person is.

## 6. The bug that looked like flakiness

Our third setup run failed: a person's message never arrived. It had worked twice, so a retry looked like the obvious fix. The server log told a different story, and its first error was:

```
Unable to create the new team membership because the team has reached the limit of members
```

Mattermost allows 50 members per team by default, and every test run added eight accounts. The bridge carries on past that error, so bots then can't join channels, and messages just don't arrive. If you run a template repeatedly, raise it:

```python
mm_admin.put("/api/v4/config/patch", json={"TeamSettings": {"MaxUsersPerTeam": 2000}})
```

## 7. Our design was wrong, and a test showed us

The plan put the clerk in every private room, so it could hear claims. Then the spike confirmed what the docs say: **every agent in a room can read all of it.** The clerk would have received every "Zomato, 11 PM" message, in a system whose whole promise is that it doesn't.

We could have had the clerk ignore what it read. But that's a promise made in code, when a server boundary was available. So each person got two rooms:

- `meera-private`: Meera and her agent. **The clerk gets a 403.**
- `meera-desk`: Meera, her agent and the clerk. Typed claims only.

It's more channels, but "the clerk can't read it" is now something the server says, not something we say.

## 8. Claims with nowhere to hide a merchant name

A claim is a strict schema. Unknown fields are rejected, every string is a closed set or a narrow pattern, and rejections carry a fixed code so the clerk can never echo private text into a room.

```json
{"v":1,"type":"expense","ref":"p2","trip_id":"goa-2026","owner_id":"priya","payer_id":"priya",
 "date":"2026-10-10","amount_paise":85000,"currency":"INR","category":"food",
 "participant_ids":["priya","arjun"],"shares":{"priya":42500,"arjun":42500}}
```

A release counts only if the `sender` that Switch set on the event is that person's own account, on their own desk. We attacked the live clerk: Meera's agent typing `release c9` got "Refused: Meera's agent can't do that. Only Meera can." Arjun typing it, from two different rooms, got nowhere either.

The model's position is the most restrictive of all. It runs with no tools and no MCP servers, from an empty directory:

```bash
claude -p "$PROMPT" --model claude-haiku-5-5 --system-prompt "$SYSTEM" --tools "" \
  --strict-mcp-config --setting-sources "" --no-session-persistence
```

It can't post, read a file or reach Switch. Plain code holds the key, checks the model's output against the real statement lines, and builds every message itself. Even when we made a test model obey a prompt injection and return a fake line with `<@all> SECRET MERCHANT` as its title, nothing reached the desk.

## 9. The ₹1,500

Here's the flow, each step in a different place:

1. Meera's agent sees a debit to Arjun and files a transfer claim on her desk. She releases it.
2. The group sees: "Meera says they paid Arjun ₹1,500. Not counted until Arjun confirms."
3. The clerk asks Arjun's agent, on Arjun's desk. Arjun's agent checks his statement in plain code and finds nothing.
4. That "no" is a fact about Arjun's statement, so **Arjun releases it himself**. We kept this extra step because it's the principle.
5. The group sees: "NOT CONFIRMED."
6. The clerk asks Meera's agent to re-check. It finds a credit with the same amount and reference, and tells her privately.
7. She withdraws it. The group sees: "The payment failed and never arrived."

The payment was never in the ledger, so nothing needs undoing. A transfer changes a balance only once both people have released it. Being balanced on paper and being actually paid are different numbers, and we keep them apart.

Finding the reversal is deterministic. A model never sees payments between friends.

## 10. What our privacy tests prove, and what they don't

Twice we wrote a test that passed for the wrong reason. Both are worth stealing the lesson from.

**A model "resisted" an attack because it never ran.** We told a model with no tools to read a friend's file. It made no tool calls and leaked nothing. All green. Then the positive control, the same test with `Read` enabled, should have found the secret, and it didn't. The model had replied "Not logged in". The script now fails unless the model really ran.

**A verifier found no leaks because it read no messages.** Ours parsed `messages`, where the API returns `root` and `replies`. It scanned nothing and reported no leaks. Now it fails if there's nothing to scan. To prove it can fail, we planted `TANISHQ GOA JEWELLERS` in the group room and required it to fail, and it did.

So: every negative test needs a positive control.

**What's proven, live, against Switch:** agents and the clerk are refused rooms they aren't in; only an owner's account can release; 87 merchant words, references and account numbers from the statements never appear in the group room or on the desks; balances recomputed from the room's own text net to zero.

**What isn't:**

- Statements still reach the model provider, after plain code strips balances, account numbers, references, out-of-trip lines and payments between friends. Merchant names go.
- Whoever hosts Switch can read private rooms. This is fine among friends, and not for parties who don't trust each other.
- It runs on one laptop, under one OS user. Switch's boundaries are real, but the separation between folders here isn't a security boundary.
- There's no end-to-end encryption, and we don't claim any.

**With the model really running,** the attack test passes 8 of 8: zero tools, zero tool calls, secret not leaked. The positive control is the striking part. Given only `Read`, run from Meera's folder, the same model read the file in Arjun's folder. A working directory and a tool list wouldn't have protected her. Having no tools does.

**How good is the model at this?** Across 3 runs of 16 lines, it made 48 judgments. The first scoring gave 35 correct decisions, 13 where it asked the person, and none wrong. A later scoring gave 39, 9 and none. It asks about different lines on different runs, so our scripted test people had to answer whatever came up. That's a small sample on data we wrote and labelled ourselves, so it says the demo works and not much more.

## 11. From a script to something six people can use

The first version needed a command to create a trip, and worked with three people. Making it a product found more than we expected.

**Provisioning has to survive failing halfway.** Each person is a Switch owner, a Mattermost account linked by that account's own session, a registered agent, two rooms, and sender ids learned by watching real posts. Any of those can fail, and Switch can't delete a room (issue #1202), so rollback isn't available. What worked: write each password down before the call that uses it, save after every step, and on retry skip what's done. One person's failure doesn't stop the others, and the group room waits. If you build on Switch, plan for resuming rather than undoing.

**Don't show a server's error text to a user.** It could hold anything. Our status file has fixed codes and sentences only, and a test plants a password in a failure and checks that it never comes out.

**Validate a statement without quoting it.** A CSV error normally says what was wrong with what. Ours says "Line 3, txn_time: use a 24-hour time like 20:15", and never repeats a value, so errors can be logged and shown safely.

**Show people exactly what the model provider gets.** Before anything is sent, a person can open the exact lines. It calls the same function the agent uses, and a test compares the two against what a recording model really receives. It also shows something uncomfortable: a personal pharmacy purchase in the trip's dates is in there, because that's how the model tells it apart.

**Six people found things three didn't.**

- Two people's statements showed the same dinner. The clerk held both until one dropped theirs, and our verifier had no idea how to read that. It failed 58 of 60 with the board correct, and it was right to complain. It was missing a rule, so we taught it the rule and didn't relax the check.
- The model filed a rafting trip for all six. It can't know Dev skipped it. The owner reads the chit, drops it, and says so, which is the design. A wrong "shared" could already be fixed that way, but a wrong "personal" had no way back, so we added one.
- One message in about 700 reached Mattermost and never reached Switch, and we still don't know why. A theory we liked died when we measured it: 648 more messages, none lost. So the app now shows "Sending", and says when it's safe to press again, rather than pretending this can't happen.

**The results:** three real-model runs in a row with six people, each with verify at 61 of 61 and balances matching numbers we worked out by hand first. A run with every process stopped mid-trip and a release sent while they were down also finished clean. The original three-person story still passes 37 of 37. It's a small test, on statements we wrote ourselves.

## 12. What we learned

- **Test the boundary before building on it.** The test changed our architecture on day one.
- **Put limits in the server, then in code, and last in prompts.** We ended up with no prompt-based security at all.
- **Let the model propose and let code decide.** The model is good at "`ZOMATO*ORD 8812` might be shared, better ask". It has no business doing arithmetic or holding a key.
- **Asking a person is a feature.** "Yes, but Meera had gone to bed" is the best line in the demo, and no statement contains it.
- **Read the server log first.** A retry would have papered over a 50-member limit.

## 13. Try it

`[GAP: repository link. The repository is private at the time of drafting]`
`[GAP: demo video link]`

The no-model tests run in about two seconds with `uv sync && uv run pytest`. The full setup is in the README. Once Switch is running, `uv run python -m kitty.home_app` opens the app at <http://127.0.0.1:7000>.

**What's missing, plainly:** real bank connections, converting between currencies, real accounts instead of invite links, an organiser login, adding someone after the trip opens, changing a split after release, running on separate machines, and any evidence that people want this. A real version could read statements on each person's own device, so the host never holds them, and pre-check reversals before anyone has to claim anything.

Thanks to the Switch team for a server whose boundaries held up when we attacked them, and to the author of Airlock for proving the path.

---

## Assets checklist

| Asset | Status |
|---|---|
| Architecture diagram | Done: `docs/assets/architecture.png` |
| Stage during the dispute | Done, but from the no-model run. **Retake from a real-model run** |
| Meera's private reversal message | Done: `docs/assets/meera-private-reversal.png` |
| Board at "Everyone's square" | Done: `docs/assets/board-square.png` |
| Mattermost channel view, to show this is real team chat | Done: `docs/assets/mattermost-group.png`, `mattermost-meera-desk.png` |
| Verify output | Done: `docs/evidence/verify_output_real_model.json` |
| Demo video | **Not recorded. On hold until the owner says so** |
| App screenshots, from a real-model six-person run | Done: `docs/assets/app/` |
| Older three-person screenshots | The Stage and board shots still need retaking from a real-model run |
