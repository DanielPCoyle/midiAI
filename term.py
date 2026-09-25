#!/usr/bin/env python3
"""tmux backend for push_cc's herdr() -- same eight calls, same JSON shapes.

herdr never sees more than one screen of a claude pane either (it runs on the
alternate screen and never scrolls), so nothing here is a downgrade. The one
new idea is the pid join in agent list: claude agents --json knows cwd,
session id and status for every claude on the machine, tmux knows which pane
is which -- pid is the only key that is safe to join them on, because two
agents commonly share a cwd.
"""
import glob
import json
import os
import re
import subprocess
import threading
import time

import telemetry

PANE_FORMAT = ("#{pane_id}\t#{pane_pid}\t#{pane_current_path}\t"
               "#{pane_active}\t#{window_active}\t#{session_attached}\t"
               "#{@agent_name}")

NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")  # herdr's own rule, adopted deliberately

STATUS = {"busy": "working", "waiting": "blocked"}  # anything else -> idle

WORKTREE_ROOT = os.path.expanduser("~/.push/worktrees")


def _branch_slug(branch):
    """feature/thing -> feature-thing. A branch may hold slashes; the one
    directory named after it may not, or one branch quietly becomes two."""
    return re.sub(r"-{2,}", "-",
                  re.sub(r"[^a-z0-9]", "-", branch.lower())).strip("-") or "wt"


def _run(cmd, timeout=10):
    """every subprocess call lands here so dispatch() never has to catch."""
    with telemetry.span(telemetry.exec_name(cmd), "client") as s:
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.SubprocessError) as e:
            out = subprocess.CompletedProcess(cmd, 1, "", str(e))
        s.set("process.exit_code", out.returncode)
        return out


def _ok(result):
    return json.dumps({"result": result})


def _err(code, message):
    # push_cc greps stdout for the literal error code (agent_name_taken) and
    # for the substring '"error"' -- both must survive json.dumps untouched.
    return json.dumps({"error": {"code": code, "message": (message or "")[:200]}})


def _flag(rest, name):
    if name in rest:
        i = rest.index(name)
        if i + 1 < len(rest):
            return rest[i + 1]
    return None


def _status(claude_status, session=None):
    """A background session -- one Claude Code hosts in a daemon (`claude
    bg-spare`), which is what the pane's claude is then attached to -- says
    `busy` for as long as any background shell or agent of its runs, a dev
    server included, so forever. Its `state` is what says whether it is
    waiting on you: `blocked` is turn over, prompt empty, and `done` is the
    same after a finished turn ("done 11:17 AM" on screen). Read as busy, the
    queue never sent to it."""
    session = session or {}
    if session.get("kind") == "background" and session.get("state") in ("blocked", "done"):
        return "idle"
    return STATUS.get(claude_status, "idle")


def _tail(text, n):
    """tmux `-S -N` means N lines back in HISTORY, not N lines returned.
    Claude runs on the alt screen so there is no history -- capture always
    hands back one full screen, and truncating to N is on us, same as herdr."""
    lines = text.splitlines()
    return "\n".join(lines[-n:]) if n > 0 else ""


def _proc_table():
    """pid -> (ppid, comm) for everything, in one ps. One call a poll beats a
    pgrep per pane per poll, and the tree has to be walked anyway."""
    out = _run(["ps", "-axo", "pid=,ppid=,comm="])
    table = {}
    for line in out.stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            table[int(parts[0])] = (int(parts[1]), parts[2])
    return table


def _descendants(root, table, depth=3):
    """root first, then its children, breadth first."""
    kids = {}
    for pid, (ppid, _) in table.items():
        kids.setdefault(ppid, []).append(pid)
    seen, frontier = [root], [root]
    for _ in range(depth):
        nxt = [c for p in frontier for c in kids.get(p, [])]
        if not nxt:
            break
        seen += nxt
        frontier = nxt
    return seen


def _find_engine(pane_pid, by_pid, table):
    """Which engine (if any) a pane's process tree is running, and claude's
    session pid when it is claude.

    Two questions, not one, for claude: is there a claude in this pane at
    all, and does `claude agents --json` know a session for it? herdr
    identifies the agent by process and layers state on top, and it is right
    to. A claude sitting in the agents view has not started a session, so it
    appears nowhere in `claude agents --json` -- joining on that alone drops
    the pane off the Push entirely until someone types into it. The process
    is what says an agent is there; the session is enrichment.

    Codex has no equivalent join here: `codex agents` browses the app-server
    daemon interactively and has no `--json`, and the app-server protocol
    itself is a whole daemon to speak to for what would otherwise be a cheap
    per-poll call -- so a codex agent is only ever "there", never enriched by
    pid the way claude is. Its status is `unknown` (see _match_agents) and
    its session comes from _codex_rollout_for, by cwd, not by pid.

    Returns ("claude", session_pid_or_None) | ("codex", None) | (None, None).
    """
    line = _descendants(pane_pid, table)
    known = next((p for p in line if p in by_pid), None)
    # `claude bg-spare` is a daemon, not a session -- basename match only
    comms = {table.get(p, (0, ""))[1].rsplit("/", 1)[-1] for p in line}
    if known is not None or "claude" in comms:
        return "claude", known
    if "codex" in comms:
        return "codex", None
    return None, None


# A codex rollout's first line is `session_meta`, carrying cwd, a session id
# and a start timestamp -- push_cc.codex_usage already reads the newest few
# rollouts for rate-limit bars the same way (glob, sort by mtime, cap the
# scan), proof this is cheap enough for a poll loop. Only that first line is
# read per file -- never the whole transcript -- so linking an agent to its
# rollout costs one small read per candidate file, not one per byte of
# history.
CODEX_SESSIONS_GLOB = os.path.expanduser("~/.codex/sessions/**/rollout-*.jsonl")
CODEX_ROLLOUT_TTL = 5.0
CODEX_ROLLOUT_SCAN = 20
_codex_rollout_cache = {"at": -1e9, "rows": []}


