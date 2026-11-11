"""Anti-loop backstop tests for `_run_all_locked` (FIX B).

A phase that contract-fails IDENTICALLY several times in a row is stuck:
the agent regenerates the same defect every retry, so spending the rest
of `max_iters_per_phase` on byte-identical re-runs is pure waste.

FIX B adds a backstop in the outer retry loop: after
`_IDENTICAL_CONTRACT_FAIL_LIMIT` consecutive identical failures, instead
of retrying the orchestrator halts with a CLEAR reason naming the stuck
contract.

These tests drive `run_all` with a controlled fake `run_next_stage` that
simulates the identical-failure loop, so the backstop logic is exercised
in isolation.
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


# max_iters_per_phase=8 so the backstop (limit=3) preempts WELL BEFORE
# iter exhaustion — this is exactly the d1 shape (8 iters available, the
# loop should stop at 3 identical failures, not burn all 8).
RESEARCH_TEMPLATE = """\
---
research_mode: METHOD
domain: synthetic_smoke_test
topic_slug: pytest-stuck
research_question: |
  Does the anti-loop backstop stop identical contract-fails early?
data:
  location: {data_dir}
  description: synthetic pytest fixture
  cohorts: [a]
  modalities: [tabular]
success_criteria:
  primary_metric: auc
  baseline: null_model
bounds:
  max_iters_per_phase: 8
  max_backtracks: 5
budgets:
  max_phase_wall_minutes: 5
  allow_web: false
  claude_model: claude-sonnet-4-6
  max_phase_budget_usd: 1.0
auto_approve: true
---

# pytest stuck-contract fixture
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
            "pytest-stuck",
            research_md_path=rmd_path,
            workspaces_root=ws_root,
            skip_literature=True,
        )


def _bump_iter(ws: workspace.Workspace, phase: str) -> int:
    """Increment iteration_counts[phase] in STATE.md (mirrors what
    run_next_stage's record_stage_start does) and return the new count."""
    s = state.read(ws.state_path)
    counts = s.setdefault("iteration_counts", {})
    counts[phase] = int(counts.get(phase, 0)) + 1
    state.write(ws.state_path, s)
    return counts[phase]


def _events(ws: workspace.Workspace) -> list[dict]:
    path = ws.root / EVENTS_LOG
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


# ---------------------------------------------------------------------------
# (a) stuck phase → clear halt, not silent iter-cap burn
# ---------------------------------------------------------------------------

def test_identical_contract_fail_without_substantive_outputs_halts_clearly(
    dry_run_env, ws, monkeypatch
):
    """A stage that contract-fails identically (here a REVIEW dir that never
    wrote negative_results.md) must HALT with a clear stuck-contract reason
    at the 3rd identical failure rather than burning all 8 iters silently."""
    stage_idx = stage_mod.next_stage_index(ws.root)
    review_dir = stage_mod.create_stage_dir(
        ws.root, stage_idx, PhaseName.REVIEW
    )
    rel = review_dir.name
    FIXED_NOTE = "contract: missing negative_results.md"

    def fake_run_next_stage(ws_arg, forced_phase=None):
        _bump_iter(ws_arg, PhaseName.REVIEW)
        return orchestrator.StageOutcome(
            stage_index=stage_idx,
            phase=PhaseName.REVIEW,
            stage_dir_rel=rel,
            status="CONTRACT_FAIL",
            attempts=1,
            wall_seconds=0.1,
            decision=None,
            note=FIXED_NOTE,
        )

    monkeypatch.setattr(orchestrator, "run_next_stage", fake_run_next_stage)
    outcomes = orchestrator.run_all(ws, max_stages=20)

    cf = [o for o in outcomes if o.status == "CONTRACT_FAIL"]
    assert len(cf) == 3, (
        f"expected halt at 3 identical failures, NOT 8; got {len(cf)}"
    )
    s = state.read(ws.state_path)
    assert s["status"] == RunStatus.HALTED
    assert "stuck" in (s.get("halted_reason") or "").lower()
    assert PhaseName.REVIEW in (s.get("halted_reason") or "")

    kinds = [e.get("kind") for e in _events(ws)]
    assert "stage_stuck_contract_fail_halt" in kinds
    assert "stuck_contract_forward_advance" not in kinds


# ---------------------------------------------------------------------------
# (b) NON-identical failures keep retrying (no premature backstop)
# ---------------------------------------------------------------------------

