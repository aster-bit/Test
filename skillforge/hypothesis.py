"""Hypothesis-Driven Program Synthesis: the unit of search is an algorithmic hypothesis, not a program.

A Hypothesis (the "Hypothesis IR") is a falsifiable statement about HOW the task should be computed:
its core mechanism, the assumptions it relies on, its resource profile, where it should win and lose,
and how to test it. Programs are *implementations* of hypotheses; one hypothesis may have several
independent implementations, which is what lets the evaluator separate "the idea is weak" from "this
implementation is weak".

Everything the model says about a hypothesis (names, expected strengths, diagnoses) is stored as a claim.
What counts as evidence is measured elsewhere (novelty.py, hexperiment.py): code structure, per-instance
behaviour, paired score differences between implementations.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from .open_synth import Candidate, OpenSynthesizer

CAUSES = ("IMPLEMENTATION", "PARAMETERS", "MECHANISM_LIMIT", "TASK_ASSUMPTION", "INSUFFICIENT_INFORMATION")


@dataclass
class Hypothesis:
    id: str
    name: str
    mechanism: str                      # the core computational mechanism, one sentence
    principle: str                      # how it works and why it should solve the task
    assumptions: list[str] = field(default_factory=list)      # what must be true about instances
    resources: str = ""                 # expected time / memory behaviour under the task limits
    constraints: list[str] = field(default_factory=list)      # when it cannot be used
    advantages: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    strong_families: list[str] = field(default_factory=list)  # falsifiable prediction: relatively best here
    weak_families: list[str] = field(default_factory=list)    # falsifiable prediction: relatively worst here
    test: str = ""                      # how to check the hypothesis experimentally
    origin: str = "initial"             # initial | pivot | recombine
    parents: list[str] = field(default_factory=list)
    created_index: int = 0

    def record(self) -> dict[str, Any]:
        return asdict(self)

    def brief(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if k not in ("origin", "parents", "created_index")}


FIELDS_DOC = """Each hypothesis is a JSON object with exactly these keys:
  "name": short label,
  "mechanism": ONE sentence naming the core computational mechanism (what the main loop / data structure does),
  "principle": 2-4 sentences: how it works and why it should score well on this task,
  "assumptions": [what must be true about the instances for it to work],
  "resources": how its running time / memory behave under the task limits,
  "constraints": [conditions under which it cannot be applied],
  "advantages": [expected advantages over the alternatives],
  "weaknesses": [where it is expected to lose score],
  "strong_families": [instance families (names from the list below) where it should do relatively BEST],
  "weak_families": [instance families where it should do relatively WORST],
  "test": how an experiment could show the hypothesis is wrong."""


class HypothesisParseError(ValueError):
    pass


def _from_dict(d: dict[str, Any], hid: str, origin: str, parents: list[str], index: int) -> Hypothesis:
    def lst(k):
        v = d.get(k, [])
        return [str(x)[:400] for x in v][:8] if isinstance(v, list) else [str(v)[:400]]
    if not isinstance(d, dict) or not d.get("mechanism"):
        raise HypothesisParseError("hypothesis needs at least a 'mechanism'")
    return Hypothesis(hid, str(d.get("name", hid))[:80], str(d["mechanism"])[:500], str(d.get("principle", ""))[:1200],
                      lst("assumptions"), str(d.get("resources", ""))[:500], lst("constraints"), lst("advantages"),
                      lst("weaknesses"), lst("strong_families"), lst("weak_families"), str(d.get("test", ""))[:600],
                      origin, parents, index)


def parse_json_block(text: str, tag: str = "json") -> Any:
    m = re.search(r"```" + tag + r"\s*\n(.*?)```", text, re.S)
    if not m:
        raise HypothesisParseError(f"reply has no ```{tag} block")
    try:
        return json.loads(m.group(1))
    except ValueError as e:
        raise HypothesisParseError(f"```{tag} block is not valid JSON: {e}") from e


class HypothesisSynthesizer(OpenSynthesizer):
    """Hypothesis Generator + Architecture Synthesizer. Shares call/cost accounting with OpenSynthesizer."""

    def _families(self) -> str:
        return "\n".join(f"  - {f.name}: {f.description}" for f in self.env.families() if not f.audit_only)

    def _hyp_task(self) -> str:
        return self._task_block() + "\n\nINSTANCE FAMILIES (public description of the kinds of instances)\n" + self._families()

    # ------------------------------------------------------------------ hypothesis generator
    def propose(self, n: int) -> list[Hypothesis]:
        prompt = (self._hyp_task() + f"\n\nDo NOT write any code yet. Propose {n} competing ALGORITHMIC HYPOTHESES "
                  "for this task: fundamentally different ways of organising the computation. Two hypotheses whose "
                  "implementations would share the same core loop or the same central data structure count as ONE - "
                  "differences in constants, heuristics' weights, pre/post-processing or naming are not differences. "
                  "Every hypothesis must be implementable within the solution format and limits above, and each must "
                  "be a serious contender, not a strawman.\n\n" + FIELDS_DOC +
                  "\n\nReply with one ```json block containing a list of the hypotheses and nothing else important.")
        data = parse_json_block(self._call(prompt, "hypothesize"))
        if not isinstance(data, list) or not data:
            raise HypothesisParseError("expected a JSON list of hypotheses")
        return [_from_dict(d, f"H{i}", "initial", [], i) for i, d in enumerate(data[:n])]

    # ------------------------------------------------------------------ architecture synthesizer
    def implement(self, h: Hypothesis, arm: str, index: int, note: str = "") -> Candidate:
        prompt = (self._task_block() + "\n\nALGORITHMIC HYPOTHESIS TO IMPLEMENT\n" + json.dumps(h.brief(), indent=1)
                  + "\n\nImplement THIS hypothesis as a complete, high-quality solution. Stay faithful to its "
                  "mechanism: do not replace it with a different approach, even if you believe another would score "
                  "higher - the purpose is to measure this idea at its best. Engineering inside the mechanism "
                  "(data structures, pruning, time management, input robustness) is expected."
                  + (f" ({note})" if note else ""))
        return self._make(self._call(prompt, "synthesize"), arm, f"implement:{h.id}", [], index)

    def refine(self, h: Hypothesis, parent: Candidate, dev_feedback: str, arm: str, index: int) -> tuple[Candidate, str]:
        """Improve an implementation while keeping its hypothesis. Returns the child and the model's CAUSE label
        (a conjecture about why score was lost - verified later only by measurement)."""
        prompt = (self._task_block() + "\n\nALGORITHMIC HYPOTHESIS\n" + json.dumps(h.brief(), indent=1)
                  + "\n\nCURRENT IMPLEMENTATION\n" + self._program_block(parent)
                  + "\n\nRESULTS ON DEVELOPMENT INSTANCES\n" + dev_feedback
                  + "\n\n1. Under 'DIAGNOSIS:' state in 2-5 sentences the main cause of the lost score.\n"
                  "2. Under 'CAUSE:' write exactly one of " + ", ".join(CAUSES) + " - is the loss due to a defect of "
                  "this implementation, to its parameters, to an inherent limit of the mechanism, to a wrong "
                  "assumption about the task, or can it not be decided from the available information?\n"
                  "3. Under 'CHANGE:' say what you change.\n"
                  "4. Give 'STRATEGY:' and the complete improved program in the required format. Keep the "
                  "hypothesis' mechanism as the core; improve how it is realised.")
        text = self._call(prompt, "synthesize")
        c = self._make(text, arm, f"refine:{h.id}", [parent.id], index)
        m = re.search(r"CAUSE:\s*\**\s*([A-Z_]+)", text)
        cause = m.group(1) if m and m.group(1) in CAUSES else "UNSTATED"
        d = re.search(r"DIAGNOSIS:\s*(.*?)(?:\n\s*\n|CAUSE:)", text, re.S)
        c.change = ((("DIAGNOSIS: " + d.group(1).strip() + " ") if d else "") + c.change)[:2500]
        return c, cause

    # ------------------------------------------------------------------ cross-paradigm moves (hypothesis first, then code)
    def _hyp_and_program(self, prompt: str, hid: str, origin: str, parents: list[str], cand_parents: list[str],
                         arm: str, index: int) -> tuple[Hypothesis | None, Candidate]:
        text = self._call(prompt, "synthesize_h")
        c = self._make(text, arm, f"{origin}:{hid}", cand_parents, index)
        try:
            h = _from_dict(parse_json_block(text, "hypothesis"), hid, origin, parents, index)
        except HypothesisParseError as e:
            h = None
            c.parse_error = c.parse_error or f"hypothesis block: {e}"
            c.ir = None
        return h, c

    def pivot(self, evidence: list[dict[str, Any]], hid: str, arm: str, index: int) -> tuple[Hypothesis | None, Candidate]:
        prompt = (self._hyp_task() + "\n\nEVIDENCE SO FAR - every algorithmic hypothesis tested on this task, with "
                  "measured development results of its implementations:\n" + json.dumps(evidence, indent=1)[:14000]
                  + "\n\nProgress has stalled. Under 'ANALYSIS:' explain in 3-6 sentences which shared way of thinking "
                  "about the task limits ALL of these hypotheses (not which constants are off). Then state a NEW "
                  "hypothesis whose core mechanism differs from every one above, in a ```hypothesis block containing "
                  "one JSON object.\n" + FIELDS_DOC + "\nThen implement it completely: 'STRATEGY:' followed by the "
                  "```pipeline and ```python blocks.")
        return self._hyp_and_program(prompt, hid, "pivot", [e["id"] for e in evidence], [], arm, index)

    def recombine_h(self, ha: Hypothesis, ca: Candidate, hb: Hypothesis, cb: Candidate, evidence: list[dict[str, Any]],
                    hid: str, arm: str, index: int) -> tuple[Hypothesis | None, Candidate]:
        prompt = (self._hyp_task() + "\n\nTWO TESTED HYPOTHESES\nHYPOTHESIS X\n" + json.dumps(ha.brief(), indent=1)
                  + "\nBEST IMPLEMENTATION OF X\n" + self._program_block(ca)
                  + "\n\nHYPOTHESIS Y\n" + json.dumps(hb.brief(), indent=1)
                  + "\nBEST IMPLEMENTATION OF Y\n" + self._program_block(cb)
                  + "\n\nMEASURED DEVELOPMENT RESULTS\n" + json.dumps(evidence, indent=1)[:8000]
                  + "\n\nDesign a NEW hypothesis that combines the PRINCIPLES of X and Y into one mechanism in which "
                  "each compensates the measured weakness of the other. Merely running both and picking one output "
                  "is allowed only if you can justify from the measurements that they are complementary per "
                  "instance. Under 'ANALYSIS:' say what each contributes. State the new hypothesis in a "
                  "```hypothesis block containing one JSON object.\n" + FIELDS_DOC + "\nThen implement it completely: "
                  "'STRATEGY:' followed by the ```pipeline and ```python blocks.")
        return self._hyp_and_program(prompt, hid, "recombine", [ha.id, hb.id], [ca.id, cb.id], arm, index)
