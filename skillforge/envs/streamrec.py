"""Environment: reconstruction of concurrent write sessions from a degraded event log.

A simulated cluster runs concurrent sessions (open / write / checkpoint / commit / abort) on nodes that
crash; a crash rolls every session on the node back to its last checkpoint and the session later recovers,
possibly on another node. The log that reaches the candidate has lost most session ids, is reordered by
clock jitter, and has dropped and duplicated events. The hidden truth is the simulator's own record
(which session produced each event, and each session's final state), so the checker only compares with
generation-time data: this module contains NO tracker, NO inference logic and NO solver.
"""

from __future__ import annotations

import random
from typing import Any

from .base import CheckResult, Family, Instance, TaskEnvironment

CPU_LIMIT_S = 1.0
STATUSES = ("committed", "aborted", "active", "down")

SPEC = f"""
TASK: Reconstruct concurrent write sessions of a storage cluster from a degraded event log: decide which
session each anonymous event belongs to, and report every session's final state.

THE SYSTEM THAT PRODUCED THE LOG
  * A session lives on one node at a time and goes through:
      open        -> the session becomes active on a node; its stream offset and durable offset are 0;
      write       -> appends `len` bytes at the session's current stream offset `off`; the offset becomes off+len;
      checkpoint  -> everything written so far becomes durable; the event's `off` is the current stream offset;
      commit      -> like a checkpoint, then the session ends with status "committed"; `off` = final offset;
      abort       -> the session ends with status "aborted"; bytes after the last checkpoint are discarded;
      recover     -> see below.
  * A node may crash (event type "crash", belongs to no session). Every session that is active on that node
    at that moment goes down and its stream offset is rolled back to its durable offset (writes after its
    last checkpoint are lost). A down session produces no events until its "recover" event, which makes it
    active again on the node named in that event (possibly a different node); `off` of the recover event is
    the offset it resumes from. Sessions on other nodes are unaffected.
  * At the end of the log a session that never committed or aborted has status "active", or "down" if it
    crashed and has not recovered.
  * durable = the durable offset at the end (for a committed session: its final offset).

INPUT (state["input"]): {{"nodes": ["n0", ...], "events": [{{"i": <index>, "t": <timestamp int>,
     "node": "<node>", "type": "open|write|checkpoint|commit|abort|recover|crash",
     "off": <int or null>, "len": <int or null>, "sid": "<session id>" or null}}, ...]}}
  * `off` is given for write, checkpoint, commit and recover events; `len` only for write events.
  * Every "open" event carries its session id, and every session has exactly one open event in the log.
    Other session events carry their `sid` only sometimes; "crash" events have sid null.

DEGRADATION of the log
  * clock jitter: each event's timestamp was increased by a small random amount and the log is sorted by
    these timestamps, so events may appear slightly out of their true order (also across sessions);
    an "open" event is never delayed past later events of its own session;
  * some write / checkpoint / abort / recover events are missing from the log (their effects did happen);
  * some session events appear twice: the second copy has the same node/type/off/len, a later timestamp,
    and its own, possibly different, sid visibility. A duplicate belongs to the same session as the original
    and must not be applied twice.

OUTPUT (set state["output"]):
  {{"assignments": {{"<event index i>": "<session id>", ...}},
    "sessions": {{"<session id>": {{"status": "committed|aborted|active|down", "durable": <int>}}, ...}}}}
  `assignments` must cover every event whose type is not "crash" and whose sid is null in the input.

SCORING: 0.6 * (fraction of the anonymous non-crash events assigned to the true session)
       + 0.4 * (mean over sessions of: 0.5 if status is correct + 0.5 if durable is correct).
  A malformed output scores 0. If a log has no anonymous events, the first term counts as fully correct.

LIMITS: at most {CPU_LIMIT_S} second of CPU time per log (measure with time.process_time(); exceeding about
twice that aborts the log with score 0). The skill must be deterministic code: language-model nodes are not
available at run time for this task. Logs have 30 to 600 events, 2 to 5 nodes and 3 to 24 sessions.
"""

