# SkillForge report — invoice-processor

- Run: `run-20261122-232520-308a40`  seed: `7`  provider: `mock`
- Sandbox: `subprocess` (namespace+rlimits+audit-hook)
- **Status: CERTIFIED** against target: zero hard violations, all constraints met, holdout pass-rate lower 95% bound >= 0.95
- Stop reason: target_reached; generations run: 15; diversity restarts: 1; holdout attempts: 1
- Spend: $234.7696 over 57116 LLM calls (**simulated** — mock provider pricing estimate, no money was spent)
- Holdout commitment (sha256 at creation): `f476ac2b573c4022…`; opened once: True

> **Important:** LLM-backed candidates were executed against a *simulated* model whose error rates are assumptions. Quality numbers for LLM stages are not evidence about any real model. Deterministic stages were executed for real in the sandbox.

## 1. Specification
Extract structured data from invoice documents (text), validate arithmetic, detect missing/conflicting/duplicate data and decide valid | invalid | needs_review.

Hard invariants (feasibility constraints): `schema_valid`, `no_fabrication`, `valid_implies_consistent`, `never_approve_bad`

Constraints: `no_network_in_code`=None, `latency`=8000.0, `cost`=0.02, `usefulness`=0.5

Objectives (Pareto, no weighted sum): pass_rate (maximize), robustness (maximize), field_accuracy (maximize), cost_per_item_usd (minimize), latency_ms_p95 (minimize), complexity (minimize)

## 2. Test worlds
| split | scenarios | families | origin |
|---|---|---|---|
| train(final pool) | 316 | 13 | {'world': 160, 'adversary': 156} |
| validation | 150 | 13 | {'world': 150} |
| holdout#0 | 300 | 14 | {'world': 206, 'perturbed': 94} |

Realism labels per family: clean_labeled=realistic, clean_table=realistic, email_prose=realistic, format_variants=realistic, zero_tax_single_item=realistic, arithmetic_error=realistic, missing_field=realistic, conflicting_totals=synthetic, ocr_truncated=synthetic, very_long=synthetic, duplicate_submission=synthetic, credit_note=realistic, prompt_injection=adversarial, layout_drift=synthetic (holdout-only)

## 3. Search history
| gen | pop | feasible | front | best train pass | best feasible | mean | ε | train pool | +adv | spend $ |
|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 14 | 0 | 5 | 0.931 | 0.000 | 0.649 | 0.0600 | 160 | 0 | 6.386 |
| 1 | 14 | 1 | 7 | 0.936 | 0.848 | 0.810 | 0.0384 | 171 | 11 | 17.800 |
| 2 | 14 | 3 | 5 | 0.885 | 0.852 | 0.797 | 0.0216 | 183 | 12 | 28.683 |
| 3 | 14 | 5 | 10 | 0.892 | 0.892 | 0.827 | 0.0096 | 195 | 12 | 50.968 |
| 4 | 14 | 12 | 8 | 0.922 | 0.922 | 0.832 | 0.0024 | 205 | 10 | 77.238 |
| 5 | 14 | 13 | 10 | 0.911 | 0.911 | 0.816 | 0.0000 | 213 | 8 | 115.629 |
| 6 | 14 | 13 | 12 | 0.915 | 0.915 | 0.809 | 0.0000 | 224 | 11 | 141.981 |
| 7 | 14 | 0 | 1 | 0.830 | 0.000 | 0.716 | 0.0000 | 235 | 11 | 177.148 |
| 8 | 14 | 0 | 1 | 0.769 | 0.000 | 0.643 | 0.0000 | 247 | 12 | 181.834 |
| 9 | 14 | 0 | 1 | 0.870 | 0.000 | 0.614 | 0.0600 | 247 | 0 | 191.329 |
| 10 | 14 | 0 | 1 | 0.849 | 0.000 | 0.682 | 0.0384 | 259 | 12 | 203.305 |
| 11 | 14 | 0 | 5 | 0.866 | 0.000 | 0.769 | 0.0216 | 269 | 10 | 210.670 |
| 12 | 14 | 0 | 1 | 0.872 | 0.000 | 0.710 | 0.0096 | 281 | 12 | 214.752 |
| 13 | 14 | 1 | 1 | 0.935 | 0.935 | 0.795 | 0.0024 | 293 | 12 | 220.100 |
| 14 | 14 | 3 | 2 | 0.938 | 0.938 | 0.759 | 0.0000 | 304 | 11 | 225.878 |
| 15 | 14 | 8 | 2 | 0.997 | 0.997 | 0.863 | 0.0000 | 316 | 12 | 234.172 |

