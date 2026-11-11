"""Unit tests for omicairn.knowledge_graph — the per-workspace knowledge
graph that is the durable product of a run (core beliefs 11 & 12).

Covers: node/edge upsert + dedup, op-log fold + round-trip, the
hypothesis lifecycle state machine, the mechanical honesty rule
(supported/refuted require on-disk evidence), the on-the-fly
provenance path, query/traversal, and stats.
"""
from __future__ import annotations

import pathlib

import pytest

from omicairn import knowledge_graph as kg
from omicairn.constants import (
    EdgeRel,
    HypothesisOrigin,
    HypothesisStatus,
    NodeType,
)


def _ws(tmp_path: pathlib.Path) -> pathlib.Path:
    (tmp_path / "knowledge").mkdir(parents=True, exist_ok=True)
    return tmp_path


# ---------------------------------------------------------------------------
# Upsert + dedup + fold
# ---------------------------------------------------------------------------

def test_upsert_node_then_load_round_trips(tmp_path):
    ws = _ws(tmp_path)
    n = kg.Node(id="goal:g1", type=NodeType.GOAL, title="Does X predict Y?")
    kg.upsert_node(ws, n)
    g = kg.load_graph(ws)
    assert "goal:g1" in g.nodes
    assert g.nodes["goal:g1"].title == "Does X predict Y?"
    assert g.nodes["goal:g1"].created_at  # auto-filled
    assert g.nodes["goal:g1"].updated_at


def test_upsert_node_dedups_by_id_last_write_wins(tmp_path):
    ws = _ws(tmp_path)
    kg.upsert_node(ws, kg.Node(id="goal:g1", type=NodeType.GOAL, title="first"))
    kg.upsert_node(ws, kg.Node(id="goal:g1", type=NodeType.GOAL, title="second"))
    g = kg.load_graph(ws)
    assert len(g.nodes) == 1
    assert g.nodes["goal:g1"].title == "second"


def test_upsert_node_preserves_created_at_on_update(tmp_path):
    ws = _ws(tmp_path)
    first = kg.upsert_node(ws, kg.Node(id="goal:g1", type=NodeType.GOAL, title="a"))
    created = first.created_at
    second = kg.upsert_node(ws, kg.Node(id="goal:g1", type=NodeType.GOAL, title="b"))
    assert second.created_at == created  # creation time is sticky
    assert second.updated_at >= created


def test_unknown_node_type_raises(tmp_path):
    ws = _ws(tmp_path)
    with pytest.raises(ValueError, match="unknown node type"):
        kg.upsert_node(ws, kg.Node(id="x:1", type="nonsense", title="t"))


def test_unknown_edge_rel_raises(tmp_path):
    ws = _ws(tmp_path)
    with pytest.raises(ValueError, match="unknown edge rel"):
        kg.upsert_edge(ws, kg.Edge(src="a", rel="bogus", dst="b"))


def test_edge_id_is_deterministic(tmp_path):
    e = kg.Edge(src="a", rel=EdgeRel.TESTS, dst="b")
    assert e.id == "a--tests-->b"


def test_load_graph_skips_malformed_lines(tmp_path):
    ws = _ws(tmp_path)
    kg.upsert_node(ws, kg.Node(id="goal:g1", type=NodeType.GOAL, title="ok"))
    # Simulate a crash mid-write: append a truncated line.
    with kg.graph_path(ws).open("a") as f:
        f.write('{"op": "upsert_node", "node": {"id": "goal:g2"')  # no newline/close
    g = kg.load_graph(ws)  # must not raise
    assert "goal:g1" in g.nodes
    assert "goal:g2" not in g.nodes


def test_load_graph_missing_file_is_empty(tmp_path):
    g = kg.load_graph(tmp_path)
    assert g.nodes == {} and g.edges == {}


# ---------------------------------------------------------------------------
# Helpers: goal / hypothesis / finding
# ---------------------------------------------------------------------------

def test_add_goal_and_hypothesis_links_via_derived_from(tmp_path):
    ws = _ws(tmp_path)
    goal = kg.add_goal(ws, "Does X predict Y?", explicit_id="g1")
    hyp = kg.add_hypothesis(ws, "H1: X is prognostic", goal.id,
                            origin=HypothesisOrigin.PREREGISTERED)
    g = kg.load_graph(ws)
    assert hyp.status == HypothesisStatus.PROPOSED
    assert hyp.origin == HypothesisOrigin.PREREGISTERED
    assert hyp.extra["posthoc"] is False
    # derived_from edge hypothesis -> goal
    derived = g.edges_from(hyp.id, rel=EdgeRel.DERIVED_FROM)
    assert len(derived) == 1 and derived[0].dst == goal.id


