"""The orchestrator: flat-numbered-stage loop with verdict routing.

One call to ``run_all(ws)`` drives the full pipeline: it loops calling
``run_next_stage(ws)``, which decides which phase to run next based on
(1) the most-recent verdict emitted by a verdict-emitting phase and
(2) STATE.md. Each stage gets its own globally-numbered directory
(``000_scope``, ``001_plan``, …, ``003_cross_check``, ``004_execute``, …)
that is never overwritten.

The brains-vs-plumbing split
----------------------------
This module is intentionally thin. All the research intelligence lives
in the Claude Code phase sessions. The orchestrator only does:

  1. Pick the next phase (decide_next_phase).
  2. Check that running it would not exceed bounds (max_iters_per_phase,
     max_backtracks). If so, halt.
  3. Create a fresh stage directory and render the phase prompt.
  4. For LLM phases: launch `claude -p` via claude_runner and verify the
     contract.
  5. For the synthetic CROSS_CHECK phase: run check_execute on the
     latest execute stage and write verdict.json + findings.md.
  6. Read the verdict (if any), update STATE.md, and return.

Verdict routing
---------------
After a verdict-emitting phase, the orchestrator reads
``<stage_dir>/verdict.json`` and uses the decision to pick the next
phase:

    proceed             → next phase in PIPELINE_ORDER
    retry_execute       → execute (new stage, counts as a retry iter)
    back_to_scope/plan/execute  → that phase (counts as a backtrack)
    halt                → stop, mark halted
    done                → stop, mark done

Non-verdict phases implicitly proceed on contract pass.
"""
from __future__ import annotations

import dataclasses
import json
import pathlib
import time
from typing import Any

from . import contracts, cross_check, knowledge_graph as kg, phases
from . import contribution as contribution_mod
from . import feasibility as feasibility_mod
from . import lab_knowledge
from . import lab_theorems
from . import provenance as provenance_mod
from . import stage as stage_mod, state
from . import steering as steering_mod
from . import steering_verify as steering_verify_mod
from . import user_notes as user_notes_mod
from . import verdict as verdict_mod
from .claude_runner import (
    run as claude_run,
    _dry_run_forced_verdict,
    _inactivity_timeout_s as _resolved_inactivity_timeout_s,
)
from .constants import (
    ANALYSIS_MD,
    BACKTRACK_DECISIONS,
    DECISION_TO_PHASE,
    Decision,
    DONE_FLAG,
    CLAUDE_CODE_PHASES,
    EventKind,
    EXECUTE_HARD_CEILING_SECONDS,
    STAGE_TIME_LIMIT_MODE_DYNAMIC,
    stage_time_limit_mode as _resolve_stage_time_limit_mode,
    STAGE_ITER_MODE_DYNAMIC,
    ZERO_PROGRESS_STALL_LIMIT,
    stage_iter_mode as _resolve_stage_iter_mode,
    FEASIBILITY_FILE,
    IDEATE_DIRECTIONS_FILE,
    KG_UPDATES_FILE,
    MAX_CONTRACT_RETRIES,
    NON_TERMINAL_DECISIONS,
    PhaseName,
    NEGATIVE_RESULTS_MD,
    RESULTS_JSON,
    REMOVED_PHASE_NAMES,
    REVIEW_NOTES_MD,
    ROUTING_SUGGESTION_CONSUMED_SUFFIX,
    ROUTING_SUGGESTION_FILE,
    RoutingSuggestionKey,
    RunStatus,
    SUMMARY_FILE,
    TERMINAL_DECISIONS,
    Token,
    VERDICT_FILE,
    VERDICT_PHASES,
    serendipity_enabled,
)

# Phases whose stage dirs may carry a graph_updates.json the orchestrator
# folds into the workspace knowledge graph (core belief 12). EXECUTE is
# the primary "assemble the KB on the run" site; the analysis REVIEW may
# also register on-the-fly hypotheses + findings.
_GRAPH_INGEST_PHASES: frozenset[str] = frozenset({
    PhaseName.EXECUTE, PhaseName.REVIEW,
})

# EXECUTE-phase wall-budget FLOOR for GPU-heavy / multimodal runs.
#
# 2026-06-15 wave-2 incident: 4 of 6 world-model runs were KILLED mid-EXECUTE
# by the per-stage hard wall cap (`002_execute TIMEOUT wall=10800s` = 180 min).
# They were NOT hung — the inactivity watchdog correctly never fired; it was
# genuine long multi-VLM probing (forward passes over several <=7B VLMs across
# probing arms legitimately take 3-6 h). Only `cross-model-transfer` squeaked
# in at ~3 h. Recovery required hand-setting `max_phase_wall_minutes_by_phase:
# {execute: 360}` per workspace. The DEFAULT/under-set 180-min EXECUTE cap is
# far too tight for GPU/multimodal runs.
#
# This FLOOR raises the EFFECTIVE EXECUTE wall budget to at least 6 h when the
# run is confidently GPU-heavy or multimodal — but ONLY as a floor: a user who
# explicitly configured a HIGHER execute budget keeps theirs (we never lower).
# It applies to the EXECUTE phase ONLY — SCOPE/PLAN/REVIEW/etc. are cheap and a
# 6 h floor there would mask real hangs. CPU-only / pure-theory / text-only
# runs legitimately finish fast and are NOT floored.
GPU_EXECUTE_WALL_FLOOR_MINUTES: int = 360

# 2026-06-15 d3 incident (wm-dynamics-decouple, CLEVRER video-dynamics): the
# EXECUTE turn was FALSE-KILLED by the inactivity watchdog while its remote
# GPU job was GENUINELY PROGRESSING (run.log showed PROG=[1450→1550/5000
# videos] GPU0/2 UP, then the agent's bash poll loop went quiet — stopped
# emitting PROG / touching .heartbeat — for >45 min while real per-shard GPU
# work continued; the beacon's since_last_growth_s climbed to 2670→kill at
# 2700s). The .heartbeat liveness is owned by the agent's poll-loop output
# cadence, so a slow-per-shard detached GPU job that out-quiets that cadence
# loses its signal and is false-killed. This is the inactivity-watchdog
# SIBLING of the wall-cap floor above.
#
# FLOOR: for confidently GPU-heavy/multimodal EXECUTE, raise the inactivity
# timeout to at least 90 min — long enough that a slow-but-live per-shard GPU
# job between heartbeat touches isn't false-killed, still short enough to
# catch a TRUE hang within ~1.5 h. Applied as max(configured, floor): a user
# who set a HIGHER inactivity budget keeps theirs; we only ever RAISE. EXECUTE
# phase + GPU-heavy ONLY (CPU/theory keep the default); and claude_runner only
# floors an already-ACTIVE watchdog — a disabled (0) watchdog stays disabled.
# Reuses the SAME _is_gpu_heavy_run predicate as the wall floor.
GPU_INACTIVITY_FLOOR_SECONDS: int = 5400

# Modality markers that imply model forward passes (a GPU-heavy run). Matched
# case-insensitively as substrings so "video_world_model", "image", "vision"
# etc. all trip. Kept as a canonical set rather than scattered literals.
_MULTIMODAL_MODALITY_MARKERS: frozenset[str] = frozenset({
    "vision", "video", "image", "audio", "speech",
})

from . import workspace as workspace_mod
from .workspace import PROMPTS_DIR, Workspace


def _is_gpu_heavy_run(research_md: Any) -> bool:
    """True when this case will run model forward passes — i.e. it targets a
    GPU compute resource OR declares a vision/video/image (multimodal)
    modality. Such runs legitimately take hours in EXECUTE.

    Degrades safely: any missing / unparseable signal is treated as "not
    confidently GPU-heavy" (returns False) so the floor is NEVER applied to a
    run we can't confidently classify, and never crashes the budget resolver.
    """
    # Structural is-GPU signal: ComputeResource.has_gpu (gpus > 0). Prefer the
    # structural property over a hardcoded resource-name literal (e.g.
    # "gpu-node"); fall back to the categorical `kind == "gpu"/"cluster"` when
    # has_gpu is unavailable. Never key on a specific host id.
    try:
        compute = getattr(research_md, "compute", None)
        if compute is not None:
            has_gpu = getattr(compute, "has_gpu", None)
            if has_gpu is True:
                return True
            kind = getattr(compute, "kind", None)
            if isinstance(kind, str) and kind.strip().lower() in ("gpu", "cluster"):
                return True
    except Exception:  # noqa: BLE001 — unparseable compute → not confident
        pass

    # Multimodal signal: any declared modality whose name contains a marker
    # implying forward passes over a (vision/video/...) model.
    try:
        modalities = getattr(research_md, "modalities", None) or []
        for m in modalities:
            if not isinstance(m, str):
                continue
            ml = m.strip().lower()
            if any(marker in ml for marker in _MULTIMODAL_MODALITY_MARKERS):
                return True
    except Exception:  # noqa: BLE001 — unparseable modalities → not confident
        pass

    return False


def _resolve_phase_wall_seconds(
    research_md: Any, phase_name: str, phase_default_seconds: int,
) -> tuple[int, int | None]:
    """Resolve the effective per-phase wall budget (seconds) for a phase.

    Resolution order (unchanged for non-EXECUTE phases):
      1. `max_phase_wall_minutes_by_phase[phase]` (explicit per-phase override)
      2. case-wide `max_phase_wall_minutes`
      3. the phase's built-in default

    Then, for the EXECUTE phase ONLY and only when the run is confidently
    GPU-heavy/multimodal, apply GPU_EXECUTE_WALL_FLOOR_MINUTES as a FLOOR:
    raise the budget to the floor if it is lower, but NEVER lower a value the
    user explicitly set higher (max(configured, floor)).

    Returns ``(effective_seconds, floored_from_seconds)`` where
    ``floored_from_seconds`` is the pre-floor budget when the floor was
    applied (so the caller can log the raise), else ``None``.
    """
    by_phase = getattr(research_md, "max_phase_wall_minutes_by_phase", None) or {}
    phase_specific = by_phase.get(phase_name, 0)
    if phase_specific and int(phase_specific) > 0:
        effective = int(phase_specific) * 60
    else:
        user_phase_wall_s = int(getattr(research_md, "max_phase_wall_minutes", 0) or 0) * 60
        effective = user_phase_wall_s if user_phase_wall_s > 0 else phase_default_seconds

    if phase_name == PhaseName.EXECUTE and _is_gpu_heavy_run(research_md):
        floor_s = GPU_EXECUTE_WALL_FLOOR_MINUTES * 60
        if effective < floor_s:
            return floor_s, effective  # raise to floor; report the prior value

    return effective, None


def _resolve_inactivity_floor_seconds(
    research_md: Any, phase_name: str,
) -> int | None:
    """The inactivity-timeout FLOOR (seconds) to apply for this phase, or
    ``None`` when no floor applies.

    Returns ``GPU_INACTIVITY_FLOOR_SECONDS`` for the EXECUTE phase of a
    confidently GPU-heavy/multimodal run (reusing the SAME
    ``_is_gpu_heavy_run`` predicate as the wall floor), else ``None``.
    CPU-only / pure-theory / text-only runs and all non-EXECUTE phases get
    no floor (``None``) and keep the configured/default inactivity timeout.

    This is only the FLOOR VALUE — claude_runner applies it as
    ``max(configured, floor)`` and only to an already-active watchdog (a
    disabled or explicitly-overridden watchdog is left untouched), so the
    kill LOGIC is unchanged; we only raise the threshold for GPU runs.
    Degrades safely: ``_is_gpu_heavy_run`` returns False on any
    missing/unparseable signal → ``None`` → no floor, no crash.
    """
    if phase_name == PhaseName.EXECUTE and _is_gpu_heavy_run(research_md):
        return GPU_INACTIVITY_FLOOR_SECONDS
    return None


# Multipliers for the DYNAMIC-mode execute-wall ESTIMATOR. Named constants (no
# number-soup): the estimate only SEEDS the soft ceiling — an under-estimate is
# harmless (the liveness-gated ceiling soft-extends past it) and an over-estimate
# only delays the soft-extension logging — so these are coarse-but-safe.
#  - GPU multiplier: a GPU/multimodal EXECUTE runs materially longer than a
#    CPU/theory one (multi-VLM forward passes; wave-2 incident).
#  - FT multiplier: fine-tuning is ~5-10x an inference/probing pass; use 6x.
#  - Per-(model×eval-item) scaling caps how much the declared workload can push
#    the seed above the resolved base, so a mis-declared huge workload can't seed
#    an absurd (but still soft, never a kill) ceiling.
#  - History multiplier: seed from max(observed prior execute wall) * 1.5 so a
#    repeat execute gets headroom over its own prior duration.
_EST_GPU_MULTIPLIER: float = 2.0
_EST_FINETUNE_MULTIPLIER: float = 6.0
_EST_WORKLOAD_MAX_MULTIPLIER: float = 8.0
_EST_HISTORY_MULTIPLIER: float = 1.5


def _prior_execute_wall_seconds(ws: Any) -> list[float]:
    """Bounded scan of the workspace's events.jsonl for the observed wall of
    PRIOR completed execute stages — the data-driven input to the estimator.

    Uses the EXECUTE_PROGRESS beacon's ``elapsed_s`` (the watchdog's own
    running clock; the max per execute stage approximates that stage's wall)
    and any STAGE_TIMEOUT ``seconds`` for an execute phase. Returns the
    per-stage maxima (one entry per execute stage that emitted a signal).

    Degrade-safe: any read/parse error returns ``[]`` (estimator falls back to
    the resolved base). Never raises, never does I/O beyond reading the single
    events.jsonl the workspace already owns.
    """
    from .constants import EVENTS_LOG

    by_stage: dict[str, float] = {}
    try:
        path = ws.root / EVENTS_LOG
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:  # noqa: BLE001 — missing/corrupt log → no history
        return []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(ev, dict):
            continue
        kind = ev.get("kind")
        stage = ev.get("stage")
        if not isinstance(stage, str):
            continue
        # Only execute-class stages contribute (stage dirs are named
        # NNN_<phase>; match the phase suffix so we never seed off a REVIEW).
        if not stage.endswith(f"_{PhaseName.EXECUTE}"):
            continue
        secs: float | None = None
        if kind == EventKind.EXECUTE_PROGRESS:
            raw = ev.get("elapsed_s")
            secs = float(raw) if isinstance(raw, (int, float)) else None
        elif kind == EventKind.STAGE_TIMEOUT:
            raw = ev.get("seconds")
            secs = float(raw) if isinstance(raw, (int, float)) else None
        if secs is not None and secs > 0:
            by_stage[stage] = max(by_stage.get(stage, 0.0), secs)
    return list(by_stage.values())


