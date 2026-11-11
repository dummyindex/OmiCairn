"""Warn-only verifiers for SteeringStep.expected_changes.

A SteeringStep may declare a list of post-stage assertions in
``expected_changes`` (e.g. ``"mtime(figures/fig3_*.png) > NOW"``).
These predicates are advisory — they NEVER block the pipeline,
fail a stage, or downgrade a verdict. They exist so that:

  1. The planner has an explicit slot for "what should change" that
     the human can read in ``steering_plan.json`` before the step runs.
  2. After the stage completes, the orchestrator can compare actual
     filesystem state against the planner's expectations and surface
     drift as a warning in ``steering_log.jsonl``.

Predicates are expressed as strings (kept free-form in the plan
schema for back-compat with in-flight plans and the planner prompt),
but they are NOT evaluated by per-call regex. Instead a SINGLE
tolerant normalizer (`parse_predicate`) maps every accepted string
form onto a typed, structured `Predicate` (see `PredicateKind`), and
the evaluators dispatch on the structured `kind` — no string parsing
happens at evaluation time. This is the canonical-constants /
structured-helper shape the project mandates: one normalizer, one
predicate vocabulary, no scattered regexes.

Accepted string forms (canonical AND the loose forms the LLM planner
naturally emits — both normalize to the same structured predicate):

  mtime(<glob>) > NOW          — at least one matching path's mtime is
                                 newer than ``started_at_ts``.
  exists(<path>)               — path exists relative to ws.root.
  <path> exists                  (loose form of the above)
  not exists(<path>)           — path does not exist.
  <path> not exists / <path> does not exist   (loose forms)
  contains(<path>, '<substr>') — path's text contains substr.
  <path> contains '<substr>'     (loose form)
  not contains(<path>, '<substr>') — text does not contain substr.
  <path> not contains '<substr>'   (loose form)

Before evaluation, template tokens (``{{stage_dir}}`` etc.) are
expanded via `expand_tokens`, which reuses the canonical `Token`
vocabulary from `omicairn.constants` — the same token names the prompt
renderer substitutes. An unexpanded ``{{...}}`` token left in a
predicate at evaluation time is a planner bug, so a predicate that
STILL contains ``{{`` after expansion is reported UNRECOGNIZED rather
than silently globbing a literal ``{{stage_dir}}`` path.

Path operands may be workspace-RELATIVE (the canonical, preferred
form — resolved under ``ws_root``) or ABSOLUTE. All three evaluators
resolve their operand through ONE shared rule (`_resolve_path` /
`_resolve_glob`): an absolute target is used as-is (and, for globs,
matched from its filesystem anchor) rather than being joined onto
``ws_root`` or — the historical crash — passed as an absolute pattern
to ``Path.glob()`` (which raises ``NotImplementedError``). Keeping the
rule in one place is deliberate: the previous per-evaluator divergence
is what let ``_eval_mtime`` crash while ``_eval_exists`` quietly worked.

Anything genuinely unparseable (typos, natural-language assertions
like ``X contains a file matching '*.json'``, future predicates)
yields an UNRECOGNIZED result rather than crashing the orchestrator.
And as a final backstop, ANY unexpected exception raised inside an
evaluator is caught in `verify_predicate` and degraded to a non-blocking
FAIL — the warn-only verifier must NEVER crash the orchestrator.
The whole module is warn-only, so unrecognized predicates ARE the
failure mode worth surfacing — but always non-blocking.

This module depends only on the stdlib plus `omicairn.constants` (for the
canonical `Token` names — a leaf module with no import-cycle risk). It
does NOT import `steering`; the orchestrator owns the wiring.
"""
from __future__ import annotations

import dataclasses
import pathlib
import re

from .constants import Token

# ---------------------------------------------------------------------------
# Result + structured-predicate types
# ---------------------------------------------------------------------------

PASS = "pass"
FAIL = "fail"
UNRECOGNIZED = "unrecognized"


class PredicateKind:
    """Canonical predicate kinds. Constants, not magic strings."""

    MTIME_NEWER = "mtime_newer"  # at least one glob match is newer than start
    EXISTS = "exists"
    NOT_EXISTS = "not_exists"
    CONTAINS = "contains"
    NOT_CONTAINS = "not_contains"


