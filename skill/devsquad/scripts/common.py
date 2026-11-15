"""Shared helpers for ds.py and guard.py. Standard library only."""
import fnmatch
import json
import os
import re

STATE_DIR = ".devsquad"
MAX_ATTEMPTS_DEFAULT = 3

DEFAULT_TEST_DIRS = ["tests", "test", "__tests__", "spec", "specs"]
DEFAULT_TEST_FILES = [
    "test_*.py", "*_test.py", "conftest.py", "*_test.go",
    "*.test.*", "*.spec.*", "*Test.java", "*Tests.java", "*Test.kt",
]
SECRET_FILES = ["*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*", "id_ed25519*", "id_ecdsa*"]
SAFE_ENV_FILES = {".env.example", ".env.sample", ".env.template"}
# Untracked build/test artifacts that test runs create; never part of a change.
ARTIFACT_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules",
                 ".tox", ".nox", "coverage", "htmlcov", ".next", "dist", "build", "target"}
ARTIFACT_FILES = ["*.pyc", "*.pyo", ".coverage", ".coverage.*", "*.log"]


def is_artifact(relpath):
    parts = relpath.replace("\\", "/").split("/")
    if parts[0] in (STATE_DIR, ".claude") or any(p in ARTIFACT_DIRS for p in parts[:-1]):
        return True
    return any(fnmatch.fnmatch(parts[-1], pat) for pat in ARTIFACT_FILES)


def norm(path, base=None):
    """Absolute, case-normalised path. Accepts Git Bash style /c/Users/..."""
    p = str(path).strip().strip("'\"")
    m = re.match(r"^/([a-zA-Z])(/|$)", p)
    if m and os.name == "nt":
        p = m.group(1) + ":/" + p[3:]
    if not os.path.isabs(p) and base:
        p = os.path.join(base, p)
    return os.path.normcase(os.path.normpath(os.path.abspath(p)))


def find_root(start):
    """Nearest ancestor containing .git, or None."""
    cur = os.path.abspath(start)
    while True:
        if os.path.exists(os.path.join(cur, ".git")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def is_within(path, folder):
    path, folder = norm(path), norm(folder)
    return path == folder or path.startswith(folder.rstrip(os.sep) + os.sep)


def rel_parts(path, root):
    rel = os.path.relpath(norm(path), norm(root))
    return [p for p in rel.replace("\\", "/").split("/") if p not in ("", ".")]


def load_config(root):
    cfg = {"test_dirs": DEFAULT_TEST_DIRS, "test_files": DEFAULT_TEST_FILES,
           "max_attempts": MAX_ATTEMPTS_DEFAULT}
    try:
        with open(os.path.join(root, STATE_DIR, "config.json"), encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


def is_test_path(path, root, cfg):
    parts = rel_parts(path, root)
    if not parts:
        return False
    dirs = {d.lower() for d in cfg["test_dirs"]}
    if any(p.lower() in dirs for p in parts[:-1]):
        return True
    name = parts[-1]
    return any(fnmatch.fnmatch(name, pat) for pat in cfg["test_files"])


def is_secret_path(path):
    name = os.path.basename(str(path).strip().strip("'\""))
    low = name.lower()
    if low in SAFE_ENV_FILES:
        return False
    if low == ".env" or low.startswith(".env."):
        return True
    return any(fnmatch.fnmatch(low, pat) for pat in SECRET_FILES)


def current_run(root):
    """(run_id, run_dir) of the active run, or (None, None)."""
    try:
        with open(os.path.join(root, STATE_DIR, "current"), encoding="utf-8") as f:
            run_id = f.read().strip()
    except OSError:
        return None, None
    run_dir = os.path.join(root, STATE_DIR, "runs", run_id)
    return (run_id, run_dir) if os.path.isdir(run_dir) else (None, None)


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default
