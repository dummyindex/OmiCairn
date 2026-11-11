"""Tests for the EXECUTE-phase wall-budget FLOOR for GPU/multimodal runs.

Motivation (2026-06-15 wave-2 incident): 4 of 6 world-model runs were KILLED
mid-EXECUTE by the per-stage hard wall cap (180 min) — they were NOT hung, just
genuine long multi-VLM probing (forward passes over several <=7B VLMs take
3-6 h). The fix raises the EFFECTIVE EXECUTE wall to a 6 h FLOOR when the run is
confidently GPU-heavy / multimodal, while never lowering an explicit higher
user value, and never touching non-EXECUTE phases.

These exercise the pure helpers (`_is_gpu_heavy_run`,
`_resolve_phase_wall_seconds`) directly so the budget arithmetic is covered
without spinning up a full `claude -p` stage, mirroring the focused-helper
style of test_compute_resources / test_research_md.
"""
from __future__ import annotations

import pytest

from omicairn import orchestrator
from omicairn.compute_resources import ComputeResource, DEFAULT_LOCAL
from omicairn.constants import PhaseName


# A tiny ResearchMd stand-in carrying only the fields the budget resolver
# reads. Keeps the test focused on the floor logic (the real ResearchMd
# constructor takes ~30 fields; test_research_md covers its parsing).
class _StubRM:
    def __init__(
        self,
        *,
        compute=DEFAULT_LOCAL,
        modalities=None,
        max_phase_wall_minutes=0,
        max_phase_wall_minutes_by_phase=None,
    ):
        self.compute = compute
        self.modalities = modalities or []
        self.max_phase_wall_minutes = max_phase_wall_minutes
        self.max_phase_wall_minutes_by_phase = max_phase_wall_minutes_by_phase or {}


_GPU = ComputeResource(id="gpu-node", display_name="A100 node", kind="gpu", gpus=8)
_CPU = DEFAULT_LOCAL  # kind="cpu", gpus=0
_FLOOR_S = orchestrator.GPU_EXECUTE_WALL_FLOOR_MINUTES * 60  # 360 min -> 21600 s
_PHASE_DEFAULT_S = 240 * 60  # a representative phase built-in default


# ---------------------------------------------------------------------------
# is-GPU / multimodal detection
# ---------------------------------------------------------------------------

def test_constant_value_is_six_hours():
    assert orchestrator.GPU_EXECUTE_WALL_FLOOR_MINUTES == 360


def test_gpu_resource_is_gpu_heavy():
    assert orchestrator._is_gpu_heavy_run(_StubRM(compute=_GPU)) is True


def test_cluster_kind_is_gpu_heavy_even_without_has_gpu():
    # A categorical "cluster" with gpus unset still counts (kind fallback).
    clus = ComputeResource(id="slurm", kind="cluster", gpus=0)
    assert orchestrator._is_gpu_heavy_run(_StubRM(compute=clus)) is True


@pytest.mark.parametrize("modality", [
    "vision", "video", "image", "video_world_model", "Vision", "AUDIO", "speech",
])
def test_multimodal_modalities_are_gpu_heavy(modality):
    rm = _StubRM(compute=_CPU, modalities=[modality])
    assert orchestrator._is_gpu_heavy_run(rm) is True


def test_cpu_text_run_is_not_gpu_heavy():
    rm = _StubRM(compute=_CPU, modalities=["tabular", "text"])
    assert orchestrator._is_gpu_heavy_run(rm) is False


def test_unparseable_signals_are_not_gpu_heavy_and_do_not_crash():
    class Broken:
        pass  # no .compute, no .modalities

    assert orchestrator._is_gpu_heavy_run(Broken()) is False


def test_non_string_modalities_are_ignored_not_crash():
    rm = _StubRM(compute=_CPU, modalities=[None, 123, {"x": 1}])
    assert orchestrator._is_gpu_heavy_run(rm) is False


# ---------------------------------------------------------------------------
# Floor application via the phase-wall resolver
# ---------------------------------------------------------------------------

