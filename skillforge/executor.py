"""Host-side Skill IR interpreter.

Runs a candidate over a *batch* of inputs, stage by stage:

* consecutive ``code`` components are fused into one sandbox call that
  processes every item (one process spawn per stage, not per item);
* ``llm`` components are executed per item through the provider (keys stay
  on the host); tool calls are executed as sandboxed code;
* ``branch`` components partition the batch by a state key and recurse.

Per-item latency = measured sandbox time + provider latency (real or simulated).
"""

from __future__ import annotations

import json
import string
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from .ir import BranchComponent, CodeComponent, LLMComponent, SkillIR
from .llm import BudgetExceeded, LLMProvider, LLMRequest, ToolSpec
from .sandbox import ExecutionSandbox


@dataclass
class ItemTrace:
    output: dict[str, Any] | None = None
    error: str | None = None
    latency_ms: float = 0.0
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    llm_calls: int = 0
    path: list[str] = field(default_factory=list)
    simulated_llm: bool = False


def _get(state: dict[str, Any], dotted: str) -> Any:
    cur: Any = state
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


class _SafeFormatter(string.Formatter):
    """Only whitelisted placeholders; no attribute/index access into objects."""

    ALLOWED = {"input_text", "context_json", "state_json", "input_json"}

    def get_field(self, field_name, args, kwargs):
        if field_name not in self.ALLOWED:
            raise KeyError(f"placeholder {field_name!r} not allowed")
        return kwargs[field_name], field_name


_FMT = _SafeFormatter()


def render_prompt(template: str, state: dict[str, Any]) -> str:
    inp = state.get("input") if isinstance(state.get("input"), dict) else {}
    text = state.get("text")
    if text is None:
        text = inp.get("text") or ""
    ctx = inp.get("context") or {}
    fields = state.get("fields") or {}
    try:
        work = {k: v for k, v in state.items() if k not in ("input", "output") and not k.startswith("_")}
        return _FMT.format(template, input_text=text, context_json=json.dumps(ctx),
                           state_json=json.dumps(fields or work, default=str)[:20000],
                           input_json=json.dumps(state.get("input"), default=str))
    except (KeyError, ValueError, IndexError):
        # malformed templates (e.g. from mutation) degrade to the raw document
        return template + "\n\n" + text


