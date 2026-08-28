# Handoff — replacing herdr

Branch `worktree-herdr-replacement-research`, four commits, `0860cf7..a10a440`.
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

1. **Commit the app files in the main checkout.** `Pane.js`, `Rail.js`,
   `Pads.js` are untracked and `theme.js` / `PushMirror.js` are newer there.
   Any UI work here forks against a stale app until that lands and this branch
   rebases onto it.
2. **Chart the wayfinder map** on board 216. Most of the spine is already
   walked — the settled items above belong in Decisions-so-far, not as open
   tickets. Real fog remaining: whether tmux counts as "contained in the
   application"; where agent management lives in the iPad UI and how a new
   agent's cwd is chosen; what replaces herdr's UI for the human; whether
   herdr's `done`-vs-`idle` split is worth adopting.
3. **Drive the Push with `PUSH_BACKEND=tmux`.** Nothing here has been tested
   with the hardware attached — it is all smoke tests.
4. **Settle blocked-detection.** Unknown whether `claude agents --json` reports
   `waiting` for a prose question as well as a permission prompt. If it does,
   this backend is better than herdr at spotting a stuck agent.

## Two things worth knowing

- **A latent bug, independent of all this.** `herdr()` reads stdout and
  `start_agent()` greps it for `agent_name_taken`. herdr 0.6.8 puts errors on
  stdout with exit 0; 0.8.2 puts them on stderr with exit 1. Upgrading herdr
  makes `start_agent` stop seeing name collisions and hand out duplicates.
- **Board 216 has ~40 junk labels** (`VANTA`, `LFQA`, `COOK`, `SEO`, the `*79`
  set) dragged in by cloning bugcast for its lanes. Harmless, undeleted,
  awaiting a decision.
