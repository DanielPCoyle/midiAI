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

## Before you change the backend

`push_cc.herdr()` is the one function every pane call goes through, and it
dispatches on `PUSH_BACKEND` — `tmux` (default, `term.py`) or `herdr`. Two
things in there look like bugs and are not:

- `SCRAPE_LINES = "400"` has never returned 400 lines, from either backend.
  Claude Code runs on the alternate screen and never scrolls, so both cap at
  the pane height.
- An agent with no session yet reports `unknown`, not `idle`. The process is
  what says an agent is present; the session only enriches it.

`python3 push_cc.py --selftest`, `python3 term.py` and `python3 smoke_tmux.py`
are the three gates. The last one needs a live tmux server and cleans up after
itself.
