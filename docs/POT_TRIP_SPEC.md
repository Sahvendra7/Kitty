# The Pot trip: product specification

**Status: approved, and Core is built.** What Core leaves out is listed in `docs/superpowers/plans/2026-11-07-pot-trip-core.md`, and later changes are in `docs/BUILD_JOURNEY.md`, section E. Written Sat 7 Nov 2026, against `feat/kitty-mvp` at `4ba9d82`: clean working tree, level with its remote, 184 of 184 tests passing. Pre-sprint checkpoint: tag `checkpoint/pre-product-sprint`, commit `0c28571`, plus `~/Desktop/Switch/checkpoints/`.

Background research: `../research/kitty-india-expense-workflows.md`. Decision record: ADR-016. No code was changed to write this.

## 1. Decision in brief

One organiser collects the group's money on their own UPI account. Members submit payment claims. The organiser's own agent checks those claims against the organiser's private records, in plain code. Only the organiser's explicit confirmation changes the shared ledger.

A new `treasurer` trip setting turns this on. Without it, Kitty behaves exactly as it does today.

### Where this spec departs from the brief, and why

| The brief says | The spec says | Reason |
|---|---|---|
| The clerk "validates the transaction match" | The clerk validates the **attestation**: who sent it, the amount, opaque tokens matching, the credit not already used. The row match happens in the treasurer's agent | The clerk has no path to any statement and gets 403 on private rooms (ADR-002). Giving it transactions would break the architecture |
| "Immutable decision history" | **Append-only and tamper-evident** | Nothing here stops whoever controls the database rewriting everything. We can make a change detectable to anyone holding an earlier copy, and no more |
| Candidate found and recipient review as separate states | One stored state, `recipient_review`, with a `candidate` attribute | They differ by one fact, and the second adds no decision |
| Treasurer "can request further evidence" | Closed reason codes, and the proof flow is a separate later step (§10) | Core works with no file handling. File relay is untested by us |

## 2. Personas

Assumed from desk research, with **no interviews yet** (§15).

| Persona | Situation | Wants |
|---|---|---|
| **Asha, trip organiser or club treasurer**, 19–24 | Runs the money through a personal UPI account, which also shows salary or allowance, rent and shopping | To know who has paid without opening a bank app for each person, and to be trusted without showing the statement |
| **Rohan, member** | Pays Asha from a UPI app, sometimes late, sometimes in parts | To see that their payment counted, with no argument |
| **Meera, member with a problem** | Payment failed, or the wrong amount went, or someone else paid for them | A clear next step, with no public accusation |
| **Dev, spender** | Bought groceries for the group out of pocket | To be paid back, on the record |

Everyone must be 18 or over for now (§12).

## 3. Trip modes

| | **Mode A: shared expenses** | **Mode B: the Pot** |
|---|---|---|
| Setting | `treasurer` absent | `treasurer` names one person on the trip |
| Expenses | Anyone releases | The same |
| Money between people | `transfer`, any direction | Member to treasurer is a `contribution`. Treasurer to member, and member to member, stay `transfer` |
| Settlement suggestion | `L.settle`, fewest payments | Everyone settles with the treasurer |
| Claim dates | Trip dates only | Contributions also from `collect_from`, as people pay before a trip |
| Member statement | Required | Optional. A member can type "I paid" |
| Group command | `balances`, `settle`, `status` | The same, plus `pot` |
| Status | Live tested | Live tested, six people |

**The setting is fixed once the group room opens,** like the roster (ADR-010). There are no separate engines, spaces or group types.

## 4. Workflow

| # | Who | Action | What the group room shows |
|---|---|---|---|
| 1 | Organiser | Creates a trip, picks the treasurer, sets the collection start date | Nothing yet |
| 2 | Treasurer | Asks for money: `call 5000` | `**k1** Asha asked each person for ₹5,000.00.` |
| 3 | Member | Pays from their own UPI app, **outside Kitty** | |
| 4 | Member's agent | Finds the debit on their statement, or the member types amount, date and optional reference. Files a typed claim on the desk | Nothing |
| 5 | Member | Releases it, by their own account | `**p3** Rohan says they paid Asha ₹5,000.00 on 9 Oct. Not counted yet.` Adds `· ref …1001` if the member opted in |
| 6 | Treasurer's agent | **Plain code, no model.** Looks for the credit | Nothing |
| 7 | Treasurer | Sees a private chit, then confirms, rejects or asks | See §5 |
| 8 | Clerk | Checks authority and tokens, appends the decision, recalculates | `**p3 confirmed received by Asha:** Rohan paid ₹5,000.00 · checked against Asha's records · 9 Oct, 14:02 IST. [d7 a1b2c3d4e5f6]` |
| 9 | Anyone | `pot` | Table of asked, confirmed and outstanding, plus pot position |
| 10 | Treasurer or member | Pays vendors or buys for the group. Their agent proposes expenses, they release | As today |
| 11 | Treasurer | Reimburses or refunds outside Kitty. Existing `transfer`, confirmed by the **member** as recipient | As today |
| 12 | Clerk | All balances zero, nothing open | `**Everyone's square.**` |

The confirmation timestamp is when the treasurer decided, taken from the server's message time. It isn't the bank's credit time, which stays private. Every post that records a decision ends with a chain tag (§9), shown here only on step 8 for space.

**A call never creates a debt.** Only released expenses do. "Outstanding" says who hasn't paid in what the treasurer asked for, and balances say who owes whom. The two can differ, and the UI keeps them apart. `call 5000` asks everyone but the treasurer, `call 500 rohan meera` asks named people, and `cancel k1` withdraws a mistaken call as a new decision.

## 5. State machine

### Per person: derived, never stored

| Label | Rule |
|---|---|
| **Awaiting payment** | Asked exceeds credited, with nothing open |
| **Part paid** | Credited above zero and below asked |
| **Paid** | Credited equals asked |
| **Above ask** | Credited exceeds asked |

