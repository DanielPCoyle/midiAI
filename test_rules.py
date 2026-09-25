"""rules.py: reading and writing CLAUDE.md/AGENTS.md/.claude/rules the way
Claude Code's own memory model reads them, with the AGENTS.md sync, the
effective() load order, and the model-backed review. Every model call is
faked -- this must never invoke the real `claude` CLI. HOME is faked for the
whole process -- this must never read or write the real ~/.claude.
python3 test_rules.py"""
import json
import os
import subprocess
import tempfile

# Fake HOME before anything below ever calls rules.* -- rules.py reads
# os.path.expanduser("~") lazily at call time (never caches it at import),
# specifically so a test process can do this. Realpath'd because mapui's
# _rules_repo_request always hands rules.py an already-realpath'd root (it
# realpaths cwd, and `git rev-parse --show-toplevel` canonicalizes too) --
# on macOS /tmp -> /private/tmp, and a test root left un-canonicalized would
# fail containment checks the real caller never triggers.
FAKE_HOME = os.path.realpath(tempfile.mkdtemp())
os.environ["HOME"] = FAKE_HOME

import rules  # noqa: E402 -- after HOME is faked


def new_repo(where=None):
    root = os.path.realpath(where or tempfile.mkdtemp())
    if not os.path.isdir(root):
        os.makedirs(root)
    g = lambda *a: subprocess.run(["git", "-C", root, *a], check=True, capture_output=True)
    g("init", "-q", "-b", "main")
    g("config", "user.email", "t@t")
    g("config", "user.name", "t")
    open(os.path.join(root, "a.txt"), "w").write("one\n")
    g("add", "a.txt")
    g("commit", "-qm", "feat: first")
    return root


def guard_model(prompt):
    raise AssertionError("must never reach the real review model")


repo = new_repo()

# ==================================================================
# list_rules: always-on slots, missing ones flagged, recursive rules
# ==================================================================

rows = rules.list_rules(repo)
by_kind_scope = [(r["scope"], r["kind"], r["exists"]) for r in rows]
assert ("global", "file", False) in by_kind_scope, rows          # ~/.claude/CLAUDE.md missing
assert ("project", "file", False) in by_kind_scope               # <root>/CLAUDE.md missing
assert ("local", "file", False) in by_kind_scope                 # CLAUDE.local.md missing
agents_rows = [r for r in rows if r["scope"] == "project" and r["path"].endswith("AGENTS.md")]
assert len(agents_rows) == 1 and agents_rows[0]["exists"] is False, "AGENTS.md is always listed"
dot_rows = [r for r in rows if r["scope"] == "project"
           and r["path"] == os.path.join(repo, ".claude", "CLAUDE.md")]
assert dot_rows == [], ".claude/CLAUDE.md is listed only when it exists"

os.makedirs(os.path.join(repo, ".claude"), exist_ok=True)
open(os.path.join(repo, ".claude", "CLAUDE.md"), "w").write("# Project (dot)\n\nhi\n")
rows2 = rules.list_rules(repo)
dot_rows2 = [r for r in rows2 if r["scope"] == "project"
            and r["path"] == os.path.join(repo, ".claude", "CLAUDE.md")]
assert len(dot_rows2) == 1 and dot_rows2[0]["exists"] is True
os.remove(os.path.join(repo, ".claude", "CLAUDE.md"))

# a nested rule is still found (recursive) and its `rel` is under the rules dir
nested_dir = os.path.join(repo, ".claude", "rules", "sub")
os.makedirs(nested_dir, exist_ok=True)
open(os.path.join(nested_dir, "nested.md"), "w").write("# Nested\n\nbody\n")
rows3 = rules.list_rules(repo)
nested_rows = [r for r in rows3 if r["kind"] == "rule" and r["scope"] == "project"]
assert len(nested_rows) == 1 and nested_rows[0]["rel"] == os.path.join("sub", "nested.md"), nested_rows
assert nested_rows[0]["title"] == "Nested"
os.remove(os.path.join(nested_dir, "nested.md"))
os.rmdir(nested_dir)

# ==================================================================
# write_rule: with/without paths (frontmatter exact), never overwrite
# ==================================================================

p1 = rules.write_rule(repo, "project", "My First Rule", "Always do X.", [])
assert p1 == os.path.join(repo, ".claude", "rules", "my-first-rule.md"), p1
with open(p1) as f:
    text1 = f.read()
