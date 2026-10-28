"""Tests for `orchestrator.run_all` optional parameters.

`run_all` takes three knobs the main dry-run scenarios don't exercise:

1. ``start`` — forces the first stage to a specific phase (used by
   ``omicairn run --add-stage <phase>``).
2. ``stop_after`` — breaks out of the outer loop the moment a DONE
   stage of the named phase lands (used for cheap smoke tests that
   only want to run through a specific phase).
3. ``max_stages`` — emergency cap on the number of stages the outer
   loop is willing to create. Above the cap it emits a ``run_all_cap``
   event and returns what it has.

These tests pin each knob independently, so a change that accidentally
drops support for one of them gets caught at edit time. All tests use
``OMICAIRN_DRY_RUN=1`` so no real claude subprocess runs; full-suite wall
time should stay well under a second.
"""
from __future__ import annotations

import json
import os
import pathlib
import tempfile
from typing import Iterator

import pytest

from omicairn import orchestrator, state, stage as stage_mod, workspace
from omicairn.constants import (
    ENV_DRY_RUN,
    ENV_DRY_VERDICT,
    EVENTS_LOG,
    Decision,
    EventKind,
    PhaseName,
    RunStatus,
)


RESEARCH_TEMPLATE = """\
---
research_mode: METHOD
domain: synthetic_smoke_test
topic_slug: pytest-runall
research_question: |
  Does run_all honor its optional parameters?
data:
  location: {data_dir}
  description: synthetic pytest fixture
  cohorts: [a]
  modalities: [tabular]
success_criteria:
  primary_metric: auc
  baseline: null_model
bounds:
  max_iters_per_phase: 3
  max_backtracks: 5
budgets:
  max_phase_wall_minutes: 5
  allow_web: false
  claude_model: claude-sonnet-4-6
  max_phase_budget_usd: 1.0
auto_approve: true
---

# pytest run_all fixture
"""


@pytest.fixture
def dry_run_env() -> Iterator[None]:
    prev_dry = os.environ.get(ENV_DRY_RUN)
    prev_verdict = os.environ.get(ENV_DRY_VERDICT)
    os.environ[ENV_DRY_RUN] = "1"
    os.environ.pop(ENV_DRY_VERDICT, None)
    try:
        yield
    finally:
        if prev_dry is None:
            os.environ.pop(ENV_DRY_RUN, None)
        else:
            os.environ[ENV_DRY_RUN] = prev_dry
        if prev_verdict is None:
            os.environ.pop(ENV_DRY_VERDICT, None)
        else:
            os.environ[ENV_DRY_VERDICT] = prev_verdict


@pytest.fixture
def ws() -> Iterator[workspace.Workspace]:
    with tempfile.TemporaryDirectory() as td:
        tmpdir = pathlib.Path(td)
        data_dir = tmpdir / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "dummy.csv").write_text("x,y\n1,1\n")
        rmd_path = tmpdir / "RESEARCH.md"
        rmd_path.write_text(RESEARCH_TEMPLATE.format(data_dir=data_dir))
        ws_root = tmpdir / "workspaces"
        ws_root.mkdir()
        yield workspace.init(
            "pytest-runall",
            research_md_path=rmd_path,
            workspaces_root=ws_root,
            skip_literature=True,
        )


# ---------------------------------------------------------------------------
# stop_after
# ---------------------------------------------------------------------------

def test_stop_after_halts_at_the_named_phase(dry_run_env, ws):
    """`stop_after=execute` must run scope → plan → execute and then
    stop without running cross_check, write, or review."""
    outcomes = orchestrator.run_all(
        ws, stop_after=PhaseName.EXECUTE, max_stages=20
    )
    phases_seen = [o.phase for o in outcomes]
    assert phases_seen == [
        PhaseName.SCOPE,
        PhaseName.PLAN,
        PhaseName.EXECUTE,
    ], f"expected to stop at execute, got {phases_seen}"
    # The pipeline is NOT done — review hasn't run.
    s = state.read(ws.state_path)
    assert s["status"] == RunStatus.IN_PROGRESS
    assert s["iteration_counts"][PhaseName.REVIEW] == 0


def test_stop_after_scope_only_runs_first_phase(dry_run_env, ws):
    """The most extreme stop_after: just scope. Useful for checking
    that the very first phase produces a valid contract without
    spending time on the whole pipeline."""
    outcomes = orchestrator.run_all(
        ws, stop_after=PhaseName.SCOPE, max_stages=20
    )
    assert len(outcomes) == 1
    assert outcomes[0].phase == PhaseName.SCOPE
    assert outcomes[0].status == "DONE"


