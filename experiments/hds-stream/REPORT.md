# Experiment `hds-stream` - hypothesis-driven synthesis vs program evolution

Environment `session_log_reconstruction` v1 (sha256 `905bebe3d4de`), pre-registered 2026-11-23T14:39:15Z; audit split sha256 `d511cfa3b93b` (100 instances). Replicates completed: 2/2. Provider: claude_code (real model).

## Diagnostic (before the search)

- hypotheses with a working implementation: 4
- AST similarity within a hypothesis 0.547 vs between hypotheses 0.407 (permutation p = 0.0102); control (plain independent generations) 0.410
- instances on which programs differ: 0.89
- mean calibration score per group: {"H0": [0.9167, 0.9037], "H1": [0.9702, 0.965], "H2": [0.9209, 0.9148], "H3": [0.9221, 0.9249], "control": [0.9254, 0.9522, 0.9762, 0.9163]}
- verdict: PASS

## Audit (sealed set)

### Replicate 0

| role | program | hypothesis | mean score | 95% CI |
|---|---|---|---|---|
| A | `A-00-bf62de` | - | 0.8917 | [0.8661, 0.9157] |
| P_initial | `C-01-11080f` | - | 0.9214 | [0.9021, 0.9391] |
| P_final | `C-06-b25a36` | - | 0.9343 | [0.9136, 0.9520] |
| H_initial | `H-03-c1992a` | H3 | 0.9340 | [0.9152, 0.9511] |
| H_final | `H-04-b10734` | H4 | 0.9541 | [0.9400, 0.9670] |
| hyp:H0 | `H-00-29ebb6` | H0 | 0.8240 | [0.7947, 0.8523] |
| hyp:H1 | `H-01-7c8066` | H1 | 0.9492 | [0.9317, 0.9647] |
| hyp:H2 | `H-02-c9ea71` | H2 | 0.8047 | [0.7708, 0.8371] |
| hyp:H3 | `H-03-c1992a` | H3 | 0.9340 | [0.9152, 0.9511] |
| hyp:H4 | `H-04-b10734` | H4 | 0.9541 | [0.9400, 0.9670] |
| hyp:H5 | `H-07-eae7b4` | H5 | 0.9347 | [0.9171, 0.9511] |
| hyp:H6 | `H-09-9ab0d7` | H6 | 0.9322 | [0.9131, 0.9483] |

### Replicate 1

| role | program | hypothesis | mean score | 95% CI |
|---|---|---|---|---|
| A | `A-00-a8c2c5` | - | 0.9546 | [0.9389, 0.9681] |
| P_initial | `C-02-3c8e02` | - | 0.9360 | [0.9172, 0.9528] |
| P_final | `C-11-896d65` | - | 0.9558 | [0.9428, 0.9673] |
| H_initial | `H-01-8fecfc` | H1 | 0.9604 | [0.9471, 0.9717] |
| H_final | `H-05-443106` | H1 | 0.9583 | [0.9455, 0.9698] |
| hyp:H0 | `H-00-91be78` | H0 | 0.9126 | [0.8920, 0.9321] |
| hyp:H1 | `H-05-443106` | H1 | 0.9583 | [0.9455, 0.9698] |
| hyp:H2 | `H-02-f5b0e0` | H2 | 0.7841 | [0.7551, 0.8126] |
| hyp:H3 | `H-03-4ec92e` | H3 | 0.8864 | [0.8609, 0.9102] |
| hyp:H4 | `H-04-03936a` | H4 | 0.9481 | [0.9314, 0.9629] |
| hyp:H5 | `H-08-fccdab` | H5 | 0.8344 | [0.8024, 0.8644] |
| hyp:H6 | `H-10-04eab2` | H6 | 0.8112 | [0.7853, 0.8363] |

## Pre-registered tests

| test | rep0 | rep1 | verdict |
|---|---|---|---|
| G1 | +0.0198 [+0.0064, +0.0352] ✔ | +0.0025 [-0.0052, +0.0098] ✘ | **MIXED** |
| G2 | +0.0624 [+0.0390, +0.0910] ✔ | +0.0037 [-0.0055, +0.0129] ✘ | **MIXED** |
| G3_paradigm_transition | +0.0201 [+0.0061, +0.0350] ✔ | -0.0020 [-0.0090, +0.0048] ✘ | **MIXED** |
| descriptive_P_final_minus_P_initial | +0.0128 [-0.0009, +0.0267] ✘ | +0.0198 [+0.0096, +0.0316] ✔ | **MIXED** |

## Replicate 0: what happened

- calls / cost: A 1 / $0.85, P 12 / $9.63, H 12 / $14.00
- diversity P: {"programs": 12, "usable": 12, "code_clusters": 6, "mean_pairwise_ast_similarity": 0.499, "mean_pairwise_behavioural_distance": 0.0419, "graph_signatures": ["C", "CC"]}
- diversity H: {"programs": 11, "usable": 11, "code_clusters": 8, "mean_pairwise_ast_similarity": 0.4983, "mean_pairwise_behavioural_distance": 0.0623, "graph_signatures": ["C", "CCCC"]}
- escapes from stagnation: P 1, H 0
- transition: {"initial_hypothesis": "H3", "final_hypothesis": "H4", "final_hypothesis_origin": "recombine", "different_hypothesis": true, "ast_similarity": 0.5999, "code_differs": true}

