"""Phase registry.

A Phase is: name, prompt template filename, required output files,
optional output files, allowed tools, timeout, LLM-driven flag.

All string constants are imported from `constants.py` — do not type a
phase name here as a literal.
"""
from __future__ import annotations

import dataclasses
from typing import Sequence

from .constants import (
    ANALYSIS_MD,
    ARM_MANIFEST_FILE,
    CLAUDE_CODE_PHASES,
    CODE_MAIN_PY,
    COUNTER_CRITIQUE_MD,
    DATA_FETCH_LOG,
    DONE_FLAG,
    FEASIBILITY_FILE,
    NEGATIVE_RESULTS_MD,
    FIGURES_CONTRACT_JSON,
    FINDINGS_FILE,
    IDEATE_DIRECTIONS_FILE,
    IDEATE_SOURCES_FILE,
    KG_UPDATES_FILE,
    PARALLEL_ARMS_STAMP_FILE,
    PIPELINE_ORDER,
    PREREGISTRATION_FILE,
    PROOF_OBLIGATIONS_FILE,
    PhaseName,
    RESOURCE_REQUEST_MD,
    RESULTS_JSON,
    REVIEW_NOTES_MD,
    SUMMARY_FILE,
    VERDICT_FILE,
)


@dataclasses.dataclass(frozen=True)
class Phase:
    name: str
    prompt_file: str  # empty string when phase runs in-process (cross_check)
    required_outputs: tuple[str, ...]  # filenames relative to the stage dir
    optional_outputs: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    timeout_seconds: int
    description: str

    @property
    def is_claude_code(self) -> bool:
        """True if this phase dispatches a `claude -p` subprocess.

        The one exception is `cross_check`, which runs in pure Python
        in the orchestrator process with no agent session at all.
        """
        return self.name in CLAUDE_CODE_PHASES

    @property
    def prompt_path(self) -> str:
        return self.prompt_file


# Tool allowlists are expressed the way `claude --allowedTools` expects:
# one entry per tool. We grant the agent the full Claude Code toolset
# (file ops, unrestricted Bash, web access, Task spawning). Destructive
# commands (`rm -rf`, writes to RESEARCH.md / CLAUDE.md / STATE.md) are
# blocked by the `deny` list in templates/settings.json, not by pruning
# this allow list. Narrower per-command sandboxing caused the agent to
# silently defer legitimate work (e.g. `pip install scanpy`) when the
# only "safe" route out was a placeholder figure.
_DEFAULT_TOOLS: tuple[str, ...] = (
    "Read",
    "Write",
    "Edit",
    "Glob",
    "Grep",
    "Bash",
    "WebFetch",
    "WebSearch",
    "TodoWrite",
    "Task",
)

# Subset filtered out when RESEARCH.md sets `allow_web: false`. The tools
# themselves are also present in `_DEFAULT_TOOLS`; this tuple is the
# canonical list of "what counts as web access" used by `tools_with_web`.
_WEB_TOOLS: tuple[str, ...] = ("WebFetch", "WebSearch")


# Required stage-directory artifacts that every LLM phase must produce.
# These are files inside the stage directory (e.g. 003_execute/summary.md).
_COMMON_STAGE_OUTPUTS: tuple[str, ...] = (SUMMARY_FILE, DONE_FLAG)


SCOPE = Phase(
    name=PhaseName.SCOPE,
    prompt_file="scope.md",
    required_outputs=("goal.md", "data_profile.md", *_COMMON_STAGE_OUTPUTS),
    # feasibility.json — the SCOPE-time statistical-power / data-feasibility
    # gate (JSON-only contract). OPTIONAL: emitted when the feasibility
    # gate is active for the case (confirmatory/replication runs by
    # default); absent for exploratory / gate-disabled cases so legacy
    # SCOPE stages contract-pass unchanged. The orchestrator validates +
    # routes on it after SCOPE completes. See omicairn.feasibility,
    # prompts/scope.md, docs/design-docs/feasibility-gate.md.
    optional_outputs=(FEASIBILITY_FILE,),
    allowed_tools=_DEFAULT_TOOLS,
    timeout_seconds=45 * 60,
    description="Understand the research question and profile the data.",
)

PLAN = Phase(
    name=PhaseName.PLAN,
    prompt_file="plan.md",
    required_outputs=(
        "literature.md",
        "hypotheses.md",
        "experiment_plan.md",
        FIGURES_CONTRACT_JSON,
        PREREGISTRATION_FILE,
        *_COMMON_STAGE_OUTPUTS,
    ),
    optional_outputs=(),
    allowed_tools=_DEFAULT_TOOLS,
    timeout_seconds=45 * 60,
    description=(
        "Light literature review, hypothesis generation, experiment "
        "design, pre-registration."
    ),
)

