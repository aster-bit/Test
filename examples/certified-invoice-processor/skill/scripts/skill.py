#!/usr/bin/env python3
"""Standalone runtime for a SkillForge-compiled skill (copied verbatim into skill packages).

Usage:
    echo '{"text": "...", "context": {}}' | python scripts/skill.py
    python scripts/skill.py --file input.json

Code stages run in a separate ``python -I -S`` process with resource limits and
an empty environment, using the bundled ``sandbox_runner.py`` (same isolation
policy the skill was evaluated under). LLM stages run through Claude Code in
headless mode (``claude -p``, using the local Claude Code login — no API key);
if the ``claude`` CLI is not installed they fail closed (the item is routed to
human review rather than guessed).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import string
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
IR = json.loads((HERE / "skill_ir.json").read_text())
TOOLS = json.loads((HERE / "tools.json").read_text()) if (HERE / "tools.json").exists() else {}


def _limits():  # pragma: no cover - runs in child
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (20, 21))
    resource.setrlimit(resource.RLIMIT_FSIZE, (1_000_000,) * 2)


def run_code(segments, states):
    payload = json.dumps({"segments": segments, "states": states, "item_timeout_s": 5.0})
    p = subprocess.run([sys.executable, "-I", "-S", str(HERE / "sandbox_runner.py")], input=payload,
                       capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"}, timeout=60,
                       preexec_fn=_limits if os.name == "posix" else None)
    doc = json.loads(p.stdout)
    return [(r["state"], r["error"]) for r in doc["results"]]


class _Fmt(string.Formatter):
    def get_field(self, name, a, kw):
        if name not in ("input_text", "context_json", "state_json"):
            raise KeyError(name)
        return kw[name], name


def render(template, state):
    text = state.get("text") if state.get("text") is not None else (state.get("input") or {}).get("text") or ""
    return _Fmt().format(template, input_text=text,
                         context_json=json.dumps((state.get("input") or {}).get("context") or {}),
                         state_json=json.dumps(state.get("fields") or {}, default=str))


def _claude(system, prompt, model, effort):
    """One headless Claude Code call (`claude -p`); uses the local Claude Code login, no API key."""
    with tempfile.TemporaryDirectory() as cwd:
        p = subprocess.run(
            [shutil.which("claude") or "claude", "-p", "--output-format", "json", "--tools", "",
             "--no-session-persistence", "--strict-mcp-config", "--disable-slash-commands", "--setting-sources", "",
             "--system-prompt", system, "--model", model, "--effort", effort],
            input=prompt, capture_output=True, text=True, timeout=300, cwd=cwd)
    doc = json.loads(p.stdout)
    if doc.get("is_error"):
        raise RuntimeError(f"claude -p failed: {doc.get('subtype')}")
    return doc.get("result") or ""


def _transcript(messages):
    if len(messages) == 1:
        return messages[0]["content"]
    parts = [f"<{m['role'].upper()}>\n{m['content']}\n</{m['role'].upper()}>" for m in messages]
    return "\n\n".join(parts + ["Continue as ASSISTANT: reply to the last USER turn."])


def run_llm(c, state):
    if not shutil.which("claude"):
        # fail closed: the item goes to human review instead of being guessed
        state.setdefault("flags", {})["llm_unavailable"] = True
        state.setdefault("issues", []).append("llm_unavailable")
        state[c["output_key"]] = ""
        return state
    messages = []
    for shot in c.get("few_shot", []):
        messages.append({"role": "user", "content": render(c["prompt_template"], {"input": {"text": shot["input"]}})})
        messages.append({"role": "assistant", "content": shot["output"]})
    messages.append({"role": "user", "content": render(c["prompt_template"], state)})
    tools = [t for t in c.get("tools", []) if t in TOOLS]
    system = c["system"]
    if tools:
        system += ("\n\nYou can call these tools:\n" +
                   json.dumps([{"name": n, "description": TOOLS[n]["description"],
                                "input_schema": TOOLS[n]["input_schema"]} for n in tools]) +
                   '\nTo call tools, reply with ONLY {"tool_calls": [{"name": ..., "input": {...}}]}. '
                   "When you have the final answer, reply with the answer itself.")
    text = ""
    for _ in range(c.get("max_tool_turns", 3) + 1):
        text = _claude(system, _transcript(messages), c["model"], c.get("effort", "low"))
        calls = []
        if tools:
            m = re.search(r"\{.*\}", text, re.S)
            try:
                obj = json.loads(m.group(0)) if m else None
            except ValueError:
                obj = None
            if isinstance(obj, dict) and isinstance(obj.get("tool_calls"), list):
                calls = [x for x in obj["tool_calls"] if isinstance(x, dict) and x.get("name") in tools]
        if not calls:
            break
        messages.append({"role": "assistant", "content": text})
        results = []
        ctx = (state.get("input") or {}).get("context") or {}
        for call in calls:
            seg = [{"name": "tool", "source": TOOLS[call["name"]]["source"], "params": {}}]
            st, err = run_code(seg, [{"args": call.get("input") or {}, "context": ctx}])[0]
            results.append(f"TOOL_RESULT {call['name']}: " + json.dumps({"error": err} if err else st.get("result")))
        messages.append({"role": "user", "content": "\n".join(results)})
    state[c["output_key"]] = text
    return state


def get(state, dotted):
    cur = state
    for p in dotted.split("."):
        cur = cur.get(p) if isinstance(cur, dict) else None
    return cur


def run_block(comps, state):
    i = 0
    while i < len(comps):
        c = comps[i]
        if c["kind"] == "code":
            seg = []
            while i < len(comps) and comps[i]["kind"] == "code":
                seg.append({"name": comps[i]["name"], "source": comps[i]["source"], "params": comps[i]["params"]})
                i += 1
            state, err = run_code(seg, [state])[0]
            if err:
                raise RuntimeError(err)
            continue
        if c["kind"] == "llm":
            state = run_llm(c, state)
        elif c["kind"] == "branch":
            state = run_block(c["then"] if get(state, c["condition_key"]) else c["otherwise"], state)
        i += 1
    return state


def process(doc: dict) -> dict:
    state = run_block(IR["components"], {"input": doc})
    return state["output"]


def main() -> None:
    if len(sys.argv) > 2 and sys.argv[1] == "--file":
        doc = json.loads(Path(sys.argv[2]).read_text())
    else:
        doc = json.loads(sys.stdin.read())
    print(json.dumps(process(doc), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
