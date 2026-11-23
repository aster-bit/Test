"""Environment: multi-mode scheduling under resource calendars with weighted tardiness.

An invented composite task (not a textbook benchmark): jobs have alternative execution modes, resource
capacities change over time (shifts, maintenance windows with zero capacity), and the cost combines makespan
with weighted lateness. Instances are large enough that a 1-second pure-Python budget forces real
algorithmic choices.

This module contains NO solver of any kind: only the specification, the instance generator, a hidden lower
bound and the checker. Scores are bound-relative, so no reference solution caps them.
"""

from __future__ import annotations

import random
from typing import Any

from .base import CheckResult, Family, Instance, TaskEnvironment

CPU_LIMIT_S = 1.0

SPEC = f"""
TASK: Multi-mode job scheduling under resource calendars, minimising makespan plus weighted tardiness.

INPUT (state["input"]), a JSON object:
  {{"resources": {{"<name>": {{"calendar": [[0, <cap>], [<t1>, <cap>], ...]}}, ...}},
    "jobs": [{{"id": "<str>", "release": <int >= 0>, "after": ["<job id>", ...],
               "due": <int or null>, "weight": <int >= 0>,
               "modes": [{{"duration": <int >= 1>, "requires": {{"<resource>": <units int >= 1>, ...}}}}, ...]}}, ...]}}

  * A calendar is a list of [from_time, capacity] breakpoints with strictly increasing times, starting at 0.
    The capacity given at a breakpoint holds from that time (inclusive) until the next breakpoint; the LAST
    segment holds forever and always carries the resource's maximum capacity. Capacity may be 0 in a window.
  * Every job must be executed in exactly ONE of its modes, without interruption.

OUTPUT (set state["output"]) — exactly one of:
  {{"status": "ok", "schedule": {{"<job id>": {{"start": <int>, "mode": <index into that job's modes>}}, ...}}}}
  {{"status": "infeasible", "reason": "<short text>"}}

HARD CONSTRAINTS of an "ok" answer (any violation = invalid answer, score 0):
  * every job appears with an integer start >= its release and a valid mode index;
  * for every predecessor p of job j: start[j] >= start[p] + duration of p's chosen mode;
  * at every integer time t and for every resource r: the units used by jobs running at t
    (start <= t < start + duration, in their chosen modes) do not exceed the calendar capacity of r at t.
An instance is INFEASIBLE exactly when the precedence relation has a cycle, a job references an unknown
predecessor, or some job has no mode whose every requirement names a known resource and fits within that
resource's maximum capacity. "infeasible" for a feasible instance, or "ok" for an infeasible one, scores 0.

OBJECTIVE: minimise  cost = makespan + sum over jobs with a due date of weight * max(0, finish - due),
where finish = start + duration of the chosen mode and makespan = max finish.
A valid answer scores L / cost, where L is a hidden lower bound on the optimal cost (score in (0, 1], higher
is better, 1.0 is usually unattainable). A correct "infeasible" answer scores 1.

LIMITS: at most {CPU_LIMIT_S} second of CPU time per instance (measure with time.process_time(); exceeding
about twice that aborts the instance with score 0). Instances have 15 to 150 jobs, 2 to 4 resources,
1 to 3 modes per job, durations 1..12.
"""


def _structure_error(inp: dict) -> str | None:
    jobs = {j["id"]: j for j in inp["jobs"]}
    maxcap = {r: max(c for _, c in v["calendar"]) for r, v in inp["resources"].items()}
    for j in inp["jobs"]:
        for p in j["after"]:
            if p not in jobs:
                return f"job {j['id']} depends on unknown job {p}"
        if not any(all(r in maxcap and u <= maxcap[r] for r, u in m["requires"].items()) for m in j["modes"]):
            return f"job {j['id']} has no executable mode"
    color: dict[str, int] = {}
    for root in jobs:  # iterative DFS (instances can be large)
        if root in color:
            continue
        stack = [(root, iter(jobs[root]["after"]))]
        color[root] = 1
        while stack:
            v, it = stack[-1]
            for p in it:
                if color.get(p) == 1:
                    return "precedence cycle"
                if p not in color:
                    color[p] = 1
                    stack.append((p, iter(jobs[p]["after"])))
                    break
            else:
                color[v] = 2
                stack.pop()
    return None


