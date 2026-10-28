"""Tests for omicairn.steering_verify — Slice 3 warn-only verifiers."""
from __future__ import annotations

import pathlib
import time

import pytest

from omicairn import steering_verify
from omicairn.constants import Token
from omicairn.steering_verify import FAIL, PASS, UNRECOGNIZED


# ---------------------------------------------------------------------------
# mtime predicate
# ---------------------------------------------------------------------------

def test_mtime_pass_when_file_modified_after_start(tmp_path):
    started = time.time() - 100  # 100s ago
    f = tmp_path / "fig.png"
    f.write_text("x")  # mtime = now > started
    r = steering_verify.verify_predicate(
        "mtime(fig.png) > NOW", tmp_path, started
    )
    assert r.status == PASS


def test_mtime_glob_matches_some_paths(tmp_path):
    started = time.time() - 100
    (tmp_path / "figures").mkdir()
    (tmp_path / "figures" / "fig3_a.png").write_text("a")
    (tmp_path / "figures" / "fig3_b.png").write_text("b")
    r = steering_verify.verify_predicate(
        "mtime(figures/fig3_*.png) > NOW", tmp_path, started
    )
    assert r.status == PASS
    assert "2/2" in r.detail


def test_mtime_fail_when_no_glob_matches(tmp_path):
    r = steering_verify.verify_predicate(
        "mtime(figures/missing_*.png) > NOW", tmp_path, time.time() - 100
    )
    assert r.status == FAIL
    assert "no path matched" in r.detail


def test_mtime_fail_when_paths_exist_but_were_not_touched(tmp_path):
    f = tmp_path / "old.png"
    f.write_text("x")
    # Started "in the future" relative to f's mtime — nothing was touched after.
    started = time.time() + 100
    r = steering_verify.verify_predicate(
        "mtime(old.png) > NOW", tmp_path, started
    )
    assert r.status == FAIL


# ---------------------------------------------------------------------------
# exists / not exists
# ---------------------------------------------------------------------------

def test_exists_pass(tmp_path):
    (tmp_path / "report.md").write_text("x")
    r = steering_verify.verify_predicate(
        "exists(report.md)", tmp_path, 0.0
    )
    assert r.status == PASS


def test_exists_fail_when_path_missing(tmp_path):
    r = steering_verify.verify_predicate(
        "exists(report.md)", tmp_path, 0.0
    )
    assert r.status == FAIL


def test_not_exists_pass(tmp_path):
    r = steering_verify.verify_predicate(
        "not exists(scratch.txt)", tmp_path, 0.0
    )
    assert r.status == PASS


def test_not_exists_fail(tmp_path):
    (tmp_path / "scratch.txt").write_text("x")
    r = steering_verify.verify_predicate(
        "not exists(scratch.txt)", tmp_path, 0.0
    )
    assert r.status == FAIL


# ---------------------------------------------------------------------------
# contains
# ---------------------------------------------------------------------------

def test_contains_pass(tmp_path):
    (tmp_path / "report.md").write_text("preliminary evidence supports ...")
    r = steering_verify.verify_predicate(
        "contains(report.md, 'preliminary evidence')", tmp_path, 0.0
    )
    assert r.status == PASS


def test_contains_fail(tmp_path):
    (tmp_path / "report.md").write_text("definitive proof established")
    r = steering_verify.verify_predicate(
        "contains(report.md, 'preliminary evidence')", tmp_path, 0.0
    )
    assert r.status == FAIL


def test_not_contains_pass(tmp_path):
    (tmp_path / "report.md").write_text("the evidence shows ...")
    r = steering_verify.verify_predicate(
        "not contains(report.md, 'definitive proof')", tmp_path, 0.0
    )
    assert r.status == PASS


def test_not_contains_fail(tmp_path):
    (tmp_path / "report.md").write_text("the definitive proof is ...")
    r = steering_verify.verify_predicate(
        "not contains(report.md, 'definitive proof')", tmp_path, 0.0
    )
    assert r.status == FAIL


def test_contains_fails_when_path_missing(tmp_path):
    r = steering_verify.verify_predicate(
        "contains(missing.md, 'x')", tmp_path, 0.0
    )
    assert r.status == FAIL
    assert "does not exist" in r.detail


# ---------------------------------------------------------------------------
# Unknown predicates
# ---------------------------------------------------------------------------

def test_unknown_predicate_yields_unrecognized(tmp_path):
    r = steering_verify.verify_predicate(
        "size(fig.png) > 1MB", tmp_path, 0.0
    )
    assert r.status == UNRECOGNIZED
    assert "supported forms" in r.detail


def test_garbage_predicate_yields_unrecognized(tmp_path):
    r = steering_verify.verify_predicate("not a thing", tmp_path, 0.0)
    assert r.status == UNRECOGNIZED


# ---------------------------------------------------------------------------
# Aggregate API
# ---------------------------------------------------------------------------

