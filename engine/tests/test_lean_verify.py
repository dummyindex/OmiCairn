"""Tests for omicairn.lean_verify — the Lean 4 proof checker.

Two layers, per the build brief:

1. **Parser / verdict LOGIC (always runs).** `parse_lean_output` is pure —
   it decides compiled?/errors/theorem-names from raw Lean output. We feed
   it CAPTURED Lean output fixtures (a passing proof, a broken proof, a
   `sorry` proof) and assert the verdict. This tests the mechanism's logic
   REGARDLESS of whether Lean is installed.

2. **Real-Lean end-to-end (gated on availability).** When a Lean toolchain
   is present, we feed the verifier a TRUE lemma (`2 + 2 = 4 := by rfl`)
   and assert it verifies, and a FALSE/broken proof and assert it is
   reported as failing. Skipped with a clear reason when Lean is absent.

The fixtures below are REAL `lean` v4.30.0 output captured during the
build (Mathlib-free), so the parser is tested against authentic strings.
"""
from __future__ import annotations

import pathlib

import pytest

from omicairn import constants as CONST
from omicairn import lab_theorems, lean_verify
from omicairn.constants import LEAN_MATHLIB_PROJECT_DIRNAME


# ---------------------------------------------------------------------------
# Captured Lean output fixtures (real `lean` v4.30.0, Mathlib-free)
# ---------------------------------------------------------------------------

# A passing proof: rc 0, no diagnostics at all.
_FIXTURE_OK_RC = 0
_FIXTURE_OK_OUT = ""

# A broken proof (`theorem t_bad : 2 + 2 = 5 := by rfl`): rc 1, an
# `error:` diagnostic on the tactic.
_FIXTURE_BAD_RC = 1
_FIXTURE_BAD_OUT = (
    "/tmp/bad.lean:1:32: error: Tactic `rfl` failed: The left-hand side\n"
    "  2 + 2\n"
    "is not definitionally equal to the right-hand side\n"
    "  5\n\n"
    "⊢ 2 + 2 = 5\n"
)

# A `sorry` proof (`theorem t_sorry : 2 + 2 = 4 := by sorry`): rc 0, but a
# warning that the declaration uses `sorry`. An unproven hole MUST be
# treated as a failure — it is not an established result.
_FIXTURE_SORRY_RC = 0
_FIXTURE_SORRY_OUT = "/tmp/sorry.lean:1:8: warning: declaration uses `sorry`\n"


# ---------------------------------------------------------------------------
# Layer 1: pure parser / verdict logic (ALWAYS runs, no Lean needed)
# ---------------------------------------------------------------------------

def test_parse_passing_output_compiles():
    compiled, errors, theorems = lean_verify.parse_lean_output(
        returncode=_FIXTURE_OK_RC, stdout=_FIXTURE_OK_OUT, stderr="",
        source="theorem t_ok : 2 + 2 = 4 := by rfl",
    )
    assert compiled is True
    assert errors == []
    assert theorems == ["t_ok"]


def test_parse_broken_output_fails_with_error():
    compiled, errors, theorems = lean_verify.parse_lean_output(
        returncode=_FIXTURE_BAD_RC, stdout=_FIXTURE_BAD_OUT, stderr="",
        source="theorem t_bad : 2 + 2 = 5 := by rfl",
    )
    assert compiled is False
    assert errors, "a broken proof must surface at least one error"
    assert any("rfl" in e for e in errors)
    assert theorems == ["t_bad"]


def test_parse_sorry_output_fails_even_though_rc_zero():
    """A `sorry` proof exits 0 with only a warning, but an unproven hole is
    NOT an established result — the verdict must be `compiled: False`."""
    compiled, errors, _ = lean_verify.parse_lean_output(
        returncode=_FIXTURE_SORRY_RC, stdout=_FIXTURE_SORRY_OUT, stderr="",
        source="theorem t_sorry : 2 + 2 = 4 := by sorry",
    )
    assert compiled is False
    assert any("sorry" in e.lower() for e in errors)


def test_parse_theorem_names_handles_lemma_example_def():
    src = (
        "theorem a : True := trivial\n"
        "lemma b : True := trivial\n"
        "example : True := trivial\n"
        "private theorem c : True := trivial\n"
        "@[simp] theorem d : True := trivial\n"
    )
    names = lean_verify.parse_theorem_names(src)
    assert "a" in names and "b" in names and "c" in names and "d" in names


def test_nonzero_rc_without_error_line_still_fails():
    """If Lean exits non-zero we don't trust it even absent a parseable
    `error:` line (e.g. an internal failure)."""
    compiled, _, _ = lean_verify.parse_lean_output(
        returncode=2, stdout="some unexpected output", stderr="", source="",
    )
    assert compiled is False


# ---------------------------------------------------------------------------
# Layer 1c: proof-SUBSTANCE heuristic (WARN-only, M3b) — pure, no Lean needed
#
# The heuristic flags the SYNTACTIC SHADOW of the trivial pattern: a theorem
# that (a) binds a relational hypothesis (the empirical finding as a premise)
# AND (b) is discharged by a single arithmetic-monotonicity/transitivity
# library lemma with no construction. It NEVER affects `compiled` and is
# deliberately conservative — the real judgment is the LLM REVIEW dimension.
# ---------------------------------------------------------------------------

# The actual d7 headline theorem (the trivial pattern this gate targets):
# assumes the empirical finding `g_b ≤ g_p` and applies Nat-mult monotonicity.
_D7_TRIVIAL_SRC = (
    "theorem plausibility_moves_at_least_binding\n"
    "    (alpha g_b g_p : Nat) (h : g_b ≤ g_p) :\n"
    "    alpha * g_b ≤ alpha * g_p :=\n"
    "  Nat.mul_le_mul_left alpha h\n"
)

