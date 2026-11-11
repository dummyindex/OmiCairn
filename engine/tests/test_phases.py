"""Unit tests for omicairn.phases — the phase registry.

The `phases` module defines the Phase dataclasses (SCOPE, PLAN,
EXECUTE, CROSS_CHECK, REVIEW, plus the off-pipeline FREE/IDEATE) and the helpers that the
orchestrator and CLI use to look them up and add/remove web tools.

phases.py carries a comment claiming "Any drift here is caught by the
test in tests/test_phases.py" but that file didn't actually exist —
orchestrator dry-run tests covered the registry transitively. This
file is the direct dual: fast, independent, and it pins the exact
shape of every phase against drift so renames / reorderings / dropped
contract files fail at edit time instead of during a real run.
"""
from __future__ import annotations

import pytest

from omicairn import phases
from omicairn.constants import (
    DONE_FLAG,
    FINDINGS_FILE,
    CLAUDE_CODE_PHASES,
    PIPELINE_ORDER,
    PhaseName,
    SUMMARY_FILE,
    VERDICT_FILE,
)


# ---------------------------------------------------------------------------
# Registry layout — ORDER and BY_NAME pinned to PIPELINE_ORDER
# ---------------------------------------------------------------------------

def test_order_matches_pipeline_order():
    """phases.ORDER must render Phase objects in the exact same order
    as constants.PIPELINE_ORDER. If these drift, the orchestrator's
    linear-progression loop picks the wrong next phase."""
    assert tuple(p.name for p in phases.ORDER) == PIPELINE_ORDER


def test_by_name_is_complete():
    """phases.BY_NAME must contain every PIPELINE_ORDER phase, plus the
    off-pipeline FREE phase (steering Slice 2) and the off-pipeline
    IDEATE phase (standalone brainstorm front-stage). Keyed by the
    canonical phase name string."""
    assert set(phases.BY_NAME.keys()) == (
        set(PIPELINE_ORDER) | {PhaseName.FREE, PhaseName.IDEATE}
    )
    for name in PIPELINE_ORDER:
        assert phases.BY_NAME[name].name == name
    assert phases.BY_NAME[PhaseName.FREE].name == PhaseName.FREE
    assert phases.BY_NAME[PhaseName.IDEATE].name == PhaseName.IDEATE


def test_get_returns_the_named_phase():
    assert phases.get(PhaseName.SCOPE) is phases.SCOPE
    assert phases.get(PhaseName.EXECUTE) is phases.EXECUTE
    assert phases.get(PhaseName.CROSS_CHECK) is phases.CROSS_CHECK
    assert phases.get(PhaseName.REVIEW) is phases.REVIEW


def test_get_rejects_unknown_phase_with_helpful_message():
    with pytest.raises(ValueError) as exc:
        phases.get("not_a_phase")
    msg = str(exc.value)
    assert "unknown phase" in msg
    # The error message should list the valid phases so the user knows
    # what to use. We just check one of them is in the message.
    assert PhaseName.SCOPE in msg


# ---------------------------------------------------------------------------
# Every phase has the contract-file shape the orchestrator expects
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("phase", list(phases.ORDER))
def test_every_phase_has_positive_timeout(phase):
    assert phase.timeout_seconds > 0, (
        f"{phase.name} has non-positive timeout {phase.timeout_seconds}s; "
        f"orchestrator uses this for claude_run's timeout_seconds param"
    )


@pytest.mark.parametrize("phase", list(phases.ORDER))
def test_every_phase_has_a_description(phase):
    """phase.description is used in the append_system_prompt sent to
    claude — an empty one would leak '' into the prompt."""
    assert phase.description.strip(), (
        f"{phase.name} has an empty description"
    )


@pytest.mark.parametrize("phase", list(phases.ORDER))
def test_every_phase_has_nonempty_required_outputs(phase):
    """A phase with no required_outputs would trivially pass
    contracts.verify on an empty stage dir, which would let the
    orchestrator mark a broken stage as DONE. test_contracts.py has
    the parametrized boundary test; this assertion is the static
    regression guard."""
    assert len(phase.required_outputs) > 0, (
        f"{phase.name} has no required_outputs — empty-dir contract "
        f"verification would trivially pass"
    )


def test_claude_code_phases_flag_matches_prompt_file_presence():
    """Exactly the phases in CLAUDE_CODE_PHASES must carry a non-empty
    prompt_file. cross_check is the one in-process phase; its
    prompt_file must be the empty string. Any drift here silently
    breaks _render_prompt (which unconditionally reads
    phase.prompt_file)."""
    for phase in phases.ORDER:
        if phase.name in CLAUDE_CODE_PHASES:
            assert phase.prompt_file, (
                f"Claude Code phase {phase.name} has no prompt_file"
            )
            assert phase.is_claude_code is True
        else:
            assert phase.prompt_file == "", (
                f"in-process phase {phase.name} unexpectedly has "
                f"prompt_file={phase.prompt_file!r}"
            )
            assert phase.is_claude_code is False


