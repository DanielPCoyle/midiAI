#!/usr/bin/env python3
"""Enforcement for one checkout's guardrail checklist (mapui's /guardrails).

The checklist (app/src/guardrails.js, ~/.midiai/guardrails.json via mapui) is
just a claim and a validation note. This turns a guardrail into something
that actually runs: a script (exit 0 pass, 2 n/a, anything else fail) or an
agent review (an isolated `claude -p` judges a diff against a written brief
and answers JSON). An AI "compile" step writes whichever kind fits.

Enforcers live IN the repo, under .guardrails/ -- a manifest plus one file
per rail. Two facts live OUTSIDE the repo, beside guardrails.json:
- ~/.midiai/guardrail-approvals.json -- a script only runs if its current
  content hash was approved on THIS machine (AI-written code; a pulled
  change needs re-approval). Agent briefs execute nothing, so need none.
- ~/.midiai/guardrail-runs.json -- the last result per rail.

    python3 guardrails.py run --cwd .
    python3 guardrails.py status --cwd .
    python3 guardrails.py compile --cwd . --id some-id   (item JSON on stdin)
    python3 guardrails.py hook pre-commit|pre-push|stop --cwd .

mapui.py imports the functions below; keep their names and shapes.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

APPROVALS_FILE = os.path.expanduser("~/.midiai/guardrail-approvals.json")
RUNS_FILE = os.path.expanduser("~/.midiai/guardrail-runs.json")

HOOK_MARKER = "# midiai-guardrails"
TRIGGERS = ("manual", "stop", "pre-commit", "pre-push")

# A guardrail id becomes a filename, and this server can be reached over the
# LAN with an id that came from an app -- so it is sanitized against this
# fixed set, never against "does it look like a path".
_ID_RE = re.compile(r"[^a-z0-9._-]")

# The compile call gets read access to the real repo (to look at what linters
# or scripts already exist) but nothing else -- it writes a file, it does not
# run one.
COMPILE_CMD = ["claude", "-p", "--model", "sonnet", "--no-session-persistence",
               "--setting-sources", "", "--strict-mcp-config",
               "--allowedTools", "Read", "Glob", "Grep"]

# The review call gets no tools at all -- it only ever sees the diff it is
# handed, the same isolation memory.SUMMARY_CMD and commit_message lean on.
REVIEW_CMD = ["claude", "-p", "--model", "sonnet", "--no-session-persistence",
              "--setting-sources", "", "--strict-mcp-config", "--tools", ""]


# ---------------------------------------------------------------- plumbing

def _safe_id(raw):
    return _ID_RE.sub("-", str(raw).lower())[:80]


def _root(path):
    """git's top level for `path`, the same key mapui's _repo_request hands
    in -- approvals and results are stored by root, so a hook fired from a
    subfolder must land on the same string or it sees nothing."""
    path = os.path.realpath(os.path.expanduser(str(path)))
    out = subprocess.run(["git", "-C", path, "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True, timeout=10)
    return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else path


def _dir(root):
    return os.path.join(root, ".guardrails")


def _load_json_file(path):
    try:
        with open(path) as f:
            got = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return got if isinstance(got, dict) else {}


def _save_json_file(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def _write_text(path, content):
    if not content.endswith("\n"):
        content += "\n"
    with open(path, "w") as f:
        f.write(content)


def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _git(root, *argv, timeout=30):
    return subprocess.run(["git", "-C", root, *argv], capture_output=True,
                          text=True, timeout=timeout)


def _parse_json_object(text):
    """The first {...} JSON object in text, tolerating a code fence around it
    -- models asked for "strict JSON, no fence" still sometimes wrap it in
    ```json."""
    text = (text or "").strip()
    start = text.find("{")
    if start < 0:
        raise RuntimeError("the model did not return JSON")
    # raw_decode reads one value and ignores what follows (a closing fence);
    # it knows a "}" inside a string is text -- scripts are full of them.
    # strict=False: a model puts raw newlines inside a script string.
    try:
        got, _ = json.JSONDecoder(strict=False).raw_decode(text, start)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"the model's JSON did not parse: {e}") from e
    if not isinstance(got, dict):
        raise RuntimeError("the model did not return a JSON object")
    return got


# ------------------------------------------------------------------ manifest

def load_manifest(root):
    got = _load_json_file(os.path.join(_dir(root), "manifest.json"))
    got.setdefault("version", 1)
    got.setdefault("triggers", {})
    got.setdefault("rails", {})
    return got


def save_manifest(root, m):
    _save_json_file(os.path.join(_dir(root), "manifest.json"), m)


def rail_file(root, id):
    """(path, content) for the file the manifest currently has for `id`, or
    (None, "") if there isn't one -- a missing manifest entry and a manifest
    entry whose file went missing on disk are the same "nothing to show"."""
    rid = _safe_id(id)
    entry = load_manifest(root).get("rails", {}).get(rid)
    fname = (entry or {}).get("file") or ""
    # the manifest is repo content, so "file" may only name something inside
    # .guardrails/ -- never ../../somewhere
    if not fname or os.path.basename(fname) != fname or fname.startswith("."):
        return None, ""
    path = os.path.join(_dir(root), fname)
    try:
        with open(path) as f:
            return path, f.read()
    except OSError:
        return None, ""


def _remove_other_kind_files(root, rid, keep):
    """A recompile replaces the other kind's file -- switching a rail from
    script to agent (or back) should not leave the old enforcer behind."""
    for fname in (f"{rid}.sh", f"{rid}.py", f"{rid}.review.md"):
        if fname == keep:
            continue
        path = os.path.join(_dir(root), fname)
        if os.path.exists(path):
            os.remove(path)


# ------------------------------------------------------------------ compile

def _compile_prompt(item):
    return (
        "You are writing an ENFORCER for one guardrail in a software repo's "
        ".guardrails/ directory. Decide whether it is best checked by a "
        "script or by a judgement-based review of a diff, then write it.\n\n"
        f"Guardrail id: {item.get('id', '')}\n"
        f"Phase: {item.get('phase', '')}\n"
        f"Title: {item.get('title', '')}\n"
        f"Implemented (what should be true): {item.get('implemented', '')}\n"
        f"How to validate: {item.get('validate', '')}\n\n"
        "Prefer a SCRIPT whenever the check is mechanical -- a git command, a "
        "grep, a file existing, running a linter or test command this repo "
        "already has. Write an AGENT REVIEW only when the check genuinely "
        "needs judgement a script cannot make.\n\n"
        "Script rules: it runs with the repo as its cwd and must be "
        "READ-ONLY -- never modify the repo, never commit or push, no "
        "network access unless the guardrail is specifically about network "
        "behaviour. Exit 0 to pass, exit 2 if the guardrail does not apply "
        "to this change (n/a), any other exit code to fail. Print one line "
        "explaining the result. It may read the environment variables "
        "GUARDRAIL_ROOT, GUARDRAIL_TRIGGER and GUARDRAIL_DIFF_BASE.\n\n"
        "Agent review rules: a brief telling a reviewer what to look for in "
        "a diff, what counts as a pass, what counts as a fail, and when the "
        "guardrail does not apply (n/a).\n\n"
        "Reply with STRICT JSON and nothing else -- no code fence, no prose "
        "around it, exactly one of:\n"
        '{"kind":"script","lang":"sh or py","script":"the full script text"}\n'
        'or\n'
        '{"kind":"agent","brief":"the full brief text"}'
    )


def _call_claude(cmd, prompt, cwd, timeout):
    done = subprocess.run(cmd, input=prompt, text=True, capture_output=True,
                          timeout=timeout, cwd=cwd)
    if done.returncode:
        raise RuntimeError((done.stderr or "the model call failed").strip()[-300:])
    return done.stdout


def compile_rail(root, item, run=None):
    """Ask claude -p to turn one checklist item into an enforcer, write the
    file, update the manifest, and return what was written. `run` is an
    injectable fn(prompt) -> str, for tests."""
    rid = _safe_id(item.get("id", ""))
    if not rid:
        raise ValueError("guardrail has no usable id")
    prompt = _compile_prompt(item)
    out = run(prompt) if run else _call_claude(COMPILE_CMD, prompt, root, 240)
    parsed = _parse_json_object(out)
    kind = parsed.get("kind")
    if kind == "script":
        lang = parsed.get("lang") if parsed.get("lang") in ("sh", "py") else "sh"
        script = str(parsed.get("script") or "")
        if not script.strip():
            raise RuntimeError("the model returned an empty script")
        fname = f"{rid}.{lang}"
        path = os.path.join(_dir(root), fname)
        os.makedirs(_dir(root), exist_ok=True)
        _write_text(path, script)
        os.chmod(path, 0o755)
        content = script
    elif kind == "agent":
        brief = str(parsed.get("brief") or "")
        if not brief.strip():
            raise RuntimeError("the model returned an empty brief")
        fname = f"{rid}.review.md"
        path = os.path.join(_dir(root), fname)
        os.makedirs(_dir(root), exist_ok=True)
        _write_text(path, brief)
        content = brief
    else:
        raise RuntimeError(f"unrecognised enforcer kind: {kind!r}")
    _remove_other_kind_files(root, rid, keep=fname)
    m = load_manifest(root)
    existing = m["rails"].get(rid, {})
    m["rails"][rid] = {"phase": item.get("phase", ""), "title": item.get("title", ""),
                        "kind": kind, "file": fname,
                        "blocking": bool(existing.get("blocking", False))}
    save_manifest(root, m)
    # Nothing to explicitly revoke here: is_approved() hashes the file that
    # is CURRENTLY on disk, and that file just changed, so any prior
    # approval stops matching on its own.
    return {"kind": kind, "file": fname, "content": content}


# ------------------------------------------------------------------ approval

def digest(content):
    return hashlib.sha256(content.encode()).hexdigest()


def approve(root, id, expect=None):
    """`expect` is the digest of the text the person actually read: if the
    file changed between their "view" and their "approve", nothing is
    approved -- approval is of what was seen, not of what is there now."""
    path, content = rail_file(root, id)
    if path is None:
        raise ValueError("no enforcer file for that guardrail")
    got = digest(content)
    if expect is not None and expect != got:
        raise ValueError("the script changed since you viewed it -- view it again")
    all_approvals = _load_json_file(APPROVALS_FILE)
    all_approvals.setdefault(root, {})[_safe_id(id)] = got
    _save_json_file(APPROVALS_FILE, all_approvals)


def is_approved(root, id):
    """Agent briefs execute nothing, so they need no approval -- only a
    script's current content has to match an approval recorded on this
    machine."""
    path, content = rail_file(root, id)
    if path is None:
        return False
    if path.endswith(".review.md"):
        return True
    return _load_json_file(APPROVALS_FILE).get(root, {}).get(_safe_id(id)) == digest(content)


# ------------------------------------------------------------------ running

def _diff_base(root):
    """merge-base(HEAD, X) for the first of origin/HEAD, main, master that
    exists -- used both as GUARDRAIL_DIFF_BASE for scripts and as the
    fallback range for an agent review when there is no working-tree diff."""
    for ref in ("origin/HEAD", "main", "master"):
        out = _git(root, "merge-base", ref, "HEAD")
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    return ""


def _collect_diff(root, trigger):
    if trigger == "pre-commit":
        diff = _git(root, "diff", "--cached", "--no-color").stdout
    else:
        diff = _git(root, "diff", "HEAD", "--no-color").stdout
        if diff.strip():
            untracked = _git(root, "ls-files", "--others", "--exclude-standard").stdout.split()
            if untracked:
                diff += "\n\nNew untracked files:\n" + "\n".join(untracked[:200])
    if not diff.strip():
        base = _diff_base(root)
        if base:
            diff = _git(root, "diff", f"{base}...HEAD", "--no-color").stdout
    if len(diff) > 60000:
        diff = diff[:60000] + "\n[diff truncated]"
    return diff


def _run_script(root, rid, entry, trigger, trust):
    started = time.time()
    base = {"id": rid, "phase": entry.get("phase", ""), "title": entry.get("title", ""),
            "kind": "script", "blocking": bool(entry.get("blocking", False))}
    if not trust and not is_approved(root, rid):
        return {**base, "verdict": "unapproved",
                "reason": "script not approved on this machine", "ms": 0, "at": _now_iso()}
    path, _ = rail_file(root, rid)   # the same checked path is_approved hashed
    if not path or not os.path.isfile(path):
        return {**base, "verdict": "error", "reason": "enforcer file is missing",
                "ms": 0, "at": _now_iso()}
    env = dict(os.environ, GUARDRAIL_ROOT=root, GUARDRAIL_TRIGGER=trigger or "",
              GUARDRAIL_DIFF_BASE=_diff_base(root), MIDIAI_GUARDRAILS="1")
    try:
        out = subprocess.run([path], cwd=root, capture_output=True, text=True,
                             timeout=120, env=env)
        reason = ((out.stdout or "") + (out.stderr or ""))[-1500:].strip()
        verdict = {0: "pass", 2: "na"}.get(out.returncode, "fail")
    except subprocess.TimeoutExpired:
        verdict, reason = "error", "timed out after 120s"
    except OSError as e:
        verdict, reason = "error", str(e)
    return {**base, "verdict": verdict, "reason": reason,
            "ms": int((time.time() - started) * 1000), "at": _now_iso()}


def _review_prompt(entry, brief, diff):
    return (
        "You are judging one CHANGE against one guardrail. Read the brief, "
        "then the diff, and decide.\n\n"
        f"Guardrail: {entry.get('title', '')}\n\n"
        f"Brief:\n{brief}\n\n"
        f"Diff:\n{diff}\n\n"
        "Reply with STRICT JSON and nothing else -- no code fence, no prose "
        "around it:\n"
        '{"verdict":"pass, fail, or na","reason":"one or two sentences"}'
    )


def _run_agent(root, rid, entry, trigger, run_model):
    started = time.time()
    base = {"id": rid, "phase": entry.get("phase", ""), "title": entry.get("title", ""),
            "kind": "agent", "blocking": bool(entry.get("blocking", False))}
    diff = _collect_diff(root, trigger)
    if not diff.strip():
        return {**base, "verdict": "na", "reason": "nothing to review -- no diff",
                "ms": int((time.time() - started) * 1000), "at": _now_iso()}
    _, brief = rail_file(root, rid)
    prompt = _review_prompt(entry, brief, diff)
    try:
        out = run_model(prompt) if run_model else _call_claude(REVIEW_CMD, prompt,
                                                                 tempfile.gettempdir(), 180)
        parsed = _parse_json_object(out)
        verdict = parsed.get("verdict")
        reason = str(parsed.get("reason", ""))[:1500]
    except Exception as e:
        return {**base, "verdict": "error", "reason": str(e)[:1500],
                "ms": int((time.time() - started) * 1000), "at": _now_iso()}
    if verdict not in ("pass", "fail", "na"):
        return {**base, "verdict": "error",
                "reason": f"unrecognised verdict from the model: {verdict!r}",
                "ms": int((time.time() - started) * 1000), "at": _now_iso()}
    return {**base, "verdict": verdict, "reason": reason,
            "ms": int((time.time() - started) * 1000), "at": _now_iso()}


def _save_results(root, results):
    all_runs = _load_json_file(RUNS_FILE)
    bucket = all_runs.setdefault(root, {})
    for r in results:
        bucket[r["id"]] = r
    _save_json_file(RUNS_FILE, all_runs)


def run(root, ids=None, phase=None, trigger=None, trust=False, run_model=None):
    """Runs the selected rails -- scripts serially, agent reviews in up to 4
    parallel threads -- and saves each result. Selection: by explicit ids, by
    phase, or (trigger given) by every phase whose configured trigger matches."""
    m = load_manifest(root)
    triggers = m.get("triggers", {})
    selected = []
    for rid, entry in m.get("rails", {}).items():
        if ids is not None and rid not in ids:
            continue
        if phase is not None and entry.get("phase") != phase:
            continue
        if trigger is not None and triggers.get(entry.get("phase"), "manual") != trigger:
            continue
        selected.append((rid, entry))
    results = [None] * len(selected)
    script_idx = [i for i, (_, e) in enumerate(selected) if e.get("kind") != "agent"]
    agent_idx = [i for i, (_, e) in enumerate(selected) if e.get("kind") == "agent"]
    for i in script_idx:
        rid, entry = selected[i]
        results[i] = _run_script(root, rid, entry, trigger, trust)
    if agent_idx:
        with ThreadPoolExecutor(max_workers=4) as ex:
            futs = {ex.submit(_run_agent, root, selected[i][0], selected[i][1],
                              trigger, run_model): i for i in agent_idx}
            for fut in as_completed(futs):
                results[futs[fut]] = fut.result()
    _save_results(root, results)
    return results


def status(root):
    m = load_manifest(root)
    all_runs = _load_json_file(RUNS_FILE).get(root, {})
    approved = {rid: is_approved(root, rid) for rid in m.get("rails", {})}
    hdir = _hooks_dir(root)
    hooks = {"pre-commit": _git_hook_is_ours(hdir, "pre-commit"),
             "pre-push": _git_hook_is_ours(hdir, "pre-push"),
             "stop": _stop_hook_installed(root)}
    return {"manifest": m, "results": all_runs, "approved": approved, "hooks": hooks}


def set_blocking(root, id, blocking):
    rid = _safe_id(id)
    m = load_manifest(root)
    if rid not in m.get("rails", {}):
        raise ValueError("no such guardrail")
    m["rails"][rid]["blocking"] = bool(blocking)
    save_manifest(root, m)


def set_trigger(root, phase, trigger):
    if trigger not in TRIGGERS:
        raise ValueError(f"unknown trigger: {trigger!r}")
    m = load_manifest(root)
    m.setdefault("triggers", {})[phase] = trigger
    save_manifest(root, m)


# -------------------------------------------------------------------- hooks

def _hooks_dir(root):
    """The real hooks directory for this checkout -- handles a worktree and
    a repo-wide core.hooksPath, neither of which is simply <root>/.git/hooks."""
    out = _git(root, "rev-parse", "--git-path", "hooks")
    path = out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else "hooks"
    return path if os.path.isabs(path) else os.path.join(root, path)


def _git_hook_shim(name):
    return (f"#!/bin/sh\n{HOOK_MARKER}\n"
            f'exec "{sys.executable}" "{os.path.abspath(__file__)}" hook {name} '
            f'--cwd "$(git rev-parse --show-toplevel)"\n')


def _git_hook_is_ours(hdir, name):
    path = os.path.join(hdir, name)
    try:
        with open(path) as f:
            return HOOK_MARKER in f.read()
    except OSError:
        return False


def _install_git_hook(hdir, name):
    path = os.path.join(hdir, name)
    shim = _git_hook_shim(name)
    if os.path.exists(path):
        try:
            with open(path) as f:
                content = f.read()
        except OSError:
            return "exists"
        if HOOK_MARKER not in content:
            return "exists"          # never overwrite a hook we did not write
        if content == shim:
            return "ours"
        with open(path, "w") as f:
            f.write(shim)
        os.chmod(path, 0o755)
        return "ours"
    os.makedirs(hdir, exist_ok=True)
    with open(path, "w") as f:
        f.write(shim)
    os.chmod(path, 0o755)
    return "installed"


def _uninstall_git_hook(hdir, name):
    path = os.path.join(hdir, name)
    try:
        with open(path) as f:
            content = f.read()
    except OSError:
        return "absent"
    if HOOK_MARKER not in content:
        return "left alone"          # somebody else's hook
    os.remove(path)
    return "removed"


def _settings_path(root):
    return os.path.join(root, ".claude", "settings.local.json")


def _stop_command():
    return f'"{sys.executable}" "{os.path.abspath(__file__)}" hook stop'


def _stop_hook_installed(root):
    settings = _load_json_file(_settings_path(root))
    cmd = _stop_command()
    for entry in settings.get("hooks", {}).get("Stop", []) or []:
        if cmd in [h.get("command") for h in entry.get("hooks", []) or []]:
            return True
    return False


def _install_stop_hook(root):
    """Merges a Stop hook entry into <root>/.claude/settings.local.json,
    creating it if absent and touching nothing else already there."""
    if _stop_hook_installed(root):
        return "ours"
    path = _settings_path(root)
    settings = _load_json_file(path)
    stops = settings.setdefault("hooks", {}).setdefault("Stop", [])
    stops.append({"hooks": [{"type": "command", "command": _stop_command(), "timeout": 300}]})
    _save_json_file(path, settings)
    return "installed"


def _uninstall_stop_hook(root):
    path = _settings_path(root)
    if not os.path.exists(path):
        return "absent"
    settings = _load_json_file(path)
    cmd = _stop_command()
    stops = settings.get("hooks", {}).get("Stop", [])
    kept = [e for e in stops if cmd not in [h.get("command") for h in e.get("hooks", []) or []]]
    if len(kept) == len(stops):
        return "absent"
    if kept:
        settings["hooks"]["Stop"] = kept
    else:
        settings.get("hooks", {}).pop("Stop", None)
        if not settings.get("hooks"):
            settings.pop("hooks", None)
    _save_json_file(path, settings)
    return "removed"


def install_hooks(root):
    hdir = _hooks_dir(root)
    return {"pre-commit": _install_git_hook(hdir, "pre-commit"),
            "pre-push": _install_git_hook(hdir, "pre-push"),
            "stop": _install_stop_hook(root)}


def uninstall_hooks(root):
    hdir = _hooks_dir(root)
    return {"pre-commit": _uninstall_git_hook(hdir, "pre-commit"),
            "pre-push": _uninstall_git_hook(hdir, "pre-push"),
            "stop": _uninstall_stop_hook(root)}


# --------------------------------------------------------------------- CLI

def _blocking_failures(results):
    return [r for r in results if r["blocking"] and r["verdict"] in ("fail", "error", "unapproved")]


def _cli_hook_stop(root):
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        payload = {}
    if payload.get("stop_hook_active"):
        sys.exit(0)
    cwd = payload.get("cwd") or root
    results = run(_root(cwd), trigger="stop")
    blocking_fail = _blocking_failures(results)
    if blocking_fail:
        reason = "; ".join(f"{r['id']}: {r['reason']}" for r in blocking_fail)
        print(json.dumps({"decision": "block",
                          "reason": f"guardrails failed -- fix before stopping: {reason}"}))
        sys.exit(0)
    advisory_fail = [r for r in results if not r["blocking"]
                     and r["verdict"] in ("fail", "error", "unapproved")]
    if advisory_fail:
        reason = "; ".join(f"{r['id']}: {r['verdict']}" for r in advisory_fail)
        print(json.dumps({"systemMessage": f"guardrails: {reason}"}))
    sys.exit(0)


def _cli():
    p = argparse.ArgumentParser(prog="guardrails.py")
    sub = p.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run")
    run_p.add_argument("--cwd", default=".")
    run_p.add_argument("--phase")
    run_p.add_argument("--id", action="append", dest="ids")
    run_p.add_argument("--trigger")
    run_p.add_argument("--trust", action="store_true")
    run_p.add_argument("--json", action="store_true")

    hook_p = sub.add_parser("hook")
    hook_p.add_argument("kind", choices=["pre-commit", "pre-push", "stop"])
    hook_p.add_argument("--cwd", default=".")

    compile_p = sub.add_parser("compile")
    compile_p.add_argument("--cwd", default=".")
    compile_p.add_argument("--id", required=True)

    sub.add_parser("status").add_argument("--cwd", default=".")

    args = p.parse_args()
    root = _root(args.cwd)

    if args.cmd == "run":
        results = run(root, ids=args.ids, phase=args.phase, trigger=args.trigger,
                      trust=args.trust)
        if args.json:
            print(json.dumps(results, indent=2))
        else:
            for r in results:
                print(f"{r['verdict']:>10}  {r['id']}  {r['reason'][:120]}")
        sys.exit(1 if _blocking_failures(results) else 0)

    if args.cmd == "hook":
        if os.environ.get("MIDIAI_GUARDRAILS") == "1":
            sys.exit(0)          # a hook this process itself triggered
        if args.kind == "stop":
            _cli_hook_stop(root)
            return
        results = run(root, trigger=args.kind)
        blocking_fail = _blocking_failures(results)
        if blocking_fail:
            for r in blocking_fail:
                print(f"guardrail {r['id']} failed: {r['reason']}", file=sys.stderr)
            sys.exit(1)
        sys.exit(0)

    if args.cmd == "compile":
        raw = sys.stdin.read()
        try:
            item = json.loads(raw)
        except json.JSONDecodeError:
            print("bad item JSON on stdin", file=sys.stderr)
            sys.exit(1)
        print(json.dumps(compile_rail(root, item), indent=2))
        return

    if args.cmd == "status":
        print(json.dumps(status(root), indent=2))


if __name__ == "__main__":
    _cli()
