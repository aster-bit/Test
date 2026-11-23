# cal-v2 — interpretation (written after unsealing; the protocol was not changed)

## Verdict under the pre-registered rule

**Stage criterion NOT MET.** The rule was "H1 (C_final > C_initial on the sealed audit, Bonferroni 99% CI
excluding 0) in *every* replicate". It holds in replicate 1 (+0.0058 [+0.0021, +0.0108]) and misses in
replicate 0 (+0.0033 [-0.0004, +0.0085]). This is recorded as a negative result for the stage. The threshold,
the environment, the metric and the splits were not touched after the run started.

## What the data does show

- The direction is the same in both replicates and for every comparison: C_final > C_initial, > B_best, > A.
  H3 (evolution beats one-shot generation) holds in both replicates; H4 (beats the hand-written skill) and H5
  (beats a direct Claude call per instance) hold in both with large margins (+0.06…+0.07).
- The effect of evolution over its own starting architecture is **small**: +1.7% and +2.9% relative. It is a
  refinement, not a qualitative jump. Over independent sampling at equal budget (H2) the gain is +0.005 and is
  significant in one replicate only.
- Unlike sched-v1, the environment was not saturated: selection scores of the 24 programs in a replicate spread
  from 0.2035 to 0.2180, and 4 of 15 variation steps were significant improvements over the parent.
- Both replicates independently arrived at the same idea — adding a *dynamic* dispatch constructor (ATC-style
  priorities recomputed from actual earliest starts) on top of static priority rules + annealing. The generator
  named these operations itself (`dynamic_atc_construction_with_targeted_search`,
  `add_dynamic_dispatch_constructor`). This is a known scheduling technique recalled by the model and applied
  after diagnosing failures; it is **not** claimed as an invented algorithm.
- **Space expansion did not work.** All 4 `expand` steps (new paradigm on stagnation) produced usable but
  significantly *worse* programs (−0.004…−0.015 vs. the best). Every improvement came from diagnosed
  interventions on an existing program or from recombination. With a 12-call budget a brand-new paradigm has no
  calls left to be tuned, so this mechanism is unproven rather than refuted.
- No program changed the pipeline graph: every candidate is a single code node (`C`). "Structural" here means
  low AST similarity / changed function set inside that node, not a new component graph. No LLM node survived
  because the task rewards CPU-bounded search.
- Reporting quirk: in replicate 0 "structural 9" counts expansions too while "variation steps 6" does not.

## Limits

- Two replicates, one model, one environment family; absolute scores are low because the hidden bound is loose.
- Effects of this size (0.003–0.006) are near the resolution of 100 audit instances with a Bonferroni 99% CI.
- Direct-Claude baseline: 5% and 14% invalid answers, $0.10–0.16 and 89–125 s p95 per instance; synthesised
  skills cost $0 per instance at <1 s, break-even after 28–48 instances.
