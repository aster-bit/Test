"""EvolutionEngine — constrained multi-objective population search.

* Selection: NSGA-II (non-dominated sorting + crowding distance) under Deb's
  constraint-domination rule: a feasible candidate beats an infeasible one;
  among infeasible ones, smaller total violation wins. Hard invariants are
  therefore constraints, never just score components.
* Elitism: the best fronts survive unchanged.
* Novelty: behaviour descriptor = pass/fail vector on a fixed reference subset
  of the train pool; a few survivor slots go to the most novel candidates so
  the population does not collapse onto one architecture.
* Overfitting guard: selection only ever sees *train* results; validation is
  used afterwards to choose finalists; holdout is evaluated once by the pipeline.

No claim of global optimality is made: this is a heuristic search under a budget.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .evaluation import EvalResult, EvaluationEngine
from .ir import SkillIR
from .llm import BudgetExceeded
from .synthesis import CandidateSynthesizer


def dominates(a: list[float], b: list[float]) -> bool:
    """a Pareto-dominates b (all objectives in maximize form)."""
    return all(x >= y for x, y in zip(a, b)) and any(x > y for x, y in zip(a, b))


def constrained_dominates(ra: tuple[bool, float, list[float]], rb: tuple[bool, float, list[float]]) -> bool:
    fa, va, oa = ra
    fb, vb, ob = rb
    if fa and not fb:
        return True
    if fb and not fa:
        return False
    if not fa and not fb:
        return va < vb
    return dominates(oa, ob)


def nondominated_sort(records: list[tuple[bool, float, list[float]]]) -> list[list[int]]:
    n = len(records)
    S: list[list[int]] = [[] for _ in range(n)]
    counts = [0] * n
    fronts: list[list[int]] = [[]]
    for p in range(n):
        for q in range(n):
            if p == q:
                continue
            if constrained_dominates(records[p], records[q]):
                S[p].append(q)
            elif constrained_dominates(records[q], records[p]):
                counts[p] += 1
        if counts[p] == 0:
            fronts[0].append(p)
    i = 0
    while fronts[i]:
        nxt = []
        for p in fronts[i]:
            for q in S[p]:
                counts[q] -= 1
                if counts[q] == 0:
                    nxt.append(q)
        i += 1
        fronts.append(nxt)
    return [f for f in fronts if f]


def crowding_distance(objs: list[list[float]]) -> list[float]:
    n = len(objs)
    if n == 0:
        return []
    d = [0.0] * n
    for m in range(len(objs[0])):
        order = sorted(range(n), key=lambda i: objs[i][m])
        lo, hi = objs[order[0]][m], objs[order[-1]][m]
        d[order[0]] = d[order[-1]] = float("inf")
        if hi == lo:
            continue
        for k in range(1, n - 1):
            d[order[k]] += (objs[order[k + 1]][m] - objs[order[k - 1]][m]) / (hi - lo)
    return d


def hamming(a: tuple[int, ...], b: tuple[int, ...]) -> float:
    if not a:
        return 0.0
    return sum(x != y for x, y in zip(a, b)) / len(a)


@dataclass
class Individual:
    ir: SkillIR
    result: EvalResult
    behaviour: tuple[int, ...] = ()
    rank: int = 0
    crowding: float = 0.0
    novelty: float = 0.0

    def record(self, ev: EvaluationEngine, eps: float = 0.0) -> tuple[bool, float, list[float]]:
        """Constraint record under the epsilon-constrained method.

        With eps == 0 this is exactly Deb's rule (any hard violation => infeasible).
        During early generations eps > 0 tolerates a small *rate* of violations so that
        strong-but-imperfect designs are not discarded in favour of trivially "safe" ones
        (e.g. a skill that routes everything to review). eps decays to 0 well before the
        end of the search, and final selection is always strict.
        """
        r = self.result
        rate = r.metrics.get("hard_violation_rate", 0.0)
        excess = max(0.0, rate - eps) + sum(max(0.0, v - eps) for v in r.constraint_violations.values())
        feasible = excess == 0.0
        return (feasible, excess, ev.objective_vector(r))


@dataclass
class GenerationStats:
    generation: int
    population: int
    feasible: int
    pareto_size: int
    best_pass_rate: float
    best_feasible_pass_rate: float
    mean_pass_rate: float
    architectures: dict[str, int]
    train_pool_size: int
    adversarial_added: int
    spend_usd: float
    elapsed_s: float
    rejected: dict[str, int] = field(default_factory=dict)
    epsilon: float = 0.0
    restarts: int = 0
    front: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class EvolutionConfig:
    population_size: int = 14
    generations: int | None = None      # None = open-ended: run until the gate certifies a skill
    elite: int = 3
    novelty_slots: int = 2
    crossover_rate: float = 0.3
    llm_patch_rate: float = 0.1
    max_mutations: int = 3
    patience: int = 4                   # generations without progress before a diversity restart
    max_wall_seconds: float | None = None
    novelty_k: int = 3
    racing: bool = True
    race_fraction: float = 0.25
    epsilon0: float = 0.06              # initial tolerated hard-violation rate (epsilon-constraint method)
    epsilon_generations: int = 5        # epsilon decays to 0 over this many generations after each (re)start
    max_adversarial: int = 240          # cap on adversarial cases kept in the train pool


class EvolutionEngine:
    def __init__(self, evaluator: EvaluationEngine, synthesizer: CandidateSynthesizer, config: EvolutionConfig,
                 rng: random.Random, adversary=None,
                 on_evaluated: Callable[[Individual, int], None] | None = None,
                 on_generation: Callable[[GenerationStats, list[Individual]], None] | None = None,
                 on_rejected: Callable[[SkillIR, str, int], None] | None = None,
                 should_stop: Callable[[], bool] | None = None):
        self.ev = evaluator
        self.syn = synthesizer
        self.cfg = config
        self.rng = rng
        self.adversary = adversary
        self.on_evaluated = on_evaluated or (lambda ind, g: None)
        self.on_generation = on_generation or (lambda st, pop: None)
        self.on_rejected = on_rejected or (lambda ir, why, g: None)
        self.should_stop = should_stop or (lambda: False)
        self.on_pool_added: Callable[[list], None] = lambda scen: None
        self.novelty_archive: list[tuple[int, ...]] = []
        self.history: list[GenerationStats] = []
        self.rejections: dict[str, int] = {}
        self.stop_reason = ""
        self.all_individuals: dict[str, Individual] = {}
        self.eps = config.epsilon0
        self.restart_gen = 0
        self.restarts: list[int] = []
        self.pool: list = []
        self.pool_dirty = False

    def epsilon(self, gen: int) -> float:
        """Epsilon schedule, restarted after every diversity restart."""
        age = gen - self.restart_gen
        return self.cfg.epsilon0 * max(0.0, 1.0 - age / max(1, self.cfg.epsilon_generations)) ** 2

    def extend_pool(self, scenarios: list, origin: str) -> None:
        """Add scenarios to the train pool (e.g. a retired holdout); everyone is re-evaluated next generation."""
        for sc in scenarios:
            sc.origin = origin
        self.pool.extend(scenarios)
        self.pool_dirty = True

    # ------------------------------------------------------------------
    def _reference_ids(self, pool) -> list[str]:
        return [s.id for s in pool if s.origin == "world"][:80]

    def _race(self, ir: SkillIR, pool, gen: int) -> bool:
        """Racing for expensive (real-LLM) candidates: evaluate a stratified subsample first and drop
        the candidate if even the upper 95% bound of its pass rate is below the current best."""
        if not self.cfg.racing or not ir.llm_components() or getattr(self.syn.provider, "simulated", True):
            return True
        best = max((p.result.metrics["pass_rate"] for p in self.all_individuals.values()), default=0.0)
        if best <= 0:
            return True
        by_fam: dict[str, list] = {}
        for s in pool:
            by_fam.setdefault(s.family, []).append(s)
        sub = [s for fam in sorted(by_fam) for s in by_fam[fam][: max(2, int(len(by_fam[fam]) * self.cfg.race_fraction))]]
        res = self.ev.evaluate(ir, sub, split="train_race", metamorphic=False)
        upper = res.intervals["pass_rate"][1]
        if upper < best:
            self._reject(ir, f"raced_out: pass upper bound {upper:.3f} < best {best:.3f} on {len(sub)} items", gen)
            return False
        return True

    def _evaluate(self, ir: SkillIR, pool, ref_ids: list[str], gen: int) -> Individual | None:
        if ir.id not in self.all_individuals and not self._race(ir, pool, gen):
            return None
        res = self.ev.evaluate(ir, pool, split="train")
        if res.metrics["error_rate"] >= 0.999:
            why = (res.failure_examples[0]["error"] if res.failure_examples else "all items failed") or "all items failed"
            self._reject(ir, f"unexecutable: {why[:160]}", gen)
            return None
        byid = {o.scenario_id: o for o in res.outcomes}
        beh = tuple(int(byid[i].passed) for i in ref_ids if i in byid)
        ind = Individual(ir=ir, result=res, behaviour=beh)
        self.all_individuals[ir.id] = ind
        self.on_evaluated(ind, gen)
        return ind

    def _reject(self, ir: SkillIR, why: str, gen: int) -> None:
        key = why.split(":")[0]
        self.rejections[key] = self.rejections.get(key, 0) + 1
        self.on_rejected(ir, why, gen)

    def _rank(self, pop: list[Individual]) -> list[list[int]]:
        recs = [p.record(self.ev, self.eps) for p in pop]
        fronts = nondominated_sort(recs)
        for r, f in enumerate(fronts):
            cd = crowding_distance([recs[i][2] for i in f])
            for i, d in zip(f, cd):
                pop[i].rank, pop[i].crowding = r, d
        archive = self.novelty_archive + [p.behaviour for p in pop]
        for p in pop:
            dists = sorted(hamming(p.behaviour, b) for b in archive if b is not p.behaviour)
            k = dists[: self.cfg.novelty_k]
            p.novelty = sum(k) / len(k) if k else 0.0
        return fronts

    def _tournament(self, pop: list[Individual]) -> Individual:
        a, b = self.rng.sample(pop, 2) if len(pop) > 1 else (pop[0], pop[0])
        key = lambda p: (p.rank, -p.crowding, -p.novelty)  # noqa: E731
        return a if key(a) <= key(b) else b

    def _select(self, pop: list[Individual], n: int) -> list[Individual]:
        fronts = self._rank(pop)
        chosen: list[Individual] = []
        for f in fronts:
            members = sorted((pop[i] for i in f), key=lambda p: -p.crowding)
            if len(chosen) + len(members) <= n - self.cfg.novelty_slots:
                chosen.extend(members)
            else:
                chosen.extend(members[: max(0, n - self.cfg.novelty_slots - len(chosen))])
                break
        rest = sorted((p for p in pop if p not in chosen), key=lambda p: -p.novelty)
        chosen.extend(rest[: n - len(chosen)])
        return chosen

    # ------------------------------------------------------------------
    @staticmethod
    def _failures(ind: "Individual") -> list[dict]:
        """Failure signal for directed mutation: scenario failures plus violated constraints."""
        return list(ind.result.failure_examples) + [{"constraint": k, "amount": v}
                                                    for k, v in ind.result.constraint_violations.items()]

    def _progress_key(self, pop: list[Individual]) -> tuple[float, float]:
        feas = [p.result.metrics["pass_rate"] for p in pop if p.result.feasible]
        least_viol = min((p.result.metrics["hard_violation_rate"] + p.result.constraint_magnitude for p in pop),
                         default=1.0)
        return (max(feas, default=0.0), -least_viol)

    def _restart(self, pop: list[Individual], gen: int) -> list[Individual]:
        """Diversity restart: keep the elite, refill with heavily mutated seeds and elites."""
        # same violation measure as selection (hard invariants AND constraints), then quality
        elites = sorted(pop, key=lambda p: (p.record(self.ev, 0.0)[1], -p.result.metrics["pass_rate"]))[: self.cfg.elite]
        fresh: list[SkillIR] = []
        seen = {p.ir.fingerprint() for p in elites}
        seeds = self.syn.plugin.seed_candidates(self.syn.spec)
        tries = 0
        while len(fresh) < self.cfg.population_size - len(elites) and tries < self.cfg.population_size * 10:
            tries += 1
            src = self.rng.choice(elites).ir if (elites and self.rng.random() < 0.5) else self.rng.choice(seeds)
            child = self.syn.mutate(src, gen + 1, self.rng.randint(2, 6),
                                    failures=(self._failures(elites[0]) if elites else None))
            if child and child.fingerprint() not in seen:
                child.origin = "restart"
                seen.add(child.fingerprint())
                fresh.append(child)
        new_pop = list(elites)
        for ir in fresh:
            ind = self._evaluate(ir, self.pool, self.ref_ids, gen + 1)
            if ind:
                new_pop.append(ind)
        self.restart_gen = gen + 1
        self.restarts.append(gen + 1)
        return new_pop

    def _cap_adversarial(self, pop: list[Individual]) -> None:
        adv = [s for s in self.pool if s.origin == "adversary"]
        excess = len(adv) - self.cfg.max_adversarial
        if excess <= 0:
            return
        # drop the oldest adversarial cases that no current candidate fails any more
        failing = {o.scenario_id for p in pop for o in p.result.outcomes if not o.passed}
        drop: set[str] = set()
        for s in adv:
            if len(drop) >= excess:
                break
            if s.id not in failing:
                drop.add(s.id)
        for s in adv:  # still too many: drop oldest regardless
            if len(drop) >= excess:
                break
            drop.add(s.id)
        self.pool = [s for s in self.pool if s.id not in drop]
        self.pool_dirty = True

    def run(self, initial: list[SkillIR], train_pool: list,
            gate: Callable[[list[Individual], int], bool] | None = None,
            budget_check: Callable[[], str | None] | None = None) -> list[Individual]:
        """Open-ended search. Stops only when ``gate`` certifies a skill, a configured limit is hit,
        or the user cancels. Stagnation triggers a diversity restart, never a stop."""
        t0 = time.time()
        gate = gate or (lambda pop, g: False)
        budget_check = budget_check or (lambda: None)
        self.pool = list(train_pool)
        self.ref_ids = self._reference_ids(self.pool)
        pop: list[Individual] = []
        for ir in initial:
            ind = self._evaluate(ir, self.pool, self.ref_ids, 0)
            if ind:
                pop.append(ind)
        progress: list[tuple[float, float]] = []
        adv_added = 0
        gen = 0
        while True:
            self.eps = self.epsilon(gen)
            self._rank(pop)
            stats = self._stats(gen, pop, len(self.pool), adv_added, time.time() - t0)
            self.history.append(stats)
            self.on_generation(stats, pop)
            try:
                if gate(pop, gen):
                    self.stop_reason = "target_reached"
                    break
            except BudgetExceeded as e:
                self.stop_reason = f"budget: {e}"
                break
            reason = budget_check()
            if reason:
                self.stop_reason = reason
                break
            if self.cfg.generations is not None and gen >= self.cfg.generations:
                self.stop_reason = "max_generations"
                break
            if self.cfg.max_wall_seconds is not None and time.time() - t0 > self.cfg.max_wall_seconds:
                self.stop_reason = "wall_clock"
                break
            if self.should_stop():
                self.stop_reason = "cancelled by user"
                break
            self.novelty_archive.extend(p.behaviour for p in pop)
            self.novelty_archive = self.novelty_archive[-2000:]

            # ---- stagnation => diversity restart (only once epsilon has reached 0 for this epoch)
            progress.append(self._progress_key(pop))
            strict = self.epsilon(gen) == 0.0
            if strict and len(progress) > self.cfg.patience and \
                    max(progress[-self.cfg.patience:]) <= progress[-self.cfg.patience - 1]:
                try:
                    pop = self._restart(pop, gen)
                except BudgetExceeded as e:
                    self.stop_reason = f"budget: {e}"
                    break
                progress.clear()
                adv_added = 0
                gen += 1
                continue

            # ---- adversarial co-evolution: attack the current front, grow the train pool
            adv_added = 0
            if self.adversary is not None:
                targets = [p.ir for p in sorted(pop, key=lambda p: (p.rank, -p.result.metrics["pass_rate"]))[:3]]
                try:
                    found, _rep = self.adversary.attack(targets, self.pool, gen + 1)
                except BudgetExceeded as e:
                    self.stop_reason = f"budget: {e}"
                    break
                self.pool.extend(found)
                self.on_pool_added(found)
                adv_added = len(found)
                if found:
                    self.pool_dirty = True
                self._cap_adversarial(pop)

            # ---- variation
            offspring: list[SkillIR] = []
            seen = {p.ir.fingerprint() for p in pop}
            tries = 0
            while len(offspring) < self.cfg.population_size and tries < self.cfg.population_size * 10:
                tries += 1
                r = self.rng.random()
                child = None
                if r < self.cfg.llm_patch_rate:
                    parent = self._tournament(pop)
                    try:
                        child = self.syn.llm_code_patch(parent.ir, parent.result.failure_examples[:6], gen + 1)
                    except BudgetExceeded:
                        child = None
                if child is None and r < self.cfg.llm_patch_rate + self.cfg.crossover_rate and len(pop) > 1:
                    a, b = self._tournament(pop), self._tournament(pop)
                    if a is not b:
                        child = self.syn.crossover(a.ir, b.ir, gen + 1)
                        if child is not None and self.rng.random() < 0.5:
                            child = self.syn.mutate(child, gen + 1, 1) or child
                if child is None:
                    parent = self._tournament(pop)
                    child = self.syn.mutate(parent.ir, gen + 1, self.rng.randint(1, self.cfg.max_mutations),
                                            failures=self._failures(parent))
                if child is None:
                    continue
                fp = child.fingerprint()
                if fp in seen:
                    self.rejections["duplicate"] = self.rejections.get("duplicate", 0) + 1
                    continue
                seen.add(fp)
                offspring.append(child)

            # ---- evaluate everybody on the (possibly changed) shared train pool
            try:
                pop_eval = []
                dirty, self.pool_dirty = self.pool_dirty, False
                for p in pop:
                    ind = self._evaluate(p.ir, self.pool, self.ref_ids, gen + 1) if dirty else p
                    if ind:
                        pop_eval.append(ind)
                for child in offspring:
                    ind = self._evaluate(child, self.pool, self.ref_ids, gen + 1)
                    if ind:
                        pop_eval.append(ind)
                        parents = [q for q in pop if q.ir.id in set(child.parents)]
                        if parents and ind.result.metrics["pass_rate"] > max(q.result.metrics["pass_rate"] for q in parents):
                            for line in child.mutation_log:
                                op = line.split(":")[0]
                                if op in self.syn.op_stats:
                                    self.syn.op_stats[op]["improved"] += 1
            except BudgetExceeded as e:
                self.stop_reason = f"budget: {e}"
                break
            self.eps = self.epsilon(gen + 1)
            survivors = self._select(pop_eval, self.cfg.population_size)
            for p in pop_eval:
                if p not in survivors and p.ir.generation == gen + 1:
                    why = "dominated" if p.result.feasible else \
                        "infeasible: " + ",".join(sorted(p.result.hard_violation_counts) +
                                                  sorted(p.result.constraint_violations))
                    self._reject(p.ir, why, gen + 1)
            pop = survivors
            gen += 1
        self.eps = 0.0
        self._rank(pop)
        self.final_pool = self.pool
        return pop

    def _stats(self, gen: int, pop: list[Individual], pool_size: int, adv: int, elapsed: float) -> GenerationStats:
        feas = [p for p in pop if p.result.feasible]
        arch: dict[str, int] = {}
        for p in pop:
            a = p.ir.architecture().value
            arch[a] = arch.get(a, 0) + 1
        front = [p for p in pop if p.rank == 0]
        return GenerationStats(
            generation=gen, population=len(pop), feasible=len(feas), pareto_size=len(front),
            best_pass_rate=max((p.result.metrics["pass_rate"] for p in pop), default=0.0),
            best_feasible_pass_rate=max((p.result.metrics["pass_rate"] for p in feas), default=0.0),
            mean_pass_rate=sum(p.result.metrics["pass_rate"] for p in pop) / max(1, len(pop)),
            architectures=arch, train_pool_size=pool_size, adversarial_added=adv,
            spend_usd=self.syn.provider.meter.usd, elapsed_s=round(elapsed, 2), rejected=dict(self.rejections),
            epsilon=round(self.eps, 5), restarts=len(self.restarts),
            front=[{"id": p.ir.id, "label": p.ir.label, "feasible": p.result.feasible,
                    **{k: round(p.result.metrics[k], 5) for k in ("pass_rate", "robustness", "cost_per_item_usd",
                                                                   "latency_ms_p95", "complexity")}}
                   for p in front])