def test_on_the_fly_hypothesis_is_posthoc_and_raised_by_finding(tmp_path):
    ws = _ws(tmp_path)
    goal = kg.add_goal(ws, "Goal", explicit_id="g1")
    find = kg.add_finding(ws, "baseline beats FM on cohort B",
                          body="AUC 0.94 vs 0.69", evidence_paths=[])
    hyp = kg.add_hypothesis(
        ws, "H_new: simple baseline generalizes better", goal.id,
        origin=HypothesisOrigin.ON_THE_FLY, raised_by_finding=find.id,
    )
    assert hyp.extra["posthoc"] is True
    g = kg.load_graph(ws)
    raises = g.edges_to(hyp.id, rel=EdgeRel.RAISES)
    assert len(raises) == 1 and raises[0].src == find.id


def test_hypotheses_for_goal_filters(tmp_path):
    ws = _ws(tmp_path)
    g1 = kg.add_goal(ws, "Goal 1", explicit_id="g1")
    g2 = kg.add_goal(ws, "Goal 2", explicit_id="g2")
    kg.add_hypothesis(ws, "H1", g1.id)
    kg.add_hypothesis(ws, "H2", g1.id)
    kg.add_hypothesis(ws, "H3", g2.id)
    assert len(kg.hypotheses_for_goal(ws, g1.id)) == 2
    assert len(kg.hypotheses_for_goal(ws, g2.id)) == 1


# ---------------------------------------------------------------------------
# Hypothesis lifecycle state machine
# ---------------------------------------------------------------------------

def test_legal_transition_proposed_to_testing(tmp_path):
    ws = _ws(tmp_path)
    goal = kg.add_goal(ws, "G", explicit_id="g1")
    hyp = kg.add_hypothesis(ws, "H1", goal.id)
    out = kg.set_hypothesis_status(ws, hyp.id, HypothesisStatus.TESTING)
    assert out.status == HypothesisStatus.TESTING


def test_illegal_transition_proposed_to_supported_raises(tmp_path):
    ws = _ws(tmp_path)
    goal = kg.add_goal(ws, "G", explicit_id="g1")
    hyp = kg.add_hypothesis(ws, "H1", goal.id)
    # proposed -> supported is not a legal direct edge (must go via testing)
    with pytest.raises(ValueError, match="illegal hypothesis transition"):
        kg.set_hypothesis_status(ws, hyp.id, HypothesisStatus.SUPPORTED)


def test_superseded_is_terminal(tmp_path):
    ws = _ws(tmp_path)
    goal = kg.add_goal(ws, "G", explicit_id="g1")
    hyp = kg.add_hypothesis(ws, "H1", goal.id)
    kg.set_hypothesis_status(ws, hyp.id, HypothesisStatus.SUPERSEDED,
                             superseded_by="hypothesis:better")
    with pytest.raises(ValueError, match="illegal hypothesis transition"):
        kg.set_hypothesis_status(ws, hyp.id, HypothesisStatus.TESTING)


def test_set_status_on_missing_node_raises(tmp_path):
    ws = _ws(tmp_path)
    with pytest.raises(KeyError):
        kg.set_hypothesis_status(ws, "hypothesis:nope", HypothesisStatus.TESTING)


def test_set_status_on_non_hypothesis_raises(tmp_path):
    ws = _ws(tmp_path)
    goal = kg.add_goal(ws, "G", explicit_id="g1")
    with pytest.raises(ValueError, match="not a hypothesis"):
        kg.set_hypothesis_status(ws, goal.id, HypothesisStatus.TESTING)


# ---------------------------------------------------------------------------
# The mechanical honesty rule
# ---------------------------------------------------------------------------

def test_cannot_support_hypothesis_without_evidence(tmp_path):
    ws = _ws(tmp_path)
    goal = kg.add_goal(ws, "G", explicit_id="g1")
    hyp = kg.add_hypothesis(ws, "H1", goal.id)
    kg.set_hypothesis_status(ws, hyp.id, HypothesisStatus.TESTING)
    # No supports edge yet -> honesty rule blocks
    with pytest.raises(ValueError, match="honesty rule"):
        kg.set_hypothesis_status(ws, hyp.id, HypothesisStatus.SUPPORTED)


def test_can_support_hypothesis_with_on_disk_evidence(tmp_path):
    ws = _ws(tmp_path)
    # Create a real evidence file inside the workspace.
    (ws / "002_execute").mkdir(parents=True)
    ev = ws / "002_execute" / "results.json"
    ev.write_text('{"auc": 0.81}')

    goal = kg.add_goal(ws, "G", explicit_id="g1")
    hyp = kg.add_hypothesis(ws, "H1", goal.id)
    kg.set_hypothesis_status(ws, hyp.id, HypothesisStatus.TESTING)

    res = kg.Node(
        id=kg.result_ref_id("auc"), type=NodeType.RESULT_REF,
        title="auc=0.81", evidence_paths=["002_execute/results.json"],
    )
    kg.upsert_node(ws, res)
    kg.upsert_edge(ws, kg.Edge(src=res.id, rel=EdgeRel.SUPPORTS, dst=hyp.id))
    kg.upsert_edge(ws, kg.Edge(src=hyp.id, rel=EdgeRel.TESTS, dst=res.id))

    out = kg.set_hypothesis_status(ws, hyp.id, HypothesisStatus.SUPPORTED)
    assert out.status == HypothesisStatus.SUPPORTED


