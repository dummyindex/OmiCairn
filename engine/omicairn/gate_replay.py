"""Cross-corpus GATE-SUITE replay (read-only).

The standing OmiCairn audit loop repeatedly needs to replay the cross_check
GATE SUITE across ALL finalized workspaces at once — e.g. when a NEW gate
merges (W16/W17/W18 + the Lean substance layers), you want to
retroactively sweep the whole corpus to (a) regression-test the gate on
real data and (b) surface any latent defect it now catches. This module is
the first-class, deterministic capability that replaces the throwaway
Python that did this by hand.

It backs `omicairn audit --replay-gates`. It does NOT re-implement any gate
logic: it REUSES the canonical, importable gate functions from
`omicairn.cross_check` and `omicairn.lean_verify`, plus their existing artifact
loaders (`_find_latest_plan_sibling`, `_read_preregistration_field`,
`_load_latest_results_json`, `_w18_read_lean_verdicts`). Every gate is
described once in a single canonical descriptor registry (`_GATES`) — no
string-literal soup, no parallel inline literals.

STRICTLY READ-ONLY: it loads artifacts and runs pure gate functions; it
NEVER writes into a workspace, never mutates any artifact, never commits.
Output goes to stdout (table) or as a JSON document (`--json`).
"""
from __future__ import annotations

import dataclasses
import json
import pathlib
from typing import Callable

from . import cross_check, lean_verify, stage as stage_mod
from .constants import (
    ConstructValidityGate,
    DecisiveArmPowerGate,
    EffectClearsMdeGate,
    PhaseName,
    PreregCompletenessGate,
    RESULTS_JSON,
)


# ---------------------------------------------------------------------------
# Per-workspace resolved context
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class _WorkspaceContext:
    """The artifacts a gate may need, resolved once per workspace.

    A field is None when the corresponding artifact/stage is absent — each
    gate descriptor declares which fields it requires and is reported "n/a"
    (never crashed) when a required one is missing. Resolution mirrors the
    canonical cross_check loaders so the replay sees exactly what the live
    pipeline gate would.
    """
    root: pathlib.Path
    # Highest-indexed execute stage that carries a parseable results.json,
    # plus the parsed dict (the same pair `_load_latest_results_json`
    # resolves, but keeping the stage dir so plan-sibling lookups work).
    execute_dir: pathlib.Path | None
    results: dict | None
    # The SHIPPED Lean proofs: every *.lean in the FRESHEST stage that
    # carries a non-vendored `lean/` dir (stale proof-theater left behind
    # in an earlier re-deepened stage is NOT scanned — see
    # _resolve_lean_files for the freshest-stage rationale).
    lean_files: tuple[pathlib.Path, ...]


def _resolve_latest_execute_with_results(
    root: pathlib.Path,
) -> tuple[pathlib.Path | None, dict | None]:
    """Highest-indexed execute stage holding a parseable results.json.

    Mirrors `cross_check._load_latest_results_json` but also returns the
    stage DIR (the gates need it to find the plan sibling). Returns
    (None, None) when no execute stage has a readable results.json.
    """
    if not root.is_dir():
        return None, None
    best: tuple[int, pathlib.Path] | None = None
    for child in root.iterdir():
        if not child.is_dir():
            continue
        parsed = cross_check.parse_stage_dir_name(child.name)
        if parsed is None:
            continue
        idx, phase = parsed
        if phase != PhaseName.EXECUTE:
            continue
        if (child / RESULTS_JSON).exists() and (best is None or idx > best[0]):
            best = (idx, child)
    if best is None:
        return None, None
    try:
        results = json.loads((best[1] / RESULTS_JSON).read_text())
    except (OSError, json.JSONDecodeError):
        return best[1], None
    if not isinstance(results, dict):
        return best[1], None
    return best[1], results


# Path components that mark a VENDORED Lean source (a Lake/Mathlib
# dependency checkout), not one of the workspace's OWN proofs. Replaying
# the substance heuristic over thousands of dependency .lean files is pure
# noise — the canonical W18 reader only ever looks at `<stage>/lean/*.lean`
# (the OmiCairn math-formalization skill's output dir), so we mirror that scope.
_VENDORED_LEAN_MARKERS: frozenset[str] = frozenset({".lake", "lake-packages"})


