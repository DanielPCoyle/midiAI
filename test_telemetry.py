"""telemetry.py's own tests -- a temp DB throughout, never the real
~/.podium/telemetry.db, and no real `claude` CLI. python3 test_telemetry.py"""
import json
import os
import sqlite3
import sys
import tempfile
import time

import telemetry

SCRATCH = tempfile.mkdtemp(prefix="telemetry-test-")


def _fresh_db():
    """A brand new temp DB path, with any previous test's writer thread
    stopped so the next span/event starts a fresh one against it."""
    path = os.path.join(tempfile.mkdtemp(prefix="telemetry-test-db-", dir=SCRATCH), "telemetry.db")
    os.environ["TELEMETRY_DB"] = path
    os.environ["PODIUM_TELEMETRY"] = "1"
    telemetry._reset_for_tests()
    return path


def _rows(path):
    telemetry.flush()
    conn = sqlite3.connect(path)
    got = conn.execute(
        "SELECT ts, name, kind, ms, ok, err, attrs FROM spans ORDER BY ts").fetchall()
    conn.close()
    return got


def test_span_timing_and_ok():
    path = _fresh_db()
    with telemetry.span("unit test span", "internal", x=1) as s:
        time.sleep(0.02)
        s.set("y", "z")
    rows = _rows(path)
    assert len(rows) == 1, rows
    ts, name, kind, ms, ok, err, attrs = rows[0]
    assert name == "unit test span" and kind == "internal", rows[0]
    assert ms >= 15, "span did not time the block: %r" % ms
    assert ok == 1 and err is None, rows[0]
    assert json.loads(attrs) == {"x": 1, "y": "z"}, attrs
    print("test_span_timing_and_ok ok")


def test_span_as_context_manager_variants():
    # usable both as `with span(...):` and `with span(...) as s: s.set(...)`
    path = _fresh_db()
    with telemetry.span("bare", "internal"):
        pass
    with telemetry.span("with-set", "internal") as s:
        s.set("k", "v")
    rows = _rows(path)
    assert len(rows) == 2, rows
    assert json.loads(rows[1][6]) == {"k": "v"}
    print("test_span_as_context_manager_variants ok")


def test_span_exception_records_and_reraises():
    path = _fresh_db()
    try:
        with telemetry.span("boom span", "internal"):
            raise ValueError("nope")
        raise AssertionError("span must re-raise the wrapped exception")
    except ValueError as e:
        assert str(e) == "nope"
    rows = _rows(path)
    assert len(rows) == 1, rows
    ts, name, kind, ms, ok, err, attrs = rows[0]
    assert ok == 0 and err == "ValueError", rows[0]
    print("test_span_exception_records_and_reraises ok")


def test_event_is_instant():
    path = _fresh_db()
    telemetry.event("queue.send", kind="queue", terminal_id="a1")
    rows = _rows(path)
    assert len(rows) == 1, rows
    ts, name, kind, ms, ok, err, attrs = rows[0]
    assert name == "queue.send" and kind == "queue" and ms == 0.0 and ok == 1
    print("test_event_is_instant ok")


def test_attr_sanitising():
    path = _fresh_db()
    long_str = "x" * 500
    with telemetry.span("attrs span", "internal", s=long_str, n=3, f=1.5, b=True,
                        bad_dict={"a": 1}, bad_list=[1, 2], bad_none=None):
        pass
    rows = _rows(path)
    attrs = json.loads(rows[0][6])
    assert attrs["s"] == "x" * 200, (len(attrs["s"]), attrs["s"][:20])
    assert attrs["n"] == 3 and attrs["f"] == 1.5 and attrs["b"] is True
    assert "bad_dict" not in attrs, "a dict attr must be dropped, never recorded"
    assert "bad_list" not in attrs, "a list attr must be dropped, never recorded"
    assert "bad_none" not in attrs, "a None attr must be dropped, never recorded"
    print("test_attr_sanitising ok")


def test_disabled_flag_writes_nothing():
    path = _fresh_db()
    os.environ["PODIUM_TELEMETRY"] = "0"
    telemetry._reset_for_tests()
    with telemetry.span("should not land", "internal"):
        pass
    telemetry.event("also not", kind="internal")
    telemetry.flush()
    assert not os.path.exists(path), \
        "PODIUM_TELEMETRY=0 must never even create the DB file"
    os.environ["PODIUM_TELEMETRY"] = "1"
    telemetry._reset_for_tests()
    print("test_disabled_flag_writes_nothing ok")


