"""Hypothesis-driven synthesis: environments (solution-free, checker properties) and plumbing on a mock generator."""
import json
import random

import pytest

from skillforge.envs import get_env
from skillforge.envs.base import assert_solution_free, build_split
from skillforge.hypothesis import HypothesisParseError, parse_json_block


@pytest.mark.parametrize("name", ["docrepair", "streamrec"])
def test_env_solution_free_reproducible_and_truth_scores_one(name):
    env = get_env(name)
    assert_solution_free(env)
    a, b = build_split(env, "s", 30, 7, True), build_split(env, "s", 30, 7, True)
    assert [i.input for i in a] == [i.input for i in b]
    assert {f.name for f in env.families()} == {i.family for i in a}
    for i in a:
        truth = {"items": i.hidden["items"]} if name == "docrepair" else i.hidden
        r = env.check(i, truth)
        assert r.valid and r.score == 1.0
        assert env.check(i, None).score == 0.0 and env.check(i, {"x": 1}).score == 0.0
        assert "hidden" not in json.dumps(i.input)


def test_docrepair_checker_is_positional_and_graded():
    env = get_env("docrepair")
    i = build_split(env, "s", 12, 3)[0]
    items = [dict(x) for x in i.hidden["items"]]
    n = len(items)
    items[0]["qty"] += 1
    assert env.check(i, {"items": items}).score == pytest.approx((3 * n - 1) / (3 * n))
    assert env.check(i, {"items": items[1:]}).score < 0.9          # a lost row shifts every position
    assert env.check(i, {"items": items + [items[0]]}).score == pytest.approx((3 * n - 1) / (3 * (n + 1)))
    items[1]["qty"] = str(items[1]["qty"])                           # wrong type is wrong
    assert env.check(i, {"items": items}).score == pytest.approx((3 * n - 2) / (3 * n))


def test_streamrec_truth_is_consistent_with_the_stated_semantics():
    env = get_env("streamrec")
    for inst in build_split(env, "s", 40, 11, True):
        ev = inst.input["events"]
        assert [e["t"] for e in ev] == sorted(e["t"] for e in ev)
        opens = [e["sid"] for e in ev if e["type"] == "open"]
        assert len(opens) == len(set(opens)) == len(inst.hidden["sessions"])
        assert all(e["sid"] is None for e in ev if e["type"] == "crash")
        hidden = {str(e["i"]) for e in ev if e["sid"] is None and e["type"] != "crash"}
        assert hidden == set(inst.hidden["assignments"])
        first = {}
        for e in ev:
            s = e["sid"] or inst.hidden["assignments"].get(str(e["i"]))
            if s:
                first.setdefault(s, e["type"])
        assert all(t == "open" for t in first.values())
        half = dict(inst.hidden)
        half["assignments"] = {}
        r = env.check(inst, half)
        assert r.score == pytest.approx(0.4 if hidden else 1.0)


def test_hypothesis_block_parsing():
    assert parse_json_block("x\n```json\n[{\"mechanism\": \"m\"}]\n```")[0]["mechanism"] == "m"
    with pytest.raises(HypothesisParseError):
        parse_json_block("no block")


EMPTY = ("STRATEGY: return nothing.\n```pipeline\n[{\"kind\": \"code\", \"fn\": \"solve\"}]\n```\n"
         "```python\ndef solve(state, params):\n    state[\"output\"] = {\"items\": []}\n    return state\n```")
