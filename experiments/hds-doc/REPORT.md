# Experiment `hds-doc` - hypothesis-driven synthesis vs program evolution

Environment `degraded_manifest_extraction` v1 (sha256 `184558e7e454`), pre-registered 2026-11-23T14:39:15Z; audit split sha256 `9c1afcbb0343` (100 instances). Replicates completed: 2/2. Provider: claude_code (real model).

## Diagnostic (before the search)

- hypotheses with a working implementation: 4
- AST similarity within a hypothesis 0.556 vs between hypotheses 0.488 (permutation p = 0.0358); control (plain independent generations) 0.624
- instances on which programs differ: 0.92
- mean calibration score per group: {"H0": [0.8578, 0.9052], "H1": [0.9574, 0.8981], "H2": [0.9049, 0.8489], "H3": [0.95, 0.9186], "control": [0.8919, 0.9258, 0.899]}
- verdict: PASS

## Audit (sealed set)

### Replicate 0

| role | program | hypothesis | mean score | 95% CI |
|---|---|---|---|---|
| A | `A-00-21c50a` | - | 0.8529 | [0.8064, 0.8933] |
| P_initial | `C-02-c041c2` | - | 0.8696 | [0.8280, 0.9044] |
| P_final | `C-08-562f08` | - | 0.9316 | [0.9210, 0.9420] |
| H_initial | `H-00-abd6eb` | H0 | 0.8481 | [0.8023, 0.8897] |
| H_final | `H-10-9e68f9` | H4 | 0.9338 | [0.9230, 0.9443] |
| hyp:H0 | `H-06-003182` | H0 | 0.9273 | [0.9162, 0.9380] |
| hyp:H1 | `H-01-888674` | H1 | 0.7668 | [0.7063, 0.8222] |
| hyp:H2 | `H-02-a27d45` | H2 | 0.8527 | [0.8064, 0.8956] |
| hyp:H3 | `H-03-465f44` | H3 | 0.8611 | [0.8192, 0.8973] |
| hyp:H4 | `H-10-9e68f9` | H4 | 0.9338 | [0.9230, 0.9443] |
| hyp:H5 | `H-09-0ecc0c` | H5 | 0.9102 | [0.8894, 0.9282] |

### Replicate 1

| role | program | hypothesis | mean score | 95% CI |
|---|---|---|---|---|
| A | `A-00-08e06a` | - | 0.8343 | [0.7866, 0.8779] |
| P_initial | `C-00-ebe21c` | - | 0.8716 | [0.8299, 0.9065] |
| P_final | `C-09-949aae` | - | 0.9320 | [0.9210, 0.9426] |
| H_initial | `H-02-ba0796` | H2 | 0.9175 | [0.8991, 0.9330] |
| H_final | `H-10-fea9fa` | H2 | 0.9288 | [0.9174, 0.9401] |
| hyp:H0 | `H-00-bb0981` | H0 | 0.8785 | [0.8374, 0.9123] |
| hyp:H1 | `H-01-aada86` | H1 | 0.7976 | [0.7434, 0.8481] |
| hyp:H2 | `H-10-fea9fa` | H2 | 0.9288 | [0.9174, 0.9401] |
| hyp:H3 | `H-03-53c9ac` | H3 | 0.8912 | [0.8606, 0.9185] |
| hyp:H4 | `H-04-f99411` | H4 | 0.8862 | [0.8452, 0.9200] |
| hyp:H5 | `H-06-9a6402` | H5 | 0.9025 | [0.8732, 0.9283] |

## Pre-registered tests

| test | rep0 | rep1 | verdict |
|---|---|---|---|
| G1 | +0.0023 [-0.0026, +0.0069] ✘ | -0.0032 [-0.0085, +0.0017] ✘ | **NOT SUPPORTED** |
| G2 | +0.0810 [+0.0346, +0.1373] ✔ | +0.0945 [+0.0474, +0.1531] ✔ | **SUPPORTED** |
| G3_paradigm_transition | +0.0857 [+0.0402, +0.1434] ✔ | +0.0113 [-0.0024, +0.0333] ✘ | **MIXED** |
| descriptive_P_final_minus_P_initial | +0.0620 [+0.0243, +0.1121] ✔ | +0.0604 [+0.0213, +0.1109] ✔ | **SUPPORTED** |

## Replicate 0: what happened

- calls / cost: A 1 / $3.62, P 12 / $10.83, H 12 / $8.58
- diversity P: {"programs": 12, "usable": 12, "code_clusters": 2, "mean_pairwise_ast_similarity": 0.6075, "mean_pairwise_behavioural_distance": 0.0443, "graph_signatures": ["C", "CC", "CCC", "CCCC", "CCCCCC"]}
- diversity H: {"programs": 11, "usable": 11, "code_clusters": 6, "mean_pairwise_ast_similarity": 0.5694, "mean_pairwise_behavioural_distance": 0.0462, "graph_signatures": ["C", "CC", "CCC"]}
- escapes from stagnation: P 0, H 0
- transition: {"initial_hypothesis": "H0", "final_hypothesis": "H4", "final_hypothesis_origin": "recombine", "different_hypothesis": true, "ast_similarity": 0.3617, "code_differs": true}

