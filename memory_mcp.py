#!/usr/bin/env python3
"""Stdio MCP server exposing memory.py's SQLite+FTS5 store to an agent.

Replaces claude-mem's mem-search tools: a Claude Code session started with
this server registered can search midiAI's own memory (session summaries,
imported claude-mem observations, and every prompt/reply/tool call from
transcripts) mid-session instead of asking the user to look it up.

Protocol: newline-delimited JSON-RPC 2.0 on stdin/stdout, one message per
line. Nothing but a response line is ever written to stdout -- a stray
print() here would corrupt every message after it, since the client reads
stdout as the protocol stream, not a log. Anything worth logging goes to
stderr.

Every tool call opens its own memory.connect() and closes it before
returning, same discipline as mapui.py -- see memory.py's module docstring
for why each caller gets its own sqlite3.Connection.

    python3 memory_mcp.py     run the server (reads stdin, writes stdout)
"""
import json
import os
import sys

import memory

PROTOCOL_VERSION = "2025-06-18"
MAX_CHARS = 12000
_KINDS = ("summary", "observation", "prompt", "reply", "tool")


class _ToolError(Exception):
    """Raised by a tool handler for a bad argument -- caught in _tools_call
    and turned into a {isError: true} result, never a JSON-RPC error. A
    protocol-level error is for malformed JSON-RPC; a tool failing on its
    own arguments is not that."""


TOOLS = [
    {
        "name": "memory_search",
        "description": (
            "Full-text search over midiAI's memory: session summaries, "
            "imported claude-mem observations, and every prompt, reply, and "
            "tool call from past Claude Code transcripts. Scoped to the "
            "current project unless all_projects is set."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search terms."},
                "project": {"type": "string",
                            "description": "Limit to this project; overrides the default of the current project."},
                "all_projects": {"type": "boolean", "default": False,
                                  "description": "Search across every project instead of just the current one."},
                "kind": {"type": "string", "enum": list(_KINDS),
                         "description": "Limit to one kind of entry."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
            },
            "required": ["query"],
        },
    },
    {
        "name": "memory_recent",
        "description": (
            "List the most recent session summaries and observations for a "
            "project (defaulting to the current one), to catch up on what "
            "happened recently without a specific search term."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string",
                             "description": "Defaults to the current project (from cwd) when omitted."},
                "limit": {"type": "integer", "default": 10},
            },
        },
    },
    {
        "name": "memory_get",
        "description": (
            "Fetch the full title and body of one memory entry by id, when "
            "a memory_search or memory_recent snippet is not enough."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "integer", "description": "Entry id, as shown by memory_search/memory_recent."},
            },
            "required": ["id"],
        },
    },
    {
        "name": "memory_session",
        "description": (
            "List one Claude Code session's memory entries in chronological "
            "order as a title-only timeline (no bodies), to see the shape "
            "of a past session before pulling any one entry in full."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "session": {"type": "string", "description": "Session id (transcript filename stem)."},
                "limit": {"type": "integer", "default": 200},
            },
            "required": ["session"],
        },
    },
]


def _truncate(text):
    if len(text) > MAX_CHARS:
        return text[:MAX_CHARS].rstrip() + f"\n\n… truncated at {MAX_CHARS} chars"
    return text


def _tool_result(text, is_error=False):
    result = {"content": [{"type": "text", "text": text}]}
    if is_error:
        result["isError"] = True
    return result


def _validate_limit(value, default, lo=1, hi=None):
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise _ToolError("limit must be an integer")
    if value < lo or (hi is not None and value > hi):
        bound = f"between {lo} and {hi}" if hi is not None else f">= {lo}"
        raise _ToolError(f"limit must be {bound}")
    return value


def _handle_memory_search(args):
    query = args.get("query")
    if not isinstance(query, str) or not query.strip():
        raise _ToolError("query is required and must be a non-empty string")
    project = args.get("project")
    if project is not None and not isinstance(project, str):
        raise _ToolError("project must be a string")
    all_projects = args.get("all_projects", False)
    if not isinstance(all_projects, bool):
        raise _ToolError("all_projects must be a boolean")
    kind = args.get("kind")
    if kind is not None and kind not in _KINDS:
        raise _ToolError(f"kind must be one of {', '.join(_KINDS)}")
    limit = _validate_limit(args.get("limit"), default=10, lo=1, hi=50)

    if project:
        scope = project
    elif all_projects:
        scope = None
    else:
        scope = memory.project_for(os.getcwd())

    kinds = [kind] if kind else None

    conn = memory.connect()
    try:
        rows = memory.search(conn, query, project=scope, kinds=kinds, limit=limit)
    finally:
        conn.close()

    if not rows:
        msg = f'No matches for "{query}"'
        if scope:
            msg += f" in project {scope}. Try all_projects=true to search everywhere."
        else:
            msg += "."
        return _tool_result(msg)

    blocks = []
    for r in rows:
        date = (r["ts"] or "")[:10]
        header = f"#{r['id']} [{r['kind']}] {date} {r['project'] or ''} — {r['title'] or ''}"
        blocks.append(f"{header}\n{r['snippet'] or ''}")
    text = "\n\n".join(blocks) + "\n\nmemory_get <id> for the full text"
    return _tool_result(_truncate(text))


