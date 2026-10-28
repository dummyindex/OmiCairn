"""Single source of truth for string constants.

Per design principle 7 and docs/DESIGN.md § Coding Conventions:
every string literal that appears in more than one place MUST be
hoisted into this module (or into an enum/dataclass defined here).

Why this file exists:
- Python has no compile-time typo protection.
- Duplicate string literals are silent drift bombs: rename one, miss
  another, break the pipeline at runtime with an error that is hard
  to trace.
- Phase names, stage-dir prefix format, verdict decision strings,
  render-token names, and knowledge-file paths all need to match
  exactly across orchestrator, prompts, contracts, and CLI. Any
  mismatch is a bug.

This module has NO imports from other `omicairn` modules, so anything
can import from it without creating cycles.
"""
from __future__ import annotations

from typing import Final


# =============================================================================
# Phase names
# =============================================================================
#
# The pipeline phases plus the standalone FREE/IDEATE phases. All but
# one dispatch a Claude Code (`claude -p`) subprocess. `cross_check` is a synthetic (pure-Python)
# phase that the orchestrator runs after `execute` to emit a verdict.
#
# Ordering matters: the "happy path" pipeline walks ORDER in sequence.

class PhaseName:
    SCOPE: Final[str] = "scope"
    PLAN: Final[str] = "plan"
    EXECUTE: Final[str] = "execute"
    CROSS_CHECK: Final[str] = "cross_check"
    # Analysis-adequacy review: judges whether the EXECUTE evidence answers
    # the preregistered question and routes back for more analysis if not.
    REVIEW: Final[str] = "review"
    # Ad-hoc steering phase (Slice 2). Not part of PIPELINE_ORDER —
    # only invoked when a SteeringPlan step has phase="free", in which
    # case the SteeringStep.prompt_inline IS the prompt (no template).
    FREE: Final[str] = "free"
    # IDEATE (a.k.a. "brainstorm") — standalone FRONT stage, run before
    # SCOPE when the user has a research PURPOSE but not yet a concrete
    # question. Surveys the landscape (papers + high-impact blogs +
    # stated beliefs from academia / professors / laureates /
    # philosophers / industry), writes them as `source` nodes in the
    # knowledge graph, and proposes research DIRECTIONS (proposed `goal`
    # nodes + seed hypotheses + `cites` edges to the motivating sources).
    # NOT in PIPELINE_ORDER — invoked via `omicairn ideate`; the user then
    # `--adopt`s a direction to seed the normal pipeline. Mirrors FREE:
    # a claude-code phase that the linear loop never auto-schedules.
    IDEATE: Final[str] = "ideate"


# Linear pipeline order. A "happy path" run walks this list in sequence.
# Backtracking via a verdict may jump to an earlier phase, subject to the
# `max_backtracks` bound. Not modifiable at runtime.
PIPELINE_ORDER: Final[tuple[str, ...]] = (
    PhaseName.SCOPE,
    PhaseName.PLAN,
    PhaseName.EXECUTE,
    PhaseName.CROSS_CHECK,
    PhaseName.REVIEW,
)

# Phases whose stages are produced by a Claude Code subprocess
# (`claude -p`). cross_check is the lone code-driven phase: the
# orchestrator runs it in pure Python with no agent session at all.
#
# The name captures the dispatch distinction — "this phase spawns a
# claude -p subprocess" vs "this phase runs in-process as Python" —
# which is what `orchestrator._run_stage` actually branches on. The
# old name `LLM_PHASES` was imprecise: every claude -p session is
# LLM-backed by construction, but the relevant distinction is the
# dispatch mechanism, not the underlying model.
CLAUDE_CODE_PHASES: Final[frozenset[str]] = frozenset(
    {PhaseName.SCOPE, PhaseName.PLAN, PhaseName.EXECUTE,
     PhaseName.REVIEW, PhaseName.FREE, PhaseName.IDEATE}
)

# Every phase name that can legitimately appear in a stage directory name.
# This is the AUTHORITATIVE set `parse_stage_dir_name` validates against —
# the linear PIPELINE_ORDER phases PLUS the standalone stages that are run
# on demand and never auto-scheduled (FREE/IDEATE steering steps).
# Keeping it here (not inlining `PIPELINE_ORDER` in the parser) is what makes
# the create/parse round-trip symmetric: `stage_dir_name` can build any of
# these, so `parse_stage_dir_name` must accept all of them (core-beliefs #14).
KNOWN_STAGE_PHASES: Final[frozenset[str]] = frozenset(PIPELINE_ORDER) | {
    PhaseName.FREE, PhaseName.IDEATE,
}

# Phases that emit a verdict.json that the orchestrator reads to decide
# routing. cross_check always emits one (code-driven). review emits one
# as part of its contract (LLM writes it).
VERDICT_PHASES: Final[frozenset[str]] = frozenset(
    {PhaseName.CROSS_CHECK, PhaseName.REVIEW}
)


# =============================================================================
# Stage directory naming
# =============================================================================
#
# Stages are numbered globally across the workspace, left-padded to
# `STAGE_INDEX_WIDTH` digits, joined to the phase name by
# `STAGE_INDEX_SEP`. Example: "000_scope", "001_plan", "002_execute",
# "003_cross_check", "004_execute", ...
#
# Rationale: flat numbered layout preserves strict temporal order and
# makes history inspectable via `ls` without needing git or symlinks.

STAGE_INDEX_WIDTH: Final[int] = 3
STAGE_INDEX_SEP: Final[str] = "_"
STAGE_INDEX_MAX: Final[int] = 10 ** STAGE_INDEX_WIDTH  # 1000


def stage_dir_name(index: int, phase_name: str) -> str:
    """Format a stage directory name: (5, "execute") -> "005_execute"."""
    if index < 0 or index >= STAGE_INDEX_MAX:
        raise ValueError(
            f"stage index {index} out of range [0, {STAGE_INDEX_MAX})"
        )
    return f"{index:0{STAGE_INDEX_WIDTH}d}{STAGE_INDEX_SEP}{phase_name}"


def parse_stage_dir_name(name: str) -> tuple[int, str] | None:
    """Parse a stage directory name back to (index, phase_name).

    Returns None if `name` is not a valid stage directory name.
    """
    if len(name) < STAGE_INDEX_WIDTH + 1:
        return None
    prefix = name[:STAGE_INDEX_WIDTH]
    if not prefix.isdigit():
        return None
    if name[STAGE_INDEX_WIDTH] != STAGE_INDEX_SEP:
        return None
    phase = name[STAGE_INDEX_WIDTH + 1 :]
    # Accept any KNOWN stage phase, not just PIPELINE_ORDER: standalone
    # stages (ideate/free) are real stage dirs that stage_dir_name
    # creates, so the parser must round-trip them too (core-beliefs #14).
    if phase not in KNOWN_STAGE_PHASES:
        return None
    return int(prefix), phase


# =============================================================================
# Knowledge journal
# =============================================================================
#
# Each workspace has a `knowledge/` directory that accumulates lessons,
# a running narrative, and project-specific gotchas. Subagents append
# to these files as they discover non-obvious things.

KNOWLEDGE_DIR: Final[str] = "knowledge"
LESSONS_FILE: Final[str] = "lessons.jsonl"
JOURNAL_FILE: Final[str] = "journal.md"
DOMAIN_NOTES_FILE: Final[str] = "domain_notes.md"

# Per-workspace knowledge graph — append-only op-log of node/edge
# upserts, folded to current state on read (see omicairn.knowledge_graph
# and docs/design-docs/dynamic-hypotheses.md). Lives inside KNOWLEDGE_DIR.
# The graph is the durable product of a run (core belief 11).
KG_GRAPH_FILE: Final[str] = "graph.jsonl"
KG_LOCK_FILE: Final[str] = ".graph.lock"
# Structured graph-update payload an EXECUTE/REVIEW agent may drop in its
# stage dir; the orchestrator folds it into the workspace graph. Absent
# for legacy cases — ingestion is additive + silent when the file is
# missing. See knowledge_graph.ingest_graph_updates.
KG_UPDATES_FILE: Final[str] = "graph_updates.json"


class GraphUpdateVerdict:
    """Canonical `verdict` field VALUES on a `graph_updates.json`
    ``result_refs[]`` entry — the EMITTER (prompts/execute.md) / CONSUMER
    (knowledge_graph.ingest_graph_updates) contract for whether a result
    supports, refutes, or is inconclusive about its hypothesis. SUPPORTS/
    REFUTES map to an EdgeRel + HypothesisStatus advance; INCONCLUSIVE is a
    status-only advance (no edge). Spelled like EdgeRel.SUPPORTS/REFUTES but
    a DISTINCT field (result->hypothesis verdict, not a graph edge relation),
    so it lives here, not aliasing EdgeRel. INCONCLUSIVE was previously
    ORPHANED — matched no constant anywhere. (Same drift class as EventKind.)
    """
    SUPPORTS: Final[str] = "supports"
    REFUTES: Final[str] = "refutes"
    INCONCLUSIVE: Final[str] = "inconclusive"


GRAPH_UPDATE_VERDICTS: Final[frozenset[str]] = frozenset({
    GraphUpdateVerdict.SUPPORTS,
    GraphUpdateVerdict.REFUTES,
    GraphUpdateVerdict.INCONCLUSIVE,
})

# IDEATE stage artifacts. `directions.json` is the structured proposal
# the IDEATE agent emits (research directions + the sources motivating
# each); the orchestrator folds it into the knowledge graph via
# knowledge_graph.ingest_ideation. `sources.md` is the human-readable
# annotated source survey. See prompts/ideate.md.
IDEATE_DIRECTIONS_FILE: Final[str] = "directions.json"
IDEATE_SOURCES_FILE: Final[str] = "sources.md"


class NodeType:
    """Knowledge-graph node kinds. See dynamic-hypotheses.md § 2.1."""
    GOAL: Final[str] = "goal"
    HYPOTHESIS: Final[str] = "hypothesis"
    FINDING: Final[str] = "finding"
    RESULT_REF: Final[str] = "result_ref"
    DATASET_REF: Final[str] = "dataset_ref"
    METADATA: Final[str] = "metadata"
    # External information source ingested by the IDEATE stage: a paper,
    # a high-impact blog/essay, or a stated belief/position from a named
    # authority (professor, Nobel laureate, classical philosopher, lab,
    # industry). Carries `source_type` (see SourceType) in extra. A
    # proposed research direction `cites` the sources that motivated it,
    # so every direction is traceable to its provenance — and the user
    # can weight provenance (belief 13, prefer_reputable_sources).
    SOURCE: Final[str] = "source"


NODE_TYPES: Final[frozenset[str]] = frozenset({
    NodeType.GOAL, NodeType.HYPOTHESIS, NodeType.FINDING,
    NodeType.RESULT_REF, NodeType.DATASET_REF, NodeType.METADATA,
    NodeType.SOURCE,
})


class SourceType:
    """Provenance taxonomy for IDEATE `source` nodes. Lets directions be
    ranked/weighted by where the idea came from (belief 13). Domain-
    agnostic: these are KINDS of authority, not fields."""
    PAPER: Final[str] = "paper"               # peer-reviewed / preprint
    BLOG: Final[str] = "blog"                 # high-impact blog / essay
    ACADEMIA: Final[str] = "academia"         # institutional / lab position
    PROFESSOR: Final[str] = "professor"       # named senior academic
    NOBEL: Final[str] = "nobel"               # Nobel / Turing / Fields laureate
    PHILOSOPHER: Final[str] = "philosopher"   # classical / contemporary
    INDUSTRY: Final[str] = "industry"         # frontier-lab / practitioner
    OTHER: Final[str] = "other"


SOURCE_TYPES: Final[frozenset[str]] = frozenset({
    SourceType.PAPER, SourceType.BLOG, SourceType.ACADEMIA,
    SourceType.PROFESSOR, SourceType.NOBEL, SourceType.PHILOSOPHER,
    SourceType.INDUSTRY, SourceType.OTHER,
})


class EdgeRel:
    """Knowledge-graph edge relations. See dynamic-hypotheses.md § 2.2."""
    TESTS: Final[str] = "tests"
    SUPPORTS: Final[str] = "supports"
    REFUTES: Final[str] = "refutes"
    DERIVED_FROM: Final[str] = "derived_from"
    RAISES: Final[str] = "raises"
    CITES: Final[str] = "cites"


EDGE_RELS: Final[frozenset[str]] = frozenset({
    EdgeRel.TESTS, EdgeRel.SUPPORTS, EdgeRel.REFUTES,
    EdgeRel.DERIVED_FROM, EdgeRel.RAISES, EdgeRel.CITES,
})


class HypothesisStatus:
    """Lifecycle states for a hypothesis node."""
    PROPOSED: Final[str] = "proposed"
    TESTING: Final[str] = "testing"
    SUPPORTED: Final[str] = "supported"
    REFUTED: Final[str] = "refuted"
    INCONCLUSIVE: Final[str] = "inconclusive"
    SUPERSEDED: Final[str] = "superseded"


# Statuses a hypothesis may legally move *to* from a given status.
# proposed → testing → {supported, refuted, inconclusive}; any state may
# be superseded by a sharper hypothesis. supported/refuted/inconclusive
# may be revisited (→ testing) if new evidence arrives.
HYPOTHESIS_TRANSITIONS: Final[dict[str, frozenset[str]]] = {
    HypothesisStatus.PROPOSED: frozenset({
        HypothesisStatus.TESTING, HypothesisStatus.SUPERSEDED,
    }),
    HypothesisStatus.TESTING: frozenset({
        HypothesisStatus.SUPPORTED, HypothesisStatus.REFUTED,
        HypothesisStatus.INCONCLUSIVE, HypothesisStatus.SUPERSEDED,
    }),
    HypothesisStatus.SUPPORTED: frozenset({
        HypothesisStatus.TESTING, HypothesisStatus.SUPERSEDED,
    }),
    HypothesisStatus.REFUTED: frozenset({
        HypothesisStatus.TESTING, HypothesisStatus.SUPERSEDED,
    }),
    HypothesisStatus.INCONCLUSIVE: frozenset({
        HypothesisStatus.TESTING, HypothesisStatus.SUPERSEDED,
    }),
    HypothesisStatus.SUPERSEDED: frozenset(),
}

# Statuses whose entry requires at least one on-disk-resolvable
# supports/refutes edge (the mechanical honesty rule, belief 10 + 11).
HYPOTHESIS_STATUSES_REQUIRING_EVIDENCE: Final[frozenset[str]] = frozenset({
    HypothesisStatus.SUPPORTED, HypothesisStatus.REFUTED,
})


class HypothesisOrigin:
    PREREGISTERED: Final[str] = "preregistered"
    ON_THE_FLY: Final[str] = "on_the_fly"
    MANUAL: Final[str] = "manual"

# Free-form notes from the user, injected into the next phase prompt.
# Written by `omicairn note`; read by orchestrator._render_prompt via
# {{user_notes}}; one-shot entries move to USER_NOTES_CONSUMED_FILE
# after the matching phase completes successfully.
USER_NOTES_FILE: Final[str] = "user_notes.md"
USER_NOTES_CONSUMED_FILE: Final[str] = "user_notes_consumed.md"

# Append-only event log, at workspace root.
EVENTS_LOG: Final[str] = "events.jsonl"


# =============================================================================
# Event kinds (the `kind` field of every append_event / steering-log record)
# =============================================================================
#
# Every JSONL record the orchestrator writes carries a `kind` tag that
# post-hoc tooling (`omicairn status`, events timelines, tests) keys off to
# classify the record. Historically these were ~40 raw string literals
# scattered across the emit sites with the consumers (tests, the steering
# verifier, `omicairn status`) matching by RAW LITERAL — the exact emitter/
# consumer string-coupling that has already caused drift bugs (the
# steering-verifier predicate drift; the literature arxiv-slug drift). A
# renamed or typo'd kind silently broke a consumer with no error.
#
# `EventKind` is the single source of truth. Emitters reference
# `EventKind.X`; consumers compare against the same attribute. Values are
# BYTE-IDENTICAL to the historical literals so the events.jsonl /
# steering_log.jsonl streams are unchanged and existing workspaces, the
# cron, and external tooling keep working.
#
# Grouped by subsystem. Two physical logs share this namespace: the
# workspace event stream (`events.jsonl`, via omicairn.stage.append_event) and
# the steering audit log (`steering_log.jsonl`, via omicairn.steering); both
# tag records with `kind`, so one taxonomy covers both. (Other JSONL
# stores — human-feedback records, knowledge-graph ops, lab-knowledge
# entries, the routing_suggestion.json schema — are SEPARATE validated
# taxonomies with their own consumers and are intentionally NOT folded in.)