def _lower_bound(inp: dict) -> int:
    """Valid lower bound on cost: max(critical path, energy) + sum of unavoidable weighted tardiness."""
    jobs = {j["id"]: j for j in inp["jobs"]}
    maxcap = {r: max(c for _, c in v["calendar"]) for r, v in inp["resources"].items()}

    def fits(m):
        return all(r in maxcap and u <= maxcap[r] for r, u in m["requires"].items())

    mind = {k: min(m["duration"] for m in j["modes"] if fits(m)) for k, j in jobs.items()}
    order: list[str] = []
    indeg = {k: len(j["after"]) for k, j in jobs.items()}
    succ: dict[str, list[str]] = {k: [] for k in jobs}
    for k, j in jobs.items():
        for p in j["after"]:
            succ[p].append(k)
    ready = [k for k in jobs if indeg[k] == 0]
    while ready:
        v = ready.pop()
        order.append(v)
        for s in succ[v]:
            indeg[s] -= 1
            if indeg[s] == 0:
                ready.append(s)
    ef: dict[str, int] = {}
    for v in order:
        j = jobs[v]
        ef[v] = max([j["release"]] + [ef[p] for p in j["after"]]) + mind[v]
    cp = max(ef.values(), default=0)
    energy = 0
    for r, cap in maxcap.items():
        if cap > 0:
            e = sum(min(m["duration"] * m["requires"].get(r, 0) for m in j["modes"] if fits(m)) for j in jobs.values())
            energy = max(energy, -(-e // cap))
    tard = sum(j["weight"] * max(0, ef[k] - j["due"]) for k, j in jobs.items() if j["due"] is not None)
    return max(cp, energy) + tard


FAMILIES = [
    Family("mid", 2.0, "35-55 jobs, mixed features"),
    Family("calendar_heavy", 1.5, "many calendar breakpoints incl. zero-capacity maintenance windows"),
    Family("mode_rich", 1.5, "every job has 3 modes with duration/resource trade-offs"),
    Family("tight_due", 1.5, "tight due dates with heavy weights: tardiness dominates"),
    Family("dense_precedence", 1.0, "deep precedence structure"),
    Family("big", 1.5, "70-90 jobs"),
    Family("infeasible_cycle", 0.4, "precedence cycle"),
    Family("infeasible_mode", 0.4, "a job without executable mode / unknown predecessor"),
    Family("xl", 1.5, "110-150 jobs (scale shift: sealed audit only)", audit_only=True),
]


class CalendarSchedulingEnv(TaskEnvironment):
    name = "calendar_multimode_scheduling"
    version = "2"  # v2: larger instances after the cal-v1 headroom gate failed (0.417 < 0.5)

    def public_spec(self) -> str:
        return SPEC

    def public_examples(self) -> list[dict[str, Any]]:
        ex = {"resources": {"crew": {"calendar": [[0, 2], [4, 0], [6, 3]]}, "oven": {"calendar": [[0, 1]]}},
              "jobs": [
                  {"id": "A", "release": 0, "after": [], "due": 5, "weight": 2,
                   "modes": [{"duration": 3, "requires": {"crew": 2}}, {"duration": 5, "requires": {"crew": 1}}]},
                  {"id": "B", "release": 1, "after": ["A"], "due": None, "weight": 0,
                   "modes": [{"duration": 2, "requires": {"crew": 1, "oven": 1}}]}]}
        return [{"input": ex,
                 "output": {"status": "ok", "schedule": {"A": {"start": 0, "mode": 0}, "B": {"start": 6, "mode": 0}}},
                 "note": "A runs 0-3 with 2 crew (finish 3 <= due 5). B cannot run during 3-6: starting at 3 it would "
                         "overlap the zero-capacity window 4-6. B runs 6-8. makespan 8, no tardiness, cost 8."}]

    def families(self) -> list[Family]:
        return FAMILIES

    def generate(self, family: str, rng: random.Random, instance_id: str) -> Instance:
        p = dict(n=(35, 55), res=(2, 4), cap=(3, 7), bps=(0, 3), zero_p=0.25, modes=(1, 3), prec=0.12,
                 due_p=0.5, due_tight=1.3, w=(1, 4), rel_p=0.25)
        if family == "calendar_heavy":
            p.update(n=(30, 50), bps=(3, 6), zero_p=0.45)
        elif family == "mode_rich":
            p.update(n=(30, 50), modes=(3, 3))
        elif family == "tight_due":
            p.update(n=(30, 50), due_p=0.9, due_tight=0.85, w=(3, 9))
        elif family == "dense_precedence":
            p.update(n=(30, 50), prec=0.4)
        elif family == "big":
            p.update(n=(70, 90))
        elif family == "xl":
            p.update(n=(110, 150), bps=(1, 5))
        elif family in ("infeasible_cycle", "infeasible_mode"):
            p.update(n=(15, 30))
        n = rng.randint(*p["n"])
        names = ["crew", "crane", "truck", "oven", "lab", "bay"]
        rng.shuffle(names)
        rnames = names[: rng.randint(*p["res"])]
        maxcap = {r: rng.randint(*p["cap"]) for r in rnames}
        ids = [f"J{i}" for i in range(n)]
        rng.shuffle(ids)
        jobs = []
        for k, jid in enumerate(ids):
            base_d = rng.randint(1, 10)
            req = {r: rng.randint(1, maxcap[r]) for r in rnames if rng.random() < 0.6}
            if not req:
                r = rng.choice(rnames)
                req = {r: rng.randint(1, maxcap[r])}
            modes = [{"duration": base_d, "requires": req}]
            for _ in range(rng.randint(*p["modes"]) - 1):
                kind = rng.random()
                if kind < 0.5:  # slower but lighter
                    modes.append({"duration": min(12, base_d + rng.randint(1, 4)),
                                  "requires": {r: max(1, u - rng.randint(1, 2)) for r, u in req.items()}})
                elif kind < 0.8:  # faster but heavier
                    modes.append({"duration": max(1, base_d - rng.randint(1, 3)),
                                  "requires": {r: min(maxcap[r], u + rng.randint(1, 2)) for r, u in req.items()}})
                else:  # different resource
                    r2 = rng.choice(rnames)
                    modes.append({"duration": base_d + rng.randint(0, 2), "requires": {r2: rng.randint(1, maxcap[r2])}})
            preds = [ids[i] for i in range(k) if rng.random() < p["prec"] / max(1, (k - i)) ** 0.5]
            jobs.append({"id": jid, "release": rng.randint(0, 25) if rng.random() < p["rel_p"] else 0, "after": preds,
                         "due": None, "weight": 0, "modes": modes})
        # due dates relative to a crude earliest-finish estimate (precedence only)
        ef: dict[str, int] = {}
        for j in jobs:
            ef[j["id"]] = max([j["release"]] + [ef[q] for q in j["after"]]) + min(m["duration"] for m in j["modes"])
        load = sum(min(m["duration"] for m in j["modes"]) for j in jobs)
        for j in jobs:
            if rng.random() < p["due_p"]:
                slack = rng.uniform(0.0, 0.5) * load / max(2, len(rnames))
                j["due"] = int(ef[j["id"]] * p["due_tight"] + slack * (p["due_tight"] - 0.5))
                j["weight"] = rng.randint(*p["w"])
        horizon = max(10, load // 2)
        resources = {}
        for r in rnames:
            cal = [[0, rng.randint(1, maxcap[r])]]
            times = sorted(rng.sample(range(2, horizon), min(horizon - 2, rng.randint(*p["bps"]))))
            for t in times:
                cal.append([t, 0 if rng.random() < p["zero_p"] else rng.randint(1, maxcap[r])])
            if len(cal) == 1:
                cal[0][1] = maxcap[r]
            else:
                cal[-1][1] = maxcap[r]
            resources[r] = {"calendar": cal}
        if family == "infeasible_cycle":
            a, b = sorted(rng.sample(range(n), 2))
            if ids[a] not in jobs[b]["after"]:
                jobs[b]["after"].append(ids[a])
            jobs[a]["after"].append(ids[b])
        if family == "infeasible_mode":
            j = rng.choice(jobs)
            if rng.random() < 0.6:
                r = rng.choice(rnames)
                j["modes"] = [{"duration": m["duration"], "requires": {**m["requires"], r: maxcap[r] + rng.randint(1, 2)}}
                              for m in j["modes"]]
            else:
                j["after"].append("J_missing")
        rng.shuffle(jobs)
        inp = {"resources": resources, "jobs": jobs}
        err = _structure_error(inp)
        hidden = {"feasible": err is None, "reason": err, "lower_bound": _lower_bound(inp) if err is None else None}
        return Instance(instance_id, family, inp, hidden)

    # ------------------------------------------------------------------ hidden checker
    def check(self, instance: Instance, output: Any) -> CheckResult:
        h, inp = instance.hidden, instance.input
        if not isinstance(output, dict) or output.get("status") not in ("ok", "infeasible"):
            return CheckResult(False, 0.0, ["malformed_output"], f"output must be a dict with status ok|infeasible, got {str(output)[:120]}")
        if output["status"] == "infeasible":
            if not h["feasible"]:
                return CheckResult(True, 1.0, [], "correctly reported infeasible")
            return CheckResult(False, 0.0, ["false_infeasible"], "instance is feasible but the answer said infeasible")
        if not h["feasible"]:
            return CheckResult(False, 0.0, ["missed_infeasibility"], f"instance is infeasible ({h['reason']}) but a schedule was returned")
        sched = output.get("schedule")
        jobs = {j["id"]: j for j in inp["jobs"]}
        if not isinstance(sched, dict) or set(sched) != set(jobs):
            return CheckResult(False, 0.0, ["incomplete_schedule"], f"schedule must contain exactly every job id; missing {sorted(set(jobs) - set(sched or {}))[:5]}")
        start: dict[str, int] = {}
        dur: dict[str, int] = {}
        req: dict[str, dict[str, int]] = {}
        for k, e in sched.items():
            try:
                s, m = e["start"], e["mode"]
                if isinstance(s, bool) or isinstance(m, bool) or int(s) != s or int(m) != m:
                    raise ValueError
                s, m = int(s), int(m)
            except (TypeError, KeyError, ValueError):
                return CheckResult(False, 0.0, ["malformed_entry"], f"entry for {k} must be {{start:int, mode:int}}")
            if not 0 <= m < len(jobs[k]["modes"]):
                return CheckResult(False, 0.0, ["bad_mode"], f"{k}: mode index {m} out of range")
            start[k], dur[k], req[k] = s, jobs[k]["modes"][m]["duration"], jobs[k]["modes"][m]["requires"]
        for k, j in jobs.items():
            if start[k] < j["release"]:
                return CheckResult(False, 0.0, ["release_violated"], f"{k} starts at {start[k]} before release {j['release']}")
            for p in j["after"]:
                if start[k] < start[p] + dur[p]:
                    return CheckResult(False, 0.0, ["precedence_violated"], f"{k} starts at {start[k]} before predecessor {p} finishes at {start[p] + dur[p]}")
        for r_name in {r for q in req.values() for r in q}:
            if r_name not in inp["resources"]:
                return CheckResult(False, 0.0, ["unknown_resource"], f"chosen mode uses unknown resource {r_name}")
        for r, spec in inp["resources"].items():
            cal = spec["calendar"]
            users = [(start[k], start[k] + dur[k], req[k][r]) for k in jobs if r in req[k]]
            points = sorted({s for s, _, _ in users} | {t for t, _ in cal})
            for t in points:
                cap = 0
                for bt, c in cal:
                    if bt <= t:
                        cap = c
                    else:
                        break
                used = sum(u for s, e, u in users if s <= t < e)
                if used > cap:
                    return CheckResult(False, 0.0, ["capacity_violated"], f"resource {r} uses {used} > capacity {cap} at t={t}")
        makespan = max(start[k] + dur[k] for k in jobs)
        tard = sum(j["weight"] * max(0, start[k] + dur[k] - j["due"]) for k, j in jobs.items() if j["due"] is not None)
        cost = makespan + tard
        score = min(1.0, h["lower_bound"] / cost) if cost > 0 else 1.0
        return CheckResult(True, score, [], f"valid schedule; makespan = {makespan}, weighted tardiness = {tard}, cost = {cost}",
                           {"cost": cost, "makespan": makespan, "tardiness": tard})