def _resolve_lean_files(root: pathlib.Path) -> tuple[pathlib.Path, ...]:
    """The SHIPPED Lean proofs: the FRESHEST `<NNN_stage>/lean/*.lean`.

    Scans ONLY the single highest-indexed stage (by the parsed stage index
    from `parse_stage_dir_name`) whose `lean/` subdir actually holds ≥1
    non-vendored `*.lean` source — NOT every stage. This mirrors how
    `_resolve_latest_execute_with_results` (and cross_check/W18 at runtime)
    judge the SHIPPED artifact by stage recency.

    Why freshest-only: a run that RE-DEEPENS a proof leaves the stale
    proof-theater behind in an EARLIER stage (e.g. d7 has the old T1
    monotonicity tautology in `036_execute/lean/` and the substantive
    Cauchy-Schwarz bound in `042_execute/lean/`; fair-erasure has an early
    XOR-table theater and a later substantive nlinarith). Replaying the
    substance heuristic over ALL stages would re-flag a proof that was
    already REPLACED — a false positive against work the run already fixed.
    The gate suite must judge the shipped proof = the freshest stage's,
    exactly as cross_check/W18 does at runtime (by stage recency).

    Mirrors W18's `_W18_LEAN_SUBDIR` convention — the dir the OmiCairn
    math-formalization skill writes proofs to — and scopes to that subdir
    rather than an unscoped rglob, which would sweep in vendored
    Lake/Mathlib dependency sources (`.lake/packages/...`) and drown real
    signal. Vendored paths are excluded defensively even if they appear
    under a `lean/` dir.
    """
    if not root.is_dir():
        return ()
    best: tuple[int, tuple[pathlib.Path, ...]] | None = None
    for child in root.iterdir():
        if not child.is_dir():
            continue
        parsed = cross_check.parse_stage_dir_name(child.name)
        if parsed is None:
            continue
        idx, _phase = parsed
        lean_dir = child / cross_check._W18_LEAN_SUBDIR
        if not lean_dir.is_dir():
            continue
        stage_files = tuple(
            lf for lf in sorted(lean_dir.rglob("*.lean"))
            if not (_VENDORED_LEAN_MARKERS & set(lf.parts))
        )
        if not stage_files:
            continue
        if best is None or idx > best[0]:
            best = (idx, stage_files)
    return best[1] if best is not None else ()


def _resolve_context(root: pathlib.Path) -> _WorkspaceContext:
    execute_dir, results = _resolve_latest_execute_with_results(root)
    lean_files = _resolve_lean_files(root)
    return _WorkspaceContext(
        root=root,
        execute_dir=execute_dir,
        results=results,
        lean_files=lean_files,
    )


# ---------------------------------------------------------------------------
# Per-gate result + the canonical descriptor registry
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class GateResult:
    """Outcome of replaying ONE gate against ONE workspace."""
    gate: str
    status: str  # "pass" | "warn" | "error" | "n/a"
    warnings: list[str] = dataclasses.field(default_factory=list)
    errors: list[str] = dataclasses.field(default_factory=list)
    na_reason: str = ""

    @property
    def finding_count(self) -> int:
        return len(self.warnings) + len(self.errors)

    def cell(self) -> str:
        """One-cell rendering for the table (PASS / WARN(n) / ERR(n) / n/a)."""
        if self.status == "n/a":
            return "n/a"
        if self.status == "pass":
            return "PASS"
        if self.status == "error":
            return f"ERR({len(self.errors)})"
        return f"WARN({len(self.warnings)})"


# A gate runner takes the resolved context and returns a GateResult. It must
# return a "n/a" GateResult (never raise) when a required artifact is absent.
_GateRunner = Callable[[_WorkspaceContext], GateResult]


def _na(gate: str, reason: str) -> GateResult:
    return GateResult(gate=gate, status="n/a", na_reason=reason)


def _classify(gate: str, errors: list[str], warnings: list[str]) -> GateResult:
    """Fold a gate's (errors, warnings) lists into a GateResult."""
    if errors:
        return GateResult(gate=gate, status="error", errors=errors, warnings=warnings)
    if warnings:
        return GateResult(gate=gate, status="warn", warnings=warnings)
    return GateResult(gate=gate, status="pass")


