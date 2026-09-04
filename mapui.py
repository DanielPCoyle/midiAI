#!/usr/bin/env python3
"""The midiAI API for the Push 2: macros.json read/write, the live surface
mirror, and pad firing. The editor UI itself is the Expo app in ./app --
this file only serves the data it needs.

    python3 mapui.py        then, in app/, npx expo start
    python3 mapui.py --lan  binds 0.0.0.0 so an iPad on the LAN can reach it
"""
import argparse
import base64
import binascii
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import push_cc

PORT = 8765
PUSH_CC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "push_cc.py")
PUSH_LOG = "/tmp/push.log"

# Hold-to-talk for the app, which is the same trade the Push's own hold makes
# in reverse. Claude Code's voice owns record/transcribe/submit end to end and
# hands back no text, so a tablet holding its button would get words it could
# not edit -- the whole complaint. Recording here instead costs a transcribe
# after release and gives up the text, which is the point.
#
# The mic is this machine's, not the tablet's. Expo Go has no Web Speech API,
# so a browser-side recogniser would leave the iPad with nothing, and the mic
# worth talking into is the one already next to the Push.
WHISPER_MODEL = os.environ.get("MIDIAI_WHISPER_MODEL") or os.path.expanduser(
    "~/.cache/openwhispr/whisper-models/ggml-base.bin")
RECORD_MAX_S = 120                 # a stuck button must not fill the disk
_rec_lock = threading.Lock()
_rec = None                        # (Popen, wav path) while one is running


# Where pasted images land. Not the repo -- these are scratch, and a stray
# screenshot in `git status` is noise every agent then has to read past.
PASTE_DIR = os.path.expanduser("~/.midiai/pastes")
PASTE_MAX = 20 * 1024 * 1024

# The type is taken from the bytes, never from the name the client sent: the
# name is the one part of an upload an attacker picks, and this server binds
# to the LAN. Nothing here writes a path the caller chose.
MAGIC = [(b"\x89PNG\r\n\x1a\n", ".png"), (b"\xff\xd8\xff", ".jpg"),
         (b"GIF87a", ".gif"), (b"GIF89a", ".gif")]


def image_ext(raw):
    """The extension these bytes have earned, or "" if they are not an image."""
    for sig, ext in MAGIC:
        if raw.startswith(sig):
            return ext
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return ".webp"
    return ""


def voice_missing():
    """Why dictation cannot run, in the words of the thing that is absent.

    Three different missing pieces fail identically at the button -- no
    recorder, no transcriber, no model -- and only this can tell them apart."""
    if not shutil.which("rec"):
        return "no sox on PATH: brew install sox"
    if not shutil.which("whisper-cli"):
        return "no whisper-cli on PATH: brew install whisper-cpp"
    if not os.path.exists(WHISPER_MODEL):
        return f"no whisper model at {WHISPER_MODEL} (set MIDIAI_WHISPER_MODEL)"
    return ""


# Whisper narrates the silence it is given -- "(crickets chirping)", "[BLANK
# AUDIO]" -- and a button tapped by accident is silence. Those belong to the
# transcriber, not to anything anyone said, so they never reach the box.
NOISE = re.compile(r"[\(\[][^)\]]*[\)\]]")


def transcribe(wav):
    """The words in a wav file, or "" if it holds none."""
    out = subprocess.run(
        ["whisper-cli", "-m", WHISPER_MODEL, "-f", wav,
         "-nt", "-np", "-l", "en"],
        capture_output=True, text=True, timeout=120)
    return " ".join(NOISE.sub(" ", out.stdout).split())


def push_state():
    """Why the Push is or is not working, rather than just whether it is.

    Three separate things look identical from the outside: the process being
    down, the device off USB, and the device sitting in Live mode."""
    running = subprocess.run(["pgrep", "-f", "push_cc.py"],
                             capture_output=True).returncode == 0
    try:
        import mido
        midi = any("Push 2 User Port" in n for n in mido.get_output_names())
    except Exception:
        midi = False
    try:
        import usb.core
        usb_ok = usb.core.find(idVendor=0x2982, idProduct=0x1967) is not None
    except Exception:
        usb_ok = False
    if not usb_ok and not midi:
        why = "Push not on USB \u2014 check the cable, or the dock"
    elif not midi:
        why = "no User Port \u2014 press User on the Push"
    elif not running:
        why = "push_cc is not running"
    else:
        why = "connected"
    return {"running": running, "midi": midi, "usb": usb_ok, "why": why,
            "ok": running and midi}


