"""Unit tests for omicairn.cross_check.

These tests build minimal stage directories (not full workspaces) and
pass them directly to `check_execute`. The fixtures mirror the on-disk
layout of a real execute stage:

    tmp/
    └── 002_execute/
        ├── results.json
        ├── analysis.md
        ├── code/main.py
        └── figures/fig1_*.png
"""
from __future__ import annotations

import json
import pathlib
import tempfile

from omicairn.cross_check import (
    _BASELINE_NAME_HINTS_GENERIC,
    CrossCheckResult,
    _check_point_estimate_in_ci,
    _check_subsampling,
    _check_w15_latest_execute_provenance,
    _check_w21_prereg_completeness,
    _check_w23_decisive_arm_power,
    _check_w24_construct_validity,
    _check_w25_effect_clears_mde,
    _guess_primary_condition,
    _normalize_ci,
    _walk_ci95,
    baseline_name_hints,
    build_execute_verdict,
    check_execute,
    ci_skip_patterns,
    normalize_results_json,
)
from omicairn.constants import (
    ConstructValidityGate,
    Decision,
    DecisiveArmPowerGate,
    EffectClearsMdeGate,
    LatestExecuteProvenanceGate,
    PreregCompletenessGate,
    PreregField,
    ScopedExitCorroborationGate,
)


# W2/W5 are opt-in per domain pack. The survival_prognostic pack enables
# the full biomedical patch set; pass this as `domain=` to any
# `check_execute` call that wants to exercise those patches.
_W_DOMAIN: str = "survival_prognostic"

# Guard: if the pack is renamed/removed, resolve_pack would silently
# fall back to `_generic` (which has all W-checks disabled) and every
# W-test below would pass trivially. Fail loud at import time instead.
from omicairn.cross_check import enabled_execute_checks
assert {"w2", "w5"} <= enabled_execute_checks(_W_DOMAIN), (
    f"{_W_DOMAIN} pack is missing execute W-check opt-ins; "
    f"W-tests would silently become no-ops"
)


def _make_execute_stage(
    tmp: pathlib.Path,
    results: dict,
    analysis: str,
    *,
    code_files=None,
    figures=None,
    skip_env_lockfile: bool = False,
    proof_obligations=None,
) -> pathlib.Path:
    """Build a minimal execute stage directory at tmp/002_execute.

    Tier 1.3 added an env-lockfile coverage check that warns when
    code/environment.lock.json is absent. To keep legacy fixtures
    "clean" by default, the helper writes a benign lockfile that
    matches the stub `code/main.py`'s zero imports. Tests that want
    to exercise the missing-lockfile path pass ``skip_env_lockfile=True``.
    """
    stage = tmp / "002_execute"
    (stage / "code").mkdir(parents=True, exist_ok=True)
    (stage / "results.json").write_text(json.dumps(results))
    (stage / "analysis.md").write_text(analysis)
    for name in code_files or ("main.py",):
        (stage / "code" / name).write_text("# stub\n")
    if figures:
        (stage / "figures").mkdir(parents=True, exist_ok=True)
        for name in figures:
            (stage / "figures" / name).write_bytes(b"\x89PNG")
    if proof_obligations is not None:
        (stage / "proof_obligations.json").write_text(
            json.dumps(proof_obligations)
        )
    if not skip_env_lockfile:
        # Empty lockfile (no imports) for fixtures whose stub code has
        # no external imports. The check passes trivially.
        (stage / "code" / "environment.lock.json").write_text(
            json.dumps({
                "version": 1,
                "captured_at": "fixture",
                "python_version": "3.11",
                "platform": "fixture",
                "executable": "fixture",
                "packages": [],
                "imported_modules": [],
            })
        )
    return stage


def test_execute_missing_comparison_ci_is_error():
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {
            "primary_model": {"auc": 0.82, "ci_95": [0.677, 0.943]},
            "comparisons": {
                "delta_primary_vs_null": {"delta": 0.37, "ci_95": [0.141, 0.594]},
            },
        }
        analysis = "AUC 0.82 (CI [0.677, 0.943])\n"  # delta CI absent
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert not r.ok
        assert any("delta_primary_vs_null" in e for e in r.errors)


def test_execute_all_cis_present_is_ok():
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {
            "primary_model": {"auc": 0.82, "ci_95": [0.677, 0.943]},
            "comparisons": {
                "delta_primary_vs_null": {"delta": 0.37, "ci_95": [0.141, 0.594]},
            },
        }
        analysis = (
            "AUC 0.82 (CI [0.677, 0.943])\n"
            "Primary vs null ΔAUC=0.37 CI [0.141, 0.594]\n"
        )
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert r.ok, r.summary()


def test_execute_unicode_minus_ci_is_accepted():
    """analysis.md uses U+2212 MINUS SIGN (typographic convention) for the
    negative CI bound; cross_check must treat it as equivalent to ASCII
    hyphen-minus. Reproduces the morphogen-organoid-screen false error."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {
            "primary_model": {
                "mean_delta": 0.532,
                "ci_95": [-0.060, 0.956],
            },
            "baselines": {
                "perm_nim_labels": {"mean_delta": 0.0, "ci_95": [-0.80, 0.79]},
            },
            "comparisons": {
                "delta_primary_vs_perm_nim_labels": {
                    "delta": 0.532,
                    "ci_95": [-0.060, 0.956],
                    "p_directional": 0.035,
                },
            },
        }
        # Note: all minus signs below are U+2212 MINUS SIGN, not U+002D
        # HYPHEN-MINUS. Copy-paste from real analysis.md prose.
        analysis = (
            "| delta_primary_vs_perm_nim_labels | +0.532 | [−0.060, +0.956] | 0.035 |\n"
            "Paired Δ vs perm_nim_labels = +0.532 (95 % CI [−0.060, +0.956]).\n"
            "Baseline perm_nim_labels CI = [−0.80, +0.79].\n"
        )
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert r.ok, r.summary()
        assert not any("not fully present" in e for e in r.errors)


def test_execute_recomputed_ci_is_flagged():
    """Analyst wrote [-0.104, 0.407] but results.json says [0.035, 0.291]."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {
            "comparisons": {
                "delta_primary_vs_single_feature": {
                    "delta": 0.155,
                    "ci_95": [0.035, 0.291],
                },
            },
        }
        analysis = (
            "Primary vs single-feature ΔAUC=+0.155 CI [−0.104, +0.407]\n"
            "(CI crosses zero, consistent with small n)\n"
        )
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert not r.ok
        assert any("delta_primary_vs_single_feature" in e for e in r.errors)


def test_execute_preregistered_range_not_flagged():
    """H3 range [0.70, 0.95] is a pre-registered diagnostic range, not a CI."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {"primary_model": {"auc": 0.82, "ci_95": [0.677, 0.943]}}
        analysis = (
            "AUC 0.820 (CI [0.677, 0.943]).\n"
            "H3: AUC must fall in pre-registered range [0.70, 0.95]. Passed.\n"
        )
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert r.ok, f"expected ok, got errors={r.errors}"


def test_execute_sibling_scalar_ci_schema():
    """Handle the alternate schema with ci_lower_95/ci_upper_95 scalars."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {
            "conditions": {
                "full_model": {
                    "auc": 0.82,
                    "ci_lower_95": 0.677,
                    "ci_upper_95": 0.943,
                }
            }
        }
        analysis = "Full model AUC=0.82 (CI [0.677, 0.943])\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert r.ok, r.summary()


def test_execute_baselines_without_paired_delta_is_error():
    """If `baselines` is present, `comparisons.*` with a CI is required."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {
            "primary_model": {"auc": 0.82, "ci_95": [0.677, 0.943]},
            "baselines": {
                "random_null": {"auc": 0.5, "ci_95": [0.5, 0.5]}
            },
        }
        analysis = "AUC 0.82 CI [0.677, 0.943]; baseline 0.5 CI [0.5, 0.5]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert not r.ok
        assert any("paired" in e.lower() for e in r.errors)


def test_normalize_conditions_to_canonical():
    """Legacy `conditions.*` form should map to primary_model + baselines.*."""
    raw = {
        "conditions": {
            "full_model": {
                "auc": 0.82,
                "ci_lower_95": 0.677,
                "ci_upper_95": 0.943,
            },
            "single_feature_baseline": {
                "auc": 0.66,
                "ci_lower_95": 0.49,
                "ci_upper_95": 0.83,
            },
            "random_null": {
                "auc": 0.5,
                "ci_lower_95": 0.5,
                "ci_upper_95": 0.5,
            },
        }
    }
    out = normalize_results_json(raw)
    assert "primary_model" in out
    assert out["primary_model"]["ci_95"] == [0.677, 0.943]
    assert "baselines" in out
    assert "single_feature_baseline" in out["baselines"]
    assert "random_null" in out["baselines"]
    assert out["baselines"]["random_null"]["ci_95"] == [0.5, 0.5]
    assert "conditions" not in out


def test_normalize_canonical_is_idempotent():
    canonical = {
        "primary_model": {"auc": 0.8, "ci_95": [0.7, 0.9]},
        "baselines": {"null": {"auc": 0.5, "ci_95": [0.4, 0.6]}},
        "comparisons": {
            "delta_primary_vs_null": {"delta": 0.3, "ci_95": [0.15, 0.45]},
        },
    }
    out = normalize_results_json(canonical)
    assert out["primary_model"]["ci_95"] == [0.7, 0.9]
    assert out["baselines"]["null"]["ci_95"] == [0.4, 0.6]
    assert out["comparisons"]["delta_primary_vs_null"]["ci_95"] == [0.15, 0.45]


def test_execute_d4_iter2_shape_is_error():
    """Legacy shape + no comparisons should still flag missing paired delta."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {
            "conditions": {
                "full_model": {
                    "auc": 0.8271,
                    "ci_lower_95": 0.6826,
                    "ci_upper_95": 0.9495,
                    "permutation_p_one_sided": 0.0001,
                    "permutation_B": 10000,
                },
                "single_feature_baseline": {
                    "auc": 0.6642,
                    "ci_lower_95": 0.4875,
                    "ci_upper_95": 0.8296,
                },
                "random_null": {
                    "auc": 0.5,
                    "ci_lower_95": 0.5,
                    "ci_upper_95": 0.5,
                },
            },
        }
        analysis = (
            "Full model AUC=0.8271 CI [0.6826, 0.9495]\n"
            "Single feature AUC=0.6642 CI [0.4875, 0.8296]\n"
            "Random null AUC=0.5 CI [0.5, 0.5]\n"
        )
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert not r.ok
        assert any("paired" in e.lower() for e in r.errors), r.errors
        assert any(
            "legacy" in w.lower() or "canonical" in w.lower()
            for w in r.warnings
        ), r.warnings


def test_execute_d4_iter2_shape_with_comparisons_is_ok():
    """Same legacy shape but WITH a comparisons block should pass."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {
            "conditions": {
                "full_model": {
                    "auc": 0.8271,
                    "ci_lower_95": 0.6826,
                    "ci_upper_95": 0.9495,
                },
                "single_feature_baseline": {
                    "auc": 0.6642,
                    "ci_lower_95": 0.4875,
                    "ci_upper_95": 0.8296,
                },
            },
            "comparisons": {
                "delta_full_vs_single_feature": {
                    "delta": 0.1629,
                    "ci_95": [0.035, 0.291],
                }
            },
        }
        analysis = (
            "Full model AUC=0.8271 CI [0.6826, 0.9495]\n"
            "Single feature AUC=0.6642 CI [0.4875, 0.8296]\n"
            "Delta=0.1629 CI [0.035, 0.291]\n"
        )
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert r.ok, r.summary()
        assert any(
            "legacy" in w.lower() or "canonical" in w.lower()
            for w in r.warnings
        )


def _write_plan_contract(tmp: pathlib.Path, contract: dict) -> pathlib.Path:
    plan = tmp / "001_plan"
    plan.mkdir(parents=True, exist_ok=True)
    (plan / "figures_contract.json").write_text(json.dumps(contract))
    return plan


def test_figures_contract_missing_file_is_warning_not_error():
    """A planned figure absent from the execute stage is surfaced as a
    WARNING so REVIEW can judge whether it's a disclosed limitation
    or a producible-but-skipped gap worth retrying. Cross_check does
    not flip the verdict to retry_execute on its own.
    """
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_contract(tmp, {
            "figures": [{
                "id": "F4",
                "required": True,
                "output_paths": ["figures/fig4_panelC_nomogram.pdf"],
                "results_tables": ["results/nomogram_coxph_summary.csv"],
            }]
        })
        results = {"primary_model": {"auc": 0.82, "ci_95": [0.67, 0.94]}}
        analysis = "AUC 0.82 (CI [0.67, 0.94])\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert r.ok, r.summary()
        assert not any("fig4_panelC_nomogram.pdf" in e for e in r.errors)
        assert any("fig4_panelC_nomogram.pdf" in w for w in r.warnings)
        assert any("nomogram_coxph_summary.csv" in w for w in r.warnings)


def test_figures_contract_satisfied_is_ok():
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_contract(tmp, {
            "figures": [{
                "id": "F4",
                "required": True,
                "output_paths": ["figures/fig4_panelC_nomogram.pdf"],
                "results_tables": ["results/nomogram.csv"],
            }]
        })
        results = {"primary_model": {"auc": 0.82, "ci_95": [0.67, 0.94]}}
        analysis = "AUC 0.82 (CI [0.67, 0.94])\n"
        stage = _make_execute_stage(
            tmp, results, analysis,
            figures=["fig4_panelC_nomogram.pdf"],
        )
        (stage / "results").mkdir(parents=True, exist_ok=True)
        (stage / "results" / "nomogram.csv").write_text("a,b\n1,2\n")
        r = check_execute(stage)
        assert r.ok, r.summary()


def test_figures_contract_non_required_missing_is_silent():
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_contract(tmp, {
            "figures": [{
                "id": "F7",
                "required": False,
                "output_paths": ["figures/fig7_scrna.pdf"],
                "results_tables": [],
            }]
        })
        results = {"primary_model": {"auc": 0.82, "ci_95": [0.67, 0.94]}}
        analysis = "AUC 0.82 (CI [0.67, 0.94])\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert not any("fig7_scrna.pdf" in e for e in r.errors)


def test_figures_contract_absent_is_silent():
    """Workspaces without a plan sibling or contract skip silently."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {"primary_model": {"auc": 0.82, "ci_95": [0.67, 0.94]}}
        analysis = "AUC 0.82 (CI [0.67, 0.94])\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert r.ok, r.summary()


def test_data_fetch_log_failure_is_warning_not_error():
    """A recorded fetch failure (HTTP 5xx, FAILED, exception) is surfaced
    as an informational warning; the verdict stays ok so REVIEW can
    judge whether analysis.md § Limitations discloses the gap.
    """
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {"primary_model": {"auc": 0.82, "ci_95": [0.67, 0.94]}}
        analysis = "AUC 0.82 (CI [0.67, 0.94])\n"
        stage = _make_execute_stage(tmp, results, analysis)
        (stage / "data_fetch_log.md").write_text(
            "# Data fetch log\n"
            "- GSE176078: https://host/x.h5 → datasets/GSE176078/x.h5 (1234 B, "
            "sha256=abc, wall=3s, 2026-04-14T10:00Z)\n"
            "- GSE161529: FAILED http_status=503 attempts=3\n"
        )
        r = check_execute(stage)
        assert r.ok, r.summary()
        assert any("failed fetch" in w and "data_fetch_log.md:3" in w
                   for w in r.warnings), r.warnings


def test_data_fetch_log_success_only_is_silent():
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {"primary_model": {"auc": 0.82, "ci_95": [0.67, 0.94]}}
        analysis = "AUC 0.82 (CI [0.67, 0.94])\n"
        stage = _make_execute_stage(tmp, results, analysis)
        (stage / "data_fetch_log.md").write_text(
            "# Data fetch log\n"
            "- GSE1: https://host/a → datasets/GSE1/a (10 B, "
            "sha256=x, wall=1s, 2026-04-14T10:00Z)\n"
        )
        r = check_execute(stage)
        assert r.ok, r.summary()
        assert not any("failed fetch" in w for w in r.warnings)


def test_data_fetch_log_absent_is_silent():
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {"primary_model": {"auc": 0.82, "ci_95": [0.67, 0.94]}}
        analysis = "AUC 0.82 (CI [0.67, 0.94])\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert r.ok, r.summary()
        assert not any("data_fetch_log" in w for w in r.warnings)


def test_generic_baseline_hints_have_no_biomedical_tokens():
    """Generic defaults must not carry field-specific tokens."""
    biomedical = {"grade_only", "stage_only", "age_only", "tnm"}
    for tok in _BASELINE_NAME_HINTS_GENERIC:
        assert tok not in biomedical, (
            f"generic baseline hint {tok!r} is biomedical-specific; "
            "move to domain_packs/<pack>/profile.yaml"
        )


def test_baseline_name_hints_merges_domain_pack():
    """survival_prognostic profile should contribute grade_only + stage_only."""
    hints = baseline_name_hints("survival")
    assert "grade_only" in hints
    assert "stage_only" in hints
    # Generic hints are still present
    assert "null" in hints
    assert "random" in hints


def test_baseline_name_hints_generic_domain_excludes_biomedical():
    hints = baseline_name_hints("generic")
    assert "grade_only" not in hints
    assert "null" in hints


def test_ci_skip_patterns_merges_domain_pack():
    subs, _rx = ci_skip_patterns("survival")
    assert "multivariate_cox.variables" in subs
    assert "univariate" in subs  # still has the generic


def test_ci_skip_patterns_generic_has_no_cox_hook():
    subs, _rx = ci_skip_patterns("generic")
    assert "multivariate_cox.variables" not in subs
    assert "univariate" in subs


# ---------------------------------------------------------------------------
# CrossCheckResult.summary() — formatting the human-readable status string
# ---------------------------------------------------------------------------

def test_crosscheck_result_summary_ok_when_clean():
    r = CrossCheckResult(ok=True, warnings=[], errors=[])
    assert r.summary() == "ok"


def test_crosscheck_result_summary_errors_only():
    r = CrossCheckResult(
        ok=False, warnings=[], errors=["e1", "e2"]
    )
    s = r.summary()
    assert s.startswith("ERRORS:")
    assert "e1" in s and "e2" in s
    assert "|" in s  # pipe-separator between errors


def test_crosscheck_result_summary_warnings_only():
    r = CrossCheckResult(
        ok=True, warnings=["w1"], errors=[]
    )
    assert r.summary() == "WARNINGS: w1"


def test_crosscheck_result_summary_errors_and_warnings():
    """Errors come first (more urgent); warnings second; joined by '; '."""
    r = CrossCheckResult(
        ok=False, warnings=["w1"], errors=["e1"]
    )
    s = r.summary()
    assert "ERRORS:" in s
    assert "WARNINGS:" in s
    assert s.index("ERRORS:") < s.index("WARNINGS:")


# ---------------------------------------------------------------------------
# check_execute — defensive branches for missing/malformed inputs
# ---------------------------------------------------------------------------

def test_check_execute_missing_results_json_returns_trivially_ok():
    """If results.json / analysis.md are absent, contracts.py will have
    already reported a failure. cross_check must NOT double-report —
    it returns a trivially-ok result so the stage isn't flagged twice."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        stage = tmp / "002_execute"
        stage.mkdir()
        # No results.json, no analysis.md.
        r = check_execute(stage)
        assert r.ok
        assert not r.errors
        assert not r.warnings


def test_check_execute_malformed_results_json_reports_error():
    """Unparseable results.json must produce a concrete error mentioning
    the filename, not crash. _run_cross_check_stage depends on getting
    a CrossCheckResult back — a raised exception would halt the whole
    pipeline instead of emitting a retry verdict."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        stage = tmp / "002_execute"
        stage.mkdir()
        (stage / "results.json").write_text("{ not valid json ,")
        (stage / "analysis.md").write_text("Placeholder analysis.\n")
        r = check_execute(stage)
        assert not r.ok
        assert any("results.json" in e for e in r.errors)
        assert any("parse" in e.lower() for e in r.errors)


# ---------------------------------------------------------------------------
# build_execute_verdict — the function the orchestrator actually calls
# ---------------------------------------------------------------------------
#
# This helper wraps check_execute and converts the CrossCheckResult into
# a verdict_mod.Verdict. It had zero direct tests — the orchestrator
# dry-run path bypasses it, and dry-run always writes a verdict.json
# stub rather than calling this builder. A regression that returned
# the wrong Decision (or wrong phase in the retry) would only surface
# at runtime the first time a real execute stage produced an error.

def test_build_execute_verdict_clean_returns_proceed():
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {
            "primary_model": {"auc": 0.82, "ci_95": [0.677, 0.943]},
            "comparisons": {
                "delta_primary_vs_null": {
                    "delta": 0.37, "ci_95": [0.141, 0.594]
                },
            },
        }
        analysis = (
            "AUC 0.82 (CI [0.677, 0.943])\n"
            "Primary vs null ΔAUC=0.37 CI [0.141, 0.594]\n"
        )
        stage = _make_execute_stage(tmp, results, analysis)
        v, r = build_execute_verdict(stage)
        assert r.ok
        assert v.decision == Decision.PROCEED
        assert "clean" in (v.reason or "").lower()


def test_build_execute_verdict_warnings_folds_into_proceed_reason():
    """Warnings-only result still returns a proceed verdict, but the
    reason must reference the warning count and the warnings must be
    attached as evidence so review can see them later."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        # Legacy `conditions.*` shape with paired comparison → warning
        # about legacy shape, but still ok.
        results = {
            "conditions": {
                "full_model": {"auc": 0.82, "ci_lower_95": 0.677, "ci_upper_95": 0.943},
                "single_feature_baseline": {"auc": 0.66, "ci_lower_95": 0.49, "ci_upper_95": 0.83},
            },
            "comparisons": {
                "delta_full_vs_single_feature": {"delta": 0.16, "ci_95": [0.035, 0.291]},
            },
        }
        analysis = (
            "Full model AUC=0.82 CI [0.677, 0.943]\n"
            "Single feature AUC=0.66 CI [0.49, 0.83]\n"
            "Delta=0.16 CI [0.035, 0.291]\n"
        )
        stage = _make_execute_stage(tmp, results, analysis)
        v, r = build_execute_verdict(stage)
        assert r.ok
        assert v.decision == Decision.PROCEED
        # Warning count surfaced in the reason.
        assert "warning" in (v.reason or "").lower()
        # Warnings attached as evidence so a downstream reader can see
        # what triggered them.
        assert v.evidence
        assert any("legacy" in e.lower() or "canonical" in e.lower() for e in v.evidence)


def test_build_execute_verdict_errors_returns_retry_execute():
    """On errors, must return a retry_execute verdict whose evidence
    lists every underlying error. The orchestrator then routes this
    back to the execute phase instead of proceeding to review."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        # Baselines without comparisons → error.
        results = {
            "primary_model": {"auc": 0.82, "ci_95": [0.677, 0.943]},
            "baselines": {
                "random_null": {"auc": 0.5, "ci_95": [0.5, 0.5]}
            },
        }
        analysis = "AUC 0.82 CI [0.677, 0.943]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        v, r = build_execute_verdict(stage)
        assert not r.ok
        # retry(phase=EXECUTE) → Decision.RETRY_EXECUTE
        assert v.decision == Decision.RETRY_EXECUTE
        # Evidence must carry the underlying errors — otherwise the
        # retry message is useless to the execute phase retrying.
        assert v.evidence
        assert any("paired" in e.lower() for e in v.evidence)
        # The fix hint must reference the canonical schema so the
        # retrying executor knows what to change.
        assert v.proposed_fix
        assert (
            "canonical" in v.proposed_fix.lower()
            or "primary_model" in v.proposed_fix.lower()
        )


# ---------------------------------------------------------------------------
# _walk_ci95 — NaN/Inf skipping and list-branch traversal
# ---------------------------------------------------------------------------

def test_check_execute_nan_ci_does_not_raise_or_flag():
    """NaN bounds mark a deliberately-skipped analysis; they must be
    walked past silently so their placeholder values never get flagged
    as missing from analysis.md."""
    import math
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {
            "primary_model": {"auc": 0.82, "ci_95": [0.677, 0.943]},
            "comparisons": {
                "delta_skipped": {
                    "delta": float("nan"),
                    # Use string→float so json can serialize; NaN isn't
                    # valid JSON, so emit the values as plain nulls in
                    # a sibling dict that `_walk_ci95` will just skip.
                    "ci_95": [0.0, 0.0],
                },
                "delta_primary_vs_null": {
                    "delta": 0.37, "ci_95": [0.141, 0.594]
                },
            },
        }
        # Explicitly blow out the ci_95 in delta_skipped to contain
        # a NaN via Python-level mutation after JSON decode, so the
        # skip branch fires.
        import json as _json
        stage = tmp / "002_execute"
        (stage / "code").mkdir(parents=True)
        (stage / "results.json").write_text(
            # JSON does not natively support NaN, but json.loads
            # with default=parse_constant accepts it. Write with
            # allow_nan=True.
            _json.dumps(
                {
                    **results,
                    "comparisons": {
                        "delta_skipped": {
                            "delta": None, "ci_95": [float("nan"), float("nan")],
                        },
                        "delta_primary_vs_null": {
                            "delta": 0.37, "ci_95": [0.141, 0.594],
                        },
                    },
                },
                allow_nan=True,
            )
        )
        (stage / "analysis.md").write_text(
            "Primary AUC 0.82 CI [0.677, 0.943]\n"
            "Primary vs null Delta=0.37 CI [0.141, 0.594]\n"
        )
        # No code file needed for check_execute — only results.json
        # and analysis.md are read.
        r = check_execute(stage)
        # The NaN CI must NOT surface as an error: the real delta CI
        # is present in analysis.md and the NaN one is skipped.
        assert r.ok, f"expected ok, got errors={r.errors}, warnings={r.warnings}"
        assert not any("nan" in e.lower() for e in r.errors)


def test_check_execute_handles_list_of_ci_blocks():
    """results.json with an array of CI entries (e.g. per-fold results)
    must be walked without crashing. Covers the list-branch in
    _walk_ci95."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {
            "primary_model": {"auc": 0.82, "ci_95": [0.677, 0.943]},
            "comparisons": {
                "delta_primary_vs_null": {
                    "delta": 0.37, "ci_95": [0.141, 0.594]
                },
            },
            "per_fold": [
                {"fold": 0, "ci_95": [0.600, 0.900]},
                {"fold": 1, "ci_95": [0.620, 0.910]},
            ],
        }
        analysis = (
            "Primary AUC 0.82 CI [0.677, 0.943]\n"
            "Delta=0.37 CI [0.141, 0.594]\n"
            "Per-fold: CI [0.600, 0.900] and CI [0.620, 0.910]\n"
        )
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        # Per-fold CIs are primary_model/baselines bucket (non-comparison),
        # so if they were missing they'd be warnings, not errors. But
        # we wrote them into analysis.md, so they should be clean.
        assert r.ok, r.summary()


# ---------------------------------------------------------------------------
# Low-level helpers: _guess_primary_condition, _normalize_ci,
# _walk_ci95, and the more exotic branches of normalize_results_json /
# check_execute. These cover the ~37 lines that were uncovered because
# the higher-level tests only exercise the happy paths.
# ---------------------------------------------------------------------------

def test_guess_primary_condition_empty_conditions_returns_none():
    """Line 127: empty dict → None, not KeyError."""
    assert _guess_primary_condition({}) is None


def test_guess_primary_condition_picks_role_primary_block():
    """Line 129-130: explicit role=primary wins even if the name
    doesn't contain 'primary'."""
    out = _guess_primary_condition({
        "cox_full": {"role": "primary", "ci_95": [0.1, 0.2]},
        "cox_null": {"ci_95": [0.0, 0.1]},
    })
    assert out == "cox_full"


