"""Unit tests for omicairn.state (STATE.md schema + mutators + bounds)."""
from __future__ import annotations

import pathlib
import tempfile

from omicairn import state
from omicairn.constants import (
    DEFAULT_MAX_BACKTRACKS,
    DEFAULT_MAX_ITERS_PER_PHASE,
    MAX_CONTRACT_RETRIES,
    PIPELINE_ORDER,
    PhaseName,
    RunStatus,
)


def _tmp_state_path() -> pathlib.Path:
    tmp = pathlib.Path(tempfile.mkdtemp())
    return tmp / "STATE.md"


def test_initial_state_has_all_required_keys():
    s = state.initial_state("test-topic")
    assert s["topic_slug"] == "test-topic"
    assert s["status"] == RunStatus.IN_PROGRESS
    assert s["backtracks_used"] == 0
    assert s["current_stage"] is None
    assert s["pending_decision"] is None
    # next_stage_index is derived from the filesystem, not stored.
    assert "next_stage_index" not in s
    # Every phase in PIPELINE_ORDER must appear as a 0-counter.
    for p in PIPELINE_ORDER:
        assert s["iteration_counts"][p] == 0
        assert s["contract_retry_counts"][p] == 0
    # Bounds block is always present.
    assert s["bounds"]["max_iters_per_phase"] == DEFAULT_MAX_ITERS_PER_PHASE
    assert s["bounds"]["max_backtracks"] == DEFAULT_MAX_BACKTRACKS


# ---------------------------------------------------------------------------
# CONTRACT_FAIL re-accounting: a contract-fail must NOT burn the science cap
# (it consumes a separate, bounded contract-retry budget instead).
# ---------------------------------------------------------------------------

def test_reclassify_contract_fail_moves_increment_off_science_count():
    """A CONTRACT_FAIL's record_stage_start increment is moved OUT of
    iteration_counts and INTO contract_retry_counts — paperwork that didn't
    land is NOT a science iteration."""
    s = state.initial_state("t")
    # Simulate record_stage_start having bumped the science count before the
    # stage ran (the crash-safety increment).
    s["iteration_counts"][PhaseName.EXECUTE] = 1
    new_cr = state.reclassify_contract_fail(s, PhaseName.EXECUTE)
    assert new_cr == 1
    # The science increment was reclaimed; the contract budget absorbed it.
    assert s["iteration_counts"][PhaseName.EXECUTE] == 0
    assert s["contract_retry_counts"][PhaseName.EXECUTE] == 1


def test_reclassify_contract_fail_floors_science_count_at_zero():
    """Defensive: a reclassify when the science count is already 0 must NOT
    drive it negative (idempotency / double-call safety)."""
    s = state.initial_state("t")
    assert s["iteration_counts"][PhaseName.REVIEW] == 0
    state.reclassify_contract_fail(s, PhaseName.REVIEW)
    assert s["iteration_counts"][PhaseName.REVIEW] == 0
    assert s["contract_retry_counts"][PhaseName.REVIEW] == 1


def test_contract_fail_does_not_count_toward_science_bounds():
    """The whole point of the d7 hardening: N contract-fails leave the
    science cap UNtouched, so a COMPLETED experiment can still finish its
    paperwork instead of halting on max_iters_per_phase."""
    s = state.initial_state("t", max_iters_per_phase=2)
    for _ in range(5):
        # Mirror record_stage_start (bump) then a clean CONTRACT_FAIL.
        s["iteration_counts"][PhaseName.EXECUTE] = (
            s["iteration_counts"].get(PhaseName.EXECUTE, 0) + 1
        )
        state.reclassify_contract_fail(s, PhaseName.EXECUTE)
    # Science cap is 2 but 5 contract-fails did NOT exhaust it.
    exceeded, _ = state.bounds_exceeded(s, PhaseName.EXECUTE)
    assert exceeded is False
    assert s["iteration_counts"][PhaseName.EXECUTE] == 0


