"""Wraps `claude -p` as a subprocess.

A single stage invocation is: one `claude -p` call with a bounded
timeout, a workspace --add-dir, a tool allowlist, and an appended
system prompt. Everything the stage needs has already been written to
the workspace (RESEARCH.md, CLAUDE.md, prompt file, subagents, state).

Dry-run mode
------------
Setting the env var ``OMICAIRN_DRY_RUN=1`` bypasses the subprocess entirely
and writes placeholder contract files into the stage directory. This
lets us exercise the orchestrator's stage-loop / verdict-routing
logic without spending claude tokens or waiting on the model.

Dry-run builds every file in ``phase.required_outputs`` as a minimal
but non-trivial placeholder so that ``contracts.verify`` passes.
Per-phase extras (results.json, verdict.json) follow a canonical
shape so downstream cross_check / routing logic sees a plausible
payload.

Setting ``OMICAIRN_DRY_VERDICT=<decision>`` additionally forces the next
verdict-emitting phase to emit that specific decision (e.g.
``retry_execute``, ``back_to_plan``), which is how tests drive
backtracking without staging a real failure.
"""
from __future__ import annotations

import dataclasses
import datetime as dt_module
import json
import os
import pathlib
import re
import subprocess
import tempfile
import time
from typing import Sequence

from . import phases as phases_mod
from . import stage as stage_mod
from .constants import (
    ANALYSIS_MD,
    Decision,
    EventKind,
    DONE_FLAG,
    ENV_DATA_DIR,
    ENV_DRY_RUN,
    ENV_DRY_VERDICT,
    EVENTS_LOG,
    FINDINGS_FILE,
    PREREGISTRATION_FILE,
    RESULTS_JSON,
    SUMMARY_FILE,
    VERDICT_FILE,
    VerdictKey,
    dry_run_enabled,
)

# When OMICAIRN_DRY_RUN equals this literal string (case-insensitive), the
# runner additionally renders the final prompt to
# ``<stage_dir>/_rendered_prompt.md`` and writes a small render-check
# JSON before falling through to the standard placeholder-writer. This
# turns dry-run into a smoke test for prompt-template wiring without
# costing claude tokens. The plain OMICAIRN_DRY_RUN=1 / true / yes path
# is unchanged.
_DRY_RUN_MODE_RENDER: str = "render"


# ---------------------------------------------------------------------------
# Transient failure detection + retry
# ---------------------------------------------------------------------------

# Patterns in the subprocess log that indicate a transient infrastructure
# failure (rate limit, server error, network issue) worth retrying.
# Matched case-insensitively against the full log text.
_TRANSIENT_ERROR_PATTERNS: tuple[str, ...] = (
    "429",                      # HTTP 429 Too Many Requests
    "rate limit",               # "rate limit exceeded"
    "rate_limit",               # Anthropic API error type
    "overloaded",               # "The model is overloaded"
    "529",                      # Anthropic overloaded status
    "503",                      # Service Unavailable
    "502",                      # Bad Gateway
    "server_error",             # API error type
    "internal server error",
    "econnreset",               # Network reset
    "etimedout",                # Network timeout
    "socket hang up",           # Node.js network error
    "connection reset",
    "temporarily unavailable",
)

_DEFAULT_MAX_RETRIES: int = 2
_DEFAULT_RETRY_BASE_DELAY: float = 30.0   # seconds
_RETRY_BACKOFF_FACTOR: float = 2.0
ENV_MAX_RETRIES: str = "OMICAIRN_MAX_RETRIES"

# Inactivity watchdog: kill a `claude -p` turn that has produced NO new
# output (log file size unchanged) for this many seconds, so the
# orchestrator can retry instead of blocking on a hung turn until the
# (often multi-hour) phase wall budget. Observed repeatedly on the GPU
# fleet: a claude turn going idle (state S, 0% CPU) for 80-99 min while
# the orchestrator's subprocess.run blocked on it (audit 2026-06-10,
# d4/d5/d6). The default is DELIBERATELY GENEROUS (45 min) so it never
# fires on a legitimately-long turn — e.g. one that SSHes a multi-hour
# remote GPU job and waits, emitting nothing locally meanwhile. Tune via
# OMICAIRN_INACTIVITY_TIMEOUT_S (seconds). DEFAULT OFF (0): the watchdog uses
# subprocess.Popen polling, whereas the default path uses subprocess.run
# (which the test-suite mocks); keeping it opt-in preserves that contract
# and means production explicitly enables it (e.g. the GPU fleet runner
# exports OMICAIRN_INACTIVITY_TIMEOUT_S=2700). A sensible production value is
# ~45 min — generous enough to never fire on a legitimately-long turn
# (e.g. one that SSHes a multi-hour remote GPU job and waits, emitting
# nothing locally), while still auto-recovering the 80-99 min hung turns
# observed on the fleet (audit 2026-06-10, d4/d5/d6).
ENV_INACTIVITY_TIMEOUT: str = "OMICAIRN_INACTIVITY_TIMEOUT_S"
_DEFAULT_INACTIVITY_TIMEOUT_S: float = 0.0

# DYNAMIC stage-time-limit mode REQUIRES an active inactivity watchdog: the
# soft-ceiling / hard-ceiling logic lives ONLY in the Popen poll loop (which is
# entered only when inactivity_timeout_s > 0), and the liveness-gated soft
# ceiling's kill decision defers to the SAME stall predicate the hang-catcher
# uses (which itself needs inactivity_timeout_s > 0). If DYNAMIC mode were left
# with the default OFF (0) watchdog, `_run_with_watchdogs` would take the bare
# `subprocess.run` HARD-wall branch and DYNAMIC would be silently INERT — a
# progressing long GPU/FT/inference arm (the d7 LLaVA-NeXT held-out drop) would
# STILL be truncated at the wall (round-1/round-3 blocker, audit 2026-07-01).
#
# So when a caller resolves DYNAMIC mode (wall_kill_requires_stall=True) but the
# env/override leaves the watchdog disabled, we FLOOR the inactivity timeout to
# this DYNAMIC-mode default. It is DELIBERATELY GENEROUS (90 min, matching the
# GPU inactivity floor) so it never false-kills a legitimately-quiet-but-live
# arm, while still giving both the poll-loop path AND a live hang predicate. An
# explicitly-configured HIGHER inactivity timeout (env / GPU floor) is never
# lowered — we only RAISE a disabled watchdog to this floor, and only in DYNAMIC
# mode. HARD mode and every non-execute phase are untouched (they never pass
# wall_kill_requires_stall), so their default-OFF watchdog + bare-subprocess.run
# fast path is byte-for-byte preserved.
_DYNAMIC_MODE_INACTIVITY_TIMEOUT_S: float = 5400.0

# Heartbeat liveness signal (audit 2026-06-10). A HEALTHY long EXECUTE turn
# blocked on a remote/foreground GPU job (polling `ssh <host> tail -f`, or a
# multi-hour run) emits NOTHING locally — it is externally identical to a
# HUNG turn (both ~0% CPU, zero log growth), so a log-growth-only watchdog
# can't tell them apart (hence it ships default-OFF). Fix: the watchdog also
# counts the turn ALIVE if a heartbeat FILE was touched since the last poll.
# The EXECUTE agent `touch`es it each poll loop (see prompts/execute.md). It
# lives at {{stage_dir}}/.heartbeat (the agent knows that path), falling back
# to <cwd>/.omicairn_heartbeat when no stage dir is known.
#
# File-write liveness signal (audit 2026-06-11). The heartbeat only covers
# the GPU-POLL EXECUTE shape. A pure-LOCAL EXECUTE turn (CPU scoring /
# data-prep with no remote job) never enters the poll loop, so it never
# touches the heartbeat — and under `--output-format text` the CLI buffers
# stdout, so the log grows only at turn-end. Such a turn writing 1000+ LOC
# was false-killed at the 2700s mark (a SAR-Gate run). Fix: the watchdog
# ALSO counts the turn ALIVE if the newest file mtime anywhere under
# {{stage_dir}} advanced (the agent is writing code/results). Since the
# watchdog never kills while ANY of the three signals is fresh, a default-ON
# watchdog is SAFE for BOTH the GPU-poll and pure-local EXECUTE shapes.
HEARTBEAT_FILENAME: str = ".heartbeat"
HEARTBEAT_FALLBACK_FILENAME: str = ".omicairn_heartbeat"


def _heartbeat_path(
    stage_dir: pathlib.Path | None, cwd: pathlib.Path
) -> pathlib.Path:
    """Resolve the heartbeat file the EXECUTE agent touches to signal it is
    alive-and-waiting. Prefer ``<stage_dir>/.heartbeat`` (the agent's own
    {{stage_dir}}); fall back to ``<cwd>/.omicairn_heartbeat`` when no stage dir
    is known (e.g. the steering-planner one-off)."""
    if stage_dir is not None:
        return stage_dir / HEARTBEAT_FILENAME
    return cwd / HEARTBEAT_FALLBACK_FILENAME


def _mtime_or_none(path: pathlib.Path) -> float | None:
    """Return ``path``'s mtime, or None if it does not exist / can't stat.
    Used by the watchdog to treat a freshly-touched heartbeat as liveness."""
    try:
        return path.stat().st_mtime
    except OSError:
        return None


# Cap the directory walk so the file-write liveness probe stays O(cheap)
# even on a stage dir that accumulates thousands of artifacts (figures,
# per-shard result JSONs, __pycache__). We only need the SINGLE newest
# mtime; we never read contents. 4000 dir-entries is far above any real
# stage and bounds the per-poll cost.
_FILEWRITE_PROBE_MAX_ENTRIES: int = 4000

