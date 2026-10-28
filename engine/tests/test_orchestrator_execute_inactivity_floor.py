"""Tests for the EXECUTE-phase INACTIVITY-watchdog FLOOR for GPU/multimodal runs.

Motivation (2026-06-15 d3 incident, wm-dynamics-decouple / CLEVRER video
dynamics): the EXECUTE turn was FALSE-KILLED by the 2700s inactivity watchdog
while its remote GPU job was GENUINELY PROGRESSING — the agent's bash poll loop
went quiet (stopped emitting PROG / touching .heartbeat) for >45 min while real
per-shard GPU work continued, so since_last_growth_s climbed to the kill. The
fix raises the inactivity timeout to a 90 min FLOOR
(GPU_INACTIVITY_FLOOR_SECONDS) for confidently GPU-heavy / multimodal EXECUTE
phases, applied as max(configured, floor) by claude_runner (never lowering an
explicit-higher value, never enabling a disabled watchdog), and never touching
non-EXECUTE phases or CPU/theory runs. This is the inactivity-watchdog SIBLING
of the EXECUTE wall floor (test_orchestrator_execute_wall_floor).

These exercise the pure helper (`_resolve_inactivity_floor_seconds`) directly,
and confirm it REUSES the same `_is_gpu_heavy_run` predicate as the wall floor,
mirroring the focused-helper style of test_orchestrator_execute_wall_floor.
"""
from __future__ import annotations

import pytest

from omicairn import orchestrator
from omicairn.compute_resources import ComputeResource, DEFAULT_LOCAL
from omicairn.constants import PhaseName


class _StubRM:
    """Minimal ResearchMd stand-in carrying only the fields the GPU-detection
    predicate reads (same shape as the wall-floor test's stub)."""

    def __init__(self, *, compute=DEFAULT_LOCAL, modalities=None):
        self.compute = compute
        self.modalities = modalities or []
        # The inactivity-floor resolver does not read wall fields, but keep
        # them present so the stub is interchangeable with the wall-floor one.
        self.max_phase_wall_minutes = 0
        self.max_phase_wall_minutes_by_phase = {}


_GPU = ComputeResource(id="gpu-node", display_name="A100 node", kind="gpu", gpus=8)
_CPU = DEFAULT_LOCAL  # kind="cpu", gpus=0
_FLOOR_S = orchestrator.GPU_INACTIVITY_FLOOR_SECONDS  # 5400 s = 90 min


# ---------------------------------------------------------------------------
# Constant + floor-value resolution
# ---------------------------------------------------------------------------

def test_constant_value_is_ninety_minutes():
    assert orchestrator.GPU_INACTIVITY_FLOOR_SECONDS == 5400


def test_a_gpu_multimodal_execute_gets_floor():
    """(a) GPU + multimodal (vision) EXECUTE → floor 5400 returned (so a
    configured 2700 gets raised to 5400 downstream)."""
    rm = _StubRM(compute=_GPU, modalities=["vision"])
    assert orchestrator._resolve_inactivity_floor_seconds(
        rm, PhaseName.EXECUTE
    ) == _FLOOR_S


def test_a2_pure_modality_video_execute_gets_floor():
    """A multimodal-only run (no GPU resource) still trips on modality."""
    rm = _StubRM(compute=_CPU, modalities=["video"])
    assert orchestrator._resolve_inactivity_floor_seconds(
        rm, PhaseName.EXECUTE
    ) == _FLOOR_S


def test_c_cpu_theory_execute_no_floor():
    """(c) CPU-only / pure-theory / text run → no floor (keeps the default
    2700/whatever-configured inactivity timeout)."""
    rm = _StubRM(compute=_CPU, modalities=["text", "tabular"])
    assert orchestrator._resolve_inactivity_floor_seconds(
        rm, PhaseName.EXECUTE
    ) is None


def test_floor_is_execute_phase_only():
    """A GPU/vision run gets NO inactivity floor on non-EXECUTE phases — a
    90 min inactivity window on cheap SCOPE/PLAN/REVIEW would mask real
    hangs there (mirrors the wall-floor's execute-only restriction)."""
    rm = _StubRM(compute=_GPU, modalities=["vision"])
    for phase in (PhaseName.SCOPE, PhaseName.PLAN, PhaseName.REVIEW):
        assert orchestrator._resolve_inactivity_floor_seconds(rm, phase) is None, \
            f"{phase} must not be floored"


def test_d_absent_unparseable_no_floor_no_crash():
    """(d) absent/unparseable compute+modalities → no floor, no crash
    (degrade-safe: we never floor a run we can't confidently classify)."""
    class Broken:
        pass  # no .compute, no .modalities

    assert orchestrator._resolve_inactivity_floor_seconds(
        Broken(), PhaseName.EXECUTE
    ) is None


# ---------------------------------------------------------------------------
# (e) Reuses the SAME _is_gpu_heavy_run predicate as the wall floor — the
# inactivity floor must NOT reimplement GPU detection (no string-soup).
# ---------------------------------------------------------------------------

def test_e_reuses_is_gpu_heavy_run_predicate(monkeypatch):
    """The resolver delegates GPU detection to orchestrator._is_gpu_heavy_run
    (the shared predicate). Patching it flips the floor decision both ways,
    proving the inactivity floor and the wall floor key on the SAME signal."""
    rm = _StubRM(compute=_CPU, modalities=["text"])  # would be non-GPU

    monkeypatch.setattr(orchestrator, "_is_gpu_heavy_run", lambda _rm: True)
    assert orchestrator._resolve_inactivity_floor_seconds(
        rm, PhaseName.EXECUTE
    ) == _FLOOR_S

    monkeypatch.setattr(orchestrator, "_is_gpu_heavy_run", lambda _rm: False)
    assert orchestrator._resolve_inactivity_floor_seconds(
        rm, PhaseName.EXECUTE
    ) is None


def test_e2_floor_matches_is_gpu_heavy_run_across_cases():
    """For a range of stubs, "floor applies on EXECUTE" iff _is_gpu_heavy_run
    is True — pinning that the two share one source of truth."""
    cases = [
        _StubRM(compute=_GPU),
        _StubRM(compute=_CPU, modalities=["image"]),
        _StubRM(compute=_CPU, modalities=["text"]),
        _StubRM(compute=ComputeResource(id="slurm", kind="cluster", gpus=0)),
    ]
    for rm in cases:
        floored = orchestrator._resolve_inactivity_floor_seconds(
            rm, PhaseName.EXECUTE
        ) is not None
        assert floored == orchestrator._is_gpu_heavy_run(rm)
