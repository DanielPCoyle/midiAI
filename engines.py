#!/usr/bin/env python3
"""Two engines an agent Podium starts can run as -- Claude Code or Codex --
each picking one of four providers: Anthropic, OpenAI, AWS Bedrock, Google
Vertex. Settings > Providers holds credentials per provider; Settings >
Engines picks which provider (and model/effort/etc) each engine defaults to;
New agent can override the model per launch.

Store: ~/.podium/providers.json, no secrets in it --
    {"providers": {"anthropic": {"mode": "subscription"|"api_key"},
                    "openai": {"mode": "chatgpt"|"api_key"},
                    "bedrock": {"region", "profile"},
                    "vertex": {"region", "project"}},
     "engines": {"claude": {"provider", "model", "effort"},
                 "codex": {"provider", "model", "effort", "sandbox", "approval"},
                 "default": "claude"|"codex"}}

The Anthropic API key stays in access.py's existing Keychain item
(access.KC_SERVICE/KC_ACCOUNT) so a key saved before this file existed keeps
working -- this module never makes a second copy of it. The OpenAI key gets
its own item, same shape (podium-openai-api-key / podium). Bedrock and Vertex
have no secret of their own here: they authenticate through the AWS/gcloud
CLIs already on this Mac (~/.aws/*, gcloud's own credentials).

launch_argv(engine, overrides=None) is the one function a caller needs:
    engines.launch_argv("claude")                    # today's defaults
    engines.launch_argv("codex", {"model": "gpt-5.1"})

For claude it is not a second implementation of "how does a claude agent
reach a model" -- it builds the OLD cfg shape access.launch_flags/with_access
already understand (from this store's provider + engine fields) and calls
straight into access.py, so that logic exists in exactly one place. For
codex it builds a `codex` argv: `-m` for the model, `-c key=value` overrides
for the provider (a built-in id for OpenAI's ChatGPT login and for Bedrock,
a custom `model_providers.<id>` block whose `auth.command` reads the
Keychain for an OpenAI API key -- the key itself never reaches argv, env, or
this module's own return value), and `-s`/`-a` for sandbox/approval.

Vertex for Codex is UNVERIFIED and NOT offered: Codex needs the Responses
API, and every source found for Vertex's OpenAI-compatible endpoint
(cloud.google.com's OpenAI-compatibility docs, ai.google.dev's) documents
only Chat Completions -- no mention of a `/responses` surface. codex's own
config reference (learn.chatgpt.com/docs/config-file/config-reference) says
`wire_api` accepts "responses" and nothing else for a custom provider, and
lists no built-in Vertex provider the way `amazon-bedrock` is built in.
Rather than guess at an unconfirmed integration, `launch_argv("codex", ...)`
raises when the codex engine's provider is "vertex"; the value is still
accepted in storage (clean/update) so the app can show it as a disabled
option, same as a greyed-out radio button.

    python3 test_engines.py        # this file's gate; self-check lives there
"""
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request

import access

STORE_FILE = os.path.expanduser("~/.podium/providers.json")

OPENAI_KC_SERVICE = "podium-openai-api-key"
OPENAI_KC_ACCOUNT = "podium"
# OpenAI issues both "sk-..." and "sk-proj-..." keys; both start the same way.
_OPENAI_KEY_RE = re.compile(r"^sk-[A-Za-z0-9_-]{20,300}$")

CLAUDE_PROVIDERS = ("anthropic", "bedrock", "vertex")
# "vertex" is accepted here (so the store can hold it and the UI can show a
# disabled option) but launch_argv refuses it -- see the module docstring.
CODEX_PROVIDERS = ("openai", "bedrock", "vertex")
SANDBOX = ("read-only", "workspace-write", "danger-full-access")
APPROVAL = ("on-request", "never")
ENGINE_NAMES = ("claude", "codex")

_run = subprocess.run          # tests swap these
_urlopen = urllib.request.urlopen


