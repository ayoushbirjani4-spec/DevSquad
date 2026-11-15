"""DevSquad PreToolUse hook.

Reads the hook JSON on stdin and enforces per-role file and shell rules for
the ds-* subagents. For the main session it only asks before destructive git
commands and secret reads, and keeps the evidence folder ds.py-only.

Decision output follows the hooks spec: hookSpecificOutput.permissionDecision.
No output + exit 0 means "no opinion". ds-* agents fail closed (exit 2) if the
guard itself crashes.
"""
import fnmatch
import json
import os
import re
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as c  # noqa: E402

ROLES = {"ds-reproducer", "ds-fixer", "ds-reviewer"}
WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}

DESTRUCTIVE_GIT = re.compile(
    r"\bgit\b(?:\s+-C\s+\S+)?\s+(push|commit|reset\s+--hard|clean|restore|stash|rebase|merge|"
    r"am|cherry-pick|tag|branch\s+-[dD]|checkout\s+(?:--|\.(?:\s|$)|-f))", re.I)
RECURSIVE_RM = re.compile(r"\b(rm|rmdir)\s+(-[a-zA-Z]*[rR]|/s)|\bRemove-Item\b.*-Recurse", re.I)
INSTALL = re.compile(
    r"\b(pip3?|uv\s+pip|poetry|pipenv|conda|npm|pnpm|yarn|bun|gem|cargo|apt(?:-get)?|brew|choco|winget)"
    r"\s+(install|add|i|get)\b|\bgo\s+(install|get)\b", re.I)
PIPE_TO_SHELL = re.compile(r"\b(curl|wget|iwr|irm|Invoke-WebRequest|Invoke-RestMethod)\b[^|]*\|\s*"
                           r"(sh|bash|zsh|python3?|iex|Invoke-Expression)\b", re.I)
REDIRECT = re.compile(r"(?:\d?>>?|&>>?)\s*([^\s;|&<>]+)")
WRITE_CMDS = {"tee", "mv", "cp", "rm", "touch", "truncate", "dd", "install", "ln", "chmod",
              "Set-Content", "Add-Content", "Out-File", "New-Item", "Remove-Item", "Move-Item",
              "Copy-Item"}


_CALL = {}


def log(decision, reason=""):
    """Append one line per call to $DEVSQUAD_GUARD_LOG, if set (debugging aid)."""
    path = os.environ.get("DEVSQUAD_GUARD_LOG")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({**_CALL, "decision": decision, "reason": reason}) + "\n")
    except OSError:
        pass


def decide(decision, reason):
    log(decision, reason)
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
        "permissionDecisionReason": "DevSquad guard: " + reason,
    }}))
    sys.exit(0)


class Ctx:
    def __init__(self, data):
        self.role = data.get("agent_type") or ""
        self.cwd = data.get("cwd") or os.getcwd()
        start = os.environ.get("CLAUDE_PROJECT_DIR") or self.cwd
        self.root = c.find_root(start) or c.find_root(self.cwd) or os.path.abspath(start)
        self.cfg = c.load_config(self.root)
        self.run_id, self.run_dir = c.current_run(self.root)
        run = c.load_json(os.path.join(self.run_dir, "run.json"), {}) if self.run_dir else {}
        self.dirty = [os.path.join(self.root, p) for p in run.get("baseline_dirty", [])]

    def evidence_dir(self):
        return os.path.join(self.run_dir, "evidence") if self.run_dir else None

    def reports_dir(self):
        return os.path.join(self.run_dir, "reports") if self.run_dir else None

    def write_problem(self, path):
        """Why `role` may not write `path`, or None if it may."""
        p = c.norm(path, self.cwd)
        ev = self.evidence_dir()
        if ev and c.is_within(p, ev):
            return "the evidence folder is written only by ds.py check"
        if self.role not in ROLES:
            return None
        rep = self.reports_dir()
        if rep and c.is_within(p, rep):
            return None
        if not c.is_within(p, self.root):
            return "path is outside the repository"
        top = (c.rel_parts(p, self.root) or [""])[0].lower()
        if top in (".git", ".claude", c.STATE_DIR):
            return f"{top}/ is off limits (only your report file under {c.STATE_DIR}/runs/<run>/reports/)"
        if c.is_secret_path(p):
            return "secret files are off limits"
        if any(c.norm(d) == p for d in self.dirty):
            return "the user had uncommitted changes in this file before the run (do_not_touch)"
        test = c.is_test_path(p, self.root, self.cfg)
        if self.role == "ds-reproducer" and not test:
            return "the reproducer may only write test files"
        if self.role == "ds-fixer" and test:
            return "the fixer may not edit test files; report status 'blocked' if the test is wrong"
        if self.role == "ds-reviewer":
            return "the reviewer may only write its report file"
        return None


