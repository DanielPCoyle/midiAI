# push-cc — Ableton Push 2 as an AI command center

Eight herdr agents on the top pad row, named on the Push's own screen, driven
without touching the keyboard.

## The surface

| Control | Notes / CC | Does |
|---|---|---|
| **Top pad row** | 92–99 | tap = focus that agent · **hold** = talk to it |
| **Row 2** | 84–91 | approve — sends `y`, lit only while that agent is blocked |
| **Row 3** | 76–83 | deny — sends `n`, same guard |
| **Bottom row** | 36–43 | macros — insert canned text, no submit |
| **Play** | CC 85 | enter — submits, to the focused agent · runs an armed chain |
| **Automate** | CC 89 | hold, tap a pad: arms that pad's row as a chain |
| **Touchstrip** | pitchbend | slide to set reasoning effort, `low` → `max` |
| **Buttons above screen** | CC 102–109 | pick a view — each with its own colour, named on the screen above it; press the one you are on to walk its modes |
| **Buttons below screen** | CC 20–27 | the sessions: tap = focus · hold = talk · named on the screen above them |
| **Solo** | CC 61 | pin: stop the surface following herdr's focus, and stop questions pulling it |

Focus goes both ways. Tapping a session button focuses that pane in herdr,
and clicking a pane on the computer moves the Push to it — one set of
sessions, two pairs of hands, never two different ideas of where you are.
Solo opts out.

Pad colour follows herdr's `agent_status`:

| Colour | Status | Meaning |
|---|---|---|
| green | `idle` | done |
| yellow, pulsing | `working` | in progress |
| red, blinking | `blocked` | needs you |
| white | `unknown` | present, state unclear |
| off | — | empty slot |

## The screen

960×160, and not MIDI — a bulk USB endpoint of its own (`0x2982:0x1967`,
interface 0, endpoint `0x01`).

**focus** is one session, full width: its TLDR at rest, the raw pane when you
scroll, the question and its options when it asks one. Its second mode is the
shortcuts grid — the same subject, the session you are driving, shown as what
you can say to it. Record, Select and Automate all put you there, because
that is the mode their pads live in.

**agents** names every column: repo, status, model, and a tail of the terminal
id. That last one earns its space — two checkouts of one repo show the same
name, and the id is the only thing that tells them apart.

In this view the pads are the *subagents* the current session has spawned —
read off `~/.claude/projects/<session>/subagents/`, labelled with the
description each was dispatched with. Yellow pulsing is still running, green
has come back. Tapping one asks before it acts, and yes points the **focus**
view at that subagent's own transcript instead of the session's pane. A
session button takes you back. The view's second mode names them all on the
glass, in pad order — the grid says which are still going, the list says what
each was asked to do.

Two permanent bands frame every view: the top names what each button above the
screen switches to, the bottom names the session each button below it drives.
Eight identical buttons you have to remember are not a surface.

**prs** lists the open pull requests for the selected session's checkout —
your branch first, then whatever is on fire. The swatch is the whole CI answer
(green passed, yellow still running, red failed, grey no checks), with the
author and review state on the right. `gh` runs on its own thread and the list
is cached for a minute; **Browse** opens the same list in a browser.

**usage** answers one question — what is being spent — at three altitudes,
walked with its own view button or Left/Right: the plan's own limit bars, then
tokens by model, then output and context tokens per agent. Tokens, not money:
an invented cost is worse than no cost.

## The browser mirror

`mapui.py` shows the Push's screen live, with the two button rows where they
physically sit — the view picker above the glass, the sessions below it — and
clicking one presses it. The mirror renders nothing of its own: push_cc saves
the frame it just sent to the Push and the page shows that file. Two drawings
of one screen drift apart the day someone edits only one of them.

A click goes back through the same file the poll loop already watches, so it
lands where a physical press lands — a click and a press cannot come to mean
two different things. It keeps working with the screen unplugged: the
renderers are pure PIL and only `disp.show()` needs the device.

## Run

    .venv/bin/python push_cc.py

    push_cc.py --list      # pad -> agent mapping, no hardware needed
    push_cc.py --selftest  # pure-logic asserts
    push_cc.py --debug     # log every control you press, with its number

**Quit Ableton Live first**, or turn off Track/Remote for Push 2 Port 2 in its
MIDI prefs. Live repaints the whole surface continuously, so while it runs
your LED writes land underneath its own and the device looks dead in one
direction only — input works, output does nothing. This costs an afternoon if
you do not know it.

