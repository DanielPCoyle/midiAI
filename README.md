# push-cc — Ableton Push 2 as an AI command center

Eight agents on the top pad row, named on the Push's own screen, driven
without touching the keyboard.

## The surface

| Control | Notes / CC | Does |
|---|---|---|
| **The grid** | 36–99 | 64 prompt pads — insert canned text, no submit |
| **The grid, asked a question** | 36–99 | one pad per option down the left column, each its own colour — the prompt pads go dark |
| **Play** | CC 85 | enter — submits, to the focused agent · runs an armed chain |
| **Automate** | CC 89 | hold, tap a pad: arms that pad's row as a chain |
| **Touchstrip** | pitchbend | slide to set reasoning effort, `low` → `max` |
| **Buttons above screen** | CC 102–109 | pick a view — each with its own colour, named on the screen above it; press the one you are on to walk its modes |
| **Buttons below screen** | CC 20–27 | the agents: tap = focus · hold = talk · named on the screen above them |
| **Page ‹ ›** | CC 62–63 | walk the pad pages — right off the end makes a new one |
| **Solo** | CC 61 | pin: stop the surface following the terminal's focus, and stop questions pulling it |

Focus goes both ways. Tapping an agent button focuses that pane in the
terminal,
and clicking a pane on the computer moves the Push to it — one set of
agents, two pairs of hands, never two different ideas of where you are.
Solo opts out.

The grid is one **page** of pads, and Page ‹ › walk between them. Paging right
off the end mints a new one, but only from a page with something on it —
otherwise leaning on the button would make empty grids forever. Left of the
first does nothing: pages are a line, not a ring, and falling from page one to
page nine is never what the hand meant. A page you empty is gone the next time
the file is read, so there is nothing to delete and nothing to accumulate. The
count appears on the glass beside the view names only once there are two, so
the tag showing up *is* the news that a second page exists.

Those two buttons used to step between agents, which the eight buttons under
the display and the top pad row already did.

While an agent is asking something, the grid stops being prompt pads altogether:
one pad per option, down the left column, and every other pad dark. A column
of lit pads on a dark grid reads as a list — the same options as whole rows
read as a grid that has caught fire. The prompt pads go out rather than sit there
looking pressable beside an answer, because there is one thing to do.

Each option has its own hue, and the number beside it on the glass is drawn in
that same hue: `ANSWER_CC` in `push_cc.py`, `ANSWER_RGB` in `display.py`, and
`ANSWER_HEX` in the app — the last derived from the pad palette rather than
retyped, so the three cannot drift. Six hues, cycled: an option seven that
repeats option one beats a palette index guessed blind on hardware.

The app shows the same list off the same `opts`, coloured the same way but as
full-width rows, since it has the width for the text and no 64 identical
squares to disambiguate. The pager and the pad editor withdraw for as long as
the question stands.

Pad colour follows the agent's status:

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

**focus** is one agent, full width: its TLDR at rest, the raw pane when you
scroll, the question and its options when it asks one. Its second mode is the
shortcuts grid — the same subject, the agent you are driving, shown as what
you can say to it. Record, Select and Automate all put you there, because
that is the mode their pads live in.

**sessions** names every column: repo, status, model, and a tail of the
terminal id. That last one earns its space — two checkouts of one repo show
the same name, and the id is the only thing that tells them apart.

In this view the pads are the *subagents* the current agent has spawned —
read off `~/.claude/projects/<session>/subagents/`, labelled with the
description each was dispatched with. Yellow pulsing is still running, green
has come back. Tapping one asks before it acts, and yes points the **focus**
view at that subagent's own transcript instead of the agent's pane. An
agent button takes you back. The view's second mode names them all on the
glass, in pad order — the grid says which are still going, the list says what
each was asked to do.

Two permanent bands frame every view: the top names what each button above the
screen switches to, the bottom names the agent each button below it drives.
Eight identical buttons you have to remember are not a surface.

**prs** lists the open pull requests for the selected agent's checkout —
your branch first, then whatever is on fire. The swatch is the whole CI answer
(green passed, yellow still running, red failed, grey no checks), with the
author and review state on the right. `gh` runs on its own thread and the list
is cached for a minute; **Browse** opens the same list in a browser.

