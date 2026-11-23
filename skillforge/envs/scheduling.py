"""Environment: resource-constrained job scheduling (RCPSP with release times).

Public task: given jobs (duration, release time, predecessors, resource demands) and renewable resources
with capacities, return a start time for every job minimising the makespan, or report infeasibility.

Scoring uses a hidden resource/critical-path LOWER BOUND, so there is no ceiling imposed by any reference
solver. Hidden parts (checker only): feasibility label, lower bound, and a reference makespan (exact for small instances via
enumeration of all precedence-feasible lists with a serial schedule-generation scheme — which is known to
contain an optimal schedule for regular objectives — and the best of an extensive randomised search for
larger ones; reported for context only, never used for scoring).

This module deliberately contains no solver usable by candidates: the reference search lives in private
functions used only at instance-generation time and in `check`.
"""

from __future__ import annotations

import itertools
import random
from typing import Any

from .base import CheckResult, Family, Instance, TaskEnvironment

TIME_LIMIT_S = 1.5

SPEC = f"""
TASK: Resource-constrained job scheduling.

INPUT (state["input"]), a JSON object:
  {{"resources": {{"<name>": <capacity int>, ...}},
    "jobs": [{{"id": "<str>", "duration": <int >= 0>, "release": <int >= 0>,
               "after": ["<job id>", ...],            # predecessors: this job may start only after they FINISH
               "requires": {{"<resource>": <units int>, ...}}}}, ...]}}

OUTPUT (set state["output"]) — exactly one of:
  {{"status": "ok", "start": {{"<job id>": <int start time>, ...}}}}      # every job must appear
  {{"status": "infeasible", "reason": "<short text>"}}

HARD CONSTRAINTS of an "ok" answer (any violation = invalid answer, score 0):
  * every job has an integer start >= its release time;
  * for every predecessor p of job j: start[j] >= start[p] + duration[p];
  * at every integer time t, for every resource r, the sum of units of jobs running at t
    (start <= t < start + duration) is <= capacity[r]. Zero-duration jobs use no capacity.
An instance is INFEASIBLE exactly when the precedence relation contains a cycle, a job references an
unknown job or resource, or a job requires more units of a resource than its capacity. Answering
"infeasible" for a feasible instance, or "ok" for an infeasible one, scores 0.

OBJECTIVE: minimise the makespan = max(start + duration). A valid answer scores
L / your_makespan, where L is a hidden lower bound on the optimal makespan (so the score is in (0, 1],
higher is better, and 1.0 is not always attainable). A correct "infeasible" answer scores 1.

LIMITS: each instance must be solved within {TIME_LIMIT_S} seconds of CPU; instances have 3 to 40 jobs and
1 to 4 resources; durations 0..12, release times 0..30.
"""


# ------------------------------------------------------------------ hidden reference machinery
def _validate_structure(inp: dict) -> str | None:
    jobs = {j["id"]: j for j in inp["jobs"]}
    res = inp["resources"]
    for j in inp["jobs"]:
        for p in j["after"]:
            if p not in jobs:
                return f"job {j['id']} depends on unknown job {p}"
        for r, u in j["requires"].items():
            if r not in res:
                return f"job {j['id']} requires unknown resource {r}"
            if u > res[r]:
                return f"job {j['id']} needs {u} {r} > capacity {res[r]}"
    color: dict[str, int] = {}

    def dfs(v: str) -> bool:
        color[v] = 1
        for p in jobs[v]["after"]:
            if color.get(p) == 1 or (color.get(p) is None and dfs(p)):
                return True
        color[v] = 2
        return False

    for v in jobs:
        if color.get(v) is None and dfs(v):
            return "precedence cycle"
    return None