# A substantive shape (the d5 contrast): a constructive existence proof that
# builds a witness — must NOT trip the heuristic.
_SUBSTANTIVE_WITNESS_SRC = (
    "theorem separation_regime_nonempty :\n"
    "    ∃ img txt R, aligned img txt ∧ ¬ axis_aligned R := by\n"
    "  refine ⟨mkImg, mkTxt, mkR, ?_, ?_⟩\n"
    "  · decide\n"
    "  · decide\n"
)


def test_substance_heuristic_trips_on_d7_trivial_pattern():
    """The real d7 headline (assume `g_b ≤ g_p`, apply `Nat.mul_le_mul_left`)
    trips the WARN-only heuristic."""
    warns = lean_verify.trivial_pattern_warnings(_D7_TRIVIAL_SRC)
    assert warns, "d7's assume-the-finding + mul-monotonicity theorem should trip"
    assert "Nat.mul_le_mul_left" in warns[0]
    assert "WARN-only" in warns[0]


def test_substance_heuristic_clean_on_constructive_witness():
    """A constructive-existence witness proof (the d5 substantive shape) must
    NOT trip — the anonymous constructor / refine marks real work."""
    assert lean_verify.trivial_pattern_warnings(_SUBSTANTIVE_WITNESS_SRC) == []


def test_substance_heuristic_clean_without_hypothesis():
    """A monotonicity lemma applied with NO bound hypothesis is not the
    'assume-the-finding' pattern — no premise to restate, so no warning."""
    src = "theorem t (a : Nat) : a ≤ a := le_refl a\n"
    assert lean_verify.trivial_pattern_warnings(src) == []


def test_substance_heuristic_clean_on_multistep_proof():
    """A relational hypothesis discharged via a multi-step tactic block (real
    derivation) does NOT trip even if a monotonicity lemma name appears."""
    src = (
        "theorem t (a b : Nat) (h : a ≤ b) : a ≤ b := by\n"
        "  have h2 := Nat.le_trans h (le_refl b)\n"
        "  exact h2\n"
    )
    assert lean_verify.trivial_pattern_warnings(src) == []


def test_substance_warnings_never_flip_compiled():
    """The heuristic is WARN-only: a trivial-but-true theorem still has
    `compiled` decided purely by `parse_lean_output`; substance_warnings is a
    separate, non-blocking field."""
    compiled, errors, _ = lean_verify.parse_lean_output(
        returncode=0, stdout="", stderr="", source=_D7_TRIVIAL_SRC,
    )
    assert compiled is True and errors == []
    # And the heuristic still flags it independently.
    assert lean_verify.trivial_pattern_warnings(_D7_TRIVIAL_SRC)


def test_substance_warnings_attached_to_tool_missing_verdict(tmp_path, monkeypatch):
    """substance_warnings is populated from source even when Lean is absent
    (it is a pure source-level hint)."""
    monkeypatch.setattr(lean_verify, "_resolve_bin", lambda name: None)
    lean_path = tmp_path / "Claim.lean"
    lean_path.write_text(_D7_TRIVIAL_SRC)
    v = lean_verify.verify_lean_file(lean_path)
    assert v.tool_missing is True
    assert v.substance_warnings, "trivial pattern should be flagged in verdict"
    assert "substance_warnings" in v.to_dict()


# ---------------------------------------------------------------------------
# Library-TRIVIA heuristic (WARN-only; the math-LIBRARY substance gate) — pure
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("src", [
    "theorem t : True := trivial",
    "theorem t : True := by trivial",
    "theorem t : 1 = 1 := by decide",
    "theorem t : True := by native_decide",
    "theorem t : 2 = 2 := by rfl",
])
def test_library_trivia_flags_tautology_closers(src):
    """Content-free tautology closers (the math-LIBRARY proof-theater the paper
    layers deliberately ignore) ARE flagged by the library-trivia heuristic."""
    warns = lean_verify.library_trivia_warnings(src)
    assert warns and "LIBRARY-trivia" in warns[0]


@pytest.mark.parametrize("src", [
    # named-lemma wrappers — the lemma IS the content
    "theorem sqrt2 : Irrational (Real.sqrt 2) := irrational_sqrt_two",
    "theorem add2 (a b : Nat) : a + b = b + a := Nat.add_comm a b",
    # real derivation tactics
    "theorem cs (a b c d : Real) : (a*c+b*d)^2 <= (a^2+b^2)*(c^2+d^2)"
    " := by nlinarith [sq_nonneg (a*d-b*c)]",
    "theorem t : 2 + 2 = 4 := by norm_num",
    # discharges a relational hypothesis — left to the paper-M3b layers
    "theorem mono (a b : Nat) (h : a ≤ b) : a ≤ b := h",
])
def test_library_trivia_clean_on_real_short_proof(src):
    """A genuine short proof (named-lemma wrapper, derivation tactic, or
    hypothesis discharge) is NEVER flagged — WARN-not-reject discipline."""
    assert lean_verify.library_trivia_warnings(src) == []


def test_library_trivia_never_flips_compiled():
    """library_trivia_warnings is WARN-only — orthogonal to compiled/errors."""
    src = "theorem t : True := by trivial"
    compiled, errors, _ = lean_verify.parse_lean_output(
        returncode=0, stdout="", stderr="", source=src,
    )
    assert compiled is True and errors == []
    assert lean_verify.library_trivia_warnings(src)


