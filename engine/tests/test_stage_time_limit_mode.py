"""Tests for the DYNAMIC vs HARD stage time-limit mode + the dynamic
execute-wall ESTIMATOR (owner directive 2026-07-01).

Background: OmiCairn's per-phase WALL cap was a progress-BLIND kill — a
legitimately-long but PROGRESSING GPU/FT/inference arm hit the wall and was
truncated (the d7 example-study-compositional-steering LLaVA-NeXT held-out drop).
The fix demotes the execute-class wall to a LIVENESS-GATED SOFT CEILING in
DYNAMIC mode (default) — the kill decision defers to the existing inactivity
predicate — with a hard safety ceiling as the one absolute bound; HARD mode
keeps the historical unconditional wall kill for determinism.

These exercise the pure resolver (`constants.stage_time_limit_mode`), the
research_md parse of `budgets.stage_time_limit_mode` + `workload:`, and the
pure estimator (`orchestrator.estimate_execute_wall_seconds`) directly —
mirroring the focused-helper style of test_orchestrator_execute_wall_floor.
The watchdog kill-gating cases live in test_claude_runner.py.
"""
from __future__ import annotations

import pathlib

import pytest

from omicairn import constants, orchestrator, research_md
from omicairn.compute_resources import ComputeResource, DEFAULT_LOCAL
from omicairn.constants import PhaseName

_GPU = ComputeResource(id="gpu-node", display_name="A100", kind="gpu", gpus=8)
_CPU = DEFAULT_LOCAL


class _StubRM:
    """Minimal ResearchMd stand-in carrying only the fields the mode
    resolver / estimator read."""

    def __init__(
        self,
        *,
        stage_time_limit_mode=constants.DEFAULT_STAGE_TIME_LIMIT_MODE,
        compute=_CPU,
        modalities=None,
        workload_is_finetune=False,
        workload_n_models=None,
        workload_n_eval_items=None,
    ):
        self.stage_time_limit_mode = stage_time_limit_mode
        self.compute = compute
        self.modalities = modalities or []
        self.workload_is_finetune = workload_is_finetune
        self.workload_n_models = workload_n_models
        self.workload_n_eval_items = workload_n_eval_items


# ---------------------------------------------------------------------------
# constants.stage_time_limit_mode resolver
# ---------------------------------------------------------------------------

def test_default_mode_is_dynamic(monkeypatch):
    monkeypatch.delenv(constants.ENV_STAGE_TIME_LIMIT_MODE, raising=False)
    assert constants.stage_time_limit_mode() == constants.STAGE_TIME_LIMIT_MODE_DYNAMIC
    assert constants.DEFAULT_STAGE_TIME_LIMIT_MODE == "dynamic"


def test_env_override_hard(monkeypatch):
    monkeypatch.setenv(constants.ENV_STAGE_TIME_LIMIT_MODE, "hard")
    assert constants.stage_time_limit_mode() == constants.STAGE_TIME_LIMIT_MODE_HARD


def test_env_override_is_case_insensitive_and_trimmed(monkeypatch):
    monkeypatch.setenv(constants.ENV_STAGE_TIME_LIMIT_MODE, "  HARD  ")
    assert constants.stage_time_limit_mode() == constants.STAGE_TIME_LIMIT_MODE_HARD


def test_env_unrecognized_degrades_to_default(monkeypatch):
    monkeypatch.setenv(constants.ENV_STAGE_TIME_LIMIT_MODE, "banana")
    assert constants.stage_time_limit_mode() == constants.STAGE_TIME_LIMIT_MODE_DYNAMIC


def test_env_beats_research_md(monkeypatch):
    monkeypatch.setenv(constants.ENV_STAGE_TIME_LIMIT_MODE, "hard")
    rm = _StubRM(stage_time_limit_mode="dynamic")
    assert constants.stage_time_limit_mode(rm) == constants.STAGE_TIME_LIMIT_MODE_HARD


def test_research_md_mode_used_when_no_env(monkeypatch):
    monkeypatch.delenv(constants.ENV_STAGE_TIME_LIMIT_MODE, raising=False)
    rm = _StubRM(stage_time_limit_mode="hard")
    assert constants.stage_time_limit_mode(rm) == constants.STAGE_TIME_LIMIT_MODE_HARD


