"""Error-path tests for `orchestrator._run_llm_stage`.

The three non-DONE outcome branches in ``_run_llm_stage`` were
previously uncovered by any test:

    if result.returncode == -2:             # CLI_MISSING
    if result.timed_out:                    # TIMEOUT
    if not contract.ok or not cc.ok:        # CONTRACT_FAIL

The default dry-run path always returns ``returncode=0`` and writes
a full contract, so it exercises none of them. A real failure on any
of these paths currently surfaces only in the middle of a real
``omicairn run`` — far too late to be useful as a harness signal.

These tests monkeypatch ``omicairn.orchestrator.claude_run`` with fake
return values that force each branch deterministically, and pin:

1. The returned ``StageOutcome.status`` string.
2. The state transitions (e.g. status → HALTED for CLI_MISSING).
3. The events.jsonl entries (``stage_timeout``,
   ``stage_contract_fail``) the operator relies on for post-hoc
   inspection.
4. That ``run_all`` correctly breaks out of the outer loop on any
   non-DONE outcome.

All tests use ``OMICAIRN_DRY_RUN=1`` for the env-driven workspace init,
but then override ``orchestrator.claude_run`` directly so the stage
loop takes the real (non-dry-run) branches through the outcome
handler.
"""
from __future__ import annotations

import json
import os
import pathlib
import tempfile
from typing import Iterator

import pytest

from omicairn import claude_runner, orchestrator, state, workspace
from omicairn.constants import (
    ENV_DRY_RUN,
    EVENTS_LOG,
    EventKind,
    PhaseName,
    RunStatus,
)


RESEARCH_TEMPLATE = """\
---
research_mode: METHOD
domain: synthetic_smoke_test
topic_slug: pytest-errors
research_question: |
  Do error paths behave as specified?
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

# pytest error-paths fixture
"""


@pytest.fixture
def dry_run_env() -> Iterator[None]:
    prev = os.environ.get(ENV_DRY_RUN)
    os.environ[ENV_DRY_RUN] = "1"
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop(ENV_DRY_RUN, None)
        else:
            os.environ[ENV_DRY_RUN] = prev


@pytest.fixture
def ws(dry_run_env) -> Iterator[workspace.Workspace]:
    """Fresh workspace with a valid RESEARCH.md. The dry_run_env
    fixture is only needed for workspace.init(skip_literature=True) —
    the individual tests disable it before invoking the stage loop
    so the monkeypatched claude_run actually gets called instead of
    the dry-run short-circuit inside claude_runner.run."""
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
            "pytest-errors",
            research_md_path=rmd_path,
            workspaces_root=ws_root,
            skip_literature=True,
        )


def _fake_result(**kwargs) -> claude_runner.ClaudeResult:
    """Shortcut: build a ClaudeResult with sensible defaults."""
    defaults = dict(
        returncode=0,
        wall_seconds=0.1,
        stdout_path=pathlib.Path("/tmp/fake.log"),
        timed_out=False,
        cmd=["<fake>"],
        dry_run=False,
    )
    defaults.update(kwargs)
    return claude_runner.ClaudeResult(**defaults)


# ---------------------------------------------------------------------------
# TIMEOUT branch
# ---------------------------------------------------------------------------

def test_llm_stage_timeout_returns_timeout_outcome(ws, monkeypatch):
    """If claude_run returns ``timed_out=True``, the stage outcome
    must be status=TIMEOUT and a ``stage_timeout`` event must be
    emitted into events.jsonl with the phase's timeout_seconds."""
    # Disable dry-run so the real claude_run path is exercised (and
    # then monkeypatched below).
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)

    def fake_run(**kwargs):
        return _fake_result(returncode=-1, timed_out=True)

    monkeypatch.setattr(orchestrator, "claude_run", fake_run)

    outcome = orchestrator.run_next_stage(ws)
    assert outcome is not None
    assert outcome.status == "TIMEOUT"
    assert outcome.phase == PhaseName.SCOPE  # first stage is scope
    assert outcome.decision is None
    assert "timed out" in outcome.note

    # Event was written.
    events_path = ws.root / EVENTS_LOG
    events = [
        json.loads(l) for l in events_path.read_text().splitlines() if l
    ]
    timeout_events = [e for e in events if e.get("kind") == EventKind.STAGE_TIMEOUT]
    assert len(timeout_events) == 1
    ev = timeout_events[0]
    assert ev["phase"] == PhaseName.SCOPE
    # The event records the effective timeout — exactly the RESEARCH.md
    # max_phase_wall_minutes value when present. The fixture sets
    # max_phase_wall_minutes=5 (= 300s); we honor that verbatim, allowing
    # the user to either raise OR lower the phase's built-in default.
    #
    # Prior to commit fixing parkinson-real-run report §3.1 we clamped
    # via `min(phase.default, user)` — which silently prevented RESEARCH.md
    # from RAISING the ceiling. Now the RESEARCH.md value is authoritative.
    assert ev["seconds"] == 5 * 60