You do **not** need to press the Push's User button. A Push that has just been
plugged in is in Live mode, where everything it sends goes to port 1 and
everything sent to port 2 is ignored — so the pads and buttons are deaf while
the screen keeps drawing, which reads as a device that came back half alive and
sits on one session with no button able to move it. Startup now asks for User
mode itself (`Set MIDI Mode`, sysex `0A 01`). Sysex is accepted on both ports in
every mode, so that request lands whichever port the device is currently
listening to, and Ableton never has to be opened to do it.

## Chains

A **row is a chain**. Hold **Automate** and tap a pad: everything from that pad
rightward to the end of its row is queued, in order, empties skipped. The pads
light up, the screen lists the steps, and nothing has happened yet — **Play**
runs it, **Stop** throws it away.

Two gestures, deliberately. A chain is fire-and-forget by definition, and the
macro pads already learned this lesson: you get to read what is about to run
before it runs.

Steps advance on the agent's own status, not a timer. The catch is that status
lags the send — an agent still reports `idle` for a poll or two after its enter
lands, so advancing on a bare `idle` fires the whole row into one prompt. A step
is done once we have seen it go `working` and then sit `idle` for two polls
running. A step that starts and finishes between polls is caught by a grace
period instead.

`blocked` **pauses** the chain rather than advancing it. The agent asked you
something; the approve/deny rows do their ordinary job and the chain picks up
where it left off. A pipeline that stops and asks is the point, not a case to
engineer around.

The chain pins to the agent it started on, so you can walk away and watch
another session while it runs — which is most of the reason there are eight.
Stop mid-step sends escape to that agent too, or the chain dies while the thing
it started keeps going.

## Effort on the touchstrip

The strip is the only **absolute** control on the surface — an encoder is
relative and can only nudge, a strip you can slam straight to `max`. It maps to
Claude Code's five reasoning levels, bottom to top: `low`, `medium`, `high`,
`xhigh`, `max`.

Nothing is sent while you slide. The screen shows the level under your finger
and lifting off is what commits it, as `/effort <level>` — which takes the level
inline, so there is no picker widget to drive blind.

What the screen reports is read back out of the session's own transcript, where
each message records its `effort`. Not the last value we wrote: the level can be
changed from the keyboard too, and a readout that only ever echoes our own
writes would be worse than none.

## How the voice works

It is Claude Code's own `voice:pushToTalk`, not ours.

A terminal never sends a key-up event, so Claude infers "still holding" from
key **auto-repeat** and calls it released after ~120ms of quiet. Holding a pad
past 250ms replays `space` into that pane every 60ms; letting go stops the
replay, and the gap *is* the release.

`herdr agent send` writes into a pane's pty without focusing it, so you can
talk to one agent while watching another. It submits on release — Claude owns
the whole record/transcribe/submit path and there is no seam to read it first.

## Notes

- Only agents in herdr panes appear. `claude agents --json` sees every session
  but offers no focus or send, so herdr is the substrate.
- Slots pin per terminal id: an agent exiting does not shuffle the others, so
  muscle memory survives. A 9th agent is invisible.
- Macros insert and do not submit. Pressing a pad is the only way to learn what
  it does, so a surface you explore by touching must not fire on contact. Load
  one, read it, hit Play.
- Approve/deny refuse to send unless herdr reports that agent `blocked`, so a
  stray press cannot type a bare `y` into someone's prompt.
- The model comes from the session's own transcript — herdr does not track it,
  but its record carries the Claude Code session id, which locates the file.
- Usage is accumulated incrementally off a byte offset. Transcripts only
  append, run to megabytes, and we poll twice a second.
- The screen is BGR565, blue in the high bits, confirmed against the hardware
  with labelled colour bars. Lines pad to 2048 bytes and the buffer is XORed
  with the signal-shaping mask. It blanks ~2s after the last frame, so the
  poll doubles as its heartbeat.
- No pyusb, no libusb, or a busy device means no screen and working pads. The
  pads are the product; the screen is the label on it.
- Mic permission attaches to whatever launches `rec` — the first hold raises a
  macOS prompt for your terminal.

## Setup

    brew install sox libusb
    python3 -m venv .venv
    .venv/bin/pip install mido python-rtmidi pyusb pillow numpy

`voiceEnabled: true` in `~/.claude/settings.json`. No `keybindings.json` — we
drive `space`, which is Claude's stock binding, so every session already has
it however old it is.