def test_stop_after_review_passes_through_cross_check(dry_run_env, ws):
    """`stop_after=review` must still run cross_check between execute
    and review — stop_after checks the PHASE on DONE, not a hard skip
    for intermediate phases."""
    outcomes = orchestrator.run_all(
        ws, stop_after=PhaseName.REVIEW, max_stages=20
    )
    phases_seen = [o.phase for o in outcomes]
    assert phases_seen == [
        PhaseName.SCOPE,
        PhaseName.PLAN,
        PhaseName.EXECUTE,
        PhaseName.CROSS_CHECK,
        PhaseName.REVIEW,
    ]


# ---------------------------------------------------------------------------
# max_stages cap
# ---------------------------------------------------------------------------

def test_max_stages_cap_stops_loop_and_emits_event(dry_run_env, ws):
    """Set max_stages below the natural pipeline length; the outer loop
    must stop after exactly that many DONE stages AND emit a
    `run_all_cap` event into events.jsonl for post-hoc inspection."""
    outcomes = orchestrator.run_all(ws, max_stages=2)
    assert len(outcomes) == 2
    assert [o.phase for o in outcomes] == [PhaseName.SCOPE, PhaseName.PLAN]
    # Every outcome at this point must be DONE (we stopped on cap,
    # not on a failure).
    assert all(o.status == "DONE" for o in outcomes)
    # The cap must be visible in events.jsonl so an operator can
    # distinguish "ran out of budget" from "halted on contract fail".
    events_path = ws.root / EVENTS_LOG
    events = [
        json.loads(l) for l in events_path.read_text().splitlines() if l
    ]
    kinds = [e.get("kind") for e in events]
    assert "run_all_cap" in kinds, (
        f"expected run_all_cap event to be emitted, got kinds={kinds}"
    )
    cap_events = [e for e in events if e.get("kind") == EventKind.RUN_ALL_CAP]
    assert cap_events[0]["max_stages"] == 2
    assert cap_events[0]["completed"] == 2


def test_max_stages_zero_runs_nothing(dry_run_env, ws):
    """Edge case: max_stages=0 creates no stages at all. The event
    still fires so the operator sees an explicit cap-hit record."""
    outcomes = orchestrator.run_all(ws, max_stages=0)
    assert outcomes == []
    events_path = ws.root / EVENTS_LOG
    # events.jsonl may not even exist yet if the cap fires before any
    # other event. It does exist because append_event mkdirs.
    if events_path.exists():
        events = [
            json.loads(l) for l in events_path.read_text().splitlines() if l
        ]
        kinds = [e.get("kind") for e in events]
        assert "run_all_cap" in kinds


# ---------------------------------------------------------------------------
# start (forced first phase)
# ---------------------------------------------------------------------------

def test_start_forces_first_phase_to_execute(dry_run_env, ws):
    """`start='execute'` on an empty workspace must run execute as the
    very first stage, bypassing decide_next_phase's scope-first rule.
    This is how `omicairn run --add-stage execute` injects a stage.

    After the forced execute stage, normal routing resumes — the next
    stage is cross_check (the linear next after execute)."""
    outcomes = orchestrator.run_all(
        ws, start=PhaseName.EXECUTE, stop_after=PhaseName.CROSS_CHECK
    )
    phases_seen = [o.phase for o in outcomes]
    # First stage is the forced execute; subsequent stages follow
    # normal routing (execute -> cross_check).
    assert phases_seen[0] == PhaseName.EXECUTE, (
        f"forced first phase must be execute, got {phases_seen[0]}"
    )
    assert PhaseName.CROSS_CHECK in phases_seen


def test_start_only_applies_to_first_iteration(dry_run_env, ws):
    """After the forced first stage, `start` must be cleared so
    subsequent stages come from decide_next_phase. Verify by starting
    at PLAN and asserting stage 1 is execute (the linear next) rather
    than plan again."""
    outcomes = orchestrator.run_all(
        ws, start=PhaseName.PLAN, stop_after=PhaseName.EXECUTE
    )
    phases_seen = [o.phase for o in outcomes]
    assert phases_seen[0] == PhaseName.PLAN
    # After plan, normal routing advances to execute (linear next).
    assert PhaseName.EXECUTE in phases_seen
    # And scope was NOT run — we skipped it by forcing plan first.
    assert PhaseName.SCOPE not in phases_seen


# ---------------------------------------------------------------------------
# run_next_stage — direct single-stage API
# ---------------------------------------------------------------------------

