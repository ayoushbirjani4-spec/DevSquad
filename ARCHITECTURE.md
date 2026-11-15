# DevSquad v1 — Architecture

A Claude Code skill that fixes bugs with a small team of specialist subagents,
where every "it works" claim is backed by a command that was actually run.

Verified against the Claude Code docs (skills, sub-agents, hooks, agent-teams)
on 2026-11-15, Claude Code 2.1.323.

---

## 1. Scope

v1 does one job: **debug and fix a reported bug in an existing repository.**

| In v1 | Deferred |
|---|---|
| Reproduce → fix → review → verify loop | Feature development workflow |
| 3 specialists: Reproducer, Fixer, Reviewer | Security, Documentation, Architect agents |
| Hook-enforced per-agent permissions | Agent Teams / parallel hypotheses |
| Evidence files from real command runs | Datasets, retrieval, ChromaDB, SQLite |
| Human approval before commit | Anything that pushes, merges, or deploys |

Why three agents, not six: debugging is sequential (you can't fix before you
reproduce, or review before you fix), so more agents add cost, not speed.
The orchestrator does discovery itself; an Architect subagent would only
re-read the same files at extra cost.

## 2. Components

```
User ──/devsquad <bug>──▶ Orchestrator (main session, runs SKILL.md)
                              │  owns: run state, gates, final verification
             ┌────────────────┼────────────────┐
             ▼                ▼                ▼
      ds-reproducer       ds-fixer        ds-reviewer
      writes failing      edits source    reads diff +
      test, proves it     (not tests),    evidence, gives
      fails               makes it pass   verdict
             │                │                │
             └──── reports/*.json  +  evidence/*.json ────┘
                              │
                  guard.py (PreToolUse hook) enforces who may touch what
                  ds.py (CLI) runs checks, records evidence, validates, gates
```

| Component | File | Role |
|---|---|---|
| Skill | `skill/devsquad/SKILL.md` | Orchestrator instructions. `disable-model-invocation: true`: runs only on `/devsquad` so it never fires on ordinary coding requests. |
| Workflow | `skill/devsquad/workflows/bug-fix.md` | Step-by-step phases and gates. |
| Protocols | `skill/devsquad/protocols/*.md` | Report schema, evidence rules, quality gates. |
| Agents | `agents/ds-*.md` | Specialist prompts, `tools:` allowlists, models, guard hook. |
| Guard | `skill/devsquad/scripts/guard.py` | PreToolUse hook. Reads `agent_type`, allows/denies file and shell access per role. |
| CLI | `skill/devsquad/scripts/ds.py` | `init`, `check`, `diff`, `validate`, `gate`. Stdlib Python only. |
| Installer | `install.py` | Copies skill + agents into `~/.claude` (user) or a repo's `.claude` (project), fills in absolute paths, and registers the guard in that scope's `settings.json`. |

The orchestrator is the **main session**, not a forked subagent, because it
must talk to the user (clarifications, approval) and spawn the specialists.

## 3. Roles and enforced permissions

Permissions are enforced in two layers. `tools:` removes tools entirely;
`guard.py` restricts what the remaining tools may touch. Prompt text is the
third, weakest layer and is never relied on alone.

