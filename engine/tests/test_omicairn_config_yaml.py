"""Tests for the omicairn_config.yaml overlay on RESEARCH.md.

The overlay separates orchestration knobs (config, machine-edited) from
the semantic research brief (RESEARCH.md, human-authored). When a
sibling omicairn_config.yaml exists, its values OVERRIDE the matching
RESEARCH.md fields. RESEARCH.md's `budgets:` / `bounds:` /
`auto_approve` / `data.shared_cache` blocks still work for back-compat
(they become the defaults).

The new `max_phase_wall_minutes_by_phase` block has no RESEARCH.md
counterpart and lives only in omicairn_config.yaml.

Why this design: the parkinson 15-case suite (May 2026) lost 1 of 15
runs (asyn-propagation) to a 4h global EXECUTE budget being hit on
an iterated retry. With per-phase budgets, the single heavy phase
could have been given more headroom without inflating SCOPE/PLAN/
REVIEW budgets that didn't need it.
"""
from __future__ import annotations

import pathlib
import tempfile

import pytest

from omicairn import research_md


_RESEARCH_MINIMAL = """\
research_mode: METHOD
domain: synthetic
topic_slug: pytest-cfg-overlay
research_question: |
  Does omicairn_config.yaml override RESEARCH.md correctly?
data:
  location: {data_dir}
  description: pytest fixture
budgets:
  max_phase_wall_minutes: 60
  max_total_wall_minutes: 180
  allow_web: false
  claude_model: claude-sonnet-4-6
  max_phase_budget_usd: 5.0
bounds:
  max_iters_per_phase: 3
  max_backtracks: 4
auto_approve: true
"""


def _write_case(tmpdir: pathlib.Path, cfg_text: str | None) -> pathlib.Path:
    """Write a RESEARCH.md + optional omicairn_config.yaml and return
    the RESEARCH.md path."""
    data_dir = tmpdir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "dummy.csv").write_text("x,y\n1,1\n")
    rmd = tmpdir / "RESEARCH.md"
    rmd.write_text("---\n" + _RESEARCH_MINIMAL.format(data_dir=data_dir) + "---\n")
    if cfg_text is not None:
        (tmpdir / "omicairn_config.yaml").write_text(cfg_text)
    return rmd


def test_no_omicairn_config_uses_research_md_values():
    """Absent omicairn_config.yaml: all orchestration values come from
    RESEARCH.md. The `max_phase_wall_minutes_by_phase` dict is empty."""
    with tempfile.TemporaryDirectory() as td:
        tmpdir = pathlib.Path(td)
        rmd = _write_case(tmpdir, cfg_text=None)
        rm = research_md.load(rmd)
        assert rm.max_phase_wall_minutes == 60
        assert rm.max_total_wall_minutes == 180
        assert rm.allow_web is False
        assert rm.claude_model == "claude-sonnet-4-6"
        assert rm.max_phase_budget_usd == 5.0
        assert rm.max_iters_per_phase == 3
        assert rm.max_backtracks == 4
        assert rm.auto_approve is True
        assert rm.max_phase_wall_minutes_by_phase == {}


def test_omicairn_config_overrides_research_md_budgets():
    """Present omicairn_config.yaml: its `budgets:` / `bounds:` /
    `auto_approve` values override RESEARCH.md."""
    cfg = """\
budgets:
  max_phase_wall_minutes: 240
  max_total_wall_minutes: 1440
  allow_web: true
  claude_model: claude-opus-4-7
  max_phase_budget_usd: 25.0
bounds:
  max_iters_per_phase: 5
  max_backtracks: 5
auto_approve: false
"""
    with tempfile.TemporaryDirectory() as td:
        tmpdir = pathlib.Path(td)
        rmd = _write_case(tmpdir, cfg_text=cfg)
        rm = research_md.load(rmd)
        assert rm.max_phase_wall_minutes == 240
        assert rm.max_total_wall_minutes == 1440
        assert rm.allow_web is True
        assert rm.claude_model == "claude-opus-4-7"
        assert rm.max_phase_budget_usd == 25.0
        assert rm.max_iters_per_phase == 5
        assert rm.max_backtracks == 5
        assert rm.auto_approve is False


