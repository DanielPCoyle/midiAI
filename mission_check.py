#!/usr/bin/env python3
"""MIDI-001 — push_cc's data path against real tmux panes.

Everything the poll loop computes each tick, minus the MIDI and USB I/O:
agents -> slots -> the panel columns, the focus view, and the question sweep.
If this is right, the only untested part of the loop is talking to the device.

    python3 mission_check.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import push_cc

fails = []


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        fails.append(name)


print("== backend ==")
check("default backend is tmux", push_cc.BACKEND == "tmux", push_cc.BACKEND)

print("\n== agents() -> the live list ==")
live = push_cc.agents()
check("agents found in tmux", len(live) >= 2, f"{len(live)} agents")
for a in live:
    print(f"      {a['terminal_id']}  {a['agent_status']:<8} {a['cwd']}")
check("every agent has a terminal id", all(a.get("terminal_id") for a in live))
check("statuses are ones push_cc knows",
      all(a["agent_status"] in ("idle", "working", "blocked", "unknown") for a in live),
      str({a["agent_status"] for a in live}))

print("\n== assign() -> slots, the thing muscle memory depends on ==")
slots = push_cc.assign(live, {})
check("agents got slots", len(slots) == len(live), str(slots))
first = dict(slots)
# an agent dying must not shuffle the survivors
survivors = [a for a in live if a["terminal_id"] != live[0]["terminal_id"]]
after = push_cc.assign(survivors, dict(slots))
kept = all(after.get(s) == t for s, t in first.items()
           if t != live[0]["terminal_id"])
check("a death does not shuffle the others", kept, f"{first} -> {after}")

print("\n== panel_col() -> one screen column per agent ==")
by_id = {a["terminal_id"]: a for a in live}
for slot, tid in sorted(slots.items()):
    col = push_cc.panel_col(by_id[tid])
    check(f"slot {slot} column renders",
          col is not None and set(col) >= {"name", "status", "model", "context"},
          str(col))

print("\n== colour_for() -> what the pads actually light ==")
for tid in slots.values():
    c = push_cc.colour_for(by_id[tid])
    # (colour, animation) -- the pads pulse and blink, so it was never one int
    check(f"{tid} has a pad colour", isinstance(c, tuple) and len(c) == 2
          and all(isinstance(n, int) for n in c), str(c))
check("no agent is EMPTY", all(push_cc.colour_for(by_id[t]) != push_cc.EMPTY
                              for t in slots.values()))

print("\n== pane_summary() -> the scrape the focus view lives on ==")
tid = live[0]["terminal_id"]
summary = push_cc.pane_summary(by_id[tid], push_cc.SWEEP_LINES)
check("summary came back", bool(summary), str(list(summary))[:90])
check("pane lines were read", len(summary.get("lines") or []) > 0,
      f"{len(summary.get('lines') or [])} lines")
check("scrape keys are the ones focus_info wants",
      set(summary) >= {"say", "act", "opts", "sel", "lines", "tldr", "pending"},
      str(sorted(summary)))

print("\n== focus_info() -> the whole focus view payload ==")
info = push_cc.focus_info(0, by_id[tid], summary)
check("focus payload renders",
      set(info) >= {"slot", "name", "model", "effort", "status", "opts", "lines"},
      str(sorted(info)))
check("context fraction is sane", 0.0 <= info["context"] <= 1.0, str(info["context"]))

print("\n== sweep_panes() -> who is waiting on a human ==")
asking = push_cc.sweep_panes(slots, by_id, current=0)
check("sweep runs and returns a set", isinstance(asking, set), str(asking))

print("\n== follow_focus() -> the Push follows the terminal ==")
focused = next((a["terminal_id"] for a in live if a.get("focused")), None)
print(f"      tmux reports focused = {focused!r} (detached session => None is correct)")
check("nothing focused leaves the seat alone",
      push_cc.follow_focus(None, slots, 1) == 1)
check("a focused pane moves the seat",
      push_cc.follow_focus(slots[0], slots, 1) == 0, f"{slots}")

print("\nOVERALL:", "PASS" if not fails else f"FAIL ({', '.join(fails)})")
sys.exit(1 if fails else 0)
