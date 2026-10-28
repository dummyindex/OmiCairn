"""Tests for omicairn.contribution — the contribution-depth directive.

The knob (`contribution_type`: analysis | method | theory) decides what
KIND of contribution a run must make. `validate` normalises + loud-fails
on an unknown value; `render_directive` returns the per-type prompt block
the orchestrator injects into the {{contribution_directive}} token.
"""
from __future__ import annotations

import pytest

from omicairn import contribution
from omicairn.constants import (
    CONTRIBUTION_TYPES,
    DEFAULT_CONTRIBUTION_TYPE,
    ProofStatus,
)


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------

def test_validate_none_returns_default():
    assert contribution.validate(None) == DEFAULT_CONTRIBUTION_TYPE
    assert DEFAULT_CONTRIBUTION_TYPE == "analysis"


def test_validate_empty_string_returns_default():
    assert contribution.validate("") == "analysis"
    assert contribution.validate("   ") == "analysis"


@pytest.mark.parametrize("ctype", CONTRIBUTION_TYPES)
def test_validate_each_valid_value(ctype):
    assert contribution.validate(ctype) == ctype


def test_validate_is_case_insensitive_and_strips():
    assert contribution.validate("  METHOD ") == "method"
    assert contribution.validate("Theory") == "theory"


def test_validate_unknown_raises():
    with pytest.raises(ValueError, match="contribution_type must be one of"):
        contribution.validate("model")  # close-but-wrong


# ---------------------------------------------------------------------------
# render_directive
# ---------------------------------------------------------------------------

def test_render_default_is_analysis_block():
    block = contribution.render_directive(None)
    assert "ANALYSIS" in block
    # The analysis block must NOT demand a method / baselines.
    assert "NOT required to invent" in block


def test_render_method_block_demands_method_and_two_baselines():
    block = contribution.render_directive("method")
    assert "METHOD" in block
    # The core enforcement: propose a method AND beat >=2 real baselines
    # with an ablation — and analysis-only is insufficient.
    assert ">=2 REAL" in block
    assert "ablation" in block.lower()
    assert "INSUFFICIENT" in block


def test_render_theory_block_demands_lean_verification():
    block = contribution.render_directive("theory")
    assert "THEORY" in block
    assert "Lean" in block
    assert "conjecture" in block.lower()


@pytest.mark.parametrize("ctype", CONTRIBUTION_TYPES)
def test_render_is_nonempty_for_every_type(ctype):
    block = contribution.render_directive(ctype)
    assert block.strip()


def test_render_blocks_are_distinct_per_type():
    blocks = {c: contribution.render_directive(c) for c in CONTRIBUTION_TYPES}
    assert len(set(blocks.values())) == len(CONTRIBUTION_TYPES)


def test_render_theory_block_names_three_proof_states():
    """The THEORY directive must surface the new three-state vocabulary so
    EXECUTE can label a derived+validated claim proof-pending."""
    block = contribution.render_directive("theory")
    assert ProofStatus.PROVEN_LEAN in block
    assert ProofStatus.DERIVED_VALIDATED_PROOF_PENDING in block
    assert ProofStatus.CONJECTURE in block


# ---------------------------------------------------------------------------
# Part A — research_arc: validate_arc (OPTIONAL FREE TEXT, never raises)
# ---------------------------------------------------------------------------
#
# The arc is no longer an enum. It is a short free-form hint: validate_arc
# normalises (strip), maps empty -> None, and otherwise returns the hint
# VERBATIM. There is no fixed value set, so nothing is ever rejected.

def test_validate_arc_none_and_empty_return_none():
    assert contribution.validate_arc(None) is None
    assert contribution.validate_arc("") is None
    assert contribution.validate_arc("   ") is None


def test_validate_arc_returns_free_text_verbatim_stripped():
    # Any non-empty hint is accepted as-is (only outer whitespace stripped),
    # case PRESERVED — there is no canonical value set to normalise against.
    assert contribution.validate_arc("theory-first: derive then validate") == (
        "theory-first: derive then validate")
    assert contribution.validate_arc("  measure -> derive -> re-measure  ") == (
        "measure -> derive -> re-measure")
    # A legacy enum-shaped string is just free text now (an in-the-wild OT
    # case literally uses `empirical_first`): it must round-trip verbatim.
    assert contribution.validate_arc("empirical_first") == "empirical_first"


def test_validate_arc_never_raises_on_any_string():
    # No value set => nothing is "unknown"; arbitrary text is a valid hint.
    assert contribution.validate_arc("synthesis of 3 priors") == (
        "synthesis of 3 priors")
    assert contribution.validate_arc("backwards") == "backwards"


# ---------------------------------------------------------------------------
# Part A — research_arc: render_arc_directive (verbatim hint / neutral unset)
# ---------------------------------------------------------------------------

def test_render_arc_present_hint_is_rendered_verbatim_and_advisory():
    hint = "empirical-first: measure then abstract into a bound"
    block = contribution.render_arc_directive(hint)
    # The hint prints VERBATIM.
    assert hint in block
    # ... framed as ADVISORY, explicitly not a rule.
    assert "ADVISORY" in block
    assert "NOT a rule" in block


def test_render_arc_legacy_enum_string_renders_as_free_text():
    # `empirical_first` (a legacy value still used by an OT-case brief) now
    # prints verbatim as free text — correct, no special-casing.
    block = contribution.render_arc_directive("empirical_first")
    assert "empirical_first" in block


def test_render_arc_unset_is_neutral_and_forces_nothing():
    # An unset arc renders a neutral note and forces NOTHING — crucially NO
    # theory-first nudge, for ANY contribution_type. A theory case with no arc
    # must behave exactly as it did before this knob existed.
    for ctype in (None, "analysis", "method", "theory"):
        block = contribution.render_arc_directive(None, contribution_type=ctype)
        assert "unset" in block.lower()
        assert "THEORY-FIRST" not in block
        assert "theory-first" not in block.lower()
        # Same neutral block regardless of contribution_type (no fallback).
        assert block == contribution.render_arc_directive(None)


def test_render_arc_contribution_type_does_not_affect_present_hint():
    hint = "derive the bound, then validate on sim and real data"
    assert contribution.render_arc_directive(hint, contribution_type="theory") == (
        contribution.render_arc_directive(hint, contribution_type="analysis"))