def test_distinct_contract_fail_notes_do_not_trigger_backstop(
    dry_run_env, ws, monkeypatch
):
    """The IDENTICAL-streak backstop is for no-progress repeats only. If the
    failure note CHANGES each attempt (the agent is making different mistakes
    — i.e. it IS moving), the streak resets and the SEPARATE contract-retry
    budget (MAX_CONTRACT_RETRIES) governs — NOT the science-iteration cap.
    This guards against the identical-streak backstop firing on a phase that
    is genuinely iterating toward a fix, while still bounding runaway
    contract retries by the contract budget rather than the (larger) science cap."""
    calls = {"n": 0}

    def fake_run_next_stage(ws_arg, forced_phase=None):
        calls["n"] += 1
        _bump_iter(ws_arg, PhaseName.REVIEW)
        # Distinct note every attempt → streak never reaches the limit.
        return orchestrator.StageOutcome(
            stage_index=0,
            phase=PhaseName.REVIEW,
            stage_dir_rel="004_review",
            status="CONTRACT_FAIL",
            attempts=1,
            wall_seconds=0.1,
            decision=None,
            note=f"cross_check: distinct failure #{calls['n']}",
        )

    monkeypatch.setattr(orchestrator, "run_next_stage", fake_run_next_stage)
    outcomes = orchestrator.run_all(ws, max_stages=20)

    from omicairn.constants import MAX_CONTRACT_RETRIES
    cf = [o for o in outcomes if o.status == "CONTRACT_FAIL"]
    # With DISTINCT notes the identical-streak backstop never fires; the run is
    # instead bounded by the contract-retry budget (MAX_CONTRACT_RETRIES=3),
    # NOT the science-iteration cap (8). So exactly MAX_CONTRACT_RETRIES
    # CONTRACT_FAILs then halt via stage_retries_exhausted.
    assert len(cf) == MAX_CONTRACT_RETRIES, (
        f"distinct failures must be bounded by the contract-retry budget "
        f"({MAX_CONTRACT_RETRIES}), NOT the science cap; got {len(cf)}"
    )
    kinds = [e.get("kind") for e in _events(ws)]
    assert "stage_stuck_contract_fail_halt" not in kinds
    assert "stage_retries_exhausted" in kinds
    # And the science-iteration count was NOT burned by these contract
    # retries — every increment was reclassified into the contract budget.
    s = state.read(ws.state_path)
    assert int(s["iteration_counts"][PhaseName.REVIEW]) == 0
    assert int(s["contract_retry_counts"][PhaseName.REVIEW]) == MAX_CONTRACT_RETRIES


# ===========================================================================
# Resume-time finalize of a valid-but-unstamped stage (d5 fix, 2026-06-11)
# ===========================================================================
#
# Root cause the lead found: a deepening EXECUTE that ran 8.9h produced a
# valid results.json + analysis.md + code/main.py + 12 figures + summary.md
# (check_execute PASSES) but the orchestrator died before writing done.flag.
# `StageRef.done` is purely `done.flag` existence, so the next `omicairn run`
# treated the stage as NOT-done, re-ran EXECUTE into empty stages 013/014
# (each contract-failing fast on "missing results.json, ..."), burned iters
# to the cap (8/8) and HALTED — nearly stranding the completed work. The
# identical-failure backstop doesn't cover this: it only halts.
#
# These tests prove BOTH directions of the source-level fix
# (`_finalize_valid_unstamped_stages`):
#   - a valid-but-unstamped execute stage is finalized + advanced (NOT
#     re-run / looped to the cap);
#   - a genuinely-incomplete execute stage (missing results.json) is NOT
#     rescued (still treated as not-done / a real contract fail);
#   - an execute stage with all files but a FAILING substantive checker
#     (fabricated CI) is NOT rescued (the key safety property).

from omicairn import contracts, cross_check, phases, verdict as verdict_mod  # noqa: E402

# results.json + analysis.md shape that makes cross_check.check_execute
# PASS: every comparisons.* CI also appears verbatim in analysis.md.
_VALID_RESULTS = {
    "primary_model": {"auc": 0.82, "ci_95": [0.677, 0.943]},
    "comparisons": {
        "delta_primary_vs_null": {"delta": 0.37, "ci_95": [0.141, 0.594]},
    },
}
_VALID_ANALYSIS = (
    "# Analysis\n\n"
    "AUC 0.82 (CI [0.677, 0.943]).\n"
    "Primary vs null delta-AUC=0.37 CI [0.141, 0.594].\n"
)


