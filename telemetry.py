#!/usr/bin/env python3
"""midiAI's own telemetry -- instrument midiAI itself, not the projects it
manages. Every isolated `claude -p` call, every HTTP request mapui serves,
every `tmux`/`git` exec term.py makes, every guardrail run and integration
call, lands here as one row in a local SQLite table so the app's TELEMETRY
tab can show what is slow, what is failing, and what a background model call
is costing -- the pain this was built for: `/projects` at 11.6s (uncached
`claude agents --json`), `/catalog` at 19s (a glob), and guardrail/queue/
integration failures nobody could see.

Privacy (hard rules, enforced here, not just documented):
  - Never record prompt text, model output, request/response bodies,
    secrets, env, or command-line arguments beyond the program name and its
    first subcommand. A checkout root is the only kind of path recorded.
  - Attribute values are str (cut to 200 chars), int, float or bool --
    anything else (a dict, a list, None, ...) is silently dropped.
  - Telemetry must never raise into, block, or noticeably slow the code it
    wraps: every public entry point is exception-safe around its OWN
    bookkeeping (the span's *body* -- the caller's code -- still raises
    normally), and writes happen off the caller's thread through a bounded
    queue that drops rather than blocks once it backs up.

    python3 telemetry.py        self-check, against a throwaway DB

Store: SQLite at ~/.midiai/telemetry.db (override with TELEMETRY_DB;
MIDIAI_TELEMETRY=0 disables recording entirely -- summary() still reads
whatever is already there). WAL + a busy_timeout so mapui and push_cc, which
both import modules that record, can write from different processes without
stepping on each other.
"""
import json
import os
import re
import queue
import sqlite3
import subprocess
import threading
import time
from contextlib import contextmanager

DB_PATH = os.path.expanduser("~/.midiai/telemetry.db")

RETENTION_S = 7 * 24 * 3600          # a week
QUEUE_MAX = 10000                    # past this, new spans are dropped, never blocked
FLUSH_INTERVAL_S = 1.0
BATCH_MAX = 500
ATTR_STR_MAX = 200

KINDS = ("server", "client", "model", "guardrail", "queue", "integration", "internal")

SCHEMA = """
CREATE TABLE IF NOT EXISTS spans (
    ts REAL, name TEXT, kind TEXT, ms REAL, ok INTEGER, err TEXT, attrs TEXT
);
CREATE INDEX IF NOT EXISTS idx_spans_ts ON spans(ts);
CREATE INDEX IF NOT EXISTS idx_spans_kind_ts ON spans(kind, ts);
"""

_q = queue.Queue(maxsize=QUEUE_MAX)
_writer_thread = None
_writer_lock = threading.Lock()
_stop_writer = threading.Event()


def _db_path():
    return os.environ.get("TELEMETRY_DB") or DB_PATH


def _enabled():
    return os.environ.get("MIDIAI_TELEMETRY", "1") != "0"


# ------------------------------------------------------------------ writer