**usage** answers one question — what is being spent — at three altitudes,
walked with its own view button or Left/Right: the plan's own limit bars, then
tokens by model, then output and context tokens per agent. Tokens, not money:
an invented cost is worse than no cost.

## The app

The UI is a React Native app (Expo) in `app/`, laid out for an iPad in
landscape: the Push's own screen across the top with its two button rows where
they physically sit, and the 64 pads below it across the full width. The pad
editor is a sheet, not a column — an **edit pad** key appears in the top row
once you have picked one, and the editor was holding a third of the screen
open next to a grid that wants every pixel it can get.

Pads are rearranged by dragging one onto another, which swaps them — the same
trade the Push's own move mode makes, minus the mode. One `PanResponder` for
the whole grid rather than sixty-four: the pads are a regular 8×8, so where a
finger is *is* which pad it is on, and a drag crossing pads never has to be
handed from one child to the next. Under six pixels of travel it was a tap and
selects instead; a drag that ends off the grid, or back where it started, is a
change of mind and does nothing. An empty pad has nothing to give, so dragging
one would only teleport the pad you aimed at — it is refused.

Hover a pad, or tap it, and after a moment a card comes up over it: the label,
the first 200 characters of what it would send, and two keys — **run** and
**edit**. A pad is a two-word label for a paragraph you wrote days ago, and
the two ways to find out what it says were to fire it at a live agent or to
open the editor. The card is the third. It never appears while `tap fires the
pad` is armed, where a tap has already done the thing the card would ask about.

What puts it away is another pad, an empty one, a tap or a drag — never the
card noticing you left it. react-native-web builds hover out of events that
bubble, so crossing onto the card's own title or its keys arrives as *leaving*
the card, and it closed itself every time you reached for the buttons it
exists to offer. A thing that cannot be trusted to say when you have gone
should not be the one asked.
Tapping a view button here presses that button there too, by default: the
app's own idea of which view and mode it is showing is re-seeded from the
Push on every poll. **following the Push**, lit by default, is what makes
that true — turn it off and the two go independent, the view staying
wherever you left it, so a second screen can look at something the Push is
not. Agent focus stays global and shared either way; only the view went
local.

The screen is mirrored, not re-drawn. `push_cc.py` saves the frame it just
sent to the Push and the app shows that image: two drawings of one screen
drift apart the day someone edits only one of them. Its two label bands are
cropped off, because the rows above and below the glass *are* those labels, as
buttons. A tap comes back through a file the poll loop already watches and
lands in `tap_tab` / `tap_seat`, the same functions a physical press lands in,
so the two cannot come to mean different things.

    python3 mapui.py --lan          on the Mac: the API, reachable on the LAN
    cd app && npx expo start        then open it in Expo Go on the iPad

`--lan` is the part to be deliberate about: this endpoint types into live
Claude Code agents, so anything on that network can too. Without it the
server stays on 127.0.0.1 and only `npx expo start --web` — the same app in a
browser on the Mac — can reach it.

Expo Go needs nothing built. A standalone app — its own icon, no Expo Go —
is `npx expo run:ios` with Xcode, or an EAS build; `app.json` carries the two
Info.plist keys that path needs, because both of its failures are silent. The
API speaks plain http to an address on the LAN, so App Transport Security
refuses it, and since iOS 14 anything reaching for a local address needs its
own permission before the prompt is ever shown. Expo Go supplies both itself,
which is exactly why the browser and Expo Go work and a built app would have
come up with an empty grid and nothing in the log but a refused fetch.

The iPad does not have to be told where the Mac is. Expo Go loads the bundle
from it, so `Constants.expoConfig.hostUri` already holds the address; the host
field at the top is only there for when that guess is wrong.

The header says *why*, not just whether. `push_state` in `mapui.py` already
knows the difference between push_cc being down, the Push being off USB, and
the Push sitting in Live mode, so the app prints that sentence — the light it
replaced went green whenever a surface file existed on disk, which a dead
push_cc leaves behind. **reconnect**, beside it, restarts push_cc: the one
move that fixes all three. It lights up the moment there is something to fix
and greys out when the API itself is what is missing, because nothing can
restart a server that is not running.

