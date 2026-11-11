"""Tests for the SCOPE-time feasibility / statistical-power gate.

Coverage (per the deliverable's required matrix):

  - schema validation (well-formed → ok; each failure mode → not ok)
  - config parsing + gate_applies (confirmatory / all / none; default)
  - decision_for routing: feasible → proceed, underpowered → remediation,
    needs_data → acquire (back_to_plan)
  - NEUTRALITY: a well-powered NULL passes cleanly (the gate keys on
    power, not on the hypothesised direction)
  - decide_next_phase integration: a SCOPE stage carrying a
    feasibility-gate verdict routes to remediation; a feasible/absent
    one falls through to the linear walk (PLAN).
"""
from __future__ import annotations

import json
import pathlib
import tempfile
from typing import Iterator

import pytest

from omicairn import feasibility, orchestrator, state, stage as stage_mod, verdict as verdict_mod, workspace
from omicairn.constants import (
    DONE_FLAG,
    Decision,
    FEASIBILITY_FILE,
    FeasibilityVerdict,
    PhaseName,
    RecommendedAction,
)


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------

def _write_feas(tmp: pathlib.Path, payload) -> pathlib.Path:
    p = tmp / FEASIBILITY_FILE
    p.write_text(payload if isinstance(payload, str) else json.dumps(payload))
    return p


_GOOD_FEASIBLE = {
    "verdict": "feasible",
    "recommended_action": "proceed",
    "rationale": "Both atlases yield > 5000 DA neurons, above the reference.",
    "analysis_units": [
        {"name": "Wang atlas", "extractable_n_estimate": 8000,
         "reference_n": 22048, "adequacy": "adequate"},
    ],
}

_GOOD_UNDERPOWERED = {
    "verdict": "underpowered",
    "recommended_action": "fix_extraction",
    "rationale": "52 DA cells vs 22,048 reference — strict gate on processed h5ad.",
    "analysis_units": [
        {"name": "Wang atlas", "extractable_n_estimate": 52,
         "reference_n": 22048, "adequacy": "inadequate"},
        {"name": "Adams atlas", "extractable_n_estimate": 27,
         "reference_n": 22048, "adequacy": "inadequate"},
    ],
}

_GOOD_NEEDS_DATA = {
    "verdict": "needs_data",
    "recommended_action": "acquire_data",
    "rationale": "The highest-powered Fullard SNpc slab is on CELLxGENE but not cached.",
    "analysis_units": [
        {"name": "Fullard atlas", "extractable_n_estimate": None,
         "reference_n": 2000000, "adequacy": "missing"},
    ],
}


def test_validate_well_formed_feasible():
    with tempfile.TemporaryDirectory() as td:
        p = _write_feas(pathlib.Path(td), _GOOD_FEASIBLE)
        res = feasibility.validate(p)
    assert res.ok
    assert res.feasibility is not None
    assert res.feasibility.verdict == FeasibilityVerdict.FEASIBLE
    assert res.feasibility.analysis_units[0].extractable_n_estimate == 8000


def test_validate_missing_file():
    with tempfile.TemporaryDirectory() as td:
        res = feasibility.validate(pathlib.Path(td) / FEASIBILITY_FILE)
    assert not res.ok
    assert res.feasibility is None


def test_validate_unparseable():
    with tempfile.TemporaryDirectory() as td:
        p = _write_feas(pathlib.Path(td), "{not json")
        res = feasibility.validate(p)
    assert not res.ok


@pytest.mark.parametrize("mutate,frag", [
    (lambda d: d.update(verdict="great"), "verdict"),
    (lambda d: d.update(recommended_action="yolo"), "recommended_action"),
    (lambda d: d.update(rationale=""), "rationale"),
    (lambda d: d.update(analysis_units=[]), "analysis_units"),
    (lambda d: d["analysis_units"][0].update(adequacy="huge"), "adequacy"),
    (lambda d: d["analysis_units"][0].update(name=""), "name"),
])
def test_validate_rejects_malformed(mutate, frag):
    payload = json.loads(json.dumps(_GOOD_FEASIBLE))  # deep copy
    mutate(payload)
    with tempfile.TemporaryDirectory() as td:
        p = _write_feas(pathlib.Path(td), payload)
        res = feasibility.validate(p)
    assert not res.ok
    assert any(frag in e for e in res.errors), res.errors