EXECUTE = Phase(
    name=PhaseName.EXECUTE,
    prompt_file="execute.md",
    required_outputs=(
        RESULTS_JSON,
        ANALYSIS_MD,
        "figures/legends.md",
        CODE_MAIN_PY,
        *_COMMON_STAGE_OUTPUTS,
    ),
    # locked_model.json only required when RESEARCH.md declares a
    # pre-registered held-out partition; cross_check enforces that case.
    optional_outputs=(
        "locked_model.json",
        # Written only when EXECUTE pulls inputs from public URLs into
        # the shared dataset cache; cross_check._check_data_fetch reads
        # it to surface failed fetches as informational warnings.
        DATA_FETCH_LOG,
        # Tier 1.3 environment lockfile — best-effort capture of the
        # active Python env, audited by cross_check._check_env_lockfile_
        # coverage. Optional today so legacy/minimal fixtures still
        # contract-pass; promote to required once env_capture is
        # universally adopted.
        "code/environment.lock.json",
        # Tier 3.2 — code-reviewer subagent output. Written when
        # EXECUTE dispatches the code-reviewer for an independent
        # correctness audit of code/*.py.
        "code_review.md",
        # Knowledge-graph update payload (core belief 12). When EXECUTE
        # surfaces signal beyond the primary hypothesis — secondary
        # result_refs, findings, on-the-fly hypotheses — it records them
        # here and the orchestrator folds them into the workspace graph
        # via knowledge_graph.ingest_graph_updates. Optional + additive:
        # never contract-enforced, absent for strict single-hypothesis
        # confirmations. See prompts/execute.md step 8b.
        KG_UPDATES_FILE,
        # Resource request (analysis-rigor rule). Written ONLY when the
        # honest full-data analysis cannot finish within the wall/CPU
        # budget (or needs a GPU, unavailable locally): EXECUTE files
        # this instead of silently subsampling the final/benchmark
        # analysis to fit the clock. Never contract-required.
        RESOURCE_REQUEST_MD,
        # Durable detached-job marker. Written the instant EXECUTE launches
        # a detached/background GPU job on a remote/shared host, so a
        # recovered/retried turn HARVESTS the in-flight job instead of
        # relaunching it (orchestrator._job_marker_note surfaces it in the
        # retry note). Absent for local/foreground analyses. Never
        # contract-required. See prompts/execute.md (round-trip block).
        "job_marker.json",
        # Proof-obligation ledger (THEORY cases). One entry per math claim:
        # its proof_status (proven_lean | derived_validated_proof_pending |
        # conjecture), the attempted .lean file, and the empirical-validation
        # pointer. EXECUTE emits it; REVIEW reads it to keep a deferred
        # (proof-pending) claim from being reported as proven.
        # Optional + additive — absent for non-theory / legacy runs. See prompts/execute.md THEORY directive + ProofStatus.
        PROOF_OBLIGATIONS_FILE,
        # Within-EXECUTE parallel-arm fan-out artifacts (W32). Present ONLY
        # when the stage fanned out an embarrassingly-parallel workload (a
        # cross-model-transfer matrix, a model x dataset table, or multi-seed
        # replicates) into independent subagent arms. arm_manifest.json is the
        # agent-authored fan-out plan (the pre-dispatch CLI gate input);
        # parallel_arms.json is the recorded verdict stamp. NEVER
        # contract-required (absent for every sequential run); cross_check W32
        # reads them to re-validate the disjoint-writes / distinct-seeds /
        # no-concurrent-GPU / routing contract on-disk. (The per-arm evidence
        # fragments arms/<arm_id>/arm_provenance.json are deliberately NOT
        # declared here: each is written by ONE subagent into its OWN disjoint
        # arm dir — race-free, and not a top-level stage contract artifact.)
        # See prompts/execute.md § 2b + omicairn.parallel_arms.
        ARM_MANIFEST_FILE,
        PARALLEL_ARMS_STAMP_FILE,
    ),
    allowed_tools=_DEFAULT_TOOLS,
    timeout_seconds=60 * 60,
    description=(
        "Write code, run on real data, iterate, honestly validate."
    ),
)

