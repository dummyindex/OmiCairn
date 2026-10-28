"""Unit tests for the IDEATE (brainstorm) layer of the knowledge graph:
external `source` nodes, ingestion of a directions.json payload into
proposed-direction goal nodes, the cites→source provenance edges, the
adopt path (proposed → active goal with materialized hypotheses), and
the human-readable renderer.

See docs/design-docs/dynamic-hypotheses.md and prompts/ideate.md.
"""
from __future__ import annotations

import pathlib

import pytest

from omicairn import knowledge_graph as kg
from omicairn.constants import (
    EdgeRel,
    HypothesisOrigin,
    NodeType,
    SourceType,
)


def _ws(tmp_path: pathlib.Path) -> pathlib.Path:
    (tmp_path / "knowledge").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _payload() -> dict:
    return {
        "sources": [
            {"title": "Anthropic circuits thread", "source_type": "blog",
             "url": "https://transformer-circuits.pub", "author": "Olah et al.",
             "year": 2024, "summary": "mechanistic interpretability program"},
            {"title": "Bengio consciousness prior", "source_type": "professor",
             "author": "Y. Bengio", "year": 2017,
             "summary": "sparse factor graph of high-level concepts"},
            {"title": "Feynman: what I cannot create I do not understand",
             "source_type": "nobel", "author": "R. Feynman"},
        ],
        "directions": [
            {"id": "d1",
             "title": "Cross-modal feature attribution",
             "question": "Do MLLMs share circuits across vision and text?",
             "rationale": "circuits thread shows monosemantic features; "
                          "untested across modalities",
             "hypotheses": ["H1: shared features exist for cross-modal concepts",
                            "H2: ablating them degrades both modalities"],
             "cites": ["Anthropic circuits thread", "Bengio consciousness prior"],
             "novelty": "high", "feasibility": "medium"},
            {"id": "d2",
             "title": "Generative-then-verify",
             "question": "Can we verify a synthetic feature by re-creating it?",
             "rationale": "Feynman test applied to learned features",
             "hypotheses": ["H3: reconstruction error localizes the feature"],
             "cites": ["Feynman: what I cannot create I do not understand"],
             "novelty": "medium", "feasibility": "high"},
        ],
    }


def test_add_source_sets_type_and_metadata(tmp_path):
    ws = _ws(tmp_path)
    n = kg.add_source(ws, "Some paper", source_type="paper",
                      url="http://x", author="A", year=2020, summary="s")
    assert n.type == NodeType.SOURCE
    assert n.extra["source_type"] == SourceType.PAPER
    assert n.extra["url"] == "http://x"
    assert n.extra["year"] == 2020


def test_add_source_unknown_type_falls_back_to_other(tmp_path):
    ws = _ws(tmp_path)
    n = kg.add_source(ws, "X", source_type="bogus")
    assert n.extra["source_type"] == SourceType.OTHER


def test_ingest_ideation_creates_sources_directions_and_cites(tmp_path):
    ws = _ws(tmp_path)
    counts = kg.ingest_ideation(ws, _payload(), source_stage="000_ideate")
    assert counts["sources"] == 3
    assert counts["directions"] == 2
    assert counts["cite_edges"] == 3  # d1->2 sources, d2->1 source
    assert counts["warnings"] == []
    g = kg.load_graph(ws)
    assert len(g.nodes_of_type(NodeType.SOURCE)) == 3
    # directions are goal nodes flagged kind=direction
    dirs = [n for n in g.nodes_of_type(NodeType.GOAL)
            if n.extra.get("kind") == "direction"]
    assert len(dirs) == 2


def test_proposed_directions_resolves_sources_and_orders_by_novelty(tmp_path):
    ws = _ws(tmp_path)
    kg.ingest_ideation(ws, _payload(), source_stage="000_ideate")
    dirs = kg.proposed_directions(ws)
    assert len(dirs) == 2
    # high-novelty d1 first (un-adopted, novelty high < medium)
    assert dirs[0]["novelty"] == "high"
    assert dirs[0]["label"] == "Cross-modal feature attribution"
    # cited sources resolved with type
    types = {s["source_type"] for s in dirs[0]["sources"]}
    assert SourceType.BLOG in types and SourceType.PROFESSOR in types
    assert all(d["adopted"] is False for d in dirs)


def test_ideated_directions_do_not_pollute_hypothesis_space(tmp_path):
    ws = _ws(tmp_path)
    kg.ingest_ideation(ws, _payload(), source_stage="000_ideate")
    # seed hypotheses are stored on the direction, NOT as hypothesis nodes,
    # until adoption — so the hypothesis space stays clean.
    g = kg.load_graph(ws)
    assert len(g.nodes_of_type(NodeType.HYPOTHESIS)) == 0
    d = kg.proposed_directions(ws)[0]
    assert len(d["seed_hypotheses"]) == 2