def test_run_all_breaks_out_on_timeout(ws, monkeypatch):
    """`run_all` must NOT proceed to plan after scope times out. The
    outer loop short-circuits on any non-DONE outcome so the operator
    can inspect the failure before another stage is created."""
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)
    monkeypatch.setattr(
        orchestrator, "claude_run",
        lambda **k: _fake_result(returncode=-1, timed_out=True),
    )

    outcomes = orchestrator.run_all(ws, max_stages=10)
    assert len(outcomes) == 1, (
        f"run_all should stop immediately after a TIMEOUT; got {len(outcomes)} stages"
    )
    assert outcomes[0].status == "TIMEOUT"
    assert outcomes[0].phase == PhaseName.SCOPE


# ---------------------------------------------------------------------------
# CLI_MISSING branch (returncode == -2)
# ---------------------------------------------------------------------------

def test_llm_stage_cli_missing_halts_run(ws, monkeypatch):
    """If claude_run returns ``returncode=-2`` (FileNotFoundError for
    the claude CLI), the orchestrator sets STATE.md status to HALTED
    and returns status=CLI_MISSING. This is a terminal condition —
    the pipeline cannot progress without claude on PATH."""
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)
    monkeypatch.setattr(
        orchestrator, "claude_run",
        lambda **k: _fake_result(returncode=-2, timed_out=False),
    )

    outcome = orchestrator.run_next_stage(ws)
    assert outcome is not None
    assert outcome.status == "CLI_MISSING"
    assert outcome.decision is None
    assert "claude CLI not found" in outcome.note

    # State must transition to HALTED with a descriptive reason.
    s = state.read(ws.state_path)
    assert s["status"] == RunStatus.HALTED
    assert "claude CLI not found" in (s.get("halted_reason") or "")


def test_cli_missing_blocks_further_stages(ws, monkeypatch):
    """After CLI_MISSING halts the run, a follow-up run_next_stage
    must return None (because status is now HALTED), not retry."""
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)
    monkeypatch.setattr(
        orchestrator, "claude_run",
        lambda **k: _fake_result(returncode=-2, timed_out=False),
    )
    first = orchestrator.run_next_stage(ws)
    assert first is not None
    assert first.status == "CLI_MISSING"

    # The second call must see HALTED state and refuse to start.
    second = orchestrator.run_next_stage(ws)
    assert second is None


# ---------------------------------------------------------------------------
# CONTRACT_FAIL branch
# ---------------------------------------------------------------------------

def test_llm_stage_contract_fail_on_empty_output(ws, monkeypatch):
    """If claude_run returns rc=0 but writes no contract files, the
    stage must fail with status=CONTRACT_FAIL and emit a
    ``stage_contract_fail`` event with the missing-file reasons."""
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)
    # Fake run writes nothing to stage_dir — contracts.verify will
    # see all required_outputs missing.
    monkeypatch.setattr(
        orchestrator, "claude_run",
        lambda **k: _fake_result(returncode=0, timed_out=False),
    )

    outcome = orchestrator.run_next_stage(ws)
    assert outcome is not None
    assert outcome.status == "CONTRACT_FAIL"
    assert outcome.phase == PhaseName.SCOPE
    assert outcome.decision is None
    assert "missing" in outcome.note or "contract" in outcome.note

    # Event was emitted.
    events_path = ws.root / EVENTS_LOG
    events = [
        json.loads(l) for l in events_path.read_text().splitlines() if l
    ]
    fail_events = [e for e in events if e.get("kind") == EventKind.STAGE_CONTRACT_FAIL]
    assert len(fail_events) == 1
    assert fail_events[0]["phase"] == PhaseName.SCOPE
    assert "missing" in fail_events[0]["reasons"]