def _defaults():
    return {
        "providers": {
            "anthropic": {"mode": "subscription", "hint": ""},
            "openai": {"mode": "chatgpt", "hint": ""},
            "bedrock": {"region": "", "profile": ""},
            "vertex": {"region": "", "project": ""},
        },
        "engines": {
            "claude": {"provider": "anthropic", "model": "", "effort": ""},
            "codex": {"provider": "openai", "model": "", "effort": "",
                      "sandbox": "workspace-write", "approval": "on-request"},
            "default": "claude",
        },
    }


def _merge(base, patch):
    """base, deep-filled from patch where patch has the same shape -- an old
    or partial providers.json still gets every key a newer version added."""
    if not isinstance(patch, dict):
        return base
    out = {}
    for k, v in base.items():
        pv = patch.get(k)
        out[k] = _merge(v, pv) if isinstance(v, dict) and isinstance(pv, dict) else \
            (pv if k in patch else v)
    return out


def _migrate_from_access():
    """~/.podium/claude-access.json -- the shape access.py has always
    written (mode/model/effort/bedrock/vertex) -- read once, when
    providers.json does not exist yet, and turned into the new shape.
    access.py's own file is never written here; it stays exactly what it
    was, since /settings/claude* still reads it directly."""
    try:
        with open(access.CONFIG_FILE) as f:
            old = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(old, dict):
        return None
    got = _defaults()
    mode = old.get("mode") if old.get("mode") in access.MODES else "subscription"
    got["engines"]["claude"]["model"] = str(old.get("model") or "")
    effort = old.get("effort") or ""
    got["engines"]["claude"]["effort"] = effort if effort in access.EFFORTS else ""
    if mode == "api_key":
        got["providers"]["anthropic"]["mode"] = "api_key"
        got["engines"]["claude"]["provider"] = "anthropic"
    elif mode in ("bedrock", "vertex"):
        got["engines"]["claude"]["provider"] = mode
    else:
        got["engines"]["claude"]["provider"] = "anthropic"
    b = old.get("bedrock")
    if isinstance(b, dict):
        got["providers"]["bedrock"] = {"region": str(b.get("region") or ""),
                                       "profile": str(b.get("profile") or "")}
    v = old.get("vertex")
    if isinstance(v, dict):
        got["providers"]["vertex"] = {"region": str(v.get("region") or ""),
                                      "project": str(v.get("project") or "")}
    return got


def _save(cfg):
    os.makedirs(os.path.dirname(STORE_FILE), exist_ok=True)
    tmp = STORE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, STORE_FILE)


def load():
    try:
        with open(STORE_FILE) as f:
            disk = json.load(f)
        if isinstance(disk, dict):
            return _merge(_defaults(), disk)
    except (OSError, json.JSONDecodeError):
        pass
    got = _migrate_from_access() or _defaults()
    _save(got)
    return got


# -------------------------------------------------------------- validation

def _clean_provider_patch(name, sub):
    if name == "anthropic":
        return {"mode": sub["mode"]} if sub.get("mode") in ("subscription", "api_key") else {}
    if name == "openai":
        return {"mode": sub["mode"]} if sub.get("mode") in ("chatgpt", "api_key") else {}
    if name in ("bedrock", "vertex"):
        keys = ("region", "profile") if name == "bedrock" else ("region", "project")
        vals = {k: str(sub.get(k) or "") for k in keys}
        return vals if all(access._FIELD_RE.match(v) for v in vals.values()) else {}
    return {}


def _clean_engine_patch(name, sub):
    out = {}
    providers = CLAUDE_PROVIDERS if name == "claude" else CODEX_PROVIDERS
    if sub.get("provider") in providers:
        out["provider"] = sub["provider"]
    if "model" in sub and access._MODEL_RE.match(str(sub["model"] or "")):
        out["model"] = str(sub["model"] or "")
    if "effort" in sub and (sub["effort"] or "") in access.EFFORTS:
        out["effort"] = sub["effort"] or ""
    if name == "codex":
        if sub.get("sandbox") in SANDBOX:
            out["sandbox"] = sub["sandbox"]
        if sub.get("approval") in APPROVAL:
            out["approval"] = sub["approval"]
    return out


