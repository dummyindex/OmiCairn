"""Tests for the math-paper-review subsystem (omicairn.paper_review).

Pure-Python store/graph/verdict/axiom-parse logic is tested without a Lean
toolchain by monkeypatching `lean_verify.verify_lean_source`. The axiom parser
is tested on fixtures. A single end-to-end test that needs the REAL toolchain
is gated on `lean_toolchain_available()` AND a discoverable Mathlib project
(skipped otherwise), mirroring test_lab_theorems / test_lean_verify.
"""
from __future__ import annotations

import pytest

from omicairn import paper_review as pr
from omicairn import lean_verify
from omicairn import lab_theorems
from omicairn.constants import ClaimVerdict, PaperEra, CurriculumLevel as CL


# --- helpers ---------------------------------------------------------------

def _verdict(compiled=True, theorems=None, stdout="", substance=None,
             tool_missing=False):
    return lean_verify.LeanVerdict(
        compiled=compiled, theorems_checked=theorems or [], stdout=stdout,
        substance_warnings=substance or [], tool_missing=tool_missing,
        lean_file="x.lean",
    )


# A real named-lemma wrapper (compiles, axiom-clean, NOT trivia) for verdict
# tests where the verify call is mocked.
_REAL_SRC = ("import Mathlib\n\ntheorem t (n : Nat) : ∃ p, n ≤ p ∧ Nat.Prime p "
             ":= Nat.exists_infinite_primes n\n")
_CLEAN_AXIOMS = "'t' depends on axioms: [propext, Classical.choice, Quot.sound]"


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """Temp store + a temp lean_dir + an isolated linked-theorems lab_root."""
    root = tmp_path / "store"
    root.mkdir()
    lean_d = tmp_path / "lean"
    lean_d.mkdir()
    monkeypatch.setattr(pr, "resolve_root", lambda explicit=None: root)
    monkeypatch.setattr(pr, "lean_dir", lambda root=None: lean_d)
    # Isolate lab_theorems writes to a temp dir so we never touch the shared store.
    lab_root = tmp_path / "linked"
    monkeypatch.setattr(lab_theorems, "resolve_root",
                        lambda explicit=None: lab_root)
    return root


# --- axiom parsing (PURE — no Lean) ----------------------------------------

def test_parse_axioms_clean_set():
    axs = pr.parse_axioms(_CLEAN_AXIOMS)
    assert axs == ["propext", "Classical.choice", "Quot.sound"]
    assert pr.axioms_clean(axs)


def test_parse_axioms_no_axioms_form():
    assert pr.parse_axioms("'t' does not depend on any axioms") == []
    assert pr.axioms_clean([])  # empty set is trivially clean


def test_parse_axioms_dirty_with_sorry():
    axs = pr.parse_axioms("'t' depends on axioms: [propext, sorryAx]")
    assert "sorryAx" in axs
    assert not pr.axioms_clean(axs)


# --- store round-trip ------------------------------------------------------

def test_paper_roundtrip(store):
    pr.add_paper("euclid", title="Elements", author="Euclid",
                 era=PaperEra.ANTIQUITY)
    p = pr.get_paper("euclid")
    assert p is not None and p.title == "Elements" and p.era == PaperEra.ANTIQUITY


def test_claim_roundtrip_and_last_write_wins(store):
    c = pr.PaperClaim(claim_id="euclid::primes", paper_id="euclid",
                      informal_statement="v1")
    pr.put_claim(c)
    c2 = pr.PaperClaim(claim_id="euclid::primes", paper_id="euclid",
                       informal_statement="v2")
    pr.put_claim(c2)
    loaded = pr.load_claims()
    assert len(loaded) == 1
    assert loaded["euclid::primes"].informal_statement == "v2"


# --- verdict logic (mocked Lean) -------------------------------------------

def test_verified_axiom_clean(store, monkeypatch):
    monkeypatch.setattr(pr.lean_verify, "verify_lean_source",
                        lambda *a, **k: _verdict(theorems=["t"], stdout=_CLEAN_AXIOMS))
    c = pr.PaperClaim(claim_id="p::c", paper_id="p", informal_statement="primes",
                      lean_proof=_REAL_SRC)
    c = pr.review_claim(c, link_theorem=False)
    assert c.verdict == ClaimVerdict.VERIFIED
    assert c.axiom_clean and c.axioms == ["propext", "Classical.choice", "Quot.sound"]


def test_not_axiom_clean_downgraded_to_stmt_only(store, monkeypatch):
    dirty = "'t' depends on axioms: [propext, sorryAx]"
    monkeypatch.setattr(pr.lean_verify, "verify_lean_source",
                        lambda *a, **k: _verdict(theorems=["t"], stdout=dirty))
    c = pr.PaperClaim(claim_id="p::c", paper_id="p", lean_statement="t : True",
                      lean_proof=_REAL_SRC)
    c = pr.review_claim(c, link_theorem=False)
    # Compiles, but a non-kernel axiom → NOT verified, only statement-formalized.
    assert c.verdict == ClaimVerdict.FORMALIZED_STMT_ONLY
    assert not c.axiom_clean


def test_failed_compile(store, monkeypatch):
    monkeypatch.setattr(pr.lean_verify, "verify_lean_source",
                        lambda *a, **k: _verdict(compiled=False))
    c = pr.PaperClaim(claim_id="p::c", paper_id="p", lean_proof="theorem t : False := by ring")
    c = pr.review_claim(c, link_theorem=False)
    assert c.verdict == ClaimVerdict.FAILED


