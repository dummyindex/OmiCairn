"""SCOPE-time feasibility / statistical-power gate.

The PROBLEM this guards (concrete): OmiCairn once ran a pre-registered
confirmatory replication on 52 & 27 cells vs the original's 22,048,
faithfully producing an underpowered null whose true cause was its own
data-handling — instead of catching the infeasibility upstream. A real
PI catches that at data-profiling, in minutes.

This module is the THIN, mechanical half of the fix. The POWER JUDGMENT
is the model's (prompt-driven, see prompts/scope.md): SCOPE profiles the
ACTUALLY extractable analysis units (e.g. cells per atlas after gating)
and compares them against what the hypothesis / reference effect size
needs, then emits ``feasibility.json``. This module only:

  1. defines + validates the JSON-only schema (golden principle 8);
  2. decides whether the gate APPLIES to a given run (a config knob,
     default = confirmatory-only, so exploratory work is never penalised);
  3. maps the model's recommended_action onto an orchestrator routing
     Decision.

NEUTRALITY (belief 13, non-negotiable): the gate keys ONLY on
statistical power / data feasibility, NEVER on the hypothesised direction
or sign of any result. A well-powered NULL passes cleanly. The gate's job
is to stop UNDER-powered runs, not to chase positives.
"""
from __future__ import annotations

import dataclasses
import json
import pathlib
from typing import Any

from .constants import (
    ADEQUACY_LABELS,
    Decision,
    FEASIBILITY_ACTION_TO_DECISION,
    FEASIBILITY_APPLIES_ALL,
    FEASIBILITY_APPLIES_CHOICES,
    FEASIBILITY_APPLIES_NONE,
    FEASIBILITY_DEFAULT_APPLIES_TO,
    FEASIBILITY_DEFAULT_MIN_FRACTION,
    FEASIBILITY_FILE,
    FEASIBILITY_VERDICTS,
    FeasibilityVerdict,
    RECOMMENDED_ACTIONS,
)


# ---------------------------------------------------------------------------
# Config (omicairn_config.yaml `feasibility:` overlay)
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class FeasibilityConfig:
    """Parsed `feasibility:` knobs. Neutral, exploratory-friendly defaults."""
    applies_to: str = FEASIBILITY_DEFAULT_APPLIES_TO
    min_fraction_of_reference: float = FEASIBILITY_DEFAULT_MIN_FRACTION


def parse_config(raw: dict[str, Any] | None) -> FeasibilityConfig:
    """Parse a `feasibility:` mapping into a FeasibilityConfig.

    Tolerant: an absent/None block yields the defaults. An unknown
    ``applies_to`` falls back to the default rather than raising — the
    gate must never crash a run on a config typo (it is a safety rail,
    not a hard contract).
    """
    if not isinstance(raw, dict):
        return FeasibilityConfig()
    applies = str(raw.get("applies_to") or FEASIBILITY_DEFAULT_APPLIES_TO).strip().lower()
    if applies not in FEASIBILITY_APPLIES_CHOICES:
        applies = FEASIBILITY_DEFAULT_APPLIES_TO
    try:
        frac = float(raw.get("min_fraction_of_reference"))
    except (TypeError, ValueError):
        frac = FEASIBILITY_DEFAULT_MIN_FRACTION
    return FeasibilityConfig(applies_to=applies, min_fraction_of_reference=frac)


# Research modes that count as "confirmatory" for gating purposes. METHOD
# mode tests a pre-registered primary hypothesis (the replication pattern
# the gate exists for); DISCOVERY / exploratory modes are exempt by
# default so hypothesis-generating work is never penalised.
_CONFIRMATORY_MODES: frozenset[str] = frozenset({"method"})


def gate_applies(cfg: FeasibilityConfig, research_mode: str, pre_registration: bool) -> bool:
    """Whether the orchestrator should ROUTE on feasibility.json.

    - applies_to == "none"  → never route (gate off).
    - applies_to == "all"   → always route.
    - applies_to == "confirmatory" (default) → route only for
      confirmatory/replication runs: METHOD mode OR an explicitly
      required pre-registration. Exploratory DISCOVERY runs fall through
      so the gate does not block legitimate hypothesis-generating work.
    """
    if cfg.applies_to == FEASIBILITY_APPLIES_NONE:
        return False
    if cfg.applies_to == FEASIBILITY_APPLIES_ALL:
        return True
    # confirmatory (default)
    mode = (research_mode or "").strip().lower()
    return mode in _CONFIRMATORY_MODES or bool(pre_registration)


