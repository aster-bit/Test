"""EvaluationEngine — the only component that decides how good a candidate is.

Evidence hierarchy (strongest first), each recorded separately:
1. formal invariants on the output itself (e.g. status=valid => arithmetic holds);
2. deterministic ground-truth comparison on generated scenarios;
3. property checks (hard invariants over every output);
4. metamorphic relations (semantics-preserving input transforms);
5. differential agreement between finalists (diagnostic only);
6. adversarial scenarios (robustness / security metrics);
7. statistical intervals (Wilson; exact zero-failure bound).
No LLM judge is used in the invoice domain: every criterion has an objective check.
"""

from __future__ import annotations

import hashlib
import json
import random
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from .domains.base import DomainPlugin, Scenario
from .executor import Executor, ItemTrace
from .ir import SkillIR
from .spec import Direction, SkillSpec
from .stats import mean, percentile, wilson, zero_failure_upper_bound
from .world import derive_seed

STRESS_CATEGORIES = {"edge", "erroneous", "missing", "contradictory", "dependency_failure", "resource",
                     "concurrency", "sequence", "injection", "drift"}


@dataclass
class ScenarioOutcome:
    scenario_id: str
    family: str
    passed: bool
    scores: dict[str, float]
    hard_violations: list[str]
    soft_violations: list[str]
    error: str | None
    latency_ms: float
    cost_usd: float
    tokens: int
    output: dict[str, Any] | None = None


@dataclass
class EvalResult:
    candidate_id: str
    split: str
    n: int
    metrics: dict[str, float]
    intervals: dict[str, tuple[float, float]]
    hard_violation_counts: dict[str, int]
    soft_violation_counts: dict[str, int]
    constraint_violations: dict[str, float]
    per_family: dict[str, dict[str, float]]
    feasible: bool
    outcomes: list[ScenarioOutcome] = field(default_factory=list)
    simulated_llm: bool = False
    failure_examples: list[dict[str, Any]] = field(default_factory=list)

    @property
    def total_hard_violations(self) -> int:
        return sum(self.hard_violation_counts.values())

    @property
    def constraint_magnitude(self) -> float:
        return self.total_hard_violations + sum(self.constraint_violations.values())

    def summary(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id, "split": self.split, "n": self.n,
            "metrics": {k: round(v, 6) for k, v in self.metrics.items()},
            "intervals": {k: [round(a, 4), round(b, 4)] for k, (a, b) in self.intervals.items()},
            "hard_violations": self.hard_violation_counts, "soft_violations": self.soft_violation_counts,
            "constraint_violations": self.constraint_violations, "per_family": self.per_family,
            "feasible": self.feasible, "simulated_llm": self.simulated_llm,
            "failure_examples": self.failure_examples[:8],
        }


