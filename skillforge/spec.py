"""SkillSpec — the machine-readable specification produced by the DomainCompiler.

Every downstream component (world generation, evaluation, selection, export)
reads *only* this object, so it is the single source of truth for a run.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Direction(StrEnum):
    MAXIMIZE = "maximize"
    MINIMIZE = "minimize"


class FieldSpec(BaseModel):
    name: str
    type: Literal["string", "number", "integer", "boolean", "date", "enum", "object", "array", "any"]
    required: bool = True
    nullable: bool = False
    description: str = ""
    enum_values: list[str] | None = None


class Invariant(BaseModel):
    """A property that must hold for every output.

    ``hard`` invariants are feasibility constraints (a single violation on the
    evaluation set makes the candidate infeasible); soft invariants are scored.
    ``check`` names a checker registered by the domain plugin — checks are code,
    never LLM judgments.
    """

    id: str
    description: str
    check: str
    hard: bool = True


class Metric(BaseModel):
    id: str
    description: str
    direction: Direction
    unit: str = ""
    # How the metric is grounded; used to bound claims in the report.
    evidence: Literal[
        "formal", "deterministic", "ground_truth", "property", "metamorphic",
        "differential", "adversarial", "statistical", "llm_judge", "measured", "static",
    ] = "ground_truth"


class Objective(BaseModel):
    metric: str
    direction: Direction
    weight_hint: float = 1.0  # informational only — selection is Pareto-based


class Budget(BaseModel):
    """Resource limits. ``None`` means unlimited (the default): the search runs until a skill
    is certified against the target or the user stops it."""

    max_usd: float | None = None
    max_generations: int | None = None
    population_size: int = 14
    max_wall_seconds: float | None = None
    max_llm_calls: int | None = None


class Target(BaseModel):
    """Operational definition of the "ideal" skill the open-ended search is driven towards.

    Perfection cannot be proven on a finite sample; a skill is *certified* when, on a sealed
    holdout evaluated exactly once: zero hard-invariant violations, every constraint met, and
    the lower 95% confidence bound of the pass rate >= ``min_pass_lower_bound``.
    The same criteria (on validation) gate which candidates may attempt certification.
    """

    min_pass_lower_bound: float = 0.95
    zero_hard_violations: bool = True
    require_constraints: bool = True


class Constraint(BaseModel):
    id: str
    description: str
    kind: Literal["latency_ms_p95", "cost_per_item_usd", "no_network", "no_llm", "max_complexity", "min_metric",
                  "custom"]
    value: float | str | None = None
    metric: str | None = None  # for min_metric


class AcceptanceCriterion(BaseModel):
    id: str
    description: str
    metric: str
    op: Literal[">=", "<=", "=="]
    threshold: float
    # Acceptance is judged on the lower (for >=) / upper (for <=) CI bound,
    # not the point estimate — we do not claim more than the data supports.
    use_confidence_bound: bool = True


class Risk(BaseModel):
    id: str
    description: str
    mitigation: str = ""


class SkillSpec(BaseModel):
    name: str
    domain: str
    summary: str
    objectives: list[Objective]
    inputs: list[FieldSpec]
    outputs: list[FieldSpec]
    constraints: list[Constraint] = Field(default_factory=list)
    invariants: list[Invariant] = Field(default_factory=list)
    metrics: list[Metric] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    risks: list[Risk] = Field(default_factory=list)
    budget: Budget = Field(default_factory=Budget)
    target: Target = Field(default_factory=Target)
    acceptance_criteria: list[AcceptanceCriterion] = Field(default_factory=list)
    # Honest statement of what *cannot* be verified objectively in this domain.
    verification_limits: list[str] = Field(default_factory=list)
    domain_plugin: str = ""
    raw_request: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)

    def metric(self, metric_id: str) -> Metric | None:
        return next((m for m in self.metrics if m.id == metric_id), None)