| hypothesis | origin | mechanism (model's words) | impl. working/attempted | best SELECTION | measured label | own prediction |
|---|---|---|---|---|---|---|
| H0 Row-local noisy-channel repair with arithmetic verification | initial | Each reconstructed row is repaired on its own: the program enumerates a bounded, weighted list of repair candidates for each field and keeps the cheapest (code, qty, unit, amount) combination that passes the check digit  | 2/2 | 0.9388 | mechanism inferior (supported: >= 2 independent implementations each significantly below the leader) | {"strong_minus_weak_relative_advantage": -0.0484, "confirmed": false} |
| H1 Global token-stream segmentation by dynamic programming | initial | A Viterbi-style DP runs over the flattened sequence of value tokens in each section. Its state is (token index, column position), and it chooses the row boundaries and the token-to-field assignment that minimise the tota | 1/1 | 0.8299 | below the leader, but idea and implementation not separated (one independent implementation) | {"strong_minus_weak_relative_advantage": -0.0328, "confirmed": false} |
| H2 Section-level beam search constrained by subtotals and total | initial | A beam search over the rows of a section keeps partial hypotheses keyed by their running AMOUNT sum. Each row contributes a few ranked repair candidates, and only complete hypotheses whose sum matches a candidate SUBTOTA | 1/1 | 0.8954 | below the leader, but idea and implementation not separated (one independent implementation) | {"strong_minus_weak_relative_advantage": 0.0219, "confirmed": true} |
| H3 Whole-document constraint-violation local search | initial | Iterative repair keeps a single complete document hypothesis (segmentation plus every field value). It applies hill-climbing or annealing moves: edit a field, split or merge a row, or skip a line. Each move must reduce a | 1/1 | 0.8935 | below the leader, but idea and implementation not separated (one independent implementation) | {"strong_minus_weak_relative_advantage": 0.0167, "confirmed": true} |
| H4 Checksum-constrained beam over probability-scored noisy-channel row lattices | recombine of H0+H2 | A slot-DP segments each section's token stream into rows (allowing skipped junk and missing fields). Each row becomes a k-best lattice of (qty, unit) candidates scored by a single noisy-channel cost: one-op enumeration w | 5/5 | 0.9467 | leader | {"strong_minus_weak_relative_advantage": 0.0062, "confirmed": true} |
| H5 Per-document self-calibrating channel (EM) with constraint-automaton decoding | pivot | A two-pass hard-EM loop: pass 1 decodes every row with prior channel costs (codes by an exact Viterbi whose automaton state is the running check-digit sum mod 10, numbers by best-first lattice generation plus exact weigh | 1/1 | 0.9114 | below the leader, but idea and implementation not separated (one independent implementation) | {"strong_minus_weak_relative_advantage": 0.0229, "confirmed": true} |

| step | move | hypothesis | usable | Δ SELECTION vs leader [95% CI] | AST sim. to leader | best after |
|---|---|---|---|---|---|---|
| 1 | recombine | H4 | True | +0.0447 [+0.0130, +0.0830] ✔ | 0.459 | 0.9417 |
| 2 | refine | H4 | True | +0.0006 [+0.0000, +0.0015] | 0.9282 | 0.9423 |
| 3 | second_impl | H0 | True | -0.0035 [-0.0080, +0.0007] | 0.5681 | 0.9423 |
| 4 | refine | H4 | True | +0.0010 [+0.0000, +0.0027] | 0.9128 | 0.9432 |
| 5 | refine | H4 | True | +0.0008 [-0.0011, +0.0036] | 0.898 | 0.9441 |
| 6 | pivot | H5 | True | -0.0327 [-0.0624, -0.0099] | 0.5248 | 0.9441 |
| 7 | refine | H4 | True | +0.0026 [-0.0013, +0.0065] | 0.9521 | 0.9467 |

- model's cause conjectures (not evidence): [{"hypothesis": "H4", "llm_cause": "MECHANISM_LIMIT", "status": "unverified conjecture"}, {"hypothesis": "H4", "llm_cause": "MECHANISM_LIMIT", "status": "unverified conjecture"}, {"hypothesis": "H4", "llm_cause": "PARAMETERS", "status": "unverified conjecture"}, {"hypothesis": "H4", "llm_cause": "MECHANISM_LIMIT", "status": "unverified conjecture"}]

## Replicate 1: what happened

- calls / cost: A 1 / $0.62, P 12 / $8.79, H 12 / $13.70
- diversity P: {"programs": 12, "usable": 12, "code_clusters": 4, "mean_pairwise_ast_similarity": 0.6297, "mean_pairwise_behavioural_distance": 0.0264, "graph_signatures": ["C", "CC", "CCB(C|C)C", "CCC", "CCCC"]}
- diversity H: {"programs": 11, "usable": 11, "code_clusters": 5, "mean_pairwise_ast_similarity": 0.5684, "mean_pairwise_behavioural_distance": 0.0526, "graph_signatures": ["C", "CC"]}
- escapes from stagnation: P 1, H 1
- transition: {"initial_hypothesis": "H2", "final_hypothesis": "H2", "final_hypothesis_origin": "initial", "different_hypothesis": false, "ast_similarity": 0.5655, "code_differs": true}

| hypothesis | origin | mechanism (model's words) | impl. working/attempted | best SELECTION | measured label | own prediction |
|---|---|---|---|---|---|---|
| H0 Line-local repair + per-row candidate lattice | initial | First rebuild logical rows by counting fields per physical line, joining wrapped fragments and splitting merged lines; then, for each row independently, take the cross product of channel-inverted candidate sets for CODE/ | 2/2 | 0.9276 | mechanism inferior (supported: >= 2 independent implementations each significantly below the leader) | {"strong_minus_weak_relative_advantage": -0.0374, "confirmed": false} |
| H1 Global token-stream Viterbi segmentation | initial | Flatten everything between COLUMNS and TOTAL into one token stream with line-break markers, then run a dynamic program over token positions whose states are the column slot (and section context). Transitions consume toke | 1/1 | 0.8276 | below the leader, but idea and implementation not separated (one independent implementation) | {"strong_minus_weak_relative_advantage": -0.0856, "confirmed": false} |
| H2 Section-sum constrained beam search | initial | For each section, run a beam or meet-in-the-middle search over the ranked candidate list of every row, keyed by the running AMOUNT sum. The search selects the joint assignment that matches one of the candidate values of  | 5/5 | 0.9455 | leader | {"strong_minus_weak_relative_advantage": 0.0314, "confirmed": true} |
| H3 Generate-truth-and-align (analysis by synthesis) | initial | For each row, enumerate plausible true (QTY, UNIT) pairs, using QTY in 1..500 together with divisibility against amount candidates to derive UNIT. Render the clean strings in the document's money and qty format, and scor | 1/1 | 0.9065 | below the leader, but idea and implementation not separated (one independent implementation) | {"strong_minus_weak_relative_advantage": 0.0108, "confirmed": true} |
| H4 Checksum-ranked channel lattice with subtotal-implied row repair | recombine of H2+H0 | Per-row channel-inverted candidate lattices (Y's costs, widened by X's derive-the-third-field edit distance) are ranked by a section beam keyed on the running AMOUNT sum and scored against SUBTOTAL/TOTAL; when no path re | 1/1 | 0.9346 | not distinguishable from the leader on SELECTION | {"strong_minus_weak_relative_advantage": 0.0117, "confirmed": true} |
| H5 Equation-driven derivation closure with channel-alignment scoring | pivot | Every equation of the document (q*u=a per row, sum of amounts = subtotal per section, sum of subtotals = total) is used as a generator: the missing variable is computed exactly from cheap (<=1-edit) neighbourhoods of the | 1/1 | 0.9139 | below the leader, but idea and implementation not separated (one independent implementation) | {"strong_minus_weak_relative_advantage": 0.0135, "confirmed": true} |

| step | move | hypothesis | usable | Δ SELECTION vs leader [95% CI] | AST sim. to leader | best after |
|---|---|---|---|---|---|---|
| 1 | recombine | H4 | True | -0.0023 [-0.0218, +0.0133] | 0.638 | 0.9369 |
| 2 | refine | H2 | True | -0.0076 [-0.0175, +0.0039] | 0.8694 | 0.9369 |
| 3 | pivot | H5 | True | -0.0230 [-0.0579, +0.0043] | 0.5874 | 0.9369 |
| 4 | refine | H2 | True | +0.0050 [+0.0000, +0.0130] | 0.8494 | 0.9419 |
| 5 | refine | H2 | True | +0.0023 [-0.0012, +0.0066] | 0.8058 | 0.9442 |
| 6 | second_impl | H0 | True | -0.0487 [-0.0872, -0.0186] | 0.4612 | 0.9442 |
| 7 | refine | H2 | True | +0.0013 [-0.0031, +0.0060] | 0.7966 | 0.9455 |

- model's cause conjectures (not evidence): [{"hypothesis": "H2", "llm_cause": "IMPLEMENTATION", "status": "unverified conjecture"}, {"hypothesis": "H2", "llm_cause": "IMPLEMENTATION", "status": "unverified conjecture"}, {"hypothesis": "H2", "llm_cause": "IMPLEMENTATION", "status": "unverified conjecture"}, {"hypothesis": "H2", "llm_cause": "IMPLEMENTATION", "status": "unverified conjecture"}]

