# Example output: certified invoice-processing skill

Produced by `skillforge demo --provider mock` (seed 7): 15 generations, 1 diversity restart,
certified on the first sealed holdout (300 scenarios, opened once).

| holdout metric | value | 95% interval |
|---|---|---|
| pass rate | 0.993 | [0.976, 0.998] |
| hard-invariant violations | 0 | rate ≤ 0.010 |
| layout drift (never seen in search) | 0.926 | [0.77, 0.98] |
| cost / document | $0.0012 (simulated) | — |

**Important:** this run used the offline mock provider, so the LLM fallback stage was executed against a
*simulated* model. Deterministic stages ran for real in the sandbox. Re-run with Claude Code
(`skillforge demo`, the default when the `claude` CLI is installed) before relying on the LLM stage.

* `skill/` — the exported Claude Skill package (`SKILL.md`, `scripts/`, `references/`, `tests/`, `manifest.json`).
  The LLM stage runs through `claude -p`; without the CLI it fails closed (routes to human review).
* `report.md` — the full SkillForge report.
