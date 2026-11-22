# params: {}
# origin: library, role: validator
import re

def _digits(s):
    return re.sub(r"\D", "", s)

def step(state, params):
    text = state.get("input", {}).get("text") or ""
    low = text.lower()
    digits = _digits(text)
    f = state["fields"]
    for k in ("vendor", "invoice_number"):
        v = f.get(k)
        if v is not None and str(v).strip().lower() not in low:
            f[k] = None
            state["flags"]["ungrounded_" + k] = True
    for k in ("subtotal", "tax", "total"):
        v = f.get(k)
        if v is not None:
            d = _digits("%.2f" % abs(v))
            d2 = _digits(str(int(abs(v)))) if params.get("allow_integer_form", True) else d
            if d not in digits and d2 not in digits:
                f[k] = None
                state["flags"]["ungrounded_" + k] = True
    v = f.get("date")
    if v is not None and params.get("check_date", True):
        y, m, d = v.split("-")
        if y not in text or str(int(d)) not in text:
            f["date"] = None
            state["flags"]["ungrounded_date"] = True
    return state
