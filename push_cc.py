#!/usr/bin/env python3
"""Ableton Push 2 as an AI command center, driven by herdr.

Top pad row (notes 92-99) = up to 8 herdr agents, left to right.
  tap    -> focus that agent's pane
  colour -> green done / yellow working / red blocked / white unknown / off empty

Row 2 (84-91) approve / row 3 (76-83) deny, per column. Lit only while that
agent is blocked, and ignored otherwise, so a stray press cannot answer a
prompt that is not there.

Bottom row (36-43) = MACROS, inserted into whichever agent herdr reports
focused. They do not submit -- you read it, then hit Play.

Play (CC 85) = enter, sent to the focused agent. Lit green when there is one.

The 960x160 screen names each column, so two checkouts of the same repo are
told apart by the tail of their terminal id. Missing pyusb just means no
screen; the pads carry on.

Buttons above the display (CC 102-109) pick the view: 1 agents, 2 usage,
3 focus (one agent, what it is doing, and any question it is asking). The
tempo encoder scrolls that view back through the agent's output; 0 is always
the live tail, and changing agent snaps back to it.

Arrows: left/right step between live agents. Up/down move the highlighted
option when one is being offered, and scroll the focus view when none is.

When the current agent is asking something, the bottom row turns white and
answers it instead of inserting macros.

Add Device splits and starts a new claude there in auto mode, in the current
agent's directory. Add Track makes a worktree off it, named from whatever is
typed in the prompt, or timestamped if that is empty.

Mute clears whatever is typed in the prompt and lights only when there is
something to clear. Convert runs /compact, New runs /clear, Quantize opens
/model -- which is a
select widget, so the arrows and Play drive it. Delete closes the current
agent's pane outright. It submits on press, unlike the macro
row, because a button labelled Delete doing nothing until you press another
one is worse than the thing it guards against.

Hold Record and tap a macro pad to save whatever is sitting in the current
agent's prompt onto it -- type it or dictate it, then capture. Tapping with an
empty prompt clears the pad. Stored in macros.json, which is hand-editable.
White is the one you are on. Usage counts tokens, not money -- nothing here
knows your plan, and an invented cost is worse than no cost.

  python3 push_cc.py             run it (Push must be in User mode)
  python3 push_cc.py --list      dump agents, no hardware needed
  python3 push_cc.py --selftest  pure-logic asserts, no hardware needed
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

PAD_NOTES = list(range(92, 100))     # top pad row, left -> right = agents
APPROVE_NOTES = list(range(84, 92))  # row below: answer that agent's prompt yes
DENY_NOTES = list(range(76, 84))     # row below that: answer no
MACRO_NOTES = list(range(36, 44))    # bottom row: canned prompts
TAB_CCS = list(range(102, 110))    # buttons above the display -> view switcher
VIEWS = ["agents", "usage", "focus", "macros"]
MARK_CCS = list(range(20, 28))     # buttons directly above the pads -> focus marker
PLAY_CC = 85                       # transport Play -> enter, submits what is typed
TEMPO_CC = 14                      # tempo encoder -> scroll the focus view
ARROW_CCS = {44: "left", 45: "right", 46: "up", 47: "down"}
RECORD_CC = 86                     # hold Record, tap a pad: saves the prompt to it
SCROLL_STEP = 3                    # lines per arrow press; the encoder does fine work
DELETE_CC = 118                    # Delete -> close the current agent's pane
ADD_DEVICE_CC = 52                 # Add Device -> split, new claude in auto mode
ADD_TRACK_CC = 53                  # Add Track -> new worktree

MUTE_CC = 60                       # Mute -> ctrl+l, chat:clearInput

# Buttons that send a fixed string to the current agent. The newline is part of
# the entry: not everything here submits.
COMMAND_CCS = {
    35: ("convert", "/compact\r"),
    87: ("new", "/clear\r"),
    116: ("quantize", "/model\r"),
    MUTE_CC: ("mute", "\x0c"),     # clears the prompt, submits nothing
}
MACRO_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "macros.json")
SCRAPE_LINES = "400"               # how far back the focus view can scroll

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
    """macros.json if present, defaults otherwise. Hand-editable on purpose."""
    try:
        with open(MACRO_FILE) as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError):
        raw = DEFAULT_MACROS
    out = []
    for entry in (raw or [])[:SLOTS]:
        if not entry:
            out.append(None)
        elif isinstance(entry, str):
            out.append((label_for(entry), entry))
        else:
            text = entry.get("text", "")
            out.append((entry.get("label") or label_for(text), text) if text else None)
    return out + [None] * (SLOTS - len(out))


def save_macros(macros):
    tmp = MACRO_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump([None if not m else {"label": m[0], "text": m[1]} for m in macros],
                  f, indent=2)
    os.replace(tmp, MACRO_FILE)   # never leave a half-written file behind


MACROS = load_macros()


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


def blocked_agent(slot, slots, by_id):
    """The agent on this slot, but only if it is actually waiting on an answer."""
    a = by_id.get(slots.get(slot))
    return a if a and a.get("agent_status") == "blocked" else None


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
UP, DOWN = "\x1b[A", "\x1b[B"


def turn(value):
    """Push encoders are relative: 1..63 clockwise, 127..65 anticlockwise."""
    return value if value < 64 else value - 128


def pane_summary(agent):
    """What this agent is doing, scraped from its rendered pane.

    herdr exposes status but not content, and the pane is the only place the
    question text and its options actually exist."""
    if not agent:
        return {}
    try:
        raw = json.loads(herdr("agent", "read", agent["terminal_id"],
                               "--lines", SCRAPE_LINES))
        lines = raw["result"]["read"]["text"].splitlines()
    except (json.JSONDecodeError, KeyError, OSError, subprocess.SubprocessError):
        return {}
    body = [l for l in lines if not set(l.strip()) <= set("─━ ")]   # drop rules
    opts, say, act, pending, sel = [], "", "", "", None
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
        elif line.lstrip().startswith("❯"):
            pending = line.lstrip()[1:].strip()
    return {"say": say, "act": act, "opts": opts, "sel": sel,
            "lines": body, "pending": "" if opts else pending}


def focus_info(slot, agent, summary, scroll=0):
    return {"slot": slot,
            "name": os.path.basename(agent.get("cwd", "")) or "?",
            "model": model_for(agent),
            "status": agent.get("agent_status"),
            **{k: summary.get(k, "") for k in ("act", "say", "pending")},
            "opts": summary.get("opts") or [], "sel": summary.get("sel"),
            "lines": summary.get("lines") or [], "scroll": scroll}


def panel_col(agent):
    """One screen column. Two agents can share a repo name, so show a tail of
    the terminal id -- that is the thing that actually tells them apart."""
    if agent is None:
        return None
    return (os.path.basename(agent.get("cwd", "")) or "?",
            agent.get("agent_status"),
            model_for(agent),
            agent.get("terminal_id", "")[-6:],
            bool(agent.get("focused")))


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
        self.cc("play", 0, [PLAY_CC], BLACK)


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
    current, summary, scroll, arming = 0, {}, 0, False
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
                    push.cc("tab", s, TAB_CCS,
                            WHITE if s == view else (BLUE if s < len(VIEWS) else BLACK))
                    push.cc("mark", s, MARK_CCS,
                            WHITE if a and a.get("focused") else BLACK)
                    # approve/deny light only when there is something to answer
                    blocked = bool(a) and a.get("agent_status") == "blocked"
                    push.note("ok", s, APPROVE_NOTES[s], GREEN if blocked else BLACK)
                    push.note("no", s, DENY_NOTES[s], RED if blocked else BLACK)
                    push.note("macro", s, MACRO_NOTES[s],
                              BLUE if s < len(MACROS) else BLACK)
                focused = next((a["terminal_id"] for a in live if a.get("focused")), None)
                # Scraped every poll, not just in the focus view: the bottom row
                # answers a pending question from wherever you happen to be.
                cur = by_id.get(slots.get(current))
                summary = pane_summary(cur)
                opts = summary.get("opts") or []
                target = slots.get(current) or focused

                if disp:
                    if VIEWS[view] == "focus":
                        info = focus_info(current, cur, summary, scroll) if cur else None
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
                        cols = tuple(build(by_id.get(slots.get(s))) for s in range(SLOTS))
                        state = (view, cols)
                        drawn = lambda: draw(cols)
                    if state != shown:              # re-render on change only
                        shown, frame = state, drawn()
                    try:
                        disp.show(frame)            # every poll: it blanks after ~2s
                    except Exception:
                        disp = None

                push.cc("play", 0, [PLAY_CC], GREEN if target else BLACK)
                for cc, arrow in ARROW_CCS.items():
                    if arrow in ("left", "right"):
                        lit = bool(slots)
                    elif summary.get("sel") is not None:
                        lit = True              # answering
                    else:
                        lit = VIEWS[view] == "focus"    # scrolling
                    push.cc(f"arrow{cc}", 0, [cc], WHITE if lit else BLACK)
                push.cc("rec", 0, [RECORD_CC], RED if arming else BLACK)
                push.cc("del", 0, [DELETE_CC], RED if cur else BLACK)
                for cc, (name, _) in COMMAND_CCS.items():
                    # mute only lights when there is actually something to clear
                    on = bool(summary.get("pending")) if cc == MUTE_CC else bool(target)
                    push.cc(name, 0, [cc], WHITE if on else BLACK)
                push.cc("adddev", 0, [ADD_DEVICE_CC], GREEN)
                push.cc("addtrk", 0, [ADD_TRACK_CC], GREEN)
                for s in range(SLOTS):              # bottom row changes job when asked
                    push.note("macro", s, MACRO_NOTES[s],
                              RED if arming else
                              (WHITE if s < len(opts) else
                               (BLACK if opts else
                                (BLUE if MACROS[s] else BLACK))))

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
                        current, pad_down, shown, scroll = slot, None, None, 0
                elif msg.type == "note_on" and msg.velocity and msg.note in APPROVE_NOTES:
                    answer(APPROVE_NOTES.index(msg.note), YES, slots, by_id)
                elif msg.type == "note_on" and msg.velocity and msg.note in DENY_NOTES:
                    answer(DENY_NOTES.index(msg.note), NO, slots, by_id)
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
                elif (msg.type == "control_change" and msg.control in COMMAND_CCS
                      and msg.value and target):
                    name, cmd = COMMAND_CCS[msg.control]
                    print(f"{name} -> {cmd!r} -> {target}", flush=True)
                    herdr("agent", "send", target, cmd)
                elif (msg.type == "control_change" and msg.control == DELETE_CC
                      and msg.value and cur):
                    pane = cur.get("pane_id")
                    print(f"delete -> closing pane {pane} ({target})", flush=True)
                    if pane:
                        herdr("pane", "close", pane)
                        slots.pop(current, None)    # do not paint a dead slot
                        current, shown, scroll = step_slot(current, 1, slots), None, 0
                elif msg.type == "control_change" and msg.control == RECORD_CC:
                    arming, shown = bool(msg.value), None
                    if arming:
                        view = VIEWS.index("macros")   # show what you would overwrite
                elif (msg.type == "control_change" and msg.control in ARROW_CCS
                      and msg.value):
                    arrow = ARROW_CCS[msg.control]
                    if arrow in ("left", "right"):
                        current, shown, scroll = step_slot(
                            current, 1 if arrow == "right" else -1, slots), None, 0
                        print(f"-> slot {current} ({slots.get(current)})", flush=True)
                    elif summary.get("sel") is not None and target:
                        # A real select widget: up/down are confirm:previous and
                        # confirm:next there. In prose, up is history:previous and
                        # would recall an old prompt, so we never forward it.
                        herdr("agent", "send", target, UP if arrow == "up" else DOWN)
                    else:                       # nothing to answer -> scroll instead
                        scroll, shown = max(0, scroll + (
                            SCROLL_STEP if arrow == "up" else -SCROLL_STEP)), None
                elif msg.type == "control_change" and msg.control == TEMPO_CC:
                    # clockwise winds back through history, anticlockwise returns
                    # to the live tail at 0 -- same sense as the up arrow
                    scroll, shown = max(0, scroll + turn(msg.value)), None
                elif (msg.type == "control_change" and msg.control == PLAY_CC
                      and msg.value and target):
                    print(f"play -> enter -> {target}", flush=True)
                    herdr("agent", "send", target, ENTER)
                elif msg.type == "note_on" and msg.velocity and msg.note in MACRO_NOTES:
                    i = MACRO_NOTES.index(msg.note)
                    if arming:
                        text = (summary.get("pending") or "").strip()
                        MACROS[i] = (label_for(text), text) if text else None
                        save_macros(MACROS)
                        shown = None
                        print(f"pad {i} <- {text!r}" if text
                              else f"pad {i} cleared", flush=True)
                    elif opts and target:
                        if i < len(opts):           # answering, not typing
                            num, label = opts[i]
                            sel = summary.get("sel")
                            print(f"answer {num}. {label[:40]!r} -> {target}", flush=True)
                            if sel is None:
                                herdr("agent", "send", target, num)  # prose: type it
                            else:                   # widget: walk the caret, commit
                                step = DOWN if i > sel else UP
                                for _ in range(abs(i - sel)):
                                    herdr("agent", "send", target, step)
                                herdr("agent", "send", target, ENTER)
                    elif MACROS[i] and target:
                        label, text = MACROS[i]
                        print(f"macro {label!r} -> {target}", flush=True)
                        herdr("agent", "send", target, text)

            if pad_down and talk_slot is None and now - pad_down[1] >= HOLD_S:
                # No focus call: Claude reads its own pty, so a background agent
                # hears this while you keep watching another one.
                talk_slot, next_key, sent = pad_down[0], 0.0, 0
                current, shown, scroll = talk_slot, None, 0
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
    assert PLAY_CC not in TAB_CCS + MARK_CCS, "play must not collide with a lit row"

    assert panel_col(None) is None
    col = panel_col({"cwd": "/a/b/bugcast", "agent_status": "blocked",
                     "terminal_id": "term_659ce899ee9293", "focused": True})
    assert col == ("bugcast", "blocked", "", "ee9293", True), col
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
    assert len(load_macros()) == SLOTS          # always exactly one per pad

    assert slug("Fix The Parser") == "fix-the-parser"
    assert slug("feat/thing") == "feat/thing"
    assert slug("  spaces   everywhere  ") == "spaces-everywhere"
    assert slug("!!!") .startswith("push/")     # nothing usable -> timestamped
    assert slug("") .startswith("push/")
    assert len(slug("x" * 200)) == 60

    assert COMMAND_CCS[MUTE_CC][1] == "\x0c"          # ctrl+l, no newline
    assert not COMMAND_CCS[MUTE_CC][1].endswith("\r")  # must not submit
    assert all(v.endswith("\r") for k, (_, v) in COMMAND_CCS.items() if k != MUTE_CC)
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
