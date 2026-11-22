# params: {"policy": "strict", "detect_credit_note": true, "enforce_consistency": true, "trust_llm_status": false, "report_missing": true}
# origin: library, role: code
import re

REQUIRED = ("vendor", "invoice_number", "date", "total")

def step(state, params):
    f = state.get("fields") or {}
    issues = list(state.get("issues") or [])
    # a value recovered later in the pipeline (e.g. by the LLM fallback) clears its parse-failure flag
    if f.get("date") is not None:
        issues = [i for i in issues if i != "unparseable_date"]
    for k in REQUIRED:
        if f.get(k) is None and ("missing_field:" + k) not in issues:
            if params.get("report_missing", True):
                issues.append("missing_field:" + k)
    if params.get("trust_llm_status", False) and state.get("llm_status"):
        status = state["llm_status"]
    else:
        status = "valid"
        if any(i.startswith(("missing_field", "conflicting_values", "duplicate_invoice", "unparseable")) for i in issues):
            status = "needs_review"
        if "arithmetic_mismatch" in issues:
            status = "invalid"
        if params.get("policy", "strict") == "lenient" and status == "needs_review" and f.get("total") is not None:
            status = "valid"
    items = state.get("items") or []
    total = f.get("total")
    text = (state.get("input") or {}).get("text") or ""
    doc_type = state.get("llm_document_type") or "invoice"
    if params.get("detect_credit_note", True):
        if re.search(r"credit\s+note", text, re.I) or (total is not None and total < 0):
            doc_type = "credit_note"
    if params.get("enforce_consistency", True) and status == "valid":
        sub, tax = f.get("subtotal"), f.get("tax")
        ok = all(f.get(k) is not None for k in REQUIRED) and sub is not None and tax is not None
        ok = ok and abs(sub + tax - total) <= 0.011
        if ok and items:
            ok = abs(sum(i["amount"] for i in items) - sub) <= 0.011 * max(1, len(items))
        if not ok:
            status = "needs_review"
            issues.append("consistency_guard")
    state["output"] = {
        "vendor": f.get("vendor"), "invoice_number": f.get("invoice_number"), "date": f.get("date"),
        "currency": f.get("currency"), "subtotal": f.get("subtotal"), "tax": f.get("tax"), "total": total,
        "line_items": items, "document_type": doc_type, "status": status, "issues": sorted(set(issues)),
    }
    return state