# ---------------------------------------------------------------------------
# Layer 1e: ELEMENTARY-theorem heuristic (WARN-only, M3b DEPTH layer) — pure
#
# The depth layer ABOVE trivial_pattern_warnings. It flags elementary
# proof-theater: a theorem that is substantive-by-the-trivial-heuristic yet a
# closed/constant-table fact proved by a single algebraic-identity tactic
# (`ring`/`norm_num`/`rfl`) — true-but-textbook (the live XOR-table catch). It
# must NOT fire on a genuine constructed-witness check (d5) or a derived bound
# (d7), which use `native_decide`. The fixtures are the REAL .lean theorem
# text from the live cases (copied in, not read from workspaces at test time).
# ---------------------------------------------------------------------------

# (a) The LIVE catch: fair-erasure-durability's headline H4 — the
# XOR-is-not-linearly-separable parity table. A 4-point CONSTANT table closed
# by `simp only [const-defs]; ring` / `norm_num`. substance_warnings stamped
# [] on this (M3b-substantive by the old gate). It must now WARN elementary.
# (Real theorem text from 002_execute/lean/Separation.lean.)
_XOR_TABLE_SRC = (
    "def p1 : Pt := ⟨ 1,  1,  1⟩\n"
    "def p2 : Pt := ⟨-1, -1,  1⟩\n"
    "def p3 : Pt := ⟨ 1, -1, -1⟩\n"
    "def p4 : Pt := ⟨-1,  1, -1⟩\n"
    "def g (p : Pt) : ℝ := p.x1 * p.x2\n"
    "noncomputable def meanPos (w1 w2 : ℝ) : ℝ := (lin w1 w2 p1 + lin w1 w2 p2) / 2\n"
    "theorem linear_clean (w1 w2 : ℝ) : meanPos w1 w2 = meanNeg w1 w2 := by\n"
    "  simp only [meanPos, meanNeg, lin, p1, p2, p3, p4]; ring\n"
    "theorem nonlinear_perfect :\n"
    "    p1.y * g p1 = 1 ∧ p2.y * g p2 = 1 := by\n"
    "  refine ⟨?_, ?_⟩ <;> simp only [g, p1, p2] <;> ring\n"
    "theorem nonlinear_margin :\n"
    "    ∃ γ : ℝ, γ > 0 ∧ p1.y * g p1 ≥ γ := by\n"
    "  refine ⟨1, by norm_num, ?_⟩ <;> simp only [g, p1] <;> norm_num\n"
)

# (b) The d5 substantive bar: T1_T2_witness — CONSTRUCTS explicit rational
# matrices (img/txt/R) and VERIFIES the separation regime by `native_decide`.
# The constructed witness IS the contribution; truth is not visible from the
# statement. Must NOT warn. (Real theorem text from
# 026_execute/lean/T1_T2_witness.lean.)
_D5_WITNESS_SRC = (
    "def img : Mat := [ [1, 0], [0, 1] ]\n"
    "def txt : Mat := [ [7/25, 24/25], [9/41, 40/41] ]\n"
    "def R : Mat := [ [7/25, -24/25], [24/25, 7/25] ]\n"
    "theorem T1_small_matched_cosine :\n"
    "    maxAbsMatchedCos2 img txt ≤ (7/25) * (7/25) := by native_decide\n"
    "theorem T1_cka_one : linearCkaSq img txt = 1 := by native_decide\n"
    "theorem T2_R_orthogonal : mulMat R (transpose R) = idMat 8 := by native_decide\n"
)

# (c) The d7 substantive bar: a Cauchy–Schwarz CEILING that DERIVES the
# dissociation from MEASURED collinearity, with rational witnesses verified by
# `native_decide`. Must NOT warn. (Real theorem text from
# 042_execute/lean/T1b_star_cauchy_schwarz.lean.)
_D7_CEILING_SRC = (
    "def u : Vec := [3/5, 4/5]\n"
    "def v_llava : Vec := [1, 0]\n"
    "def c_llava : Vec := [18375/18617, 2992/18617]\n"
    "def k_llava : Rat := 18375/18617\n"
    "theorem llava_residual_identity :\n"
    "    normSq (sub v_llava c_llava) = 2 * (1 - k_llava) := by native_decide\n"
    "theorem llava_cauchy_schwarz_ceiling :\n"
    "    (dot (sub v_llava c_llava) u) * (dot (sub v_llava c_llava) u)\n"
    "      ≤ 2 * (1 - k_llava) * normSq u := by native_decide\n"
)


def test_elementary_heuristic_warns_on_xor_constant_table():
    """(a) The LIVE catch: the XOR/parity 4-point CONSTANT table closed by
    `ring`/`norm_num` — substantive by the trivial heuristic, but elementary
    proof-theater. Must WARN."""
    warns = lean_verify.elementary_theorem_warnings(_XOR_TABLE_SRC)
    assert warns, "the XOR constant-table headline (ring/norm_num) must warn"
    assert "ELEMENTARY" in warns[0]
    assert "WARN-only" in warns[0]
    # And it appears through the unified substance entry point + verdict field.
    assert any("ELEMENTARY" in w for w in lean_verify.substance_warnings(_XOR_TABLE_SRC))


def test_elementary_heuristic_clean_on_d5_native_decide_witness():
    """(b) The d5 constructed-witness check (`native_decide` over built
    rational matrices) is the depth bar — must NOT warn. `native_decide`
    anywhere disables the heuristic."""
    assert lean_verify.elementary_theorem_warnings(_D5_WITNESS_SRC) == []
    assert not any(
        "ELEMENTARY" in w for w in lean_verify.substance_warnings(_D5_WITNESS_SRC)
    )


def test_elementary_heuristic_clean_on_d7_derived_ceiling():
    """(c) The d7 Cauchy–Schwarz ceiling (derives the dissociation from
    measured collinearity; `native_decide`) — must NOT warn."""
    assert lean_verify.elementary_theorem_warnings(_D7_CEILING_SRC) == []
    assert not any(
        "ELEMENTARY" in w for w in lean_verify.substance_warnings(_D7_CEILING_SRC)
    )


