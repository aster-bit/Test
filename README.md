# SkillForge — Autonomous Skill Synthesis Engine

SkillForge turns a natural-language description of a professional task into a specialised,
repeatedly tested, optimised and **certified** executable skill, exported as a Claude-compatible
Skill package (`SKILL.md`, `scripts/`, `references/`, `tests/`, `manifest.json`).

It is not a chat bot or an agent wrapper: it is a search system. It builds test worlds with ground
truth, synthesises architecturally different candidates, runs them in a sandbox, attacks them with an
adversary, evolves them under hard constraints, and certifies the result on a sealed holdout.

**LLM calls go through Claude Code** (headless `claude -p`, using your Claude Code login — no API key).
Without the `claude` CLI, or with `--provider mock`, everything runs offline against a *simulated* model,
and every report says so.

**Budget is unlimited by default.** A run continues until a skill is certified against the target
(see below) or you stop it (Ctrl+C / "Stop" in the dashboard) — then the best skill found so far is
exported and clearly marked **NOT CERTIFIED**.

## Quick start

```bash
pip install -e ".[dev]"
skillforge demo                      # invoice-processing demo via Claude Code; runs until certified
skillforge demo --provider mock      # fully offline, simulated LLM
skillforge run "Создай максимально надёжный скилл для обработки счетов ..., цель 97%"
skillforge serve --port 8000         # API for the dashboard
cd dashboard && npm install && npm run dev   # http://localhost:3000 (NEXT_PUBLIC_SKILLFORGE_API to override)
pytest                               # unit, property-based (Hypothesis), sandbox and end-to-end tests
```

Useful flags: `--target 0.97` (required lower 95% bound of the holdout pass rate), `--concurrency 8`
(parallel `claude -p` calls), `--train/--validation/--holdout` (scenario counts), and optional safety
limits `--max-generations`, `--max-holdout-attempts` (both unlimited by default).

## What "ideal" means here

Perfection cannot be proven on a finite sample, so the target is operational (`SkillSpec.target`):

1. **zero** hard-invariant violations,
2. every constraint met (latency, cost per item, usefulness floor, optional no-LLM),
3. lower bound of the Wilson 95% interval of the pass rate ≥ target (default 0.95),

first on the validation split (the *gate*), then on a **sealed holdout opened exactly once**. If a
candidate passes the gate but fails the holdout, that holdout is retired into the training data, a fresh
independent holdout is generated (its sha256 commitment is logged before use) and the search continues.
Every attempt is listed in the report.

## Pipeline

```
request ─► DomainCompiler ─► SkillSpec (Pydantic: objectives, I/O, constraints, invariants, metrics,
                               risks, budget, target, acceptance criteria, verification limits)
        ─► WorldGenerator ─► train / validation / sealed holdout (seeded, realism-labelled families)
        ─► CandidateSynthesizer ─► diverse Skill IR candidates (9 architecture classes)
        ─► loop:  ExecutionSandbox ─► EvaluationEngine ─► NSGA-II (ε-constraint, novelty, elitism)
                  AdversarialEngine ─► new failure cases into train (never sees validation/holdout)
                  mutation (failure-directed + adaptive) / crossover / LLM code patches (Claude Code)
                  stagnation ─► diversity restart;   gate ─► holdout certification
        ─► SkillCompiler ─► skill package + report
```

| module | file | role |
|---|---|---|
| DomainCompiler | `skillforge/domain_compiler.py` | RU/EN request parsing, optional `claude -p --json-schema` analysis, refuses domains without objective evaluation |
| Skill IR | `skillforge/ir.py` | typed `code` / `llm` / `branch` components; architecture class derived from structure |
| WorldGenerator | `skillforge/world.py` | split isolation via independent seed namespaces; holdout-only drift family |
| CandidateSynthesizer | `skillforge/synthesis.py` | seeds, failure-directed + adaptive mutation, crossover, sandboxed LLM code patches |
| ExecutionSandbox | `skillforge/sandbox.py`, `sandbox_runner.py` | Docker (`--network none`, read-only, cap-drop, memory/cpu/pids limits) or `unshare -rn` + `python -I -S` + rlimits + PEP 578 audit hook; static AST policy first; empty environment |
| Executor | `skillforge/executor.py` | host-side IR interpreter; code batched into the sandbox, LLM steps on the host |
| EvaluationEngine | `skillforge/evaluation.py` | invariants, ground truth, metamorphic relations, differential agreement, Wilson / exact zero-failure bounds |
| AdversarialEngine | `skillforge/adversary.py` | hill-climbing over truth-tracking perturbations against the current front |
| EvolutionEngine | `skillforge/evolution.py` | constrained NSGA-II, ε schedule, novelty slots, racing for expensive candidates, restarts |
| ExperimentStore | `skillforge/store.py` | SQLite (Postgres-portable SQL): candidates, IR, params, prompts, evaluations, scenarios, events, holdout locks |
| SkillCompiler | `skillforge/compiler.py`, `export_runtime.py` | Claude Skill package with sandboxed standalone runtime; LLM stages via `claude -p`, fail closed without it |
| ResearchDashboard | `skillforge/api.py`, `dashboard/` | FastAPI + Next.js (TypeScript): progress, Pareto scatter, finalists, certification attempts, events, report |
| LLM providers | `skillforge/llm.py` | `ClaudeCodeProvider` (default), `MockProvider` (simulated, offline), `AnthropicProvider` (opt-in only) |

## Demo domain: invoice processing

Input: invoice text + `{"seen_invoice_ids": [...]}`. Output: vendor, number, ISO date, currency, line
items, subtotal/tax/total, document type, `status ∈ {valid, invalid, needs_review}`, issue codes.

* Hard invariants: `schema_valid`, `no_fabrication` (absent required fields stay null),
  `valid_implies_consistent` (formal arithmetic check on the output itself), `never_approve_bad`.
* 14 scenario families: normal, edge (number/date formats), arithmetic errors, missing data, conflicting
  totals, OCR/dependency failure, very long documents (resource), duplicate submissions (concurrency),
  credit notes (unexpected sequence), prompt injection, and a **holdout-only layout-drift** family.
  Each family is labelled realistic / synthetic / adversarial.
* Seed architectures: deterministic, rule-based, prompt-only, prompt+scripts, hybrid code-first with LLM
  fallback, multi-step LLM, tool-using agent, state machine, plus a deliberately risky guessing design.

## Honesty rules built into the system

* No LLM judge in the demo domain: every criterion has an objective check. A request for a domain with no
  plugin is refused rather than optimised against an LLM judge.
* Simulated-model results are labelled everywhere (report, SKILL.md, manifest, dashboard).
* Validation reuse is disclosed as optimistically biased; only holdout numbers are unbiased.
* Retired holdouts are disclosed; the report never claims a global optimum.

## Adding a domain

Implement `skillforge/domains/base.py::DomainPlugin` (spec builder, families + generator with ground truth,
invariant checkers, scorer, perturbations, metamorphic relations, building blocks, mutation operators,
failure hints) and register it in `skillforge/domains/__init__.py`.