# ---------------------------------------------------------------------------
# Config + gate_applies
# ---------------------------------------------------------------------------

def test_parse_config_defaults():
    cfg = feasibility.parse_config(None)
    assert cfg.applies_to == "confirmatory"
    assert cfg.min_fraction_of_reference == pytest.approx(0.1)


def test_parse_config_tolerates_bad_applies_to():
    cfg = feasibility.parse_config({"applies_to": "nonsense"})
    assert cfg.applies_to == "confirmatory"  # falls back, never crashes


def test_parse_config_custom():
    cfg = feasibility.parse_config(
        {"applies_to": "all", "min_fraction_of_reference": 0.25})
    assert cfg.applies_to == "all"
    assert cfg.min_fraction_of_reference == pytest.approx(0.25)


def test_gate_applies_confirmatory_method_mode():
    cfg = feasibility.parse_config(None)  # confirmatory default
    assert feasibility.gate_applies(cfg, research_mode="METHOD", pre_registration=False)


def test_gate_applies_confirmatory_prereg_discovery():
    """An explicitly required pre-registration counts as confirmatory even
    in DISCOVERY mode."""
    cfg = feasibility.parse_config(None)
    assert feasibility.gate_applies(cfg, research_mode="DISCOVERY", pre_registration=True)


def test_gate_does_not_apply_to_exploratory_discovery():
    """Belief 13: do not block legitimate exploratory work."""
    cfg = feasibility.parse_config(None)
    assert not feasibility.gate_applies(
        cfg, research_mode="DISCOVERY", pre_registration=False)


def test_gate_none_disables_even_method():
    cfg = feasibility.parse_config({"applies_to": "none"})
    assert not feasibility.gate_applies(cfg, research_mode="METHOD", pre_registration=True)


def test_gate_all_applies_to_discovery():
    cfg = feasibility.parse_config({"applies_to": "all"})
    assert feasibility.gate_applies(
        cfg, research_mode="DISCOVERY", pre_registration=False)


# ---------------------------------------------------------------------------
# decision_for routing
# ---------------------------------------------------------------------------

def _feas_obj(payload) -> feasibility.Feasibility:
    with tempfile.TemporaryDirectory() as td:
        p = _write_feas(pathlib.Path(td), payload)
        res = feasibility.validate(p)
    assert res.ok and res.feasibility is not None
    return res.feasibility


def test_decision_feasible_proceeds():
    assert feasibility.decision_for(_feas_obj(_GOOD_FEASIBLE)) == Decision.PROCEED


def test_decision_underpowered_fix_extraction_back_to_scope():
    assert feasibility.decision_for(_feas_obj(_GOOD_UNDERPOWERED)) == Decision.BACK_TO_SCOPE


def test_decision_needs_data_acquire_back_to_plan():
    assert feasibility.decision_for(_feas_obj(_GOOD_NEEDS_DATA)) == Decision.BACK_TO_PLAN


def test_decision_rescope_back_to_scope():
    payload = json.loads(json.dumps(_GOOD_UNDERPOWERED))
    payload["recommended_action"] = RecommendedAction.RESCOPE
    assert feasibility.decision_for(_feas_obj(payload)) == Decision.BACK_TO_SCOPE


# ---------------------------------------------------------------------------
# NEUTRALITY — a well-powered NULL must pass cleanly
# ---------------------------------------------------------------------------