def _codex_rollouts():
    """[{"cwd", "session_id", "path"}, ...], newest file first -- cached for
    CODEX_ROLLOUT_TTL so an agent-list poll every 0.5s does not glob and open
    files every time."""
    now = time.monotonic()
    if now - _codex_rollout_cache["at"] < CODEX_ROLLOUT_TTL:
        return _codex_rollout_cache["rows"]
    paths = glob.glob(CODEX_SESSIONS_GLOB, recursive=True)
    paths.sort(key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0, reverse=True)
    rows = []
    for path in paths[:CODEX_ROLLOUT_SCAN]:
        try:
            with open(path) as f:
                first = json.loads(f.readline())
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if first.get("type") != "session_meta":
            continue
        payload = first.get("payload") or {}
        if payload.get("cwd"):
            rows.append({"cwd": payload["cwd"],
                        "session_id": payload.get("session_id"), "path": path})
    _codex_rollout_cache.update(at=now, rows=rows)
    return rows


def _codex_rollout_for(cwd):
    """The newest rollout whose session_meta.cwd matches this pane's cwd, or
    None. A real "start time" join (matching a rollout to the moment THIS
    pane's process started) is not available cheaply -- tmux carries no pane
    start time in its format variables -- so this links on cwd and recency
    only, which is right when one codex runs per checkout and can be wrong
    with two codex panes open on the very same cwd at once. Documented, not
    hidden: get it wrong and the worst case is a transcript link pointing at
    a sibling agent's session, never a crash."""
    for row in _codex_rollouts():
        if row["cwd"] == cwd:
            return row
    return None


def _match_agents(rows, by_pid, table=None, background=()):
    """rows: parsed PANE_FORMAT tuples. join on pid, never cwd -- two agents
    routinely share a cwd and a cwd join would silently collapse them.

    focused needs pane_active AND window_active AND session_attached: a
    session's window_active is per-session, so with more than one tmux
    session multiple panes come back window_active=1 at once. Only
    session_attached narrows that to the one thing an attached human could
    actually be looking at -- herdr returns exactly one focused pane, and
    push_cc takes the first match, so a second one points the Push at the
    wrong seat.

    One agent per pane, though a pane can come back more than once: a
    grouped session shares its windows, so `list-panes -a` lists each pane
    once per session in the group. ptybridge's `podium-push` view is one --
    with the terminal tab open, every agent showed up twice in the rail and
    the seat tabs. Its rows differ only in the per-session flags, so the
    pane is focused if any of them says so."""
    agents = []
    seen = {}
    table = _proc_table() if table is None else table   # injectable for demo()
    for pane_id, pane_pid, cwd, pa, wa, sattach, name in rows:
        focused = pa == "1" and wa == "1" and sattach == "1"
        if pane_id in seen:
            seen[pane_id]["focused"] = seen[pane_id]["focused"] or focused
            continue
        engine, cpid = _find_engine(int(pane_pid), by_pid, table)
        if engine is None:
            continue                    # no agent here, just a shell
        if engine == "claude":
            info = by_pid.get(cpid) or {}
            agent_cwd = info.get("cwd") or cwd
            # a claude with no session yet is present but unclassified, which
            # is what `unknown` is for. idle would be a claim we cannot make.
            status = _status(info.get("status"), info) if info else "unknown"
            session = info.get("sessionId")
        else:                            # codex
            agent_cwd = cwd
            # No machine-readable status exists cheaply for codex (see
            # _find_engine) and this file never starts a codex session to go
            # learn its TUI's busy/idle text -- `unknown` is the honest
            # answer, not a guessed pane-scrape that could be silently wrong
            # forever. A future pass that actually observes a live codex
            # pane can add a scrape heuristic here.
            status = "unknown"
            session = (_codex_rollout_for(cwd) or {}).get("session_id")
        agents.append({
            "terminal_id": pane_id,
            "pane_id": pane_id,
            "cwd": agent_cwd,
            "engine": engine,
            "agent_status": status,
            "focused": focused,
            "agent_session": {"value": session},
            "name": name or None,
        })
        seen[pane_id] = agents[-1]
    _adopt_background(agents, by_pid, background)
    return agents


def _adopt_background(agents, by_pid, background):
    """A background session's pid is its daemon's, which is no descendant of
    the pane whose claude is attached to it -- so the pid join above misses it
    and the pane reads `unknown` for good. Such a pane takes the background
    session in its own cwd, but only when that is unambiguous: exactly one
    unclaimed background session there and exactly one unmatched pane. The
    pid rule exists because a cwd join collapses two agents in one folder;
    anything short of one-to-one stays unknown rather than guess."""
    claimed = {a["agent_session"]["value"] for a in agents if a["agent_session"]["value"]}
    free = [s for s in background if s.get("sessionId") and s["sessionId"] not in claimed]
    orphans = [a for a in agents
               if a.get("engine") == "claude" and not a["agent_session"]["value"]]
    for a in orphans:
        mine = [s for s in free if s.get("cwd") == a["cwd"]]
        rivals = [o for o in orphans if o["cwd"] == a["cwd"]]
        if len(mine) == 1 and len(rivals) == 1:
            s = mine[0]
            a.update(agent_status=_status(s.get("status"), s),
                     agent_session={"value": s["sessionId"]})