# Every Claude Code phase writes done.flag + summary.md as part of its
# _COMMON_STAGE_OUTPUTS — without those the orchestrator can't
# distinguish a done stage from an in-progress one.
@pytest.mark.parametrize(
    "phase",
    [p for p in phases.ORDER if p.name in CLAUDE_CODE_PHASES],
)
def test_claude_code_phases_require_done_flag_and_summary(phase):
    assert DONE_FLAG in phase.required_outputs, (
        f"Claude Code phase {phase.name} is missing {DONE_FLAG} from "
        f"required_outputs — latest_stage_of() would never find it"
    )
    assert SUMMARY_FILE in phase.required_outputs, (
        f"Claude Code phase {phase.name} is missing {SUMMARY_FILE} from "
        f"required_outputs — {{stage_listing}} would break"
    )


def test_cross_check_required_outputs_include_verdict_and_findings():
    """cross_check's contract is specifically verdict.json + findings.md
    + done.flag — this is what _run_cross_check_stage writes and what
    decide_next_phase reads back."""
    required = set(phases.CROSS_CHECK.required_outputs)
    assert required == {VERDICT_FILE, FINDINGS_FILE, DONE_FLAG}


def test_review_required_outputs_include_verdict_file():
    """The review phase must write verdict.json because its verdict is
    the only signal that the pipeline is DONE. Without it, a finished
    review gets interpreted as 'DONE by default' via the missing-verdict
    fallthrough — a subtle footgun if it ever gets dropped."""
    assert VERDICT_FILE in phases.REVIEW.required_outputs


# ---------------------------------------------------------------------------
# tools_with_web — web-tool opt-in
# ---------------------------------------------------------------------------

def test_tools_with_web_true_returns_tools_unchanged():
    """allow_web=True returns the full declared tool list. The
    orchestrator passes this to claude's --allowedTools."""
    for phase in phases.ORDER:
        got = phases.tools_with_web(phase, allow_web=True)
        assert tuple(got) == phase.allowed_tools


def test_tools_with_web_false_strips_webfetch_and_websearch():
    """allow_web=False must remove WebFetch and WebSearch (only).
    Every other tool stays in the allowlist — don't accidentally
    strip, say, Bash(python:*)."""
    for phase in phases.ORDER:
        got = phases.tools_with_web(phase, allow_web=False)
        assert "WebFetch" not in got
        assert "WebSearch" not in got
        # Every non-web tool that was originally present is still there.
        for t in phase.allowed_tools:
            if t in {"WebFetch", "WebSearch"}:
                continue
            assert t in got, (
                f"{phase.name}: tool {t!r} was stripped when allow_web=False"
            )


def test_plan_phase_includes_web_tools_in_declared_allowlist():
    """Sanity check that at least the phases that need web (PLAN,
    EXECUTE, REVIEW) declare WebFetch/WebSearch — otherwise
    `allow_web=True` has nothing to unblock and the prompt-level
    "you may use WebSearch" instructions are dead letter."""
    assert "WebFetch" in phases.PLAN.allowed_tools
    assert "WebSearch" in phases.PLAN.allowed_tools


def test_cross_check_has_no_allowed_tools():
    """cross_check is pure Python — the orchestrator never spawns a
    claude process for it, so its tool list is empty by design."""
    assert phases.CROSS_CHECK.allowed_tools == ()


# ---------------------------------------------------------------------------
# next_phase_in_order — linear fallback used by decide_next_phase
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "current,expected",
    [
        (PhaseName.SCOPE, PhaseName.PLAN),
        (PhaseName.PLAN, PhaseName.EXECUTE),
        (PhaseName.EXECUTE, PhaseName.CROSS_CHECK),
        (PhaseName.CROSS_CHECK, PhaseName.REVIEW),
        (PhaseName.REVIEW, None),  # end of pipeline
    ],
)
def test_next_phase_in_order(current, expected):
    assert phases.next_phase_in_order(current) == expected


def test_next_phase_in_order_rejects_unknown():
    with pytest.raises(ValueError):
        phases.next_phase_in_order("not_a_phase")


# ---------------------------------------------------------------------------
# Frozen-dataclass immutability
# ---------------------------------------------------------------------------

def test_phase_is_immutable():
    """Phase is frozen=True so callers can't mutate the global
    registry entries. If someone drops frozen=True, mutations could
    leak across tests. This test is the canary."""
    with pytest.raises(Exception):  # FrozenInstanceError subclasses AttributeError
        phases.SCOPE.name = "mutated"  # type: ignore[misc]


def test_phase_prompt_path_returns_prompt_file():
    """Phase.prompt_path (line 41) is a property alias for prompt_file.
    Pin it so a rename of the underlying field or a rewrite of the
    alias (e.g. resolving to an absolute path) gets caught at test
    time. All LLM phases have a non-empty prompt_file; cross_check
    is the only one with an empty string and must report that too."""
    for p in phases.ORDER:
        assert p.prompt_path == p.prompt_file
    # LLM phases have a prompt file; cross_check does not.
    assert phases.SCOPE.prompt_path == "scope.md"
    assert phases.CROSS_CHECK.prompt_path == ""
