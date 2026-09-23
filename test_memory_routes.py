"""mapui's /memory/* routes, exercised through the pure memory_query() helper
-- no socket, no live server. python3 test_memory_routes.py

memory.DB_PATH is pointed at a fresh tempfile database before mapui's own
memory.connect() calls ever run, so nothing here can touch the real
~/.midiai/memory.db."""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import memory

_tmp_dir = tempfile.mkdtemp(prefix="midiai-memroutes-")
memory.DB_PATH = os.path.join(_tmp_dir, "memory.db")

import mapui  # noqa: E402 -- must come after DB_PATH is redirected


def _seed():
    """A couple of entries in two different projects, straight through the
    same entries table index_transcript() writes to -- the shape test_memory.py
    already relies on."""
    conn = memory.connect()
    with conn:
        conn.execute(
            "INSERT INTO entries (session, project, ts, kind, title, body, source) "
            "VALUES ('s1', 'proj1', '2026-09-23T10:00:00.000Z', 'summary', "
            "'Fixed the queue bug', 'Investigated the drain rules and fixed the gap timer.', "
            "'transcript')")
        conn.execute(
            "INSERT INTO entries (session, project, ts, kind, title, body, source) "
            "VALUES ('s2', 'proj2', '2026-09-23T11:00:00.000Z', 'observation', "
            "'Push firmware note', 'Some other project entirely.', 'transcript')")
        for i in range(3):
            conn.execute(
                "INSERT INTO entries (session, project, ts, kind, title, body, source) "
                "VALUES ('s1', 'proj1', ?, 'summary', ?, 'filler body', 'transcript')",
                (f"2026-09-23T09:0{i}:00.000Z", f"filler {i}"))
    conn.close()
    return conn


def test_search_scoped_by_cwd():
    _seed()
    status, body, ctype = mapui.memory_query("/memory/search?q=queue&cwd=/Users/x/proj1")
    assert status == 200 and ctype == "application/json", (status, body, ctype)
    items = json.loads(body)["items"]
    assert len(items) == 1 and items[0]["title"] == "Fixed the queue bug", items

    status, body, ctype = mapui.memory_query("/memory/search?q=queue&cwd=/Users/x/proj2")
    items = json.loads(body)["items"]
    assert items == [], items
    print("test_search_scoped_by_cwd ok")


def test_search_no_cwd_covers_all_projects():
    status, body, ctype = mapui.memory_query("/memory/search?q=queue")
    items = json.loads(body)["items"]
    assert any(it["title"] == "Fixed the queue bug" for it in items), items
    print("test_search_no_cwd_covers_all_projects ok")


def test_recent():
    status, body, ctype = mapui.memory_query("/memory/recent?cwd=/Users/x/proj1&limit=2")
    assert status == 200, (status, body)
    items = json.loads(body)["items"]
    assert len(items) == 2, items
    assert all(it["project"] == "proj1" for it in items), items
    print("test_recent ok")


def test_entry_found_and_404():
    status, body, ctype = mapui.memory_query("/memory/search?q=queue")
    entry_id = json.loads(body)["items"][0]["id"]

    status, body, ctype = mapui.memory_query(f"/memory/entry?id={entry_id}")
    assert status == 200 and ctype == "application/json", (status, body, ctype)
    item = json.loads(body)["item"]
    assert item["title"] == "Fixed the queue bug", item

    status, body, ctype = mapui.memory_query("/memory/entry?id=999999")
    assert status == 404, (status, body)

    status, body, ctype = mapui.memory_query("/memory/entry?id=notanumber")
    assert status == 400 and body == "bad request", (status, body)

    status, body, ctype = mapui.memory_query("/memory/entry")
    assert status == 400 and body == "bad request", (status, body)
    print("test_entry_found_and_404 ok")


def test_stats_has_indexing():
    status, body, ctype = mapui.memory_query("/memory/stats")
    assert status == 200 and ctype == "application/json", (status, body, ctype)
    st = json.loads(body)
    assert "indexing" in st and isinstance(st["indexing"], bool), st
    assert "by_kind" in st and "sessions" in st, st

    # Before the loop has ever run a tick, the flag says so.
    assert st["indexing"] is True, st
    mapui._memory_first_sweep_done = True
    status, body, ctype = mapui.memory_query("/memory/stats")
    assert json.loads(body)["indexing"] is False
    mapui._memory_first_sweep_done = False
    print("test_stats_has_indexing ok")


def test_bad_limit():
    status, body, ctype = mapui.memory_query("/memory/search?q=queue&limit=notanumber")
    assert status == 400 and body == "bad request", (status, body)

    status, body, ctype = mapui.memory_query("/memory/recent?limit=notanumber")
    assert status == 400 and body == "bad request", (status, body)
    print("test_bad_limit ok")


def test_limit_clamping():
    status, body, ctype = mapui.memory_query("/memory/recent?cwd=/Users/x/proj1&limit=0")
    items = json.loads(body)["items"]
    assert len(items) == 1, items   # clamped up to 1

    status, body, ctype = mapui.memory_query("/memory/recent?cwd=/Users/x/proj1&limit=500")
    items = json.loads(body)["items"]
    assert len(items) == 4, items   # clamped down to 100, but only 4 exist

    status, body, ctype = mapui.memory_query("/memory/recent?cwd=/Users/x/proj1")
    items = json.loads(body)["items"]
    assert len(items) == 4, items   # default 30, well above what's seeded
    print("test_limit_clamping ok")


if __name__ == "__main__":
    try:
        test_search_scoped_by_cwd()
        test_search_no_cwd_covers_all_projects()
        test_recent()
        test_entry_found_and_404()
        test_stats_has_indexing()
        test_bad_limit()
        test_limit_clamping()
        print("ok")
    finally:
        shutil.rmtree(_tmp_dir, ignore_errors=True)