### Per contribution: stored

```text
              drop
   held ──────────────► dropped
    │ release (owner)
    ▼         recheck (treasurer)
 submitted ◄───────────────────────────┐
    │ pot_match (treasurer's agent)    │
    ▼                                  │
 recipient_review ── ask ──► needs_clarification
    │         │                        │
    │         └── reject ──► rejected ◄┘ reject
    │ confirm                 │  ▲  │
    ▼                         │  │  └ withdraw (member) ──► withdrawn
 confirmed ── correct ────────┘  │
    ▲          (not received)    │
    └──────────── correct ───────┘
                  (received)
```

Any open state (`submitted`, `recipient_review`, `needs_clarification`) can also go to `withdrawn` by the member.

**The stored status is a fold over an append-only decision list.** Nothing is overwritten. "Corrected" is a kind of decision, and the result of one is `confirmed` or `rejected`.

### Transitions

| # | From | Event | Actor, checked by server-set sender | Guard | To |
|---|---|---|---|---|---|
| 1 | none | `contribution` claim | Member's agent, on member's desk | Valid shape. `to` is the treasurer. Date from `collect_from` to trip end. No duplicate (§6) | `held` |
| 2 | `held` | `drop p3` | Member | Owner | `dropped` |
| 3 | `held` | `release p3` | Member | Owner | `submitted`. Clerk sends `KITTY_POT_CHECK` to the treasurer's desk |
| 4 | `submitted` | `pot_match` | Treasurer's agent, on treasurer's desk | Contribution exists, is `submitted`, and result is in the closed list | `recipient_review` |
| 5 | `recipient_review` | `pot_match` | Treasurer's agent | Used after the treasurer picks among several | `recipient_review` |
| 6 | `recipient_review` | `confirm p3` | Treasurer, on own desk | Candidate is `exact` or `selected`. Token unused. Reference tokens agree if both exist | `confirmed`, basis `records` |
| 7 | `recipient_review` | `confirm p3 manual` | Treasurer, on own desk | Candidate is `none` | `confirmed`, basis `word` |
| 8 | `recipient_review` | `ask p3 <code>` | Treasurer | Code in `amount_differs`, `date_differs`, `not_found_yet`, `send_proof` | `needs_clarification`. Group sees status only, member's desk sees the code |
| 9 | `recipient_review`, `needs_clarification` | `reject p3 <code>` | Treasurer | Code in `not_received`, `duplicate`, `reversed` | `rejected`. Clerk sends `KITTY_RECHECK p3` to member's desk |
| 10 | `submitted`, `recipient_review`, `needs_clarification` | `recheck p3` | Treasurer | | `submitted`, check re-sent |
| 11 | any open, or `rejected` | `withdraw p3 <reason>` | Member | Existing closed reasons | `withdrawn` |
| 12 | `rejected` | `dispute p3` | Member | Once | Stays `rejected`, flagged in the group. Moves no money |
| 13 | `confirmed` | `correct p3 <code>` | Treasurer | `reversed`, `not_received`, `mistaken` | `rejected`, new decision, group post shows the balance effect |
| 14 | `rejected` | `correct p3 received` | Treasurer | Token check as in 6, if one exists | `confirmed`, new decision |

Everything else is refused with a fixed code, in the style of `claims.py`. Agents sending `confirm`, `reject`, `ask`, `correct` or `release` get `agent_cannot_approve`.

### Hard rules

1. **A contribution counts on the ledger only while its status is `confirmed`.** Only transitions 6, 7 and 14 create that, and only from the treasurer's own human account.
2. **A screenshot can't cause any transition.** No claim field or command accepts one, and §10's proof flow touches no status.
3. A member can't undo a confirmed contribution. Getting money back is a refund, confirmed by that member as recipient.
4. Decisions, whether by hand or by agent, never carry free text.
5. **"Everyone's square" needs no open contributions, and a `rejected` one counts as open** until the member withdraws it or the treasurer corrects it. Today's check also requires a released expense, so a Pot trip fully refunded with no expenses needs its own version.

### Candidate results from the treasurer's agent

| Result | Meaning | Treasurer sees | Confirm allowed? |
|---|---|---|---|
| `exact` | One credit: same amount, within 2 days, same name, not reversed, not already used | "Found it: 9 Oct." | Yes |
| `selected` | The treasurer picked one from a list | The same | Yes |
| `several` | More than one fits, or the amount fits but the name doesn't (a parent paid, say) | A list in their **own app**, read locally, never posted to Switch | After picking |
| `amount_differs` | Same person and window, different amount | "Something near this date, for a different amount" | No. Ask, then the member withdraws and refiles |
| `none` | Nothing found, or the statement is older than the claim, or there is no statement | "Nothing yet. Your statement ends 8 Oct." | Only `confirm p3 manual` |

"Found nothing" is **never** presented as "not received". Today's `_check` does present it that way, which is right for Mode A's two-sided story and wrong here, as a treasurer's export is usually stale.

## 6. Duplicates, retries, reversals, wrong confirmations

| Case | Handling |
|---|---|
| Same member, same date, same amount, neither has a reference | Refused `possible_duplicate`. Adding a reference tells them apart |
| Same reference on any two active contributions, from anyone | Refused `duplicate_reference`. The refusal doesn't say whose |
| One credit against two claims | Treasurer's agent won't offer a used row. **Clerk also refuses** `credit_already_used` on a repeated token, so losing the agent's memory can't double-count |
| Button pressed twice, message resent | Second one refused `already_<status>`. Resending is always safe, as today |
| Confirm crosses with a withdraw | The clerk is single-threaded and the log is ordered, so the first wins, the second gets a fixed code, and replay reproduces the same result |
| Credit taken back **before** confirmation | Candidate search skips any credit followed by a `UPI/REV` line or a debit with the same reference. Result `none`, private note |
| Credit taken back **after** confirmation | On a statement refresh, the agent re-checks bound rows and tells the treasurer privately. A person runs `correct p3 reversed`. Never automatic |
| Failed payment, on the payer's side | `KITTY_RECHECK` runs today's `find_reversal` privately, then `withdraw p3 payment_failed` |
| Wrong confirmation | Transition 13 or 14. Old decision stays, new one appended, balances recomputed. Nothing else is edited (§7) |
| Member wrongly confirms a reimbursement | **No correction path.** It's an ordinary `transfer`, and Mode A has none. Left as is, since the false yes harms the member who gave it. Noted as a limit |

