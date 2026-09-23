#!/usr/bin/env python3
"""A real terminal behind the app: one pty per view, attached to tmux.

The focus view's terminal tab was a picture of the pane with a keyboard taped
to it -- `capture-pane` strips the colour, polling rules out a cursor, and
there is no mouse and no scrollback. None of that is fixable from the app
side. This is the other answer: a pty running `tmux attach`, its bytes
streamed to xterm.js, which is a terminal and therefore has all four.

Two things here are not obvious and both are about not disturbing the user's
own terminals:

  * The pty attaches to a GROUPED session, never to `push` directly. tmux
    sizes a session to its smallest attached client, so attaching the app
    beside a real Terminal window would shrink that window to the browser's
    idea of a terminal. A session created with `new-session -t` shares the
    windows and keeps its own size, so the two views coexist.
  * Killing that grouped session on the way out does not kill the windows.
    They belong to the group, not to the view.

`python3 ptybridge.py` runs the self-check.
"""
import os
import pty
import select
import signal
import struct
import subprocess
import termios
import threading
import time
import fcntl

# A view is cheap to make and cheap to lose, so it is reaped soon after the
# last reader goes -- but not instantly, or a page refresh would cost you the
# scrollback it took a day to fill.
IDLE_REAP = 90.0
READ_CHUNK = 65536

VIEW_PREFIX = "midiai-"


def _run(argv, timeout=10):
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def session_of(pane):
    """The tmux session a pane lives in, or ''."""
    out = _run(["tmux", "display-message", "-p", "-t", pane, "#{session_name}"])
    return out.stdout.strip() if out.returncode == 0 else ""


def base_session(name):
    """The real session behind a name that may already be a view.

    Grouped sessions share their windows, so `display-message -t <pane>` can
    answer with the VIEW's name rather than the session the pane was made in.
    Prefixing that again produced `midiai-midiai-push`, a view of a view, and
    a fresh one on every reconnect. The prefix comes off before it goes on."""
    while name.startswith(VIEW_PREFIX):
        name = name[len(VIEW_PREFIX):]
    return name


def view_for(session):
    """A session grouped with `session`, made if it is not there yet.

    Grouped rather than the same session: tmux sizes a session to its smallest
    client, and the app must not be the reason someone's Terminal window went
    80 columns wide."""
    session = base_session(session or "")
    if not session:
        return ""            # `new-session -t ""` groups with whatever is
                             # current and leaves a session called `midiai-`
    name = VIEW_PREFIX + session
    if _run(["tmux", "has-session", "-t", name]).returncode != 0:
        made = _run(["tmux", "new-session", "-d", "-s", name, "-t", session])
        if made.returncode != 0:
            return ""
    return name


def clients_of(view):
    """How many of our own clients are on a view. Two is a leak, not a feature:
    tmux sizes a session to its smallest client, so a second one of ours makes
    the app squeeze itself."""
    out = _run(["tmux", "list-clients", "-t", view, "-F", "#{client_tty}"])
    return len(out.stdout.split()) if out.returncode == 0 else 0


def where(pane):
    """Which pane the app's own tmux client is looking at now.

    The app can offer a terminal or it can decide what the terminal shows, not
    both -- tmux keeps the active pane per window and shares it between every
    client, so moving it from here would move somebody else's cursor. Clicking
    is therefore tmux's business, and this is how the app finds out what
    happened: it asks its own client, and follows."""
    slot = slot_for(pane)
    if not slot:
        return ""
    out = _run(["tmux", "display-message", "-p", "-t", slot["view"], "#{pane_id}"])
    return out.stdout.strip() if out.returncode == 0 else ""


def redraw(view):
    """Make tmux repaint the whole screen into the pty.

    A view outlives the connection reading it, which is the point -- a refresh
    should put you back where you were. But the screen tmux painted on attach
    went to the reader that has since gone, so a reconnecting one sees nothing
    at all until the agent happens to print something. This is the repaint that
    makes a new reader's first frame the current screen rather than the next
    keystroke.

    `refresh-client -t` wants a CLIENT -- a tty -- and not a session, which is
    the mistake that made this silently do nothing and left a fresh terminal
    blank but for a cursor. The clients of the view have to be looked up and
    refreshed one at a time."""
    out = _run(["tmux", "list-clients", "-t", view, "-F", "#{client_tty}"])
    if out.returncode != 0:
        return False
    ttys = [t for t in out.stdout.split() if t]
    for tty in ttys:
        _run(["tmux", "refresh-client", "-t", tty])
    return bool(ttys)


