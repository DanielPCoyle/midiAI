"""Guardrail enforcement: compiling, running, approving, hooks, events and
evidence. Every model call is faked -- this must never invoke the real
`claude` CLI.
python3 test_guardrails.py"""
import glob
import json
import os
import stat
import subprocess
import sys
import tempfile

import guardrails

GUARDRAILS_PY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "guardrails.py")

# Local state lives outside the repo, so tests point it at a scratch file
# rather than the real ~/.midiai/*, the same way test_queue.py repoints
# mapui.QUEUE_FILE.
guardrails.APPROVALS_FILE = os.path.join(tempfile.mkdtemp(), "approvals.json")
guardrails.RUNS_FILE = os.path.join(tempfile.mkdtemp(), "runs.json")


def new_repo():
    root = tempfile.mkdtemp()
    g = lambda *a: subprocess.run(["git", "-C", root, *a], check=True, capture_output=True)
    g("init", "-q", "-b", "main")
    g("config", "user.email", "t@t")
    g("config", "user.name", "t")
    open(os.path.join(root, "a.txt"), "w").write("one\n")
    g("add", "a.txt")
    g("commit", "-qm", "feat: first")
    return root


def guard_model(prompt):
    raise AssertionError("a script-only run must not reach the review model")


repo = new_repo()


def compile_script(id_, script, phase="test"):
    return guardrails.compile_rail(
        repo, {"id": id_, "phase": phase, "title": id_, "implemented": "", "validate": ""},
        run=lambda p: json.dumps({"kind": "script", "lang": "sh", "script": script}))


def compile_agent(id_, brief, phase="review"):
    return guardrails.compile_rail(
        repo, {"id": id_, "phase": phase, "title": id_, "implemented": "", "validate": ""},
        run=lambda p: json.dumps({"kind": "agent", "brief": brief}))


# ---- compile: script, files + manifest written, chmod +x ----
def fake_script_prompt(prompt):
    assert "some-id" in prompt and "the shape of a good title" in prompt
    return json.dumps({"kind": "script", "lang": "sh", "script": "#!/bin/sh\necho ok\nexit 0\n"})


item = {"id": "some-id", "phase": "design", "title": "T",
       "implemented": "the shape of a good title", "validate": "y"}
out = guardrails.compile_rail(repo, item, run=fake_script_prompt)
assert out == {"kind": "script", "file": "some-id.sh", "content": "#!/bin/sh\necho ok\nexit 0\n"}, out
script_path = os.path.join(repo, ".guardrails", "some-id.sh")
assert os.path.exists(script_path)
assert os.stat(script_path).st_mode & stat.S_IXUSR, "compiled scripts are chmod +x"
m = guardrails.load_manifest(repo)
assert m["rails"]["some-id"]["kind"] == "script" and m["rails"]["some-id"]["blocking"] is False
assert m["rails"]["some-id"]["gate"] == "exit", "default gate when the item names none"
assert m["rails"]["some-id"]["needs"] == [], "default gate when the item names none"

# ---- compile: agent, and a recompile replaces the other kind's file, keeping blocking ----
guardrails.set_blocking(repo, "some-id", True)
out2 = guardrails.compile_rail(repo, item, run=lambda p: json.dumps({"kind": "agent", "brief": "Look for X."}))
assert out2 == {"kind": "agent", "file": "some-id.review.md", "content": "Look for X."}
assert not os.path.exists(script_path), "recompile to the other kind removes the old file"
assert os.path.exists(os.path.join(repo, ".guardrails", "some-id.review.md"))
m = guardrails.load_manifest(repo)
assert m["rails"]["some-id"]["blocking"] is True, "a recompile keeps the existing blocking flag"

# ---- script run: pass / fail / exit-2 na / unapproved / --trust ----
compile_script("pass-chk", "#!/bin/sh\necho all good\nexit 0\n")
compile_script("fail-chk", "#!/bin/sh\necho went wrong\nexit 1\n")
compile_script("na-chk", "#!/bin/sh\necho not applicable\nexit 2\n")

r = guardrails.run(repo, ids=["pass-chk"])[0]
assert r["verdict"] == "unapproved", "an unapproved script is not executed"

for rid in ("pass-chk", "fail-chk", "na-chk"):
    guardrails.approve(repo, rid)