assert text1 == "# My First Rule\n\nAlways do X.\n", repr(text1)

p2 = rules.write_rule(repo, "project", "My First Rule", "A different body.", [])
assert p2 == os.path.join(repo, ".claude", "rules", "my-first-rule-2.md"), \
    "a same-titled rule never overwrites the first -- it gets -2"
assert os.path.exists(p1), "the original is untouched"
with open(p1) as f:
    assert f.read() == text1, "never overwritten"

p3 = rules.write_rule(repo, "project", "Scoped Rule", "Only for API code.",
                      ["src/api/**", "app/**/*.tsx"])
with open(p3) as f:
    text3 = f.read()
assert text3 == ('---\npaths:\n  - "src/api/**"\n  - "app/**/*.tsx"\n---\n\n'
                 '# Scoped Rule\n\nOnly for API code.\n'), repr(text3)

# editing an existing rule keeps its path, and can add/drop frontmatter
p1_again = rules.write_rule(repo, "project", "My First Rule", "Edited body.",
                            ["only/here/**"], path=p1)
assert p1_again == p1, "editing keeps its path"
with open(p1) as f:
    text1b = f.read()
assert text1b == '---\npaths:\n  - "only/here/**"\n---\n\n# My First Rule\n\nEdited body.\n'

# global scope writes to ~/.claude/rules, entirely separate from the project's
pg = rules.write_rule(repo, "global", "Global Habit", "Never do Y.", [])
assert pg == os.path.join(FAKE_HOME, ".claude", "rules", "global-habit.md"), pg
assert os.path.commonpath([pg, FAKE_HOME]) == os.path.realpath(FAKE_HOME)

# local is not a rule scope
try:
    rules.write_rule(repo, "local", "Nope", "x", [])
    assert False, "local has no rules dir"
except ValueError:
    pass

# ==================================================================
# path escape refused: .., absolute elsewhere, non-.md
# ==================================================================

for bad in (
    os.path.join(repo, ".claude", "rules", "..", "..", "evil.md"),
    "/etc/hosts",
    os.path.join(repo, ".claude", "rules", "not-markdown.sh"),
    os.path.join(repo, ".claude", "rules", "..", "AGENTS.md"),
):
    try:
        rules.read(repo, bad)
        assert False, f"should have refused: {bad}"
    except ValueError:
        pass

try:
    rules.write_file(repo, "/etc/hosts", "pwned")
    assert False
except ValueError:
    pass

try:
    rules.delete_rule(repo, os.path.join(repo, "CLAUDE.md"))
    assert False, "CLAUDE.md is never deletable as a rule"
except ValueError:
    pass

# the managed policy is read-only
try:
    rules.write_file(repo, rules.MANAGED_POLICY, "no")
    assert False
except ValueError:
    pass

# AGENTS.md is editable around its generated block: hand text is kept,
# the block is regenerated (an edit inside it is replaced, not kept)
rules.write_file(repo, os.path.join(repo, "AGENTS.md"), "hand-written intro\n")
with open(os.path.join(repo, "AGENTS.md")) as f:
    got = f.read()
assert got.startswith("hand-written intro\n"), got
assert rules._AGENTS_START in got and rules._AGENTS_END in got, "the block comes back"
assert rules.agents_md_state(repo)["synced"], "and it is current"

# ==================================================================
# AGENTS.md sync: block created/updated, outside text untouched,
# scoped rules excluded, doubled flag
# ==================================================================

repo2 = new_repo()
open(os.path.join(repo2, "AGENTS.md"), "w").write(
    "# Hand-written notes\n\nSomeone wrote this before Podium touched the file.\n")
rules.write_rule(repo2, "project", "Formatting", "Two-space indent everywhere.", [])
with open(os.path.join(repo2, "AGENTS.md")) as f:
    agents2 = f.read()
assert agents2.startswith("# Hand-written notes\n\n"
                          "Someone wrote this before Podium touched the file.\n"), agents2
assert rules._AGENTS_START in agents2 and rules._AGENTS_END in agents2
assert "## Formatting" in agents2 and "Two-space indent everywhere." in agents2

state2 = rules.agents_md_state(repo2)
assert state2 == {"exists": True, "synced": True, "claude_md": False, "doubled": True}, state2

