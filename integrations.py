#!/usr/bin/env python3
"""The plug-in contract for external services midiAI talks to -- Vercel is
the first (integrations/vercel/), Sentry/AWS/Telemetry come later.

An integration is a folder with manifest.json + one executable (`run`):
    {"name": "vercel", "title": "Vercel", "run": "vercel.py",
     "secrets": [{"key": "token", "label": "Access token", "help": "..."}],
     "config": [{"key": "team", "label": "Team id (optional)"}],
     "capabilities": ["auth.check", "resources.list", "deploy.list", ...]}

Built-ins ship in the repo at integrations/<name>/; the user's own go in
~/.midiai/integrations/<name>/, which overrides a built-in of the same name.
Both the folder name and manifest["name"] must match the slug
^[a-z0-9-]{1,40}$ AND agree with each other -- a folder is never trusted to
say it's someone else, the same reasoning rules.py applies to plain folders.

call(name, op, args) execs `run` (a .py under sys.executable, anything else
must already be executable) with cwd = its folder, timeout 30s, stdin = one
JSON object {"op", "args", "secrets": {key: value}, "config": {...}}, stdout
= one JSON object {"ok": true, "data": ...} or {"ok": false, "error": "..."}.
Secrets never go on argv or in env -- only on that stdin payload -- and an
op not in the manifest's capabilities is refused before anything execs.

Secrets live in the macOS Keychain, service midiai-integration-<name>,
account = secret key, via the same access.keychain_* helpers access.py uses
for the Anthropic API key (factored out of it for this). The app only ever
sees {"set": bool, "hint": "...last4"}. Setting a secret runs auth.check
first when the integration declares that capability; a failed check stores
nothing.

Non-secret state -- per-integration config, and which resource each checkout
maps to -- lives outside the repo at ~/.midiai/integrations.json:
    {name: {"config": {...}, "map": {"<checkout root>": {"resource": "<id>",
                                                          "label": "<name>"}}}}

    python3 integrations.py        # self-check, no Keychain or network touched
"""
import json
import os
import re
import subprocess
import sys
import threading
import time

import access

BUILTIN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "integrations")
USER_DIR = os.path.expanduser("~/.midiai/integrations")
STATE_FILE = os.path.expanduser("~/.midiai/integrations.json")

_NAME_RE = re.compile(r"^[a-z0-9-]{1,40}$")
# same shape access.py holds a raw secret to before it goes anywhere near
# `security -i`
_SECRET_RE = re.compile(r"^[A-Za-z0-9_.\-]{8,400}$")

CALL_TIMEOUT = 30
AUTH_TTL = 60

_run = subprocess.run           # tests swap this
_auth_cache = {}
_auth_lock = threading.Lock()


def _kc_service(name):
    return f"midiai-integration-{name}"


# -------------------------------------------------------------- discovery

def _load_manifest_file(path):
    try:
        with open(path) as f:
            m = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return m if isinstance(m, dict) else None


def _resolve_run(d, run):
    """The absolute path to `run`, refusing anything that isn't a plain
    filename resolving inside `d` -- a manifest is data from a folder that
    could be someone's `~/.midiai/integrations/`, never trusted with `..` or
    an absolute path."""
    if not run or not isinstance(run, str) or run.startswith("/") or ".." in run.split("/"):
        return None
    real_dir = os.path.realpath(d)
    real_run = os.path.realpath(os.path.join(d, run))
    if real_run != real_dir and not real_run.startswith(real_dir + os.sep):
        return None
    if not os.path.isfile(real_run):
        return None
    if not real_run.endswith(".py") and not os.access(real_run, os.X_OK):
        return None
    return real_run


def discover():
    """name -> {"dir", "builtin", "manifest", "run_path"}. Iterates built-ins
    first, then the user's own, so a user folder of the same name overrides
    one that ships in the repo."""
    found = {}
    for base, builtin in ((BUILTIN_DIR, True), (USER_DIR, False)):
        if not os.path.isdir(base):
            continue
        for entry in sorted(os.listdir(base)):
            if not _NAME_RE.match(entry):
                continue
            d = os.path.join(base, entry)
            if not os.path.isdir(d):
                continue
            manifest = _load_manifest_file(os.path.join(d, "manifest.json"))
            if not manifest or manifest.get("name") != entry:
                continue
            run_path = _resolve_run(d, manifest.get("run") or "")
            if not run_path:
                continue
            found[entry] = {"dir": d, "builtin": builtin, "manifest": manifest,
                            "run_path": run_path}
    return found


