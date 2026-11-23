# Experiment `sched-v1` — results

Pre-registered 2026-11-23T01:59:38Z (environment sha256 `539d2ef9c2ac`, audit split sha256 `ac1241291a54`). Replicates completed: 2/2. Provider: claude_code (real model).

Score = hidden lower bound / makespan for valid schedules (1.0 is generally unattainable), 0 for invalid answers; audit set: 120 instances incl. an audit-only `large` family.

## Audit results (sealed set, each candidate evaluated once)

### Replicate 0

| role | what | mean score | 95% CI | valid | cost/item $ | p95 ms |
|---|---|---|---|---|---|---|
| A | one-shot (first program, as written) | 0.9178 | [0.9018, 0.9337] | 1.000 | 0.00000 | 900 |
| B_best | best of independent generations | 0.9178 | [0.9018, 0.9337] | 1.000 | 0.00000 | 900 |
| C_initial | best from-scratch program inside arm C | 0.9176 | [0.9016, 0.9333] | 1.000 | 0.00000 | 900 |
| C_final | evolutionary synthesis champion | 0.9176 | [0.9016, 0.9333] | 1.000 | 0.00000 | 900 |
| H_manual | hand-written baseline skill | 0.8939 | [0.8743, 0.9126] | 1.000 | 0.00000 | 0 |
| D_direct | direct Claude call per instance | 0.9075 | [0.8895, 0.9247] | 1.000 | 0.02699 | 33795 |

### Replicate 1

| role | what | mean score | 95% CI | valid | cost/item $ | p95 ms |
|---|---|---|---|---|---|---|
| A | one-shot (first program, as written) | 0.9177 | [0.9018, 0.9335] | 1.000 | 0.00000 | 901 |
| B_best | best of independent generations | 0.9178 | [0.9018, 0.9337] | 1.000 | 0.00000 | 952 |
| C_initial | best from-scratch program inside arm C | 0.9175 | [0.9016, 0.9334] | 1.000 | 0.00000 | 1000 |
| C_final | evolutionary synthesis champion | 0.9175 | [0.9016, 0.9334] | 1.000 | 0.00000 | 1000 |
| H_manual | hand-written baseline skill | 0.8939 | [0.8743, 0.9126] | 1.000 | 0.00000 | 0 |
| D_direct | direct Claude call per instance | 0.8708 | [0.8328, 0.9062] | 0.958 | 0.04364 | 47261 |

## Pre-registered hypotheses

| hypothesis | rep0: diff [99% CI] | rep1: diff [99% CI] | verdict |
|---|---|---|---|
| H1_primary (C_final - C_initial) | +0.0000 [+0.0000, +0.0000] ✘ | +0.0000 [+0.0000, +0.0000] ✘ | **NOT SUPPORTED** |
| H2 (C_final - B_best) | -0.0003 [-0.0016, +0.0004] ✘ | -0.0003 [-0.0008, +0.0000] ✘ | **NOT SUPPORTED** |
| H3 (C_final - A) | -0.0003 [-0.0016, +0.0004] ✘ | -0.0001 [-0.0006, +0.0002] ✘ | **NOT SUPPORTED** |
| H4 (C_final - H_manual) | +0.0236 [+0.0140, +0.0342] ✔ | +0.0236 [+0.0140, +0.0342] ✔ | **SUPPORTED** |
| H5 (C_final - D_direct) | +0.0101 [+0.0047, +0.0164] ✔ | +0.0467 [+0.0107, +0.0944] ✔ | **SUPPORTED** |

**Stage success criterion (H1 in every replicate): NOT MET** (NOT SUPPORTED).

## What the search actually did

### Replicate 0

- Synthesis calls: B=12, C=12; synthesis cost: B=$2.97, C=$3.96.
- Usable programs: B 12/12, C 12/12.
- New algorithms by AST measure (similarity to all earlier < 0.60): B 2, C 6.
- C variation steps: 5 (usable 5, structural 9, significant improvements over parent on SELECTION 0, of which structural 0); expansions 4 (usable 4).
- Operations named by the generator itself: intervene:add_exact_branch_and_bound_phase, intervene:add_improvement_phase, intervene:change_algorithm_to_lamarckian_ga, intervene:split_and_specialise_with_window_lns.
- C_initial → C_final structure: {"ast_similarity_to_parent": 1.0, "graph_changed": false, "functions_changed": false, "structural": false, "added_functions": [], "removed_functions": [], "novel_vs_initial": false}
- Max AST similarity of C_final to any arm-B program: 0.6534

