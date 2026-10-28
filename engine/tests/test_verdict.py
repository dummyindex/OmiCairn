"""Unit tests for omicairn.verdict (verdict.json schema + routing helpers)."""
from __future__ import annotations

import json
import pathlib
import tempfile

import pytest

from omicairn import verdict
from omicairn.constants import (
    BACKTRACK_DECISIONS,
    DONE_FLAG,
    Decision,
    FINDINGS_FILE,
    PhaseName,
    VERDICT_FILE,
)


def _tmp_stage_dir() -> pathlib.Path:
    root = pathlib.Path(tempfile.mkdtemp())
    d = root / "002_execute"
    d.mkdir()
    return d


# ---------------------------------------------------------------------------
# Verdict dataclass
# ---------------------------------------------------------------------------

def test_verdict_dataclass_defaults():
    v = verdict.Verdict(
        decision=Decision.PROCEED,
        reason="ok",
        evidence=[],
    )
    assert v.is_valid() is True
    assert v.proposed_fix == ""
    assert v.target_file_hints == []
    d = v.to_dict()
    assert d["decision"] == Decision.PROCEED
    assert d["reason"] == "ok"


def test_verdict_is_valid_rejects_unknown_decision():
    v = verdict.Verdict(decision="nope", reason="r", evidence=[])
    assert v.is_valid() is False


def test_verdict_is_valid_accepts_every_legal_decision():
    all_decisions = [
        Decision.PROCEED,
        Decision.RETRY_EXECUTE,
        Decision.BACK_TO_SCOPE,
        Decision.BACK_TO_PLAN,
        Decision.BACK_TO_EXECUTE,
        Decision.HALT,
        Decision.DONE,
    ]
    for d in all_decisions:
        v = verdict.Verdict(decision=d, reason="r", evidence=[])
        assert v.is_valid(), f"{d} should be valid"


# ---------------------------------------------------------------------------
# Read / write roundtrip
# ---------------------------------------------------------------------------

def test_write_verdict_writes_valid_json():
    stage_dir = _tmp_stage_dir()
    v = verdict.Verdict(
        decision=Decision.RETRY_EXECUTE,
        reason="cross_check errors",
        evidence=["missing ci_95", "conditions.*"],
        proposed_fix="canonicalize results.json",
        target_file_hints=["code/main.py"],
    )
    verdict.write_verdict(stage_dir, v)
    path = stage_dir / VERDICT_FILE
    assert path.exists()
    data = json.loads(path.read_text())
    assert data["decision"] == Decision.RETRY_EXECUTE
    assert data["evidence"] == ["missing ci_95", "conditions.*"]
    assert data["target_file_hints"] == ["code/main.py"]


def test_write_verdict_rejects_invalid_decision():
    stage_dir = _tmp_stage_dir()
    v = verdict.Verdict(decision="garbage", reason="r", evidence=[])
    with pytest.raises(ValueError):
        verdict.write_verdict(stage_dir, v)


def test_read_verdict_roundtrips_a_written_verdict():
    stage_dir = _tmp_stage_dir()
    original = verdict.Verdict(
        decision=Decision.BACK_TO_PLAN,
        reason="plan insufficient",
        evidence=["no falsification test"],
    )
    verdict.write_verdict(stage_dir, original)
    back = verdict.read_verdict(stage_dir)
    assert back is not None
    assert back.decision == Decision.BACK_TO_PLAN
    assert back.reason == "plan insufficient"
    assert back.evidence == ["no falsification test"]


def test_read_verdict_missing_returns_none():
    stage_dir = _tmp_stage_dir()
    assert verdict.read_verdict(stage_dir) is None


def test_read_verdict_malformed_json_returns_none():
    stage_dir = _tmp_stage_dir()
    (stage_dir / VERDICT_FILE).write_text("not json at all")
    assert verdict.read_verdict(stage_dir) is None


def test_read_verdict_unknown_decision_returns_none():
    stage_dir = _tmp_stage_dir()
    (stage_dir / VERDICT_FILE).write_text(
        json.dumps({"decision": "teleport", "reason": "r", "evidence": []})
    )
    assert verdict.read_verdict(stage_dir) is None


def test_read_verdict_accepts_verdict_key_as_alias_for_decision():
    """Real failure observed in ddr-blca-20260430: a stage emitted
    ``{"verdict": "retry_execute", ...}`` instead of the canonical
    ``{"decision": "retry_execute", ...}``. Pre-fix read_verdict
    returned None and the pipeline silently advanced past a
    retry-needed stage. Now `verdict` is accepted as an alias for
    `decision`."""
    stage_dir = _tmp_stage_dir()
    (stage_dir / VERDICT_FILE).write_text(
        json.dumps({
            "verdict": Decision.RETRY_EXECUTE,
            "reason": "two unfixable section heading errors",
            "unfixable_errors": [{"location": "title", "error": "..."}],
        })
    )
    v = verdict.read_verdict(stage_dir)
    assert v is not None
    assert v.decision == Decision.RETRY_EXECUTE
    assert "unfixable" in v.reason


