"""Environment: extraction of line items from a degraded manifest document.

An invented document format with built-in arithmetic redundancy, passed through a character-level
degradation channel (look-alike confusions, digit substitutions, lost / doubled characters, wrapped and
merged rows). The hidden truth is the set of records the document was rendered from, so the checker is a
plain comparison with generation-time data: this module contains NO parser, NO repair logic and NO solver.
"""

from __future__ import annotations

import random
from typing import Any

from .base import CheckResult, Family, Instance, TaskEnvironment

CPU_LIMIT_S = 1.0
LOOKALIKE = {"0": "O", "1": "l", "5": "S", "8": "B", "2": "Z", "6": "G"}
LETTER_LOOKALIKE = {"O": "0", "I": "1", "S": "5", "B": "8", "Z": "2", "G": "6"}
DIGIT_SUBST = {"0": "68", "1": "74", "2": "7", "3": "89", "4": "91", "5": "63", "6": "50", "7": "12", "8": "30",
               "9": "43"}

SPEC = f"""
TASK: Recover the line items of a shipping manifest from a degraded text copy of the document.

INPUT (state["input"]): {{"text": "<the document, lines separated by \\n>"}}

THE ORIGINAL (undamaged) DOCUMENT has this layout:
  MANIFEST <id>
  COLUMNS: <four column names CODE, QTY, UNIT, AMOUNT in the order used by this document, separated like rows>
  == SECTION <name> ==
  <one row per line item: the four fields in the column order, separated by the document's separator>
  ...
  SUBTOTAL <money>                      (sum of AMOUNT over the rows of that section)
  == SECTION <name> ==  ...             (1 to 6 sections)
  TOTAL <money> ITEMS <number of line items in the whole document>
  * Separator: one of " | ", ";", a tab, or three spaces - the same throughout a document.
  * CODE: two capital letters followed by five digits. The fifth digit is a check digit equal to
    (1*d1 + 2*d2 + 3*d3 + 4*d4 + p1 + p2) mod 10, where d1..d4 are the first four digits and p1, p2 are the
    alphabet positions of the two letters (A=1 ... Z=26).
  * QTY: an integer 1..500, written plainly or followed by " pcs" (same style throughout a document).
  * UNIT: unit price, money. AMOUNT: QTY * UNIT, money.
  * Money always has exactly two decimals. The decimal mark is "." or "," and thousands are either not
    grouped or grouped with an apostrophe (1'234.50) - the same style throughout a document.

DEGRADATION that may have been applied to the copy you receive:
  * inside CODE, QTY, UNIT, AMOUNT, SUBTOTAL and TOTAL values, individual characters may be damaged:
      - a digit shown as a look-alike letter: 0->O, 1->l, 5->S, 8->B, 2->Z, 6->G;
      - a code LETTER shown as a look-alike digit: O->0, I->1, S->5, B->8, Z->2, G->6;
      - a digit misread as another digit: 0->6/8, 1->7/4, 2->7, 3->8/9, 4->9/1, 5->6/3, 6->5/0, 7->1/2,
        8->3/0, 9->4/3;
      - a digit lost, or a digit printed twice in a row;
      - the decimal mark of a money value lost;
  * a row may be wrapped: broken after one of its separators and continued on the next line;
  * two consecutive rows of a section may be merged onto one line (joined by the separator);
  * unrelated lines such as "--- page 2 ---" may be inserted anywhere.
  Keywords (MANIFEST, COLUMNS, SECTION, SUBTOTAL, TOTAL, ITEMS, column names) are never damaged. Any value
  may be damaged, including subtotals, the total and the item count; most values are intact.

OUTPUT (set state["output"]):
  {{"items": [{{"code": "<AA99999>", "qty": <int>, "unit_cents": <int>}}, ...]}}
  the line items of the ORIGINAL document, in document order, unit price in cents.

SCORING: with n = number of true items and m = number of items you return, the score is
  (number of correct fields among code / qty / unit_cents of your item i versus true item i, for i < min(n, m))
  / (3 * max(n, m)).   Items are compared by position. A malformed output scores 0.

LIMITS: at most {CPU_LIMIT_S} second of CPU time per document (measure with time.process_time(); exceeding
about twice that aborts the document with score 0). The skill must be deterministic code: language-model
nodes are not available at run time for this task. Documents have 6 to 60 line items.
"""


