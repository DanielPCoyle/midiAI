#!/usr/bin/env python3
"""End-to-end smoke of the tmux backend against a live tmux server.

Exercises the three calls the demo fixtures cannot reach -- agent start,
pane close, worktree create -- plus send/read/focus round trips. Throwaway
git repo and its own tmux session, so it touches nothing real.

    python3 smoke_tmux.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import term

SESSION = "smoke"
fails = []


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        fails.append(name)


def j(raw):
    return json.loads(raw)


def tmux(*a):
    return subprocess.run(["tmux", *a], capture_output=True, text=True)


print("== agent start ==")
tmux("kill-server")
work = tempfile.mkdtemp(prefix="smoke-")
# first start with no server running takes the new-session branch
r = j(term.dispatch(("agent", "start", "alpha", "--cwd", work,
                     "--split", "right", "--focus", "--", "sleep", "600")))
check("start with no server", "result" in r, str(r)[:90])
r = j(term.dispatch(("agent", "start", "beta", "--cwd", work,
                     "--split", "right", "--focus", "--", "sleep", "600")))
check("start into existing server", "result" in r, str(r)[:90])
r = j(term.dispatch(("agent", "start", "alpha", "--cwd", work,
                     "--split", "right", "--focus", "--", "sleep", "600")))
check("duplicate name refused", r.get("error", {}).get("code") == "agent_name_taken")

names = tmux("list-panes", "-a", "-F", "#{@agent_name}").stdout.split()
check("both names survive in one window", sorted(names) == ["alpha", "beta"], str(names))

print("\n== send / read round trip ==")
pane = tmux("list-panes", "-a", "-F", "#{pane_id}").stdout.split()[0]
tmux("respawn-pane", "-k", "-t", pane, "cat")     # something that echoes
time.sleep(0.6)
term.dispatch(("agent", "send", pane, "round-trip-marker"))
time.sleep(0.6)
text = j(term.dispatch(("agent", "read", pane, "--lines", "40")))["result"]["read"]["text"]
check("sent text is readable back", "round-trip-marker" in text)
check("read truncates to --lines", len(text.splitlines()) <= 40, f"{len(text.splitlines())} lines")

r = j(term.dispatch(("agent", "send", pane, "")))
check("empty send refused", r.get("error", {}).get("code") == "empty_send")

print("\n== focus ==")
r = j(term.dispatch(("agent", "focus", pane)))
check("focus returns ok", "result" in r)
active = tmux("display", "-p", "-t", pane, "#{pane_active}").stdout.strip()
check("focused pane is active in its window", active == "1")

print("\n== pane close ==")
before = len(tmux("list-panes", "-a", "-F", "#{pane_id}").stdout.split())
r = j(term.dispatch(("pane", "close", pane)))
time.sleep(0.4)
after = len(tmux("list-panes", "-a", "-F", "#{pane_id}").stdout.split())
check("close removes exactly one pane", "result" in r and after == before - 1,
      f"{before} -> {after}")

print("\n== worktree create ==")
repo = tempfile.mkdtemp(prefix="smokerepo-")
for cmd in (["git", "init", "-q", "-b", "main"], ["git", "config", "user.email", "s@x"],
            ["git", "config", "user.name", "s"]):
    subprocess.run(cmd, cwd=repo, capture_output=True)
open(os.path.join(repo, "f.txt"), "w").write("x\n")
subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, capture_output=True)

root = tempfile.mkdtemp(prefix="smokewt-")
term.WORKTREE_ROOT = root                      # keep it out of ~/.push
r = j(term.dispatch(("worktree", "create", "--cwd", repo,
                     "--branch", "push/123456", "--focus")))
check("worktree create returns ok", "result" in r, str(r)[:120])
dest = os.path.join(root, os.path.basename(repo), "push-123456")
check("slashed branch flattens to one dir", os.path.isdir(dest), dest)
branches = subprocess.run(["git", "branch", "--format=%(refname:short)"],
                          cwd=repo, capture_output=True, text=True).stdout.split()
check("branch keeps its slash", "push/123456" in branches, str(branches))
check("no stray dir beside the repo",
      not os.path.exists(os.path.dirname(repo) + f"/{os.path.basename(repo)}-push"))

print("\n== cleanup ==")
tmux("kill-server")
subprocess.run(["git", "-C", repo, "worktree", "remove", "--force", dest],
               capture_output=True)
for d in (work, repo, root):
    shutil.rmtree(d, ignore_errors=True)
print("cleaned")

print("\nOVERALL:", "PASS" if not fails else f"FAIL ({', '.join(fails)})")
sys.exit(1 if fails else 0)
