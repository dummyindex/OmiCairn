"""Tests for the cross-workspace LAB knowledge base (omicairn.lab_knowledge):
distilling a workspace's knowledge graph into a URI-indexed, append-only
store that compounds across runs, and rendering it for IDEATE/SCOPE.
"""
from __future__ import annotations

import pathlib

import pytest

from omicairn import knowledge_graph as kg, lab_knowledge
from omicairn.constants import EdgeRel, HypothesisStatus, NodeType


def _ws_with_resolved_graph(tmp_path: pathlib.Path, name: str) -> pathlib.Path:
    """A workspace whose graph has a supported hypothesis (with on-disk
    evidence), a finding, a dataset_ref, and an adopted direction."""
    ws = tmp_path / name
    (ws / "knowledge").mkdir(parents=True)
    (ws / "002_execute").mkdir(parents=True)
    (ws / "002_execute" / "results.json").write_text('{"auc": 0.81}')
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
    kg.upsert_node(ws, kg.Node(id="dataset_ref:fullard", type=NodeType.DATASET_REF,
                               title="Fullard SNpc atlas", body="GSE...",
                               evidence_paths=["002_execute/results.json"]))
    return ws


def _lab(tmp_path):
    return tmp_path / "labkb"


def test_distill_pulls_resolved_hypotheses_findings_datasets(tmp_path):
    ws = _ws_with_resolved_graph(tmp_path, "run-a")
    counts = lab_knowledge.distill_workspace(ws, topic_slug="x", root=_lab(tmp_path))
    assert counts["hypotheses"] == 1   # the supported one
    assert counts["findings"] == 1
    assert counts["datasets"] == 1
    entries = lab_knowledge.load(_lab(tmp_path))
    assert len(entries) == 3


def test_distill_indexes_local_file_uris(tmp_path):
    ws = _ws_with_resolved_graph(tmp_path, "run-a")
    lab_knowledge.distill_workspace(ws, root=_lab(tmp_path))
    hyp = lab_knowledge.query(kind="hypothesis", root=_lab(tmp_path))[0]
    assert any(u.startswith("file://") and u.endswith("results.json")
               for u in hyp["evidence_uris"])
    assert hyp["source_uri"].startswith("file://")


def test_distill_indexes_s3_uris_with_uri_base(tmp_path):
    ws = _ws_with_resolved_graph(tmp_path, "run-a")
    lab_knowledge.distill_workspace(
        ws, uri_base="s3://mybucket/omicairn/run-a", root=_lab(tmp_path))
    hyp = lab_knowledge.query(kind="hypothesis", root=_lab(tmp_path))[0]
    assert any(u == "s3://mybucket/omicairn/run-a/002_execute/results.json"
               for u in hyp["evidence_uris"])


def test_distill_only_resolved_hypotheses(tmp_path):
    """A merely-proposed hypothesis is not distilled (only supported/
    refuted/inconclusive enter the lab memory)."""
    ws = tmp_path / "run-p"
    (ws / "knowledge").mkdir(parents=True)
    g = kg.add_goal(ws, "G", explicit_id="g1")
    kg.add_hypothesis(ws, "H_unresolved", g.id)  # stays proposed
    counts = lab_knowledge.distill_workspace(ws, root=_lab(tmp_path))
    assert counts["hypotheses"] == 0


def test_distill_is_idempotent_and_accumulates_across_workspaces(tmp_path):
    lab = _lab(tmp_path)
    wsa = _ws_with_resolved_graph(tmp_path, "run-a")
    wsb = _ws_with_resolved_graph(tmp_path, "run-b")
    lab_knowledge.distill_workspace(wsa, root=lab)
    lab_knowledge.distill_workspace(wsa, root=lab)   # re-distill: in place
    lab_knowledge.distill_workspace(wsb, root=lab)   # different ws: adds
    s = lab_knowledge.stats(lab)
    assert s["workspaces"] == 2
    # run-a's 3 + run-b's 3 = 6 (re-distill of run-a did not duplicate)
    assert s["entries"] == 6