def test_support_edge_with_nonexistent_evidence_is_rejected(tmp_path):
    ws = _ws(tmp_path)
    goal = kg.add_goal(ws, "G", explicit_id="g1")
    hyp = kg.add_hypothesis(ws, "H1", goal.id)
    kg.set_hypothesis_status(ws, hyp.id, HypothesisStatus.TESTING)
    res = kg.Node(
        id=kg.result_ref_id("auc"), type=NodeType.RESULT_REF,
        title="auc", evidence_paths=["002_execute/does_not_exist.json"],
    )
    kg.upsert_node(ws, res)
    kg.upsert_edge(ws, kg.Edge(src=res.id, rel=EdgeRel.SUPPORTS, dst=hyp.id))
    with pytest.raises(ValueError, match="honesty rule"):
        kg.set_hypothesis_status(ws, hyp.id, HypothesisStatus.SUPPORTED)


def test_honesty_rule_rejects_directory_as_evidence(tmp_path):
    """REGRESSION (adversarial audit #1): the agent controls
    evidence_paths, so pointing at the stage DIRECTORY (which always
    exists) must NOT license a supported verdict. Only real non-empty
    files count."""
    ws = _ws(tmp_path)
    (ws / "002_execute").mkdir(parents=True)  # a directory, always exists
    goal = kg.add_goal(ws, "G", explicit_id="g1")
    hyp = kg.add_hypothesis(ws, "H1", goal.id)
    kg.set_hypothesis_status(ws, hyp.id, HypothesisStatus.TESTING)
    res = kg.Node(id=kg.result_ref_id("x"), type=NodeType.RESULT_REF,
                  title="x", evidence_paths=["002_execute"])  # the DIR
    kg.upsert_node(ws, res)
    kg.upsert_edge(ws, kg.Edge(src=res.id, rel=EdgeRel.SUPPORTS, dst=hyp.id))
    with pytest.raises(kg.HonestyRuleError):
        kg.set_hypothesis_status(ws, hyp.id, HypothesisStatus.SUPPORTED)


def test_honesty_rule_rejects_empty_file_as_evidence(tmp_path):
    """REGRESSION (#1): a 0-byte placeholder is not evidence."""
    ws = _ws(tmp_path)
    (ws / "002_execute").mkdir(parents=True)
    (ws / "002_execute" / "empty.json").write_text("")  # 0 bytes
    goal = kg.add_goal(ws, "G", explicit_id="g1")
    hyp = kg.add_hypothesis(ws, "H1", goal.id)
    kg.set_hypothesis_status(ws, hyp.id, HypothesisStatus.TESTING)
    res = kg.Node(id=kg.result_ref_id("x"), type=NodeType.RESULT_REF,
                  title="x", evidence_paths=["002_execute/empty.json"])
    kg.upsert_node(ws, res)
    kg.upsert_edge(ws, kg.Edge(src=res.id, rel=EdgeRel.SUPPORTS, dst=hyp.id))
    with pytest.raises(kg.HonestyRuleError):
        kg.set_hypothesis_status(ws, hyp.id, HypothesisStatus.SUPPORTED)


def test_honesty_rule_rejects_absolute_path_outside_workspace(tmp_path):
    """REGRESSION (#1): an absolute path to a real file OUTSIDE the
    workspace (e.g. /etc/hostname) must not count — evidence must live
    under the workspace root."""
    ws = _ws(tmp_path)
    # A real, non-empty file outside the workspace.
    outside = tmp_path.parent / "outside_evidence.json"
    outside.write_text('{"fake": 1}')
    try:
        goal = kg.add_goal(ws, "G", explicit_id="g1")
        hyp = kg.add_hypothesis(ws, "H1", goal.id)
        kg.set_hypothesis_status(ws, hyp.id, HypothesisStatus.TESTING)
        res = kg.Node(id=kg.result_ref_id("x"), type=NodeType.RESULT_REF,
                      title="x", evidence_paths=[str(outside.resolve())])
        kg.upsert_node(ws, res)
        kg.upsert_edge(ws, kg.Edge(src=res.id, rel=EdgeRel.SUPPORTS, dst=hyp.id))
        with pytest.raises(kg.HonestyRuleError):
            kg.set_hypothesis_status(ws, hyp.id, HypothesisStatus.SUPPORTED)
    finally:
        outside.unlink(missing_ok=True)


