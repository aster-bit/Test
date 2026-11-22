# params: {"tolerance": 0.02, "check_items_sum": true, "check_line_math": false}
# origin: library, role: code
def step(state, params):
    f = state["fields"]
    tol = float(params.get("tolerance", 0.01))
    sub, tax, tot = f.get("subtotal"), f.get("tax"), f.get("total")
    bad = False
    if sub is not None and tax is not None and tot is not None:
        bad = bad or abs(sub + tax - tot) > tol
    items = state.get("items") or []
    if params.get("check_items_sum", True) and items and sub is not None:
        bad = bad or abs(sum(i["amount"] for i in items) - sub) > tol * max(1, len(items))
    if params.get("check_line_math", True):
        for i in items:
            if abs(i["quantity"] * i["unit_price"] - i["amount"]) > tol + 1e-9:
                bad = True
    if params.get("infer_missing_subtotal", False) and sub is None and tot is not None and items:
        f["subtotal"] = round(sum(i["amount"] for i in items), 2)
    if bad and "arithmetic_mismatch" not in state["issues"]:
        state["issues"].append("arithmetic_mismatch")
    return state
