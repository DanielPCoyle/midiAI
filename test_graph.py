"""The GIT tab's lane layout on a branch-and-merge. python3 test_graph.py"""
from mapui import graph_lanes, ref_pills


def lay(*commits):
    rows = graph_lanes([{"sha": s, "parents": p.split()} for s, p in commits])
    return {r["sha"]: r for r in rows}


# M merges feature B into A; both came off C. Newest first, as git gives it.
g = lay(("M", "A B"), ("B", "C"), ("A", "C"), ("C", ""))
assert g["M"]["col"] == 0 and g["M"]["edges"] == [[0, 1, 0, 2, 0], [0, 1, 1, 2, 1]]
assert g["B"]["col"] == 1, "the merged branch gets its own lane"
assert [0, 0, 0, 2, 0] in g["B"]["edges"], "main runs past the feature commit"
assert g["A"]["col"] == 0 and [1, 0, 1, 2, 1] in g["A"]["edges"]
assert g["C"]["col"] == 0 and [1, 0, 0, 1, 1] in g["C"]["edges"], \
    "the feature lane closes into the fork point"
assert g["C"]["width"] == 2 and g["C"]["edges"][-1:] == [[1, 0, 0, 1, 1]], \
    "a root draws nothing below itself"

# two unrelated tips: the second opens a lane beside the first
g = lay(("X", "C"), ("Y", "C"), ("C", ""))
assert (g["X"]["col"], g["Y"]["col"], g["C"]["col"]) == (0, 1, 0)

assert ref_pills("HEAD -> main, origin/main, feat/x, tag: v1", ["origin"]) == [
    {"name": "main", "kind": "local", "head": True},
    {"name": "origin/main", "kind": "remote", "head": False},
    {"name": "feat/x", "kind": "local", "head": False},
    {"name": "v1", "kind": "tag", "head": False}], "a slash is not a remote"
print("ok")