def test_honesty_rule_rejects_dotdot_traversal(tmp_path):
    """REGRESSION (#1): a `..` escape to a file outside the workspace
    must not count."""
    ws = _ws(tmp_path)
    outside = tmp_path.parent / "trav_evidence.json"
    outside.write_text('{"fake": 1}')
    try:
        goal = kg.add_goal(ws, "G", explicit_id="g1")
        hyp = kg.add_hypothesis(ws, "H1", goal.id)
        kg.set_hypothesis_status(ws, hyp.id, HypothesisStatus.TESTING)
        res = kg.Node(id=kg.result_ref_id("x"), type=NodeType.RESULT_REF,
                      title="x", evidence_paths=["../trav_evidence.json"])
        kg.upsert_node(ws, res)
        kg.upsert_edge(ws, kg.Edge(src=res.id, rel=EdgeRel.SUPPORTS, dst=hyp.id))
        with pytest.raises(kg.HonestyRuleError):
            kg.set_hypothesis_status(ws, hyp.id, HypothesisStatus.SUPPORTED)
    finally:
        outside.unlink(missing_ok=True)


def test_illegal_transition_raises_distinct_error_not_honesty(tmp_path):
    """REGRESSION (adversarial audit #2): a supported→inconclusive
    re-ingest is an illegal transition, NOT an honesty failure — it must
    raise IllegalTransitionError so the warning isn't mislabeled."""
    ws = _ws(tmp_path)
    (ws / "002_execute").mkdir(parents=True)
    (ws / "002_execute" / "results.json").write_text('{"auc": 0.8}')
    goal = kg.add_goal(ws, "G", explicit_id="g1")
    hyp = kg.add_hypothesis(ws, "H1", goal.id)
    res = kg.Node(id=kg.result_ref_id("x"), type=NodeType.RESULT_REF,
                  title="x", evidence_paths=["002_execute/results.json"])
    kg.upsert_node(ws, res)
    kg.upsert_edge(ws, kg.Edge(src=res.id, rel=EdgeRel.SUPPORTS, dst=hyp.id))
    kg.set_hypothesis_status(ws, hyp.id, HypothesisStatus.TESTING)
    kg.set_hypothesis_status(ws, hyp.id, HypothesisStatus.SUPPORTED)
    # Now try the illegal supported -> inconclusive.
    with pytest.raises(kg.IllegalTransitionError):
        kg.set_hypothesis_status(ws, hyp.id, HypothesisStatus.INCONCLUSIVE)
    # And it must NOT be (mis)classified as an honesty error.
    assert not isinstance(
        kg.IllegalTransitionError("x"), kg.HonestyRuleError
    )


def test_refute_requires_refutes_edge_not_supports(tmp_path):
    ws = _ws(tmp_path)
    (ws / "002_execute").mkdir(parents=True)
    (ws / "002_execute" / "results.json").write_text("{}")
    goal = kg.add_goal(ws, "G", explicit_id="g1")
    hyp = kg.add_hypothesis(ws, "H1", goal.id)
    kg.set_hypothesis_status(ws, hyp.id, HypothesisStatus.TESTING)
    res = kg.Node(id=kg.result_ref_id("x"), type=NodeType.RESULT_REF,
                  title="x", evidence_paths=["002_execute/results.json"])
    kg.upsert_node(ws, res)
    # Only a SUPPORTS edge present; trying to REFUTE must fail.
    kg.upsert_edge(ws, kg.Edge(src=res.id, rel=EdgeRel.SUPPORTS, dst=hyp.id))
    with pytest.raises(ValueError, match="honesty rule"):
        kg.set_hypothesis_status(ws, hyp.id, HypothesisStatus.REFUTED)


# ---------------------------------------------------------------------------
# Query, traversal, stats
# ---------------------------------------------------------------------------

def test_query_nodes_filters_by_type_status_origin_text(tmp_path):
    ws = _ws(tmp_path)
    goal = kg.add_goal(ws, "G", explicit_id="g1")
    kg.add_hypothesis(ws, "alpha effect", goal.id,
                      origin=HypothesisOrigin.PREREGISTERED)
    kg.add_hypothesis(ws, "beta effect", goal.id,
                      origin=HypothesisOrigin.ON_THE_FLY)
    assert len(kg.query_nodes(ws, node_type=NodeType.HYPOTHESIS)) == 2
    assert len(kg.query_nodes(ws, node_type=NodeType.GOAL)) == 1
    assert len(kg.query_nodes(ws, origin=HypothesisOrigin.ON_THE_FLY)) == 1
    assert len(kg.query_nodes(ws, text="alpha")) == 1


def test_ancestry_walks_derived_from(tmp_path):
    ws = _ws(tmp_path)
    goal = kg.add_goal(ws, "G", explicit_id="g1")
    hyp = kg.add_hypothesis(ws, "H1", goal.id)
    chain = kg.ancestry(ws, hyp.id, rel=EdgeRel.DERIVED_FROM)
    assert chain == [goal.id]


