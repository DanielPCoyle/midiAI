#!/usr/bin/env python3
"""How the Claude Code agents midiAI starts get to a model.

Settings > Claude in the app. Four ways in: the claude.ai subscription the
CLI is already logged into (nothing to add), an Anthropic API key, Bedrock,
or Vertex -- plus a default model and effort. All of it reaches an agent as
flags on its own `claude` command line (`launch_flags`, called by term.py for
every agent it starts), so it applies to midiAI's agents only and never
rewrites ~/.claude/settings.json under the terminal you use by hand.

The API key lives in the macOS login Keychain and nowhere else. The agent
gets it through `apiKeyHelper`, a command Claude Code runs to fetch it, so
the key is never on a command line, in an environment variable, in a file,
or sent back to the app -- only its last four characters are kept, as a hint.

    python3 access.py        # self-check, no Keychain or network touched
"""
import json
import os
import re
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.request

CONFIG_FILE = os.path.expanduser("~/.midiai/claude-access.json")
KC_SERVICE = "midiai-anthropic-api-key"
KC_ACCOUNT = "midiai"
SECURITY = "/usr/bin/security"

MODES = ("subscription", "api_key", "bedrock", "vertex")
EFFORTS = ("", "low", "medium", "high", "xhigh", "max")
# model ids and aliases: claude-opus-5-5, opus, claude-sonnet-5[1m]
_MODEL_RE = re.compile(r"^[A-Za-z0-9._\[\]-]{0,80}$")
_FIELD_RE = re.compile(r"^[A-Za-z0-9._-]{0,64}$")      # region, profile, project
# Checked before it goes anywhere near `security -i`: the key is the one
# value here typed by a person and passed to a command interpreter.
_KEY_RE = re.compile(r"^sk-ant-[A-Za-z0-9_-]{20,200}$")

_run = subprocess.run          # tests swap these
_urlopen = urllib.request.urlopen


def _defaults():
    return {"mode": "subscription", "model": "", "effort": "",
            "bedrock": {"region": "", "profile": ""},
            "vertex": {"region": "", "project": ""},
            "hint": ""}


def load():
    got = _defaults()
    try:
        with open(CONFIG_FILE) as f:
            disk = json.load(f)
    except (OSError, json.JSONDecodeError):
        return got
    if isinstance(disk, dict):
        got.update(clean({**got, **disk}))
        got["hint"] = str(disk.get("hint") or "")[:40]
    return got


def _save(cfg):
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, CONFIG_FILE)


def clean(patch):
    """Only the fields this file knows, each held to its own shape. Anything
    else a client sends is dropped: these values end up on a command line."""
    out = {}
    if patch.get("mode") in MODES:
        out["mode"] = patch["mode"]
    if "model" in patch and _MODEL_RE.match(str(patch["model"] or "")):
        out["model"] = str(patch["model"] or "")
    if "effort" in patch and (patch["effort"] or "") in EFFORTS:
        out["effort"] = patch["effort"] or ""
    for group, keys in (("bedrock", ("region", "profile")), ("vertex", ("region", "project"))):
        g = patch.get(group)
        if isinstance(g, dict):
            vals = {k: str(g.get(k) or "") for k in keys}
            if all(_FIELD_RE.match(v) for v in vals.values()):
                out[group] = vals
    return out


def update(patch):
    cfg = load()
    bad = [k for k in ("mode", "model", "effort", "bedrock", "vertex")
           if k in patch and k not in clean(patch)]
    if bad:
        raise ValueError(f"not a valid value for: {', '.join(bad)}")
    cfg.update(clean(patch))
    _save(cfg)
    return cfg


# ---------------------------------------------------------------- the key

def keychain_exists(service, account):
    """Is there an item at (service, account)? Without -w, find-generic-password
    prints the item's attributes and not the secret: this asks "is it there",
    it never reads it. Shared by access.py's own key and integrations.py's
    per-integration secrets."""
    out = _run([SECURITY, "find-generic-password", "-s", service, "-a", account],
               capture_output=True, text=True, timeout=10)
    return out.returncode == 0


def keychain_get(service, account):
    """The secret at (service, account), or None. Never logged, never
    returned to a caller that turns around and sends it anywhere but the
    provider it's for."""
    out = _run([SECURITY, "find-generic-password", "-s", service, "-a", account, "-w"],
               capture_output=True, text=True, timeout=10)
    if out.returncode != 0:
        return None
    return out.stdout.rstrip("\n")


def keychain_set(service, account, value, label=""):
    """Store `value` via `security -i` on stdin -- never on argv, where a
    process listing would show it. Returns whether the Keychain took it."""
    out = _run([SECURITY, "-i"], input=(
        f'add-generic-password -U -s {service} -a {account} '
        f'-l "{label}" -w "{value}"\n'),
        capture_output=True, text=True, timeout=15)
    return out.returncode == 0 and keychain_exists(service, account)


