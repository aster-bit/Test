"""End-to-end smoke test of the full pipeline in offline (mock) mode, plus export checks."""

import json
import subprocess
import sys

import pytest

from skillforge.domain_compiler import UnsupportedDomain, parse_request
from skillforge.pipeline import RunConfig, SkillForge
from skillforge.store import ExperimentStore, HoldoutAlreadyOpened
from skillforge.world import WorldConfig


def test_parse_request_ru_en():
    p = parse_request("Скилл для счетов без LLM, бюджет $5, задержка p95 до 300 мс, $0.001 за документ, 3 поколения")
    assert p["budget"]["max_usd"] == 5.0  # an explicit budget is still honoured; default is unlimited
    assert p["latency_ms_p95"] == 300.0
    assert p["cost_per_item_usd"] == 0.001
    assert p["budget"]["max_generations"] == 3
    assert p["no_llm"] is True


def test_unknown_domain_is_refused():
    from skillforge.domain_compiler import DomainCompiler

    with pytest.raises(UnsupportedDomain):
        DomainCompiler().compile("Write a skill that composes sonnets about the sea")


def test_holdout_lock(tmp_path):
    st = ExperimentStore(tmp_path / "db.sqlite")
    st.create_run("r", "x", 1, "mock", {}, {})
    st.open_holdout("r", 0)
    with pytest.raises(HoldoutAlreadyOpened):
        st.open_holdout("r", 0)
    st.open_holdout("r", 1)  # a *new* holdout set may be opened once
    assert st.run("r")["holdout_opened"] == 2


@pytest.fixture(scope="module")
def small_run(tmp_path_factory):
    out = tmp_path_factory.mktemp("run")
    cfg = RunConfig(seed=3, provider="mock", output_dir=str(out), db_path=str(out / "db.sqlite"),
                    world=WorldConfig(50, 40, 60), adversary_probes=8, metamorphic_sample=6, max_generations=2)
    req = "Надёжный скилл для обработки счетов, популяция 10"
    report = SkillForge(cfg).run(req)
    return report, out, cfg


def test_pipeline_end_to_end(small_run):
    report, out, cfg = small_run
    rec = report["recommended"]["id"]
    assert report["holdout_opened_once"]
    assert rec in report["holdout"]
    assert report["simulated"] is True
    assert any("SIMULATED" in lim for lim in report["limitations"])
    assert len(report["generations"]) >= 2
    # adversarial cases were added to train but no adversary id leaks into validation/holdout worlds
    st = ExperimentStore(cfg.db_path)
    run_id = report["run_id"]
    rows = st._all("SELECT id, split FROM scenarios WHERE run_id=?", (run_id,))
    assert not [r for r in rows if r["split"] == "holdout" and r["id"].startswith("adv-")]
    assert st.run(run_id)["holdout_opened"] >= 1
    assert report["certified"] or any("NOT CERTIFIED" in lim for lim in report["limitations"])
    # every evaluated candidate is persisted with its IR
    cands = st.candidates(run_id)
    assert len(cands) >= 10
    assert st.candidate(cands[0]["id"])["ir"]["components"]


def test_exported_package(small_run):
    report, out, cfg = small_run
    pkg = next(p for p in map(__import__("pathlib").Path, report["artifacts"]) if p.is_dir())
    for f in ("SKILL.md", "manifest.json", "scripts/skill.py", "scripts/skill_ir.json", "references/spec.json",
              "references/report.md", "tests/test_skill.py"):
        assert (pkg / f).exists(), f
    skill_md = (pkg / "SKILL.md").read_text()
    assert skill_md.startswith("---\nname: ")
    manifest = json.loads((pkg / "manifest.json").read_text())
    assert manifest["candidate_id"] == report["recommended"]["id"]
    doc = {"text": "INVOICE\nVendor: Foo Ltd\nInvoice No: A-17\nDate: 2024-02-03\nCurrency: USD\n\nItems:\n"
                   "- Pen x2 @ 1.50 = 3.00\n\nSubtotal: 3.00\nTax: 0.30\nTotal: 3.30",
           "context": {"seen_invoice_ids": []}}
    env = {"PATH": "/usr/bin:/bin"}  # no claude CLI on PATH -> LLM stages fail closed
    p = subprocess.run([sys.executable, str(pkg / "scripts" / "skill.py")], input=json.dumps(doc),
                       capture_output=True, text=True, timeout=120, env=env)
    assert p.returncode == 0, p.stderr
    out_doc = json.loads(p.stdout)
    assert out_doc["status"] in ("valid", "needs_review")
    if out_doc["status"] == "valid":
        assert out_doc["total"] == 3.3


def test_per_item_cost_is_not_a_budget():
    p = parse_request("не дороже $0.02 за документ, $5 per document")
    assert "max_usd" not in p["budget"]
    assert parse_request("бюджет неограничен, цель 97%")["target_pass"] == 0.97