def test_contract_retries_exceeded_is_the_runaway_backstop():
    """contract_retries_exceeded must bound runaway contract-fails: it fires
    once the phase has CONTRACT_FAILed MAX_CONTRACT_RETRIES times (cap-
    inclusive) — the airtight halt that prevents infinite write-up retry."""
    s = state.initial_state("t")
    for i in range(1, MAX_CONTRACT_RETRIES + 1):
        state.reclassify_contract_fail(s, PhaseName.REVIEW)
        exceeded, reason = state.contract_retries_exceeded(s, PhaseName.REVIEW)
        if i < MAX_CONTRACT_RETRIES:
            assert exceeded is False, f"must not halt before the cap (i={i})"
        else:
            assert exceeded is True, "must halt AT the cap (runaway backstop)"
            assert "contract" in reason.lower()


def test_contract_retry_counts_survive_read_round_trip(tmp_path):
    """contract_retry_counts must persist through write/read so the budget is
    honoured across resume invocations, not reset to 0 on every `omicairn run`."""
    path = tmp_path / "STATE.md"
    s = state.initial_state("t")
    state.reclassify_contract_fail(s, PhaseName.EXECUTE)
    state.reclassify_contract_fail(s, PhaseName.EXECUTE)
    state.write(path, s)
    s2 = state.read(path)
    assert s2["contract_retry_counts"][PhaseName.EXECUTE] == 2


def test_legacy_state_file_without_contract_retry_counts_loads_with_default(tmp_path):
    """A STATE.md written before contract_retry_counts existed must still
    load — read() setdefaults the missing map to all-zero counters."""
    path = tmp_path / "STATE.md"
    path.write_text(
        "---\n"
        "topic_slug: legacy\n"
        "status: in_progress\n"
        "halted_reason: null\n"
        "current_stage: null\n"
        "backtracks_used: 0\n"
        "iteration_counts: {scope: 1}\n"
        "bounds: {max_iters_per_phase: 3, max_backtracks: 5}\n"
        "---\n\nlegacy state\n"
    )
    s = state.read(path)
    for p in PIPELINE_ORDER:
        assert s["contract_retry_counts"][p] == 0


def test_initial_state_honors_custom_bounds():
    s = state.initial_state("t", max_iters_per_phase=7, max_backtracks=11)
    assert s["bounds"]["max_iters_per_phase"] == 7
    assert s["bounds"]["max_backtracks"] == 11


def test_write_then_read_roundtrip():
    path = _tmp_state_path()
    s = state.initial_state("roundtrip-topic")
    state.write(path, s)
    assert path.exists()
    # The rendered file contains both the YAML front-matter and the
    # human-readable summary block.
    text = path.read_text()
    assert text.startswith("---")
    assert "roundtrip-topic" in text
    # Re-read and compare the front-matter fields.
    back = state.read(path)
    assert back["topic_slug"] == "roundtrip-topic"
    assert back["status"] == RunStatus.IN_PROGRESS
    for p in PIPELINE_ORDER:
        assert back["iteration_counts"][p] == 0


def test_read_fills_missing_defaults_for_legacy_state():
    """Old-format STATE.md files missing new fields should still load."""
    path = _tmp_state_path()
    path.write_text(
        "---\n"
        "topic_slug: legacy\n"
        "started_at: 2026-01-01T00:00:00+00:00\n"
        "next_stage_index: 7\n"  # legacy field, silently dropped on read
        "---\n"
    )
    s = state.read(path)
    assert s["topic_slug"] == "legacy"
    # All defaults must be populated.
    assert s["status"] == RunStatus.IN_PROGRESS
    assert s["backtracks_used"] == 0
    assert s["current_stage"] is None
    # Legacy next_stage_index is stripped on read.
    assert "next_stage_index" not in s
    for p in PIPELINE_ORDER:
        assert s["iteration_counts"][p] == 0
    assert s["bounds"]["max_iters_per_phase"] == DEFAULT_MAX_ITERS_PER_PHASE


def test_record_stage_start_increments_counter_and_sets_current_stage():
    s = state.initial_state("t")
    state.record_stage_start(s, 0, PhaseName.SCOPE)
    assert s["iteration_counts"][PhaseName.SCOPE] == 1
    assert s["current_stage"] == "000_scope"
    state.record_stage_start(s, 4, PhaseName.EXECUTE)
    assert s["iteration_counts"][PhaseName.EXECUTE] == 1
    assert s["current_stage"] == "004_execute"
    # Running scope a second time advances its counter only.
    state.record_stage_start(s, 5, PhaseName.SCOPE)
    assert s["iteration_counts"][PhaseName.SCOPE] == 2
    assert s["iteration_counts"][PhaseName.EXECUTE] == 1


