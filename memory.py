#!/usr/bin/env python3
"""midiAI's own memory store -- SQLite + FTS5, replacing the claude-mem plugin.

Indexes every Claude Code transcript under ~/.claude/projects incrementally,
the same byte-offset trick push_cc.py uses for the pane's live history: each
session remembers how far it has read, so a sweep only ever touches new
bytes. Idle sessions get summarised by a one-shot `claude -p` call with every
side effect turned off (no session, no settings, no MCP, no tools), so
summarising a conversation can never feed a new turn back into the index it
is building. claude-mem's own SQLite export can be imported once, read-only,
so its history is not lost on the way out.

    python3 memory.py sweep                  reindex every transcript
    python3 memory.py import-claude-mem       one-time pull from claude-mem
    python3 memory.py summarize-due           summarise idle sessions
    python3 memory.py search "queue reorder"  full-text search
    python3 memory.py stats
    python3 memory.py context                 SessionStart hook: prints a digest

Every function here takes its own sqlite3.Connection -- mapui is a threaded
server, and two threads sharing one connection would step on each other's
transactions. Call connect() per caller, not once at import time.
"""
import argparse
import glob
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime

import telemetry

DB_PATH = os.environ.get("MIDIAI_MEMORY_DB") or os.path.expanduser("~/.midiai/memory.db")
PROJECTS_ROOT = os.path.expanduser("~/.claude/projects")

# Substrings of a ~/.claude/projects/<dir> name that are never indexed --
# claude-mem's own worker transcripts, which would otherwise index claude-mem
# talking to itself as if it were a real session.
SKIP_DIRS = ("claude-mem-observer",)

# -p with every side effect off: no transcript written, no settings sourced,
# no MCP servers, no tools, no session persisted. Verified against a live
# run -- summarising a session can never fire a hook or plugin that writes
# back into the index this module is building.
SUMMARY_CMD = ["claude", "-p", "--model", "haiku", "--no-session-persistence",
               "--setting-sources", "", "--strict-mcp-config", "--tools", ""]

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    project TEXT,
    cwd TEXT,
    first_ts TEXT,
    last_ts TEXT,
    transcript TEXT,
    "offset" INTEGER NOT NULL DEFAULT 0,
    prompts INTEGER NOT NULL DEFAULT 0,
    summarized_ts TEXT,
    summary_tried_ts TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_transcript ON sessions(transcript);

CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY,
    session TEXT,
    project TEXT,
    ts TEXT,
    kind TEXT NOT NULL CHECK(kind IN ('prompt', 'reply', 'tool', 'observation', 'summary')),
    title TEXT,
    body TEXT,
    source TEXT NOT NULL DEFAULT 'transcript',
    ext_id TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_entries_project_ts ON entries(project, ts);
CREATE INDEX IF NOT EXISTS idx_entries_session ON entries(session);

CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
    title, body, content='entries', content_rowid='id', tokenize='porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS entries_ai AFTER INSERT ON entries BEGIN
    INSERT INTO entries_fts(rowid, title, body) VALUES (new.id, new.title, new.body);
END;
CREATE TRIGGER IF NOT EXISTS entries_ad AFTER DELETE ON entries BEGIN
    INSERT INTO entries_fts(entries_fts, rowid, title, body) VALUES ('delete', old.id, old.title, old.body);
END;
CREATE TRIGGER IF NOT EXISTS entries_au AFTER UPDATE ON entries BEGIN
    INSERT INTO entries_fts(entries_fts, rowid, title, body) VALUES ('delete', old.id, old.title, old.body);
    INSERT INTO entries_fts(rowid, title, body) VALUES (new.id, new.title, new.body);
END;
"""


def connect(path=None):
    """Open the store, creating its schema on first use.

    Each caller gets its own connection -- mapui is threaded, and sqlite3
    connections are not safe to share across threads."""
    path = path or DB_PATH
    if path != ":memory:":
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def project_for(cwd):
    """A checkout's project name, worktrees folded back to their repo."""
    if not cwd:
        return ""
    cwd = cwd.rstrip("/")
    marker = "/.claude/worktrees/"
    if marker in cwd:
        cwd = cwd.split(marker, 1)[0].rstrip("/")
    return os.path.basename(cwd)


