"""End-to-end orchestrator tests using dry-run mode.

These tests replicate the three manually-verified smoke-test scenarios
from the flat-numbered-stage migration:

1. PROCEED path — every stage succeeds, pipeline reaches DONE.
2. retry_execute forced at cross_check — orchestrator creates three
   execute stages then halts on max_iters_per_phase.
3. back_to_plan forced at cross_check — orchestrator creates three
   plan stages then halts on max_iters_per_phase.

The tests use `OMICAIRN_DRY_RUN=1` so no real `claude -p` subprocess runs.
`OMICAIRN_DRY_VERDICT=<decision>` forces the cross_check / review phases to
emit a chosen verdict, which lets us drive routing without staging a
real failure.
"""
from __future__ import annotations

import json
import os
import pathlib
import tempfile
from typing import Iterator

import pytest

from omicairn import orchestrator, state, steering as steering_mod, workspace
from omicairn.constants import (
    ENV_DRY_RUN,
    ENV_DRY_VERDICT,
    Decision,
    EventKind,
    PhaseName,
    RunStatus,
    VERDICT_FILE,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

RESEARCH_TEMPLATE = """\
---
research_mode: METHOD
domain: synthetic_smoke_test
topic_slug: pytest-dryrun
research_question: |
  Does the dry-run pipeline round-trip end-to-end?
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

# pytest dry-run fixture

Not a real research case.
"""


@pytest.fixture
def dry_run_env() -> Iterator[None]:
    """Enable OMICAIRN_DRY_RUN for the duration of the test."""
    prev_dry = os.environ.get(ENV_DRY_RUN)
    prev_verdict = os.environ.get(ENV_DRY_VERDICT)
    os.environ[ENV_DRY_RUN] = "1"
    # Start with no forced verdict; individual tests set it themselves.
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


def _make_workspace(tmpdir: pathlib.Path) -> workspace.Workspace:
    """Scaffold a minimal workspace inside tmpdir and return it."""
    data_dir = tmpdir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "dummy.csv").write_text("x,y\n1,1\n")

    rmd_path = tmpdir / "RESEARCH.md"
    rmd_path.write_text(RESEARCH_TEMPLATE.format(data_dir=data_dir))

    ws_root = tmpdir / "workspaces"
    ws_root.mkdir()
    return workspace.init(
        "pytest-dryrun",
        research_md_path=rmd_path,
        workspaces_root=ws_root,
        skip_literature=True,
    )


@pytest.fixture
def ws() -> Iterator[workspace.Workspace]:
    with tempfile.TemporaryDirectory() as td:
        yield _make_workspace(pathlib.Path(td))


# ---------------------------------------------------------------------------
# Scenario 1: PROCEED path (the happy path)
# ---------------------------------------------------------------------------

def test_dry_run_proceed_reaches_done(dry_run_env, ws):
    outcomes = orchestrator.run_all(ws, max_stages=20)
    # Five stages in order; the analysis REVIEW is terminal and defaults
    # to DONE in dry-run.
    assert [o.stage_dir_rel for o in outcomes] == [
        "000_scope",
        "001_plan",
        "002_execute",
        "003_cross_check",
        "004_review",
    ]
    assert all(o.status == "DONE" for o in outcomes)
    # Review dry-run defaults to "done" when no OMICAIRN_DRY_VERDICT is set.
    assert outcomes[-1].decision == Decision.DONE
    # Cross-check inherits the same default since the forced-verdict path
    # is the same env var. We also assert STATE.md reached DONE.
    s = state.read(ws.state_path)
    assert s["status"] == RunStatus.DONE


def test_dry_run_proceed_updates_iteration_counts(dry_run_env, ws):
    orchestrator.run_all(ws, max_stages=20)
    s = state.read(ws.state_path)
    # Each phase ran exactly once on the happy path.
    for phase_name in (
        PhaseName.SCOPE,
        PhaseName.PLAN,
        PhaseName.EXECUTE,
        PhaseName.CROSS_CHECK,
        PhaseName.REVIEW,
    ):
        assert s["iteration_counts"][phase_name] == 1, (
            f"expected 1 {phase_name} iteration, got "
            f"{s['iteration_counts'][phase_name]}"
        )
    assert s["backtracks_used"] == 0


# ---------------------------------------------------------------------------
# Scenario 2: forced retry_execute halts after max_iters
# ---------------------------------------------------------------------------

def test_dry_run_retry_execute_halts_after_three_attempts(dry_run_env, ws):
    """Force every cross_check to emit `retry_execute`. The orchestrator
    must run execute three times (iter cap) then halt."""
    os.environ[ENV_DRY_VERDICT] = Decision.RETRY_EXECUTE
    outcomes = orchestrator.run_all(ws, max_stages=20)

    # Count only the execute stages that were actually created on disk
    # (HALTED outcomes also carry phase=execute because that's the
    # phase the orchestrator refused to start). DONE-status outcomes
    # correspond 1:1 with created stage dirs.
    execute_done = [
        o for o in outcomes
        if o.phase == PhaseName.EXECUTE and o.status == "DONE"
    ]
    assert len(execute_done) == 3, (
        f"expected 3 DONE execute attempts before halt, got {len(execute_done)}"
    )

    # The last outcome must be HALTED due to the iteration cap, and
    # its phase must be execute (the phase the orchestrator refused).
    last = outcomes[-1]
    assert last.status == "HALTED"
    assert last.decision == Decision.HALT
    assert last.phase == PhaseName.EXECUTE
    assert "max_iters_per_phase" in last.note
    assert "execute" in last.note

    # STATE.md reflects the halt with a reason.
    s = state.read(ws.state_path)
    assert s["status"] == RunStatus.HALTED
    assert s["iteration_counts"][PhaseName.EXECUTE] == 3