# Basenames the file-write liveness walk must NOT count as a stage-dir write,
# because they are touched by TIMER-driven observability / liveness bookkeeping
# rather than by the stage's actual work (the beacon-masks-watchdog class of
# bug, audit 2026-06-17):
#   - EVENTS_LOG (events.jsonl): re-touched every ~75s by the inactivity
#     watchdog's own EXECUTE_PROGRESS beacon (_emit_progress_beacon). It
#     normally lives at the WORKSPACE root (a sibling of the stage dir, so the
#     stage-dir walk never sees it) — but excluding it by name is cheap
#     defense-in-depth against a future layout/refactor that ever places it
#     under the walked tree, so a beacon can NEVER be mistaken for real work.
#   - the heartbeat file: it is ALREADY a SEPARATE, explicit liveness signal
#     (hb_advanced). Counting it AGAIN via the file-write walk would
#     double-count it as a stage-dir write, defeating the file-write signal's
#     purpose of proving REAL artifact progress distinct from a bare heartbeat
#     touch.
_FILEWRITE_EXCLUDE_BASENAMES: frozenset[str] = frozenset({
    EVENTS_LOG,
    HEARTBEAT_FILENAME,
    HEARTBEAT_FALLBACK_FILENAME,
})


def _newest_file_mtime(root: pathlib.Path | None) -> float | None:
    """Return the newest mtime of any file under ``root`` (recursively), or
    None if ``root`` is None / missing / empty / unreadable.

    This is the THIRD watchdog liveness signal (audit 2026-06-11): a
    pure-LOCAL EXECUTE turn (CPU scoring / data-prep, NO remote GPU job)
    streams nothing to stdout under ``--output-format text`` AND never
    enters the poll loop that `touch`es the heartbeat — yet it is plainly
    alive while it writes code/results into the stage dir. Keying liveness
    on "did ANY file under the stage dir get written/modified since the
    last poll" catches exactly that case. A genuinely hung turn writes
    nothing, so this never masks a real hang. The walk is bounded by
    ``_FILEWRITE_PROBE_MAX_ENTRIES`` and reads only stat() (never content),
    so it is cheap to call every poll."""
    if root is None:
        return None
    newest: float | None = None
    seen = 0
    try:
        stack = [root]
        while stack:
            current = stack.pop()
            try:
                entries = list(os.scandir(current))
            except OSError:
                continue
            for entry in entries:
                seen += 1
                if seen > _FILEWRITE_PROBE_MAX_ENTRIES:
                    return newest
                try:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(pathlib.Path(entry.path))
                        continue
                    # Skip timer-driven observability/liveness files — a
                    # beacon-written events.jsonl or a heartbeat touch must
                    # never be mistaken for REAL stage-dir artifact progress
                    # (the heartbeat is already its own separate signal).
                    if entry.name in _FILEWRITE_EXCLUDE_BASENAMES:
                        continue
                    m = entry.stat(follow_symlinks=False).st_mtime
                except OSError:
                    continue
                if newest is None or m > newest:
                    newest = m
    except OSError:
        return newest
    return newest


# Process-tree CPU liveness probe (audit 2026-06-17, omicairn-inner-agent-spawn-
# freeze). OmiCairn spawns the agent as a THREE-level tree: the `claude -p`
# toolbox SHIM (the PID we Popen) -> a launcher -> the Node WORKER. The shim
# and launcher sit at 0% CPU the WHOLE turn (they exec/wait on the worker);
# only the grandchild burns CPU. An auditor probing the top-level PID thus
# sees 0% CPU + Δjiffies=0 and — under `--output-format text` (stdout
# buffered to turn-end) — zero log growth + zero stage-dir writes for many
# minutes: EXTERNALLY identical to a hang while the worker is hard at work.
# That false-"freeze" got healthy mid-turn agents repeatedly kill -9'd. The
# probe sums utime+stime over the ENTIRE descendant tree so the beacon can
# report "worker CPU advanced" — a FOURTH liveness datum, true even for a
# silent single long LLM call. OBSERVABILITY ONLY: never in the kill
# predicate (a turn blocked on a remote GPU job is ~0% local CPU yet healthy;
# log/heartbeat/file already govern the kill).
_CPU_PROBE_MAX_PROCS: int = 256