def _first_line(text, n):
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line[:n]
    return text.strip()[:n]


def _now_iso():
    # Naive UTC throughout, to match transcript timestamps (ISO-8601 with a
    # trailing Z) without an aware/naive datetime mismatch anywhere they are
    # compared -- see _parse_ts.
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _parse_ts(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.rstrip("Z"))
    except ValueError:
        return None


def index_transcript(conn, path):
    """Read one transcript from its stored byte offset to EOF, incrementally.

    Reuses push_cc.history's rules for what counts as user/assistant text:
    isSidechain, isMeta and isCompactSummary lines are skipped; a
    <command-name>x</command-name> wrapper becomes "/x"; other text starting
    with "<" is command stdout or a reminder, not something anyone typed;
    tool_result blocks in a user message are not user text."""
    session_id = os.path.splitext(os.path.basename(path))[0]
    existing = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    start = existing["offset"] if existing else 0
    try:
        size = os.path.getsize(path)
    except OSError:
        return 0

    reset = size < start
    if reset:
        start = 0
    if size <= start:
        if reset:
            with conn:
                conn.execute("DELETE FROM entries WHERE session = ? AND source = 'transcript'",
                              (session_id,))
                conn.execute('UPDATE sessions SET "offset" = 0 WHERE id = ?', (session_id,))
        return 0

    try:
        with open(path, "rb") as f:
            f.seek(start)
            chunk = f.read()
    except OSError:
        return 0

    cut = chunk.rfind(b"\n") + 1
    if not cut:
        return 0  # nothing but a half-written line yet

    cwd = existing["cwd"] if existing else None
    first_ts = existing["first_ts"] if existing else None
    last_ts = existing["last_ts"] if existing else None
    prompts_added = 0
    rows = []

    for raw in chunk[:cut].split(b"\n"):
        if not raw or (b'"user"' not in raw and b'"assistant"' not in raw):
            continue
        try:
            d = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        if d.get("isSidechain") or d.get("isMeta") or d.get("isCompactSummary"):
            continue
        dtype = d.get("type")
        if dtype not in ("user", "assistant"):
            continue

        if not cwd and d.get("cwd"):
            cwd = d["cwd"]
        ts = d.get("timestamp")
        if ts:
            if not first_ts:
                first_ts = ts
            last_ts = ts

        content = (d.get("message") or {}).get("content")
        if dtype == "user":
            if isinstance(content, str):
                parts = [content]
            elif isinstance(content, list):
                parts = [b.get("text", "") for b in content
                         if isinstance(b, dict) and b.get("type") == "text"]
            else:
                parts = []
            text = "\n".join(p for p in parts if p).strip()
            name = re.search(r"<command-name>(.*?)</command-name>", text)
            if name:
                text = name.group(1).strip()
            elif text.startswith("<"):
                continue  # command stdout, caveats, reminders
            if not text:
                continue
            rows.append((session_id, None, ts, "prompt", _first_line(text, 120),
                          text[:20000], "transcript", None))
            prompts_added += 1
        else:
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    text = block.get("text", "").strip()
                    if not text:
                        continue
                    rows.append((session_id, None, ts, "reply", _first_line(text, 120),
                                 text[:20000], "transcript", None))
                elif btype == "tool_use":
                    name = block.get("name") or "?"
                    inp = block.get("input") or {}
                    detail = next((str(inp[k]) for k in
                                   ("file_path", "command", "pattern", "url",
                                    "description", "query", "prompt")
                                   if inp.get(k)), "")
                    detail = " ".join(detail.split())[:140]
                    title = f"{name}: {detail}" if detail else name
                    rows.append((session_id, None, ts, "tool", title,
                                 json.dumps(inp)[:2000], "transcript", None))

    project = project_for(cwd) if cwd else (existing["project"] if existing else None)
    new_offset = start + cut
    new_prompts = (existing["prompts"] if existing else 0) + prompts_added

    with conn:
        if reset:
            conn.execute("DELETE FROM entries WHERE session = ? AND source = 'transcript'",
                          (session_id,))
        if rows:
            conn.executemany(
                "INSERT INTO entries (session, project, ts, kind, title, body, source, ext_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(s, project, ts, kind, title, body, source, ext_id)
                 for (s, _, ts, kind, title, body, source, ext_id) in rows])
        conn.execute("""
            INSERT INTO sessions (id, project, cwd, first_ts, last_ts, transcript, "offset", prompts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                project = excluded.project,
                cwd = excluded.cwd,
                first_ts = excluded.first_ts,
                last_ts = excluded.last_ts,
                transcript = excluded.transcript,
                "offset" = excluded."offset",
                prompts = excluded.prompts
        """, (session_id, project, cwd, first_ts, last_ts, path, new_offset, new_prompts))

    return len(rows)


