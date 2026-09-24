"""engines.py: the providers.json store, migration from the old
~/.midiai/claude-access.json, argv building for claude (through access.py,
not a second copy of it) and codex, and value validation. The Keychain is
mocked throughout (same in-memory dict test_integrations.py uses for
integrations.py's secrets) and every network call is faked -- this must
never touch the real ~/.midiai, the real Keychain, or call the real
codex/claude CLIs or the Anthropic/OpenAI/AWS/Google APIs.
python3 test_engines.py"""
import json
import os
import subprocess
import tempfile
import urllib.error

import access
import engines

# Local state lives outside the repo -- point both stores at scratch files,
# the same way test_guardrails.py/test_integrations.py repoint theirs.
access.CONFIG_FILE = os.path.join(tempfile.mkdtemp(), "claude-access.json")
engines.STORE_FILE = os.path.join(tempfile.mkdtemp(), "providers.json")

# ---------------------------------------------------------------- fakes

_kc = {}       # (service, account) -> value, an in-memory Keychain


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


def _ok_response(*a, **k):
    class R:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
    return R()


access._urlopen = _ok_response
engines._urlopen = _ok_response
access._run = lambda argv, **k: subprocess.CompletedProcess(argv, 0, "", "")


def reset_store():
    _kc.clear()
    if os.path.exists(engines.STORE_FILE):
        os.remove(engines.STORE_FILE)
    if os.path.exists(access.CONFIG_FILE):
        os.remove(access.CONFIG_FILE)


ANTH_KEY = "sk-ant-api03-" + "x" * 40 + "abcd"
OPENAI_KEY = "sk-" + "y" * 40 + "wxyz"


# ------------------------------------------------------------- migration

reset_store()
for old_mode, want_provider in (("subscription", "anthropic"), ("api_key", "anthropic"),
                                ("bedrock", "bedrock"), ("vertex", "vertex")):
    reset_store()
    with open(access.CONFIG_FILE, "w") as f:
        json.dump({"mode": old_mode, "model": "opus", "effort": "high",
                   "bedrock": {"region": "us-east-1", "profile": "work"},
                   "vertex": {"region": "us-east5", "project": "proj-1"},
                   "hint": "sk-ant-…abcd"}, f)
    cfg = engines.load()
    assert cfg["engines"]["claude"]["provider"] == want_provider, (old_mode, cfg)
    assert cfg["engines"]["claude"]["model"] == "opus", cfg
    assert cfg["engines"]["claude"]["effort"] == "high", cfg
    assert cfg["providers"]["bedrock"] == {"region": "us-east-1", "profile": "work"}, cfg
    assert cfg["providers"]["vertex"] == {"region": "us-east5", "project": "proj-1"}, cfg
    if old_mode == "api_key":
        assert cfg["providers"]["anthropic"]["mode"] == "api_key", cfg
    # access.py's own file is read, never written, by the migration
    with open(access.CONFIG_FILE) as f:
        assert json.load(f)["mode"] == old_mode, "migration must not touch the old file"

# no old file at all -- pure defaults, no crash
reset_store()
cfg = engines.load()
assert cfg["engines"]["default"] == "claude", cfg
assert cfg["engines"]["codex"]["sandbox"] == "workspace-write", cfg
assert cfg["engines"]["codex"]["approval"] == "on-request", cfg

print("migration: ok")


# --------------------------------------------------------------- claude argv

reset_store()
# subscription, nothing configured: exactly what access.with_access([claude,
# --permission-mode, auto]) has always produced for an untouched install
argv = engines.launch_argv("claude")
assert argv == ["claude", "--permission-mode", "auto"], argv

# api_key: a saved key (through engines.save_provider_key, which is really
# access.save_key) plus the anthropic provider set to api_key mode
engines.save_provider_key("anthropic", ANTH_KEY)
engines.update({"providers": {"anthropic": {"mode": "api_key"}},
               "engines": {"claude": {"model": "opus", "effort": "high"}}})