def test_record_stage_done_is_a_noop_hook():
    """record_stage_done is kept as a named hook but has no state
    mutations today — next_stage_index is derived from the filesystem
    by the orchestrator via stage.next_stage_index(). We assert that
    calling it doesn't pollute the state dict."""
    s = state.initial_state("t")
    before = dict(s)
    state.record_stage_done(s, 0, PhaseName.SCOPE)
    state.record_stage_done(s, 3, PhaseName.EXECUTE)
    assert s == before


def test_record_decision_and_backtrack():
    s = state.initial_state("t")
    state.record_decision(s, "retry_execute")
    assert s["pending_decision"] == "retry_execute"
    state.record_backtrack(s)
    state.record_backtrack(s)
    assert s["backtracks_used"] == 2


def test_set_status_with_reason():
    s = state.initial_state("t")
    state.set_status(s, RunStatus.HALTED, reason="test halt")
    assert s["status"] == RunStatus.HALTED
    assert s["halted_reason"] == "test halt"


def test_bounds_exceeded_checks_iters_per_phase():
    s = state.initial_state("t", max_iters_per_phase=2)
    # No runs yet → not exceeded.
    ok, _ = state.bounds_exceeded(s, PhaseName.EXECUTE)
    assert ok is False
    # After two completed starts, running a third should exceed.
    state.record_stage_start(s, 0, PhaseName.EXECUTE)
    state.record_stage_start(s, 2, PhaseName.EXECUTE)
    exceeded, reason = state.bounds_exceeded(s, PhaseName.EXECUTE)
    assert exceeded is True
    assert "execute" in reason
    assert "cap is 2" in reason


def test_bounds_exceeded_checks_backtracks():
    s = state.initial_state("t", max_backtracks=2)
    # 0 backtracks used — not yet exceeded.
    ok, _ = state.bounds_exceeded(s, PhaseName.PLAN)
    assert ok is False
    # 1 backtrack — still below cap of 2.
    state.record_backtrack(s)
    ok, _ = state.bounds_exceeded(s, PhaseName.PLAN)
    assert ok is False
    # 2 backtracks used — at the cap. Bound is exclusive, so refused.
    state.record_backtrack(s)
    exceeded, reason = state.bounds_exceeded(s, PhaseName.PLAN)
    assert exceeded is True
    assert "max_backtracks" in reason
    assert "cap is 2" in reason


# ---------------------------------------------------------------------------
# state.read() error branches
#
# Read must raise a clear ValueError on three malformed-STATE.md shapes
# so a CI failure points the developer straight at the problem rather
# than blowing up deep inside yaml.safe_load or a .get() on a string.
# ---------------------------------------------------------------------------

def test_read_raises_when_no_yaml_front_matter():
    """Line 132: a STATE.md that doesn't start with `---` must raise
    a descriptive ValueError, not a generic yaml error."""
    import pytest
    path = _tmp_state_path()
    path.write_text("no front matter here\njust prose\n")
    with pytest.raises(ValueError, match="no YAML front-matter"):
        state.read(path)


def test_read_raises_when_front_matter_unterminated():
    """Line 135: a STATE.md that opens with `---` but never closes must
    raise "malformed" rather than returning a bogus partial state."""
    import pytest
    path = _tmp_state_path()
    path.write_text("---\ntopic_slug: unterminated\n")  # missing closing ---
    with pytest.raises(ValueError, match="malformed"):
        state.read(path)


def test_read_raises_when_front_matter_is_not_a_mapping():
    """Line 138: legal YAML but not a mapping (e.g. a list or a bare
    string) must raise "not a mapping". Otherwise downstream code
    calling .get() on the result crashes with a confusing AttributeError."""
    import pytest
    path = _tmp_state_path()
    # Legal YAML — a list, not a dict.
    path.write_text("---\n- a\n- b\n---\n")
    with pytest.raises(ValueError, match="not a mapping"):
        state.read(path)


