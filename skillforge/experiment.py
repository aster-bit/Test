"""Pre-registered synthesis experiment (SkillForge v2).

    python -m skillforge.experiment prepare --name sched-v1      # freeze protocol -> prereg.json (commit it)
    python -m skillforge.experiment run --name sched-v1          # verifies the freeze, runs, writes results

Arms (equal synthesis-call budget B for B and C):
  A  one-shot: the first program the generator writes (no repair, no selection)          — 1 call
  B  independent: B independent generations (+ repairs inside the budget), best on SELECTION
  C  evolutionary: 3 independent generations, then structural mutations chosen by a bandit,
     with stagnation-triggered space expansion; best on SELECTION                          — B calls
Reference baselines (no synthesis): H hand-written skill, D direct Claude call per instance.

Data protocol: DEV (generator may see inputs + checker diagnoses), SELECTION (hidden; aggregate scores only,
used to pick parents/champions), AUDIT (sealed; sha256 frozen before the search; every pre-registered
candidate evaluated exactly once at the end). The environment, prompts, splits, budgets, hypotheses and
statistics are hashed into prereg.json; `run` refuses to start if anything changed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .env_eval import EnvEvalResult, EnvEvaluator, bootstrap_mean_ci, paired_diff_ci
from .envs import get_env
from .envs.base import Instance, build_split, split_hash
from .executor import Executor
from .llm import CostMeter, LLMRequest, make_provider
from .novelty import NOVEL_AST_THRESHOLD, ast_similarity, classify_change, graph_signature
from .open_synth import STRUCTURAL_OPERATORS, Candidate, OpenSynthesizer, build_ir, parse_reply
from .sandbox import ExecutionSandbox, SandboxLimits
from .stats import mean

ROOT = Path(__file__).resolve().parents[1]
FROZEN_FILES = ["skillforge/envs/base.py", "skillforge/llm.py", "skillforge/ir.py", "skillforge/open_synth.py", "skillforge/experiment.py",
                "skillforge/env_eval.py", "skillforge/novelty.py", "skillforge/executor.py", "skillforge/sandbox.py",
                "skillforge/sandbox_runner.py", "experiments/baselines/scheduling_manual.md"]


@dataclass
class ExpConfig:
    name: str
    env: str = "scheduling"
    seed: int = 2026
    dev: int = 40
    selection: int = 80
    audit: int = 120
    budget: int = 12            # synthesis calls per arm (B and C)
    c_initial: int = 3
    stagnation_patience: int = 2
    parallel: int = 4
    model: str = "claude-opus-5-5"
    effort: str = "medium"
    direct_model: str = "claude-opus-5-5"
    item_timeout_s: float = 3.0   # hard sandbox limit; the spec tells the generator 1.5 s (slack for load)
    recombine_rate: float = 0.25
    replicate: int = 0            # independent replicate index (same frozen protocol and data)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def exp_dir(name: str) -> Path:
    d = ROOT / "experiments" / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def make_splits(cfg: ExpConfig, env) -> dict[str, list[Instance]]:
    return {"dev": build_split(env, "dev", cfg.dev, cfg.seed),
            "selection": build_split(env, "selection", cfg.selection, cfg.seed),
            "audit": build_split(env, "audit", cfg.audit, cfg.seed, include_audit_only=True)}


HYPOTHESES = {
    "H1_primary": "C_final beats C_initial (the best program the generator wrote from scratch inside arm C) on the "
                  "AUDIT mean score: paired bootstrap Bonferroni-corrected CI of the difference has lower bound > 0, "
                  "AND C_final differs structurally from C_initial (graph/function-set change or AST similarity "
                  f"< {NOVEL_AST_THRESHOLD}).",
    "H2": "C_final beats B_best (independent sampling, equal budget) on AUDIT (same test).",
    "H3": "C_final beats A (one-shot) on AUDIT (same test).",
    "H4": "C_final beats H (hand-written baseline) on AUDIT (same test).",
    "H5": "C_final beats D (direct Claude call per instance) on AUDIT (same test).",
    "novelty_metric": f"new algorithm = executable candidate whose AST similarity to every earlier candidate of the "
                      f"same arm is < {NOVEL_AST_THRESHOLD}; structural mutation per novelty.classify_change; "
                      "significant improvement = child beats parent on SELECTION with paired bootstrap 95% CI > 0.",
    "statistics": "per replicate: mean score with bootstrap 95% CI; 5 paired comparisons, Bonferroni => 99% paired "
                  "bootstrap CIs over audit instances. Replicates are independent repetitions of the whole search "
                  "(generator stochasticity); a hypothesis is called SUPPORTED only if it holds in every replicate, "
                  "MIXED if in some, NOT SUPPORTED if in none.",
    "leakage_controls": "generator sees: public spec, format examples, parent programs, and for DEV instances the "
                        "input, its own output, score and the checker's violation text (no reference, no bound). "
                        "SELECTION: aggregate scores only, used for parent/champion choice. AUDIT: never used before "
                        "the final single evaluation.",
    "success_criterion": "Stage succeeds iff H1_primary holds. Otherwise the result is reported as negative.",
}


def prepare(cfg: ExpConfig, replicates: int = 2) -> dict[str, Any]:
    env = get_env(cfg.env)
    splits = make_splits(cfg, env)
    pre = {
        "config": cfg.__dict__, "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "environment": {"name": env.name, "version": env.version, "source_sha256": env.source_hash()},
        "frozen_files": {f: sha(ROOT / f) for f in FROZEN_FILES},
        "splits": {k: {"n": len(v), "sha256": split_hash(v),
                       "families": sorted({i.family for i in v})} for k, v in splits.items()},
        "hypotheses": HYPOTHESES, "replicates": replicates,
    }
    p = exp_dir(cfg.name) / "prereg.json"
    if p.exists():
        raise SystemExit(f"{p} already exists — a pre-registration is never overwritten")
    p.write_text(json.dumps(pre, indent=2))
    return pre


def verify(cfg: ExpConfig) -> dict[str, Any]:
    p = exp_dir(cfg.name) / "prereg.json"
    pre = json.loads(p.read_text())
    if {k: v for k, v in pre["config"].items() if k != "replicate"} != \
            {k: v for k, v in cfg.__dict__.items() if k != "replicate"}:
        raise SystemExit("config differs from pre-registration")
    if cfg.replicate >= pre["replicates"]:
        raise SystemExit(f"only {pre['replicates']} replicates were pre-registered")
    env = get_env(cfg.env)
    problems = []
    if env.source_hash() != pre["environment"]["source_sha256"]:
        problems.append("environment source changed")
    for f, h in pre["frozen_files"].items():
        if sha(ROOT / f) != h:
            problems.append(f"frozen file changed: {f}")
    splits = make_splits(cfg, env)
    for k, v in splits.items():
        if split_hash(v) != pre["splits"][k]["sha256"]:
            problems.append(f"split {k} changed")
    if problems:
        raise SystemExit("pre-registration violated: " + "; ".join(problems))
    return {"prereg": pre, "env": env, "splits": splits}


# ---------------------------------------------------------------------------------------------- run
class Runner:
    def __init__(self, cfg: ExpConfig, env, splits, out: Path, provider=None):
        self.cfg, self.env, self.splits, self.out = cfg, env, splits, out
        self.meter = CostMeter()
        self.provider = provider or make_provider("claude_code", meter=self.meter, concurrency=cfg.parallel)
        if provider is not None:
            self.meter = provider.meter
        sandbox = ExecutionSandbox("auto", SandboxLimits(item_timeout_s=cfg.item_timeout_s, wall_timeout_s=60))
        self.sandbox = sandbox
        self.evaluator = EnvEvaluator(env, Executor(sandbox, self.provider, {}), chunks=4)
        self.lock = threading.Lock()
        self.log_f = (out / "events.log").open("a")
        self.candidates: dict[str, dict[str, Any]] = {}
        self.cands_obj: dict[str, Candidate] = {}

    def log(self, msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} {msg}"
        with self.lock:
            print(line, flush=True)
            self.log_f.write(line + "\n")
            self.log_f.flush()

    # ---------------------------------------------------------------- evaluation helpers
    def usable_problem(self, c: Candidate) -> str | None:
        if c.parse_error:
            return f"format error: {c.parse_error}"
        if c.static_problems:
            return "static policy violations: " + "; ".join(c.static_problems[:8])
        dev = self.evaluator.evaluate(c.ir, self.splits["dev"], "dev")
        errs = [r for r in dev.results if r.error]
        if len(errs) >= 0.5 * len(dev.results):
            return "runtime errors on most instances, e.g.: " + " | ".join(e.error[:300] for e in errs[:3])
        return None

    def dev_feedback(self, c: Candidate) -> str:
        dev = self.evaluator.evaluate(c.ir, self.splits["dev"], "dev")
        s = dev.summary()
        worst = sorted(zip(self.splits["dev"], dev.results), key=lambda t: t[1].score)[:6]
        lines = [f"mean score {s['mean_score']:.3f}; valid answers {s['valid_rate']:.2%}; runtime errors {s['errors']}; "
                 f"violations {s['violations']}; mean score per instance family {s['per_family']}",
                 "Weakest development instances:"]
        for inst, r in worst:
            lines.append(f"- family={inst.family} score={r.score:.3f} checker: {r.message}\n  input: "
                         f"{json.dumps(inst.input)[:1500]}\n  your output: {json.dumps(r.output)[:400] if r.output is not None else r.error}")
        return "\n".join(lines)

    def evaluate_candidate(self, c: Candidate) -> dict[str, Any] | None:
        if c.ir is None:
            return None
        dev = self.evaluator.evaluate(c.ir, self.splits["dev"], "dev")
        sel = self.evaluator.evaluate(c.ir, self.splits["selection"], "selection")
        return {"dev": dev.summary(), "selection": sel.summary(), "selection_scores": sel.scores}

    def register(self, c: Candidate, ev: dict[str, Any] | None, problem: str | None) -> None:
        rec = c.record()
        rec["usable"] = ev is not None and problem is None
        rec["problem"] = problem
        rec["graph_signature"] = graph_signature(c.pipeline) if c.pipeline else ""
        if ev:
            rec.update({"dev": ev["dev"], "selection": ev["selection"], "selection_scores": ev["selection_scores"]})
        with self.lock:
            self.candidates[c.id] = rec
            self.cands_obj[c.id] = c
        self.log(f"[{c.arm}] {c.id} {c.origin} usable={rec['usable']} "
                 f"sel={ev['selection']['mean_score']:.3f} dev={ev['dev']['mean_score']:.3f}" if ev and rec['usable']
                 else f"[{c.arm}] {c.id} {c.origin} UNUSABLE: {(problem or '')[:160]}")

    def make_usable(self, synth: OpenSynthesizer, c: Candidate, arm: str, budget_left) -> Candidate:
        problem = self.usable_problem(c)
        if problem and budget_left() > 0:
            self.log(f"[{arm}] repairing {c.id}: {problem[:140]}")
            r = synth.repair(c, problem, arm)
            r.parents = c.parents
            r.change = (r.change + f" [repair of {c.id}]").strip()
            self.register(c, None, problem)  # the broken original stays in the lineage
            return r
        return c

    def finish(self, c: Candidate) -> None:
        problem = self.usable_problem(c)
        self.register(c, None if problem else self.evaluate_candidate(c), problem)

    # ---------------------------------------------------------------- arms
    def _parallel_generate(self, synth: OpenSynthesizer, arm: str, start: int, n: int) -> list[Candidate]:
        with ThreadPoolExecutor(max_workers=n) as pool:
            return list(pool.map(lambda i: synth.generate(arm, i, attempt_note=f"independent attempt #{i + 1}"),
                                 range(start, start + n)))

    def arm_B(self) -> list[str]:
        synth = OpenSynthesizer(self.env, self.provider, self.cfg.model, self.cfg.effort)
        self.synth_B = synth
        ids: list[str] = []
        idx = 0

        def budget_left():
            return self.cfg.budget - synth.calls

        while synth.calls < self.cfg.budget:
            n = min(self.cfg.parallel, self.cfg.budget - synth.calls)
            gens = self._parallel_generate(synth, "B", idx, n)
            for c in gens:  # repairs are sequential so the budget is respected exactly
                if c.generation_index == 0:  # arm A := the very first program, as written (no repair)
                    a = Candidate(**{**c.__dict__, "id": c.id.replace("B-", "A-", 1), "arm": "A"})
                    if a.ir is not None:
                        a.ir = build_ir(a.pipeline, a.module, a.id)
                    self.finish(a)
                c = self.make_usable(synth, c, "B", budget_left)
                self.finish(c)
                ids.append(c.id)
            idx += n
        return ids

    def arm_C(self) -> list[str]:
        """Evolutionary synthesis: from-scratch initial programs, then self-diagnosed structural
        interventions and recombinations; stagnation triggers space expansion (a new paradigm)."""
        synth = OpenSynthesizer(self.env, self.provider, self.cfg.model, self.cfg.effort)
        self.synth_C = synth
        rng = random.Random(self.cfg.seed * 1000 + self.cfg.replicate)
        archive: list[str] = []
        self.c_trace: list[dict[str, Any]] = []

        def budget_left():
            return self.cfg.budget - synth.calls

        def sel(cid):
            return self.candidates[cid]["selection"]["mean_score"]

        def pick() -> str:  # binary tournament on SELECTION aggregate score
            a, b = rng.sample(archive, 2) if len(archive) > 1 else (archive[0], archive[0])
            return a if sel(a) >= sel(b) else b

        init_ids = []
        for c in self._parallel_generate(synth, "C", 0, self.cfg.c_initial):
            c = self.make_usable(synth, c, "C", budget_left)
            self.finish(c)
            init_ids.append(c.id)
        self.c_initial_ids = [i for i in init_ids if self.candidates[i]["usable"]]
        archive.extend(self.c_initial_ids)
        best = max((sel(i) for i in archive), default=0.0)
        stagnant = 0
        idx = self.cfg.c_initial
        while synth.calls < self.cfg.budget and archive:
            parents: list[str] = []
            if stagnant >= self.cfg.stagnation_patience:
                summ = []
                for cid in archive:
                    r = self.candidates[cid]
                    weak = sorted(r["dev"]["per_family"].items(), key=lambda kv: kv[1])[:3]
                    summ.append({"id": cid, "strategy": r["strategy"][:500],
                                 "dev_mean_score": round(r["dev"]["mean_score"], 4),
                                 "dev_valid_rate": round(r["dev"]["valid_rate"], 3),
                                 "dev_violations": r["dev"]["violations"], "weakest_families_on_dev": weak})
                self.log(f"[C] stagnation ({stagnant}) -> space expansion")
                child = synth.expand(summ, "C", idx)
                move = "expand"
                stagnant = 0
            elif len(archive) >= 2 and rng.random() < self.cfg.recombine_rate:
                pa = pick()
                pb = max((x for x in archive if x != pa), key=sel)
                parents = [pa, pb]
                self.log(f"[C] recombine {pa} + {pb}")
                child = synth.recombine(self.cands_obj[pa], self.cands_obj[pb], self.dev_feedback(self.cands_obj[pa]),
                                        self.dev_feedback(self.cands_obj[pb]), "C", idx)
                move = "recombine"
            else:
                pa = pick()
                parents = [pa]
                self.log(f"[C] self-diagnosed intervention on {pa}")
                child = synth.intervene(self.cands_obj[pa], self.dev_feedback(self.cands_obj[pa]), "C", idx)
                move = child.origin
            idx += 1
            child = self.make_usable(synth, child, "C", budget_left)
            self.finish(child)
            rec = self.candidates[child.id]
            step = {"child": child.id, "move": move, "parents": parents, "usable": rec["usable"],
                    "operation": child.origin}
            if rec["usable"]:
                archive.append(child.id)
                s_child = sel(child.id)
                if parents:
                    ref = max(parents, key=sel)  # compare with the better parent
                    p = self.candidates[ref]
                    d, lo, hi = paired_diff_ci(rec["selection_scores"], p["selection_scores"], 0.95, seed=idx)
                    step.update({"compared_to": ref, "delta_vs_parent": d, "ci95": [lo, hi], "significant": lo > 0,
                                 **classify_change(p["module"], p["pipeline"], child.module, child.pipeline)})
                else:
                    prev_best = max((x for x in archive if x != child.id), key=sel)
                    p = self.candidates[prev_best]
                    d, lo, hi = paired_diff_ci(rec["selection_scores"], p["selection_scores"], 0.95, seed=idx)
                    step.update({"compared_to": prev_best, "delta_vs_parent": d, "ci95": [lo, hi],
                                 "significant": lo > 0,
                                 **classify_change(p["module"], p["pipeline"], child.module, child.pipeline)})
                if s_child > best + 1e-9:
                    best, stagnant = s_child, 0
                else:
                    stagnant += 1
            else:
                stagnant += 1
            step["best_selection_after"] = best
            self.c_trace.append(step)
            self.log(f"[C] step {len(self.c_trace)}: {move} -> usable={rec['usable']} "
                     f"delta={step.get('delta_vs_parent')} structural={step.get('structural')} best={best:.4f}")
        return archive

    # ---------------------------------------------------------------- baselines
    def manual_baseline(self) -> Candidate:
        text = (ROOT / "experiments/baselines/scheduling_manual.md").read_text()
        strat, _, pipe, mod = parse_reply(text)
        c = Candidate("H-manual", "H", "handwritten", [], strat, "", pipe, mod, time.time(), 0, 0, 0.0)
        c.ir = build_ir(pipe, mod, c.id)
        return c

    def direct_claude(self, instances: list[Instance]) -> EnvEvalResult:
        """D: ask Claude to solve each instance directly (no synthesised program)."""
        res = EnvEvalResult("D-direct", "audit")
        spec = self.env.public_spec()

        def one(inst: Instance):
            t0 = time.time()
            r = self.provider.complete(LLMRequest(
                system="You solve task instances exactly as specified. Reply with the JSON output object only.",
                messages=[{"role": "user", "content": f"{spec}\n\nINSTANCE INPUT:\n{json.dumps(inst.input)}\n\n"
                                                      "Return only the JSON output object."}],
                model=self.cfg.direct_model, max_tokens=8000, effort="low", purpose="direct"))
            m = re.search(r"\{.*\}", r.text, re.S)
            try:
                out = json.loads(m.group(0)) if m else None
            except ValueError:
                out = None
            c = self.env.check(inst, out)
            from .env_eval import InstanceResult
            return InstanceResult(inst.id, inst.family, c.score, c.valid, c.violations, c.message,
                                  None if out is not None else "unparseable reply", (time.time() - t0) * 1000,
                                  r.cost_usd, 1, out)

        with ThreadPoolExecutor(max_workers=self.cfg.parallel) as pool:
            res.results = list(pool.map(one, instances))
        return res


def run(cfg: ExpConfig, provider=None) -> dict[str, Any]:
    v = verify(cfg)
    env, splits = v["env"], v["splits"]
    out = exp_dir(cfg.name) / f"rep{cfg.replicate}"
    out.mkdir(parents=True, exist_ok=True)
    if (out / "results.json").exists():
        raise SystemExit(f"{out}/results.json exists — a replicate is never re-run or overwritten")
    R = Runner(cfg, env, splits, out, provider)
    t0 = time.time()
    R.log(f"pre-registration verified; sandbox={R.sandbox.describe()['strength']}")
    R.log("=== arm B (+A = first program of B) ===")
    tB = time.time()
    b_ids = R.arm_B()
    R.log(f"arm B done in {time.time() - tB:.0f}s, calls={R.synth_B.calls}, cost=${R.synth_B.cost:.3f}")
    R.log("=== arm C ===")
    tC = time.time()
    c_ids = R.arm_C()
    R.log(f"arm C done in {time.time() - tC:.0f}s, calls={R.synth_C.calls}, cost=${R.synth_C.cost:.3f}")

    def best(arm, pool=None):
        ids = [k for k, r in R.candidates.items() if r["arm"] == arm and r["usable"] and (pool is None or k in pool)]
        return max(ids, key=lambda k: (R.candidates[k]["selection"]["mean_score"],
                                       -sum(R.candidates[k]["selection"]["violations"].values()))) if ids else None

    champions = {"A": next((k for k, r in R.candidates.items() if r["arm"] == "A"), None),
                 "B_best": best("B"), "C_initial": best("C", set(R.c_initial_ids)), "C_final": best("C")}
    R.log(f"champions (chosen on SELECTION, before audit): {champions}")

    # ---- the sealed audit: every pre-registered candidate once
    audit = splits["audit"]
    audit_res: dict[str, EnvEvalResult] = {}
    for role, cid in champions.items():
        if cid and R.candidates[cid]["usable"]:
            audit_res[role] = R.evaluator.evaluate(R.cands_obj[cid].ir, audit, "audit")
    H = R.manual_baseline()
    audit_res["H_manual"] = R.evaluator.evaluate(H.ir, audit, "audit")
    R.log("direct Claude baseline on audit ...")
    audit_res["D_direct"] = R.direct_claude(audit)
    R.log("audit done")

    def stat(res: EnvEvalResult) -> dict[str, Any]:
        s = res.summary()
        s["mean_ci95"] = list(bootstrap_mean_ci(res.scores, 0.95, seed=1))
        s.pop("candidate_id", None)
        return s

    audit_summary = {k: stat(v) for k, v in audit_res.items()}
    comps = {}
    if "C_final" in audit_res:
        for other, h in (("C_initial", "H1_primary"), ("B_best", "H2"), ("A", "H3"), ("H_manual", "H4"),
                         ("D_direct", "H5")):
            if other in audit_res:
                d, lo, hi = paired_diff_ci(audit_res["C_final"].scores, audit_res[other].scores, 0.99, seed=7)
                comps[h] = {"comparison": f"C_final - {other}", "mean_diff": d, "ci99_bonferroni": [lo, hi],
                            "holds": lo > 0}
    structural_c = None
    if champions["C_final"] and champions["C_initial"]:
        a, b = R.cands_obj[champions["C_initial"]], R.cands_obj[champions["C_final"]]
        structural_c = classify_change(a.module, a.pipeline, b.module, b.pipeline)
        sim = structural_c["ast_similarity_to_parent"]
        structural_c["novel_vs_initial"] = structural_c["structural"] or sim < NOVEL_AST_THRESHOLD
        if "H1_primary" in comps:
            comps["H1_primary"]["holds"] = comps["H1_primary"]["holds"] and (
                champions["C_final"] != champions["C_initial"]) and structural_c["novel_vs_initial"]

    # ---- novelty accounting per arm (creation order)
    novelty = {}
    for arm in ("B", "C"):
        recs = sorted((r for r in R.candidates.values() if r["arm"] == arm), key=lambda r: r["created_at"])
        seen: list[str] = []
        novel = []
        for r in recs:
            if r["usable"]:
                ms = max((ast_similarity(r["module"], s) for s in seen), default=0.0)
                r["max_ast_similarity_to_earlier"] = round(ms, 4)
                if ms < NOVEL_AST_THRESHOLD:
                    novel.append(r["id"])
                seen.append(r["module"])
        novelty[arm] = {"candidates": len(recs), "usable": sum(r["usable"] for r in recs), "new_algorithms": novel}
    b_mods = [r["module"] for r in R.candidates.values() if r["arm"] == "B" and r["usable"]]
    if champions["C_final"]:
        novelty["C_final_max_ast_similarity_to_any_B"] = round(max(
            (ast_similarity(R.cands_obj[champions["C_final"]].module, m) for m in b_mods), default=0.0), 4)
    steps = getattr(R, "c_trace", [])
    novelty["C_operations_named_by_generator"] = sorted({s["operation"] for s in steps if s["move"].startswith("intervene")})
    novelty["C_mutations"] = {
        "total": len([s for s in steps if s["move"] != "expand"]),
        "usable": len([s for s in steps if s["usable"] and s["move"] != "expand"]),
        "structural": len([s for s in steps if s.get("structural")]),
        "significant_improvements": len([s for s in steps if s.get("significant")]),
        "significant_and_structural": len([s for s in steps if s.get("significant") and s.get("structural")]),
        "expansions": len([s for s in steps if s["move"] == "expand"]),
        "expansions_usable": len([s for s in steps if s["move"] == "expand" and s["usable"]]),
    }

    # ---- cost / break-even
    synth_cost = {"B": R.synth_B.cost, "C": R.synth_C.cost}
    d_cost = audit_summary["D_direct"]["cost_per_item_usd"]
    c_run = audit_summary.get("C_final", {}).get("cost_per_item_usd", 0.0)
    breakeven = (synth_cost["C"] / (d_cost - c_run)) if d_cost > c_run else None

    results = {
        "config": cfg.__dict__, "elapsed_s": round(time.time() - t0, 1), "champions": champions,
        "audit": audit_summary, "hypotheses": comps, "C_initial_vs_final_structure": structural_c,
        "novelty": novelty,
        "synthesis": {"B": {"calls": R.synth_B.calls, "cost_usd": R.synth_B.cost, "log": R.synth_B.log},
                      "C": {"calls": R.synth_C.calls, "cost_usd": R.synth_C.cost, "log": R.synth_C.log}},
        "break_even_runs_vs_direct": breakeven, "direct_cost_per_item": d_cost, "skill_cost_per_item": c_run,
        "meter": R.meter.snapshot(), "c_trace": steps,
        "provider": R.provider.name + (" (SIMULATED)" if R.provider.simulated else " (real model)"),
        "sandbox": R.sandbox.describe(),
    }
    (out / "lineage.json").write_text(json.dumps(list(R.candidates.values()), indent=1, default=str))
    (out / "results.json").write_text(json.dumps(results, indent=2, default=str))
    (out / "audit_per_instance.json").write_text(json.dumps(
        {k: [{"id": r.instance_id, "family": r.family, "score": r.score, "valid": r.valid, "violations": r.violations}
             for r in v.results] for k, v in audit_res.items()}, indent=1))
    R.log(f"results written to {out}")
    return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="skillforge.experiment")
    ap.add_argument("cmd", choices=["prepare", "run"])
    ap.add_argument("--name", required=True)
    ap.add_argument("--budget", type=int, default=12)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--replicate", type=int, default=0)
    ap.add_argument("--replicates", type=int, default=2)
    a = ap.parse_args(argv)
    cfg = ExpConfig(name=a.name, budget=a.budget, seed=a.seed, replicate=a.replicate)
    if a.cmd == "prepare":
        pre = prepare(cfg, a.replicates)
        print(json.dumps({k: pre[k] for k in ("environment", "splits")}, indent=1))
    else:
        res = run(cfg)
        print(json.dumps({"champions": res["champions"], "hypotheses": res["hypotheses"],
                          "audit": {k: (round(v["mean_score"], 4), v["mean_ci95"]) for k, v in res["audit"].items()}},
                         indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