class EvaluationEngine:
    def __init__(self, spec: SkillSpec, plugin: DomainPlugin, executor: Executor, master_seed: int,
                 metamorphic_sample: int = 12):
        self.spec = spec
        self.plugin = plugin
        self.executor = executor
        self.master_seed = master_seed
        self.metamorphic_sample = metamorphic_sample
        self.checkers = plugin.invariant_checkers()
        self.hard = [i for i in spec.invariants if i.hard]
        self.soft = [i for i in spec.invariants if not i.hard]
        self.families = {f.name: f for f in plugin.families()}
        # (candidate fingerprint, scenario id, input hash) -> trace. Identical candidates are never re-run,
        # so re-evaluating survivors on a grown train pool only pays for the new scenarios.
        self._cache: dict[tuple[str, str, str], ItemTrace] = {}
        self.cache_hits = 0
        self.executions = 0

    def _run_cached(self, ir: SkillIR, ids: list[str], inputs: list[dict[str, Any]]) -> list[ItemTrace]:
        fp = ir.fingerprint()
        keys = [(fp, sid, hashlib.sha1(json.dumps(x, sort_keys=True, default=str).encode()).hexdigest())
                for sid, x in zip(ids, inputs)]
        missing = [i for i, k in enumerate(keys) if k not in self._cache]
        self.cache_hits += len(keys) - len(missing)
        if missing:
            fresh = self.executor.run(ir, [inputs[i] for i in missing])
            self.executions += len(missing)
            for i, t in zip(missing, fresh):
                self._cache[keys[i]] = t
        return [self._cache[k] for k in keys]

    # ------------------------------------------------------------------
    def evaluate(self, ir: SkillIR, scenarios: list[Scenario], split: str,
                 metamorphic: bool = True) -> EvalResult:
        traces = self._run_cached(ir, [s.id for s in scenarios], [s.input for s in scenarios])
        outcomes = [self._outcome(s, t) for s, t in zip(scenarios, traces)]
        result = self._aggregate(ir, split, scenarios, outcomes, traces)
        if metamorphic and self.metamorphic_sample > 0:
            mc, mn = self._metamorphic(ir, scenarios, {o.scenario_id: o for o in outcomes})
            result.metrics["metamorphic_consistency"] = mc
            result.metrics["metamorphic_checks"] = float(mn)
        return result

    def _outcome(self, s: Scenario, t: ItemTrace) -> ScenarioOutcome:
        out = t.output if t.error is None else None
        scores = self.plugin.score(s, out)
        hard = [i.id for i in self.hard if not self.checkers[i.check](s, out).ok]
        soft = [i.id for i in self.soft if not self.checkers[i.check](s, out).ok]
        return ScenarioOutcome(
            scenario_id=s.id, family=s.family, passed=bool(scores.get("pass", 0.0)) and not hard, scores=scores,
            hard_violations=hard, soft_violations=soft, error=t.error, latency_ms=t.latency_ms,
            cost_usd=t.cost_usd, tokens=t.input_tokens + t.output_tokens, output=out)

    def _aggregate(self, ir: SkillIR, split: str, scenarios: list[Scenario], outcomes: list[ScenarioOutcome],
                   traces: list[ItemTrace]) -> EvalResult:
        n = len(outcomes)
        passes = sum(o.passed for o in outcomes)
        stress = [o for o, s in zip(outcomes, scenarios)
                  if self.families.get(s.family) and self.families[s.family].category in STRESS_CATEGORIES
                  or s.origin in ("adversary", "perturbed")]
        inj = [o for o, s in zip(outcomes, scenarios)
               if (self.families.get(s.family) and self.families[s.family].category == "injection")
               or "p_inject" in s.tags]
        hard_counts: dict[str, int] = defaultdict(int)
        soft_counts: dict[str, int] = defaultdict(int)
        for o in outcomes:
            for h in o.hard_violations:
                hard_counts[h] += 1
            for sv in o.soft_violations:
                soft_counts[sv] += 1
        items_with_hard = sum(1 for o in outcomes if o.hard_violations)
        lat = [o.latency_ms for o in outcomes]
        llm_comps = ir.llm_components()
        metrics = {
            "pass_rate": passes / n if n else 0.0,
            "field_accuracy": mean([o.scores.get("field_accuracy", 0.0) for o in outcomes]),
            "status_accuracy": mean([o.scores.get("status_accuracy", 0.0) for o in outcomes]),
            "issue_f1": mean([o.scores.get("issue_f1", 0.0) for o in outcomes]),
            "robustness": (sum(o.passed for o in stress) / len(stress)) if stress else 0.0,
            "security": 1.0 - (sum(1 for o in inj if o.hard_violations) / len(inj)) if inj else 1.0,
            "error_rate": sum(1 for o in outcomes if o.error) / n if n else 0.0,
            "hard_violation_rate": items_with_hard / n if n else 0.0,
            "latency_ms_p50": percentile(lat, 0.5),
            "latency_ms_p95": percentile(lat, 0.95),
            "cost_per_item_usd": mean([o.cost_usd for o in outcomes]),
            "tokens_per_item": mean([float(o.tokens) for o in outcomes]),
            "llm_calls_per_item": mean([float(t.llm_calls) for t in traces]),
            "complexity": ir.complexity(),
            "portability": 1.0 if not llm_comps else max(0.2, 0.6 - 0.1 * sum(len(c.tools) for c in llm_comps)),
        }
        intervals = {
            "pass_rate": wilson(passes, n),
            "robustness": wilson(sum(o.passed for o in stress), len(stress)),
            "hard_violation_rate": ((0.0, zero_failure_upper_bound(n)) if items_with_hard == 0
                                    else wilson(items_with_hard, n)),
        }
        per_family: dict[str, dict[str, float]] = {}
        groups: dict[str, list[ScenarioOutcome]] = defaultdict(list)
        for o in outcomes:
            groups[o.family].append(o)
        for fam, os_ in sorted(groups.items()):
            k = sum(o.passed for o in os_)
            lo, hi = wilson(k, len(os_))
            per_family[fam] = {"n": len(os_), "pass_rate": k / len(os_), "ci_low": lo, "ci_high": hi,
                               "hard_violations": sum(len(o.hard_violations) for o in os_)}
        constraints = self._constraint_violations(ir, metrics)
        failures = [
            {"scenario_id": o.scenario_id, "family": o.family, "hard": o.hard_violations, "error": o.error,
             "scores": o.scores, "input_excerpt": (lambda t: t if len(t) <= 1400 else t[:500] + "\n…\n" + t[-900:])(
                 str((s.input or {}).get("text", ""))),
             "expected_status": (s.expected or {}).get("status"), "got_status": (o.output or {}).get("status")}
            for s, o in zip(scenarios, outcomes) if not o.passed
        ]
        # hard-constraint failures first: they drive failure-directed mutation and the report
        failures.sort(key=lambda f: (not f["hard"], f["error"] is None))
        return EvalResult(
            candidate_id=ir.id, split=split, n=n, metrics=metrics, intervals=intervals,
            hard_violation_counts=dict(hard_counts), soft_violation_counts=dict(soft_counts),
            constraint_violations=constraints, per_family=per_family,
            feasible=items_with_hard == 0 and not constraints, outcomes=outcomes,
            simulated_llm=any(t.simulated_llm for t in traces), failure_examples=failures[:20])

    def _constraint_violations(self, ir: SkillIR, m: dict[str, float]) -> dict[str, float]:
        v: dict[str, float] = {}
        for c in self.spec.constraints:
            if c.kind == "no_llm" and ir.llm_components():
                v[c.id] = 1.0
            elif c.kind == "latency_ms_p95" and c.value is not None and m["latency_ms_p95"] > float(c.value):
                v[c.id] = (m["latency_ms_p95"] - float(c.value)) / float(c.value)
            elif c.kind == "cost_per_item_usd" and c.value is not None and m["cost_per_item_usd"] > float(c.value):
                v[c.id] = (m["cost_per_item_usd"] - float(c.value)) / max(float(c.value), 1e-9)
            elif c.kind == "min_metric" and c.metric and c.value is not None and m.get(c.metric, 0.0) < float(c.value):
                v[c.id] = (float(c.value) - m.get(c.metric, 0.0)) / max(float(c.value), 1e-9)
            elif c.kind == "max_complexity" and c.value is not None and m["complexity"] > float(c.value):
                v[c.id] = (m["complexity"] - float(c.value)) / float(c.value)
        return v

    def _metamorphic(self, ir: SkillIR, scenarios: list[Scenario],
                     base: dict[str, ScenarioOutcome]) -> tuple[float, int]:
        rels = self.plugin.metamorphic_relations()
        if not rels:
            return 1.0, 0
        rng = random.Random(derive_seed(self.master_seed, "metamorphic", len(scenarios)))
        pool = [s for s in scenarios if base[s.id].output is not None]
        jobs = []
        for rel in rels:
            cands = [s for s in pool if rel.applies_to(s)]
            for s in rng.sample(cands, min(len(cands), max(1, self.metamorphic_sample // len(rels)))):
                jobs.append((rel, s, rel.transform(s.input, random.Random(derive_seed(self.master_seed, rel.id, s.id)))))
        if not jobs:
            return 1.0, 0
        traces = self._run_cached(ir, [f"mr:{rel.id}:{s.id}" for rel, s, _ in jobs], [j[2] for j in jobs])
        ok = sum(1 for (rel, s, _), t in zip(jobs, traces)
                 if t.output is not None and rel.relation(base[s.id].output, t.output))
        return ok / len(jobs), len(jobs)

    # ------------------------------------------------------------------
    @staticmethod
    def differential(results: list[EvalResult]) -> dict[str, float]:
        """Disagreement of each candidate with the majority status of the set (no ground truth used)."""
        if len(results) < 3:
            return {}
        by_sid: dict[str, list[str | None]] = defaultdict(list)
        for r in results:
            for o in r.outcomes:
                by_sid[o.scenario_id].append((o.output or {}).get("status"))
        majority = {sid: max(set(v), key=v.count) for sid, v in by_sid.items()}
        out = {}
        for r in results:
            dis = [((o.output or {}).get("status") != majority[o.scenario_id]) for o in r.outcomes]
            out[r.candidate_id] = sum(dis) / len(dis) if dis else 0.0
        return out

    def objective_vector(self, r: EvalResult) -> list[float]:
        """Objectives converted to 'maximize' form, in spec order."""
        vec = []
        for o in self.spec.objectives:
            v = r.metrics.get(o.metric, 0.0)
            if o.metric.startswith("latency"):
                # wall-clock measurements jitter between runs; quantise so selection is reproducible per seed
                v = round(v / 50.0) * 50.0
            vec.append(v if o.direction == Direction.MAXIMIZE else -v)
        return vec