def test_dry_run_max_iters_reconciled_upward_from_config(dry_run_env, ws):
    """A run whose STATE.md froze a SMALLER iter cap than the live config
    (e.g. started before the case's omicairn_config raised it — the d7
    extension halted at a stale 5 while config said 8) must reconcile the
    cap UPWARD from `ws.research_md.max_iters_per_phase` and honor the
    larger cap, not halt at the stale frozen value.

    The fixture's config cap is 3. We backdate STATE.md's frozen cap to 1,
    force retry_execute forever, and assert the run still reaches the
    config cap of 3 (not halting at 1) and journals the reconciliation.
    """
    os.environ[ENV_DRY_VERDICT] = Decision.RETRY_EXECUTE
    # Simulate a run initialized under an older, smaller cap.
    s0 = state.read(ws.state_path)
    s0.setdefault("bounds", {})["max_iters_per_phase"] = 1
    state.write(ws.state_path, s0)
    assert int(ws.research_md.max_iters_per_phase) == 3  # live config

    outcomes = orchestrator.run_all(ws, max_stages=20)

    execute_done = [
        o for o in outcomes
        if o.phase == PhaseName.EXECUTE and o.status == "DONE"
    ]
    # Honored the config cap (3), not the stale frozen cap (1).
    assert len(execute_done) == 3, (
        f"expected 3 execute attempts after upward reconciliation, "
        f"got {len(execute_done)}"
    )
    s = state.read(ws.state_path)
    assert s["bounds"]["max_iters_per_phase"] == 3
    # The reconciliation was journaled to events.jsonl.
    import json as _json
    events_path = ws.root / "events.jsonl"
    events = [_json.loads(line)
              for line in events_path.read_text().splitlines() if line]
    recon = [e for e in events
             if e.get("kind") == EventKind.MAX_ITERS_RECONCILED_FROM_CONFIG]
    assert recon, "expected a max_iters_reconciled_from_config event"
    assert recon[0]["from"] == 1 and recon[0]["to"] == 3


def test_dry_run_max_iters_not_lowered_mid_flight(dry_run_env, ws):
    """The reconciliation is UPWARD-only: a STATE.md cap LARGER than the
    live config must NOT be silently lowered mid-flight (that could strand
    a run that legitimately needs the iterations it was started with)."""
    os.environ[ENV_DRY_VERDICT] = Decision.RETRY_EXECUTE
    s0 = state.read(ws.state_path)
    s0.setdefault("bounds", {})["max_iters_per_phase"] = 5  # > config's 3
    state.write(ws.state_path, s0)

    outcomes = orchestrator.run_all(ws, max_stages=20)
    execute_done = [
        o for o in outcomes
        if o.phase == PhaseName.EXECUTE and o.status == "DONE"
    ]
    # Honored the larger STATE cap (5), NOT lowered to the config's 3.
    assert len(execute_done) == 5
    s = state.read(ws.state_path)
    assert s["bounds"]["max_iters_per_phase"] == 5


# ---------------------------------------------------------------------------
# Scenario 3: forced back_to_plan halts after three plan attempts
# ---------------------------------------------------------------------------

def test_dry_run_back_to_plan_halts_after_three_attempts(dry_run_env, ws):
    """Force cross_check to emit `back_to_plan`. The orchestrator must
    run plan three times (iter cap) before halting. The backtrack
    counter must increase on each backtrack."""
    os.environ[ENV_DRY_VERDICT] = Decision.BACK_TO_PLAN
    outcomes = orchestrator.run_all(ws, max_stages=20)

    plan_done = [
        o for o in outcomes
        if o.phase == PhaseName.PLAN and o.status == "DONE"
    ]
    assert len(plan_done) == 3, (
        f"expected 3 DONE plan attempts before halt, got {len(plan_done)}"
    )

    last = outcomes[-1]
    assert last.status == "HALTED"
    assert last.decision == Decision.HALT
    assert last.phase == PhaseName.PLAN
    assert "plan" in last.note
    assert "max_iters_per_phase" in last.note

    s = state.read(ws.state_path)
    assert s["status"] == RunStatus.HALTED
    # We backtracked at least twice (the first plan is the original,
    # the two re-plans each increment the counter).
    assert s["backtracks_used"] >= 2


# ---------------------------------------------------------------------------
# Scenario 4: forced back_to_execute — distinguishes from retry_execute
# ---------------------------------------------------------------------------