def _proc_cpu_jiffies(pid: int) -> int | None:
    """Return one process's CPU jiffies (utime+stime) from /proc/<pid>/stat,
    or None if unreadable (Linux-only; absent → CPU signal simply omitted)."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    # The comm field (field 2) may contain spaces/parens, so split AFTER the
    # last ')'. utime/stime are fields 14/15 (1-based) → indices 11/12 of the
    # post-comm remainder.
    try:
        rparen = data.rfind(b")")
        rest = data[rparen + 2:].split()
        return int(rest[11]) + int(rest[12])
    except (ValueError, IndexError):
        return None


def _tree_cpu_jiffies(root_pid: int | None) -> int | None:
    """Sum CPU jiffies (utime+stime) over ``root_pid`` AND all descendants,
    or None if nothing is readable. The FOURTH liveness signal: the real
    worker is a GRANDCHILD of the spawned PID (the 0%-CPU shim — see module
    comment), so probing the spawned PID alone reads ~0. We walk the tree via
    /proc/<pid>/task/*/children (no external `ps`); a rising sum proves the
    worker is alive even when log/heartbeat/stage dir are quiet. Bounded by
    ``_CPU_PROBE_MAX_PROCS``."""
    if root_pid is None:
        return None
    total: int | None = None
    seen = 0
    stack = [root_pid]
    visited: set[int] = set()
    while stack:
        pid = stack.pop()
        if pid in visited:
            continue
        visited.add(pid)
        seen += 1
        if seen > _CPU_PROBE_MAX_PROCS:
            break
        j = _proc_cpu_jiffies(pid)
        if j is not None:
            total = j if total is None else total + j
        # Enumerate children across all threads of this pid.
        try:
            task_dir = pathlib.Path(f"/proc/{pid}/task")
            for task in task_dir.iterdir():
                try:
                    kids = (task / "children").read_text().split()
                except OSError:
                    continue
                for k in kids:
                    try:
                        kid = int(k)
                    except ValueError:
                        continue
                    if kid not in visited:
                        stack.append(kid)
        except OSError:
            continue
    return total


def _inactivity_timeout_s(
    override: float | None = None, floor_s: float | None = None
) -> float:
    """Seconds of zero-output before a turn is deemed hung (0 = disabled,
    the default — see ENV_INACTIVITY_TIMEOUT).

    A non-None ``override`` (used by best-effort tail invocations like
    a bounded side task, see ``run(inactivity_timeout_override=...)``) takes
    precedence over the env var. This lets a caller opt into the
    Popen + process-group teardown path (which kills the WHOLE child
    tree — including un-reaped MCP-server grandchildren that block the
    `claude -p` parent from exiting) even when the global watchdog is
    OFF. The env var still wins for the normal pipeline stages, which
    pass no override.

    A non-None ``floor_s`` raises the resolved timeout to at least that
    many seconds — but ONLY when the resolved value is already an ACTIVE
    watchdog (> 0). The orchestrator passes this for GPU-heavy/multimodal
    EXECUTE phases (GPU_INACTIVITY_FLOOR_SECONDS) so a slow-but-live
    per-shard GPU job between heartbeat touches isn't false-killed (the
    d3 wm-dynamics-decouple incident). We apply ``max(resolved, floor)``
    so an explicitly-HIGHER configured value is never lowered; and we
    NEVER raise a disabled (0) or an explicit-override watchdog from the
    floor — a caller that opted into a precise override keeps it, and a
    disabled watchdog stays disabled (degrade-safe: absent/zero signal →
    no floor)."""
    if override is not None:
        # An explicit caller override is authoritative — do not floor it.
        return max(0.0, override)
    raw = os.environ.get(ENV_INACTIVITY_TIMEOUT, "").strip()
    if not raw:
        resolved = _DEFAULT_INACTIVITY_TIMEOUT_S
    else:
        try:
            resolved = max(0.0, float(raw))
        except ValueError:
            resolved = _DEFAULT_INACTIVITY_TIMEOUT_S
    # Floor only an already-ACTIVE watchdog (resolved > 0). A disabled (0)
    # watchdog stays disabled — flooring it would silently turn the
    # watchdog ON for GPU runs that intentionally left it off.
    if floor_s is not None and resolved > 0:
        return max(resolved, floor_s)
    return resolved


# ---------------------------------------------------------------------------
# Execute-stage progress beacon (audit 2026-06-14). The inactivity watchdog
# above ALREADY computes everything an external auditor needs to tell a
# slow-but-alive turn (agent mid-10-15-min LLM call: 0 CPU, idle socket, no
# file write) from a true hang — but it keeps that liveness PRIVATE to its
# poll loop, so the loop / a human / `omicairn status` can't see it without
# forensic mtime/SSH digging (which burned real time clearing wm-spatial-edit
# false-alarms this session). Fix: periodically surface the watchdog's own
# liveness to events.jsonl as an EXECUTE_PROGRESS beacon. The beacon is
# best-effort observability — NEVER load-bearing, and an append_event failure
# (e.g. full disk) must never crash or kill a run. It changes NOTHING about
# when the watchdog kills.
#
# Cadence: emit once every BEACON_EVERY_N_POLLS poll-intervals (not every
# poll, which would spam the log). With the production poll_s≈15s this lands
# a beacon every ~75s — frequent enough that an auditor sees a fresh beacon
# within a couple minutes, sparse enough that a multi-hour stage adds only
# a few-hundred lines to events.jsonl.
_BEACON_EVERY_N_POLLS: int = 5


def _should_beacon(poll_count: int, every_n: int = _BEACON_EVERY_N_POLLS) -> bool:
    """True on the poll iterations where a beacon should be emitted.

    ``poll_count`` is the 1-based count of completed poll intervals (the
    watchdog increments it each time ``proc.wait(timeout=poll_s)`` times
    out, i.e. once per ``poll_s``). We beacon on every ``every_n``-th poll
    so the cadence is ~``every_n * poll_s`` seconds. Pure + deterministic
    so it is trivially unit-testable without a real subprocess. ``every_n``
    <= 0 disables beaconing (defensive; the watchdog always passes >0)."""
    if every_n <= 0 or poll_count <= 0:
        return False
    return poll_count % every_n == 0


def _which_signal_advanced(
    log_grew: bool, heartbeat_advanced: bool, file_advanced: bool
) -> str:
    """Name the liveness signal(s) that last advanced, for the beacon. This
    is the field that lets an auditor see WHY a turn is considered alive
    (streaming output vs. polling a GPU job vs. writing local files). A
    comma-joined list when several advanced together; ``"none"`` when none
    advanced since the last poll (the turn is going quiet — the beacon then
    shows elapsed-since-growth climbing toward the inactivity timeout)."""
    parts: list[str] = []
    if log_grew:
        parts.append("log")
    if heartbeat_advanced:
        parts.append("heartbeat")
    if file_advanced:
        parts.append("file")
    return ",".join(parts) if parts else "none"


def _emit_progress_beacon(
    workspace_root: pathlib.Path | None,
    *,
    stage_dir: pathlib.Path | None,
    elapsed_s: float,
    since_growth_s: float,
    last_signal: str,
    inactivity_timeout_s: float,
    worker_cpu_jiffies: int | None = None,
    worker_cpu_advanced: bool | None = None,
) -> None:
    """Append one EXECUTE_PROGRESS beacon to events.jsonl, best-effort.

    Records the data distinguishing slow-but-alive from hung: elapsed,
    seconds since the watchdog last saw GROWTH, and WHICH signal advanced.
    ``worker_cpu_jiffies``/``worker_cpu_advanced`` carry the descendant-tree
    CPU sum + whether it rose since the last poll (the real worker is a
    0%-CPU shim's grandchild — see ``_tree_cpu_jiffies``), so an auditor sees
    the worker alive even when log/heartbeat/file are quiet (a long buffered
    LLM turn) — closing the omicairn-inner-agent-spawn-freeze misdiagnosis (audit
    2026-06-17). CPU is observability only, NEVER in the kill predicate.

    Wrapped in a blanket try/except: an append_event failure (disk full,
    perms, ...) must NEVER propagate into the watchdog loop and kill a run."""
    if workspace_root is None:
        return
    try:
        payload: dict = {
            "kind": EventKind.EXECUTE_PROGRESS,
            "stage": stage_dir.name if stage_dir is not None else None,
            "elapsed_s": round(elapsed_s, 1),
            "since_last_growth_s": round(since_growth_s, 1),
            "last_signal": last_signal,
            "inactivity_timeout_s": round(inactivity_timeout_s, 1),
        }
        # Only surface the CPU fields when the probe succeeded (Linux /proc).
        # Absent on platforms without /proc — the beacon stays valid either way.
        if worker_cpu_jiffies is not None:
            payload["worker_cpu_jiffies"] = worker_cpu_jiffies
        if worker_cpu_advanced is not None:
            payload["worker_cpu_advanced"] = worker_cpu_advanced
        stage_mod.append_event(workspace_root, payload)
    except Exception:
        # Best-effort observability: a failed beacon must never crash or
        # kill the run. Swallow everything (OSError, ValueError, ...).
        pass


def _append_event_best_effort(
    workspace_root: pathlib.Path | None, payload: dict
) -> None:
    """Append an events.jsonl record, swallowing ALL errors.

    Used by the watchdog poll loop for the DYNAMIC-mode soft-extension /
    hard-ceiling beacons. Like :func:`_emit_progress_beacon`, an append
    failure (disk full, perms, no workspace_root) must NEVER propagate into
    the loop and abort a live run — these records are observability only and
    never gate the kill logic (which keys on ``last_growth`` alone)."""
    if workspace_root is None:
        return
    try:
        stage_mod.append_event(workspace_root, payload)
    except Exception:
        pass


def _run_with_watchdogs(
    cmd: list[str], *, cwd: str, env: dict[str, str], log_file,
    log_path: pathlib.Path, wall_timeout_s: int, inactivity_timeout_s: float,
    poll_s: float = 15.0, heartbeat_path: pathlib.Path | None = None,
    stage_dir: pathlib.Path | None = None,
    stdin_path: pathlib.Path | None = None,
    wall_kill_requires_stall: bool = False,
    hard_ceiling_s: float | None = None,
) -> tuple[int, bool]:
    """Run `claude -p` under BOTH a wall timeout and an output-inactivity
    watchdog. Returns (returncode, timed_out).

    The inactivity watchdog polls THREE liveness signals every ``poll_s``:
    (1) the log file size, (2) ``heartbeat_path``'s mtime (if given), and
    (3) the newest file-write mtime anywhere under ``stage_dir`` (if given).
    The turn is deemed ALIVE if ANY advanced since the last poll. Only when
    ALL THREE have been stale for ``inactivity_timeout_s`` (and that is > 0)
    is the process tree killed and a timeout reported (the caller treats a
    timeout as a terminal failure that the orchestrator retries). Keying on
    GROWTH — not CPU — means a turn that is actively streaming tool calls /
    model output is never killed; the heartbeat additionally protects a
    turn HEALTHILY blocked on a long remote/foreground GPU job that emits
    nothing locally (it `touch`es the heartbeat each poll loop — see
    prompts/execute.md); and the file-write signal protects a pure-LOCAL
    EXECUTE turn (CPU scoring / data-prep, NO remote job → never enters the
    heartbeat poll loop) that is plainly alive while writing code/results
    into the stage dir but, under ``--output-format text``, buffers stdout
    and so grows the log only at turn-end (audit 2026-06-11: a SAR-Gate
    EXECUTE wrote 1041 LOC then got false-killed at the 2700s mark). Only a
    truly hung/idle turn — no log, no heartbeat, AND no file writes — is
    killed.

    ``wall_kill_requires_stall`` (DYNAMIC stage-time-limit mode): when True,
    the WALL cap is demoted from an unconditional kill to a LIVENESS-GATED
    SOFT CEILING — once elapsed exceeds ``wall_timeout_s`` the turn is killed
    ONLY if it is ALSO stalled (the SAME inactivity predicate the hang-catcher
    uses); a turn still emitting any liveness signal is allowed to continue
    (and an ``EXECUTE_WALL_SOFT_EXTENDED`` beacon is emitted). This is the fix
    for progress-blind wall truncation of long GPU/FT/inference arms (owner
    directive 2026-07-01; d7 LLaVA-NeXT held-out drop). The genuine-hang path
    is unchanged: a stalled turn past the wall is killed exactly as before (in
    fact via the same ``last_growth`` test). Default False = the historical
    HARD behaviour (wall is an unconditional kill), so every existing caller
    and non-execute phase is byte-for-byte unchanged.

    ``hard_ceiling_s`` (DYNAMIC-mode absolute backstop): when set, the turn is
    ALWAYS killed once elapsed exceeds it — even while touching files — so a
    runaway that soft-extends forever (junk-writing loop) cannot pin a slot
    indefinitely. None (default) = no absolute backstop (HARD mode / legacy).
    """
    import signal
    # The phase prompt is delivered to `claude --print` on STDIN (the
    # canonical large-prompt path) rather than as an argv element, so a
    # multi-MB prompt can never overflow ARG_MAX. We hand the child an OPEN
    # file object (read end of the prompt file) as its stdin: the OS streams
    # it with no parent-side write loop, so there is NO pipe-buffer deadlock
    # risk even for prompts far larger than the 64 KiB pipe buffer. When no
    # stdin_path is given (legacy callers / tests), the child inherits the
    # parent's stdin unchanged.
    stdin_obj = open(stdin_path, "rb") if stdin_path is not None else None
    try:
        # DYNAMIC stage-time-limit mode (wall_kill_requires_stall) REQUIRES the
        # Popen poll loop — that is the ONLY place the wall is demoted to a
        # liveness-gated soft ceiling and the hard ceiling is enforced. It also
        # requires a POSITIVE inactivity timeout: the soft ceiling's kill
        # decision defers to the same stall predicate the hang-catcher uses,
        # which is a no-op when the watchdog is disabled (<=0). So in DYNAMIC
        # mode, floor a disabled/absent watchdog to the DYNAMIC-mode default so
        # (a) we take the poll-loop path below rather than the bare
        # subprocess.run HARD-wall kill, and (b) a genuinely-stalled arm past
        # the wall is still caught. An already-active (>0) watchdog is never
        # lowered. HARD mode (wall_kill_requires_stall False) keeps its
        # default-OFF fast path untouched — the historical behaviour is
        # byte-for-byte preserved for every non-execute phase and legacy caller.
        if wall_kill_requires_stall and inactivity_timeout_s <= 0:
            inactivity_timeout_s = _DYNAMIC_MODE_INACTIVITY_TIMEOUT_S

        if inactivity_timeout_s <= 0:
            # Watchdog disabled → original single-wall-timeout behaviour.
            # (Unreachable in DYNAMIC mode after the floor above; this is the
            # HARD-mode / legacy fast path.)
            try:
                proc = subprocess.run(
                    cmd, cwd=cwd, env=env, stdin=stdin_obj, stdout=log_file,
                    stderr=subprocess.STDOUT, timeout=wall_timeout_s,
                    check=False,
                )
                return proc.returncode, False
            except subprocess.TimeoutExpired:
                return -1, True

        # Popen + poll: start in its own process group so we can kill the
        # whole tree (the CLI spawns child node/claude processes).
        proc = subprocess.Popen(
            cmd, cwd=cwd, env=env, stdin=stdin_obj, stdout=log_file,
            stderr=subprocess.STDOUT, start_new_session=True,
        )
        return _watchdog_poll_loop(
            proc, cmd=cmd, log_file=log_file, log_path=log_path,
            wall_timeout_s=wall_timeout_s,
            inactivity_timeout_s=inactivity_timeout_s, poll_s=poll_s,
            heartbeat_path=heartbeat_path, stage_dir=stage_dir, signal=signal,
            wall_kill_requires_stall=wall_kill_requires_stall,
            hard_ceiling_s=hard_ceiling_s,
        )
    finally:
        if stdin_obj is not None:
            stdin_obj.close()


def _watchdog_poll_loop(
    proc, *, cmd: list[str], log_file, log_path: pathlib.Path,
    wall_timeout_s: int, inactivity_timeout_s: float, poll_s: float,
    heartbeat_path: pathlib.Path | None, stage_dir: pathlib.Path | None,
    signal,
    wall_kill_requires_stall: bool = False,
    hard_ceiling_s: float | None = None,
) -> tuple[int, bool]:
    """The Popen poll loop, factored out of _run_with_watchdogs so the
    stdin file handle can be opened/closed in a single `with`-style finally
    around BOTH spawn paths.

    Behaviour is unchanged from the inline loop EXCEPT for the wall-cap kill
    site, which honours ``wall_kill_requires_stall`` / ``hard_ceiling_s`` (see
    :func:`_run_with_watchdogs`). With both at their defaults (False / None)
    the loop is byte-for-byte the historical HARD behaviour."""
    start = time.time()
    last_size = -1
    last_hb_mtime = _mtime_or_none(heartbeat_path) if heartbeat_path else None
    last_file_mtime = _newest_file_mtime(stage_dir) if stage_dir else None
    last_growth = time.time()
    # Progress-beacon bookkeeping: workspace_root is the stage dir's parent
    # (stage dirs are created directly under the workspace root, e.g.
    # <ws>/002_execute — see stage.create_stage_dir), which is where
    # append_event writes events.jsonl. None when no stage_dir is known
    # (e.g. the steering-planner one-off) → beacons are simply skipped.
    beacon_ws_root = stage_dir.parent if stage_dir is not None else None
    poll_count = 0
    last_signal = "none"
    # DYNAMIC-mode soft-extension bookkeeping: emit the EXECUTE_WALL_SOFT_EXTENDED
    # beacon on the FIRST poll the wall is soft-extended (the transition an
    # auditor cares about) and thereafter only on the beacon cadence, so a
    # multi-hour soft-extended stage does not spam events.jsonl one record per
    # poll_s. Never load-bearing (append is best-effort; kill logic untouched).
    soft_extended_once = False
    # Worker-CPU liveness (audit 2026-06-17): proc.pid is the 0%-CPU shim;
    # the worker is its grandchild. Track the whole-tree jiffie sum so the
    # beacon reports CPU progress. OBSERVABILITY ONLY — not in the kill test.
    last_cpu_jiffies = _tree_cpu_jiffies(proc.pid)

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

    while True:
        try:
            rc = proc.wait(timeout=poll_s)
            return rc, False
        except subprocess.TimeoutExpired:
            pass
        poll_count += 1
        now = time.time()
        # Compute the liveness signals FIRST (before the wall decision) so the
        # wall cap can be liveness-gated in DYNAMIC mode: a wall crossing only
        # kills when the turn is ALSO stalled. In HARD mode
        # (wall_kill_requires_stall False) the wall is still an unconditional
        # kill — handled just below, before liveness even matters — so the
        # historical path is byte-for-byte unchanged.
        try:
            size = log_path.stat().st_size
        except OSError:
            size = last_size
        hb_mtime = _mtime_or_none(heartbeat_path) if heartbeat_path else None
        file_mtime = _newest_file_mtime(stage_dir) if stage_dir else None
        # ALIVE if ANY signal advanced: log grew, heartbeat was (re-)touched,
        # OR a file under the stage dir was written/modified. A freshly-
        # CREATED heartbeat or first-seen file mtime (None → a value) also
        # counts as a tick.
        log_grew = size != last_size
        hb_advanced = hb_mtime != last_hb_mtime
        file_advanced = file_mtime != last_file_mtime

        # ── WALL CAP ─────────────────────────────────────────────────────
        # DYNAMIC-mode absolute backstop FIRST: a stage that has run past the
        # hard safety ceiling is ALWAYS killed, even while touching files —
        # this is the one bound a soft-extended run can never cross (guards
        # the "junk-writing loop forever" pathology). Checked before the
        # liveness-gated soft path so nothing can defer it.
        if hard_ceiling_s is not None and now - start > hard_ceiling_s:
            try:
                log_file.write(
                    f"\n# WATCHDOG: hard safety ceiling {int(hard_ceiling_s)}s "
                    f"exceeded (elapsed {int(now - start)}s) — killing even "
                    f"though liveness may be fresh; a multi-day 'progressing' "
                    f"stage is a runaway, not science.\n"
                )
                log_file.flush()
            except (OSError, ValueError):
                pass
            if beacon_ws_root is not None:
                _append_event_best_effort(beacon_ws_root, {
                    "kind": EventKind.EXECUTE_HARD_CEILING_KILL,
                    "elapsed_s": round(now - start, 1),
                    "hard_ceiling_s": round(hard_ceiling_s, 1),
                    "since_last_growth_s": round(now - last_growth, 1),
                })
            _kill_tree()
            return -1, True
        if now - start > wall_timeout_s:
            if not wall_kill_requires_stall:
                # HARD mode / legacy: the wall is an unconditional kill.
                _kill_tree()
                return -1, True
            # DYNAMIC mode: the wall is a LIVENESS-GATED SOFT CEILING. Kill
            # ONLY if the turn is ALSO stalled — i.e. the SAME predicate the
            # inactivity hang-catcher uses (no new liveness signal AND stale
            # for the inactivity window). A fresh liveness signal this poll,
            # or a still-recent last_growth, means the arm is PROGRESSING past
            # its estimated wall → soft-extend (log a beacon, keep polling)
            # rather than truncate it (the d7 LLaVA-NeXT held-out drop). The
            # kill decision is deferred entirely to the inactivity path below,
            # so hang detection stays a single source of truth.
            _stalled = (
                not (log_grew or hb_advanced or file_advanced)
                and inactivity_timeout_s > 0
                and now - last_growth > inactivity_timeout_s
            )
            if not _stalled:
                # Throttle: log the first soft-extension always (the
                # transition), then only on the beacon cadence.
                if (not soft_extended_once or _should_beacon(poll_count)) \
                        and beacon_ws_root is not None:
                    _append_event_best_effort(beacon_ws_root, {
                        "kind": EventKind.EXECUTE_WALL_SOFT_EXTENDED,
                        "elapsed_s": round(now - start, 1),
                        "wall_timeout_s": round(float(wall_timeout_s), 1),
                        "since_last_growth_s": round(now - last_growth, 1),
                        "last_signal": _which_signal_advanced(
                            log_grew, hb_advanced, file_advanced
                        ),
                        "inactivity_timeout_s": round(inactivity_timeout_s, 1),
                    })
                soft_extended_once = True
            # If _stalled, we fall through to the inactivity path below, which
            # performs the kill (same code, same predicate). No kill here.

        if log_grew or hb_advanced or file_advanced:
            last_size = size
            last_hb_mtime = hb_mtime
            last_file_mtime = file_mtime
            last_growth = now
            last_signal = _which_signal_advanced(
                log_grew, hb_advanced, file_advanced
            )
        else:
            # Nothing advanced this poll. Record that the turn is going quiet
            # so the beacon's last_signal reflects the ACTUAL current state
            # ("none") rather than a stale-reassuring name from a much earlier
            # poll (e.g. "heartbeat,file") while since_last_growth_s climbs
            # toward the kill — which misled auditors during the d3
            # wm-dynamics-decouple false-kill investigation. Best-effort
            # observability ONLY: this does NOT touch last_growth and so does
            # NOT change when a genuinely-idle turn is killed (the kill test
            # below keys on last_growth, untouched here).
            last_signal = _which_signal_advanced(False, False, False)  # "none"
        if not (log_grew or hb_advanced or file_advanced) and \
                now - last_growth > inactivity_timeout_s:
            # No output AND no heartbeat AND no stage-dir file writes for the
            # inactivity window → hung turn. Record it in the log so the
            # failure is diagnosable, then kill + retry.
            try:
                log_file.write(
                    f"\n# WATCHDOG: no output, heartbeat, or file writes for "
                    f"{int(inactivity_timeout_s)}s — killing hung turn so the "
                    f"orchestrator can retry.\n"
                )
                log_file.flush()
            except (OSError, ValueError):
                pass
            _kill_tree()
            return -1, True

        # Best-effort liveness beacon (the turn is still running — we only
        # reach here when it was NOT killed above). Surfaces the watchdog's
        # private liveness to events.jsonl on a ~_BEACON_EVERY_N_POLLS *
        # poll_s cadence so an external auditor can tell slow-but-alive from
        # hung. Never load-bearing: _emit_progress_beacon swallows all
        # errors, and the kill logic above is untouched.
        if _should_beacon(poll_count):
            # Whole-tree CPU jiffies — proof the worker grandchild is alive
            # even when log/heartbeat/file are quiet. Observability only.
            cpu_jiffies = _tree_cpu_jiffies(proc.pid)
            if cpu_jiffies is None or last_cpu_jiffies is None:
                cpu_advanced: bool | None = None
            else:
                cpu_advanced = cpu_jiffies > last_cpu_jiffies
            last_cpu_jiffies = cpu_jiffies
            _emit_progress_beacon(
                beacon_ws_root,
                stage_dir=stage_dir,
                elapsed_s=now - start,
                since_growth_s=now - last_growth,
                last_signal=last_signal,
                inactivity_timeout_s=inactivity_timeout_s,
                worker_cpu_jiffies=cpu_jiffies,
                worker_cpu_advanced=cpu_advanced,
            )


# When Claude Code runs against a Bedrock backend (CLAUDE_CODE_USE_BEDROCK=1),
# Anthropic model ids must be given in their `global.anthropic.<id>`
# inference-profile form; bare ids such as `claude-opus-4-8` are rejected.
# In that mode a bare id is normalised to the profile form so the same
# RESEARCH.md / omicairn_config.yaml runs unchanged on either backend.
# Already-qualified ids (`global.*`, `us.*`, `eu.*`, `anthropic.*`, or an
# ARN) pass through untouched. With the default Anthropic API backend ids
# are passed through as written. OMICAIRN_NO_MODEL_NORMALIZE=1 disables the
# rewrite entirely. See docs/references/omicairn-config-schema.md.
ENV_NO_MODEL_NORMALIZE: str = "OMICAIRN_NO_MODEL_NORMALIZE"
ENV_USE_BEDROCK: str = "CLAUDE_CODE_USE_BEDROCK"
_BARE_ANTHROPIC_MODEL_RE = re.compile(r"^claude-[a-z0-9.-]+$", re.IGNORECASE)
_TRUTHY = ("1", "true", "yes", "on")


def normalize_model_id(model: str) -> str:
    """On the Bedrock backend, map a bare Anthropic model id to the
    `global.anthropic.<id>` form Bedrock accepts; otherwise (or for an
    already-qualified id) return the id unchanged.

    Domain-agnostic, provider-specific plumbing — it keys only on the id
    SHAPE, never on research content."""
    if os.environ.get(ENV_NO_MODEL_NORMALIZE, "").strip().lower() in _TRUTHY:
        return model
    if os.environ.get(ENV_USE_BEDROCK, "").strip().lower() not in _TRUTHY:
        return model
    m = (model or "").strip()
    if _BARE_ANTHROPIC_MODEL_RE.match(m):
        return f"global.anthropic.{m}"
    return model


# ---------------------------------------------------------------------------
# argv-overflow safety: route large prompt bodies off argv (audit 2026-06-27)
# ---------------------------------------------------------------------------
#
# A verbose phase can assemble a prompt of thousands of lines plus a large
# domain system prompt. Passing those as argv
# elements (`-p <prompt>`, `--append-system-prompt <text>`) can push the
# total argv past the kernel's ARG_MAX, so execve refuses to spawn `claude`
# with `OSError: [Errno 7] Argument list too long` — killing the orchestrator
# at spawn time (reproduced twice on certifybench, 2177+-line prompt).
#
# Fix: the prompt always goes to the child's STDIN (`claude --print` with no
# positional reads the prompt from stdin — the canonical large-prompt path),
# and the append-system-prompt goes to a temp file passed via the CLI's
# `--append-system-prompt-file` flag. Both flags/paths verified against the
# installed native CLI (`claude --claude-help`). The `_GUARD` constant is a
# last-ditch backstop: if whatever argv we DO assemble ever approaches
# ARG_MAX we raise a clear, actionable error naming the oversized arg rather
# than dying with a cryptic Errno 7.
CLAUDE_FLAG_PRINT: str = "--print"
CLAUDE_FLAG_APPEND_SYSTEM_PROMPT_FILE: str = "--append-system-prompt-file"
# Basename for the per-stage append-system-prompt sidecar file (written into
# the stage dir when known so it is inspectable, else a tempfile).
APPEND_SYSTEM_PROMPT_FILENAME: str = ".omicairn_append_system_prompt.txt"
# Basename for the per-stage prompt sidecar fed to `claude --print` on stdin.
PROMPT_STDIN_FILENAME: str = ".omicairn_prompt_stdin.txt"
# Spawn only if the assembled argv byte-length is under this fraction of
# SC_ARG_MAX. With the prompt + system prompt off argv this should never
# trip; it is a backstop, not a routine code path.
_ARGV_SAFE_FRACTION: float = 0.5
# Fallback ARG_MAX (bytes) when the platform exposes no SC_ARG_MAX (POSIX
# floor is 4096; this is a conservative 128 KiB so the guard stays useful).
_ARGV_MAX_FALLBACK: int = 128 * 1024


def _argv_byte_len(cmd: Sequence[str]) -> int:
    """Total bytes the argv vector contributes to execve: each arg's UTF-8
    encoding plus its NUL terminator. A close upper bound on the kernel's
    ARG_MAX accounting (which also counts the env block + pointers — we leave
    headroom via _ARGV_SAFE_FRACTION rather than model the env exactly)."""
    return sum(len(a.encode("utf-8", "surrogatepass")) + 1 for a in cmd)


def _argv_safe_limit() -> int:
    """Bytes of argv we allow before refusing to spawn (a fraction of the
    platform ARG_MAX). Degrade-safe: any failure reading SC_ARG_MAX falls
    back to a conservative floor."""
    try:
        arg_max = os.sysconf("SC_ARG_MAX")
        if not isinstance(arg_max, int) or arg_max <= 0:
            arg_max = _ARGV_MAX_FALLBACK
    except (ValueError, OSError, AttributeError):
        arg_max = _ARGV_MAX_FALLBACK
    return int(arg_max * _ARGV_SAFE_FRACTION)


def _assert_argv_within_limit(cmd: Sequence[str]) -> None:
    """Raise a clear, actionable error if the assembled argv would approach
    ARG_MAX, naming the single largest arg. This should be unreachable in
    practice (the prompt is on stdin and the system prompt is a file), so a
    trip means a NEW large arg crept onto argv and must itself be routed off."""
    total = _argv_byte_len(cmd)
    limit = _argv_safe_limit()
    if total <= limit:
        return
    biggest = max(cmd, key=lambda a: len(a.encode("utf-8", "surrogatepass")))
    biggest_len = len(biggest.encode("utf-8", "surrogatepass"))
    raise ValueError(
        f"assembled claude argv is {total} bytes, over the safe limit "
        f"{limit} ({int(_ARGV_SAFE_FRACTION * 100)}% of ARG_MAX) — execve "
        f"would fail with 'Argument list too long'. Largest arg is "
        f"{biggest_len} bytes ({biggest[:60]!r}...); route it through stdin "
        f"or a file rather than argv."
    )


def _max_retries() -> int:
    """Read max retries from OMICAIRN_MAX_RETRIES env var, defaulting to 2."""
    raw = os.environ.get(ENV_MAX_RETRIES, "").strip()
    if not raw:
        return _DEFAULT_MAX_RETRIES
    try:
        return max(0, int(raw))
    except ValueError:
        return _DEFAULT_MAX_RETRIES


# .env loading. The repo's `.env` (or per-workspace `.env` override)
# carries the OAuth / API-key credentials OmiCairn uses to dispatch its
# `claude -p` subprocess. We read it once per `run` invocation and
# merge into the subprocess env.
#
# Resolution order (first hit wins):
#   1. <workspace_root>/.env        (per-run override)
#   2. <workspace_root>/../.env     (repo root if workspace is under workspaces/)
#   3. <cwd>/.env                   (caller's CWD)
#   4. $OMICAIRN_DOTENV (env var)     (explicit override path)
# Each `.env` location is paired with a sibling `.env.hf` (same dirs),
# a dedicated gitignored file for HuggingFace creds (HF_TOKEN /
# HUGGING_FACE_HUB_TOKEN) so a token can be dropped without touching the
# general `.env`. `.env.hf` is loaded with the SAME fallback semantics
# (os.environ wins). Keeping the token in its own file keeps secret
# handling auditable + separate from the OAuth/API creds in `.env`.
#
# Format: a trivial `KEY=value` parser. Lines starting with `#` are
# comments. Values may be quoted with `"` or `'`. Existing values in
# os.environ always win — `.env` is a fallback, never an override
# (so a CI environment that already has the right token isn't
# clobbered).
_DOTENV_PATH_OVERRIDE_ENV: str = "OMICAIRN_DOTENV"


def _candidate_dotenv_paths(workspace_root: pathlib.Path) -> list[pathlib.Path]:
    """Ordered list of .env paths to try. Stable / deduplicated."""
    seen: set[pathlib.Path] = set()
    out: list[pathlib.Path] = []
    explicit = os.environ.get(_DOTENV_PATH_OVERRIDE_ENV, "").strip()
    if explicit:
        p = pathlib.Path(explicit).expanduser().resolve()
        if p not in seen:
            out.append(p)
            seen.add(p)
    for cand in (
        workspace_root / ".env",
        workspace_root / ".env.hf",
        workspace_root.parent / ".env",
        workspace_root.parent / ".env.hf",
        # Repo root from a workspaces/<topic>-<ts>/ layout.
        workspace_root.parent.parent / ".env",
        workspace_root.parent.parent / ".env.hf",
        pathlib.Path.cwd() / ".env",
        pathlib.Path.cwd() / ".env.hf",
    ):
        try:
            cand_resolved = cand.resolve()
        except OSError:
            continue
        if cand_resolved not in seen:
            out.append(cand_resolved)
            seen.add(cand_resolved)
    return out


def _parse_dotenv_text(text: str) -> dict[str, str]:
    """Trivial KEY=value parser. Returns the parsed mapping.

    - Blank lines and lines starting with `#` are skipped.
    - `export KEY=value` is accepted (the `export` prefix is stripped).
    - Values surrounded by matching `"` or `'` have those quotes stripped.
    - Inline `# ...` comments after an unquoted value are stripped.
    - Whitespace around `=` is trimmed.
    - Malformed lines (no `=`) are silently ignored.
    """
    out: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or not (key[0].isalpha() or key[0] == "_"):
            continue
        if not all(c.isalnum() or c == "_" for c in key):
            continue
        value = value.strip()
        # Quoted-value handling.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        else:
            # Strip an inline comment from an unquoted value.
            if " #" in value:
                value = value.split(" #", 1)[0].rstrip()
        out[key] = value
    return out


def _load_dotenv_into(
    env: dict[str, str],
    workspace_root: pathlib.Path,
) -> tuple[pathlib.Path, list[str]] | None:
    """Find the first existing .env candidate and merge its keys into ``env``.

    Existing keys in ``env`` win — this function is a fallback only,
    matching the conventional dotenv semantics. Returns
    ``(path, loaded_keys)`` for the .env that was applied, or ``None``
    if no candidate existed. The caller is responsible for writing an
    audit line to its own log; doing so here got clobbered by the
    log_path truncation in the retry loop.
    """
    for cand in _candidate_dotenv_paths(workspace_root):
        if not cand.is_file():
            continue
        try:
            parsed = _parse_dotenv_text(cand.read_text(errors="replace"))
        except OSError:
            continue
        loaded_keys: list[str] = []
        for key, value in parsed.items():
            if key in env and env[key]:
                continue
            env[key] = value
            loaded_keys.append(key)
        return (cand, sorted(loaded_keys))
    return None


# Patterns indicating a Claude Code account-level *session cap* (the
# 5-hour "you've hit your limit" window). Distinct from the API-side
# transient errors above: a session cap is NOT retryable on the same
# token within the current window — the only fix is to wait until the
# stated reset time and re-invoke. We surface this as its own
# StageOutcome status (SESSION_LIMIT) so the outer wrapper / cron can
# schedule a resume rather than burning attempts in a tight retry loop.
_SESSION_CAP_PATTERNS: tuple[str, ...] = (
    "you've hit your limit",        # straight apostrophe
    "you’ve hit your limit",  # curly apostrophe (CLI sometimes uses U+2019)
    "you have hit your limit",
    # The CLI now emits "...hit your SESSION limit..." (the extra word
    # broke the bare "hit your limit" match, so auto-resume silently
    # never engaged — the parkinson 2026-05 runs hard-failed in
    # 2s instead of sleeping to the reset). Match the phrase
    # apostrophe-agnostically by anchoring after the apostrophe.
    "hit your session limit",
    "session limit reached",
    "hit your usage limit",
    "claude usage limit reached",
    "5-hour limit",
)

# Best-effort parse of the Claude Code session-cap reset hint. The CLI
# prints lines like:  "You've hit your limit · resets 6am (<IANA/Zone>)"
# Also seen in the wild:
#   "limit resets at 6:00 AM UTC"
#   "will reset at 2026-04-14T11:00Z"
#   "resets in 2h 14m"
# We capture (a) IANA-tz form first (richest), (b) abbreviated tz form,
# (c) ISO timestamp, (d) relative duration. Parsing is best-effort —
# wrappers should also consult ``reset_raw_line`` for the unparsed text.
_RESET_TIME_PATTERN = re.compile(
    r"resets?\s+(?:at\s+)?([0-9]{1,2}(?::[0-9]{2})?\s*[ap]m?)\s*\(([A-Za-z_/+\-0-9]+)\)",
    re.IGNORECASE,
)
_RESET_TIME_ABBR_PATTERN = re.compile(
    r"resets?\s+(?:at\s+)?([0-9]{1,2}(?::[0-9]{2})?\s*[ap]m?)\s+([A-Z]{2,5})\b",
    re.IGNORECASE,
)
_RESET_ISO_PATTERN = re.compile(
    r"reset\s+(?:at\s+)?(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?Z?)",
    re.IGNORECASE,
)
_RESET_RELATIVE_PATTERN = re.compile(
    r"resets?\s+in\s+([0-9hms\s]+)",
    re.IGNORECASE,
)


# Auto-resume on session cap (Tier-fix 2026-04-29). When detect_session_cap
# returns a parsable reset time, we sleep until reset + buffer and retry
# the same stage in-place rather than exiting and requiring a manual
# `omicairn run` resume. Bounded to MAX_AUTO_WAIT_S so a misparsed reset time
# can't put the runner into a multi-day sleep. Opt-out by setting
# OMICAIRN_AUTO_RESUME_SESSION_CAP=0.
_AUTO_RESUME_ENV: str = "OMICAIRN_AUTO_RESUME_SESSION_CAP"
_AUTO_RESUME_BUFFER_S: int = 90        # extra seconds after reset before retry
_AUTO_RESUME_MAX_WAIT_S: int = 6 * 3600  # 6 hours absolute ceiling
_AUTO_RESUME_MIN_WAIT_S: int = 30      # if reset is in the past, sleep at least this


def _auto_resume_enabled() -> bool:
    raw = os.environ.get(_AUTO_RESUME_ENV, "").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _parse_session_reset(cap: dict) -> dt_module.datetime | None:
    """Best-effort: parse the cap dict's reset_text + reset_tz into a
    UTC-aware datetime. Returns None on any parse failure.

    Handles:
      - "9:40pm (Etc/UTC)"               → today at 21:40 in that zone
      - "6am (Etc/UTC)"                  → today at 06:00 in that zone
      - ISO 8601 timestamps              → as-is
      - relative durations ("in 2h 14m") → now + duration
    Always returns a UTC-aware datetime; if the parsed time is in the
    past relative to now, advances by 24h (the cap rolls to next day).
    """
    if not isinstance(cap, dict):
        return None
    reset_text = (cap.get("reset_text") or "").strip()
    reset_tz = (cap.get("reset_tz") or "").strip()
    if not reset_text:
        return None

    now_utc = dt_module.datetime.now(dt_module.timezone.utc)

    # ISO 8601 form.
    if "T" in reset_text and "-" in reset_text[:10]:
        try:
            normalised = reset_text.rstrip("Z")
            d = dt_module.datetime.fromisoformat(normalised)
            if d.tzinfo is None:
                d = d.replace(tzinfo=dt_module.timezone.utc)
            return d.astimezone(dt_module.timezone.utc)
        except ValueError:
            pass

    # Relative form: "2h 14m", "30m", "45 minutes".
    rel_match = re.fullmatch(
        r"\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*m(?:in(?:utes?)?)?)?\s*(?:(\d+)\s*s)?\s*",
        reset_text, re.IGNORECASE,
    )
    if rel_match and any(rel_match.groups()):
        h = int(rel_match.group(1) or 0)
        m = int(rel_match.group(2) or 0)
        s = int(rel_match.group(3) or 0)
        return now_utc + dt_module.timedelta(hours=h, minutes=m, seconds=s)

    # Clock-time form ("9:40pm", "6am", "11:30 PM").
    clk_match = re.fullmatch(
        r"\s*(\d{1,2})(?::(\d{2}))?\s*([ap])m?\s*", reset_text, re.IGNORECASE,
    )
    if clk_match:
        hour = int(clk_match.group(1))
        minute = int(clk_match.group(2) or 0)
        ampm = clk_match.group(3).lower()
        if ampm == "p" and hour < 12:
            hour += 12
        elif ampm == "a" and hour == 12:
            hour = 0
        # Resolve timezone to a UTC offset.
        tz_obj: dt_module.tzinfo | None = dt_module.timezone.utc
        if reset_tz:
            try:
                from zoneinfo import ZoneInfo
                tz_obj = ZoneInfo(reset_tz)
            except Exception:
                # Fall back to common abbreviations.
                _TZ_ABBR_OFFSETS_HOURS = {
                    "PT": -7, "PST": -8, "PDT": -7,
                    "MT": -6, "MST": -7, "MDT": -6,
                    "CT": -5, "CST": -6, "CDT": -5,
                    "ET": -4, "EST": -5, "EDT": -4,
                    "UTC": 0, "GMT": 0,
                }
                offset_h = _TZ_ABBR_OFFSETS_HOURS.get(reset_tz.upper())
                if offset_h is not None:
                    tz_obj = dt_module.timezone(dt_module.timedelta(hours=offset_h))
        local_today = dt_module.datetime.now(tz_obj).date()
        candidate = dt_module.datetime.combine(
            local_today,
            dt_module.time(hour=hour, minute=minute),
            tzinfo=tz_obj,
        )
        # If reset already passed today, it must be tomorrow.
        if candidate <= dt_module.datetime.now(tz_obj):
            candidate += dt_module.timedelta(days=1)
        return candidate.astimezone(dt_module.timezone.utc)

    return None

# Sentinel written by run() before the subprocess output begins; used by
# detect_session_cap to skip the prompt dump (which could contain the
# trigger phrase verbatim if a user pasted it into RESEARCH.md).
_LOG_OUTPUT_SENTINEL: str = "# --- begin claude output ---"


def detect_session_cap(log_path: pathlib.Path) -> dict | None:
    """Return ``{"reset_text": ..., "reset_tz": ...}`` if the subprocess
    log shows a Claude Code session-cap message, else None.

    The detector runs regardless of returncode because the CLI sometimes
    prints the cap message and exits 0. We scan only the post-sentinel
    region of the log so a user's RESEARCH.md / prompt that quotes the
    trigger phrase can't false-positive every stage.
    """
    try:
        text = log_path.read_text(errors="replace")
    except OSError:
        return None
    # Skip the cmd-dump preamble (which echoes the prompt verbatim).
    # Use rfind so the LAST sentinel wins on retry-appended logs — the
    # cap that matters is the one from the most recent attempt.
    idx = text.rfind(_LOG_OUTPUT_SENTINEL)
    scan = text[idx + len(_LOG_OUTPUT_SENTINEL):] if idx != -1 else text
    scan_lower = scan.lower()
    if not any(pat in scan_lower for pat in _SESSION_CAP_PATTERNS):
        return None
    reset_text: str | None = None
    reset_tz: str | None = None
    raw_line: str | None = None
    # Try the richer patterns first, fall back to coarser ones.
    for pat in (_RESET_TIME_PATTERN, _RESET_TIME_ABBR_PATTERN):
        m = pat.search(scan)
        if m:
            reset_text = m.group(1).strip()
            reset_tz = m.group(2).strip()
            break
    if reset_text is None:
        m = _RESET_ISO_PATTERN.search(scan) or _RESET_RELATIVE_PATTERN.search(scan)
        if m:
            reset_text = m.group(1).strip()
    # Capture the trigger line itself so wrappers have a fallback when
    # parsing fails entirely.
    for line in scan.splitlines():
        ll = line.lower()
        if any(p in ll for p in _SESSION_CAP_PATTERNS):
            raw_line = line.strip()[:240]
            break
    return {"reset_text": reset_text, "reset_tz": reset_tz, "reset_raw_line": raw_line}


def _is_transient_failure(log_path: pathlib.Path, returncode: int) -> bool:
    """True if the subprocess failure looks transient (rate limit, server
    error, network issue) — the log contains one of the known patterns
    and the returncode is not a sentinel like -1 (timeout) or -2 (CLI
    missing).

    Only the post-sentinel region is scanned (same guard ``detect_session_cap``
    uses): the launch preamble carries ``# ts: <time.time()>``, the workspace
    path, and the verbatim prompt — any of which can contain a transient
    substring by chance (e.g. a timestamp whose digits happen to include
    "502"/"503"/"429", or a RESEARCH.md prompt that quotes "rate limit").
    Scanning the whole file misclassified such benign failures as transient
    and burned the full retry budget on a genuinely non-retryable error
    (latent flake surfaced 2026-06-10). We scan from the FIRST sentinel so
    every retry attempt's real output is still covered.
    """
    if returncode in (-1, -2):
        return False
    try:
        text = log_path.read_text(errors="replace")
    except OSError:
        return False
    idx = text.find(_LOG_OUTPUT_SENTINEL)
    scan = text[idx + len(_LOG_OUTPUT_SENTINEL):] if idx != -1 else text
    scan_lower = scan.lower()
    return any(pat in scan_lower for pat in _TRANSIENT_ERROR_PATTERNS)


@dataclasses.dataclass
class ClaudeResult:
    returncode: int
    wall_seconds: float
    stdout_path: pathlib.Path
    timed_out: bool
    cmd: list[str]
    dry_run: bool = False
    retries: int = 0
    # Populated when the subprocess output contains a Claude Code
    # session-cap message (the 5-hour account-level limit). The dict
    # carries the parsed reset time text and TZ, or None values when
    # the cap is detected but the reset hint couldn't be parsed.
    session_cap: dict | None = None


def _dry_run_enabled() -> bool:
    return dry_run_enabled()


def _dry_run_mode() -> str | None:
    """Return the dry-run mode string when OMICAIRN_DRY_RUN is truthy.

    The mode is the raw env-var value, lower-cased and stripped. The
    only mode currently honoured beyond the default placeholder-writer
    is ``"render"``, which additionally writes the rendered prompt to
    ``<stage_dir>/_rendered_prompt.md`` for prompt-wiring smoke tests.
    Returns ``None`` when dry-run is off.
    """
    if not _dry_run_enabled():
        return None
    return os.environ.get(ENV_DRY_RUN, "").strip().lower()


_RENDER_PROMPT_FILENAME: str = "_rendered_prompt.md"
_RENDER_CHECK_FILENAME: str = "_render_check.json"
# A surviving "{{token}}" marker after substitution is a prompt-wiring
# bug worth surfacing. We match the literal Jinja-style "{{...}}"
# delimiter that the OmiCairn prompt templates use.
_UNRESOLVED_PLACEHOLDER_PATTERN: re.Pattern[str] = re.compile(
    r"\{\{[^{}\n]{1,200}\}\}"
)
_UNRESOLVED_EXAMPLES_MAX: int = 10


def _write_render_check(
    stage_dir: pathlib.Path,
    prompt: str,
    phase_name: str,
) -> None:
    """Write _rendered_prompt.md and _render_check.json into stage_dir.

    Both files are diagnostic-only — they sit alongside the contract
    artefacts that ``_dry_run_write_contract`` writes. The render check
    summarises prompt-length and any surviving ``{{token}}`` markers,
    which would indicate a prompt-template wiring bug.
    """
    stage_dir.mkdir(parents=True, exist_ok=True)
    (stage_dir / _RENDER_PROMPT_FILENAME).write_text(prompt)
    matches = _UNRESOLVED_PLACEHOLDER_PATTERN.findall(prompt)
    # Preserve order, de-dupe.
    seen: set[str] = set()
    examples: list[str] = []
    for m in matches:
        if m in seen:
            continue
        seen.add(m)
        examples.append(m)
        if len(examples) >= _UNRESOLVED_EXAMPLES_MAX:
            break
    payload = {
        "phase": phase_name,
        "prompt_chars": len(prompt),
        "unresolved_placeholder_count": len(matches),
        "unresolved_examples": examples,
    }
    (stage_dir / _RENDER_CHECK_FILENAME).write_text(
        json.dumps(payload, indent=2) + "\n"
    )


def _dry_run_forced_verdict() -> str | None:
    v = os.environ.get(ENV_DRY_VERDICT, "").strip()
    return v or None


def _placeholder_for(filename: str, stage_dir: pathlib.Path, phase_name: str) -> str:
    """Return placeholder content for a known stage artifact filename.

    Unknown filenames fall back to a generic Markdown stub so
    contracts.verify still sees a non-trivial file (≥32 bytes).
    """
    base = filename.rsplit("/", 1)[-1]
    stem = base.rsplit(".", 1)[0]

    if filename == DONE_FLAG:
        return "1\n"
    if filename == SUMMARY_FILE:
        return (
            f"# {phase_name} stage summary (dry-run)\n\n"
            f"This is a placeholder summary produced by OmiCairn dry-run mode. "
            f"No real work was performed.\n"
        )
    if filename.endswith(RESULTS_JSON):
        return json.dumps(
            {
                "primary_model": {"auc": 0.82, "ci_95": [0.70, 0.91]},
                "comparisons": {
                    "delta_primary_vs_null": {
                        "delta": 0.30,
                        "ci_95": [0.15, 0.45],
                    }
                },
                "_dry_run": True,
            },
            indent=2,
        ) + "\n"
    if filename.endswith(ANALYSIS_MD):
        return (
            "# Analysis (dry-run)\n\n"
            "Primary model AUC=0.82 CI [0.70, 0.91].\n"
            "Primary vs null ΔAUC=0.30 CI [0.15, 0.45].\n"
        )
    if filename.endswith(PREREGISTRATION_FILE):
        return json.dumps(
            {
                "primary_hypothesis": "dry-run stub",
                "locked_at": "dry-run",
            },
            indent=2,
        ) + "\n"
    if filename == VERDICT_FILE:
        # The verdict-emitting LLM phase is the analysis REVIEW, the
        # terminal phase: it defaults to DONE so a happy-path dry-run
        # terminates cleanly. OMICAIRN_DRY_VERDICT overrides.
        forced = _dry_run_forced_verdict()
        decision = forced or Decision.DONE
        return json.dumps(
            {
                VerdictKey.DECISION: decision,
                VerdictKey.REASON: f"dry-run default verdict {decision!r}",
                VerdictKey.EVIDENCE: [],
            },
            indent=2,
        ) + "\n"
    if filename == FINDINGS_FILE:
        return "# Findings (dry-run)\n\nNo real findings produced.\n"
    if filename.endswith("legends.md"):
        return "# Figure legends (dry-run)\n\nFig. 1 — placeholder.\n"
    if filename.endswith("hypotheses.md") or filename.endswith("experiment_plan.md"):
        # Plan-stage placeholders must include a `## Power` section so
        # the W2 contract-enforcement check in cross_check.check_execute
        # sees a well-formed plan sibling instead of routing to
        # retry_execute on every dry-run.
        return (
            f"# {stem} (dry-run)\n\n"
            "H1: Placeholder hypothesis for OmiCairn dry-run mode.\n\n"
            "## Power\n"
            "Dry-run placeholder: minimum detectable effect at 80% power "
            "and α=0.05 would be computed from data_profile.md in a real "
            "run.\n"
        )
    if filename.endswith(".json"):
        return json.dumps({"stage": phase_name, "dry_run": True}, indent=2) + "\n"
    if filename.endswith(".md"):
        return (
            f"# {stem} (dry-run)\n\n"
            f"Placeholder Markdown for `{filename}` produced by OmiCairn dry-run "
            f"mode. Real content will be produced by the {phase_name} phase "
            f"when OMICAIRN_DRY_RUN is not set.\n"
        )
    # Unknown extension: best-effort text stub.
    return f"# {filename} (dry-run placeholder for {phase_name})\n"


def _dry_run_write_contract(
    stage_dir: pathlib.Path,
    phase_name: str,
    log_path: pathlib.Path,
) -> ClaudeResult:
    """Populate the stage directory with placeholder contract files."""
    phase = phases_mod.get(phase_name)
    stage_dir.mkdir(parents=True, exist_ok=True)
    for rel in phase.required_outputs:
        path = stage_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_placeholder_for(rel, stage_dir, phase_name))

    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        f"# OmiCairn dry-run for phase {phase_name}\n"
        f"# stage_dir: {stage_dir}\n"
        f"# wrote {len(phase.required_outputs)} placeholder files\n"
    )
    return ClaudeResult(
        returncode=0,
        wall_seconds=0.0,
        stdout_path=log_path,
        timed_out=False,
        cmd=["<dry-run>", phase_name],
        dry_run=True,
    )


def run(
    prompt: str,
    workspace_root: pathlib.Path,
    data_dir: pathlib.Path,
    allowed_tools: Sequence[str],
    model: str,
    timeout_seconds: int,
    log_path: pathlib.Path,
    append_system_prompt: str | None = None,
    max_budget_usd: float | None = None,
    permission_mode: str = "bypassPermissions",
    # New in the flat-stage model: the orchestrator passes in the
    # stage dir so dry-run mode can synthesize contract files directly.
    stage_dir: pathlib.Path | None = None,
    phase_name: str | None = None,
    # Best-effort bounded invocations pass a positive value
    # here to FORCE the Popen + process-group teardown watchdog even when
    # the global OMICAIRN_INACTIVITY_TIMEOUT_S is unset. Without this, the
    # watchdog-OFF path uses bare subprocess.run, whose timeout teardown
    # SIGKILLs only the direct child — leaving the `claude -p` agent's
    # MCP-server grandchildren un-reaped, which can keep the agent blocked
    # in teardown and pin a concurrency slot for the full wall budget
    # (observed on a d7 done-run tail invocation, audit 2026-06-13). None
    # (the default) preserves the env-driven behaviour for normal stages.
    inactivity_timeout_override: float | None = None,
    # GPU/multimodal EXECUTE inactivity-timeout FLOOR. The orchestrator passes
    # GPU_INACTIVITY_FLOOR_SECONDS here for confidently GPU-heavy/multimodal
    # EXECUTE phases so a slow-but-live per-shard GPU job (quiet >45min between
    # heartbeat touches) isn't false-killed (d3 wm-dynamics-decouple incident).
    # Applied as max(configured, floor) and ONLY to an already-active watchdog;
    # an explicit override or a disabled (0) watchdog is left untouched. None
    # (the default) preserves the env-driven behaviour for non-GPU stages.
    inactivity_timeout_floor_s: float | None = None,
    # DYNAMIC stage-time-limit mode (see constants.stage_time_limit_mode). When
    # True, the orchestrator has resolved this (execute-class) stage to DYNAMIC
    # mode: the wall becomes a LIVENESS-GATED SOFT CEILING (a progressing turn
    # past `timeout_seconds` is NOT truncated — the kill defers to the
    # inactivity/hang predicate) with `hard_ceiling_seconds` as the one
    # absolute backstop. Default False = HARD mode: the wall is an
    # unconditional kill (byte-for-byte the historical behaviour). Threaded
    # straight through to _run_with_watchdogs; non-execute phases and every
    # legacy/tail caller leave both at their defaults.
    wall_kill_requires_stall: bool = False,
    hard_ceiling_seconds: float | None = None,
) -> ClaudeResult:
    # Dry-run short-circuit: no subprocess, no tokens.
    #
    # OMICAIRN_DRY_RUN=render adds a prompt-render smoke step that writes
    # the fully-rendered prompt to <stage_dir>/_rendered_prompt.md and
    # a small <stage_dir>/_render_check.json before falling through to
    # the standard placeholder-writer. This catches unresolved
    # {{token}} markers (a prompt-template wiring bug) without
    # spending Claude tokens. All other truthy values (1, true, yes,
    # ...) preserve the original placeholder-only behaviour.
    if _dry_run_enabled() and stage_dir is not None and phase_name is not None:
        if _dry_run_mode() == _DRY_RUN_MODE_RENDER:
            _write_render_check(stage_dir, prompt, phase_name)
        return _dry_run_write_contract(stage_dir, phase_name, log_path)

    log_path.parent.mkdir(parents=True, exist_ok=True)

    # The two UNBOUNDED arguments — the phase prompt and the appended system
    # prompt — are kept OFF argv so a verbose phase can never overflow
    # ARG_MAX and die at execve with `OSError: Argument list too long`
    # (audit 2026-06-27). The prompt is streamed to `claude --print` on
    # STDIN (no positional prompt arg); the system prompt is written to a
    # sidecar file passed via --append-system-prompt-file. Both sidecar
    # files are created ONCE here and reused across all retry attempts, then
    # cleaned up in the finally below. They live in the stage dir when known
    # (inspectable) and fall back to a tempfile otherwise.
    _scratch_dir = stage_dir
    if _scratch_dir is not None:
        _scratch_dir.mkdir(parents=True, exist_ok=True)
        prompt_path = _scratch_dir / PROMPT_STDIN_FILENAME
    else:
        fd, _tmp = tempfile.mkstemp(prefix="omicairn_prompt_", suffix=".txt")
        os.close(fd)
        prompt_path = pathlib.Path(_tmp)
    prompt_path.write_text(prompt, encoding="utf-8")

    append_sp_path: pathlib.Path | None = None
    if append_system_prompt:
        if _scratch_dir is not None:
            append_sp_path = _scratch_dir / APPEND_SYSTEM_PROMPT_FILENAME
        else:
            fd, _tmp = tempfile.mkstemp(prefix="omicairn_sysprompt_", suffix=".txt")
            os.close(fd)
            append_sp_path = pathlib.Path(_tmp)
        append_sp_path.write_text(append_system_prompt, encoding="utf-8")

    cmd: list[str] = [
        "claude",
        CLAUDE_FLAG_PRINT,  # prompt arrives on stdin, NOT as a positional arg
        "--add-dir", str(workspace_root),
        "--permission-mode", permission_mode,
        "--model", normalize_model_id(model),
        "--output-format", "text",
        "--allowedTools", *allowed_tools,
    ]
    try:
        data_dir.relative_to(workspace_root)
    except ValueError:
        cmd[cmd.index("--add-dir") + 2 : cmd.index("--add-dir") + 2] = ["--add-dir", str(data_dir)]

    if append_sp_path is not None:
        cmd += [CLAUDE_FLAG_APPEND_SYSTEM_PROMPT_FILE, str(append_sp_path)]
    if max_budget_usd is not None:
        cmd += ["--max-budget-usd", str(max_budget_usd)]

    # Backstop guard: with the prompt on stdin and the system prompt in a
    # file the assembled argv is tiny, but if a NEW large arg ever creeps on
    # we raise a clear, actionable error here rather than dying with Errno 7.
    _assert_argv_within_limit(cmd)

    env = os.environ.copy()
    env.setdefault(ENV_DATA_DIR, str(data_dir))
    # Merge .env keys into the subprocess environment. Existing keys
    # (e.g. CI-provided ANTHROPIC_API_KEY) are NOT overwritten — .env
    # is a fallback. The audit line is written inside the retry loop
    # below, alongside the cmd header, so the log_path truncation in
    # mode="w" doesn't wipe it.
    dotenv_audit = _load_dotenv_into(env, pathlib.Path(workspace_root))

    max_retry_count = _max_retries()
    retry_delay = _DEFAULT_RETRY_BASE_DELAY
    total_t0 = time.time()

    # Clean up the prompt / append-system-prompt sidecar files once the run
    # (incl. all retries) is done, regardless of how we exit. Tempfile-backed
    # sidecars (no stage_dir) MUST be removed; stage-dir sidecars are
    # inspectable artifacts we also tidy up on the way out (best-effort —
    # never let cleanup mask a real failure).
    def _cleanup_sidecars() -> None:
        for p in (prompt_path, append_sp_path):
            if p is None:
                continue
            try:
                p.unlink()
            except OSError:
                pass

    # Retry loop: attempt 0 is the initial try; attempts 1..max_retry_count
    # are retries fired only when the failure looks transient (rate limit,
    # server error, network issue). Non-transient failures, timeouts, and
    # CLI-missing errors return immediately.
    rc = 1
    timed_out = False
    try:
        for attempt in range(1 + max_retry_count):
            mode = "w" if attempt == 0 else "a"
            with log_path.open(mode) as lf:
                if attempt == 0:
                    lf.write(
                        f"# OmiCairn phase launch\n# cwd: {workspace_root}\n"
                        f"# ts: {time.time()}\n"
                    )
                    if stage_dir is not None:
                        lf.write(f"# stage_dir: {stage_dir}\n")
                    if dotenv_audit is not None:
                        dotenv_path, loaded_keys = dotenv_audit
                        lf.write(
                            f"# dotenv: loaded {len(loaded_keys)} key(s) from "
                            f"{dotenv_path} ({loaded_keys})\n"
                        )
                    else:
                        lf.write(
                            "# dotenv: no .env found on candidate paths "
                            "(workspace/repo-root/cwd/$OMICAIRN_DOTENV)\n"
                        )
                    lf.write("# cmd:\n")
                    for i, part in enumerate(cmd):
                        lf.write(f"#   [{i}] {part}\n")
                    lf.write(_LOG_OUTPUT_SENTINEL + "\n")
                else:
                    lf.write(
                        f"\n# --- retry attempt {attempt + 1}/"
                        f"{1 + max_retry_count} ---\n"
                    )
                lf.flush()

                timed_out = False
                try:
                    rc, timed_out = _run_with_watchdogs(
                        cmd,
                        cwd=str(workspace_root),
                        env=env,
                        log_file=lf,
                        log_path=log_path,
                        wall_timeout_s=timeout_seconds,
                        inactivity_timeout_s=_inactivity_timeout_s(
                            inactivity_timeout_override,
                            floor_s=inactivity_timeout_floor_s,
                        ),
                        heartbeat_path=_heartbeat_path(
                            stage_dir, pathlib.Path(workspace_root)
                        ),
                        stage_dir=stage_dir,
                        stdin_path=prompt_path,
                        wall_kill_requires_stall=wall_kill_requires_stall,
                        hard_ceiling_s=hard_ceiling_seconds,
                    )
                except FileNotFoundError as e:
                    lf.write(f"\n# ERROR: claude CLI not found: {e}\n")
                    rc = -2

            total_wall = time.time() - total_t0

            # Success or non-retryable failure → return immediately.
            if rc == 0 or timed_out or rc == -2:
                cap = None if (timed_out or rc == -2) else detect_session_cap(log_path)
                return ClaudeResult(
                    returncode=rc,
                    wall_seconds=total_wall,
                    stdout_path=log_path,
                    timed_out=timed_out,
                    cmd=cmd,
                    retries=attempt,
                    session_cap=cap,
                )

            # Session-cap check FIRST — a 5-hour account cap masquerading
            # as a transient (e.g. rc!=0 with the cap message in the log)
            # must not enter the regular transient-error retry loop. We have
            # two paths for it: auto-resume (parse the reset hint and sleep
            # until reset+buffer, then retry the SAME stage in-place) or
            # the legacy SESSION_LIMIT exit (used when auto-resume is off
            # or the reset hint can't be parsed).
            cap = detect_session_cap(log_path)
            if cap is not None:
                if _auto_resume_enabled():
                    reset_dt = _parse_session_reset(cap)
                    if reset_dt is not None:
                        now = dt_module.datetime.now(dt_module.timezone.utc)
                        sleep_s = max(
                            _AUTO_RESUME_MIN_WAIT_S,
                            int((reset_dt - now).total_seconds()) + _AUTO_RESUME_BUFFER_S,
                        )
                        if sleep_s <= _AUTO_RESUME_MAX_WAIT_S:
                            with log_path.open("a") as lf:
                                lf.write(
                                    "\n# session cap reached; auto-resume in "
                                    f"{sleep_s}s (reset hint: {cap.get('reset_text')!r} "
                                    f"{cap.get('reset_tz')!r}; reset_dt={reset_dt.isoformat()})\n"
                                )
                            time.sleep(sleep_s)
                            with log_path.open("a") as lf:
                                lf.write(
                                    f"# auto-resume woke at {dt_module.datetime.now(dt_module.timezone.utc).isoformat()}; "
                                    "retrying stage\n"
                                )
                            # Retry by re-entering the for-loop iteration. The
                            # loop counter advances; treat this as a regular
                            # transient retry (same accounting). If
                            # max_retry_count is exceeded we fall through to
                            # the SESSION_LIMIT return below.
                            if attempt < max_retry_count:
                                continue
                            # Else fall through and return SESSION_LIMIT.
                        else:
                            with log_path.open("a") as lf:
                                lf.write(
                                    f"\n# session cap reset is {sleep_s}s away "
                                    f"(> {_AUTO_RESUME_MAX_WAIT_S}s ceiling); "
                                    "skipping auto-resume\n"
                                )
                return ClaudeResult(
                    returncode=rc,
                    wall_seconds=total_wall,
                    stdout_path=log_path,
                    timed_out=False,
                    cmd=cmd,
                    retries=attempt,
                    session_cap=cap,
                )

            # Transient failure with retries remaining → sleep + retry.
            if attempt < max_retry_count and _is_transient_failure(log_path, rc):
                with log_path.open("a") as lf:
                    lf.write(
                        f"# transient failure detected (rc={rc}), "
                        f"sleeping {retry_delay:.0f}s before retry\n"
                    )
                time.sleep(retry_delay)
                retry_delay *= _RETRY_BACKOFF_FACTOR
                continue

            # Non-transient failure or retries exhausted.
            return ClaudeResult(
                returncode=rc,
                wall_seconds=total_wall,
                stdout_path=log_path,
                timed_out=False,
                cmd=cmd,
                retries=attempt,
                session_cap=detect_session_cap(log_path),
            )

        # Unreachable — the loop always returns — but satisfy the type checker.
        return ClaudeResult(
            returncode=rc,
            wall_seconds=time.time() - total_t0,
            stdout_path=log_path,
            timed_out=timed_out,
            cmd=cmd,
            retries=max_retry_count,
        )
    finally:
        _cleanup_sidecars()