Diversity restarts (stagnation) at generations: [9]

Candidates evaluated: 221. Rejections: {'infeasible': 73, 'duplicate': 25, 'dominated': 17}.
Mutation operators that produced improvements: {'llm_config': {'applied': 45, 'improved': 23}, 'toggle': {'applied': 56, 'improved': 17}, 'prompt_clause': {'applied': 34, 'improved': 12}, 'add_conflict_label': {'applied': 21, 'improved': 11}, 'insert_block': {'applied': 35, 'improved': 11}, 'few_shot': {'applied': 26, 'improved': 11}, 'sync_eu': {'applied': 21, 'improved': 10}, 'tune_tolerance': {'applied': 32, 'improved': 9}, 'toggle_tools': {'applied': 26, 'improved': 9}, 'remove_block': {'applied': 23, 'improved': 8}, 'add_prose': {'applied': 14, 'improved': 5}, 'add_validators_to_llm': {'applied': 30, 'improved': 5}, 'add_label': {'applied': 15, 'improved': 4}, 'add_date_format': {'applied': 8, 'improved': 3}, 'add_item_pattern': {'applied': 6, 'improved': 2}, 'add_injection_pattern': {'applied': 2, 'improved': 2}}

## 4. Adversarial co-evolution
Probes tried: 500, failure-inducing cases added to train: 156. Most effective perturbations: {'p_inject': 77, 'p_crlf': 61, 'p_whitespace': 56, 'p_noise_lines': 55, 'p_fullwidth_colon': 54, 'p_zero_width': 53}. The adversary never had access to the holdout split.

## 5. Validation gate (candidates that were strictly feasible on train)
| candidate | architecture | feasible(val) | pass(val) | 95% CI | robustness | metamorphic | cost/item | p95 ms | differential disagreement |
|---|---|---|---|---|---|---|---|---|---|
| `cand-f5ec7b39fe` prompt-engineered+scripts [hybrid_llm_code] | hybrid_llm_code | True | 0.873 | [0.811, 0.917] | 0.877 | 0.750 | 0.00581 | 2750 | 0.053 |
| `cand-837c23bca4` prompt-engineered+scripts [hybrid_llm_code] | hybrid_llm_code | True | 0.873 | [0.811, 0.917] | 0.877 | 0.750 | 0.00581 | 2750 | 0.053 |
| `cand-5e8ab3f931` prompt-engineered+scripts [hybrid_llm_code] | hybrid_llm_code | True | 0.873 | [0.811, 0.917] | 0.877 | 0.750 | 0.00581 | 2750 | 0.053 |
| `cand-34fbbfce39` prompt-engineered+scripts [hybrid_llm_code] | hybrid_llm_code | True | 0.880 | [0.818, 0.923] | 0.868 | 0.917 | 0.01218 | 3353 | 0.067 |
| `cand-60e27c675e` prompt-engineered+scripts [hybrid_llm_code] | hybrid_llm_code | True | 0.873 | [0.811, 0.917] | 0.877 | 0.750 | 0.00581 | 2750 | 0.053 |
| `cand-9f1598e772` prompt-engineered+scripts [hybrid_llm_code] | hybrid_llm_code | True | 0.847 | [0.780, 0.896] | 0.821 | 0.917 | 0.00612 | 2767 | 0.060 |
| `cand-e2024d363e` prompt-engineered+scripts [tool_using_agent] | tool_using_agent | True | 0.867 | [0.803, 0.912] | 0.877 | 0.750 | 0.00952 | 3351 | 0.060 |
| `cand-d53353fe49` prompt-engineered+scripts [tool_using_agent] | tool_using_agent | True | 0.880 | [0.818, 0.923] | 0.877 | 1.000 | 0.00959 | 3352 | 0.040 |
| `cand-99cae998d8` prompt-engineered+scripts [tool_using_agent] | tool_using_agent | True | 0.847 | [0.780, 0.896] | 0.887 | 0.750 | 0.00670 | 3316 | 0.053 |
| `cand-ad141baec3` prompt-engineered+scripts [tool_using_agent] | tool_using_agent | True | 0.893 | [0.834, 0.933] | 0.896 | 0.917 | 0.01016 | 3359 | 0.060 |
| `cand-51a5ab6bb4` prompt-engineered+scripts [tool_using_agent] | tool_using_agent | True | 0.880 | [0.818, 0.923] | 0.877 | 1.000 | 0.00959 | 3352 | 0.053 |
| `cand-7fd19c2836` prompt-engineered+scripts [tool_using_agent] | tool_using_agent | True | 0.900 | [0.842, 0.939] | 0.868 | 0.833 | 0.01125 | 3368 | 0.053 |
| `cand-8e31ae5c07` prompt-engineered+scripts [hybrid_llm_code] | hybrid_llm_code | True | 0.887 | [0.826, 0.928] | 0.887 | 0.833 | 0.01221 | 3363 | 0.060 |
| `cand-f03dc8b39a` prompt-engineered+scripts [tool_using_agent] | tool_using_agent | True | 0.893 | [0.834, 0.933] | 0.877 | 0.667 | 0.00735 | 3331 | 0.040 |
| `cand-10b0c1bb6f` prompt-engineered+scripts [hybrid_llm_code] | hybrid_llm_code | True | 0.847 | [0.780, 0.896] | 0.821 | 0.917 | 0.00612 | 2767 | 0.053 |
| `cand-b86f50865d` state-machine [state_machine] | state_machine | True | 0.960 | [0.915, 0.982] | 0.972 | 1.000 | 0.00134 | 2459 | 0.053 |
| `cand-02e358792b` state-machine [state_machine] | state_machine | True | 0.960 | [0.915, 0.982] | 0.972 | 1.000 | 0.00134 | 2459 | 0.053 |
| `cand-472a3b9617` state-machine [tool_using_agent] | tool_using_agent | False | 0.947 | [0.898, 0.973] | 0.934 | 0.917 | 0.00154 | 2485 | 0.067 |
| `cand-da9f257a49` state-machine [state_machine] | state_machine | True | 0.993 | [0.963, 0.999] | 1.000 | 1.000 | 0.00134 | 2459 | 0.027 |

