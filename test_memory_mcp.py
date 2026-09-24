#!/usr/bin/env python3
"""Tests for memory_mcp.py. Assert-based; run directly with `python3
test_memory_mcp.py`.

Builds a throwaway DB via memory.connect(path) and inserts fixture entries
directly through the entries table (the entries_fts triggers keep the FTS
index in sync, same as memory.py's own tests rely on). Then runs
memory_mcp.py as a real subprocess with PODIUM_MEMORY_DB pointed at that DB
and cwd set to a temp directory named after the fixture project (Claude Code
starts stdio servers in the session's working directory, which is how
project defaulting is meant to work), and drives it through a scripted
JSON-RPC conversation over its stdin/stdout pipes.

Never touches the real ~/.podium/memory.db, ~/.claude, or the real `claude`
CLI -- the subprocess only ever sees the fixture DB via the env override."""
import json
import os
import select
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import memory

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPT_PATH = os.path.join(REPO_DIR, "memory_mcp.py")


def _build_fixture_db(db_path):
    conn = memory.connect(db_path)
    ids = {}

    def insert(session, project, ts, kind, title, body, source="transcript"):
        cur = conn.execute(
            "INSERT INTO entries (session, project, ts, kind, title, body, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (session, project, ts, kind, title, body, source))
        return cur.lastrowid

    ids["summary"] = insert(
        "sess-1", "fixtureproj", "2026-09-20T00:00:00.000Z", "summary",
        "Queue reorder work",
        "**Request**\nfix the queue\n**Done**\nadded drag reorder\n"
        "**Learned**\nn/a\n**Next**\nship it")
    ids["observation"] = insert(
        "sess-1", "fixtureproj", "2026-09-20T01:00:00.000Z", "observation",
        "Imported observation", "some observation text about queue handling",
        source="claude-mem")
    ids["prompt"] = insert(
        "sess-2", "fixtureproj", "2026-09-21T10:00:00.000Z", "prompt",
        "Fix the queue bug", "please fix the queue bug for real")
    ids["reply"] = insert(
        "sess-2", "fixtureproj", "2026-09-21T10:00:05.000Z", "reply",
        "Looking into it", "looking into it now")
    ids["tool"] = insert(
        "sess-2", "fixtureproj", "2026-09-21T10:00:10.000Z", "tool",
        "Read: push_cc.py", '{"file_path": "push_cc.py"}')
    ids["other_project"] = insert(
        "sess-3", "otherproj", "2026-09-22T00:00:00.000Z", "summary",
        "Queue also in other project",
        "Queue reorder happened here too, different project.")

    conn.commit()
    conn.close()
    return ids


def _send(proc, msg):
    proc.stdin.write(json.dumps(msg) + "\n")
    proc.stdin.flush()


def _send_raw(proc, line):
    proc.stdin.write(line + "\n")
    proc.stdin.flush()


def _recv(proc, timeout=10):
    r, _, _ = select.select([proc.stdout], [], [], timeout)
    if not r:
        raise AssertionError("timed out waiting for a response from memory_mcp.py")
    line = proc.stdout.readline()
    assert line, "unexpected EOF from memory_mcp.py"
    return json.loads(line)


def test_memory_mcp():
    tmp_root = tempfile.mkdtemp(prefix="podium-mcptest-")
    db_path = os.path.join(tmp_root, "memory.db")
    proj_dir = os.path.join(tmp_root, "fixtureproj")
    os.makedirs(proj_dir)

    ids = _build_fixture_db(db_path)

    env = dict(os.environ)
    env["PODIUM_MEMORY_DB"] = db_path

    proc = subprocess.Popen(
        [sys.executable, SCRIPT_PATH], cwd=proj_dir, env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1)
    try:
        # initialize: echoes the client's protocolVersion.
        _send(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                      "params": {"protocolVersion": "2025-06-18"}})
        resp = _recv(proc)
        assert resp["id"] == 1, resp
        result = resp["result"]
        assert result["protocolVersion"] == "2025-06-18", result
        assert result["serverInfo"]["name"] == "podium-memory", result
        assert "tools" in result["capabilities"], result

        # notifications/initialized has no id -> must produce no response at
        # all. Proven by sending a ping right after and getting exactly the
        # ping's response back, not a stray line from the notification.
        _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        _send(proc, {"jsonrpc": "2.0", "id": 99, "method": "ping"})
        resp = _recv(proc)
        assert resp == {"jsonrpc": "2.0", "id": 99, "result": {}}, resp

        # tools/list -> the four tools, each with a description and schema.
        _send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        resp = _recv(proc)
        tools = {t["name"]: t for t in resp["result"]["tools"]}
        assert set(tools) == {"memory_search", "memory_recent", "memory_get",
                               "memory_session"}, tools
        for name, t in tools.items():
            assert t.get("description"), name
            assert t.get("inputSchema", {}).get("type") == "object", name

        # memory_search: default scope is the current project (fixtureproj,
        # from cwd), not otherproj's matching entry.
        _send(proc, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                      "params": {"name": "memory_search", "arguments": {"query": "queue"}}})
        resp = _recv(proc)
        text = resp["result"]["content"][0]["text"]
        assert "isError" not in resp["result"], resp
        assert f"#{ids['summary']}" in text, text
        assert "Queue reorder work" in text, text
        assert "fixtureproj" in text, text
        assert "otherproj" not in text, text
        assert "memory_get <id> for the full text" in text, text

        # all_projects=true reaches the otherproj entry too.
        _send(proc, {"jsonrpc": "2.0", "id": 31, "method": "tools/call",
                      "params": {"name": "memory_search",
                                 "arguments": {"query": "queue", "all_projects": True}}})
        resp = _recv(proc)
        text = resp["result"]["content"][0]["text"]
        assert "Queue also in other project" in text, text

        # memory_recent: same project defaulting, summary + observation kinds.
        _send(proc, {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                      "params": {"name": "memory_recent", "arguments": {}}})
        resp = _recv(proc)
        text = resp["result"]["content"][0]["text"]
        assert "Queue reorder work" in text, text
        assert "Imported observation" in text, text

        # memory_get: full body, not just the snippet.
        _send(proc, {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                      "params": {"name": "memory_get", "arguments": {"id": ids["summary"]}}})
        resp = _recv(proc)
        text = resp["result"]["content"][0]["text"]
        assert "isError" not in resp["result"], resp
        assert "ship it" in text, text  # only in the full body, not the snippet
        assert f"#{ids['summary']}" in text, text

        # memory_session: chronological, title-only timeline for sess-2.
        _send(proc, {"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                      "params": {"name": "memory_session", "arguments": {"session": "sess-2"}}})
        resp = _recv(proc)
        text = resp["result"]["content"][0]["text"]
        lines = text.splitlines()
        assert len(lines) == 3, lines
        assert "Fix the queue bug" in lines[0] and "[prompt]" in lines[0], lines
        assert "Looking into it" in lines[1] and "[reply]" in lines[1], lines
        assert "Read: push_cc.py" in lines[2] and "[tool]" in lines[2], lines
        assert "please fix the queue bug for real" not in text, text  # bodies omitted

        # bad tool args -> isError, not a JSON-RPC error.
        _send(proc, {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                      "params": {"name": "memory_search", "arguments": {}}})
        resp = _recv(proc)
        assert "error" not in resp, resp
        assert resp["result"]["isError"] is True, resp
        assert "query" in resp["result"]["content"][0]["text"], resp

        # no such id -> isError too.
        _send(proc, {"jsonrpc": "2.0", "id": 71, "method": "tools/call",
                      "params": {"name": "memory_get", "arguments": {"id": 9999999}}})
        resp = _recv(proc)
        assert resp["result"]["isError"] is True, resp

        # unknown method -> JSON-RPC error -32601.
        _send(proc, {"jsonrpc": "2.0", "id": 8, "method": "not/a/real/method"})
        resp = _recv(proc)
        assert "error" in resp, resp
        assert resp["error"]["code"] == -32601, resp

        # unparseable line -> -32700, id null.
        _send_raw(proc, "not json at all {{{")
        resp = _recv(proc)
        assert "error" in resp, resp
        assert resp["error"]["code"] == -32700, resp
        assert resp["id"] is None, resp

        # EOF on stdin -> clean exit 0.
        proc.stdin.close()
        returncode = proc.wait(timeout=10)
        assert returncode == 0, returncode
        print("test_memory_mcp ok")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
        shutil.rmtree(tmp_root, ignore_errors=True)


def main():
    test_memory_mcp()
    print("ok")


if __name__ == "__main__":
    main()