class EventKind:
    """Canonical `kind` tags for orchestrator event / steering-log records."""

    # ── Workspace / stage lifecycle ──────────────────────────────────
    WORKSPACE_INIT: Final[str] = "workspace_init"
    LEGACY_AGENTS_REMOVED: Final[str] = "legacy_agents_removed"
    STAGE_DONE: Final[str] = "stage_done"
    STAGE_TIMEOUT: Final[str] = "stage_timeout"
    STAGE_CONTRACT_FAIL: Final[str] = "stage_contract_fail"
    STAGE_RETRY_AFTER_CONTRACT_FAIL: Final[str] = "stage_retry_after_contract_fail"
    STAGE_RETRIES_EXHAUSTED: Final[str] = "stage_retries_exhausted"
    # A CONTRACT_FAIL's record_stage_start increment was moved OUT of the
    # science-iteration count (iteration_counts) and INTO the bounded
    # contract-retry budget (contract_retry_counts). Observability only.
    CONTRACT_FAIL_RECLASSIFIED: Final[str] = "contract_fail_reclassified"
    CONTRACT_RETRIES_EXHAUSTED: Final[str] = "contract_retries_exhausted"
    STAGE_STUCK_CONTRACT_FAIL_HALT: Final[str] = "stage_stuck_contract_fail_halt"
    FINALIZED_VALID_UNSTAMPED_STAGE: Final[str] = "finalized_valid_unstamped_stage"
    # Periodic liveness beacon emitted by the claude_runner inactivity
    # watchdog while a (long) stage subprocess is running. Surfaces the
    # liveness signal the watchdog ALREADY computes (log/heartbeat/file
    # GROWTH) to events.jsonl so an EXTERNAL auditor (the loop, a human,
    # `omicairn status`) can distinguish "alive, last output 40s ago" from a
    # true hang WITHOUT forensic mtime/SSH digging. Best-effort
    # observability only — never load-bearing, never gates routing (a
    # mid-LLM-call EXECUTE turn is externally identical to a hang by
    # process inspection; this is the fix for that, audit 2026-06-14).
    EXECUTE_PROGRESS: Final[str] = "execute_progress"

    # ── Run-control (bounds, caps, locks, session limits) ────────────
    HALTED_BOUNDS: Final[str] = "halted_bounds"
    # A cross_check-originated retry_execute (ERROR-level fabrication/honesty
    # block: W13/W15/W16 ERROR sink + CI-order / point-in-CI / p-value /
    # prereg-sha256 checks) hit its execute-retry cap. A capped cross_check
    # ERROR retry is a CORRECTNESS gate and must NOT graceful-advance to
    # REVIEW/finalize; it HALTS for inspection
    # (bug #123a — a run had walked PAST a genuinely-blocking cross_check
    # verdict). See orchestrator._capped_blocking_cross_check_retry.
    HALTED_BLOCKING_CROSS_CHECK: Final[str] = "halted_blocking_cross_check"
    MAX_ITERS_RECONCILED_FROM_CONFIG: Final[str] = "max_iters_reconciled_from_config"
    # DYNAMIC iter-mode zero-progress spin-catcher (#133): a verdict phase
    # re-opened ZERO_PROGRESS_STALL_LIMIT times in a row emitting an IDENTICAL
    # progress fingerprint (no forward progress) was halted as genuine non-
    # convergence — the safety that makes the uncapped DYNAMIC ceiling safe.
    # See orchestrator zero-progress branch + state.zero_progress_exceeded.
    ZERO_PROGRESS_HALT: Final[str] = "zero_progress_halt"
    CLAUDE_SESSION_LIMIT: Final[str] = "claude_session_limit"
    RUN_LOCK_REFUSED: Final[str] = "run_lock_refused"
    RUN_ALL_CAP: Final[str] = "run_all_cap"
    RUN_ALL_TOTAL_WALL_EXCEEDED: Final[str] = "run_all_total_wall_exceeded"
    REAPED_ORPHAN: Final[str] = "reaped_orphan"
    # The EXECUTE-phase wall budget was raised to the GPU/multimodal FLOOR
    # because the configured/default value was too tight for a GPU-heavy or
    # vision/video run (see orchestrator GPU_EXECUTE_WALL_FLOOR_MINUTES and
    # the 2026-06-15 wave-2 4/6-timeout incident). Best-effort observability;
    # never gates routing.
    EXECUTE_WALL_FLOOR_APPLIED: Final[str] = "execute_wall_floor_applied"

    # The EXECUTE-phase INACTIVITY watchdog timeout was raised to the
    # GPU/multimodal FLOOR (GPU_INACTIVITY_FLOOR_SECONDS) because the
    # configured/default value was too tight for a GPU-heavy or vision/video
    # run whose remote per-shard GPU job goes quiet between heartbeat touches
    # (see orchestrator GPU_INACTIVITY_FLOOR_SECONDS and the 2026-06-15 d3
    # wm-dynamics-decouple false-kill incident). Sibling of the wall floor
    # above. Best-effort observability; never gates routing.
    EXECUTE_INACTIVITY_FLOOR_APPLIED: Final[str] = "execute_inactivity_floor_applied"

    # DYNAMIC stage-time-limit mode: an execute-class stage crossed its
    # (soft) per-phase wall ceiling WHILE STILL PROGRESSING (a liveness
    # signal advanced within the inactivity window), so the wall was NOT
    # enforced as a kill — the stage was allowed to continue and the wall
    # demoted to a soft ceiling. Emitted (throttled to the beacon cadence)
    # each time the poll loop observes the job past its wall but alive.
    # Surfaces estimate-vs-actual so an auditor sees a long arm was extended
    # for compute rather than truncated (the d7 LLaVA-NeXT held-out drop).
    # Best-effort observability; never gates routing. See claude_runner
    # wall_kill_requires_stall + orchestrator.estimate_execute_wall_seconds.
    EXECUTE_WALL_SOFT_EXTENDED: Final[str] = "execute_wall_soft_extended"

    # DYNAMIC mode ABSOLUTE backstop: an execute-class stage ran past the
    # hard safety ceiling (EXECUTE_HARD_CEILING_SECONDS) and was KILLED even
    # though it may have been touching files — a runaway that has consumed
    # days of wall while "progressing" is a real pathology, not science.
    # This is the one bound DYNAMIC mode never soft-extends. Emitted once,
    # immediately before the kill. Best-effort observability.
    EXECUTE_HARD_CEILING_KILL: Final[str] = "execute_hard_ceiling_kill"

    # ── Steering ─────────────────────────────────────────────────────
    # The three plan/verifier-results kinds live in steering_log.jsonl
    # (omicairn.steering append_log / write_plan); the rest are events.jsonl.
    STEERING_PLAN_WRITE: Final[str] = "steering_plan_write"
    STEERING_PLAN_CONSUMED: Final[str] = "steering_plan_consumed"
    STEERING_VERIFIER_RESULTS: Final[str] = "steering_verifier_results"
    STEERING_VERIFIER_WARNING: Final[str] = "steering_verifier_warning"
    STEERING_REACTIVATED_TERMINAL_RUN: Final[str] = "steering_reactivated_terminal_run"
    HALTED_UNKNOWN_STEERING_PHASE: Final[str] = "halted_unknown_steering_phase"
    LEGACY_STEERING_STEP_SKIPPED: Final[str] = "legacy_steering_step_skipped"

    # ── Knowledge graph / lab knowledge ──────────────────────────────
    KNOWLEDGE_GRAPH_SEEDED: Final[str] = "knowledge_graph_seeded"
    KNOWLEDGE_GRAPH_SEED_ERROR: Final[str] = "knowledge_graph_seed_error"
    KNOWLEDGE_GRAPH_INGEST_ERROR: Final[str] = "knowledge_graph_ingest_error"
    KNOWLEDGE_GRAPH_UPDATED: Final[str] = "knowledge_graph_updated"
    LAB_KNOWLEDGE_DISTILLED: Final[str] = "lab_knowledge_distilled"
    LAB_KNOWLEDGE_DISTILL_ERROR: Final[str] = "lab_knowledge_distill_error"
    # Serendipity-promotion loop (creativity audit F1). A post-run,
    # distill-time step that promotes a SUPPORTED + surprising/cheap-to-test
    # ON-THE-FLY hypothesis into a PROPOSED (un-adopted) ideated direction in
    # the cross-run lab KB — so a within-run discovery earns a SEAT AT IDEATE
    # (picked up by the continuous-loop cron / `omicairn ideate`) WITHOUT
    # auto-launching a run or relaxing any W-gate. *_PROMOTED carries the
    # promoted count; *_PROMOTE_ERROR when the (best-effort) step raised.
    # Gated OFF by default behind ENV_ENABLE_SERENDIPITY. See
    # lab_knowledge.promote_serendipitous_directions + the F1 design doc.
    SERENDIPITY_PROMOTED: Final[str] = "serendipity_promoted"
    SERENDIPITY_PROMOTE_ERROR: Final[str] = "serendipity_promote_error"

    # ── Ideation / literature ────────────────────────────────────────
    IDEATION_INGESTED: Final[str] = "ideation_ingested"
    IDEATION_INGEST_ERROR: Final[str] = "ideation_ingest_error"
    IDEATE_SOURCES_TO_LITERATURE: Final[str] = "ideate_sources_to_literature"
    IDEATE_SOURCES_TO_LITERATURE_ERROR: Final[str] = "ideate_sources_to_literature_error"
    DATASET_REFS_SEEDED: Final[str] = "dataset_refs_seeded"
    DATASET_REFS_SEED_ERROR: Final[str] = "dataset_refs_seed_error"

    # ── SCOPE feasibility / statistical-power gate ───────────────────
    # Emitted by orchestrator._process_feasibility_gate after a SCOPE
    # stage. *_inactive: gate off for this case but SCOPE emitted a
    # feasibility.json anyway. *_missing: gate active but no
    # feasibility.json (conservative proceed). *_invalid: present but
    # failed schema validation (warn-only proceed — the gate is a safety
    # rail, never a schema-typo halt). *_evaluated: a valid verdict was
    # read and routed on. None gate routing on belief direction — only
    # statistical power / data feasibility (belief 13).
    FEASIBILITY_GATE_INACTIVE: Final[str] = "feasibility_gate_inactive"
    FEASIBILITY_GATE_MISSING: Final[str] = "feasibility_gate_missing"
    FEASIBILITY_GATE_INVALID: Final[str] = "feasibility_gate_invalid"
    FEASIBILITY_GATE_EVALUATED: Final[str] = "feasibility_gate_evaluated"


# Event kinds that are PURE OBSERVABILITY — they are written to events.jsonl
# on a TIMER while a stage runs (or at finalize-time) and so do NOT, by
# themselves, prove the stage made REAL progress. The liveness/stall machinery
# (fleet `last_activity_age_s`, the in-process watchdog's file-write signal)
# MUST NOT count these as activity, or a WEDGED stage's own beacon masks the
# stall — the run keeps looking "active" while its inner agent is frozen
# (the beacon-masks-watchdog bug, audit 2026-06-17: d1 047_execute sat 44
# min with a frozen inner `claude`, 0 stage-dir writes, 0 non-beacon events,
# yet the periodic EXECUTE_PROGRESS beacon kept events.jsonl — and thus
# `omicairn status`'s last-activity age — looking fresh, hiding the stall). Real
# progress (a stage-dir file write, a NON-beacon event, a heartbeat touch)
# still resets the clock; only these timer-emitted observability beacons are
# discounted. Keep this a CANONICAL set (never an inline literal) so the
# emitter (claude_runner._emit_progress_beacon) and the consumers (fleet,
# the watchdog) can never drift apart.
NON_PROGRESS_EVENT_KINDS: Final[frozenset[str]] = frozenset({
    EventKind.EXECUTE_PROGRESS,
    # The DYNAMIC-mode wall beacons are ALSO timer-emitted by the in-process
    # watchdog poll loop (claude_runner._watchdog_poll_loop), NOT proof of real
    # stage work — so they must be discounted for the SAME reason as
    # EXECUTE_PROGRESS (the beacon-masks-watchdog class, audit 2026-06-17).
    # EXECUTE_WALL_SOFT_EXTENDED in particular is appended every beacon-cadence
    # poll for the ENTIRE inactivity grace window even when NO liveness signal
    # advanced this poll (the loop keeps soft-extending until the stall
    # predicate fires). If it counted as progress, a wedged-but-past-soft-wall
    # stage's own soft-extend beacons would keep fleet.last_activity_age_s
    # (and `omicairn status` + external recovery crons) looking fresh for up to one
    # inactivity window, masking the stall — exactly the failure the
    # beacon-masks-watchdog fix resolved. EXECUTE_HARD_CEILING_KILL is added for
    # consistency (it fires once, immediately before a kill, so it could never
    # meaningfully mask a stall — but the emitter/consumer sets must never drift).
    EventKind.EXECUTE_WALL_SOFT_EXTENDED,
    EventKind.EXECUTE_HARD_CEILING_KILL,
})


# Run-level reproducibility provenance: append-only, one entry per
# workspace-touching invocation (init / run / ideate),
# recording the OmiCairn SYSTEM git commit + dirty flag + datetime so any
# stage's result can be traced to the exact code state that produced
# it. Distinct from paper_library's LIBRARY-root provenance.jsonl —
# this one lives at the WORKSPACE root and the workspace does not
# otherwise use that filename. See omicairn.provenance.
PROVENANCE_LOG: Final[str] = "provenance.jsonl"
PROVENANCE_SCHEMA_VERSION: Final[int] = 1


# Per-stage code-provenance stamp. The run-level provenance.jsonl above is
# per-INVOCATION (one line per init/run/ideate), so on a run resumed
# across many invocations at different engine commits, a single stage cannot
# be mapped back to the exact engine SHA that produced it (the convexbench run
# spans 2 SHAs across 3 `run` lines, all dirty). This per-stage file closes
# that gap: one stamp per stage dir, written once at stage creation, recording
# the engine git commit live at that moment. Phase 1 = additive, no gate reads
# it. See omicairn.provenance.write_stage_stamp + claude_docs human_review
# 2026-06-20-code-provenance-dual-mode-design.md.
CODE_PROVENANCE_STAMP: Final[str] = "code_provenance.json"
CODE_PROVENANCE_SCHEMA_VERSION: Final[int] = 1


class CodeProvenanceMode:
    """Canonical ``omicairn_config.yaml`` ``code_provenance.mode`` values.

    Consumers (per-stage stamp writer; future snapshot-vs-pinned switching +
    gate-skip in phase 2) reference these members, never a string literal.
    ``git_pinned`` is a strict superset of ``snapshot_copy`` information (it
    keeps the per-stage ``code/`` dir AND pins the engine SHA + a content
    manifest), so it is the recommended default. See the dual-mode design doc.
    """

    GIT_PINNED: Final[str] = "git_pinned"
    SNAPSHOT_COPY: Final[str] = "snapshot_copy"
    DEFAULT: Final[str] = GIT_PINNED
    ALL: Final[frozenset[str]] = frozenset({GIT_PINNED, SNAPSHOT_COPY})


class CodeProvenanceKey:
    """Canonical JSON keys in a per-stage ``code_provenance.json`` payload.

    Phase 1 populates SCHEMA_VERSION / MODE / ENGINE / RECORDED_AT only; the
    EXPERIMENT_CODE manifest, ENGINE_DIFF_*, and REFERENCE_IMPL keys are
    reserved for the phase-2 ``git_pinned`` content-hashing increment (deferred
    pending owner sign-off — it touches the EXECUTE prompt + cross_check's
    code/-dir dependency).
    """

    SCHEMA_VERSION: Final[str] = "schema_version"
    MODE: Final[str] = "mode"
    ENGINE: Final[str] = "engine"
    RECORDED_AT: Final[str] = "recorded_at"
    # Reserved for phase 2 (git_pinned content-hashing) — documented, unused.
    ENGINE_DIFF_PATH: Final[str] = "engine_diff_path"
    ENGINE_DIFF_TRUNCATED: Final[str] = "engine_diff_truncated"
    EXPERIMENT_CODE: Final[str] = "experiment_code"
    REFERENCE_IMPL: Final[str] = "reference_impl"


# =============================================================================
# Per-stage artifact names
# =============================================================================
#
# Every stage directory contains these files at the root of the stage dir
# (not under a phase-name subdirectory). Paths below are bare filenames
# relative to the stage directory.

