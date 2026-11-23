# Audit: what in SkillForge v1 is generic, and what is a hand-prepared invoice solution

Date: 2026-11-23. Scope: every module under `skillforge/` at commit `d11c3e0`.

## Verdict

SkillForge v1 is a **parameter/composition optimiser over a human-written solution space**, not a
synthesis engine. In the two certified runs (`runs11`, `runs12`) 100% of the 221 / 340 evaluated
candidates were built from human-written code blocks; `llm_code_patch` (the only path to new code) was
attempted 0 times in mock mode and once in the Claude Code smoke run. Every "discovery" that unblocked the
search during development (prose ISO dates, currency-suffixed line items, pipe splitting, stale
`unparseable_date` flag) was made by the developer, who edited the pools and blocks by hand.

## Module-by-module

| module | status | why |
|---|---|---|
| `ir.py` (SkillIR: code / llm / branch nodes) | **generic** | Typed execution graph; nothing domain-specific. Architecture labels are derived from structure. |
| `sandbox.py`, `sandbox_runner.py` | **generic** | Static AST policy + isolated process. Allowed-module list was tuned for regex parsing; widened in v2. |
| `executor.py` | **generic** | Host-side graph interpreter; prompt placeholders were document-centric (`{input_text}`) — `{input_json}` added in v2. |
| `llm.py` (Claude Code / mock / API providers) | **generic** | Mock *handlers* are domain plugins (see below). |
| `stats.py`, `store.py` | **generic** | |
| `evaluation.py` | **mostly generic** | Uses plugin-provided checkers/scorer; "stress category" names are generic labels. |
| `evolution.py` (NSGA-II, ε-constraint, novelty, restarts, niches) | **generic machinery, domain-fed variation** | Variation is entirely delegated to `CandidateSynthesizer`, i.e. to the plugin's operators. |
| `synthesis.py` | **not generic** | `initial_population` = plugin seeds; `mutate` = plugin operators; crossover only recombines existing components; `llm_code_patch` rewrites *one existing* block and was effectively unused. |
| `adversary.py` | **domain-fed** | Searches over plugin-written perturbations. |
| `world.py` | generic splitter over plugin generators | |
| `domain_compiler.py` | **not generic** | Refuses any domain without a hand-written plugin. |
| `compiler.py` | mostly generic | Exported test template hard-codes invoice fields (`invoice_number`, `date`, `total`). |
| `pipeline.py` | generic orchestration | Certification protocol flawed (see below). |
| `domains/invoice/components.py` | **hand-prepared solution** | 13 handwritten algorithms (`extract_fields`, `check_arithmetic`, …) + pools of labels/regexes/date formats. |
| `domains/invoice/plugin.py` | **hand-prepared solution space** | 9 handwritten seed architectures, ~20 invoice-specific mutation operators, failure→operator hint tables, simulated LLM built from the same handwritten parser. |
| `domains/invoice/world.py` | legitimate environment | Generator + ground truth (this is what a plugin *should* contain). |

## Methodological problems found

1. **Sequential certification without multiplicity control.** A failed holdout was retired into train and a
   new one generated until one passed (runs12 certified on attempt 3). Repeated testing until success
   inflates the false-certification rate and was not corrected for.
2. **Simulated LLM shares the solver's blind spots.** The mock model read documents with the same pools the
   candidates used, so "LLM fallback" quality was bounded by handwritten code and is not evidence about a
   real model.
3. **Developer-in-the-loop leakage.** The search space was edited between runs after inspecting failures on
   validation data; final numbers therefore describe a space tuned by a human against the same generator.

## What v2 changes (this stage)

* New plugin interface `skillforge/envs/` (TaskEnvironment): public specification, instance generator,
  **hidden** reference and checker. It is forbidden to contain candidates, algorithms or mutation operators.
* `skillforge/open_synth.py`: candidates are written from scratch by Claude Code as an execution graph +
  Python module; variation is *structural* (typed operators applied by the model to the full program);
  stagnation triggers *space expansion* (analysis of failures → a new solution paradigm).
* `skillforge/novelty.py`: AST-structure and behaviour distances, so "new algorithm" is measured, not
  asserted; cosmetic edits are rejected.
* `skillforge/experiment.py`: pre-registered protocol (hashes frozen and committed before search),
  dev / selection / audit separation, one audit evaluation, paired statistics with Bonferroni correction,
  equal synthesis budgets for the compared arms.

The v1 invoice pipeline is kept as a legacy demo and is not used by v2 experiments.
