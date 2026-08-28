#!/bin/zsh
# midiAI — double-click to start the surface.
#
#   midiAI.command          start push_cc + mapui
#   midiAI.command app      ... and Metro, for the iPad or a browser
#   midiAI.command lan      ... and bind the API to the LAN, so an iPad can reach it
#   midiAI.command stop     stop all of it
#
# Restarting rather than refusing when something is already up: double-clicking
# twice is the obvious thing to do when you are not sure it worked, and two
# push_cc processes fight over the same MIDI port.

# ${0:A} resolves symlinks; a plain dirname "$0" lands in ~/Desktop when this
# is double-clicked through the shortcut, and then nothing is where it should be
cd "${0:A:h}" || exit 1
PY=".venv/bin/python"
LOG="/tmp"

say() { printf '%s\n' "$*"; }

stop() {
  pkill -f 'push_cc.py' 2>/dev/null
  pkill -f 'mapui.py' 2>/dev/null
  pkill -f 'expo start' 2>/dev/null
  say "stopped."
}

if [[ "$1" == "stop" ]]; then stop; exit 0; fi

if [[ ! -x "$PY" ]]; then
  say "No virtualenv at $PY."
  say "mido and pyusb live there, and the system python does not have them:"
  say "    python3 -m venv .venv && .venv/bin/pip install mido python-rtmidi pyusb pillow"
  read -r "?press return to close"
  exit 1
fi

say "midiAI"
say "──────"

# tmux is where the agents live. No session is not an error -- the surface
# shows nothing until the first one, and "+ new session" in the app makes it.
if tmux has-session 2>/dev/null; then
  say "tmux      $(tmux list-panes -a 2>/dev/null | wc -l | tr -d ' ') panes"
else
  say "tmux      no session yet — the app's \"+ new session\" will start one"
fi

pkill -f 'push_cc.py' 2>/dev/null
pkill -f 'mapui.py' 2>/dev/null
sleep 1                      # let the old one drop the MIDI and USB handles

PUSH_BACKEND=tmux nohup "$PY" push_cc.py --debug >"$LOG/push.log" 2>&1 &
if [[ "$1" == "lan" ]]; then
  nohup "$PY" mapui.py --lan >"$LOG/mapui.log" 2>&1 &
  say "api       http://$(ipconfig getifaddr en0 2>/dev/null || echo localhost):8765  (LAN)"
  say "          anything on this network can now type into your agents"
else
  nohup "$PY" mapui.py >"$LOG/mapui.log" 2>&1 &
  say "api       http://localhost:8765"
fi
sleep 3

# The Push is optional -- the app is a whole surface on its own, and saying
# which of the three ways it can be absent beats "not working".
"$PY" - <<'PY' 2>/dev/null
import mido, usb.core
usb_ok = usb.core.find(idVendor=0x2982, idProduct=0x1967) is not None
midi = any("Push 2 User Port" in n for n in mido.get_output_names())
if midi:      print("push      connected")
elif usb_ok:  print("push      on USB but no User Port — press User on the device")
else:         print("push      not plugged in")
PY

if pgrep -f push_cc.py >/dev/null; then
  say "push_cc   running"
else
  # push_cc exits when it cannot find the Push -- it predates the app, and
  # back then there was nothing to run without hardware. The API and the app's
  # own actions still work; what stops is the surface it publishes, so the
  # rail freezes on whatever it last said.
  say "push_cc   not running — it needs the Push, see $LOG/push.log"
  say "          the app still creates, prompts and closes agents;"
  say "          its session rail will show a stale snapshot until the Push is back"
fi

if [[ "$1" == "app" || "$1" == "lan" ]]; then
  say "metro     starting…"
  (cd app && nohup npx expo start >"$LOG/expo.log" 2>&1 &)
  sleep 12
  grep -o 'exp://[^ ]*' "$LOG/expo.log" | head -1 | sed 's/^/          /'
  say "          open Expo Go on the iPad, or press w in that log for the browser"
fi

say ""
say "logs      $LOG/push.log  $LOG/mapui.log"
say "stop      $(pwd)/midiAI.command stop"
say ""
say "Closing this window leaves it running."
