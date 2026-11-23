STRATEGY: Hand-written baseline (written by the developer before the experiment; never shown to the generator).
Structure validation, then a serial schedule-generation scheme: jobs in a precedence-feasible order sorted by
earliest due date, each job in its shortest executable mode at the earliest time the calendars allow.
No search and no mode optimisation.

```pipeline
[{"kind": "code", "fn": "solve"}]
```
```python
def solve(state, params):
    inp = state["input"]
    res = inp["resources"]
    maxcap = {r: max(c for _, c in v["calendar"]) for r, v in res.items()}
    jobs = {j["id"]: j for j in inp["jobs"]}
    mode = {}
    for k, j in jobs.items():
        for p in j["after"]:
            if p not in jobs:
                state["output"] = {"status": "infeasible", "reason": "unknown predecessor"}
                return state
        ok = [i for i, m in enumerate(j["modes"])
              if all(r in maxcap and u <= maxcap[r] for r, u in m["requires"].items())]
        if not ok:
            state["output"] = {"status": "infeasible", "reason": "no executable mode"}
            return state
        mode[k] = min(ok, key=lambda i: j["modes"][i]["duration"])
    indeg = {k: len(j["after"]) for k, j in jobs.items()}
    succ = {k: [] for k in jobs}
    for k, j in jobs.items():
        for p in j["after"]:
            succ[p].append(k)
    ready = [k for k in jobs if indeg[k] == 0]
    order = []
    while ready:
        ready.sort(key=lambda k: (jobs[k]["due"] if jobs[k]["due"] is not None else 10 ** 9, jobs[k]["release"]))
        v = ready.pop(0)
        order.append(v)
        for s in succ[v]:
            indeg[s] -= 1
            if indeg[s] == 0:
                ready.append(s)
    if len(order) != len(jobs):
        state["output"] = {"status": "infeasible", "reason": "cycle"}
        return state
    horizon = sum(j["modes"][mode[k]]["duration"] for k, j in jobs.items()) \
        + max([j["release"] for j in jobs.values()] or [0]) + max(t for v in res.values() for t, _ in v["calendar"]) + 2
    free = {}
    for r, v in res.items():
        row = []
        cal = v["calendar"]
        for t in range(horizon + 1):
            c = 0
            for bt, cc in cal:
                if bt <= t:
                    c = cc
            row.append(c)
        free[r] = row
    start = {}
    for k in order:
        j = jobs[k]
        m = j["modes"][mode[k]]
        t = max([j["release"]] + [start[p] + jobs[p]["modes"][mode[p]]["duration"] for p in j["after"]])
        while not all(free[r][x] >= u for r, u in m["requires"].items() for x in range(t, t + m["duration"])):
            t += 1
        for r, u in m["requires"].items():
            for x in range(t, t + m["duration"]):
                free[r][x] -= u
        start[k] = t
    state["output"] = {"status": "ok", "schedule": {k: {"start": start[k], "mode": mode[k]} for k in jobs}}
    return state
```
