# Briefs, reports and evidence

## Brief (orchestrator → specialist)

Every Agent call's prompt uses this shape. Specialists start with no context
except this brief, so include everything they need.

```
DEVSQUAD BRIEF
run_id: <run_id>
run_dir: <absolute RUN path>
attempt: <n>
role: ds-reproducer | ds-fixer | ds-reviewer

bug:
<bug text exactly as the user gave it>

test_command: <full suite command>
framework: <e.g. pytest, jest, go test>
focus_files: <files/functions to start from>
repro_test: <path and single-test command>     (fixer, reviewer)
evidence: <evidence files to read>             (fixer, reviewer)
do_not_touch: <baseline_dirty files>           (fixer)
address: <exact failures or findings to fix>   (fixer retries)
diff: <run_dir>/diff.patch                     (reviewer)

Write your report to: <run_dir>/reports/<role>-<attempt>.json
```

## Evidence

Created only by `DS check --label <label> --agent <role> -- <command>`.
Stored as `RUN/evidence/NNN-<label>.json`:

```json
{
  "id": "003-repro-fails",
  "label": "repro-fails",
  "agent": "ds-reproducer",
  "command": "python -m pytest -q tests/test_calc.py::test_divide_neg",
  "exit_code": 1,
  "duration_s": 0.84,
  "stdout_tail": "...last 4000 chars...",
  "stderr_tail": "...",
  "git_head": "1b4c089...",
  "state_hash": "sha256 of the working-tree changes when it ran",
  "timestamp": "2026-11-15T14:31:02Z"
}
```

`state_hash` ties evidence to a code state. The final gate only accepts verify
evidence whose `state_hash` equals the current one, so evidence from before a
later edit doesn't count.

## Report (specialist → orchestrator)

File: `RUN/reports/<agent>-<attempt>.json`. Cite evidence by path relative
to RUN (e.g. `evidence/003-repro-fails.json`).

```json
{
  "schema": "devsquad.report/v1",
  "run_id": "...",
  "agent": "ds-reproducer | ds-fixer | ds-reviewer",
  "attempt": 1,
  "status": "completed | failed | blocked",
  "summary": "what you did and found, plain language",
  "files_changed": ["relative/path.py"],
  "evidence": ["evidence/003-repro-fails.json"],
  "blockers": ["what stopped you, if status is blocked"],
  "details": {}
}
```

`details` by role:

| Role | Required keys |
|---|---|
| ds-reproducer | `reproduced` (bool), `repro_tests` (list of `{path, command}`), `failure_signature` (the assertion/error that proves the bug) |
| ds-fixer | `root_cause`, `fix_description` |
| ds-reviewer | `verdict` (`approve` or `request_changes`), `findings` (list of `{severity, file, line, issue, suggestion}`; severity is `blocking`, `major` or `minor`) |

## What `DS validate` checks

- JSON parses; all fields present with the right types; `agent` and `attempt`
  match the file name; `run_id` matches the active run.
- Every cited evidence file exists.
- Reproducer with `reproduced: true` cites at least one evidence file with a
  non-zero exit code.
- Fixer with `status: completed` cites at least one evidence file with exit 0.
- Every `files_changed` entry is actually changed according to git.
- Reviewer `verdict: approve` has no `blocking` findings.

The final message each specialist returns to you should be one line: the
report path and status. All substance lives in the report file.
