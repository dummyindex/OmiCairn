"""Reaper for ORPHANED runs — mark dead `in_progress` workspaces terminal.

OmiCairn only has DONE and HALTED as terminal ``RunStatus`` values. A run that
dies WITHOUT reaching a verdict — an abandoned ``001_plan`` duplicate, a
superseded re-run, a process killed mid-stage — keeps ``status: in_progress``
in STATE.md forever. With no ``.run.lock`` and no live ``omicairn run`` process it
is *orphaned*: a phantom "active" run that pollutes every ``omicairn status`` /
audit sweep and wastes disk.

This module finds those orphans and (only with ``--apply``) marks them HALTED
with a clear reason, journalling a ``reaped_orphan`` event. It NEVER deletes a
file — deletion is the owner's call. HALTED is fully reversible (the owner can
``omicairn run`` the workspace again; the orchestrator re-acquires the lock).

Safety is the whole point. An orphan is declared iff ALL of:

  1. STATE.md ``status == in_progress``  (done/halted are skipped → idempotent),
  2. ``.run.lock`` is missing OR its holder PID is dead (``lock.inspect``),
  3. NO live ``omicairn run`` OS process references this workspace's directory name,
     AND
  4. it has executed AT LEAST ONE stage. A freshly-``init``ed case is
     ``in_progress`` with no lock/process but has run ZERO stages — it is
     PENDING its first dispatch, not orphaned (no work to lose). See
     ``_never_run``.

(3) is load-bearing: a healthy run can hold no lock for brief windows, so the
absence of a lock alone is NOT proof of death — the OS-process check is. We
match on the FULL workspace basename (``<slug>-<ts>``), never the slug prefix,
because two runs of the same case share a slug prefix (e.g. a dead
``...-005257`` and a LIVE ``...-065944``) and a prefix match would confuse them.

If we CANNOT reliably enumerate processes (no ``/proc``, ``ps`` unavailable /
errors), we FAIL SAFE: every ``in_progress`` workspace is treated as ALIVE and
nothing is reaped. We never reap on uncertainty.
"""
from __future__ import annotations

import dataclasses
import os
import pathlib
import re
import subprocess
from typing import Iterable

from . import lock as lock_mod
from . import stage as stage_mod
from . import state as state_mod
from .constants import EventKind, RunStatus


RUN_LOCK_NAME = ".run.lock"
# Back-compat alias: the canonical name now lives in constants.EventKind.
# Kept so existing imports (`reap.REAP_EVENT_KIND`) keep resolving.
REAP_EVENT_KIND = EventKind.REAPED_ORPHAN

# A workspace dir is recognised by carrying one of these marker files. Used
# both to decide "is <path> a single workspace?" and to skip non-workspace
# children (lab_knowledge/, lab_library/, ...) when scanning a root.
_WORKSPACE_MARKERS = ("STATE.md", "RESEARCH.md")

# Matches the `workspaces/<basename>` (or absolute `.../<basename>`) positional
# argument of a `omicairn run` invocation. We capture the final path component —
# the workspace directory name — and strip a trailing slash.
_WS_PATH_ARG_RE = re.compile(r"(?:^|/)([^/\s]+)/*$")


@dataclasses.dataclass(frozen=True)
class OrphanVerdict:
    """Result of classifying one workspace."""

    workspace: pathlib.Path
    status: str  # STATE.md status as read (in_progress/done/halted/unknown)
    is_orphan: bool
    reason: str  # why it IS an orphan (set when is_orphan) — used as halt reason
    skip_reason: str  # why it is NOT an orphan (set when not is_orphan)


@dataclasses.dataclass
class ReapReport:
    """Outcome of a reap pass over one or more workspaces."""

    scanned: int = 0
    orphans: list[OrphanVerdict] = dataclasses.field(default_factory=list)
    reaped: list[pathlib.Path] = dataclasses.field(default_factory=list)
    applied: bool = False
    process_scan_degraded: bool = False

    @property
    def orphan_count(self) -> int:
        return len(self.orphans)

    @property
    def reaped_count(self) -> int:
        return len(self.reaped)


# ---------------------------------------------------------------------------
# OS-process liveness — the load-bearing false-positive guard
# ---------------------------------------------------------------------------


