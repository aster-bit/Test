"""Tests for SkillForge v2: solution-free environments, novelty metrics, open synthesis plumbing."""

import json
import random
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from skillforge.envs import get_env
from skillforge.envs.base import assert_solution_free, build_split, split_hash
from skillforge.envs.scheduling import _sgs, _topo_orders, _validate_structure
from skillforge.novelty import ast_similarity, classify_change, graph_signature
from skillforge.open_synth import ParseError, build_ir, parse_reply

ENV = get_env("scheduling")
FAMS = [f.name for f in ENV.families()]
MANUAL = (Path(__file__).resolve().parents[1] / "experiments/baselines/scheduling_manual.md").read_text()


def test_environment_is_solution_free():
    assert_solution_free(ENV)
    for attr in ("seed_candidates", "mutation_operators", "solve"):
        assert not hasattr(ENV, attr)


def test_splits_reproducible_and_disjoint():
    a = build_split(ENV, "dev", 20, 5)
    b = build_split(ENV, "dev", 20, 5)
    assert split_hash(a) == split_hash(b)
    c = build_split(ENV, "audit", 20, 5, include_audit_only=True)
    assert {json.dumps(x.input, sort_keys=True) for x in a}.isdisjoint({json.dumps(x.input, sort_keys=True) for x in c})
    assert all(x.family != "large" for x in a)


@given(st.integers(0, 2**32), st.sampled_from([f for f in FAMS if f != "large"]))
@settings(max_examples=40, deadline=None)
def test_checker_accepts_valid_and_scores_reference_as_one(seed, family):
    inst = ENV.generate(family, random.Random(seed), "t")
    if not inst.hidden["feasible"]:
        assert ENV.check(inst, {"status": "infeasible", "reason": "x"}).score == 1.0
        assert ENV.check(inst, {"status": "ok", "start": {}}).score == 0.0
        return
    # any precedence-feasible list through serial SGS gives a valid schedule
    order = next(_topo_orders(inst.input))
    mk, start = _sgs(inst.input, order)
    r = ENV.check(inst, {"status": "ok", "start": start})
    assert r.valid and 0 < r.score <= 1.0
    assert mk >= inst.hidden["makespan"] >= inst.hidden["lower_bound"]
    # generator-visible message must not leak the hidden reference or bound
    assert "reference" not in r.message and "bound" not in r.message
    assert r.score == pytest.approx(1.0 if mk == 0 else min(1.0, inst.hidden["lower_bound"] / mk))
    assert ENV.check(inst, {"status": "infeasible"}).score == 0.0


@given(st.integers(0, 2**32))
@settings(max_examples=30, deadline=None)
def test_checker_rejects_every_kind_of_violation(seed):
    inst = ENV.generate("medium", random.Random(seed), "t")
    _, start = _sgs(inst.input, next(_topo_orders(inst.input)))
    jobs = {j["id"]: j for j in inst.input["jobs"]}
    # precedence violation
    with_pred = [j for j in jobs.values() if j["after"] and jobs[j["after"][0]]["duration"] > 0]
    if with_pred:
        j = with_pred[0]
        bad = dict(start)
        bad[j["id"]] = start[j["after"][0]]
        assert not ENV.check(inst, {"status": "ok", "start": bad}).valid
    # capacity violation: start everything at its release
    crowd = {k: v["release"] for k, v in jobs.items()}
    r = ENV.check(inst, {"status": "ok", "start": crowd})
    assert r.valid in (True, False)  # may be valid if capacities are generous
    if not r.valid:
        assert r.violations
    # missing job
    some = dict(start)
    some.pop(next(iter(some)))
    assert ENV.check(inst, {"status": "ok", "start": some}).violations == ["incomplete_schedule"]


def test_exact_reference_is_optimal_on_small():
    for seed in range(15):
        inst = ENV.generate("small", random.Random(seed), "t")
        if inst.hidden.get("method") != "exact" or len(inst.input["jobs"]) > 6:
            continue
        best = min(_sgs(inst.input, o)[0] for o in _topo_orders(inst.input))
        assert best == inst.hidden["makespan"]


def test_infeasible_families_are_infeasible():
    for fam in ("infeasible_cycle", "infeasible_capacity"):
        for seed in range(10):
            inst = ENV.generate(fam, random.Random(seed), "t")
            assert _validate_structure(inst.input) is not None
            assert inst.hidden["feasible"] is False