def sweep(conn, root=PROJECTS_ROOT):
    """Index every transcript under root. Repeat sweeps are stat-only for
    anything already caught up, so this stays cheap to run often."""
    offsets = {r["transcript"]: r["offset"] for r in
               conn.execute('SELECT transcript, "offset" FROM sessions WHERE transcript IS NOT NULL')}
    added = 0
    for path in sorted(glob.glob(os.path.join(root, "*", "*.jsonl"))):
        if any(skip in os.path.basename(os.path.dirname(path)) for skip in SKIP_DIRS):
            continue
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        if size <= offsets.get(path, 0):
            continue
        try:
            added += index_transcript(conn, path)
        except Exception:
            continue  # one garbled file must not sink the whole sweep
    return added


def due_for_summary(conn, idle_s=600, min_prompts=2, max_age_s=2 * 86400, now=None):
    """Sessions worth summarising: gone quiet, not ancient, not just tried."""
    now = now or datetime.utcnow()
    rows = conn.execute(
        "SELECT id, last_ts, summarized_ts, summary_tried_ts FROM sessions "
        "WHERE last_ts IS NOT NULL AND prompts >= ?", (min_prompts,)).fetchall()
    due = []
    for r in rows:
        last = _parse_ts(r["last_ts"])
        if last is None:
            continue
        age = (now - last).total_seconds()
        if age < idle_s or age > max_age_s:
            continue
        if r["summarized_ts"]:
            sm = _parse_ts(r["summarized_ts"])
            if sm and sm >= last:
                continue
        if r["summary_tried_ts"]:
            tried = _parse_ts(r["summary_tried_ts"])
            if tried and (now - tried).total_seconds() < 3600:
                continue
        due.append((last, r["id"]))
    due.sort(key=lambda t: t[0], reverse=True)
    return [sid for _, sid in due]


def _run_summary_cmd(prompt):
    result = telemetry.run_model("memory.summary", SUMMARY_CMD, prompt, 180,
                                 cwd=tempfile.gettempdir())
    if result.returncode != 0:
        raise RuntimeError(f"summary command exited {result.returncode}: {result.stderr[:500]}")
    return result.stdout