def test_guess_primary_condition_falls_back_to_non_baseline_name():
    """Lines 135-138: no role=primary, no name containing
    'primary'/'full_model'/'full' — the function must return the
    first name that is not recognised as a baseline."""
    out = _guess_primary_condition({
        "null_model": {"ci_95": [0.0, 0.1]},
        "my_custom_cox": {"ci_95": [0.1, 0.2]},
    })
    assert out == "my_custom_cox"


def test_guess_primary_condition_all_baselines_returns_none():
    """Line 138: if every name is a baseline, fall through to
    `return None`."""
    out = _guess_primary_condition({
        "null_model": {"ci_95": [0.0, 0.1]},
        "baseline": {"ci_95": [0.0, 0.1]},
    })
    assert out is None


def test_normalize_ci_passes_through_non_dict_block():
    """Line 152-153: _normalize_ci(list) must return the same list
    unchanged (not crash on .get())."""
    assert _normalize_ci([1, 2, 3]) == [1, 2, 3]
    assert _normalize_ci("scalar") == "scalar"
    assert _normalize_ci(None) is None


def test_normalize_ci_survives_non_numeric_ci_bounds():
    """Lines 158-159: ci_lower_95/ci_upper_95 that can't be float()-ed
    must leave ci_95 unset rather than raise."""
    out = _normalize_ci({
        "ci_lower_95": "N/A",
        "ci_upper_95": "N/A",
        "other": 1,
    })
    # No ci_95 produced, but the other fields are preserved.
    assert "ci_95" not in out
    assert out["other"] == 1


def test_normalize_results_json_non_dict_input_returned_as_is():
    """Line 173-174: non-dict input short-circuits. normalize is a
    no-op on lists, strings, None."""
    assert normalize_results_json([1, 2, 3]) == [1, 2, 3]
    assert normalize_results_json("not a dict") == "not a dict"
    assert normalize_results_json(None) is None


def test_normalize_results_json_skips_non_dict_conditions_block():
    """Line 186-187: a legacy conditions dict containing a non-dict
    value must skip that entry rather than crash. Only the real
    block becomes primary_model."""
    out = normalize_results_json({
        "conditions": {
            "real": {"role": "primary", "auc": 0.8},
            "bogus": "this is a string not a dict",
        }
    })
    assert out["primary_model"]["auc"] == 0.8
    # The bogus entry must not appear in baselines.
    assert "bogus" not in out.get("baselines", {})


def test_walk_ci95_skips_non_numeric_ci_values():
    """Lines 261-262: a ci_95 field whose values can't be float()-ed
    is skipped, not treated as a 0-wide bound."""
    out = _walk_ci95({
        "primary": {"ci_95": ["N/A", "N/A"]},
        "other": {"ci_95": [0.1, 0.2]},
    })
    # Only the numeric one survives.
    assert len(out) == 1
    assert out[0][1] == 0.1
    assert out[0][2] == 0.2


def test_walk_ci95_substring_skip_univariate():
    """Line 265-267: paths whose lowercased form contains a generic
    skip substring (e.g. 'univariate') are excluded from the walk."""
    out = _walk_ci95({
        "univariate_results": {
            "gene_a": {"ci_95": [0.1, 0.2]},
            "gene_b": {"ci_95": [0.3, 0.4]},
        },
        "multivariate": {"ci_95": [0.5, 0.6]},
    })
    paths = [p for p, _, _ in out]
    # univariate branch excluded, multivariate kept.
    assert "multivariate" in paths
    assert not any("univariate" in p for p in paths)


def test_walk_ci95_regex_skip_h2_heritability():
    """Line 268-269: the generic h2 regex skip excludes heritability
    CIs like h2.beta, h2_add, h2[trait]. Pins the regex branch."""
    out = _walk_ci95({
        "h2": {
            "beta": {"ci_95": [0.1, 0.2]},
        },
        "real": {"ci_95": [0.3, 0.4]},
    })
    paths = [p for p, _, _ in out]
    assert "real" in paths
    assert not any(p.startswith("h2") for p in paths)


def test_check_execute_early_return_when_no_bounds():
    """Line 380-381: a results.json with no CI bounds at all produces
    an early-return CrossCheckResult. The function still runs the
    baselines/comparisons check, and if there are no baselines
    either, the result is clean (ok=True)."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {
            "primary_model": {"auc": 0.82},  # no ci_95
        }
        analysis = "AUC was 0.82."
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert r.ok, r.summary()
        assert r.errors == []


def test_check_execute_skips_non_dict_comparisons_entry():
    """Line 361-362: a comparisons dict containing a non-dict value
    must skip that entry rather than crash when looking up
    `block.get("ci_95")`. Also pins that the baselines-without-delta
    error still fires for the malformed entry."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {
            "primary_model": {"auc": 0.82, "ci_95": [0.677, 0.943]},
            "baselines": {"null_model": {"auc": 0.50, "ci_95": [0.40, 0.60]}},
            "comparisons": {
                "delta": "not a dict — must be skipped",
            },
        }
        analysis = "Primary AUC 0.82 [0.677, 0.943]; baseline 0.50 [0.40, 0.60]."
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        # The non-dict comparison entry is skipped, so has_paired_ci
        # stays False, which fires the baselines-without-delta error.
        # This pins BOTH the skip (line 362) AND the downstream error
        # branch, in one test.
        assert not r.ok
        assert any("paired" in e.lower() or "delta" in e.lower()
                   for e in r.errors)


def test_check_execute_rounded_2dp_ci_is_tolerated():
    """Line 415-416: an analysis.md CI that matches at 2-decimal
    precision (rounded) must be treated as clean, not flagged as
    'recomputed'. This pins the rounded_2 in results_ci_set branch."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        # CI in results is [0.677345, 0.943128] → rounds to
        # [0.68, 0.94] at 2 dp.
        results = {
            "primary_model": {
                "auc": 0.82,
                "ci_95": [0.677345, 0.943128],
            },
            "comparisons": {
                "delta": {"ci_95": [0.141, 0.594]},
            },
        }
        # Analysis writes the 2-dp rounded form.
        analysis = (
            "Primary AUC 0.82 CI [0.68, 0.94]. "
            "Delta CI [0.141, 0.594]."
        )
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        # The primary CI is expressed at 2dp — this must be accepted
        # via the rounded_2 branch, not flagged as "recomputed".
        assert not any(
            "near but" in w for w in r.warnings
        ), f"unexpected near-but warnings: {r.warnings}"


def test_check_execute_flags_near_but_not_equal_ci():
    """Lines 419-435: the near-but-not-equal fuzzy match. An
    analysis.md CI that is close to (but doesn't exactly match at
    3-dp or 2-dp) a results.json CI — yet matches at 1-dp, isn't
    substring-matched, and isn't a "round" value — fires a
    `near but NOT equal` warning. This pins the warning generator.

    Hitting this path needs carefully-chosen numbers. With
    results [0.712, 0.889] and analysis [0.73, 0.87]:
        - 3dp rounded (0.73, 0.87) != results 3dp (0.712, 0.889)
        - 2dp rounded (0.73, 0.87) != results 2dp (0.71, 0.89)
        - neither bound is a 0.05-multiple (not "round")
        - 1dp rounded (0.7, 0.9) == results 1dp (0.7, 0.9) ✓
        - substring check fails (no shared 2-3 decimal prefix)
        - nearest is the primary_model CI by L1 distance
    """
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {
            "primary_model": {"auc": 0.80, "ci_95": [0.712, 0.889]},
            "comparisons": {
                "delta": {"ci_95": [0.141, 0.594]},
            },
        }
        analysis = (
            "Primary AUC 0.80 CI [0.73, 0.87]. "
            "Delta CI [0.141, 0.594]."
        )
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        joined = " ".join(r.warnings).lower()
        assert "near but" in joined and "not equal" in joined, r.warnings


# ---------------------------------------------------------------------------
# Fuzzy-match fall-through branches inside check_execute (cross_check.py
# L420 / L427-428 / L430). These are the silent "don't flag this CI"
# paths — a warning here would be very wrong because it would nag the
# analyst about something that's actually fine, and a regression would
# spam every run with false positives.
# ---------------------------------------------------------------------------

def test_execute_unrelated_ci_at_1dp_miss_is_not_flagged():
    """L420: if an analysis-md CI fails to match any results.json CI
    at even 1-decimal-place rounding, it's almost certainly a *different
    metric's* CI (e.g. an exploratory table the analyst added in prose).
    The fuzzy matcher must skip it silently rather than emit a
    `near-but-not-equal` warning.

    Setup: results.json CI is [0.700, 0.910]; analysis.md contains an
    unrelated CI [0.234, 0.567] somewhere. rounded_1 = (0.2, 0.6) is
    not in results_ci_set_1dp = {(0.7, 0.9)}, so the
    `if rounded_1 not in results_ci_set_1dp: continue` guard fires.
    The whole fuzzy loop produces neither warnings nor errors."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {"primary_model": {"auc": 0.82, "ci_95": [0.700, 0.910]}}
        analysis = (
            "Primary AUC 0.82 CI [0.700, 0.910].\n"
            "Unrelated exploratory CI [0.234, 0.567].\n"
        )
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        # The primary CI matches exactly; the unrelated CI must NOT
        # trigger a near-but-not-equal warning.
        assert r.ok, r.summary()
        joined = " ".join(r.warnings + r.errors).lower()
        assert "near but" not in joined, (
            f"unrelated CI [0.234, 0.567] was flagged as near-but-not-"
            f"equal to [0.700, 0.910]; fuzzy matcher L420 must skip "
            f"1dp-miss cases: {r.summary()}"
        )


def test_execute_ci_text_substring_match_is_not_flagged():
    """L427-428 + L430: the fuzzy matcher's last line of defence is a
    text-substring check. It covers cases where the analyst wrote the
    CI with slightly different rounding that *happens* to contain the
    canonical representation as a substring (e.g. b_lo=0.7001 in
    results.json vs a_lo=0.7099 in analysis.md — the stripped rep
    '0.7' of the results value is a prefix of the analysis value's
    '0.710' 3-decimal form).

    Setup designed to exercise the path:
      - bounds = [0.7001, 0.9001] — rounded_3/2 = (0.7, 0.9)
      - analysis CI [0.7099, 0.9099] — rounded_3 = (0.71, 0.91),
        rounded_2 = (0.71, 0.91), rounded_1 = (0.7, 0.9)
      - 3dp miss, 2dp miss, 1dp MATCH → proceeds to substring check
      - `_number_present_in_text(0.7001, '0.710')` finds the stripped
        '0.7' candidate inside '0.710' → substring_match=True,
        break (L427-428), then the `if substring_match: continue`
        (L429-430) suppresses the warning
      - Both analysis-md values (not round multiples of 0.05) so the
        L417-418 `_is_round` short-circuit doesn't fire first

    Without L427-L430, the matcher would emit a near-but-not-equal
    warning on every bootstrap iteration where the analyst rounded
    the canonical value — a near-constant false-positive."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {
            "primary_model": {"auc": 0.82, "ci_95": [0.7001, 0.9001]},
        }
        # The primary CI is itself listed too, so the
        # _number_present_in_text lo/hi check at L384-L387 passes
        # trivially and doesn't emit a (path-level) warning. What we
        # want to exercise is the tail fuzzy matcher, which iterates
        # over all CI-shaped substrings in the text.
        analysis = (
            "Primary AUC 0.82 CI [0.7001, 0.9001].\n"
            "Supporting bootstrap CI [0.7099, 0.9099].\n"
        )
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert r.ok, r.summary()
        joined = " ".join(r.warnings + r.errors).lower()
        assert "near but" not in joined, (
            f"substring-matched CI was flagged as near-but-not-equal; "
            f"the L427-L430 substring-suppress branch must swallow it: "
            f"{r.summary()}"
        )


def test_execute_ci_present_when_analysis_has_higher_precision():
    """Real failure observed in angiogenesis-crc-20260430 retry: the
    paired delta CI in results.json is `[-0.11766..., 0.02819...]`
    (rounded to 3dp by the cross-check as `[-0.118, 0.028]`), and
    analysis.md prints it as `[-0.1177, +0.0282]` (4dp, more
    precision than the cross-check expected). The lo bound `-0.118`
    is NOT a substring of `-0.1177` — last digit differs — so the
    pre-fix matcher flagged the CI as "not fully present" and
    cross_check escalated to retry_execute, sending the pipeline
    back round despite analysis.md being correct.

    The numeric-scan path now extracts every decimal number from
    analysis.md and accepts when any of them rounds to the same 3dp
    value as the result.
    """
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {
            "primary_model": {"c_index": 0.6528, "ci_95": [0.5956, 0.7101]},
            "baselines": {
                "stage_only": {"c_index": 0.6964, "ci_95": [0.6390, 0.7505]},
            },
            "comparisons": {
                "delta_primary_vs_stage_only": {
                    "delta": -0.0436,
                    "ci_95": [-0.11766379232814346, 0.02819228903334074],
                    "p_directional": 0.8762,
                },
            },
        }
        # Analysis prints CI to 4dp — the literal-substring pass at
        # 3dp won't find "-0.118" in "-0.1177".
        analysis = (
            "## Primary comparison\n\n"
            "| key | delta | ci_95 | p_directional |\n"
            "|---|---|---|---|\n"
            "| delta_primary_vs_stage_only | -0.0436 | "
            "[-0.1177, +0.0282] | 0.8762 |\n"
            "| primary_model.c_index | 0.6528 | [0.5956, 0.7101] | — |\n"
            "| baselines.stage_only.c_index | 0.6964 | "
            "[0.6390, 0.7505] | — |\n"
        )
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        # No errors expected — the numeric-scan path should resolve
        # `-0.118 → -0.1177` and `0.028 → 0.0282`.
        assert not r.errors, (
            "high-precision analysis-md CI should NOT trip the "
            f"`not fully present` check: errors={r.errors}"
        )


# ---------------------------------------------------------------------------
# Numeric sanity checks — reversed/NaN CI, point-estimate-in-CI,
# bounded metric ranges, p-value validity. These catch fabrication
# classes that slip past the CI-presence check (the bad numbers may
# well be faithfully copied into analysis.md from the bad results.json).
# ---------------------------------------------------------------------------


def test_execute_reversed_ci_is_error():
    """ci_95 with lower > upper is an impossible bootstrap result."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {
            "primary_model": {"auc": 0.82, "ci_95": [0.943, 0.677]},
        }
        analysis = "AUC 0.82 CI [0.943, 0.677]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert not r.ok
        assert any(
            "reversed" in e.lower() or "lower > upper" in e.lower()
            for e in r.errors
        ), f"expected reversed-CI error, got {r.errors}"


def test_execute_nan_ci_in_load_bearing_block_is_error():
    """A NaN CI in `primary_model` is a failed computation the executor
    forgot to handle — load-bearing blocks cannot carry placeholders."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        nan_results_json = (
            '{"primary_model": {"auc": 0.82, '
            '"ci_95": [NaN, 0.9]}}'
        )
        stage = tmp / "002_execute"
        (stage / "code").mkdir(parents=True)
        (stage / "results.json").write_text(nan_results_json)
        (stage / "analysis.md").write_text("AUC 0.82\n")
        (stage / "code" / "main.py").write_text("# stub\n")
        r = check_execute(stage)
        assert not r.ok
        joined = " ".join(r.errors).lower()
        assert "nan" in joined or "placeholder" in joined, (
            f"expected NaN/placeholder error on primary_model.ci_95, "
            f"got {r.errors}"
        )


def test_execute_point_estimate_outside_ci_is_error():
    """An AUC of 0.82 cannot sit inside CI [0.50, 0.70] — either the
    point estimate or the CI is fabricated."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {
            "primary_model": {"auc": 0.82, "ci_95": [0.50, 0.70]},
        }
        analysis = "AUC 0.82 CI [0.50, 0.70]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert not r.ok
        assert any(
            "outside" in e.lower() and "ci_95" in e.lower()
            for e in r.errors
        ), f"expected point-estimate-outside-CI error, got {r.errors}"


def test_execute_point_estimate_inside_ci_is_ok():
    """The happy-path symmetry of the previous test: 0.82 ∈ [0.68, 0.94]
    passes the containment check cleanly."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {
            "primary_model": {"auc": 0.82, "ci_95": [0.677, 0.943]},
            "comparisons": {
                "delta_primary_vs_null": {
                    "delta": 0.15,
                    "ci_95": [0.05, 0.25],
                    "p_directional": 0.02,
                },
            },
        }
        analysis = (
            "AUC 0.82 CI [0.677, 0.943]\n"
            "Δ vs null = 0.15 CI [0.05, 0.25] p=0.02\n"
        )
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert r.ok, r.summary()


def test_execute_rounded_point_estimate_inside_ci_is_ok():
    """Analysts often round CI bounds to 3 dp while keeping the point
    estimate at full precision, which can put it ~5e-4 outside the
    rounded CI. The tolerance must absorb this."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {
            # point estimate sits exactly at the rounded lower bound
            # minus 4e-4 — still well under the 5e-4 tolerance.
            "primary_model": {"auc": 0.6776, "ci_95": [0.678, 0.943]},
        }
        analysis = "AUC 0.6776 CI [0.678, 0.943]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert r.ok, f"tolerance should absorb 4e-4 rounding: {r.summary()}"


def test_execute_count_fields_not_treated_as_point_estimate():
    """Real failure observed in ddr-blca-20260430 run: agent emitted
    ``primary_model = {"n": 347, "n_events": 231,
    "concordance_index": 0.518, "ci_95": [0.479, 0.556], ...}``.
    Pre-fix walker iterated dict in insertion order, found
    `n_events` (231) before `concordance_index` (0.518), picked it
    as the "point estimate", and erroneously reported 231 outside
    the c-index CI [0.479, 0.556] — flagging a perfectly honest
    block as fabricated and triggering retry_execute.

    Fix: the n_*, lambda_*, alpha_*, cv_*, seed_*, bootstrap_*
    prefixes mark counts/hyperparams that must NOT be picked as
    metric point estimates.
    """
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {
            "primary_model": {
                "n": 347,
                "n_events": 231,
                "n_selected_genes": 4,
                "lambda_opt": 0.0747,
                "concordance_index": 0.5180,
                "ci_95": [0.479, 0.556],
                "p_value_vs_null_permutation": 0.205,
            },
        }
        analysis = (
            "Primary C-index 0.518 (95% CI [0.479, 0.556]); "
            "n=347 events=231; lambda_opt=0.0747; 4 genes selected.\n"
        )
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        # No errors expected — only the metric (concordance_index)
        # should be checked against ci_95, and 0.518 is within
        # [0.479, 0.556].
        assert not r.errors, (
            "n_events / n_selected_genes / lambda_opt must not be "
            "treated as the metric point estimate; got: "
            f"{r.errors}"
        )


def test_execute_auc_above_one_is_error():
    """AUC = 1.02 is impossible — bounded metric violation."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {
            "primary_model": {"auc": 1.02, "ci_95": [0.95, 1.05]},
        }
        analysis = "AUC 1.02 CI [0.95, 1.05]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert not r.ok
        assert any("[0, 1]" in e and "auc" in e.lower() for e in r.errors), (
            f"expected AUC out-of-[0,1] error, got {r.errors}"
        )


def test_execute_correlation_above_one_is_error():
    """Pearson r = 1.3 violates [-1, 1]."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {
            "primary_model": {"pearson_r": 1.3, "ci_95": [1.1, 1.5]},
        }
        analysis = "r=1.3 CI [1.1, 1.5]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert not r.ok
        assert any("[-1, 1]" in e for e in r.errors), (
            f"expected correlation out-of-[-1,1] error, got {r.errors}"
        )


def test_execute_negative_rmse_is_error():
    """RMSE is a non-negative loss; -0.5 is impossible."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {
            "primary_model": {"rmse": -0.5, "ci_95": [-0.7, -0.3]},
        }
        analysis = "RMSE -0.5 CI [-0.7, -0.3]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert not r.ok
        assert any(
            "negative" in e.lower() and "rmse" in e.lower()
            for e in r.errors
        ), f"expected negative-RMSE error, got {r.errors}"


def test_execute_p_value_above_one_is_error():
    """A p-value of 1.5 is not a probability."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {
            "primary_model": {
                "auc": 0.82,
                "ci_95": [0.68, 0.94],
                "p_value_vs_null_permutation": 1.5,
            },
        }
        analysis = "AUC 0.82 CI [0.68, 0.94] p=1.5\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert not r.ok
        assert any("p-values" in e.lower() for e in r.errors), (
            f"expected p-value out-of-range error, got {r.errors}"
        )


def test_execute_p_value_exact_zero_is_warning():
    """p=0.0 at a permutation resolution floor should be reported as
    `p<1/B`; exact zero is a warning, not an error, since the pipeline
    should still run to completion but the writer must rephrase."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {
            "primary_model": {
                "auc": 0.82,
                "ci_95": [0.68, 0.94],
                "p_value_vs_null_permutation": 0.0,
            },
        }
        analysis = "AUC 0.82 CI [0.68, 0.94] p<0.001\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        # errors may still include unrelated things from downstream
        # checks; the key assertion is the exact-endpoint warning fired.
        assert any(
            "exact endpoint" in w.lower() or "p<1/b" in w.lower()
            for w in r.warnings
        ), f"expected exact-endpoint p-value warning, got {r.warnings}"


def test_execute_bounded_metric_check_scoped_to_load_bearing_blocks():
    """A bogus-looking AUC-named field buried in `coefficients` (not a
    load-bearing block) must NOT fire the bounded-metric check. That
    zone of results.json is free-form and routinely carries values
    outside [0, 1] (e.g. coefficient names that happen to contain
    'acc')."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {
            "primary_model": {"auc": 0.82, "ci_95": [0.68, 0.94]},
            "coefficients": {
                # a coefficient whose key happens to be 'auc' — the
                # walker must not reach it.
                "auc": 3.14,
            },
        }
        analysis = "AUC 0.82 CI [0.68, 0.94]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert r.ok, (
            f"bounded-metric check leaked into coefficients: {r.summary()}"
        )


# ---------------------------------------------------------------------------
# Domain-pack-aware hint helpers — exception-fallback paths
# ---------------------------------------------------------------------------

def test_baseline_name_hints_swallows_domain_pack_exceptions(monkeypatch):
    """L114-115: `baseline_name_hints` calls `domain_pack.load_profile`
    inside a try/except that must swallow ANY exception (not just
    OSError, which load_profile itself catches). The fallback returns
    the generic list unchanged.

    This is defensive against a future `load_profile` implementation
    that raises unexpected errors (e.g. YAML parse errors, import
    cycles, permission issues not caught by the inner OSError handler)
    — the cross-check helper must not blow up a whole phase run just
    because a domain pack is broken. We force that by
    monkeypatching the bound reference used via `from . import
    domain_pack as dp` — since the import happens at call time, any
    attribute lookup against the module sees our patched attribute."""
    from omicairn import domain_pack as dp

    def boom(name):
        raise RuntimeError("synthetic: pack load failed")

    monkeypatch.setattr(dp, "load_profile", boom)

    # Must return the generic list with no extras; no exception.
    hints = baseline_name_hints(domain="anything")
    assert hints == tuple(_BASELINE_NAME_HINTS_GENERIC)


def test_ci_skip_patterns_swallows_domain_pack_exceptions(monkeypatch):
    """L232-233: mirror of the baseline_name_hints fallback for the
    ci_skip_patterns helper. Same rationale — `_walk_ci95` calls this
    on every CI in results.json, so an uncaught raise here would halt
    cross_check on every run the moment any domain pack breaks."""
    from omicairn import domain_pack as dp

    def boom(name):
        raise RuntimeError("synthetic: pack load failed")

    monkeypatch.setattr(dp, "load_profile", boom)

    subs, regex = ci_skip_patterns(domain="anything")
    # The generic regex tuple is always returned.
    assert regex == (r"h2[._\[]",)
    # `subs` must not contain any domain-pack extras; exact contents
    # are implementation-owned, but we can assert the fallback didn't
    # raise and returned a tuple of strings.
    assert isinstance(subs, tuple)
    assert all(isinstance(s, str) for s in subs)


# ---------------------------------------------------------------------------
# Prompt-patch W2/W5 contract enforcement.
#
# These tests pin the behaviour of `_check_w2_power_section_present` and
# `_check_w5_h1_annotation_on_primary_panel`. Each validator must:
#   - silent-skip when the expected input artifact is absent (keeps
#     minimal fixtures passing),
#   - fire an ERROR when the artifact is present but violates the
#     contract.
# ---------------------------------------------------------------------------


def _write_fig_script(stage: pathlib.Path, name: str, body: str) -> pathlib.Path:
    """Write a fig script under `<stage>/code/figures/<name>`."""
    fig_code = stage / "code" / "figures"
    fig_code.mkdir(parents=True, exist_ok=True)
    p = fig_code / name
    p.write_text(body)
    return p


def _write_plan_sibling(tmp: pathlib.Path, index: int, contents: str) -> pathlib.Path:
    """Create a sibling plan stage dir at tmp/<index>_plan with
    hypotheses.md containing `contents`."""
    plan_dir = tmp / f"{index:03d}_plan"
    plan_dir.mkdir(parents=True, exist_ok=True)
    (plan_dir / "hypotheses.md").write_text(contents)
    return plan_dir


# W1 — Figure 1 must be a schematic.

def test_execute_w1_does_not_fire_on_fig10_plus():
    """W1 used to glob `fig1*.py` which matches fig10_*, fig11_*, etc.
    Real-world catch (cuproptosis-stad EXECUTE): fig10_cerna_network.py
    using imshow tripped W1 spuriously. Only fig1_*/fig1-* should be
    audited as the schematic."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {"primary_model": {"auc": 0.82, "ci_95": [0.677, 0.943]}}
        analysis = "AUC 0.82 CI [0.677, 0.943]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        # fig1 schematic — primitives only.
        _write_fig_script(
            stage, "fig1_schematic.py",
            "from matplotlib.patches import Rectangle\n"
            "import matplotlib.pyplot as plt\n"
            "fig, ax = plt.subplots()\n"
            "ax.add_patch(Rectangle((0,0), 1, 1))\n"
            "fig.savefig('fig1_schematic.png')\n",
        )
        # fig10 — results figure that legitimately plots data.
        _write_fig_script(
            stage, "fig10_cerna_network.py",
            "import matplotlib.pyplot as plt\n"
            "fig, ax = plt.subplots()\n"
            "ax.imshow([[1,2],[3,4]])\n"
            "fig.savefig('fig10_cerna_network.png')\n",
        )
        r = check_execute(stage, domain=_W_DOMAIN)
        # fig10 must NOT trip W1.
        assert not any("fig10_cerna_network" in e and "W1" in e for e in r.errors), \
            f"W1 false-positive on fig10_*, got {r.errors}"


def test_execute_w1_fig1_schematic_only_is_ok():
    """A fig1 script that only uses matplotlib primitives (Rectangle,
    FancyArrowPatch, text) is a valid schematic. W1 must not fire."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {
            "primary_model": {"auc": 0.82, "ci_95": [0.677, 0.943]},
        }
        analysis = "AUC 0.82 CI [0.677, 0.943]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        _write_fig_script(
            stage,
            "fig1_schematic.py",
            "import matplotlib.pyplot as plt\n"
            "from matplotlib.patches import Rectangle, FancyArrowPatch\n"
            "fig, ax = plt.subplots()\n"
            "ax.add_patch(Rectangle((0, 0), 1, 1))\n"
            "ax.add_patch(FancyArrowPatch((0, 0), (1, 1)))\n"
            "ax.text(0.5, 0.5, 'variable -> outcome')\n"
            "ax.set_axis_off()\n"
            "fig.savefig('fig1_schematic.png')\n",
        )
        r = check_execute(stage, domain=_W_DOMAIN)
        assert r.ok, f"schematic fig1 must pass W1; got {r.summary()}"


