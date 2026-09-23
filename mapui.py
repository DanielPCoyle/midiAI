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
import hashlib
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import access
import guardrails
import memory
import ptybridge
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
_app_test_runs = {}                # repo root -> push_cc.TestRun


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

# The guardrail checklist's state, per checkout. Beside projects.json rather
# than inside the repo, deliberately: the starter list is a framework, and a
# half-ticked framework committed to somebody's repo reads as a claim about
# that repo that nobody agreed to. Somewhere in .github is the right home for
# this the day a team decides it is theirs -- that is a decision to make on
# purpose, not a default.
GUARDRAILS_FILE = os.path.expanduser("~/.midiai/guardrails.json")


def load_guardrails():
    try:
        with open(GUARDRAILS_FILE) as f:
            got = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return got if isinstance(got, dict) else {}


def save_guardrails(all_of_them):
    os.makedirs(os.path.dirname(GUARDRAILS_FILE), exist_ok=True)
    tmp = GUARDRAILS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(all_of_them, f, indent=2)
    os.replace(tmp, GUARDRAILS_FILE)


# Prompts waiting for an agent to finish, per terminal_id, in the order they
# will go. Ours and not Claude Code's: once text is handed to Claude Code's
# own queue a pty cannot reach it again, so nothing there can be edited,
# reordered or pulled. Held here, it can -- and it keeps draining whichever
# agent the app happens to be looking at, or with the app closed.
QUEUE_FILE = os.path.expanduser("~/.midiai/queue.json")
QUEUE_TICK_S = 2
# herdr reads idle for a moment after a submit, before the agent picks it up;
# without a gap the whole queue would go in one burst.
# ponytail: a fixed gap -- a slash command that finishes inside it just waits
# the rest; key off a busy->idle edge if that ever feels slow.
QUEUE_GAP_S = 6
_queue_lock = threading.Lock()
_queue_last = {}      # tid -> when we last sent into it
# Paused is the default: a queue holds everything until you press play (drain
# whenever the agent is free) or send next (release just the head, once).
# ponytail: memory-only, so a restart pauses every queue again -- the safe
# direction, and the app shows it; persist it if that ever surprises anyone.
_queue_playing = set()   # tids whose queue drains on its own
_queue_once = set()      # tids allowed to send their head one time
_queue_gone = set()   # ids already delivered, so a stale client list can't
                      # put one back


def load_queue():
    try:
        with open(QUEUE_FILE) as f:
            got = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return got if isinstance(got, dict) else {}


def save_queue(q):
    os.makedirs(os.path.dirname(QUEUE_FILE), exist_ok=True)
    tmp = QUEUE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump({k: v for k, v in q.items() if v}, f, indent=2)
    os.replace(tmp, QUEUE_FILE)


def clean_queue(items):
    out = []
    for item in (items or [])[:100]:
        if not isinstance(item, dict):
            continue
        qid, text = str(item.get("id") or "")[:40], str(item.get("text") or "")[:20000]
        if qid and text.strip() and qid not in _queue_gone:
            out.append({"id": qid, "text": text})
    return out


def queue_state(tid, items):
    return {"items": items, "playing": tid in _queue_playing, "next": tid in _queue_once}


def queue_tick():
    """Send the head of each queue whose agent is free to take it."""
    with _queue_lock:
        heads = {tid: items[0] for tid, items in load_queue().items() if items}
    if not heads:
        return
    live = {a.get("terminal_id"): a for a in push_cc.agents()}
    for tid, head in heads.items():
        if tid not in _queue_playing and tid not in _queue_once:
            continue          # paused: it waits for play or send next
        agent = live.get(tid)
        if (not agent or agent.get("agent_status") != "idle"
                or time.time() - _queue_last.get(tid, 0) < QUEUE_GAP_S):
            continue
        pane = push_cc.pane_summary(agent)
        # a question on screen would take the text as its answer, and a half
        # dictated line would have ours glued onto the end of it
        if pane.get("opts") or (pane.get("pending") or "").strip():
            continue
        push_cc.herdr("agent", "send", tid, head["text"] + "\r")
        _queue_last[tid] = time.time()
        _queue_once.discard(tid)
        with _queue_lock:
            _queue_gone.add(head["id"])
            q = load_queue()
            q[tid] = [i for i in q.get(tid, []) if i.get("id") != head["id"]]
            save_queue(q)


# The conversation's pinned header: what you last asked, in one line. A
# short prompt is its own summary; a long one (a pasted spec, a wall of
# dictation) gets a line from Haiku -- the same isolated `claude -p` the
# memory summaries use, so it writes no transcript and fires no hooks.
# Cached by the text itself, so a prompt is summarised once however often
# the app redraws. mapui can be bound to the LAN, so the input is capped.
# ponytail: memory-only cache, lost on restart -- a restart re-summarises
# whatever is pinned then, one call.
PROMPT_SHORT = 160
_prompt_lines = {}              # sha1(text) -> one line
_prompt_lock = threading.Lock()


def one_line(text, n=PROMPT_SHORT):
    flat = " ".join((text or "").split())
    return flat if len(flat) <= n else flat[:n - 1].rstrip() + "…"


def summarize_prompt(text, run=None):
    text = (text or "")[:8000]
    flat = " ".join(text.split())
    if len(flat) <= PROMPT_SHORT:
        return flat
    key = hashlib.sha1(text.encode()).hexdigest()
    with _prompt_lock:
        if key in _prompt_lines:
            return _prompt_lines[key]
    ask = ("Summarise what this request asks for in ONE line of at most 100 "
           "characters, imperative voice, no preamble, no quotes:\n\n" + text)
    try:
        if run:
            line = run(ask)
        else:
            out = subprocess.run(memory.SUMMARY_CMD, input=ask, text=True,
                                 capture_output=True, timeout=60,
                                 cwd=tempfile.gettempdir())
            if out.returncode:
                raise RuntimeError(out.stderr[-200:])
            line = out.stdout
        line = one_line(line.strip().splitlines()[0] if line.strip() else "", 120)
    except Exception:
        return one_line(text)       # not cached: the next ask tries again
    if not line:
        return one_line(text)
    with _prompt_lock:
        _prompt_lines[key] = line
    return line


def queue_loop():
    while True:
        try:
            queue_tick()
        except Exception as e:   # one bad tick must not stop every later one
            print(f"queue: {e}", file=sys.stderr)
        time.sleep(QUEUE_TICK_S)


# Keeps memory.py's store fed without this file knowing anything about
# transcripts or summarising -- that is all memory.sweep()/summarize()'s job.
MEMORY_TICK_S = 60
# indexing: whether memory_loop's first sweep has completed yet, for
# /memory/stats -- read by _memory_get, written only by memory_tick.
_memory_first_sweep_done = False


def memory_tick():
    """One pass: reindex every transcript, then summarise at most one idle
    session -- one per tick, on purpose, so a backlog of idle sessions never
    turns into a burst of `claude -p` calls. MIDIAI_MEMORY_SUMMARIES=0 turns
    the summarising half off entirely (for a machine where a model call in
    the background is unwelcome); sweeping still runs either way."""
    global _memory_first_sweep_done
    conn = memory.connect()
    try:
        memory.sweep(conn)
        _memory_first_sweep_done = True
        if os.environ.get("MIDIAI_MEMORY_SUMMARIES") != "0":
            due = memory.due_for_summary(conn)
            if due:
                memory.summarize(conn, due[0])
    finally:
        conn.close()


def memory_loop():
    while True:
        try:
            memory_tick()
        except Exception as e:   # one bad tick must not stop every later one
            print(f"memory: {e}", file=sys.stderr)
        time.sleep(MEMORY_TICK_S)


def _memory_limit(qs, default=30):
    """(limit, error) from a parsed query string -- clamped to 1-100, or an
    error if the caller sent something that is not an integer at all."""
    raw = (qs.get("limit") or [None])[0]
    if raw is None:
        return default, None
    try:
        n = int(raw)
    except ValueError:
        return None, "bad request"
    return max(1, min(100, n)), None


def memory_query(path):
    """The pure half of /memory/*: parse the query, call into memory.py, and
    return (status, body, content_type). No self, no socket -- _memory_get
    calls this from a live request and test_memory_routes.py calls it
    directly, so the routing logic is exercised without binding a port."""
    split = urllib.parse.urlsplit(path)
    qs = urllib.parse.parse_qs(split.query)
    cwd = (qs.get("cwd") or [""])[0]
    project = memory.project_for(cwd) if cwd else None

    conn = memory.connect()
    try:
        if split.path == "/memory/search":
            limit, err = _memory_limit(qs)
            if err:
                return 400, err, "text/plain"
            q = (qs.get("q") or [""])[0]
            items = memory.search(conn, q, project=project, limit=limit)
            return 200, json.dumps({"items": items}), "application/json"
        if split.path == "/memory/recent":
            limit, err = _memory_limit(qs)
            if err:
                return 400, err, "text/plain"
            items = memory.recent(conn, project=project, limit=limit)
            return 200, json.dumps({"items": items}), "application/json"
        if split.path == "/memory/entry":
            raw_id = (qs.get("id") or [None])[0]
            try:
                entry_id = int(raw_id)
            except (TypeError, ValueError):
                return 400, "bad request", "text/plain"
            item = memory.get(conn, entry_id)
            if item is None:
                return 404, "not found", "text/plain"
            return 200, json.dumps({"item": item}), "application/json"
        if split.path == "/memory/stats":
            st = memory.stats(conn)
            st["indexing"] = not _memory_first_sweep_done
            return 200, json.dumps(st), "application/json"
        return 404, "not found", "text/plain"
    except sqlite3.Error as e:
        return 500, str(e), "text/plain"
    finally:
        conn.close()


# What one checkout's record may contain. Anything else a client sends is
# dropped rather than stored: this file is read back and rendered, so the
# shape it can hold is decided here and not by whatever posted last.
def clean_items(got):
    """The one definition of a guardrail item's shape. A checklist's own
    additions and a saved template hold the same thing, so they are cleaned by
    the same function -- two cleaners is two shapes, and the one that drifts is
    always the one you are not looking at."""
    text = lambda v, n: str(v or "")[:n]
    out = []
    for item in (got or [])[:400]:
        if not isinstance(item, dict) or not text(item.get("id"), 80):
            continue
        out.append({"id": text(item.get("id"), 80),
                    "phase": text(item.get("phase"), 40) or "cross",
                    "title": text(item.get("title"), 200),
                    "implemented": text(item.get("implemented"), 4000),
                    "validate": text(item.get("validate"), 4000)})
    return out


def clean_phases(got):
    """The checklist's own tabs. Empty means "whatever the app ships" -- a
    team that has never touched them should keep getting new ones as the
    framework grows, and only taking ownership stops that."""
    text = lambda v, n: str(v or "")[:n]
    out, seen = [], set()
    for phase in (got or [])[:40]:
        if not isinstance(phase, dict):
            continue
        key = text(phase.get("key"), 40)
        if not key or key in seen:
            continue          # a duplicate key would shadow a whole tab
        seen.add(key)
        out.append({"key": key,
                    "name": text(phase.get("name"), 60) or key,
                    "constrains": text(phase.get("constrains"), 60),
                    "what": text(phase.get("what"), 1000)})
    return out


# Named by a person, so a label rather than an identifier -- but it is a key in
# a file this server writes, so it is stripped and capped rather than trusted.
def clean_template_name(name):
    return " ".join(str(name or "").split())[:60]


TEMPLATES_FILE = os.path.expanduser("~/.midiai/guardrail-templates.json")


def load_templates():
    try:
        with open(TEMPLATES_FILE) as f:
            got = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return got if isinstance(got, dict) else {}


def save_templates(all_of_them):
    os.makedirs(os.path.dirname(TEMPLATES_FILE), exist_ok=True)
    tmp = TEMPLATES_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(all_of_them, f, indent=2)
    os.replace(tmp, TEMPLATES_FILE)


def clean_guardrails(got):
    if not isinstance(got, dict):
        return {"checked": {}, "custom": [], "hidden": []}
    custom = clean_items(got.get("custom"))
    return {
        # only the ticked ones: a key whose value is False is a box someone
        # un-ticked, which is the same state as never having ticked it and
        # should not outlive the gesture
        "checked": {str(k)[:80]: True
                    for k, v in (got.get("checked") or {}).items()
                    if v and isinstance(got.get("checked"), dict)},
        "custom": custom,
        "hidden": [str(i)[:80] for i in (got.get("hidden") or [])[:500]],
        "phases": clean_phases(got.get("phases")),
        # the order guardrails are listed in, rearranged by dragging; ids not
        # in it keep the shipped order after the ones that are
        "order": [str(i)[:80] for i in (got.get("order") or [])[:800]
                  if isinstance(i, (str, int))],
    }


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


SKIP_WALK = {"node_modules", ".venv", ".git", "__pycache__"}