# ------------------------------------------------------------------ state

def _load_state():
    try:
        with open(STATE_FILE) as f:
            d = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return d if isinstance(d, dict) else {}


def _save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_FILE)


def get_config(name):
    return dict((_load_state().get(name) or {}).get("config") or {})


def set_config(name, config):
    if name not in discover():
        raise ValueError("no such integration")
    if not isinstance(config, dict):
        raise ValueError("config must be an object")
    state = _load_state()
    entry = state.setdefault(name, {})
    entry["config"] = {str(k): str(v) for k, v in config.items()}
    _save_state(state)
    return entry["config"]


def get_map(name, cwd):
    return (_load_state().get(name) or {}).get("map", {}).get(cwd)


def get_all_maps(name):
    return dict((_load_state().get(name) or {}).get("map") or {})


def set_map(name, cwd, resource, label):
    if name not in discover():
        raise ValueError("no such integration")
    state = _load_state()
    entry = state.setdefault(name, {})
    mp = entry.setdefault("map", {})
    if resource is None:
        mp.pop(cwd, None)
    else:
        mp[cwd] = {"resource": str(resource), "label": str(label or "")}
    _save_state(state)
    return entry.get("map", {})


def mapped_for(cwd):
    """Every (name, mapping) pair mapped to this checkout, across every
    known integration -- what /deploy iterates to build the DEPLOY tab."""
    out = []
    for name in discover():
        m = get_map(name, cwd)
        if m:
            out.append((name, m))
    return out


# ---------------------------------------------------------------- secrets

def secret_hint(name, key):
    val = access.keychain_get(_kc_service(name), key)
    if val is None:
        return None
    return f"…{val[-4:]}" if len(val) >= 4 else f"…{val}"


# A secret's key becomes a Keychain account name inside a `security -i`
# command line, and it comes from a manifest anyone can drop into
# ~/.midiai/integrations/ -- so it is held to a plain identifier, or a
# quote in it could smuggle a second Keychain command in.
_SECRET_KEY_RE = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")


def _declared_secret_keys(manifest):
    return [s.get("key") for s in (manifest.get("secrets") or [])
            if isinstance(s, dict) and isinstance(s.get("key"), str)
            and _SECRET_KEY_RE.match(s.get("key"))]


def _all_secrets(name, manifest, override=None):
    out = {}
    for key in _declared_secret_keys(manifest):
        v = (override or {}).get(key)
        if v is None:
            v = access.keychain_get(_kc_service(name), key)
        if v is not None:
            out[key] = v
    return out


def secret_set(name, key, value):
    """Validate the shape, run auth.check first when the integration has
    that capability (a failed check stores nothing), then store via the
    Keychain's stdin form. Returns {"set": True, "hint": "...last4"}."""
    integ = discover().get(name)
    if not integ:
        raise ValueError("no such integration")
    manifest = integ["manifest"]
    if key not in _declared_secret_keys(manifest):
        raise ValueError("not a secret this integration declares")
    value = str(value or "")
    if not _SECRET_RE.match(value):
        raise ValueError("that doesn't look like a valid secret")
    if "auth.check" in (manifest.get("capabilities") or []):
        secrets = _all_secrets(name, manifest, override={key: value})
        result = _exec(integ, "auth.check", {}, secrets, get_config(name))
        if not result.get("ok"):
            raise ValueError(result.get("error") or "the integration rejected it")
    if not access.keychain_set(_kc_service(name), key, value, label=f"midiAI {name} {key}"):
        raise RuntimeError("the Keychain would not take it")
    forget_auth(name)
    return {"set": True, "hint": f"…{value[-4:]}"}


def secret_delete(name, key):
    access.keychain_delete(_kc_service(name), key)
    forget_auth(name)


# -------------------------------------------------------------- execution

