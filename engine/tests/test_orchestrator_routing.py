"""Direct branch-by-branch tests for `orchestrator.decide_next_phase`.

The dry-run scenario tests in test_orchestrator_dryrun.py exercise the
routing table transitively by running the full pipeline. That catches
most regressions, but it's slow (~0.5s per scenario) and couples the
"verdict → next phase" question to questions about iter caps, backtrack
counters, stage creation, and contract verification. A single typo in
DECISION_TO_PHASE can survive that kind of test if the iter-cap fires
before the wrong routing does any observable damage.

This file is the focused dual: it directly builds stage directories on
disk (no orchestrator.run_all), writes verdict.json files with specific
decisions, and asserts decide_next_phase returns the right next phase
for every branch of the routing logic:

- empty / all-incomplete workspace → SCOPE
- non-verdict last phase → linear next in PIPELINE_ORDER
- verdict phase with PROCEED / DONE / HALT
- verdict phase with every RETRY_* / BACK_TO_* decision (parametrized)
- verdict phase with missing / malformed / unknown-decision verdict.json
  (all three must fall through to linear next)
- status == DONE / HALTED → None regardless of stages
- review stage with PROCEED at the end of the pipeline → None

No tempdir workspaces here — we only need a root directory plus a
STATE.md file and a handful of stage dirs. Each test runs in
milliseconds.
"""
from __future__ import annotations

import json
import pathlib
import tempfile
import types
from typing import Iterator

import pytest

from omicairn import (
    orchestrator,
    state,
    stage as stage_mod,
    steering as steering_mod,
    verdict as verdict_mod,
)
from omicairn.constants import (
    DECISION_TO_PHASE,
    DONE_FLAG,
    Decision,
    EventKind,
    PhaseName,
    PIPELINE_ORDER,
    RunStatus,
    VERDICT_FILE,
    VERDICT_PHASES,
)


# ---------------------------------------------------------------------------
# Minimal Workspace stub
# ---------------------------------------------------------------------------
#
# decide_next_phase only reads ws.root and ws.state_path. We don't need a
# real Workspace (which would also parse RESEARCH.md, init literature,
# etc.) — a small stub is enough, and it keeps the tests hermetic.

class _StubWorkspace:
    def __init__(self, root: pathlib.Path) -> None:
        self.root = root
        self.state_path = root / "STATE.md"


@pytest.fixture
def ws() -> Iterator[_StubWorkspace]:
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        # Seed STATE.md with an in-progress state so decide_next_phase
        # doesn't early-return on status == DONE / HALTED.
        s = state.initial_state("pytest-routing")
        state.write(root / "STATE.md", s)
        yield _StubWorkspace(root)


def _mk_stage(
    ws: _StubWorkspace,
    index: int,
    phase: str,
    *,
    done: bool = True,
    decision: str | None = None,
    verdict_body: str | None = None,
) -> pathlib.Path:
    """Create a stage directory with optional done.flag and verdict.json.

    If `decision` is set, writes a well-formed verdict.json with that
    decision. If `verdict_body` is set, writes that raw text as
    verdict.json (used to exercise the malformed / unknown-decision
    branches).
    """
    path = stage_mod.create_stage_dir(ws.root, index, phase)
    if done:
        (path / DONE_FLAG).write_text("1\n")
    if decision is not None:
        v = verdict_mod.Verdict(
            decision=decision, reason="pytest", evidence=[]
        )
        verdict_mod.write_verdict(path, v)
    if verdict_body is not None:
        (path / VERDICT_FILE).write_text(verdict_body)
    return path


def _read_events(ws: _StubWorkspace) -> list[dict]:
    """Read events.jsonl records written to the workspace root."""
    path = ws.root / "events.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


# ---------------------------------------------------------------------------
# Early-return branches
# ---------------------------------------------------------------------------

def test_empty_workspace_returns_scope(ws):
    """No stages yet — must start at the first phase in PIPELINE_ORDER."""
    assert orchestrator.decide_next_phase(ws) == PhaseName.SCOPE


def test_only_incomplete_stage_returns_scope(ws):
    """A stage dir without done.flag does NOT count as last_done —
    decide_next_phase falls through to the 'no done stages' branch
    and returns SCOPE, so the orchestrator will create a new scope
    stage rather than resuming the incomplete one."""
    _mk_stage(ws, 0, PhaseName.SCOPE, done=False)
    assert orchestrator.decide_next_phase(ws) == PhaseName.SCOPE


def test_status_done_returns_none(ws):
    """status == DONE short-circuits before we even look at stages."""
    s = state.read(ws.state_path)
    state.set_status(s, RunStatus.DONE)
    state.write(ws.state_path, s)
    assert orchestrator.decide_next_phase(ws) is None


