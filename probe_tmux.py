#!/usr/bin/env python3
"""Probe: can tmux stand in for herdr?

Three claims, checked against push_cc's own parsers rather than a hand-rolled
reader -- if prompt_text() and read_pane() cannot see it, it does not count.

  1. `tmux send-keys -l` carries the exact byte strings push_cc sends through
     `herdr agent send`, into a DETACHED pane (no focus stolen, none to steal).
  2. `tmux capture-pane -p -S -N` returns what `herdr agent read --lines N`
     returned -- close enough for prompt_text/read_pane/tldr.
  3. tmux `#{pane_pid}` joins to `claude agents --json` `pid`, giving cwd,
     sessionId and status without herdr.

Usage: python3 probe_tmux.py [pane]     (default %0, session `probe`)
"""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, "/Users/dancoyle/midiAI")
import push_cc

PANE = sys.argv[1] if len(sys.argv) > 1 else "%0"

BACKSPACE, ESCAPE = "\x7f", "\x1b"
DELETE_FWD = "\x1b[3~"
LEFT, RIGHT = "\x1b[D", "\x1b[C"


def send(text):
    subprocess.run(["tmux", "send-keys", "-l", "-t", PANE, "--", text],
                   check=True)


def read(lines=40):
    out = subprocess.run(["tmux", "capture-pane", "-p", "-t", PANE,
                          "-S", f"-{lines}"], capture_output=True, text=True)
    return out.stdout.splitlines()


def pending():
    """Exactly what push_cc would show as the half-typed prompt."""
    return push_cc.prompt_text(read(40))


def clear():
    send(ESCAPE)
    time.sleep(0.4)
    send(ESCAPE)
    time.sleep(0.6)


fails = []


def step(name, fn, want):
    fn()
    time.sleep(0.9)
    got = pending()
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {name:<24} prompt_text={got!r}")
    if not ok:
        fails.append(f"{name}: wanted {want!r}, got {got!r}")


print("== 1. send-keys -l carries push_cc's byte strings, pane detached ==")
clear()
step("baseline empty", lambda: None, "")
step("literal text", lambda: send("probe text"), "probe text")
step("backspace x5", lambda: send(BACKSPACE * 5), "probe")
step("left arrow x3", lambda: send(LEFT * 3), "probe")
step("forward delete x3", lambda: send(DELETE_FWD * 3), "pr")
step("escape clears", lambda: clear(), "")

print("\n== 2. capture-pane feeds push_cc's scrapers ==")
lines = read(400)
scan = push_cc.read_pane(lines)
body = [l for l in lines if not set(l.strip()) <= set("─━ ")]
print(f"      {len(lines)} lines captured (asked 400)")
print(f"      read_pane -> say={scan['say'][:40]!r} act={scan['act'][:30]!r} "
      f"opts={len(scan['opts'])}")
print(f"      tldr      -> {push_cc.tldr(body)[:1]}")
if not lines:
    fails.append("capture-pane returned nothing")

print("\n== 3. pane_pid joins to `claude agents --json` ==")
fmt = "#{pane_id}\t#{pane_pid}\t#{pane_current_path}\t#{pane_active}\t#{window_active}"
panes = [l.split("\t") for l in subprocess.run(
    ["tmux", "list-panes", "-a", "-F", fmt],
    capture_output=True, text=True).stdout.splitlines()]
agents = {a["pid"]: a for a in json.loads(subprocess.run(
    ["claude", "agents", "--json"], capture_output=True, text=True).stdout)}

STATUS = {"busy": "working", "waiting": "blocked"}
joined = []
for pid_str, row in ((p[1], p) for p in panes):
    a = agents.get(int(pid_str))
    if not a:
        continue
    joined.append({
        "terminal_id": row[0], "pane_id": row[0],
        "cwd": a["cwd"],
        "agent_status": STATUS.get(a.get("status"), "idle"),
        "focused": row[3] == "1" and row[4] == "1",
        "agent_session": {"value": a["sessionId"]},
    })

for j in joined:
    print(f"      {j['terminal_id']}  {j['agent_status']:<8} {j['cwd']}")
    t = push_cc.transcript(j)
    print(f"        transcript exists: {os.path.exists(t) if t else False}")
    print(f"        model={push_cc.model_for(j)!r} "
          f"effort={push_cc.effort_for(j)!r} context={push_cc.context_for(j)}")
if not joined:
    fails.append("no tmux pane joined to a claude agent by pid")

print("\n== verdict ==")
if fails:
    print("FAIL")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("PASS -- tmux can stand in for every herdr call push_cc makes.")
