#!/usr/bin/env python3
"""Ableton Push 2 as an AI command center, driven by herdr.

Buttons under the display (CC 20-27) = up to 8 herdr agents, left to right.
  tap    -> drive that agent
  hold   -> talk to it, via that agent's own voice:pushToTalk
  colour -> green done / yellow working / red blocked / white the one you drive
  Page left/right step between them.

The whole 8x8 pad grid (36-99) = shortcuts. Tap one to insert its text into
the current agent; Play submits. Hold one and it opens the mic instead, so you
dictate the rest of the sentence onto what it just typed. Hold Shift and tap one to read what it does without running it.
Hold Record or Select and tap one to save
whatever is in the prompt onto it. macros.json is hand-editable.

Buttons above the display (CC 102-109) pick the view: 1 focus, 2 agents,
3 usage, 4 shortcuts. White is the one you are on.

The 960x160 screen names each column, so two checkouts of the same repo are
told apart by the tail of their terminal id. Missing pyusb just means no
screen; the buttons carry on.

When any agent starts asking something the screen jumps to the focus view on
it, once, on the edge -- navigate away and it will not drag you back. Its
button blinks red, which herdr's status alone would never tell you. Answer a
select widget with the arrows or the master encoder, then Play.

Closing asks first, on the Push: the screen turns red with the session name
and the button row becomes a pair -- button 1 green for yes, button 2 blinking
red for no. Play and Stop Clip answer too, being enter and escape already. An
unanswered request expires after ten seconds, and none of it reaches the
terminal.

Add Device splits and starts a new claude there in auto mode, in the current
agent's directory. Add Track makes a worktree off it, named from whatever is
typed in the prompt. Duplicate forks the current agent. Solo pins the screen.

Convert runs /compact, New /clear, Quantize /model, Double Loop /effort and
Metronome /mcp. Stop Clip sends escape. Mute sends tab, which accepts an
autocomplete. Undo clears what is typed. Arrows and
the master encoder pass through as arrow keys. The tempo encoder scrolls; each
of the eight encoders scrolls the agent above it, and touching one peeks at it.

  python3 push_cc.py             run it (Push must be in User mode)
  python3 push_cc.py --list      dump agents, no hardware needed
  python3 push_cc.py --selftest  pure-logic asserts, no hardware needed
  python3 push_cc.py --debug     log every control you press, with its number
  python3 mapui.py               edit the 64 pads in a browser, live
"""
import json
import os
import re
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
TAIL_BYTES = 256 * 1024   # transcripts run to megabytes; only the end matters
POLL_S = 0.5
SLOTS = 8


# The whole grid is shortcuts now. Sessions live on the buttons under the
# display, where there are exactly eight of them and no row arithmetic.
MACRO_ROWS = 8
MACRO_NOTES = list(range(36, 100))
MACRO_SLOTS = len(MACRO_NOTES)
TAB_CCS = list(range(102, 110))    # buttons above the display -> view switcher
# focus first: it is the one you actually watch, and view defaults to 0 so
# it is also what comes up on start
VIEWS = ["focus", "agents", "usage", "macros"]
SESSION_CCS = list(range(20, 28))  # under the display: tap selects, hold talks
PLAY_CC = 85                       # transport Play -> enter, submits what is typed
TEMPO_CC = 14                      # tempo encoder -> scroll the focus view
VOLUME_CC = 79                     # master encoder -> up/down arrows at the agent
ENC_CCS = list(range(71, 79))      # the 8 encoders, one over each agent column
ENC_TOUCH = list(range(0, 8))      # touching one is a note, not a CC
SHIFT_CC = 49                      # held modifier, the standard Push idiom
SOLO_CC = 61                       # pin the screen so questions stop moving it
DUPLICATE_CC = 88                  # fork the current agent into a new session
PAGE_CCS = {62: -1, 63: 1}         # page left/right -> previous/next session
CONFIRM_S = 10                     # a close request that goes unanswered expires
YES_SLOT, NO_SLOT = 0, 1           # while confirming, the first two session buttons
MAX_STEPS = 8                      # a fast spin must not fire fifty keypresses
UP, DOWN = "\x1b[A", "\x1b[B"   # also used to walk a select widget's caret

# Straight through to the agent as real arrow keys. Claude already decides what
# they mean in context -- caret in a dialog, history at an empty prompt, cursor
# in text -- and second-guessing that from here only ever gets it wrong.
ARROW_CCS = {44: "\x1b[D", 45: "\x1b[C", 46: UP, 47: DOWN}
RECORD_CC = 86                     # hold Record, tap a pad: saves the prompt to it
SELECT_CC = 48                     # Select does the same; either hand can reach one
ARM_CCS = (RECORD_CC, SELECT_CC)
DELETE_CC = 118                    # Delete -> close the current agent's pane
ADD_DEVICE_CC = 52                 # Add Device -> split, new claude in auto mode
ADD_TRACK_CC = 53                  # Add Track -> new worktree
BROWSE_CC = 111                    # Browse -> this repo's pull requests, in a browser

UNDO_CC = 119                      # Undo -> backspace the prompt empty
MUTE_CC = 60                       # Mute -> tab, which is autocomplete:accept
FREED_CCS = []                     # unmapped buttons: blank them or they stay lit

# Hold Automate, tap a pad: arms that pad's row as a chain. Play runs it, Stop
# discards it. Two gestures on purpose -- a chain is fire-and-forget by
# definition, and this surface already learned that lesson the hard way.
AUTOMATE_CC = 89
CHAIN_GRACE_S = 5.0    # a step that never reports working finished between polls
CHAIN_SETTLE = 2       # idle polls in a row before the next step goes in

# The touchstrip is the only absolute control on the surface. An encoder is
# relative and can only ever nudge; a strip you can slam straight to max.
EFFORTS = ("low", "medium", "high", "xhigh", "max")
PITCH_SPAN = 8192 * 2               # mido pitchwheel runs -8192..8191
# Confirmed against the hardware: the strip brackets its pitchbend with a touch
# note, v127 finger on and v0 off, so the release is a real event and not a
# timer we guessed at. Nothing is sent until that v0 arrives.
STRIP_NOTE = 12
SYSEX_HEAD = [0x00, 0x21, 0x1D, 0x01, 0x01]   # Ableton, device 1, model 1
MODE_CC, MODE_LIVE, MODE_USER = 0x0A, 0, 1
STRIP_CFG_CC, STRIP_LED_CC = 0x17, 0x19
# The spec's power-up default. Bits 5 and 6 are autoreturn, to centre -- which
# is exactly the spring-back strip_pick has to throw away, so the empirical
# find and the document agree. Bit 0 hands the LEDs to us; everything else is
# left alone, including the pitch bend the rest of this depends on.
STRIP_CFG_DEFAULT = 0x68
# Bit 0 takes the LEDs, and bit 1 is the one that actually matters: with "host
# sends" left on values, the spec ignores every Set Touch Strip LEDs command
# silently, which reads exactly like a dead wire. Bit 2 stays put -- the strip
# must keep sending pitch bend, which is what the rest of this reads.
STRIP_CFG_HOST = STRIP_CFG_DEFAULT | 0x03
STRIP_LEDS = 31                # LED 0 is the bottom, LED 30 the top
STRIP_ON, STRIP_OFF = 7, 0     # 3 bits each: 7 is full, 0 is dark

# Raw keys, sent as-is. Separate from COMMAND_CCS because nothing here submits
# and that table asserts everything in it does.
KEY_CCS = {MUTE_CC: ("tab", "\t")}
STOP_CC = 29                       # Stop Clip -> escape, interrupts the agent
BACKSPACE, ESCAPE = "\x7f", "\x1b"
DELETE_FWD = "\x1b[3~"            # forward delete, for text past the cursor

# Buttons that send a fixed string to the current agent. The newline is part of
# the entry: not everything here submits.
COMMAND_CCS = {
    35: ("convert", "/compact\r"),
    87: ("new", "/clear\r"),
    116: ("quantize", "/model\r"),
    117: ("dbloop", "/effort\r"),   # like /model, a widget the arrows can drive
    9: ("metro", "/mcp\r"),
}
_HERE = os.path.dirname(os.path.abspath(__file__))
MACRO_FILE = os.path.join(_HERE, "macros.json")
# mapui runs in its own process and cannot see which session the Push has
# selected, so publish it. Written only when it changes.
TARGET_FILE = os.path.join(_HERE, ".target.json")
SCRAPE_LINES = "400"               # how far back the focus view can scroll
SWEEP_LINES = "40"                 # enough to spot a question at the foot of a pane
SWEEP_S = 1.5                      # every agent, throttled: 8 reads is not free

