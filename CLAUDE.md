# midiAI

An Ableton Push 2 driven as a command centre for Claude Code agents. `push_cc.py`
is the hardware loop (MIDI in, USB screen out), `mapui.py` the HTTP API, `app/`
an Expo app for the iPad, `display.py` the Push's own screen, `term.py` the tmux
backend the surface talks to.

`README.md` is the real documentation — the surface table, the screen, chains,
the voice, and the Notes section, which is where the hard-won details live.

## Agent skills

### Issue tracker

Kanban cards on the SimplerDevelopment portal board **midiAI** (`projectId 216`,
`clientId 104`) via the Simpler Development MCP. Every call needs an explicit
`clientId` — the portal's default is a different company.
See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical roles, label strings unchanged from their names.
See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` and `docs/adr/` at the repo root, both created
lazily by `/domain-modeling` rather than up front.
See `docs/agents/domain.md`.

## What "this app" means

**Unless the Push 2 is named, the work is the React Native app.** `app/` — its
UI and UX — is the default subject of any request here: layout, the rail, the
panes, the prompt library, what a control does and whether it should be on
screen at all. The hardware is a peer of that app, not the centre of it.

So "the left hand side", "the panel", "the button" means the one in `app/`.
Only an explicit mention — the Push, the pads, the encoders, the touchstrip,
the glass, a CC number, `push_cc.py` or `display.py` — moves the subject to
the controller.

Two consequences worth stating, because both have been got wrong:

- Do not answer a UI request by editing `display.py` or the Push's own layout.
- Do not build a second on-screen copy of something the Push already is. The
  mirror tab is the one drawing of the hardware; a panel that redraws the pad
  grid beside it is two drawings of one thing, and they drift.

## Before you change the backend

`push_cc.herdr()` is the one function every pane call goes through, and it
dispatches on `PUSH_BACKEND` — `tmux` (default, `term.py`) or `herdr`. Two
things in there look like bugs and are not:

- `SCRAPE_LINES = "400"` has never returned 400 lines, from either backend.
  Claude Code runs on the alternate screen and never scrolls, so both cap at
  the pane height.
- An agent with no session yet reports `unknown`, not `idle`. The process is
  what says an agent is present; the session only enriches it.

`python3 push_cc.py --selftest`, `python3 term.py`, `python3 ptybridge.py`,
`node app/reflow_check.mjs`, `node app/check.js`, `python3 test_queue.py`,
`python3 test_graph.py`,
`python3 test_memory.py`, `python3 test_memory_mcp.py`,
`python3 test_memory_routes.py`,
`python3 test_agent_def.py`,
`python3 test_summarize_prompt.py` and `python3 smoke_tmux.py` are the gates.

**The last one is destructive.** It opens with `kill-server` because its first
checks are about a cold machine, so running it closes every agent on the box
and loses whatever each was in the middle of. "Cleans up after itself" used to
be written here and is what got a live session killed mid-flight. It now
refuses to start where a server is already up; `--force` is the only way past,
and it means what it says. Run it when the machine is idle, not as a
reflexive gate.
