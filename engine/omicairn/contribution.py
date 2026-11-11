"""Contribution-depth directive (method/model depth vs analysis vs theory).

A OmiCairn run declares a `contribution_type` in `omicairn_config.yaml`
(RESEARCH.md fallback). It is one of :data:`CONTRIBUTION_TYPES`
(``analysis`` | ``method`` | ``theory``); the default is ``analysis``.

The owner's complaint is that OmiCairn runs are "too analysis-style, lack
method depth": they FIND and MEASURE a phenomenon rather than CONTRIBUTE
a method/model that beats real baselines, or a theorem with a proof. This
knob makes the contribution shape a first-class, selectable, ENFORCED
target. It is threaded exactly like the ``compute:`` block: parsed in
``research_md.load`` and surfaced to SCOPE/PLAN/EXECUTE via the
``{{contribution_directive}}`` token, which the orchestrator fills with
:func:`render_directive`.

Why a RENDERED token rather than static prompt text: the directive must
be CONDITIONAL on the type. A ``method`` case is told it MUST propose +
beat >=2 real baselines with ablations; an ``analysis`` case is told the
opposite (do not manufacture a method just to satisfy a contract). Static
prompt text cannot vary per case; ``compute_resources`` set the precedent
for a per-case rendered directive block.

This module is GENERAL research infrastructure — domain-agnostic, no
domain-specific content. It encodes a research rule (contribution
depth), not domain knowledge.
"""
from __future__ import annotations

from .constants import (
    CONTRIBUTION_TYPES,
    DEFAULT_CONTRIBUTION_TYPE,
    METHOD_DEPTH_RULES_REL,
    ProofStatus,
)


def validate(value: str | None) -> str:
    """Normalise + validate a raw ``contribution_type`` value.

    ``None`` / empty → the default (``analysis``). A non-empty value must
    be a member of :data:`CONTRIBUTION_TYPES`; anything else raises
    ``ValueError`` LOUDLY (mirrors compute_resources' unknown-id failure —
    a typo'd knob is a config bug, not a silent fallback).
    """
    if value is None:
        return DEFAULT_CONTRIBUTION_TYPE
    v = str(value).strip().lower()
    if not v:
        return DEFAULT_CONTRIBUTION_TYPE
    if v not in CONTRIBUTION_TYPES:
        allowed = ", ".join(CONTRIBUTION_TYPES)
        raise ValueError(
            f"contribution_type must be one of {{{allowed}}}; got {value!r}"
        )
    return v


# The three directive blocks. Each is a short, imperative paragraph in the
# existing prompt voice (active, no filler). They are rendered verbatim
# into the {{contribution_directive}} token. The detailed protocol lives
# in rules/method_depth.md (pointed to here) so prompt edits stay surgical.
_ANALYSIS_DIRECTIVE = f"""\
**Contribution type: ANALYSIS** (discovery / measurement / characterisation).
This run is NOT required to invent a new method. A clean analysis,
discovery, or characterisation study — with at least one adversarial
baseline, honest CIs, and a falsifiable finding — is a SUFFICIENT
contribution. Do NOT manufacture a "method" to look deeper than the
science warrants; that is the opposite of rigor. The existing analysis-
rigor rules ({METHOD_DEPTH_RULES_REL} and rules/analysis_rigor.md) apply
as usual."""

_METHOD_DIRECTIVE = f"""\
**Contribution type: METHOD** (a method/model/operator is the deliverable).
A study that only FINDS and MEASURES a phenomenon is INSUFFICIENT for this
run. You MUST:
  1. Propose a CONCRETE, named method/model/operator that is itself the
     contribution (not just an analysis of an existing one).
  2. Implement it and run it on the FULL pre-registered evaluation.
  3. Beat >=2 REAL competing-method baselines — e.g. fine-tuning,
     prompting, or an alternative algorithm — NOT merely adversarial /
     sanity controls (a random or shuffled control confirms a measurement
     is real; it is NOT a method to beat). For EACH baseline, report a
     paired delta(method, baseline) on the pre-registered metric with a
     95% CI.
  4. Report >=1 ABLATION isolating the component your method claims to
     contribute (turn it off / swap it for the trivial version; show the
     gain attributes to it).
Ideally explain WHY the method works with theory; if you state a derived
formula or bound as PROVEN it MUST be Lean-verified (proof_status
`{ProofStatus.PROVEN_LEAN}`). A derived-and-empirically-validated formula
whose Lean proof is still pending may instead be carried as
`{ProofStatus.DERIVED_VALIDATED_PROOF_PENDING}` and labeled proof-pending in
prose — never stated as "proven" (see the math-formalization protocol in
{METHOD_DEPTH_RULES_REL}). REVIEW will route an analysis-only result on a
method case back for more work."""