def test_retention():
    path = _fresh_db()
    conn = telemetry._connect(path)
    old_ts = time.time() - telemetry.RETENTION_S - 3600
    recent_ts = time.time() - 10
    conn.execute("INSERT INTO spans VALUES (?,?,?,?,?,?,?)",
                (old_ts, "old", "internal", 1.0, 1, None, "{}"))
    conn.execute("INSERT INTO spans VALUES (?,?,?,?,?,?,?)",
                (recent_ts, "recent", "internal", 1.0, 1, None, "{}"))
    conn.commit()
    conn.close()
    telemetry._reset_for_tests()          # next emit opens a fresh writer on this DB
    with telemetry.span("trigger", "internal"):
        pass
    telemetry.flush()                     # the writer's own retention pass runs
    # strictly before it can call task_done() on the queued item flush() waits
    # for, both in the same single writer thread -- so by the time flush()
    # returns, retention has already happened.
    rows = _rows(path)
    names = {r[1] for r in rows}
    assert "old" not in names, "a span older than the retention window must be swept"
    assert "recent" in names and "trigger" in names, names
    print("test_retention ok")


def test_queue_never_blocks_past_10k():
    path = _fresh_db()
    os.environ["PODIUM_TELEMETRY"] = "0"   # keep the writer from draining the queue
    telemetry._reset_for_tests()
    os.environ["PODIUM_TELEMETRY"] = "1"
    # fill the queue directly -- emitting through span() would start the
    # writer and drain it as fast as it fills, which defeats the point
    for i in range(telemetry.QUEUE_MAX):
        telemetry._q.put_nowait((time.time(), f"n{i}", "internal", 1.0, 1, None, "{}"))
    t0 = time.monotonic()
    telemetry.event("one more", kind="internal")   # queue is full: must drop, not block
    took = time.monotonic() - t0
    assert took < 0.5, f"event() blocked for {took}s when the queue was full"
    # cleanup (stopping the writer + clearing the queue) is left to the next
    # test's _fresh_db() -> _reset_for_tests(), which joins the writer thread
    # first and so can safely touch _q's internals without racing it.
    print("test_queue_never_blocks_past_10k ok")


# ------------------------------------------------------------- run_model

FAKE_CLAUDE = '''#!/usr/bin/env python3
import json, os, sys
argv_capture = os.environ.get("ARGV_CAPTURE_FILE")
if argv_capture:
    with open(argv_capture, "w") as f:
        json.dump(sys.argv[1:], f)
prompt = sys.stdin.read()
if "TRIGGER_ERROR" in prompt:
    print(json.dumps({"result": "the model refused this request", "is_error": True,
                       "total_cost_usd": 0.0011,
                       "usage": {"input_tokens": 5, "output_tokens": 2},
                       "modelUsage": {"claude-haiku-test": {}}, "ttft_ms": 12}))
elif "TRIGGER_GARBAGE" in prompt:
    sys.stdout.write("not json at all, a raw model reply")
else:
    print(json.dumps({"result": "ok result text", "is_error": False,
                       "total_cost_usd": 0.0022,
                       "usage": {"input_tokens": 10, "output_tokens": 4,
                                 "cache_read_input_tokens": 1},
                       "modelUsage": {"claude-sonnet-test": {}}, "ttft_ms": 50}))
'''

_fake_claude_path = os.path.join(SCRATCH, "fake_claude.py")
with open(_fake_claude_path, "w") as _f:
    _f.write(FAKE_CLAUDE)


def _fake_cmd(*extra):
    return [sys.executable, _fake_claude_path, *extra]


def test_run_model_success_shape_and_no_prompt_leak():
    path = _fresh_db()
    argv_file = os.path.join(SCRATCH, "argv1.json")
    os.environ["ARGV_CAPTURE_FILE"] = argv_file
    try:
        prompt = "a secret prompt that must never be stored anywhere, ever"
        result = telemetry.run_model("test.purpose", _fake_cmd("--model", "sonnet"),
                                     prompt, 10)
    finally:
        del os.environ["ARGV_CAPTURE_FILE"]
    assert result.returncode == 0, result
    assert result.stdout == "ok result text", result.stdout

    with open(argv_file) as f:
        argv = json.load(f)
    assert argv == ["--model", "sonnet", "--output-format", "json"], argv

    rows = _rows(path)
    assert len(rows) == 1, rows
    ts, name, kind, ms, ok, err, attrs_json = rows[0]
    assert name == "gen_ai.chat" and kind == "model"
    assert ok == 1 and err is None
    attrs = json.loads(attrs_json)
    assert attrs["gen_ai.operation.name"] == "chat"
    assert attrs["podium.purpose"] == "test.purpose"
    assert attrs["gen_ai.request.model"] == "sonnet"
    assert attrs["gen_ai.response.model"] == "claude-sonnet-test"
    assert attrs["gen_ai.usage.input_tokens"] == 10
    assert attrs["gen_ai.usage.output_tokens"] == 4
    assert attrs["podium.cache_read_tokens"] == 1
    assert attrs["podium.cost_usd"] == 0.0022
    assert attrs["podium.ttft_ms"] == 50
    assert attrs["podium.prompt_chars"] == len(prompt)
    # the whole point: the prompt text itself is never recorded
    blob = json.dumps(attrs)
    assert "secret prompt" not in blob and prompt not in blob
    print("test_run_model_success_shape_and_no_prompt_leak ok")


