"""Unit + property-based tests for SkillForge's core machinery."""

import random

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from skillforge.domains import get_plugin
from skillforge.evolution import constrained_dominates, crowding_distance, dominates, nondominated_sort
from skillforge.ir import ArchitectureClass, SkillIR
from skillforge.stats import percentile, wilson, zero_failure_upper_bound
from skillforge.world import WorldConfig, WorldGenerator

PLUGIN = get_plugin("invoice_processing")


# ---------------------------------------------------------------- statistics
@given(st.integers(0, 500), st.integers(1, 500))
def test_wilson_bounds_contain_estimate(k, n):
    k = min(k, n)
    lo, hi = wilson(k, n)
    assert 0.0 <= lo <= k / n + 1e-12 <= hi + 1e-12 <= 1.0 + 1e-12


@given(st.integers(1, 10_000))
def test_zero_failure_bound_is_rule_of_three_like(n):
    ub = zero_failure_upper_bound(n)
    assert 0 < ub <= 1
    if n >= 30:
        assert abs(ub - 3 / n) < 0.35 * (3 / n)


@given(st.lists(st.floats(-1e6, 1e6), min_size=1, max_size=50))
def test_percentile_within_range(xs):
    assert min(xs) <= percentile(xs, 0.95) <= max(xs)


# ---------------------------------------------------------------- Pareto machinery
vec = st.lists(st.floats(-10, 10, allow_nan=False), min_size=3, max_size=3)


@given(vec, vec)
def test_dominance_is_antisymmetric(a, b):
    assert not (dominates(a, b) and dominates(b, a))


@given(st.lists(st.tuples(st.booleans(), st.floats(0, 5), vec), min_size=1, max_size=25))
@settings(max_examples=60)
def test_nondominated_sort_partitions_and_first_front_is_undominated(recs):
    recs = [(f, 0.0 if f else v, o) for f, v, o in recs]
    fronts = nondominated_sort(recs)
    flat = [i for f in fronts for i in f]
    assert sorted(flat) == list(range(len(recs)))
    for i in fronts[0]:
        assert not any(constrained_dominates(recs[j], recs[i]) for j in range(len(recs)) if j != i)


def test_feasible_always_beats_infeasible():
    feasible = (True, 0.0, [0.0, 0.0])
    infeasible = (False, 0.01, [1.0, 1.0])
    assert constrained_dominates(feasible, infeasible)
    assert not constrained_dominates(infeasible, feasible)


def test_crowding_extremes_infinite():
    d = crowding_distance([[0, 1], [0.5, 0.5], [1, 0]])
    assert d[0] == float("inf") and d[2] == float("inf") and d[1] < float("inf")


# ---------------------------------------------------------------- world reproducibility / split isolation
def test_world_is_reproducible_by_seed():
    a = WorldGenerator(PLUGIN, 11, WorldConfig(40, 20, 40)).build()
    b = WorldGenerator(PLUGIN, 11, WorldConfig(40, 20, 40)).build()
    for split in a:
        assert [s.to_dict() for s in a[split]] == [s.to_dict() for s in b[split]]


def test_splits_are_disjoint_and_drift_is_holdout_only():
    w = WorldGenerator(PLUGIN, 3, WorldConfig(60, 40, 80)).build()
    texts = {k: {s.input["text"] for s in v if s.input["text"].strip()} for k, v in w.items()}
    assert not (texts["train"] & texts["holdout"])
    assert not (texts["validation"] & texts["holdout"])
    assert all(s.family != "layout_drift" for s in w["train"] + w["validation"])
    assert any(s.family == "layout_drift" for s in w["holdout"])


