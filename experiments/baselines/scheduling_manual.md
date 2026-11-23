STRATEGY: Hand-written baseline (written by the developer BEFORE the experiment; never shown to the generator).
Validate structure (unknown refs, capacity, cycles), then a serial schedule-generation scheme with the
classic "longest remaining path" priority rule. No search, no improvement phase.

```pipeline
[{"kind": "code", "fn": "solve"}]
```
```python
def solve(state, params):
    inp = state["input"]
    res = inp["resources"]
    jobs = {j["id"]: j for j in inp["jobs"]}
    for j in inp["jobs"]:
        for p in j["after"]:
            if p not in jobs:
                state["output"] = {"status": "infeasible", "reason": "unknown predecessor"}
                return state
        for r, u in j["requires"].items():
            if r not in res or u > res[r]:
                state["output"] = {"status": "infeasible", "reason": "resource"}
                return state
    succ = {k: [] for k in jobs}
    indeg = {k: len(jobs[k]["after"]) for k in jobs}
    for j in inp["jobs"]:
        for p in j["after"]:
            succ[p].append(j["id"])
    order, ready = [], [k for k in jobs if indeg[k] == 0]
    while ready:
        v = ready.pop()
        order.append(v)
        for s in succ[v]:
            indeg[s] -= 1
            if indeg[s] == 0:
                ready.append(s)
    if len(order) != len(jobs):
        state["output"] = {"status": "infeasible", "reason": "cycle"}
        return state
    tail = {}
    for v in reversed(order):
        tail[v] = jobs[v]["duration"] + max([tail[s] for s in succ[v]] or [0])
    indeg = {k: len(jobs[k]["after"]) for k in jobs}
    ready = [k for k in jobs if indeg[k] == 0]
    seq = []
    while ready:
        ready.sort(key=lambda v: -tail[v])
        v = ready.pop(0)
        seq.append(v)
        for s in succ[v]:
            indeg[s] -= 1
            if indeg[s] == 0:
                ready.append(s)
    horizon = sum(j["duration"] for j in inp["jobs"]) + max([j["release"] for j in inp["jobs"]] or [0]) + 1
    usage = {r: [0] * (horizon + 1) for r in res}
    start = {}
    for v in seq:
        j = jobs[v]
        t = max([j["release"]] + [start[p] + jobs[p]["duration"] for p in j["after"]])
        while not all(usage[r][x] + u <= res[r] for r, u in j["requires"].items() for x in range(t, t + j["duration"])):
            t += 1
        for r, u in j["requires"].items():
            for x in range(t, t + j["duration"]):
                usage[r][x] += u
        start[v] = t
    state["output"] = {"status": "ok", "start": start}
    return state
```