def test_execute_w1_skips_silently_when_no_fig1_script():
    """When `code/figures/` doesn't exist, W1 must silent-skip rather
    than firing a "missing fig1" error. Keeps minimal fixtures green."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {
            "primary_model": {"auc": 0.82, "ci_95": [0.677, 0.943]},
        }
        analysis = "AUC 0.82 CI [0.677, 0.943]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage, domain=_W_DOMAIN)
        assert r.ok, r.summary()
        assert not any("W1" in e or "fig1" in e for e in r.errors)


# W2 — Pre-hoc power analysis must exist in the plan stage.

def test_execute_w2_plan_missing_power_section_is_error():
    """A plan sibling with no power section must fail W2."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_sibling(
            tmp,
            1,
            "# Hypotheses\n\n"
            "H1: The primary metric exceeds the null.\n"
            "H2: The analysis is reproducible.\n",
        )
        results = {"primary_model": {"auc": 0.82, "ci_95": [0.677, 0.943]}}
        analysis = "AUC 0.82 CI [0.677, 0.943]\n"
        stage = _make_execute_stage(tmp, results, analysis)  # at tmp/002_execute
        r = check_execute(stage, domain=_W_DOMAIN)
        assert r.ok
        assert any(
            "power" in w.lower() and "W2" in w
            for w in r.warnings
        ), f"expected W2 missing-power-section error, got {r.warnings}"


def test_execute_w2_plan_with_power_section_is_ok():
    """A plan sibling with a proper `## Power` section must pass W2."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_sibling(
            tmp,
            1,
            "# Hypotheses\n\n"
            "H1: The primary metric exceeds the null.\n\n"
            "## Power\n"
            "Given n=500 and α=0.05, the minimum detectable effect at "
            "80% power is 0.08 AUC units.\n",
        )
        results = {"primary_model": {"auc": 0.82, "ci_95": [0.677, 0.943]}}
        analysis = "AUC 0.82 CI [0.677, 0.943]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage, domain=_W_DOMAIN)
        assert r.ok, r.summary()


def test_execute_w2_skips_silently_when_no_plan_sibling():
    """When no `*_plan` sibling exists, W2 silent-skips."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {"primary_model": {"auc": 0.82, "ci_95": [0.677, 0.943]}}
        analysis = "AUC 0.82 CI [0.677, 0.943]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage, domain=_W_DOMAIN)
        assert r.ok, r.summary()
        assert not any("W2" in e for e in r.errors)


def test_execute_w2_ignores_later_plan_sibling():
    """A plan stage with index >= the execute stage index must NOT
    satisfy W2 (retry_execute walks back to the plan it ran against,
    not a later re-plan)."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        # Plan at index 5, execute at index 2.
        _write_plan_sibling(
            tmp,
            5,
            "# Hypotheses\n\n## Power\nMDE = 0.1 at 80% power α=0.05.\n",
        )
        results = {"primary_model": {"auc": 0.82, "ci_95": [0.677, 0.943]}}
        analysis = "AUC 0.82 CI [0.677, 0.943]\n"
        stage = _make_execute_stage(tmp, results, analysis)  # tmp/002_execute
        r = check_execute(stage, domain=_W_DOMAIN)
        # No preceding plan sibling → silent skip (no error from W2).
        assert r.ok, r.summary()


# W5 — H1 decision annotation on the primary-vs-baseline panel.

def test_execute_w5_missing_h1_annotation_anywhere_is_error():
    """Non-fig1 figure scripts with no H1 decision text annotation fail W5."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {"primary_model": {"auc": 0.82, "ci_95": [0.677, 0.943]}}
        analysis = "AUC 0.82 CI [0.677, 0.943]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        _write_fig_script(
            stage,
            "fig2_primary.py",
            "import matplotlib.pyplot as plt\n"
            "fig, ax = plt.subplots()\n"
            "ax.bar(['primary', 'null'], [0.82, 0.55])\n"
            "ax.set_ylabel('AUC')\n"
            "fig.savefig('fig2_primary.png')\n",
        )
        r = check_execute(stage, domain=_W_DOMAIN)
        assert r.ok
        assert any(
            "H1-decision" in w and "W5" in w
            for w in r.warnings
        ), f"expected W5 missing-H1-annotation error, got {r.warnings}"


def test_execute_w5_fig2_with_h1_annotation_is_ok():
    """A fig2 script with ax.text containing 'H1: SUPPORTED' passes W5."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {"primary_model": {"auc": 0.82, "ci_95": [0.677, 0.943]}}
        analysis = "AUC 0.82 CI [0.677, 0.943]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        _write_fig_script(
            stage,
            "fig2_primary.py",
            "import matplotlib.pyplot as plt\n"
            "fig, ax = plt.subplots()\n"
            "ax.bar(['primary', 'null'], [0.82, 0.55])\n"
            "ax.text(0.5, 0.9, 'H1: SUPPORTED (Δ=0.27, 95% CI 0.10-0.44)',\n"
            "        transform=ax.transAxes)\n"
            "fig.savefig('fig2_primary.png')\n",
        )
        # W8 companion viz-data file so the combined check passes.
        (stage / "figures" / "data").mkdir(parents=True, exist_ok=True)
        (stage / "figures" / "data" / "fig2_primary.json").write_text("{}")
        r = check_execute(stage, domain=_W_DOMAIN)
        assert r.ok, r.summary()


def test_execute_w5_accepts_not_supported_annotation():
    """Honest negative results are fine — 'H1: NOT SUPPORTED' also
    counts as a valid annotation (null findings are still scientific
    results and must be annotated just as explicitly)."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {"primary_model": {"auc": 0.55, "ci_95": [0.45, 0.65]}}
        analysis = "AUC 0.55 CI [0.45, 0.65]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        _write_fig_script(
            stage,
            "fig2_comparison.py",
            "import matplotlib.pyplot as plt\n"
            "fig, ax = plt.subplots()\n"
            "ax.annotate('H1: NOT SUPPORTED (Δ=-0.05, 95% CI -0.2 to +0.1)',\n"
            "            xy=(0.5, 0.9), xycoords='axes fraction')\n"
            "fig.savefig('fig2_comparison.png')\n",
        )
        # W8 companion viz-data file so the combined check passes.
        (stage / "figures" / "data").mkdir(parents=True, exist_ok=True)
        (stage / "figures" / "data" / "fig2_comparison.json").write_text("{}")
        r = check_execute(stage, domain=_W_DOMAIN)
        assert r.ok, r.summary()


def test_execute_w5_skips_silently_when_only_fig1_script():
    """Only a fig1 schematic exists → W5 silent-skips (no primary-result figure to check)."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {"primary_model": {"auc": 0.82, "ci_95": [0.677, 0.943]}}
        analysis = "AUC 0.82 CI [0.677, 0.943]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        _write_fig_script(
            stage,
            "fig1_schematic.py",
            "from matplotlib.patches import Rectangle\n"
            "import matplotlib.pyplot as plt\n"
            "fig, ax = plt.subplots()\n"
            "ax.add_patch(Rectangle((0, 0), 1, 1))\n"
            "ax.text(0.5, 0.5, 'design')\n"
            "fig.savefig('fig1_schematic.png')\n",
        )
        r = check_execute(stage, domain=_W_DOMAIN)
        assert r.ok, r.summary()
        assert not any("W5" in e for e in r.errors)


def test_execute_w5_accepts_h1_annotation_on_later_figure():
    """A multi-panel figure set may place the H1 decision on fig5 (validation)
    rather than fig2. W5 should pass as long as *some* non-fig1 figure
    script carries the literal H1-decision annotation."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {"primary_model": {"auc": 0.82, "ci_95": [0.677, 0.943]}}
        analysis = "AUC 0.82 CI [0.677, 0.943]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        # fig2 is an upstream descriptive figure with no H1 decision
        _write_fig_script(
            stage,
            "fig2_descriptive.py",
            "import matplotlib.pyplot as plt\n"
            "fig, ax = plt.subplots()\n"
            "ax.bar(['A', 'B'], [1, 2])\n"
            "fig.savefig('fig2_descriptive.png')\n",
        )
        # fig5 carries the H1 decision
        _write_fig_script(
            stage,
            "fig5_validation.py",
            "import matplotlib.pyplot as plt\n"
            "fig, ax = plt.subplots()\n"
            "ax.text(0.5, 0.9, 'H1: SUPPORTED (Δ=0.27, 95% CI 0.10-0.44)',\n"
            "        transform=ax.transAxes)\n"
            "fig.savefig('fig5_validation.png')\n",
        )
        (stage / "figures" / "data").mkdir(parents=True, exist_ok=True)
        (stage / "figures" / "data" / "fig2_descriptive.json").write_text("{}")
        (stage / "figures" / "data" / "fig5_validation.json").write_text("{}")
        r = check_execute(stage, domain=_W_DOMAIN)
        assert r.ok, r.summary()


# W7 — Stratum-trained sensitivity fit.

_W7_PLAN_WITH_MARKER = (
    "# Hypotheses\n\n"
    "H1: The primary metric exceeds the null.\n"
    "H2: A stratum-trained confirmation model fit on the high-stage\n"
    "    subgroup validates the signature's stratum-specific effect.\n\n"
    "## Power\n"
    "MDE = 0.08 at 80% power α=0.05.\n"
)

_W7_PLAN_WITHOUT_MARKER = (
    "# Hypotheses\n\n"
    "H1: The primary metric exceeds the null.\n\n"
    "## Power\n"
    "MDE = 0.08 at 80% power α=0.05.\n"
)


def test_execute_w7_missing_stratum_trained_entry_is_error():
    """Plan declares stratum-trained confirmation but results.json has
    no `primary_model.stratum_trained_*` entry → W7 fires."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_sibling(tmp, 1, _W7_PLAN_WITH_MARKER)
        results = {
            "primary_model": {"c_index": 0.72, "ci_95": [0.68, 0.76]},
            "comparisons": {
                "delta_primary_vs_null": {"delta": 0.17, "ci_95": [0.10, 0.24]},
            },
        }
        analysis = (
            "C-index 0.72 CI [0.68, 0.76]\n"
            "Δ=0.17 CI [0.10, 0.24]\n"
        )
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert r.ok
        assert any("W7" in w for w in r.warnings), r.warnings


def test_execute_w7_skipped_with_reason_is_ok():
    """Stratum entry with `skipped=true` + `reason` satisfies W7."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_sibling(tmp, 1, _W7_PLAN_WITH_MARKER)
        results = {
            "primary_model": {
                "c_index": 0.72,
                "ci_95": [0.68, 0.76],
                "stratum_trained_stage_iii_iv": {
                    "skipped": True,
                    "reason": "discovery events=18 < min 30",
                },
            },
            "comparisons": {
                "delta_primary_vs_null": {"delta": 0.17, "ci_95": [0.10, 0.24]},
            },
        }
        analysis = (
            "C-index 0.72 CI [0.68, 0.76]\n"
            "Δ=0.17 CI [0.10, 0.24]\n"
        )
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert r.ok, r.summary()


def test_execute_w7_skipped_without_reason_is_error():
    """Skipped stratum entry must include a `reason` string."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_sibling(tmp, 1, _W7_PLAN_WITH_MARKER)
        results = {
            "primary_model": {
                "c_index": 0.72,
                "ci_95": [0.68, 0.76],
                "stratum_trained_late_stage": {"skipped": True},
            },
            "comparisons": {
                "delta_primary_vs_null": {"delta": 0.17, "ci_95": [0.10, 0.24]},
            },
        }
        analysis = "C-index 0.72 CI [0.68, 0.76]\nΔ=0.17 CI [0.10, 0.24]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert r.ok
        assert any("reason" in w.lower() and "W7" in w for w in r.warnings), r.warnings


def test_execute_w7_complete_entry_is_ok():
    """Full-schema stratum_trained entry (metric + ci_95 + n + events) passes."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_sibling(tmp, 1, _W7_PLAN_WITH_MARKER)
        results = {
            "primary_model": {
                "c_index": 0.72,
                "ci_95": [0.68, 0.76],
                "stratum_trained_late_stage": {
                    "c_index": 0.69,
                    "ci_95": [0.62, 0.75],
                    "n": 142,
                    "events": 58,
                },
            },
            "comparisons": {
                "delta_primary_vs_null": {"delta": 0.17, "ci_95": [0.10, 0.24]},
            },
        }
        analysis = "C-index 0.72 CI [0.68, 0.76]\nΔ=0.17 CI [0.10, 0.24]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert r.ok, r.summary()


def test_execute_w7_incomplete_entry_is_error():
    """Missing `n`/`events` on a non-skipped stratum_trained entry fails."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_sibling(tmp, 1, _W7_PLAN_WITH_MARKER)
        results = {
            "primary_model": {
                "c_index": 0.72,
                "ci_95": [0.68, 0.76],
                "stratum_trained_late_stage": {
                    "c_index": 0.69,
                    "ci_95": [0.62, 0.75],
                    # missing n + events
                },
            },
            "comparisons": {
                "delta_primary_vs_null": {"delta": 0.17, "ci_95": [0.10, 0.24]},
            },
        }
        analysis = "C-index 0.72 CI [0.68, 0.76]\nΔ=0.17 CI [0.10, 0.24]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert r.ok
        assert any("W7" in w and ("n" in w or "events" in w) for w in r.warnings), r.warnings


def test_execute_w7_silent_skip_when_plan_has_no_marker():
    """Plan without stratum-trained marker → W7 silent-skips."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_sibling(tmp, 1, _W7_PLAN_WITHOUT_MARKER)
        results = {
            "primary_model": {"c_index": 0.72, "ci_95": [0.68, 0.76]},
            "comparisons": {
                "delta_primary_vs_null": {"delta": 0.17, "ci_95": [0.10, 0.24]},
            },
        }
        analysis = "C-index 0.72 CI [0.68, 0.76]\nΔ=0.17 CI [0.10, 0.24]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert r.ok, r.summary()
        assert not any("W7" in e for e in r.errors)


def test_execute_w7_silent_skip_when_no_plan_sibling():
    """No plan sibling → W7 silent-skips regardless."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {"primary_model": {"c_index": 0.72, "ci_95": [0.68, 0.76]}}
        analysis = "C-index 0.72 CI [0.68, 0.76]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert r.ok, r.summary()
        assert not any("W7" in e for e in r.errors)


_W7_PLAN_NEGATED_MARKER = (
    "# Hypotheses\n\n"
    "H1: The primary metric exceeds the null.\n\n"
    "## Sensitivity analyses\n"
    "No stratum-trained confirmation is required — the authors' "
    "pipeline is already line- and NIM-agnostic within each dataset.\n\n"
    "## Power\n"
    "MDE = 0.08 at 80% power α=0.05.\n"
)


def test_execute_w7_silent_skip_when_plan_explicitly_negates_marker():
    """Plan explicitly says `No stratum-trained confirmation is required`
    → W7 silent-skips (the substring match must not fire on negations)."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_sibling(tmp, 1, _W7_PLAN_NEGATED_MARKER)
        results = {
            "primary_model": {"c_index": 0.72, "ci_95": [0.68, 0.76]},
            "comparisons": {
                "delta_primary_vs_null": {"delta": 0.17, "ci_95": [0.10, 0.24]},
            },
        }
        analysis = "C-index 0.72 CI [0.68, 0.76]\nΔ=0.17 CI [0.10, 0.24]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert r.ok, r.summary()
        assert not any("W7" in e for e in r.errors)


_W7_PLAN_MIXED_MARKER = (
    "# Hypotheses\n\n"
    "H1: The primary metric exceeds the null.\n"
    "H2: We will fit a stratum-trained confirmation model on the "
    "high-stage subgroup.\n\n"
    "## Caveats\n"
    "A line-specific stratum-trained fit is not applicable here.\n"
)


def test_execute_w7_fires_when_any_sentence_is_a_commitment():
    """When one plan sentence commits to stratum-trained AND another
    negates, W7 still fires — the commitment wins."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_sibling(tmp, 1, _W7_PLAN_MIXED_MARKER)
        results = {
            "primary_model": {"c_index": 0.72, "ci_95": [0.68, 0.76]},
            "comparisons": {
                "delta_primary_vs_null": {"delta": 0.17, "ci_95": [0.10, 0.24]},
            },
        }
        analysis = "C-index 0.72 CI [0.68, 0.76]\nΔ=0.17 CI [0.10, 0.24]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert r.ok
        assert any("W7" in w for w in r.warnings), r.warnings


# W8 — viz-data separation contract.

def _write_viz_data(stage: pathlib.Path, name: str, body: str = '{"x": [1]}') -> pathlib.Path:
    """Write a minimal viz-data file under `<stage>/figures/data/<name>`."""
    data_dir = stage / "figures" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    p = data_dir / name
    p.write_text(body)
    return p


def test_execute_w8_missing_viz_data_file_is_error():
    """A render script with no matching figures/data/<stem>.{json,csv,...}
    file fails W8."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {"primary_model": {"auc": 0.82, "ci_95": [0.677, 0.943]}}
        analysis = "AUC 0.82 CI [0.677, 0.943]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        _write_fig_script(
            stage,
            "fig2_primary.py",
            "import json, matplotlib.pyplot as plt\n"
            "fig, ax = plt.subplots()\n"
            "ax.bar(['primary', 'null'], [0.82, 0.55])\n"
            "ax.text(0.5, 0.9, 'H1: SUPPORTED (Δ=0.27)', transform=ax.transAxes)\n"
            "fig.savefig('fig2_primary.png')\n",
        )
        r = check_execute(stage)
        assert r.ok
        assert any(
            "fig2_primary.py" in w and "W8" in w and "viz-data" in w
            for w in r.warnings
        ), f"expected W8 missing-viz-data error, got {r.warnings}"


def test_execute_w8_matching_viz_data_file_is_ok():
    """A render script with a matching figures/data/<stem>.json passes W8."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {"primary_model": {"auc": 0.82, "ci_95": [0.677, 0.943]}}
        analysis = "AUC 0.82 CI [0.677, 0.943]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        _write_fig_script(
            stage,
            "fig2_primary.py",
            "import json, matplotlib.pyplot as plt\n"
            "data = json.loads(open('figures/data/fig2_primary.json').read())\n"
            "fig, ax = plt.subplots()\n"
            "ax.bar(data['labels'], data['values'])\n"
            "ax.text(0.5, 0.9, 'H1: SUPPORTED (Δ=0.27)', transform=ax.transAxes)\n"
            "fig.savefig('fig2_primary.png')\n",
        )
        _write_viz_data(
            stage, "fig2_primary.json",
            '{"labels": ["primary", "null"], "values": [0.82, 0.55]}',
        )
        r = check_execute(stage)
        assert r.ok, r.summary()


def test_execute_w8_csv_extension_accepted():
    """A matching `.csv` viz-data file is accepted (not only `.json`)."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {"primary_model": {"auc": 0.82, "ci_95": [0.677, 0.943]}}
        analysis = "AUC 0.82 CI [0.677, 0.943]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        _write_fig_script(
            stage,
            "fig3_km.py",
            "import pandas as pd, matplotlib.pyplot as plt\n"
            "df = pd.read_csv('figures/data/fig3_km.csv')\n"
            "fig, ax = plt.subplots()\n"
            "ax.step(df['t'], df['s'])\n"
            "ax.text(0.5, 0.9, 'H1: SUPPORTED (Δ=0.27)', transform=ax.transAxes)\n"
            "fig.savefig('fig3_km.png')\n",
        )
        _write_viz_data(stage, "fig3_km.csv", "t,s\n0,1.0\n1,0.9\n")
        r = check_execute(stage)
        assert r.ok, r.summary()


def test_execute_w8_render_script_importing_data_loader_is_error():
    """A render script that imports `data_loader` fails W8 even when the
    viz-data file exists — it means the renderer is recomputing results
    instead of reading the pre-computed minimal file."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {"primary_model": {"auc": 0.82, "ci_95": [0.677, 0.943]}}
        analysis = "AUC 0.82 CI [0.677, 0.943]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        _write_fig_script(
            stage,
            "fig4_features.py",
            "import matplotlib.pyplot as plt\n"
            "from data_loader import load_cohort\n"
            "df = load_cohort()\n"
            "fig, ax = plt.subplots()\n"
            "ax.bar(df['name'], df['importance'])\n"
            "fig.savefig('fig4_features.png')\n",
        )
        _write_viz_data(stage, "fig4_features.json")
        r = check_execute(stage)
        assert r.ok
        assert any(
            "fig4_features.py" in w and "W8" in w and "data_loader" in w
            for w in r.warnings
        ), f"expected W8 forbidden-import error, got {r.warnings}"


def test_execute_w8_render_script_importing_models_is_error():
    """Importing `models` from a renderer also fails W8."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {"primary_model": {"auc": 0.82, "ci_95": [0.677, 0.943]}}
        analysis = "AUC 0.82 CI [0.677, 0.943]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        _write_fig_script(
            stage,
            "fig5_calibration.py",
            "import matplotlib.pyplot as plt\n"
            "import models\n"
            "preds = models.predict()\n"
            "fig, ax = plt.subplots()\n"
            "ax.plot(preds)\n"
            "fig.savefig('fig5_calibration.png')\n",
        )
        _write_viz_data(stage, "fig5_calibration.json")
        r = check_execute(stage)
        assert r.ok
        assert any(
            "fig5_calibration.py" in w and "W8" in w
            and "models" in w
            for w in r.warnings
        ), f"expected W8 forbidden-import error, got {r.warnings}"


def test_execute_w8_unparseable_render_script_still_requires_data_file():
    """A render script with a syntax error — AST walk returns [] imports,
    so the forbidden-import branch can't fire. The data-file-presence
    branch still fires if the viz data is missing, which is the more
    important check (prevents silent W8 bypass via broken python).
    When the data file IS present the unparseable script passes W8
    silently; runtime execution errors will surface elsewhere."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {"primary_model": {"auc": 0.82, "ci_95": [0.677, 0.943]}}
        analysis = "AUC 0.82 CI [0.677, 0.943]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        # Syntax-error script, no viz-data file — W8 must still fire on
        # the missing data file rather than silently passing.
        _write_fig_script(
            stage,
            "fig2_broken.py",
            "import matplotlib.pyplot as plt\n"
            "def (syntax error: broken:\n"
            "fig, ax = plt.subplots()\n",
        )
        r = check_execute(stage)
        assert r.ok
        assert any(
            "fig2_broken.py" in w and "W8" in w and "viz-data" in w
            for w in r.warnings
        ), f"expected W8 missing-viz-data error on unparseable script, got {r.warnings}"


def test_execute_w8_fig10_not_exempted_as_schematic():
    """The fig1 schematic exemption must match `fig1` exactly, not
    `fig10`/`fig11`/etc. A `fig10_*.py` script without viz data must
    trip W8. Regression guard against a `startswith('fig1')` bug."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {"primary_model": {"auc": 0.82, "ci_95": [0.677, 0.943]}}
        analysis = "AUC 0.82 CI [0.677, 0.943]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        _write_fig_script(
            stage,
            "fig10_summary.py",
            "import matplotlib.pyplot as plt\n"
            "fig, ax = plt.subplots()\n"
            "ax.bar(['a', 'b'], [1, 2])\n"
            "fig.savefig('fig10_summary.png')\n",
        )
        r = check_execute(stage)
        assert r.ok
        assert any(
            "fig10_summary.py" in w and "W8" in w
            for w in r.warnings
        ), f"fig10 must not be exempt from W8; got {r.warnings}"


def test_execute_w8_silent_skip_when_no_figure_scripts():
    """When `code/figures/` is missing, W8 silent-skips."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {"primary_model": {"auc": 0.82, "ci_95": [0.677, 0.943]}}
        analysis = "AUC 0.82 CI [0.677, 0.943]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert r.ok, r.summary()
        assert not any("W8" in e for e in r.errors)


# ---------------------------------------------------------------------------
# W13 — load-bearing-arm gate (always-on ERROR; no-op when unnamed)
# ---------------------------------------------------------------------------

def _w13_prereg(load_bearing_arm) -> dict:
    """Minimal preregistration.json carrying a load_bearing_arm value.
    Pass the sentinel object() to omit the field entirely."""
    prereg: dict = {
        "primary_hypothesis": "H1",
        "primary_metric": "delta",
        "mde": 0.05,
    }
    if load_bearing_arm is not _W13_OMIT:
        prereg["load_bearing_arm"] = load_bearing_arm
    return prereg


_W13_OMIT = object()


def test_execute_w13_named_arm_absent_is_error():
    """preregistration names a load_bearing_arm whose comparisons.delta_*
    key is ABSENT from results.json → W13 ERRORs (not warns), blocking REVIEW."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(
            tmp, 1, _w13_prereg("delta_steered_vs_unsteered")
        )
        results = {
            "primary_model": {"auc": 0.82, "ci_95": [0.70, 0.90]},
            "comparisons": {
                # a DIFFERENT comparison ran; the load-bearing one did not.
                "delta_other_vs_null": {"delta": 0.05, "ci_95": [0.01, 0.09]},
            },
        }
        analysis = "AUC 0.82 CI [0.70, 0.90]\nΔ=0.05 CI [0.01, 0.09]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert not r.ok, r.summary()
        assert any(
            "W13" in e and "delta_steered_vs_unsteered" in e for e in r.errors
        ), r.errors
        # Must be an ERROR, never demoted to a warning.
        assert not any("W13:" in w for w in r.warnings), r.warnings


def test_execute_w13_named_arm_present_passes_silently():
    """Inverse: the named comparisons.delta_* key IS present → W13 silent."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(
            tmp, 1, _w13_prereg("delta_steered_vs_unsteered")
        )
        results = {
            "primary_model": {"auc": 0.82, "ci_95": [0.70, 0.90]},
            "comparisons": {
                "delta_steered_vs_unsteered": {
                    "delta": 0.12, "ci_95": [0.05, 0.19]
                },
            },
        }
        analysis = "AUC 0.82 CI [0.70, 0.90]\nΔ=0.12 CI [0.05, 0.19]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert r.ok, r.summary()
        assert not any("W13" in e for e in r.errors)
        assert not any("W13:" in w for w in r.warnings)


def test_execute_w13_no_field_is_noop_backward_compat():
    """Backward compat: preregistration WITHOUT load_bearing_arm (older
    cases) → W13 is a no-op, no error even though no comparison ran."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(tmp, 1, _w13_prereg(_W13_OMIT))
        results = {"primary_model": {"auc": 0.82, "ci_95": [0.70, 0.90]}}
        analysis = "AUC 0.82 CI [0.70, 0.90]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert r.ok, r.summary()
        assert not any("W13" in e for e in r.errors)
        assert not any("W13:" in w for w in r.warnings)


