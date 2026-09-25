"""The instructions an agent actually reads -- CLAUDE.md, CLAUDE.local.md,
AGENTS.md and `.claude/rules/`, read and written the way Claude Code's own
memory model reads them (code.claude.com/docs/en/memory, Claude Code
2.1.281):

  managed policy   /Library/Application Support/ClaudeCode/CLAUDE.md  (RO)
  global           ~/.claude/CLAUDE.md, ~/.claude/rules/**/*.md
  project          <root>/CLAUDE.md, <root>/.claude/CLAUDE.md,
                   <root>/AGENTS.md, <root>/.claude/rules/**/*.md
  local            <root>/CLAUDE.local.md

A rule file may open with YAML frontmatter naming `paths:` -- a list (or a
comma string) of globs. WITH paths it loads only when Claude reads a
matching file ("on demand"); WITHOUT paths it loads at the start of every
session, the same as CLAUDE.md itself. `@path` imports inside a file expand
relative to that file, depth capped at 4, and are never read out of a code
span or fenced block.

Safety is the whole point of this module, not an afterthought: every path
that arrives over HTTP is realpath-resolved and checked against the exact
set of files this module manages (the always-on slots, or a `.md` file
inside one of the two rules directories) before anything touches disk --
anything else is a ValueError, which the caller turns into a 400. The
managed policy file is never written to. `write_file` will not touch
AGENTS.md either: that file's generated block is the only part of it this
module ever writes, and only `sync_agents_md` writes it, never a caller's
own text. A new rule never overwrites one already at that path, and a move
never overwrites one already at the destination.

python3 test_rules.py -- fake HOME, temp repos, no real ~/.claude ever
touched, no real `claude` CLI ever called."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time

import telemetry

MANAGED_POLICY = "/Library/Application Support/ClaudeCode/CLAUDE.md"

# Same isolation guardrails.REVIEW_CMD uses -- no tools, no session, nothing
# but the prompt handed to it and the JSON it hands back.
REVIEW_CMD = ["claude", "-p", "--model", "sonnet", "--no-session-persistence",
              "--setting-sources", "", "--strict-mcp-config", "--tools", ""]

_AGENTS_START = "<!-- podium:rules:start -->"
_AGENTS_END = "<!-- podium:rules:end -->"
_AGENTS_NOTE = ("<!-- generated from .claude/rules/ by Podium -- rules with "
                "`paths:` frontmatter are Claude-only and are not listed "
                "here; edit the rules, not this block. -->")

_HEADING_RE = re.compile(r"^#\s+(.+)$", re.M)
# an @ not itself escaped by a backtick either side, followed by anything
# that is not whitespace or a backtick -- the token Claude Code treats as an
# import path.
_IMPORT_RE = re.compile(r"(?<![\w`@])@([^\s`]+)")

_REVIEW_CACHE = {}


# ---------------------------------------------------------------- locations

def _home():
    return os.path.expanduser("~")


def _user_claude_md():
    return os.path.join(_home(), ".claude", "CLAUDE.md")


def _user_rules_dir():
    return os.path.join(_home(), ".claude", "rules")


def _project_claude_md(root):
    return os.path.join(root, "CLAUDE.md")


def _project_dot_claude_md(root):
    return os.path.join(root, ".claude", "CLAUDE.md")


def _project_local_md(root):
    return os.path.join(root, "CLAUDE.local.md")


def _project_agents_md(root):
    return os.path.join(root, "AGENTS.md")


def _project_rules_dir(root):
    return os.path.join(root, ".claude", "rules")


def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# -------------------------------------------------------------------- text

def _split_frontmatter(text):
    """(fm, body). `fm` holds only the one key this module reads --
    `paths`, as a list -- from a YAML list, an inline `[a, b]`, or a bare
    comma string. Anything else in the frontmatter is simply not returned;
    it is the author's and stays in `body` for nothing, since a rewrite
    always writes frontmatter fresh from the paths this module was given."""
    if not text.startswith("---"):
        return {}, text
    lines = text.split("\n")
    if lines[0].strip() != "---":
        return {}, text
    closing = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            closing = i
            break
    if closing is None:
        return {}, text
    fm_lines = lines[1:closing]
    body = "\n".join(lines[closing + 1:])
    if body.startswith("\n"):
        body = body[1:]
    paths = []
    i = 0
    while i < len(fm_lines):
        stripped = fm_lines[i].strip()
        if stripped.startswith("paths:"):
            rest = stripped[len("paths:"):].strip()
            if rest.startswith("[") and rest.endswith("]"):
                inner = rest[1:-1]
                paths = [p.strip().strip('"').strip("'")
                        for p in inner.split(",") if p.strip()]
            elif rest:
                paths = [p.strip().strip('"').strip("'")
                        for p in rest.split(",") if p.strip()]
            else:
                j = i + 1
                while j < len(fm_lines) and fm_lines[j].strip().startswith("-"):
                    item = fm_lines[j].strip()[1:].strip().strip('"').strip("'")
                    if item:
                        paths.append(item)
                    j += 1
                i = j - 1
        i += 1
    return {"paths": paths}, body


def _compose_rule(title, body, paths):
    body = (body or "").rstrip("\n")
    text = ""
    if paths:
        text += "---\npaths:\n"
        for p in paths:
            text += f'  - "{p}"\n'
        text += "---\n\n"
    title = (title or "").strip()
    text += f"# {title}\n\n{body}\n" if title else f"{body}\n"
    return text


def _title_of(text):
    _, body = _split_frontmatter(text)
    m = _HEADING_RE.search(body)
    return m.group(1).strip() if m else None


def _line_byte_counts(text):
    data = text.encode("utf-8", errors="replace")
    lines = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
    return lines, len(data)


def _find_imports(text):
    """@path tokens outside fenced code blocks and inline code spans --
    Claude Code does not treat either as an import."""
    out, in_fence = [], False
    for line in text.split("\n"):
        s = line.strip()
        if s.startswith("```") or s.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        clean = re.sub(r"`[^`]*`", "", line)
        out.extend(m.group(1) for m in _IMPORT_RE.finditer(clean))
    return out


def _shown(path, root=None):
    """The ~-relative or root-relative form of a path -- whichever one it is
    actually under. Falls back to the path itself for anything else (an
    ancestor directory above both, in effective())."""
    rp = os.path.realpath(path)
    if root:
        rroot = os.path.realpath(root)
        if rp == rroot or rp.startswith(rroot + os.sep):
            rel = os.path.relpath(rp, rroot)
            return rel if rel != "." else os.path.basename(rp)
    rhome = os.path.realpath(_home())
    if rp == rhome or rp.startswith(rhome + os.sep):
        return "~" + rp[len(rhome):]
    return path


def _slugify(title):
    s = re.sub(r"[^a-z0-9]+", "-", (title or "").strip().lower()).strip("-")
    return (s or "rule")[:60].strip("-") or "rule"


def _unique_path(dirpath, slug):
    path = os.path.join(dirpath, slug + ".md")
    if not os.path.exists(path):
        return path
    n = 2
    while True:
        path = os.path.join(dirpath, f"{slug}-{n}.md")
        if not os.path.exists(path):
            return path
        n += 1


def _rule_files(rules_dir):
    if not os.path.isdir(rules_dir):
        return []
    out = []
    for top, dirs, files in os.walk(rules_dir):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for f in sorted(files):
            if f.endswith(".md"):
                out.append(os.path.join(top, f))
    return out


def _read(path):
    try:
        with open(path, errors="replace") as f:
            return f.read()
    except OSError:
        return None


def _atomic_write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, path)


# ------------------------------------------------------------------ rows

def _file_row(scope, kind, path, root, rel=None):
    text = _read(path)
    exists = text is not None
    title = os.path.basename(path)
    paths = []
    lines = bts = 0
    if exists:
        fm, body = _split_frontmatter(text)
        paths = fm.get("paths", [])
        # an always-on file is named by its filename: AGENTS.md's first
        # heading is a rule inside the generated block, which made the row
        # read as that rule
        m = _HEADING_RE.search(body) if kind == "rule" else None
        if m:
            title = m.group(1).strip()
        lines, bts = _line_byte_counts(text)
    return {"scope": scope, "kind": kind, "path": path, "shown": _shown(path, root),
            "rel": rel, "title": title, "paths": paths, "lines": lines,
            "bytes": bts, "exists": exists}


def list_rules(root):
    """Every always-on file slot (even missing ones, so the app can offer to
    create them) plus every rule, global then project then local."""
    rows = [_file_row("global", "file", _user_claude_md(), root)]
    rules_dir = _user_rules_dir()
    for p in _rule_files(rules_dir):
        rows.append(_file_row("global", "rule", p, root,
                              rel=os.path.relpath(p, rules_dir)))

    rows.append(_file_row("project", "file", _project_claude_md(root), root))
    dot = _project_dot_claude_md(root)
    if os.path.isfile(dot):
        rows.append(_file_row("project", "file", dot, root))
    rows.append(_file_row("project", "file", _project_agents_md(root), root))
    prules_dir = _project_rules_dir(root)
    for p in _rule_files(prules_dir):
        rows.append(_file_row("project", "rule", p, root,
                              rel=os.path.relpath(p, prules_dir)))

    rows.append(_file_row("local", "file", _project_local_md(root), root))
    return rows


# --------------------------------------------------------------- resolving

def _allowed_file_slots(root):
    """realpath -> scope, for every always-on file this module knows about."""
    return {
        os.path.realpath(MANAGED_POLICY): "managed",
        os.path.realpath(_user_claude_md()): "global",
        os.path.realpath(_project_claude_md(root)): "project",
        os.path.realpath(_project_dot_claude_md(root)): "project",
        os.path.realpath(_project_local_md(root)): "local",
        os.path.realpath(_project_agents_md(root)): "project",
    }


def _rule_dir_for(root, rp):
    """(scope, rules_dir_realpath) if `rp` (already realpath'd) is a .md
    file inside one of the two rules directories, else (None, None)."""
    if not rp.endswith(".md"):
        return None, None
    for scope, rules_dir in (("global", _user_rules_dir()),
                             ("project", _project_rules_dir(root))):
        rd = os.path.realpath(rules_dir)
        if rp == rd or rp.startswith(rd + os.sep):
            return scope, rd
    return None, None


def _resolve(root, raw_path):
    """realpath `raw_path` and require it to be an always-on file slot or a
    rule -- the one check every path arriving over HTTP has to pass before
    it touches this module's read or write side. Anything else is a
    ValueError, never a best guess."""
    if not raw_path:
        raise ValueError("no path given")
    rp = os.path.realpath(os.path.expanduser(str(raw_path)))
    if rp in _allowed_file_slots(root):
        return rp
    scope, _ = _rule_dir_for(root, rp)
    if scope:
        return rp
    raise ValueError("that path is not one this server manages")


# ------------------------------------------------------------------- reads

def read(root, path):
    rp = _resolve(root, path)
    with open(rp, errors="replace") as f:
        return f.read()


# ------------------------------------------------------------------ writes

def write_rule(root, scope, title, body, paths, path=None):
    """Create or edit one rule. A new rule's filename is a slug of its
    title, `-2`/`-3`... appended rather than ever overwriting an existing
    file; editing an existing rule (`path` given) keeps its path exactly.
    Returns the path written."""
    if scope not in ("global", "project"):
        raise ValueError("a rule lives in global or project scope")
    rules_dir = _user_rules_dir() if scope == "global" else _project_rules_dir(root)
    clean_paths = [p.strip() for p in (paths or []) if isinstance(p, str) and p.strip()]
    text = _compose_rule(title, body, clean_paths)

    if path:
        target = _resolve(root, path)
        found_scope, _ = _rule_dir_for(root, target)
        if found_scope != scope:
            raise ValueError("that rule is not in this scope")
    else:
        os.makedirs(rules_dir, exist_ok=True)
        target = _unique_path(rules_dir, _slugify(title))

    _atomic_write(target, text)
    if scope == "project":
        sync_agents_md(root)
    return target


def write_file(root, path, text):
    """Write one of the always-on files -- never the managed policy (it is
    read-only). AGENTS.md is yours to edit around the generated block: the
    text is written, then the block between the markers is regenerated from
    .claude/rules/, so an edit inside the block is replaced rather than kept
    (it would be lost on the next rule save anyway) and removing the markers
    just puts the block back at the end. Creating a file is allowed."""
    rp = _resolve(root, path)
    slot = _allowed_file_slots(root).get(rp)
    if slot is None:
        raise ValueError("that is not an always-on file, it's a rule -- use write_rule")
    if rp == os.path.realpath(MANAGED_POLICY):
        raise ValueError("the managed policy file is read-only")
    _atomic_write(rp, text or "")
    if rp == os.path.realpath(_project_agents_md(root)):
        sync_agents_md(root)
    return rp


def delete_rule(root, path):
    """Remove a rule file (and it alone -- CLAUDE.md/AGENTS.md are never
    deleted here; the caller empties them with write_file instead)."""
    rp = _resolve(root, path)
    scope, _ = _rule_dir_for(root, rp)
    if not scope or not os.path.isfile(rp):
        raise ValueError("that is not a rule this server manages")
    os.remove(rp)
    if scope == "project":
        sync_agents_md(root)


def move_rule(root, path, to_scope):
    """Move a rule between global and project rules directories, preserving
    its relative position under the rules dir. Never overwrites whatever is
    already at the destination."""
    if to_scope not in ("global", "project"):
        raise ValueError("a rule moves to global or project scope")
    rp = _resolve(root, path)
    src_scope, src_dir = _rule_dir_for(root, rp)
    if not src_scope or not os.path.isfile(rp):
        raise ValueError("that is not a rule this server manages")
    dest_dir = _user_rules_dir() if to_scope == "global" else _project_rules_dir(root)
    rel = os.path.relpath(rp, src_dir)
    dest = os.path.join(os.path.realpath(dest_dir), rel)
    if os.path.realpath(dest) == rp:
        raise ValueError("it is already there")
    if os.path.exists(dest):
        raise ValueError("a rule already exists at that name in that scope")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.move(rp, dest)
    if src_scope == "project" or to_scope == "project":
        sync_agents_md(root)
    return dest


# --------------------------------------------------------------- AGENTS.md

def _project_rules_for_agents(root):
    """(rel, title, body) for every project rule WITHOUT `paths:`, rel
    order -- path-scoped rules are Claude-only and never appear here."""
    rules_dir = _project_rules_dir(root)
    out = []
    for p in _rule_files(rules_dir):
        text = _read(p)
        if text is None:
            continue
        fm, body = _split_frontmatter(text)
        if fm.get("paths"):
            continue
        rel = os.path.relpath(p, rules_dir)
        title = _title_of(text) or os.path.basename(p)
        out.append((rel, title, body.strip()))
    out.sort(key=lambda t: t[0])
    return out


def _generated_block(root):
    parts = [_AGENTS_NOTE]
    for _, title, body in _project_rules_for_agents(root):
        # the rule's own "# title" line would sit under our "## title"
        m = _HEADING_RE.match(body.lstrip())
        if m and m.group(1).strip() == title:
            body = body.lstrip()[m.end():].lstrip("\n")
        parts.append(f"## {title}\n\n{body}".rstrip())
    return "\n\n".join(parts) + "\n"


def _agents_md_split(text):
    """(before, block_or_None, after) around the markers -- block is
    stripped of its surrounding newlines so a fresh render compares equal to
    what is actually stored."""
    si = text.find(_AGENTS_START)
    if si < 0:
        return text, None, ""
    ei = text.find(_AGENTS_END, si)
    if ei < 0:
        return text, None, ""
    before = text[:si]
    block = text[si + len(_AGENTS_START):ei].strip("\n")
    after = text[ei + len(_AGENTS_END):]
    return before, block, after


def sync_agents_md(root):
    """Rewrite ONLY the generated block in <root>/AGENTS.md, byte for byte
    everywhere else. Creates the file (and the markers, with the one-line
    note) the first time there is nothing to rewrite into."""
    path = _project_agents_md(root)
    text = _read(path) or ""
    before, block, after = _agents_md_split(text)
    fresh = _generated_block(root).strip("\n")
    if block is None:
        sep = "" if not text else ("\n" if text.endswith("\n") else "\n\n")
        new_text = text + sep + _AGENTS_START + "\n" + fresh + "\n" + _AGENTS_END + "\n"
    elif block == fresh:
        return path
    else:
        new_text = before + _AGENTS_START + "\n" + fresh + "\n" + _AGENTS_END + after
    _atomic_write(path, new_text)
    return path


def agents_md_state(root):
    path = _project_agents_md(root)
    text = _read(path)
    exists = text is not None
    _, block, _ = _agents_md_split(text or "")
    fresh = _generated_block(root).strip("\n")
    synced = exists and block is not None and block == fresh
    claude_md = (os.path.isfile(_project_claude_md(root))
                or os.path.isfile(_project_dot_claude_md(root))
                or os.path.isfile(_project_local_md(root)))
    return {"exists": exists, "synced": synced, "claude_md": claude_md,
            "doubled": not claude_md}


# -------------------------------------------------------------- effective

def _git_root(cwd):
    """git's top level of cwd, or cwd itself for a plain folder with no
    repo -- the same fallback mapui's routes give a plain-folder project."""
    try:
        out = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return cwd
    return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else cwd


def _ancestor_dirs(path):
    """Every directory from the filesystem root down to `path`, root first
    -- the order CLAUDE.md/CLAUDE.local.md load in."""
    path = os.path.realpath(path)
    out, cur = [], path
    while True:
        out.append(cur)
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    out.reverse()
    return out


def _rules_dir_entries(rules_dir, scope, root):
    out = []
    for p in _rule_files(rules_dir):
        text = _read(p)
        if text is None:
            continue
        fm, _ = _split_frontmatter(text)
        globs = fm.get("paths", [])
        lines, bts = _line_byte_counts(text)
        out.append({"path": p, "shown": _shown(p, root), "scope": scope, "kind": "rule",
                    "when": "on demand" if globs else "start", "lines": lines,
                    "bytes": bts, "paths": globs, "imported_by": None})
    return out


def effective(cwd):
    """The full load order for one agent's cwd: managed policy, the user's
    CLAUDE.md and rules, every ancestor directory's CLAUDE.md family (root
    of the filesystem first, cwd last), AGENTS.md only when no CLAUDE.md
    exists anywhere in that chain, then the project's rules."""
    cwd = os.path.realpath(os.path.expanduser(str(cwd)))
    root = _git_root(cwd)
    files, warnings, added = [], [], set()

    def add_file(path, scope):
        rp = os.path.realpath(path)
        if rp in added:
            return None
        text = _read(rp)
        if text is None:
            return None
        added.add(rp)
        lines, bts = _line_byte_counts(text)
        shown = _shown(rp, root)
        row = {"path": rp, "shown": shown, "scope": scope, "kind": "file",
               "when": "start", "lines": lines, "bytes": bts, "paths": [],
               "imported_by": None}
        files.append(row)
        if lines > 200:
            warnings.append(f"{shown} is over 200 lines")
        base = os.path.dirname(rp)
        for token in _find_imports(text):
            target = os.path.realpath(os.path.join(base, os.path.expanduser(token)))
            if not os.path.isfile(target):
                warnings.append(f"@{token} in {shown} does not resolve")
        files.extend(_expand_imports(rp, text, root, added))
        return row

    add_file(MANAGED_POLICY, "managed")
    add_file(_user_claude_md(), "global")
    files.extend(_rules_dir_entries(_user_rules_dir(), "global", root))

    ancestors = _ancestor_dirs(cwd)
    any_claude_md = False
    for d in ancestors:
        scope = "project" if d == root else "ancestor"
        got_c = add_file(os.path.join(d, "CLAUDE.md"), scope)
        got_dc = add_file(os.path.join(d, ".claude", "CLAUDE.md"), scope)
        got_l = add_file(os.path.join(d, "CLAUDE.local.md"),
                         "local" if d == root else "ancestor")
        if got_c or got_dc or got_l:
            any_claude_md = True

    if not any_claude_md:
        for d in ancestors:
            scope = "project" if d == root else "ancestor"
            add_file(os.path.join(d, "AGENTS.md"), scope)
            add_file(os.path.join(d, ".claude", "AGENTS.md"), scope)
        if _rule_files(_project_rules_dir(root)):
            warnings.append("no CLAUDE.md here, so AGENTS.md is read too and "
                            "doubles the unscoped project rules")

    files.extend(_rules_dir_entries(_project_rules_dir(root), "project", root))

    return {"files": files, "total_lines": sum(f["lines"] for f in files),
            "warnings": warnings}


def _expand_imports(path, text, root, visited, depth=0):
    """Rows for every @import a file pulls in, relative to the IMPORTING
    file, depth capped at 4 -- Claude Code's own limit -- and never twice."""
    rows = []
    if depth >= 4:
        return rows
    base = os.path.dirname(path)
    for token in _find_imports(text):
        target = os.path.realpath(os.path.join(base, os.path.expanduser(token)))
        if target in visited:
            continue
        itext = _read(target)
        if itext is None:
            continue
        visited.add(target)
        lines, bts = _line_byte_counts(itext)
        rows.append({"path": target, "shown": _shown(target, root), "scope": "import",
                    "kind": "file", "when": "start", "lines": lines, "bytes": bts,
                    "paths": [], "imported_by": _shown(path, root)})
        rows.extend(_expand_imports(target, itext, root, visited, depth + 1))
    return rows


# ---------------------------------------------------------------- review

def _parse_json_object(text):
    """The first {...} JSON object in text, tolerating a code fence around
    it -- the same tolerant parse guardrails.py's model calls use."""
    text = (text or "").strip()
    start = text.find("{")
    if start < 0:
        raise RuntimeError("the model did not return JSON")
    try:
        got, _ = json.JSONDecoder(strict=False).raw_decode(text, start)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"the model's JSON did not parse: {e}") from e
    if not isinstance(got, dict):
        raise RuntimeError("the model did not return a JSON object")
    return got