def test_render_lab_knowledge_md(tmp_path):
    lab = _lab(tmp_path)
    assert "first run" in lab_knowledge.render_lab_knowledge_md(lab)
    ws = _ws_with_resolved_graph(tmp_path, "run-a")
    lab_knowledge.distill_workspace(ws, root=lab)
    md = lab_knowledge.render_lab_knowledge_md(lab)
    assert "[supported]" in md
    assert "H1: X is prognostic" in md
    assert "Fullard SNpc atlas" in md


def test_index_file_written(tmp_path):
    lab = _lab(tmp_path)
    ws = _ws_with_resolved_graph(tmp_path, "run-a")
    lab_knowledge.distill_workspace(ws, root=lab)
    from omicairn.constants import LAB_KB_INDEX_FILE
    idx = (lab / LAB_KB_INDEX_FILE).read_text()
    assert "Lab knowledge index" in idx
    assert "results.json" in idx


def test_distill_empty_graph_is_noop(tmp_path):
    ws = tmp_path / "empty"
    (ws / "knowledge").mkdir(parents=True)
    counts = lab_knowledge.distill_workspace(ws, root=_lab(tmp_path))
    assert sum(counts.values()) == 0


def test_distill_includes_proposed_and_adopted_directions(tmp_path):
    """An IDEATE-only run's value is its proposed directions — distill
    must capture both proposed and adopted (tagged), so the lab memory
    grows from ideation, not just completed pipelines."""
    ws = tmp_path / "run-ideate"
    (ws / "knowledge").mkdir(parents=True)
    kg.ingest_ideation(ws, {
        "directions": [
            {"id": "d1", "question": "Dir one?", "hypotheses": ["Ha"], "novelty": "high"},
            {"id": "d2", "question": "Dir two?", "hypotheses": ["Hb"], "novelty": "low"},
        ],
    }, source_stage="000_ideate")
    kg.adopt_direction(ws, "d1")  # one adopted, one left proposed
    counts = lab_knowledge.distill_workspace(ws, root=_lab(tmp_path))
    assert counts["directions"] == 2
    dirs = lab_knowledge.query(kind="direction", root=_lab(tmp_path))
    statuses = {d["status"] for d in dirs}
    assert statuses == {"adopted", "proposed"}


# ---------------------------------------------------------------------------
# End-to-end dataset channel: asset_probe seeds DATASET_REF nodes into the
# run KG, distill then carries them into the lab KB. Before this wiring the
# dataset channel was designed but had NO producer, so it always rendered
# empty (lab_knowledge.jsonl: 0 dataset entries). These tests assert the
# previously-empty channel now carries traffic.
# ---------------------------------------------------------------------------

def test_seeded_dataset_ref_distills_into_lab_kb(tmp_path):
    """Seed a DATASET_REF from a resolved probe, distill, and confirm a
    dataset-kind entry lands in the lab-KB jsonl (channel now non-empty)."""
    from omicairn import asset_probe
    ws = tmp_path / "run-a"
    (ws / "knowledge").mkdir(parents=True)
    asset_probe.write_probe_json(ws, [asset_probe.ProbeResult(
        url="https://github.com/chanzuckerberg/cellxgene-census",
        accession="cellxgene-census", status=asset_probe.ProbeStatus.OK,
        http_status=200, size_bytes=None, content_type="text/html",
        checked_at="t", error="")])
    assert asset_probe.seed_dataset_refs(ws) == 1

    lab = _lab(tmp_path)
    counts = lab_knowledge.distill_workspace(ws, root=lab)
    assert counts["datasets"] == 1

    dsets = lab_knowledge.query(kind="dataset", root=lab)
    assert len(dsets) == 1
    assert dsets[0]["title"] == "cellxgene-census"
    assert dsets[0]["evidence_uris"] == ["https://github.com/chanzuckerberg/cellxgene-census"]
    # The previously-empty channel now surfaces in the IDEATE/SCOPE render.
    md = lab_knowledge.render_lab_knowledge_md(lab)
    assert "Datasets the lab already holds" in md
    assert "cellxgene-census" in md