def test_execute_w13_dotted_form_arm_present_passes():
    """The field may carry the fully-dotted `comparisons.delta_*` form;
    the prefix is stripped before matching the comparisons leaf key."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(
            tmp, 1, _w13_prereg("comparisons.delta_primary_vs_null")
        )
        results = {
            "primary_model": {"auc": 0.82, "ci_95": [0.70, 0.90]},
            "comparisons": {
                "delta_primary_vs_null": {"delta": 0.1, "ci_95": [0.02, 0.18]},
            },
        }
        analysis = "AUC 0.82 CI [0.70, 0.90]\nΔ=0.1 CI [0.02, 0.18]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert r.ok, r.summary()
        assert not any("W13" in e for e in r.errors)


def test_execute_w13_list_of_arms_one_missing_is_error():
    """A list value names a conjunction of arms; if any is absent W13 errors."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(
            tmp, 1,
            _w13_prereg(["delta_a_vs_null", "delta_b_vs_null"]),
        )
        results = {
            "primary_model": {"auc": 0.82, "ci_95": [0.70, 0.90]},
            "comparisons": {
                "delta_a_vs_null": {"delta": 0.1, "ci_95": [0.02, 0.18]},
                # delta_b_vs_null missing
            },
        }
        analysis = "AUC 0.82 CI [0.70, 0.90]\nΔ=0.1 CI [0.02, 0.18]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert not r.ok, r.summary()
        assert any("W13" in e and "delta_b_vs_null" in e for e in r.errors), r.errors


def test_execute_w13_empty_string_field_is_noop():
    """A blank load_bearing_arm value is treated as 'not named' → no-op."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(tmp, 1, _w13_prereg("   "))
        results = {"primary_model": {"auc": 0.82, "ci_95": [0.70, 0.90]}}
        analysis = "AUC 0.82 CI [0.70, 0.90]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert r.ok, r.summary()
        assert not any("W13" in e for e in r.errors)


# ---------------------------------------------------------------------------
# W14 — shared-protocol baseline awareness (ADVISORY / warn-only; no-op when
# the `baselines` block is absent)
# ---------------------------------------------------------------------------

def _w14_prereg(baselines) -> dict:
    """Minimal preregistration.json carrying a `baselines` block.
    Pass the sentinel object() to omit the field entirely."""
    prereg: dict = {
        "primary_hypothesis": "H1",
        "primary_metric": "delta",
        "mde": 0.05,
    }
    if baselines is not _W14_OMIT:
        prereg["baselines"] = baselines
    return prereg


_W14_OMIT = object()


def test_execute_w14_feasible_baseline_not_run_warns():
    """A feasible shared-protocol baseline is named but no comparison arm
    reached results.json → W14 WARNS (never errors)."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(
            tmp, 1,
            _w14_prereg({
                "shared_protocol_feasible": True,
                "candidates": ["LlavaGuard-7B"],
            }),
        )
        results = {"primary_model": {"auc": 0.82, "ci_95": [0.70, 0.90]}}
        analysis = "AUC 0.82 CI [0.70, 0.90]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        # Advisory: must NOT block (ok stays governed by errors only).
        assert any("W14" in w and "LlavaGuard-7B" in w for w in r.warnings), r.warnings
        assert not any("W14" in e for e in r.errors), r.errors


def test_execute_w14_feasible_baseline_run_passes_silently():
    """The named feasible baseline DID run (a comparisons.* arm exists) →
    W14 silent."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(
            tmp, 1,
            _w14_prereg({
                "shared_protocol_feasible": True,
                "candidates": ["LlavaGuard-7B"],
            }),
        )
        results = {
            "primary_model": {"auc": 0.90, "ci_95": [0.85, 0.95]},
            "comparisons": {
                "delta_primary_vs_llavaguard": {"delta": 0.1, "ci_95": [0.02, 0.18]},
            },
        }
        analysis = "AUC 0.90 CI [0.85, 0.95]\nΔ=0.1 CI [0.02, 0.18]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert not any("W14" in w for w in r.warnings), r.warnings
        assert not any("W14" in e for e in r.errors), r.errors


def test_execute_w14_infeasible_without_rationale_warns():
    """`shared_protocol_feasible: false` with no `no_baseline_rationale` →
    W14 WARNS (an unexplained declined head-to-head)."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(
            tmp, 1,
            _w14_prereg({"shared_protocol_feasible": False}),
        )
        results = {"primary_model": {"auc": 0.82, "ci_95": [0.70, 0.90]}}
        analysis = "AUC 0.82 CI [0.70, 0.90]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert any("W14" in w and "no_baseline_rationale" in w for w in r.warnings), r.warnings
        assert not any("W14" in e for e in r.errors), r.errors


def test_execute_w14_infeasible_with_rationale_passes_silently():
    """A justified `shared_protocol_feasible: false` (first-of-kind / pure
    null) → W14 silent. A legitimately-uncomparable run is not penalised."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(
            tmp, 1,
            _w14_prereg({
                "shared_protocol_feasible": False,
                "no_baseline_rationale": (
                    "first protocol of this kind; no prior method operates "
                    "on this measurement"
                ),
            }),
        )
        results = {"primary_model": {"auc": 0.82, "ci_95": [0.70, 0.90]}}
        analysis = "AUC 0.82 CI [0.70, 0.90]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert not any("W14" in w for w in r.warnings), r.warnings
        assert not any("W14" in e for e in r.errors), r.errors


def test_execute_w14_no_block_is_noop_backward_compat():
    """Backward compat: a preregistration WITHOUT a `baselines` block (older
    cases) → W14 is a silent no-op."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(tmp, 1, _w14_prereg(_W14_OMIT))
        results = {"primary_model": {"auc": 0.82, "ci_95": [0.70, 0.90]}}
        analysis = "AUC 0.82 CI [0.70, 0.90]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert r.ok, r.summary()
        assert not any("W14" in w for w in r.warnings)
        assert not any("W14" in e for e in r.errors)


def test_execute_w14_feasible_no_candidates_is_noop():
    """`shared_protocol_feasible: true` but no candidate named yet → W14
    no-ops (a PLAN-only signal; the missing-arm warning needs a named
    candidate to point at)."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(
            tmp, 1,
            _w14_prereg({"shared_protocol_feasible": True, "candidates": []}),
        )
        results = {"primary_model": {"auc": 0.82, "ci_95": [0.70, 0.90]}}
        analysis = "AUC 0.82 CI [0.70, 0.90]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert not any("W14" in w for w in r.warnings), r.warnings


# ---------------------------------------------------------------------------
# W15 — salvage-provenance guard (always-on ERROR; companion to W13). The
# load-bearing arm's number must be PROVENANCE-CLEAN (re-scorable / `fresh`),
# not recovered via salvage. No-op when unnamed, absent (W13's job), or
# untagged (older results → treated as clean).
# ---------------------------------------------------------------------------

def test_execute_w15_load_bearing_arm_salvaged_is_error():
    """The named load_bearing_arm IS present but tagged provenance=salvaged
    → W15 ERRORs (not warns), blocking REVIEW."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(
            tmp, 1, _w13_prereg("delta_steered_vs_unsteered")
        )
        results = {
            "primary_model": {"auc": 0.82, "ci_95": [0.70, 0.90]},
            "comparisons": {
                "delta_steered_vs_unsteered": {
                    "delta": 0.12, "ci_95": [0.05, 0.19],
                    "provenance": "salvaged",
                },
            },
        }
        analysis = "AUC 0.82 CI [0.70, 0.90]\nΔ=0.12 CI [0.05, 0.19]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert not r.ok, r.summary()
        assert any(
            "W15" in e and "delta_steered_vs_unsteered" in e for e in r.errors
        ), r.errors
        # Must be an ERROR, never demoted to a warning.
        assert not any("W15" in w for w in r.warnings), r.warnings


def test_execute_w15_load_bearing_arm_fresh_passes_silently():
    """The named arm is tagged provenance=fresh → W15 silent."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(
            tmp, 1, _w13_prereg("delta_steered_vs_unsteered")
        )
        results = {
            "primary_model": {"auc": 0.82, "ci_95": [0.70, 0.90]},
            "comparisons": {
                "delta_steered_vs_unsteered": {
                    "delta": 0.12, "ci_95": [0.05, 0.19],
                    "provenance": "fresh",
                },
            },
        }
        analysis = "AUC 0.82 CI [0.70, 0.90]\nΔ=0.12 CI [0.05, 0.19]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert r.ok, r.summary()
        assert not any("W15" in e for e in r.errors)
        assert not any("W15" in w for w in r.warnings)


def test_execute_w15_untagged_arm_is_noop_backward_compat():
    """Backward compat: the named arm has NO provenance field (older results)
    → treated as clean → W15 no-op. (W13 still passes because the arm is
    present.)"""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(
            tmp, 1, _w13_prereg("delta_steered_vs_unsteered")
        )
        results = {
            "primary_model": {"auc": 0.82, "ci_95": [0.70, 0.90]},
            "comparisons": {
                "delta_steered_vs_unsteered": {
                    "delta": 0.12, "ci_95": [0.05, 0.19],
                    # no provenance field
                },
            },
        }
        analysis = "AUC 0.82 CI [0.70, 0.90]\nΔ=0.12 CI [0.05, 0.19]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert r.ok, r.summary()
        assert not any("W15" in e for e in r.errors)
        assert not any("W15" in w for w in r.warnings)


def test_execute_w15_no_load_bearing_arm_is_noop():
    """No load_bearing_arm declared → W15 no-op even if a comparison is
    salvage-tagged (W15 only judges the NAMED headline arm)."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(tmp, 1, _w13_prereg(_W13_OMIT))
        results = {
            "primary_model": {"auc": 0.82, "ci_95": [0.70, 0.90]},
            "comparisons": {
                "delta_some_vs_null": {
                    "delta": 0.12, "ci_95": [0.05, 0.19],
                    "provenance": "salvaged",
                },
            },
        }
        analysis = "AUC 0.82 CI [0.70, 0.90]\nΔ=0.12 CI [0.05, 0.19]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert not any("W15" in e for e in r.errors), r.errors
        assert not any("W15" in w for w in r.warnings), r.warnings


def test_execute_w15_named_arm_absent_is_w13_not_w15():
    """When the named arm is ABSENT, W13 flags incompleteness; W15 stays
    silent (it only judges provenance of arms that DID land)."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(
            tmp, 1, _w13_prereg("delta_steered_vs_unsteered")
        )
        results = {
            "primary_model": {"auc": 0.82, "ci_95": [0.70, 0.90]},
            "comparisons": {
                "delta_other_vs_null": {
                    "delta": 0.05, "ci_95": [0.01, 0.09], "provenance": "fresh",
                },
            },
        }
        analysis = "AUC 0.82 CI [0.70, 0.90]\nΔ=0.05 CI [0.01, 0.09]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert not r.ok, r.summary()
        assert any("W13" in e for e in r.errors), r.errors
        assert not any("W15" in e for e in r.errors), r.errors


def test_execute_w15_host_scratch_alias_is_error():
    """Alternative salvage spellings (host_scratch / manual) also trip W15,
    matched case-insensitively."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(
            tmp, 1, _w13_prereg("delta_primary_vs_null")
        )
        results = {
            "primary_model": {"auc": 0.82, "ci_95": [0.70, 0.90]},
            "comparisons": {
                "delta_primary_vs_null": {
                    "delta": 0.1, "ci_95": [0.02, 0.18],
                    "provenance": "Host-Scratch",
                },
            },
        }
        analysis = "AUC 0.82 CI [0.70, 0.90]\nΔ=0.1 CI [0.02, 0.18]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert not r.ok, r.summary()
        assert any("W15" in e and "delta_primary_vs_null" in e for e in r.errors), r.errors


def test_execute_w15_list_arms_one_salvaged_is_error():
    """A list load_bearing_arm: if any named arm is salvage-tagged W15 errors,
    naming the salvaged key."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(
            tmp, 1, _w13_prereg(["delta_a_vs_null", "delta_b_vs_null"])
        )
        results = {
            "primary_model": {"auc": 0.82, "ci_95": [0.70, 0.90]},
            "comparisons": {
                "delta_a_vs_null": {
                    "delta": 0.1, "ci_95": [0.02, 0.18], "provenance": "fresh",
                },
                "delta_b_vs_null": {
                    "delta": 0.2, "ci_95": [0.10, 0.30], "provenance": "salvaged",
                },
            },
        }
        analysis = "AUC 0.82 CI [0.70, 0.90]\nΔ=0.1 CI [0.02, 0.18]\nΔ=0.2 CI [0.10, 0.30]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert not r.ok, r.summary()
        w15 = [e for e in r.errors if "W15" in e]
        assert w15, r.errors
        # The salvaged-keys clause sits between "but " and " carries
        # provenance"; the fresh sibling must NOT be listed there (it may
        # still appear in the load_bearing_arm echo earlier in the message).
        salvaged_clause = w15[0].split("but ", 1)[1].split(" carries provenance")[0]
        assert "comparisons.delta_b_vs_null" in salvaged_clause, salvaged_clause
        assert "comparisons.delta_a_vs_null" not in salvaged_clause, salvaged_clause


# ---------------------------------------------------------------------------
# W15L — latest-execute-stage provenance gate (advisory / warn-only sibling of
# W15). WARNs when a named load_bearing_arm is ABSENT from the latest execute
# stage but PRESENT in an earlier execute sibling (the d5 set-level-CKA
# fracture); no-op on single-execute runs, no field, or arm-present-in-latest.
# ---------------------------------------------------------------------------

def _write_execute_stage_at(
    tmp: pathlib.Path, index: int, results: dict, analysis: str
) -> pathlib.Path:
    """Build an execute stage at tmp/<index>_execute with a clean lockfile so
    only the gate under test (not unrelated env/CI checks) speaks."""
    stage = tmp / f"{index:03d}_execute"
    (stage / "code").mkdir(parents=True, exist_ok=True)
    (stage / "results.json").write_text(json.dumps(results))
    (stage / "analysis.md").write_text(analysis)
    (stage / "code" / "main.py").write_text("# stub\n")
    (stage / "code" / "environment.lock.json").write_text(
        json.dumps({
            "version": 1, "captured_at": "fixture", "python_version": "3.11",
            "platform": "fixture", "executable": "fixture",
            "packages": [], "imported_modules": [],
        })
    )
    return stage


def test_w15l_arm_dropped_by_latest_present_earlier_warns():
    """The d5 fracture: the load_bearing_arm landed in an EARLY execute stage
    (004) but the LATEST execute stage (006) dropped it → W15L WARNs (never
    errors / never blocks), naming the arm + the stale stage."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(
            tmp, 1, _w13_prereg("delta_set_level_vs_null")
        )
        # Early execute stage carries the headline arm.
        early_results = {
            "primary_model": {"auc": 0.82, "ci_95": [0.70, 0.90]},
            "comparisons": {
                "delta_set_level_vs_null": {"delta": 0.839, "ci_95": [0.70, 0.95]},
            },
        }
        _write_execute_stage_at(
            tmp, 4, early_results,
            "AUC 0.82 CI [0.70, 0.90]\nΔ=0.839 CI [0.70, 0.95]\n",
        )
        # Latest execute stage dropped it (re-ran without the set-level arm).
        latest_results = {
            "primary_model": {"auc": 0.81, "ci_95": [0.69, 0.89]},
            "comparisons": {
                "delta_other_vs_null": {"delta": 0.10, "ci_95": [0.03, 0.17]},
            },
        }
        stage = _write_execute_stage_at(
            tmp, 6, latest_results,
            "AUC 0.81 CI [0.69, 0.89]\nΔ=0.10 CI [0.03, 0.17]\n",
        )
        r = check_execute(stage)
        w15l = [
            w for w in r.warnings
            if w.startswith(LatestExecuteProvenanceGate.GATE_ID + ":")
        ]
        assert len(w15l) == 1, r.warnings
        assert "comparisons.delta_set_level_vs_null" in w15l[0], w15l[0]
        assert "004_execute" in w15l[0], w15l[0]
        # WARN-only: never an error, never the GATE_ID in errors.
        assert not any(
            LatestExecuteProvenanceGate.GATE_ID in e for e in r.errors
        ), r.errors
        assert LatestExecuteProvenanceGate.SEVERITY == "warn"


def test_w15l_arm_present_in_latest_is_noop():
    """The healthy case: the named arm IS present in the latest execute stage
    → W15L silent even though an earlier stage also has it."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(
            tmp, 1, _w13_prereg("delta_set_level_vs_null")
        )
        results = {
            "primary_model": {"auc": 0.82, "ci_95": [0.70, 0.90]},
            "comparisons": {
                "delta_set_level_vs_null": {"delta": 0.839, "ci_95": [0.70, 0.95]},
            },
        }
        analysis = "AUC 0.82 CI [0.70, 0.90]\nΔ=0.839 CI [0.70, 0.95]\n"
        _write_execute_stage_at(tmp, 4, results, analysis)
        stage = _write_execute_stage_at(tmp, 6, results, analysis)
        r = check_execute(stage)
        assert not any(
            LatestExecuteProvenanceGate.GATE_ID in w for w in r.warnings
        ), r.warnings


def test_w15l_single_execute_run_is_noop():
    """A single-execute run cannot fracture across stages → W15L no-op even if
    the arm is absent from that stage (that uniform absence is W13's job)."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(
            tmp, 1, _w13_prereg("delta_set_level_vs_null")
        )
        # Only one execute stage, and it does NOT carry the arm.
        results = {
            "primary_model": {"auc": 0.82, "ci_95": [0.70, 0.90]},
            "comparisons": {
                "delta_other_vs_null": {"delta": 0.10, "ci_95": [0.03, 0.17]},
            },
        }
        stage = _write_execute_stage_at(
            tmp, 2, results, "AUC 0.82 CI [0.70, 0.90]\nΔ=0.10 CI [0.03, 0.17]\n"
        )
        r = check_execute(stage)
        assert not any(
            LatestExecuteProvenanceGate.GATE_ID in w for w in r.warnings
        ), r.warnings


def test_w15l_no_load_bearing_arm_is_noop():
    """No load_bearing_arm declared → W15L no-op even across multiple execute
    stages where an arm appears then disappears."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(tmp, 1, _w13_prereg(_W13_OMIT))
        early = {
            "primary_model": {"auc": 0.82, "ci_95": [0.70, 0.90]},
            "comparisons": {
                "delta_x_vs_null": {"delta": 0.5, "ci_95": [0.3, 0.7]},
            },
        }
        _write_execute_stage_at(
            tmp, 4, early, "AUC 0.82 CI [0.70, 0.90]\nΔ=0.5 CI [0.3, 0.7]\n"
        )
        latest = {
            "primary_model": {"auc": 0.81, "ci_95": [0.69, 0.89]},
            "comparisons": {
                "delta_y_vs_null": {"delta": 0.2, "ci_95": [0.1, 0.3]},
            },
        }
        stage = _write_execute_stage_at(
            tmp, 6, latest, "AUC 0.81 CI [0.69, 0.89]\nΔ=0.2 CI [0.1, 0.3]\n"
        )
        r = check_execute(stage)
        assert not any(
            LatestExecuteProvenanceGate.GATE_ID in w for w in r.warnings
        ), r.warnings


def test_w15l_arm_never_ran_anywhere_is_noop():
    """The arm is absent from the latest stage AND from every earlier execute
    stage (it simply never ran) → no fracture → W15L no-op (W13 flags the
    incompleteness)."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(
            tmp, 1, _w13_prereg("delta_set_level_vs_null")
        )
        early = {
            "primary_model": {"auc": 0.82, "ci_95": [0.70, 0.90]},
            "comparisons": {
                "delta_other_vs_null": {"delta": 0.10, "ci_95": [0.03, 0.17]},
            },
        }
        _write_execute_stage_at(
            tmp, 4, early, "AUC 0.82 CI [0.70, 0.90]\nΔ=0.10 CI [0.03, 0.17]\n"
        )
        latest = {
            "primary_model": {"auc": 0.81, "ci_95": [0.69, 0.89]},
            "comparisons": {
                "delta_another_vs_null": {"delta": 0.20, "ci_95": [0.10, 0.30]},
            },
        }
        stage = _write_execute_stage_at(
            tmp, 6, latest, "AUC 0.81 CI [0.69, 0.89]\nΔ=0.20 CI [0.10, 0.30]\n"
        )
        r = check_execute(stage)
        assert not any(
            LatestExecuteProvenanceGate.GATE_ID in w for w in r.warnings
        ), r.warnings


def test_w15l_unit_helper_warns_on_fracture():
    """Unit-level: the gate fn appends exactly one WARN to its sink on a
    fracture and nothing on the healthy path."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(
            tmp, 1, _w13_prereg("delta_lb_vs_null")
        )
        _write_execute_stage_at(
            tmp, 4,
            {"comparisons": {"delta_lb_vs_null": {"delta": 0.5, "ci_95": [0.3, 0.7]}}},
            "Δ=0.5 CI [0.3, 0.7]\n",
        )
        latest = tmp / "006_execute"
        latest.mkdir(parents=True, exist_ok=True)
        latest_results = {
            "comparisons": {"delta_other_vs_null": {"delta": 0.1, "ci_95": [0.0, 0.2]}},
        }
        warns: list[str] = []
        _check_w15_latest_execute_provenance(latest, latest_results, warns)
        assert len(warns) == 1, warns
        assert warns[0].startswith(LatestExecuteProvenanceGate.GATE_ID + ":")
        assert "delta_lb_vs_null" in warns[0]


# ---------------------------------------------------------------------------
# W23 — decisive-arm zero-power gate (advisory / warn-only sibling of W17).
# WARNs when the pre-registered discriminating_arm, though it RAN, was
# structurally degenerate (power == 0): a constant decisive input across the
# comparison panel (the posttrain-regime coverage-numerator c==1 defect), an
# explicit zero power / all-zero power_curve / null MDE-at-target, or a
# full-range bootstrap CI. No-op when no discriminating_arm is declared, the arm
# is absent, the arm varied / had power, or a fired-but-null arm.
# ---------------------------------------------------------------------------

_W23_DISC_ARM = "delta_regimeprobe_vs_behavioural"
_W23_OMIT = object()


def _w23_prereg(discriminating_arm=_W23_OMIT) -> dict:
    """Minimal preregistration.json naming a discriminating_arm (and a
    load_bearing_arm so W13/W17 are realistic). Omit via _W23_OMIT."""
    prereg: dict = {
        "primary_hypothesis": "H1",
        "primary_metric": "delta",
        "mde": 0.05,
        "load_bearing_arm": "delta_headline_vs_null",
    }
    if discriminating_arm is not _W23_OMIT:
        prereg["discriminating_arm"] = discriminating_arm
    return prereg


def _w23_results(disc_arm_block: dict, *, panel=None, power_analysis=None) -> dict:
    """results.json carrying the headline arm (W13 passes), the named
    discriminating arm block, and optionally a top-level regime-style panel /
    run-level power_analysis."""
    results: dict = {
        "primary_model": {"auc": 0.82, "ci_95": [0.70, 0.90]},
        "comparisons": {
            "delta_headline_vs_null": {"delta": 0.12, "ci_95": [0.05, 0.19]},
            _W23_DISC_ARM: disc_arm_block,
        },
        # central_question framed scoped so W17 does not ERROR (W23 must work
        # independently of W17's resolved/scoped severity).
        "central_question": {"discriminating_arm_resolution": "scoped"},
    }
    if panel is not None:
        results["panel"] = panel
    if power_analysis is not None:
        results["power_analysis"] = power_analysis
    return results


def _w23_regime_panel(n: int = 10, c_value=1) -> list[dict]:
    """A regime-style per-model panel: the coverage numerator `c` is CONSTANT
    (the real c==1 defect) while the denominator `d` (and the ratio c_over_d)
    VARY — the exact trap W23 keys on the numerator, not the ratio."""
    panel = []
    for i in range(n):
        d = 3 + i * 4  # varies per model
        panel.append({
            "model": f"model_{i}",
            "c": c_value,           # CONSTANT across all units (zero variance)
            "d": d,                 # varies
            "c_over_d": c_value / d,  # varies (the misleading per-model ratio)
            "sign": 1 if i % 2 == 0 else -1,
        })
    return panel


def test_w23_constant_coverage_numerator_warns():
    """The real regime defect: discriminating_arm RAN, but its decisive input
    `c` is CONSTANT (==1) across all 10 models → W23 WARNs (zero power)."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(tmp, 1, _w23_prereg(_W23_DISC_ARM))
        # The discriminating arm RAN and returned a (null) delta — but the panel
        # numerator is constant, so the predictor had no power.
        results = _w23_results(
            {"delta": 0.0, "ci_95": [-0.4, 0.2], "p_directional": 0.94},
            panel=_w23_regime_panel(10, c_value=1),
        )
        analysis = "AUC 0.82 CI [0.70, 0.90]\nΔ=0.12 CI [0.05, 0.19]\nΔ=0.0 CI [-0.4, 0.2]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        w23 = [w for w in r.warnings if w.startswith(DecisiveArmPowerGate.GATE_ID + ":")]
        assert len(w23) == 1, r.warnings
        assert _W23_DISC_ARM in w23[0], w23[0]
        assert "`c`" in w23[0] and "CONSTANT" in w23[0], w23[0]
        # WARN-only: never an error.
        assert not any(DecisiveArmPowerGate.GATE_ID in e for e in r.errors), r.errors
        assert DecisiveArmPowerGate.SEVERITY == "warn"


def test_w23_zero_power_curve_warns():
    """The discriminating arm reports a power_analysis whose every power_curve
    entry is power == 0 (+ a null MDE-at-target) → W23 WARNs (zero power)."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(tmp, 1, _w23_prereg(_W23_DISC_ARM))
        power_analysis = {
            "n_models": 10,
            "alpha": 0.05,
            "power_target": 0.8,
            "realised_balance": {
                "power_curve": [
                    {"sep": 0.0, "power": 0.0},
                    {"sep": 1.0, "power": 0.0},
                    {"sep": 4.0, "power": 0.0},
                    {"sep": 8.0, "power": 0.0},
                ],
                "mde_sep_at_power_target": None,
                "power_to_detect_perfect_boundary": 0.0,
            },
        }
        results = _w23_results(
            {"delta": 0.0, "ci_95": [-0.4, 0.2]},
            power_analysis=power_analysis,
        )
        analysis = "AUC 0.82 CI [0.70, 0.90]\nΔ=0.12 CI [0.05, 0.19]\nΔ=0.0 CI [-0.4, 0.2]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        w23 = [w for w in r.warnings if w.startswith(DecisiveArmPowerGate.GATE_ID + ":")]
        assert len(w23) == 1, r.warnings
        assert "power is zero" in w23[0], w23[0]
        assert not any(DecisiveArmPowerGate.GATE_ID in e for e in r.errors), r.errors


def test_w23_full_range_ci_warns():
    """The discriminating arm's bounded-metric CI spans the entire [0, 1] range
    → no discrimination → W23 WARNs."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(tmp, 1, _w23_prereg(_W23_DISC_ARM))
        results = _w23_results(
            {"accuracy": 0.5, "ci_95": [0.0, 1.0], "delta_vs_chance": 0.0},
        )
        analysis = "AUC 0.82 CI [0.70, 0.90]\nΔ=0.12 CI [0.05, 0.19]\nacc 0.5 CI [0.0, 1.0]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        w23 = [w for w in r.warnings if w.startswith(DecisiveArmPowerGate.GATE_ID + ":")]
        assert len(w23) == 1, r.warnings
        assert "full" in w23[0].lower(), w23[0]
        assert not any(DecisiveArmPowerGate.GATE_ID in e for e in r.errors), r.errors


