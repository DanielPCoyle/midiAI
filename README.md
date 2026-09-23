# push-cc — Ableton Push 2 as an AI command center

Eight agents on the top pad row, named on the Push's own screen, driven
without touching the keyboard.

## The surface

| Control | Notes / CC | Does |
|---|---|---|
| **The grid** | 36–99 | 64 prompt pads — insert canned text, no submit |
| **The grid, asked a question** | 36–99 | one pad per option down the left column, each its own colour — the prompt pads go dark |
| **Play** | CC 85 | enter — submits, to the focused agent · runs an armed chain |
| **Automate** | CC 89 | tap, then tap a pad: arms that pad's row as a chain |
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

Opening one in the app spells that swatch out: above the diff, the checks that
are still a question — failed, or still running — each a link to its own run,
with the rest as a count. A green check named in full says nothing the count
does not, and a repo with twenty of them buries the PR's own description.

**usage** answers one question — what is being spent — at three altitudes,
walked with its own view button or Left/Right: the plan's own limit bars, then
tokens by model, then output and context tokens per agent. Tokens, not money:
an invented cost is worse than no cost.

## The app

The UI is a React Native app (Expo) in `app/`, laid out for an iPad in
landscape: the rail of projects and agents down the left, the agent you are
reading in the middle, the prompt library down the right. The Push itself —
its screen, its two button rows, all 64 pads — is a **tab** you switch to, not
the shell you live in.

There used to be a header — brand, status pill, view tabs, buttons — and a
separate repo bar underneath it naming the checkout: two bands of chrome for
one fact and a half. They are one row now, carrying a status dot, `midiAI`,
the checkout as a chip (repo name and branch), the view tabs, the
**following the Push** toggle or a quiet `headless` label, the path from `~`,
and the `⋮`. The checkout became a chip rather than a band of its own because
it is a fact about what you are looking at, the same size as the rest of
them. Focus, guardrails and CI/CD are all answers about one repo and each
used to name it in its own heading, which was three drawings of one fact and
three chances to drift; they say their own subject now and the chip says the
repo. **Usage is the exception and gets no checkout chip**: spend is
account-wide, and a repo name over it would be claiming those were this
repo's tokens.

The status pill itself is gone, but its sentence is not. The pill's only real
content was *why* push_cc is not answering, and that sentence still appears
in the band — now only when push_cc is unreachable, because a red dot that
cannot say why is worse than no light.

Two tabs are drawn under a wider name than the view key beneath them, in
`TAB_LABEL`: `tests` reads **GUARDRAILS** and `prs` reads **GIT**. The keys
are a wire contract — `VIEWS` in `push_cc.py`, and the `views_data` the Push
is drawn from — so only the word changes, never what anything is looked up by.
That word was CI/CD until the tab grew a working tree; a `git status` is not
CI, so the wider name had to get wider still.

GIT carries four sub-tabs in its own title row rather than a second band of
chrome: **work**, **pull requests**, **actions** and **hooks** — the life of a
change, in order. Hooks is still a drawing of `.git/hooks` — it lists the
*uninstalled* ones too, because "no pre-push here" is the answer to why nothing
stopped a broken push and an empty list cannot say it.

**Work** is the one that never leaves this machine, and the tab opens on it.
Every other sub-tab is a question about a server — what CI said, what a
reviewer said. This one is the checkout in front of you: the branch and how far
it has drifted from its upstream, the files that changed, the commit graph, and
the commands that move things between them. It splits with the same segmented
switch:

| | |
|---|---|
| **changes** | Top to bottom: a sync bar (the branch, which switches it; how far ahead or behind; Fetch · Pull · Push as one group, the one with work to do lit green; Stash), then the commit box where you write -- above what it commits -- with Write with AI, Amend and Commit on one row, then the files beside the diff. Files are Staged over Changes over Conflicted, each a row with git's letter, the path (folder quiet, name bright), its `+/−` from `git diff --numstat`, and icon keys to stage, unstage or discard; each section carries its own Stage all / Unstage all / Discard all, and the stashes sit at the foot of the list. Drawn in Claude Design first, direction A of three. |
| **tree** | Every branch as a GitKraken-style graph: a coloured lane per branch, a dot per commit (hollow for a merge), and pills for local branches (filled), remotes (☁) and tags. `mapui.graph_lanes` lays the lanes out from `git log --all --topo-order`'s parents and the app only draws lines, as rotated Views, so it needs no SVG library; `python3 test_graph.py` is its gate. Picking one shows its patch in the same viewer a file's changes use — the same question asked of a different range. |

The diff viewer is the pull-request review's, reused whole, and so is the
parser behind it: a patch is a patch, and a second renderer would only be a
second thing to drift.

The commands are verbs from a table the server owns — `stage`, `unstage`,
`discard`, `commit`, `fetch`, `pull`, `push`, `stash`, `stash-pop`,
`stash-drop`, `branch`. Nothing composes a command line, every path rides after
a `--`, and no verb reaches a shell; a branch name, a stash ref and a sha each
have to match their own pattern before git sees them. This server binds to the
LAN, and "run git for me" is the one route on it that would otherwise be a way
to run anything.

Discarding and dropping a stash are the two commands here with no undo — git
keeps no reflog for a change that was never committed — so both wait behind a
confirm that says exactly that. Everything else is git's own refusal, passed
back verbatim: "your local changes would be overwritten" says more than a code
of ours would.

Actions splits again, one level further down, and the third level gets a third
idiom: the top tabs are tracked-out capitals, the CI/CD row is words in the
title, and this is a **segmented switch**. Three levels that all looked alike
would be three nobody could tell apart. It asks the same question wherever it
appears — what happened, or what is configured:

| | |
|---|---|
| **runs** | Workflow runs and their jobs. Still drawn from constants; says `mock` in the heading. `gh run list` once the shape is agreed. |
| **manage** | The real `.github/workflows` in the checkout — read, edited and written back. |