def test_stats_counts_everything(tmp_path):
    ws = _ws(tmp_path)
    (ws / "002_execute").mkdir(parents=True)
    (ws / "002_execute" / "results.json").write_text("{}")
    goal = kg.add_goal(ws, "G", explicit_id="g1")
    h1 = kg.add_hypothesis(ws, "H1", goal.id,
                           origin=HypothesisOrigin.PREREGISTERED)
    h2 = kg.add_hypothesis(ws, "H2", goal.id,
                           origin=HypothesisOrigin.ON_THE_FLY)
    res = kg.Node(id=kg.result_ref_id("x"), type=NodeType.RESULT_REF,
                  title="x", evidence_paths=["002_execute/results.json"])
    kg.upsert_node(ws, res)
    kg.upsert_edge(ws, kg.Edge(src=h1.id, rel=EdgeRel.TESTS, dst=res.id))
    kg.upsert_edge(ws, kg.Edge(src=res.id, rel=EdgeRel.SUPPORTS, dst=h1.id))
    kg.set_hypothesis_status(ws, h1.id, HypothesisStatus.TESTING)
    kg.set_hypothesis_status(ws, h1.id, HypothesisStatus.SUPPORTED)

    s = kg.stats(ws)
    assert s["nodes_by_type"][NodeType.HYPOTHESIS] == 2
    assert s["nodes_by_type"][NodeType.GOAL] == 1
    assert s["hypotheses_on_the_fly"] == 1
    assert s["hypotheses_tested"] == 1          # only h1 has a tests edge
    assert s["hypotheses_by_status"][HypothesisStatus.SUPPORTED] == 1
    assert s["hypotheses_by_status"][HypothesisStatus.PROPOSED] == 1  # h2
    assert s["edges_by_rel"][EdgeRel.TESTS] == 1


# ---------------------------------------------------------------------------
# seed_goals
# ---------------------------------------------------------------------------

def test_seed_goals_creates_goal_and_hypothesis_nodes(tmp_path):
    ws = _ws(tmp_path)
    goals = [
        {"id": "g1", "question": "Q1", "hypotheses": ["H1", "H2"]},
        {"id": "g2", "question": "Q2", "hypotheses": ["H3"]},
    ]
    counts = kg.seed_goals(ws, goals)
    assert counts == {"goals": 2, "hypotheses": 3}
    s = kg.stats(ws)
    assert s["nodes_by_type"][NodeType.GOAL] == 2
    assert s["nodes_by_type"][NodeType.HYPOTHESIS] == 3
    # all seeded hypotheses are preregistered
    assert s["hypotheses_on_the_fly"] == 0


def test_seed_goals_is_idempotent(tmp_path):
    ws = _ws(tmp_path)
    goals = [{"id": "g1", "question": "Q1", "hypotheses": ["H1"]}]
    kg.seed_goals(ws, goals)
    kg.seed_goals(ws, goals)  # again
    s = kg.stats(ws)
    assert s["nodes_by_type"][NodeType.GOAL] == 1
    assert s["nodes_by_type"][NodeType.HYPOTHESIS] == 1


def test_seed_goals_does_not_clobber_advanced_status(tmp_path):
    """REGRESSION (audit C1, Critical): seed_goals runs at the start of
    EVERY `omicairn run`. A pre-registered hypothesis advanced to supported
    in a prior run must NOT be reset to proposed when seed_goals runs
    again on resume — that corrupted the durable graph for confirmatory
    hypotheses."""
    ws = _ws(tmp_path)
    (ws / "002_execute").mkdir(parents=True)
    (ws / "002_execute" / "results.json").write_text('{"auc": 0.8}')
    goals = [{"id": "g1", "question": "Q1", "hypotheses": ["H1: prognostic"]}]
    kg.seed_goals(ws, goals)
    hid = kg.hypothesis_id("H1: prognostic", "goal:g1")
    res = kg.Node(id=kg.result_ref_id("auc"), type=NodeType.RESULT_REF,
                  title="auc", evidence_paths=["002_execute/results.json"])
    kg.upsert_node(ws, res)
    kg.upsert_edge(ws, kg.Edge(src=res.id, rel=EdgeRel.SUPPORTS, dst=hid))
    kg.set_hypothesis_status(ws, hid, HypothesisStatus.TESTING)
    kg.set_hypothesis_status(ws, hid, HypothesisStatus.SUPPORTED)
    assert kg.get_node(ws, hid).status == HypothesisStatus.SUPPORTED
    # Re-seed (simulates the next `omicairn run`): status must survive.
    kg.seed_goals(ws, goals)
    assert kg.get_node(ws, hid).status == HypothesisStatus.SUPPORTED


