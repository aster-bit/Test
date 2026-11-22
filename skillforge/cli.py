"""Command-line entry point.

    skillforge demo                      # invoice demo; runs until a skill is certified (Ctrl+C to stop)
    skillforge run "Создай максимально надёжный скилл для обработки счетов ..."
    skillforge serve --port 8000         # API for the dashboard

LLM stages go through Claude Code (`claude -p`, your Claude Code login) when the CLI is installed;
`--provider mock` runs fully offline with a simulated model.
Budget is unlimited: the run continues until the target is met. First Ctrl+C stops gracefully after
the current generation and exports the best skill so far (marked NOT certified); a second Ctrl+C aborts.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys

DEMO_REQUEST = (
    "Создай максимально надёжный скилл для обработки входящих счетов (invoices): извлечь реквизиты, "
    "проверить арифметику, обнаружить пропущенные, противоречивые и повторные счета, никогда не одобрять "
    "некорректный счёт и не выдумывать отсутствующие данные. Задержка p95 до 8 s, не дороже $0.02 за документ."
)


def main(argv: list[str] | None = None) -> int:
    from .llm import claude_code_available
    from .pipeline import RunConfig, SkillForge
    from .world import WorldConfig

    ap = argparse.ArgumentParser(prog="skillforge")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "demo"):
        p = sub.add_parser(name)
        if name == "run":
            p.add_argument("request")
        p.add_argument("--seed", type=int, default=7)
        p.add_argument("--provider", default="auto", choices=["auto", "claude_code", "mock", "anthropic"])
        p.add_argument("--sandbox", default="auto", choices=["auto", "docker", "subprocess"])
        p.add_argument("--out", default="runs")
        p.add_argument("--db", default="runs/skillforge.db")
        p.add_argument("--train", type=int, default=None, help="default 160 (mock) / 60 (claude_code)")
        p.add_argument("--validation", type=int, default=None, help="default 150 (mock) / 80 (claude_code)")
        p.add_argument("--holdout", type=int, default=None, help="default 300 (mock) / 150 (claude_code)")
        p.add_argument("--concurrency", type=int, default=4, help="parallel claude -p calls")
        p.add_argument("--target", type=float, default=None,
                       help="required lower 95%% bound of the holdout pass rate (default from request or 0.95)")
        p.add_argument("--max-generations", type=int, default=None, help="safety limit; default unlimited")
        p.add_argument("--max-holdout-attempts", type=int, default=None, help="default unlimited")
    s = sub.add_parser("serve")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--db", default="runs/skillforge.db")
    args = ap.parse_args(argv)

    if args.cmd == "serve":
        import os

        import uvicorn

        os.environ["SKILLFORGE_DB"] = args.db
        uvicorn.run("skillforge.api:app", host=args.host, port=args.port)
        return 0

    real = args.provider in ("claude_code", "anthropic") or (args.provider == "auto" and claude_code_available())
    sizes = (60, 80, 150) if real else (160, 150, 300)
    world = WorldConfig(args.train or sizes[0], args.validation or sizes[1], args.holdout or sizes[2])
    cfg = RunConfig(seed=args.seed, provider=args.provider, sandbox_backend=args.sandbox, output_dir=args.out,
                    db_path=args.db, world=world, concurrency=args.concurrency,
                    target_pass_lower_bound=args.target, max_generations=args.max_generations,
                    max_holdout_attempts=args.max_holdout_attempts)
    forge = SkillForge(cfg)
    hits = {"n": 0}

    def on_sigint(signum, frame):
        hits["n"] += 1
        if hits["n"] == 1:
            print("\n[skillforge] stopping after the current generation; exporting best-so-far (Ctrl+C again to "
                  "abort)", file=sys.stderr)
            forge.cancel()
        else:
            raise KeyboardInterrupt

    signal.signal(signal.SIGINT, on_sigint)
    request = DEMO_REQUEST if args.cmd == "demo" else args.request
    print(f"[skillforge] provider={'claude_code' if real and args.provider != 'anthropic' else args.provider}; "
          "budget unlimited; running until a skill is certified (Ctrl+C to stop)", file=sys.stderr)
    report = forge.run(request)
    rec = report["recommended"]
    h = report["holdout"][rec["id"]]
    print(json.dumps({
        "run_id": report["run_id"], "certified": report["certified"], "recommended": rec,
        "holdout_pass_rate": h["metrics"]["pass_rate"], "holdout_pass_ci": h["intervals"]["pass_rate"],
        "hard_violations": h["hard_violations"], "holdout_attempts": len(report["holdout_attempts"]),
        "generations": len(report["generations"]) - 1, "restarts": len(report["restarts"]),
        "acceptance": [(a["id"], a["passed"]) for a in report["acceptance"]],
        "simulated_llm": report["simulated"], "spend_usd": report["spend"]["usd"],
        "stop_reason": report["stop_reason"], "artifacts": report["artifacts"],
    }, indent=2, ensure_ascii=False))
    return 0 if report["certified"] else 2


if __name__ == "__main__":
    sys.exit(main())
