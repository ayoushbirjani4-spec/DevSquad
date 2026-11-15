"""Install DevSquad into Claude Code.

  python install.py                 user install  (~/.claude, all projects)
  python install.py --project PATH  project install (PATH/.claude)
  add --force to overwrite an existing install
  add --uninstall to remove it again (same --project, if you used one)

Copies skill/devsquad -> <dest>/skills/devsquad and agents/*.md -> <dest>/agents,
filling in absolute paths for the Python interpreter and hook scripts.
"""
import argparse
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def fwd(p):
    return os.path.abspath(p).replace("\\", "/")


MATCHER = "Bash|Edit|Write|MultiEdit|NotebookEdit|Read|Grep"


def load_settings(path):
    """(data, raw) from settings.json; backs it up before any change."""
    if not os.path.exists(path):
        return {}, None
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except ValueError:
        sys.exit(f"{path} is not valid JSON; fix it and re-run (nothing was changed there).")
    with open(path + ".devsquad-bak", "w", encoding="utf-8") as f:
        f.write(raw)
    return data, raw


def save_settings(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def is_guard_entry(e):
    return any("devsquad" in str(h.get("args", "")) + str(h.get("command", ""))
               and "guard.py" in str(h.get("args", "")) + str(h.get("command", ""))
               for h in e.get("hooks", []))


def install_hook(dest, python, guard):
    """Add the guard to <dest>/settings.json. Settings hooks also run inside
    subagents, unlike agent-frontmatter hooks in an untrusted workspace.
    Replaces any earlier DevSquad entry; keeps everything else untouched."""
    path = os.path.join(dest, "settings.json")
    data, _ = load_settings(path)
    entries = data.setdefault("hooks", {}).setdefault("PreToolUse", [])
    entries[:] = [e for e in entries if not is_guard_entry(e)]
    entries.append({"matcher": MATCHER,
                    "hooks": [{"type": "command", "command": python, "args": [guard]}]})
    save_settings(path, data)
    return path


def uninstall(dest, skill_dst, agent_dst, agents):
    """Remove the guard hook, the skill and DevSquad's own agent files."""
    removed = []
    path = os.path.join(dest, "settings.json")
    data, raw = load_settings(path)
    hooks = data.get("hooks", {})
    entries = hooks.get("PreToolUse", [])
    kept = [e for e in entries if not is_guard_entry(e)]
    if len(kept) != len(entries):
        if kept:
            hooks["PreToolUse"] = kept
        else:
            hooks.pop("PreToolUse", None)
            if not hooks:
                data.pop("hooks", None)
        save_settings(path, data)
        removed.append(f"guard hook in {path}")
    elif raw is not None:
        os.remove(path + ".devsquad-bak")  # nothing changed, no backup needed
    if os.path.exists(skill_dst):
        shutil.rmtree(skill_dst)
        removed.append(skill_dst)
    for f in agents:
        p = os.path.join(agent_dst, f)
        if os.path.exists(p):
            os.remove(p)
            removed.append(p)
    if removed:
        print("Removed DevSquad:\n  " + "\n  ".join(removed))
    else:
        print(f"DevSquad is not installed in {dest}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", help="repository to install into instead of ~/.claude")
    ap.add_argument("--force", action="store_true", help="overwrite an existing install")
    ap.add_argument("--uninstall", action="store_true", help="remove the skill, agents and guard hook")
    a = ap.parse_args()

    dest = os.path.join(a.project, ".claude") if a.project else os.path.join(os.path.expanduser("~"), ".claude")
    skill_dst = os.path.join(dest, "skills", "devsquad")
    agent_dst = os.path.join(dest, "agents")
    agents = sorted(f for f in os.listdir(os.path.join(HERE, "agents")) if f.endswith(".md"))

    if a.uninstall:
        uninstall(dest, skill_dst, agent_dst, agents)
        return

    existing = [p for p in [skill_dst] + [os.path.join(agent_dst, f) for f in agents] if os.path.exists(p)]
    if existing and not a.force:
        print("Already installed (use --force to overwrite):\n  " + "\n  ".join(existing))
        sys.exit(1)

    if os.path.exists(skill_dst):
        shutil.rmtree(skill_dst)
    shutil.copytree(os.path.join(HERE, "skill", "devsquad"), skill_dst,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    os.makedirs(agent_dst, exist_ok=True)
    for f in agents:
        shutil.copy2(os.path.join(HERE, "agents", f), os.path.join(agent_dst, f))

    py = fwd(sys.executable)
    scripts = fwd(os.path.join(skill_dst, "scripts"))
    subs = {
        "{{PYTHON}}": py,
        "{{GUARD}}": f"{scripts}/guard.py",
        "{{DS}}": f'"{py}" "{scripts}/ds.py"',
    }
    targets = [os.path.join(skill_dst, "SKILL.md")] + [os.path.join(agent_dst, f) for f in agents]
    for path in targets:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        for k, v in subs.items():
            text = text.replace(k, v)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    settings = install_hook(dest, py, subs["{{GUARD}}"])

    print(f"Installed DevSquad to {dest}")
    print(f"  hook:   PreToolUse guard added to {settings}")
    print(f"  skill:  {skill_dst}")
    print(f"  agents: {', '.join(agents)}")
    print(f"  python: {py}")
    print("Restart Claude Code (or open a new session), then run:  /devsquad <bug description>")


if __name__ == "__main__":
    main()