@dataclasses.dataclass(frozen=True)
class Predicate:
    """A typed, parsed predicate. The evaluators dispatch on ``kind``.

    ``target`` is the glob/path operand; ``substr`` is set only for the
    contains kinds. The original ``raw`` string is retained for logging.
    """

    kind: str  # one of PredicateKind.*
    target: str
    substr: str = ""
    raw: str = ""


@dataclasses.dataclass(frozen=True)
class VerifyResult:
    """Outcome of one predicate check."""

    predicate: str
    status: str  # PASS | FAIL | UNRECOGNIZED
    detail: str  # human-readable explanation


# ---------------------------------------------------------------------------
# Token expansion (reuses the canonical Token vocabulary)
# ---------------------------------------------------------------------------
#
# Predicates may carry the same `{{token}}` placeholders the prompt
# renderer uses (most importantly `{{stage_dir}}`). They must be
# expanded BEFORE parsing/evaluation or the path operands point at a
# literal `{{stage_dir}}/...` path that never matches.

# The token names this verifier knows how to expand. Keyed by the
# canonical `Token` constant so the vocabulary stays in lockstep with
# the renderer — never type the bare string.
_EXPANDABLE_TOKENS = (Token.STAGE_DIR, Token.WORKSPACE_ROOT)


def expand_tokens(predicate: str, token_values: dict[str, str]) -> str:
    """Substitute ``{{token}}`` placeholders using ``token_values``.

    Only the canonical tokens this module supports (``stage_dir``,
    ``workspace_root``) are substituted; any other ``{{...}}`` is left
    intact so it is later flagged UNRECOGNIZED instead of silently
    matching a literal path.
    """
    for name in _EXPANDABLE_TOKENS:
        if name in token_values:
            predicate = predicate.replace("{{" + name + "}}", token_values[name])
    return predicate


# ---------------------------------------------------------------------------
# Single tolerant normalizer: string form -> structured Predicate
# ---------------------------------------------------------------------------
# ONE place that recognizes the predicate vocabulary. Both the
# canonical functional forms (`exists(X)`) and the loose forms the
# LLM planner emits (`X exists`) normalize onto the same `Predicate`.
# Evaluation (below) never touches a regex.

_RE_MTIME = re.compile(r"^\s*mtime\(\s*(?P<glob>.+?)\s*\)\s*>\s*NOW\s*$")

# Canonical functional forms.
_RE_EXISTS_FN = re.compile(
    r"^\s*(?P<neg>not\s+)?exists\(\s*(?P<path>[^)]+?)\s*\)\s*$"
)
_RE_CONTAINS_FN = re.compile(
    r"^\s*(?P<neg>not\s+)?contains\(\s*"
    r"(?P<path>[^,]+?)\s*,\s*"
    r"['\"](?P<substr>.+)['\"]\s*\)\s*$"
)

# Loose forms: ``<path> [not] exists`` / ``<path> does not exist``.
_RE_EXISTS_LOOSE = re.compile(
    r"^\s*(?P<path>\S+)\s+(?P<neg>not\s+|does\s+not\s+)?exists?\s*$"
)
# Loose form: ``<path> [not] contains '<substr>'``. The substring MUST
# be quoted — an unquoted tail is natural language (e.g. "contains a
# file matching ...") and is intentionally left UNRECOGNIZED.
_RE_CONTAINS_LOOSE = re.compile(
    r"^\s*(?P<path>\S+)\s+(?P<neg>not\s+|does\s+not\s+)?contains?\s+"
    r"['\"](?P<substr>.+)['\"]\s*$"
)


