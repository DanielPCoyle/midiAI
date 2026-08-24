#!/usr/bin/env python3
"""Ableton Push 2 as an AI command center, driven by herdr.

Top pad row (notes 92-99) = up to 8 herdr agents, left to right.
  tap    -> focus that agent's pane
  colour -> green done / yellow working / red blocked / white unknown / off empty

Tab buttons above the display (CC 102-109) = push-to-talk for that column.
  hold   -> drive that agent's own voice:pushToTalk. Claude records, transcribes
            and submits on release. Needs sox and voiceEnabled.

  python3 push_cc.py             run it (Push must be in User mode)
  python3 push_cc.py --list      dump agents, no hardware needed
  python3 push_cc.py --selftest  pure-logic asserts, no hardware needed
"""
import json
import subprocess
import sys
import time

PORT_NAME = "Ableton Push 2 User Port"
# Terminals have no key-up event, so Claude Code infers "still holding" from key
# auto-repeat and calls it released after ~120ms of silence. We imitate the
# repeat. ctrl+y is one byte, so herdr ships it as literal text -- no key-name
# table on either side to disagree with us.
# ponytail: bound in ~/.claude/keybindings.json; move both if ctrl+y ever clashes.
PTT_KEY = "\x19"
REPEAT_S = 0.06  # must stay under Claude's 120ms release timer
POLL_S = 0.5
SLOTS = 8

PAD_NOTES = list(range(92, 100))   # top pad row, left -> right
TAB_CCS = list(range(102, 110))    # buttons above the display
MARK_CCS = list(range(20, 28))     # buttons directly above the pads -> focus marker

# Palette indices guaranteed by the Ableton Push 2 spec, and animation channels.
BLACK, WHITE, GREEN, RED, YELLOW = 0, 122, 126, 127, 8
STATIC, PULSE, BLINK = 0, 9, 14

STATUS = {
    "working": (YELLOW, PULSE),
    "blocked": (RED, BLINK),
    "idle": (GREEN, STATIC),
}
UNKNOWN = (WHITE, STATIC)
EMPTY = (BLACK, STATIC)


# ---------------------------------------------------------------- herdr

def herdr(*args):
    out = subprocess.run(["herdr", *args], capture_output=True, text=True, timeout=10)
    return out.stdout.strip()


def agents():
    """Live herdr agents, or [] if herdr is unreachable."""
    try:
        payload = json.loads(herdr("agent", "list") or "{}")
    except (json.JSONDecodeError, OSError, subprocess.SubprocessError):
        return []
    return payload.get("result", {}).get("agents", [])


def assign(live, slots):
    """Pin each agent to a slot index. Slots stay put until an agent dies, so
    muscle memory survives a sibling exiting."""
    ids = [a["terminal_id"] for a in live]
    for slot, tid in list(slots.items()):
        if tid not in ids:
            del slots[slot]
    taken = set(slots.values())
    for tid in ids:
        if tid in taken:
            continue
        free = next((s for s in range(SLOTS) if s not in slots), None)
        if free is None:
            break  # ponytail: 9th+ agent is invisible. 8 pads, 8 agents.
        slots[free] = tid
    return slots


def colour_for(agent):
    if agent is None:
        return EMPTY
    return STATUS.get(agent.get("agent_status"), UNKNOWN)


# ---------------------------------------------------------------- push

class Push:
    def __init__(self, mido, inp, out):
        self.mido, self.inp, self.out = mido, inp, out
        self.painted = {}

    def pad(self, slot, colour, anim):
        self._send("pad", slot, self.mido.Message(
            "note_on", channel=anim, note=PAD_NOTES[slot], velocity=colour))

    def cc(self, kind, slot, ccs, colour, anim=STATIC):
        self._send(kind, slot, self.mido.Message(
            "control_change", channel=anim, control=ccs[slot], value=colour))

    def _send(self, kind, slot, msg):
        # Push redraws on every message; only send on change so animations
        # don't restart their phase every poll.
        key = (kind, slot)
        sig = (msg.channel, getattr(msg, "velocity", None) or getattr(msg, "value", None))
        if self.painted.get(key) == sig:
            return
        self.painted[key] = sig
        self.out.send(msg)

    def blank(self):
        for s in range(SLOTS):
            self.pad(s, BLACK, STATIC)
            self.cc("tab", s, TAB_CCS, BLACK)
            self.cc("mark", s, MARK_CCS, BLACK)