def test_run_model_output_format_not_duplicated():
    path = _fresh_db()
    argv_file = os.path.join(SCRATCH, "argv2.json")
    os.environ["ARGV_CAPTURE_FILE"] = argv_file
    try:
        telemetry.run_model("test.purpose", _fake_cmd("--output-format", "json"),
                            "hi", 10)
    finally:
        del os.environ["ARGV_CAPTURE_FILE"]
    with open(argv_file) as f:
        argv = json.load(f)
    assert argv.count("--output-format") == 1, argv
    print("test_run_model_output_format_not_duplicated ok")


def test_run_model_is_error():
    path = _fresh_db()
    result = telemetry.run_model("test.purpose", _fake_cmd(), "please TRIGGER_ERROR now", 10)
    assert result.returncode == 1, result
    assert result.stderr == "the model refused this request", result.stderr
    rows = _rows(path)
    ts, name, kind, ms, ok, err, attrs = rows[0]
    assert ok == 0 and err == "model_error", rows[0]
    print("test_run_model_is_error ok")


def test_run_model_garbage_falls_back_to_raw_stdout():
    path = _fresh_db()
    result = telemetry.run_model("test.purpose", _fake_cmd(), "please TRIGGER_GARBAGE now", 10)
    assert result.stdout == "not json at all, a raw model reply", result.stdout
    assert result.returncode == 0, result   # the fake process itself exited clean
    rows = _rows(path)
    assert len(rows) == 1
    print("test_run_model_garbage_falls_back_to_raw_stdout ok")


# ------------------------------------------------------------------ summary

def _seed(conn, rows):
    conn.executemany(
        "INSERT INTO spans (ts, name, kind, ms, ok, err, attrs) VALUES (?,?,?,?,?,?,?)",
        rows)
    conn.commit()