def _handle_memory_recent(args):
    project = args.get("project")
    if project is not None and not isinstance(project, str):
        raise _ToolError("project must be a string")
    if not project:
        project = memory.project_for(os.getcwd())
    limit = _validate_limit(args.get("limit"), default=10, lo=1, hi=None)

    conn = memory.connect()
    try:
        rows = memory.recent(conn, project=project, limit=limit)
    finally:
        conn.close()

    if not rows:
        return _tool_result(f"No recent summaries or observations for project {project or '(unscoped)'}.")

    blocks = []
    for r in rows:
        date = (r["ts"] or "")[:10]
        header = f"#{r['id']} [{r['kind']}] {date} — {r['title'] or ''}"
        body_lines = [ln.strip() for ln in (r["body"] or "").splitlines() if ln.strip()][:3]
        blocks.append(header + ("\n" + "\n".join(body_lines) if body_lines else ""))
    return _tool_result(_truncate("\n\n".join(blocks)))


def _handle_memory_get(args):
    id_ = args.get("id")
    if isinstance(id_, bool) or not isinstance(id_, int):
        raise _ToolError("id is required and must be an integer")

    conn = memory.connect()
    try:
        row = memory.get(conn, id_)
    finally:
        conn.close()

    if not row:
        return _tool_result(f"No entry with id {id_}.", is_error=True)

    date = (row["ts"] or "")[:10]
    header = f"#{row['id']} [{row['kind']}] {date} {row['project'] or ''} — {row['title'] or ''}"
    return _tool_result(_truncate(f"{header}\n\n{row['body'] or ''}"))


def _handle_memory_session(args):
    session = args.get("session")
    if not isinstance(session, str) or not session.strip():
        raise _ToolError("session is required and must be a non-empty string")
    limit = _validate_limit(args.get("limit"), default=200, lo=1, hi=None)

    conn = memory.connect()
    try:
        rows = memory.session_entries(conn, session, limit=limit)
    finally:
        conn.close()

    if not rows:
        return _tool_result(f"No entries found for session {session}.")

    lines = [f"{r['ts'] or ''} [{r['kind']}] {r['title'] or ''}" for r in rows]
    return _tool_result(_truncate("\n".join(lines)))


_TOOL_HANDLERS = {
    "memory_search": _handle_memory_search,
    "memory_recent": _handle_memory_recent,
    "memory_get": _handle_memory_get,
    "memory_session": _handle_memory_session,
}


def _tools_call(params):
    name = params.get("name")
    args = params.get("arguments")
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return _tool_result("bad arguments: expected an object", is_error=True)

    handler = _TOOL_HANDLERS.get(name)
    if not handler:
        return _tool_result(f"unknown tool: {name}", is_error=True)

    try:
        return handler(args)
    except _ToolError as e:
        return _tool_result(str(e), is_error=True)
    except Exception as e:
        # A DB problem or anything else unexpected -- still a tool error,
        # not a reason to crash the server or the JSON-RPC session.
        return _tool_result(f"memory error: {e}", is_error=True)


def _initialize_result(params):
    pv = (params or {}).get("protocolVersion") or PROTOCOL_VERSION
    return {
        "protocolVersion": pv,
        "capabilities": {"tools": {}},
        "serverInfo": {"name": "midiai-memory", "version": "1"},
    }


def _send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def _ok(id_, result):
    _send({"jsonrpc": "2.0", "id": id_, "result": result})


def _error(id_, code, message):
    _send({"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}})


def _handle(msg):
    if not isinstance(msg, dict):
        _error(None, -32700, "invalid message: expected a JSON object")
        return

    has_id = "id" in msg
    id_ = msg.get("id")
    method = msg.get("method")
    params = msg.get("params") or {}

    if not method:
        if has_id:
            _error(id_, -32600, "invalid request: missing method")
        return

    try:
        if method == "initialize":
            if has_id:
                _ok(id_, _initialize_result(params))
        elif method == "notifications/initialized":
            pass  # notification: no response, ever
        elif method == "ping":
            if has_id:
                _ok(id_, {})
        elif method == "tools/list":
            if has_id:
                _ok(id_, {"tools": TOOLS})
        elif method == "tools/call":
            result = _tools_call(params)
            if has_id:
                _ok(id_, result)
        else:
            if has_id:
                _error(id_, -32601, f"method not found: {method}")
    except Exception as e:
        # Whatever goes wrong dispatching a method must not take the server
        # down -- the client on the other end of stdin is a live session.
        if has_id:
            _error(id_, -32603, f"internal error: {e}")


def main():
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            _error(None, -32700, "parse error: invalid JSON")
            continue
        try:
            _handle(msg)
        except Exception:
            pass  # never let a single bad message end the session
    sys.exit(0)


if __name__ == "__main__":
    main()