def test_dry_run_back_to_execute_increments_backtracks(dry_run_env, ws):
    """`back_to_execute` routes to execute AND counts as a backtrack.
    `retry_execute` also routes to execute but does NOT count as a
    backtrack. Both halt on the same iter cap (3), so the only way to
    tell them apart from STATE.md is `backtracks_used`.
    """
    os.environ[ENV_DRY_VERDICT] = Decision.BACK_TO_EXECUTE
    outcomes = orchestrator.run_all(ws, max_stages=20)

    execute_done = [
        o for o in outcomes
        if o.phase == PhaseName.EXECUTE and o.status == "DONE"
    ]
    assert len(execute_done) == 3, (
        f"expected 3 DONE execute attempts before halt, got {len(execute_done)}"
    )

    last = outcomes[-1]
    assert last.status == "HALTED"
    assert last.decision == Decision.HALT
    assert last.phase == PhaseName.EXECUTE

    s = state.read(ws.state_path)
    assert s["status"] == RunStatus.HALTED
    assert s["iteration_counts"][PhaseName.EXECUTE] == 3
    # The critical assertion: back_to_* increments backtracks_used.
    # We bounced back twice (from cc #1 and cc #2); the third cc's
    # bounce hits the bounds check before the next execute runs but
    # the decision is still recorded, so backtracks_used is 3.
    assert s["backtracks_used"] >= 2, (
        f"back_to_execute must increment backtracks_used; got "
        f"{s['backtracks_used']}"
    )


# ---------------------------------------------------------------------------
# Scenario 5: forced back_to_scope — full pipeline reset
# ---------------------------------------------------------------------------

def test_dry_run_back_to_scope_reruns_full_pipeline(dry_run_env, ws):
    """back_to_scope routes all the way back to scope. Each loop runs
    scope → plan → execute → cross_check, so iter counts for plan and
    execute also climb in lockstep with scope."""
    os.environ[ENV_DRY_VERDICT] = Decision.BACK_TO_SCOPE
    outcomes = orchestrator.run_all(ws, max_stages=30)

    scope_done = [
        o for o in outcomes
        if o.phase == PhaseName.SCOPE and o.status == "DONE"
    ]
    assert len(scope_done) == 3, (
        f"expected 3 DONE scope attempts before halt, got {len(scope_done)}"
    )

    last = outcomes[-1]
    assert last.status == "HALTED"
    assert last.phase == PhaseName.SCOPE

    s = state.read(ws.state_path)
    assert s["status"] == RunStatus.HALTED
    # Every loop advances all four pre-cross_check phases together.
    assert s["iteration_counts"][PhaseName.SCOPE] == 3
    assert s["iteration_counts"][PhaseName.PLAN] == 3
    assert s["iteration_counts"][PhaseName.EXECUTE] == 3
    assert s["iteration_counts"][PhaseName.CROSS_CHECK] == 3
    assert s["backtracks_used"] >= 2


# ---------------------------------------------------------------------------
# Scenario 7: max_backtracks cap halts before max_iters_per_phase
# ---------------------------------------------------------------------------

_TIGHT_BACKTRACKS_TEMPLATE = """\
---
research_mode: METHOD
domain: synthetic_smoke_test
topic_slug: pytest-dryrun-tight
research_question: |
  Does the backtrack cap halt before the iter cap?
data:
  location: {data_dir}
  description: synthetic pytest fixture
  cohorts: [a]
  modalities: [tabular]
success_criteria:
  primary_metric: auc
  baseline: null_model
bounds:
  max_iters_per_phase: 10
  max_backtracks: 2
budgets:
  max_phase_wall_minutes: 5
  allow_web: false
  claude_model: claude-sonnet-4-6
  max_phase_budget_usd: 1.0
auto_approve: true
---

# pytest dry-run fixture (tight backtrack cap)
"""


def test_dry_run_max_backtracks_halts_before_iter_cap(dry_run_env):
    """With max_iters_per_phase=10 but max_backtracks=2, the backtrack
    cap must halt the pipeline first. This is the only regression guard
    for the `backtracks_used >= max_backtracks` branch of
    state.bounds_exceeded."""
    os.environ[ENV_DRY_VERDICT] = Decision.BACK_TO_PLAN
    with tempfile.TemporaryDirectory() as td:
        tmpdir = pathlib.Path(td)
        data_dir = tmpdir / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "dummy.csv").write_text("x,y\n1,1\n")
        rmd_path = tmpdir / "RESEARCH.md"
        rmd_path.write_text(_TIGHT_BACKTRACKS_TEMPLATE.format(data_dir=data_dir))
        ws_root = tmpdir / "workspaces"
        ws_root.mkdir()
        ws = workspace.init(
            "pytest-dryrun-tight",
            research_md_path=rmd_path,
            workspaces_root=ws_root,
            skip_literature=True,
        )

        outcomes = orchestrator.run_all(ws, max_stages=30)

        last = outcomes[-1]
        assert last.status == "HALTED"
        assert "max_backtracks" in last.note, (
            f"expected halt note to mention max_backtracks; got {last.note!r}"
        )

        s = state.read(ws.state_path)
        assert s["status"] == RunStatus.HALTED
        assert s["backtracks_used"] == 2, (
            f"expected backtracks_used=2 at halt, got {s['backtracks_used']}"
        )
        # Plan iter should be <= 3 — we never reach the iter cap of 10.
        assert s["iteration_counts"][PhaseName.PLAN] <= 3


# ---------------------------------------------------------------------------
# Scenario 8: decide_next_phase routing on an empty workspace
# ---------------------------------------------------------------------------

def test_decide_next_phase_empty_workspace_returns_scope(dry_run_env, ws):
    # After init, no stages exist yet — the first phase must be scope.
    assert orchestrator.decide_next_phase(ws) == PhaseName.SCOPE


def test_decide_next_phase_returns_none_when_halted(dry_run_env, ws):
    s = state.read(ws.state_path)
    state.set_status(s, RunStatus.HALTED, reason="manual")
    state.write(ws.state_path, s)
    assert orchestrator.decide_next_phase(ws) is None


