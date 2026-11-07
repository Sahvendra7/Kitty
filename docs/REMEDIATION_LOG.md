# Remediation log

Fixes from the review in `docs/CODE_REVIEW_REPORT.md`, on `fix/kitty-release-hardening`. Each regression test failed before its fix, except where noted. The full suite was run after each one.

| ID | Severity | Root cause | Fix | Tests | Commit |
|---|---|---|---|---|---|
| F1 | P1 | `Clerk._release_answer` never checked the transfer's status, and `_withdraw` left a held answer in place. A withdrawn transfer could be confirmed. | Releasing an answer needs `awaiting_receiver`. Withdrawal clears a held answer. | `test_f1_*` (4), `test_transfer_transitions_are_listed_or_refused` (42) | `f933a48` |
| F2 | P1 | `_find_conflict` matched only *released* copies, so a third owner's copy skipped a held conflict. `_resolve_conflict` assumed exactly two copies. | Held conflicts count as matches. A conflict clears only when one copy is left. Otherwise a "Conflict narrowed" post. | `test_f2_*` (3) | `f933a48` |
| F3 | P2 | The daemon logged, applied, posted, then saved the cursor. A crash in between led to redelivery and a second apply, which matters for `call`. | `message_id` is kept in the log line, and ids already in the log are skipped. The log is fsynced before it is applied. Posts are retried only on connect errors. | `test_f3_*` (3) | `e19c3a3` |
| F4 | P1 | On trips with no treasurer, `person_app.feed()` read group posts by text alone, and the person configs didn't name the clerk. | `clerk_bot` is in every person config, from both `provision.py` and `setup_trip.py`. The feed trusts only that sender's posts. The parity assertion in `test_provision.py` was extended to expect it. | `test_f4_*` (6) | `3a2aafd` |
| F5 | P1 | `subprocess.TimeoutExpired` carries the argv, prompt included, and reached `log.exception`. | `llm.complete` turns a timeout or `OSError` into `LlmError` with no chained cause. | `test_f5_*` (2). They were committed with F4 in `3a2aafd`, and pass from `a22fc5b`. | `a22fc5b` |
| F6 | P2 | The Pot forms cleared before the send result. `say()` didn't catch errors. The 900 ms poll erased the error. | `say()` returns ok. Forms clear only on success. The send error stays until a send works. Also: confirm before deleting a trip, a guard on the add-person form, `CSS.escape` in the focus selector. | New failure-path interaction in `scripts/ui_check.py`. It failed twice first: the second failure found the poll erasing the error. | `449272e` |
| F14 | P3 | `setup_trip.write_private` wrote and then chmodded. | It uses `kitty.trips.write_json`, which is atomic and mode 600 from creation. | Checked by hand: mode `0o600`, and the content round-trips. No unit test, since the script needs the live stack. | `5d95762` |

## Deferred

F7 to F13, and the P3 list, are in the report with the recommended fix for each. Nothing in that list was partly changed.

## Verification after the last fix

- `uv run pytest`: 736 passed.
- `scripts/ui_check.py --axe`: 0 problems.
- `pip-audit`: no known vulnerabilities, with a positive control.

Not run: the live Switch and Mattermost suites, and any real-model run. See the report, section 10.