# cross_check is a synthetic phase — the orchestrator runs it in pure
# Python (no LLM call). It reads the most recent execute stage and
# emits verdict.json + findings.md, then done.flag.
CROSS_CHECK = Phase(
    name=PhaseName.CROSS_CHECK,
    prompt_file="",  # no LLM prompt; orchestrator runs code directly
    required_outputs=(VERDICT_FILE, FINDINGS_FILE, DONE_FLAG),
    optional_outputs=(),
    allowed_tools=(),
    timeout_seconds=5 * 60,
    description=(
        "Automated semantic cross-check on the most recent execute "
        "stage; emits a routing verdict for the orchestrator."
    ),
)

REVIEW = Phase(
    name=PhaseName.REVIEW,
    prompt_file="review.md",
    required_outputs=(
        REVIEW_NOTES_MD,
        NEGATIVE_RESULTS_MD,
        VERDICT_FILE,  # review emits its own routing verdict
        *_COMMON_STAGE_OUTPUTS,
    ),
    optional_outputs=(
        # devils-advocate subagent critique of the analysis.
        COUNTER_CRITIQUE_MD,
        KG_UPDATES_FILE,
    ),
    allowed_tools=_DEFAULT_TOOLS,
    timeout_seconds=45 * 60,
    description=(
        "Analysis-adequacy review: judge whether the evidence answers the "
        "preregistered question, record negative results, and route back "
        "for more analysis or finish."
    ),
)


# The FREE phase is invoked exclusively by SteeringPlan steps with
# phase="free". Its prompt is NOT loaded from disk; it comes from the
# SteeringStep.prompt_inline. Required outputs are minimal — just the
# stage's summary + done.flag — because the user's steering request
# may produce arbitrary side-effects (a regenerated figure, a
# sanity-check note) that don't fit any structured contract. When the
# user wants structure, they should target a real phase (execute,
# etc.) instead.
FREE = Phase(
    name=PhaseName.FREE,
    prompt_file="",  # rendered from SteeringStep.prompt_inline at dispatch
    required_outputs=_COMMON_STAGE_OUTPUTS,
    optional_outputs=(),
    allowed_tools=_DEFAULT_TOOLS,
    timeout_seconds=30 * 60,
    description=(
        "Ad-hoc steering phase. Prompt comes inline from the active "
        "SteeringStep. Not part of PIPELINE_ORDER."
    ),
)


IDEATE = Phase(
    name=PhaseName.IDEATE,
    prompt_file="ideate.md",
    # directions.json (folded into the graph) + the human-readable source
    # survey are the substantive outputs. Required so the stage's contract
    # is meaningful; both are validated by contracts.verify.
    required_outputs=(
        IDEATE_DIRECTIONS_FILE,
        IDEATE_SOURCES_FILE,
        *_COMMON_STAGE_OUTPUTS,
    ),
    optional_outputs=(),
    allowed_tools=_DEFAULT_TOOLS,   # needs WebFetch/WebSearch for the survey
    timeout_seconds=45 * 60,
    description=(
        "Standalone brainstorm front-stage: survey papers + high-impact "
        "blogs + stated beliefs across academia/laureates/philosophers/"
        "industry, build the knowledge graph, and propose research "
        "directions tied to the user's purpose. Not part of PIPELINE_ORDER."
    ),
)


# Order matches constants.PIPELINE_ORDER (cross_check between execute and
# review). Any drift here is caught by the test in tests/test_phases.py.
ORDER: tuple[Phase, ...] = (SCOPE, PLAN, EXECUTE, CROSS_CHECK, REVIEW)
# BY_NAME includes FREE + IDEATE so phases.get() works even though they
# are not in ORDER (FREE is dispatched via SteeringPlan steps; IDEATE is
# the standalone brainstorm front-stage invoked via `omicairn ideate`). Both
# are intentionally absent from the linear-pipeline walk.
BY_NAME: dict[str, Phase] = {p.name: p for p in (*ORDER, FREE, IDEATE)}


def get(name: str) -> Phase:
    if name not in BY_NAME:
        raise ValueError(
            f"unknown phase {name!r}; valid: {sorted(BY_NAME)}"
        )
    return BY_NAME[name]


def tools_with_web(phase: Phase, allow_web: bool) -> Sequence[str]:
    if allow_web:
        return phase.allowed_tools
    return tuple(t for t in phase.allowed_tools if t not in _WEB_TOOLS)


def next_phase_in_order(current: str) -> str | None:
    """The phase immediately after `current` in the linear pipeline.

    Returns None when `current` is the last phase (review).
    """
    if current not in PIPELINE_ORDER:
        raise ValueError(f"unknown phase {current!r}")
    idx = PIPELINE_ORDER.index(current)
    if idx + 1 >= len(PIPELINE_ORDER):
        return None
    return PIPELINE_ORDER[idx + 1]