# ---------------------------------------------------------------------------
# Scenario 9: HALT verdict must transition state to HALTED (not DONE)
# ---------------------------------------------------------------------------

def test_halt_verdict_from_cross_check_marks_state_halted(dry_run_env, ws):
    """Forcing every cross_check / review to emit `halt` must leave
    STATE.md at `halted`, not `done`. The two final states are
    semantically different — `done` means the pipeline completed its
    analysis review; `halted` means it refused to continue. Before
    the fix, `decide_next_phase` returned None for both HALT and DONE,
    and `run_next_stage` unconditionally set status=DONE on None,
    collapsing the distinction.
    """
    os.environ[ENV_DRY_VERDICT] = Decision.HALT
    outcomes = orchestrator.run_all(ws, max_stages=20)
    assert outcomes, "expected at least one stage"

    s = state.read(ws.state_path)
    assert s["status"] == RunStatus.HALTED, (
        f"expected HALTED after HALT verdict; got {s['status']}"
    )
    # The halted_reason should carry something from the verdict reason,
    # not the generic "all phases complete" string.
    reason = s.get("halted_reason") or ""
    assert "halt" in reason.lower() or "dry-run" in reason.lower(), (
        f"halted_reason should come from verdict; got {reason!r}"
    )


def test_cross_check_forced_with_no_execute_stage_halts_gracefully(
    dry_run_env, ws
):
    """`run_next_stage(forced_phase='cross_check')` on a workspace
    with no prior execute stage must NOT crash. It must write a
    HALT verdict + findings.md + done.flag, return status=DONE
    (because contract passed), and then transition the run to
    HALTED on the next call (because the HALT verdict is now the
    most recent one).

    This pins the 'no prior execute stage found' graceful-fail
    branch in _run_cross_check_stage, which was previously only a
    defensive comment — no test exercised it."""
    from omicairn import stage as stage_mod

    # First call: force cross_check with no execute stage present.
    outcome = orchestrator.run_next_stage(
        ws, forced_phase=PhaseName.CROSS_CHECK
    )
    assert outcome is not None
    # The stage itself succeeds (contract passes — verdict.json +
    # findings.md + done.flag are all written), but the decision
    # inside the verdict is HALT.
    assert outcome.status == "DONE"
    assert outcome.decision == Decision.HALT
    assert outcome.phase == PhaseName.CROSS_CHECK
    assert "no prior execute" in outcome.note

    # Exactly one stage dir was created, and it's the cross_check one.
    stages = stage_mod.list_stages(ws.root)
    assert len(stages) == 1
    assert stages[0].phase == PhaseName.CROSS_CHECK
    assert stages[0].done

    # Second call: decide_next_phase reads the HALT verdict and returns
    # None. The fixed _final_status_from_last_verdict then transitions
    # the run to HALTED (not DONE) because the verdict said HALT.
    follow_up = orchestrator.run_next_stage(ws)
    assert follow_up is None
    s = state.read(ws.state_path)
    assert s["status"] == RunStatus.HALTED, (
        f"cross_check graceful-fail HALT should transition to HALTED; "
        f"got {s['status']}"
    )


def test_halt_verdict_allows_subsequent_run_next_stage_noop(dry_run_env, ws):
    """After HALT, calling run_next_stage again must be a no-op
    (return None without creating a new stage). The HALTED status
    is the terminal guard — it must block both the explicit
    decide_next_phase path and any forced_phase attempt."""
    os.environ[ENV_DRY_VERDICT] = Decision.HALT
    orchestrator.run_all(ws, max_stages=20)
    s = state.read(ws.state_path)
    assert s["status"] == RunStatus.HALTED

    # Follow-up call must see HALTED and return None.
    result = orchestrator.run_next_stage(ws)
    assert result is None


def test_proceed_then_done_still_transitions_to_done(dry_run_env, ws):
    """Regression guard for the fix: the DONE path must still work.
    The default dry-run review verdict is DONE, so a happy-path run
    should leave STATE.md at `done`, not `halted`. (A too-eager fix
    could easily flip the default the wrong way.)"""
    outcomes = orchestrator.run_all(ws, max_stages=20)
    assert all(o.status == "DONE" for o in outcomes)
    s = state.read(ws.state_path)
    assert s["status"] == RunStatus.DONE, (
        f"happy-path dry-run must still reach DONE, not HALTED; got {s['status']}"
    )


# ---------------------------------------------------------------------------
# Scenario 10: every event in events.jsonl is well-formed
# ---------------------------------------------------------------------------