def _money(cents: int, dec: str, group: bool) -> str:
    whole, frac = divmod(cents, 100)
    w = str(whole)
    if group and len(w) > 3:
        parts = []
        while w:
            parts.append(w[-3:])
            w = w[:-3]
        w = "'".join(reversed(parts))
    return f"{w}{dec}{frac:02d}"


def _code(rng: random.Random) -> str:
    letters = [rng.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(2)]
    d = [rng.randrange(10) for _ in range(4)]
    chk = (d[0] + 2 * d[1] + 3 * d[2] + 4 * d[3] + sum(ord(c) - 64 for c in letters)) % 10
    return "".join(letters) + "".join(map(str, d)) + str(chk)


def _damage(s: str, rng: random.Random, p: dict[str, float], stats: dict[str, int]) -> str:
    out = []
    for ch in s:
        r = rng.random()
        if ch.isdigit():
            if r < p["look"] and ch in LOOKALIKE:
                out.append(LOOKALIKE[ch]); stats["look"] += 1
            elif r < p["look"] + p["subst"]:
                out.append(rng.choice(DIGIT_SUBST[ch])); stats["subst"] += 1
            elif r < p["look"] + p["subst"] + p["drop"]:
                stats["drop"] += 1
            elif r < p["look"] + p["subst"] + p["drop"] + p["dup"]:
                out.append(ch + ch); stats["dup"] += 1
            else:
                out.append(ch)
        elif ch.isalpha() and ch.isupper() and ch in LETTER_LOOKALIKE and r < p["look"]:
            out.append(LETTER_LOOKALIKE[ch]); stats["look"] += 1
        elif ch in ".," and r < p["dec"]:
            stats["dec"] += 1
        else:
            out.append(ch)
    return "".join(out)


PROFILES = {
    "clean":         dict(look=0.004, subst=0.004, drop=0.002, dup=0.002, dec=0.01, wrap=0.02, merge=0.0, junk=0.02),
    "lookalike":     dict(look=0.09, subst=0.004, drop=0.002, dup=0.002, dec=0.02, wrap=0.03, merge=0.02, junk=0.04),
    "misread":       dict(look=0.01, subst=0.06, drop=0.004, dup=0.004, dec=0.02, wrap=0.03, merge=0.02, junk=0.04),
    "lost_chars":    dict(look=0.01, subst=0.01, drop=0.04, dup=0.03, dec=0.10, wrap=0.03, merge=0.02, junk=0.04),
    "broken_layout": dict(look=0.02, subst=0.02, drop=0.01, dup=0.01, dec=0.03, wrap=0.22, merge=0.15, junk=0.12),
    "heavy":         dict(look=0.06, subst=0.05, drop=0.03, dup=0.02, dec=0.08, wrap=0.12, merge=0.08, junk=0.08),
    "long_heavy":    dict(look=0.07, subst=0.06, drop=0.035, dup=0.025, dec=0.10, wrap=0.15, merge=0.10, junk=0.10),
}