def plugin_skill_files(where):
    """Every SKILL.md under a `skills/` directory at any depth in one
    plugin -- mattpocock files its as skills/<category>/<skill>/, so a fixed
    depth drops them. Walked with node_modules and friends pruned before
    they are entered: the old `**/skills/**/SKILL.md` glob read every one of
    them and filtered after, 11,500 directories and ~2.2s of /catalog's
    2.35s, which at page load held up every request behind it."""
    out = []
    for top, dirs, files in os.walk(where):
        # hidden folders too, as the glob did: .openclaw/skills and the like
        # are a plugin's copies for other tools, not Claude Code skills
        dirs[:] = [d for d in dirs if d not in SKIP_WALK and not d.startswith(".")]
        if "SKILL.md" in files and "skills" in os.path.relpath(top, where).split(os.sep):
            out.append(os.path.join(top, "SKILL.md"))
    return out


def plain_checkout(path):
    """A folder with no repo, shaped like a worktree row so the rail draws it
    the same way: its one checkout, on no branch."""
    return {"path": path, "branch": "", "head": "", "main": True, "detached": False,
            "locked": False, "prunable": False, "exists": True}


def git(root, *argv, timeout=30):
    """The one call every /work route makes into git -- argv stays a list, so
    a path or message with spaces or shell metacharacters in it can never be
    reinterpreted, the same guarantee _repo_request and _pr_read already
    lean on."""
    return subprocess.run(["git", "-C", root, *argv],
                          capture_output=True, text=True, timeout=timeout)


STATUS_WORD = {"M": "modified", "A": "added", "D": "deleted", "R": "renamed",
              "C": "copied", "T": "typechange", "U": "conflicted", "?": "untracked"}
CONFLICTS = {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}


def work_status(root):
    """The working tree, split the three ways the GIT tab draws it: staged,
    unstaged, and conflicted. `-z` NUL-terminates every part instead of
    newline-separating them, which is what lets a path containing a newline
    round-trip correctly; `-b` puts the `## ...` header in front as its own
    part rather than a separate stream. A renamed or copied path carries its
    old name as the NEXT part rather than on the same line, so that part has
    to be consumed even when the entry turns out to be a conflict."""
    out = git(root, "status", "--porcelain=v1", "-b", "-z", "--untracked-files=all")
    parts = out.stdout.split("\0")
    head_line = parts[0] if parts else ""
    staged, unstaged, conflicts = [], [], []
    i = 1
    while i < len(parts):
        entry = parts[i]
        i += 1
        if not entry:
            continue
        x, y, path = entry[0], entry[1], entry[3:]
        old_path = ""
        if x in ("R", "C"):
            old_path = parts[i] if i < len(parts) else ""
            i += 1
        if x + y in CONFLICTS:
            conflicts.append({"path": path, "state": STATUS_WORD["U"], "was": old_path})
            continue
        if x not in (" ", "?"):
            staged.append({"path": path, "state": STATUS_WORD.get(x, x), "was": old_path})
        if y != " ":
            # untracked ("??") falls through to here too -- x is "?" so the
            # staged check above never fires for it, and y is "?" as well, so
            # STATUS_WORD["?"] gives it "untracked" with no special-casing
            unstaged.append({"path": path, "state": STATUS_WORD.get(y, y), "was": old_path})
    return head_line, staged, unstaged, conflicts


def work_head(head_line):
    """The `## ...` header line from `git status -b`, split into the branch
    facts the GIT tab's header row shows. Four shapes to cover: a plain
    branch, one tracking an upstream (with or without an ahead/behind
    bracket), detached HEAD, and a fresh repo that has never committed."""
    line = head_line[3:] if head_line.startswith("## ") else head_line
    if line.startswith("HEAD ("):
        return {"branch": "", "upstream": "", "ahead": 0, "behind": 0, "detached": True}
    if line.startswith("No commits yet on "):
        return {"branch": line[len("No commits yet on "):], "upstream": "",
                "ahead": 0, "behind": 0, "detached": False}
    branch, sep, rest = line.partition("...")
    upstream, ahead, behind = "", 0, 0
    if sep:
        upstream, _, bracket = rest.partition(" [")
        m = re.search(r"ahead (\d+)", bracket)
        ahead = int(m.group(1)) if m else 0
        m = re.search(r"behind (\d+)", bracket)
        behind = int(m.group(1)) if m else 0
    return {"branch": branch, "upstream": upstream, "ahead": ahead,
            "behind": behind, "detached": False}


def graph_lanes(commits):
    """Lay commits (newest first, each {"sha", "parents"}) out in lanes, the
    way GitKraken draws them: a commit's first parent carries on in its lane,
    a merge's other parents open or join lanes beside it, and a lane ends when
    the branch it was waiting for turns out to be one already drawn.

    Adds "col" (the commit's lane), "width" (lanes in play on that row) and
    "edges" -- [x1, y1, x2, y2, lane] with x in lanes and y in half-rows
    (0 top, 1 the dot, 2 bottom) -- so the app only has to draw lines."""
    lanes = []                      # lane -> the sha it is waiting to reach
    for c in commits:
        sha, parents = c["sha"], c["parents"]
        if sha in lanes:
            col = lanes.index(sha)
        else:                       # a branch tip nothing above points at
            col = lanes.index(None) if None in lanes else len(lanes)
            if col == len(lanes):
                lanes.append(None)
        edges = []
        for i, want in enumerate(lanes):
            if want == sha:         # this lane, and any others ending here
                edges.append([i, 0, col, 1, i])
                if i != col:
                    lanes[i] = None
            elif want is not None:  # passing straight by
                edges.append([i, 0, i, 2, i])
        lanes[col] = parents[0] if parents else None
        for n, p in enumerate(parents):
            if n == 0:
                j = col
            elif p in lanes:
                j = lanes.index(p)
            else:
                j = lanes.index(None) if None in lanes else len(lanes)
                if j == len(lanes):
                    lanes.append(None)
                lanes[j] = p
            edges.append([col, 1, j, 2, j if n else col])
        while lanes and lanes[-1] is None:
            lanes.pop()
        c.update(col=col, edges=edges,
                 width=max([col] + [max(e[0], e[2]) for e in edges]) + 1)
    return commits


def work_log(root, limit=150):
    """The commit graph for the GIT tab: every branch, laid out in lanes by
    graph_lanes. Topo order so a commit always comes after every child it
    has, which the lane walk depends on. A repo with no commits yet makes git
    exit non-zero rather than print nothing, so that is "no log", not an
    error."""
    fmt = "%H\x1f%P\x1f%h\x1f%an\x1f%ar\x1f%D\x1f%s"
    out = git(root, "log", "--all", "--topo-order", "--decorate=short",
             f"--max-count={limit}", f"--format={fmt}")
    if out.returncode:
        return []
    remotes = git(root, "remote").stdout.split()
    rows = []
    for line in out.stdout.split("\n"):
        if "\x1f" not in line:
            continue
        sha, parents, short, who, when, refs, subject = line.split("\x1f", 6)
        rows.append({"sha": sha, "parents": parents.split(), "short": short,
                     "who": who, "when": when, "subject": subject,
                     "refs": ref_pills(refs, remotes)})
    return graph_lanes(rows)


def ref_pills(decorate, remotes):
    """%D ("HEAD -> main, origin/main, tag: v1") as {name, kind, head}. A
    slash says nothing -- feat/x is a local branch -- so remote is decided by
    this repo's actual remote names."""
    out = []
    for r in filter(None, decorate.split(", ")):
        head = r.startswith("HEAD -> ")
        r = r[8:] if head else r
        if r.startswith("tag: "):
            out.append({"name": r[5:], "kind": "tag", "head": False})
        elif r == "HEAD" or r.split("/", 1)[0] in remotes:
            out.append({"name": r, "kind": "remote", "head": r == "HEAD"})
        else:
            out.append({"name": r, "kind": "local", "head": head})
    return out


# The commit box's "write it": a message drafted from the change itself. The
# staged diff when there is one -- that is what the commit will hold --
# else everything uncommitted, with a note saying so, since the commit key
# still only takes what is staged. The repo's own recent subjects go in as
# the house style to follow. Drafted, never committed: it lands in the box.
AI_DIFF_MAX = 30000


def commit_message(root, run=None):
    """(message, scope) where scope is "staged" or "all"; raises on failure."""
    staged = git(root, "diff", "--cached", "--stat", "--patch", "--no-color").stdout
    scope = "staged" if staged.strip() else "all"
    diff = staged if staged.strip() else git(root, "diff", "--stat", "--patch", "--no-color").stdout
    if scope == "all":
        untracked = git(root, "ls-files", "--others", "--exclude-standard").stdout.split()
        if untracked:
            diff += "\n\nNew untracked files:\n" + "\n".join(untracked[:200])
    if not diff.strip():
        raise ValueError("nothing has changed to write a message about")
    if len(diff) > AI_DIFF_MAX:
        diff = diff[:AI_DIFF_MAX] + "\n[diff truncated]"
    recent = git(root, "log", "-12", "--format=%s").stdout.strip()
    ask = ("Write a git commit message for the change below.\n\n"
           "Match the style of this repository's recent subjects:\n" + (recent or "(no history)") +
           "\n\nRules: a subject line of at most 72 characters in that style, a blank "
           "line, then a short body saying what changed and why. No trailers, no "
           "code fences, no preamble -- output only the message.\n\nThe change:\n" + diff)
    if run:
        out = run(ask)
    else:
        done = subprocess.run(memory.SUMMARY_CMD, input=ask, text=True,
                              capture_output=True, timeout=120, cwd=tempfile.gettempdir())
        if done.returncode:
            raise RuntimeError((done.stderr or "the model call failed")[-300:])
        out = done.stdout
    text = out.strip()
    if text.startswith("```"):       # a fence despite being asked not to
        text = text.strip("`").split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    if not text:
        raise RuntimeError("the model returned nothing")
    return text, scope


def work_numstat(root, cached):
    """{path: (added, deleted)} for the staged (cached) or unstaged side, so
    each file row can say how big its change is. A binary file reports "-"
    and is left out -- no count beats a wrong one."""
    out = git(root, "diff", "--numstat", "--no-color", *(["--cached"] if cached else []))
    counts = {}
    for line in out.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            counts[parts[2]] = (int(parts[0]), int(parts[1]))
    return counts


def work_stashes(root):
    """The stash list for the GIT tab's stash panel. An empty list here means
    either no stashes or git failing outright -- both render the same way in
    the app, so there is nothing to distinguish."""
    out = git(root, "stash", "list", "--format=%gd\x1f%s")
    if out.returncode:
        return []
    rows = []
    for line in out.stdout.split("\n"):
        if not line:
            continue
        ref, sep, text = line.partition("\x1f")
        rows.append({"ref": ref, "text": text})
    return rows


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

# Which terminal to hand a pane to. Same bargain as EDITOR_CMD: the caller
# picks WHAT to open and never WITH WHAT, so there is no argument on this wire
# that becomes a command. Empty means whatever the OS opens a .command with,
# which on a Mac is Terminal.
TERMINAL_APP = os.environ.get("MIDIAI_TERMINAL", "")

# A tmux pane id and nothing else. This is the only thing from the request that
# reaches the script below, and `%` followed by digits has nothing in it to
# quote wrong -- which is why the script can be written as text at all.
PANE_ID_RE = re.compile(r"^%\d+$")
SUB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


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


# A subagent's model is fixed the moment its parent dispatches it -- nothing
# outside can reach into a running one. What can change is the definition
# the NEXT dispatch of that type is built from: its `model:` frontmatter.
# Project definitions shadow user ones, the same order Claude Code loads
# them in. Plugin agents are left alone (the next plugin update would undo
# the edit without saying so) and built-ins like Explore have no file.
AGENT_MODELS = ("inherit", "haiku", "sonnet", "opus", "fable")
_FRONT_MODEL = re.compile(r"^model:.*$", re.M)


def agent_def(agent_type, cwd):
    """(path, scope) of the definition file for a subagent type, or
    (None, None). The type is a path component here, so it has to pass the
    same name check a skill's directory does before it gets near a join."""
    if not SKILL_NAME_RE.match(agent_type or ""):
        return None, None
    rows = repo_worktrees(cwd) if cwd else []
    root = rows[0]["path"] if rows else cwd
    places = ([("project", os.path.join(root, ".claude", "agents"))] if root else []) + \
        [("user", os.path.expanduser("~/.claude/agents"))]
    for scope, folder in places:
        path = os.path.join(folder, agent_type + ".md")
        if os.path.isfile(path):
            return path, scope
    return None, None


def agent_model(path):
    """The `model:` a definition pins, or "inherit" when it pins none."""
    try:
        with open(path) as f:
            text = f.read()
    except OSError:
        return ""
    head = text.split("\n---", 1)[0] if text.startswith("---") else ""
    m = _FRONT_MODEL.search(head)
    return m.group(0).split(":", 1)[1].strip() if m else "inherit"


