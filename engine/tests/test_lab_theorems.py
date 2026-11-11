"""Tests for the lab theorem library + virtual mathematician (omicairn.lab_theorems).

Pure-Python store/graph/retrieval/curriculum logic is tested without a Lean
toolchain by monkeypatching `lean_verify.verify_lean_source`. A single
end-to-end test that needs the REAL toolchain is gated on
`lean_toolchain_available()` (skipped otherwise), mirroring test_lean_verify.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from omicairn import lab_theorems as ml
from omicairn import lean_verify
from omicairn.constants import CurriculumLevel as CL, TheoremStatus


# --- helpers ---------------------------------------------------------------

def _fake_verdict(compiled=True, source="", theorems=None, substance=None,
                  tool_missing=False):
    return lean_verify.LeanVerdict(
        compiled=compiled, theorems_checked=theorems or [],
        substance_warnings=substance or [], tool_missing=tool_missing,
        lean_file="x.lean",
    )


# A non-trivial mocked Lean source for store/graph/retrieval tests where the
# proof CONTENT is irrelevant (verify_lean_source is mocked) but the source must
# NOT trip the library-trivia substance gate (which would demote it to
# `verified_trivial`). A named-lemma wrapper is the canonical "honest short
# proof" shape — content the gate must accept.
_NONTRIVIAL_SRC = "theorem t (a b : Nat) : a + b = b + a := Nat.add_comm a b"


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """A temp store root; monkeypatch resolve_root to it."""
    root = tmp_path / "store"
    root.mkdir()
    monkeypatch.setattr(ml, "resolve_root", lambda explicit=None: root)
    return root


# --- store I/O -------------------------------------------------------------

def test_put_and_load_roundtrip(store, monkeypatch):
    monkeypatch.setattr(
        ml.lean_verify, "verify_lean_source",
        lambda *a, **k: _fake_verdict(theorems=["t"]))
    e = ml.add_theorem("t", "a fact", _NONTRIVIAL_SRC,
                       curriculum_level=CL.ELEMENTARY)
    assert e.status == TheoremStatus.VERIFIED
    loaded = ml.load()
    assert "t" in loaded
    assert loaded["t"].statement == "a fact"
    assert loaded["t"].curriculum_level == CL.ELEMENTARY


def test_last_write_wins(store, monkeypatch):
    monkeypatch.setattr(ml.lean_verify, "verify_lean_source",
                        lambda *a, **k: _fake_verdict())
    ml.add_theorem("t", "v1", _NONTRIVIAL_SRC)
    ml.add_theorem("t", "v2", _NONTRIVIAL_SRC)
    assert ml.get("t").statement == "v2"
    assert len(ml.load()) == 1


# --- validation discipline -------------------------------------------------

def test_failed_compile_not_contributed_by_default(store, monkeypatch):
    monkeypatch.setattr(ml.lean_verify, "verify_lean_source",
                        lambda *a, **k: _fake_verdict(compiled=False))
    e = ml.add_theorem("bad", "false", "theorem bad : False := by sorry")
    assert e.status == TheoremStatus.REFUTED
    assert ml.get("bad") is None  # require_verified default → not stored


def test_sorry_compiles_but_is_not_verified(store, monkeypatch):
    src = "theorem t : True := by sorry"
    monkeypatch.setattr(ml.lean_verify, "verify_lean_source",
                        lambda *a, **k: _fake_verdict(compiled=True))
    e = ml.add_theorem("t", "x", src, require_verified=False)
    # Compiles (Lean warns, not errors) but sorry present → not verified.
    assert e.compiled is True
    assert e.sorry_free is False
    assert e.status == TheoremStatus.UNVERIFIED


def test_tool_missing_stays_unverified(store, monkeypatch):
    monkeypatch.setattr(
        ml.lean_verify, "verify_lean_source",
        lambda *a, **k: _fake_verdict(compiled=False, tool_missing=True))
    e = ml.add_theorem("t", "x", "theorem t : True := trivial",
                       require_verified=False)
    assert e.status == TheoremStatus.UNVERIFIED


def test_has_sorry_detection():
    assert ml.has_sorry("theorem t : True := by sorry")
    assert ml.has_sorry("theorem t : True := by admit")
    assert not ml.has_sorry("theorem t : True := trivial")


def test_parse_tactics_generic():
    src = "theorem t (a b : Real) := by nlinarith [sq_nonneg (a-b)]; ring"
    tacs = ml.parse_tactics(src)
    assert "nlinarith" in tacs and "ring" in tacs


# --- knowledge graph + curriculum -----------------------------------------

def _seed_chain(monkeypatch):
    monkeypatch.setattr(ml.lean_verify, "verify_lean_source",
                        lambda *a, **k: _fake_verdict())
    ml.add_theorem("base", "b", _NONTRIVIAL_SRC,
                   curriculum_level=CL.ELEMENTARY)
    ml.add_theorem("mid", "m", _NONTRIVIAL_SRC,
                   prerequisites=["base"], curriculum_level=CL.UNDERGRAD)
    ml.add_theorem("top", "t", _NONTRIVIAL_SRC,
                   prerequisites=["mid"], curriculum_level=CL.GRADUATE)


def test_build_graph_edges(store, monkeypatch):
    _seed_chain(monkeypatch)
    g = ml.build_graph()
    assert len(g.nodes) == 3
    assert len(g.edges) == 2
    assert g.edges_from("theorem:top")[0].dst == "theorem:mid"


def test_prerequisites_closure(store, monkeypatch):
    _seed_chain(monkeypatch)
    assert ml.prerequisites_closure("top") == ["base", "mid"]


def test_curriculum_monotonic_clean(store, monkeypatch):
    _seed_chain(monkeypatch)
    assert ml.check_curriculum_monotonic() == []


def test_curriculum_monotonic_violation(store, monkeypatch):
    monkeypatch.setattr(ml.lean_verify, "verify_lean_source",
                        lambda *a, **k: _fake_verdict())
    ml.add_theorem("hard", "h", _NONTRIVIAL_SRC,
                   curriculum_level=CL.GRADUATE)
    ml.add_theorem("easy", "e", _NONTRIVIAL_SRC,
                   prerequisites=["hard"], curriculum_level=CL.ELEMENTARY)
    v = ml.check_curriculum_monotonic()
    assert any("higher-band" in line for line in v)


def test_missing_prereq_flagged(store, monkeypatch):
    monkeypatch.setattr(ml.lean_verify, "verify_lean_source",
                        lambda *a, **k: _fake_verdict())
    ml.add_theorem("t", "x", _NONTRIVIAL_SRC,
                   prerequisites=["nonexistent"])
    v = ml.check_curriculum_monotonic()
    assert any("missing" in line for line in v)


# --- virtual mathematician retrieval --------------------------------------

def test_retrieve_prefers_hint_closure(store, monkeypatch):
    _seed_chain(monkeypatch)
    vm = ml.VirtualMathematician()
    got = vm.retrieve("an unrelated statement", hint_prereqs=["top"])
    names = [e.name for e in got]
    # hint closure of top = base, mid, top — all included.
    assert set(names) >= {"base", "mid", "top"}


def test_retrieve_ignores_low_overlap(store, monkeypatch):
    monkeypatch.setattr(ml.lean_verify, "verify_lean_source",
                        lambda *a, **k: _fake_verdict())
    ml.add_theorem("cauchy_schwarz", "Cauchy Schwarz inner product bound",
                   _NONTRIVIAL_SRC)
    ml.add_theorem("irrational_root", "sqrt two irrational",
                   _NONTRIVIAL_SRC)
    vm = ml.VirtualMathematician()
    # statement shares only stopwords with irrational_root → excluded
    got = vm.retrieve("the Cauchy Schwarz inner product bound for vectors")
    names = [e.name for e in got]
    assert "cauchy_schwarz" in names
    assert "irrational_root" not in names


# --- virtual mathematician loop (mocked compile) --------------------------

def test_vm_prove_and_contribute_mocked(store, monkeypatch):
    monkeypatch.setattr(
        ml.lean_verify, "verify_lean_source",
        lambda *a, **k: _fake_verdict(theorems=["new_thm"]))
    vm = ml.VirtualMathematician(max_iters=2)
    res = vm.prove("new_thm", "(a : Nat) : a = a",
                   curriculum_level=CL.ELEMENTARY)
    assert res.proved and res.contributed
    assert ml.get("new_thm") is not None
    assert ml.get("new_thm").status == TheoremStatus.VERIFIED


def test_vm_repair_loop_then_fail(store, monkeypatch):
    calls = {"n": 0}

    def fail(*a, **k):
        calls["n"] += 1
        return _fake_verdict(compiled=False)

    monkeypatch.setattr(ml.lean_verify, "verify_lean_source", fail)
    vm = ml.VirtualMathematician(max_iters=3)
    res = vm.prove("nope", "(a : Nat) : a = a + 1")
    assert not res.proved
    assert calls["n"] == 3  # exhausted the repair budget
    assert len(res.attempts) == 3


def test_vm_tool_missing_aborts(store, monkeypatch):
    monkeypatch.setattr(
        ml.lean_verify, "verify_lean_source",
        lambda *a, **k: _fake_verdict(compiled=False, tool_missing=True))
    vm = ml.VirtualMathematician()
    res = vm.prove("t", "(a : Nat) : a = a")
    assert not res.proved
    assert "toolchain" in res.note


# --- Seeded-library regression (reads the tracked store; no Lean needed) ---

def _tracked_store_root() -> pathlib.Path:
    """The repo's tracked ``lab_theorems/store`` (two levels above the omicairn
    package: .../engine/omicairn → repo → lab_theorems/store)."""
    return (pathlib.Path(ml.__file__).resolve().parent.parent.parent
            / "lab_theorems" / "store")


def test_seeded_library_is_substantial_and_clean():
    """REGRESSION: the tracked seeded library holds the expanded batch
    (>= 50 verified theorems) and passes the curriculum-monotonicity invariant.
    Reads the committed JSONL op-log directly — no Lean toolchain required, so
    this guards the shipped store on every CI run."""
    root = _tracked_store_root()
    if not (root / "theorems.jsonl").exists():
        pytest.skip("tracked lab_theorems store not present")
    entries = ml.load(root=root)
    verified = [e for e in entries.values()
                if e.status == TheoremStatus.VERIFIED]
    # The owner asked for substantially more than the original 14; after the
    # 2026-06-20 CMU-SCS-grad expansion the floor was 85, after the
    # 2026-06-22 toward-1000 parallel expansion (88 -> 244) it was 244, after
    # batch 3 (244 -> 422) it was 422, after batch 4 (422 -> 648) it was 648,
    # after batch 5 (648 -> 847) it was 847, after batch 6 (847 -> 1052,
    # 1000-theorem milestone) it was 1052, after batch 7 (1052 -> 1136,
    # the 2026-06-23 modern-DL-architecture expansion) it was 1136, and after
    # batch 8 (1136 -> 1183, the 2026-06-23 ML-theory-gaps expansion:
    # PAC-Bayes / Johnson-Lindenstrauss / optimal-transport / online-convex-
    # optimization / VC-dimension) the floor is 1183.
    assert len(verified) >= 1183, f"only {len(verified)} verified theorems"
    # Every verified entry must record a real sorry-free compile + a citation.
    for e in verified:
        assert e.compiled and e.sorry_free, f"{e.name} not sorry-free in store"
        assert e.citation, f"{e.name} has no source citation"
    # Curriculum/dependency invariant must be clean on the shipped store.
    assert ml.check_curriculum_monotonic(root=root) == []


def test_toward_1000_batch_is_axiom_clean():
    """REGRESSION (owner's HARD BAR): the 2026-06-22 toward-1000 batch is not
    merely sorry-free but AXIOM-CLEAN — every theorem's `#print axioms` (run by
    the integrator with a real `lake env lean`, recorded in the tracked
    `store/axiom_check_batch2.json` artifact) depends ONLY on the standard
    axioms {propext, Classical.choice, Quot.sound}: no `sorryAx`, no custom
    `axiom`. Toolchain-free (reads the committed artifact), so it guards the
    shipped axiom-level guarantee on every CI run. A theorem that does not
    print clean does NOT count toward the library."""
    root = _tracked_store_root()
    artifact = root / "axiom_check_batch2.json"
    if not artifact.exists():
        pytest.skip("axiom_check_batch2 artifact not present")
    data = json.loads(artifact.read_text())
    allowed = set(data["allowed_axioms"])
    assert allowed == {"propext", "Classical.choice", "Quot.sound"}
    thms = data["theorems"]
    assert len(thms) >= 156, f"only {len(thms)} batch-2 theorems recorded"
    assert data["all_clean"] is True
    # Re-derive cleanliness from the recorded axiom lists (do not trust the
    # cached boolean alone): every recorded theorem must have an axiom list
    # that is a subset of the standard axioms with no sorryAx.
    for name, rec in thms.items():
        axs = rec["axioms"]
        assert axs is not None, f"{name} has no recorded #print axioms line"
        assert "sorryAx" not in axs, f"{name} depends on sorryAx"
        assert set(axs) <= allowed, f"{name} depends on non-standard axioms: {axs}"
    # Every axiom-clean batch-2 theorem must also be a verified store entry.
    entries = ml.load(root=root)
    for name in thms:
        assert name in entries, f"axiom-clean {name} missing from store"
        assert entries[name].status == TheoremStatus.VERIFIED


def test_toward_1000_batch3_is_axiom_clean():
    """REGRESSION (owner's HARD BAR): the 2026-06-22 batch-3 expansion
    (244 -> 422) is AXIOM-CLEAN — every theorem's `#print axioms` (re-run by
    the integrator with a real `lake env lean`, recorded in the tracked
    `store/axiom_check_batch3.json` artifact) depends ONLY on the standard
    axioms {propext, Classical.choice, Quot.sound}: no `sorryAx`, no custom
    `axiom`. Toolchain-free (reads the committed artifact). A theorem that does
    not print clean does NOT count toward the library — one authored theorem
    (`interlacing_2x2_diag`, a hyp==goal restate) was dropped at integration
    for failing the substance gate, so it must NOT appear here."""
    root = _tracked_store_root()
    artifact = root / "axiom_check_batch3.json"
    if not artifact.exists():
        pytest.skip("axiom_check_batch3 artifact not present")
    data = json.loads(artifact.read_text())
    allowed = set(data["allowed_axioms"])
    assert allowed == {"propext", "Classical.choice", "Quot.sound"}
    thms = data["theorems"]
    assert len(thms) >= 178, f"only {len(thms)} batch-3 theorems recorded"
    assert data["all_clean"] is True
    assert "interlacing_2x2_diag" not in thms, "dropped trivia must not be seeded"
    for name, rec in thms.items():
        axs = rec["axioms"]
        assert axs is not None, f"{name} has no recorded #print axioms line"
        assert "sorryAx" not in axs, f"{name} depends on sorryAx"
        assert set(axs) <= allowed, f"{name} depends on non-standard axioms: {axs}"
    entries = ml.load(root=root)
    for name in thms:
        assert name in entries, f"axiom-clean {name} missing from store"
        assert entries[name].status == TheoremStatus.VERIFIED


def test_toward_1000_batch4_is_axiom_clean():
    """REGRESSION (owner's HARD BAR): the 2026-06-22 batch-4 expansion
    (422 -> 648) is AXIOM-CLEAN — every theorem's `#print axioms` (re-run by
    the integrator with a real `lake env lean`, recorded in the tracked
    `store/axiom_check_batch4.json` artifact) depends ONLY on the standard
    axioms {propext, Classical.choice, Quot.sound}: no `sorryAx`, no custom
    `axiom`, no `admit`, no vacuous `decide`. Toolchain-free (reads the
    committed artifact). A theorem that does not print clean does NOT count
    toward the library — one authored theorem (`data_processing_mi_b4`, a literal
    `(h : Ixz ≤ Ixy) : Ixz ≤ Ixy := h` hyp==goal restate) was dropped at
    integration for failing the substance gate, so it must NOT appear here."""
    root = _tracked_store_root()
    artifact = root / "axiom_check_batch4.json"
    if not artifact.exists():
        pytest.skip("axiom_check_batch4 artifact not present")
    data = json.loads(artifact.read_text())
    allowed = set(data["allowed_axioms"])
    assert allowed == {"propext", "Classical.choice", "Quot.sound"}
    thms = data["theorems"]
    assert len(thms) >= 226, f"only {len(thms)} batch-4 theorems recorded"
    assert data["all_clean"] is True
    assert "data_processing_mi_b4" not in thms, "dropped trivia must not be seeded"
    for name, rec in thms.items():
        axs = rec["axioms"]
        assert axs is not None, f"{name} has no recorded #print axioms line"
        assert "sorryAx" not in axs, f"{name} depends on sorryAx"
        assert set(axs) <= allowed, f"{name} depends on non-standard axioms: {axs}"
    entries = ml.load(root=root)
    for name in thms:
        assert name in entries, f"axiom-clean {name} missing from store"
        assert entries[name].status == TheoremStatus.VERIFIED


def test_toward_1000_batch5_is_axiom_clean():
    """REGRESSION (owner's HARD BAR): the 2026-06-22 batch-5 expansion
    (648 -> 847) is AXIOM-CLEAN — every theorem's `#print axioms` (re-run by
    the integrator with a real `lake env lean` against the local Mathlib,
    recorded in the tracked `store/axiom_check_batch5.json` artifact) depends
    ONLY on the standard axioms {propext, Classical.choice, Quot.sound}: no
    `sorryAx`, no custom `axiom`, no `admit`, no vacuous `decide`. Toolchain-free
    (reads the committed artifact), so it guards the shipped axiom-level
    guarantee on every CI run. All 199 authored batch-5 theorems re-verified
    clean at integration (zero drops). A theorem that does not print clean does
    NOT count toward the library."""
    root = _tracked_store_root()
    artifact = root / "axiom_check_batch5.json"
    if not artifact.exists():
        pytest.skip("axiom_check_batch5 artifact not present")
    data = json.loads(artifact.read_text())
    allowed = set(data["allowed_axioms"])
    assert allowed == {"propext", "Classical.choice", "Quot.sound"}
    thms = data["theorems"]
    assert len(thms) >= 199, f"only {len(thms)} batch-5 theorems recorded"
    assert data["all_clean"] is True
    # Re-derive cleanliness from the recorded axiom lists (do not trust the
    # cached boolean alone): every recorded theorem must have an axiom list
    # that is a subset of the standard axioms with no sorryAx.
    for name, rec in thms.items():
        axs = rec["axioms"]
        assert axs is not None, f"{name} has no recorded #print axioms line"
        assert "sorryAx" not in axs, f"{name} depends on sorryAx"
        assert set(axs) <= allowed, f"{name} depends on non-standard axioms: {axs}"
    # Every axiom-clean batch-5 theorem must also be a verified store entry.
    entries = ml.load(root=root)
    for name in thms:
        assert name in entries, f"axiom-clean {name} missing from store"
        assert entries[name].status == TheoremStatus.VERIFIED


def test_toward_1000_batch6_is_axiom_clean():
    """REGRESSION (owner's HARD BAR): the 2026-06-22 batch-6 close-out
    (847 -> 1052, the 1000-theorem milestone REACHED) is AXIOM-CLEAN — every
    theorem's `#print axioms` (re-run by the integrator with a real
    `lake env lean` against the local Mathlib, recorded in the tracked
    `store/axiom_check_batch6.json` artifact) depends ONLY on the standard
    axioms {propext, Classical.choice, Quot.sound}: no `sorryAx`, no custom
    `axiom`, no `admit`, no vacuous `decide`. Toolchain-free (reads the
    committed artifact), so it guards the shipped axiom-level guarantee on
    every CI run. All 205 authored batch-6 theorems re-verified clean at
    integration (zero drops). A theorem that does not print clean does NOT
    count toward the library."""
    root = _tracked_store_root()
    artifact = root / "axiom_check_batch6.json"
    if not artifact.exists():
        pytest.skip("axiom_check_batch6 artifact not present")
    data = json.loads(artifact.read_text())
    allowed = set(data["allowed_axioms"])
    assert allowed == {"propext", "Classical.choice", "Quot.sound"}
    thms = data["theorems"]
    assert len(thms) >= 205, f"only {len(thms)} batch-6 theorems recorded"
    assert data["all_clean"] is True
    # Re-derive cleanliness from the recorded axiom lists (do not trust the
    # cached boolean alone): every recorded theorem must have an axiom list
    # that is a subset of the standard axioms with no sorryAx.
    for name, rec in thms.items():
        axs = rec["axioms"]
        assert axs is not None, f"{name} has no recorded #print axioms line"
        assert "sorryAx" not in axs, f"{name} depends on sorryAx"
        assert set(axs) <= allowed, f"{name} depends on non-standard axioms: {axs}"
    # Every axiom-clean batch-6 theorem must also be a verified store entry.
    entries = ml.load(root=root)
    for name in thms:
        assert name in entries, f"axiom-clean {name} missing from store"
        assert entries[name].status == TheoremStatus.VERIFIED
    # The close-out crosses the owner's 1000-theorem target on disk.
    assert len(entries) >= 1000, f"library has only {len(entries)} theorems"


def test_batch7_is_axiom_clean():
    """REGRESSION (owner's HARD BAR): the 2026-06-23 batch-7 modern-DL-
    architecture expansion (1052 -> 1136) is AXIOM-CLEAN — every theorem's
    `#print axioms` (re-run by the integrator with the canonical
    `lab_theorems/axiom_check.py` against the local Mathlib, recorded in the
    tracked `store/axiom_check_batch7.json` artifact) depends ONLY on the
    standard axioms {propext, Classical.choice, Quot.sound}: no `sorryAx`, no
    custom `axiom`, no `admit`, no vacuous `decide`. Toolchain-free (reads the
    committed artifact), so it guards the shipped axiom-level guarantee on every
    CI run. All 84 authored batch-7 theorems re-verified clean at integration
    (zero drops). Batch 7 closed the previously-empty softmax/attention,
    activation/normalization, generative/representation, calibration/conformal,
    divergence/sketching, and adaptive-optimizer families. A theorem that does
    not print clean does NOT count toward the library."""
    root = _tracked_store_root()
    artifact = root / "axiom_check_batch7.json"
    if not artifact.exists():
        pytest.skip("axiom_check_batch7 artifact not present")
    data = json.loads(artifact.read_text())
    allowed = set(data["allowed_axioms"])
    assert allowed == {"propext", "Classical.choice", "Quot.sound"}
    thms = data["theorems"]
    assert len(thms) >= 84, f"only {len(thms)} batch-7 theorems recorded"
    assert data["all_clean"] is True
    # Re-derive cleanliness from the recorded axiom lists (do not trust the
    # cached boolean alone): every recorded theorem must have an axiom list
    # that is a subset of the standard axioms with no sorryAx.
    for name, rec in thms.items():
        axs = rec["axioms"]
        assert axs is not None, f"{name} has no recorded #print axioms line"
        assert "sorryAx" not in axs, f"{name} depends on sorryAx"
        assert set(axs) <= allowed, f"{name} depends on non-standard axioms: {axs}"
    # Every axiom-clean batch-7 theorem must also be a verified store entry.
    entries = ml.load(root=root)
    for name in thms:
        assert name in entries, f"axiom-clean {name} missing from store"
        assert entries[name].status == TheoremStatus.VERIFIED


def test_batch8_is_axiom_clean():
    """REGRESSION (owner's HARD BAR): the 2026-06-23 batch-8 ML-theory-gaps
    expansion (1136 -> 1183) is AXIOM-CLEAN — every theorem's `#print axioms`
    (re-run by the integrator with the canonical `lab_theorems/axiom_check.py`
    against the local Mathlib, recorded in the tracked
    `store/axiom_check_batch8.json` artifact) depends ONLY on the standard
    axioms {propext, Classical.choice, Quot.sound}: no `sorryAx`, no custom
    `axiom`, no `admit`, no vacuous `decide`. Toolchain-free (reads the
    committed artifact), so it guards the shipped axiom-level guarantee on every
    CI run. All 47 authored batch-8 theorems re-verified clean at integration
    (zero drops). Batch 8 closed the previously-empty PAC-Bayes, Johnson-
    Lindenstrauss / random-projection, optimal-transport / Wasserstein, online-
    convex-optimization-regret, and VC-dimension families. A theorem that does
    not print clean does NOT count toward the library."""
    root = _tracked_store_root()
    artifact = root / "axiom_check_batch8.json"
    if not artifact.exists():
        pytest.skip("axiom_check_batch8 artifact not present")
    data = json.loads(artifact.read_text())
    allowed = set(data["allowed_axioms"])
    assert allowed == {"propext", "Classical.choice", "Quot.sound"}
    thms = data["theorems"]
    assert len(thms) >= 47, f"only {len(thms)} batch-8 theorems recorded"
    assert data["all_clean"] is True
    # Re-derive cleanliness from the recorded axiom lists (do not trust the
    # cached boolean alone): every recorded theorem must have an axiom list
    # that is a subset of the standard axioms with no sorryAx.
    for name, rec in thms.items():
        axs = rec["axioms"]
        assert axs is not None, f"{name} has no recorded #print axioms line"
        assert "sorryAx" not in axs, f"{name} depends on sorryAx"
        assert set(axs) <= allowed, f"{name} depends on non-standard axioms: {axs}"
    # Every axiom-clean batch-8 theorem must also be a verified store entry.
    entries = ml.load(root=root)
    for name in thms:
        assert name in entries, f"axiom-clean {name} missing from store"
        assert entries[name].status == TheoremStatus.VERIFIED


def test_seeded_library_spans_curriculum():
    """REGRESSION: the seeded batch spans the curriculum (elementary→graduate)
    and multiple domains, not just the original inequality family."""
    root = _tracked_store_root()
    if not (root / "theorems.jsonl").exists():
        pytest.skip("tracked lab_theorems store not present")
    entries = ml.load(root=root)
    levels = {e.curriculum_level for e in entries.values()}
    assert {CL.UNDERGRAD, CL.GRADUATE} <= levels
    names = set(entries)
    # spot-check representatives from the new linear-algebra / probability /
    # number-theory / real-analysis batches actually landed.
    for expected in ("rank_nullity", "fermat_little", "ivt", "jensen_finite",
                     "chebyshev_core",
                     # 2026-06-20 CMU-SCS-grad expansion representatives:
                     "gd_quadratic_descent", "hoeffding_lemma_symmetric",
                     "rayleigh_quotient_bound_2x2", "fenchel_young_inequality",
                     "eckart_young_truncation",
                     # 2026-06-22 toward-1000 parallel expansion representatives
                     # (one per domain file):
                     "occam_sample_complexity", "convex_sup",
                     "kl_divergence_nonneg", "gram_posSemidef",
                     "chebyshev_variance", "halls_marriage",
                     "cantor_no_surjection", "master_leaf_count",
                     # 2026-06-22 batch-3 expansion representatives (one per
                     # domain file): coding/crypto, RL/Bellman, DP/causal,
                     # online/bandits/boosting, deferred-discharge,
                     # spectral-graph/matrix, SGD/mirror-descent/RKHS:
                     "hamming_dist_triangle", "bellman_backup_sup_contraction",
                     "dp_sequential_composition_exp", "adaboost_product_error_bound",
                     "borel_cantelli_limsup_null", "laplacian_posSemidef",
                     "mirror_descent_regret_balance",
                     # 2026-06-22 batch-4 expansion representatives (one per
                     # domain file): deferred-discharge, learning theory,
                     # optimization, info/probability, spectral-graph,
                     # numerical-linalg, estimation/control, randomized/complexity:
                     "courant_fischer_sup_eigenvalue_b4", "talagrand_contraction_sum_b4",
                     "nesterov_estimate_seq_potential_b4", "optional_stopping_submartingale_b4",
                     "matrix_tree_K3_count_b4", "gram_schmidt_orthonormal_b4",
                     "cramer_rao_kernel_b4", "submodular_greedy_one_minus_inv_e_b4",
                     # 2026-06-23 batch-7 modern-DL-architecture expansion
                     # representatives (one per domain file): softmax/attention,
                     # activation/norm, generative/repr, calibration/conformal,
                     # divergence/sketch, adaptive optimizers:
                     "softmax_sums_to_one_b7", "relu_lipschitz_b7",
                     "elbo_jensen_log_midpoint_b7", "conformal_coverage_kernel_b7",
                     "hellinger_term_le_b7", "adam_ema_constant_grad_b7"):
        assert expected in names, f"{expected} missing from seeded store"


# --- REAL toolchain end-to-end (gated) ------------------------------------

@pytest.mark.skipif(not lean_verify.lean_toolchain_available(),
                    reason="lean toolchain not installed")
def test_real_compile_trivial_sorry_free(store):
    """A genuinely-compiling Mathlib-free proof becomes verified for real."""
    src = "theorem add_comm_two (a b : Nat) : a + b = b + a := Nat.add_comm a b"
    e = ml.add_theorem("add_comm_two", "Nat add is commutative", src,
                       curriculum_level=CL.ELEMENTARY)
    assert e.status == TheoremStatus.VERIFIED
    assert e.compiled and e.sorry_free
    assert "add_comm_two" in e.theorems_checked


@pytest.mark.skipif(not lean_verify.lean_toolchain_available(),
                    reason="lean toolchain not installed")
def test_real_compile_trivial_demoted(store):
    """A genuinely-compiling tautology (`by decide`) is REAL-compiled by the
    local toolchain, sorry-free, yet demoted to verified_trivial — the
    substance gate fires on a real compile, not just the mock."""
    src = "theorem real_taut : 1 = 1 := by decide"
    e = ml.add_theorem("real_taut", "one equals one", src,
                       curriculum_level=CL.ELEMENTARY, require_verified=False)
    assert e.compiled and e.sorry_free      # really compiled, sorry-free
    assert e.status == TheoremStatus.VERIFIED_TRIVIAL
    assert e.substance_warnings


def _mathlib_lake_project() -> pathlib.Path | None:
    """The reusable Mathlib Lake project, if present on this host (env override
    or the canonical checkout). Returns None when absent so the gated
    Mathlib-backed regression skips cleanly off-host."""
    import os
    from omicairn.constants import LEAN_MATHLIB_PROJECT_DIRNAME
    cands = []
    env = os.environ.get("OMICAIRN_MATHLIB_PROJECT", "").strip()
    if env:
        cands.append(pathlib.Path(env))
    cands.append(pathlib.Path("/path/to/OmiCairn")
                 / LEAN_MATHLIB_PROJECT_DIRNAME)
    for c in cands:
        if (c / "lakefile.toml").exists() or (c / "lakefile.lean").exists():
            return c.resolve()
    return None


@pytest.mark.skipif(not lean_verify.lean_toolchain_available()
                    or _mathlib_lake_project() is None,
                    reason="lean toolchain / Mathlib Lake project not available")
def test_real_compile_new_batch_theorem_substantive(store):
    """REGRESSION: a NEW seeded theorem (the ℝ³ Cauchy–Schwarz / Lagrange
    identity) really compiles sorry-free against local Mathlib AND is NOT
    flagged as trivia — proving the expanded batch is load-bearing, not
    table-fact proof-theater. (Gated; skips where Mathlib is absent.)"""
    proj = _mathlib_lake_project()
    wd = proj / "OmicairnProof" / "LabTheoremsTest"
    src = ("import Mathlib\n\n"
           "theorem cs3 (a b c x y z : ℝ) :\n"
           "    (a * x + b * y + c * z) ^ 2 ≤ "
           "(a ^ 2 + b ^ 2 + c ^ 2) * (x ^ 2 + y ^ 2 + z ^ 2) := by\n"
           "  nlinarith [sq_nonneg (a * y - b * x), sq_nonneg (a * z - c * x),\n"
           "             sq_nonneg (b * z - c * y)]\n")
    e = ml.add_theorem("cs3", "Cauchy-Schwarz in R^3", src,
                       curriculum_level=CL.UNDERGRAD, workdir=wd,
                       citation="test")
    assert e.compiled and e.sorry_free
    assert e.status == TheoremStatus.VERIFIED   # substantive, not _trivial
    assert "nlinarith" in e.tactics


@pytest.mark.skipif(not lean_verify.lean_toolchain_available()
                    or _mathlib_lake_project() is None,
                    reason="lean toolchain / Mathlib Lake project not available")
def test_real_compile_convex_opt_grad_marquee(store):
    """REGRESSION (CMU-SCS-grad batch): the 10-725 gradient-descent descent
    lemma (L-smooth quadratic instance) and the 36-705 Hoeffding mgf kernel
    really compile sorry-free against local Mathlib and are NOT trivia — the
    two marquee additions of the 2026-06-20 expansion are load-bearing, not
    proof-theater. (Gated; skips where Mathlib is absent.)"""
    proj = _mathlib_lake_project()
    wd = proj / "OmicairnProof" / "LabTheoremsTest"
    # 10-725 GD descent lemma, quadratic instance — a derivation (nlinarith).
    gd_src = ("import Mathlib\n\n"
              "theorem gd_q (x : ℝ) : (x - (1/2) * (2*x)) ^ 2 ≤ x ^ 2 := by\n"
              "  nlinarith [sq_nonneg x]\n")
    e_gd = ml.add_theorem("gd_q", "GD descent lemma (quadratic)", gd_src,
                          curriculum_level=CL.GRADUATE, workdir=wd,
                          citation="CMU 10-725; Nesterov §1.2.3")
    assert e_gd.compiled and e_gd.sorry_free
    assert e_gd.status == TheoremStatus.VERIFIED   # substantive, not _trivial
    # 36-705 Hoeffding mgf kernel — a named-Mathlib-lemma wrapper (legit, not
    # trivia: it cites Real.cosh_le_exp_half_sq).
    ho_src = ("import Mathlib\n\n"
              "theorem ho (t : ℝ) : Real.cosh t ≤ Real.exp (t ^ 2 / 2) :=\n"
              "  Real.cosh_le_exp_half_sq t\n")
    e_ho = ml.add_theorem("ho", "Hoeffding mgf kernel", ho_src,
                          curriculum_level=CL.GRADUATE, workdir=wd,
                          citation="CMU 36-705; Wainwright §2.1.2")
    assert e_ho.compiled and e_ho.sorry_free
    assert e_ho.status == TheoremStatus.VERIFIED   # named-lemma wrapper, not trivia


# --- Fix 1: verdict suffix comes from the canonical constant ---------------

def test_verdict_path_uses_constant(store):
    """The verdict.json suffix is the canonical constants.LEAN_VERDICT_SUFFIX,
    not a bare string literal (the dead-hasattr-guard regression)."""
    from omicairn.constants import LEAN_VERDICT_SUFFIX
    e = ml.TheoremEntry(name="thm_x", statement="s")
    vp = ml._verdict_path(e, root=store)
    assert vp.name == "thm_x" + LEAN_VERDICT_SUFFIX
    # And the function does NOT depend on a (never-present) attr on lean_verify.
    assert not hasattr(ml.lean_verify, "LEAN_VERDICT_SUFFIX")


# --- Fix 2: substance gate — trivia demoted, real short proof accepted -----

def test_trivial_proof_demoted_not_first_class(store, monkeypatch):
    """A content-free tautology proof that COMPILES sorry-free is recorded as
    `verified_trivial` (flagged), NOT accepted as a first-class theorem."""
    monkeypatch.setattr(ml.lean_verify, "verify_lean_source",
                        lambda *a, **k: _fake_verdict(theorems=["t"]))
    src = "theorem t : True := by trivial"
    e = ml.add_theorem("t", "a tautology", src, require_verified=False)
    assert e.compiled and e.sorry_free
    assert e.status == TheoremStatus.VERIFIED_TRIVIAL
    assert e.substance_warnings  # carries the library-trivia warning
    # Under the default require_verified, a trivial proof is NOT contributed.
    e2 = ml.add_theorem("t2", "a tautology", "theorem t2 : 1 = 1 := by decide")
    assert e2.status == TheoremStatus.VERIFIED_TRIVIAL
    assert ml.get("t2") is None  # not stored as first-class


def test_genuine_short_proof_still_accepted(store, monkeypatch):
    """A real one-line Mathlib-wrapper proof (named-lemma citation) is NOT
    over-rejected — it stays first-class VERIFIED (WARN-not-reject discipline)."""
    monkeypatch.setattr(ml.lean_verify, "verify_lean_source",
                        lambda *a, **k: _fake_verdict(theorems=["sqrt2"]))
    src = "theorem sqrt2 : Irrational (Real.sqrt 2) := irrational_sqrt_two"
    e = ml.add_theorem("sqrt2", "sqrt 2 is irrational", src)
    assert e.status == TheoremStatus.VERIFIED
    assert not any("LIBRARY-trivia" in w for w in e.substance_warnings)
    assert ml.get("sqrt2") is not None  # contributed as first-class


def test_vm_trivial_proof_not_contributed(store, monkeypatch):
    """The auto-contribution (virtual-mathematician) path also demotes a
    trivial proof: proved but flagged verified_trivial and NOT contributed."""
    monkeypatch.setattr(
        ml.lean_verify, "verify_lean_source",
        lambda *a, **k: _fake_verdict(theorems=["taut"]))
    vm = ml.VirtualMathematician(
        drafter=lambda *a, **k: "theorem taut : True := by trivial", max_iters=1)
    res = vm.prove("taut", "True", curriculum_level=CL.ELEMENTARY)
    assert res.proved and not res.contributed
    assert res.entry.status == TheoremStatus.VERIFIED_TRIVIAL
    assert ml.get("taut") is None  # never auto-contributed


# --- Fix 3: cycle detection + prereq-must-be-verified ----------------------

def test_depends_on_cycle_errors(store, monkeypatch):
    """A depends_on cycle is flagged as a violation, not silently looped."""
    monkeypatch.setattr(ml.lean_verify, "verify_lean_source",
                        lambda *a, **k: _fake_verdict())
    # a -> b -> a (same band so monotonicity itself is clean; the cycle is
    # the violation). require_verified=False to record despite the dangling
    # forward ref at insert time.
    ml.add_theorem("a", "x", _NONTRIVIAL_SRC, prerequisites=["b"],
                   require_verified=False)
    ml.add_theorem("b", "y", _NONTRIVIAL_SRC, prerequisites=["a"],
                   require_verified=False)
    v = ml.check_curriculum_monotonic()
    assert any("cycle" in line for line in v)


def test_unverified_prereq_flagged(store, monkeypatch):
    """A theorem resting on a prereq that is PRESENT but UNVERIFIED is a
    violation — a verified theorem may not depend on un-compiled ground."""
    monkeypatch.setattr(ml.lean_verify, "verify_lean_source",
                        lambda *a, **k: _fake_verdict())
    # Record an UNVERIFIED prereq (force it via require_verified=False on a
    # source the mock reports as compiled=False).
    monkeypatch.setattr(ml.lean_verify, "verify_lean_source",
                        lambda *a, **k: _fake_verdict(compiled=False))
    ml.add_theorem("pre", "shaky", _NONTRIVIAL_SRC, require_verified=False)
    assert ml.get("pre").status != TheoremStatus.VERIFIED
    # Now a verified dependent on it.
    monkeypatch.setattr(ml.lean_verify, "verify_lean_source",
                        lambda *a, **k: _fake_verdict())
    ml.add_theorem("dep", "rests on pre", _NONTRIVIAL_SRC,
                   prerequisites=["pre"])
    v = ml.check_curriculum_monotonic()
    assert any("UNVERIFIED" in line and "pre" in line for line in v)
