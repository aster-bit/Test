"""Open-ended synthesis (SkillForge v2): no seeds, no building blocks, no domain operators.

The generator (Claude, via Claude Code `claude -p`) receives ONLY:
  * the environment's public specification and format examples,
  * the execution-graph contract below,
  * for variation: the parent program and diagnoses on DEV instances (never selection/audit data,
    never the checker or reference solutions).

Three generative moves:
  generate()  — synthesis from scratch;
  mutate()    — a typed STRUCTURAL operator applied by the model to the whole program;
  expand()    — triggered by stagnation: failure analysis of everything tried so far, then a new solution
                paradigm that must differ from all previous strategies.
Programs are parsed into the generic SkillIR (code / llm / branch nodes) and run in the sandbox.
Whether a change was structural and whether a program is a new algorithm is decided by novelty.py
measurements, not by the model's self-description.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from .envs.base import TaskEnvironment
from .ir import BranchComponent, CodeComponent, LLMComponent, SkillIR
from .llm import LLMProvider, LLMRequest
from .sandbox import ALLOWED_IMPORTS, validate_source

GEN_MODEL = "claude-opus-5-5"

CONTRACT = f"""
SOLUTION FORMAT — your solution is an EXECUTION GRAPH of nodes that transform a shared dict `state`.
state["input"] holds one task instance. The graph must finally set state["output"] (a dict).

Reply with: a line "STRATEGY:" followed by 2-6 sentences naming the approach, then exactly two fenced blocks:

```pipeline
[ ...JSON list of nodes... ]
```
```python
# one Python module defining every function referenced by code nodes
```

Node kinds:
  {{"kind": "code", "fn": "<function name>"}}
      calls fn(state, params) (params is an empty dict); fn must return the state dict.
  {{"kind": "llm", "name": "<id>", "prompt": "<text>", "output_key": "<state key>", "model": "claude-haiku-5-5"}}
      the host calls a language model; in the prompt, {{input_json}} and {{state_json}} are replaced by the
      instance and the current intermediate state; write every other literal brace doubled. The raw text
      reply is stored in state[output_key]. Allowed models: claude-haiku-5-5, claude-sonnet-5-5.
      An LLM call costs money and 2-10 s latency per instance; use one only if it adds value.
  {{"kind": "branch", "condition_key": "<state key>", "then": [nodes], "otherwise": [nodes]}}
      routes on the truthiness of state[condition_key].

Python rules (checked statically and enforced at runtime; violations make the program unusable):
  * imports only from: {", ".join(sorted(ALLOWED_IMPORTS))}
  * no file / network / process access; do not use eval, exec, compile, open, getattr, setattr, delattr,
    globals, locals, vars, input, the `global` statement, or any dunder attribute or dunder string
    (so no classes with __init__; use dicts/lists/tuples and closures; `nonlocal` is fine).
  * functions of different nodes do NOT share module-level variables — pass data through `state`.
  * state must stay JSON-serialisable between nodes (dicts, lists, str, int, float, bool, None).
  * respect the per-instance time limit of the task; use time.perf_counter() budgets for any search.
