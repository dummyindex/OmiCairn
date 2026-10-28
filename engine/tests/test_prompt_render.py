"""Prompt render integration test.

This is the runtime-dual of test_prompt_lint.py. prompt_lint does a
static scan to confirm every {{token}} in a prompt file is declared
and populated. This file actually CALLS `orchestrator._render_prompt`
against a synthetic workspace and asserts the output contains no
unreplaced `{{...}}` placeholders.

Why both? Static analysis catches "the renderer's ctx dict doesn't
know about this token". This integration check catches the dual:
"the renderer's ctx dict DOES populate it but there's a bug in how
it's injected (e.g. the key has a typo, the value is None, or the
replace loop is buggy)". Either class of bug would silently corrupt
a rendered prompt, which only surfaces when a real `omicairn run` starts.
"""
from __future__ import annotations

import pathlib
import re
import tempfile
from typing import Iterator

import pytest

from omicairn import orchestrator, phases, stage as stage_mod, workspace
from omicairn.constants import PIPELINE_ORDER, PhaseName


_RESEARCH_MD = """\
---
research_mode: METHOD
domain: synthetic_smoke_test
topic_slug: pytest-render
research_question: |
  Does prompt rendering produce no unreplaced tokens?
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

# render fixture
"""


@pytest.fixture
def ws() -> Iterator[workspace.Workspace]:
    with tempfile.TemporaryDirectory() as td:
        tmpdir = pathlib.Path(td)
        data_dir = tmpdir / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "dummy.csv").write_text("x,y\n1,1\n")
        rmd_path = tmpdir / "RESEARCH.md"
        rmd_path.write_text(_RESEARCH_MD.format(data_dir=data_dir))
        ws_root = tmpdir / "workspaces"
        ws_root.mkdir()
        yield workspace.init(
            "pytest-render",
            research_md_path=rmd_path,
            workspaces_root=ws_root,
            skip_literature=True,
        )


# Matches any remaining `{{name}}` placeholder.
_UNREPLACED_PATTERN = re.compile(r"\{\{[a-z_][a-z0-9_]*\}\}")


def _synthetic_stage_ref(
    ws_root: pathlib.Path, phase_name: str, index: int
) -> stage_mod.StageRef:
    """Build a StageRef for a phase without actually creating the
    directory on disk. _render_prompt only reads the rel_path/index/
    phase attributes, so we don't need a real directory."""
    stage_name = f"{index:03d}_{phase_name}"
    return stage_mod.StageRef(
        index=index,
        phase=phase_name,
        path=ws_root / stage_name,
        rel_path=stage_name,
    )


@pytest.mark.parametrize(
    "phase",
    [phases.SCOPE, phases.PLAN, phases.EXECUTE, phases.REVIEW],
)
def test_render_prompt_leaves_no_unreplaced_tokens(ws, phase):
    """For every LLM phase, the rendered prompt must contain no
    surviving `{{token}}` markers. A surviving marker means either:
    - the prompt references a token the renderer doesn't populate
      (caught statically by test_prompt_lint; this is a runtime dual)
    - the renderer is buggy in a way the static scan missed
    - a new token was added in one place but not the other
    """
    stage_ref = _synthetic_stage_ref(ws.root, phase.name, index=0)
    rendered = orchestrator._render_prompt(
        phase=phase,
        ws=ws,
        stage_ref=stage_ref,
        iter_count=1,
        max_iter=3,
        retry_note="",
    )
    unreplaced = _UNREPLACED_PATTERN.findall(rendered)
    assert not unreplaced, (
        f"{phase.prompt_file} has unreplaced tokens after render: "
        f"{sorted(set(unreplaced))}. Either wire them in "
        f"orchestrator._render_prompt or remove them from the prompt."
    )


@pytest.mark.parametrize(
    "phase",
    [phases.SCOPE, phases.PLAN, phases.EXECUTE, phases.REVIEW],
)
def test_render_prompt_substitutes_core_tokens(ws, phase):
    """Spot-check that a few high-value tokens actually got substituted
    to their ctx values. Catches the case where the renderer runs to
    completion but silently skips a token (e.g. because of a typo in
    the Token class value vs the prompt-file placeholder)."""
    stage_ref = _synthetic_stage_ref(ws.root, phase.name, index=0)
    rendered = orchestrator._render_prompt(
        phase=phase,
        ws=ws,
        stage_ref=stage_ref,
        iter_count=2,
        max_iter=3,
        retry_note="",
    )
    # The topic slug must appear somewhere in every rendered prompt —
    # every LLM phase references {{topic_slug}}. (This is a soft
    # assumption; if a future prompt legitimately drops topic_slug
    # the assertion can relax to "research_question substituted".)
    assert "pytest-render" in rendered or "Does prompt rendering" in rendered


def test_render_prompt_substitutes_retry_note(ws):
    """When retry_note is provided, the rendered prompt must contain
    the literal retry note text somewhere. When it's empty, the
    placeholder '(fresh attempt)' should appear instead."""
    stage_ref = _synthetic_stage_ref(ws.root, PhaseName.EXECUTE, index=0)
    with_note = orchestrator._render_prompt(
        phase=phases.EXECUTE,
        ws=ws,
        stage_ref=stage_ref,
        iter_count=2,
        max_iter=3,
        retry_note="CANARY_RETRY_NOTE_ABC123",
    )
    assert "CANARY_RETRY_NOTE_ABC123" in with_note

    without_note = orchestrator._render_prompt(
        phase=phases.EXECUTE,
        ws=ws,
        stage_ref=stage_ref,
        iter_count=1,
        max_iter=3,
        retry_note="",
    )
    assert "(fresh attempt)" in without_note


def test_free_phase_prompt_carries_no_manuscript_scope_rule(ws):
    """Every FREE steering prompt gets the scope rule appended, so a user
    steering request cannot turn the FREE phase into manuscript drafting."""
    from omicairn import steering as steering_mod

    steering_mod.write_plan(ws.root, steering_mod.SteeringPlan(
        user_request="write the paper",
        steps=[steering_mod.SteeringStep(
            phase=PhaseName.FREE,
            prompt_inline="Draft a Nature-style abstract and cover letter.",
        )],
    ))
    stage_ref = _synthetic_stage_ref(ws.root, PhaseName.FREE, index=9)
    rendered = orchestrator._render_prompt(
        phase=phases.FREE, ws=ws, stage_ref=stage_ref,
        iter_count=1, max_iter=3, retry_note="",
    )
    assert orchestrator.FREE_PHASE_SCOPE_RULE.strip() in rendered
    assert "do NOT draft manuscripts" in rendered