def _sgs(inp: dict, order: list[str]) -> tuple[int, dict[str, int]]:
    jobs = {j["id"]: j for j in inp["jobs"]}
    res = inp["resources"]
    horizon = sum(j["duration"] for j in inp["jobs"]) + max((j["release"] for j in inp["jobs"]), default=0) + 1
    usage = {r: [0] * (horizon + 1) for r in res}
    start: dict[str, int] = {}
    for jid in order:
        j = jobs[jid]
        t = max([j["release"]] + [start[p] + jobs[p]["duration"] for p in j["after"]])
        d = j["duration"]
        while True:
            if all(usage[r][tt] + u <= res[r] for r, u in j["requires"].items() for tt in range(t, t + d)):
                break
            t += 1
        for r, u in j["requires"].items():
            for tt in range(t, t + d):
                usage[r][tt] += u
        start[jid] = t
    return max((start[k] + jobs[k]["duration"] for k in start), default=0), start


def _topo_orders(inp: dict):
    jobs = {j["id"]: j for j in inp["jobs"]}
    preds = {k: set(v["after"]) for k, v in jobs.items()}

    def rec(done: list[str], left: set[str]):
        if not left:
            yield list(done)
            return
        for v in sorted(left):
            if preds[v] <= set(done):
                done.append(v)
                left.remove(v)
                yield from rec(done, left)
                left.add(v)
                done.pop()

    yield from rec([], set(jobs))


