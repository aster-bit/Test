# Experiment `cal-v2` — results

Pre-registered 2026-11-23T03:16:35Z (environment sha256 `028822f6a415`, audit split sha256 `9cc50f60fd54`). Replicates completed: 2/2. Provider: claude_code (real model).

Score = hidden lower bound / achieved objective for valid answers (1.0 is generally unattainable; the bound is loose, so absolute values are low and only differences are meaningful), 0 for invalid answers; audit set: 100 instances incl. audit-only families.

## Audit results (sealed set, each candidate evaluated once)

### Replicate 0

| role | what | mean score | 95% CI | valid | cost/item $ | p95 ms |
|---|---|---|---|---|---|---|
| A | one-shot (first program, as written) | 0.1946 | [0.1338, 0.2613] | 1.000 | 0.00000 | 731 |
| B_best | best of independent generations | 0.1961 | [0.1352, 0.2628] | 1.000 | 0.00000 | 805 |
| C_initial | best from-scratch program inside arm C | 0.1978 | [0.1367, 0.2656] | 1.000 | 0.00000 | 786 |
| C_final | evolutionary synthesis champion | 0.2011 | [0.1400, 0.2683] | 1.000 | 0.00000 | 785 |
| H_manual | hand-written baseline skill | 0.1350 | [0.0794, 0.1989] | 1.000 | 0.00000 | 3 |
| D_direct | direct Claude call per instance | 0.1425 | [0.0858, 0.2077] | 0.950 | 0.09827 | 88969 |

### Replicate 1

| role | what | mean score | 95% CI | valid | cost/item $ | p95 ms |
|---|---|---|---|---|---|---|
| A | one-shot (first program, as written) | 0.1934 | [0.1331, 0.2604] | 1.000 | 0.00000 | 728 |
| B_best | best of independent generations | 0.1988 | [0.1379, 0.2657] | 1.000 | 0.00000 | 756 |
| C_initial | best from-scratch program inside arm C | 0.1978 | [0.1362, 0.2650] | 1.000 | 0.00000 | 724 |
| C_final | evolutionary synthesis champion | 0.2036 | [0.1421, 0.2708] | 1.000 | 0.00000 | 784 |
| H_manual | hand-written baseline skill | 0.1350 | [0.0794, 0.1989] | 1.000 | 0.00000 | 4 |
| D_direct | direct Claude call per instance | 0.1403 | [0.0834, 0.2046] | 0.860 | 0.16321 | 125260 |

## Pre-registered hypotheses

| hypothesis | rep0: diff [99% CI] | rep1: diff [99% CI] | verdict |
|---|---|---|---|
| H1_primary (C_final - C_initial) | +0.0033 [-0.0004, +0.0085] ✘ | +0.0058 [+0.0021, +0.0108] ✔ | **MIXED** |
| H2 (C_final - B_best) | +0.0050 [-0.0001, +0.0127] ✘ | +0.0048 [+0.0008, +0.0113] ✔ | **MIXED** |
| H3 (C_final - A) | +0.0065 [+0.0007, +0.0152] ✔ | +0.0102 [+0.0037, +0.0201] ✔ | **SUPPORTED** |
| H4 (C_final - H_manual) | +0.0661 [+0.0339, +0.1073] ✔ | +0.0686 [+0.0356, +0.1121] ✔ | **SUPPORTED** |
| H5 (C_final - D_direct) | +0.0586 [+0.0299, +0.0936] ✔ | +0.0633 [+0.0327, +0.1015] ✔ | **SUPPORTED** |

**Stage success criterion (H1 in every replicate): NOT MET** (MIXED).

## What the search actually did

### Replicate 0

- Synthesis calls: B=12, C=12; synthesis cost: B=$3.46, C=$4.74.
- Usable programs: B 11/12, C 12/12.
- New algorithms by AST measure (similarity to all earlier < 0.60): B 1, C 4.
- C variation steps: 6 (usable 6, structural 9, significant improvements over parent on SELECTION 2, of which structural 2); expansions 3 (usable 3).
- Operations named by the generator itself: intervene:add_time_aware_dispatch_construction, intervene:dynamic_atc_construction_with_targeted_search, intervene:targeted_block_moves_with_checkpointed_decoding.
- C_initial → C_final structure: {"ast_similarity_to_parent": 0.6348, "graph_changed": false, "functions_changed": true, "structural": true, "added_functions": ["_find"], "removed_functions": [], "novel_vs_initial": true}
- Max AST similarity of C_final to any arm-B program: 0.5321