def relaunch():
    subprocess.run(["pkill", "-f", "push_cc.py"], capture_output=True)
    time.sleep(1.2)                      # let it drop the MIDI and USB handles
    log = open(PUSH_LOG, "a")
    subprocess.Popen([sys.executable, PUSH_CC, "--debug"],
                     stdout=log, stderr=subprocess.STDOUT,
                     start_new_session=True)   # must outlive this request
    time.sleep(2.5)
    return push_state()


API_NOTE = (
    "midiAI API for the Push 2.\n"
    "\n"
    "The UI is the Expo app in ./app -- run `npx expo start` there and open\n"
    "it in Expo Go on the iPad, or `npx expo start --web` for a browser.\n"
)


def live_page():
    """Which bank of pads the Push is showing. push_cc owns that number -- this
    process imports the same module but never runs its loop, so asking our own
    copy would always answer page one."""
    try:
        with open(push_cc.SURFACE_FILE) as f:
            return int(json.load(f).get("page") or 0)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return 0


def read_target():
    """Which session the Push is on. Empty if push_cc is not running."""
    try:
        with open(push_cc.TARGET_FILE) as f:
            return json.load(f) or {}
    except (OSError, json.JSONDecodeError):
        return {}


def target_cwd():
    """cwd of whichever agent the Push is pointed at, else the process cwd.
    .target.json only carries terminal_id/name/status -- the cwd has to come
    from a join against push_cc.agents(), same pid-not-cwd rule as term.py."""
    tid = read_target().get("terminal_id")
    agent = next((a for a in push_cc.agents() if a.get("terminal_id") == tid), None)
    return (agent or {}).get("cwd") or os.getcwd()


def _is_inside(path, base):
    """True if `path` is `base` or a descendant of it. A bare startswith would
    let /a/bc match /a/b -- commonpath does not."""
    path, base = os.path.realpath(path), os.path.realpath(base)
    try:
        return os.path.commonpath([path, base]) == base
    except ValueError:
        return False


def herdr_error(text):
    """herdr()'s stdout, parsed for an error object -- None on success or on
    anything that fails to parse, same leniency herdr() itself uses."""
    try:
        return json.loads(text).get("error")
    except (json.JSONDecodeError, AttributeError):
        return None


# The repos worth listing, which is a different question from "where is an
# agent right now". Nothing on the wire knew a repo existed until something
# was running in it, so the app's PROJECTS group could only ever show you what
# you were already doing. This file is the memory: a repo lands in it the
# first time an agent runs there, or when someone adds it, and stays until it
# is explicitly forgotten. Beside the pastes rather than in the repo -- it is
# a fact about this machine, not about any one checkout.
PROJECTS_FILE = os.path.expanduser("~/.midiai/projects.json")


def load_projects():
    try:
        with open(PROJECTS_FILE) as f:
            got = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    return [p for p in got if isinstance(p, str)] if isinstance(got, list) else []


def save_projects(paths):
    """Best effort. A list that cannot be written is not a reason to fail the
    GET that was only ever passing through here on its way to an answer."""
    try:
        os.makedirs(os.path.dirname(PROJECTS_FILE), exist_ok=True)
        tmp = PROJECTS_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(sorted(set(paths)), f, indent=1)
        os.replace(tmp, PROJECTS_FILE)
    except OSError as e:
        print(f"projects: {e}", file=sys.stderr, flush=True)


def repo_worktrees(cwd):
    """One directory's worktrees, or None if it is not a repo at all.

    git always lists the main checkout first, so rows[0] IS the project -- which
    is how a path anywhere inside a repo, worktree included, resolves to the one
    entry that stands for it."""
    if not cwd or not os.path.isdir(cwd):
        return None
    out = push_cc.herdr("worktree", "list", "--cwd", cwd)
    if herdr_error(out):
        return None
    try:
        rows = json.loads(out).get("result", {}).get("worktrees", [])
    except json.JSONDecodeError:
        return None
    return rows or None