def _concat_start_files(cwd):
    parts = []
    for f in effective(cwd)["files"]:
        if f.get("when") != "start":
            continue
        text = _read(f["path"])
        if text is None:
            continue
        parts.append(f"=== {f['shown']} ===\n{text}")
    return "\n\n".join(parts)[:80000]


def _review_prompt(blob):
    return (
        "You are reviewing the instructions an AI coding agent reads at the "
        "start of every session in this repo -- its CLAUDE.md/AGENTS.md "
        "files and always-on rules. Find real problems only: two files that "
        "contradict each other, a rule duplicated in more than one place, a "
        "rule so vague it gives no actual instruction, or one that looks "
        "stale (names a file, tool or decision that no longer seems live). "
        "An empty list is a fine answer if nothing is actually wrong.\n\n"
        f"{blob}\n\n"
        "Reply with STRICT JSON and nothing else -- no code fence, no prose "
        "around it:\n"
        '{"issues": [{"kind": "conflict, duplicate, vague, or stale", '
        '"files": ["the === path === shown above"], '
        '"quote": "the exact text", "why": "one sentence", '
        '"suggest": "one sentence"}]}'
    )


def _call_claude(cmd, prompt, cwd, timeout):
    done = telemetry.run_model("rules.review", cmd, prompt, timeout, cwd=cwd)
    if done.returncode:
        raise RuntimeError((done.stderr or "the model call failed").strip()[-300:])
    return done.stdout