def clean(patch):
    """Only the fields this store knows, each held to its own shape -- the
    same discipline access.clean applies to Claude's, because these values
    end up on a command line (codex's own `-c` overrides, or access.py's
    settings flags for claude)."""
    out = {"providers": {}, "engines": {}}
    for name, sub in (patch.get("providers") or {}).items():
        if name in ("anthropic", "openai", "bedrock", "vertex") and isinstance(sub, dict):
            got = _clean_provider_patch(name, sub)
            if got:
                out["providers"][name] = got
    eng_patch = patch.get("engines") or {}
    for name, sub in eng_patch.items():
        if name in ENGINE_NAMES and isinstance(sub, dict):
            got = _clean_engine_patch(name, sub)
            if got:
                out["engines"][name] = got
    if eng_patch.get("default") in ENGINE_NAMES:
        out["engines"]["default"] = eng_patch["default"]
    return out


def update(patch):
    """Merge {"providers": {...}, "engines": {...}} into the store. Engine
    and provider sub-objects MERGE field by field (patching just "model"
    leaves "provider"/"effort" alone) -- bedrock/vertex replace as a whole
    group, same as access.clean, since a region/profile pair is one idea."""
    if not isinstance(patch, dict):
        raise ValueError("patch must be an object")
    cleaned = clean(patch)
    bad = []
    for name in (patch.get("providers") or {}):
        if name not in ("anthropic", "openai", "bedrock", "vertex") or \
                name not in cleaned["providers"]:
            bad.append(f"providers.{name}")
    eng_patch = patch.get("engines") or {}
    for name in eng_patch:
        if name in ENGINE_NAMES:
            if name not in cleaned["engines"]:
                bad.append(f"engines.{name}")
        elif name == "default":
            if eng_patch["default"] not in ENGINE_NAMES:
                bad.append("engines.default")
        else:
            bad.append(f"engines.{name}")
    if bad:
        raise ValueError(f"not a valid value for: {', '.join(bad)}")
    cfg = load()
    for name, sub in cleaned["providers"].items():
        cfg["providers"][name] = {**cfg["providers"][name], **sub}
    for name, sub in cleaned["engines"].items():
        if name == "default":
            continue
        cfg["engines"][name] = {**cfg["engines"][name], **sub}
    if "default" in cleaned["engines"]:
        cfg["engines"]["default"] = cleaned["engines"]["default"]
    _save(cfg)
    return cfg


# ------------------------------------------------------------------ secrets

def _tls():
    return access._tls()


def _verify_openai_key(key):
    req = urllib.request.Request("https://api.openai.com/v1/models",
                                 headers={"Authorization": f"Bearer {key}"})
    try:
        with _urlopen(req, timeout=15, context=_tls()) as r:
            return r.status == 200
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise ValueError("OpenAI rejected this key") from None
        raise ValueError(f"OpenAI answered {e.code}; not saved") from None
    except (urllib.error.URLError, OSError):
        raise ValueError("couldn't reach OpenAI to check the key; not saved") from None


def save_provider_key(provider, key):
    """Anthropic goes straight through access.py -- same shape check, same
    verify-before-store, same Keychain item a key saved before this module
    existed already lives in. OpenAI gets the same three steps, its own
    item."""
    if provider == "anthropic":
        access.save_key(key)
        return
    if provider == "openai":
        key = (key or "").strip()
        if not _OPENAI_KEY_RE.match(key):
            raise ValueError("that doesn't look like an OpenAI API key (sk-…)")
        _verify_openai_key(key)
        if not access.keychain_set(OPENAI_KC_SERVICE, OPENAI_KC_ACCOUNT, key,
                                   label="Podium OpenAI API key"):
            raise RuntimeError("the Keychain would not take it")
        cfg = load()
        cfg["providers"]["openai"]["hint"] = f"{key[:7]}…{key[-4:]}"
        _save(cfg)
        return
    raise ValueError(f"no such provider: {provider!r}")


def delete_provider_key(provider):
    if provider == "anthropic":
        access.delete_key()
        return
    if provider == "openai":
        access.keychain_delete(OPENAI_KC_SERVICE, OPENAI_KC_ACCOUNT)
        cfg = load()
        cfg["providers"]["openai"]["hint"] = ""
        _save(cfg)
        return
    raise ValueError(f"no such provider: {provider!r}")


# --------------------------------------------------------------- launching