CRUDE = '''STRATEGY: split rows on the separator.
```pipeline
[{"kind": "code", "fn": "parse"}]
```
```python
import re

def parse(state, params):
    lines = state["input"]["text"].split("\\n")
    cols = re.split(r" \\| |;|\\t|   ", lines[1].replace("COLUMNS: ", ""))
    items = []
    for ln in lines[2:]:
        f = re.split(r" \\| |;|\\t|   ", ln)
        if len(f) != 4:
            continue
        d = dict(zip(cols, f))
        try:
            q = int(d["QTY"].replace(" pcs", ""))
            u = int(round(float(d["UNIT"].replace("'", "").replace(",", ".")) * 100))
        except ValueError:
            q, u = 0, 0
        items.append({"code": d["CODE"], "qty": q, "unit_cents": u})
    state["output"] = {"items": items}
    return state
```'''
HYPS = json.dumps([{"name": f"h{i}", "mechanism": f"mechanism {i}", "strong_families": ["clean"], "weak_families": ["heavy"]}
                   for i in range(3)])


def _provider():
    from skillforge.llm import MockProvider
    prov = MockProvider()
    n = {"i": 0}

    def synth(req, p):
        n["i"] += 1
        text = req.messages[0]["content"]
        if "mechanism 1" in text and "TWO TESTED" not in text:
            return p.finalize(req, "DIAGNOSIS: weak.\nCAUSE: IMPLEMENTATION\nCHANGE: none\n\n" + CRUDE)
        if n["i"] % 5 == 0:
            return p.finalize(req, "STRATEGY: broken\n```pipeline\n[{\"kind\": \"llm\", \"name\": \"x\", \"prompt\": \"p\", \"output_key\": \"output\"}]\n```\n```python\nx = 1\n```")
        return p.finalize(req, "CHANGE: none\n\n" + EMPTY)

    prov.register("hypothesize", lambda req, p: p.finalize(req, "```json\n" + HYPS + "\n```"))
    prov.register("synthesize", synth)
    prov.register("synthesize_h", lambda req, p: p.finalize(
        req, "ANALYSIS: x\n```hypothesis\n{\"name\": \"new\", \"mechanism\": \"combined\"}\n```\n" + CRUDE))
    return prov


def test_hypothesis_experiment_end_to_end_with_mock_generator(tmp_path, monkeypatch):
    from skillforge import hexperiment as HX

    monkeypatch.setattr(HX, "exp_dir", lambda name: tmp_path)
    cfg = HX.HConfig(name="t", env="docrepair", dev=8, selection=10, audit=12, budget=10, c_initial=2, parallel=2,
                     k_hyp=3, timer="cpu", chunks=2)
    with pytest.raises(SystemExit):
        HX.prepare(cfg)                       # no diagnostic yet -> no pre-registration
    d = HX.diag(cfg, n=10, control=2, provider=_provider())
    assert d["calls"] == 1 + 3 * 2 + 2 and not d["passed"]      # mock programs are not diverse per hypothesis
    with pytest.raises(SystemExit):
        HX.prepare(cfg)                       # failed diagnostic blocks the search
    res = HX.run(cfg, provider=_provider(), check=False)
    assert res["synthesis"]["H"]["calls"] == 10 and res["synthesis"]["P"]["calls"] == 10 and res["synthesis"]["A"]["calls"] == 1
    assert {"H_final", "H_initial", "P_final", "A"} <= set(res["audit"])
    moves = [s["move"] for s in res["h_trace"]]
    explore = sum(s["calls"] for s in res["h_trace"] if s["explore"])
    assert "refine" in moves and explore >= 0.4 * sum(s["calls"] for s in res["h_trace"]) - 1
    assert any(h["origin"] in ("pivot", "recombine") for h in res["hypotheses"].values())
    assert res["attribution"]["H1"]["label"] == "leader" or res["audit"]["H_final"]["mean_score"] > 0
    assert all(c["status"] in ("consistent with measurement", "unverified conjecture") for c in res["llm_conjectures"])
    assert res["tests"]["G1"] is not None and res["provider"].endswith("(SIMULATED)")
    lin = json.loads((tmp_path / "rep0" / "lineage.json").read_text())
    assert all("hypothesis" in r for r in lin if r["arm"] == "H")
    assert any(r["problem"] and "language-model" in r["problem"] for r in lin)