PROFILES = {
    # hide: P(sid hidden); jitter: max timestamp delay; drop / dup: per-event probabilities; crash: per-step
    "mostly_labelled": dict(hide=0.30, jitter=2, drop=0.01, dup=0.02, crash=0.010, sessions=(3, 8), steps=(40, 140)),
    "anonymous":       dict(hide=0.85, jitter=3, drop=0.01, dup=0.02, crash=0.012, sessions=(4, 10), steps=(60, 180)),
    "crashy":          dict(hide=0.65, jitter=3, drop=0.02, dup=0.03, crash=0.050, sessions=(4, 10), steps=(60, 180)),
    "lossy":           dict(hide=0.65, jitter=4, drop=0.10, dup=0.10, crash=0.015, sessions=(4, 10), steps=(60, 180)),
    "jittery":         dict(hide=0.70, jitter=14, drop=0.02, dup=0.04, crash=0.015, sessions=(4, 10), steps=(60, 180)),
    "crowded":         dict(hide=0.80, jitter=5, drop=0.03, dup=0.04, crash=0.020, sessions=(10, 16), steps=(120, 260)),
    "large_mixed":     dict(hide=0.80, jitter=8, drop=0.06, dup=0.06, crash=0.030, sessions=(16, 24), steps=(300, 480)),
}
LENS = (64, 64, 128, 128, 256, 512)