# `claude agents --json` is a Node CLI: ~0.8s a run. push_cc lists agents
# every 0.5s and mapui on most requests, so uncached it was never NOT
# running -- a sample every 0.3s found one live ten times out of ten -- and
# the machine it loaded is the one serving the app: a page load's requests
# queued behind it, /projects took 11s. One run serves everyone for
# CLAUDE_AGENTS_TTL, and callers that arrive mid-run wait for it rather
# than starting their own.
CLAUDE_AGENTS_TTL = 1.5
_claude_agents_cache = {"at": -1e9, "out": "[]"}
_claude_agents_lock = threading.Lock()


def _claude_agents():
    with _claude_agents_lock:
        if time.monotonic() - _claude_agents_cache["at"] >= CLAUDE_AGENTS_TTL:
            out = _run(["claude", "agents", "--json"])
            _claude_agents_cache.update(at=time.monotonic(), out=out.stdout or "[]")
        return _claude_agents_cache["out"]


def _agent_list():
    panes = _run(["tmux", "list-panes", "-a", "-F", PANE_FORMAT])
    if panes.returncode != 0:
        # No server is not a failure, it is an empty machine -- the state you
        # are in the moment herdr closes and before the first Add Device.
        # Reporting it as an error puts two lines a second into push.log
        # forever, and the surface would read the same either way.
        return _ok({"agents": [], "type": "agent_list"})
    try:
        claude_agents = json.loads(_claude_agents())
    except json.JSONDecodeError:
        claude_agents = []
    by_pid = {a["pid"]: a for a in claude_agents if "pid" in a}
    rows = [tuple(line.split("\t")) for line in panes.stdout.splitlines() if line.strip()]
    rows = [r for r in rows if len(r) == 7]
    background = [a for a in claude_agents if a.get("kind") == "background"]
    return _ok({"agents": _match_agents(rows, by_pid, background=background),
                "type": "agent_list"})


# Claude Code's suggested next prompt is drawn dim on an otherwise empty
# input line: "❯\xa0" then SGR 2. A plain capture drops the styling, so a
# suggestion read exactly like text you had typed and the app adopted it as
# yours. Only the bottom of the pane is captured styled -- the input box.
_GHOST_RE = re.compile(r"❯\xa0(?:\x1b\[[0-9;]*m)*\x1b\[2m")


def _is_ghost(styled):
    rows = [r for r in styled.splitlines() if "❯" in r]
    return bool(rows) and bool(_GHOST_RE.search(rows[-1]))


def _agent_read(target, n):
    out = _run(["tmux", "capture-pane", "-p", "-t", target, "-S", "-0"])
    if out.returncode != 0:
        return _err("tmux_capture_pane", out.stderr)
    styled = _run(["tmux", "capture-pane", "-e", "-p", "-t", target, "-S", "-12"])
    return _ok({"read": {"text": _tail(out.stdout, n),
                         "ghost": styled.returncode == 0 and _is_ghost(styled.stdout)}})


def _agent_send(target, text):
    if not text:
        return _err("empty_send", "tmux send-keys errors on empty text")
    # A literal \r typed with -l lands in Claude Code's input as a newline,
    # not a submit -- every prompt from the app sat in the box unsent. tmux's
    # named Enter key is what submits, so each \r goes as that and the text
    # around it stays literal.
    for part in re.split(r"(\r)", text):
        if not part:
            continue
        keys = ["Enter"] if part == "\r" else ["-l", "--", part]
        out = _run(["tmux", "send-keys", "-t", target, *keys])
        if out.returncode != 0:
            return _err("tmux_send_keys", out.stderr)
    return _ok({})


def _agent_focus(target):
    _run(["tmux", "select-pane", "-t", target])
    _run(["tmux", "select-window", "-t", target])
    _run(["tmux", "switch-client", "-t", target])  # best-effort, no client may be attached
    return _ok({})


def _name_taken(name):
    """@agent_name is a pane-scoped user option, not the window name: a split
    lands in the CURRENT window, so rename-window would name every agent in
    that window at once and each new one would clobber the last. pane_title is
    no good either -- claude sets its own ("✳ Claude Code") over OSC 2. a
    @user option is per pane and the app inside cannot touch it.

    Shared by agent start and worktree create -- both mint a fresh pane name."""
    taken = _run(["tmux", "list-panes", "-a", "-F", "#{@agent_name}"])
    return name in taken.stdout.splitlines()


def _has_server():
    """Whether tmux has a server at all.

    The server exits with its last session, so "no server running" is the
    ordinary state of a machine nobody is working on -- not a fault. It has to
    be asked because `new-window` and `split-window` both need a session to
    attach to and neither can create one. Only `new-session` can.
    """
    return _run(["tmux", "list-sessions"]).returncode == 0


def _with_access(argv):
    """Settings > Claude: how a new agent reaches a model (subscription, API
    key, Bedrock, Vertex) and its default model and effort, as flags on its
    own command line. Lazy, like push_cc's import of this file: a broken
    access.py must not stop an agent starting."""
    try:
        import access
        return access.with_access(argv)
    except Exception:
        return list(argv)


def _engine_argv(engine, model):
    """The full argv for a new-agent request that names its engine --
    engines.py already worked out every flag (provider, model, effort,
    sandbox/approval for codex), so this is not a second copy of that
    decision, just the lazy import _with_access already uses for access.py.
    None on a bad engine/value, so the caller can report it instead of
    starting a broken pane."""
    try:
        import engines
        return engines.launch_argv(engine, {"model": model} if model else None)
    except Exception:
        return None


