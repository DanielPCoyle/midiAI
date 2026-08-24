# push-cc — Ableton Push 2 as an AI command center

Top pad row = up to 8 herdr agents. Tap a pad to focus that window.
Hold the tab button above it to talk; the transcript is inserted into that
agent's prompt box, **without** pressing Enter. You read it, then submit.

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
- First transcription after a reboot takes ~7s (141MB model off cold disk).
  Warm, a short utterance is ~1.4s.
- `base.en` is solid on full sentences and shaky on 2-word barks. If short
  commands misfire, drop in `ggml-small.en.bin` (slower) or point
  `record_stop()` at Groq's whisper-large-v3-turbo.
- Mic permission attaches to whatever launches ffmpeg — the first hold will
  raise a macOS prompt for your terminal.

## Setup that was already done

    brew install whisper-cpp
    python3 -m venv .venv && .venv/bin/pip install mido python-rtmidi
    curl -L -o ./ggml-base.en.bin \
      https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin
