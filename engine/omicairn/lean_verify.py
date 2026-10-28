"""Lean 4 proof checker for OmiCairn mathematical claims.

The owner's strong requirement: when a OmiCairn run makes a MATHEMATICAL claim
(a derived formula, a bound, a theorem, an identity, a closed-form), it
MUST be formalized in Lean 4 and the proof MUST check before the claim may
be reported as ESTABLISHED. An unverified derivation is downgraded
to a labeled "conjecture"/"empirical observation," never stated as proven.

This module is the verifier. It takes a ``.lean`` file (or a
statement+proof string), runs the LOCAL Lean toolchain, and returns a
structured :class:`LeanVerdict`. Proof CHECKING is CPU-only and runs in the
orchestrator environment — NEVER on the shared GPU host.

Compute placement (explicit)
----------------------------
Proof checking is a **CPU-class workload** — stated explicitly via
:func:`compute_class` (default :data:`constants.LEAN_DEFAULT_COMPUTE_CLASS`
= ``cpu``), not merely in prose. Two consequences:

- **CPU-appropriate placement + thread pinning.** ``lean``/``lake env lean``
  run on the local CPU toolchain. To stay a polite neighbour on a host SHARED
  with a co-located GPU run (or other CPU work), the child process is launched
  with a BOUNDED ``LEAN_NUM_THREADS`` (see :func:`_lean_thread_cap`) so a heavy
  Mathlib proof cannot saturate every core. Today's Mathlib-free lemmas finish
  sub-second regardless, so this is invisible for them; override with
  ``OMICAIRN_LEAN_NUM_THREADS`` (and we NEVER clobber a ``LEAN_NUM_THREADS`` the
  caller set themselves).
- **Forward hook for a FUTURE GPU-requiring Lean step.** The ``compute_class``
  on the verify path is the single extension point: if a future Lean step is
  flagged ``gpu`` (a heavy ML-guided tactic / proof search), the dispatch can
  route it like a GPU job (via ``compute_resources``) instead of assuming
  local-CPU. Default ``cpu`` preserves today's behaviour EXACTLY — nothing is
  speculatively built; the hook is a documented field + the dispatch reading it.

Design notes
------------
- **Subprocess safety mirrors ``claude_runner._run_with_watchdogs``**: the
  Lean process is started in its own session/process group and killed
  (SIGTERM → SIGKILL) on timeout, so a hung ``lean`` cannot block the run.
- **Toolchain discovery is robust**: explicit ``OMICAIRN_LEAN_BIN_DIR`` →
  ``PATH`` → the conventional ``~/.elan/bin`` (where ``elan`` installs).
- **Lake vs bare lean**: if the ``.lean`` lives inside a Lake project (a
  ``lakefile.lean``/``lakefile.toml`` is found in an ancestor dir), it is
  checked with ``lake env lean <file>`` so any project deps (e.g. Mathlib)
  resolve; otherwise plain ``lean <file>``. Mathlib-free lemmas need no
  project and check in well under a second.
- **The verdict parser is PURE** (:func:`parse_lean_output`) so its logic
  is unit-testable on captured Lean output fixtures WITHOUT a Lean install.
  The mechanism's LOGIC is tested regardless of install state.

This is GENERAL research infrastructure — domain-agnostic, no
domain-specific content.
"""
from __future__ import annotations

import dataclasses
import os
import pathlib
import re
import shutil
import signal
import subprocess
import time
from typing import Optional

from .constants import (
    COMPUTE_CLASS_CPU,
    COMPUTE_CLASS_GPU,
    ENV_LEAN_BIN_DIR,
    ENV_LEAN_NUM_THREADS,
    ENV_LEAN_TIMEOUT,
    LAKE_BIN,
    LEAN_BIN,
    LEAN_DEFAULT_COMPUTE_CLASS,
    LEAN_DEFAULT_TIMEOUT_S,
    LEAN_MATHLIB_PROJECT_DIRNAME,
    LEAN_MAX_DEFAULT_THREADS,
    LEAN_NATIVE_NUM_THREADS_ENV,
    LEAN_THREAD_DIVISOR,
)


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class LeanVerdict:
    """Structured result of checking a Lean source.

    ``compiled`` is the headline: True iff Lean exited 0 with no error
    diagnostics. ``theorems_checked`` lists the theorem/lemma/example
    names declared in the source (best-effort, parsed from the source —
    Lean does not enumerate them on success). ``tool_missing`` is True
    when no Lean toolchain was found (distinct from a real compile
    failure). ``timed_out`` is True when the watchdog killed a hung
    process. The dataclass is JSON-serialisable via :meth:`to_dict` for
    storage as a workspace artifact (``<Claim>.verdict.json``).
    """
    compiled: bool
    theorems_checked: list[str] = dataclasses.field(default_factory=list)
    errors: list[str] = dataclasses.field(default_factory=list)
    returncode: Optional[int] = None
    timed_out: bool = False
    tool_missing: bool = False
    duration_s: float = 0.0
    lean_file: str = ""
    stdout: str = ""
    stderr: str = ""
    # The compute class this check ran UNDER (canonical
    # constants.COMPUTE_CLASS_*). Default ``cpu`` — proof checking is a
    # CPU-class workload run on the local toolchain. Recorded on the verdict so
    # the placement is AUDITABLE (and so a future gpu-flagged Lean step is
    # visible in the artifact). See module docstring "Compute placement".
    compute_class: str = COMPUTE_CLASS_CPU
    # WARN-only proof-SUBSTANCE hints (rules/method_depth.md M3b), from
    # :func:`substance_warnings`, which runs THREE heuristic layers: (1) the
    # "assume the empirical finding as a premise, then apply a single
    # arithmetic-monotonicity library lemma" trivial pattern, (2) the
    # DEPTH layer — an ELEMENTARY closed/constant-table fact proved by a single
    # algebraic-identity tactic (`ring`/`norm_num`/`rfl`), e.g. the
    # XOR-not-linearly-separable parity table (true-but-textbook), and (3) the
    # SIBLING layer — a single linear equality closed by `linarith`/`omega` where
    # a lone hypothesis is the goal merely TRANSPOSED (same atoms), deriving
    # nothing (the d6 `chk` catch). This NEVER
    # affects ``compiled`` — the real substance judgment is the analysis
    # REVIEW's proof-theater check (rules/method_depth.md M3b); this is only a hint for
    # a human/reviewer. Empty for a substantive proof (and for any proof the
    # heuristics cannot confidently classify, which is the common case — both
    # are conservative, e.g. any `decide`/`native_decide` witness-check
    # disables layer 2).
    substance_warnings: list[str] = dataclasses.field(default_factory=list)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @property
    def ok(self) -> bool:
        """True iff the proof checked clean (alias of ``compiled``)."""
        return self.compiled


# ---------------------------------------------------------------------------
# Toolchain discovery
# ---------------------------------------------------------------------------

def _elan_bin_dir() -> pathlib.Path:
    """Conventional elan install dir (``~/.elan/bin``)."""
    return pathlib.Path.home() / ".elan" / "bin"


# ---------------------------------------------------------------------------
# Reusable Mathlib Lake project location (safe, canonical)
# ---------------------------------------------------------------------------

def mathlib_project_dir(root: str | pathlib.Path) -> pathlib.Path:
    """Return the reusable Mathlib-backed Lake project dir under ``root``.

    The dir name is the CANONICAL :data:`LEAN_MATHLIB_PROJECT_DIRNAME`
    (``.lean-mathlib-omicairn``) — never assembled ad-hoc. The leading DOT is
    load-bearing (hides the bulky Mathlib checkout) AND a safety property: a
    DASH-leading name (``-lean-mathlib-omicairn``) is parsed as an option flag by
    shells / ``lake`` / ``lean`` / argparse, which is exactly how a stray
    ``-lean-mathlib-omicairn/`` dir was once created from a hand-typed
    ``lake new -lean-mathlib-omicairn math``.

    This function is the single safe constructor: it asserts the dot prefix
    and refuses to ever return a path whose final component begins with ``-``,
    so OmiCairn code cannot re-create the stray dir.
    """
    name = LEAN_MATHLIB_PROJECT_DIRNAME
    # Defensive invariants on the canonical constant itself — cheap, and they
    # turn a future bad edit of the constant into an immediate, local failure
    # rather than a stray dash-dir at the filesystem root.
    assert name.startswith("."), (
        f"Mathlib project dir name must be dot-prefixed, got {name!r}"
    )
    assert not name.startswith("-"), (
        f"Mathlib project dir name must not begin with '-' (parsed as a "
        f"flag), got {name!r}"
    )
    return pathlib.Path(root) / name


def _safe_path_arg(path: str | pathlib.Path) -> str:
    """Return ``path`` as a string safe to pass as a command ARGUMENT.

    ``lake``/``lean`` (and most CLIs) parse a leading ``-`` as an option, so a
    path whose basename starts with ``-`` would be mis-read as a flag. We
    normalise to an absolute path (which always starts with ``/`` on POSIX) so
    the argument can never be flag-confused. Callers ALSO place a ``--``
    end-of-options sentinel before file args (see :func:`_run_lean` cmd
    construction) — belt and suspenders.
    """
    return str(pathlib.Path(path).resolve())


def _resolve_bin(name: str) -> Optional[str]:
    """Find a toolchain binary by name.

    Order: ``OMICAIRN_LEAN_BIN_DIR`` (if set) → ``PATH`` → ``~/.elan/bin``.
    Returns an absolute path string, or ``None`` if not found.
    """
    explicit = os.environ.get(ENV_LEAN_BIN_DIR, "").strip()
    if explicit:
        cand = pathlib.Path(explicit).expanduser() / name
        if cand.exists() and os.access(cand, os.X_OK):
            return str(cand)
    on_path = shutil.which(name)
    if on_path:
        return on_path
    cand = _elan_bin_dir() / name
    if cand.exists() and os.access(cand, os.X_OK):
        return str(cand)
    return None


def lean_toolchain_available() -> bool:
    """True iff a ``lean`` binary is discoverable (gates the real-Lean
    test; the parser logic is tested separately on fixtures)."""
    return _resolve_bin(LEAN_BIN) is not None


def _find_lake_project(start: pathlib.Path) -> Optional[pathlib.Path]:
    """Walk ancestors of ``start`` for a Lake project root (the dir that
    holds ``lakefile.lean`` or ``lakefile.toml``). Returns it or None."""
    cur = start if start.is_dir() else start.parent
    cur = cur.resolve()
    for d in (cur, *cur.parents):
        if (d / "lakefile.lean").exists() or (d / "lakefile.toml").exists():
            return d
    return None


def _wall_timeout_s() -> int:
    """Per-proof wall timeout, overridable via ``OMICAIRN_LEAN_TIMEOUT_S``."""
    raw = os.environ.get(ENV_LEAN_TIMEOUT, "").strip()
    if not raw:
        return LEAN_DEFAULT_TIMEOUT_S
    try:
        return max(1, int(raw))
    except ValueError:
        return LEAN_DEFAULT_TIMEOUT_S


# ---------------------------------------------------------------------------
# Compute placement (CPU-class; forward hook for a future GPU-requiring step)
# ---------------------------------------------------------------------------

def compute_class(requested: str | None = None) -> str:
    """The canonical compute class a Lean proof-check runs under.

    Lean proof checking is a **CPU-class workload** — this function makes that
    explicit rather than leaving it implicit in a docstring. It returns one of
    the canonical :data:`constants.COMPUTE_CLASS_CPU` / ``COMPUTE_CLASS_GPU``
    strings (never a bare literal — design rule "no string-soup").

    ``requested`` is the FORWARD HOOK: a future Lean step that genuinely needs
    special compute (a heavy ML-guided tactic / proof search) may pass
    ``COMPUTE_CLASS_GPU`` so callers route it like a GPU job (via
    ``compute_resources``) instead of assuming local-CPU. ``None`` (today's
    every-caller default) resolves to :data:`constants.LEAN_DEFAULT_COMPUTE_CLASS`
    = ``cpu`` — i.e. EXACTLY current behaviour, zero change. An unrecognised
    value falls back to the default (conservative: never silently treat an
    unknown class as GPU).
    """
    if requested is None:
        return LEAN_DEFAULT_COMPUTE_CLASS
    val = str(requested).strip().lower()
    if val in (COMPUTE_CLASS_CPU, COMPUTE_CLASS_GPU):
        return val
    return LEAN_DEFAULT_COMPUTE_CLASS