def test_status_halted_returns_none(ws):
    """status == HALTED also short-circuits — even if a subsequent
    stage dir appears on disk, the orchestrator refuses to resume."""
    _mk_stage(ws, 0, PhaseName.SCOPE, done=True)
    s = state.read(ws.state_path)
    state.set_status(s, RunStatus.HALTED, reason="manual halt")
    state.write(ws.state_path, s)
    assert orchestrator.decide_next_phase(ws) is None


# ---------------------------------------------------------------------------
# FIX 3 (d7 steering bug, 2026-06-10): `omicairn steer` on a DONE/HALTED
# workspace must NOT silently no-op. A pending, VALID steering plan must
# yield a real next phase even when the run is terminal; a terminal run
# with NO pending plan must still return None (don't loop on a finished
# run).
# ---------------------------------------------------------------------------

def _write_steering_plan(ws, phase=PhaseName.EXECUTE):
    plan = steering_mod.SteeringPlan(
        user_request="regenerate fig 3 at 600 dpi on the done run",
        steps=[steering_mod.SteeringStep(
            phase=phase,
            prompt_overlay="USER STEERING: re-render fig3 at 600 dpi",
            rationale="render only fig3",
        )],
    )
    steering_mod.write_plan(ws.root, plan)
    return plan


def test_done_workspace_with_pending_steering_plan_yields_phase(ws):
    """A `done` workspace WITH a valid pending steering plan must route to
    the plan's step phase, NOT short-circuit to None — otherwise `omicairn run`
    prints "(no stages ran)" and the steer request is silently dropped."""
    for idx, phase in enumerate(PIPELINE_ORDER):
        _mk_stage(ws, idx, phase, done=True,
                  decision=Decision.DONE if phase == PhaseName.REVIEW else None)
    s = state.read(ws.state_path)
    state.set_status(s, RunStatus.DONE)
    state.write(ws.state_path, s)
    # Sanity: without a plan this is None (the genuinely-done case).
    assert orchestrator.decide_next_phase(ws) is None
    # With a pending valid plan it must yield the plan's phase.
    _write_steering_plan(ws, phase=PhaseName.EXECUTE)
    assert orchestrator.decide_next_phase(ws) == PhaseName.EXECUTE


def test_halted_workspace_with_pending_steering_plan_yields_phase(ws):
    """Same carve-out for a HALTED run: a pending valid plan un-blocks it."""
    _mk_stage(ws, 0, PhaseName.SCOPE, done=True)
    s = state.read(ws.state_path)
    state.set_status(s, RunStatus.HALTED, reason="manual halt")
    state.write(ws.state_path, s)
    _write_steering_plan(ws, phase=PhaseName.REVIEW)
    assert orchestrator.decide_next_phase(ws) == PhaseName.REVIEW


def test_done_workspace_without_plan_still_returns_none(ws):
    """The preserved-behavior half: a genuinely-done workspace with NO
    steering plan must still short-circuit to None so `omicairn run` never
    loops forever on a finished run."""
    for idx, phase in enumerate(PIPELINE_ORDER):
        _mk_stage(ws, idx, phase, done=True,
                  decision=Decision.DONE if phase == PhaseName.REVIEW else None)
    s = state.read(ws.state_path)
    state.set_status(s, RunStatus.DONE)
    state.write(ws.state_path, s)
    assert orchestrator.decide_next_phase(ws) is None


def test_done_workspace_with_invalid_steering_plan_returns_none(ws):
    """A malformed/invalid hand-edited plan must NOT un-terminate the run
    (validity is required by has_pending_plan) — a steps-less plan is
    invalid, so a done workspace stays None."""
    _mk_stage(ws, 0, PhaseName.SCOPE, done=True)
    s = state.read(ws.state_path)
    state.set_status(s, RunStatus.DONE)
    state.write(ws.state_path, s)
    # Empty-steps plan fails validate_plan → not "pending+valid".
    steering_mod.write_plan(
        ws.root, steering_mod.SteeringPlan(user_request="x", steps=[]))
    assert orchestrator.decide_next_phase(ws) is None


def test_done_workspace_with_completed_plan_returns_none(ws):
    """An already-consumed (all-steps-done) plan must NOT reactivate a done
    run — only a plan with a not-done step counts as pending."""
    _mk_stage(ws, 0, PhaseName.SCOPE, done=True)
    s = state.read(ws.state_path)
    state.set_status(s, RunStatus.DONE)
    state.write(ws.state_path, s)
    plan = steering_mod.SteeringPlan(
        user_request="x",
        steps=[steering_mod.SteeringStep(
            phase=PhaseName.REVIEW, prompt_overlay="y", done=True)],
    )
    steering_mod.write_plan(ws.root, plan)
    assert orchestrator.decide_next_phase(ws) is None