def test_elementary_heuristic_clean_on_omega_bound_with_free_vars():
    """A derived linear bound over FREE variables (`omega`) generalises — it is
    a real consequence, not a closed constant. Must NOT warn."""
    src = "theorem t_bound (n : Nat) : 0 < n + 1 := by omega\n"
    assert lean_verify.elementary_theorem_warnings(src) == []


def test_elementary_heuristic_clean_on_named_lemma_derivation():
    """A proof that applies a named lemma / multi-step derivation (not a pure
    identity tactic) is doing work — must NOT warn even with no decide."""
    src = (
        "theorem t (a b : Nat) (h : a ≤ b) : a ≤ b := by\n"
        "  apply Nat.le_trans h\n"
        "  exact le_refl b\n"
    )
    assert lean_verify.elementary_theorem_warnings(src) == []


def test_substance_warnings_unifies_both_layers():
    """substance_warnings() runs BOTH layers. The d7-trivial assume-then-
    monotonicity case still warns via layer 1 (regression); the XOR table
    warns via layer 2; a clean witness-check warns from neither."""
    # (d) regression: the original assume-then-monotonicity trivial pattern
    # still trips (via trivial_pattern_warnings inside substance_warnings).
    trivial = lean_verify.substance_warnings(_D7_TRIVIAL_SRC)
    assert trivial, "the assume-then-monotonicity pattern must still warn"
    assert any("monotonicity" in w for w in trivial)
    # XOR table warns elementary; witness-checks warn from neither layer.
    assert lean_verify.substance_warnings(_XOR_TABLE_SRC)
    assert lean_verify.substance_warnings(_D5_WITNESS_SRC) == []
    assert lean_verify.substance_warnings(_D7_CEILING_SRC) == []


def test_elementary_warning_surfaces_in_verdict(tmp_path, monkeypatch):
    """The elementary warning flows into LeanVerdict.substance_warnings even
    when Lean is absent (pure source-level hint) — the verdict the XOR case
    SHOULD have carried."""
    monkeypatch.setattr(lean_verify, "_resolve_bin", lambda name: None)
    lean_path = tmp_path / "Separation.lean"
    lean_path.write_text(_XOR_TABLE_SRC)
    v = lean_verify.verify_lean_file(lean_path)
    assert v.tool_missing is True
    assert any("ELEMENTARY" in w for w in v.substance_warnings)


def test_elementary_warnings_never_flip_compiled():
    """WARN-only: an elementary-but-true theorem still has `compiled` decided
    purely by parse_lean_output; substance_warnings is a separate field."""
    compiled, errors, _ = lean_verify.parse_lean_output(
        returncode=0, stdout="", stderr="", source=_XOR_TABLE_SRC,
    )
    assert compiled is True and errors == []
    assert lean_verify.elementary_theorem_warnings(_XOR_TABLE_SRC)


# ---------------------------------------------------------------------------
# Layer 1f: LINEAR-REARRANGEMENT heuristic (WARN-only, M3b SIBLING) — pure
#
# The sibling of the elementary/trivial layers. It flags a use of the
# linear-equality solvers (`linarith`/`omega`/`linear_combination`) that derives
# NOTHING: the goal is a single linear EQUALITY and a lone hypothesis is the goal
# merely TRANSPOSED across `=` (same atom set both sides). `linarith`/`omega` are
# DEEP markers (they disable the elementary layer), so this degenerate
# transposition use needs its own detector. It must NOT fire when the solver
# COMBINES ≥2 hyps, derives a quadratic bound (`nlinarith` Cauchy–Schwarz), or
# derives a bound over FREE vars (`omega`, goal atoms ⊋ hyp atoms).
# ---------------------------------------------------------------------------

# (a) The LIVE catch: example-study-d6's headline `chk`. `hpart` IS the goal
# rearranged (move `rMS*rSR` across `=`); `linarith` only transposes, derives
# nothing — yet it was sold as "an identity we prove in Lean 4". Must WARN.
_D6_REARRANGEMENT_SRC = (
    "theorem chk (rMR rMS rSR rpart g : ℝ) "
    "(hpart : rpart * g = rMR - rMS * rSR) :\n"
    "    rMR = rpart * g + rMS * rSR := by linarith [hpart]\n"
)

# (b) A REAL nlinarith bound COMBINING two hypotheses into a quadratic
# consequence — substance, must NOT warn.
_NLINARITH_TWO_HYP_SRC = (
    "theorem two_hyp (a b c : ℝ) (h1 : a ≤ b) (h2 : b ≤ c) : a * a ≤ c * c := by\n"
    "  nlinarith [h1, h2]\n"
)

# (c) An `omega` derivation of a bound over a FREE variable — the goal's atoms
# are not the transpose of any single equality hypothesis; a real consequence.
# Must NOT warn.
_OMEGA_FREE_VAR_SRC = "theorem t_bound (n : Nat) : 0 < n + 1 := by omega\n"

# (d) The fair-erasure Cauchy–Schwarz shape: `nlinarith` derives a QUADRATIC
# ceiling ⟪Q,Δ⟫² ≤ ‖Δ‖²·… from INDEPENDENT premises + `sq_nonneg`. Real work,
# must NOT warn (nlinarith is a rearrangement disqualifier).
_CAUCHY_SCHWARZ_SRC = (
    "theorem quadratic_reader_ceiling (Q d nd : ℝ) "
    "(hq : Q * Q ≤ nd) (hpos : 0 ≤ nd) :\n"
    "    (Q * d) * (Q * d) ≤ nd * (d * d) := by\n"
    "  nlinarith [hq, hpos, sq_nonneg d]\n"
)


