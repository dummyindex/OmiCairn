"""STATE.md read/write — flat-numbered-stage schema.

STATE.md is a Markdown file with a YAML front-matter block. The
orchestrator reads it at the top of every iteration and writes it after
each stage. It tracks:

- overall run status (in_progress / done / halted)
- which stage index is next
- how many times each phase has been attempted (for bounds)
- how many backtracks have been used
- what verdict the most recent verdict-emitting stage produced
- optional halted_reason

Schema example (YAML)::

    topic_slug: example-topic
    started_at: "2026-04-10T19:06:38+00:00"
    updated_at: "2026-04-10T20:14:00+00:00"
    status: in_progress
    halted_reason: null
    current_stage: 003_cross_check
    backtracks_used: 0
    pending_decision: retry_execute
    iteration_counts:
      scope: 1
      plan: 1
      execute: 1
      cross_check: 1
      review: 0
    bounds:
      max_iters_per_phase: 100
      max_backtracks: 5
"""
from __future__ import annotations

import datetime as dt
import pathlib
from typing import Any

import yaml

from .constants import (
    DEFAULT_MAX_BACKTRACKS,
    DEFAULT_MAX_ITERS_PER_PHASE,
    MAX_CONTRACT_RETRIES,
    PIPELINE_ORDER,
    RunStatus,
)


STATE_TEMPLATE = """\
---
{frontmatter}
---

# {topic_slug} — OmiCairn run state

Status: **{status}**  ·  Current stage: **{current_stage}**  ·  Started: {started_at}

## Iteration counts

{iter_md}

## Bounds

- max_iters_per_phase: {max_iters}
- max_backtracks:      {max_backtracks}
- backtracks_used:     {backtracks_used}
"""


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def initial_state(
    topic_slug: str,
    max_iters_per_phase: int = DEFAULT_MAX_ITERS_PER_PHASE,
    max_backtracks: int = DEFAULT_MAX_BACKTRACKS,
) -> dict[str, Any]:
    """Return a fresh state dict for a newly-initialised workspace."""
    return {
        "topic_slug": topic_slug,
        "started_at": _now(),
        "updated_at": _now(),
        "status": RunStatus.IN_PROGRESS,
        "halted_reason": None,
        "paused_reason": None,
        "current_stage": None,
        "backtracks_used": 0,
        "pending_decision": None,
        "iteration_counts": {p: 0 for p in PIPELINE_ORDER},
        # Parallel to iteration_counts but for CONTRACT_FAILs only — paperwork
        # that didn't land, NOT a fresh science iteration. Bounded separately
        # by MAX_CONTRACT_RETRIES so a string of write-up retries can't exhaust
        # the science cap (see constants.MAX_CONTRACT_RETRIES).
        "contract_retry_counts": {p: 0 for p in PIPELINE_ORDER},
        # Per-phase zero-progress fingerprint tracking (#133): the DYNAMIC
        # iter-mode spin-catcher. Each entry is
        # {"fingerprint": <str|None>, "stalls": <int>}: a phase that re-opens
        # with an IDENTICAL fingerprint bumps `stalls`; a changing fingerprint
        # resets it to 0. Halts the phase once stalls hits
        # ZERO_PROGRESS_STALL_LIMIT. Lazily populated (empty at init).
        "phase_progress": {},
        "bounds": {
            "max_iters_per_phase": int(max_iters_per_phase),
            "max_backtracks": int(max_backtracks),
        },
    }


def write(state_path: pathlib.Path, state: dict[str, Any]) -> None:
    """Persist state to STATE.md (YAML front-matter + summary)."""
    state["updated_at"] = _now()

    iter_lines = []
    for phase in PIPELINE_ORDER:
        n = state.get("iteration_counts", {}).get(phase, 0)
        iter_lines.append(f"- **{phase}**: {n}")
    iter_md = "\n".join(iter_lines)

    bounds = state.get("bounds", {})
    max_iters = bounds.get("max_iters_per_phase", DEFAULT_MAX_ITERS_PER_PHASE)
    max_backtracks = bounds.get("max_backtracks", DEFAULT_MAX_BACKTRACKS)

    fm = yaml.safe_dump(state, sort_keys=False, default_flow_style=False).strip()
    text = STATE_TEMPLATE.format(
        frontmatter=fm,
        topic_slug=state.get("topic_slug", "unknown"),
        status=state.get("status", RunStatus.IN_PROGRESS),
        current_stage=state.get("current_stage") or "(none yet)",
        started_at=state.get("started_at", "?"),
        iter_md=iter_md,
        max_iters=max_iters,
        max_backtracks=max_backtracks,
        backtracks_used=state.get("backtracks_used", 0),
    )
    state_path.write_text(text)