def test_llm_stage_contract_fail_on_trivial_output(ws, monkeypatch):
    """A contract file that exists but is <32 bytes must fail the
    contract (treated as a placeholder stub). Pins the
    _TRIVIAL_SIZE_BYTES threshold from contracts.py by exercising it
    through the real orchestrator path — not a contracts unit test."""
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)

    def fake_run(*, stage_dir, phase_name, **kwargs):
        # Write trivially-tiny files for every required_output so
        # they exist but fail the size threshold.
        from omicairn import phases
        phase = phases.get(phase_name)
        stage_dir.mkdir(parents=True, exist_ok=True)
        for rel in phase.required_outputs:
            p = stage_dir / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            if rel.endswith(".flag"):
                # .flag files are allowed to be 1 byte; write
                # something non-empty so that branch passes and the
                # trivial check fires on the non-flag files.
                p.write_text("1\n")
            else:
                p.write_text("x")  # 1 byte — well under 32
        return _fake_result(returncode=0, timed_out=False)

    monkeypatch.setattr(orchestrator, "claude_run", fake_run)
    outcome = orchestrator.run_next_stage(ws)
    assert outcome is not None
    assert outcome.status == "CONTRACT_FAIL"
    assert "trivial" in outcome.note


def test_run_all_retries_on_contract_fail_until_exhausted(ws, monkeypatch):
    """run_all must RETRY CONTRACT_FAIL within max_iters_per_phase.

    Earlier behaviour halted on the first CONTRACT_FAIL. The parkinson
    15-case suite (May 2026) lost 5 of 15 papers to that halt because
    a single transient claude -p empty-output failure killed the entire
    pipeline. Now we retry — and only halt after `max_iters_per_phase`
    has been exhausted on the same phase.

    A persistent empty-output CONTRACT_FAIL produces an IDENTICAL contract
    failure note each attempt (same missing-files summary), so it bounds at
    `_IDENTICAL_CONTRACT_FAIL_LIMIT` (3) consecutive identical no-progress
    failures: exactly 3 CONTRACT_FAIL outcomes, then a stuck-contract halt.
    These contract retries are reclassified OUT of the science cap into the
    contract-retry budget, so iteration_counts is NOT burned.
    """
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)
    monkeypatch.setattr(
        orchestrator, "claude_run",
        lambda **k: _fake_result(returncode=0, timed_out=False),
    )
    outcomes = orchestrator.run_all(ws, max_stages=10)
    # 3 attempts run (identical-streak limit / contract budget both 3).
    assert len(outcomes) == 3, (
        f"expected 3 CONTRACT_FAIL attempts before halt; got {len(outcomes)}"
    )
    assert all(o.status == "CONTRACT_FAIL" for o in outcomes)
    # The retry events must be present in events.jsonl.
    events_path = ws.root / EVENTS_LOG
    events = [json.loads(l) for l in events_path.read_text().splitlines() if l]
    retry_events = [e for e in events if e.get("kind") == EventKind.STAGE_RETRY_AFTER_CONTRACT_FAIL]
    # 2 retries between 3 CONTRACT_FAILs (the first one is the
    # original attempt; the subsequent 2 are retries).
    assert len(retry_events) == 2, (
        f"expected 2 retry events; got {len(retry_events)}"
    )
    # Identical empty-output failures route through the stuck-contract halt
    # (the more-specific classification for byte-identical no-progress fails).
    halt = [e for e in events if e.get("kind") == EventKind.STAGE_STUCK_CONTRACT_FAIL_HALT]
    assert len(halt) == 1
    assert halt[0]["phase"] == PhaseName.SCOPE
    assert halt[0]["iterations_used"] == 3
    # Reclassified: the science-iteration count was NOT burned; the 3 fails
    # are tracked in the separate contract-retry budget instead.
    s_after = state.read(ws.state_path)
    assert int(s_after["iteration_counts"][PhaseName.SCOPE]) == 0
    assert int(s_after["contract_retry_counts"][PhaseName.SCOPE]) == 3


