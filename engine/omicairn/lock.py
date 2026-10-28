"""PID-tagged file locks for OmiCairn concurrent-run prevention.

Single PC, single user — a heavyweight lock library would be overkill.
We use a small PID-file pattern: write the holder's PID into a lock
file, and a process that wants the lock checks (a) the file's age and
(b) whether the PID is still alive before either acquiring or refusing.

Used by:
  - research_cases/<slug>/data/.fetch.lock — guards concurrent
    fetch_data.py invocations against the same case.
  - workspaces/<slug>-<ts>/.run.lock — guards concurrent
    `omicairn run <ws>` invocations against the same workspace.

Intentionally NOT a full filelock/portalocker replacement: this lives
on a single user's machine and the failure mode it prevents (two
processes fighting over the same file in parallel) is best caught with
a ~50 LOC PID-file pattern that any human can read at 3am during an
incident.
"""
from __future__ import annotations

import dataclasses
import os
import pathlib
import time

# A lock file older than this AND held by a dead PID is reclaimable
# without further checks.
STALE_AGE_SECONDS = 60 * 60  # 1 hour


@dataclasses.dataclass(frozen=True)
class LockState:
    """Snapshot of a lock's state, returned by `inspect`."""

    path: pathlib.Path
    holder_pid: int | None  # None when the file is missing or unreadable
    age_seconds: float | None  # None when the file is missing
    holder_alive: bool  # False when no holder OR holder is dead
    is_stale: bool  # True iff age > STALE_AGE_SECONDS AND holder dead


def _read_pid(lock_path: pathlib.Path) -> int | None:
    try:
        text = lock_path.read_text().strip()
    except (OSError, UnicodeDecodeError):
        return None
    try:
        pid = int(text)
    except ValueError:
        return None
    return pid if pid > 0 else None


def _pid_alive(pid: int) -> bool:
    """True iff a process with this PID exists on this host.

    Uses signal 0 — POSIX standard for "check process existence
    without sending a signal". On macOS / Linux this raises
    ProcessLookupError when the PID is gone, PermissionError when
    the PID is alive but owned by another user (treat as alive — we
    don't want to steal a peer's lock).
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def inspect(lock_path: pathlib.Path) -> LockState:
    """Return the current state of a lock without modifying it."""
    if not lock_path.exists():
        return LockState(
            path=lock_path,
            holder_pid=None,
            age_seconds=None,
            holder_alive=False,
            is_stale=False,
        )
    pid = _read_pid(lock_path)
    age = max(0.0, time.time() - lock_path.stat().st_mtime)
    alive = _pid_alive(pid) if pid is not None else False
    is_stale = (not alive) and age > STALE_AGE_SECONDS
    return LockState(
        path=lock_path,
        holder_pid=pid,
        age_seconds=age,
        holder_alive=alive,
        is_stale=is_stale,
    )


def acquire(lock_path: pathlib.Path) -> bool:
    """Try to acquire the lock. Return True on success.

    Acquires when:
      - the file does not exist; OR
      - the file exists but the holder PID is dead AND age > STALE_AGE_SECONDS
        ; OR
      - the file is unreadable / has no parseable PID (treat as corrupt
        and reclaim).

    Refuses when a peer's PID is alive, even if the lock is old. The
    caller is expected to surface "lock held by live PID N" to the
    user and stop.

    The acquired lock writes "<pid>\\n" so peers can read the holder.
    """
    state = inspect(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if state.holder_pid is None:
        # File missing or unreadable. Safe to reclaim.
        return _write_pid(lock_path)
    if state.holder_alive:
        return False
    if not state.is_stale:
        # Holder dead but lock is recent (< 1 h). Still refuse —
        # someone may have just crashed and is restarting; their
        # next attempt would race ours.
        return False
    return _write_pid(lock_path)


def _write_pid(lock_path: pathlib.Path) -> bool:
    try:
        lock_path.write_text(f"{os.getpid()}\n")
    except OSError:
        return False
    return True


def release(lock_path: pathlib.Path) -> None:
    """Release the lock if held by us. No-op if it's gone or held by a peer.

    Refuses to delete a peer's lock — never `rm -f` another process's
    lock without confirming its PID is dead.
    """
    state = inspect(lock_path)
    if state.holder_pid is None:
        return
    if state.holder_pid != os.getpid():
        return
    try:
        lock_path.unlink()
    except OSError:
        pass


def force_clear(lock_path: pathlib.Path) -> bool:
    """Forcibly remove a stale lock. Refuses if the holder is alive.

    Intended for a `omicairn locks --clear-stale` subcommand or manual
    invocation on stuck-but-dead-holder lock files.

    Ergonomic guard: callers frequently pass the WORKSPACE DIRECTORY by
    mistake instead of the ``.run.lock`` file inside it (a silent no-op
    that leaves the stale lock in place and the next `omicairn run` refusing
    to start). If given an existing directory, resolve to its
    ``.run.lock`` so the common mistake "just works".
    """
    lock_path = pathlib.Path(lock_path)
    if lock_path.is_dir():
        lock_path = lock_path / ".run.lock"
    state = inspect(lock_path)
    if state.holder_alive:
        return False
    try:
        lock_path.unlink(missing_ok=True)
    except OSError:
        return False
    return True
