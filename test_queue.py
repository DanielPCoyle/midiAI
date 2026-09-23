"""The up-next queue's drain rules, against a fake herdr. python3 test_queue.py"""
import os
import tempfile

import mapui
import push_cc

mapui.QUEUE_FILE = os.path.join(tempfile.mkdtemp(), "q.json")
sent, status, pane = [], {"s": "working"}, {}
push_cc.agents = lambda: [{"terminal_id": "t1", "agent_status": status["s"]}]
push_cc.pane_summary = lambda a, *k: pane
push_cc.herdr = lambda *a: sent.append(a[-1])

mapui.save_queue({"t1": mapui.clean_queue([{"id": "a", "text": "one"},
                                           {"id": "b", "text": "two"},
                                           {"id": "c", "text": "  "}])})
assert [i["id"] for i in mapui.load_queue()["t1"]] == ["a", "b"], "blank dropped"
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
print("ok")