**Pull requests** splits the same way — **open** is the list, **manage** is the
files that shape a PR without being one: `PULL_REQUEST_TEMPLATE.md`,
`CODEOWNERS`, `dependabot.yml`. Each is looked for at every location GitHub
itself honours, first-existing wins, so a repo with `CODEOWNERS` at the root
is never offered a `.github/CODEOWNERS` that would silently win over it. A
missing one is the row worth reading — no CODEOWNERS is *why* nobody was asked
to review — so it lists hollow rather than not at all, and opens with a starter
that already says the thing the file exists to say.

Guardrails splits too: **overview** and **tests**. Overview is a checklist of
what is meant to stand between an agent and main, grouped by SDLC phase — plan
constrains intent, design constrains architecture, implement constrains
actions, test verifies independently, deploy constrains authority, maintain
detects drift — plus six cross-cutting controls. Every item carries two fields
and they are not the same field: **the guardrail** is the claim, and **how to
validate it** is how you find out whether the claim is true. The second is the
one that matters; a checklist of assertions nobody can check is the thing it is
pretending to protect against, so each validation names a command, a file, or a
failure to induce deliberately and watch. Items can be added, edited and struck
out, and the phases themselves renamed, reordered, added and removed — the
starter list is a framework, not this repo's opinion of itself.

Only the *difference* from what ships is stored. A `custom` entry sharing a
shipped item's id shadows it, which is what makes editing a shipped guardrail
possible without keeping a copy of the whole list per repo, and what lets
"revert to shipped" simply drop the override. Untouched phases mean "whatever
the app ships", so a team that never edits them keeps getting new ones as the
framework grows; only taking ownership stops that. A phase holding guardrails
will not delete — removing it would file them under a tab that no longer
exists, and anything that does arrive orphaned still draws, under a section
saying so. Search reads the validation text too, because "which of these
mention gitleaks" is the question you actually arrive with.

Guardrails are reordered by dragging a row's grip within its phase: the
record keeps an `order` of ids, and anything not in it (a new one, or one a
later build ships) keeps its natural place after those that are. Dragging
is off while a search is up -- a filtered phase hides the rows you would be
placing it between. A template keeps its order, and applying one brings it.

**Save as template** puts the list somewhere every project can reach it
(`~/.midiai/guardrail-templates.json`). A template carries the guardrails and
the phases, and deliberately **not the ticks** — which controls a team holds
itself to travels between repos, and whether each is actually in force is a
fact about one repo that would be a lie anywhere else. Applying one replaces
the item set behind a confirm that names what it costs; ticks survive by id, so
re-applying a list you already follow is a no-op on your assessment rather than
a reset of it. Anything the template carries that this build no longer ships —
an edit, an addition, a retired starter — is kept from the template's own copy,
so a template outlives the list it was made from.

Per-checkout state lives in `~/.midiai/guardrails.json`, deliberately outside
the repo: a half-ticked framework committed to someone's tree reads as a claim
nobody agreed to.

Manage lists every workflow with the two facts that identify it, what fires it
and what secret it needs, and opens one into its own YAML. **＋ new workflow**
scaffolds from four templates in the shape the repos here already use: the
banner header (what it does, what secret it needs, what a fork without that
secret sees), `concurrency` on anything a rapid push can start twice, a
`check-secret` gate ahead of any job needing a secret so a fork skips cleanly
instead of showing a red required check nobody can fix, and action majors
pinned rather than floating. They live in `app/src/workflows.js`.

Writing one goes through `POST /workflow`, which takes a **name and never a
path** — `WORKFLOW_NAME_RE` is the containment check, the same bargain
`skill_path` drives, and `.github/workflows` is named by the server. `POST
/govern` is tighter still: a key from a fixed table, so there is no open-ended
part at all. Both land in one `_write_repo_file`, because those four lines are
the whole security boundary and two copies of them is two chances to fix only
one. An existing file is a 409 unless the editor sends `replace`, so a new
workflow cannot silently take the name of a pipeline someone is relying on.

Guardrails and CI/CD both describe a **checkout**, not an agent, so both read
a worktree picked in the rail with nobody in it. That is what `place` falls
back to when no agent is focused. Only **focus** still stands aside for the
no-agent panel below — it is the one view that needs somebody to talk to.

The right-hand column is **focus's own panel**: guardrails, CI/CD and usage
are read, not typed into, so a column of things to say to an agent would be
furniture with nothing to fire at on any of them, and it stays off screen on
those views. A question on the glass takes it too — the answer pads are what
that moment is for.

At rest it is a 56px strip of three keys — **prompts**, **skills**, **hooks**
— each drawn as an icon with a count badge, and a badge appears only when the
count is non-zero: a faint "0" and a faint "3" read the same at a glance.
Pressing a key opens the full 344px panel; the panel is dismissed by a
chevron in its own header. Collapsed is the resting state at every width
now — it used to default open above `wide` (1200) on the grounds that there
was room for both, and there was, but the transcript wanted the width more.
The `⋮` that used to sit in the pane's own title row and pick between the
three tabs is gone with it: a third and fourth key across a title row was how
a title row stopped being readable, and the panel is governed from the panel
now.

A new module `app/src/Icon.js` is the single place a meaning becomes a
glyph — call sites say `prompts` or `mcp`, never a Feather name. It wraps
`@expo/vector-icons`'s Feather set, which was already a dependency; nothing
was added. It draws the strip's three keys, its own collapse chevron, and the
rail's MCPs footer row with its down-count. The view tabs deliberately stayed
TEXT: an icon-only tab bar is something you have to learn before you can
navigate, and four glyphs that all mean "a view of this checkout" is exactly
what nobody learns.

Prompts are what you send an agent; skills and hooks are what it already has,
read off disk by `GET /catalog` and split by **scope** into sub-tabs: project,
project · local, global, plugins. Where a thing comes from is the first fact
about it — a hook in the repo is the team's, one in `~/.claude` is yours — and
131 plugin skills over 38 of your own is not a list you scroll looking for one
of the 38.