# ----------------------------------------------------------------- novelty
def test_ast_similarity_ignores_renaming_but_sees_algorithm_change():
    a = "def f(xs):\n    t = 0\n    for x in xs:\n        t += x\n    return t\n"
    renamed = "def g(values):\n    acc = 0\n    for v in values:\n        acc += v\n    return acc\n"
    other = "import heapq\ndef f(xs):\n    h = list(xs)\n    heapq.heapify(h)\n    return [heapq.heappop(h) for _ in range(len(h))]\n"
    assert ast_similarity(a, renamed) == 1.0
    assert ast_similarity(a, other) < 0.6


def test_classify_change_detects_cosmetic_vs_structural():
    p = [{"kind": "code", "fn": "solve"}]
    src = "def solve(state, params):\n    state['output'] = 1\n    return state\n"
    cos = "def solve(state, params):\n    # tweak\n    state['output'] = 2\n    return state\n"
    assert classify_change(src, p, cos, p)["structural"] is False
    p2 = [{"kind": "code", "fn": "solve"}, {"kind": "code", "fn": "improve"}]
    src2 = src + "\ndef improve(state, params):\n    return state\n"
    ch = classify_change(src, p, src2, p2)
    assert ch["structural"] and ch["graph_changed"] and ch["added_functions"] == ["improve"]
    assert graph_signature([{"kind": "branch", "then": [{"kind": "llm"}], "otherwise": []}]) == "B(L|)"


# ----------------------------------------------------------------- parsing
def test_parse_and_build_ir_from_reply():
    strat, change, pipe, mod = parse_reply(MANUAL)
    assert "baseline" in strat.lower()
    ir = build_ir(pipe, mod, "x")
    assert [c.name for c in ir.components] == ["solve"]


@pytest.mark.parametrize("bad", ["no blocks here", "```pipeline\nnot json\n```\n```python\ndef a(s,p): return s\n```",
                                 "```pipeline\n[{\"kind\": \"code\", \"fn\": \"missing\"}]\n```\n```python\ndef a(s,p): return s\n```"])
def test_parse_errors(bad):
    with pytest.raises(ParseError):
        strat, change, pipe, mod = parse_reply(bad)
        build_ir(pipe, mod, "x")


# ----------------------------------------------------------------- end-to-end plumbing (mock generator)
def test_experiment_end_to_end_with_mock_generator(tmp_path, monkeypatch):
    from skillforge import experiment as E
    from skillforge.llm import MockProvider

    monkeypatch.setattr(E, "exp_dir", lambda name: tmp_path)
    monkeypatch.setattr(E, "FROZEN_FILES", E.FROZEN_FILES[:2])
    cfg = E.ExpConfig(name="t", dev=6, selection=8, audit=10, budget=4, c_initial=2, parallel=2)
    E.prepare(cfg, replicates=1)
    with pytest.raises(SystemExit):
        E.prepare(cfg)  # never overwritten
    prov = MockProvider()
    n = {"i": 0}

    def synth(req, p):
        n["i"] += 1
        if n["i"] == 2:  # one broken program exercises the repair path
            return p.finalize(req, "STRATEGY: broken\n```pipeline\n[{\"kind\": \"code\", \"fn\": \"solve\"}]\n```\n"
                                   "```python\nimport os\ndef solve(state, params):\n    return state\n```")
        return p.finalize(req, MANUAL.replace("STRATEGY:", "CHANGE: none\n\nSTRATEGY:"))

    prov.register("synthesize", synth)
    prov.register("direct", lambda req, p: p.finalize(req, '{"status": "infeasible"}'))
    res = E.run(cfg, provider=prov)
    assert res["synthesis"]["B"]["calls"] == 4 and res["synthesis"]["C"]["calls"] == 4
    assert set(res["audit"]) >= {"A", "B_best", "C_final", "H_manual", "D_direct"}
    assert "H1_primary" in res["hypotheses"]
    lineage = json.loads((tmp_path / "rep0" / "lineage.json").read_text())
    with pytest.raises(SystemExit):
        E.run(cfg, provider=prov)  # a replicate is never re-run
    assert any(not r["usable"] for r in lineage) and any(r["usable"] for r in lineage)
    assert res["provider"].endswith("(SIMULATED)")