def set_agent_model(path, model):
    """Rewrite one line of the frontmatter; everything else in the file is
    the author's and goes back byte for byte. None on success, else why."""
    try:
        with open(path) as f:
            text = f.read()
    except OSError as e:
        return f"could not read {path}: {e}"
    if not text.startswith("---\n") or "\n---" not in text[3:]:
        return f"{path} has no frontmatter to set a model in"
    end = text.index("\n---", 3)
    head, rest = text[:end], text[end:]
    line = f"model: {model}"
    head = _FRONT_MODEL.sub(line, head, count=1) if _FRONT_MODEL.search(head) \
        else head + "\n" + line
    tmp = path + ".tmp"
    try:
        with open(tmp, "w") as f:
            f.write(head + rest)
        os.replace(tmp, path)
    except OSError as e:
        return f"could not write {path}: {e}"
    return None


def agent_def_row(agent_type, cwd):
    path, scope = agent_def(agent_type, cwd)
    return {"type": agent_type, "path": path, "scope": scope,
            "model": agent_model(path) if path else "",
            "editable": bool(path)}


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
SKILL_LABELS_FILE = os.path.expanduser("~/.midiai/skill-labels.json")


def skill_label_key(scope, name, root="", path=""):
    place = path if scope == "plugin" else (root if scope == "project" else "")
    return "\u0000".join([scope or "", os.path.realpath(place) if place else "", name or ""])


def load_skill_labels():
    try:
        with open(SKILL_LABELS_FILE) as f:
            got = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return got if isinstance(got, dict) else {}


def save_skill_labels(labels):
    try:
        os.makedirs(os.path.dirname(SKILL_LABELS_FILE), exist_ok=True)
        tmp = SKILL_LABELS_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(labels, f, indent=1, sort_keys=True)
        os.replace(tmp, SKILL_LABELS_FILE)
    except OSError as e:
        print(f"skill labels: {e}", file=sys.stderr, flush=True)


def set_skill_label(scope, name, label, root="", path=""):
    labels = load_skill_labels()
    key = skill_label_key(scope, name, root, path)
    label = " ".join(str(label or "").split())[:80]
    if label:
        labels[key] = label
    else:
        labels.pop(key, None)
    save_skill_labels(labels)


def parked_key(scope, event, matcher, summary):
    """The id a parked hook is found again by.

    A hash, not the readable key the notes file uses, because this one travels:
    the app sends it back to turn the hook on again. note_key joins its parts
    with NUL, which is fine in a file nobody transports and a nuisance in
    anything that has to survive a shell, a log line or a grep."""
    return hashlib.sha1(
        note_key(scope, event, matcher, summary).encode()).hexdigest()[:16]


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


def hook_note(notes, key):
    """Normalize old description-only notes and current metadata records."""
    value = notes.get(key, "")
    if isinstance(value, dict):
        return {"description": str(value.get("description") or ""),
                "label": str(value.get("label") or "")}
    return {"description": str(value or ""), "label": ""}


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


# Where a setting is read from, highest precedence first -- Claude Code's own
# order, minus the two levels above it (managed settings and --settings) which
# this app has no business writing. First file that names a key wins.
def settings_chain(root):
    return [("local", os.path.join(root, ".claude", "settings.local.json")),
            ("project", os.path.join(root, ".claude", "settings.json")),
            ("user", os.path.expanduser("~/.claude/settings.json"))]