def test_honesty_rule_rejects_workspace_input_file_as_evidence(tmp_path):
    """REGRESSION (audit H3, High): a real, non-empty, in-workspace file
    that is a workspace INPUT (RESEARCH.md, omicairn_config.yaml, data/*) and
    not a stage OUTPUT must not count as evidence — else an agent moves a
    hypothesis to supported with zero computation by citing RESEARCH.md."""
    ws = _ws(tmp_path)
    (ws / "RESEARCH.md").write_text("---\nresearch_question: q\n---\n")
    (ws / "data").mkdir()
    (ws / "data" / "input.csv").write_text("x,y\n1,2\n")
    goal = kg.add_goal(ws, "G", explicit_id="g1")
    hyp = kg.add_hypothesis(ws, "H1", goal.id)
    kg.set_hypothesis_status(ws, hyp.id, HypothesisStatus.TESTING)
    for bad in ["RESEARCH.md", "data/input.csv"]:
        res = kg.Node(id=kg.result_ref_id(bad), type=NodeType.RESULT_REF,
                      title=bad, evidence_paths=[bad])
        kg.upsert_node(ws, res)
        kg.upsert_edge(ws, kg.Edge(src=res.id, rel=EdgeRel.SUPPORTS, dst=hyp.id))
    with pytest.raises(kg.HonestyRuleError):
        kg.set_hypothesis_status(ws, hyp.id, HypothesisStatus.SUPPORTED)


def test_honesty_rule_accepts_stage_output_evidence(tmp_path):
    """Complement to H3: a file inside a numbered stage dir IS valid
    evidence (the legitimate case must still pass)."""
    ws = _ws(tmp_path)
    (ws / "002_execute").mkdir(parents=True)
    (ws / "002_execute" / "results.json").write_text('{"auc": 0.8}')
    goal = kg.add_goal(ws, "G", explicit_id="g1")
    hyp = kg.add_hypothesis(ws, "H1", goal.id)
    kg.set_hypothesis_status(ws, hyp.id, HypothesisStatus.TESTING)
    res = kg.Node(id=kg.result_ref_id("auc"), type=NodeType.RESULT_REF,
                  title="auc", evidence_paths=["002_execute/results.json"])
    kg.upsert_node(ws, res)
    kg.upsert_edge(ws, kg.Edge(src=res.id, rel=EdgeRel.SUPPORTS, dst=hyp.id))
    out = kg.set_hypothesis_status(ws, hyp.id, HypothesisStatus.SUPPORTED)
    assert out.status == HypothesisStatus.SUPPORTED


def test_paper_projection_excludes_unadopted_directions(tmp_path):
    """REGRESSION (audit H1, High): un-adopted IDEATE directions are GOAL
    nodes but must NOT appear as goals in paper_projection — only
    real goals + adopted directions do."""
    ws = _ws(tmp_path)
    kg.add_goal(ws, "real research goal", explicit_id="g1")
    kg.ingest_ideation(ws, {
        "directions": [
            {"id": "d1", "question": "brainstorm direction one?",
             "hypotheses": ["Hx"], "novelty": "high"},
        ],
    }, source_stage="000_ideate")
    proj = kg.paper_projection(ws)
    qs = [g["question"] for g in proj["goals"]]
    assert "real research goal" in qs
    assert "brainstorm direction one?" not in qs   # un-adopted excluded
    # after adoption it appears
    kg.adopt_direction(ws, "d1")
    qs2 = [g["question"] for g in kg.paper_projection(ws)["goals"]]
    assert "brainstorm direction one?" in qs2


# ---------------------------------------------------------------------------
# ingest_graph_updates — assembling the KB on the run
# ---------------------------------------------------------------------------

def test_ingest_supports_preregistered_hypothesis_with_evidence(tmp_path):
    ws = _ws(tmp_path)
    (ws / "002_execute").mkdir(parents=True)
    (ws / "002_execute" / "results.json").write_text('{"auc": 0.81}')
    kg.seed_goals(ws, [{"id": "g1", "question": "Q1",
                        "hypotheses": ["H1: X is prognostic"]}])
    updates = {
        "result_refs": [{
            "key": "auc", "title": "AUC=0.81", "value": 0.81,
            "evidence_paths": ["002_execute/results.json"],
            "hypothesis": "H1: X is prognostic", "goal_id": "g1",
            "verdict": "supports",
        }],
    }
    counts = kg.ingest_graph_updates(ws, updates, source_phase="execute",
                                     source_stage="002_execute")
    assert counts["result_refs"] == 1
    assert counts["status_advanced"] == 1
    assert counts["warnings"] == []
    hid = kg.hypothesis_id("H1: X is prognostic", "goal:g1")
    assert kg.get_node(ws, hid).status == HypothesisStatus.SUPPORTED


def test_ingest_blocks_support_without_evidence_keeps_testing(tmp_path):
    ws = _ws(tmp_path)
    kg.seed_goals(ws, [{"id": "g1", "question": "Q1", "hypotheses": ["H1"]}])
    updates = {
        "result_refs": [{
            "key": "auc", "title": "AUC", "value": 0.5,
            "evidence_paths": ["002_execute/missing.json"],  # not on disk
            "hypothesis": "H1", "goal_id": "g1", "verdict": "supports",
        }],
    }
    counts = kg.ingest_graph_updates(ws, updates, source_phase="execute",
                                     source_stage="002_execute")
    assert counts["status_advanced"] == 0
    assert any("honesty rule" in w for w in counts["warnings"])
    hid = kg.hypothesis_id("H1", "goal:g1")
    # advanced to testing, but NOT supported
    assert kg.get_node(ws, hid).status == HypothesisStatus.TESTING


