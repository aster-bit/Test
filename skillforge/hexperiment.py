"""Hypothesis-driven synthesis experiment (SkillForge v3 slice).

    python -m skillforge.hexperiment diag    --name N --env docrepair    # cheap diagnostic, BEFORE any search
    python -m skillforge.hexperiment prepare --name N --env docrepair    # freeze protocol (needs a passed diag)
    python -m skillforge.hexperiment run     --name N --env docrepair --replicate R
    python -m skillforge.hexperiment report  --name N

Arms (equal synthesis-call budget for P and H; A is one call):
  A  one-shot synthesis: one program, as written.
  P  program evolution: the existing engine (experiment.Runner.arm_C) - unit of search = program.
  H  hypothesis evolution - unit of search = algorithmic hypothesis:
       propose K hypotheses (1 call) -> implement each (K calls) -> portfolio loop:
         exploit : refine the best implementation of the leading hypothesis (mechanism kept);
         explore : re-implement an untested hypothesis, write a second independent implementation of the
                   runner-up (separates idea from implementation), recombine two hypotheses, or pivot to a
                   new hypothesis from the measured evidence. At least `explore_share` of the post-initial
                   calls go to exploration; stagnation forces a pivot.

The diagnostic answers one question before money is spent on search: do different hypotheses turn into
different working algorithms? It implements each hypothesis twice, independently, and compares
within-hypothesis with between-hypothesis code similarity (permutation test), plus a control group of
plain independent generations.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .env_eval import EnvEvalResult, bootstrap_mean_ci, paired_diff_ci
from .envs import get_env
from .envs.base import build_split, split_hash
from .experiment import ROOT, ExpConfig, Runner, exp_dir, make_splits, sha
from .hypothesis import Hypothesis, HypothesisParseError, HypothesisSynthesizer
from .novelty import NOVEL_AST_THRESHOLD, ast_similarity, behavioural_distance, graph_signature
from .open_synth import Candidate, OpenSynthesizer
from .stats import mean

FROZEN = ["skillforge/envs/base.py", "skillforge/llm.py", "skillforge/ir.py", "skillforge/open_synth.py",
          "skillforge/experiment.py", "skillforge/env_eval.py", "skillforge/novelty.py", "skillforge/executor.py",
          "skillforge/sandbox.py", "skillforge/sandbox_runner.py", "skillforge/hypothesis.py",
          "skillforge/hexperiment.py"]
CONF = 1 - 0.05 / 3   # three confirmatory paired comparisons per replicate


@dataclass
class HConfig(ExpConfig):
    k_hyp: int = 4
    explore_share: float = 0.4


PREREG = {
    "G1": "H_final beats P_final (program evolution, equal budget) on the AUDIT mean score: paired bootstrap CI of "
          "the difference (Bonferroni over 3 comparisons) has lower bound > 0.",
    "G2": "H_final beats A (one-shot synthesis) on AUDIT (same test).",
    "G3_paradigm_transition": "H_final beats H_initial (the best program after the initial round of the H arm) on "
                              "AUDIT (same test) AND H_final implements a different hypothesis than H_initial AND the "
                              f"AST similarity of the two programs is < {NOVEL_AST_THRESHOLD}. This is the "
                              "operational definition of a paradigm transition without human intervention.",
    "descriptive": "P_final - P_initial on AUDIT; best implementation of every hypothesis on AUDIT; per arm: number "
                   f"of code clusters (single linkage, AST similarity >= {NOVEL_AST_THRESHOLD}), mean pairwise AST "
                   "similarity, mean pairwise behavioural distance on SELECTION; improvements of the best SELECTION "
                   "score that happened after >= 2 consecutive non-improving steps (escapes from stagnation); "
                   "synthesis cost.",
    "attribution": "Failure attribution labels are derived from measurements only: a hypothesis with no executable "
                   "implementation is 'untested' (never 'refuted'); 'mechanism inferior' requires >= 2 independent "
                   "implementations each significantly below the leader; with one implementation the label is "
                   "'idea and implementation not separated'. Causes named by the model are stored as conjectures.",
    "statistics": "paired bootstrap over audit instances; a claim is SUPPORTED only if it holds in every replicate, "
                  "MIXED if in some, NOT SUPPORTED if in none.",
    "leakage_controls": "generator sees the public spec, format examples, public family descriptions, programs, and "
                        "for DEV instances the input, its own output, score and checker message. SELECTION: aggregate "
                        "use only (leader / champion choice). AUDIT: one evaluation at the end.",
    "success_criterion": "The method is said to beat the previous one in a domain iff G1 is SUPPORTED there; a "
                         "paradigm transition is said to be demonstrated iff G3 is SUPPORTED there. Anything else is "
                         "reported as negative or mixed.",
}
DIAG_RULE = ("PASS iff (a) >= 3 of the proposed hypotheses have an executable implementation, (b) mean AST similarity "
             "within a hypothesis exceeds the mean between hypotheses with one-sided permutation p <= 0.05, "
             "(c) mean between-hypothesis AST similarity is lower than the mean pairwise similarity of the control "
             "group (plain independent generations), (d) the usable programs receive different scores on >= 50% of "
             "the calibration instances.")


def clusters(mods: list[str], thr: float = NOVEL_AST_THRESHOLD) -> int:
    parent = list(range(len(mods)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for i, j in itertools.combinations(range(len(mods)), 2):
        if ast_similarity(mods[i], mods[j]) >= thr:
            parent[find(i)] = find(j)
    return len({find(i) for i in range(len(mods))})


def diversity(recs: list[dict[str, Any]]) -> dict[str, Any]:
    us = [r for r in recs if r.get("usable")]
    pairs = list(itertools.combinations(us, 2))
    return {"programs": len(recs), "usable": len(us), "code_clusters": clusters([r["module"] for r in us]),
            "mean_pairwise_ast_similarity": round(mean([ast_similarity(a["module"], b["module"]) for a, b in pairs]), 4) if pairs else None,
            "mean_pairwise_behavioural_distance": round(mean([behavioural_distance(a["selection_scores"], b["selection_scores"])
                                                               for a, b in pairs]), 4) if pairs else None,
            "graph_signatures": sorted({r.get("graph_signature", "") for r in us})}


def escapes(best_seq: list[float], patience: int = 2) -> int:
    """Number of improvements of the running best that came after >= `patience` non-improving steps."""
    n, stale = 0, 0
    for prev, cur in zip(best_seq, best_seq[1:]):
        if cur > prev + 1e-9:
            n += stale >= patience
            stale = 0
        else:
            stale += 1
    return n


class HRunner(Runner):
    def usable_problem(self, c: Candidate) -> str | None:
        if c.pipeline and not getattr(self.env, "allow_llm_nodes", True) and "L" in graph_signature(c.pipeline):
            return "language-model nodes are not available at run time for this task"
        return super().usable_problem(c)

    # ---------------------------------------------------------------- arm A
    def arm_A(self) -> str:
        self.synth_A = OpenSynthesizer(self.env, self.provider, self.cfg.model, self.cfg.effort)
        c = self.synth_A.generate("A", 0)
        self.finish(c)
        return c.id

    # ---------------------------------------------------------------- arm H
    def arm_H(self) -> None:
        cfg = self.cfg
        synth = HypothesisSynthesizer(self.env, self.provider, cfg.model, cfg.effort)
        self.synth_H = synth
        self.hyps: dict[str, Hypothesis] = {}
        self.impls: dict[str, list[str]] = {}
        self.h_trace: list[dict[str, Any]] = []
        self.conjectures: list[dict[str, Any]] = []
        self.h_initial_ids: list[str] = []
        self.h_first_leader: str | None = None
        idx = 0

        def left():
            return cfg.budget - synth.calls

        def sel(cid):
            return self.candidates[cid]["selection"]["mean_score"]

        def usable(hid):
            return [c for c in self.impls.get(hid, []) if self.candidates[c]["usable"]]

        def best_of(hid):
            u = usable(hid)
            return max(u, key=sel) if u else None

        def leader():
            bs = [(best_of(h), h) for h in self.hyps if best_of(h)]
            return max(bs, key=lambda t: sel(t[0])) if bs else (None, None)

        def add(c: Candidate, hid: str) -> Candidate:
            c = self.make_usable(synth, c, "H", left)
            self.finish(c)
            for cid in {c.id} | {k for k, r in self.candidates.items() if r["arm"] == "H" and "hypothesis" not in r}:
                self.candidates[cid]["hypothesis"] = hid
                if cid not in self.impls.setdefault(hid, []):
                    self.impls[hid].append(cid)
            return c

        def evidence() -> list[dict[str, Any]]:
            out = []
            for hid, h in self.hyps.items():
                recs = [self.candidates[c] for c in self.impls.get(hid, [])]
                us = [r for r in recs if r["usable"]]
                e = {"id": hid, "name": h.name, "mechanism": h.mechanism, "implementations_attempted": len(recs),
                     "implementations_working": len(us)}
                if us:
                    b = max(us, key=lambda r: r["dev"]["mean_score"])
                    e.update(best_dev_mean_score=round(b["dev"]["mean_score"], 4),
                             dev_scores_of_all_working_implementations=[round(r["dev"]["mean_score"], 4) for r in us],
                             dev_score_per_family=b["dev"]["per_family"], dev_violations=b["dev"]["violations"],
                             dev_runtime_errors=b["dev"]["errors"])
                else:
                    e["note"] = ("no working implementation - the idea is UNTESTED, not refuted; problems: "
                                 + " | ".join((r.get("problem") or "")[:200] for r in recs))
                out.append(e)
            return out

        # ---- 1. hypotheses before any code
        hs: list[Hypothesis] = []
        for _ in range(2):
            try:
                hs = synth.propose(cfg.k_hyp)
                break
            except HypothesisParseError as e:
                self.log(f"[H] hypothesis proposal unusable: {e}")
        if not hs:
            return
        for h in hs:
            self.hyps[h.id] = h
            self.log(f"[H] hypothesis {h.id}: {h.name} - {h.mechanism[:140]}")
        # ---- 2. one implementation of each
        with ThreadPoolExecutor(max_workers=len(hs)) as pool:
            cs = list(pool.map(lambda t: synth.implement(t[1], "H", t[0]), enumerate(hs)))
        idx = len(hs)
        for h, c in zip(hs, cs):
            c = add(c, h.id)
            self.h_initial_ids.append(c.id)
        lc, lh = leader()
        self.h_first_leader = lh
        self.h_initial_best = lc
        best = sel(lc) if lc else 0.0
        self.h_best_seq = [best]
        init_calls = synth.calls
        explore_used, stagnant, cyc = 0, 0, 0
        attempts = {h.id: 1 for h in hs}
        second_done: set[str] = set()

        # ---- 3. portfolio loop
        while left() > 0:
            lc, lh = leader()
            post = synth.calls - init_calls
            untested = [h for h in self.hyps if not usable(h) and attempts.get(h, 1) < 2 and self.hyps[h].origin == "initial"]
            want_explore = lc is None or stagnant >= cfg.stagnation_patience or explore_used < cfg.explore_share * (post + 1)
            move, hid, child, ref, cause = "", "", None, lc, None
            calls_before = synth.calls
            if want_explore:
                tested = [h for h in self.hyps if usable(h)]
                runner = [h for h in tested if h != lh and h not in second_done and self.hyps[h].origin == "initial"
                          and len([c for c in usable(h) if self.candidates[c]["origin"].startswith("implement")]) == 1]
                options = []
                if untested:
                    options = ["reimplement"]
                elif stagnant >= cfg.stagnation_patience or len(tested) < 2:
                    options = ["pivot"]
                else:
                    order = ["recombine", "second_impl", "pivot"]
                    options = order[cyc % 3:] + order[:cyc % 3]
                    cyc += 1
                for m in options:
                    if m == "reimplement":
                        hid = untested[0]
                        attempts[hid] = attempts.get(hid, 1) + 1
                        child = synth.implement(self.hyps[hid], "H", idx, "independent implementation; an earlier attempt was not executable")
                    elif m == "second_impl" and runner:
                        hid = max(runner, key=lambda h: sel(best_of(h)))
                        second_done.add(hid)
                        child = synth.implement(self.hyps[hid], "H", idx, "second, independent implementation of this hypothesis")
                    elif m == "recombine" and len(tested) >= 2:
                        la = self.candidates[lc]["selection_scores"]
                        other = max((h for h in tested if h != lh), key=lambda h: mean(
                            [max(x, y) for x, y in zip(la, self.candidates[best_of(h)]["selection_scores"])]))
                        hid = f"H{len(self.hyps)}"
                        hnew, child = synth.recombine_h(self.hyps[lh], self.cands_obj[lc], self.hyps[other],
                                                        self.cands_obj[best_of(other)], evidence(), hid, "H", idx)
                        self.hyps[hid] = hnew or Hypothesis(hid, "unparsed", "(hypothesis block missing)", "", origin="recombine",
                                                            parents=[lh, other], created_index=idx)
                    elif m == "pivot":
                        hid = f"H{len(self.hyps)}"
                        hnew, child = synth.pivot(evidence(), hid, "H", idx)
                        self.hyps[hid] = hnew or Hypothesis(hid, "unparsed", "(hypothesis block missing)", "", origin="pivot",
                                                            parents=list(self.hyps), created_index=idx)
                    if child is not None:
                        move = m
                        break
                if stagnant >= cfg.stagnation_patience:
                    stagnant = 0
            if child is None:
                move, hid = "refine", lh
                child, cause = synth.refine(self.hyps[lh], self.cands_obj[lc], self.dev_feedback(self.cands_obj[lc]), "H", idx)
            idx += 1
            self.log(f"[H] move {move} on {hid}")
            child = add(child, hid)
            rec = self.candidates[child.id]
            step = {"move": move, "hypothesis": hid, "child": child.id, "usable": rec["usable"], "leader_before": lh,
                    "explore": move != "refine", "calls": synth.calls - calls_before}
            if move != "refine":
                explore_used += synth.calls - calls_before
            if rec["usable"] and ref:
                p = self.candidates[ref]
                d, lo, hi = paired_diff_ci(rec["selection_scores"], p["selection_scores"], 0.95, seed=idx)
                step.update(compared_to=ref, delta_vs_leader=d, ci95=[lo, hi], significant=lo > 0,
                            ast_similarity_to_leader=round(ast_similarity(p["module"], child.module), 4))
            if cause:
                self.conjectures.append({"child": child.id, "parent": ref, "hypothesis": hid, "llm_cause": cause,
                                         "measured_child_significantly_better": bool(step.get("significant")),
                                         "status": ("consistent with measurement" if cause in ("IMPLEMENTATION", "PARAMETERS")
                                                    and step.get("significant") else "unverified conjecture")})
            if rec["usable"] and sel(child.id) > best + 1e-9:
                best, stagnant = sel(child.id), 0
            else:
                stagnant += 1
            step["best_selection_after"] = best
            self.h_best_seq.append(best)
            self.h_trace.append(step)
            self.log(f"[H] step {len(self.h_trace)}: {move}/{hid} usable={rec['usable']} "
                     f"delta={step.get('delta_vs_leader')} best={best:.4f}")

    # ---------------------------------------------------------------- measured failure attribution
    def attribution(self) -> dict[str, Any]:
        def sel(cid):
            return self.candidates[cid]["selection"]["mean_score"]
        bests = {}
        for hid in self.hyps:
            u = [c for c in self.impls.get(hid, []) if self.candidates[c]["usable"]]
            if u:
                bests[hid] = max(u, key=sel)
        lead = max(bests, key=lambda h: sel(bests[h])) if bests else None
        fams = sorted({f for b in bests.values() for f in self.candidates[b]["selection"]["per_family"]})
        fam_mean = {f: mean([self.candidates[b]["selection"]["per_family"].get(f, 0.0) for b in bests.values()]) for f in fams}
        out = {}
        for hid, h in self.hyps.items():
            recs = [self.candidates[c] for c in self.impls.get(hid, [])]
            indep = [r for r in recs if r["usable"] and r["origin"].split(":")[0] in ("implement", "pivot", "recombine")]
            a: dict[str, Any] = {"name": h.name, "origin": h.origin, "parents": h.parents,
                                 "implementations_attempted": len(recs),
                                 "implementations_working": sum(r["usable"] for r in recs),
                                 "independent_working_implementations": len(indep)}
            if hid not in bests:
                a["label"] = "untested (no executable implementation; the idea is not refuted)"
                a["problems"] = [(r.get("problem") or "")[:200] for r in recs]
                out[hid] = a
                continue
            b = self.candidates[bests[hid]]
            a.update(best_implementation=bests[hid], best_selection=round(sel(bests[hid]), 4),
                     runtime_error_rate_dev=round(b["dev"]["errors"] / max(1, b["dev"]["n"]), 3))
            if hid == lead:
                a["label"] = "leader"
            else:
                L = self.candidates[bests[lead]]
                worse = []
                for r in (indep or [b]):
                    d, lo, hi = paired_diff_ci(r["selection_scores"], L["selection_scores"], 0.95, seed=3)
                    worse.append(hi < 0)
                d, lo, hi = paired_diff_ci(b["selection_scores"], L["selection_scores"], 0.95, seed=3)
                a["best_minus_leader"] = [round(d, 4), round(lo, 4), round(hi, 4)]
                if hi >= 0:
                    a["label"] = "not distinguishable from the leader on SELECTION"
                elif len(indep) >= 2 and all(worse):
                    a["label"] = "mechanism inferior (supported: >= 2 independent implementations each significantly below the leader)"
                else:
                    a["label"] = "below the leader, but idea and implementation not separated (one independent implementation)"
            if len(indep) >= 2:
                x, y = sorted(indep, key=lambda r: -r["selection"]["mean_score"])[:2]
                d, lo, hi = paired_diff_ci(x["selection_scores"], y["selection_scores"], 0.95, seed=5)
                a["implementation_sensitivity"] = {"best_minus_second_independent": [round(d, 4), round(lo, 4), round(hi, 4)],
                                                   "significant": lo > 0}
            adv = {f: b["selection"]["per_family"].get(f, 0.0) - fam_mean[f] for f in fams}
            s = [adv[f] for f in h.strong_families if f in adv]
            w = [adv[f] for f in h.weak_families if f in adv]
            a["own_prediction"] = ("not testable" if not s or not w or len(bests) < 2 else
                                   {"strong_minus_weak_relative_advantage": round(mean(s) - mean(w), 4),
                                    "confirmed": mean(s) > mean(w)})
            out[hid] = a
        return out


# ------------------------------------------------------------------------------------------- diagnostic
def perm_p(labels: list[str], sim: dict[tuple[int, int], float], iters: int = 5000, seed: int = 0) -> tuple[float, float, float]:
    def stat(lb):
        w = [v for (i, j), v in sim.items() if lb[i] == lb[j]]
        b = [v for (i, j), v in sim.items() if lb[i] != lb[j]]
        return (mean(w), mean(b)) if w and b else (None, None)
    w0, b0 = stat(labels)
    if w0 is None:
        return float("nan"), float("nan"), 1.0
    rng = random.Random(seed)
    ge = 0
    lb = labels[:]
    for _ in range(iters):
        rng.shuffle(lb)
        w, b = stat(lb)
        ge += (w - b) >= (w0 - b0) - 1e-12
    return w0, b0, (ge + 1) / (iters + 1)


def diag(cfg: HConfig, n: int = 36, impl_per_h: int = 2, control: int = 4, provider=None) -> dict[str, Any]:
    env = get_env(cfg.env)
    out = exp_dir(cfg.name)
    if (out / "diag.json").exists():
        raise SystemExit("diag.json exists - the diagnostic is run once per experiment name")
    cal = build_split(env, "calibration", n, cfg.seed + 777_000)
    R = HRunner(cfg, env, {"dev": cal, "selection": cal, "audit": cal}, out, provider)
    synth = HypothesisSynthesizer(env, R.provider, cfg.model, cfg.effort)
    hs = synth.propose(cfg.k_hyp)
    jobs = [("H", h, j) for h in hs for j in range(impl_per_h)] + [("G", None, j) for j in range(control)]

    def make(t):
        kind, h, j = t
        k = jobs.index(t)
        return synth.implement(h, "D", k, f"independent implementation #{j + 1}") if kind == "H" else \
            synth.generate("G", k, attempt_note=f"independent attempt #{j + 1}")
    with ThreadPoolExecutor(max_workers=cfg.parallel) as pool:
        cs = list(pool.map(make, jobs))
    progs = []
    for (kind, h, j), c in zip(jobs, cs):
        problem = R.usable_problem(c)
        rec = {"group": h.id if h else "control", "kind": kind, "id": c.id, "usable": problem is None, "problem": problem,
               "sha256": hashlib.sha256(c.module.encode()).hexdigest(), "strategy": c.strategy[:600], "module": c.module,
               "graph": graph_signature(c.pipeline) if c.pipeline else ""}
        if problem is None:
            ev = R.evaluator.evaluate(c.ir, cal, "calibration")
            rec.update(scores=ev.scores, mean_score=mean(ev.scores), per_family=ev.summary()["per_family"])
        progs.append(rec)
    H = [p for p in progs if p["kind"] == "H" and p["usable"]]
    G = [p for p in progs if p["kind"] == "G" and p["usable"]]
    sim = {(i, j): ast_similarity(H[i]["module"], H[j]["module"]) for i, j in itertools.combinations(range(len(H)), 2)}
    beh = {(i, j): -behavioural_distance(H[i]["scores"], H[j]["scores"]) for i, j in sim}
    labels = [p["group"] for p in H]
    w, b, p_ast = perm_p(labels, sim)
    wb, bb, p_beh = perm_p(labels, beh)
    ctrl = mean([ast_similarity(x["module"], y["module"]) for x, y in itertools.combinations(G, 2)]) if len(G) > 1 else None
    allp = H + G
    differ = sum(1 for i in range(n) if len({round(p["scores"][i], 6) for p in allp}) > 1) / n if len(allp) > 1 else 0.0
    tested = len({p["group"] for p in H})
    crit = {"a_hypotheses_with_working_implementation": tested, "a_ok": tested >= 3,
            "b_ast_within": w, "b_ast_between": b, "b_perm_p": p_ast, "b_ok": w == w and w > b and p_ast <= 0.05,
            "c_control_mean_pairwise_ast": ctrl, "c_ok": ctrl is not None and b == b and b < ctrl,
            "d_discrimination": differ, "d_ok": differ >= 0.5}
    res = {"rule": DIAG_RULE, "passed": all(crit[k] for k in ("a_ok", "b_ok", "c_ok", "d_ok")), "criteria": crit,
           "behaviour": {"within_distance": -wb if wb == wb else None, "between_distance": -bb if bb == bb else None,
                         "perm_p": p_beh},
           "hypotheses": [h.record() for h in hs],
           "group_scores": {g: [round(p["mean_score"], 4) for p in allp if p["group"] == g]
                            for g in sorted({p["group"] for p in allp})},
           "pairwise_ast": [{"a": H[i]["id"], "b": H[j]["id"], "same_hypothesis": labels[i] == labels[j],
                             "similarity": round(v, 4)} for (i, j), v in sim.items()],
           "programs": [{k: v for k, v in p.items() if k not in ("module", "scores")} for p in progs],
           "calls": synth.calls, "synthesis_cost_usd": synth.cost, "environment_sha256": env.source_hash(),
           "config": cfg.__dict__, "n": n}
    (out / "diag.json").write_text(json.dumps(res, indent=2))
    (out / "diag_programs.json").write_text(json.dumps([{"id": p["id"], "group": p["group"], "module": p["module"]} for p in progs], indent=1))
    return res


# ------------------------------------------------------------------------------------------- prereg / run
def prepare(cfg: HConfig, replicates: int = 2) -> dict[str, Any]:
    env = get_env(cfg.env)
    d = exp_dir(cfg.name)
    if (d / "prereg.json").exists():
        raise SystemExit("prereg.json already exists - a pre-registration is never overwritten")
    dj = json.loads((d / "diag.json").read_text()) if (d / "diag.json").exists() else None
    if not dj or not dj["passed"] or dj["environment_sha256"] != env.source_hash():
        raise SystemExit("diagnostic missing, failed, or run against a different environment version")
    splits = make_splits(cfg, env)
    pre = {"config": cfg.__dict__, "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "environment": {"name": env.name, "version": env.version, "source_sha256": env.source_hash()},
           "frozen_files": {f: sha(ROOT / f) for f in FROZEN},
           "splits": {k: {"n": len(v), "sha256": split_hash(v), "families": sorted({i.family for i in v})} for k, v in splits.items()},
           "hypotheses": PREREG, "replicates": replicates, "diagnostic": dj["criteria"]}
    (d / "prereg.json").write_text(json.dumps(pre, indent=2))
    return pre


def verify(cfg: HConfig) -> dict[str, Any]:
    pre = json.loads((exp_dir(cfg.name) / "prereg.json").read_text())
    strip = lambda c: {k: v for k, v in c.items() if k != "replicate"}
    if strip(pre["config"]) != strip(cfg.__dict__):
        raise SystemExit("config differs from pre-registration")
    if cfg.replicate >= pre["replicates"]:
        raise SystemExit(f"only {pre['replicates']} replicates were pre-registered")
    env = get_env(cfg.env)
    problems = [f"frozen file changed: {f}" for f, h in pre["frozen_files"].items() if sha(ROOT / f) != h]
    if env.source_hash() != pre["environment"]["source_sha256"]:
        problems.append("environment source changed")
    splits = make_splits(cfg, env)
    problems += [f"split {k} changed" for k, v in splits.items() if split_hash(v) != pre["splits"][k]["sha256"]]
    if problems:
        raise SystemExit("pre-registration violated: " + "; ".join(problems))
    return {"env": env, "splits": splits}


def run(cfg: HConfig, provider=None, check: bool = True) -> dict[str, Any]:
    if check:
        v = verify(cfg)
        env, splits = v["env"], v["splits"]
    else:   # tests only
        env = get_env(cfg.env)
        splits = make_splits(cfg, env)
    out = exp_dir(cfg.name) / f"rep{cfg.replicate}"
    out.mkdir(parents=True, exist_ok=True)
    if (out / "results.json").exists():
        raise SystemExit(f"{out}/results.json exists - a replicate is never re-run or overwritten")
    R = HRunner(cfg, env, splits, out, provider)
    t0 = time.time()
    R.log("=== arm A ===")
    a_id = R.arm_A()
    R.log("=== arm P (program evolution) ===")
    R.arm_C()
    R.log(f"arm P done: calls={R.synth_C.calls} cost=${R.synth_C.cost:.2f}")
    R.log("=== arm H (hypothesis evolution) ===")
    R.arm_H()
    R.log(f"arm H done: calls={R.synth_H.calls} cost=${R.synth_H.cost:.2f}")

    def sel(k):
        return R.candidates[k]["selection"]["mean_score"]

    def best(arm, pool=None):
        ids = [k for k, r in R.candidates.items() if r["arm"] == arm and r["usable"] and (pool is None or k in pool)]
        return max(ids, key=sel) if ids else None

    champs = {"A": a_id if R.candidates[a_id]["usable"] else None, "P_initial": best("C", set(R.c_initial_ids)),
              "P_final": best("C"), "H_initial": best("H", set(R.h_initial_ids)), "H_final": best("H")}
    attr = R.attribution() if getattr(R, "hyps", None) else {}
    for hid, a in attr.items():
        if a.get("best_implementation"):
            champs[f"hyp:{hid}"] = a["best_implementation"]
    R.log(f"champions (chosen on SELECTION, before audit): {champs}")
    audit: dict[str, EnvEvalResult] = {}
    for role, cid in champs.items():
        if cid:
            audit[role] = R.evaluator.evaluate(R.cands_obj[cid].ir, splits["audit"], "audit")

    def stat(res):
        s = res.summary()
        s["mean_ci95"] = list(bootstrap_mean_ci(res.scores, 0.95, seed=1))
        s.pop("candidate_id", None)
        return s

    def cmp(x, y):
        if x not in audit or y not in audit:
            return None
        d, lo, hi = paired_diff_ci(audit[x].scores, audit[y].scores, CONF, seed=7)
        return {"comparison": f"{x} - {y}", "mean_diff": d, "ci_bonferroni": [lo, hi], "holds": lo > 0}

    tests = {"G1": cmp("H_final", "P_final"), "G2": cmp("H_final", "A"), "G3_paradigm_transition": cmp("H_final", "H_initial"),
             "descriptive_P_final_minus_P_initial": cmp("P_final", "P_initial")}
    transition = None
    if champs["H_final"] and champs["H_initial"]:
        f, i = R.candidates[champs["H_final"]], R.candidates[champs["H_initial"]]
        sim = ast_similarity(f["module"], i["module"])
        transition = {"initial_hypothesis": i["hypothesis"], "final_hypothesis": f["hypothesis"],
                      "final_hypothesis_origin": R.hyps[f["hypothesis"]].origin,
                      "different_hypothesis": i["hypothesis"] != f["hypothesis"], "ast_similarity": round(sim, 4),
                      "code_differs": sim < NOVEL_AST_THRESHOLD}
        if tests["G3_paradigm_transition"]:
            g = tests["G3_paradigm_transition"]
            g["score_holds"] = g["holds"]
            g["holds"] = g["holds"] and transition["different_hypothesis"] and transition["code_differs"]
    arm_recs = {a: sorted((r for r in R.candidates.values() if r["arm"] == a), key=lambda r: r["created_at"]) for a in ("C", "H")}
    p_seq = [max((sel(i) for i in R.c_initial_ids), default=0.0)] + [s["best_selection_after"] for s in getattr(R, "c_trace", [])]
    results = {
        "config": cfg.__dict__, "elapsed_s": round(time.time() - t0, 1), "champions": champs,
        "audit": {k: stat(v) for k, v in audit.items()}, "tests": tests, "transition": transition,
        "diversity": {"P": diversity(arm_recs["C"]), "H": diversity(arm_recs["H"])},
        "stagnation_escapes": {"P": escapes(p_seq), "H": escapes(getattr(R, "h_best_seq", [0.0]))},
        "best_selection_sequence": {"P": p_seq, "H": getattr(R, "h_best_seq", [])},
        "hypotheses": {hid: h.record() for hid, h in getattr(R, "hyps", {}).items()},
        "attribution": attr, "llm_conjectures": getattr(R, "conjectures", []),
        "h_trace": getattr(R, "h_trace", []), "p_trace": getattr(R, "c_trace", []),
        "h_first_leader": getattr(R, "h_first_leader", None),
        "synthesis": {"A": {"calls": R.synth_A.calls, "cost_usd": R.synth_A.cost},
                      "P": {"calls": R.synth_C.calls, "cost_usd": R.synth_C.cost},
                      "H": {"calls": R.synth_H.calls, "cost_usd": R.synth_H.cost}},
        "provider": R.provider.name + (" (SIMULATED)" if R.provider.simulated else " (real model)"),
        "sandbox": R.sandbox.describe(),
    }
    (out / "lineage.json").write_text(json.dumps(list(R.candidates.values()), indent=1, default=str))
    (out / "results.json").write_text(json.dumps(results, indent=2, default=str))
    (out / "audit_per_instance.json").write_text(json.dumps(
        {k: [{"id": r.instance_id, "family": r.family, "score": r.score} for r in v.results] for k, v in audit.items()}, indent=1))
    R.log(f"results written to {out}")
    return results


# ------------------------------------------------------------------------------------------- report
def report(name: str) -> str:
    d = exp_dir(name)
    pre = json.loads((d / "prereg.json").read_text())
    dj = json.loads((d / "diag.json").read_text())
    reps = [(r, json.loads((d / f"rep{r}" / "results.json").read_text())) for r in range(pre["replicates"])
            if (d / f"rep{r}" / "results.json").exists()]

    def verdict(fl):
        return "NOT TESTED" if not fl else "SUPPORTED" if all(fl) else "MIXED" if any(fl) else "NOT SUPPORTED"
    c = dj["criteria"]
    L = [f"# Experiment `{name}` - hypothesis-driven synthesis vs program evolution", "",
         f"Environment `{pre['environment']['name']}` v{pre['environment']['version']} (sha256 `{pre['environment']['source_sha256'][:12]}`), "
         f"pre-registered {pre['created']}; audit split sha256 `{pre['splits']['audit']['sha256'][:12]}` "
         f"({pre['splits']['audit']['n']} instances). Replicates completed: {len(reps)}/{pre['replicates']}. "
         f"Provider: {reps[0][1]['provider'] if reps else 'n/a'}.", "",
         "## Diagnostic (before the search)", "",
         f"- hypotheses with a working implementation: {c['a_hypotheses_with_working_implementation']}",
         f"- AST similarity within a hypothesis {c['b_ast_within']:.3f} vs between hypotheses {c['b_ast_between']:.3f} "
         f"(permutation p = {c['b_perm_p']:.4f}); control (plain independent generations) {c['c_control_mean_pairwise_ast']:.3f}",
         f"- instances on which programs differ: {c['d_discrimination']:.2f}",
         f"- mean calibration score per group: {json.dumps(dj['group_scores'])}", f"- verdict: {'PASS' if dj['passed'] else 'FAIL'}", "",
         "## Audit (sealed set)", ""]
    for r, res in reps:
        L += [f"### Replicate {r}", "", "| role | program | hypothesis | mean score | 95% CI |", "|---|---|---|---|---|"]
        lin = {x["id"]: x for x in json.loads((d / f"rep{r}" / "lineage.json").read_text())}
        for role, s in res["audit"].items():
            cid = res["champions"][role]
            L.append(f"| {role} | `{cid}` | {lin[cid].get('hypothesis', '-')} | {s['mean_score']:.4f} | [{s['mean_ci95'][0]:.4f}, {s['mean_ci95'][1]:.4f}] |")
        L.append("")
    L += ["## Pre-registered tests", "", "| test | " + " | ".join(f"rep{r}" for r, _ in reps) + " | verdict |", "|---|" + "---|" * (len(reps) + 1)]
    for t in ("G1", "G2", "G3_paradigm_transition", "descriptive_P_final_minus_P_initial"):
        cells, fl = [], []
        for _, res in reps:
            x = res["tests"].get(t)
            if x:
                cells.append(f"{x['mean_diff']:+.4f} [{x['ci_bonferroni'][0]:+.4f}, {x['ci_bonferroni'][1]:+.4f}] {'✔' if x['holds'] else '✘'}")
                fl.append(bool(x["holds"]))
            else:
                cells.append("n/a")
        L.append(f"| {t} | " + " | ".join(cells) + f" | **{verdict(fl)}** |")
    L.append("")
    for r, res in reps:
        tr = res["transition"] or {}
        L += [f"## Replicate {r}: what happened", "",
              f"- calls / cost: A {res['synthesis']['A']['calls']} / ${res['synthesis']['A']['cost_usd']:.2f}, "
              f"P {res['synthesis']['P']['calls']} / ${res['synthesis']['P']['cost_usd']:.2f}, "
              f"H {res['synthesis']['H']['calls']} / ${res['synthesis']['H']['cost_usd']:.2f}",
              f"- diversity P: {json.dumps(res['diversity']['P'])}", f"- diversity H: {json.dumps(res['diversity']['H'])}",
              f"- escapes from stagnation: P {res['stagnation_escapes']['P']}, H {res['stagnation_escapes']['H']}",
              f"- transition: {json.dumps(tr)}", "", "| hypothesis | origin | mechanism (model's words) | impl. working/attempted | best SELECTION | measured label | own prediction |",
              "|---|---|---|---|---|---|---|"]
        for hid, h in res["hypotheses"].items():
            a = res["attribution"].get(hid, {})
            L.append(f"| {hid} {h['name']} | {h['origin']}{' of ' + '+'.join(h['parents']) if h['origin'] == 'recombine' else ''} | {h['mechanism'][:220]} | "
                     f"{a.get('implementations_working')}/{a.get('implementations_attempted')} | {a.get('best_selection', '-')} | "
                     f"{a.get('label')} | {json.dumps(a.get('own_prediction'))} |")
        L += ["", "| step | move | hypothesis | usable | Δ SELECTION vs leader [95% CI] | AST sim. to leader | best after |", "|---|---|---|---|---|---|---|"]
        for i, s in enumerate(res["h_trace"], 1):
            dl = f"{s['delta_vs_leader']:+.4f} [{s['ci95'][0]:+.4f}, {s['ci95'][1]:+.4f}]{' ✔' if s['significant'] else ''}" if "delta_vs_leader" in s else "-"
            L.append(f"| {i} | {s['move']} | {s['hypothesis']} | {s['usable']} | {dl} | {s.get('ast_similarity_to_leader', '-')} | {s['best_selection_after']:.4f} |")
        L += ["", f"- model's cause conjectures (not evidence): {json.dumps([{k: x[k] for k in ('hypothesis', 'llm_cause', 'status')} for x in res['llm_conjectures']])}", ""]
    txt = "\n".join(L) + "\n"
    (d / "REPORT.md").write_text(txt)
    return txt


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="skillforge.hexperiment")
    ap.add_argument("cmd", choices=["diag", "prepare", "run", "report"])
    ap.add_argument("--name", required=True)
    ap.add_argument("--env", default="docrepair")
    ap.add_argument("--dev", type=int, default=30)
    ap.add_argument("--selection", type=int, default=60)
    ap.add_argument("--audit", type=int, default=100)
    ap.add_argument("--budget", type=int, default=12)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--replicate", type=int, default=0)
    ap.add_argument("--replicates", type=int, default=2)
    a = ap.parse_args(argv)
    if a.cmd == "report":
        print(report(a.name))
        return 0
    cfg = HConfig(name=a.name, env=a.env, budget=a.budget, seed=a.seed, replicate=a.replicate, dev=a.dev,
                  selection=a.selection, audit=a.audit, timer="cpu", chunks=2, item_timeout_s=2.0)
    if a.cmd == "diag":
        g = diag(cfg)
        print(json.dumps({k: g[k] for k in ("passed", "criteria", "behaviour", "group_scores", "calls", "synthesis_cost_usd")}, indent=1))
        print(json.dumps([{"id": h["id"], "name": h["name"], "mechanism": h["mechanism"]} for h in g["hypotheses"]], indent=1))
        return 0 if g["passed"] else 3
    if a.cmd == "prepare":
        pre = prepare(cfg, a.replicates)
        print(json.dumps({k: pre[k] for k in ("environment", "splits")}, indent=1))
        return 0
    res = run(cfg)
    print(json.dumps({"champions": res["champions"], "tests": res["tests"], "transition": res["transition"],
                      "audit": {k: round(v["mean_score"], 4) for k, v in res["audit"].items()}}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