class Pty:
    """One pty, its child, and the lock that keeps two writers apart."""

    def __init__(self, argv, cols=120, rows=32):
        self.argv = argv
        self.lock = threading.Lock()
        self.touched = time.time()
        self.pid, self.fd = pty.fork()
        if self.pid == 0:                      # child: becomes the command
            os.environ["TERM"] = "xterm-256color"
            try:
                os.execvp(argv[0], argv)
            finally:
                os._exit(1)                    # execvp only returns on failure
        self.resize(cols, rows)

    @property
    def alive(self):
        if self.pid <= 0:
            return False
        try:
            return os.waitpid(self.pid, os.WNOHANG) == (0, 0)
        except ChildProcessError:
            return False

    def read(self, timeout=0.4):
        """Whatever is there, or b'' -- never a block long enough to hold a
        request open past the point a client would give up on it."""
        try:
            ready, _, _ = select.select([self.fd], [], [], timeout)
            if not ready:
                return b""
            chunk = os.read(self.fd, READ_CHUNK)
        except (OSError, ValueError):
            return b""
        return chunk

    def repaint(self, cols, rows):
        """Make tmux draw the whole screen again, for a reader that has just
        arrived and has nothing.

        Replaying our own captured bytes was the obvious answer and the wrong
        one: they were written for whatever size the terminal was THEN, so
        replaying them into a differently sized xterm lays two geometries on
        top of each other and the screen comes out shredded.

        A size change is the one thing tmux always redraws for -- it cannot
        believe the client is up to date across a resize. So the size is
        nudged and put back, and what arrives is the real current screen at
        the real current size. `refresh-client` will not do: tmux sends only
        what changed, and it has no idea the last reader took the screen with
        it."""
        self.resize(cols, max(2, int(rows or 24) - 1))
        time.sleep(0.05)
        return self.resize(cols, rows)

    def write(self, data):
        with self.lock:
            try:
                os.write(self.fd, data)
                return True
            except (OSError, ValueError):
                return False

    def resize(self, cols, rows):
        """The one thing a scraped view could never do. tmux resizes the
        session to match, which is why the session is a grouped one."""
        cols = max(20, min(500, int(cols or 80)))
        rows = max(5, min(200, int(rows or 24)))
        try:
            fcntl.ioctl(self.fd, termios.TIOCSWINSZ,
                        struct.pack("HHHH", rows, cols, 0, 0))
            return True
        except (OSError, ValueError):
            return False

    def close(self):
        try:
            os.close(self.fd)
        except (OSError, ValueError):
            pass
        if self.pid > 0:
            for sig in (signal.SIGHUP, signal.SIGKILL):
                try:
                    os.kill(self.pid, sig)
                    os.waitpid(self.pid, os.WNOHANG)
                except (ProcessLookupError, ChildProcessError, OSError):
                    break
        self.pid = -1


# Keyed by the tmux SESSION, not the pane. Every pane of a session shares one
# view because the terminal shows the whole window anyway -- a pty per pane
# meant several of our own clients attached to one session, and tmux sizes a
# session to its smallest client, so the app was squeezing itself.
_views = {}                # session -> {"pty": Pty, "view": str, "readers": int}
_views_lock = threading.Lock()


def open_view(pane, cols=120, rows=32):
    """The pty for this pane, made if there is not one yet.

    Reused across reconnects on purpose: a refresh should put you back where
    you were, not in a fresh shell with an empty screen."""
    session = base_session(session_of(pane))
    if not session:
        return None, "no tmux pane by that name"
    with _views_lock:
        slot = _views.get(session)
        if slot and slot["pty"].alive:
            slot["pty"].touched = time.time()
            return slot, ""
        if slot:
            slot["pty"].close()
        view = view_for(session)
        if not view:
            return None, "could not make a view session"
        slot = {"pty": Pty(["tmux", "attach", "-t", view], cols, rows),
                "view": view, "readers": 0, "session": session}
        _views[session] = slot
    # Wait for tmux to actually attach before returning, so the first read
    # finds the attach's own paint rather than an empty buffer.
    for _ in range(20):
        if clients_of(view):
            break
        time.sleep(0.05)
    focus_pane(pane)          # land on the agent asked for, not the group's last
    return slot, ""


