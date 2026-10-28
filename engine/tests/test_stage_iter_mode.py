"""Tests for the DYNAMIC vs HARD stage ITERATION mode + the zero-progress
spin-catcher (owner directive 2026-07-01, #133 — SIBLING of #131's
stage_time_limit_mode).

Background: OmiCairn's per-phase iteration cap (max_iters_per_phase) was a HARD,
SCIENCE-SHAPING bound — a run that was already converging kept re-opening a
late phase toward the 12/16 cap and near-halted. The fix raises the default
to a high, NON-BINDING SAFETY CEILING (100) and makes DYNAMIC (default) the
mode where the real terminators are the stop-conditions (review done,
cross_check proceed) — the cap only trips on pathological non-convergence.
The new safety that makes "uncapped" safe is the ZERO-PROGRESS guard: a phase
that re-opens N times emitting the same output is halted. HARD keeps the fixed
cap as a deterministic terminator.

These exercise the pure resolver (`constants.stage_iter_mode`), the research_md
parse of `budgets.stage_iter_mode`, and the state-level fingerprint/ceiling
seams (`state.bounds_exceeded` mode-awareness + `record_phase_progress` /
`zero_progress_exceeded`) directly — mirroring test_stage_time_limit_mode.py.
"""
from __future__ import annotations

import pathlib

from omicairn import constants, research_md, state
from omicairn.constants import Decision, PhaseName


class _StubRM:
    """Minimal ResearchMd stand-in carrying only stage_iter_mode."""

    def __init__(self, *, stage_iter_mode=constants.DEFAULT_STAGE_ITER_MODE):
        self.stage_iter_mode = stage_iter_mode


# ---------------------------------------------------------------------------
# constants.stage_iter_mode resolver (mirrors the time-limit resolver tests)
# ---------------------------------------------------------------------------

def test_default_mode_is_dynamic(monkeypatch):
    monkeypatch.delenv(constants.ENV_STAGE_ITER_MODE, raising=False)
    assert constants.stage_iter_mode() == constants.STAGE_ITER_MODE_DYNAMIC
    assert constants.DEFAULT_STAGE_ITER_MODE == "dynamic"


def test_env_override_hard(monkeypatch):
    monkeypatch.setenv(constants.ENV_STAGE_ITER_MODE, "hard")
    assert constants.stage_iter_mode() == constants.STAGE_ITER_MODE_HARD


def test_env_override_is_case_insensitive_and_trimmed(monkeypatch):
    monkeypatch.setenv(constants.ENV_STAGE_ITER_MODE, "  HARD  ")
    assert constants.stage_iter_mode() == constants.STAGE_ITER_MODE_HARD


def test_env_unrecognized_degrades_to_default(monkeypatch):
    monkeypatch.setenv(constants.ENV_STAGE_ITER_MODE, "banana")
    assert constants.stage_iter_mode() == constants.STAGE_ITER_MODE_DYNAMIC


def test_env_beats_research_md(monkeypatch):
    monkeypatch.setenv(constants.ENV_STAGE_ITER_MODE, "hard")
    rm = _StubRM(stage_iter_mode="dynamic")
    assert constants.stage_iter_mode(rm) == constants.STAGE_ITER_MODE_HARD


def test_research_md_mode_used_when_no_env(monkeypatch):
    monkeypatch.delenv(constants.ENV_STAGE_ITER_MODE, raising=False)
    rm = _StubRM(stage_iter_mode="hard")
    assert constants.stage_iter_mode(rm) == constants.STAGE_ITER_MODE_HARD


def test_research_md_unrecognized_degrades(monkeypatch):
    monkeypatch.delenv(constants.ENV_STAGE_ITER_MODE, raising=False)
    rm = _StubRM(stage_iter_mode="nonsense")
    assert constants.stage_iter_mode(rm) == constants.STAGE_ITER_MODE_DYNAMIC