def _make_execute_stage(
    ws: workspace.Workspace, *, results: dict, analysis: str, done: bool = False
) -> stage_mod.StageRef:
    """Create a fresh NNN_execute stage dir on the workspace with the given
    results.json + analysis.md plus every other EXECUTE required output
    (figures/legends.md, code/main.py, summary.md). `done=False` omits
    done.flag — the crash-after-work case. Returns its StageRef."""
    idx = stage_mod.next_stage_index(ws.root)
    sdir = stage_mod.create_stage_dir(ws.root, idx, PhaseName.EXECUTE)
    (sdir / "results.json").write_text(json.dumps(results))
    (sdir / "analysis.md").write_text(analysis)
    (sdir / "figures").mkdir(parents=True, exist_ok=True)
    (sdir / "figures" / "legends.md").write_text(
        "# Figure legends\n\nFigure 1. Primary ROC across cohorts.\n"
    )
    (sdir / "code").mkdir(parents=True, exist_ok=True)
    (sdir / "code" / "main.py").write_text(
        "# analysis entrypoint\nprint('ran the real analysis')\n"
    )
    (sdir / "summary.md").write_text(
        "Ran primary analysis on real data; H1 supported (AUC 0.82).\n"
    )
    if done:
        verdict_mod.write_done_flag(sdir)
    return stage_mod.StageRef(
        index=idx, phase=PhaseName.EXECUTE, path=sdir, rel_path=sdir.name
    )


def test_valid_but_unstamped_execute_is_finalized_and_advances(ws):
    """A substantively-COMPLETE execute stage missing ONLY done.flag
    (check_execute passes, every required output present) is finalized by
    the resume sweep: done.flag written, recorded DONE + PROCEED, and the
    pipeline advances FORWARD (decide_next_phase → cross_check) rather than
    re-running EXECUTE into empty stages / looping to the iter cap."""
    st = _make_execute_stage(
        ws, results=_VALID_RESULTS, analysis=_VALID_ANALYSIS, done=False
    )
    # Precondition: the file-presence contract reports ONLY done.flag
    # missing (this is exactly the d5 crash-after-work signature).
    contract = contracts.verify(phases.EXECUTE, st.path)
    assert not contract.ok and contract.missing == ["done.flag"], contract.summary()
    assert cross_check.check_execute(st.path).ok, "fixture must be check_execute-valid"
    assert not st.done

    n = orchestrator._finalize_valid_unstamped_stages(ws)
    assert n == 1, "the valid-but-unstamped execute stage must be finalized"

    # done.flag now exists → the stage is DONE; a re-read StageRef agrees.
    refreshed = [s for s in stage_mod.list_stages(ws.root) if s.index == st.index][0]
    assert refreshed.done
    v = verdict_mod.read_verdict(st.path)
    assert v is not None and v.decision == Decision.PROCEED

    # decide_next_phase now advances FORWARD from the recovered execute
    # stage to cross_check — NOT back into another execute attempt.
    assert orchestrator.decide_next_phase(ws) == PhaseName.CROSS_CHECK

    kinds = [e.get("kind") for e in _events(ws)]
    assert "finalized_valid_unstamped_stage" in kinds
    fin = [e for e in _events(ws)
           if e.get("kind") == EventKind.FINALIZED_VALID_UNSTAMPED_STAGE][0]
    assert fin["phase"] == PhaseName.EXECUTE
    assert fin["decision"] == Decision.PROCEED

    # Idempotent: a second sweep finalizes nothing (no double-stamp).
    assert orchestrator._finalize_valid_unstamped_stages(ws) == 0


def test_incomplete_execute_missing_results_is_not_rescued(ws):
    """A genuinely-incomplete execute stage (results.json absent) must NOT
    be rescued by the finalize sweep — it stays not-done so the normal
    contract-fail / retry / halt machinery still governs it."""
    idx = stage_mod.next_stage_index(ws.root)
    sdir = stage_mod.create_stage_dir(ws.root, idx, PhaseName.EXECUTE)
    # Everything BUT results.json (and done.flag). check_execute returns
    # trivially ok here (missing artifacts are a contract concern), so the
    # ONLY thing standing between this and a false rescue is the
    # contract-modulo-done.flag guard — assert that guard holds.
    (sdir / "analysis.md").write_text(_VALID_ANALYSIS)
    (sdir / "figures").mkdir(parents=True, exist_ok=True)
    (sdir / "figures" / "legends.md").write_text("# legends\n\nFig 1.\n")
    (sdir / "code").mkdir(parents=True, exist_ok=True)
    (sdir / "code" / "main.py").write_text("# stub\nprint('x')\n")
    (sdir / "summary.md").write_text("Partial run; results.json never written.\n")

    n = orchestrator._finalize_valid_unstamped_stages(ws)
    assert n == 0, "a stage missing results.json must NOT be finalized"
    refreshed = [s for s in stage_mod.list_stages(ws.root) if s.index == idx][0]
    assert not refreshed.done
    assert not (sdir / "done.flag").exists()
    assert verdict_mod.read_verdict(sdir) is None
    kinds = [e.get("kind") for e in _events(ws)]
    assert "finalized_valid_unstamped_stage" not in kinds