**Asymmetry in corrections, stated openly.** A treasurer admitting receipt harms only themselves. A treasurer retracting a confirmation harms the member, so it's always visible in the group with its balance effect, and the member can `dispute`. The contribution stops counting at once, as the confirmation no longer exists. Kitty doesn't arbitrate.

## 7. Ledger

`ledger.py` doesn't change. Contribution records are kept apart from every other kind of money, and only projected into `L.Payment` when balances are computed.

| Kind of money | Record | Who confirms | Ledger effect |
|---|---|---|---|
| **Collection** | `contribution`, member to treasurer | Treasurer | `L.Payment(member, treasurer)` |
| **Spent by treasurer, including vendor payments** | Released `expense`, payer is the treasurer | Treasurer releases | `L.Line` |
| **Spent by a member** | Released `expense`, payer is the member | Member releases | `L.Line` |
| **Reimbursement or refund** | `transfer`, treasurer to member | **Member** | `L.Payment(treasurer, member)` |
| **Member to member** | `transfer`, as today | Both | `L.Payment` |

A `transfer` from a member to the treasurer is refused in Mode B with `use_contribution`, so collection can't arrive by two routes.

### Figures, all integer paise, all derived

```text
asked(m)       = sum of calls naming m. The treasurer is never asked.
credited(m)    = sum of m's contributions currently confirmed
outstanding(m) = max(0, asked(m) - credited(m))
above(m)       = max(0, credited(m) - asked(m))
collected      = sum of credited(m)
spent          = sum of released expenses paid by the treasurer
paid_out       = sum of confirmed transfers from the treasurer
pot_position   = collected - spent - paid_out      May go negative: the treasurer fronted money
reimbursed(m)  = min(m's released expenses, confirmed transfers treasurer -> m)
refunded(m)    = confirmed transfers treasurer -> m, minus reimbursed(m)
```

**Pot position describes the ledger, not a bank balance.** It says what the treasurer should be holding according to confirmed records. Kitty can't check it against the account.

### Invariants, checked

Probed in a throwaway script over 20,000 random histories (`pot_position` identity and hub settlement both held). The script isn't kept, and these become property tests:

1. Balances sum to zero.
2. `pot_position == -net[treasurer] - treasurer's expense shares`.
3. The hub plan clears every balance, in at most n−1 payments, and every payment involves the treasurer.

### Hub settlement

```python
def settle_via(net, hub):  # new, in kitty/pot.py. L.settle stays as it is.
    return [Payment(p, hub, -v) if v < 0 else Payment(hub, p, v)
            for p, v in net.items() if p != hub and v]
```

### Worked example (computed with the real `ledger.balances`)

Six people. Each of five is asked ₹5,000. Rohan, Esha, Farhan and Dev pay in full. Meera pays ₹3,000. Asha spends ₹24,000 and ₹9,000. Dev spends ₹3,600. All three are split among six, ₹6,100 each.

| Person | Paid in or spent | Share | **Net** | Settles by |
|---|---:|---:|---:|---|
| Asha | +33,000 spent, −23,000 received | 6,100 | **+3,900** | |
| Rohan | +5,000 | 6,100 | **−1,100** | Pays Asha ₹1,100 |
| Esha | +5,000 | 6,100 | **−1,100** | Pays Asha ₹1,100 |
| Farhan | +5,000 | 6,100 | **−1,100** | Pays Asha ₹1,100 |
| Meera | +3,000 | 6,100 | **−3,100** | Pays Asha ₹3,100 |
| Dev | +5,000 + 3,600 | 6,100 | **+2,500** | Asha pays Dev ₹2,500 |

Pot position: 23,000 − 33,000 = **−₹10,000**. Asha has fronted that much, of which ₹6,100 is Asha's own share.

**If Meera's ₹3,000 was confirmed wrongly and later corrected:** Asha goes to +₹6,900 and Meera to −₹6,100. Everyone else is unchanged, the sum is still zero, and both decisions remain visible.

**A correction after money moved on the old numbers** edits nothing else. If Asha had already refunded someone, that transfer stands, and the new balances show who now owes whom.

### Coexistence with Mode A

| Guarantee | How |
|---|---|
| Mode A posts are byte-identical | Golden transcript test, captured at `4ba9d82` **before any change** |
| Mode A model prompts are byte-identical | Recording-brain test |
| `claims.py`, `ledger.py`, `statements.py`, `statement_import.py`, `llm.py`, `llm_brain.py`, `agent.py` | **Zero diff**, asserted at review |
| `clerk.py` | Only `if self.pot` hooks. With `pot=None`, each hook falls through |
| `verify.py` | New checks only when `treasurer` is set. Mode A counts stay at 37, 61 and 28 |
| Existing commands | `confirm`, `pot` and the rest mean nothing in Mode A, as today |

## 8. Permission model