def keychain_delete(service, account):
    _run([SECURITY, "delete-generic-password", "-s", service, "-a", account],
         capture_output=True, text=True, timeout=10)


def key_saved():
    return keychain_exists(KC_SERVICE, KC_ACCOUNT)


def _tls():
    """The python.org build ships with no CA bundle until someone runs its
    "Install Certificates" script, so HTTPS fails with CERTIFICATE_VERIFY_FAILED
    where curl works. macOS's own bundle is at /etc/ssl/cert.pem."""
    ctx = ssl.create_default_context()
    if not ctx.get_ca_certs() and os.path.exists("/etc/ssl/cert.pem"):
        ctx = ssl.create_default_context(cafile="/etc/ssl/cert.pem")
    return ctx


def verify_key(key):
    """Ask Anthropic whether the key works, before keeping it. A key that is
    typed wrong would otherwise fail in the next agent that starts, far from
    the field it was typed into."""
    req = urllib.request.Request("https://api.anthropic.com/v1/models?limit=1", headers={
        "x-api-key": key, "anthropic-version": "2023-06-01"})
    try:
        with _urlopen(req, timeout=15, context=_tls()) as r:
            return r.status == 200
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise ValueError("Anthropic rejected this key") from None
        raise ValueError(f"Anthropic answered {e.code}; not saved") from None
    except (urllib.error.URLError, OSError):
        raise ValueError("couldn't reach Anthropic to check the key; not saved") from None


def save_key(key):
    key = (key or "").strip()
    if not _KEY_RE.match(key):
        raise ValueError("that doesn't look like an Anthropic API key (sk-ant-…)")
    verify_key(key)
    if not keychain_set(KC_SERVICE, KC_ACCOUNT, key, label="midiAI Anthropic API key"):
        raise RuntimeError("the Keychain would not take it")
    cfg = load()
    cfg["hint"] = f"{key[:7]}…{key[-4:]}"
    _save(cfg)


def delete_key():
    keychain_delete(KC_SERVICE, KC_ACCOUNT)
    cfg = load()
    cfg["hint"] = ""
    _save(cfg)


# ------------------------------------------------------------ what agents get

def launch_flags(cfg=None, have_key=None):
    """Extra `claude` flags for an agent midiAI starts. Empty for the
    subscription with no defaults, so an untouched install launches exactly
    as it always has."""
    cfg = cfg or load()
    settings, env = {}, {}
    mode = cfg.get("mode")
    if mode == "api_key" and (key_saved() if have_key is None else have_key):
        settings["apiKeyHelper"] = (f"{SECURITY} find-generic-password "
                                    f"-s {KC_SERVICE} -a {KC_ACCOUNT} -w")
    elif mode == "bedrock":
        b = cfg.get("bedrock") or {}
        env["CLAUDE_CODE_USE_BEDROCK"] = "1"
        if b.get("region"):
            env["AWS_REGION"] = b["region"]
        if b.get("profile"):
            env["AWS_PROFILE"] = b["profile"]
    elif mode == "vertex":
        v = cfg.get("vertex") or {}
        env["CLAUDE_CODE_USE_VERTEX"] = "1"
        if v.get("region"):
            env["CLOUD_ML_REGION"] = v["region"]
        if v.get("project"):
            env["ANTHROPIC_VERTEX_PROJECT_ID"] = v["project"]
    if env:
        settings["env"] = env
    flags = ["--settings", json.dumps(settings)] if settings else []
    if cfg.get("model"):
        flags += ["--model", cfg["model"]]
    if cfg.get("effort"):
        flags += ["--effort", cfg["effort"]]
    return flags


def with_access(argv, flags=None):
    """argv with launch_flags added when it starts a claude session. Only a
    bare `claude [flags]` -- `claude agents`, `claude mcp …`, `claude auth …`
    are subcommands, and settings flags mean nothing (or worse) to them.

    `flags`, when given, is used instead of this file's own launch_flags() --
    engines.py's claude launch path already worked out the flags for the
    engine's chosen provider and passes them straight through, so they are
    not computed a second time from this file's own (possibly different)
    config."""
    if not argv or os.path.basename(argv[0]) != "claude":
        return list(argv)
    if len(argv) > 1 and not argv[1].startswith("-"):
        return list(argv)
    try:
        return [argv[0], *(flags if flags is not None else launch_flags()), *argv[1:]]
    except Exception:
        return list(argv)       # a broken settings file must not stop an agent starting


# ---------------------------------------------------------------- status

_auth = {"at": 0.0, "got": None}
_auth_lock = threading.Lock()
AUTH_TTL = 30