def test_trivia_proof_downgraded(store, monkeypatch):
    # A content-free tautology closer compiles but trips the substance gate →
    # statement-only, never verified.
    monkeypatch.setattr(pr.lean_verify, "verify_lean_source",
                        lambda *a, **k: _verdict(theorems=["t"], stdout=_CLEAN_AXIOMS))
    c = pr.PaperClaim(claim_id="p::c", paper_id="p",
                      lean_proof="theorem t : True := by trivial")
    c = pr.review_claim(c, link_theorem=False)
    assert c.verdict == ClaimVerdict.FORMALIZED_STMT_ONLY
    assert c.substance_warnings


def test_deferred_untouched(store):
    c = pr.PaperClaim(claim_id="p::hard", paper_id="p",
                      verdict=ClaimVerdict.DEFERRED, defer_reason="too hard")
    out = pr.review_claim(c, link_theorem=False)
    assert out.verdict == ClaimVerdict.DEFERRED
    assert out.defer_reason == "too hard"


def test_tool_missing_not_fabricated(store, monkeypatch):
    monkeypatch.setattr(pr.lean_verify, "verify_lean_source",
                        lambda *a, **k: _verdict(compiled=False, tool_missing=True))
    c = pr.PaperClaim(claim_id="p::c", paper_id="p", lean_statement="t : True",
                      lean_proof=_REAL_SRC)
    c = pr.review_claim(c, link_theorem=False)
    assert c.verdict != ClaimVerdict.VERIFIED  # never fabricate verified


# --- Reviewer loop + persistence + linking ---------------------------------

def test_reviewer_persists_and_links(store, monkeypatch):
    monkeypatch.setattr(pr.lean_verify, "verify_lean_source",
                        lambda *a, **k: _verdict(theorems=["t"], stdout=_CLEAN_AXIOMS))
    # lab_theorems.add_theorem also calls lean_verify.verify_lean_source; mock
    # it through that module's reference too.
    monkeypatch.setattr(lab_theorems.lean_verify, "verify_lean_source",
                        lambda *a, **k: _verdict(theorems=["t"], stdout=_CLEAN_AXIOMS))
    pr.add_paper("p", title="P")
    claims = [pr.PaperClaim(claim_id="p::c", paper_id="p",
                            informal_statement="primes", lean_proof=_REAL_SRC)]
    res = pr.Reviewer(link_theorems=True).review_paper("p", claims=claims)
    assert res.verified == 1
    stored = pr.get_claim("p::c")
    assert stored is not None and stored.verdict == ClaimVerdict.VERIFIED
    assert stored.linked_theorem == "t"
    # the paper now records its claim
    assert "p::c" in pr.get_paper("p").claim_ids
    # and the linked theorem landed in the (isolated) lab_theorems store
    assert lab_theorems.get("t") is not None


# --- graph + stats ---------------------------------------------------------

def test_graph_edges(store, monkeypatch):
    monkeypatch.setattr(pr.lean_verify, "verify_lean_source",
                        lambda *a, **k: _verdict(theorems=["t"], stdout=_CLEAN_AXIOMS))
    pr.add_paper("p", title="P")
    pr.Reviewer(link_theorems=False).review_paper("p", claims=[
        pr.PaperClaim(claim_id="p::c", paper_id="p", lean_proof=_REAL_SRC)])
    # manually link for the edge test
    c = pr.get_claim("p::c"); c.linked_theorem = "t"; pr.put_claim(c)
    g = pr.build_graph()
    assert any(e.rel == "presents" for e in g.edges.values())
    assert any(e.rel == "formalized_as" for e in g.edges.values())


def test_stats(store, monkeypatch):
    monkeypatch.setattr(pr.lean_verify, "verify_lean_source",
                        lambda *a, **k: _verdict(theorems=["t"], stdout=_CLEAN_AXIOMS))
    pr.add_paper("p", era=PaperEra.ANTIQUITY)
    pr.Reviewer(link_theorems=False).review_paper("p", claims=[
        pr.PaperClaim(claim_id="p::c", paper_id="p", lean_proof=_REAL_SRC)])
    s = pr.stats()
    assert s["papers"] == 1 and s["verified"] == 1
    assert s["by_era"][PaperEra.ANTIQUITY] == 1


# --- gated real-Lean e2e ----------------------------------------------------

@pytest.mark.skipif(not lean_verify.lean_toolchain_available(),
                    reason="Lean toolchain not installed")
def test_real_lean_axiom_clean_e2e(store):
    """End-to-end with the REAL toolchain: a Mathlib-free axiom-clean lemma must
    verify AND parse a clean axiom set. Skips if no Mathlib project is found
    (Mathlib-free source needs none)."""
    src = "theorem pr_e2e (a b : Nat) : a + b = b + a := Nat.add_comm a b\n"
    c = pr.PaperClaim(claim_id="e2e::add_comm", paper_id="e2e", lean_proof=src,
                      informal_statement="addition commutes",
                      curriculum_level=CL.ELEMENTARY)
    # No workdir → Mathlib-free temp compile; this lemma needs no Mathlib.
    c = pr.review_claim(c, workdir=None, link_theorem=False)
    if c.lean_verdict.get("tool_missing"):
        pytest.skip("toolchain reported missing at run time")
    assert c.verdict == ClaimVerdict.VERIFIED, c.lean_verdict.get("errors")
    assert c.axiom_clean