| step | move | parents | usable | Δ selection vs parent [95% CI] | structural | best so far |
|---|---|---|---|---|---|---|
| 1 | intervene:dynamic_atc_construction_with_targeted_search | C-02-011438 | True | +0.0022 [+0.0003, +0.0046] ✔ | True | 0.2176 |
| 2 | recombine | C-03-8feefe, C-02-011438 | True | -0.0013 [-0.0041, +0.0008] | True | 0.2176 |
| 3 | intervene:targeted_block_moves_with_checkpointed_decoding | C-02-011438 | True | +0.0020 [+0.0004, +0.0039] ✔ | True | 0.2176 |
| 4 | expand | — | True | -0.0044 [-0.0092, -0.0011] | True | 0.2176 |
| 5 | recombine | C-02-011438, C-03-8feefe | True | -0.0005 [-0.0029, +0.0014] | True | 0.2176 |
| 6 | expand | — | True | -0.0050 [-0.0078, -0.0028] | True | 0.2176 |
| 7 | intervene:add_time_aware_dispatch_construction | C-05-8133d6 | True | -0.0007 [-0.0029, +0.0009] | True | 0.2176 |
| 8 | expand | — | True | -0.0082 [-0.0131, -0.0046] | True | 0.2176 |
| 9 | recombine | C-07-d20c9b, C-03-8feefe | True | -0.0008 [-0.0042, +0.0015] | True | 0.2176 |

