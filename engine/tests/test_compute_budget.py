"""Tests for omicairn.compute_budget — the small-batch throughput→ETA probe math.

This module is a go/no-go gate for hours of compute, so its arithmetic and
its branch logic (PROCEED / SHARD / SUBSAMPLE / REQUEST) must be exactly
right. The tests pin:

  - the linear extrapolation + safety margin + fixed-overhead formula;
  - each recommendation branch and the boundary between them;
  - the "no wall configured" degenerate path always PROCEEDs;
  - the unreliable-small-probe flag;
  - the d5 regression: a job whose MEASURED per-item cost overflows the wall
    is NOT recommended PROCEED (it is SHARD/REQUEST) — the whole point of the
    probe;
  - input validation (a silent bad estimate is worse than a loud failure).
"""
from __future__ import annotations

import math

import pytest

from omicairn.compute_budget import (
    MARGIN_FRACTION,
    MIN_RELIABLE_PROBE_UNITS,
    ProbeEstimate,
    Recommendation,
    estimate_from_probe,
)


# ---------------------------------------------------------------------------
# Core formula
# ---------------------------------------------------------------------------


def test_per_unit_and_projection_formula():
    """projected = per_unit * N * (1 + margin) + fixed_overhead, exactly."""
    e = estimate_from_probe(
        probe_units=10,
        probe_seconds=100.0,          # 10 s/unit
        full_units=50,
        wall_budget_s=10_000.0,
        fixed_overhead_s=30.0,
        margin_fraction=0.25,
    )
    assert e.per_unit_seconds == pytest.approx(10.0)
    expected = 10.0 * 50 * 1.25 + 30.0  # 625 + 30 = 655
    assert e.projected_full_wall_s == pytest.approx(expected)
    assert e.fits is True
    assert e.recommendation is Recommendation.PROCEED


def test_zero_margin_is_pure_linear():
    e = estimate_from_probe(
        probe_units=4, probe_seconds=8.0, full_units=100,
        wall_budget_s=1_000.0, margin_fraction=0.0,
    )
    # 2 s/unit * 100 = 200, no margin, no overhead
    assert e.projected_full_wall_s == pytest.approx(200.0)


def test_default_margin_is_module_constant():
    e = estimate_from_probe(
        probe_units=10, probe_seconds=10.0, full_units=10, wall_budget_s=1_000.0,
    )
    assert e.margin_fraction == MARGIN_FRACTION


# ---------------------------------------------------------------------------
# Recommendation branches
# ---------------------------------------------------------------------------


def test_proceed_when_fits_with_margin():
    e = estimate_from_probe(
        probe_units=20, probe_seconds=40.0, full_units=100, wall_budget_s=3_600.0,
    )
    assert e.fits is True
    assert e.recommendation is Recommendation.PROCEED
    assert e.shards_needed == 1
    assert e.max_units_within_wall == 100  # the whole run fits


def test_shard_when_overflow_and_shardable():
    e = estimate_from_probe(
        probe_units=10, probe_seconds=300.0, full_units=1000,
        wall_budget_s=3_600.0, shardable=True,
    )
    assert e.fits is False
    assert e.recommendation is Recommendation.SHARD
    assert e.shards_needed >= 2
    # shards must cover the full run: shards * max_units_per_shard >= N
    assert e.shards_needed * e.max_units_within_wall >= e.full_units
    assert 0 < e.max_units_within_wall < e.full_units


def test_subsample_when_overflow_and_not_shardable():
    e = estimate_from_probe(
        probe_units=10, probe_seconds=300.0, full_units=1000,
        wall_budget_s=3_600.0, shardable=False,
    )
    assert e.fits is False
    assert e.recommendation is Recommendation.SUBSAMPLE
    # a reduced N still fits, hence subsample (not request)
    assert e.max_units_within_wall >= 1


def test_request_when_even_one_unit_cannot_fit():
    """Fixed overhead alone blows the wall → no honest local plan → REQUEST."""
    e = estimate_from_probe(
        probe_units=5, probe_seconds=10.0, full_units=100,
        wall_budget_s=60.0, fixed_overhead_s=120.0,
    )
    assert e.recommendation is Recommendation.REQUEST
    assert e.max_units_within_wall == 0
    assert e.shards_needed == 0


def test_request_fires_even_when_shardable():
    """Shardable doesn't save you if not even one unit fits the wall."""
    e = estimate_from_probe(
        probe_units=2, probe_seconds=200.0, full_units=10,
        wall_budget_s=10.0, shardable=True,
    )
    assert e.recommendation is Recommendation.REQUEST


# ---------------------------------------------------------------------------
# Degenerate / edge paths
# ---------------------------------------------------------------------------


def test_no_wall_always_proceeds_but_still_projects():
    e = estimate_from_probe(
        probe_units=10, probe_seconds=600.0, full_units=420, wall_budget_s=0.0,
    )
    assert e.fits is True
    assert e.recommendation is Recommendation.PROCEED
    assert e.wall_budget_s == 0.0
    # projection is still computed (operator can read the absolute estimate)
    assert e.projected_full_wall_s > 0
    assert e.max_units_within_wall == e.full_units


def test_negative_wall_treated_as_no_wall():
    e = estimate_from_probe(
        probe_units=10, probe_seconds=10.0, full_units=10, wall_budget_s=-1.0,
    )
    assert e.recommendation is Recommendation.PROCEED


def test_small_probe_flagged_unreliable():
    e = estimate_from_probe(
        probe_units=MIN_RELIABLE_PROBE_UNITS - 1, probe_seconds=20.0,
        full_units=100, wall_budget_s=3_600.0,
    )
    assert e.reliable is False
    assert "WARNING" in e.human_summary()