# Palette indices guaranteed by the Ableton Push 2 spec, and animation channels.
BLACK, WHITE, GREEN, RED, YELLOW = 0, 122, 126, 127, 8
# Shift and Record are white-only buttons: the value is brightness, not a
# palette index, so they need their own two levels.
DIM, BRIGHT = 20, 127
BLUE = 125  # ponytail: not in the spec's guaranteed set; worst case it is the
            # wrong hue, which costs nothing. Swap if it reads badly.

# Palette indices the Push 2 spec guarantees, plus the blue above. Any 0-127
# index works on the hardware; these are the ones worth offering by name.
PALETTE = [("red", RED), ("orange", 3), ("yellow", YELLOW), ("green", GREEN),
           ("blue", BLUE), ("white", WHITE)]
DEFAULT_LABELS = [{"name": "prompt", "colour": BLUE}]
STATIC, PULSE, BLINK = 0, 9, 14

STATUS = {
    "working": (YELLOW, PULSE),
    "blocked": (RED, BLINK),
    "idle": (GREEN, STATIC),
}
UNKNOWN = (WHITE, STATIC)
EMPTY = (BLACK, STATIC)

ENTER = "\r"

# Bottom row, left to right. These INSERT and do not submit: the only way to
# learn a pad is to press it, and a surface you explore by touching must not
# fire irreversible things on contact. Hit enter yourself once you have read it.
DEFAULT_MACROS = [
    "continue",
    "run the tests and report what fails",
    "/code-review",
    "commit this",
    "explain what you just did and why",
    "stop and summarise where you are",
    "/handoff",
    "show me the diff",
]


def label_for(text):
    """Pads need a name that fits, and typing one by hand is a chore nobody
    will do. Two words off the front is close enough to recognise."""
    if not text:
        return ""
    words = text.split()
    short = " ".join(words[:2])
    return short if len(short) <= 14 else short[:13] + "\u2026"


def load_macros():
    """macros.json if present, defaults otherwise. Hand-editable on purpose.

    Accepts a bare list, which is what the file used to be, as well as the
    {labels, pads} form -- an older file must not lose its pads to a schema
    it was written before."""
    try:
        with open(MACRO_FILE) as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError):
        raw = DEFAULT_MACROS
    if isinstance(raw, dict):
        labels, pads = raw.get("labels") or DEFAULT_LABELS, raw.get("pads") or []
    else:
        labels, pads = DEFAULT_LABELS, raw or []
    by_name = {l["name"]: l.get("colour", BLUE) for l in labels if l.get("name")}
    out = []
    for entry in pads[:MACRO_SLOTS]:
        if isinstance(entry, str):
            entry = {"text": entry}
        text = (entry or {}).get("text", "")
        if not text:
            out.append(None)
            continue
        colour = by_name.get(entry.get("tag"), entry.get("colour", BLUE))
        out.append({"label": entry.get("label") or label_for(text),
                    "text": text,
                    "colour": int(colour) & 0x7F,
                    "tag": entry.get("tag"),
                    # off unless asked for: a pad that fires on contact is how
                    # /handoff went into a live session three times
                    "submit": bool(entry.get("submit"))})
    return labels, out + [None] * (MACRO_SLOTS - len(out))


def save_macros(macros, labels=None):
    tmp = MACRO_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"labels": labels if labels is not None else LABELS,
                   "pads": list(macros)}, f, indent=2)
    os.replace(tmp, MACRO_FILE)   # never leave a half-written file behind


LABELS, MACROS = load_macros()
_macros_mtime = 0.0


def publish_target(agent):
    """Tell mapui which session the Push is pointed at."""
    payload = {} if not agent else {
        "terminal_id": agent.get("terminal_id"),
        "name": os.path.basename(agent.get("cwd", "")) or "?",
        "status": agent.get("agent_status"),
    }
    tmp = TARGET_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f)
    os.replace(tmp, TARGET_FILE)


def reload_macros():
    """Pick up edits from mapui.py without a restart. Cheap: one stat a poll."""
    global _macros_mtime
    try:
        mtime = os.path.getmtime(MACRO_FILE)
    except OSError:
        return False
    if mtime == _macros_mtime:
        return False
    _macros_mtime = mtime
    LABELS[:], MACROS[:] = load_macros()
    return True


# ---------------------------------------------------------------- herdr

def herdr(*args):
    """herdr reports failure as a JSON error on stdout with exit 0, so a helper
    that only returns stdout swallows it. Add Device was failing silently for
    hours that way."""
    out = subprocess.run(["herdr", *args], capture_output=True, text=True, timeout=10)
    text = out.stdout.strip()
    if '"error"' in text:
        try:
            err = json.loads(text)["error"]
            print(f"herdr {args[0]} {args[1]}: {err.get('code')}: "
                  f"{err.get('message', '')[:120]}", file=sys.stderr, flush=True)
        except (json.JSONDecodeError, KeyError, IndexError):
            print(f"herdr {' '.join(args[:2])}: {text[:160]}", file=sys.stderr, flush=True)
    elif out.stderr.strip():
        print(f"herdr {' '.join(args[:2])}: {out.stderr.strip()[:160]}",
              file=sys.stderr, flush=True)
    return text


def start_agent(where, split="right"):
    """A new claude in `where`. herdr insists agent names are unique, so take
    the directory's name and number it rather than reusing one."""
    base = os.path.basename(where.rstrip("/")) or "agent"
    for n in range(1, 21):
        name = base if n == 1 else f"{base}-{n}"
        out = herdr("agent", "start", name, "--cwd", where, "--split", split,
                    "--focus", "--", "claude", "--permission-mode", "auto")
        if "agent_name_taken" not in out:
            return name
    return None


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


def slug(text):
    """Prompt text -> a branch name. Typing the name in the prompt and pressing
    the button beats a hardware surface that cannot ask you anything."""
    keep = [c if (c.isalnum() or c in "/_-") else "-" for c in text.strip().lower()]
    name = re.sub(r"-{2,}", "-", "".join(keep)).strip("-/")
    return name[:60] or "push/" + time.strftime("%H%M%S")


def answer_keys(pick, sel):
    """Keys that move a select widget's caret from `sel` to `pick` and commit.

    The lit pads have been painted as answer buttons since the grid became all
    shortcuts, and pressing one has done nothing since -- a button that lights
    and then ignores you is worse than one that never lights. Walking the caret
    is how the encoder already answers; this just aims it at a pad."""
    step = pick - sel
    return (DOWN if step > 0 else UP) * abs(step) + ENTER


def step_slot(current, delta, slots):
    """Next occupied slot, wrapping. Empty pads are not worth stopping on."""
    live = sorted(s for s in slots if slots[s])
    if not live:
        return current
    if current in live:
        return live[(live.index(current) + delta) % len(live)]
    return live[0] if delta > 0 else live[-1]


# ---------------------------------------------------------------- chains

