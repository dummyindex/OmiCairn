"""End-to-end tests for the IDEATE (brainstorm) stage, driven by the two
real research cases (ideate-parkinson-mechanism, ideate-alzheimer-mechanism).

What this validates:
- the two purpose-level cases parse + init;
- running IDEATE (dry-run) produces a 000_ideate stage and the orchestrator
  folds a directions.json into the knowledge graph (source nodes +
  proposed-direction goal nodes + cites→source provenance edges);
- the survey spans diverse provenance (paper / blog / professor / nobel /
  philosopher / industry) and directions cite it;
- `--adopt` promotes a direction to an active goal AND steers the
  downstream pipeline: SCOPE's rendered prompt then shows the adopted
  direction (via {{knowledge_graph_projection}}) AND the steering note
  (via {{user_notes}}) — i.e. the stage handoff actually works.

Dry-run mode: no real claude -p. The IDEATE agent's directions.json is
simulated here (what an honest survey would emit). A real-LLM run is a
manual exercise, not a CI test.
"""
from __future__ import annotations

import json
import os
import pathlib
import tempfile
from typing import Iterator

import pytest

from omicairn import (
    knowledge_graph as kg,
    orchestrator,
    research_md,
    user_notes,
    workspace,
)
from omicairn.constants import (
    ENV_DRY_RUN,
    IDEATE_DIRECTIONS_FILE,
    NodeType,
    PhaseName,
    SourceType,
)

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_CASES = _REPO_ROOT / "research_cases"


# Simulated IDEATE survey output for the Parkinson's-mechanism case: spans
# paper + academia + professor + nobel provenance; directions cite across types.
_PD_DIRECTIONS = {
    "sources": [
        {"title": "Braak staging of PD-related brain pathology", "source_type": "paper",
         "author": "Braak et al.", "year": 2003,
         "summary": "caudo-rostral spread of Lewy pathology"},
        {"title": "Single-nucleus atlas of the human substantia nigra",
         "source_type": "academia", "author": "(consortium atlas)",
         "summary": "dopaminergic subtypes with differential vulnerability"},
        {"title": "Surmeier: calcium pacemaking and mitochondrial stress",
         "source_type": "professor", "author": "D. J. Surmeier",
         "summary": "selective vulnerability from autonomous pacemaking"},
        {"title": "Prusiner on prion-like protein propagation", "source_type": "nobel",
         "author": "S. B. Prusiner",
         "summary": "templated misfolding as a spreading mechanism"},
    ],
    "directions": [
        {"id": "d1", "title": "Vulnerable dopaminergic subtype programs",
         "question": "Which transcriptional programs distinguish vulnerable "
                     "from resilient dopaminergic neuron subtypes in PD?",
         "rationale": "Pacemaking stress predicts subtype-specific mitochondrial "
                      "programs; atlas-scale snRNA-seq now makes this testable.",
         "hypotheses": ["H1: vulnerable SOX6+ subtypes show reduced oxidative "
                        "phosphorylation gene expression",
                        "H2: the signature is shared with prodromal tissue"],
         "cites": ["Single-nucleus atlas of the human substantia nigra",
                   "Surmeier: calcium pacemaking and mitochondrial stress",
                   "Braak staging of PD-related brain pathology"],
         "novelty": "high", "feasibility": "medium"},
        {"id": "d2", "title": "Propagation-linked glial responses",
         "question": "Do glial states track the regional order of Lewy "
                     "pathology spread?",
         "rationale": "the prion-like view implies a staged glial response; "
                      "no cross-region single-cell test exists.",
         "hypotheses": ["H3: reactive microglia states increase along Braak order"],
         "cites": ["Prusiner on prion-like protein propagation",
                   "Braak staging of PD-related brain pathology"],
         "novelty": "high", "feasibility": "high"},
        {"id": "d3", "title": "Reusable vulnerability signatures",
         "question": "Does a nigral vulnerability signature transfer across cohorts?",
         "rationale": "incremental but high-feasibility external validation.",
         "hypotheses": ["H4: the signature replicates in an independent cohort"],
         "cites": ["Single-nucleus atlas of the human substantia nigra"],
         "novelty": "medium", "feasibility": "high"},
    ],
}