def test_research_md_unrecognized_degrades(monkeypatch):
    monkeypatch.delenv(constants.ENV_STAGE_TIME_LIMIT_MODE, raising=False)
    rm = _StubRM(stage_time_limit_mode="nonsense")
    assert constants.stage_time_limit_mode(rm) == constants.STAGE_TIME_LIMIT_MODE_DYNAMIC


def test_resolver_none_research_md_no_crash(monkeypatch):
    monkeypatch.delenv(constants.ENV_STAGE_TIME_LIMIT_MODE, raising=False)
    assert constants.stage_time_limit_mode(None) == constants.STAGE_TIME_LIMIT_MODE_DYNAMIC


def test_hard_ceiling_is_three_day_total_wall():
    assert (
        constants.EXECUTE_HARD_CEILING_SECONDS
        == constants.DEFAULT_MAX_TOTAL_WALL_MINUTES * 60
    )


# ---------------------------------------------------------------------------
# research_md parse of budgets.stage_time_limit_mode + workload:
# ---------------------------------------------------------------------------

def _write_case(tmp_path: pathlib.Path, cfg_yaml: str) -> pathlib.Path:
    """Write a minimal RESEARCH.md + omicairn_config.yaml case and return the
    RESEARCH.md path."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    rmd = tmp_path / "RESEARCH.md"
    rmd.write_text(
        "research_mode: confirmatory\n"
        "domain: interp\n"
        "topic_slug: t\n"
        "research_question: does X hold?\n"
        f"data:\n  location: {data_dir}\n"
    )
    (tmp_path / "omicairn_config.yaml").write_text(cfg_yaml)
    return rmd


def test_absent_mode_defaults_to_dynamic(tmp_path):
    rmd = _write_case(tmp_path, "budgets:\n  max_phase_wall_minutes: 240\n")
    rm = research_md.load(rmd)
    assert rm.stage_time_limit_mode == "dynamic"


def test_configured_hard_mode_parsed(tmp_path):
    rmd = _write_case(tmp_path, "budgets:\n  stage_time_limit_mode: hard\n")
    rm = research_md.load(rmd)
    assert rm.stage_time_limit_mode == "hard"


def test_configured_bogus_mode_degrades_to_dynamic(tmp_path):
    rmd = _write_case(tmp_path, "budgets:\n  stage_time_limit_mode: SLURM\n")
    rm = research_md.load(rmd)
    assert rm.stage_time_limit_mode == "dynamic"


def test_workload_block_parsed(tmp_path):
    rmd = _write_case(
        tmp_path,
        "workload:\n  n_models: 4\n  n_eval_items: 500\n  is_finetune: true\n",
    )
    rm = research_md.load(rmd)
    assert rm.workload_n_models == 4
    assert rm.workload_n_eval_items == 500
    assert rm.workload_is_finetune is True


def test_workload_absent_all_none(tmp_path):
    rmd = _write_case(tmp_path, "budgets:\n  max_phase_wall_minutes: 240\n")
    rm = research_md.load(rmd)
    assert rm.workload_n_models is None
    assert rm.workload_n_eval_items is None
    assert rm.workload_is_finetune is False


def test_workload_bad_values_degrade_to_none(tmp_path):
    rmd = _write_case(
        tmp_path,
        "workload:\n  n_models: not_a_number\n  n_eval_items: -5\n",
    )
    rm = research_md.load(rmd)
    assert rm.workload_n_models is None   # unparseable → None
    assert rm.workload_n_eval_items is None  # <=0 → None


# ---------------------------------------------------------------------------
# estimate_execute_wall_seconds — pure function
# ---------------------------------------------------------------------------

_BASE = 3600  # 1 h resolved base


def test_estimate_non_execute_is_noop():
    secs, basis = orchestrator.estimate_execute_wall_seconds(
        _StubRM(), PhaseName.REVIEW, _BASE, [])
    assert secs == _BASE
    assert basis == "non_execute"


def test_estimate_no_history_cpu_stays_at_base():
    secs, basis = orchestrator.estimate_execute_wall_seconds(
        _StubRM(compute=_CPU), PhaseName.EXECUTE, _BASE, [])
    assert secs == _BASE
    assert basis == "base"


def test_estimate_no_history_gpu_multiplies():
    secs, basis = orchestrator.estimate_execute_wall_seconds(
        _StubRM(compute=_GPU), PhaseName.EXECUTE, _BASE, [])
    assert secs == int(_BASE * orchestrator._EST_GPU_MULTIPLIER)
    assert "gpu" in basis


def test_estimate_finetune_multiplies_further():
    secs, _ = orchestrator.estimate_execute_wall_seconds(
        _StubRM(compute=_GPU, workload_is_finetune=True),
        PhaseName.EXECUTE, _BASE, [])
    assert secs == int(
        _BASE * orchestrator._EST_GPU_MULTIPLIER
        * orchestrator._EST_FINETUNE_MULTIPLIER
    )


def test_estimate_n_models_scales_but_is_capped():
    # n_models far above the cap must clamp to _EST_WORKLOAD_MAX_MULTIPLIER.
    secs, _ = orchestrator.estimate_execute_wall_seconds(
        _StubRM(compute=_CPU, workload_n_models=10_000),
        PhaseName.EXECUTE, _BASE, [])
    assert secs == int(_BASE * orchestrator._EST_WORKLOAD_MAX_MULTIPLIER)


def test_estimate_history_seed_max_times_1_5():
    secs, basis = orchestrator.estimate_execute_wall_seconds(
        _StubRM(compute=_GPU), PhaseName.EXECUTE, _BASE, [7200.0, 3000.0])
    assert secs == int(7200.0 * orchestrator._EST_HISTORY_MULTIPLIER)
    assert basis == "history"


def test_estimate_never_below_base_floor_invariant():
    # History shorter than base → the base floor still wins (never lower it).
    secs, _ = orchestrator.estimate_execute_wall_seconds(
        _StubRM(compute=_CPU), PhaseName.EXECUTE, _BASE, [60.0])
    assert secs >= _BASE


def test_estimate_missing_workload_fields_degrade_to_base():
    class Broken:  # no workload_* attrs, no compute, no modalities
        pass

    secs, basis = orchestrator.estimate_execute_wall_seconds(
        Broken(), PhaseName.EXECUTE, _BASE, [])
    assert secs == _BASE
    assert basis == "base"


# ---------------------------------------------------------------------------
# _prior_execute_wall_seconds — bounded events.jsonl scan
# ---------------------------------------------------------------------------

class _StubWS:
    def __init__(self, root):
        self.root = root


def test_prior_wall_scan_reads_execute_beacons(tmp_path):
    import json

    events = tmp_path / constants.EVENTS_LOG
    events.write_text(
        "\n".join(json.dumps(e) for e in [
            {"kind": constants.EventKind.EXECUTE_PROGRESS,
             "stage": "002_execute", "elapsed_s": 1200.0},
            {"kind": constants.EventKind.EXECUTE_PROGRESS,
             "stage": "002_execute", "elapsed_s": 3400.0},  # later, larger
            {"kind": constants.EventKind.STAGE_TIMEOUT,
             "stage": "005_execute", "seconds": 7200},
            # a non-EXECUTE beacon must NOT contribute
            {"kind": constants.EventKind.EXECUTE_PROGRESS,
             "stage": "008_review", "elapsed_s": 999999.0},
        ]) + "\n"
    )
    vals = sorted(orchestrator._prior_execute_wall_seconds(_StubWS(tmp_path)))
    assert vals == [3400.0, 7200.0]  # per-stage max, execute-only


def test_prior_wall_scan_missing_log_returns_empty(tmp_path):
    assert orchestrator._prior_execute_wall_seconds(_StubWS(tmp_path)) == []


def test_prior_wall_scan_corrupt_lines_skipped(tmp_path):
    events = tmp_path / constants.EVENTS_LOG
    events.write_text(
        'not json\n{"kind": "execute_progress", "stage": "002_execute", '
        '"elapsed_s": 100.0}\n{bad\n'
    )
    assert orchestrator._prior_execute_wall_seconds(_StubWS(tmp_path)) == [100.0]