DONE_FLAG: Final[str] = "done.flag"
SUMMARY_FILE: Final[str] = "summary.md"
PRIOR_STAGES_FILE: Final[str] = "prior_stages.md"
PHASE_LOG: Final[str] = "phase.log"
VERDICT_FILE: Final[str] = "verdict.json"
FINDINGS_FILE: Final[str] = "findings.md"
# Per-stage append-only log of on-the-way data fetches. Written by
# EXECUTE when it pulls inputs from public URLs into the shared
# dataset cache. Parsed by cross_check._check_data_fetch to surface
# failed fetches as informational warnings.
DATA_FETCH_LOG: Final[str] = "data_fetch_log.md"
# OPTIONAL EXECUTE artifact. Written by EXECUTE only when the analysis
# it must run honestly cannot finish within the wall/CPU budget (or
# needs a GPU, which is NOT available locally). It is the explicit,
# user-facing resource request that EXECUTE emits *instead* of silently
# subsampling the final/benchmark analysis to fit the clock. Never
# contract-required — its absence is the normal case.
RESOURCE_REQUEST_MD: Final[str] = "RESOURCE_REQUEST.md"
# SCOPE feasibility / statistical-power gate artifact (JSON-only
# contract, like verdict.json). Written by SCOPE when
# the feasibility gate is active for the case (see omicairn.feasibility).
# Carries a per-analysis-unit extractable-N profile and an overall
# power/data-feasibility verdict the orchestrator routes on, BEFORE PLAN
# locks a pre-registration. The POWER JUDGMENT is the model's (prompt-
# driven); the harness only enforces the schema + routes on the verdict.
# Optional output: absent for exploratory / gate-disabled cases, so
# legacy SCOPE stages contract-pass unchanged. See prompts/scope.md and
# docs/design-docs/feasibility-gate.md.
FEASIBILITY_FILE: Final[str] = "feasibility.json"

# Per-phase primary artifacts. Shared across phases.py (contract),
# cross_check.py (semantic checks) and claude_runner.py (dry-run
# placeholders). Hoisted here so
# renaming a filename touches exactly one line.
PREREGISTRATION_FILE: Final[str] = "preregistration.json"
RESULTS_JSON: Final[str] = "results.json"
ANALYSIS_MD: Final[str] = "analysis.md"
FIGURES_CONTRACT_JSON: Final[str] = "figures_contract.json"
# REVIEW-stage outputs. counter_critique.md is the devils-advocate critique of
# the analysis; negative_results.md is the consolidated record of every
# refuted / inconclusive / untestable hypothesis and every dropped, deferred,
# failed or off-target arm, with evidence paths (a first-class deliverable).
REVIEW_NOTES_MD: Final[str] = "review_notes.md"
COUNTER_CRITIQUE_MD: Final[str] = "counter_critique.md"
NEGATIVE_RESULTS_MD: Final[str] = "negative_results.md"
CODE_SUBDIR: Final[str] = "code"
FIGURES_SUBDIR: Final[str] = "figures"
RESULTS_SUBDIR: Final[str] = "results"
CODE_MAIN_PY: Final[str] = "code/main.py"

# Tier 3.1 routing-advisor protocol. A pi-advisor (or any agent) may drop a
# `routing_suggestion.json` on the workspace root (or a stage dir) advising the
# orchestrator on the next decision; once read it is renamed to end in
# `.consumed.json` so it never fires twice. The filename, the consumed suffix,
# and the JSON schema keys are the emitter/consumer contract shared between
# orchestrator.py (consumer) and the prompt files (emitter) — hoisted here so
# the contract lives in ONE place (design rule "no string-literal soup"; the same
# coupling EventKind was created to eliminate).
ROUTING_SUGGESTION_FILE: Final[str] = "routing_suggestion.json"
# Suffix the consumed file is renamed to (via pathlib.Path.with_suffix, which
# replaces only the final ``.json`` component → ``routing_suggestion.consumed.json``).
ROUTING_SUGGESTION_CONSUMED_SUFFIX: Final[str] = ".consumed.json"


class RoutingSuggestionKey:
    """Canonical JSON keys in a `routing_suggestion.json` payload.

    The canonical schema (parser audit Step F, since 2026-05) uses
    KIND/TARGET_PHASE/CONFIDENCE/REASON; the deprecated legacy schema used
    SUGGESTED_DECISION (`route_to_<phase>`) + RATIONALE, still accepted
    warn-only for back-compat. All consumers (orchestrator.py routing read +
    the routing-suggestion render) reference these members, never a literal.
    """

    # ── Canonical schema ─────────────────────────────────────────────
    KIND: Final[str] = "kind"
    TARGET_PHASE: Final[str] = "target_phase"
    CONFIDENCE: Final[str] = "confidence"
    REASON: Final[str] = "reason"
    # The canonical `kind` value that marks a payload as a routing command.
    KIND_ROUTE: Final[str] = "route"

    # ── Legacy (deprecated, warn-only back-compat) ───────────────────
    SUGGESTED_DECISION: Final[str] = "suggested_decision"
    RATIONALE: Final[str] = "rationale"
    # The legacy decision prefix: `route_to_<phase>`.
    ROUTE_TO_PREFIX: Final[str] = "route_to_"


# =============================================================================
# Verdict schema
# =============================================================================
#
# A verdict.json file carries a decision from a verdict-emitting phase
# (cross_check or the analysis review) telling the orchestrator what to do next.

class Decision:
    """Routing decisions the orchestrator understands.

    PROCEED: move to the next phase in PIPELINE_ORDER.
    RETRY_<PHASE>: re-run the named phase as a new stage.
    BACK_TO_<PHASE>: jump backwards to the named phase (counts as a
        backtrack — bounded by max_backtracks).
    HALT: stop the pipeline; write halted.flag; leave state for inspection.
    DONE: pipeline is complete, no further stages.
    """
    PROCEED: Final[str] = "proceed"
    RETRY_EXECUTE: Final[str] = "retry_execute"
    BACK_TO_SCOPE: Final[str] = "back_to_scope"
    BACK_TO_PLAN: Final[str] = "back_to_plan"
    BACK_TO_EXECUTE: Final[str] = "back_to_execute"
    HALT: Final[str] = "halt"
    DONE: Final[str] = "done"


class VerdictKey:
    """Canonical KEY names in a `verdict.json` payload — the most load-bearing
    schema in the system (it gates every routing decision). The dict KEYS were
    bare literals across verdict.py (read), orchestrator.py + claude_runner.py
    (read/write); hoisted so the key contract lives in ONE place
    (design rule "no string-literal soup"). The VALUES of `decision` are the
    `Decision` enum above; these are only the KEY strings. VERDICT is the
    accepted top-level alias for DECISION (some agents emit `{"verdict": ...}`;
    see verdict.read_verdict).
    """
    DECISION: Final[str] = "decision"
    VERDICT: Final[str] = "verdict"
    REASON: Final[str] = "reason"
    EVIDENCE: Final[str] = "evidence"
    PROPOSED_FIX: Final[str] = "proposed_fix"
    TARGET_FILE_HINTS: Final[str] = "target_file_hints"


# Map a BACK_TO_* / RETRY_* decision to the target phase it routes to.
DECISION_TO_PHASE: Final[dict[str, str]] = {
    Decision.RETRY_EXECUTE: PhaseName.EXECUTE,
    Decision.BACK_TO_SCOPE: PhaseName.SCOPE,
    Decision.BACK_TO_PLAN: PhaseName.PLAN,
    Decision.BACK_TO_EXECUTE: PhaseName.EXECUTE,
}

# Decisions that count as a backtrack (jump to an earlier phase in order).
BACKTRACK_DECISIONS: Final[frozenset[str]] = frozenset({
    Decision.BACK_TO_SCOPE,
    Decision.BACK_TO_PLAN,
    Decision.BACK_TO_EXECUTE,
})

# All non-terminal decisions (the orchestrator will keep running).
NON_TERMINAL_DECISIONS: Final[frozenset[str]] = (
    frozenset({Decision.PROCEED, Decision.RETRY_EXECUTE})
    | BACKTRACK_DECISIONS
)

# Terminal decisions (the orchestrator stops after one of these).
TERMINAL_DECISIONS: Final[frozenset[str]] = frozenset({Decision.HALT, Decision.DONE})


# =============================================================================
# SCOPE feasibility / statistical-power gate
# =============================================================================
#
# SCOPE emits feasibility.json (FEASIBILITY_FILE) declaring, per planned
# analysis unit, the ACTUALLY extractable N vs the reference effect's
# requirement, plus an overall verdict + recommended remediation. The
# gate keys ONLY on statistical power / data feasibility, NEVER on the
# hypothesised direction of any result — a well-powered NULL passes
# cleanly (neutrality, belief 13). The judgment is the model's; the
# harness validates the schema and routes on the verdict. See
# omicairn.feasibility and docs/design-docs/feasibility-gate.md.

class FeasibilityVerdict:
    """Overall power/data verdict the SCOPE agent emits."""
    FEASIBLE: Final[str] = "feasible"          # adequately powered → proceed
    UNDERPOWERED: Final[str] = "underpowered"  # extractable N too small
    NEEDS_DATA: Final[str] = "needs_data"      # a required dataset is missing


FEASIBILITY_VERDICTS: Final[frozenset[str]] = frozenset({
    FeasibilityVerdict.FEASIBLE,
    FeasibilityVerdict.UNDERPOWERED,
    FeasibilityVerdict.NEEDS_DATA,
})


class RecommendedAction:
    """Remediation the SCOPE agent recommends; maps to a routing Decision."""
    PROCEED: Final[str] = "proceed"                # → next phase (plan)
    FIX_EXTRACTION: Final[str] = "fix_extraction"  # → back_to_scope (re-profile)
    ACQUIRE_DATA: Final[str] = "acquire_data"      # → back_to_plan (fetch first)
    RESCOPE: Final[str] = "rescope"                # → back_to_scope (re-frame)


RECOMMENDED_ACTIONS: Final[frozenset[str]] = frozenset({
    RecommendedAction.PROCEED,
    RecommendedAction.FIX_EXTRACTION,
    RecommendedAction.ACQUIRE_DATA,
    RecommendedAction.RESCOPE,
})

# Per-analysis-unit adequacy labels (advisory; the overall verdict is
# what the harness routes on).
class AdequacyLabel:
    ADEQUATE: Final[str] = "adequate"
    MARGINAL: Final[str] = "marginal"
    INADEQUATE: Final[str] = "inadequate"
    MISSING: Final[str] = "missing"


ADEQUACY_LABELS: Final[frozenset[str]] = frozenset({
    AdequacyLabel.ADEQUATE, AdequacyLabel.MARGINAL,
    AdequacyLabel.INADEQUATE, AdequacyLabel.MISSING,
})

# Map a recommended_action to the routing Decision the orchestrator
# follows when the feasibility gate fires. PROCEED falls through to the
# normal linear walk (next phase), so it is intentionally NOT here.
FEASIBILITY_ACTION_TO_DECISION: Final[dict[str, str]] = {
    RecommendedAction.FIX_EXTRACTION: Decision.BACK_TO_SCOPE,
    RecommendedAction.ACQUIRE_DATA: Decision.BACK_TO_PLAN,
    RecommendedAction.RESCOPE: Decision.BACK_TO_SCOPE,
}

# omicairn_config.yaml `feasibility:` overlay knobs (sibling to depth:/bounds:).
# All optional; sensible defaults keep behaviour neutral and exploratory-
# friendly. `applies_to` controls WHICH runs the gate applies to:
#   - "confirmatory": gate only pre-registered/replication runs (DEFAULT) —
#     never penalise exploratory / hypothesis-generating work (belief 13).
#   - "all": gate every run.
#   - "none": gate disabled (SCOPE still MAY emit feasibility.json, but
#     the orchestrator never routes on it).
FEASIBILITY_CONFIG_KEYS: Final[tuple[str, ...]] = (
    "applies_to",                  # "confirmatory" | "all" | "none"
    "min_fraction_of_reference",   # float — soft floor on extractable/reference N
)
FEASIBILITY_APPLIES_CONFIRMATORY: Final[str] = "confirmatory"
FEASIBILITY_APPLIES_ALL: Final[str] = "all"
FEASIBILITY_APPLIES_NONE: Final[str] = "none"
FEASIBILITY_APPLIES_CHOICES: Final[frozenset[str]] = frozenset({
    FEASIBILITY_APPLIES_CONFIRMATORY,
    FEASIBILITY_APPLIES_ALL,
    FEASIBILITY_APPLIES_NONE,
})
FEASIBILITY_DEFAULT_APPLIES_TO: Final[str] = FEASIBILITY_APPLIES_CONFIRMATORY
# Soft advisory floor surfaced to the SCOPE prompt; the model makes the
# call. 0.1 = "extractable N below ~10% of the reference N is a red flag"
# (the parkinson 52-vs-22,048 case was ~0.2%).
FEASIBILITY_DEFAULT_MIN_FRACTION: Final[float] = 0.1


# =============================================================================
# Pipeline run status
# =============================================================================

class RunStatus:
    IN_PROGRESS: Final[str] = "in_progress"
    DONE: Final[str] = "done"
    HALTED: Final[str] = "halted"


# =============================================================================
# Render-prompt token names
# =============================================================================
#
# When the orchestrator renders a phase prompt template, it substitutes
# `{{token}}` with the runtime value. These tokens MUST match exactly the
# `{{...}}` placeholders in prompts/*.md. Never type a token literal in a
# prompt file — always reference this list first.

class Token:
    TOPIC_SLUG: Final[str] = "topic_slug"
    RESEARCH_MODE: Final[str] = "research_mode"
    DOMAIN: Final[str] = "domain"
    DATA_LOCATION: Final[str] = "data_location"
    RESEARCH_QUESTION: Final[str] = "research_question"
    PRIMARY_METRIC: Final[str] = "primary_metric"
    BASELINE: Final[str] = "baseline"
    # Contribution-depth directive for this case. Rendered from the
    # omicairn_config.yaml `contribution_type:` knob (analysis | method |
    # theory) via omicairn.contribution.render_directive(), exactly as
    # COMPUTE_RESOURCES is rendered from the compute: block. The block is
    # CONDITIONAL on the type: a `method` case is told it MUST propose +
    # beat >=2 real baselines with ablations; an `analysis` case is told
    # the opposite (do not manufacture a method). Surfaced to SCOPE/PLAN/
    # EXECUTE so the deliverable's contribution shape is declared and
    # enforced. See omicairn/contribution.py.
    CONTRIBUTION_DIRECTIVE: Final[str] = "contribution_directive"
    # The bare contribution_type value (analysis | method | theory) for
    # stages that gate on the type itself rather than the full directive
    # prose — e.g. REVIEW (contribution-aware adequacy checks). Rendered
    # from the same knob.
    CONTRIBUTION_TYPE: Final[str] = "contribution_type"
    # Research-arc directive for this case. Rendered from the OPTIONAL
    # free-text `research_arc:` knob (omicairn_config.yaml, RESEARCH.md fallback)
    # via omicairn.contribution.render_arc_directive(), exactly as
    # CONTRIBUTION_DIRECTIVE is rendered from contribution_type. A present
    # hint is rendered VERBATIM as ADVISORY guidance the agent SHOULD weigh
    # (which way the argument flows between math and data); it is NOT a rule
    # and does NOT constrain the stage list. An unset arc renders a neutral
    # "let the science decide" note and forces NOTHING (back-compat). See
    # omicairn/contribution.py.
    RESEARCH_ARC_DIRECTIVE: Final[str] = "research_arc_directive"
    # The bare research_arc value (free-text hint, or "unset") for stages
    # that surface the hint itself rather than the full directive prose.
    RESEARCH_ARC: Final[str] = "research_arc"
    ALLOW_WEB: Final[str] = "allow_web"
    WORKSPACE_ROOT: Final[str] = "workspace_root"
    # Absolute path to the project-wide shared dataset cache. Phases
    # fetch on-the-way public data into ``{{shared_cache_dir}}/<accession>/``
    # so subsequent workspaces re-use the download. See
    # docs/references/research-md-schema.md § Data fetching.
    SHARED_CACHE_DIR: Final[str] = "shared_cache_dir"

    # Flat-numbered-stage tokens.
    STAGE_DIR: Final[str] = "stage_dir"
    STAGE_INDEX: Final[str] = "stage_index"
    PHASE_NAME: Final[str] = "phase_name"
    ITER: Final[str] = "iter"
    MAX_ITER: Final[str] = "max_iter"
    STAGE_LISTING: Final[str] = "stage_listing"
    RETRY_NOTE: Final[str] = "retry_note"

    # Injected knowledge / domain pack guidance.
    DOMAIN_PACK_GUIDANCE: Final[str] = "domain_pack_guidance"
    KNOWLEDGE_SUMMARY: Final[str] = "knowledge_summary"

    # Free-form user instructions, appended via `omicairn note`, scoped
    # per-phase or global. Resolves to "(none)" when no notes apply.
    USER_NOTES: Final[str] = "user_notes"

    # Active steering-step overlay (Slice 2). Resolves to the current
    # SteeringStep.prompt_overlay (when the orchestrator is running a
    # steering-routed stage) OR "(none)" otherwise. Phases that opt
    # in by adding `{{steering}}` to their template will see the
    # USER STEERING block injected; phases that don't opt in render
    # exactly as before.
    STEERING: Final[str] = "steering"

    # Pre-flight asset probe summary (rendered Markdown table). Sourced
    # from `<workspace>/asset_probe.json`. SCOPE quotes it verbatim so
    # PLAN sees which accessions / DOIs are reachable BEFORE writing
    # the pre-registration. Resolves to "(asset probe not run)" when
    # the file is absent.
    ASSET_PROBE_SUMMARY: Final[str] = "asset_probe_summary"

    # Cross-workspace shared lessons (Tier 3.3). Resolves to "(none)"
    # when paper-library has no lessons matching the current phase.
    SHARED_LESSONS_FOR_PHASE: Final[str] = "shared_lessons_for_phase"

    # Routing-suggestion advisory (Tier 3.1). Resolves to "(none)"
    # when no agent has written a routing suggestion this iteration.
    ROUTING_SUGGESTION: Final[str] = "routing_suggestion"

    # The workspace knowledge graph rendered as a projection markdown
    # block (core belief 11): supported/refuted hypotheses + evidence
    # across all goals. Resolves to "(knowledge graph is empty)" for
    # legacy / single-hypothesis cases that produced no graph_updates.json.
    KNOWLEDGE_GRAPH_PROJECTION: Final[str] = "knowledge_graph_projection"

    # IDEATE source-collection targets (hyperparameters from the case's
    # `ideate:` config: target_papers / target_blogs / target_beliefs /
    # min_directions / max_directions), rendered as a guidance block into
    # the ideate prompt so the survey hits the requested breadth.
    IDEATE_TARGETS: Final[str] = "ideate_targets"

    # OPTIONAL source-credibility-weighting policy for IDEATE (belief 13).
    # Renders guidance into the ideate prompt only when
    # `prefer_reputable_sources` is enabled in the case config; otherwise
    # a neutral one-liner. The bias is opt-in, never the default.
    IDEATE_SOURCE_POLICY: Final[str] = "ideate_source_policy"

    # Cross-workspace LAB KNOWLEDGE distilled from prior runs (supported/
    # refuted hypotheses, findings, datasets, adopted directions, each
    # URI-indexed). Rendered into IDEATE/SCOPE so a new run sees what the
    # lab has already established, refuted, and which datasets it holds —
    # runs compound instead of starting blank. "(no prior lab knowledge)"
    # when the store is empty.
    LAB_KNOWLEDGE: Final[str] = "lab_knowledge"

    # Rendered availability listing of the lab THEOREM library (omicairn.lab_theorems)
    # for the EXECUTE/theory path: the verified, Lean-compiled theorems a run can
    # RETRIEVE (via `omicairn labtheorems retrieve "<keywords>"`) and import as proof
    # scaffolding instead of cold-proving. "(no lab theorem library available)"
    # when empty/absent. Headless-robust: a static listing + a CLI seam, no MCP.
    LAB_THEOREMS: Final[str] = "lab_theorems"

    # Resolved COMPUTE-RESOURCE envelope for this case (CPU/RAM/GPU/runtime),
    # from the omicairn_config.yaml `compute:` block resolved against
    # COMPUTATION_RESOURCES.md. Rendered into EXECUTE/PLAN so the agent
    # knows whether a GPU is available and how to size full-data runs —
    # instead of assuming "no GPU locally". See compute_resources.py.
    COMPUTE_RESOURCES: Final[str] = "compute_resources"

    # SCOPE feasibility / statistical-power gate guidance. Rendered into
    # the SCOPE prompt: tells the agent whether the gate is active for
    # this case (applies_to + research_mode), the soft extractable-N
    # floor, and that it must emit feasibility.json profiling the ACTUALLY
    # extractable analysis units vs the reference effect's requirement.
    # Resolves to a neutral "gate disabled" note when the case opts out,
    # so exploratory runs aren't pushed to over-profile. See
    # docs/design-docs/feasibility-gate.md.
    FEASIBILITY_GUIDANCE: Final[str] = "feasibility_guidance"

    # Latest-of-phase pointers. Example: {{latest_plan_dir}} is the path
    # (relative to workspace) to the most recent plan stage directory.
    LATEST_SCOPE_DIR: Final[str] = "latest_scope_dir"
    LATEST_PLAN_DIR: Final[str] = "latest_plan_dir"
    LATEST_EXECUTE_DIR: Final[str] = "latest_execute_dir"
    LATEST_CROSS_CHECK_DIR: Final[str] = "latest_cross_check_dir"
    LATEST_REVIEW_DIR: Final[str] = "latest_review_dir"


