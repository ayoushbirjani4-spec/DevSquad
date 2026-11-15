# Bug-fix workflow

`DS` = the ds.py command from SKILL.md. `RUN` = the run directory printed by
`DS init`. Keep one short status line per phase for the user.

## Phase 0 — Intake

1. Make sure the bug text says what goes wrong (symptom, error, or wrong
   output). If not, ask the user one question and wait.
2. Run `DS init --bug "<bug text>"`. It prints `run_id` and `RUN`, records the
   baseline commit and the files the user had already changed.
3. If it reports the folder isn't a git repository, stop and tell the user:
   DevSquad needs git to track and review changes.

## Phase 1 — Discovery (you, no subagent)

1. Find the language, test framework and test command from project files
   (`pyproject.toml`, `package.json`, `pom.xml`, `go.mod`, `Makefile`, CI config,
   README). Don't guess: if none is found, ask the user for the test command.
2. Record it: `DS set --test-cmd "<command>"`.
3. Run the baseline suite: `DS check --label baseline --agent orchestrator -- <test command>`.
   A non-zero exit here means failures existed before the run. Note them; they
   must not be blamed on the fix.
4. Read just enough code to point the reproducer at the likely area: the
   files and functions named in the error or bug text.

## Phase 2 — Reproduce

1. Spawn `ds-reproducer` with a brief (format in `protocols/reports.md`):
   bug text, run id, RUN, test command, test framework, suspected files.
2. When it returns, run `DS validate RUN/reports/ds-reproducer-1.json`.
3. **Gate R** (see `quality-gates.md`). Re-run the repro test yourself:
   `DS check --label repro-confirm --agent orchestrator -- <repro test command>`.
   It must fail, and the failure must match the reported symptom.
4. Not reproduced → tell the user what was tried, ask for more detail, stop.
   Validation errors → send them back to the reproducer once (`SendMessage`
   to its agent id), then stop if still invalid.

## Phase 3 — Fix (attempt n, starting at 1)

1. Spawn `ds-fixer` with: bug text, run id, RUN, attempt n, repro test path and
   command, the repro evidence file, the files in `baseline_dirty` it must
   not touch, and on retries the exact failure or review findings to address.
2. Run `DS validate RUN/reports/ds-fixer-<n>.json`.
3. **Gate F**, yourself, after the fixer returns:
   - `DS check --label verify-repro --agent orchestrator -- <repro test command>` → must exit 0.
   - `DS check --label verify-suite --agent orchestrator -- <test command>` → must exit 0,
     or, if the baseline failed, must fail no tests that passed in the baseline
     (compare the outputs; say which you checked).
4. Fail → Phase 5.

## Phase 4 — Review

1. `DS diff` writes `RUN/diff.patch`.
2. Spawn `ds-reviewer` with: bug text, run id, RUN, attempt n, path to
   `diff.patch`, the reproducer and fixer reports, the verify evidence files.
3. `DS validate RUN/reports/ds-reviewer-<n>.json`.
4. **Gate C**: verdict `approve` and no `blocking` findings. `major` findings:
   use judgement; fix them if cheap, otherwise list them in the final report.

## Phase 5 — Correct

If gate F or C failed and n < 3: n = n + 1, go to Phase 3 with the specific
failure (evidence tail or review findings). If n = 3: go to Phase 6 anyway and
report the gate as FAIL.

## Phase 6 — Deliver

1. If code changed since the last `DS diff`, run `DS diff` again, then run
   `verify-repro` and `verify-suite` again: the gate checks that verify
   evidence matches the current code.
2. `DS gate` writes `RUN/final_report.md` and prints PASS, WARN or FAIL.
3. Show the user: gate result, root cause, files changed, test evidence,
   review findings, anything unresolved. Quote the gate result exactly.
4. Ask whether to commit. Only on a clear yes: stage the changed files by name
   (never `git add -A`, never `.devsquad/`) and commit. Never push.
