"""FastAPI backend for the ResearchDashboard.

    uvicorn skillforge.api:app --port 8000      (or: skillforge serve)

Runs execute in background threads; the dashboard polls /runs/{id}/events.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field

from .pipeline import RunConfig, SkillForge
from .store import ExperimentStore
from .world import WorldConfig

DB = os.environ.get("SKILLFORGE_DB", "runs/skillforge.db")
OUT = os.environ.get("SKILLFORGE_OUT", str(Path(DB).parent))

app = FastAPI(title="SkillForge", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=os.environ.get("SKILLFORGE_CORS", "http://localhost:3000").split(","),
                   allow_methods=["*"], allow_headers=["*"])
store = ExperimentStore(DB)
_forges: dict[str, SkillForge] = {}


class RunRequest(BaseModel):
    request: str = Field(min_length=10)
    seed: int = 7
    provider: str = Field("auto", pattern="^(auto|claude_code|mock|anthropic)$")
    train: int = Field(160, ge=20, le=5000)
    validation: int = Field(150, ge=20, le=5000)
    holdout: int = Field(300, ge=30, le=10000)
    concurrency: int = Field(4, ge=1, le=32)
    target: float | None = Field(None, ge=0.5, le=0.999, description="holdout pass-rate lower 95% bound")


@app.get("/health")
def health() -> dict[str, Any]:
    from .llm import claude_code_available
    from .sandbox import ExecutionSandbox

    return {"ok": True, "claude_code_cli": claude_code_available(), "sandbox": ExecutionSandbox().describe()}


@app.post("/runs")
def start_run(body: RunRequest) -> dict[str, str]:
    import time
    import uuid

    run_id = "run-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    cfg = RunConfig(seed=body.seed, provider=body.provider, output_dir=OUT, db_path=DB,
                    world=WorldConfig(body.train, body.validation, body.holdout), concurrency=body.concurrency,
                    target_pass_lower_bound=body.target)
    forge = SkillForge(cfg, store)
    _forges[run_id] = forge

    def target():
        try:
            forge.run(body.request, run_id=run_id)
        except Exception:  # noqa: BLE001 - recorded in the store by the pipeline
            pass

    threading.Thread(target=target, daemon=True, name=run_id).start()
    return {"run_id": run_id}


@app.post("/runs/{run_id}/cancel")
def cancel(run_id: str) -> dict[str, bool]:
    f = _forges.get(run_id)
    if not f:
        raise HTTPException(404, "run not active in this process")
    f.cancel(run_id)
    return {"cancelled": True}


@app.get("/runs")
def runs() -> list[dict[str, Any]]:
    return store.runs()


def _run_or_404(run_id: str) -> dict[str, Any]:
    r = store.run(run_id)
    if not r:
        raise HTTPException(404, "unknown run")
    return r


@app.get("/runs/{run_id}")
def run(run_id: str) -> dict[str, Any]:
    r = _run_or_404(run_id)
    r["scenario_counts"] = store.scenario_counts(run_id)
    r["artifacts"] = store.artifacts(run_id)
    r["holdout_attempts"] = store.holdout_attempts(run_id)
    return r


@app.get("/runs/{run_id}/generations")
def generations(run_id: str) -> list[dict[str, Any]]:
    _run_or_404(run_id)
    return store.generations(run_id)


@app.get("/runs/{run_id}/candidates")
def candidates(run_id: str) -> list[dict[str, Any]]:
    _run_or_404(run_id)
    latest: dict[str, dict[str, Any]] = {}
    for e in store.evaluations(run_id):
        latest[(e["candidate_id"], e["split"])] = e
    out = []
    for c in store.candidates(run_id):
        tr = latest.get((c["id"], "train"))
        val = latest.get((c["id"], "validation"))
        hold = latest.get((c["id"], "holdout"))
        c["train"] = tr["summary"]["metrics"] if tr else None
        c["train_feasible"] = bool(tr["feasible"]) if tr else None
        c["validation"] = val["summary"]["metrics"] if val else None
        c["holdout"] = hold["summary"]["metrics"] if hold else None
        out.append(c)
    return out


@app.get("/candidates/{cand_id}")
def candidate(cand_id: str) -> dict[str, Any]:
    c = store.candidate(cand_id)
    if not c:
        raise HTTPException(404, "unknown candidate")
    return c


@app.get("/runs/{run_id}/events")
def events(run_id: str, after: int = 0) -> list[dict[str, Any]]:
    _run_or_404(run_id)
    return store.events(run_id, after)


@app.get("/runs/{run_id}/report", response_class=PlainTextResponse)
def report(run_id: str) -> str:
    _run_or_404(run_id)
    p = Path(OUT) / run_id / "report.md"
    if not p.exists():
        raise HTTPException(404, "report not ready")
    return p.read_text()


@app.get("/runs/{run_id}/report.json")
def report_json(run_id: str):
    _run_or_404(run_id)
    p = Path(OUT) / run_id / "report.json"
    if not p.exists():
        raise HTTPException(404, "report not ready")
    return FileResponse(p, media_type="application/json")