Every control that is not a pad is drawn as one of the Push's own buttons: a
matte near-black key with a thin LED bar low on its face, dark when the button
is off and glowing its colour when it is on. The hardware puts the light there
rather than in the button, so the row above the screen reads the same whether
you are looking at the desk or at the iPad.

It keeps working with the Push's screen unplugged: the renderers are pure PIL
and only `disp.show()` needs the device.

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
sits on one agent with no button able to move it. Startup now asks for User
mode itself (`Set MIDI Mode`, sysex `0A 01`). Sysex is accepted on both ports in
every mode, so that request lands whichever port the device is currently
listening to, and Ableton never has to be opened to do it.

## The panes

The agents live in a terminal multiplexer, and the surface talks to it through
one function. **tmux** is the default and the whole of it:

    tmux new -s push        # or attach: tmux attach -t push
    .venv/bin/python push_cc.py

Nothing else is required — no server to keep running, no daemon of ours. With
no tmux session at all the surface simply shows no agents, and **Add Device**
starts the first one, session and all.

Every call goes through `herdr()` in `push_cc.py`, which dispatches on
`PUSH_BACKEND`:

| `PUSH_BACKEND` | Substrate |
|---|---|
| `tmux` (default) | `term.py` — `tmux` plus `claude agents --json` |
| `herdr` | the `herdr` CLI, as before |

The seven calls the surface makes are `agent list`, `agent read`, `agent send`,
`agent focus`, `agent start`, `pane close`, `worktree create`. `term.py`
answers them in the same JSON, which is why nothing downstream knows or cares
which one is running.

Two things are worth knowing about the tmux backend. Agents are matched to
panes on **pid**, never cwd, because two agents in one repo is the normal case
and a cwd would silently fold them into one. And a pane holds a claude the
moment the process is there, whether or not a session has started — a claude
still sitting in the agents view reports `unknown` rather than vanishing off
the surface until someone types into it.

`smoke_tmux.py` exercises all eight against a live tmux server, including the
one that matters here: that no call ever reaches for `herdr`. `mission_check.py`
walks push_cc's own data path against real panes, and `mission_api.py` drives
the routes below.

## The app's own hands

The Push can make an agent (Add Device) and a worktree (Add Track), but for a
long time the app could only fire prompts and press buttons. It can now do the
rest over HTTP:

| Route | Does |
|---|---|
| `GET /agents` | every agent, with cwd, status, focus and name |
| `POST /agents` | `{cwd, name?, split?}` — a new claude in that directory |
| `POST /agents/rename` | `{terminal_id, name}` |
| `POST /agents/close` | `{terminal_id}` |
| `POST /prompt` | `{text, submit?, terminal_id?}` — free text, not a pad |

`/prompt` without a `terminal_id` goes to whichever agent the Push is
pointed at, which is the same target a pad fires into: one place decides what
"the current agent" means, so a tap and a typed sentence cannot disagree.

A name is `[a-z][a-z0-9_-]{0,31}` and unique among live agents — herdr's rule,
kept because it was already the right one. It lives in a tmux pane option
rather than the pane title, which claude overwrites with its own.

An agent with no name reads by the basename of its directory, exactly as
before. Renaming is a convenience, not a requirement.

## Chains

A **row is a chain**. Hold **Automate** and tap a pad: everything from that pad
rightward to the end of its row is queued, in order, empties skipped. The pads
light up, the screen lists the steps, and nothing has happened yet — **Play**
runs it, **Stop** throws it away.

Two gestures, deliberately. A chain is fire-and-forget by definition, and the
prompt pads already learned this lesson: you get to read what is about to run
before it runs.

Steps advance on the agent's own status, not a timer. The catch is that status
lags the send — an agent still reports `idle` for a poll or two after its enter
lands, so advancing on a bare `idle` fires the whole row into one prompt. A step
is done once we have seen it go `working` and then sit `idle` for two polls
running. A step that starts and finishes between polls is caught by a grace
period instead.

`blocked` **pauses** the chain rather than advancing it. The agent asked you
something; the grid becomes that question's options and the chain picks up
where it left off once one is pressed. A pipeline that stops and asks is the point, not a case to
engineer around.

The chain pins to the agent it started on, so you can walk away and watch
another agent while it runs — which is most of the reason there are eight.
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