argv = engines.launch_argv("claude")
assert "--settings" in argv, argv
settings = json.loads(argv[argv.index("--settings") + 1])
assert "find-generic-password" in settings["apiKeyHelper"], settings
assert access.KC_SERVICE in settings["apiKeyHelper"] and access.KC_ACCOUNT in settings["apiKeyHelper"]
assert ANTH_KEY not in " ".join(argv), "the key itself must never reach argv"
assert argv[-4:] == ["--model", "opus", "--effort", "high", ][-4:] or \
    ("--model" in argv and "opus" in argv), argv

# bedrock
engines.update({"engines": {"claude": {"provider": "bedrock"}}})
engines.update({"providers": {"bedrock": {"region": "us-east-1", "profile": "work"}}})
argv = engines.launch_argv("claude")
settings = json.loads(argv[argv.index("--settings") + 1])
assert settings["env"] == {"CLAUDE_CODE_USE_BEDROCK": "1", "AWS_REGION": "us-east-1",
                          "AWS_PROFILE": "work"}, settings

# vertex
engines.update({"engines": {"claude": {"provider": "vertex"}}})
engines.update({"providers": {"vertex": {"region": "us-east5", "project": "proj-1"}}})
argv = engines.launch_argv("claude")
settings = json.loads(argv[argv.index("--settings") + 1])
assert settings["env"]["CLAUDE_CODE_USE_VERTEX"] == "1", settings
assert settings["env"]["ANTHROPIC_VERTEX_PROJECT_ID"] == "proj-1", settings

# a per-launch model override does not touch the saved default
argv = engines.launch_argv("claude", {"model": "haiku"})
assert "haiku" in argv and "opus" not in argv, argv
assert engines.load()["engines"]["claude"]["model"] == "opus", "override must not persist"

print("claude argv: ok")


# --------------------------------------------------------------- codex argv

reset_store()
# openai + chatgpt (default): nothing extra, per the spec's own Facts
argv = engines.launch_argv("codex")
assert argv == ["codex", "-s", "workspace-write", "-a", "on-request"], argv

argv = engines.launch_argv("codex", {"model": "gpt-5.1"})
assert argv[:3] == ["codex", "-m", "gpt-5.1"], argv

# openai + api_key: a custom provider whose auth.command reads the Keychain
# -- never env_key, never the key itself
engines.save_provider_key("openai", OPENAI_KEY)
engines.update({"providers": {"openai": {"mode": "api_key"}}})
argv = engines.launch_argv("codex")
joined = " ".join(argv)
assert "model_provider=" in joined and "wire_api=" in joined and "responses" in joined, argv
assert "auth.command=" in joined and access.SECURITY in joined, argv
assert "auth.args=" in joined and engines.OPENAI_KC_SERVICE in joined, argv
assert OPENAI_KEY not in joined, "the OpenAI key itself must never reach argv"
assert "env_key" not in joined, "must not ask codex to read the key from an env var"

# effort: -c model_reasoning_effort=... quoted as a valid TOML string
engines.update({"engines": {"codex": {"effort": "high"}}})
argv = engines.launch_argv("codex")
i = argv.index("-c")
pair = next(a for a in argv if a.startswith("model_reasoning_effort="))
assert pair == 'model_reasoning_effort="high"', pair

# sandbox / approval, from the store and as a per-launch override
engines.update({"engines": {"codex": {"sandbox": "read-only", "approval": "never"}}})
argv = engines.launch_argv("codex")
assert argv[-4:] == ["-s", "read-only", "-a", "never"], argv
argv = engines.launch_argv("codex", {"sandbox": "danger-full-access"})
assert argv[-4:-2] == ["-s", "danger-full-access"], argv

# bedrock
engines.update({"engines": {"codex": {"provider": "bedrock"}}})
engines.update({"providers": {"bedrock": {"region": "us-west-2", "profile": "codex-role"}}})
argv = engines.launch_argv("codex")
joined = " ".join(argv)
assert "model_provider=" in joined and "amazon-bedrock" in joined, argv
assert "aws.region=" in joined and "us-west-2" in joined, argv
assert "aws.profile=" in joined and "codex-role" in joined, argv