def _lean_thread_cap() -> int:
    """Bounded worker-thread count for a co-located ``lean``/``lake`` run.

    ``lean`` honours ``LEAN_NUM_THREADS``. On a host SHARED with a GPU run (or
    other CPU work) a heavy Mathlib proof must not grab every core, so OmiCairn
    pins a BOUNDED default: ``max(1, floor(logical_cores / DIVISOR))`` clamped
    to :data:`constants.LEAN_MAX_DEFAULT_THREADS`. An explicit positive
    ``OMICAIRN_LEAN_NUM_THREADS`` overrides exactly; a non-positive / unparsable
    value falls back to the computed default. PURE w.r.t. its own env read —
    does not mutate ``os.environ``. Today's Mathlib-free lemmas finish
    sub-second regardless of this cap, so it is invisible for them.
    """
    raw = os.environ.get(ENV_LEAN_NUM_THREADS, "").strip()
    if raw:
        try:
            n = int(raw)
            if n > 0:
                return n
        except ValueError:
            pass
    cores = os.cpu_count() or 1
    cap = max(1, cores // LEAN_THREAD_DIVISOR)
    return min(cap, LEAN_MAX_DEFAULT_THREADS)


def _lean_child_env() -> dict[str, str]:
    """Child-process environment for a Lean run with CPU thread-pinning.

    Copies the current environment and sets ``LEAN_NUM_THREADS`` to the bounded
    :func:`_lean_thread_cap`, UNLESS the caller already set ``LEAN_NUM_THREADS``
    themselves (we never clobber an explicit operator choice). Returns a fresh
    dict — never mutates ``os.environ`` in the parent.
    """
    env = dict(os.environ)
    if not env.get(LEAN_NATIVE_NUM_THREADS_ENV, "").strip():
        env[LEAN_NATIVE_NUM_THREADS_ENV] = str(_lean_thread_cap())
    return env


# ---------------------------------------------------------------------------
# Source / output parsing (PURE — fixture-testable without Lean installed)
# ---------------------------------------------------------------------------

# Declaration keywords Lean checks. We surface the declared names so a
# verdict can record WHICH theorems were established (Lean prints nothing
# on success, so the names come from the source).
_DECL_RE = re.compile(
    r"^\s*(?:@\[[^\]]*\]\s*)?(?:private\s+|protected\s+|noncomputable\s+)?"
    r"(?:theorem|lemma|example|def)\s+([A-Za-z_][A-Za-z0-9_'.]*)",
    re.MULTILINE,
)

# Lean diagnostic lines look like ``<file>:L:C: error: ...``. We treat any
# ``error:`` diagnostic as a failure. ``sorry`` is an unproven hole and
# MUST count as a failure even though Lean only warns about it — an
# unproven derivation may not be cited as proven (the whole point).
_ERROR_LINE_RE = re.compile(r":\d+:\d+:\s*error:\s*(.*)$", re.MULTILINE)
_BARE_ERROR_RE = re.compile(r"^error:\s*(.*)$", re.MULTILINE)
_SORRY_RE = re.compile(r"\bdeclaration uses 'sorry'\b|(?<![A-Za-z])sorry(?![A-Za-z])")


def strip_lean_comments(source: str) -> str:
    """Return ``source`` with Lean 4 comments removed (CODE only).

    Strips nesting block comments ``/- ... -/`` (Lean block comments nest) and
    line comments ``-- ...``. Used before a source-side ``sorry`` scan so the
    word "sorry" appearing in a comment / docstring (e.g. ``-- no sorry here``,
    ``/-- proves X without sorry -/``) never false-flags a clean proof and
    wrongly demotes a real sorry-free theorem. The AUTHORITATIVE unproven-hole
    signal stays the compiler diagnostic ``declaration uses 'sorry'`` (matched
    on raw Lean OUTPUT); only the SOURCE-side scan needs comment stripping.
    Pure; no Lean needed. Conservative: a stray unbalanced ``/-`` (a syntax
    error Lean would reject anyway) consumes to EOF — such source never
    compiles and is never recorded as verified.
    """
    if not source:
        return source or ""
    out: list[str] = []
    i = 0
    n = len(source)
    depth = 0  # block-comment nesting depth (Lean block comments nest)
    while i < n:
        two = source[i : i + 2]
        if depth > 0:
            if two == "/-":
                depth += 1
                i += 2
            elif two == "-/":
                depth -= 1
                i += 2
            else:
                i += 1
            continue
        if two == "/-":
            depth += 1
            i += 2
            continue
        if two == "--":
            nl = source.find("\n", i)
            if nl == -1:
                break  # line comment runs to EOF
            i = nl  # keep the newline
            continue
        out.append(source[i])
        i += 1
    return "".join(out)


def source_uses_sorry(source: str) -> bool:
    """True iff ``source`` uses a real ``sorry``/``admit`` hole in CODE.

    Comment-aware: strips Lean comments first (:func:`strip_lean_comments`) so
    the word in a docstring/comment is ignored. This is the SOURCE-side check;
    the compiler diagnostic remains authoritative when Lean output is available.
    """
    code = strip_lean_comments(source or "")
    return bool(
        re.search(r"(?<![A-Za-z])sorry(?![A-Za-z])", code)
        or re.search(r"(?<![A-Za-z])admit(?![A-Za-z])", code)
    )


def parse_theorem_names(source: str) -> list[str]:
    """Extract declared theorem/lemma/example/def names from Lean source.

    Pure; no Lean needed. Used to populate ``theorems_checked``.
    """
    return _DECL_RE.findall(source or "")


# --- Proof-SUBSTANCE heuristic (WARN-only; see LeanVerdict.substance_warnings)
#
# A purely syntactic *shadow* of the M3b "trivial pattern": a theorem that
# (a) takes a hypothesis (a premise — the empirical finding restated as an
# assumption) AND (b) is discharged by a SINGLE application of a known
# arithmetic-monotonicity / transitivity library lemma, with no construction
# (no `⟨...⟩` anonymous-constructor witness, no `intro`/`refine`/`obtain`/
# `have`/`calc`/`cases`/`induction`, no multi-step `by` block). This is a
# HINT, not a verdict — many substantive proofs use these lemmas as one step
# among many, and many trivial proofs won't match. The authoritative judgment
# is the analysis REVIEW's proof-theater check. We deliberately keep the lemma set
# SMALL and the trigger CONSERVATIVE (single-lemma body only) to avoid false
# positives. These are generic Lean/core lemma names, not domain keywords.
_TRIVIAL_MONOTONE_LEMMAS: frozenset[str] = frozenset({
    "Nat.mul_le_mul_left",
    "Nat.mul_le_mul_right",
    "Nat.mul_le_mul",
    "Nat.add_le_add_left",
    "Nat.add_le_add_right",
    "Nat.le_trans",
    "le_trans",
    "Nat.mul_le_mul_of_le_left",
    "Nat.mul_le_mul_of_le_right",
})

# A theorem "takes a hypothesis" if it binds a parameter whose type is a
# proposition we treat as a premise — heuristically, a binder `(h : ... ≤/</=
# ...)` (an ordering/equality hypothesis, the shape of "the empirical finding
# as an assumption"). Conservative: only relational hypotheses count.
_HYP_BINDER_RE = re.compile(r"\([^():]*:[^()]*(?:≤|<|≥|>|=)[^()]*\)")

# Multi-step / constructive proof markers — if ANY appears, the proof is not a
# bare single-lemma application and we do NOT warn (it is doing real work).
_CONSTRUCTIVE_MARKERS_RE = re.compile(
    r"(?:^|\s)(?:intro|refine|obtain|rintro|have|calc|cases|induction|"
    r"constructor|use|exists|apply\b.*\n.*\bapply)\b"
    r"|⟨"
)


def trivial_pattern_warnings(source: str) -> list[str]:
    """Heuristic WARN-only detector for the M3b trivial proof pattern.

    Returns a (possibly empty) list of human-readable warnings. A theorem
    trips the heuristic ONLY when it BOTH (a) binds a relational hypothesis
    (the empirical finding restated as a premise) AND (b) its proof body is a
    SINGLE application of a known arithmetic-monotonicity/transitivity library
    lemma with no construction. PURE — needs no Lean install. This is a hint
    toward the M3b review judgment, never a compile failure.

    The companion :func:`elementary_theorem_warnings` catches the DEPTH layer
    ABOVE this one — a closed/constant-fact theorem proved by a single
    algebraic-identity tactic. :func:`substance_warnings` runs both.
    """
    src = source or ""
    if not _HYP_BINDER_RE.search(src):
        return []
    if _CONSTRUCTIVE_MARKERS_RE.search(src):
        return []
    hits = sorted({lem for lem in _TRIVIAL_MONOTONE_LEMMAS if lem in src})
    if not hits:
        return []
    return [
        "M3b substance hint (WARN-only, heuristic): the proof binds a "
        "relational hypothesis and is discharged by a single "
        f"arithmetic-monotonicity/transitivity lemma ({', '.join(hits)}) with "
        "no construction. This is the SHADOW of the trivial 'assume-the-"
        "finding + apply monotonicity' pattern (rules/method_depth.md M3b). "
        "It is NOT a failure — the analysis REVIEW judges "
        "whether the theorem derives a non-obvious consequence or merely "
        "restates the empirical claim with an arithmetic hat."
    ]


# --- Elementary-theorem heuristic (WARN-only; the DEPTH layer above
#     trivial_pattern_warnings; see LeanVerdict.substance_warnings)
#
# trivial_pattern_warnings catches "assume the finding, apply monotonicity."
# This catches a DIFFERENT proof-theater shape that is substantive by that
# heuristic yet ELEMENTARY: a theorem whose ENTIRE proof closes by a single
# closed-form ALGEBRAIC-IDENTITY tactic (`ring`/`norm_num`/`rfl`) over a SMALL
# HAND-WRITTEN CONSTANT TABLE — a self-contained arithmetic/boolean fact a
# competent reviewer sees true by inspection (the textbook example). This was
# caught LIVE: fair-erasure-durability's headline H4 theorem is the
# XOR-is-not-linearly-separable example (a 4-point parity table closed by
# `ring`/`norm_num`), which is true-but-textbook and derives no non-obvious
# consequence — yet it stamped substance_warnings:[] / M3b-substantive.
#
# THE STRUCTURAL LINE (elementary closed-constant fact vs deep constructed
# witness). The hard part is NOT flagging the GENUINELY-deep d5/d7 proofs,
# which ALSO reduce constant objects. The discriminator is the CLOSING TACTIC:
#   * `ring` / `norm_num` / `rfl` prove a closed ALGEBRAIC IDENTITY — they
#     reduce a numeric/algebraic equality that holds by the field/ring axioms.
#     An identity over a tiny hand-written table is exactly the by-inspection
#     fact (XOR parity). These TRIP the heuristic.
#   * `decide` / `native_decide` VERIFY a CONSTRUCTED witness against a
#     COMPUTED predicate — the d5 (`R Rᵀ = I₈`, CKA=1, kNN=1 on built rational
#     matrices) and d7 (Cauchy–Schwarz ceiling on measured-collinearity unit
#     vectors) shape, where the constructed object IS the contribution and the
#     truth is NOT visible from the statement. These NEVER trip — `decide`/
#     `native_decide` anywhere in the source disables this heuristic, so a
#     legitimate witness-check can never be false-flagged.
#   * `omega` derives a LINEAR bound (often over FREE variables) — a real
#     consequence, not a closed constant. It also disables the heuristic.
#   * A nontrivial lemma application, `induction`, `calc`, `apply`, `exact`-of-
#     a-named-lemma → real derivation; disables the heuristic.
# We are deliberately CONSERVATIVE: warn only when EVERY closing tactic in the
# source is an identity tactic AND no witness/bound/lemma machinery appears.
# False positives on legit witness-checks must degrade to a reviewer prompt,
# never a block — the warning's whole purpose is to SURFACE the theorem to the
# REVIEW M3b grader, not auto-fail. These are generic Lean/Mathlib tactic
# names, not domain keywords.

# Closing tactics that discharge a CLOSED ALGEBRAIC/NUMERIC IDENTITY by ring/
# field axioms or definitional reduction — the by-inspection fact shape.
_IDENTITY_TACTICS: frozenset[str] = frozenset({
    "ring", "ring_nf", "norm_num", "rfl",
})

# Tactics/markers whose PRESENCE anywhere in the source means the proof is
# doing real work (a constructed-witness check, a derived bound, induction, or
# a named-lemma derivation) — they DISABLE the elementary heuristic so a
# genuine d5/d7-style proof is never flagged. `decide`/`native_decide` are the
# witness-CHECK tactics (the d5/d7 contribution shape); `omega` derives a
# linear bound; the rest are multi-step derivation markers.
_DEEP_PROOF_MARKERS: frozenset[str] = frozenset({
    "decide", "native_decide", "omega",
    "induction", "calc", "nlinarith", "linarith", "gcongr", "positivity",
})
_DEEP_PROOF_MARKER_RE = re.compile(
    r"(?<![A-Za-z_])(?:" + "|".join(sorted(_DEEP_PROOF_MARKERS)) + r")(?![A-Za-z_])"
)

# The LINEAR-EQUALITY solver subset of the deep markers. `linarith`/`omega`/
# `linear_combination` close a LINEAR (in)equality goal by Gaussian-style
# elimination over the cited/in-context linear facts. They are "deep" enough to
# disable the ELEMENTARY heuristic above (a derived bound IS a consequence) —
# but they ALSO admit a degenerate use that derives NOTHING: closing a goal that
# is a SINGLE hypothesis merely TRANSPOSED across the equals sign (e.g.
# `linarith [hpart]` where `hpart : a*g = b - c` proves `b = a*g + c`). That is
# the LINEAR-REARRANGEMENT proof-theater :func:`linear_rearrangement_warnings`
# targets — the M3b sibling of the elementary/trivial layers. Defined ONCE as a
# canonical subset; referenced (never re-inlined) by the rearrangement detector.
# NOTE: `nlinarith` is DELIBERATELY EXCLUDED — it solves NON-linear goals
# (products/squares) and is the real-work tactic (the fair-erasure Cauchy–Schwarz
# `nlinarith` derives ⟪Q,Δ⟫²≤‖Δ‖²·… from independent premises); flagging it
# would false-positive on substance.
_LINEAR_EQUALITY_SOLVERS: frozenset[str] = frozenset({
    "linarith", "omega", "linear_combination",
})
assert _LINEAR_EQUALITY_SOLVERS <= _DEEP_PROOF_MARKERS | {"linear_combination"}, (
    "linear-equality solvers must be a recognised subset of the deep markers "
    "(linear_combination is a term-mode closer, not in _DEEP_PROOF_MARKERS)"
)

# Tactic/marker tokens whose presence means the proof is doing MORE than a
# single linear-equality transposition: a non-linear solver, a witness check,
# induction, a calc chain, or a positivity/monotonicity derivation. If ANY of
# these appears, the rearrangement detector bails (the proof may combine facts
# or derive a genuine bound). These are the deep markers MINUS the linear
# solvers themselves (which are EXPECTED in the very pattern we target), so the
# detector keys on "a linear solver AND nothing heavier."
_REARRANGEMENT_DISQUALIFIERS: frozenset[str] = (
    _DEEP_PROOF_MARKERS - _LINEAR_EQUALITY_SOLVERS
) | frozenset({"nlinarith"})
_REARRANGEMENT_DISQUALIFIER_RE = re.compile(
    r"(?<![A-Za-z_])(?:" + "|".join(sorted(_REARRANGEMENT_DISQUALIFIERS)) + r")(?![A-Za-z_])"
)
_LINEAR_SOLVER_RE = re.compile(
    r"(?<![A-Za-z_])(?:" + "|".join(sorted(_LINEAR_EQUALITY_SOLVERS)) + r")(?![A-Za-z_])"
)

# Lean tokens that are NOT atoms when tokenising a goal/hypothesis proposition:
# the type names the atoms range over, plus structural keywords. Stripping these
# leaves the variable/product identifiers whose SET we compare between goal and
# hypothesis. Conservative: an unrecognised identifier counts as an atom (so we
# only ever OVER-count atoms, never silently equate two different statements).
_NON_ATOM_TOKENS: frozenset[str] = frozenset({
    "ℝ", "ℚ", "ℤ", "ℕ", "Real", "Rat", "Int", "Nat",
    "by", "fun", "if", "then", "else", "True", "False",
})

# A `by`-tactic token: matches whole-word tactic names so `ring` is not seen
# inside `ring_nf`/`Bring`. Used to find the closing/terminal tactic of a
# proof block.
_TACTIC_TOKEN_RE = re.compile(r"(?<![A-Za-z_'])([A-Za-z_][A-Za-z0-9_]*)(?![A-Za-z0-9_'])")

# A `by` proof block introduces tactic mode; an elementary proof reaches its
# conclusion purely by identity tactics inside one. We also recognise a
# term-mode proof that is `exact ⟨...⟩` of identity-closed lemmas, but the
# common live shape is `:= by ... ring`.
_BY_BLOCK_RE = re.compile(r":=\s*by\b", re.MULTILINE)


def elementary_theorem_warnings(source: str) -> list[str]:
    """Heuristic WARN-only detector for ELEMENTARY-theorem proof-theater.

    Flags a theorem that compiles and is "substantive" by
    :func:`trivial_pattern_warnings` yet is a closed/constant-table result
    proved by a single algebraic-identity tactic (`ring`/`norm_num`/`rfl`) —
    a textbook fact deriving no non-obvious consequence (the live XOR-table
    catch). PURE — needs no Lean install. A HINT for the REVIEW M3b grader,
    NEVER a compile failure.

    Conservative by design: returns ``[]`` (no warning) the moment the source
    contains ANY witness-check / bound / derivation marker
    (:data:`_DEEP_PROOF_MARKERS` — notably ``decide``/``native_decide``, the
    d5/d7 witness-check shape, and ``omega``, a derived linear bound). It warns
    ONLY when the proofs are closed-form identity tactics over constants with
    none of that machinery, so a legitimate constructed-witness proof can never
    be false-flagged.
    """
    src = source or ""
    # A proof that anywhere VERIFIES a constructed witness (decide/native_decide),
    # derives a bound (omega/linarith/…), inducts, or chains a calc is doing real
    # work — never flag it. This single guard is what protects d5/d7.
    if _DEEP_PROOF_MARKER_RE.search(src):
        return []
    # Must have at least one `by` tactic block to be the "proved by a tactic"
    # shape we target (a bare term proof `:= ⟨...⟩` or `:= lemma ...` is handled
    # by trivial_pattern_warnings / left to the reviewer).
    if not _BY_BLOCK_RE.search(src):
        return []
    # Collect the distinct tactic NAMES that appear (whole-word). The proof is
    # "identity-only" iff every NON-structural tactic it uses is an identity
    # tactic. Structural tactics that merely shuffle the goal without supplying
    # mathematical content are allowed alongside the identity closer.
    _STRUCTURAL_TACTICS = {
        "by", "simp", "only", "refine", "exact", "constructor", "intro",
        "intros", "show", "have", "fun", "and", "And",
    }
    used = set(_TACTIC_TOKEN_RE.findall(src))
    # The mathematically-load-bearing tactics: drop the structural/keyword set.
    content_tactics = {
        t for t in used
        if t in _IDENTITY_TACTICS
        or t in {
            # other recognised closers/combinators that DON'T by themselves
            # indicate deep work but also aren't identity tactics; if any of
            # these appears we cannot conclude "identity-only" and bail.
            "apply", "rw", "rewrite", "linear_combination", "field_simp",
            "cases", "rcases", "obtain", "use", "exists",
        }
    }
    # If a non-identity content tactic is present, the proof may derive
    # something — be conservative and do NOT warn.
    if content_tactics - _IDENTITY_TACTICS:
        return []
    # Require at least one identity tactic actually present (else there is no
    # identity proof to flag).
    id_hits = sorted(used & _IDENTITY_TACTICS)
    if not id_hits:
        return []
    return [
        "M3b ELEMENTARY-theorem hint (WARN-only, heuristic): every proof in "
        f"this source closes by a closed-form algebraic-identity tactic "
        f"({', '.join(id_hits)}) over what is, structurally, a small constant "
        "table or a closed numeric/algebraic statement — with NO "
        "constructed-witness check (`decide`/`native_decide`), derived bound "
        "(`omega`/`linarith`), induction, or named-lemma derivation. This is "
        "the SHADOW of ELEMENTARY proof-theater (rules/method_depth.md M3b): "
        "a theorem that is TRUE-and-constructed yet textbook — e.g. the "
        "XOR-is-not-linearly-separable parity table closed by `ring` — "
        "deriving nothing a competent reviewer would not see by inspection. "
        "It is NOT a failure and NOT the trivial assume-then-monotonicity "
        "pattern — it is the DEPTH layer above it. The analysis REVIEW "
        "judges (proof-theater check) whether the headline theorem "
        "derives a NON-OBVIOUS consequence (a witness whose truth is not "
        "visible from the statement, a bound that constrains the claim) versus "
        "restating a textbook/constant-table result with a green checkmark."
    ]


# --- Linear-rearrangement heuristic (WARN-only; the M3b SIBLING of the
#     elementary/trivial layers; see LeanVerdict.substance_warnings)
#
# trivial_pattern_warnings catches "assume the finding, apply monotonicity."
# elementary_theorem_warnings catches "close a constant table by ring/norm_num."
# This third layer catches a use of the LINEAR-EQUALITY solvers
# (`linarith`/`omega`/`linear_combination`) that derives NOTHING: the goal is a
# single linear EQUALITY and there is a hypothesis that is the SAME equality
# merely TRANSPOSED across the equals sign — same atoms on both sides, the
# solver only moves terms. This slipped the gate LIVE: example-study-d6's headline
#
#     theorem chk (rMR rMS rSR rpart g : ℝ) (hpart : rpart * g = rMR - rMS * rSR) :
#         rMR = rpart * g + rMS * rSR := by linarith [hpart]
#
# was sold as "an identity we prove in Lean 4," yet `hpart` IS the goal
# rearranged — `linarith` derives no new consequence. `linarith`/`omega` are in
# _DEEP_PROOF_MARKERS (they CAN derive real bounds), so the elementary layer
# never fires on them; this layer is the narrow, structural detector for the
# DEGENERATE transposition use.
#
# THE STRUCTURAL LINE (transpose ONE equality vs combine SEVERAL / derive an
# inequality). We flag ONLY when ALL hold — anything ambiguous is left clean:
#   1. The proof's mathematical content is a linear-equality solver and nothing
#      heavier (no nlinarith / decide / native_decide / induction / calc /
#      positivity / gcongr — _REARRANGEMENT_DISQUALIFIERS). A nlinarith deriving
#      a quadratic bound (fair-erasure Cauchy–Schwarz) is REAL and bails here.
#   2. The goal is a SINGLE linear (in)equality over real/rational/int atoms.
#   3. There is EXACTLY ONE relational hypothesis in the signature, it is also a
#      linear equality, and its ATOM SET EQUALS the goal's atom set. Same atoms
#      both sides ⇒ the solver is transposing, not COMBINING independent facts
#      (≥2 hyps) and not deriving a bound over FREE vars (goal atoms ⊋ hyp atoms,
#      the legit `omega` shape). If we cannot confidently extract atoms, we DON'T
#      flag (conservative — a missed theater case beats a false flag on a real
#      proof, same philosophy as the layers above).

# A single goal relation: capture the LHS, the relation, the RHS. We only ever
# look at the FIRST top-level relation in the goal (a conjunction/∃ goal has
# extra structure and is left to the reviewer — see the ∧/∃/∀ bail below).
_GOAL_RELATION_RE = re.compile(r"^(.*?)(=|≤|≥|<|>)(.*)$", re.DOTALL)
# Goal-level structure that means the statement is NOT a single bare relation
# (a conjunction, an existential/universal, an implication). Presence ⇒ bail.
_COMPOUND_GOAL_RE = re.compile(r"[∧∨∃∀↔→]|\bAnd\b|\bOr\b|\bExists\b")
# A relational hypothesis binder: `(h : <prop-with-a-relation>)`. We capture the
# binder NAME(s) and the proposition so we can test whether the prop is a linear
# equality whose atoms match the goal's. Mirrors _HYP_BINDER_RE's shape but also
# captures the name for the actionable message.
_NAMED_REL_HYP_RE = re.compile(
    r"\(\s*([A-Za-z_][A-Za-z0-9_']*)\s*:\s*([^()]*(?:=|≤|≥|<|>)[^()]*)\)"
)


def _proof_atoms(prop: str) -> frozenset[str]:
    """The SET of variable/product atoms in a Lean (in)equality proposition.

    A simple identifier tokenizer (the same conservative tactic the module's
    other regex parsers use): take whole-word identifiers, drop numeric
    literals and the small set of type-name / keyword :data:`_NON_ATOM_TOKENS`.
    A product `rpart * g` contributes BOTH `rpart` and `g`, so transposing
    `a*g = b - c` into `b = a*g + c` yields the SAME atom set — exactly the
    transposition signature. Unrecognised identifiers count AS atoms, so this
    only ever OVER-counts (never silently equates two different statements).
    """
    toks = _TACTIC_TOKEN_RE.findall(prop or "")
    return frozenset(t for t in toks if t not in _NON_ATOM_TOKENS)


def _goal_after_binders(decl: str) -> Optional[str]:
    """Best-effort extract the GOAL proposition of a single theorem decl.

    ``decl`` is the text from ``theorem``/``lemma`` up to (not including)
    ``:=``. The goal is what follows the LAST top-level ``:`` that closes the
    binder list — heuristically, the segment after the final ``)`` (end of the
    last binder), else after the last ``:``. Conservative: returns ``None`` if
    it cannot confidently isolate a goal (caller then does NOT flag).
    """
    head = decl.split(":=", 1)[0]
    # The goal begins after the binder list. If there are binders, it follows
    # the last ')'; otherwise it follows the FIRST ':' after the theorem name.
    last_paren = head.rfind(")")
    tail = head[last_paren + 1:] if last_paren != -1 else head
    colon = tail.find(":")
    if colon == -1:
        return None
    goal = tail[colon + 1:].strip()
    return goal or None


def linear_rearrangement_warnings(source: str) -> list[str]:
    """Heuristic WARN-only detector for LINEAR-REARRANGEMENT proof-theater.

    Flags a theorem whose goal is a single linear (in)equality closed by a
    linear-equality solver (`linarith`/`omega`/`linear_combination`) where a
    SINGLE relational hypothesis is the goal merely TRANSPOSED — same atom set
    on both sides — so the solver derives NO new consequence (the live d6 `chk`
    catch: `hpart : rpart*g = rMR - rMS*rSR` ⊢ `rMR = rpart*g + rMS*rSR`). PURE
    — needs no Lean install. A HINT for the REVIEW M3b grader, NEVER a compile
    failure. This is the SIBLING of :func:`elementary_theorem_warnings` /
    :func:`trivial_pattern_warnings`.

    Conservative by construction — flags ONLY when ALL hold (anything ambiguous
    stays clean, so a real proof can never be false-flagged):

    1. A linear-equality solver appears and NO heavier marker
       (:data:`_REARRANGEMENT_DISQUALIFIERS` — `nlinarith`/`decide`/
       `native_decide`/`induction`/`calc`/`positivity`/`gcongr`). `nlinarith`
       combining ≥2 hyps or deriving a quadratic bound (Cauchy–Schwarz) bails.
    2. The goal is a SINGLE linear relation (not a `∧`/`∃`/`∀`/`→` compound).
    3. EXACTLY ONE relational hypothesis exists, it is an EQUALITY, and its atom
       set EQUALS the goal's atom set. Two-or-more hyps (the solver COMBINES) or
       a goal whose atoms strictly contain the hyp's (a bound over FREE vars,
       the legit `omega` shape) do NOT match.
    """
    src = source or ""
    # (1) A linear-equality solver must be present, and no heavier machinery.
    if not _LINEAR_SOLVER_RE.search(src):
        return []
    if _REARRANGEMENT_DISQUALIFIER_RE.search(src):
        return []
    # Examine each theorem/lemma declaration independently. A decl runs from a
    # `theorem`/`lemma` keyword to the next one (best-effort split). We only flag
    # the SINGLE-theorem transposition shape; multi-theorem sources with mixed
    # content are handled per-decl.
    decls = re.split(r"(?m)^(?=\s*(?:@\[[^\]]*\]\s*)?(?:private\s+|protected\s+|"
                     r"noncomputable\s+)?(?:theorem|lemma)\b)", src)
    out: list[str] = []
    for decl in decls:
        if not re.search(r"(?:theorem|lemma)\b", decl):
            continue
        name_m = _DECL_RE.search(decl)
        if name_m is None:
            continue
        name = name_m.group(1)
        goal = _goal_after_binders(decl)
        if goal is None:
            continue
        # (2) Single linear relation only — a compound goal is left to review.
        if _COMPOUND_GOAL_RE.search(goal):
            continue
        gm = _GOAL_RELATION_RE.match(goal)
        if gm is None:
            continue
        goal_rel = gm.group(2)
        goal_atoms = _proof_atoms(goal)
        if not goal_atoms:
            continue
        # (3) Exactly one relational hypothesis, an equality, atom-set == goal.
        hyps = _NAMED_REL_HYP_RE.findall(decl.split(":=", 1)[0])
        # Drop any binder whose captured "prop" is actually the GOAL itself
        # (the final `: goal` can be picked up if it sits inside parens — rare).
        hyps = [(hn, hp) for (hn, hp) in hyps if hp.strip() != goal.strip()]
        if len(hyps) != 1:
            # 0 hyps → nothing to transpose; ≥2 → the solver COMBINES facts.
            continue
        hyp_name, hyp_prop = hyps[0]
        # The cited hypothesis must itself be an EQUALITY (transposition only
        # makes sense across `=`; a `≤` hyp feeding a `≤` goal is a real bound).
        if "=" not in hyp_prop or any(r in hyp_prop for r in ("≤", "≥", "<", ">")):
            continue
        # The GOAL, likewise, is the transposition target only when it is an
        # equality (or an order relation that is an equality in disguise is left
        # clean — we do NOT over-reach; require `=` for a confident flag).
        if goal_rel != "=":
            continue
        hyp_atoms = _proof_atoms(hyp_prop)
        if hyp_atoms != goal_atoms:
            # goal atoms ⊋ hyp atoms ⇒ derives over free vars (legit);
            # disjoint/partial ⇒ not a transposition. Either way, don't flag.
            continue
        solvers = sorted(t for t in _LINEAR_SOLVER_RE.findall(decl))
        solver_label = ", ".join(dict.fromkeys(solvers)) or "linarith"
        out.append(
            "M3b LINEAR-REARRANGEMENT hint (WARN-only, heuristic): theorem "
            f"`{name}` is a single linear EQUALITY closed by a linear-equality "
            f"solver ({solver_label}), and its only relational hypothesis "
            f"`{hyp_name}` has the SAME atom set as the goal — i.e. the goal is "
            f"hypothesis `{hyp_name}` rearranged. `{solver_label}` performs only "
            "TRANSPOSITION across the equals sign; it COMBINES no independent "
            "premises and derives no new consequence. This is the SHADOW of "
            "LINEAR-REARRANGEMENT proof-theater (rules/method_depth.md M3b), the "
            "SIBLING of the elementary/assume-then-monotonicity patterns: an "
            "'identity proved in Lean' that only transposes terms. It is NOT a "
            "failure. Provide a theorem that DERIVES a bound/consequence from "
            "INDEPENDENT premises (≥2 hypotheses, or a strict inequality the "
            "solver actually establishes), or honestly demote the claim to a "
            "'formalized observation' rather than a proven identity. The analysis "
            "REVIEW judges substance (rules/method_depth.md M3b)."
        )
    return out


# --- LIBRARY-TRIVIA heuristic (WARN-only; the math-LIBRARY substance gate) --
#
# The three layers above serve the run-level M3b gate (EXECUTE/CROSS_CHECK), where `decide`/
# `native_decide`/`trivial` are DELIBERATELY treated as legitimate (they are
# the d5/d7 constructed-witness-check shape — the contribution IS the
# constructed object). But the math-LIBRARY (lab_theorems) has the OPPOSITE
# concern: a virtual-mathematician / auto-curriculum loop that contributes on
# `compiled` alone will happily fill the store with `theorem t : True := by
# trivial`, `theorem t : 1 = 1 := by decide`, or a `native_decide` tautology —
# proofs that COMPILE sorry-free yet carry no mathematical content (the exact
# proof-theater the M3b gates exist for). The `substance_warnings`
# layers do NOT catch these (by design), so the library needs its OWN check.
#
# THE STRUCTURAL LINE (content-free tautology closer vs a real short proof).
# A trivia proof's ENTIRE body is one content-free closer over no premise and
# no NAMED external lemma; a legitimate short proof either cites a NAMED
# Mathlib lemma (the lemma IS the content — `sqrt_two_irrational :=
# irrational_sqrt_two`, `add_comm_two := Nat.add_comm a b`,
# `bernoulli := one_add_mul_le_pow ..`) or runs a real derivation tactic
# (`nlinarith`/`induction`/`calc`/`linarith`/`omega`/…). We flag ONLY when:
#   * every closing tactic the proof uses is a CONTENT-FREE closer
#     (:data:`_TRIVIA_CLOSERS`); AND
#   * the source references NO named external lemma (a dotted `Foo.bar`
#     identifier, or a known content lemma); AND
#   * the theorem binds NO hypothesis it could be discharging.
# Conservative by construction: the moment a named lemma, a derivation tactic,
# or a real binder appears, we return [] (a legit one-line wrapper is NEVER
# flagged). This mirrors the WARN-only contract of the layers above — the
# library records the warning and demotes the entry to `verified_trivial`
# rather than hard-rejecting an honest short proof.

# Content-free closing tactics: each discharges a goal by reflection /
# decidability / definitional triviality, supplying no mathematical content
# beyond "this is decidable / true by reflexivity". A proof made ENTIRELY of
# these (with no named-lemma derivation) is a tautology, not a theorem.
_TRIVIA_CLOSERS: frozenset[str] = frozenset({
    "trivial", "decide", "native_decide", "tauto", "rfl", "simp", "simp_all",
})
# Derivation tactics whose presence means the proof does real work — any one
# of them disables the trivia flag (mirrors _DEEP_PROOF_MARKERS, but here even
# `decide`/`native_decide` are trivia closers, so this set is the genuinely
# content-bearing tactics only).
_NON_TRIVIA_TACTICS: frozenset[str] = frozenset({
    "nlinarith", "linarith", "omega", "induction", "calc", "positivity",
    "gcongr", "linear_combination", "ring", "ring_nf", "norm_num", "field_simp",
    "apply", "refine", "exact", "rw", "rewrite", "cases", "rcases", "obtain",
    "constructor", "use", "have", "intro", "intros",
})
# A dotted identifier (`Nat.add_comm`, `Int.gcd_eq_gcd_ab`) is a NAMED library
# lemma application — content. A leading-uppercase namespaced name is the
# Mathlib convention; a lowercase `foo.bar` term projection is rarer but also
# counted as a reference (conservative: err toward NOT flagging).
_NAMED_LEMMA_RE = re.compile(r"(?<![A-Za-z0-9_'])[A-Za-z_][A-Za-z0-9_']*\.[A-Za-z_][A-Za-z0-9_'.]*")
# A relational-hypothesis binder (the "assume the finding" shape) — if the
# theorem binds one, a content-free closer is doing MORE than a tautology
# (it is discharging a premise), so we leave it to the M3b layers.
_TRIVIA_HYP_BINDER_RE = _HYP_BINDER_RE


def library_trivia_warnings(source: str) -> list[str]:
    """Heuristic WARN-only detector for math-LIBRARY trivia (tautology proofs).

    Flags a theorem whose proof is a content-free tautology closer
    (`trivial`/`decide`/`native_decide`/`tauto`/`rfl`/`simp`) with NO named
    external lemma application and NO hypothesis to discharge — a proof that
    COMPILES sorry-free yet establishes nothing a competent reader would not
    see by inspection. PURE — needs no Lean install. Used by the lab_theorems
    contribution path to demote such an entry to ``verified_trivial`` (a
    recorded warning, NOT a hard reject), so the auto-curriculum loop cannot
    silently fill the library with `compiled`-but-trivial table facts.

    Conservative by design: returns ``[]`` (no warning) the moment the source
    references a NAMED lemma (dotted `Foo.bar`), uses a real derivation tactic
    (:data:`_NON_TRIVIA_TACTICS`), or binds a relational hypothesis — so a
    legitimate one-line Mathlib wrapper (`:= irrational_sqrt_two`,
    `:= Nat.add_comm a b`) is NEVER flagged.
    """
    src = source or ""
    if not src.strip():
        return []
    # A named external lemma application is content — never trivia.
    if _NAMED_LEMMA_RE.search(src):
        return []
    # A relational-hypothesis binder → discharging a premise, not a tautology;
    # leave it to the M3b layers (trivial_pattern_warnings).
    if _TRIVIA_HYP_BINDER_RE.search(src):
        return []
    used = set(_TACTIC_TOKEN_RE.findall(src))
    # Any genuine derivation tactic disables the flag.
    if used & _NON_TRIVIA_TACTICS:
        return []
    closers = sorted(used & _TRIVIA_CLOSERS)
    if not closers:
        # Not a recognised tautology-closer shape (e.g. a bare term-mode proof
        # `:= ⟨…⟩` or an unrecognised tactic) — leave to the reviewer / M3b
        # layers rather than over-reach.
        return []
    return [
        "LIBRARY-trivia hint (WARN-only, heuristic): every closer in this "
        f"proof is a content-free tautology tactic ({', '.join(closers)}) with "
        "NO named-lemma derivation and NO hypothesis to discharge — it "
        "COMPILES sorry-free but establishes nothing beyond decidability / "
        "reflexivity (e.g. `theorem t : True := by trivial`, "
        "`theorem t : 1 = 1 := by decide`). This is the math-LIBRARY shadow of "
        "proof-theater: a `compiled` table fact, not a contributed theorem. "
        "It is recorded as `verified_trivial` (a flag, NOT a rejection): a "
        "genuine short proof cites a NAMED Mathlib lemma or runs a real "
        "derivation tactic; this one does neither. Provide a proof that "
        "derives a non-obvious consequence, or keep it flagged."
    ]


def substance_warnings(source: str) -> list[str]:
    """All WARN-only proof-substance hints for a Lean source (M3b).

    The single entry point the verifier uses to populate
    :attr:`LeanVerdict.substance_warnings`. Runs ALL heuristic layers and
    concatenates their (usually empty) warnings:

    1. :func:`trivial_pattern_warnings` — the assume-the-finding +
       arithmetic-monotonicity shadow (the original M3b trivial pattern).
    2. :func:`elementary_theorem_warnings` — the DEPTH layer: a closed/constant
       fact proved by an algebraic-identity tactic (the elementary
       proof-theater the XOR-table headline slipped through).
    3. :func:`linear_rearrangement_warnings` — the SIBLING layer: a single
       linear equality closed by `linarith`/`omega`/`linear_combination` where a
       lone hypothesis is the goal merely TRANSPOSED (same atom set), deriving
       nothing (the live d6 `chk` catch the elementary layer can't see because
       `linarith`/`omega` are deep markers that disable it).

    All are advisory hints for the REVIEW M3b grader, never compile failures.
    PURE — needs no Lean install.
    """
    return (
        trivial_pattern_warnings(source)
        + elementary_theorem_warnings(source)
        + linear_rearrangement_warnings(source)
    )


def parse_lean_output(
    *, returncode: int, stdout: str, stderr: str, source: str = ""
) -> tuple[bool, list[str], list[str]]:
    """Decide compiled? + collect errors + theorem names from raw output.

    PURE function — the entire verdict LOGIC lives here so it can be tested
    on captured Lean output fixtures without a Lean install. Returns
    ``(compiled, errors, theorems_checked)``.

    A source compiles iff: Lean exited 0 AND there are no ``error:``
    diagnostics AND there is no ``sorry`` (an unproven hole — Lean only
    warns, but for our purposes an unfinished proof is NOT established).
    """
    combined = f"{stdout}\n{stderr}"
    errors: list[str] = []
    for m in _ERROR_LINE_RE.finditer(combined):
        errors.append(m.group(1).strip())
    for m in _BARE_ERROR_RE.finditer(combined):
        msg = m.group(1).strip()
        if msg and msg not in errors:
            errors.append(msg)
    # Authoritative signal = the compiler diagnostic on raw Lean OUTPUT
    # (`declaration uses 'sorry'`). The SOURCE-side fallback is comment-aware
    # (source_uses_sorry strips Lean comments) so the word "sorry" in a
    # docstring/comment never fabricates a failure on a clean proof.
    sorry_present = bool(_SORRY_RE.search(combined)) or source_uses_sorry(source or "")
    if sorry_present:
        errors.append(
            "proof uses 'sorry' (unproven hole — not an established result)"
        )
    compiled = (returncode == 0) and not errors
    return compiled, errors, parse_theorem_names(source)


# ---------------------------------------------------------------------------
# Subprocess runner (watchdog'd — mirrors claude_runner._run_with_watchdogs)
# ---------------------------------------------------------------------------

def _run_lean(
    cmd: list[str], *, cwd: str, timeout_s: int
) -> tuple[int, str, bool]:
    """Run a Lean/Lake command under a wall-timeout watchdog.

    Returns ``(returncode, combined_output, timed_out)``. Starts the
    process in its own session so a hung ``lean`` (and its children) can be
    killed as a group — the same SIGTERM→SIGKILL discipline
    ``claude_runner`` uses for ``claude -p``. The child env carries a bounded
    ``LEAN_NUM_THREADS`` (:func:`_lean_child_env`) so a heavy CPU-class proof
    stays a polite neighbour on a shared host.
    """
    try:
        proc = subprocess.Popen(
            cmd, cwd=cwd, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, start_new_session=True, text=True,
            env=_lean_child_env(),
        )
    except (FileNotFoundError, OSError) as exc:
        return 127, f"failed to launch {cmd[0]!r}: {exc}", False

    def _kill_tree() -> None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass

    try:
        out, _ = proc.communicate(timeout=timeout_s)
        return proc.returncode, out or "", False
    except subprocess.TimeoutExpired:
        _kill_tree()
        try:
            out, _ = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            out = ""
        return -1, out or "", True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def verify_lean_file(
    lean_file: str | pathlib.Path, *, timeout_s: int | None = None,
    compute_class_requested: str | None = None,
) -> LeanVerdict:
    """Check a ``.lean`` file with the local Lean toolchain.

    Picks ``lake env lean <file>`` when the file is inside a Lake project
    (so project deps resolve), else bare ``lean <file>``. Returns a
    :class:`LeanVerdict`. When no Lean toolchain is found, returns a
    verdict with ``tool_missing=True`` and ``compiled=False`` (never
    raises for a missing toolchain — the caller decides how to surface
    "Lean not installed").

    ``compute_class_requested`` is the FORWARD HOOK (default ``None`` → ``cpu``
    via :func:`compute_class`, i.e. exactly today's local-CPU behaviour). A
    future Lean step that needs special compute may pass
    :data:`constants.COMPUTE_CLASS_GPU`; the resolved class is recorded on the
    verdict (``compute_class``). Today no GPU-Lean backend exists, so a ``gpu``
    request still runs on the local toolchain but is flagged in the verdict +
    a note appended to ``errors`` is NOT added (it still compiles normally) —
    the class field simply makes the intent auditable for the router that will
    one day read it. The CPU thread cap (``LEAN_NUM_THREADS``) is applied in
    :func:`_run_lean` regardless.
    """
    klass = compute_class(compute_class_requested)
    path = pathlib.Path(lean_file).resolve()
    source = ""
    try:
        source = path.read_text()
    except OSError:
        pass

    if timeout_s is None:
        timeout_s = _wall_timeout_s()

    substance = substance_warnings(source)

    lean = _resolve_bin(LEAN_BIN)
    if lean is None:
        return LeanVerdict(
            compiled=False, tool_missing=True, lean_file=str(path),
            errors=["lean toolchain not found (install via elan; see "
                    "rules/method_depth.md)"],
            theorems_checked=parse_theorem_names(source),
            substance_warnings=substance, compute_class=klass,
        )

    # The file path is passed AFTER a ``--`` end-of-options sentinel and is
    # always an absolute (resolved) path, so a path whose basename begins with
    # ``-`` can never be mis-parsed as a ``lean``/``lake`` flag.
    file_arg = _safe_path_arg(path)
    project_root = _find_lake_project(path)
    if project_root is not None:
        lake = _resolve_bin(LAKE_BIN)
        if lake is not None:
            cmd = [lake, "env", LEAN_BIN, "--", file_arg]
            cwd = str(project_root)
        else:
            cmd = [lean, "--", file_arg]
            cwd = str(path.parent)
    else:
        cmd = [lean, "--", file_arg]
        cwd = str(path.parent)

    start = time.time()
    rc, out, timed_out = _run_lean(cmd, cwd=cwd, timeout_s=timeout_s)
    duration = time.time() - start

    if timed_out:
        return LeanVerdict(
            compiled=False, timed_out=True, returncode=rc,
            duration_s=duration, lean_file=str(path), stdout=out,
            errors=[f"lean timed out after {timeout_s}s"],
            theorems_checked=parse_theorem_names(source),
            substance_warnings=substance, compute_class=klass,
        )

    compiled, errors, theorems = parse_lean_output(
        returncode=rc, stdout=out, stderr="", source=source
    )
    return LeanVerdict(
        compiled=compiled, theorems_checked=theorems, errors=errors,
        returncode=rc, duration_s=duration, lean_file=str(path), stdout=out,
        substance_warnings=substance, compute_class=klass,
    )


def verify_lean_source(
    source: str, *, workdir: str | pathlib.Path | None = None,
    name: str = "Claim", timeout_s: int | None = None,
    compute_class_requested: str | None = None,
) -> LeanVerdict:
    """Check a Lean statement+proof STRING by writing it to a temp ``.lean``
    file and delegating to :func:`verify_lean_file`.

    ``workdir`` lets the caller place the file inside a Lake project (so
    project deps resolve). When omitted, a private temp dir is used —
    suitable for Mathlib-free lemmas. The temp file is left in place when
    ``workdir`` is given (it becomes the auditable artifact); a private
    temp dir is cleaned up. ``compute_class_requested`` is forwarded to
    :func:`verify_lean_file` (default ``None`` → ``cpu``; the forward hook).
    """
    import tempfile

    if workdir is not None:
        wd = pathlib.Path(workdir)
        wd.mkdir(parents=True, exist_ok=True)
        lean_path = wd / f"{name}.lean"
        lean_path.write_text(source)
        return verify_lean_file(
            lean_path, timeout_s=timeout_s,
            compute_class_requested=compute_class_requested,
        )

    with tempfile.TemporaryDirectory(prefix="omicairn_lean_") as td:
        lean_path = pathlib.Path(td) / f"{name}.lean"
        lean_path.write_text(source)
        return verify_lean_file(
            lean_path, timeout_s=timeout_s,
            compute_class_requested=compute_class_requested,
        )