Validation Pareto set: `cand-5e8ab3f931`, `cand-99cae998d8`, `cand-ad141baec3`, `cand-7fd19c2836`, `cand-f03dc8b39a`, `cand-10b0c1bb6f`, `cand-02e358792b`, `cand-da9f257a49`

## 6. Independent evaluation of the exported skill (sealed holdout, evaluated once)
**Recommended:** `cand-da9f257a49` — state-machine [state_machine] (`state_machine`), chosen on validation by first candidate that passed the validation gate AND sealed-holdout certification (gate order: strictly feasible on train, highest train pass rate, lowest cost).

| metric | value | 95% interval | evidence |
|---|---|---|---|
| pass_rate | 0.9933 | [0.976, 0.998] | ground_truth |
| robustness | 0.9917 | [0.970, 0.998] | adversarial |
| field_accuracy | 0.9993 | — | ground_truth |
| status_accuracy | 0.9967 | — | ground_truth |
| issue_f1 | 0.9411 | — | measured |
| security | 1 | — | adversarial |
| metamorphic_consistency | 1 | — | metamorphic |
| hard_violation_rate | 0 | [0.000, 0.010] | measured |
| error_rate | 0 | — | measured |
| cost_per_item_usd | 0.00123 | — | measured |
| tokens_per_item | 131.9 | — | measured |
| latency_ms_p50 | 0.1744 | — | measured |
| latency_ms_p95 | 2479 | — | measured |
| complexity | 34.26 | — | static |
| portability | 0.6 | — | static |

Hard invariant violations on holdout: none

Per family (holdout):

| family | realism | n | pass | 95% CI | hard viol. |
|---|---|---|---|---|---|
| arithmetic_error | realistic | 24 | 1.000 | [0.86, 1.00] | 0 |
| clean_labeled | realistic | 38 | 1.000 | [0.91, 1.00] | 0 |
| clean_table | realistic | 30 | 1.000 | [0.89, 1.00] | 0 |
| conflicting_totals | synthetic | 15 | 1.000 | [0.80, 1.00] | 0 |
| credit_note | realistic | 11 | 1.000 | [0.74, 1.00] | 0 |
| duplicate_submission | synthetic | 21 | 1.000 | [0.85, 1.00] | 0 |
| email_prose | realistic | 17 | 1.000 | [0.82, 1.00] | 0 |
| format_variants | realistic | 30 | 1.000 | [0.89, 1.00] | 0 |
| layout_drift | synthetic (holdout-only) | 27 | 0.926 | [0.77, 0.98] | 0 |
| missing_field | realistic | 27 | 1.000 | [0.88, 1.00] | 0 |
| ocr_truncated | synthetic | 13 | 1.000 | [0.77, 1.00] | 0 |
| prompt_injection | adversarial | 30 | 1.000 | [0.89, 1.00] | 0 |
| very_long | synthetic | 6 | 1.000 | [0.61, 1.00] | 0 |
| zero_tax_single_item | realistic | 11 | 1.000 | [0.74, 1.00] | 0 |

