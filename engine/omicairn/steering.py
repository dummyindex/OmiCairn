"""Human-in-the-loop steering for OmiCairn pipelines.

Steering lets a human nudge the autonomous SCOPE → ... → REVIEW
pipeline mid-flight: regenerate a figure, add a sensitivity analysis,
soften an overclaim, re-fit a sub-analysis, etc. The mechanism mirrors the
existing PLAN-then-EXECUTE shape:

  omicairn steer <ws> "<natural language request>"
       │
       ▼
  ┌─────────────────────────────────┐
  │ Steering Planner — claude -p    │  reads workspace state +
  │ session, ~one Claude Code call  │  RESEARCH.md + user text
  └─────────────────────────────────┘
       │
       ▼
  knowledge/steering_plan.json       ← typed contract (this module)
  knowledge/steering_log.jsonl       ← append-only audit
       │
       ▼
  orchestrator.decide_next_phase     ← one new top-priority input
       │
       ▼
  steered phase(s) run as normal     ← reuses _run_llm_stage,
  Claude Code sessions, with the     │  prompt rendering, contracts,
  steering payload injected via the  │  W-checks. No second control
  {{steering}} prompt token.         │  plane.

Verbs are emergent. The planner decomposes the user's request into
a sequence of phase invocations with prompt overlays — there is no
fixed verb registry. New phase types (e.g. `free`) live in the
existing phases.ORDER catalog. This is parser-audit-style structural
contract: typed plan → structured execution. No prose parsing.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import pathlib
from typing import Any

from .constants import EventKind

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

PLAN_FILENAME = "steering_plan.json"
LOG_FILENAME = "steering_log.jsonl"
SCHEMA_VERSION = 1


@dataclasses.dataclass
class SteeringStep:
    """One step in a steering plan.

    Fields:
      phase           Phase name from phases.ORDER, OR an ad-hoc name
                      (e.g. ``free``) if the planner decided no
                      existing phase fits. Ad-hoc names route through
                      the `free` phase machinery (Slice 2).
      prompt_overlay  Free-text instruction injected via the
                      ``{{steering}}`` token into the phase's normal
                      prompt. Used when ``phase`` is a known phase.
      prompt_inline   Full prompt body for ad-hoc phases. Mutually
                      exclusive with prompt_overlay.
      tool_subset     Optional restriction on the phase's tool
                      allowlist. None means "use the phase's default".
      contract        Optional override for required output files.
                      None means "use the phase's default".
      expected_changes A list of post-stage assertions that the
                      Slice 3 verifier checks (warn-only). Each
                      element is a string predicate (e.g.
                      ``"mtime(figures/fig3_*.png) > <ts>"``) — kept
                      free-form for now; verifier interprets.
      rationale       One-line "why this step" explanation from the
                      planner.
      done            Set True after the step completes (orchestrator
                      writes back).
      stage_dir_rel   Set to the actual stage dir after dispatch
                      (orchestrator writes back).
    """
    phase: str
    prompt_overlay: str = ""
    prompt_inline: str = ""
    tool_subset: list[str] | None = None
    contract: list[str] | None = None
    expected_changes: list[str] = dataclasses.field(default_factory=list)
    rationale: str = ""
    done: bool = False
    stage_dir_rel: str = ""


@dataclasses.dataclass
class SteeringPlan:
    """A typed plan written by the steering planner."""
    version: int = SCHEMA_VERSION
    user_request: str = ""
    rationale: str = ""
    steps: list[SteeringStep] = dataclasses.field(default_factory=list)
    on_step_failure: str = "halt"  # "halt" | "warn_and_continue"
    created_at: str = ""
    consumed_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SteeringPlan":
        steps_raw = d.get("steps") or []
        steps = []
        for s in steps_raw:
            if not isinstance(s, dict):
                continue
            steps.append(SteeringStep(
                phase=str(s.get("phase") or ""),
                prompt_overlay=str(s.get("prompt_overlay") or ""),
                prompt_inline=str(s.get("prompt_inline") or ""),
                tool_subset=s.get("tool_subset"),
                contract=s.get("contract"),
                expected_changes=list(s.get("expected_changes") or []),
                rationale=str(s.get("rationale") or ""),
                done=bool(s.get("done", False)),
                stage_dir_rel=str(s.get("stage_dir_rel") or ""),
            ))
        return cls(
            version=int(d.get("version") or SCHEMA_VERSION),
            user_request=str(d.get("user_request") or ""),
            rationale=str(d.get("rationale") or ""),
            steps=steps,
            on_step_failure=str(d.get("on_step_failure") or "halt"),
            created_at=str(d.get("created_at") or ""),
            consumed_at=str(d.get("consumed_at") or ""),
        )

    def next_pending_step(self) -> SteeringStep | None:
        for s in self.steps:
            if not s.done:
                return s
        return None

    def is_complete(self) -> bool:
        return all(s.done for s in self.steps)


# ---------------------------------------------------------------------------
# IO helpers
# ---------------------------------------------------------------------------

def _knowledge_dir(workspace_root: pathlib.Path) -> pathlib.Path:
    d = workspace_root / "knowledge"
    d.mkdir(parents=True, exist_ok=True)
    return d


def plan_path(workspace_root: pathlib.Path) -> pathlib.Path:
    return _knowledge_dir(workspace_root) / PLAN_FILENAME


def log_path(workspace_root: pathlib.Path) -> pathlib.Path:
    return _knowledge_dir(workspace_root) / LOG_FILENAME


def read_plan(workspace_root: pathlib.Path) -> SteeringPlan | None:
    """Load the active steering plan; return None if none exists."""
    p = plan_path(workspace_root)
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(d, dict):
        return None
    return SteeringPlan.from_dict(d)


def has_pending_plan(workspace_root: pathlib.Path) -> bool:
    """True iff a steering plan exists that is BOTH valid (``validate_plan``
    passes) AND has at least one not-done step.

    This is the single predicate the orchestrator uses to decide whether a
    ``done``/``halted`` workspace should nonetheless dispatch — a user ran
    ``omicairn steer`` on a finished run and the pending plan must not be
    silently swallowed by the terminal-status gate. A genuinely-finished
    workspace with NO pending plan returns False, so the orchestrator still
    short-circuits and never loops forever on a finished run. Validity is
    required so a malformed hand-edited plan can't un-terminate a run.
    """
    plan = read_plan(workspace_root)
    if plan is None or plan.is_complete():
        return False
    return not validate_plan(plan)


def write_plan(workspace_root: pathlib.Path, plan: SteeringPlan) -> None:
    """Write the active plan + append a copy to the audit log."""
    if not plan.created_at:
        plan.created_at = dt.datetime.now(dt.timezone.utc).isoformat(
            timespec="seconds"
        )
    p = plan_path(workspace_root)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(plan.to_dict(), indent=2) + "\n")
    tmp.replace(p)
    # Append-only audit (one line per write — captures step
    # progression as the orchestrator marks steps done).
    with log_path(workspace_root).open("a") as fh:
        fh.write(json.dumps({
            "ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "kind": EventKind.STEERING_PLAN_WRITE,
            "plan": plan.to_dict(),
        }) + "\n")


def consume_plan(workspace_root: pathlib.Path, plan: SteeringPlan) -> None:
    """Mark the plan finished and rename the active file."""
    plan.consumed_at = dt.datetime.now(dt.timezone.utc).isoformat(
        timespec="seconds"
    )
    p = plan_path(workspace_root)
    if p.exists():
        try:
            p.rename(p.with_suffix(".consumed.json"))
        except OSError:
            pass
    with log_path(workspace_root).open("a") as fh:
        fh.write(json.dumps({
            "ts": plan.consumed_at,
            "kind": EventKind.STEERING_PLAN_CONSUMED,
            "plan": plan.to_dict(),
        }) + "\n")


def mark_step_done(
    workspace_root: pathlib.Path,
    step_index: int,
    stage_dir_rel: str,
) -> None:
    """Mark a specific step done and persist."""
    plan = read_plan(workspace_root)
    if plan is None:
        return
    if not (0 <= step_index < len(plan.steps)):
        return
    plan.steps[step_index].done = True
    plan.steps[step_index].stage_dir_rel = stage_dir_rel
    write_plan(workspace_root, plan)
    if plan.is_complete():
        consume_plan(workspace_root, plan)


def skip_steps_with_phases(
    workspace_root: pathlib.Path, phase_names: frozenset[str],
) -> list[tuple[int, str]]:
    """Mark every pending step whose phase is in ``phase_names`` done (with an
    empty ``stage_dir_rel``) and persist. Returns ``(index, phase)`` per step
    skipped; a no-op returns ``[]`` without touching the plan file."""
    if not (workspace_root / "knowledge" / PLAN_FILENAME).exists():
        return []
    plan = read_plan(workspace_root)
    if plan is None or plan.is_complete():
        return []
    skipped: list[tuple[int, str]] = []
    for idx, step in enumerate(plan.steps):
        if not step.done and step.phase in phase_names:
            step.done = True
            step.stage_dir_rel = ""
            skipped.append((idx, step.phase))
    if skipped:
        write_plan(workspace_root, plan)
        if plan.is_complete():
            consume_plan(workspace_root, plan)
    return skipped


def append_log(workspace_root: pathlib.Path, entry: dict) -> None:
    """Append an arbitrary JSON entry to the steering audit log.

    Used by Slice 3 verifiers to land per-predicate pass/fail rows
    without round-tripping through write_plan. Caller MUST include
    a ``kind`` key; ``ts`` is added if absent.
    """
    if "kind" not in entry:
        raise ValueError("steering append_log entry needs a 'kind' field")
    if "ts" not in entry:
        entry = {
            "ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            **entry,
        }
    with log_path(workspace_root).open("a") as fh:
        fh.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

ON_FAILURE_VALUES = frozenset({"halt", "warn_and_continue"})


def validate_plan(plan: SteeringPlan) -> list[str]:
    """Return a list of human-readable validation errors. Empty
    list means the plan is well-formed.

    Validates:
      - version in {1, ...known versions}
      - user_request non-empty
      - steps non-empty
      - each step has either phase + prompt_overlay OR phase + prompt_inline
      - on_step_failure in {halt, warn_and_continue}
    """
    errors: list[str] = []
    if plan.version != SCHEMA_VERSION:
        errors.append(
            f"version {plan.version} != supported {SCHEMA_VERSION}"
        )
    if not plan.user_request.strip():
        errors.append("user_request is empty")
    if not plan.steps:
        errors.append("steps is empty — at least one step required")
    if plan.on_step_failure not in ON_FAILURE_VALUES:
        errors.append(
            f"on_step_failure={plan.on_step_failure!r} not in "
            f"{sorted(ON_FAILURE_VALUES)}"
        )
    for i, step in enumerate(plan.steps):
        if not step.phase:
            errors.append(f"step[{i}] missing `phase`")
        if step.prompt_overlay and step.prompt_inline:
            errors.append(
                f"step[{i}] has both prompt_overlay and prompt_inline "
                "(mutually exclusive)"
            )
        if not step.prompt_overlay and not step.prompt_inline:
            errors.append(
                f"step[{i}] has neither prompt_overlay nor prompt_inline"
            )
    return errors
