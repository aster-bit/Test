"""Skill IR — typed intermediate representation of a candidate architecture.

A candidate is a tree of components operating on a mutable ``state`` dict:

* ``code``   – Python source defining ``step(state, params) -> state``. Always
               executed inside the ExecutionSandbox, never in the host process.
* ``llm``    – a prompt template executed host-side through the provider
               abstraction (so API keys never enter the sandbox). May expose
               sandboxed tools, which turns it into a tool-using agent step.
* ``branch`` – routes each item to ``then``/``otherwise`` based on a state key
               (state machines and hybrid "rules first, LLM fallback" designs).

The architecture class is *derived* from the structure, so mutations that
change structure automatically move a candidate between classes.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from enum import StrEnum
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field


class ArchitectureClass(StrEnum):
    PROMPT_ONLY = "prompt_only"
    PROMPT_WITH_SCRIPTS = "prompt_with_scripts"
    DETERMINISTIC = "deterministic_algorithm"
    RULE_BASED = "rule_based"
    MULTI_STEP_LLM = "multi_step_llm_workflow"
    HYBRID = "hybrid_llm_code"
    STATE_MACHINE = "state_machine"
    TOOL_AGENT = "tool_using_agent"
    COMPOSITE = "composite"


class CodeComponent(BaseModel):
    kind: Literal["code"] = "code"
    name: str
    source: str
    params: dict[str, Any] = Field(default_factory=dict)
    # "rules" components are code generated from a declarative rule table;
    # they are tagged so architecture classification can see them.
    role: Literal["code", "rules", "validator", "repair", "glue"] = "code"
    origin: str = "library"  # library | llm_generated | mutated


class LLMComponent(BaseModel):
    kind: Literal["llm"] = "llm"
    name: str
    system: str
    prompt_template: str  # uses {input_text}, {state_json}
    output_key: str = "llm_output"
    model: str = "claude-sonnet-5-5"
    max_tokens: int = 2048
    effort: Literal["low", "medium", "high"] = "low"
    few_shot: list[dict[str, Any]] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)  # names of sandboxed domain tools
    max_tool_turns: int = 3


class BranchComponent(BaseModel):
    kind: Literal["branch"] = "branch"
    name: str
    condition_key: str  # truthy state[condition_key] -> then
    then: list["Component"] = Field(default_factory=list)
    otherwise: list["Component"] = Field(default_factory=list)


Component = Annotated[Union[CodeComponent, LLMComponent, BranchComponent], Field(discriminator="kind")]
BranchComponent.model_rebuild()


class SkillIR(BaseModel):
    id: str = Field(default_factory=lambda: "cand-" + uuid.uuid4().hex[:10])
    parents: list[str] = Field(default_factory=list)
    generation: int = 0
    label: str = ""
    components: list[Component]
    mutation_log: list[str] = Field(default_factory=list)
    origin: str = "seed"

    # ---------- structure helpers ----------
    def walk(self) -> list[CodeComponent | LLMComponent | BranchComponent]:
        out: list = []

        def rec(comps):
            for c in comps:
                out.append(c)
                if isinstance(c, BranchComponent):
                    rec(c.then)
                    rec(c.otherwise)

        rec(self.components)
        return out

    def llm_components(self) -> list[LLMComponent]:
        return [c for c in self.walk() if isinstance(c, LLMComponent)]

    def code_components(self) -> list[CodeComponent]:
        return [c for c in self.walk() if isinstance(c, CodeComponent)]

    def architecture(self) -> ArchitectureClass:
        comps = self.walk()
        llms = [c for c in comps if isinstance(c, LLMComponent)]
        codes = [c for c in comps if isinstance(c, CodeComponent)]
        branches = [c for c in comps if isinstance(c, BranchComponent)]
        if any(c.tools for c in llms):
            return ArchitectureClass.TOOL_AGENT
        if not llms:
            if branches:
                return ArchitectureClass.STATE_MACHINE
            if sum(1 for c in codes if c.role == "rules") >= 2:
                return ArchitectureClass.RULE_BASED
            return ArchitectureClass.DETERMINISTIC
        if branches and codes:
            return ArchitectureClass.STATE_MACHINE if len(branches) > 1 else ArchitectureClass.HYBRID
        substantive = [c for c in codes if c.role != "glue"]
        if len(llms) > 1:
            return ArchitectureClass.MULTI_STEP_LLM if len(substantive) <= 1 else ArchitectureClass.COMPOSITE
        if len(substantive) == 0:
            return ArchitectureClass.PROMPT_ONLY
        if len(substantive) >= 3:
            return ArchitectureClass.HYBRID
        return ArchitectureClass.PROMPT_WITH_SCRIPTS

    def complexity(self) -> float:
        """Static complexity: components + source lines + prompt length (scaled)."""
        score = 0.0
        for c in self.walk():
            score += 1.0
            if isinstance(c, CodeComponent):
                score += len([l for l in c.source.splitlines() if l.strip()]) / 25.0
            elif isinstance(c, LLMComponent):
                score += (len(c.system) + len(c.prompt_template)) / 800.0 + len(c.few_shot) * 0.3 + len(c.tools) * 0.5
        return round(score, 3)

    def fingerprint(self) -> str:
        payload = json.dumps([c.model_dump() for c in self.components], sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def prompts(self) -> list[dict[str, str]]:
        return [{"name": c.name, "system": c.system, "template": c.prompt_template} for c in self.llm_components()]
