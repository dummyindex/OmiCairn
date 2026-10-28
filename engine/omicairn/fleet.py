"""Fleet status — one read-only line per run over a workspaces root.

``omicairn status <ws>`` answers "how is THIS one run doing." It does not answer
the question the operator actually asks every cycle when several runs are in
flight: **"across all my runs, which are live, which are stuck, which finished,
and which is hogging a GPU?"** Today that is a manual sweep — ``ps`` for live
``omicairn run`` processes, ``nvidia-smi`` on the GPU host, ``ls`` the newest stage
dir to guess an ETA — and the operator repeatedly mis-reads "stuck vs working."

``fleet`` automates exactly that sweep, read-only. For each workspace under a
root it returns: topic, status, current stage, per-phase iteration counts,
**live?** (is a ``omicairn run`` orchestrator actually driving it), **last-activity
age** (how long since anything under the workspace was written — the
stuck-vs-working signal), and **host/GPU** when the EXECUTE agent recorded a
detached GPU job in ``job_marker.json``.

Reuse, not reinvention: the liveness machinery already exists in
:mod:`omicairn.reap` (it needs it to decide what is safe to reap). ``fleet`` is
deliberately "``omicairn reap`` but it PRINTS instead of MUTATING": it calls the
same :func:`reap.scan_live_run_workspaces`, :func:`reap.is_workspace_dir`, and
the same PID-file :func:`lock.inspect`. The only genuinely new logic is
last-activity age and surfacing the ``job_marker.json`` host/GPU — neither of
which mutates anything.

Scope (matches the PI verdict — see the design report): this is a *view*, not
a scheduler. It does NOT launch, queue, prioritise, preempt, or kill runs; it
does NOT scrape live ``nvidia-smi`` (that couples the harness to a host's GPU
stack — we surface only the GPU/host the agent already recorded). For killing
dead runs there is ``omicairn reap``; for sequencing launches there is the human +
the audit loop, which is the right altitude for a 5–8-run, API-bound fleet.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import pathlib
from typing import Any

from . import lock as lock_mod
from . import reap as reap_mod
from . import stage as stage_mod
from . import state as state_mod
from .constants import EVENTS_LOG, PIPELINE_ORDER, RunStatus, max_concurrent_runs


RUN_LOCK_NAME = reap_mod.RUN_LOCK_NAME
JOB_MARKER_FILENAME = "job_marker.json"

# Files/dirs whose mtime does NOT reflect agent progress and so must be
# excluded from the "last activity" computation: the run lock is re-touched
# by liveness bookkeeping, and STATE.md is rewritten by `omicairn status`-class
# reads in some flows. We DO count stage dirs, logs, and the heartbeat —
# those move only when the run actually does work.
#
# ``events.jsonl`` is a SPECIAL case (NOT a blanket exclude): it carries real
# progress events (stage_done, gate verdicts, ...) AND the inactivity
# watchdog's periodic ``EXECUTE_PROGRESS`` beacon, which re-touches the file
# every ~75s EVEN while the stage's inner agent is frozen. Counting its raw
# file mtime let a WEDGED stage mask its own stall — the run kept looking
# "active" in `omicairn status` (the beacon-masks-watchdog bug, audit 2026-06-17:
# d1 047_execute sat 44 min frozen, 0 stage-dir writes, but its beacon
# kept events.jsonl fresh). So events.jsonl is handled separately below: we
# key on the newest NON-beacon event time (stage.newest_non_beacon_event_mtime)
# instead of the file mtime.
_ACTIVITY_EXCLUDE = {RUN_LOCK_NAME, EVENTS_LOG}


@dataclasses.dataclass(frozen=True)
class FleetRow:
    """One run's fleet-status line. All fields are read, never written."""

    workspace: pathlib.Path
    topic_slug: str
    status: str                       # in_progress / done / halted / unknown
    current_stage: str | None
    iteration_counts: dict[str, int]
    live: bool                        # a `omicairn run` process is driving this ws
    lock_pid: int | None              # holder of .run.lock, if any (alive or not)
    last_activity_age_s: float | None  # seconds since newest write; None if empty
    host: str | None                  # from job_marker.json (detached GPU job)
    gpu_session: str | None           # pid_or_session from job_marker.json
    job_status: str | None            # job_marker.json "status" (launched/harvested)
    latest_eta_note: str | None       # most recent run.log line mentioning an ETA
    paused_reason: str | None
    halted_reason: str | None

    @property
    def name(self) -> str:
        return self.workspace.name

    @property
    def iters_compact(self) -> str:
        """`s1/p1/e2/c1/r1`-style compact per-phase counter for the table.

        Uses the first letter of each pipeline phase. Phases sharing a first
        letter would collide, but PIPELINE_ORDER's letters are currently
        distinct; if that changes the disambiguation is a one-liner here.
        """
        return "/".join(
            f"{p[0]}{self.iteration_counts.get(p, 0)}" for p in PIPELINE_ORDER
        )


