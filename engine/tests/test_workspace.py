"""Unit tests for omicairn.workspace init behavior.

These tests focus on the dev-loop-critical invariant: `omicairn init` must
NEVER touch the network when OMICAIRN_DRY_RUN=1 is set, even if
--skip-literature was not passed. A developer running
`omicairn init && omicairn run --dry-run` should be able to work fully offline.
"""
from __future__ import annotations

import os
import pathlib
import tempfile
from typing import Iterator

import pytest

from omicairn import literature, workspace
from omicairn.constants import ENV_DRY_RUN


RESEARCH_TEMPLATE = """\
---
research_mode: METHOD
domain: synthetic_smoke_test
topic_slug: pytest-workspace
research_question: |
  Does workspace.init behave when the network is not available?
data:
  location: {data_dir}
  description: synthetic pytest fixture
  cohorts: [a]
  modalities: [tabular]
success_criteria:
  primary_metric: auc
  baseline: null_model
bounds:
  max_iters_per_phase: 3
  max_backtracks: 5
budgets:
  max_phase_wall_minutes: 5
  allow_web: false
  claude_model: claude-sonnet-4-6
  max_phase_budget_usd: 1.0
auto_approve: true
---

# workspace init fixture
"""


@pytest.fixture
def tmp_workspace_root() -> Iterator[pathlib.Path]:
    with tempfile.TemporaryDirectory() as td:
        tmpdir = pathlib.Path(td)
        data_dir = tmpdir / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "dummy.csv").write_text("x,y\n1,1\n")
        rmd_path = tmpdir / "RESEARCH.md"
        rmd_path.write_text(RESEARCH_TEMPLATE.format(data_dir=data_dir))
        ws_root = tmpdir / "workspaces"
        ws_root.mkdir()
        yield tmpdir  # caller reaches in for rmd_path + ws_root


def _paths(tmpdir: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    return tmpdir / "RESEARCH.md", tmpdir / "workspaces"


def _trap_fetch(monkeypatch) -> dict:
    """Replace literature.fetch with a recorder that fails the test if
    called. Returns a dict that will be populated with the call args if
    it IS called (used by the 'should call' test)."""
    called: dict = {"count": 0, "args": None}

    def tripwire(*args, **kwargs):
        called["count"] += 1
        called["args"] = (args, kwargs)
        return []

    monkeypatch.setattr(literature, "fetch", tripwire)
    return called


# ---------------------------------------------------------------------------
# OMICAIRN_DRY_RUN=1 must suppress network fetch
# ---------------------------------------------------------------------------

def test_init_under_dry_run_does_not_call_literature_fetch(
    monkeypatch, tmp_workspace_root
):
    called = _trap_fetch(monkeypatch)
    monkeypatch.setenv(ENV_DRY_RUN, "1")

    rmd_path, ws_root = _paths(tmp_workspace_root)
    ws = workspace.init(
        topic_slug="pytest-workspace",
        research_md_path=rmd_path,
        workspaces_root=ws_root,
        skip_literature=False,  # critical: we do NOT opt out explicitly
    )

    assert called["count"] == 0, (
        "workspace.init must NOT call literature.fetch when "
        f"OMICAIRN_DRY_RUN=1; got {called['count']} calls"
    )
    # Literature subdirs still exist — they're part of the workspace
    # scaffold regardless of whether we actually fetch.
    assert (ws.root / "literature").is_dir()
    assert (ws.root / "literature" / "abstracts").is_dir()
    assert (ws.root / "literature" / "pdfs").is_dir()
    assert (ws.root / "literature" / "fulltext").is_dir()
    # A placeholder README explains the suppression.
    readme = ws.root / "literature" / "README.md"
    assert readme.exists()
    assert "OMICAIRN_DRY_RUN" in readme.read_text()


def test_init_with_skip_literature_does_not_call_literature_fetch(
    monkeypatch, tmp_workspace_root
):
    """Even without OMICAIRN_DRY_RUN, --skip-literature must suppress the
    fetch. This is how tests/CI pipelines should run normally."""
    called = _trap_fetch(monkeypatch)
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)

    rmd_path, ws_root = _paths(tmp_workspace_root)
    ws = workspace.init(
        topic_slug="pytest-workspace",
        research_md_path=rmd_path,
        workspaces_root=ws_root,
        skip_literature=True,
    )

    assert called["count"] == 0
    assert (ws.root / "literature").is_dir()