def test_rearrangement_heuristic_warns_on_d6_chk_transposition():
    """(a) The LIVE catch: d6's `chk` — `linarith [hpart]` where `hpart` is the
    goal transposed (same atom set both sides). Derives nothing; must WARN."""
    warns = lean_verify.linear_rearrangement_warnings(_D6_REARRANGEMENT_SRC)
    assert warns, "d6's linarith-transposes-one-equality theorem must warn"
    assert "LINEAR-REARRANGEMENT" in warns[0]
    assert "WARN-only" in warns[0]
    assert "chk" in warns[0] and "hpart" in warns[0]
    # And it surfaces through the unified substance entry point.
    assert any(
        "LINEAR-REARRANGEMENT" in w
        for w in lean_verify.substance_warnings(_D6_REARRANGEMENT_SRC)
    )


def test_rearrangement_heuristic_clean_on_nlinarith_combining_two_hyps():
    """(b) `nlinarith` combining TWO hypotheses into a quadratic bound is real
    work — `nlinarith` is a disqualifier, so it must NOT warn."""
    assert lean_verify.linear_rearrangement_warnings(_NLINARITH_TWO_HYP_SRC) == []
    assert not any(
        "LINEAR-REARRANGEMENT" in w
        for w in lean_verify.substance_warnings(_NLINARITH_TWO_HYP_SRC)
    )


def test_rearrangement_heuristic_clean_on_omega_bound_over_free_vars():
    """(c) `omega` deriving a bound over a FREE variable (no single equality
    hypothesis whose atoms equal the goal's) — a real consequence. Must NOT
    warn."""
    assert lean_verify.linear_rearrangement_warnings(_OMEGA_FREE_VAR_SRC) == []
    assert not any(
        "LINEAR-REARRANGEMENT" in w
        for w in lean_verify.substance_warnings(_OMEGA_FREE_VAR_SRC)
    )


def test_rearrangement_heuristic_clean_on_cauchy_schwarz_quadratic_ceiling():
    """(d) The fair-erasure Cauchy–Schwarz `nlinarith` ceiling derives a
    quadratic bound from independent premises — substance. Must NOT warn."""
    assert lean_verify.linear_rearrangement_warnings(_CAUCHY_SCHWARZ_SRC) == []
    assert not any(
        "LINEAR-REARRANGEMENT" in w
        for w in lean_verify.substance_warnings(_CAUCHY_SCHWARZ_SRC)
    )


def test_rearrangement_heuristic_clean_when_solver_combines_two_equalities():
    """`linarith [h1, h2]` chaining TWO equalities (a = b, b = c ⊢ a = c)
    COMBINES independent facts — there is no single transposable hypothesis, so
    it must NOT warn."""
    src = (
        "theorem comb (a b c : ℝ) (h1 : a = b) (h2 : b = c) : a = c := by\n"
        "  linarith [h1, h2]\n"
    )
    assert lean_verify.linear_rearrangement_warnings(src) == []


def test_rearrangement_heuristic_clean_on_inequality_hypothesis_and_goal():
    """A single ≤-hypothesis feeding a ≤-goal via `linarith` is a genuine bound
    (not a transposition across `=`) — the hypothesis is an inequality, so the
    detector requires an EQUALITY hyp and stays clean."""
    src = "theorem t (a b : ℝ) (h : a ≤ b) : a - 1 ≤ b := by linarith [h]\n"
    assert lean_verify.linear_rearrangement_warnings(src) == []


def test_rearrangement_heuristic_clean_on_linear_combination_two_facts():
    """`linear_combination h1 + 2*h2` derives an equality from TWO independent
    equalities — a real combination, not a single transposition. Must NOT
    warn."""
    src = (
        "theorem t (a b c : ℝ) (h1 : a = 2*b) (h2 : b = 3*c) : a = 6*c := by\n"
        "  linear_combination h1 + 2*h2\n"
    )
    assert lean_verify.linear_rearrangement_warnings(src) == []


def test_rearrangement_heuristic_does_not_disturb_other_layers_or_fixtures():
    """The new layer must stay clean on every existing fixture (the elementary
    XOR table, the d5/d7 witness checks, the d7 assume-then-monotonicity
    trivial pattern) — it targets ONLY linarith/omega transposition."""
    for src in (_XOR_TABLE_SRC, _D5_WITNESS_SRC, _D7_CEILING_SRC, _D7_TRIVIAL_SRC):
        assert lean_verify.linear_rearrangement_warnings(src) == []


def test_substance_warnings_runs_all_three_layers():
    """substance_warnings() now unifies THREE layers. The d6 transposition warns
    via the rearrangement layer; the elementary/trivial cases via theirs; the
    real witness/bound proofs via none."""
    assert any(
        "LINEAR-REARRANGEMENT" in w
        for w in lean_verify.substance_warnings(_D6_REARRANGEMENT_SRC)
    )
    assert lean_verify.substance_warnings(_XOR_TABLE_SRC)        # elementary
    assert lean_verify.substance_warnings(_D7_TRIVIAL_SRC)       # trivial
    assert lean_verify.substance_warnings(_CAUCHY_SCHWARZ_SRC) == []
    assert lean_verify.substance_warnings(_NLINARITH_TWO_HYP_SRC) == []


def test_rearrangement_warning_surfaces_in_verdict(tmp_path, monkeypatch):
    """The rearrangement warning flows into LeanVerdict.substance_warnings even
    when Lean is absent (pure source-level hint) — the verdict the d6 `chk`
    theorem SHOULD have carried."""
    monkeypatch.setattr(lean_verify, "_resolve_bin", lambda name: None)
    lean_path = tmp_path / "Chk.lean"
    lean_path.write_text(_D6_REARRANGEMENT_SRC)
    v = lean_verify.verify_lean_file(lean_path)
    assert v.tool_missing is True
    assert any("LINEAR-REARRANGEMENT" in w for w in v.substance_warnings)