def _safe_state(ws_root: pathlib.Path) -> dict[str, Any]:
    """Read STATE.md tolerantly; return an empty-ish dict on any failure so a
    single corrupt workspace never aborts a whole-fleet sweep."""
    sp = ws_root / "STATE.md"
    if not sp.exists():
        return {}
    try:
        return state_mod.read(sp)
    except (OSError, ValueError):
        return {}


def _newest_activity_mtime(ws_root: pathlib.Path) -> float | None:
    """Newest mtime under the workspace that reflects real progress.

    Walks stage dirs, events.jsonl, logs/, and the heartbeat — but skips the
    run lock (see ``_ACTIVITY_EXCLUDE``). Returns a POSIX timestamp, or None
    when nothing countable exists yet (a freshly-init'd workspace).

    Best-effort and cheap: we only stat top-level entries plus the immediate
    contents of stage / logs dirs, not a full recursive rglob of every
    artifact (a finished run can have thousands of files; we just need the
    freshest signal, which is always a recently-written stage file or log).
    """
    newest: float | None = None

    def _consider(p: pathlib.Path) -> None:
        nonlocal newest
        try:
            m = p.stat().st_mtime
        except OSError:
            return
        if newest is None or m > newest:
            newest = m

    try:
        entries = list(ws_root.iterdir())
    except OSError:
        return None

    for child in entries:
        if child.name in _ACTIVITY_EXCLUDE:
            continue
        # Count the entry itself (covers .heartbeat, STATE.md, ...).
        # events.jsonl is intentionally NOT counted here (it is in
        # _ACTIVITY_EXCLUDE); its real-progress contribution is added below
        # via the NON-beacon event time so a wedged stage's own periodic
        # EXECUTE_PROGRESS beacon can't keep the run looking active.
        _consider(child)
        # And one level into dirs that hold per-stage / per-attempt output, so
        # a just-written results.json / *.log bumps the age even if the parent
        # dir mtime didn't change on this platform.
        if child.is_dir():
            try:
                for sub in child.iterdir():
                    _consider(sub)
            except OSError:
                continue

    # events.jsonl: count the newest NON-beacon event as activity (a real
    # progress event still reflects work; the timer-emitted EXECUTE_PROGRESS
    # beacon does not). Beacon-only tails contribute NOTHING, so a frozen
    # stage no longer masks its stall. Best-effort: None (no events.jsonl, or
    # a beacon-only tail) simply adds no signal.
    event_ts = stage_mod.newest_non_beacon_event_mtime(ws_root)
    if event_ts is not None and (newest is None or event_ts > newest):
        newest = event_ts
    return newest