def test_omicairn_config_per_phase_budget_dict():
    """The new `max_phase_wall_minutes_by_phase` block is parsed
    into a `dict[str, int]` on the ResearchMd dataclass."""
    cfg = """\
budgets:
  max_phase_wall_minutes: 60
  max_phase_wall_minutes_by_phase:
    execute: 480
    review: 120
    plan: 90
"""
    with tempfile.TemporaryDirectory() as td:
        tmpdir = pathlib.Path(td)
        rmd = _write_case(tmpdir, cfg_text=cfg)
        rm = research_md.load(rmd)
        assert rm.max_phase_wall_minutes_by_phase == {
            "execute": 480,
            "review": 120,
            "plan": 90,
        }
        # Other phases inherit the case-wide value.
        assert rm.max_phase_wall_minutes == 60


def test_omicairn_config_partial_overlay_keeps_research_md_for_missing_keys():
    """If omicairn_config.yaml only sets some keys, the unset keys fall
    back to RESEARCH.md. We don't want a half-config to silently zero
    out the others."""
    cfg = """\
budgets:
  max_phase_wall_minutes: 999
"""
    with tempfile.TemporaryDirectory() as td:
        tmpdir = pathlib.Path(td)
        rmd = _write_case(tmpdir, cfg_text=cfg)
        rm = research_md.load(rmd)
        # cfg-overridden
        assert rm.max_phase_wall_minutes == 999
        # NOT cfg-overridden — comes from RESEARCH.md
        assert rm.max_total_wall_minutes == 180
        assert rm.max_iters_per_phase == 3
        assert rm.allow_web is False


def test_omicairn_config_malformed_per_phase_dict_raises():
    """A `max_phase_wall_minutes_by_phase` that isn't a mapping is a
    structural config error and must fail loud."""
    cfg = """\
budgets:
  max_phase_wall_minutes_by_phase:
    - this
    - is
    - a list not a dict
"""
    with tempfile.TemporaryDirectory() as td:
        tmpdir = pathlib.Path(td)
        rmd = _write_case(tmpdir, cfg_text=cfg)
        with pytest.raises(ValueError, match="must be a mapping"):
            research_md.load(rmd)


def test_omicairn_config_yaml_round_trips_through_workspace_init_and_load():
    """REGRESSION: `omicairn init` must copy omicairn_config.yaml into the
    workspace dir; `load_workspace` (used by `omicairn run`) must then
    see the overlay.

    Bug context: commit 17fdc98 added the overlay parser to
    research_md.load(), but `workspace.init` only copied RESEARCH.md.
    Under the standard CLI flow `omicairn init … && omicairn run <ws>` the
    overlay was silently lost on every run. Caught by code reviewer
    2026-05-23 on parkinson-paperless §3.4.
    """
    from omicairn import workspace

    cfg = """\
budgets:
  max_phase_wall_minutes: 240
  max_total_wall_minutes: 1440
  max_phase_wall_minutes_by_phase:
    execute: 360
    review: 120
  max_phase_budget_usd: 25.0
  allow_web: true
  claude_model: claude-sonnet-4-6
bounds:
  max_iters_per_phase: 5
  max_backtracks: 5
auto_approve: true
"""
    with tempfile.TemporaryDirectory() as td:
        tmpdir = pathlib.Path(td)
        rmd = _write_case(tmpdir, cfg_text=cfg)
        ws_root = tmpdir / "workspaces"
        ws_root.mkdir()
        # Use OMICAIRN_DRY_RUN to skip literature fetch in workspace.init.
        import os
        from omicairn.constants import ENV_DRY_RUN
        prev = os.environ.get(ENV_DRY_RUN)
        os.environ[ENV_DRY_RUN] = "1"
        try:
            ws = workspace.init(
                "pytest-rt",
                research_md_path=rmd,
                workspaces_root=ws_root,
                skip_literature=True,
            )
        finally:
            if prev is None:
                os.environ.pop(ENV_DRY_RUN, None)
            else:
                os.environ[ENV_DRY_RUN] = prev
        # Confirm overlay was honored at init time.
        assert ws.research_md.max_phase_wall_minutes_by_phase == {
            "execute": 360, "review": 120,
        }
        # CRITICAL: the file must be present in the workspace.
        ws_cfg = ws.root / "omicairn_config.yaml"
        assert ws_cfg.exists(), (
            f"omicairn_config.yaml not copied into workspace at {ws_cfg}; "
            f"`omicairn run` would silently drop the overlay"
        )
        # Round-trip: load_workspace must re-parse and produce the
        # same overlay values that init saw.
        ws2 = workspace.load_workspace(ws.root)
        assert ws2.research_md.max_phase_wall_minutes_by_phase == {
            "execute": 360, "review": 120,
        }, (
            f"overlay lost on load_workspace round-trip; "
            f"got {ws2.research_md.max_phase_wall_minutes_by_phase!r}"
        )
        # Also check the case-wide values survive the round-trip.
        assert ws2.research_md.max_phase_wall_minutes == 240
        assert ws2.research_md.max_iters_per_phase == 5