def test_neutrality_powered_null_proceeds():
    """The gate keys ONLY on power / data feasibility, never on the
    direction or sign of a result. A study that is well-powered and
    expects (or finds) a NULL must proceed cleanly — the gate's job is to
    stop UNDER-powered runs, not to chase positives (belief 13)."""
    powered_null = {
        "verdict": "feasible",
        "recommended_action": "proceed",
        "rationale": (
            "Adequately powered (N=18,000 cells, > reference). We expect a "
            "null and that is a valid, valuable confirmatory result."
        ),
        "analysis_units": [
            {"name": "atlas", "extractable_n_estimate": 18000,
             "reference_n": 22048, "adequacy": "adequate"},
        ],
    }
    feas = _feas_obj(powered_null)
    assert feas.verdict == FeasibilityVerdict.FEASIBLE
    assert feasibility.decision_for(feas) == Decision.PROCEED


def test_feasible_verdict_overrides_nonproceed_action():
    """Defensive: if the agent emits verdict=feasible but a non-proceed
    action, the verdict wins (a clean run must not be blocked)."""
    payload = json.loads(json.dumps(_GOOD_FEASIBLE))
    payload["recommended_action"] = "rescope"
    assert feasibility.decision_for(_feas_obj(payload)) == Decision.PROCEED


# ---------------------------------------------------------------------------
# Integration: decide_next_phase honours a SCOPE feasibility verdict
# ---------------------------------------------------------------------------

class _StubWorkspace:
    """decide_next_phase reads only ws.root + ws.state_path."""
    def __init__(self, root: pathlib.Path) -> None:
        self.root = root
        self.state_path = root / "STATE.md"


@pytest.fixture
def ws() -> Iterator[_StubWorkspace]:
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        s = state.initial_state("pytest-feasibility")
        state.write(root / "STATE.md", s)
        yield _StubWorkspace(root)


def _mk_scope(ws, decision: str | None) -> pathlib.Path:
    path = stage_mod.create_stage_dir(ws.root, 0, PhaseName.SCOPE)
    (path / DONE_FLAG).write_text("1\n")
    if decision is not None:
        verdict_mod.write_verdict(
            path, verdict_mod.Verdict(decision=decision, reason="feas", evidence=[]))
    return path


def test_scope_underpowered_verdict_routes_back_to_scope(ws):
    _mk_scope(ws, Decision.BACK_TO_SCOPE)
    assert orchestrator.decide_next_phase(ws) == PhaseName.SCOPE


def test_scope_needs_data_verdict_routes_back_to_plan(ws):
    _mk_scope(ws, Decision.BACK_TO_PLAN)
    assert orchestrator.decide_next_phase(ws) == PhaseName.PLAN


def test_scope_no_verdict_proceeds_to_plan(ws):
    """The common case: feasible / gate off → no verdict.json → the
    normal linear walk advances SCOPE → PLAN."""
    _mk_scope(ws, None)
    assert orchestrator.decide_next_phase(ws) == PhaseName.PLAN


def test_scope_proceed_verdict_advances_to_plan(ws):
    """A verdict.json that says proceed must NOT loop back — it advances
    linearly (proceed is not in DECISION_TO_PHASE, so the branch falls
    through to next-in-order)."""
    _mk_scope(ws, Decision.PROCEED)
    assert orchestrator.decide_next_phase(ws) == PhaseName.PLAN


# ---------------------------------------------------------------------------
# Full integration: _process_feasibility_gate against a real workspace.
# This exercises the config-driven gate_applies + validate + write-verdict
# + event path that the orchestrator runs after a SCOPE stage completes.
# ---------------------------------------------------------------------------

_RMD = """\
---
research_mode: {mode}
domain: synthetic_smoke_test
topic_slug: pytest-feas
research_question: |
  Does the feasibility gate route correctly?
data:
  location: {data_dir}
  description: synthetic pytest fixture
  cohorts: [a]
  modalities: [tabular]
success_criteria:
  primary_metric: auc
  baseline: null_model
{feasibility_block}auto_approve: true
---
# fixture
"""


def _make_ws(td: pathlib.Path, *, mode: str = "METHOD",
             feasibility_block: str = "") -> workspace.Workspace:
    data_dir = td / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "dummy.csv").write_text("x,y\n1,1\n")
    rmd = td / "RESEARCH.md"
    rmd.write_text(_RMD.format(
        mode=mode, data_dir=data_dir, feasibility_block=feasibility_block))
    ws_root = td / "workspaces"
    ws_root.mkdir()
    return workspace.init("pytest-feas", research_md_path=rmd,
                          workspaces_root=ws_root, skip_literature=True)


