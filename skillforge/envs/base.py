"""TaskEnvironment — the *only* thing a domain contributes in SkillForge v2.

An environment provides:
  * a public, natural-language specification (what the generator is allowed to read),
  * a few public input/output format examples,
  * a seeded instance generator,
  * a HIDDEN reference and checker (never shown to the generator; never imported by synthesis code).

It must NOT contain candidate solutions, solution algorithms, building blocks or mutation operators.
`skillforge.envs.policy.assert_solution_free` enforces the interface surface at load time.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import random
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Family:
    name: str
    weight: float
    description: str
    realism: str = "synthetic"
    audit_only: bool = False  # scale/distribution shift measured only on the sealed audit set


@dataclass
class Instance:
    id: str
    family: str
    input: dict[str, Any]          # what the candidate receives
    hidden: dict[str, Any]         # reference data — checker only
    tags: list[str] = field(default_factory=list)

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "family": self.family, "input": self.input}


@dataclass
class CheckResult:
    valid: bool                    # output satisfies every hard constraint of the task
    score: float                   # in [0, 1]
    violations: list[str]          # hard-constraint violations (empty if valid)
    message: str                   # human-readable diagnosis (may be shown for DEV instances only)
    extra: dict[str, Any] = field(default_factory=dict)


class TaskEnvironment:
    name: str = "base"
    version: str = "1"

    # -------- public surface (may be shown to the generator)
    def public_spec(self) -> str:
        raise NotImplementedError

    def public_examples(self) -> list[dict[str, Any]]:
        return []

    # -------- world
    def families(self) -> list[Family]:
        raise NotImplementedError

    def generate(self, family: str, rng: random.Random, instance_id: str) -> Instance:
        raise NotImplementedError

    # -------- hidden evaluation
    def check(self, instance: Instance, output: Any) -> CheckResult:
        raise NotImplementedError

    # -------- provenance
    def source_hash(self) -> str:
        """sha256 of the environment's source file (spec, generator and checker) — frozen in the prereg."""
        import sys
        path = inspect.getsourcefile(sys.modules[type(self).__module__])
        return hashlib.sha256(open(path, "rb").read()).hexdigest()


def derive_seed(*parts: Any) -> int:
    return int.from_bytes(hashlib.sha256(repr(parts).encode()).digest()[:8], "big")


def build_split(env: TaskEnvironment, split: str, size: int, master_seed: int,
                include_audit_only: bool = False) -> list[Instance]:
    fams = [f for f in env.families() if include_audit_only or not f.audit_only]
    rng = random.Random(derive_seed(master_seed, env.name, split, "plan"))
    plan = [f.name for f in fams for _ in range(2)]
    while len(plan) < size:
        plan.append(rng.choices([f.name for f in fams], weights=[f.weight for f in fams])[0])
    rng.shuffle(plan)
    return [env.generate(fam, random.Random(derive_seed(master_seed, env.name, split, i)), f"{split}-{i:04d}")
            for i, fam in enumerate(plan[:max(size, len(plan))])]


def split_hash(instances: list[Instance]) -> str:
    blob = json.dumps([[i.id, i.family, i.input, i.hidden] for i in instances], sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


FORBIDDEN_ENV_ATTRS = ("seed_candidates", "mutation_operators", "suggest_operators", "tool_library",
                       "register_simulated_models", "solve", "solution", "baseline_solver")


def assert_solution_free(env: TaskEnvironment) -> None:
    """Environments may not ship solutions or search operators."""
    bad = [a for a in FORBIDDEN_ENV_ATTRS if hasattr(env, a)]
    if bad:
        raise TypeError(f"environment {env.name} exposes solution/search surface: {bad}")
