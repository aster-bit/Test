# params: {}
# origin: library, role: code
def step(state, params):
    inv = state["fields"].get("invoice_number")
    seen = ((state.get("input") or {}).get("context") or {}).get("seen_invoice_ids") or []
    if inv is not None:
        norm = (lambda s: str(s).strip().upper()) if params.get("case_insensitive", True) else str
        if norm(inv) in {norm(s) for s in seen}:
            if "duplicate_invoice" not in state["issues"]:
                state["issues"].append("duplicate_invoice")
    return state
