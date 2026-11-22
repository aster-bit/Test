# params: {"nfkc": true, "strip_zero_width": true, "split_pipes": false}
# origin: library, role: code
import re, unicodedata

def step(state, params):
    t = (state.get("input") or {}).get("text") or ""
    if params.get("nfkc", True):
        t = unicodedata.normalize("NFKC", t)
    if params.get("strip_zero_width", True):
        t = re.sub("[​‌‍⁠﻿\x0c]", "", t)
    t = t.replace("\r\n", "\n").replace("\r", "\n")
    if params.get("split_pipes", False):
        # split "Label: value | Label: value" header lines only; never table rows ("a | 2 | 1.00 | 2.00")
        t = "\n".join(re.sub(r"\s+\|\s+", "\n", l) if re.search(r"[A-Za-z#][^|:]{0,25}:\s*\S", l) and " | " in l
                      else l for l in t.split("\n"))
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
