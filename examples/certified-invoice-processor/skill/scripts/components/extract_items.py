# params: {"patterns": ["^\\s*-\\s*(?P<desc>.+?)\\s+x(?P<qty>\\d+)\\s+@\\s+(?P<price>\\S+)\\s+=\\s+(?P<amount>\\S+)\\s*$", "^\\s*(?P<desc>[^|]+?)\\s*\\|\\s*(?P<qty>\\d+)\\s*\\|\\s*(?P<price>[^|]+?)\\s*\\|\\s*(?P<amount>[^|]+?)\\s*$", "^\\s*(?P<qty>\\d+)\\s+x\\s+(?P<desc>.+?)\\s+at\\s+(?P<price>\\S+)\\s+each:\\s+(?P<amount>\\S+)\\s*$"], "eu_decimals": true}
# origin: library, role: code
import re

def _num(s, eu):
    s = s.strip()
    neg = s.startswith("-")
    s = re.sub(r"[^\d.,]", "", s)
    if eu and re.fullmatch(r"\d{1,3}(\.\d{3})*,\d{1,2}", s):
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", "")
    v = round(float(s), 2)
    return -v if neg else v

def step(state, params):
    eu = params.get("eu_decimals", False)
    pats = [re.compile(p) for p in params.get("patterns", [])]
    items = []
    for line in state.get("text", "").split("\n"):
        for p in pats:
            m = p.match(line)
            if not m:
                continue
            g = m.groupdict()
            try:
                items.append({"description": g["desc"].strip(), "quantity": int(g["qty"]),
                              "unit_price": _num(g["price"], eu), "amount": _num(g["amount"], eu)})
            except (ValueError, KeyError, TypeError):
                pass
            break
    state["items"] = items
    return state
