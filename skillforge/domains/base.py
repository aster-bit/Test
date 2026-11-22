"""Domain plugin interface.

A domain plugin supplies everything that must be *objective* about a domain:
scenario generators with ground truth, invariant checkers, a scorer,
metamorphic relations, perturbation operators for the adversary and a library
of building blocks for the synthesizer. The core engine is domain-agnostic.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Callable

from ..ir import SkillIR
from ..llm import MockProvider
from ..spec import SkillSpec


@dataclass
class Scenario:
    id: str
    family: str
    input: dict[str, Any]
    expected: dict[str, Any] | None  # None => no ground truth available (differential only)
    realism: str  # "realistic" | "synthetic" | "adversarial"
    seed: int
    tags: list[str] = field(default_factory=list)
    origin: str = "world"  # world | adversary | metamorphic

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "family": self.family, "input": self.input, "expected": self.expected,
                "realism": self.realism, "seed": self.seed, "tags": self.tags, "origin": self.origin}


@dataclass
class FamilySpec:
    name: str
    realism: str
    description: str
    category: str  # normal | edge | erroneous | missing | contradictory | dependency_failure |
    #                resource | concurrency | sequence | injection | drift
    weight: float = 1.0
    holdout_only: bool = False  # used to measure distribution shift honestly


@dataclass
class InvariantResult:
    ok: bool
    message: str = ""


@dataclass
class MetamorphicRelation:
    id: str
    description: str
    transform: Callable[[dict[str, Any], random.Random], dict[str, Any]]
    # relation(original_output, transformed_output) -> bool
    relation: Callable[[dict[str, Any], dict[str, Any]], bool]
    applies_to: Callable[[Scenario], bool] = lambda s: True


@dataclass
class Perturbation:
    id: str
    description: str
    # (scenario, rng) -> new scenario with *recomputed* ground truth, or None if n/a
    apply: Callable[[Scenario, random.Random], Scenario | None]


class DomainPlugin:
    name: str = "base"
    keywords: tuple[str, ...] = ()

    # --- specification ---
    def build_spec(self, request: str, parsed: dict[str, Any]) -> SkillSpec:  # pragma: no cover - abstract
        raise NotImplementedError

    # --- world ---
    def families(self) -> list[FamilySpec]:
        raise NotImplementedError

    def generate(self, family: str, rng: random.Random, scenario_id: str, seed: int) -> Scenario:
        raise NotImplementedError

    def perturbations(self) -> list[Perturbation]:
        return []

    def metamorphic_relations(self) -> list[MetamorphicRelation]:
        return []

    # --- evaluation ---
    def invariant_checkers(self) -> dict[str, Callable[[Scenario, dict[str, Any] | None], InvariantResult]]:
        raise NotImplementedError

    def score(self, scenario: Scenario, output: dict[str, Any] | None) -> dict[str, float]:
        """Return per-scenario metric values in [0,1] incl. key 'pass' (0/1)."""
        raise NotImplementedError

    # --- synthesis ---
    def seed_candidates(self, spec: SkillSpec) -> list[SkillIR]:
        raise NotImplementedError

    def mutation_operators(self) -> list[tuple[str, Callable[[SkillIR, random.Random], SkillIR | None]]]:
        return []

    def suggest_operators(self, failures: list[dict[str, Any]]) -> dict[str, float]:
        """Map observed train failures to mutation-operator weights (failure-directed mutation)."""
        return {}

    def tool_library(self) -> dict[str, dict[str, Any]]:
        """name -> {"description", "input_schema", "source"} (source runs in sandbox)."""
        return {}

    def register_simulated_models(self, provider: MockProvider) -> None:
        pass

    def render_skill_instructions(self, spec: SkillSpec, ir: SkillIR) -> str:
        return ""