def parse_predicate(predicate: str) -> Predicate | None:
    """Map an accepted predicate string onto a structured `Predicate`.

    Returns ``None`` when the string matches no accepted form (the
    caller then reports it UNRECOGNIZED). This is the SINGLE recognizer
    for the predicate vocabulary — canonical and loose forms both land
    here, evaluation downstream is regex-free.
    """
    m = _RE_MTIME.match(predicate)
    if m:
        return Predicate(PredicateKind.MTIME_NEWER, m.group("glob"), raw=predicate)

    for rx in (_RE_EXISTS_FN, _RE_EXISTS_LOOSE):
        m = rx.match(predicate)
        if m:
            negated = bool(m.group("neg"))
            kind = PredicateKind.NOT_EXISTS if negated else PredicateKind.EXISTS
            return Predicate(kind, m.group("path"), raw=predicate)

    for rx in (_RE_CONTAINS_FN, _RE_CONTAINS_LOOSE):
        m = rx.match(predicate)
        if m:
            negated = bool(m.group("neg"))
            kind = (
                PredicateKind.NOT_CONTAINS if negated else PredicateKind.CONTAINS
            )
            return Predicate(
                kind, m.group("path"), substr=m.group("substr"), raw=predicate
            )

    return None


# ---------------------------------------------------------------------------
# Shared path resolution (ONE rule for all three evaluators)
# ---------------------------------------------------------------------------
#
# A predicate operand may be ABSOLUTE or workspace-RELATIVE. The
# canonical, preferred form is ws-relative (resolved under ``ws_root``),
# but the LLM planner — and `{{workspace_root}}` token expansion — can
# legitimately emit absolute targets. Every evaluator MUST resolve its
# operand through these two helpers so the absolute/relative rule lives
# in ONE place. The divergence that previously let `_eval_exists` join
# ``ws_root / abs`` (which silently returns ``abs``) while `_eval_mtime`
# passed an absolute pattern straight to ``Path.glob()`` (which raises
# ``NotImplementedError: Non-relative patterns are unsupported``) is
# exactly the regression these helpers eliminate.


def _resolve_path(target: str, ws_root: pathlib.Path) -> pathlib.Path:
    """Resolve a single path operand: absolute as-is, else under ws_root.

    ``ws_root / target`` already returns ``target`` unchanged when it is
    absolute, but we branch explicitly so the rule is identical to (and
    obviously consistent with) ``_resolve_glob`` below.
    """
    t = pathlib.Path(target)
    return t if t.is_absolute() else ws_root / target


def _resolve_glob(target: str, ws_root: pathlib.Path) -> list[pathlib.Path]:
    """Glob a path operand without ever passing an absolute pattern to
    ``ws_root.glob`` (which raises ``NotImplementedError``).

    Absolute target → glob from its anchor via the pattern tail; relative
    target → ``ws_root.glob(target)`` as before. Returns a sorted list.
    """
    t = pathlib.Path(target)
    if t.is_absolute():
        # Anchor (``/`` on POSIX) cannot itself be globbed with a
        # multi-segment pattern, so split off the anchor and glob the
        # remaining relative pattern from there.
        anchor = pathlib.Path(t.anchor)
        rel_pattern = str(t.relative_to(anchor))
        return sorted(anchor.glob(rel_pattern))
    return sorted(ws_root.glob(target))


# ---------------------------------------------------------------------------
# Structured evaluators (dispatch on Predicate.kind — no parsing here)
# ---------------------------------------------------------------------------


def _eval_mtime(p: Predicate, ws_root: pathlib.Path, started_at_ts: float):
    matches = _resolve_glob(p.target, ws_root)
    if not matches:
        return FAIL, f"no path matched glob {p.target!r}"
    fresh = [m for m in matches if m.stat().st_mtime > started_at_ts]
    if not fresh:
        return FAIL, (
            f"glob {p.target!r} matched {len(matches)} path(s) but none were "
            f"modified after step start (ts={started_at_ts:.0f})"
        )
    return PASS, f"{len(fresh)}/{len(matches)} match(es) modified after step start"


def _eval_exists(p: Predicate, ws_root: pathlib.Path, started_at_ts: float):
    target = _resolve_path(p.target, ws_root)
    found = target.exists()
    expected = p.kind == PredicateKind.EXISTS
    if found == expected:
        return PASS, f"{p.target} exists={found} matches expectation"
    return FAIL, f"{p.target} exists={found} but expected exists={expected}"