def _looks_like_omicairn_run(tokens: list[str]) -> bool:
    """True iff this argv looks like a `omicairn run <ws>` invocation.

    Accepts both the console-script form (``.../bin/omicairn run ...``) and the
    module form (``python -m omicairn ... run`` / ``... omicairn.cli ... run``). We
    require BOTH a ``omicairn`` marker AND a standalone ``run`` token so we don't
    match `omicairn status` / `omicairn inspect` / unrelated processes that merely
    mention "omicairn" somewhere.
    """
    joined = " ".join(tokens)
    has_omicairn = any(
        t.endswith("/omicairn") or t == "omicairn" or t.endswith("omicairn.cli") for t in tokens
    ) or ("-m" in tokens and "omicairn" in tokens)
    if not has_omicairn:
        # `omicairn.cli` may appear glued (rare); fall back to a substring check
        # for the module form only, still gated on a `run` token below.
        has_omicairn = "omicairn.cli" in joined or "-m omicairn" in joined
    if not has_omicairn:
        return False
    return "run" in tokens


def _basename_from_tokens(tokens: list[str]) -> str | None:
    """Extract the workspace dir name from a `omicairn run` argv.

    The workspace path is the first positional token AFTER ``run`` that is not
    an option (doesn't start with ``-``). We return only its final path
    component so a relative ``workspaces/foo-123`` and an absolute
    ``/abs/workspaces/foo-123`` both yield ``foo-123``.
    """
    try:
        run_idx = tokens.index("run")
    except ValueError:
        return None
    for tok in tokens[run_idx + 1:]:
        if tok.startswith("-"):
            continue
        m = _WS_PATH_ARG_RE.search(tok)
        if m:
            return m.group(1)
        return None
    return None


def _iter_proc_cmdlines() -> Iterable[list[str]]:
    """Yield argv token-lists for every process via ``/proc`` (Linux).

    Raises if ``/proc`` is not a directory so the caller can fall back to
    ``ps``. Individual unreadable PIDs (races, permissions) are skipped.
    """
    proc = pathlib.Path("/proc")
    if not proc.is_dir():
        raise FileNotFoundError("/proc not available")
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except (OSError, ProcessLookupError):
            continue
        if not raw:
            continue
        tokens = [t for t in raw.decode("utf-8", "replace").split("\0") if t]
        if tokens:
            yield tokens


def _iter_ps_cmdlines() -> Iterable[list[str]]:
    """Yield argv token-lists via ``ps`` (portable fallback).

    ``ps -axww -o args=`` prints the full command line per process, unwrapped
    (``-ww``). We split each line on whitespace — coarse but sufficient: the
    workspace basename never contains whitespace, and we only need the
    ``run`` token + the path argument to align.
    """
    out = subprocess.run(
        ["ps", "-axww", "-o", "args="],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    ).stdout
    for line in out.splitlines():
        tokens = line.split()
        if tokens:
            yield tokens


def scan_live_run_workspaces() -> tuple[set[str], bool]:
    """Return ``(basenames, degraded)``.

    ``basenames`` is the set of workspace directory names that a live
    ``omicairn run`` process is currently operating on. ``degraded`` is True when
    we could NOT reliably enumerate processes (neither ``/proc`` nor ``ps``
    worked); in that case callers MUST fail safe and treat every run as alive.

    Note: this process intentionally includes its own ancestors. The CLI
    invoking ``omicairn reap`` is ``omicairn reap`` not ``omicairn run``, so it is filtered
    out by ``_looks_like_omicairn_run`` (no standalone ``run`` token).
    """
    names: set[str] = set()
    for source in (_iter_proc_cmdlines, _iter_ps_cmdlines):
        try:
            collected = False
            for tokens in source():
                collected = True
                if not _looks_like_omicairn_run(tokens):
                    continue
                base = _basename_from_tokens(tokens)
                if base:
                    names.add(base)
            if collected:
                # This enumeration source worked (we saw at least one process);
                # trust its result and stop. names may legitimately be empty
                # (no omicairn run is alive) — that is NOT degraded.
                return names, False
        except (OSError, ValueError, subprocess.SubprocessError):
            continue
    # Neither source produced ANY process listing → we are blind. Fail safe.
    return names, True


# ---------------------------------------------------------------------------
# Workspace classification
# ---------------------------------------------------------------------------


def is_workspace_dir(path: pathlib.Path) -> bool:
    """True iff ``path`` is a OmiCairn workspace (carries STATE.md/RESEARCH.md)."""
    return path.is_dir() and any((path / m).exists() for m in _WORKSPACE_MARKERS)


def _read_status(ws_root: pathlib.Path) -> str:
    state_path = ws_root / "STATE.md"
    if not state_path.exists():
        return "unknown"
    try:
        s = state_mod.read(state_path)
    except (OSError, ValueError):
        return "unknown"
    return str(s.get("status") or "unknown")