def test_done_workspace_with_legacy_manuscript_steps_stays_done(ws):
    """A legacy plan queuing removed manuscript phases (write/typeset) must
    not reactivate a done run; the steps are skipped and the plan consumed."""
    _mk_stage(ws, 0, PhaseName.SCOPE, done=True)
    s = state.read(ws.state_path)
    state.set_status(s, RunStatus.DONE)
    state.write(ws.state_path, s)
    steering_mod.write_plan(ws.root, steering_mod.SteeringPlan(
        user_request="x",
        steps=[steering_mod.SteeringStep(phase="write", prompt_overlay="y"),
               steering_mod.SteeringStep(phase="typeset", prompt_overlay="y")],
    ))
    assert orchestrator.decide_next_phase(ws) is None
    assert not steering_mod.has_pending_plan(ws.root)
    assert orchestrator.run_next_stage(ws) is None
    assert state.read(ws.state_path)["status"] == RunStatus.DONE


def test_legacy_manuscript_step_skipped_before_valid_step(ws):
    """Mixed plan: the removed-phase step is skipped, the next valid step
    is dispatched."""
    _mk_stage(ws, 0, PhaseName.SCOPE, done=True)
    steering_mod.write_plan(ws.root, steering_mod.SteeringPlan(
        user_request="x",
        steps=[steering_mod.SteeringStep(phase="write", prompt_overlay="y"),
               steering_mod.SteeringStep(
                   phase=PhaseName.EXECUTE, prompt_overlay="y")],
    ))
    assert orchestrator.decide_next_phase(ws) == PhaseName.EXECUTE
    plan = steering_mod.read_plan(ws.root)
    assert plan.steps[0].done and not plan.steps[1].done


# ---------------------------------------------------------------------------
# Linear progression from non-verdict phases
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "last_phase,expected_next",
    [
        (PhaseName.SCOPE, PhaseName.PLAN),
        (PhaseName.PLAN, PhaseName.EXECUTE),
        (PhaseName.EXECUTE, PhaseName.CROSS_CHECK),
    ],
)
def test_linear_progression_from_non_verdict_phase(
    ws, last_phase, expected_next
):
    """A done stage of a non-verdict phase must linearly advance to
    the next phase in PIPELINE_ORDER. cross_check and review are
    excluded because they ARE verdict phases and have their own
    branch — see the verdict tests below."""
    _mk_stage(ws, 0, last_phase, done=True)
    assert orchestrator.decide_next_phase(ws) == expected_next


# ---------------------------------------------------------------------------
# Verdict-phase routing — every decision in DECISION_TO_PHASE
# ---------------------------------------------------------------------------

# Build the parameter list from DECISION_TO_PHASE so if the table grows
# a new decision, this test automatically picks it up (or fails loudly
# if the expected target changes).
_DECISION_ROUTING_CASES = sorted(DECISION_TO_PHASE.items())


@pytest.mark.parametrize("decision,expected_phase", _DECISION_ROUTING_CASES)
def test_cross_check_verdict_routes_according_to_decision_table(
    ws, decision, expected_phase
):
    """cross_check emits a verdict; decide_next_phase must route to
    DECISION_TO_PHASE[decision]. This is the single test that pins the
    entire verdict → next-phase routing table."""
    _mk_stage(ws, 0, PhaseName.SCOPE, done=True)
    _mk_stage(ws, 1, PhaseName.PLAN, done=True)
    _mk_stage(ws, 2, PhaseName.EXECUTE, done=True)
    _mk_stage(ws, 3, PhaseName.CROSS_CHECK, done=True, decision=decision)
    assert orchestrator.decide_next_phase(ws) == expected_phase


@pytest.mark.parametrize("decision,expected_phase", _DECISION_ROUTING_CASES)
def test_review_verdict_routes_according_to_decision_table(
    ws, decision, expected_phase
):
    """review emits the same verdict schema; any decision in the table
    must route to the same phase from review as from cross_check.
    This is how retry_execute from the analysis review loops back to
    execute, and back_to_plan / back_to_scope rewind the pipeline."""
    for idx, phase in enumerate(PIPELINE_ORDER):
        _mk_stage(
            ws, idx, phase,
            done=True,
            decision=decision if phase == PhaseName.REVIEW else None,
        )
    assert orchestrator.decide_next_phase(ws) == expected_phase


# ---------------------------------------------------------------------------
# Verdict-phase terminal decisions (DONE / HALT) and PROCEED
# ---------------------------------------------------------------------------

def test_cross_check_proceed_advances_linearly(ws):
    """PROCEED is the common case: no backtrack, just go to the next
    phase in PIPELINE_ORDER (review after cross_check)."""
    _mk_stage(ws, 0, PhaseName.SCOPE, done=True)
    _mk_stage(ws, 1, PhaseName.PLAN, done=True)
    _mk_stage(ws, 2, PhaseName.EXECUTE, done=True)
    _mk_stage(
        ws, 3, PhaseName.CROSS_CHECK, done=True, decision=Decision.PROCEED
    )
    assert orchestrator.decide_next_phase(ws) == PhaseName.REVIEW