def test_w23_varying_input_and_fired_null_arm_is_noop():
    """The honest cases that must NOT trip W23:
      (a) a normal varying-numerator panel (real power), AND
      (b) a discriminating arm that FIRED with a real, discriminating CI and
          returned a null effect (power present, effect absent).
    """
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(tmp, 1, _w23_prereg(_W23_DISC_ARM))
        # Panel numerator `c` VARIES across models → real discrimination power.
        panel = [
            {"model": f"m{i}", "c": i + 1, "d": 10, "c_over_d": (i + 1) / 10}
            for i in range(10)
        ]
        # Arm fired with a tight, non-full-range CI and a null point delta — the
        # classic honest-null: it HAD power, it found no effect.
        power_analysis = {
            "realised_balance": {
                "power_curve": [
                    {"sep": 0.0, "power": 0.05},
                    {"sep": 1.0, "power": 0.62},
                    {"sep": 2.0, "power": 0.91},
                ],
                "mde_sep_at_power_target": 1.4,
                "power_to_detect_perfect_boundary": 0.95,
            },
        }
        results = _w23_results(
            {"delta": 0.01, "ci_95": [-0.05, 0.07], "p_directional": 0.62},
            panel=panel,
            power_analysis=power_analysis,
        )
        analysis = (
            "AUC 0.82 CI [0.70, 0.90]\nΔ=0.12 CI [0.05, 0.19]\n"
            "Δ=0.01 CI [-0.05, 0.07]\n"
        )
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert not any(
            DecisiveArmPowerGate.GATE_ID in w for w in r.warnings
        ), r.warnings
        assert not any(DecisiveArmPowerGate.GATE_ID in e for e in r.errors), r.errors


def test_w23_no_discriminating_arm_declared_is_noop():
    """No discriminating_arm declared → W23 no-op (W17 owns the absence-WARN)
    even with a constant panel present."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(tmp, 1, _w23_prereg(_W23_OMIT))
        results = {
            "primary_model": {"auc": 0.82, "ci_95": [0.70, 0.90]},
            "comparisons": {
                "delta_headline_vs_null": {"delta": 0.12, "ci_95": [0.05, 0.19]},
            },
            "panel": _w23_regime_panel(10, c_value=1),
        }
        analysis = "AUC 0.82 CI [0.70, 0.90]\nΔ=0.12 CI [0.05, 0.19]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert not any(
            DecisiveArmPowerGate.GATE_ID in w for w in r.warnings
        ), r.warnings


def test_w23_arm_absent_from_results_is_noop():
    """The named discriminating arm never reached results.json → W23 no-op
    (that incompleteness is W17's job, not a power signal)."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(tmp, 1, _w23_prereg(_W23_DISC_ARM))
        results = {
            "primary_model": {"auc": 0.82, "ci_95": [0.70, 0.90]},
            "comparisons": {
                "delta_headline_vs_null": {"delta": 0.12, "ci_95": [0.05, 0.19]},
            },
            "panel": _w23_regime_panel(10, c_value=1),
        }
        analysis = "AUC 0.82 CI [0.70, 0.90]\nΔ=0.12 CI [0.05, 0.19]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert not any(
            DecisiveArmPowerGate.GATE_ID in w for w in r.warnings
        ), r.warnings


def test_w23_unit_helper_warns_once_on_constant_input():
    """Unit-level: the gate fn appends exactly one WARN on the constant-input
    signature and nothing when the numerator varies."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(tmp, 1, _w23_prereg(_W23_DISC_ARM))
        stage = tmp / "002_execute"
        stage.mkdir(parents=True, exist_ok=True)
        # Constant numerator → one WARN.
        degenerate = _w23_results(
            {"delta": 0.0, "ci_95": [-0.4, 0.2]},
            panel=_w23_regime_panel(10, c_value=1),
        )
        warns: list[str] = []
        _check_w23_decisive_arm_power(stage, degenerate, warns)
        assert len(warns) == 1, warns
        assert warns[0].startswith(DecisiveArmPowerGate.GATE_ID + ":")
        # Two-unit panel is below MIN_UNITS → not trusted → no WARN.
        small = _w23_results(
            {"delta": 0.0, "ci_95": [-0.4, 0.2]},
            panel=[{"c": 1, "d": 3}, {"c": 1, "d": 5}],
        )
        warns2: list[str] = []
        _check_w23_decisive_arm_power(stage, small, warns2)
        assert warns2 == [], warns2


# ---------------------------------------------------------------------------
# W25 — effect-clears-own-MDE gate (advisory WARN-only sibling of W2/W17).
# W2 verifies a numeric MDE is DECLARED; W25 verifies the load-bearing/headline
# effect actually CLEARS it. Tier 1 (sharp) on a declared load_bearing_arm;
# Tier 2 (advisory fallback) on the `delta_primary_vs_*` family when no
# load_bearing_arm is declared. WARN-only, no-op when the effect clears the MDE.
# Fixtures are built from the REAL defect shapes (sensitive-memory Δ=+0.0043 vs
# mde 0.03; intersectional +0.0013 vs 0.02; example-study-d7 +0.076 vs 0.018 clean).
# ---------------------------------------------------------------------------

# The real-defect headline magnitudes (sensitive-memory parent).
_W25_SUBMDE_DELTA = 0.0043
_W25_DECLARED_MDE = 0.03


def _w25_prereg(*, mde=_W25_DECLARED_MDE, load_bearing_arm=None) -> dict:
    """Minimal preregistration.json for W25. `mde` may be a number / None /
    omitted (pass `mde=...` sentinel). `load_bearing_arm` defaults to None
    (omitted) → exercises the Tier-2 primary-contrast fallback."""
    prereg: dict = {"primary_hypothesis": "H1", "primary_metric": "delta"}
    if mde is not None:
        prereg["mde"] = mde
    if load_bearing_arm is not None:
        prereg["load_bearing_arm"] = load_bearing_arm
    return prereg


def test_w25_submde_load_bearing_arm_warns():
    """TIER 1: a DECLARED load_bearing_arm whose own headline effect is sub-MDE
    (Δ=+0.0043 vs declared mde 0.03 — the 7× sub-MDE sensitive-memory defect)
    → W25 WARNs that the headline does not clear its own MDE."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(
            tmp, 1, _w25_prereg(load_bearing_arm="delta_primary_vs_relevance_only")
        )
        results = {
            "primary_model": {"hv": 0.057, "ci_95": [0.051, 0.061]},
            "comparisons": {
                "delta_primary_vs_relevance_only": {
                    "delta": _W25_SUBMDE_DELTA, "ci_95": [0.0016, 0.0078],
                },
            },
        }
        analysis = "HV 0.057 CI [0.051, 0.061]\nΔ=0.0043 CI [0.0016, 0.0078]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        w25 = [w for w in r.warnings if w.startswith(EffectClearsMdeGate.GATE_ID + ":")]
        assert len(w25) == 1, r.warnings
        assert "load_bearing_arm" in w25[0], w25[0]
        assert "BELOW it" in w25[0], w25[0]
        assert "formally undetectable" in w25[0], w25[0]
        # WARN-only: never an error.
        assert not any(EffectClearsMdeGate.GATE_ID in e for e in r.errors), r.errors
        assert EffectClearsMdeGate.SEVERITY == "warn"


def test_w25_submde_primary_contrast_fallback_warns():
    """TIER 2: NO load_bearing_arm declared (the under-declared confirmatory
    plan W21 WARNs about — and the exact shape of the real defects), but a
    numeric MDE is, and EVERY `delta_primary_vs_*` arm is sub-MDE (intersectional
    +0.0013-vs-0.02: largest primary contrast 0.0140 still < 0.02) → advisory
    WARN keyed on the largest primary contrast."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(tmp, 1, _w25_prereg(mde=0.02))
        results = {
            "primary_model": {"gap_reduction": 0.0013, "ci_95": [0.0006, 0.0014]},
            "comparisons": {
                "delta_primary_vs_stacked": {"delta": 0.0013, "ci_95": [0.0006, 0.0014]},
                "delta_primary_vs_best_combination": {"delta": 0.0009, "ci_95": [0.0002, 0.001]},
                "delta_primary_vs_random_subspace": {"delta": 0.0140, "ci_95": [0.012, 0.015]},
            },
        }
        analysis = "gap 0.0013 CI [0.0006, 0.0014]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        w25 = [w for w in r.warnings if w.startswith(EffectClearsMdeGate.GATE_ID + ":")]
        assert len(w25) == 1, r.warnings
        # Fallback names the LARGEST primary contrast (still sub-MDE) + is advisory.
        assert "delta_primary_vs_random_subspace" in w25[0], w25[0]
        assert "primary-contrast" in w25[0], w25[0]
        assert "ADVISORY" in w25[0], w25[0]
        assert not any(EffectClearsMdeGate.GATE_ID in e for e in r.errors), r.errors


def test_w25_effect_clears_mde_is_noop():
    """A headline effect that CLEARS its own MDE (example-study-d7-style: a primary
    contrast Δ=+0.076 vs mde 0.018) → W25 no-op (a powered contrast exists)."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(tmp, 1, _w25_prereg(mde=0.018))
        results = {
            "primary_model": {"gain": 0.076, "ci_95": [0.068, 0.083]},
            "comparisons": {
                "delta_primary_vs_unsteered": {"delta": 0.076, "ci_95": [0.068, 0.083]},
                "delta_primary_vs_difficulty_control": {"delta": -0.012, "ci_95": [-0.02, -0.004]},
            },
        }
        analysis = "gain 0.076 CI [0.068, 0.083]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert not any(
            EffectClearsMdeGate.GATE_ID in w for w in r.warnings
        ), r.warnings
        assert not any(EffectClearsMdeGate.GATE_ID in e for e in r.errors), r.errors


def test_w25_no_mde_declared_is_noop():
    """No numeric MDE declared → W25 no-op (declaration is W2's job), even when
    every primary contrast would be sub-MDE."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        # mde=None → field omitted entirely.
        _write_plan_with_preregistration(tmp, 1, _w25_prereg(mde=None))
        results = {
            "primary_model": {"hv": 0.057, "ci_95": [0.051, 0.061]},
            "comparisons": {
                "delta_primary_vs_relevance_only": {"delta": 0.0043, "ci_95": [0.0016, 0.0078]},
            },
        }
        analysis = "HV 0.057 CI [0.051, 0.061]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert not any(
            EffectClearsMdeGate.GATE_ID in w for w in r.warnings
        ), r.warnings


def test_w25_no_load_bearing_arm_and_no_primary_contrast_is_noop():
    """A numeric MDE is declared, no load_bearing_arm, and NO
    `delta_primary_vs_*` primary-contrast arm exists (only unrelated
    comparisons) → W25 no-op (nothing resolvable to test)."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(tmp, 1, _w25_prereg(mde=0.03))
        results = {
            "primary_model": {"hv": 0.057, "ci_95": [0.051, 0.061]},
            "comparisons": {
                "delta_4action_vs_2action": {"delta": 0.0043, "ci_95": [0.001, 0.008]},
            },
        }
        analysis = "HV 0.057 CI [0.051, 0.061]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert not any(
            EffectClearsMdeGate.GATE_ID in w for w in r.warnings
        ), r.warnings


def test_w25_declared_arm_clears_does_not_fall_through_to_fallback():
    """A DECLARED load_bearing_arm that CLEARS its MDE must make Tier 1
    authoritative — W25 must NOT fall through to the Tier-2 primary-contrast
    fallback even if some OTHER primary contrast is sub-MDE."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(
            tmp, 1, _w25_prereg(mde=0.02, load_bearing_arm="delta_primary_vs_strong")
        )
        results = {
            "primary_model": {"m": 0.1, "ci_95": [0.08, 0.12]},
            "comparisons": {
                # The NAMED headline arm clears the MDE.
                "delta_primary_vs_strong": {"delta": 0.10, "ci_95": [0.08, 0.12]},
                # A secondary primary contrast is sub-MDE — must NOT trigger Tier 2.
                "delta_primary_vs_weak": {"delta": 0.001, "ci_95": [0.0, 0.002]},
            },
        }
        analysis = "m 0.1 CI [0.08, 0.12]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert not any(
            EffectClearsMdeGate.GATE_ID in w for w in r.warnings
        ), r.warnings


def test_w25_unit_helper_warns_once():
    """Unit-level: the gate fn appends exactly one Tier-1 WARN on a sub-MDE
    declared arm, and nothing when it clears."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(
            tmp, 1, _w25_prereg(load_bearing_arm="delta_primary_vs_relevance_only")
        )
        stage = tmp / "002_execute"
        stage.mkdir(parents=True, exist_ok=True)
        submde = {
            "comparisons": {
                "delta_primary_vs_relevance_only": {"delta": _W25_SUBMDE_DELTA, "ci_95": [0.001, 0.008]},
            },
        }
        warns: list[str] = []
        _check_w25_effect_clears_mde(stage, submde, warns)
        assert len(warns) == 1, warns
        assert warns[0].startswith(EffectClearsMdeGate.GATE_ID + ":")
        clears = {
            "comparisons": {
                "delta_primary_vs_relevance_only": {"delta": 0.10, "ci_95": [0.08, 0.12]},
            },
        }
        warns2: list[str] = []
        _check_w25_effect_clears_mde(stage, clears, warns2)
        assert warns2 == [], warns2


# ---------------------------------------------------------------------------
# W24 — construct-validity gate (advisory / warn-only sibling of W20/W23/W25).
# WARNs when a LOAD-BEARING learned/surrogate axis carries a pre-registered
# `construct_validity` requirement (metric + threshold) whose MEASURED stat
# FAILS — point below threshold OR (for a correlation) a CI spanning the null
# (the sensitive-memory appropriateness-axis rho=0.276 vs rho>=0.6 / CI-spans-0
# defect, left load-bearing yet verdict `proceed`). No-op when no
# construct_validity object is declared, the axis is not load-bearing, the stat
# is unresolvable, or the validation PASSES.
# ---------------------------------------------------------------------------

# The real sensitive-memory H4 validity-fail: rho=0.276, CI [-0.119, 0.594].
_W24_FAIL_RHO = 0.2764094143404488
_W24_FAIL_CI = [-0.11890588776964844, 0.5935736846218347]
_W24_METRIC_PATH = "h4_judge_human_agreement.spearman_rho"
_W24_OMIT = object()


def _w24_prereg(construct_validity=_W24_OMIT) -> dict:
    """Minimal preregistration.json. A `construct_validity` object is attached
    to an H4 hypothesis (the validity-gate hypothesis) when provided; omit via
    _W24_OMIT to exercise the back-compat no-op."""
    prereg: dict = {
        "primary_hypothesis": "H1",
        "primary_metric": "dominated_hypervolume",
        "hypotheses": [
            {"id": "H1", "decision_rule": "HV delta CI excludes 0"},
            {"id": "H4", "decision_rule": "validity gate: rho >= 0.6"},
        ],
    }
    if construct_validity is not _W24_OMIT:
        prereg["hypotheses"][1]["construct_validity"] = construct_validity
    return prereg


def _w24_results(*, rho=_W24_FAIL_RHO, ci=None) -> dict:
    """results.json carrying the H4 judge/human agreement validity block at the
    canonical dotted path, plus a healthy headline arm so unrelated gates pass."""
    block: dict = {"spearman_rho": rho}
    if ci is not None:
        block["ci_95"] = ci
    return {
        "primary_model": {"hv": 0.057, "ci_95": [0.051, 0.061]},
        "comparisons": {
            "delta_primary_vs_relevance_only": {"delta": 0.08, "ci_95": [0.05, 0.11]},
        },
        "h4_judge_human_agreement": block,
    }


def test_w24_load_bearing_validity_fail_warns():
    """The real sensitive-memory defect: a LOAD-BEARING appropriateness axis
    with a pre-registered rho>=0.6 validity requirement, measured rho=0.276 with
    a CI spanning 0 → W24 WARNs that the headline rests on an unvalidated
    construct (must be framed preliminary / pending-validation)."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        cv = {"metric": _W24_METRIC_PATH, "min_rho": 0.6, "load_bearing": True}
        _write_plan_with_preregistration(tmp, 1, _w24_prereg(cv))
        results = _w24_results(rho=_W24_FAIL_RHO, ci=_W24_FAIL_CI)
        analysis = "rho 0.276 CI [-0.119, 0.594]\nHV 0.057 CI [0.051, 0.061]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        w24 = [w for w in r.warnings if w.startswith(ConstructValidityGate.GATE_ID + ":")]
        assert len(w24) == 1, r.warnings
        assert _W24_METRIC_PATH in w24[0], w24[0]
        assert "validity check" in w24[0] and "FAILED" in w24[0], w24[0]
        assert "PRELIMINARY" in w24[0] or "PENDING-VALIDATION" in w24[0], w24[0]
        # WARN-only: never an error, never blocks.
        assert not any(ConstructValidityGate.GATE_ID in e for e in r.errors), r.errors
        assert ConstructValidityGate.SEVERITY == "warn"


def test_w24_validity_passes_is_noop():
    """A genuinely PASSING validation (rho=0.8, CI [0.65, 0.9] clears the
    threshold AND excludes 0) → W24 no-op (a passing construct must NOT trip)."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        cv = {"metric": _W24_METRIC_PATH, "min_rho": 0.6}
        _write_plan_with_preregistration(tmp, 1, _w24_prereg(cv))
        results = _w24_results(rho=0.8, ci=[0.65, 0.9])
        stage = _make_execute_stage(tmp, results, "rho 0.8 CI [0.65, 0.9]\n")
        r = check_execute(stage)
        assert not any(
            ConstructValidityGate.GATE_ID in w for w in r.warnings
        ), r.warnings
        assert not any(ConstructValidityGate.GATE_ID in e for e in r.errors), r.errors


def test_w24_no_construct_validity_declared_is_noop():
    """No `construct_validity` object declared (every existing / in-flight run)
    → W24 no-op (back-compatible), even when the validity block is present and
    would fail a threshold."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(tmp, 1, _w24_prereg(_W24_OMIT))
        results = _w24_results(rho=_W24_FAIL_RHO, ci=_W24_FAIL_CI)
        stage = _make_execute_stage(tmp, results, "rho 0.276\n")
        r = check_execute(stage)
        assert not any(
            ConstructValidityGate.GATE_ID in w for w in r.warnings
        ), r.warnings


def test_w24_not_load_bearing_is_noop():
    """An explicit `load_bearing: false` opts the axis out → W24 no-op even
    though the measured validity stat fails the threshold."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        cv = {"metric": _W24_METRIC_PATH, "min_rho": 0.6, "load_bearing": False}
        _write_plan_with_preregistration(tmp, 1, _w24_prereg(cv))
        results = _w24_results(rho=_W24_FAIL_RHO, ci=_W24_FAIL_CI)
        stage = _make_execute_stage(tmp, results, "rho 0.276\n")
        r = check_execute(stage)
        assert not any(
            ConstructValidityGate.GATE_ID in w for w in r.warnings
        ), r.warnings


def test_w24_ci_spans_null_fails_even_if_point_clears():
    """A correlation point estimate that CLEARS the threshold but whose CI still
    SPANS the null (rho=0.62 but CI [-0.05, 0.9]) is a validity FAIL — the
    construct is statistically indistinguishable from no agreement → W24 WARNs.
    Also exercises the CI-spans-null signature independently of the point test.
    """
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        cv = {"metric": _W24_METRIC_PATH, "min_rho": 0.6}
        _write_plan_with_preregistration(tmp, 1, _w24_prereg(cv))
        results = _w24_results(rho=0.62, ci=[-0.05, 0.9])
        stage = _make_execute_stage(tmp, results, "rho 0.62 CI [-0.05, 0.9]\n")
        r = check_execute(stage)
        w24 = [w for w in r.warnings if w.startswith(ConstructValidityGate.GATE_ID + ":")]
        assert len(w24) == 1, r.warnings
        assert "SPANS the null" in w24[0], w24[0]
        assert not any(ConstructValidityGate.GATE_ID in e for e in r.errors), r.errors


def test_w24_unit_helper_warns_once_and_warn_only():
    """Unit-level: the gate fn appends exactly one WARN on a load-bearing
    validity-fail, nothing on a pass, and is WARN-severity (never errors/routes).
    """
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        cv = {"metric": _W24_METRIC_PATH, "min_rho": 0.6}
        _write_plan_with_preregistration(tmp, 1, _w24_prereg(cv))
        stage = tmp / "002_execute"
        stage.mkdir(parents=True, exist_ok=True)
        fail = _w24_results(rho=_W24_FAIL_RHO, ci=_W24_FAIL_CI)
        warns: list[str] = []
        _check_w24_construct_validity(stage, fail, warns)
        assert len(warns) == 1, warns
        assert warns[0].startswith(ConstructValidityGate.GATE_ID + ":")
        ok = _w24_results(rho=0.85, ci=[0.7, 0.95])
        warns2: list[str] = []
        _check_w24_construct_validity(stage, ok, warns2)
        assert warns2 == [], warns2
        assert ConstructValidityGate.SEVERITY == "warn"


# ---------------------------------------------------------------------------
# W16 — degenerate-metric headline guard (load-bearing-gated). ERROR when a
# null-boundary + collapsed-CI metric is the SOLE evidence for the headline
# arm; WARN when a disambiguating companion is present; no-op for controls,
# unnamed arms, and non-degenerate metrics.
# ---------------------------------------------------------------------------

def test_execute_w16_degenerate_sole_evidence_is_error():
    """The d3 failure: the load_bearing_arm's sole evidence is auc=0.5 with
    a point CI [0.5, 0.5] → W16 ERRORs (degenerate predictor signature)."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(
            tmp, 1, _w13_prereg("delta_steered_vs_unsteered")
        )
        results = {
            # primary_model headline metric is pinned at chance with a
            # collapsed CI — and it is the SOLE point-estimate in the block.
            "primary_model": {"auc": 0.5, "ci_95": [0.5, 0.5]},
            "comparisons": {
                "delta_steered_vs_unsteered": {
                    "delta": 0.12, "ci_95": [0.05, 0.19]
                },
            },
        }
        analysis = "AUC 0.50 CI [0.50, 0.50]\nΔ=0.12 CI [0.05, 0.19]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert not r.ok, r.summary()
        assert any(
            "W16" in e and "primary_model.auc" in e for e in r.errors
        ), r.errors
        # SOLE-evidence path is a hard ERROR, not a warning.
        assert not any("W16" in w for w in r.warnings), r.warnings


def test_execute_w16_degenerate_comparison_arm_is_error():
    """The degenerate metric can live in the named comparison arm itself:
    delta_*.auc=0.5 / ci=[0.5,0.5] as the sole evidence → W16 ERROR."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(
            tmp, 1, _w13_prereg("delta_gate_vs_nogate")
        )
        results = {
            "primary_model": {"auc": 0.82, "ci_95": [0.70, 0.90]},
            "comparisons": {
                # The headline arm itself is degenerate: the gate's
                # selectivity AUC is pinned at chance with a point CI.
                "delta_gate_vs_nogate": {"auc": 0.5, "ci_95": [0.5, 0.5]},
            },
        }
        analysis = "AUC 0.82 CI [0.70, 0.90]\nGate AUC 0.50 CI [0.50, 0.50]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert not r.ok, r.summary()
        assert any(
            "W16" in e and "comparisons.delta_gate_vs_nogate.auc" in e
            for e in r.errors
        ), r.errors


def test_execute_w16_planted_null_control_is_not_flagged():
    """A planted-null control SHOULD read auc=0.5 — that is the EXPECTED
    correct result. A block tagged role=null_control is exempt → no W16
    finding even though its metric is degenerate."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(
            tmp, 1, _w13_prereg("delta_steered_vs_unsteered")
        )
        results = {
            # The headline analysis is healthy (real AUC, real CI). The
            # degenerate 0.5 sits on an explicitly-labelled planted-null
            # control, which is supposed to read chance.
            "primary_model": {
                "auc": 0.5, "ci_95": [0.5, 0.5], "role": "null_control",
            },
            "comparisons": {
                "delta_steered_vs_unsteered": {
                    "delta": 0.12, "ci_95": [0.05, 0.19]
                },
            },
        }
        analysis = "Null control AUC 0.50 CI [0.50, 0.50]\nΔ=0.12 CI [0.05, 0.19]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert not any("W16" in e for e in r.errors), r.errors
        assert not any("W16" in w for w in r.warnings), r.warnings


def test_execute_w16_degenerate_with_companion_is_warn():
    """A degenerate metric WITH a non-degenerate companion measurement in
    the same block (a separating signal) → W16 WARNs (advisory), not ERROR.
    The arm still carries a measurement that discriminates the claim."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(
            tmp, 1, _w13_prereg("delta_steered_vs_unsteered")
        )
        results = {
            # auc pinned at chance (degenerate) BUT a non-degenerate
            # spearman correlation sits alongside it as a companion that
            # could separate mechanism from uninformative predictor.
            "primary_model": {
                "auc": 0.5, "spearman": 0.41, "ci_95": [0.5, 0.5],
            },
            "comparisons": {
                "delta_steered_vs_unsteered": {
                    "delta": 0.12, "ci_95": [0.05, 0.19]
                },
            },
        }
        analysis = "AUC 0.50 CI [0.50, 0.50]\nspearman 0.41\nΔ=0.12 CI [0.05, 0.19]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert any(
            "W16" in w and "primary_model.auc" in w for w in r.warnings
        ), r.warnings
        # Companion present → advisory, never a hard ERROR.
        assert not any("W16" in e for e in r.errors), r.errors


def test_execute_w16_healthy_metric_passes_silently():
    """A real headline metric (auc=0.82, CI with width) → W16 silent."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(
            tmp, 1, _w13_prereg("delta_steered_vs_unsteered")
        )
        results = {
            "primary_model": {"auc": 0.82, "ci_95": [0.70, 0.90]},
            "comparisons": {
                "delta_steered_vs_unsteered": {
                    "delta": 0.12, "ci_95": [0.05, 0.19]
                },
            },
        }
        analysis = "AUC 0.82 CI [0.70, 0.90]\nΔ=0.12 CI [0.05, 0.19]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert r.ok, r.summary()
        assert not any("W16" in e for e in r.errors)
        assert not any("W16" in w for w in r.warnings)


def test_execute_w16_boundary_value_with_real_ci_is_not_flagged():
    """A metric AT 0.5 but with a REAL-width CI is not degenerate — only the
    pinned-value-AND-collapsed-CI conjunction trips W16. A 0.5 point estimate
    with [0.42, 0.58] is just a null result honestly reported, not a constant
    predictor."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(
            tmp, 1, _w13_prereg("delta_steered_vs_unsteered")
        )
        results = {
            "primary_model": {"auc": 0.5, "ci_95": [0.42, 0.58]},
            "comparisons": {
                "delta_steered_vs_unsteered": {
                    "delta": 0.12, "ci_95": [0.05, 0.19]
                },
            },
        }
        analysis = "AUC 0.50 CI [0.42, 0.58]\nΔ=0.12 CI [0.05, 0.19]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert not any("W16" in e for e in r.errors), r.errors
        assert not any("W16" in w for w in r.warnings), r.warnings


def test_execute_w16_no_load_bearing_arm_is_noop():
    """Backward compat: no load_bearing_arm declared → W16 no-op even if the
    primary metric is degenerate (W16 only judges declared headline arms)."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(tmp, 1, _w13_prereg(_W13_OMIT))
        results = {"primary_model": {"auc": 0.5, "ci_95": [0.5, 0.5]}}
        analysis = "AUC 0.50 CI [0.50, 0.50]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert not any("W16" in e for e in r.errors), r.errors
        assert not any("W16" in w for w in r.warnings), r.warnings


def test_execute_w16_correlation_at_zero_is_flagged():
    """The null boundary for a correlation metric is 0.0, not 0.5: a
    spearman pinned at 0.0 with a collapsed CI is degenerate too."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(
            tmp, 1, _w13_prereg("delta_steered_vs_unsteered")
        )
        results = {
            "primary_model": {"spearman": 0.0, "ci_95": [0.0, 0.0]},
            "comparisons": {
                "delta_steered_vs_unsteered": {
                    "delta": 0.12, "ci_95": [0.05, 0.19]
                },
            },
        }
        analysis = "spearman 0.00 CI [0.00, 0.00]\nΔ=0.12 CI [0.05, 0.19]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert any(
            "W16" in e and "primary_model.spearman" in e for e in r.errors
        ), r.errors


