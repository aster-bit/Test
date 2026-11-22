"""End-to-end orchestration of a SkillForge run (open-ended until certification).

Campaign:
    search (evolution + adversary) ──► gate on validation ──► certification on a sealed holdout
         ▲                                                         │ fail: retire this holdout
         └──────────── holdout cases join train, new holdout ◄─────┘
    success ──► export skill package + report

* Budget is unlimited by default; the run ends when a skill is certified against the
  spec's Target, or when the user cancels (best-so-far is exported, marked NOT certified).
* Each holdout set is opened exactly once (one-way lock in the store, sha256 commitment
  recorded at creation). Retired holdouts are disclosed in the report.
"""

from __future__ import annotations

import hashlib
import json
import random
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Any

from .adversary import AdversarialEngine
from .compiler import SkillCompiler
from .domain_compiler import DomainCompiler
from .domains.base import Scenario
from .evaluation import EvalResult, EvaluationEngine
from .evolution import EvolutionConfig, EvolutionEngine, Individual, dominates
from .executor import Executor
from .llm import CostMeter, MockProvider, make_provider
from .report import render_markdown
from .sandbox import ExecutionSandbox, validate_source
from .spec import SkillSpec
from .store import ExperimentStore
from .synthesis import CandidateSynthesizer
from .world import WorldConfig, WorldGenerator, derive_seed


@dataclass
class RunConfig:
    seed: int = 7
    provider: str = "auto"  # auto (Claude Code if installed) | claude_code | mock | anthropic
    sandbox_backend: str = "auto"
    world: WorldConfig = field(default_factory=WorldConfig)
    export_alternates: int = 2
    output_dir: str = "runs"
    db_path: str = "runs/skillforge.db"
    plugin: str | None = None
    adversary_probes: int = 24
    adversary_rounds: int = 2
    metamorphic_sample: int = 12
    concurrency: int = 4
    gate_candidates_per_generation: int = 3
    target_pass_lower_bound: float | None = None  # overrides spec.target if set
    max_generations: int | None = None            # None = unlimited (overrides spec)
    max_holdout_attempts: int | None = None       # None = unlimited
    population_size: int | None = None            # overrides spec


def _deps() -> dict[str, str]:
    out = {}
    for pkg in ("pydantic", "fastapi", "hypothesis"):
        try:
            out[pkg] = metadata.version(pkg)
        except metadata.PackageNotFoundError:
            out[pkg] = "not installed"
    return out


def _commit(scen: list[Scenario]) -> str:
    return hashlib.sha256(json.dumps([s.to_dict() for s in scen], sort_keys=True, default=str).encode()).hexdigest()


def meets_target(spec: SkillSpec, r: EvalResult) -> tuple[bool, list[str]]:
    """Certification criteria (spec.target) evaluated on one result; returns (ok, unmet reasons)."""
    t = spec.target
    unmet = []
    if t.zero_hard_violations and r.total_hard_violations:
        unmet.append(f"{r.total_hard_violations} hard violations {r.hard_violation_counts}")
    if t.require_constraints and r.constraint_violations:
        unmet.append(f"constraints {sorted(r.constraint_violations)}")
    lo = r.intervals["pass_rate"][0]
    if lo < t.min_pass_lower_bound:
        unmet.append(f"pass lower bound {lo:.3f} < {t.min_pass_lower_bound}")
    return not unmet, unmet


