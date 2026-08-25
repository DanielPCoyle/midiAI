#!/usr/bin/env python3
"""Ableton Push 2 as an AI command center, driven by herdr.

Top pad row (notes 92-99) = up to 8 herdr agents, left to right.
  tap    -> focus that agent's pane
  colour -> green done / yellow working / red blocked / white unknown / off empty

Row 2 (84-91) approve / row 3 (76-83) deny, per column. Lit only while that
agent is blocked, and ignored otherwise, so a stray press cannot answer a
prompt that is not there.

Bottom row (36-43) = MACROS, inserted into whichever agent herdr reports
focused. They do not submit -- you read it, then press enter.

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
# repeat. space is Claude's stock voice:pushToTalk, so every session already has
# it and there is no keybindings.json to keep in sync.
# ponytail: only fires while a pad is physically held, so a stray space needs a
# deliberate press. If that ever bites, bind a control char and send that byte.
PTT_KEY = " "
REPEAT_S = 0.06  # must stay under Claude's 120ms release timer
HOLD_S = 0.25    # pad down longer than this is a hold, not a tap
POLL_S = 0.5
SLOTS = 8

PAD_NOTES = list(range(92, 100))     # top pad row, left -> right = agents
APPROVE_NOTES = list(range(84, 92))  # row below: answer that agent's prompt yes
DENY_NOTES = list(range(76, 84))     # row below that: answer no
MACRO_NOTES = list(range(36, 44))    # bottom row: canned prompts
TAB_CCS = list(range(102, 110))    # buttons above the display
MARK_CCS = list(range(20, 28))     # buttons directly above the pads -> focus marker

# Palette indices guaranteed by the Ableton Push 2 spec, and animation channels.
BLACK, WHITE, GREEN, RED, YELLOW = 0, 122, 126, 127, 8
BLUE = 125  # ponytail: not in the spec's guaranteed set; worst case it is the
            # wrong hue, which costs nothing. Swap if it reads badly.
STATIC, PULSE, BLINK = 0, 9, 14

STATUS = {
    "working": (YELLOW, PULSE),
    "blocked": (RED, BLINK),
    "idle": (GREEN, STATIC),
}
UNKNOWN = (WHITE, STATIC)
EMPTY = (BLACK, STATIC)

# Claude's Confirmation context binds y/enter to yes and escape/n to no. We only
# ever send these while herdr reports the agent blocked.
YES, NO = "y", "n"

# Bottom row, left to right. These INSERT and do not submit: the only way to
# learn a pad is to press it, and a surface you explore by touching must not
# fire irreversible things on contact. Hit enter yourself once you have read it.
MACROS = [
    ("continue",   "continue"),
    ("tests",      "run the tests and report what fails"),
    ("review",     "/code-review"),
    ("commit",     "commit this"),
    ("why",        "explain what you just did and why"),
    ("recap",      "stop and summarise where you are"),
    ("handoff",    "/handoff"),
    ("diff",       "show me the diff"),
]


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


def blocked_agent(slot, slots, by_id):
    """The agent on this slot, but only if it is actually waiting on an answer."""
    a = by_id.get(slots.get(slot))
    return a if a and a.get("agent_status") == "blocked" else None


def colour_for(agent):
    if agent is None:
        return EMPTY
    return STATUS.get(agent.get("agent_status"), UNKNOWN)


def answer(slot, key, slots, by_id):
    a = blocked_agent(slot, slots, by_id)
    if not a:
        return False   # nothing to answer; a stray press must not type into a prompt
    print(f"answering {key!r} -> {a['terminal_id']}", flush=True)
    herdr("agent", "send", a["terminal_id"], key)
    return True


# ---------------------------------------------------------------- push

class Push:
    def __init__(self, mido, inp, out):
        self.mido, self.inp, self.out = mido, inp, out
        self.painted = {}

    def pad(self, slot, colour, anim):
        self.note("pad", slot, PAD_NOTES[slot], colour, anim)

    def note(self, kind, slot, note, colour, anim=STATIC):
        self._send(kind, slot, self.mido.Message(
            "note_on", channel=anim, note=note, velocity=colour))

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
            self.note("ok", s, APPROVE_NOTES[s], BLACK)
            self.note("no", s, DENY_NOTES[s], BLACK)
            self.note("macro", s, MACRO_NOTES[s], BLACK)
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

    debug = "--debug" in sys.argv
    slots, talk_slot, pad_down, next_key, next_poll, sent = {}, None, None, 0.0, 0.0, 0
    by_id, focused = {}, None
    print(f"connected to {name}. ctrl-c to quit.", flush=True)
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
                    push.pad(s, *((RED, STATIC) if s == talk_slot else (colour, anim)))
                    push.cc("tab", s, TAB_CCS, colour if a else BLACK)
                    push.cc("mark", s, MARK_CCS,
                            WHITE if a and a.get("focused") else BLACK)
                    # approve/deny light only when there is something to answer
                    blocked = bool(a) and a.get("agent_status") == "blocked"
                    push.note("ok", s, APPROVE_NOTES[s], GREEN if blocked else BLACK)
                    push.note("no", s, DENY_NOTES[s], RED if blocked else BLACK)
                    push.note("macro", s, MACRO_NOTES[s],
                              BLUE if s < len(MACROS) else BLACK)
                focused = next((a["terminal_id"] for a in live if a.get("focused")), None)

            for msg in inp.iter_pending():
                if debug and msg.type not in ("clock", "active_sensing"):
                    what = (f"CC {msg.control}={msg.value}" if msg.type == "control_change"
                            else f"note {msg.note} v{msg.velocity}"
                            if msg.type in ("note_on", "note_off") else msg.type)
                    print(f"  raw: {what}", flush=True)
                if msg.type in ("note_on", "note_off") and msg.note in PAD_NOTES:
                    slot = PAD_NOTES.index(msg.note)
                    if msg.type == "note_on" and msg.velocity:
                        if slots.get(slot):
                            pad_down = (slot, now)
                    elif slot == talk_slot:
                        print(f"pad {slot} released, {sent} keys sent", flush=True)
                        talk_slot, pad_down = None, None
                    elif pad_down and pad_down[0] == slot:
                        herdr("agent", "focus", slots[slot])   # short press = focus
                        pad_down = None
                elif msg.type == "note_on" and msg.velocity and msg.note in APPROVE_NOTES:
                    answer(APPROVE_NOTES.index(msg.note), YES, slots, by_id)
                elif msg.type == "note_on" and msg.velocity and msg.note in DENY_NOTES:
                    answer(DENY_NOTES.index(msg.note), NO, slots, by_id)
                elif msg.type == "note_on" and msg.velocity and msg.note in MACRO_NOTES:
                    i = MACRO_NOTES.index(msg.note)
                    if i < len(MACROS) and focused:
                        label, text = MACROS[i]
                        print(f"macro {label!r} -> {focused}", flush=True)
                        herdr("agent", "send", focused, text)

            if pad_down and talk_slot is None and now - pad_down[1] >= HOLD_S:
                # No focus call: Claude reads its own pty, so a background agent
                # hears this while you keep watching another one.
                talk_slot, next_key, sent = pad_down[0], 0.0, 0
                print(f"pad {talk_slot} held -> talking to {slots[talk_slot]}", flush=True)

            if talk_slot is not None and now >= next_key:
                next_key, sent = now + REPEAT_S, sent + 1
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

    # approve/deny must be inert unless herdr says that agent is blocked
    by_id = {"x": a("x", "idle"), "y": a("y", "blocked")}
    slots = {0: "x", 1: "y"}
    assert blocked_agent(0, slots, by_id) is None      # idle -> no
    assert blocked_agent(1, slots, by_id)["terminal_id"] == "y"
    assert blocked_agent(7, slots, by_id) is None      # empty slot -> no
    assert answer(0, YES, slots, by_id) is False       # never sends to a non-blocked agent

    assert len(MACROS) <= SLOTS, "bottom row only has 8 pads"
    assert not any(t.endswith("\r") for _, t in MACROS), "macros must not self-submit"
    assert len({n for n in PAD_NOTES + APPROVE_NOTES + DENY_NOTES + MACRO_NOTES}) == 32
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
