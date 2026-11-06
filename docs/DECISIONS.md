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

**Decided:** 6 Nov 2026. **Status:** verified live, 8 of 8 (Gate 1C).

**Problem:** prompts are not a security boundary. The research plan gave Claude Code a working folder with tool limits, and
that leaves a statement-reading model able to act.

**Decision:** the model runs with `--tools ""`, `--strict-mcp-config`, `--setting-sources ""`, from an empty temporary
directory, with its input in the prompt. A deterministic runner holds the Switch key. Model output is parsed as data and
validated against the real rows and closed lists.

**Evidence that tool limits alone would not have done it:** Gate 1C's positive control gave the same model only `Read`, run from Meera's folder, and it read the file in Arjun's folder. The research plan's folder-plus-tool-limits design would not have held.

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

## ADR-009: Trips made in the app get their own folder. The three-person demo keeps its old paths

**Decided:** 7 Nov 2026. **Status:** tested. Three-person re-run recorded in `BUILD_JOURNEY.md`.

**Problem:** every process read fixed paths and ports. In-app trips need many trips side by side, and the original demo has to stay reproducible.

**Decision:** `kitty/paths.py` gives one set of paths. With `KITTY_TRIP_DIR` set, a process reads `trips/<id>/`. Without it, it reads exactly what it always read. The files inside have the same shape on both routes, so the clerk, the agents and `verify.py` have no separate code path. `setup_trip.py` stays as the command-line fallback.

**Trade-off:** two ways to create a trip have to be kept in step.

## ADR-010: Provisioning saves progress after every step, and shows only fixed sentences

**Decided:** 7 Nov 2026. **Status:** tested with a fake that fails at each of 10 points, and live with an injected failure.

**Decision:** passwords are written to disk before the call that uses them. Each finished step is recorded, and a retry skips it. A name that an earlier failed try may have taken gets a new suffix. `state/status.json`, the only thing the organiser's page reads, holds step labels and fixed codes, and never the text of a server's reply, which could hold anything. Logs get the exception's type only.

If one person's step fails, the others carry on, and the group room waits until everyone is ready. If a service is unreachable, everything stops at once. A person can be removed or added until the group room exists. After that the roster is fixed, because every agent and the clerk hold it in memory.

**Rejected:** rolling back on failure. Switch can't delete rooms (issue #1202), so rollback couldn't be complete. An unused account or room holds no data.

## ADR-011: Each person's app needs their own single-use link, on trips made in the app

**Decided:** 7 Nov 2026. **Status:** tested.

**Problem:** the person apps were open local ports, fine for a stage, but anyone who could reach the port could read that person's private room.

**Decision:** the organiser gets one link per person. Opening it swaps the link for an HttpOnly, SameSite=Strict cookie, named for that trip and person because cookies ignore ports. A used link opens nothing, so if the organiser or anyone else got there first, the person finds out. Making a new link ends the old session. All routes also check Host and Origin, so another website in the same browser can't drive the app.

**Trade-off:** the organiser could open someone's link before they do. They'd be caught by the used link, but that's detection and not prevention. Real identity would need real accounts. The original demo has no access file and stays open, because its stage embeds the apps. Its Host and Origin checks are new.

## ADR-012: A statement is checked by plain code, on its owner's side, and errors never quote it

**Decided:** 7 Nov 2026. **Status:** tested.

**Decision:** `kitty/statement_import.py` checks size, type, encoding, the exact header, and every field against a narrow pattern, including a masked account column, and refuses long digit runs that look like real account or card numbers. The saved file is rebuilt from the validated fields, so nothing unparsed reaches the agent. Messages name a line and a column, and never a value. The file name is checked and thrown away.

**Model data flow unchanged.** Upload makes no model call and adds nothing to the prompts. The model still receives only what `statements.model_view` returns, once the person starts their review. The page's "see exactly what the model provider would get" panel calls that same function, and a test compares it with what a recording model really receives. One addition: on a non-rupee trip the amount field is named `amount_usd` or similar, so that a dollar amount isn't labelled rupees. Rupee prompts are byte-identical.

**Duplicate files are detected per person, by a hash of the validated lines.** We rejected comparing across people: it would need a shared index of statement hashes, a link between private folders that exists only to catch a mistake.

**After a review starts, a new file may only add lines.** Agents know lines by position, and some may already be released. So a new file must contain every earlier line, and new ones go on the end. This is also how the suggested payments arrive in the six-person run.

**Synthetic only** is enforced as far as code can: a required tick-box, a masked account column, and the digit-run check. A determined person could still type in real data, and the page says not to.

## ADR-013: Trips have one currency, and Kitty doesn't convert

**Decided:** 7 Nov 2026. **Status:** tested without Switch for USD. The live runs used rupees only.