def _exec(integ, op, args, secrets, config):
    run_path = integ["run_path"]
    argv = [sys.executable, run_path] if run_path.endswith(".py") else [run_path]
    payload = json.dumps({"op": op, "args": args or {}, "secrets": secrets, "config": config})
    try:
        out = _run(argv, input=payload, capture_output=True, text=True,
                   timeout=CALL_TIMEOUT, cwd=integ["dir"])
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timed out"}
    except OSError as e:
        return {"ok": False, "error": str(e)[:300]}
    try:
        parsed = json.loads(out.stdout or "{}")
    except json.JSONDecodeError:
        return {"ok": False, "error": (out.stderr or "bad response from the integration")[:300]}
    if not isinstance(parsed, dict) or "ok" not in parsed:
        return {"ok": False, "error": "malformed response from the integration"}
    return parsed


def call(name, op, args=None):
    """Exec one op against one integration. An op the manifest doesn't
    declare in "capabilities" is refused before anything runs."""
    integ = discover().get(name)
    if not integ:
        return {"ok": False, "error": "no such integration"}
    manifest = integ["manifest"]
    if op not in (manifest.get("capabilities") or []):
        return {"ok": False, "error": f"{name} does not support {op}"}
    # ids arrive from the app and adapters put them in URL paths: a
    # "../" in one would aim an authenticated request somewhere else
    for k in ("id", "resource"):
        v = (args or {}).get(k)
        if v is not None and not _ID_RE.match(str(v)):
            return {"ok": False, "error": f"not a valid {k}"}
    secrets = _all_secrets(name, manifest)
    return _exec(integ, op, args or {}, secrets, get_config(name))


# --------------------------------------------------------- cached auth.check

def auth_status(name):
    """{"ok": bool|None, "error": str|None} -- None/None when the
    integration has no auth.check capability or no secret is set yet
    (nothing to check). Cached for AUTH_TTL seconds: /integrations calls
    this for every row on every poll."""
    integ = discover().get(name)
    if not integ:
        return {"ok": None, "error": None}
    manifest = integ["manifest"]
    if "auth.check" not in (manifest.get("capabilities") or []):
        return {"ok": None, "error": None}
    keys = _declared_secret_keys(manifest)
    if keys and not any(access.keychain_exists(_kc_service(name), k) for k in keys):
        return {"ok": None, "error": None}
    with _auth_lock:
        cached = _auth_cache.get(name)
        if cached and time.time() - cached["at"] < AUTH_TTL:
            return cached["result"]
    result = call(name, "auth.check", {})
    got = {"ok": bool(result.get("ok")), "error": None if result.get("ok") else result.get("error")}
    with _auth_lock:
        _auth_cache[name] = {"at": time.time(), "result": got}
    return got


def forget_auth(name=None):
    with _auth_lock:
        if name is None:
            _auth_cache.clear()
        else:
            _auth_cache.pop(name, None)


# --------------------------------------------------------------------- rows

def rows():
    """One row per discovered integration, for GET /integrations."""
    out = []
    for name, integ in sorted(discover().items()):
        manifest = integ["manifest"]
        secrets = []
        for s in manifest.get("secrets") or []:
            key = s.get("key")
            if not key:
                continue
            is_set = access.keychain_exists(_kc_service(name), key)
            secrets.append({"key": key, "label": s.get("label", key), "help": s.get("help", ""),
                            "set": is_set, "hint": secret_hint(name, key) if is_set else None})
        config_vals = get_config(name)
        config = [{"key": c.get("key"), "label": c.get("label", c.get("key")),
                   "value": config_vals.get(c.get("key"), "")}
                  for c in manifest.get("config") or [] if c.get("key")]
        status = auth_status(name)
        out.append({"name": name, "title": manifest.get("title", name), "builtin": integ["builtin"],
                    "capabilities": list(manifest.get("capabilities") or []), "secrets": secrets,
                    "config": config, "ok": status["ok"], "error": status["error"],
                    # {checkout root: {resource, label}} -- the app reads its
                    # own project's entry (roots are git top levels, as stored)
                    "map": dict(_load_state().get(name, {}).get("map") or {})})
    return out