# vertex: unverified Responses API support -- refused, not guessed
engines.update({"engines": {"codex": {"provider": "vertex"}}})
try:
    engines.launch_argv("codex")
    raise AssertionError("codex + vertex must not silently launch")
except ValueError as e:
    assert "vertex" in str(e).lower() or "Vertex" in str(e)

print("codex argv: ok")


# ------------------------------------------------------------ value validation

reset_store()
for bad_patch, path in (
    ({"engines": {"claude": {"model": "opus; rm -rf ~"}}}, "engines.claude"),
    ({"engines": {"codex": {"model": "$(id)"}}}, "engines.codex"),
    ({"engines": {"codex": {"sandbox": "full-yolo"}}}, "engines.codex"),
    ({"engines": {"codex": {"approval": "always"}}}, "engines.codex"),
    ({"engines": {"claude": {"provider": "azure"}}}, "engines.claude"),
    ({"providers": {"bedrock": {"region": "$(id)", "profile": ""}}}, "providers.bedrock"),
    ({"engines": {"default": "gemini"}}, "engines.default"),
):
    try:
        engines.update(bad_patch)
        raise AssertionError(f"accepted a bad value: {bad_patch}")
    except ValueError as e:
        assert path in str(e), (bad_patch, e)

# a key that doesn't look like one is refused before anything is stored or checked
for bad_key in ("not-a-key", "sk-ant-tooshort", ""):
    try:
        engines.save_provider_key("anthropic", bad_key)
        raise AssertionError("a malformed Anthropic key was accepted")
    except ValueError:
        pass
for bad_key in ("nope", "sk-short"):
    try:
        engines.save_provider_key("openai", bad_key)
        raise AssertionError("a malformed OpenAI key was accepted")
    except ValueError:
        pass

# a key the provider rejects (401) is never stored
def rejected(req, timeout, context=None):
    raise urllib.error.HTTPError(req.full_url, 401, "no", {}, None)


real_urlopen = engines._urlopen
engines._urlopen = rejected
try:
    try:
        engines.save_provider_key("openai", OPENAI_KEY)
        raise AssertionError("a key OpenAI rejected was saved")
    except ValueError as e:
        assert "rejected" in str(e)
    assert not _kc_exists(engines.OPENAI_KC_SERVICE, engines.OPENAI_KC_ACCOUNT), \
        "nothing stored after a failed check"
finally:
    engines._urlopen = real_urlopen

print("value validation: ok")


# --------------------------------------------------------------- codex status

real_run = engines._run
real_which = engines.shutil.which
calls = []


def fake_run(argv, **k):
    calls.append(argv)
    if argv[1:] == ["--version"]:
        return subprocess.CompletedProcess(argv, 0, "codex-cli 0.153.2\n", "")
    if argv[1:] == ["login", "status"]:
        # on stderr, as the real codex 0.153 prints it -- a fake on stdout is
        # what let "reads stdout only" pass while the real one said logged out
        return subprocess.CompletedProcess(argv, 0, "", "Logged in using ChatGPT\n")
    raise AssertionError(f"unexpected call: {argv}")


engines._run = fake_run
engines.shutil.which = lambda name: "/usr/local/bin/codex"
engines.forget_codex_status()
try:
    got = engines.codex_status()
    assert got == {"installed": True, "version": "0.153.2", "logged_in": True}, got
    engines.codex_status()
    assert len(calls) == 2, "cached within TTL, not asked again"
finally:
    engines._run = real_run
    engines.shutil.which = real_which
    engines.forget_codex_status()

# not installed: no subprocess call at all
engines.shutil.which = lambda name: None
engines.forget_codex_status()
calls.clear()

def must_not_run(argv, **k):
    raise AssertionError("codex_status must not shell out when codex is not installed")


engines._run = must_not_run
try:
    got = engines.codex_status()
    assert got == {"installed": False, "version": "", "logged_in": False}, got
finally:
    engines._run = real_run
    engines.shutil.which = real_which
    engines.forget_codex_status()

print("codex status: ok")

print("ok")
