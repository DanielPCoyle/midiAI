"""The pinned last-prompt line. python3 test_summarize_prompt.py"""
import mapui

calls = []


def fake(ask):
    calls.append(ask)
    return "Pin the last prompt to the top\nand a second line nobody wants"


assert mapui.summarize_prompt("fix the  git\ntab count") == "fix the git tab count", \
    "a short prompt is its own summary, whitespace folded"
assert calls == []

long = "please " * 60
assert mapui.summarize_prompt(long, run=fake) == "Pin the last prompt to the top"
assert mapui.summarize_prompt(long, run=fake) == "Pin the last prompt to the top"
assert len(calls) == 1, "summarised once, then cached"

broken = "again " * 60
line = mapui.summarize_prompt(broken, run=lambda a: 1 / 0)
assert line.startswith("again again") and line.endswith("…") and len(line) <= mapui.PROMPT_SHORT, line
assert mapui.summarize_prompt(broken, run=fake) == "Pin the last prompt to the top", \
    "a failure is not cached -- the next ask tries again"
print("ok")
