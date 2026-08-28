#!/usr/bin/env python3
"""Ableton Push 2 as an AI command center.

The agents live in a terminal multiplexer -- tmux by default, herdr if you set
PUSH_BACKEND=herdr. Every pane call goes through herdr() below, which keeps
its name because it is the dispatcher for both, and renaming it is thirty call
sites of churn for a word.

Buttons under the display (CC 20-27) = up to 8 agents, left to right.
  tap    -> drive that agent
  hold   -> talk to it, via that agent's own voice:pushToTalk
  colour -> green done / yellow working / red blocked / white the one you drive
  Page left/right step between them.

The whole 8x8 pad grid (36-99) = shortcuts. Tap one to insert its text into
the current agent; Play submits. Hold one and it opens the mic instead, so you
dictate the rest of the sentence onto what it just typed. Hold Shift and tap one to read what it does without running it.
Hold Record or Select and tap one to save
whatever is in the prompt onto it. macros.json is hand-editable.

Buttons above the display (CC 102-109) pick the view: 1 focus,
2 sessions, 3 tests, 4 prs, 5 usage. White is the one you are on.

The 960x160 screen names each column, so two checkouts of the same repo are
told apart by the tail of their terminal id. Missing pyusb just means no
screen; the buttons carry on.

When any agent starts asking something the screen jumps to the focus view on
it, once, on the edge -- navigate away and it will not drag you back. Its
button blinks red, which no backend's status alone would ever tell you -- an
agent that asks a question in prose reads as idle to tmux and to herdr both,
because it genuinely is. Only the pane knows. Answer a
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
import glob
import json
import os
import re
import subprocess
import sys
import threading
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
VIEWS = ["focus", "sessions", "tests", "prs", "usage"]
# a view with more than one mode: its own button cycles them, Left/Right too.
# One button per subject beats two buttons for two halves of one question.
# the shortcuts grid is the focus view's second mode: it is the same subject
# -- the session you are driving -- shown as what you can say to it
# the sessions view's second mode names the pads its first mode lights
VIEW_MODES = {"focus": 2, "sessions": 2, "usage": 3}
SESSION_CCS = list(range(20, 28))  # under the display: tap selects, hold talks
PLAY_CC = 85                       # transport Play -> enter, submits what is typed
TEMPO_CC = 14                      # tempo encoder -> scroll the focus view
VOLUME_CC = 79                     # master encoder -> up/down arrows at the agent
ENC_CCS = list(range(71, 79))      # the 8 encoders, one over each agent column
ENC_TOUCH = list(range(0, 8))      # touching one is a note, not a CC
SHIFT_CC = 49                      # held modifier, the standard Push idiom
SOLO_CC = 61                       # pin: neither questions nor the terminal move it
DUPLICATE_CC = 88                  # fork the current agent into a new session
PAGE_CCS = {62: -1, 63: 1}         # page left/right -> previous/next pad page
CONFIRM_S = 10                     # a question that goes unanswered expires
YES_SLOT, NO_SLOT = 0, 1           # while confirming, the first two session buttons
MAX_STEPS = 8                      # a fast spin must not fire fifty keypresses
UP, DOWN = "\x1b[A", "\x1b[B"   # also used to walk a select widget's caret

# Straight through to the agent as real arrow keys. Claude already decides what
# they mean in context -- caret in a dialog, history at an empty prompt, cursor
# in text -- and second-guessing that from here only ever gets it wrong.
ARROW_CCS = {44: "\x1b[D", 45: "\x1b[C", 46: UP, 47: DOWN}
# While a chain is armed the same two buttons choose which of the row's
# sequences to run. They go back to being arrow keys the moment it is not.
PICK_CCS = {44: -1, 45: 1}
BACK_CC = 44           # left also backs out of the test tree, while it is up
# Octave up/down page the focus view. The knobs already scroll it a line at a
# time, which is the wrong gesture for getting somewhere: a button you can hit
# twice beats a knob you have to keep turning.
SCROLL_CCS = {55: 1, 54: -1}       # up goes back through history, down returns
PAGE_LINES = 6                     # about a screenful at the body's font
RECORD_CC = 86                     # hold Record, tap a pad: saves the prompt to it
ARM_CCS = (RECORD_CC,)
# Select rearranges the grid instead of arming with Record: tap it, tap a pad to
# pick it up, tap another to put it down. mapui has done this by dragging since
# it existed; doing it on the device means never leaving the device.
SELECT_CC = 48
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
# the browser mirror: the frame we just drew, what the surface around it says,
# and the one file that comes back the other way
FRAME_FILE = os.path.join(_HERE, ".frame.png")
SURFACE_FILE = os.path.join(_HERE, ".surface.json")
CMD_FILE = os.path.join(_HERE, ".command.json")
SCRAPE_LINES = "400"               # how far back the focus view can scroll
SWEEP_LINES = "40"                 # enough to spot a question at the foot of a pane
SWEEP_S = 1.5                      # every agent, throttled: 8 reads is not free

# Palette indices guaranteed by the Ableton Push 2 spec, and animation channels.
# The Push 2's own colour numbers. These replaced an earlier set that had been
# arrived at by trial; macros.json was remapped in the same commit, so no pad
# changed colour. Note 3 means white here and meant orange before -- which is
# why that migration had to be one pass of a lookup and not a run of
# substitutions, or every orange pad would have ended up white.
BLACK, WHITE, RED, ORANGE = 0, 3, 120, 60
YELLOW, GREEN, CYAN = 13, 21, 33
BLUE, INDIGO, VIOLET = 45, 49, 53
# Shift and Record are white-only buttons: the value is brightness, not a
# palette index, so they need their own two levels.
DIM, BRIGHT = 20, 127
BLUE = 125  # ponytail: not in the spec's guaranteed set; worst case it is the
            # wrong hue, which costs nothing. Swap if it reads badly.
TAB_DIM = 124   # (20,20,20): present, clearly not the one

# Palette indices the Push 2 spec guarantees, plus the blue above. Any 0-127
# index works on the hardware; these are the ones worth offering by name.
PALETTE = [("red", RED), ("orange", ORANGE), ("yellow", YELLOW),
           ("green", GREEN), ("cyan", CYAN), ("blue", BLUE),
           ("indigo", INDIGO), ("violet", VIOLET), ("white", WHITE)]
# A hue per view, on its button and on its label -- six views, and six palette
# indices this hardware is known to render honestly. Selection is the
# animation rather than the brightness: an arbitrary palette index has no dim
# twin to fall back on, and the label strip already boxes the one you are on.
# display.VIEW_RGB carries the same assignment in screen colours; change both.
VIEW_CC = {"focus": WHITE, "sessions": BLUE, "tests": GREEN,
           "prs": YELLOW, "usage": RED}
# One hue per option, so the pad under your finger and the number on the glass
# are the same colour. display.ANSWER_RGB carries the same list in screen
# colours; change both. Six rather than eight: an option seven that repeats
# option one is better than a palette index guessed blind on hardware I cannot
# see, and the number is printed beside it either way.
ANSWER_CC = [GREEN, BLUE, YELLOW, ORANGE, RED, WHITE]
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


def read_page(pads, by_name):
    """One grid's worth of entries, normalised, always exactly MACRO_SLOTS."""
    out = []
    for entry in (pads or [])[:MACRO_SLOTS]:
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
    return out + [None] * (MACRO_SLOTS - len(out))


def load_macros():
    """macros.json if present, defaults otherwise. Hand-editable on purpose.

    Three shapes, because each of them was the file once and none should lose
    its pads to a schema written after it: a bare list, {labels, pads}, and
    {labels, pages}. The first two are page one of the third.

    Trailing empty pages are dropped here rather than on save, so a page you
    emptied leaves by itself and the file never fills up with blank grids."""
    try:
        with open(MACRO_FILE) as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError):
        raw = DEFAULT_MACROS
    if isinstance(raw, dict):
        labels = raw.get("labels") or DEFAULT_LABELS
        pages = raw.get("pages") or [raw.get("pads") or []]
    else:
        labels, pages = DEFAULT_LABELS, [raw or []]
    by_name = {l["name"]: l.get("colour", BLUE) for l in labels if l.get("name")}
    out = [read_page(p, by_name) for p in pages] or [read_page([], by_name)]
    while len(out) > 1 and not any(out[-1]):
        out.pop()
    return labels, out


def save_macros(pages=None, labels=None):
    """Write the whole store. The live page is PAGES[page] by identity, so a
    caller that mutated MACROS has already mutated what gets written."""
    store = PAGES if pages is None else pages
    if store and not isinstance(store[0], list):
        store = [store]          # a bare page, from a caller older than pages
    tmp = MACRO_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"labels": labels if labels is not None else LABELS,
                   "pages": [list(pg) for pg in store]}, f, indent=2)
    os.replace(tmp, MACRO_FILE)   # never leave a half-written file behind


LABELS, PAGES = load_macros()
page = 0
MACROS = PAGES[page]        # the live page, by identity -- not a copy of one
_macros_mtime = 0.0


def set_page(delta):
    """Page left or right across the banks. True if anywhere was gone to.

    Right off the end mints a new page, but only from one with something on
    it, or leaning on the button would make empty grids forever. Left of the
    first does nothing: pages are a line, not a ring, and falling from page
    one to page nine is never what the hand meant."""
    global MACROS, page
    want = page + delta
    if want < 0 or want == page:
        return False
    if want >= len(PAGES):
        if want > len(PAGES) or not any(PAGES[-1]):
            return False
        PAGES.append([None] * MACRO_SLOTS)
    page, MACROS = want, PAGES[want]
    return True


def publish_target(agent):
    """Tell mapui which session the Push is pointed at."""
    payload = {} if not agent else {
        "terminal_id": agent.get("terminal_id"),
        "name": agent_name(agent),
        "status": agent.get("agent_status"),
    }
    tmp = TARGET_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f)
    os.replace(tmp, TARGET_FILE)


_frame_stamp = 0.0


def publish_frame(frame):
    """Hand the browser the exact image the Push is showing.

    The mirror renders nothing of its own: two drawings of one screen drift
    apart the day someone edits only one of them."""
    try:
        tmp = FRAME_FILE + ".tmp"
        frame.save(tmp, format="PNG")   # the name says .tmp; PIL needs telling
        os.replace(tmp, FRAME_FILE)
        globals()["_frame_stamp"] = time.time()
    except (OSError, ValueError) as e:      # a mirror is never worth a crash
        print(f"mirror: {e}", file=sys.stderr, flush=True)


_surface_was = None


def publish_surface(surface):
    """What the buttons mean, and what the app needs to draw this view itself.

    Written on its own clock rather than with the frame. The glass redraws only
    when the glass changes, and half of what the app shows -- which seat you are
    on, a question arriving, the page -- can change without moving a pixel of
    it. Tying the two put the app a redraw behind its own state."""
    global _surface_was
    try:
        text = json.dumps(surface, default=str)
    except (TypeError, ValueError) as e:
        print(f"mirror: {e}", file=sys.stderr, flush=True)
        return
    if text == _surface_was:
        return
    _surface_was = text
    try:
        tmp = SURFACE_FILE + ".tmp"
        with open(tmp, "w") as f:
            f.write(text)
        os.replace(tmp, SURFACE_FILE)
    except OSError as e:
        print(f"mirror: {e}", file=sys.stderr, flush=True)


def take_command():
    """A press from the browser mirror, consumed exactly once."""
    try:
        with open(CMD_FILE) as f:
            raw = f.read()
        os.remove(CMD_FILE)
    except OSError:
        return None
    try:
        cmd = json.loads(raw)
    except json.JSONDecodeError:
        return None         # already unlinked: a bad one cannot loop
    return cmd if isinstance(cmd, dict) else None


