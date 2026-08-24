# push-cc — Ableton Push 2 as an AI command center

Top pad row = up to 8 herdr agents. Tap a pad to focus that window.
Hold the tab button above it to talk. That drives the agent's own
`voice:pushToTalk`, so Claude records, transcribes and **submits** on release.
The agent does not need to be focused — you can talk to one while watching
another.

| Pad colour | herdr `agent_status` | meaning |
|---|---|---|
| green   | `idle`    | done |
| yellow (pulsing) | `working` | in progress |
| red (blinking)   | `blocked` | needs you |
| white   | `unknown` | agent present, state unclear |
| off     | —         | empty slot |

The white button directly above a pad marks the currently focused agent.

## Run

    .venv/bin/python push_cc.py

Press the Push's **User** button first — in Live mode all its MIDI goes to
port 1 and this sees nothing. If Ableton Live is running, turn *off*
Track/Sync/Remote for Push 2 port 2 in Live's MIDI prefs, or Live keeps the
User port to itself.

    push_cc.py --list      # show the pad→agent mapping, no hardware needed
    push_cc.py --selftest  # pure-logic asserts

## Notes

- Only agents running inside herdr panes appear. `claude agents --json` sees
  every session but offers no focus/send, so herdr is the substrate.
- Slots are pinned per terminal id: an agent exiting does not shuffle the
  others, so muscle memory survives. A 9th agent is invisible.
- Voice is Claude Code's, not ours. A terminal never sends key-up, so Claude
  infers "still holding" from key auto-repeat and calls it released after
  ~120ms of quiet. Holding a tab button just replays `ctrl+y` every 60ms into
  that pane; letting go stops the replay, and the gap *is* the release.
- `ctrl+y` because a control character is one byte, so `herdr agent send`
  ships it as literal text and neither side needs a key-name table. `f13` and
  friends are multi-byte escape sequences and encode differently per terminal.
- It submits. If you want to read the transcript before it goes, there is no
  seam here to do that — Claude owns the whole record/transcribe/submit path.
- Mic permission attaches to whatever launches `rec` — the first hold will
  raise a macOS prompt for your terminal.

## Setup that was already done

    brew install sox
    python3 -m venv .venv && .venv/bin/pip install mido python-rtmidi

`voiceEnabled: true` in `~/.claude/settings.json`, and in
`~/.claude/keybindings.json`:

    { "bindings": [ { "context": "Chat",
                      "bindings": { "ctrl+y": "voice:pushToTalk" } } ] }

Added, not moved: `space` still works for hold-to-talk when you are typing at
the keyboard yourself.