def test_run_all_retries_on_contract_fail_then_succeeds(ws, monkeypatch):
    """A CONTRACT_FAIL followed by a successful retry must produce both
    outcomes and let the pipeline continue past the previously-failing
    phase.

    Iter 1 returns claude_run with returncode=0 + empty contract →
    CONTRACT_FAIL. Iter 2 writes the full contract → DONE. The outer
    loop should then proceed to the next phase (PLAN).
    """
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)
    call_count = {"n": 0}

    def fake_run(**kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # First attempt: no contract files written → CONTRACT_FAIL
            return _fake_result(returncode=0, timed_out=False)
        # Subsequent: write the contract artefacts so the stage
        # passes (and any further phases also pass).
        from omicairn import phases
        phase = phases.get(kwargs["phase_name"])
        sd = kwargs["stage_dir"]
        sd.mkdir(parents=True, exist_ok=True)
        for rel in phase.required_outputs:
            p = sd / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            if rel.endswith(".flag"):
                p.write_text("1\n")
            else:
                p.write_text("x" * 200)
        return _fake_result(returncode=0, timed_out=False)

    monkeypatch.setattr(orchestrator, "claude_run", fake_run)
    outcomes = orchestrator.run_all(ws, max_stages=3)
    # First outcome CONTRACT_FAIL (the empty iter 1).
    assert outcomes[0].status == "CONTRACT_FAIL"
    # Then a retry that succeeds at SCOPE; then PLAN starts.
    assert len(outcomes) >= 2
    assert outcomes[1].status == "DONE"
    assert outcomes[1].phase == PhaseName.SCOPE
    # Retry event present.
    events_path = ws.root / EVENTS_LOG
    events = [json.loads(l) for l in events_path.read_text().splitlines() if l]
    retry_events = [e for e in events if e.get("kind") == EventKind.STAGE_RETRY_AFTER_CONTRACT_FAIL]
    assert len(retry_events) == 1


# ---------------------------------------------------------------------------
# Bounds-exceeded HALTED path from run_next_stage
# ---------------------------------------------------------------------------

def test_bounds_exceeded_halts_before_stage_creation(ws, monkeypatch):
    """If STATE.md already says scope has been attempted max_iters
    times, the orchestrator must refuse to create another scope stage
    and return a HALTED outcome WITHOUT calling claude_run at all.

    This is the pre-stage bounds check — it must run before any work
    so a genuinely-exhausted phase never burns a fresh stage dir."""
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)

    # Make claude_run blow up if it's called — the bounds check must
    # short-circuit before we get there.
    def must_not_be_called(**kwargs):
        raise AssertionError(
            "claude_run was called even though bounds were exceeded"
        )
    monkeypatch.setattr(orchestrator, "claude_run", must_not_be_called)

    # Saturate scope's iteration count to the max_iters bound.
    s = state.read(ws.state_path)
    s["iteration_counts"][PhaseName.SCOPE] = s["bounds"]["max_iters_per_phase"]
    state.write(ws.state_path, s)

    outcome = orchestrator.run_next_stage(ws)
    assert outcome is not None
    assert outcome.status == "HALTED"
    assert outcome.stage_dir_rel == ""  # no stage dir was created
    # No claude_run call happened (the asserting fake would have raised).

    # State transitioned to HALTED with a bounds-related reason.
    s_after = state.read(ws.state_path)
    assert s_after["status"] == RunStatus.HALTED
    reason = s_after.get("halted_reason") or ""
    assert "bound" in reason.lower() or "iter" in reason.lower()

    # A halted_bounds event was emitted.
    events_path = ws.root / EVENTS_LOG
    events = [
        json.loads(l) for l in events_path.read_text().splitlines() if l
    ]
    bounds_events = [e for e in events if e.get("kind") == EventKind.HALTED_BOUNDS]
    assert len(bounds_events) == 1
    assert bounds_events[0]["phase"] == PhaseName.SCOPE


# ---------------------------------------------------------------------------
# SESSION_LIMIT branch — Claude Code account-level 5-hour cap
# ---------------------------------------------------------------------------