def test_cross_check_done_returns_none(ws):
    """DONE from cross_check short-circuits the whole pipeline —
    treated the same as DONE from review, even though it's unusual for
    cross_check to emit DONE in practice."""
    _mk_stage(ws, 0, PhaseName.SCOPE, done=True)
    _mk_stage(ws, 1, PhaseName.PLAN, done=True)
    _mk_stage(ws, 2, PhaseName.EXECUTE, done=True)
    _mk_stage(
        ws, 3, PhaseName.CROSS_CHECK, done=True, decision=Decision.DONE
    )
    assert orchestrator.decide_next_phase(ws) is None


def test_cross_check_halt_returns_none(ws):
    """HALT from a verdict phase must NOT be retried — decide_next_phase
    returns None so the orchestrator stops creating new stages. The
    calling code is responsible for updating state.status to HALTED."""
    _mk_stage(ws, 0, PhaseName.SCOPE, done=True)
    _mk_stage(ws, 1, PhaseName.PLAN, done=True)
    _mk_stage(ws, 2, PhaseName.EXECUTE, done=True)
    _mk_stage(
        ws, 3, PhaseName.CROSS_CHECK, done=True, decision=Decision.HALT
    )
    assert orchestrator.decide_next_phase(ws) is None


def test_review_done_returns_none(ws):
    """The happy-path end-of-pipeline branch: review emits DONE and the
    orchestrator stops. This is the terminal state after a successful
    full run."""
    for idx, phase in enumerate(PIPELINE_ORDER):
        _mk_stage(
            ws, idx, phase, done=True,
            decision=Decision.DONE if phase == PhaseName.REVIEW else None,
        )
    assert orchestrator.decide_next_phase(ws) is None


def test_review_proceed_returns_none_at_end_of_pipeline(ws):
    """PROCEED from the last phase in PIPELINE_ORDER has nowhere to
    advance to — decide_next_phase must fall through the verdict
    branch AND the linear-next branch and return None. This is the
    'every phase done, no redirect' terminal state."""
    for idx, phase in enumerate(PIPELINE_ORDER):
        _mk_stage(
            ws, idx, phase, done=True,
            decision=Decision.PROCEED if phase == PhaseName.REVIEW else None,
        )
    assert orchestrator.decide_next_phase(ws) is None


@pytest.mark.parametrize(
    "decision,expected",
    [
        (Decision.DONE, None),
        (Decision.PROCEED, None),
        (Decision.HALT, None),
        (Decision.RETRY_EXECUTE, PhaseName.EXECUTE),
        (Decision.BACK_TO_PLAN, PhaseName.PLAN),
        (Decision.BACK_TO_SCOPE, PhaseName.SCOPE),
    ],
)
def test_analysis_review_decisions_route(ws, decision, expected):
    """The analysis-adequacy REVIEW is the last pipeline phase: done /
    proceed end the run, halt stops, and the three analysis routes send
    the run back for more evidence."""
    for idx, phase in enumerate(PIPELINE_ORDER):
        _mk_stage(
            ws, idx, phase, done=True,
            decision=decision if phase == PhaseName.REVIEW else None,
        )
    assert orchestrator.decide_next_phase(ws) == expected


@pytest.mark.parametrize("legacy", ["retry_write", "retry_review", "back_to_write"])
def test_review_verdict_with_removed_decision_does_not_crash(ws, legacy):
    """A legacy verdict.json carrying a removed paper-era decision is read
    as no verdict; the analysis review never validly ran, so routing re-runs
    REVIEW instead of walking off the end of the pipeline as DONE."""
    for idx, phase in enumerate(PIPELINE_ORDER):
        _mk_stage(
            ws, idx, phase, done=True,
            verdict_body=(json.dumps({
                "decision": legacy, "reason": "r", "evidence": [],
            }) if phase == PhaseName.REVIEW else None),
        )
    assert orchestrator.decide_next_phase(ws) == PhaseName.REVIEW


def test_legacy_write_stage_dir_is_ignored(ws):
    """An old NNN_write / NNN_claim_audit dir from a paper-era workspace is
    not a recognised stage: routing keys off the last real stage."""
    _mk_stage(ws, 0, PhaseName.SCOPE, done=True)
    _mk_stage(ws, 1, PhaseName.PLAN, done=True)
    _mk_stage(ws, 2, PhaseName.EXECUTE, done=True)
    _mk_stage(ws, 3, PhaseName.CROSS_CHECK, done=True,
              decision=Decision.PROCEED)
    for idx, legacy in ((4, "write"), (5, "claim_audit")):
        d = ws.root / f"00{idx}_{legacy}"
        d.mkdir()
        (d / DONE_FLAG).write_text("1\n")
        (d / VERDICT_FILE).write_text(json.dumps({
            "decision": "retry_write", "reason": "r", "evidence": []}))
    assert orchestrator.decide_next_phase(ws) == PhaseName.REVIEW
    assert [r.phase for r in stage_mod.list_stages(ws.root)] == list(
        PIPELINE_ORDER[:4])


# ---------------------------------------------------------------------------
# Missing / malformed verdicts on verdict phases — all fall through
# ---------------------------------------------------------------------------