def review(cwd, run=None):
    """Ask an isolated model to review the files an agent at `cwd` actually
    loads at the start of a session. Cached in memory by the sha256 of what
    was sent, so re-opening the panel does not re-spend a model call on
    nothing having changed. `run` is an injectable fn(prompt) -> str, for
    tests -- never call the real `claude` CLI from one."""
    blob = _concat_start_files(cwd)
    key = hashlib.sha256(blob.encode()).hexdigest()
    cached = _REVIEW_CACHE.get(key)
    if cached is not None:
        return cached
    prompt = _review_prompt(blob)
    out = run(prompt) if run else _call_claude(REVIEW_CMD, prompt, tempfile.gettempdir(), 180)
    parsed = _parse_json_object(out)
    raw_issues = parsed.get("issues")
    issues = []
    for it in raw_issues if isinstance(raw_issues, list) else []:
        if not isinstance(it, dict) or it.get("kind") not in (
                "conflict", "duplicate", "vague", "stale"):
            continue
        raw_files = it.get("files")
        # the model echoes the "=== path ===" labels the files were given under
        files = [str(x).strip().strip("=").strip() for x in raw_files if isinstance(x, str)] \
            if isinstance(raw_files, list) else []
        issues.append({"kind": it["kind"], "files": files,
                       "quote": str(it.get("quote", ""))[:2000],
                       "why": str(it.get("why", ""))[:500],
                       "suggest": str(it.get("suggest", ""))[:500]})
    result = {"issues": issues, "at": _now_iso()}
    _REVIEW_CACHE[key] = result
    return result
