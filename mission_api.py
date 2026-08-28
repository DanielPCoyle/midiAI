#!/usr/bin/env python3
"""MIDI-001 — the app-facing endpoints, against a live tmux server.

Serves this worktree's mapui on a spare port so the running instance on 8765
is left alone, then drives every route the app would call.

    python3 mission_api.py
"""
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import HTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mapui
import push_cc
import term

PORT = 8766
BASE = f"http://127.0.0.1:{PORT}"
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
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


srv = HTTPServer(("127.0.0.1", PORT), mapui.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
time.sleep(0.4)
print(f"serving this worktree's mapui on {PORT} (yours on 8765 untouched)\n")

print("== GET /agents ==")
code, body = call("GET", "/agents")
agents = json.loads(body).get("agents", []) if code == 200 else []
check("200 with an agent list", code == 200 and isinstance(agents, list),
      f"{code} {len(agents)} agents")
check("the tmux panes are in it", len(agents) >= 2, str([a["terminal_id"] for a in agents]))
check("each carries what the app draws",
      all(set(a) >= {"terminal_id", "cwd", "agent_status", "focused"} for a in agents))

print("\n== POST /agents — create ==")
code, body = call("POST", "/agents", {"cwd": "/tmp", "name": "missionmade"})
check("201/200 and a name back", code == 200, f"{code} {body[:80]}")
made = json.loads(body).get("name") if code == 200 else None
check("it reports the name it used", made == "missionmade", str(made))
time.sleep(1.0)
code, body = call("GET", "/agents")
names = [a.get("name") for a in json.loads(body).get("agents", [])]
check("the new agent appears in the list", "missionmade" in names, str(names))

code, body = call("POST", "/agents", {"cwd": "/tmp", "name": "missionmade"})
check("duplicate name refused with 409", code == 409, f"{code} {body[:70]}")

code, body = call("POST", "/agents", {"cwd": "/no/such/dir", "name": "nope"})
check("bad cwd refused with 400", code == 400, f"{code} {body[:70]}")

print("\n== POST /agents/rename ==")
tid = next((a["terminal_id"] for a in json.loads(call("GET", "/agents")[1])["agents"]
            if a.get("name") == "missionmade"), None)
code, body = call("POST", "/agents/rename", {"terminal_id": tid, "name": "renamed-by-api"})
check("rename accepted", code == 200, f"{code} {body[:70]}")
code, body = call("GET", "/agents")
names = [a.get("name") for a in json.loads(body).get("agents", [])]
check("the list reports the new name", "renamed-by-api" in names, str(names))

code, body = call("POST", "/agents/rename", {"terminal_id": tid, "name": "Not Valid"})
check("an invalid name is refused with 400", code == 400, f"{code} {body[:70]}")

print("\n== POST /prompt ==")
# Its OWN pane, never an existing one. This used to respawn agents[0] with
# `cat` to get something that echoes -- which killed a live claude on the
# session the surface was driving, and left the Push showing a pane that was
# no longer an agent. A test may not eat the thing it is testing on.
made = subprocess.run(["tmux", "new-window", "-P", "-F", "#{pane_id}",
                       "-c", "/tmp", "--", "cat"],
                      capture_output=True, text=True)
target = made.stdout.strip()
check("the prompt test made its own pane", target.startswith("%"), target)
time.sleep(0.6)
code, body = call("POST", "/prompt",
                  {"terminal_id": target, "text": "prompt-from-the-api", "submit": False})
check("prompt accepted", code == 200, f"{code} {body[:70]}")
time.sleep(0.8)
seen = subprocess.run(["tmux", "capture-pane", "-p", "-t", target],
                      capture_output=True, text=True).stdout
check("the text landed in that pane", "prompt-from-the-api" in seen)

code, body = call("POST", "/prompt", {"terminal_id": target, "text": ""})
check("empty prompt refused with 400", code == 400, f"{code} {body[:70]}")

subprocess.run(["tmux", "kill-pane", "-t", target], capture_output=True)

print("\n== POST /agents/close ==")
before = len(json.loads(call("GET", "/agents")[1])["agents"])
code, body = call("POST", "/agents/close", {"terminal_id": tid})
time.sleep(0.6)
after = len(json.loads(call("GET", "/agents")[1])["agents"])
check("close accepted and the agent is gone", code == 200 and after == before - 1,
      f"{code} {before} -> {after}")

print("\n== the two files the app polls ==")
code, body = call("GET", "/macros")
check("/macros serves the grid", code == 200 and "pages" in body, str(code))
code, body = call("GET", "/target")
check("/target answers", code == 200, f"{code} {body[:90]}")

srv.shutdown()
print("\nOVERALL:", "PASS" if not fails else f"FAIL ({', '.join(fails)})")
sys.exit(1 if fails else 0)