Every tab has a `＋` in the same place, and every row it lists opens for
editing. `＋ prompt` fills in the first empty pad on the page; `＋ skill` and
`＋ hook` open one sheet that does both, because they are the same errand
twice — pick a scope, fill in two or three fields, save. A plugin's skill is
not editable and its row says so by not being a button: it belongs to
something installed, and editing one in place would be undone by its next
update without saying so.

**The client never names a path.** `POST /skill` takes a scope and a name and
derives the file itself; the name has to match `[a-z][a-z0-9-]{0,63}`, which
*is* the containment check — it admits no separator, so there is nothing for a
`..` to traverse from. A path taken from the caller and then inspected is the
version of that which keeps being wrong, and this server is one `--lan` away
from the network. Deleting a skill removes its `SKILL.md` and the directory
only if that leaves it empty; a skill with scripts or a `references/` beside it
is a small project, and a button in a side panel is not where anyone means to
delete one.

A plugin's skill opens too, read-only: to be read, to be **opened in the
editor** — which runs where the agents do, not where you are looking, because
the app may be a tablet and the files are over there — and to be **copied**
somewhere it becomes yours. Copying is what a plugin skill does instead of
moving: taking it would break the package and be undone by its next update
anyway, and the reason to reach for one is to have your own version. Your own
skills move rather than copy, between global and any project this machine
already knows about. That list is the whitelist: a scope and a name derive the
file everywhere else, and `/skill/move` is the one call that takes a directory,
so it takes one off the projects list rather than out of the request.

Hooks live in files that are not ours — `settings.json` also holds
permissions, env and whatever else you keep there — so a hook edit reads the
file, changes the one entry, and writes it back whole, with the previous
contents to a `.bak` beside it every time. A settings file that is not valid
JSON is refused rather than rewritten. A row is addressed by `(event, group
index, entry index)` from the catalog, not by its command text, because
matching on the text edits the wrong one the moment two of them agree; and the
matcher belongs to the group, so an existing hook cannot be moved between
groups by retyping it. All five hook types are writable — `command`, `http`, `mcp_tool`, `prompt`
and `agent` — each with its own fields, plus the shared `timeout`, `if` and
`once`. The server keeps a **whitelist per type**, which is what makes writing
one from an HTTP request reasonable at all: the caller says which type, and
only that type's own fields reach the file. A key nobody here has heard of is
dropped rather than passed through, and a `timeout` of `"soon"` is refused
before it becomes a settings file Claude Code will not boot from.

A hook's **name** is its `statusMessage` — a real documented field, and the
spinner text shown while the hook runs, so it earns its place twice. The
**description** has no home in the schema at all. Unknown keys do survive
today (measured: a session ran with an invented `description` key and the hook
still fired), but settings.json failing to load because a future version got
stricter about a field we made up is not a trade worth making for a note. So
descriptions live in `~/.midiai/hook-notes.json`, filed under what the hook
looks like rather than under its group and entry indices — those renumber the
moment a sibling is deleted, which would hand one hook's description to
another. A note follows an edit that changes the hook, and is deleted with it.

### Turning things off

Three things you would expect to work the same way do not, and the panel says
so rather than pretending otherwise.

**Skills have four states, not two** — `skillOverrides` in settings, one of
`on`, `name-only`, `user-invocable-only`, `off`. The middle two are the useful
ones: a skill you still want to reach by `/name` but never want reaching for
you. Absent means `on`, so turning one back on *deletes* the key rather than
writing the word, and the file stays a list of the decisions actually made.
Written to `.claude/settings.local.json` by default, which is where the
built-in `/skills` menu puts it and where a decision about your own checkout
belongs — it is the file git does not carry, so turning something off for
yourself does not turn it off for everyone who clones the repo.

**Plugins are a documented boolean** — `enabledPlugins`, keyed
`name@marketplace`, and they get their own tab. That tab exists because
`skillOverrides` explicitly does not reach a plugin's skills: for those 93
rows the only switch there is is the plugin's own.

**Hooks have no off switch at all.** The one documented control is
`disableAllHooks`, which takes every hook, the custom status line and the `@`
file suggestions with it. So this one is ours: turning a hook off lifts the
whole entry out of the settings file into `~/.midiai/hooks-parked.json`, and
turning it on puts it back into the group whose matcher it had. The panel keeps
listing it, dimmed — a hook you cannot see is a hook you write a second copy
of. The cost, said plainly because it is real: **a parked hook is invisible to
anything that reads settings.json.** Only this app knows it is there.

The event field is an autocomplete over all
thirty-three of Claude Code's hook events, in the order they happen rather than
alphabetically, each with what it actually fires on. The names alone are a
quiz: `Stop`, `StopFailure` and `SubagentStop` are guesses apart, `PostToolUse`
/ `PostToolUseFailure` / `PostToolBatch` are three different moments, and
`PreCompact` says nothing about when compaction happens. It matches on the name
first and only falls through to the descriptions when nothing is named that —
at thirty-three events a single letter matches most of the prose, which is a
list rather than a narrowing. Offered, not enforced: the list is a snapshot of
a tool that keeps growing one.

The prompts tab is the **prompt library**: the pads as a searchable list,
grouped by their colour labels, with **run** and **edit** on whichever one you
have picked. It used to carry a `Library | Grid` toggle whose second half drew
an on-screen 8×8 replica. That replica was the Push mirror, worse — a second
drawing of one grid, which is the thing this codebase refuses to have — so it
is gone, and with it `PadGrid.js`, the drag-to-swap it hosted (the Push's own
move mode does that), and the pager it needed. A list can say what a lit
square cannot, which was always the reason the panel is a list.

The panel does not appear at all when no agent is running, nor does the
strip that opens it. Every pad fires into "whichever session the
Push is pointed at"; with nothing running there is no such session and the
server answers a fire with `409 no session selected on the Push`. A panel
whose every button is a guaranteed error is not worth the width.