def summarize(conn, session_id, run=None):
    """Ask the model for a short recap of one session and store it.

    Failure of any kind -- the subprocess, or a reply that does not start
    with TITLE: -- marks the attempt tried and returns None; it never
    raises, since a summariser that can crash the sweep that calls it is
    worse than no summariser."""
    run = run or _run_summary_cmd
    session = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if not session:
        return None

    convo_rows = conn.execute(
        "SELECT kind, body, ts FROM entries WHERE session = ? AND kind IN ('prompt', 'reply') "
        "ORDER BY ts DESC, id DESC", (session_id,)).fetchall()
    budget = 12000
    picked, used = [], 0
    for r in convo_rows:
        who = "User" if r["kind"] == "prompt" else "Assistant"
        line = f"{who}: {r['body']}"
        if picked and used + len(line) > budget:
            break
        picked.append(line)
        used += len(line)
    picked.reverse()  # newest-first budget, chronological order in the prompt
    convo = "\n\n".join(picked)

    tool_titles = [r["title"] for r in conn.execute(
        "SELECT title FROM entries WHERE session = ? AND kind = 'tool'", (session_id,))]
    counts = Counter(tool_titles)
    tools = "\n".join(f"- {t} x{n}" if n > 1 else f"- {t}" for t, n in counts.most_common()) or "(none)"

    prompt = (
        "Summarize this coding session for a memory store.\n\n"
        f"Conversation:\n{convo}\n\n"
        f"Tools used:\n{tools}\n\n"
        "Respond in exactly this format: a first line `TITLE: <one line>`, then "
        "markdown sections **Request**, **Done**, **Learned**, **Next**, each with "
        "short bullets."
    )

    try:
        output = run(prompt)
    except Exception:
        conn.execute("UPDATE sessions SET summary_tried_ts = ? WHERE id = ?",
                      (_now_iso(), session_id))
        conn.commit()
        return None

    lines = (output or "").splitlines()
    title, body_start = None, 0
    for i, line in enumerate(lines):
        if line.strip().upper().startswith("TITLE:"):
            title = line.split(":", 1)[1].strip()
            body_start = i + 1
            break
    if not title:
        conn.execute("UPDATE sessions SET summary_tried_ts = ? WHERE id = ?",
                      (_now_iso(), session_id))
        conn.commit()
        return None

    body = "\n".join(lines[body_start:]).strip()
    ext_id = f"sum-{session_id}"
    with conn:
        conn.execute("DELETE FROM entries WHERE session = ? AND kind = 'summary' AND source = 'transcript'",
                      (session_id,))
        conn.execute(
            "INSERT INTO entries (session, project, ts, kind, title, body, source, ext_id) "
            "VALUES (?, ?, ?, 'summary', ?, ?, 'transcript', ?) "
            "ON CONFLICT(ext_id) DO UPDATE SET title = excluded.title, body = excluded.body, ts = excluded.ts",
            (session_id, session["project"], session["last_ts"], title, body, ext_id))
        conn.execute("UPDATE sessions SET summarized_ts = ? WHERE id = ?",
                      (session["last_ts"], session_id))
    return title