| hypothesis | origin | mechanism (model's words) | impl. working/attempted | best SELECTION | measured label | own prediction |
|---|---|---|---|---|---|---|
| H0 Greedy online offset tracker | initial | A single pass over the events in timestamp order keeps one live state record per session (node, stream offset, durable offset, status) and assigns each anonymous event to the candidate session whose predicted next offset | 1/1 | 0.8362 | below the leader, but idea and implementation not separated (one independent implementation) | {"strong_minus_weak_relative_advantage": 0.0511, "confirmed": true} |
| H1 Multi-hypothesis beam tracking | initial | A beam of the K best complete joint world-states (all session states, the assignments so far, and accumulated penalty) is expanded event by event, branching over every feasible owner and over the 'duplicate' interpretati | 1/1 | 0.9687 | below the leader, but idea and implementation not separated (one independent implementation) | {"strong_minus_weak_relative_advantage": -0.0396, "confirmed": false} |
| H2 Offset-chain path cover via min-cost assignment | initial | A directed graph is built whose edges link event a to event b when b can directly follow a in one session (same node or recover-linked, with b.off equal to a.off+len or the durable offset, and timestamps compatible withi | 1/1 | 0.8528 | below the leader, but idea and implementation not separated (one independent implementation) | {"strong_minus_weak_relative_advantage": -0.0809, "confirmed": false} |
| H3 Anchor-seeded global local search | initial | One complete assignment of all anonymous events (with duplicate flags) is iteratively improved by a stochastic local search (reassign one event, swap two events, or toggle duplicate) whose objective is computed by fully  | 2/2 | 0.9692 | mechanism inferior (supported: >= 2 independent implementations each significantly below the leader) | {"strong_minus_weak_relative_advantage": -0.0281, "confirmed": false} |
| H4 Beam-seeded semantic annealing with margin-gated override | recombine of H3+H1 | A time-boxed multi-hypothesis beam over joint session states produces a complete assignment that seeds a simulated-annealing local search whose objective is an exact per-session crash-aware replay, and the annealed solut | 4/4 | 0.9796 | leader | {"strong_minus_weak_relative_advantage": -0.0009, "confirmed": false} |
| H5 Continuity-fragment contraction with track-swap search | pivot | Events are first contracted into fragments joined by mutually unique exact offset continuity (a.end == b.start on the same node within a time window), label-bearing fragments are pinned, and a time-boxed annealing then m | 1/1 | 0.9627 | below the leader, but idea and implementation not separated (one independent implementation) | {"strong_minus_weak_relative_advantage": 0.0055, "confirmed": true} |
| H6 Lagrangian session-trajectory decomposition | pivot | Each session independently computes its optimal whole trajectory (open -> ... -> end) through the time-ordered event DAG with a crash-aware Viterbi DP whose own labelled events are high-reward waypoints, while subgradien | 1/1 | 0.9551 | below the leader, but idea and implementation not separated (one independent implementation) | {"strong_minus_weak_relative_advantage": 0.0255, "confirmed": true} |

| step | move | hypothesis | usable | Δ SELECTION vs leader [95% CI] | AST sim. to leader | best after |
|---|---|---|---|---|---|---|
| 1 | recombine | H4 | True | +0.0104 [+0.0028, +0.0193] ✔ | 0.5999 | 0.9796 |
| 2 | refine | H4 | True | -0.0018 [-0.0050, +0.0004] | 0.9793 | 0.9796 |
| 3 | second_impl | H3 | True | -0.0287 [-0.0461, -0.0120] | 0.4699 | 0.9796 |
| 4 | pivot | H5 | True | -0.0169 [-0.0298, -0.0049] | 0.4805 | 0.9796 |
| 5 | refine | H4 | True | -0.0024 [-0.0068, +0.0007] | 0.975 | 0.9796 |
| 6 | pivot | H6 | True | -0.0246 [-0.0486, -0.0056] | 0.4441 | 0.9796 |
| 7 | refine | H4 | True | -0.0030 [-0.0090, +0.0004] | 0.9676 | 0.9796 |

- model's cause conjectures (not evidence): [{"hypothesis": "H4", "llm_cause": "PARAMETERS", "status": "unverified conjecture"}, {"hypothesis": "H4", "llm_cause": "IMPLEMENTATION", "status": "unverified conjecture"}, {"hypothesis": "H4", "llm_cause": "IMPLEMENTATION", "status": "unverified conjecture"}]

## Replicate 1: what happened

- calls / cost: A 1 / $0.71, P 12 / $5.61, H 12 / $6.12
- diversity P: {"programs": 12, "usable": 12, "code_clusters": 3, "mean_pairwise_ast_similarity": 0.601, "mean_pairwise_behavioural_distance": 0.1382, "graph_signatures": ["C"]}
- diversity H: {"programs": 11, "usable": 11, "code_clusters": 7, "mean_pairwise_ast_similarity": 0.4853, "mean_pairwise_behavioural_distance": 0.076, "graph_signatures": ["C", "CC", "CCC"]}
- escapes from stagnation: P 0, H 0
- transition: {"initial_hypothesis": "H1", "final_hypothesis": "H1", "final_hypothesis_origin": "initial", "different_hypothesis": false, "ast_similarity": 0.9108, "code_differs": false}

| hypothesis | origin | mechanism (model's words) | impl. working/attempted | best SELECTION | measured label | own prediction |
|---|---|---|---|---|---|---|
| H0 Greedy online offset-chaining tracker | initial | A single pass over events in timestamp order keeps one state record per session (node, stream offset, durable offset, status) and assigns each anonymous event to the compatible session with the lowest mismatch cost. | 2/2 | 0.9269 | mechanism inferior (supported: >= 2 independent implementations each significantly below the leader) | {"strong_minus_weak_relative_advantage": -0.0004, "confirmed": false} |
| H1 Beam search over joint cluster states | initial | A beam of K weighted joint hypotheses (each holding all sessions' states plus a pending buffer of deferred events) is expanded event by event and pruned by a cost that penalises skipped offsets, implied missing events, r | 4/4 | 0.9711 | leader | {"strong_minus_weak_relative_advantage": -0.0967, "confirmed": false} |
| H2 Successor-link min-cost path cover | initial | A sparse graph is built whose edges link each event to plausible same-session successors (by offset continuity, node or crash-recover transitions, and time proximity), and a min-cost assignment or matching selects one su | 1/1 | 0.8128 | below the leader, but idea and implementation not separated (one independent implementation) | {"strong_minus_weak_relative_advantage": -0.0019, "confirmed": false} |
| H3 Local search over the assignment vector with a replay-consistency objective | initial | Starting from a quick seed assignment, simulated annealing or hill climbing repeatedly reassigns single anonymous events (or swaps pairs, or moves whole runs) between sessions, re-scoring each affected session by an orde | 1/1 | 0.9137 | below the leader, but idea and implementation not separated (one independent implementation) | {"strong_minus_weak_relative_advantage": 0.0341, "confirmed": true} |
| H4 Beam-seeded conservative replay descent | recombine of H1+H3 | A joint-state beam search produces the best complete hypothesis, then a deterministic first-improvement descent over the anonymous-event assignment vector moves single events or swaps whole anonymous suffixes between two | 1/1 | 0.9535 | below the leader, but idea and implementation not separated (one independent implementation) | {"strong_minus_weak_relative_advantage": -0.0619, "confirmed": false} |
| H5 Session-decomposed Viterbi with Lagrangian event prices | pivot | Each session independently runs a full-log Viterbi dynamic program over its own protocol states (stream offset, durable offset, node, status) choosing which events to take; the sessions are coupled only by per-event dual | 1/1 | 0.8764 | below the leader, but idea and implementation not separated (one independent implementation) | {"strong_minus_weak_relative_advantage": 0.0645, "confirmed": true} |
| H6 Easy-first anchor propagation with windowed marginal replay costs | pivot | A priority queue holds every anonymous event keyed by the margin between its best and second-best session's marginal replay cost; the most confident event is committed first, its session's replay snapshots are rebuilt, a | 1/1 | 0.8654 | below the leader, but idea and implementation not separated (one independent implementation) | {"strong_minus_weak_relative_advantage": -0.0359, "confirmed": false} |

| step | move | hypothesis | usable | Δ SELECTION vs leader [95% CI] | AST sim. to leader | best after |
|---|---|---|---|---|---|---|
| 1 | recombine | H4 | True | -0.0070 [-0.0143, -0.0004] | 0.5774 | 0.9605 |
| 2 | refine | H1 | True | +0.0106 [+0.0013, +0.0251] ✔ | 0.9108 | 0.9711 |
| 3 | second_impl | H0 | True | -0.0575 [-0.0892, -0.0309] | 0.4529 | 0.9711 |
| 4 | refine | H1 | True | -0.0014 [-0.0045, +0.0013] | 0.8926 | 0.9711 |
| 5 | pivot | H5 | True | -0.0948 [-0.1257, -0.0666] | 0.3778 | 0.9711 |
| 6 | refine | H1 | True | -0.0001 [-0.0023, +0.0029] | 0.9062 | 0.9711 |
| 7 | pivot | H6 | True | -0.1057 [-0.1374, -0.0745] | 0.4543 | 0.9711 |

- model's cause conjectures (not evidence): [{"hypothesis": "H1", "llm_cause": "IMPLEMENTATION", "status": "consistent with measurement"}, {"hypothesis": "H1", "llm_cause": "IMPLEMENTATION", "status": "unverified conjecture"}, {"hypothesis": "H1", "llm_cause": "IMPLEMENTATION", "status": "unverified conjecture"}]