The pad editor is a sheet, not a column — an **edit pad** key appears once you
have picked one, and the editor was holding a third of the screen open next to
the thing you were reading.

Under the focus title, one line used to name the checkout and the branch you
were actually typing into — two agents in one repo is the normal case here,
and on two different branches was why the line existed rather than the repo
name alone. That fact moved to the band's own checkout chip, which names both
for whichever agent or worktree is focused; what is left on this line now is
the one fact that is about this agent rather than its checkout — which of its
faces (pretty, terminal, subagents) you are looking at.

The tabs are the views, uppercased so the one row of chrome reads as chrome:
**FOCUS · GUARDRAILS · GIT · USAGE**. `tests` reads GUARDRAILS because the
view is everything meant to stand between a change and main, not the test
files it lists today, and `prs` reads GIT because the view is the repo's
state and PRS was the one label that had to be decoded. `sessions` and its
subagents split are no longer tabs — the rail's tree answers "who is running"
without a click and is on screen the whole time. The view names are a wire
contract (`VIEWS` in `push_cc.py`, a `views_data` key) and are untouched: only
the word drawn changed, the tab index a `/press` carries is still the view's
own index, and the Push still has every view it had.

Top right is a **⋮**, holding reconnect, Push mirror and the host field.
Those three used to sit in the header row and drop out of it one at a time as
it narrowed — the host field above 1200 only, the mirror above 820 — so two of
the three things you reach for when something is wrong were the two the width
took away. Behind the ⋮ they are there at every width, and the row keeps what
you read rather than what you press.
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
the routes below. `probe_work.py` drives the three `/work` routes on a spare
port, reading this checkout and writing only to a repo it makes for the
purpose — `discard` and `stash drop` are real commands, and a gate that ran
them here would run them over whatever anyone had uncommitted.

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
| `GET /projects` | every remembered repo, worktrees attached |
| `POST /projects` | `{path}` — remember a repo; any path inside it will do |
| `POST /projects/remove` | `{path}` — forget one; nothing on disk is touched |
| `GET /branches?cwd=` | local branches, each naming the worktree that holds it |
| `GET /work?cwd=` | the working tree in one call — branch, upstream, ahead/behind, staged, unstaged, conflicted, stashes, and the commit graph |
| `GET /work/diff?cwd=&file=&staged=&sha=` | one file's patch or one commit's, as git's own text; an untracked file falls back to `--no-index` so a new file still reads as a diff |
| `POST /work/do` | `{cwd, verb, files?, message?, branch?, ref?, amend?}` — one of eleven verbs, never a command line |
| `POST /worktrees/switch` | `{path, branch}` — check another branch out in a worktree |
| `GET /catalog?cwd=` | the skills and hooks an agent there can reach, each tagged with its scope |
| `GET /skill?scope=&name=` | one skill's description and instructions, for the editor |
| `POST /skill` | `{scope, name, description, body, replace?}` — create or replace |
| `POST /skill/delete` | `{scope, name}` — its SKILL.md, and the directory if that empties it |
| `POST /hook` | `{scope, event, matcher, type, …, name?, description?, gi?, hi?}` — append, or replace that row |
| `POST /hook/delete` | `{scope, event, gi, hi}` |
| `POST /skill/move` | `{name, scope, to, to_cwd?}` — global ↔ a project; a plugin's is copied |
| `POST /open` | `{path}` — open that folder in the editor on this machine |
| `POST /skill/state` | `{name, state}` — `on` / `name-only` / `user-invocable-only` / `off` |
| `POST /hook/toggle` | `{scope, event, gi, hi, on:false}` to park one, `{on:true, parked}` to bring it back |
| `POST /plugin/toggle` | `{key, enabled}` — writes `enabledPlugins` |

`/prompt` without a `terminal_id` goes to whichever agent the Push is
pointed at, which is the same target a pad fires into: one place decides what
"the current agent" means, so a tap and a typed sentence cannot disagree.

A name is `[a-z][a-z0-9_-]{0,31}` and unique among live agents — herdr's rule,
kept because it was already the right one. It lives in a tmux pane option
rather than the pane title, which claude overwrites with its own.

An agent with no name reads by the basename of its directory, exactly as
before. Renaming is a convenience, not a requirement.

### The rail is one tree

It used to be a **PROJECTS** tree over an **AGENTS**/MCPS tabbed section, each
capped at 30% of the window height — two capped scrolls inside one scrolling
rail, and the relationship that actually matters, which worktree a given
agent is living in, was drawn nowhere: you read a card's cwd and matched it
against the tree above by eye. Before that there was a `worktrees…` sheet
behind an agent's menu instead of a PROJECTS tree at all. Now repos open onto
their worktrees and each worktree carries the agents living in it, so
placement is the tree's own structure rather than something you reconstruct.

Projects are a **server-side list**, `~/.midiai/projects.json`, and `GET
/projects` returns each one with its worktrees already attached — one call, not
one per repo. It was derived in the app at first, from the live agents' `cwd`s,
and that could only ever show you what you were already doing: a repo with
nothing running in it did not exist as far as the wire was concerned. Now
running an agent somewhere adds it, `POST /projects` adds one you are not in
yet, and only `POST /projects/remove` takes one away. A path anywhere inside a
repo is stored as the record git lists first — the main checkout — so adding a
worktree and adding its repo are the same act, and two agents in one repo
collapse to one entry.

The one rule that stays in the app is which worktree an agent is *in*: a seat
is placed in its **longest** matching worktree, not its first. This repo keeps
its own worktrees under `.claude/worktrees/`, inside the main checkout, and a
first-match rule files every one of them under the checkout instead.

A collapsed repo still answers "is anything blocked in here" without opening
it: its row draws one status dot, worst-first across every agent nested
underneath — blocked beats working beats done beats idle beats unknown — so a
collapsed tree still tells you where to look.