def _open_pane(cwd, argv, access=True):
    """A pane running argv in cwd -- a new window in the push session, or a
    new session if this is the first pane on the machine.

    Every path that opens one comes through here. _agent_start asked whether
    the server was up and the worktree verbs did not, so creating or opening
    a worktree worked all day and then failed the morning after the last agent
    was closed -- tmux's own "no server running" surfacing to the app as a 500.
    The question has one answer now, in one place.

    `access=False` skips _with_access: an engine-built argv (_engine_argv)
    already carries its own --settings/--model or -c overrides, and running
    it back through _with_access would try to inject access.py's OWN
    defaults a second time -- for claude that means two --settings flags
    from two different stores; codex is unaffected either way since
    _with_access only touches a bare `claude`, but the skip is correct for
    both rather than correct by accident.
    """
    if access:
        argv = _with_access(argv)
    if _has_server():
        return _run(["tmux", "new-window", "-c", cwd,
                     "-P", "-F", "#{pane_id}", "--", *argv])
    return _run(["tmux", "new-session", "-d", "-s", "push", "-c", cwd,
                 "-P", "-F", "#{pane_id}", "--", *argv])


def _agent_start(args):
    rest = list(args[2:])
    name = rest[0] if rest else None
    if not name:
        return _err("bad_args", "agent start needs a name")
    cwd = _flag(rest, "--cwd") or os.getcwd()
    split = _flag(rest, "--split") or ""     # empty means a window of its own
    engine = _flag(rest, "--engine") or ""   # a new-agent request carries this

    if engine:
        argv = _engine_argv(engine, _flag(rest, "--model") or "")
        if argv is None:
            return _err("bad_engine", f"could not build a launch for engine {engine!r}")
        needs_access = False        # engines.py already built the final argv
    else:
        argv = rest[rest.index("--") + 1:] if "--" in rest else []
        needs_access = True         # legacy bare-claude path, unchanged

    if _name_taken(name):
        return _err("agent_name_taken", f"agent name {name!r} already in use")

    # A window each, not a split. Two agents sharing a window share its
    # layout, and tmux keeps layout and zoom on the WINDOW -- every client in
    # a group sees the same one. So a split makes it impossible for the app to
    # show one agent while a terminal beside it shows another: zooming for one
    # zooms for both. Window SELECTION is per session, so a window each is
    # what lets the two of them look at different agents at once.
    #
    # `--split` still splits, for whoever wants a side-by-side on the glass.
    # It is no longer what every caller passes without meaning it.
    if split:
        split_argv = _with_access(argv) if needs_access else argv
        out = _run(["tmux", "split-window", "-h" if split == "right" else "-v",
                    "-c", cwd, "-P", "-F", "#{pane_id}", "--", *split_argv])
    else:
        out = _open_pane(cwd, argv, access=needs_access)
    if out.returncode != 0:
        return _err("tmux_start", out.stderr)
    _run(["tmux", "set-option", "-p", "-t", out.stdout.strip(),
          "@agent_name", name])
    return _ok({})


def _agent_rename(target, name):
    if name == "--clear":
        _run(["tmux", "set-option", "-p", "-u", "-t", target, "@agent_name"])
        return _ok({})
    if not NAME_RE.match(name or ""):
        return _err("invalid_agent_name", f"{name!r} does not match {NAME_RE.pattern}")
    # exclude target itself -- renaming a pane to the name it already holds
    # is a no-op, not a collision with itself
    taken = _run(["tmux", "list-panes", "-a", "-F", "#{pane_id} #{@agent_name}"])
    others = [line.split(" ", 1)[1] for line in taken.stdout.splitlines()
              if line.split(" ", 1)[0] != target and len(line.split(" ", 1)) == 2]
    if name in others:
        return _err("agent_name_taken", f"agent name {name!r} already in use")
    _run(["tmux", "set-option", "-p", "-t", target, "@agent_name", name])
    return _ok({})


def _pane_close(pane_id):
    out = _run(["tmux", "kill-pane", "-t", pane_id])
    if out.returncode != 0:
        return _err("tmux_kill_pane", out.stderr)
    return _ok({})


def _repo_name(cwd):
    """The repo's own folder name, from the main checkout or any linked
    worktree of it. basename(cwd) is only right in the main checkout: from a
    worktree it named the new one after the worktree, <root>/<wt-slug>/<branch>."""
    out = _run(["git", "-C", cwd, "rev-parse", "--git-common-dir"])
    common = out.stdout.strip() if out.returncode == 0 else ""
    if not common:
        return os.path.basename(cwd)
    return os.path.basename(os.path.dirname(os.path.normpath(os.path.join(cwd, common))))


def _worktree_create(args):
    rest = list(args[2:])
    cwd = _flag(rest, "--cwd")
    branch = _flag(rest, "--branch")
    name = _flag(rest, "--name")
    if not cwd or not branch:
        return _err("bad_args", "worktree create needs --cwd and --branch")
    if name is not None:
        if not NAME_RE.match(name):
            return _err("invalid_agent_name", f"{name!r} does not match {NAME_RE.pattern}")
        # checked before git worktree add, so a name collision doesn't leave
        # a stray worktree behind
        if _name_taken(name):
            return _err("agent_name_taken", f"agent name {name!r} already in use")
    cwd = os.path.normpath(cwd)
    # ponytail: one root for every worktree, herdr's layout -- <root>/<repo>/
    # <branch-slug>. WORKTREE_ROOT is the knob.
    #
    # The slug matters: push_cc's slug() deliberately KEEPS "/" so a branch
    # reads feature/thing, and its fallback name is literally push/HHMMSS.
    # Pasted into a path that becomes a stray directory level beside the repo,
    # so the branch keeps its slash and only the path gets flattened.
    dest = os.path.join(WORKTREE_ROOT, _repo_name(cwd),
                        _branch_slug(branch))
    out = _run(["git", "-C", cwd, "worktree", "add", dest, "-b", branch])
    if out.returncode != 0:
        out = _run(["git", "-C", cwd, "worktree", "add", dest, branch])  # branch exists already
        if out.returncode != 0:
            return _err("git_worktree_add", out.stderr)
    out = _open_pane(dest, ["claude", "--permission-mode", "auto"])
    if out.returncode != 0:
        return _err("tmux_new_window", out.stderr)
    if name:
        _run(["tmux", "set-option", "-p", "-t", out.stdout.strip(),
              "@agent_name", name])
    return _ok({})