def test_execute_with_failing_substantive_checker_is_not_rescued(ws):
    """SAFETY PROPERTY: a stage with all required FILES present but a
    FAILING substantive checker (fabricated/missing comparison CI) must NOT
    be finalized. Files-present alone is insufficient — the semantic
    check_execute must also pass, else we'd stamp DONE on bad science."""
    bad_results = {
        "primary_model": {"auc": 0.82, "ci_95": [0.677, 0.943]},
        "comparisons": {
            # This delta CI is a load-bearing claim but is ABSENT from
            # analysis.md below → check_execute raises an ERROR.
            "delta_primary_vs_null": {"delta": 0.37, "ci_95": [0.141, 0.594]},
        },
    }
    analysis_missing_delta_ci = "# Analysis\n\nAUC 0.82 (CI [0.677, 0.943]).\n"
    st = _make_execute_stage(
        ws, results=bad_results, analysis=analysis_missing_delta_ci, done=False
    )
    # Files-present contract passes modulo done.flag …
    assert orchestrator._contract_ok_modulo_done_flag(phases.EXECUTE, st.path)
    # … but the substantive checker FAILS (the gate that blocks the rescue).
    assert not cross_check.check_execute(st.path).ok

    n = orchestrator._finalize_valid_unstamped_stages(ws)
    assert n == 0, "a stage whose substantive checker fails must NOT be rescued"
    refreshed = [s for s in stage_mod.list_stages(ws.root) if s.index == st.index][0]
    assert not refreshed.done
    assert not (st.path / "done.flag").exists()


def test_finalize_sweep_runs_inside_run_next_stage_before_routing(
    dry_run_env, ws, monkeypatch
):
    """End-to-end: the sweep is wired into run_next_stage BEFORE
    decide_next_phase, so a valid-but-unstamped execute stage on disk at
    resume causes the NEXT dispatched stage to be cross_check (forward
    motion) — NOT a re-run execute. We stub _run_llm_stage / the
    cross_check runner so no real Claude session is needed; we only assert
    WHICH phase the orchestrator chose to dispatch."""
    _make_execute_stage(
        ws, results=_VALID_RESULTS, analysis=_VALID_ANALYSIS, done=False
    )
    dispatched: list[str] = []

    def fake_cross_check_stage(ws_arg, stage_index, s):
        dispatched.append(PhaseName.CROSS_CHECK)
        # Return a terminal-ish DONE so run_all stops after one dispatch.
        sdir = stage_mod.create_stage_dir(ws_arg.root, stage_index, PhaseName.CROSS_CHECK)
        verdict_mod.write_verdict(sdir, verdict_mod.done(reason="stubbed terminal"))
        verdict_mod.write_done_flag(sdir)
        s2 = state.read(ws_arg.state_path)
        state.record_stage_done(s2, stage_index, PhaseName.CROSS_CHECK)
        state.record_decision(s2, Decision.DONE)
        state.write(ws_arg.state_path, s2)
        return orchestrator.StageOutcome(
            stage_index=stage_index, phase=PhaseName.CROSS_CHECK,
            stage_dir_rel=sdir.name, status="DONE", attempts=1,
            wall_seconds=0.0, decision=Decision.DONE, note="stub",
        )

    def fake_llm_stage(ws_arg, stage_index, phase, s):
        dispatched.append(phase.name)
        raise AssertionError(
            f"orchestrator re-dispatched an LLM phase {phase.name!r}; "
            "it should have advanced to cross_check from the finalized "
            "execute stage instead of re-running execute"
        )

    monkeypatch.setattr(orchestrator, "_run_cross_check_stage", fake_cross_check_stage)
    monkeypatch.setattr(orchestrator, "_run_llm_stage", fake_llm_stage)

    outcomes = orchestrator.run_all(ws, max_stages=5)

    # The first (and only) dispatched stage was cross_check — proving the
    # finalize sweep ran first and decide_next_phase advanced forward.
    assert dispatched and dispatched[0] == PhaseName.CROSS_CHECK, dispatched
    assert PhaseName.EXECUTE not in dispatched
    kinds = [e.get("kind") for e in _events(ws)]
    assert "finalized_valid_unstamped_stage" in kinds
    # No empty-retry cascade markers.
    assert "stage_contract_fail" not in kinds
    assert "stage_retries_exhausted" not in kinds
    assert "stage_stuck_contract_fail_halt" not in kinds
    assert any(o.phase == PhaseName.CROSS_CHECK for o in outcomes)