def test_resolver_none_research_md_no_crash(monkeypatch):
    monkeypatch.delenv(constants.ENV_STAGE_ITER_MODE, raising=False)
    assert constants.stage_iter_mode(None) == constants.STAGE_ITER_MODE_DYNAMIC


def test_default_max_iters_is_high_ceiling():
    # The raised default is a non-binding safety ceiling, well above the old
    # 5/12/16 science-shaping caps.
    assert constants.DEFAULT_MAX_ITERS_PER_PHASE >= 50


# ---------------------------------------------------------------------------
# research_md parse of budgets.stage_iter_mode
# ---------------------------------------------------------------------------

def _write_case(tmp_path: pathlib.Path, cfg_yaml: str) -> pathlib.Path:
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
    assert rm.stage_iter_mode == "dynamic"


def test_configured_hard_mode_parsed(tmp_path):
    rmd = _write_case(tmp_path, "budgets:\n  stage_iter_mode: hard\n")
    rm = research_md.load(rmd)
    assert rm.stage_iter_mode == "hard"


def test_configured_bogus_mode_degrades_to_dynamic(tmp_path):
    rmd = _write_case(tmp_path, "budgets:\n  stage_iter_mode: FOREVER\n")
    rm = research_md.load(rmd)
    assert rm.stage_iter_mode == "dynamic"


def test_iter_mode_independent_of_time_mode(tmp_path):
    # The two stage-bounds axes are orthogonal: pinning one hard does not pin
    # the other.
    rmd = _write_case(tmp_path, "budgets:\n  stage_time_limit_mode: hard\n")
    rm = research_md.load(rmd)
    assert rm.stage_time_limit_mode == "hard"
    assert rm.stage_iter_mode == "dynamic"


# ---------------------------------------------------------------------------
# state.bounds_exceeded — mode-aware iteration ceiling
# ---------------------------------------------------------------------------

def _state_with(iters: int, *, cap: int) -> dict:
    s = state.initial_state("t", max_iters_per_phase=cap)
    s["iteration_counts"][PhaseName.REVIEW] = iters
    return s


def test_dynamic_lets_progressing_run_exceed_old_cap():
    # A phase that reached iteration_counts=13 (past the OLD 12/16 AND 5 caps)
    # is NOT refused while below the new high ceiling — DYNAMIC lets a healthy
    # run continue to its real stop-condition.
    s = _state_with(13, cap=constants.DEFAULT_MAX_ITERS_PER_PHASE)
    exceeded, _ = state.bounds_exceeded(
        s, PhaseName.REVIEW, iter_mode=constants.STAGE_ITER_MODE_DYNAMIC)
    assert exceeded is False


def test_dynamic_still_trips_at_high_ceiling():
    # DYNAMIC keeps an ABSOLUTE backstop: at the raised ceiling it still refuses
    # (pathological non-convergence).
    s = _state_with(
        constants.DEFAULT_MAX_ITERS_PER_PHASE,
        cap=constants.DEFAULT_MAX_ITERS_PER_PHASE)
    exceeded, reason = state.bounds_exceeded(
        s, PhaseName.REVIEW, iter_mode=constants.STAGE_ITER_MODE_DYNAMIC)
    assert exceeded is True
    assert "ceiling" in reason


def test_hard_enforces_fixed_cap_byte_for_byte():
    # HARD + a pinned small cap refuses at cur>=cap exactly as legacy.
    s = _state_with(5, cap=5)
    exceeded, reason = state.bounds_exceeded(
        s, PhaseName.REVIEW, iter_mode=constants.STAGE_ITER_MODE_HARD)
    assert exceeded is True
    assert "deterministic cap" in reason
    # cur below cap → allowed.
    s2 = _state_with(4, cap=5)
    exceeded2, _ = state.bounds_exceeded(
        s2, PhaseName.REVIEW, iter_mode=constants.STAGE_ITER_MODE_HARD)
    assert exceeded2 is False


