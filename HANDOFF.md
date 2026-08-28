# Handoff — replacing herdr

Branch `worktree-herdr-replacement-research`, nine commits, `0860cf7..69984ba`.
Board: SimplerDevelopment project **216**, map card **1805**.
Worktree at `.claude/worktrees/herdr-replacement-research`.

## Goal

Close herdr entirely and keep the Push 2 surface working, then create, delete,
rename and prompt agents from the Expo app — so the whole thing runs contained
in this application.

## Current progress

**Off herdr, provably.** `push_cc.herdr()` was already the single choke point
for every pane call; it now dispatches on `PUSH_BACKEND`, default `tmux`
(`term.py`), with `herdr` still available. `smoke_tmux.py` watches every
subprocess through all eight calls and asserts the only binaries used are
`claude`, `git`, `ps`, `tmux`. Closing herdr cannot break the surface.

**Agent lifecycle from the app.** `mapui.py` gained `GET /agents`,
`POST /agents`, `/agents/close`, `/agents/rename`, `/prompt`. Names live in a
pane-scoped tmux `@agent_name`; `agent_name()` prefers it and falls back to the
cwd basename.

**Board.** SimplerDevelopment portal, project **216** (`clientId 104`), lanes
Backlog(942) → Planned(943) → In Progress(944) → Validating(945) →
Approved(946) → Shipped(947). SKU `MIDI-###`. Its description carries the
settled findings; `docs/agents/issue-tracker.md` carries the wayfinding
operations.

Gates: `python3 push_cc.py --selftest`, `python3 term.py`,
`python3 smoke_tmux.py`. All pass. `HERDR-REPLACEMENT.md` is the full research
record.

## What worked

- **Mimicking herdr's JSON instead of designing a nicer API.** `term.py`
  answers in herdr's shapes, so ~2800 lines of push_cc and all of `mapui.py`
  needed no edit. The whole migration is a 12-line diff in `push_cc.py`.
- **Reading herdr's source (Apache-2.0, Rust) for architecture.** Three things
  came from it: identify an agent by its *process* and treat the session as
  enrichment; put the name on the pane, not the window; one worktree root with
  a slugified branch.
- **Measuring instead of reasoning.** Every claim in `HERDR-REPLACEMENT.md`
  that matters was measured on this machine.

## What didn't work — do not redo these

- **Do not "fix" `SCRAPE_LINES = "400"`.** It has never returned 400 lines,
  from herdr either. Claude Code runs on the alternate screen and never
  scrolls, so both backends cap at the pane height — measured 57 on each, and
  herdr's own guide says rows leaving the alt screen never enter its
  scrollback. I initially called this a tmux gap; it is not.
- **Do not port `claude.toml`.** herdr scrapes Claude's status with 208 lines
  of regex and ships a *remote update channel* to replace them when the UI
  changes. We would have no such channel. Status comes from
  `claude agents --json`.
- **Do not read herdr HEAD (0.8.2) for CLI shapes.** Installed is 0.6.8 and
  they differ: HEAD has no `agent send` at all, and its `agent start` rejects
  `--cwd/--split/--focus` as "legacy". Architecture yes, shapes no.
- **Do not join panes to agents on cwd.** Two agents in one repo is the normal
  case; a cwd join silently folds them into one. Join on pid.
- **Option B (push_cc owns the ptys) is blocked** by `mapui.py:54` —
  `relaunch()` does `pkill -f push_cc.py` from a browser button, so owning the
  ptys means every relaunch kills every agent. It would need a separate daemon,
  which is tmux.

## Next steps

0. **Two operator-only blockers, both on the board** — MIDI-005 (card 1810)
   and MIDI-006 (card 1811). Merge `worktree-herdr-replacement-research` into
   main, commit the app files there, and stop the running push_cc (pid 76178)
   so this branch's can drive the Push. Nothing else is waiting on anything.

1. **Commit the app files in the main checkout.** `Pane.js`, `Rail.js`,
   `Pads.js` are untracked and `theme.js` / `PushMirror.js` are newer there.
   Any UI work here forks against a stale app until that lands and this branch
   rebases onto it.
2. ~~Chart the wayfinder map.~~ **Done — map card 1805 on board 216**, with
   the settled items in Decisions-so-far and six tickets, four of them closed.
   Fog still genuinely open: what replaces herdr's UI for the human, and where
   agent management lives in the iPad UI (the latter waits on the app files).
   `done`-vs-`idle` moved to Out of scope — MIDI-002 showed it cannot
   distinguish the case it was wanted for.
3. **Drive the Push with `PUSH_BACKEND=tmux`** (MIDI-005, card 1810). The
   device is on USB; what blocks it is the push_cc already running on the herdr
   backend, which owns the MIDI port. Everything reachable without the hardware
   is verified — `mission_check.py` walks the data path, `mission_api.py` the
   routes.
4. ~~Settle blocked-detection.~~ **Done — the answer is no.** An
   AskUserQuestion widget reports `waiting`, but so does herdr see it (the
   widget draws the same "esc to cancel" chrome its rules match). A *pure
   prose* question reports **`idle`** — same blind spot as herdr, because the
   agent genuinely is idle: it asked and stopped. `sweep_panes` is
   load-bearing. Reproduce with `mission_q.py` / `mission_p.py`.

## Two things worth knowing

- ~~A latent bug~~ **fixed** (MIDI-003). `backend_said()` now reads whichever
  stream carried an error object, so an upgrade to herdr 0.8.2 — which moved
  errors to stderr — no longer makes `start_agent` hand out duplicate names.
- **Board 216 has ~40 junk labels** (`VANTA`, `LFQA`, `COOK`, `SEO`, the `*79`
  set) dragged in by cloning bugcast for its lanes. Harmless, undeleted,
  awaiting a decision.
