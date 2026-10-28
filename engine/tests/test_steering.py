"""Tests for omicairn.steering — Slice 1 of human-steering rollout."""
from __future__ import annotations

import json
import pathlib
import tempfile

import pytest

from omicairn import steering


def test_read_plan_missing_returns_none():
    with tempfile.TemporaryDirectory() as t:
        ws = pathlib.Path(t)
        assert steering.read_plan(ws) is None


def test_read_plan_malformed_json_returns_none():
    with tempfile.TemporaryDirectory() as t:
        ws = pathlib.Path(t)
        steering.plan_path(ws).write_text("not json")
        assert steering.read_plan(ws) is None


def test_write_then_read_roundtrips():
    with tempfile.TemporaryDirectory() as t:
        ws = pathlib.Path(t)
        plan = steering.SteeringPlan(
            user_request="regenerate fig 3 at 600 dpi",
            rationale="user wants print-legible panels",
            steps=[
                steering.SteeringStep(
                    phase="execute",
                    prompt_overlay="USER STEERING: re-render fig3 at 600 dpi",
                    expected_changes=["mtime(figures/fig3_*.png) > NOW"],
                    rationale="render only fig3",
                ),
            ],
        )
        steering.write_plan(ws, plan)
        loaded = steering.read_plan(ws)
        assert loaded is not None
        assert loaded.user_request == plan.user_request
        assert len(loaded.steps) == 1
        assert loaded.steps[0].phase == "execute"
        assert loaded.steps[0].done is False
        # write_plan stamps created_at if missing
        assert loaded.created_at != ""


def test_validate_plan_canonical_passes():
    plan = steering.SteeringPlan(
        user_request="x",
        steps=[
            steering.SteeringStep(
                phase="execute",
                prompt_overlay="USER STEERING: add a sensitivity arm",
            )
        ],
    )
    assert steering.validate_plan(plan) == []


def test_validate_plan_rejects_empty_steps():
    plan = steering.SteeringPlan(user_request="x", steps=[])
    errs = steering.validate_plan(plan)
    assert any("steps is empty" in e for e in errs)


def test_validate_plan_rejects_empty_user_request():
    plan = steering.SteeringPlan(
        user_request="",
        steps=[steering.SteeringStep(phase="x", prompt_overlay="y")],
    )
    errs = steering.validate_plan(plan)
    assert any("user_request is empty" in e for e in errs)


def test_validate_plan_rejects_step_with_both_overlay_and_inline():
    plan = steering.SteeringPlan(
        user_request="x",
        steps=[
            steering.SteeringStep(
                phase="execute",
                prompt_overlay="overlay",
                prompt_inline="inline",
            )
        ],
    )
    errs = steering.validate_plan(plan)
    assert any("mutually exclusive" in e for e in errs)


def test_validate_plan_rejects_step_with_neither_overlay_nor_inline():
    plan = steering.SteeringPlan(
        user_request="x",
        steps=[steering.SteeringStep(phase="execute")],
    )
    errs = steering.validate_plan(plan)
    assert any("neither" in e for e in errs)


def test_validate_plan_rejects_unknown_on_failure():
    plan = steering.SteeringPlan(
        user_request="x",
        steps=[steering.SteeringStep(phase="x", prompt_overlay="y")],
        on_step_failure="teleport",
    )
    errs = steering.validate_plan(plan)
    assert any("on_step_failure" in e for e in errs)


def test_next_pending_step_returns_first_undone():
    plan = steering.SteeringPlan(
        user_request="x",
        steps=[
            steering.SteeringStep(phase="execute", prompt_overlay="a", done=True),
            steering.SteeringStep(phase="cross_check", prompt_overlay="b", done=False),
            steering.SteeringStep(phase="review", prompt_overlay="c", done=False),
        ],
    )
    nxt = plan.next_pending_step()
    assert nxt is not None
    assert nxt.phase == "cross_check"


def test_next_pending_step_returns_none_when_all_done():
    plan = steering.SteeringPlan(
        user_request="x",
        steps=[
            steering.SteeringStep(phase="execute", prompt_overlay="a", done=True),
        ],
    )
    assert plan.next_pending_step() is None
    assert plan.is_complete()


# ---------------------------------------------------------------------------
# has_pending_plan — the single predicate the orchestrator uses to decide
# whether a done/halted workspace should still dispatch (FIX 3, 2026-06-10).
# ---------------------------------------------------------------------------

def test_has_pending_plan_false_when_no_plan():
    with tempfile.TemporaryDirectory() as t:
        assert steering.has_pending_plan(pathlib.Path(t)) is False


def test_has_pending_plan_true_for_valid_plan_with_undone_step():
    with tempfile.TemporaryDirectory() as t:
        ws = pathlib.Path(t)
        steering.write_plan(ws, steering.SteeringPlan(
            user_request="re-render fig3",
            steps=[steering.SteeringStep(phase="execute", prompt_overlay="a")],
        ))
        assert steering.has_pending_plan(ws) is True


def test_has_pending_plan_false_when_all_steps_done():
    with tempfile.TemporaryDirectory() as t:
        ws = pathlib.Path(t)
        steering.write_plan(ws, steering.SteeringPlan(
            user_request="x",
            steps=[steering.SteeringStep(
                phase="execute", prompt_overlay="a", done=True)],
        ))
        assert steering.has_pending_plan(ws) is False


def test_has_pending_plan_false_for_invalid_plan():
    """A plan that fails validate_plan (e.g. empty steps) is NOT pending —
    a malformed hand-edited plan must not un-terminate a done run."""
    with tempfile.TemporaryDirectory() as t:
        ws = pathlib.Path(t)
        steering.write_plan(
            ws, steering.SteeringPlan(user_request="x", steps=[]))
        assert steering.has_pending_plan(ws) is False