# Per-phase "latest X dir" token name.
def latest_stage_token(phase_name: str) -> str:
    return f"latest_{phase_name}_dir"


# =============================================================================
# CLI choices
# =============================================================================
#
# The `--phase` flag accepts `all` plus any PIPELINE_ORDER entry. Derived
# from the canonical tuple so adding a phase updates all CLI surfaces.

PHASE_CHOICE_ALL: Final[str] = "all"
CLI_PHASE_CHOICES: Final[tuple[str, ...]] = (PHASE_CHOICE_ALL, *PIPELINE_ORDER)


# =============================================================================
# Bounds defaults
# =============================================================================

# 2026-04-30: bumped 3 → 5 after the EMT 78→80→halt and cuproptosis
# W5-disobedience halts. With retry_note now plumbed (Phase-A fix),
# successive retries actually carry actionable feedback, so a 4th
# and 5th attempt are likely to converge rather than spin.
#
# 2026-07-01 (uncap-max-iters, owner directive "by default don't put any
# limit on those agents; let agents unleash their power" — #133, SIBLING of
# the #131 stage_time_limit_mode work): raised 5 → 100. The historical small
# cap was a SCIENCE-SHAPING bound: a converged run kept re-opening a verdict
# phase toward the cap and near-halted although it was already done. That is
# the wrong lever. The REAL terminators of a healthy phase are its verdicts —
# cross_check retry_execute/proceed routing + the blocking-advance guard and
# the analysis review's done/retry routing — NOT an artificial iteration count. So in DYNAMIC mode
# (the new default; see stage_iter_mode below) this cap is demoted to a
# high, NON-BINDING pathological-non-convergence SAFETY CEILING: a healthy
# progressing run never reaches it, and the thing that actually stops an
# uncapped phase is its real verdict OR the zero-progress guard
# (ZERO_PROGRESS_STALL_LIMIT). HARD mode keeps the configured/defaulted cap
# as a deterministic terminator. The dollar budget is already unlimited
# (DEFAULT_RUN_BUDGET_USD None) and wall is bounded by its own #131 envelope,
# so a raised iteration ceiling does NOT open an unbounded-cost hole — cost
# is bounded by wall + the zero-progress spin-catcher. A case may still pin a
# small cap via bounds.max_iters_per_phase (HARD-mode determinism seekers).
DEFAULT_MAX_ITERS_PER_PHASE: Final[int] = 100
DEFAULT_MAX_BACKTRACKS: Final[int] = 5

# =============================================================================
# Wall-budget defaults (case-level; minutes)
# =============================================================================
#
# These are the DEFAULT wall ceilings a case inherits when its
# `omicairn_config.yaml` / RESEARCH.md `budgets:` block does not set them. A case
# always overrides per-case; per-phase built-in defaults (`phase.timeout_
# seconds`) and the GPU/multimodal EXECUTE wall FLOOR are resolved separately
# in the orchestrator. Hoisted out of bare literals in research_md.py (owner
# standing rule: no string-/number-literal soup — defaults go through a named
# constant, not an inline `60`/`180`).
#
# 2026-06-19 (raise-compute-envelope): bumped total 180 -> 4320 (3 days) and
# per-phase 60 -> 1440 (1 day) so a long, legitimately-detached GPU run (multi-
# VLM probing / SAE training / causal tracing on gpu-host-1) can run to
# completion against a wall instead of being SIGKILLed at the old ~3h ceiling.
# This is the WALL (Slurm `--time`-style total/per-phase budget) ONLY — it is
# INDEPENDENT of the output-inactivity watchdog (OMICAIRN_INACTIVITY_TIMEOUT_S,
# default OFF / 2700s when set), which is deliberately left untouched: a 3-day
# run still wants a ~45-min inactivity floor to catch a genuinely hung job.
# Per-phase stays generous-but-bounded (not unbounded) so a single runaway
# phase cannot silently consume the whole 3-day total.
DEFAULT_MAX_PHASE_WALL_MINUTES: Final[int] = 1440   # 1 day
DEFAULT_MAX_TOTAL_WALL_MINUTES: Final[int] = 4320   # 3 days

# =============================================================================
# Stage time-limit MODE (dynamic vs hard) + the DYNAMIC hard-safety ceiling
# =============================================================================
#
# The per-phase WALL budget above is a Slurm-`--time`-style bound. Historically
# it was an UNCONDITIONAL kill: at `elapsed > wall`, claude_runner SIGKILLs the
# stage subprocess regardless of whether the job is still making progress. That
# is the wrong primary lever for long research arms (owner directive 2026-07-01,
# grounded in the d7 example-study-compositional-steering drop: "one further VLM
# (LLaVA-NeXT-1.6-7B) passed the fit gate but its full held-out scoring exceeded
# the GPU budget and is excluded as dev-only"). A pure time number is
# simultaneously too small for the tail arm and too large as a hang backstop —
# which is exactly why OmiCairn already grew a SEPARATE output-inactivity watchdog
# that keys on PROGRESS (log/heartbeat/stage-file GROWTH), not wall-clock.
#
# Two modes govern how the per-phase wall is enforced for EXECUTE-class stages:
#
#   DYNAMIC (default): the wall becomes a LIVENESS-GATED SOFT CEILING. Once
#   elapsed exceeds the wall, the stage is killed ONLY if it is ALSO stalled
#   (the same inactivity predicate the hang-catcher already uses); a job whose
#   liveness signal is fresh is allowed to continue and a beacon is emitted.
#   The estimator (orchestrator.estimate_execute_wall_seconds) seeds an initial
#   wall so the soft-extension logging begins near a realistic expectation, but
#   an under-estimate is harmless (soft-extension covers it) and an over-estimate
#   only delays the logging — the estimate NEVER abandons an arm. The one
#   absolute bound is EXECUTE_HARD_CEILING_SECONDS below (never soft-extended).
#
#   HARD: byte-for-byte the historical behaviour — the resolved/configured wall
#   is an unconditional kill (the strict Slurm-style deterministic bound). A
#   deterministic-benchmark domain or a run that wants reproducible wall
#   semantics opts in here.
#
# The mode applies ONLY to execute-class stages; SCOPE/PLAN/CROSS_CHECK/REVIEW
# keep the hard wall in BOTH modes (a 6-hour REVIEW is a bug, not science —
# there is no long-GPU-arm to protect there).
STAGE_TIME_LIMIT_MODE_DYNAMIC: Final[str] = "dynamic"
STAGE_TIME_LIMIT_MODE_HARD: Final[str] = "hard"
STAGE_TIME_LIMIT_MODES: Final[tuple[str, ...]] = (
    STAGE_TIME_LIMIT_MODE_DYNAMIC, STAGE_TIME_LIMIT_MODE_HARD,
)
DEFAULT_STAGE_TIME_LIMIT_MODE: Final[str] = STAGE_TIME_LIMIT_MODE_DYNAMIC
ENV_STAGE_TIME_LIMIT_MODE: Final[str] = "OMICAIRN_STAGE_TIME_LIMIT_MODE"

# DYNAMIC-mode absolute backstop: the ONE bound a soft-extended execute stage
# can never cross. A run that has consumed this much wall even while touching
# files is a runaway (e.g. a stuck loop writing junk into the stage dir), not a
# legitimately-long arm, and MUST be killed. Set to the 3-day total-wall
# default: a single stage should never outlast the whole run's wall envelope.
# Expressed in SECONDS (the unit claude_runner's poll loop compares against).
EXECUTE_HARD_CEILING_SECONDS: Final[int] = DEFAULT_MAX_TOTAL_WALL_MINUTES * 60


def stage_time_limit_mode(
    research_md: "Any | None" = None,
) -> str:
    """Resolve the effective stage time-limit mode (``dynamic`` | ``hard``).

    Resolution order (first match wins), mirroring the env-override pattern of
    :func:`max_concurrent_runs` / ``lean_verify._wall_timeout_s`` — no literal
    soup, one auditable resolver:

      1. env ``OMICAIRN_STAGE_TIME_LIMIT_MODE`` (per-invocation / per-run override)
      2. ``research_md.stage_time_limit_mode`` (parsed from
         ``budgets.stage_time_limit_mode``; a domain pack that carries a budget
         default flows through this field). Absent → the parser already
         defaulted it to :data:`DEFAULT_STAGE_TIME_LIMIT_MODE`.
      3. :data:`DEFAULT_STAGE_TIME_LIMIT_MODE` (= dynamic).

    Any unrecognised value at any layer degrades to the default (dynamic) —
    a typo can never silently disable the long-arm protection AND can never
    crash the resolver. Case-insensitive; surrounding whitespace tolerated.
    """
    import os

    def _normalize(raw: "Any") -> str | None:
        if not isinstance(raw, str):
            return None
        v = raw.strip().lower()
        return v if v in STAGE_TIME_LIMIT_MODES else None

    env_mode = _normalize(os.environ.get(ENV_STAGE_TIME_LIMIT_MODE, ""))
    if env_mode is not None:
        return env_mode

    if research_md is not None:
        rmd_mode = _normalize(getattr(research_md, "stage_time_limit_mode", None))
        if rmd_mode is not None:
            return rmd_mode

    return DEFAULT_STAGE_TIME_LIMIT_MODE


# =============================================================================
# Stage ITERATION mode (dynamic vs hard) + the zero-progress spin-catcher
# =============================================================================
#
# The SIBLING axis to stage_time_limit_mode above (owner directive 2026-07-01,
# #133). That governs TIME (how a per-phase WALL is enforced); this governs
# ITERATIONS (how the per-phase iteration cap DEFAULT_MAX_ITERS_PER_PHASE is
# enforced). Both use the IDENTICAL resolver idiom (env > research_md budget
# field > default=dynamic) and both default DYNAMIC — one coherent stage-bounds
# config, two orthogonal axes, no parallel mode system.
#
#   DYNAMIC (default): the per-phase iteration cap is a NON-BINDING SAFETY
#   CEILING only (the raised DEFAULT_MAX_ITERS_PER_PHASE = 100). A healthy
#   progressing phase is terminated by its REAL stop-condition upstream
#   (cross_check proceed|retry_execute / the analysis review's done|retry),
#   NOT by the count. The ONLY thing that stops an otherwise-uncapped phase
#   short of the ceiling is the ZERO-PROGRESS guard below: an agent spinning
#   without advancing the artifact is a genuine non-convergence and is halted.
#   This is the direct dual of #131's DYNAMIC time mode — #131 allows an
#   over-wall stage to continue only while it is LIVE (touching files); #133
#   allows unbounded iterations only while EACH one makes PROGRESS (the verdict
#   fingerprint changes). Same philosophy at two timescales (within-stage wall
#   vs across-stage iteration).
#
#   HARD: byte-for-byte the historical behaviour — the resolved/configured
#   per-phase iteration cap is an unconditional refuse (the deterministic
#   terminator). A run that wants reproducible iteration semantics opts in
#   here (typically alongside a small bounds.max_iters_per_phase).
STAGE_ITER_MODE_DYNAMIC: Final[str] = "dynamic"
STAGE_ITER_MODE_HARD: Final[str] = "hard"
STAGE_ITER_MODES: Final[tuple[str, ...]] = (
    STAGE_ITER_MODE_DYNAMIC, STAGE_ITER_MODE_HARD,
)
DEFAULT_STAGE_ITER_MODE: Final[str] = STAGE_ITER_MODE_DYNAMIC
ENV_STAGE_ITER_MODE: Final[str] = "OMICAIRN_STAGE_ITER_MODE"

# Zero-progress spin-catcher: the number of CONSECUTIVE re-opening iterations
# of the SAME verdict phase that produce an IDENTICAL progress fingerprint
# (decision + proposed_fix + target_file_hints) before the phase is halted as genuine non-convergence.
# 3 = the first stalled iteration + two identical re-runs that advanced
# nothing. Kept small (an agent re-emitting the byte-identical verdict twice
# will not converge on attempt 4+), but > 1 so a single coincidental repeat
# does not trip it. This is what makes DYNAMIC's high ceiling SAFE: it is the
# iteration-axis analogue of #131's stall predicate for the time axis.
ZERO_PROGRESS_STALL_LIMIT: Final[int] = 3


def stage_iter_mode(
    research_md: "Any | None" = None,
) -> str:
    """Resolve the effective stage iteration mode (``dynamic`` | ``hard``).

    Structural clone of :func:`stage_time_limit_mode` — same resolution order
    (first match wins), same degrade-to-default normalizer, so the two
    stage-bounds axes share one auditable idiom:

      1. env ``OMICAIRN_STAGE_ITER_MODE`` (per-invocation / per-run override)
      2. ``research_md.stage_iter_mode`` (parsed from
         ``budgets.stage_iter_mode``; absent → the parser already defaulted
         it to :data:`DEFAULT_STAGE_ITER_MODE`).
      3. :data:`DEFAULT_STAGE_ITER_MODE` (= dynamic).

    Any unrecognised value at any layer degrades to the default (dynamic) —
    a typo can never silently pin a small hard cap AND can never crash the
    resolver. Case-insensitive; surrounding whitespace tolerated.
    """
    import os

    def _normalize(raw: "Any") -> str | None:
        if not isinstance(raw, str):
            return None
        v = raw.strip().lower()
        return v if v in STAGE_ITER_MODES else None

    env_mode = _normalize(os.environ.get(ENV_STAGE_ITER_MODE, ""))
    if env_mode is not None:
        return env_mode

    if research_md is not None:
        rmd_mode = _normalize(getattr(research_md, "stage_iter_mode", None))
        if rmd_mode is not None:
            return rmd_mode

    return DEFAULT_STAGE_ITER_MODE


