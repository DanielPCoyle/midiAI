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
import glob
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


def skill_description(skill_md_path):
    """The `description:` line out of a SKILL.md's YAML frontmatter, without
    pulling in a YAML dependency for what is, in practice, a handful of
    `key: value` lines between two `---` markers. Reading a full parser's
    worth of edge cases isn't worth it for one field, so this reads only what
    the spec actually requires: a top-of-file frontmatter block, the
    `description:` key, and its value folded across any more-indented
    continuation lines that follow it.

    Reads at most the first 4KB -- frontmatter lives at the very top of the
    file or nowhere, so a skill authored with a huge SKILL.md body is never a
    reason to read the whole thing just to find one field."""
    try:
        with open(skill_md_path, "r", errors="replace") as f:
            head = f.read(4096)
    except OSError:
        return ""
    lines = head.splitlines()
    if not lines or lines[0].strip() != "---":
        return ""            # no frontmatter block -- nothing to read
    closing = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            closing = i
            break
    if closing is None:
        return ""            # frontmatter never closes within the 4KB read
    description, capturing = "", False
    for line in lines[1:closing]:
        if capturing and line[:1] in (" ", "\t") and line.strip():
            # a continuation line: fold it onto the value with one space,
            # the same way YAML's own folded scalars behave
            description += " " + line.strip()
            continue
        capturing = False
        if line.lstrip().startswith("description:"):
            description = line.split(":", 1)[1].strip()
            capturing = True
    # YAML's quotes are syntax, not part of the sentence: about one skill in
    # seven writes the value quoted, and left in they read as a typo in a list
    # where every neighbour is bare. Stripped only when both ends match, so a
    # description that genuinely opens with a quote keeps it.
    if len(description) > 1 and description[0] == description[-1] and description[0] in "\"'":
        description = description[1:-1]
    return description[:300]


# A skill's own name, and the only shape one may have. It is a directory name
# on this machine, minted from a string that arrived over HTTP on a server the
# --lan flag exposes to the network -- so it is checked against this and never
# against "does it look like a path", which is the check that is always one
# encoding away from being wrong.
SKILL_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")

# The two scopes a skill can be WRITTEN to. `plugin` is somebody else's package
# and `local` is a settings file, not a skill directory -- neither is editable
# here, and the way to say so is to have no path for them at all.
def skill_home(scope, root):
    if scope == "user":
        return os.path.expanduser("~/.claude/skills")
    if scope == "project":
        return os.path.join(root, ".claude", "skills")
    return None


def skill_path(scope, name, root):
    """Where a skill of this name in this scope lives -- or None.

    The client never sends a path. It sends a scope, which selects one of two
    directories this file names itself, and a name, which has to survive
    SKILL_NAME_RE. That regex IS the containment check and is written to be
    read as one: it admits no separator, no dot, and no leading dash, so there
    is nothing for a `..` to traverse from and no encoding of one that reaches
    os.path.join as anything but a rejected name. A path taken from the caller
    and then inspected is the version of this that keeps being wrong."""
    home = skill_home(scope, root)
    if not home or not SKILL_NAME_RE.match(name or ""):
        return None
    return os.path.join(home, name, "SKILL.md")


# The editor to open a folder in. Fixed here rather than taken from a request:
# the caller picks WHAT to open, never WITH WHAT, so there is no argument on
# this wire that turns into a command. Overridable by whoever starts the
# server, which is the person whose editor it is.
EDITOR_CMD = os.environ.get("MIDIAI_EDITOR", "code-insiders")


def openable(path):
    """A directory this server will hand to the editor, resolved -- or None.

    Anything under the home directory, and nothing else. That is a wide rule
    and deliberately so: the useful things to open are repos, worktrees and
    skill folders, which have no single root between them. What it does rule
    out is /etc, another user, and the class of request whose whole point is
    the path being somewhere you would not have gone."""
    if not path:
        return None
    real = os.path.realpath(os.path.expanduser(path))
    if os.path.isfile(real):
        real = os.path.dirname(real)
    home = os.path.realpath(os.path.expanduser("~"))
    if not os.path.isdir(real):
        return None
    try:
        if os.path.commonpath([real, home]) != home:
            return None
    except ValueError:              # different drives -- not under home either
        return None
    return real