@given(st.integers(0, 2**32), st.sampled_from([f.name for f in PLUGIN.families()]))
@settings(max_examples=80, deadline=None)
def test_ground_truth_is_internally_consistent(seed, family):
    s = PLUGIN.generate(family, random.Random(seed), "t", seed)
    exp = s.expected
    assert exp["status"] in ("valid", "invalid", "needs_review")
    if exp["status"] == "valid":
        assert abs(exp["subtotal"] + exp["tax"] - exp["total"]) < 0.011
        assert abs(sum(i["amount"] for i in exp["line_items"]) - exp["subtotal"]) < 0.011 * max(1, len(exp["line_items"]))
    for k in exp["_absent"]:
        if k in ("vendor", "invoice_number", "date", "total"):
            assert exp[k] is None


@given(st.integers(0, 2**32))
@settings(max_examples=40, deadline=None)
def test_perturbations_preserve_or_track_truth(seed):
    rng = random.Random(seed)
    s = PLUGIN.generate("clean_labeled", rng, "t", seed)
    for p in PLUGIN.perturbations():
        out = p.apply(s, random.Random(seed))
        if out is None:
            continue
        assert out.expected["status"] in ("valid", "invalid", "needs_review")
        if p.id == "p_dup":
            assert "duplicate_invoice" in out.expected["issues"] and out.expected["status"] == "needs_review"
        elif p.id == "p_inject":
            assert out.expected["status"] == s.expected["status"]


# ---------------------------------------------------------------- IR
def test_seed_population_is_architecturally_diverse():
    spec = PLUGIN.build_spec("invoices", {})
    archs = {ir.architecture() for ir in PLUGIN.seed_candidates(spec)}
    assert len(archs) >= 6
    assert ArchitectureClass.TOOL_AGENT in archs and ArchitectureClass.DETERMINISTIC in archs


def test_no_llm_constraint_removes_llm_seeds():
    spec = PLUGIN.build_spec("invoices", {"no_llm": True})
    assert all(not ir.llm_components() for ir in PLUGIN.seed_candidates(spec))


def test_ir_roundtrip_and_fingerprint_stability():
    spec = PLUGIN.build_spec("invoices", {})
    for ir in PLUGIN.seed_candidates(spec):
        again = SkillIR.model_validate_json(ir.model_dump_json())
        assert again.fingerprint() == ir.fingerprint()
        assert again.architecture() == ir.architecture()


@pytest.mark.parametrize("seed", range(5))
def test_mutations_produce_valid_ir(seed):
    from skillforge.llm import MockProvider
    from skillforge.synthesis import CandidateSynthesizer

    spec = PLUGIN.build_spec("invoices", {})
    syn = CandidateSynthesizer(spec, PLUGIN, MockProvider(), random.Random(seed))
    for parent in PLUGIN.seed_candidates(spec):
        child = syn.mutate(parent, 1, 3)
        if child is None:
            continue
        assert child.parents == [parent.id]
        SkillIR.model_validate_json(child.model_dump_json())
        names = [c.name for c in child.components]
        assert names[-1] == "finalize"


@given(st.floats(0.5, 0.99))
@settings(max_examples=30)
def test_min_n_for_lower_bound_is_minimal(t):
    from skillforge.stats import min_n_for_lower_bound

    n = min_n_for_lower_bound(t)
    assert wilson(n, n)[0] >= t
    assert n == 1 or wilson(n - 1, n - 1)[0] < t


@given(st.integers(0, 2**32), st.sampled_from(["clean_labeled", "clean_table", "email_prose", "format_variants",
                                                "credit_note", "very_long", "arithmetic_error"]))
@settings(max_examples=60, deadline=None)
def test_domain_library_can_read_every_generated_layout(seed, family):
    """Coverage check of the building-block pools: with every pool entry enabled, the deterministic
    library must recover the ground truth of generated documents (otherwise the search space has a
    blind spot that no amount of optimisation can fix)."""
    from skillforge.domains.invoice.plugin import reference_read

    s = PLUGIN.generate(family, random.Random(seed), "t", seed)
    out = reference_read(s.input["text"], s.input["context"])
    exp = s.expected
    assert len(out["line_items"]) == len(exp["line_items"])
    for k in ("invoice_number", "date", "total"):
        assert out[k] == exp[k], (k, out[k], exp[k])
