"""Final report rendering (Markdown + JSON). Claims are bounded by the evidence collected."""

from __future__ import annotations

from typing import Any


def _fmt(v: Any, digits: int = 4) -> str:
    if isinstance(v, float):
        return f"{v:.{digits}g}"
    return str(v)


def _ci(iv) -> str:
    return f"[{iv[0]:.3f}, {iv[1]:.3f}]" if iv else "—"


def render_markdown(r: dict[str, Any]) -> str:
    spec = r["spec"]
    rec = r["recommended"]
    hold = r["holdout"].get(rec["id"], {}) if rec else {}
    L: list[str] = []
    L.append(f"# SkillForge report — {spec['name']}")
    L.append("")
    L.append(f"- Run: `{r['run_id']}`  seed: `{r['seed']}`  provider: `{r['provider']}`")
    L.append(f"- Sandbox: `{r['sandbox']['backend']}` ({r['sandbox']['strength']})")
    t = r.get("target", {})
    L.append(f"- **Status: {'CERTIFIED' if r.get('certified') else 'NOT CERTIFIED'}** against target: zero hard "
             f"violations, all constraints met, holdout pass-rate lower 95% bound >= {t.get('min_pass_lower_bound')}")
    L.append(f"- Stop reason: {r['stop_reason']}; generations run: {len(r['generations']) - 1}; diversity restarts: "
             f"{len(r.get('restarts', []))}; holdout attempts: {len(r.get('holdout_attempts', []))}")
    L.append(f"- Spend: ${r['spend']['usd']:.4f} over {r['spend']['calls']} LLM calls"
             + (" (**simulated** — mock provider pricing estimate, no money was spent)" if r["simulated"] else ""))
    L.append(f"- Holdout commitment (sha256 at creation): `{r['holdout_commitment'][:16]}…`; opened once: "
             f"{r['holdout_opened_once']}")
    L.append("")
    if r["simulated"]:
        L.append("> **Important:** LLM-backed candidates were executed against a *simulated* model whose error rates "
                 "are assumptions. Quality numbers for LLM stages are not evidence about any real model. "
                 "Deterministic stages were executed for real in the sandbox.")
        L.append("")
    L.append("## 1. Specification")
    L.append(f"{spec['summary']}")
    L.append("")
    L.append("Hard invariants (feasibility constraints): " +
             ", ".join(f"`{i['id']}`" for i in spec["invariants"] if i["hard"]))
    L.append("")
    L.append("Constraints: " + ", ".join(f"`{c['id']}`={c.get('value')}" for c in spec["constraints"]))
    L.append("")
    L.append("Objectives (Pareto, no weighted sum): " +
             ", ".join(f"{o['metric']} ({o['direction']})" for o in spec["objectives"]))
    L.append("")
    L.append("## 2. Test worlds")
    L.append("| split | scenarios | families | origin |")
    L.append("|---|---|---|---|")
    for split, info in r["worlds"].items():
        L.append(f"| {split} | {info['n']} | {info['families']} | {info['origins']} |")
    L.append("")
    L.append("Realism labels per family: " + ", ".join(f"{k}={v}" for k, v in r["family_realism"].items()))
    L.append("")
    L.append("## 3. Search history")
    L.append("| gen | pop | feasible | front | best train pass | best feasible | mean | ε | train pool | +adv | spend $ |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|")
    gens = r["generations"]
    shown = gens if len(gens) <= 40 else gens[:10] + [None] + gens[-29:]
    for g in shown:
        if g is None:
            L.append("| … | | | | | | | | | | |")
            continue
        L.append(f"| {g['generation']} | {g['population']} | {g['feasible']} | {g['pareto_size']} | "
                 f"{g['best_pass_rate']:.3f} | {g['best_feasible_pass_rate']:.3f} | {g['mean_pass_rate']:.3f} | "
                 f"{g.get('epsilon', 0):.4f} | {g['train_pool_size']} | {g['adversarial_added']} | {g['spend_usd']:.3f} |")
    if r.get("restarts"):
        L.append("")
        L.append(f"Diversity restarts (stagnation) at generations: {r['restarts']}")
    L.append("")
    L.append(f"Candidates evaluated: {r['n_candidates']}. Rejections: {r['rejections']}.")
    L.append(f"Mutation operators that produced improvements: {r['op_effectiveness']}")
    L.append("")
    L.append("## 4. Adversarial co-evolution")
    L.append(f"Probes tried: {r['adversary']['probes']}, failure-inducing cases added to train: "
             f"{r['adversary']['found']}. Most effective perturbations: {r['adversary']['effective']}. "
             "The adversary never had access to the holdout split.")
    L.append("")
    L.append("## 5. Validation gate (candidates that were strictly feasible on train)")
    L.append("| candidate | architecture | feasible(val) | pass(val) | 95% CI | robustness | metamorphic | "
             "cost/item | p95 ms | differential disagreement |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for f in r["finalists"][-25:]:
        m = f["validation"]["metrics"]
        L.append(f"| `{f['id']}` {f['label']} | {f['architecture']} | {f['validation']['feasible']} | "
                 f"{m['pass_rate']:.3f} | {_ci(f['validation']['intervals'].get('pass_rate'))} | "
                 f"{m['robustness']:.3f} | {m.get('metamorphic_consistency', float('nan')):.3f} | "
                 f"{m['cost_per_item_usd']:.5f} | {m['latency_ms_p95']:.0f} | "
                 f"{r['differential'].get(f['id'], float('nan')):.3f} |")
    L.append("")
    L.append("Validation Pareto set: " + ", ".join(f"`{x}`" for x in r["validation_pareto"]))
    L.append("")
    L.append("## 6. Independent evaluation of the exported skill (sealed holdout, evaluated once)")
    if rec:
        m, iv = hold["metrics"], hold["intervals"]
        L.append(f"**Recommended:** `{rec['id']}` — {rec['label']} (`{rec['architecture']}`), chosen on validation by "
                 f"{r['selection_rule']}.")
        L.append("")
        L.append("| metric | value | 95% interval | evidence |")
        L.append("|---|---|---|---|")
        ev = {mm["id"]: mm["evidence"] for mm in spec["metrics"]}
        for k in ("pass_rate", "robustness", "field_accuracy", "status_accuracy", "issue_f1", "security",
                  "metamorphic_consistency", "hard_violation_rate", "error_rate", "cost_per_item_usd",
                  "tokens_per_item", "latency_ms_p50", "latency_ms_p95", "complexity", "portability"):
            if k in m:
                L.append(f"| {k} | {_fmt(m[k])} | {_ci(iv.get(k))} | {ev.get(k, 'measured')} |")
        L.append("")
        L.append(f"Hard invariant violations on holdout: {hold['hard_violations'] or 'none'}")
        L.append("")
        L.append("Per family (holdout):")
        L.append("")
        L.append("| family | realism | n | pass | 95% CI | hard viol. |")
        L.append("|---|---|---|---|---|---|")
        for fam, d in hold["per_family"].items():
            L.append(f"| {fam} | {r['family_realism'].get(fam, '?')} | {d['n']} | {d['pass_rate']:.3f} | "
                     f"[{d['ci_low']:.2f}, {d['ci_high']:.2f}] | {d['hard_violations']} |")
        L.append("")
        gap = r.get("generalization_gap", {})
        L.append(f"Generalization: train {gap.get('train', 0):.3f} → validation {gap.get('validation', 0):.3f} → "
                 f"holdout {gap.get('holdout', 0):.3f} (pass rate). Drift family (never seen during search): "
                 f"{gap.get('drift', 'n/a')}.")
        L.append("")
        L.append("All finalists on holdout (reported for transparency; NOT used for selection):")
        L.append("")
        L.append("| candidate | pass | 95% CI | hard violations | cost/item |")
        L.append("|---|---|---|---|---|")
        for cid, h in r["holdout"].items():
            L.append(f"| `{cid}` | {h['metrics']['pass_rate']:.3f} | {_ci(h['intervals']['pass_rate'])} | "
                     f"{sum(h['hard_violations'].values())} | {h['metrics']['cost_per_item_usd']:.5f} |")
        L.append("")
    L.append("### Certification attempts (each holdout set opened exactly once)")
    L.append("| attempt | candidate | n | pass | 95% CI | hard violations | result | holdout sha256 |")
    L.append("|---|---|---|---|---|---|---|---|")
    for a in r.get("holdout_attempts", []):
        res = "CERTIFIED" if a.get("passed") else ("report only" if a.get("report_only") else
                                                   "failed → retired into train")
        L.append(f"| {a['attempt']} | `{a['candidate_id']}` | {a['n']} | {a['pass_rate']:.3f} | "
                 f"{_ci(a['pass_ci'])} | {sum(a['hard_violations'].values())} | {res} | `{a['commitment'][:12]}` |")
    L.append("")
    L.append("## 7. Acceptance criteria (recommended candidate, holdout)")
    L.append("| criterion | metric | required | observed (bound used) | result |")
    L.append("|---|---|---|---|---|")
    for a in r["acceptance"]:
        L.append(f"| {a['id']} | {a['metric']} | {a['op']} {a['threshold']} | {a['observed']:.4f} ({a['bound']}) | "
                 f"{'PASS' if a['passed'] else 'FAIL'} |")
    L.append("")
    L.append("## 8. Limitations and bounded claims")
    for lim in r["limitations"]:
        L.append(f"- {lim}")
    L.append("")
    L.append("## 9. Operating cost estimate")
    if rec:
        m = hold["metrics"]
        L.append(f"Per document: ${m['cost_per_item_usd']:.5f}, {m['tokens_per_item']:.0f} tokens, "
                 f"{m['llm_calls_per_item']:.2f} LLM calls, p95 latency {m['latency_ms_p95']:.0f} ms. "
                 f"Per 10,000 documents: ${m['cost_per_item_usd'] * 10000:.2f}"
                 + (" (simulated pricing)." if r["simulated"] else "."))
    L.append("")
    L.append("## 10. Artifacts")
    for a in r["artifacts"]:
        L.append(f"- `{a}`")
    L.append("")
    return "\n".join(L)
