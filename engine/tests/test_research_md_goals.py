"""Tests for multi-goal RESEARCH.md parsing (core belief 12).

A workspace may declare several goals, each with seed hypotheses. When
no `goals:` block is present, a single goal is synthesized from
`research_question` so existing single-goal cases are byte-for-byte
unchanged in behavior. See docs/design-docs/dynamic-hypotheses.md § 3.
"""
from __future__ import annotations

import pathlib
import tempfile

import pytest

from omicairn import research_md


_BASE = """\
research_mode: METHOD
domain: synthetic
topic_slug: pytest-goals
research_question: |
  Does X predict Y?
data:
  location: {data_dir}
  description: pytest fixture
"""


def _write(tmp: pathlib.Path, body: str) -> pathlib.Path:
    data_dir = tmp / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "d.csv").write_text("x,y\n1,1\n")
    rmd = tmp / "RESEARCH.md"
    rmd.write_text("---\n" + _BASE.format(data_dir=data_dir) + body + "---\n")
    return rmd


def test_single_goal_synthesized_when_no_goals_block():
    with tempfile.TemporaryDirectory() as td:
        rmd = _write(pathlib.Path(td), body="")
        rm = research_md.load(rmd)
        assert len(rm.goals) == 1
        assert rm.is_multi_goal is False
        g = rm.goals[0]
        assert g.id == "g1"
        assert g.question.strip() == "Does X predict Y?"
        assert g.hypotheses == []
        # defaults for the dynamic-H knobs
        assert rm.allow_on_the_fly_hypotheses is True
        assert rm.max_on_the_fly_hypotheses == 5


def test_multi_goal_block_parsed():
    body = """\
goals:
  - id: g1
    question: "Does X predict Y in cohort A?"
    hypotheses:
      - "H1: X is prognostic for Y (HR>1.5)"
      - "H2: the X effect is independent of stage"
  - id: g2
    question: "Does the X signal transfer to cohort B?"
    hypotheses:
      - "H3: an A-trained model retains AUC>0.65 on B"
allow_on_the_fly_hypotheses: true
max_on_the_fly_hypotheses: 8
"""
    with tempfile.TemporaryDirectory() as td:
        rmd = _write(pathlib.Path(td), body=body)
        rm = research_md.load(rmd)
        assert rm.is_multi_goal is True
        assert [g.id for g in rm.goals] == ["g1", "g2"]
        assert len(rm.goals[0].hypotheses) == 2
        assert len(rm.goals[1].hypotheses) == 1
        assert len(rm.all_seed_hypotheses) == 3
        assert rm.max_on_the_fly_hypotheses == 8


def test_goal_ids_default_when_absent():
    body = """\
goals:
  - question: "first goal"
    hypotheses: ["Ha"]
  - question: "second goal"
    hypotheses: ["Hb"]
"""
    with tempfile.TemporaryDirectory() as td:
        rmd = _write(pathlib.Path(td), body=body)
        rm = research_md.load(rmd)
        assert [g.id for g in rm.goals] == ["g1", "g2"]


def test_first_goal_falls_back_to_research_question():
    body = """\
goals:
  - hypotheses: ["Ha"]
"""
    with tempfile.TemporaryDirectory() as td:
        rmd = _write(pathlib.Path(td), body=body)
        rm = research_md.load(rmd)
        assert rm.goals[0].question.strip() == "Does X predict Y?"


def test_non_first_goal_without_question_raises():
    body = """\
goals:
  - question: "first"
    hypotheses: ["Ha"]
  - hypotheses: ["Hb"]
"""
    with tempfile.TemporaryDirectory() as td:
        rmd = _write(pathlib.Path(td), body=body)
        with pytest.raises(ValueError, match="missing a `question:`"):
            research_md.load(rmd)


def test_goals_not_a_list_raises():
    body = "goals: not-a-list\n"
    with tempfile.TemporaryDirectory() as td:
        rmd = _write(pathlib.Path(td), body=body)
        with pytest.raises(ValueError, match="`goals:` must be a list"):
            research_md.load(rmd)


def test_goal_hypotheses_not_a_list_raises():
    body = """\
goals:
  - question: "g"
    hypotheses: "should be a list"
"""
    with tempfile.TemporaryDirectory() as td:
        rmd = _write(pathlib.Path(td), body=body)
        with pytest.raises(ValueError, match="hypotheses` must be a list"):
            research_md.load(rmd)


def test_on_the_fly_can_be_disabled():
    body = "allow_on_the_fly_hypotheses: false\n"
    with tempfile.TemporaryDirectory() as td:
        rmd = _write(pathlib.Path(td), body=body)
        rm = research_md.load(rmd)
        assert rm.allow_on_the_fly_hypotheses is False


# ---------------------------------------------------------------------------
# IDEATE source-collection hyperparameters
# ---------------------------------------------------------------------------

def test_ideate_config_defaults_when_absent():
    with tempfile.TemporaryDirectory() as td:
        rmd = _write(pathlib.Path(td), body="")
        rm = research_md.load(rmd)
        assert rm.ideate_config == {
            "target_papers": 8, "target_blogs": 3, "target_beliefs": 4,
            "min_directions": 3, "max_directions": 6,
        }