# Alzheimer's-mechanism case: a clinician/academia view is load-bearing here.
_AD_DIRECTIONS = {
    "sources": [
        {"title": "SEA-AD: cellular landscape of AD progression",
         "source_type": "paper", "author": "Gabitto et al.", "year": 2024,
         "summary": "continuous pseudo-progression across cortical cell types"},
        {"title": "APOE4 and lipid-droplet-accumulating microglia",
         "source_type": "paper", "author": "Haney et al.", "year": 2024,
         "summary": "APOE4 drives lipid-laden microglial states"},
        {"title": "Amyloid cascade hypothesis revisited",
         "source_type": "professor", "author": "J. Hardy",
         "summary": "amyloid upstream of tau and neurodegeneration"},
        {"title": "Memory-clinic perspective on biomarker staging",
         "source_type": "academia", "author": "(clinical consortium)",
         "summary": "biological staging by amyloid/tau biomarkers"},
    ],
    "directions": [
        {"id": "d1", "title": "APOE4-dependent microglial trajectories",
         "question": "Does APOE4 shift microglia toward lipid-laden states "
                     "earlier along the pseudo-progression axis?",
         "rationale": "combines SEA-AD progression with APOE4 lipid biology; "
                      "biomarker staging defines early vs late.",
         "hypotheses": ["H1: APOE4 carriers enter the lipid-laden state earlier",
                        "H2: the shift precedes local tau burden"],
         "cites": ["SEA-AD: cellular landscape of AD progression",
                   "APOE4 and lipid-droplet-accumulating microglia",
                   "Memory-clinic perspective on biomarker staging"],
         "novelty": "high", "feasibility": "medium"},
        {"id": "d2", "title": "Amyloid-to-tau cellular hand-off",
         "question": "Which cell states mediate the transition from amyloid "
                     "to tau pathology?",
         "rationale": "the amyloid cascade predicts an ordered cellular response.",
         "hypotheses": ["H3: an astrocyte state peaks between amyloid and tau onset"],
         "cites": ["Amyloid cascade hypothesis revisited",
                   "Memory-clinic perspective on biomarker staging"],
         "novelty": "high", "feasibility": "medium"},
        {"id": "d3", "title": "Progression-axis benchmark",
         "question": "How robust are pseudo-progression axes across cohorts?",
         "rationale": "the field lacks a shared comparison of progression axes.",
         "hypotheses": ["H4: axes agree on cell-type ordering"],
         "cites": ["SEA-AD: cellular landscape of AD progression"],
         "novelty": "medium", "feasibility": "high"},
    ],
}

