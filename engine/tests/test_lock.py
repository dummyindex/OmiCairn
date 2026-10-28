"""Tests for omicairn.lock — PID-tagged file locks."""
from __future__ import annotations

import os
import pathlib
import time

import pytest

from omicairn import lock


def test_inspect_missing_returns_no_holder(tmp_path):
    state = lock.inspect(tmp_path / "missing.lock")
    assert state.holder_pid is None
    assert state.age_seconds is None
    assert state.holder_alive is False
    assert state.is_stale is False


def test_acquire_creates_lock_with_our_pid(tmp_path):
    p = tmp_path / "x.lock"
    assert lock.acquire(p) is True
    assert p.exists()
    assert int(p.read_text().strip()) == os.getpid()


def test_acquire_refuses_when_held_by_live_peer(tmp_path):
    p = tmp_path / "x.lock"
    # Use our own PID — guaranteed alive.
    p.write_text(f"{os.getpid()}\n")
    # Inspect first: our own process IS alive.
    state = lock.inspect(p)
    assert state.holder_alive is True
    # Calling acquire should NOT clobber the existing lock owned by
    # us — but the current implementation would refuse because
    # holder_alive=True. (Re-acquiring our own lock is the caller's
    # problem; the lock is only meant to be held briefly.)
    assert lock.acquire(p) is False


def test_acquire_reclaims_stale_dead_holder(tmp_path, monkeypatch):
    """A lock whose PID is dead AND is older than STALE_AGE_SECONDS
    is reclaimable."""
    p = tmp_path / "x.lock"
    # Find a clearly-dead PID (extremely high number unlikely to exist).
    dead_pid = 999999
    p.write_text(f"{dead_pid}\n")
    # Backdate the file to look stale.
    old_ts = time.time() - (lock.STALE_AGE_SECONDS + 60)
    os.utime(p, (old_ts, old_ts))
    assert lock.acquire(p) is True
    assert int(p.read_text().strip()) == os.getpid()


def test_acquire_refuses_dead_holder_within_stale_window(tmp_path):
    """Holder dead but lock < 1 h old: STILL refuse. Per the protocol,
    we don't want to race a process that just crashed and is about
    to restart."""
    p = tmp_path / "x.lock"
    p.write_text("999999\n")  # dead PID, fresh mtime (just written)
    state = lock.inspect(p)
    assert state.holder_alive is False
    assert state.is_stale is False
    assert lock.acquire(p) is False


def test_acquire_reclaims_corrupt_lock(tmp_path):
    """A lock file with non-numeric content has no PID we can check
    liveness for — reclaim it."""
    p = tmp_path / "x.lock"
    p.write_text("not a pid\n")
    assert lock.acquire(p) is True
    assert int(p.read_text().strip()) == os.getpid()


def test_release_removes_lock_we_hold(tmp_path):
    p = tmp_path / "x.lock"
    lock.acquire(p)
    lock.release(p)
    assert not p.exists()


def test_release_refuses_to_remove_peers_lock(tmp_path):
    """If another PID holds the lock, release MUST be a no-op —
    the 'never rm -f a peer's lock' rule."""
    p = tmp_path / "x.lock"
    # Write a PID that is NOT ours (but pick one that's alive — our parent).
    parent_pid = os.getppid()
    p.write_text(f"{parent_pid}\n")
    lock.release(p)  # should be no-op
    assert p.exists()


def test_force_clear_removes_dead_lock(tmp_path):
    p = tmp_path / "x.lock"
    p.write_text("999999\n")
    assert lock.force_clear(p) is True
    assert not p.exists()


def test_force_clear_refuses_to_remove_live_lock(tmp_path):
    p = tmp_path / "x.lock"
    p.write_text(f"{os.getpid()}\n")
    assert lock.force_clear(p) is False
    assert p.exists()


def test_force_clear_accepts_workspace_dir_not_just_lock_file(tmp_path):
    """Ergonomic guard (2026-06-10): callers often pass the WORKSPACE DIR
    instead of <dir>/.run.lock — which silently no-op'd, leaving the stale
    lock and blocking the next `omicairn run`. Given a dir, force_clear must
    resolve to its .run.lock and remove a dead one."""
    ws = tmp_path / "ws-123"
    ws.mkdir()
    (ws / ".run.lock").write_text("999999\n")  # dead PID
    assert lock.force_clear(ws) is True
    assert not (ws / ".run.lock").exists()


def test_inspect_marks_old_dead_holder_as_stale(tmp_path):
    p = tmp_path / "x.lock"
    p.write_text("999999\n")
    old_ts = time.time() - (lock.STALE_AGE_SECONDS + 60)
    os.utime(p, (old_ts, old_ts))
    state = lock.inspect(p)
    assert state.holder_alive is False
    assert state.is_stale is True


def test_acquire_creates_parent_dirs(tmp_path):
    """acquire() must mkdir -p the parent dir; tests/fixtures often
    use a nested path that doesn't exist yet."""
    p = tmp_path / "deeply" / "nested" / "x.lock"
    assert lock.acquire(p) is True
    assert p.exists()