def test_every_event_in_full_dryrun_has_kind_and_ts(dry_run_env, ws):
    """Integration regression guard: run the full pipeline, then read
    events.jsonl and confirm every line has both `kind` (non-empty)
    and `ts` fields. append_event enforces this at the function level
    now, but that only catches callers that go through it. This test
    additionally pins the shape of every event the pipeline actually
    emits during a real dry-run, so if someone starts writing to
    events.jsonl by hand the bad record gets caught."""
    import json
    from omicairn.constants import EVENTS_LOG
    outcomes = orchestrator.run_all(ws, max_stages=20)
    assert outcomes, "dry-run must produce at least one stage"
    events_path = ws.root / EVENTS_LOG
    assert events_path.exists(), "events.jsonl must exist after a run"
    lines = [l for l in events_path.read_text().splitlines() if l.strip()]
    # Every line must be valid JSON.
    records = [json.loads(l) for l in lines]
    assert len(records) >= 1
    # Every event must have a non-empty `kind` and a `ts` field.
    bad: list[tuple[int, dict]] = []
    for i, rec in enumerate(records):
        if not rec.get("kind"):
            bad.append((i, rec))
            continue
        if "ts" not in rec:
            bad.append((i, rec))
    assert not bad, (
        f"events.jsonl has {len(bad)} malformed records missing "
        f"`kind` or `ts`: {bad[:3]}"
    )
    # A happy-path dry-run must include at least one workspace_init and
    # one stage_done event — these are the two backbones of post-hoc
    # reconstruction, and their absence would indicate append_event was
    # bypassed entirely.
    kinds = {r["kind"] for r in records}
    assert EventKind.WORKSPACE_INIT in kinds, (
        f"workspace_init event missing from events.jsonl: kinds={kinds}"
    )
    assert EventKind.STAGE_DONE in kinds, (
        f"stage_done event missing from events.jsonl: kinds={kinds}"
    )


# ---------------------------------------------------------------------------
# Scenario 11: invalid forced verdict falls back to proceed
#
# `omicairn run --dry-verdict not_a_real_decision` is a developer foot-gun:
# the orchestrator should NOT crash on `is_valid() is False`. Instead it
# must fall back to `proceed` and log "invalid forced verdict" so the
# dev can see what happened. Covers orchestrator.py L318.
# ---------------------------------------------------------------------------

def test_dry_run_invalid_forced_verdict_falls_back_to_proceed(dry_run_env, ws):
    """Line 318: when OMICAIRN_DRY_VERDICT holds a string that's not in
    Decision.*, the cross_check stage must still emit a legal verdict
    (proceed) and a findings.md whose reason captures the fallback."""
    import os
    from omicairn.constants import ENV_DRY_VERDICT
    os.environ[ENV_DRY_VERDICT] = "not_a_real_decision"

    outcomes = orchestrator.run_all(ws, max_stages=20)
    # cross_check falls back to proceed; REVIEW's placeholder verdict.json
    # carries the invalid decision, so it is a CONTRACT_FAIL (never a
    # silent DONE) and the bounded retry loop eventually halts the run.
    review_outcomes = [o for o in outcomes if o.phase == PhaseName.REVIEW]
    assert review_outcomes
    assert all(o.status == "CONTRACT_FAIL" for o in review_outcomes)
    s = state.read(ws.state_path)
    assert s["status"] != RunStatus.DONE

    # The cross_check stage outcome must have decision=proceed with a
    # reason mentioning the invalid forced verdict.
    cross_check_outcomes = [
        o for o in outcomes if o.phase == PhaseName.CROSS_CHECK
    ]
    assert len(cross_check_outcomes) == 1
    cc = cross_check_outcomes[0]
    assert cc.decision == Decision.PROCEED
    assert "invalid forced verdict" in cc.note

    # findings.md must document the fallback for post-hoc inspection.
    cc_stage_dir = ws.root / cc.stage_dir_rel
    findings = (cc_stage_dir / "findings.md").read_text()
    assert "not_a_real_decision" in findings


# ---------------------------------------------------------------------------
# Scenario 12: real cross_check with errors in findings.md
#
# In dry-run with no OMICAIRN_DRY_VERDICT, cross_check runs for real. If we
# plant an execute stage with mismatched CIs between results.json and
# analysis.md, cross_check emits errors — which orchestrator must
# render into findings.md with an `## Errors` section. Covers
# orchestrator.py L330-333.
# ---------------------------------------------------------------------------

def test_cross_check_findings_renders_errors_section(dry_run_env, ws):
    """Lines 331-333: run scope+plan+execute in dry-run, then overwrite
    the execute stage's results.json and analysis.md to manufacture a
    real cross_check error (baselines without paired delta CI).
    cross_check stage is then run without a forced verdict so the
    real build_execute_verdict fires, and findings.md must contain
    an "## Errors" section."""
    import json
    # Ensure NO forced verdict — we want the real cross_check path.
    import os
    from omicairn.constants import ENV_DRY_VERDICT
    os.environ.pop(ENV_DRY_VERDICT, None)

    # Run scope, plan, execute (stop before cross_check).
    outcomes = orchestrator.run_all(ws, stop_after=PhaseName.EXECUTE)
    assert [o.stage_dir_rel for o in outcomes] == [
        "000_scope", "001_plan", "002_execute",
    ]

    # Overwrite the execute stage's artifacts with a results.json that
    # declares baselines but no paired delta CI → cross_check error.
    exec_dir = ws.root / "002_execute"
    (exec_dir / "results.json").write_text(json.dumps({
        "primary_model": {"auc": 0.82, "ci_95": [0.70, 0.91]},
        "baselines": {"null_model": {"auc": 0.50}},
        # Intentionally NO comparisons.delta with ci_95 → error.
    }))
    (exec_dir / "analysis.md").write_text(
        "# Analysis\n\n"
        "Primary AUC=0.82 CI [0.70, 0.91], null AUC=0.50.\n"
    )

    # Now run cross_check. With OMICAIRN_DRY_VERDICT unset, the orchestrator
    # runs the real build_execute_verdict against our planted files.
    cc_outcome = orchestrator.run_next_stage(ws)
    assert cc_outcome is not None
    assert cc_outcome.phase == PhaseName.CROSS_CHECK

    cc_stage_dir = ws.root / cc_outcome.stage_dir_rel
    findings = (cc_stage_dir / "findings.md").read_text()
    # Error section header must appear (line 331) with at least one
    # bullet (line 332).
    assert "## Errors" in findings
    assert "baselines" in findings  # the error text mentions baselines
    assert "paired" in findings or "delta" in findings


