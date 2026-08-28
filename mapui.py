#!/usr/bin/env python3
"""The midiAI API for the Push 2: macros.json read/write, the live surface
mirror, and pad firing. The editor UI itself is the Expo app in ./app --
this file only serves the data it needs.

    python3 mapui.py        then, in app/, npx expo start
    python3 mapui.py --lan  binds 0.0.0.0 so an iPad on the LAN can reach it
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

import push_cc

PORT = 8765
PUSH_CC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "push_cc.py")
PUSH_LOG = "/tmp/push.log"


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
        if self.path == "/worktrees/open":
            return self._worktrees_open()
        if self.path == "/prompt":
            return self._prompt()
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
        """A button on the mirror. push_cc consumes the file on its next poll,
        which is the same path a physical press takes -- so a click and a press
        cannot mean two different things."""
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            cmd = json.loads(raw)
            keep = {k: int(cmd[k])
                    for k in ("tab", "seat", "page", "answer") if k in cmd}
        except (json.JSONDecodeError, TypeError, ValueError):
            return self._send(400, "bad request", "text/plain")
        if not keep:
            return self._send(400, "nothing to press", "text/plain")
        tmp = push_cc.CMD_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(keep, f)
        os.replace(tmp, push_cc.CMD_FILE)
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

    def _worktree(self):
        """A new worktree, and an agent in it. The Push has had this on Add
        Track since the beginning; the app could see the button's effect and
        never press it."""
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            body = json.loads(raw)
            cwd, branch = body["cwd"], body["branch"]
        except (json.JSONDecodeError, KeyError, TypeError):
            return self._send(400, "bad request", "text/plain")
        if not os.path.isdir(cwd):
            return self._send(400, "cwd is not a directory", "text/plain")
        # slug() is what the Push feeds this, so the app gets the same rules
        branch = push_cc.slug(branch)
        if not branch:
            return self._send(400, "no branch name", "text/plain")
        out = push_cc.herdr("worktree", "create", "--cwd", cwd,
                            "--branch", branch, "--focus")
        err = herdr_error(out)
        if err:
            return self._send(500, err.get("message", "worktree failed"),
                              "text/plain")
        self._send(200, json.dumps({"branch": branch}), "application/json")

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
        push_cc.herdr("agent", "send", target,
                      text + ("\r" if body.get("submit") else ""))
        self._send(200, "ok", "text/plain")

    def log_message(self, *_):
        pass                                   # ponytail: no request spam


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
    HTTPServer((host, PORT), Handler).serve_forever()
