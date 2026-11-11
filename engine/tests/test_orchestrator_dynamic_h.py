"""Integration tests for the orchestrator's knowledge-graph hooks
(core beliefs 11 & 12): seeding goals/hypotheses at run start, folding
a stage's graph_updates.json into the workspace graph, and the
on-the-fly gating that distinguishes the single-H control from the
dynamic-Hs configuration.

Dry-run mode (OMICAIRN_DRY_RUN=1) drives the orchestrator without a real
claude -p subprocess. Dry-run writes only each phase's required
contract files, NOT the optional graph_updates.json — so the ingest
hook is a silent no-op under a plain dry run, which is exactly the
additivity guarantee we want for legacy cases. To exercise ingestion
we drop a graph_updates.json into the stage dir and call the hook.
"""
from __future__ import annotations

import json
import os
import pathlib
import tempfile
from typing import Iterator

import pytest

from omicairn import knowledge_graph as kg, orchestrator, workspace
from omicairn.constants import (
    ENV_DRY_RUN,
    EventKind,
    HypothesisOrigin,
    HypothesisStatus,
    KG_UPDATES_FILE,
    NodeType,
    PhaseName,
)


_MULTI_GOAL = """\
---
research_mode: DISCOVERY
domain: synthetic_smoke_test
topic_slug: pytest-dynh
research_question: |
  Does X associate with Y?
goals:
  - id: g1
    question: "Does X predict Y in cohort A?"
    hypotheses:
      - "H1: X is prognostic for Y"
      - "H2: the X effect is stage-independent"
  - id: g2
    question: "Does the X signal transfer to cohort B?"
    hypotheses:
      - "H3: an A-trained model retains signal on B"
allow_on_the_fly_hypotheses: true
max_on_the_fly_hypotheses: 5
data:
  location: {data_dir}
  description: synthetic pytest fixture
budgets:
  max_phase_wall_minutes: 5
  allow_web: false
  claude_model: claude-sonnet-4-6
  max_phase_budget_usd: 1.0
bounds:
  max_iters_per_phase: 3
  max_backtracks: 5
auto_approve: true
---

# pytest dynamic-H fixture
"""