results = {r["id"]: r for r in guardrails.run(repo, ids=["pass-chk", "fail-chk", "na-chk"])}
assert results["pass-chk"]["verdict"] == "pass" and "all good" in results["pass-chk"]["reason"]
assert results["fail-chk"]["verdict"] == "fail" and "went wrong" in results["fail-chk"]["reason"]
assert results["na-chk"]["verdict"] == "na" and "not applicable" in results["na-chk"]["reason"]
assert results["pass-chk"]["event"] == "manual" and results["pass-chk"]["gate"] == "exit", \
    "results carry event and gate even for an unbound manual run"

compile_script("untrusted-chk", "#!/bin/sh\necho trusted run\nexit 0\n")
assert guardrails.run(repo, ids=["untrusted-chk"])[0]["verdict"] == "unapproved"
r = guardrails.run(repo, ids=["untrusted-chk"], trust=True)[0]
assert r["verdict"] == "pass" and "trusted run" in r["reason"], "--trust skips the approval gate"

# ---- content change drops approval ----
assert guardrails.is_approved(repo, "pass-chk")
compile_script("pass-chk", "#!/bin/sh\necho different now\nexit 0\n")
assert not guardrails.is_approved(repo, "pass-chk"), "a recompiled script needs re-approval"
guardrails.approve(repo, "pass-chk")
assert guardrails.is_approved(repo, "pass-chk")

# ---- agent review: fake model, pass / fail / unparseable -> error, no-diff -> na ----
compile_agent("review-chk", "Look for a docstring on every new function.")
guardrails.set_binding(repo, "review", "exit", ["agent:stop"])

r = guardrails.run(repo, ids=["review-chk"], run_model=lambda p: "must not be called")[0]
assert r["verdict"] == "na" and "no diff" in r["reason"], "a clean tree has nothing to review"

open(os.path.join(repo, "a.txt"), "w").write("two\n")


def fake_pass(prompt):
    assert "Look for a docstring" in prompt
    return json.dumps({"verdict": "pass", "reason": "looks fine"})


r = guardrails.run(repo, ids=["review-chk"], run_model=fake_pass)[0]
assert r["verdict"] == "pass" and r["reason"] == "looks fine"

r = guardrails.run(repo, ids=["review-chk"],
                   run_model=lambda p: json.dumps({"verdict": "fail", "reason": "missing tests"}))[0]
assert r["verdict"] == "fail" and r["reason"] == "missing tests"

r = guardrails.run(repo, ids=["review-chk"], run_model=lambda p: "not json at all")[0]
assert r["verdict"] == "error", r

# ---- event selection by gate ----
compile_script("deploy-chk", "#!/bin/sh\necho deployed\nexit 0\n", phase="deploy")
guardrails.set_binding(repo, "test", "exit", ["git:pre-commit"])
guardrails.set_binding(repo, "design", "exit", ["agent:stop"])

sel = guardrails.run(repo, event="git:pre-commit", trust=True, run_model=guard_model)
assert {r["id"] for r in sel} == {"pass-chk", "fail-chk", "na-chk", "untrusted-chk"}, sel
assert all(r["event"] == "git:pre-commit" and r["gate"] == "exit" for r in sel)

sel_none = guardrails.run(repo, event="git:pre-commit", ids=["deploy-chk"], trust=True,
                          run_model=guard_model)
assert sel_none == [], "deploy-chk's phase has no binding, so no event selects it"

# manual (no event) is unfiltered by bindings -- the Run button always works
sel2 = guardrails.run(repo, trigger="manual", ids=["deploy-chk"], trust=True, run_model=guard_model)
assert {r["id"] for r in sel2} == {"deploy-chk"}, sel2

sel3 = guardrails.run(repo, phase="deploy", trust=True, run_model=guard_model)
assert {r["id"] for r in sel3} == {"deploy-chk"}

# ---- blocking: the same check the CLI's exit code is built from ----
guardrails.set_blocking(repo, "fail-chk", True)
blocked = guardrails.run(repo, ids=["fail-chk"], trust=True)
assert guardrails._blocking_failures(blocked), "a blocking rail that fails must count as blocking"
guardrails.set_blocking(repo, "fail-chk", False)
advisory = guardrails.run(repo, ids=["fail-chk"], trust=True)
assert not guardrails._blocking_failures(advisory), "a non-blocking failure is advisory only"