def test_bounds_exceeded_default_arg_is_backward_compatible():
    # Legacy call sites pass no iter_mode; still enforce the cap (the ceiling
    # is high by default, so a small pinned cap still refuses).
    s = _state_with(5, cap=5)
    exceeded, _ = state.bounds_exceeded(s, PhaseName.REVIEW)
    assert exceeded is True


# ---------------------------------------------------------------------------
# zero-progress guard — record_phase_progress + zero_progress_exceeded
# ---------------------------------------------------------------------------

def test_zero_progress_loop_is_caught():
    s = state.initial_state("t")
    limit = constants.ZERO_PROGRESS_STALL_LIMIT
    # Feed `limit + 1` re-opening iterations with an IDENTICAL fingerprint.
    for i in range(limit + 1):
        state.record_phase_progress(
            s, PhaseName.REVIEW, "same-fp", reopened=True)
        exceeded, reason = state.zero_progress_exceeded(
            s, PhaseName.REVIEW, limit)
        # First record establishes the fp (stalls=0); stalls accrue only on
        # subsequent identical re-opens.
        if i >= limit:
            assert exceeded is True
            assert "zero_progress_non_convergence" in reason
    assert state.zero_progress_exceeded(
        s, PhaseName.REVIEW, limit)[0] is True


def test_changing_fingerprint_never_stalls():
    s = state.initial_state("t")
    limit = constants.ZERO_PROGRESS_STALL_LIMIT
    for i in range(limit * 3):
        state.record_phase_progress(
            s, PhaseName.REVIEW, f"fp-{i}", reopened=True)
        assert state.zero_progress_exceeded(
            s, PhaseName.REVIEW, limit)[0] is False


def test_advancing_decision_resets_stalls():
    s = state.initial_state("t")
    # Two identical stalls...
    state.record_phase_progress(s, PhaseName.REVIEW, "fp", reopened=True)
    state.record_phase_progress(s, PhaseName.REVIEW, "fp", reopened=True)
    assert s["phase_progress"][PhaseName.REVIEW]["stalls"] >= 1
    # ...then an ADVANCING (PROCEED) verdict resets the counter.
    state.record_phase_progress(s, PhaseName.REVIEW, "fp", reopened=False)
    assert s["phase_progress"][PhaseName.REVIEW]["stalls"] == 0
    assert state.zero_progress_exceeded(
        s, PhaseName.REVIEW, constants.ZERO_PROGRESS_STALL_LIMIT)[0] is False


def test_first_advance_never_stalls():
    # A FIRST review that ADVANCES (PROCEED/DONE) must not increment stalls,
    # so uncapping never invites a spurious spin-halt on a clean finish.
    s = state.initial_state("t")
    state.record_phase_progress(
        s, PhaseName.REVIEW, "fp", reopened=False)  # PROCEED
    assert s["phase_progress"][PhaseName.REVIEW]["stalls"] == 0
    assert state.zero_progress_exceeded(
        s, PhaseName.REVIEW, constants.ZERO_PROGRESS_STALL_LIMIT)[0] is False


# ---------------------------------------------------------------------------
# STATE.md round-trip: phase_progress persists + old files tolerate its absence
# ---------------------------------------------------------------------------

def test_phase_progress_round_trips(tmp_path):
    s = state.initial_state("t")
    state.record_phase_progress(s, PhaseName.REVIEW, "fp", reopened=True)
    p = tmp_path / "STATE.md"
    state.write(p, s)
    reloaded = state.read(p)
    assert "phase_progress" in reloaded
    assert reloaded["phase_progress"][PhaseName.REVIEW]["fingerprint"] == "fp"


def test_old_state_without_phase_progress_loads(tmp_path):
    # A STATE.md written before phase_progress existed must parse cleanly.
    p = tmp_path / "STATE.md"
    p.write_text(
        "---\n"
        "topic_slug: t\n"
        "status: in_progress\n"
        "iteration_counts:\n  review: 2\n"
        "---\n\n# t\n"
    )
    reloaded = state.read(p)
    assert reloaded["phase_progress"] == {}
