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

"Whoever hosts Switch" includes anyone with admin access to its database or to the bundled Mattermost.

## What is sent to the model provider

Exactly two prompts, both in [`kitty/llm_brain.py`](../kitty/llm_brain.py):

1. **Sorting.** The person's first name, the trip's name, and for each spending line in the trip: a row number, date, time, narration with long digit runs masked, and amount.
2. **Reading a reply.** Everyone's first names, the question (which includes a merchant name, time and amount), and the person's reply, up to 400 characters.

The provider in this build is Anthropic, through Claude Code. What it retains is set by the account's terms, and we have not independently checked it.

A model gets no tools, no MCP servers and no settings. The CLI's own report shows zero tools and zero servers (`scripts/gate1c_llm_sandbox.py`).

## What's enforced, and by what

| Boundary | Enforced by | How we know |
|---|---|---|
| Agent stays out of the group room and others' rooms | Switch server (403) | `gate1_spike.py`, `gate1b_humans.py`, `verify.py` |
| Clerk stays out of private rooms | Switch server (403) | `gate1b_humans.py`, `verify.py` |
| Friends stay out of your channels | Mattermost (403) | `gate1b_humans.py` |
| A claim has no field for free text | `kitty/claims.py`, on the clerk's side | `tests/test_core.py`, `gate1d_release_live.py` |
| Only you can release | Clerk compares the sender id Switch sets | `tests/test_core.py`, `gate1d_release_live.py` |
| A model can't act | No tools, and the runner holds the key | CLI's report. **Live attack not yet run** |

## What is not enforced

- **On this one-laptop demo**, every process runs as the same OS user, so one could read another's folder. A model process can't, as it has no tools, but this is not a security boundary between people. A real deployment would put each person's agent on their own machine.
- **An agent's code could post free text to its desk.** The clerk's connection would receive it. The clerk drops it, unstored and unrelayed, but it did reach the clerk process. What prevents this is that only `kitty/agent.py`'s deterministic code holds the key, and it builds every desk message from validated integers and closed lists.
- **The numbers themselves carry some information.** A released ₹12,400 "shopping" claim says something, even with no merchant name.
- **"No" is information.** Telling the clerk a payment didn't reach you discloses that fact, which is why you release that answer yourself.

## Retention

- Statements stay in `people/<person>/`, git-ignored.
- The clerk's log, `state/clerk-events-*.jsonl`, holds only messages that can change its state. Other chat on a desk is never written.
- Rooms are never deleted, because of a known Switch issue (#1202). A new trip is a new set of rooms, and the old ones stay in Switch's database and Mattermost until `just reset`.
- Credentials are in git-ignored `state/`, mode 600.
