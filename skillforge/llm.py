"""LLM provider abstraction.

* ``ClaudeCodeProvider`` — DEFAULT real provider: headless Claude Code (``claude -p``),
  using the user's Claude Code login. No API key is needed.
* ``AnthropicProvider`` — optional direct API access (only with ``--provider anthropic``).
* ``MockProvider``      — deterministic, offline. Domain plugins register
  *simulated models* for it so that the whole pipeline (including prompt
  optimization) can run without an API key. Everything produced by the mock
  is flagged ``simulated=True`` and the final report says so explicitly.

Providers live on the host. Sandboxed candidate code never receives an API key
or a provider handle — LLM components are executed by the host-side executor.
"""

from __future__ import annotations

import hashlib
import re
import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

# USD per 1M tokens (input, output). Source: Anthropic model table (cached 2026-11).
PRICING: dict[str, tuple[float, float]] = {
    "claude-fable-5-2": (8.0, 40.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
}
DEFAULT_MODEL = "claude-opus-5-5"


def price(model: str, input_tokens: int, output_tokens: int) -> float:
    pin, pout = PRICING.get(model, PRICING[DEFAULT_MODEL])
    return (input_tokens * pin + output_tokens * pout) / 1_000_000


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class CostMeter:
    max_usd: float | None = None  # None = unlimited
    max_calls: int | None = None
    usd: float = 0.0
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    by_purpose: dict[str, float] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def check(self) -> None:
        if self.max_usd is not None and self.usd >= self.max_usd:
            raise BudgetExceeded(f"USD budget exhausted ({self.usd:.4f} >= {self.max_usd})")
        if self.max_calls is not None and self.calls >= self.max_calls:
            raise BudgetExceeded(f"LLM call budget exhausted ({self.calls})")

    def add(self, purpose: str, resp: "LLMResponse") -> None:
        with self._lock:
            self.usd += resp.cost_usd
            self.calls += 1
            self.input_tokens += resp.input_tokens
            self.output_tokens += resp.output_tokens
            self.by_purpose[purpose] = self.by_purpose.get(purpose, 0.0) + resp.cost_usd

    def snapshot(self) -> dict[str, Any]:
        return {
            "usd": round(self.usd, 6), "calls": self.calls, "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens, "by_purpose": {k: round(v, 6) for k, v in self.by_purpose.items()},
        }


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class LLMRequest:
    system: str
    messages: list[dict[str, Any]]
    model: str = DEFAULT_MODEL
    max_tokens: int = 2048
    effort: str = "low"
    tools: list[ToolSpec] = field(default_factory=list)
    purpose: str = "execute"  # execute | synthesize | compile_spec | adversary | judge
    # Side-channel for the mock provider only (e.g. which component is asking).
    # Real providers ignore it, so it cannot leak hidden data to a real model.
    mock_context: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    model: str = ""
    stop_reason: str = "end_turn"
    simulated: bool = False
    raw_content: list[Any] = field(default_factory=list)


class LLMProvider(Protocol):
    name: str
    simulated: bool
    meter: CostMeter

    def complete(self, req: LLMRequest) -> LLMResponse: ...


SimulatedModel = Callable[[LLMRequest, "MockProvider"], LLMResponse]


class MockProvider:
    """Deterministic offline provider. Behaviour is supplied by domain plugins."""

    name = "mock"
    simulated = True

    def __init__(self, seed: int = 0, meter: CostMeter | None = None):
        self.seed = seed
        self.meter = meter or CostMeter()
        self._handlers: dict[str, SimulatedModel] = {}

    def register(self, purpose: str, handler: SimulatedModel) -> None:
        self._handlers[purpose] = handler

    def rng_for(self, *parts: Any) -> "Any":
        import random
        h = hashlib.sha256(json.dumps([self.seed, *parts], sort_keys=True, default=str).encode()).digest()
        return random.Random(int.from_bytes(h[:8], "big"))

    def finalize(self, req: LLMRequest, text: str, tool_calls: list[dict] | None = None,
                 stop_reason: str = "end_turn") -> LLMResponse:
        prompt_text = req.system + "".join(
            m["content"] if isinstance(m["content"], str) else json.dumps(m["content"]) for m in req.messages
        )
        tin, tout = estimate_tokens(prompt_text), estimate_tokens(text) + 8 * len(tool_calls or [])
        # Simulated latency model: fixed overhead + per-output-token decode time.
        latency = 350.0 + tout * 12.0 + tin * 0.05
        return LLMResponse(text=text, tool_calls=tool_calls or [], input_tokens=tin, output_tokens=tout,
                           cost_usd=price(req.model, tin, tout), latency_ms=latency, model=req.model,
                           stop_reason=stop_reason, simulated=True)

    def complete(self, req: LLMRequest) -> LLMResponse:
        self.meter.check()
        handler = self._handlers.get(req.mock_context.get("handler", req.purpose))
        if handler is None:
            resp = self.finalize(req, json.dumps({"note": "mock provider has no handler", "purpose": req.purpose}))
        else:
            resp = handler(req, self)
        self.meter.add(req.purpose, resp)
        return resp


class AnthropicProvider:
    """Real provider using the official Anthropic Python SDK (anthropic>=1.0)."""

    name = "anthropic"
    simulated = False

    def __init__(self, meter: CostMeter | None = None, use_fallbacks: bool = True, timeout: float = 120.0):
        import anthropic  # imported lazily so the package works without the SDK

        self._anthropic = anthropic
        self.client = anthropic.Anthropic(timeout=timeout, max_retries=3)
        self.meter = meter or CostMeter()
        self.use_fallbacks = use_fallbacks

    def complete(self, req: LLMRequest) -> LLMResponse:
        self.meter.check()
        kwargs: dict[str, Any] = dict(
            model=req.model,
            max_tokens=req.max_tokens,
            system=req.system,
            messages=req.messages,
            output_config={"effort": req.effort},
        )
        if req.tools:
            kwargs["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in req.tools
            ]
        t0 = time.perf_counter()
        a = self._anthropic
        try:
            if self.use_fallbacks and req.model in ("claude-fable-5-2", "claude-opus-5-5", "claude-sonnet-5-5"):
                msg = self.client.beta.messages.create(
                    betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs
                )
            else:
                msg = self.client.messages.create(**kwargs)
        except a.RateLimitError:
            raise
        except a.APIStatusError as e:  # non-retryable request errors surface as candidate failures
            return LLMResponse(text="", stop_reason=f"api_error:{e.status_code}", model=req.model)
        latency = (time.perf_counter() - t0) * 1000
        text_parts, tool_calls = [], []
        for block in msg.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append({"id": block.id, "name": block.name, "input": dict(block.input)})
        tin, tout = msg.usage.input_tokens, msg.usage.output_tokens
        resp = LLMResponse(
            text="".join(text_parts), tool_calls=tool_calls, input_tokens=tin, output_tokens=tout,
            cost_usd=price(msg.model, tin, tout), latency_ms=latency, model=msg.model,
            stop_reason=msg.stop_reason or "", simulated=False,
            raw_content=[b.model_dump(exclude_none=True) for b in msg.content],
        )
        self.meter.add(req.purpose, resp)
        return resp


    def complete_structured(self, system: str, prompt: str, schema: dict[str, Any], model: str = DEFAULT_MODEL,
                            purpose: str = "compile_spec") -> dict[str, Any] | None:
        self.meter.check()
        msg = self.client.messages.create(
            model=model, max_tokens=4000, system=system, messages=[{"role": "user", "content": prompt}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}})
        text = "".join(b.text for b in msg.content if b.type == "text")
        self.meter.add(purpose, LLMResponse(text=text, input_tokens=msg.usage.input_tokens,
                                            output_tokens=msg.usage.output_tokens,
                                            cost_usd=price(model, msg.usage.input_tokens, msg.usage.output_tokens)))
        try:
            return json.loads(text)
        except ValueError:
            return None