class DocRepairEnv(TaskEnvironment):
    name = "degraded_manifest_extraction"
    version = "1"
    allow_llm_nodes = False

    def public_spec(self) -> str:
        return SPEC

    def public_examples(self) -> list[dict[str, Any]]:
        return [{"input": {"text": "MANIFEST M-1042\nCOLUMNS: CODE;QTY;UNIT;AMOUNT\n== SECTION A ==\n"
                                   "KD30517;12;4.50;54.00\nQA11207;3;1O.00;30.00\nSUBTOTAL 84.00\nTOTAL 84.00 ITEMS 2"},
                 "output": {"items": [{"code": "KD30517", "qty": 12, "unit_cents": 450},
                                      {"code": "QA11207", "qty": 3, "unit_cents": 1000}]},
                 "note": "format illustration only"}]

    def families(self) -> list[Family]:
        return [Family("clean", 0.6, "almost undamaged documents"),
                Family("lookalike", 1.0, "many digit/letter look-alike confusions"),
                Family("misread", 1.4, "digits misread as other digits"),
                Family("lost_chars", 1.2, "lost and doubled digits, lost decimal marks"),
                Family("broken_layout", 1.2, "wrapped and merged rows, inserted lines"),
                Family("heavy", 1.4, "all kinds of damage together"),
                Family("long_heavy", 1.0, "long documents with heavy damage", audit_only=True)]

    def generate(self, family: str, rng: random.Random, instance_id: str) -> Instance:
        p = PROFILES[family]
        n_items = rng.randint(30, 60) if family == "long_heavy" else rng.randint(6, 34)
        n_sec = max(1, min(6, n_items // rng.randint(4, 9)))
        sep = rng.choice([" | ", ";", "\t", "   "])
        dec = rng.choice(".,")
        if sep == ";" and dec == ",":
            dec = "."
        group = rng.random() < 0.4
        pcs = rng.random() < 0.4
        cols = ["CODE", "QTY", "UNIT", "AMOUNT"]
        rng.shuffle(cols)
        price_scale = rng.choice([500, 5000, 60000])
        items = [{"code": _code(rng), "qty": rng.choice([rng.randint(1, 9), rng.randint(1, 60), rng.randint(1, 500)]),
                  "unit_cents": rng.randint(5, price_scale)} for _ in range(n_items)]
        cuts = sorted(rng.sample(range(1, n_items), n_sec - 1)) if n_sec > 1 else []
        bounds = [0] + cuts + [n_items]
        stats = {k: 0 for k in ("look", "subst", "drop", "dup", "dec", "wrap", "merge", "junk")}

        def dmg(s: str) -> str:
            return _damage(s, rng, p, stats)

        lines = [f"MANIFEST M-{rng.randint(1000, 9999)}", "COLUMNS: " + sep.join(cols)]
        page = 1
        for si in range(n_sec):
            lines.append(f"== SECTION {chr(65 + si)} ==")
            rows = []
            for it in items[bounds[si]:bounds[si + 1]]:
                f = {"CODE": dmg(it["code"]), "QTY": dmg(str(it["qty"])) + (" pcs" if pcs else ""),
                     "UNIT": dmg(_money(it["unit_cents"], dec, group)),
                     "AMOUNT": dmg(_money(it["qty"] * it["unit_cents"], dec, group))}
                rows.append([f[c] for c in cols])
            k = 0
            while k < len(rows):
                if k + 1 < len(rows) and rng.random() < p["merge"]:
                    lines.append(sep.join(rows[k] + rows[k + 1])); stats["merge"] += 1
                    k += 2
                    continue
                if rng.random() < p["wrap"]:
                    cut = rng.randint(1, 3)
                    lines.append(sep.join(rows[k][:cut]) + sep.rstrip(" ") if sep.strip() else sep.join(rows[k][:cut]))
                    lines.append(sep.join(rows[k][cut:])); stats["wrap"] += 1
                else:
                    lines.append(sep.join(rows[k]))
                if rng.random() < p["junk"]:
                    page += 1
                    lines.append(rng.choice([f"--- page {page} ---", "(continued)", f"printed {rng.randint(1, 28)}/0{rng.randint(1, 9)}"]))
                    stats["junk"] += 1
                k += 1
            sub = sum(it["qty"] * it["unit_cents"] for it in items[bounds[si]:bounds[si + 1]])
            lines.append("SUBTOTAL " + dmg(_money(sub, dec, group)))
        total = sum(it["qty"] * it["unit_cents"] for it in items)
        lines.append("TOTAL " + dmg(_money(total, dec, group)) + " ITEMS " + dmg(str(n_items)))
        return Instance(instance_id, family, {"text": "\n".join(lines)},
                        {"items": items, "damage": stats}, [family])

    def check(self, instance: Instance, output: Any) -> CheckResult:
        truth = instance.hidden["items"]
        if not isinstance(output, dict) or not isinstance(output.get("items"), list):
            return CheckResult(False, 0.0, ["malformed_output"], "output must be {\"items\": [...]}")
        got = output["items"]
        n, m = len(truth), len(got)
        ok = 0
        wrong = {"code": 0, "qty": 0, "unit_cents": 0}
        for t, g in zip(truth, got):
            if not isinstance(g, dict):
                for k in wrong:
                    wrong[k] += 1
                continue
            for k in wrong:
                v = g.get(k)
                if isinstance(v, bool) or type(v) is not type(t[k]) or v != t[k]:
                    wrong[k] += 1
                else:
                    ok += 1
        score = ok / (3 * max(n, m)) if max(n, m) else 0.0
        viol = []
        if m != n:
            viol.append("item_count_mismatch")
        msg = (f"returned {m} items, document has {n}; " if m != n else "item count correct; ") + \
              f"wrong fields among compared items: code {wrong['code']}, qty {wrong['qty']}, unit_cents {wrong['unit_cents']}"
        return CheckResult(True, score, viol, msg, {"wrong": wrong})