_CASE_PAYLOADS = {
    "ideate-parkinson-mechanism": _PD_DIRECTIONS,
    "ideate-alzheimer-mechanism": _AD_DIRECTIONS,
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


def _init_case(slug: str, ws_root: pathlib.Path) -> workspace.Workspace:
    return workspace.init(
        slug, research_md_path=_CASES / slug / "RESEARCH.md",
        workspaces_root=ws_root, skip_literature=True,
    )


@pytest.mark.parametrize("slug", list(_CASE_PAYLOADS))
def test_ideate_case_parses_and_is_web_enabled(slug):
    rm = research_md.load(_CASES / slug / "RESEARCH.md")
    assert rm.topic_slug == slug
    assert rm.allow_web is True               # IDEATE needs the web
    assert rm.research_question.strip()       # purpose present


@pytest.mark.parametrize("slug", list(_CASE_PAYLOADS))
def test_ideate_stage_runs_and_ingests_directions(dry_run_env, slug):
    with tempfile.TemporaryDirectory() as td:
        ws = _init_case(slug, pathlib.Path(td) / "ws")
        outcome = orchestrator.run_next_stage(ws, forced_phase=PhaseName.IDEATE)
        assert outcome is not None and outcome.status == "DONE"
        assert outcome.stage_dir_rel.endswith("ideate")
        # Dry-run wrote placeholder contract files; overwrite directions.json
        # with the simulated survey and re-ingest (mirrors a real agent).
        stage = ws.root / outcome.stage_dir_rel
        (stage / IDEATE_DIRECTIONS_FILE).write_text(
            json.dumps(_CASE_PAYLOADS[slug]))
        orchestrator._ingest_ideation_stage(ws, stage)

        g = kg.load_graph(ws.root)
        assert len(g.nodes_of_type(NodeType.SOURCE)) == 4
        dirs = kg.proposed_directions(ws.root)
        assert len(dirs) == 3
        # best-first ordering: a high-novelty direction leads
        assert dirs[0]["novelty"] == "high"
        # every direction is grounded in >=1 cited source
        assert all(d["sources"] for d in dirs)


# ---------------------------------------------------------------------------
# Survey-grade IDEATE cases (real literature surveys, targets tuned UP via
# omicairn_config.yaml). These are NOT exercised in dry-run; we assert the case
# files are well-formed and the brief parses with the survey config applied,
# so a manual `omicairn ideate` run starts from a correct contract.
# ---------------------------------------------------------------------------
_SURVEY_CASES = ["ideate-parkinson-mechanism", "ideate-alzheimer-mechanism"]


@pytest.mark.parametrize("slug", _SURVEY_CASES)
def test_survey_case_files_present(slug):
    case = _CASES / slug
    assert (case / "RESEARCH.md").exists()
    assert (case / "omicairn_config.yaml").exists()
    assert (case / "data").is_dir()       # data.location resolvable at init


@pytest.mark.parametrize("slug", _SURVEY_CASES)
def test_survey_case_parses_with_tuned_ideate_targets(slug):
    """The brief parses and the omicairn_config.yaml `ideate:` overlay raises
    the collection targets above the smoke defaults (8 papers) to ~50 —
    i.e. this is wired as a real survey, not a smoke test."""
    rm = research_md.load(_CASES / slug / "RESEARCH.md")
    assert rm.topic_slug == slug
    assert rm.allow_web is True                       # IDEATE needs the web
    assert rm.research_question.strip()               # purpose present
    assert len(rm.goals) == 1 and rm.goals[0].id == "g1"
    cfg = rm.ideate_config
    assert cfg["target_papers"] == 50                 # tuned UP from default 8
    assert cfg["target_blogs"] == 8
    assert cfg["target_beliefs"] == 12
    assert cfg["min_directions"] == 5
    assert cfg["max_directions"] == 8


def test_alzheimer_case_flags_dataset_downloadability():
    """The AD brief must instruct IDEATE to prefer openly-downloadable
    datasets and to flag access-controlled ones, so downstream EXECUTE can
    actually fetch the data the directions point at."""
    rm = research_md.load(_CASES / "ideate-alzheimer-mechanism" / "RESEARCH.md")
    rq = rm.research_question.lower()
    assert "alzheimer" in rq
    # open-data preference + an open repository named
    assert "openly-downloadable" in rq or "openly downloadable" in rq
    assert "cellxgene" in rq
    # access-controlled sources are explicitly flagged for EXECUTE
    assert "access-controlled" in rq and "dbgap" in rq


def test_second_ideate_run_does_not_collide(dry_run_env):
    """REGRESSION (audit H14, High): IDEATE stage dirs are invisible to
    list_stages (ideate not in PIPELINE_ORDER), so next_stage_index must
    still count them by raw dir name — otherwise a second `omicairn ideate`
    tries to recreate 000_ideate and crashes with FileExistsError."""
    with tempfile.TemporaryDirectory() as td:
        ws = _init_case("ideate-parkinson-mechanism", pathlib.Path(td) / "ws")
        o1 = orchestrator.run_next_stage(ws, forced_phase=PhaseName.IDEATE)
        o2 = orchestrator.run_next_stage(ws, forced_phase=PhaseName.IDEATE)
        assert o1.status == "DONE" and o2.status == "DONE"
        assert o1.stage_dir_rel != o2.stage_dir_rel  # distinct indices
        # and `omicairn run` after ideate still cleanly routes to SCOPE
        assert orchestrator.decide_next_phase(ws) == PhaseName.SCOPE


@pytest.mark.parametrize("slug", list(_CASE_PAYLOADS))
def test_ideate_survey_spans_diverse_provenance(dry_run_env, slug):
    with tempfile.TemporaryDirectory() as td:
        ws = _init_case(slug, pathlib.Path(td) / "ws")
        kg.ingest_ideation(ws.root, _CASE_PAYLOADS[slug],
                           source_stage="000_ideate")
        g = kg.load_graph(ws.root)
        types = {n.extra.get("source_type")
                 for n in g.nodes_of_type(NodeType.SOURCE)}
        # at least 3 distinct provenance types — not just papers (the
        # PD survey spans 4, the AD survey 3)
        assert len(types) >= 3
        assert SourceType.PAPER in types
        # and at least one NON-paper authority is present
        assert types - {SourceType.PAPER}


@pytest.mark.parametrize("slug", list(_CASE_PAYLOADS))
def test_adopt_steers_downstream_scope(dry_run_env, slug):
    """The headline integrity test: after --adopt, SCOPE's rendered prompt
    sees BOTH the adopted direction (graph projection) AND the steering
    note (user_notes). This proves the IDEATE→SCOPE handoff works with the
    existing channels."""
    from omicairn import phases, stage as stage_mod
    with tempfile.TemporaryDirectory() as td:
        ws = _init_case(slug, pathlib.Path(td) / "ws")
        kg.ingest_ideation(ws.root, _CASE_PAYLOADS[slug],
                           source_stage="000_ideate")

        # Reproduce what `omicairn ideate --adopt <id>` does.
        top = kg.proposed_directions(ws.root)[0]
        goal = kg.adopt_direction(ws.root, top["id"])
        seeds = goal.extra.get("seed_hypotheses", [])
        note_body = (
            f"ADOPTED RESEARCH DIRECTION (from IDEATE): {goal.extra.get('label')}\n"
            f"Question: {goal.title}\n"
            + "\n".join(f"  - {h}" for h in seeds)
        )
        user_notes.append_note(ws.root, note_body, phase=None, once=False)

        # adoption materialized the seed hypotheses as real ones
        assert len(kg.hypotheses_for_goal(ws.root, goal.id)) == len(seeds) >= 1

        # Projection CONTENT marks the direction adopted (checked on the
        # rendered graph directly — the scope.md *template* also contains
        # the word, so we can't grep the full prompt for it).
        proj = kg.render_directions_md(ws.root)
        assert "✓ADOPTED" in proj

        # Render the SCOPE prompt and assert both channels carried through.
        scope_phase = phases.get(PhaseName.SCOPE)
        sref = stage_mod.StageRef(index=1, phase=PhaseName.SCOPE,
                                  path=ws.root / "001_scope", rel_path="001_scope")
        rendered = orchestrator._render_prompt(
            scope_phase, ws, sref, iter_count=0, max_iter=3, retry_note=None)
        # graph projection channel: the adopted direction's question appears
        assert goal.title[:40] in rendered
        # user-notes channel: the steering note body appears verbatim
        assert "ADOPTED RESEARCH DIRECTION (from IDEATE)" in rendered
        # no unresolved tokens leaked
        import re
        assert not re.findall(r"\{\{[a-z_]+\}\}", rendered)


def test_unadopted_directions_do_not_steer(dry_run_env):
    """Before any --adopt, SCOPE sees the proposed directions (for context)
    but none is marked adopted — the user is still in control."""
    from omicairn import phases, stage as stage_mod
    with tempfile.TemporaryDirectory() as td:
        ws = _init_case("ideate-parkinson-mechanism", pathlib.Path(td) / "ws")
        kg.ingest_ideation(ws.root, _PD_DIRECTIONS,
                           source_stage="000_ideate")
        scope_phase = phases.get(PhaseName.SCOPE)
        sref = stage_mod.StageRef(index=1, phase=PhaseName.SCOPE,
                                  path=ws.root / "001_scope", rel_path="001_scope")
        rendered = orchestrator._render_prompt(
            scope_phase, ws, sref, iter_count=0, max_iter=3, retry_note=None)
        # Nothing adopted: the directions-view has no ✓ADOPTED marker...
        assert "✓ADOPTED" not in kg.render_directions_md(ws.root)
        # ...and crucially, un-adopted brainstorm directions do NOT leak
        # into the SCOPE graph projection as goals (audit H1 fix). The
        # direction's question must NOT appear — SCOPE proceeds from
        # RESEARCH.md until the user adopts a direction. (The proposals
        # remain available via `omicairn ideate --list` / render_directions_md.)
        assert "distinguish vulnerable" not in rendered
        assert "distinguish vulnerable" in kg.render_directions_md(ws.root)


def test_ideate_sources_bridge_to_literature(tmp_path):
    """The IDEATE→literature/ bridge: surveyed `source` nodes are mapped
    to Paper entries and merged into literature/papers.json (+ abstracts +
    LITERATURE_INDEX) so the downstream pipeline inherits the bibliography.
    Metadata-only (no network)."""
    from omicairn import literature
    out = tmp_path / "literature"
    sources = [
        {"title": "Toy Models of Superposition", "summary": "features...",
         "source_type": "paper", "url": "https://x/a.pdf", "author": "Elhage",
         "year": 2022},
        {"title": "Bengio consciousness prior", "summary": "sparse factors",
         "source_type": "professor", "author": "Bengio", "year": 2017},
        {"title": "kept-min"},       # has title -> kept
        {"summary": "no title"},     # skipped
    ]
    counts = literature.export_sources_to_literature(out, sources, domain="ml",
                                                     download=False)
    assert counts["added"] == 3
    import json as _json
    papers = _json.loads((out / "papers.json").read_text())
    titles = {p["title"] for p in papers}
    assert "Toy Models of Superposition" in titles
    assert "Bengio consciousness prior" in titles
    bengio = next(p for p in papers if p["title"] == "Bengio consciousness prior")
    assert bengio["source_api"] == "ideate:professor"
    assert "professor" in bengio["domain_tags"]
    assert (out / "LITERATURE_INDEX.md").exists()
    assert list((out / "abstracts").glob("*.txt"))
    # idempotent: re-run adds nothing new
    c2 = literature.export_sources_to_literature(out, sources, domain="ml",
                                                 download=False)
    assert c2["added"] == 0


def test_ideate_ingest_populates_literature(dry_run_env):
    """End-to-end (dry-run): the IDEATE ingest hook also bridges the
    surveyed sources into literature/papers.json — closing the seam the
    user flagged (empty literature/ after ideate)."""
    with tempfile.TemporaryDirectory() as td:
        ws = _init_case("ideate-parkinson-mechanism", pathlib.Path(td) / "ws")
        stage = ws.root / "000_ideate"
        stage.mkdir()
        (stage / IDEATE_DIRECTIONS_FILE).write_text(json.dumps(_PD_DIRECTIONS))
        orchestrator._ingest_ideation_stage(ws, stage)
        lit = ws.root / "literature" / "papers.json"
        assert lit.exists()
        papers = json.loads(lit.read_text())
        assert len(papers) == 4
        assert any("Braak" in p["title"] for p in papers)


def test_cmd_ideate_retries_transient_contract_fail(dry_run_env, monkeypatch):
    """REGRESSION: `omicairn ideate` is standalone (no pipeline §3.1 retry).
    A transient CONTRACT_FAIL (empty claude output) must be retried up to
    max_iters_per_phase rather than leaving an empty ideate stage — a real
    50-source run hit this. Mock run_next_stage to fail once then succeed."""
    import argparse
    from omicairn import cli
    with tempfile.TemporaryDirectory() as td:
        ws = _init_case("ideate-parkinson-mechanism", pathlib.Path(td) / "ws")

        class _O:
            def __init__(self, status):
                self.status = status
                self.stage_dir_rel = "000_ideate"
                self.note = status

        calls = {"n": 0}

        def fake_run_next_stage(w, forced_phase=None):
            calls["n"] += 1
            return _O("CONTRACT_FAIL") if calls["n"] == 1 else _O("DONE")

        monkeypatch.setattr(cli.orchestrator, "run_next_stage", fake_run_next_stage)
        monkeypatch.setattr(cli.workspace, "load_workspace", lambda r: ws)
        args = argparse.Namespace(workspace=str(ws.root), list=False, adopt=None)
        rc = cli.cmd_ideate(args)
        assert calls["n"] == 2          # retried once
        assert rc == 0                  # succeeded on retry


def test_export_slug_collision_does_not_overwrite(tmp_path):
    """REGRESSION (adversarial audit): two distinct sources whose titles
    share a 60+ char prefix must NOT collide to one slug and overwrite
    each other's abstract/PDF."""
    from omicairn import literature
    prefix = "A" * 65
    out = tmp_path / "literature"
    sources = [
        {"title": prefix + " ALPHA", "summary": "alpha body",
         "source_type": "paper"},
        {"title": prefix + " BETA", "summary": "beta body",
         "source_type": "paper"},
    ]
    counts = literature.export_sources_to_literature(out, sources, download=False)
    assert counts["added"] == 2
    # two distinct abstract files (no overwrite)
    abstracts = sorted(p.read_text() for p in (out / "abstracts").glob("*.txt"))
    assert abstracts == ["alpha body", "beta body"]


def test_export_respects_total_byte_and_count_caps(tmp_path, monkeypatch):
    """REGRESSION (adversarial audit, DoS): the bridge must stop
    downloading once the aggregate byte cap (or count cap) is hit."""
    from omicairn import literature
    out = tmp_path / "literature"
    calls = {"n": 0}

    def fake_dl(url, dest, max_bytes=0):
        calls["n"] += 1
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"x" * 1000)
        return True

    monkeypatch.setattr(literature, "_download_file", fake_dl)
    sources = [{"title": f"S{i}", "summary": "s", "source_type": "paper",
                "url": f"https://example.com/{i}.pdf"} for i in range(10)]
    counts = literature.export_sources_to_literature(
        out, sources, download=True, max_total_bytes=3000)
    # stops downloading after ~3 files (3000 bytes), but still adds all metadata
    assert counts["downloaded"] <= 4
    assert counts["added"] == 10
    assert counts["download_capped"] is True