class Executor:
    def __init__(self, sandbox: ExecutionSandbox, provider: LLMProvider, tool_library: dict[str, dict[str, Any]]):
        self.sandbox = sandbox
        self.provider = provider
        self.tools = tool_library

    def run(self, ir: SkillIR, inputs: list[dict[str, Any]]) -> list[ItemTrace]:
        states = [{"input": x} for x in inputs]
        traces = [ItemTrace() for _ in inputs]
        self._run_block(ir.components, list(range(len(inputs))), states, traces)
        for st, tr in zip(states, traces):
            if tr.error is None:
                out = st.get("output")
                if not isinstance(out, dict):
                    tr.error = "candidate produced no output"
                tr.output = out if isinstance(out, dict) else None
        return traces

    # ------------------------------------------------------------------
    def _run_block(self, comps, idx: list[int], states, traces) -> None:
        i = 0
        while i < len(comps):
            live = [j for j in idx if traces[j].error is None]
            if not live:
                return
            c = comps[i]
            if isinstance(c, CodeComponent):
                seg = []
                while i < len(comps) and isinstance(comps[i], CodeComponent):
                    seg.append(comps[i])
                    i += 1
                self._run_code(seg, live, states, traces)
                continue
            if isinstance(c, LLMComponent):
                workers = max(1, int(getattr(self.provider, "concurrency", 1)))
                if workers == 1 or len(live) == 1:
                    for j in live:
                        self._run_llm(c, j, states, traces)
                else:  # items are independent; each touches only its own state/trace
                    with ThreadPoolExecutor(max_workers=workers) as pool:
                        for f in [pool.submit(self._run_llm, c, j, states, traces) for j in live]:
                            f.result()  # re-raises BudgetExceeded
            elif isinstance(c, BranchComponent):
                yes = [j for j in live if _get(states[j], c.condition_key)]
                no = [j for j in live if j not in set(yes)]
                for j in yes:
                    traces[j].path.append(f"{c.name}:then")
                for j in no:
                    traces[j].path.append(f"{c.name}:else")
                if yes and c.then:
                    self._run_block(c.then, yes, states, traces)
                if no and c.otherwise:
                    self._run_block(c.otherwise, no, states, traces)
            i += 1

    def _run_code(self, seg: list[CodeComponent], live: list[int], states, traces) -> None:
        payload = [{"name": c.name, "source": c.source, "params": c.params} for c in seg]
        res = self.sandbox.run_batch(payload, [states[j] for j in live])
        for j, item in zip(live, res.items):
            traces[j].latency_ms += item.ms
            traces[j].path.extend(c.name for c in seg)
            if item.error:
                traces[j].error = f"[{'+'.join(c.name for c in seg)}] {item.error}"
            else:
                states[j] = item.state

    def _run_tool(self, name: str, args: dict[str, Any], state: dict[str, Any], trace: ItemTrace) -> str:
        tool = self.tools.get(name)
        if tool is None:
            return json.dumps({"error": f"unknown tool {name}"})
        ctx = (state.get("input") or {}).get("context") or {}
        res = self.sandbox.run_batch([{"name": f"tool_{name}", "source": tool["source"], "params": {}}],
                                     [{"args": args, "context": ctx}])
        item = res.items[0]
        trace.latency_ms += item.ms
        if item.error:
            return json.dumps({"error": item.error})
        return json.dumps(item.state.get("result"))

    def _run_llm(self, c: LLMComponent, j: int, states, traces) -> None:
        st, tr = states[j], traces[j]
        prompt = render_prompt(c.prompt_template, st)
        messages: list[dict[str, Any]] = []
        for shot in c.few_shot:
            messages.append({"role": "user", "content": render_prompt(c.prompt_template,
                                                                      {"input": {"text": shot["input"]}})})
            messages.append({"role": "assistant", "content": shot["output"]})
        messages.append({"role": "user", "content": prompt})
        tool_specs = [ToolSpec(n, self.tools[n]["description"], self.tools[n]["input_schema"])
                      for n in c.tools if n in self.tools]
        doc = st.get("text") if st.get("text") is not None else (st.get("input") or {}).get("text") or ""
        mock_ctx = {"document": doc, "component": c.name, "context": (st.get("input") or {}).get("context")}
        tr.path.append(c.name)
        text = ""
        try:
            for turn in range(max(1, c.max_tool_turns) + 1):
                req = LLMRequest(system=c.system, messages=messages, model=c.model, max_tokens=c.max_tokens,
                                 effort=c.effort, tools=tool_specs, purpose="execute",
                                 mock_context=dict(mock_ctx, attempt=turn))
                resp = self.provider.complete(req)
                tr.llm_calls += 1
                tr.cost_usd += resp.cost_usd
                tr.input_tokens += resp.input_tokens
                tr.output_tokens += resp.output_tokens
                tr.latency_ms += resp.latency_ms
                tr.simulated_llm = tr.simulated_llm or resp.simulated
                text = resp.text
                if resp.stop_reason == "refusal" or resp.stop_reason.startswith("api_error"):
                    st.setdefault("flags", {})["llm_" + resp.stop_reason.split(":")[0]] = True
                    break
                if not resp.tool_calls or turn >= c.max_tool_turns:
                    break
                assistant_content = resp.raw_content or (
                    ([{"type": "text", "text": resp.text}] if resp.text else []) +
                    [{"type": "tool_use", "id": t["id"], "name": t["name"], "input": t["input"]} for t in resp.tool_calls])
                messages.append({"role": "assistant", "content": assistant_content})
                messages.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": t["id"], "content": self._run_tool(t["name"], t["input"], st, tr)}
                    for t in resp.tool_calls]})
        except BudgetExceeded:
            raise
        st[c.output_key] = text