def estimate_execute_wall_seconds(
    research_md: Any,
    phase_name: str,
    base_seconds: int,
    history: list[float] | None = None,
) -> tuple[int, str]:
    """Estimate the INITIAL (soft) wall budget for an execute-class stage.

    PURE function (no I/O — the caller passes ``base_seconds`` from
    :func:`_resolve_phase_wall_seconds` and a pre-scanned ``history`` of prior
    execute-stage wall observations), unit-testable exactly like
    ``_resolve_phase_wall_seconds`` / ``_is_gpu_heavy_run``.

    The estimate SEEDS the DYNAMIC-mode soft ceiling — it is NEVER a hard kill.
    An under-estimate is covered by the liveness-gated soft extension; an
    over-estimate only delays when soft-extension logging begins. So estimator
    error can never abandon an arm (by construction).

    Returns ``(seconds, basis)`` where ``basis`` is a short human-readable tag
    for the log. INVARIANT: the returned value is NEVER below ``base_seconds``
    (the resolved config/floor is the estimate's floor) — the estimate only
    ever RAISES the seed.

    For a NON-execute phase this is a no-op (returns ``base_seconds``,
    ``"non_execute"``); DYNAMIC mode does not touch those phases.
    """
    if phase_name != PhaseName.EXECUTE:
        return base_seconds, "non_execute"

    base = float(base_seconds)

    # (1) Data-driven seed: if we have >=1 prior execute-stage observation,
    # anchor on max(observed) * 1.5 — a repeat execute should get headroom over
    # its own worst prior run. Floored at base so a short prior never lowers it.
    hist = [h for h in (history or []) if isinstance(h, (int, float)) and h > 0]
    if hist:
        seeded = max(base, max(float(h) for h in hist) * _EST_HISTORY_MULTIPLIER)
        return int(seeded), "history"

    # (2) No history → structural seed from stage type + declared workload.
    mult = 1.0
    basis_parts: list[str] = []
    if _is_gpu_heavy_run(research_md):
        mult *= _EST_GPU_MULTIPLIER
        basis_parts.append("gpu")
    if getattr(research_md, "workload_is_finetune", False):
        mult *= _EST_FINETUNE_MULTIPLIER
        basis_parts.append("finetune")

    # Declared workload scale (n_models × n_eval_items) nudges the seed up, but
    # is CAPPED so a mis-declared workload can't seed an absurd ceiling. Both
    # fields optional + degrade-safe (parsed to None when absent/invalid).
    n_models = getattr(research_md, "workload_n_models", None)
    n_eval = getattr(research_md, "workload_n_eval_items", None)
    if isinstance(n_models, int) and n_models > 1:
        # Each additional declared model adds ~1x the per-model base, capped.
        workload_mult = min(float(n_models), _EST_WORKLOAD_MAX_MULTIPLIER)
        mult *= workload_mult
        basis_parts.append(f"n_models={n_models}")
    if isinstance(n_eval, int) and n_eval > 0:
        basis_parts.append(f"n_eval_items={n_eval}")

    seeded = max(base, base * mult)
    basis = "+".join(basis_parts) if basis_parts else "base"
    return int(seeded), basis


@dataclasses.dataclass
class StageOutcome:
    """Result of running (or attempting to run) a single stage."""
    stage_index: int
    phase: str
    stage_dir_rel: str
    status: str  # "DONE" | "HALTED" | "CONTRACT_FAIL" | "TIMEOUT" | "CLI_MISSING" | "SESSION_LIMIT"
    attempts: int
    wall_seconds: float
    decision: str | None
    note: str


# ---------------------------------------------------------------------------
# Phase decision / routing
# ---------------------------------------------------------------------------

def _default_next_phase_after(phase_name: str) -> str | None:
    """Linear fallback: the phase after `phase_name` in PIPELINE_ORDER."""
    return phases.next_phase_in_order(phase_name)


# Decisions that ADVANCE the pipeline forward (do NOT re-open the same phase
# for another attempt). A verdict carrying one of these is progress by
# definition, so the DYNAMIC iter-mode zero-progress spin-catcher must never
# count it as a stall. Everything else — a RETRY_* re-opening the phase in
# place, or a BACK_TO_* backtrack — is a "re-open" the fingerprint watches.
_ADVANCING_DECISIONS: frozenset[str] = frozenset({
    Decision.PROCEED, Decision.DONE,
})


def _verdict_progress_fingerprint(v: "verdict_mod.Verdict | None", decision: str | None) -> str:
    """Stable digest of a verdict phase's forward-progress signal (#133).

    Folds the fields that distinguish a REAL new verdict from a re-emitted
    identical one: the decision, proposed_fix, and sorted target_file_hints. Two iterations with an
    identical digest AND a re-opening decision are a zero-progress stall; a
    changing digest is genuine progress (allowed without bound). Pure +
    degrade-safe: a None verdict digests the decision alone.
    """
    import hashlib

    parts: list[str] = [str(decision)]
    if v is not None:
        parts.extend([
            (getattr(v, "proposed_fix", "") or "").strip(),
            "|".join(sorted(getattr(v, "target_file_hints", []) or [])),
        ])
    return hashlib.sha256("\x1f".join(parts).encode("utf-8", "replace")).hexdigest()


def _capped_blocking_cross_check_retry(ws: Workspace, capped_phase: str) -> bool:
    """True iff the capped EXECUTE retry is a cross_check ERROR block (#123a).

    Used by the bounds-exceeded halt path to tag the halt with the specific
    ``HALTED_BLOCKING_CROSS_CHECK`` event (a correctness block, not a plain
    iteration-cap halt), so the halt reason is auditable: last done stage is
    a CROSS_CHECK whose verdict is retry_execute, and the capped phase is
    EXECUTE.
    """
    if capped_phase != PhaseName.EXECUTE:
        return False
    last_done = next(
        (st for st in reversed(stage_mod.list_stages(ws.root)) if st.done), None
    )
    if last_done is None or last_done.phase != PhaseName.CROSS_CHECK:
        return False
    v = verdict_mod.read_verdict(last_done.path)
    return v is not None and v.decision == Decision.RETRY_EXECUTE


# Number of CONSECUTIVE identical contract-fails (same phase + same
# failure note) the outer loop tolerates before treating the phase as
# stuck and invoking the anti-loop backstop. 3 means: first failure +
# two identical re-runs that made zero progress → stop. Kept small: an
# agent that re-emits the byte-identical defect twice will not fix it on
# attempt 4-8.
_IDENTICAL_CONTRACT_FAIL_LIMIT: int = 3


def _substantive_checker_result(
    ws: Workspace, phase_name: str, stage_dir: pathlib.Path
) -> cross_check.CrossCheckResult:
    """The phase-appropriate SUBSTANTIVE cross-check for a stage dir — the
    semantic layer ON TOP of the file-presence contract, exposed so the
    resume-time finalize sweep can ask "is this content sound?":
    execute→check_execute, everything else→trivially OK.

    ok=True is NECESSARY but NOT sufficient to call a stage complete:
    check_execute short-circuits to ok=True when results.json/analysis.md
    are absent (missing artifacts are a contract concern). Callers MUST
    pair this with a contract check (see _finalize_valid_unstamped_stages).
    """
    if phase_name == PhaseName.EXECUTE:
        return cross_check.check_execute(stage_dir, domain=ws.research_md.domain)
    return cross_check.CrossCheckResult(ok=True, warnings=[], errors=[])


def _contract_ok_modulo_done_flag(phase: phases.Phase, stage_dir: pathlib.Path) -> bool:
    """True iff every required output EXCEPT done.flag is present + non-trivial.

    `contracts.verify` treats done.flag like any other required file, so a
    crash-after-work stage reports `missing=['done.flag']`. Re-running the
    contract with done.flag stripped tells us the only thing missing is the
    marker (vs a genuinely-incomplete stage missing real artifacts).
    """
    stripped = dataclasses.replace(
        phase,
        required_outputs=tuple(o for o in phase.required_outputs if o != DONE_FLAG),
    )
    return contracts.verify(stripped, stage_dir).ok


def _has_invalid_verdict(stage_dir: pathlib.Path) -> bool:
    """True when verdict.json exists but is unreadable or names a decision
    outside the allowed set (e.g. a legacy ``retry_write``)."""
    return (
        (stage_dir / VERDICT_FILE).exists()
        and verdict_mod.read_verdict(stage_dir) is None
    )


def _finalize_valid_unstamped_stages(ws: Workspace) -> int:
    """Rescue stages that completed real work but crashed before stamping
    done.flag: write the marker, record DONE + a PROCEED verdict, return the
    count finalized.

    BUG (d5, 2026-06-11): a deepening EXECUTE ran 8.9h and produced a valid
    results.json (92KB) + analysis.md + code/main.py + 12 figures + summary
    (check_execute PASSES), but the orchestrator died before line ~1549 wrote
    done.flag. `StageRef.done` is purely `done.flag` existence, so the next
    `omicairn run` saw the stage as NOT-done, re-routed to EXECUTE, created empty
    stages 013/014 (contract-failing fast on "missing results.json, ..."),
    burned iters to the cap and HALTED — nearly stranding 8.9h of work.

    Fix at the SOURCE: a missing-ONLY-done.flag on an otherwise contract-valid,
    semantically-sound stage is a crash-after-work marker gap, not a contract
    failure. Finalizing it here (top of the run, BEFORE decide_next_phase)
    advances the pipeline from the recovered stage instead of re-running it.

    Generalised to ANY phase. Rescue predicate: (1) not done, AND (2) every
    non-done.flag required_output present + non-trivial, AND (3) the phase's
    substantive checker passes. (2)+(3) are the safety property — a stage
    missing real artifacts fails (2); one with fabricated numbers fails (3);
    neither is falsely rescued. Verdict phases keep their own verdict.json
    decision; we only stamp the marker for them.
    """
    finalized = 0
    for st in stage_mod.list_stages(ws.root):
        if st.done:
            continue
        try:
            phase = phases.get(st.phase)
        except ValueError:
            continue
        # Phase whose contract never required the marker — nothing to do
        # (cross_check stamps its own; no other phase applies).
        if DONE_FLAG not in phase.required_outputs:
            continue
        if not _contract_ok_modulo_done_flag(phase, st.path):
            continue  # genuinely incomplete — leave it to fail/retry/halt
        try:
            cc = _substantive_checker_result(ws, st.phase, st.path)
        except Exception:
            # A checker that itself blows up is NOT a green light to finalize.
            continue
        if not cc.ok:
            continue  # substantive checker found real problems — do not rescue
        if st.phase in VERDICT_PHASES and _has_invalid_verdict(st.path):
            continue  # invalid verdict — let the phase re-run, never PROCEED

        # All three conditions hold → valid-but-unstamped. Stamp + record DONE
        # so decide_next_phase advances forward from it.
        verdict_mod.write_done_flag(st.path)
        s = state.read(ws.state_path)
        state.record_stage_done(s, st.index, st.phase)
        # Verdict phases carry their own decision; don't overwrite it. Others
        # get a PROCEED verdict so downstream routing/inspection has a record.
        if st.phase in VERDICT_PHASES:
            v = verdict_mod.read_verdict(st.path)
            decision = v.decision if v is not None else Decision.PROCEED
        else:
            decision = Decision.PROCEED
            if verdict_mod.read_verdict(st.path) is None:
                verdict_mod.write_verdict(st.path, verdict_mod.proceed(
                    reason=(
                        f"finalized valid-but-unstamped {st.phase} stage "
                        f"(all required outputs + substantive checker ok; only "
                        f"done.flag missing — crash-after-work marker gap)"
                    )
                ))
        state.record_decision(s, decision)
        state.write(ws.state_path, s)
        stage_mod.append_event(ws.root, {
            "kind": EventKind.FINALIZED_VALID_UNSTAMPED_STAGE,
            "stage": st.rel_path,
            "phase": st.phase,
            "decision": decision,
            "cross_check_warnings": len(cc.warnings),
        })
        finalized += 1
    return finalized


def _skip_removed_phase_steering_steps(ws: Workspace) -> None:
    """A legacy steering plan may still queue a removed manuscript phase
    (write/typeset/...). Mark those steps done so they neither reactivate a
    finished run nor halt it on an unknown phase."""
    for idx, phase in steering_mod.skip_steps_with_phases(
        ws.root, REMOVED_PHASE_NAMES,
    ):
        stage_mod.append_event(ws.root, {
            "kind": EventKind.LEGACY_STEERING_STEP_SKIPPED,
            "step_index": idx,
            "phase": phase,
        })


def decide_next_phase(ws: Workspace) -> str | None:
    """Return the phase name to run next, or None if the pipeline is done.

    Routing rules, in priority order:

    1. If STATE.md status is DONE or HALTED → None, UNLESS a pending, valid
       steering plan exists (a user ran `omicairn steer` on a finished run —
       the terminal status must not silently swallow the steer request).
    2. If no stages exist yet → SCOPE (start of the pipeline).
    3. Find the most recent *done* stage.
       a. If that stage is a verdict-emitting phase and has a
          ``verdict.json``, follow the verdict:
            - DONE / HALT      → None
            - PROCEED          → next phase in PIPELINE_ORDER
            - retry_<phase>    → that phase (new stage)
            - back_to_<phase>  → that phase (counts as a backtrack)
       b. Otherwise, linearly proceed to the next phase in
          PIPELINE_ORDER after the last done stage.
    4. If every phase in PIPELINE_ORDER has at least one done stage
       and no verdict redirects us, the pipeline is done → None.
    """
    _skip_removed_phase_steering_steps(ws)
    s = state.read(ws.state_path)
    # A done/halted workspace short-circuits to None — UNLESS a pending,
    # valid steering plan exists. Without this carve-out, `omicairn steer` on a
    # finished run writes a valid steering_plan.json but the next
    # `omicairn run` consumes nothing and prints "(no stages ran)", forcing the
    # user to manually flip status (d7 steering bug, 2026-06-10). A
    # genuinely-done workspace with NO pending plan still returns None, so
    # `omicairn run` never loops on a finished run.
    if s.get("status") in (RunStatus.DONE, RunStatus.HALTED):
        if not steering_mod.has_pending_plan(ws.root):
            return None

    # Steering plan (Slice 1 wiring) takes top priority. If a
    # steering_plan.json with pending steps exists, dequeue the next
    # step and route to its phase. The orchestrator's normal phase-
    # render path then runs that step as a regular Claude Code
    # session (with the {{steering}} token populated).
    plan = steering_mod.read_plan(ws.root)
    if plan is not None and not plan.is_complete():
        nxt = plan.next_pending_step()
        if nxt is not None:
            # Phase name from the plan. If it's not in PIPELINE_ORDER
            # we treat it as `free` (Slice 2 implements; for Slice 1
            # we route to whatever the planner asked for, even if
            # `free` isn't in phases.ORDER yet — caller handles).
            return nxt.phase or PhaseName.SCOPE

    stages = stage_mod.list_stages(ws.root)
    if not stages:
        return PhaseName.SCOPE

    # Find the last done LINEAR-PIPELINE stage to route from. Standalone
    # stages (ideate/free) are not positions in PIPELINE_ORDER — they don't
    # drive linear "what's next" routing (ideate precedes SCOPE).
    # list_stages surfaces them (parser round-trips PhaseName.IDEATE), so we
    # must skip them here explicitly. Without the skip,
    # _default_next_phase_after('ideate') raises (ideate ∉ PIPELINE_ORDER).
    last_done = None
    for st in reversed(stages):
        if st.done and st.phase in phases.PIPELINE_ORDER:
            last_done = st
            break
    if last_done is None:
        return PhaseName.SCOPE

    if last_done.phase in VERDICT_PHASES:
        v = verdict_mod.read_verdict(last_done.path)
        if v is not None:
            d = v.decision
            if d in (Decision.DONE, Decision.HALT):
                return None
            if d == Decision.PROCEED:
                # Tier 3.1 routing-advisor protocol: a pi-advisor (or any
                # other agent) may have written `routing_suggestion.json`
                # on the workspace root. We honor it ONLY when the
                # verdict is PROCEED — i.e. the agent has not already
                # asked us to do something specific.
                suggestion = _read_routing_suggestion(ws)
                if suggestion:
                    return suggestion
                return _default_next_phase_after(last_done.phase)
            if d in DECISION_TO_PHASE:
                return DECISION_TO_PHASE[d]
        elif (last_done.phase == PhaseName.REVIEW
                and _has_invalid_verdict(last_done.path)):
            # A REVIEW whose verdict is unreadable or carries a removed
            # decision (legacy `retry_write`) never ran a valid analysis
            # review — re-run it rather than walking off the end as DONE.
            return PhaseName.REVIEW
        # Missing verdict on a verdict-emitting phase — fall through to
        # linear proceed as a safe default.

    # SCOPE feasibility / statistical-power gate. SCOPE is not a regular
    # verdict-emitting phase, but when the gate fires it writes a
    # verdict.json into the scope stage (back_to_scope / back_to_plan)
    # that we must honor — otherwise a doomed underpowered confirmatory
    # run would proceed straight to a wasted PLAN/EXECUTE. A `proceed`/
    # absent verdict (the common case: feasible, or gate off) falls
    # through to the normal linear walk. See _process_feasibility_gate.
    if last_done.phase == PhaseName.SCOPE:
        v = verdict_mod.read_verdict(last_done.path)
        if v is not None and v.decision in DECISION_TO_PHASE:
            return DECISION_TO_PHASE[v.decision]

    # Non-verdict phase (or fall-through): linear proceed.
    nxt = _default_next_phase_after(last_done.phase)
    if nxt is not None:
        return nxt
    # We were at the last phase already — pipeline is done.
    return None