def test_verdict_phase_without_verdict_json_falls_through_to_linear(ws):
    """A cross_check stage with done.flag but no verdict.json should
    fall through to the linear-next branch. This is a safety net for
    the case where a verdict-emitting stage completed but its verdict
    is missing — the orchestrator shouldn't silently hang."""
    _mk_stage(ws, 0, PhaseName.SCOPE, done=True)
    _mk_stage(ws, 1, PhaseName.PLAN, done=True)
    _mk_stage(ws, 2, PhaseName.EXECUTE, done=True)
    _mk_stage(ws, 3, PhaseName.CROSS_CHECK, done=True, decision=None)
    # No verdict.json written. Linear next after cross_check is review.
    assert orchestrator.decide_next_phase(ws) == PhaseName.REVIEW


def test_verdict_phase_with_malformed_verdict_falls_through(ws):
    """Malformed verdict.json — verdict_mod.read_verdict returns None
    and decide_next_phase must fall through to linear next, not raise."""
    _mk_stage(ws, 0, PhaseName.SCOPE, done=True)
    _mk_stage(ws, 1, PhaseName.PLAN, done=True)
    _mk_stage(ws, 2, PhaseName.EXECUTE, done=True)
    _mk_stage(
        ws, 3, PhaseName.CROSS_CHECK, done=True,
        verdict_body="this is not json",
    )
    assert orchestrator.decide_next_phase(ws) == PhaseName.REVIEW


def test_verdict_phase_with_unknown_decision_falls_through(ws):
    """A verdict with an unrecognized decision string (from e.g. a
    future version or a typo) must NOT be treated as any known
    decision — fall through to linear next."""
    _mk_stage(ws, 0, PhaseName.SCOPE, done=True)
    _mk_stage(ws, 1, PhaseName.PLAN, done=True)
    _mk_stage(ws, 2, PhaseName.EXECUTE, done=True)
    _mk_stage(
        ws, 3, PhaseName.CROSS_CHECK, done=True,
        verdict_body=json.dumps({
            "decision": "teleport_to_mars",
            "reason": "r",
            "evidence": [],
        }),
    )
    assert orchestrator.decide_next_phase(ws) == PhaseName.REVIEW


# ---------------------------------------------------------------------------
# Incomplete re-attempt after a done stage
# ---------------------------------------------------------------------------

def test_incomplete_later_stage_does_not_override_earlier_done(ws):
    """If a stage was started but never completed (no done.flag), it's
    NOT considered the 'last done' — decide_next_phase should use the
    most recent ACTUALLY DONE stage. This guards against the case
    where a crash mid-stage leaves a half-written dir and a rerun
    would otherwise route off the crashed stage."""
    _mk_stage(ws, 0, PhaseName.SCOPE, done=True)
    _mk_stage(ws, 1, PhaseName.PLAN, done=True)
    # 002_execute exists but is not done — simulating a mid-flight crash.
    _mk_stage(ws, 2, PhaseName.EXECUTE, done=False)
    # Last DONE stage is plan → linear next is execute.
    assert orchestrator.decide_next_phase(ws) == PhaseName.EXECUTE


# ---------------------------------------------------------------------------
# Sanity: every verdict phase is actually in VERDICT_PHASES
# ---------------------------------------------------------------------------

def test_verdict_phases_constant_matches_routing_behavior():
    """decide_next_phase only reads verdict.json for phases in
    VERDICT_PHASES. If someone renames a phase or edits the frozenset,
    the parametrized routing tests above will still pass as long as
    the right phase is in VERDICT_PHASES — this test pins the constant
    against drift separately."""
    assert PhaseName.CROSS_CHECK in VERDICT_PHASES
    assert PhaseName.REVIEW in VERDICT_PHASES
    # LLM-only phases are NOT verdict phases.
    assert PhaseName.SCOPE not in VERDICT_PHASES
    assert PhaseName.PLAN not in VERDICT_PHASES
    assert PhaseName.EXECUTE not in VERDICT_PHASES


# ---------------------------------------------------------------------------
# _build_retry_note — Phase A retry-feedback plumbing
# ---------------------------------------------------------------------------

def test_build_retry_note_fresh_attempt_when_no_stages(ws):
    """Empty workspace → '(fresh attempt — no prior stages)'."""
    note = orchestrator._build_retry_note(ws, "scope", iter_count=0)
    assert "fresh attempt" in note


def test_build_retry_note_fresh_when_no_verdict_stage(ws):
    """Workspace with only non-verdict stages → '(fresh attempt)'."""
    _mk_stage(ws, 0, PhaseName.SCOPE, done=True)
    _mk_stage(ws, 1, PhaseName.PLAN, done=True)
    note = orchestrator._build_retry_note(ws, "execute", iter_count=0)
    assert "fresh attempt" in note