def test_llm_stage_session_limit_returns_session_limit_outcome(ws, monkeypatch):
    """If claude_run returns rc=0 but session_cap is populated (the
    Claude Code 5-hour usage cap), the orchestrator must:

    1. Return status=SESSION_LIMIT (not CONTRACT_FAIL),
    2. Emit a ``claude_session_limit`` event with reset hint,
    3. Leave run status at IN_PROGRESS so a later resume works.
    """
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)
    monkeypatch.setattr(
        orchestrator, "claude_run",
        lambda **k: _fake_result(
            returncode=0,
            session_cap={"reset_text": "6am", "reset_tz": "Etc/UTC"},
        ),
    )

    outcome = orchestrator.run_next_stage(ws)
    assert outcome is not None
    assert outcome.status == "SESSION_LIMIT"
    assert outcome.phase == PhaseName.SCOPE
    assert outcome.decision is None
    assert "session limit" in outcome.note.lower()
    assert "6am" in outcome.note

    # State stays in_progress so resume creates the next stage cleanly.
    # halted_reason is intentionally NOT set — this is a pause, not a
    # halt; events.jsonl is the source of truth.
    s = state.read(ws.state_path)
    assert s["status"] == RunStatus.IN_PROGRESS
    assert s.get("halted_reason") in (None, "")
    # paused_reason surfaces the cap so `omicairn status` can show why the
    # run is paused even though status is still in_progress.
    assert "claude_session_limit" in (s.get("paused_reason") or "")
    assert "6am" in (s.get("paused_reason") or "")

    # Event was emitted.
    events_path = ws.root / EVENTS_LOG
    events = [
        json.loads(l) for l in events_path.read_text().splitlines() if l
    ]
    cap_events = [e for e in events if e.get("kind") == EventKind.CLAUDE_SESSION_LIMIT]
    assert len(cap_events) == 1
    ev = cap_events[0]
    assert ev["phase"] == PhaseName.SCOPE
    assert ev["reset_text"] == "6am"
    assert ev["reset_tz"] == "Etc/UTC"


def test_run_all_breaks_out_on_session_limit(ws, monkeypatch):
    """run_all must stop on SESSION_LIMIT so a wrapper can schedule the
    resume rather than burn more attempts inside the same window."""
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)
    monkeypatch.setattr(
        orchestrator, "claude_run",
        lambda **k: _fake_result(
            returncode=0,
            session_cap={"reset_text": None, "reset_tz": None},
        ),
    )
    outcomes = orchestrator.run_all(ws, max_stages=10)
    assert len(outcomes) == 1
    assert outcomes[0].status == "SESSION_LIMIT"


def test_paused_reason_cleared_on_next_stage_start(ws, monkeypatch):
    """After SESSION_LIMIT sets paused_reason, a subsequent successful
    stage start (record_stage_start) must clear it. Otherwise `omicairn
    status` would show a stale pause marker after the run resumed."""
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)

    # First call: hit the session cap, paused_reason is set.
    monkeypatch.setattr(
        orchestrator, "claude_run",
        lambda **k: _fake_result(
            returncode=0,
            session_cap={"reset_text": "6am", "reset_tz": "Etc/UTC"},
        ),
    )
    outcome1 = orchestrator.run_next_stage(ws)
    assert outcome1.status == "SESSION_LIMIT"
    s_paused = state.read(ws.state_path)
    assert "claude_session_limit" in (s_paused.get("paused_reason") or "")

    # Second call: write valid contract outputs so the stage completes
    # cleanly. record_stage_start must clear paused_reason.
    def fake_run_success(*, stage_dir, phase_name, **kwargs):
        from omicairn import phases
        phase = phases.get(phase_name)
        stage_dir.mkdir(parents=True, exist_ok=True)
        for rel in phase.required_outputs:
            p = stage_dir / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            if rel.endswith(".flag"):
                p.write_text("1\n")
            else:
                p.write_text("x" * 200)  # easily clears trivial-size threshold
        return _fake_result(returncode=0)

    monkeypatch.setattr(orchestrator, "claude_run", fake_run_success)
    orchestrator.run_next_stage(ws)
    s_after = state.read(ws.state_path)
    assert s_after.get("paused_reason") in (None, ""), (
        f"paused_reason should be cleared on next stage start; got {s_after.get('paused_reason')!r}"
    )