def read_settings(path):
    try:
        with open(path) as f:
            got = json.load(f)
        return got if isinstance(got, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def mcp_rows(cwd):
    """MCP servers visible from ``cwd``, after Claude Code's scope precedence.

    MCP configuration deliberately does not live in settings.json: user and
    private-per-project entries are in ~/.claude.json, while shared project
    entries are in the repository's .mcp.json.  Return only display metadata;
    env and headers can contain secrets and never belong on this wire.
    """
    rows = repo_worktrees(cwd)
    root = rows[0]["path"] if rows else cwd
    home = read_settings(os.path.expanduser("~/.claude.json"))
    sources = []

    projects = home.get("projects") if isinstance(home, dict) else None
    if isinstance(projects, dict):
        # Claude keys local MCPs by the directory the project was opened in.
        # Prefer the exact cwd, with the resolved repository root as fallback.
        for candidate in dict.fromkeys([cwd, root]):
            rec = projects.get(candidate)
            servers = rec.get("mcpServers") if isinstance(rec, dict) else None
            if isinstance(servers, dict):
                sources.append(("local", servers))

    shared = read_settings(os.path.join(root, ".mcp.json")).get("mcpServers")
    if isinstance(shared, dict):
        sources.append(("project", shared))
    user = home.get("mcpServers") if isinstance(home, dict) else None
    if isinstance(user, dict):
        sources.append(("user", user))

    # Enabled plugins may bundle MCPs at their root. They are lower precedence
    # than all three manually configured scopes and are still useful to see:
    # for many installs these are most of the MCPs Claude actually starts.
    try:
        installed = read_settings(
            os.path.expanduser("~/.claude/plugins/installed_plugins.json")
        ).get("plugins", {})
    except AttributeError:
        installed = {}
    if isinstance(installed, dict):
        for plugin_key, records in installed.items():
            enabled, _ = resolve_in_map(root, plugin_key, "enabledPlugins")
            if enabled is False or not isinstance(records, list) or not records:
                continue
            record = records[0] if isinstance(records[0], dict) else {}
            install_path = record.get("installPath")
            if not isinstance(install_path, str):
                continue
            short = plugin_key.split("@")[0]
            plugin_mcp = read_settings(os.path.join(install_path, ".mcp.json"))
            # Plugin files exist in both accepted shapes: the standard
            # {mcpServers:{...}} wrapper and a direct name-to-config map.
            found = plugin_mcp.get("mcpServers", plugin_mcp)
            servers = dict(found) if isinstance(found, dict) else {}
            # ...and a third site this missed entirely: a marketplace entry can
            # declare mcpServers inline, with no .mcp.json anywhere in the
            # install. Firebase does, which is how the one server that was
            # actually failing became the one server the rail could not draw.
            market = read_settings(os.path.join(install_path, ".claude-plugin",
                                                "marketplace.json"))
            for entry in market.get("plugins") or []:
                if isinstance(entry, dict) and entry.get("name") == short:
                    inline = entry.get("mcpServers")
                    if isinstance(inline, dict):
                        for key, config in inline.items():
                            servers.setdefault(key, config)
            if servers:
                # Preserve the plugin identity because two plugins can use the
                # same short server name without being the same MCP.
                sources.append(("plugin", {
                    f"{short}:{name}": config for name, config in servers.items()
                }))

    out, seen = [], set()
    for scope, servers in sources:       # local > project > user
        for name, config in servers.items():
            if not isinstance(name, str) or name in seen:
                continue
            seen.add(name)
            config = config if isinstance(config, dict) else {}
            transport = str(config.get("type") or ("http" if config.get("url") else "stdio"))
            endpoint = config.get("url") or config.get("command") or ""
            if config.get("url"):
                # Query strings and userinfo are common places for credentials.
                # The rail needs an address, never authentication material.
                parsed = urllib.parse.urlsplit(str(endpoint))
                host = parsed.hostname or ""
                if parsed.port:
                    host += f":{parsed.port}"
                endpoint = urllib.parse.urlunsplit(
                    (parsed.scheme, host, parsed.path, "", "")
                )
            out.append({"name": name, "scope": scope, "transport": transport,
                        "endpoint": str(endpoint)[:300]})
    return sorted(out, key=lambda row: row["name"].lower())


MCP_TTL = 120.0     # a health check spawns every stdio server; do it rarely
_mcp_health = {}    # cwd -> {at, busy, rows, err}


def mcp_health_fetch(cwd, names):
    """One `claude mcp list`, on its own thread.

    Nine seconds, because it starts every configured stdio server and speaks
    to every remote one. That is far too long to hold a request open, and far
    too expensive to repeat per poll -- so it runs behind the list the way
    pr_fetch runs behind the PR list, and the first answer says `checking`
    rather than lying `down`."""
    slot = _mcp_health[cwd]
    try:
        out = subprocess.run(["claude", "mcp", "list"], cwd=cwd,
                             capture_output=True, text=True, timeout=120)
        rows = push_cc.mcp_health(out.stdout + "\n" + out.stderr, names)
        slot.update(rows=rows, err="" if rows else (out.stderr.strip()[:120]),
                    busy=False)
    except Exception as e:
        slot.update(rows={}, err=type(e).__name__, busy=False)


def mcp_health_for(cwd, names, now):
    """The cached health for this checkout, refreshing behind you."""
    slot = _mcp_health.setdefault(cwd, {"at": -MCP_TTL, "rows": {}, "busy": False,
                                        "err": ""})
    if not slot["busy"] and now - slot["at"] >= MCP_TTL:
        slot.update(at=now, busy=True)
        threading.Thread(target=mcp_health_fetch, args=(cwd, names),
                         daemon=True).start()
    return slot


def resolve_in_map(root, key, sub):
    """(value, scope) for `sub[key]` -- the first settings file that names it.

    Reported rather than assumed: a plugin or a skill with no entry anywhere is
    a real state, and saying "on" about it would be inventing a setting the
    user never made."""
    for scope, path in settings_chain(root):
        got = read_settings(path).get(sub)
        if isinstance(got, dict) and key in got:
            return got[key], scope
    return None, None


# The four states a skill can be in, from Claude Code's own reference. Absent
# means "on" -- so turning one back on deletes the key rather than writing the
# word, and settings.json stays a list of the decisions actually made.
SKILL_STATES = ("on", "name-only", "user-invocable-only", "off")

# A hook has no off switch in the schema: the only documented one is
# disableAllHooks, which takes the status line and @ suggestions with it. So a
# hook that is off lives here instead, whole, and goes back where it was when
# it is turned on again. settings.json keeps only what Claude Code documents.
PARKED_FILE = os.path.expanduser("~/.midiai/hooks-parked.json")


def load_parked():
    try:
        with open(PARKED_FILE) as f:
            got = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return got if isinstance(got, dict) else {}


def save_parked(parked):
    try:
        os.makedirs(os.path.dirname(PARKED_FILE), exist_ok=True)
        tmp = PARKED_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(parked, f, indent=1, sort_keys=True)
        os.replace(tmp, PARKED_FILE)
    except OSError as e:
        print(f"parked hooks: {e}", file=sys.stderr, flush=True)


def installed_plugins():
    """Every plugin on this machine, as `name@marketplace`.

    From the plugin manager's own record rather than by walking the cache
    directory: the cache holds a directory per version, and a plugin that was
    updated this morning has two."""
    try:
        with open(os.path.expanduser("~/.claude/plugins/installed_plugins.json")) as f:
            got = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    plugins = got.get("plugins") if isinstance(got, dict) else None
    return sorted(plugins) if isinstance(plugins, dict) else []


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
                note = hook_note(notes, note_key(scope, event, matcher, summary))
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
                            "description": note["description"],
                            "label": note["label"],
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
        if self.path.startswith("/history"):
            return self._history()
        if self.path.startswith("/pty/where"):
            return self._pty_where()
        if self.path.startswith("/pty/stream"):
            return self._pty_stream()
        if self.path == "/surface":
            try:
                with open(push_cc.SURFACE_FILE) as f:
                    return self._send(200, f.read(), "application/json")
            except OSError:
                return self._send(200, "{}", "application/json")
        if self.path == "/target":
            return self._send(200, json.dumps({**read_target(), **push_state()}),
                              "application/json")
        if self.path == "/pr" or self.path.startswith("/pr?"):
            return self._pr_read()
        if self.path == "/prs" or self.path.startswith("/prs?"):
            return self._prs_read()
        if self.path == "/guardrail-templates":
            return self._templates_read()
        if self.path == "/guardrails" or self.path.startswith("/guardrails?"):
            return self._guardrails_read()
        if self.path == "/guardrails/enforce" or self.path.startswith("/guardrails/enforce?"):
            return self._guardrails_enforce_read()
        if self.path == "/governs" or self.path.startswith("/governs?"):
            return self._governs_read()
        if self.path == "/govern" or self.path.startswith("/govern?"):
            return self._govern_read()
        if self.path == "/workflows" or self.path.startswith("/workflows?"):
            return self._workflows_read()
        if self.path == "/workflow" or self.path.startswith("/workflow?"):
            return self._workflow_read()
        if self.path == "/tests" or self.path.startswith("/tests?"):
            return self._tests_read()
        # exact "==" plus a "?"-qualified startswith, never a bare prefix --
        # otherwise this would also swallow /work/diff
        if self.path == "/work" or self.path.startswith("/work?"):
            return self._work_read()
        if self.path.startswith("/work/dirty?"):
            return self._work_dirty()
        if self.path == "/work/diff" or self.path.startswith("/work/diff?"):
            return self._work_diff()
        if self.path.startswith("/agent-def?"):
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            row = agent_def_row((q.get("type") or [""])[0], (q.get("cwd") or [""])[0])
            return self._send(200, json.dumps(row), "application/json")
        if self.path.startswith("/memory/"):
            return self._memory_get()
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
        elif self.path == "/settings/claude":
            self._send(200, json.dumps(access.state()), "application/json")
        elif self.path == "/agents":
            self._send(200, json.dumps({"agents": push_cc.agents()}),
                      "application/json")
        elif self.path.startswith("/queue?"):
            query = urllib.parse.urlsplit(self.path).query
            tid = (urllib.parse.parse_qs(query).get("terminal_id") or [""])[0]
            with _queue_lock:
                items = load_queue().get(tid, [])
            self._send(200, json.dumps(queue_state(tid, items)), "application/json")
        elif self.path == "/mcp/health" or self.path.startswith("/mcp/health?"):
            return self._mcp_health()
        elif self.path == "/mcps" or self.path.startswith("/mcps?"):
            query = urllib.parse.urlsplit(self.path).query
            cwd = (urllib.parse.parse_qs(query).get("cwd") or [None])[0] or target_cwd()
            self._send(200, json.dumps({"mcps": mcp_rows(cwd)}),
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
        if self.path == "/skill/state":
            return self._skill_state()
        if self.path == "/hook/toggle":
            return self._hook_toggle()
        if self.path == "/plugin/toggle":
            return self._plugin_toggle()
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
        if self.path == "/pty/input":
            return self._pty_write("input")
        if self.path == "/pty/resize":
            return self._pty_write("resize")
        if self.path == "/pty/close":
            return self._pty_write("close")
        if self.path == "/terminal/open":
            return self._terminal_open()
        if self.path == "/keys":
            return self._keys()
        if self.path == "/prompt":
            return self._prompt()
        if self.path.startswith("/settings/claude"):
            return self._claude_settings()
        if self.path in ("/queue/play", "/queue/next"):
            return self._queue_control()
        if self.path in ("/queue", "/queue/add"):
            return self._queue_post()
        if self.path == "/summarize-prompt":
            body = self._read_json_body() or {}
            return self._send(200, json.dumps({"summary": summarize_prompt(str(body.get("text") or ""))}),
                              "application/json")
        if self.path == "/agent-def/model":
            return self._agent_model_post()
        if self.path == "/paste":
            return self._paste()
        if self.path == "/record/start":
            return self._record_start()
        if self.path == "/record/stop":
            return self._record_stop()
        if self.path == "/pr/review":
            return self._pr_review()
        if self.path == "/workflow":
            return self._workflow_write()
        if self.path == "/govern":
            return self._govern_write()
        if self.path.startswith("/mcp/") and self.path != "/mcp/health":
            return self._mcp_do(self.path[len("/mcp/"):])
        if self.path == "/guardrails":
            return self._guardrails_write()
        if self.path == "/guardrails/compile":
            return self._guardrails_compile()
        if self.path == "/guardrails/rail":
            return self._guardrails_rail()
        if self.path == "/guardrails/approve":
            return self._guardrails_approve()
        if self.path == "/guardrails/run":
            return self._guardrails_run()
        if self.path == "/guardrails/blocking":
            return self._guardrails_blocking()
        if self.path == "/guardrails/trigger":
            return self._guardrails_trigger()
        if self.path == "/guardrails/hooks":
            return self._guardrails_hooks()
        if self.path == "/guardrail-template":
            return self._template_write()
        if self.path == "/guardrail-template/delete":
            return self._template_delete()
        if self.path == "/tests/run":
            return self._tests_run()
        if self.path == "/tests/stop":
            return self._tests_stop()
        if self.path == "/work/init":
            return self._work_init()
        if self.path == "/work/ai-message":
            return self._work_ai_message()
        if self.path == "/work/do":
            return self._work_do()
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
               "prefix": e.get("prefix", ""),
               "dynamic": e.get("dynamic", ""),
               "fields": [
                   {"name": str(field.get("name", ""))[:40],
                    "label": str(field.get("label", ""))[:80],
                    "placeholder": str(field.get("placeholder", ""))[:160]}
                   for field in (e.get("fields") or [])[:20]
                   if isinstance(field, dict)
               ],
               "colour": int(e.get("colour", push_cc.BLUE)) & 0x7F,
               "tag": e.get("tag"), "submit": bool(e.get("submit")),
               "scope": e.get("scope", "global"),
               "project": e.get("project", ""),
               "worktree": e.get("worktree", "")}
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
                    for k in ("tab", "seat", "sub", "page", "answer") if k in cmd}
            if "answer_text" in cmd:
                answer_text = cmd["answer_text"]
                if (not isinstance(answer_text, str) or not answer_text.strip()
                        or len(answer_text) > 10000):
                    raise ValueError("bad custom answer")
                keep["answer_text"] = answer_text.strip()
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

    def _repo_request(self, body=None):
        """Resolve only an existing git checkout supplied by the app."""
        cwd = (body or {}).get("cwd") or target_cwd()
        cwd = os.path.realpath(os.path.expanduser(str(cwd)))
        if not os.path.isdir(cwd):
            return None, "checkout does not exist"
        check = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"],
                               capture_output=True, text=True, timeout=10)
        if check.returncode:
            return None, "not a git checkout"
        return check.stdout.strip(), ""

    def _pr_read(self):
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        root, err = self._repo_request({"cwd": (q.get("cwd") or [""])[0]})
        try:
            number = int((q.get("number") or [0])[0])
        except (TypeError, ValueError):
            number = 0
        if err or number < 1:
            return self._send(400, err or "invalid PR number", "text/plain")
        fields = "number,title,body,author,baseRefName,headRefName,files,statusCheckRollup,url"
        out = subprocess.run(["gh", "pr", "view", str(number), "--json", fields],
                             cwd=root, capture_output=True, text=True, timeout=30)
        if out.returncode:
            return self._send(502, (out.stderr.strip() or "gh failed")[:500], "text/plain")
        diff = subprocess.run(["gh", "pr", "diff", str(number)], cwd=root,
                              capture_output=True, text=True, timeout=45)
        try:
            row = json.loads(out.stdout)
        except json.JSONDecodeError:
            return self._send(502, "gh returned invalid JSON", "text/plain")
        rollup = row.pop("statusCheckRollup", []) or []
        row.update(author=(row.get("author") or {}).get("login", ""),
                   base=row.pop("baseRefName", ""), head=row.pop("headRefName", ""),
                   checks=push_cc.check_state(rollup),
                   # the colour on the list row, spelled out: which check, and
                   # where to go when it is the red one
                   runs=push_cc.check_rows(rollup),
                   diff=(diff.stdout if not diff.returncode else diff.stderr)[:250000])
        self._send(200, json.dumps(row), "application/json")

    def _prs_read(self):
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        root, err = self._repo_request({"cwd": (q.get("cwd") or [""])[0]})
        if err:
            return self._send(400, err, "text/plain")
        head = subprocess.run(["git", "-C", root, "branch", "--show-current"],
                              capture_output=True, text=True, timeout=10)
        fields = "number,title,author,isDraft,reviewDecision,statusCheckRollup,headRefName"
        out = subprocess.run(["gh", "pr", "list", "--limit", str(push_cc.PR_MAX),
                              "--json", fields], cwd=root, capture_output=True,
                             text=True, timeout=30)
        if out.returncode:
            return self._send(502, (out.stderr.strip() or "gh failed")[:500], "text/plain")
        try:
            rows = push_cc.pr_rows(json.loads(out.stdout), head.stdout.strip())
        except json.JSONDecodeError:
            return self._send(502, "gh returned invalid JSON", "text/plain")
        self._send(200, json.dumps({"kind": "prs", "repo": os.path.basename(root),
                                    "rows": rows, "err": ""}), "application/json")

    def _workflows_read(self):
        """Every workflow in the checkout, described by its own contents.

        A directory listing, not a `gh` call: these are files in the tree, and
        the ones that matter most are the ones that have never run -- a
        workflow added on a branch has no runs to be found by, and asking
        GitHub about it would return nothing at all."""
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        root, err = self._repo_request({"cwd": (q.get("cwd") or [""])[0]})
        if err:
            return self._send(400, err, "text/plain")
        rows = []
        here = os.path.join(root, push_cc.WORKFLOW_DIR)
        for path in sorted(glob.glob(os.path.join(here, "*.yml")) +
                           glob.glob(os.path.join(here, "*.yaml"))):
            stem = os.path.splitext(os.path.basename(path))[0]
            try:
                text = open(path).read()
            except OSError:
                continue
            # `editable` is the honest half of the name rule: a file already on
            # disk under a name this server will not write back is still worth
            # listing and reading, it just cannot be saved from here, and the
            # screen has to know that before it draws a Save.
            rows.append({"file": os.path.basename(path), "name": stem,
                         "lines": text.count("\n") + 1,
                         "editable": bool(push_cc.WORKFLOW_NAME_RE.match(stem))
                                     and path.endswith(".yml"),
                         **push_cc.workflow_meta(text)})
        self._send(200, json.dumps({"kind": "workflows", "dir": push_cc.WORKFLOW_DIR,
                                    "repo": os.path.basename(root), "rows": rows}),
                   "application/json")

    def _workflow_read(self):
        """One workflow's text, by name."""
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        root, err = self._repo_request({"cwd": (q.get("cwd") or [""])[0]})
        path = push_cc.workflow_path((q.get("name") or [""])[0], root)
        if err or not path:
            return self._send(400, err or "invalid workflow name", "text/plain")
        try:
            with open(path) as f:
                text = f.read()
        except OSError as e:
            return self._send(404, f"could not read it: {e}", "text/plain")
        self._send(200, json.dumps({"name": os.path.splitext(os.path.basename(path))[0],
                                    "path": os.path.relpath(path, root), "body": text,
                                    **push_cc.workflow_meta(text)}), "application/json")

    def _workflow_write(self):
        """Create or replace one workflow, the same bargain _skill_write drives.

        The caller sends a name, never a path: WORKFLOW_NAME_RE is what makes
        that safe to expose on a server --lan puts on the network, and
        `.github/workflows` is named by this file. Create and update are one
        route because the name alone decides the path, so "the one already
        there" and "a new one" are the same write.

        A workflow runs on somebody else's machine with this repo's secrets, so
        the one thing this will not do is write a file nobody asked for by that
        name: `replace` is the editor saying it opened this one and meant to
        change it, and without it an existing workflow is a 409 rather than a
        silent overwrite of a pipeline someone is relying on."""
        body = self._read_json_body()
        if body is None:
            return self._send(400, "bad request", "text/plain")
        root, err = self._repo_request(body)
        path = push_cc.workflow_path(body.get("name"), root)
        text = body.get("body")
        if err or not path or not isinstance(text, str):
            return self._send(
                400, err or "a workflow name is lowercase letters, digits and "
                            "dashes, starting with a letter", "text/plain")
        if not text.strip():
            return self._send(400, "an empty workflow is not a workflow", "text/plain")
        self._write_repo_file(path, text, root, bool(body.get("replace")))

    def _write_repo_file(self, path, text, root, replace):
        """Put text at a path this server derived, or say why not.

        Both editable things in the repo -- a workflow and the files that
        govern a pull request -- land here, because these four lines are the
        whole security boundary and two copies of them is two chances to fix
        only one. The caller has already turned whatever it was given into a
        path; nothing here takes one from a request."""
        if os.path.exists(path) and not replace:
            return self._send(409, f"{os.path.basename(path)} already exists",
                              "text/plain")
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w") as f:
                f.write(text if text.endswith("\n") else text + "\n")
            os.replace(tmp, path)
        except OSError as e:
            return self._send(500, f"could not write it: {e}", "text/plain")
        self._send(200, json.dumps({"path": os.path.relpath(path, root)}),
                   "application/json")

    def _templates_read(self):
        """Every saved checklist, whole.

        Not a name list plus a fetch per name: a template is a few kilobytes,
        there are never many, and the screen wants to say how big each one is
        before you pick it. One request answers the whole panel."""
        rows = []
        for name, spot in sorted(load_templates().items()):
            if not isinstance(spot, dict):
                continue
            rows.append({"name": name, "made": spot.get("made") or 0,
                         "items": clean_items(spot.get("items")),
                         "phases": clean_phases(spot.get("phases"))})
        self._send(200, json.dumps({"kind": "guardrail-templates", "rows": rows}),
                   "application/json")

    def _template_write(self):
        """Save a checklist under a name, for use in another checkout.

        The items and not the ticks: which guardrails a team holds itself to
        travels between repos, and whether each one is actually in force is a
        fact about one repo that would be a lie anywhere else."""
        body = self._read_json_body()
        if body is None:
            return self._send(400, "bad request", "text/plain")
        name = clean_template_name(body.get("name"))
        items = clean_items(body.get("items"))
        if not name:
            return self._send(400, "a template needs a name", "text/plain")
        if not items:
            return self._send(400, "an empty checklist is not a template", "text/plain")
        everything = load_templates()
        # same bargain as a workflow: overwriting one someone else is loading
        # from is a way to lose a list by reusing its name
        if name in everything and not body.get("replace"):
            return self._send(409, f"a template called {name} already exists",
                              "text/plain")
        everything[name] = {"made": int(time.time()), "items": items,
                            "phases": clean_phases(body.get("phases"))}
        try:
            save_templates(everything)
        except OSError as e:
            return self._send(500, f"could not save it: {e}", "text/plain")
        self._send(200, json.dumps({"name": name, "count": len(items)}),
                   "application/json")

    def _template_delete(self):
        body = self._read_json_body()
        if body is None:
            return self._send(400, "bad request", "text/plain")
        name = clean_template_name(body.get("name"))
        everything = load_templates()
        if name not in everything:
            return self._send(404, "no template by that name", "text/plain")
        del everything[name]
        try:
            save_templates(everything)
        except OSError as e:
            return self._send(500, f"could not save it: {e}", "text/plain")
        self._send(200, "gone", "text/plain")

    def _mcp_health(self):
        """Which of them are actually up, cached behind a thread.

        Separate from /mcps on purpose: the list is a file read and answers
        instantly, the health is nine seconds of starting subprocesses. Tying
        them together would make the rail wait on the slow half to draw the
        fast one."""
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        cwd = (q.get("cwd") or [""])[0] or target_cwd()
        cwd = os.path.realpath(os.path.expanduser(str(cwd)))
        if not os.path.isdir(cwd):
            return self._send(400, "checkout does not exist", "text/plain")
        # `claude mcp list` prints a plugin's server as `plugin:<plugin>:<name>`
        # where mcp_rows calls it `<plugin>:<name>` -- the same server, one
        # prefix apart. Ask under both spellings and answer under ours, or
        # every plugin MCP draws as "never checked" forever.
        names = [row["name"] for row in mcp_rows(cwd)]
        theirs = {n: n for n in names}
        theirs.update({f"plugin:{n}": n for n in names})
        slot = mcp_health_for(cwd, list(theirs), time.time())
        rows = {theirs.get(k, k): v for k, v in (slot["rows"] or {}).items()}
        self._send(200, json.dumps({"kind": "mcp-health", "rows": rows,
                                    "checking": slot["busy"], "err": slot["err"]}),
                   "application/json")

    # `disable` is only honest for the scopes Claude Code actually has a switch
    # for. A project (.mcp.json) server is listed in settings.json's
    # disabledMcpjsonServers, and a plugin's MCP goes off with its plugin. A
    # user or local server has no such key -- the only way to stop it is to
    # remove it, and offering a Disable that quietly did a Remove would be the
    # worst of the three.
    MCP_VERBS = ("login", "logout", "remove", "add", "disable", "enable")

    def _mcp_do(self, verb):
        body = self._read_json_body()
        if body is None or verb not in self.MCP_VERBS:
            return self._send(400, "bad request", "text/plain")
        cwd = os.path.realpath(os.path.expanduser(
            str(body.get("cwd") or target_cwd())))
        if not os.path.isdir(cwd):
            return self._send(400, "checkout does not exist", "text/plain")
        name = str(body.get("name") or "").strip()
        if not name or len(name) > 120 or "\n" in name:
            return self._send(400, "that is not an MCP name", "text/plain")
        if verb in ("disable", "enable"):
            return self._mcp_switch(cwd, name, verb == "disable")
        if verb == "add":
            return self._mcp_add(cwd, body, name)
        # login opens a browser and waits for the redirect, so it cannot be
        # awaited inside a request -- it is started and its result is read off
        # the next health check, which is the same thing the user is watching.
        argv = ["claude", "mcp", verb, name]
        if verb == "remove":
            scope = str(body.get("scope") or "local")
            if scope not in ("local", "user", "project"):
                return self._send(400, "unknown scope", "text/plain")
            argv += ["-s", scope]
        if verb == "login":
            subprocess.Popen(argv, cwd=cwd, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            _mcp_health.pop(cwd, None)      # whatever it says now is stale
            return self._send(200, "a browser is opening to authorise it",
                              "text/plain")
        out = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                             timeout=60)
        if out.returncode:
            return self._send(502, (out.stderr.strip() or out.stdout.strip()
                                    or f"{verb} failed")[:400], "text/plain")
        _mcp_health.pop(cwd, None)
        self._send(200, (out.stdout.strip() or "done")[:400], "text/plain")

    def _mcp_switch(self, cwd, name, off):
        """Turn a project-scoped server off or on via settings.json.

        Absent means on, so turning one back on deletes it from the list
        rather than writing the word `false` -- settings.json stays a record
        of the decisions actually made, the same rule the skill switch keeps."""
        rows = repo_worktrees(cwd)
        root = rows[0]["path"] if rows else cwd
        here = next((r for r in mcp_rows(cwd) if r["name"] == name), None)
        if not here:
            return self._send(404, "no MCP by that name here", "text/plain")
        if here["scope"] not in ("project",):
            return self._send(
                400, f"a {here['scope']}-scope server has no disable switch in "
                     "Claude Code -- removing it is the only way to stop it, "
                     "and this will not do that behind your back", "text/plain")

        def change(data):
            off_list = data.get("disabledMcpjsonServers")
            off_list = [n for n in off_list if isinstance(n, str)] \
                if isinstance(off_list, list) else []
            if off and name not in off_list:
                off_list.append(name)
            if not off:
                off_list = [n for n in off_list if n != name]
            if off_list:
                data["disabledMcpjsonServers"] = sorted(set(off_list))
            else:
                data.pop("disabledMcpjsonServers", None)
            return None

        path = os.path.join(root, ".claude", "settings.local.json")
        bad = edit_settings(path, change)
        if bad:
            return self._send(500, bad, "text/plain")
        _mcp_health.pop(cwd, None)
        self._send(200, json.dumps({"name": name, "disabled": off}),
                   "application/json")

    def _mcp_add(self, cwd, body, name):
        """Install one, by handing `claude mcp add` an argv it built itself.

        Everything here is a separate argv element and no shell is involved,
        so nothing on this wire is concatenated into a command line. That is
        the difference between passing a server's command through and letting
        a caller compose one."""
        scope = str(body.get("scope") or "local")
        transport = str(body.get("transport") or "stdio")
        target = str(body.get("target") or "").strip()
        if scope not in ("local", "user", "project"):
            return self._send(400, "unknown scope", "text/plain")
        if transport not in ("stdio", "sse", "http"):
            return self._send(400, "unknown transport", "text/plain")
        if not target:
            return self._send(400, "an MCP needs a command or a URL", "text/plain")
        argv = ["claude", "mcp", "add", "-s", scope, "-t", transport]
        for pair in (body.get("env") or [])[:20]:
            if isinstance(pair, str) and "=" in pair and "\n" not in pair:
                argv += ["-e", pair[:400]]
        for pair in (body.get("headers") or [])[:20]:
            if isinstance(pair, str) and ":" in pair and "\n" not in pair:
                argv += ["--header", pair[:400]]
        argv += [name, target]
        args = [str(a) for a in (body.get("args") or [])[:40]
                if isinstance(a, (str, int)) and "\n" not in str(a)]
        if args:
            argv += args
        out = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                             timeout=60)
        if out.returncode:
            return self._send(502, (out.stderr.strip() or out.stdout.strip()
                                    or "add failed")[:400], "text/plain")
        _mcp_health.pop(cwd, None)
        self._send(200, (out.stdout.strip() or "added")[:400], "text/plain")

    def _guardrails_read(self):
        """One checkout's guardrail checklist state.

        Only the state: which are ticked, which were added, which of the
        starters were struck out. The starter list itself ships with the app,
        so a record for a checkout nobody has touched is simply empty."""
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        root, err = self._repo_request({"cwd": (q.get("cwd") or [""])[0]})
        if err:
            return self._send(400, err, "text/plain")
        got = clean_guardrails(load_guardrails().get(root))
        self._send(200, json.dumps({"kind": "guardrails", "root": root, **got}),
                   "application/json")

    def _guardrails_write(self):
        """Replace one checkout's record. Whole-record rather than per-tick:
        the thing is a few kilobytes, and a partial update protocol is a way
        to end up with a checklist that disagrees with itself."""
        body = self._read_json_body()
        if body is None:
            return self._send(400, "bad request", "text/plain")
        root, err = self._repo_request(body)
        if err:
            return self._send(400, err, "text/plain")
        everything = load_guardrails()
        everything[root] = clean_guardrails(body)
        try:
            save_guardrails(everything)
        except OSError as e:
            return self._send(500, f"could not save it: {e}", "text/plain")
        self._send(200, json.dumps(everything[root]), "application/json")

    def _guardrails_enforce_read(self):
        """Enforcement status for one checkout: the manifest, each rail's
        last result and approval state, and whether the git/Stop hooks are
        installed. A different record from _guardrails_read's checklist --
        that one is ticks, this one is whether a tick actually runs."""
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        root, err = self._repo_request({"cwd": (q.get("cwd") or [""])[0]})
        if err:
            return self._send(400, err, "text/plain")
        self._send(200, json.dumps(guardrails.status(root)), "application/json")

    def _guardrails_compile(self):
        """AI compile: turns one checklist item into a script or an agent
        review brief. Can take up to a few minutes, so the caller should
        show a spinner rather than treat a slow reply as a hang."""
        body = self._read_json_body()
        if body is None:
            return self._send(400, "bad request", "text/plain")
        root, err = self._repo_request(body)
        if err:
            return self._send(400, err, "text/plain")
        item = body.get("item")
        if not isinstance(item, dict) or not item.get("id"):
            return self._send(400, "item needs an id", "text/plain")
        try:
            out = guardrails.compile_rail(root, item)
        except Exception as e:
            return self._send(500, str(e)[:500], "text/plain")
        self._send(200, json.dumps(out), "application/json")

    def _guardrails_rail(self):
        """The enforcer's own text, for the app's read-only view toggle."""
        body = self._read_json_body()
        if body is None:
            return self._send(400, "bad request", "text/plain")
        root, err = self._repo_request(body)
        if err:
            return self._send(400, err, "text/plain")
        rid = str(body.get("id") or "")
        path, content = guardrails.rail_file(root, rid)
        if path is None:
            return self._send(404, "no enforcer for that guardrail", "text/plain")
        kind = "agent" if path.endswith(".review.md") else "script"
        self._send(200, json.dumps({"file": os.path.basename(path), "content": content,
                                    "sha": guardrails.digest(content),
                                    "approved": guardrails.is_approved(root, rid), "kind": kind}),
                  "application/json")

    def _guardrails_approve(self):
        body = self._read_json_body()
        if body is None:
            return self._send(400, "bad request", "text/plain")
        root, err = self._repo_request(body)
        if err:
            return self._send(400, err, "text/plain")
        try:
            # the app always sends the digest of what it showed; approval
            # without one (an old client) would approve unseen text
            if not body.get("sha"):
                return self._send(400, "view the script before approving it", "text/plain")
            guardrails.approve(root, str(body.get("id") or ""), expect=str(body["sha"]))
        except ValueError as e:
            return self._send(400, str(e), "text/plain")
        self._send(200, json.dumps(guardrails.status(root)), "application/json")

    def _guardrails_run(self):
        body = self._read_json_body()
        if body is None:
            return self._send(400, "bad request", "text/plain")
        root, err = self._repo_request(body)
        if err:
            return self._send(400, err, "text/plain")
        raw_ids = body.get("ids")
        ids = [str(i) for i in raw_ids] if isinstance(raw_ids, list) else None
        phase = body.get("phase") or None
        try:
            results = guardrails.run(root, ids=ids, phase=phase)
        except Exception as e:
            return self._send(500, str(e)[:500], "text/plain")
        self._send(200, json.dumps({"results": results}), "application/json")

    def _guardrails_blocking(self):
        body = self._read_json_body()
        if body is None:
            return self._send(400, "bad request", "text/plain")
        root, err = self._repo_request(body)
        if err:
            return self._send(400, err, "text/plain")
        try:
            guardrails.set_blocking(root, str(body.get("id") or ""), bool(body.get("blocking")))
        except ValueError as e:
            return self._send(400, str(e), "text/plain")
        self._send(200, json.dumps(guardrails.status(root)), "application/json")

    def _guardrails_trigger(self):
        body = self._read_json_body()
        if body is None:
            return self._send(400, "bad request", "text/plain")
        root, err = self._repo_request(body)
        if err:
            return self._send(400, err, "text/plain")
        try:
            guardrails.set_trigger(root, str(body.get("phase") or ""), str(body.get("trigger") or ""))
        except ValueError as e:
            return self._send(400, str(e), "text/plain")
        self._send(200, json.dumps(guardrails.status(root)), "application/json")

    def _guardrails_hooks(self):
        body = self._read_json_body()
        if body is None:
            return self._send(400, "bad request", "text/plain")
        root, err = self._repo_request(body)
        if err:
            return self._send(400, err, "text/plain")
        if body.get("install"):
            guardrails.install_hooks(root)
        else:
            guardrails.uninstall_hooks(root)
        self._send(200, json.dumps(guardrails.status(root)), "application/json")

    def _governs_read(self):
        """The files that shape a pull request without being one -- whether
        they are there or not.

        A missing one is the interesting row: no CODEOWNERS is why nobody was
        asked to review, and a list that only showed what exists could never
        say so."""
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        root, err = self._repo_request({"cwd": (q.get("cwd") or [""])[0]})
        if err:
            return self._send(400, err, "text/plain")
        rows = []
        for key, spot in push_cc.GOVERNS.items():
            path = push_cc.govern_path(key, root)
            there = os.path.exists(path)
            rows.append({"key": key, "what": spot["what"],
                         "path": os.path.relpath(path, root), "there": there,
                         "lines": (sum(1 for _ in open(path, errors="replace"))
                                   if there else 0)})
        self._send(200, json.dumps({"kind": "governs",
                                    "repo": os.path.basename(root), "rows": rows}),
                   "application/json")

    def _govern_read(self):
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        root, err = self._repo_request({"cwd": (q.get("cwd") or [""])[0]})
        key = (q.get("key") or [""])[0]
        path = push_cc.govern_path(key, root)
        if err or not path:
            return self._send(400, err or "no such file to govern with", "text/plain")
        try:
            with open(path) as f:
                text = f.read()
        except OSError:
            text = ""      # not there yet is not an error; it is the empty one
        self._send(200, json.dumps({"key": key, "path": os.path.relpath(path, root),
                                    "there": os.path.exists(path), "body": text}),
                   "application/json")

    def _govern_write(self):
        body = self._read_json_body()
        if body is None:
            return self._send(400, "bad request", "text/plain")
        root, err = self._repo_request(body)
        path = push_cc.govern_path(body.get("key"), root)
        text = body.get("body")
        if err or not path or not isinstance(text, str):
            return self._send(400, err or "no such file to govern with", "text/plain")
        if not text.strip():
            return self._send(400, "an empty file governs nothing", "text/plain")
        self._write_repo_file(path, text, root, bool(body.get("replace")))

    def _agent_model_post(self):
        """Set the model the next dispatch of a subagent type runs on."""
        body = self._read_json_body() or {}
        model = body.get("model")
        if model not in AGENT_MODELS:
            return self._send(400, f"model is one of: {', '.join(AGENT_MODELS)}", "text/plain")
        path, _ = agent_def(body.get("type"), body.get("cwd") or "")
        if not path:
            return self._send(404, "no editable definition for that subagent type "
                              "(built-in or plugin)", "text/plain")
        err = set_agent_model(path, model)
        if err:
            return self._send(500, err, "text/plain")
        self._send(200, json.dumps(agent_def_row(body.get("type"), body.get("cwd") or "")),
                   "application/json")

    def _queue_post(self):
        """/queue replaces an agent's whole list -- one call covers edit,
        reorder and remove. /queue/add appends one prompt to the end."""
        body = self._read_json_body()
        tid = str((body or {}).get("terminal_id") or "")
        if not tid:
            return self._send(400, "bad request", "text/plain")
        with _queue_lock:
            q = load_queue()
            if self.path == "/queue/add":
                items = q.get(tid, []) + [{"id": os.urandom(6).hex(),
                                           "text": str(body.get("text") or "")}]
            else:
                items = body.get("items")
                if not isinstance(items, list):
                    return self._send(400, "bad request", "text/plain")
            q[tid] = clean_queue(items)
            save_queue(q)
        if not q[tid]:
            # a "send next" for a queue since emptied must not fire whatever
            # is added later -- that would be sending something unseen
            _queue_once.discard(tid)
        self._send(200, json.dumps({"items": q[tid]}), "application/json")

    def _claude_settings(self):
        """Settings > Claude. access.py holds every rule; this only routes.
        The key comes in on /settings/claude/key and never goes back out --
        state() carries whether one is saved and a hint, not the key."""
        body = self._read_json_body()
        if body is None:
            return self._send(400, "bad request", "text/plain")
        try:
            if self.path == "/settings/claude":
                access.update(body)
            elif self.path == "/settings/claude/key":
                access.save_key(str(body.get("key") or ""))
            elif self.path == "/settings/claude/key/delete":
                access.delete_key()
            elif self.path == "/settings/claude/login":
                access.login()
                return self._send(200, json.dumps({"ok": True}), "application/json")
            else:
                return self._send(404, "not found", "text/plain")
        except ValueError as e:
            return self._send(400, str(e), "text/plain")
        except Exception as e:
            return self._send(500, str(e)[:300], "text/plain")
        self._send(200, json.dumps(access.state()), "application/json")

    def _queue_control(self):
        """/queue/play {terminal_id, playing} turns draining on or off;
        /queue/next {terminal_id} lets the head go once, when the agent is
        next free -- the same checks as playing, just for one item."""
        body = self._read_json_body()
        tid = str((body or {}).get("terminal_id") or "")
        if not tid:
            return self._send(400, "bad request", "text/plain")
        if self.path == "/queue/play":
            (_queue_playing.add if body.get("playing") else _queue_playing.discard)(tid)
        else:
            _queue_once.add(tid)
        with _queue_lock:
            items = load_queue().get(tid, [])
        self._send(200, json.dumps(queue_state(tid, items)), "application/json")

    def _read_json_body(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            body = json.loads(raw)
            return body if isinstance(body, dict) else None
        except json.JSONDecodeError:
            return None

    def _memory_get(self):
        """/memory/search, /memory/recent, /memory/entry, /memory/stats --
        routing lives in memory_query so it can be tested without a socket."""
        status, body, ctype = memory_query(self.path)
        self._send(status, body, ctype)

    def _pr_review(self):
        body = self._read_json_body()
        if body is None:
            return self._send(400, "bad request", "text/plain")
        root, err = self._repo_request(body)
        action = body.get("action")
        note = str(body.get("body") or "").strip()
        try:
            number = int(body.get("number"))
        except (TypeError, ValueError):
            number = 0
        flags = {"approve": "--approve", "comment": "--comment",
                 "request-changes": "--request-changes"}
        if err or number < 1 or action not in flags:
            return self._send(400, err or "invalid review", "text/plain")
        if action in ("comment", "request-changes") and not note:
            return self._send(400, "a review comment is required", "text/plain")
        cmd = ["gh", "pr", "review", str(number), flags[action]]
        if note:
            cmd += ["--body", note]
        out = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=30)
        if out.returncode:
            return self._send(502, (out.stderr.strip() or "review failed")[:500], "text/plain")
        self._send(200, "review submitted", "text/plain")

    def _tests_payload(self, root, path):
        files, packages = push_cc.test_files(root), push_cc.test_packages(root)
        tree = push_cc.test_tree(files)
        run = _app_test_runs.get(root)
        states = run.states if run else {}
        ok, bad = run.tally() if run else (0, 0)
        return {"kind": "tests", "repo": os.path.basename(root), "path": path,
                "items": push_cc.test_items(tree, path, states),
                "running": run.now if run and not run.done else "",
                "passed": ok, "failed": bad, "packages": len(packages),
                "slow": any(p["slow"] for p in push_cc.scope_packages(packages, path, True))}

    def _tests_read(self):
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        root, err = self._repo_request({"cwd": (q.get("cwd") or [""])[0]})
        if err:
            return self._send(400, err, "text/plain")
        path = [p for p in (q.get("path") or [""])[0].split("/") if p]
        self._send(200, json.dumps(self._tests_payload(root, path)), "application/json")

    def _tests_run(self):
        body = self._read_json_body()
        if body is None:
            return self._send(400, "bad request", "text/plain")
        root, err = self._repo_request(body)
        if err:
            return self._send(400, err, "text/plain")
        active = _app_test_runs.get(root)
        if active and not active.done:
            return self._send(409, "tests are already running", "text/plain")
        path = [str(p) for p in body.get("path") or [] if p]
        packages = push_cc.scope_packages(push_cc.test_packages(root), path)
        if not packages:
            return self._send(409, "no Vitest or test script is runnable here", "text/plain")
        only = "/".join(path + ([str(body.get("file"))] if body.get("file") else [])) or None
        _app_test_runs[root] = push_cc.TestRun(root, packages if not only else packages[:1], only).start()
        self._send(200, "tests started", "text/plain")

    def _tests_stop(self):
        body = self._read_json_body()
        if body is None:
            return self._send(400, "bad request", "text/plain")
        root, err = self._repo_request(body)
        if err:
            return self._send(400, err, "text/plain")
        run = _app_test_runs.get(root)
        if run and not run.done:
            run.abort()
        self._send(200, "tests stopped", "text/plain")

    def _work_dirty(self):
        """The GIT tab's count: files changed and not yet committed, staged
        or not, untracked included. Just the status call -- /work also
        builds the commit graph, too much to poll for one number. A file
        staged and then edited again is one file, not two."""
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        root, err = self._repo_request({"cwd": (q.get("cwd") or [""])[0]})
        if err == "not a git checkout":
            # a real folder with no repo: not an error, a fact the tab shows
            return self._send(200, json.dumps({"count": 0, "git": False}), "application/json")
        if err:
            return self._send(400, err, "text/plain")
        _, staged, unstaged, conflicts = work_status(root)
        paths = {r["path"] for r in staged + unstaged + conflicts}
        self._send(200, json.dumps({"count": len(paths), "git": True}), "application/json")

    def _work_read(self):
        """Everything the GIT tab's overview screen needs in one call --
        branch/upstream, the three working-tree lists, stashes, and the
        commit graph -- so the app draws the whole screen from one request
        instead of five round trips."""
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        root, err = self._repo_request({"cwd": (q.get("cwd") or [""])[0]})
        if err:
            return self._send(400, err, "text/plain")
        head_line, staged, unstaged, conflicts = work_status(root)
        for rows, cached in ((staged, True), (unstaged, False)):
            counts = work_numstat(root, cached)
            for r in rows:
                if r["path"] in counts:
                    r["add"], r["del"] = counts[r["path"]]
        row = {"repo": os.path.basename(root), "root": root,
               **work_head(head_line),
               "staged": staged, "unstaged": unstaged, "conflicts": conflicts,
               "stashes": work_stashes(root), "log": work_log(root)}
        self._send(200, json.dumps(row), "application/json")

    def _work_diff(self):
        """The raw patch for one commit or one file, sent back unparsed --
        the app already owns a diff parser and wants git's own text, not a
        JSON re-encoding of it."""
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        root, err = self._repo_request({"cwd": (q.get("cwd") or [""])[0]})
        if err:
            return self._send(400, err, "text/plain")
        sha = (q.get("sha") or [""])[0]
        if sha:
            if not re.fullmatch(r"[0-9a-fA-F]{4,40}", sha):
                return self._send(400, "bad sha", "text/plain")
            out = git(root, "show", sha)
            if out.returncode:
                return self._send(500, (out.stderr.strip() or "git failed")[:500], "text/plain")
            return self._send(200, out.stdout[:250000], "text/plain")
        file = (q.get("file") or [""])[0]
        if not file or file.startswith("/") or ".." in file.split("/"):
            return self._send(400, "bad path", "text/plain")
        staged = (q.get("staged") or ["0"])[0] == "1"
        out = git(root, "diff", "--cached", "--", file) if staged else git(root, "diff", "--", file)
        if out.returncode:
            return self._send(500, (out.stderr.strip() or "git failed")[:500], "text/plain")
        text = out.stdout
        if not text and not staged:
            # an untracked file has no diff against the index at all -- diffing
            # it against /dev/null is how git itself renders a "new file"
            # patch, and --no-index exits 1 by design even though stdout is
            # exactly the patch we want
            text = git(root, "diff", "--no-index", "--", "/dev/null", file).stdout
        self._send(200, text[:250000], "text/plain")

    def _work_init(self):
        """`git init` for a project that started as a plain folder, with an
        optional first commit of what is already there. Only for a folder
        the project list already holds -- this server can be bound to the
        LAN, and it is not a way to plant a repo anywhere on the disk."""
        body = self._read_json_body()
        if body is None:
            return self._send(400, "bad request", "text/plain")
        cwd = os.path.realpath(os.path.expanduser(str(body.get("cwd") or "")))
        if not os.path.isdir(cwd):
            return self._send(400, "that folder does not exist", "text/plain")
        known = {os.path.realpath(p) for p in load_projects()}
        if cwd not in known:
            return self._send(403, "only a folder in the project list can be initialised",
                              "text/plain")
        if git(cwd, "rev-parse", "--show-toplevel").returncode == 0:
            return self._send(409, "this folder is already inside a git repository", "text/plain")
        branch = str(body.get("branch") or "main").strip()
        if git(cwd, "check-ref-format", "--branch", branch).returncode:
            return self._send(400, f"{branch!r} is not a valid branch name", "text/plain")
        out = git(cwd, "init", "-b", branch)
        if out.returncode:
            return self._send(500, (out.stderr or "git init failed").strip()[-300:], "text/plain")
        said = f"initialised on {branch}"
        if body.get("commit"):
            git(cwd, "add", "-A")
            done = git(cwd, "commit", "-m", "Initial commit")
            if done.returncode:
                # the repo exists either way; say why the commit did not happen
                said += " -- first commit failed: " + (done.stderr or done.stdout).strip()[-200:]
            else:
                said += ", first commit made"
        self._send(200, json.dumps({"ok": True, "said": said}), "application/json")

    def _work_ai_message(self):
        body = self._read_json_body()
        if body is None:
            return self._send(400, "bad request", "text/plain")
        root, err = self._repo_request(body)
        if err:
            return self._send(400, err, "text/plain")
        try:
            message, scope = commit_message(root)
        except ValueError as e:
            return self._send(409, str(e), "text/plain")
        except Exception as e:
            return self._send(502, f"could not draft a message: {e}", "text/plain")
        self._send(200, json.dumps({"message": message, "scope": scope}), "application/json")

    def _work_do(self):
        """The GIT tab's action bar: stage, unstage, discard, commit, fetch,
        pull, push, stash, stash-pop, stash-drop, and branch, all through one
        endpoint rather than one route per verb. On failure git's own refusal
        -- nothing to commit, no upstream, a dirty checkout -- is passed back
        verbatim, the same way _worktrees_switch already does for switch."""
        body = self._read_json_body()
        if body is None:
            return self._send(400, "bad request", "text/plain")
        root, err = self._repo_request(body)
        if err:
            return self._send(400, err, "text/plain")
        verb = body.get("verb")
        if verb not in ("stage", "unstage", "discard", "commit", "fetch", "pull",
                        "push", "stash", "stash-pop", "stash-drop", "branch"):
            return self._send(400, "unknown verb", "text/plain")
        raw_files = body.get("files")
        files = [str(f) for f in raw_files if str(f)] if isinstance(raw_files, list) else []
        if any(f.startswith("/") or ".." in f.split("/") for f in files):
            return self._send(400, "bad path", "text/plain")
        message = str(body.get("message") or "")
        amend = bool(body.get("amend"))
        timeout = 30
        if verb == "stage":
            argv = ["add", "-A", "--"] + files if files else ["add", "-A"]
        elif verb == "unstage":
            argv = ["reset", "-q", "HEAD", "--"] + files if files else ["reset", "-q", "HEAD"]
        elif verb == "discard":
            # an untracked path makes checkout fail (nothing tracked to
            # revert) and a tracked one makes clean a no-op (nothing untracked
            # to remove) -- so only report an error when BOTH refuse
            checkout = git(root, "checkout", "-q", "--", *(files or ["."]))
            clean = git(root, "clean", "-qfd", "--", *files) if files else git(root, "clean", "-qfd")
            if checkout.returncode and clean.returncode:
                failed = checkout if checkout.stderr.strip() else clean
                return self._send(409, (failed.stderr or failed.stdout).strip()[:500], "text/plain")
            return self._send(200, "ok", "text/plain")
        elif verb == "commit":
            if not message.strip():
                if not amend:
                    return self._send(400, "a commit message is required", "text/plain")
                argv = ["commit", "--amend", "--no-edit"]
            else:
                argv = ["commit", "-m", message] + (["--amend"] if amend else [])
        elif verb == "fetch":
            argv, timeout = ["fetch", "--prune"], 120
        elif verb == "pull":
            argv, timeout = ["pull"], 120
        elif verb == "push":
            timeout = 120
            head = work_head(work_status(root)[0])
            argv = (["push", "-u", "origin", head["branch"]]
                    if not head["upstream"] and head["branch"] else ["push"])
        elif verb == "stash":
            argv = ["stash", "push", "-u"] + (["-m", message] if message.strip() else [])
        elif verb in ("stash-pop", "stash-drop"):
            ref = str(body.get("ref") or "")
            if not re.fullmatch(r"stash@\{\d+\}", ref):
                return self._send(400, "bad ref", "text/plain")
            argv = ["stash", "pop" if verb == "stash-pop" else "drop", ref]
        else:  # branch
            branch = str(body.get("branch") or "")
            if not re.fullmatch(r"[A-Za-z0-9._/-]{1,100}", branch) or branch.startswith("-"):
                return self._send(400, "bad branch name", "text/plain")
            argv = ["checkout", "-b", branch]
        out = git(root, *argv, timeout=timeout)
        if out.returncode:
            return self._send(409, (out.stderr or out.stdout).strip()[:500], "text/plain")
        self._send(200, (out.stdout or "ok").strip()[:500], "text/plain")

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
        # empty by default: an agent gets a window of its own unless the
        # caller explicitly asks for a split
        split = body.get("split") or ""
        name = body.get("name")
        if name:
            out = push_cc.herdr("agent", "start", name, "--cwd", cwd,
                                *(("--split", split) if split else ()),
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
        # activate first: owning the dialog is not the same as fronting it,
        # and without this it opened behind whatever you were looking at --
        # the button then seemed to do nothing while a dialog sat waiting
        prompt = (q.get("prompt") or ["midiAI: folder for this session"])[0][:120]
        script = ('tell application "System Events"\n'
                  '  activate\n'
                  f'  return POSIX path of (choose folder with prompt {json.dumps(prompt)} '
                  f'default location POSIX file {json.dumps(start)})\n'
                  'end tell')
        try:
            out = subprocess.run(["osascript", "-e", script],
                                 capture_output=True, text=True, timeout=180)
        except subprocess.TimeoutExpired:
            return self._send(504, "nobody picked a folder", "text/plain")
        # cancel is error -128, not a failure -- the app just closes the
        # spinner and leaves the field alone. Anything else used to be
        # reported as a cancel too, which is how a broken dialog looked
        # like a button that did nothing.
        if out.returncode != 0:
            if "-128" in out.stderr:
                return self._send(200, json.dumps({"path": None}),
                                  "application/json")
            return self._send(502, "the folder dialog failed: "
                              + (out.stderr.strip() or f"exit {out.returncode}")[-300:],
                              "text/plain")
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
                    # A folder you added that has no repo yet is still a
                    # project: listed as its one plain checkout, flagged so the
                    # GIT tab can offer to start one. Only remembered ones --
                    # an agent living in some stray folder does not make it
                    # a project.
                    if os.path.isdir(cwd) and cwd not in seen:
                        seen.add(cwd)
                        out.append({"path": cwd, "git": False,
                                    "name": os.path.basename(cwd.rstrip("/")) or cwd,
                                    "worktrees": [plain_checkout(cwd)]})
                continue
            main = rows[0]["path"]
            keep.add(main)          # the file converges on main checkouts
            if main in seen:
                continue
            seen.add(main)
            out.append({"path": main, "git": True,
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
        # no repo is fine: a project can start as a plain folder, and the GIT
        # tab offers `git init` for it
        main = rows[0]["path"] if rows else path
        save_projects(set(load_projects()) | {main})
        self._send(200, json.dumps({"path": main, "git": bool(rows)}), "application/json")

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
            "label": load_skill_labels().get(
                skill_label_key(scope, name, self._skill_root(cwd), path), ""),
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
        set_skill_label(scope, name, body.get("label", ""), self._skill_root(cwd))
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
        set_skill_label(scope, name, "", self._skill_root(cwd))
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
        if scope != "plugin":
            old_root = self._skill_root(body.get("cwd") or target_cwd())
            labels = load_skill_labels()
            old_key = skill_label_key(scope, name, old_root)
            label = labels.pop(old_key, "")
            if label:
                labels[skill_label_key(to, name, root)] = label
            save_skill_labels(labels)
        self._send(200, json.dumps({"path": dest_md,
                                    "copied": scope == "plugin"}),
                   "application/json")

    def _set_in_map(self, body, sub, key, value, default_scope="local"):
        """Write `sub[key] = value` into one settings file, or delete it when
        `value` is None.

        `local` by default, which is where /skills puts skillOverrides and
        where a decision about this checkout belongs -- it is the file git does
        not carry, so turning something off for yourself does not turn it off
        for everyone who clones the repo."""
        root = self._skill_root(body.get("cwd") or target_cwd())
        scope = body.get("scope") or default_scope
        path = dict(settings_chain(root)).get(scope)
        if not path:
            return None, "that is not a settings scope"

        def change(data):
            got = data.get(sub)
            if got is not None and not isinstance(got, dict):
                return f"{sub} in that settings file is not an object"
            if value is None:
                if isinstance(got, dict):
                    got.pop(key, None)
                    if not got:
                        del data[sub]
                return None
            data.setdefault(sub, {})[key] = value
            return None

        return path, edit_settings(path, change)

    def _skill_state(self):
        """How visible a skill is: on, name-only, user-invocable-only, or off.

        Four states rather than a switch, because Claude Code has four and the
        middle two are the useful ones -- a skill you still want to reach by
        name but never want reaching for you. `on` deletes the key rather than
        writing the word, so the file stays a list of decisions actually made
        and a skill that is simply on looks like one."""
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            body = json.loads(raw)
            name, state = body["name"], body["state"]
        except (json.JSONDecodeError, KeyError, TypeError):
            return self._send(400, "bad request", "text/plain")
        if state not in SKILL_STATES:
            return self._send(400, f"a skill is one of: {', '.join(SKILL_STATES)}",
                              "text/plain")
        if not SKILL_NAME_RE.match(name or ""):
            return self._send(400, "bad name for a skill", "text/plain")
        path, bad = self._set_in_map(body, "skillOverrides", name,
                                     None if state == "on" else state)
        if bad:
            return self._send(500, bad, "text/plain")
        self._send(200, json.dumps({"path": path, "state": state}),
                   "application/json")

    def _plugin_toggle(self):
        """A plugin on or off, which is `enabledPlugins` -- a documented map,
        and the only switch a plugin's own skills have, since skillOverrides
        does not reach them."""
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            body = json.loads(raw)
            key, on = body["key"], bool(body["enabled"])
        except (json.JSONDecodeError, KeyError, TypeError):
            return self._send(400, "bad request", "text/plain")
        if not isinstance(key, str) or "@" not in key or "/" in key:
            return self._send(400, "a plugin is name@marketplace", "text/plain")
        # written either way, never deleted: an absent entry means "whatever
        # the installer decided", and the point of pressing this is to decide
        path, bad = self._set_in_map(body, "enabledPlugins", key, on)
        if bad:
            return self._send(500, bad, "text/plain")
        self._send(200, json.dumps({"path": path, "enabled": on}),
                   "application/json")

    def _hook_toggle(self):
        """A hook off, or back on again.

        Off lifts the whole entry out of the settings file and parks it in
        ~/.midiai/hooks-parked.json; on puts it back into the group whose
        matcher it had. There is no per-hook switch in the schema -- the only
        documented one is disableAllHooks, which takes the status line and the
        @ file suggestions with it -- so this is ours, and the trade is the
        same one the descriptions make: settings.json holds only what Claude
        Code documents, and what it cannot hold lives beside it.

        The cost, said plainly because it is real: a parked hook is invisible
        to anything reading settings.json. Only this app knows it exists."""
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            body = json.loads(raw)
            on = bool(body["on"])
        except (json.JSONDecodeError, KeyError, TypeError):
            return self._send(400, "bad request", "text/plain")
        path, err = self._hook_target(body)
        if err:
            return self._send(400, err, "text/plain")
        root = self._skill_root(body.get("cwd") or target_cwd())
        scope = body.get("scope") or ""
        parked = load_parked()

        if on:
            key = body.get("parked") or ""
            rec = parked.get(key)
            if not rec:
                return self._send(404, "that hook is not parked -- reopen the panel",
                                  "text/plain")
            event, matcher, entry = rec["event"], rec.get("matcher", ""), rec["entry"]

            def put_back(data):
                hooks = data.setdefault("hooks", {})
                if not isinstance(hooks, dict):
                    return "the hooks key in that settings file is not an object"
                groups = hooks.setdefault(event, [])
                if not isinstance(groups, list):
                    return f"hooks.{event} in that settings file is not a list"
                for group in groups:
                    if isinstance(group, dict) and str(group.get("matcher") or "") == matcher:
                        group.setdefault("hooks", []).append(entry)
                        return None
                group = {"hooks": [entry]}
                if matcher:
                    group["matcher"] = matcher
                groups.append(group)
                return None

            bad = edit_settings(path, put_back)
            if bad:
                return self._send(500, bad, "text/plain")
            note = {"description": str(rec.get("description") or ""),
                    "label": str(rec.get("label") or "")}
            if note["description"] or note["label"]:
                notes = load_notes()
                notes[note_key(scope, event, matcher, hook_summary(entry))] = note
                save_notes(notes)
            del parked[key]
            save_parked(parked)
            return self._send(200, json.dumps({"on": True}), "application/json")

        try:
            gi, hi = int(body["gi"]), int(body["hi"])
        except (KeyError, TypeError, ValueError):
            return self._send(400, "bad request", "text/plain")
        row = next((r for r in hook_rows(path, scope)
                    if r["gi"] == gi and r["hi"] == hi), None)
        if not row:
            return self._send(409, "that hook is no longer where it was -- "
                                   "reopen the panel", "text/plain")

        def lift(data):
            hooks = data.get("hooks")
            if not isinstance(hooks, dict) or row["event"] not in hooks:
                return "that hook is no longer where it was -- reopen the panel"
            groups = hooks[row["event"]]
            try:
                del groups[gi]["hooks"][hi]
            except (IndexError, KeyError, TypeError):
                return "that hook is no longer where it was -- reopen the panel"
            if not groups[gi]["hooks"]:
                del groups[gi]
            if not groups:
                del hooks[row["event"]]
            if not hooks:
                del data["hooks"]
            return None

        bad = edit_settings(path, lift)
        if bad:
            return self._send(409, bad, "text/plain")
        parked[parked_key(scope, row["event"], row["matcher"], row["summary"])] = {
            "scope": scope, "root": root, "event": row["event"],
            "matcher": row["matcher"], "entry": row["entry"],
            "description": row.get("description", ""),
            "label": row.get("label", ""),
        }
        save_parked(parked)
        self._send(200, json.dumps({"on": False}), "application/json")

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
        text = " ".join(str(body.get("description") or "").split())[:600]
        label = " ".join(str(body.get("label") or "").split())[:80]
        if text or label:
            notes[key] = {"description": text, "label": label}
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
        # Rooted at each plugin's own installPath, not globbed across the
        # plugin tree. The old globs matched `plugins/cache/<owner>/<plugin>/
        # <version>/skills/*` for EVERY cached version and the marketplace
        # copy besides, so one plugin with six versions on disk listed its
        # skills six times -- `mem-search x6`, and 21 names doubled or worse.
        # Only one version is ever loaded, so only one belongs in a catalogue
        # of what an agent can see. installed_plugins.json names it, the same
        # way mcp_rows already finds a plugin's MCPs.
        #
        # Two depths still, because a plugin's skills sit either directly
        # under it or one level inside a named sub-package -- both are real
        # layouts. Capped at 200 before any file is opened.
        plugin_skills = []
        try:
            installed = read_settings(os.path.expanduser(
                "~/.claude/plugins/installed_plugins.json")).get("plugins", {})
        except AttributeError:
            installed = {}
        for records in (installed or {}).values():
            record = records[0] if isinstance(records, list) and records and \
                isinstance(records[0], dict) else {}
            where = record.get("installPath")
            if not isinstance(where, str):
                continue
            # Any depth under a `skills/` directory, not one or two guessed
            # levels: mattpocock files its as skills/<category>/<skill>/, and
            # a fixed depth silently dropped every one of them. Anchored on a
            # `skills` ancestor so a vendored SKILL.md inside node_modules --
            # playwright ships two -- is not mistaken for a plugin's own.
            plugin_skills += plugin_skill_files(where)
        if not plugin_skills:
            # nothing installed, or a manifest we could not read -- an empty
            # catalogue would be a worse answer than a duplicated one
            plugin_skills = (
                glob.glob(os.path.expanduser("~/.claude/plugins/*/*/*/skills/*/SKILL.md")) +
                glob.glob(os.path.expanduser("~/.claude/plugins/*/*/skills/*/SKILL.md")))
        plugin_skills = sorted(set(plugin_skills))[:200]

        skills = (skill_rows(user_skills, "user") +
                 skill_rows(project_skills, "project") +
                 skill_rows(plugin_skills, "plugin"))
        labels = load_skill_labels()
        for row in skills:
            row["label"] = labels.get(skill_label_key(
                row["scope"], row["name"], root, row.get("path", "")), "")
        # skillOverrides is what /skills writes when you press Space on one.
        # It does not reach plugin skills -- the docs are explicit -- so those
        # say so rather than showing a switch that would do nothing.
        for row in skills:
            state, where = resolve_in_map(root, row["name"], "skillOverrides")
            row["overridable"] = row["scope"] != "plugin"
            row["state"] = state if state in SKILL_STATES else "on"
            row["stateSet"] = state in SKILL_STATES
            row["stateScope"] = where
        skills.sort(key=lambda s: (s["scope"], s["name"].lower()))

        hooks = (hook_rows(os.path.expanduser("~/.claude/settings.json"), "user") +
                hook_rows(os.path.join(root, ".claude/settings.json"), "project") +
                hook_rows(os.path.join(root, ".claude/settings.local.json"), "local"))
        for row in hooks:
            row["on"] = True
        # A parked hook is not in any settings file, so nothing above found it.
        # It is listed anyway, off: a hook you cannot see is a hook you will
        # write a second copy of.
        for key, rec in load_parked().items():
            if rec.get("root") and os.path.realpath(rec["root"]) != os.path.realpath(root):
                continue
            entry = rec.get("entry") or {}
            summary = hook_summary(entry)
            hooks.append({"event": rec.get("event", ""),
                          "matcher": rec.get("matcher", ""),
                          "type": entry.get("type") or "command",
                          "summary": summary[:400], "command": summary[:400],
                          "entry": entry,
                          "name": str(entry.get("statusMessage") or ""),
                          "description": rec.get("description", ""),
                          "label": rec.get("label", ""),
                          # no address: it is not in a file to have one. The
                          # parked key is how the toggle finds it again.
                          "gi": None, "hi": None, "parked": key,
                          "on": False,
                          "scope": rec.get("scope", "user"), "path": ""})
        hooks.sort(key=lambda h: (h["scope"], h["event"].lower()))

        plugins = []
        for key in installed_plugins():
            on, where = resolve_in_map(root, key, "enabledPlugins")
            name, _, market = key.partition("@")
            plugins.append({"key": key, "name": name, "marketplace": market,
                            "enabled": on is not False, "set": on is not None,
                            "scope": where})

        self._send(200, json.dumps({"skills": skills, "hooks": hooks,
                                    "plugins": plugins}),
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

    def _history(self):
        """The focused agent's whole conversation, from its transcript -- the
        pane only ever holds a screenful. `since` is how many turns the app
        already has, so a poll of a long session sends only what is new."""
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        tid = (q.get("tid") or [""])[0]
        try:
            since = max(0, int((q.get("since") or ["0"])[0]))
        except ValueError:
            since = 0
        agent = next((a for a in push_cc.agents() if a.get("terminal_id") == tid), None)
        if not agent:
            return self._send(404, "no such agent", "text/plain")
        path = push_cc.transcript(agent)
        # A subagent's log sits beside its parent's, in the same JSONL shape,
        # so one reader serves both. The id comes from the client, so it is
        # checked before it is ever part of a path.
        sub = (q.get("sub") or [""])[0]
        if sub:
            if not SUB_ID_RE.match(sub) or not path:
                return self._send(400, "bad request", "text/plain")
            path = os.path.join(path[:-len(".jsonl")], "subagents", f"agent-{sub}.jsonl")
            if not os.path.isfile(path):
                return self._send(404, "no such subagent", "text/plain")
        turns = push_cc.history(path, sidechain=bool(sub))
        # /clear starts a new log under the same pane: the app starts over
        # when this changes rather than appending one session to another
        self._send(200, json.dumps({"session": os.path.basename(path or ""),
                                    "total": len(turns), "turns": turns[since:]}),
                   "application/json")

    def _pty_where(self):
        """The pane the app's terminal is currently looking at.

        Cheap enough to poll: one `display-message`, no subprocess tree, and
        only while the terminal tab is open. It is how "the app follows the
        terminal" works -- see ptybridge.where for why it is a read and never
        a write."""
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        pane = (q.get("pane") or [""])[0]
        if not PANE_ID_RE.match(pane):
            return self._send(400, "that is not a tmux pane", "text/plain")
        self._send(200, json.dumps({"pane": ptybridge.where(pane)}),
                   "application/json")

    def _pty_stream(self):
        """The pane's bytes, as they happen.

        Server-sent events rather than a WebSocket: this server is a
        ThreadingHTTPServer, so a long-lived response costs one thread and
        nothing else, and SSE needs no handshake and no frame parser to get
        wrong. The upstream half is a plain POST -- keystrokes are small and
        already coalesced by the app before they are sent.

        base64 because a terminal emits arbitrary bytes and SSE is a
        line-oriented text protocol: a raw \\n inside the payload would end
        the event early and cut a frame in half."""
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        pane = (q.get("pane") or [""])[0]
        if not PANE_ID_RE.match(pane):
            return self._send(400, "that is not a tmux pane", "text/plain")
        try:
            cols = int((q.get("cols") or ["120"])[0])
            rows = int((q.get("rows") or ["32"])[0])
        except ValueError:
            cols, rows = 120, 32
        slot, err = ptybridge.open_view(pane, cols, rows)
        if err:
            return self._send(502, err, "text/plain")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        slot["readers"] += 1
        # a nudged resize, which is the one thing tmux always redraws for --
        # see Pty.repaint for why replaying our own bytes shredded the screen
        slot["pty"].repaint(cols, rows)
        try:
            while True:
                chunk = slot["pty"].read(0.4)
                if chunk:
                    slot["pty"].touched = time.time()
                    self.wfile.write(b"data: " + base64.b64encode(chunk) + b"\n\n")
                    self.wfile.flush()
                elif not slot["pty"].alive:
                    self.wfile.write(b"event: gone\ndata: -\n\n")
                    self.wfile.flush()
                    break
                else:
                    # a comment keeps the connection warm through a quiet
                    # agent without putting anything on the terminal
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass                       # the tab closed; the view outlives it
        finally:
            slot["readers"] -= 1
            ptybridge.reap()

    def _pty_write(self, what):
        """Keystrokes, a new size, or the end of a view.

        Bytes, never key names -- the same wire /keys uses, for the same
        reason: there is no vocabulary to keep in step with anything."""
        body = self._read_json_body()
        if body is None:
            return self._send(400, "bad request", "text/plain")
        pane = str(body.get("pane") or "")
        if not PANE_ID_RE.match(pane):
            return self._send(400, "that is not a tmux pane", "text/plain")
        if what == "close":
            return self._send(200, "closed" if ptybridge.close_view(pane)
                              else "nothing open", "text/plain")
        slot = ptybridge.slot_for(pane)
        if not slot:
            return self._send(409, "no live view for that pane", "text/plain")
        slot["pty"].touched = time.time()
        if what == "resize":
            ok = slot["pty"].resize(body.get("cols"), body.get("rows"))
            return self._send(200 if ok else 500,
                              "resized" if ok else "could not resize", "text/plain")
        data = body.get("data")
        if not isinstance(data, str) or len(data) > 65536:
            return self._send(400, "data must be a short base64 string", "text/plain")
        try:
            raw = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError):
            return self._send(400, "data must be base64", "text/plain")
        ok = slot["pty"].write(raw)
        self._send(200 if ok else 502, "ok" if ok else "the pty is gone", "text/plain")

    def _terminal_open(self):
        """Hand one pane to a real terminal on this machine.

        The in-app terminal is a picture of the pane with a keyboard on it:
        `capture-pane` strips the colour, polling means there is no cursor,
        and there is no mouse and no scrollback. None of that is fixable from
        the app side, and none of it needs fixing on a desktop -- tmux is
        already running, so the terminal that has all of it is one attach
        away. This is that attach, as a key.

        Written as a file and opened rather than run through AppleScript: a
        script on disk has no string to interpolate into, and the one value
        from the request is a pane id that has already had to match
        `%<digits>` to get here."""
        body = self._read_json_body()
        if body is None:
            return self._send(400, "bad request", "text/plain")
        pane = str(body.get("terminal_id") or read_target().get("terminal_id") or "")
        if not PANE_ID_RE.match(pane):
            return self._send(400, "that is not a tmux pane", "text/plain")
        if sys.platform != "darwin":
            return self._send(
                501, "opening a terminal is wired for macOS only -- "
                     f"`tmux attach` and select pane {pane} by hand", "text/plain")
        script = os.path.join(tempfile.gettempdir(), f"midiai-attach-{pane[1:]}.command")
        try:
            with open(script, "w") as f:
                f.write("#!/bin/sh\n"
                        "# opened by midiAI -- attaches to the pane you were reading\n"
                        f"S=$(tmux display-message -p -t '{pane}' '#{{session_name}}') || exit 1\n"
                        f"tmux select-pane -t '{pane}'\n"
                        'exec tmux attach -t "$S"\n')
            os.chmod(script, 0o700)
            subprocess.Popen(["open"] + (["-a", TERMINAL_APP] if TERMINAL_APP else [])
                             + [script])
        except OSError as e:
            return self._send(500, f"could not open a terminal: {e}", "text/plain")
        self._send(200, "opened a terminal on the machine running midiAI",
                   "text/plain")

    def _keys(self):
        """Bytes straight through to a pane. Nothing here is interpreted.

        The terminal view is a real terminal, so what it sends is what a
        keyboard sends: characters, and the control sequences for the keys
        that are not characters -- \\x03 for Ctrl-C, \\x1b[A for Up. Deliberately
        bytes and not tmux key NAMES: `send-keys -l` takes them literally, so
        there is no name to allowlist, nothing that can start with a dash and
        be read as an option, and no second syntax to keep in step with tmux's.

        /prompt is the other thing and stays the other thing -- it edits the
        agent's input line, replaces it, decides about submitting. This does
        none of that. It is a wire."""
        body = self._read_json_body()
        if body is None:
            return self._send(400, "bad request", "text/plain")
        keys = body.get("keys")
        if not isinstance(keys, str) or not keys or len(keys) > 4096:
            return self._send(400, "keys must be a short non-empty string",
                              "text/plain")
        target = body.get("terminal_id") or read_target().get("terminal_id")
        if not target:
            return self._send(409, "no session selected", "text/plain")
        push_cc.herdr("agent", "send", target, keys)
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
    threading.Thread(target=queue_loop, daemon=True).start()
    threading.Thread(target=memory_loop, daemon=True).start()
    ThreadingHTTPServer((host, PORT), Handler).serve_forever()
