# params: {"patterns": ["ignore (all |any )?(previous|prior|above) instructions", "\\bassistant\\b"]}
# origin: library, role: code
import re

def step(state, params):
    pats = [re.compile(p, re.I) for p in params.get("patterns", [])]
    kept, hit = [], False
    for line in state.get("text", "").split("\n"):
        if any(p.search(line) for p in pats):
            hit = True
            continue
        kept.append(line)
    state["text"] = "\n".join(kept)
    if hit:
        state["flags"]["injection"] = True
        if "injection_detected" not in state["issues"]:
            state["issues"].append("injection_detected")
    return state
