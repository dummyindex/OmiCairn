"""End-to-end comparison: single-hypothesis control vs dynamic-Hs, the
demonstration #49 / docs/design-docs/dynamic-hypotheses.md §5 calls for.

This is the "show the difference" deliverable. It is driven by the REAL
research-case files under research_cases/ (synth-singleh-control and
synth-dynamic-h), so it validates the whole config→behaviour path:
RESEARCH.md goals + omicairn_config.yaml `allow_on_the_fly_hypotheses`
overlay → workspace.init → orchestrator graph seeding → ingest gating.

Fairness of the A/B: BOTH arms receive the SAME simulated EXECUTE
evidence (one honest analysis of the shared dataset, which contains a
real secondary signal: feature2 is prognostic only in group B). The
ONLY thing that differs is each case's `allow_on_the_fly` lever. So any
divergence in the resulting knowledge graph is attributable to the
dynamic-hypothesis mechanism alone — not to different inputs.

Run under OMICAIRN_DRY_RUN: no real claude -p. The EXECUTE agent's
`graph_updates.json` is simulated here (the same payload an honest
discovery EXECUTE would emit on this data); a real-LLM end-to-end run
is a separate manual exercise, not a CI test.
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
    HypothesisOrigin,
    HypothesisStatus,
    KG_UPDATES_FILE,
    NodeType,
    PhaseName,
)

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_CASES = _REPO_ROOT / "research_cases"

# The honest EXECUTE output on the shared synth-dynamic-demo dataset.
# feature1 supports H1 (both arms); feature2's OVERALL effect is weak →
# H2 inconclusive; the real find is the subgroup interaction, emitted as
# an on-the-fly hypothesis with on-disk evidence. IDENTICAL for both
# arms — the gating is what differs.
_SIMULATED_EXECUTE_UPDATES = {
    "result_refs": [
        {"key": "auc_feature1", "title": "feature1 held-out AUC=0.74",
         "value": 0.74, "evidence_paths": ["002_execute/results.json"],
         "hypothesis": "H1: feature1 is prognostic for outcome (held-out AUC > 0.6)",
         "goal_id": "g1", "verdict": "supports"},
        {"key": "auc_feature2_overall", "title": "feature2 overall AUC=0.58 (CI crosses 0.5)",
         "value": 0.58, "evidence_paths": ["002_execute/results.json"],
         "hypothesis": "H2: feature2 is prognostic for outcome overall",
         "goal_id": "g2", "verdict": "inconclusive"},
        {"key": "auc_feature2_groupB", "title": "feature2 AUC=0.79 within group B",
         "value": 0.79, "evidence_paths": ["002_execute/results.json"],
         "hypothesis": "H_otf: feature2 is prognostic for outcome within group B only",
         "goal_id": "g2", "verdict": "supports"},
    ],
    "findings": [
        {"title": "feature2 effect is subgroup-dependent (group B only)",
         "body": "feature2 separates outcome in group B (AUC 0.79) but not group A (AUC 0.53).",
         "evidence_paths": ["002_execute/results.json"],
         "tags": ["interaction", "subgroup"]},
    ],
    "on_the_fly_hypotheses": [
        {"statement": "H_otf: feature2 is prognostic for outcome within group B only",
         "goal_id": "g2",
         "raised_by_finding": "feature2 effect is subgroup-dependent (group B only)",
         "cheap_to_test": True},
    ],
}


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


def _run_arm(case_slug: str, ws_root: pathlib.Path) -> dict:
    """Init a workspace from the real case dir, seed the graph, inject
    the shared simulated EXECUTE evidence, ingest under that case's
    gating, and return its graph stats + projection."""
    rmd = _CASES / case_slug / "RESEARCH.md"
    ws = workspace.init(
        case_slug, research_md_path=rmd,
        workspaces_root=ws_root, skip_literature=True,
    )
    orchestrator._seed_knowledge_graph(ws)
    stage = ws.root / "002_execute"
    stage.mkdir()
    (stage / "results.json").write_text(json.dumps({
        "feature1_auc": 0.74, "feature2_overall_auc": 0.58,
        "feature2_groupB_auc": 0.79,
    }))
    (stage / KG_UPDATES_FILE).write_text(json.dumps(_SIMULATED_EXECUTE_UPDATES))
    orchestrator._ingest_stage_graph_updates(ws, stage, PhaseName.EXECUTE)
    return {
        "stats": kg.stats(ws.root),
        "projection": kg.paper_projection(ws.root),
        "allow_on_the_fly": ws.research_md.allow_on_the_fly_hypotheses,
    }


def test_real_cases_parse_as_clean_ab(dry_run_env):
    """The two case files differ only in the lever under test."""
    from omicairn import research_md
    control = research_md.load(_CASES / "synth-singleh-control" / "RESEARCH.md")
    dynamic = research_md.load(_CASES / "synth-dynamic-h" / "RESEARCH.md")
    assert control.allow_on_the_fly_hypotheses is False
    assert dynamic.allow_on_the_fly_hypotheses is True
    # both point at the same shared dataset → same evidence base
    assert control.data_location == dynamic.data_location


def test_dynamic_graph_strictly_richer_than_single(dry_run_env):
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        control = _run_arm("synth-singleh-control", root / "c")
        dynamic = _run_arm("synth-dynamic-h", root / "d")

    cs, ds = control["stats"], dynamic["stats"]

    # Control: only its 1 pre-registered hypothesis, ZERO on-the-fly
    # (the lever is off → the subgroup signal is discarded).
    assert cs["hypotheses_on_the_fly"] == 0
    assert cs["nodes_by_type"][NodeType.HYPOTHESIS] == 1

    # Dynamic: 2 seed Hs + 1 on-the-fly = 3, with the interaction captured.
    assert ds["hypotheses_on_the_fly"] == 1
    assert ds["nodes_by_type"][NodeType.HYPOTHESIS] == 3

    # Strictly richer on every axis that matters.
    assert (ds["nodes_by_type"][NodeType.HYPOTHESIS]
            > cs["nodes_by_type"][NodeType.HYPOTHESIS])
    assert ds["hypotheses_tested"] > cs["hypotheses_tested"]
    assert (ds["nodes_by_type"].get(NodeType.FINDING, 0)
            >= cs["nodes_by_type"].get(NodeType.FINDING, 0))
    # Dynamic validates MORE hypotheses: feature1 (both) + the
    # feature2-in-group-B interaction (dynamic only) = 2 supported,
    # vs the control's 1. The extra supported result is real signal the
    # single-H arm discarded, not fabrication (see honesty test).
    cs_sup = cs["hypotheses_by_status"].get(HypothesisStatus.SUPPORTED, 0)
    ds_sup = ds["hypotheses_by_status"].get(HypothesisStatus.SUPPORTED, 0)
    assert cs_sup == 1 and ds_sup == 2


def test_both_arms_obey_the_honesty_rule(dry_run_env):
    """Neither arm marks a hypothesis supported without on-disk evidence.
    The richer graph is not richer by fabrication."""
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        control = _run_arm("synth-singleh-control", root / "c")
        dynamic = _run_arm("synth-dynamic-h", root / "d")

    for arm in (control, dynamic):
        for goal in arm["projection"]["goals"]:
            for h in goal["hypotheses"]:
                if h["status"] in (HypothesisStatus.SUPPORTED,
                                    HypothesisStatus.REFUTED):
                    # every resolved hypothesis cites >=1 evidence path
                    assert h["evidence"], (
                        f"{h['statement']!r} is {h['status']} with no evidence"
                    )
                    assert any(e["evidence_paths"] for e in h["evidence"])


def test_control_discards_the_real_secondary_signal(dry_run_env):
    """The crux of the demonstration: the single-H control, by design,
    has NO node representing the feature2-in-group-B interaction — the
    real secondary finding the data contains. The dynamic arm does."""
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        control = _run_arm("synth-singleh-control", root / "c")
        dynamic = _run_arm("synth-dynamic-h", root / "d")

    def _mentions_interaction(arm) -> bool:
        hyps = [h["statement"].lower()
                for g in arm["projection"]["goals"] for h in g["hypotheses"]]
        return any("group b" in s for s in hyps)

    assert _mentions_interaction(dynamic) is True
    assert _mentions_interaction(control) is False


def test_comparison_table_is_emittable(dry_run_env, capsys):
    """Emit the side-by-side comparison (the human-facing artifact of
    the demonstration). Not an assertion-heavy test — it documents the
    difference in a readable form and guards that the projection shape
    stays renderable."""
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        control = _run_arm("synth-singleh-control", root / "c")
        dynamic = _run_arm("synth-dynamic-h", root / "d")

    cs, ds = control["stats"], dynamic["stats"]
    rows = [
        ("hypotheses (total)",
         cs["nodes_by_type"][NodeType.HYPOTHESIS],
         ds["nodes_by_type"][NodeType.HYPOTHESIS]),
        ("  on-the-fly", cs["hypotheses_on_the_fly"], ds["hypotheses_on_the_fly"]),
        ("hypotheses tested", cs["hypotheses_tested"], ds["hypotheses_tested"]),
        ("findings", cs["nodes_by_type"].get(NodeType.FINDING, 0),
         ds["nodes_by_type"].get(NodeType.FINDING, 0)),
        ("supported", cs["hypotheses_by_status"].get(HypothesisStatus.SUPPORTED, 0),
         ds["hypotheses_by_status"].get(HypothesisStatus.SUPPORTED, 0)),
    ]
    print("\n=== single-H control vs dynamic-Hs (same data, same EXECUTE evidence) ===")
    print(f"{'metric':24} {'single-H':>10} {'dynamic-Hs':>12}")
    for name, c, d in rows:
        print(f"{name:24} {c:>10} {d:>12}")
    # guard: the dynamic projection renders the on-the-fly hypothesis
    assert ds["nodes_by_type"][NodeType.HYPOTHESIS] == 3
    out = capsys.readouterr().out
    assert "dynamic-Hs" in out