def plugin_skill(path):
    """A plugin's SKILL.md, resolved -- or None.

    Plugin skills are the one kind whose location this file cannot derive from
    a scope and a name: they live wherever their package put them, one or two
    directories deep. So this is the one place a path arrives from the caller,
    and it is admitted only if it really is a SKILL.md really inside
    ~/.claude/plugins. commonpath, not startswith: /a/bc must not pass for
    /a/b."""
    if not path:
        return None
    real = os.path.realpath(path)
    home = os.path.realpath(os.path.expanduser("~/.claude/plugins"))
    try:
        if os.path.commonpath([real, home]) != home:
            return None
    except ValueError:
        return None
    return real if os.path.isfile(real) else None


def skill_text(name, description, body):
    """A SKILL.md, front to back. Claude Code reads the frontmatter and the
    body is the instructions -- so this writes exactly the two keys it needs
    and leaves everything else to what the author typed."""
    desc = " ".join((description or "").split())
    return f"---\nname: {name}\ndescription: {desc}\n---\n\n{(body or '').strip()}\n"


def skill_body(path):
    """Everything after the frontmatter block. The description is
    skill_description's job; this is the other half of the same file, so that
    the editor opens on what is actually there rather than on a rewrite of it."""
    try:
        with open(path, "r", errors="replace") as f:
            text = f.read()
    except OSError:
        return ""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return text          # no frontmatter: the whole file is the body
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "\n".join(lines[i + 1:]).strip("\n")
    return text              # never closes -- treat it as prose, not as a header


# Where a hook of each scope is written. `plugin` has no settings file of ours
# to edit, which is why it is not here rather than why it is refused.
def hooks_file(scope, root):
    if scope == "user":
        return os.path.expanduser("~/.claude/settings.json")
    if scope == "project":
        return os.path.join(root, ".claude", "settings.json")
    if scope == "local":
        return os.path.join(root, ".claude", "settings.local.json")
    return None


def edit_settings(path, change):
    """Read a settings file, hand `change` its dict, write it back.

    Two things make this different from every other write in this file. The
    file is not ours -- it holds permissions, env, statusLine, whatever else
    the user keeps in there -- so it is read, mutated in place and written
    whole, never regenerated from what we know about. And it is the file that
    decides how their whole tool behaves, so the previous contents go to a
    `.bak` beside it on every write. A hook editor that eats a settings.json
    is worse than no hook editor.

    Returns None on success, or a sentence saying what went wrong."""
    data = {}
    had = None
    try:
        with open(path) as f:
            had = f.read()
        data = json.loads(had)
        if not isinstance(data, dict):
            return "that settings file is not a JSON object"
    except FileNotFoundError:
        pass                       # a scope with no settings file yet is fine
    except OSError as e:
        return f"could not read {path}: {e}"
    except json.JSONDecodeError as e:
        # refusing beats rewriting: whatever is in there was hand-written, and
        # replacing it with our idea of it would lose the rest of the file
        return f"{path} is not valid JSON ({e}) -- fix it by hand first"
    err = change(data)
    if err:
        return err
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if had is not None:
            with open(path + ".bak", "w") as f:
                f.write(had)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except OSError as e:
        return f"could not write {path}: {e}"
    return None


def skill_rows(skill_md_paths, scope):
    """Turn a list of SKILL.md paths into /catalog rows. A thin wrapper, but
    it's the one place that decides a skill's `name` is its directory name
    -- the same name the user types after the slash -- rather than anything
    parsed out of the file itself."""
    out = []
    for path in skill_md_paths:
        name = os.path.basename(os.path.dirname(path))
        out.append({"name": name, "description": skill_description(path),
                    "scope": scope, "path": path})
    return out


# What a hook entry may contain, per type, straight off Claude Code's own
# reference. This is a whitelist and is the reason it can be written from an
# HTTP request at all: the caller says which type, and only that type's fields
# reach the file. A key nobody here has heard of is dropped, not passed
# through -- settings.json is the file that decides how the whole tool behaves
# and it is not a place to forward unknown data into.
HOOK_COMMON = {"if": str, "timeout": (int, float), "statusMessage": str,
               "once": bool}
