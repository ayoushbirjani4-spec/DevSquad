"""DevSquad run-state CLI. Standard library only.

  ds.py init --bug TEXT          start a run in the current git repo
  ds.py set --test-cmd CMD       record the project's test command
  ds.py check --label L --agent A -- CMD...   run CMD, record evidence
  ds.py diff                     write diff.patch for the reviewer
  ds.py validate REPORT.json     check a specialist report
  ds.py gate                     final gate, writes final_report.md
  ds.py end                      close the active run (keeps its files)
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as c  # noqa: E402

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
TAIL = 4000
ROLES = ("ds-reproducer", "ds-fixer", "ds-reviewer")
SCHEMA = "devsquad.report/v1"


def die(msg, code=2):
    print(f"ds: {msg}", file=sys.stderr)
    sys.exit(code)


def now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def git(root, *args, check=True):
    r = subprocess.run(["git", "-C", root, *args], capture_output=True)
    if check and r.returncode != 0:
        die(f"git {' '.join(args)} failed: {r.stderr.decode(errors='replace').strip()}")
    return r.stdout


def write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def sha_file(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


# ---------------------------------------------------------------- run context

class Run:
    def __init__(self, need_run=True):
        self.root = c.find_root(os.getcwd())
        if not self.root:
            die("not inside a git repository. DevSquad needs git to track changes.")
        self.id, self.dir = c.current_run(self.root)
        if need_run and not self.dir:
            die("no active run. Start one with: ds.py init --bug \"...\"")
        self.cfg = c.load_config(self.root)

    @property
    def meta_path(self):
        return os.path.join(self.dir, "run.json")

    def meta(self):
        return c.load_json(self.meta_path, {})

    def save_meta(self, m):
        write_json(self.meta_path, m)

    def base(self):
        return self.meta().get("baseline_head") or EMPTY_TREE

    def untracked(self):
        out = git(self.root, "ls-files", "--others", "--exclude-standard", "-z")
        return sorted(p for p in out.decode(errors="replace").split("\0")
                      if p and not c.is_artifact(p))

    def changed_files(self):
        out = git(self.root, "diff", "--name-only", "-z", self.base())
        tracked = [p for p in out.decode(errors="replace").split("\0") if p]
        return sorted(set(tracked) | set(self.untracked()))

    def state_hash(self):
        h = hashlib.sha256(git(self.root, "diff", "--binary", self.base()))
        for p in self.untracked():
            h.update(b"\0" + p.encode() + b"\0")
            h.update((sha_file(os.path.join(self.root, p)) or "missing").encode())
        return h.hexdigest()

    def head(self):
        r = subprocess.run(["git", "-C", self.root, "rev-parse", "HEAD"], capture_output=True)
        return r.stdout.decode().strip() if r.returncode == 0 else None

    def evidence(self):
        """All evidence records, oldest first."""
        d = os.path.join(self.dir, "evidence")
        recs = []
        for name in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            if name.endswith(".json"):
                rec = c.load_json(os.path.join(d, name))
                if rec:
                    recs.append(rec)
        return recs

    def latest_evidence(self, label):
        recs = [r for r in self.evidence() if r.get("label") == label]
        return recs[-1] if recs else None

    def reports(self, agent):
        """{attempt: report} for one agent."""
        d = os.path.join(self.dir, "reports")
        out = {}
        for name in os.listdir(d) if os.path.isdir(d) else []:
            m = re.fullmatch(re.escape(agent) + r"-(\d+)\.json", name)
            if m:
                out[int(m.group(1))] = c.load_json(os.path.join(d, name))
        return out


# ------------------------------------------------------------------- commands

def cmd_init(a):
    run = Run(need_run=False)
    if not a.bug.strip():
        die("--bug is empty")
    run_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = os.path.join(run.root, c.STATE_DIR, "runs", run_id)
    if os.path.exists(run_dir):
        die(f"run {run_id} already exists; wait a second and retry")
    for sub in ("evidence", "reports"):
        os.makedirs(os.path.join(run_dir, sub))

    # hide .devsquad from git without touching the user's .gitignore
    excl = os.path.join(run.root, ".git", "info", "exclude")
    if os.path.isdir(os.path.join(run.root, ".git")):
        os.makedirs(os.path.dirname(excl), exist_ok=True)
        existing = open(excl, encoding="utf-8").read() if os.path.exists(excl) else ""
        if f"/{c.STATE_DIR}/" not in existing.split():
            with open(excl, "a", encoding="utf-8") as f:
                f.write(("" if existing.endswith("\n") or not existing else "\n") + f"/{c.STATE_DIR}/\n")

    status = git(run.root, "status", "--porcelain", "-z", "--untracked-files=all").decode(errors="replace")
    dirty, entries = [], status.split("\0")
    i = 0
    while i < len(entries):
        e = entries[i]
        if len(e) > 3:
            dirty.append(e[3:])
            if e[0] in "RC":  # rename/copy: next entry is the source path
                i += 1
        i += 1
    dirty = sorted(p for p in dirty if not c.is_artifact(p))

    if run.id:
        print(f"note: run {run.id} was still active; it is replaced by the new run")
    head = run.head()
    meta = {
        "run_id": run_id, "created": now(), "bug": a.bug,
        "baseline_head": head,
        "baseline_dirty": dirty,
        "baseline_dirty_hashes": {p: sha_file(os.path.join(run.root, p)) for p in dirty},
        "test_cmd": None, "repro_hashes": {},
    }
    write_json(os.path.join(run_dir, "run.json"), meta)
    with open(os.path.join(run.root, c.STATE_DIR, "current"), "w", encoding="utf-8") as f:
        f.write(run_id)
    print(f"run_id: {run_id}")
    print(f"run_dir: {run_dir}")
    print(f"baseline_head: {head or '(no commits yet)'}")
    print(f"baseline_dirty: {', '.join(dirty) if dirty else '(none)'}")


def cmd_set(a):
    run = Run()
    m = run.meta()
    m["test_cmd"] = a.test_cmd
    run.save_meta(m)
    print(f"test_cmd: {a.test_cmd}")


def cmd_check(a):
    run = Run()
    argv = a.cmd[1:] if a.cmd and a.cmd[0] == "--" else a.cmd
    if not argv:
        die("nothing to run. Usage: ds.py check --label L --agent A -- <command>")
    label = re.sub(r"[^a-zA-Z0-9_.-]+", "-", a.label).strip("-")[:60] or "check"
    if len(argv) == 1:
        command, run_args, shell = argv[0], argv[0], True
    else:
        exe = shutil.which(argv[0]) or argv[0]
        command, run_args, shell = subprocess.list2cmdline(argv), [exe, *argv[1:]], False

    t0 = time.time()
    try:
        p = subprocess.run(run_args, shell=shell, cwd=os.getcwd(), capture_output=True,
                           timeout=a.timeout)
        code, out, err = p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as e:
        code, out, err = 124, e.stdout or b"", (e.stderr or b"") + b"\n[ds: timed out]"
    except OSError as e:
        code, out, err = 127, b"", str(e).encode()
    out, err = out.decode(errors="replace"), err.decode(errors="replace")

    ev_dir = os.path.join(run.dir, "evidence")
    while True:  # exclusive create so parallel calls never clash
        n = len([f for f in os.listdir(ev_dir) if f.endswith(".json")]) + 1
        ev_id = f"{n:03d}-{label}"
        path = os.path.join(ev_dir, ev_id + ".json")
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            continue
    rec = {
        "id": ev_id, "label": label, "agent": a.agent, "command": command,
        "cwd": os.getcwd(), "exit_code": code, "duration_s": round(time.time() - t0, 2),
        "stdout_tail": out[-TAIL:], "stderr_tail": err[-TAIL:],
        "git_head": run.head(), "state_hash": run.state_hash(), "timestamp": now(),
    }
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=2)

    combined = (out + ("\n" + err if err.strip() else "")).rstrip()
    print(combined[-3000:])
    print(f"\n[ds] evidence/{ev_id}.json  exit_code={code}")
    sys.exit(code if 0 <= code < 256 else 1)


def cmd_diff(_a):
    run = Run()
    patch = git(run.root, "diff", "--binary", run.base())
    for p in run.untracked():
        r = subprocess.run(["git", "-C", run.root, "diff", "--binary", "--no-index", "--", "/dev/null", p],
                           capture_output=True)
        patch += r.stdout
    with open(os.path.join(run.dir, "diff.patch"), "wb") as f:
        f.write(patch)
    files = run.changed_files()
    write_json(os.path.join(run.dir, "diff.json"),
               {"state_hash": run.state_hash(), "files": files, "timestamp": now()})
    print(f"diff.patch: {len(patch)} bytes, {len(files)} file(s): {', '.join(files) or '(none)'}")


def validate_report(run, path):
    """List of problems with a report (empty = valid)."""
    errs = []
    rep = c.load_json(path)
    if not isinstance(rep, dict):
        return [f"{path}: not readable JSON"], None
    m = re.fullmatch(r"(ds-[a-z]+)-(\d+)\.json", os.path.basename(path))
    if not m or m.group(1) not in ROLES:
        errs.append("file name must be <agent>-<attempt>.json, e.g. ds-fixer-1.json")

    def need(key, typ, obj=rep, where=""):
        if not isinstance(obj.get(key), typ):
            errs.append(f"{where}{key}: missing or not {typ.__name__ if isinstance(typ, type) else typ}")
            return False
        return True

    need("schema", str) and rep["schema"] != SCHEMA and errs.append(f"schema must be {SCHEMA}")
    need("run_id", str) and rep["run_id"] != run.id and errs.append(f"run_id must be {run.id}")
    need("agent", str)
    need("attempt", int)
    if m and rep.get("agent") != m.group(1):
        errs.append("agent does not match file name")
    if m and rep.get("attempt") != int(m.group(2)):
        errs.append("attempt does not match file name")
    if need("status", str) and rep["status"] not in ("completed", "failed", "blocked"):
        errs.append("status must be completed, failed or blocked")
    need("summary", str)
    for k in ("files_changed", "evidence", "blockers"):
        need(k, list)
    if rep.get("status") == "blocked" and not rep.get("blockers"):
        errs.append("status blocked needs at least one blocker")
    need("details", dict)

    ev = []
    for e in rep.get("evidence") or []:
        p = e if os.path.isabs(e) else os.path.join(run.dir, e)
        rec = c.load_json(p)
        if rec is None:
            errs.append(f"evidence not found: {e}")
        else:
            ev.append(rec)

    changed = set(run.changed_files())
    for f in rep.get("files_changed") or []:
        if f.replace("\\", "/") not in changed:
            errs.append(f"files_changed lists {f}, but git shows no change to it")

    d, agent = rep.get("details") or {}, rep.get("agent")
    if agent == "ds-reproducer":
        if need("reproduced", bool, d, "details."):
            if d["reproduced"] and not any(r.get("exit_code") != 0 for r in ev):
                errs.append("reproduced=true but no cited evidence shows a failing run (non-zero exit)")
        if need("repro_tests", list, d, "details.") and d.get("reproduced"):
            if not d["repro_tests"]:
                errs.append("details.repro_tests is empty")
            for t in d["repro_tests"]:
                if not (isinstance(t, dict) and isinstance(t.get("path"), str) and isinstance(t.get("command"), str)):
                    errs.append("each repro_tests entry needs string path and command")
                elif not os.path.isfile(os.path.join(run.root, t["path"])):
                    errs.append(f"repro test file not found: {t['path']}")
        need("failure_signature", str, d, "details.")
    elif agent == "ds-fixer":
        need("root_cause", str, d, "details.")
        need("fix_description", str, d, "details.")
        if rep.get("status") == "completed" and not any(r.get("exit_code") == 0 for r in ev):
            errs.append("status completed but no cited evidence shows a passing run (exit 0)")
    elif agent == "ds-reviewer":
        if need("verdict", str, d, "details.") and d["verdict"] not in ("approve", "request_changes"):
            errs.append("details.verdict must be approve or request_changes")
        if need("findings", list, d, "details."):
            for i, f in enumerate(d["findings"]):
                if not isinstance(f, dict) or f.get("severity") not in ("blocking", "major", "minor"):
                    errs.append(f"findings[{i}].severity must be blocking, major or minor")
                elif not all(isinstance(f.get(k), str) for k in ("file", "issue")):
                    errs.append(f"findings[{i}] needs string file and issue")
            if d.get("verdict") == "approve" and any(
                    isinstance(f, dict) and f.get("severity") == "blocking" for f in d["findings"]):
                errs.append("verdict approve with blocking findings")
    return errs, rep


def cmd_validate(a):
    run = Run()
    errs, rep = validate_report(run, a.report)
    if errs:
        print("INVALID report " + a.report)
        for e in errs:
            print(f"  - {e}")
        sys.exit(1)
    if rep["agent"] == "ds-reproducer" and rep["details"].get("reproduced"):
        m = run.meta()
        m["repro_hashes"] = {t["path"]: sha_file(os.path.join(run.root, t["path"]))
                             for t in rep["details"]["repro_tests"]}
        m["repro_tests"] = rep["details"]["repro_tests"]
        run.save_meta(m)
    print(f"VALID {os.path.basename(a.report)}: status={rep['status']}")


def cmd_gate(_a):
    run = Run()
    meta, state = run.meta(), run.state_hash()
    results = []

    def add(name, status, detail):
        results.append((name, status, detail))

    def latest(agent):
        reps = run.reports(agent)
        if not reps:
            return None, None, 0
        n = max(reps)
        errs, _ = validate_report(run, os.path.join(run.dir, "reports", f"{agent}-{n}.json"))
        return reps[n], errs, len(reps)

    rep, errs, _ = latest("ds-reproducer")
    if not rep:
        add("Reproduced", "FAIL", "no reproducer report")
    elif errs:
        add("Reproduced", "FAIL", "invalid report: " + "; ".join(errs))
    elif not rep["details"].get("reproduced"):
        add("Reproduced", "FAIL", "reproducer could not reproduce the bug")
    else:
        add("Reproduced", "PASS", rep["details"].get("failure_signature", ""))

    hashes = meta.get("repro_hashes") or {}
    if not hashes:
        add("Repro test untouched", "FAIL", "no validated repro test recorded")
    else:
        changed = [p for p, h in hashes.items() if sha_file(os.path.join(run.root, p)) != h]
        add("Repro test untouched", "FAIL" if changed else "PASS",
            ("modified after reproduction: " + ", ".join(changed)) if changed else ", ".join(hashes))

    rep, errs, attempts = latest("ds-fixer")
    max_a = int(run.cfg.get("max_attempts", c.MAX_ATTEMPTS_DEFAULT))
    if not rep:
        add("Fix", "FAIL", "no fixer report")
    elif errs:
        add("Fix", "FAIL", "invalid report: " + "; ".join(errs))
    elif rep["status"] != "completed":
        add("Fix", "FAIL", f"fixer status {rep['status']}: {rep['summary']}")
    elif attempts > max_a:
        add("Fix", "FAIL", f"{attempts} attempts, limit is {max_a}")
    else:
        add("Fix", "PASS", f"attempt {attempts}: {rep['details'].get('root_cause', '')}")

    def verify(label, name, baseline_ok=None):
        ev = run.latest_evidence(label)
        if not ev:
            return add(name, "FAIL", f"no '{label}' evidence; run it with ds.py check")
        if ev.get("agent") != "orchestrator":
            return add(name, "FAIL", f"'{label}' must be run by the orchestrator, not {ev.get('agent')}")
        if ev.get("state_hash") != state:
            return add(name, "FAIL", f"{ev['id']} ran on older code; re-run after the last change")
        if ev["exit_code"] == 0:
            return add(name, "PASS", f"{ev['id']}: exit 0")
        if baseline_ok is False:
            return add(name, "WARN", f"{ev['id']}: exit {ev['exit_code']}, but the baseline suite also "
                                     "failed; compare failures by hand")
        add(name, "FAIL", f"{ev['id']}: exit {ev['exit_code']}")

    base = run.latest_evidence("baseline")
    verify("verify-repro", "Repro test passes")
    verify("verify-suite", "Full suite", None if not base else base["exit_code"] == 0)

    rep, errs, _ = latest("ds-reviewer")
    diff_meta = c.load_json(os.path.join(run.dir, "diff.json"), {})
    if not rep:
        add("Review", "FAIL", "no reviewer report")
    elif errs:
        add("Review", "FAIL", "invalid report: " + "; ".join(errs))
    elif diff_meta.get("state_hash") != state:
        add("Review", "FAIL", "code changed after the reviewed diff; run ds.py diff and review again")
    else:
        blocking = [f for f in rep["details"]["findings"] if f.get("severity") == "blocking"]
        if rep["details"]["verdict"] != "approve" or blocking:
            add("Review", "FAIL", f"verdict {rep['details']['verdict']}, {len(blocking)} blocking finding(s)")
        else:
            major = sum(1 for f in rep["details"]["findings"] if f.get("severity") == "major")
            add("Review", "PASS", f"approved, {major} major finding(s) to mention")

    touched = [p for p, h in (meta.get("baseline_dirty_hashes") or {}).items()
               if sha_file(os.path.join(run.root, p)) != h]
    add("User's prior changes", "WARN" if touched else "PASS",
        ("run modified files the user had already changed: " + ", ".join(touched)) if touched else "untouched")

    statuses = {s for _, s, _ in results}
    overall = "FAIL" if "FAIL" in statuses else "WARN" if "WARN" in statuses else "PASS"
    dirty = set(meta.get("baseline_dirty") or [])
    files = [f for f in run.changed_files() if f not in dirty or f in touched]

    lines = [f"# DevSquad final report — {overall}", "",
             f"Run `{run.id}`, generated {now()}", "", "## Bug", "", meta.get("bug", ""), "",
             "## Gate", "", "| Check | Result | Detail |", "|---|---|---|"]
    lines += [f"| {n} | {s} | {d.replace('|', '/').replace(chr(10), ' ')} |" for n, s, d in results]
    lines += ["", "## Files changed by this run", ""] + [f"- `{f}`" for f in files or ["(none)"]]
    fix, _, _ = latest("ds-fixer")
    if fix:
        lines += ["", "## Root cause", "", fix["details"].get("root_cause", ""), "",
                  "## Fix", "", fix["details"].get("fix_description", "")]
    rev, _, _ = latest("ds-reviewer")
    if rev and rev["details"].get("findings"):
        lines += ["", "## Review findings", ""]
        lines += [f"- **{f.get('severity')}** `{f.get('file')}:{f.get('line', '')}` {f.get('issue')}"
                  for f in rev["details"]["findings"] if isinstance(f, dict)]
    lines += ["", "Nothing has been committed. Commit only after the user approves."]
    with open(os.path.join(run.dir, "final_report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    for n, s, d in results:
        print(f"{s:4}  {n}: {d}")
    print(f"\nGATE {overall}  ->  {os.path.join(run.dir, 'final_report.md')}")
    sys.exit({"PASS": 0, "WARN": 3, "FAIL": 1}[overall])


def cmd_end(_a):
    """Close the active run so the guard goes quiet again. The run folder stays."""
    run = Run(need_run=False)
    if not run.id:
        print("no active run")
        return
    os.remove(os.path.join(run.root, c.STATE_DIR, "current"))
    print(f"ended run {run.id} (files kept in {run.dir})")


def main():
    ap = argparse.ArgumentParser(prog="ds.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init"); p.add_argument("--bug", required=True); p.set_defaults(fn=cmd_init)
    p = sub.add_parser("set"); p.add_argument("--test-cmd", required=True); p.set_defaults(fn=cmd_set)
    p = sub.add_parser("check")
    p.add_argument("--label", required=True)
    p.add_argument("--agent", required=True, choices=["orchestrator", *ROLES])
    p.add_argument("--timeout", type=int, default=900)
    p.add_argument("cmd", nargs=argparse.REMAINDER)
    p.set_defaults(fn=cmd_check)
    p = sub.add_parser("diff"); p.set_defaults(fn=cmd_diff)
    p = sub.add_parser("validate"); p.add_argument("report"); p.set_defaults(fn=cmd_validate)
    p = sub.add_parser("gate"); p.set_defaults(fn=cmd_gate)
    p = sub.add_parser("end"); p.set_defaults(fn=cmd_end)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
