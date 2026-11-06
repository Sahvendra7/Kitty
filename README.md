# Kitty

**Back from a group trip. Nobody typed an expense. Nobody saw anyone's bank statement. Everyone's square.**

Built for the [Flip the Switch hackathon](https://www.wemakedevs.org/hackathons/switch), Best Personal or Creative Workflow.

![Priya, Arjun and Meera's apps beside the trip board, during the dispute](docs/assets/stage-dispute.png)

Each friend has their own private agent. It reads that person's statement on their side and asks them about unclear lines. Only a typed claim, released by its owner, reaches the group. A clerk made of ordinary code does the sums.

**The moment it's built around:** Meera says she paid Arjun ₹1,500. Arjun's side shows nothing. Meera's agent re-checks privately and finds the payment failed and was reversed. The dispute ends, and neither statement left anyone's side. Had it counted, Arjun would have gone from being owed ₹391.67 to owing ₹1,108.33.

All data here is synthetic. Kitty never moves money and gives no financial advice.

## How Kitty uses Switch

Take Switch away and this stops working: its server is what keeps the agents apart.

| Switch feature | What it does in Kitty | Proof |
|---|---|---|
| **Separately owned agents** | Priya, Arjun, Meera and a neutral host are four non-admin owners, each registering agents with their own key | `gate1_spike.py` |
| **Room membership, enforced by the server** | A personal agent gets **403** on the group room and on anyone else's rooms. The clerk gets **403** on private rooms | `gate1_spike.py`, `gate1b_humans.py`, `verify.py` |
| **Sender id set by the server** | A release counts only if Switch says that person's own account sent it. An agent typing `release c1` is refused | `gate1d_release_live.py` |
| **Mattermost bridge** | Real people in a real team chat, in private channels, each account linked to its owner by its own session | `gate1b_humans.py` |
| **Agent protocol** | The clerk and the agents are custom clients on Switch's WebSocket and operations API | `kitty/switch_client.py` |
| **The room as the record** | `verify.py` re-derives the balances from the group room's own text | `verify.py` |

Rooms for three people:

| Room | People | Agents | What's said there |
|---|---|---|---|
| `meera-private` | Meera | her agent | "Zomato, 11 PM, ₹850. Was this shared?" |
| `meera-desk` | Meera | her agent, the clerk | Typed claims, waiting for her release |
| `goa-trip` | everyone | **the clerk only** | Released claims and balances |

![Architecture](docs/assets/architecture.png)

## Who does what

- **People** answer what only they know ("yes, but Meera had gone to bed"), release claims, and pay each other.
- **Personal agents** ([`kitty/agent.py`](kitty/agent.py)) sort statement lines and ask questions. The model behind each one has **no tools**: it can't post, read a file or reach Switch. It suggests, and plain code decides.
- **The clerk** ([`kitty/clerk.py`](kitty/clerk.py)) has no LLM. It validates claims, holds them until their owner releases them, matches transfers, and does the sums in integer paise.
- **`verify`** ([`scripts/verify.py`](scripts/verify.py)) proves the result without trusting the clerk's totals.

## Run it

**Tests, in under a minute, with no Switch and no model:**

```bash
uv sync
PYTHONPATH=. uv run pytest
```

**The full demo.** You need Docker, [uv](https://docs.astral.sh/uv/) and [just](https://github.com/casey/just). Switch runs from source, beside this repository, at the commit we tested:

```bash
git clone https://github.com/sandbox-quantum/switch.git ../switch-src
git -C ../switch-src checkout fa933012b
cd ../switch-src
just init-env
just up
just migrate
just run
```

`just run` stays in the foreground. In a second terminal, from this repository:

```bash
uv run python scripts/setup_trip.py
uv run python scripts/run_all.py
```

Then open the demo stage at <http://127.0.0.1:7100/stage>. Add `KITTY_BRAIN=rules` before `run_all.py` to run with no model at all. For the real model, install Claude Code and run `claude auth login` first.

In a third terminal, you can play the people's side automatically and then check the outcome:

```bash
uv run python scripts/play_story.py
uv run python scripts/verify.py
```

Mattermost is at <http://localhost:8065>. Each person's login is in `state/<person>.json`.

| Tool | What it does |
|---|---|
| `scripts/setup_trip.py` | Creates users, agents and rooms. Run it again for a fresh trip |
| `scripts/run_all.py` | Runs the clerk, each agent and each person's app as separate processes |
| `scripts/play_story.py` | Scripted people, posting through their own Mattermost sessions |
| `scripts/demo_pay.py` | Adds one synthetic payment to two synthetic statements |
| `scripts/verify.py` | Checks the outcome |
| `scripts/eval_agent.py` | Scores the model against a hand-labelled answer key |

## Results

These are real runs, and the output is in [`docs/evidence/`](docs/evidence/).

| Check | Result |
|---|---|
| Unit and scenario tests, no Switch, no model | 68 of 68 |
| Isolation between owners (`gate1_spike.py`) | 28 of 28 |
| Real people in Mattermost (`gate1b_humans.py`) | 26 of 26 |
| Attacks on the live clerk, and a planted leak that verify must catch (`gate1d_release_live.py`) | 10 of 10 |
| Live end-to-end outcome (`verify.py`), no-model brain | 33 of 33 |
| Model with no tools, under attack (`gate1c_llm_sandbox.py`) | **Not yet run: see below** |
| Model accuracy against the answer key (`eval_agent.py`) | **Not yet run: see below** |

`verify` checks that balances recomputed from room text net to zero; every visible claim traces to a release by its owner's own account; 87 merchant words, refs and account numbers from the statements are absent from the group room and desks; the clerk is the only agent in the group room; the refusals are live 403s; a withdrawn payment isn't counted; and each confirmed payment was released by both people.

## What's real and what isn't

| Real | Synthetic or simplified |
|---|---|
| Switch server, room membership, sender ids | The three bank statements |
| Mattermost accounts and channels | The payments, which `demo_pay.py` adds to the statements |
| The clerk's rules and arithmetic | In `play_story.py`, the people are scripted, though they post through real accounts |

## Limits

Read these before believing the tagline.

- **Statements still reach the model provider.** Plain code first strips balances, account numbers, references, lines outside the trip dates, incoming money and payments between friends. Merchant names and amounts of spending inside the trip are still sent. See [`docs/PRIVACY.md`](docs/PRIVACY.md).
- **Whoever hosts Switch can read private rooms.** This suits friends who trust the host, and not parties who don't. There is no end-to-end encryption.
- **It runs on one laptop.** The processes are separate and hold separate credentials, but one OS user could read all of `state/`. Switch enforces room boundaries. The operating system doesn't enforce boundaries between these folders here.
- **No real banks.** CSV only, in one made-up format.
- **Trip setup is a command**, not a screen. People and dates come from `data/synthetic/trip.json`.
- **Three people in the demo.** The code takes any number, but only three have been run live.
- **A from-source Switch at a pinned commit**, which isn't the released product.
- **No user research.** Demand is unproven. The one number we found is a survey run by a company selling a split feature: [YouTrip, n=1,000](https://mustsharenews.com/singaporeans-fight-group-trip-expenses/), where 45% of those who front money say they spend over a day settling up.

## Related work

- [`martinvibes/airlock`](https://github.com/martinvibes/airlock), which showed that real multiple owners on one Mac work from source, and which informed our route. We read it to learn, and wrote our own code. Kitty is a different job: many-party settlement and two-sided confirmation.
- Splitwise, Tricount and Splid need someone to enter expenses. Splitwise Pro's and SplitterUp's imports bring one person's data into a shared app. Kitty keeps each person's data on their side and checks claims against each other.
- [Switch](https://github.com/sandbox-quantum/switch) is source-available under Apache 2.0 with the Commons Clause.

## More

- [`docs/BUILD_JOURNEY.md`](docs/BUILD_JOURNEY.md): what happened, in order, including what failed
- [`docs/DECISIONS.md`](docs/DECISIONS.md): the architecture decisions
- [`docs/PRIVACY.md`](docs/PRIVACY.md): exactly what goes where