| Action | Member | Member's agent | Treasurer | Treasurer's agent | Clerk | Organiser or host, as themselves |
|---|---|---|---|---|---|---|
| File a contribution | | ✅ own desk | | | | |
| Release or drop it | ✅ own | ❌ | | | | |
| Withdraw, dispute | ✅ own | ❌ | | | | |
| Read treasurer's statement | ❌ | ❌ | ✅ | ✅ plain code | ❌ no path | See §11 |
| Post `pot_match` | | | | ✅ own desk | | |
| `call`, `confirm`, `reject`, `ask`, `recheck`, `correct` | ❌ | ❌ | ✅ own desk only | ❌ | | |
| Confirm a transfer **to** themselves | ✅ | ❌ | ✅ | ❌ | | |
| Confirm their **own** payment out | ❌ | ❌ | ❌ | ❌ | | |
| Enter the group room | ✅ | ❌ 403 | ✅ | ❌ 403 | ✅ | See §11 |
| Enter someone else's private room | ❌ 403 | ❌ 403 | ❌ 403 | ❌ 403 | ❌ 403 | See §11 |
| Change the treasurer | No one, once the group opens | | | | | |

Authority comes from the sender id Switch sets and the room an event arrived in, never from message text (ADR-004). **The treasurer gets no extra read access.** It's a role over decisions, not a view over data.

## 9. Data model

### `trip.json`, Mode B only

```json
{"treasurer": "asha", "collect_from": "2026-09-25"}
```

People pay in and settle from `collect_from` to 30 days after the trip. Spending stays within the trip's dates. **These dates widen only what plain code searches. The model's window stays the trip's dates.**

### New typed claims, parsed in `kitty/pot.py`

`claims.py` doesn't change, as `pot.py` tries its own types first and passes anything else through.

```json
{"v":1,"type":"contribution","ref":"r3","trip_id":"…","owner_id":"rohan","to_id":"asha",
 "date":"2026-10-09","amount_paise":500000,"currency":"INR",
 "ref_token":"<32 hex or null>","ref_last4":"<4 alphanumerics or null>"}

{"v":1,"type":"pot_match","ref":"a7","trip_id":"…","owner_id":"asha","contribution_id":"p3",
 "result":"exact","credit_token":"<32 hex or null>"}
```

Exact keys, closed values, no field that can carry a narration, as with every claim today.

### Reference tokens: how the clerk can check without seeing

`token = HMAC-SHA256(pot_key, normalised_reference)`, truncated to 32 hex characters.

- `pot_key` is one random per-trip secret given to each **personal agent's** state at provisioning, and **not** to `clerk.json`.
- The clerk compares `ref_token` with `credit_token`, and refuses a repeated `credit_token`, without learning any reference.
- If a statement line has no reference, the agent tokens its row identity instead, so one row is still used once.
- **Limit:** anyone with the key could brute-force a short reference. On one laptop, every process could read it.

This also keeps a rule `verify.py` already enforces, that statement references never appear on desks or in the group room. `ref_last4` is the only part that can reach the group, and only if the member ticks a box. The default is off.

**Real 12-digit references don't work yet.** The importer deliberately refuses digit runs of 12 or more as a guard against real data (ADR-012, code `long_number`, which we ran), and it stays unchanged. So the synthetic data uses short references like `U441001`, and the member's field takes 4 to 16 letters and digits. A real UTR typed by a member simply won't match a synthetic statement. Real formats belong with real-statement support, which is out of scope. Labels also vary by app, so the form says "UPI reference" and matches on the value alone.

### Existing behaviour the build has to handle

| Where | Today | In Mode B |
|---|---|---|
| `agent_runner.py` | With no statement, the agent answers every private message with `NO_STATEMENT` | Allowed through for the "I paid" command |
| `agent.py` `_check` | With no statement, files "did not reach you" automatically | `PotAgent` asks the member instead, as for a treasurer's reimbursement |
| `agent.py` `_scan` | Files any payment to a friend as a `transfer`, for trip dates only | `PotAgent` files payments to the treasurer as contributions, and also looks from `collect_from` |
| `agent.py` `_scan` for the treasurer | The first pass calls the model | Separated from collection (§11) |
| `clerk.py` `_square_check` | Requires a released expense | Pot version (§5, rule 5) |
| `clerk_daemon.py` | Logs no timestamps, and matches a fixed command list | Mode B extends both |
| `person_app.py` | Reads clerk text with regexes | New patterns for new templates |

### Clerk records

```python
@dataclass(frozen=True)
class Decision:           # append-only list in PotBook
    seq: int              # d1, d2, ... with no gaps
    at: str               # the server's message time, kept in the event log
    actor: str            # server-set sender id
    kind: str             # call | submitted | confirm | ask | reject | withdraw | dispute | correct
    target: str           # k1, p3
    basis: str | None     # records | word
    code: str | None      # closed list

@dataclass
class ContributionRecord:
    id: str
    claim: Contribution
    candidate: str | None
    credit_token: str | None
    disputed: bool = False
    # status is computed from the decisions, and never stored as a value to overwrite
```

### Decision chain

Each group post that records a decision ends with `[d7 a1b2c3d4e5f6]`, where the hash is `sha256(previous_hash + post_text)`, first 12 hex characters. `verify.py` recomputes the chain from room text alone.

**What it gives:** gaps and edits become detectable, by anyone with an earlier copy.
**What it doesn't:** someone rewriting the database and every copy goes unnoticed.

### Event log and replay: a real gap to fix

Today the clerk's `audit` list is memory only, and replay after a restart gives every record a **new** time. In Mode B, the daemon must store the server's timestamp with each logged event and feed it back on replay. `should_log` must also learn the new commands, or decisions would vanish on restart, which gets its own test.

### Files

| Change | Files |
|---|---|
| New | `kitty/pot.py` (pure: parsing, decisions, status, figures, hub, chain), `kitty/pot_agent.py` (`PotAgent(PersonalAgent)`, candidate search), `scripts/pot_trip.py`, `data/synthetic/pot/`, `tests/test_pot_*.py`, `tests/test_mode_a_golden.py` |
| Small, guarded | `clerk.py`, `clerk_daemon.py`, `agent_runner.py`, `provision.py`, `trips.py`, `home_app.py`, `person_app.py`, `app/home.html`, `app/person.html`, `app/board.html`, `scripts/verify.py` |
| Zero diff | `claims.py`, `ledger.py`, `statements.py`, `statement_import.py`, `llm.py`, `llm_brain.py`, `agent.py` |