# =============================================================================
# Per-phase LLM dollar-budget default
# =============================================================================
#
# The DEFAULT per-phase LLM spend cap a case inherits when its
# `omicairn_config.yaml` / RESEARCH.md `budgets.max_phase_budget_usd` is unset.
# This is the DOLLAR cap (`claude --max-budget-usd`), distinct from the WALL
# budgets above — it bounds *spend* per phase, not wall-clock. When None, the
# orchestrator passes max_budget_usd=None to the runner and the runner OMITS
# the `--max-budget-usd` flag entirely, so `claude -p` never self-halts on
# spend. A case may still opt into a finite numeric cap per-case (the override
# path in research_md.load is untouched).
#
# 2026-06-27 (owner directive "set cap to unlimited for ALL of our research
# runs"): default set to None = UNLIMITED. Hoisted from the bare `None`
# literal in research_md.load into this named constant (owner standing rule:
# defaults go through a named constant, not an inline literal) so the global
# default lives in one auditable place and can be re-tightened later without
# hunting through the parser.
DEFAULT_RUN_BUDGET_USD: Final[float | None] = None  # unlimited per owner 2026-06-27

# =============================================================================
# Concurrency cap — a HARD, code-enforced ceiling on concurrently-LIVE runs
# =============================================================================
#
# Historically concurrency was governed by CONVENTION only: `run_capacity` in
# compute_resources.py is documented as "a CEILING the operator/loop uses ...
# NEVER enforced in code — sharing is a permission", and the "20 concurrent"
# from a prior envelope bump was a guideline, not a gate. As the audit loop
# scaled up the fleet this proved insufficient: nothing actually stopped a
# 26th `omicairn run` from launching and oversubscribing the shared Claude API +
# the host CPUs.
#
# 2026-06-23 (owner directive): introduce a REAL launch-time cap. Before a
# `omicairn run` starts/resumes a workspace, the CLI counts currently-LIVE OmiCairn
# runs across the workspaces root (same liveness definition reap/fleet trust:
# a live `.run.lock` holder PID OR a live `omicairn run` OS process — NOT the
# stale `status: in_progress` in STATE.md) and REFUSES to launch a NEW run
# when that count is already >= this cap.
#
# This is a CEILING on CONCURRENTLY-LIVE runs across the WHOLE workspaces tree
# (a GPU-sharing run and a CPU/theory run both count) — it is NOT a per-host
# GPU-accounting bound (that stays `run_capacity`). Below the cap, behaviour is
# byte-for-byte unchanged. Resuming an ALREADY-counted live run never
# self-blocks (the target workspace is excluded from its own count). The count
# is best-effort (count-then-launch, not a mutex): a true simultaneous double
# launch can momentarily exceed the cap by the number of concurrent launches —
# acceptable for an API-bound fleet that launches serially via the audit loop.
#
# 25 is the owner-set number. Override per-invocation with
# OMICAIRN_MAX_CONCURRENT_RUNS (a positive int; <=0 or unparseable falls back to
# the default). Named constant, never a literal `25` in the enforcement path
# (owner standing rule: no number-soup).
MAX_CONCURRENT_RUNS: Final[int] = 25
ENV_MAX_CONCURRENT_RUNS: Final[str] = "OMICAIRN_MAX_CONCURRENT_RUNS"


def max_concurrent_runs() -> int:
    """The enforced concurrency cap, overridable via ``OMICAIRN_MAX_CONCURRENT_RUNS``.

    Returns the env override when it parses to a positive int, else the
    owner-set :data:`MAX_CONCURRENT_RUNS` default. Mirrors the env-override
    pattern of ``lean_verify._wall_timeout_s`` (empty/invalid/<=0 → default).
    """
    import os

    raw = os.environ.get(ENV_MAX_CONCURRENT_RUNS, "").strip()
    if not raw:
        return MAX_CONCURRENT_RUNS
    try:
        parsed = int(raw)
    except ValueError:
        return MAX_CONCURRENT_RUNS
    return parsed if parsed > 0 else MAX_CONCURRENT_RUNS


# A CONTRACT_FAIL ("the stage ran but didn't finish writing its required
# contract files") is NOT a fresh scientific iteration — it's paperwork that
# didn't land. Counting it against `max_iters_per_phase` (as it historically
# was, via record_stage_start's unconditional increment) let a string of
# write-up retries exhaust the science-iteration cap and strand a COMPLETED
# experiment in its paperwork (d7 external-vector EXECUTE, 2026-06-18: 3
# CONTRACT_FAILs burned the cap, forcing manual 14->16->17 bumps).
#
# CONTRACT_FAILs now consume a SEPARATE, small budget (`contract_retry_counts`
# in STATE.md) bounded by this constant, and are reclassified OUT of the
# science-iteration count. This is the runaway backstop: a stage that
# CONTRACT_FAILs MAX_CONTRACT_RETRIES times still halts (it never enables
# infinite retry). Kept small — if the agent can't write its contract files in
# this many attempts, more attempts won't help. The pre-existing
# IDENTICAL-failure backstop (_IDENTICAL_CONTRACT_FAIL_LIMIT) still preempts
# even faster when the SAME defect repeats.
MAX_CONTRACT_RETRIES: Final[int] = 3


# =============================================================================
# Depth / rigor SOFT targets (advisory, never hard contract failures)
# =============================================================================
#
# Overlaid from omicairn_config.yaml's `depth:` (or `rigor:`) block exactly
# like budgets/bounds (see research_md.load). Every key defaults to
# None ("no constraint") so omitting the block leaves behaviour
# unchanged. These are advisory targets surfaced to prompts/REVIEW —
# they describe how thorough a biomedical-2026 study is expected to be
# (breadth: multi-dataset/cross-cohort; depth: orthogonal analyses) —
# but they NEVER fail the execute contract. Hoisted as a tuple so the
# parser and any future consumer share one source of truth.
DEPTH_CONFIG_KEYS: Final[tuple[str, ...]] = (
    "min_datasets",            # int  — use >= this many named datasets
    "min_independent_analyses",  # int — >= this many orthogonal analyses
    "require_cross_cohort",    # bool — replicate across >=2 cohorts
    "min_figures",             # int  — target analysis-figure count
)
# Config keys older cases may still carry that this release ignores (with a
# deprecation warning) instead of rejecting.
DEPRECATED_CONFIG_KEYS: Final[tuple[str, ...]] = (
    "target_venue",
    "paper_author",
    "citation_gate",
)
# Deprecated sub-keys of the `depth:` / `rigor:` blocks.
DEPRECATED_DEPTH_KEYS: Final[tuple[str, ...]] = ("citation_target",)
# Which depth keys are booleans (parsed with bool(), not int()).
DEPTH_CONFIG_BOOL_KEYS: Final[frozenset[str]] = frozenset({"require_cross_cohort"})

# Standing analysis-rigor rules file (subsampling / breadth+depth /
# resource-request / neutrality). Lives at the OmiCairn package root under
# rules/ — NOT under prompts/ (which the prompt-lint reserves for phase
# templates). PLAN and EXECUTE point the agent at it; REVIEW scrutinises
# against it. Path is relative to the OmiCairn package root.
ANALYSIS_RIGOR_RULES_REL: Final[str] = "rules/analysis_rigor.md"


# =============================================================================
# Contribution depth (method/model depth vs analysis vs theory)
# =============================================================================
#
# A per-case knob (omicairn_config.yaml `contribution_type:`, RESEARCH.md
# fallback) declaring what KIND of contribution the study must make. It is
# threaded like `compute:` / `depth:` and surfaced to SCOPE/PLAN/EXECUTE via
# the {{contribution_directive}} token (rendered by omicairn.contribution).
#
#   - "analysis" (DEFAULT): a discovery / measurement / characterisation
#     study. Existing rules apply; the run is NOT required to invent a
#     method. Conservative default — back-compat with every existing case
#     (none declare the knob) and avoids forcing spurious "methods".
#   - "method": the study MUST propose a concrete method/model/operator,
#     IMPLEMENT it, and beat >=2 REAL competing-method baselines (e.g.
#     fine-tuning, prompting, an alternative algorithm — NOT merely
#     adversarial/sanity controls) on the pre-registered metric, with
#     ablations. A pure-analysis study is INSUFFICIENT for a method case.
#   - "theory": the study's core claims are mathematical (a derived
#     formula, a bound, a theorem). Each such claim MUST be Lean-verified
#     (see LEAN_* below) before it may be stated as proven; an unverified
#     derivation is downgraded to a labeled conjecture/empirical
#     observation.
CONTRIBUTION_TYPES: Final[tuple[str, ...]] = ("analysis", "method", "theory")
DEFAULT_CONTRIBUTION_TYPE: Final[str] = "analysis"


# =============================================================================
# Research arc — an OPTIONAL FREE-TEXT advisory hint (no enum, no taxonomy)
# =============================================================================
#
# A per-case knob (omicairn_config.yaml `research_arc:`, RESEARCH.md fallback)
# carrying a SHORT free-form methodological-intent hint — which way the
# argument flows between math and data, in the brief-author's own words. It
# is threaded like `contribution_type:` and surfaced to SCOPE/PLAN/EXECUTE
# via the {{research_arc_directive}} token (rendered by omicairn.contribution).
# It is purely ADVISORY: it shapes EMPHASIS the agent SHOULD weigh, it is
# NEVER a rule — it does not constrain the stage list (PIPELINE_ORDER is
# fixed) nor contribution_type.
#
# There is deliberately NO canonical value set. The owner found a named enum
# (e.g. theory_first | empirical_first) too stiff and too ML/method-specific:
# it reified an ML-shaped taxonomy at the schema level. Free text is
# domain-general and forces nothing. Example hints a brief might supply:
# "theory-first: derive the bound then validate on sim+real", "empirical-
# first: measure then abstract into a bound", "synthesis of N priors",
# "measure -> derive -> re-measure", or absent.
#
# Default UNSET (None): back-compat. An UNSET arc forces NOTHING — it renders
# a neutral "let the science and the brief determine the arc" note (no
# theory-first nudge for a theory case; a theory case with no arc behaves
# exactly as it did before this knob existed). A PRESENT hint is rendered
# VERBATIM, framed as advisory guidance. See omicairn/contribution.py.


# =============================================================================
# Proof status — the honest THREE-state vocabulary for a math claim
# =============================================================================
#
# Today a `theory` math claim is binary: either its Lean verdict passes
# (statable as PROVEN) or it is downgraded to a "conjecture". That forces a
# DERIVED + EMPIRICALLY-VALIDATED result whose machine-checked Lean proof is
# merely deferred (a PARALLEL obligation) into the dishonest extreme of
# "conjecture", understating real work. ProofStatus adds the honest middle
# state so a result can carry the label it has actually earned:
#
#   - PROVEN_LEAN: a Lean verdict for the claim PASSES (`compiled: true`,
#     sorry-free, substantive). ONLY this status may be reported as "proven /
#     theorem (Lean-verified)". (Today's lone proven path.)
#   - DERIVED_VALIDATED_PROOF_PENDING: the claim is (i) formally DERIVED — a
#     written derivation / proof sketch exists — AND (ii) empirically
#     VALIDATED — its prediction holds on data with CIs — but its machine-
#     checked Lean proof is a PARALLEL obligation not yet closed. A theory
#     case MAY finish carrying such a claim PROVIDED it is labeled
#     proof-pending in results.json / analysis.md (NOT reported as "proven").
#   - CONJECTURE: neither proven nor (derivation + empirical validation). A
#     bare derivation with no empirical backing, or an unvalidated guess.
#     Stated only as a labeled conjecture / empirical observation.
#
# This is MORE honest states, NOT a loophole: a proof-pending claim reported
# as proven is an OVER-CLAIM. See omicairn/contribution.py +
# rules/method_depth.md.
class ProofStatus:
    """The three honest states a theory math claim may carry. The label
    decides how the claim may be reported."""
    PROVEN_LEAN: Final[str] = "proven_lean"
    DERIVED_VALIDATED_PROOF_PENDING: Final[str] = "derived_validated_proof_pending"
    CONJECTURE: Final[str] = "conjecture"


# Per-stage proof-obligation record (JSON; sibling of results.json /
# preregistration.json). EXECUTE/THEORY emits one entry per math claim:
# claim id + statement, the `.lean` file attempted (if any), the verdict
# status, the empirical-validation pointer (a results.json key), and the
# resulting proof_status. The REVIEW prompt reads it to verify the
# reported results HONOR each claim's earned label. OPTIONAL: absent for a
# legacy/non-theory run (back-compat). See prompts/execute.md.
PROOF_OBLIGATIONS_FILE: Final[str] = "proof_obligations.json"


# Standing method-depth + math-rigor rules file (parallel to
# ANALYSIS_RIGOR_RULES_REL). Houses the detailed contribution-type
# protocol + the Lean-verified-math protocol so the prompt edits stay
# surgical. PLAN/EXECUTE point the agent at it; REVIEW scrutinises against
# it. Path relative to the OmiCairn package root.
METHOD_DEPTH_RULES_REL: Final[str] = "rules/method_depth.md"


# =============================================================================
# Lean-verified math
# =============================================================================
#
# A OmiCairn run that makes a MATHEMATICAL claim (derived formula, bound,
# theorem, identity, closed-form) MUST formalize it in Lean 4 and the
# proof MUST check before the claim is stated as established. See
# omicairn/lean_verify.py + the `math-formalization` skill. These constants
# name the toolchain binaries, the per-proof wall timeout, and the
# in-workspace artifact layout.
LEAN_BIN: Final[str] = "lean"
LAKE_BIN: Final[str] = "lake"
# Per-.lean wall timeout (seconds). A single Mathlib-free lemma checks in
# well under a second; a Mathlib-backed proof can take longer on first
# build (oleans cached after `lake exe cache get`). Generous so it never
# fires on a legitimate proof, tight enough to recover a hung `lean`.
# Override via env var (parsed in lean_verify._wall_timeout_s).
LEAN_DEFAULT_TIMEOUT_S: Final[int] = 300
ENV_LEAN_TIMEOUT: Final[str] = "OMICAIRN_LEAN_TIMEOUT_S"
# Optional explicit Lean/Lake binary dir (e.g. ~/.elan/bin). When unset,
# lean_verify falls back to PATH then the conventional ~/.elan/bin.
ENV_LEAN_BIN_DIR: Final[str] = "OMICAIRN_LEAN_BIN_DIR"
# In-workspace artifact layout: a run writes `<stage>/lean/<Claim>.lean`
# and the verifier verdict to `<stage>/lean/<Claim>.verdict.json`.
LEAN_STAGE_SUBDIR: Final[str] = "lean"
LEAN_VERDICT_SUFFIX: Final[str] = ".verdict.json"

# --- Lean COMPUTE PLACEMENT (CPU-class workload; forward hook for GPU) -------
#
# Proof CHECKING is a CPU-class workload: `lean`/`lake env lean` use the CPU
# only, never a GPU. We make that placement EXPLICIT (not merely a docstring)
# via the canonical `compute_class` notion below, so (a) the verdict records
# the class it ran under, and (b) there is ONE clean extension point for a
# hypothetical FUTURE Lean step that needs special compute (a heavy ML-guided
# tactic / proof-search). The class is a property of the WORKLOAD; it is
# distinct from a compute RESOURCE's `kind` field (compute_resources.py), which
# describes a HOST. Today every Lean proof is `cpu` → unchanged local behaviour.
#
# These string literals are canonical (design rule "no string-soup"): the
# lean_verify dispatch and any future router key on these, never a bare "cpu".
COMPUTE_CLASS_CPU: Final[str] = "cpu"
COMPUTE_CLASS_GPU: Final[str] = "gpu"
# The default class for a Lean proof-check. Default = cpu = exactly today's
# behaviour (run on the local CPU toolchain). A future Lean step that genuinely
# needs a GPU would carry COMPUTE_CLASS_GPU so the dispatch routes it like a
# GPU job (via compute_resources) instead of assuming local-CPU.
LEAN_DEFAULT_COMPUTE_CLASS: Final[str] = COMPUTE_CLASS_CPU