# ---------------------------------------------------------------------------
# Scenario 13: real cross_check with warnings in findings.md
#
# Warnings fire when primary_model CIs are not fully present in
# analysis.md but baselines+delta are consistent (no errors). Covers
# orchestrator.py L334-337.
# ---------------------------------------------------------------------------

def test_cross_check_findings_renders_warnings_section(dry_run_env, ws):
    """Lines 335-337: a primary_model ci_95 whose numbers don't appear
    in analysis.md (and no baselines/delta, so no error) must produce
    a warnings-only cross_check result, rendered into findings.md
    under a "## Warnings" header."""
    import json
    import os
    from omicairn.constants import ENV_DRY_VERDICT
    os.environ.pop(ENV_DRY_VERDICT, None)

    orchestrator.run_all(ws, stop_after=PhaseName.EXECUTE)
    exec_dir = ws.root / "002_execute"
    # No baselines / comparisons — so no error path fires. But the
    # analysis.md CI doesn't match the results.json CI, so primary
    # ci_95 triggers a "not fully present" warning.
    (exec_dir / "results.json").write_text(json.dumps({
        "primary_model": {"auc": 0.82, "ci_95": [0.70, 0.91]},
    }))
    (exec_dir / "analysis.md").write_text(
        "# Analysis\n\n"
        "Primary AUC=0.82 — see Table 1 for confidence interval.\n"
    )

    cc_outcome = orchestrator.run_next_stage(ws)
    cc_stage_dir = ws.root / cc_outcome.stage_dir_rel
    findings = (cc_stage_dir / "findings.md").read_text()
    # Warnings header must appear (line 335) with at least one bullet
    # (line 336). No errors header because no baselines+no comparisons.
    assert "## Warnings" in findings
    assert "## Errors" not in findings
    assert "not fully present" in findings or "primary_model" in findings


# ---------------------------------------------------------------------------
# Scenario 14: review phase with an unreadable / invalid verdict.json
#
# verdict.json is in REVIEW's required_outputs, so the contract passes
# when the file exists. If read_verdict still rejects it (parse error or
# a decision outside the allowed set, e.g. a legacy `retry_write`), the
# stage is a CONTRACT_FAIL so the bounded retry loop re-runs REVIEW —
# never a silent DONE.
# ---------------------------------------------------------------------------

def test_review_with_backtrack_decision_increments_counter(
    dry_run_env, ws, monkeypatch
):
    """Line 521: when the review phase emits a BACKTRACK_DECISION (e.g.
    back_to_execute), orchestrator must call state.record_backtrack so
    the backtrack counter advances. The dry-run placeholder writes
    verdict.json with the forced decision, so monkeypatching
    verdict_mod.read_verdict gives us the simplest path to a
    back_to_execute review without also affecting earlier stages
    (the backtrack path loops via back_to_execute, so if we set
    OMICAIRN_DRY_VERDICT naively cross_check would also emit it and the
    pipeline would loop)."""
    # Run through to cross_check in dry-run so the next run_next_stage
    # call lands on REVIEW (cross_check proceeds naturally in dry-run).
    orchestrator.run_all(ws, stop_after=PhaseName.CROSS_CHECK)

    # Before the review stage, monkeypatch read_verdict to return a
    # back_to_execute verdict.
    from omicairn import orchestrator as orch
    from omicairn import verdict as verdict_mod
    fake_verdict = verdict_mod.Verdict(
        decision=Decision.BACK_TO_EXECUTE,
        reason="test: force backtrack from review",
        evidence=[],
    )
    monkeypatch.setattr(orch.verdict_mod, "read_verdict",
                        lambda p: fake_verdict)

    before_backtracks = state.read(ws.state_path).get("backtracks_used", 0)
    # Force REVIEW directly: with the monkeypatch active, decide_next_phase
    # would read the fake back_to_execute verdict from the prior
    # cross_check stage and route to EXECUTE. Forcing the phase bypasses
    # routing and lets us test what happens when REVIEW emits the
    # back_to_execute verdict specifically.
    review_outcome = orchestrator.run_next_stage(ws, forced_phase=PhaseName.REVIEW)
    assert review_outcome is not None
    assert review_outcome.phase == PhaseName.REVIEW
    assert review_outcome.decision == Decision.BACK_TO_EXECUTE

    # The backtrack counter must have incremented.
    s = state.read(ws.state_path)
    assert s["backtracks_used"] == before_backtracks + 1
    assert s["pending_decision"] == Decision.BACK_TO_EXECUTE


def test_legacy_paper_stage_dirs_do_not_break_resume(dry_run_env, ws):
    """Resuming a paper-era workspace that carries NNN_write / NNN_claim_audit
    dirs (with stale retry_write verdicts) must not crash: the legacy dirs are
    not stages, they only occupy indices, and the run finishes at REVIEW."""
    import json

    orchestrator.run_all(ws, stop_after=PhaseName.CROSS_CHECK)
    for legacy in ("004_write", "005_claim_audit"):
        d = ws.root / legacy
        d.mkdir()
        (d / "done.flag").write_text("1\n")
        (d / "verdict.json").write_text(json.dumps(
            {"decision": "retry_write", "reason": "r", "evidence": []}))
    outcomes = orchestrator.run_all(ws, max_stages=10)
    assert [o.stage_dir_rel for o in outcomes] == ["006_review"]
    assert state.read(ws.state_path)["status"] == RunStatus.DONE