def test_rearrangement_warnings_never_flip_compiled():
    """WARN-only: a transposition-theater theorem still has `compiled` decided
    purely by parse_lean_output; substance_warnings is a separate field."""
    compiled, errors, _ = lean_verify.parse_lean_output(
        returncode=0, stdout="", stderr="", source=_D6_REARRANGEMENT_SRC,
    )
    assert compiled is True and errors == []
    assert lean_verify.linear_rearrangement_warnings(_D6_REARRANGEMENT_SRC)


# ---------------------------------------------------------------------------
# Layer 1b: verdict dataclass + tool-missing path (no Lean needed)
# ---------------------------------------------------------------------------

def test_verdict_to_dict_roundtrip():
    v = lean_verify.LeanVerdict(compiled=True, theorems_checked=["t"])
    d = v.to_dict()
    assert d["compiled"] is True
    assert d["theorems_checked"] == ["t"]
    assert v.ok is True


# ---------------------------------------------------------------------------
# Layer 1c: COMPUTE PLACEMENT — Lean is a CPU-class workload (default), the
# child run is thread-pinned (LEAN_NUM_THREADS), and there is a forward hook
# for a future GPU-requiring Lean step. Default = cpu = today's behaviour.
# ---------------------------------------------------------------------------

def test_compute_class_default_is_cpu():
    """No request → the canonical CPU class (today's behaviour, zero change)."""
    assert lean_verify.compute_class() == CONST.COMPUTE_CLASS_CPU
    assert lean_verify.compute_class(None) == CONST.COMPUTE_CLASS_CPU
    assert lean_verify.compute_class() == "cpu"


def test_compute_class_recognises_gpu_forward_hook():
    """The forward hook resolves an explicit gpu request to the canonical
    gpu class (case-insensitive); the value is canonical, never a bare str."""
    assert lean_verify.compute_class("gpu") == CONST.COMPUTE_CLASS_GPU
    assert lean_verify.compute_class("GPU") == CONST.COMPUTE_CLASS_GPU
    assert lean_verify.compute_class("cpu") == CONST.COMPUTE_CLASS_CPU


def test_compute_class_unknown_falls_back_to_default_cpu():
    """An unrecognised class is conservatively the default (never silently
    treated as gpu)."""
    assert lean_verify.compute_class("quantum") == CONST.LEAN_DEFAULT_COMPUTE_CLASS
    assert lean_verify.compute_class("") == CONST.LEAN_DEFAULT_COMPUTE_CLASS
    assert lean_verify.compute_class("  cpu  ") == CONST.COMPUTE_CLASS_CPU


def test_verdict_records_compute_class_default_cpu(tmp_path, monkeypatch):
    """A verify (even on the tool-missing path) records compute_class=cpu by
    default, so the placement is auditable in the verdict JSON."""
    monkeypatch.setattr(lean_verify, "_resolve_bin", lambda name: None)
    lean_path = tmp_path / "Claim.lean"
    lean_path.write_text("theorem t : True := trivial\n")
    v = lean_verify.verify_lean_file(lean_path)
    assert v.compute_class == "cpu"
    assert v.to_dict()["compute_class"] == "cpu"


def test_verify_propagates_requested_compute_class(tmp_path, monkeypatch):
    """The forward hook flows through: a gpu request is recorded on the
    verdict. (Today still runs the local toolchain — no GPU-Lean backend
    exists — but the intent is now auditable for a future router.)"""
    monkeypatch.setattr(lean_verify, "_resolve_bin", lambda name: None)
    lean_path = tmp_path / "Claim.lean"
    lean_path.write_text("theorem t : True := trivial\n")
    v = lean_verify.verify_lean_file(lean_path, compute_class_requested="gpu")
    assert v.compute_class == "gpu"


def test_lean_thread_cap_default_is_bounded(monkeypatch):
    """With no override, the cap is a bounded fraction of the host cores,
    clamped to LEAN_MAX_DEFAULT_THREADS and never below 1 — so a heavy proof
    can't grab every core on a shared host."""
    monkeypatch.delenv(CONST.ENV_LEAN_NUM_THREADS, raising=False)
    monkeypatch.setattr(lean_verify.os, "cpu_count", lambda: 64)
    cap = lean_verify._lean_thread_cap()
    assert cap == CONST.LEAN_MAX_DEFAULT_THREADS  # 64//2 clamped to 8
    monkeypatch.setattr(lean_verify.os, "cpu_count", lambda: 1)
    assert lean_verify._lean_thread_cap() == 1  # never below 1


