"""Watchdog wall-cap gating tests for DYNAMIC vs HARD stage time-limit mode.

These drive `claude_runner._watchdog_poll_loop` directly with a fake process, a
fake clock, and a monkeypatched file-liveness probe, so the wall/inactivity
kill logic is covered WITHOUT spawning a real `claude -p`. They pin the
behavioural contract of the fix (owner directive 2026-07-01):

  * DYNAMIC mode extends a PROGRESSING job past the (old, tiny) wall cap and
    emits an EXECUTE_WALL_SOFT_EXTENDED beacon — the job is NOT killed.
  * HARD mode (wall_kill_requires_stall=False, the default) enforces the wall
    as an unconditional kill even while files advance — byte-for-byte legacy.
  * A genuinely-STALLED job past the wall is STILL killed in DYNAMIC mode (the
    kill defers to the same inactivity/last_growth predicate) — the critical
    no-regression case.
  * The hard safety ceiling ALWAYS kills, even while progressing.

Progress is simulated via the stage-dir file mtime signal (the one that
protects a pure-local EXECUTE), monkeypatched so we don't touch a real clock.
"""
from __future__ import annotations

import json
import signal as signal_mod

import pytest

from omicairn import claude_runner
from omicairn.constants import EventKind, EVENTS_LOG


class _FakeProc:
    """A subprocess.Popen stand-in: never exits on its own (every wait()
    times out) so the poll loop's own kill logic decides the outcome.
    Records whether it was killed."""

    def __init__(self):
        self.pid = 424242
        self.killed = False

    def wait(self, timeout=None):
        # Raise the same exception the real Popen.wait raises on timeout so the
        # loop treats us as "still running" every poll.
        import subprocess
        raise subprocess.TimeoutExpired(cmd="claude", timeout=timeout)


def _clock(monkeypatch, step: float = 100.0):
    """Monkeypatch claude_runner.time.time to advance by `step` on every call
    after an initial 0.0. The poll loop reads time.time() a few times per
    iteration; a monotonic per-call advance is enough to march elapsed past a
    tiny wall within a couple of polls while keeping the math predictable."""
    state = {"t": 0.0}

    def _now():
        v = state["t"]
        state["t"] += step
        return v

    monkeypatch.setattr(claude_runner.time, "time", _now)
    return state


def _run_loop(monkeypatch, *, stage_dir, log_path, wall_timeout_s,
              inactivity_timeout_s, wall_kill_requires_stall, hard_ceiling_s=None,
              progressing: bool, max_polls: int = 12):
    """Drive _watchdog_poll_loop with a fake proc + fake file-liveness.

    `progressing=True` → the stage-dir newest-file mtime advances every poll
    (job is alive); `False` → it stays fixed (job is stalled). A guard raises
    if the loop runs past max_polls without terminating (so a bug can't hang
    the test)."""
    proc = _FakeProc()

    # Kill just flips a flag + returns (don't touch a real pgid).
    def _fake_kill():
        proc.killed = True
    # Patch the closure-local _kill_tree by patching os.killpg to a no-op and
    # proc.wait to succeed after a kill; simplest is to patch os.getpgid to
    # raise so _kill_tree's SIGTERM path is a harmless no-op, then detect the
    # kill via the return value (rc,timed_out)==(-1,True). We instead track via
    # a monkeypatched os.killpg recording the call.
    killed = {"flag": False}

    def _rec_killpg(pgid, sig):
        killed["flag"] = True

    monkeypatch.setattr(claude_runner.os, "killpg", _rec_killpg)
    monkeypatch.setattr(claude_runner.os, "getpgid", lambda pid: pid)
    # proc.wait(timeout=10) inside _kill_tree must return promptly; our fake
    # always raises TimeoutExpired, which _kill_tree catches then SIGKILLs
    # (also recorded). Fine — killed flag is set either way.

    # File-liveness: advance mtime each poll when progressing, else fixed.
    mtime_state = {"v": 1000.0}

    def _fake_newest(_dir):
        if progressing:
            mtime_state["v"] += 1.0
        return mtime_state["v"]

    monkeypatch.setattr(claude_runner, "_newest_file_mtime", _fake_newest)
    _clock(monkeypatch, step=100.0)

    # Cap iterations: wrap proc.wait to raise StopIteration after max_polls so a
    # non-terminating loop fails loudly instead of hanging.
    calls = {"n": 0}
    orig_wait = proc.wait

    def _guarded_wait(timeout=None):
        calls["n"] += 1
        if calls["n"] > max_polls:
            raise AssertionError("poll loop did not terminate within max_polls")
        return orig_wait(timeout=timeout)

    proc.wait = _guarded_wait

    with log_path.open("w") as lf:
        rc, timed_out = claude_runner._watchdog_poll_loop(
            proc, cmd=["claude"], log_file=lf, log_path=log_path,
            wall_timeout_s=wall_timeout_s,
            inactivity_timeout_s=inactivity_timeout_s, poll_s=0.001,
            heartbeat_path=None, stage_dir=stage_dir, signal=signal_mod,
            wall_kill_requires_stall=wall_kill_requires_stall,
            hard_ceiling_s=hard_ceiling_s,
        )
    return rc, timed_out, killed["flag"]