# Lean is CPU-bound; on a host SHARED with a co-located GPU run (or any other
# CPU work) a heavy Mathlib proof must not grab every core. `lean` honours the
# LEAN_NUM_THREADS env var to cap its internal parallelism. OmiCairn sets a bounded
# default (a small fraction of the host's logical cores, clamped) so a big proof
# stays a polite neighbour; today's Mathlib-free lemmas finish sub-second
# regardless, so this is invisible for them. Override with OMICAIRN_LEAN_NUM_THREADS
# (0/unset = use the computed default; an explicit positive int pins exactly).
# A value of <=0 after parsing falls back to the computed default.
ENV_LEAN_NUM_THREADS: Final[str] = "OMICAIRN_LEAN_NUM_THREADS"
# The env var `lean` itself reads for its worker-thread count.
LEAN_NATIVE_NUM_THREADS_ENV: Final[str] = "LEAN_NUM_THREADS"
# Default thread cap when neither OMICAIRN_LEAN_NUM_THREADS nor LEAN_NUM_THREADS is
# set: max(1, floor(logical_cores / LEAN_THREAD_DIVISOR)), clamped to
# LEAN_MAX_DEFAULT_THREADS. Leaves headroom for a co-located run on a shared box.
LEAN_THREAD_DIVISOR: Final[int] = 2
LEAN_MAX_DEFAULT_THREADS: Final[int] = 8

# Reusable Mathlib-backed Lake project dir name (lives at the repo/project
# root). The OmiCairn `LeanScaffold/` is deliberately Mathlib-free; a proof that
# genuinely needs Mathlib (`ring`/`nlinarith`/`Real`/…) verifies inside this
# SEPARATE, set-up-once project (see omicairn-lean-mathlib-local-setup memory note
# + rules/method_depth.md M6).
#
# The DOT prefix is intentional and load-bearing: it keeps the bulky Mathlib
# checkout (.lake/, ~8100 oleans) hidden from `ls` / fuzzy-add tooling at the
# project root. It is ALSO a safety property — a DASH-prefixed name
# (`-lean-mathlib-omicairn`) is parsed as an option flag by shell/`lake`/`lean`
# and by argparse, which is how a stray `-lean-mathlib-omicairn/` dir was once
# created from a hand-typed `lake new -lean-mathlib-omicairn math`. This literal
# is canonical: build the path via lean_verify.mathlib_project_dir(), which
# asserts the dot prefix and rejects any dash-leading name, so the stray dir
# can never be re-created by OmiCairn code.
LEAN_MATHLIB_PROJECT_DIRNAME: Final[str] = ".lean-mathlib-omicairn"


# =============================================================================
# Dry-run environment variable
# =============================================================================
#
# Setting `OMICAIRN_DRY_RUN=1` causes claude_runner to stub out the LLM call
# with placeholder files, letting us test orchestrator routing without
# spending tokens. Setting `OMICAIRN_DRY_VERDICT=<decision>` forces a specific
# verdict from the next verdict-emitting phase — useful for testing
# backtracking without staging real failures.

ENV_DRY_RUN: Final[str] = "OMICAIRN_DRY_RUN"
ENV_DRY_VERDICT: Final[str] = "OMICAIRN_DRY_VERDICT"
ENV_DATA_DIR: Final[str] = "OMICAIRN_DATA_DIR"

# Opt-IN for the literature-download SSRF guard (scheme allowlist +
# private/loopback/metadata-IP block in literature._download_file). The
# guard is OFF BY DEFAULT (operator decision, 2026-06-03); set this to a
# truthy value to ENABLE it. Turn it on for untrusted / cloud
# deployments where a hostile (e.g. IDEATE-surveyed) URL could point the
# server-side fetch at an internal/metadata endpoint (169.254.169.254,
# localhost, private ranges). See literature._ssrf_guard_enabled.
ENV_ENABLE_SSRF_GUARD: Final[str] = "OMICAIRN_ENABLE_SSRF_GUARD"

# Cross-workspace LAB KNOWLEDGE BASE. Distills each finished workspace's
# knowledge graph (supported/refuted hypotheses, findings, datasets,
# adopted directions) into an append-only, URI-indexed store so runs
# compound across the lab (core belief 11, cross-workspace). See
# omicairn.lab_knowledge.
#
# Location resolution (omicairn.lab_knowledge.resolve_root), highest first:
#   1. an explicit path (CLI `--root`)
#   2. the OMICAIRN_LAB_KB env var
#   3. `<workspaces_root>/lab_knowledge/` — the default: a VISIBLE folder
#      beside the run workspaces (created on demand during a run). No more
#      hidden `~/.cache`.
ENV_LAB_KB: Final[str] = "OMICAIRN_LAB_KB"
LAB_KB_DIRNAME: Final[str] = "lab_knowledge"   # folder name for the store
LAB_KB_FILE: Final[str] = "lab_knowledge.jsonl"
LAB_KB_INDEX_FILE: Final[str] = "LAB_KNOWLEDGE_INDEX.md"
LAB_KB_LOCK: Final[str] = ".lab_kb.lock"

# =============================================================================
# LAB THEOREM LIBRARY (the "virtual mathematician" substrate)
# =============================================================================
#
# A lab-level, Lean-VALIDATED theorem library so OmiCairn theory phases are not
# limited to the base LLM's pretrained math: every entry carries a Lean
# source whose `compiled`/`sorry-free` verdict was produced by the LOCAL
# toolchain (omicairn.lean_verify), plus its prerequisite dependency edges,
# curriculum level, source citation, and tactics used. Mirrors the lab_KB
# storage discipline: a single append-only JSONL op-log, folded on read,
# with an atomic-create lockfile. See omicairn.lab_theorems.
#
# NB: the subsystem is named `lab_theorems` (NOT "mathlib") so it parallels
# lab_knowledge / lab_library and does NOT collide with Lean's own Mathlib
# dependency (the `.lean-mathlib-omicairn` Lake project).
#
# Location resolution (omicairn.lab_theorems.resolve_root), highest precedence first:
#   1. an explicit path (CLI `--root`)
#   2. the OMICAIRN_LAB_THEOREMS env var
#   3. `<repo>/lab_theorems/store/` — the default tracked location that ships
#      the seeded library, resolved relative to the omicairn package.
ENV_LAB_THEOREMS: Final[str] = "OMICAIRN_LAB_THEOREMS"
LAB_THEOREMS_DIRNAME: Final[str] = "store"        # folder name under lab_theorems/
LAB_THEOREMS_FILE: Final[str] = "theorems.jsonl"   # append-only op-log
LAB_THEOREMS_INDEX_FILE: Final[str] = "LAB_THEOREMS_INDEX.md"
LAB_THEOREMS_LOCK: Final[str] = ".lab_theorems.lock"
# Top-level area name (beside the package): lab_theorems/{lean,store}
LAB_THEOREMS_AREA: Final[str] = "lab_theorems"
LAB_THEOREMS_LEAN_SUBDIR: Final[str] = "lean"      # the .lean sources live here


class CurriculumLevel:
    """Ordered curriculum bands for the math library (elementary → PhD),
    mirroring the owner's curriculum-learning request. The ORDINAL is what
    the retriever / scheduler reads; the label is for display. Domain-
    agnostic (a level is a difficulty band, not a field)."""
    ELEMENTARY: Final[str] = "elementary"
    JUNIOR_HIGH: Final[str] = "junior_high"
    HIGH_SCHOOL: Final[str] = "high_school"
    UNDERGRAD: Final[str] = "undergrad"
    GRADUATE: Final[str] = "graduate"
    PHD: Final[str] = "phd"


# Ascending difficulty — index = ordinal. A theorem may only depend on
# prerequisites at the same or a LOWER band (a curriculum-monotonicity
# invariant the library validator can check).
CURRICULUM_ORDER: Final[tuple[str, ...]] = (
    CurriculumLevel.ELEMENTARY,
    CurriculumLevel.JUNIOR_HIGH,
    CurriculumLevel.HIGH_SCHOOL,
    CurriculumLevel.UNDERGRAD,
    CurriculumLevel.GRADUATE,
    CurriculumLevel.PHD,
)


class TheoremStatus:
    """Validation lifecycle of a math-library entry. `verified` = Lean
    compiled sorry-free on the local toolchain AND the proof carries real
    mathematical content (passed the trivia substance check);
    `verified_trivial` = compiled sorry-free but the proof is a content-free
    tautology closer (`trivial`/`decide`/`native_decide` with no named-lemma
    derivation) — recorded with a substance warning, NOT a first-class library
    theorem (the same proof-theater the M3b/W18 gates guard against);
    `unverified` = entered but not yet (or not) compiled clean (honest
    placeholder); `refuted` = the stated proof failed to compile / was found
    unsound."""
    VERIFIED: Final[str] = "verified"
    VERIFIED_TRIVIAL: Final[str] = "verified_trivial"
    UNVERIFIED: Final[str] = "unverified"
    REFUTED: Final[str] = "refuted"


# Theorem-library knowledge-graph relation: theorem A `depends_on` theorem/lemma
# B (B is a prerequisite of A). A canonical EDGE relation for the prereq DAG,
# kept here (not in EdgeRel) so the theorem graph stays self-contained.
THEOREM_EDGE_DEPENDS_ON: Final[str] = "depends_on"
# theorem-graph node type for a library theorem (self-contained namespace)
THEOREM_NODE: Final[str] = "theorem"

# Opt-IN for the SERENDIPITY-PROMOTION loop (creativity audit F1). OFF BY
# DEFAULT (conservative — this is goal-1 core-adjacent machinery): when unset
# or falsy, the post-run promotion step is a strict no-op and no live run
# changes behaviour. Set to a truthy value to enable promoting a supported,
# surprising/cheap-to-test on-the-fly hypothesis into a PROPOSED (un-adopted)
# lab-KB direction. Mirrors the opt-in pattern of ENV_ENABLE_SSRF_GUARD.
# See lab_knowledge.promote_serendipitous_directions + orchestrator wiring.
ENV_ENABLE_SERENDIPITY: Final[str] = "OMICAIRN_ENABLE_SERENDIPITY"
# Marker stamped on the `origin` field of a lab-KB direction entry that was
# created by the serendipity-promotion loop (vs a hand-/IDEATE-authored
# direction). Lets the cron / a human / `omicairn ideate` tell a promoted
# discovery apart from a brainstormed one. Lab-KB entries are plain dicts,
# so this is a free string constant — NOT a NodeType / HypothesisOrigin
# extension (those are graph-node taxonomies).
SERENDIPITY_DIRECTION_ORIGIN: Final[str] = "serendipity"

# Cross-run HUMAN FEEDBACK store. Every explicit human steering message /
# note / correction / approval / rejection / rating is recorded here as a
# TYPED record LINKED to the run + stage + artifact the human reacted to —
# durable annotation that survives prompt consumption and aggregates across
# runs (the steering_plan/steering_log are per-workspace and ephemeral). It
# mirrors the lab_knowledge store: a VISIBLE folder beside the run workspaces,
# an append-only JSONL op-log folded on read, and a PID-tagged lockfile.
# See omicairn.human_feedback.
#
# Location resolution (omicairn.human_feedback.resolve_root), highest first:
#   1. an explicit path (CLI `--root`)
#   2. the OMICAIRN_HUMAN_FEEDBACK env var
#   3. `<workspaces_root>/human_feedback/` — the default: a VISIBLE folder
#      beside the run workspaces (created on demand during a run).
#   4. `<cwd>/workspaces/human_feedback/` — CLI fallback (no ws context).
ENV_HUMAN_FEEDBACK: Final[str] = "OMICAIRN_HUMAN_FEEDBACK"
HUMAN_FEEDBACK_DIRNAME: Final[str] = "human_feedback"  # folder name
HUMAN_FEEDBACK_FILE: Final[str] = "feedback.jsonl"
HUMAN_FEEDBACK_INDEX_FILE: Final[str] = "HUMAN_FEEDBACK_INDEX.md"
HUMAN_FEEDBACK_LOCK: Final[str] = ".human_feedback.lock"
HUMAN_FEEDBACK_SCHEMA_VERSION: Final[int] = 1

# PROJECT DESCRIPTOR PROTOCOL — the portable, machine-readable manifest that
# makes any project folder (manual / other-agent / other-OmiCairn / collaborator)
# interoperable with OmiCairn. `project.json` is the canonical source of truth
# (JSON, never markdown-parsed); `PROJECT.md` is a one-way generated human
# view. Deep knowledge stays in `knowledge/graph.jsonl`. See
# omicairn.project_descriptor.
PROJECT_DESCRIPTOR_FILE: Final[str] = "project.json"
PROJECT_MD_FILE: Final[str] = "PROJECT.md"
PROJECT_SCHEMA_VERSION: Final[str] = "1.0"

# Shared LAB LITERATURE LIBRARY root (override). The cross-workspace
# store of paper metadata + PDFs + converted markdown full-text (the
# literature analogue of the lab knowledge base). $OMICAIRN_PAPER_LIB
# overrides the resolved default; lets CI, tests, and multi-user setups
# point at an alternate path without code changes.
#
# Location resolution (omicairn.paper_library.resolve_root), highest first:
#   1. an explicit path (CLI `--root`)
#   2. the OMICAIRN_PAPER_LIB env var
#   3. `<workspaces_root>/lab_library/` — the default: a VISIBLE folder
#      beside the run workspaces (created on demand during a run). No more
#      hidden `~/.cache`.
#   4. `<cwd>/workspaces/lab_library/` — CLI fallback (no ws context).
ENV_PAPER_LIB: Final[str] = "OMICAIRN_PAPER_LIB"

_DRY_RUN_FALSY: Final[frozenset[str]] = frozenset({"", "0", "false", "False"})


def dry_run_enabled() -> bool:
    """True when OMICAIRN_DRY_RUN is set to a truthy value."""
    import os
    return os.environ.get(ENV_DRY_RUN, "").strip() not in _DRY_RUN_FALSY


def serendipity_enabled() -> bool:
    """True when OMICAIRN_ENABLE_SERENDIPITY is set to a truthy value.

    Default-OFF: the serendipity-promotion loop is a no-op until explicitly
    enabled (creativity audit F1, conservative goal-1 hardening). Reuses the
    same falsy set as dry_run_enabled so "", "0", "false", "False" all read
    as disabled.
    """
    import os
    return os.environ.get(ENV_ENABLE_SERENDIPITY, "").strip() not in _DRY_RUN_FALSY


# =============================================================================
# Domain packs
# =============================================================================
#
# Domain packs live at `engine/domain_packs/<name>/` and are
# selected at runtime via `research_md.domain`. The `_generic` fallback
# is used when no pack matches.

DOMAIN_PACKS_DIR: Final[str] = "domain_packs"
DOMAIN_PACK_GENERIC: Final[str] = "_generic"
DOMAIN_PACK_PROFILE_FILE: Final[str] = "profile.yaml"
DOMAIN_PACK_LESSONS_DIR: Final[str] = "lessons"


def domain_pack_phase_file(phase_name: str) -> str:
    """Return the per-phase guidance file for a domain pack.

    Example: phase_execute.md, phase_review.md.
    """
    return f"phase_{phase_name}.md"


# =============================================================================
# Shared paper library
# =============================================================================
#
# `omicairn.paper_library` persists downloaded papers across workspaces so
# that overlapping literature queries don't re-download the same PDFs.
# The library root is a flat directory: a manifest JSON + de-duplicated
# PDFs + abstracts + converted markdown full-text + a provenance log.

PAPER_LIB_MANIFEST: Final[str] = "papers.json"
PAPER_LIB_PROVENANCE: Final[str] = "provenance.jsonl"
PAPER_LIB_PDFS_DIR: Final[str] = "pdfs"
PAPER_LIB_ABSTRACTS_DIR: Final[str] = "abstracts"
# Converted PDF→markdown full-text, content-addressed by slug
# (`markdown/<slug>.md`). Generated once when a PDF is contributed and
# shared across runs so the costly conversion happens only once.
PAPER_LIB_MARKDOWN_DIR: Final[str] = "markdown"
PAPER_LIB_LOCK: Final[str] = ".lock"
# Folder name for the visible default library location (see ENV_PAPER_LIB
# resolution note above). Mirrors LAB_KB_DIRNAME.
PAPER_LIB_DIRNAME: Final[str] = "lab_library"


# =============================================================================
# results.json schema field names (the cross_check W-gates read these)
# =============================================================================
#
# Canonical top-level field names of an EXECUTE stage's `results.json` — the
# contract cross_check's numeric/provenance/headline gates parse. This is the
# ONE canonical home; cross_check's per-gate `_W13_COMPARISONS_KEY` alias
# points back here so the literal is never re-typed (GP7: no string-literal
# soup across gate + test + message). NOTE: preregistration.json's `baselines`
# block (W14, a PLAN-time field read from a DIFFERENT file) is intentionally
# NOT folded here — it shares the spelling but is not a results.json field.
class ResultsField:
    """Canonical top-level field names of an EXECUTE stage's results.json.

    The contract the cross_check numeric/provenance/headline gates parse. Kept
    here (not as per-gate module literals) so the gate, its message, and its
    test all read the SAME name from one source of truth.
    """
    PRIMARY_MODEL: Final[str] = "primary_model"
    BASELINES: Final[str] = "baselines"
    COMPARISONS: Final[str] = "comparisons"
    # Legacy pre-canonical shape, normalised in memory into PRIMARY_MODEL +
    # BASELINES; surfaced as a schema-check WARN to nudge emitters forward.
    CONDITIONS: Final[str] = "conditions"


