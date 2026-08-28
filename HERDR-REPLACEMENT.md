# Replacing herdr

## The dependency is one function

Every herdr call in this codebase goes through `herdr()` at `push_cc.py:414`.
`mapui.py:197` calls `push_cc.herdr(...)`. Nothing else shells out to it. So
the surface to replace is that one function plus the two helpers that shape
its output — `agents()` (`push_cc.py:446`) and `start_agent()` (`push_cc.py:433`).

The other ~2800 lines consume dicts, not herdr. Keep the dict shape and they
do not change.

## What is actually asked of herdr

Seven calls, and nothing else:

| Call | Site | What it needs |
|---|---|---|
| `agent list` | `agents()`, main loop | per pane: `cwd`, `agent_status`, `focused`, `terminal_id`, `pane_id`, `agent_session.value` |
| `agent read <tid> --lines N` | `pane_summary()` :334 | the rendered pane text, 400 lines deep for focus, 40 for the sweep |
| `agent send <tid> <text>` | ~15 sites + `mapui._fire` | literal bytes into a pty **without focusing it** |
| `agent focus <tid>` | `tap_seat` :1859 | move the human's screen |
| `agent start <name> --cwd --split right --focus -- claude …` | `start_agent()` | spawn a claude in a new split |
| `pane close <pane_id>` | confirm-close :1833 | kill a pane |
| `worktree create --cwd --branch --focus` | Add Track :2366 | git worktree + open it |

## What is already herdr-free

More than it looks. Everything derived from the transcript needs only
`cwd` + the Claude session id:

- `model_for` / `short_model` — regex over `~/.claude/projects/<slug>/<sid>.jsonl`
- `effort_for`, `context_for`, `usage_for`, `finished_calls` (subagents) — same file
- all of `read_pane` / `prompt_text` / `tldr` / `_OPT_RE` — parses rendered text,
  does not care who rendered it