Rupee, dollar, euro and pound, because all four have two decimals and amounts are whole minor units. The field names still say `paise`, as renaming them would touch the claim format for no user benefit. A claim in the wrong currency is refused.

## ADR-014: A person can ask again about a line the model kept private

**Decided:** 7 Nov 2026, while preparing the six-person run. **Status:** tested without Switch.

A wrongly shared line could be dropped from the desk, but a line wrongly marked personal had no way back, so a model error would silently change who owes what. `reopen <title>` puts it back in the queue, and the app shows a button. The model proposes, and the person decides, in both directions.

## ADR-015: Post-activity fairness: investigated, not built

**Decided:** 7 Nov 2026. **Status:** design only. **Owner's call whether to build it.**

**The ask:** someone privately says they didn't take part in an activity, and a revised split gets proposed and explicitly approved, without their explanation being exposed.

**What already works:** if the payer knows at review time, they say so ("yes, but Dev skipped it"), and the split is right from the start. The six-person run does this. The gap is the case where an expense has already been released, and someone else objects.

**Design that fits the architecture:**

1. In their private room, Dev picks a released expense and says why, in any words. The reason stays there. It never reaches the model unless Dev's reply needs reading, and it's never in a claim.
2. Dev's agent files a typed `share_change` claim on Dev's desk: expense id and a closed reason list of one, `did_not_take_part`. There's no free-text field, for the same reason claims have none today.
3. Dev releases it. The group sees: "Dev asks to come off c3. Each remaining person's share would go from ₹1,800.00 to ₹2,250.00." The clerk computes this from integers.
4. Everyone whose share rises, and the payer, each approve or refuse on their own desk, using the same sender-id check as releases. Their answers are theirs too.
5. Only with every approval does the clerk replace the shares. One refusal leaves the original standing, and the group sees only that it wasn't agreed.

**What this can't hide:** that Dev asked. Others pay more, so they're entitled to know who came off and by how much. Only the reason stays private. Making the asker anonymous would mean others approve a cost they can't assess, and on a six-person trip the changed share would reveal it anyway.

**Why it's not built today:** it adds a claim type, a multi-party approval state, a new way for a released expense to change, a settled-payments edge case (what if money has already moved on the old numbers?), and matching work in `verify.py`. That touches the part of the system with the strongest evidence, close to the deadline, and the six-person run shows the common case is already handled. About a day's careful work, with tests.

## ADR-016: The Pot trip: one engine, one `treasurer` setting, and only the recipient's confirmation counts

**Decided:** 7 Nov 2026, direction by the owner. **Status:** specified in `docs/POT_TRIP_SPEC.md`. **Not built. Implementation waits for the owner's approval of the spec.**

**The ask:** make Kitty useful for Indian groups whose money runs through one organiser's personal UPI account, without anyone exposing their financial records.

**Options:** (a) stay as a reconciler with an evidence vault; (b) a separate Treasurer Mode; (c) a lightweight expense management platform; (d) two workflows on one engine.

**Decision:** (d), reduced to a single trip setting. With `treasurer` set, a member-to-treasurer payment becomes a `contribution`, and it counts only while the treasurer's own human account has confirmed it. The treasurer's agent checks the treasurer's records in plain code with no model. A screenshot can't change any state.

**Why:** the code needs no new accounting. `ledger.balances` already nets contributions, spending and refunds through one person. We checked `pot_position == -net[treasurer] - treasurer's shares` and hub settlement over 20,000 random histories in a throwaway script. Research found receipt OCR and approval queues are commodity, and approval is crowded in this very hackathon. Confirming money against a private account is the part we didn't find elsewhere, within limited search.

**Rejected:**

- *Reusing `find_credit`.* We ran it: it returns a credit that was later taken back, and matches two claims to one row. That's acceptable for Mode A's story, and wrong for a pot. A new search goes in new code, and Mode A's stays as it is.
- *Giving the clerk the transactions so it can validate matches.* That breaks ADR-002. The clerk checks an attestation instead, using keyed tokens it can compare and can't read.
- *Sending references through desks.* That would fail an existing `verify.py` rule, rightly. Tokens replace them.
- *A treasurer change, or an organiser override, in this version.* Nobody else has the money, so an override would invent a confirmation.
- *An expense approval gate.* Demand is unproven, and it can come later.

**Trade-offs and limits:**

- A confirmation is a person's word. A treasurer can lie, and collusion can't be detected.
- The decision history is append-only and tamper-evident, not immutable. Whoever controls the database can still rewrite everything.
- **Whoever hosts Switch or Mattermost can read private rooms,** so a treasurer who is also the host isn't bound by room boundaries.
- A treasurer with no statement can only confirm by hand, and then Kitty is little more than a tracker.
- Real 12-digit references, real statements, groups above 12, and use without Docker are all out of scope.
- **No user research.** Five interview questions and the assumptions to test are in the spec, and nothing beyond it should be built until they have answers.
