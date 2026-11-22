# params: {"required": ["vendor", "invoice_number", "date", "total"]}
# origin: library, role: code
def step(state, params):
    f = state.get("fields") or {}
    need = [k for k in params.get("required", ["vendor", "invoice_number", "date", "total"]) if f.get(k) is None]
    weak = (not state.get("items")) and params.get("require_items", True)
    weak = weak or ("unparseable_date" in state.get("issues", []))
    state["needs_llm"] = bool(need or weak) and not state.get("flags", {}).get("total_conflict")
    return state