def test_write_findings_and_done_flag():
    stage_dir = _tmp_stage_dir()
    verdict.write_findings(stage_dir, "# findings\nall good\n")
    verdict.write_done_flag(stage_dir)
    assert (stage_dir / FINDINGS_FILE).read_text().startswith("# findings")
    assert (stage_dir / DONE_FLAG).exists()


# ---------------------------------------------------------------------------
# Decision builder helpers
# ---------------------------------------------------------------------------

def test_builder_proceed():
    v = verdict.proceed("everything looks clean")
    assert v.decision == Decision.PROCEED
    assert "clean" in v.reason


def test_builder_halt_and_done():
    h = verdict.halt("out of budget")
    assert h.decision == Decision.HALT
    d = verdict.done()
    assert d.decision == Decision.DONE


def test_builder_retry_for_execute():
    v = verdict.retry(PhaseName.EXECUTE, reason="bad results", fix="add CI")
    assert v.decision == Decision.RETRY_EXECUTE
    assert v.proposed_fix == "add CI"


def test_builder_retry_rejects_phases_without_retry():
    with pytest.raises(ValueError):
        verdict.retry(PhaseName.SCOPE, reason="r")
    with pytest.raises(ValueError):
        verdict.retry(PhaseName.PLAN, reason="r")
    with pytest.raises(ValueError):
        verdict.retry(PhaseName.REVIEW, reason="r")


def test_builder_back_to_maps_to_correct_decision():
    mapping = {
        PhaseName.SCOPE: Decision.BACK_TO_SCOPE,
        PhaseName.PLAN: Decision.BACK_TO_PLAN,
        PhaseName.EXECUTE: Decision.BACK_TO_EXECUTE,
    }
    for phase, expected in mapping.items():
        v = verdict.back_to(phase, reason="r")
        assert v.decision == expected
    with pytest.raises(ValueError):
        verdict.back_to(PhaseName.CROSS_CHECK, reason="r")


def test_backtrack_decisions_set_matches_builders():
    """Every decision produced by back_to() must be in BACKTRACK_DECISIONS
    so the orchestrator's backtrack counter fires correctly."""
    for phase in (PhaseName.SCOPE, PhaseName.PLAN,
                  PhaseName.EXECUTE):
        v = verdict.back_to(phase, reason="r")
        assert v.decision in BACKTRACK_DECISIONS


# ---------------------------------------------------------------------------
# read_verdict defensive branches
#
# A verdict.json written by a misbehaving subagent (or a corrupted disk)
# must NOT crash the orchestrator — read_verdict should return None and
# let the caller fall through to linear routing.
# ---------------------------------------------------------------------------

def test_read_verdict_returns_none_when_json_is_not_a_dict():
    """Line 78: `if not isinstance(data, dict): return None`. A verdict
    file containing `["legal", "json", "but", "a", "list"]` or a bare
    string is valid JSON but the schema requires a dict — fall through
    to None rather than crashing on .get()."""
    d = _tmp_stage_dir()
    (d / VERDICT_FILE).write_text(json.dumps(["not", "a", "dict"]))
    assert verdict.read_verdict(d) is None

    # Bare-string JSON is also legal JSON but wrong shape.
    (d / VERDICT_FILE).write_text(json.dumps("just a string"))
    assert verdict.read_verdict(d) is None


def test_read_verdict_returns_none_when_decision_is_not_a_string():
    """Line 82: `if not isinstance(decision, str): return None`. A dict
    with a non-string decision field (e.g. `{"decision": 42}`) must
    fall through. Previously untested — the happy-path tests always
    write a legal string decision."""
    d = _tmp_stage_dir()
    (d / VERDICT_FILE).write_text(json.dumps({
        "decision": 42,  # wrong type
        "reason": "r",
        "evidence": [],
    }))
    assert verdict.read_verdict(d) is None

    # Decision missing entirely → .get() returns None → not a string.
    (d / VERDICT_FILE).write_text(json.dumps({
        "reason": "r",
        "evidence": [],
    }))
    assert verdict.read_verdict(d) is None


# ---------------------------------------------------------------------------
# Legacy paper-era verdicts
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("legacy", ["retry_write", "retry_review", "back_to_write"])
def test_read_verdict_legacy_removed_decision_is_none(legacy):
    """A verdict.json from a pre-removal workspace carrying a paper-era
    decision reads as no verdict (linear fall-through), never raises."""
    d = _tmp_stage_dir()
    (d / VERDICT_FILE).write_text(json.dumps({
        "decision": legacy, "reason": "r", "evidence": [],
        "reviewer_consensus": "accept", "revision_count": 0,
    }))
    assert verdict.read_verdict(d) is None


def test_read_verdict_ignores_legacy_peer_review_fields():
    d = _tmp_stage_dir()
    (d / VERDICT_FILE).write_text(json.dumps({
        "decision": Decision.DONE, "reason": "r", "evidence": [],
        "reviewer_consensus": "accept", "revision_count": 0,
    }))
    v = verdict.read_verdict(d)
    assert v is not None and v.decision == Decision.DONE
    assert "reviewer_consensus" not in v.to_dict()