def test_no_resolved_assets_leaves_dataset_channel_empty(tmp_path):
    """A run with no resolved assets seeds nothing → no dataset entries."""
    from omicairn import asset_probe
    ws = tmp_path / "run-empty"
    (ws / "knowledge").mkdir(parents=True)
    assert asset_probe.seed_dataset_refs(ws) == 0
    lab = _lab(tmp_path)
    lab_knowledge.distill_workspace(ws, root=lab)
    assert lab_knowledge.query(kind="dataset", root=lab) == []


def test_distinct_hf_datasets_render_as_distinct_bullets(tmp_path):
    """T-1 fix end-to-end: two HF datasets whose probe accession degraded to
    the bare host 'huggingface.co' must NOT collapse into one 'huggingface.co'
    bullet in the IDEATE/SCOPE render (the render dedups by title)."""
    from omicairn import asset_probe
    ws = tmp_path / "run-a"
    (ws / "knowledge").mkdir(parents=True)
    asset_probe.write_probe_json(ws, [
        asset_probe.ProbeResult(
            url="https://huggingface.co/datasets/Intel/SocialCounterfactuals",
            accession="huggingface.co", status=asset_probe.ProbeStatus.OK,
            http_status=200, size_bytes=None, content_type="text/html",
            checked_at="t", error=""),
        asset_probe.ProbeResult(
            url="https://huggingface.co/datasets/facebook/winoground",
            accession="huggingface.co", status=asset_probe.ProbeStatus.OK,
            http_status=200, size_bytes=None, content_type="text/html",
            checked_at="t", error=""),
    ])
    assert asset_probe.seed_dataset_refs(ws) == 2

    lab = _lab(tmp_path)
    assert lab_knowledge.distill_workspace(ws, root=lab)["datasets"] == 2

    md = lab_knowledge.render_lab_knowledge_md(lab)
    dataset_bullets = [ln for ln in md.splitlines()
                       if ln.startswith("- ") and "huggingface.co/datasets" in ln]
    assert len(dataset_bullets) == 2  # distinct, not collapsed to one
    assert any("Intel/SocialCounterfactuals" in ln for ln in dataset_bullets)
    assert any("facebook/winoground" in ln for ln in dataset_bullets)


# ---------------------------------------------------------------------------
# Location resolution (resolve_root) + create-on-demand
#
# The store moved out of the hidden ~/.cache into a VISIBLE folder beside
# the run workspaces (<workspaces_root>/lab_knowledge), and the location is
# configurable: explicit path > $OMICAIRN_LAB_KB > workspaces_root default.
# ---------------------------------------------------------------------------

def test_resolve_root_explicit_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("OMICAIRN_LAB_KB", str(tmp_path / "env"))
    got = lab_knowledge.resolve_root(
        explicit=tmp_path / "explicit",
        workspaces_root=tmp_path / "ws")
    assert got == (tmp_path / "explicit").resolve()


def test_resolve_root_env_beats_workspaces_root(tmp_path, monkeypatch):
    monkeypatch.setenv("OMICAIRN_LAB_KB", str(tmp_path / "env"))
    got = lab_knowledge.resolve_root(workspaces_root=tmp_path / "ws")
    assert got == (tmp_path / "env").resolve()


def test_resolve_root_workspaces_root_default(tmp_path, monkeypatch):
    monkeypatch.delenv("OMICAIRN_LAB_KB", raising=False)
    got = lab_knowledge.resolve_root(workspaces_root=tmp_path / "workspaces")
    assert got == (tmp_path / "workspaces" / "lab_knowledge").resolve()