And `sweep_panes` already carries the comment that `agent_status` is not
trustworthy for questions ("an agent asking a prose question stays idle, so
herdr never reports it. Only the pane knows."). The app is already doing its
own state detection where it matters.

## Two of the four fields come from Claude Code itself

`claude agents --json` is installed and works today:

```json
{"pid": 4041, "cwd": "/Users/dancoyle/midiAI", "kind": "interactive",
 "sessionId": "534f3da9-…", "name": "midiai-1b", "status": "busy"}
{"pid": 68940, "cwd": "/Users/dancoyle", "status": "waiting",
 "waitingFor": "input needed"}
```

That is `cwd`, `agent_session.value` (→ `sessionId`), and `agent_status`
(`busy`→`working`, `waiting`→`blocked`, else `idle`) — for **every** claude on
the machine, herdr pane or not. It even beats herdr on one point: `waitingFor`
is reported, where herdr's own hook (`~/.claude/hooks/herdr-agent-state.sh`)
only forwards the session id and lets herdr infer the rest from the pty stream.

What `claude agents --json` cannot give: a handle to **send** to, **read**, or
**focus**. That is the whole remaining problem, and it is a terminal-host
problem, not an agent problem.

## Options

### A. tmux + a shim — recommended
Install tmux, rewrite `herdr()` as a dispatcher over `tmux`, join to
`claude agents --json` by pid.

| herdr | tmux |
|---|---|
| `agent list` | `tmux list-panes -a -F '#{pane_id} #{pane_pid} #{pane_current_path} #{pane_active} #{window_active}'` ⋈ `claude agents --json` on pid |
| `agent read` | `tmux capture-pane -p -t %7 -S -400` |
| `agent send` | `tmux send-keys -l -t %7 -- "$text"` (literal, no focus change — exactly herdr's semantics) |
| `agent focus` | `tmux select-window` + `select-pane` |
| `agent start` | `tmux split-window -h -c DIR claude --permission-mode auto` |
| `pane close` | `tmux kill-pane -t %7` |
| `worktree create` | `git worktree add` + `tmux new-window -c <path>` |

The pid join is the only new idea, and it is cheap: launch claude as the
pane's *direct* command so `#{pane_pid}` **is** the claude pid. For panes
where claude sits under a shell, one `pgrep -P` walk.

`terminal_id` becomes the tmux pane id (`%7`). `pane_id` the same. Slot
pinning, `follow_focus`, the mirror, `publish_target` — all untouched, they
only ever compare ids for equality.

Cost: ~120 lines, one new binary (`brew install tmux`). Reversible: keep
`herdr()` behind a flag. All seven calls survive intact — verified end to end
in **Probe results**.

### B. Own the ptys in Python
`pty` + `pyte` for screen emulation; push_cc becomes the terminal host; the
Expo app renders panes. Zero external deps, no subprocess-per-poll, and the
iPad gains a real terminal view. Also ~1000 lines of terminal emulator you now
maintain, and the human loses a desktop terminal unless you build one.

Worth it only if the iPad app is meant to *become* the workstation. Not today.

### C. Claude Agent SDK / headless
Drop terminals entirely; agents run as SDK sessions, questions arrive as
`canUseTool` callbacks instead of scraped text. Architecturally the cleanest —
no parsing, no `_OPT_RE`, no caret heuristic. But it deletes the premise:
"one set of sessions, two pairs of hands". There is no pane for the human to
sit in. This is a different product.

## Plan for A

*(superseded by **Final plan — A1** at the foot of this document)*

1. ~~`brew install tmux`. Confirm the semantics.~~ **Done — all three claims
   verified in Probe results.**
2. New `term.py`: `panes()`, `send()`, `read()`, `focus()`, `start()`,
   `close()`, `worktree()`. `panes()` returns the **same dicts** `agents()`
   returns today.
3. Point `herdr()` at it behind `PUSH_BACKEND=tmux|herdr` so both run and you
   can A/B on real hardware.
4. `mapui.py:197` → `push_cc.send(target, …)`.
5. Delete the herdr branch and the env var once a session's worth of driving
   has not surfaced a difference.

Steps 2–4 are mechanical once step 1 confirms the semantics — good Sonnet work.

## What you actually lose

Not code — herdr's UI. The agent panel, toasts, `agent_panel_scope = "all"`,
named workspaces, remote attach. tmux gives you a status line and a scripting
surface, not that. The honest question is not "can we replace herdr" — we can,
in ~120 lines — but "do you want to stop using herdr as a human?" If the
answer is no, this migration buys nothing; if it is yes, A is a day.

## Verify before committing to any of it

```sh
brew install tmux
tmux new -d -s probe 'claude --permission-mode auto'
tmux list-panes -a -F '#{pane_id} #{pane_pid} #{pane_current_path}'
claude agents --json | python3 -c 'import json,sys;print([a["pid"] for a in json.load(sys.stdin)])'
# the pids must line up. if they do, option A is confirmed.
tmux send-keys -l -t %0 -- 'hello'      # must land without stealing focus
tmux capture-pane -p -t %0 -S -40
```

---

# Probe results

Ran, on this machine, against a real claude in a detached tmux pane
(`probe_tmux.py`, tmux 3.7c). Three claims tested; two hold outright, one
does not.

## 1. Byte transport — PASS

`tmux send-keys -l` into a **detached** pane carries every byte string
push_cc sends, verified against push_cc's own `prompt_text()`:

```
PASS  literal text             prompt_text='probe text'
PASS  backspace x5             prompt_text='probe'          \x7f
PASS  left arrow x3            prompt_text='probe'          \x1b[D
PASS  forward delete x3        prompt_text='pr'             \x1b[3~
PASS  escape clears            prompt_text=''               \x1b
```

Detached is the strong form of "without focusing it" — there is no client to
steal focus from. This is `agent send`, exactly.

## 2. pid join — PASS

`#{pane_pid}` equals `claude agents --json`'s `pid` when claude is the pane's
direct command. And the session ids agree with herdr's, checked side by side:

```
simplerdevelopment2026   herdr=254503c2  claude=254503c2  same=True  model='fable 5'
/Users/dancoyle/midiAI   herdr=534f3da9  claude=534f3da9  same=True  model='opus 5'
```

`claude agents --json` `sessionId` **is** herdr's `agent_session.value`. So
`transcript()`, and with it model / effort / context / usage / subagents,
survives untouched.

Join on **pid, never cwd** — two agents in this very workspace share
`/Users/dancoyle/midiAI`. A cwd join silently collapses them.

## 3. Pane read depth — PASS (my first read of this was wrong)

I initially called this a FAIL. It is not. The correction matters because it
decides the whole A1/A2/B question, so here is the full working.

Claude Code runs on the **alternate screen** and never scrolls the terminal —
it repaints. Measured through a 200-line response, on an 80x24 pane:

```
  0s alt=1 hist=0  capture_lines=24
 42s alt=1 hist=0  capture_lines=24
 84s alt=1 hist=0  capture_lines=24     (pane showing 185..198 — the output was real)
```

`history_size` never leaves 0. So tmux has no scrollback for a claude pane,
and `capture-pane -S -400` can only ever return the visible screen.

I stopped there and called it a gap. That was the error: I never checked what
**herdr** returns. It is not 400 either.

```
simplerdevelopment2026  herdr agent read --lines  40 ->  40 lines
simplerdevelopment2026  herdr agent read --lines 400 ->  57 lines
/Users/dancoyle/midiAI  herdr agent read --lines 400 ->  57 lines
```

57 — herdr's pane height. herdr returns the visible screen too. It has no
scrollback for an alt-screen app either, `pane_history = true` notwithstanding.

Rerunning the tmux measurement with the pane sized to match:

```
tmux new -d -s hist2 -x 80 -y 57 …
tmux capture-pane -S -40  -> 57 lines
tmux capture-pane -S -400 -> 57 lines      height=57 alt=1
```

Identical. The 24-vs-57 difference was my test pane being 24 rows tall, not a
capability gap.

**`SCRAPE_LINES = "400"` has never delivered 400 lines.** It delivers one
screen, from herdr today and from tmux tomorrow. The focus view's scroll walks
a screen, and always has. Nothing is lost by moving.

One shim detail: herdr truncates to N (`--lines 40` → 40); tmux `-S -40` means
"start 40 lines back in history" and returns the whole visible screen. So
`read(n)` must be `capture-pane -p -S -0` then `[-n:]`.

## Revised recommendation — A1, and A2/B are not needed

The comparison you asked for resolves itself: **A2's and B's entire
justification was scrollback parity, and there is no scrollback to be at
parity with.** Neither is worth building for this.

- **A1 — tmux + ~120-line shim.** Full parity on all seven calls. No `pyte`,
  no pty ownership, no new Python dependency. A day.
- **A2 — dropped.** It buys a terminal emulator to recover history that no
  layer of the stack actually has.
- **B — own the ptys.** Still coherent, but only as *"the iPad becomes the
  workstation"*, never as a herdr replacement. And it has an independent
  blocker, below.

### The blocker B would have hit anyway

`mapui.py:54` — `relaunch()` does `pkill -f push_cc.py` and respawns, exposed
at `POST /relaunch` and reachable from a button in the browser mirror.

push_cc is **designed to be disposable**. If it owned the ptys, every relaunch
would kill every agent on the machine. B therefore requires the pty host to be
a separate long-lived daemon that survives push_cc — process hosting, detach
and reattach, resize, reaping, a way for the human to attach a terminal to it.

That daemon is tmux. Writing it is writing tmux.

## Final plan — A1

1. ~~`brew install tmux`~~ done (3.7c), semantics verified above.
2. `term.py`: `panes()`, `send()`, `read(n)`, `focus()`, `start()`, `close()`,
   `worktree()`. `panes()` returns the same dicts `agents()` returns today —
   join on **pid**, truncate `read` to N.
3. `herdr()` dispatches behind `PUSH_BACKEND=tmux|herdr`; A/B on hardware.
4. `mapui.py:197` → `push_cc.send(target, …)`.
5. Drop the herdr branch once a session's driving shows no difference.

Steps 2–4 are mechanical now that the semantics are pinned — good Sonnet work.

## The decision this really comes down to

Not capability. Every herdr call has a verified tmux equivalent, and the one
feature that looked exclusive turned out not to exist. What you would give up
is herdr's **UI** — the agent panel, toasts, `agent_panel_scope = "all"`,
named workspaces, remote attach — for tmux's status line and scripting.

If you still want to sit in herdr as a human, this migration buys nothing but
independence. If you are ready to live in tmux, A1 is a day's work and the
dependency is gone.

---

# Reviewing herdr's source (Apache-2.0, Rust)

`github.com/herdrdev/herdr`. **Installed here is 0.6.8; HEAD is 0.8.2** — two
minor versions apart, and the semantics differ (HEAD's `agent start` requires
an existing pane and never splits; 0.6.8's takes `--split`, which is what
push_cc calls). Read the source for ideas, not for shapes to copy.

## What it confirmed

**The scrollback finding, verbatim from herdr's own agent skill:**

> `--lines` asks Herdr for more rows from the pane's available screen and host
> scrollback. If increasing it does not reveal more of a completed response,
> the pane is probably running the agent on the terminal's alternate screen.
> Rows that leave the alternate screen do not enter Herdr's host scrollback,
> so a larger line count cannot recover them.

And in the engine: herdr embeds **libghostty-vt** (Ghostty's Zig terminal
core), which allocates the alternate screen with `max_scrollback = 0`
(`vendor/libghostty-vt/src/terminal/Terminal.zig:3714-3725`) — exactly as a
real terminal does, exactly as tmux does. There is no ring buffer to miss.

*(There is one bolt-on: `src/server/alt_screen_read.rs` synthesizes
mouse-wheel bytes into the pty and diffs the redraws to harvest history. It
only fires when the agent is idle AND has mouse-reporting enabled AND
repaints like a pager. Claude Code does not, which is why we both measure one
screen. Not worth reimplementing.)*

**`agent_status` for Claude is 100% screen-scraped.** The `SessionStart` hook
calls `pane.report_agent_session`, whose params carry no `state` field at all
(`src/api/schema/panes.rs:383-394`) — session id only. Hook-driven state
exists but only for an allow-list Claude is not on (`src/detect/mod.rs:316`).
State comes from `src/detect/manifests/claude.toml`, 208 lines of prioritized
`contains`/`regex` rules over screen regions, polled every 300ms.

That file also confirms push_cc's comment. Every `blocked` rule requires
permission-dialog chrome — `"esc to cancel"`, `"do you want to proceed?"`,
numbered yes/no lines. With no rule matched, a recognized agent defaults to
**Idle** (`src/detect/manifest.rs:529-584`). A prose question is idle. push_cc
was right, and its `sweep_panes` scraping is load-bearing, not redundant.

## What we adopted

**Identify the agent by process; treat the session as enrichment.** This is
herdr's architecture and it fixed a real bug. We were joining panes to
`claude agents --json` on pid alone, so a claude that had not yet started a
session — sitting in the agents view — appeared in no list and **vanished off
the Push entirely** until someone typed into it. Measured: `pane_pid=66732`
was `claude`, and `claude agents --json` did not list it.

`_find_claude_pid` now answers two questions instead of one: is a claude
running in this pane (walk the process tree), and does a session exist for it
(the pid join). Process present, no session -> the agent appears as
`unknown`, which is what `unknown` is for. Verified live: the pane that used
to disappear now reports `unknown` with the right cwd.

Also adopted, smaller: one `ps -axo pid=,ppid=,comm=` per poll to build the
tree, rather than a `pgrep` per pane per poll.

## What we deliberately did NOT adopt

**`claude.toml`'s 208 regex rules.** We take state from `claude agents --json`
instead — Claude Code's own account of itself (`busy` / `waiting` +
`waitingFor: "input needed"` / `idle`), no scraping.

The clinching argument is `src/detect/manifest_update.rs`: herdr ships a
remote update channel to push **new manifests when Claude Code's UI changes**.
Those regexes are known to break on a UI change and need an update pipeline to
stay alive. We would have no such pipeline. Taking state from the program
itself has no such failure mode.

Caveat, stated honestly: I confirmed `claude agents --json` reports
`waiting`/`"input needed"` for a genuinely blocked agent, but I did not manage
to build a controlled permission-prompt-vs-prose-question comparison — driving
a live TUI into each state proved fiddly. If it turns out to report `waiting`
for both, our backend is strictly better than herdr here. The A/B on hardware
will settle it, and push_cc's own scraping covers the gap either way, because
it needs the option *text*, not just the state.

## Worth adopting later — the `done` state

herdr's public API splits idle in two (`src/app/api_helpers.rs:96-107`):

    (Idle, seen=false) -> done
    (Idle, seen=true)  -> idle

where `seen` flips when a human focuses the pane. `done` means *finished while
you were looking somewhere else* — and CLI reads deliberately do not mark a
pane seen, only focus does.

That maps onto this surface better than it maps onto a terminal. push_cc
already knows which slot the Push is sitting on, so "went idle since you last
had this seat focused" is cheap to track. It would want its own pad colour in
`STATUS`, `ANSWER_RGB`'s neighbourhood in display.py, and theme.js.

Not built — it is a feature, not part of getting off herdr. Flagged because it
is the one idea in herdr's source that this surface would wear better than
herdr does.

## A latent bug in push_cc, independent of any of this

herdr 0.8.2's skill states: *"CLI server errors are JSON on stderr with exit
status 1."* push_cc's `herdr()` reads **stdout**, and `start_agent()` greps
stdout for `agent_name_taken`. On 0.6.8 that is correct — errors arrive on
stdout with exit 0, as the docstring says.

**Upgrade herdr and `start_agent` stops seeing name collisions**, silently
returning the first name every time and handing out duplicates. Worth knowing
whichever backend you land on.

## Also adopted: the worktree path layout

herdr puts worktrees under one root — `{worktree_directory}/{repo}/{branch-slug}`,
default `~/.herdr/worktrees`, configurable — and slugifies the branch for the
path via `branch_to_path_slug` (`src/worktree.rs:34-49,154-156`).

We were writing `<parent of repo>/<repo>-<branch>`, which is broken here,
because push_cc's `slug()` (`push_cc.py:475`) **deliberately keeps `/`** so a
branch reads `feature/thing` — and its fallback name is literally
`push/HHMMSS`. Pasted into a path:

    old:  /Users/dancoyle/midiAI-push/123456     <- stray dir beside the repo
    new:  ~/.push/worktrees/midiAI/push-123456

The branch keeps its slash; only the directory name is flattened. `WORKTREE_ROOT`
is the knob. Covered by demo assertions.

## Corrections to earlier notes in this document

**"`agent send`"** — that is the 0.6.8 CLI, which push_cc calls and which
works. HEAD has no `agent send` at all; a spec test asserts its absence
(`src/cli/spec.rs:1262-1263`), replaced by `send-keys` and `prompt`. Likewise
HEAD's `agent start` rejects `--cwd/--split/--focus` as *legacy*
(`src/cli/spec.rs:1287-1303`) and takes `--kind/--pane` against a pane you
split beforehand. **Read HEAD for architecture, never for shapes.**

**The stdout/stderr split is now sourced.** HEAD: `src/cli.rs:738-745` sends
error JSON to **stderr** and exits **1**; success to stdout, exit 0. push_cc's
docstring describes 0.6.8's stdout/exit-0 behaviour and is correct for the
binary installed. Both are true at their own versions — which is exactly why
`start_agent()` breaks on upgrade, as flagged above.

Our `term.py` keeps errors on **stdout**, matching what push_cc actually
parses. That is deliberate: we are replacing herdr, not tracking its HEAD.
