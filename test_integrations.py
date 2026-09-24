"""integrations.py's plug-in contract, the Vercel adapter's normalisation,
and mapui's /deploy* + /integrations* logic (through their pure functions --
no live server, no socket). The Keychain is mocked throughout; this must
never touch the real one, never call the real Vercel API, and never invoke
the real `claude` CLI (guardrail agent reviews are faked, same as
test_guardrails.py).
python3 test_integrations.py"""
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile

FAKE_HOME = tempfile.mkdtemp()
os.environ["HOME"] = FAKE_HOME

import access
import guardrails
import integrations

# Every piece of local state lives outside the repo -- point it all at
# scratch files, the same way test_guardrails.py/test_rules.py repoint
# theirs, so nothing here can touch ~/.podium/*.
integrations.BUILTIN_DIR = os.path.join(tempfile.mkdtemp(), "integrations")
integrations.USER_DIR = os.path.join(tempfile.mkdtemp(), "user-integrations")
integrations.STATE_FILE = os.path.join(tempfile.mkdtemp(), "integrations.json")
os.makedirs(integrations.BUILTIN_DIR)
guardrails.APPROVALS_FILE = os.path.join(tempfile.mkdtemp(), "approvals.json")
guardrails.RUNS_FILE = os.path.join(tempfile.mkdtemp(), "runs.json")
guardrails.ACTIVE_SPECS_FILE = os.path.join(tempfile.mkdtemp(), "active-specs.json")

import mapui  # noqa: E402 -- after the state files above are redirected

# ------------------------------------------------------------- fake Keychain
# A dict standing in for the real macOS Keychain. access.keychain_* are
# swapped wholesale, so nothing integrations.py does can reach `security`.
_kc = {}


def _kc_exists(service, account):
    return (service, account) in _kc


def _kc_get(service, account):
    return _kc.get((service, account))


def _kc_set(service, account, value, label=""):
    _kc[(service, account)] = value
    return True


def _kc_delete(service, account):
    _kc.pop((service, account), None)


access.keychain_exists = _kc_exists
access.keychain_get = _kc_get
access.keychain_set = _kc_set
access.keychain_delete = _kc_delete


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