def read(state_path: pathlib.Path) -> dict[str, Any]:
    """Load STATE.md front-matter; fill in any missing defaults."""
    text = state_path.read_text()
    if not text.lstrip().startswith("---"):
        raise ValueError(f"STATE.md at {state_path} has no YAML front-matter")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise ValueError(f"STATE.md at {state_path} is malformed")
    fm = yaml.safe_load(parts[1])
    if not isinstance(fm, dict):
        raise ValueError(f"STATE.md at {state_path} front-matter is not a mapping")

    # Tolerate old-format state files by filling in missing keys with defaults.
    fm.setdefault("status", RunStatus.IN_PROGRESS)
    fm.setdefault("halted_reason", None)
    fm.setdefault("paused_reason", None)
    fm.setdefault("current_stage", None)
    fm.setdefault("backtracks_used", 0)
    # Legacy state files may carry a `next_stage_index` field; the
    # orchestrator now derives it from the filesystem, so we drop it
    # on read so it doesn't get rewritten.
    fm.pop("next_stage_index", None)
    fm.setdefault("pending_decision", None)
    fm.setdefault("iteration_counts", {p: 0 for p in PIPELINE_ORDER})
    for p in PIPELINE_ORDER:
        fm["iteration_counts"].setdefault(p, 0)
    # Tolerate state files written before contract_retry_counts existed.
    fm.setdefault("contract_retry_counts", {p: 0 for p in PIPELINE_ORDER})
    for p in PIPELINE_ORDER:
        fm["contract_retry_counts"].setdefault(p, 0)
    # Tolerate state files written before phase_progress existed (#133).
    # Lazily populated per-phase, so no per-PIPELINE_ORDER seeding needed.
    fm.setdefault("phase_progress", {})
    fm.setdefault("bounds", {
        "max_iters_per_phase": DEFAULT_MAX_ITERS_PER_PHASE,
        "max_backtracks": DEFAULT_MAX_BACKTRACKS,
    })
    return fm


# ---------------------------------------------------------------------------
# Mutators — small functions that edit the state dict in place
# ---------------------------------------------------------------------------

def record_stage_start(
    state: dict[str, Any], stage_index: int, phase: str
) -> None:
    """Called just before a stage starts."""
    from .constants import stage_dir_name
    state["current_stage"] = stage_dir_name(stage_index, phase)
    state["iteration_counts"][phase] = state["iteration_counts"].get(phase, 0) + 1


def set_paused(state: dict[str, Any], reason: str) -> None:
    """Mark the run as paused (e.g., Claude Code 5-hour session cap).

    Distinct from `set_status(HALTED)`: the run remains in_progress so a
    later resume picks up cleanly. `paused_reason` is informational —
    surfaces in `omicairn status` and is cleared the next time a stage starts.

    No-op when status is HALTED or DONE: a terminal run can't be paused,
    and surfacing a "paused" marker on a halted run would mislead the
    operator about whether resume is appropriate.
    """
    if state.get("status") != RunStatus.IN_PROGRESS:
        return
    state["paused_reason"] = reason


def clear_paused(state: dict[str, Any]) -> None:
    """Drop any prior pause marker. Called at the top of run_next_stage
    so a fresh attempt always starts from a clean slate, even if the
    attempt later halts via bounds-exceeded."""
    state["paused_reason"] = None


def reset_for_steering(state: dict[str, Any]) -> bool:
    """Un-terminate a ``done``/``halted`` run so a pending steering plan can
    dispatch. Flips status back to ``in_progress`` and clears the terminal /
    pause markers (``halted_reason``, ``paused_reason``, ``pending_decision``)
    so a steered stage starts from a clean slate. Returns True iff the status
    was actually terminal (i.e. a reset happened); a no-op (returns False) on
    an already-in_progress run, so the common path is untouched.

    Called by ``run_next_stage`` only when ``steering.has_pending_plan`` is
    True — this is the fix for `omicairn steer` on a finished run silently
    no-op'ing (d7 steering bug, 2026-06-10).
    """
    if state.get("status") not in (RunStatus.DONE, RunStatus.HALTED):
        return False
    state["status"] = RunStatus.IN_PROGRESS
    state["halted_reason"] = None
    state["paused_reason"] = None
    state["pending_decision"] = None
    return True