# ---------------------------------------------------------------------------
# W17 — discriminating-arm resolution gate (the decisive-result lever; sibling of
# W13). ERROR when the registered discriminating_arm is unrun AND the central
# question is framed as resolved; WARN when scoped (the honest path) or when
# no discriminating_arm is registered. Silent pass when the arm ran.
# ---------------------------------------------------------------------------

_W17_OMIT = object()


def _w17_prereg(discriminating_arm) -> dict:
    """Minimal preregistration.json carrying a discriminating_arm value.
    Pass the sentinel _W17_OMIT to omit the field entirely. Always carries
    a load_bearing_arm so the prereg is realistic (W13 also runs)."""
    prereg: dict = {
        "primary_hypothesis": "H1",
        "primary_metric": "delta",
        "mde": 0.05,
        "load_bearing_arm": "delta_headline_vs_null",
    }
    if discriminating_arm is not _W17_OMIT:
        prereg["discriminating_arm"] = discriminating_arm
    return prereg


def _w17_results(comparisons: dict, framing=_W17_OMIT) -> dict:
    """results.json with a headline arm present (so W13 passes) and the
    given comparisons block. Optionally carry a central_question framing."""
    comps = {"delta_headline_vs_null": {"delta": 0.12, "ci_95": [0.05, 0.19]}}
    comps.update(comparisons)
    results: dict = {
        "primary_model": {"auc": 0.82, "ci_95": [0.70, 0.90]},
        "comparisons": comps,
    }
    if framing is not _W17_OMIT:
        results["central_question"] = {
            "discriminating_arm_resolution": framing
        }
    return results


def test_execute_w17_discriminating_arm_present_passes_silently():
    """Case (a): the registered discriminating_arm HAS a result; framing
    resolved → W17 silent pass (the deciding experiment ran)."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(
            tmp, 1, _w17_prereg("delta_perceiver_layer_steered")
        )
        results = _w17_results(
            {"delta_perceiver_layer_steered": {"delta": 0.2, "ci_95": [0.1, 0.3]}},
            framing="resolved",
        )
        analysis = (
            "AUC 0.82 CI [0.70, 0.90]\nΔ=0.12 CI [0.05, 0.19]\n"
            "Δ=0.2 CI [0.1, 0.3]\n"
        )
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert r.ok, r.summary()
        assert not any("W17" in e for e in r.errors)
        assert not any("W17:" in w for w in r.warnings)


def test_execute_w17_unrun_arm_resolved_framing_is_error():
    """Case (b): discriminating_arm UNRUN and central question framed as
    resolved → W17 ERRORs, blocking REVIEW (the over-claim)."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(
            tmp, 1, _w17_prereg("delta_perceiver_layer_steered")
        )
        # The discriminating arm did NOT run; only the headline arm did.
        results = _w17_results({}, framing="resolved")
        analysis = "AUC 0.82 CI [0.70, 0.90]\nΔ=0.12 CI [0.05, 0.19]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert not r.ok, r.summary()
        assert any(
            "W17" in e and "delta_perceiver_layer_steered" in e
            for e in r.errors
        ), r.errors
        # ERROR, not demoted to a warning.
        assert not any("W17:" in w for w in r.warnings), r.warnings


def test_execute_w17_unrun_arm_absent_framing_defaults_to_error():
    """Conservative default: discriminating_arm UNRUN with NO
    central_question block → framing defaults to resolved → W17 ERRORs."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(
            tmp, 1, _w17_prereg("delta_perceiver_layer_steered")
        )
        results = _w17_results({})  # no central_question block at all
        analysis = "AUC 0.82 CI [0.70, 0.90]\nΔ=0.12 CI [0.05, 0.19]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert not r.ok, r.summary()
        assert any(
            "W17" in e and "delta_perceiver_layer_steered" in e
            for e in r.errors
        ), r.errors


def test_execute_w17_unrun_arm_scoped_framing_is_warn():
    """Case (c): discriminating_arm UNRUN but the claim is narrowed/scoped
    to the completed arms (framing 'scoped') → W17 WARNs, never blocks
    REVIEW (the honest path)."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(
            tmp, 1, _w17_prereg("delta_perceiver_layer_steered")
        )
        results = _w17_results({}, framing="scoped")
        analysis = "AUC 0.82 CI [0.70, 0.90]\nΔ=0.12 CI [0.05, 0.19]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert r.ok, r.summary()
        assert not any("W17" in e for e in r.errors), r.errors
        assert any(
            "W17" in w and "delta_perceiver_layer_steered" in w
            for w in r.warnings
        ), r.warnings


def test_execute_w17_no_discriminating_arm_field_is_warn_not_crash():
    """Case (d): no discriminating_arm registered → W17 degrades gracefully
    to an advisory WARN (PLAN should name one); never crashes, never
    blocks REVIEW."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(tmp, 1, _w17_prereg(_W17_OMIT))
        results = _w17_results({}, framing="resolved")
        analysis = "AUC 0.82 CI [0.70, 0.90]\nΔ=0.12 CI [0.05, 0.19]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert r.ok, r.summary()
        assert not any("W17" in e for e in r.errors), r.errors
        assert any("W17" in w for w in r.warnings), r.warnings


def test_execute_w17_empty_string_field_is_warn():
    """A blank discriminating_arm value → advisory WARN (PLAN should name
    the deciding experiment); never blocks REVIEW."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(tmp, 1, _w17_prereg("   "))
        results = _w17_results({}, framing="resolved")
        analysis = "AUC 0.82 CI [0.70, 0.90]\nΔ=0.12 CI [0.05, 0.19]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert r.ok, r.summary()
        assert not any("W17" in e for e in r.errors), r.errors
        assert any("W17" in w for w in r.warnings), r.warnings


def test_execute_w17_dotted_form_arm_present_passes():
    """The discriminating_arm may carry the fully-dotted comparisons.delta_*
    form; the prefix is stripped (reuses W13's normaliser) before matching."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(
            tmp, 1, _w17_prereg("comparisons.delta_soft_gate_recovers")
        )
        results = _w17_results(
            {"delta_soft_gate_recovers": {"delta": 0.3, "ci_95": [0.2, 0.4]}},
            framing="resolved",
        )
        analysis = (
            "AUC 0.82 CI [0.70, 0.90]\nΔ=0.12 CI [0.05, 0.19]\n"
            "Δ=0.3 CI [0.2, 0.4]\n"
        )
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert r.ok, r.summary()
        assert not any("W17" in e for e in r.errors), r.errors
        assert not any("W17:" in w for w in r.warnings), r.warnings


# ---------------------------------------------------------------------------
# W17 `scoped`-exit corroboration hardening (P1, 2026-06-19;
# ScopedExitCorroborationGate). A `scoped` resolution is no longer a FREE
# downgrade of the decisive-arm gate: it ESCALATES the benign WARN when it
# carries no structured canonical-enum blocked_reason, OR when the run has
# accumulated > MAX_SCOPED_EXITS scoped framings. A single corroborated scope
# under the cap stays the benign WARN. WARN-only throughout (never blocks).
# ---------------------------------------------------------------------------

_ESCALATED = "escalated advisory"


def _w17_scoped_results(
    central_question,
    comparisons=None,
) -> dict:
    """results.json with the headline arm present (W13 passes) and an explicit
    central_question structure (a dict or a list of dicts) — lets a test
    control framing, blocked_reason, and scoped_exit_count directly."""
    comps = {"delta_headline_vs_null": {"delta": 0.12, "ci_95": [0.05, 0.19]}}
    if comparisons:
        comps.update(comparisons)
    return {
        "primary_model": {"auc": 0.82, "ci_95": [0.70, 0.90]},
        "comparisons": comps,
        "central_question": central_question,
    }


def _run_w17_scoped(tmp, results):
    _write_plan_with_preregistration(
        tmp, 1, _w17_prereg("delta_perceiver_layer_steered")
    )
    analysis = "AUC 0.82 CI [0.70, 0.90]\nΔ=0.12 CI [0.05, 0.19]\n"
    stage = _make_execute_stage(tmp, results, analysis)
    return check_execute(stage)


def test_w17_scoped_with_structured_blocked_reason_under_cap_is_benign():
    """(1) scoped + a canonical-enum blocked_reason, single exit → benign base
    WARN (NOT escalated), never blocks (the legitimate honest narrow-scope)."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        r = _run_w17_scoped(
            tmp,
            _w17_scoped_results(
                {
                    "discriminating_arm_resolution": "scoped",
                    "blocked_reason": ScopedExitCorroborationGate.BLOCKED_REASON_COMPUTE_UNAFFORDABLE,
                }
            ),
        )
        assert r.ok, r.summary()
        assert not any("W17" in e for e in r.errors), r.errors
        w17 = [w for w in r.warnings if "W17" in w]
        assert w17, r.warnings
        # Benign, NOT escalated.
        assert not any(_ESCALATED in w for w in w17), w17


def test_w17_scoped_without_blocked_reason_escalates():
    """(2) scoped + NO blocked_reason → escalated WARN (uncorroborated
    self-assertion), still WARN-only (never blocks)."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        r = _run_w17_scoped(
            tmp,
            _w17_scoped_results({"discriminating_arm_resolution": "scoped"}),
        )
        assert r.ok, r.summary()  # WARN-only: never sinks to error / blocks.
        assert not any("W17" in e for e in r.errors), r.errors
        esc = [w for w in r.warnings if _ESCALATED in w and "W17" in w]
        assert esc, r.warnings
        assert any(
            ScopedExitCorroborationGate.BLOCKED_REASON_KEY in w for w in esc
        ), esc


def test_w17_scoped_free_text_blocked_reason_escalates():
    """(2b) scoped + free-text blocked_reason NOT in the enum → still
    uncorroborated → escalated (the enum is canonical, not free text)."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        r = _run_w17_scoped(
            tmp,
            _w17_scoped_results(
                {
                    "discriminating_arm_resolution": "scoped",
                    "blocked_reason": "we ran out of time this sprint",
                }
            ),
        )
        assert r.ok, r.summary()
        assert any(_ESCALATED in w and "W17" in w for w in r.warnings), r.warnings


def test_w17_scoped_over_cap_escalates_even_with_blocked_reason():
    """(3) > MAX_SCOPED_EXITS scoped framings → escalated even WITH a structured
    blocked_reason (serial scoping = dodging). WARN-only."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        over = ScopedExitCorroborationGate.MAX_SCOPED_EXITS + 1
        r = _run_w17_scoped(
            tmp,
            _w17_scoped_results(
                {
                    "discriminating_arm_resolution": "scoped",
                    "blocked_reason": ScopedExitCorroborationGate.BLOCKED_REASON_ACCESS_GATED,
                    "scoped_exit_count": over,
                }
            ),
        )
        assert r.ok, r.summary()
        assert not any("W17" in e for e in r.errors), r.errors
        esc = [w for w in r.warnings if _ESCALATED in w and "W17" in w]
        assert esc, r.warnings
        assert any(str(over) in w for w in esc), esc


def test_w17_scoped_list_over_cap_escalates():
    """(3b) central_question as a LIST of scoped questions exceeding the cap →
    count inferred from the list shape → escalated."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        n = ScopedExitCorroborationGate.MAX_SCOPED_EXITS + 1
        questions = [
            {
                "discriminating_arm_resolution": "scoped",
                "blocked_reason": ScopedExitCorroborationGate.BLOCKED_REASON_DATA_UNAVAILABLE,
            }
            for _ in range(n)
        ]
        r = _run_w17_scoped(tmp, _w17_scoped_results(questions))
        assert r.ok, r.summary()
        assert any(_ESCALATED in w and "W17" in w for w in r.warnings), r.warnings


def test_w17_resolved_with_arm_run_is_noop():
    """(4) resolved framing WITH the discriminating arm RUN → silent no-op
    (the deciding experiment ran; the corroboration gate is irrelevant)."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(
            tmp, 1, _w17_prereg("delta_perceiver_layer_steered")
        )
        results = _w17_scoped_results(
            {"discriminating_arm_resolution": "resolved"},
            comparisons={
                "delta_perceiver_layer_steered": {"delta": 0.2, "ci_95": [0.1, 0.3]}
            },
        )
        analysis = (
            "AUC 0.82 CI [0.70, 0.90]\nΔ=0.12 CI [0.05, 0.19]\n"
            "Δ=0.2 CI [0.1, 0.3]\n"
        )
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert r.ok, r.summary()
        assert not any("W17" in e for e in r.errors), r.errors
        # No W17-emitted warning (W21's advisory text mentions W17 descriptively
        # — match the gate's own prefix, not the substring).
        assert not any(w.startswith("W17") for w in r.warnings), r.warnings


def test_w17_no_central_question_is_no_scoped_escalation():
    """(5) no central_question block at all → framing defaults to resolved →
    the corroboration/cap path is never entered (no escalated WARN). With an
    unrun arm this is the conservative W17 ERROR path, NOT a scoped escalation;
    the assertion of interest is that NO `escalated advisory` WARN fires."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(
            tmp, 1, _w17_prereg("delta_perceiver_layer_steered")
        )
        results = _w17_results({})  # no central_question block
        analysis = "AUC 0.82 CI [0.70, 0.90]\nΔ=0.12 CI [0.05, 0.19]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        # Conservative default → W17 ERROR (resolved+unrun), but crucially the
        # scoped-corroboration path is a no-op: no escalated-advisory WARN.
        assert not any(_ESCALATED in w for w in r.warnings), r.warnings


def test_w17_scoped_gate_is_warn_severity_and_never_errors():
    """The corroboration gate is WARN-only: SEVERITY == 'warn' and an
    uncorroborated/over-cap scoped exit never produces an ERROR."""
    assert ScopedExitCorroborationGate.SEVERITY == "warn"
    assert ScopedExitCorroborationGate.GATE_ID == "W17"
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        r = _run_w17_scoped(
            tmp,
            _w17_scoped_results({"discriminating_arm_resolution": "scoped"}),
        )
        assert not any("W17" in e for e in r.errors), r.errors


# ---------------------------------------------------------------------------
# W18 — proof-substance / theorem-depth enforcement gate
# (theory-gated; ERROR on a headline first-class theorem carrying a
#  substance_warning; WARN when supporting or honestly demoted; reads the
#  REAL omicairn.lean_verify.substance_warnings strings as fixtures.)
# ---------------------------------------------------------------------------

def _w18_real_elementary_warning() -> str:
    """The REAL elementary-theorem warning omicairn.lean_verify emits — used as
    a fixture so the test pins W18 to the actual stored string, not a stand-
    in. Generated from the canonical XOR-table proof shape (ring closer, no
    decide/native_decide/omega)."""
    from omicairn import lean_verify
    src = (
        "theorem linear_clean_not_nonlinear_clean : (1 : Int) = 1 := by\n"
        "  norm_num\n"
    )
    warns = lean_verify.elementary_theorem_warnings(src)
    assert warns, "fixture must produce an elementary warning"
    return warns[0]


def _w18_prereg(*, contribution_type=None, load_bearing_theorem=None) -> dict:
    prereg: dict = {"primary_hypothesis": "H1", "primary_metric": "delta",
                    "mde": 0.05}
    if contribution_type is not None:
        prereg["contribution_type"] = contribution_type
    if load_bearing_theorem is not None:
        prereg["load_bearing_theorem"] = load_bearing_theorem
    return prereg


def _w18_write_lean_verdict(
    stage: pathlib.Path, name: str, verdict: dict
) -> None:
    lean = stage / "lean"
    lean.mkdir(parents=True, exist_ok=True)
    (lean / f"{name}.verdict.json").write_text(json.dumps(verdict))


def _w18_clean_execute(tmp: pathlib.Path) -> pathlib.Path:
    results = {"primary_model": {"auc": 0.82, "ci_95": [0.70, 0.90]}}
    analysis = "AUC 0.82 CI [0.70, 0.90]\n"
    return _make_execute_stage(tmp, results, analysis)


def test_execute_w18_theory_headline_elementary_first_class_is_error():
    """(a) theory run, HEADLINE theorem with an elementary substance_warning,
    framed as a first-class `theorem` (default role) → W18 ERROR."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(
            tmp, 1, _w18_prereg(
                contribution_type="theory",
                load_bearing_theorem="linear_clean_not_nonlinear_clean",
            )
        )
        stage = _w18_clean_execute(tmp)
        _w18_write_lean_verdict(stage, "Separation", {
            "compiled": True,
            "theorems_checked": ["linear_clean_not_nonlinear_clean"],
            "claim": "linear_clean_not_nonlinear_clean",
            "substance_warnings": [_w18_real_elementary_warning()],
        })
        r = check_execute(stage)
        assert not r.ok, r.summary()
        assert any(
            "W18" in e and "Separation.verdict.json" in e for e in r.errors
        ), r.errors
        # ERROR, not demoted to a warning.
        assert not any("W18" in w for w in r.warnings), r.warnings


def test_execute_w18_theory_headline_demoted_is_warn_not_error():
    """(b) same warning but the theorem is HONESTLY DEMOTED to a labeled
    conjecture / formalized observation → W18 WARN (advisory), not ERROR —
    the honest path."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(
            tmp, 1, _w18_prereg(
                contribution_type="theory",
                load_bearing_theorem="linear_clean_not_nonlinear_clean",
            )
        )
        stage = _w18_clean_execute(tmp)
        _w18_write_lean_verdict(stage, "Separation", {
            "compiled": True,
            "theorems_checked": ["linear_clean_not_nonlinear_clean"],
            "claim": "linear_clean_not_nonlinear_clean",
            "contribution_role": "formalized_observation",
            "substance_warnings": [_w18_real_elementary_warning()],
        })
        r = check_execute(stage)
        assert r.ok, r.summary()
        assert not any("W18" in e for e in r.errors), r.errors
        assert any("W18" in w for w in r.warnings), r.warnings


def test_execute_w18_supporting_theorem_warning_is_warn_not_error():
    """(c) a SUPPORTING (non-headline) theorem carries a warning → W18 WARN,
    not ERROR. The headline theorem is named and clean; the flagged verdict
    is a different (supporting) theorem."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(
            tmp, 1, _w18_prereg(
                contribution_type="theory",
                load_bearing_theorem="residual_margin_lower_bound",
            )
        )
        stage = _w18_clean_execute(tmp)
        # headline theorem — clean.
        _w18_write_lean_verdict(stage, "Bound", {
            "compiled": True,
            "theorems_checked": ["residual_margin_lower_bound"],
            "claim": "residual_margin_lower_bound",
            "substance_warnings": [],
        })
        # supporting theorem — flagged, but NOT the headline.
        _w18_write_lean_verdict(stage, "Lemma", {
            "compiled": True,
            "theorems_checked": ["means_are_zero"],
            "claim": "means_are_zero",
            "substance_warnings": [_w18_real_elementary_warning()],
        })
        r = check_execute(stage)
        assert r.ok, r.summary()
        assert not any("W18" in e for e in r.errors), r.errors
        assert any(
            "W18" in w and "Lemma.verdict.json" in w for w in r.warnings
        ), r.warnings


def test_execute_w18_clean_substantive_theorem_passes_silently():
    """(d) a clean substantive theorem (no substance_warnings — the d5/d7
    witness-check style) → W18 silent pass."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(
            tmp, 1, _w18_prereg(
                contribution_type="theory",
                load_bearing_theorem="T1_T2_witness",
            )
        )
        stage = _w18_clean_execute(tmp)
        _w18_write_lean_verdict(stage, "Witness", {
            "compiled": True,
            "theorems_checked": ["T1_T2_witness"],
            "claim": "T1_T2_witness",
            "substance_warnings": [],
        })
        r = check_execute(stage)
        assert r.ok, r.summary()
        assert not any("W18" in e for e in r.errors), r.errors
        assert not any("W18" in w for w in r.warnings), r.warnings


def test_execute_w18_non_theory_paper_is_no_op():
    """(e) a non-theory run (contribution_type absent / 'analysis') →
    W18 NEVER fires, even with a flagged headline verdict present."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        # contribution_type omitted entirely (the common analysis case).
        _write_plan_with_preregistration(
            tmp, 1, _w18_prereg(load_bearing_theorem="some_theorem")
        )
        stage = _w18_clean_execute(tmp)
        _w18_write_lean_verdict(stage, "Separation", {
            "compiled": True,
            "theorems_checked": ["some_theorem"],
            "claim": "some_theorem",
            "substance_warnings": [_w18_real_elementary_warning()],
        })
        r = check_execute(stage)
        assert r.ok, r.summary()
        assert not any("W18" in e for e in r.errors), r.errors
        assert not any("W18" in w for w in r.warnings), r.warnings


def test_execute_w18_headline_fallback_to_claim_when_unnamed():
    """When PLAN names no `load_bearing_theorem`, W18 falls back to the
    per-verdict `claim` field: a verdict whose `claim` names a checked
    theorem carrying a warning, framed first-class → ERROR (the
    fair-erasure case shape: theory run with no declared field)."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(
            tmp, 1, _w18_prereg(contribution_type="theory")
        )
        stage = _w18_clean_execute(tmp)
        _w18_write_lean_verdict(stage, "Separation", {
            "compiled": True,
            "theorems_checked": ["linear_clean_not_nonlinear_clean"],
            "claim": "linear_clean_not_nonlinear_clean",
            "substance_warnings": [_w18_real_elementary_warning()],
        })
        r = check_execute(stage)
        assert not r.ok, r.summary()
        assert any("W18" in e for e in r.errors), r.errors


def test_execute_w18_no_lean_subdir_is_no_op():
    """Theory run with NO lean artifacts → W18 silent no-op (nothing to
    enforce; backward compatible with non-formalized runs)."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(
            tmp, 1, _w18_prereg(contribution_type="theory")
        )
        stage = _w18_clean_execute(tmp)
        r = check_execute(stage)
        assert r.ok, r.summary()
        assert not any("W18" in e for e in r.errors), r.errors
        assert not any("W18" in w for w in r.warnings), r.warnings


def test_execute_w18_verdict_without_substance_key_is_no_op():
    """A verdict-JSON schema variant that OMITS substance_warnings entirely
    (older agent-written verdicts) is treated as no-warning → W18 silent
    (tolerant of the on-disk schema variants)."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(
            tmp, 1, _w18_prereg(contribution_type="theory",
                                 load_bearing_theorem="T1")
        )
        stage = _w18_clean_execute(tmp)
        _w18_write_lean_verdict(stage, "T1", {
            "compiled": True, "theorems_checked": ["T1"], "claim": "T1",
            # no substance_warnings key at all
        })
        r = check_execute(stage)
        assert r.ok, r.summary()
        assert not any("W18" in e for e in r.errors), r.errors
        assert not any("W18" in w for w in r.warnings), r.warnings


# ---------------------------------------------------------------------------
# Metric-bounds domain-pack hook
# ---------------------------------------------------------------------------

def test_metric_bounds_0_1_generic_has_core_metrics():
    """The generic [0, 1] allowlist carries the universal ML-ish metrics
    (auc, accuracy, c_index) so every domain has a sane default."""
    from omicairn.cross_check import metric_bounds_0_1
    bounds = metric_bounds_0_1(None)
    for metric in ("auc", "accuracy", "c_index", "f1"):
        assert metric in bounds


def test_metric_bounds_0_1_merges_survival_prognostic_extras():
    """survival_prognostic profile.yaml adds ibs / time_dependent_auc to
    the [0, 1] class. They must appear on top of generic defaults."""
    from omicairn.cross_check import metric_bounds_0_1
    bounds = metric_bounds_0_1("survival_prognostic")
    assert "auc" in bounds  # generic still there
    assert "ibs" in bounds  # pack extra
    assert "time_dependent_auc" in bounds


def test_metric_bounds_nonneg_merges_survival_prognostic_extras():
    from omicairn.cross_check import metric_bounds_nonneg
    bounds = metric_bounds_nonneg("survival_prognostic")
    assert "rmse" in bounds  # generic
    assert "partial_likelihood" in bounds  # pack extra
    assert "hr_se" in bounds


def test_metric_bounds_neg1_1_generic_for_unknown_domain():
    """An unknown domain falls back to _generic, which declares no
    metric_bounds_neg1_1 extras — so the result equals the generic
    frozenset exactly."""
    from omicairn.cross_check import (
        _METRIC_BOUNDS_NEG1_1_GENERIC,
        metric_bounds_neg1_1,
    )
    assert metric_bounds_neg1_1("nonexistent-domain") == _METRIC_BOUNDS_NEG1_1_GENERIC


def test_bounded_metric_domain_extra_flags_out_of_range_ibs():
    """A domain-pack extra metric (ibs) must be range-checked just like
    a generic one. An ibs of 1.3 is impossible and must error under
    survival_prognostic but go unchecked under _generic."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {
            "primary_model": {
                "c_index": 0.72,
                "ibs": 1.3,  # impossible — should fire under survival pack
                "ci_95": [0.65, 0.79],
            },
        }
        analysis = "c_index 0.72 (CI [0.65, 0.79])\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r_survival = check_execute(stage, domain="survival_prognostic")
        assert not r_survival.ok
        assert any("ibs" in e and "[0, 1]" in e for e in r_survival.errors)
        # Under _generic, ibs is not in the allowlist → no bounds error.
        r_generic = check_execute(stage, domain=None)
        assert not any("ibs" in e for e in r_generic.errors)


# ---------------------------------------------------------------------------
# Opt-in W-check enforcement per domain pack
# ---------------------------------------------------------------------------

def test_enabled_execute_checks_generic_opts_out_of_optin_set():
    """_generic opts out of the OPT-IN W1/W2/W5 patch set (Nature/Cell
    biomedical convention that false-fires on other fields). W32 is
    DEFAULT-ON (opt-out via disable_execute_checks), so it is present even
    for the generic domain."""
    from omicairn.cross_check import enabled_execute_checks
    assert enabled_execute_checks(None) == frozenset({"w32"})


def test_enabled_execute_checks_survival_enables_full_set():
    from omicairn.cross_check import enabled_execute_checks
    # Opt-in {w2,w5} UNION the default-on {w32}.
    assert enabled_execute_checks("survival_prognostic") == frozenset(
        {"w2", "w5", "w32"}
    )


# ---------------------------------------------------------------------------
# Tier 2.1 — W9 hypothesis-DAG and fallback enforcement
# ---------------------------------------------------------------------------

def _write_plan_with_preregistration(
    tmp: pathlib.Path, index: int, prereg: dict
) -> pathlib.Path:
    plan_dir = tmp / f"{index:03d}_plan"
    plan_dir.mkdir(parents=True, exist_ok=True)
    (plan_dir / "hypotheses.md").write_text("# Hypotheses\n## Power\nMDE = 0.08\n")
    (plan_dir / "preregistration.json").write_text(json.dumps(prereg))
    return plan_dir


def _write_asset_probe(tmp: pathlib.Path, assets: list[dict]) -> pathlib.Path:
    p = tmp / "asset_probe.json"
    p.write_text(json.dumps({"version": 1, "assets": assets}))
    return p


def test_w9_blocked_dataset_without_fallback_is_error():
    """A hypothesis depends on a missing dataset and has no
    fallback_if_blocked → W9 ERROR."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(tmp, 1, {
            "primary_hypothesis": "H1",
            "hypotheses": [
                {"id": "H1", "decision_rule": "x ≥ y",
                 "depends_on": ["dataset:missing-set"]},
            ],
        })
        _write_asset_probe(tmp, [
            {"url": "https://x", "accession": "missing-set",
             "status": "missing", "http_status": 404},
        ])
        results = {"primary_model": {"auc": 0.82, "ci_95": [0.677, 0.943]}}
        analysis = "AUC 0.82 CI [0.677, 0.943]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert any("W9" in w and "fallback_if_blocked" in w for w in r.warnings)