def _eval_contains(p: Predicate, ws_root: pathlib.Path, started_at_ts: float):
    target = _resolve_path(p.target, ws_root)
    if not target.exists():
        return FAIL, f"{p.target} does not exist"
    try:
        text = target.read_text(errors="replace")
    except (OSError, UnicodeDecodeError) as e:
        return FAIL, f"could not read {p.target}: {e}"
    found = p.substr in text
    expected = p.kind == PredicateKind.CONTAINS
    if found == expected:
        return PASS, (
            f"{p.target} contains={found} matches expectation "
            f"(substr={p.substr!r})"
        )
    return FAIL, (
        f"{p.target} contains={found} but expected contains={expected} "
        f"(substr={p.substr!r})"
    )


_EVALUATORS = {
    PredicateKind.MTIME_NEWER: _eval_mtime,
    PredicateKind.EXISTS: _eval_exists,
    PredicateKind.NOT_EXISTS: _eval_exists,
    PredicateKind.CONTAINS: _eval_contains,
    PredicateKind.NOT_CONTAINS: _eval_contains,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_SUPPORTED_FORMS = (
    "mtime(<glob>) > NOW, [not ]exists(<path>) / <path> [not ]exists, "
    "[not ]contains(<path>, '<substr>') / <path> [not ]contains '<substr>'"
)


def verify_predicate(
    predicate: str,
    ws_root: pathlib.Path,
    started_at_ts: float,
    token_values: dict[str, str] | None = None,
) -> VerifyResult:
    """Check one predicate string. Always returns a VerifyResult.

    ``token_values`` supplies values for ``{{token}}`` expansion (at
    minimum ``{{stage_dir}}``); when omitted, no expansion is done.
    Unrecognised predicates — including any still carrying an
    unexpanded ``{{...}}`` token — yield ``status=UNRECOGNIZED``: never
    raised, never blocking. The orchestrator logs them as warnings.
    """
    expanded = expand_tokens(predicate, token_values or {})

    # An unexpanded token survived — treat as unrecognized rather than
    # globbing a literal `{{stage_dir}}/...` path (root cause 1 guard).
    if "{{" in expanded:
        return VerifyResult(
            predicate=predicate,
            status=UNRECOGNIZED,
            detail=(
                f"predicate still contains an unexpanded template token after "
                f"expansion: {expanded!r}"
            ),
        )

    parsed = parse_predicate(expanded)
    if parsed is None:
        return VerifyResult(
            predicate=predicate,
            status=UNRECOGNIZED,
            detail=(
                f"predicate not recognised by the normalizer; supported forms: "
                f"{_SUPPORTED_FORMS}"
            ),
        )
    # Defense in depth: the verifier is warn-only and MUST NEVER crash
    # the orchestrator. Any unexpected error inside an evaluator (a path
    # idiom we didn't anticipate, an OS-level surprise, a future predicate
    # shape) degrades to a non-blocking FAIL carrying the error text — it
    # is never re-raised. This is the load-bearing guarantee: a malformed
    # predicate is a logged warning, not a dead run.
    try:
        status, detail = _EVALUATORS[parsed.kind](parsed, ws_root, started_at_ts)
    except Exception as e:  # noqa: BLE001 — warn-only: must not propagate
        return VerifyResult(
            predicate=predicate,
            status=FAIL,
            detail=(
                f"evaluator for kind={parsed.kind!r} raised "
                f"{type(e).__name__}: {e} (warn-only — not propagated)"
            ),
        )
    return VerifyResult(predicate=predicate, status=status, detail=detail)


def verify_expected_changes(
    expected: list[str],
    ws_root: pathlib.Path,
    started_at_ts: float,
    token_values: dict[str, str] | None = None,
) -> list[VerifyResult]:
    """Run all predicates on a SteeringStep.expected_changes list."""
    return [
        verify_predicate(p, ws_root, started_at_ts, token_values) for p in expected
    ]


def summarise_results(results: list[VerifyResult]) -> dict[str, int]:
    """Tally per-status counts for log emission."""
    counts = {PASS: 0, FAIL: 0, UNRECOGNIZED: 0}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    return counts
