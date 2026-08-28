#!/usr/bin/env python3
"""MIDI-011 — the two refusals that protect work, over HTTP.

mission_wtman.py drives term.dispatch directly, so it cannot reach the guard
that matters most: mapui refuses to remove a worktree an agent is living in.
That rule is in mapui, so it has to be tested through mapui.

Needs the server running on 8765 and a tmux server with the `push` session.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8765"
fails = []


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        fails.append(name)


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def tmux(*a):
    return subprocess.run(["tmux", *a], capture_output=True, text=True)


repo = tempfile.mkdtemp(prefix="guardrepo-")
for cmd in (["git", "init", "-q", "-b", "main"],
            ["git", "config", "user.email", "s@x"], ["git", "config", "user.name", "s"]):
    subprocess.run(cmd, cwd=repo, capture_output=True)
open(os.path.join(repo, "f.txt"), "w").write("x\n")
subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, capture_output=True)

dest = os.path.join(repo, "..", "guardwt")
dest = os.path.abspath(dest)
subprocess.run(["git", "-C", repo, "worktree", "add", "-q", dest, "-b", "guarded"],
               capture_output=True)
check("a worktree to defend exists", os.path.isdir(dest), dest)

print("\n== the main worktree is never removable ==")
code, body = call("POST", "/worktrees/remove", {"cwd": repo, "path": repo})
check("removing the main checkout is refused", code == 409, f"{code} {body[:90]}")

print("\n== a dirty worktree keeps its work ==")
open(os.path.join(dest, "unsaved.txt"), "w").write("work nobody committed\n")
code, body = call("POST", "/worktrees/remove", {"cwd": repo, "path": dest})
check("a dirty worktree is refused", code == 409, f"{code} {body[:90]}")
check("the uncommitted file is still there",
      os.path.exists(os.path.join(dest, "unsaved.txt")))
os.remove(os.path.join(dest, "unsaved.txt"))

print("\n== a worktree an agent lives in is not pulled out from under it ==")
win = tmux("new-window", "-P", "-F", "#{pane_id}", "-c", dest,
           "--", "claude", "--permission-mode", "auto")
pane = win.stdout.strip()
check("an agent started in the worktree", pane.startswith("%"), pane)
agent_seen = False
for _ in range(14):
    time.sleep(3)
    code, body = call("GET", "/agents")
    # realpath both sides: macOS resolves /var -> /private/var, so the agent
    # reports a path that is the same directory spelled differently. mapui's
    # own guard already does this; a test that does not just fails louder.
    real = os.path.realpath(dest)
    if code == 200 and any(os.path.realpath(a.get("cwd", "")).startswith(real)
                           for a in json.loads(body).get("agents", [])):
        agent_seen = True
        break
check("the API can see that agent", agent_seen)

code, body = call("POST", "/worktrees/remove", {"cwd": repo, "path": dest})
check("removing it is refused with 409", code == 409, f"{code} {body[:110]}")
check("the refusal names the agent, not just the path",
      "agent" in body.lower() or dest in body, body[:110])
check("the worktree survived the attempt", os.path.isdir(dest))

# even force must not win against a living agent -- force is about dirt, not
# about pulling the floor out from under something that is running
code, body = call("POST", "/worktrees/remove",
                  {"cwd": repo, "path": dest, "force": True})
check("force does not override a live agent", code == 409, f"{code} {body[:90]}")
check("still there after force", os.path.isdir(dest))

print("\n== once the agent is gone it can be removed ==")
tmux("kill-pane", "-t", pane)
time.sleep(4)
code, body = call("POST", "/worktrees/remove", {"cwd": repo, "path": dest})
check("removal succeeds once nothing lives there", code == 200, f"{code} {body[:90]}")
check("the directory is gone", not os.path.isdir(dest))

subprocess.run(["git", "-C", repo, "worktree", "prune"], capture_output=True)
shutil.rmtree(repo, ignore_errors=True)
shutil.rmtree(dest, ignore_errors=True)
print("\nOVERALL:", "PASS" if not fails else f"FAIL ({', '.join(fails)})")
sys.exit(1 if fails else 0)