def test_ideate_config_overridable():
    body = (
        "ideate:\n"
        "  target_papers: 20\n"
        "  target_beliefs: 10\n"
        "  max_directions: 8\n"
    )
    with tempfile.TemporaryDirectory() as td:
        rmd = _write(pathlib.Path(td), body=body)
        rm = research_md.load(rmd)
        assert rm.ideate_config["target_papers"] == 20
        assert rm.ideate_config["target_beliefs"] == 10
        assert rm.ideate_config["max_directions"] == 8
        assert rm.ideate_config["target_blogs"] == 3      # default kept
        assert rm.ideate_config["min_directions"] == 3    # default kept


def test_ideate_config_non_integer_raises():
    body = "ideate:\n  target_papers: lots\n"
    with tempfile.TemporaryDirectory() as td:
        rmd = _write(pathlib.Path(td), body=body)
        with pytest.raises(ValueError, match="ideate.target_papers must be an integer"):
            research_md.load(rmd)


def test_ideate_config_min_gt_max_raises():
    body = "ideate:\n  min_directions: 9\n  max_directions: 4\n"
    with tempfile.TemporaryDirectory() as td:
        rmd = _write(pathlib.Path(td), body=body)
        with pytest.raises(ValueError, match="min_directions.*max_directions"):
            research_md.load(rmd)


def test_prefer_reputable_sources_default_and_override():
    with tempfile.TemporaryDirectory() as td:
        rm = research_md.load(_write(pathlib.Path(td), body=""))
        assert rm.prefer_reputable_sources is False          # opt-in bias, off by default
    with tempfile.TemporaryDirectory() as td:
        rm = research_md.load(_write(pathlib.Path(td),
                                     body="prefer_reputable_sources: true\n"))
        assert rm.prefer_reputable_sources is True


# ---------------------------------------------------------------------------
# Depth / rigor SOFT targets (advisory; default None = no constraint)
# ---------------------------------------------------------------------------

from omicairn.constants import DEPTH_CONFIG_KEYS  # noqa: E402


def test_depth_config_defaults_all_none_when_absent():
    """No `depth:`/`rigor:` block → every key parses to None (no constraint)."""
    with tempfile.TemporaryDirectory() as td:
        rm = research_md.load(_write(pathlib.Path(td), body=""))
        assert set(rm.depth_config) == set(DEPTH_CONFIG_KEYS)
        assert all(v is None for v in rm.depth_config.values())


def test_depth_config_block_parsed():
    body = """\
depth:
  min_datasets: 3
  min_independent_analyses: 4
  require_cross_cohort: true
  min_figures: 6
"""
    with tempfile.TemporaryDirectory() as td:
        rm = research_md.load(_write(pathlib.Path(td), body=body))
        assert rm.depth_config["min_datasets"] == 3
        assert rm.depth_config["min_independent_analyses"] == 4
        assert rm.depth_config["require_cross_cohort"] is True
        assert rm.depth_config["min_figures"] == 6


def test_depth_config_partial_leaves_rest_none():
    body = "depth:\n  min_datasets: 2\n"
    with tempfile.TemporaryDirectory() as td:
        rm = research_md.load(_write(pathlib.Path(td), body=body))
        assert rm.depth_config["min_datasets"] == 2
        assert rm.depth_config["min_independent_analyses"] is None
        assert rm.depth_config["require_cross_cohort"] is None
        assert rm.depth_config["min_figures"] is None


def test_depth_config_rigor_alias_accepted():
    """The `rigor:` block is an accepted alias for `depth:`."""
    body = "rigor:\n  min_figures: 8\n  require_cross_cohort: false\n"
    with tempfile.TemporaryDirectory() as td:
        rm = research_md.load(_write(pathlib.Path(td), body=body))
        assert rm.depth_config["min_figures"] == 8
        assert rm.depth_config["require_cross_cohort"] is False


def test_depth_config_non_integer_raises():
    body = "depth:\n  min_datasets: many\n"
    with tempfile.TemporaryDirectory() as td:
        rmd = _write(pathlib.Path(td), body=body)
        with pytest.raises(ValueError, match="min_datasets must be an integer"):
            research_md.load(rmd)


# ---------------------------------------------------------------------------
# contribution_type knob (analysis | method | theory; default analysis)
# ---------------------------------------------------------------------------

def test_contribution_type_defaults_to_analysis_when_absent():
    """No `contribution_type:` → default 'analysis' (back-compat: no
    existing case declares the knob)."""
    with tempfile.TemporaryDirectory() as td:
        rm = research_md.load(_write(pathlib.Path(td), body=""))
        assert rm.contribution_type == "analysis"


@pytest.mark.parametrize("ctype", ["analysis", "method", "theory"])
def test_contribution_type_each_valid_value_parsed(ctype):
    body = f"contribution_type: {ctype}\n"
    with tempfile.TemporaryDirectory() as td:
        rm = research_md.load(_write(pathlib.Path(td), body=body))
        assert rm.contribution_type == ctype


def test_contribution_type_case_insensitive():
    body = "contribution_type: METHOD\n"
    with tempfile.TemporaryDirectory() as td:
        rm = research_md.load(_write(pathlib.Path(td), body=body))
        assert rm.contribution_type == "method"


def test_contribution_type_unknown_raises_loudly():
    """A typo'd knob is a config bug, not a silent fallback (mirrors the
    compute_resources unknown-id loud failure)."""
    body = "contribution_type: methdo\n"
    with tempfile.TemporaryDirectory() as td:
        rmd = _write(pathlib.Path(td), body=body)
        with pytest.raises(ValueError, match="contribution_type must be one of"):
            research_md.load(rmd)