def test_set_status_clears_halted_reason_on_non_halted_transition():
    """Line 208: transitioning OUT of HALTED must clear halted_reason
    so `omicairn status` doesn't show "done" and "halted because X" in
    the same breath. The initial HALTED → IN_PROGRESS transition is
    legal when a developer resumes a halted run."""
    s = state.initial_state("t")
    state.set_status(s, RunStatus.HALTED, reason="bounds exceeded")
    assert s["halted_reason"] == "bounds exceeded"
    # Transition to DONE — halted_reason must be cleared.
    state.set_status(s, RunStatus.DONE)
    assert s["status"] == RunStatus.DONE
    assert s["halted_reason"] is None


def test_set_paused_no_op_on_halted():
    """set_paused must NOT write paused_reason on a HALTED run. A
    terminal run can't be paused; surfacing a pause marker on top of a
    halt would mislead the operator about whether to resume."""
    s = state.initial_state("t")
    state.set_status(s, RunStatus.HALTED, reason="bounds exceeded")
    state.set_paused(s, "claude_session_limit: resets 6am")
    assert s.get("paused_reason") is None


def test_set_paused_no_op_on_done():
    """Same guard for DONE — a finished run is not paused."""
    s = state.initial_state("t")
    state.set_status(s, RunStatus.DONE)
    state.set_paused(s, "claude_session_limit: resets 6am")
    assert s.get("paused_reason") is None


def test_set_paused_writes_when_in_progress():
    s = state.initial_state("t")
    state.set_paused(s, "claude_session_limit: resets 6am")
    assert s["paused_reason"] == "claude_session_limit: resets 6am"


def test_clear_paused_drops_marker():
    s = state.initial_state("t")
    state.set_paused(s, "claude_session_limit: resets 6am")
    state.clear_paused(s)
    assert s.get("paused_reason") is None


# ---------------------------------------------------------------------------
# reset_for_steering — un-terminate a done/halted run so a pending steering
# plan can dispatch (FIX 3, d7 steering bug, 2026-06-10).
# ---------------------------------------------------------------------------

def test_reset_for_steering_flips_halted_to_in_progress():
    s = state.initial_state("t")
    state.set_status(s, RunStatus.HALTED, reason="manual halt")
    s["pending_decision"] = "retry_execute"
    s["paused_reason"] = "stale"
    changed = state.reset_for_steering(s)
    assert changed is True
    assert s["status"] == RunStatus.IN_PROGRESS
    assert s["halted_reason"] is None
    assert s["paused_reason"] is None
    assert s["pending_decision"] is None


def test_reset_for_steering_flips_done_to_in_progress():
    s = state.initial_state("t")
    state.set_status(s, RunStatus.DONE)
    changed = state.reset_for_steering(s)
    assert changed is True
    assert s["status"] == RunStatus.IN_PROGRESS


def test_reset_for_steering_no_op_when_in_progress():
    """An already-in_progress run is untouched (returns False) — the common
    path must not be perturbed."""
    s = state.initial_state("t")
    assert s["status"] == RunStatus.IN_PROGRESS
    changed = state.reset_for_steering(s)
    assert changed is False
    assert s["status"] == RunStatus.IN_PROGRESS


def test_paused_reason_survives_read_round_trip(tmp_path):
    """A STATE.md round-tripped through write/read must preserve the
    paused_reason field — the YAML serializer needs to handle it and
    read() must pass it through (not just default it back to None)."""
    path = tmp_path / "STATE.md"
    s = state.initial_state("t")
    state.set_paused(s, "claude_session_limit: resets 6am")
    state.write(path, s)
    s2 = state.read(path)
    assert s2["paused_reason"] == "claude_session_limit: resets 6am"


def test_legacy_state_file_without_paused_reason_loads_with_default(tmp_path):
    """STATE.md files written before the paused_reason field existed
    must still load — read() should setdefault the missing key to None."""
    path = tmp_path / "STATE.md"
    # Hand-craft a legacy front-matter (no paused_reason key).
    path.write_text(
        "---\n"
        "topic_slug: legacy\n"
        "status: in_progress\n"
        "halted_reason: null\n"
        "current_stage: null\n"
        "backtracks_used: 0\n"
        "iteration_counts: {scope: 0}\n"
        "bounds: {max_iters_per_phase: 3, max_backtracks: 5}\n"
        "---\n\nlegacy state\n"
    )
    s = state.read(path)
    assert s["paused_reason"] is None