# a scoped rule (has `paths:`) never reaches the AGENTS.md block
rules.write_rule(repo2, "project", "API Only", "Validate inputs.", ["src/api/**"])
with open(os.path.join(repo2, "AGENTS.md")) as f:
    agents2b = f.read()
assert "API Only" not in agents2b, "path-scoped rules are Claude-only"
assert agents2b.startswith("# Hand-written notes\n\n"
                           "Someone wrote this before Podium touched the file.\n"), \
    "outside-the-markers text survives a second sync untouched"

state2b = rules.agents_md_state(repo2)
assert state2b["synced"] is True

# doubled flips false once a CLAUDE.md exists at the project
open(os.path.join(repo2, "CLAUDE.md"), "w").write("# Project\n")
state2c = rules.agents_md_state(repo2)
assert state2c["claude_md"] is True and state2c["doubled"] is False, state2c

# delete + move both re-sync the block
rule_a_path = os.path.join(repo2, ".claude", "rules", "formatting.md")
rules.delete_rule(repo2, rule_a_path)
with open(os.path.join(repo2, "AGENTS.md")) as f:
    agents2c = f.read()
assert "## Formatting" not in agents2c, "a deleted unscoped rule leaves the block"

# ==================================================================
# move_rule: between scopes, never overwrites the destination
# ==================================================================

repo3 = new_repo()
m1 = rules.write_rule(repo3, "project", "Movable", "content", [])
moved = rules.move_rule(repo3, m1, "global")
assert moved == os.path.join(FAKE_HOME, ".claude", "rules", "movable.md")
assert not os.path.exists(m1), "the source is gone"
assert os.path.isfile(moved)

# moving it back would collide with the earlier global "Global Habit" only if
# same name -- here it collides with nothing, but moving onto an existing
# name must refuse
rules.write_rule(repo3, "project", "Movable", "second one", [])
try:
    rules.move_rule(repo3, moved, "project")
    assert False, "must not overwrite the rule already at the destination"
except ValueError:
    pass
assert os.path.isfile(moved), "the attempted move left the source alone"

# ==================================================================
# effective(): order, ancestors, local after project, imports, AGENTS.md
# ==================================================================

# a project nested under a parent directory that also holds a stray
# CLAUDE.md -- exercises the ancestor walk, not just the project root
parent = os.path.realpath(tempfile.mkdtemp())
proj_root = os.path.join(parent, "proj")
new_repo(proj_root)
open(os.path.join(parent, "CLAUDE.md"), "w").write("# Ancestor level\n\nbe careful\n")

# global CLAUDE.md that imports another file, to exercise import expansion
os.makedirs(os.path.join(FAKE_HOME, ".claude"), exist_ok=True)
open(os.path.join(FAKE_HOME, ".claude", "CLAUDE.md"), "w").write(
    "# Global\n\nAlways be kind. @imported.md\n\n"
    "```\nnot an import: @fake.md\n```\nand not `@also-fake.md` either.\n")
open(os.path.join(FAKE_HOME, ".claude", "imported.md"), "w").write("# Imported\n\nmore rules\n")
os.makedirs(os.path.join(FAKE_HOME, ".claude", "rules"), exist_ok=True)
open(os.path.join(FAKE_HOME, ".claude", "rules", "global-habit.md"), "w").write(
    "# Global Habit\n\nNever do Y.\n")

open(os.path.join(proj_root, "CLAUDE.md"), "w").write("# Proj root\n\nproj rule\n")
open(os.path.join(proj_root, "CLAUDE.local.md"), "w").write("# Local only\n\nlocal rule\n")
os.makedirs(os.path.join(proj_root, ".claude", "rules"), exist_ok=True)
open(os.path.join(proj_root, ".claude", "rules", "unscoped.md"), "w").write(
    "# Unscoped\n\nalways loads\n")
open(os.path.join(proj_root, ".claude", "rules", "scoped.md"), "w").write(
    '---\npaths:\n  - "*.py"\n---\n\n# Scoped\n\non demand\n')

eff = rules.effective(proj_root)
shown_order = [f["shown"] for f in eff["files"]]

# global user CLAUDE.md comes before its import, which comes before the
# global rule, which comes before anything project-level
i_global = shown_order.index("~/.claude/CLAUDE.md")
i_import = next(i for i, f in enumerate(eff["files"]) if f["path"].endswith("imported.md"))
i_global_rule = next(i for i, f in enumerate(eff["files"])
                     if f["path"].endswith(os.path.join("rules", "global-habit.md")))