def record_stage_done(
    state: dict[str, Any], stage_index: int, phase: str
) -> None:
    """Called after a stage's contract passes and done.flag is written.

    The authoritative "next stage index" is derived from the filesystem
    by the orchestrator (``stage.next_stage_index``), so this function
    has nothing to mutate on the state dict itself. It exists as a
    named hook so the orchestrator has a single place to extend if
    per-stage bookkeeping becomes necessary in the future.
    """
    del state, stage_index, phase  # currently unused; keep the signature stable


def record_decision(
    state: dict[str, Any], decision: str | None
) -> None:
    state["pending_decision"] = decision


def record_backtrack(state: dict[str, Any]) -> None:
    state["backtracks_used"] = state.get("backtracks_used", 0) + 1


def set_status(
    state: dict[str, Any], status: str, reason: str | None = None
) -> None:
    state["status"] = status
    # halted_reason is only meaningful when the run is actually halted.
    # Clearing it on non-halted transitions keeps `omicairn status` output
    # from implying a done run also "halted".
    if status == RunStatus.HALTED:
        if reason is not None:
            state["halted_reason"] = reason
    else:
        state["halted_reason"] = None


def bounds_exceeded(
    state: dict[str, Any], phase: str, iter_mode: str | None = None
) -> tuple[bool, str]:
    """Return (True, reason) if running `phase` would exceed a bound.

    Both bounds are cap-exclusive: `max_iters_per_phase=3` means the
    phase can run up to 3 times, and the 4th attempt is refused. Same
    for `max_backtracks=5` — 5 backtracks are allowed, the 6th is
    refused. Does NOT mutate state; the caller decides what to do.

    `iter_mode` (#133) selects how the per-phase ITERATION cap is treated.
    It is an ADDITIVE optional arg (default None → resolved-internally-as
    the module default) so no existing caller breaks:

    - HARD (or None-defaulting-to-hard-legacy is NOT the default): the cap is
      an unconditional deterministic refuse — byte-for-byte the historical
      behaviour.
    - DYNAMIC (the resolved default): the SAME comparison still applies but
      the cap is now a HIGH, non-binding SAFETY CEILING (DEFAULT_MAX_ITERS_PER_
      PHASE = 100). A healthy phase is terminated upstream by its real stop-
      condition or by the zero-progress guard; this ceiling only trips on
      pathological non-convergence.

    Crucially, the *code path* is identical for both modes — the cap is always
    checked. The mode is what the RAISED DEFAULT + the zero-progress guard turn
    into "uncapped-in-spirit": HARD pins a small cap for determinism, DYNAMIC
    leaves the cap high so the real terminators do the work. The backtrack
    bound is mode-independent (a structural backtrack budget, not a per-phase
    iteration count).
    """
    from .constants import STAGE_ITER_MODE_HARD  # local: avoid import cycle churn

    bounds = state.get("bounds", {})
    max_iters = int(bounds.get("max_iters_per_phase", DEFAULT_MAX_ITERS_PER_PHASE))
    max_backtracks = int(bounds.get("max_backtracks", DEFAULT_MAX_BACKTRACKS))

    cur = state.get("iteration_counts", {}).get(phase, 0)
    if cur >= max_iters:
        _which = (
            "max_iters_per_phase (HARD-mode deterministic cap)"
            if iter_mode == STAGE_ITER_MODE_HARD
            else "max_iters_per_phase (DYNAMIC-mode safety ceiling)"
        )
        return True, (
            f"{_which} exceeded for phase {phase!r}: "
            f"already ran {cur} times, cap is {max_iters}"
        )
    bt_used = state.get("backtracks_used", 0)
    if bt_used >= max_backtracks:
        return True, (
            f"max_backtracks exceeded: used {bt_used}, "
            f"cap is {max_backtracks}"
        )
    return False, ""


