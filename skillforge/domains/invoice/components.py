"""Building-block library for invoice candidates.

Each block is *source code* (``def step(state, params)``) that runs only
inside the ExecutionSandbox. Parameters are data and are what the evolution
engine mutates; LLM-backed synthesis may also emit brand-new blocks, which go
through the same static validation + sandbox path.

State convention:
    state["input"]  -> {"text": str, "context": {"seen_invoice_ids": [...]}}
    state["text"]   -> normalized working text
    state["fields"] -> {vendor, invoice_number, date, currency, subtotal, tax, total}
    state["raw"]    -> raw label matches, e.g. raw["total"] = ["110.00", "120.00"]
    state["items"]  -> [{description, quantity, unit_price, amount}]
    state["issues"] -> [str]
    state["output"] -> final output dict (set by finalize)
"""

NORMALIZE = r'''
import re, unicodedata

def step(state, params):
    t = (state.get("input") or {}).get("text") or ""
    if params.get("nfkc", True):
        t = unicodedata.normalize("NFKC", t)
    if params.get("strip_zero_width", True):
        t = re.sub("[​‌‍⁠﻿\x0c]", "", t)
    t = t.replace("\r\n", "\n").replace("\r", "\n")
    if params.get("split_pipes", False):
        t = re.sub(r"\s+\|\s+", "\n", t)
    if params.get("split_semicolon_pairs", False):
        t = re.sub(r";\s+(?=[A-Za-z][A-Za-z ]{1,20}:)", "\n", t)
    t = "\n".join(l.rstrip() for l in t.split("\n"))
    state["text"] = t
    state.setdefault("fields", {})
    state.setdefault("raw", {})
    state.setdefault("items", [])
    state.setdefault("issues", [])
    state.setdefault("flags", {})
    return state
'''

INJECTION_GUARD = r'''
import re

def step(state, params):
    pats = [re.compile(p, re.I) for p in params.get("patterns", [])]
    kept, hit = [], False
    for line in state.get("text", "").split("\n"):
        if any(p.search(line) for p in pats):
            hit = True
            continue
        kept.append(line)
    state["text"] = "\n".join(kept)
    if hit:
        state["flags"]["injection"] = True
        if "injection_detected" not in state["issues"]:
            state["issues"].append("injection_detected")
    return state
'''

EXTRACT_FIELDS = r'''
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
'''

PARSE_VALUES = r'''
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
'''

EXTRACT_ITEMS = r'''
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
'''

CHECK_ARITHMETIC = r'''
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
'''

DETECT_CONFLICTS = r'''
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
'''

CHECK_DUPLICATE = r'''
def step(state, params):
    inv = state["fields"].get("invoice_number")
    seen = ((state.get("input") or {}).get("context") or {}).get("seen_invoice_ids") or []
    if inv is not None:
        norm = (lambda s: str(s).strip().upper()) if params.get("case_insensitive", True) else str
        if norm(inv) in {norm(s) for s in seen}:
            if "duplicate_invoice" not in state["issues"]:
                state["issues"].append("duplicate_invoice")
    return state
'''

GROUND_CHECK = r'''
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
'''

CONFIDENCE_GATE = r'''
def step(state, params):
    f = state.get("fields") or {}
    need = [k for k in params.get("required", ["vendor", "invoice_number", "date", "total"]) if f.get(k) is None]
    weak = (not state.get("items")) and params.get("require_items", True)
    weak = weak or ("unparseable_date" in state.get("issues", []))
    state["needs_llm"] = bool(need or weak) and not state.get("flags", {}).get("total_conflict")
    return state
'''

PARSE_LLM_JSON = r'''
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
'''

FINALIZE = r'''
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
'''

LIBRARY = {
    "normalize": NORMALIZE,
    "injection_guard": INJECTION_GUARD,
    "extract_fields": EXTRACT_FIELDS,
    "parse_values": PARSE_VALUES,
    "extract_items": EXTRACT_ITEMS,
    "check_arithmetic": CHECK_ARITHMETIC,
    "detect_conflicts": DETECT_CONFLICTS,
    "check_duplicate": CHECK_DUPLICATE,
    "ground_check": GROUND_CHECK,
    "confidence_gate": CONFIDENCE_GATE,
    "parse_llm_json": PARSE_LLM_JSON,
    "finalize": FINALIZE,
}

# ----- domain knowledge pools the mutation operators draw from -----
LABEL_POOL = {
    "vendor": ["Vendor", "From", "Seller", "Supplier", "Company", "Billed by"],
    "invoice_number": ["Invoice No", "Invoice #", "Invoice Number", "Invoice", "Inv #", "Invoice ID"],
    "date": ["Date", "Invoice Date", "Issue date", "Dated"],
    "currency": ["Currency", "Ccy"],
    "subtotal": ["Subtotal", "Sub-total", "Net"],
    "tax": ["Tax", "Sales Tax", "VAT", "GST"],
    "total": ["Total", "Amount Due", "Total Due", "Grand Total", "Balance Due", "Please pay", "Balance"],
}
DATE_FORMAT_POOL = ["%Y-%m-%d", "%d.%m.%Y", "%B %d, %Y", "%d %b %Y", "%Y/%m/%d", "%b %d, %Y", "%d %B %Y"]
ITEM_PATTERN_POOL = [
    r"^\s*-\s*(?P<desc>.+?)\s+x(?P<qty>\d+)\s+@\s+(?P<price>\S+)\s+=\s+(?P<amount>\S+)\s*$",
    r"^\s*(?P<desc>[^|]+?)\s*\|\s*(?P<qty>\d+)\s*\|\s*(?P<price>[^|]+?)\s*\|\s*(?P<amount>[^|]+?)\s*$",
    r"^\s*(?P<qty>\d+)\s+x\s+(?P<desc>.+?)\s+at\s+(?P<price>\S+)\s+each:\s+(?P<amount>\S+)\s*$",
    r"^\s*(?P<desc>[^;]+);(?P<qty>\d+);(?P<price>[^;]+);(?P<amount>[^;]+)$",
]
PROSE_PATTERN_POOL = [
    r"(?:invoice|credit note)\s+(?P<invoice_number>[A-Z0-9][A-Z0-9-]*\d)\b",
    r"\bfrom\s+(?P<vendor>[A-Z][\w&.\- ]+?)(?=\s+issued|\.|,)",
    r"issued on\s+(?P<date>[A-Za-z0-9 ,./]+?)\.",
    r"Subtotal\s+(?P<subtotal>-?[^\s,]+(?:,\d{3})*(?:\.\d+)?)",
    r",\s*tax\s+(?P<tax>-?[^\s,]+(?:,\d{3})*(?:\.\d+)?)",
    r"total due\s+(?P<total>-?[^\s,(]+(?:,\d{3})*(?:\.\d+)?)",
    r"\((?P<currency>USD|EUR|GBP)\)",
]
INJECTION_PATTERN_POOL = [
    r"ignore (all |any )?(previous|prior|above) instructions",
    r"\bassistant\b",
    r"approv(e|al) (this|the)? ?(payment|invoice)|pre-approved|approval mode",
    r"set status to",
    r"disregard",
    r"you are now",
    r"<!--.*-->",
    r"^\s*(system|note to ai)\s*:",
]