def _never_run(ws_root: pathlib.Path) -> bool:
    """True iff this workspace has executed ZERO stages — a freshly-``init``ed
    case still AWAITING its first ``omicairn ideate``/``omicairn run`` dispatch.

    Such a case has no work to lose and is NOT an orphan, even though its
    STATE.md says ``in_progress`` with no lock/process (``init`` seeds exactly
    that). We require BOTH never-run signals to agree before skipping, so a run
    that DID execute a stage is never mistaken for never-run (the conservative
    choice that keeps real orphan detection intact):

      1. ``stage.list_stages`` is empty — no ``NNN_<phase>/`` dir on disk
         (the canonical filesystem truth; preferred over hand-parsing), AND
      2. STATE.md ``current_stage`` is null/None — the orchestrator has never
         recorded a stage start.
    """
    if stage_mod.list_stages(ws_root):
        return False
    state_path = ws_root / "STATE.md"
    if not state_path.exists():
        return False
    try:
        s = state_mod.read(state_path)
    except (OSError, ValueError):
        return False
    return s.get("current_stage") is None


def _lock_holder_alive(ws_root: pathlib.Path) -> bool:
    """True iff a ``.run.lock`` exists AND its holder PID is alive."""
    lock_path = ws_root / RUN_LOCK_NAME
    if not lock_path.exists():
        return False
    return lock_mod.inspect(lock_path).holder_alive


def _superseded_note(ws_root: pathlib.Path) -> str:
    """If a LATER sibling workspace with the same slug prefix is in a terminal
    state, return a short note naming it. Best-effort, purely informational.

    Slug prefix = the workspace dir name with the trailing ``-<timestamp>``
    stripped. Siblings are the other child dirs of the same parent. "Later" =
    lexicographically greater dir name (timestamps sort chronologically).
    """
    name = ws_root.name
    prefix = _strip_trailing_timestamp(name)
    if not prefix:
        return ""
    parent = ws_root.parent
    if not parent.is_dir():
        return ""
    terminal = {RunStatus.DONE, RunStatus.HALTED}
    for sib in sorted(parent.iterdir()):
        if sib.name == name or not is_workspace_dir(sib):
            continue
        if _strip_trailing_timestamp(sib.name) != prefix:
            continue
        if sib.name > name and _read_status(sib) in terminal:
            return f"; superseded by terminal sibling {sib.name}"
    return ""


_TS_SUFFIX_RE = re.compile(r"-\d{8}-\d{6}$")


def _strip_trailing_timestamp(name: str) -> str:
    """Strip a trailing ``-YYYYMMDD-HHMMSS`` to recover the case slug prefix.

    Returns "" if the name has no such suffix (so unrelated dirs never share a
    derived prefix and get falsely linked as supersede pairs).
    """
    m = _TS_SUFFIX_RE.search(name)
    return name[: m.start()] if m else ""


def classify_workspace(
    ws_root: pathlib.Path,
    live_run_basenames: set[str],
    process_scan_degraded: bool,
) -> OrphanVerdict:
    """Classify a single workspace against the orphan predicate.

    ``live_run_basenames`` / ``process_scan_degraded`` come from
    :func:`scan_live_run_workspaces` (computed ONCE per pass and threaded in,
    so a multi-workspace scan does a single process enumeration).
    """
    status = _read_status(ws_root)

    if status != RunStatus.IN_PROGRESS:
        return OrphanVerdict(
            workspace=ws_root,
            status=status,
            is_orphan=False,
            reason="",
            skip_reason=f"status is {status!r} (only in_progress runs are reapable)",
        )

    if _lock_holder_alive(ws_root):
        return OrphanVerdict(
            workspace=ws_root,
            status=status,
            is_orphan=False,
            reason="",
            skip_reason="holds a live .run.lock (run is alive)",
        )

    # FAIL SAFE: if we are blind to the process table, treat as alive.
    if process_scan_degraded:
        return OrphanVerdict(
            workspace=ws_root,
            status=status,
            is_orphan=False,
            reason="",
            skip_reason=(
                "process enumeration unavailable — treating as ALIVE "
                "(fail-safe; never reap on uncertainty)"
            ),
        )

    if ws_root.name in live_run_basenames:
        return OrphanVerdict(
            workspace=ws_root,
            status=status,
            is_orphan=False,
            reason="",
            skip_reason="a live `omicairn run` OS process references this workspace",
        )

    # NEVER-RUN guard: a freshly-`init`ed case is in_progress with no lock and
    # no process (init seeds exactly that), but it has run ZERO stages — there
    # is no abandoned work to reap. Reaping it would WRONGLY halt a valid case
    # awaiting its first dispatch. Skip it (it is pending, not orphaned).
    if _never_run(ws_root):
        return OrphanVerdict(
            workspace=ws_root,
            status=status,
            is_orphan=False,
            reason="",
            skip_reason=(
                "freshly initialized (0 stages run) — awaiting first dispatch, "
                "not orphaned"
            ),
        )

    reason = (
        "orphaned: in_progress with no live lock or process "
        "(likely superseded by a re-run or abandoned)"
        + _superseded_note(ws_root)
    )
    return OrphanVerdict(
        workspace=ws_root,
        status=status,
        is_orphan=True,
        reason=reason,
        skip_reason="",
    )