def test_build_retry_note_salvages_contract_failed_same_phase(ws):
    """Regression (audit 2026-06-10): when EXECUTE retries after its own
    CONTRACT_FAIL, the retry note must surface the prior attempt's
    substantive artifacts (results.json/analysis.md) so the agent finishes
    them instead of re-running hours of GPU compute. d1's 002_execute
    produced full results.json + analysis.md but the turn ended before
    done.flag → CONTRACT_FAIL → 003 must not re-extract from scratch."""
    _mk_stage(ws, 0, PhaseName.SCOPE, done=True)
    _mk_stage(ws, 1, PhaseName.PLAN, done=True)
    # A NOT-done execute stage (contract-failed) carrying real outputs.
    failed = _mk_stage(ws, 2, PhaseName.EXECUTE, done=False)
    (failed / "results.json").write_text('{"primary_model": {"auc": 0.8}}')
    (failed / "analysis.md").write_text("real analysis content\n" * 5)
    note = orchestrator._build_retry_note(ws, "execute", iter_count=1)
    assert "CONTRACT-FAILED but already produced" in note
    assert "results.json" in note and "analysis.md" in note
    assert "rather than re-running" in note


def test_build_retry_note_salvage_skips_empty_current_stage(ws):
    """Regression (2026-06-10 bug): _build_retry_note runs AFTER the
    current retry stage dir is created, so the latest not-done same-phase
    stage is the EMPTY current one. The salvage scan must skip it and find
    the EARLIER failed attempt that holds results.json — else the clause
    never fires (observed: d1's empty 004_execute shadowed its 002)."""
    _mk_stage(ws, 0, PhaseName.SCOPE, done=True)
    _mk_stage(ws, 1, PhaseName.PLAN, done=True)
    failed = _mk_stage(ws, 2, PhaseName.EXECUTE, done=False)  # has results
    (failed / "results.json").write_text('{"primary_model": {"auc": 0.8}}')
    (failed / "analysis.md").write_text("real analysis\n" * 5)
    _mk_stage(ws, 3, PhaseName.EXECUTE, done=False)  # empty CURRENT retry
    note = orchestrator._build_retry_note(ws, "execute", iter_count=2)
    assert "CONTRACT-FAILED but already produced" in note
    assert "002_execute" in note, "must point at the failed prior, not 003"


def test_build_retry_note_no_salvage_when_prior_attempt_empty(ws):
    """A contract-failed prior attempt with NO substantive files → no
    salvage clause (nothing worth completing; fresh re-run is correct)."""
    _mk_stage(ws, 0, PhaseName.SCOPE, done=True)
    _mk_stage(ws, 1, PhaseName.PLAN, done=True)
    _mk_stage(ws, 2, PhaseName.EXECUTE, done=False)  # empty, no outputs
    note = orchestrator._build_retry_note(ws, "execute", iter_count=1)
    assert "CONTRACT-FAILED but already produced" not in note


# ---------------------------------------------------------------------------
# _build_retry_note — HARVEST-before-RELAUNCH on an in-flight remote job
# (FIX 2, audit 2026-06-10): a recovered/retried EXECUTE turn must be told a
# durable job_marker.json exists from a prior attempt, so it harvests the
# still-running GPU job instead of relaunching it on the shared host.
# ---------------------------------------------------------------------------

def test_build_retry_note_harvest_before_relaunch_when_job_marker_present(ws):
    """A prior EXECUTE attempt that launched a detached GPU job leaves a
    job_marker.json. The retry note MUST instruct the agent to HARVEST
    before relaunching (so it never collides with the still-running job),
    and surface the marker's host + remote results path."""
    _mk_stage(ws, 0, PhaseName.SCOPE, done=True)
    _mk_stage(ws, 1, PhaseName.PLAN, done=True)
    failed = _mk_stage(ws, 2, PhaseName.EXECUTE, done=False)
    (failed / orchestrator.JOB_MARKER_FILENAME).write_text(json.dumps({
        "host": "gpu-host-1",
        "remote_cmd": "python main.py",
        "remote_results_path": "gpu-host-1:/scratch/run/results.json",
        "launched_at": "2026-06-10T01:00:00Z",
        "pid_or_session": "tmux:exec-d4",
        "status": "launched",
    }))
    note = orchestrator._build_retry_note(ws, "execute", iter_count=1)
    assert "job_marker.json" in note
    assert "HARVEST" in note
    assert "Do NOT start a NEW GPU job" in note
    # Marker detail is surfaced so the agent knows where to look.
    assert "gpu-host-1" in note
    assert "tmux:exec-d4" in note


def test_build_retry_note_no_harvest_note_without_job_marker(ws):
    """No job_marker.json anywhere → no harvest instruction (a fresh GPU
    launch is correct; we must not scare the agent off launching)."""
    _mk_stage(ws, 0, PhaseName.SCOPE, done=True)
    _mk_stage(ws, 1, PhaseName.PLAN, done=True)
    _mk_stage(ws, 2, PhaseName.EXECUTE, done=False)
    note = orchestrator._build_retry_note(ws, "execute", iter_count=1)
    assert "HARVEST" not in note
    assert "job_marker.json" not in note


