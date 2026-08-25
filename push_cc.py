#!/usr/bin/env python3
"""Ableton Push 2 as an AI command center, driven by herdr.

Buttons under the display (CC 20-27) = up to 8 herdr agents, left to right.
  tap    -> drive that agent
  hold   -> talk to it, via that agent's own voice:pushToTalk
  colour -> green done / yellow working / red blocked / white the one you drive
  Page left/right step between them.

The whole 8x8 pad grid (36-99) = shortcuts. Tap one to insert its text into
the current agent; Play submits. Hold Record, or Shift, and tap one to save
whatever is in the prompt onto it. macros.json is hand-editable.

Buttons above the display (CC 102-109) pick the view: 1 agents, 2 usage,
3 focus, 4 shortcuts. White is the one you are on.

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
Metronome /mcp. Stop Clip sends escape. Undo clears what is typed. Arrows and
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
VIEWS = ["agents", "usage", "focus", "macros"]
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
DELETE_CC = 118                    # Delete -> close the current agent's pane
ADD_DEVICE_CC = 52                 # Add Device -> split, new claude in auto mode
ADD_TRACK_CC = 53                  # Add Track -> new worktree

UNDO_CC = 119                      # Undo -> backspace the prompt empty
FREED_CCS = [60]                   # Mute, unmapped now: blank it or it stays lit
STOP_CC = 29                       # Stop Clip -> escape, interrupts the agent
BACKSPACE, ESCAPE = "\x7f", "\x1b"

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


def slug(text):
    """Prompt text -> a branch name. Typing the name in the prompt and pressing
    the button beats a hardware surface that cannot ask you anything."""
    keep = [c if (c.isalnum() or c in "/_-") else "-" for c in text.strip().lower()]
    name = re.sub(r"-{2,}", "-", "".join(keep)).strip("-/")
    return name[:60] or "push/" + time.strftime("%H%M%S")


def step_slot(current, delta, slots):
    """Next occupied slot, wrapping. Empty pads are not worth stopping on."""
    live = sorted(s for s in slots if slots[s])
    if not live:
        return current
    if current in live:
        return live[(live.index(current) + delta) % len(live)]
    return live[0] if delta > 0 else live[-1]


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
    idle, so herdr never reports it. Only the pane knows. The typing comes off
    the same read, so it costs nothing to keep."""
    asking, pendings = set(), {}
    for slot, tid in slots.items():
        if slot == current or not tid:
            continue
        agent = by_id.get(tid)
        if not agent:
            continue
        summary = pane_summary(agent, SWEEP_LINES)
        if summary.get("opts"):
            asking.add(slot)
        if summary.get("pending"):
            pendings[slot] = summary["pending"]
    return asking, pendings


def focus_info(slot, agent, summary, scroll=0):
    return {"slot": slot,
            "name": os.path.basename(agent.get("cwd", "")) or "?",
            "model": model_for(agent),
            "status": agent.get("agent_status"),
            **{k: summary.get(k, "") for k in ("act", "say", "pending")},
            "opts": summary.get("opts") or [], "sel": summary.get("sel"),
            "lines": summary.get("lines") or [], "scroll": scroll}


def panel_col(agent, pending=""):
    """One screen column. Two agents can share a repo name, so show a tail of
    the terminal id -- that is the thing that actually tells them apart."""
    if agent is None:
        return None
    return (os.path.basename(agent.get("cwd", "")) or "?",
            agent.get("agent_status"),
            model_for(agent),
            agent.get("terminal_id", "")[-6:],
            bool(agent.get("focused")),
            pending)


def colour_for(agent):
    if agent is None:
        return EMPTY
    return STATUS.get(agent.get("agent_status"), UNKNOWN)


# ---------------------------------------------------------------- push