def test_a_gpu_vision_execute_180_raised_to_floor():
    """(a) GPU+vision run with execute=180 -> raised to 360."""
    rm = _StubRM(
        compute=_GPU, modalities=["vision"], max_phase_wall_minutes=180,
    )
    eff, floored_from = orchestrator._resolve_phase_wall_seconds(
        rm, PhaseName.EXECUTE, _PHASE_DEFAULT_S,
    )
    assert eff == _FLOOR_S                      # raised to 360 min
    assert floored_from == 180 * 60             # reported prior value


def test_b_gpu_explicit_higher_execute_stays():
    """(b) GPU run with execute=480 explicit -> stays 480 (floor doesn't lower)."""
    rm = _StubRM(
        compute=_GPU, max_phase_wall_minutes_by_phase={PhaseName.EXECUTE: 480},
        max_phase_wall_minutes=180,
    )
    eff, floored_from = orchestrator._resolve_phase_wall_seconds(
        rm, PhaseName.EXECUTE, _PHASE_DEFAULT_S,
    )
    assert eff == 480 * 60                      # explicit higher value kept
    assert floored_from is None                 # no floor applied -> no log


def test_b2_gpu_casewide_higher_execute_stays():
    """Case-wide max_phase_wall_minutes above the floor is also kept."""
    rm = _StubRM(compute=_GPU, modalities=["video"], max_phase_wall_minutes=600)
    eff, floored_from = orchestrator._resolve_phase_wall_seconds(
        rm, PhaseName.EXECUTE, _PHASE_DEFAULT_S,
    )
    assert eff == 600 * 60
    assert floored_from is None


def test_c_cpu_theory_text_execute_180_unchanged():
    """(c) CPU-only / text run with execute=180 -> stays 180 (floor NOT applied)."""
    rm = _StubRM(compute=_CPU, modalities=["text"], max_phase_wall_minutes=180)
    eff, floored_from = orchestrator._resolve_phase_wall_seconds(
        rm, PhaseName.EXECUTE, _PHASE_DEFAULT_S,
    )
    assert eff == 180 * 60                      # left exactly as configured
    assert floored_from is None


def test_d_absent_unparseable_no_floor_no_crash():
    """(d) absent/unparseable modalities+compute -> no floor, no crash.

    Falls back through the normal resolution to the phase default.
    """
    class Broken:
        pass

    eff, floored_from = orchestrator._resolve_phase_wall_seconds(
        Broken(), PhaseName.EXECUTE, _PHASE_DEFAULT_S,
    )
    assert eff == _PHASE_DEFAULT_S              # untouched phase default
    assert floored_from is None


def test_floor_is_execute_phase_only():
    """A GPU/vision run does NOT get the floor on non-EXECUTE phases — a 6 h
    floor on SCOPE/PLAN/REVIEW would mask real hangs."""
    rm = _StubRM(compute=_GPU, modalities=["vision"], max_phase_wall_minutes=180)
    for phase in (PhaseName.SCOPE, PhaseName.PLAN, PhaseName.REVIEW):
        eff, floored_from = orchestrator._resolve_phase_wall_seconds(
            rm, phase, _PHASE_DEFAULT_S,
        )
        assert eff == 180 * 60, f"{phase} should not be floored"
        assert floored_from is None


def test_gpu_run_with_no_budget_set_floored_from_phase_default():
    """A GPU run that left the budget unset (0) resolves to the phase default,
    then is raised to the floor when the default is below it."""
    rm = _StubRM(compute=_GPU, modalities=["vision"], max_phase_wall_minutes=0)
    low_default = 180 * 60
    eff, floored_from = orchestrator._resolve_phase_wall_seconds(
        rm, PhaseName.EXECUTE, low_default,
    )
    assert eff == _FLOOR_S
    assert floored_from == low_default


def test_per_phase_override_below_floor_is_raised():
    """An explicit per-phase execute override BELOW the floor is still raised
    (the floor protects against a too-tight explicit value too)."""
    rm = _StubRM(
        compute=_GPU, max_phase_wall_minutes_by_phase={PhaseName.EXECUTE: 120},
    )
    eff, floored_from = orchestrator._resolve_phase_wall_seconds(
        rm, PhaseName.EXECUTE, _PHASE_DEFAULT_S,
    )
    assert eff == _FLOOR_S
    assert floored_from == 120 * 60