def head_sha(root):
    return subprocess.run(["git", "-C", root, "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()


# ---------------------------------------------------------- a fake integration

FAKE_DIR = os.path.join(integrations.BUILTIN_DIR, "fake")
os.makedirs(FAKE_DIR)
with open(os.path.join(FAKE_DIR, "manifest.json"), "w") as f:
    json.dump({"name": "fake", "title": "Fake", "run": "fake.py",
               "secrets": [{"key": "token", "label": "Token"}],
               "config": [{"key": "team", "label": "Team"}],
               "capabilities": ["auth.check", "deploy.list", "deploy.get",
                                "deploy.promote", "deploy.rollback"]}, f)


def write_fake(body):
    with open(os.path.join(FAKE_DIR, "fake.py"), "w") as f:
        f.write(body)


write_fake("import json, sys\n"
          "d = json.loads(sys.stdin.read())\n"
          "print(json.dumps({'ok': True, 'data': d}))\n")

# ================================================================
# discovery: built-in found; a user folder of the same name overrides it
# ================================================================
assert "fake" in integrations.discover()
assert integrations.discover()["fake"]["builtin"] is True

user_fake = os.path.join(integrations.USER_DIR, "fake")
os.makedirs(user_fake)
with open(os.path.join(user_fake, "manifest.json"), "w") as f:
    json.dump({"name": "fake", "title": "Fake (mine)", "run": "fake.py", "capabilities": []}, f)
with open(os.path.join(user_fake, "fake.py"), "w") as f:
    f.write("import json, sys\nprint(json.dumps({'ok': True, 'data': {}}))\n")
assert integrations.discover()["fake"]["builtin"] is False
assert integrations.discover()["fake"]["manifest"]["title"] == "Fake (mine)"
shutil.rmtree(user_fake)   # back to just the built-in for everything below

# ================================================================
# run containment: a manifest pointing outside its own folder is dropped
# ================================================================
escapee = os.path.join(integrations.BUILTIN_DIR, "escapee")
os.makedirs(escapee)
with open(os.path.join(escapee, "manifest.json"), "w") as f:
    json.dump({"name": "escapee", "run": "../fake/fake.py", "capabilities": []}, f)
assert "escapee" not in integrations.discover(), "run must resolve inside its own folder"
shutil.rmtree(escapee)

badname = os.path.join(integrations.BUILTIN_DIR, "also-bad")
os.makedirs(badname)
with open(os.path.join(badname, "manifest.json"), "w") as f:
    json.dump({"name": "not-the-folder-name", "run": "fake.py", "capabilities": []}, f)
assert "also-bad" not in integrations.discover(), "folder name and manifest name must agree"
shutil.rmtree(badname)

print("discovery + user override + containment: ok")

# ================================================================
# an op not in capabilities is refused before exec
# ================================================================
got = integrations.call("fake", "deploy.cancel", {})
assert got == {"ok": False, "error": "fake does not support deploy.cancel"}, got
print("op not in capabilities refused: ok")

# ================================================================
# secrets: stdin only, never argv/env; the fake echoes what it saw
# ================================================================
calls = []
real_run = subprocess.run


def spy(argv, **k):
    calls.append((argv, k.get("input"), k.get("env")))
    return real_run(argv, **k)


integrations._run = spy

out = integrations.secret_set("fake", "token", "sk-fake-token-1234567890")
assert out == {"set": True, "hint": "…7890"}, out
argv, stdin_in, env = calls[-1]
assert "sk-fake-token-1234567890" not in " ".join(argv), "the secret leaked onto argv"
assert not env or "sk-fake-token-1234567890" not in json.dumps(env), "the secret leaked into env"
assert stdin_in and "sk-fake-token-1234567890" in stdin_in, "the secret must reach the integration on stdin"
assert _kc[("podium-integration-fake", "token")] == "sk-fake-token-1234567890", \
    "stored under the per-integration Keychain service"

result = integrations.call("fake", "auth.check", {})
assert result["ok"] and result["data"]["secrets"] == {"token": "sk-fake-token-1234567890"}, result

rows = integrations.rows()
frow = next(r for r in rows if r["name"] == "fake")
assert frow["secrets"][0]["set"] is True and frow["secrets"][0]["hint"] == "…7890", frow
assert "sk-fake-token-1234567890" not in json.dumps(rows), "a secret value must never come back to a caller"
print("secrets on stdin, never argv/env; hint-only rows: ok")

# ================================================================
# auth.check failure stores nothing
# ================================================================
write_fake("import json, sys\n"
          "json.loads(sys.stdin.read())\n"
          "print(json.dumps({'ok': False, 'error': 'bad token'}))\n")
_kc.clear()
try:
    integrations.secret_set("fake", "token", "sk-another-secret-000000")
    raise AssertionError("a failed auth.check must not store the secret")
except ValueError as e:
    assert "bad token" in str(e), e
assert not _kc, "nothing stored after a failed check"
print("auth.check failure stores nothing: ok")

# restore an echoing fake for the deploy tests below
write_fake("import json, sys\n"
          "d = json.loads(sys.stdin.read())\n"
          "print(json.dumps({'ok': True, 'data': d}))\n")
integrations._run = real_run

# ================================================================
# the deploy:promote gate blocks deploy_action; the provider is not called
# ================================================================
repo = new_repo()
sha1 = head_sha(repo)
open(os.path.join(repo, "b.txt"), "w").write("two\n")
subprocess.run(["git", "-C", repo, "add", "b.txt"], check=True, capture_output=True)
subprocess.run(["git", "-C", repo, "commit", "-qm", "feat: second"], check=True, capture_output=True)
sha2 = head_sha(repo)

guardrails.compile_rail(
    repo, {"id": "no-go", "phase": "release", "title": "no-go", "implemented": "", "validate": ""},
    run=lambda p: json.dumps({"kind": "script", "lang": "sh", "script": "#!/bin/sh\nexit 1\n"}))
guardrails.approve(repo, "no-go")
guardrails.set_blocking(repo, "no-go", True)
guardrails.set_binding(repo, "release", "exit", ["deploy:promote"])

integrations.set_map("fake", repo, "proj_1", "my-app")

provider_calls = []
real_call = integrations.call


def blocked_spy(name, op, args=None):
    provider_calls.append((name, op, args))
    if op == "deploy.get":
        return {"ok": True, "data": {"id": args["id"], "sha": sha2, "env": "preview", "state": "ready"}}
    if op == "deploy.list":
        return {"ok": True, "data": [{"id": "dpl_prod", "sha": sha1, "env": "production",
                                      "state": "ready", "current": True}]}
    if op in ("deploy.promote", "deploy.rollback"):
        raise AssertionError(f"the provider must not be called when the gate blocks: {op}")
    return real_call(name, op, args)


integrations.call = blocked_spy
try:
    outcome = mapui.deploy_action(repo, "fake", "promote", "dpl_2")
finally:
    integrations.call = real_call
assert outcome["blocked"] is True, outcome
assert any(r["id"] == "no-go" and r["verdict"] == "fail" for r in outcome["results"]), outcome
assert not any(c[1] in ("deploy.promote", "deploy.rollback") for c in provider_calls), provider_calls
print("deploy:promote gate blocks the action, provider not called: ok")

# ---- and the mirror case: a passing gate lets the provider through ----
guardrails.compile_rail(
    repo, {"id": "no-go", "phase": "release", "title": "no-go", "implemented": "", "validate": ""},
    run=lambda p: json.dumps({"kind": "script", "lang": "sh", "script": "#!/bin/sh\nexit 0\n"}))
guardrails.approve(repo, "no-go")

allowed_calls = []


def allowed_spy(name, op, args=None):
    allowed_calls.append((name, op, args))
    if op == "deploy.get":
        return {"ok": True, "data": {"id": args["id"], "sha": sha2, "env": "preview", "state": "ready"}}
    if op == "deploy.list":
        return {"ok": True, "data": [{"id": "dpl_prod", "sha": sha1, "env": "production",
                                      "state": "ready", "current": True}]}
    if op == "deploy.promote":
        return {"ok": True, "data": {"promoted": args["id"]}}
    return real_call(name, op, args)


integrations.call = allowed_spy
try:
    outcome = mapui.deploy_action(repo, "fake", "promote", "dpl_2")
finally:
    integrations.call = real_call
assert outcome["blocked"] is False, outcome
assert any(c[1] == "deploy.promote" for c in allowed_calls), "a passing gate must let the provider through"
print("a passing gate lets promote through: ok")

# ================================================================
# provenance attached when the sha exists locally, null otherwise
# ================================================================
def listing_spy(name, op, args=None):
    if op == "deploy.list":
        return {"ok": True, "data": [
            {"id": "dpl_a", "sha": sha1, "env": "production", "state": "ready", "current": True},
            {"id": "dpl_b", "sha": "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
             "env": "preview", "state": "ready", "current": False}]}
    return real_call(name, op, args)


integrations.call = listing_spy
try:
    deploys = mapui.deploy_rows(repo)["deploys"]
finally:
    integrations.call = real_call
row_a = next(r for r in deploys if r["sha"] == sha1)
row_b = next(r for r in deploys if r["sha"] != sha1)
assert row_a["provenance"] is not None, "a sha that exists locally gets provenance"
assert row_b["provenance"] is None, "a sha that doesn't exist locally gets none"
print("provenance attached when the sha exists: ok")

# ================================================================
# Vercel adapter: normalisation against canned API JSON, no network
# ================================================================
VERCEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "integrations", "vercel", "vercel.py")
spec = importlib.util.spec_from_file_location("vercel_adapter_under_test", VERCEL_PATH)
vercel_adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vercel_adapter)


