"""CandidateSynthesizer — produces architecturally diverse candidates and variations.

Sources of candidates:
* domain seeds (one per architecture class the domain supports);
* domain-aware mutation operators (params, prompts, structure, architecture jumps);
* generic crossover (aligned-by-name parameter crossover or one-point structural);
* LLM-proposed code patches (real provider only) — the model sees *train*
  failures only, its code is statically validated and only ever runs in the sandbox.
"""

from __future__ import annotations

import json
import random
import re
from typing import Any

from .domains.base import DomainPlugin
from .ir import BranchComponent, CodeComponent, SkillIR
from .llm import LLMProvider, LLMRequest
from .sandbox import validate_source
from .spec import SkillSpec


class CandidateSynthesizer:
    def __init__(self, spec: SkillSpec, plugin: DomainPlugin, provider: LLMProvider, rng: random.Random):
        self.spec = spec
        self.plugin = plugin
        self.provider = provider
        self.rng = rng
        self.ops = plugin.mutation_operators()
        self.op_stats: dict[str, dict[str, int]] = {name: {"applied": 0, "improved": 0} for name, _ in self.ops}
        self.llm_code_proposals = {"attempted": 0, "accepted": 0, "rejected_static": 0}
        self.directed = 0

    # ------------------------------------------------------------------
    def initial_population(self, size: int) -> list[SkillIR]:
        seeds = self.plugin.seed_candidates(self.spec)
        pop: list[SkillIR] = []
        seen: set[str] = set()
        for s in seeds:
            if s.fingerprint() not in seen:
                pop.append(s)
                seen.add(s.fingerprint())
        attempts = 0
        while len(pop) < size and attempts < size * 20:
            attempts += 1
            parent = self.rng.choice(seeds)
            child = self.mutate(parent, generation=0, n_mut=self.rng.randint(1, 3))
            if child and child.fingerprint() not in seen:
                child.origin = "seed_variant"
                pop.append(child)
                seen.add(child.fingerprint())
        return pop[:max(size, len(seeds))]

    def _pick_op(self, hints: dict[str, float] | None = None):
        """Operator choice = 50% failure-directed (domain hints from the parent's train failures),
        50% adaptive: P(op) ∝ (improved + 1) / (applied + 2) (Laplace-smoothed success rate)."""
        if hints and self.rng.random() < 0.5:
            names = [n for n, _ in self.ops if hints.get(n)]
            if names:
                pick = self.rng.choices(names, weights=[hints[n] for n in names])[0]
                self.directed += 1
                return next(o for o in self.ops if o[0] == pick)
        weights = [(self.op_stats[n]["improved"] + 1) / (self.op_stats[n]["applied"] + 2) for n, _ in self.ops]
        return self.rng.choices(self.ops, weights=weights)[0]

    def mutate(self, parent: SkillIR, generation: int, n_mut: int = 1,
               failures: list[dict[str, Any]] | None = None) -> SkillIR | None:
        hints = self.plugin.suggest_operators(failures) if failures else None
        cur = parent
        log: list[str] = []
        applied: list[str] = []
        for _ in range(n_mut):
            for _try in range(12):
                name, op = self._pick_op(hints)
                res = op(cur, self.rng)
                if res is None:
                    continue
                cur, desc = res
                log.append(f"{name}: {desc}")
                applied.append(name)
                break
        if not log:
            return None
        child = SkillIR(parents=[parent.id], generation=generation, label=parent.label, components=cur.components,
                        mutation_log=log, origin="mutation")
        child.label = self._label(parent.label, child)
        for a in applied:
            self.op_stats[a]["applied"] += 1
        return child

    def crossover(self, a: SkillIR, b: SkillIR, generation: int) -> SkillIR | None:
        a_names = [c.name for c in a.components]
        b_names = [c.name for c in b.components]
        if a_names == b_names:
            comps = []
            for ca, cb in zip(a.components, b.components):
                pick = ca if self.rng.random() < 0.5 else cb
                if isinstance(ca, CodeComponent) and isinstance(cb, CodeComponent) and ca.source == cb.source:
                    params = {}
                    for k in set(ca.params) | set(cb.params):
                        src = ca.params if (self.rng.random() < 0.5 and k in ca.params) or k not in cb.params else cb.params
                        params[k] = src[k]
                    pick = ca.model_copy(deep=True, update={"params": params})
                comps.append(pick.model_copy(deep=True))
            kind = "uniform"
        else:
            # one-point crossover on the shared canonical prefix (keeps first/last stage sane)
            shared = [n for n in a_names if n in b_names]
            if len(shared) < 2:
                return None
            cut_name = self.rng.choice(shared[1:])
            ia, ib = a_names.index(cut_name), b_names.index(cut_name)
            comps = [c.model_copy(deep=True) for c in a.components[:ia]] + \
                    [c.model_copy(deep=True) for c in b.components[ib:]]
            names = [c.name for c in comps]
            if len(set(names)) != len(names):
                return None
            kind = f"one-point@{cut_name}"
        child = SkillIR(parents=[a.id, b.id], generation=generation, components=comps,
                        mutation_log=[f"crossover({kind})"], origin="crossover")
        child.label = self._label(a.label, child)
        return child

    @staticmethod
    def _label(base: str, ir: SkillIR) -> str:
        base = re.sub(r"\s*\[.*\]$", "", base or "candidate")
        return f"{base} [{ir.architecture().value}]"

    # ------------------------------------------------------------------
    def llm_code_patch(self, ir: SkillIR, failures: list[dict[str, Any]], generation: int) -> SkillIR | None:
        """Ask a real model to rewrite one code component. No-op on simulated providers."""
        if getattr(self.provider, "simulated", True):
            return None
        codes = [c for c in ir.walk() if isinstance(c, CodeComponent) and c.role not in ("glue",)]
        if not codes:
            return None
        target = self.rng.choice(codes)
        self.llm_code_proposals["attempted"] += 1
        prompt = (
            f"Skill specification (JSON):\n{self.spec.model_dump_json(include={'summary', 'outputs', 'invariants'})}\n\n"
            f"Component `{target.name}` currently has this source:\n```python\n{target.source}\n```\n"
            f"and params: {json.dumps(target.params)[:2000]}\n\n"
            f"Training failures (inputs truncated):\n{json.dumps(failures[:6], default=str)[:6000]}\n\n"
            "Write an improved replacement. Constraints: define `def step(state, params):` returning the state "
            "dict; only import re, json, math, datetime, decimal, unicodedata, itertools, collections, string, "
            "functools; no file, network, eval/exec, getattr or dunder access. Reply with a single ```python block."
        )
        resp = self.provider.complete(LLMRequest(
            system="You are a careful Python engineer improving one stage of a data-processing pipeline.",
            messages=[{"role": "user", "content": prompt}], max_tokens=8000, effort="medium", purpose="synthesize"))
        m = re.search(r"```python\n(.*?)```", resp.text, re.S)
        if not m:
            return None
        source = m.group(1)
        if validate_source(source):
            self.llm_code_proposals["rejected_static"] += 1
            return None
        new = ir.model_copy(deep=True)

        def rec(comps):
            for c in comps:
                if isinstance(c, CodeComponent) and c.name == target.name:
                    c.source, c.origin = source, "llm_generated"
                elif isinstance(c, BranchComponent):
                    rec(c.then)
                    rec(c.otherwise)

        rec(new.components)
        self.llm_code_proposals["accepted"] += 1
        return SkillIR(parents=[ir.id], generation=generation, components=new.components, label=ir.label,
                       mutation_log=[f"llm_code_patch: {target.name}"], origin="llm_patch")