## 10. Proof on request: limited follow-up, **not in the core build**

An owner can approve one specific shareable copy for one expense or payment. It's not a document store.

| Step | What happens |
|---|---|
| Ask | A member says `proof c3` in the group, or the treasurer uses `ask p3 send_proof` |
| Notify | The clerk puts a chit on the owner's desk |
| Prepare | The owner picks a file **they prepared themselves** in their app. The app accepts JPEG or PNG, decodes and re-encodes, strips metadata, and computes a hash. No OCR, no auto-redaction |
| Approve or decline | `share c3` or `decline c3`, by the owner's own account. A decline tells the group only that it was declined |
| Deliver | The clerk fetches the file through Switch's media route, checks the hash against the release, and posts it. A member's request goes to the group room. A treasurer's goes to the treasurer's desk, whose agent never downloads attachments |

**Originals stay on the owner's side.** Nothing about proof changes a status. A shared screenshot is one more thing a person can look at.

### Capabilities that don't exist, which we won't imply

| Missing | Reality | What we'd say on screen |
|---|---|---|
| **Recall** | We found no way to delete a posted message or file in the protocol doc, and haven't tested for one. Anyone in the room can download or forward it | "Once shared, this can't be taken back" |
| **Access audit** | We found no record of who opened a file. We can log who was **granted** it, not who looked | "Kitty records that it was shared, not who opened it" |
| **Host privacy** | Files sit in Switch's media store and Mattermost | Covered by §11 |
| **Authenticity** | A prepared copy can be doctored | "This is the copy the owner chose to share" |

Switch's source shows attachments in both directions and over Mattermost, but **we've never run it**. A 45-minute live spike comes first, and if it fails this section stays unbuilt.

## 11. Privacy

### What may reach the group room

Only: member identity, claimed amount, contribution status, confirmed amount, confirmation time, and the last four characters of a reference if the member opted in.

**Never:** transaction history, unrelated merchants, balances, salary, rent, personal purchases, unrelated counterparties, or narrations.

### Who can see what, in Mode B

| Information | Treasurer | Their agent's code | Model provider | Clerk | Members | **Whoever hosts Switch and Mattermost** |
|---|---|---|---|---|---|---|
| Statement file | ✅ | ✅ | ❌ for collection (see below) | ❌ | ❌ | ❌ not in Switch, but on the same disk in this one-laptop setup |
| Credit lines being checked | ✅ | ✅ plain code | ❌ | ❌ | ❌ | ❌ |
| "Found it: 9 Oct" on a private chit | ✅ | ✅ | ❌ | ❌ 403 | ❌ 403 | **✅ can read private rooms** |
| Candidate list | ✅ own app, local | ✅ | ❌ | ❌ | ❌ | ❌ |
| `pot_match`, tokens | ✅ | ✅ | ❌ | ✅ | ❌ | **✅** |
| Treasurer's spending lines inside trip dates | ✅ | ✅ | **✅ only if the treasurer starts a spending review** | ❌ | ❌ | Only what's discussed in rooms |
| Member's typed payment | ✅ claim only | ✅ | ❌ | ✅ | ✅ when released | **✅** |

### The infrastructure host

**Kitty doesn't promise privacy against whoever hosts Switch or Mattermost.** They can read every room, including private ones. Mattermost's seeded `admin` and `user` accounts are members of every channel, as `verify.py` shows. There's no end-to-end encryption. On the current one-laptop setup, one OS user could read every folder.

**So if the treasurer is also the host, room boundaries don't bind them.** They could read members' private rooms. The reverse applies too: a host who isn't the treasurer could read the treasurer's private chits. The product therefore:

1. Keeps text in private rooms to a minimum: no narrations, and no counterparties beyond the one the claim named. Candidate lists stay out of Switch.
2. Says this plainly when a Pot trip is created, in the organiser's own view, in words a student would understand.
3. Never calls the treasurer's account "private" without saying from whom.

### The model and the treasurer's statement

Matching makes **no model call** (ADR-006). But today, pressing "Go through my statement" sends every spending line inside the trip dates to the model provider, including personal ones. For a treasurer's personal account that's a much bigger disclosure, so in Mode B:

- Loading a statement and checking contributions never involve a model.
- Spending review is a **separate, explicit step**, with the existing preview of the exact lines.
- A treasurer can skip it and enter pot expenses by hand.
- A recording-brain test proves that no credit, and no line from before the trip, reaches a prompt.

### Threats

| Threat | Control | What's left |
|---|---|---|
| Narration, balance or counterparty reaches the group | Typed claims. Fixed post templates built from integers and ids. `verify.py` scan extended to the treasurer's balances and unrelated names, with a planted-text positive control | Numbers carry some meaning |
| Group infers things from the treasurer's behaviour | Decision times are the only timing shown | Slow confirmation is visible |
| `amount_differs` leaks a bank detail | Goes to that member's desk only. It's about their own payment | The group sees that something needs clarifying |
| Treasurer's statement reaches the model | Separate step, disclosed, tested | If they choose it, it's sent |
| Reference on desks | Tokens only | Key holder could brute-force a short value |
| Logs | Existing rule: only state-changing messages, fixed codes | |
| Host reads rooms | Disclosed. Minimum text | **Not solved** |
| Treasurer forwards what they can see | Nothing | Outside any software |

## 12. UI changes