def _read_routing_suggestion(ws: Workspace) -> str | None:
    """Tier 3.1 routing advisor: read ``<workspace>/routing_suggestion.json``
    and return a target phase name when the suggestion is valid.

    Canonical schema (parser audit Step F, since 2026-05):

        {
            "kind": "route",                  # required
            "target_phase": "<phase>",        # required, in PIPELINE_ORDER
            "confidence": "medium" | "high",  # low silently ignored
            "reason": "<one sentence>"
        }

    Legacy schema (deprecated, accepted as warn-only back-compat):

        {
            "suggested_decision": "route_to_<phase>",
            "confidence": "...",
        }

    Other suggestion shapes (``continue``, ``reframe``) are advisory
    notes for downstream phases, not routing commands. After the
    suggestion is consumed (any decision), the file is renamed to
    ``routing_suggestion.consumed.json`` so it does not fire again.
    """
    sug_path = ws.root / ROUTING_SUGGESTION_FILE
    if not sug_path.exists():
        return None
    try:
        sug = json.loads(sug_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    target: str | None = None
    confidence = (sug.get(RoutingSuggestionKey.CONFIDENCE) or "").strip().lower()
    if confidence not in ("medium", "high"):
        # Low confidence is silently ignored.
        _consume_routing_suggestion(sug_path)
        return None

    # ── Canonical schema first ───────────────────────────────────
    kind = (sug.get(RoutingSuggestionKey.KIND) or "").strip().lower()
    if kind == RoutingSuggestionKey.KIND_ROUTE:
        from .constants import PIPELINE_ORDER
        candidate = (sug.get(RoutingSuggestionKey.TARGET_PHASE) or "").strip().lower()
        if candidate in PIPELINE_ORDER:
            target = candidate
        _consume_routing_suggestion(sug_path)
        return target

    # ── Legacy back-compat ───────────────────────────────────────
    decision = (sug.get(RoutingSuggestionKey.SUGGESTED_DECISION) or "").strip().lower()
    if decision.startswith(RoutingSuggestionKey.ROUTE_TO_PREFIX):
        candidate = decision[len(RoutingSuggestionKey.ROUTE_TO_PREFIX):]
        from .constants import PIPELINE_ORDER
        if candidate in PIPELINE_ORDER:
            target = candidate
    _consume_routing_suggestion(sug_path)
    return target


def _consume_routing_suggestion(sug_path: pathlib.Path) -> None:
    """Rename routing_suggestion.json to .consumed.json so we don't
    re-apply it on the next decide_next_phase call."""
    try:
        sug_path.rename(sug_path.with_suffix(ROUTING_SUGGESTION_CONSUMED_SUFFIX))
    except OSError:
        pass


def _final_status_from_last_verdict(ws: Workspace) -> tuple[str, str]:
    """Decide the terminal run status when no next phase is available.

    Returns (status, reason). Called from ``run_next_stage`` when
    ``decide_next_phase`` returns None. The logic mirrors
    ``decide_next_phase`` but focuses on DONE vs HALTED:

      - Last done stage emitted a HALT verdict → HALTED with the
        verdict's reason.
      - Last done stage emitted a DONE verdict (or review finished
        without a verdict) → DONE with "all phases complete".
      - No done stages at all → DONE with "all phases complete"
        (caller shouldn't hit this, but fail safely).
    """
    stages = stage_mod.list_stages(ws.root)
    last_done = None
    for st in reversed(stages):
        if st.done:
            last_done = st
            break
    if last_done is None or last_done.phase not in VERDICT_PHASES:
        return RunStatus.DONE, "all phases complete"
    v = verdict_mod.read_verdict(last_done.path)
    if v is None:
        if (last_done.phase == PhaseName.REVIEW
                and _has_invalid_verdict(last_done.path)):
            return RunStatus.HALTED, (
                f"{last_done.rel_path}: invalid {VERDICT_FILE} "
                f"(unreadable or removed decision); analysis review not complete"
            )
        return RunStatus.DONE, "all phases complete"
    if v.decision == Decision.HALT:
        reason = (v.reason or "halted by verdict")[:200]
        return RunStatus.HALTED, reason
    # DONE or any other non-HALT decision that routes to None
    # (e.g. a review emitting DONE) → clean finish.
    return RunStatus.DONE, "all phases complete"


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------

def _knowledge_summary(ws: Workspace, for_phase: str) -> str:
    return stage_mod.summarize_lessons(ws.root, for_phase=for_phase)


def _asset_probe_summary(ws: Workspace) -> str:
    """Render the asset_probe.json (if any) as a markdown table.

    Resolves to "(asset probe not run)" when the file is missing or
    cannot be parsed. Failure is silent: the summary is advisory and
    must never block prompt rendering.
    """
    probe_path = ws.root / "asset_probe.json"
    if not probe_path.exists():
        return "(asset probe not run)"
    try:
        from . import asset_probe
        payload = json.loads(probe_path.read_text())
        rows = []
        for entry in payload.get("assets", []) or []:
            rows.append(asset_probe.ProbeResult(
                url=entry.get("url", ""),
                accession=entry.get("accession", ""),
                status=entry.get("status", "unknown"),
                http_status=entry.get("http_status"),
                size_bytes=entry.get("size_bytes"),
                content_type=entry.get("content_type"),
                checked_at=entry.get("checked_at", ""),
                error=entry.get("error", "") or "",
            ))
        return asset_probe.render_summary(rows) or "(asset probe empty)"
    except Exception:
        return "(asset probe not parseable)"


def _shared_lessons_summary(ws: Workspace, for_phase: str) -> str:
    """Cross-workspace lessons matching the current phase. Tier 3.3.

    Defers to ``paper_library.shared_lessons_for_phase``; resolves
    to ``(none)`` when the helper is absent or returns empty.
    """
    try:
        from . import paper_library
        getter = getattr(paper_library, "shared_lessons_for_phase", None)
        if getter is None:
            return "(none)"
        text = getter(for_phase)
        return text or "(none)"
    except Exception:
        return "(none)"


def _routing_suggestion_summary(ws: Workspace) -> str:
    """Most recent agent-emitted routing suggestion across stages.

    Tier 3.1 routing-advisor protocol. Each stage may write a
    ``routing_suggestion.json`` advising the orchestrator on the
    next decision. Returns ``(none)`` when no suggestion has been
    written this iteration.
    """
    stages = stage_mod.list_stages(ws.root)
    for st in reversed(stages):
        if not st.done:
            continue
        sug = st.path / ROUTING_SUGGESTION_FILE
        if not sug.exists():
            continue
        try:
            data = json.loads(sug.read_text())
        except Exception:
            continue
        # Render either schema: canonical (kind=route → target_phase / reason)
        # or the legacy (suggested_decision / rationale) shape.
        decision = (
            data.get(RoutingSuggestionKey.TARGET_PHASE)
            or data.get(RoutingSuggestionKey.SUGGESTED_DECISION, "?")
        )
        rationale = (
            data.get(RoutingSuggestionKey.REASON)
            or data.get(RoutingSuggestionKey.RATIONALE, "")
            or ""
        ).strip()[:300]
        confidence = data.get(RoutingSuggestionKey.CONFIDENCE, "?")
        return (
            f"_Pending routing suggestion from {st.path.name}:_ "
            f"`{decision}` (confidence={confidence}). {rationale}"
        )
    return "(none)"


# Phases the orchestrator considers "verdict-emitting" for retry-note
# purposes. Their verdict.json carries a `decision` and (typically)
# proposed_fix / target_file_hints / evidence — exactly the feedback
# a retry stage needs.
_RETRY_NOTE_VERDICT_PHASES: tuple[str, ...] = (
    PhaseName.REVIEW, PhaseName.CROSS_CHECK,
)

# Durable marker the EXECUTE agent writes the instant it launches a
# detached GPU job (see prompts/execute.md). On a recovered/retried turn
# its presence means "a remote job is (or was) in flight for this stage" —
# the retry note must then tell the agent to HARVEST that job before
# relaunching, so a fresh turn never collides with a still-running job on
# the shared host (OOM / usage-policy violation / wasted GPU hours).
JOB_MARKER_FILENAME: str = "job_marker.json"


def _job_marker_note(ws: Workspace, current_phase: str) -> str:
    """If any prior same-phase stage dir holds a ``job_marker.json``, return
    a harvest-before-relaunch instruction block; else "". General across
    phases — keyed only on the marker file, never on research content."""
    stages = stage_mod.list_stages(ws.root)
    for st in reversed(stages):
        if st.phase != current_phase:
            continue
        marker = st.path / JOB_MARKER_FILENAME
        if not marker.is_file():
            continue
        detail = ""
        try:
            m = json.loads(marker.read_text())
            host = m.get("host") or "?"
            rpath = m.get("remote_results_path") or "?"
            sess = m.get("pid_or_session") or "?"
            detail = (
                f" (host=`{host}`, remote_results=`{rpath}`, "
                f"pid/session=`{sess}`)"
            )
        except (OSError, json.JSONDecodeError, AttributeError):
            pass
        return (
            f"\n\n**A `job_marker.json` from a prior {current_phase} attempt "
            f"exists at `{st.rel_path}/{JOB_MARKER_FILENAME}`{detail} — a GPU "
            f"job was launched on the shared host for THIS stage. CHECK the "
            f"remote job and HARVEST it before relaunching: `ssh` the host, "
            f"see whether the job is still running (resume polling it) or "
            f"already finished (`scp` its results back), then finish the "
            f"contract. Do NOT start a NEW GPU job while one is already "
            f"running for this stage — it would OOM-collide, violate the "
            f"usage policy, and waste hours of compute.**"
        )
    return ""


def _build_retry_note(
    ws: Workspace, current_phase: str, iter_count: int
) -> str:
    """Render the most recent verdict-emitting stage's actionable
    feedback as a markdown block for the next phase's prompt.

    Rationale: the prior verdict's `proposed_fix` and `target_file_hints`
    are the EXACT instructions the retry should follow. Without this, the
    agent has to discover the prior review by hand from
    `{{stage_listing}}` (which only shows a one-line summary).

    Returns:
        - "(fresh attempt)" on a brand-new run with no prior verdict
        - A multi-line markdown block with the prior decision,
          proposed_fix, and target_file_hints
    """
    stages = stage_mod.list_stages(ws.root)
    if not stages:
        return "(fresh attempt — no prior stages)"

    # Salvage clause: when THIS phase is retrying after its own
    # CONTRACT_FAIL (the prior same-phase stage is on disk but not done),
    # surface the substantive artifacts it already produced so the agent
    # COMPLETES them instead of re-running expensive work from scratch.
    # Observed: a long EXECUTE produced full results.json + analysis.md
    # but the claude -p turn ended before writing done.flag → CONTRACT_FAIL
    # → the next attempt would needlessly re-extract activations (hours of
    # GPU). General across phases; never weakens the contract (the agent
    # still must emit every required file + done.flag).
    #
    # Pick the most recent same-phase, not-done stage that ACTUALLY HOLDS
    # substantive outputs. We must NOT just take the latest such stage:
    # this runs AFTER the current (retry) stage dir is created, so the
    # current empty stage is itself in `stages` — naively taking the last
    # not-done same-phase stage selects the empty current one and the
    # clause never fires (bug observed 2026-06-10 on d1's 004_execute).
    # Scanning for substantive files skips the empty current stage and
    # finds the failed prior attempt that has results.json/analysis.md.
    _SALVAGE_FILES = (RESULTS_JSON, ANALYSIS_MD, SUMMARY_FILE, VERDICT_FILE,
                      REVIEW_NOTES_MD, NEGATIVE_RESULTS_MD)
    # HARVEST-before-RELAUNCH: an in-flight remote GPU job is invisible to
    # the file-salvage scan above (its results may still be ON THE HOST),
    # so a durable job_marker.json is the only signal it exists. Append its
    # harvest instruction to whatever the salvage scan finds (both fire
    # together when an attempt launched a job AND wrote partial outputs).
    salvage = _job_marker_note(ws, current_phase)
    for st in reversed(stages):
        if st.phase != current_phase or st.done:
            continue
        existing = [p.name for p in sorted(st.path.glob("*"))
                    if p.is_file() and p.stat().st_size > 0]
        substantive = [f for f in existing if f in _SALVAGE_FILES]
        prior_same = st
        if substantive:
            salvage = (
                f"\n\n**Prior {current_phase} attempt `{prior_same.rel_path}` "
                f"CONTRACT-FAILED but already produced substantive outputs: "
                f"{', '.join(sorted(substantive))}** (full file list: "
                f"{', '.join(existing[:20])}). Its heavy compute may also be "
                f"cached on the scratch host. FIRST inspect those files: if "
                f"they are complete and correct, COPY/regenerate them into "
                f"your stage dir and just finish the contract (write the "
                f"missing files + done.flag LAST) rather than re-running the "
                f"whole analysis. Only re-run the parts that are actually "
                f"missing or wrong."
            )
            break

    # Walk backwards looking for the most recent verdict-emitting stage.
    for st in reversed(stages):
        if not st.done:
            continue
        if st.phase not in _RETRY_NOTE_VERDICT_PHASES:
            continue
        v = verdict_mod.read_verdict(st.path)
        if v is None:
            continue
        # Distinguish "retry was demanded" (the agent must address
        # specific concerns) from "prior stage just proceeded" (forward
        # motion; no retry needed). On `proceed`/`done` the retry-note
        # framing of "MUST address every concrete concern" is wrong —
        # there are no concerns to address.
        is_retry = v.decision not in ("proceed", "done")
        # Found a verdict. Render its actionable parts.
        lines: list[str] = []
        lines.append(
            f"_Prior {st.phase} stage `{st.rel_path}` emitted "
            f"`decision: {v.decision}`._"
        )
        if is_retry and v.proposed_fix:
            lines.append("")
            lines.append("**Proposed fix from prior stage:**")
            lines.append(v.proposed_fix.strip())
        if is_retry and v.target_file_hints:
            lines.append("")
            lines.append("**Target file hints from prior stage:**")
            for hint in v.target_file_hints[:6]:
                lines.append(f"- {hint}")
        lines.append("")
        if is_retry:
            lines.append(
                f"**You MUST read** `{st.rel_path}/`'s artifacts "
                "(verdict.json, findings.md / review_notes.md, etc.) "
                "before starting your retry. "
                "Address every concrete concern surfaced above. Do NOT "
                "silently re-emit the prior iteration's wording — "
                "produce a substantively-revised output."
            )
        else:
            lines.append(
                f"_(Forward-motion note: prior {st.phase} proceeded "
                "cleanly; no retry required. Read its outputs as part "
                "of normal upstream-stage consultation, not as concerns "
                "to address.)_"
            )
        return "\n".join(lines) + salvage
    return ("(fresh attempt)" + salvage) if salvage else "(fresh attempt)"


def _ideate_targets_summary(ws: Workspace) -> str:
    """Render the IDEATE source-collection targets (hyperparameters) as a
    guidance block for the {{ideate_targets}} token. Only the IDEATE
    prompt references the token, but it is computed for every render
    (cheap) so the lint that maps tokens→ctx stays simple.

    When a prior IDEATE stage already populated the graph, this reports
    how many sources exist and how many MORE to collect — so a large
    target (e.g. 50 papers) is reached by ACCUMULATING across re-runs
    (source nodes dedup by title; the graph is additive) rather than
    demanding one heroic single-session survey."""
    c = ws.research_md.ideate_config
    target_total = c["target_papers"] + c["target_blogs"] + c["target_beliefs"]
    try:
        have = kg.stats(ws.root).get("nodes_by_type", {}).get("source", 0)
    except (OSError, ValueError):
        have = 0
    if have > 0:
        remaining = max(0, target_total - have)
        topup = (
            f"\nNOTE: {have} sources are already in the graph from a prior "
            f"IDEATE pass. Collect roughly {remaining} MORE NEW sources "
            f"(do not re-list existing ones — they dedup) to reach the "
            f"~{target_total}-source target. If the target is already met, "
            f"deepen the directions instead.\n"
        )
    else:
        topup = ""
    return (
        f"Collection targets for this survey (aim for at least these counts):\n"
        f"  - papers (peer-reviewed / preprint): {c['target_papers']}\n"
        f"  - high-impact blogs / essays: {c['target_blogs']}\n"
        f"  - stated beliefs from researchers (professor / nobel / "
        f"philosopher / industry, combined): {c['target_beliefs']}\n"
        f"Propose between {c['min_directions']} and {c['max_directions']} "
        f"research directions."
        + topup
    )


def _feasibility_guidance(ws: Workspace) -> str:
    """Render the SCOPE feasibility / statistical-power gate guidance for
    the {{feasibility_guidance}} token. When the gate is active for this
    case, instruct SCOPE to emit feasibility.json profiling the ACTUALLY
    extractable analysis units vs the reference effect's requirement,
    BEFORE PLAN locks a pre-registration. When the gate is inactive
    (exploratory / opted-out), render a neutral note so exploratory runs
    aren't pushed to over-profile.

    NEUTRALITY (belief 13): the guidance asks about power / data
    feasibility ONLY, never the hypothesised direction — a well-powered
    NULL must pass cleanly."""
    cfg = ws.research_md.feasibility_config
    active = feasibility_mod.gate_applies(
        cfg,
        research_mode=ws.research_md.research_mode,
        pre_registration=ws.research_md.pre_registration,
    )
    if not active:
        return (
            "Feasibility/power gate: NOT active for this case "
            f"(applies_to={cfg.applies_to}, mode={ws.research_md.research_mode}). "
            "feasibility.json is optional here; do not over-profile an "
            "exploratory / hypothesis-generating run."
        )
    return (
        "Feasibility / statistical-power gate is ACTIVE for this "
        "confirmatory run. BEFORE PLAN locks a pre-registration you MUST "
        f"write `feasibility.json` (JSON only) into your stage dir.\n"
        "Profile, per planned analysis unit / dataset, the ACTUALLY "
        "extractable N (e.g. cells per atlas AFTER your intended gating, "
        "events, samples) and compare it to the reference effect's "
        "requirement (the original study's N / the N the effect size "
        "needs). A units' extractable N below "
        f"~{cfg.min_fraction_of_reference:g}x the reference N is a red "
        "flag worth flagging.\n"
        "Schema (all fields required):\n"
        "  {\n"
        '    "verdict": "feasible" | "underpowered" | "needs_data",\n'
        '    "recommended_action": "proceed" | "fix_extraction" | '
        '"acquire_data" | "rescope",\n'
        '    "rationale": "<why — power/data reasoning only>",\n'
        '    "analysis_units": [\n'
        '      {"name": "...", "extractable_n_estimate": <int|null>,\n'
        '       "reference_n": <int|null>,\n'
        '       "adequacy": "adequate" | "marginal" | "inadequate" | '
        '"missing"}\n'
        "    ]\n"
        "  }\n"
        "Routing the harness applies: feasible→proceed; "
        "fix_extraction→re-profile (relax the gate / load raw/all cells); "
        "acquire_data→fetch the missing dataset first (if web is allowed, "
        "prefer fetching over silent exclusion); rescope→re-frame the "
        "question.\n"
        "NEUTRALITY (non-negotiable): key the verdict ONLY on statistical "
        "power / data feasibility, NEVER on the hypothesised direction or "
        "sign of any result. A well-powered NULL is `feasible` → proceed. "
        "The gate's job is to stop UNDER-powered runs, not to chase "
        "positives. Where data genuinely does not exist, the honest path "
        "is acquire_data or rescope — never fabricate."
    )


def _lab_knowledge_summary(ws: Workspace) -> str:
    """Render the cross-workspace lab knowledge base for the
    {{lab_knowledge}} token (IDEATE/SCOPE). Resolves the same
    workspace-relative root the distiller writes to, so a run reads back
    what prior runs in the same workspaces tree wrote. Best-effort: a
    sentinel when empty or unreadable so the token is always substitutable."""
    try:
        return lab_knowledge.render_lab_knowledge_md(
            root=lab_knowledge.resolve_root(workspaces_root=ws.root.parent))
    except (OSError, ValueError):
        return "(no prior lab knowledge available)"


def _lab_theorems_summary(ws: Workspace) -> str:
    """Render the lab THEOREM-library availability for the {{lab_theorems}}
    token (EXECUTE/theory path). Resolves the default tracked store so a theory
    run sees the verified, Lean-compiled theorems it can `omicairn labtheorems
    retrieve` and import instead of cold-proving. Best-effort: a sentinel when
    empty/unreadable so the token is always substitutable."""
    try:
        return lab_theorems.render_availability_md()
    except (OSError, ValueError):
        return "(no lab theorem library available)"


def _ideate_source_policy(ws: Workspace) -> str:
    """Render the OPTIONAL source-credibility-weighting policy
    ({{ideate_source_policy}}). The bias is opt-in (belief 13): a neutral
    line by default, a weighting instruction only when the case sets
    `prefer_reputable_sources`."""
    if not ws.research_md.prefer_reputable_sources:
        return (
            "Source policy: weight sources on their own merits and "
            "verification status; no provenance bias is configured."
        )
    return (
        "Source policy (prefer_reputable_sources ENABLED): give higher "
        "weight to sources from well-established groups, high-impact outlets, and "
        "integrity-clean records, and lower weight to unknown or "
        "integrity-flagged ones — reflect this in each direction's "
        "ordering and rationale. This is an ADMITTED, configured bias; it "
        "adjusts ranking/priors only and NEVER overrides a source's "
        "verification status or a reproducible on-disk result."
    )


def _domain_pack_guidance(ws: Workspace, for_phase: str) -> str:
    """Optional domain pack guidance block for the current phase.

    Domain packs are bundles under ``engine/domain_packs/<name>/``
    with a ``profile.yaml`` + per-phase ``phase_<phase>.md`` files.
    We load them lazily to avoid making the orchestrator depend on an
    unloaded module.
    """
    try:
        from . import domain_pack  # optional module
    except ImportError:
        return ""
    return domain_pack.render_guidance(
        domain=ws.research_md.domain,
        phase_name=for_phase,
    )


def _maybe_mark_steering_step_done(
    ws: Workspace, outcome: "StageOutcome"
) -> None:
    """If a steering plan is active, mark its head step done.

    Called after a successful (DONE) stage. Identifies the head
    pending step by phase match (the orchestrator dequeues by phase
    in `decide_next_phase`, so the next pending step's phase equals
    the just-completed phase). Idempotent: a no-op when no plan is
    active or the head step's phase doesn't match.

    Also (Slice 3) runs warn-only verifiers on the step's
    ``expected_changes`` and lands per-predicate results in
    ``steering_log.jsonl``. Verifier failures NEVER block — they're
    advisory drift signals.
    """
    plan = steering_mod.read_plan(ws.root)
    if plan is None or plan.is_complete():
        return
    nxt = plan.next_pending_step()
    if nxt is None:
        return
    if nxt.phase and nxt.phase != outcome.phase:
        # Stage was a normal linear-walk dispatch, not a steering
        # routing. Don't consume the step.
        return
    # Locate the step's index for mark_step_done.
    for idx, step in enumerate(plan.steps):
        if step is nxt:
            _run_step_verifiers(ws, step, outcome)
            steering_mod.mark_step_done(ws.root, idx, outcome.stage_dir_rel)
            return


def _run_step_verifiers(
    ws: Workspace, step: "steering_mod.SteeringStep", outcome: "StageOutcome"
) -> None:
    """Run warn-only expected_changes verifiers and log results.

    Uses the just-completed stage directory's mtime as the "step
    started at" reference for ``mtime(...) > NOW`` predicates: the
    stage dir is created at dispatch time, so anything modified
    after that mtime was touched by the steered session.
    """
    if not step.expected_changes:
        return
    stage_dir = ws.root / outcome.stage_dir_rel
    started_at = (
        stage_dir.stat().st_mtime if stage_dir.exists() else 0.0
    )
    # Expand the same `{{token}}` vocabulary the prompt renderer uses
    # so predicates like `mtime({{stage_dir}}/lean/T.lean) > NOW`
    # resolve to the real stage dir before verification. `{{stage_dir}}`
    # maps to the RELATIVE stage path because the verifier joins
    # operands onto ws.root. Keyed by the canonical Token constants.
    token_values = {
        Token.STAGE_DIR: outcome.stage_dir_rel,
        Token.WORKSPACE_ROOT: str(ws.root),
    }
    results = steering_verify_mod.verify_expected_changes(
        list(step.expected_changes), ws.root, started_at, token_values
    )
    counts = steering_verify_mod.summarise_results(results)
    steering_mod.append_log(ws.root, {
        "kind": EventKind.STEERING_VERIFIER_RESULTS,
        "phase": outcome.phase,
        "stage_dir_rel": outcome.stage_dir_rel,
        "step_phase": step.phase,
        "started_at_ts": started_at,
        "counts": counts,
        "results": [
            {
                "predicate": r.predicate,
                "status": r.status,
                "detail": r.detail,
            }
            for r in results
        ],
    })
    # Also surface a stage-event so `omicairn status` and the
    # events.jsonl tail show drift without anyone needing to crack
    # open steering_log.jsonl.
    if counts.get(steering_verify_mod.FAIL, 0) or counts.get(
        steering_verify_mod.UNRECOGNIZED, 0
    ):
        stage_mod.append_event(ws.root, {
            "kind": EventKind.STEERING_VERIFIER_WARNING,
            "phase": outcome.phase,
            "stage": outcome.stage_dir_rel,
            "fail": counts.get(steering_verify_mod.FAIL, 0),
            "unrecognized": counts.get(steering_verify_mod.UNRECOGNIZED, 0),
            "pass": counts.get(steering_verify_mod.PASS, 0),
        })


def _format_steering_overlay(
    step: "steering_mod.SteeringStep | None",
) -> str:
    """Render the active SteeringStep into the {{steering}} block.

    Phase prompts that opt in by including `{{steering}}` will see
    this rendered overlay. When no steering step is active (the
    common case — most runs are pure linear-walk), this returns
    "(none)" so prompts don't see a stray empty block.
    """
    if step is None:
        return "(none)"
    parts = ["USER STEERING (active for this stage):"]
    if step.prompt_overlay:
        parts.append(step.prompt_overlay)
    if step.rationale:
        parts.append(f"\nRationale: {step.rationale}")
    if step.expected_changes:
        parts.append(
            "\nExpected changes (warn-only verifiers):\n  - "
            + "\n  - ".join(step.expected_changes)
        )
    return "\n".join(parts)


def _active_steering_step(
    ws: Workspace, phase_name: str
) -> "steering_mod.SteeringStep | None":
    """Return the next-pending SteeringStep iff its phase matches.

    The orchestrator's `decide_next_phase` already consults the plan;
    by the time `_render_prompt` runs, the next pending step's phase
    should equal `phase_name`. We re-check defensively here so
    legacy linear-walk dispatches (e.g. the user invoked
    `omicairn run --phase execute` while a stale plan exists) don't
    accidentally inject the wrong steering overlay.
    """
    plan = steering_mod.read_plan(ws.root)
    if plan is None or plan.is_complete():
        return None
    nxt = plan.next_pending_step()
    if nxt is None:
        return None
    if nxt.phase and nxt.phase != phase_name:
        return None
    return nxt


# Appended to every FREE steering prompt: the release does not draft papers.
FREE_PHASE_SCOPE_RULE: str = (
    "\n\n---\n\n**Scope rule (always applies):** do NOT draft manuscripts, "
    "papers, abstracts, cover letters, or venue-formatted text, even if asked "
    "above. Deliverables are analyses, results, figures, and reports for "
    "experts (results.json, analysis.md, figures, notes). If the request asks "
    "for a manuscript, do the underlying analysis work only and say so in "
    "summary.md.\n"
)


def _render_prompt(
    phase: phases.Phase,
    ws: Workspace,
    stage_ref: stage_mod.StageRef,
    iter_count: int,
    max_iter: int,
    retry_note: str,
) -> str:
    active_step = _active_steering_step(ws, phase.name)
    if phase.name == PhaseName.FREE:
        # FREE phase: prompt comes inline from the SteeringStep, NOT
        # from a prompt_file template. The step MUST exist (the
        # orchestrator only routes to FREE via a steering plan).
        if active_step is None or not active_step.prompt_inline:
            raise RuntimeError(
                "FREE phase invoked without an active SteeringStep "
                "carrying prompt_inline. This is an orchestrator-"
                "routing bug — FREE is only reachable via a "
                "SteeringPlan step with phase='free'."
            )
        template = active_step.prompt_inline + FREE_PHASE_SCOPE_RULE
    else:
        template = (PROMPTS_DIR / phase.prompt_file).read_text()
    # The "primary" ctx: everything sourced from research_md + stage
    # state. These values are plain strings that do NOT themselves
    # contain `{{token}}` syntax.
    primary_ctx: dict[str, str] = {
        Token.TOPIC_SLUG: ws.research_md.topic_slug,
        Token.RESEARCH_MODE: ws.research_md.research_mode,
        Token.DOMAIN: ws.research_md.domain,
        Token.DATA_LOCATION: str(ws.research_md.data_location),
        Token.RESEARCH_QUESTION: ws.research_md.research_question.strip(),
        Token.PRIMARY_METRIC: ws.research_md.primary_metric,
        Token.BASELINE: ws.research_md.baseline or "unspecified",
        Token.ALLOW_WEB: str(ws.research_md.allow_web).lower(),
        Token.COMPUTE_RESOURCES: ws.research_md.compute.render_md(
            probe_host=True),
        Token.CONTRIBUTION_DIRECTIVE: contribution_mod.render_directive(
            ws.research_md.contribution_type),
        Token.CONTRIBUTION_TYPE: contribution_mod.validate(
            ws.research_md.contribution_type),
        Token.RESEARCH_ARC_DIRECTIVE: contribution_mod.render_arc_directive(
            ws.research_md.research_arc, ws.research_md.contribution_type),
        Token.RESEARCH_ARC: (
            contribution_mod.validate_arc(ws.research_md.research_arc)
            or "unset"),
        Token.RETRY_NOTE: retry_note or "(fresh attempt)",
        Token.WORKSPACE_ROOT: str(ws.root),
        Token.SHARED_CACHE_DIR: str(ws.shared_cache_dir),
        Token.STAGE_DIR: stage_ref.rel_path,
        Token.STAGE_INDEX: f"{stage_ref.index:03d}",
        Token.PHASE_NAME: phase.name,
        Token.ITER: str(iter_count),
        Token.MAX_ITER: str(max_iter),
        Token.STAGE_LISTING: stage_mod.render_stage_listing(ws.root),
        Token.STEERING: _format_steering_overlay(active_step),
    }
    # latest_<phase>_dir tokens (also primary — just pointers to dirs).
    for tok_name, tok_val in stage_mod.latest_dir_tokens(ws.root).items():
        primary_ctx[tok_name] = tok_val

    def _substitute(text: str, ctx: dict[str, str]) -> str:
        for k, v in ctx.items():
            text = text.replace("{{" + k + "}}", str(v))
        return text

    # "Secondary" ctx: values that themselves may contain `{{token}}`
    # references (e.g. a domain pack phase file that references
    # {{domain}}). Pre-substitute primary tokens in these values
    # before injecting them into the main template. Without this, a
    # `{{domain}}` inside domain_pack_guidance would survive
    # past the main substitution loop because the primary-ctx pass
    # runs BEFORE the guidance is injected.
    secondary_raw: dict[str, str] = {
        Token.DOMAIN_PACK_GUIDANCE: _domain_pack_guidance(ws, phase.name),
        Token.KNOWLEDGE_SUMMARY: _knowledge_summary(ws, phase.name),
        Token.USER_NOTES: user_notes_mod.render_notes_for_phase(
            ws.root, phase.name
        ),
        Token.ASSET_PROBE_SUMMARY: _asset_probe_summary(ws),
        Token.SHARED_LESSONS_FOR_PHASE: _shared_lessons_summary(ws, phase.name),
        Token.ROUTING_SUGGESTION: _routing_suggestion_summary(ws),
        Token.KNOWLEDGE_GRAPH_PROJECTION: kg.render_paper_projection_md(ws.root),
        Token.IDEATE_TARGETS: _ideate_targets_summary(ws),
        Token.IDEATE_SOURCE_POLICY: _ideate_source_policy(ws),
        Token.LAB_KNOWLEDGE: _lab_knowledge_summary(ws),
        Token.LAB_THEOREMS: _lab_theorems_summary(ws),
        Token.FEASIBILITY_GUIDANCE: _feasibility_guidance(ws),
    }
    secondary_ctx = {
        k: _substitute(str(v), primary_ctx)
        for k, v in secondary_raw.items()
    }

    full_ctx = {**primary_ctx, **secondary_ctx}
    return _substitute(template, full_ctx)


# ---------------------------------------------------------------------------
# Cross-check stage (synthetic, pure Python)
# ---------------------------------------------------------------------------

def _run_cross_check_stage(
    ws: Workspace, stage_index: int, s: dict[str, Any]
) -> StageOutcome:
    """Run the synthetic cross_check phase.

    Finds the most recent done execute stage, runs
    cross_check.build_execute_verdict on it, writes
    ``verdict.json`` + ``findings.md`` + ``done.flag`` to a fresh
    cross_check stage directory, and returns its outcome.
    """
    phase = phases.CROSS_CHECK
    stage_dir = stage_mod.create_stage_dir(ws.root, stage_index, phase.name)
    rel = stage_dir.name

    # Per-stage code provenance (phase 1, additive): stamp the engine SHA
    # live at stage creation so this stage is attributable to an exact
    # engine state even though provenance.jsonl is per-invocation.
    # Best-effort — never fails a stage.
    provenance_mod.write_stage_stamp(stage_dir)

    # Record start BEFORE doing any work, matching _run_llm_stage. A
    # mid-flight crash still counts against max_iters_per_phase this way,
    # and `omicairn status` always shows the current_stage correctly.
    state.record_stage_start(s, stage_index, phase.name)
    state.write(ws.state_path, s)

    execute_latest = stage_mod.latest_stage_of(ws.root, PhaseName.EXECUTE)
    if execute_latest is None:
        # Shouldn't happen — cross_check only runs after execute — but
        # fail gracefully.
        msg = "cross_check: no prior execute stage found; cannot run"
        verdict_mod.write_findings(stage_dir, msg + "\n")
        v = verdict_mod.halt(reason=msg)
        verdict_mod.write_verdict(stage_dir, v)
        verdict_mod.write_done_flag(stage_dir)
        return StageOutcome(
            stage_index=stage_index,
            phase=phase.name,
            stage_dir_rel=rel,
            status="DONE",
            attempts=1,
            wall_seconds=0.0,
            decision=Decision.HALT,
            note=msg,
        )

    # Dry-run verdict override: let tests force a specific decision
    # without staging a real failure. Uses the same env-var reader as
    # claude_runner so the two share a single source of truth.
    forced = _dry_run_forced_verdict()
    if forced:
        v = verdict_mod.Verdict(
            decision=forced,
            reason=f"dry-run forced verdict {forced!r}",
            evidence=[],
        )
        if not v.is_valid():
            # Fall back to proceed if the forced value is invalid.
            v = verdict_mod.proceed(
                reason=f"invalid forced verdict {forced!r}, defaulting to proceed"
            )
        findings = f"# cross_check findings (dry-run forced)\n\n{v.reason}\n"
    else:
        t0 = time.time()
        v, cc = cross_check.build_execute_verdict(
            execute_latest.path,
            domain=ws.research_md.domain,
        )
        _ = time.time() - t0
        findings_lines = ["# cross_check findings", ""]
        if cc.errors:
            findings_lines.append("## Errors")
            findings_lines.extend(f"- {e}" for e in cc.errors)
            findings_lines.append("")
        if cc.warnings:
            findings_lines.append("## Warnings")
            findings_lines.extend(f"- {w}" for w in cc.warnings)
            findings_lines.append("")
        if not cc.errors and not cc.warnings:
            findings_lines.append("All cross-artifact checks passed.")
        findings = "\n".join(findings_lines) + "\n"

    # Write the 1-line summary.md so stage_listing shows a useful entry.
    summary_line = (
        f"cross_check verdict: {v.decision} — {v.reason[:120]}"
    )
    (stage_dir / SUMMARY_FILE).write_text(summary_line + "\n")
    verdict_mod.write_findings(stage_dir, findings)
    verdict_mod.write_verdict(stage_dir, v)
    verdict_mod.write_done_flag(stage_dir)

    # Contract check: cross_check required_outputs are verdict.json,
    # findings.md, done.flag. We just wrote all three, but verify so
    # any future bug that drops a file is caught loudly.
    contract = contracts.verify(phase, stage_dir)
    if not contract.ok:
        # Shouldn't happen — we just wrote all of them — but defensive.
        return StageOutcome(
            stage_index=stage_index,
            phase=phase.name,
            stage_dir_rel=rel,
            status="CONTRACT_FAIL",
            attempts=1,
            wall_seconds=0.0,
            decision=None,
            note=f"contract fail: {contract.summary()}",
        )

    # Record in STATE.md: stage_done + decision. (stage_start was
    # recorded at the top of this function before any work began.)
    state.record_stage_done(s, stage_index, phase.name)
    state.record_decision(s, v.decision)
    if v.decision in BACKTRACK_DECISIONS:
        state.record_backtrack(s)
    state.write(ws.state_path, s)

    # `evidence_count` reflects len(verdict.evidence). For cross_check
    # this is the warning/error count (warnings on `proceed`, errors on
    # `retry_*`); for the analysis review it is the number of
    # supporting items the agent recorded. Field used to be misnamed
    # `errors` which read as a hard-failure count even on `proceed`.
    stage_mod.append_event(ws.root, {
        "kind": EventKind.STAGE_DONE,
        "stage": rel,
        "phase": phase.name,
        "decision": v.decision,
        "evidence_count": len(getattr(v, "evidence", [])),
    })

    return StageOutcome(
        stage_index=stage_index,
        phase=phase.name,
        stage_dir_rel=rel,
        status="DONE",
        attempts=1,
        wall_seconds=0.0,
        decision=v.decision,
        note=v.reason,
    )


# ---------------------------------------------------------------------------
# LLM stage
# ---------------------------------------------------------------------------

def _inline_post_stage_check(
    phase: phases.Phase, stage_dir: pathlib.Path, ws: Workspace
) -> cross_check.CrossCheckResult:
    """Per-phase post-stage verification (LIVE-run path, post-claude).

    EXECUTE is intentionally OK here: a freshly-finished execute stage's
    semantic check runs in the SEPARATE synthetic CROSS_CHECK phase
    (`build_execute_verdict`), not inline, so re-running check_execute here
    would double-report. The resume-time finalize sweep uses the broader
    `_substantive_checker_result` (which DOES cover execute) because it has
    no following CROSS_CHECK stage to lean on yet.
    """
    if phase.name == PhaseName.EXECUTE:
        return cross_check.CrossCheckResult(ok=True, warnings=[], errors=[])
    return _substantive_checker_result(ws, phase.name, stage_dir)


def _seed_knowledge_graph(ws: Workspace) -> None:
    """Seed the workspace knowledge graph with goal + pre-registered
    hypothesis nodes from RESEARCH.md (core belief 12). Idempotent
    (stable node ids), so it is safe to call at the start of every run.

    Graph writing is additive and must never block a run; failures are
    logged as an event and swallowed.
    """
    try:
        goals = [
            {"id": g.id, "question": g.question, "hypotheses": list(g.hypotheses)}
            for g in ws.research_md.goals
        ]
        counts = kg.seed_goals(ws.root, goals, source_phase=PhaseName.SCOPE)
        stage_mod.append_event(ws.root, {
            "kind": EventKind.KNOWLEDGE_GRAPH_SEEDED,
            "goals": counts["goals"],
            "preregistered_hypotheses": counts["hypotheses"],
        })
    except (OSError, ValueError, KeyError) as e:
        stage_mod.append_event(ws.root, {
            "kind": EventKind.KNOWLEDGE_GRAPH_SEED_ERROR, "error": str(e),
        })


def _ingest_stage_graph_updates(
    ws: Workspace, stage_dir: pathlib.Path, phase_name: str
) -> None:
    """If a completed stage dropped a graph_updates.json, fold it into
    the workspace knowledge graph. This is the "assemble the knowledge
    base on the run" path (core belief 12): one EXECUTE pass can register
    many result_refs, findings, and on-the-fly hypotheses instead of
    discarding every signal that isn't the one pre-registered hypothesis.

    Additive + silent when the file is absent (legacy cases unaffected).
    On-the-fly registration is gated by `allow_on_the_fly_hypotheses`:
    when false, the budget is 0 and the single-H control accrues no
    exploratory hypotheses.
    """
    if phase_name not in _GRAPH_INGEST_PHASES:
        return
    updates_path = stage_dir / KG_UPDATES_FILE
    if not updates_path.exists():
        return
    try:
        updates = json.loads(updates_path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        stage_mod.append_event(ws.root, {
            "kind": EventKind.KNOWLEDGE_GRAPH_INGEST_ERROR,
            "stage": stage_dir.name, "error": str(e),
        })
        return
    max_otf = (
        int(ws.research_md.max_on_the_fly_hypotheses)
        if ws.research_md.allow_on_the_fly_hypotheses else 0
    )
    try:
        counts = kg.ingest_graph_updates(
            ws.root, updates, source_phase=phase_name,
            source_stage=stage_dir.name, max_on_the_fly=max_otf,
        )
    except (OSError, ValueError, KeyError) as e:
        stage_mod.append_event(ws.root, {
            "kind": EventKind.KNOWLEDGE_GRAPH_INGEST_ERROR,
            "stage": stage_dir.name, "error": str(e),
        })
        return
    _warns = counts.get("warnings", [])
    stage_mod.append_event(ws.root, {
        "kind": EventKind.KNOWLEDGE_GRAPH_UPDATED,
        "stage": stage_dir.name,
        "phase": phase_name,
        "result_refs": counts["result_refs"],
        "findings": counts["findings"],
        "on_the_fly": counts["on_the_fly"],
        "edges": counts["edges"],
        "status_advanced": counts["status_advanced"],
        "warnings": len(_warns),
        # Surface the actual diagnostics (honesty refusals, illegal-
        # transition conflicts) — a bare count hides WHY a hypothesis
        # silently stayed in `testing` (audit H2). Capped to stay log-sane.
        "warning_details": [str(w)[:240] for w in _warns[:10]],
    })


def _ingest_ideation_stage(ws: Workspace, stage_dir: pathlib.Path) -> None:
    """Fold an IDEATE stage's directions.json into the knowledge graph
    (source nodes + proposed-direction goal nodes + cites edges). The
    proposed directions are then readable by SCOPE/PLAN via
    {{knowledge_graph_projection}} and adoptable via `omicairn ideate
    --adopt`. Additive + silent when the file is absent."""
    updates_path = stage_dir / IDEATE_DIRECTIONS_FILE
    if not updates_path.exists():
        return
    try:
        payload = json.loads(updates_path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        stage_mod.append_event(ws.root, {
            "kind": EventKind.IDEATION_INGEST_ERROR,
            "stage": stage_dir.name, "error": str(e),
        })
        return
    try:
        counts = kg.ingest_ideation(
            ws.root, payload, source_phase=PhaseName.IDEATE,
            source_stage=stage_dir.name,
        )
    except (OSError, ValueError, KeyError) as e:
        stage_mod.append_event(ws.root, {
            "kind": EventKind.IDEATION_INGEST_ERROR,
            "stage": stage_dir.name, "error": str(e),
        })
        return
    _iwarns = counts.get("warnings", [])
    stage_mod.append_event(ws.root, {
        "kind": EventKind.IDEATION_INGESTED,
        "stage": stage_dir.name,
        "sources": counts["sources"],
        "directions": counts["directions"],
        "cite_edges": counts["cite_edges"],
        "warnings": len(_iwarns),
        "warning_details": [str(w)[:240] for w in _iwarns[:10]],
    })
    # Bridge IDEATE's surveyed sources into literature/ (metadata only —
    # no network here, safe in dry-run/tests) so the downstream pipeline
    # inherits the bibliography. PDF download happens in cmd_ideate when
    # web is enabled. Additive + idempotent.
    _export_ideate_sources_to_literature(ws, download=False)


def _export_ideate_sources_to_literature(ws: Workspace, download: bool) -> None:
    """Map the graph's `source` nodes to Paper entries and merge them into
    <ws>/literature/papers.json (+ abstracts, + LITERATURE_INDEX, +
    optional PDF download). Best-effort: never fails the run."""
    try:
        from . import literature
        srcs = [
            {
                "title": n.title,
                "summary": n.body,
                "source_type": n.extra.get("source_type", "other"),
                "url": n.extra.get("url", ""),
                "author": n.extra.get("author", ""),
                "year": n.extra.get("year"),
            }
            for n in kg.query_nodes(ws.root, node_type="source")
        ]
        if not srcs:
            return
        counts = literature.export_sources_to_literature(
            ws.root / "literature", srcs,
            domain=ws.research_md.domain, download=download,
        )
        stage_mod.append_event(ws.root, {
            "kind": EventKind.IDEATE_SOURCES_TO_LITERATURE,
            "added": counts["added"], "downloaded": counts["downloaded"],
            "download_enabled": download,
        })
    except (OSError, ValueError, KeyError, ImportError) as e:
        stage_mod.append_event(ws.root, {
            "kind": EventKind.IDEATE_SOURCES_TO_LITERATURE_ERROR, "error": str(e),
        })


def _process_feasibility_gate(
    ws: Workspace, stage_dir: pathlib.Path
) -> str:
    """SCOPE-time feasibility / statistical-power gate routing.

    Called after a SCOPE stage passes its contract. Returns the routing
    Decision (recorded by the caller). The gate keys ONLY on statistical
    power / data feasibility, NEVER on the hypothesised direction of any
    result (neutrality, belief 13) — a well-powered NULL proceeds cleanly.

    Behaviour:
      - Gate not active for this case (config applies_to / mode) → proceed.
      - feasibility.json absent (exploratory / agent opted out) → proceed.
      - feasibility.json present but invalid → proceed (warn-only event;
        the gate is a safety rail, never a hard blocker that strands a run
        on a schema typo — the validity invariant is logged, not enforced
        as a halt).
      - Valid + verdict feasible → proceed.
      - Valid + underpowered/needs_data → write a verdict.json into the
        scope stage (carrying the remediation decision + rationale so the
        retry note plumbing surfaces it) and return back_to_scope/plan.
    """
    cfg = ws.research_md.feasibility_config
    active = feasibility_mod.gate_applies(
        cfg,
        research_mode=ws.research_md.research_mode,
        pre_registration=ws.research_md.pre_registration,
    )
    feas_path = stage_dir / FEASIBILITY_FILE
    if not active:
        if feas_path.exists():
            stage_mod.append_event(ws.root, {
                "kind": EventKind.FEASIBILITY_GATE_INACTIVE,
                "stage": stage_dir.name,
                "applies_to": cfg.applies_to,
                "research_mode": ws.research_md.research_mode,
            })
        return Decision.PROCEED
    if not feas_path.exists():
        # Gate is active but SCOPE produced no feasibility.json. Surface
        # it as a warning event (the prompt asks for it on confirmatory
        # runs) but do not block — absence is treated as "no power
        # concern declared", a conservative proceed.
        stage_mod.append_event(ws.root, {
            "kind": EventKind.FEASIBILITY_GATE_MISSING,
            "stage": stage_dir.name,
            "applies_to": cfg.applies_to,
        })
        return Decision.PROCEED

    validation = feasibility_mod.validate(feas_path)
    if not validation.ok or validation.feasibility is None:
        stage_mod.append_event(ws.root, {
            "kind": EventKind.FEASIBILITY_GATE_INVALID,
            "stage": stage_dir.name,
            "errors": validation.errors[:10],
        })
        return Decision.PROCEED

    feas = validation.feasibility
    decision = feasibility_mod.decision_for(feas)
    stage_mod.append_event(ws.root, {
        "kind": EventKind.FEASIBILITY_GATE_EVALUATED,
        "stage": stage_dir.name,
        "verdict": feas.verdict,
        "recommended_action": feas.recommended_action,
        "decision": decision,
        "units": [
            {"name": u.name, "extractable_n": u.extractable_n_estimate,
             "reference_n": u.reference_n, "adequacy": u.adequacy}
            for u in feas.analysis_units
        ],
    })
    if decision == Decision.PROCEED:
        return Decision.PROCEED

    # Remediation route: write a verdict.json into the scope stage so the
    # existing verdict-routing (decide_next_phase) + retry-note plumbing
    # carry the rationale into the next stage's prompt. proposed_fix is
    # the feasibility rationale; evidence lists the inadequate units.
    evidence = [
        f"{u.name}: extractable_n={u.extractable_n_estimate} "
        f"vs reference_n={u.reference_n} ({u.adequacy})"
        for u in feas.analysis_units
    ]
    v = verdict_mod.Verdict(
        decision=decision,
        reason=(
            f"feasibility gate: verdict={feas.verdict}, "
            f"recommended_action={feas.recommended_action}. {feas.rationale}"
        )[:500],
        evidence=evidence,
        proposed_fix=feas.rationale,
    )
    verdict_mod.write_verdict(stage_dir, v)
    return decision


def _run_llm_stage(
    ws: Workspace, stage_index: int, phase: phases.Phase, s: dict[str, Any]
) -> StageOutcome:
    """Create a stage dir, render the prompt, call claude -p, verify."""
    stage_dir = stage_mod.create_stage_dir(ws.root, stage_index, phase.name)
    rel = stage_dir.name

    # Per-stage code provenance (phase 1, additive): stamp the engine SHA
    # live at stage creation so this stage is attributable to an exact
    # engine state even though provenance.jsonl is per-invocation.
    # Best-effort — never fails a stage.
    provenance_mod.write_stage_stamp(stage_dir)

    # Record start in STATE.md before we launch.
    state.record_stage_start(s, stage_index, phase.name)
    state.write(ws.state_path, s)

    stage_ref = stage_mod.StageRef(
        index=stage_index,
        phase=phase.name,
        path=stage_dir,
        rel_path=rel,
    )

    iter_count = s["iteration_counts"].get(phase.name, 0)
    max_iter = int(s["bounds"]["max_iters_per_phase"])

    # Retry loop within this stage attempt: the stage itself gets ONE
    # claude invocation; if the contract fails, we do NOT automatically
    # retry the same stage. Instead the orchestrator's outer loop will
    # pick a new stage index for a retry.
    #
    # On a retry — i.e. iter_count > 0 OR the most-recent verdict from a
    # prior verdict-emitting stage said retry_<phase> / back_to_<phase>
    # — populate retry_note with the prior verdict's actionable
    # feedback (proposed_fix + target_file_hints + score + lowest
    # dimensions). Without this the agent has to discover the prior
    # review's findings by hand, which is what the morphogen / EMT
    # 78→80→halt loop demonstrated empirically.
    retry_note = _build_retry_note(ws, phase.name, iter_count)
    append_system = (
        "You are one phase of OmiCairn, a fully automated research "
        "system using Claude Code as the agentic backbone. Your phase is "
        f"{phase.name.upper()} (stage {rel}, iteration {iter_count}/{max_iter}). "
        f"Read CLAUDE.md and RESEARCH.md for rules and the research "
        f"question. Read STATE.md for prior-stage context. Write your "
        f"contract files INSIDE {rel}/ and nowhere else. Write done.flag "
        f"LAST. Phase: {phase.description}"
    )

    prompt = _render_prompt(phase, ws, stage_ref, iter_count, max_iter, retry_note)
    log = ws.log_for(rel, iter_count)
    tools = tuple(phases.tools_with_web(phase, ws.research_md.allow_web))

    # Effective per-phase wall budget. RESEARCH.md's `max_phase_wall_minutes`
    # is the user's authoritative declaration of how long this phase may run
    # — it can either raise or lower the phase's built-in default.
    #
    # Earlier (commit 600479f) we clamped via `min(phase.default, user)`,
    # which meant a user requesting MORE time silently got the lower default.
    # The parkinson real-run report §3.1 showed every EXECUTE phase timing
    # out at 3600s despite RESEARCH.md declaring 240*60=14400s. The fix:
    # use the RESEARCH.md value when present, fall back to phase.default
    # only when unset. This matches the user-visible contract: "what you
    # write in RESEARCH.md is what gets enforced."
    # Stratified per-phase override (omicairn_config.yaml §3.4). When a
    # phase appears in `max_phase_wall_minutes_by_phase`, its value
    # wins. Otherwise fall back to the case-wide `max_phase_wall_minutes`.
    # Otherwise fall back to the phase's built-in default. parkinson
    # asyn-propagation needed 6+ h for an iterated EXECUTE while
    # other phases (SCOPE, PLAN) didn't need more than the 4 h
    # default.
    #
    # EXECUTE-phase wall FLOOR (GPU/multimodal): on top of the resolution
    # above, a confidently GPU-heavy / multimodal EXECUTE phase gets its
    # effective budget RAISED to GPU_EXECUTE_WALL_FLOOR_MINUTES if lower —
    # never lowered below an explicit higher user value. See the constant's
    # docstring + the 2026-06-15 wave-2 4/6-timeout incident.
    effective_timeout_s, floored_from_s = _resolve_phase_wall_seconds(
        ws.research_md, phase.name, phase.timeout_seconds,
    )
    if floored_from_s is not None:
        stage_mod.append_event(ws.root, {
            "kind": EventKind.EXECUTE_WALL_FLOOR_APPLIED,
            "stage": rel,
            "phase": phase.name,
            "from_seconds": floored_from_s,
            "to_seconds": effective_timeout_s,
            "floor_minutes": GPU_EXECUTE_WALL_FLOOR_MINUTES,
            "reason": "GPU-heavy/multimodal run; raised EXECUTE wall to floor",
        })

    # EXECUTE-phase inactivity-watchdog FLOOR (GPU/multimodal): the SIBLING of
    # the wall floor above. For a confidently GPU-heavy/multimodal EXECUTE
    # phase, raise the inactivity timeout to GPU_INACTIVITY_FLOOR_SECONDS so a
    # slow-but-live per-shard GPU job (quiet >45min between heartbeat touches)
    # isn't false-killed (d3 wm-dynamics-decouple incident). The actual raise
    # is applied in claude_runner as max(configured, floor) and only to an
    # already-active watchdog; here we only pick the floor value and log the
    # raise when it would actually take effect.
    inactivity_floor_s = _resolve_inactivity_floor_seconds(
        ws.research_md, phase.name,
    )
    if inactivity_floor_s is not None:
        # Compute the would-be-resolved value (env/default; no override at the
        # pipeline call site) purely to decide whether to log — and to record
        # an accurate from→to. claude_runner re-applies the same floor.
        configured_inactivity_s = _resolved_inactivity_timeout_s()
        # Only an ACTIVE (>0) watchdog below the floor is actually raised.
        if 0 < configured_inactivity_s < inactivity_floor_s:
            stage_mod.append_event(ws.root, {
                "kind": EventKind.EXECUTE_INACTIVITY_FLOOR_APPLIED,
                "stage": rel,
                "phase": phase.name,
                "from_seconds": round(configured_inactivity_s, 1),
                "to_seconds": inactivity_floor_s,
                "floor_seconds": GPU_INACTIVITY_FLOOR_SECONDS,
                "reason": (
                    "GPU-heavy/multimodal run; raised EXECUTE inactivity "
                    "watchdog to floor"
                ),
            })

    # ── Stage TIME-LIMIT MODE (dynamic vs hard) ──────────────────────────
    # DYNAMIC (default) demotes the per-phase WALL from an unconditional kill
    # to a LIVENESS-GATED SOFT CEILING for the EXECUTE (execute-class) phase,
    # so a legitimately-long-but-PROGRESSING GPU/FT/inference arm is never
    # truncated (the d7 LLaVA-NeXT held-out drop). HARD keeps the historical
    # unconditional wall kill. Non-execute phases keep the hard wall in BOTH
    # modes (a 6h REVIEW is a bug, not science). The mode resolves through
    # constants.stage_time_limit_mode: env OMICAIRN_STAGE_TIME_LIMIT_MODE >
    # research_md.stage_time_limit_mode (budgets.*) > default (dynamic).
    _mode = _resolve_stage_time_limit_mode(ws.research_md)
    _is_execute = phase.name == PhaseName.EXECUTE
    _dynamic_execute = _is_execute and _mode == STAGE_TIME_LIMIT_MODE_DYNAMIC
    wall_kill_requires_stall = _dynamic_execute
    hard_ceiling_seconds: float | None = (
        float(EXECUTE_HARD_CEILING_SECONDS) if _dynamic_execute else None
    )
    if _dynamic_execute:
        # Seed the soft ceiling from the dynamic estimator. The estimate is
        # NOT a hard kill — it only sets where soft-extension logging begins
        # (and is the SEED wall passed as timeout_seconds), floored at the
        # resolved config/GPU-floor value. An under-estimate is covered by the
        # liveness-gated soft extension; an over-estimate only delays logging.
        _history = _prior_execute_wall_seconds(ws)
        estimated_s, basis = estimate_execute_wall_seconds(
            ws.research_md, phase.name, effective_timeout_s, _history,
        )
        if estimated_s > effective_timeout_s:
            stage_mod.append_event(ws.root, {
                "kind": EventKind.EXECUTE_WALL_FLOOR_APPLIED,
                "stage": rel,
                "phase": phase.name,
                "from_seconds": effective_timeout_s,
                "to_seconds": estimated_s,
                "reason": (
                    f"dynamic mode: estimator seeded soft wall (basis={basis})"
                ),
            })
            effective_timeout_s = estimated_s

    t0 = time.time()
    result = claude_run(
        prompt=prompt,
        workspace_root=ws.root,
        data_dir=ws.data_dir,
        allowed_tools=tools,
        model=ws.research_md.claude_model,
        timeout_seconds=effective_timeout_s,
        log_path=log,
        append_system_prompt=append_system,
        max_budget_usd=ws.research_md.max_phase_budget_usd,
        stage_dir=stage_dir,
        phase_name=phase.name,
        inactivity_timeout_floor_s=inactivity_floor_s,
        wall_kill_requires_stall=wall_kill_requires_stall,
        hard_ceiling_seconds=hard_ceiling_seconds,
    )
    wall = time.time() - t0
    attempts = 1

    if result.returncode == -2:
        state.set_status(s, RunStatus.HALTED, reason="claude CLI not found")
        state.write(ws.state_path, s)
        return StageOutcome(
            stage_index=stage_index,
            phase=phase.name,
            stage_dir_rel=rel,
            status="CLI_MISSING",
            attempts=attempts,
            wall_seconds=wall,
            decision=None,
            note="claude CLI not found on PATH",
        )

    if result.timed_out:
        stage_mod.append_event(ws.root, {
            "kind": EventKind.STAGE_TIMEOUT,
            "stage": rel,
            "phase": phase.name,
            "seconds": effective_timeout_s,
        })
        return StageOutcome(
            stage_index=stage_index,
            phase=phase.name,
            stage_dir_rel=rel,
            status="TIMEOUT",
            attempts=attempts,
            wall_seconds=wall,
            decision=None,
            note=f"timed out after {effective_timeout_s}s",
        )

    # Claude Code account-level session cap (5-hour window). Distinct
    # from CONTRACT_FAIL: the agent never had a chance to write outputs
    # because the CLI cut its session. Resume requires waiting until
    # the stated reset time. We surface SESSION_LIMIT so a wrapper /
    # cron / human can schedule the resume rather than treating this
    # as a contract failure that warrants a retry on the same window.
    if result.session_cap is not None:
        cap_note_parts = []
        reset_text = result.session_cap.get("reset_text")
        reset_tz = result.session_cap.get("reset_tz")
        reset_raw_line = result.session_cap.get("reset_raw_line")
        if reset_text:
            cap_note_parts.append(f"resets {reset_text}")
        if reset_tz:
            cap_note_parts.append(f"({reset_tz})")
        cap_note = " ".join(cap_note_parts) or (
            f"reset hint not parsed; raw: {reset_raw_line}"
            if reset_raw_line else "reset hint not parsed"
        )
        stage_mod.append_event(ws.root, {
            "kind": EventKind.CLAUDE_SESSION_LIMIT,
            "stage": rel,
            "phase": phase.name,
            "reset_text": reset_text,
            "reset_tz": reset_tz,
            # Wrappers can fall back to scheduling on reset_raw_line when
            # the structured fields are None.
            "reset_raw_line": reset_raw_line,
        })
        # Don't flip STATE.md status — the run is paused, not halted.
        # `events.jsonl` is the source of truth for "why did this stop";
        # `paused_reason` surfaces it in `omicairn status` and is cleared on
        # the next stage start.
        state.set_paused(s, f"claude_session_limit: {cap_note}")
        state.write(ws.state_path, s)
        return StageOutcome(
            stage_index=stage_index,
            phase=phase.name,
            stage_dir_rel=rel,
            status="SESSION_LIMIT",
            attempts=attempts,
            wall_seconds=wall,
            decision=None,
            note=f"claude session limit hit; {cap_note}",
        )

    contract = contracts.verify(phase, stage_dir)
    cc_result = _inline_post_stage_check(phase, stage_dir, ws)
    # A verdict.json that exists but is unreadable or names a decision
    # outside the allowed set (e.g. a legacy `retry_write`) must not
    # silently end the run as DONE — re-run the stage instead.
    verdict_invalid = (
        phase.name in VERDICT_PHASES
        and phase.name in CLAUDE_CODE_PHASES
        and _has_invalid_verdict(stage_dir)
    )

    if contract.ok and cc_result.ok and not verdict_invalid:
        # Write done.flag if the agent forgot to.
        flag = stage_dir / DONE_FLAG
        if not flag.exists():
            flag.write_text("1\n")
        state.record_stage_done(s, stage_index, phase.name)

        # Assemble the knowledge graph on the run: fold any
        # graph_updates.json this stage produced into the workspace
        # graph (core belief 12). Additive + silent when absent.
        _ingest_stage_graph_updates(ws, stage_dir, phase.name)
        # IDEATE stages fold their directions.json (sources + proposed
        # directions) into the graph instead.
        if phase.name == PhaseName.IDEATE:
            _ingest_ideation_stage(ws, stage_dir)

        # Verdict-emitting LLM phases (the analysis review) write
        # verdict.json as part of their contract. Read it and record
        # the decision. Non-verdict phases implicitly proceed.
        decision = None
        if phase.name in VERDICT_PHASES and phase.name in CLAUDE_CODE_PHASES:
            v = verdict_mod.read_verdict(stage_dir)
            if v is not None:
                decision = v.decision
                state.record_decision(s, decision)
                if decision in BACKTRACK_DECISIONS:
                    state.record_backtrack(s)
                # #133 zero-progress tracking (DYNAMIC iter-mode spin-catcher).
                # Fingerprint this verdict's forward-progress signal and record
                # whether the decision RE-OPENS this phase (a RETRY_* / BACK_TO_*
                # that will run it again) vs ADVANCES. A re-open with an
                # identical fingerprint to last time is a stall; the halt itself
                # is applied at the bounds seam. Recorded for ALL verdict
                # phases; only consulted in DYNAMIC mode.
                _fp = _verdict_progress_fingerprint(v, decision)
                state.record_phase_progress(
                    s, phase.name, _fp,
                    reopened=decision not in _ADVANCING_DECISIONS,
                )
            else:
                # No verdict from the LLM — assume done.
                decision = Decision.DONE
                state.record_decision(s, decision)
        elif phase.name == PhaseName.SCOPE:
            # SCOPE-time feasibility / statistical-power gate. When the
            # gate is active for the case and SCOPE emitted a
            # feasibility.json verdict of underpowered/needs_data, route
            # to remediation (back_to_scope/plan) instead of marching
            # into a doomed confirmatory pipeline. A feasible verdict (a
            # well-powered NULL included) proceeds cleanly. The gate
            # writes a verdict.json into the scope stage so the normal
            # verdict-routing + backtrack accounting applies; absence of
            # feasibility.json (exploratory / gate-off) → plain proceed.
            decision = _process_feasibility_gate(ws, stage_dir)
            state.record_decision(s, decision)
            if decision in BACKTRACK_DECISIONS:
                state.record_backtrack(s)
        else:
            # Non-verdict phase implicitly proceeds.
            state.record_decision(s, Decision.PROCEED)
        state.write(ws.state_path, s)

        stage_mod.append_event(ws.root, {
            "kind": EventKind.STAGE_DONE,
            "stage": rel,
            "phase": phase.name,
            "decision": decision or Decision.PROCEED,
            "wall": round(wall, 1),
        })
        # Sweep consumed once-notes for this phase. Idempotent on empty.
        user_notes_mod.consume_once_notes(ws.root, phase.name)
        note = f"ok ({wall:.0f}s)"
        if cc_result.warnings:
            note += f" [cross_check warnings: {len(cc_result.warnings)}]"
        return StageOutcome(
            stage_index=stage_index,
            phase=phase.name,
            stage_dir_rel=rel,
            status="DONE",
            attempts=attempts,
            wall_seconds=wall,
            decision=decision or Decision.PROCEED,
            note=note,
        )

    # Contract or cross-check failed — leave the stage in place as an
    # incomplete attempt for post-hoc inspection, and return
    # CONTRACT_FAIL so the outer loop can decide whether to retry.
    reasons = []
    if not contract.ok:
        reasons.append(f"contract: {contract.summary()}")
    if not cc_result.ok:
        reasons.append(f"cross_check: {cc_result.summary()}")
    if verdict_invalid:
        # The agent writes done.flag last; drop it so the failed stage is
        # not counted as done on a later resume.
        (stage_dir / DONE_FLAG).unlink(missing_ok=True)
        allowed = ", ".join(sorted(NON_TERMINAL_DECISIONS | TERMINAL_DECISIONS))
        reasons.append(
            f"verdict: {VERDICT_FILE} unreadable or decision not in {{{allowed}}}"
        )
    note = " | ".join(reasons)
    stage_mod.append_event(ws.root, {
        "kind": EventKind.STAGE_CONTRACT_FAIL,
        "stage": rel,
        "phase": phase.name,
        "reasons": note,
    })
    state.write(ws.state_path, s)
    return StageOutcome(
        stage_index=stage_index,
        phase=phase.name,
        stage_dir_rel=rel,
        status="CONTRACT_FAIL",
        attempts=attempts,
        wall_seconds=wall,
        decision=None,
        note=note,
    )


# ---------------------------------------------------------------------------
# Outer stage loop
# ---------------------------------------------------------------------------

def run_next_stage(
    ws: Workspace, forced_phase: str | None = None
) -> StageOutcome | None:
    """Run one stage and return its outcome.

    Returns None if the pipeline is done (no next phase) or halted.

    `forced_phase`, if given, bypasses `decide_next_phase` and runs the
    specified phase. This is how CLI commands like
    ``omicairn run <workspace> --add-stage execute`` manually insert a
    stage.
    """
    _skip_removed_phase_steering_steps(ws)
    s = state.read(ws.state_path)
    if s.get("status") in (RunStatus.DONE, RunStatus.HALTED):
        # A terminal run normally short-circuits — EXCEPT when a pending,
        # valid steering plan exists. `omicairn steer` on a finished run used
        # to write a valid plan that the next `omicairn run` silently ignored
        # (it returned None here, BEFORE the steering dequeue), forcing the
        # user to hand-flip status (d7 steering bug, 2026-06-10). Auto-reset
        # done/halted → in_progress (clearing halted/pause markers) so the
        # steered stage dispatches. A genuinely-done workspace with NO
        # pending plan still returns None below.
        if not steering_mod.has_pending_plan(ws.root):
            return None
        prior_status = s.get("status")
        if state.reset_for_steering(s):
            state.write(ws.state_path, s)
            stage_mod.append_event(ws.root, {
                "kind": EventKind.STEERING_REACTIVATED_TERMINAL_RUN,
                "from_status": prior_status,
            })

    # Any new attempt clears the prior pause marker so `omicairn status`
    # never carries a stale SESSION_LIMIT message into a subsequent
    # halt or success. Doing it here (rather than in record_stage_start)
    # also covers the bounds_exceeded path that returns before the
    # stage actually starts.
    if s.get("paused_reason"):
        state.clear_paused(s)
        state.write(ws.state_path, s)

    # Resume-time reconciliation (d5 fix, 2026-06-11): finalize any stage
    # that completed real work but crashed before stamping done.flag. This
    # runs BEFORE decide_next_phase so a valid-but-unstamped execute stage
    # is seen as DONE and the pipeline advances FORWARD from it — instead
    # of re-routing to EXECUTE, creating empty retry stages, and burning
    # iters to the cap on missing-results contract fails (which nearly
    # stranded 8.9h of completed deepening work). The predicate is strict
    # (all substantive required_outputs present + the phase's substantive
    # checker passes), so a genuinely-incomplete stage is never rescued.
    if _finalize_valid_unstamped_stages(ws):
        # The sweep wrote STATE.md directly; re-read so the local copy
        # doesn't clobber its decision/stage_done bookkeeping on later
        # state.write calls in this function.
        s = state.read(ws.state_path)

    phase_name = forced_phase or decide_next_phase(ws)
    if phase_name is None:
        # No next phase — pipeline is either finished cleanly or the
        # last verdict explicitly halted it. Distinguish by looking at
        # the most recent verdict. Without this, a HALT verdict from
        # cross_check or review would leave the run marked DONE,
        # silently losing the difference between "analysis finished"
        # and "pipeline refused to continue" — see orchestrator
        # docstring: "halt → stop, mark halted".
        if s.get("status") not in (RunStatus.DONE, RunStatus.HALTED):
            final_status, final_reason = _final_status_from_last_verdict(ws)
            state.set_status(s, final_status, reason=final_reason)
            state.write(ws.state_path, s)
        return None

    # Reconcile the per-phase iteration cap with the LIVE config before the
    # bounds check. STATE.md freezes `max_iters_per_phase` at `init` time, so
    # a run created under an older/smaller cap (or before the case's
    # omicairn_config raised it) would halt at the stale value even though the
    # config now permits more iterations (the d7 extension hit this: config
    # said 8, STATE.md still said 5 from init, execute halted at 5). Refresh
    # UPWARD only — honor a raised cap for an in-flight run, but never
    # silently LOWER a running cap mid-flight (that could strand a run that
    # legitimately needs the iterations it was started with).
    _live_cap = int(ws.research_md.max_iters_per_phase)
    _state_cap = int(s.get("bounds", {}).get(
        "max_iters_per_phase", _live_cap))
    if _live_cap > _state_cap:
        s.setdefault("bounds", {})["max_iters_per_phase"] = _live_cap
        state.write(ws.state_path, s)
        stage_mod.append_event(ws.root, {
            "kind": EventKind.MAX_ITERS_RECONCILED_FROM_CONFIG,
            "phase": phase_name,
            "from": _state_cap,
            "to": _live_cap,
        })

    # Resolve the stage ITERATION mode (#133): env OMICAIRN_STAGE_ITER_MODE >
    # research_md.stage_iter_mode (budgets.*) > default (dynamic). Sibling of
    # the stage_time_limit_mode resolution above; one coherent stage-bounds
    # config, two orthogonal axes.
    _iter_mode = _resolve_stage_iter_mode(ws.research_md)

    # DYNAMIC-mode zero-progress halt (#133): the safety that makes the raised,
    # non-binding iteration ceiling safe. A verdict phase that re-opened
    # ZERO_PROGRESS_STALL_LIMIT times in a row emitting an IDENTICAL fingerprint
    # (recorded at the verdict-record seam above) is a genuine non-convergence —
    # it will spin forever emitting the same output, and the #131 liveness
    # watchdog cannot catch it (files ARE being touched). Halt it here. Only
    # active in DYNAMIC mode; HARD relies on its fixed cap.
    if _iter_mode == STAGE_ITER_MODE_DYNAMIC:
        zp_exceeded, zp_reason = state.zero_progress_exceeded(
            s, phase_name, ZERO_PROGRESS_STALL_LIMIT)
        if zp_exceeded:
            state.set_status(s, RunStatus.HALTED, reason=zp_reason)
            state.write(ws.state_path, s)
            stage_mod.append_event(ws.root, {
                "kind": EventKind.ZERO_PROGRESS_HALT,
                "phase": phase_name,
                "reason": zp_reason,
            })
            return StageOutcome(
                stage_index=-1,
                phase=phase_name,
                stage_dir_rel="",
                status="HALTED",
                attempts=0,
                wall_seconds=0.0,
                decision=Decision.HALT,
                note=zp_reason,
            )

    # Bounds check BEFORE creating a new stage dir.
    exceeded, reason = state.bounds_exceeded(s, phase_name, iter_mode=_iter_mode)
    if exceeded:
        # #123a: distinguish a HALT forced by an unresolved cross_check
        # ERROR retry (a correctness block) from a plain iteration-cap halt,
        # so the halt reason is auditable.
        blocking_cc = _capped_blocking_cross_check_retry(ws, phase_name)
        if blocking_cc:
            reason = (
                "unresolved cross_check ERROR (retry_execute) at the "
                f"execute-retry cap — refusing to advance to REVIEW: {reason}"
            )
        state.set_status(s, RunStatus.HALTED, reason=reason)
        state.write(ws.state_path, s)
        stage_mod.append_event(ws.root, {
            "kind": (
                EventKind.HALTED_BLOCKING_CROSS_CHECK
                if blocking_cc
                else EventKind.HALTED_BOUNDS
            ),
            "phase": phase_name,
            "reason": reason,
        })
        return StageOutcome(
            stage_index=-1,
            phase=phase_name,
            stage_dir_rel="",
            status="HALTED",
            attempts=0,
            wall_seconds=0.0,
            decision=Decision.HALT,
            note=reason,
        )

    stage_index = stage_mod.next_stage_index(ws.root)

    try:
        phase = phases.get(phase_name)
    except ValueError:
        # A steering plan may name a phase that is not a pipeline phase
        # (decide_next_phase returns the plan's phase verbatim): halt cleanly with a
        # diagnostic rather than crashing with an uncaught ValueError (which
        # leaves the run orphaned: in_progress, no live process).
        reason = (
            f"steering plan requested unknown phase {phase_name!r}; "
            f"valid pipeline phases: {sorted(phases.BY_NAME)}. "
            f"Halting cleanly (no orphan); fix the plan."
        )
        state.set_status(s, RunStatus.HALTED, reason=reason)
        state.write(ws.state_path, s)
        stage_mod.append_event(ws.root, {
            "kind": EventKind.HALTED_UNKNOWN_STEERING_PHASE,
            "phase": phase_name,
            "reason": reason,
        })
        return StageOutcome(
            stage_index=-1, phase=phase_name, stage_dir_rel="",
            status="HALTED", attempts=0, wall_seconds=0.0,
            decision=Decision.HALT, note=reason,
        )

    if phase.name == PhaseName.CROSS_CHECK:
        outcome = _run_cross_check_stage(ws, stage_index, s)
    elif phase.name in CLAUDE_CODE_PHASES:
        outcome = _run_llm_stage(ws, stage_index, phase, s)
    else:
        raise ValueError(
            f"unknown phase kind for {phase.name!r}: not LLM, not cross_check"
        )

    # Distill into the cross-run lab KB after every DONE stage — not only
    # at run_all's tail. Without this, a run that does real work but never
    # reaches run_all's end (a standalone `omicairn ideate`, a `--stop-after` /
    # single `--phase` invocation, or a run killed mid-pipeline) NEVER
    # shares its knowledge, leaving the lab KB empty and {{lab_knowledge}}
    # blank for the next run (audit 2026-06-10: no run had ever distilled).
    # distill_workspace is idempotent (folded on read, last write per key
    # wins) and best-effort, so per-stage calls just keep the lab KB
    # continuously current.
    if outcome is not None and outcome.status == "DONE":
        _maybe_distill_to_lab(ws)
    return outcome


def run_all(
    ws: Workspace,
    start: str | None = None,
    stop_after: str | None = None,
    max_stages: int = 64,
) -> list[StageOutcome]:
    """Drive the pipeline end-to-end.

    Parameters
    ----------
    start:
        If given, the first stage is forced to this phase (equivalent
        to ``omicairn run --add-stage <phase>``). Subsequent stages are
        chosen by decide_next_phase as normal.
    stop_after:
        Stop after the first done stage whose phase is this name.
        Useful for cheap smoke tests that only want to run through
        a specific phase.
    max_stages:
        Safety belt. The outer loop will refuse to run more than this
        many stages in a single invocation. Defaults to 64, which is
        comfortably above the expected max for normal runs.
    """
    # Acquire the per-workspace .run.lock . Two
    # concurrent `omicairn run <ws>` invocations on the same workspace
    # race to create stage dirs and corrupt the linear stage history;
    # we observed this on immunesig-pancan-20260503-200649 where two
    # runs spawned competing 004_execute / 005_execute dirs and
    # required manual cleanup. The lock prevents that class entirely.
    from . import lock as lock_mod
    run_lock_path = ws.root / ".run.lock"
    if not lock_mod.acquire(run_lock_path):
        state_holder = lock_mod.inspect(run_lock_path)
        msg = (
            f"refusing to run: another `omicairn run` process holds "
            f"{run_lock_path} (PID {state_holder.holder_pid}, "
            f"alive={state_holder.holder_alive}). "
        )
        if state_holder.holder_alive:
            msg += "Wait for it to finish, or kill it before retrying."
        else:
            msg += (
                f"Holder is dead but lock is recent "
                f"(age={state_holder.age_seconds:.0f}s, stale-window "
                f"is {lock_mod.STALE_AGE_SECONDS}s). Re-run after "
                f"the stale window expires, or use "
                f"`omicairn.lock.force_clear()` if you're certain."
            )
        stage_mod.append_event(ws.root, {
            "kind": EventKind.RUN_LOCK_REFUSED,
            "lock_path": str(run_lock_path),
            "holder_pid": state_holder.holder_pid,
            "holder_alive": state_holder.holder_alive,
        })
        raise RuntimeError(msg)
    try:
        return _run_all_locked(
            ws, start, stop_after, max_stages
        )
    finally:
        lock_mod.release(run_lock_path)


def _run_all_locked(
    ws: Workspace,
    start: str | None,
    stop_after: str | None,
    max_stages: int,
) -> list[StageOutcome]:
    """Inner driver. Caller MUST hold ws.root/.run.lock."""
    # Record the OmiCairn system version + datetime for THIS `run`
    # invocation. Per-invocation because a run is resumed across many
    # invocations and the code is often edited between resumes — the
    # commit that produced a late stage may differ from an early one.
    # Appends to provenance.jsonl + stamps STATE.md. Best-effort.
    provenance_mod.record_invocation(ws.root, command="run")

    # A resumed legacy workspace may still carry manuscript-pipeline
    # subagents and a CLAUDE.md without the no-manuscript rule.
    removed_agents = workspace_mod.sync_workspace_assets(ws)
    if removed_agents:
        stage_mod.append_event(ws.root, {
            "kind": EventKind.LEGACY_AGENTS_REMOVED,
            "agents": removed_agents,
        })

    # Seed the knowledge graph with goals + pre-registered hypotheses
    # before the first stage (idempotent). The graph is the durable
    # product of the run (core belief 11); seeding here means even a
    # run that halts early leaves a graph skeleton on disk.
    _seed_knowledge_graph(ws)

    outcomes: list[StageOutcome] = []
    forced = start
    # Anti-loop backstop (FIX B): track the SAME contract-fail signature
    # repeating with no progress. A phase that re-fails identically
    # `_IDENTICAL_CONTRACT_FAIL_LIMIT` times in a row is stuck (the agent
    # cannot self-correct the exact same defect), so burning the rest of
    # max_iters_per_phase on identical re-runs is pure waste. We stop early
    # and halt with a clear reason naming the stuck contract — instead of
    # looping silently.
    _last_cf_signature: tuple[str, str] | None = None
    _cf_streak = 0
    # Total-wall guard. RESEARCH.md sets `max_total_wall_minutes`
    # (parkinson smoke-run §3.6 flagged it as parsed-but-ignored).
    # When set, the outer loop breaks before dispatching the next
    # stage if cumulative wall time has exceeded the budget. We do
    # NOT mid-stage abort — a stage in flight runs to its own per-
    # phase wall timeout and then we re-check.
    total_wall_s_limit = int(ws.research_md.max_total_wall_minutes) * 60
    total_t0 = time.time()
    while True:
        if len(outcomes) >= max_stages:
            stage_mod.append_event(ws.root, {
                "kind": EventKind.RUN_ALL_CAP,
                "max_stages": max_stages,
                "completed": len(outcomes),
            })
            break
        if total_wall_s_limit > 0:
            elapsed = time.time() - total_t0
            if elapsed >= total_wall_s_limit:
                stage_mod.append_event(ws.root, {
                    "kind": EventKind.RUN_ALL_TOTAL_WALL_EXCEEDED,
                    "limit_seconds": total_wall_s_limit,
                    "elapsed_seconds": int(elapsed),
                    "completed": len(outcomes),
                })
                break
        outcome = run_next_stage(ws, forced_phase=forced)
        forced = None  # only applies to the first iteration
        if outcome is None:
            break
        outcomes.append(outcome)
        # Steering-step bookkeeping (Slice 2). If this stage was
        # routed by a steering plan, mark its step done so the next
        # iteration dequeues the next pending step instead of looping
        # on the same one. We only mark on DONE outcomes — partial /
        # failed stages should NOT consume a step (the user can
        # re-run or edit the plan after inspecting).
        if outcome.status == "DONE":
            _maybe_mark_steering_step_done(ws, outcome)
            if stop_after and outcome.phase == stop_after:
                break
            continue
        # CONTRACT_FAIL is the dominant transient failure mode in
        # parallel runs — empty claude -p output, rate-limit blip,
        # subprocess died mid-write. The parkinson 15-case suite
        # (May 2026) lost 5 of 15 runs to halt-on-first-fail. We retry,
        # but a CONTRACT_FAIL is "the stage didn't finish writing its
        # required files", NOT a fresh science iteration. So it consumes a
        # SEPARATE, bounded contract-retry budget (MAX_CONTRACT_RETRIES) —
        # NOT the `max_iters_per_phase` science cap. `record_stage_start`
        # already bumped iteration_counts BEFORE the stage ran (crash-
        # safety); `reclassify_contract_fail` moves that one increment OUT
        # of the science count and INTO contract_retry_counts, so a string
        # of write-up retries can never strand a COMPLETED experiment in its
        # paperwork (the d7 external-vector incident, 2026-06-18).
        if outcome.status == "CONTRACT_FAIL":
            s = state.read(ws.state_path)
            # Re-account: this CONTRACT_FAIL is paperwork that didn't land,
            # not a science iteration. Decrement iteration_counts (floor 0),
            # increment contract_retry_counts. A mid-flight CRASH (no clean
            # CONTRACT_FAIL outcome) is NOT reclassified — it rightly keeps
            # its science-iter increment so a doomed phase can't spin.
            contract_retries = state.reclassify_contract_fail(s, outcome.phase)
            state.write(ws.state_path, s)
            iters_so_far = int(
                s.get("iteration_counts", {}).get(outcome.phase, 0)
            )
            max_contract_retries = MAX_CONTRACT_RETRIES
            stage_mod.append_event(ws.root, {
                "kind": EventKind.CONTRACT_FAIL_RECLASSIFIED,
                "stage": outcome.stage_dir_rel,
                "phase": outcome.phase,
                "contract_retries_used": contract_retries,
                "max_contract_retries": max_contract_retries,
                "science_iters_remaining_unburned": iters_so_far,
            })
            # Track the consecutive identical-failure streak (FIX B).
            signature = (outcome.phase, outcome.note)
            if signature == _last_cf_signature:
                _cf_streak += 1
            else:
                _last_cf_signature = signature
                _cf_streak = 1
            # Anti-loop backstop (FIX B) — evaluated FIRST, before the
            # contract-retry-budget check. When a phase contract-fails
            # IDENTICALLY `_IDENTICAL_CONTRACT_FAIL_LIMIT` times in a row it
            # is stuck (zero progress across retries), so we halt with a
            # clear stuck-contract reason, ahead of the plain
            # budget-exhaustion halt below.
            if _cf_streak >= _IDENTICAL_CONTRACT_FAIL_LIMIT:
                state.set_status(
                    s, RunStatus.HALTED,
                    reason=(
                        f"{outcome.phase} stuck: identical "
                        f"contract-fail {_cf_streak}× with no "
                        f"progress — {outcome.note}"
                    )[:200],
                )
                state.write(ws.state_path, s)
                stage_mod.append_event(ws.root, {
                    "kind": EventKind.STAGE_STUCK_CONTRACT_FAIL_HALT,
                    "stage": outcome.stage_dir_rel,
                    "phase": outcome.phase,
                    "consecutive_identical": _cf_streak,
                    "iterations_used": contract_retries,
                    "note": outcome.note,
                })
                break
            cf_exhausted, _ = state.contract_retries_exceeded(s, outcome.phase)
            if not cf_exhausted:
                stage_mod.append_event(ws.root, {
                    "kind": EventKind.STAGE_RETRY_AFTER_CONTRACT_FAIL,
                    "stage": outcome.stage_dir_rel,
                    "phase": outcome.phase,
                    "iterations_so_far": contract_retries,
                    "max_contract_retries": max_contract_retries,
                    "consecutive_identical": _cf_streak,
                    "note": outcome.note,
                })
                forced = outcome.phase
                continue
            # Out of contract retries for this phase → halt. Carry the
            # operator's most-useful debugging signal forward. The science
            # cap was NOT burned by these write-up retries (the whole point
            # of the reclassify); this is the runaway backstop for a phase
            # that genuinely cannot land its contract files with NON-identical
            # failures (the identical-streak path above handles repeats).
            stage_mod.append_event(ws.root, {
                "kind": EventKind.STAGE_RETRIES_EXHAUSTED,
                "phase": outcome.phase,
                "iterations_used": contract_retries,
                "max_contract_retries": max_contract_retries,
                "note": outcome.note,
            })
            break
        # Permanent / non-recoverable terminal outcomes
        # (TIMEOUT, HALTED, CLI_MISSING, BOUNDS_EXCEEDED,
        # SESSION_LIMIT). Halt the outer loop so the operator
        # can inspect.
        break

    _maybe_distill_to_lab(ws)
    return outcomes


def _maybe_distill_to_lab(ws: Workspace) -> None:
    """Distill this workspace's knowledge graph into the cross-workspace
    lab knowledge base so future runs compound (core belief 11). Runs at
    the end of every drive; idempotent (re-distill updates in place).
    Best-effort: a lab-store problem must never fail the run."""
    try:
        # Seed DATASET_REF nodes from the run's resolved assets FIRST, so the
        # distill below shares them (the lab-KB dataset channel was wired but
        # had no producer — see asset_probe.seed_dataset_refs). Best-effort:
        # a seeding hiccup must never block distill or fail the run.
        try:
            from . import asset_probe
            seeded = asset_probe.seed_dataset_refs(ws.root)
            if seeded:
                stage_mod.append_event(ws.root, {
                    "kind": EventKind.DATASET_REFS_SEEDED, "count": seeded,
                })
        except (OSError, ValueError, KeyError) as e:
            stage_mod.append_event(ws.root, {
                "kind": EventKind.DATASET_REFS_SEED_ERROR, "error": str(e),
            })
        lab_root = lab_knowledge.resolve_root(workspaces_root=ws.root.parent)
        counts = lab_knowledge.distill_workspace(
            ws.root, topic_slug=ws.research_md.topic_slug, root=lab_root)
        if any(counts.values()):
            stage_mod.append_event(ws.root, {
                "kind": EventKind.LAB_KNOWLEDGE_DISTILLED, **counts,
            })
    except (OSError, ValueError, KeyError) as e:
        stage_mod.append_event(ws.root, {
            "kind": EventKind.LAB_KNOWLEDGE_DISTILL_ERROR, "error": str(e),
        })
        return
    # Serendipity-promotion loop (creativity audit F1). Default-OFF behind
    # ENV_ENABLE_SERENDIPITY — when disabled this is a strict no-op so no
    # live run changes behaviour. A pure READ of the just-distilled graph +
    # an append-only WRITE to the lab KB: it cannot affect routing, a
    # verdict, the gates, or the on-the-fly cap. Separate try/except (a
    # promotion bug must never fail a run, and must never mask a distill
    # error above). See lab_knowledge.promote_serendipitous_directions.
    if not serendipity_enabled():
        return
    try:
        promoted = lab_knowledge.promote_serendipitous_directions(
            ws.root, topic_slug=ws.research_md.topic_slug, root=lab_root)
        if promoted.get("promoted"):
            stage_mod.append_event(ws.root, {
                "kind": EventKind.SERENDIPITY_PROMOTED, **promoted,
            })
    except (OSError, ValueError, KeyError) as e:
        stage_mod.append_event(ws.root, {
            "kind": EventKind.SERENDIPITY_PROMOTE_ERROR, "error": str(e),
        })


def run_steering_planner(
    workspace_root: pathlib.Path, request: str
) -> int:
    """Dispatch the steering planner as a one-off Claude Code session.

    Reads `prompts/steering_planner.md`, renders `{{user_request}}`,
    runs `claude -p` with the workspace as cwd. The planner writes
    `<workspace>/knowledge/steering_plan.json` (handled directly by
    the planner; orchestrator just dispatches).

    Returns the claude-p exit code. 0 on success.
    """
    from . import claude_runner
    pkg_root = pathlib.Path(__file__).resolve().parent.parent
    template_path = pkg_root / "prompts" / "steering_planner.md"
    if not template_path.exists():
        raise FileNotFoundError(
            f"steering planner prompt missing: {template_path}"
        )
    template = template_path.read_text()
    knowledge_dir = workspace_root / "knowledge"
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    # Synthetic stage_dir for the planner: it writes
    # steering_plan.json directly into knowledge/.
    rendered = template.replace("{{user_request}}", request)
    rendered = rendered.replace(
        "{{stage_dir}}", str(knowledge_dir)
    )
    # Steering planner gets a small tool subset (Read + minimal Bash
    # + write). It is NOT supposed to dispatch subagents or fetch web.
    tools = ("Read", "Write", "Bash", "Glob", "Grep")
    log_path = (
        knowledge_dir
        / f"steering_planner_{int(time.time())}.log"
    )
    # Read research_md to pick the model; default to opus.
    from . import workspace as workspace_mod
    ws = workspace_mod.load_workspace(workspace_root)
    result = claude_runner.run(
        prompt=rendered,
        workspace_root=workspace_root,
        data_dir=ws.research_md.data_location,
        allowed_tools=tools,
        model=ws.research_md.claude_model,
        timeout_seconds=10 * 60,
        log_path=log_path,
        max_budget_usd=2.0,
    )
    return result.returncode