def test_probe_at_threshold_is_reliable():
    e = estimate_from_probe(
        probe_units=MIN_RELIABLE_PROBE_UNITS, probe_seconds=20.0,
        full_units=100, wall_budget_s=3_600.0,
    )
    assert e.reliable is True


def test_fits_boundary_is_inclusive():
    """projected == wall exactly should still count as fitting (<=)."""
    # 1 s/unit, 100 units, no margin, no overhead → projected 100s; wall 100s.
    e = estimate_from_probe(
        probe_units=10, probe_seconds=10.0, full_units=100,
        wall_budget_s=100.0, margin_fraction=0.0,
    )
    assert e.projected_full_wall_s == pytest.approx(100.0)
    assert e.fits is True
    assert e.recommendation is Recommendation.PROCEED


# ---------------------------------------------------------------------------
# The d5 regression: measure-don't-guess
# ---------------------------------------------------------------------------


def test_d5_regression_measured_overflow_does_not_proceed():
    """The d5 failure was committing to a full generation that could not
    finish within the 720-min EXECUTE wall, because the per-item cost was
    GUESSED (optimistically) not MEASURED. With a real probe that measures a
    cost which overflows the wall, the probe must NOT say PROCEED — it must
    push to SHARD or REQUEST. This is the single most important behaviour in
    the module."""
    # Probe measures ~90 s/item over 15 items; full cohort is 600 items;
    # wall is 720 min; ~5 min one-time model load.
    e = estimate_from_probe(
        probe_units=15,
        probe_seconds=15 * 90.0,
        full_units=600,
        wall_budget_s=720 * 60.0,
        fixed_overhead_s=300.0,
    )
    assert e.projected_full_wall_s > e.wall_budget_s
    assert e.fits is False
    assert e.recommendation is not Recommendation.PROCEED
    assert e.recommendation in (Recommendation.SHARD, Recommendation.REQUEST)
    # And the human summary names the wall breach explicitly.
    assert "EXCEEDS" in e.human_summary()


def test_max_units_within_wall_actually_fits():
    """max_units_within_wall must be the largest N whose projection fits —
    i.e. projecting that N fits, projecting N+1 does not (modulo int floor)."""
    e = estimate_from_probe(
        probe_units=10, probe_seconds=300.0, full_units=1000,
        wall_budget_s=3_600.0,
    )
    n = e.max_units_within_wall
    assert n >= 1
    fit_n = estimate_from_probe(
        probe_units=10, probe_seconds=300.0, full_units=n,
        wall_budget_s=3_600.0,
    )
    assert fit_n.fits is True
    # N+1 (capped at full_units) should not fit if we were truly at the edge
    if n < e.full_units:
        over = estimate_from_probe(
            probe_units=10, probe_seconds=300.0, full_units=n + 1,
            wall_budget_s=3_600.0,
        )
        assert over.fits is False


# ---------------------------------------------------------------------------
# Input validation — loud failure beats a silent bad estimate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kwargs", [
    {"probe_units": 0, "probe_seconds": 10.0, "full_units": 10, "wall_budget_s": 100.0},
    {"probe_units": -1, "probe_seconds": 10.0, "full_units": 10, "wall_budget_s": 100.0},
    {"probe_units": 10, "probe_seconds": 10.0, "full_units": 0, "wall_budget_s": 100.0},
    {"probe_units": 10, "probe_seconds": -1.0, "full_units": 10, "wall_budget_s": 100.0},
    {"probe_units": 10, "probe_seconds": 10.0, "full_units": 10, "wall_budget_s": 100.0,
     "fixed_overhead_s": -5.0},
    {"probe_units": 10, "probe_seconds": 10.0, "full_units": 10, "wall_budget_s": 100.0,
     "margin_fraction": -0.1},
])
def test_invalid_inputs_raise(kwargs):
    with pytest.raises(ValueError):
        estimate_from_probe(**kwargs)


def test_human_summary_is_auditable():
    """The summary must echo the raw probe numbers so a reviewer can redo the
    arithmetic by hand (the estimate must not be a black box)."""
    e = estimate_from_probe(
        probe_units=12, probe_seconds=240.0, full_units=300, wall_budget_s=3_600.0,
    )
    s = e.human_summary()
    assert "12 units" in s
    assert "240" in s or "240.0" in s
    assert "300 units" in s
    assert "Recommendation:" in s


def test_estimate_is_frozen_dataclass():
    import dataclasses as _dc
    e = estimate_from_probe(
        probe_units=10, probe_seconds=10.0, full_units=10, wall_budget_s=100.0,
    )
    assert isinstance(e, ProbeEstimate)
    with pytest.raises(_dc.FrozenInstanceError):
        e.fits = False  # type: ignore[misc]


def test_subsecond_per_unit_renders_in_ms():
    e = estimate_from_probe(
        probe_units=1000, probe_seconds=0.5, full_units=10_000, wall_budget_s=3_600.0,
    )
    # 0.5/1000 = 0.0005 s/unit -> rendered in ms
    assert e.per_unit_seconds == pytest.approx(0.0005)
    assert "ms/unit" in e.human_summary()


def test_shards_needed_is_ceiling():
    """shards_needed must be a ceiling so the shards actually cover N."""
    e = estimate_from_probe(
        probe_units=10, probe_seconds=300.0, full_units=1000, wall_budget_s=3_600.0,
    )
    assert e.shards_needed == math.ceil(e.full_units / e.max_units_within_wall)
