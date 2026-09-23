"""Setting a subagent type's model in its definition. python3 test_agent_def.py"""
import os
import tempfile

import mapui

tmp = tempfile.mkdtemp()
repo = os.path.join(tmp, "repo")
os.makedirs(os.path.join(repo, ".claude", "agents"))


def write(name, text):
    path = os.path.join(repo, ".claude", "agents", name + ".md")
    with open(path, "w") as f:
        f.write(text)
    return path


pinned = write("worker", "---\nname: worker\nmodel: sonnet\ntools: Read\n---\n\nBody: model: opus stays.\n")
path, scope = mapui.agent_def("worker", repo)
assert (path, scope) == (pinned, "project"), (path, scope)
assert mapui.agent_model(pinned) == "sonnet"

assert mapui.set_agent_model(pinned, "haiku") is None
text = open(pinned).read()
assert text == "---\nname: worker\nmodel: haiku\ntools: Read\n---\n\nBody: model: opus stays.\n", \
    "one frontmatter line changes; the body's own 'model:' is not touched"

unpinned = write("reviewer", "---\nname: reviewer\n---\nreview things\n")
assert mapui.agent_model(unpinned) == "inherit", "no model: line means it inherits"
assert mapui.set_agent_model(unpinned, "opus") is None
assert open(unpinned).read() == "---\nname: reviewer\nmodel: opus\n---\nreview things\n"

bare = write("bare", "no frontmatter at all\n")
assert mapui.set_agent_model(bare, "opus"), "a file with no frontmatter is refused"
assert open(bare).read() == "no frontmatter at all\n", "and left alone"

for bad in ("../etc", "Explore/../../x", "", "Worker"):
    assert mapui.agent_def(bad, repo) == (None, None), bad
assert mapui.agent_def("general-purpose", repo) == (None, None), "a built-in has no file"
print("ok")
