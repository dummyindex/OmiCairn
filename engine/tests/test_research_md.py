"""Unit tests for omicairn.research_md — YAML frontmatter parser.

research_md.py is the single human-authored entry point into every
OmiCairn run. A parser bug here propagates into STATE.md, bounds checks,
budgets, and every downstream phase — and the failure mode is usually
"looks fine, just silently wrong". These tests pin the parser's:

1. Required-field validation (topic_slug, research_question, data.location)
2. Default values for every optional field
3. Type coercion (int, float, bool)
4. Env-var and `~` interpolation in `data.location`
5. Frontmatter detection — the parser must handle both pure-YAML and
   Markdown-with-`---`-frontmatter styles
6. Graceful rejection of non-mapping top-level YAML
"""
from __future__ import annotations

import os
import pathlib

import pytest

from omicairn import research_md
from omicairn.constants import (
    DEFAULT_MAX_BACKTRACKS,
    DEFAULT_MAX_ITERS_PER_PHASE,
    DEFAULT_MAX_PHASE_WALL_MINUTES,
    DEFAULT_MAX_TOTAL_WALL_MINUTES,
    DEFAULT_RUN_BUDGET_USD,
)


def _write(tmp_path: pathlib.Path, body: str, name: str = "RESEARCH.md") -> pathlib.Path:
    p = tmp_path / name
    p.write_text(body)
    return p


# ---------------------------------------------------------------------------
# Minimal valid file — every default should resolve cleanly
# ---------------------------------------------------------------------------

_MINIMAL = """\
---
topic_slug: minimal-case
research_question: |
  Does the minimal parser path work?
data:
  location: {data_dir}
---
"""