if __name__ == "__main__":
    import shutil
    import tempfile

    tmp = tempfile.mkdtemp()
    try:
        BUILTIN_DIR = os.path.join(tmp, "integrations")
        USER_DIR = os.path.join(tmp, "user-integrations")
        STATE_FILE = os.path.join(tmp, "integrations.json")
        os.makedirs(os.path.join(BUILTIN_DIR, "fake"))

        manifest = {"name": "fake", "title": "Fake", "run": "fake.py",
                    "secrets": [{"key": "token", "label": "Token"}],
                    "config": [{"key": "team", "label": "Team"}],
                    "capabilities": ["auth.check", "deploy.list"]}
        with open(os.path.join(BUILTIN_DIR, "fake", "manifest.json"), "w") as f:
            json.dump(manifest, f)
        with open(os.path.join(BUILTIN_DIR, "fake", "fake.py"), "w") as f:
            f.write("import json, sys\n"
                   "d = json.loads(sys.stdin.read())\n"
                   "print(json.dumps({'ok': True, 'data': d}))\n")

        # discovery finds it, and a user folder of the same name overrides it
        assert "fake" in discover()
        assert discover()["fake"]["builtin"] is True
        os.makedirs(os.path.join(USER_DIR, "fake"))
        with open(os.path.join(USER_DIR, "fake", "manifest.json"), "w") as f:
            json.dump(manifest, f)
        with open(os.path.join(USER_DIR, "fake", "fake.py"), "w") as f:
            f.write(open(os.path.join(BUILTIN_DIR, "fake", "fake.py")).read())
        assert discover()["fake"]["builtin"] is False, "user folder overrides the built-in"
        shutil.rmtree(os.path.join(USER_DIR, "fake"))

        # run containment: a manifest pointing outside its own folder is dropped
        os.makedirs(os.path.join(BUILTIN_DIR, "escapee"))
        with open(os.path.join(BUILTIN_DIR, "escapee", "manifest.json"), "w") as f:
            json.dump({"name": "escapee", "run": "../fake/fake.py", "capabilities": []}, f)
        assert "escapee" not in discover(), "run must resolve inside its own folder"
        shutil.rmtree(os.path.join(BUILTIN_DIR, "escapee"))

        # an op not in capabilities is refused before exec
        got = call("fake", "deploy.promote", {})
        assert got == {"ok": False, "error": "fake does not support deploy.promote"}, got

        # secrets on stdin, never argv or env -- the fake script echoes what it saw
        calls = []
        real_run = subprocess.run
        def spy(argv, **k):
            calls.append((argv, k.get("input"), k.get("env")))
            return real_run(argv, **k)
        _run = spy
        _saved = {}
        access.keychain_exists = lambda s, a: a in _saved.get(s, {})
        access.keychain_get = lambda s, a: _saved.get(s, {}).get(a)
        def fake_set(s, a, v, label=""):
            _saved.setdefault(s, {})[a] = v
            return True
        access.keychain_set = fake_set
        access.keychain_delete = lambda s, a: _saved.get(s, {}).pop(a, None)

        out = secret_set("fake", "token", "sk-fake-secret-1234567890")
        assert out["set"] and out["hint"] == "…7890", out
        argv, stdin_in, env = calls[-1]
        assert "sk-fake-secret-1234567890" not in " ".join(argv), "secret leaked onto argv"
        assert not env or "sk-fake-secret-1234567890" not in json.dumps(env), "secret leaked into env"
        assert "sk-fake-secret-1234567890" in stdin_in, "secret must reach the integration on stdin"

        result = call("fake", "auth.check", {})
        assert result["ok"], result
        seen = result["data"]
        assert seen["secrets"] == {"token": "sk-fake-secret-1234567890"}
        assert seen["op"] == "auth.check"

        # a failed auth.check stores nothing
        with open(os.path.join(BUILTIN_DIR, "fake", "fake.py"), "w") as f:
            f.write("import json, sys\n"
                   "json.loads(sys.stdin.read())\n"
                   "print(json.dumps({'ok': False, 'error': 'nope'}))\n")
        _saved.clear()
        try:
            secret_set("fake", "token", "sk-another-secret-000000")
            raise AssertionError("a failed auth.check must not store the secret")
        except ValueError as e:
            assert "nope" in str(e)
        assert not _saved, "nothing stored after a failed check"

        print("integrations.py: ok")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