def _worktree_list_rows(cwd):
    """git worktree list --porcelain, parsed into dicts. Shared by list itself
    and by remove's main-worktree / dirty checks, so both see the same idea
    of which record is the main one.

    Porcelain is blank-line-separated records of `worktree <path>`,
    `HEAD <sha>`, then either `branch refs/heads/<name>` or a bare
    `detached`, and optionally `locked`/`prunable`/`bare`. git always lists
    the main worktree first.
    """
    out = _run(["git", "-C", cwd, "worktree", "list", "--porcelain"])
    if out.returncode != 0:
        return None, out.stderr
    worktrees = []
    rec = {}

    def flush():
        if not rec.get("path"):
            return
        branch = rec.get("branch")
        if branch and branch.startswith("refs/heads/"):
            branch = branch[len("refs/heads/"):]
        worktrees.append({
            "path": rec["path"],
            "branch": branch,
            "head": rec.get("head"),
            "main": len(worktrees) == 0,
            "detached": "detached" in rec,
            "locked": "locked" in rec,
            "prunable": "prunable" in rec,
            # git keeps listing a worktree whose directory was deleted -- this
            # is exactly the orphan state the remove verb exists to clean up.
            "exists": os.path.isdir(rec["path"]),
        })

    for line in out.stdout.splitlines():
        if not line.strip():
            flush()
            rec = {}
            continue
        key, _, val = line.partition(" ")
        if key == "worktree":
            rec["path"] = val
        elif key == "HEAD":
            rec["head"] = val
        elif key == "branch":
            rec["branch"] = val
        elif key in ("detached", "locked", "prunable", "bare"):
            rec[key] = True
    flush()
    return worktrees, None


def _worktree_list(args):
    cwd = _flag(args[2:], "--cwd")
    if not cwd:
        return _err("bad_args", "worktree list needs --cwd")
    worktrees, err = _worktree_list_rows(cwd)
    if err is not None:
        return _err("git_worktree_list", err)
    return _ok({"worktrees": worktrees, "type": "worktree_list"})


def _worktree_branches(args):
    """Every local branch of a repo, and which worktree (if any) has it out.

    git will not check the same branch out twice, so "taken" is not advice --
    it is the reason the switch would be refused, named before you try it."""
    cwd = _flag(args[2:], "--cwd")
    if not cwd:
        return _err("bad_args", "worktree branches needs --cwd")
    out = _run(["git", "-C", cwd, "for-each-ref", "--format=%(refname:short)",
                "refs/heads"])
    if out.returncode != 0:
        return _err("git_for_each_ref", out.stderr)
    worktrees, _err_list = _worktree_list_rows(cwd)
    held = {w["branch"]: w["path"] for w in (worktrees or []) if w.get("branch")}
    return _ok({"branches": [{"name": b, "at": held.get(b)}
                             for b in out.stdout.split()],
                "type": "branch_list"})


def _worktree_switch(args):
    """Check another branch out in a worktree that already exists.

    No dirty check of our own, unlike remove: git refuses a checkout that would
    lose work and carries changes over when it would not, which is a better
    rule than any we would write, and nothing here deletes anything. Its
    refusal is passed back verbatim -- "your local changes would be
    overwritten" says more than a code of ours would."""
    rest = list(args[2:])
    dest = _flag(rest, "--path")
    branch = _flag(rest, "--branch")
    if not dest or not branch:
        return _err("bad_args", "worktree switch needs --path and --branch")
    if not os.path.isdir(dest):
        return _err("worktree_missing", f"{dest} is not a directory")
    out = _run(["git", "-C", dest, "checkout", branch])
    if out.returncode != 0:
        return _err("git_checkout", out.stderr.strip() or out.stdout.strip())
    return _ok({"branch": branch})


def _worktree_remove(args):
    rest = list(args[2:])
    raw_cwd = _flag(rest, "--cwd")
    raw_dest = _flag(rest, "--path")
    force = "--force" in rest
    if not raw_cwd or not raw_dest:
        return _err("bad_args", "worktree remove needs --cwd and --path")
    # realpath, not normpath -- macOS's /tmp is a symlink to /private/tmp and
    # a bare string compare against git's (resolved) path would miss the main
    # worktree entirely.
    cwd = os.path.realpath(raw_cwd)
    dest = os.path.realpath(raw_dest)

    worktrees, _list_err = _worktree_list_rows(cwd)
    main = next((w for w in (worktrees or []) if w["main"]), None)
    if main and os.path.realpath(main["path"]) == dest:
        return _err("worktree_is_main", "the main worktree cannot be removed")

    if not force and os.path.isdir(dest):
        status = _run(["git", "-C", dest, "status", "--porcelain"])
        if status.stdout.strip():
            # losing uncommitted work to a tidy-up button is the failure that
            # must not happen -- say what is dirty
            return _err("worktree_dirty",
                        f"uncommitted changes in {dest}: {status.stdout.strip()}")

    out = _run(["git", "-C", cwd, "worktree", "remove", dest] +
              (["--force"] if force else []))
    if out.returncode != 0:
        if not os.path.isdir(dest):
            # gone already -- pruning an orphan IS the removal
            prune = _run(["git", "-C", cwd, "worktree", "prune"])
            if prune.returncode != 0:
                return _err("git_worktree_prune", prune.stderr)
            return _ok({})
        return _err("git_worktree_remove", out.stderr)
    return _ok({})