def test_mark_step_done_updates_disk_and_consumes_when_complete():
    with tempfile.TemporaryDirectory() as t:
        ws = pathlib.Path(t)
        plan = steering.SteeringPlan(
            user_request="x",
            steps=[
                steering.SteeringStep(phase="execute", prompt_overlay="a"),
            ],
        )
        steering.write_plan(ws, plan)
        steering.mark_step_done(ws, 0, "002_execute")
        # Plan was the only step → consumed.
        assert not steering.plan_path(ws).exists()
        assert steering.plan_path(ws).with_suffix(".consumed.json").exists()


def test_mark_step_done_partial_keeps_plan_active():
    with tempfile.TemporaryDirectory() as t:
        ws = pathlib.Path(t)
        plan = steering.SteeringPlan(
            user_request="x",
            steps=[
                steering.SteeringStep(phase="execute", prompt_overlay="a"),
                steering.SteeringStep(phase="review", prompt_overlay="b"),
            ],
        )
        steering.write_plan(ws, plan)
        steering.mark_step_done(ws, 0, "002_execute")
        loaded = steering.read_plan(ws)
        assert loaded is not None
        assert loaded.steps[0].done is True
        assert loaded.steps[1].done is False
        assert not loaded.is_complete()


def test_log_jsonl_append_only():
    with tempfile.TemporaryDirectory() as t:
        ws = pathlib.Path(t)
        plan = steering.SteeringPlan(
            user_request="x",
            steps=[steering.SteeringStep(phase="execute", prompt_overlay="a")],
        )
        steering.write_plan(ws, plan)
        steering.write_plan(ws, plan)
        steering.mark_step_done(ws, 0, "002_execute")
        log_lines = steering.log_path(ws).read_text().strip().splitlines()
        # 2 writes + 1 mark_done write + 1 consume = 4 lines minimum.
        assert len(log_lines) >= 4
        # Each line must be valid JSON.
        for line in log_lines:
            json.loads(line)


# ---------------------------------------------------------------------------
# Slice 2 — FREE phase + {{steering}} overlay rendering
# ---------------------------------------------------------------------------

def test_free_phase_is_in_by_name_but_not_in_order():
    """FREE is dispatched only via SteeringPlan; it lives in BY_NAME
    so phases.get('free') works, but is intentionally absent from
    ORDER so the linear walk never lands on it."""
    from omicairn import phases
    from omicairn.constants import PhaseName, PIPELINE_ORDER
    assert PhaseName.FREE in phases.BY_NAME
    assert phases.BY_NAME[PhaseName.FREE].name == PhaseName.FREE
    assert PhaseName.FREE not in PIPELINE_ORDER
    assert phases.FREE not in phases.ORDER


def test_free_phase_has_empty_prompt_file():
    """FREE has no template — the prompt comes from the active
    SteeringStep.prompt_inline at dispatch time."""
    from omicairn import phases
    assert phases.FREE.prompt_file == ""


def test_free_phase_is_in_claude_code_phases():
    """FREE dispatches a `claude -p` session, so the orchestrator
    routes it through _run_llm_stage (not the cross_check path)."""
    from omicairn.constants import CLAUDE_CODE_PHASES, PhaseName
    assert PhaseName.FREE in CLAUDE_CODE_PHASES


def test_format_steering_overlay_returns_none_when_no_step():
    from omicairn import orchestrator
    assert orchestrator._format_steering_overlay(None) == "(none)"


def test_format_steering_overlay_renders_user_steering_block():
    from omicairn import orchestrator
    step = steering.SteeringStep(
        phase="execute",
        prompt_overlay="re-run the primary model with the held-out cohort",
        rationale="REVIEW flagged over-claim",
        expected_changes=["mtime(results.json) > NOW"],
    )
    rendered = orchestrator._format_steering_overlay(step)
    assert "USER STEERING" in rendered
    assert "re-run the primary model" in rendered
    assert "REVIEW flagged" in rendered
    assert "mtime(results.json)" in rendered


def test_steering_token_resolves_to_none_when_no_active_plan(tmp_path):
    """The {{steering}} block on a vanilla run renders as (none) so
    no stray empty section ever surfaces in a phase prompt."""
    from omicairn import orchestrator
    # Build a workspace shell without any steering plan.
    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    step = orchestrator._active_steering_step(
        type("WS", (), {"root": ws_root})(), "review"
    )
    assert step is None


def test_active_steering_step_returns_step_for_matching_phase(tmp_path):
    from omicairn import orchestrator
    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    plan = steering.SteeringPlan(
        user_request="x",
        steps=[
            steering.SteeringStep(
                phase="review", prompt_overlay="re-check the null"
            ),
        ],
    )
    steering.write_plan(ws_root, plan)
    nxt = orchestrator._active_steering_step(
        type("WS", (), {"root": ws_root})(), "review"
    )
    assert nxt is not None
    assert nxt.prompt_overlay == "re-check the null"


def test_active_steering_step_returns_none_for_phase_mismatch(tmp_path):
    """Defensive: if the user manually dispatched a different phase
    while a stale plan is sitting around, don't inject the wrong
    overlay."""
    from omicairn import orchestrator
    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    plan = steering.SteeringPlan(
        user_request="x",
        steps=[
            steering.SteeringStep(
                phase="review", prompt_overlay="re-check the null"
            ),
        ],
    )
    steering.write_plan(ws_root, plan)
    # User dispatched 'execute', not 'review' — should NOT match.
    nxt = orchestrator._active_steering_step(
        type("WS", (), {"root": ws_root})(), "execute"
    )
    assert nxt is None