class Push:
    def __init__(self, mido, inp, out):
        self.mido, self.inp, self.out = mido, inp, out
        self.painted = {}

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
        for i in range(len(FREED_CCS)):
            self.cc("freed", i, FREED_CCS, BLACK)


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
    push.blank()
    out.send(mido.Message("start"))  # spec: animations don't run until a start arrives

    disp_mod, disp = screen()
    debug = "--debug" in sys.argv
    slots, talk_slot, pad_down, next_key, next_poll, sent = {}, None, None, 0.0, 0.0, 0
    by_id, focused = {}, None
    shown, frame, view = None, None, 0
    current, summary, arming = 0, {}, False
    scrolls, peek = {}, None        # scroll is per agent; peek is a held finger
    shifted, pinned = False, False
    published = object()   # sentinel: nothing published yet
    closing = None      # (slot, deadline): asked to close, waiting on an answer
    asking, pendings, next_sweep, was_asking = set(), {}, 0.0, False
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

                if now >= next_sweep:
                    next_sweep = now + SWEEP_S
                    asking, pendings = sweep_panes(slots, by_id, current)
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

                if disp:
                    if closing:
                        agent = by_id.get(slots.get(closing[0]))
                        nm = os.path.basename((agent or {}).get("cwd", "")) or "?"
                        left = max(0, int(closing[1] - now))
                        slot_n = closing[0]
                        state = ("closing", slot_n, nm, left)
                        drawn = (lambda n=nm, i=slot_n, t=left:
                                 disp_mod.render_confirm(n, i, t))
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
                        typed = dict(pendings)
                        typed[seat] = summary.get("pending", "")
                        cols = tuple(
                            build(by_id.get(slots.get(s)), typed.get(s, ""))
                            if build is panel_col else build(by_id.get(slots.get(s)))
                            for s in range(SLOTS))
                        state = (view, cols, seat)
                        drawn = ((lambda: draw(cols, seat)) if draw is disp_mod.render
                                 else (lambda: draw(cols)))
                    if state != shown:              # re-render on change only
                        shown, frame = state, drawn()
                    try:
                        disp.show(frame)            # every poll: it blanks after ~2s
                    except Exception:
                        disp = None

                push.cc("play", 0, [PLAY_CC], GREEN if target else BLACK)
                push.cc("solo", 0, [SOLO_CC], WHITE if pinned else BLACK)
                push.cc("dup", 0, [DUPLICATE_CC], GREEN if cur else BLACK)
                for cc in PAGE_CCS:
                    push.cc(f"page{cc}", 0, [cc], WHITE if cur else BLACK)
                for cc in ARROW_CCS:
                    push.cc(f"arrow{cc}", 0, [cc], WHITE if target else BLACK)
                push.cc("rec", 0, [RECORD_CC], RED if arming else BLACK)
                push.cc("del", 0, [DELETE_CC], RED if cur else BLACK)
                push.cc("undo", 0, [UNDO_CC],
                        WHITE if summary.get("pending") else BLACK)
                # red while there is something worth interrupting
                push.cc("stop", 0, [STOP_CC],
                        RED if (cur or {}).get("agent_status") == "working"
                        else (WHITE if target else BLACK))
                for cc, (name, _) in COMMAND_CCS.items():
                    push.cc(name, 0, [cc], WHITE if target else BLACK)
                push.cc("adddev", 0, [ADD_DEVICE_CC], GREEN)
                push.cc("addtrk", 0, [ADD_TRACK_CC], GREEN)
                for i in range(MACRO_SLOTS):        # bottom row changes job when asked
                    row0 = i < SLOTS
                    push.note("macro", i, MACRO_NOTES[i],
                              RED if arming else
                              (WHITE if row0 and i < len(opts) else
                               (BLACK if opts and row0 else
                                (MACROS[i]["colour"] if MACROS[i] else BLACK))))

            for msg in inp.iter_pending():
                if debug and msg.type not in ("clock", "active_sensing"):
                    what = (f"CC {msg.control}={msg.value}" if msg.type == "control_change"
                            else f"note {msg.note} v{msg.velocity}"
                            if msg.type in ("note_on", "note_off") else msg.type)
                    print(f"  raw: {what}", flush=True)
                if msg.type in ("note_on", "note_off") and msg.note in ENC_TOUCH:
                    slot = ENC_TOUCH.index(msg.note)
                    held = msg.type == "note_on" and msg.velocity
                    if held and slots.get(slot):
                        peek, view, shown = slot, VIEWS.index("focus"), None
                    elif not held and peek == slot:
                        peek, shown = None, None
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
                    elif slot == talk_slot:
                        print(f"pad {slot} released, {sent} keys sent", flush=True)
                        talk_slot, pad_down = None, None
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
                    print(f"add device -> new claude (auto) in {where}", flush=True)
                    herdr("agent", "start", "claude", "--cwd", where,
                          "--split", "right", "--focus",
                          "--", "claude", "--permission-mode", "auto")
                elif (msg.type == "control_change" and msg.control == ADD_TRACK_CC
                      and msg.value):
                    where = (cur or {}).get("cwd") or os.getcwd()
                    branch = slug(summary.get("pending") or "")
                    print(f"add track -> worktree {branch!r} off {where}", flush=True)
                    herdr("worktree", "create", "--cwd", where,
                          "--branch", branch, "--focus")
                elif (msg.type == "control_change" and msg.control == STOP_CC
                      and msg.value):
                    if closing:
                        print("close cancelled", flush=True)
                        closing, shown = None, None
                    elif target:
                        print(f"stop -> escape -> {target}", flush=True)
                        herdr("agent", "send", target, ESCAPE)
                elif (msg.type == "control_change" and msg.control == UNDO_CC
                      and msg.value and target):
                    # ctrl+l never reaches Claude and ctrl+u only kills the row
                    # the cursor is on, so a wrapped prompt survives both.
                    # Backspacing is dumb, depends on no keybinding, and works.
                    n = len(summary.get("pending") or "") + 16
                    print(f"undo -> {n} backspaces -> {target}", flush=True)
                    herdr("agent", "send", target, BACKSPACE * n)
                elif (msg.type == "control_change" and msg.control in COMMAND_CCS
                      and msg.value and target):
                    name, cmd = COMMAND_CCS[msg.control]
                    print(f"{name} -> {cmd!r} -> {target}", flush=True)
                    herdr("agent", "send", target, cmd)
                elif (msg.type == "control_change" and msg.control == DELETE_CC
                      and msg.value and cur):
                    closing, shown = (current, now + CONFIRM_S), None
                    print(f"delete -> confirm close slot {current}?", flush=True)
                elif msg.type == "control_change" and msg.control == RECORD_CC:
                    arming, shown = bool(msg.value), None
                    if arming:
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
                    print(f"duplicate -> second agent in {where}", flush=True)
                    herdr("agent", "start", "claude", "--cwd", where,
                          "--split", "down", "--focus",
                          "--", "claude", "--permission-mode", "auto")
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
                elif (msg.type == "control_change" and msg.control == PLAY_CC
                      and msg.value):
                    if closing:                     # Play is yes to the question
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
                elif msg.type == "note_on" and msg.velocity and msg.note in MACRO_NOTES:
                    i = MACRO_NOTES.index(msg.note)
                    if arming or shifted:
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
                    elif MACROS[i] and target:
                        m = MACROS[i]
                        text = m["text"] + (ENTER if m["submit"] else "")
                        print(f"macro {m['label']!r}"
                              f"{' + enter' if m['submit'] else ''} -> {target}",
                              flush=True)
                        herdr("agent", "send", target, text)

            if pad_down and talk_slot is None and now - pad_down[1] >= HOLD_S:
                # No focus call: Claude reads its own pty, so a background agent
                # hears this while you keep watching another one.
                talk_slot, next_key, sent = pad_down[0], 0.0, 0
                current, shown = talk_slot, None
                scrolls[talk_slot] = 0
                print(f"pad {talk_slot} held -> talking to {slots[talk_slot]}", flush=True)

            if talk_slot is not None and now >= next_key:
                next_key, sent = now + REPEAT_S, sent + 1
                herdr("agent", "send", slots[talk_slot], PTT_KEY)

            time.sleep(0.005)
    except KeyboardInterrupt:
        pass
    finally:
        publish_target(None)
        push.painted.clear()
        push.blank()
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
    assert col == ("bugcast", "blocked", "", "ee9293", True, ""), col
    col = panel_col({"cwd": "/a/b/bugcast", "agent_status": "idle",
                     "terminal_id": "t", "focused": False}, "half a sentence")
    assert col[-1] == "half a sentence", col
    assert short_model("claude-opus-5") == "opus 5"
    assert short_model("claude-haiku-4-5-20251001") == "haiku 4.5"
    assert short_model("claude-sonnet-5") == "sonnet 5"

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