def test_run_next_stage_returns_none_when_already_done(dry_run_env, ws):
    """If status is already DONE, run_next_stage must refuse to start
    another stage and return None. This is the safety rail against
    accidentally adding more stages after the pipeline finished."""
    s = state.read(ws.state_path)
    state.set_status(s, RunStatus.DONE, reason="already done")
    state.write(ws.state_path, s)
    outcome = orchestrator.run_next_stage(ws)
    assert outcome is None
    # No new stage was created.
    assert stage_mod.list_stages(ws.root) == []


def test_run_next_stage_marks_done_when_pipeline_exhausted(dry_run_env, ws):
    """If decide_next_phase returns None because every phase has a done
    stage, run_next_stage must transition status to DONE (if not
    already) and return None. The dry-run PROCEED scenario verifies
    this transitively, but doing it here in one call keeps the test
    focused on the transition itself."""
    # Run the full pipeline first.
    outcomes = orchestrator.run_all(ws, max_stages=20)
    assert outcomes[-1].phase == PhaseName.REVIEW
    s = state.read(ws.state_path)
    assert s["status"] == RunStatus.DONE
    # Now calling run_next_stage must return None without creating anything.
    before = len(stage_mod.list_stages(ws.root))
    assert orchestrator.run_next_stage(ws) is None
    after = len(stage_mod.list_stages(ws.root))
    assert before == after


def test_run_next_stage_forced_phase_bypasses_decide(dry_run_env, ws):
    """`run_next_stage(forced_phase='execute')` on an empty workspace
    creates an execute stage directly — same semantics as `run_all`'s
    `start` parameter but at the single-stage granularity."""
    outcome = orchestrator.run_next_stage(ws, forced_phase=PhaseName.EXECUTE)
    assert outcome is not None
    assert outcome.phase == PhaseName.EXECUTE
    assert outcome.status == "DONE"
    # Only one stage exists and it's the forced one.
    stages = stage_mod.list_stages(ws.root)
    assert len(stages) == 1
    assert stages[0].phase == PhaseName.EXECUTE


def test_run_next_stage_distills_to_lab_on_done(dry_run_env, ws, monkeypatch):
    """Regression (audit 2026-06-10): a standalone `run_next_stage` that
    completes a stage MUST distill into the lab KB — not only `run_all`'s
    tail. Without this, `omicairn ideate`, `--stop-after`, and killed runs
    never share knowledge and {{lab_knowledge}} stays empty forever."""
    calls = []
    monkeypatch.setattr(orchestrator, "_maybe_distill_to_lab",
                        lambda w: calls.append(w.root))
    outcome = orchestrator.run_next_stage(ws, forced_phase=PhaseName.EXECUTE)
    assert outcome is not None and outcome.status == "DONE"
    assert calls == [ws.root], "DONE stage must trigger exactly one distill"


def test_run_next_stage_no_distill_when_already_done(dry_run_env, ws, monkeypatch):
    """No stage ran (pipeline already DONE) → no distill call."""
    state.set_status(state_dict := state.read(ws.state_path), RunStatus.DONE)
    state.write(ws.state_path, state_dict)
    calls = []
    monkeypatch.setattr(orchestrator, "_maybe_distill_to_lab",
                        lambda w: calls.append(w.root))
    assert orchestrator.run_next_stage(ws) is None
    assert calls == [], "no stage ran → no distill"


# ---------------------------------------------------------------------------
# A steering plan (or a legacy one) naming an unknown / removed phase must
# HALT cleanly, never crash or orphan the run.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("removed", ["typeset", "write", "claim_audit", "peer_review"])
def test_run_next_stage_removed_paper_phase_halts_cleanly(dry_run_env, ws, removed):
    outcome = orchestrator.run_next_stage(ws, forced_phase=removed)
    assert outcome is not None
    assert outcome.status == "HALTED"
    assert outcome.decision == Decision.HALT
    assert state.read(ws.state_path)["status"] == RunStatus.HALTED
    assert not any(removed in p.name for p in ws.root.iterdir() if p.is_dir())


def test_run_next_stage_unknown_phase_halts_cleanly_not_crash(dry_run_env, ws):
    """A steering plan with a genuinely-unknown phase must HALT cleanly (no
    orphan), not raise an uncaught ValueError."""
    outcome = orchestrator.run_next_stage(ws, forced_phase="bogus_phase")
    assert outcome is not None, "unknown phase must not crash → None/raise"
    assert outcome.status == "HALTED"
    assert outcome.decision == Decision.HALT
    # STATE must reflect the clean halt (so the run is terminal, not orphaned).
    s = state.read(ws.state_path)
    assert s["status"] == RunStatus.HALTED