def _scope_stage(ws) -> pathlib.Path:
    return stage_mod.create_stage_dir(ws.root, 0, PhaseName.SCOPE)


def test_gate_underpowered_writes_verdict_and_routes():
    with tempfile.TemporaryDirectory() as td:
        ws = _make_ws(pathlib.Path(td))  # METHOD → gate active
        sd = _scope_stage(ws)
        _write_feas(sd, _GOOD_UNDERPOWERED)
        s = state.read(ws.state_path)
        decision = orchestrator._process_feasibility_gate(ws, sd)
        assert decision == Decision.BACK_TO_SCOPE
        # A verdict.json must have been written so decide_next_phase can route.
        v = verdict_mod.read_verdict(sd)
        assert v is not None and v.decision == Decision.BACK_TO_SCOPE
        assert v.proposed_fix  # carries the rationale to the retry note


def test_gate_needs_data_routes_back_to_plan_acquire():
    with tempfile.TemporaryDirectory() as td:
        ws = _make_ws(pathlib.Path(td))
        sd = _scope_stage(ws)
        _write_feas(sd, _GOOD_NEEDS_DATA)
        decision = orchestrator._process_feasibility_gate(ws, sd)
    assert decision == Decision.BACK_TO_PLAN


def test_gate_feasible_proceeds_no_verdict_written():
    with tempfile.TemporaryDirectory() as td:
        ws = _make_ws(pathlib.Path(td))
        sd = _scope_stage(ws)
        _write_feas(sd, _GOOD_FEASIBLE)
        decision = orchestrator._process_feasibility_gate(ws, sd)
        # No remediation verdict on a clean proceed.
        assert not (sd / "verdict.json").exists()
    assert decision == Decision.PROCEED


def test_gate_inactive_for_exploratory_discovery_proceeds():
    """Belief 13: an exploratory DISCOVERY run is NOT gated even if it
    drops a (would-be-underpowered) feasibility.json — the orchestrator
    proceeds and never routes on it."""
    with tempfile.TemporaryDirectory() as td:
        ws = _make_ws(pathlib.Path(td), mode="DISCOVERY")
        sd = _scope_stage(ws)
        _write_feas(sd, _GOOD_UNDERPOWERED)
        decision = orchestrator._process_feasibility_gate(ws, sd)
    assert decision == Decision.PROCEED


def test_gate_applies_to_all_block_gates_discovery():
    block = "feasibility:\n  applies_to: all\n"
    with tempfile.TemporaryDirectory() as td:
        ws = _make_ws(pathlib.Path(td), mode="DISCOVERY", feasibility_block=block)
        sd = _scope_stage(ws)
        _write_feas(sd, _GOOD_UNDERPOWERED)
        decision = orchestrator._process_feasibility_gate(ws, sd)
    assert decision == Decision.BACK_TO_SCOPE


def test_gate_none_block_disables_for_method():
    block = "feasibility:\n  applies_to: none\n"
    with tempfile.TemporaryDirectory() as td:
        ws = _make_ws(pathlib.Path(td), mode="METHOD", feasibility_block=block)
        sd = _scope_stage(ws)
        _write_feas(sd, _GOOD_UNDERPOWERED)
        decision = orchestrator._process_feasibility_gate(ws, sd)
    assert decision == Decision.PROCEED


def test_gate_active_but_invalid_feasibility_proceeds_warn_only():
    """A schema-invalid feasibility.json must not strand the run — the
    gate is a safety rail, not a hard halt on a typo."""
    with tempfile.TemporaryDirectory() as td:
        ws = _make_ws(pathlib.Path(td))
        sd = _scope_stage(ws)
        _write_feas(sd, "{broken")
        decision = orchestrator._process_feasibility_gate(ws, sd)
    assert decision == Decision.PROCEED