def _worktree_open(args):
    dest = _flag(args[2:], "--path")
    if not dest:
        return _err("bad_args", "worktree open needs --path")
    if not os.path.isdir(dest):
        return _err("worktree_missing", f"{dest} is not a directory")
    # creating and opening are different acts -- this is the one you want the
    # morning after, on a worktree that already exists
    out = _open_pane(dest, ["claude", "--permission-mode", "auto"])
    if out.returncode != 0:
        return _err("tmux_new_window", out.stderr)
    return _ok({})


def dispatch(args):
    """same JSON shape herdr's stdout carried, so herdr() only needs a
    backend switch. never raises -- an unhandled crash here would take
    push_cc's poll loop with it."""
    try:
        args = tuple(args)
        head = args[:2]
        if head == ("agent", "list"):
            return _agent_list()
        if head == ("agent", "read"):
            n = int(_flag(args[3:], "--lines") or 40)
            return _agent_read(args[2], n)
        if head == ("agent", "send"):
            return _agent_send(args[2], args[3] if len(args) > 3 else "")
        if head == ("agent", "focus"):
            return _agent_focus(args[2])
        if head == ("agent", "start"):
            return _agent_start(args)
        if head == ("agent", "rename"):
            return _agent_rename(args[2], args[3])
        if head == ("pane", "close"):
            return _pane_close(args[2])
        if head == ("worktree", "create"):
            return _worktree_create(args)
        if head == ("worktree", "list"):
            return _worktree_list(args)
        if head == ("worktree", "branches"):
            return _worktree_branches(args)
        if head == ("worktree", "switch"):
            return _worktree_switch(args)
        if head == ("worktree", "remove"):
            return _worktree_remove(args)
        if head == ("worktree", "open"):
            return _worktree_open(args)
        return _err("unknown_command", " ".join(str(a) for a in head))
    except Exception as e:
        return _err("term_internal", str(e))


