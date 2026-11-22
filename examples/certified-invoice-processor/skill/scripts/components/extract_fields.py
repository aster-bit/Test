# params: {"labels": {"vendor": ["Vendor", "From", "Seller"], "invoice_number": ["Invoice No", "Invoice #", "Invoice Number"], "date": ["Date", "Invoice Date"], "subtotal": ["Subtotal", "Net"], "tax": ["Tax", "Sales Tax"], "total": ["Total", "Amount Due", "Total Due"], "currency": ["Currency"]}, "prose_patterns": [",\\s*tax\\s+(?P<tax>-?[^\\s,]+(?:,\\d{3})*(?:\\.\\d+)?)", "total due\\s+(?P<total>-?[^\\s,(]+(?:,\\d{3})*(?:\\.\\d+)?)", "\\((?P<currency>USD|EUR|GBP)\\)", "issued on\\s+(?P<date>[A-Za-z0-9 ,./-]+?)\\.(?=\\s|$)", "(?:invoice|credit note)\\s+(?P<invoice_number>[A-Z0-9][A-Z0-9-]*\\d)\\b"], "guess_missing": false}
# origin: library, role: code
import re

def _label_re(labels):
    labels = sorted(labels, key=len, reverse=True)
    alt = "|".join(re.escape(l) for l in labels)
    return re.compile(r"^\s*(?:" + alt + r")\s*:\s*(.+?)\s*$", re.I | re.M)

def step(state, params):
    text = state.get("text", "")
    raw = state["raw"]
    for field, labels in params.get("labels", {}).items():
        if not labels:
            continue
        vals = [m.group(1) for m in _label_re(labels).finditer(text)]
        if vals:
            raw.setdefault(field, [])
            raw[field].extend(vals)
    for pat in params.get("prose_patterns", []):
        for m in re.finditer(pat, text, re.I | re.M):
            for k, v in m.groupdict().items():
                if v is not None:
                    raw.setdefault(k, [])
                    raw[k].append(v.strip())
    f = state["fields"]
    for k in ("vendor", "invoice_number", "date", "currency"):
        if raw.get(k) and f.get(k) is None:
            f[k] = raw[k][0].strip().rstrip(".")
    for k in ("subtotal", "tax", "total"):
        if raw.get(k) and f.get(k) is None:
            f[k] = raw[k][0]
    if params.get("guess_missing", False):
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        if f.get("vendor") is None and lines:
            f["vendor"] = lines[0][:60]
        if f.get("invoice_number") is None:
            m = re.search(r"\b[A-Z]{1,4}-?\d{3,}\b", text)
            if m:
                f["invoice_number"] = m.group(0)
    return state