def _read_events(ws_root):
    p = ws_root / EVENTS_LOG
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def _mk_stage(tmp_path):
    ws = tmp_path
    stage_dir = ws / "002_execute"
    stage_dir.mkdir()
    log_path = stage_dir / "run.log"
    return ws, stage_dir, log_path


# ---------------------------------------------------------------------------
# DYNAMIC: progressing past wall → soft-extend, NOT killed
# ---------------------------------------------------------------------------

def test_dynamic_progressing_job_not_killed_past_wall(tmp_path, monkeypatch):
    ws, stage_dir, log_path = _mk_stage(tmp_path)
    # Tiny wall (1s) is crossed on the first poll (clock steps 100s), but the
    # job is progressing (file mtime advances) → soft-extend, keep polling,
    # then the guard stops the loop → this asserts NO kill occurred.
    with pytest.raises(AssertionError, match="did not terminate"):
        _run_loop(
            monkeypatch, stage_dir=stage_dir, log_path=log_path,
            wall_timeout_s=1, inactivity_timeout_s=100000,
            wall_kill_requires_stall=True, progressing=True, max_polls=5,
        )
    # A soft-extension beacon must have been emitted while it ran.
    kinds = [e.get("kind") for e in _read_events(ws)]
    assert EventKind.EXECUTE_WALL_SOFT_EXTENDED in kinds


# ---------------------------------------------------------------------------
# HARD: progressing past wall → still killed (unconditional)
# ---------------------------------------------------------------------------

def test_hard_mode_kills_at_wall_even_while_progressing(tmp_path, monkeypatch):
    ws, stage_dir, log_path = _mk_stage(tmp_path)
    rc, timed_out, killed = _run_loop(
        monkeypatch, stage_dir=stage_dir, log_path=log_path,
        wall_timeout_s=1, inactivity_timeout_s=100000,
        wall_kill_requires_stall=False, progressing=True,
    )
    assert (rc, timed_out) == (-1, True)
    assert killed is True
    # No soft-extension in HARD mode.
    kinds = [e.get("kind") for e in _read_events(ws)]
    assert EventKind.EXECUTE_WALL_SOFT_EXTENDED not in kinds


# ---------------------------------------------------------------------------
# DYNAMIC: stalled past wall → STILL killed (no-regression, defers to inactivity)
# ---------------------------------------------------------------------------

def test_dynamic_stalled_job_past_wall_is_still_killed(tmp_path, monkeypatch):
    ws, stage_dir, log_path = _mk_stage(tmp_path)
    # Wall tiny AND inactivity tiny; job NOT progressing (fixed mtime, no log
    # growth). Past the wall with a stale last_growth → the inactivity predicate
    # fires and kills, exactly as a genuine hang would.
    rc, timed_out, killed = _run_loop(
        monkeypatch, stage_dir=stage_dir, log_path=log_path,
        wall_timeout_s=1, inactivity_timeout_s=1,
        wall_kill_requires_stall=True, progressing=False,
    )
    assert (rc, timed_out) == (-1, True)
    assert killed is True