def test_resolve_root_default_is_visible_not_home_cache(tmp_path, monkeypatch):
    """The default must NOT be a hidden ~/.cache path."""
    monkeypatch.delenv("OMICAIRN_LAB_KB", raising=False)
    got = lab_knowledge.resolve_root(workspaces_root=tmp_path / "workspaces")
    parts = got.parts
    assert ".cache" not in parts
    assert not any(p.startswith(".") for p in parts[-2:])  # last segs visible
    assert got.name == "lab_knowledge"


def test_resolve_root_cwd_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv("OMICAIRN_LAB_KB", raising=False)
    monkeypatch.chdir(tmp_path)
    got = lab_knowledge.resolve_root()
    assert got == (tmp_path / "workspaces" / "lab_knowledge").resolve()


def test_default_root_honours_env(tmp_path, monkeypatch):
    monkeypatch.setenv("OMICAIRN_LAB_KB", str(tmp_path / "envkb"))
    assert lab_knowledge.default_root() == (tmp_path / "envkb").resolve()


def test_distill_creates_root_if_missing(tmp_path):
    """During a run the store folder is created on demand — no pre-mkdir."""
    ws = _ws_with_resolved_graph(tmp_path, "run-a")
    missing = tmp_path / "deep" / "not" / "there" / "lab_knowledge"
    assert not missing.exists()
    counts = lab_knowledge.distill_workspace(ws, root=missing)
    assert sum(counts.values()) > 0
    assert missing.exists()
    assert (missing / "lab_knowledge.jsonl").exists()
    from omicairn.constants import LAB_KB_INDEX_FILE
    assert (missing / LAB_KB_INDEX_FILE).exists()


# ---------------------------------------------------------------------------
# Delta-append: re-distilling UNCHANGED knowledge appends ~nothing (root fix
# for the op-log's ~14x bloat). Symmetric to the compact() backstop, but the
# fix is to never WRITE the redundant lines in the first place.
# ---------------------------------------------------------------------------

def _raw_lines(lab: pathlib.Path) -> int:
    from omicairn.constants import LAB_KB_FILE
    return lab_knowledge._count_raw_lines(lab / LAB_KB_FILE)


def test_redistill_unchanged_does_not_grow_oplog(tmp_path):
    """The CORE fix: distilling the SAME workspace knowledge twice appends
    entries the first time and ZERO new raw lines the second time."""
    lab = _lab(tmp_path)
    ws = _ws_with_resolved_graph(tmp_path, "run-a")
    lab_knowledge.distill_workspace(ws, root=lab)
    raw_after_first = _raw_lines(lab)
    assert raw_after_first == 3            # hypothesis + finding + dataset
    # Re-distill the identical workspace several more times.
    for _ in range(5):
        lab_knowledge.distill_workspace(ws, root=lab)
    assert _raw_lines(lab) == raw_after_first   # NO new lines appended
    # And the folded read-state is unchanged (still the 3 distinct keys).
    assert len(lab_knowledge.load(lab)) == 3


def test_redistill_changed_knowledge_does_append(tmp_path):
    """Distilling CHANGED knowledge DOES append: a status flip and a brand
    new finding are written; unchanged siblings are not re-written."""
    lab = _lab(tmp_path)
    ws = _ws_with_resolved_graph(tmp_path, "run-a")
    lab_knowledge.distill_workspace(ws, root=lab)
    base_raw = _raw_lines(lab)

    # 1) Flip the hypothesis status supported -> inconclusive (changed
    #    knowledge). Legal path: supported -> testing -> inconclusive.
    g = kg.load_graph(ws)
    hyp = next(n for n in g.nodes_of_type(NodeType.HYPOTHESIS))
    kg.set_hypothesis_status(ws, hyp.id, HypothesisStatus.TESTING)
    kg.set_hypothesis_status(ws, hyp.id, HypothesisStatus.INCONCLUSIVE)
    # 2) Add a brand new finding (new key).
    kg.add_finding(ws, "second finding", body="extra",
                   evidence_paths=["002_execute/results.json"])

    lab_knowledge.distill_workspace(ws, root=lab)
    # Exactly 2 new raw lines: the changed hypothesis + the new finding.
    # The unchanged finding and dataset were NOT re-appended.
    assert _raw_lines(lab) == base_raw + 2
    entries = lab_knowledge.load(lab)
    hyp_entry = next(e for e in entries.values() if e["kind"] == "hypothesis")
    assert hyp_entry["status"] == HypothesisStatus.INCONCLUSIVE
    assert any(e["title"] == "second finding" for e in entries.values())


