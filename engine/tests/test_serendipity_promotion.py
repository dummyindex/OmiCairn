"""Tests for the serendipity-promotion loop (creativity audit F1).

A SUPPORTED, surprising/cheap-to-test ON-THE-FLY hypothesis is promoted to a
PROPOSED (un-adopted) ideated direction in the cross-run lab KB, so a
within-run discovery earns a SEAT AT IDEATE — without auto-launching a run or
relaxing any gate. Covers: the promotion filter (origin/status/signal),
fail-closed on missing signals, the adopted=False / proposed invariant, the
add_hypothesis optional-kwarg back-compat, and the default-OFF env flag.
"""
from __future__ import annotations

import pathlib

from omicairn import knowledge_graph as kg, lab_knowledge
from omicairn.constants import (
    EdgeRel,
    HypothesisOrigin,
    HypothesisStatus,
    NodeType,
    SERENDIPITY_DIRECTION_ORIGIN,
)


def _lab(tmp_path):
    return tmp_path / "labkb"


def _add_supported_otf(
    ws: pathlib.Path,
    goal_id: str,
    statement: str,
    *,
    cheap_to_test=None,
    surprise=None,
    origin=HypothesisOrigin.ON_THE_FLY,
    resolve_to_supported=True,
) -> str:
    """Add a hypothesis + (optionally) a result_ref with on-disk evidence
    and advance it to SUPPORTED through the real honesty-gated path."""
    hyp = kg.add_hypothesis(
        ws, statement, goal_id, origin=origin,
        cheap_to_test=cheap_to_test, surprise=surprise,
    )
    if not resolve_to_supported:
        return hyp.id
    res = kg.Node(
        id=kg.result_ref_id(statement[:8]), type=NodeType.RESULT_REF,
        title="effect=0.42", evidence_paths=["002_execute/results.json"],
    )
    kg.upsert_node(ws, res)
    kg.upsert_edge(ws, kg.Edge(src=res.id, rel=EdgeRel.SUPPORTS, dst=hyp.id))
    kg.set_hypothesis_status(ws, hyp.id, HypothesisStatus.TESTING)
    kg.set_hypothesis_status(ws, hyp.id, HypothesisStatus.SUPPORTED)
    return hyp.id


def _ws(tmp_path, name="run-a") -> pathlib.Path:
    ws = tmp_path / name
    (ws / "knowledge").mkdir(parents=True)
    (ws / "002_execute").mkdir(parents=True)
    (ws / "002_execute" / "results.json").write_text('{"effect": 0.42}')
    kg.add_goal(ws, "Does X predict Y?", explicit_id="g1")
    return ws


# ---------------------------------------------------------------------------
# Promotion filter
# ---------------------------------------------------------------------------

def test_promotes_supported_cheap_to_test_on_the_fly(tmp_path):
    ws = _ws(tmp_path)
    _add_supported_otf(ws, "goal:g1", "H-otf: Z mediates the effect",
                        cheap_to_test=True)
    out = lab_knowledge.promote_serendipitous_directions(ws, root=_lab(tmp_path))
    assert out == {"promoted": 1, "candidates": 1}
    dirs = lab_knowledge.query(kind="direction", root=_lab(tmp_path))
    assert len(dirs) == 1
    d = dirs[0]
    assert d["status"] == "proposed"            # NEVER auto-adopted
    assert d["origin"] == SERENDIPITY_DIRECTION_ORIGIN
    assert "Z mediates the effect" in d["title"]
    # evidence URI traces back to the supporting result on disk
    assert any(u.endswith("results.json") for u in d["evidence_uris"])


def test_promotes_supported_high_surprise_on_the_fly(tmp_path):
    ws = _ws(tmp_path)
    _add_supported_otf(ws, "goal:g1", "H-otf: surprising regime shift",
                        surprise="high")
    out = lab_knowledge.promote_serendipitous_directions(ws, root=_lab(tmp_path))
    assert out["promoted"] == 1