def test_adopt_direction_materializes_hypotheses(tmp_path):
    ws = _ws(tmp_path)
    kg.ingest_ideation(ws, _payload(), source_stage="000_ideate")
    d1 = kg.proposed_directions(ws)[0]
    goal = kg.adopt_direction(ws, d1["id"])
    assert goal.extra["adopted"] is True
    # now the 2 seed hypotheses are real preregistered hypothesis nodes
    hyps = kg.hypotheses_for_goal(ws, goal.id)
    assert len(hyps) == 2
    assert all(h.origin == HypothesisOrigin.PREREGISTERED for h in hyps)
    # reflected in proposed_directions adopted flag
    assert any(d["adopted"] for d in kg.proposed_directions(ws))


def test_adopt_direction_is_idempotent(tmp_path):
    ws = _ws(tmp_path)
    kg.ingest_ideation(ws, _payload(), source_stage="000_ideate")
    d1 = kg.proposed_directions(ws)[0]
    kg.adopt_direction(ws, d1["id"])
    kg.adopt_direction(ws, d1["id"])  # again
    goal = kg.get_node(ws, d1["id"])
    assert len(kg.hypotheses_for_goal(ws, goal.id)) == 2  # not 4


def test_adopt_accepts_bare_id(tmp_path):
    ws = _ws(tmp_path)
    kg.ingest_ideation(ws, _payload(), source_stage="000_ideate")
    full_id = kg.proposed_directions(ws)[0]["id"]  # "goal:..."
    bare = full_id.split(":", 1)[-1]
    goal = kg.adopt_direction(ws, bare)
    assert goal.extra["adopted"] is True


def test_adopt_unknown_direction_raises(tmp_path):
    ws = _ws(tmp_path)
    with pytest.raises(KeyError):
        kg.adopt_direction(ws, "nope")


def test_adopt_non_direction_goal_raises(tmp_path):
    ws = _ws(tmp_path)
    g = kg.add_goal(ws, "regular goal", explicit_id="g1")
    with pytest.raises(ValueError, match="not an ideated direction"):
        kg.adopt_direction(ws, g.id)


def test_ingest_skips_malformed_entries(tmp_path):
    ws = _ws(tmp_path)
    counts = kg.ingest_ideation(ws, {
        "sources": [{"no_title": 1}],
        "directions": [{"no_question": 1}],
    }, source_stage="000_ideate")
    assert counts["sources"] == 0
    assert counts["directions"] == 0
    assert len(counts["warnings"]) == 2


def test_ingest_cites_unknown_source_warns_but_writes_edge(tmp_path):
    ws = _ws(tmp_path)
    counts = kg.ingest_ideation(ws, {
        "directions": [{"id": "d1", "question": "q?", "cites": ["ghost source"]}],
    }, source_stage="000_ideate")
    assert counts["directions"] == 1
    assert counts["cite_edges"] == 1
    assert any("unknown source" in w for w in counts["warnings"])


def test_render_directions_md(tmp_path):
    ws = _ws(tmp_path)
    assert kg.render_directions_md(ws) == "(no ideated directions yet)"
    kg.ingest_ideation(ws, _payload(), source_stage="000_ideate")
    md = kg.render_directions_md(ws)
    assert "Cross-modal feature attribution" in md
    assert "Novelty:" in md and "high" in md
    assert "Seed hypotheses" in md
    assert "blog" in md or "professor" in md
    # adopt one, it should be flagged
    kg.adopt_direction(ws, kg.proposed_directions(ws)[0]["id"])
    assert "ADOPTED" in kg.render_directions_md(ws)


def test_ingest_persists_verification_and_credibility(tmp_path):
    """Source verification (anti-fabrication) + credibility flow from
    directions.json into the source node's extra so downstream can
    distrust unverified / low-credibility sources."""
    ws = _ws(tmp_path)
    kg.ingest_ideation(ws, {
        "sources": [
            {"title": "Real Paper", "source_type": "paper",
             "url": "https://arxiv.org/abs/1234.5678",
             "verified": True, "verification_note": "fetched, title matches",
             "credibility": "high"},
            {"title": "Sketchy Claim", "source_type": "blog",
             "verified": False, "verification_note": "could not resolve",
             "credibility": "low"},
        ],
    }, source_stage="000_ideate")
    g = kg.load_graph(ws)
    real = kg.get_node(ws, kg.source_id("Real Paper"))
    sketchy = kg.get_node(ws, kg.source_id("Sketchy Claim"))
    assert real.extra["verified"] is True and real.extra["credibility"] == "high"
    assert sketchy.extra["verified"] is False and sketchy.extra["credibility"] == "low"
    # default when omitted
    kg.ingest_ideation(ws, {"sources": [{"title": "Bare", "source_type": "paper"}]},
                       source_stage="000_ideate")
    bare = kg.get_node(ws, kg.source_id("Bare"))
    assert bare.extra["verified"] is False and bare.extra["credibility"] == "unrated"