"""

SYSTEM = ("You are the solution generator of an automated skill-synthesis system. You invent and implement "
          "complete, executable solution architectures for tasks you know only from their specification. "
          "Your program will be executed in a sandbox and scored by a hidden checker on hidden instances.")

STRUCTURAL_OPERATORS: dict[str, str] = {
    "change_algorithm": "Replace the core algorithm with a different algorithmic paradigm (not a re-tuning).",
    "add_improvement_phase": "Add a new stage that takes the current result and improves it with a different "
                             "technique, keeping the result valid.",
    "split_and_specialise": "Split the main function into separate graph nodes with distinct responsibilities, "
                            "then make one of them substantially smarter.",
    "merge_and_simplify": "Merge nodes / remove redundant passes so the program is simpler, without losing quality.",
    "add_verification_and_recovery": "Add a verifier node that checks the output against the specification and a "
                                     "branch that recovers differently when the check fails.",
    "add_state_or_memory": "Introduce an explicit intermediate data structure / state that later stages exploit "
                           "(e.g. precomputed indices, profiles, caches).",
    "change_topology": "Route different kinds of instances to different strategies with branch nodes, based on "
                       "features you compute.",
    "swap_llm_and_code": "If the program uses an LLM node, replace it with code; otherwise introduce an LLM node "
                         "where language-model judgement could plausibly help (e.g. planner or fallback).",
}


@dataclass
class Candidate:
    id: str
    arm: str
    origin: str                      # generate | mutate:<op> | expand | repair
    parents: list[str]
    strategy: str
    change: str
    pipeline: list[dict[str, Any]]
    module: str
    created_at: float
    generation_index: int            # order of creation within the arm
    calls_used: int                  # synthesis calls spent on this candidate (incl. repair)
    synth_cost_usd: float
    static_problems: list[str] = field(default_factory=list)
    parse_error: str | None = None
    ir: SkillIR | None = None

    def record(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("ir", None)
        return d


class ParseError(ValueError):
    pass


def parse_reply(text: str) -> tuple[str, str, list[dict[str, Any]], str]:
    m_pipe = re.search(r"```pipeline\s*\n(.*?)```", text, re.S)
    m_py = re.search(r"```python\s*\n(.*?)```", text, re.S)
    if not m_pipe or not m_py:
        raise ParseError("reply must contain a ```pipeline block and a ```python block")
    try:
        pipeline = json.loads(m_pipe.group(1))
    except ValueError as e:
        raise ParseError(f"pipeline is not valid JSON: {e}") from e
    if not isinstance(pipeline, list) or not pipeline:
        raise ParseError("pipeline must be a non-empty JSON list")
    strat = re.search(r"STRATEGY:\s*(.*?)(?:\n\s*\n|```|\n[A-Z]{4,}:)", text, re.S)
    change = re.search(r"CHANGE:\s*(.*?)(?:\n\s*\n|```|\n[A-Z]{4,}:)", text, re.S)
    return ((strat.group(1).strip() if strat else "")[:1500], (change.group(1).strip() if change else "")[:1500],
            pipeline, m_py.group(1))


def build_ir(pipeline: list[dict[str, Any]], module: str, cid: str) -> SkillIR:
    fns = set(re.findall(r"^def\s+(\w+)\s*\(", module, re.M))

    def conv(nodes, depth=0) -> list:
        if depth > 6:
            raise ParseError("branch nesting too deep")
        out = []
        for i, n in enumerate(nodes):
            k = n.get("kind")
            if k == "code":
                fn = n.get("fn")
                if fn not in fns:
                    raise ParseError(f"code node references undefined function {fn!r}")
                if fn == "step":
                    raise ParseError("do not name a function 'step'")
                src = module.rstrip() + f"\n\n\ndef step(state, params):\n    return {fn}(state, params)\n"
                out.append(CodeComponent(name=fn, source=src, params={}, origin="llm_generated"))
            elif k == "llm":
                model = n.get("model", "claude-haiku-5-5")
                if model not in ("claude-haiku-5-5", "claude-sonnet-5-5"):
                    model = "claude-haiku-5-5"
                out.append(LLMComponent(name=str(n.get("name", f"llm{i}")), system="You are one component of an automated skill. Follow the instructions exactly.",
                                        prompt_template=str(n.get("prompt", "")), output_key=str(n.get("output_key", f"llm{i}")),
                                        model=model, max_tokens=4000, effort="low"))
            elif k == "branch":
                out.append(BranchComponent(name=str(n.get("name", f"branch{i}")), condition_key=str(n.get("condition_key", "")),
                                           then=conv(n.get("then", []), depth + 1),
                                           otherwise=conv(n.get("otherwise", []), depth + 1)))
            else:
                raise ParseError(f"unknown node kind {k!r}")
        return out

    return SkillIR(id=cid, components=conv(pipeline), origin="open_synthesis", label=cid)


class OpenSynthesizer:
    def __init__(self, env: TaskEnvironment, provider: LLMProvider, model: str = GEN_MODEL, effort: str = "medium"):
        self.env = env
        self.provider = provider
        self.model = model
        self.effort = effort
        self.calls = 0
        self.cost = 0.0
        self.log: list[dict[str, Any]] = []

    # ------------------------------------------------------------------ prompting
    def _task_block(self) -> str:
        ex = json.dumps(self.env.public_examples(), indent=1)
        return f"TASK SPECIFICATION\n{self.env.public_spec()}\n\nFORMAT EXAMPLES\n{ex}\n\n{CONTRACT}"

    def _call(self, prompt: str, purpose: str) -> str:
        t0 = time.time()
        resp = self.provider.complete(LLMRequest(system=SYSTEM, messages=[{"role": "user", "content": prompt}],
                                                 model=self.model, max_tokens=32000, effort=self.effort,
                                                 purpose=purpose, mock_context={"handler": purpose}))
        self.calls += 1
        self.cost += resp.cost_usd
        self.log.append({"purpose": purpose, "seconds": round(time.time() - t0, 1), "cost_usd": resp.cost_usd,
                         "stop_reason": resp.stop_reason, "chars": len(resp.text)})
        return resp.text

    @staticmethod
    def _program_block(c: Candidate) -> str:
        return (f"STRATEGY: {c.strategy}\n\n```pipeline\n{json.dumps(c.pipeline, indent=1)}\n```\n"
                f"```python\n{c.module}\n```")

    def _make(self, text: str, arm: str, origin: str, parents: list[str], index: int) -> Candidate:
        cid = f"{arm}-{index:02d}-{uuid.uuid4().hex[:6]}"
        c = Candidate(cid, arm, origin, parents, "", "", [], "", time.time(), index, 1, 0.0)
        try:
            c.strategy, c.change, c.pipeline, c.module = parse_reply(text)
            c.static_problems = validate_source(c.module.rstrip() + "\n\ndef step(state, params):\n    return state\n")
            if not c.static_problems:
                c.ir = build_ir(c.pipeline, c.module, cid)
        except ParseError as e:
            c.parse_error = str(e)
        return c

    # ------------------------------------------------------------------ moves
    def generate(self, arm: str, index: int, attempt_note: str = "") -> Candidate:
        prompt = (self._task_block() + "\n\nDesign and implement a complete, high-quality solution for this task."
                  + (f" ({attempt_note})" if attempt_note else ""))
        return self._make(self._call(prompt, "synthesize"), arm, "generate", [], index)

    def mutate(self, parent: Candidate, op: str, dev_feedback: str, arm: str, index: int) -> Candidate:
        prompt = (self._task_block() + "\n\nCURRENT PROGRAM\n" + self._program_block(parent)
                  + "\n\nRESULTS OF THE CURRENT PROGRAM ON DEVELOPMENT INSTANCES\n" + dev_feedback
                  + f"\n\nSTRUCTURAL OPERATION TO APPLY: {op} — {STRUCTURAL_OPERATORS[op]}\n"
                  "Produce the complete new program. The change must be structural (graph topology and/or "
                  "algorithm), not a re-tuning of constants. Add a line 'CHANGE:' (2-4 sentences) before the "
                  "blocks describing what you changed and why it should help.")
        c = self._make(self._call(prompt, "synthesize"), arm, f"mutate:{op}", [parent.id], index)
        return c

    def intervene(self, parent: Candidate, dev_feedback: str, arm: str, index: int) -> Candidate:
        """Self-directed structural mutation: the model diagnoses failures and designs its OWN intervention.
        The operator catalogue is shown only as inspiration; the model may (and is encouraged to) invent one."""
        menu = "\n".join(f"  - {k}: {v}" for k, v in STRUCTURAL_OPERATORS.items())
        prompt = (self._task_block() + "\n\nCURRENT PROGRAM\n" + self._program_block(parent)
                  + "\n\nRESULTS OF THE CURRENT PROGRAM ON DEVELOPMENT INSTANCES\n" + dev_feedback
                  + "\n\nYOUR JOB: make this program substantially better through a STRUCTURAL change.\n"
                  "1. Under 'DIAGNOSIS:' state in 2-5 sentences the main cause of lost score (think about what the "
                  "algorithm fundamentally cannot do, not about constants).\n"
                  "2. Under 'OPERATION:' give a short snake_case name for the structural intervention you design. "
                  "Examples of intervention types (you may invent a different one):\n" + menu + "\n"
                  "3. Under 'CHANGE:' describe in 2-4 sentences what you change in the graph/algorithm and why.\n"
                  "4. Give the updated 'STRATEGY:' and the complete new program in the required format.\n"
                  "Re-tuning constants or cosmetic edits do not count and will be discarded.")
        text = self._call(prompt, "synthesize")
        c = self._make(text, arm, "intervene", [parent.id], index)
        m = re.search(r"OPERATION:\s*([A-Za-z0-9_\- ]{2,60})", text)
        op = re.sub(r"[^a-z0-9]+", "_", m.group(1).strip().lower()).strip("_") if m else "unnamed"
        c.origin = f"intervene:{op}"
        d = re.search(r"DIAGNOSIS:\s*(.*?)(?:\n\s*\n|OPERATION:)", text, re.S)
        c.change = ((("DIAGNOSIS: " + d.group(1).strip() + " ") if d else "") + c.change)[:2500]
        return c

    def recombine(self, a: Candidate, b: Candidate, fb_a: str, fb_b: str, arm: str, index: int) -> Candidate:
        prompt = (self._task_block() + "\n\nPROGRAM X\n" + self._program_block(a) + "\n\nDEV RESULTS OF X\n" + fb_a[:5000]
                  + "\n\nPROGRAM Y\n" + self._program_block(b) + "\n\nDEV RESULTS OF Y\n" + fb_b[:5000]
                  + "\n\nDesign a new program that combines the complementary strengths of X and Y into one coherent "
                  "architecture (not a mere switch between them unless routing is genuinely justified). Under "
                  "'CHANGE:' explain what is taken from each and what is new. Then give 'STRATEGY:' and the complete "
                  "program in the required format.")
        return self._make(self._call(prompt, "synthesize"), arm, "recombine", [a.id, b.id], index)

    def expand(self, archive: list[dict[str, Any]], arm: str, index: int) -> Candidate:
        tried = json.dumps(archive, indent=1)[:12000]
        prompt = (self._task_block() + "\n\nSEARCH HISTORY — every approach tried so far for this task, with its "
                  "development score and characteristic failures:\n" + tried
                  + "\n\nProgress has stalled. First, under 'ANALYSIS:', explain in 3-6 sentences what limits all of "
                  "these approaches. Then invent a solution PARADIGM that is fundamentally different from every "
                  "approach listed (not a variant or re-tuning of one of them), and implement it completely. "
                  "Start the strategy with 'PARADIGM: <name>'.")
        c = self._make(self._call(prompt, "synthesize"), arm, "expand", [a["id"] for a in archive], index)
        return c

    def repair(self, broken: Candidate, problem: str, arm: str) -> Candidate:
        prompt = (self._task_block() + "\n\nThis program could not be used:\n" + self._program_block(broken)
                  + f"\n\nPROBLEM:\n{problem[:4000]}\n\nReturn the complete corrected program in the required "
                  "format, keeping its approach.")
        c = self._make(self._call(prompt, "synthesize"), arm, broken.origin, broken.parents, broken.generation_index)
        c.strategy = c.strategy or broken.strategy
        c.calls_used = broken.calls_used + 1
        c.change = (broken.change + " [repaired]").strip()
        return c