def reload_macros():
    """Pick up edits from mapui.py without a restart. Cheap: one stat a poll."""
    global _macros_mtime, MACROS, page
    try:
        mtime = os.path.getmtime(MACRO_FILE)
    except OSError:
        return False
    if mtime == _macros_mtime:
        return False
    _macros_mtime = mtime
    labels, pages = load_macros()
    LABELS[:], PAGES[:] = labels, pages
    # the page you were on may have been the one that just went empty
    page, MACROS = min(page, len(PAGES) - 1), PAGES[min(page, len(PAGES) - 1)]
    return True


# ---------------------------------------------------------------- herdr

# tmux hosts the panes now, so herdr can be closed and the surface keeps
# working. PUSH_BACKEND=herdr goes back, for as long as that is useful.
BACKEND = os.environ.get("PUSH_BACKEND", "tmux")


def backend_said(stdout, stderr):
    """Which stream carried the answer.

    herdr 0.6.8 reports an error as JSON on stdout and exits 0. 0.8.2 moved it
    to stderr with exit 1. start_agent greps this function's RETURN VALUE for
    agent_name_taken to find a free name, so an error arriving on the stream
    we do not read looks exactly like success -- and it names the second agent
    the same as the first, quietly. Read whichever stream actually spoke.

    Only a stream carrying an error object wins; a bare warning on stderr is
    not an answer and must not replace one."""
    if '"error"' not in stdout and '"error"' in stderr:
        return stderr
    return stdout