def _toml(value):
    """A TOML-valid literal for the right-hand side of a `codex -c key=value`
    override. `-c` parses `value` as TOML and falls back to the raw string
    when it fails to parse (codex --help) -- json.dumps already produces
    valid TOML for every shape sent here (a quoted string, an array of
    quoted strings, true/false), so codex always sees the type meant, never
    a lucky fallback, and every character in it is JSON/TOML-escaped."""
    return json.dumps(value)


def _claude_argv(cfg, overrides):
    eng = dict(cfg["engines"]["claude"])
    if overrides.get("model"):
        eng["model"] = overrides["model"]
    if overrides.get("effort"):
        eng["effort"] = overrides["effort"]
    provider = eng.get("provider") or "anthropic"
    if provider not in CLAUDE_PROVIDERS:
        raise ValueError(f"claude engine has an unknown provider: {provider!r}")
    providers = cfg["providers"]
    mode = provider
    if provider == "anthropic":
        mode = "api_key" if (providers.get("anthropic") or {}).get("mode") == "api_key" \
            else "subscription"
    # access.launch_flags/with_access already know how to turn this exact
    # shape into --settings/--model/--effort -- built here, not reimplemented.
    old_cfg = {
        "mode": mode,
        "model": eng.get("model", ""),
        "effort": eng.get("effort", ""),
        "bedrock": providers.get("bedrock") or {"region": "", "profile": ""},
        "vertex": providers.get("vertex") or {"region": "", "project": ""},
    }
    flags = access.launch_flags(old_cfg)
    return access.with_access(["claude", "--permission-mode", "auto"], flags=flags)


def _codex_provider_flags(provider, providers):
    if provider == "openai":
        openai_cfg = providers.get("openai") or {}
        if (openai_cfg.get("mode") or "chatgpt") != "api_key":
            return []       # codex's own built-in "openai" provider + its ChatGPT login
        pid = "podium-openai-key"
        return [
            "-c", f"model_provider={_toml(pid)}",
            "-c", f"model_providers.{pid}.name={_toml('OpenAI (Podium key)')}",
            "-c", f"model_providers.{pid}.base_url={_toml('https://api.openai.com/v1')}",
            "-c", f"model_providers.{pid}.wire_api={_toml('responses')}",
            # the key never reaches argv, env, or this function's return
            # value -- codex runs this command itself and reads its stdout
            "-c", f"model_providers.{pid}.auth.command={_toml(access.SECURITY)}",
            "-c", f"model_providers.{pid}.auth.args="
                  f"{_toml(['find-generic-password', '-s', OPENAI_KC_SERVICE, '-a', OPENAI_KC_ACCOUNT, '-w'])}",
        ]
    if provider == "bedrock":
        b = providers.get("bedrock") or {}
        flags = ["-c", f"model_provider={_toml('amazon-bedrock')}"]
        if b.get("region"):
            flags += ["-c", f"model_providers.amazon-bedrock.aws.region={_toml(b['region'])}"]
        if b.get("profile"):
            flags += ["-c", f"model_providers.amazon-bedrock.aws.profile={_toml(b['profile'])}"]
        return flags
    if provider == "vertex":
        raise ValueError("Codex does not support Google Vertex yet -- no confirmed "
                         "Responses API support (see engines.py's module docstring)")
    raise ValueError(f"codex engine has an unknown provider: {provider!r}")


def _codex_argv(cfg, overrides):
    eng = dict(cfg["engines"]["codex"])
    for k in ("model", "effort", "sandbox", "approval"):
        if overrides.get(k):
            eng[k] = overrides[k]
    provider = eng.get("provider") or "openai"
    model = eng.get("model") or ""
    if model and not access._MODEL_RE.match(model):
        raise ValueError("not a valid model id")
    sandbox = eng.get("sandbox") or "workspace-write"
    if sandbox not in SANDBOX:
        raise ValueError(f"not a valid sandbox: {sandbox!r}")
    approval = eng.get("approval") or "on-request"
    if approval not in APPROVAL:
        raise ValueError(f"not a valid approval policy: {approval!r}")
    effort = eng.get("effort") or ""
    if effort and effort not in access.EFFORTS:
        raise ValueError(f"not a valid effort: {effort!r}")

    argv = ["codex"]
    if model:
        argv += ["-m", model]
    argv += _codex_provider_flags(provider, cfg["providers"])
    if effort:
        argv += ["-c", f"model_reasoning_effort={_toml(effort)}"]
    argv += ["-s", sandbox, "-a", approval]
    return argv