def test_w9_blocked_dataset_with_fallback_is_ok():
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(tmp, 1, {
            "primary_hypothesis": "H1",
            "hypotheses": [
                {"id": "H1", "decision_rule": "x ≥ y",
                 "depends_on": ["dataset:missing-set"],
                 "fallback_if_blocked": "H1_SURROGATE_local_only"},
            ],
        })
        _write_asset_probe(tmp, [
            {"url": "https://x", "accession": "missing-set",
             "status": "missing", "http_status": 404},
        ])
        results = {"primary_model": {"auc": 0.82, "ci_95": [0.677, 0.943]}}
        analysis = "AUC 0.82 CI [0.677, 0.943]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert not any("W9" in e for e in r.errors)


def test_w9_defer_to_limitations_warns_only():
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(tmp, 1, {
            "primary_hypothesis": "H1",
            "hypotheses": [
                {"id": "H1", "decision_rule": "x ≥ y",
                 "depends_on": ["dataset:missing-set"],
                 "fallback_if_blocked": "DEFER_TO_LIMITATIONS"},
            ],
        })
        _write_asset_probe(tmp, [
            {"url": "https://x", "accession": "missing-set",
             "status": "missing", "http_status": 404},
        ])
        results = {"primary_model": {"auc": 0.82, "ci_95": [0.677, 0.943]}}
        analysis = "AUC 0.82 CI [0.677, 0.943]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert not any("W9" in e for e in r.errors)
        assert any("deferred to Limitations" in w for w in r.warnings)


def test_w9_missing_decision_rule_is_error():
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(tmp, 1, {
            "primary_hypothesis": "H1",
            "hypotheses": [{"id": "H1", "depends_on": []}],
        })
        results = {"primary_model": {"auc": 0.82, "ci_95": [0.677, 0.943]}}
        analysis = "AUC 0.82 CI [0.677, 0.943]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert any("decision_rule" in w and "W9" in w for w in r.warnings)


def test_w9_missing_depends_on_is_error():
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(tmp, 1, {
            "primary_hypothesis": "H1",
            "hypotheses": [{"id": "H1", "decision_rule": "x ≥ y"}],
        })
        results = {"primary_model": {"auc": 0.82, "ci_95": [0.677, 0.943]}}
        analysis = "AUC 0.82 CI [0.677, 0.943]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert any("depends_on" in w and "W9" in w for w in r.warnings)


def test_w9_silent_skip_when_no_hypotheses_block():
    """Plan without `hypotheses` → W9 silent-skips (legacy plans)."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(tmp, 1, {
            "primary_hypothesis": "H1",
        })
        results = {"primary_model": {"auc": 0.82, "ci_95": [0.677, 0.943]}}
        analysis = "AUC 0.82 CI [0.677, 0.943]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert not any("W9" in e for e in r.errors)


# ---------------------------------------------------------------------------
# Tier 2.2 — W10 family-correction declared
# ---------------------------------------------------------------------------

def test_w10_two_hypotheses_without_family_correction_is_error():
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(tmp, 1, {
            "primary_hypothesis": "H1",
            "hypotheses": [
                {"id": "H1", "decision_rule": "x", "depends_on": []},
                {"id": "H2", "decision_rule": "y", "depends_on": []},
            ],
        })
        results = {"primary_model": {"auc": 0.82, "ci_95": [0.677, 0.943]}}
        analysis = "AUC 0.82 CI [0.677, 0.943]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert any("W10" in w and "family_correction" in w for w in r.warnings)


def test_w10_bonferroni_passes():
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(tmp, 1, {
            "primary_hypothesis": "H1",
            "family_correction": "bonferroni",
            "hypotheses": [
                {"id": "H1", "decision_rule": "x", "depends_on": []},
                {"id": "H2", "decision_rule": "y", "depends_on": []},
            ],
        })
        results = {"primary_model": {"auc": 0.82, "ci_95": [0.677, 0.943]}}
        analysis = "AUC 0.82 CI [0.677, 0.943]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert not any("W10" in e for e in r.errors)


def test_w10_none_with_justification_requires_rationale():
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(tmp, 1, {
            "primary_hypothesis": "H1",
            "family_correction": "none_with_justification",
            "family_correction_rationale": "",
            "hypotheses": [
                {"id": "H1", "decision_rule": "x", "depends_on": []},
                {"id": "H2", "decision_rule": "y", "depends_on": []},
            ],
        })
        results = {"primary_model": {"auc": 0.82, "ci_95": [0.677, 0.943]}}
        analysis = "AUC 0.82 CI [0.677, 0.943]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert any("W10" in w and "rationale" in w for w in r.warnings)


def test_w10_silent_skip_for_single_hypothesis():
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(tmp, 1, {
            "primary_hypothesis": "H1",
            "hypotheses": [
                {"id": "H1", "decision_rule": "x", "depends_on": []},
            ],
        })
        results = {"primary_model": {"auc": 0.82, "ci_95": [0.677, 0.943]}}
        analysis = "AUC 0.82 CI [0.677, 0.943]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert not any("W10" in e for e in r.errors)


# ---------------------------------------------------------------------------
# Tier 2.4 — CI-contains-point on nested hypothesis sub-blocks
# ---------------------------------------------------------------------------

def test_ci_contains_point_catches_nested_hypothesis_violation():
    """The morphogen H1 surrogate shipped point=7.424 below CI lower=7.429.
    The fix in _iter_load_bearing_blocks must walk into
    primary_model.hypotheses.H<n>."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {
            "primary_model": {
                "auc": 0.82, "ci_95": [0.677, 0.943],
                "hypotheses": {
                    "H1": {
                        "cluster_composition_concordance": 7.424,
                        "ci_95": [7.429, 7.494],
                    },
                },
            },
        }
        analysis = "AUC 0.82 CI [0.677, 0.943]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert any(
            "primary_model.hypotheses.H1" in e and "outside" in e
            for e in r.errors
        )


def test_ci_contains_point_catches_suffixed_metric_violation():
    """Real fair-removal-vs-equalization-20260613 run: primary_model
    carried a VALID canonical pair (residual_eo_gap inside its ci_95)
    AND a second metric whose own suffixed CI excluded its value —
    ``probe_auroc=0.4617`` below ``probe_auroc_ci_95=[0.4820, 0.5150]``.
    The pre-fix checker only paired the canonical point against ci_95,
    so the suffixed breach (reported downstream as
    "AUROC 0.462, CI [0.482, 0.515]") sailed through.

    The fix walks every ``<metric>`` / ``<metric>_ci_95`` pair, so the
    breach is now an error while the valid canonical pair stays silent.
    """
    results = {
        "primary_model": {
            # canonical pair: VALID (point inside ci_95).
            "residual_eo_gap": 0.06963465208142752,
            "ci_95": [0.04567444696528182, 0.09352472483861192],
            # suffixed pair: BREACH (point below the suffixed CI lower).
            "probe_auroc": 0.4617155207198375,
            "probe_auroc_ci_95": [0.4820037985090063, 0.5149717192267893],
        },
    }
    errors: list[str] = []
    _check_point_estimate_in_ci(results, errors)
    assert any(
        "primary_model.probe_auroc" in e
        and "outside" in e.lower()
        and "ci_95" in e.lower()
        for e in errors
    ), f"expected suffixed-metric breach error, got {errors}"
    # The valid canonical metric must NOT be reported.
    assert not any("residual_eo_gap" in e for e in errors), errors


def test_ci_contains_point_suffixed_metric_inside_ci_is_ok():
    """Guard against false positives: a ``<metric>`` whose value lies
    inside its own ``<metric>_ci_95`` (alongside a valid canonical pair)
    must produce no error."""
    results = {
        "primary_model": {
            "residual_eo_gap": 0.06963465208142752,
            "ci_95": [0.04567444696528182, 0.09352472483861192],
            # suffixed pair: VALID (point inside the suffixed CI).
            "probe_auroc": 0.50,
            "probe_auroc_ci_95": [0.48, 0.52],
        },
    }
    errors: list[str] = []
    _check_point_estimate_in_ci(results, errors)
    assert errors == [], f"expected no error, got {errors}"


# ---------------------------------------------------------------------------
# Tier 2.5 — W2 numeric MDE requirement
# ---------------------------------------------------------------------------

def test_w2_heading_only_no_number_is_error():
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_sibling(
            tmp, 1,
            "# Hypotheses\n\n"
            "H1\n\n"
            "## Power\n"
            "Power analysis with α=0.05 will be conducted on TBD.\n"
            "Minimum detectable effect to be determined later.\n",
        )
        results = {"primary_model": {"auc": 0.82, "ci_95": [0.677, 0.943]}}
        analysis = "AUC 0.82 CI [0.677, 0.943]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage, domain=_W_DOMAIN)
        assert any(
            "no concrete numeric MDE" in w for w in r.warnings
        ), f"got {r.warnings}"


def test_w2_concrete_mde_passes():
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_sibling(
            tmp, 1,
            "# Hypotheses\n\n"
            "## Power\n\n"
            "MDE = 0.05 at 80% power, α=0.05.\n",
        )
        results = {"primary_model": {"auc": 0.82, "ci_95": [0.677, 0.943]}}
        analysis = "AUC 0.82 CI [0.677, 0.943]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage, domain=_W_DOMAIN)
        assert not any("no concrete numeric MDE" in e for e in r.errors)


# ---------------------------------------------------------------------------
# Tier 1.3 — environment lockfile coverage
# ---------------------------------------------------------------------------

def test_execute_env_lockfile_missing_is_warning():
    """No code/environment.lock.json → cross_check warns but does not error."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {"primary_model": {"auc": 0.7, "ci_95": [0.6, 0.8]}}
        analysis = "AUC 0.7 CI [0.6, 0.8]\n"
        stage = _make_execute_stage(tmp, results, analysis, skip_env_lockfile=True)
        r = check_execute(stage)
        assert r.ok or all("environment.lock" not in e for e in r.errors)
        assert any("environment.lock.json missing" in w for w in r.warnings)


def test_execute_env_lockfile_present_with_full_coverage_is_ok():
    """Lockfile exists and covers every imported module → no warning."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {"primary_model": {"auc": 0.7, "ci_95": [0.6, 0.8]}}
        analysis = "AUC 0.7 CI [0.6, 0.8]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        # Replace stub main.py with one that imports a real package.
        (stage / "code" / "main.py").write_text(
            "import json\nimport pytest\n"
        )
        lock = {
            "version": 1, "captured_at": "t", "python_version": "3.11",
            "platform": "x", "executable": "y",
            "packages": [{"name": "pytest", "version": "9.0"}],
            "imported_modules": ["pytest"],
        }
        (stage / "code" / "environment.lock.json").write_text(json.dumps(lock))
        r = check_execute(stage)
        # Lockfile is present so the "missing" warning is gone, and pytest
        # is genuinely installed in this test env so coverage is OK.
        assert not any("environment.lock.json missing" in w for w in r.warnings)


def test_execute_env_lockfile_missing_module_warns():
    """Imports include a module not in the lockfile packages → warning."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {"primary_model": {"auc": 0.7, "ci_95": [0.6, 0.8]}}
        analysis = "AUC 0.7 CI [0.6, 0.8]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        (stage / "code" / "main.py").write_text(
            "import some_obscure_pkg_not_installed\n"
        )
        lock = {
            "version": 1, "captured_at": "t", "python_version": "3.11",
            "platform": "x", "executable": "y",
            "packages": [{"name": "numpy", "version": "2.4"}],
            "imported_modules": ["some_obscure_pkg_not_installed"],
        }
        (stage / "code" / "environment.lock.json").write_text(json.dumps(lock))
        r = check_execute(stage)
        assert any(
            "missing distributions" in w or "imported_modules misses" in w
            for w in r.warnings
        )


# ---------------------------------------------------------------------------
# Tier 1.5 — preregistration sha256 drift
# ---------------------------------------------------------------------------

def test_execute_preregistration_drift_detected():
    """results.json carries a stale preregistration_sha256 → ERROR."""
    import hashlib
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        plan_dir = tmp / "001_plan"
        plan_dir.mkdir()
        prereg = b'{"primary_hypothesis": "x"}\n'
        (plan_dir / "preregistration.json").write_bytes(prereg)
        actual = hashlib.sha256(prereg).hexdigest()
        # Use a wrong hash → drift detected.
        results = {
            "primary_model": {"auc": 0.7, "ci_95": [0.6, 0.8]},
            "preregistration_sha256": "0" * 64,
        }
        analysis = "AUC 0.7 CI [0.6, 0.8]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        # Move stage to 002_execute (already named that by the helper).
        r = check_execute(stage)
        assert any("preregistration_sha256" in e for e in r.errors)
        assert all(actual not in w for w in r.warnings)  # actual hash referenced in error only


def test_execute_preregistration_hash_matches_no_error():
    """Correct sha256 → no preregistration drift error."""
    import hashlib
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        plan_dir = tmp / "001_plan"
        plan_dir.mkdir()
        prereg = b'{"primary_hypothesis": "x"}\n'
        (plan_dir / "preregistration.json").write_bytes(prereg)
        actual = hashlib.sha256(prereg).hexdigest()
        results = {
            "primary_model": {"auc": 0.7, "ci_95": [0.6, 0.8]},
            "preregistration_sha256": actual,
        }
        analysis = "AUC 0.7 CI [0.6, 0.8]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert not any("preregistration_sha256" in e for e in r.errors)


def test_execute_preregistration_hash_silent_skip_when_field_absent():
    """No preregistration_sha256 in results.json → check silent-skips."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        plan_dir = tmp / "001_plan"
        plan_dir.mkdir()
        (plan_dir / "preregistration.json").write_text('{"x": 1}')
        results = {"primary_model": {"auc": 0.7, "ci_95": [0.6, 0.8]}}
        analysis = "AUC 0.7 CI [0.6, 0.8]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert not any("preregistration_sha256" in e for e in r.errors)


# ---------------------------------------------------------------------------
# Subsampling-leak SOFT guard (warning-only; never errors; no false positive)
# ---------------------------------------------------------------------------

def _clean_results() -> dict:
    return {"primary_model": {"auc": 0.8, "ci_95": [0.7, 0.9]}}


def test_subsampling_code_call_warns_not_errors():
    """A subsampling call in code/ surfaces a WARNING, never an error."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        stage = _make_execute_stage(tmp, _clean_results(), "AUC 0.8 [0.7, 0.9]\n")
        (stage / "code" / "main.py").write_text(
            "import scanpy as sc\n"
            "adata = sc.read_h5ad('x.h5ad')\n"
            "sc.pp.subsample(adata, n_obs=5000)\n"
        )
        warns: list[str] = []
        _check_subsampling(stage, warns)
        assert len(warns) == 1
        assert "subsampling" in warns[0].lower()
        # And it never escalates to an error in the full check.
        r = check_execute(stage)
        assert r.ok  # warning-only — clean schema means ok stays True
        assert any("subsampl" in w.lower() for w in r.warnings)


def test_subsampling_pandas_sample_n_warns():
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        stage = _make_execute_stage(tmp, _clean_results(), "AUC 0.8 [0.7, 0.9]\n")
        (stage / "code" / "main.py").write_text(
            "import pandas as pd\n"
            "df = pd.read_csv('big.csv')\n"
            "df = df.sample(n=1000, random_state=0)\n"
        )
        warns: list[str] = []
        _check_subsampling(stage, warns)
        assert warns and "df.sample" in warns[0] or warns  # one warning


def test_subsampling_prose_in_analysis_warns():
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        analysis = "We subsampled the cohort to 10000 cells for speed.\n"
        stage = _make_execute_stage(tmp, _clean_results(), analysis)
        warns: list[str] = []
        _check_subsampling(stage, warns)
        assert len(warns) == 1


def test_no_subsampling_no_warning():
    """Clean stage with no subsampling signals → no warning (no false positive)."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        stage = _make_execute_stage(tmp, _clean_results(), "AUC 0.8 [0.7, 0.9]\n")
        (stage / "code" / "main.py").write_text(
            "import numpy as np\n"
            "x = np.load('full.npy')  # full dataset, every row\n"
        )
        warns: list[str] = []
        _check_subsampling(stage, warns)
        assert warns == []


def test_subsampling_suppressed_by_disclosure_marker():
    """Explicit full-data/smoke/RESOURCE_REQUEST disclosure suppresses the warn."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        analysis = (
            "Smoke test only: we subsampled to 500 cells for a quick dev "
            "iteration; the final run uses the full dataset.\n"
        )
        stage = _make_execute_stage(tmp, _clean_results(), analysis)
        (stage / "code" / "main.py").write_text(
            "import scanpy as sc\nsc.pp.subsample(a, n_obs=500)\n"
        )
        warns: list[str] = []
        _check_subsampling(stage, warns)
        assert warns == []


def test_subsampling_word_sample_without_n_or_frac_not_flagged():
    """`.sample()` without n=/frac= (e.g. a method named sample) not flagged."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        stage = _make_execute_stage(tmp, _clean_results(), "AUC 0.8 [0.7, 0.9]\n")
        (stage / "code" / "main.py").write_text(
            "posterior_draws = model.sample(draws=2000)  # MCMC, not subsampling\n"
        )
        warns: list[str] = []
        _check_subsampling(stage, warns)
        assert warns == []


# ---------------------------------------------------------------------------
# W20 — decision-rule-HONORED gate (a W17-sibling). PLAN emits the OPTIONAL
# machine-checkable twin `decision_rule_structured` per hypothesis; W20 reads
# the named metric from results.json and ERRORs when the rule is VIOLATED
# while the hypothesis is framed SUPPORTED (structural per-hypothesis status).
# Backward compatibility: ABSENT field → silent pass (the whole finalized
# corpus predates it). WARN (never ERROR) on a violation with unknown framing,
# a malformed rule, a missing metric key, or an unresolvable ci_95.
# ---------------------------------------------------------------------------

_W20_OMIT = object()


def _w20_prereg(structured_rule, *, hid: str = "H1") -> dict:
    """Minimal preregistration.json with one hypothesis carrying a prose
    decision_rule. Attach a structured twin unless _W20_OMIT is passed (the
    critical back-compat case: a hypotheses block with NO structured rule)."""
    hyp: dict = {
        "id": hid,
        "decision_rule": "primary metric exceeds baseline with 95% CI excluding 0",
        "depends_on": [],
    }
    if structured_rule is not _W20_OMIT:
        hyp["decision_rule_structured"] = structured_rule
    return {"primary_hypothesis": hid, "primary_metric": "delta", "hypotheses": [hyp]}


def _w20_results(metric_blocks: dict, *, status=_W20_OMIT, hid: str = "H1") -> dict:
    """results.json carrying the given top-level metric blocks. Optionally
    record a structured per-hypothesis status (the framed-supported signal)."""
    results: dict = {
        "primary_model": {"auc": 0.82, "ci_95": [0.70, 0.90]},
    }
    results.update(metric_blocks)
    if status is not _W20_OMIT:
        results["hypothesis_status"] = {hid: status}
    return results


_W20_ANALYSIS = "AUC 0.82 CI [0.70, 0.90]\n"


def test_execute_w20_absent_structured_field_passes_silently():
    """Case (a) — THE critical back-compat case: a hypotheses block exists
    but NO `decision_rule_structured` → W20 silent pass, never errors/warns.
    This is every existing / in-flight run."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(tmp, 1, _w20_prereg(_W20_OMIT))
        results = _w20_results({"h_metric": {"r_s": 0.5}})
        stage = _make_execute_stage(tmp, results, _W20_ANALYSIS)
        r = check_execute(stage)
        assert r.ok, r.summary()
        assert not any("W20" in e for e in r.errors), r.errors
        assert not any("W20:" in w for w in r.warnings), r.warnings


def test_execute_w20_present_and_honored_passes_silently():
    """Case (b): structured rule present and HONORED (op holds, ci excludes)
    → W20 silent pass."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(tmp, 1, _w20_prereg(
            {"metric": "delta_block.delta", "op": ">=", "threshold": 0.02,
             "ci_excludes": 0}
        ))
        results = _w20_results(
            {"delta_block": {"delta": 0.12, "ci_95": [0.05, 0.19]}},
            status="supported",
        )
        stage = _make_execute_stage(tmp, results, _W20_ANALYSIS)
        r = check_execute(stage)
        assert r.ok, r.summary()
        assert not any("W20" in e for e in r.errors), r.errors
        assert not any("W20:" in w for w in r.warnings), r.warnings


def test_execute_w20_violated_and_framed_supported_is_error():
    """Case (c): structured rule VIOLATED + hypothesis framed SUPPORTED →
    W20 ERRORs, blocking REVIEW (the wm-cross-model-transfer H3 over-claim)."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(tmp, 1, _w20_prereg(
            {"metric": "spearman.r_s", "op": "<", "threshold": 0.0,
             "ci_excludes": 0}
        ))
        # r_s = +0.07 with a CI straddling 0 → rule "< 0 with CI excluding 0"
        # is VIOLATED, yet the run frames H1 supported.
        results = _w20_results(
            {"spearman": {"r_s": 0.068, "ci_95": [-0.39, 0.50]}},
            status="supported",
        )
        stage = _make_execute_stage(tmp, results, _W20_ANALYSIS)
        r = check_execute(stage)
        assert not r.ok, r.summary()
        assert any("W20" in e and "H1" in e for e in r.errors), r.errors
        assert not any("W20:" in w for w in r.warnings), r.warnings


def test_execute_w20_violated_but_not_framed_supported_is_warn():
    """Case (d): structured rule VIOLATED but the hypothesis is NOT framed
    supported (honest refuted/inconclusive) → W20 WARNs, never blocks."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(tmp, 1, _w20_prereg(
            {"metric": "spearman.r_s", "op": "<", "threshold": 0.0}
        ))
        results = _w20_results(
            {"spearman": {"r_s": 0.068}},
            status="inconclusive",
        )
        stage = _make_execute_stage(tmp, results, _W20_ANALYSIS)
        r = check_execute(stage)
        assert r.ok, r.summary()
        assert not any("W20" in e for e in r.errors), r.errors
        assert any("W20" in w and "H1" in w for w in r.warnings), r.warnings


def test_execute_w20_violated_no_status_block_is_warn():
    """Framing UNKNOWN (no structured status at all) + violation → WARN, not
    ERROR (conservative: cannot prove the run framed it supported)."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(tmp, 1, _w20_prereg(
            {"metric": "spearman.r_s", "op": ">=", "threshold": 0.5}
        ))
        results = _w20_results({"spearman": {"r_s": 0.068}})  # no status
        stage = _make_execute_stage(tmp, results, _W20_ANALYSIS)
        r = check_execute(stage)
        assert r.ok, r.summary()
        assert not any("W20" in e for e in r.errors), r.errors
        assert any("W20" in w for w in r.warnings), r.warnings


def test_execute_w20_metric_key_missing_is_warn_not_error():
    """Case (e): the named metric path is absent from results.json → WARN,
    NEVER error (a missing key is not a violation)."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(tmp, 1, _w20_prereg(
            {"metric": "nonexistent.path", "op": ">=", "threshold": 0.5}
        ))
        results = _w20_results({"spearman": {"r_s": 0.9}}, status="supported")
        stage = _make_execute_stage(tmp, results, _W20_ANALYSIS)
        r = check_execute(stage)
        assert r.ok, r.summary()
        assert not any("W20" in e for e in r.errors), r.errors
        assert any(
            "W20" in w and "not found" in w for w in r.warnings
        ), r.warnings


def test_execute_w20_ci_excludes_only_clause_violated_is_error():
    """Case (f): op holds but the ci_excludes clause is VIOLATED (the CI
    INCLUDES the excluded value) + framed supported → ERROR."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(tmp, 1, _w20_prereg(
            {"metric": "spearman.r_s", "op": ">", "threshold": 0.0,
             "ci_excludes": 0}
        ))
        # r_s = 0.068 > 0 (op holds) BUT ci_95 straddles 0 → CI clause fails.
        results = _w20_results(
            {"spearman": {"r_s": 0.068, "ci_95": [-0.39, 0.50]}},
            status="supported",
        )
        stage = _make_execute_stage(tmp, results, _W20_ANALYSIS)
        r = check_execute(stage)
        assert not r.ok, r.summary()
        assert any("W20" in e and "CI clause" in e for e in r.errors), r.errors


def test_execute_w20_ci_excludes_honored_passes():
    """ci_excludes clause HONORED (CI excludes the value) + op holds + framed
    supported → silent pass."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(tmp, 1, _w20_prereg(
            {"metric": "spearman.r_s", "op": ">", "threshold": 0.0,
             "ci_excludes": 0}
        ))
        results = _w20_results(
            {"spearman": {"r_s": 0.30, "ci_95": [0.05, 0.55]}},
            status="supported",
        )
        stage = _make_execute_stage(tmp, results, _W20_ANALYSIS)
        r = check_execute(stage)
        assert r.ok, r.summary()
        assert not any("W20" in e for e in r.errors), r.errors
        assert not any("W20:" in w for w in r.warnings), r.warnings


def test_execute_w20_ci_clause_unresolvable_is_warn_not_error():
    """ci_excludes set but no ci_95 sibling exists → WARN that the CI clause
    could not be evaluated; the op-only check still governs (here op holds →
    no violation)."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(tmp, 1, _w20_prereg(
            {"metric": "spearman.r_s", "op": ">", "threshold": 0.0,
             "ci_excludes": 0}
        ))
        results = _w20_results(
            {"spearman": {"r_s": 0.30}},  # no ci_95 sibling
            status="supported",
        )
        stage = _make_execute_stage(tmp, results, _W20_ANALYSIS)
        r = check_execute(stage)
        assert r.ok, r.summary()
        assert not any("W20" in e for e in r.errors), r.errors
        assert any("W20" in w and "ci_95" in w for w in r.warnings), r.warnings


def test_execute_w20_malformed_rule_is_warn():
    """Case (g): a malformed structured rule (bad op) → WARN, never error,
    never crash."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(tmp, 1, _w20_prereg(
            {"metric": "spearman.r_s", "op": "=>", "threshold": 0.5}
        ))
        results = _w20_results({"spearman": {"r_s": 0.9}}, status="supported")
        stage = _make_execute_stage(tmp, results, _W20_ANALYSIS)
        r = check_execute(stage)
        assert r.ok, r.summary()
        assert not any("W20" in e for e in r.errors), r.errors
        assert any("W20" in w and "malformed" in w for w in r.warnings), r.warnings


def test_execute_w20_not_an_object_rule_is_warn():
    """A `decision_rule_structured` that is not an object → WARN, not crash."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(tmp, 1, _w20_prereg("metric>=0.5"))
        results = _w20_results({"spearman": {"r_s": 0.9}}, status="supported")
        stage = _make_execute_stage(tmp, results, _W20_ANALYSIS)
        r = check_execute(stage)
        assert r.ok, r.summary()
        assert not any("W20" in e for e in r.errors), r.errors
        assert any("W20" in w for w in r.warnings), r.warnings


def test_execute_w20_status_via_hypotheses_list_is_error():
    """The framed-supported signal may come from a `hypotheses` LIST
    ({id,status}) instead of the `hypothesis_status` dict → still ERRORs on a
    violation."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(tmp, 1, _w20_prereg(
            {"metric": "spearman.r_s", "op": ">=", "threshold": 0.5}
        ))
        results = {
            "primary_model": {"auc": 0.82, "ci_95": [0.70, 0.90]},
            "spearman": {"r_s": 0.068},
            "hypotheses": [{"id": "H1", "status": "supported"}],
        }
        stage = _make_execute_stage(tmp, results, _W20_ANALYSIS)
        r = check_execute(stage)
        assert not r.ok, r.summary()
        assert any("W20" in e and "H1" in e for e in r.errors), r.errors


