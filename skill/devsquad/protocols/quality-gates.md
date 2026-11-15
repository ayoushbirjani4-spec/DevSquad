# Quality gates

A gate is judged by the orchestrator from `DS` output it produced itself. No
numeric score can override a failed gate.

| Gate | Passes when | On failure |
|---|---|---|
| **R** Reproduced | Reproducer report valid, `reproduced: true`; your own `repro-confirm` run exits non-zero; its output shows the reported symptom (same error type, message, or wrong value), not an unrelated error such as an import error or a typo in the test | Ask the user for more detail; stop |
| **F** Fixed | Your own `verify-repro` exits 0; your own `verify-suite` exits 0, or the baseline also failed and no test that passed in the baseline fails now | Back to fixer (Phase 5) |
| **C** Reviewed | Reviewer report valid, verdict `approve`, zero `blocking` findings | Back to fixer with the findings |
| **V** Verified | `DS gate` prints PASS | Report WARN/FAIL honestly; still show the user |

## What `DS gate` checks

- Reproducer: a valid report with `reproduced: true` exists.
- Fixer: latest report `completed`; attempts ≤ 3.
- Verify evidence: latest `verify-repro` exit 0 and latest `verify-suite`
  exit 0 (or the baseline failed too → WARN, needs your comparison), and both
  have the **current** `state_hash`.
- Review: latest reviewer report approves, has no blocking findings, and
  `diff.patch` matches the current `state_hash` (the reviewer saw this code).
- The repro test files exist and are unchanged since the reproducer's
  evidence (the fixer didn't edit them).
- No overlap between changed files and `baseline_dirty`, else WARN.

PASS = every check passed. WARN = nothing failed but a human should look
(baseline already failing, touched a pre-existing user change). FAIL =
anything else.

## Severity guide for reviewers

- **blocking**: the fix is wrong, incomplete for the reported bug, breaks other
  behaviour, hides the error instead of fixing it (swallowed exception, special
  case for the test input), or introduces a security problem.
- **major**: real problem, but the bug is fixed: missing edge case, poor error
  handling, misleading name.
- **minor**: style and readability.