def test_review_unreadable_verdict_is_contract_fail(dry_run_env, ws, monkeypatch):
    """verdict.json exists but read_verdict returns None → CONTRACT_FAIL
    (not DONE), and no decision is recorded."""
    orchestrator.run_all(ws, stop_after=PhaseName.CROSS_CHECK)

    from omicairn import orchestrator as orch
    monkeypatch.setattr(orch.verdict_mod, "read_verdict", lambda p: None)

    review_outcome = orchestrator.run_next_stage(ws)
    assert review_outcome is not None
    assert review_outcome.phase == PhaseName.REVIEW
    assert review_outcome.status == "CONTRACT_FAIL"
    assert review_outcome.decision is None
    assert "verdict" in review_outcome.note
    s = state.read(ws.state_path)
    assert s.get("pending_decision") != Decision.DONE


def test_review_removed_decision_is_contract_fail(dry_run_env, ws, monkeypatch):
    """A live-path REVIEW that writes a removed paper-era decision
    (`retry_write`) must be a CONTRACT_FAIL, not a silent DONE."""
    orchestrator.run_all(ws, stop_after=PhaseName.CROSS_CHECK)
    monkeypatch.setenv(ENV_DRY_VERDICT, "retry_write")

    review_outcome = orchestrator.run_next_stage(ws)
    assert review_outcome is not None
    assert review_outcome.phase == PhaseName.REVIEW
    assert review_outcome.status == "CONTRACT_FAIL"
    assert review_outcome.decision is None
    verdict = json.loads(
        (ws.root / review_outcome.stage_dir_rel / VERDICT_FILE).read_text())
    assert verdict["decision"] == "retry_write"
    assert not (ws.root / review_outcome.stage_dir_rel / "done.flag").exists()


def test_resume_after_invalid_review_verdict_reruns_review(
    dry_run_env, ws, monkeypatch
):
    """A REVIEW that failed on a removed decision must not be read as a
    finished run on resume: re-activating the run creates a new REVIEW
    stage instead of finishing DONE with zero stages run."""
    monkeypatch.setenv(ENV_DRY_VERDICT, "retry_write")
    orchestrator.run_all(ws, max_stages=20)
    reviews_before = sorted(p.name for p in ws.root.glob("*_review"))
    assert reviews_before
    s = state.read(ws.state_path)
    assert s["status"] != RunStatus.DONE
    s["status"] = RunStatus.IN_PROGRESS
    state.write(ws.state_path, s)

    outcomes = orchestrator.run_all(ws, max_stages=1)
    assert [o.phase for o in outcomes] == [PhaseName.REVIEW]
    assert outcomes[0].stage_dir_rel not in reviews_before
    assert state.read(ws.state_path)["status"] != RunStatus.DONE


def test_legacy_done_review_with_removed_decision_reruns_review(dry_run_env, ws):
    """A paper-era REVIEW stage stamped done with a `retry_write` verdict
    routes back to REVIEW; it never maps to DONE, and the unstamped-stage
    rescue never PROCEEDs past it."""
    from omicairn import orchestrator as orch

    orchestrator.run_all(ws, stop_after=PhaseName.CROSS_CHECK)
    legacy = ws.root / "004_review"
    legacy.mkdir()
    (legacy / "done.flag").write_text("1\n")
    (legacy / VERDICT_FILE).write_text(json.dumps(
        {"decision": "retry_write", "reason": "r", "evidence": []}))
    assert orch.decide_next_phase(ws) == PhaseName.REVIEW
    status, _ = orch._final_status_from_last_verdict(ws)
    assert status == RunStatus.HALTED

    outcomes = orchestrator.run_all(ws, max_stages=10)
    assert [o.stage_dir_rel for o in outcomes] == ["005_review"]
    assert state.read(ws.state_path)["status"] == RunStatus.DONE


# ---------------------------------------------------------------------------
# Terminal-status + inline post-stage check edges (orchestrator.py L164/L540/L559)
# ---------------------------------------------------------------------------

def test_final_status_from_last_verdict_empty_workspace_is_done(dry_run_env, ws):
    """Line 164: when `_final_status_from_last_verdict` runs on a
    workspace with no done stages (`last_done is None`), it must
    return (DONE, 'all phases complete'). This is the safety
    fall-through the function's docstring advertises — 'caller
    shouldn't hit this, but fail safely'. We call it directly
    (rather than trying to manufacture the upstream condition)
    because `run_next_stage` only reaches it after decide_next_phase
    returns None, and on a fresh workspace the next phase is always
    SCOPE. Direct-call is the only path that pins this branch."""
    from omicairn import orchestrator as orch
    status, reason = orch._final_status_from_last_verdict(ws)
    assert status == RunStatus.DONE
    assert reason == "all phases complete"


def test_review_stage_notes_inline_check_warnings(dry_run_env, ws, monkeypatch):
    """`_run_llm_stage` appends '[cross_check warnings: N]' to the outcome
    note when the inline post-stage check returns warnings but no errors."""
    from omicairn import cross_check as cc_mod
    from omicairn import orchestrator as orch

    orchestrator.run_all(ws, stop_after=PhaseName.CROSS_CHECK)
    monkeypatch.setattr(
        orch, "_inline_post_stage_check",
        lambda phase, stage_dir, ws: cc_mod.CrossCheckResult(
            ok=True, warnings=["synthetic advisory"], errors=[]),
    )
    outcome = orchestrator.run_next_stage(ws)
    assert outcome is not None
    assert outcome.phase == PhaseName.REVIEW
    assert outcome.status == "DONE"
    assert "[cross_check warnings: 1]" in outcome.note