# ---- status() shape ----
st = guardrails.status(repo)
assert st["manifest"]["rails"]["pass-chk"]["kind"] == "script"
assert st["approved"]["pass-chk"] is True and st["approved"]["deploy-chk"] is False
assert st["results"]["pass-chk"]["verdict"] == "pass"
assert set(st["hooks"]) == {"git:pre-commit", "git:commit-msg", "git:pre-push",
                            "git:post-merge", "agent:stop"}

# ---- install_hooks: leaves a foreign hook alone, is idempotent, covers all four git hooks ----
repo2 = new_repo()
hooks_dir = os.path.join(repo2, ".git", "hooks")
os.makedirs(hooks_dir, exist_ok=True)
foreign_path = os.path.join(hooks_dir, "pre-push")
with open(foreign_path, "w") as f:
    f.write("#!/bin/sh\necho someone else's hook\n")
os.chmod(foreign_path, 0o755)

res = guardrails.install_hooks(repo2)
assert res["git:pre-commit"] == "installed" and res["git:commit-msg"] == "installed"
assert res["git:post-merge"] == "installed" and res["agent:stop"] == "installed"
assert res["git:pre-push"] == "exists", "a hook with no marker is never touched"
assert open(foreign_path).read() == "#!/bin/sh\necho someone else's hook\n"
assert "hook git:pre-commit" in open(os.path.join(hooks_dir, "pre-commit")).read()
assert "hook git:commit-msg" in open(os.path.join(hooks_dir, "commit-msg")).read()
assert "hook git:post-merge" in open(os.path.join(hooks_dir, "post-merge")).read()

res2 = guardrails.install_hooks(repo2)
assert res2 == {"git:pre-commit": "ours", "git:commit-msg": "ours", "git:pre-push": "exists",
                "git:post-merge": "ours", "agent:stop": "ours"}, \
    "installing twice is a no-op on what is already ours"

un = guardrails.uninstall_hooks(repo2)
assert un["git:pre-commit"] == "removed" and un["agent:stop"] == "removed"
assert un["git:commit-msg"] == "removed" and un["git:post-merge"] == "removed"
assert un["git:pre-push"] == "left alone"
assert not os.path.exists(os.path.join(hooks_dir, "pre-commit"))
assert os.path.exists(foreign_path), "uninstall never removes a hook we did not write"
assert guardrails.status(repo2)["hooks"]["agent:stop"] is False