HOOK_TYPES = {
    "command": (("command",), {"command": str, "args": list, "shell": str,
                               "async": bool, "asyncRewake": bool}),
    "http": (("url",), {"url": str, "headers": dict, "allowedEnvVars": list}),
    "mcp_tool": (("server", "tool"), {"server": str, "tool": str, "input": dict}),
    "prompt": (("prompt",), {"prompt": str, "model": str}),
    "agent": (("prompt",), {"prompt": str, "model": str}),
}


def hook_entry(body):
    """(entry, error): one hook object built from a request, or why not.

    Nothing is copied that the type does not name, and nothing is copied whose
    value is the wrong shape -- a `timeout` of "soon" would be written straight
    into a file Claude Code reads on every start, and the first anyone heard of
    it would be the tool refusing to boot."""
    kind = body.get("type") or "command"
    if kind not in HOOK_TYPES:
        return None, f"a hook is one of: {', '.join(HOOK_TYPES)}"
    required, fields = HOOK_TYPES[kind]
    entry = {"type": kind}
    for key, want in list(fields.items()) + list(HOOK_COMMON.items()):
        if key not in body or body[key] in (None, "", [], {}):
            continue
        val = body[key]
        # bool is a subclass of int in Python, so a `true` would satisfy a
        # number field without this
        if want is str and not isinstance(val, str):
            return None, f"{key} has to be text"
        if want is bool and not isinstance(val, bool):
            return None, f"{key} has to be true or false"
        if want is list and not (isinstance(val, list)
                                 and all(isinstance(x, str) for x in val)):
            return None, f"{key} has to be a list of text"
        if want is dict and not (isinstance(val, dict)
                                 and all(isinstance(x, str) for x in val.values())):
            return None, f"{key} has to be names and text values"
        if want == (int, float) and (isinstance(val, bool)
                                     or not isinstance(val, (int, float))):
            return None, f"{key} has to be a number"
        entry[key] = val
    missing = [k for k in required if not entry.get(k)]
    if missing:
        return None, f"a {kind} hook needs {' and '.join(missing)}"
    return entry, None


def hook_summary(entry):
    """The one line the panel shows for a hook. Every type has a different
    field that identifies it, and showing "type: mcp_tool" would name the
    mechanism where the reader wants the errand."""
    kind = entry.get("type") or "command"
    if kind == "http":
        return str(entry.get("url") or "")
    if kind == "mcp_tool":
        return f"{entry.get('server', '?')} · {entry.get('tool', '?')}"
    if kind in ("prompt", "agent"):
        return " ".join(str(entry.get("prompt") or "").split())
    return str(entry.get("command") or "")


# Names and descriptions for hooks. The name is `statusMessage`, which is a
# real documented field and shows as the spinner text while the hook runs, so
# it earns its place twice. A description has no home in the schema at all --
# unknown keys survive today, but settings.json failing to load because a
# future version got stricter about a field we invented is not a trade worth
# making for a note. So the notes live here, beside the pastes and the
# projects, and settings.json keeps only what Claude Code documents.
NOTES_FILE = os.path.expanduser("~/.midiai/hook-notes.json")


def note_key(scope, event, matcher, summary):
    """What a note is filed under. Not the group/entry index: those renumber
    the moment a sibling is deleted, which would silently hand one hook's
    description to another."""
    return "\u0000".join([scope or "", event or "", matcher or "", summary or ""])


def load_notes():
    try:
        with open(NOTES_FILE) as f:
            got = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return got if isinstance(got, dict) else {}


def save_notes(notes):
    """Best effort, like the projects list: a note that cannot be written is
    not a reason to fail the hook write it was describing."""
    try:
        os.makedirs(os.path.dirname(NOTES_FILE), exist_ok=True)
        tmp = NOTES_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(notes, f, indent=1, sort_keys=True)
        os.replace(tmp, NOTES_FILE)
    except OSError as e:
        print(f"hook notes: {e}", file=sys.stderr, flush=True)