def auth_status():
    """`claude auth status`, cached: it is a Node CLI start (~1s), and the
    settings panel asks every time it opens."""
    with _auth_lock:
        if _auth["got"] is not None and time.time() - _auth["at"] < AUTH_TTL:
            return _auth["got"]
        try:
            out = _run(["claude", "auth", "status", "--json"],
                       capture_output=True, text=True, timeout=20)
            d = json.loads(out.stdout or "{}")
            got = {k: d.get(k) for k in ("loggedIn", "authMethod", "apiProvider",
                                         "email", "subscriptionType")}
        except Exception as e:
            got = {"error": str(e)[:200]}
        _auth.update(at=time.time(), got=got)
        return got


def forget_auth():
    with _auth_lock:
        _auth["got"] = None


def state():
    cfg = load()
    saved = key_saved()
    return {"mode": cfg["mode"], "model": cfg["model"], "effort": cfg["effort"],
            "bedrock": cfg["bedrock"], "vertex": cfg["vertex"],
            "key": {"saved": saved, "hint": cfg["hint"] if saved else ""},
            "auth": auth_status()}


def login():
    """`claude auth login` wants a browser and a terminal; open one on the
    Mac, the same way the folder picker is the Mac's own Finder dialog."""
    script = ('tell application "Terminal"\n  activate\n'
              '  do script "claude auth login"\nend tell')
    out = _run(["osascript", "-e", script], capture_output=True, text=True, timeout=20)
    forget_auth()
    if out.returncode != 0:
        raise RuntimeError((out.stderr or "couldn't open Terminal").strip()[-200:])


if __name__ == "__main__":
    import tempfile
    CONFIG_FILE = os.path.join(tempfile.mkdtemp(), "access.json")

    # untouched: agents launch exactly as before
    assert launch_flags(_defaults(), have_key=False) == []
    assert with_access(["claude", "agents", "--json"]) == ["claude", "agents", "--json"]
    assert with_access(["bash"]) == ["bash"]

    # api key: a helper that reads the Keychain -- the key is not in the flags
    cfg = {**_defaults(), "mode": "api_key", "model": "opus", "effort": "high"}
    f = launch_flags(cfg, have_key=True)
    s = json.loads(f[1])
    assert f[0] == "--settings" and "find-generic-password" in s["apiKeyHelper"], f
    assert f[2:] == ["--model", "opus", "--effort", "high"], f
    assert launch_flags(cfg, have_key=False) == ["--model", "opus", "--effort", "high"], \
        "api key mode with no key saved falls back to the subscription"

    # bedrock / vertex go in as settings env, only the fields that are set
    s = json.loads(launch_flags({**_defaults(), "mode": "bedrock",
                                 "bedrock": {"region": "us-east-1", "profile": ""}})[1])
    assert s["env"] == {"CLAUDE_CODE_USE_BEDROCK": "1", "AWS_REGION": "us-east-1"}, s
    s = json.loads(launch_flags({**_defaults(), "mode": "vertex",
                                 "vertex": {"region": "us-east5", "project": "p1"}})[1])
    assert s["env"]["ANTHROPIC_VERTEX_PROJECT_ID"] == "p1", s

    # values bound for a command line are held to their shape
    try:
        update({"model": "opus; rm -rf ~"})
        raise AssertionError("a model with shell in it was accepted")
    except ValueError:
        pass
    try:
        update({"bedrock": {"region": "$(id)", "profile": ""}})
        raise AssertionError("a region with shell in it was accepted")
    except ValueError:
        pass
    assert update({"mode": "vertex", "effort": "max"})["mode"] == "vertex"
    assert load()["effort"] == "max"

    # a key is shape-checked, verified, then stored via stdin -- never argv
    calls = []
    _run = lambda argv, **k: (calls.append((argv, k.get("input"))),
                              subprocess.CompletedProcess(argv, 0, "", ""))[1]
    _urlopen = lambda req, timeout, context=None: type("R", (), {"status": 200, "__enter__": lambda s: s,
                                                   "__exit__": lambda s, *a: None})()
    try:
        save_key("not-a-key")
        raise AssertionError("a malformed key was accepted")
    except ValueError:
        pass
    good = "sk-ant-api03-" + "x" * 40 + "abcd"
    save_key(good)
    stored = [c for c in calls if c[0][:2] == [SECURITY, "-i"]]
    assert stored and good in stored[0][1], "stored through stdin"
    assert all(good not in " ".join(c[0]) for c in calls), "the key is never on argv"
    assert load()["hint"] == "sk-ant-…abcd", load()["hint"]

    def rejected(req, timeout, context=None):
        raise urllib.error.HTTPError(req.full_url, 401, "no", {}, None)
    _urlopen = rejected
    calls.clear()
    try:
        save_key(good)
        raise AssertionError("a key Anthropic rejected was saved")
    except ValueError as e:
        assert "rejected" in str(e)
    assert not [c for c in calls if c[0][:2] == [SECURITY, "-i"]], "nothing stored"
    print("access.py: ok")