def chain_steps(macros, start):
    """The pads from `start` rightward to the end of its row, empties dropped.

    A row IS the chain. The grid already groups eight pads under one hand and
    mapui already reorders them by dragging, so a sequence needs no storage of
    its own -- and a pad's text lives in exactly one place, which a separate
    sequence file would quietly duplicate."""
    row_end = (start // 8 + 1) * 8
    return [i for i in range(start, row_end) if macros[i]]


def chain_step_done(saw_working, status, waited, idle_polls):
    """True when the step just sent has finished.

    Status lags the send: an agent still reads `idle` for a poll or two after
    its enter lands. Advance on a bare `idle` and every pad in the row fires
    into the same prompt at once. So wait to have seen `working` -- or for the
    grace period, which covers a step that began and ended between two polls --
    and only then count idle polls."""
    if not (saw_working or waited >= CHAIN_GRACE_S):
        return False
    return status == "idle" and idle_polls >= CHAIN_SETTLE


class Chain:
    """One row of pads fired at one agent, in order, advancing as it finishes.

    Pinned to the agent it started on, not to whatever is selected now: you
    have to be able to walk away and watch another session while it runs,
    which is most of the point of having eight of them."""

    def __init__(self, steps, target, slot, name):
        self.steps, self.target, self.slot, self.name = steps, target, slot, name
        self.index, self.phase = 0, "armed"
        self.sent_at, self.saw_working, self.idle_polls = 0.0, False, 0
        self.done_at = 0.0

    @property
    def step(self):
        return self.steps[self.index] if self.index < len(self.steps) else None

    def fire(self, macros, now):
        """Send the current step. Always submits, whatever the pad's own flag
        says: a chain that stops for you to press enter is not a chain.

        Skips pads that have gone empty since the chain was armed -- mapui
        rewrites macros.json live, so the row can change underneath a run.
        False when there is nothing left to send."""
        while self.index < len(self.steps) and not macros[self.steps[self.index]]:
            print(f"chain: pad {self.steps[self.index]} went empty, skipping",
                  flush=True)
            self.index += 1
        if self.index >= len(self.steps):
            return False
        m = macros[self.step]
        herdr("agent", "send", self.target, m["text"] + ENTER)
        self.sent_at, self.saw_working, self.idle_polls = now, False, 0
        print(f"chain {self.index + 1}/{len(self.steps)}: "
              f"{m['label']!r} -> {self.target}", flush=True)
        return True

    def info(self, macros):
        return {"agent": self.name, "slot": self.slot, "index": self.index,
                "phase": self.phase,
                "steps": [{"label": (macros[i] or {}).get("label", "?"),
                           "colour": (macros[i] or {}).get("colour", BLUE)}
                          for i in self.steps]}


_model_cache = {}   # path -> (size, name). Model changes mid-session via /model.


def short_model(raw):
    """claude-haiku-4-5-20251001 -> haiku 4.5"""
    name = re.sub(r"-\d{8}$", "", raw.removeprefix("claude-"))
    fam, _, ver = name.partition("-")
    return f"{fam} {ver.replace('-', '.')}".strip()


def model_for(agent):
    """herdr does not track the model, but the session's own transcript does."""
    path = transcript(agent)
    if not path:
        return ""
    try:
        size = os.stat(path).st_size
    except OSError:
        return ""
    hit = _model_cache.get(path)
    if hit and hit[0] == size:          # file has not grown, model cannot have changed
        return hit[1]
    try:
        with open(path, "rb") as f:
            f.seek(max(0, size - TAIL_BYTES))
            found = re.findall(rb'"model":"([^"]+)"', f.read())
    except OSError:
        return ""
    name = short_model(found[-1].decode()) if found else ""
    _model_cache[path] = (size, name)
    return name


def strip_bar(level, levels=EFFORTS, n=STRIP_LEDS):
    """LED brightnesses bottom to top: a bar as tall as the thinking you asked
    for. A strip that sits dark between touches is a control with no readout,
    and this one already knows the answer. Unknown level reads as dark rather
    than as `low` -- no bar is honest about not knowing, a short one is not."""
    if level not in levels:
        return [STRIP_OFF] * n
    lit = round((levels.index(level) + 1) / len(levels) * n)
    return [STRIP_ON] * lit + [STRIP_OFF] * (n - lit)


def strip_pack(leds):
    """Three bits per LED, two to a byte, the lower LED in the low bits. 31 is
    odd, so the last byte carries the top LED alone."""
    return [leds[i] | (leds[i + 1] << 3 if i + 1 < len(leds) else 0)
            for i in range(0, len(leds), 2)]


def strip_pick(pitch, current):
    """Level under the finger, or `current` when this is the strip's rest
    position.

    The strip springs back to exact centre and reports it as a bend BEFORE the
    release note, so taking 0 at face value commits `high` wherever you
    actually were -- observed on the hardware, every single time. 0 is the
    rest value and never a choice: the middle band runs ~1600 either side of
    it, so refusing one point of 3200 costs nothing you can feel."""
    return current if pitch == 0 else effort_at(pitch)


def effort_at(pitch):
    """Strip position -> one of five levels. Absolute, so the bottom of the
    strip is always `low` and the top always `max`, however you got there --
    that is the whole reason this lives on the strip and not on a knob."""
    frac = (pitch + PITCH_SPAN // 2) / PITCH_SPAN
    return EFFORTS[min(len(EFFORTS) - 1, max(0, int(frac * len(EFFORTS))))]


_effort_cache = {}   # path -> (size, level). /effort changes it mid-session.


def effort_for(agent):
    """The level the session is actually on, which is not the same as the one
    we last asked for: it can be changed from the keyboard too, and a readout
    that quietly reports our own last write would be worse than none."""
    path = transcript(agent) if agent else None   # the strip asks with no agent
    if not path:
        return ""
    try:
        size = os.stat(path).st_size
    except OSError:
        return ""
    hit = _effort_cache.get(path)
    if hit and hit[0] == size:      # not grown, so the level cannot have moved
        return hit[1]
    try:
        with open(path, "rb") as f:
            f.seek(max(0, size - TAIL_BYTES))
            found = re.findall(rb'"effort":"([^"]+)"', f.read())
    except OSError:
        return ""
    level = found[-1].decode() if found else ""
    _effort_cache[path] = (size, level)
    return level


_usage_cache = {}   # path -> (offset, totals). Transcripts only ever append.
_USAGE_FIELDS = {
    "out": rb'"output_tokens":(\d+)',
    # a leading quote is load-bearing: cache_read_input_tokens ends in the same
    # letters, and without it every cache read would be counted twice.
    "inp": rb'"input_tokens":(\d+)',
    "cread": rb'"cache_read_input_tokens":(\d+)',
    "cwrite": rb'"cache_creation_input_tokens":(\d+)',
}


def transcript(agent):
    sid = (agent.get("agent_session") or {}).get("value")
    if not sid:
        return None
    return os.path.expanduser(
        f"~/.claude/projects/{agent.get('cwd', '').replace('/', '-')}/{sid}.jsonl")


def usage_for(agent):
    """Token totals for this session, accumulated incrementally.

    A full re-scan is 2MB a session and we poll twice a second, so only the
    bytes appended since last time are parsed."""
    path = transcript(agent)
    try:
        size = os.stat(path).st_size if path else 0
    except OSError:
        return {}
    off, tot = _usage_cache.get(path, (0, {k: 0 for k in _USAGE_FIELDS}))
    if size < off:                       # truncated or replaced; start over
        off, tot = 0, {k: 0 for k in _USAGE_FIELDS}
    if size > off:
        try:
            with open(path, "rb") as f:
                f.seek(off)
                chunk = f.read()
        except OSError:
            return tot
        cut = chunk.rfind(b"\n") + 1    # never parse a half-written line
        if cut:
            for key, pat in _USAGE_FIELDS.items():
                tot[key] += sum(int(m) for m in re.findall(pat, chunk[:cut]))
            _usage_cache[path] = (off + cut, tot)
    return tot


_ctx_cache = {}     # path -> (size, used)
# Every current model is 1M except Haiku. Checked against the model table
# rather than recalled: assuming 200k put these sessions at 137% full.
CONTEXT_LIMITS = (("haiku", 200_000),)
CONTEXT_DEFAULT = 1_000_000


def context_for(agent):
    """How full this session's context is, and out of what.

    Not the usage totals: those accumulate over the whole session. Context is
    the input side of the most recent request only."""
    path = transcript(agent)
    model = model_for(agent)
    limit = next((v for k, v in CONTEXT_LIMITS if k in model), CONTEXT_DEFAULT)
    if not path:                       # no session id: open(None) is a TypeError
        return 0, limit
    try:
        size = os.stat(path).st_size
    except OSError:
        return 0, limit
    hit = _ctx_cache.get(path)
    if hit and hit[0] == size:
        return hit[1], limit
    try:
        with open(path, "rb") as f:
            f.seek(max(0, size - TAIL_BYTES))
            tail = f.read()
    except OSError:
        return 0, limit
    i = tail.rfind(b'"usage":{')
    used = 0
    if i >= 0:
        # one object, not the file: fields must come from the same request
        chunk = tail[i:i + 600]
        for pat in (_USAGE_FIELDS["inp"], _USAGE_FIELDS["cread"],
                    _USAGE_FIELDS["cwrite"]):
            m = re.search(pat, chunk)
            if m:
                used += int(m.group(1))
    _ctx_cache[path] = (size, used)
    return used, limit


def usage_col(agent):
    if agent is None:
        return None
    u = usage_for(agent)
    return (os.path.basename(agent.get("cwd", "")) or "?",
            u.get("out", 0),
            u.get("cread", 0) + u.get("inp", 0) + u.get("cwrite", 0),
            bool(agent.get("focused")))


# Two different things render as a numbered list, and they answer differently.
# A select widget puts a caret on the highlighted row -- a typed digit does not
# choose there, you arrow to it and press enter. Prose options carry no caret,
# and the digit is simply the reply you type. The caret is the discriminator.
_OPT_RE = re.compile(r"^\s*(❯|>)?\s*(\d+)\.\s+(.+?)\s*$")


def prompt_text(lines):
    """The whole input buffer, not just its first row.

    A long prompt wraps, and the continuation rows are indented under the
    caret. Reading only the caret row truncates it, which quietly shortened
    every macro recorded and every branch name slugged from it."""
    start = next((i for i in range(len(lines) - 1, -1, -1)
                  if lines[i].lstrip().startswith("❯")), None)
    if start is None:
        return ""
    first = lines[start].lstrip()[1:]
    parts = [first.strip()]
    for line in lines[start + 1:]:
        stripped = line.strip()
        if not line.startswith((" ", "\t")) or not stripped:
            break
        if set(stripped) <= set("─━") or stripped[0] in "⏵✔⏸":   # rule or footer
            break
        parts.append(stripped)
    return " ".join(x for x in parts if x).strip()


def turn(value):
    """Push encoders are relative: 1..63 clockwise, 127..65 anticlockwise."""
    return value if value < 64 else value - 128


def pane_summary(agent, depth=SCRAPE_LINES):
    """What this agent is doing, scraped from its rendered pane.

    herdr exposes status but not content, and the pane is the only place the
    question text and its options actually exist."""
    if not agent:
        return {}
    try:
        raw = json.loads(herdr("agent", "read", agent["terminal_id"],
                               "--lines", depth))
        lines = raw["result"]["read"]["text"].splitlines()
    except (json.JSONDecodeError, KeyError, OSError, subprocess.SubprocessError):
        return {}
    body = [l for l in lines if not set(l.strip()) <= set("─━ ")]   # drop rules
    opts, say, act, sel = [], "", "", None
    for line in lines:
        m = _OPT_RE.match(line)
        if m and not line.lstrip().startswith(("⏺", "✻")):
            if m.group(1):
                sel = len(opts)                # caret: this is a select widget
            opts.append((m.group(2), m.group(3)))
        elif line.startswith("⏺"):
            say, opts, sel = line[1:].strip(), [], None   # new answer, stale choice
        elif line.startswith("✻"):
            act = line[1:].strip()
    pending = prompt_text(lines)
    return {"say": say, "act": act, "opts": opts, "sel": sel,
            "lines": body, "pending": "" if opts else pending}


def sweep_panes(slots, by_id, current):
    """Which slots are waiting on a human, and what each is part-way through
    typing.

    Not agent_status for the first: an agent asking a prose question stays
    idle, so herdr never reports it. Only the pane knows."""
    asking = set()
    for slot, tid in slots.items():
        if slot == current or not tid:
            continue
        agent = by_id.get(tid)
        if not agent:
            continue
        summary = pane_summary(agent, SWEEP_LINES)
        if summary.get("opts"):
            asking.add(slot)
    return asking


def focus_info(slot, agent, summary, scroll=0):
    used, limit = context_for(agent)
    return {"slot": slot,
            "name": os.path.basename(agent.get("cwd", "")) or "?",
            "model": model_for(agent),
            "effort": effort_for(agent),
            "status": agent.get("agent_status"),
            **{k: summary.get(k, "") for k in ("act", "say", "pending")},
            "opts": summary.get("opts") or [], "sel": summary.get("sel"),
            "lines": summary.get("lines") or [], "scroll": scroll,
            "context": used / max(1, limit)}


def panel_col(agent):
    """One screen column. Two agents can share a repo name, so show a tail of
    the terminal id -- that is the thing that actually tells them apart.

    No prompt text: a half-written prompt belongs to the focus view."""
    if agent is None:
        return None
    used, limit = context_for(agent)
    return {"name": os.path.basename(agent.get("cwd", "")) or "?",
            "status": agent.get("agent_status"),
            "model": model_for(agent),
            "effort": effort_for(agent),
            "sub": agent.get("terminal_id", "")[-6:],
            "focused": bool(agent.get("focused")),
            "context": used / limit if limit else 0.0}


def colour_for(agent):
    if agent is None:
        return EMPTY
    return STATUS.get(agent.get("agent_status"), UNKNOWN)


# ---------------------------------------------------------------- push

class Push:
    def __init__(self, mido, inp, out):
        self.mido, self.inp, self.out = mido, inp, out
        self.painted = {}
        self.leds = None        # last strip bar sent, so we only resend on change

    def sysex(self, *body):
        self.out.send(self.mido.Message("sysex", data=SYSEX_HEAD + list(body)))

    def midi_mode(self, mode=MODE_USER):
        """A Push that has just been plugged in is in Live mode: everything it
        sends goes to the other port and everything we send it is ignored. The
        screen still draws -- that is USB, not MIDI -- so the surface looks
        half alive, stuck on one session with no button able to move it. That
        is what the User button was for, and asking Ableton to press it for us
        is a poor way to start. Sysex is accepted on both ports in every mode,
        so this lands whichever one the device is currently listening to."""
        self.sysex(MODE_CC, mode)

    def strip_host(self, host=True):
        """Take the strip's LEDs off the device, or hand them back. Must be
        done before any LED write lands, and undone on the way out or the
        strip stays ours in whatever runs next."""
        self.sysex(STRIP_CFG_CC, STRIP_CFG_HOST if host else STRIP_CFG_DEFAULT)
        self.leds = None

    def strip(self, leds):
        """16 bytes down the same wire as every pad, so only on change."""
        if leds == self.leds:
            return
        self.leds = leds
        self.sysex(STRIP_LED_CC, *strip_pack(leds))

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
            self.cc("tab", s, TAB_CCS, BLACK)
            self.cc("session", s, SESSION_CCS, BLACK)
        for i in range(MACRO_SLOTS):
            self.note("macro", i, MACRO_NOTES[i], BLACK)
        self.cc("play", 0, [PLAY_CC], BLACK)
        self.cc("shift", 0, [SHIFT_CC], BLACK)
        for i, cc in enumerate(ARM_CCS):
            self.cc(f"arm{i}", 0, [cc], BLACK)
        for i in range(len(FREED_CCS)):
            self.cc("freed", i, FREED_CCS, BLACK)
        self.strip([STRIP_OFF] * STRIP_LEDS)


def screen():
    """The 960x160 panel, or None if it is unavailable. Never fatal: the pads
    are the product, the screen is the label on it."""
    try:
        import display
        return display, display.Display()
    except Exception as e:                      # no pyusb, no libusb, device busy
        print(f"no display ({e}); pads still work", file=sys.stderr)
        return None, None


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
    push.midi_mode(MODE_USER)   # first of all: in Live mode it cannot hear us
    push.strip_host(True)   # before blank: LED writes go nowhere until we own them
    push.blank()
    out.send(mido.Message("start"))  # spec: animations don't run until a start arrives

    disp_mod, disp = screen()
    debug = "--debug" in sys.argv
    slots, talk_slot, pad_down, next_key, next_poll, sent = {}, None, None, 0.0, 0.0, 0
    by_id, focused, live = {}, None, []   # live persists a hold, unscraped
    shown, frame, view = None, None, 0
    current, summary, arming = 0, {}, False
    scrolls, peek = {}, None        # scroll is per agent; peek is a held finger
    shifted, pinned = False, False
    macro_down, talk_src, previewing = None, None, None
    arm_held = {cc: False for cc in ARM_CCS}   # not `held`: a local below took it
    published = object()   # sentinel: nothing published yet
    closing = None      # (slot, deadline): asked to close, waiting on an answer
    asking, next_sweep, was_asking = set(), 0.0, False
    chain, automating = None, False   # a row of pads queued at one agent
    strip_level, strip_touch = None, False  # uncommitted until the finger lifts
    print(f"connected to {name}. ctrl-c to quit.", flush=True)
    try:
        while True:
            now = time.monotonic()
            # First, before anything that can block. Claude infers the hold
            # from these spaces arriving and calls it released after ~120ms of
            # quiet, so a gap is not a slow frame -- it is a release, and the
            # recording sputters. Send, then do the slow work.
            if talk_slot is not None and now >= next_key:
                next_key, sent = now + REPEAT_S, sent + 1
                herdr("agent", "send", slots[talk_slot], PTT_KEY)

            # A poll costs two subprocesses, and a sweep costs one per agent:
            # together more than the release timer allows, which is why the
            # hold worked or sputtered depending on where the 1.5s sweep fell.
            # Nothing it scrapes can change in the second or two of a hold.
            talking = talk_slot is not None
            if now >= next_poll:
                next_poll = now + POLL_S
                if not talking:
                    live = agents()
                assign(live, slots)
                by_id = {a["terminal_id"]: a for a in live}
                for s in range(SLOTS):
                    a = by_id.get(slots.get(s))
                    colour, anim = colour_for(a)
                    if s in asking:
                        colour, anim = RED, BLINK    # herdr cannot see this one
                    if closing:                   # the row is a yes/no pair now
                        colour, anim = ((GREEN, STATIC) if s == YES_SLOT else
                                        (RED, BLINK) if s == NO_SLOT else
                                        (BLACK, STATIC))
                    elif s == talk_slot:
                        colour, anim = RED, STATIC
                    elif s == current and a:
                        colour, anim = WHITE, STATIC  # the one you are driving
                    push.cc("session", s, SESSION_CCS, colour, anim)
                    push.cc("tab", s, TAB_CCS,
                            WHITE if s == view else (BLUE if s < len(VIEWS) else BLACK))
                focused = next((a["terminal_id"] for a in live if a.get("focused")), None)
                # Scraped every poll, not just in the focus view: the bottom row
                # answers a pending question from wherever you happen to be.
                seat = current if peek is None else peek
                cur = by_id.get(slots.get(seat))
                scroll = scrolls.get(seat, 0)
                if not talking:
                    summary = pane_summary(cur)
                opts = summary.get("opts") or []
                target = slots.get(current) or focused
                if (cur or {}).get("terminal_id") != published:
                    published = (cur or {}).get("terminal_id")
                    publish_target(cur)
                if reload_macros():
                    shown = None
                    print("macros reloaded", flush=True)
                if closing and now >= closing[1]:
                    print("close request expired", flush=True)
                    closing, shown = None, None

                if now >= next_sweep and not talking:
                    next_sweep = now + SWEEP_S
                    asking = sweep_panes(slots, by_id, current)
                if opts:
                    asking.add(current)
                else:
                    asking.discard(current)

                # jump on the edge only, so navigating away does not fight you
                if asking and not was_asking and not pinned:
                    ask_slot = current if current in asking else sorted(asking)[0]
                    current, view = ask_slot, VIEWS.index("focus")
                    shown, scrolls[ask_slot] = None, 0
                    print(f"question on slot {ask_slot} -> focus", flush=True)
                was_asking = bool(asking)

                if chain and chain.phase in ("running", "waiting"):
                    a = by_id.get(chain.target)
                    status = (a or {}).get("agent_status")
                    if a is None:      # the agent it was pinned to went away
                        print("chain: agent gone, stopping", flush=True)
                        chain, shown = None, None
                    # blocked pauses instead of advancing. The chain waiting on
                    # an answer is the feature, not a case to engineer around --
                    # the approve/deny rows do their ordinary job and it resumes.
                    elif status == "blocked" or chain.slot in asking:
                        if chain.phase != "waiting":
                            chain.phase, shown = "waiting", None
                            print(f"chain: waiting on you at step "
                                  f"{chain.index + 1}", flush=True)
                    else:
                        if chain.phase == "waiting":
                            chain.phase, shown = "running", None
                        chain.saw_working |= status == "working"
                        chain.idle_polls = (chain.idle_polls + 1
                                            if status == "idle" else 0)
                        if chain_step_done(chain.saw_working, status,
                                           now - chain.sent_at, chain.idle_polls):
                            chain.index += 1
                            shown = None
                            if not chain.fire(MACROS, now):
                                chain.phase, chain.done_at = "done", now
                                print("chain complete", flush=True)
                elif chain and chain.phase == "done" and now - chain.done_at > CONFIRM_S:
                    chain, shown = None, None   # let the screen go back to work

                if disp:
                    if strip_level:
                        # outranks the chain: your finger is on the strip now
                        state = ("effort", strip_level)
                        drawn = (lambda l=strip_level:
                                 disp_mod.render_effort(l, EFFORTS))
                    elif previewing is not None:
                        m, idx = MACROS[previewing], MACRO_NOTES[previewing]
                        state = ("pad", previewing, repr(m))
                        drawn = (lambda mm=m, ii=idx: disp_mod.render_pad(mm, ii))
                    elif closing:
                        agent = by_id.get(slots.get(closing[0]))
                        nm = os.path.basename((agent or {}).get("cwd", "")) or "?"
                        left = max(0, int(closing[1] - now))
                        slot_n = closing[0]
                        state = ("closing", slot_n, nm, left)
                        drawn = (lambda n=nm, i=slot_n, t=left:
                                 disp_mod.render_confirm(n, i, t))
                    elif chain and chain.phase != "waiting":
                        # below preview and confirm: an explicit question you
                        # just asked outranks a chain running in the background.
                        # And a WAITING chain gives the screen up entirely --
                        # it is paused because the agent asked you something,
                        # so covering that question with a list of steps hides
                        # the one thing you have to act on. The pads still
                        # blink red, which is what says the chain is holding.
                        cinfo = chain.info(MACROS)
                        state = ("chain", repr(cinfo))
                        drawn = (lambda c=cinfo: disp_mod.render_chain(c))
                    elif VIEWS[view] == "focus":
                        info = focus_info(seat, cur, summary, scroll) if cur else None
                        state = (view, repr(info))
                        drawn = (lambda: disp_mod.render_focus(info)) if info else (
                            lambda: disp_mod.render((None,) * SLOTS))
                    elif VIEWS[view] == "macros":
                        cols = tuple(MACROS)
                        state = (view, cols, arming)
                        drawn = lambda: disp_mod.render_macros(cols, arming)
                    else:
                        build, draw = ((panel_col, disp_mod.render)
                                       if VIEWS[view] == "agents"
                                       else (usage_col, disp_mod.render_usage))
                        cols = tuple(build(by_id.get(slots.get(s)))
                                     for s in range(SLOTS))
                        state = (view, cols)
                        drawn = (lambda c=cols: draw(c))
                    if state != shown:              # re-render on change only
                        try:
                            shown, frame = state, drawn()
                        except Exception as e:
                            # the pads are the product, the screen is the label:
                            # a drawing bug must not take the surface down
                            print(f"render failed ({VIEWS[view]}): {e}",
                                  file=sys.stderr, flush=True)
                            shown, disp = state, None
                    try:
                        disp.show(frame)            # every poll: it blanks after ~2s
                    except Exception:
                        disp = None

                # while a chain is armed Play means "run it", not "enter", and a
                # button that changed jobs has to say so
                armed = bool(chain and chain.phase == "armed")
                push.cc("play", 0, [PLAY_CC],
                        BLUE if armed else (GREEN if target else BLACK),
                        PULSE if armed else STATIC)
                push.cc("auto", 0, [AUTOMATE_CC],
                        BRIGHT if (automating or chain) else DIM)
                push.cc("solo", 0, [SOLO_CC], WHITE if pinned else BLACK)
                push.cc("dup", 0, [DUPLICATE_CC], GREEN if cur else BLACK)
                for cc in PAGE_CCS:
                    push.cc(f"page{cc}", 0, [cc], WHITE if cur else BLACK)
                for cc in ARROW_CCS:
                    push.cc(f"arrow{cc}", 0, [cc], WHITE if target else BLACK)
                # a mapped button that never lights reads as a dead one
                push.cc("shift", 0, [SHIFT_CC], BRIGHT if shifted else DIM)
                for i, cc in enumerate(ARM_CCS):
                    push.cc(f"arm{i}", 0, [cc], RED if arming else DIM)
                push.cc("del", 0, [DELETE_CC], RED if cur else BLACK)
                push.cc("undo", 0, [UNDO_CC],
                        WHITE if summary.get("pending") else BLACK)
                # red while there is something worth interrupting
                push.cc("stop", 0, [STOP_CC],
                        RED if (cur or {}).get("agent_status") == "working"
                        else (WHITE if target else BLACK))
                for cc, (name, _) in list(COMMAND_CCS.items()) + list(KEY_CCS.items()):
                    push.cc(name, 0, [cc], WHITE if target else BLACK)
                push.cc("adddev", 0, [ADD_DEVICE_CC], GREEN)
                push.cc("addtrk", 0, [ADD_TRACK_CC], GREEN)
                push.cc("browse", 0, [BROWSE_CC], WHITE if cur else BLACK)
                # the strip reads back what it sets: the level under your
                # finger while you are choosing it, the agent's real one the
                # rest of the time
                push.strip(strip_bar(strip_level if strip_touch and strip_level
                                     else effort_for(cur)))
                chain_at = {p: k for k, p in enumerate(chain.steps)} if chain else {}
                for i in range(MACRO_SLOTS):        # bottom row changes job when asked
                    row0, anim = i < SLOTS, STATIC
                    if i in chain_at:               # the chain owns its own pads
                        k = chain_at[i]
                        colour, anim = (
                            (GREEN, STATIC) if chain.phase == "done" else
                            (BLACK, STATIC) if k < chain.index else      # spent
                            ((YELLOW, PULSE) if chain.phase == "running" else
                             (RED, BLINK) if chain.phase == "waiting" else
                             (BLUE, PULSE)) if k == chain.index else     # up next
                            (MACROS[i]["colour"] if MACROS[i] else BLACK, STATIC))
                    else:
                        colour = (RED if arming else
                                  (WHITE if row0 and i < len(opts) else
                                   (BLACK if opts and row0 else
                                    (MACROS[i]["colour"] if MACROS[i] else BLACK))))
                    push.note("macro", i, MACRO_NOTES[i], colour, anim)

            for msg in inp.iter_pending():
                if msg.type == "sysex":
                    # The Push sends this when it switches Live/User mode, which
                    # also blanks its LEDs. painted still believes they are lit,
                    # so without this the surface stays dark until a restart.
                    push.painted.clear()
                    push.strip_host(True)   # the mode change took the strip back
                    shown = None
                    print("mode change -> repainting", flush=True)
                    continue
                if debug and msg.type not in ("clock", "active_sensing"):
                    what = (f"CC {msg.control}={msg.value}" if msg.type == "control_change"
                            else f"note {msg.note} v{msg.velocity}"
                            if msg.type in ("note_on", "note_off")
                            # the strip. 0 is its spring-back, called out as
                            # rest so this line cannot imply a level that
                            # strip_pick is in fact throwing away
                            else f"pitch {msg.pitch} -> " + (
                                "(rest)" if msg.pitch == 0
                                else effort_at(msg.pitch))
                            if msg.type == "pitchwheel" else msg.type)
                    print(f"  raw: {what}", flush=True)
                if msg.type in ("note_on", "note_off") and msg.note in ENC_TOUCH:
                    slot = ENC_TOUCH.index(msg.note)
                    touching = msg.type == "note_on" and msg.velocity
                    if touching and slots.get(slot):
                        peek, view, shown = slot, VIEWS.index("focus"), None
                    elif not touching and peek == slot:
                        peek, shown = None, None
                elif msg.type in ("note_on", "note_off") and msg.note == STRIP_NOTE:
                    strip_touch = msg.type == "note_on" and bool(msg.velocity)
                    if not strip_touch and strip_level and target:
                        # lifting off is the commit. /effort takes the level
                        # inline, so there is no picker to drive blind.
                        print(f"strip -> /effort {strip_level} -> {target}", flush=True)
                        herdr("agent", "send", target,
                              f"/effort {strip_level}{ENTER}")
                    if not strip_touch:
                        strip_level, shown = None, None
                elif msg.type == "control_change" and msg.control in SESSION_CCS:
                    slot = SESSION_CCS.index(msg.control)
                    if closing and msg.value:
                        if slot == YES_SLOT:
                            kill = closing[0]
                            pane = (by_id.get(slots.get(kill)) or {}).get("pane_id")
                            print(f"confirmed -> closing {pane}", flush=True)
                            if pane:
                                herdr("pane", "close", pane)
                                slots.pop(kill, None)
                            if current == kill:
                                current = step_slot(current, 1, slots)
                            closing, shown = None, None
                        elif slot == NO_SLOT:
                            print("close cancelled", flush=True)
                            closing, shown = None, None
                    elif closing:
                        pass                        # ignore the release
                    elif msg.value:
                        if slots.get(slot):
                            pad_down = (slot, now)
                    elif slot == talk_slot and talk_src == "session":
                        print(f"pad {slot} released, {sent} keys sent", flush=True)
                        talk_slot, talk_src, pad_down = None, None, None
                    elif pad_down and pad_down[0] == slot:
                        if shifted:
                            closing, shown = (slot, now + CONFIRM_S), None
                            print(f"shift+pad {slot} -> confirm close?", flush=True)
                            pad_down = None
                            continue
                        herdr("agent", "focus", slots[slot])   # short press = focus
                        current, pad_down, shown = slot, None, None
                        scrolls[slot] = 0
                elif (msg.type == "control_change" and msg.control in TAB_CCS
                      and msg.value):
                    i = TAB_CCS.index(msg.control)
                    if i < len(VIEWS):
                        view, shown = i, None       # force a redraw
                        print(f"view -> {VIEWS[i]}", flush=True)
                elif (msg.type == "control_change" and msg.control == ADD_DEVICE_CC
                      and msg.value):
                    where = (cur or {}).get("cwd") or os.getcwd()
                    name = start_agent(where, "right")
                    print(f"add device -> {name or 'FAILED'} in {where}", flush=True)
                elif (msg.type == "control_change" and msg.control == ADD_TRACK_CC
                      and msg.value):
                    where = (cur or {}).get("cwd") or os.getcwd()
                    branch = slug(summary.get("pending") or "")
                    print(f"add track -> worktree {branch!r} off {where}", flush=True)
                    herdr("worktree", "create", "--cwd", where,
                          "--branch", branch, "--focus")
                elif (msg.type == "control_change" and msg.control == BROWSE_CC
                      and msg.value and cur):
                    where = cur.get("cwd") or os.getcwd()
                    # Popen, not run: gh reaches the network, and a second of
                    # blocking here costs the poll that keeps the screen from
                    # blanking. gh's own complaints land in this log, which is
                    # where you would go looking anyway.
                    subprocess.Popen(["gh", "pr", "list", "--web"], cwd=where)
                    print(f"browse -> pull requests for {where}", flush=True)
                elif (msg.type == "control_change" and msg.control == STOP_CC
                      and msg.value):
                    if chain:
                        # mid-step, stop has to mean the agent too, or the chain
                        # dies and the thing it started keeps running
                        print(f"chain {chain.phase} -> discarded", flush=True)
                        if chain.phase in ("running", "waiting"):
                            herdr("agent", "send", chain.target, ESCAPE)
                        chain, shown = None, None
                    elif closing:
                        print("close cancelled", flush=True)
                        closing, shown = None, None
                    elif target:
                        print(f"stop -> escape -> {target}", flush=True)
                        herdr("agent", "send", target, ESCAPE)
                elif (msg.type == "control_change" and msg.control == UNDO_CC
                      and msg.value and target):
                    # ctrl+l never reaches Claude and ctrl+u only kills the row
                    # the cursor is on, so a wrapped prompt survives both.
                    # Deleting is dumb, depends on no keybinding, and works --
                    # but only behind the cursor, so go forwards first. Together
                    # they empty the buffer from wherever the cursor happens to
                    # be sitting, which "clear" has to mean.
                    n = len(summary.get("pending") or "") + 16
                    print(f"undo -> clearing {n} either side -> {target}", flush=True)
                    herdr("agent", "send", target, DELETE_FWD * n + BACKSPACE * n)
                elif (msg.type == "control_change" and msg.control in KEY_CCS
                      and msg.value and target):
                    name, key = KEY_CCS[msg.control]
                    print(f"{name} -> {key!r} -> {target}", flush=True)
                    herdr("agent", "send", target, key)
                elif (msg.type == "control_change" and msg.control in COMMAND_CCS
                      and msg.value and target):
                    name, cmd = COMMAND_CCS[msg.control]
                    print(f"{name} -> {cmd!r} -> {target}", flush=True)
                    herdr("agent", "send", target, cmd)
                elif (msg.type == "control_change" and msg.control == DELETE_CC
                      and msg.value and cur):
                    closing, shown = (current, now + CONFIRM_S), None
                    print(f"delete -> confirm close slot {current}?", flush=True)
                elif msg.type == "control_change" and msg.control in ARM_CCS:
                    arm_held[msg.control] = bool(msg.value)
                    was, arming = arming, any(arm_held.values())
                    shown = None
                    if arming and not was:
                        view = VIEWS.index("macros")   # show what you would overwrite
                elif (msg.type == "control_change" and msg.control in ARROW_CCS
                      and msg.value and target):
                    herdr("agent", "send", target, ARROW_CCS[msg.control])
                elif (msg.type == "control_change" and msg.control == VOLUME_CC
                      and target):
                    # clockwise walks down a list, the way a wheel does
                    delta = turn(msg.value)
                    steps = min(abs(delta), MAX_STEPS)
                    herdr("agent", "send", target,
                          (DOWN if delta > 0 else UP) * steps)
                elif msg.type == "control_change" and msg.control == TEMPO_CC:
                    # clockwise winds back through history, anticlockwise returns
                    # to the live tail at 0 -- same sense as the up arrow
                    seat = current if peek is None else peek
                    scrolls[seat] = max(0, scrolls.get(seat, 0) + turn(msg.value))
                    shown = None
                elif msg.type == "control_change" and msg.control == SHIFT_CC:
                    shifted = bool(msg.value)
                elif (msg.type == "control_change" and msg.control == SOLO_CC
                      and msg.value):
                    pinned, shown = not pinned, None
                    print(f"pinned = {pinned}", flush=True)
                elif (msg.type == "control_change" and msg.control == DUPLICATE_CC
                      and msg.value and cur):
                    where = cur.get("cwd") or os.getcwd()
                    name = start_agent(where, "down")
                    print(f"duplicate -> {name or 'FAILED'} in {where}", flush=True)
                elif (msg.type == "control_change" and msg.control in PAGE_CCS
                      and msg.value):
                    current = step_slot(current, PAGE_CCS[msg.control], slots)
                    shown, scrolls[current] = None, 0
                    print(f"page -> slot {current} ({slots.get(current)})", flush=True)
                elif msg.type == "control_change" and msg.control in ENC_CCS:
                    # each knob scrolls the column beneath it, no switching needed
                    slot = ENC_CCS.index(msg.control)
                    scrolls[slot] = max(0, scrolls.get(slot, 0) + turn(msg.value))
                    if slot in (current, peek):
                        shown = None
                elif msg.type == "pitchwheel":
                    # Only while a finger is genuinely on it, and never the
                    # spring-back to centre -- see strip_pick.
                    if strip_touch:
                        level = strip_pick(msg.pitch, strip_level)
                        if level != strip_level:
                            strip_level, shown = level, None
                elif (msg.type == "control_change" and msg.control == AUTOMATE_CC
                      and msg.value):
                    # A latch, not a held modifier. Record and Select are holds
                    # because the pad you hit is the one you are overwriting and
                    # that wants deliberation; picking a row to run does not,
                    # and a two-handed hold on a surface you play one-handed
                    # just does not land.
                    automating, shown = not automating, None
                    if automating:
                        view = VIEWS.index("macros")   # the rows to choose from
                        print("automate on -> tap a pad to arm its row",
                              flush=True)
                    else:
                        print("automate off", flush=True)
                elif (msg.type == "control_change" and msg.control == PLAY_CC
                      and msg.value):
                    if chain and chain.phase == "armed":
                        shown = None
                        if chain.fire(MACROS, now):
                            chain.phase = "running"
                        else:                   # the whole row went empty
                            chain.phase, chain.done_at = "done", now
                    elif closing:                   # Play is yes to the question
                        slot = closing[0]
                        pane = (by_id.get(slots.get(slot)) or {}).get("pane_id")
                        print(f"confirmed -> closing {pane}", flush=True)
                        if pane:
                            herdr("pane", "close", pane)
                            slots.pop(slot, None)
                        if current == slot:
                            current = step_slot(current, 1, slots)
                        closing, shown = None, None
                    elif target:
                        print(f"play -> enter -> {target}", flush=True)
                        herdr("agent", "send", target, ENTER)
                elif (msg.type in ("note_on", "note_off") and msg.note in MACRO_NOTES
                      and not (msg.type == "note_on" and msg.velocity)):
                    i = MACRO_NOTES.index(msg.note)
                    if previewing == i:
                        previewing, shown = None, None
                    if macro_down and macro_down[0] == i:
                        if talk_src == "macro":     # dictated onto it; let go
                            print(f"pad {i} released, {sent} keys sent", flush=True)
                            talk_slot, talk_src = None, None
                        elif MACROS[i] and MACROS[i]["submit"] and target:
                            herdr("agent", "send", target, ENTER)
                        macro_down = None
                elif msg.type == "note_on" and msg.velocity and msg.note in MACRO_NOTES:
                    i = MACRO_NOTES.index(msg.note)
                    if automating:
                        steps = chain_steps(MACROS, i)
                        if steps and target:
                            chain = Chain(steps, target, current,
                                          os.path.basename((cur or {}).get("cwd", ""))
                                          or "?")
                            # the latch has done its job; leaving it on would
                            # turn the next ordinary pad press into a chain
                            automating, shown = False, None
                            print(f"chain armed: {len(steps)} steps from pad {i} "
                                  f"-> {target}", flush=True)
                        else:
                            # stay latched: an empty row is a miss, not a change
                            # of mind, and the lit button says you are still here
                            print(f"chain: nothing to run from pad {i}, "
                                  f"still armed -- pick another row", flush=True)
                    elif arming:
                        text = (summary.get("pending") or "").strip()
                        was = MACROS[i] or {}
                        MACROS[i] = {"label": label_for(text), "text": text,
                                     "colour": was.get("colour", BLUE),
                                     "tag": was.get("tag"),
                                     "submit": was.get("submit", False)} if text else None
                        save_macros(MACROS)
                        shown = None
                        print(f"pad {i} <- {text!r}" if text
                              else f"pad {i} cleared", flush=True)
                    elif shifted:
                        # ask what a pad does without finding out the hard way
                        previewing, shown = i, None
                        print(f"shift+pad {i} -> preview", flush=True)
                    elif opts and i < SLOTS and i < len(opts) and target:
                        # exactly the pads painted white above: what lights is
                        # what answers. Ahead of the macro branch so a pad that
                        # is currently an answer cannot fire its old text into
                        # a question instead.
                        pick = summary.get("sel") or 0
                        print(f"answer {i + 1}/{len(opts)}: {opts[i][1]!r} "
                              f"-> {target}", flush=True)
                        herdr("agent", "send", target, answer_keys(i, pick))
                    elif MACROS[i] and target:
                        m = MACROS[i]
                        # the text goes in now so it reads back immediately, but
                        # its newline waits for the release: a hold means you are
                        # about to dictate the rest, and submitting first would
                        # send half a thought
                        print(f"macro {m['label']!r} -> {target}", flush=True)
                        herdr("agent", "send", target, m["text"])
                        macro_down = (i, now)

            if (macro_down and talk_slot is None and target
                    and now - macro_down[1] >= HOLD_S):
                talk_slot, talk_src, next_key, sent = current, "macro", 0.0, 0
                print(f"pad {macro_down[0]} held -> dictating onto it", flush=True)

            if pad_down and talk_slot is None and now - pad_down[1] >= HOLD_S:
                # No focus call: Claude reads its own pty, so a background agent
                # hears this while you keep watching another one.
                talk_slot, talk_src, next_key, sent = pad_down[0], "session", 0.0, 0
                current, shown = talk_slot, None
                scrolls[talk_slot] = 0
                print(f"pad {talk_slot} held -> talking to {slots[talk_slot]}", flush=True)

            time.sleep(0.005)
    except KeyboardInterrupt:
        pass
    finally:
        publish_target(None)
        push.painted.clear()
        push.blank()
        push.strip_host(False)   # hand the strip back or it stays ours
        if disp:
            try:
                disp.blank()
            except Exception:
                pass


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

    assert len(MACROS) == MACRO_SLOTS, "one entry per macro pad"
    # the text itself never carries a newline; submitting is the flag's job, so
    # a pad cannot end up firing because of how someone typed it
    assert not any(m["text"].endswith("\r") for m in filter(None, MACROS)), \
        "submit is a flag, not a newline in the text"
    assert all(0 <= m["colour"] <= 127 for m in filter(None, MACROS)), "palette is 0-127"
    assert all(isinstance(m["submit"], bool) for m in filter(None, MACROS))
    assert len(set(MACRO_NOTES)) == len(MACRO_NOTES) == 64, "every pad, once"
    assert PLAY_CC not in TAB_CCS + SESSION_CCS, "play must not collide with a lit row"
    assert not set(SESSION_CCS) & set(TAB_CCS), "the two button rows must not overlap"

    assert panel_col(None) is None
    col = panel_col({"cwd": "/a/b/bugcast", "agent_status": "blocked",
                     "terminal_id": "term_659ce899ee9293", "focused": True})
    assert col["name"] == "bugcast" and col["sub"] == "ee9293", col
    col = panel_col({"cwd": "/a/b/bugcast", "agent_status": "idle",
                     "terminal_id": "t", "focused": False})
    assert 0.0 <= col["context"] <= 1.0, "context fraction stays in range"
    # every renderer reads by key, so a new field cannot break an old unpack
    assert set(col) == {"name", "status", "model", "effort", "sub",
                        "focused", "context"}, col
    assert "typed" not in col, "the prompt is the focus view's job, not a column's"
    assert short_model("claude-opus-5") == "opus 5"
    assert short_model("claude-haiku-4-5-20251001") == "haiku 4.5"
    assert short_model("claude-sonnet-5") == "sonnet 5"

    # a row is the chain: from a pad to the end of ITS row, never into the next
    pads = [{"text": "t", "label": "t", "colour": BLUE, "tag": None,
             "submit": False} for _ in range(MACRO_SLOTS)]
    assert chain_steps(pads, 0) == list(range(0, 8)), "row 0 stops at 8"
    assert chain_steps(pads, 5) == [5, 6, 7], "starts where you tapped"
    assert chain_steps(pads, 8) == list(range(8, 16)), "row 1 is its own chain"
    assert chain_steps(pads, 63) == [63], "last pad is a chain of one"
    holes = list(pads)
    holes[1] = holes[2] = None
    assert chain_steps(holes, 0) == [0, 3, 4, 5, 6, 7], "empties drop out"
    assert chain_steps([None] * MACRO_SLOTS, 0) == [], "an empty row is no chain"

    # the whole point: a step is not done just because the agent still reads
    # idle in the poll right after its enter landed
    assert not chain_step_done(False, "idle", 0.1, 1), "idle before working is lag"
    assert not chain_step_done(True, "idle", 9.0, 1), "one idle poll is not settled"
    assert chain_step_done(True, "idle", 9.0, CHAIN_SETTLE), "working then settled idle"
    assert not chain_step_done(True, "working", 9.0, 0), "still going"
    # a step that began and ended between two polls never reported working
    assert chain_step_done(False, "idle", CHAIN_GRACE_S, CHAIN_SETTLE), "grace covers it"
    assert not chain_step_done(True, "blocked", 9.0, CHAIN_SETTLE), "blocked is not done"

    ch = Chain([0, 1], "term_x", 3, "midiAI")
    assert ch.phase == "armed" and ch.step == 0
    ch.index = 2
    assert ch.step is None, "past the end has no step"
    # mapui can clear a pad while the chain that queued it is still running
    ch = Chain([0, 1], "term_x", 3, "midiAI")
    assert ch.fire([None] * MACRO_SLOTS, 0.0) is False, "an emptied row just ends"
    assert ch.index == 2, "and it does not sit on a pad that is gone"
    info = Chain([0, 1], "t", 0, "n").info(pads)
    assert set(info) == {"agent", "slot", "steps", "index", "phase"}, info
    assert len(info["steps"]) == 2 and set(info["steps"][0]) == {"label", "colour"}
    assert AUTOMATE_CC not in TAB_CCS + SESSION_CCS + list(COMMAND_CCS)
    assert AUTOMATE_CC not in (PLAY_CC, STOP_CC, SHIFT_CC) + ARM_CCS
    assert BROWSE_CC not in TAB_CCS + SESSION_CCS + list(COMMAND_CCS)
    assert BROWSE_CC not in (PLAY_CC, STOP_CC, SHIFT_CC, AUTOMATE_CC,
                             ADD_DEVICE_CC, ADD_TRACK_CC, DELETE_CC) + ARM_CCS

    # the strip is absolute: both ends must be reachable, and reachable at the
    # very edge, or the two levels people most want are the two they cannot hit
    # answering: walk the caret to the pad you pressed, then commit
    assert answer_keys(0, 0) == ENTER, "already on it, just commit"
    assert answer_keys(2, 0) == DOWN * 2 + ENTER, "down to a later option"
    assert answer_keys(0, 2) == UP * 2 + ENTER, "back up to an earlier one"
    assert answer_keys(3, 1) == DOWN * 2 + ENTER, "distance, not destination"
    assert answer_keys(7, 0).endswith(ENTER), "every answer submits"

    assert STRIP_NOTE not in ENC_TOUCH + MACRO_NOTES, "the strip needs its own note"
    # the bar: taller means more thinking, and it has to reach both ends
    assert strip_bar("low").count(STRIP_ON) > 0, "the lowest level still shows"
    assert strip_bar("max") == [STRIP_ON] * STRIP_LEDS, "max fills the strip"
    assert strip_bar("") == [STRIP_OFF] * STRIP_LEDS, "unknown reads as dark"
    assert strip_bar("banana") == [STRIP_OFF] * STRIP_LEDS, "and so does nonsense"
    heights = [strip_bar(l).count(STRIP_ON) for l in EFFORTS]
    assert heights == sorted(heights), "more effort is never a shorter bar"
    assert len(set(heights)) == len(EFFORTS), "every level looks different"
    assert all(len(strip_bar(l)) == STRIP_LEDS for l in EFFORTS), "one per LED"

    # 3 bits each, two to a byte, low LED in the low bits -- 31 is odd, so the
    # top LED rides alone in the last byte
    assert len(strip_pack([STRIP_OFF] * STRIP_LEDS)) == 16, "16 bytes on the wire"
    assert strip_pack([1, 2]) == [1 | (2 << 3)], "second LED sits in the high bits"
    assert strip_pack([7] * 31)[-1] == 7, "the odd top LED keeps its own byte"
    assert all(0 <= b <= 0x7F for b in strip_pack([7] * 31)), "sysex stays 7-bit"
    assert STRIP_CFG_HOST & 0x01 and not STRIP_CFG_DEFAULT & 0x01, "bit 0 is ours"
    # bit 1 is the whole feature: without it the device accepts the config, says
    # so when asked, and then discards every LED write without a word
    assert STRIP_CFG_HOST & 0x02, "host must send sysex or the LEDs are ignored"
    # and taking them must not disturb the pitch bend everything else reads
    assert STRIP_CFG_HOST & 0x04 == STRIP_CFG_DEFAULT & 0x04, "still pitch bend"
    # the spring-back: the strip reports exact centre as a bend just BEFORE its
    # release note, so a naive read committed `high` from anywhere on the strip
    assert strip_pick(0, "medium") == "medium", "rest must not overwrite a choice"
    assert strip_pick(0, None) is None, "and it cannot invent one either"
    assert strip_pick(-2688, "high") == "medium", "a real reading still moves it"
    lvl = None                    # the exact sequence the hardware logged
    for p in (-2496, -2624, -2688, -2624, 0):
        lvl = strip_pick(p, lvl)
    assert lvl == "medium", "lifting off commits where the finger was"
    assert effort_at(-8192) == "low", "bottom of the strip"
    assert effort_at(8191) == "max", "top of the strip"
    assert effort_at(0) == EFFORTS[len(EFFORTS) // 2], "middle is the middle"
    assert all(effort_at(p) in EFFORTS
               for p in range(-8192, 8192, 97)), "no position falls off the list"
    seen = [effort_at(p) for p in range(-8192, 8192, 13)]
    assert set(seen) == set(EFFORTS), "every level has a reachable band"
    # monotonic: sliding up must never step back down
    assert seen == sorted(seen, key=EFFORTS.index), "the strip only goes one way"

    assert usage_col(None) is None
    # "input_tokens" must not also match the tail of cache_read_input_tokens
    line = (b'{"usage":{"input_tokens":2,"cache_creation_input_tokens":699,'
            b'"cache_read_input_tokens":199412,"output_tokens":1017}}\n')
    import re as _re
    assert [int(m) for m in _re.findall(_USAGE_FIELDS["inp"], line)] == [2]
    assert [int(m) for m in _re.findall(_USAGE_FIELDS["cread"], line)] == [199412]
    assert [int(m) for m in _re.findall(_USAGE_FIELDS["out"], line)] == [1017]

    assert _OPT_RE.match("❯ 1. Yes").groups() == ("❯", "1", "Yes")
    assert _OPT_RE.match("  2. No, exit").groups() == (None, "2", "No, exit")
    assert _OPT_RE.match("  ⏵⏵ auto mode on") is None
    assert _OPT_RE.match("❯ commit the fix") is None   # typed text, not a choice
    assert _OPT_RE.match("❯ 1. Yes").group(1) == "❯"    # widget: caret present
    assert _OPT_RE.match("  2. No, exit").group(1) is None      # prose: none

    assert turn(1) == 1 and turn(3) == 3        # clockwise
    assert turn(127) == -1 and turn(125) == -3  # anticlockwise, two's complement
    assert turn(63) == 63 and turn(64) == -64   # the wrap point

    live = {0: "x", 2: "z", 5: "w"}
    assert step_slot(0, 1, live) == 2           # skips the empty pads
    assert step_slot(5, 1, live) == 0           # wraps
    assert step_slot(0, -1, live) == 5
    assert step_slot(3, 1, live) == 0           # not on a live slot -> first
    assert step_slot(0, 1, {}) == 0             # nothing live -> stay put

    assert label_for("") == ""
    assert label_for("/handoff") == "/handoff"
    assert label_for("run the tests and report what fails") == "run the"
    assert len(label_for("supercalifragilistic expialidocious")) == 14
    assert len(load_macros()[1]) == MACRO_SLOTS   # always exactly one per pad
    assert len(MACRO_NOTES) == len(set(MACRO_NOTES)) == MACRO_ROWS * SLOTS
    assert min(MACRO_NOTES) == 36 and max(MACRO_NOTES) == 99

    assert slug("Fix The Parser") == "fix-the-parser"
    assert slug("feat/thing") == "feat/thing"
    assert slug("  spaces   everywhere  ") == "spaces-everywhere"
    assert slug("!!!") .startswith("push/")     # nothing usable -> timestamped
    assert slug("") .startswith("push/")
    assert len(slug("x" * 200)) == 60

    assert all(v.endswith("\r") for _, v in COMMAND_CCS.values())
    assert not any(v.endswith("\r") for _, v in KEY_CCS.values()), "raw keys do not submit"
    assert not set(KEY_CCS) & set(COMMAND_CCS), "a button belongs to one table"

    # a wrapped prompt is one buffer, not just its caret row
    assert prompt_text(["❯ hello"]) == "hello"
    assert prompt_text(["❯ run the tests and report what",
                        "  failscontinue",
                        "──────────",
                        "  ⏵⏵ auto mode on"]) == "run the tests and report what failscontinue"
    assert prompt_text(["❯ /clear", "  ⏵⏵ footer", "❯ later"]) == "later"  # last caret wins
    assert prompt_text(["nothing here"]) == ""
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