# ---------------------------------------------------------------------------
# DYNAMIC: hard safety ceiling ALWAYS kills, even while progressing
# ---------------------------------------------------------------------------

def test_dynamic_hard_ceiling_kills_even_when_progressing(tmp_path, monkeypatch):
    ws, stage_dir, log_path = _mk_stage(tmp_path)
    # Progressing forever, wall generous, but hard ceiling tiny → killed once
    # elapsed crosses the ceiling. Inactivity huge so ONLY the ceiling can kill.
    rc, timed_out, killed = _run_loop(
        monkeypatch, stage_dir=stage_dir, log_path=log_path,
        wall_timeout_s=100000, inactivity_timeout_s=100000,
        wall_kill_requires_stall=True, hard_ceiling_s=1, progressing=True,
    )
    assert (rc, timed_out) == (-1, True)
    assert killed is True
    kinds = [e.get("kind") for e in _read_events(ws)]
    assert EventKind.EXECUTE_HARD_CEILING_KILL in kinds


# ---------------------------------------------------------------------------
# Back-compat: default args reproduce the historical unconditional wall kill
# ---------------------------------------------------------------------------

def test_default_args_are_hard_unconditional_wall(tmp_path, monkeypatch):
    """A caller that passes NEITHER new arg (the legacy signature) gets the
    exact historical behaviour: wall crossing kills regardless of progress."""
    ws, stage_dir, log_path = _mk_stage(tmp_path)
    proc = _FakeProc()
    killed = {"flag": False}
    monkeypatch.setattr(claude_runner.os, "killpg",
                        lambda pgid, sig: killed.__setitem__("flag", True))
    monkeypatch.setattr(claude_runner.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(claude_runner, "_newest_file_mtime",
                        lambda d: 1000.0 + 1.0)  # would-be progressing
    _clock(monkeypatch, step=100.0)
    with log_path.open("w") as lf:
        rc, timed_out = claude_runner._watchdog_poll_loop(
            proc, cmd=["claude"], log_file=lf, log_path=log_path,
            wall_timeout_s=1, inactivity_timeout_s=100000, poll_s=0.001,
            heartbeat_path=None, stage_dir=stage_dir, signal=signal_mod,
        )
    assert (rc, timed_out) == (-1, True)
    assert killed["flag"] is True


# ===========================================================================
# END-TO-END dispatch-path coverage: DYNAMIC must engage through
# _run_with_watchdogs even with the inactivity watchdog DISABLED (default).
#
# The tests above drive _watchdog_poll_loop DIRECTLY, which PRESUPPOSES the
# Popen/watchdog-ON path. That is exactly the gap that let DYNAMIC ship inert:
# the shipped default is OMICAIRN_INACTIVITY_TIMEOUT_S unset → inactivity_timeout_s
# resolves to 0 → _run_with_watchdogs takes the bare subprocess.run HARD-wall
# branch and never reaches the poll loop, so wall_kill_requires_stall /
# hard_ceiling_s are ignored and a progressing long GPU arm (the d7 LLaVA-NeXT
# held-out drop) is STILL truncated. These drive _run_with_watchdogs (the real
# dispatch seam) with inactivity_timeout_s=0 and assert DYNAMIC now forces the
# poll-loop path (round-1/round-3 blocker, audit 2026-07-01).
# ===========================================================================


def _fake_popen_factory(monkeypatch, *, exits_after_polls: int | None = None):
    """Monkeypatch subprocess.Popen inside claude_runner to return a fake proc.

    `exits_after_polls=None` → the proc NEVER exits on its own (every wait()
    times out) so the poll loop's own kill logic decides — the "long job"
    shape. An int → the proc "finishes" (wait returns 0) after that many polls.
    Returns a dict recording {"popen_called": bool, "proc": <the fake>}."""
    rec: dict = {"popen_called": False, "proc": None}

    class _FakePopen:
        def __init__(self, *a, **k):
            self.pid = 515151
            self._polls = 0
            rec["popen_called"] = True
            rec["proc"] = self

        def wait(self, timeout=None):
            import subprocess as _sp
            self._polls += 1
            if exits_after_polls is not None and self._polls >= exits_after_polls:
                return 0
            raise _sp.TimeoutExpired(cmd="claude", timeout=timeout)

    monkeypatch.setattr(claude_runner.subprocess, "Popen",
                        lambda *a, **k: _FakePopen(*a, **k))
    return rec


def _fail_if_subprocess_run(monkeypatch):
    """Make claude_runner.subprocess.run BLOW UP if called — proves the bare
    HARD-wall fast path was NOT taken (the whole point of the fix)."""
    def _boom(*a, **k):
        raise AssertionError(
            "subprocess.run (bare HARD-wall path) was taken — DYNAMIC mode "
            "failed to force the poll-loop path"
        )
    monkeypatch.setattr(claude_runner.subprocess, "run", _boom)


def _drive_run_with_watchdogs(monkeypatch, *, stage_dir, log_path,
                              wall_timeout_s, inactivity_timeout_s,
                              wall_kill_requires_stall, hard_ceiling_s,
                              progressing, max_polls=8):
    """Run _run_with_watchdogs with a faked Popen + faked clock/file-liveness,
    capping polls so a non-terminating loop fails loudly. Returns
    (rc, timed_out, killed_flag)."""
    _fail_if_subprocess_run(monkeypatch)
    rec = _fake_popen_factory(monkeypatch)

    killed = {"flag": False}
    monkeypatch.setattr(claude_runner.os, "killpg",
                        lambda pgid, sig: killed.__setitem__("flag", True))
    monkeypatch.setattr(claude_runner.os, "getpgid", lambda pid: pid)

    mtime_state = {"v": 1000.0}

    def _fake_newest(_dir):
        if progressing:
            mtime_state["v"] += 1.0
        return mtime_state["v"]

    monkeypatch.setattr(claude_runner, "_newest_file_mtime", _fake_newest)
    # CPU probe is observability only; stub so no /proc dependency.
    monkeypatch.setattr(claude_runner, "_tree_cpu_jiffies", lambda pid: None)
    _clock(monkeypatch, step=100.0)

    # Guard: cap poll iterations by wrapping the fake proc's wait AFTER Popen
    # is constructed. We wrap the class method via a counter closure below by
    # patching the proc post-hoc is awkward; instead cap via the clock/asserts
    # in the loop — but to be safe, wrap wait on first construction.
    orig_popen = claude_runner.subprocess.Popen

    calls = {"n": 0}

    class _CappedPopen:
        def __init__(self, *a, **k):
            self._inner = orig_popen(*a, **k)
            self.pid = self._inner.pid
            rec["proc"] = self

        def wait(self, timeout=None):
            calls["n"] += 1
            if calls["n"] > max_polls:
                raise AssertionError("poll loop did not terminate within max_polls")
            return self._inner.wait(timeout=timeout)

    monkeypatch.setattr(claude_runner.subprocess, "Popen",
                        lambda *a, **k: _CappedPopen(*a, **k))

    with log_path.open("w") as lf:
        rc, timed_out = claude_runner._run_with_watchdogs(
            ["claude"], cwd=str(stage_dir), env={}, log_file=lf,
            log_path=log_path, wall_timeout_s=wall_timeout_s,
            inactivity_timeout_s=inactivity_timeout_s, poll_s=0.001,
            heartbeat_path=None, stage_dir=stage_dir,
            wall_kill_requires_stall=wall_kill_requires_stall,
            hard_ceiling_s=hard_ceiling_s,
        )
    return rc, timed_out, killed["flag"], rec


def test_dynamic_forces_poll_loop_when_inactivity_disabled_and_progressing(
    tmp_path, monkeypatch
):
    """THE motivating case: DYNAMIC mode + inactivity_timeout_s=0 (the shipped
    default) + a PROGRESSING job past a tiny wall MUST NOT be truncated — the
    d7 LLaVA-NeXT full held-out scoring would now COMPLETE. Before the fix this
    took the bare subprocess.run HARD-wall branch and hard-killed at the wall."""
    ws, stage_dir, log_path = _mk_stage(tmp_path)
    # Progressing forever past a 1s wall; inactivity DISABLED (0). The fix
    # floors inactivity to the DYNAMIC-mode default so the poll loop runs and
    # the soft ceiling never kills a progressing job → loop runs to the guard.
    with pytest.raises(AssertionError, match="did not terminate"):
        _drive_run_with_watchdogs(
            monkeypatch, stage_dir=stage_dir, log_path=log_path,
            wall_timeout_s=1, inactivity_timeout_s=0,
            wall_kill_requires_stall=True, hard_ceiling_s=None,
            progressing=True, max_polls=5,
        )
    # A soft-extension beacon proves the wall was demoted to a soft ceiling.
    kinds = [e.get("kind") for e in _read_events(ws)]
    assert EventKind.EXECUTE_WALL_SOFT_EXTENDED in kinds


def test_dynamic_inactivity_disabled_stalled_job_still_caught(
    tmp_path, monkeypatch
):
    """No-regression: DYNAMIC + inactivity=0 + a STALLED job past the wall is
    STILL killed. The fix floors inactivity to the DYNAMIC default, so the
    stall predicate is live and a genuinely-hung arm is caught (it does NOT run
    unbounded to the hard ceiling just because the env watchdog was off)."""
    ws, stage_dir, log_path = _mk_stage(tmp_path)
    # A fixed mtime = no liveness. The floored inactivity (5400s) is crossed
    # quickly because the fake clock steps 100s/call over many polls; give the
    # loop enough polls to march last_growth past the floor.
    rc, timed_out, killed, _ = _drive_run_with_watchdogs(
        monkeypatch, stage_dir=stage_dir, log_path=log_path,
        wall_timeout_s=1, inactivity_timeout_s=0,
        wall_kill_requires_stall=True, hard_ceiling_s=None,
        progressing=False, max_polls=200,
    )
    assert (rc, timed_out) == (-1, True)
    assert killed is True


def test_hard_mode_inactivity_disabled_takes_bare_subprocess_run(
    tmp_path, monkeypatch
):
    """Back-compat: HARD mode (wall_kill_requires_stall=False) + inactivity
    disabled MUST keep the bare subprocess.run fast path — the fix only forces
    the poll loop for DYNAMIC. We assert subprocess.run IS called (and Popen is
    NOT), i.e. the historical path is byte-for-byte preserved."""
    ws, stage_dir, log_path = _mk_stage(tmp_path)
    run_called = {"flag": False}
    popen_called = {"flag": False}

    class _RanProc:
        returncode = 0

    def _fake_run(*a, **k):
        run_called["flag"] = True
        return _RanProc()

    monkeypatch.setattr(claude_runner.subprocess, "run", _fake_run)
    monkeypatch.setattr(claude_runner.subprocess, "Popen",
                        lambda *a, **k: popen_called.__setitem__("flag", True))
    with log_path.open("w") as lf:
        rc, timed_out = claude_runner._run_with_watchdogs(
            ["claude"], cwd=str(stage_dir), env={}, log_file=lf,
            log_path=log_path, wall_timeout_s=1,
            inactivity_timeout_s=0, poll_s=0.001,
            heartbeat_path=None, stage_dir=stage_dir,
            wall_kill_requires_stall=False, hard_ceiling_s=None,
        )
    assert (rc, timed_out) == (0, False)
    assert run_called["flag"] is True
    assert popen_called["flag"] is False