Per row: a linked worktree is marked `↳`, the checkout is not. One with an
agent shows that agent's status hue and taps through to its seat; one without
**selects** it, and the pane draws the panel below rather than starting a
claude on the spot — one tap on a list is not enough intent to spawn a
process. An empty worktree still draws, marked `empty` rather than dropped
from the tree — selecting one nobody is in is still how you pick a checkout
to read guardrails or CI for. The `⋮` on the row holds *switch branch*,
*close agent* and *close + delete worktree*; the parent row carries a `＋` for
a new worktree and a `×` to forget the project.

`GET /branches` says which branches another worktree already holds, because git
will not check one out twice — so a branch that could only produce that error
is drawn as the fact rather than offered as a button. There is no dirty check
of ours: git refuses a checkout that would lose work and carries changes over
when it would not, and its own sentence comes back as the error.

An agent nested under its worktree carries four keys rather than a menu, but
only when it is the **selected** agent: **✎** rename, **⊟** `/compact`, **⊘**
`/clear`, **×** close. Four keys on every nested row at 268px is not a row
anyone can read, so everyone else keeps just the hue, the name and the
model — the same as the selected row shows before you have picked one.
Compact and clear are *typed*, not called — they are Claude Code's own
commands with no API behind them, so they go down the same pty a pad fires
into. Clear arms before it fires: it throws away everything the agent knows
and it is a 20px target in a 268px column, so one tap reddens it and the next
does it.

A nested row has no width left for a third fact once the name and model are
drawn, so an agent's subagent count no longer rides along on its own row —
reading it per seat would mean globbing and parsing every agent's transcript
on every 400ms poll regardless of whether anyone is looking. It reads now
under the agent itself, in the focus view's own `subagents · N` sub-tab, for
whichever agent you are currently looking at. Tapping a row there does what
yes does on the Push — points focus at that subagent's transcript — without
the question, since a tap is not a brushed pad; `subagent · ‹ back` in the
header returns to the agent. While any are running, a banner at the foot of
the pretty view says how many and opens that tab.

The usage view's Codex bars come from the newest `~/.codex/sessions`
snapshot, which is only as recent as the last Codex run. A window whose
`resets_at` has passed is dropped rather than drawn, so an account untouched
for weeks shows no bars instead of its last session's percent. Every bar with
a reset time says when, as a countdown and a clock.

Eight seats is few enough to read at a glance and too many to read while you
are working in one repo of three, so the rail's own header row carries
**all · here** — `here` being the worktree you picked in the tree, or the one
the focused agent is in. It is never the default: hiding agents by default is
how you lose one. Filtered, never renumbered — `i` is still the seat index a
`/press` carries, and the Push's eighth button is still the eighth seat.

MCPS left the AGENTS tab pair it used to share and became a pinned **footer
row** instead — there is no tab pair left to share, since the tree is AGENTS
now and always on screen. The footer holds an MCPs row and `＋ new agent`,
both outside the body scroll. The MCPs row carries the count of servers that
are down, in red, and only when at least one is — the same zero-is-not-a-
warning rule as everywhere else. Pressing it swaps the rail's body between
the tree and the MCP list and reads as selected while the list is showing, so
there is a way back. All the MCP machinery — health checking, scope tabs, the
add form, per-server actions — is unchanged; only the way you reach it
changed. `＋ install an MCP` is pinned in the footer too, above the MCPs row:
installing one is what you came for when the list has not got it, so it
cannot sit underneath however many servers the list happens to have. It was
previously kept above the fold by capping the list's height, and that cap
clipped the list while leaving the rest of the rail empty beneath it — worth
recording, because it is exactly the kind of fix that trades one bug for
another.

Both 30vh caps are gone now too. Whichever body is showing — the tree, or the
MCP list — takes the whole body scroll. The header (search, the `all / here`
scope toggle, `＋ add project`) and the footer both stay outside that scroll,
same as they always did: an action or a "show more" buried below a long list
is one nobody can reach.

### No active agent

Pick a worktree nobody is in and **focus** says so by name, with **＋ new
agent**, **close** (stop looking at it) and **close and delete worktree**.
Guardrails and CI/CD are unaffected and keep reading that checkout — they used
to be replaced by this too, which made picking a repo just to look at its tests
or its pull requests impossible. GUARDRAILS' count goes quiet while a pick
is up: it is read off the Push's own focused agent, so beside a picked
worktree it would be counting a different checkout. GIT's count is files
changed and not committed (`/work/dirty` -- staged or not, untracked
included, a file counted once) in whichever checkout is in view, picked or
focused, so it stays. It
is also where you land after closing an agent, which otherwise left you staring
at whichever seat the Push happened to be on with no sign of what you had just
emptied. A dirty tree comes back as git's own refusal, with a second key to
force past it once you have read it.

In the browser, **＋ add project** is the Mac's own Finder folder chooser and
nothing else: the folder you choose is added, no sheet in between. The
dialog is brought to the front (`activate` first) -- it used to open behind
whatever you were looking at, and every nonzero exit was reported as a
cancel, so the button seemed to do nothing. A real failure now shows under
the button; only error -128 is a cancel. On the iPad it is still the sheet
below, because the Finder window would open on the Mac across the room.

A project does not need a repository. A folder with none is kept as a
plain project: one checkout, labelled **no repo**, no new-worktree ＋ (a
worktree needs a repo). The GIT tab wears a **⚠** instead of a count
(`/work/dirty` answers `git: false`), and opening it offers `git init` --
a first branch name, and on by default a first commit of what is already
there (`/work/init`). That route only acts on a folder the project list
holds; mapui can be bound to the LAN. With a folder picked in the rail,
the views read the pick rather than the focused agent's repo -- the GIT
badge already did, and the two used to disagree.

**Add project** browses the disk; **new agent** does not. The folder picker
used to live in the new-agent modal, which put the same question — where is
this repo — in front of you every single time you started one. Adding the
project answers it once, and new agent picks from what has been answered. The
path field is still there and still editable, because a path you can type is
never a dead end, which is what the first run needs.