def test_lean_thread_cap_explicit_override(monkeypatch):
    """An explicit positive OMICAIRN_LEAN_NUM_THREADS pins exactly; a non-positive
    / unparsable value falls back to the computed default."""
    monkeypatch.setattr(lean_verify.os, "cpu_count", lambda: 16)
    monkeypatch.setenv(CONST.ENV_LEAN_NUM_THREADS, "3")
    assert lean_verify._lean_thread_cap() == 3
    monkeypatch.setenv(CONST.ENV_LEAN_NUM_THREADS, "0")
    assert lean_verify._lean_thread_cap() == min(16 // 2, CONST.LEAN_MAX_DEFAULT_THREADS)
    monkeypatch.setenv(CONST.ENV_LEAN_NUM_THREADS, "notanint")
    assert lean_verify._lean_thread_cap() == min(16 // 2, CONST.LEAN_MAX_DEFAULT_THREADS)


def test_lean_child_env_sets_thread_cap_without_clobbering(monkeypatch):
    """_lean_child_env sets LEAN_NUM_THREADS to the cap when unset, NEVER
    overrides an explicit LEAN_NUM_THREADS the operator set, and never mutates
    the parent os.environ."""
    monkeypatch.delenv(CONST.ENV_LEAN_NUM_THREADS, raising=False)
    monkeypatch.delenv(CONST.LEAN_NATIVE_NUM_THREADS_ENV, raising=False)
    monkeypatch.setattr(lean_verify.os, "cpu_count", lambda: 8)
    env = lean_verify._lean_child_env()
    assert env[CONST.LEAN_NATIVE_NUM_THREADS_ENV] == "4"  # 8//2
    # Parent env untouched.
    assert CONST.LEAN_NATIVE_NUM_THREADS_ENV not in lean_verify.os.environ
    # An operator-set LEAN_NUM_THREADS is NOT clobbered.
    monkeypatch.setenv(CONST.LEAN_NATIVE_NUM_THREADS_ENV, "13")
    env2 = lean_verify._lean_child_env()
    assert env2[CONST.LEAN_NATIVE_NUM_THREADS_ENV] == "13"


def test_run_lean_launches_with_thread_pinned_env(tmp_path, monkeypatch):
    """End-to-end placement check: verify_lean_file launches the child with a
    LEAN_NUM_THREADS-pinned env (confirms the cap actually reaches the
    subprocess, not just the helper)."""
    monkeypatch.setattr(lean_verify, "_resolve_bin", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(lean_verify, "_find_lake_project", lambda p: None)
    monkeypatch.delenv(CONST.LEAN_NATIVE_NUM_THREADS_ENV, raising=False)
    monkeypatch.delenv(CONST.ENV_LEAN_NUM_THREADS, raising=False)
    monkeypatch.setattr(lean_verify.os, "cpu_count", lambda: 8)

    captured: dict = {}

    class _FakeProc:
        pid = 4321
        returncode = 0

        def communicate(self, timeout=None):
            return "", ""

    def fake_popen(cmd, **kwargs):
        captured["env"] = kwargs.get("env")
        return _FakeProc()

    monkeypatch.setattr(lean_verify.subprocess, "Popen", fake_popen)
    lean_path = tmp_path / "Claim.lean"
    lean_path.write_text("theorem t : True := trivial\n")
    lean_verify.verify_lean_file(lean_path)
    env = captured["env"]
    assert env is not None
    assert env[CONST.LEAN_NATIVE_NUM_THREADS_ENV] == "4"


def test_verify_missing_file_reports_failure_not_crash(tmp_path, monkeypatch):
    """Pointing the verifier at a non-existent file must not raise; it
    returns a non-compiled verdict (tool_missing if Lean absent, else a
    real lean error)."""
    missing = tmp_path / "DoesNotExist.lean"
    v = lean_verify.verify_lean_file(missing)
    assert v.compiled is False


def test_tool_missing_path_when_no_lean(tmp_path, monkeypatch):
    """Force the toolchain-discovery to find nothing and confirm we get a
    structured tool_missing verdict (not an exception). This exercises the
    'Lean absent' branch even on a host where Lean IS installed."""
    monkeypatch.setattr(lean_verify, "_resolve_bin", lambda name: None)
    lean_path = tmp_path / "Claim.lean"
    lean_path.write_text("theorem t : 2 + 2 = 4 := by rfl\n")
    v = lean_verify.verify_lean_file(lean_path)
    assert v.compiled is False
    assert v.tool_missing is True
    # Theorem names are still parsed from source even without a toolchain.
    assert v.theorems_checked == ["t"]


# ---------------------------------------------------------------------------
# Layer 1d: reusable Mathlib Lake project dir — canonical + never dash-leading
#
# Regression guard for the stray ``-lean-mathlib-omicairn/`` dir (a DASH-leading
# sibling of the intended dot-prefixed ``.lean-mathlib-omicairn/``) created from a
# hand-typed ``lake new -lean-mathlib-omicairn math``. The dir name now comes from
# one canonical constant and a safe constructor that refuses a dash-leading
# name, so OmiCairn code cannot re-create the stray dir.
# ---------------------------------------------------------------------------

def test_mathlib_project_dirname_is_dot_prefixed_not_dash():
    """The canonical dir name is dot-prefixed and never begins with '-'
    (a dash-leading path is parsed as a flag by shells/lake/lean/argparse)."""
    assert LEAN_MATHLIB_PROJECT_DIRNAME == ".lean-mathlib-omicairn"
    assert LEAN_MATHLIB_PROJECT_DIRNAME.startswith(".")
    assert not LEAN_MATHLIB_PROJECT_DIRNAME.startswith("-")


def test_mathlib_project_dir_builds_canonical_path(tmp_path):
    """The safe constructor joins the canonical name under any root and the
    resulting basename is exactly the constant — never a stray dash variant."""
    p = lean_verify.mathlib_project_dir(tmp_path)
    assert p == tmp_path / LEAN_MATHLIB_PROJECT_DIRNAME
    assert p.name == ".lean-mathlib-omicairn"
    assert not p.name.startswith("-")


def test_safe_path_arg_neutralises_dash_leading_basename(tmp_path):
    """A file whose basename begins with '-' is returned as an ABSOLUTE path
    (starts with '/'), so it cannot be mis-parsed as a lean/lake option flag."""
    dash_file = tmp_path / "-weird.lean"
    dash_file.write_text("theorem t : True := trivial\n")
    arg = lean_verify._safe_path_arg(dash_file)
    assert arg.startswith("/")
    assert not pathlib.Path(arg).name.startswith("/")  # basename unchanged
    assert arg == str(dash_file.resolve())


def test_verify_uses_end_of_options_sentinel_and_abs_path(tmp_path, monkeypatch):
    """The command built for a dash-leading file places it after a ``--``
    end-of-options sentinel AND as an absolute path — belt-and-suspenders so
    no path can leak in as a flag. Capture the cmd via a fake _run_lean."""
    monkeypatch.setattr(lean_verify, "_resolve_bin",
                        lambda name: f"/usr/bin/{name}")
    # No lakefile in ancestry of tmp_path → bare `lean` branch.
    monkeypatch.setattr(lean_verify, "_find_lake_project", lambda p: None)

    captured: dict = {}

    def fake_run(cmd, *, cwd, timeout_s):
        captured["cmd"] = cmd
        return 0, "", False

    monkeypatch.setattr(lean_verify, "_run_lean", fake_run)
    dash_file = tmp_path / "-weird.lean"
    dash_file.write_text("theorem t : True := trivial\n")
    lean_verify.verify_lean_file(dash_file)
    cmd = captured["cmd"]
    assert "--" in cmd, f"expected end-of-options sentinel in {cmd}"
    # The file arg comes AFTER the sentinel and is absolute.
    file_arg = cmd[cmd.index("--") + 1]
    assert file_arg.startswith("/")
    assert file_arg == str(dash_file.resolve())


# ---------------------------------------------------------------------------
# Layer 2: real-Lean end-to-end (gated on toolchain availability)
# ---------------------------------------------------------------------------

_LEAN_AVAILABLE = lean_verify.lean_toolchain_available()
_skip_no_lean = pytest.mark.skipif(
    not _LEAN_AVAILABLE,
    reason="Lean toolchain not found on PATH/~/.elan/bin — install via "
           "elan to run the real-Lean checks (parser logic is tested "
           "separately on fixtures).",
)


@_skip_no_lean
def test_real_lean_true_lemma_verifies(tmp_path):
    """A TRUE lemma checks: `2 + 2 = 4 := by rfl` compiles clean."""
    v = lean_verify.verify_lean_source(
        "theorem t_true : 2 + 2 = 4 := by rfl\n", name="TrueClaim",
    )
    assert v.compiled is True, f"expected pass; got errors={v.errors}"
    assert v.tool_missing is False
    assert "t_true" in v.theorems_checked
    assert v.returncode == 0


@_skip_no_lean
def test_real_lean_omega_bound_verifies(tmp_path):
    """A slightly richer TRUE claim (a linear bound via `omega`) checks —
    the shape of a 'quantity bounded below' derivation."""
    v = lean_verify.verify_lean_source(
        "theorem t_bound (n : Nat) : 0 < n + 1 := by omega\n", name="Bound",
    )
    assert v.compiled is True, f"expected pass; got errors={v.errors}"


@_skip_no_lean
def test_real_lean_false_proof_fails(tmp_path):
    """A FALSE/broken proof is reported as failing, not proven."""
    v = lean_verify.verify_lean_source(
        "theorem t_false : 2 + 2 = 5 := by rfl\n", name="FalseClaim",
    )
    assert v.compiled is False
    assert v.tool_missing is False
    assert v.errors, "a broken proof must surface errors"


@_skip_no_lean
def test_real_lean_sorry_fails(tmp_path):
    """A `sorry` proof (rc 0 + warning) is reported as NOT established."""
    v = lean_verify.verify_lean_source(
        "theorem t_hole : 2 + 2 = 4 := by sorry\n", name="HoleClaim",
    )
    assert v.compiled is False


@_skip_no_lean
def test_real_lean_in_lake_scaffold_builds():
    """The bundled Mathlib-free Lake scaffold's Basic.lean checks via the
    verifier in its project context (lake env lean)."""
    repo_root = pathlib.Path(lean_verify.__file__).resolve().parent.parent
    basic = repo_root / "LeanScaffold" / "LeanScaffold" / "Basic.lean"
    assert basic.exists(), f"scaffold source missing at {basic}"
    v = lean_verify.verify_lean_file(basic)
    assert v.compiled is True, f"scaffold should check; errors={v.errors}"
    assert "two_plus_two" in v.theorems_checked


# ---------------------------------------------------------------------------
# Comment-aware sorry detection (lean_verify + lab_theorems.has_sorry)
# ---------------------------------------------------------------------------

def test_strip_lean_comments():
    s = "theorem t : 1=1 := by rfl  -- no sorry here\n/- block sorry -/\ndef x := 1"
    out = lean_verify.strip_lean_comments(s)
    assert "sorry" not in out
    assert "theorem t" in out and "def x" in out


def test_strip_lean_comments_nested_block():
    s = "/- outer /- inner sorry -/ still comment -/ theorem t : True := trivial"
    out = lean_verify.strip_lean_comments(s)
    assert "sorry" not in out
    assert "theorem t" in out


def test_has_sorry_ignores_comment():
    assert not lab_theorems.has_sorry(
        "theorem t : 1=1 := by rfl  -- this proof avoids sorry"
    )
    assert not lab_theorems.has_sorry(
        "/-- A proof of X that needs no sorry. -/\ntheorem t : True := trivial"
    )


def test_has_sorry_detects_real_hole():
    assert lab_theorems.has_sorry("theorem t : P := by sorry")
    assert lab_theorems.has_sorry("theorem t : P := by admit")


def test_source_uses_sorry_word_boundary():
    # `sorrowful` / `admittedly` must not match.
    assert not lean_verify.source_uses_sorry("-- sorrowful proof\ntheorem t:1=1:=rfl")
    assert not lean_verify.source_uses_sorry("def admittedly := 1")
    assert lean_verify.source_uses_sorry("theorem t : P := sorry")