def _run_lean_substance(ctx: _WorkspaceContext) -> GateResult:
    """Lean proof-substance layer: replay `lean_verify.substance_warnings`
    over every *.lean source in the workspace (trivial / elementary /
    linear-rearrangement layers). WARN-only by construction."""
    gate = "lean_substance"
    if not ctx.lean_files:
        return _na(gate, "no *.lean files in workspace")
    warnings: list[str] = []
    for path in ctx.lean_files:
        try:
            src = path.read_text(errors="replace")
        except OSError:
            continue
        for w in lean_verify.substance_warnings(src):
            rel = path.relative_to(ctx.root)
            warnings.append(f"{rel}: {w}")
    return _classify(gate, [], warnings)


def _run_w13(ctx: _WorkspaceContext) -> GateResult:
    gate = "W13"
    if ctx.execute_dir is None or ctx.results is None:
        return _na(gate, "no execute stage with results.json")
    errors: list[str] = []
    cross_check._check_w13_load_bearing_arm_complete(
        ctx.execute_dir, ctx.results, errors)
    return _classify(gate, errors, [])


def _run_w14(ctx: _WorkspaceContext) -> GateResult:
    gate = "W14"
    if ctx.execute_dir is None or ctx.results is None:
        return _na(gate, "no execute stage with results.json")
    warnings: list[str] = []
    cross_check._check_w14_shared_protocol_baseline(
        ctx.execute_dir, ctx.results, warnings)
    return _classify(gate, [], warnings)


def _run_w15(ctx: _WorkspaceContext) -> GateResult:
    gate = "W15"
    if ctx.execute_dir is None or ctx.results is None:
        return _na(gate, "no execute stage with results.json")
    errors: list[str] = []
    cross_check._check_w15_load_bearing_arm_provenance(
        ctx.execute_dir, ctx.results, errors)
    return _classify(gate, errors, [])


def _run_w16(ctx: _WorkspaceContext) -> GateResult:
    gate = "W16"
    if ctx.execute_dir is None or ctx.results is None:
        return _na(gate, "no execute stage with results.json")
    errors: list[str] = []
    warnings: list[str] = []
    cross_check._check_w16_degenerate_metric_headline(
        ctx.execute_dir, ctx.results, errors, warnings)
    return _classify(gate, errors, warnings)


def _run_w17(ctx: _WorkspaceContext) -> GateResult:
    gate = "W17"
    if ctx.execute_dir is None or ctx.results is None:
        return _na(gate, "no execute stage with results.json")
    errors: list[str] = []
    warnings: list[str] = []
    cross_check._check_w17_discriminating_arm_resolution(
        ctx.execute_dir, ctx.results, errors, warnings)
    return _classify(gate, errors, warnings)


def _run_w18(ctx: _WorkspaceContext) -> GateResult:
    gate = "W18"
    if ctx.execute_dir is None:
        return _na(gate, "no execute stage")
    errors: list[str] = []
    warnings: list[str] = []
    cross_check._check_w18_proof_substance_enforced(
        ctx.execute_dir, errors, warnings)
    return _classify(gate, errors, warnings)


def _run_w20(ctx: _WorkspaceContext) -> GateResult:
    gate = "W20"
    if ctx.execute_dir is None or ctx.results is None:
        return _na(gate, "no execute stage with results.json")
    errors: list[str] = []
    warnings: list[str] = []
    cross_check._check_w20_decision_rule_honored(
        ctx.execute_dir, ctx.results, errors, warnings)
    return _classify(gate, errors, warnings)


def _run_w21(ctx: _WorkspaceContext) -> GateResult:
    # W21 (prereg-completeness) reads the prereg off the plan sibling of the
    # execute stage, so it needs only the execute dir (NOT results.json). It
    # is confirmatory-gated by preregistration.json presence — the SAME
    # signal W13/W17/W20 key on — so it is n/a when there is no execute
    # stage to anchor the plan-sibling lookup, and a silent no-op (PASS) for
    # exploratory runs that emitted no preregistration.json. WARN-only.
    gate = PreregCompletenessGate.GATE_ID
    if ctx.execute_dir is None:
        return _na(gate, "no execute stage (W21 needs the plan sibling)")
    warnings: list[str] = []
    cross_check._check_w21_prereg_completeness(ctx.execute_dir, warnings)
    return _classify(gate, [], warnings)