def bash_write_targets(cmd, cwd):
    """Best-effort list of absolute paths a shell command writes to.
    Follows `cd` so relative paths resolve where the shell would put them."""
    base, out = cwd, []
    for seg in re.split(r"&&|\|\||[;|\n]", cmd):
        found = [t for t in REDIRECT.findall(seg) if t not in ("/dev/null", "NUL", "$null")]
        seg_wo = REDIRECT.sub(" ", seg)
        try:
            words = shlex.split(seg_wo, posix=True)
        except ValueError:
            words = seg_wo.split()
        if words:
            name = os.path.basename(words[0])
            args = [w for w in words[1:] if not w.startswith("-")]
            if name in ("cd", "pushd", "Set-Location") and args:
                base = c.norm(args[0], base)
            elif name == "sed" and any(w.startswith("-i") or w == "--in-place" for w in words):
                has_e = any(w in ("-e", "--expression") for w in words)
                found += args if has_e else args[1:]
            elif name in ("mv", "cp", "install", "ln", "Move-Item", "Copy-Item"):
                found += args[-1:]
            elif name in WRITE_CMDS:
                found += args
        out += [c.norm(t, base) for t in found if t and not t.startswith(("$", "&"))]
    return out


SECRET_SAMPLES = (".env", ".env.local", ".env.production", "id_rsa", "id_ed25519", "server.pem",
                  "server.key", "cert.p12", "cert.pfx")


def grep_targets_secret(inp):
    """True if a Grep call points at a secret file or its glob/type can match one."""
    path = inp.get("path") or ""
    if path and c.is_secret_path(path):
        return True
    pats = [p for p in (inp.get("glob") or "").split() if p]
    while any("{" in p for p in pats):  # expand braces: *.{py,key} -> *.py, *.key
        out = []
        for p in pats:
            m = re.search(r"\{([^{}]*)\}", p)
            out += [p[:m.start()] + alt + p[m.end():] for alt in m.group(1).split(",")] if m \
                else [p.replace("{", "")]
        pats = out
    return any(fnmatch.fnmatch(s, p.split("/")[-1].lower()) for p in pats for s in SECRET_SAMPLES)


def check_bash(ctx, cmd):
    if ctx.role in ROLES:
        if DESTRUCTIVE_GIT.search(cmd):
            decide("deny", "git commands that change history or discard work are not allowed for specialists")
        if RECURSIVE_RM.search(cmd):
            decide("deny", "recursive delete is not allowed")
        if INSTALL.search(cmd):
            decide("deny", "installing packages is not allowed; report status 'blocked' with the missing dependency")
        if PIPE_TO_SHELL.search(cmd):
            decide("deny", "piping downloads into a shell is not allowed")
        for tok in re.findall(r"[^\s'\"=<>|;&]+", cmd):
            if c.is_secret_path(tok):
                decide("deny", f"secret files are off limits ({os.path.basename(tok)})")
        for t in bash_write_targets(cmd, ctx.cwd):
            why = ctx.write_problem(t)
            if why:
                decide("deny", f"shell write to {os.path.relpath(t, ctx.root)}: {why}")
        return
    # main session (orchestrator) or other agents
    if DESTRUCTIVE_GIT.search(cmd) or RECURSIVE_RM.search(cmd):
        decide("ask", "destructive command; confirm with the user")
    ev = ctx.evidence_dir()
    for t in bash_write_targets(cmd, ctx.cwd):
        if ev and c.is_within(t, ev):
            decide("deny", "the evidence folder is written only by ds.py check")


_ROLE = ""


def main():
    global _ROLE
    try:
        data = json.load(sys.stdin)
    except ValueError:
        sys.exit(0)
    _ROLE = data.get("agent_type") or ""
    ctx = Ctx(data)
    tool = data.get("tool_name", "")
    inp = data.get("tool_input") or {}
    _CALL.update(agent=_ROLE or "main", tool=tool,
                 target=str(inp.get("command") or inp.get("file_path") or inp.get("notebook_path")
                            or inp.get("path") or inp.get("glob") or "")[:200])
    # Installed as a global settings hook: stay silent in ordinary sessions.
    if _ROLE not in ROLES and not ctx.run_id:
        log("none", "no active DevSquad run")
        sys.exit(0)
    if tool == "Bash":
        check_bash(ctx, inp.get("command", ""))
    elif tool in WRITE_TOOLS:
        path = inp.get("file_path") or inp.get("notebook_path") or ""
        why = ctx.write_problem(path) if path else None
        if why:
            decide("deny", f"{os.path.basename(path)}: {why}")
    elif tool == "Read":
        path = inp.get("file_path", "")
        if path and c.is_secret_path(path):
            decide("deny" if ctx.role in ROLES else "ask", "reading a secrets file")
    elif tool == "Grep":
        if grep_targets_secret(inp):
            decide("deny" if ctx.role in ROLES else "ask", "searching inside secrets files")
    log("none")
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # fail closed for specialists, open for everyone else
        sys.stderr.write(f"DevSquad guard error: {exc!r}\n")
        sys.exit(2 if _ROLE in ROLES else 0)