def test_delta_append_folded_state_matches_append_all(tmp_path):
    """Behaviour-preserving: the folded read-state after delta-append equals
    the folded state of the OLD append-all behaviour (modulo the volatile
    distilled_at timestamp, which carries no knowledge)."""
    lab_delta = _lab(tmp_path) / "delta"
    lab_allappend = _lab(tmp_path) / "allappend"
    ws = _ws_with_resolved_graph(tmp_path, "run-a")

    # Delta path: the real distill (skips unchanged on re-distill).
    for _ in range(3):
        lab_knowledge.distill_workspace(ws, root=lab_delta)

    # Simulate the OLD append-all: distill once to populate, then replay the
    # SAME entries unconditionally via _append (no skip) to mimic re-distill.
    lab_knowledge.distill_workspace(ws, root=lab_allappend)
    folded = lab_knowledge.load(lab_allappend)
    lab_knowledge._append(lab_allappend, list(folded.values()))
    lab_knowledge._append(lab_allappend, list(folded.values()))

    def _ident(store):
        return {k: lab_knowledge._entry_identity(e)
                for k, e in lab_knowledge.load(store).items()}

    assert _ident(lab_delta) == _ident(lab_allappend)
    # The delta op-log has FEWER raw lines than the append-all op-log.
    from omicairn.constants import LAB_KB_FILE
    assert (lab_knowledge._count_raw_lines(lab_delta / LAB_KB_FILE)
            < lab_knowledge._count_raw_lines(lab_allappend / LAB_KB_FILE))


def test_entry_identity_excludes_only_distilled_at(tmp_path):
    """Identity drops the volatile distilled_at and nothing else — so two
    distills that differ ONLY in timestamp compare equal, but any knowledge
    field difference compares unequal."""
    e = {"kind": "hypothesis", "key": "k", "title": "T", "status": "supported",
         "summary": "s", "evidence_uris": ["u"], "source_uri": "src",
         "distilled_at": "2026-01-01T00:00:00+00:00"}
    e2 = dict(e, distilled_at="2026-06-13T12:00:00+00:00")   # only timestamp
    assert lab_knowledge._entry_identity(e) == lab_knowledge._entry_identity(e2)
    e3 = dict(e, status="refuted")                            # knowledge change
    assert lab_knowledge._entry_identity(e) != lab_knowledge._entry_identity(e3)
    e4 = dict(e, summary="different")
    assert lab_knowledge._entry_identity(e) != lab_knowledge._entry_identity(e4)


def test_delta_append_fold_runs_inside_the_lock(tmp_path, monkeypatch):
    """Concurrent-safety: the fold-before-append decision happens INSIDE the
    held lab-KB lock, so a peer distill cannot race it. We assert the lock is
    held at the moment load() (the fold) is called during distill."""
    lab = _lab(tmp_path)
    ws = _ws_with_resolved_graph(tmp_path, "run-a")
    lab_knowledge.distill_workspace(ws, root=lab)   # seed so re-distill folds

    from omicairn.constants import LAB_KB_LOCK
    seen = {"lock_held_during_fold": False}
    real_load = lab_knowledge.load

    def spy_load(r=None):
        if r is not None and (pathlib.Path(r) / LAB_KB_LOCK).exists():
            seen["lock_held_during_fold"] = True
        return real_load(r)

    monkeypatch.setattr(lab_knowledge, "load", spy_load)
    lab_knowledge.distill_workspace(ws, root=lab)
    assert seen["lock_held_during_fold"], "fold must run inside the held lock"
