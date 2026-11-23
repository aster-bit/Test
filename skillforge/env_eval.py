"""Independent evaluation of synthesised candidates against a TaskEnvironment.

The checker and hidden references live in the environment and run on the host; candidates run in the
sandbox and receive only `instance.input`. Results for the *dev* split may be shown to the generator
(inputs, the candidate's own output and the checker's diagnosis); *selection* results are reduced to
aggregate numbers; the *audit* split is evaluated once per pre-registered candidate.
"""

from __future__ import annotations

import hashlib
import json
import random
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from .envs.base import Instance, TaskEnvironment
from .executor import Executor, ItemTrace
from .ir import SkillIR
from .stats import mean, wilson


@dataclass
class InstanceResult:
    instance_id: str
    family: str
    score: float
    valid: bool
    violations: list[str]
    message: str
    error: str | None
    latency_ms: float
    cost_usd: float
    llm_calls: int
    output: Any = None


@dataclass
class EnvEvalResult:
    candidate_id: str
    split: str
    results: list[InstanceResult] = field(default_factory=list)

    @property
    def scores(self) -> list[float]:
        return [r.score for r in self.results]

    def summary(self) -> dict[str, Any]:
        n = len(self.results)
        valid = sum(r.valid for r in self.results)
        fams: dict[str, list[float]] = {}
        for r in self.results:
            fams.setdefault(r.family, []).append(r.score)
        viol: dict[str, int] = {}
        for r in self.results:
            for v in r.violations:
                viol[v] = viol.get(v, 0) + 1
        lat = sorted(r.latency_ms for r in self.results)
        return {
            "candidate_id": self.candidate_id, "split": self.split, "n": n,
            "mean_score": mean(self.scores), "valid_rate": valid / n if n else 0.0,
            "valid_ci": list(wilson(valid, n)), "errors": sum(1 for r in self.results if r.error),
            "violations": viol, "per_family": {k: round(mean(v), 4) for k, v in sorted(fams.items())},
            "cost_per_item_usd": mean([r.cost_usd for r in self.results]),
            "llm_calls_per_item": mean([float(r.llm_calls) for r in self.results]),
            "latency_ms_p95": lat[int(0.95 * (n - 1))] if n else 0.0,
        }


def bootstrap_mean_ci(xs: list[float], conf: float = 0.95, iters: int = 4000, seed: int = 0) -> tuple[float, float]:
    if not xs:
        return (0.0, 0.0)
    rng = random.Random(seed)
    n = len(xs)
    ms = sorted(sum(xs[rng.randrange(n)] for _ in range(n)) / n for _ in range(iters))
    a = (1 - conf) / 2
    return ms[int(a * iters)], ms[int((1 - a) * iters) - 1]


def paired_diff_ci(a: list[float], b: list[float], conf: float = 0.95, iters: int = 4000,
                   seed: int = 0) -> tuple[float, float, float]:
    """Mean of (a - b) over the same instances with a paired bootstrap CI."""
    d = [x - y for x, y in zip(a, b)]
    lo, hi = bootstrap_mean_ci(d, conf, iters, seed)
    return mean(d), lo, hi


class EnvEvaluator:
    def __init__(self, env: TaskEnvironment, executor: Executor, chunks: int = 4):
        self.env = env
        self.executor = executor
        self.chunks = chunks
        self._cache: dict[tuple[str, str], tuple[ItemTrace, Any]] = {}

    def _key(self, ir: SkillIR, inst: Instance) -> tuple[str, str]:
        h = hashlib.sha1(json.dumps(inst.input, sort_keys=True).encode()).hexdigest()[:12]
        return ir.fingerprint(), inst.id + ":" + h

    def evaluate(self, ir: SkillIR, instances: list[Instance], split: str) -> EnvEvalResult:
        todo = [i for i in instances if self._key(ir, i) not in self._cache]
        if todo:
            size = max(1, -(-len(todo) // self.chunks))
            parts = [todo[k:k + size] for k in range(0, len(todo), size)]
            with ThreadPoolExecutor(max_workers=len(parts)) as pool:
                outs = list(pool.map(lambda part: self.executor.run(ir, [p.input for p in part]), parts))
            for part, traces in zip(parts, outs):
                for inst, tr in zip(part, traces):
                    self._cache[self._key(ir, inst)] = (tr, tr.output)
        res = EnvEvalResult(ir.id, split)
        for inst in instances:
            tr, out = self._cache[self._key(ir, inst)]
            if tr.error:
                chk_valid, score, viol, msg = False, 0.0, ["runtime_error"], tr.error[:300]
            else:
                c = self.env.check(inst, out)
                chk_valid, score, viol, msg = c.valid, c.score, c.violations, c.message
            res.results.append(InstanceResult(inst.id, inst.family, score, chk_valid, viol, msg, tr.error,
                                              tr.latency_ms, tr.cost_usd, tr.llm_calls, out))
        return res