def herdr(*args):
    """The one call every pane operation goes through, and the one place that
    knows which backend is answering.

    A backend reports failure as a JSON error object, and a helper that only
    returned stdout swallowed it -- Add Device failed silently for hours that
    way. Which stream it arrives on is backend_said's problem, not this
    function's; both get said out loud either way."""
    if BACKEND == "tmux":
        import term  # lazy: a broken term.py should not stop push_cc importing
        text, stderr = term.dispatch(args), ""
    else:
        out = subprocess.run(["herdr", *args],
                             capture_output=True, text=True, timeout=10)
        text, stderr = backend_said(out.stdout.strip(), out.stderr.strip()), \
            out.stderr.strip()
    if '"error"' in text:
        try:
            err = json.loads(text)["error"]
            print(f"{BACKEND} {args[0]} {args[1]}: {err.get('code')}: "
                  f"{err.get('message', '')[:120]}", file=sys.stderr, flush=True)
        except (json.JSONDecodeError, KeyError, IndexError):
            print(f"{BACKEND} {' '.join(args[:2])}: {text[:160]}",
                  file=sys.stderr, flush=True)
    elif stderr:
        print(f"{BACKEND} {' '.join(args[:2])}: {stderr[:160]}",
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


def swap_pads(macros, a, b):
    """Exchange two pads, in place.

    There is no separate move: an empty destination holds None, and None swaps
    in as happily as anything else. One operation covers both, so there is no
    second path to get wrong."""
    macros[a], macros[b] = macros[b], macros[a]


def answer_keys(pick, sel):
    """Keys that move a select widget's caret from `sel` to `pick` and commit.

    The lit pads have been painted as answer buttons since the grid became all
    shortcuts, and pressing one has done nothing since -- a button that lights
    and then ignores you is worse than one that never lights. Walking the caret
    is how the encoder already answers; this just aims it at a pad."""
    step = pick - sel
    return (DOWN if step > 0 else UP) * abs(step) + ENTER


def answer_pad(i, count):
    """Which option a pad stands for while a question is up, or None.

    The left column, down from the top, one pad an option -- the order the
    glass lists them in. A column of lit pads on an otherwise dark grid reads
    as a list; the same options as whole rows read as a grid that has caught
    fire, which is what six of them looked like.

    Note 36 is the bottom-left pad, so the top of the column is the highest
    multiple of eight."""
    if i % SLOTS:
        return None
    row = SLOTS - 1 - i // SLOTS
    return row if row < count else None


def page_scroll(scroll, delta, page=PAGE_LINES):
    """Where a page button lands.

    Scroll 0 is the summary, not the top of the transcript, so the first page
    back has to arrive at the live tail rather than six lines above it --
    otherwise leaving the TLDR silently skips the newest thing said."""
    if scroll == 0 and delta > 0:
        return 1
    return max(0, scroll + delta * page)


def step_slot(current, delta, slots):
    """Next occupied slot, wrapping. Empty pads are not worth stopping on."""
    live = sorted(s for s in slots if slots[s])
    if not live:
        return current
    if current in live:
        return live[(live.index(current) + delta) % len(live)]
    return live[0] if delta > 0 else live[-1]


# ---------------------------------------------------------------- chains

def row_blocks(macros, row):
    """A row's sequences: runs of filled pads, split wherever one is blank.

    A blank pad is a break, not a gap to step over. Two things that merely
    share a row are usually two sequences, and joining them silently is how
    `commit this` ends up in front of `merge every PR`. The gap was already
    there in every layout anyone had built; it just did not mean anything."""
    blocks, run = [], []
    for i in range(row * 8, row * 8 + 8):
        if macros[i]:
            run.append(i)
        elif run:
            blocks.append(run)
            run = []
    if run:
        blocks.append(run)
    return blocks


def chain_steps(macros, start):
    """The whole sequence `start` belongs to.

    A block is the unit, not the pad you happened to hit: the pads either side
    of a gap are one thought, and you pick between thoughts with left/right
    rather than by aiming at a different pad. Still no storage of its own --
    mapui reorders by dragging and the row is still the record."""
    for block in row_blocks(macros, start // 8):
        if start in block:
            return block
    return []


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

    def cycle(self, macros, delta):
        """Left/right walks the row's other sequences, while still armed.

        Recomputed rather than remembered: mapui can rewrite the row between
        arming and choosing, and a stale block list would arm pads that moved."""
        if not self.steps:
            return False
        blocks = row_blocks(macros, self.steps[0] // 8)
        if len(blocks) < 2:
            return False
        here = next((k for k, b in enumerate(blocks) if b[0] == self.steps[0]), 0)
        self.steps = blocks[(here + delta) % len(blocks)]
        self.index = 0
        return True

    def seq(self, macros):
        """Which of the row's sequences this is, and how many there are."""
        if not self.steps:
            return 0, 1
        blocks = row_blocks(macros, self.steps[0] // 8)
        here = next((k for k, b in enumerate(blocks) if b[0] == self.steps[0]), 0)
        return here, max(1, len(blocks))

    def info(self, macros):
        here, total = self.seq(macros)
        return {"agent": self.name, "slot": self.slot, "index": self.index,
                "phase": self.phase, "seq": here, "seqs": total,
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


# ---------------------------------------------------------------- subagents

_done_cache = {}      # transcript -> (size, ids that have come back)
SUB_STALE_S = 600     # a task with no result and a cold log has died, not run


def finished_calls(path):
    """Tool calls this session already has a result for.

    ponytail: the whole file, re-read whenever it grows -- 40ms on an 11MB
    transcript, and only asked for while the sessions view is up. Read from an
    offset if that ever bites."""
    try:
        size = os.stat(path).st_size
    except OSError:
        return set()
    hit = _done_cache.get(path)
    if hit and hit[0] == size:
        return hit[1]
    ids = set()
    try:
        with open(path, errors="replace") as f:
            for line in f:
                if '"tool_result"' not in line:
                    continue        # cheap reject: most lines are not results
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for block in row.get("message", {}).get("content") or []:
                    if (isinstance(block, dict)
                            and block.get("type") == "tool_result"):
                        ids.add(block.get("tool_use_id"))
    except OSError:
        return set()
    _done_cache[path] = (size, ids)
    return ids


def subagents(agent, limit=None, now=None):
    """The Task subagents of one session, oldest first.

    Claude writes each one a log and a sibling .meta.json carrying the
    description it was dispatched with -- which is the only human name a
    subagent ever gets. Whether it is still going is the parent's business:
    the task is running until its tool call comes back."""
    path = transcript(agent) if agent else None
    if not path:
        return []
    done = finished_calls(path)
    now = time.time() if now is None else now
    out = []
    for meta in glob.glob(os.path.join(path[:-len(".jsonl")],
                                       "subagents", "agent-*.meta.json")):
        try:
            with open(meta) as f:
                info = json.load(f)
            born = os.path.getmtime(meta)
        except (OSError, json.JSONDecodeError):
            continue
        log = meta[:-len(".meta.json")] + ".jsonl"
        try:
            touched = os.path.getmtime(log)
        except OSError:
            touched = born
        out.append({
            "id": os.path.basename(meta)[len("agent-"):-len(".meta.json")],
            "label": info.get("description") or info.get("agentType") or "task",
            "type": info.get("agentType") or "?",
            "model": info.get("model") or "",
            "log": log, "at": born,
            # no result AND a cold log means it died with its session rather
            # than finishing -- otherwise a crash leaves a pad pulsing forever
            "running": (info.get("toolUseId") not in done
                        and now - touched < SUB_STALE_S)})
    out.sort(key=lambda x: x["at"])
    return out[-limit:] if limit else out


def tool_line(block):
    """A tool call as one row: the name, and the argument that identifies it."""
    args = block.get("input") or {}
    hint = next((str(args[k]) for k in
                 ("file_path", "path", "pattern", "command", "prompt",
                  "description", "url", "query")
                 if args.get(k)), "")
    hint = " ".join(hint.split())[:60]
    return f"{block.get('name', '?')}({hint})" if hint else str(block.get("name"))


def sub_lines(path, tail=TAIL_BYTES):
    """One subagent's log as pane-shaped rows: what it said flush left, what
    it did indented -- which is exactly what the focus view greys out."""
    try:
        size = os.stat(path).st_size
        with open(path, "rb") as f:
            f.seek(max(0, size - tail))
            chunk = f.read()
    except OSError:
        return []
    rows = chunk.split(b"\n")
    if size > tail:
        rows = rows[1:]             # the first is half a line
    out = []
    for row in rows:
        try:
            entry = json.loads(row)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        content = (entry.get("message") or {}).get("content")
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                out += [l for l in block.get("text", "").splitlines() if l.strip()]
            elif block.get("type") == "tool_use":
                out.append("  " + tool_line(block))
    return out


def sub_info(sub, slot=0, scroll=0):
    """A subagent shaped like focus_info, so one renderer draws both."""
    lines = sub_lines(sub["log"])
    said = [l for l in lines if not l.startswith("  ")]
    return {"slot": slot, "name": sub["label"],
            "model": short_model(sub["model"]),
            # the header's third field. A subagent has no effort of its own to
            # show, and its type is the thing you actually want named there
            "effort": sub["type"],
            "status": "working" if sub["running"] else "idle",
            "act": "", "say": said[-1] if said else "", "pending": "",
            "tldr": tldr(lines) or [], "suggested": False,
            "opts": [], "sel": None, "lines": lines, "scroll": scroll,
            "context": context_used(sub["log"]) / context_limit(sub["model"])}


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


_model_tok = {}     # path -> (offset, {model: totals}). Appends only, like usage.
_MODEL_RE = re.compile(rb'"model":"([^"]+)"')


def model_usage_for(agent):
    """Tokens by model for one session, accumulated incrementally.

    The same source and the same arithmetic /usage uses for its session block:
    the transcript, one JSON object per line, each carrying both the model that
    produced it and what it spent. usage_for scans the whole tail for numbers
    and so cannot say which model spent them -- parsing per line is what keeps
    the two paired. No dollars: see the README. Nothing here knows the plan,
    and on this one the marginal cost of a token is zero."""
    path = transcript(agent) if agent else None
    try:
        size = os.stat(path).st_size if path else 0
    except OSError:
        return {}
    off, tot = _model_tok.get(path, (0, {}))
    if size < off:                       # truncated or replaced; start over
        off, tot = 0, {}
    if size > off:
        try:
            with open(path, "rb") as f:
                f.seek(off)
                chunk = f.read()
        except OSError:
            return tot
        cut = chunk.rfind(b"\n") + 1    # never parse a half-written line
        if cut:
            for line in chunk[:cut].splitlines():
                m = _MODEL_RE.search(line)
                if not m:
                    continue
                seat = tot.setdefault(short_model(m.group(1).decode()),
                                      {k: 0 for k in _USAGE_FIELDS})
                for key, pat in _USAGE_FIELDS.items():
                    seat[key] += sum(int(v) for v in re.findall(pat, line))
            _model_tok[path] = (off + cut, tot)
    return tot


def model_totals(live):
    """Every agent's per-model tokens, added up. Account-wide is what /usage
    reports and what you actually want to look at; per agent is the other
    view's job."""
    out = {}
    for agent in live:
        for name, tot in model_usage_for(agent).items():
            seat = out.setdefault(name, {k: 0 for k in _USAGE_FIELDS})
            for key, value in tot.items():
                seat[key] += value
    # <synthetic> is Claude Code's marker for messages it generated itself, not
    # a model, and it spends nothing. Dropping empties covers it and whatever
    # else turns up wearing the same shape.
    return {k: v for k, v in out.items() if any(v.values())}


# ---------------------------------------------------------------- tests

# .claude/worktrees holds checkouts of the same repo -- and Add Track is what
# puts them there, so this project generates its own decoys. Counting them
# doubled cookoojobs from 262 files to 525 and lit failures in trees nobody
# was working in.
TEST_SKIP = {"node_modules", ".git", ".claude", "dist", "build", ".next",
             "coverage", "__snapshots__", ".turbo", "vendor"}
TEST_PATS = (".test.", ".spec.")
# jest and playwright both print one of these per file as they go. The JSON
# reporters are more of a contract but only speak at the end, and a grid that
# stays dark for three minutes and then flips is not worth hardware.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
# PASS/FAIL only. jest also ticks every individual assertion with ✓, and
# accepting those filled the grid with pads named "concatenating" and "emits".
# The guard below is the real fix: a result line names a test FILE.
_RESULT_RE = re.compile(r"^\s*(PASS|FAIL)\s+(\S+)")
# playwright drives real browsers against a real app, detox drives a simulator.
# They belong in the tree, but not behind a pad you might lean on.
TEST_SLOW = ("playwright", "detox", "cypress")
# not-run is its own colour: dark grey says no information, and a green pad
# for something that never ran is the one lie a test display must not tell
TEST_LEDS = {"pass": (GREEN, STATIC), "fail": (RED, BLINK),
             "run": (YELLOW, PULSE), "": (TAB_DIM, STATIC)}


def test_files(root):
    """Every test file under `root`, relative and sorted, decoys dropped."""
    out = []
    for here, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in TEST_SKIP and not d.startswith(".")]
        for name in files:
            if any(p in name for p in TEST_PATS):
                out.append(os.path.relpath(os.path.join(here, name), root))
    return sorted(out)


def test_packages(root):
    """The runnable units: every package.json with a test script.

    In a monorepo these are also the top of the tree -- cookoojobs' four
    sub-packages are both the directories you zoom into and the things you
    run, so structure and execution agree without being made to."""
    out = []
    for here, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in TEST_SKIP and not d.startswith(".")]
        if "package.json" not in files:
            continue
        try:
            with open(os.path.join(here, "package.json")) as f:
                cmd = (json.load(f).get("scripts") or {}).get("test", "")
        except (OSError, ValueError):
            continue
        if not cmd or cmd.startswith("npm run"):     # a router, not a runner
            continue
        out.append({"name": os.path.relpath(here, root),
                    "cwd": here, "cmd": cmd,
                    "slow": any(s in cmd for s in TEST_SLOW)})
    return sorted(out, key=lambda p: p["name"])


def test_tree(paths):
    """Nested dicts; a file is a leaf holding None."""
    root = {}
    for p in paths:
        node = root
        parts = p.split(os.sep)
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node.setdefault(parts[-1], None)
    return root


def tree_at(tree, path):
    """The node a zoom path points at, or None if it has gone away -- macros
    are not the only thing that can change under a view."""
    node = tree
    for part in path:
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def leaves_under(node, prefix=""):
    """Every file beneath a node, so a directory can answer for its children."""
    if node is None:
        return [prefix]
    out = []
    for name, child in node.items():
        out.extend(leaves_under(child, f"{prefix}/{name}" if prefix else name))
    return out


def roll_up(states):
    """One colour for a directory, from what is beneath it. Failure wins:
    the whole point is that you can follow red down to the file without
    knowing where it lives."""
    if not states:
        return ""
    for want in ("fail", "run", "pass"):
        if want in states:
            return want
    return ""


def test_items(tree, path, states):
    """The children at this zoom, each wearing the worst state beneath it.

    Worst, not average: a directory is red if anything under it failed, which
    is what lets you follow red down to the file without knowing where it
    lives. That descent is the whole reason this is a tree and not a list."""
    node = tree_at(tree, path)
    if not isinstance(node, dict):
        return []
    out = []
    for name in sorted(node):
        child = node[name]
        under = leaves_under(child, "/".join(path + [name]))
        seen = {states.get(leaf, "") for leaf in under} - {""}
        out.append({"name": name, "dir": isinstance(child, dict),
                    "state": roll_up(seen)})
    return out[:MACRO_SLOTS]


def parse_result(line):
    """(status, file) from a runner's stdout, or None. Colour codes stripped:
    jest paints PASS green and the escape lands before the word."""
    m = _RESULT_RE.match(_ANSI_RE.sub("", line))
    if not m:
        return None
    path = m.group(2)
    if not any(pat in path for pat in TEST_PATS):
        return None                      # a test's name, not a file's
    return ("pass" if m.group(1) == "PASS" else "fail"), path


_tests = {"root": None, "tree": {}, "pkgs": [], "busy": False}


def tests_for(root):
    """A repo's tree and its runnable packages, discovered off the loop.

    Both walks together are 86ms on cookoojobs -- inside the release timer,
    but only just, and the next repo is always bigger. Discovered once per
    repo on a thread and then held: nothing here moves while you look at it."""
    if root and _tests["root"] != root and not _tests["busy"]:
        _tests["busy"] = True

        def go():
            try:
                found = test_tree(test_files(root)), test_packages(root)
            except OSError:
                found = {}, []
            _tests.update(root=root, tree=found[0], pkgs=found[1], busy=False)

        threading.Thread(target=go, daemon=True).start()
    if _tests["root"] != root:
        return {}, []
    return _tests["tree"], _tests["pkgs"]


def scope_packages(pkgs, path, slow=False):
    """The runners a zoom path covers.

    Slow ones stay out unless asked for: playwright starts browsers and detox
    a simulator, and a pad is a very low bar for either."""
    here = "/".join(path)
    if here:
        pkgs = [p for p in pkgs
                if p["name"] == here or p["name"].startswith(here + os.sep)
                or here.startswith(p["name"] + os.sep)] or pkgs
    return [p for p in pkgs if slow or not p["slow"]]


class TestRun:
    """One pass of a repo's unit tests, watched as it goes.

    Sequential, the way `npm test` runs them, and on its own thread: a run is
    minutes and the poll loop owes a keystroke every 60ms. Nothing here is
    read by the loop except a dict it never writes."""

    def __init__(self, root, packages, only=None):
        self.root, self.packages, self.only = root, packages, only
        self.states = {}         # path relative to root -> pass | fail | run
        self.now = ""            # the package currently running
        self.done, self.stop = False, False
        self.proc = None

    def key(self, pkg, printed):
        """Runners print paths relative to their own package, the tree is
        relative to the repo. Without this every result misses its pad."""
        printed = printed.lstrip("./")
        if pkg["name"] in (".", ""):
            return os.path.normpath(printed)
        if printed.startswith(pkg["name"] + os.sep):
            return os.path.normpath(printed)
        return os.path.normpath(os.path.join(pkg["name"], printed))

    def run(self):
        for pkg in self.packages:
            if self.stop:
                break
            self.now = pkg["name"]
            cmd = pkg["cmd"]
            if self.only:
                # the runner works from its own package; the tree counts from
                # the repo, so hand it back a path it recognises
                rel = self.only
                if pkg["name"] not in (".", "") and rel.startswith(pkg["name"] + os.sep):
                    rel = rel[len(pkg["name"]) + 1:]
                cmd = f"{cmd} {rel}"
            # npm is what normally puts node_modules/.bin on PATH, and these
            # scripts say bare `jest`. Without this the process starts, says
            # "jest: not found", and the grid reports nothing at all.
            binp = os.pathsep.join(
                [os.path.join(pkg["cwd"], "node_modules", ".bin"),
                 os.path.join(self.root, "node_modules", ".bin"),
                 os.environ.get("PATH", "")])
            try:
                self.proc = subprocess.Popen(
                    ["sh", "-c", cmd], cwd=pkg["cwd"], text=True,
                    env=dict(os.environ, PATH=binp),
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            except OSError as e:
                print(f"tests: {pkg['name']} would not start ({e})", flush=True)
                continue
            for line in self.proc.stdout:
                if self.stop:
                    self.proc.terminate()
                    break
                got = parse_result(line)
                if got:
                    status, printed = got
                    self.states[self.key(pkg, printed)] = status
            self.proc.wait()
            self.proc = None
        self.now, self.done = "", True

    def start(self):
        threading.Thread(target=self.run, daemon=True).start()
        return self

    def abort(self):
        self.stop = True
        if self.proc:
            try:
                self.proc.terminate()
            except OSError:
                pass

    def tally(self):
        vals = list(self.states.values())
        return vals.count("pass"), vals.count("fail")


# ---------------------------------------------------------------- plan usage

CREDS_FILE = os.path.expanduser("~/.claude/.credentials.json")
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
USAGE_TTL = 120.0        # the endpoint is rate limited; /usage itself caches longer
# The reply also carries five_hour/seven_day at the top level, but `limits` is
# the normalised form: each entry already says what it is, how full, when it
# resets and how worried to be. Reading that instead means a window we have
# never heard of still draws, and one that goes away stops drawing.
USAGE_KINDS = {"session": "session", "weekly_all": "week"}
USAGE_MODES = VIEW_MODES["usage"]   # plan bars, tokens by model, per agent
_plan = {"at": -USAGE_TTL, "bars": [], "busy": False, "err": ""}


def claude_token():
    """The live OAuth token.

    Keychain first, and it has to be: the copy in ~/.claude went stale eight
    days before this was written and was never rewritten, because the entry
    macOS holds is the one Claude Code actually keeps fresh. We only ever
    read. Refreshing is Claude Code's job -- a refresh token that rotates
    under it would log every session out, which is a poor trade for a bar on
    a MIDI controller."""
    try:
        out = subprocess.run(
            ["security", "find-generic-password",
             "-s", "Claude Code-credentials", "-w"],
            capture_output=True, text=True, timeout=10)
        if not out.returncode:
            return json.loads(out.stdout)["claudeAiOauth"]["accessToken"]
    except Exception:
        pass
    try:                              # the file, for whatever it is worth
        with open(CREDS_FILE) as f:
            return json.load(f)["claudeAiOauth"]["accessToken"]
    except (OSError, KeyError, json.JSONDecodeError):
        return ""


def plan_fetch():
    """Ask the endpoint /usage asks. Runs on its own thread, never the loop.

    Undocumented: nothing promises this keeps working, so every field is
    optional and a bad shape yields no bars rather than an exception. curl,
    not urllib, because this venv's Python has no CA bundle -- and the token
    goes in on stdin so it never reaches an argument list or this log."""
    token = claude_token()
    if not token:
        _plan.update(err="no credentials", busy=False)
        return
    try:
        out = subprocess.run(
            ["curl", "-sS", "--max-time", "15", "-H", "@-", USAGE_URL],
            input=f"Authorization: Bearer {token}\n"
                  f"anthropic-beta: oauth-2025-04-20\n",
            capture_output=True, text=True, timeout=20)
        raw = json.loads(out.stdout)
    except Exception as e:                      # network, timeout, or not JSON
        _plan.update(err=f"{type(e).__name__}", busy=False)
        return
    bars = []
    for seat in raw.get("limits") or []:
        if not isinstance(seat, dict) or seat.get("percent") is None:
            continue
        model = ((seat.get("scope") or {}).get("model") or {}).get("display_name")
        bars.append({
            # a scoped window names its own model, which is the only place
            # "Fable" appears -- there is no fable key to look up
            "label": model or USAGE_KINDS.get(seat.get("kind"), seat.get("kind", "?")),
            "used": max(0.0, min(1.0, float(seat["percent"]) / 100.0)),
            "severity": seat.get("severity") or "normal",
            "active": bool(seat.get("is_active")),
            "resets": seat.get("resets_at") or ""})
    _plan.update(bars=bars, err="" if bars else "no limits in reply", busy=False)


def plan_usage(now):
    """The cached bars, refreshing behind you when they go stale."""
    if not _plan["busy"] and now - _plan["at"] >= USAGE_TTL:
        _plan.update(at=now, busy=True)
        threading.Thread(target=plan_fetch, daemon=True).start()
    return _plan["bars"], _plan["err"]


PR_TTL = 60.0       # gh reaches the network; a minute-old list is still true
PR_MAX = 20         # what one gh call asks for; the screen shows its worst
_prs = {}           # checkout -> {at, busy, rows, err}


def check_state(rollup):
    """Many check runs, one colour. The worst one wins, which is the only
    summary a glance can use."""
    seen = set()
    for run in rollup or []:
        if not isinstance(run, dict):
            continue
        # check runs report conclusion+status, plain statuses report state
        done = (run.get("conclusion") or run.get("state") or "").upper()
        if not done or run.get("status") in ("QUEUED", "IN_PROGRESS", "PENDING"):
            seen.add("pending")
        elif done in ("SUCCESS", "NEUTRAL", "SKIPPED"):
            seen.add("pass")
        elif done in ("PENDING", "EXPECTED"):
            seen.add("pending")
        else:
            seen.add("fail")        # failure, cancelled, timed out, action req
    for state in ("fail", "pending", "pass"):
        if state in seen:
            return state
    return "none"


def pr_rows(raw, branch=""):
    """gh's JSON -> what the screen draws, worst news first."""
    rows = []
    for pr in raw if isinstance(raw, list) else []:
        rows.append({
            "n": pr.get("number", 0),
            "title": " ".join((pr.get("title") or "").split()),
            "who": (pr.get("author") or {}).get("login", ""),
            "draft": bool(pr.get("isDraft")),
            "checks": check_state(pr.get("statusCheckRollup")),
            # the shortest true label: gh says REVIEW_REQUIRED for "nobody has
            # looked", which is the common case and not worth a word
            "review": {"APPROVED": "approved",
                       "CHANGES_REQUESTED": "changes"}.get(
                           pr.get("reviewDecision") or "", ""),
            # the one you are standing on, which is why you opened this view
            "mine": bool(branch) and pr.get("headRefName") == branch})
    rank = {"fail": 0, "pending": 1, "none": 2, "pass": 3}
    # your branch first, then whatever is on fire: the screen only draws the
    # top few, so the order is what decides which ones you get to see
    rows.sort(key=lambda r: (not r["mine"], rank.get(r["checks"], 3), r["n"]))
    return rows


def pr_fetch(where):
    """One gh call, on its own thread. gh is a second of network, and the loop
    has half of one."""
    slot = _prs[where]
    try:
        head = subprocess.run(["git", "-C", where, "branch", "--show-current"],
                              capture_output=True, text=True, timeout=10)
        out = subprocess.run(
            ["gh", "pr", "list", "--limit", str(PR_MAX), "--json",
             "number,title,author,isDraft,reviewDecision,statusCheckRollup,"
             "headRefName"],
            cwd=where, capture_output=True, text=True, timeout=30)
        if out.returncode:
            # gh's own words: not a repo, no remote, not logged in
            first = (out.stderr.strip().splitlines() or ["gh failed"])[0]
            slot.update(rows=[], err=first[:70], busy=False)
            return
        rows = pr_rows(json.loads(out.stdout), head.stdout.strip())
    except Exception as e:
        slot.update(rows=[], err=type(e).__name__, busy=False)
        return
    slot.update(rows=rows, err="", busy=False)


def open_prs(where, now):
    """The cached list for one checkout, refreshing behind you."""
    if not where:
        return [], ""
    slot = _prs.setdefault(where, {"at": -PR_TTL, "rows": [], "busy": False,
                                   "err": "asking gh..."})
    if not slot["busy"] and now - slot["at"] >= PR_TTL:
        slot.update(at=now, busy=True)
        threading.Thread(target=pr_fetch, args=(where,), daemon=True).start()
    return slot["rows"], slot["err"]


_ctx_cache = {}     # path -> (size, used)
# Every current model is 1M except Haiku. Checked against the model table
# rather than recalled: assuming 200k put these sessions at 137% full.
CONTEXT_LIMITS = (("haiku", 200_000),)
CONTEXT_DEFAULT = 1_000_000


def context_used(path):
    """Tokens on the input side of the last request in a transcript. Any
    transcript: a session's, or one subagent's own log."""
    try:
        size = os.stat(path).st_size if path else 0
    except OSError:
        return 0
    if not size:
        return 0
    hit = _ctx_cache.get(path)
    if hit and hit[0] == size:
        return hit[1]
    try:
        with open(path, "rb") as f:
            f.seek(max(0, size - TAIL_BYTES))
            tail = f.read()
    except OSError:
        return 0
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
    return used


def context_limit(model):
    return next((v for k, v in CONTEXT_LIMITS if k in model), CONTEXT_DEFAULT)


def context_for(agent):
    """How full this session's context is, and out of what.

    Not the usage totals: those accumulate over the whole session. Context is
    the input side of the most recent request only."""
    limit = context_limit(model_for(agent))
    return context_used(transcript(agent)), limit


def usage_col(agent):
    if agent is None:
        return None
    u = usage_for(agent)
    return (agent_name(agent),
            u.get("out", 0),
            u.get("cread", 0) + u.get("inp", 0) + u.get("cwrite", 0),
            bool(agent.get("focused")))


# Two different things render as a numbered list, and they answer differently.
# A select widget puts a caret on the highlighted row -- a typed digit does not
# choose there, you arrow to it and press enter. Prose options carry no caret,
# and the digit is simply the reply you type. The caret is the discriminator.
_OPT_RE = re.compile(r"^\s*(❯|>)?\s*(\d+)\.\s+(.+?)\s*$")


_TLDR_RE = re.compile(r"^[*#\s]*TL;?DR\b", re.I)
# What Claude Code draws in the margin: answer, activity, recap, prompt.
_MARKERS = ("\u23fa", "\u273b", "\u203b", "\u276f")


def tldr(lines):
    """The agent's last word, which is the only part of a pane worth reading
    at a glance.

    An explicit TLDR section if it wrote one -- ours are told to end every
    substantive answer with one -- and otherwise its last answer with the
    indented rows that belong to it. `say` kept only the first of those, which
    is where most of the sentence lived. Raw scrollback is what the knobs are
    for; it should not be what you get by default."""
    for i in range(len(lines) - 1, -1, -1):
        if _TLDR_RE.match(lines[i].strip()):
            out = []
            # the heading itself is a wasted row: this view is the TLDR now,
            # and the screen has about five lines to spend
            for line in lines[i + 1:]:
                # the pane's own furniture ends the section: the next answer,
                # the status footer, a recap, the prompt. Running past them was
                # putting "recap:" and half a typed prompt in the summary.
                if line[:1] in _MARKERS and out:
                    break
                if line.strip():
                    out.append(line.strip())
            return out[:7]
    start = next((i for i in range(len(lines) - 1, -1, -1)
                  if lines[i].startswith("\u23fa")), None)
    if start is None:
        return []
    out = [lines[start][1:].strip()]
    for line in lines[start + 1:]:
        if not line.startswith((" ", "\t")) or not line.strip():
            break
        out.append(line.strip())
    return out


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


def read_pane(lines):
    """The question, the activity, and the options, out of a rendered pane.

    A caret is required before any of it counts, and only the list the caret is
    actually in survives. _OPT_RE matches anything that opens with "1." and
    Claude writes numbered lists in prose all day; without the caret, a recap
    with two bullets became a two-option question, which takes the whole grid
    and offers to answer it. Without the run-splitting, a pane holding prose
    counting to four above a widget counting to six answered as one list of
    ten -- and answer_keys walks the caret from where it is to where you
    pointed, so a caret at the wrong index sends the wrong number of downs into
    a live session."""
    runs, cur, say, act, at_run, at_opt = [], [], "", "", None, None
    for line in lines:
        m = _OPT_RE.match(line)
        if m and not line.lstrip().startswith(("⏺", "✻")):
            n = int(m.group(2))
            # numbering that does not carry on from the line above starts a new
            # list. A pane holds more than one at a time -- prose that counts to
            # four, then the widget counting to six -- and reading them as one
            # run puts the caret at an index in a list that does not exist.
            if cur and n != cur[-1][0] + 1:
                runs.append(cur)
                cur = []
            if m.group(1):
                at_run, at_opt = len(runs), len(cur)
            cur.append((n, m.group(2), m.group(3)))
        elif line.startswith("⏺"):
            say = line[1:].strip()             # new answer, every stale list with it
            runs, cur, at_run, at_opt = [], [], None, None
        elif line.startswith("✻"):
            act = line[1:].strip()
    runs.append(cur)
    if at_run is None:                         # numbered prose, not a question
        return {"say": say, "act": act, "opts": [], "sel": None}
    return {"say": say, "act": act, "sel": at_opt,
            "opts": [(num, label) for _, num, label in runs[at_run]]}


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
    scan = read_pane(lines)
    pending = prompt_text(lines)
    return {**scan, "lines": body, "tldr": tldr(body),
            "pending": "" if scan["opts"] else pending}


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


def focus_info(slot, agent, summary, scroll=0, suggested=False):
    used, limit = context_for(agent)
    return {"slot": slot,
            "name": agent_name(agent),
            "model": model_for(agent),
            "effort": effort_for(agent),
            "status": agent.get("agent_status"),
            **{k: summary.get(k, "") for k in ("act", "say", "pending")},
            "tldr": summary.get("tldr") or [],
            # a macro put this there and you have not touched it yet, so it is
            # a proposal to read, not a sentence you are in the middle of
            "suggested": suggested,
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
    return {"name": agent_name(agent),
            "status": agent.get("agent_status"),
            "model": model_for(agent),
            "effort": effort_for(agent),
            "sub": agent.get("terminal_id", "")[-6:],
            # the app acts on a seat -- rename, close -- and needs to say
            # which agent it means. The screen shows `sub`; this is the handle.
            "tid": agent.get("terminal_id"),
            "focused": bool(agent.get("focused")),
            "context": used / limit if limit else 0.0}


def follow_focus(focused, slots, current, pinned=False):
    """Which slot the Push should be sitting on, given the pane herdr has
    focused on the computer.

    The surface is a second pair of hands on the same sessions, not a separate
    place you are also somewhere -- clicking a pane on screen and finding the
    Push still pointed at the last one is the bug. Solo is the opt-out: it
    already means stay put, for the same reason."""
    if pinned or focused is None:
        return current
    slot = next((s for s, tid in slots.items() if tid == focused), None)
    return current if slot is None else slot


def agent_name(agent):
    agent = agent or {}
    return agent.get("name") or os.path.basename(agent.get("cwd", "")) or "?"


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
    """The renderers, and the 960x160 panel if it can be had.

    Two answers, not one: the drawing is pure PIL and the browser mirror wants
    it whether or not the hardware screen is there -- which is exactly when a
    mirror is worth having. Never fatal either way: the pads are the product,
    the screen is the label on it."""
    try:
        import display
    except Exception as e:                      # no PIL
        print(f"no display module ({e}); pads still work", file=sys.stderr)
        return None, None
    try:
        return display, display.Display()
    except Exception as e:              # no pyusb, no libusb, device busy
        print(f"no screen ({e}); pads and the browser mirror still work",
              file=sys.stderr)
        return display, None


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
    last_focus = object()   # sentinel: the first poll follows whatever is focused
    shown, frame, view = None, None, 0
    current, summary, arming = 0, {}, False
    scrolls, peek = {}, None        # scroll is per agent; peek is a held finger
    shifted, pinned = False, False
    macro_down, talk_src, previewing = None, None, None
    arm_held = {cc: False for cc in ARM_CCS}   # not `held`: a local below took it
    published = object()   # sentinel: nothing published yet
    # (kind, deadline, index, name): asked something, waiting on an answer.
    # One variable for both questions -- the yes/no row, the timeout and the
    # screen are identical, only what yes does differs. index means a session
    # slot to close, or a subagent pad to focus.
    confirm = None
    subfocus, subs = None, []   # (session, subagent id) the focus view is on
    asking, next_sweep, was_asking = set(), 0.0, False
    chain, automating = None, False   # a row of pads queued at one agent
    strip_level, strip_touch = None, False  # uncommitted until the finger lifts
    moving = None        # None off, -1 waiting for a pad, >=0 the pad in hand
    view_mode = {}       # view name -> which of its modes; its own button walks them
    test_path, test_run, titems = [], None, []   # where you are in the tree
    # the exact text the last macro inserted. Compared rather than remembered
    # as a flag: type one character onto it and it stops matching, which is
    # precisely when it stops being a suggestion. Nothing to reset, nothing to
    # leak, and submitting empties the prompt so it lapses on its own.
    inserted = ""

    def say_yes():
        """The answer to whichever question is on the glass."""
        nonlocal confirm, current, view, shown, subfocus
        kind, _, idx, nm = confirm
        if kind == "focus":
            sub = subs[idx] if idx < len(subs) else None
            if sub:
                subfocus = ((cur or {}).get("terminal_id"), sub["id"])
                show("focus", 0)
                scrolls[current] = 0
                print(f"confirmed -> focus subagent {nm!r}", flush=True)
        else:
            slot = idx
            pane = (by_id.get(slots.get(slot)) or {}).get("pane_id")
            print(f"confirmed -> closing {pane}", flush=True)
            if pane:
                herdr("pane", "close", pane)
                slots.pop(slot, None)
            if current == slot:
                current = step_slot(current, 1, slots)
        confirm, shown = None, None

    def tap_tab(i):
        """A view button, from the Push or from the mirror in the browser."""
        nonlocal view, shown
        if not 0 <= i < len(VIEWS):
            return
        name, modes = VIEWS[i], VIEW_MODES.get(VIEWS[i], 1)
        # already here: the same button walks that view's modes, so one button
        # owns one subject however deep it goes. Each view keeps its own place
        # -- one counter shared between them meant leaving one moved the other.
        if i == view:
            view_mode[name] = (view_mode.get(name, 0) + 1) % modes
        view, shown = i, None                   # force a redraw
        print(f"view -> {name}" + (f" {view_mode.get(name, 0) + 1}/{modes}"
                                   if modes > 1 else ""), flush=True)

    def tap_seat(slot):
        """A session button: focus it there, and follow it here."""
        nonlocal current, shown, subfocus
        if not slots.get(slot):
            return
        herdr("agent", "focus", slots[slot])
        # back out of a subagent: the seat you pressed is the session, not
        # something it spawned
        current, shown, subfocus = slot, None, None
        scrolls[slot] = 0

    def show(name, m=None):
        """Go to a view, and to one of its modes when the reason to go there
        is about a particular one."""
        nonlocal view, shown
        view, shown = VIEWS.index(name), None
        if m is not None:
            view_mode[name] = m

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
                    if confirm:                   # the row is a yes/no pair now
                        colour, anim = ((GREEN, STATIC) if s == YES_SLOT else
                                        (RED, BLINK) if s == NO_SLOT else
                                        (BLACK, STATIC))
                    elif s == talk_slot:
                        colour, anim = RED, STATIC
                    elif s == current and a:
                        colour, anim = WHITE, STATIC  # the one you are driving
                    push.cc("session", s, SESSION_CCS, colour, anim)
                    push.cc("tab", s, TAB_CCS,
                            VIEW_CC.get(VIEWS[s], TAB_DIM)
                            if s < len(VIEWS) else BLACK,
                            PULSE if s == view else STATIC)
                # the legend for the eight buttons under the glass
                seats = tuple((agent_name(a), a.get("agent_status"))
                              if (a := by_id.get(slots.get(s))) else None
                              for s in range(SLOTS))
                focused = next((a["terminal_id"] for a in live if a.get("focused")), None)
                # on the edge only: herdr's focus MOVING is what follows, so a
                # question that pulls the surface to another slot is not
                # dragged straight back by a focus that never changed
                if focused != last_focus:
                    last_focus = focused
                    seat_now = follow_focus(focused, slots, current, pinned)
                    if seat_now != current:
                        current, shown = seat_now, None
                        scrolls[seat_now] = 0
                        print(f"herdr focus -> slot {seat_now}", flush=True)
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
                cmd = take_command()
                if cmd:
                    print(f"mirror: {cmd}", flush=True)
                    if "tab" in cmd:
                        tap_tab(int(cmd["tab"]))
                    if "seat" in cmd:
                        tap_seat(int(cmd["seat"]))
                    if "page" in cmd and set_page(int(cmd["page"])):
                        shown = None
                    if "answer" in cmd and target:
                        k = int(cmd["answer"])
                        if 0 <= k < len(opts):
                            herdr("agent", "send", target,
                                  answer_keys(k, summary.get("sel") or 0))
                            print(f"answer {k + 1}/{len(opts)} from the mirror",
                                  flush=True)
                if reload_macros():
                    shown = None
                    print("macros reloaded", flush=True)
                if confirm and now >= confirm[1]:
                    print(f"{confirm[0]} request expired", flush=True)
                    confirm, shown = None, None

                if now >= next_sweep and not talking:
                    next_sweep = now + SWEEP_S
                    asking = sweep_panes(slots, by_id, current)
                # the pads in the sessions view are this session's subagents,
                # and the focus view may be sitting on one of them
                subs = (subagents(cur, MACRO_SLOTS)
                        if VIEWS[view] == "sessions" or subfocus else [])
                if subfocus and subfocus[0] != (cur or {}).get("terminal_id"):
                    subfocus = None          # you drove somewhere else
                troot = (cur or {}).get("cwd") or ""
                if VIEWS[view] == "tests":
                    ttree, tpkgs = tests_for(troot)
                    titems = test_items(ttree, test_path,
                                        test_run.states if test_run else {})
                if opts:
                    asking.add(current)
                else:
                    asking.discard(current)

                # jump on the edge only, so navigating away does not fight you
                if asking and not was_asking and not pinned:
                    ask_slot = current if current in asking else sorted(asking)[0]
                    current, scrolls[ask_slot] = ask_slot, 0
                    show("focus", 0)
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
                    # the grid becomes the options, and answering resumes it.
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

                mode = view_mode.get(VIEWS[view], 0)
                if disp_mod:            # drawn for the glass and the mirror both
                    # The app draws every view itself rather than scaling up a
                    # photograph of the glass, so it is handed the same dicts
                    # the renderers get. Assembled alongside them, never
                    # instead: one truth, two consumers.
                    data = {}
                    seat_cols = [panel_col(by_id.get(slots.get(s)))
                                 for s in range(SLOTS)]
                    if strip_level:
                        # outranks the chain: your finger is on the strip now
                        state = ("effort", strip_level)
                        drawn = (lambda l=strip_level:
                                 disp_mod.render_effort(l, EFFORTS))
                    elif previewing is not None:
                        m, idx = MACROS[previewing], MACRO_NOTES[previewing]
                        state = ("pad", previewing, repr(m))
                        drawn = (lambda mm=m, ii=idx: disp_mod.render_pad(mm, ii))
                    elif confirm:
                        kind, _, idx, nm = confirm
                        left = max(0, int(confirm[1] - now))
                        state = ("confirm", kind, idx, nm, left)
                        drawn = (lambda n=nm, i=idx, t=left, k=kind:
                                 disp_mod.render_confirm(n, i, t, k))
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
                    elif VIEWS[view] == "focus" and mode == 1:
                        cols = tuple(MACROS)
                        state = (view, "macros", cols, arming, moving)
                        drawn = (lambda c=cols, a=arming, m=moving:
                                 disp_mod.render_macros(c, a, m))
                    elif VIEWS[view] == "focus" and any(
                            subfocus and x["id"] == subfocus[1] for x in subs):
                        # the focus view, pointed at a subagent instead of the
                        # session that spawned it. One that has vanished falls
                        # through to the ordinary focus view below.
                        idx = next(i for i, x in enumerate(subs)
                                   if x["id"] == subfocus[1])
                        sinfo = sub_info(subs[idx], idx, scrolls.get(current, 0))
                        data = {"kind": "focus", "info": sinfo, "sub": True}
                        state = (view, "sub", repr(sinfo))
                        drawn = (lambda i=sinfo: disp_mod.render_focus(i))
                    elif VIEWS[view] == "focus":
                        pend = (summary.get("pending") or "").strip()
                        info = (focus_info(seat, cur, summary, scroll,
                                           bool(pend) and pend == inserted.strip())
                                if cur else None)
                        data = {"kind": "focus", "info": info}
                        state = (view, repr(info))
                        drawn = (lambda: disp_mod.render_focus(info)) if info else (
                            lambda: disp_mod.render((None,) * SLOTS))
                    elif VIEWS[view] == "usage":
                        # one question -- what is being spent -- answered at
                        # three altitudes: the account's plan, the models it
                        # spent on, then the agents that did the spending
                        if mode == 1:
                            tot = model_totals(live)
                            data = {"kind": "usage", "at": mode,
                                    "models": sorted(tot.items())}
                            state = (view, "tokens", repr(sorted(tot.items())), mode)
                            drawn = (lambda t=tot, m=mode:
                                     disp_mod.render_models(t, m, USAGE_MODES))
                        elif mode == 2:
                            cols = tuple(usage_col(by_id.get(slots.get(s)))
                                         for s in range(SLOTS))
                            data = {"kind": "usage", "at": mode, "agents": cols}
                            state = (view, "agents", cols, mode)
                            drawn = (lambda c=cols, m=mode:
                                     disp_mod.render_usage(c, m, USAGE_MODES))
                        else:                       # first: what you glance at
                            bars, uerr = plan_usage(now)
                            data = {"kind": "usage", "at": mode, "bars": bars,
                                    "err": uerr}
                            state = (view, "plan", repr(bars), uerr, mode)
                            drawn = (lambda b=bars, e=uerr, m=mode:
                                     disp_mod.render_plan(b, e, m, USAGE_MODES))
                    elif VIEWS[view] == "sessions" and mode == 1:
                        rows = tuple({"label": x["label"], "type": x["type"],
                                      "running": x["running"],
                                      "focused": bool(subfocus)
                                      and subfocus[1] == x["id"]} for x in subs)
                        data = {"kind": "subs", "rows": rows,
                                "repo": agent_name(cur)}
                        state = (view, "subs", rows)
                        drawn = (lambda r=rows, n=agent_name(cur):
                                 disp_mod.render_subs({"repo": n, "subs": r}))
                    elif VIEWS[view] == "prs":
                        rows, perr = open_prs(troot, now)
                        pinfo = {"repo": agent_name(cur), "rows": rows,
                                 "err": perr}
                        data = {"kind": "prs", **pinfo}
                        state = (view, repr(pinfo))
                        drawn = (lambda i=pinfo: disp_mod.render_prs(i))
                    elif VIEWS[view] == "tests":
                        ok, bad = test_run.tally() if test_run else (0, 0)
                        tinfo = {"repo": os.path.basename(troot) or "?",
                                 "path": list(test_path), "items": titems,
                                 "running": (test_run.now if test_run
                                             and not test_run.done else ""),
                                 "passed": ok, "failed": bad,
                                 "slow": any(p["slow"] for p in
                                             scope_packages(tpkgs, test_path, True))}
                        data = {"kind": "tests", **tinfo}
                        state = (view, repr(tinfo))
                        drawn = (lambda i=tinfo: disp_mod.render_tests(i))
                    else:
                        # the pads in this view are the subagents whichever mode
                        # the glass is in, so the app gets them either way
                        data = {"kind": "sessions", "repo": agent_name(cur),
                                "rows": [{"label": x["label"], "type": x["type"],
                                          "running": x["running"],
                                          "focused": bool(subfocus)
                                          and subfocus[1] == x["id"]}
                                         for x in subs]}
                        cols = tuple(panel_col(by_id.get(slots.get(s)))
                                     for s in range(SLOTS))
                        state = (view, cols)
                        drawn = (lambda c=cols: disp_mod.render(c))
                    # both legends, drawn once here rather than in nine
                    # renderers. Not on the two that take the whole glass -- a
                    # preview or a confirm is not a view, and neither left room
                    # for a band. The seats go into the state: a renamed or
                    # restatused agent has to force the redraw itself.
                    banded = previewing is None and not confirm
                    if banded:
                        state = (*state, seats, current)
                    if state != shown:              # re-render on change only
                        try:
                            shown, frame = state, drawn()
                            if banded:
                                frame = disp_mod.view_strip(frame, VIEWS, view,
                                                            page, len(PAGES))
                                frame = disp_mod.seat_strip(frame, seats, current)
                            publish_frame(frame)
                        except Exception as e:
                            # the pads are the product, the screen is the label:
                            # a drawing bug must not take the surface down
                            print(f"render failed ({VIEWS[view]}): {e}",
                                  file=sys.stderr, flush=True)
                            shown, disp, disp_mod = state, None, None
                    publish_surface({
                        "views": VIEWS, "view": view,
                        "modes": [VIEW_MODES.get(v, 1) for v in VIEWS],
                        "mode": mode,
                        # every view's own place, not just this one's: the
                        # mirror labels them all
                        "at": [view_mode.get(v, 0) for v in VIEWS],
                        "colours": [disp_mod.VIEW_RGB.get(v, (200, 200, 200))
                                    for v in VIEWS],
                        "seats": seats, "current": current,
                        # a question is answerable from wherever you are, so
                        # the app is told about one in every view
                        "opts": opts,
                        "page": page, "pages": len(PAGES),
                        # what the app needs to draw this view itself, and
                        # every seat in enough detail for its rail
                        "data": data, "cols": seat_cols,
                        # the two label bands, so the mirror can crop them:
                        # the page's own buttons already say it
                        "bands": [disp_mod.STRIP_H, disp_mod.SEAT_H],
                        "size": [disp_mod.WIDTH, disp_mod.HEIGHT],
                        "stamp": _frame_stamp})
                    try:
                        if disp:        # every poll: it blanks after about 2s
                            disp.show(frame)
                    except Exception:
                        disp = None     # unplugged; the mirror carries on

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
                for cc in PAGE_CCS:     # lit for the directions that go somewhere
                    step = page + PAGE_CCS[cc]
                    push.cc(f"page{cc}", 0, [cc],
                            WHITE if 0 <= step < len(PAGES) + bool(any(PAGES[-1]))
                            else BLACK)
                for cc in ARROW_CCS:
                    push.cc(f"arrow{cc}", 0, [cc], WHITE if target else BLACK)
                for cc in SCROLL_CCS:      # lit only when there is a pane to page
                    push.cc(f"pg{cc}", 0, [cc], WHITE if cur else BLACK)
                # a mapped button that never lights reads as a dead one
                push.cc("shift", 0, [SHIFT_CC], BRIGHT if shifted else DIM)
                for i, cc in enumerate(ARM_CCS):
                    push.cc(f"arm{i}", 0, [cc], RED if arming else DIM)
                push.cc("select", 0, [SELECT_CC],
                        GREEN if moving is not None else DIM)
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
                # The grid shows shortcuts where shortcuts are the subject.
                # On the usage view it is 64 lit pads about something else, and
                # a surface that is always on says nothing. The modes keep
                # their pads regardless: an armed chain, a pad in hand, Record
                # held, and a question waiting are all live state, and a
                # question in particular answers from wherever you are.
                lit_macros = VIEWS[view] == "focus"
                testing = VIEWS[view] == "tests"
                # the sessions view puts the subagents themselves on the pads. No
                # answer-pad exception: a question drags you to the focus view
                # before you could press one here.
                picking = VIEWS[view] == "sessions"
                chain_at = {p: k for k, p in enumerate(chain.steps)} if chain else {}
                for i in range(MACRO_SLOTS):        # the grid changes job when asked
                    anim = STATIC
                    if testing:
                        colour, anim = (TEST_LEDS[titems[i]["state"]]
                                        if i < len(titems) else (BLACK, STATIC))
                    elif i in chain_at:             # the chain owns its own pads
                        k = chain_at[i]
                        colour, anim = (
                            (GREEN, STATIC) if chain.phase == "done" else
                            (BLACK, STATIC) if k < chain.index else      # spent
                            ((YELLOW, PULSE) if chain.phase == "running" else
                             (RED, BLINK) if chain.phase == "waiting" else
                             (BLUE, PULSE)) if k == chain.index else     # up next
                            (MACROS[i]["colour"] if MACROS[i] else BLACK, STATIC))
                    elif moving is not None:
                        # the one in hand blinks; the rest just show what they
                        # hold, because that is what you are choosing between
                        if i == moving:
                            colour, anim = GREEN, BLINK
                        else:
                            colour = MACROS[i]["colour"] if MACROS[i] else BLACK
                    elif picking and not arming and not opts:
                        sb = subs[i] if i < len(subs) else None
                        colour, anim = (
                            (BLACK, STATIC) if sb is None else
                            (WHITE, STATIC) if subfocus and subfocus[1] == sb["id"]
                            else (YELLOW, PULSE) if sb["running"]
                            else (GREEN, STATIC))
                    elif opts and not arming:
                        # a question takes the whole grid. The macros go dark
                        # rather than sitting there looking pressable next to
                        # an answer -- there is one thing to do here now.
                        k = answer_pad(i, len(opts))
                        colour = (BLACK if k is None
                                  else ANSWER_CC[k % len(ANSWER_CC)])
                    else:
                        colour = (RED if arming else
                                  (MACROS[i]["colour"]
                                   if MACROS[i] and lit_macros else BLACK))
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
                        peek = slot
                        show("focus", 0)
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
                    if confirm and msg.value:
                        if slot == YES_SLOT:
                            say_yes()
                        elif slot == NO_SLOT:
                            print(f"{confirm[0]} cancelled", flush=True)
                            confirm, shown = None, None
                    elif confirm:
                        pass                        # ignore the release
                    elif msg.value:
                        if slots.get(slot):
                            pad_down = (slot, now)
                    elif slot == talk_slot and talk_src == "session":
                        print(f"pad {slot} released, {sent} keys sent", flush=True)
                        talk_slot, talk_src, pad_down = None, None, None
                    elif pad_down and pad_down[0] == slot:
                        if shifted:
                            confirm = ("close", now + CONFIRM_S, slot,
                                       agent_name(by_id.get(slots.get(slot))))
                            shown = None
                            print(f"shift+pad {slot} -> confirm close?", flush=True)
                            pad_down = None
                            continue
                        tap_seat(slot)                  # short press = focus
                        pad_down = None
                elif (msg.type == "control_change" and msg.control in TAB_CCS
                      and msg.value):
                    i = TAB_CCS.index(msg.control)
                    tap_tab(TAB_CCS.index(msg.control))
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
                    if VIEWS[view] == "tests" and test_run and not test_run.done:
                        test_run.abort()
                        shown = None
                        print("tests: aborted", flush=True)
                    elif chain:
                        # mid-step, stop has to mean the agent too, or the chain
                        # dies and the thing it started keeps running
                        print(f"chain {chain.phase} -> discarded", flush=True)
                        if chain.phase in ("running", "waiting"):
                            herdr("agent", "send", chain.target, ESCAPE)
                        chain, shown = None, None
                    elif confirm:
                        print(f"{confirm[0]} cancelled", flush=True)
                        confirm, shown = None, None
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
                    confirm = ("close", now + CONFIRM_S, current, agent_name(cur))
                    shown = None
                    print(f"delete -> confirm close slot {current}?", flush=True)
                elif (msg.type == "control_change" and msg.control == SELECT_CC
                      and msg.value):
                    if moving is None:
                        moving = -1
                        show("focus", 1)                  # show the grid
                        print("move on -> tap a pad to pick it up", flush=True)
                    else:
                        moving = None
                        print("move off", flush=True)
                    shown = None
                elif msg.type == "control_change" and msg.control in ARM_CCS:
                    arm_held[msg.control] = bool(msg.value)
                    was, arming = arming, any(arm_held.values())
                    shown = None
                    if arming and not was:
                        show("focus", 1)      # show what you would overwrite
                elif (msg.type == "control_change" and msg.control == BACK_CC
                      and msg.value and VIEWS[view] == "tests" and test_path):
                    test_path, shown = test_path[:-1], None
                    print(f"tests -> {'/'.join(test_path) or '(top)'}", flush=True)
                elif (msg.type == "control_change" and msg.control in PICK_CCS
                      and msg.value and chain and chain.phase == "armed"
                      and chain.cycle(MACROS, PICK_CCS[msg.control])):
                    here, total = chain.seq(MACROS)
                    shown = None
                    print(f"chain: sequence {here + 1}/{total}, "
                          f"{len(chain.steps)} steps", flush=True)
                elif (msg.type == "control_change" and msg.control in PICK_CCS
                      and msg.value and VIEW_MODES.get(VIEWS[view], 1) > 1):
                    name, modes = VIEWS[view], VIEW_MODES[VIEWS[view]]
                    at = (view_mode.get(name, 0) + PICK_CCS[msg.control]) % modes
                    view_mode[name], shown = at, None
                    print(f"{name} -> mode {at + 1}/{modes}", flush=True)
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
                elif (msg.type == "control_change" and msg.control in SCROLL_CCS
                      and msg.value):
                    seat = current if peek is None else peek
                    scrolls[seat] = page_scroll(scrolls.get(seat, 0),
                                                SCROLL_CCS[msg.control])
                    shown = None
                    print(f"page -> slot {seat} scroll {scrolls[seat]}", flush=True)
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
                    if set_page(PAGE_CCS[msg.control]):
                        shown = None
                        print(f"page {page + 1}/{len(PAGES)}", flush=True)
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
                        show("focus", 1)               # the rows to choose from
                        print("automate on -> tap a pad to arm its row",
                              flush=True)
                    else:
                        print("automate off", flush=True)
                elif (msg.type == "control_change" and msg.control == PLAY_CC
                      and msg.value):
                    if VIEWS[view] == "tests":
                        if test_run and not test_run.done:
                            print("tests: already running", flush=True)
                        else:
                            pk = scope_packages(tpkgs, test_path)
                            if pk:
                                test_run, shown = TestRun(troot, pk).start(), None
                                print("tests: running " +
                                      ", ".join(q["name"] for q in pk), flush=True)
                            else:
                                print("tests: nothing runnable here", flush=True)
                    elif chain and chain.phase == "armed":
                        shown = None
                        if chain.fire(MACROS, now):
                            chain.phase = "running"
                        else:                   # the whole row went empty
                            chain.phase, chain.done_at = "done", now
                    elif confirm:                   # Play is yes to the question
                        say_yes()
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
                    # the live view, not the flag the last poll painted with:
                    # a tab press between polls would otherwise fire a macro
                    # into an agent from a pad that is lit as something else
                    if VIEWS[view] == "tests":
                        if i < len(titems):
                            it = titems[i]
                            if it["dir"]:
                                test_path, shown = test_path + [it["name"]], None
                                print(f"tests -> {'/'.join(test_path)}", flush=True)
                            elif not (test_run and not test_run.done):
                                # a leaf runs itself: both runners take a path,
                                # and the package it sits in picks the runner
                                leaf = "/".join(test_path + [it["name"]])
                                pk = scope_packages(tpkgs, test_path) or tpkgs
                                if pk:
                                    test_run = TestRun(troot, pk[:1], only=leaf).start()
                                    shown = None
                                    print(f"tests: re-running {leaf}", flush=True)
                    elif moving is not None:
                        if moving < 0:
                            if MACROS[i]:
                                moving = i
                                print(f"move: picked up pad {i} "
                                      f"({MACROS[i]['label']!r})", flush=True)
                            else:
                                print(f"move: pad {i} is empty", flush=True)
                        elif i == moving:
                            moving = -1          # tapped again: put it back down
                            print("move: put back down", flush=True)
                        else:
                            swap_pads(MACROS, moving, i)
                            save_macros(MACROS)
                            print(f"move: pad {moving} <-> pad {i}", flush=True)
                            # stay in move mode: rearranging is rarely one pad
                            moving = -1
                        shown = None
                    elif automating:
                        steps = chain_steps(MACROS, i)
                        if steps and target:
                            chain = Chain(steps, target, current, agent_name(cur))
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
                    elif opts and answer_pad(i, len(opts)) is not None and target:
                        # exactly the pads painted white above: what lights is
                        # what answers. Ahead of the macro branch so a pad that
                        # is currently an answer cannot fire its old text into
                        # a question instead.
                        k = answer_pad(i, len(opts))
                        pick = summary.get("sel") or 0
                        print(f"answer {k + 1}/{len(opts)}: {opts[k][1]!r} "
                              f"-> {target}", flush=True)
                        herdr("agent", "send", target, answer_keys(k, pick))
                    elif VIEWS[view] == "sessions":
                        # last, under every mode: the pads only stand for the
                        # subagents when nothing louder has borrowed them
                        if i < len(subs):
                            confirm = ("focus", now + CONFIRM_S, i,
                                       subs[i]["label"])
                            shown = None
                            print(f"sessions: pad {i} -> focus "
                                  f"{subs[i]['label']!r}?", flush=True)
                    elif MACROS[i] and target:
                        m = MACROS[i]
                        # the text goes in now so it reads back immediately, but
                        # its newline waits for the release: a hold means you are
                        # about to dictate the rest, and submitting first would
                        # send half a thought
                        print(f"macro {m['label']!r} -> {target}", flush=True)
                        herdr("agent", "send", target, m["text"])
                        inserted = m["text"]
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

    # tests: the tree the grid navigates, and reading a runner's output
    assert test_tree(["a/b.test.ts", "a/c.test.ts", "d.test.ts"]) == \
        {"a": {"b.test.ts": None, "c.test.ts": None}, "d.test.ts": None}
    tt = test_tree(["a/b/c.test.ts", "a/d.test.ts", "e.test.ts"])
    assert sorted(tree_at(tt, [])) == ["a", "e.test.ts"], "the top is a level"
    assert sorted(tree_at(tt, ["a"])) == ["b", "d.test.ts"], "and so is a branch"
    assert tree_at(tt, ["a", "b", "c.test.ts"]) is None, "a file is a leaf"
    assert tree_at(tt, ["nope"]) is None, "a path that has gone away"
    assert sorted(leaves_under(tt["a"], "a")) == ["a/b/c.test.ts", "a/d.test.ts"]
    assert leaves_under(None, "x") == ["x"], "a file is its own leaf"

    # failure wins, so red can be followed down without knowing where it lives
    assert roll_up({"pass", "fail"}) == "fail"
    assert roll_up({"pass", "run"}) == "run", "running outranks settled passes"
    assert roll_up({"fail", "run"}) == "fail", "but a failure outranks running"
    assert roll_up({"pass"}) == "pass"
    assert roll_up(set()) == "", "nothing run yet is not a pass"

    assert parse_result("PASS src/a.test.ts") == ("pass", "src/a.test.ts")
    assert parse_result("  FAIL  x/b.spec.js") == ("fail", "x/b.spec.js")
    # jest paints the word, so the escape arrives before it
    assert parse_result("\x1b[32mPASS\x1b[0m src/c.test.ts") == ("pass", "src/c.test.ts")
    assert parse_result("Tests: 3 passed") is None, "a summary is not a file"
    # jest ticks each assertion too; those are not files and must not take pads
    assert parse_result("  ✓ concatenating") is None, "a test name is not a file"
    assert parse_result("PASS concatenating") is None, "nor is it, dressed as one"
    assert parse_result("") is None and parse_result("random") is None

    r = TestRun("/r", [{"name": "pkg", "cwd": "/r/pkg", "cmd": "jest", "slow": False}])
    assert r.key(r.packages[0], "src/a.test.ts") == "pkg/src/a.test.ts", \
        "a runner prints paths from its own package, the tree counts from the repo"
    assert r.key(r.packages[0], "./src/a.test.ts") == "pkg/src/a.test.ts"
    assert r.key(r.packages[0], "pkg/src/a.test.ts") == "pkg/src/a.test.ts", \
        "already qualified, do not double it"
    assert r.key({"name": ".", "cwd": "/r"}, "a.test.ts") == "a.test.ts"
    r.states = {"a": "pass", "b": "fail", "c": "pass"}
    assert r.tally() == (2, 1)

    # tokens by model: the same numbers /usage counts, from the same place
    assert model_usage_for(None) == {}, "no agent, no tokens"
    assert model_totals([]) == {}, "no agents, nothing to total"
    _model_tok["/fake"] = (0, {"opus 5": {"out": 5, "inp": 1, "cread": 2, "cwrite": 3},
                               "<synthetic>": {k: 0 for k in _USAGE_FIELDS}})
    # model_totals reads through model_usage_for, so exercise the filter direct
    merged = {}
    for name, tot in _model_tok["/fake"][1].items():
        seat = merged.setdefault(name, {k: 0 for k in _USAGE_FIELDS})
        for k, v in tot.items():
            seat[k] += v
    kept = {k: v for k, v in merged.items() if any(v.values())}
    assert kept == {"opus 5": {"out": 5, "inp": 1, "cread": 2, "cwrite": 3}}, kept
    assert "<synthetic>" not in kept, "not a model, and it spends nothing"
    del _model_tok["/fake"]
    assert len(VIEWS) <= len(TAB_CCS), "a button per view"
    assert set(VIEW_MODES) <= set(VIEWS), "a mode count for a view that is gone"
    import display as _disp
    assert set(VIEW_CC) == set(VIEWS) == set(_disp.VIEW_RGB), \
        "every view names a button colour and a label colour"
    assert len(set(VIEW_CC.values())) == len(VIEW_CC), "two views, one hue"

    # subagents live on disk: their log is what they said, and the parent's
    # transcript is the only thing that says whether one has come back
    import tempfile
    row = lambda **kw: json.dumps(kw) + "\n"
    with tempfile.TemporaryDirectory() as tmp:
        parent = os.path.join(tmp, "session.jsonl")
        with open(parent, "w") as f:
            f.write(row(message={"content": [{"type": "tool_use", "id": "t1",
                                              "name": "Task"}]}))
            f.write(row(message={"content": "a plain string, not blocks"}))
            f.write(row(message={"content": [{"type": "tool_result",
                                              "tool_use_id": "t1"}]}))
        assert finished_calls(parent) == {"t1"}, "the one that came back"
        assert finished_calls(parent + ".nope") == set(), "no file, no crash"
        log = os.path.join(tmp, "agent-a1.jsonl")
        with open(log, "w") as f:
            f.write(row(message={"content": [{"type": "text", "text": "found it"},
                                             {"type": "tool_use", "name": "Read",
                                              "input": {"file_path": "/a/b.py"}}]}))
            f.write(row(message={"content": [{"type": "text",
                                              "text": "TLDR\n- done"}]}))
        lines = sub_lines(log)
        assert lines == ["found it", "  Read(/a/b.py)", "TLDR", "- done"], lines
        info = sub_info({"label": "find it", "type": "Explore", "model": "sonnet",
                         "running": True, "log": log}, 2)
        assert info["status"] == "working" and info["effort"] == "Explore", info
        assert info["tldr"] == ["- done"], info["tldr"]
        assert info["say"] == "- done", info["say"]
        # every key render_focus reads, or it draws a KeyError instead of a pane
        assert set(info) >= set(focus_info(0, {"cwd": "/x"}, {})), info
    assert tool_line({"name": "Bash", "input": {}}) == "Bash", "no argument, no ()"

    # PR checks: the worst run decides the colour, and gh reports two shapes
    # the Push follows the pane herdr has focused, unless you pinned it
    seats = {0: "x", 2: "y"}
    assert follow_focus("y", seats, 0) == 2, "clicking a pane moves the surface"
    assert follow_focus("y", seats, 0, pinned=True) == 0, "solo means stay put"
    assert follow_focus(None, seats, 1) == 1, "nothing focused, nothing to do"
    assert follow_focus("gone", seats, 1) == 1, "focused pane holds no slot"
    assert follow_focus("x", seats, 0) == 0, "already there"

    # an error on the stream we do not read is indistinguishable from success,
    # and start_agent answers "is this name free?" with exactly that
    ok, taken = '{"result":{}}', '{"error":{"code":"agent_name_taken"}}'
    assert backend_said(ok, "") == ok, "0.6.8: the answer is on stdout"
    assert backend_said(taken, "") == taken, "0.6.8: so is the error"
    assert "agent_name_taken" in backend_said("", taken), "0.8.2 moved it to stderr"
    assert backend_said(ok, "warning: something") == ok, \
        "a bare warning is not an answer and must not replace one"
    assert backend_said("", "") == "", "nothing said, nothing returned"

    assert check_state([]) == "none"
    assert check_state([{"conclusion": "SUCCESS", "status": "COMPLETED"},
                        {"state": "SUCCESS"}]) == "pass", "a StatusContext too"
    assert check_state([{"conclusion": "SUCCESS", "status": "COMPLETED"},
                        {"conclusion": "", "status": "QUEUED"}]) == "pending"
    assert check_state([{"conclusion": "", "status": "QUEUED"},
                        {"conclusion": "FAILURE", "status": "COMPLETED"}]) == "fail", \
        "red outranks a run still going"
    assert check_state([{"conclusion": "SKIPPED", "status": "COMPLETED"}]) == "pass"
    pr = lambda n, **kw: {"number": n, "title": "  t\n t ",
                          "author": {"login": "me"}, **kw}
    rows = pr_rows([pr(1), pr(2, statusCheckRollup=[{"conclusion": "FAILURE"}]),
                    pr(3, headRefName="here", isDraft=True,
                       reviewDecision="APPROVED")], "here")
    assert [r["n"] for r in rows] == [3, 2, 1], "mine first, then what is on fire"
    assert rows[0]["draft"] and rows[0]["review"] == "approved"
    assert rows[2]["title"] == "t t", "a wrapped title is one line here"
    assert pr_rows({"error": "not a list"}) == [], "gh failing is not a crash"
    assert pr_rows([pr(1, reviewDecision="REVIEW_REQUIRED")])[0]["review"] == "", \
        "nobody has looked yet is the common case, not news"
    # every question the surface can ask has a screen that asks it
    import display as _d
    for kind in ("close", "focus"):
        assert kind in _d.CONFIRMS, kind
        assert _d.render_confirm("repo", 0, 5, kind).size == (_d.WIDTH, _d.HEIGHT)

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
    assert set(col) == {"name", "status", "model", "effort", "sub", "tid",
                        "focused", "context"}, col
    assert col["tid"] == "t", "the app acts on a seat and must know which agent"
    assert "typed" not in col, "the prompt is the focus view's job, not a column's"
    assert short_model("claude-opus-5") == "opus 5"
    assert short_model("claude-haiku-4-5-20251001") == "haiku 4.5"
    assert short_model("claude-sonnet-5") == "sonnet 5"

    # a row is the chain: from a pad to the end of ITS row, never into the next
    pads = [{"text": "t", "label": "t", "colour": BLUE, "tag": None,
             "submit": False} for _ in range(MACRO_SLOTS)]
    assert chain_steps(pads, 0) == list(range(0, 8)), "a full row is one sequence"
    assert chain_steps(pads, 5) == list(range(0, 8)), "tapping inside picks the block"
    assert chain_steps(pads, 8) == list(range(8, 16)), "row 1 is its own"
    assert chain_steps(pads, 63) == [56 + k for k in range(8)], "last row, whole"
    assert chain_steps([None] * MACRO_SLOTS, 0) == [], "an empty row is no chain"

    # a blank pad splits the row: two sequences, not one with a hole in it
    split = list(pads)
    split[3] = None
    assert row_blocks(split, 0) == [[0, 1, 2], [4, 5, 6, 7]], "the gap is a break"
    assert chain_steps(split, 0) == [0, 1, 2], "left of the gap"
    assert chain_steps(split, 5) == [4, 5, 6, 7], "right of it is a different one"
    assert chain_steps(split, 3) == [], "the gap itself runs nothing"
    edges = list(pads)
    edges[0] = edges[7] = None
    assert row_blocks(edges, 0) == [[1, 2, 3, 4, 5, 6]], "gaps at the ends do not split"
    lone = [None] * MACRO_SLOTS
    lone[2] = lone[5] = pads[0]
    assert row_blocks(lone, 0) == [[2], [5]], "two sequences of one"

    # left/right walk them, and wrap
    ch = Chain(chain_steps(split, 0), "t", 0, "n")
    assert ch.seq(split) == (0, 2), "first of two"
    assert ch.cycle(split, 1) and ch.steps == [4, 5, 6, 7], "right moves along"
    assert ch.seq(split) == (1, 2)
    assert ch.cycle(split, 1) and ch.steps == [0, 1, 2], "and wraps"
    assert ch.cycle(split, -1) and ch.steps == [4, 5, 6, 7], "left goes back"
    assert not Chain(chain_steps(pads, 0), "t", 0, "n").cycle(pads, 1), \
        "one sequence has nowhere to go"
    ch.index = 2
    ch.cycle(split, 1)
    assert ch.index == 0, "a different sequence starts at its own beginning"

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
    assert set(info) == {"agent", "slot", "steps", "index", "phase",
                         "seq", "seqs"}, info
    assert len(info["steps"]) == 2 and set(info["steps"][0]) == {"label", "colour"}
    assert AUTOMATE_CC not in TAB_CCS + SESSION_CCS + list(COMMAND_CCS)
    assert AUTOMATE_CC not in (PLAY_CC, STOP_CC, SHIFT_CC) + ARM_CCS
    assert BROWSE_CC not in TAB_CCS + SESSION_CCS + list(COMMAND_CCS)
    assert BROWSE_CC not in (PLAY_CC, STOP_CC, SHIFT_CC, AUTOMATE_CC,
                             ADD_DEVICE_CC, ADD_TRACK_CC, DELETE_CC) + ARM_CCS

    # the strip is absolute: both ends must be reachable, and reachable at the
    # very edge, or the two levels people most want are the two they cannot hit
    # paging out of the summary must land on the newest line, not six above it
    assert page_scroll(0, 1) == 1, "the first page back arrives at the live tail"
    assert page_scroll(1, 1) == 1 + PAGE_LINES, "then it pages properly"
    assert page_scroll(0, -1) == 0, "already live, nowhere further down"
    assert page_scroll(3, -1) == 0, "never past the tail"
    assert page_scroll(20, -1) == 20 - PAGE_LINES, "and back down a page"
    assert page_scroll(1, -1) == 0, "one line back returns to the summary"
    assert set(SCROLL_CCS) & set(ARROW_CCS) == set(), "paging is not the arrows"
    assert set(SCROLL_CCS) & set(PAGE_CCS) == set(), "nor switching sessions"

    # the focus view's default: an explicit TLDR, else the last answer
    pane = ["⏺ an older answer", "  its second line",
            "⏺ the last answer", "  wrapped onto here",
            "✻ Churned for 6s"]
    assert tldr(pane) == ["the last answer", "wrapped onto here"], \
        "the whole answer, not just its first line, and only the last one"
    withtldr = pane + ["**TLDR**", "- Progress: did the thing",
                       "- Next: the other thing",
                       "✻ Churned for 6s · done",
                       "※ recap: not part of it",
                       "❯ half a typed prompt"]
    got = tldr(withtldr)
    assert got == ["- Progress: did the thing", "- Next: the other thing"], got
    assert not any("TLDR" in l for l in got), "the heading is a row we cannot spare"
    assert not any("recap" in l or "typed prompt" in l for l in got), \
        "the pane's own furniture ends the section"
    assert tldr(["⏺ only me"]) == ["only me"], "an answer with no body"
    assert tldr([]) == [] and tldr(["nothing here"]) == [], "no answer, no summary"
    # matched however it is spelled, and dropped either way
    assert tldr(["tl;dr", "- Progress: x"]) == ["- Progress: x"]
    assert tldr(["## TL;DR", "- Next: y"]) == ["- Next: y"]

    # moving pads: one operation, because an empty destination is just None
    a = [{"label": "a", "text": "a", "colour": BLUE, "tag": None, "submit": False},
         None,
         {"label": "c", "text": "c", "colour": RED, "tag": None, "submit": False}]
    swap_pads(a, 0, 2)
    assert [x and x["label"] for x in a] == ["c", None, "a"], "two full pads swap"
    swap_pads(a, 0, 1)
    assert [x and x["label"] for x in a] == [None, "c", "a"], "an empty one is a move"
    swap_pads(a, 1, 1)
    assert [x and x["label"] for x in a] == [None, "c", "a"], "onto itself is a no-op"
    assert SELECT_CC not in ARM_CCS, "Select rearranges now, it does not arm"
    assert SELECT_CC not in (PLAY_CC, STOP_CC, SHIFT_CC, AUTOMATE_CC, BROWSE_CC)

    # answering: walk the caret to the pad you pressed, then commit
    # a question takes the grid: a row per option, the first one at the top
    assert answer_pad(56, 3) == 0, "the top of the left column is the first"
    assert answer_pad(48, 3) == 1, "the pad under it is the second"
    assert answer_pad(40, 3) == 2, "and the one under that the third"
    assert answer_pad(57, 3) is None, "the rest of that row answers nothing"
    assert answer_pad(63, 3) is None, "including the end of it"
    assert answer_pad(32, 3) is None, "nor does the column past the last option"
    assert answer_pad(0, 8) == 7, "eight options reach the foot of the column"
    assert [answer_pad(i, 8) for i in range(MACRO_SLOTS)].count(None) == 56, \
        "eight options light eight pads, not eight rows"
    assert len(ANSWER_CC) == len(_d.ANSWER_RGB), "an answer hue with no screen twin"

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
    # a numbered list in prose is not a question, however well it matches
    prose = ["Two things you should know:",
             "1. The Push is off USB right now, so the hardware is unverified",
             "2. A pre-existing screen bug this made visible"]
    assert read_pane(prose)["opts"] == [], "prose numbering is not a widget"
    assert read_pane(prose)["sel"] is None, "and there is no caret to walk"
    widget = ["Commit this?", "❯ 1. Yes", "  2. Hold"]
    seen = read_pane(widget)
    assert [o[1] for o in seen["opts"]] == ["Yes", "Hold"], "a real one still reads"
    assert seen["sel"] == 0, "and the caret says where the walk starts"
    assert read_pane(["⏺ done", "1. a note"])["opts"] == [], "nor after an answer"
    # a pane holds more than one numbered list; only the one with the caret is
    # a question, and the caret's index is an index into that list alone
    both = ["The dependency is one function.", "1. Every herdr call funnels",
            "2. Only seven distinct calls", "3. Two of the four fields",
            "4. What is left is a terminal-host problem", "Run it?",
            "  1. Run the probe", "❯ 2. Build the shim now", "  3. Stop here"]
    seen = read_pane(both)
    assert [o[1] for o in seen["opts"]] == ["Run the probe", "Build the shim now",
                                            "Stop here"], "the widget, not the prose"
    assert seen["sel"] == 1, "and the caret indexes that list, not the pane"
    assert answer_keys(2, seen["sel"]) == DOWN + ENTER, "one step, not five"
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
    assert all(len(pg) == MACRO_SLOTS for pg in load_macros()[1])  # one per pad
    assert len(MACRO_NOTES) == len(set(MACRO_NOTES)) == MACRO_ROWS * SLOTS
    assert min(MACRO_NOTES) == 36 and max(MACRO_NOTES) == 99

    # pages are a line you can extend, not a ring you can spin
    _keep = [list(pg) for pg in PAGES]
    PAGES[:] = [[None] * MACRO_SLOTS]
    while page:
        set_page(-1)
    assert not set_page(-1), "left of the first page is not page zero"
    assert not set_page(1), "an empty page has not earned another"
    PAGES[0][0] = {"label": "x", "text": "x", "colour": BLUE,
                   "tag": None, "submit": False}
    assert set_page(1) and len(PAGES) == 2, "a used page earns the next one"
    assert MACROS is PAGES[1], "the live page is the store's page, not a copy"
    assert set_page(-1) and MACROS is PAGES[0], "and back again"
    assert not set_page(2), "pages are stepped, never jumped over"
    PAGES[:] = _keep

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
