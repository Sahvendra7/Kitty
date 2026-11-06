# Architecture decisions

Short records of the choices that matter. `BUILD_JOURNEY.md` has the chronological story.

## ADR-001: Run Switch from source at a pinned commit, not through the Console

**Decided:** 6 Nov 2026. **Status:** verified by running.

A Console-run local server allows one owner, and Kitty's premise needs separate owners. Running `switch-core` from source
at `fa933012b` let us create three non-admin owners through the Gateway. The commit is pinned because `main` moves
several times a day (92 commits in the day after this one). Prior art: `martinvibes/airlock` reported this route working.

**Trade-off:** someone reproducing this needs Docker, `uv` and `just`, and a from-source server is not the released product.

## ADR-002: The clerk is not in private rooms. Each person gets a private room and a desk

**Decided:** 6 Nov 2026, after Gate 1A. **Status:** verified by running. **Replaces** the original research plan.

**Problem:** the plan put the clerk in every private room. In Switch, every agent in a room reads all of it, and the spike
confirmed the clerk could `read_context` a private room it belonged to. Merchant names discussed there would reach the
clerk, which the product forbids.

**Options:** (a) keep one room and have the clerk ignore what it reads; (b) strip merchant names from the conversation;
(c) split into two rooms.

**Decision:** (c). `<person>-private` holds the person and their agent. `<person>-desk` holds the person, their agent and
the clerk. Switch enforces it: the clerk gets 403 on private rooms.

**Why:** (a) is a promise in code, where a server boundary is available. (b) would ruin the conversation.

**Trade-off:** seven channels for three people, and people must look in two places. The person app smooths this over.
**Limit:** an agent could still post free text to its desk. The clerk's connection would receive it, though the clerk drops
it unstored and unrelayed. The real protection is that only deterministic code holds the agent's Switch key.

## ADR-003: The model has no tools. It cannot post, read files, or reach Switch

**Decided:** 6 Nov 2026. **Status:** tools list verified from the CLI's own report. **Attack run pending** (Gate 1C).

**Problem:** prompts are not a security boundary. The research plan gave Claude Code a working folder with tool limits, and
that leaves a statement-reading model able to act.

**Decision:** the model runs with `--tools ""`, `--strict-mcp-config`, `--setting-sources ""`, from an empty temporary
directory, with its input in the prompt. A deterministic runner holds the Switch key. Model output is parsed as data and
validated against the real rows and closed lists.

**Trade-off:** this is less of an "agent" in the autonomous sense: the model proposes and code acts. That is the point.

## ADR-004: Authority comes only from server-set sender ids and the room

**Decided:** 6 Nov 2026. **Status:** verified live (Gate 1B, 1D).

A release counts only if the event's `sender`, set by Switch, equals that person's human id and it arrived on their desk.
Owner and payer are checked against the desk. Text cannot claim identity. Ids are learned at setup by observing a real post
from that person's own session, then compared, and never parsed, as the protocol doc instructs.

## ADR-005: Integer paise, with confirmed payments kept apart from obligations

**Decided:** 6 Nov 2026. **Status:** property-tested and worked by hand.

All money is integer paise with no floats, and a transfer changes a balance only after both people's human accounts release
it. This is what stops a failed payment counting: Meera's ₹1,500 was never in the ledger, so withdrawing it changes nothing.

## ADR-006: Reading money is deterministic. The model never sees payments between friends

**Decided:** 6 Nov 2026. **Status:** tested.

Reversal detection and "did this reach you?" run in `statements.py` with no model, matching amount, date, name and reference.
Those lines never enter a prompt. The first pass reads outgoing spending only, and incoming lines are opened only when a dispute
needs them. This minimises what reaches the provider and keeps the money facts out of a probabilistic system.

**Trade-off:** a production version should pre-check reversals on the payer's side, which would avoid some disputes. We left that
out because the receiver's side still has to be asked, and that two-sided flow is the mechanism being demonstrated.

## ADR-007: Every piece of information that crosses needs its owner's release, including "no"

**Decided:** 6 Nov 2026. **Status:** tested.

"Did Meera's ₹1,500 reach you?" is a fact about Arjun's statement. So Arjun's agent drafts the answer, and Arjun releases or
drops it. That adds a step to the demo, and we kept it, because the principle is the product.

## ADR-008: The clerk logs only messages that can change state

**Decided:** 6 Nov 2026. **Status:** tested live: free text on a desk produced no rejection and no log line.

The event log exists so the clerk can restart. A private note someone types on a desk has no reason to be on disk.