Champion strategies (generator's own description):

- **A** `A-00-79a13c` (generate; graph `C`; selection 0.2135): The pipeline is one code node. It first runs the exact infeasibility checks: unknown predecessors, precedence cycles (Kahn's topological sort), and jobs with no mode that uses only known resources within their maximum capacity. Calendars are expanded into per-time capacity arrays, and schedules are built by a serial schedule-generation scheme. Each job goes in at its earliest feasible start, respecting release times,
- **B_best** `B-03-da940d` (generate; graph `C`; selection 0.2145): The approach is unchanged: a single deterministic code node with no LLM calls. It first detects infeasibility from unknown predecessors, precedence cycles (Kahn sort) or jobs with no fitting mode. Otherwise it decodes job orders with a serial schedule-generation scheme over per-time capacity arrays, placing each job at its earliest resource-feasible start and choosing modes greedily by finish time plus weighted tardi
- **C_initial** `C-02-011438` (generate; graph `C`; selection 0.2154): I check feasibility exactly as the spec defines it, then optimise the schedule with a single code node. The feasibility check rejects unknown predecessors, precedence cycles (found with Kahn's algorithm) and jobs with no mode that fits the known resources' maximum capacities. Feasible instances are decoded by a serial schedule-generation scheme over per-time-unit capacity arrays built from the calendars. Each job goe
- **C_final** `C-03-8feefe` (intervene:dynamic_atc_construction_with_targeted_search; graph `C`; selection 0.2176): I check feasibility exactly as the spec defines it and return "infeasible" with a reason when it fails. For feasible instances I compute effective due dates and inherited descendant weights over the precedence graph. A dynamic ATC-style serial schedule-generation scheme then places jobs one at a time. It picks the job and mode with the best weighted urgency per unit of duration at its true earliest feasible start, ca

- Cost: direct Claude $0.0983/instance vs synthesised skill $0.00000/instance; synthesis of arm C cost $4.74 → break-even after 48 instances.

### Replicate 1

- Synthesis calls: B=12, C=12; synthesis cost: B=$3.83, C=$4.54.
- Usable programs: B 12/12, C 11/12.
- New algorithms by AST measure (similarity to all earlier < 0.60): B 2, C 2.
- C variation steps: 7 (usable 7, structural 7, significant improvements over parent on SELECTION 2, of which structural 2); expansions 1 (usable 1).
- Operations named by the generator itself: intervene:add_blame_driven_squeaky_wheel_and_chain_moves, intervene:add_dynamic_dispatch_construction, intervene:add_dynamic_dispatch_constructor, intervene:add_squeaky_wheel_learning_phase, intervene:compound_chain_moves_and_energy_aware_decoding.
- C_initial → C_final structure: {"ast_similarity_to_parent": 0.4714, "graph_changed": false, "functions_changed": false, "structural": true, "added_functions": [], "removed_functions": [], "novel_vs_initial": true}
- Max AST similarity of C_final to any arm-B program: 0.4421

| step | move | parents | usable | Δ selection vs parent [95% CI] | structural | best so far |
|---|---|---|---|---|---|---|
| 1 | recombine | C-00-f5319b, C-01-fe7794 | True | +0.0024 [+0.0004, +0.0055] ✔ | True | 0.2164 |
| 2 | recombine | C-00-f5319b, C-03-99ad5f | True | -0.0020 [-0.0057, +0.0003] | True | 0.2164 |
| 3 | intervene:compound_chain_moves_and_energy_aware_decoding | C-03-99ad5f | True | +0.0002 [-0.0014, +0.0015] | True | 0.2166 |
| 4 | intervene:add_dynamic_dispatch_constructor | C-05-38e5d1 | True | +0.0014 [+0.0000, +0.0035] ✔ | True | 0.2180 |
| 5 | intervene:add_squeaky_wheel_learning_phase | C-06-260a46 | True | -0.0004 [-0.0024, +0.0011] | False | 0.2180 |
| 6 | intervene:add_dynamic_dispatch_construction | C-03-99ad5f | True | -0.0016 [-0.0051, +0.0011] | True | 0.2180 |
| 7 | expand | — | True | -0.0145 [-0.0222, -0.0082] | True | 0.2180 |
| 8 | intervene:add_blame_driven_squeaky_wheel_and_chain_moves | C-08-cc34c8 | True | +0.0012 [-0.0006, +0.0043] | True | 0.2180 |

Champion strategies (generator's own description):

- **A** `A-00-870771` (generate; graph `C`; selection 0.2128): One deterministic code node does everything. It first checks the input for infeasibility: an unknown predecessor, a precedence cycle (found with Kahn's topological sort), or a job with no mode that fits the known resources' maximum capacities. For feasible instances it uses a serial schedule-generation scheme over per-time-unit capacity arrays built from the calendars; zero-capacity windows are skipped by jumping pas
- **B_best** `B-10-02ba2e` (generate; graph `C`; selection 0.2170): The solver first checks the instance and answers "infeasible" when a job names an unknown predecessor, the precedences form a cycle, or a job has no mode that fits within the known resources' maximum capacities. Otherwise it builds a time-indexed capacity profile from each resource's calendar. A serial schedule generator decodes a random-key priority vector plus an optional fixed mode per job. Each job is placed at t
- **C_initial** `C-01-fe7794` (generate; graph `C`; selection 0.2140): The solver runs everything in one code node and makes no LLM calls. It first checks for infeasibility: unknown predecessors, self-loops or cycles (using Kahn's topological sort), and jobs with no mode that fits within known resources' maximum capacity. To build schedules it uses a calendar-aware serial schedule generation scheme on time-indexed capacity arrays. Each job goes at its earliest start that is feasible for
- **C_final** `C-06-260a46` (intervene:add_dynamic_dispatch_constructor; graph `C`; selection 0.2180): An exact infeasibility check comes first, then three constructors feed a shared pool. The first is static priority rules decoded by a calendar-aware serial SGS with λ-energy mode choice. The second, new, is a dynamic ATC dispatcher that recomputes priorities from actual earliest starts and slack, in Giffler–Thompson and delay-penalised variants plus noisy restarts. The third is biased random sampling. The best pool e

- Cost: direct Claude $0.1632/instance vs synthesised skill $0.00000/instance; synthesis of arm C cost $4.54 → break-even after 28 instances.

