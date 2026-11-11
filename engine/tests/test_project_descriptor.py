"""Unit tests for omicairn.project_descriptor — the Project Descriptor Protocol.

Deterministic core only (no LLM): schema validation, describe_workspace from
a graph, JSON round-trip, one-way markdown render, structure detection, and
the verified=false ingestion of foreign descriptors. The agent-based scan of
arbitrary foreign folders is exercised live, not here.
"""
from __future__ import annotations

import json
import pathlib

from omicairn import knowledge_graph as kg, project_descriptor as pd
from omicairn.constants import EdgeRel, HypothesisStatus, NodeType


def _ws_with_graph(tmp_path: pathlib.Path, name: str = "run-a") -> pathlib.Path:
    ws = tmp_path / name
    (ws / "knowledge").mkdir(parents=True)
    (ws / "002_execute").mkdir(parents=True)
    (ws / "002_execute" / "results.json").write_text('{"auc": 0.81}')
    (ws / "data").mkdir()
    goal = kg.add_goal(ws, "Does X predict Y?", explicit_id="g1")
    hyp = kg.add_hypothesis(ws, "H1: X is prognostic", goal.id)
    res = kg.Node(id=kg.result_ref_id("auc"), type=NodeType.RESULT_REF,
                  title="auc=0.81", evidence_paths=["002_execute/results.json"])
    kg.upsert_node(ws, res)
    kg.upsert_edge(ws, kg.Edge(src=res.id, rel=EdgeRel.SUPPORTS, dst=hyp.id))
    kg.set_hypothesis_status(ws, hyp.id, HypothesisStatus.TESTING)
    kg.set_hypothesis_status(ws, hyp.id, HypothesisStatus.SUPPORTED)
    kg.add_finding(ws, "baseline beats FM", body="0.94 vs 0.69",
                   evidence_paths=["002_execute/results.json"])
    kg.upsert_node(ws, kg.Node(id="dataset_ref:x", type=NodeType.DATASET_REF,
                               title="GSE123 atlas"))
    return ws


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------

def test_validate_accepts_minimal_valid():
    assert pd.validate({"project": "p", "origin": "manual"}) == []


def test_validate_requires_project():
    errs = pd.validate({"origin": "manual"})
    assert any("project" in e for e in errs)


def test_validate_rejects_bad_origin():
    errs = pd.validate({"project": "p", "origin": "robot"})
    assert any("origin" in e for e in errs)


def test_validate_rejects_wrong_types():
    errs = pd.validate({"project": "p", "origin": "omicairn",
                        "knowledge": {"findings": "not-a-list"},
                        "open_questions": "nope"})
    assert any("knowledge.findings" in e for e in errs)
    assert any("open_questions" in e for e in errs)


# ---------------------------------------------------------------------------
# describe_workspace (deterministic)
# ---------------------------------------------------------------------------

def test_describe_workspace_summarizes_graph(tmp_path):
    ws = _ws_with_graph(tmp_path)
    d = pd.describe_workspace(ws)
    assert d.origin == "omicairn"
    assert d.provenance.get("verified") is True
    assert len(d.knowledge["goals"]) == 1
    assert len(d.knowledge["hypotheses"]) == 1
    assert len(d.knowledge["findings"]) == 1
    assert len(d.knowledge["datasets"]) == 1
    assert pd.validate(d.to_dict()) == []


def test_describe_detects_structure(tmp_path):
    ws = _ws_with_graph(tmp_path)
    d = pd.describe_workspace(ws)
    assert d.structure.get("data") == ["./data"]
    assert d.structure.get("graph") == "./knowledge/graph.jsonl"
    assert d.structure.get("results") == ["./002_execute/results.json"]


# ---------------------------------------------------------------------------
# write / read round-trip + render
# ---------------------------------------------------------------------------

def test_write_read_roundtrip(tmp_path):
    ws = _ws_with_graph(tmp_path)
    d = pd.describe_workspace(ws)
    out = tmp_path / "out"
    path = pd.write_descriptor(out, d)
    assert path.name == "project.json"
    assert (out / "PROJECT.md").exists()
    back = pd.read_descriptor(out)
    assert back is not None
    assert back.project == d.project
    assert back.knowledge["hypotheses"] == d.knowledge["hypotheses"]


def test_read_descriptor_absent_is_none(tmp_path):
    assert pd.read_descriptor(tmp_path) is None


def test_project_json_is_valid_json_not_markdown(tmp_path):
    ws = _ws_with_graph(tmp_path)
    pd.write_descriptor(tmp_path / "o", pd.describe_workspace(ws))
    # canonical file parses as JSON (never markdown)
    json.loads((tmp_path / "o" / "project.json").read_text())


def test_render_markdown_marks_unverified():
    d = pd.ProjectDescriptor(
        project="p", origin="manual",
        knowledge={"findings": [{"title": "shaky claim", "verified": False}]})
    md = pd.render_markdown(d)
    assert "shaky claim" in md
    assert "_(unverified)_" in md
    assert "do not edit by hand" in md  # one-way view warning


# ---------------------------------------------------------------------------
# ingest foreign descriptor → graph (verified=false)
# ---------------------------------------------------------------------------

def test_ingest_foreign_marks_unverified(tmp_path):
    d = pd.ProjectDescriptor(
        project="foreign-x", origin="manual",
        knowledge={
            "findings": [{"title": "Gene G up in disease",
                          "evidence": ["results.csv"]}],
            "hypotheses": [{"title": "G drives phenotype",
                            "status": "proposed"}],
            "datasets": [{"title": "GSE999"}],
        })
    target = tmp_path / "target_ws"
    (target / "knowledge").mkdir(parents=True)
    counts = pd.ingest_descriptor_to_graph(target, d, mark_unverified=True)
    assert counts == {"findings": 1, "hypotheses": 1, "datasets": 1}
    g = kg.load_graph(target)
    fnode = g.nodes_of_type(NodeType.FINDING)[0]
    assert fnode.extra.get("verified") is False
    assert fnode.origin == "external"
    assert fnode.extra.get("source_project") == "foreign-x"
    hnode = g.nodes_of_type(NodeType.HYPOTHESIS)[0]
    assert hnode.extra.get("verified") is False


def test_ingest_omicairn_origin_keeps_verified(tmp_path):
    d = pd.ProjectDescriptor(
        project="native", origin="omicairn",
        knowledge={"findings": [{"title": "real result", "verified": True}]})
    target = tmp_path / "t2"
    (target / "knowledge").mkdir(parents=True)
    pd.ingest_descriptor_to_graph(target, d, mark_unverified=True)
    fnode = kg.load_graph(target).nodes_of_type(NodeType.FINDING)[0]
    assert fnode.extra.get("verified") is True
    assert fnode.origin == "omicairn"