def test_review_stage_inline_check_failure_routes_to_contract_fail(
    dry_run_env, ws, monkeypatch
):
    """When `_inline_post_stage_check` returns `ok=False`, `_run_llm_stage`
    must append 'cross_check: ...' to the reasons list and return
    `status=CONTRACT_FAIL`."""
    from omicairn import cross_check as cc_mod
    from omicairn import orchestrator as orch

    orchestrator.run_all(ws, stop_after=PhaseName.CROSS_CHECK)
    monkeypatch.setattr(
        orch, "_inline_post_stage_check",
        lambda phase, stage_dir, ws: cc_mod.CrossCheckResult(
            ok=False, warnings=[], errors=["synthetic: phantom.py missing"]),
    )
    outcome = orchestrator.run_next_stage(ws)
    assert outcome is not None
    assert outcome.phase == PhaseName.REVIEW
    assert outcome.status == "CONTRACT_FAIL"
    assert "cross_check:" in outcome.note
    assert "phantom.py" in outcome.note


# ---------------------------------------------------------------------------
# FIX 3 (d7 steering bug, 2026-06-10): `omicairn steer` on a DONE workspace must
# actually DISPATCH the steering plan, not silently no-op. This exercises the
# FULL run_next_stage path (not just decide_next_phase) — including
# run_next_stage's own terminal-status gate + the auto-reset-on-steering.
# ---------------------------------------------------------------------------

def test_steer_on_done_workspace_dispatches_stage(dry_run_env, ws):
    """Run a pipeline to DONE, then `omicairn steer` (write a pending plan).
    run_next_stage must reactivate the run and dispatch the steered stage —
    the user must NOT have to hand-flip STATE.md status."""
    orchestrator.run_all(ws, max_stages=20)
    assert state.read(ws.state_path)["status"] == RunStatus.DONE

    # A bare run_next_stage on the done workspace WITHOUT a plan no-ops.
    assert orchestrator.run_next_stage(ws) is None

    # Now simulate `omicairn steer`: a valid pending plan whose step is a real
    # CLAUDE_CODE phase the orchestrator can dispatch in dry-run.
    steering_mod.write_plan(ws.root, steering_mod.SteeringPlan(
        user_request="re-check the sensitivity analysis",
        steps=[steering_mod.SteeringStep(
            phase=PhaseName.REVIEW,
            prompt_overlay="USER STEERING: re-assess the sensitivity arm",
            rationale="user doubts the robustness claim",
        )],
    ))

    outcome = orchestrator.run_next_stage(ws)
    assert outcome is not None, (
        "steer on a DONE workspace must dispatch the plan, not no-op"
    )
    assert outcome.phase == PhaseName.REVIEW
    assert outcome.status == "DONE"
    # The run was auto-reactivated (status flipped off DONE) and the
    # reactivation was journalled.
    s_after = state.read(ws.state_path)
    assert s_after["status"] == RunStatus.IN_PROGRESS
    assert s_after["halted_reason"] is None
    events = (ws.root / "events.jsonl")
    if events.exists():
        assert "steering_reactivated_terminal_run" in events.read_text()


def test_run_next_stage_done_without_plan_still_no_ops(dry_run_env, ws):
    """The preserved-behavior half at the run_next_stage level: a genuinely
    done workspace with no steering plan must stay None and stay DONE."""
    orchestrator.run_all(ws, max_stages=20)
    assert state.read(ws.state_path)["status"] == RunStatus.DONE
    assert orchestrator.run_next_stage(ws) is None
    # Status must be untouched — no spurious reactivation.
    assert state.read(ws.state_path)["status"] == RunStatus.DONE


def test_resume_removes_legacy_manuscript_agents_and_rerenders_claude_md(
    dry_run_env, ws
):
    """Resuming a paper-era workspace drops removed manuscript subagents
    from .claude/agents/ and re-renders a CLAUDE.md that predates the
    no-manuscript rule."""
    from omicairn.constants import NO_MANUSCRIPT_RULE_MARKER

    agents = ws.root / ".claude" / "agents"
    (agents / "section-writer.md").write_text("---\nname: section-writer\n---\n")
    (agents / "peer-reviewer.md").write_text("---\nname: peer-reviewer\n---\n")
    (agents / "analyst.md").unlink()
    ws.claude_md_path.write_text("# legacy CLAUDE.md\n")

    orchestrator.run_all(ws, stop_after=PhaseName.SCOPE)
    assert not (agents / "section-writer.md").exists()
    assert not (agents / "peer-reviewer.md").exists()
    assert (agents / "analyst.md").exists()
    assert NO_MANUSCRIPT_RULE_MARKER in ws.claude_md_path.read_text()
    events = [json.loads(line) for line in
              (ws.root / "events.jsonl").read_text().splitlines() if line]
    removed = [e for e in events
               if e.get("kind") == EventKind.LEGACY_AGENTS_REMOVED]
    assert removed and set(removed[0]["agents"]) == {
        "section-writer.md", "peer-reviewer.md"}
