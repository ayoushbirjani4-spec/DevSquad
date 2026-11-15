# DevSquad

A Claude Code skill that fixes bugs with three specialist subagents
(reproducer, fixer, reviewer). Each step has to be backed by a recorded command
run, and nothing gets committed until you approve. Runs on a Claude Pro
subscription, with no API key. Design: [ARCHITECTURE.md](ARCHITECTURE.md).

## Install

Needs Python 3.9+ and git.

```bash
python install.py                     # for all your projects (~/.claude)
python install.py --project ../myapp  # for one repo (myapp/.claude)
```

The installer also adds one PreToolUse hook (the guard) to that scope's
`settings.json`, keeping a `.devsquad-bak` backup. The guard does nothing
outside DevSquad runs.

Then open a new Claude Code session in the repo and run:

```
/devsquad average([]) crashes with ZeroDivisionError
```

## What happens

1. The orchestrator finds the test command and records a baseline run.
2. **ds-reproducer** writes a failing test that shows the bug.
3. **ds-fixer** fixes the source until that test passes. A hook blocks it
   from editing tests.
4. The orchestrator re-runs the tests itself. **ds-reviewer** reviews the diff.
5. Failed checks go back to the fixer, up to 3 attempts.
6. `ds.py gate` writes `.devsquad/runs/<id>/final_report.md` (PASS / WARN / FAIL)
   and you decide whether to commit. Nothing is ever pushed.

## Layout

```
skill/devsquad/SKILL.md          orchestrator (runs only on /devsquad)
skill/devsquad/workflows/        phase-by-phase procedure
skill/devsquad/protocols/        report schema, evidence, quality gates
skill/devsquad/scripts/ds.py     run state, evidence, validation, gate
skill/devsquad/scripts/guard.py  PreToolUse hook enforcing per-agent permissions
agents/ds-*.md                   the three specialists
examples/buggy-calc/             tiny repo with one known bug
tests/                           python -m unittest discover -s tests
```

## Config (optional)

`.devsquad/config.json` in the target repo:

```json
{ "test_dirs": ["tests", "spec"], "test_files": ["test_*.py", "*.test.ts"], "max_attempts": 3 }
```