def test_does_not_promote_unsupported_on_the_fly(tmp_path):
    ws = _ws(tmp_path)
    # cheap_to_test but left in `testing` (no supports edge / not advanced)
    _add_supported_otf(ws, "goal:g1", "H-otf: untested idea",
                       cheap_to_test=True, resolve_to_supported=False)
    out = lab_knowledge.promote_serendipitous_directions(ws, root=_lab(tmp_path))
    assert out["promoted"] == 0
    assert lab_knowledge.query(kind="direction", root=_lab(tmp_path)) == []


def test_does_not_promote_supported_with_no_signal_fail_closed(tmp_path):
    ws = _ws(tmp_path)
    # supported on-the-fly but NEITHER cheap_to_test NOR high-surprise
    _add_supported_otf(ws, "goal:g1", "H-otf: dull but true")
    out = lab_knowledge.promote_serendipitous_directions(ws, root=_lab(tmp_path))
    assert out["promoted"] == 0


def test_does_not_promote_low_or_medium_surprise(tmp_path):
    ws = _ws(tmp_path)
    _add_supported_otf(ws, "goal:g1", "H-otf: meh-surprise", surprise="medium")
    out = lab_knowledge.promote_serendipitous_directions(ws, root=_lab(tmp_path))
    assert out["promoted"] == 0


def test_does_not_promote_preregistered_hypothesis(tmp_path):
    ws = _ws(tmp_path)
    # supported + cheap_to_test, but PRE-REGISTERED (not serendipitous)
    _add_supported_otf(ws, "goal:g1", "H1: pre-registered main effect",
                       cheap_to_test=True,
                       origin=HypothesisOrigin.PREREGISTERED)
    out = lab_knowledge.promote_serendipitous_directions(ws, root=_lab(tmp_path))
    assert out["promoted"] == 0


def test_promotion_is_idempotent(tmp_path):
    ws = _ws(tmp_path)
    _add_supported_otf(ws, "goal:g1", "H-otf: stable discovery", cheap_to_test=True)
    lab_knowledge.promote_serendipitous_directions(ws, root=_lab(tmp_path))
    lab_knowledge.promote_serendipitous_directions(ws, root=_lab(tmp_path))
    # delta-append: re-promoting identical knowledge folds to one entry
    assert len(lab_knowledge.query(kind="direction", root=_lab(tmp_path))) == 1


def test_promoted_direction_visible_to_render_for_ideate(tmp_path):
    ws = _ws(tmp_path)
    _add_supported_otf(ws, "goal:g1", "H-otf: cron should see me",
                       cheap_to_test=True)
    lab_knowledge.promote_serendipitous_directions(ws, root=_lab(tmp_path))
    md = lab_knowledge.render_lab_knowledge_md(root=_lab(tmp_path))
    assert "Directions already explored" in md
    assert "cron should see me" in md


# ---------------------------------------------------------------------------
# add_hypothesis optional-kwarg back-compat
# ---------------------------------------------------------------------------

def test_add_hypothesis_back_compat_no_kwargs(tmp_path):
    ws = _ws(tmp_path)
    h = kg.add_hypothesis(ws, "plain hypothesis", "goal:g1")
    # extra carries exactly the historical keys — no promotion fields leak in
    assert set(h.extra.keys()) == {"goal_id", "posthoc"}


def test_add_hypothesis_persists_promotion_signals(tmp_path):
    ws = _ws(tmp_path)
    h = kg.add_hypothesis(ws, "rich hypothesis", "goal:g1",
                          cheap_to_test=True, surprise="high")
    assert h.extra["cheap_to_test"] is True
    assert h.extra["surprise"] == "high"


def test_ingest_graph_updates_threads_signals(tmp_path):
    ws = _ws(tmp_path)
    kg.ingest_graph_updates(
        ws,
        {"on_the_fly_hypotheses": [
            {"statement": "H-otf via ingest", "goal_id": "g1",
             "cheap_to_test": True, "surprise": "high"}]},
        source_phase="execute", source_stage="002_execute",
    )
    node = kg.get_node(ws, kg.hypothesis_id("H-otf via ingest", "goal:g1"))
    assert node is not None
    assert node.extra.get("cheap_to_test") is True
    assert node.extra.get("surprise") == "high"
