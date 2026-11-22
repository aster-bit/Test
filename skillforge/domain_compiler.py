"""DomainCompiler — natural-language task -> validated SkillSpec.

Two layers:
1. Deterministic extraction of explicit numbers (budget, latency, cost per item,
   generations, population, "no LLM") from the request (RU/EN).
2. Optional LLM analysis (real provider only — Claude Code ``claude -p --json-schema``
   by default) validated against a Pydantic model; it may only fill constraints the
   deterministic parser missed, never invent ground truth.

The spec is then built by the matching domain plugin, which owns the objective
checks. If no plugin matches, compilation fails loudly instead of producing an
optimizer that could only be graded by an LLM judge.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from .domains import match_plugin
from .domains.base import DomainPlugin
from .llm import LLMProvider
from .spec import SkillSpec


class UnsupportedDomain(ValueError):
    pass


class RequestAnalysis(BaseModel):
    domain_keywords: list[str] = Field(default_factory=list)
    budget_usd: float | None = None
    latency_ms_p95: float | None = None
    cost_per_item_usd: float | None = None
    no_llm: bool = False
    extra_constraints: list[str] = Field(default_factory=list)


_NUM = r"(\d+(?:[.,]\d+)?)"


def _f(s: str) -> float:
    return float(s.replace(",", "."))


def parse_request(text: str) -> dict[str, Any]:
    t = text.lower()
    out: dict[str, Any] = {"budget": {}}
    # budget only from an explicit keyword; "unlimited/без ограничений" or no mention => unlimited
    m = re.search(r"(?:budget|бюджет\w*)[^\d$€]{0,20}[$€]?\s*" + _NUM + r"\s*(?:\$|usd|долл|dollars?)?", t)
    if m and not re.search(r"(?:budget|бюджет\w*)\W{0,3}(?:unlimited|неогранич|без ограничен)", t):
        out["budget"]["max_usd"] = _f(m.group(1))
    m = re.search(r"(?:latency|задержк\w*|p95)[^\d]{0,25}" + _NUM + r"\s*(ms|мс|s|с|сек\w*)\b", t)
    if m:
        v = _f(m.group(1))
        out["latency_ms_p95"] = v if m.group(2) in ("ms", "мс") else v * 1000
    m = re.search(r"[$]?\s*" + _NUM + r"\s*\$?\s*(?:usd\s*)?(?:per|за|на)\s+(?:item|document|invoice|документ|счет|счёт)", t)
    if m:
        out["cost_per_item_usd"] = _f(m.group(1))
    m = re.search(r"(\d+)\s*(?:generations|поколени\w*)", t)
    if m:
        out["budget"]["max_generations"] = int(m.group(1))
    m = re.search(r"(?:population|популяци\w*)\D{0,10}(\d+)", t)
    if m:
        out["budget"]["population_size"] = int(m.group(1))
    m = re.search(r"(?:target|цел\w*|точност\w*|надёжност\w*|надежност\w*|pass)[^\d%]{0,20}(\d{2}(?:[.,]\d+)?)\s*%", t)
    if m:
        out["target_pass"] = min(0.999, _f(m.group(1)) / 100)
    if re.search(r"(without|no|без)\s+(an?\s+)?(llm|ллм|ai model|модел\w*)", t):
        out["no_llm"] = True
    return out


class DomainCompiler:
    def __init__(self, provider: LLMProvider | None = None):
        self.provider = provider

    def _llm_analysis(self, request: str) -> RequestAnalysis | None:
        if self.provider is None or getattr(self.provider, "simulated", True):
            return None
        fn = getattr(self.provider, "complete_structured", None)
        if fn is None:
            return None
        out = fn("Extract explicit constraints from a request to build an automated skill. Only report values "
                 "the user actually stated; leave others null.", request, RequestAnalysis.model_json_schema(),
                 model="claude-opus-5-5")
        return RequestAnalysis.model_validate(out) if out else None

    def compile(self, request: str, plugin_name: str | None = None) -> tuple[SkillSpec, DomainPlugin]:
        parsed = parse_request(request)
        analysis = None
        try:
            analysis = self._llm_analysis(request)
        except Exception as e:  # noqa: BLE001 - analysis is optional; deterministic parse still applies
            parsed.setdefault("notes", []).append(f"LLM analysis failed: {type(e).__name__}")
        if analysis is not None:
            if analysis.budget_usd and "max_usd" not in parsed["budget"]:
                parsed["budget"]["max_usd"] = analysis.budget_usd
            for k in ("latency_ms_p95", "cost_per_item_usd"):
                v = getattr(analysis, k)
                if v is not None and k not in parsed:
                    parsed[k] = v
            parsed["no_llm"] = parsed.get("no_llm", False) or analysis.no_llm
        if plugin_name:
            from .domains import get_plugin
            plugin = get_plugin(plugin_name)
        else:
            plugin, score = match_plugin(request + " " + " ".join(analysis.domain_keywords if analysis else []))
            if plugin is None:
                raise UnsupportedDomain(
                    "No domain plugin with objective evaluation matches this request. SkillForge will not optimize "
                    "a skill that could only be graded by an LLM judge; add a DomainPlugin (world generator + "
                    "ground truth + invariant checkers) first.")
        spec = plugin.build_spec(request, parsed)
        if parsed.get("notes"):
            spec.extra["compiler_notes"] = parsed["notes"]
        spec.extra["parsed_request"] = {k: v for k, v in parsed.items() if k != "notes"}
        return spec, plugin