def launch_argv(engine, overrides=None):
    """Full argv for a new agent of this engine. `overrides` may carry
    "model" (both engines) and, for codex, "effort"/"sandbox"/"approval" --
    a per-launch choice from New Agent, without touching the saved default."""
    cfg = load()
    overrides = overrides or {}
    if engine == "claude":
        return _claude_argv(cfg, overrides)
    if engine == "codex":
        return _codex_argv(cfg, overrides)
    raise ValueError(f"unknown engine: {engine!r}")


# ------------------------------------------------------------------ status

_codex_status_cache = {"at": -1e9, "got": None}
_codex_status_lock = threading.Lock()
CODEX_STATUS_TTL = 30


def codex_status():
    """{"installed", "version", "logged_in"} -- `codex --version` and
    `codex login status` are both fast (no daemon, no network for status),
    but cached anyway on the same TTL access.auth_status uses, for the same
    reason: a settings panel that asks on every poll."""
    with _codex_status_lock:
        cached = _codex_status_cache["got"]
        if cached is not None and time.time() - _codex_status_cache["at"] < CODEX_STATUS_TTL:
            return cached
    installed = shutil.which("codex") is not None
    version, logged_in = "", False
    if installed:
        try:
            v = _run(["codex", "--version"], capture_output=True, text=True, timeout=10)
            version = (v.stdout or "").strip().split()[-1] if v.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            version = ""
        try:
            s = _run(["codex", "login", "status"], capture_output=True, text=True, timeout=10)
            # codex prints its status on stderr ("Logged in using ChatGPT"),
            # not stdout -- reading stdout alone always said "not logged in"
            logged_in = s.returncode == 0 and "logged in" in ((s.stdout or "") + (s.stderr or "")).lower()
        except (OSError, subprocess.SubprocessError):
            logged_in = False
    got = {"installed": installed, "version": version, "logged_in": logged_in}
    with _codex_status_lock:
        _codex_status_cache.update(at=time.time(), got=got)
    return got


def forget_codex_status():
    with _codex_status_lock:
        _codex_status_cache["got"] = None


def codex_login():
    """`codex login` wants a browser and a terminal, same reasoning as
    access.login for `claude auth login`."""
    script = ('tell application "Terminal"\n  activate\n'
              '  do script "codex login"\nend tell')
    out = _run(["osascript", "-e", script], capture_output=True, text=True, timeout=20)
    forget_codex_status()
    if out.returncode != 0:
        raise RuntimeError((out.stderr or "couldn't open Terminal").strip()[-200:])


def state():
    """GET /providers' body: providers (with key.saved/hint where they have
    one), engines, codex's own install/login status, and claude's auth
    status (access.auth_status, already cached there)."""
    cfg = load()
    anth_saved = access.key_saved()
    openai_saved = access.keychain_exists(OPENAI_KC_SERVICE, OPENAI_KC_ACCOUNT)
    providers = {
        "anthropic": {**cfg["providers"]["anthropic"],
                     "key": {"saved": anth_saved,
                             "hint": cfg["providers"]["anthropic"].get("hint", "") if anth_saved else ""}},
        "openai": {**cfg["providers"]["openai"],
                  "key": {"saved": openai_saved,
                          "hint": cfg["providers"]["openai"].get("hint", "") if openai_saved else ""}},
        "bedrock": dict(cfg["providers"]["bedrock"]),
        "vertex": dict(cfg["providers"]["vertex"]),
    }
    engines = {
        "claude": dict(cfg["engines"]["claude"]),
        # not offered yet -- see the module docstring's Vertex section
        "codex": {**cfg["engines"]["codex"], "vertex_supported": False},
        "default": cfg["engines"]["default"],
    }
    return {
        "providers": providers,
        "engines": engines,
        "codex": codex_status(),
        "claude": {"auth": access.auth_status()},
    }