def run():
    import mido

    name = next((n for n in mido.get_output_names() if PORT_NAME in n), None)
    if not name:
        sys.exit(f"{PORT_NAME!r} not found. Plug the Push in and press its User button.\n"
                 f"seen: {mido.get_output_names()}")
    if subprocess.run(["which", "rec"], capture_output=True).returncode:
        print("warning: no sox on PATH -- voice will no-op: brew install sox", file=sys.stderr)

    inp = mido.open_input(name)
    out = mido.open_output(name)
    push = Push(mido, inp, out)
    push.blank()
    out.send(mido.Message("start"))  # spec: animations don't run until a start arrives

    slots, talk_slot, next_key, next_poll = {}, None, 0.0, 0.0
    print(f"connected to {name}. ctrl-c to quit.")
    try:
        while True:
            now = time.monotonic()
            if now >= next_poll:
                next_poll = now + POLL_S
                live = agents()
                assign(live, slots)
                by_id = {a["terminal_id"]: a for a in live}
                for s in range(SLOTS):
                    a = by_id.get(slots.get(s))
                    colour, anim = colour_for(a)
                    push.pad(s, colour, anim)
                    if s != talk_slot:
                        push.cc("tab", s, TAB_CCS, colour if a else BLACK)
                    push.cc("mark", s, MARK_CCS,
                            WHITE if a and a.get("focused") else BLACK)

            for msg in inp.iter_pending():
                if msg.type == "note_on" and msg.velocity and msg.note in PAD_NOTES:
                    tid = slots.get(PAD_NOTES.index(msg.note))
                    if tid:
                        herdr("agent", "focus", tid)
                elif msg.type == "control_change" and msg.control in TAB_CCS:
                    slot = TAB_CCS.index(msg.control)
                    if msg.value and talk_slot is None and slots.get(slot):
                        # No focus call: Claude reads its own pty, so a background
                        # agent hears this while you keep watching another one.
                        talk_slot, next_key = slot, 0.0
                        push.cc("tab", slot, TAB_CCS, RED, BLINK)
                    elif not msg.value and slot == talk_slot:
                        # Just stop repeating; the silence is the release.
                        talk_slot = None
                        push.cc("tab", slot, TAB_CCS, BLACK)

            if talk_slot is not None and now >= next_key:
                next_key = now + REPEAT_S
                herdr("agent", "send", slots[talk_slot], PTT_KEY)

            time.sleep(0.005)
    except KeyboardInterrupt:
        pass
    finally:
        push.painted.clear()
        push.blank()


# ---------------------------------------------------------------- checks

def selftest():
    a = lambda t, s="idle": {"terminal_id": t, "agent_status": s}
    slots = {}
    assign([a("x"), a("y"), a("z")], slots)
    assert slots == {0: "x", 1: "y", 2: "z"}, slots

    assign([a("x"), a("z")], slots)          # y dies
    assert slots == {0: "x", 2: "z"}, slots  # z must not slide left

    assign([a("x"), a("z"), a("w")], slots)  # new agent takes the hole
    assert slots == {0: "x", 1: "w", 2: "z"}, slots

    over = [a(f"t{i}") for i in range(12)]
    assert len(assign(over, {})) == SLOTS

    assert colour_for(None) == EMPTY
    assert colour_for(a("x", "working")) == (YELLOW, PULSE)
    assert colour_for(a("x", "blocked")) == (RED, BLINK)
    assert colour_for(a("x", "idle")) == (GREEN, STATIC)
    assert colour_for(a("x", "banana")) == UNKNOWN
    assert colour_for({}) == UNKNOWN
    print("ok")


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--selftest":
        selftest()
    elif arg == "--list":
        live = agents()
        slots = assign(live, {})
        by_id = {a["terminal_id"]: a for a in live}
        for s in range(SLOTS):
            a = by_id.get(slots.get(s))
            print(f"pad {s}: " + (f"{a['agent_status']:<8} {a['cwd']}" if a else "-"))
    else:
        run()