def record_phase_progress(
    state: dict[str, Any], phase: str, fingerprint: str, *, reopened: bool
) -> int:
    """Track the DYNAMIC iter-mode zero-progress fingerprint for `phase` (#133).

    `fingerprint` is a stable digest of the phase's forward-progress signal
    (for verdict phases: effective_decision + proposed_fix +
    target_file_hints — computed by the
    orchestrator at the verdict-record seam). `reopened` is True iff this
    stage's decision RE-OPENED the phase (a forward/backtrack retry), as
    opposed to advancing (PROCEED/DONE).

    Semantics (mirrors #131's stall counter for the time axis):

    - Advancing (reopened=False): reset stalls to 0 and store the fingerprint.
      A phase that made a real routing decision is progressing by definition;
      the spin-catcher must never fire on it.
    - Re-opening with the SAME fingerprint as last time: increment stalls —
      the agent re-emitted an identical verdict without advancing the artifact.
    - Re-opening with a CHANGED fingerprint: reset stalls to 0, store the new
      fingerprint — genuine progress (a different verdict/revision), so an
      unbounded number of these is allowed (that is the point of "uncapped").

    Returns the NEW stall count for `phase`. Does not itself halt — the
    caller consults :func:`zero_progress_exceeded`.
    """
    pp = state.setdefault("phase_progress", {})
    entry = pp.setdefault(phase, {"fingerprint": None, "stalls": 0})
    prior_fp = entry.get("fingerprint")
    if not reopened:
        entry["fingerprint"] = fingerprint
        entry["stalls"] = 0
        return 0
    if prior_fp is not None and fingerprint == prior_fp:
        entry["stalls"] = int(entry.get("stalls", 0)) + 1
    else:
        entry["fingerprint"] = fingerprint
        entry["stalls"] = 0
    return int(entry["stalls"])


def zero_progress_exceeded(
    state: dict[str, Any], phase: str, stall_limit: int
) -> tuple[bool, str]:
    """Return (True, reason) if `phase` has stalled `stall_limit` times (#133).

    A phase that re-opened `stall_limit` consecutive times emitting an
    identical progress fingerprint is a genuine non-convergence and must halt
    — this is the safety that makes the uncapped DYNAMIC ceiling safe. Does
    NOT mutate state (a pure read of the phase_progress slot).
    """
    stalls = int(state.get("phase_progress", {}).get(phase, {}).get("stalls", 0))
    if stalls >= stall_limit:
        return True, (
            f"zero_progress_non_convergence: phase {phase!r} produced identical "
            f"output {stalls}x with no forward progress (stall_limit={stall_limit})"
        )
    return False, ""


def reclassify_contract_fail(state: dict[str, Any], phase: str) -> int:
    """Re-account a stage that just CONTRACT_FAILed.

    `record_stage_start` unconditionally bumps `iteration_counts[phase]`
    BEFORE the stage runs (crash-safety: a mid-flight CRASH still counts as a
    science iteration, so a genuinely-doomed phase can't spin forever). But a
    CLEAN CONTRACT_FAIL — the stage ran to completion and merely failed to land
    its required contract files — is paperwork that didn't finish, NOT a fresh
    scientific attempt. So we move that one increment OUT of the science count
    and INTO the bounded contract-retry budget:

      iteration_counts[phase]      -= 1   (floor 0)
      contract_retry_counts[phase] += 1

    Idempotency / floor: never drives iteration_counts below 0 (defensive
    against a double-call). Returns the NEW contract_retry_counts[phase].

    Called by the orchestrator's outer loop exactly once per CONTRACT_FAIL
    outcome — the single seam where a stage's status is known. Does NOT mutate
    backtracks or status; the caller decides whether to retry or halt via
    `contract_retries_exceeded`.
    """
    iters = state.setdefault("iteration_counts", {})
    cur_iter = int(iters.get(phase, 0))
    if cur_iter > 0:
        iters[phase] = cur_iter - 1
    crc = state.setdefault("contract_retry_counts", {})
    crc[phase] = int(crc.get(phase, 0)) + 1
    return crc[phase]


def contract_retries_exceeded(
    state: dict[str, Any], phase: str
) -> tuple[bool, str]:
    """Return (True, reason) if `phase` has used up its contract-retry budget.

    The runaway backstop for CONTRACT_FAILs: once a phase has CONTRACT_FAILed
    `MAX_CONTRACT_RETRIES` times, further write-up retries are refused (it never
    enables infinite retry). Cap-inclusive like `bounds_exceeded`: a budget of 3
    allows 3 contract-fail attempts; the would-be 4th retry is refused. Does NOT
    mutate state.
    """
    used = int(state.get("contract_retry_counts", {}).get(phase, 0))
    if used >= MAX_CONTRACT_RETRIES:
        return True, (
            f"max_contract_retries exceeded for phase {phase!r}: "
            f"CONTRACT_FAILed {used} times, cap is {MAX_CONTRACT_RETRIES}"
        )
    return False, ""