def import_claude_mem(conn, path=os.path.expanduser("~/.claude-mem/claude-mem.db")):
    """One-time, idempotent pull from claude-mem's own SQLite export.

    Opened read-only by URI so a concurrent claude-mem process, if one is
    still running, is never at risk from this side."""
    if not os.path.exists(path):
        return {"observations": 0, "summaries": 0}

    src = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    try:
        obs_rows = src.execute(
            "SELECT id, memory_session_id, project, text, type, title, subtitle, facts, "
            "narrative, created_at FROM observations").fetchall()
    except sqlite3.OperationalError:
        obs_rows = []
    try:
        sum_rows = src.execute(
            "SELECT id, memory_session_id, project, request, investigated, learned, "
            "completed, next_steps, created_at FROM session_summaries").fetchall()
    except sqlite3.OperationalError:
        sum_rows = []
    src.close()

    obs_params = []
    for r in obs_rows:
        title = r["title"] or r["subtitle"] or r["type"] or ""
        parts = []
        if r["subtitle"]:
            parts.append(r["subtitle"])
        if r["narrative"]:
            parts.append(r["narrative"])
        if r["facts"]:
            try:
                facts = json.loads(r["facts"])
                if isinstance(facts, list) and facts:
                    parts.append("\n".join(f"- {x}" for x in facts))
            except (json.JSONDecodeError, TypeError, ValueError):
                pass  # tolerate non-JSON facts rather than dropping the observation
        if r["text"]:
            parts.append(r["text"])
        body = "\n\n".join(p for p in parts if p)
        obs_params.append((r["memory_session_id"], r["project"], r["created_at"],
                            "observation", title, body, "claude-mem", f"cm-obs-{r['id']}"))

    sum_params = []
    for r in sum_rows:
        req = r["request"] or ""
        title = _first_line(req, 120) if req else (r["project"] or "session summary")
        sections = []
        for label, key in (("Request", "request"), ("Investigated", "investigated"),
                            ("Learned", "learned"), ("Completed", "completed"),
                            ("Next", "next_steps")):
            if r[key]:
                sections.append(f"**{label}**\n{r[key]}")
        sum_params.append((r["memory_session_id"], r["project"], r["created_at"],
                            "summary", title, "\n\n".join(sections), "claude-mem",
                            f"cm-sum-{r['id']}"))

    insert_sql = ("INSERT OR IGNORE INTO entries (session, project, ts, kind, title, body, "
                  "source, ext_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)")

    def _count(kind):
        return conn.execute(
            "SELECT COUNT(*) AS n FROM entries WHERE source = 'claude-mem' AND kind = ?",
            (kind,)).fetchone()["n"]

    # Not conn.total_changes: the entries_fts triggers write several rows per
    # entries row into FTS5's own shadow tables, and those count too, so the
    # delta is not "how many entries were added". A before/after row count on
    # the base table is unaffected by that and stays correct under IGNORE.
    with conn:
        before = _count("observation")
        conn.executemany(insert_sql, obs_params)
        n_obs = _count("observation") - before

        before = _count("summary")
        conn.executemany(insert_sql, sum_params)
        n_sum = _count("summary") - before

    return {"observations": n_obs, "summaries": n_sum}


def search(conn, q, project=None, kinds=None, limit=20):
    """Full-text search. Tokens are always quoted before they reach FTS5, so
    user input like `foo" OR bar*` is just three literal words, never
    query syntax."""
    tokens = re.findall(r"\w+", q or "")
    if not tokens:
        return recent(conn, project=project, **({"kinds": kinds} if kinds is not None else {}),
                      limit=limit)

    match = " ".join(f'"{t}"' for t in tokens)
    sql = (
        "SELECT e.id, e.session, e.project, e.ts, e.kind, e.title, e.source, "
        "snippet(entries_fts, 1, '[', ']', '…', 20) AS snippet "
        "FROM entries_fts JOIN entries e ON e.id = entries_fts.rowid "
        "WHERE entries_fts MATCH ?"
    )
    params = [match]
    if project:
        sql += " AND e.project = ?"
        params.append(project)
    if kinds:
        sql += f" AND e.kind IN ({','.join('?' for _ in kinds)})"
        params.extend(kinds)
    # bm25() ranks lowest-is-best; fine to order by it without selecting it.
    sql += " ORDER BY bm25(entries_fts), e.ts DESC LIMIT ?"
    params.append(limit)

    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def recent(conn, project=None, kinds=("summary", "observation"), limit=20):
    sql = "SELECT id, session, project, ts, kind, title, body, source FROM entries WHERE 1=1"
    params = []
    if project:
        sql += " AND project = ?"
        params.append(project)
    if kinds:
        sql += f" AND kind IN ({','.join('?' for _ in kinds)})"
        params.extend(kinds)
    sql += " ORDER BY ts DESC LIMIT ?"
    params.append(limit)
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def get(conn, entry_id):
    row = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
    return dict(row) if row else None


def session_entries(conn, session, limit=500):
    rows = conn.execute(
        "SELECT id, session, project, ts, kind, title, body, source FROM entries "
        "WHERE session = ? ORDER BY ts ASC, id ASC LIMIT ?", (session, limit)).fetchall()
    return [dict(row) for row in rows]


def projects(conn):
    rows = conn.execute(
        "SELECT project, COUNT(*) AS entries, MAX(ts) AS last_ts FROM entries "
        "WHERE project IS NOT NULL AND project != '' GROUP BY project ORDER BY last_ts DESC"
    ).fetchall()
    return [dict(row) for row in rows]