def test_init_without_dry_run_or_skip_does_call_literature_fetch(
    monkeypatch, tmp_workspace_root
):
    """Positive case: when neither OMICAIRN_DRY_RUN nor --skip-literature is
    set, workspace.init SHOULD call literature.fetch. The tripwire
    doesn't actually fetch anything (we monkeypatched it to return []),
    so the test still runs offline."""
    called = _trap_fetch(monkeypatch)
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)

    rmd_path, ws_root = _paths(tmp_workspace_root)
    ws = workspace.init(
        topic_slug="pytest-workspace",
        research_md_path=rmd_path,
        workspaces_root=ws_root,
        skip_literature=False,
    )

    assert called["count"] == 1, (
        f"expected exactly 1 call to literature.fetch; got {called['count']}"
    )
    # The fetch was called with our ResearchMd and the right output dir.
    args, kwargs = called["args"]
    assert kwargs.get("output_dir") == ws.root / "literature"


# ---------------------------------------------------------------------------
# Workspace dataclass property pins
#
# The `Workspace` dataclass exposes five path properties (state_path,
# claude_md_path, research_md_path, events_log_path, knowledge_dir).
# Only state_path and logs_dir are read anywhere in the codebase; the
# other four are convenience accessors for orchestrator/inspector
# callers and were never exercised, so a typo in any of them (e.g.
# "CLAUDE.MD" or "Research.md") would not be caught. Pin all four.
# ---------------------------------------------------------------------------

def test_workspace_path_properties_match_files_on_disk(
    monkeypatch, tmp_workspace_root
):
    """Lines 60/64/68/72: claude_md_path, research_md_path,
    events_log_path, knowledge_dir. init() writes every one of these
    files/dirs, so the property values must line up with what
    actually exists on disk."""
    _trap_fetch(monkeypatch)
    monkeypatch.setenv(ENV_DRY_RUN, "1")

    rmd_path, ws_root = _paths(tmp_workspace_root)
    ws = workspace.init(
        topic_slug="pytest-workspace",
        research_md_path=rmd_path,
        workspaces_root=ws_root,
        skip_literature=True,
    )

    # RESEARCH.md — copied verbatim from the input.
    assert ws.research_md_path == ws.root / "RESEARCH.md"
    assert ws.research_md_path.exists()

    # CLAUDE.md — rendered from templates.
    assert ws.claude_md_path == ws.root / "CLAUDE.md"
    assert ws.claude_md_path.exists()

    # events.jsonl — written by the workspace_init event append.
    from omicairn.constants import EVENTS_LOG
    assert ws.events_log_path == ws.root / EVENTS_LOG
    assert ws.events_log_path.exists()

    # knowledge/ — created by ensure_knowledge_scaffold.
    from omicairn.constants import KNOWLEDGE_DIR
    assert ws.knowledge_dir == ws.root / KNOWLEDGE_DIR
    assert ws.knowledge_dir.is_dir()


def test_init_fetch_failure_is_non_fatal(monkeypatch, tmp_workspace_root):
    """When literature.fetch raises (e.g. network error, API 500),
    workspace.init must NOT propagate the exception — it should fall
    back to a README placeholder explaining the failure."""
    def boom(*args, **kwargs):
        raise RuntimeError("simulated API outage")

    monkeypatch.setattr(literature, "fetch", boom)
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)

    rmd_path, ws_root = _paths(tmp_workspace_root)
    ws = workspace.init(
        topic_slug="pytest-workspace",
        research_md_path=rmd_path,
        workspaces_root=ws_root,
        skip_literature=False,
    )

    # The workspace was still created.
    assert ws.root.exists()
    readme = ws.root / "literature" / "README.md"
    assert readme.exists()
    assert "simulated API outage" in readme.read_text()


