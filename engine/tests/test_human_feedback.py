"""Tests for omicairn.human_feedback — the cross-run human-feedback/annotation store.

The store turns every human steer/note/correction into a TYPED, LINKED,
queryable record (workspace + stage_dir + target_artifact + kind), aggregated
across runs beside lab_knowledge/. These tests pin the load-bearing contracts:
round-trip, typed query, cross-run aggregation, provenance, and that a
feedback-store failure never breaks the primary command path.
"""
from __future__ import annotations

import pathlib

import pytest

from omicairn import human_feedback as hf


def test_record_then_load_round_trip(tmp_path):
    hf.record(kind="steer", text="extend d7 with more models",
              workspace="example-study-d7-20260610", run_topic_slug="example-study-d7",
              stage_dir="012_plan", tags=["steps:6"], root=tmp_path)
    store = hf.load(tmp_path)
    assert len(store) == 1
    rec = next(iter(store.values()))
    assert rec["kind"] == "steer"
    assert rec["text"] == "extend d7 with more models"
    assert rec["workspace"] == "example-study-d7-20260610"
    assert rec["stage_dir"] == "012_plan"
    assert "steps:6" in rec["tags"]


def test_records_carry_provenance(tmp_path):
    hf.record(kind="note", text="tighten the sensitivity analysis", workspace="w1", root=tmp_path)
    rec = next(iter(hf.load(tmp_path).values()))
    # provenance: a recorded-at timestamp and a omicairn commit (best-effort).
    assert rec.get("recorded_at"), "record must stamp recorded_at"
    assert "omicairn_commit" in rec, "record must capture the OmiCairn code commit"


def test_typed_query_filters_by_kind(tmp_path):
    hf.record(kind="steer", text="s1", workspace="w1", root=tmp_path)
    hf.record(kind="correction", text="primary metric should be AUPRC, not AUROC",
              workspace="w1", stage_dir="025_execute",
              target_artifact="results.json", tags=["metric"], root=tmp_path)
    hf.record(kind="note", text="n1", workspace="w1", root=tmp_path)
    corrections = hf.query(kind="correction", root=tmp_path)
    assert len(corrections) == 1
    assert corrections[0]["target_artifact"] == "results.json"
    assert "metric" in corrections[0]["tags"]
    # the link fields make it minable: "all metric corrections"
    by_tag = hf.query(tag="metric", root=tmp_path)
    assert len(by_tag) == 1


def test_cross_run_aggregation_single_store(tmp_path):
    # two different workspaces -> one shared store (the cross-run point).
    hf.record(kind="steer", text="a", workspace="d1-run", root=tmp_path)
    hf.record(kind="steer", text="b", workspace="d7-run", root=tmp_path)
    hf.record(kind="rejection", text="reject this analysis", workspace="d7-run",
              prior_decision="PROCEED", root=tmp_path)
    store = hf.load(tmp_path)
    assert len(store) == 3
    workspaces = {r["workspace"] for r in store.values()}
    assert workspaces == {"d1-run", "d7-run"}
    # filter to one run
    d7 = hf.query(workspace="d7-run", root=tmp_path)
    assert len(d7) == 2


def test_query_respects_limit(tmp_path):
    for i in range(5):
        hf.record(kind="note", text=f"n{i}", workspace="w1", root=tmp_path)
    assert len(hf.query(root=tmp_path, limit=3)) == 3


def test_unknown_kind_fails_loud(tmp_path):
    # a typo'd kind is a caller bug — fail loudly, don't silently store junk.
    with pytest.raises((ValueError, KeyError)):
        hf.record(kind="rejct", text="oops", workspace="w1", root=tmp_path)


def test_record_from_workspace_resolves_links(tmp_path):
    # record_from_workspace derives workspace/run_topic_slug/stage from the
    # workspace dir, so callers (cmd_steer/cmd_note) just pass the ws root.
    ws = tmp_path / "workspaces" / "example-study-d7-20260610-064717"
    (ws).mkdir(parents=True)
    (ws / "STATE.md").write_text(
        "---\ntopic_slug: example-study-d7\n"
        "current_stage: 021_execute\nstatus: in_progress\n---\n")
    rec = hf.record_from_workspace(ws, kind="note", text="a note", tags=["phase:review"])
    # may be None only if the store write fails; here it should succeed
    assert rec is not None
    store = hf.load(hf.resolve_root(workspaces_root=ws.parent))
    assert any(r["text"] == "a note" for r in store.values())
    got = next(r for r in store.values() if r["text"] == "a note")
    assert got["workspace"] == ws.name
    assert "phase:review" in got["tags"]


def test_record_from_workspace_never_raises_on_store_failure(tmp_path, monkeypatch):
    # ADDITIVE durability mirror must never break the primary command: a
    # store-write failure returns None, does not propagate.
    ws = tmp_path / "ws-x"
    ws.mkdir()
    (ws / "STATE.md").write_text("---\ntopic_slug: x\n---\n")
    monkeypatch.setattr(hf, "record", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    # must swallow and return None, not raise
    assert hf.record_from_workspace(ws, kind="note", text="x") is None