if __name__ == "__main__":
    def demo_repo_name():
        # a worktree made from a worktree is filed under the repo, not the worktree
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            repo, wt = os.path.join(tmp, "myrepo"), os.path.join(tmp, "elsewhere", "wt-one")
            _run(["git", "init", "-q", repo])
            _run(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t",
                  "commit", "-q", "--allow-empty", "-m", "x"])
            _run(["git", "-C", repo, "worktree", "add", "-q", wt, "-b", "one"])
            assert _repo_name(repo) == "myrepo", _repo_name(repo)
            assert _repo_name(wt) == "myrepo", _repo_name(wt)
            assert _repo_name(tmp) == os.path.basename(tmp), "no repo: the folder's own name"

    def demo():
        demo_repo_name()   # before demo() swaps _run out for fakes
        assert _status("busy") == "working"
        assert _status("waiting") == "blocked"
        assert _status("idle") == "idle"
        assert _status("anything-else") == "idle"
        # a background session waiting on you is idle, whatever its daemon says
        assert _status("busy", {"kind": "background", "state": "blocked"}) == "idle"
        assert _status("busy", {"kind": "background", "state": "done"}) == "idle"
        assert _status("busy", {"kind": "background", "state": "running"}) == "working"
        assert _status("busy", {"kind": "interactive"}) == "working"

        capture = "\n".join(f"line{i}" for i in range(57))
        tail40 = _tail(capture, 40).splitlines()
        assert tail40 == [f"line{i}" for i in range(17, 57)], tail40
        assert len(tail40) == 40
        # the real bytes of a suggestion, and of the same words typed
        assert _is_ghost("\x1b[39m❯\xa0\x1b[2mgo ahead\x1b[0m\n  footer")
        assert not _is_ghost("\x1b[39m❯\xa0go ahead\n  footer")
        assert not _is_ghost("\x1b[39m❯\xa0\n"), "empty box is no suggestion"
        assert not _is_ghost("❯ \x1b[2mold dim prompt\n❯\xa0typed"), "last ❯ wins"

        # pid join: two panes sharing a cwd stay two agents -- cwd never
        # decides identity, pid does
        by_pid = {
            111: {"cwd": "/repo", "sessionId": "aaa", "status": "busy"},
            222: {"cwd": "/repo", "sessionId": "bbb", "status": "waiting"},
        }
        rows = [("%0", "111", "/repo", "1", "1", "1", "alpha"),
                ("%1", "222", "/repo", "1", "1", "0", "")]
        agents = _match_agents(rows, by_pid)
        assert len(agents) == 2, "same cwd, different pids -> two agents"
        assert {a["agent_session"]["value"] for a in agents} == {"aaa", "bbb"}
        by_tid = {a["terminal_id"]: a for a in agents}
        assert by_tid["%0"]["agent_status"] == "working"
        assert by_tid["%1"]["agent_status"] == "blocked"
        assert by_tid["%0"]["name"] == "alpha", "@agent_name surfaces as name"
        assert by_tid["%1"]["name"] is None, "unset @agent_name comes back empty -> None"

        # window_active is per-session -- two sessions can both show
        # window_active=1 at once. only session_attached narrows that to
        # the pane a human is actually looking at.
        by_pid2 = {111: {"cwd": "/a", "sessionId": "a"}, 222: {"cwd": "/b", "sessionId": "b"}}
        rows2 = [("%0", "111", "/a", "1", "1", "0", ""),   # session a, detached
                 ("%1", "222", "/b", "1", "1", "1", "")]   # session b, attached
        focused = [a for a in _match_agents(rows2, by_pid2) if a["focused"]]
        assert len(focused) == 1 and focused[0]["terminal_id"] == "%1", focused

        # nobody attached anywhere -> nobody is looking at anything
        rows3 = [("%0", "111", "/a", "1", "1", "0", ""),
                 ("%1", "222", "/b", "1", "1", "0", "")]
        assert not any(a["focused"] for a in _match_agents(rows3, by_pid2))

        # a grouped session (ptybridge's podium-push view) lists every pane
        # once per session: still one agent, focused if any copy is
        rows5 = [("%0", "111", "/a", "1", "1", "0", "abc"),   # push
                 ("%0", "111", "/a", "1", "1", "1", "abc")]   # podium-push
        dup = _match_agents(rows5, by_pid2)
        assert len(dup) == 1 and dup[0]["focused"], dup

        # a claude that has not started a session yet: no entry in
        # `claude agents --json`, but the process is right there. herdr shows
        # it, so must we -- dropping it takes the pad off the Push until
        # someone types into it.
        table = {900: (1, "login"), 901: (900, "claude")}
        rows4 = [("%9", "900", "/repo", "0", "0", "0", "")]
        [a] = _match_agents(rows4, {}, table)
        assert a["agent_status"] == "unknown", a
        assert a["agent_session"]["value"] is None
        assert a["cwd"] == "/repo", "no session, so the pane's cwd is all we have"
        assert a["engine"] == "claude"

        # a pane attached to a background session: the session's pid is the
        # bg-spare daemon's, outside the pane's process tree. Captured shape
        # from the live `story` pane that stalled the queue.
        bg = {"pid": 31582, "kind": "background", "cwd": "/story", "sessionId": "sss",
              "status": "busy", "state": "blocked"}
        table6 = {700: (1, "zsh"), 701: (700, "claude"), 31582: (1, "claude")}
        [c] = _match_agents([("%2", "700", "/story", "0", "0", "0", "")], {31582: bg},
                            table6, background=[bg])
        assert (c["agent_status"], c["agent_session"]["value"]) == ("idle", "sss"), c
        # two unmatched panes in that cwd: which one owns it is a guess, so neither does
        two = _match_agents([("%2", "700", "/story", "0", "0", "0", ""),
                             ("%3", "702", "/story", "0", "0", "0", "")], {31582: bg},
                            {**table6, 702: (1, "zsh"), 703: (702, "claude")}, background=[bg])
        assert [a["agent_status"] for a in two] == ["unknown", "unknown"], two
        # a session some pane already holds by pid is not handed out again
        held = _match_agents([("%4", "704", "/story", "0", "0", "0", ""),
                              ("%2", "700", "/story", "0", "0", "0", "")],
                             {31582: bg, 705: {**bg, "pid": 705}},
                             {**table6, 704: (1, "zsh"), 705: (704, "claude")},
                             background=[{**bg, "pid": 705}])
        assert [a["agent_session"]["value"] for a in held] == ["sss", None], held

        # a shell with no claude under it is not an agent and must not appear
        assert _match_agents([("%8", "900", "/repo", "0", "0", "0", "")], {},
                             {900: (1, "login")}) == []

        # a session found under a shell still wins over the bare-process path
        table5 = {800: (1, "login"), 801: (800, "claude")}
        [b] = _match_agents([("%7", "800", "/x", "0", "0", "0", "")],
                            {801: {"cwd": "/real", "status": "busy",
                                   "sessionId": "ccc"}}, table5)
        assert (b["agent_status"], b["cwd"], b["engine"]) == ("working", "/real", "claude"), b

        # a codex pane -- no pid join exists for it (claude agents --json
        # knows nothing about codex), so it shows up on process alone, engine
        # "codex", status "unknown" always (see _find_engine's docstring for
        # why that is deliberate, not a gap)
        table_codex = {700: (1, "login"), 701: (700, "codex")}
        [c] = _match_agents([("%6", "700", "/cx", "0", "0", "0", "")], {}, table_codex)
        assert c["engine"] == "codex", c
        assert c["agent_status"] == "unknown", c
        assert c["cwd"] == "/cx", c

        # one pane each of claude and codex, sharing nothing -- both come
        # back, correctly labelled, neither mistaken for the other
        table_mixed = {600: (1, "login"), 601: (600, "claude"),
                       650: (1, "login"), 651: (650, "codex")}
        mixed = _match_agents(
            [("%5", "600", "/a", "0", "0", "0", ""),
             ("%4", "650", "/b", "0", "0", "0", "")],
            {}, table_mixed)
        by_engine = {m["terminal_id"]: m["engine"] for m in mixed}
        assert by_engine == {"%5": "claude", "%4": "codex"}, by_engine

        # rollout linking: a codex agent's session id comes from the newest
        # rollout whose session_meta.cwd matches the pane's cwd -- cheap
        # (first line of a handful of files), not a claim about which PANE
        # wrote it (see _codex_rollout_for's docstring on the two-panes-one-
        # cwd limit)
        import tempfile
        rollout_dir = tempfile.mkdtemp()
        rollout_path = os.path.join(rollout_dir, "rollout-x.jsonl")
        with open(rollout_path, "w") as f:
            f.write(json.dumps({"type": "session_meta",
                                "payload": {"cwd": "/cx", "session_id": "cdx-1"}}) + "\n")
        real_glob, real_cache = CODEX_SESSIONS_GLOB, dict(_codex_rollout_cache)
        globals()["CODEX_SESSIONS_GLOB"] = os.path.join(rollout_dir, "rollout-*.jsonl")
        _codex_rollout_cache["at"] = -1e9
        try:
            row = _codex_rollout_for("/cx")
            assert row and row["session_id"] == "cdx-1", row
            assert _codex_rollout_for("/nowhere-near") is None
            [d] = _match_agents([("%3", "700", "/cx", "0", "0", "0", "")], {}, table_codex)
            assert d["agent_session"]["value"] == "cdx-1", d
        finally:
            globals()["CODEX_SESSIONS_GLOB"] = real_glob
            _codex_rollout_cache.clear()
            _codex_rollout_cache.update(real_cache)
            import shutil as _shutil
            _shutil.rmtree(rollout_dir, ignore_errors=True)

        # _engine_argv defers to engines.launch_argv (lazily imported, same
        # pattern as _with_access/access.py) and comes back None -- never
        # raises -- when that raises, so _agent_start can report bad_engine
        # instead of crashing. A fake module stands in for engines.py here so
        # this file's own self-test never touches ~/.podium or the Keychain;
        # engines.py's own launch_argv is test_engines.py's job.
        import sys
        import types
        fake_engines = types.ModuleType("engines")
        calls = []

        def fake_launch_argv(engine, overrides=None):
            calls.append((engine, overrides))
            if engine == "codex":
                return ["codex", "-s", "workspace-write"]
            raise ValueError(f"unknown engine: {engine!r}")

        fake_engines.launch_argv = fake_launch_argv
        real_engines_mod = sys.modules.get("engines")
        sys.modules["engines"] = fake_engines
        try:
            assert _engine_argv("codex", "") == ["codex", "-s", "workspace-write"]
            assert calls[-1] == ("codex", None), calls
            assert _engine_argv("codex", "gpt-5.1")[0] == "codex"
            assert calls[-1] == ("codex", {"model": "gpt-5.1"}), calls
            assert _engine_argv("not-a-real-engine", "") is None
        finally:
            if real_engines_mod is None:
                sys.modules.pop("engines", None)
            else:
                sys.modules["engines"] = real_engines_mod

        # push_cc's slug() keeps "/" on purpose, and its fallback branch name
        # is push/HHMMSS -- straight into a path that is a stray directory.
        assert _branch_slug("feature/thing") == "feature-thing"
        assert _branch_slug("push/123456") == "push-123456"
        assert _branch_slug("Fix--The__Thing") == "fix-the-thing"
        assert _branch_slug("///") == "wt", "a path component is never empty"


        # agent rename: bad names are rejected on format alone, before any
        # tmux call is made
        for bad in ("Uppercase", "9leading", "a" * 40, ""):
            r = json.loads(_agent_rename("%0", bad))
            assert r.get("error", {}).get("code") == "invalid_agent_name", (bad, r)

        # valid-name acceptance and the duplicate check both need to know who
        # else holds a name -- fake that one tmux call so this stays pure
        # fixtures, same as the rest of demo()
        global _run
        real_run = _run

        def fake_run(cmd, timeout=10):
            if cmd[:2] == ["tmux", "list-panes"]:
                return subprocess.CompletedProcess(cmd, 0, "%0 \n%1 bob\n", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        _run = fake_run
        try:
            r = json.loads(_agent_rename("%0", "alice"))
            assert r.get("result") == {}, r

            r = json.loads(_agent_rename("%0", "bob"))
            assert r.get("error", {}).get("code") == "agent_name_taken", r

            # %1 already IS bob -- that is not "another pane" holding the name
            r = json.loads(_agent_rename("%1", "bob"))
            assert r.get("result") == {}, "renaming to your own current name is not a collision"
        finally:
            _run = real_run

        assert "agent_name_taken" in _err("agent_name_taken", "window x taken")

        # the branch verbs, on this repo -- both take real git, so the checks
        # are the ones that need no writes: the current branch is in the list
        # and is marked as held by the checkout it is actually out in, and a
        # missing flag is refused before git is ever run.
        here = os.path.dirname(os.path.abspath(__file__))
        got = json.loads(_worktree_branches(("worktree", "branches", "--cwd", here)))
        if "result" in got:                    # skip where this is not a repo
            names = [b["name"] for b in got["result"]["branches"]]
            cur = _run(["git", "-C", here, "branch", "--show-current"]).stdout.strip()
            if cur:
                assert cur in names, (cur, names)
                held = next(b for b in got["result"]["branches"] if b["name"] == cur)
                assert held["at"], "a branch that is checked out names its worktree"
        for bad in (("worktree", "branches"),
                    ("worktree", "switch", "--path", here),
                    ("worktree", "switch", "--branch", "main")):
            r = json.loads(dispatch(bad))
            assert r.get("error", {}).get("code") == "bad_args", (bad, r)
        r = json.loads(dispatch(("worktree", "switch", "--path", "/no/such/dir",
                                 "--branch", "main")))
        assert r.get("error", {}).get("code") == "worktree_missing", r

        # `claude agents --json` runs once per CLAUDE_AGENTS_TTL, however many
        # callers ask inside it -- uncached it never stopped running
        real_run, runs = _run, []

        def counting_run(cmd, timeout=10):
            runs.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, '[{"pid": 1}]', "")

        _run = counting_run
        try:
            _claude_agents_cache["at"] = -1e9
            assert _claude_agents() == _claude_agents() == '[{"pid": 1}]'
            assert len(runs) == 1, runs
            _claude_agents_cache["at"] = -1e9       # expired: asked again
            _claude_agents()
            assert len(runs) == 2, runs
        finally:
            _run = real_run
            _claude_agents_cache["at"] = -1e9

        print("term.py demo: ok")

    demo()
