#!/bin/zsh
# midiAI — double-click to start the surface.
#
#   midiAI.command          start everything and open the app in a browser
#   midiAI.command stop     stop all of it
#
# One mode, because a double-click cannot pass an argument and the Desktop
# shortcut is how this is actually launched. The API binds the LAN so an iPad
# running Expo Go reaches it without anyone typing an address.
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
pkill -f 'expo start' 2>/dev/null
sleep 1                      # let the old one drop the MIDI and USB handles

PUSH_BACKEND=tmux nohup "$PY" push_cc.py --debug >"$LOG/push.log" 2>&1 &
nohup "$PY" mapui.py --lan >"$LOG/mapui.log" 2>&1 &
LAN="$(ipconfig getifaddr en0 2>/dev/null || echo localhost)"
say "api       http://$LAN:8765  (LAN — anything on this network can type into your agents)"

(cd app && nohup npx expo start >"$LOG/expo.log" 2>&1 &)
sleep 3

# The Push is optional -- push_cc runs headless without it, and saying which of
# the three ways it is absent beats "not working".
"$PY" - <<'PY' 2>/dev/null
import mido, usb.core
usb_ok = usb.core.find(idVendor=0x2982, idProduct=0x1967) is not None
midi = any("Push 2 User Port" in n for n in mido.get_output_names())
if midi:      print("push      connected")
elif usb_ok:  print("push      on USB but no User Port — press User on the device")
else:         print("push      not plugged in — headless, the app is the whole surface")
PY

if pgrep -f push_cc.py >/dev/null; then
  say "push_cc   running"
else
  say "push_cc   not running — see $LOG/push.log"
fi

# Metro prints no URL when it is not on a terminal, so poll the port and build
# the exp:// address from the LAN address we already have.
say "app       waiting for Metro…"
for _ in {1..40}; do
  curl -sf -o /dev/null http://localhost:8081 && break
  sleep 1
done
open "http://localhost:8081" 2>/dev/null
say "          browser: http://localhost:8081"
say "          iPad:    exp://$LAN:8081  (paste into Expo Go)"

say ""
say "logs      $LOG/push.log  $LOG/mapui.log  $LOG/expo.log"
say "stop      $(pwd)/midiAI.command stop"
say ""
say "Closing this window leaves it running."