| Screen | Change | Mode A effect |
|---|---|---|
| **New trip** | "Who holds the group's money?" Default: "Nobody, everyone pays and we settle up". Picking someone reveals the collection start date and the host warning | Default unchanged |
| **Member's app** | "Your contribution" card with asked, confirmed and outstanding. "I paid" form with amount, date, optional reference, and a tick to show its last four in the group. A chit that names each state in plain words | None |
| **Treasurer's app** | "Collection" tab listing each claim, with Confirm, Not received, Ask. Candidate review reads the statement locally. "Ask for money" form. "Correct this" with a reason and a warning of the balance effect | None |
| **Trip board** | Roll-call and pot position when `treasurer` is set | None |
| **Group room** | `pot` command, new line templates | None |

### Wording rules

| Say | Never say |
|---|---|
| "Asha confirmed it arrived" | "Verified", "bank-confirmed" |
| "Asha can't match this yet" | "Fake", "fraud", "suspicious" |
| "Checked against Asha's records" | "Proven" |
| "On Asha's word" (basis `word`) | Anything suggesting a statement check took place |
| "Should be holding ₹X, by the records" | "Has ₹X" |

## 13. Failure cases

| Failure | Behaviour |
|---|---|
| No matching credit | `none`. Private note says what the statement covers. Nothing goes to the group. Treasurer can refresh, ask, reject or confirm manually |
| Treasurer's statement is stale | The same, and the note gives the last date. `recheck` after adding a later export, which only appends (ADR-012) |
| Treasurer has no statement | `none` every time. Only `confirm manual` is left, shown as "on Asha's word". Works, but **loses what makes Kitty different**, and we should say so |
| Member has no statement | Types the claim. No debit check on their side, and no reversal check |
| Treasurer's agent is down | Claim waits in `submitted`, resumes from its saved cursor (already tested for restarts). The group sees "Waiting for Asha" |
| A message is lost (one in about 700 so far, cause unknown) | Stuck in `submitted`. `recheck` re-sends. No silent retry |
| Clerk's post to a room fails | Daemon retries. If it still fails, it refuses new pot decisions, and the board says the room is out of step. The chain check catches it |
| Crash between logging an event and posting | On start, the clerk compares its last decision with the room's last chain number and posts what's missing. **New behaviour, Mode B only** |
| Wrong amount paid | Member's claim states what they actually paid. Exact match, part paid. If they claimed wrongly: `amount_differs`, ask, withdraw, refile |
| Paid twice by mistake | Two credits, two claims, separate references. Shows **Above ask**, and the refund is an ordinary transfer |
| Paid from a parent's or friend's account | Name doesn't match, so `several`. Treasurer picks the line locally. Basis stays `records` |
| Member types a wrong reference | Clerk refuses the confirm with `reference_differs`. Treasurer asks, member withdraws and refiles |
| One payment covers several people | §14, question 7 |
| Treasurer disappears | Nothing can be confirmed. There's **no override**, since nobody else has the money. Records stand, and people must sort it out themselves |
| 12 or more people | 12 is the app's limit and six the most run live. **A college event of 50 or more can't run on this architecture,** as each person needs an agent process, an app process, two rooms and an account |

## 14. Red team

| # | Question | Answer | Mechanism | Remaining risk | Tests |
|---|---|---|---|---|---|
| 1 | **Can the treasurer falsely confirm?** | **Yes.** Confirmed money is what they then owe the group, so a false yes hurts them, unless they collude with the member | Their own account only. Visible in the group. Basis shown | **Collusion to inflate the pot can't be detected.** A statement can also be edited before upload. A confirmation is a person's word, with or without a statement | `test_only_treasurer_account_confirms`, `test_agent_cannot_confirm`, `test_basis_shown` |
| 2 | **Can one UPI claim be submitted twice?** | Not while both are active, in the checked ways | Same member, date and amount refused without a reference. Reference token unique across the trip. Credit token used once, checked in **both** agent and clerk | Different claims with no reference, on different dates within slack, are stopped only by the one-row rule. Two typed-in made-up references pass until the check finds no credit | `test_duplicate_*`, `test_credit_token_single_use` |
| 3 | **Can a failed or reversed payment be confirmed by accident?** | **Today's `find_credit` would allow it.** We ran this: it returns a credit later taken back, and matches two claims to one row. The spec doesn't reuse it | New candidate search skips reversed rows and used rows. Later reversals raise a private prompt. Manual confirm remains possible, labelled | A treasurer can manually confirm something that later fails. The reversal format on the recipient's side is **our assumption**, and only synthetic | `test_reversed_credit_is_not_a_candidate`, `test_reversal_after_confirm_prompts_only` |
| 4 | **Can the treasurer's account data leak?** | Not through the typed path. **Yes through the host,** and through the model if they opt in | Fixed templates. Tokens. Local candidate list. Separate model step. Extended `verify` with a plant | Host. One-laptop filesystem. The treasurer's own choices | `test_group_posts_are_templates`, `test_no_credit_reaches_model`, `verify` canaries |
| 5 | **Can someone impersonate a member?** | Not through text | Server-set sender id. Single-use invites. Desk ownership. Stranger on a desk refused. Unique names | The organiser could open an invite first, which is detected and not prevented (ADR-011). Host or a stolen session can act as anyone | Existing `test_a_stranger_in_a_desk_is_refused`, `gate1d_release_live.py` |
| 6 | **No matching credit?** | `none`. **Never** "not received", and nothing goes to the group | Treasurer chooses the next step. Contribution stays uncounted meanwhile | A treasurer could ignore it. The member sees "Waiting for Asha" | `test_none_stays_private`, `test_none_blocks_plain_confirm` |
| 7 | **One payment covers several members?** | **The money is right, the roll-call looks wrong.** Rohan pays ₹15,000 for three: Rohan shows Above ask by ₹10,000, the other two show outstanding. Final balances are still correct, as those two owe and Rohan is owed | Core: as described. They can each pay Rohan through an ordinary transfer | **Real usability gap, and probably common.** Later fix, not core: a two-sided `cover` that moves roll-call credit only, offered by Rohan and accepted by the covered person, and never touching the ledger | `test_overpayment_keeps_balances_right` |
| 8 | **Wrong amount?** | The claim must state the real amount. It counts as part paid. Claim and credit differing gives `amount_differs` and no confirm path until refiled | The treasurer can't edit a member's claim | A member who paid ₹4,500 and claimed ₹5,000 gets a private ask, not a public accusation | `test_amount_differs_blocks_confirm`, `test_part_payment_recalculates` |
| 9 | **The treasurer changes?** | **Not supported.** Fixed when the group opens, because agents and the clerk hold it, and it would split the pot's history | Workaround: settle up and start a new trip | **Real gap,** as treasurers do change. A later era-based handover would need the new treasurer's acceptance and a confirmed transfer of the pot position. There's no override if a treasurer vanishes, by design | `test_treasurer_is_fixed_after_group_opens` |
| 10 | **Can a wrong confirmation be reversed without corrupting the ledger?** | Yes | Balances are recomputed from current status. No record is edited. Balance sum and pot identity are properties over any sequence, including flip-flops | A retraction can be abused. It's visible and disputable, not preventable. Money may already have moved, and only people can sort that out | `test_correction_is_append_only`, property test over random decisions, `test_correction_after_settlement` |