def test_summary_percentiles_and_poll_and_totals():
    path = _fresh_db()
    conn = telemetry._connect(path)
    now = time.time()

    def r(dt, name, kind, ms, ok, err, attrs):
        return (now - dt, name, kind, ms, ok, err, json.dumps(attrs))

    rows = []
    # http: GET /work, ms = 10,20,30,40,100 -> p50=30 p95=100 max=100, no poll
    for i, ms in enumerate([10, 20, 30, 40, 100]):
        rows.append(r(i, "http GET /work", "server", ms, 1, None, {}))
    # http: one error on /work
    rows.append(r(1, "http GET /work", "server", 5, 0, "ValueError", {}))
    # http: GET /queue, tagged as a poll
    for i, ms in enumerate([5, 7]):
        rows.append(r(i, "http GET /queue", "server", ms, 1, None, {"podium.poll": True}))

    # model calls: two of the same (purpose, model)
    for cost, in_tok, out_tok in [(0.01, 100, 20), (0.02, 200, 40)]:
        rows.append(r(1, "gen_ai.chat", "model", 500, 1, None,
                      {"podium.purpose": "memory.summary", "gen_ai.response.model": "haiku",
                       "podium.cost_usd": cost, "gen_ai.usage.input_tokens": in_tok,
                       "gen_ai.usage.output_tokens": out_tok}))

    # tool execs
    for ms in [3, 9, 27]:
        rows.append(r(1, "exec git status", "client", ms, 1, None, {"process.exit_code": 0}))

    # guardrails: 3 runs of the same rail, one fail
    rows.append(r(1, "guardrail my-rail", "guardrail", 10, 1, None, {"verdict": "pass"}))
    rows.append(r(1, "guardrail my-rail", "guardrail", 20, 1, None, {"verdict": "pass"}))
    rows.append(r(1, "guardrail my-rail", "guardrail", 30, 1, None, {"verdict": "fail"}))

    # integrations: one error
    rows.append(r(1, "integration vercel deploy.list", "integration", 40, 1, None, {"ok": True}))
    rows.append(r(1, "integration vercel deploy.list", "integration", 50, 0, "TimeoutExpired",
                  {"ok": False}))

    # queue sends
    for _ in range(4):
        rows.append(r(1, "queue.send", "queue", 0, 1, None, {}))

    _seed(conn, rows)
    conn.close()

    s = telemetry.summary(3600)

    http_by_route = {row["route"]: row for row in s["http"]}
    work = http_by_route["GET /work"]
    # 6 ms values in this group: the 5 successes (10,20,30,40,100) plus the
    # one error row (ms=5) -- same route, same group. sorted: 5,10,20,30,40,100
    assert work["count"] == 6 and work["errors"] == 1, work
    assert work["p50"] == 20, work
    assert work["p95"] == 100, work
    assert work["max"] == 100, work
    assert work["poll"] is False, work

    queue_route = http_by_route["GET /queue"]
    assert queue_route["poll"] is True, queue_route
    assert queue_route["count"] == 2

    assert len(s["models"]) == 1
    model = s["models"][0]
    assert model["purpose"] == "memory.summary" and model["model"] == "haiku"
    assert model["count"] == 2
    assert round(model["cost_usd"], 4) == 0.03
    assert model["input_tokens"] == 300 and model["output_tokens"] == 60

    assert len(s["tools"]) == 1
    tool = s["tools"][0]
    assert tool["name"] == "exec git status" and tool["count"] == 3

    assert len(s["guardrails"]) == 1
    g = s["guardrails"][0]
    assert g["id"] == "my-rail" and g["runs"] == 3 and g["fail"] == 1 and g["error"] == 0

    assert len(s["integrations"]) == 1
    integ = s["integrations"][0]
    assert integ["name"] == "vercel" and integ["count"] == 2 and integ["errors"] == 1

    assert s["queue"]["sent"] == 4

    assert s["totals"]["requests"] == 8         # 6 + 2 (work + queue)
    assert s["totals"]["model_calls"] == 2
    assert round(s["totals"]["cost_usd"], 4) == 0.03
    assert s["totals"]["errors"] == 2            # the /work error + the integration error

    err_names = {e["name"] for e in s["errors"]}
    assert "http GET /work" in err_names and "integration vercel deploy.list" in err_names
    print("test_summary_percentiles_and_poll_and_totals ok")


def test_summary_since_window_excludes_old_rows():
    path = _fresh_db()
    conn = telemetry._connect(path)
    now = time.time()
    _seed(conn, [(now - 2, "http GET /old", "server", 1.0, 1, None, "{}"),
                (now - 100000, "http GET /ancient", "server", 1.0, 1, None, "{}")])
    conn.close()
    s = telemetry.summary(60)
    routes = {row["route"] for row in s["http"]}
    assert "GET /old" in routes and "GET /ancient" not in routes, routes
    print("test_summary_since_window_excludes_old_rows ok")


def test_overhead():
    """Not a correctness check -- a sanity number for how cheap `with span()`
    is. Reported, not asserted tightly (CI machines vary), but a gross
    regression (ms-scale) should fail loudly."""
    _fresh_db()
    n = 10000
    t0 = time.monotonic()
    for i in range(n):
        with telemetry.span("overhead probe", "internal", i=i):
            pass
    took = time.monotonic() - t0
    per_span_us = (took / n) * 1_000_000
    print(f"test_overhead: {per_span_us:.2f} us/span over {n} spans")
    assert per_span_us < 200, f"span() overhead too high: {per_span_us:.2f} us"


def test_exec_name_never_records_arguments():
    assert telemetry.exec_name(["git", "-C", "/Users/x/secret-repo", "log", "-1"]) == "exec git log"
    assert telemetry.exec_name(["tmux", "send-keys", "-t", "%2", "-l", "--", "my password"]) \
        == "exec tmux send-keys"
    assert telemetry.exec_name(["claude", "Summarise THIS private text"]) == "exec claude ?", \
        "typed text never becomes a span name"


if __name__ == "__main__":
    test_span_timing_and_ok()
    test_span_as_context_manager_variants()
    test_span_exception_records_and_reraises()
    test_event_is_instant()
    test_attr_sanitising()
    test_disabled_flag_writes_nothing()
    test_retention()
    test_queue_never_blocks_past_10k()
    test_run_model_success_shape_and_no_prompt_leak()
    test_run_model_output_format_not_duplicated()
    test_run_model_is_error()
    test_run_model_garbage_falls_back_to_raw_stdout()
    test_summary_percentiles_and_poll_and_totals()
    test_summary_since_window_excludes_old_rows()
    test_overhead()
    test_exec_name_never_records_arguments()
    telemetry._reset_for_tests()
    print("ok")