| step | move | parents | usable | Δ selection vs parent [95% CI] | structural | best so far |
|---|---|---|---|---|---|---|
| 1 | intervene:add_exact_branch_and_bound_phase | C-00-5eede9 | True | +0.0000 [+0.0000, +0.0000] | True | 0.9195 |
| 2 | recombine | C-03-c3de14, C-00-5eede9 | True | +0.0000 [+0.0000, +0.0000] | True | 0.9195 |
| 3 | expand | — | True | -0.0005 [-0.0015, +0.0000] | True | 0.9195 |
| 4 | intervene:change_algorithm_to_lamarckian_ga | C-01-337189 | True | +0.0000 [+0.0000, +0.0000] | True | 0.9195 |
| 5 | expand | — | True | -0.0015 [-0.0037, +0.0000] | True | 0.9195 |
| 6 | intervene:split_and_specialise_with_window_lns | C-03-c3de14 | True | +0.0000 [+0.0000, +0.0000] | True | 0.9195 |
| 7 | expand | — | True | +0.0000 [+0.0000, +0.0000] | True | 0.9195 |
| 8 | intervene:add_improvement_phase | C-04-acd063 | True | +0.0000 [+0.0000, +0.0000] | True | 0.9195 |
| 9 | expand | — | True | +0.0000 [+0.0000, +0.0000] | True | 0.9195 |