def test_build_retry_note_harvest_note_survives_malformed_marker(ws):
    """A malformed job_marker.json must still trigger the harvest
    instruction (the marker's mere presence is the signal; detail is
    best-effort)."""
    _mk_stage(ws, 0, PhaseName.SCOPE, done=True)
    failed = _mk_stage(ws, 1, PhaseName.EXECUTE, done=False)
    (failed / orchestrator.JOB_MARKER_FILENAME).write_text("not json{")
    note = orchestrator._build_retry_note(ws, "execute", iter_count=1)
    assert "HARVEST" in note
    assert "job_marker.json" in note


def test_build_retry_note_renders_proposed_fix(ws):
    """A done review stage with a verdict and proposed_fix must appear
    verbatim in the retry note."""
    _mk_stage(ws, 0, PhaseName.SCOPE, done=True)
    _mk_stage(ws, 1, PhaseName.PLAN, done=True)
    _mk_stage(ws, 2, PhaseName.EXECUTE, done=True)
    _mk_stage(
        ws, 3, PhaseName.CROSS_CHECK, done=True,
        decision=Decision.PROCEED,
    )
    review_path = _mk_stage(
        ws, 4, PhaseName.REVIEW, done=True, decision=Decision.RETRY_EXECUTE,
    )
    rich = {
        "decision": "retry_execute",
        "reason": "primary null not diagnosed",
        "evidence": ["002_execute/results.json"],
        "proposed_fix": "Run the cohort-stratified sensitivity analysis.",
        "target_file_hints": [
            "002_execute/analysis.md lacks a power check",
            "add GDC release v32 cohort",
        ],
    }
    (review_path / "verdict.json").write_text(json.dumps(rich))

    note = orchestrator._build_retry_note(ws, "execute", iter_count=1)
    assert "retry_execute" in note
    assert "cohort-stratified sensitivity" in note
    assert "GDC release v32" in note
    assert "MUST read" in note
    assert "004_review" in note


def test_build_retry_note_proceed_uses_forward_motion_framing(ws):
    """When the prior verdict-emitting stage emitted `proceed`, the note
    cites it but uses forward-motion framing, not the retry mandate, and
    omits proposed_fix / target_file_hints."""
    _mk_stage(ws, 0, PhaseName.SCOPE, done=True)
    _mk_stage(ws, 1, PhaseName.PLAN, done=True)
    _mk_stage(ws, 2, PhaseName.EXECUTE, done=True)
    cc_path = _mk_stage(
        ws, 3, PhaseName.CROSS_CHECK, done=True, decision=Decision.PROCEED,
    )
    (cc_path / "verdict.json").write_text(json.dumps({
        "decision": "proceed",
        "reason": "no blocking findings",
        "evidence": {},
        "proposed_fix": "SHOULD NOT APPEAR",
        "target_file_hints": [],
    }))

    note = orchestrator._build_retry_note(ws, "review", iter_count=1)
    assert "proceed" in note
    assert "003_cross_check" in note
    assert "MUST read" not in note
    assert "SHOULD NOT APPEAR" not in note
    assert "Forward-motion note" in note or "no retry required" in note


def test_build_retry_note_picks_most_recent_verdict_stage(ws):
    """When multiple verdict stages exist, the most recent one wins."""
    for idx, phase in enumerate((
        PhaseName.SCOPE, PhaseName.PLAN, PhaseName.EXECUTE,
        PhaseName.CROSS_CHECK,
        PhaseName.REVIEW,       # 4: older review
        PhaseName.EXECUTE,      # 5: retry execute
        PhaseName.CROSS_CHECK,  # 6
    )):
        _mk_stage(ws, idx, phase, done=True)
    (ws.root / "004_review" / "verdict.json").write_text(json.dumps({
        "decision": "retry_execute",
        "reason": "first review",
        "evidence": {},
        "proposed_fix": "OLDER FIX",
    }))
    (ws.root / "006_cross_check" / "verdict.json").write_text(json.dumps({
        "decision": "retry_execute",
        "reason": "gate fail",
        "evidence": {},
        "proposed_fix": "NEWER FIX from cross_check",
    }))
    note = orchestrator._build_retry_note(ws, "execute", iter_count=2)
    assert "NEWER FIX from cross_check" in note
    assert "OLDER FIX" not in note


# ---------------------------------------------------------------------------
# _read_routing_suggestion — typed schema (parser audit Step F, 2026-05)
# ---------------------------------------------------------------------------

def test_read_routing_suggestion_canonical_schema_routes(ws):
    """Canonical schema {kind: 'route', target_phase: <phase>,
    confidence: 'high'} routes to that phase."""
    import json
    (ws.root / "routing_suggestion.json").write_text(json.dumps({
        "kind": "route",
        "target_phase": "review",
        "confidence": "high",
        "reason": "smoke test",
    }))
    target = orchestrator._read_routing_suggestion(ws)
    assert target == "review"
    # File should be consumed (renamed) so a second call returns None.
    assert (ws.root / "routing_suggestion.consumed.json").exists()
    assert not (ws.root / "routing_suggestion.json").exists()


