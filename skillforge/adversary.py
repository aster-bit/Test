"""AdversarialEngine — searches for inputs that break the current elite.

Search space: compositions of the domain's truth-tracking perturbations applied
to train scenarios and to fresh scenarios drawn from an *adversary-only* seed
namespace. Fitness of a probe = fraction of target candidates it breaks (hard
violations weigh double). A few rounds of hill-climbing extend successful
probes. Found cases are appended to the train pool.

Isolation: the engine is constructed without any reference to the holdout
split, and every probe id is prefixed ``adv-`` so leaks are detectable.
"""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass, field

from .domains.base import DomainPlugin, Scenario
from .evaluation import EvaluationEngine
from .ir import SkillIR
from .world import derive_seed


@dataclass
class AdversaryReport:
    generation: int
    probes_tried: int
    found: int
    by_perturbation: dict[str, int] = field(default_factory=dict)
    mean_break_rate: float = 0.0


class AdversarialEngine:
    def __init__(self, plugin: DomainPlugin, evaluator: EvaluationEngine, master_seed: int,
                 probes_per_round: int = 24, rounds: int = 2, keep: int = 12):
        self.plugin = plugin
        self.evaluator = evaluator
        self.master_seed = master_seed
        self.probes_per_round = probes_per_round
        self.rounds = rounds
        self.keep = keep
        self.perturbations = plugin.perturbations()
        self.history: list[AdversaryReport] = []
        self.total_effective = Counter()

    def _fresh(self, rng: random.Random, gen: int, i: int) -> Scenario:
        fams = [f for f in self.plugin.families() if not f.holdout_only]
        fam = rng.choices([f.name for f in fams], weights=[f.weight for f in fams])[0]
        seed = derive_seed(self.master_seed, "adversary", gen, i)
        return self.plugin.generate(fam, random.Random(seed), f"adv-base-{gen}-{i}", seed)

    def _perturb(self, s: Scenario, rng: random.Random, k: int) -> Scenario | None:
        cur = s
        for _ in range(k):
            p = rng.choice(self.perturbations)
            nxt = p.apply(cur, rng)
            if nxt is not None:
                cur = nxt
        return cur if cur is not s else None

    def _break_score(self, targets: list[SkillIR], probes: list[Scenario]) -> list[float]:
        scores = [0.0] * len(probes)
        for ir in targets:
            res = self.evaluator.evaluate(ir, probes, split="adversary_probe", metamorphic=False)
            for i, o in enumerate(res.outcomes):
                scores[i] += (0.0 if o.passed else 1.0) + (1.0 if o.hard_violations else 0.0)
        return [s / (2 * len(targets)) for s in scores]

    def attack(self, targets: list[SkillIR], train_pool: list[Scenario], generation: int) -> tuple[list[Scenario], AdversaryReport]:
        if not self.perturbations or not targets:
            return [], AdversaryReport(generation, 0, 0)
        rng = random.Random(derive_seed(self.master_seed, "adversary-search", generation))
        base_pool = [s for s in train_pool if s.origin == "world" and (s.input.get("text") or "").strip()]
        probes: list[Scenario] = []
        for i in range(self.probes_per_round):
            base = rng.choice(base_pool) if base_pool and rng.random() < 0.5 else self._fresh(rng, generation, i)
            p = self._perturb(base, rng, rng.randint(1, 3))
            if p is not None:
                p.id = f"adv-g{generation}-r0-{i}"
                probes.append(p)
        tried = len(probes)
        scored = list(zip(self._break_score(targets, probes), probes)) if probes else []
        for r in range(1, self.rounds):
            scored.sort(key=lambda t: -t[0])
            parents = [p for s, p in scored[: max(2, len(scored) // 3)] if s > 0]
            if not parents:
                break
            children = []
            for i in range(self.probes_per_round // 2):
                c = self._perturb(rng.choice(parents), rng, 1)
                if c is not None:
                    c.id = f"adv-g{generation}-r{r}-{i}"
                    children.append(c)
            tried += len(children)
            if children:
                scored += list(zip(self._break_score(targets, children), children))
        scored.sort(key=lambda t: -t[0])
        chosen: list[Scenario] = []
        sig_count: Counter = Counter()
        for s, p in scored:
            if s <= 0 or len(chosen) >= self.keep:
                continue
            sig = tuple(sorted(t for t in p.tags if t.startswith("p_")))
            if sig_count[sig] >= 3:  # keep the set diverse
                continue
            sig_count[sig] += 1
            p.origin = "adversary"
            p.realism = "adversarial"
            chosen.append(p)
        by_pert: Counter = Counter(t for p in chosen for t in p.tags if t.startswith("p_"))
        self.total_effective.update(by_pert)
        rep = AdversaryReport(generation, tried, len(chosen), dict(by_pert),
                              sum(s for s, _ in scored) / len(scored) if scored else 0.0)
        self.history.append(rep)
        return chosen, rep