def test_ingest_registers_on_the_fly_hypotheses_and_findings(tmp_path):
    ws = _ws(tmp_path)
    kg.seed_goals(ws, [{"id": "g1", "question": "Q1", "hypotheses": ["H1"]}])
    updates = {
        "findings": [{
            "title": "baseline beats FM on cohort B",
            "body": "AUC 0.94 vs 0.69",
            "evidence_paths": ["002_execute/results.json"],
            "tags": ["transfer"],
        }],
        "on_the_fly_hypotheses": [
            {"statement": "H_new: simple baseline generalizes better",
             "goal_id": "g1",
             "raised_by_finding": "baseline beats FM on cohort B"},
            {"statement": "H_new2: interaction term matters", "goal_id": "g1"},
        ],
    }
    counts = kg.ingest_graph_updates(ws, updates, source_phase="execute",
                                     source_stage="002_execute")
    assert counts["findings"] == 1
    assert counts["on_the_fly"] == 2
    s = kg.stats(ws)
    # 1 preregistered (H1) + 2 on-the-fly = 3 hypotheses
    assert s["nodes_by_type"][NodeType.HYPOTHESIS] == 3
    assert s["hypotheses_on_the_fly"] == 2
    # the raises edge from the finding exists
    assert s["edges_by_rel"][EdgeRel.RAISES] == 1


def test_ingest_caps_on_the_fly_at_max(tmp_path):
    ws = _ws(tmp_path)
    kg.seed_goals(ws, [{"id": "g1", "question": "Q1", "hypotheses": []}])
    updates = {
        "on_the_fly_hypotheses": [
            {"statement": f"H{i}", "goal_id": "g1"} for i in range(10)
        ],
    }
    counts = kg.ingest_graph_updates(ws, updates, source_phase="execute",
                                     source_stage="002_execute",
                                     max_on_the_fly=3)
    assert counts["on_the_fly"] == 3
    assert any("capped at 3" in w for w in counts["warnings"])


def test_ingest_unknown_hypothesis_in_result_becomes_on_the_fly(tmp_path):
    ws = _ws(tmp_path)
    (ws / "002_execute").mkdir(parents=True)
    (ws / "002_execute" / "results.json").write_text("{}")
    kg.seed_goals(ws, [{"id": "g1", "question": "Q1", "hypotheses": ["H1"]}])
    updates = {
        "result_refs": [{
            "key": "novel_metric", "title": "novel", "value": 1.0,
            "evidence_paths": ["002_execute/results.json"],
            "hypothesis": "H_unseen: not pre-registered", "goal_id": "g1",
            "verdict": "supports",
        }],
    }
    counts = kg.ingest_graph_updates(ws, updates, source_phase="execute",
                                     source_stage="002_execute")
    # the unseen hypothesis was auto-registered as on-the-fly, then supported
    assert counts["on_the_fly"] == 1
    assert counts["status_advanced"] == 1
    hid = kg.hypothesis_id("H_unseen: not pre-registered", "goal:g1")
    n = kg.get_node(ws, hid)
    assert n.origin == "on_the_fly" and n.status == HypothesisStatus.SUPPORTED


def test_ingest_skips_malformed_entries(tmp_path):
    ws = _ws(tmp_path)
    kg.seed_goals(ws, [{"id": "g1", "question": "Q1", "hypotheses": []}])
    updates = {
        "result_refs": [{"no_key": 1}],
        "findings": [{"no_title": 1}],
        "on_the_fly_hypotheses": [{"no_statement": 1}],
    }
    counts = kg.ingest_graph_updates(ws, updates, source_phase="execute",
                                     source_stage="002_execute")
    assert counts["result_refs"] == 0
    assert counts["findings"] == 0
    assert counts["on_the_fly"] == 0
    assert len(counts["warnings"]) == 3


def test_ingest_on_the_fly_does_not_clobber_advanced_status(tmp_path):
    """REGRESSION: when a result_ref both introduces an on-the-fly
    hypothesis AND advances it to supported, a duplicate entry in the
    on_the_fly_hypotheses list must NOT reset it back to proposed.

    Caught by the #49 e2e harness: add_hypothesis always writes
    status=proposed, so re-adding an already-advanced hypothesis
    clobbered it. The on-the-fly loop now skips existing ids."""
    ws = _ws(tmp_path)
    (ws / "002_execute").mkdir(parents=True)
    (ws / "002_execute" / "results.json").write_text("{}")
    kg.seed_goals(ws, [{"id": "g1", "question": "Q", "hypotheses": []}])
    updates = {
        "findings": [{"title": "F", "body": "b",
                      "evidence_paths": ["002_execute/results.json"]}],
        "result_refs": [{
            "key": "k", "title": "k", "value": 1.0,
            "evidence_paths": ["002_execute/results.json"],
            "hypothesis": "H_x: discovered effect", "goal_id": "g1",
            "verdict": "supports",
        }],
        # SAME hypothesis also listed as on-the-fly (the realistic case:
        # the agent reports it both as a tested result and a new H).
        "on_the_fly_hypotheses": [
            {"statement": "H_x: discovered effect", "goal_id": "g1",
             "raised_by_finding": "F"},
        ],
    }
    counts = kg.ingest_graph_updates(ws, updates, source_phase="execute",
                                     source_stage="002_execute")
    hid = kg.hypothesis_id("H_x: discovered effect", "goal:g1")
    n = kg.get_node(ws, hid)
    assert n.status == HypothesisStatus.SUPPORTED  # NOT clobbered to proposed
    # counted once, not twice
    assert counts["on_the_fly"] == 1
    # the raises edge from the finding still got wired
    g = kg.load_graph(ws)
    assert g.edges_to(hid, rel=EdgeRel.RAISES)