# =============================================================================
# Confirmatory-research pre-registration fields (W13/W17/W20 + W21)
# =============================================================================
#
# The three pre-registration declarations a CONFIRMATORY run is expected to
# carry in `<plan>/preregistration.json`. Each is the field one of the
# back-compatible W-gates reads and SILENTLY no-ops on when absent:
#
#   - LOAD_BEARING_ARM      → W13 (headline arm complete) / W15 / W16
#   - DISCRIMINATING_ARM    → W17 (the deciding experiment ran before the
#                             central question is framed RESOLVED)
#   - DECISION_RULE_STRUCTURED → W20 (the machine-checkable twin of each
#                             hypothesis' prose decision_rule; lives PER
#                             HYPOTHESIS inside `hypotheses[]`, not top-level)
#
# Because each gate no-ops when its field is absent, a plan that simply
# OMITS them passes the whole suite VACUOUSLY — the structural root cause of
# runs deferring their decisive experiment as "future work" (audit finding
# S1). W21 (the prereg-completeness gate) surfaces that vacuous pass as a
# WARN, without changing W13/W17/W20 or their silent-no-op back-compat.
#
# These are the ONE canonical home for the field names — cross_check's
# per-gate `_W13_LOAD_BEARING_ARM_FIELD` etc. alias back here so the literals
# are never re-typed (GP7: no string-literal soup across gate + test +
# message).
class PreregField:
    """Canonical pre-registration field names the confirmatory W-gates read.

    `ALL` is the completeness set W21 checks for presence — declared in the
    fixed gate order (W13, W17, W20) so the missing-field message reads
    in the same order an operator scans the gate column.
    """
    LOAD_BEARING_ARM: Final[str] = "load_bearing_arm"
    DISCRIMINATING_ARM: Final[str] = "discriminating_arm"
    DECISION_RULE_STRUCTURED: Final[str] = "decision_rule_structured"
    # The hypotheses container DECISION_RULE_STRUCTURED is nested under, and
    # the per-hypothesis key it appears as. W21 looks for the structured rule
    # on ≥1 hypothesis rather than as a top-level prereg field.
    HYPOTHESES: Final[str] = "hypotheses"

    # The declarations a confirmatory plan should carry, in gate order.
    ALL: Final[tuple[str, ...]] = (
        LOAD_BEARING_ARM,
        DISCRIMINATING_ARM,
        DECISION_RULE_STRUCTURED,
    )


# W21 — prereg-completeness gate. ADVISORY (WARN-only) surfacing layer over
# W13/W17/W20: fires for CONFIRMATORY runs (a plan that emitted a
# preregistration.json — the exact signal W13/W17/W20 gate themselves on)
# whose plan declares fewer than all PreregField.ALL fields, naming
# which are missing. PASS when all are present; never fires on exploratory/
# descriptive runs (no preregistration.json → no confirmatory signal).
class PreregCompletenessGate:
    GATE_ID: Final[str] = "W21"
    SEVERITY: Final[str] = "warn"


# W15L — latest-execute-stage provenance gate. ADVISORY (WARN-only) sibling of
# W15. W13 verifies the load-bearing arm is PRESENT in the LATEST execute stage;
# W15 verifies its number is provenance-clean. W15L closes the cross-stage
# provenance fracture W13 misses on a multi-execute run: the d5 run reported a
# headline (n=48 set-level CKA=0.839) that lived only in an EARLY execute stage
# (026) while the LATEST execute stage (032) dropped the arm — the headline
# traced to results.json, but a NON-LATEST one. W15L fires when a named
# load_bearing_arm is ABSENT from the latest execute stage's results.json yet
# PRESENT in an earlier execute sibling's — surfacing that the headline's
# provenance has fractured to a stale stage. WARN-only (shipped WARN-first like
# W21): names the fractured arm + the stale stage, never blocks. No-op on
# single-execute runs, when no load_bearing_arm is declared, or when the arm is
# present in the latest stage.
class LatestExecuteProvenanceGate:
    GATE_ID: Final[str] = "W15L"
    SEVERITY: Final[str] = "warn"


# W23 — decisive-arm zero-power gate. ADVISORY (WARN-only) sibling of W17. W17
# verifies the pre-registered DISCRIMINATING arm RAN before the central
# question may be framed RESOLVED; W23 verifies that arm — when it did run —
# actually had statistical POWER to discriminate, i.e. its decisive input was
# not structurally degenerate. The motivating defect (posttrain-regime-predicts-
# erasure, 2026-06): the plan pre-registered a leave-one-model-out coverage
# predictor, but the coverage NUMERATOR collapsed to a CONSTANT (`c == 1` across
# all 10 models), so the predictor had power == 0 at EVERY effect size — the
# decisive test could not fire — yet nothing in cross_check flagged it and the
# result had to be re-framed by hand as a measurement-design null. W23 detects the
# DEGENERATE-PREDICTOR signature on the named discriminating arm and WARNs that
# the decisive test could not fire (zero power), so the headline must be framed
# as a measurement-design negative, not a supported/again-null scientific result.
#
# The three conservative signatures W23 keys on (any ONE, on the NAMED arm only):
#   (1) CONSTANT-INPUT — a panel/per-unit list whose decisive numerator field
#       (the `POWER_INPUT_FIELDS` names, e.g. coverage `c`) takes the SAME
#       value across all comparison units (zero variance over >= MIN_UNITS).
#   (2) ZERO-POWER — an explicitly-reported power field for the arm reads 0:
#       any `POWER_FIELDS` scalar == 0, OR a `power_curve` whose every entry
#       has power == 0, OR a `MDE_AT_POWER_FIELD` that is null (no effect
#       size reaches the power target).
#   (3) FULL-RANGE-CI — the arm's bootstrap `ci_95` spans the ENTIRE admissible
#       range of a bounded-metric class (e.g. [0.0, 1.0] for a 0_1 metric,
#       [-1.0, 1.0] for a correlation) — no discrimination possible.
#
# WARN-ONLY (shipped WARN-first like W21/W15L): appends to `warnings` only, no
# errors sink, never flips ok / routes back. Fail-safe no-op when: no discriminating
# arm is declared, the arm is absent (W17's job), the metric genuinely varied /
# has nonzero power, or the signature is ambiguous. A legitimate honest-null where
# the arm FIRED with power and returned a null effect must NOT trip it — the
# distinction W23 draws is "the test had no power to detect an effect" vs "the
# test ran with power and found none".
class DecisiveArmPowerGate:
    GATE_ID: Final[str] = "W23"
    SEVERITY: Final[str] = "warn"

    # Per-unit list fields (panel of comparison units) whose decisive INPUT
    # collapsing to a single value across units kills power (signature 1).
    POWER_INPUT_FIELDS: Final[tuple[str, ...]] = (
        "c",            # coverage numerator (the regime defect: c == 1 everywhere)
        "numerator",
        "n_above_null",
        "n_correct",
        "n_hits",
    )
    # Container fields holding the per-unit list of comparison units.
    PANEL_FIELDS: Final[tuple[str, ...]] = ("panel", "per_model", "units", "rows")
    # Explicit power / effective-sample scalar fields that read 0 when the arm
    # could not discriminate (signature 2).
    POWER_FIELDS: Final[tuple[str, ...]] = (
        "power",
        "power_to_detect_perfect_boundary",
        "effective_sample",
        "n_above_null",
        "effective_B",
    )
    # The arm/results power-analysis block + its sub-structures.
    POWER_BLOCK_FIELDS: Final[tuple[str, ...]] = ("power_analysis", "power")
    REALISED_BALANCE_FIELD: Final[str] = "realised_balance"
    POWER_CURVE_FIELD: Final[str] = "power_curve"
    POWER_CURVE_POWER_KEY: Final[str] = "power"
    # `null` here means "no effect size reaches the power target" → zero power.
    MDE_AT_POWER_FIELD: Final[str] = "mde_sep_at_power_target"
    # Minimum comparison units before the constant-input signature is trusted
    # (a 1-2-unit list can be constant by chance / by design; require a real panel).
    MIN_UNITS: Final[int] = 3


# W24 — construct-validity gate. ADVISORY (WARN-only) sibling of W20/W23/W25
# (audit proposal P3). Many headlines rest on a LEARNED / SURROGATE axis (an
# LLM-judge stand-in for a human construct, a learned predictor used in place
# of a gold label) whose meaning is only as good as a pre-registered
# construct-validity check — agreement with a human/gold anchor. The motivating
# defect (sensitive-memory-retrieval, 2026-06): the appropriateness axis the
# whole frontier is built on was validated against human ConfAIde ratings at
# Spearman rho=0.276, 95% CI [-0.119, 0.594] — BELOW the pre-registered
# rho>=0.6 AND a CI spanning the null (0.0) — i.e. the validity check FAILED,
# yet the construct was left LOAD-BEARING and the run still got verdict
# `proceed`. No gate flagged that the headline rests on an UNVALIDATED
# construct. W20 checks a hypothesis DECISION rule was honored; W23 checks the
# decisive arm had POWER; neither checks a load-bearing construct's VALIDITY.
# W24 closes that.
#
# Schema (OPTIONAL, mirrors W20's `decision_rule_structured` twin — PLAN emits
# a machine-checkable validity declaration alongside the prose H4/validity-gate
# rule; NO prose parsing, GP8). A `construct_validity` object, on a prereg
# hypothesis (`hypotheses[i].construct_validity`) and/or top-level prereg:
#
#   "construct_validity": {
#       "metric": "h4_judge_human_agreement.spearman_rho",  # dotted path into
#                                                            # results.json
#       "min_rho": 0.6,           # threshold the validity stat must CLEAR
#                                 # (aliases: min_stat / threshold — same home)
#       "load_bearing": true      # OPTIONAL; default true — the axis the
#                                 # headline rests on. false → W24 no-ops.
#   }
#
# FIRE (escalate a WARN) iff ALL hold: (1) a construct_validity object declares
# a `metric` dotted path + a numeric threshold; (2) the axis is LOAD-BEARING
# (`load_bearing` is absent/true — a declared construct-validity requirement is
# load-bearing by default; an explicit `load_bearing: false` no-ops); (3) the
# MEASURED stat at `metric` FAILS — point estimate strictly below the
# threshold, OR (when the metric resolves under a bounded-correlation class)
# its `ci_95` sibling SPANS the null (0.0 ∈ [lo, hi]). The WARN says the
# headline rests on a learned construct whose pre-registered validity check
# FAILED and must be framed as preliminary / pending-validation, not as
# established.
#
# WARN-ONLY (shipped WARN-first like W21/W15L/W23/W25): appends to `warnings`
# only, no errors sink, never flips ok / routes back. Fail-safe NO-OP (false-
# positive guard) when: no construct_validity object declared (every existing /
# in-flight run → silent pass, back-compatible like W13/W20-structured at first
# ship); the object is malformed (no metric / no numeric threshold); the axis
# is explicitly `load_bearing: false`; the measured stat is unresolvable; or the
# validity stat PASSES (point >= threshold AND, for a correlation, its CI
# excludes 0.0). A genuine PASSING validation must NOT trip it.
class ConstructValidityGate:
    GATE_ID: Final[str] = "W24"
    SEVERITY: Final[str] = "warn"

    # The OPTIONAL structured validity declaration object (sibling of W20's
    # `decision_rule_structured`). PLAN emits this alongside the prose H4 /
    # validity-gate `decision_rule` so W24 can mechanically check it (no prose
    # parsing). Read off a prereg hypothesis AND/OR the top-level prereg dict.
    CONSTRUCT_VALIDITY_KEY: Final[str] = "construct_validity"
    # Fields inside a construct_validity object.
    METRIC_KEY: Final[str] = "metric"  # dotted path into results.json
    # Threshold the validity stat must CLEAR. `min_rho` is canonical (the
    # motivating ConfAIde rho>=0.6 rule); `min_stat`/`threshold` are accepted
    # aliases so a non-correlation validity stat (e.g. a Krippendorff alpha)
    # can declare the same requirement without a parallel key. First present,
    # in this order, wins.
    THRESHOLD_KEYS: Final[tuple[str, ...]] = ("min_rho", "min_stat", "threshold")
    # OPTIONAL load-bearing flag. Absent → True (a declared construct-validity
    # requirement is load-bearing by default; the axis the headline rests on).
    LOAD_BEARING_KEY: Final[str] = "load_bearing"
    # The CI-sibling key (same as W20's) — when the validity metric is a
    # bounded correlation, a `ci_95` that SPANS 0.0 is a validity FAIL even if
    # the point estimate happens to clear the threshold.
    CI95_KEY: Final[str] = "ci_95"
    # The null value for a bounded-correlation validity stat (no association).
    # A CI spanning this null means the construct is statistically
    # indistinguishable from no agreement with the human/gold anchor.
    CORRELATION_NULL: Final[float] = 0.0


# W24 (above) was RESERVED here; it is now IMPLEMENTED. The W-family numbering
# kept the gap so the construct-validity gate landed as W24 without renumbering
# W25.
#
# W25 — effect-clears-own-MDE gate. ADVISORY (WARN-only) sibling of W2/W17. W2
# verifies a numeric minimum detectable effect (MDE) is DECLARED in
# preregistration.json.mde — it NEVER checks the run's load-bearing effect
# actually CLEARS it. Result (audit proposal P4, 2026-06-18): runs reported
# with headline effects far BELOW their own declared MDE, framed as positive
# findings — sensitive-memory-retrieval (headline Δ=+0.0043 vs declared MDE
# 0.03 → 7× sub-MDE) and fair-intersectional-subspace (+0.0013 vs MDE 0.02 →
# 15× sub-MDE). Both are "formally undetectable" headlines that no gate flagged.
#
# W25 reads the SAME declared MDE as W2 (`_read_preregistration_field(plan_dir,
# MDE_FIELD)`) and resolves the headline effect via TWO tiers, both keyed on the
# headline/load-bearing arm only (like W17/W23 — never a secondary arm):
#
#   TIER 1 (sharp, load-bearing path) — when preregistration.json names a
#     `load_bearing_arm` (W13's field + the SAME `_w13_named_arm_keys`
#     normaliser, NOT a parallel mechanism), W25 reads that arm's point
#     estimate from results.json["comparisons"] and compares |effect| < mde.
#     A named arm whose own headline effect is sub-MDE is unambiguous: the
#     headline leads with a formally-undetectable effect.
#
#   TIER 2 (advisory fallback) — when NO load_bearing_arm is declared (the
#     under-declared confirmatory plan W21 already WARNs about — and the exact
#     shape of BOTH real defects) but a numeric MDE IS declared, W25 inspects
#     the run's PRIMARY-CONTRAST arms (`comparisons.PRIMARY_CONTRAST_PREFIX*`,
#     i.e. the `delta_primary_vs_*` family — the head-to-head contrasts the
#     headline is built from). If EVERY primary-contrast arm is sub-MDE (the
#     LARGEST |effect| among them does not clear the MDE), then no primary
#     contrast cleared detectability and the headline necessarily leads with a
#     sub-MDE effect → advisory WARN. If ANY primary contrast clears the MDE
#     (e.g. example-study-d7's Δ=+0.076 vs mde 0.018), W25 no-ops — a powered
#     contrast exists, so this is not the all-sub-MDE signature.
#
# WARN-ONLY (shipped WARN-first like W21/W15L/W23): appends to `warnings` only,
# no errors sink, never flips ok / routes back. Fail-safe NO-OP when: no numeric
# MDE declared, no plan sibling, no resolvable headline/primary-contrast effect,
# or the effect clears the MDE. The WARN says the load-bearing/headline effect
# is below the pre-registered MDE and must be framed as "below the pre-registered
# detectability threshold / formally undetectable," NOT as a positive finding.
# An HONEST NULL where the arm cleared power and returned a null effect is
# correctly framed already; because W25 only ever WARNs (never blocks) and the
# Tier-2 message is advisory ("verify the sub-MDE effect is framed as below
# threshold, not as a positive result"), the rare honest-null overlap costs at
# most one advisory note, never a false block.
class EffectClearsMdeGate:
    GATE_ID: Final[str] = "W25"
    SEVERITY: Final[str] = "warn"

    # The declared-MDE field W25 reads — the SAME field W2 reads. Single home:
    # both gates alias this constant rather than re-typing the literal (GP7).
    MDE_FIELD: Final[str] = "mde"

    # Tier-2 fallback: the comparisons-key prefix marking a PRIMARY-CONTRAST arm
    # (the `delta_primary_vs_*` head-to-head contrasts the headline is built
    # from). When no load_bearing_arm is declared, W25 checks whether EVERY arm
    # with this prefix is sub-MDE. `delta_` is the canonical comparisons-key
    # stem (ResultsField.COMPARISONS entries are `delta_*`).
    PRIMARY_CONTRAST_PREFIX: Final[str] = "delta_primary_vs_"