def test_parse_minimal_frontmatter(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    p = _write(tmp_path, _MINIMAL.format(data_dir=data_dir))
    rmd = research_md.load(p)
    # Required fields round-trip.
    assert rmd.topic_slug == "minimal-case"
    assert "minimal parser" in rmd.research_question
    assert rmd.data_location == data_dir.resolve()
    # Defaults that must survive an otherwise-empty file.
    assert rmd.research_mode == "METHOD"
    assert rmd.domain == "general"
    assert not hasattr(rmd, "target_venue")
    assert rmd.claude_model == "claude-opus-4-8"
    assert rmd.allow_web is True
    # No compute: block → CPU-only DEFAULT_LOCAL fallback (historical).
    assert rmd.compute.id == "default-local"
    assert rmd.compute.has_gpu is False
    assert rmd.auto_approve is True
    # Per-phase LLM dollar cap defaults to UNLIMITED (None) per owner directive
    # 2026-06-27 — the runner then OMITS --max-budget-usd so no phase self-halts
    # on spend. Hoisted into the named constant DEFAULT_RUN_BUDGET_USD.
    assert rmd.max_phase_budget_usd is None
    assert rmd.max_phase_budget_usd == DEFAULT_RUN_BUDGET_USD
    assert rmd.max_iters_per_phase == DEFAULT_MAX_ITERS_PER_PHASE
    assert rmd.max_backtracks == DEFAULT_MAX_BACKTRACKS
    # Wall budgets default to the RAISED 3-day envelope (raise-compute-
    # envelope, 2026-06-19): total = 3 days, per-phase = 1 day, both hoisted
    # into named constants (no bare 60/180 literals).
    assert rmd.max_total_wall_minutes == DEFAULT_MAX_TOTAL_WALL_MINUTES
    assert rmd.max_total_wall_minutes == 4320          # 3 days
    assert rmd.max_phase_wall_minutes == DEFAULT_MAX_PHASE_WALL_MINUTES
    assert rmd.max_phase_wall_minutes == 1440          # 1 day
    # Unset lists default to empty, not None (callers index without
    # None-checking).
    assert rmd.cohorts == []
    assert rmd.modalities == []


def test_wall_budget_defaults_are_three_day_envelope(tmp_path):
    """The case-level wall DEFAULTS are the 3-day envelope; an explicit
    omicairn_config/RESEARCH.md value still overrides (no behaviour regression for
    cases that set their own budget). The inactivity WATCHDOG is a separate
    knob (OMICAIRN_INACTIVITY_TIMEOUT_S env) and is NOT touched by these defaults."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    # Default (no budgets: block) → 3-day total / 1-day per-phase.
    rmd = research_md.load(_write(tmp_path, _MINIMAL.format(data_dir=data_dir)))
    assert rmd.max_total_wall_minutes == 4320
    assert rmd.max_phase_wall_minutes == 1440
    # Per-phase total is generous but BOUNDED (< the 3-day total), so a single
    # runaway phase cannot silently consume the whole budget.
    assert rmd.max_phase_wall_minutes < rmd.max_total_wall_minutes
    # Explicit override still wins (the default is only a fallback).
    body = _MINIMAL.format(data_dir=data_dir).replace(
        "---\n", "---\nbudgets:\n  max_total_wall_minutes: 90\n", 1)
    over = research_md.load(_write(tmp_path, body, name="OVERRIDE.md"))
    assert over.max_total_wall_minutes == 90


# ---------------------------------------------------------------------------
# Legacy paper-era keys are ignored with a FutureWarning, never rejected
# ---------------------------------------------------------------------------

def test_legacy_paper_keys_are_ignored_with_warning(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    body = _MINIMAL.format(data_dir=data_dir).replace(
        "---\n",
        "---\ntarget_venue: nature_communications\n"
        "paper_author: Dr. Jane Doe\n"
        "citation_gate:\n  mode: block\n"
        "depth:\n  citation_target: 40\n  min_datasets: 2\n",
        1,
    )
    with pytest.warns(FutureWarning) as rec:
        rmd = research_md.load(_write(tmp_path, body, name="LEGACY.md"))
    msg = " ".join(str(w.message) for w in rec)
    for key in ("target_venue", "paper_author", "citation_gate",
                "depth.citation_target"):
        assert key in msg
    assert rmd.topic_slug == "minimal-case"
    assert "citation_target" not in rmd.depth_config
    assert rmd.depth_config["min_datasets"] == 2
    for attr in ("target_venue", "paper_author", "citation_gate_config"):
        assert not hasattr(rmd, attr)


def test_no_deprecation_warning_without_legacy_keys(tmp_path, recwarn):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    research_md.load(_write(tmp_path, _MINIMAL.format(data_dir=data_dir)))
    assert not [w for w in recwarn if issubclass(w.category, FutureWarning)]


# ---------------------------------------------------------------------------
# Required fields
# ---------------------------------------------------------------------------

def test_missing_topic_slug_raises(tmp_path):
    body = """\
---
research_question: |
  Missing topic_slug.
data:
  location: /tmp
---
"""
    p = _write(tmp_path, body)
    with pytest.raises(ValueError, match="topic_slug"):
        research_md.load(p)


def test_missing_research_question_raises(tmp_path):
    body = """\
---
topic_slug: foo
data:
  location: /tmp
---
"""
    p = _write(tmp_path, body)
    with pytest.raises(ValueError, match="research_question"):
        research_md.load(p)


def test_missing_data_location_raises(tmp_path):
    body = """\
---
topic_slug: foo
research_question: Why?
data:
  description: has no location
---
"""
    p = _write(tmp_path, body)
    with pytest.raises(ValueError, match="data.location"):
        research_md.load(p)


# ---------------------------------------------------------------------------
# Null values for required fields must raise (not silently coerce to "None")
# ---------------------------------------------------------------------------
#
# YAML null shows up several ways:
#   location: null     # explicit
#   location: ~        # bare tilde (YAML's shorthand for null)
#   location:          # empty value after the colon (also null)
#
# All three previously passed the "key present" check in `_get`, then got
# `str()`-coerced to the literal string "None" and resolved relative to
# cwd. That produced a downstream file-not-found error from deep inside
# the pipeline instead of a clean validation error at load time. The
# `_get` fix rejects None for required=True fields; these tests pin it.

def test_null_data_location_explicit_null_raises(tmp_path):
    body = """\
---
topic_slug: foo
research_question: Why?
data:
  location: null
---
"""
    p = _write(tmp_path, body)
    with pytest.raises(ValueError, match="data.location"):
        research_md.load(p)


def test_null_data_location_bare_tilde_raises(tmp_path):
    """Bare `~` is YAML null (not a home-dir placeholder). Without the
    null-check, this becomes str(None)='None' and resolves to <cwd>/None."""
    body = """\
---
topic_slug: foo
research_question: Why?
data:
  location: ~
---
"""
    p = _write(tmp_path, body)
    with pytest.raises(ValueError, match="data.location"):
        research_md.load(p)


def test_null_data_location_empty_value_raises(tmp_path):
    """`location:` with nothing after the colon is also YAML null."""
    body = """\
---
topic_slug: foo
research_question: Why?
data:
  location:
---
"""
    p = _write(tmp_path, body)
    with pytest.raises(ValueError, match="data.location"):
        research_md.load(p)


def test_null_topic_slug_raises(tmp_path):
    """Every required field shares the same _get codepath, so the
    null-check must protect topic_slug too."""
    body = """\
---
topic_slug: null
research_question: Why?
data:
  location: /tmp
---
"""
    p = _write(tmp_path, body)
    with pytest.raises(ValueError, match="topic_slug"):
        research_md.load(p)


def test_null_research_question_raises(tmp_path):
    body = """\
---
topic_slug: foo
research_question:
data:
  location: /tmp
---
"""
    p = _write(tmp_path, body)
    with pytest.raises(ValueError, match="research_question"):
        research_md.load(p)


def test_null_optional_field_still_uses_default(tmp_path):
    """Regression guard for the fix: `_get(required=False)` must still
    return a None if explicitly set, because the default-chain depends
    on it for optional fields (e.g. baseline can legitimately be null).
    The null-check only fires on required=True."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    body = f"""\
---
topic_slug: foo
research_question: q
data:
  location: {data_dir}
success_criteria:
  primary_metric: auc
  baseline: null
---
"""
    p = _write(tmp_path, body)
    rmd = research_md.load(p)
    # baseline is optional: null passes through to the field.
    assert rmd.baseline is None
    assert rmd.primary_metric == "auc"


# ---------------------------------------------------------------------------
# Non-mapping top-level YAML must raise, not silently pass
# ---------------------------------------------------------------------------

def test_non_mapping_top_level_raises(tmp_path):
    p = _write(tmp_path, "---\n- a\n- b\n- c\n---\n")
    with pytest.raises(ValueError, match="mapping"):
        research_md.load(p)


# ---------------------------------------------------------------------------
# Type coercion
# ---------------------------------------------------------------------------

_TYPED = """\
---
topic_slug: typed-case
research_question: Are types coerced correctly?
data:
  location: {data_dir}
budgets:
  max_phase_wall_minutes: "42"
  max_phase_budget_usd: "7.5"
  allow_web: false
bounds:
  max_iters_per_phase: "4"
  max_backtracks: "9"
auto_approve: "yes"
---
"""


def test_int_and_float_fields_are_coerced(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    p = _write(tmp_path, _TYPED.format(data_dir=data_dir))
    rmd = research_md.load(p)
    # int(str) must work.
    assert rmd.max_phase_wall_minutes == 42
    assert isinstance(rmd.max_phase_wall_minutes, int)
    assert rmd.max_iters_per_phase == 4
    assert rmd.max_backtracks == 9
    # float(str) must work.
    assert rmd.max_phase_budget_usd == 7.5
    assert isinstance(rmd.max_phase_budget_usd, float)


def test_phase_budget_default_is_unlimited(tmp_path):
    """Owner directive 2026-06-27 "set cap to unlimited for ALL of our research
    runs": with no budgets.max_phase_budget_usd configured, the per-phase dollar
    cap is None = UNLIMITED (the runner omits --max-budget-usd → no spend halt).
    A case may still opt into a finite numeric cap, which still coerces to a
    float (the per-case override path is untouched)."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    # (a) Unconfigured → unlimited (None), tied to the named constant.
    rmd = research_md.load(_write(tmp_path, _MINIMAL.format(data_dir=data_dir)))
    assert rmd.max_phase_budget_usd is None
    assert rmd.max_phase_budget_usd == DEFAULT_RUN_BUDGET_USD
    # (b) Configured finite cap → that float (per-case override still works).
    body = (
        "---\n"
        "topic_slug: capped-case\n"
        "research_question: Does the per-case override still cap?\n"
        "data:\n"
        f"  location: {data_dir}\n"
        "budgets:\n"
        "  max_phase_budget_usd: 25.0\n"
        "---\n"
    )
    capped = research_md.load(_write(tmp_path, body, name="CAPPED.md"))
    assert capped.max_phase_budget_usd == 25.0
    assert isinstance(capped.max_phase_budget_usd, float)


def test_bool_fields_respect_yaml_parse(tmp_path):
    """allow_web: false in YAML must parse as Python False, not the
    string 'false' (which bool(...) would coerce to True)."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    p = _write(tmp_path, _TYPED.format(data_dir=data_dir))
    rmd = research_md.load(p)
    assert rmd.allow_web is False


# ---------------------------------------------------------------------------
# Env var interpolation in data.location
# ---------------------------------------------------------------------------

def test_env_var_interpolation_in_data_location(tmp_path, monkeypatch):
    data_dir = tmp_path / "from-env"
    data_dir.mkdir()
    monkeypatch.setenv("TEST_DATA_ROOT", str(tmp_path))
    body = """\
---
topic_slug: env-var-case
research_question: Interpolation?
data:
  location: $TEST_DATA_ROOT/from-env
---
"""
    p = _write(tmp_path, body)
    rmd = research_md.load(p)
    assert rmd.data_location == data_dir.resolve()


def test_tilde_expansion_in_data_location(tmp_path):
    # Note: bare `~` in YAML parses as null, so we must quote it. The
    # parser uses os.path.expanduser on the string value, so a quoted
    # '~/some/subpath' string round-trips correctly.
    body = """\
---
topic_slug: tilde-case
research_question: Does ~ expand inside a quoted path?
data:
  location: "~/some/omicairn-test-subdir"
---
"""
    p = _write(tmp_path, body)
    rmd = research_md.load(p)
    expected = pathlib.Path(
        os.path.expanduser("~/some/omicairn-test-subdir")
    ).resolve()
    assert rmd.data_location == expected


# ---------------------------------------------------------------------------
# Frontmatter detection
# ---------------------------------------------------------------------------

def test_parse_markdown_with_frontmatter_and_body(tmp_path):
    """RESEARCH.md commonly has a human-readable body after the
    closing ---. The parser must not choke on it."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    body = f"""\
---
topic_slug: body-case
research_question: Does body after frontmatter work?
data:
  location: {data_dir}
---

# Human-readable narrative

This is the body of RESEARCH.md. It is NOT parsed as YAML.
It may contain `---` characters, bullets, everything.
"""
    p = _write(tmp_path, body)
    rmd = research_md.load(p)
    assert rmd.topic_slug == "body-case"


def test_parse_pure_yaml_no_frontmatter_markers(tmp_path):
    """A pure-YAML file without --- delimiters must also parse."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    body = f"""\
topic_slug: pure-yaml
research_question: Does pure YAML parse?
data:
  location: {data_dir}
"""
    p = _write(tmp_path, body)
    rmd = research_md.load(p)
    assert rmd.topic_slug == "pure-yaml"


# ---------------------------------------------------------------------------
# data subsection
# ---------------------------------------------------------------------------

def test_cohorts_and_modalities_are_preserved(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    body = f"""\
---
topic_slug: lists-case
research_question: Do lists round-trip?
data:
  location: {data_dir}
  description: fixture
  cohorts: [tcga, metabric, cptac]
  modalities: [rnaseq, wes]
---
"""
    p = _write(tmp_path, body)
    rmd = research_md.load(p)
    assert rmd.cohorts == ["tcga", "metabric", "cptac"]
    assert rmd.modalities == ["rnaseq", "wes"]
    assert rmd.data_description == "fixture"


def test_success_criteria_primary_metric_and_baseline(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    body = f"""\
---
topic_slug: crit-case
research_question: Are primary_metric and baseline parsed?
data:
  location: {data_dir}
success_criteria:
  primary_metric: c_index
  baseline: grade_only
  pre_registration: required
  honest_validation: required
---
"""
    p = _write(tmp_path, body)
    rmd = research_md.load(p)
    assert rmd.primary_metric == "c_index"
    assert rmd.baseline == "grade_only"
    # pre_registration/honest_validation: "required" → True.
    assert rmd.pre_registration is True
    assert rmd.honest_validation is True


def test_pre_registration_optional_parses_as_false(tmp_path):
    """When pre_registration is anything other than 'required', it must
    default to False. Catches a regression where any truthy string
    would flip the bool."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    body = f"""\
---
topic_slug: optional-prereg
research_question: Default pre_registration?
data:
  location: {data_dir}
success_criteria:
  pre_registration: optional
---
"""
    p = _write(tmp_path, body)
    rmd = research_md.load(p)
    assert rmd.pre_registration is False
    assert rmd.honest_validation is False


def test_data_dir_is_alias_for_data_location(tmp_path):
    """ResearchMd.data_dir (line 48) is a property alias for
    data_location kept for callers that prefer the "dir" name. Pin
    the alias so a rename of data_location doesn't silently break
    it."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    p = _write(tmp_path, _MINIMAL.format(data_dir=data_dir))
    rmd = research_md.load(p)
    assert rmd.data_dir == rmd.data_location
    assert rmd.data_dir == data_dir.resolve()


# ---------------------------------------------------------------------------
# Smoke-test fixtures — validate every real RESEARCH.md on disk
# ---------------------------------------------------------------------------
#
# engine ships with real research cases under
# ../research_cases/<case>/RESEARCH.md. These are the smoke-test
# inputs the developer uses to drive end-to-end `omicairn run` smoke tests.
# A typo / parser regression in any of them is invisible until someone
# kicks off a real (slow, API-hitting) run. This test pins them at
# edit-time: every on-disk RESEARCH.md must load cleanly and expose
# a non-empty topic_slug + research_question.
#
# The test skips gracefully if the research_cases/ directory is
# missing — e.g. a CI sandbox that only has the engine repo
# checked out.

_RESEARCH_CASES_DIR = (
    pathlib.Path(__file__).resolve().parents[2] / "research_cases"
)


def _real_research_md_files() -> list[pathlib.Path]:
    if not _RESEARCH_CASES_DIR.is_dir():
        return []
    return sorted(
        p / "RESEARCH.md"
        for p in _RESEARCH_CASES_DIR.iterdir()
        if p.is_dir() and (p / "RESEARCH.md").is_file()
    )


@pytest.mark.skipif(
    not _real_research_md_files(),
    reason="research_cases/ directory not present in this checkout",
)
@pytest.mark.parametrize(
    "rmd_path",
    _real_research_md_files(),
    ids=lambda p: p.parent.name,
)
def test_shipped_research_md_files_load_cleanly(rmd_path):
    """Every RESEARCH.md in research_cases/ must parse without
    raising, and the loaded object must surface a non-empty
    topic_slug + research_question. This catches:

      - YAML frontmatter typos
      - Missing required fields after schema changes
      - Type-coercion regressions (e.g. bounds now expects int but
        a case still has "3" as a string — the _get coercion handles
        this, but if the contract changes we want to know)
      - Env-var interpolation breakage in data.location

    Edit-time failure here saves a much more expensive failure at
    `omicairn run` smoke-test time."""
    rmd = research_md.load(rmd_path)
    assert rmd.topic_slug, f"{rmd_path}: empty topic_slug"
    assert rmd.research_question.strip(), (
        f"{rmd_path}: empty research_question"
    )
    # data_location must resolve to a real path. It doesn't need to
    # exist on disk (the fixture's data/ may be git-ignored), but the
    # attribute must be a non-empty Path.
    assert isinstance(rmd.data_location, pathlib.Path)
    assert str(rmd.data_location), f"{rmd_path}: empty data.location"
