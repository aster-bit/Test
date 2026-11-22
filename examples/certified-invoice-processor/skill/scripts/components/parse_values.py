# params: {"date_formats": ["%Y-%m-%d", "%B %d, %Y", "%d.%m.%Y"], "eu_decimals": true, "currency_from_amounts": true}
# origin: library, role: code
import re
from datetime import datetime

SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP"}

def parse_amount(s, eu):
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return round(float(s), 2)
    s = str(s).strip()
    neg = s.startswith("-") or (s.startswith("(") and s.endswith(")"))
    s = re.sub(r"[^\d.,]", "", s)
    if not s:
        return None
    if eu and re.fullmatch(r"\d{1,3}(\.\d{3})*,\d{1,2}", s):
        s = s.replace(".", "").replace(",", ".")
    elif eu and re.fullmatch(r"\d+,\d{2}", s):
        s = s.replace(",", ".")
    else:
        s = s.replace(",", "")
    try:
        v = round(float(s), 2)
    except ValueError:
        return None
    return -v if neg else v

def parse_date(s, formats):
    if s is None:
        return None
    s = re.sub(r"\s+", " ", str(s).strip().rstrip("."))
    for fmt in formats:
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None

def step(state, params):
    f = state["fields"]
    eu = params.get("eu_decimals", False)
    raw_totals = state["raw"].get("total", [])
    cur = f.get("currency")
    if cur is not None:
        m = re.search(r"\b(USD|EUR|GBP)\b", str(cur).upper())
        cur = m.group(1) if m else SYMBOLS.get(str(cur).strip()[:1])
    if cur is None and params.get("currency_from_amounts", False):
        blob = " ".join(str(x) for k in ("total", "subtotal") for x in state["raw"].get(k, []))
        m = re.search(r"\b(USD|EUR|GBP)\b", blob)
        if m:
            cur = m.group(1)
        else:
            for sym, code in SYMBOLS.items():
                if sym in blob:
                    cur = code
                    break
    f["currency"] = cur
    for k in ("subtotal", "tax", "total"):
        f[k] = parse_amount(f.get(k), eu)
    state["parsed_totals"] = sorted({parse_amount(x, eu) for x in raw_totals if parse_amount(x, eu) is not None})
    if f.get("date") is not None:
        d = parse_date(f["date"], params.get("date_formats", ["%Y-%m-%d"]))
        if d is None:
            state["issues"].append("unparseable_date")
        f["date"] = d
    return state
