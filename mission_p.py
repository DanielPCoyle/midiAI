#!/usr/bin/env python3
"""MIDI-002 — does `claude agents --json` call a prose question `waiting`?

herdr cannot: every blocked rule in its claude.toml needs permission-dialog
chrome, so an agent asking "which approach?" reads as idle. If Claude Code's
own accounting says `waiting`, this backend sees a stuck agent that herdr
misses, and the docs should say so.

Drives a live pane, then correlates the status against what the pane draws.
"""
import json
import subprocess
import sys
import time

PANE = "p"
PROMPT = ("Reply with exactly one sentence: a plain-prose question asking me "
          "whether you should use approach A or approach B. Do NOT use the "
          "AskUserQuestion tool, do not offer a numbered list, do not use any "
          "widget. Just the sentence, then stop and wait.")


def tmux(*a):
    return subprocess.run(["tmux", *a], capture_output=True, text=True).stdout


def status_of(pid):
    try:
        rows = json.loads(subprocess.run(["claude", "agents", "--json"],
                                         capture_output=True, text=True).stdout)
    except json.JSONDecodeError:
        return None, ""
    for a in rows:
        if a.get("pid") == pid:
            return a.get("status"), a.get("waitingFor", "")
    return None, ""


pid = int(tmux("display", "-p", "-t", PANE, "#{pane_pid}").strip())
print(f"pane pid {pid}\n")

subprocess.run(["tmux", "send-keys", "-l", "-t", PANE, "--", PROMPT])
time.sleep(0.4)
subprocess.run(["tmux", "send-keys", "-t", PANE, "Enter"])

asked_at = None
for i in range(40):
    time.sleep(3)
    st, wf = status_of(pid)
    pane = tmux("capture-pane", "-p", "-t", PANE)
    # the caret on a numbered option is what push_cc calls a question
    asking = "approach" in pane.lower() and "?" in pane
    print(f"{i*3:3}s status={st!r:10} waitingFor={wf!r:16} question_on_screen={asking}",
          flush=True)
    if asking and asked_at is None:
        asked_at = i * 3
    if asking and st is not None:
        # give it a few more polls once the question is up, then decide
        if i * 3 - asked_at >= 12:
            break

print()
st, wf = status_of(pid)
pane = tmux("capture-pane", "-p", "-t", PANE)
print("--- what the pane shows ---")
print("\n".join(pane.splitlines()[-14:]))
print(f"\n--- verdict ---\nfinal status={st!r} waitingFor={wf!r}")
print("A prose question IS visible to claude agents --json"
      if st == "waiting" else
      "A prose question is NOT reported as waiting -- sweep_panes stays load-bearing")