# Single-hypothesis control: one goal, on-the-fly disabled.
_SINGLE_H = """\
---
research_mode: DISCOVERY
domain: synthetic_smoke_test
topic_slug: pytest-singleh
research_question: |
  Does X associate with Y?
goals:
  - id: g1
    question: "Does X predict Y in cohort A?"
    hypotheses:
      - "H1: X is prognostic for Y"
allow_on_the_fly_hypotheses: false
data:
  location: {data_dir}
  description: synthetic pytest fixture
budgets:
  max_phase_wall_minutes: 5
  allow_web: false
  claude_model: claude-sonnet-4-6
  max_phase_budget_usd: 1.0
bounds:
  max_iters_per_phase: 3
  max_backtracks: 5
auto_approve: true
---

# pytest single-H control fixture
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


def _make_ws(tmpdir: pathlib.Path, template: str, slug: str) -> workspace.Workspace:
    data_dir = tmpdir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "d.csv").write_text("x,y\n1,1\n")
    rmd = tmpdir / "RESEARCH.md"
    rmd.write_text(template.format(data_dir=data_dir))
    ws_root = tmpdir / "workspaces"
    ws_root.mkdir()
    return workspace.init(
        slug, research_md_path=rmd, workspaces_root=ws_root,
        skip_literature=True,
    )


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------

def test_seed_runs_at_run_all_start(dry_run_env):
    """run_all seeds the graph with goals + pre-registered hypotheses
    before the first stage, so even an early halt leaves a skeleton."""
    with tempfile.TemporaryDirectory() as td:
        ws = _make_ws(pathlib.Path(td), _MULTI_GOAL, "pytest-dynh")
        orchestrator.run_all(ws, stop_after=PhaseName.SCOPE, max_stages=2)
        g = kg.load_graph(ws.root)
        goals = g.nodes_of_type(NodeType.GOAL)
        hyps = g.nodes_of_type(NodeType.HYPOTHESIS)
        assert len(goals) == 2
        assert len(hyps) == 3
        assert all(h.origin == HypothesisOrigin.PREREGISTERED for h in hyps)
        assert all(h.status == HypothesisStatus.PROPOSED for h in hyps)


def test_seed_is_idempotent_across_two_runs(dry_run_env):
    with tempfile.TemporaryDirectory() as td:
        ws = _make_ws(pathlib.Path(td), _MULTI_GOAL, "pytest-dynh")
        orchestrator.run_all(ws, stop_after=PhaseName.SCOPE, max_stages=2)
        orchestrator.run_all(ws, stop_after=PhaseName.SCOPE, max_stages=2)
        g = kg.load_graph(ws.root)
        assert len(g.nodes_of_type(NodeType.GOAL)) == 2
        assert len(g.nodes_of_type(NodeType.HYPOTHESIS)) == 3


def test_seed_event_emitted(dry_run_env):
    with tempfile.TemporaryDirectory() as td:
        ws = _make_ws(pathlib.Path(td), _MULTI_GOAL, "pytest-dynh")
        orchestrator.run_all(ws, stop_after=PhaseName.SCOPE, max_stages=2)
        events = [
            json.loads(ln) for ln in
            (ws.root / "events.jsonl").read_text().splitlines() if ln.strip()
        ]
        seeded = [e for e in events if e.get("kind") == EventKind.KNOWLEDGE_GRAPH_SEEDED]
        assert seeded and seeded[0]["goals"] == 2
        assert seeded[0]["preregistered_hypotheses"] == 3


# ---------------------------------------------------------------------------
# Additivity: a stage with no graph_updates.json is a silent no-op
# ---------------------------------------------------------------------------

def test_ingest_no_updates_file_is_noop(dry_run_env):
    with tempfile.TemporaryDirectory() as td:
        ws = _make_ws(pathlib.Path(td), _MULTI_GOAL, "pytest-dynh")
        # Build a stage dir with no graph_updates.json.
        stage = ws.root / "002_execute"
        stage.mkdir()
        orchestrator._ingest_stage_graph_updates(ws, stage, PhaseName.EXECUTE)
        events = [
            json.loads(ln) for ln in
            (ws.root / "events.jsonl").read_text().splitlines() if ln.strip()
        ] if (ws.root / "events.jsonl").exists() else []
        assert not [e for e in events if e.get("kind") == EventKind.KNOWLEDGE_GRAPH_UPDATED]


def test_ingest_ignored_for_non_ingest_phase(dry_run_env):
    with tempfile.TemporaryDirectory() as td:
        ws = _make_ws(pathlib.Path(td), _MULTI_GOAL, "pytest-dynh")
        stage = ws.root / "000_scope"
        stage.mkdir()
        (stage / KG_UPDATES_FILE).write_text(json.dumps({
            "findings": [{"title": "t", "body": "b", "evidence_paths": []}],
        }))
        # SCOPE is not in _GRAPH_INGEST_PHASES → no ingest.
        orchestrator._ingest_stage_graph_updates(ws, stage, PhaseName.SCOPE)
        g = kg.load_graph(ws.root)
        assert len(g.nodes_of_type(NodeType.FINDING)) == 0


# ---------------------------------------------------------------------------
# Real ingestion via the hook
# ---------------------------------------------------------------------------

def test_ingest_folds_updates_and_advances_status(dry_run_env):
    with tempfile.TemporaryDirectory() as td:
        ws = _make_ws(pathlib.Path(td), _MULTI_GOAL, "pytest-dynh")
        orchestrator._seed_knowledge_graph(ws)
        stage = ws.root / "002_execute"
        stage.mkdir()
        (stage / "results.json").write_text('{"auc_a": 0.81}')
        (stage / KG_UPDATES_FILE).write_text(json.dumps({
            "result_refs": [{
                "key": "auc_a", "title": "AUC=0.81", "value": 0.81,
                "evidence_paths": ["002_execute/results.json"],
                "hypothesis": "H1: X is prognostic for Y", "goal_id": "g1",
                "verdict": "supports",
            }],
            "findings": [{
                "title": "unexpected interaction with stage",
                "body": "stage*X term significant",
                "evidence_paths": ["002_execute/results.json"],
            }],
            "on_the_fly_hypotheses": [
                {"statement": "H_new: effect is driven by stage-III only",
                 "goal_id": "g1",
                 "raised_by_finding": "unexpected interaction with stage"},
            ],
        }))
        orchestrator._ingest_stage_graph_updates(ws, stage, PhaseName.EXECUTE)

        g = kg.load_graph(ws.root)
        # H1 advanced to supported (had on-disk evidence)
        h1 = kg.get_node(ws.root, kg.hypothesis_id(
            "H1: X is prognostic for Y", "goal:g1"))
        assert h1.status == HypothesisStatus.SUPPORTED
        # one finding + one on-the-fly hypothesis added
        assert len(g.nodes_of_type(NodeType.FINDING)) == 1
        otf = [h for h in g.nodes_of_type(NodeType.HYPOTHESIS)
               if h.origin == HypothesisOrigin.ON_THE_FLY]
        assert len(otf) == 1
        # event recorded
        events = [
            json.loads(ln) for ln in
            (ws.root / "events.jsonl").read_text().splitlines() if ln.strip()
        ]
        upd = [e for e in events if e.get("kind") == EventKind.KNOWLEDGE_GRAPH_UPDATED]
        assert upd and upd[-1]["on_the_fly"] == 1


# ---------------------------------------------------------------------------
# The single-H vs dynamic-H lever: on-the-fly gating
# ---------------------------------------------------------------------------

def test_single_h_control_suppresses_on_the_fly(dry_run_env):
    """With allow_on_the_fly_hypotheses=false the orchestrator passes
    max_on_the_fly=0, so even a graph_updates.json proposing new
    hypotheses adds none — the control stays at its one seed H."""
    with tempfile.TemporaryDirectory() as td:
        ws = _make_ws(pathlib.Path(td), _SINGLE_H, "pytest-singleh")
        orchestrator._seed_knowledge_graph(ws)
        stage = ws.root / "002_execute"
        stage.mkdir()
        (stage / "results.json").write_text('{"auc_a": 0.81}')
        (stage / KG_UPDATES_FILE).write_text(json.dumps({
            "on_the_fly_hypotheses": [
                {"statement": "H_new: exploratory", "goal_id": "g1"},
                {"statement": "H_new2: also exploratory", "goal_id": "g1"},
            ],
        }))
        orchestrator._ingest_stage_graph_updates(ws, stage, PhaseName.EXECUTE)

        g = kg.load_graph(ws.root)
        hyps = g.nodes_of_type(NodeType.HYPOTHESIS)
        assert len(hyps) == 1  # only the seed H1
        assert all(h.origin == HypothesisOrigin.PREREGISTERED for h in hyps)


def test_dynamic_h_richer_than_single_h(dry_run_env):
    """Same EXECUTE evidence, two configs: the dynamic-Hs workspace's
    graph ends strictly richer (more hypotheses) than the single-H
    control. This is the core demonstration in miniature."""
    same_updates = {
        "result_refs": [{
            "key": "auc_a", "title": "AUC=0.81", "value": 0.81,
            "evidence_paths": ["002_execute/results.json"],
            "hypothesis": "H1: X is prognostic for Y", "goal_id": "g1",
            "verdict": "supports",
        }],
        "findings": [{
            "title": "novel subgroup signal", "body": "...",
            "evidence_paths": ["002_execute/results.json"],
        }],
        "on_the_fly_hypotheses": [
            {"statement": "H_new: subgroup-specific effect", "goal_id": "g1",
             "raised_by_finding": "novel subgroup signal"},
            {"statement": "H_new2: dose-response", "goal_id": "g1"},
        ],
    }

    def _run(template: str, slug: str) -> dict:
        with tempfile.TemporaryDirectory() as td:
            ws = _make_ws(pathlib.Path(td), template, slug)
            orchestrator._seed_knowledge_graph(ws)
            stage = ws.root / "002_execute"
            stage.mkdir()
            (stage / "results.json").write_text('{"auc_a": 0.81}')
            (stage / KG_UPDATES_FILE).write_text(json.dumps(same_updates))
            orchestrator._ingest_stage_graph_updates(ws, stage, PhaseName.EXECUTE)
            return kg.stats(ws.root)

    single = _run(_SINGLE_H, "pytest-singleh")
    dynamic = _run(_MULTI_GOAL, "pytest-dynh")

    # single-H: 1 seed hypothesis, no on-the-fly
    assert single["nodes_by_type"][NodeType.HYPOTHESIS] == 1
    assert single["hypotheses_on_the_fly"] == 0
    # dynamic-Hs: 3 seed + 2 on-the-fly = 5 hypotheses, 2 on-the-fly
    assert dynamic["nodes_by_type"][NodeType.HYPOTHESIS] == 5
    assert dynamic["hypotheses_on_the_fly"] == 2
    # strictly richer
    assert (dynamic["nodes_by_type"][NodeType.HYPOTHESIS]
            > single["nodes_by_type"][NodeType.HYPOTHESIS])


# ---------------------------------------------------------------------------
# Review-from-graph: the projection reaches the analysis REVIEW prompt
# ---------------------------------------------------------------------------

def test_review_prompt_receives_graph_projection(dry_run_env):
    """The {{knowledge_graph_projection}} token in review.md renders the
    populated graph, so REVIEW judges supported/refuted hypotheses across
    goals (core belief 11). Verified by rendering the REVIEW prompt
    directly."""
    from omicairn import phases, stage as stage_mod

    with tempfile.TemporaryDirectory() as td:
        ws = _make_ws(pathlib.Path(td), _MULTI_GOAL, "pytest-dynh")
        orchestrator._seed_knowledge_graph(ws)
        # Populate the graph with a supported H + an on-the-fly H.
        stage = ws.root / "002_execute"
        stage.mkdir()
        (stage / "results.json").write_text('{"auc_a": 0.81}')
        (stage / KG_UPDATES_FILE).write_text(json.dumps({
            "result_refs": [{
                "key": "auc_a", "title": "AUC=0.81", "value": 0.81,
                "evidence_paths": ["002_execute/results.json"],
                "hypothesis": "H1: X is prognostic for Y", "goal_id": "g1",
                "verdict": "supports",
            }],
            "on_the_fly_hypotheses": [
                {"statement": "H_new: subgroup effect", "goal_id": "g1"},
            ],
        }))
        orchestrator._ingest_stage_graph_updates(ws, stage, PhaseName.EXECUTE)

        # Render the REVIEW prompt and assert the projection is present.
        review_phase = phases.get(PhaseName.REVIEW)
        stage_ref = stage_mod.StageRef(
            index=4, phase=PhaseName.REVIEW,
            path=ws.root / "004_review", rel_path="004_review",
        )
        rendered = orchestrator._render_prompt(
            review_phase, ws, stage_ref, iter_count=0, max_iter=3,
            retry_note=None,
        )
        # The token must be substituted (no literal left)…
        assert "{{knowledge_graph_projection}}" not in rendered
        # …and the supported hypothesis + its evidence path must appear.
        assert "[supported]" in rendered
        assert "H1: X is prognostic for Y" in rendered
        assert "002_execute/results.json" in rendered
        # on-the-fly hypothesis tagged exploratory
        assert "post-hoc" in rendered


def test_review_prompt_empty_graph_is_legacy_safe(dry_run_env):
    """When no graph_updates ever landed, the projection renders the
    empty sentinel so REVIEW falls back to its results.json path — the
    legacy / single-hypothesis case is unchanged."""
    from omicairn import phases, stage as stage_mod

    with tempfile.TemporaryDirectory() as td:
        ws = _make_ws(pathlib.Path(td), _SINGLE_H, "pytest-singleh")
        # No seeding, no ingest → empty graph.
        review_phase = phases.get(PhaseName.REVIEW)
        stage_ref = stage_mod.StageRef(
            index=4, phase=PhaseName.REVIEW,
            path=ws.root / "004_review", rel_path="004_review",
        )
        rendered = orchestrator._render_prompt(
            review_phase, ws, stage_ref, iter_count=0, max_iter=3,
            retry_note=None,
        )
        assert "(knowledge graph is empty)" in rendered


# ---------------------------------------------------------------------------
# Serendipity-promotion wiring (creativity audit F1) — the flag gate
# ---------------------------------------------------------------------------

def _seed_promotable(ws_root: pathlib.Path) -> None:
    """Put a supported + cheap-to-test on-the-fly hypothesis in the graph."""
    from omicairn.constants import EdgeRel
    (ws_root / "002_execute").mkdir(parents=True, exist_ok=True)
    (ws_root / "002_execute" / "results.json").write_text('{"e": 0.4}')
    g = kg.add_goal(ws_root, "Q?", explicit_id="g1")
    hyp = kg.add_hypothesis(ws_root, "H-otf: serendipitous effect", g.id,
                            origin=HypothesisOrigin.ON_THE_FLY,
                            cheap_to_test=True)
    res = kg.Node(id=kg.result_ref_id("e"), type=NodeType.RESULT_REF,
                  title="e=0.4", evidence_paths=["002_execute/results.json"])
    kg.upsert_node(ws_root, res)
    kg.upsert_edge(ws_root, kg.Edge(src=res.id, rel=EdgeRel.SUPPORTS, dst=hyp.id))
    kg.set_hypothesis_status(ws_root, hyp.id, HypothesisStatus.TESTING)
    kg.set_hypothesis_status(ws_root, hyp.id, HypothesisStatus.SUPPORTED)


def test_serendipity_promotion_off_by_default(dry_run_env, monkeypatch):
    """With OMICAIRN_ENABLE_SERENDIPITY unset, _maybe_distill_to_lab does NOT
    promote — no direction entry, no SERENDIPITY_PROMOTED event."""
    from omicairn import lab_knowledge
    monkeypatch.delenv("OMICAIRN_ENABLE_SERENDIPITY", raising=False)
    with tempfile.TemporaryDirectory() as td:
        ws = _make_ws(pathlib.Path(td), _MULTI_GOAL, "pytest-dynh")
        _seed_promotable(ws.root)
        orchestrator._maybe_distill_to_lab(ws)
        lab_root = lab_knowledge.resolve_root(workspaces_root=ws.root.parent)
        assert lab_knowledge.query(kind="direction", status="proposed",
                                   root=lab_root) == []
        kinds = [json.loads(l)["kind"]
                 for l in (ws.root / "events.jsonl").read_text().splitlines()]
        assert EventKind.SERENDIPITY_PROMOTED not in kinds


def test_serendipity_promotion_on_when_flag_set(dry_run_env, monkeypatch):
    """With the flag set, _maybe_distill_to_lab promotes the supported
    cheap-to-test on-the-fly discovery to a PROPOSED lab direction + emits
    the canonical event."""
    from omicairn import lab_knowledge
    monkeypatch.setenv("OMICAIRN_ENABLE_SERENDIPITY", "1")
    with tempfile.TemporaryDirectory() as td:
        ws = _make_ws(pathlib.Path(td), _MULTI_GOAL, "pytest-dynh")
        _seed_promotable(ws.root)
        orchestrator._maybe_distill_to_lab(ws)
        lab_root = lab_knowledge.resolve_root(workspaces_root=ws.root.parent)
        dirs = lab_knowledge.query(kind="direction", status="proposed",
                                   root=lab_root)
        assert len(dirs) == 1
        assert dirs[0]["origin"] == "serendipity"
        kinds = [json.loads(l)["kind"]
                 for l in (ws.root / "events.jsonl").read_text().splitlines()]
        assert EventKind.SERENDIPITY_PROMOTED in kinds