Champion strategies (generator's own description):

- **A** `A-00-8db8d2` (generate; graph `C`; selection 0.9195): The solver first checks for infeasibility. It reports "infeasible" for an unknown job or resource, a demand above capacity, or a precedence cycle (found with Kahn's topological sort). Otherwise it computes a lower bound from the critical path (with release times) and per-resource energy, and stops early whenever a schedule reaches that bound. Schedules come from a serial schedule-generation scheme over precedence-fea
- **B_best** `B-00-8db8d2` (generate; graph `C`; selection 0.9195): The solver first checks for infeasibility. It reports "infeasible" for an unknown job or resource, a demand above capacity, or a precedence cycle (found with Kahn's topological sort). Otherwise it computes a lower bound from the critical path (with release times) and per-resource energy, and stops early whenever a schedule reaches that bound. Schedules come from a serial schedule-generation scheme over precedence-fea
- **C_initial** `C-00-5eede9` (generate; graph `C`; selection 0.9195): A single deterministic code node first checks feasibility. It returns "infeasible" when a job references an unknown job or resource, when a job needs more units than a resource's capacity, or when Kahn's topological sort finds a precedence cycle. Otherwise it builds schedules with a serial schedule-generation scheme over a time-indexed resource profile; this always gives a valid schedule that respects release times a
- **C_final** `C-00-5eede9` (generate; graph `C`; selection 0.9195): A single deterministic code node first checks feasibility. It returns "infeasible" when a job references an unknown job or resource, when a job needs more units than a resource's capacity, or when Kahn's topological sort finds a precedence cycle. Otherwise it builds schedules with a serial schedule-generation scheme over a time-indexed resource profile; this always gives a valid schedule that respects release times a

- Cost: direct Claude $0.0270/instance vs synthesised skill $0.00000/instance; synthesis of arm C cost $3.96 → break-even after 147 instances.

### Replicate 1

- Synthesis calls: B=12, C=12; synthesis cost: B=$3.07, C=$3.77.
- Usable programs: B 12/12, C 12/12.
- New algorithms by AST measure (similarity to all earlier < 0.60): B 3, C 2.
- C variation steps: 5 (usable 5, structural 9, significant improvements over parent on SELECTION 0, of which structural 0); expansions 4 (usable 4).
- Operations named by the generator itself: intervene:add_exact_branch_and_bound_phase, intervene:add_improvement_phase.
- C_initial → C_final structure: {"ast_similarity_to_parent": 1.0, "graph_changed": false, "functions_changed": false, "structural": false, "added_functions": [], "removed_functions": [], "novel_vs_initial": false}
- Max AST similarity of C_final to any arm-B program: 0.6714

| step | move | parents | usable | Δ selection vs parent [95% CI] | structural | best so far |
|---|---|---|---|---|---|---|
| 1 | recombine | C-02-2ed6ac, C-00-300330 | True | +0.0000 [+0.0000, +0.0000] | True | 0.9195 |
| 2 | recombine | C-02-2ed6ac, C-00-300330 | True | +0.0000 [+0.0000, +0.0000] | True | 0.9195 |
| 3 | expand | — | True | +0.0000 [+0.0000, +0.0000] | True | 0.9195 |
| 4 | intervene:add_improvement_phase | C-05-659109 | True | +0.0000 [+0.0000, +0.0000] | True | 0.9195 |
| 5 | expand | — | True | +0.0000 [+0.0000, +0.0000] | True | 0.9195 |
| 6 | intervene:add_improvement_phase | C-03-80d198 | True | +0.0000 [+0.0000, +0.0000] | True | 0.9195 |
| 7 | expand | — | True | +0.0000 [+0.0000, +0.0000] | True | 0.9195 |
| 8 | intervene:add_exact_branch_and_bound_phase | C-02-2ed6ac | True | +0.0000 [+0.0000, +0.0000] | True | 0.9195 |
| 9 | expand | — | True | +0.0000 [+0.0000, +0.0000] | True | 0.9195 |

Champion strategies (generator's own description):

- **A** `A-00-77ee34` (generate; graph `C`; selection 0.9189): I validate the instance first. I check for unknown job or resource references, demands above capacity, and precedence cycles (Kahn's algorithm). If any check fails, the program returns "infeasible" with a reason. Otherwise a single code node runs a random-key **serial schedule generation scheme**: jobs are pulled from a precedence-eligible heap and each is placed at the earliest resource-feasible time, respecting rel
- **B_best** `B-02-9aa491` (generate; graph `CB(C|C)`; selection 0.9195): Checks run first and catch infeasible instances: unknown job or resource references, demands above capacity, and precedence cycles (Kahn's algorithm). A branch then either reports infeasible or runs the deterministic optimiser. The optimiser uses a serial schedule-generation scheme (SGS) that schedules jobs by priority, placing each one at the earliest time its releases, predecessors and resources allow. Starting poi
- **C_initial** `C-00-300330` (generate; graph `CC`; selection 0.9195): The approach has three steps. First, a deterministic validator checks for unknown job or resource references, demands that exceed capacity, and precedence cycles (using Kahn's algorithm), and returns "infeasible" with a reason. Second, a feasible instance is solved with a serial schedule generation scheme driven by random-key priorities. Each decoded schedule is improved by forward-backward double justification, whic
- **C_final** `C-00-300330` (generate; graph `CC`; selection 0.9195): The approach has three steps. First, a deterministic validator checks for unknown job or resource references, demands that exceed capacity, and precedence cycles (using Kahn's algorithm), and returns "infeasible" with a reason. Second, a feasible instance is solved with a serial schedule generation scheme driven by random-key priorities. Each decoded schedule is improved by forward-backward double justification, whic

- Cost: direct Claude $0.0436/instance vs synthesised skill $0.00000/instance; synthesis of arm C cost $3.77 → break-even after 86 instances.


## Interpretation (written after the audit; not part of the pre-registration)

**Negative result for the stage criterion, caused by a saturated environment.** In both replicates the
evolutionary champion is one of the initial from-scratch programs: none of the 18 variation steps
(self-diagnosed interventions, recombinations, 8 space expansions) improved the SELECTION score. The reason
is visible in the lineage: 22 of 25 programs in replicate 1 have the *identical* selection score and
candidates differ on only 3 of 80 instances — the generator's first attempt already solves these instances
(apparently optimally), so there is nothing to improve. The environment was too easy to discriminate
between search strategies; this is a design flaw of the experiment, and it says nothing either way about
whether evolutionary synthesis helps where headroom exists.

What the experiment does show (real model, sealed audit, both replicates):
* a program synthesised from the specification alone beats the hand-written baseline (H4 supported);
* against a direct Claude call per instance the result is MIXED (H5 holds in replicate 1 only); the direct
  baseline costs $0.027–0.044 and 34–47 s (p95) per instance and returned 4.2% invalid schedules in one
  replicate, while the synthesised skill costs nothing per instance and answers in ≤ 1 s.

Follow-up: experiment `cal-v1` uses a harder environment and adds a pre-registered *headroom gate* that must
pass before the protocol is frozen.
