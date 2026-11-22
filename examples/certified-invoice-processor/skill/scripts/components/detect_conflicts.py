# params: {"extra_total_labels": ["Please pay", "Balance Due"], "null_conflicting_total": true}
# origin: library, role: code
import re

def step(state, params):
    vals = set(state.get("parsed_totals") or [])
    extra = params.get("extra_total_labels", [])
    text = state.get("text", "")
    for lab in extra:
        for m in re.finditer(r"^\s*" + re.escape(lab) + r"\s*:\s*([-$€£A-Z ]{0,5}-?[\d.,]+)", text, re.I | re.M):
            raw = re.sub(r"[^\d.,-]", "", m.group(1))
            if params.get("eu_decimals", False) and re.fullmatch(r"-?\d{1,3}(\.\d{3})*,\d{1,2}", raw):
                raw = raw.replace(".", "").replace(",", ".")
            try:
                vals.add(round(float(raw.replace(",", "")), 2))
            except ValueError:
                pass
    if len(vals) > 1:
        if "conflicting_values" not in state["issues"]:
            state["issues"].append("conflicting_values")
        if params.get("null_conflicting_total", True):
            state["fields"]["total"] = None
            state["flags"]["total_conflict"] = True
    return state