def test_execute_w20_no_hypotheses_block_passes_silently():
    """A preregistration with NO hypotheses list at all → W20 no-ops
    (back-compat with the many plans that declare only primary_hypothesis)."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(tmp, 1, {
            "primary_hypothesis": "H1", "primary_metric": "delta",
        })
        results = _w20_results({"spearman": {"r_s": 0.068}}, status="supported")
        stage = _make_execute_stage(tmp, results, _W20_ANALYSIS)
        r = check_execute(stage)
        assert r.ok, r.summary()
        assert not any("W20" in e for e in r.errors), r.errors
        assert not any("W20:" in w for w in r.warnings), r.warnings


# ---------------------------------------------------------------------------
# W21 — prereg-completeness gate (the ADVISORY surfacing layer over
# W13/W17/W20). Confirmatory-gated by preregistration.json presence
# (the exact signal W13/W17/W20 key on); WARN-only; PASS when all
# PreregField.ALL declarations are present.
# ---------------------------------------------------------------------------

def _w21_complete_prereg() -> dict:
    """A prereg declaring ALL confirmatory fields (2 top-level + a
    per-hypothesis decision_rule_structured), built from canonical constants."""
    return {
        PreregField.LOAD_BEARING_ARM: "delta_a_vs_b",
        PreregField.DISCRIMINATING_ARM: "delta_c_vs_b",
        PreregField.HYPOTHESES: [
            {
                "id": "H1",
                PreregField.DECISION_RULE_STRUCTURED: {
                    "metric": "comparisons.delta_a_vs_b.delta",
                    "op": ">=",
                    "threshold": 0.02,
                },
            }
        ],
    }


def test_w21_confirmatory_missing_fields_warns_naming_them():
    """W21: a confirmatory run (preregistration.json present) declaring NONE
    of the four fields WARNs, naming every missing field by canonical
    constant. Asserts the canonical severity, never a raw literal."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(tmp, 1, {
            "primary_hypothesis": "H1", "primary_metric": "delta",
        })
        stage = tmp / "002_execute"
        stage.mkdir(parents=True, exist_ok=True)
        warnings: list[str] = []
        _check_w21_prereg_completeness(stage, warnings)
        assert len(warnings) == 1, warnings
        msg = warnings[0]
        assert msg.startswith(PreregCompletenessGate.GATE_ID + ":")
        for field in PreregField.ALL:
            assert field in msg, f"{field} not named in {msg!r}"
        # Severity is surfaced WARN — the function only ever appends to the
        # warnings list (never errors), matching PreregCompletenessGate.SEVERITY.
        assert PreregCompletenessGate.SEVERITY == "warn"


def test_w21_confirmatory_partial_fields_names_only_missing():
    """W21 names ONLY the absent fields. A prereg with load_bearing_arm
    present but discriminating_arm + decision_rule_structured
    absent warns naming exactly those two, in gate order."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(tmp, 1, {
            PreregField.LOAD_BEARING_ARM: "delta_a_vs_b",
        })
        stage = tmp / "002_execute"
        stage.mkdir(parents=True, exist_ok=True)
        warnings: list[str] = []
        _check_w21_prereg_completeness(stage, warnings)
        assert len(warnings) == 1, warnings
        msg = warnings[0]
        # The structured missing-field list lives between "declaration(s): "
        # and the trailing period before the explanatory sentence.
        missing_list = msg.split("declaration(s): ", 1)[1].split(". ", 1)[0]
        assert PreregField.DISCRIMINATING_ARM in missing_list
        assert PreregField.DECISION_RULE_STRUCTURED in missing_list
        # The present fields are NOT named in the missing list.
        assert PreregField.LOAD_BEARING_ARM not in missing_list
        # Gate order preserved: discriminating_arm before decision_rule_structured.
        assert missing_list.index(PreregField.DISCRIMINATING_ARM) < missing_list.index(
            PreregField.DECISION_RULE_STRUCTURED)


def test_w21_confirmatory_all_fields_present_passes():
    """W21: a confirmatory run declaring all four fields is a silent PASS
    (no warning appended) — the gate is a no-op when complete."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(tmp, 1, _w21_complete_prereg())
        stage = tmp / "002_execute"
        stage.mkdir(parents=True, exist_ok=True)
        warnings: list[str] = []
        _check_w21_prereg_completeness(stage, warnings)
        assert warnings == [], warnings


def test_w21_exploratory_no_prereg_does_not_fire():
    """W21: an exploratory run emits NO preregistration.json → the gate must
    NOT fire (no plan-sibling prereg = not a confirmatory run). Same
    back-compat as W13/W17/W20, which all no-op without a prereg."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        # A plan dir with NO preregistration.json (exploratory).
        plan = tmp / "001_plan"
        plan.mkdir(parents=True, exist_ok=True)
        (plan / "hypotheses.md").write_text("# exploratory\n")
        stage = tmp / "002_execute"
        stage.mkdir(parents=True, exist_ok=True)
        warnings: list[str] = []
        _check_w21_prereg_completeness(stage, warnings)
        assert warnings == [], warnings


def test_w21_no_plan_sibling_does_not_fire():
    """W21 degrades gracefully: no plan sibling at all → no fire, no crash."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        stage = tmp / "002_execute"
        stage.mkdir(parents=True, exist_ok=True)
        warnings: list[str] = []
        _check_w21_prereg_completeness(stage, warnings)  # must not raise
        assert warnings == []


# ---------------------------------------------------------------------------
# W21 — LIVE-PIPELINE wiring. The direct-helper tests above prove the gate
# logic; these prove W21 is actually DISPATCHED by `check_execute` (the live
# check_execute path that runs in-band on every run), not just by
# `omicairn audit --replay-gates`. Before 2026-06-17 W21 was registered only in
# gate_replay, so a confirmatory run that under-declared its prereg fields got
# NO in-band WARN (audit S1 only partially closed). These assert the live path.
# ---------------------------------------------------------------------------

def test_w21_live_check_execute_confirmatory_under_declared_warns():
    """LIVE PATH: a confirmatory run (preregistration.json present) that
    declares none of the four PreregField.ALL fields must surface a W21 WARN
    through `check_execute` itself — and stay r.ok (WARN-only, never an
    error / never a block)."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        # A confirmatory plan whose prereg declares only the legacy
        # primary_hypothesis/primary_metric — none of the W13/W17/W20
        # fields W21 keys on.
        _write_plan_with_preregistration(tmp, 1, {
            "primary_hypothesis": "H1", "primary_metric": "delta",
        })
        # A clean execute stage (CIs in analysis match results) so the only
        # signal under test is the W21 WARN, not unrelated CI errors.
        results = {
            "primary_model": {"auc": 0.82, "ci_95": [0.677, 0.943]},
            "comparisons": {
                "delta_primary_vs_null": {"delta": 0.37, "ci_95": [0.141, 0.594]},
            },
        }
        analysis = (
            "AUC 0.82 (CI [0.677, 0.943])\n"
            "Primary vs null ΔAUC=0.37 CI [0.141, 0.594]\n"
        )
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        # WARN-only: never blocks REVIEW, never an error.
        assert r.ok, r.summary()
        assert not any(PreregCompletenessGate.GATE_ID in e for e in r.errors), r.errors
        w21 = [w for w in r.warnings if w.startswith(PreregCompletenessGate.GATE_ID + ":")]
        assert len(w21) == 1, r.warnings
        for field in PreregField.ALL:
            assert field in w21[0], f"{field} not named in live W21 warn: {w21[0]!r}"


def test_w21_live_check_execute_complete_prereg_no_warn():
    """LIVE PATH: a confirmatory run declaring all four fields is a silent PASS
    through `check_execute` — no W21 warning emitted (composes with W20: the
    complete prereg also carries an honored decision_rule_structured, so W20
    is likewise silent — the two gates do not double-fire)."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        # Complete prereg (all four fields) + a falsifiable H1 (decision_rule
        # + depends_on so W9 is also silent).
        prereg = _w21_complete_prereg()
        prereg["hypotheses"][0]["decision_rule"] = (
            "delta_a_vs_b exceeds 0.02 with 95% CI excluding 0"
        )
        prereg["hypotheses"][0]["depends_on"] = []
        _write_plan_with_preregistration(tmp, 1, prereg)
        # results.json (a) honors the structured rule (delta_a_vs_b.delta
        # >= 0.02) so W20 stays silent, and (b) RUNS the discriminating arm
        # (delta_c_vs_b present, framed resolved) so W17 stays silent — the
        # only gate under test is W21, proving it composes (no double-fire).
        results = {
            "primary_model": {"auc": 0.82, "ci_95": [0.70, 0.90]},
            "central_question": {"discriminating_arm_resolution": "resolved"},
            "comparisons": {
                "delta_a_vs_b": {"delta": 0.05, "ci_95": [0.02, 0.08]},
                "delta_c_vs_b": {"delta": 0.04, "ci_95": [0.01, 0.07]},
            },
        }
        analysis = (
            "AUC 0.82 CI [0.70, 0.90]\n"
            "Δ a vs b = 0.05 CI [0.02, 0.08]\n"
            "Δ c vs b = 0.04 CI [0.01, 0.07]\n"
        )
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert r.ok, r.summary()
        assert not any(PreregCompletenessGate.GATE_ID in w for w in r.warnings), r.warnings
        assert not any(PreregCompletenessGate.GATE_ID in e for e in r.errors), r.errors
        # W20 disjoint: no W20 warn/error either (rule honored).
        assert not any("W20:" in w for w in r.warnings), r.warnings
        assert not any("W20" in e for e in r.errors), r.errors


def test_w21_live_check_execute_exploratory_no_prereg_does_not_fire():
    """LIVE PATH: an exploratory run (no preregistration.json) must NOT trigger
    a W21 warn through `check_execute` — same back-compat as W13/W17/W20."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        # Plan dir WITHOUT preregistration.json → exploratory.
        plan = tmp / "001_plan"
        plan.mkdir(parents=True, exist_ok=True)
        (plan / "hypotheses.md").write_text("# exploratory\n")
        results = {
            "primary_model": {"auc": 0.82, "ci_95": [0.677, 0.943]},
            "comparisons": {
                "delta_primary_vs_null": {"delta": 0.37, "ci_95": [0.141, 0.594]},
            },
        }
        analysis = (
            "AUC 0.82 (CI [0.677, 0.943])\n"
            "Primary vs null ΔAUC=0.37 CI [0.141, 0.594]\n"
        )
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert r.ok, r.summary()
        assert not any(PreregCompletenessGate.GATE_ID in w for w in r.warnings), r.warnings


# ---------------------------------------------------------------------------
# ResultsField — canonical results.json schema field names (GP7: no string-
# literal soup). Regression guard for the cross_check fold that consolidated
# the scattered "primary_model"/"comparisons"/"baselines"/"conditions" bare
# literals + the per-gate `_W13_COMPARISONS_KEY` half-fold into one class.
# ---------------------------------------------------------------------------

def test_results_field_canonical_values():
    """The canonical schema names are the exact on-disk results.json keys."""
    from omicairn.constants import ResultsField

    assert ResultsField.PRIMARY_MODEL == "primary_model"
    assert ResultsField.BASELINES == "baselines"
    assert ResultsField.COMPARISONS == "comparisons"
    assert ResultsField.CONDITIONS == "conditions"


def test_w13_comparisons_key_aliases_results_field():
    """The W13 per-gate constant is a single-source-of-truth alias, not a
    re-typed literal — it must resolve to ResultsField.COMPARISONS."""
    from omicairn.constants import ResultsField
    from omicairn.cross_check import _W13_COMPARISONS_KEY

    assert _W13_COMPARISONS_KEY == ResultsField.COMPARISONS


def test_w13_w14_w17_gate_through_results_field_constant():
    """End-to-end: a results.json keyed by the ResultsField constants still
    gates exactly as before the fold — W13 resolves the load-bearing arm in
    COMPARISONS, W14 sees the BASELINES→COMPARISONS paired-delta, and the
    PRIMARY_MODEL block is read. Keys are written via the constants so a
    future divergence between class and gate would break this test."""
    from omicairn.constants import ResultsField

    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_plan_with_preregistration(
            tmp, 1, _w13_prereg("delta_primary_vs_baseline")
        )
        results = {
            ResultsField.PRIMARY_MODEL: {"auc": 0.82, "ci_95": [0.70, 0.90]},
            ResultsField.BASELINES: {
                "null": {"auc": 0.55, "ci_95": [0.45, 0.65]},
            },
            ResultsField.COMPARISONS: {
                "delta_primary_vs_baseline": {
                    "delta": 0.27, "ci_95": [0.10, 0.44]
                },
            },
        }
        analysis = (
            "AUC 0.82 CI [0.70, 0.90]\n"
            "baseline AUC 0.55 CI [0.45, 0.65]\n"
            "ΔAUC=0.27 CI [0.10, 0.44]\n"
        )
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        # W13 resolves its named arm in COMPARISONS → no W13 error; the
        # BASELINES block has a paired-delta CI in COMPARISONS → no schema error.
        assert r.ok, r.summary()
        assert not any("W13" in e for e in r.errors), r.errors


def test_baselines_without_paired_delta_errors_through_constant():
    """Inverse guard: a BASELINES block with NO paired-delta CI in COMPARISONS
    still ERRORs — proving the bottom-of-file fold reads the same fields."""
    from omicairn.constants import ResultsField

    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {
            ResultsField.PRIMARY_MODEL: {"auc": 0.82, "ci_95": [0.70, 0.90]},
            ResultsField.BASELINES: {
                "null": {"auc": 0.55, "ci_95": [0.45, 0.65]},
            },
            # no COMPARISONS block → no paired delta CI
        }
        analysis = "AUC 0.82 CI [0.70, 0.90]\nbaseline AUC 0.55 CI [0.45, 0.65]\n"
        stage = _make_execute_stage(tmp, results, analysis)
        r = check_execute(stage)
        assert not r.ok, r.summary()
        assert any(
            ResultsField.BASELINES in e and ResultsField.COMPARISONS in e
            for e in r.errors
        ), r.errors


# ---------------------------------------------------------------------------
# W32 — within-EXECUTE parallel-arm independence gate (_check_parallel_arm_contract)
# ---------------------------------------------------------------------------
#
# The gate is default-ON (via enabled_execute_checks) and a silent no-op unless
# an arm_manifest.json OR arm_provenance.jsonl is present. Manifest-level
# invariant violations are BLOCKING (surface in check_execute().errors); the
# recorded-evidence re-check is ADVISORY (warnings only, ok stays True).

from omicairn.constants import ParallelArmIndependenceGate as _PAGate

# A minimal clean single-arm execute (no manifest) reused by the no-op test.
_W32_RESULTS = {
    "primary_model": {"auc": 0.8, "ci_95": [0.7, 0.9]},
    "comparisons": {"delta_primary_vs_null": {"delta": 0.3, "ci_95": [0.1, 0.5]}},
}
_W32_ANALYSIS = "AUC 0.8 CI [0.7, 0.9]. Δ 0.3 CI [0.1, 0.5].\n"


def _write_arm_manifest(stage, arms, **top):
    payload = {"version": 1, "arms": arms, **top}
    (stage / "arm_manifest.json").write_text(json.dumps(payload))


def test_w32_no_manifest_is_noop_backward_compat():
    """The critical backward-compat case: a normal single-arm execute (no
    arm_manifest.json / arm_provenance.jsonl) sees ZERO W32 findings."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        stage = _make_execute_stage(tmp, _W32_RESULTS, _W32_ANALYSIS)
        r = check_execute(stage)
        assert not any(_PAGate.GATE_ID in e for e in r.errors), r.errors
        assert not any(_PAGate.GATE_ID in w for w in r.warnings), r.warnings


def test_w32_clean_manifest_materialized_arms_ok():
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        stage = _make_execute_stage(tmp, _W32_RESULTS, _W32_ANALYSIS)
        for c in "ab":
            d = stage / "arms" / c
            d.mkdir(parents=True)
            (d / "frag.json").write_text("{}")
        _write_arm_manifest(
            stage,
            [{"arm_id": "a", "seed": 1, "output_dir": "arms/a"},
             {"arm_id": "b", "seed": 2, "output_dir": "arms/b"}],
            max_parallel=2, gpu_slots=1,
        )
        r = check_execute(stage)
        assert not any(_PAGate.GATE_ID in e for e in r.errors), r.errors


def test_w32_overlapping_writes_is_error():
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        stage = _make_execute_stage(tmp, _W32_RESULTS, _W32_ANALYSIS)
        _write_arm_manifest(
            stage,
            [{"arm_id": "a", "seed": 1, "output_dir": "arms/x"},
             {"arm_id": "b", "seed": 2, "output_dir": "arms/x"}],
        )
        r = check_execute(stage)
        assert not r.ok
        assert any(_PAGate.GATE_ID in e and "overlapping" in e for e in r.errors)


def test_w32_duplicate_seed_is_error():
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        stage = _make_execute_stage(tmp, _W32_RESULTS, _W32_ANALYSIS)
        _write_arm_manifest(
            stage,
            [{"arm_id": "a", "seed": 5, "output_dir": "arms/a"},
             {"arm_id": "b", "seed": 5, "output_dir": "arms/b"}],
        )
        r = check_execute(stage)
        assert not r.ok
        assert any(_PAGate.GATE_ID in e and "seed" in e for e in r.errors)


def test_w32_concurrent_gpu_over_budget_is_error():
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        stage = _make_execute_stage(tmp, _W32_RESULTS, _W32_ANALYSIS)
        _write_arm_manifest(
            stage,
            [{"arm_id": "a", "seed": 1, "output_dir": "arms/a", "gpu": True},
             {"arm_id": "b", "seed": 2, "output_dir": "arms/b", "gpu": True}],
            gpu_slots=1,
        )
        r = check_execute(stage)
        assert not r.ok
        assert any(_PAGate.GATE_ID in e and "GPU" in e for e in r.errors)


def test_w32_reserved_host_is_error():
    from omicairn.constants import RESERVED_ARM_HOST
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        stage = _make_execute_stage(tmp, _W32_RESULTS, _W32_ANALYSIS)
        _write_arm_manifest(
            stage,
            [{"arm_id": "a", "seed": 1, "output_dir": "arms/a",
              "host": RESERVED_ARM_HOST}],
        )
        r = check_execute(stage)
        assert not r.ok
        assert any(_PAGate.GATE_ID in e and RESERVED_ARM_HOST in e for e in r.errors)


def test_w32_backstop_ignores_self_declared_gpu_slots():
    """HARD backstop passes gpu_slots=None → a manifest that self-declares
    gpu_slots:8 CANNOT authorize 8 concurrent GPU arms; the on-disk re-check
    still BLOCKS (the invariant is externally-budgeted, not self-declared)."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        stage = _make_execute_stage(tmp, _W32_RESULTS, _W32_ANALYSIS)
        _write_arm_manifest(
            stage,
            [{"arm_id": c, "seed": i, "output_dir": f"arms/{c}", "gpu": True}
             for i, c in enumerate("abcdefgh")],
            max_parallel=8, gpu_slots=8,
        )
        r = check_execute(stage)
        assert not r.ok
        assert any(_PAGate.GATE_ID in e and "exceed gpu_slots=1" in e for e in r.errors), r.errors


def test_w32_backstop_gpu_host_without_flag_is_error():
    """An arm routed to a GPU host but gpu:false (or omitted) must be blocked by
    the on-disk backstop — the gpu flag cannot be omitted to bypass invariant 3."""
    from omicairn.constants import GPU_ROUTED_HOSTS
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        stage = _make_execute_stage(tmp, _W32_RESULTS, _W32_ANALYSIS)
        _write_arm_manifest(
            stage,
            [{"arm_id": c, "seed": i, "output_dir": f"arms/{c}",
              "gpu": False, "host": GPU_ROUTED_HOSTS[0]}
             for i, c in enumerate("abcdefgh")],
            max_parallel=8,
        )
        r = check_execute(stage)
        assert not r.ok
        assert any(_PAGate.GATE_ID in e and "gpu=True" in e for e in r.errors), r.errors


def test_w32_recorded_evidence_is_warn_only():
    """Per-arm provenance fragments with a failed (empty) arm dir → WARN only;
    ok stays True and no W32 error is raised (advisory, WARN-first). Fragments
    are per-arm files (arms/<id>/arm_provenance.json), NOT a shared append-log."""
    from omicairn.constants import ARM_PROVENANCE_FRAGMENT
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        stage = _make_execute_stage(tmp, _W32_RESULTS, _W32_ANALYSIS)
        # A valid manifest so the blocking path is clean...
        (stage / "arms" / "a").mkdir(parents=True)
        (stage / "arms" / "a" / "frag.json").write_text("{}")
        _write_arm_manifest(
            stage,
            [{"arm_id": "a", "seed": 1, "output_dir": "arms/a"},
             {"arm_id": "b", "seed": 2, "output_dir": "arms/b"}],
        )
        # ...but arm b's recorded fragment points at a dir absent on disk (a
        # failed arm). Arm a's fragment materializes in its own dir; arm b's
        # fragment references arms/b which is never created.
        (stage / "arms" / "a" / ARM_PROVENANCE_FRAGMENT).write_text(
            json.dumps({"arm_id": "a", "seed": 1, "output_dir": "arms/a"})
        )
        bdir = stage / "arms" / "b"
        bdir.mkdir(parents=True)
        (bdir / ARM_PROVENANCE_FRAGMENT).write_text(
            json.dumps({"arm_id": "b", "seed": 2, "output_dir": "arms/bmissing"})
        )
        r = check_execute(stage)
        assert not any(_PAGate.GATE_ID in e for e in r.errors), r.errors
        assert any(_PAGate.GATE_ID in w and "FAILED arm" in w for w in r.warnings)


def test_w32_domain_opt_out_is_noop(monkeypatch):
    """When w32 is NOT in enabled_execute_checks(domain) (a domain pack opted
    out via disable_execute_checks), the gate does NOT run even on an
    overlapping-writes manifest — no W32 error surfaces."""
    import omicairn.cross_check as cc
    # Simulate a pack that opted w32 out: enabled set lacks w32.
    monkeypatch.setattr(cc, "enabled_execute_checks", lambda domain=None: frozenset())
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        stage = _make_execute_stage(tmp, _W32_RESULTS, _W32_ANALYSIS)
        _write_arm_manifest(
            stage,
            [{"arm_id": "a", "seed": 1, "output_dir": "arms/x"},
             {"arm_id": "b", "seed": 2, "output_dir": "arms/x"}],
        )
        r = check_execute(stage)
        # Gate suppressed → the overlapping-writes violation does NOT surface.
        assert not any(_PAGate.GATE_ID in e for e in r.errors), r.errors


def test_w32_default_on_for_none_domain():
    """W32 is default-ON: the None (generic) domain enables it."""
    from omicairn.cross_check import enabled_execute_checks
    assert "w32" in enabled_execute_checks(None)


# ---------------------------------------------------------------------------
# #123a/b — severity contract: build_execute_verdict routes retry_execute
# ONLY from the ERROR sink. An execute workspace with warnings-but-no-errors
# (e.g. the env-lockfile advisory, an advisory W-gate) must PROCEED, never
# retry_execute. This is the Python-side regression guard for the two-sided
# fix (the LLM-side normalizer is tested in test_verdict.py).
# ---------------------------------------------------------------------------

def test_build_execute_verdict_clean_workspace_proceeds():
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {
            "primary_model": {"auc": 0.82, "ci_95": [0.677, 0.943]},
            "comparisons": {
                "delta_primary_vs_null": {"delta": 0.37, "ci_95": [0.141, 0.594]},
            },
        }
        analysis = (
            "AUC 0.82 (CI [0.677, 0.943])\n"
            "Primary vs null ΔAUC=0.37 CI [0.141, 0.594]\n"
        )
        stage = _make_execute_stage(tmp, results, analysis)
        v, r = build_execute_verdict(stage)
        assert not r.errors
        assert v.decision == Decision.PROCEED
        assert v.decision != Decision.RETRY_EXECUTE


def test_build_execute_verdict_warnings_only_proceeds_never_retry_execute():
    """A workspace whose ONLY signals are advisory WARNINGS (here: the
    missing env-lockfile advisory) yields PROCEED. Advisory / WARN gates can
    NEVER produce a retry_execute route from Python — the ERROR sink is the
    sole trigger (bug #123b: a W21-style advisory wrongly drove a retry)."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {
            "primary_model": {"auc": 0.82, "ci_95": [0.677, 0.943]},
            "comparisons": {
                "delta_primary_vs_null": {"delta": 0.37, "ci_95": [0.141, 0.594]},
            },
        }
        analysis = (
            "AUC 0.82 (CI [0.677, 0.943])\n"
            "Primary vs null ΔAUC=0.37 CI [0.141, 0.594]\n"
        )
        # skip_env_lockfile forces a WARNING (not an error).
        stage = _make_execute_stage(tmp, results, analysis, skip_env_lockfile=True)
        v, r = build_execute_verdict(stage)
        assert not r.errors and r.warnings  # warnings-only
        assert v.decision == Decision.PROCEED
        assert v.decision != Decision.RETRY_EXECUTE
        # The warning is surfaced as evidence, not routed as a block.
        assert v.evidence == list(r.warnings)


def test_build_execute_verdict_error_routes_retry_execute():
    """Contrast: a genuine ERROR (missing paired-delta CI in analysis.md)
    DOES route retry_execute — the fix does not weaken real correctness
    blocks."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        results = {
            "primary_model": {"auc": 0.82, "ci_95": [0.677, 0.943]},
            "comparisons": {
                "delta_primary_vs_null": {"delta": 0.37, "ci_95": [0.141, 0.594]},
            },
        }
        analysis = "AUC 0.82 (CI [0.677, 0.943])\n"  # delta CI absent = ERROR
        stage = _make_execute_stage(tmp, results, analysis)
        v, r = build_execute_verdict(stage)
        assert r.errors
        assert v.decision == Decision.RETRY_EXECUTE


if __name__ == "__main__":
    test_execute_missing_comparison_ci_is_error()
    test_execute_all_cis_present_is_ok()
    test_execute_recomputed_ci_is_flagged()
    test_execute_preregistered_range_not_flagged()
    test_execute_sibling_scalar_ci_schema()
    test_execute_baselines_without_paired_delta_is_error()
    test_normalize_conditions_to_canonical()
    test_normalize_canonical_is_idempotent()
    test_execute_d4_iter2_shape_is_error()
    test_execute_d4_iter2_shape_with_comparisons_is_ok()
    test_generic_baseline_hints_have_no_biomedical_tokens()
    test_baseline_name_hints_merges_domain_pack()
    test_baseline_name_hints_generic_domain_excludes_biomedical()
    test_ci_skip_patterns_merges_domain_pack()
    test_ci_skip_patterns_generic_has_no_cox_hook()
    test_results_field_canonical_values()
    test_w13_comparisons_key_aliases_results_field()
    test_w13_w14_w17_gate_through_results_field_constant()
    test_baselines_without_paired_delta_errors_through_constant()
    test_w32_no_manifest_is_noop_backward_compat()
    test_w32_clean_manifest_materialized_arms_ok()
    test_w32_overlapping_writes_is_error()
    test_w32_duplicate_seed_is_error()
    test_w32_concurrent_gpu_over_budget_is_error()
    test_w32_reserved_host_is_error()
    test_w32_recorded_evidence_is_warn_only()
    test_w32_default_on_for_none_domain()
    test_build_execute_verdict_clean_workspace_proceeds()
    test_build_execute_verdict_warnings_only_proceeds_never_retry_execute()
    test_build_execute_verdict_error_routes_retry_execute()
    print("all cross_check tests passed")