def stats(conn):
    by_kind = {r["kind"]: r["n"] for r in
               conn.execute("SELECT kind, COUNT(*) AS n FROM entries GROUP BY kind")}
    by_source = {r["source"]: r["n"] for r in
                 conn.execute("SELECT source, COUNT(*) AS n FROM entries GROUP BY source")}
    sessions_n = conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"]
    db_file = ""
    for row in conn.execute("PRAGMA database_list"):
        if row["name"] == "main":
            db_file = row["file"] or ""
    try:
        size = os.path.getsize(db_file) if db_file else 0
    except OSError:
        size = 0
    return {"by_kind": by_kind, "by_source": by_source, "sessions": sessions_n, "bytes": size}


def digest(conn, project, limit=8, max_chars=3000):
    """The SessionStart context: a short recap of this project's memory, or
    "" when there isn't one yet."""
    if not project:
        return ""
    rows = recent(conn, project=project, kinds=("summary",), limit=limit)
    if not rows:
        rows = recent(conn, project=project, kinds=("observation",), limit=limit)
    if not rows:
        return ""

    lines = [f"# midiAI memory for {project} -- memory_search can dig deeper", ""]
    for r in rows:
        date = (r["ts"] or "")[:10]
        lines.append(f"- {date} {r['title']}")
        # the title is usually the request restated, so the lines worth the
        # space are the ones after it: what was done, learned, left next.
        # Section headers ("**Done**") and a line repeating the title go.
        title = (r["title"] or "").strip()
        body = [l.strip() for l in (r["body"] or "").splitlines() if l.strip()]
        body = [l for l in body
                if not re.fullmatch(r"\*\*[^*]+\*\*:?", l)
                and not (title and l.lstrip("-* ").startswith(title[:60]))]
        for bl in body[:2]:
            lines.append(f"  {bl[:200]}{'…' if len(bl) > 200 else ''}")
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "…"
    return text


def _cli_context():
    # A SessionStart hook: whatever goes wrong here, the session must still
    # start, so every failure is swallowed and this always exits 0.
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw and raw.strip() else {}
        project = project_for(data.get("cwd", ""))
        conn = connect()
        text = digest(conn, project)
        if text:
            print(text)
    except Exception:
        pass
    sys.exit(0)


def main():
    parser = argparse.ArgumentParser(description="midiAI memory store")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("sweep")

    p_import = sub.add_parser("import-claude-mem")
    p_import.add_argument("--path", default=os.path.expanduser("~/.claude-mem/claude-mem.db"))

    p_due = sub.add_parser("summarize-due")
    p_due.add_argument("--max", type=int, default=3)

    p_search = sub.add_parser("search")
    p_search.add_argument("q")
    p_search.add_argument("--project")
    p_search.add_argument("--limit", type=int, default=20)

    sub.add_parser("stats")
    sub.add_parser("context")

    args = parser.parse_args()

    if args.cmd == "context":
        _cli_context()
        return
    if not args.cmd:
        parser.print_help()
        return

    conn = connect()
    if args.cmd == "sweep":
        print(f"{sweep(conn)} entries added")
    elif args.cmd == "import-claude-mem":
        counts = import_claude_mem(conn, path=args.path)
        print(f"observations={counts['observations']} summaries={counts['summaries']}")
    elif args.cmd == "summarize-due":
        for sid in due_for_summary(conn)[:args.max]:
            title = summarize(conn, sid)
            print(f"{sid}: {title or 'failed'}")
    elif args.cmd == "search":
        for r in search(conn, args.q, project=args.project, limit=args.limit):
            print(f"[{r['kind']}] {r['title']}  ({r['project']}, {r['ts']})")
            print(f"    {r['snippet']}")
    elif args.cmd == "stats":
        print(json.dumps(stats(conn), indent=2))


if __name__ == "__main__":
    main()