i_ancestor = next(i for i, f in enumerate(eff["files"]) if f["path"] == os.path.join(parent, "CLAUDE.md"))
i_proj = next(i for i, f in enumerate(eff["files"]) if f["path"] == os.path.join(proj_root, "CLAUDE.md"))
i_local = next(i for i, f in enumerate(eff["files"])
              if f["path"] == os.path.join(proj_root, "CLAUDE.local.md"))
i_proj_rule = next(i for i, f in enumerate(eff["files"])
                   if f["path"].endswith(os.path.join("rules", "unscoped.md")))
assert i_global < i_import < i_global_rule < i_ancestor < i_proj < i_local < i_proj_rule, \
    [f["shown"] for f in eff["files"]]

import_row = eff["files"][i_import]
assert import_row["imported_by"] == "~/.claude/CLAUDE.md"
assert import_row["scope"] == "import"

# the fenced/backtick @-tokens were never treated as imports
assert not any(f["path"].endswith("fake.md") or f["path"].endswith("also-fake.md")
              for f in eff["files"])

# the on-demand (scoped) rule is present but marked "on demand", the
# unscoped one "start"
scoped_row = next(f for f in eff["files"] if f["path"].endswith("scoped.md"))
unscoped_row = next(f for f in eff["files"] if f["path"].endswith("unscoped.md"))
assert scoped_row["when"] == "on demand" and scoped_row["paths"] == ["*.py"]
assert unscoped_row["when"] == "start"

# AGENTS.md is skipped when a CLAUDE.md exists at the project
assert not any(f["path"] == os.path.join(proj_root, "AGENTS.md") for f in eff["files"])

# now take the CLAUDE.md-family files away: AGENTS.md (once synced) is read
os.remove(os.path.join(proj_root, "CLAUDE.md"))
os.remove(os.path.join(proj_root, "CLAUDE.local.md"))
os.remove(os.path.join(parent, "CLAUDE.md"))
rules.write_rule(proj_root, "project", "Trigger sync", "so AGENTS.md exists", [])
eff2 = rules.effective(proj_root)
assert any(f["path"] == os.path.join(proj_root, "AGENTS.md") for f in eff2["files"]), \
    "no CLAUDE.md anywhere in the chain -- AGENTS.md is read"
assert any("doubles the unscoped project rules" in w for w in eff2["warnings"])

# a file over 200 lines is flagged
big = os.path.join(proj_root, ".claude", "CLAUDE.md")
os.makedirs(os.path.dirname(big), exist_ok=True)
with open(big, "w") as f:
    f.write("# Big\n\n" + "\n".join(f"line {i}" for i in range(250)) + "\n")
eff3 = rules.effective(proj_root)
assert any("over 200 lines" in w for w in eff3["warnings"]), eff3["warnings"]
os.remove(big)

# ==================================================================
# review(): fake model, cached, never the real CLI
# ==================================================================

calls = {"n": 0}


def fake_model(prompt):
    calls["n"] += 1
    assert "CLAUDE.md" in prompt or "===" in prompt
    return json.dumps({"issues": [
        {"kind": "vague", "files": ["~/.claude/CLAUDE.md"], "quote": "be kind",
         "why": "not actionable", "suggest": "say what kind means here"},
        {"kind": "not-a-real-kind", "files": [], "quote": "", "why": "", "suggest": ""},
    ]})


result = rules.review(proj_root, run=fake_model)
assert calls["n"] == 1
assert len(result["issues"]) == 1, "the unrecognised kind is dropped"
assert result["issues"][0]["kind"] == "vague"
assert result["issues"][0]["quote"] == "be kind"
assert "at" in result

# same content -> cached, the model is not asked again
result2 = rules.review(proj_root, run=fake_model)
assert calls["n"] == 1, "identical content must hit the cache, not the model"
assert result2 == result

# a code fence around the JSON is tolerated
fenced = lambda p: "```json\n" + json.dumps({"issues": []}) + "\n```"
# a different cwd (repo, untouched by the review above) so this is a fresh key
result3 = rules.review(repo, run=fenced)
assert result3["issues"] == []

# a model that returns garbage raises rather than silently passing
def bad_model(p):
    return "not json at all"


try:
    rules.review(repo3, run=bad_model)
    assert False, "garbage from the model should raise"
except RuntimeError:
    pass

print("ok")