What the screen reports is read back out of the agent's own transcript, where
each message records its `effort`. Not the last value we wrote: the level can be
changed from the keyboard too, and a readout that only ever echoes our own
writes would be worse than none.

## How the voice works

It is Claude Code's own `voice:pushToTalk`, not ours.

A terminal never sends a key-up event, so Claude infers "still holding" from
key **auto-repeat** and calls it released after ~120ms of quiet. Holding a pad
past 250ms replays `space` into that pane every 60ms; letting go stops the
replay, and the gap *is* the release.

The backend writes into a pane's pty without focusing it, so you can
talk to one agent while watching another. It submits on release — Claude owns
the whole record/transcribe/submit path and there is no seam to read it first.

That is why the app has its own **Hold to talk**, under the composer. Same
gesture, opposite trade: `rec` records for as long as your finger is down and
`whisper-cli` reads the file back after you let go, so it costs a second or
two and gives up the text — which lands in the composer, editable, unsent.
Nothing reaches an agent until you press Send.

The mic is the machine running the agents, not the tablet. Expo Go has no
speech recognition to call, so a browser-side recogniser would leave the iPad
with nothing, and the mic worth talking into is the one already by the Push.

Whichever put it there, dictated text shows up in the composer and nowhere
else. It used to be drawn beside it as a read-only card headed `TYPING`, which
left the one editable field on the view empty next to the words you had just
said. The composer adopts the agent's input line instead — on change, so a
scrape twice a second cannot fight your typing, and only into a box you have
not edited. Send rewrites that line rather than adding to it.

## Images

A pty carries text, so an image cannot be typed into one. What can be typed is
a **path**: paste or attach in the composer and the file is saved on the
machine running the agents, under `~/.midiai/pastes/`, with its path dropped
into the box like anything else you are about to send. The agent reads the
file itself.

The type comes from the file's own leading bytes, never from the name the
client sent -- the name is the one part of an upload a caller picks, and this
server can be bound to the LAN.

Web only, and on purpose: iOS hands React Native no paste event and no
clipboard image without another dependency. In Expo Go the composer still
takes typed paths.

## Notes

- A question is only a question if the pane draws a **caret** on one of its
  options. The pattern that finds them matches anything opening with `1.`, and
  Claude writes numbered lists in prose constantly — a two-bullet recap read as
  a two-option question, blanked the prompt grid, and offered to answer it. The
  select widget always carets its current choice, and answering *is* walking
  that caret, so with none to walk from there was nothing to answer with except
  arrow keys and Enter fired into whatever the agent was really doing.
- Only agents in the multiplexer's panes appear. `claude agents --json` knows
  every agent's cwd, status and id but offers no focus, no send and no read,
  so a pane is still the substrate — it is the half that has hands.
- Slots pin per terminal id: an agent exiting does not shuffle the others, so
  muscle memory survives. A 9th agent is invisible.
- Prompts insert and do not submit. Pressing a pad is the only way to learn what
  it does, so a surface you explore by touching must not fire on contact. Load
  one, read it, hit Play.
- Approve/deny refuse to send unless that agent reports `blocked`, so a
  stray press cannot type a bare `y` into someone's prompt.
- The model comes from the agent's own transcript — no multiplexer tracks
  it, but the Claude Code session id locates the file, and the id is the one
  thing every backend can hand over.
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
- `/surface`'s `data` is only the view the Push glass is on — a second screen
  is not a second Push, so `views_data` carries every view, every mode, every
  tick: `{"focus": [...], "sessions": [...], ...}`, each element the same
  dict `data` would hold for that view+mode. The two modes the strip takes
  over for itself (the prompt grid, a chain's step list) still get the app a
  real pane there rather than an empty one — the strip's screen and the
  app's idea of the pane are drawn from the same place but are not the same
  thing.

## Setup

    brew install sox libusb whisper-cpp
    python3 -m venv .venv
    .venv/bin/pip install mido python-rtmidi pyusb pillow numpy

The app, once:

    cd app && npm install

`voiceEnabled: true` in `~/.claude/settings.json`. No `keybindings.json` — we
drive `space`, which is Claude's stock binding, so every session already has
it however old it is.