def _no_network(*a, **k):
    raise AssertionError("the Vercel adapter must never touch the network in this test")


vercel_adapter._urlopen = _no_network

canned_newest_prod = {
    "uid": "dpl_1", "url": "myapp.vercel.app", "readyState": "READY", "target": "production",
    "readySubstate": "PROMOTED",
    "meta": {"githubCommitSha": "abc123", "githubCommitRef": "main",
             "githubCommitMessage": "fix: thing", "githubCommitAuthorName": "dan"},
    "created": 2000, "ready": 2100,
}
canned_older_prod = {**canned_newest_prod, "uid": "dpl_0", "created": 500, "ready": 600}
canned_preview = {"uid": "dpl_2", "url": "preview.vercel.app", "readyState": "BUILDING",
                  "target": None, "meta": {}, "created": 3000, "ready": None}
canned_blocked = {"uid": "dpl_3", "url": "x.vercel.app", "readyState": "BLOCKED",
                  "target": "production", "meta": {}, "created": 4000, "ready": None}

row = vercel_adapter._norm_deploy(canned_newest_prod)
assert row["id"] == "dpl_1" and row["url"] == "myapp.vercel.app"
assert row["state"] == "ready" and row["env"] == "production"
assert row["sha"] == "abc123" and row["branch"] == "main"
assert row["message"] == "fix: thing" and row["author"] == "dan"
assert row["current"] is False, "current is only set by _mark_current, over a whole list"

preview_row = vercel_adapter._norm_deploy(canned_preview)
assert preview_row["state"] == "building" and preview_row["env"] == "preview"
assert preview_row["sha"] == "", "no git meta on this one -- fields degrade to empty, not KeyError"

blocked_row = vercel_adapter._norm_deploy(canned_blocked)
assert blocked_row["state"] == "error", "BLOCKED maps into this build's 5-state enum as error"

rows = [vercel_adapter._norm_deploy(d) for d in
       (canned_newest_prod, canned_older_prod, canned_preview, canned_blocked)]
vercel_adapter._mark_current(rows)
current = [r for r in rows if r["current"]]
assert len(current) == 1 and current[0]["id"] == "dpl_1", \
    "the newest READY production deployment is current, absent a cheaper signal"

# an op dispatch with no token configured raises before ever building a request
try:
    vercel_adapter._op_auth_check({}, {}, {})
    raise AssertionError("no token should have raised")
except vercel_adapter.ApiError as e:
    assert "token" in str(e)

print("Vercel adapter normalisation: ok")

# ---- boss review: hostile manifest keys and ids are refused ----
assert integrations._declared_secret_keys({"secrets": [{"key": 'tok" -w x'}, {"key": "token"}]}) == ["token"], \
    "a secret key with a quote never reaches the security -i command line"
assert not integrations._ID_RE.match("../v2/user") and integrations._ID_RE.match("dpl_8fH2k"), "ids are plain"

print("ok")
