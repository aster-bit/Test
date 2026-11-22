# params: {"fill_only_missing": true}
# origin: library, role: code
import json, re

def step(state, params):
    raw = state.get("llm_output")
    state.setdefault("fields", {})
    state.setdefault("issues", [])
    state.setdefault("items", [])
    state.setdefault("flags", {})
    if isinstance(raw, dict):
        doc = raw
    else:
        doc = None
        s = str(raw or "")
        m = re.search(r"\{.*\}", s, re.S)
        if m:
            try:
                doc = json.loads(m.group(0))
            except ValueError:
                doc = None
    if not isinstance(doc, dict):
        state["flags"]["llm_parse_error"] = True
        return state
    fill_only = params.get("fill_only_missing", False)
    for k in ("vendor", "invoice_number", "date", "currency", "subtotal", "tax", "total"):
        if k in doc and (not fill_only or state["fields"].get(k) is None):
            v = doc[k]
            if k in ("subtotal", "tax", "total") and v is not None:
                try:
                    v = round(float(v), 2)
                except (TypeError, ValueError):
                    v = None
            state["fields"][k] = v
    if isinstance(doc.get("line_items"), list) and (not fill_only or not state["items"]):
        items = []
        for it in doc["line_items"]:
            try:
                items.append({"description": str(it.get("description", "")), "quantity": int(it.get("quantity", 0)),
                              "unit_price": round(float(it.get("unit_price", 0)), 2),
                              "amount": round(float(it.get("amount", 0)), 2)})
            except (TypeError, ValueError, AttributeError):
                pass
        state["items"] = items
    if doc.get("status") in ("valid", "invalid", "needs_review"):
        state["llm_status"] = doc["status"]
    for i in doc.get("issues") or []:
        if isinstance(i, str) and i not in state["issues"]:
            state["issues"].append(i)
    if doc.get("document_type") in ("invoice", "credit_note"):
        state["llm_document_type"] = doc["document_type"]
    return state