def test_parkinson_cases_all_load_under_overlay():
    """CI lint: every parkinson-* test case must parse cleanly via
    research_md.load(), the per-phase override dict must be valid,
    and (for the 6 paperless cases) at least one EXECUTE override
    must be present.

    Locks in the §3.4b migration from the parkinson-paperless fix.
    """
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    cases_dir = repo_root / "research_cases"
    pk_cases = sorted(cases_dir.glob("parkinson-*"))
    assert len(pk_cases) == 15, (
        f"expected 15 parkinson-* cases; got {len(pk_cases)}"
    )
    # Cases that were paperless in the May-2026 run; their
    # omicairn_config.yaml should declare a stratified per-phase budget.
    PAPERLESS = {
        "parkinson-foundation-embedding",
        "parkinson-lincs-reversal",
        "parkinson-prs-portability",
        "parkinson-multimodal-spatial-proteomics",
        "parkinson-cellchat-immune-axis",
        "parkinson-asyn-propagation",
    }
    for case_dir in pk_cases:
        rmd = case_dir / "RESEARCH.md"
        assert rmd.exists(), f"missing RESEARCH.md in {case_dir}"
        cfg = case_dir / "omicairn_config.yaml"
        assert cfg.exists(), (
            f"missing omicairn_config.yaml in {case_dir} "
            f"(per design rule guardrail 5b)"
        )
        from omicairn import research_md
        rm = research_md.load(rmd)
        assert rm.max_phase_wall_minutes > 0
        assert rm.max_total_wall_minutes > 0
        assert rm.max_iters_per_phase > 0
        assert rm.claude_model.startswith("claude-")
        if case_dir.name in PAPERLESS:
            by_phase = rm.max_phase_wall_minutes_by_phase
            assert "execute" in by_phase, (
                f"paperless case {case_dir.name} must declare an "
                f"`execute:` per-phase budget (its previous run hit "
                f"the case-wide 4h ceiling); got {by_phase!r}"
            )


def test_omicairn_config_shared_cache_overrides_research_md():
    """omicairn_config.yaml's `shared_cache:` takes precedence over the
    RESEARCH.md `data.shared_cache:` value."""
    cfg = """\
shared_cache: ./datasets-from-config
"""
    with tempfile.TemporaryDirectory() as td:
        tmpdir = pathlib.Path(td)
        rmd = _write_case(tmpdir, cfg_text=cfg)
        rm = research_md.load(rmd)
        assert rm.shared_cache_override is not None
        assert "datasets-from-config" in str(rm.shared_cache_override)


def test_legacy_paper_author_in_config_is_ignored():
    """A paper-era `paper_author:` in omicairn_config.yaml is ignored with a
    FutureWarning instead of failing the load."""
    cfg = "paper_author: Dr. Jane Doe, Example Lab\n"
    with tempfile.TemporaryDirectory() as td:
        rmd = _write_case(pathlib.Path(td), cfg_text=cfg)
        with pytest.warns(FutureWarning, match="paper_author"):
            rm = research_md.load(rmd)
        assert not hasattr(rm, "paper_author")
