#!/usr/bin/env python3
"""worktree list/remove/open, against a throwaway repo.

A worktree today can be created and never removed -- it piles up under
WORKTREE_ROOT forever. This exercises the real dispatch functions, not HTTP:
mapui runs in another process with its own copy of term, so an HTTP mission
would only prove the route wiring, not the git logic behind it.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import term

fails = []


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        fails.append(name)


def call(*args):
    return json.loads(term.dispatch(args))


repo = tempfile.mkdtemp(prefix="wtmanrepo-")
for cmd in (["git", "init", "-q", "-b", "main"],
            ["git", "config", "user.email", "s@x"], ["git", "config", "user.name", "s"]):
    subprocess.run(cmd, cwd=repo, capture_output=True)
open(os.path.join(repo, "f.txt"), "w").write("x\n")
subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, capture_output=True)
repo = os.path.realpath(repo)

root = tempfile.mkdtemp(prefix="wtmanroot-")
term.WORKTREE_ROOT = root

# --- list on a fresh repo: exactly one entry, main, exists ---
r = call("worktree", "list", "--cwd", repo)
wts = r.get("result", {}).get("worktrees", [])
check("fresh repo lists exactly one worktree", len(wts) == 1, str(wts))
if wts:
    check("the lone worktree is main", wts[0]["main"] is True, str(wts[0]))
    check("the lone worktree exists", wts[0]["exists"] is True, str(wts[0]))

# --- create a worktree, list now returns two ---
r = call("worktree", "create", "--cwd", repo, "--branch", "feature-x")
check("worktree create succeeds", "error" not in r, str(r))

r = call("worktree", "list", "--cwd", repo)
wts = r.get("result", {}).get("worktrees", [])
check("list now returns two", len(wts) == 2, str(wts))
new = wts[1] if len(wts) == 2 else None
if new:
    check("the second entry has the right branch", new["branch"] == "feature-x", str(new))
    check("the second entry is not main", new["main"] is False, str(new))
    check("the second entry exists", new["exists"] is True, str(new))
dest = new["path"] if new else None

# --- remove a dirty worktree is refused; with force it succeeds ---
if dest:
    open(os.path.join(dest, "dirty.txt"), "w").write("uncommitted\n")
    r = call("worktree", "remove", "--cwd", repo, "--path", dest)
    check("a dirty worktree is refused", r.get("error", {}).get("code") == "worktree_dirty",
          str(r))
    check("still on disk after a refused remove", os.path.isdir(dest), dest)

    r = call("worktree", "remove", "--cwd", repo, "--path", dest, "--force")
    check("--force removes a dirty worktree", "error" not in r, str(r))
    check("gone from disk after a forced remove", not os.path.isdir(dest), dest)

# --- delete the directory behind git's back -> exists: False, remove prunes it ---
r = call("worktree", "create", "--cwd", repo, "--branch", "feature-y")
check("second worktree create succeeds", "error" not in r, str(r))
r = call("worktree", "list", "--cwd", repo)
wts = r.get("result", {}).get("worktrees", [])
orphan = next((w for w in wts if w["branch"] == "feature-y"), None)
check("feature-y is in the list", orphan is not None, str(wts))
orphan_path = orphan["path"] if orphan else None
if orphan_path:
    shutil.rmtree(orphan_path)
    r = call("worktree", "list", "--cwd", repo)
    wts = r.get("result", {}).get("worktrees", [])
    orphan = next((w for w in wts if w["branch"] == "feature-y"), None)
    check("deleted-behind-git's-back shows exists: False", orphan is not None and
          orphan["exists"] is False, str(orphan))

    r = call("worktree", "remove", "--cwd", repo, "--path", orphan_path)
    check("remove prunes the orphan", "error" not in r, str(r))
    r = call("worktree", "list", "--cwd", repo)
    wts = r.get("result", {}).get("worktrees", [])
    check("orphan is gone from the list after prune", not any(w["branch"] == "feature-y" for w in wts),
          str(wts))

# --- remove the main worktree is refused ---
r = call("worktree", "list", "--cwd", repo)
main = next(w for w in r["result"]["worktrees"] if w["main"])
r = call("worktree", "remove", "--cwd", repo, "--path", main["path"])
check("removing the main worktree is refused",
      r.get("error", {}).get("code") == "worktree_is_main", str(r))

# --- open ---
r = call("worktree", "open", "--cwd", repo, "--path", "/no/such/dir")
check("opening a missing dir is refused",
      r.get("error", {}).get("code") == "worktree_missing", str(r))

# cleanup
subprocess.run(["git", "-C", repo, "worktree", "prune"], capture_output=True)
for d in (repo, root):
    shutil.rmtree(d, ignore_errors=True)

print("\nOVERALL:", "PASS" if not fails else f"FAIL ({', '.join(fails)})")
sys.exit(1 if fails else 0)
