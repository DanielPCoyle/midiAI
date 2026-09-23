#!/usr/bin/env python3
"""The /work routes, driven for real: read them against this checkout, write
to a throwaway one.

The split is the point. The reads are safe anywhere, and the interesting tree
to read is this one -- it has real history with merges in it, which a repo
made three seconds ago does not. The writes are `git add`, `git commit`,
`git stash drop`, `git checkout --`: run those here and they land on whatever
anyone happens to have uncommitted, so every one of them runs in a temp repo
that is created, driven and left behind.

    python3 probe_work.py        # needs no server running -- it starts one

Port 8799, not 8765, so it never fights the mapui the app is talking to."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mapui                                            # noqa: E402

PORT = 8799
B = f"http://127.0.0.1:{PORT}"
srv = ThreadingHTTPServer(("127.0.0.1", PORT), mapui.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()

fails = []


def ok(label, cond, extra=""):
    line = (extra or "").strip().split("\n")[0][:70]
    print(("ok   " if cond else "FAIL ") + label + (f"  {line}" if line else ""))
    if not cond:
        fails.append(label)


def get(path, **kw):
    url = B + path + ("?" + urllib.parse.urlencode(kw) if kw else "")
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def post(path, body):
    req = urllib.request.Request(B + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def work(cwd):
    code, body = get("/work", cwd=cwd)
    assert code == 200, body
    return json.loads(body)


# --- reads, against this checkout: history is the part a fresh repo cannot fake
here = work(HERE)
ok("GET /work", bool(here["branch"] or here["detached"]), here["branch"])
ok("  the graph has commits", any(r["sha"] for r in here["log"]), f"{len(here['log'])} rows")
ok("  and connector rows keep their art",
   all(r["art"] is not None for r in here["log"]))
sha = next(r["sha"] for r in here["log"] if r["sha"])
code, body = get("/work/diff", cwd=HERE, sha=sha)
ok("GET /work/diff?sha", code == 200 and "diff --git" in body, f"{len(body)} bytes")
code, body = get("/work/diff", cwd=HERE, sha="../../etc")
ok("  and only takes a hex sha", code == 400, body)
code, body = get("/work/diff", cwd=HERE, file="../../../etc/passwd")
ok("GET /work/diff refuses a path out of the repo", code == 400, body)
code, body = get("/work", cwd="/definitely/not/a/checkout")
ok("GET /work on a non-checkout", code == 400, body)

# --- writes, in a repo made for the purpose
tmp = tempfile.mkdtemp(prefix="probe_work-")
git = lambda *a: subprocess.run(["git", "-C", tmp, *a], capture_output=True, text=True)
git("init", "-q", "-b", "main")
git("config", "user.email", "probe@example.com")
git("config", "user.name", "probe")
open(os.path.join(tmp, "first.txt"), "w").write("one\n")
git("add", "-A")
git("commit", "-qm", "first")
open(os.path.join(tmp, "a.txt"), "w").write("hello\n")            # untracked
open(os.path.join(tmp, "first.txt"), "w").write("one\ntwo\n")     # modified

w = work(tmp)
ok("a new file reads as untracked",
   any(r["path"] == "a.txt" and r["state"] == "untracked" for r in w["unstaged"]),
   str(w["unstaged"]))
code, body = get("/work/diff", cwd=tmp, file="a.txt")
ok("  and still shows a diff (--no-index)", code == 200 and "diff --git" in body)

code, body = post("/work/do", {"cwd": tmp, "verb": "stage", "files": ["a.txt"]})
ok("POST stage, one file", code == 200, body)
w = work(tmp)
ok("  it moved sides", [r["path"] for r in w["staged"]] == ["a.txt"], str(w["staged"]))
ok("  and the other file did not",
   any(r["path"] == "first.txt" for r in w["unstaged"]))
code, body = post("/work/do", {"cwd": tmp, "verb": "unstage", "files": ["a.txt"]})
ok("POST unstage", code == 200 and not work(tmp)["staged"], body)

post("/work/do", {"cwd": tmp, "verb": "stage"})
code, body = post("/work/do", {"cwd": tmp, "verb": "commit", "message": "add a"})
ok("POST commit", code == 200, body)
ok("  the graph grew", len([r for r in work(tmp)["log"] if r["sha"]]) == 2)
code, body = post("/work/do", {"cwd": tmp, "verb": "commit", "message": "   "})
ok("POST commit refuses an empty message", code == 400, body)
code, body = post("/work/do", {"cwd": tmp, "verb": "commit", "amend": True})
ok("POST amend keeps the message with none given", code == 200, body)

open(os.path.join(tmp, "first.txt"), "w").write("one\ntwo\nthree\n")
code, body = post("/work/do", {"cwd": tmp, "verb": "stash"})
ok("POST stash", code == 200, body)
stashes = work(tmp)["stashes"]
ok("  it is listed", len(stashes) == 1, str(stashes))
code, body = post("/work/do", {"cwd": tmp, "verb": "stash-pop", "ref": stashes[0]["ref"]})
ok("POST stash-pop", code == 200, body)
code, body = post("/work/do", {"cwd": tmp, "verb": "stash-drop", "ref": "stash@{0}; rm -rf /"})
ok("POST stash-drop takes no ref but a real one", code == 400, body)

code, body = post("/work/do", {"cwd": tmp, "verb": "branch", "branch": "feat/probe"})
ok("POST branch", code == 200, body)
ok("  and we are on it", work(tmp)["branch"] == "feat/probe")
code, body = post("/work/do", {"cwd": tmp, "verb": "branch", "branch": "--force"})
ok("POST branch refuses a flag as a name", code == 400, body)

code, body = post("/work/do", {"cwd": tmp, "verb": "discard"})
ok("POST discard", code == 200, body)
w = work(tmp)
ok("  tree is clean, untracked included", not w["staged"] and not w["unstaged"], str(w))

code, body = post("/work/do", {"cwd": tmp, "verb": "push"})
ok("POST push with no remote is git's own refusal, not a crash",
   code in (409, 500) and "origin" in body, body)
code, body = post("/work/do", {"cwd": tmp, "verb": "rm -rf /"})
ok("POST takes no verb but its own", code == 400, body)
code, body = post("/work/do", {"cwd": tmp, "verb": "stage", "files": ["/etc/passwd"]})
ok("POST takes no absolute path", code == 400, body)

srv.shutdown()
print("\n" + ("work: ok" if not fails else f"work: {len(fails)} FAILED -- {fails}"))
sys.exit(1 if fails else 0)