# ---------------------------------------------------------------------------
# Shipped-fixture smoke: workspace.init() must succeed against every
# RESEARCH.md under research_cases/
# ---------------------------------------------------------------------------
#
# test_research_md.py already validates that every shipped RESEARCH.md
# PARSES cleanly through research_md.load(). This is the dual — the
# full workspace.init() path, which exercises:
#
#   - research_md.load()   [already covered]
#   - knowledge scaffold creation
#   - RESEARCH.md copy-into-workspace
#   - CLAUDE.md + settings.json template rendering
#   - agents/*.md copy-into-workspace
#   - state.initial_state() + write
#   - events.jsonl init append
#   - literature scaffold (skipped in dry-run, but dirs still created)
#
# A regression anywhere along that chain that's specific to a fixture
# (e.g. an unusual research_mode string that trips a template path, a
# long topic_slug that breaks a file name, a cohort list that triggers
# a state.initial_state branch) is otherwise invisible until a real
# `omicairn run` kicks off.

_RESEARCH_CASES_DIR = (
    pathlib.Path(__file__).resolve().parents[2] / "research_cases"
)


def _shipped_research_md_files() -> list[pathlib.Path]:
    if not _RESEARCH_CASES_DIR.is_dir():
        return []
    return sorted(
        p / "RESEARCH.md"
        for p in _RESEARCH_CASES_DIR.iterdir()
        if p.is_dir() and (p / "RESEARCH.md").is_file()
    )


@pytest.mark.skipif(
    not _shipped_research_md_files(),
    reason="research_cases/ directory not present in this checkout",
)
@pytest.mark.parametrize(
    "rmd_path",
    _shipped_research_md_files(),
    ids=lambda p: p.parent.name,
)
def test_workspace_init_succeeds_for_every_shipped_research_md(
    rmd_path, monkeypatch, tmp_path
):
    """workspace.init() must run cleanly against every shipped
    RESEARCH.md under dry-run mode. Validates that the full init
    scaffold (CLAUDE.md, STATE.md, events.jsonl, knowledge/,
    .claude/agents/, literature/) gets created for each fixture.

    This is the integration dual of the research_md.load() smoke
    test — catches a regression anywhere along the init chain,
    not just in the YAML parser."""
    # Dry-run so init doesn't touch the network.
    monkeypatch.setenv(ENV_DRY_RUN, "1")
    # Additionally trap the literature fetcher so any regression
    # that leaked past OMICAIRN_DRY_RUN's check would still be caught
    # instead of accidentally hitting the network.
    _trap_fetch(monkeypatch)

    ws_root = tmp_path / "workspaces"
    ws_root.mkdir()

    # Load the fixture ourselves first so we can validate that the
    # rendered workspace reflects what the RESEARCH.md actually
    # declares — which may be different from the directory name
    # (several shipped fixtures have a directory stem that differs
    # from the internal topic_slug, e.g. a dated folder → "mitoxy-brca").
    from omicairn import research_md
    rmd = research_md.load(rmd_path)

    ws = workspace.init(
        topic_slug=rmd.topic_slug,
        research_md_path=rmd_path,
        workspaces_root=ws_root,
        skip_literature=True,
    )

    # Core scaffold files and dirs must all exist.
    assert ws.root.is_dir()
    assert (ws.root / "RESEARCH.md").is_file()
    assert (ws.root / "CLAUDE.md").is_file()
    assert (ws.root / "STATE.md").is_file()
    assert (ws.root / ".claude" / "settings.json").is_file()
    assert (ws.root / ".claude" / "agents").is_dir()
    # At least one agent file should have been copied in.
    assert list((ws.root / ".claude" / "agents").glob("*.md"))
    # Events log got its first entry from the workspace_init append.
    assert (ws.root / "events.jsonl").is_file()
    assert (ws.root / "events.jsonl").read_text().strip()
    # Knowledge scaffold.
    assert (ws.root / "knowledge").is_dir()
    # Literature scaffold (dirs always created even when fetch is
    # skipped under dry-run).
    assert (ws.root / "literature").is_dir()
    # CLAUDE.md template was rendered — the topic_slug token must
    # have been substituted to the actual slug from RESEARCH.md.
    claude_md_text = (ws.root / "CLAUDE.md").read_text()
    assert rmd.topic_slug in claude_md_text, (
        f"CLAUDE.md for {rmd_path.parent.name} does not contain its "
        f"declared topic_slug {rmd.topic_slug!r} — template "
        f"rendering likely broken."
    )