### What the red team changed in this spec

1. **`find_credit` not reused,** after running it. It accepts reversed credits and matches one row twice. A new search is required, and Mode A's stays as it is.
2. **"Found nothing" no longer means "not received".**
3. **The clerk got its own double-use check,** since the agent's memory alone isn't enough.
4. **Collection dates added.** `date_outside_trip` refuses any payment made before the trip, which is when pots get collected. We ran this too.
5. **Model step separated for the treasurer,** after seeing what "Go through my statement" sends.
6. **Real timestamps in the event log,** since replay currently invents them.
7. **References became tokens.** Sending them through desks would have failed an existing `verify.py` rule, rightly.
8. **Group limit stated.** "Events" in the brief runs into the per-person process model.

## 15. Validation before any broader expansion

**Demand isn't established.** The evidence is college pages showing the Google Form, screenshot and UTR routine, and vendor pages about personal accounts. Nobody we can name has told us they'd want this.

### Five interview questions, for Indian college treasurers and event organisers

1. Tell me about the last time you collected money for a group. Which account did it land in, and how did you track who had paid?
2. Walk me through what you do when someone sends you a payment screenshot. When has one turned out to be wrong?
3. Has anyone asked to see proof of where the money went? What did you show them, and how did you feel about showing it?
4. If a tool could check payments against your bank export on your side, and the group saw only who was confirmed, what would stop you trying it?
5. What's the most money and the most people you've handled this way, and what went wrong?

**Ask for stories, not opinions.** Don't describe Kitty until question 4.

### Assumptions to test

| # | Assumption | If false | Evidence today |
|---|---|---|---|
| 1 | Money runs through personal accounts | The privacy argument falls away | Vendor pages, not India-specific |
| 2 | Treasurers mind exposing their statements | Kitty is a nicer tracker at best | **None** |
| 3 | Checking screenshots is a real time cost | Weak pull | College pages show the routine, not the cost |
| 4 | Treasurers will export and load a statement, regularly | Manual confirm only, and no differentiation | **None.** Also unbuilt for real formats |
| 5 | Groups are 6–15 people | Big events need another architecture | **None** |
| 6 | Members accept the treasurer's word as final | Disputes need more than Kitty offers | **None** |
| 7 | A chat and app flow beats a Google Form plus a sheet | No reason to switch from free | **None** |
| 8 | People can reach this without Docker and Mattermost | No real users at all | **Known false today.** Telegram is the route to test, and there's no WhatsApp bridge in Switch's docs |
| 9 | Under-18 members can be excluded or handled | Legal problem for colleges. DPDP treats under-18s as children needing verifiable parental consent, and this needs a lawyer | Secondary sources |

**Rule:** no approval gate, receipts, or anything beyond this spec until assumptions 1, 2 and 4 have real answers.

## 16. Non-goals

Kitty won't, in this work:

- Move, hold or refund money, or process UPI.
- Verify anything against a bank, NPCI or any API. None is assumed.
- Collect PINs, OTPs or bank logins.
- Do receipt OCR, or read screenshots.
- Detect fraud automatically, or call anything fake.
- Approve expenses. That's a later phase, and this spec doesn't include it.
- Offer accounting, invoicing, GST features, budgets or reports.
- Manage documents, or keep a vault.
- Take real bank statements. Synthetic CSV only, as today.
- Change the treasurer mid-trip, or support events beyond 12 people.
- Claim compliance with any law, or privacy against the infrastructure host.
- Touch the shared-expense flow.

## 17. Acceptance criteria

**Compatibility, all must pass or nothing merges:**

- A1. 184 existing tests pass, unmodified.
- A2. Golden Mode A transcript, captured at `4ba9d82`, is byte-identical.
- A3. Live: three people `verify` 37/37, six people 61/61, dollars 28/28, with real-model rehearsals, same balances.
- A4. Zero diff on the seven files listed in §9.

**Pot behaviour:**

- B1. A contribution moves balances only when `confirmed`, and only through transitions 6, 7 or 14, from the treasurer's human account on the treasurer's desk.
- B2. Member, member's agent and treasurer's agent all fail to confirm, live, with fixed codes.
- B3. A claim with a reference, a claim without one, and a typed claim from a member with no statement each reach `confirmed`.
- B4. A reversed credit never appears as a candidate, and one credit never satisfies two claims, including with the agent's memory wiped.
- B5. `none` puts nothing in the group room.
- B6. A confirmed contribution corrected out, then back in, leaves both earlier decisions visible, with balances right each time and no other record changed.
- B7. Stopping every process mid-collection and restarting gives the same state, with the original timestamps.
- B8. A six-person Pot trip, driven through the app's real routes, matches numbers worked by hand **before** any code runs.
- B9. A hub plan is shown, every payment in it involves the treasurer, and paying it reaches "Everyone's square".