class StreamRecEnv(TaskEnvironment):
    name = "session_log_reconstruction"
    version = "1"
    allow_llm_nodes = False

    def public_spec(self) -> str:
        return SPEC

    def public_examples(self) -> list[dict[str, Any]]:
        ev = [{"i": 0, "t": 3, "node": "n0", "type": "open", "off": None, "len": None, "sid": "s0"},
              {"i": 1, "t": 5, "node": "n0", "type": "write", "off": 0, "len": 128, "sid": None},
              {"i": 2, "t": 9, "node": "n0", "type": "checkpoint", "off": 128, "len": None, "sid": "s0"},
              {"i": 3, "t": 12, "node": "n0", "type": "crash", "off": None, "len": None, "sid": None}]
        return [{"input": {"nodes": ["n0", "n1"], "events": ev},
                 "output": {"assignments": {"1": "s0"}, "sessions": {"s0": {"status": "down", "durable": 128}}},
                 "note": "format illustration only"}]

    def families(self) -> list[Family]:
        return [Family("mostly_labelled", 0.6, "most events keep their session id"),
                Family("anonymous", 1.2, "few events keep their session id"),
                Family("crashy", 1.3, "frequent node crashes, rollbacks and recoveries"),
                Family("lossy", 1.2, "many missing and duplicated events"),
                Family("jittery", 1.2, "strong timestamp jitter (heavy reordering)"),
                Family("crowded", 1.2, "many concurrent sessions"),
                Family("large_mixed", 1.0, "large logs with every kind of degradation", audit_only=True)]

    def generate(self, family: str, rng: random.Random, instance_id: str) -> Instance:
        p = PROFILES[family]
        n_nodes = rng.randint(2, 5)
        nodes = [f"n{k}" for k in range(n_nodes)]
        n_sess = rng.randint(*p["sessions"])
        steps = rng.randint(*p["steps"])
        sess: dict[str, dict[str, Any]] = {}
        true_events: list[dict[str, Any]] = []
        t = 0
        to_open = n_sess

        def emit(typ, node, sid=None, off=None, ln=None):
            true_events.append({"t": t, "node": node, "type": typ, "off": off, "len": ln, "sid": sid})

        for step in range(steps):
            t += rng.randint(1, 4)
            live = [s for s, v in sess.items() if v["status"] in ("active", "down")]
            r = rng.random()
            if to_open and (not live or r < max(0.06, 1.5 * to_open / max(1, steps - step))):
                sid = f"s{n_sess - to_open}"
                to_open -= 1
                node = rng.choice(nodes)
                sess[sid] = {"status": "active", "node": node, "off": 0, "durable": 0}
                emit("open", node, sid)
                continue
            if r > 1 - p["crash"]:
                busy = sorted({v["node"] for v in sess.values() if v["status"] == "active"})
                if busy:
                    node = rng.choice(busy)
                    emit("crash", node)
                    for v in sess.values():
                        if v["status"] == "active" and v["node"] == node:
                            v["status"], v["off"] = "down", v["durable"]
                    continue
            if not live:
                continue
            sid = rng.choice(live)
            v = sess[sid]
            if v["status"] == "down":
                if rng.random() < 0.5:
                    v["status"], v["node"] = "active", rng.choice(nodes)
                    emit("recover", v["node"], sid, v["off"])
                continue
            a = rng.random()
            if a < 0.66:
                ln = rng.choice(LENS)
                emit("write", v["node"], sid, v["off"], ln)
                v["off"] += ln
            elif a < 0.88:
                v["durable"] = v["off"]
                emit("checkpoint", v["node"], sid, v["off"])
            elif a < 0.92 and v["off"] > 0:
                v["durable"] = v["off"]
                v["status"] = "committed"
                emit("commit", v["node"], sid, v["off"])
            elif a >= 0.985:
                v["status"] = "aborted"
                emit("abort", v["node"], sid)

        # ---- degradation channel
        open_t = {e["sid"]: e["t"] for e in true_events if e["type"] == "open"}
        log = []
        for k, e in enumerate(true_events):
            droppable = e["type"] in ("write", "checkpoint", "abort", "recover")
            if droppable and rng.random() < p["drop"]:
                continue
            copies = [dict(e, order=k)]
            if e["type"] not in ("crash", "open") and rng.random() < p["dup"]:
                copies.append(dict(e, order=k, t=e["t"] + rng.randint(1, 3 + 2 * p["jitter"])))
            for c in copies:
                if c["type"] != "open":
                    c["t"] += rng.randint(0, p["jitter"])
                    if c["sid"] is not None:
                        c["t"] = max(c["t"], open_t[c["sid"]])
                log.append(c)
        log.sort(key=lambda c: (c["t"], 0 if c["type"] == "open" else 1, c["order"]))
        events, truth = [], {}
        for i, c in enumerate(log):
            hidden = c["type"] not in ("open", "crash") and rng.random() < p["hide"]
            events.append({"i": i, "t": c["t"], "node": c["node"], "type": c["type"], "off": c["off"],
                           "len": c["len"], "sid": None if (hidden or c["type"] == "crash") else c["sid"]})
            if hidden:
                truth[str(i)] = c["sid"]
        final = {s: {"status": v["status"], "durable": v["durable"]} for s, v in sess.items()}
        return Instance(instance_id, family, {"nodes": nodes, "events": events},
                        {"assignments": truth, "sessions": final}, [family])

    def check(self, instance: Instance, output: Any) -> CheckResult:
        ta, ts = instance.hidden["assignments"], instance.hidden["sessions"]
        if not isinstance(output, dict) or not isinstance(output.get("assignments"), dict) \
                or not isinstance(output.get("sessions"), dict):
            return CheckResult(False, 0.0, ["malformed_output"],
                               "output must be {\"assignments\": {...}, \"sessions\": {...}}")
        ga, gs = output["assignments"], output["sessions"]
        right = sum(1 for k, s in ta.items() if ga.get(k) == s)
        missing = sum(1 for k in ta if k not in ga)
        a_part = right / len(ta) if ta else 1.0
        st_ok = du_ok = 0
        for s, v in ts.items():
            g = gs.get(s)
            if isinstance(g, dict):
                st_ok += g.get("status") == v["status"]
                d = g.get("durable")
                du_ok += (type(d) is int) and d == v["durable"]
        s_part = (0.5 * st_ok + 0.5 * du_ok) / len(ts) if ts else 1.0
        viol = ["unassigned_events"] if missing else []
        msg = (f"anonymous events: {right}/{len(ta)} assigned to the right session ({missing} left unassigned); "
               f"sessions: {st_ok}/{len(ts)} statuses and {du_ok}/{len(ts)} durable offsets correct")
        return CheckResult(True, 0.6 * a_part + 0.4 * s_part, viol, msg,
                           {"assign": a_part, "state": s_part})