def _run_w23(ctx: _WorkspaceContext) -> GateResult:
    # W23 (decisive-arm zero-power) reads the named discriminating_arm off the
    # plan sibling and inspects its power signature in results.json. It needs an
    # execute stage with results.json (the power numbers live there) and is a
    # silent no-op for runs that declare no discriminating_arm or whose arm
    # genuinely had power. WARN-only.
    gate = DecisiveArmPowerGate.GATE_ID
    if ctx.execute_dir is None or ctx.results is None:
        return _na(gate, "no execute stage with results.json")
    warnings: list[str] = []
    cross_check._check_w23_decisive_arm_power(ctx.execute_dir, ctx.results, warnings)
    return _classify(gate, [], warnings)


def _run_w24(ctx: _WorkspaceContext) -> GateResult:
    # W24 (construct-validity) reads the OPTIONAL structured `construct_validity`
    # declaration off the plan sibling and checks the named validity metric in
    # results.json against its threshold (point and, for a correlation,
    # CI-spans-null). Needs an execute stage with results.json (the validity
    # stat lives there); silent no-op for runs declaring no construct_validity
    # object, a not-load-bearing axis, or a passing validation. WARN-only.
    gate = ConstructValidityGate.GATE_ID
    if ctx.execute_dir is None or ctx.results is None:
        return _na(gate, "no execute stage with results.json")
    warnings: list[str] = []
    cross_check._check_w24_construct_validity(ctx.execute_dir, ctx.results, warnings)
    return _classify(gate, [], warnings)


def _run_w25(ctx: _WorkspaceContext) -> GateResult:
    # W25 (effect-clears-own-MDE) reads the declared `mde` off the plan sibling
    # and compares the load-bearing / primary-contrast effect in results.json
    # against it. Needs an execute stage with results.json (the effect lives
    # there); silent no-op for runs declaring no numeric MDE or whose effect
    # clears it. WARN-only.
    gate = EffectClearsMdeGate.GATE_ID
    if ctx.execute_dir is None or ctx.results is None:
        return _na(gate, "no execute stage with results.json")
    warnings: list[str] = []
    cross_check._check_w25_effect_clears_mde(ctx.execute_dir, ctx.results, warnings)
    return _classify(gate, [], warnings)


@dataclasses.dataclass(frozen=True)
class _GateDescriptor:
    """One canonical (gate-name -> runner) entry. The runner closes over
    which stage dirs the gate consumes and adapts the cross_check signature
    (each gate has a slightly different errors/warnings list shape)."""
    name: str
    run: _GateRunner


# THE one canonical registry — no parallel literals elsewhere. Order is the
# table's column order. lean_substance first (file-level), then the
# results.json-gated W-checks in numeric order, then the plan-gated W21
# (prereg-completeness).
_GATES: tuple[_GateDescriptor, ...] = (
    _GateDescriptor("lean_substance", _run_lean_substance),
    _GateDescriptor("W13", _run_w13),
    _GateDescriptor("W14", _run_w14),
    _GateDescriptor("W15", _run_w15),
    _GateDescriptor("W16", _run_w16),
    _GateDescriptor("W17", _run_w17),
    _GateDescriptor("W18", _run_w18),
    _GateDescriptor("W20", _run_w20),
    _GateDescriptor(PreregCompletenessGate.GATE_ID, _run_w21),
    _GateDescriptor(DecisiveArmPowerGate.GATE_ID, _run_w23),
    _GateDescriptor(ConstructValidityGate.GATE_ID, _run_w24),
    _GateDescriptor(EffectClearsMdeGate.GATE_ID, _run_w25),
)


# ---------------------------------------------------------------------------
# Workspace discovery + the replay driver
# ---------------------------------------------------------------------------

# Top-level dirs under a workspaces root that are NOT runs (never scanned).
_NON_WORKSPACE_DIRS: frozenset[str] = frozenset({
    "lab_knowledge",
    "lab_library",
    "human_feedback",
})