def find_orphans(
    target: pathlib.Path,
) -> tuple[list[OrphanVerdict], bool]:
    """Classify the target (single workspace OR workspaces-root).

    Returns ``(verdicts, process_scan_degraded)``. ``verdicts`` covers every
    workspace examined (orphan or not) so callers can print a full summary.
    A single process-table enumeration is shared across all workspaces.
    """
    live, degraded = scan_live_run_workspaces()

    if is_workspace_dir(target):
        roots = [target]
    else:
        roots = [c for c in sorted(target.iterdir()) if is_workspace_dir(c)]

    verdicts = [classify_workspace(r, live, degraded) for r in roots]
    return verdicts, degraded


def is_workspace_live(
    ws_root: pathlib.Path,
    live_run_basenames: set[str],
    process_scan_degraded: bool,
) -> bool:
    """True iff ``ws_root`` is a currently-LIVE OmiCairn run.

    This is the AUTHORITATIVE liveness predicate the concurrency cap and the
    ``fleet`` view share — STATE.md ``status: in_progress`` is deliberately NOT
    consulted (it goes stale; a killed run keeps that status forever). A run is
    live iff EITHER:

      - its ``.run.lock`` holder PID is alive (``lock.inspect``), OR
      - a live ``omicairn run`` OS process references this workspace's dir name.

    Mirrors :func:`fleet.build_row`'s liveness rule. When the process scan is
    degraded we cannot prove a process is alive, so we fall back to the lock
    signal alone (a held live lock still proves liveness — we never assume a
    run is dead on uncertainty, so the cap can't be silently exceeded).
    """
    if _lock_holder_alive(ws_root):
        return True
    if process_scan_degraded:
        return False
    return ws_root.name in live_run_basenames


def count_live_runs(
    target: pathlib.Path,
    exclude: pathlib.Path | None = None,
) -> tuple[int, bool]:
    """Count currently-LIVE OmiCairn runs under ``target`` (a workspaces root or a
    single workspace), reusing the SAME liveness machinery as ``reap``/``fleet``.

    Returns ``(live_count, process_scan_degraded)``. ``exclude`` (when given,
    matched by directory name) is never counted — this is how the concurrency
    cap excludes the run it is ABOUT to launch/resume from its own count, so a
    resume of an already-live workspace never self-blocks.

    A single process-table enumeration is shared across all workspaces (same
    pattern as :func:`find_orphans`).
    """
    live, degraded = scan_live_run_workspaces()

    if is_workspace_dir(target):
        roots = [target]
    elif target.is_dir():
        roots = [c for c in sorted(target.iterdir()) if is_workspace_dir(c)]
    else:
        roots = []

    exclude_name = exclude.name if exclude is not None else None
    count = 0
    for r in roots:
        if exclude_name is not None and r.name == exclude_name:
            continue
        if is_workspace_live(r, live, degraded):
            count += 1
    return count, degraded


def reap_workspace(verdict: OrphanVerdict) -> None:
    """Mark one confirmed orphan HALTED and journal the event.

    NO files are deleted. Caller MUST have confirmed ``verdict.is_orphan``.
    """
    if not verdict.is_orphan:  # defensive — never mutate a non-orphan
        raise ValueError(f"refusing to reap non-orphan {verdict.workspace}")
    ws_root = verdict.workspace
    state_path = ws_root / "STATE.md"
    s = state_mod.read(state_path)
    state_mod.set_status(s, RunStatus.HALTED, reason=verdict.reason)
    state_mod.write(state_path, s)
    stage_mod.append_event(
        ws_root,
        {
            "kind": EventKind.REAPED_ORPHAN,
            "previous_status": RunStatus.IN_PROGRESS,
            "new_status": RunStatus.HALTED,
            "reason": verdict.reason,
        },
    )


def reap(target: pathlib.Path, apply: bool = False) -> ReapReport:
    """Find orphaned runs under ``target`` and (iff ``apply``) reap them.

    ``apply=False`` (the DEFAULT) is a dry run: classify and report only, never
    mutate. ``apply=True`` flips each confirmed orphan to HALTED + journals a
    ``reaped_orphan`` event. Idempotent: a second pass finds the now-halted
    workspace no longer in_progress and skips it.
    """
    verdicts, degraded = find_orphans(target)
    report = ReapReport(
        scanned=len(verdicts),
        orphans=[v for v in verdicts if v.is_orphan],
        applied=apply,
        process_scan_degraded=degraded,
    )
    if apply:
        for v in report.orphans:
            reap_workspace(v)
            report.reaped.append(v.workspace)
    return report
