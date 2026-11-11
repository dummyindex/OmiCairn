"""Verdict schema + routing.

A `verdict.json` file lives at the root of a stage directory and tells
the orchestrator what to do next after that stage finishes. Two stage
types emit verdicts:

- `cross_check` (synthetic, pure-Python): the orchestrator runs
  `cross_check.check_execute()` on the most recent execute stage and
  produces a verdict based on its errors/warnings.
- `review` (LLM-driven analysis-adequacy review): the agent writes
  verdict.json as part of its contract. The orchestrator reads the
  decision field to route.

See `constants.Decision` for the set of legal decisions.
"""
from __future__ import annotations

import dataclasses
import json
import pathlib
from typing import Any

from .constants import (
    DONE_FLAG,
    Decision,
    FINDINGS_FILE,
    NON_TERMINAL_DECISIONS,
    TERMINAL_DECISIONS,
    VERDICT_FILE,
    VerdictKey,
)


@dataclasses.dataclass
class Verdict:
    decision: str  # one of constants.Decision.*
    reason: str
    evidence: list[str]
    proposed_fix: str = ""
    target_file_hints: list[str] = dataclasses.field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    def is_valid(self) -> bool:
        return self.decision in (NON_TERMINAL_DECISIONS | TERMINAL_DECISIONS)


def write_verdict(stage_dir: pathlib.Path, verdict: Verdict) -> None:
    """Write verdict.json to the given stage directory.

    Does NOT write done.flag — the orchestrator does that after verifying
    the full stage contract.
    """
    if not verdict.is_valid():
        raise ValueError(
            f"unknown decision {verdict.decision!r}; "
            f"valid: {sorted(NON_TERMINAL_DECISIONS | TERMINAL_DECISIONS)}"
        )
    path = stage_dir / VERDICT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(verdict.to_dict(), indent=2) + "\n")


def read_verdict(stage_dir: pathlib.Path) -> Verdict | None:
    """Load verdict.json from a stage directory, or return None.

    Returns None if the file does not exist or cannot be parsed;
    callers should treat that as "unknown, fall through to linear".
    """
    path = stage_dir / VERDICT_FILE
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    # Canonical key is `decision`. Some agents conflate the
    # decision/verdict vocabulary and emit `verdict` as the top-level
    # key instead — accept it as an alias rather than silently
    # falling through to the orchestrator's "no verdict → assume
    # done" default (observed in ddr-blca-20260430 where the agent
    # wrote `{"verdict": "retry_execute", ...}` and the orchestrator
    # treated the missing `decision` key as DONE).
    decision = data.get(VerdictKey.DECISION)
    if not isinstance(decision, str):
        alias = data.get(VerdictKey.VERDICT)
        if isinstance(alias, str):
            decision = alias
    if not isinstance(decision, str):
        return None
    v = Verdict(
        decision=decision,
        reason=data.get(VerdictKey.REASON, "") or "",
        evidence=list(data.get(VerdictKey.EVIDENCE) or []),
        proposed_fix=data.get(VerdictKey.PROPOSED_FIX, "") or "",
        target_file_hints=list(data.get(VerdictKey.TARGET_FILE_HINTS) or []),
    )
    if not v.is_valid():
        return None
    return v


def write_findings(stage_dir: pathlib.Path, text: str) -> None:
    (stage_dir / FINDINGS_FILE).write_text(text)


def write_done_flag(stage_dir: pathlib.Path) -> None:
    (stage_dir / DONE_FLAG).write_text("1\n")


# ---------------------------------------------------------------------------
# Decision helpers
# ---------------------------------------------------------------------------

def proceed(reason: str = "all checks passed") -> Verdict:
    return Verdict(decision=Decision.PROCEED, reason=reason, evidence=[])


def halt(reason: str, evidence: list[str] | None = None) -> Verdict:
    return Verdict(
        decision=Decision.HALT,
        reason=reason,
        evidence=list(evidence or []),
    )


def retry(phase: str, reason: str, evidence: list[str] | None = None,
          fix: str = "") -> Verdict:
    """Build a retry verdict for the given phase.

    Only "execute" has a retry decision defined in Decision.
    """
    from .constants import PhaseName
    if phase != PhaseName.EXECUTE:
        raise ValueError(f"no retry decision for phase {phase!r}")
    d = Decision.RETRY_EXECUTE
    return Verdict(
        decision=d,
        reason=reason,
        evidence=list(evidence or []),
        proposed_fix=fix,
    )


def back_to(phase: str, reason: str, evidence: list[str] | None = None,
            fix: str = "") -> Verdict:
    """Build a back_to_<phase> verdict."""
    from .constants import PhaseName
    mapping = {
        PhaseName.SCOPE: Decision.BACK_TO_SCOPE,
        PhaseName.PLAN: Decision.BACK_TO_PLAN,
        PhaseName.EXECUTE: Decision.BACK_TO_EXECUTE,
    }
    if phase not in mapping:
        raise ValueError(f"no back_to decision for phase {phase!r}")
    return Verdict(
        decision=mapping[phase],
        reason=reason,
        evidence=list(evidence or []),
        proposed_fix=fix,
    )


def done(reason: str = "pipeline complete") -> Verdict:
    return Verdict(decision=Decision.DONE, reason=reason, evidence=[])