def is_finalized_workspace(root: pathlib.Path) -> bool:
    """True iff `root` looks like a finalized OmiCairn run worth replaying.

    Robust, not pipeline-strict: a workspace is "finalized" if it has a
    done EXECUTE stage carrying a results.json AND a done CROSS_CHECK stage
    after it. Clearly-non-run dirs (lab_knowledge/, lab_library/,
    human_feedback/) and anything without a RESEARCH.md are skipped.
    """
    if not root.is_dir() or root.name in _NON_WORKSPACE_DIRS:
        return False
    if not (root / "RESEARCH.md").exists():
        return False
    exec_ref = stage_mod.latest_stage_of(root, PhaseName.EXECUTE)
    if exec_ref is None or not (exec_ref.path / RESULTS_JSON).exists():
        return False
    cc_ref = stage_mod.latest_stage_of(root, PhaseName.CROSS_CHECK)
    return cc_ref is not None and cc_ref.index > exec_ref.index


def discover_workspaces(workspaces_root: pathlib.Path) -> list[pathlib.Path]:
    """Every finalized workspace under `workspaces_root`, sorted by name."""
    if not workspaces_root.is_dir():
        return []
    return sorted(
        (child for child in workspaces_root.iterdir()
         if is_finalized_workspace(child)),
        key=lambda p: p.name,
    )


@dataclasses.dataclass
class WorkspaceReplay:
    """All gate results for one workspace."""
    workspace: str
    gates: list[GateResult]

    @property
    def total_warnings(self) -> int:
        return sum(len(g.warnings) for g in self.gates)

    @property
    def total_errors(self) -> int:
        return sum(len(g.errors) for g in self.gates)

    @property
    def has_finding(self) -> bool:
        return any(g.status in ("warn", "error") for g in self.gates)


def replay_workspace(root: pathlib.Path) -> WorkspaceReplay:
    """Replay the full gate suite against ONE workspace (read-only)."""
    ctx = _resolve_context(root)
    results: list[GateResult] = []
    for desc in _GATES:
        try:
            results.append(desc.run(ctx))
        except Exception as exc:  # defensive: a gate must never crash the sweep
            results.append(GateResult(
                gate=desc.name, status="n/a",
                na_reason=f"gate raised {type(exc).__name__}: {exc}"))
    return WorkspaceReplay(workspace=root.name, gates=results)


def replay_corpus(roots: list[pathlib.Path]) -> list[WorkspaceReplay]:
    return [replay_workspace(r) for r in roots]


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

GATE_NAMES: tuple[str, ...] = tuple(d.name for d in _GATES)


def render_table(replays: list[WorkspaceReplay]) -> str:
    """A per-workspace | gate | cell table + a summary line."""
    if not replays:
        return "omicairn audit --replay-gates: no finalized workspaces found."
    ws_w = max([len("workspace")] + [len(r.workspace) for r in replays])
    cell_w = max(8, *(len(g) for g in GATE_NAMES))
    lines: list[str] = []
    header = "workspace".ljust(ws_w) + "  " + "  ".join(
        g.ljust(cell_w) for g in GATE_NAMES)
    lines.append(header)
    lines.append("-" * len(header))
    for r in replays:
        by_name = {g.gate: g for g in r.gates}
        cells = [by_name[g].cell().ljust(cell_w) for g in GATE_NAMES]
        lines.append(r.workspace.ljust(ws_w) + "  " + "  ".join(cells))
    n_with_finding = sum(1 for r in replays if r.has_finding)
    total_warn = sum(r.total_warnings for r in replays)
    total_err = sum(r.total_errors for r in replays)
    lines.append("")
    lines.append(
        f"summary: {len(replays)} workspace(s); {total_warn} warning(s), "
        f"{total_err} error(s); {n_with_finding} workspace(s) with any finding."
    )
    return "\n".join(lines)


def to_json(replays: list[WorkspaceReplay]) -> dict:
    """Machine-readable shape (the audit cron parses this)."""
    return {
        "gates": list(GATE_NAMES),
        "workspaces": [
            {
                "workspace": r.workspace,
                "total_warnings": r.total_warnings,
                "total_errors": r.total_errors,
                "gates": [
                    {
                        "gate": g.gate,
                        "status": g.status,
                        "warnings": g.warnings,
                        "errors": g.errors,
                        "na_reason": g.na_reason,
                    }
                    for g in r.gates
                ],
            }
            for r in replays
        ],
        "summary": {
            "workspaces": len(replays),
            "total_warnings": sum(r.total_warnings for r in replays),
            "total_errors": sum(r.total_errors for r in replays),
            "workspaces_with_finding": sum(1 for r in replays if r.has_finding),
        },
    }