| Agent | Model | `tools:` | Guard: may write | Guard: may not |
|---|---|---|---|---|
| ds-reproducer | sonnet | Read, Grep, Glob, Bash, Edit, Write | test files, its report | source files |
| ds-fixer | inherit | Read, Grep, Glob, Bash, Edit, Write | source files, its report | test files (can't weaken the repro test) |
| ds-reviewer | sonnet | Read, Grep, Glob, Write | its report only | everything else; has no Bash |
| orchestrator | session | all | everything | destructive git → **ask** the user |

Rules for every `ds-*` agent, enforced by `guard.py`:
- No writes outside the repo, or to `.git/`, `.claude/`, `.devsquad/` (except
  its own reports folder). The evidence folder is written only by `ds.py`.
- No reading secrets: `.env`, `.env.*` (except `.env.example`), `*.pem`,
  `*.key`, `id_rsa*`.
- Shell: denies `git push/commit/reset --hard/clean/checkout --/restore/stash/
  rebase/merge`, `rm -r`, package installs, `curl|sh` pipes, and shell
  writes to paths the agent may not edit.
- No `Agent` tool in any allowlist, so specialists cannot spawn subagents
  (nesting is otherwise allowed by default, up to 3 levels).

The shell checks are **best effort**: a determined agent can write files in
ways a regex won't catch. That's acceptable because the authoritative check
(gate V, below) is run by the orchestrator itself and compares the real
`git diff`, not agent claims.

Test files are matched by default globs (`tests/**`, `test/**`, `__tests__/**`,
`spec/**`, `test_*.py`, `*_test.py`, `*_test.go`, `*.test.*`, `*.spec.*`,
`conftest.py`, `src/test/**`), overridable in `.devsquad/config.json`.

## 4. Run state

Lives in the **target repo**, hidden from git via `.git/info/exclude`
(the user's `.gitignore` is never modified):

```
.devsquad/
├── config.json            optional overrides (test globs, max attempts)
├── current                id of the active run
└── runs/<run_id>/
    ├── run.json           bug text, baseline HEAD, baseline dirty files, test command
    ├── evidence/NNN-<label>.json   one per command, written only by ds.py
    ├── reports/<agent>-<attempt>.json
    ├── diff.patch         written by ds.py diff
    └── final_report.md    written by ds.py gate
```

One file per report and per evidence record, so nothing is ever written by
two agents at once.

## 5. Evidence and reports

**Evidence** (`ds.py check --label L -- <command>`): runs the command,
records `command, exit_code, duration, stdout/stderr tails, git_head,
timestamp, agent`. Agents must run tests through `ds.py check` and cite the
evidence file in their report.

**Report** (every agent writes one, schema `devsquad.report/v1`):

```json
{
  "schema": "devsquad.report/v1",
  "run_id": "20261115-143000",
  "agent": "ds-fixer",
  "attempt": 1,
  "status": "completed | failed | blocked",
  "summary": "one paragraph",
  "files_changed": ["src/calc.py"],
  "evidence": ["evidence/004-repro-after-fix.json"],
  "blockers": [],
  "details": { "...role-specific..." }
}
```

Role-specific `details`:
- reproducer: `repro_tests[]`, `failure_signature`, `reproduced` (bool)
- fixer: `root_cause`, `fix_description`
- reviewer: `verdict` (`approve | request_changes`), `findings[]` of
  `{severity: blocking|major|minor, file, line, issue, suggestion}`

`ds.py validate` rejects a report when: fields are missing or mistyped; a cited
evidence file doesn't exist; the reproducer claims `reproduced: true` without
evidence that exited non-zero; the fixer claims `completed` without evidence
that exited zero; `files_changed` lists a file `git` doesn't show as changed.

## 6. Workflow and gates

| Phase | Who | Gate (checked by orchestrator with `ds.py`, never on an agent's word) |
|---|---|---|
| 0 Intake | orchestrator | Bug text present (else ask). `ds.py init` records baseline HEAD + dirty files. |
| 1 Discovery | orchestrator | Test command found; **baseline** suite run recorded, so failures that existed before are never blamed on the fix. |
| 2 Reproduce | ds-reproducer | **Gate R**: report valid, repro test fails with the reported symptom. Can't reproduce → stop and ask the user. No blind fixes. |
| 3 Fix | ds-fixer | **Gate F**: orchestrator re-runs repro test (must pass) and full suite (no new failures vs baseline). |
| 4 Review | ds-reviewer | **Gate C**: verdict `approve`, no `blocking` findings. |
| 5 Correct | ds-fixer | Any failed gate → back to the fixer with the exact failure. Max **3** fixer attempts total, then report honestly. |
| 6 Deliver | orchestrator | **Gate V**: `ds.py gate` re-checks everything, writes `final_report.md`. User approves before any commit. Nothing is ever pushed. |

Pre-existing user changes: baseline dirty files are recorded at init. If the
fix touches one of them, the gate flags it for the user instead of passing.

## 7. Cost control (Claude Pro)

- Only runs on explicit `/devsquad`.
- Sequential subagents, one at a time; no Agent Teams in v1.
- Reproducer and reviewer on `sonnet`; the fixer inherits the session model.
- Reviewer receives the diff file and evidence, not the whole repo.
- `maxTurns` on each agent, plus the 3-attempt cap.

## 8. Platform notes

- Hook commands use **exec form** (`command` = absolute `python.exe`,
  `args` = [absolute `guard.py`]): no shell involved, so Windows paths with
  spaces work. `install.py` fills these in.
- `${CLAUDE_SKILL_DIR}` is substituted into SKILL.md, so the orchestrator calls
  `ds.py` without hardcoded paths.
- **The guard lives in `settings.json`, not in agent frontmatter.** In a live
  test, hooks in agent frontmatter never fired (project agent hooks need
  workspace trust), and skill-frontmatter hooks fired only in the main
  session. Settings hooks run inside subagents too, which the follow-up probe
  confirmed: `agent_type: ds-reviewer` was seen and its write denied.
  `install.py` merges one entry into `settings.json` (backup kept) and
  replaces it on reinstall.
- Because it's a settings hook it sees every session, so it stays silent
  unless the caller is a `ds-*` agent or the repo has an active run
  (`.devsquad/current`).
- `DEVSQUAD_GUARD_LOG=<file>` logs every guard decision, for debugging.

## 9. Roadmap

1. **v1 (this build)**: bug-fix workflow, 3 agents, guard, evidence, gates.
2. **Evaluation**: `evaluation/bugs/*` — small buggy repos, each with the bug
   text and a hidden verification test. Score: reproduced / fixed / no
   regressions / attempts / tokens. Start with 10 bugs (own repos + BugsInPy).
3. **v2**: ds-security (Semgrep/Bandit/Gitleaks via `ds.py check`) as an extra
   gate; competing hypotheses: 2 fixers with `isolation: worktree`, pick the
   one that passes with the smaller diff.
4. **v3**: feature workflow (Architect, Documentation), Agent Teams where work
   truly runs in parallel.

Datasets enter only as **evaluation** material. Pretraining corpora
(The Stack, StarCoderData) are out: Claude can't learn from them at runtime.