# ---------------------------------------------------------------------------
# paper_projection — the read side of "results are a projection of the graph"
# ---------------------------------------------------------------------------

def _ws_with_resolved_graph(tmp_path):
    """A workspace whose graph has: g1 with H1 supported (on-disk
    evidence), g2 with H3 still proposed, plus one on-the-fly H and one
    unaffiliated finding."""
    ws = _ws(tmp_path)
    (ws / "002_execute").mkdir(parents=True)
    (ws / "002_execute" / "results.json").write_text('{"auc": 0.81}')
    kg.seed_goals(ws, [
        {"id": "g1", "question": "Q1", "hypotheses": ["H1: X prognostic"]},
        {"id": "g2", "question": "Q2", "hypotheses": ["H3: transfers to B"]},
    ])
    kg.ingest_graph_updates(ws, {
        "result_refs": [{
            "key": "auc", "title": "AUC=0.81", "value": 0.81,
            "evidence_paths": ["002_execute/results.json"],
            "hypothesis": "H1: X prognostic", "goal_id": "g1",
            "verdict": "supports",
        }],
        "findings": [{
            "title": "orphan signal", "body": "noticed but untested",
            "evidence_paths": ["002_execute/results.json"],
        }],
        "on_the_fly_hypotheses": [
            {"statement": "H_new: subgroup effect", "goal_id": "g1"},
        ],
    }, source_phase="execute", source_stage="002_execute")
    return ws


def test_paper_projection_groups_by_goal_and_status(tmp_path):
    ws = _ws_with_resolved_graph(tmp_path)
    proj = kg.paper_projection(ws)
    assert [g["id"] for g in proj["goals"]] == ["goal:g1", "goal:g2"]
    g1 = proj["goals"][0]
    # g1 has H1 (supported) + H_new (on-the-fly, proposed)
    statuses = [h["status"] for h in g1["hypotheses"]]
    # supported leads (status ordering)
    assert statuses[0] == HypothesisStatus.SUPPORTED
    h1 = g1["hypotheses"][0]
    assert h1["evidence"] and h1["evidence"][0]["rel"] == EdgeRel.SUPPORTS
    assert "002_execute/results.json" in h1["evidence"][0]["evidence_paths"]


def test_paper_projection_counts(tmp_path):
    ws = _ws_with_resolved_graph(tmp_path)
    c = kg.paper_projection(ws)["counts"]
    assert c["supported"] == 1
    assert c["on_the_fly"] == 1
    assert c["preregistered"] == 2  # H1 + H3
    # H3 still proposed, H_new proposed
    assert c["proposed"] == 2


def test_paper_projection_orders_preregistered_before_on_the_fly(tmp_path):
    ws = _ws_with_resolved_graph(tmp_path)
    g1 = kg.paper_projection(ws)["goals"][0]
    # within g1: H1 (supported, prereg) before H_new (proposed, on-the-fly)
    origins = [(h["status"], h["origin"]) for h in g1["hypotheses"]]
    assert origins[0] == (HypothesisStatus.SUPPORTED, HypothesisOrigin.PREREGISTERED)


def test_render_paper_projection_md_empty_graph(tmp_path):
    ws = _ws(tmp_path)
    assert kg.render_paper_projection_md(ws) == "(knowledge graph is empty)"


def test_render_paper_projection_md_has_status_and_paths(tmp_path):
    ws = _ws_with_resolved_graph(tmp_path)
    md = kg.render_paper_projection_md(ws)
    assert "[supported]" in md
    assert "002_execute/results.json" in md
    assert "post-hoc" in md  # the on-the-fly H is tagged
    assert "Goal g1" in md and "Goal g2" in md


def test_paper_projection_unaffiliated_findings(tmp_path):
    ws = _ws_with_resolved_graph(tmp_path)
    proj = kg.paper_projection(ws)
    # "orphan signal" raised no hypothesis → unaffiliated
    titles = [f["title"] for f in proj["unaffiliated_findings"]]
    assert "orphan signal" in titles