# ---- settings.local.json merge preserves what was already there ----
repo3 = new_repo()
settings_path = os.path.join(repo3, ".claude", "settings.local.json")
os.makedirs(os.path.dirname(settings_path), exist_ok=True)
with open(settings_path, "w") as f:
    json.dump({"permissions": {"defaultMode": "auto"},
              "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "afplay foo"}]}]}}, f)

guardrails.install_hooks(repo3)
with open(settings_path) as f:
    merged = json.load(f)
assert merged["permissions"] == {"defaultMode": "auto"}, "an unrelated top-level key survives"
stop_cmds = [h["command"] for e in merged["hooks"]["Stop"] for h in e["hooks"]]
assert "afplay foo" in stop_cmds, "the pre-existing Stop hook entry survives"
assert any("guardrails.py" in c and "hook agent:stop" in c for c in stop_cmds), "ours was added"
assert guardrails.status(repo3)["hooks"]["agent:stop"] is True

# ---- the old bare "hook stop" command still counts as installed, and uninstall removes it ----
repo3b = new_repo()
settings_path_b = os.path.join(repo3b, ".claude", "settings.local.json")
os.makedirs(os.path.dirname(settings_path_b), exist_ok=True)
with open(settings_path_b, "w") as f:
    json.dump({"hooks": {"Stop": [{"hooks": [{"type": "command",
                                              "command": guardrails._stop_command_old()}]}]}}, f)
assert guardrails.status(repo3b)["hooks"]["agent:stop"] is True, "the old command counts as installed"
res3b = guardrails.install_hooks(repo3b)
assert res3b["agent:stop"] == "ours", "already installed under the old command -- nothing duplicated"
un3b = guardrails.uninstall_hooks(repo3b)
assert un3b["agent:stop"] == "removed"
assert guardrails.status(repo3b)["hooks"]["agent:stop"] is False

# ---- stop hook: stop_hook_active exits 0 silently, never touches run() ----
proc = subprocess.run([sys.executable, GUARDRAILS_PY, "hook", "stop", "--cwd", repo],
                      input=json.dumps({"stop_hook_active": True, "cwd": repo}),
                      text=True, capture_output=True, timeout=30)
assert proc.returncode == 0 and proc.stdout.strip() == "" and proc.stderr.strip() == "", \
    (proc.returncode, proc.stdout, proc.stderr)

# ---- old bare alias `hook pre-commit` still resolves to the git:pre-commit event ----
repo8 = new_repo()


def compile_script8(id_, script, phase="p"):
    return guardrails.compile_rail(repo8, {"id": id_, "phase": phase, "title": id_},
                                   run=lambda p: json.dumps({"kind": "script", "lang": "sh",
                                                              "script": script}))


compile_script8("alias-chk", "#!/bin/sh\necho ran\nexit 0\n")
guardrails.set_binding(repo8, "p", "exit", ["git:pre-commit"])

# The subprocess below is a fresh interpreter, so it does not see this
# process's monkeypatched APPROVALS_FILE/RUNS_FILE -- give it its own HOME
# instead of touching the real ~/.midiai, and pre-approve the script there
# (the file on disk is real and shared; only the approval record is per-home).
fake_home8 = tempfile.mkdtemp()
_, alias_content = guardrails.rail_file(repo8, "alias-chk")
rooted8 = guardrails._root(repo8)
fake_approvals8 = os.path.join(fake_home8, ".midiai", "guardrail-approvals.json")
os.makedirs(os.path.dirname(fake_approvals8), exist_ok=True)
with open(fake_approvals8, "w") as f:
    json.dump({rooted8: {"alias-chk": guardrails.digest(alias_content)}}, f)

proc = subprocess.run([sys.executable, GUARDRAILS_PY, "hook", "pre-commit", "--cwd", repo8],
                      capture_output=True, text=True, timeout=30,
                      env=dict(os.environ, HOME=fake_home8))
assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
with open(os.path.join(fake_home8, ".midiai", "guardrail-runs.json")) as f:
    runs8 = json.load(f)
assert runs8[rooted8]["alias-chk"]["verdict"] == "pass", runs8
assert runs8[rooted8]["alias-chk"]["event"] == "git:pre-commit", "the bare alias resolves to the event"

# ---- a manifest is repo content: its "file" cannot reach outside .guardrails ----
repo4 = new_repo()
os.makedirs(os.path.join(repo4, ".guardrails"))
guardrails.save_manifest(repo4, {"rails": {"x": {"file": "../evil.sh", "kind": "script",
                                                 "blocking": True}}})
with open(os.path.join(repo4, "evil.sh"), "w") as f:
    f.write("#!/bin/sh\nexit 0\n")
os.chmod(os.path.join(repo4, "evil.sh"), 0o755)
assert guardrails.rail_file(repo4, "x") == (None, ""), "no path outside .guardrails"
[r] = guardrails.run(repo4, trust=True)
assert r["verdict"] == "error", "even trusted, an escaping file is not executed"

# ---- a hook fired from a subfolder keys on the same root mapui does ----
os.makedirs(os.path.join(repo4, "sub"))
assert guardrails._root(os.path.join(repo4, "sub")) == os.path.realpath(repo4)

# ---- approval is of the text that was viewed, not of whatever is there now ----
repo5 = new_repo()
guardrails.compile_rail(repo5, {"id": "seen", "phase": "test", "title": "t"},
                        run=lambda p: '{"kind":"script","lang":"sh","script":"#!/bin/sh\nexit 0"}')
_, shown = guardrails.rail_file(repo5, "seen")
seen = guardrails.digest(shown)
with open(os.path.join(repo5, ".guardrails", "seen.sh"), "a") as f:
    f.write("rm -rf ~  # slipped in after the view\n")
try:
    guardrails.approve(repo5, "seen", expect=seen)
    raise AssertionError("approved text nobody viewed")
except ValueError:
    pass
assert not guardrails.is_approved(repo5, "seen")

# ---- model JSON: fenced, a "}" inside a string, a raw newline in a string ----
got = guardrails._parse_json_object('```json\n{"kind":"script","script":"echo \\"}\\"\nexit 0"}\n```')
assert got["script"] == 'echo "}"\nexit 0', got

# ---- v1 -> v2 migration: triggers become bindings, rails get gate/needs defaults ----
repo6 = new_repo()
os.makedirs(os.path.join(repo6, ".guardrails"))
v1 = {"version": 1, "triggers": {"implement": "stop", "ship": "pre-commit", "loose": "manual"},
     "rails": {"r1": {"phase": "implement", "kind": "script", "file": "r1.sh", "blocking": False}}}
with open(os.path.join(repo6, ".guardrails", "manifest.json"), "w") as f:
    json.dump(v1, f)

migrated = guardrails.load_manifest(repo6)
assert migrated["version"] == 2
assert migrated["bindings"] == {"implement": {"exit": ["agent:stop"]},
                                "ship": {"exit": ["git:pre-commit"]}}, migrated["bindings"]
assert "loose" not in migrated["bindings"], "manual maps to no binding"
assert migrated["rails"]["r1"]["gate"] == "exit" and migrated["rails"]["r1"]["needs"] == []

with open(os.path.join(repo6, ".guardrails", "manifest.json")) as f:
    assert json.load(f)["version"] == 1, "load_manifest migrates in memory only"

guardrails.set_blocking(repo6, "r1", True)   # any save persists the migrated shape
with open(os.path.join(repo6, ".guardrails", "manifest.json")) as f:
    on_disk = json.load(f)
assert on_disk["version"] == 2 and "triggers" not in on_disk
assert on_disk["bindings"]["implement"] == {"exit": ["agent:stop"]}

# unknown events are dropped on load, and on save
guardrails.save_manifest(repo6, {"version": 2, "bindings": {"x": {"exit": ["bogus", "agent:stop"]}},
                                 "rails": {}})
assert guardrails.load_manifest(repo6)["bindings"] == {"x": {"exit": ["agent:stop"]}}

# ---- compile stores gate + needs from a fake model, and tells it what's bound ----
repo9 = new_repo()
out9 = guardrails.compile_rail(
    repo9, {"id": "with-gate", "phase": "implement", "gate": "entry", "title": "T"},
    run=lambda p: json.dumps({"kind": "script", "lang": "sh", "script": "#!/bin/sh\nexit 0\n",
                              "needs": ["diff", "files", "bogus"]}))
m9 = guardrails.load_manifest(repo9)
assert m9["rails"]["with-gate"]["gate"] == "entry"
assert m9["rails"]["with-gate"]["needs"] == ["diff", "files"], "an unknown need is dropped"

guardrails.set_binding(repo9, "implement", "entry", ["agent:stop"])
seen_prompt = {}


def capture(p):
    seen_prompt["p"] = p
    return json.dumps({"kind": "script", "lang": "sh", "script": "#!/bin/sh\nexit 0\n", "needs": []})


guardrails.compile_rail(repo9, {"id": "with-gate", "phase": "implement", "gate": "entry", "title": "T"},
                        run=capture)
assert "agent:stop" in seen_prompt["p"] and "entry" in seen_prompt["p"], \
    "the prompt names the bound events for this rail's gate"

# ---- evidence: files per event, needs missing -> na/fail, commit-msg via GUARDRAIL_EVIDENCE ----
repo7 = new_repo()


def compile_script7(id_, script, phase="gate-test", gate="exit", needs=None):
    return guardrails.compile_rail(
        repo7, {"id": id_, "phase": phase, "gate": gate, "title": id_},
        run=lambda p: json.dumps({"kind": "script", "lang": "sh", "script": script,
                                  "needs": needs or []}))


evidence_probe = (
    "#!/bin/sh\n"
    "if [ -f \"$GUARDRAIL_EVIDENCE/files.txt\" ]; then echo files=yes; else echo files=no; fi\n"
    "if [ -f \"$GUARDRAIL_EVIDENCE/commit-msg.txt\" ]; then echo commitmsg=yes; else echo commitmsg=no; fi\n"
    "exit 0\n"
)
compile_script7("probe", evidence_probe)
guardrails.approve(repo7, "probe")
guardrails.set_binding(repo7, "gate-test", "exit", ["agent:stop", "git:commit-msg"])

open(os.path.join(repo7, "a.txt"), "w").write("changed\n")
r = guardrails.run(repo7, ids=["probe"], event="agent:stop")[0]
assert r["verdict"] == "pass", r
assert "files=yes" in r["reason"]
assert "commitmsg=no" in r["reason"], "agent:stop does not supply a commit message"

msg_path = os.path.join(repo7, "MSGFILE")
with open(msg_path, "w") as f:
    f.write("fix stuff\n")
r2 = guardrails.run(repo7, ids=["probe"], event="git:commit-msg", msg_file=msg_path)[0]
assert "commitmsg=yes" in r2["reason"], "git:commit-msg supplies a commit message"

# needs the firing event cannot supply: na when advisory, fail when blocking
compile_script7("needs-msg", "#!/bin/sh\nexit 0\n", needs=["commit-msg"])
r3 = guardrails.run(repo7, ids=["needs-msg"], event="agent:stop", trust=True)[0]
assert r3["verdict"] == "na" and "needs commit-msg" in r3["reason"], r3
guardrails.set_blocking(repo7, "needs-msg", True)
r4 = guardrails.run(repo7, ids=["needs-msg"], event="agent:stop", trust=True)[0]
assert r4["verdict"] == "fail" and "needs commit-msg" in r4["reason"], r4
r5 = guardrails.run(repo7, ids=["needs-msg"], event="git:commit-msg", trust=True,
                    msg_file=msg_path)[0]
assert r5["verdict"] == "pass", "the same rail runs fine on an event that does supply it"

# a script actually reading the commit message via GUARDRAIL_EVIDENCE
ticket_gate = (
    "#!/bin/sh\n"
    "grep -qE '[A-Z]+-[0-9]+' \"$GUARDRAIL_EVIDENCE/commit-msg.txt\" || "
    "{ echo 'no ticket key in commit message'; exit 1; }\n"
    "echo ok\nexit 0\n"
)
compile_script7("ticket-key", ticket_gate, needs=["commit-msg"])
guardrails.approve(repo7, "ticket-key")

with open(msg_path, "w") as f:
    f.write("no key here\n")
r6 = guardrails.run(repo7, ids=["ticket-key"], event="git:commit-msg", msg_file=msg_path)[0]
assert r6["verdict"] == "fail" and "no ticket key" in r6["reason"], r6

with open(msg_path, "w") as f:
    f.write("MIDI-1 fix\n")
r7 = guardrails.run(repo7, ids=["ticket-key"], event="git:commit-msg", msg_file=msg_path)[0]
assert r7["verdict"] == "pass", r7

# the evidence dir is removed after a normal run
before = set(glob.glob(os.path.join(tempfile.gettempdir(), "guardrail-evidence-*")))
guardrails.run(repo7, ids=["probe"], event="agent:stop")
after = set(glob.glob(os.path.join(tempfile.gettempdir(), "guardrail-evidence-*")))
assert after == before, "evidence dir must be removed after a run"

# ...and removed even when a check throws (try/finally, not a happy-path-only cleanup)
orig_run_script = guardrails._run_script
guardrails._run_script = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
before = set(glob.glob(os.path.join(tempfile.gettempdir(), "guardrail-evidence-*")))
try:
    guardrails.run(repo7, ids=["probe"], event="agent:stop")
    raise AssertionError("expected the injected failure to propagate")
except RuntimeError as e:
    assert str(e) == "boom"
finally:
    guardrails._run_script = orig_run_script
after = set(glob.glob(os.path.join(tempfile.gettempdir(), "guardrail-evidence-*")))
assert after == before, "evidence dir is removed even when a check throws"

# ---- a manual Run gives a commit-message rail the last commit's message ----
repo6 = new_repo()
guardrails.compile_rail(repo6, {"id": "msg", "phase": "implement", "gate": "exit"},
                        run=lambda p: json.dumps({"kind": "script", "lang": "sh", "needs": ["commit-msg"],
                                                  "script": "#!/bin/sh\ngrep -q first \"$GUARDRAIL_EVIDENCE/commit-msg.txt\""}))
[r] = guardrails.run(repo6, trust=True)
assert r["verdict"] == "pass", r

print("ok")
