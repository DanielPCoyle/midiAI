"""The commit box's AI draft, against a throwaway repo. python3 test_commit_message.py"""
import os
import subprocess
import tempfile

import mapui

repo = tempfile.mkdtemp()
g = lambda *a: subprocess.run(["git", "-C", repo, *a], check=True, capture_output=True)
g("init", "-q")
g("config", "user.email", "t@t")
g("config", "user.name", "t")
open(os.path.join(repo, "a.txt"), "w").write("one\n")
g("add", "a.txt")
g("commit", "-qm", "feat: the first thing")

asks = []


def fake(ask):
    asks.append(ask)
    return "```\nfix: change a\n\nBecause.\n```"


try:
    mapui.commit_message(repo, run=fake)
    raise AssertionError("a clean tree has nothing to describe")
except ValueError:
    pass

open(os.path.join(repo, "a.txt"), "w").write("two\n")
open(os.path.join(repo, "new.txt"), "w").write("x\n")
msg, scope = mapui.commit_message(repo, run=fake)
assert (msg, scope) == ("fix: change a\n\nBecause.", "all"), (msg, scope)
assert "feat: the first thing" in asks[-1], "recent subjects go in as the style"
assert "-one" in asks[-1] and "+two" in asks[-1] and "new.txt" in asks[-1], \
    "nothing staged: the whole working tree, untracked names included"

g("add", "a.txt")
msg, scope = mapui.commit_message(repo, run=fake)
assert scope == "staged" and "new.txt" not in asks[-1], "staged wins, and is all it sees"
print("ok")