def test_verify_expected_changes_runs_all(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    results = steering_verify.verify_expected_changes(
        ["exists(a.txt)", "exists(b.txt)", "garbage()"],
        tmp_path,
        0.0,
    )
    assert len(results) == 3
    assert results[0].status == PASS
    assert results[1].status == FAIL
    assert results[2].status == UNRECOGNIZED


def test_summarise_results_counts():
    results = [
        steering_verify.VerifyResult("p1", PASS, "ok"),
        steering_verify.VerifyResult("p2", FAIL, "no"),
        steering_verify.VerifyResult("p3", FAIL, "no"),
        steering_verify.VerifyResult("p4", UNRECOGNIZED, "?"),
    ]
    counts = steering_verify.summarise_results(results)
    assert counts == {PASS: 1, FAIL: 2, UNRECOGNIZED: 1}


# ---------------------------------------------------------------------------
# {{stage_dir}} token expansion (root cause 1)
# ---------------------------------------------------------------------------

def test_stage_dir_token_expands_and_passes(tmp_path):
    """A `{{stage_dir}}` predicate expands and PASSES against a fresh file."""
    stage_rel = "042_execute"
    (tmp_path / stage_rel / "lean").mkdir(parents=True)
    f = tmp_path / stage_rel / "lean" / "T1b_star_cauchy_schwarz.lean"
    f.write_text("theorem ...")
    token_values = {Token.STAGE_DIR: stage_rel}
    r = steering_verify.verify_predicate(
        "mtime({{stage_dir}}/lean/T1b_star_cauchy_schwarz.lean) > NOW",
        tmp_path,
        time.time() - 100,
        token_values,
    )
    assert r.status == PASS


def test_stage_dir_token_in_exists_and_contains(tmp_path):
    stage_rel = "042_execute"
    (tmp_path / stage_rel).mkdir(parents=True)
    (tmp_path / stage_rel / "results.json").write_text('{"dissociation_bound": 1}')
    tv = {Token.STAGE_DIR: stage_rel}
    r_ex = steering_verify.verify_predicate(
        "{{stage_dir}}/results.json exists", tmp_path, 0.0, tv
    )
    r_co = steering_verify.verify_predicate(
        "{{stage_dir}}/results.json contains 'dissociation_bound'", tmp_path, 0.0, tv
    )
    assert r_ex.status == PASS
    assert r_co.status == PASS


def test_unexpanded_token_is_unrecognized_not_silent_pass(tmp_path):
    """Without expansion, a `{{stage_dir}}` predicate is UNRECOGNIZED.

    The pre-fix bug let it glob a literal `{{stage_dir}}/...` path and
    report a misleading FAIL/PASS; now an unexpanded token is flagged.
    """
    r = steering_verify.verify_predicate(
        "exists({{stage_dir}}/results.json)", tmp_path, 0.0  # no token_values
    )
    assert r.status == UNRECOGNIZED
    assert "unexpanded template token" in r.detail


# ---------------------------------------------------------------------------
# Loose LLM-emitted forms (root cause 2)
# ---------------------------------------------------------------------------

def test_loose_exists_recognized(tmp_path):
    (tmp_path / "Metrics.lean").write_text("x")
    r = steering_verify.verify_predicate("Metrics.lean exists", tmp_path, 0.0)
    assert r.status == PASS


def test_loose_not_exists_recognized(tmp_path):
    r = steering_verify.verify_predicate("scratch.txt not exists", tmp_path, 0.0)
    assert r.status == PASS
    r2 = steering_verify.verify_predicate(
        "scratch.txt does not exist", tmp_path, 0.0
    )
    assert r2.status == PASS


def test_loose_contains_recognized(tmp_path):
    (tmp_path / "results.json").write_text("... sqrt_2_1mk ...")
    r = steering_verify.verify_predicate(
        "results.json contains 'sqrt_2_1mk'", tmp_path, 0.0
    )
    assert r.status == PASS


def test_loose_contains_fail_when_substr_absent(tmp_path):
    (tmp_path / "results.json").write_text("nothing here")
    r = steering_verify.verify_predicate(
        "results.json contains 'sqrt_2_1mk'", tmp_path, 0.0
    )
    assert r.status == FAIL


def test_natural_language_contains_still_unrecognized(tmp_path):
    """Unquoted natural-language 'contains a file matching ...' stays UNRECOGNIZED.

    The gate must still flag genuinely-unparseable garbage.
    """
    (tmp_path / "lean").mkdir()
    r = steering_verify.verify_predicate(
        "lean contains a file matching '*.verdict.json'", tmp_path, 0.0
    )
    assert r.status == UNRECOGNIZED


# ---------------------------------------------------------------------------
# Reproduce the exact d7 stage-042 case: pass >= 4 (was pass:0)
# ---------------------------------------------------------------------------

def test_d7_stage042_predicates_now_gate(tmp_path):
    """The 6 live d7 stage-042 predicates must yield pass>=4 (was pass:0).

    Before the fix: fail:2, unrecognized:4, pass:0 (tokens unexpanded +
    loose forms unrecognized). After: the 2 mtime + exists + 2 contains
    pass; only the natural-language predicate stays unrecognized.
    """
    stage_rel = "042_execute"
    lean = tmp_path / stage_rel / "lean"
    lean.mkdir(parents=True)
    (lean / "T1b_star_cauchy_schwarz.lean").write_text("theorem")
    (lean / "T2_star_no_free_binding.lean").write_text("theorem")
    (lean / "Metrics.lean").write_text("def")
    (lean / "Metrics.verdict.json").write_text("{}")
    (tmp_path / stage_rel / "results.json").write_text(
        '{"dissociation_bound": 1.4, "sqrt_2_1mk": 0.7}'
    )
    predicates = [
        "mtime({{stage_dir}}/lean/T1b_star_cauchy_schwarz.lean) > NOW",
        "mtime({{stage_dir}}/lean/T2_star_no_free_binding.lean) > NOW",
        "{{stage_dir}}/lean/Metrics.lean exists",
        "{{stage_dir}}/results.json contains 'dissociation_bound'",
        "{{stage_dir}}/results.json contains 'sqrt_2_1mk'",
        "{{stage_dir}}/lean contains a file matching '*.verdict.json'",
    ]
    results = steering_verify.verify_expected_changes(
        predicates, tmp_path, time.time() - 100, {Token.STAGE_DIR: stage_rel}
    )
    counts = steering_verify.summarise_results(results)
    assert counts[PASS] >= 4, counts
    assert counts[PASS] == 5  # 2 mtime + exists + 2 contains
    assert counts[UNRECOGNIZED] == 1  # the natural-language predicate
    assert counts[FAIL] == 0


# ---------------------------------------------------------------------------
# ABSOLUTE-path operands (regression: NotImplementedError from Path.glob)
# ---------------------------------------------------------------------------
#
# A steer plan whose predicate targets are ABSOLUTE paths must evaluate
# correctly, NOT crash. Before the fix, `_eval_mtime` passed an absolute
# pattern to `ws_root.glob(...)` which raises
# `NotImplementedError: Non-relative patterns are unsupported`, killing
# the orchestrator at post-stage verification (orphaning d1 + d6).


def test_absolute_mtime_glob_passes_instead_of_crashing(tmp_path):
    started = time.time() - 100
    lean = tmp_path / "knowledge" / "lean"
    lean.mkdir(parents=True)
    (lean / "alpha.lean").write_text("theorem")
    (lean / "beta.lean").write_text("theorem")
    abs_glob = str(lean / "*.lean")
    r = steering_verify.verify_predicate(f"mtime({abs_glob}) > NOW", tmp_path, started)
    assert r.status == PASS
    assert "2/2" in r.detail


def test_absolute_exists_passes(tmp_path):
    f = tmp_path / "knowledge" / "findings.md"
    f.parent.mkdir(parents=True)
    f.write_text("x")
    r = steering_verify.verify_predicate(f"exists({f})", tmp_path, 0.0)
    assert r.status == PASS


def test_absolute_contains_passes(tmp_path):
    f = tmp_path / "knowledge" / "verdict.json"
    f.parent.mkdir(parents=True)
    f.write_text('{"compiled": true}')
    r = steering_verify.verify_predicate(
        f"contains({f}, 'compiled')", tmp_path, 0.0
    )
    assert r.status == PASS


def test_relative_predicate_still_works_after_abs_fix(tmp_path):
    """Regression guard: the relative path remains the canonical case."""
    (tmp_path / "knowledge").mkdir()
    (tmp_path / "knowledge" / "f.lean").write_text("x")
    r = steering_verify.verify_predicate(
        "mtime(knowledge/f.lean) > NOW", tmp_path, time.time() - 100
    )
    assert r.status == PASS
    assert "1/1" in r.detail


def test_d1_d6_absolute_lean_glob_does_not_raise(tmp_path):
    """Reproduce the exact d1/d6 crash shape: an absolute `mtime(.../*.lean)
    > NOW` predicate evaluates (here to FAIL, no match) WITHOUT raising."""
    abs_glob = str(tmp_path / "workspaces" / "ws" / "knowledge" / "lean" / "*.lean")
    # No files exist → FAIL is the correct outcome; the point is no crash.
    r = steering_verify.verify_predicate(f"mtime({abs_glob}) > NOW", tmp_path, 0.0)
    assert r.status == FAIL
    assert "no path matched" in r.detail


def test_evaluator_exception_degrades_to_nonblocking_fail(tmp_path, monkeypatch):
    """Defense in depth: ANY unexpected evaluator error becomes a FAIL,
    never propagates — proving the verifier can't crash the orchestrator."""

    def _boom(p, ws_root, started_at_ts):
        raise RuntimeError("simulated evaluator explosion")

    monkeypatch.setitem(
        steering_verify._EVALUATORS, steering_verify.PredicateKind.EXISTS, _boom
    )
    # Must not raise:
    r = steering_verify.verify_predicate("exists(anything.txt)", tmp_path, 0.0)
    assert r.status == FAIL
    assert "RuntimeError" in r.detail
    assert "not propagated" in r.detail