class Handler(BaseHTTPRequestHandler):
    # the app runs from Metro or Expo Go, never this origin, so every
    # response needs this or the browser build just can't read it
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")

    def _send(self, code, body, ctype):
        body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _raw(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if self.path.startswith("/frame.png"):
            try:
                with open(push_cc.FRAME_FILE, "rb") as f:
                    return self._raw(200, f.read(), "image/png")
            except OSError:       # push_cc down, or no screen to mirror yet
                return self._send(404, "no frame", "text/plain")
        if self.path == "/surface":
            try:
                with open(push_cc.SURFACE_FILE) as f:
                    return self._send(200, f.read(), "application/json")
            except OSError:
                return self._send(200, "{}", "application/json")
        if self.path == "/target":
            return self._send(200, json.dumps({**read_target(), **push_state()}),
                              "application/json")
        if self.path.startswith("/dirs"):
            return self._dirs()
        if self.path.startswith("/choose-dir"):
            return self._choose_dir()
        if self.path == "/macros":
            labels, pages = push_cc.load_macros()
            self._send(200, json.dumps({"labels": labels, "pages": pages,
                                        # the page the Push is on, so the app
                                        # opens on the grid you are looking at
                                        "page": live_page()}),
                       "application/json")
        elif self.path == "/agents":
            self._send(200, json.dumps({"agents": push_cc.agents()}),
                      "application/json")
        elif self.path == "/worktrees" or self.path.startswith("/worktrees?"):
            self._worktrees_list()
        elif self.path == "/projects":
            self._projects()
        elif self.path == "/branches" or self.path.startswith("/branches?"):
            self._branches()
        else:
            self._send(200, API_NOTE, "text/plain")

    def do_POST(self):
        if self.path == "/fire":
            return self._fire()
        if self.path == "/press":
            return self._press()
        if self.path == "/relaunch":
            state = relaunch()
            return self._send(200, json.dumps(state), "application/json")
        if self.path == "/agents":
            return self._agents_create()
        if self.path == "/agents/close":
            return self._agents_close()
        if self.path == "/agents/rename":
            return self._agents_rename()
        if self.path == "/worktree":
            return self._worktree()
        if self.path == "/worktrees/remove":
            return self._worktrees_remove()
        if self.path == "/projects":
            return self._projects_add()
        if self.path == "/projects/remove":
            return self._projects_remove()
        if self.path == "/worktrees/switch":
            return self._worktrees_switch()
        if self.path == "/worktrees/open":
            return self._worktrees_open()
        if self.path == "/prompt":
            return self._prompt()
        if self.path == "/paste":
            return self._paste()
        if self.path == "/record/start":
            return self._record_start()
        if self.path == "/record/stop":
            return self._record_stop()
        if self.path != "/macros":
            return self._send(404, "no", "text/plain")
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            entries = json.loads(raw)
        except json.JSONDecodeError:
            return self._send(400, "bad json", "text/plain")
        # go through load_macros' shape so the file can only ever hold what the
        # Push can actually use -- 64 slots a page, label derived when missing
        labels = entries.get("labels") or []
        pages = entries.get("pages") or [entries.get("pads") or []]
        push_cc.save_macros(
            [[None if not e else
              {"label": e.get("label") or push_cc.label_for(e.get("text", "")),
               "text": e.get("text", ""),
               "colour": int(e.get("colour", push_cc.BLUE)) & 0x7F,
               "tag": e.get("tag"), "submit": bool(e.get("submit"))}
              for e in (pg or [])[:push_cc.MACRO_SLOTS]] for pg in pages],
            labels)
        self._send(200, "ok", "text/plain")

    def _fire(self):
        """Run a pad against whichever session the Push has selected."""
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            body = json.loads(raw)
            index = int(body["index"])
            page = int(body.get("page", live_page()))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return self._send(400, "bad request", "text/plain")
        target = read_target().get("terminal_id")
        if not target:
            return self._send(409, "no session selected on the Push", "text/plain")
        _, pages = push_cc.load_macros()
        macros = pages[page] if 0 <= page < len(pages) else []
        m = macros[index] if 0 <= index < len(macros) else None
        if not m:
            return self._send(404, "empty pad", "text/plain")
        push_cc.herdr("agent", "send", target,
                      m["text"] + ("\r" if m["submit"] else ""))
        self._send(200, f"{m['label']}{' + enter' if m['submit'] else ''}",
                   "text/plain")

    def _press(self):
        """A button on the mirror, or any of the ~54 controls the Push itself
        maps -- push_cc drains the queue on its next poll and turns a "cc" or
        "pitch" entry into the identical mido message a physical press or
        touchstrip move would have produced, which is the same path a
        physical press takes -- so a click and a press cannot mean two
        different things. Queued rather than replaced: see
        push_cc.append_command for why a hold's press and release must both
        survive."""
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            cmd = json.loads(raw)
            if not isinstance(cmd, dict):
                raise TypeError
            keep = {k: int(cmd[k])
                    for k in ("tab", "seat", "page", "answer") if k in cmd}
            if "cc" in cmd:
                cc = int(cmd["cc"])
                if not 0 <= cc <= 127:
                    raise ValueError("cc out of range")
                value = int(cmd.get("value", 127))
                if not 0 <= value <= 127:
                    raise ValueError("value out of range")
                keep["cc"], keep["value"] = cc, value
            if "pitch" in cmd:
                pitch = int(cmd["pitch"])
                if not -8192 <= pitch <= 8191:
                    raise ValueError("pitch out of range")
                keep["pitch"] = pitch
        except (json.JSONDecodeError, TypeError, ValueError):
            return self._send(400, "bad request", "text/plain")
        if not keep:
            return self._send(400, "nothing to press", "text/plain")
        push_cc.append_command(keep)
        self._send(200, "ok", "text/plain")

    def _agents_create(self):
        """A new claude, split into the tmux server. Named agents go straight
        through herdr so a taken name comes back as 409, not a silent -2."""
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            body = json.loads(raw)
            cwd = body["cwd"]
        except (json.JSONDecodeError, KeyError, TypeError):
            return self._send(400, "bad request", "text/plain")
        if not cwd or not os.path.isdir(cwd):
            return self._send(400, "cwd is not a directory", "text/plain")
        split = body.get("split") or "right"
        name = body.get("name")
        if name:
            out = push_cc.herdr("agent", "start", name, "--cwd", cwd, "--split", split,
                                "--focus", "--", "claude", "--permission-mode", "auto")
            err = herdr_error(out)
            if err:
                code = 409 if err.get("code") == "agent_name_taken" else 500
                return self._send(code, err.get("message", "start failed"), "text/plain")
        else:
            name = push_cc.start_agent(cwd, split)
            if not name:
                return self._send(500, "could not start agent", "text/plain")
        self._send(200, json.dumps({"name": name}), "application/json")

    def _dirs(self):
        """Folders on THIS machine, so the iPad can pick one.

        A file picker on the tablet would browse the tablet, which is not
        where the repos are -- the agents run here. So the browsing happens
        here and the app renders the answer.

        Read-only, and it lists directory names only. Worth knowing before
        --lan: the same warning that already applies to this server applies
        here, and this one hands out a little of the shape of your disk."""
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        path = (q.get("path") or [""])[0] or target_cwd()
        path = os.path.abspath(os.path.expanduser(path))
        if not os.path.isdir(path):
            return self._send(404, "not a directory", "text/plain")
        try:
            names = sorted(os.listdir(path))
        except OSError as e:
            return self._send(403, str(e), "text/plain")
        here = {a.get("cwd") for a in push_cc.agents()}
        entries = []
        for n in names:
            full = os.path.join(path, n)
            if not os.path.isdir(full):
                continue        # a session starts in a folder, not a file
            entries.append({
                "name": n, "path": full,
                # a repo is the thing you almost always mean, so say which
                # ones are, and let the app put them first
                "git": os.path.isdir(os.path.join(full, ".git"))
                or os.path.isfile(os.path.join(full, ".git")),
                "busy": full in here,   # an agent already lives here
            })
        parent = os.path.dirname(path)
        self._send(200, json.dumps({
            "path": path,
            "parent": None if parent == path else parent,
            "git": os.path.exists(os.path.join(path, ".git")),
            "entries": entries,
        }), "application/json")

    def _choose_dir(self):
        """The real Finder folder chooser, opened on the machine the agents
        run on. The in-app list in /dirs is the one that works from the
        tablet; this is the one that has a sidebar, search and cmd-shift-G.

        The window opens HERE, not on the iPad -- which is the whole reason
        the list stays. GET because nothing changes: the answer is a path the
        user pointed at, and the dialog is how they point.

        System Events owns the dialog so it comes to the front; osascript run
        from a nohup'd server otherwise puts it behind whatever you are
        looking at."""
        if sys.platform != "darwin":
            return self._send(501, "only on macOS", "text/plain")
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        start = (q.get("start") or [""])[0] or target_cwd()
        start = os.path.abspath(os.path.expanduser(start))
        if not os.path.isdir(start):
            start = os.path.expanduser("~")
        script = ('tell application "System Events" to POSIX path of '
                  '(choose folder with prompt "midiAI: folder for this session" '
                  f'default location POSIX file {json.dumps(start)})')
        try:
            out = subprocess.run(["osascript", "-e", script],
                                 capture_output=True, text=True, timeout=180)
        except subprocess.TimeoutExpired:
            return self._send(504, "nobody picked a folder", "text/plain")
        # cancel is a nonzero exit, not an error -- the app just closes the
        # spinner and leaves the field alone
        if out.returncode != 0:
            return self._send(200, json.dumps({"path": None}),
                              "application/json")
        path = out.stdout.strip().rstrip("/") or "/"
        self._send(200, json.dumps({"path": path}), "application/json")

    def _worktree(self):
        """A new worktree, and an agent in it. The Push has had this on Add
        Track since the beginning; the app could see the button's effect and
        never press it."""
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            body = json.loads(raw)
            cwd, branch = body["cwd"], body["branch"]
            name = body.get("name")
        except (json.JSONDecodeError, KeyError, TypeError):
            return self._send(400, "bad request", "text/plain")
        if not os.path.isdir(cwd):
            return self._send(400, "cwd is not a directory", "text/plain")
        if not os.path.exists(os.path.join(cwd, ".git")):
            return self._send(400, "not a git repo", "text/plain")
        # slug() is what the Push feeds this, so the app gets the same rules
        branch = push_cc.slug(branch)
        if not branch:
            return self._send(400, "no branch name", "text/plain")
        argv = ["worktree", "create", "--cwd", cwd, "--branch", branch, "--focus"]
        if name:
            # only appended when present -- the herdr (non-tmux) backend is
            # external and must not see an unknown flag
            argv += ["--name", name]
        out = push_cc.herdr(*argv)
        err = herdr_error(out)
        if err:
            return self._send(500, err.get("message", "worktree failed"),
                              "text/plain")
        self._send(200, json.dumps({"branch": branch}), "application/json")

    def _projects(self):
        """Every project: the ones remembered, plus wherever an agent is living
        right now, each with its worktrees already attached so the app makes one
        call rather than one per repo.

        Running somewhere new is what adds it -- there is no separate write to
        forget. Only /projects/remove takes one away: a repo whose `worktree
        list` fails today (disk not mounted, a clone half-finished) is left out
        of this answer and kept in the file, because forgetting your repos on
        one bad morning is the failure that actually costs something here."""
        live = [a.get("cwd") for a in push_cc.agents()]
        known = load_projects()
        keep, seen, out = set(), set(), []
        for cwd, remembered in ([(c, True) for c in known] +
                                [(c, False) for c in live if c]):
            rows = repo_worktrees(cwd)
            if not rows:
                if remembered:
                    keep.add(cwd)   # kept but not resolved -- see the docstring
                continue
            main = rows[0]["path"]
            keep.add(main)          # the file converges on main checkouts
            if main in seen:
                continue
            seen.add(main)
            out.append({"path": main,
                        "name": os.path.basename(main.rstrip("/")) or main,
                        "worktrees": rows})
        save_projects(keep)
        out.sort(key=lambda p: p["name"].lower())
        self._send(200, json.dumps({"projects": out}), "application/json")

    def _projects_add(self):
        """Remember a repo nobody is working in yet. Any path inside it will
        do -- it is stored as the main checkout, so adding a worktree and
        adding its repo are the same act."""
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            path = json.loads(raw)["path"]
        except (json.JSONDecodeError, KeyError, TypeError):
            return self._send(400, "bad request", "text/plain")
        if not isinstance(path, str) or not path.strip():
            return self._send(400, "bad request", "text/plain")
        path = os.path.abspath(os.path.expanduser(path.strip()))
        if not os.path.isdir(path):
            return self._send(400, "path is not a directory", "text/plain")
        rows = repo_worktrees(path)
        if not rows:
            return self._send(400, "not a git repo", "text/plain")
        main = rows[0]["path"]
        save_projects(set(load_projects()) | {main})
        self._send(200, json.dumps({"path": main}), "application/json")

    def _projects_remove(self):
        """Forget a repo. Nothing on disk is touched -- this is the list's own
        memory and nothing else, which is why it needs no confirmation and no
        --force. An agent running there puts it straight back."""
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            path = json.loads(raw)["path"]
        except (json.JSONDecodeError, KeyError, TypeError):
            return self._send(400, "bad request", "text/plain")
        if not isinstance(path, str) or not path:
            return self._send(400, "bad request", "text/plain")
        save_projects(set(load_projects()) - {path})
        self._send(200, "ok", "text/plain")

    def _worktrees_list(self):
        """The tidy-up card's data: every worktree of a repo, orphans
        included -- the app needs `exists: False` rows to offer removing."""
        query = urllib.parse.urlsplit(self.path).query
        cwd = (urllib.parse.parse_qs(query).get("cwd") or [None])[0] or target_cwd()
        out = push_cc.herdr("worktree", "list", "--cwd", cwd)
        err = herdr_error(out)
        if err:
            return self._send(500, err.get("message", "worktree list failed"),
                              "text/plain")
        try:
            result = json.loads(out).get("result", {})
        except json.JSONDecodeError:
            result = {"worktrees": [], "type": "worktree_list"}
        self._send(200, json.dumps(result), "application/json")

    def _branches(self):
        """The branches a worktree could switch to, each saying which worktree
        already has it out. git refuses to check one branch out twice, so that
        field is what lets the app grey a row rather than offer a button whose
        only outcome is an error."""
        query = urllib.parse.urlsplit(self.path).query
        cwd = (urllib.parse.parse_qs(query).get("cwd") or [None])[0] or target_cwd()
        out = push_cc.herdr("worktree", "branches", "--cwd", cwd)
        err = herdr_error(out)
        if err:
            return self._send(500, err.get("message", "branch list failed"),
                              "text/plain")
        try:
            result = json.loads(out).get("result", {})
        except json.JSONDecodeError:
            result = {"branches": [], "type": "branch_list"}
        self._send(200, json.dumps(result), "application/json")

    def _worktrees_switch(self):
        """Check a different branch out in a worktree.

        Unlike remove, a live agent is not a blocker: this changes the files
        under it, which is disruptive and recoverable, where remove deletes the
        directory out from under it. git's own refusals -- dirty tree, a branch
        already out somewhere else -- come back as the message, because git
        says it better than a code of ours would."""
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            body = json.loads(raw)
            path, branch = body["path"], body["branch"]
        except (json.JSONDecodeError, KeyError, TypeError):
            return self._send(400, "bad request", "text/plain")
        if not path or not branch:
            return self._send(400, "bad request", "text/plain")
        out = push_cc.herdr("worktree", "switch", "--path", path, "--branch", branch)
        err = herdr_error(out)
        if err:
            code = {"worktree_missing": 404, "git_checkout": 409}.get(
                err.get("code"), 500)
            return self._send(code, err.get("message", "switch failed"),
                              "text/plain")
        self._send(200, "ok", "text/plain")

    def _worktrees_remove(self):
        """Removing a worktree out from under a running agent is the other
        failure that must not happen -- checked here, not in term.py, because
        only mapui knows about live agents."""
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            body = json.loads(raw)
            cwd, path = body["cwd"], body["path"]
        except (json.JSONDecodeError, KeyError, TypeError):
            return self._send(400, "bad request", "text/plain")
        if not cwd or not path:
            return self._send(400, "bad request", "text/plain")
        blocker = next((a for a in push_cc.agents()
                        if a.get("cwd") and _is_inside(a["cwd"], path)), None)
        if blocker:
            who = blocker.get("name") or blocker.get("terminal_id")
            return self._send(409, f"agent {who} is running in {path}",
                              "text/plain")
        argv = ["worktree", "remove", "--cwd", cwd, "--path", path]
        if body.get("force"):
            argv.append("--force")
        out = push_cc.herdr(*argv)
        err = herdr_error(out)
        if err:
            code = {"worktree_is_main": 409, "worktree_dirty": 409,
                    "worktree_missing": 404}.get(err.get("code"), 500)
            return self._send(code, err.get("message", "worktree remove failed"),
                              "text/plain")
        self._send(200, "ok", "text/plain")

    def _worktrees_open(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            body = json.loads(raw)
            cwd, path = body["cwd"], body["path"]
        except (json.JSONDecodeError, KeyError, TypeError):
            return self._send(400, "bad request", "text/plain")
        if not cwd or not path:
            return self._send(400, "bad request", "text/plain")
        out = push_cc.herdr("worktree", "open", "--cwd", cwd, "--path", path)
        err = herdr_error(out)
        if err:
            code = 404 if err.get("code") == "worktree_missing" else 500
            return self._send(code, err.get("message", "worktree open failed"),
                              "text/plain")
        self._send(200, "ok", "text/plain")

    def _agents_close(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            terminal_id = json.loads(raw)["terminal_id"]
        except (json.JSONDecodeError, KeyError, TypeError):
            return self._send(400, "bad request", "text/plain")
        if not terminal_id:
            return self._send(400, "bad request", "text/plain")
        push_cc.herdr("pane", "close", terminal_id)
        self._send(200, "ok", "text/plain")

    def _agents_rename(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            body = json.loads(raw)
            terminal_id, name = body["terminal_id"], body["name"]
        except (json.JSONDecodeError, KeyError, TypeError):
            return self._send(400, "bad request", "text/plain")
        if not terminal_id or not name:
            return self._send(400, "bad request", "text/plain")
        out = push_cc.herdr("agent", "rename", terminal_id, name)
        err = herdr_error(out)
        if err:
            code = {"invalid_agent_name": 400,
                    "agent_name_taken": 409}.get(err.get("code"), 500)
            return self._send(code, err.get("message", "rename failed"), "text/plain")
        self._send(200, "ok", "text/plain")

    def _prompt(self):
        """Free text to an agent, same target rule _fire uses: whichever
        session the Push is sitting on, unless the caller names one."""
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            body = json.loads(raw)
            text = body["text"]
        except (json.JSONDecodeError, KeyError, TypeError):
            return self._send(400, "bad request", "text/plain")
        if not text:
            return self._send(400, "bad request", "text/plain")
        target = body.get("terminal_id") or read_target().get("terminal_id")
        if not target:
            return self._send(409, "no session selected on the Push", "text/plain")
        if body.get("replace"):
            # The composer shows the agent's own input line back, editable, so
            # what it sends is that line rewritten -- not something to add to
            # it. Typing the edit on top of the original would submit both.
            # Scraped rather than taken from the caller: the pane is what the
            # deletes have to land on, and it is the only thing that knows.
            # Forwards then backwards, the same pair Undo uses, because the
            # cursor sits wherever dictation left it and neither alone empties
            # a wrapped prompt.
            pend = push_cc.pane_summary({"terminal_id": target}).get("pending") or ""
            push_cc.herdr("agent", "send", target, push_cc.clear_keys(pend))
        push_cc.herdr("agent", "send", target,
                      text + ("\r" if body.get("submit") else ""))
        self._send(200, "ok", "text/plain")

    def _paste(self):
        """An image from the tablet onto this machine's disk, so a prompt can
        point at it.

        A pty carries text, so an image cannot be typed into one. What can be
        typed is a path -- the agent reads the file itself, and the path stays
        visible in the composer like every other thing we send."""
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            data = base64.b64decode(json.loads(raw)["data"], validate=True)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError,
                binascii.Error):
            return self._send(400, "bad request", "text/plain")
        if len(data) > PASTE_MAX:
            return self._send(413, "image too large", "text/plain")
        ext = image_ext(data)
        if not ext:
            return self._send(415, "not an image", "text/plain")
        try:
            os.makedirs(PASTE_DIR, exist_ok=True)
            # ponytail: named by the clock, never cleaned up. They are a few KB
            # each and an agent may still be pointing at one from an hour ago;
            # add a sweep if the directory ever actually gets big.
            path = os.path.join(PASTE_DIR,
                                time.strftime("%Y%m%d-%H%M%S") + ext)
            n = 1
            while os.path.exists(path):        # two pastes inside one second
                path = os.path.join(
                    PASTE_DIR, f"{time.strftime('%Y%m%d-%H%M%S')}-{n}{ext}")
                n += 1
            with open(path, "wb") as f:
                f.write(data)
        except OSError as e:
            return self._send(500, f"could not save: {e}", "text/plain")
        return self._send(200, json.dumps({"path": path}), "application/json")

    def _record_start(self):
        """Hold begins. One recorder at a time, this machine's mic."""
        why = voice_missing()
        if why:
            return self._send(503, why, "text/plain")
        global _rec
        with _rec_lock:
            _stop_rec()                        # a release that never arrived
            wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
            # -q or sox draws a meter at whatever launched this; the duration
            # cap is the only thing that ends a hold nobody let go of.
            proc = subprocess.Popen(
                ["rec", "-q", "-c", "1", "-r", "16000", wav,
                 "trim", "0", str(RECORD_MAX_S)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            _rec = (proc, wav)
        return self._send(200, "ok", "text/plain")

    def _record_stop(self):
        """Hold ends: stop the mic, read the words back out of the file.

        The text is returned, not sent. An agent gets it only once someone
        has looked at it and pressed a button -- which is the difference
        between this and the Push's own hold."""
        with _rec_lock:
            wav = _stop_rec()
        if not wav:
            return self._send(409, "not recording", "text/plain")
        try:
            # sox writes the header on exit; nothing worth transcribing is
            # shorter than this, and an empty file makes whisper fail loudly
            # rather than return the empty string it means.
            if os.path.getsize(wav) < 8000:    # ~0.25s at 16k mono 16-bit
                return self._send(200, json.dumps({"text": ""}),
                                  "application/json")
            return self._send(200, json.dumps({"text": transcribe(wav)}),
                              "application/json")
        except (OSError, subprocess.SubprocessError) as e:
            return self._send(500, f"transcribe failed: {e}", "text/plain")
        finally:
            try:
                os.unlink(wav)
            except OSError:
                pass

    def log_message(self, *_):
        pass                                   # ponytail: no request spam


def _stop_rec():
    """End any running recorder and hand back its file. Call under the lock."""
    global _rec
    if not _rec:
        return None
    proc, wav = _rec
    _rec = None
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    return wav


def local_ip():
    """The LAN-facing address, without sending anything: connect() on a UDP
    socket just picks a route and never puts a packet on the wire."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--lan", action="store_true",
                        help="bind 0.0.0.0 so an iPad on the LAN can reach this")
    args = parser.parse_args()
    host = "0.0.0.0" if args.lan else "127.0.0.1"
    print(f"serving {push_cc.MACRO_FILE}")
    if args.lan:
        print(f"http://{local_ip()}:{PORT}  <- point the iPad's Expo Go here")
        print("WARNING: bound to the LAN. Anything on this network can now "
              "type into the agent sessions this drives.")
    else:
        print(f"http://localhost:{PORT}")
    # threaded: /choose-dir blocks for as long as the Finder dialog is open,
    # and the app polls /agents and /target the whole time it is up
    ThreadingHTTPServer((host, PORT), Handler).serve_forever()
