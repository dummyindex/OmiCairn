"""Prompt lint: catch typos and drift in prompt token names.

Every `{{token}}` placeholder in `prompts/*.md` gets replaced by the
orchestrator's `_render_prompt` from a hardcoded ctx dict. If a prompt
references a token that isn't in ctx, the literal `{{...}}` passes
through to claude silently and corrupts the rendered prompt. These
tests catch that at edit time:

1. Every prompt-file token must be a member of the `Token` class OR a
   `latest_<phase>_dir` token for a known phase.
2. Every prompt-file token must appear in the orchestrator's render
   ctx (static scan of _render_prompt).
3. Every Token class member must be referenced by at least one prompt
   (or declared exempt). A token defined but never used is drift.
4. Contract files named in the prompt ("Write `{{stage_dir}}/foo.md`")
   must overlap with `phase.required_outputs` — no silent drift where
   the prompt says to write goal.md but phases.py requires goals.md.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from omicairn import phases
from omicairn.constants import (
    Decision,
    PIPELINE_ORDER,
    Token,
    latest_stage_token,
)


PROMPTS_DIR = pathlib.Path(__file__).resolve().parents[1] / "prompts"
ORCHESTRATOR_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "omicairn" / "orchestrator.py"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TOKEN_PATTERN = re.compile(r"\{\{([a-z_][a-z0-9_]*)\}\}")


def _prompts() -> list[pathlib.Path]:
    """Phase-prompt files only.

    `prompts/` may also hold non-phase prompt templates that are
    rendered through bespoke runners (e.g. `steering_planner.md` is
    rendered by `orchestrator.run_steering_planner` with its own
    token set + tool subset, not by the standard `_render_prompt`).
    These are excluded from the phase-lint contract.
    """
    return sorted(
        p for p in PROMPTS_DIR.glob("*.md")
        if p.name not in _NON_PHASE_PROMPT_FILES
    )


# Prompts that live in prompts/ but are NOT bound to a Phase entry.
# Each must declare its own renderer (e.g. run_steering_planner) that
# explicitly handles its tokens.
_NON_PHASE_PROMPT_FILES: frozenset[str] = frozenset({
    "steering_planner.md",
    # Standalone CLI prompt for `omicairn project scan`, rendered by
    # cli.cmd_project with its own {{scan_target}}/{{scan_out}} tokens
    # (a bespoke runner, not a pipeline phase).
    "project_scan.md",
})


def _tokens_in_file(path: pathlib.Path) -> set[str]:
    return set(_TOKEN_PATTERN.findall(path.read_text()))


def _known_token_values() -> set[str]:
    """All string values declared on the Token class."""
    out: set[str] = set()
    for name in dir(Token):
        if name.startswith("_"):
            continue
        val = getattr(Token, name)
        if isinstance(val, str):
            out.add(val)
    return out


def _all_known_tokens() -> set[str]:
    """Token.* values + the per-phase latest_<phase>_dir tokens."""
    return _known_token_values() | {
        latest_stage_token(p) for p in PIPELINE_ORDER
    }


# ---------------------------------------------------------------------------
# Lint: every prompt token is declared
# ---------------------------------------------------------------------------

def test_every_prompt_token_is_declared():
    """No prompt may reference a token that's not in the Token class
    or a latest_<phase>_dir token. Catches typos like {{stage_directory}}
    or {{topic-slug}} at edit time."""
    known = _all_known_tokens()
    drift: list[str] = []
    for prompt_path in _prompts():
        used = _tokens_in_file(prompt_path)
        unknown = used - known
        for tok in sorted(unknown):
            drift.append(f"{prompt_path.name}: unknown token {{{{{tok}}}}}")
    assert not drift, (
        "Prompt references undeclared tokens. Either add them to the "
        "Token class in omicairn/constants.py and wire them in "
        "orchestrator._render_prompt, or fix the typo:\n  "
        + "\n  ".join(drift)
    )


# ---------------------------------------------------------------------------
# Lint: every prompt token is populated by the renderer
# ---------------------------------------------------------------------------

def _orchestrator_ctx_tokens() -> set[str]:
    """Static scan of omicairn/orchestrator.py for Token.X references inside
    _render_prompt. Returns the set of token *values* that the renderer
    actually populates.
    """
    src = ORCHESTRATOR_PATH.read_text()
    # Find the _render_prompt function body.
    m = re.search(
        r"def _render_prompt\(.*?\n(.*?)(?=\n\ndef |\nclass )",
        src,
        re.DOTALL,
    )
    assert m is not None, "could not locate _render_prompt in orchestrator.py"
    body = m.group(1)

    # Resolve each Token.ATTR reference in the body to its declared value.
    tokens: set[str] = set()
    for attr in re.findall(r"Token\.([A-Z_][A-Z0-9_]*)", body):
        val = getattr(Token, attr, None)
        if isinstance(val, str):
            tokens.add(val)
    # The renderer also injects latest_dir_tokens() results for every phase.
    if "latest_dir_tokens" in body:
        for p in PIPELINE_ORDER:
            tokens.add(latest_stage_token(p))
    return tokens


def test_every_prompt_token_is_populated_by_renderer():
    """For every {{token}} used in a prompt, confirm that
    orchestrator._render_prompt populates it. This is the dual of
    test_every_prompt_token_is_declared — the Token class can have an
    entry that the renderer forgot to wire up."""
    populated = _orchestrator_ctx_tokens()
    missing: list[str] = []
    for prompt_path in _prompts():
        for tok in sorted(_tokens_in_file(prompt_path)):
            if tok not in populated:
                missing.append(f"{prompt_path.name} references {{{{{tok}}}}}")
    assert not missing, (
        "Prompt references tokens the renderer does not populate. Either "
        "wire them in orchestrator._render_prompt, or remove them from "
        "the prompt:\n  " + "\n  ".join(missing)
    )


# ---------------------------------------------------------------------------
# Lint: every declared token is referenced by at least one prompt
# ---------------------------------------------------------------------------

# Tokens that are reserved for future use or populated for reasons
# other than prompt substitution. Update this set if a new token is
# intentionally added without a prompt consumer.
_UNUSED_TOKENS_ALLOWLIST: frozenset[str] = frozenset({
    # latest_cross_check_dir has no LLM phase that reads from cross_check
    # (cross_check is pure-code), but we declare it for completeness.
    "latest_cross_check_dir",
    # phase_name: available for any future prompt but not currently
    # referenced. Kept because the renderer passes it unconditionally.
    "phase_name",
})


def test_every_declared_token_is_used_somewhere():
    """Orphan tokens declared but never used in any prompt are drift.
    Allow a small allowlist of tokens we keep for future use."""
    known = _all_known_tokens()
    used_anywhere: set[str] = set()
    for prompt_path in _prompts():
        used_anywhere |= _tokens_in_file(prompt_path)
    unused = known - used_anywhere - _UNUSED_TOKENS_ALLOWLIST
    assert not unused, (
        f"Tokens declared in Token class but never referenced in any "
        f"prompt: {sorted(unused)}. Either use them, drop them from the "
        f"Token class, or add them to _UNUSED_TOKENS_ALLOWLIST with a "
        f"comment explaining why."
    )


# ---------------------------------------------------------------------------
# Lint: contract-file names in prompts overlap with phase.required_outputs
# ---------------------------------------------------------------------------

# Map prompt filename stem to the Phase object whose contract it describes.
_PROMPT_TO_PHASE = {
    "scope.md": phases.SCOPE,
    "plan.md": phases.PLAN,
    "execute.md": phases.EXECUTE,
    "review.md": phases.REVIEW,
}

_STAGE_FILE_PATTERN = re.compile(
    r"\{\{stage_dir\}\}/([A-Za-z0-9_./-]+(?:\.md|\.json|\.flag))"
)


@pytest.mark.parametrize("prompt_name,phase", sorted(_PROMPT_TO_PHASE.items()))
def test_prompt_names_all_required_contract_files(prompt_name, phase):
    """Every `required_outputs` file for a phase must be mentioned in
    its prompt. Drift here means the LLM won't know a contract file
    exists until contracts.verify rejects the stage."""
    prompt_path = PROMPTS_DIR / prompt_name
    body = prompt_path.read_text()

    for required_rel in phase.required_outputs:
        # Match either the bare filename or the {{stage_dir}}/path form.
        bare = required_rel.rsplit("/", 1)[-1]
        if bare in body or required_rel in body:
            continue
        raise AssertionError(
            f"{prompt_name} does not mention required contract file "
            f"{required_rel!r} (phase {phase.name}). Update the prompt "
            f"to tell the LLM to write it, or remove the file from "
            f"phase.required_outputs."
        )


@pytest.mark.parametrize("prompt_name,phase", sorted(_PROMPT_TO_PHASE.items()))
def test_every_prompt_stage_dir_write_is_declared_in_phase_outputs(
    prompt_name, phase
):
    """The dual of the previous test: every `{{stage_dir}}/path` the
    prompt tells the LLM to write must map to a
    phase.required_outputs or phase.optional_outputs entry. Otherwise
    the agent produces a stage-dir artifact that (a) the orchestrator
    won't check for and (b) persists into the next stage's
    prior_stages listing with no schema.

    This catches the drift direction the forward test can't: prompt
    tells the agent to write `{{stage_dir}}/preregistartion.json`
    (typo) — the required_outputs forward test still passes because
    `preregistration.json` from phase.required_outputs remains
    mentioned elsewhere in the prompt, but the agent writes the typo
    file and the contract rejects the stage."""
    prompt_path = PROMPTS_DIR / prompt_name
    body = prompt_path.read_text()
    referenced = set(_STAGE_FILE_PATTERN.findall(body))
    if not referenced:
        # Every LLM prompt should mention at least one stage-dir write
        # (done.flag if nothing else), so an empty set is itself a red
        # flag — but the `test_llm_prompt_references_stage_dir_token`
        # test already guards against the prompt omitting stage_dir.
        return

    known_full = set(phase.required_outputs) | set(phase.optional_outputs)
    known_bare = {p.rsplit("/", 1)[-1] for p in known_full}
    unknown = sorted(
        ref for ref in referenced
        if ref not in known_full and ref.rsplit("/", 1)[-1] not in known_bare
    )
    assert not unknown, (
        f"{prompt_name} tells the agent to write stage-dir files that "
        f"are not in phase.required_outputs or phase.optional_outputs "
        f"for {phase.name}: {unknown}. Either add them to the Phase "
        f"contract in omicairn/phases.py or remove the {{{{stage_dir}}}}/ "
        f"reference from the prompt. (Files written to stage_dir that "
        f"contracts.verify does not check for are silent drift: the "
        f"orchestrator ignores them, they leak into the prior_stages "
        f"listing, and a typo in the prompt ships as a real file.)"
    )


# ---------------------------------------------------------------------------
# Lint: every phase.prompt_file resolves to a real file in prompts/
# ---------------------------------------------------------------------------
#
# orchestrator._render_prompt calls `(PROMPTS_DIR / phase.prompt_file).read_text()`
# unconditionally for every LLM phase. If someone renames prompts/scope.md to
# prompts/scoping.md but forgets to update phases.py, the rename survives the
# import and every unit test until the FIRST real `omicairn run` invocation — at
# which point the stage crashes deep inside render. These two tests turn that
# latent crash into an edit-time failure.

@pytest.mark.parametrize("phase", [p for p in phases.ORDER if p.is_claude_code])
def test_every_llm_phase_prompt_file_exists_on_disk(phase):
    """Each LLM phase's `prompt_file` must resolve to an actual file in
    prompts/. If someone renames or deletes a prompt without updating
    phases.py, this test fails at edit time — not during a real run."""
    assert phase.prompt_file, (
        f"{phase.name} is marked is_claude_code but has empty prompt_file"
    )
    target = PROMPTS_DIR / phase.prompt_file
    assert target.is_file(), (
        f"{phase.name}.prompt_file={phase.prompt_file!r} does not exist "
        f"at {target}. Either rename the prompt back or update "
        f"phases.py to point at the new filename."
    )


def test_no_orphan_prompt_files_in_prompts_dir():
    """Every *.md in prompts/ must be owned by exactly one LLM phase.
    An orphan file is drift: it was either (a) left behind after a
    rename, (b) added without a corresponding phase wire-up, or (c) a
    stale draft that should live somewhere else. Any of those should
    fail loud rather than rot in the directory."""
    on_disk = {p.name for p in _prompts()}
    # Scan ALL registered phases (BY_NAME), not just the linear ORDER —
    # standalone claude-code phases like IDEATE own a prompt file too.
    # FREE is excluded naturally (empty prompt_file from inline steering).
    owned_by_phase = {
        p.prompt_file for p in phases.BY_NAME.values()
        if p.is_claude_code and p.prompt_file
    }
    orphans = on_disk - owned_by_phase
    assert not orphans, (
        f"prompts/ contains files not referenced by any Claude Code phase: "
        f"{sorted(orphans)}. Either wire them into a phase via "
        f"phases.py or move them out of prompts/."
    )


# ---------------------------------------------------------------------------
# Semantic invariants: every LLM phase prompt must carry the core
# scaffolding instructions. These are the "never drift" rules that the
# orchestrator assumes each prompt enforces — if one gets edited away,
# the pipeline still runs but with a silently-weaker agent.
# ---------------------------------------------------------------------------
#
# Why this is a lint and not a prompt-engineering judgment call:
#
# 1. `done.flag` LAST  — the orchestrator polls for done.flag to
#    decide when a stage finished. If a prompt forgets to tell the
#    agent to write it LAST, the agent may write it first and then
#    crash mid-stage, leaving a "finished" stage that is actually
#    incomplete.
#
# 2. `{{stage_dir}}` token — every LLM prompt must mention the
#    stage directory token at least once so the agent knows WHERE
#    to write its outputs. Without it, the agent might write to the
#    workspace root, corrupting the immutable-stages contract.
#
# 3. Anti-fabrication guard — either "fabricat" or "simulat"; every
#    LLM prompt must explicitly forbid one of them. This is the
#    guardrail that keeps phase agents from inventing numbers or
#    data. Losing it is the single highest-severity silent
#    regression the pipeline can have.

_LLM_PROMPTS = [
    PROMPTS_DIR / p.prompt_file
    for p in phases.ORDER
    if p.is_claude_code
]


@pytest.mark.parametrize(
    "prompt_path",
    _LLM_PROMPTS,
    ids=lambda p: p.name,
)
def test_llm_prompt_has_done_flag_last_instruction(prompt_path):
    """Every LLM phase prompt must tell the agent to write `done.flag`
    LAST. The orchestrator treats the existence of `done.flag` as the
    signal that a stage finished — writing it early means a crash
    mid-stage leaves a prematurely-"finished" stage on disk.

    We require both the filename and the word "LAST" to appear in
    the prompt (case-insensitive for LAST)."""
    text = prompt_path.read_text()
    assert "done.flag" in text, (
        f"{prompt_path.name} does not mention `done.flag` anywhere. "
        f"Every LLM phase prompt must explicitly tell the agent to "
        f"write it; otherwise the orchestrator never sees the stage "
        f"finish."
    )
    assert "LAST" in text or "last" in text, (
        f"{prompt_path.name} mentions `done.flag` but does not say to "
        f"write it LAST. Agents that write the flag first can crash "
        f"mid-stage and leave a stage that looks done but isn't."
    )


@pytest.mark.parametrize(
    "prompt_path",
    _LLM_PROMPTS,
    ids=lambda p: p.name,
)
def test_llm_prompt_references_stage_dir_token(prompt_path):
    """Every LLM prompt must reference the `{{stage_dir}}` token at
    least once. Without it, the agent has no pointer to its
    assigned output directory and may write to the workspace root,
    violating the immutable-stages contract."""
    text = prompt_path.read_text()
    assert "{{stage_dir}}" in text, (
        f"{prompt_path.name} does not reference the {{{{stage_dir}}}} "
        f"token. Every LLM prompt must tell the agent where to write "
        f"its output files."
    )


@pytest.mark.parametrize(
    "prompt_path",
    _LLM_PROMPTS,
    ids=lambda p: p.name,
)
def test_llm_prompt_has_anti_fabrication_guard(prompt_path):
    """Every LLM prompt must explicitly forbid fabrication or
    simulation. This is the single highest-severity rule in the
    pipeline and the prompt is the only place it lives for LLM
    phases. Losing it from even one prompt is catastrophic — a
    write-phase agent that forgets the rule will invent citations;
    an execute-phase agent will invent numbers."""
    text = prompt_path.read_text().lower()
    has_fabricate = "fabricat" in text  # fabricate / fabrication
    has_simulate = "simulat" in text     # simulate / simulation
    assert has_fabricate or has_simulate, (
        f"{prompt_path.name} contains neither 'fabricat*' nor "
        f"'simulat*'. Every LLM phase prompt must explicitly forbid "
        f"one of these — this is the anti-fabrication guardrail and "
        f"it is not optional."
    )


# ---------------------------------------------------------------------------
# Every LLM prompt must carry the cross-phase context tokens
# ---------------------------------------------------------------------------
#
# {{knowledge_summary}} is how prior-stage lessons (from knowledge/lessons.jsonl)
# reach the next phase agent — omicairn.stage.summarize_lessons filters lessons by
# phase and the orchestrator substitutes the result into this token for EVERY
# phase. If a prompt drops the token, that phase's agent silently runs without
# access to prior lessons and repeats failures the pipeline already knows about.
#
# {{domain_pack_guidance}} is the per-phase specialization injected from
# domain_packs/<domain>/phase_<name>.md. Dropping it from a prompt means the
# phase agent runs with only the generic guidance and loses the domain pack's
# reason to exist for that phase.
#
# The existing lint (`test_every_declared_token_is_used_somewhere`) only
# checks that the token is used in AT LEAST ONE prompt. The drift this catches
# is the narrower one: token silently disappearing from a specific prompt
# while still being referenced elsewhere in prompts/.

_UNIVERSAL_LLM_PROMPT_TOKENS: tuple[str, ...] = (
    "knowledge_summary",
    "domain_pack_guidance",
)


@pytest.mark.parametrize(
    "prompt_path",
    _LLM_PROMPTS,
    ids=lambda p: p.name,
)
@pytest.mark.parametrize("token_name", _UNIVERSAL_LLM_PROMPT_TOKENS)
def test_every_llm_prompt_references_universal_tokens(prompt_path, token_name):
    """Every LLM phase prompt must reference {{knowledge_summary}} and
    {{domain_pack_guidance}}. These are the two tokens the orchestrator
    substitutes FOR EVERY LLM PHASE unconditionally — if a prompt drops
    either one, the phase silently loses cross-phase learning or domain
    specialization, but no existing lint catches it because both tokens
    still appear in OTHER prompts (keeping the
    declared-token-used-somewhere check green)."""
    text = prompt_path.read_text()
    placeholder = "{{" + token_name + "}}"
    assert placeholder in text, (
        f"{prompt_path.name} does not reference {placeholder}. Every "
        f"LLM phase prompt must substitute this token — the "
        f"orchestrator populates it unconditionally for every phase, "
        f"and dropping it silently disables cross-phase "
        f"{'learning' if token_name == 'knowledge_summary' else 'domain specialization'} "
        f"for this phase."
    )


# ---------------------------------------------------------------------------
# Verdict decision drift: review.md lists a set of valid decisions.
# Every one of them must be a real Decision class value.
# ---------------------------------------------------------------------------
#
# review.md is the only LLM prompt that emits a verdict.json, and it
# teaches the reviewer agent the legal set of decision strings in two
# places:
#
#   1. A JSON schema line of the form
#      `"decision": "<one of: done | retry_execute | ... | halt>"`
#   2. A "Decision rules" section where each decision is wrapped in
#      backticks: `done`, `retry_execute`, `back_to_plan`,
#      `back_to_scope`, `halt`.
#
# If someone:
#   - adds a new Decision to constants.py but doesn't update review.md,
#     the reviewer agent will never emit it (latent dead code);
#   - types a typo like `back_to_plann` or `retry_executee` in review.md,
#     the reviewer agent emits that string and the orchestrator rejects
#     it as an unknown decision, which manifests as a stage-level failure
#     deep into a real run rather than an edit-time lint error.
#
# The two tests below turn both into edit-time failures.

_REVIEW_PROMPT = PROMPTS_DIR / "review.md"

# Matches structured decision names — `back_to_<word>` and `retry_<word>`
# — anywhere in the file. These are unambiguous: no natural-language
# prose uses tokens like `back_to_plan`. The bare words `done`, `halt`,
# and `proceed` are excluded here because they appear in sentences like
# "the pipeline halts successfully" and would produce false positives.
_STRUCTURED_DECISION_PATTERN = re.compile(
    r"\b(?:retry_[a-z_]+|back_to_[a-z_]+)\b"
)


def _decision_values() -> set[str]:
    out: set[str] = set()
    for name in dir(Decision):
        if name.startswith("_"):
            continue
        v = getattr(Decision, name)
        if isinstance(v, str):
            out.add(v)
    return out


def test_review_prompt_schema_line_decisions_are_all_valid():
    """The schema line in review.md
    (`"decision": "<one of: done | retry_execute | ... | halt>"`)
    lists the legal decisions the reviewer agent may emit. Every pipe-
    separated token in that list must be a real Decision class value.

    A typo here ships to the reviewer agent as-is; the agent emits the
    typo; the orchestrator rejects it as an unknown decision and the
    run fails deep in real execution."""
    text = _REVIEW_PROMPT.read_text()
    m = re.search(r'"decision":\s*"<one of:\s*([^">]+)>"', text)
    assert m is not None, (
        "review.md no longer has a "
        '`"decision": "<one of: ... >"` schema line. Either restore '
        "it or update this lint to track the new schema format."
    )
    listed = [tok.strip() for tok in m.group(1).split("|") if tok.strip()]
    assert listed, "review.md schema line has no decisions between the pipes"

    valid = _decision_values()
    bad = [d for d in listed if d not in valid]
    assert not bad, (
        f"review.md schema line lists decision string(s) that are not in "
        f"the Decision class: {bad}. Valid Decision values: "
        f"{sorted(valid)}. Either fix the typo or add the new decision "
        f"to omicairn/constants.py Decision class."
    )


def test_review_prompt_structured_decisions_are_all_valid():
    """Every `retry_*` and `back_to_*` substring anywhere in review.md
    must be a real Decision class value. Catches typos outside the
    schema line (e.g. in the 'Decision rules' section where each
    decision is wrapped in backticks).

    We first strip `{{...}}` render-time token blocks so that tokens
    like `{{retry_note}}` (a Token class member) don't get scanned as
    a decision name."""
    text = _REVIEW_PROMPT.read_text()
    text_no_tokens = _TOKEN_PATTERN.sub("", text)
    found = set(_STRUCTURED_DECISION_PATTERN.findall(text_no_tokens))
    valid = _decision_values()
    bad = sorted(d for d in found if d not in valid)
    assert not bad, (
        f"review.md mentions structured decision name(s) that are not "
        f"in the Decision class: {bad}. Valid Decision values: "
        f"{sorted(valid)}. Either fix the typo or add the new decision "
        f"to omicairn/constants.py Decision class."
    )


# ---------------------------------------------------------------------------
# Paper-writing removal: no prompt may instruct manuscript drafting.
# ---------------------------------------------------------------------------

def test_removed_paper_prompts_are_gone():
    for name in ("write.md", "claim_audit.md", "peer_review.md"):
        assert not (PROMPTS_DIR / name).exists(), name


def test_review_prompt_is_analysis_review():
    text = (PROMPTS_DIR / "review.md").read_text()
    for out in phases.REVIEW.required_outputs:
        assert out in text, f"review.md must name required output {out}"
    lower = text.lower()
    for banned in ("paper_final", "cover_letter", "draft.md",
                   "target_venue", "{{venue"):
        assert banned not in lower, f"review.md still mentions {banned!r}"