# ---------------------------------------------------------------------------
# Schema (feasibility.json — JSON-only contract)
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class AnalysisUnit:
    """One planned analysis unit / dataset profiled by SCOPE."""
    name: str
    extractable_n_estimate: int | None
    reference_n: int | None
    adequacy: str  # one of constants.ADEQUACY_LABELS


@dataclasses.dataclass
class Feasibility:
    """The validated feasibility.json payload."""
    verdict: str          # one of FEASIBILITY_VERDICTS
    recommended_action: str  # one of RECOMMENDED_ACTIONS
    rationale: str
    analysis_units: list[AnalysisUnit]


@dataclasses.dataclass
class FeasibilityValidation:
    """Result of validating a feasibility.json file."""
    ok: bool
    feasibility: Feasibility | None
    errors: list[str]


def validate(path: pathlib.Path) -> FeasibilityValidation:
    """Validate a feasibility.json file against the JSON-only schema.

    Hard-enforces only the VALIDITY invariant (golden principle: reserve
    hard harness for validity, not content): the file must parse, carry a
    known `verdict` + `recommended_action`, a non-empty `rationale`, and a
    list of analysis units each with a known `adequacy`. The power
    JUDGMENT itself is the model's — we do not second-guess the numbers.
    """
    errors: list[str] = []
    if not path.exists():
        return FeasibilityValidation(ok=False, feasibility=None,
                                     errors=[f"{FEASIBILITY_FILE} not found"])
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        return FeasibilityValidation(ok=False, feasibility=None,
                                     errors=[f"{FEASIBILITY_FILE} unparseable: {e}"])
    if not isinstance(data, dict):
        return FeasibilityValidation(ok=False, feasibility=None,
                                     errors=[f"{FEASIBILITY_FILE} is not a JSON object"])

    verdict = str(data.get("verdict") or "").strip().lower()
    if verdict not in FEASIBILITY_VERDICTS:
        errors.append(
            f"verdict {verdict!r} not in {sorted(FEASIBILITY_VERDICTS)}"
        )
    action = str(data.get("recommended_action") or "").strip().lower()
    if action not in RECOMMENDED_ACTIONS:
        errors.append(
            f"recommended_action {action!r} not in {sorted(RECOMMENDED_ACTIONS)}"
        )
    rationale = str(data.get("rationale") or "").strip()
    if not rationale:
        errors.append("rationale is empty (the gate's reasoning is mandatory)")

    raw_units = data.get("analysis_units")
    units: list[AnalysisUnit] = []
    if not isinstance(raw_units, list) or not raw_units:
        errors.append("analysis_units must be a non-empty list")
    else:
        for i, u in enumerate(raw_units):
            if not isinstance(u, dict):
                errors.append(f"analysis_units[{i}] is not an object")
                continue
            name = str(u.get("name") or "").strip()
            if not name:
                errors.append(f"analysis_units[{i}] missing name")
            adeq = str(u.get("adequacy") or "").strip().lower()
            if adeq not in ADEQUACY_LABELS:
                errors.append(
                    f"analysis_units[{i}] adequacy {adeq!r} not in "
                    f"{sorted(ADEQUACY_LABELS)}"
                )
            units.append(AnalysisUnit(
                name=name,
                extractable_n_estimate=_opt_int(u.get("extractable_n_estimate")),
                reference_n=_opt_int(u.get("reference_n")),
                adequacy=adeq,
            ))

    if errors:
        return FeasibilityValidation(ok=False, feasibility=None, errors=errors)
    return FeasibilityValidation(
        ok=True,
        feasibility=Feasibility(
            verdict=verdict,
            recommended_action=action,
            rationale=rationale,
            analysis_units=units,
        ),
        errors=[],
    )


def _opt_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

def decision_for(feas: Feasibility) -> str:
    """Map a validated feasibility payload onto a routing Decision.

    - recommended_action drives routing (it is the model's explicit
      remediation choice):
        proceed         → Decision.PROCEED (linear walk to PLAN)
        fix_extraction  → Decision.BACK_TO_SCOPE (re-profile / relax gate)
        acquire_data    → Decision.BACK_TO_PLAN (fetch the missing dataset)
        rescope         → Decision.BACK_TO_SCOPE (re-frame the question)
    - A `feasible` verdict ALWAYS proceeds (a well-powered null included).
      If the verdict is feasible but the action is not `proceed`, the
      verdict wins (a clean run must not be blocked) and we proceed.
    """
    if feas.verdict == FeasibilityVerdict.FEASIBLE:
        return Decision.PROCEED
    return FEASIBILITY_ACTION_TO_DECISION.get(
        feas.recommended_action, Decision.PROCEED
    )