_THEORY_DIRECTIVE = f"""\
**Contribution type: THEORY** (the core claims are mathematical).
The deliverable is one or more mathematical results (a derived formula, a
bound, a theorem, an identity, a closed-form). Each math claim carries an
HONEST `proof_status` — one of THREE states (record one per claim in
`<stage>/proof_obligations.json`):
  1. `{ProofStatus.PROVEN_LEAN}` — its Lean 4 proof CHECKS (via the
     math-formalization skill / omicairn.lean_verify): write the statement+proof
     to `<stage>/lean/<Claim>.lean`, run the verifier, write the verdict to
     `<stage>/lean/<Claim>.verdict.json`. ONLY this status may be reported
     as "proven / theorem (Lean-verified)".
  2. `{ProofStatus.DERIVED_VALIDATED_PROOF_PENDING}` — the claim is BOTH
     (a) formally DERIVED (a written derivation / proof sketch exists) AND
     (b) empirically VALIDATED (its prediction holds on data with CIs), but
     its machine-checked Lean proof is a PARALLEL obligation not yet closed.
     You MAY finish the run carrying such a claim, PROVIDED you label it
     proof-pending in results.json / analysis.md ("derived ... and validated
     empirically (CI ...); a machine-checked proof is in progress") — NEVER
     report it as "proven". This deferred-proof option applies REGARDLESS of
     the research arc: a derived-but-not-yet-Lean-verified result that is
     empirically validated may ship as
     `{ProofStatus.DERIVED_VALIDATED_PROOF_PENDING}` whether the study
     derived-then-measured or measured-then-derived. The Lean proof is closed
     LATER by a `omicairn steer` deepening that adds the passing `.lean` (no new
     pipeline stage).
  3. `{ProofStatus.CONJECTURE}` — neither proven nor (derived + validated):
     a bare derivation with no empirical backing, or an unvalidated guess.
     Stated only as a labeled conjecture / empirical observation.
A theory study whose ONLY math is a conjecture (state 3) is a failed theory
stage. Empirical validation strengthens a claim but never substitutes for
the eventual Lean proof. See {METHOD_DEPTH_RULES_REL}."""

_DIRECTIVES: dict[str, str] = {
    "analysis": _ANALYSIS_DIRECTIVE,
    "method": _METHOD_DIRECTIVE,
    "theory": _THEORY_DIRECTIVE,
}


def render_directive(contribution_type: str | None) -> str:
    """Render the ``{{contribution_directive}}`` prompt block for a case.

    Tolerant of an unvalidated value (re-validates, falling back to the
    default on empty) so the orchestrator can call it directly on the
    parsed field without a second guard.
    """
    ctype = validate(contribution_type)
    return _DIRECTIVES[ctype]


# =============================================================================
# Research arc — an OPTIONAL FREE-TEXT advisory hint (no enum, no taxonomy)
# =============================================================================
#
# `research_arc` is a SHORT free-form methodological-intent hint, supplied by
# the brief (omicairn_config.yaml / RESEARCH.md). It is NOT validated against any
# canonical value set: the owner found a named enum (theory_first |
# empirical_first) too stiff and too ML/method-specific, so the arc is now
# domain-general free text that FORCES NOTHING. It is purely ADVISORY — it
# shapes the EMPHASIS the agent SHOULD weigh, it NEVER reorders the pipeline
# (PIPELINE_ORDER is fixed) and NEVER constrains contribution_type. An UNSET
# arc renders a neutral note and forces nothing (no theory-first nudge).


def validate_arc(value: str | None) -> str | None:
    """Normalise a raw ``research_arc`` hint, TOLERANTLY.

    ``None`` / empty (after strip) → ``None`` (= unset; no arc hint). Any
    non-empty value is returned stripped, VERBATIM otherwise — there is no
    fixed value set to check against, so this NEVER raises and NEVER rejects
    a hint (contrast ``validate`` for contribution_type, where a typo IS a
    loud config bug). Case is preserved (the hint is rendered as written)."""
    if value is None:
        return None
    v = str(value).strip()
    if not v:
        return None
    return v


# Neutral block when no arc hint is declared. Forces NOTHING — no theory-first
# nudge for any contribution_type. A case with no `research_arc` behaves
# exactly as it did before this knob existed.
_NEUTRAL_ARC_DIRECTIVE = """\
**Research arc: (unset)** — no methodological-arc hint declared. Let the
science and the brief determine which way the argument flows between data and
math; do not force either a derive-first or a measure-first narrative."""


def render_arc_directive(
    research_arc: str | None,
    contribution_type: str | None = None,
) -> str:
    """Render the ``{{research_arc_directive}}`` prompt block for a case.

    When the brief supplies a ``research_arc`` hint, it is rendered VERBATIM
    inside an ADVISORY frame — explicitly NOT a rule, NOT a constraint on the
    pipeline order or contribution_type, just guidance the agent SHOULD weigh.
    When the arc is UNSET, a neutral "let the science decide" note is
    rendered and NOTHING is forced (no theory-first fallback for any
    contribution_type). ``contribution_type`` is accepted for signature
    compatibility but no longer drives the unset path. Backward-compatible: a
    case with no ``research_arc`` renders exactly the neutral note, the same
    behaviour as before this knob existed."""
    arc = validate_arc(research_arc)
    if arc is None:
        return _NEUTRAL_ARC_DIRECTIVE
    return f"""\
**Research arc (advisory hint from the brief):** {arc}
This is ADVISORY guidance you SHOULD weigh when shaping the EMPHASIS of the
work — which way the argument flows between math and data. It is NOT a rule:
it does NOT constrain the stage list (PIPELINE_ORDER is fixed) nor the
contribution_type. Let the science override it where the science warrants."""