def _lower_bound(inp: dict) -> int:
    jobs = {j["id"]: j for j in inp["jobs"]}
    memo: dict[str, int] = {}

    def ef(v: str) -> int:  # earliest finish ignoring resources
        if v not in memo:
            j = jobs[v]
            memo[v] = max([j["release"]] + [ef(p) for p in j["after"]]) + j["duration"]
        return memo[v]

    cp = max((ef(v) for v in jobs), default=0)
    load = max((-(-sum(j["duration"] * j["requires"].get(r, 0) for j in inp["jobs"]) // c)
                for r, c in inp["resources"].items() if c > 0), default=0)
    return max(cp, load)


def _reference(inp: dict, rng: random.Random) -> dict[str, Any]:
    err = _validate_structure(inp)
    if err:
        return {"feasible": False, "reason": err}
    lb = _lower_bound(inp)
    n = len(inp["jobs"])
    if n <= 7:
        best = min(_sgs(inp, o)[0] for o in _topo_orders(inp))
        return {"feasible": True, "makespan": best, "method": "exact", "lower_bound": lb}
    jobs = {j["id"]: j for j in inp["jobs"]}
    succ: dict[str, list[str]] = {k: [] for k in jobs}
    for j in inp["jobs"]:
        for p in j["after"]:
            succ[p].append(j["id"])
    tail: dict[str, int] = {}

    def tl(v: str) -> int:
        if v not in tail:
            tail[v] = jobs[v]["duration"] + max((tl(s) for s in succ[v]), default=0)
        return tail[v]

    def topo_by(key) -> list[str]:
        indeg = {k: len(jobs[k]["after"]) for k in jobs}
        ready = [k for k in jobs if indeg[k] == 0]
        out = []
        while ready:
            ready.sort(key=key)
            v = ready.pop(0)
            out.append(v)
            for s in succ[v]:
                indeg[s] -= 1
                if indeg[s] == 0:
                    ready.append(s)
        return out

    best = None
    rules = [lambda v: -tl(v), lambda v: jobs[v]["release"], lambda v: -jobs[v]["duration"],
             lambda v: -len(succ[v]), lambda v: -sum(jobs[v]["requires"].values())]
    for rule in rules:
        m, _ = _sgs(inp, topo_by(rule))
        best = m if best is None else min(best, m)
    iters = 1500
    for _ in range(iters):
        keys = {k: rng.random() for k in jobs}
        bias = rng.random()
        m, _ = _sgs(inp, topo_by(lambda v: (keys[v] * (1 - bias)) - bias * tl(v) / (1 + max(tail.values() or [1]))))
        best = min(best, m)
        if best == lb:
            break
    return {"feasible": True, "makespan": best, "method": "exact" if best == lb else "best_known",
            "lower_bound": lb}


# ------------------------------------------------------------------ environment
FAMILIES = [
    Family("small", 2.0, "3-7 jobs, 1-2 resources (reference is exact)"),
    Family("medium", 2.0, "10-16 jobs, 2-3 resources"),
    Family("tight_resources", 1.5, "capacities 1-3, heavy demands: resource conflicts dominate"),
    Family("long_chains", 1.0, "deep precedence chains"),
    Family("wide_parallel", 1.0, "many independent jobs, generous capacity"),
    Family("release_times", 1.0, "staggered release times"),
    Family("zero_durations", 0.7, "milestones with zero duration mixed with normal jobs"),
    Family("infeasible_cycle", 0.6, "precedence contains a cycle"),
    Family("infeasible_capacity", 0.6, "a job needs more units than capacity or an unknown resource"),
    Family("large", 1.2, "25-40 jobs (scale shift: sealed audit only)", audit_only=True),
]


class SchedulingEnv(TaskEnvironment):
    name = "rcpsp_scheduling"
    version = "1"

    def public_spec(self) -> str:
        return SPEC

    def public_examples(self) -> list[dict[str, Any]]:
        ex_in = {"resources": {"crew": 2, "crane": 1},
                 "jobs": [{"id": "A", "duration": 3, "release": 0, "after": [], "requires": {"crew": 1}},
                          {"id": "B", "duration": 2, "release": 0, "after": [], "requires": {"crew": 1, "crane": 1}},
                          {"id": "C", "duration": 4, "release": 1, "after": ["A"], "requires": {"crew": 2}},
                          {"id": "D", "duration": 0, "release": 0, "after": ["B", "C"], "requires": {}}]}
        return [
            {"input": ex_in, "output": {"status": "ok", "start": {"A": 0, "B": 0, "C": 3, "D": 7}},
             "note": "a valid answer with makespan 7"},
            {"input": {"resources": {"m": 1}, "jobs": [
                {"id": "X", "duration": 1, "release": 0, "after": ["Y"], "requires": {"m": 1}},
                {"id": "Y", "duration": 1, "release": 0, "after": ["X"], "requires": {"m": 1}}]},
             "output": {"status": "infeasible", "reason": "precedence cycle X <-> Y"},
             "note": "cyclic precedence"},
        ]

    def families(self) -> list[Family]:
        return FAMILIES

    def generate(self, family: str, rng: random.Random, instance_id: str) -> Instance:
        p = dict(n=(3, 7), res=(1, 2), cap=(1, 4), dur=(1, 8), rel=0.0, prec=0.25, zero=0.0, req_p=0.7)
        if family == "medium":
            p.update(n=(10, 16), res=(2, 3), cap=(2, 5), prec=0.15)
        elif family == "tight_resources":
            p.update(n=(8, 14), res=(1, 3), cap=(1, 3), prec=0.1, req_p=0.95)
        elif family == "long_chains":
            p.update(n=(8, 14), res=(1, 2), cap=(2, 4), prec=0.6)
        elif family == "wide_parallel":
            p.update(n=(12, 20), res=(1, 2), cap=(4, 8), prec=0.03)
        elif family == "release_times":
            p.update(n=(8, 14), res=(1, 3), cap=(2, 4), rel=1.0, prec=0.15)
        elif family == "zero_durations":
            p.update(n=(6, 12), res=(1, 2), cap=(2, 4), zero=0.35)
        elif family == "large":
            p.update(n=(25, 40), res=(2, 4), cap=(2, 6), prec=0.08, rel=0.3)
        elif family in ("infeasible_cycle", "infeasible_capacity"):
            p.update(n=(4, 10), res=(1, 3), cap=(1, 4))
        n = rng.randint(*p["n"])
        names = ["crew", "crane", "truck", "oven", "lab", "bay"]
        rng.shuffle(names)
        resources = {names[i]: rng.randint(*p["cap"]) for i in range(rng.randint(*p["res"]))}
        ids = [f"J{i}" for i in range(n)]
        rng.shuffle(ids)
        jobs = []
        for k, jid in enumerate(ids):
            dur = 0 if rng.random() < p["zero"] else rng.randint(*p["dur"])
            preds = [ids[i] for i in range(k) if rng.random() < p["prec"] / max(1, (k - i)) ** 0.5]
            req = {}
            for r, c in resources.items():
                if rng.random() < p["req_p"]:
                    req[r] = rng.randint(1, c)
            jobs.append({"id": jid, "duration": dur, "release": rng.randint(0, 30) if rng.random() < p["rel"] else 0,
                         "after": preds, "requires": req})
        if family == "infeasible_cycle" and n >= 2:
            a, b = rng.sample(range(n), 2)
            lo, hi = min(a, b), max(a, b)
            # make lo depend (transitively) on hi while hi already depends on lo
            if ids[lo] not in jobs[hi]["after"]:
                jobs[hi]["after"].append(ids[lo])
            jobs[lo]["after"].append(ids[hi])
        if family == "infeasible_capacity":
            j = rng.choice(jobs)
            r = rng.choice(list(resources))
            if rng.random() < 0.5:
                j["requires"][r] = resources[r] + rng.randint(1, 2)
            else:
                j["requires"]["unobtainium"] = 1
        rng.shuffle(jobs)
        inp = {"resources": resources, "jobs": jobs}
        hidden = _reference(inp, random.Random(rng.random()))
        return Instance(instance_id, family, inp, hidden, tags=[hidden.get("method", "infeasible")])

    # ------------------------------------------------------------------ hidden checker
    def check(self, instance: Instance, output: Any) -> CheckResult:
        h = instance.hidden
        inp = instance.input
        if not isinstance(output, dict) or output.get("status") not in ("ok", "infeasible"):
            return CheckResult(False, 0.0, ["malformed_output"], f"output must be a dict with status ok|infeasible, got {str(output)[:120]}")
        if output["status"] == "infeasible":
            if not h["feasible"]:
                return CheckResult(True, 1.0, [], "correctly reported infeasible")
            return CheckResult(False, 0.0, ["false_infeasible"], f"instance is feasible but answer said infeasible ({str(output.get('reason'))[:80]})")
        if not h["feasible"]:
            return CheckResult(False, 0.0, ["missed_infeasibility"], f"instance is infeasible ({h['reason']}) but answer returned a schedule")
        start = output.get("start")
        jobs = {j["id"]: j for j in inp["jobs"]}
        if not isinstance(start, dict) or set(start) != set(jobs):
            missing = sorted(set(jobs) - set(start or {}))[:5]
            return CheckResult(False, 0.0, ["incomplete_schedule"], f"start must contain exactly every job id; missing {missing}")
        try:
            s = {k: int(v) for k, v in start.items()}
            if any(float(start[k]) != s[k] for k in start):
                raise ValueError
        except (TypeError, ValueError):
            return CheckResult(False, 0.0, ["non_integer_start"], "start times must be integers")
        for k, j in jobs.items():
            if s[k] < j["release"]:
                return CheckResult(False, 0.0, ["release_violated"], f"{k} starts at {s[k]} before release {j['release']}")
            for p in j["after"]:
                if s[k] < s[p] + jobs[p]["duration"]:
                    return CheckResult(False, 0.0, ["precedence_violated"], f"{k} starts at {s[k]} before predecessor {p} finishes at {s[p] + jobs[p]['duration']}")
        events: dict[int, dict[str, int]] = {}
        for k, j in jobs.items():
            if j["duration"] <= 0:
                continue
            for r, u in j["requires"].items():
                events.setdefault(s[k], {}).setdefault(r, 0)
                events[s[k]][r] += u
                events.setdefault(s[k] + j["duration"], {}).setdefault(r, 0)
                events[s[k] + j["duration"]][r] -= u
        level = {r: 0 for r in inp["resources"]}
        for t in sorted(events):
            for r, d in events[t].items():
                level[r] += d
            for r, v in level.items():
                if v > inp["resources"][r]:
                    return CheckResult(False, 0.0, ["capacity_violated"], f"resource {r} uses {v} > capacity {inp['resources'][r]} at t={t}")
        makespan = max((s[k] + j["duration"] for k, j in jobs.items()), default=0)
        ref, lb = h["makespan"], h["lower_bound"]
        score = 1.0 if makespan == 0 else min(1.0, lb / makespan)
        # the message is generator-visible on DEV: it must not reveal the hidden bound or reference
        return CheckResult(True, score, [], f"valid schedule; your makespan = {makespan}",
                           {"makespan": makespan, "reference": ref, "lower_bound": lb,
                            "beat_reference": makespan < ref, "matched_lower_bound": makespan == lb})