# ---------------------------------------------------------------------------
# Wall-time wiring (parkinson-smoke Bug 1)
# ---------------------------------------------------------------------------

_RESEARCH_TEMPLATE_TIGHT_WALL = """\
---
research_mode: METHOD
domain: synthetic_smoke_test
topic_slug: pytest-wall
research_question: |
  Does max_phase_wall_minutes propagate to claude_runner?
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
  max_phase_wall_minutes: 1
  max_total_wall_minutes: 2
  allow_web: false
  claude_model: claude-sonnet-4-6
  max_phase_budget_usd: 1.0
auto_approve: true
---

# pytest wall-budget fixture
"""


def test_max_phase_wall_minutes_propagates_as_timeout_seconds(
    monkeypatch, tmp_path
):
    """RESEARCH.md ``max_phase_wall_minutes`` must flow through to
    claude_runner.run as ``timeout_seconds = N * 60`` when tighter
    than the phase default. Before parkinson-smoke Bug 1 this field
    was parsed but discarded — only ``phase.timeout_seconds`` was
    propagated, so wall budgets in RESEARCH.md were silently ignored.
    """
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "dummy.csv").write_text("x,y\n1,1\n")
    rmd_path = tmp_path / "RESEARCH.md"
    rmd_path.write_text(_RESEARCH_TEMPLATE_TIGHT_WALL.format(data_dir=data_dir))
    ws_root = tmp_path / "workspaces"
    ws_root.mkdir()
    monkeypatch.setenv(ENV_DRY_RUN, "1")
    ws = workspace.init(
        "pytest-wall",
        research_md_path=rmd_path,
        workspaces_root=ws_root,
        skip_literature=True,
    )
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)

    captured: dict = {}

    def fake_run(**kwargs):
        captured.update(kwargs)
        # Write the contract artefacts so the stage passes
        # contract verification and the orchestrator returns DONE.
        from omicairn import phases
        phase = phases.get(kwargs["phase_name"])
        sd = kwargs["stage_dir"]
        sd.mkdir(parents=True, exist_ok=True)
        for rel in phase.required_outputs:
            p = sd / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            if rel.endswith(".flag"):
                p.write_text("1\n")
            else:
                p.write_text("x" * 200)
        return _fake_result(returncode=0)

    monkeypatch.setattr(orchestrator, "claude_run", fake_run)

    outcome = orchestrator.run_next_stage(ws)
    assert outcome is not None
    # The fake_run captured kwargs include the timeout_seconds passed
    # to claude_runner.run. With max_phase_wall_minutes=1 the user is
    # asking for a TIGHT bound; we honor it verbatim (= 60s).
    assert captured["timeout_seconds"] == 60, (
        f"expected timeout_seconds=60 (1 min from RESEARCH.md); "
        f"got {captured.get('timeout_seconds')!r}"
    )


_RESEARCH_TEMPLATE_LOOSE_WALL = """\
---
research_mode: METHOD
domain: synthetic_smoke_test
topic_slug: pytest-loose-wall
research_question: |
  Does max_phase_wall_minutes override phase.default_timeout
  when the user wants MORE time (not less)?
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
  max_phase_wall_minutes: 240
  max_total_wall_minutes: 1440
  allow_web: false
  claude_model: claude-sonnet-4-6
  max_phase_budget_usd: 1.0
auto_approve: true
---
"""