def hook_rows(settings_path, scope):
    """One settings file's hooks, flattened to one row per command.

    The file is read best-effort: a settings.json that is missing (no user
    hooks configured), unreadable, or hand-edited into invalid JSON is a
    normal state for a machine to be in, not a reason to fail the whole
    /catalog request over one scope this agent may not even have hooks in."""
    try:
        with open(settings_path) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    hooks = data.get("hooks") if isinstance(data, dict) else None
    if not isinstance(hooks, dict):
        return []
    notes = load_notes()
    out = []
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            continue
        for gi, group in enumerate(groups):
            if not isinstance(group, dict):
                continue
            matcher = group.get("matcher") or ""
            entries = group.get("hooks")
            if not isinstance(entries, list):
                continue
            for hi, entry in enumerate(entries):
                if not isinstance(entry, dict):
                    continue
                summary = hook_summary(entry)
                if not summary:
                    continue
                # `gi`/`hi` are the row's address in the file it came from --
                # the editor sends them back to say which of several hooks on
                # the same event it means. Matching on the summary instead
                # would edit the wrong one the moment two of them agreed.
                out.append({"event": event, "matcher": matcher,
                            "type": entry.get("type") or "command",
                            "summary": summary[:400],
                            # kept under its old name for the panel, which has
                            # always drawn this row from `command`
                            "command": summary[:400],
                            # the whole entry, so the editor round-trips every
                            # field rather than rewriting one it cannot see
                            "entry": entry,
                            "name": str(entry.get("statusMessage") or ""),
                            "description": notes.get(
                                note_key(scope, event, matcher, summary), ""),
                            "gi": gi, "hi": hi,
                            "scope": scope, "path": settings_path})
    return out


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
        elif self.path == "/skill" or self.path.startswith("/skill?"):
            self._skill_read()
        elif self.path == "/catalog" or self.path.startswith("/catalog?"):
            self._catalog()
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
        if self.path == "/skill":
            return self._skill_write()
        if self.path == "/skill/delete":
            return self._skill_delete()
        if self.path == "/skill/move":
            return self._skill_move()
        if self.path == "/open":
            return self._open_in_editor()
        if self.path == "/hook":
            return self._hook_write()
        if self.path == "/hook/delete":
            return self._hook_delete()
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

    def _skill_root(self, cwd):
        """The checkout a project-scoped skill belongs to, resolved the same way
        /catalog resolves it -- so the skill the app is editing is the skill the
        catalog listed, and not a sibling worktree's copy of it."""
        rows = repo_worktrees(cwd)
        return rows[0]["path"] if rows else cwd

    def _skill_read(self):
        """One skill's frontmatter and body, for the editor to open on.

        /catalog already carries the name and the description -- but not the
        instructions, which are the part you actually edit and which would make
        every catalog response many times larger for a field almost nobody is
        looking at. So the list is cheap and the edit is a second call."""
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        scope = (q.get("scope") or [""])[0]
        name = (q.get("name") or [""])[0]
        cwd = (q.get("cwd") or [None])[0] or target_cwd()
        # a plugin's skill is readable and not writable -- reading one is
        # exactly what you want when you are about to write your own version
        path = (plugin_skill((q.get("path") or [""])[0]) if scope == "plugin"
                else skill_path(scope, name, self._skill_root(cwd)))
        if not path:
            return self._send(400, "not a skill this server can read",
                              "text/plain")
        if not os.path.isfile(path):
            return self._send(404, "no such skill", "text/plain")
        self._send(200, json.dumps({
            "name": name, "scope": scope, "path": path,
            "description": skill_description(path), "body": skill_body(path),
        }), "application/json")

    def _skill_write(self):
        """Create or replace a skill, in `user` or `project` scope.

        Create and update are one route because the client has no say in where
        the file goes: scope and name decide that, so "the one already there"
        and "a new one" are the same write to the same derived path. What the
        caller cannot do is name a path, which is the whole reason this is safe
        to expose on a server --lan puts on the network.

        Plugin skills are not writable and there is deliberately no scope that
        reaches them: they belong to something installed, and editing one in
        place would be undone by its next update without saying so."""
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            body = json.loads(raw)
            scope, name = body["scope"], body["name"]
        except (json.JSONDecodeError, KeyError, TypeError):
            return self._send(400, "bad request", "text/plain")
        cwd = body.get("cwd") or target_cwd()
        path = skill_path(scope, name, self._skill_root(cwd))
        if not path:
            return self._send(
                400, "a skill name is lowercase letters, digits and dashes, "
                     "starting with a letter -- and only user or project scope "
                     "can be written", "text/plain")
        # `replace` is the editor saying "I opened this one and meant to change
        # it". Without it a create silently overwriting a skill of the same name
        # is a way to lose work by typing a name someone else already used.
        if os.path.exists(path) and not body.get("replace"):
            return self._send(409, f"a {scope} skill called {name} already exists",
                              "text/plain")
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w") as f:
                f.write(skill_text(name, body.get("description", ""),
                                   body.get("body", "")))
            os.replace(tmp, path)
        except OSError as e:
            return self._send(500, f"could not write the skill: {e}", "text/plain")
        self._send(200, json.dumps({"path": path}), "application/json")

    def _skill_delete(self):
        """Remove a skill -- its SKILL.md, and its directory if that leaves it
        empty.

        Never a recursive delete. A skill with references, scripts or a
        templates/ directory beside its SKILL.md is a small project, and a
        button in a side panel is not where anyone means to delete one: the
        SKILL.md goes, the rest stays, and what is left says so."""
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            body = json.loads(raw)
            scope, name = body["scope"], body["name"]
        except (json.JSONDecodeError, KeyError, TypeError):
            return self._send(400, "bad request", "text/plain")
        cwd = body.get("cwd") or target_cwd()
        path = skill_path(scope, name, self._skill_root(cwd))
        if not path:
            return self._send(400, "not an editable scope, or a bad name",
                              "text/plain")
        if not os.path.isfile(path):
            return self._send(404, "no such skill", "text/plain")
        try:
            os.remove(path)
            leftover = os.listdir(os.path.dirname(path))
            if not leftover:
                os.rmdir(os.path.dirname(path))
        except OSError as e:
            return self._send(500, f"could not remove the skill: {e}", "text/plain")
        self._send(200, json.dumps({"kept": leftover}), "application/json")

    def _open_in_editor(self):
        """Open a folder in the editor on the machine the agents run on.

        Which is the whole point: the app may be a tablet, and the files are
        over here. A plugin's skill cannot be edited in this panel and should
        not be -- but reading one is exactly what you want when you are about
        to write your own version of it, so opening beats copying it out.

        The command is a fixed binary and a list argv: no shell, so a path
        cannot become anything but a path."""
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            path = json.loads(raw)["path"]
        except (json.JSONDecodeError, KeyError, TypeError):
            return self._send(400, "bad request", "text/plain")
        dest = openable(path)
        if not dest:
            return self._send(400, "that is not a folder under your home "
                                   "directory", "text/plain")
        if not shutil.which(EDITOR_CMD):
            return self._send(
                501, f"{EDITOR_CMD} is not on this machine's PATH -- set "
                     f"MIDIAI_EDITOR to the one you use", "text/plain")
        try:
            subprocess.Popen([EDITOR_CMD, dest],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            return self._send(500, f"could not open it: {e}", "text/plain")
        self._send(200, json.dumps({"path": dest, "with": EDITOR_CMD}),
                   "application/json")

    def _skill_move(self):
        """Move a skill to another scope -- global to this project, or to one
        you name.

        A plugin's skill is COPIED, never moved: it belongs to something
        installed, and taking it would break the package and be undone by its
        next update anyway. Copying it is the useful half of that anyway --
        the reason to reach for one is to have your own version.

        The destination project has to be one this machine already knows
        about. That is the whitelist: a scope and a name derive the file
        everywhere else in here, and this is the one call that takes a
        directory, so it takes one off a list rather than out of a request."""
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            body = json.loads(raw)
            name, scope, to = body["name"], body["scope"], body["to"]
        except (json.JSONDecodeError, KeyError, TypeError):
            return self._send(400, "bad request", "text/plain")

        if scope == "plugin":
            real = plugin_skill(body.get("path"))
            if not real:
                return self._send(400, "that is not a plugin skill", "text/plain")
            src = os.path.dirname(real)
        else:
            src_md = skill_path(scope, name, self._skill_root(
                body.get("cwd") or target_cwd()))
            if not src_md or not os.path.isfile(src_md):
                return self._send(404, "no such skill", "text/plain")
            src = os.path.dirname(src_md)

        if to == "project":
            to_cwd = body.get("to_cwd") or body.get("cwd") or target_cwd()
            root = self._skill_root(to_cwd)
            known = {os.path.realpath(p) for p in load_projects()}
            known.add(os.path.realpath(self._skill_root(target_cwd())))
            if os.path.realpath(root) not in known:
                return self._send(400, "that project is not one this machine "
                                       "knows about -- add it first", "text/plain")
        elif to == "user":
            root = ""
        else:
            return self._send(400, "a skill moves to user or project scope",
                              "text/plain")

        dest_md = skill_path(to, name, root)
        if not dest_md:
            return self._send(400, "bad name for a skill", "text/plain")
        dest = os.path.dirname(dest_md)
        if os.path.realpath(dest) == os.path.realpath(src):
            return self._send(409, "it is already there", "text/plain")
        if os.path.exists(dest):
            return self._send(409, f"a {to} skill called {name} already exists",
                              "text/plain")
        try:
            shutil.copytree(src, dest)
            # only what this server put there is taken away again: a plugin's
            # copy stays where its package expects it
            if scope != "plugin":
                shutil.rmtree(src)
        except OSError as e:
            return self._send(500, f"could not move it: {e}", "text/plain")
        self._send(200, json.dumps({"path": dest_md,
                                    "copied": scope == "plugin"}),
                   "application/json")

    def _hook_target(self, body):
        """(path, error) for the scope this request names."""
        path = hooks_file(body.get("scope"), self._skill_root(
            body.get("cwd") or target_cwd()))
        if not path:
            return None, "hooks are written to user, project or local scope"
        return path, None

    def _hook_write(self):
        """Add a hook, or replace one already there.

        `gi`/`hi` say which row: with them this replaces exactly that entry,
        without them it appends. Appending reuses a group that already carries
        the same matcher rather than making a second one -- Claude Code reads
        both the same way, but a settings file that grows a new group per hook
        stops being a thing anyone can hand-edit afterwards, and it is still
        their file."""
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            body = json.loads(raw)
            event = str(body["event"]).strip()
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return self._send(400, "bad request", "text/plain")
        if not event:
            return self._send(400, "a hook needs an event", "text/plain")
        # the name is `statusMessage`, which Claude Code shows as the spinner
        # text while the hook runs -- a documented field doing the job an
        # invented one would have done invisibly
        if body.get("name"):
            body["statusMessage"] = str(body["name"])
        entry, why = hook_entry(body)
        if why:
            return self._send(400, why, "text/plain")
        path, err = self._hook_target(body)
        if err:
            return self._send(400, err, "text/plain")
        matcher = str(body.get("matcher") or "")
        gi, hi = body.get("gi"), body.get("hi")

        def change(data):
            hooks = data.setdefault("hooks", {})
            if not isinstance(hooks, dict):
                return "the hooks key in that settings file is not an object"
            groups = hooks.setdefault(event, [])
            if not isinstance(groups, list):
                return f"hooks.{event} in that settings file is not a list"
            if gi is not None and hi is not None:
                try:
                    group = groups[int(gi)]
                    # the matcher belongs to the group, so editing it here
                    # would silently retag every other hook sharing it
                    if str(group.get("matcher") or "") != matcher:
                        return ("that hook's matcher is shared with the others "
                                "in its group -- delete it and add it back to "
                                "move it")
                    group["hooks"][int(hi)] = entry
                    return None
                except (IndexError, KeyError, TypeError, ValueError):
                    return "that hook is no longer where it was -- reopen the panel"
            for group in groups:
                if isinstance(group, dict) and str(group.get("matcher") or "") == matcher:
                    group.setdefault("hooks", []).append(entry)
                    return None
            group = {"hooks": [entry]}
            if matcher:
                group["matcher"] = matcher
            groups.append(group)
            return None

        was = None
        if gi is not None and hi is not None:
            # the note is filed under what the hook looks like, so an edit that
            # changes that has to move it rather than orphan it
            rows = hook_rows(path, body.get("scope") or "")
            was = next((r for r in rows if r["gi"] == gi and r["hi"] == hi), None)
        bad = edit_settings(path, change)
        if bad:
            return self._send(409 if "no longer" in bad or "shared" in bad else 500,
                              bad, "text/plain")
        notes = load_notes()
        scope = body.get("scope") or ""
        if was:
            notes.pop(note_key(scope, was["event"], was["matcher"], was["summary"]), None)
        key = note_key(scope, event, matcher, hook_summary(entry))
        text = " ".join(str(body.get("description") or "").split())
        if text:
            notes[key] = text[:600]
        else:
            notes.pop(key, None)
        save_notes(notes)
        self._send(200, json.dumps({"path": path}), "application/json")

    def _hook_delete(self):
        """Remove one hook, and any group or event left empty by it -- a
        settings file that accumulates empty lists is one nobody wants to open
        afterwards."""
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            body = json.loads(raw)
            event = str(body["event"])
            gi, hi = int(body["gi"]), int(body["hi"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return self._send(400, "bad request", "text/plain")
        path, err = self._hook_target(body)
        if err:
            return self._send(400, err, "text/plain")
        scope = body.get("scope") or ""
        gone = next((r for r in hook_rows(path, scope)
                     if r["gi"] == gi and r["hi"] == hi), None)

        def change(data):
            hooks = data.get("hooks")
            if not isinstance(hooks, dict) or event not in hooks:
                return "that hook is no longer where it was -- reopen the panel"
            groups = hooks[event]
            try:
                del groups[gi]["hooks"][hi]
            except (IndexError, KeyError, TypeError):
                return "that hook is no longer where it was -- reopen the panel"
            if not groups[gi]["hooks"]:
                del groups[gi]
            if not groups:
                del hooks[event]
            if not hooks:
                del data["hooks"]     # an empty hooks:{} is litter, not state
            return None

        bad = edit_settings(path, change)
        if bad:
            return self._send(409 if "no longer" in bad else 500, bad, "text/plain")
        if gone:
            # a note for a hook that no longer exists is a note nothing will
            # ever show again, and the next hook to land on that summary would
            # inherit it
            notes = load_notes()
            if notes.pop(note_key(scope, gone["event"], gone["matcher"],
                                  gone["summary"]), None) is not None:
                save_notes(notes)
        self._send(200, "ok", "text/plain")

    def _catalog(self):
        """Every skill and hook an agent working in `cwd` can actually see,
        each tagged with the scope it came from -- user (this machine's own
        ~/.claude), project (checked into the repo, shared with whoever
        clones it), local (this checkout's own settings.local.json, which
        stays out of git), or plugin (bundled with something installed from
        the marketplace).

        This exists for the same reason /projects exists: an agent (or the
        app, on an agent's behalf) working out "what can I reach from here"
        by walking the filesystem itself would have to know all four of
        these locations and get the scope labelling right every time. One
        call is cheaper than four kinds of mistake."""
        query = urllib.parse.urlsplit(self.path).query
        cwd = (urllib.parse.parse_qs(query).get("cwd") or [None])[0] or target_cwd()
        rows = repo_worktrees(cwd)
        # repo_worktrees resolves any path inside a repo -- worktree
        # included -- to the main checkout, which is where a repo's own
        # .claude/ actually lives; a cwd that isn't a repo at all (or isn't
        # a directory) just falls back to itself, same as target_cwd() does
        # elsewhere in this file
        root = rows[0]["path"] if rows else cwd

        user_skills = sorted(glob.glob(os.path.expanduser("~/.claude/skills/*/SKILL.md")))
        project_skills = sorted(glob.glob(os.path.join(root, ".claude/skills/*/SKILL.md")))
        # two glob depths because a plugin's skills can live either directly
        # under the plugin, or nested one level deeper inside a named
        # sub-package of it -- both are real layouts on disk. Capped at 200
        # *before* any file is opened, so a user with a large plugin cache
        # never turns a read-only catalog lookup into a slow one.
        plugin_skills = sorted(set(
            glob.glob(os.path.expanduser("~/.claude/plugins/*/*/*/skills/*/SKILL.md")) +
            glob.glob(os.path.expanduser("~/.claude/plugins/*/*/skills/*/SKILL.md"))
        ))[:200]

        skills = (skill_rows(user_skills, "user") +
                 skill_rows(project_skills, "project") +
                 skill_rows(plugin_skills, "plugin"))
        skills.sort(key=lambda s: (s["scope"], s["name"].lower()))

        hooks = (hook_rows(os.path.expanduser("~/.claude/settings.json"), "user") +
                hook_rows(os.path.join(root, ".claude/settings.json"), "project") +
                hook_rows(os.path.join(root, ".claude/settings.local.json"), "local"))
        hooks.sort(key=lambda h: (h["scope"], h["event"].lower()))

        self._send(200, json.dumps({"skills": skills, "hooks": hooks}),
                  "application/json")

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
