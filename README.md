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
| **Play** | CC 85 | enter — submits, to the focused agent |
| **Buttons above screen** | CC 102–109 | pick a view: 1 agents, 2 usage |
| **Buttons below screen** | CC 20–27 | white marks the focused agent |

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

**agents** names every column: repo, status, model, and a tail of the terminal
id. That last one earns its space — two checkouts of one repo show the same
name, and the id is the only thing that tells them apart.

**usage** shows output tokens and context tokens per agent. Tokens, not money:
nothing here knows your plan, and an invented cost is worse than no cost.

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

Press the Push's **User** button too: in Live mode its MIDI goes to port 1 and
this sees nothing.

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
