"""The up-next queue's drain rules, against a fake herdr. python3 test_queue.py"""
import os
import tempfile

import mapui
import push_cc

mapui.QUEUE_FILE = os.path.join(tempfile.mkdtemp(), "q.json")
mapui.QUEUE_PLAYING_FILE = os.path.join(tempfile.mkdtemp(), "playing.json")
mapui.QUEUE_NEXT_FILE = os.path.join(tempfile.mkdtemp(), "next.json")
mapui._queue_playing.clear()
mapui._queue_once.clear()
sent, status, pane = [], {"s": "working"}, {}
push_cc.agents = lambda: [{"terminal_id": "t1", "agent_status": status["s"]}]
push_cc.pane_summary = lambda a, *k: pane
push_cc.herdr = lambda *a: sent.append(a[-1])

mapui.save_queue({"t1": mapui.clean_queue([{"id": "a", "text": "one"},
                                           {"id": "b", "text": "two"},
                                           {"id": "c", "text": "  "}])})
assert [i["id"] for i in mapui.load_queue()["t1"]] == ["a", "b"], "blank dropped"
status["s"] = "idle"
mapui.queue_tick()
assert sent == [], "paused by default: an idle agent still gets nothing"
mapui._queue_once.add("t1")
status["s"] = "working"
mapui.queue_tick()
assert sent == [] and "t1" in mapui._queue_once, "send next waits for a free agent"
status["s"] = "idle"
mapui.queue_tick()
assert sent == ["one\r"] and "t1" not in mapui._queue_once, "send next releases one"
mapui._queue_last["t1"] = 0
mapui.queue_tick()
assert sent == ["one\r"], "and only one"
sent.clear()
mapui._queue_gone.discard("a")
mapui.save_queue({"t1": mapui.clean_queue([{"id": "a", "text": "one"},
                                           {"id": "b", "text": "two"}])})
mapui._queue_playing.add("t1")
status["s"] = "working"
mapui.queue_tick()
assert sent == [], "a busy agent gets nothing"
status["s"] = "idle"
mapui.queue_tick()
assert sent == ["one\r"], sent
mapui.queue_tick()
assert sent == ["one\r"], "the gap holds the second back"
assert mapui.clean_queue([{"id": "a", "text": "one"}]) == [], \
    "a stale client list must not put a delivered prompt back"
mapui._queue_last["t1"] = 0
mapui.queue_tick()
assert sent == ["one\r", "two\r"] and mapui.load_queue() == {}
pane["opts"] = ["yes"]
mapui.save_queue({"t1": [{"id": "d", "text": "q"}]})
mapui._queue_last["t1"] = 0
mapui.queue_tick()
assert len(sent) == 2, "a question on screen is not an empty input line"
# play survives a restart: it is read back from disk
mapui._queue_playing = {"t9"}
mapui.save_playing()
assert mapui.load_playing() == {"t9"}, "play is remembered across a restart"
# so is an armed send next, and releasing it clears it on disk too
mapui._queue_once.add("t8")
mapui.save_once()
assert mapui.load_playing(mapui.QUEUE_NEXT_FILE) == {"t8"}, "send next survives a restart"
mapui._queue_once.discard("t8")
mapui.save_once()
assert mapui.load_playing(mapui.QUEUE_NEXT_FILE) == set()
print("ok")