class ClaudeCodeProvider:
    """LLM calls through Claude Code in headless mode (``claude -p``) — the default real provider.

    Uses the user's Claude Code login; no API key is read or required by SkillForge.
    Every call is an isolated, tool-less, session-less invocation:

        claude -p --output-format json --tools "" --no-session-persistence --strict-mcp-config
               --disable-slash-commands --setting-sources "" --system-prompt <s> --model <m> --effort <e>

    run from an empty working directory, so no project CLAUDE.md, hooks, MCP servers or
    built-in tools (Bash/Edit/...) are active. Multi-turn histories (few-shot, tool loops) are
    rendered into a single transcript prompt. Candidate tool use is a JSON text protocol
    (``{"tool_calls": [...]}``) executed by SkillForge's sandbox, never by Claude Code itself.
    The claude process runs on the trusted host side; candidate code never sees it.
    """

    name = "claude_code"
    simulated = False

    def __init__(self, meter: CostMeter | None = None, binary: str | None = None, timeout: float = 1200.0,
                 concurrency: int = 4, workdir: str | None = None, max_retries: int = 30):
        import shutil
        import tempfile

        self.binary = binary or shutil.which("claude") or "claude"
        self.meter = meter or CostMeter()
        self.timeout = timeout
        self.concurrency = concurrency
        self.max_retries = max_retries
        self.workdir = workdir or tempfile.mkdtemp(prefix="skillforge-cc-")

    # ---------------- prompt rendering
    @staticmethod
    def _tool_protocol(tools: list[ToolSpec]) -> str:
        spec = json.dumps([{"name": t.name, "description": t.description, "input_schema": t.input_schema}
                           for t in tools], indent=1)
        return ("\n\nYou can call these tools:\n" + spec +
                '\nTo call tools, reply with ONLY a JSON object {"tool_calls": [{"name": ..., "input": {...}}]}. '
                "Tool results will be returned to you in the transcript. When you have the final answer, reply "
                "with the answer itself (not a tool call).")

    @staticmethod
    def _render(messages: list[dict[str, Any]]) -> str:
        if len(messages) == 1 and isinstance(messages[0]["content"], str):
            return messages[0]["content"]
        parts = []
        for m in messages:
            role = m["role"].upper()
            c = m["content"]
            if isinstance(c, str):
                parts.append(f"<{role}>\n{c}\n</{role}>")
                continue
            for b in c:
                if b.get("type") == "text":
                    parts.append(f"<{role}>\n{b['text']}\n</{role}>")
                elif b.get("type") == "tool_use":
                    parts.append(f"<TOOL_CALL id={b['id']}>{json.dumps({'name': b['name'], 'input': b['input']})}"
                                 f"</TOOL_CALL>")
                elif b.get("type") == "tool_result":
                    parts.append(f"<TOOL_RESULT id={b['tool_use_id']}>{b.get('content')}</TOOL_RESULT>")
        parts.append("Continue as ASSISTANT: reply to the last USER turn.")
        return "\n\n".join(parts)

    def _invoke(self, system: str, prompt: str, model: str, effort: str,
                schema: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, float, str]:
        import subprocess

        cmd = [self.binary, "-p", "--output-format", "json", "--tools", "", "--no-session-persistence",
               "--strict-mcp-config", "--disable-slash-commands", "--setting-sources", "",
               "--system-prompt", system, "--model", model, "--effort", effort]
        if schema is not None:
            cmd += ["--json-schema", json.dumps(schema)]
        t0 = time.perf_counter()
        try:
            p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=self.timeout,
                               cwd=self.workdir)
        except subprocess.TimeoutExpired:
            return None, (time.perf_counter() - t0) * 1000, "timeout"
        latency = (time.perf_counter() - t0) * 1000
        try:
            doc = json.loads(p.stdout)
        except ValueError:
            return None, latency, f"cli_error:{p.returncode}:{(p.stderr or p.stdout)[-200:]}"
        if doc.get("is_error"):
            return doc, latency, f"cli_error:{doc.get('subtype')}"
        return doc, latency, ""

    TRANSIENT = ("rate", "overloaded", "529", "429", "timeout", "temporarily", "ECONNRESET", "network")

    def _invoke_retrying(self, system: str, prompt: str, model: str, effort: str,
                         schema: dict[str, Any] | None = None):
        """Unlimited-budget runs must survive rate limits: transient CLI errors are retried with
        exponential backoff (capped at 10 minutes) instead of being scored as candidate failures."""
        delay = 5.0
        for _ in range(self.max_retries):
            doc, latency, err = self._invoke(system, prompt, model, effort, schema)
            if not err:
                return doc, latency, err
            blob = (err + " " + json.dumps(doc or {}))[:2000].lower()
            if not any(t.lower() in blob for t in self.TRANSIENT):
                return doc, latency, err
            import sys
            print(f"[provider] transient error after {latency / 1000:.0f}s: {err[:120]}; retry in {delay:.0f}s",
                  file=sys.stderr, flush=True)
            time.sleep(delay)
            delay = min(600.0, delay * 2)
        return doc, latency, err

    def complete(self, req: LLMRequest) -> LLMResponse:
        self.meter.check()
        system = req.system + (self._tool_protocol(req.tools) if req.tools else "")
        doc, latency, err = self._invoke_retrying(system, self._render(req.messages), req.model, req.effort)
        if err:
            resp = LLMResponse(text="", stop_reason="api_error:" + err[:80], model=req.model, latency_ms=latency)
            self.meter.add(req.purpose, resp)
            return resp
        text = doc.get("result") or ""
        usage = doc.get("usage") or {}
        tin = int(usage.get("input_tokens", 0)) + int(usage.get("cache_read_input_tokens", 0)) + \
            int(usage.get("cache_creation_input_tokens", 0))
        tout = int(usage.get("output_tokens", 0))
        tool_calls: list[dict[str, Any]] = []
        if req.tools:
            m = re.search(r"\{.*\}", text, re.S)
            if m:
                try:
                    obj = json.loads(m.group(0))
                    if isinstance(obj, dict) and isinstance(obj.get("tool_calls"), list):
                        allowed = {t.name for t in req.tools}
                        tool_calls = [{"id": f"call_{i}", "name": c.get("name"), "input": c.get("input") or {}}
                                      for i, c in enumerate(obj["tool_calls"])
                                      if isinstance(c, dict) and c.get("name") in allowed]
                except ValueError:
                    pass
        cost = doc.get("total_cost_usd")
        resp = LLMResponse(
            text="" if tool_calls else text, tool_calls=tool_calls, input_tokens=tin, output_tokens=tout,
            cost_usd=float(cost) if cost is not None else price(req.model, tin, tout), latency_ms=latency,
            model=req.model, stop_reason="tool_use" if tool_calls else (doc.get("stop_reason") or "end_turn"),
            simulated=False)
        self.meter.add(req.purpose, resp)
        return resp

    def complete_structured(self, system: str, prompt: str, schema: dict[str, Any], model: str = DEFAULT_MODEL,
                            purpose: str = "compile_spec") -> dict[str, Any] | None:
        self.meter.check()
        doc, latency, err = self._invoke_retrying(system, prompt, model, "low", schema)
        if err or doc is None:
            return None
        usage = doc.get("usage") or {}
        self.meter.add(purpose, LLMResponse(text="", input_tokens=int(usage.get("input_tokens", 0)),
                                            output_tokens=int(usage.get("output_tokens", 0)),
                                            cost_usd=float(doc.get("total_cost_usd") or 0.0), latency_ms=latency))
        out = doc.get("structured_output")
        return out if isinstance(out, dict) else None


def claude_code_available() -> bool:
    import shutil

    return shutil.which("claude") is not None


def make_provider(kind: str = "auto", seed: int = 0, meter: CostMeter | None = None, concurrency: int = 4):
    """auto -> Claude Code (headless) when the `claude` CLI is installed, else the offline mock.

    The direct Anthropic API is used only when explicitly requested with kind="anthropic".
    """
    if kind == "mock":
        return MockProvider(seed=seed, meter=meter)
    if kind == "anthropic":
        return AnthropicProvider(meter=meter)
    if kind == "claude_code" or (kind == "auto" and claude_code_available()):
        return ClaudeCodeProvider(meter=meter, concurrency=concurrency)
    return MockProvider(seed=seed, meter=meter)