def _read_job_marker(ws_root: pathlib.Path) -> dict[str, Any] | None:
    """Return the parsed ``job_marker.json`` from the most recent stage that
    has one (newest stage wins — a later attempt's marker supersedes an older
    one), or None. Mirrors orchestrator._job_marker_note's "scan stages in
    reverse" but is phase-agnostic (the fleet view doesn't know the phase)."""
    try:
        stages = stage_mod.list_stages(ws_root)
    except OSError:
        return None
    for st in reversed(stages):
        marker = st.path / JOB_MARKER_FILENAME
        if not marker.is_file():
            continue
        try:
            m = json.loads(marker.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(m, dict):
            return m
    return None


# Substrings that mark a progress/ETA line in an agent's run.log. Kept tiny
# and lowercase; matched case-insensitively. We surface the LATEST matching
# line so the operator sees the agent's own freshest ETA without re-deriving
# it. This is best-effort sugar — never load-bearing — so a noisy match is
# harmless (it's just an extra info string).
_ETA_HINT_TOKENS = ("eta", "/item", "/step", "s/it", "remaining")
# Cap how much of each candidate log we read — ETA lines are emitted late and
# we only want the freshest, so we tail the file rather than read GBs.
_ETA_TAIL_BYTES = 16_384


def _latest_eta_note(ws_root: pathlib.Path) -> str | None:
    """Best-effort: the most recent run.log line that looks like a progress/
    ETA heartbeat, across the newest stage dir's logs. Returns None if none
    found or on any read error — purely informational."""
    try:
        stages = stage_mod.list_stages(ws_root)
    except OSError:
        return None
    for st in reversed(stages):
        # Candidate logs the EXECUTE prompt tells agents to write to.
        candidates = list(st.path.glob("run.log")) + list(st.path.glob("*.log"))
        best: str | None = None
        for log in candidates:
            try:
                size = log.stat().st_size
                with log.open("rb") as f:
                    if size > _ETA_TAIL_BYTES:
                        f.seek(-_ETA_TAIL_BYTES, 2)
                    chunk = f.read().decode("utf-8", "replace")
            except OSError:
                continue
            for line in reversed(chunk.splitlines()):
                low = line.lower()
                if any(tok in low for tok in _ETA_HINT_TOKENS):
                    best = line.strip()[:200]
                    break
            if best:
                break
        if best:
            return best
    return None


def build_row(
    ws_root: pathlib.Path,
    live_run_basenames: set[str],
    process_scan_degraded: bool,
) -> FleetRow:
    """Assemble one :class:`FleetRow` for a workspace.

    ``live_run_basenames`` / ``process_scan_degraded`` come from
    :func:`reap.scan_live_run_workspaces`, computed ONCE per sweep and threaded
    in so a many-workspace fleet does a single process enumeration (same
    pattern as ``reap.find_orphans``).

    Liveness rule (conservative, matches reap's spirit): a run is reported
    ``live`` iff a ``.run.lock`` holder PID is alive OR a live ``omicairn run``
    process references this workspace's dir name. When the process scan is
    degraded we cannot prove a process is alive, so we fall back to the lock
    signal alone (a held live lock still proves liveness).
    """
    s = _safe_state(ws_root)
    status = str(s.get("status") or "unknown")

    lock_state = lock_mod.inspect(ws_root / RUN_LOCK_NAME)
    lock_alive = lock_state.holder_alive
    proc_alive = (
        (not process_scan_degraded) and ws_root.name in live_run_basenames
    )
    live = bool(lock_alive or proc_alive)

    newest = _newest_activity_mtime(ws_root)
    age = None if newest is None else max(0.0, _now_ts() - newest)

    marker = _read_job_marker(ws_root)
    host = gpu_session = job_status = None
    if marker is not None:
        host = marker.get("host") or None
        gpu_session = marker.get("pid_or_session") or None
        job_status = marker.get("status") or None

    return FleetRow(
        workspace=ws_root,
        topic_slug=str(s.get("topic_slug") or ws_root.name),
        status=status,
        current_stage=s.get("current_stage"),
        iteration_counts={p: int(s.get("iteration_counts", {}).get(p, 0))
                          for p in PIPELINE_ORDER},
        live=live,
        lock_pid=lock_state.holder_pid,
        last_activity_age_s=age,
        host=host,
        gpu_session=gpu_session,
        job_status=job_status,
        latest_eta_note=_latest_eta_note(ws_root),
        paused_reason=s.get("paused_reason"),
        halted_reason=s.get("halted_reason"),
    )


def scan_fleet(target: pathlib.Path) -> tuple[list[FleetRow], bool]:
    """Build fleet rows for ``target`` (a single workspace OR a workspaces
    root). Returns ``(rows, process_scan_degraded)``.

    A single process enumeration is shared across all rows. Rows are sorted
    so the operator's eye lands on what matters: **live runs first** (the
    things that can break right now), then by most-recent activity.
    """
    live, degraded = reap_mod.scan_live_run_workspaces()

    if reap_mod.is_workspace_dir(target):
        roots = [target]
    elif target.is_dir():
        roots = [c for c in sorted(target.iterdir())
                 if reap_mod.is_workspace_dir(c)]
    else:
        roots = []

    rows = [build_row(r, live, degraded) for r in roots]
    # Live runs first (the things that can break right now); within each
    # group, freshest activity first (None ages — never-touched runs — sink).
    rows.sort(key=lambda r: (
        0 if r.live else 1,
        r.last_activity_age_s if r.last_activity_age_s is not None else 1e18,
    ))
    return rows, degraded


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _fmt_age(age_s: float | None) -> str:
    """Human age: ``42s`` / ``7m`` / ``3h12m`` / ``2d`` / ``-`` when unknown."""
    if age_s is None:
        return "-"
    age_s = int(age_s)
    if age_s < 90:
        return f"{age_s}s"
    if age_s < 90 * 60:
        return f"{age_s // 60}m"
    if age_s < 48 * 3600:
        h, rem = divmod(age_s, 3600)
        m = rem // 60
        return f"{h}h{m:02d}m" if m else f"{h}h"
    return f"{age_s // 86400}d"


def _live_marker(row: FleetRow) -> str:
    """A glanceable liveness token. ``LIVE`` (process/lock alive), ``dead?``
    (in_progress but no live process — a reap candidate), or ``·`` (terminal:
    done/halted, liveness is irrelevant)."""
    if row.status not in (RunStatus.IN_PROGRESS,):
        return "·"
    return "LIVE" if row.live else "dead?"


def render_table(rows: list[FleetRow], process_scan_degraded: bool) -> str:
    """Render the fleet as a fixed-width text table + a footer of notes.

    One line per run; a second indented line per run only when there is extra
    signal worth surfacing (GPU/host job, an ETA note, a halt/pause reason) so
    the common case stays one-line-per-run and scannable.
    """
    if not rows:
        return "(no OmiCairn workspaces found under the given path)"

    header = (
        f"{'RUN':<34} {'STATUS':<12} {'STAGE':<16} "
        f"{'LIVE':<6} {'AGE':<7} {'ITERS'}"
    )
    lines = [header, "-" * len(header)]
    for r in rows:
        stage = (r.current_stage or "-")[:16]
        lines.append(
            f"{r.name[:34]:<34} {r.status[:12]:<12} {stage:<16} "
            f"{_live_marker(r):<6} {_fmt_age(r.last_activity_age_s):<7} "
            f"{r.iters_compact}"
        )
        # Optional second line(s): only when there's something extra to say.
        extras: list[str] = []
        if r.host:
            gpu = f" session={r.gpu_session}" if r.gpu_session else ""
            jst = f" [{r.job_status}]" if r.job_status else ""
            extras.append(f"GPU job on host={r.host}{gpu}{jst}")
        if r.latest_eta_note:
            extras.append(f"latest progress: {r.latest_eta_note}")
        if r.paused_reason:
            extras.append(f"paused: {r.paused_reason}")
        if r.halted_reason:
            extras.append(f"halted: {r.halted_reason}")
        for ex in extras:
            lines.append(f"    └ {ex}")

    # Footer: counts + the stuck/dead hint, and the degraded-scan caveat.
    n_live = sum(1 for r in rows if r.live)
    n_inprog = sum(1 for r in rows if r.status == RunStatus.IN_PROGRESS)
    n_dead = sum(
        1 for r in rows
        if r.status == RunStatus.IN_PROGRESS and not r.live
    )
    lines.append("")
    # Surface the HARD concurrency cap (owner directive 2026-06-23) so the
    # operator/loop sees headroom at a glance: `N/CAP live`. The cap is the
    # launch-time gate `omicairn run` enforces (constants.max_concurrent_runs,
    # env-overridable via OMICAIRN_MAX_CONCURRENT_RUNS); fleet only DISPLAYS it.
    cap = max_concurrent_runs()
    headroom = max(0, cap - n_live)
    cap_note = (
        f" — {n_live}/{cap} live (cap, {headroom} free)"
        if headroom
        else f" — {n_live}/{cap} live (CAP REACHED; `omicairn run` will refuse new launches)"
    )
    lines.append(
        f"{len(rows)} run(s): {n_live} live, {n_inprog} in_progress, "
        f"{n_dead} in_progress-but-no-live-process.{cap_note}"
    )
    if n_dead:
        lines.append(
            "  (in_progress with no live process = likely orphaned; "
            "`omicairn reap <root>` to triage — dry-run by default.)"
        )
    if process_scan_degraded:
        lines.append(
            "  WARNING: could not enumerate processes (no /proc or ps) — "
            "LIVE reflects the .run.lock only and may under-report live runs."
        )
    return "\n".join(lines)


def _now_ts() -> float:
    return dt.datetime.now(dt.timezone.utc).timestamp()