class SkillForge:
    def __init__(self, config: RunConfig | None = None, store: ExperimentStore | None = None):
        self.cfg = config or RunConfig()
        self.store = store or ExperimentStore(self.cfg.db_path)
        self._cancel: dict[str, threading.Event] = {}

    def cancel(self, run_id: str | None = None) -> None:
        for rid, ev in self._cancel.items():
            if run_id is None or rid == run_id:
                ev.set()

    # ------------------------------------------------------------------
    def run(self, request: str, run_id: str | None = None) -> dict[str, Any]:
        run_id = run_id or "run-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        cfg = self.cfg
        cancel = self._cancel.setdefault(run_id, threading.Event())
        meter = CostMeter()
        provider = make_provider(cfg.provider, seed=cfg.seed, meter=meter, concurrency=cfg.concurrency)
        sandbox = ExecutionSandbox(cfg.sandbox_backend)
        self.store.create_run(run_id, request, cfg.seed, provider.name,
                              json.loads(json.dumps(asdict(cfg), default=str)),
                              {"sandbox": sandbox.describe(), "dependencies": _deps()})
        log = lambda kind, msg, data=None: self.store.event(run_id, kind, msg, data)  # noqa: E731
        try:
            return self._run(run_id, request, provider, meter, sandbox, cancel, log)
        except Exception as e:
            self.store.update_run(run_id, status="failed", error=f"{type(e).__name__}: {e}")
            log("error", f"run failed: {type(e).__name__}: {e}")
            raise

    def _run(self, run_id, request, provider, meter, sandbox, cancel, log) -> dict[str, Any]:
        cfg = self.cfg
        t_start = time.time()
        # ---- specification
        spec, plugin = DomainCompiler(provider).compile(request, cfg.plugin)
        if cfg.target_pass_lower_bound is not None:
            spec.target.min_pass_lower_bound = cfg.target_pass_lower_bound
        if cfg.max_generations is not None:
            spec.budget.max_generations = cfg.max_generations
        if cfg.population_size is not None:
            spec.budget.population_size = cfg.population_size
        meter.max_usd, meter.max_calls = spec.budget.max_usd, spec.budget.max_llm_calls
        self.store.update_run(run_id, spec=spec.model_dump())
        log("spec", f"compiled spec for domain {spec.domain}; target: {spec.target.model_dump()}",
            {"budget": spec.budget.model_dump()})
        if isinstance(provider, MockProvider):
            plugin.register_simulated_models(provider)
            log("warning", "Mock provider (Claude Code CLI not found or --provider mock): LLM stages use a "
                           "SIMULATED model")

        # ---- worlds
        world = WorldGenerator(plugin, cfg.seed, cfg.world)
        splits = world.build()
        holdout = splits.pop("holdout")
        attempt = 0
        commitment = _commit(holdout)
        self.store.save_scenarios(run_id, "train", splits["train"])
        self.store.save_scenarios(run_id, "validation", splits["validation"])
        log("world", f"train={len(splits['train'])} validation={len(splits['validation'])} "
                     f"holdout#0=sealed ({len(holdout)})", {"holdout_commitment": commitment})

        executor = Executor(sandbox, provider, plugin.tool_library())
        evaluator = EvaluationEngine(spec, plugin, executor, cfg.seed, cfg.metamorphic_sample)
        rng = random.Random(derive_seed(cfg.seed, "evolution"))
        synth = CandidateSynthesizer(spec, plugin, provider, random.Random(derive_seed(cfg.seed, "synth")))
        adversary = AdversarialEngine(plugin, evaluator, cfg.seed, cfg.adversary_probes, cfg.adversary_rounds)

        initial = []
        for ir in synth.initial_population(spec.budget.population_size):
            problems = [f"{c.name}: {p}" for c in ir.code_components() for p in validate_source(c.source)]
            if problems:
                self.store.save_candidate(run_id, ir, "rejected", "static: " + "; ".join(problems[:3]))
                continue
            initial.append(ir)
        log("population", f"initial population: {len(initial)} candidates",
            {"architectures": sorted({i.architecture().value for i in initial})})

        def on_eval(ind: Individual, gen: int) -> None:
            self.store.save_candidate(run_id, ind.ir)
            self.store.save_evaluation(run_id, gen, ind.result)

        def on_gen(stats, pop) -> None:
            d = asdict(stats)
            self.store.save_generation(run_id, stats.generation, d)
            log("generation", f"gen {stats.generation}: best feasible train pass={stats.best_feasible_pass_rate:.3f} "
                              f"best={stats.best_pass_rate:.3f} feasible={stats.feasible}/{stats.population} "
                              f"eps={stats.epsilon:.4f} restarts={stats.restarts} spend=${stats.spend_usd:.3f}", d)

        def on_reject(ir, why, gen) -> None:
            self.store.save_candidate(run_id, ir, "rejected", why)

        def budget_check() -> str | None:
            b = spec.budget
            if b.max_usd is not None and meter.usd >= b.max_usd:
                return f"budget_usd (${meter.usd:.3f} >= ${b.max_usd})"
            if b.max_llm_calls is not None and meter.calls >= b.max_llm_calls:
                return "budget_llm_calls"
            if b.max_wall_seconds is not None and time.time() - t_start > b.max_wall_seconds:
                return "wall_clock"
            if cfg.max_holdout_attempts is not None and attempt >= cfg.max_holdout_attempts:
                return f"max_holdout_attempts ({cfg.max_holdout_attempts})"
            return None

        # ---- certification gate (validation) + sealed holdout attempts
        val_results: dict[str, EvalResult] = {}
        hold_results: dict[str, EvalResult] = {}
        attempts: list[dict[str, Any]] = []
        gated: set[str] = set()
        state: dict[str, Any] = {"certified": None, "holdout": holdout, "commitment": commitment}

        def gate(pop: list[Individual], gen: int) -> bool:
            nonlocal attempt
            cands = sorted((p for p in pop if p.result.feasible and p.ir.fingerprint() not in gated),
                           key=lambda p: (-p.result.metrics["pass_rate"], p.result.metrics["cost_per_item_usd"]))
            for p in cands[: cfg.gate_candidates_per_generation]:
                gated.add(p.ir.fingerprint())
                v = evaluator.evaluate(p.ir, splits["validation"], split="validation")
                val_results[p.ir.id] = v
                self.store.save_evaluation(run_id, gen, v)
                ok, unmet = meets_target(spec, v)
                log("gate", f"{p.ir.id} validation pass={v.metrics['pass_rate']:.3f} "
                            f"CI=[{v.intervals['pass_rate'][0]:.3f},{v.intervals['pass_rate'][1]:.3f}] -> "
                            f"{'PASS' if ok else 'fail: ' + '; '.join(unmet)}")
                if not ok:
                    continue
                self.store.set_candidate_status(p.ir.id, "finalist")
                # certification on the current sealed holdout — opened exactly once
                self.store.open_holdout(run_id, attempt, state["commitment"], p.ir.id)
                self.store.save_scenarios(run_id, f"holdout#{attempt}", state["holdout"])
                h = evaluator.evaluate(p.ir, state["holdout"], split=f"holdout#{attempt}")
                hold_results[p.ir.id] = h
                self.store.save_evaluation(run_id, -2, h)
                hok, hunmet = meets_target(spec, h)
                rec = {"attempt": attempt, "candidate_id": p.ir.id, "generation": gen, "passed": hok,
                       "unmet": hunmet, "commitment": state["commitment"], "n": h.n,
                       "pass_rate": h.metrics["pass_rate"], "pass_ci": list(h.intervals["pass_rate"]),
                       "hard_violations": h.hard_violation_counts}
                attempts.append(rec)
                self.store.close_holdout(run_id, attempt, hok, rec)
                log("certification", f"holdout#{attempt} {p.ir.id}: "
                                     f"{'CERTIFIED' if hok else 'failed: ' + '; '.join(hunmet)}", rec)
                if hok:
                    state["certified"] = (p, v, h, attempt)
                    return True
                # retire this holdout: its cases become training data, a fresh independent holdout is sealed
                engine.extend_pool(state["holdout"], origin=f"retired_holdout#{attempt}")
                attempt += 1
                state["holdout"] = world.generate_split(f"holdout#{attempt}", cfg.world.holdout_size)
                state["commitment"] = _commit(state["holdout"])
                log("world", f"holdout#{attempt - 1} retired into train; holdout#{attempt} sealed "
                             f"({len(state['holdout'])})", {"holdout_commitment": state["commitment"]})
                return False
            return False

        evo_cfg = EvolutionConfig(population_size=spec.budget.population_size,
                                  generations=spec.budget.max_generations,
                                  max_wall_seconds=spec.budget.max_wall_seconds)
        engine = EvolutionEngine(evaluator, synth, evo_cfg, rng, adversary, on_eval, on_gen, on_reject, cancel.is_set)
        engine.on_pool_added = lambda scen: self.store.save_scenarios(run_id, "train", scen)
        engine.run(initial, splits["train"], gate, budget_check)
        log("search", f"search stopped: {engine.stop_reason}")

        # ---- choose what to export
        certified = state["certified"]
        if certified:
            rec_ind, rec_val, rec_hold, rec_attempt = certified
        else:
            # not certified (cancelled / limit): best validated candidate, else best train candidate
            pool_all = list(engine.all_individuals.values())
            if val_results:
                rid = max(val_results, key=lambda k: (val_results[k].feasible,
                                                      -val_results[k].total_hard_violations,
                                                      val_results[k].intervals["pass_rate"][0]))
                rec_ind = engine.all_individuals[rid]
            else:
                rec_ind = max(pool_all, key=lambda p: (p.result.feasible, -p.result.metrics["hard_violation_rate"],
                                                       p.result.metrics["pass_rate"]))
            rec_val = val_results.get(rec_ind.ir.id) or evaluator.evaluate(rec_ind.ir, splits["validation"],
                                                                          "validation")
            val_results[rec_ind.ir.id] = rec_val
            if rec_ind.ir.id in hold_results:
                rec_hold = hold_results[rec_ind.ir.id]
                rec_attempt = next(a["attempt"] for a in attempts if a["candidate_id"] == rec_ind.ir.id)
            else:  # report-only evaluation on the current (still sealed) holdout; it is spent afterwards
                self.store.open_holdout(run_id, attempt, state["commitment"], rec_ind.ir.id)
                self.store.save_scenarios(run_id, f"holdout#{attempt}", state["holdout"])
                rec_hold = evaluator.evaluate(rec_ind.ir, state["holdout"], split=f"holdout#{attempt}")
                rec = {"attempt": attempt, "candidate_id": rec_ind.ir.id, "passed": False, "report_only": True,
                       "commitment": state["commitment"], "n": rec_hold.n, "pass_rate": rec_hold.metrics["pass_rate"],
                       "pass_ci": list(rec_hold.intervals["pass_rate"]), "hard_violations": rec_hold.hard_violation_counts,
                       "unmet": meets_target(spec, rec_hold)[1]}
                attempts.append(rec)
                self.store.close_holdout(run_id, attempt, False, rec)
                rec_attempt = attempt
                hold_results[rec_ind.ir.id] = rec_hold
        self.store.set_candidate_status(rec_ind.ir.id, "certified" if certified else "best_uncertified")

        # finalists for the report: everything that went through the validation gate
        fin_inds = [engine.all_individuals[i] for i in val_results if i in engine.all_individuals]
        differential = EvaluationEngine.differential([val_results[i.ir.id] for i in fin_inds])
        vecs = {p.ir.id: (val_results[p.ir.id].feasible, evaluator.objective_vector(val_results[p.ir.id]))
                for p in fin_inds}
        val_pareto = [cid for cid, (f, v) in vecs.items()
                      if f and not any(f2 and dominates(v2, v) for c2, (f2, v2) in vecs.items() if c2 != cid)]

        acceptance = []
        for a in spec.acceptance_criteria:
            point = rec_hold.metrics.get(a.metric, 0.0)
            iv = rec_hold.intervals.get(a.metric)
            if a.use_confidence_bound and iv:
                obs, bound = (iv[0], "lower 95% bound") if a.op == ">=" else (iv[1], "upper 95% bound")
            else:
                obs, bound = point, "point estimate"
            ok = obs >= a.threshold if a.op == ">=" else obs <= a.threshold if a.op == "<=" else obs == a.threshold
            acceptance.append({"id": a.id, "metric": a.metric, "op": a.op, "threshold": a.threshold,
                               "observed": obs, "point": point, "bound": bound, "passed": ok})

        out_root = Path(cfg.output_dir) / run_id
        simulated = provider.simulated and bool(rec_ind.ir.llm_components())
        drift = rec_hold.per_family.get("layout_drift", {})
        gen_gap = {"train": rec_ind.result.metrics["pass_rate"], "validation": rec_val.metrics["pass_rate"],
                   "holdout": rec_hold.metrics["pass_rate"],
                   "drift": (f"{drift['pass_rate']:.3f} (n={drift['n']}, CI [{drift['ci_low']:.2f}, "
                             f"{drift['ci_high']:.2f}])") if drift else "n/a"}
        limitations = list(spec.verification_limits) + [
            "'Ideal' is defined operationally by the Target (zero hard violations, all constraints, pass-rate "
            f"lower 95% bound >= {spec.target.min_pass_lower_bound}) on a sealed holdout; 100% correctness cannot "
            "be proven on a finite sample.",
            "All scenarios are generated by SkillForge's world model; realism labels mark which families imitate "
            "real documents, but none is a real-world sample.",
            "Confidence intervals are Wilson 95% intervals over scenarios; they quantify sampling noise of the "
            "synthetic distribution only.",
            "The validation split was reused to gate many candidates; it is therefore optimistically biased. Only "
            "the holdout numbers are unbiased estimates.",
            "Evolutionary search is heuristic: the result is the first skill found that meets the target, not a "
            "proven optimum.",
            f"Sandbox backend `{sandbox.backend}` ({sandbox.describe()['strength']}).",
        ]
        retired = [a for a in attempts if not a.get("passed") and not a.get("report_only")]
        if retired:
            limitations.append(f"{len(retired)} earlier holdout set(s) failed certification and were retired into "
                               "training data; each later holdout was generated independently afterwards. Families "
                               "present in retired holdouts (incl. drift) are no longer 'unseen'.")
        if provider.simulated:
            limitations.insert(0, "LLM stages were executed against a SIMULATED model (mock mode). Their quality, "
                                  "latency and cost numbers are modelling assumptions.")
        if not certified:
            limitations.insert(0, f"NOT CERTIFIED: the run stopped ({engine.stop_reason}) before any candidate met "
                                  "the target. The exported skill is the best found so far and must not be treated "
                                  "as meeting the specification.")
        cert_commit = next((a["commitment"] for a in attempts if a["attempt"] == rec_attempt), state["commitment"])
        evaluation_doc = {"train": rec_ind.result.summary(), "validation": rec_val.summary(),
                          "holdout": rec_hold.summary(), "acceptance": acceptance, "certified": bool(certified),
                          "holdout_attempts": attempts}
        report: dict[str, Any] = {
            "run_id": run_id, "seed": cfg.seed, "provider": provider.name, "simulated": provider.simulated,
            "sandbox": sandbox.describe(), "spec": spec.model_dump(), "stop_reason": engine.stop_reason,
            "certified": bool(certified), "target": spec.target.model_dump(),
            "spend": meter.snapshot(), "holdout_commitment": cert_commit, "holdout_opened_once": True,
            "holdout_attempts": attempts,
            "worlds": {"train(final pool)": self._world_info(engine.final_pool),
                       "validation": self._world_info(splits["validation"]),
                       f"holdout#{rec_attempt}": self._world_info(
                           state["holdout"] if not certified or rec_attempt == attempt else [])},
            "family_realism": {f.name: f.realism + (" (holdout-only)" if f.holdout_only else "")
                               for f in plugin.families()},
            "generations": [asdict(g) for g in engine.history], "restarts": engine.restarts,
            "n_candidates": len(engine.all_individuals), "rejections": engine.rejections,
            "op_effectiveness": {k: v for k, v in sorted(synth.op_stats.items(), key=lambda kv: -kv[1]["improved"])
                                 if v["improved"]},
            "llm_code_proposals": synth.llm_code_proposals,
            "adversary": {"probes": sum(h.probes_tried for h in adversary.history),
                          "found": sum(h.found for h in adversary.history),
                          "effective": dict(adversary.total_effective.most_common(6))},
            "finalists": [{"id": p.ir.id, "label": p.ir.label, "architecture": p.ir.architecture().value,
                           "validation": val_results[p.ir.id].summary()} for p in fin_inds],
            "differential": differential, "validation_pareto": val_pareto,
            "selection_rule": ("first candidate that passed the validation gate AND sealed-holdout certification "
                               "(gate order: strictly feasible on train, highest train pass rate, lowest cost)")
            if certified else "not certified — best validated candidate (feasibility, violations, pass-rate bound)",
            "finalist_rule": "every candidate evaluated by the validation gate",
            "recommended": {"id": rec_ind.ir.id, "label": rec_ind.ir.label,
                            "architecture": rec_ind.ir.architecture().value},
            "holdout": {cid: r.summary() for cid, r in hold_results.items()},
            "acceptance": acceptance, "generalization_gap": gen_gap, "limitations": limitations,
            "evaluation_stats": {"executions": evaluator.executions, "cache_hits": evaluator.cache_hits,
                                 "sandbox": sandbox.stats},
            "elapsed_s": round(time.time() - t_start, 2), "artifacts": [],
        }

        compiler = SkillCompiler(spec, plugin)
        regression = [(s, o.passed) for s, o in zip(splits["validation"], rec_val.outcomes)][:60]
        md = render_markdown(report)
        pkg = compiler.compile(rec_ind.ir, out_root / "skill", run_id=run_id, seed=cfg.seed, report_md=md,
                               evaluation=evaluation_doc, regression=regression, provider=provider.name,
                               simulated=simulated, dependencies=_deps())
        artifacts = [str(pkg)]
        alts = [p for p in fin_inds if p.ir.id in val_pareto and p.ir.id != rec_ind.ir.id]
        for i, p in enumerate(alts[: cfg.export_alternates]):
            alt_eval = {"validation": val_results[p.ir.id].summary(), "certified": False,
                        "note": "alternate from the validation Pareto set; never certified on a holdout"}
            alt_reg = [(s, o.passed) for s, o in zip(splits["validation"], val_results[p.ir.id].outcomes)][:30]
            ap = compiler.compile(p.ir, out_root / "alternates" / f"{i + 1}-{p.ir.architecture().value}",
                                  run_id=run_id, seed=cfg.seed, report_md=md, evaluation=alt_eval,
                                  regression=alt_reg, provider=provider.name,
                                  simulated=provider.simulated and bool(p.ir.llm_components()), dependencies=_deps())
            artifacts.append(str(ap))
        report["artifacts"] = artifacts + [str(out_root / "report.md"), str(out_root / "report.json")]
        md = render_markdown(report)
        (out_root / "report.md").write_text(md)
        (pkg / "references" / "report.md").write_text(md)
        (out_root / "report.json").write_text(json.dumps(report, indent=2, default=str))
        for a in report["artifacts"]:
            p = Path(a)
            if p.is_file():
                self.store.save_artifact(run_id, a, hashlib.sha256(p.read_bytes()).hexdigest(), "report")
            elif p.is_dir():
                self.store.save_artifact(run_id, a, hashlib.sha256((p / "manifest.json").read_bytes()).hexdigest(),
                                         "skill_package")
        summary = {"recommended": report["recommended"], "certified": bool(certified),
                   "holdout_pass_rate": rec_hold.metrics["pass_rate"],
                   "holdout_pass_ci": list(rec_hold.intervals["pass_rate"]),
                   "hard_violations": rec_hold.hard_violation_counts,
                   "acceptance_passed": all(a["passed"] for a in acceptance), "holdout_attempts": len(attempts),
                   "spend_usd": meter.usd, "simulated": provider.simulated, "stop_reason": engine.stop_reason,
                   "generations": len(engine.history) - 1, "package": str(pkg), "elapsed_s": report["elapsed_s"]}
        self.store.update_run(run_id, status="certified" if certified else "stopped", summary=summary)
        log("done", f"{'CERTIFIED' if certified else 'NOT certified'}; exported {pkg}", summary)
        return report

    @staticmethod
    def _world_info(scen) -> dict[str, Any]:
        fams: dict[str, int] = {}
        origins: dict[str, int] = {}
        for s in scen:
            fams[s.family] = fams.get(s.family, 0) + 1
            o = s.origin.split("#")[0]
            origins[o] = origins.get(o, 0) + 1
        return {"n": len(scen), "families": len(fams), "origins": origins}