def test_max_phase_wall_minutes_can_raise_above_phase_default(
    monkeypatch, tmp_path
):
    """RESEARCH.md ``max_phase_wall_minutes`` must be honored even when it
    is LARGER than the phase's built-in default. The parkinson-real-run
    report §3.1 documented that every EXECUTE phase hit `stage_timeout:
    3600s` despite RESEARCH.md declaring 240*60=14400s — because the
    earlier fix clamped via ``min(phase.default, user)``. This regression
    test pins the corrected behavior: ``user`` wins when present.
    """
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "dummy.csv").write_text("x,y\n1,1\n")
    rmd_path = tmp_path / "RESEARCH.md"
    rmd_path.write_text(
        _RESEARCH_TEMPLATE_LOOSE_WALL.format(data_dir=data_dir)
    )
    ws_root = tmp_path / "workspaces"
    ws_root.mkdir()
    monkeypatch.setenv(ENV_DRY_RUN, "1")
    ws = workspace.init(
        "pytest-loose-wall",
        research_md_path=rmd_path,
        workspaces_root=ws_root,
        skip_literature=True,
    )
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)

    captured: dict = {}

    def fake_run(**kwargs):
        captured.update(kwargs)
        from omicairn import phases
        phase = phases.get(kwargs["phase_name"])
        sd = kwargs["stage_dir"]
        sd.mkdir(parents=True, exist_ok=True)
        for rel in phase.required_outputs:
            p = sd / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            if rel.endswith(".flag"):
                p.write_text("1\n")
            else:
                p.write_text("x" * 200)
        return _fake_result(returncode=0)

    monkeypatch.setattr(orchestrator, "claude_run", fake_run)

    outcome = orchestrator.run_next_stage(ws)
    assert outcome is not None
    # 240 minutes = 14_400 s. With the old clamp this would have been
    # min(phase.default, 14400) = phase.default (e.g. 2700s for SCOPE).
    # With the corrected logic the user value flows through verbatim.
    from omicairn import phases
    assert captured["timeout_seconds"] == 240 * 60, (
        f"expected timeout_seconds=14400 (RESEARCH.md raise); "
        f"got {captured.get('timeout_seconds')!r} "
        f"(SCOPE default is {phases.SCOPE.timeout_seconds})"
    )


def test_per_phase_budget_overrides_case_wide_via_omicairn_config_yaml(
    monkeypatch, tmp_path
):
    """omicairn_config.yaml `budgets.max_phase_wall_minutes_by_phase` must
    take precedence over the case-wide `max_phase_wall_minutes` for
    the named phase only. Other phases keep the case-wide value.

    Parkinson asyn-propagation timed out on a 4 h global EXECUTE
    budget; this test pins that a omicairn_config.yaml override can give
    EXECUTE 6 h while SCOPE/PLAN/etc inherit 1 h.
    """
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "dummy.csv").write_text("x,y\n1,1\n")
    rmd_path = tmp_path / "RESEARCH.md"
    rmd_path.write_text(_RESEARCH_TEMPLATE_TIGHT_WALL.format(data_dir=data_dir))
    # Sibling omicairn_config.yaml: raise execute to 360 min (6 h),
    # leave others at the 1 min case-wide default.
    (tmp_path / "omicairn_config.yaml").write_text(
        "budgets:\n"
        "  max_phase_wall_minutes_by_phase:\n"
        "    execute: 360\n"
    )
    ws_root = tmp_path / "workspaces"
    ws_root.mkdir()
    monkeypatch.setenv(ENV_DRY_RUN, "1")
    ws = workspace.init(
        "pytest-perphase",
        research_md_path=rmd_path,
        workspaces_root=ws_root,
        skip_literature=True,
    )
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)

    captured_by_phase: dict[str, int] = {}

    def fake_run(**kwargs):
        captured_by_phase[kwargs["phase_name"]] = kwargs["timeout_seconds"]
        # Write contract files so each stage passes.
        from omicairn import phases
        phase = phases.get(kwargs["phase_name"])
        sd = kwargs["stage_dir"]
        sd.mkdir(parents=True, exist_ok=True)
        for rel in phase.required_outputs:
            p = sd / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            if rel.endswith(".flag"):
                p.write_text("1\n")
            else:
                p.write_text("x" * 200)
        return _fake_result(returncode=0)

    monkeypatch.setattr(orchestrator, "claude_run", fake_run)

    # Run all stages until we've seen both scope and execute.
    orchestrator.run_all(ws, max_stages=5)
    # SCOPE should inherit the case-wide 1 min (= 60 s).
    assert captured_by_phase.get("scope") == 60, (
        f"SCOPE timeout should inherit case-wide 1 min = 60 s; "
        f"got {captured_by_phase.get('scope')!r}"
    )
    # EXECUTE should get the per-phase override 360 min (= 21,600 s).
    assert captured_by_phase.get("execute") == 360 * 60, (
        f"EXECUTE timeout should use per-phase override 360 min = 21600 s; "
        f"got {captured_by_phase.get('execute')!r}"
    )