def test_ssrf_guard_off_by_default(monkeypatch):
    """SSRF guard is OFF by default (operator decision): nothing is
    blocked unless OMICAIRN_ENABLE_SSRF_GUARD is set."""
    from omicairn import literature
    from omicairn.constants import ENV_ENABLE_SSRF_GUARD
    monkeypatch.delenv(ENV_ENABLE_SSRF_GUARD, raising=False)
    assert literature._host_is_blocked("169.254.169.254") is False
    assert literature._host_is_blocked("localhost") is False
    assert literature._url_is_fetchable("http://localhost:9000/x") is True
    assert literature._url_is_fetchable("file:///etc/passwd") is True  # guard off → passthrough


def test_ssrf_guard_blocks_internal_when_enabled(monkeypatch):
    """When OMICAIRN_ENABLE_SSRF_GUARD is set, internal/metadata hosts and
    non-http schemes are refused; public https is allowed."""
    from omicairn import literature
    from omicairn.constants import ENV_ENABLE_SSRF_GUARD
    monkeypatch.setenv(ENV_ENABLE_SSRF_GUARD, "1")
    assert literature._host_is_blocked("169.254.169.254") is True   # cloud metadata
    assert literature._host_is_blocked("127.0.0.1") is True
    assert literature._host_is_blocked("10.0.0.5") is True
    assert literature._url_is_fetchable("file:///etc/passwd") is False
    assert literature._url_is_fetchable("ftp://x/y") is False
    assert literature._url_is_fetchable("http://169.254.169.254/") is False
    assert literature._url_is_fetchable("https://example.com/a.pdf") is True
    import tempfile, pathlib as _pl
    with tempfile.TemporaryDirectory() as td:
        assert literature._download_file("http://localhost:8080/x.pdf",
                                         _pl.Path(td) / "x.pdf") is False
