#!/usr/bin/env python3
"""Enforcement for one checkout's guardrail checklist (mapui's /guardrails).

The checklist (app/src/guardrails.js, ~/.midiai/guardrails.json via mapui) is
just a claim and a validation note. This turns a guardrail into something
that actually runs: a script (exit 0 pass, 2 n/a, anything else fail) or an
agent review (an isolated `claude -p` judges evidence against a written
brief and answers JSON). An AI "compile" step writes whichever kind fits.

Enforcers live IN the repo, under .guardrails/ -- a manifest plus one file
per rail. Two facts live OUTSIDE the repo, beside guardrails.json:
- ~/.midiai/guardrail-approvals.json -- a script only runs if its current
  content hash was approved on THIS machine (AI-written code; a pulled
  change needs re-approval). Agent briefs execute nothing, so need none.
- ~/.midiai/guardrail-runs.json -- the last result per rail.

Rails are tool-, project- and method-agnostic. Phases are user-editable
(that stays outside this file). What lives here is the EVENT layer: a rail
belongs to one phase and one gate (entry or exit), and a gate is bound to
zero or more EVENTS -- things that actually happen (an agent stopping, a
git hook firing). `manual` is not an event you bind: the Run button (and
`run --id`/`--phase`) always works regardless of bindings.

    python3 guardrails.py run --cwd .
    python3 guardrails.py status --cwd .
    python3 guardrails.py compile --cwd . --id some-id   (item JSON on stdin)
    python3 guardrails.py hook git:pre-commit|git:commit-msg|git:pre-push|git:post-merge|agent:stop --cwd .
    (old bare names pre-commit/pre-push/stop still work, as aliases)

mapui.py imports the functions below; keep their names and shapes.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

APPROVALS_FILE = os.path.expanduser("~/.midiai/guardrail-approvals.json")
RUNS_FILE = os.path.expanduser("~/.midiai/guardrail-runs.json")

HOOK_MARKER = "# midiai-guardrails"

# The only events this build knows about. A rail's gate binds a subset of
# these; "manual" is never in this list -- it is what firing with no event
# at all means (the Run button, or run(ids=...)/run(phase=...) with no
# event= given).
EVENTS = ("agent:stop", "git:pre-commit", "git:commit-msg", "git:pre-push",
          "git:post-merge")

# The evidence kinds a rail can declare it "needs". ("ticket" comes with the
# tracker adapter -- not in this build.)
EVIDENCE = ("diff", "files", "commit-msg")

# What each event can actually supply. A need not in this set for the firing
# event is unsatisfiable this run (na, or fail if the rail is blocking).
_EVENT_EVIDENCE = {
    "agent:stop": ("diff", "files"),
    "git:pre-commit": ("diff", "files"),
    "git:commit-msg": ("diff", "files", "commit-msg"),
    "git:pre-push": ("diff", "files"),
    "git:post-merge": ("diff", "files"),
}

# v1's `triggers: {phase: T}` -> v2's `bindings`, and the deprecated
# `trigger=` kwarg on run() -> `event=`. "manual" maps to nothing: it was
# never a real trigger, just "no trigger configured".
_TRIGGER_TO_EVENT = {"stop": "agent:stop", "pre-commit": "git:pre-commit",
                     "pre-push": "git:pre-push"}

# The four real git hooks this build wires up, and the event each fires.
_GIT_HOOK_EVENTS = {"pre-commit": "git:pre-commit", "commit-msg": "git:commit-msg",
                    "pre-push": "git:pre-push", "post-merge": "git:post-merge"}

# Old bare hook names (and the git hook filenames themselves) still work as
# aliases for the event names on the `hook` CLI command.
_HOOK_ALIASES = {"pre-commit": "git:pre-commit", "pre-push": "git:pre-push",
                 "stop": "agent:stop", "commit-msg": "git:commit-msg",
                 "post-merge": "git:post-merge"}

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

# The review call gets no tools at all -- it only ever sees the evidence it
# is handed, the same isolation memory.SUMMARY_CMD and commit_message lean on.
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
    content = content or ""
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

def clean_bindings(got):
    """A phase's entry/exit gates -> the events they fire on. Unknown events
    (and unknown gates) are dropped rather than stored -- the same bargain
    every other cleaner in this codebase makes: the shape a client can hold
    is decided here, not by whatever it posted last."""
    out = {}
    if not isinstance(got, dict):
        return out
    for phase, gates in got.items():
        if not isinstance(gates, dict):
            continue
        cleaned_gates = {}
        for gate in ("entry", "exit"):
            events = gates.get(gate)
            if not isinstance(events, list):
                continue
            seen, deduped = set(), []
            for e in events:
                if e in EVENTS and e not in seen:
                    seen.add(e)
                    deduped.append(e)
            if deduped:
                cleaned_gates[gate] = deduped
        if cleaned_gates:
            out[str(phase)[:40]] = cleaned_gates
    return out


def _clean_needs(needs):
    seen, out = set(), []
    for n in (needs or []):
        if n in EVIDENCE and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def load_manifest(root):
    got = _load_json_file(os.path.join(_dir(root), "manifest.json"))
    if got.get("version", 1) < 2:
        # v1 -> v2: {phase: trigger} becomes {phase: {"exit": [event]}}.
        # Not written to disk here -- only the next save_manifest() persists
        # it, same as any other in-memory-only migration.
        bindings = {}
        for phase, trig in (got.get("triggers") or {}).items():
            ev = _TRIGGER_TO_EVENT.get(trig)
            if ev:
                bindings[str(phase)] = {"exit": [ev]}
        got["bindings"] = bindings
        got.pop("triggers", None)
        got["version"] = 2
    got.setdefault("version", 2)
    got["bindings"] = clean_bindings(got.get("bindings"))
    got.setdefault("rails", {})
    for entry in got["rails"].values():
        if isinstance(entry, dict):
            if entry.get("gate") not in ("entry", "exit"):
                entry["gate"] = "exit"
            entry["needs"] = _clean_needs(entry.get("needs"))
    return got


def save_manifest(root, m):
    m = dict(m)
    m["bindings"] = clean_bindings(m.get("bindings"))
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

def _compile_prompt(root, item):
    gate = item.get("gate") if item.get("gate") in ("entry", "exit") else "exit"
    bound = load_manifest(root).get("bindings", {}).get(item.get("phase", ""), {}).get(gate, [])
    if bound:
        lines = "\n".join(f"- {e}: supplies {', '.join(_EVENT_EVIDENCE.get(e, ()))}"
                          for e in bound)
        binding_text = (f"This rail's {gate} gate is bound to these events, each of which "
                        f"supplies certain evidence:\n{lines}\n\n")
    else:
        binding_text = (f"This rail's {gate} gate is not bound to any event yet -- it can "
                        "still be run manually, which supplies diff and files.\n\n")
    return (
        "You are writing an ENFORCER for one guardrail in a software repo's "
        ".guardrails/ directory. Decide whether it is best checked by a "
        "script or by a judgement-based review of evidence, then write it.\n\n"
        f"Guardrail id: {item.get('id', '')}\n"
        f"Phase: {item.get('phase', '')}\n"
        f"Gate: {gate}\n"
        f"Title: {item.get('title', '')}\n"
        f"Implemented (what should be true): {item.get('implemented', '')}\n"
        f"How to validate: {item.get('validate', '')}\n\n"
        f"{binding_text}"
        "The evidence kinds that exist are diff, files, and commit-msg. "
        "Decide which of these this check actually reads and return them as "
        "\"needs\" -- a subset of [\"diff\", \"files\", \"commit-msg\"].\n\n"
        "Prefer a SCRIPT whenever the check is mechanical -- a git command, a "
        "grep, a file existing, running a linter or test command this repo "
        "already has. Write an AGENT REVIEW only when the check genuinely "
        "needs judgement a script cannot make.\n\n"
        "Script rules: it runs with the repo as its cwd and must be "
        "READ-ONLY -- never modify the repo, never commit or push, no "
        "network access unless the guardrail is specifically about network "
        "behaviour. Exit 0 to pass, exit 2 if the guardrail does not apply "
        "to this change (n/a), any other exit code to fail. Print one line "
        "explaining the result. Read whatever evidence it needs from "
        "$GUARDRAIL_EVIDENCE/diff.patch, $GUARDRAIL_EVIDENCE/files.txt or "
        "$GUARDRAIL_EVIDENCE/commit-msg.txt rather than recomputing it -- it "
        "may also read GUARDRAIL_ROOT, GUARDRAIL_EVENT, GUARDRAIL_GATE and "
        "GUARDRAIL_DIFF_BASE.\n\n"
        "Agent review rules: a brief telling a reviewer what to look for in "
        "the evidence, what counts as a pass, what counts as a fail, and when "
        "the guardrail does not apply (n/a).\n\n"
        "Reply with STRICT JSON and nothing else -- no code fence, no prose "
        "around it, exactly one of:\n"
        '{"kind":"script","lang":"sh or py","script":"the full script text",'
        '"needs":["diff"]}\n'
        'or\n'
        '{"kind":"agent","brief":"the full brief text","needs":["diff"]}'
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
    gate = item.get("gate") if item.get("gate") in ("entry", "exit") else "exit"
    prompt = _compile_prompt(root, item)
    out = run(prompt) if run else _call_claude(COMPILE_CMD, prompt, root, 240)
    parsed = _parse_json_object(out)
    needs = _clean_needs(parsed.get("needs"))
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
                        "blocking": bool(existing.get("blocking", False)),
                        "gate": gate, "needs": needs}
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


def _collect_evidence(root, event):
    """(diff text, files text, base used) for whichever event is firing this
    run. `event` is None for a manual run (Run button, bare run(ids=...)),
    which is handled the same as agent:stop."""
    base = ""
    if event in ("git:pre-commit", "git:commit-msg"):
        diff = _git(root, "diff", "--cached", "--no-color").stdout
        files = _git(root, "diff", "--cached", "--name-only").stdout.strip()
    elif event == "git:pre-push":
        base = _diff_base(root)
        if base:
            diff = _git(root, "diff", f"{base}...HEAD", "--no-color").stdout
            files = _git(root, "diff", f"{base}...HEAD", "--name-only").stdout.strip()
        else:
            diff, files = "", ""
    elif event == "git:post-merge":
        has_orig = _git(root, "rev-parse", "--verify", "ORIG_HEAD").returncode == 0
        if has_orig:
            diff = _git(root, "diff", "ORIG_HEAD..HEAD", "--no-color").stdout
            files = _git(root, "diff", "ORIG_HEAD..HEAD", "--name-only").stdout.strip()
        else:
            diff, files = "", ""
    else:  # agent:stop, or a manual run (event is None)
        diff = _git(root, "diff", "HEAD", "--no-color").stdout
        files = _git(root, "diff", "HEAD", "--name-only").stdout.strip()
        if not diff.strip():
            base = _diff_base(root)
            if base:
                diff = _git(root, "diff", f"{base}...HEAD", "--no-color").stdout
                files = _git(root, "diff", f"{base}...HEAD", "--name-only").stdout.strip()
        untracked = _git(root, "ls-files", "--others", "--exclude-standard").stdout.split()
        if untracked:
            if diff.strip():
                diff += "\n\nNew untracked files:\n" + "\n".join(untracked[:200])
            files = (files + "\n" if files else "") + "\n".join(untracked[:200])
    if len(diff) > 60000:
        diff = diff[:60000] + "\n[diff truncated]"
    return diff, files, base


def _build_evidence(root, event, msg_file):
    """One evidence dir for the whole run -- diff.patch, files.txt, an
    event.json, and (only when the firing event supplies it) commit-msg.txt.
    Caller is responsible for removing the dir (try/finally in run())."""
    d = tempfile.mkdtemp(prefix="guardrail-evidence-")
    diff, files, base = _collect_evidence(root, event)
    _write_text(os.path.join(d, "diff.patch"), diff)
    _write_text(os.path.join(d, "files.txt"), files)
    commit_msg = None
    if event is None:
        # a manual Run has no message being written; the last commit's is the
        # one a commit-message rule can sensibly be checked against
        commit_msg = _git(root, "log", "-1", "--format=%B").stdout
        _write_text(os.path.join(d, "commit-msg.txt"), commit_msg)
    elif "commit-msg" in _EVENT_EVIDENCE.get(event, ()):
        try:
            with open(msg_file) as f:
                commit_msg = f.read()
        except (OSError, TypeError):
            commit_msg = ""
        _write_text(os.path.join(d, "commit-msg.txt"), commit_msg)
    _save_json_file(os.path.join(d, "event.json"),
                    {"event": event or "manual", "root": root, "base": base})
    return d, diff, files, commit_msg


def _run_script(root, rid, entry, event, trust, evidence_dir):
    started = time.time()
    gate = entry.get("gate") or "exit"
    base = {"id": rid, "phase": entry.get("phase", ""), "title": entry.get("title", ""),
            "kind": "script", "blocking": bool(entry.get("blocking", False)),
            "event": event or "manual", "gate": gate}
    if not trust and not is_approved(root, rid):
        return {**base, "verdict": "unapproved",
                "reason": "script not approved on this machine", "ms": 0, "at": _now_iso()}
    path, _ = rail_file(root, rid)   # the same checked path is_approved hashed
    if not path or not os.path.isfile(path):
        return {**base, "verdict": "error", "reason": "enforcer file is missing",
                "ms": 0, "at": _now_iso()}
    env = dict(os.environ, GUARDRAIL_ROOT=root, GUARDRAIL_TRIGGER=event or "",
              GUARDRAIL_EVENT=event or "", GUARDRAIL_GATE=gate,
              GUARDRAIL_DIFF_BASE=_diff_base(root), GUARDRAIL_EVIDENCE=evidence_dir,
              MIDIAI_GUARDRAILS="1")
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


def _review_prompt(entry, brief, needs, diff, files, commit_msg):
    sections = []
    if "diff" in needs:
        sections.append(f"Diff:\n{diff}")
    if "files" in needs:
        sections.append(f"Files changed:\n{files}")
    if "commit-msg" in needs and commit_msg is not None:
        sections.append(f"Commit message:\n{commit_msg}")
    if not sections:
        sections.append(f"Diff:\n{diff}")   # legacy rails with no needs: diff, as before
    return (
        "You are judging one CHANGE against one guardrail. Read the brief, "
        "then the evidence, and decide.\n\n"
        f"Guardrail: {entry.get('title', '')}\n\n"
        f"Brief:\n{brief}\n\n"
        + "\n\n".join(sections) +
        "\n\nReply with STRICT JSON and nothing else -- no code fence, no prose "
        "around it:\n"
        '{"verdict":"pass, fail, or na","reason":"one or two sentences"}'
    )


def _run_agent(root, rid, entry, event, run_model, diff, files, commit_msg):
    started = time.time()
    gate = entry.get("gate") or "exit"
    base = {"id": rid, "phase": entry.get("phase", ""), "title": entry.get("title", ""),
            "kind": "agent", "blocking": bool(entry.get("blocking", False)),
            "event": event or "manual", "gate": gate}
    needs = entry.get("needs") or []
    wants_diff = "diff" in needs or not needs   # a rail with no declared needs: diff, as before
    if wants_diff and not diff.strip():
        return {**base, "verdict": "na", "reason": "nothing to review -- no diff",
                "ms": int((time.time() - started) * 1000), "at": _now_iso()}
    _, brief = rail_file(root, rid)
    prompt = _review_prompt(entry, brief, needs, diff, files, commit_msg)
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


def run(root, ids=None, phase=None, event=None, trust=False, run_model=None,
        msg_file=None, trigger=None):
    """Runs the selected rails -- scripts serially, agent reviews in up to 4
    parallel threads -- and saves each result. Selection: by explicit ids, by
    phase, or (event given) by every rail whose (phase, gate) binds that
    event. `trigger` is a deprecated alias for `event`, mapped through the
    old trigger name table (a bare "manual" maps to no event at all, i.e. no
    event-based filtering -- same as leaving event unset).

    Builds one evidence dir for the whole call (removed in a finally, even
    if a check throws) and hands scripts its path via GUARDRAIL_EVIDENCE,
    agent reviews whichever pieces of it their `needs` ask for. A rail whose
    needs the firing event cannot supply is not run at all: verdict `na`
    (or `fail`, if the rail is blocking -- fail closed)."""
    if event is None and trigger is not None:
        event = _TRIGGER_TO_EVENT.get(trigger)   # "manual" (or unknown) -> None

    m = load_manifest(root)
    bindings = m.get("bindings", {})
    selected = []
    for rid, entry in m.get("rails", {}).items():
        if ids is not None and rid not in ids:
            continue
        if phase is not None and entry.get("phase") != phase:
            continue
        if event is not None:
            gate = entry.get("gate") or "exit"
            evs = bindings.get(entry.get("phase"), {}).get(gate, [])
            if event not in evs:
                continue
        selected.append((rid, entry))

    evidence_dir, diff, files, commit_msg = _build_evidence(root, event, msg_file)
    try:
        available = set(_EVENT_EVIDENCE.get(event, ("diff", "files", "commit-msg")))
        results = [None] * len(selected)
        runnable = []
        for i, (rid, entry) in enumerate(selected):
            needs = entry.get("needs") or []
            missing = [n for n in needs if n not in available]
            if missing:
                blocking = bool(entry.get("blocking", False))
                results[i] = {
                    "id": rid, "phase": entry.get("phase", ""), "title": entry.get("title", ""),
                    "kind": entry.get("kind", "script"), "blocking": blocking,
                    "event": event or "manual", "gate": entry.get("gate") or "exit",
                    "verdict": "fail" if blocking else "na",
                    "reason": f"needs {', '.join(missing)}, which {event or 'manual'} does not have",
                    "ms": 0, "at": _now_iso()}
            else:
                runnable.append(i)

        script_idx = [i for i in runnable if selected[i][1].get("kind") != "agent"]
        agent_idx = [i for i in runnable if selected[i][1].get("kind") == "agent"]
        for i in script_idx:
            rid, entry = selected[i]
            results[i] = _run_script(root, rid, entry, event, trust, evidence_dir)
        if agent_idx:
            with ThreadPoolExecutor(max_workers=4) as ex:
                futs = {ex.submit(_run_agent, root, selected[i][0], selected[i][1],
                                  event, run_model, diff, files, commit_msg): i
                       for i in agent_idx}
                for fut in as_completed(futs):
                    results[futs[fut]] = fut.result()
    finally:
        shutil.rmtree(evidence_dir, ignore_errors=True)
    _save_results(root, results)
    return results


def status(root):
    m = load_manifest(root)
    all_runs = _load_json_file(RUNS_FILE).get(root, {})
    approved = {rid: is_approved(root, rid) for rid in m.get("rails", {})}
    hdir = _hooks_dir(root)
    hooks = {ev: _git_hook_is_ours(hdir, name) for name, ev in _GIT_HOOK_EVENTS.items()}
    hooks["agent:stop"] = _stop_hook_installed(root)
    return {"manifest": m, "results": all_runs, "approved": approved, "hooks": hooks}


def set_blocking(root, id, blocking):
    rid = _safe_id(id)
    m = load_manifest(root)
    if rid not in m.get("rails", {}):
        raise ValueError("no such guardrail")
    m["rails"][rid]["blocking"] = bool(blocking)
    save_manifest(root, m)


def set_binding(root, phase, gate, events):
    if gate not in ("entry", "exit"):
        raise ValueError(f"unknown gate: {gate!r}")
    evs = [e for e in (events or []) if e in EVENTS]
    m = load_manifest(root)
    bindings = m.setdefault("bindings", {})
    phase_bindings = bindings.setdefault(str(phase), {})
    if evs:
        phase_bindings[gate] = evs
    else:
        phase_bindings.pop(gate, None)
        if not phase_bindings:
            bindings.pop(str(phase), None)
    save_manifest(root, m)


def set_gate(root, id, gate):
    if gate not in ("entry", "exit"):
        raise ValueError(f"unknown gate: {gate!r}")
    rid = _safe_id(id)
    m = load_manifest(root)
    if rid not in m.get("rails", {}):
        raise ValueError("no such guardrail")
    m["rails"][rid]["gate"] = gate
    save_manifest(root, m)


# -------------------------------------------------------------------- hooks

def _hooks_dir(root):
    """The real hooks directory for this checkout -- handles a worktree and
    a repo-wide core.hooksPath, neither of which is simply <root>/.git/hooks."""
    out = _git(root, "rev-parse", "--git-path", "hooks")
    path = out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else "hooks"
    return path if os.path.isabs(path) else os.path.join(root, path)


def _git_hook_shim(event):
    return (f"#!/bin/sh\n{HOOK_MARKER}\n"
            f'exec "{sys.executable}" "{os.path.abspath(__file__)}" hook {event} '
            f'--cwd "$(git rev-parse --show-toplevel)" "$@"\n')


def _git_hook_is_ours(hdir, name):
    path = os.path.join(hdir, name)
    try:
        with open(path) as f:
            return HOOK_MARKER in f.read()
    except OSError:
        return False


def _install_git_hook(hdir, name):
    path = os.path.join(hdir, name)
    shim = _git_hook_shim(_GIT_HOOK_EVENTS[name])
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
    return f'"{sys.executable}" "{os.path.abspath(__file__)}" hook agent:stop'


def _stop_command_old():
    """The pre-events Stop command -- `_stop_hook_installed` still recognises
    it (so status reads it as installed) and uninstall removes it too."""
    return f'"{sys.executable}" "{os.path.abspath(__file__)}" hook stop'


def _stop_hook_installed(root):
    settings = _load_json_file(_settings_path(root))
    known = (_stop_command(), _stop_command_old())
    for entry in settings.get("hooks", {}).get("Stop", []) or []:
        cmds = [h.get("command") for h in entry.get("hooks", []) or []]
        if any(c in cmds for c in known):
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
    known = (_stop_command(), _stop_command_old())
    stops = settings.get("hooks", {}).get("Stop", [])
    kept = [e for e in stops
           if not any(c in [h.get("command") for h in e.get("hooks", []) or []] for c in known)]
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
    out = {ev: _install_git_hook(hdir, name) for name, ev in _GIT_HOOK_EVENTS.items()}
    out["agent:stop"] = _install_stop_hook(root)
    return out


def uninstall_hooks(root):
    hdir = _hooks_dir(root)
    out = {ev: _uninstall_git_hook(hdir, name) for name, ev in _GIT_HOOK_EVENTS.items()}
    out["agent:stop"] = _uninstall_stop_hook(root)
    return out


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
    results = run(_root(cwd), event="agent:stop")
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
    run_p.add_argument("--event")
    run_p.add_argument("--trigger")   # deprecated alias for --event
    run_p.add_argument("--trust", action="store_true")
    run_p.add_argument("--json", action="store_true")

    hook_p = sub.add_parser("hook")
    hook_p.add_argument("kind")   # an event name, or an old bare alias
    hook_p.add_argument("--cwd", default=".")
    hook_p.add_argument("extra", nargs="*")   # commit-msg's $1 (message file path)

    compile_p = sub.add_parser("compile")
    compile_p.add_argument("--cwd", default=".")
    compile_p.add_argument("--id", required=True)

    sub.add_parser("status").add_argument("--cwd", default=".")

    args = p.parse_args()
    root = _root(args.cwd)

    if args.cmd == "run":
        results = run(root, ids=args.ids, phase=args.phase, event=args.event,
                      trigger=args.trigger, trust=args.trust)
        if args.json:
            print(json.dumps(results, indent=2))
        else:
            for r in results:
                print(f"{r['verdict']:>10}  {r['id']}  {r['reason'][:120]}")
        sys.exit(1 if _blocking_failures(results) else 0)

    if args.cmd == "hook":
        if os.environ.get("MIDIAI_GUARDRAILS") == "1":
            sys.exit(0)          # a hook this process itself triggered
        event = _HOOK_ALIASES.get(args.kind, args.kind)
        if event == "agent:stop":
            _cli_hook_stop(root)
            return
        if event not in EVENTS:
            print(f"unknown hook: {args.kind!r}", file=sys.stderr)
            sys.exit(1)
        msg_file = args.extra[0] if event == "git:commit-msg" and args.extra else None
        results = run(root, event=event, msg_file=msg_file)
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