# W17 `scoped`-exit corroboration hardening (audit proposal P1, 2026-06-19;
# the portfolio's #1 rigor blocker). The base W17 gate (above) ERRORs when
# the pre-registered DISCRIMINATING arm is UNRUN *and* the central question is
# framed `"resolved"`, but DEMOTES to a benign WARN when the run self-writes
# `central_question.discriminating_arm_resolution = "scoped"` (the "I narrowed
# the claim" path). As shipped, that `scoped` downgrade was FREE — no
# corroboration required, no cap — so ~11/22 runs deferred their deciding
# experiment as "scoped future work" and still passed. This is exactly the
# recurring "the falsifying test was not run" weakness the portfolio audits flag.
#
# This gate makes a `scoped` exit COST something, so it can't be a free
# downgrade — WITHOUT punishing a single, honestly-corroborated narrow scope:
#
#   (1) CORROBORATION — a `scoped` resolution must carry a STRUCTURED, external-
#       ish `blocked_reason` drawn from a canonical enum (BLOCKED_REASONS), NOT
#       bare self-assertion. A `scoped` exit with NO blocked_reason, or a
#       free-text value not in the enum, ESCALATES the benign W17 WARN to a
#       stronger advisory ("decisive arm deferred as `scoped` with no structured
#       justification — the central question is NOT resolved and the headline
#       must not imply it is").
#
#   (2) CAP — the run accumulates a scoped-exit count across its central
#       question(s); beyond MAX_SCOPED_EXITS, ADDITIONAL `scoped` framings
#       escalate the WARN (a run that keeps narrowing every decisive arm is
#       not honestly scoping, it is dodging). The count is read structurally
#       from results.json (no STATE.md schema change): an explicit
#       `central_question.scoped_exit_count` integer if present, else the number
#       of `scoped`-framed entries when `central_question` is a list of
#       questions. Absent both → a single scoped exit (count 1), which never
#       trips the cap.
#
# WARN-ONLY (sibling of W23/W25; shipped WARN-first): appends to `warnings`
# only, no errors sink, never flips ok / routes back. Fail-safe NO-OP when: no
# plan sibling, no discriminating_arm declared, framing is legitimately
# `resolved` with the arm run, or the arm ran. A genuine honest narrow-scope
# WITH a structured blocked_reason UNDER the cap stays the benign base-W17 WARN
# (NOT escalated) — the escalation targets only UNCORROBORATED or SERIAL scoping.
class ScopedExitCorroborationGate:
    GATE_ID: Final[str] = "W17"
    SEVERITY: Final[str] = "warn"

    # The structured-justification field a `scoped` central_question block must
    # carry. Its value must be one of BLOCKED_REASONS (a canonical enum), not
    # bare self-assertion / free text.
    BLOCKED_REASON_KEY: Final[str] = "blocked_reason"

    # Canonical enum of external-ish reasons a decisive arm could not run. A
    # `scoped` exit carrying one of these is corroborated; anything else (absent,
    # free text, wrong type) is an uncorroborated self-assertion → escalate.
    BLOCKED_REASON_COMPUTE_UNAFFORDABLE: Final[str] = "compute_unaffordable"
    BLOCKED_REASON_ACCESS_GATED: Final[str] = "access_gated"
    BLOCKED_REASON_DATA_UNAVAILABLE: Final[str] = "data_unavailable"
    BLOCKED_REASON_OUT_OF_SCOPE_BY_DESIGN: Final[str] = "out_of_scope_by_design"
    BLOCKED_REASONS: Final[tuple[str, ...]] = (
        BLOCKED_REASON_COMPUTE_UNAFFORDABLE,
        BLOCKED_REASON_ACCESS_GATED,
        BLOCKED_REASON_DATA_UNAVAILABLE,
        BLOCKED_REASON_OUT_OF_SCOPE_BY_DESIGN,
    )

    # Cap: how many `scoped` exits a run may accumulate across its central
    # question(s) before ADDITIONAL scoped framings escalate. Read structurally
    # from results.json's central_question structure (see SCOPED_EXIT_COUNT_KEY).
    MAX_SCOPED_EXITS: Final[int] = 2
    # Explicit per-run scoped-exit counter the run may record in the
    # central_question block (cross-stage accumulation without a STATE.md schema
    # change). When absent, the count is inferred from the central_question shape.
    SCOPED_EXIT_COUNT_KEY: Final[str] = "scoped_exit_count"


# ---------------------------------------------------------------------------
# Math Paper Review subsystem (omicairn.paper_review) — APPENDED (disjoint
# namespace from the sibling-owned lab_theorems store; see
# docs/design-docs/math-paper-review.md)
# ---------------------------------------------------------------------------
# A paper-indexed store that REVIEWS landmark math papers and machine-verifies
# their core theorems against the local Lean/Mathlib toolchain. Storage mirrors
# lab_theorems exactly (folded JSONL op-log + atomic-create lockfile) but lives
# in a NEW sub-area so it never collides with the existing theorem store.
ENV_PAPER_REVIEW: Final[str] = "OMICAIRN_PAPER_REVIEW"
# Sub-area under lab_theorems/ (the math area), so paper-review data ships
# beside the theorem library it bridges into.
PAPER_REVIEW_SUBAREA: Final[str] = "paper_review"
PAPER_REVIEW_DIRNAME: Final[str] = "store"          # folder under paper_review/
PAPER_REVIEW_PAPERS_FILE: Final[str] = "papers.jsonl"  # paper op-log
PAPER_REVIEW_CLAIMS_FILE: Final[str] = "claims.jsonl"  # claim op-log
PAPER_REVIEW_LOCK: Final[str] = ".paper_review.lock"
PAPER_REVIEW_LEAN_SUBDIR: Final[str] = "lean"        # .lean sources live here


class PaperEra:
    """Coarse era bands for a source paper (antiquity → modern). Ordinal for
    display ordering only; domain-agnostic (an era is a time band, not a
    field)."""
    ANTIQUITY: Final[str] = "antiquity"
    MEDIEVAL: Final[str] = "medieval"
    RENAISSANCE: Final[str] = "renaissance"
    EARLY_MODERN: Final[str] = "early_modern"
    NINETEENTH_C: Final[str] = "19th_century"
    TWENTIETH_C: Final[str] = "20th_century"
    MODERN: Final[str] = "modern"


PAPER_ERA_ORDER: Final[tuple[str, ...]] = (
    PaperEra.ANTIQUITY,
    PaperEra.MEDIEVAL,
    PaperEra.RENAISSANCE,
    PaperEra.EARLY_MODERN,
    PaperEra.NINETEENTH_C,
    PaperEra.TWENTIETH_C,
    PaperEra.MODERN,
)


class ClaimVerdict:
    """Four-way formalization verdict of a reviewed paper claim.

    `verified` = Lean STATEMENT formalized AND proof compiles sorry-free AND
    axiom-clean (axioms ⊆ the allowed kernel set) AND clears the substance
    gate; `formalized_stmt_only` = the Lean statement compiles but the proof
    is incomplete (`sorry`) or NOT axiom-clean — we have the statement, not a
    clean proof; `failed` = attempted, did not compile (recorded honestly with
    errors); `deferred` = known-hard, not attempted in v1, recorded with a
    stated reason (e.g. Gödel incompleteness, quadratic reciprocity)."""
    VERIFIED: Final[str] = "verified"
    FORMALIZED_STMT_ONLY: Final[str] = "formalized_stmt_only"
    FAILED: Final[str] = "failed"
    DEFERRED: Final[str] = "deferred"


# The classical Lean kernel axiom set a `verified` claim's proof may depend on
# (Lean 4 / Mathlib standard classical foundations). Anything OUTSIDE this set
# (notably `sorryAx`) downgrades a compiled proof to `formalized_stmt_only`.
ALLOWED_LEAN_AXIOMS: Final[frozenset[str]] = frozenset({
    "propext", "Classical.choice", "Quot.sound",
})

# Paper-review knowledge-graph relations + node type (self-contained, parallel
# to THEOREM_EDGE_DEPENDS_ON / THEOREM_NODE).
PAPER_NODE: Final[str] = "paper"
CLAIM_NODE: Final[str] = "claim"
PAPER_EDGE_PRESENTS: Final[str] = "presents"        # paper -> claim
CLAIM_EDGE_FORMALIZED_AS: Final[str] = "formalized_as"  # claim -> theorem


# =============================================================================
# Within-EXECUTE parallel-arm fan-out (omicairn.parallel_arms) — W32
# =============================================================================
#
# An EXECUTE stage may fan out an embarrassingly-parallel workload — a
# cross-model-transfer matrix (model-pair x layer), a model x dataset table, or
# multi-seed replicates — into independent ARMS, each run by a subagent into its
# OWN disjoint output subdir with its OWN explicit seed, then merged by main.py
# into the single canonical results.json. This is WITHIN-run parallelism ONLY:
# the sequential stage loop + per-workspace lock are UNTOUCHED (a safety
# property); engine-level stage-loop parallelism is explicit future work.
#
# The parallelism CONTRACT is code-checked (not prose-only) by
# omicairn.parallel_arms.validate_manifest + a post-hoc cross_check W32 re-check.
# The four HARD (blocking) invariants: (1) DISJOINT output paths per arm — no
# two arms write the same / a nested / an escaping dir, none collides with a
# reserved stage file OR a workspace shared append-log (provenance/events.jsonl,
# the W32 control files); (2) EXPLICIT DISTINCT int seeds; (3) NO CONCURRENT GPU
# LAUNCH — a GPU arm (gpu=True OR routed to a GPU_ROUTED_HOSTS box) is counted
# against an EXTERNALLY-supplied slot budget clamped to DEFAULT_GPU_SLOTS=1 (a
# manifest CANNOT self-authorize its own budget), a GPU-host arm that omits the
# gpu flag is rejected, and fan-out is CPU/inference-light only; (4) ROUTING —
# no arm routed to the reserved host. A run with NO arm_manifest.json is
# a silent no-op (backward compatible). See omicairn/parallel_arms.py +
# docs/references/parallel-arms-schema.md.

# Artifact filenames (stage-relative). arm_manifest.json is authored by EXECUTE
# and is the pre-dispatch CLI-gate input; parallel_arms.json is the recorded
# verdict stamp; per-arm evidence is one arm_provenance.json PER ARM DIR
# (arms/<arm_id>/arm_provenance.json — race-free, one writer per file — NOT a
# shared append-log; deliberately NOT a phases.py optional_output).
ARM_MANIFEST_FILE: Final[str] = "arm_manifest.json"
PARALLEL_ARMS_STAMP_FILE: Final[str] = "parallel_arms.json"
# Per-arm provenance FRAGMENT filename, written INSIDE each arm's own disjoint
# output_dir (arms/<arm_id>/arm_provenance.json). Each concurrent subagent
# writes exactly ONE of these into ITS OWN dir — there is no shared append-only
# log for N concurrent writers to race (the old arm_provenance.jsonl shared-log
# design had a lost-update / torn-line hazard: a realistic JSONL line exceeds
# the POSIX PIPE_BUF atomic-append bound even under O_APPEND). The post-hoc
# re-check reads the UNION of these per-arm fragments. This is consistent with
# the module's own disjoint-writes philosophy: one writer, one file, one dir.
ARM_PROVENANCE_FRAGMENT: Final[str] = "arm_provenance.json"
# Legacy shared append-log name — retained ONLY as a reserved output_dir name so
# an arm can never claim it (a lingering fragment from the retired shared-log
# design would still be a shared-state collision). Not written by the engine.
ARM_PROVENANCE_LOG: Final[str] = "arm_provenance.jsonl"
PARALLEL_ARMS_SCHEMA_VERSION: Final[int] = 1

# Budgets. gpu_slots caps CONCURRENT gpu=True arms (v1 default 1 = serialize all
# GPU arms; fan-out is CPU-only). max_parallel is hard-capped to protect host
# load — a within-run worker pool, NOT `omicairn run` invocations, so the 25 global
# concurrent-RUN cap semantics are unchanged (1 run = 1 lock).
DEFAULT_GPU_SLOTS: Final[int] = 1
MAX_IN_STAGE_PARALLEL: Final[int] = 8

# A host (SSH alias) reserved for other workloads: OmiCairn parallel arms
# MUST NOT be routed onto it. Edit this placeholder to match your cluster.
# Named here so the validator never traffics in the raw literal.
RESERVED_ARM_HOST: Final[str] = "reserved-host-1"

# The designated GPU host(s) (SSH aliases). Any arm routed to one of these is
# GPU work by construction — so it MUST carry gpu=True and be counted against
# gpu_slots. This closes the invariant-3 bypass where an arm omits/mislabels the
# gpu flag but is routed to a GPU box: the validator derives the gpu flag from
# the routed host and rejects a GPU-host arm that is not counted as a GPU arm.
# (Placeholder alias: edit to match your cluster's GPU host names.)
GPU_ROUTED_HOSTS: Final[tuple[str, ...]] = ("gpu-host-1",)


class ArmManifestKey:
    """Canonical JSON field names in an ``arm_manifest.json`` payload — the
    EXECUTE (emitter) / omicairn.parallel_arms (validator) / cross_check-W32
    (consumer) contract. Hoisted so the schema lives in ONE place (GP7 — no
    raw literals in the module or the gate)."""

    VERSION: Final[str] = "version"
    ARMS: Final[str] = "arms"
    MAX_PARALLEL: Final[str] = "max_parallel"
    GPU_SLOTS: Final[str] = "gpu_slots"

    # Per-arm fields.
    ARM_ID: Final[str] = "arm_id"
    SEED: Final[str] = "seed"
    OUTPUT_DIR: Final[str] = "output_dir"
    GPU: Final[str] = "gpu"
    HOST: Final[str] = "host"
    DESCRIPTION: Final[str] = "description"

    # arm_provenance.jsonl recorded-evidence fields (validate_recorded).
    LAUNCHED_AT: Final[str] = "launched_at"
    FINISHED_AT: Final[str] = "finished_at"
    GPU_DEVICES: Final[str] = "gpu_devices"
    SEED_ECHO: Final[str] = "seed_echo"


# W32 — within-EXECUTE parallel-arm independence gate. The manifest-level
# invariants (validate_manifest) are BLOCKING (a genuine contract violation
# must not reach REVIEW); the recorded-evidence re-check (validate_recorded,
# reads arm_provenance.jsonl) is ADVISORY / WARN-only (shipped WARN-first like
# W21/W23/W25). Silent no-op when neither arm_manifest.json nor
# arm_provenance.jsonl is present (the critical backward-compat case).
class ParallelArmIndependenceGate:
    GATE_ID: Final[str] = "W32"
    SEVERITY: Final[str] = "error"  # manifest invariants block; recorded ev. warns


# Reserved stage-file / stage-subdir names an arm ``output_dir`` may NOT equal,
# nest under, or be a prefix of — an arm writing into any of these would race
# the canonical stage artifacts main.py assembles OR the workspace-level shared
# append-only logs the orchestrator/other stages write. Reuses the existing
# filename constants (no re-literaling): the merged results.json / analysis.md,
# the code/ + figures/ subdirs, the done.flag, the stage markers, AND the
# concurrency-critical shared logs (provenance.jsonl / events.jsonl) + the W32
# fan-out control files (arm_manifest.json / parallel_arms.json /
# arm_provenance.jsonl) — an arm that named one of these as its output_dir would
# race the very log the disjoint-writes invariant exists to protect.
RESERVED_STAGE_OUTPUT_NAMES: Final[tuple[str, ...]] = (
    RESULTS_JSON,
    ANALYSIS_MD,
    CODE_SUBDIR,
    FIGURES_SUBDIR,
    DONE_FLAG,
    "job_marker.json",
    PREREGISTRATION_FILE,
    PROVENANCE_LOG,
    EVENTS_LOG,
    ARM_MANIFEST_FILE,
    PARALLEL_ARMS_STAMP_FILE,
    ARM_PROVENANCE_LOG,
    ARM_PROVENANCE_FRAGMENT,
)


# Manuscript-pipeline subagents removed from the release. A resumed legacy
# workspace still carries them under .claude/agents/; sync_workspace_assets
# deletes them so no phase session can dispatch one.
REMOVED_AGENT_NAMES: Final[tuple[str, ...]] = (
    "section-writer", "title-optimizer", "cover-letter-writer",
    "venue-stylist", "citation-resolver", "citation-verifier",
    "reference-coverage-auditor", "claim-auditor", "peer-reviewer",
    "quality-reviewer",
)
# Manuscript-pipeline phases removed from the release. A legacy steering plan
# step naming one is skipped (marked done) instead of dispatched.
REMOVED_PHASE_NAMES: Final[frozenset[str]] = frozenset({
    "write", "claim_audit", "peer_review", "typeset",
})
NO_MANUSCRIPT_RULE_MARKER: Final[str] = "Do not draft manuscripts"
