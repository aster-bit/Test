"""Aggregate replicate results of a pre-registered experiment into REPORT.md (verdicts per prereg rules).

    python -m skillforge.exp_report --name sched-v1
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROLES = ["A", "B_best", "C_initial", "C_final", "H_manual", "D_direct"]
ROLE_DESC = {"A": "one-shot (first program, as written)", "B_best": "best of independent generations",
             "C_initial": "best from-scratch program inside arm C", "C_final": "evolutionary synthesis champion",
             "H_manual": "hand-written baseline skill", "D_direct": "direct Claude call per instance"}


def verdict(flags: list[bool]) -> str:
    if not flags:
        return "NOT TESTED"
    return "SUPPORTED" if all(flags) else "MIXED" if any(flags) else "NOT SUPPORTED"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    a = ap.parse_args(argv)
    d = ROOT / "experiments" / a.name
    pre = json.loads((d / "prereg.json").read_text())
    reps = []
    for r in range(pre["replicates"]):
        p = d / f"rep{r}" / "results.json"
        if p.exists():
            reps.append((r, json.loads(p.read_text()), json.loads((d / f"rep{r}" / "lineage.json").read_text())))
    L = [f"# Experiment `{a.name}` — results", "",
         f"Pre-registered {pre['created']} (environment sha256 `{pre['environment']['source_sha256'][:12]}`, audit split "
         f"sha256 `{pre['splits']['audit']['sha256'][:12]}`). Replicates completed: {len(reps)}/{pre['replicates']}. "
         f"Provider: {reps[0][1]['provider'] if reps else 'n/a'}.", "",
         f"Score = hidden lower bound / achieved objective for valid answers (1.0 is generally unattainable; the bound is "
         f"loose, so absolute values are low and only differences are meaningful), 0 for invalid answers; audit set: "
         f"{pre['splits']['audit']['n']} instances incl. audit-only families.", "",
         "## Audit results (sealed set, each candidate evaluated once)", ""]
    for r, res, _ in reps:
        L += [f"### Replicate {r}", "", "| role | what | mean score | 95% CI | valid | cost/item $ | p95 ms |", "|---|---|---|---|---|---|---|"]
        for role in ROLES:
            s = res["audit"].get(role)
            if s:
                L.append(f"| {role} | {ROLE_DESC[role]} | {s['mean_score']:.4f} | [{s['mean_ci95'][0]:.4f}, {s['mean_ci95'][1]:.4f}] | "
                         f"{s['valid_rate']:.3f} | {s['cost_per_item_usd']:.5f} | {s['latency_ms_p95']:.0f} |")
        L.append("")
    L += ["## Pre-registered hypotheses", "", "| hypothesis | " + " | ".join(f"rep{r}: diff [99% CI]" for r, _, _ in reps) + " | verdict |",
          "|---|" + "---|" * (len(reps) + 1)]
    for h in ("H1_primary", "H2", "H3", "H4", "H5"):
        cells, flags = [], []
        for _, res, _ in reps:
            c = res["hypotheses"].get(h)
            if c:
                cells.append(f"{c['mean_diff']:+.4f} [{c['ci99_bonferroni'][0]:+.4f}, {c['ci99_bonferroni'][1]:+.4f}] {'✔' if c['holds'] else '✘'}")
                flags.append(bool(c["holds"]))
            else:
                cells.append("n/a")
        comp = next((res["hypotheses"][h]["comparison"] for _, res, _ in reps if h in res["hypotheses"]), "")
        L.append(f"| {h} ({comp}) | " + " | ".join(cells) + f" | **{verdict(flags)}** |")
    h1 = verdict([bool(res["hypotheses"].get("H1_primary", {}).get("holds")) for _, res, _ in reps])
    L += ["", f"**Stage success criterion (H1 in every replicate): {'MET' if h1 == 'SUPPORTED' else 'NOT MET'}** ({h1}).", "",
          "## What the search actually did", ""]
    for r, res, lin in reps:
        byid = {c["id"]: c for c in lin}
        nv = res["novelty"]
        L += [f"### Replicate {r}", "",
              f"- Synthesis calls: B={res['synthesis']['B']['calls']}, C={res['synthesis']['C']['calls']}; "
              f"synthesis cost: B=${res['synthesis']['B']['cost_usd']:.2f}, C=${res['synthesis']['C']['cost_usd']:.2f}.",
              f"- Usable programs: B {nv['B']['usable']}/{nv['B']['candidates']}, C {nv['C']['usable']}/{nv['C']['candidates']}.",
              f"- New algorithms by AST measure (similarity to all earlier < 0.60): B {len(nv['B']['new_algorithms'])}, "
              f"C {len(nv['C']['new_algorithms'])}.",
              f"- C variation steps: {nv['C_mutations']['total']} (usable {nv['C_mutations']['usable']}, structural "
              f"{nv['C_mutations']['structural']}, significant improvements over parent on SELECTION "
              f"{nv['C_mutations']['significant_improvements']}, of which structural "
              f"{nv['C_mutations']['significant_and_structural']}); expansions {nv['C_mutations']['expansions']} "
              f"(usable {nv['C_mutations']['expansions_usable']}).",
              f"- Operations named by the generator itself: {', '.join(nv.get('C_operations_named_by_generator', [])) or '—'}.",
              f"- C_initial → C_final structure: {json.dumps(res['C_initial_vs_final_structure'])}",
              f"- Max AST similarity of C_final to any arm-B program: {nv.get('C_final_max_ast_similarity_to_any_B')}",
              "", "| step | move | parents | usable | Δ selection vs parent [95% CI] | structural | best so far |", "|---|---|---|---|---|---|---|"]
        for i, s in enumerate(res["c_trace"], 1):
            dlt = (f"{s['delta_vs_parent']:+.4f} [{s['ci95'][0]:+.4f}, {s['ci95'][1]:+.4f}]" + (" ✔" if s.get("significant") else "")) \
                if "delta_vs_parent" in s else "—"
            L.append(f"| {i} | {s['move']} | {', '.join(s.get('parents') or []) or '—'} | {s['usable']} | {dlt} | "
                     f"{s.get('structural', '—')} | {s['best_selection_after']:.4f} |")
        L += ["", "Champion strategies (generator's own description):", ""]
        for role, cid in res["champions"].items():
            if cid and cid in byid:
                c = byid[cid]
                L.append(f"- **{role}** `{cid}` ({c['origin']}; graph `{c.get('graph_signature')}`; "
                         f"selection {c.get('selection', {}).get('mean_score', float('nan')):.4f}): {c['strategy'][:420]}")
        be = res.get("break_even_runs_vs_direct")
        L += ["", f"- Cost: direct Claude ${res['direct_cost_per_item']:.4f}/instance vs synthesised skill "
                  f"${res['skill_cost_per_item']:.5f}/instance; synthesis of arm C cost ${res['synthesis']['C']['cost_usd']:.2f} "
                  f"→ break-even after {be:.0f} instances." if be else "- Break-even vs direct Claude: n/a.", ""]
    (d / "REPORT.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
