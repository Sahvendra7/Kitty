# What goes where

The claim we make: **your friends never see your bank statement. Only the expenses you approve enter the group.**

We do not claim end-to-end encryption, zero knowledge or complete privacy. Service providers still process personal information, as listed below.

## Who can see what

| Information | You | Your agent's code | Model provider | Clerk | Friends | Whoever hosts Switch |
|---|---|---|---|---|---|---|
| Raw statement, with balance and account | Yes | Yes, read locally | **No** | **No** | **No** | **No** (it isn't in Switch) |
| Lines outside the trip dates | Yes | Parsed, then ignored | **No** | **No** | **No** | **No** |
| Spending lines in the trip: masked merchant text, date, time, amount | Yes | Yes | **Yes** | **No** | **No** | **No**, unless discussed in your private room |
| Payments between friends, and incoming money | Yes | Yes, in plain code only | **No** | Yes or no only, if you release it | Yes or no only, if you release it | Same |
| Your replies to your agent | Yes | Yes | **Yes**, when it needs the model to read them | **No** | **No** | **Yes** |
| Your private-room conversation, which names merchants | Yes | Yes | Partly, as above | **No** (403) | **No** (403) | **Yes** |
| Unreleased claims on your desk | Yes | Yes | No | Yes | **No** | **Yes** |
| Released claims | Yes | Yes | No | Yes | Yes | Yes |

"Whoever hosts Switch" includes anyone with admin access to its database or to the bundled Mattermost. `verify.py` shows this directly: Mattermost's seeded `admin` and `user` accounts are members of every channel, private ones included.

## What is sent to the model provider

**Adding a statement sends nothing.** The file goes from a person's browser to their own app on the same laptop, which checks it with plain code and saves it in their folder. Nothing reaches a model until they press "Go through my statement". Before that, the app shows the exact lines that would be sent, using the same function the agent uses, and a test holds the two together.

Exactly two prompts, both in [`kitty/llm_brain.py`](../kitty/llm_brain.py):

1. **Sorting.** The person's first name, the trip's name, and for each spending line in the trip: a row number, date, time, narration with long digit runs masked, and amount.
2. **Reading a reply.** Everyone's first names, the question (which includes a merchant name, time and amount), and the person's reply, up to 400 characters.

Every spending line inside the trip's dates goes in the first prompt, **including ones that turn out to be personal**, such as a pharmacy. That's how the model tells them apart. On a trip not in rupees, the amount field is named for the currency (`amount_usd`). The prompts are otherwise the same.

The provider in this build is Anthropic, through Claude Code, using `claude-haiku-5-5`. What it retains is set by the account's terms, and we have not independently checked it.

A model gets no tools, no MCP servers and no settings. The CLI's own report shows zero tools and zero servers (`scripts/gate1c_llm_sandbox.py`).

## What the organiser sees

Each person's name, the name on their payments, whether they've opened their invite, and whether they've added a statement, as yes or no. The upload screen says so. The organiser's page gets no passwords, keys, room ids, sender ids or error text from a server, and `six_person.py` checks that on a real run. They don't see anyone's statement, chits or private room.

## What's enforced, and by what

| Boundary | Enforced by | How we know |
|---|---|---|
| Agent stays out of the group room and others' rooms | Switch server (403) | `gate1_spike.py`, `gate1b_humans.py`, `verify.py` |
| Clerk stays out of private rooms | Switch server (403) | `gate1b_humans.py`, `verify.py` |
| Friends stay out of your channels | Mattermost (403) | `gate1b_humans.py` |
| A claim has no field for free text | `kitty/claims.py`, on the clerk's side | `tests/test_core.py`, `gate1d_release_live.py` |
| Only you can release | Clerk compares the sender id Switch sets | `tests/test_core.py`, `gate1d_release_live.py` |
| A model can't act | No tools, and the runner holds the key | CLI's report, plus a live attack with a positive control (`gate1c_llm_sandbox.py`, 8 of 8) |
| Only the invited person opens an app, on an app-made trip | Single-use link, then an HttpOnly cookie per trip and person | `tests/test_statement_upload.py`, `six_person.py` |
| Another website can't drive an app | Host and Origin checks, and a content type that needs a preflight | `tests/test_statement_upload.py`, `tests/test_provision.py` |
| A statement that fails its checks isn't saved, and its content isn't echoed | `kitty/statement_import.py` | `tests/test_statement_upload.py`, `six_person.py` |
| Statement words stay out of the group, desks and process logs | Typed claims and fixed messages. A failed model call logs a code only | `verify.py`, `six_person.py`, with a positive control |

## What is not enforced

- **The organiser's link, on an app-made trip.** They could open someone's invite before that person does. It's detected, because the link then says it's been used, but it's not prevented.
- **On this one-laptop demo**, every process runs as the same OS user, so one could read another's folder. A model process can't, as it has no tools, but this is not a security boundary between people. A real deployment would put each person's agent on their own machine.
- **An agent's code could post free text to its desk.** The clerk's connection would receive it. The clerk drops it, unstored and unrelayed, but it did reach the clerk process. What prevents this is that only `kitty/agent.py`'s deterministic code holds the key, and it builds every desk message from validated integers and closed lists.
- **The numbers themselves carry some information.** A released ₹12,400 "shopping" claim says something, even with no merchant name.
- **"No" is information.** Telling the clerk a payment didn't reach you discloses that fact, which is why you release that answer yourself.

## Retention

- Statements stay in `people/<person>/`, or `trips/<id>/people/<person>/` for a trip made in the app, git-ignored and mode 600. On an app-made trip, each agent also keeps its place in `agent_memory.json` beside the statement: row numbers, decisions, and the short merchant titles it already showed its owner.
- The clerk's log, `state/clerk-events-*.jsonl`, holds only messages that can change its state. Other chat on a desk is never written.
- Rooms are never deleted, because of a known Switch issue (#1202). A new trip is a new set of rooms, and the old ones stay in Switch's database and Mattermost until `just reset`.
- Credentials are in git-ignored `state/`, or `trips/<id>/state/`, mode 600. Those folders are mode 700 on app-made trips.
