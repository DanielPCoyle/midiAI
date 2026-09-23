"""Guardrail enforcement: compiling, running, approving, hooks. Every model
call is faked -- this must never invoke the real `claude` CLI.
python3 test_guardrails.py"""
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
guardrails.set_trigger(repo, "review", "stop")   # isolate it from the trigger scans below

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

# ---- trigger selection ----
compile_script("deploy-chk", "#!/bin/sh\necho deployed\nexit 0\n", phase="deploy")
guardrails.set_trigger(repo, "test", "pre-commit")
guardrails.set_trigger(repo, "design", "stop")   # keep some-id (agent kind) off the manual scan

sel = guardrails.run(repo, trigger="pre-commit", trust=True, run_model=guard_model)
assert {r["id"] for r in sel} == {"pass-chk", "fail-chk", "na-chk", "untrusted-chk"}, sel

sel2 = guardrails.run(repo, trigger="manual", trust=True, run_model=guard_model)
ids2 = {r["id"] for r in sel2}
assert ids2 == {"deploy-chk"}, ids2   # test -> pre-commit, review/design -> stop

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
assert set(st["hooks"]) == {"pre-commit", "pre-push", "stop"}

# ---- install_hooks: leaves a foreign hook alone, is idempotent ----
repo2 = new_repo()
hooks_dir = os.path.join(repo2, ".git", "hooks")
os.makedirs(hooks_dir, exist_ok=True)
foreign_path = os.path.join(hooks_dir, "pre-push")
with open(foreign_path, "w") as f:
    f.write("#!/bin/sh\necho someone else's hook\n")
os.chmod(foreign_path, 0o755)

res = guardrails.install_hooks(repo2)
assert res["pre-commit"] == "installed" and res["stop"] == "installed"
assert res["pre-push"] == "exists", "a hook with no marker is never touched"
assert open(foreign_path).read() == "#!/bin/sh\necho someone else's hook\n"

res2 = guardrails.install_hooks(repo2)
assert res2 == {"pre-commit": "ours", "pre-push": "exists", "stop": "ours"}, \
    "installing twice is a no-op on what is already ours"

un = guardrails.uninstall_hooks(repo2)
assert un["pre-commit"] == "removed" and un["stop"] == "removed"
assert un["pre-push"] == "left alone"
assert not os.path.exists(os.path.join(hooks_dir, "pre-commit"))
assert os.path.exists(foreign_path), "uninstall never removes a hook we did not write"
assert guardrails.status(repo2)["hooks"]["stop"] is False

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
assert any("guardrails.py" in c and "hook stop" in c for c in stop_cmds), "ours was added"
assert guardrails.status(repo3)["hooks"]["stop"] is True

# ---- stop hook: stop_hook_active exits 0 silently, never touches run() ----
proc = subprocess.run([sys.executable, GUARDRAILS_PY, "hook", "stop", "--cwd", repo],
                      input=json.dumps({"stop_hook_active": True, "cwd": repo}),
                      text=True, capture_output=True, timeout=30)
assert proc.returncode == 0 and proc.stdout.strip() == "" and proc.stderr.strip() == "", \
    (proc.returncode, proc.stdout, proc.stderr)

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

print("ok")
