#!/usr/bin/env python3
"""Tests for memory.py. Assert-based; run directly with `python3 test_memory.py`.

Every test uses its own tempfile.mkdtemp() database and, where relevant, its
own fixture transcript/claude-mem files. Nothing here ever opens the real
~/.midiai/memory.db, reads ~/.claude or ~/.claude-mem, or shells out to the
real `claude` CLI -- summarize() is always called with a fake `run`."""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import memory


def _tmp_db():
    d = tempfile.mkdtemp(prefix="midiai-memtest-")
    return os.path.join(d, "memory.db"), d


def _jsonl(d):
    return json.dumps(d).encode()


def test_project_for():
    assert memory.project_for("") == ""
    assert memory.project_for(None) == ""
    assert memory.project_for("/Users/x/myproj") == "myproj"
    assert memory.project_for("/Users/x/myproj/") == "myproj"
    assert memory.project_for("/Users/x/repo/.claude/worktrees/branch-1/sub") == "repo"
    assert memory.project_for("/Users/x/repo/.claude/worktrees/branch-1/") == "repo"
    print("test_project_for ok")


def test_index_transcript():
    db_path, tmp = _tmp_db()
    conn = memory.connect(db_path)
    try:
        transcript_dir = os.path.join(tmp, "transcripts")
        os.makedirs(transcript_dir, exist_ok=True)
        path = os.path.join(transcript_dir, "sess-abc.jsonl")

        lines = [
            {"type": "user", "cwd": "/Users/x/proj", "timestamp": "2026-09-23T10:00:00.000Z",
             "message": {"content": "Fix the queue bug"}},
            {"type": "assistant", "timestamp": "2026-09-23T10:00:05.000Z",
             "message": {"content": [{"type": "text", "text": "Looking into it"}]}},
            {"type": "assistant", "timestamp": "2026-09-23T10:00:10.000Z",
             "message": {"content": [{"type": "tool_use", "name": "Read",
                                       "input": {"file_path": "push_cc.py"}}]}},
            {"type": "assistant", "isSidechain": True, "timestamp": "2026-09-23T10:00:11.000Z",
             "message": {"content": [{"type": "text", "text": "sidechain text"}]}},
            {"type": "user", "isMeta": True, "timestamp": "2026-09-23T10:00:12.000Z",
             "message": {"content": "<local-command-caveat>"}},
            {"type": "user", "timestamp": "2026-09-23T10:00:13.000Z",
             "message": {"content": "<command-name>/compact</command-name>"}},
            {"type": "user", "timestamp": "2026-09-23T10:00:14.000Z",
             "message": {"content": "<system-reminder>ignore me</system-reminder>"}},
        ]
        full_line8 = {"type": "user", "timestamp": "2026-09-23T10:00:20.000Z",
                       "message": {"content": "second prompt"}}
        line8_bytes = _jsonl(full_line8)

        with open(path, "wb") as f:
            for d in lines:
                f.write(_jsonl(d))
                f.write(b"\n")
            f.write(line8_bytes[:30])   # half-written last line, no trailing newline

        added1 = memory.index_transcript(conn, path)
        assert added1 == 4, f"expected 4 entries from first pass, got {added1}"

        rows = conn.execute("SELECT kind, title FROM entries ORDER BY id").fetchall()
        kinds = [r["kind"] for r in rows]
        assert kinds == ["prompt", "reply", "tool", "prompt"], kinds
        assert rows[0]["title"] == "Fix the queue bug", rows[0]["title"]
        assert rows[3]["title"] == "/compact", rows[3]["title"]
        assert "Read:" in rows[2]["title"] and "push_cc.py" in rows[2]["title"], rows[2]["title"]

        sess = conn.execute("SELECT * FROM sessions WHERE id = 'sess-abc'").fetchone()
        assert sess["prompts"] == 2, sess["prompts"]
        assert sess["project"] == "proj", sess["project"]
        offset_after_1 = sess["offset"]

        # Second write: complete line 8, add line 9. Only the newly appended
        # bytes should be read on the next call -- not the first seven lines.
        line9 = {"type": "user", "timestamp": "2026-09-23T10:00:25.000Z",
                 "message": {"content": "third prompt"}}
        with open(path, "ab") as f:
            f.write(line8_bytes[30:])
            f.write(b"\n")
            f.write(_jsonl(line9))
            f.write(b"\n")

        added2 = memory.index_transcript(conn, path)
        assert added2 == 2, f"expected 2 new entries from second pass, got {added2}"

        total = conn.execute("SELECT COUNT(*) AS n FROM entries").fetchone()["n"]
        assert total == 6, total
        sess2 = conn.execute("SELECT * FROM sessions WHERE id = 'sess-abc'").fetchone()
        assert sess2["prompts"] == 4, sess2["prompts"]
        assert sess2["offset"] > offset_after_1

        # A third call over the same, now fully-consumed file adds nothing.
        assert memory.index_transcript(conn, path) == 0
        print("test_index_transcript ok")
    finally:
        conn.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_sweep_skips_dir():
    db_path, tmp = _tmp_db()
    conn = memory.connect(db_path)
    try:
        root = os.path.join(tmp, "projects")
        good_dir = os.path.join(root, "-Users-x-proj1")
        skip_dir = os.path.join(root, "claude-mem-observer-worker")
        os.makedirs(good_dir)
        os.makedirs(skip_dir)

        def write(path, cwd, text):
            with open(path, "wb") as f:
                f.write(_jsonl({"type": "user", "cwd": cwd,
                                 "timestamp": "2026-09-23T09:00:00.000Z",
                                 "message": {"content": text}}))
                f.write(b"\n")

        write(os.path.join(good_dir, "s1.jsonl"), "/Users/x/proj1", "hello from proj1")
        write(os.path.join(skip_dir, "s2.jsonl"), "/Users/x/observer", "should be skipped")

        added = memory.sweep(conn, root=root)
        assert added == 1, added
        titles = [r["title"] for r in conn.execute("SELECT title FROM entries").fetchall()]
        assert titles == ["hello from proj1"], titles
        sessions = [r["id"] for r in conn.execute("SELECT id FROM sessions").fetchall()]
        assert sessions == ["s1"], sessions

        # Repeat sweep must be stat-only: nothing new to add.
        assert memory.sweep(conn, root=root) == 0
        print("test_sweep_skips_dir ok")
    finally:
        conn.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_due_for_summary():
    db_path, tmp = _tmp_db()
    conn = memory.connect(db_path)
    try:
        now = datetime(2026, 9, 23, 12, 0, 0)

        def ts(dt):
            return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")

        def insert(id_, last_ts, prompts, summarized_ts=None, summary_tried_ts=None):
            conn.execute(
                "INSERT INTO sessions (id, project, cwd, first_ts, last_ts, transcript, "
                '"offset", prompts, summarized_ts, summary_tried_ts) '
                "VALUES (?, 'p', '/p', ?, ?, ?, 0, ?, ?, ?)",
                (id_, last_ts, last_ts, f"/tmp/{id_}.jsonl", prompts, summarized_ts, summary_tried_ts))

        insert("A", ts(now - timedelta(minutes=20)), 3)
        insert("tried_long_ago", ts(now - timedelta(minutes=22)), 3,
               summary_tried_ts=ts(now - timedelta(hours=2)))
        insert("A2", ts(now - timedelta(minutes=25)), 3)
        insert("too_recent", ts(now - timedelta(minutes=1)), 3)
        insert("too_few_prompts", ts(now - timedelta(minutes=20)), 1)
        insert("already_summarized", ts(now - timedelta(minutes=20)), 3,
               summarized_ts=ts(now - timedelta(minutes=15)))
        insert("tried_recently", ts(now - timedelta(minutes=20)), 3,
               summary_tried_ts=ts(now - timedelta(minutes=10)))
        insert("too_old", ts(now - timedelta(days=3)), 3)
        conn.commit()

        due = memory.due_for_summary(conn, now=now)
        assert due == ["A", "tried_long_ago", "A2"], due
        print("test_due_for_summary ok")
    finally:
        conn.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_summarize():
    db_path, tmp = _tmp_db()
    conn = memory.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO sessions (id, project, cwd, first_ts, last_ts, transcript, "
            '"offset", prompts) VALUES (?, ?, ?, ?, ?, ?, 0, ?)',
            ("sess-1", "proj1", "/p", "2026-09-23T09:00:00.000Z",
             "2026-09-23T09:30:00.000Z", "/tmp/sess-1.jsonl", 2))
        conn.executemany(
            "INSERT INTO entries (session, project, ts, kind, title, body, source) "
            "VALUES (?, 'proj1', ?, ?, ?, ?, 'transcript')",
            [
                ("sess-1", "2026-09-23T09:00:00.000Z", "prompt", "fix the bug", "please fix the bug"),
                ("sess-1", "2026-09-23T09:05:00.000Z", "reply", "looking", "looking into it now"),
                ("sess-1", "2026-09-23T09:10:00.000Z", "tool", "Edit: memory.py", '{"file_path": "memory.py"}'),
                ("sess-1", "2026-09-23T09:30:00.000Z", "prompt", "thanks", "thanks, looks good"),
            ])
        conn.commit()

        def fake_run_ok(prompt):
            assert "fix the bug" in prompt
            assert "Edit: memory.py" in prompt
            return ("TITLE: Fixed the bug\n\n**Request**\n- fix a bug\n**Done**\n- fixed it\n"
                     "**Learned**\n- n/a\n**Next**\n- ship it\n")

        title = memory.summarize(conn, "sess-1", run=fake_run_ok)
        assert title == "Fixed the bug", title
        summaries = conn.execute(
            "SELECT * FROM entries WHERE session = 'sess-1' AND kind = 'summary'").fetchall()
        assert len(summaries) == 1, len(summaries)
        assert summaries[0]["ext_id"] == "sum-sess-1", summaries[0]["ext_id"]
        assert summaries[0]["ts"] == "2026-09-23T09:30:00.000Z", summaries[0]["ts"]
        sess = conn.execute("SELECT summarized_ts FROM sessions WHERE id='sess-1'").fetchone()
        assert sess["summarized_ts"] == "2026-09-23T09:30:00.000Z", sess["summarized_ts"]

        # Re-run replaces the previous summary rather than duplicating it.
        def fake_run_ok2(prompt):
            return "TITLE: Bug fixed for real\n\n**Request**\n- x\n**Done**\n- y\n**Learned**\n- z\n**Next**\n- w\n"

        title2 = memory.summarize(conn, "sess-1", run=fake_run_ok2)
        assert title2 == "Bug fixed for real", title2
        summaries2 = conn.execute(
            "SELECT * FROM entries WHERE session = 'sess-1' AND kind = 'summary'").fetchall()
        assert len(summaries2) == 1, len(summaries2)
        assert summaries2[0]["title"] == "Bug fixed for real", summaries2[0]["title"]

        # Failure path: never raises, marks the attempt tried, stores nothing.
        conn.execute(
            "INSERT INTO sessions (id, project, cwd, first_ts, last_ts, transcript, "
            '"offset", prompts) VALUES (?, ?, ?, ?, ?, ?, 0, ?)',
            ("sess-2", "proj1", "/p", "2026-09-23T09:00:00.000Z",
             "2026-09-23T09:30:00.000Z", "/tmp/sess-2.jsonl", 2))
        conn.commit()

        def fake_run_fail(prompt):
            raise RuntimeError("boom")

        before = conn.execute("SELECT summary_tried_ts FROM sessions WHERE id='sess-2'").fetchone()
        assert before["summary_tried_ts"] is None

        result = memory.summarize(conn, "sess-2", run=fake_run_fail)
        assert result is None, result
        after = conn.execute("SELECT summary_tried_ts FROM sessions WHERE id='sess-2'").fetchone()
        assert after["summary_tried_ts"] is not None
        no_summary = conn.execute(
            "SELECT * FROM entries WHERE session = 'sess-2' AND kind = 'summary'").fetchall()
        assert no_summary == [], no_summary
        print("test_summarize ok")
    finally:
        conn.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_import_claude_mem():
    db_path, tmp = _tmp_db()
    cm_path = os.path.join(tmp, "claude-mem.db")
    conn = memory.connect(db_path)
    try:
        src = sqlite3.connect(cm_path)
        src.execute("""
            CREATE TABLE observations (
                id INTEGER PRIMARY KEY, memory_session_id TEXT, project TEXT, text TEXT,
                type TEXT, title TEXT, subtitle TEXT, facts TEXT, narrative TEXT, created_at TEXT
            )
        """)
        src.execute("""
            CREATE TABLE session_summaries (
                id INTEGER PRIMARY KEY, memory_session_id TEXT, project TEXT, request TEXT,
                investigated TEXT, learned TEXT, completed TEXT, next_steps TEXT, created_at TEXT
            )
        """)
        src.execute(
            "INSERT INTO observations (id, memory_session_id, project, text, type, title, "
            "subtitle, facts, narrative, created_at) VALUES (1, 's1', 'proj1', 'raw text', "
            "'note', 'A title', 'A subtitle', ?, 'a narrative', '2026-09-20T00:00:00.000Z')",
            (json.dumps(["fact one", "fact two"]),))
        src.execute(
            "INSERT INTO observations (id, memory_session_id, project, text, type, title, "
            "subtitle, facts, narrative, created_at) VALUES (2, 's1', 'proj1', 'more text', "
            "'note', NULL, 'fallback title', 'not json', NULL, '2026-09-20T01:00:00.000Z')"
        )
        src.execute(
            "INSERT INTO session_summaries (id, memory_session_id, project, request, "
            "investigated, learned, completed, next_steps, created_at) VALUES (1, 's1', "
            "'proj1', 'Do the thing', 'checked X', 'learned Y', 'did Z', 'ship it', "
            "'2026-09-20T02:00:00.000Z')"
        )
        src.commit()
        src.close()

        counts = memory.import_claude_mem(conn, path=cm_path)
        assert counts == {"observations": 2, "summaries": 1}, counts

        obs1_id = conn.execute("SELECT id FROM entries WHERE ext_id = 'cm-obs-1'").fetchone()["id"]
        obs1 = memory.get(conn, obs1_id)
        assert obs1["kind"] == "observation", obs1
        assert obs1["source"] == "claude-mem", obs1
        assert obs1["title"] == "A title", obs1["title"]
        assert "fact one" in obs1["body"] and "fact two" in obs1["body"], obs1["body"]
        assert "a narrative" in obs1["body"], obs1["body"]

        obs2_id = conn.execute("SELECT id FROM entries WHERE ext_id = 'cm-obs-2'").fetchone()["id"]
        obs2 = memory.get(conn, obs2_id)
        assert obs2["title"] == "fallback title", obs2["title"]  # NULL title -> subtitle
        assert "more text" in obs2["body"], obs2["body"]          # non-JSON facts tolerated

        summ_id = conn.execute("SELECT id FROM entries WHERE ext_id = 'cm-sum-1'").fetchone()["id"]
        summ = memory.get(conn, summ_id)
        assert summ["kind"] == "summary", summ
        assert summ["title"] == "Do the thing", summ["title"]
        assert "**Request**" in summ["body"] and "ship it" in summ["body"], summ["body"]

        total_before = conn.execute("SELECT COUNT(*) AS n FROM entries").fetchone()["n"]
        counts2 = memory.import_claude_mem(conn, path=cm_path)
        assert counts2 == {"observations": 0, "summaries": 0}, counts2  # idempotent
        total_after = conn.execute("SELECT COUNT(*) AS n FROM entries").fetchone()["n"]
        assert total_before == total_after
        print("test_import_claude_mem ok")
    finally:
        conn.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_import_claude_mem_missing():
    db_path, tmp = _tmp_db()
    conn = memory.connect(db_path)
    try:
        counts = memory.import_claude_mem(conn, path=os.path.join(tmp, "nope.db"))
        assert counts == {"observations": 0, "summaries": 0}, counts
        print("test_import_claude_mem_missing ok")
    finally:
        conn.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_search():
    db_path, tmp = _tmp_db()
    conn = memory.connect(db_path)
    try:
        conn.executemany(
            "INSERT INTO entries (session, project, ts, kind, title, body, source) "
            "VALUES (?, ?, ?, ?, ?, ?, 'transcript')",
            [
                ("s1", "proj1", "2026-09-20T00:00:00.000Z", "summary", "Queue reorder work",
                 "Implemented drag to reorder the queue by its grip."),
                ("s2", "proj1", "2026-09-21T00:00:00.000Z", "observation", "Queue only",
                 "Something about a queue, nothing about the other thing."),
                ("s3", "proj2", "2026-09-22T00:00:00.000Z", "summary", "Reorder in proj2",
                 "Queue reorder happened here too, different project."),
            ])
        conn.commit()

        both = memory.search(conn, "queue reorder")
        assert {r["title"] for r in both} == {"Queue reorder work", "Reorder in proj2"}, both

        scoped = memory.search(conn, "queue reorder", project="proj1")
        assert [r["title"] for r in scoped] == ["Queue reorder work"], scoped

        no_tokens = memory.search(conn, "   ")
        assert isinstance(no_tokens, list)  # falls back to recent(), no crash

        # Must never raise on FTS-syntax-shaped input -- tokens are quoted.
        injected = memory.search(conn, 'foo" OR bar*')
        assert isinstance(injected, list)

        for r in both:
            assert set(r.keys()) == {"id", "session", "project", "ts", "kind", "title",
                                      "source", "snippet"}, r.keys()
        print("test_search ok")
    finally:
        conn.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_digest():
    db_path, tmp = _tmp_db()
    conn = memory.connect(db_path)
    try:
        assert memory.digest(conn, "proj1") == ""   # nothing indexed yet
        assert memory.digest(conn, "") == ""         # no project at all

        conn.execute(
            "INSERT INTO entries (session, project, ts, kind, title, body, source) "
            "VALUES ('s1', 'proj1', '2026-09-20T00:00:00.000Z', 'summary', 'Did the thing', "
            "'**Request**\nfix it\n**Done**\nfixed', 'transcript')")
        conn.commit()

        text = memory.digest(conn, "proj1")
        assert text != "", text
        assert "proj1" in text
        assert "Did the thing" in text
        assert "memory_search" in text
        print("test_digest ok")
    finally:
        conn.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_context_cli():
    db_path, tmp = _tmp_db()
    conn = memory.connect(db_path)
    conn.execute(
        "INSERT INTO entries (session, project, ts, kind, title, body, source) "
        "VALUES ('s1', 'myproj', '2026-09-20T00:00:00.000Z', 'summary', 'Shipped X', "
        "'body text', 'transcript')")
    conn.commit()
    conn.close()
    try:
        repo_dir = os.path.dirname(os.path.abspath(memory.__file__))
        env = dict(os.environ)
        env["MIDIAI_MEMORY_DB"] = db_path

        hook_input = json.dumps({"cwd": "/Users/x/myproj", "source": "startup"})
        result = subprocess.run([sys.executable, "memory.py", "context"], cwd=repo_dir,
                                 input=hook_input, env=env, capture_output=True, text=True,
                                 timeout=30)
        assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
        assert "Shipped X" in result.stdout, result.stdout

        result2 = subprocess.run([sys.executable, "memory.py", "context"], cwd=repo_dir,
                                  input="", env=env, capture_output=True, text=True, timeout=30)
        assert result2.returncode == 0, (result2.returncode, result2.stdout, result2.stderr)

        result3 = subprocess.run([sys.executable, "memory.py", "context"], cwd=repo_dir,
                                  input="not json at all {{{", env=env, capture_output=True,
                                  text=True, timeout=30)
        assert result3.returncode == 0, (result3.returncode, result3.stdout, result3.stderr)
        print("test_context_cli ok")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    test_project_for()
    test_index_transcript()
    test_sweep_skips_dir()
    test_due_for_summary()
    test_summarize()
    test_import_claude_mem()
    test_import_claude_mem_missing()
    test_search()
    test_digest()
    test_context_cli()
    print("ok")


if __name__ == "__main__":
    main()