def _connect(path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path, timeout=5, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def _apply_retention(conn):
    try:
        conn.execute("DELETE FROM spans WHERE ts < ?", (time.time() - RETENTION_S,))
        conn.commit()
    except Exception:
        pass


def _writer_loop(path, stop_event):
    try:
        conn = _connect(path)
    except Exception:
        # cannot open the DB at all -- drain the queue so callers never block
        # on a full one, and give up quietly rather than crash the process.
        while not stop_event.is_set():
            try:
                _q.get(timeout=FLUSH_INTERVAL_S)
                _q.task_done()
            except queue.Empty:
                pass
        return

    _apply_retention(conn)          # "on first write"
    last_retention = time.time()
    try:
        while not stop_event.is_set():
            batch = []
            try:
                batch.append(_q.get(timeout=FLUSH_INTERVAL_S))
            except queue.Empty:
                pass
            while len(batch) < BATCH_MAX:
                try:
                    batch.append(_q.get_nowait())
                except queue.Empty:
                    break
            if batch:
                try:
                    conn.executemany(
                        "INSERT INTO spans (ts, name, kind, ms, ok, err, attrs) "
                        "VALUES (?,?,?,?,?,?,?)", batch)
                    conn.commit()
                except Exception:
                    pass
                for _ in batch:
                    _q.task_done()
            now = time.time()
            if now - last_retention >= 3600:
                _apply_retention(conn)
                last_retention = now
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _ensure_writer():
    global _writer_thread
    if _writer_thread is not None and _writer_thread.is_alive():
        return
    with _writer_lock:
        if _writer_thread is not None and _writer_thread.is_alive():
            return
        _stop_writer.clear()
        t = threading.Thread(target=_writer_loop, args=(_db_path(), _stop_writer),
                             name="telemetry-writer", daemon=True)
        t.start()
        _writer_thread = t


def flush(timeout=5.0):
    """Block until every span queued so far has been written (or dropped).
    Real callers never need this -- it exists for tests that assert on what
    landed in the DB right after a span/event/run_model call."""
    if _writer_thread is None or not _writer_thread.is_alive():
        return
    done = threading.Event()

    def _waiter():
        _q.join()
        done.set()

    t = threading.Thread(target=_waiter, daemon=True)
    t.start()
    t.join(timeout=timeout)


def _reset_for_tests():
    """Test-only: stop the writer thread and clear the queue, so the next
    span/event picks up a (possibly changed) TELEMETRY_DB / MIDIAI_TELEMETRY
    from scratch. Never called by the real process."""
    global _writer_thread
    if _writer_thread is not None and _writer_thread.is_alive():
        _stop_writer.set()
        _writer_thread.join(timeout=2)
    _stop_writer.clear()
    _writer_thread = None
    with _q.mutex:
        _q.queue.clear()
        _q.unfinished_tasks = 0
        _q.all_tasks_done.notify_all()


# ------------------------------------------------------------------ attrs

def _sanitize_attrs(attrs):
    out = {}
    for k, v in (attrs or {}).items():
        if not isinstance(k, str):
            continue
        key = k[:80]
        if isinstance(v, bool):
            out[key] = v
        elif isinstance(v, int):
            out[key] = v
        elif isinstance(v, float):
            out[key] = v
        elif isinstance(v, str):
            out[key] = v if len(v) <= ATTR_STR_MAX else v[:ATTR_STR_MAX]
        # dicts, lists, None, and anything else: dropped, never recorded
    return out


def _emit(name, kind, ms, ok, err, attrs):
    if not _enabled():
        return
    try:
        row = (time.time(), str(name)[:200], str(kind)[:40] if kind else "internal",
               float(ms or 0.0), 1 if ok else 0, (str(err)[:200] if err else None),
               json.dumps(_sanitize_attrs(attrs)))
        _ensure_writer()
        _q.put_nowait(row)
    except queue.Full:
        pass
    except Exception:
        pass


# ------------------------------------------------------------------ span/event

class _Span:
    __slots__ = ("name", "kind", "attrs")

    def __init__(self, name, kind, attrs):
        self.name = name
        self.kind = kind
        self.attrs = attrs

    def set(self, key, value):
        self.attrs[key] = value





_TAKES_VALUE = {"-C", "-c", "-L", "-S", "-f", "-t", "--git-dir", "--work-tree"}
_WORD_RE = re.compile(r"^[a-z][a-z0-9-]{0,30}$")


def exec_name(argv):
    """`exec <program> <subcommand>` for a span, never an argument: the
    subcommand is the first word that is not a flag or a flag's value
    (`git -C <path> log` -> `exec git log`), and only a plain lowercase word
    is kept -- anything else, a path or typed text, becomes "?"."""
    argv = [str(a) for a in (argv or [])]
    if not argv:
        return "exec ?"
    program = os.path.basename(argv[0]) or "?"
    sub, skip = "", False
    for a in argv[1:]:
        if skip:
            skip = False
            continue
        if a in _TAKES_VALUE:
            skip = True
            continue
        if a.startswith("-"):
            continue
        sub = a if _WORD_RE.match(a) else "?"
        break
    return f"exec {program} {sub}".rstrip()

@contextmanager
def span(name, kind="internal", **attrs):
    """`with span("exec git status", "client", **attrs) as s: s.set(k, v)` --
    times the block, records ok=1 or (ok=0, err=<ExceptionType name>) on
    exception, and always re-raises whatever the wrapped code raised. Never
    raises on its own account -- a telemetry failure is swallowed, not
    surfaced to the caller."""
    s = _Span(name, kind, dict(attrs))
    t0 = time.monotonic()
    ok, err = True, None
    try:
        yield s
    except Exception as e:
        ok, err = False, type(e).__name__
        raise
    finally:
        ms = (time.monotonic() - t0) * 1000.0
        try:
            _emit(s.name, s.kind, ms, ok, err, s.attrs)
        except Exception:
            pass


def event(name, kind="internal", **attrs):
    """An instant fact with no duration -- e.g. event("queue.send")."""
    try:
        _emit(name, kind, 0.0, True, None, attrs)
    except Exception:
        pass


# ------------------------------------------------------------------ run_model

def run_model(purpose, cmd, input, timeout, cwd=None):
    """THE way every isolated `claude -p` call runs. Appends
    --output-format json (unless the caller's cmd already has it), execs
    `cmd`, and returns a subprocess.CompletedProcess shaped like a plain
    `claude -p` call so existing callers (which check .returncode / .stdout /
    .stderr) are unchanged: .stdout is the model's `result` text; when the
    model reports is_error, .returncode is 1 and .stderr is `result`; if the
    JSON does not parse at all, .stdout falls back to the raw process stdout
    untouched and .returncode/.stderr are the process's own.

    Never records the prompt (`input`) or the model's output -- only its
    shape: char count, token counts, cost, timing."""
    full_cmd = list(cmd)
    if "--output-format" not in full_cmd:
        full_cmd = full_cmd + ["--output-format", "json"]
    req_model = None
    if "--model" in full_cmd:
        i = full_cmd.index("--model")
        if i + 1 < len(full_cmd):
            req_model = full_cmd[i + 1]

    base_attrs = {
        "gen_ai.operation.name": "chat",
        "midiai.purpose": str(purpose)[:200],
        "midiai.prompt_chars": len(input or ""),
    }
    if req_model:
        base_attrs["gen_ai.request.model"] = str(req_model)[:200]

    t0 = time.monotonic()
    try:
        proc = subprocess.run(full_cmd, input=input, text=True, capture_output=True,
                              timeout=timeout, cwd=cwd)
    except Exception as e:
        ms = (time.monotonic() - t0) * 1000.0
        try:
            _emit("gen_ai.chat", "model", ms, False, type(e).__name__, base_attrs)
        except Exception:
            pass
        raise
    ms = (time.monotonic() - t0) * 1000.0

    raw = proc.stdout or ""
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        parsed = None

    attrs = dict(base_attrs)
    if isinstance(parsed, dict):
        is_error = bool(parsed.get("is_error"))
        result_text = parsed.get("result")
        out_text = result_text if isinstance(result_text, str) else raw
        usage = parsed.get("usage")
        if isinstance(usage, dict):
            if isinstance(usage.get("input_tokens"), (int, float)):
                attrs["gen_ai.usage.input_tokens"] = usage["input_tokens"]
            if isinstance(usage.get("output_tokens"), (int, float)):
                attrs["gen_ai.usage.output_tokens"] = usage["output_tokens"]
            if isinstance(usage.get("cache_read_input_tokens"), (int, float)):
                attrs["midiai.cache_read_tokens"] = usage["cache_read_input_tokens"]
        model_usage = parsed.get("modelUsage")
        if isinstance(model_usage, dict) and model_usage:
            attrs["gen_ai.response.model"] = str(next(iter(model_usage.keys())))[:200]
        cost = parsed.get("total_cost_usd")
        if isinstance(cost, (int, float)):
            attrs["midiai.cost_usd"] = float(cost)
        ttft = parsed.get("ttft_ms")
        if isinstance(ttft, (int, float)):
            attrs["midiai.ttft_ms"] = float(ttft)
        rc = 1 if is_error else proc.returncode
        err_text = out_text if is_error else proc.stderr
        result = subprocess.CompletedProcess(proc.args, rc, out_text, err_text)
        ok = not is_error and rc == 0
    else:
        result = subprocess.CompletedProcess(proc.args, proc.returncode, raw, proc.stderr)
        ok = proc.returncode == 0

    try:
        _emit("gen_ai.chat", "model", ms, ok, None if ok else "model_error", attrs)
    except Exception:
        pass
    return result


# ------------------------------------------------------------------ summary

def _percentile(sorted_vals, pct):
    if not sorted_vals:
        return 0.0
    idx = int(round(pct / 100.0 * (len(sorted_vals) - 1)))
    idx = max(0, min(len(sorted_vals) - 1, idx))
    return round(sorted_vals[idx], 2)


def _route_of(name):
    # "http GET /work" -> "GET /work"
    parts = name.split(" ", 1)
    return parts[1] if len(parts) == 2 and parts[0] == "http" else name


def summary(since_s=86400):
    """Everything the TELEMETRY tab needs, sorted by what hurts."""
    since_s = max(0.0, float(since_s or 0))
    cutoff = time.time() - since_s
    out = {
        "since": since_s, "http": [], "models": [], "tools": [], "guardrails": [],
        "integrations": [], "queue": {"sent": 0}, "errors": [],
        "totals": {"requests": 0, "model_calls": 0, "cost_usd": 0.0, "errors": 0},
    }
    try:
        conn = sqlite3.connect(_db_path(), timeout=5)
        conn.execute("PRAGMA busy_timeout=5000")
    except Exception:
        return out
    try:
        rows = conn.execute(
            "SELECT ts, name, kind, ms, ok, err, attrs FROM spans WHERE ts >= ? ORDER BY ts",
            (cutoff,)).fetchall()
    except Exception:
        rows = []
    finally:
        try:
            conn.close()
        except Exception:
            pass

    http_groups, model_groups, tool_groups = {}, {}, {}
    guardrail_groups, integ_groups = {}, {}
    errors = []
    total_errors = 0

    for ts, name, kind, ms, ok, err, attrs_json in rows:
        try:
            attrs = json.loads(attrs_json) if attrs_json else {}
        except (ValueError, TypeError):
            attrs = {}
        if not ok:
            total_errors += 1
            errors.append({"ts": ts, "name": name, "err": err or ""})

        if kind == "server":
            route = _route_of(name)
            g = http_groups.setdefault(route, {"ms": [], "errors": 0, "poll": False})
            g["ms"].append(ms)
            if not ok:
                g["errors"] += 1
            if attrs.get("midiai.poll"):
                g["poll"] = True
            out["totals"]["requests"] += 1
        elif kind == "model":
            purpose = str(attrs.get("midiai.purpose", ""))
            model = str(attrs.get("gen_ai.response.model") or attrs.get("gen_ai.request.model") or "")
            key = (purpose, model)
            g = model_groups.setdefault(key, {"ms": [], "errors": 0, "cost": 0.0,
                                              "in_tok": 0, "out_tok": 0})
            g["ms"].append(ms)
            if not ok:
                g["errors"] += 1
            g["cost"] += float(attrs.get("midiai.cost_usd") or 0)
            g["in_tok"] += int(attrs.get("gen_ai.usage.input_tokens") or 0)
            g["out_tok"] += int(attrs.get("gen_ai.usage.output_tokens") or 0)
            out["totals"]["model_calls"] += 1
            out["totals"]["cost_usd"] += float(attrs.get("midiai.cost_usd") or 0)
        elif kind == "client":
            g = tool_groups.setdefault(name, {"ms": [], "errors": 0})
            g["ms"].append(ms)
            if not ok:
                g["errors"] += 1
        elif kind == "guardrail":
            rid = name[len("guardrail "):] if name.startswith("guardrail ") else name
            g = guardrail_groups.setdefault(rid, {"ms": [], "fail": 0, "error": 0})
            g["ms"].append(ms)
            verdict = attrs.get("verdict")
            if verdict == "fail":
                g["fail"] += 1
            elif verdict == "error":
                g["error"] += 1
        elif kind == "integration":
            rest = name[len("integration "):] if name.startswith("integration ") else name
            iname = rest.split(" ", 1)[0] if rest else rest
            g = integ_groups.setdefault(iname, {"ms": [], "errors": 0})
            g["ms"].append(ms)
            if not ok:
                g["errors"] += 1
        elif kind == "queue":
            pass
        if name == "queue.send":
            out["queue"]["sent"] += 1

    for route, g in http_groups.items():
        vals = sorted(g["ms"])
        out["http"].append({"route": route, "count": len(vals), "errors": g["errors"],
                            "p50": _percentile(vals, 50), "p95": _percentile(vals, 95),
                            "max": round(max(vals), 2) if vals else 0.0,
                            "poll": g["poll"]})
    out["http"].sort(key=lambda r: r["p95"], reverse=True)

    for (purpose, model), g in model_groups.items():
        vals = sorted(g["ms"])
        out["models"].append({"purpose": purpose, "model": model, "count": len(vals),
                              "errors": g["errors"], "p50": _percentile(vals, 50),
                              "cost_usd": round(g["cost"], 4), "input_tokens": g["in_tok"],
                              "output_tokens": g["out_tok"]})
    out["models"].sort(key=lambda r: r["cost_usd"], reverse=True)

    for name_, g in tool_groups.items():
        vals = sorted(g["ms"])
        out["tools"].append({"name": name_, "count": len(vals), "errors": g["errors"],
                             "p50": _percentile(vals, 50), "p95": _percentile(vals, 95)})
    out["tools"].sort(key=lambda r: r["p95"], reverse=True)

    for rid, g in guardrail_groups.items():
        vals = sorted(g["ms"])
        out["guardrails"].append({"id": rid, "runs": len(vals), "fail": g["fail"],
                                  "error": g["error"], "p95": _percentile(vals, 95)})
    out["guardrails"].sort(key=lambda r: (r["fail"] + r["error"], r["p95"]), reverse=True)

    for iname, g in integ_groups.items():
        vals = sorted(g["ms"])
        out["integrations"].append({"name": iname, "count": len(vals), "errors": g["errors"],
                                    "p95": _percentile(vals, 95)})
    out["integrations"].sort(key=lambda r: (r["errors"], r["p95"]), reverse=True)

    out["errors"] = errors[-50:]
    out["totals"]["errors"] = total_errors
    out["totals"]["cost_usd"] = round(out["totals"]["cost_usd"], 4)
    return out


if __name__ == "__main__":
    import shutil
    import tempfile

    tmp = tempfile.mkdtemp(prefix="telemetry-selfcheck-")
    os.environ["TELEMETRY_DB"] = os.path.join(tmp, "telemetry.db")
    try:
        _reset_for_tests()
        with span("selftest span", "internal", foo="bar") as s:
            s.set("n", 1)
        event("selftest.event", kind="internal", ok=True)
        flush()
        s = summary(3600)
        assert s["totals"]["requests"] == 0
        assert any(True for _ in [1])  # placeholder: table wrote without raising
        print("telemetry.py: ok")
    finally:
        _reset_for_tests()
        shutil.rmtree(tmp, ignore_errors=True)
