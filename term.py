#!/usr/bin/env python3
"""tmux backend for push_cc's herdr() -- same eight calls, same JSON shapes.

herdr never sees more than one screen of a claude pane either (it runs on the
alternate screen and never scrolls), so nothing here is a downgrade. The one
new idea is the pid join in agent list: claude agents --json knows cwd,
session id and status for every claude on the machine, tmux knows which pane
is which -- pid is the only key that is safe to join them on, because two
agents commonly share a cwd.
"""
import json
import os
import re
import subprocess

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
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        return subprocess.CompletedProcess(cmd, 1, "", str(e))


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


def _status(claude_status):
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


def _find_claude_pid(pane_pid, by_pid, table):
    """Two questions, not one: is there a claude in this pane at all, and does
    `claude agents --json` know a session for it?

    herdr identifies the agent by process and layers state on top, and it is
    right to. A claude sitting in the agents view has not started a session,
    so it appears nowhere in `claude agents --json` -- joining on that alone
    drops the pane off the Push entirely until someone types into it. The
    process is what says an agent is there; the session is enrichment.

    Returns (session_pid or None, claude_is_running).
    """
    line = _descendants(pane_pid, table)
    known = next((p for p in line if p in by_pid), None)
    # `claude bg-spare` is a daemon, not a session -- basename match only
    running = any(table.get(p, (0, ""))[1].rsplit("/", 1)[-1] == "claude"
                  for p in line)
    return known, running


def _match_agents(rows, by_pid, table=None):
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
    once per session in the group. ptybridge's `midiai-push` view is one --
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
        cpid, running = _find_claude_pid(int(pane_pid), by_pid, table)
        if cpid is None and not running:
            continue                    # no agent here, just a shell
        info = by_pid.get(cpid) or {}
        agents.append({
            "terminal_id": pane_id,
            "pane_id": pane_id,
            "cwd": info.get("cwd") or cwd,
            # a claude with no session yet is present but unclassified, which
            # is what `unknown` is for. idle would be a claim we cannot make.
            "agent_status": _status(info.get("status")) if info else "unknown",
            "focused": focused,
            "agent_session": {"value": info.get("sessionId")},
            "name": name or None,
        })
        seen[pane_id] = agents[-1]
    return agents


def _agent_list():
    panes = _run(["tmux", "list-panes", "-a", "-F", PANE_FORMAT])
    if panes.returncode != 0:
        # No server is not a failure, it is an empty machine -- the state you
        # are in the moment herdr closes and before the first Add Device.
        # Reporting it as an error puts two lines a second into push.log
        # forever, and the surface would read the same either way.
        return _ok({"agents": [], "type": "agent_list"})
    claude_out = _run(["claude", "agents", "--json"])
    try:
        claude_agents = json.loads(claude_out.stdout or "[]")
    except json.JSONDecodeError:
        claude_agents = []
    by_pid = {a["pid"]: a for a in claude_agents if "pid" in a}
    rows = [tuple(line.split("\t")) for line in panes.stdout.splitlines() if line.strip()]
    rows = [r for r in rows if len(r) == 7]
    return _ok({"agents": _match_agents(rows, by_pid), "type": "agent_list"})


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


def _open_pane(cwd, argv):
    """A pane running argv in cwd -- a new window in the push session, or a
    new session if this is the first pane on the machine.

    Every path that opens one comes through here. _agent_start asked whether
    the server was up and the worktree verbs did not, so creating or opening
    a worktree worked all day and then failed the morning after the last agent
    was closed -- tmux's own "no server running" surfacing to the app as a 500.
    The question has one answer now, in one place.
    """
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
    argv = rest[rest.index("--") + 1:] if "--" in rest else []

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
        out = _run(["tmux", "split-window", "-h" if split == "right" else "-v",
                    "-c", cwd, "-P", "-F", "#{pane_id}", "--", *argv])
    else:
        out = _open_pane(cwd, argv)
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
    dest = os.path.join(WORKTREE_ROOT, os.path.basename(cwd),
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
    def demo():
        assert _status("busy") == "working"
        assert _status("waiting") == "blocked"
        assert _status("idle") == "idle"
        assert _status("anything-else") == "idle"

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

        # a grouped session (ptybridge's midiai-push view) lists every pane
        # once per session: still one agent, focused if any copy is
        rows5 = [("%0", "111", "/a", "1", "1", "0", "abc"),   # push
                 ("%0", "111", "/a", "1", "1", "1", "abc")]   # midiai-push
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

        # a shell with no claude under it is not an agent and must not appear
        assert _match_agents([("%8", "900", "/repo", "0", "0", "0", "")], {},
                             {900: (1, "login")}) == []

        # a session found under a shell still wins over the bare-process path
        table5 = {800: (1, "login"), 801: (800, "claude")}
        [b] = _match_agents([("%7", "800", "/x", "0", "0", "0", "")],
                            {801: {"cwd": "/real", "status": "busy",
                                   "sessionId": "ccc"}}, table5)
        assert (b["agent_status"], b["cwd"]) == ("working", "/real"), b

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

        print("term.py demo: ok")

    demo()