def focus_pane(pane):
    """Point the app's own view at the window holding this pane.

    Window SELECTION is per session even inside a group, so this moves the
    app's client and nobody else's. Layout and zoom are not: they belong to
    the window and every client in the group shares them, which is why the app
    cannot show one pane of a split to itself alone -- zooming here zooms the
    terminal you have open beside it. One pane per view needs one pane per
    window, which is a question about how agents are started, not about this.
    """
    slot = slot_for(pane)
    if not slot:
        return False
    win = _run(["tmux", "display-message", "-p", "-t", pane,
                "#{window_id}"]).stdout.strip()
    if not win:
        return False
    return _run(["tmux", "select-window", "-t",
                 f"{slot['view']}:{win}"]).returncode == 0


def slot_for(pane):
    """The view a pane is served by, if one is open."""
    return _views.get(base_session(session_of(pane)))


def close_view(pane):
    with _views_lock:
        slot = _views.pop(base_session(session_of(pane)), None)
    if not slot:
        return False
    slot["pty"].close()
    # the windows belong to the group, not to this view -- killing it takes
    # away the client's own session and nothing anybody is working in
    _run(["tmux", "kill-session", "-t", slot["view"]])
    return True


def reap(now=None):
    """Views nobody is reading and nobody came back to. Returns how many."""
    now = time.time() if now is None else now
    gone = []
    with _views_lock:
        for session, slot in list(_views.items()):
            idle = now - slot["pty"].touched
            if slot["readers"] <= 0 and idle > IDLE_REAP or not slot["pty"].alive:
                slot["pty"].close()
                gone.append(slot["view"])
                _views.pop(session, None)
    for view in gone:
        if view and view != VIEW_PREFIX:      # never kill a bare `midiai-`
            _run(["tmux", "kill-session", "-t", view])
    return len(gone)


def demo():
    assert base_session("push") == "push"
    assert base_session(VIEW_PREFIX + "push") == "push", "a view is not a session"
    assert base_session(VIEW_PREFIX * 3 + "push") == "push", "however deep it got"
    assert base_session("") == "" and view_for("") == "", \
        "no session means no view -- `new-session -t \"\"` makes `midiai-`"
    # checked on the name, not by calling view_for -- a self-check that makes
    # tmux sessions leaves them behind, and this one did (`midiai-x`)
    assert VIEW_PREFIX + base_session(VIEW_PREFIX + "x") == VIEW_PREFIX + "x", \
        "never a view of a view"

    """Runs against real tmux where there is one, and says so where there is
    not -- the parts that need no server are checked either way."""
    assert view_for("") == "" or True
    p = Pty(["/bin/cat"], cols=90, rows=25)
    assert p.alive, "a pty that just forked is alive"
    assert p.write(b"hello pty\n"), "and takes input"
    seen = b""
    for _ in range(20):
        seen += p.read(0.2)
        if b"hello pty" in seen:
            break
    assert b"hello pty" in seen, f"cat echoes what it is given, got {seen!r}"
    assert p.resize(100, 30), "resize is the thing a scrape could never do"
    assert p.repaint(100, 30), "and a nudged resize is how a new reader gets a screen"
    p.close()
    assert not p.alive, "and it is gone when closed"

    dead = Pty(["/bin/sh", "-c", "exit 0"])
    time.sleep(0.3)
    dead.read(0.1)
    assert not dead.alive, "a child that exited does not read as alive"
    dead.close()

    if _run(["tmux", "has-session"]).returncode != 0:
        print("ptybridge: ok (no tmux server -- attach paths skipped)")
        return
    assert session_of("%nope") == "", "a pane that is not there has no session"
    assert open_view("%nope")[0] is None, "and cannot be opened"
    print("ptybridge: ok")


if __name__ == "__main__":
    demo()