def test_read_routing_suggestion_canonical_low_confidence_ignored(ws):
    """Low-confidence routing suggestion is silently ignored even
    when the canonical schema is well-formed."""
    import json
    (ws.root / "routing_suggestion.json").write_text(json.dumps({
        "kind": "route",
        "target_phase": "review",
        "confidence": "low",
    }))
    target = orchestrator._read_routing_suggestion(ws)
    assert target is None


def test_read_routing_suggestion_unknown_target_phase_returns_none(ws):
    """A canonical-schema suggestion with a target_phase outside
    PIPELINE_ORDER returns None and consumes the file."""
    import json
    (ws.root / "routing_suggestion.json").write_text(json.dumps({
        "kind": "route",
        "target_phase": "teleport",  # not a real phase
        "confidence": "high",
    }))
    target = orchestrator._read_routing_suggestion(ws)
    assert target is None
    assert (ws.root / "routing_suggestion.consumed.json").exists()


def test_read_routing_suggestion_legacy_schema_back_compat(ws):
    """Legacy `suggested_decision: 'route_to_<phase>'` still works
    for back-compat with workspaces written before parser audit Step F."""
    import json
    (ws.root / "routing_suggestion.json").write_text(json.dumps({
        "suggested_decision": "route_to_review",
        "confidence": "high",
    }))
    target = orchestrator._read_routing_suggestion(ws)
    assert target == "review"


def test_read_routing_suggestion_canonical_takes_precedence_over_legacy(ws):
    """If both `kind` and `suggested_decision` are set, the canonical
    `kind` form wins."""
    import json
    (ws.root / "routing_suggestion.json").write_text(json.dumps({
        "kind": "route",
        "target_phase": "plan",
        "suggested_decision": "route_to_review",  # legacy says review
        "confidence": "high",
    }))
    target = orchestrator._read_routing_suggestion(ws)
    assert target == "plan"


# ---------------------------------------------------------------------------
# FIX #123a — a capped cross_check ERROR retry_execute HALTS with a specific
# blocking event (a correctness gate), never advancing to REVIEW.
# ---------------------------------------------------------------------------

def _exhaust(s, phase, n=3):
    s.setdefault("iteration_counts", {})[phase] = n
    s.setdefault("bounds", {})["max_iters_per_phase"] = n
    return s


def test_capped_blocking_predicate_true_for_cross_check_retry(ws):
    _mk_stage(ws, 0, PhaseName.CROSS_CHECK, decision=Decision.RETRY_EXECUTE)
    assert orchestrator._capped_blocking_cross_check_retry(
        ws, PhaseName.EXECUTE) is True
    assert orchestrator._capped_blocking_cross_check_retry(
        ws, PhaseName.PLAN) is False


def test_capped_blocking_predicate_false_for_review_retry(ws):
    """The blocking-halt predicate is CROSS_CHECK-specific: a REVIEW-stage
    retry_execute is an analysis-adequacy retry, not a correctness block."""
    _mk_stage(ws, 0, PhaseName.REVIEW, decision=Decision.RETRY_EXECUTE)
    assert orchestrator._capped_blocking_cross_check_retry(
        ws, PhaseName.EXECUTE) is False


def test_run_next_stage_halts_on_capped_blocking_cross_check(ws):
    """End-to-end: driving run_next_stage with a forced EXECUTE phase, the
    latest done stage a CROSS_CHECK retry_execute, and EXECUTE at its cap
    HALTS with a HALTED_BLOCKING_CROSS_CHECK event and creates NO new
    stage."""
    _mk_stage(ws, 0, PhaseName.CROSS_CHECK, decision=Decision.RETRY_EXECUTE)
    s = state.read(ws.state_path)
    _exhaust(s, PhaseName.EXECUTE)
    state.write(ws.state_path, s)

    # run_next_stage reconciles the iter cap from ws.research_md before the
    # bounds check; the stub Workspace needs a minimal one. Set it EQUAL to
    # the exhausted cap so the reconcile is a no-op and bounds_exceeded fires.
    ws.research_md = types.SimpleNamespace(max_iters_per_phase=3)

    out = orchestrator.run_next_stage(ws, forced_phase=PhaseName.EXECUTE)
    assert out is not None
    assert out.status == "HALTED"
    assert out.decision == Decision.HALT
    assert state.read(ws.state_path).get("status") == RunStatus.HALTED
    kinds = [e.get("kind") for e in _read_events(ws)]
    assert EventKind.HALTED_BLOCKING_CROSS_CHECK in kinds
    assert EventKind.HALTED_BOUNDS not in kinds
    stage_names = sorted(p.name for p in ws.root.iterdir() if p.is_dir())
    assert stage_names == ["000_cross_check"]