Walking and choosing are two gestures now: a row's name walks into the folder,
**select** takes it, and selecting folds the list to one line with **remove**
on it — the folder list was holding 190px open for a question already answered.
Where the folder is already a repo, both the add-project and new-worktree
sheets offer its branches: adding a project can check one out on the way in,
and a worktree can be made from a branch that already exists rather than only
from a new name.

## Chains

A **row is a chain**. Tap **Automate**, then tap a pad: everything from that pad
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

## Context bars

Every mode of the Usage tab opens with a **CONTEXT** strip: each seated
agent with five phone-style bars, lit from the left in proportion to how
full its context window is right now (`push_cc.usage_fill`, the same
`context_for` the focus view's context bar reads), and the percentage. More
bars is worse here, so the colour says so -- the plan bars' ramp, green to
red at 60% and 85%. It rides beside the usage rows rather than inside them:
`display.py` unpacks those as exactly five fields for the Push's own screen.

## Commit messages from the diff

Beside the commit key in GIT › Work, **✨ write it** drafts a message:
the staged diff if anything is staged -- that is what the commit will hold
-- else every uncommitted change, untracked names included, with a line
saying so because the commit key still only takes what is staged. The
repo's last twelve subjects go in as the style to match. It is Haiku through
the same isolated `claude -p` the memory summaries use (`/work/ai-message`,
diff capped at 30k characters). It only ever fills the box: nothing is
committed until you press commit. `test_commit_message.py` is its gate.

## What you asked, pinned

The pretty conversation keeps a prompt pinned above it, **YOU ASKED**, like
a sticky section header: at the foot it is your latest prompt, and scrolled
up it is whichever prompt the top of the view falls inside -- the question
the answer on screen belongs to. Tap it to jump to that prompt. Up to 160 characters it shows as typed. Past that
it is one line from Haiku (`/summarize-prompt`, the same isolated `claude
-p` the memory summaries use), cached by the prompt's text on both sides.
The first call can take ten seconds or more, so until it lands the pin
shows the prompt's own opening words, and a prompt is only summarised once
it has stayed pinned for about a second -- scrolling past twenty long
prompts is not twenty model calls. `test_summarize_prompt.py` is its
gate.

## Subagents

The focus view's **subagents** tab lists what the agent in focus has
dispatched and is still running, each with the model it ran on -- a
subagent that has returned leaves the list and the tab's count, folded into
one "N finished · show" line so its conversation can still be opened. **view ›** opens one in the
focus view itself: its own conversation (read from
`<session>/subagents/agent-<id>.jsonl` through the parent -- every line of
that log is `isSidechain`, which `history(path, sidechain=True)` reads
rather than skips), its model and type read-only, and no composer, since a
subagent takes no input. **‹ back** returns to the parent.

A running subagent's model was fixed when it was dispatched; what can
change is the next one. **NEXT DISPATCH** sets `model:` in the type's
definition file (`<repo>/.claude/agents/<type>.md`, else
`~/.claude/agents/<type>.md`), one frontmatter line, the rest of the file
untouched. Built-ins have no file and plugin agents would be overwritten by
their next update, so neither can be set here. `test_agent_def.py` is its
gate.

## Memory

midiAI keeps its own memory of every session, in place of the claude-mem
plugin: `~/.midiai/memory.db`, SQLite with FTS5, stdlib only (`memory.py`).

- **Capture needs no hooks.** Claude Code's transcripts already hold every
  prompt, reply and tool call, so `mapui`'s memory loop sweeps
  `~/.claude/projects` once a minute and indexes whatever grew since the byte
  offset it last stopped at. claude-mem's own worker transcripts
  (`*claude-mem-observer*`) are skipped.
- **Summaries** are one per session, written by Haiku once a session has sat
  idle for ten minutes -- at most one per tick, and only for sessions active in
  the last two days. The call is `claude -p --no-session-persistence
  --setting-sources "" --strict-mcp-config --tools ""`: no transcript, so a
  summary never gets summarised, and none of your hooks or plugins fire.
  `MIDIAI_MEMORY_SUMMARIES=0` turns them off.
- **claude-mem's history** was imported once (`python3 memory.py
  import-claude-mem`, read-only on its db, safe to re-run) as `observation`
  and `summary` entries tagged `claude-mem`. The 2.2 GB vector store was not
  -- search here is full-text.
- **Agents get it back two ways.** A SessionStart hook (`memory.py context`)
  injects a short digest of the project's recent summaries, and the
  `midiai-memory` MCP server (`memory_mcp.py`, user scope) gives them
  `memory_search`, `memory_recent`, `memory_get` and `memory_session`.
- **You get it** in the right-hand column's **memory** tab: search this
  project or everywhere, tap a row for the whole entry.

Gates: `test_memory.py`, `test_memory_mcp.py`, `test_memory_routes.py`.

## Slash commands

Start the composer with `/` and a menu opens above it: Claude Code's own
commands, then every skill the catalog can see for this checkout, filtered as
you type (a plugin skill matches on its bare name and is filled in as
`plugin:skill`). ↑/↓ choose, Enter runs the highlighted one, Tab or a tap
fills it in so arguments can follow, Esc closes it. The built-ins are a
hand-kept list in `app/src/Slash.js` -- nothing readable lists them -- and a
stale one costs nothing, since Claude Code answers an unknown command
itself. A command sent to a busy agent queues like any other prompt.

## Up next

Send to a working agent and the prompt goes into its queue rather than into
Claude Code: the send key turns into *add to queue*. The queue is held by
`mapui` (`~/.midiai/queue.json`, `/queue`), which sends the top one each time
the agent goes idle -- whichever agent the app is showing, or with the app
closed. Until one goes it is still yours: it is the fourth tab of the
right-hand column, beside prompts, skills and hooks (**UP NEXT** under the
composer opens it), where a message is dragged by its grip to a new place,
edited in place, played next, or removed (the arrows do the same moves
without a drag). A sidebar and not a modal, so the conversation stays
readable while you rearrange what comes after it.

It holds back while a question is on screen or something is half-typed on the
agent's input line, and waits `QUEUE_GAP_S` between sends, because herdr still
reads idle for a moment after a submit. Claude Code's own queue -- messages
typed at the keyboard while it was busy -- is a different thing, shown
read-only: a pty cannot reach back into it. `python3 test_queue.py` is its
gate.

## Notes

- `tmux list-panes -a` lists a pane once per session that can see it, and
  ptybridge's `midiai-<session>` view is a grouped session sharing every
  window. So with the terminal tab open, every agent came back twice -- two
  of each in the rail and the seat tabs. `term._match_agents` keeps one per
  pane. Anything else that walks `list-panes -a` and counts what it finds
  has to do the same.
- `{text && <View/>}` with an empty string renders the `''` itself, and
  React Native will not have a bare string inside a View. The page looks
  right; the console fills with `Unexpected text node: . A text node cannot
  be a child of a <View>` on every render (the `.` is the message's own full
  stop after nothing). Write `{!!text && ...}`, as the rest of the app does.
- The view (focus, guardrails, git, usage) is push_cc's, not the page's. A
  test browser that clicks a top tab moves the Push and every other open
  app with it -- so a check that visits GIT should switch back to focus
  straight after, or someone typing on the iPad loses their screen.
- `/relaunch` starts `push_cc` with *mapui's* interpreter. Start mapui with
  `.venv/bin/python`; start it with a bare `python3` and the relaunched
  `push_cc` dies at `import mido` while mapui keeps answering as if all were
  well. And its `pkill -f push_cc.py` matches any process whose command line
  holds that string -- including the shell that asked for the relaunch.
- The focus info the app gets carries the agent's `tid`. It once did not, and
  nothing failed loudly: drafts shared one key, `/history` was never asked,
  and the queue polled `terminal_id=undefined`.
- A `\r` typed with `send-keys -l` does not submit. Claude Code takes it as a
  newline in the input, so a prompt from the app sat in the agent's box,
  typed and never sent, with nothing reporting an error. `term.py` sends each
  `\r` as tmux's named `Enter` key and keeps the text around it literal.
- The pretty view's conversation comes from the session transcript
  (`/history`, built by `push_cc.history`), not the pane. A scrape is one
  screenful whatever `SCRAPE_LINES` says, so the view used to drop everything
  that had scrolled off. The pane is still what says what the agent is doing
  *now* -- the log is written a block at a time. The transcript's folder is the
  cwd with every non-alphanumeric turned into `-`, dots included; swapping only
  `/` found no log for any agent in a `.claude/worktrees` checkout.
- Claude Code's suggested next prompt sits on the input line looking exactly
  like typed text once the styling is gone: `❯\xa0` then the words, dim
  (SGR 2). A plain `capture-pane` drops the dim, so the app adopted every
  suggestion as if you had typed it. `term.py` captures the bottom of the pane
  a second time with `-e` to spot it, and `push_cc` sends it as `suggestion`,
  never `pending`. The composer shows it as a placeholder: type to replace it,
  send an empty box to use it. The herdr backend has no styled read, so there
  a suggestion still arrives as `pending`.
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
- A re-run does not replace the run it re-ran. `statusCheckRollup` carries
  every attempt, so a fixed PR keeps its old failure in the list forever — 37
  entries where GitHub's own page shows 18. `check_rows` keeps each check once,
  at its newest attempt, and `check_state` reads the colour off those rows
  rather than the raw rollup, so the dot and the list cannot disagree.
- MCPs are listed from the config files and health-checked separately, because
  the list is a file read and `claude mcp list` is nine seconds of starting
  every stdio server and speaking to every remote one. The check runs behind a
  two-minute cache on its own thread, the way `open_prs` runs behind the PR
  list; until it lands a server draws grey, never red. Two name traps live
  here: the CLI prints a plugin's server as `plugin:<plugin>:<name>` where
  `mcp_rows` calls it `<plugin>:<name>`, so health is asked under both
  spellings and answered under ours; and a plugin can declare `mcpServers`
  inline in `.claude-plugin/marketplace.json` with no `.mcp.json` anywhere,
  which is how the one server that was actually failing became the one server
  the rail could not draw.
- Disable is only offered where Claude Code has a switch. A project
  (`.mcp.json`) server goes in `disabledMcpjsonServers`; a plugin's server
  goes off with its plugin. A user or local server has no such key — the only
  way to stop one is to remove it, and a Disable that quietly did a Remove
  would be worse than not offering it.
- The pretty view reads as prose because `reflow` puts it back together first.
  `capture-pane` hard-wraps every line to the PANE's width -- 39 columns in a
  split -- so one sentence arrives as four lines, and drawn a line at a time
  it became four paragraphs. Wrapped lines rejoin; only a blank line, a bullet,
  a numbered item, a heading, a fence, a table row or a short shouty line
  (TLDR, NEXT) actually breaks. Those last three also CLOSE, or the line after
  a heading glues onto it. It lives in `app/src/reflow.js` with one runnable
  check beside it -- `node app/reflow_check.mjs`, no runner, importing the same
  function the app does so the rule cannot drift from its test.
- What the agent is doing NOW sits at the foot of the conversation, not in the
  title bar: a dot that moves, Claude Code's own `✻` progress line (which
  already carries the elapsed seconds and the token count), and the tool call
  it is inside. That last one is the only terminal detail the pretty view shows
  on purpose -- everything else stays behind the work receipt. The work
  collected after the last finished answer used to be dropped, because no `⏺`
  ever came to close it, which is exactly the moment worth seeing.
- The projects tree and the agents/MCPs list below it used to each cap at 30%
  of the WINDOW height and scroll inside that, measured off
  `useWindowDimensions` rather than a percentage: the rail was one scroll
  holding the tree and the agents below it, so a `maxHeight: '45%'` would have
  measured against a parent that grows with the list and capped nothing.
  Thirty-one worktrees pushed AGENTS off the bottom of the screen, and eight
  agents or a dozen MCPs did the same to whatever was under them. Both caps
  are gone now that the tree and the MCP list are two bodies sharing one
  footer-switched scroll rather than two lists sharing one rail: whichever
  body is showing takes the whole thing, which is the fix that actually holds
  rather than a smaller cap in a new place.

  The header and the footer's keys still stay OUTSIDE that scroll, the same
  rule the caps existed to protect. ＋ new agent is in the footer rather than
  the top of the list so it does not end up below eight agent rows; putting it
  inside a scrolling list would have carried it off the top instead, which is
  the same problem from the other end.
- An agent gets a WINDOW of its own, not a split. Two agents sharing a window
  share its layout, and tmux keeps layout and zoom on the window -- every
  client in a group sees the same one -- so a split makes it impossible for
  the app to show one agent while a terminal beside it shows another: zooming
  for one zooms for both. Window *selection* is per session, which is what
  lets the two look at different agents at once. `agent start --split` still
  splits for whoever wants a side-by-side on the glass; it is no longer what
  every caller passes without meaning it.
- The terminal tab is a real terminal on the web build: xterm.js over a pty
  running `tmux attach`, in `ptybridge.py`. Colour, cursor, mouse, resize and
  scrollback, because it is not a drawing of a terminal. The native build keeps
  the scraped view -- xterm needs a DOM -- so both live in `Pane.js` behind
  `Platform.OS === 'web'`. `python3 ptybridge.py` is its self-check.
- The pty attaches to a session GROUPED with the agent's, never the agent's
  own. tmux sizes a session to its smallest client, so attaching the app beside
  a real Terminal window would shrink that window to the browser's idea of a
  terminal. One view per SESSION, not per pane: a pty per pane put several of
  our own clients on one session and the app squeezed itself.
- A reconnecting reader gets the screen from a **nudged resize**, not a replay.
  Two wrong answers came first. `refresh-client` sends only what CHANGED and
  tmux believes the departed reader still has the screen, so it sent nothing --
  a fresh terminal was a cursor on an empty box. Keeping our own tail and
  replaying it shredded the screen instead, because those bytes were written
  for whatever size the terminal was THEN. A size change is the one thing tmux
  always redraws for.
- `refresh-client -t` wants a CLIENT -- a tty -- and not a session. Handing it
  a session name fails silently, which is what made the repaint look broken
  rather than misaddressed.
- What the terminal is looking at is a READ, never a write: `#{pane_id}` of the
  app's own client, polled while that tab is open, and the focused agent
  follows it. The app cannot steer it -- tmux keeps the active pane per window
  and shares it between every client, so moving it from here would move the
  cursor in the Terminal window beside it.
- **open in Terminal** hands the pane to a real one. The in-app view cannot
  have colour (`capture-pane -p` strips it), a cursor (it polls), the mouse or
  scrollback, and none of that needs solving on a desktop where tmux is already
  running -- the terminal that has all four is one `attach` away. The key
  writes a `.command` script and opens it rather than building an AppleScript
  string: a file on disk has nothing to interpolate into, and the one value
  from the request had to match `%<digits>` to get that far. Web only; the
  native iPad build is the case with no terminal to open.
- The terminal tab is a terminal. What it sends is what a keyboard sends --
  characters, and the control sequences for the keys that are not characters
  (`\x03` for Ctrl-C, `\x1b[A` for Up) -- through `POST /keys`, which hands
  them to `send-keys -l` and interprets nothing. Bytes and not tmux key NAMES
  deliberately: no name to allowlist, nothing that can start with a dash and
  be read as an option, no second syntax to keep in step with tmux's. The
  composer is hidden while it is showing, because a terminal already has a
  line and a second box sending to the same pane is two prompts for one cursor.
- Keystrokes go out one request at a time, everything typed meanwhile riding
  the next. A fetch per key looked right and was not: they are concurrent, so
  they arrive in whatever order the network settles on, and typing
  `echo terminal-is-live` put `ech toremnial-is-live` in the pane. Typing is a
  stream and a stream has exactly one order.
- Agents sharing a worktree get tabs above the focus title. Same checkout, not
  merely the same repo -- two agents on different branches of one project are
  not working on the same thing. Drawn only when there is more than one: a
  single tab is a label pretending to be a control.
- `panel_col` counts each agent's subagents on the wire, which used to put
  the count on every rail card and not just the lit one. It was focused-only
  to begin with because the wire carried the focused agent's subagents and
  nobody else's -- a blank meant "not known" where a 0 would have been a lie.
  Now that the rail is a tree, a nested row has no width left for a third
  fact once the name and model are drawn, so the count doesn't reach a rail
  row at all any more -- it reads under the focused agent itself, in the
  focus view's own `subagents · N` sub-tab, the only place the wire ever
  detailed it in the first place.
- The skills catalogue lists a plugin's skills from its own `installPath`,
  found at any depth under a `skills/` directory. Globbing the plugin tree
  instead matched every cached VERSION and the marketplace copy besides, so
  one plugin with six versions listed its skills six times; and a fixed one-
  or two-level depth silently dropped every skill filed under a category.
  Duplicates that survive this are real: two marketplaces can install the
  same plugin, and that is worth seeing rather than hiding.
- A workflow file has two names and they are not the same name. Its stem is
  its identity on the wire — a write is addressed by it — and `name:` inside is
  the label GitHub draws. Calling both `name` meant one dict spread renamed
  `publish-sdk` to `Publish SDK`, and every read and write of it 400'd on a
  path that could not exist. `workflow_meta` returns `title` for the label.
- tmux's server exits with its last session, so `no server running` is the
  ordinary state of a machine with no agents on it, not a fault. `new-window`
  and `split-window` cannot answer it -- neither creates a session, only
  `new-session` does. `_agent_start` knew that and the worktree verbs did not,
  so opening a worktree worked all day and then 500'd the morning after the
  last agent was closed. Every path that opens a pane goes through
  `_open_pane` now, which asks once.
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
