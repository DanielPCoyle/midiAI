#!/usr/bin/env python3
"""MIDI-008 — POST /worktree, against a throwaway repo.

Add Track has been on the Push since the beginning; the app could see the
button's effect and never press it. Checks the route end to end without
touching a real repository.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import term

BASE = "http://127.0.0.1:8765"
fails = []


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        fails.append(name)


def call(path, body):
    req = urllib.request.Request(f"{BASE}{path}", data=json.dumps(body).encode(),
                                 method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


repo = tempfile.mkdtemp(prefix="wtrepo-")
for cmd in (["git", "init", "-q", "-b", "main"],
            ["git", "config", "user.email", "s@x"], ["git", "config", "user.name", "s"]):
    subprocess.run(cmd, cwd=repo, capture_output=True)
open(os.path.join(repo, "f.txt"), "w").write("x\n")
subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, capture_output=True)

root = tempfile.mkdtemp(prefix="wtroot-")
term.WORKTREE_ROOT = root      # in-process only; mapui has its own copy

code, body = call("/worktree", {"cwd": "/no/such/dir", "branch": "x"})
check("a bad cwd is refused", code == 400, f"{code} {body[:60]}")

code, body = call("/worktree", {"cwd": repo})
check("a missing branch is refused", code == 400, f"{code} {body[:60]}")

code, body = call("/worktree", {"cwd": repo, "branch": "Fix The Thing"})
check("a real request is accepted", code == 200, f"{code} {body[:80]}")
slug = json.loads(body).get("branch") if code == 200 else None
check("the branch name went through slug()", slug == "fix-the-thing", str(slug))

branches = subprocess.run(["git", "branch", "--format=%(refname:short)"],
                          cwd=repo, capture_output=True, text=True).stdout.split()
check("the branch exists in the repo", slug in branches, str(branches))

wt = subprocess.run(["git", "worktree", "list"], cwd=repo,
                    capture_output=True, text=True).stdout
check("git knows about the worktree", slug.replace("/", "-") in wt.replace("/", "-"),
      wt.strip()[:120])

subprocess.run(["git", "-C", repo, "worktree", "prune"], capture_output=True)
for d in (repo, root):
    shutil.rmtree(d, ignore_errors=True)
print("\nOVERALL:", "PASS" if not fails else f"FAIL ({', '.join(fails)})")
sys.exit(1 if fails else 0)