Generalization: train 0.997 → validation 0.993 → holdout 0.993 (pass rate). Drift family (never seen during search): 0.926 (n=27, CI [0.77, 0.98]).

All finalists on holdout (reported for transparency; NOT used for selection):

| candidate | pass | 95% CI | hard violations | cost/item |
|---|---|---|---|---|
| `cand-da9f257a49` | 0.993 | [0.976, 0.998] | 0 | 0.00123 |

### Certification attempts (each holdout set opened exactly once)
| attempt | candidate | n | pass | 95% CI | hard violations | result | holdout sha256 |
|---|---|---|---|---|---|---|---|
| 0 | `cand-da9f257a49` | 300 | 0.993 | [0.976, 0.998] | 0 | CERTIFIED | `f476ac2b573c` |

## 7. Acceptance criteria (recommended candidate, holdout)
| criterion | metric | required | observed (bound used) | result |
|---|---|---|---|---|
| ac_pass | pass_rate | >= 0.95 | 0.9760 (lower 95% bound) | PASS |
| ac_zero_hard | hard_violation_rate | == 0.0 | 0.0000 (point estimate) | PASS |
| ac_cost | cost_per_item_usd | <= 0.02 | 0.0012 (point estimate) | PASS |

## 8. Limitations and bounded claims
- LLM stages were executed against a SIMULATED model (mock mode). Their quality, latency and cost numbers are modelling assumptions.
- Ground truth exists only for synthetic documents generated by the world model; performance on real scanned invoices is NOT measured.
- Vendor-name correctness is judged by exact match to the generator's value; legitimate variants (e.g. 'ACME Corp.' vs 'Acme Corp') count as errors.
- Semantic judgement of free-text line-item descriptions is not verified beyond exact match.
- 'Ideal' is defined operationally by the Target (zero hard violations, all constraints, pass-rate lower 95% bound >= 0.95) on a sealed holdout; 100% correctness cannot be proven on a finite sample.
- All scenarios are generated by SkillForge's world model; realism labels mark which families imitate real documents, but none is a real-world sample.
- Confidence intervals are Wilson 95% intervals over scenarios; they quantify sampling noise of the synthetic distribution only.
- The validation split was reused to gate many candidates; it is therefore optimistically biased. Only the holdout numbers are unbiased estimates.
- Evolutionary search is heuristic: the result is the first skill found that meets the target, not a proven optimum.
- Sandbox backend `subprocess` (namespace+rlimits+audit-hook).

## 9. Operating cost estimate
Per document: $0.00123, 132 tokens, 0.32 LLM calls, p95 latency 2479 ms. Per 10,000 documents: $12.30 (simulated pricing).

## 10. Artifacts
- `/tmp/claude-0/-home-user-Test/8252dc0c-4f46-5207-8169-6f7e4ecdb092/scratchpad/runs11/run-20261122-232520-308a40/skill/invoice-processor`
- `/tmp/claude-0/-home-user-Test/8252dc0c-4f46-5207-8169-6f7e4ecdb092/scratchpad/runs11/run-20261122-232520-308a40/alternates/1-hybrid_llm_code/invoice-processor`
- `/tmp/claude-0/-home-user-Test/8252dc0c-4f46-5207-8169-6f7e4ecdb092/scratchpad/runs11/run-20261122-232520-308a40/alternates/2-tool_using_agent/invoice-processor`
- `/tmp/claude-0/-home-user-Test/8252dc0c-4f46-5207-8169-6f7e4ecdb092/scratchpad/runs11/run-20261122-232520-308a40/report.md`
- `/tmp/claude-0/-home-user-Test/8252dc0c-4f46-5207-8169-6f7e4ecdb092/scratchpad/runs11/run-20261122-232520-308a40/report.json`