**Privacy, each with a positive control:**

- C1. Treasurer's salary, rent, merchant, unrelated counterparty and balance canaries are absent from the group room and every desk. Planting one makes `verify` fail.
- C2. No full reference is in any room. Planting one makes `verify` fail.
- C3. A recording brain sees no credit, no line from before the trip, and no model call at all during collection.
- C4. `clerk.json` holds no `pot_key`.
- C5. The live 403s still hold: the treasurer on another member's private room, the clerk on all private rooms.
- C6. The chain breaks when one room line is altered in a test.
- C7. The organiser's page states the host limit in plain words.

**Honesty:**

- D1. README and `PRIVACY.md` are updated from real results, with limits stated.
- D2. Nothing in the UI uses the banned words in §12.

## 18. Tests to be written

Written first, against the spec. All run with no Switch and no model except the last group.

| File | Covers |
|---|---|
| `tests/test_mode_a_golden.py` | **First commit, before any other change.** Full three-person and six-person post transcripts and prompts, frozen at `4ba9d82` |
| `tests/test_pot_claims.py` | Exact keys, closed lists, rejection never echoes text, token format, `ref_last4` format, dates against the collection window, `use_contribution` |
| `tests/test_pot_state.py` | Every transition in §5, every refused one by table, actor checks, retries, confirm crossing with withdraw |
| `tests/test_pot_duplicates.py` | The four duplicate rules, including clerk-side refusal with a wiped agent |
| `tests/test_pot_candidates.py` | `exact`, `several`, `amount_differs`, `none`, reversed before and after confirm, used rows, stale statement, parent's account |
| `tests/test_pot_ledger.py` | Hypothesis: any decision sequence keeps sum zero, the pot identity, and hub clearing. The worked example here, by exact paise. Correction after settlement |
| `tests/test_pot_chain.py` | Chain computed, verified from text, a tampered line caught, gaps caught |
| `tests/test_pot_replay.py` | Every state-changing message survives replay, with the original time. A test enumerating each command against `should_log` |
| `tests/test_pot_privacy.py` | Group posts match templates only. Recording brain sees no credits. Clerk config has no key. Model called only on the explicit step |
| `tests/test_pot_agent.py` | Member with and without statement. Treasurer's agent never posts narration. Private chits hold no unrelated text |
| `tests/test_pot_app.py` | Routes, Host and Origin checks, candidate list stays local, treasurer locked after opening, forbidden words absent from pages |
| Live: `scripts/pot_trip.py`, `scripts/verify.py` | Six-person synthetic story with hand-worked numbers, restart run, negative controls. Rules brain first, so it costs no model usage, then one real-model run |

**Every negative test needs a positive control,** as the handoff requires. Empty results misled this project three times.

## 19. Technical feasibility: an honest view

**Verdict: feasible, and most of the risk is in the live run, not the logic.**

### Evidence for

- The ledger needs no change. Checked on 20,000 random histories.
- The clerk is already a pure state machine that replays from a log, and `test_core.py` and `test_scenario.py` exercise it with no Switch running.
- Matching needs no model, so a usage limit can't stop the core, and the rules brain can run it all.
- Person apps, invites, restarts, and the chit flow already exist.
- Build pace is measurable here: `5f7e791` landed 2,788 inserted lines 31 minutes after the previous commit, and `e9c88c3` landed 723 after 21 more.

### Evidence against

- Trouble while running live is what took the time last sprint: a usage-limit stop from 01:30 to 02:41, and our own outage from 03:03 to 03:41.
- **We've never run a statement that grows mid-collection through this flow.** The merge rule supports it, but not in this sequence.
- The person app parses clerk text by regex. New templates mean new regexes on both sides, a known source of bugs (`verify.py` already failed 58 of 60 once on one missed pattern).
- `clerk_daemon` has no outbox. A failed post today is logged and lost.
- Recipient-side reversal format is our invention. Real banks will differ.

### Real-world readiness, separately

**This builds a better demonstration of a workable idea, not something Indian students can use.** It needs Docker and a laptop. It takes only synthetic CSVs. It's bound to Mattermost today. It holds at most 12 people. Getting to real users means a hosted server, Telegram or similar, a phone-friendly app, real statement formats, a privacy review, and legal advice. Each is a project in itself.

### Estimate

These are our guesses from the commit pace above, not measurements. Clock time, assuming the stack stays up.

| Slice | Contents | Time |
|---|---|---:|
| **Core** | §5 without `several` or `dispute`, so a parent's payment falls to manual confirm; `pot_match`, tokens, chain; `PotAgent`; clerk hooks; replay; minimal UI; `verify`; dataset; one rules run | 2–3 h |
| Core evidence | Re-run all regressions, real-model rehearsals, restart run, screenshots | 1 h, needs model usage |
| **Full** | `several` with local candidate review, `dispute`, crash resync, reversal-after-confirm prompt, polish | 1–2 h more |
| **Proof on request** | §10, after a 45-minute spike | 2–3 h |

My earlier estimate of 3–4 hours covered less than this spec does. It's still roughly right for Core, but the rest is on top.

### Build order and stop rules

1. New branch `feat/pot-trip` from `4ba9d82`. **`feat/kitty-mvp` stays the submittable version** until every gate passes.
2. Golden test first, committed before anything else.
3. Your notes from trying the app come before this.
4. **Gate 1:** pure tests green. **Gate 2:** one live six-person run through `verify`. **Gate 3:** all regressions green.
5. **Go or no-go at 12:00 IST.** If Gate 2 hasn't passed, stop. Merge nothing, and submit the current Kitty with this spec as the stated roadmap.
6. Nothing new after the 15:00 freeze. No video until you say.
