"""Tests for omicairn.reap — the orphaned-run reaper.

The whole point of this module is SAFETY: it marks dead ``in_progress`` runs
terminal so they stop polluting audit sweeps, and it must NEVER touch a run
that is still alive. These tests prove the safety rails:

  - it reaps a genuine orphan (in_progress, no lock, no process) only on --apply;
  - dry-run (the DEFAULT) changes nothing;
  - done / halted runs are never reaped (and reaping is idempotent);
  - a run holding a LIVE .run.lock is never reaped;
  - the OS-process-liveness check OVERRIDES the missing-lock signal (the
    critical false-positive guard);
  - if the process scan is degraded (can't enumerate), it fails safe;
  - a whole workspaces-root with a mix reaps only the true orphans.
"""
from __future__ import annotations

import json
import os
import pathlib

import pytest

from omicairn import reap as reap_mod
from omicairn import stage as stage_mod
from omicairn import state as state_mod
from omicairn.constants import DONE_FLAG, PIPELINE_ORDER, RunStatus

# Capture the UN-stubbed scanner once at import time, before any test's
# monkeypatch can replace the module attribute. The real-process-scan smoke
# test calls this directly so it exercises the genuine /proc-or-ps path.
_REAL_SCAN = reap_mod.scan_live_run_workspaces


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

# A PID that is essentially guaranteed to be dead on any host.
DEAD_PID = 999999


def _make_ws(
    parent: pathlib.Path,
    name: str,
    status: str = RunStatus.IN_PROGRESS,
    lock_pid: int | None = None,
    stages: int = 1,
) -> pathlib.Path:
    """Create a minimal but realistic workspace dir under ``parent``.

    Writes a real STATE.md via ``state.write`` (so ``reap`` reads it through
    the normal ``state.read`` path), and optionally a ``.run.lock`` holding
    ``lock_pid``.

    ``stages`` seeds that many *executed* pipeline stages (``NNN_<phase>/``
    dirs, each with a ``done.flag``) and sets ``current_stage`` to the last
    one — exactly what the orchestrator leaves behind once a run has done some
    work. The DEFAULT is 1 so a fixture models a genuine *mid-pipeline* orphan
    (a run that started, executed a stage, then died). Pass ``stages=0`` to
    model a freshly-``init``ed case that has run NOTHING (``current_stage``
    stays None, zero stage dirs) — the case the never-run guard must skip.
    """
    ws = parent / name
    ws.mkdir(parents=True)
    s = state_mod.initial_state(topic_slug=name)
    state_mod.set_status(s, status, reason="seed" if status == RunStatus.HALTED else None)
    for idx in range(stages):
        phase = PIPELINE_ORDER[idx % len(PIPELINE_ORDER)]
        stage_dir = stage_mod.create_stage_dir(ws, idx, phase)
        (stage_dir / DONE_FLAG).write_text("")
        state_mod.record_stage_start(s, idx, phase)
    state_mod.write(ws / "STATE.md", s)
    if lock_pid is not None:
        (ws / reap_mod.RUN_LOCK_NAME).write_text(f"{lock_pid}\n")
    return ws


def _status_of(ws: pathlib.Path) -> str:
    return str(state_mod.read(ws / "STATE.md")["status"])


def _events(ws: pathlib.Path) -> list[dict]:
    p = ws / "events.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


@pytest.fixture(autouse=True)
def _no_live_processes(monkeypatch):
    """By default, stub the process scan to report NO live runs and NOT
    degraded. Individual tests override this to inject a live process or a
    degraded scan. Keeping the default explicit means the suite never depends
    on the real host process table (which could, in principle, contain a
    `omicairn run`)."""
    monkeypatch.setattr(
        reap_mod, "scan_live_run_workspaces", lambda: (set(), False)
    )


# ---------------------------------------------------------------------------
# Core behaviour: reap a genuine orphan only on --apply
# ---------------------------------------------------------------------------


def test_apply_reaps_in_progress_with_no_lock_and_no_process(tmp_path):
    ws = _make_ws(tmp_path, "orphan-20260610-000001")  # in_progress, no lock
    report = reap_mod.reap(ws, apply=True)

    assert report.orphan_count == 1
    assert report.reaped_count == 1
    assert _status_of(ws) == RunStatus.HALTED
    # The halt reason explains WHY and is recoverable context.
    s = state_mod.read(ws / "STATE.md")
    assert "orphaned" in (s.get("halted_reason") or "")
    # A reaped_orphan event was journaled.
    evs = _events(ws)
    assert any(e["kind"] == reap_mod.REAP_EVENT_KIND for e in evs)
    reap_ev = next(e for e in evs if e["kind"] == reap_mod.REAP_EVENT_KIND)
    assert reap_ev["previous_status"] == RunStatus.IN_PROGRESS
    assert reap_ev["new_status"] == RunStatus.HALTED


def test_dry_run_is_default_and_mutates_nothing(tmp_path):
    ws = _make_ws(tmp_path, "orphan-20260610-000002")  # in_progress, no lock
    # No apply= → dry-run by default.
    report = reap_mod.reap(ws)

    assert report.applied is False
    assert report.orphan_count == 1  # still IDENTIFIED as an orphan
    assert report.reaped_count == 0  # but NOT reaped
    assert _status_of(ws) == RunStatus.IN_PROGRESS  # unchanged
    assert _events(ws) == []  # no event journaled


# ---------------------------------------------------------------------------
# NEVER-RUN guard: a freshly-init'd case (0 stages) is NOT an orphan
# ---------------------------------------------------------------------------


def test_freshly_initd_case_with_zero_stages_is_not_orphaned(tmp_path):
    """`omicairn init` leaves a workspace in_progress with current_stage=None and
    ZERO stage dirs — a valid case AWAITING its first dispatch, not a dead run.
    It must NOT be flagged an orphan (reaping it would wrongly HALT pending
    work). The skip_reason explains it is freshly-initialized / awaiting."""
    ws = _make_ws(tmp_path, "fresh-20260612-170955", stages=0)  # never ran
    # Sanity: this fixture really is the never-run shape.
    assert stage_mod.list_stages(ws) == []
    assert state_mod.read(ws / "STATE.md").get("current_stage") is None

    verdicts, _ = reap_mod.find_orphans(ws)
    assert len(verdicts) == 1
    v = verdicts[0]
    assert v.is_orphan is False
    assert v.status == RunStatus.IN_PROGRESS  # still in_progress, just not orphaned
    assert "freshly initialized" in v.skip_reason
    assert "awaiting first dispatch" in v.skip_reason

    # Even with --apply, nothing is reaped and the status is untouched.
    report = reap_mod.reap(ws, apply=True)
    assert report.orphan_count == 0
    assert report.reaped_count == 0
    assert _status_of(ws) == RunStatus.IN_PROGRESS
    assert _events(ws) == []  # no reaped_orphan event journaled


def test_in_progress_with_one_executed_stage_is_still_orphaned(tmp_path):
    """REGRESSION GUARD for the never-run fix: a run that DID execute a stage
    (>=1 NNN_<phase>/ dir, current_stage set) and then died with no lock and no
    live process is a GENUINE mid-pipeline orphan — the never-run guard must
    NOT shield it. This is the real abandoned-run case the reaper exists for."""
    ws = _make_ws(tmp_path, "mid-pipeline-20260610-000020", stages=1)
    # Sanity: this fixture really has executed work (so it is NOT never-run).
    assert len(stage_mod.list_stages(ws)) == 1
    assert state_mod.read(ws / "STATE.md").get("current_stage") is not None
    assert reap_mod._never_run(ws) is False

    report = reap_mod.reap(ws, apply=True)
    assert report.orphan_count == 1
    assert report.reaped_count == 1
    assert _status_of(ws) == RunStatus.HALTED
    assert "orphaned" in (state_mod.read(ws / "STATE.md").get("halted_reason") or "")


# ---------------------------------------------------------------------------
# Never reap a terminal run; reaping is idempotent
# ---------------------------------------------------------------------------


def test_done_run_is_never_reaped(tmp_path):
    ws = _make_ws(tmp_path, "done-20260610-000003", status=RunStatus.DONE)
    report = reap_mod.reap(ws, apply=True)
    assert report.orphan_count == 0
    assert report.reaped_count == 0
    assert _status_of(ws) == RunStatus.DONE


def test_halted_run_is_never_reaped_and_reap_is_idempotent(tmp_path):
    ws = _make_ws(tmp_path, "halted-20260610-000004", status=RunStatus.HALTED)
    # First pass: a halted run is not in_progress → skipped.
    r1 = reap_mod.reap(ws, apply=True)
    assert r1.reaped_count == 0
    assert _status_of(ws) == RunStatus.HALTED
    # Second pass: still halted, still skipped → no error (idempotent).
    r2 = reap_mod.reap(ws, apply=True)
    assert r2.reaped_count == 0
    assert _status_of(ws) == RunStatus.HALTED


def test_reaping_twice_is_idempotent_on_a_fresh_orphan(tmp_path):
    """After --apply reaps an orphan it becomes HALTED; a second --apply must
    find nothing to do and must not error."""
    ws = _make_ws(tmp_path, "orphan-20260610-000005")
    reap_mod.reap(ws, apply=True)
    assert _status_of(ws) == RunStatus.HALTED
    # Re-run: the now-halted ws is no longer in_progress → not an orphan.
    r2 = reap_mod.reap(ws, apply=True)
    assert r2.orphan_count == 0
    assert r2.reaped_count == 0
    # Exactly ONE reap event total (the second pass did not re-journal).
    assert sum(1 for e in _events(ws) if e["kind"] == reap_mod.REAP_EVENT_KIND) == 1


# ---------------------------------------------------------------------------
# Live lock veto
# ---------------------------------------------------------------------------


def test_in_progress_with_live_lock_is_never_reaped(tmp_path):
    """A .run.lock held by a LIVE pid (our own) proves the run is alive."""
    ws = _make_ws(
        tmp_path, "live-lock-20260610-000006", lock_pid=os.getpid()
    )
    report = reap_mod.reap(ws, apply=True)
    assert report.orphan_count == 0
    assert report.reaped_count == 0
    assert _status_of(ws) == RunStatus.IN_PROGRESS


def test_in_progress_with_dead_lock_is_reaped(tmp_path):
    """A .run.lock whose pid is DEAD does not protect the run — combined with
    no live process, it is an orphan."""
    ws = _make_ws(
        tmp_path, "dead-lock-20260610-000007", lock_pid=DEAD_PID
    )
    report = reap_mod.reap(ws, apply=True)
    assert report.orphan_count == 1
    assert _status_of(ws) == RunStatus.HALTED


# ---------------------------------------------------------------------------
# THE CRITICAL FALSE-POSITIVE GUARD: process check overrides missing lock
# ---------------------------------------------------------------------------


def test_live_process_overrides_missing_lock(tmp_path, monkeypatch):
    """A run can legitimately hold NO lock for brief windows. If a live
    `omicairn run` OS process references the workspace, it MUST NOT be reaped even
    though there is no lock. This is the load-bearing safety rail."""
    ws = _make_ws(tmp_path, "live-proc-20260610-000008")  # in_progress, NO lock
    # Stub the process scan to report THIS workspace as having a live run.
    monkeypatch.setattr(
        reap_mod,
        "scan_live_run_workspaces",
        lambda: ({ws.name}, False),
    )
    report = reap_mod.reap(ws, apply=True)
    assert report.orphan_count == 0  # process check vetoed it
    assert report.reaped_count == 0
    assert _status_of(ws) == RunStatus.IN_PROGRESS


def test_live_process_for_a_DIFFERENT_basename_does_not_protect(tmp_path, monkeypatch):
    """The process match is on the FULL basename. A live run for a *different*
    workspace (even one sharing a slug prefix) must NOT shield this one."""
    ws = _make_ws(tmp_path, "case-x-20260610-000009")  # the dead one
    # A live run for a DIFFERENT timestamp of the same slug prefix.
    monkeypatch.setattr(
        reap_mod,
        "scan_live_run_workspaces",
        lambda: ({"case-x-20260610-999999"}, False),
    )
    report = reap_mod.reap(ws, apply=True)
    assert report.orphan_count == 1  # NOT shielded — different basename
    assert _status_of(ws) == RunStatus.HALTED


# ---------------------------------------------------------------------------
# Fail-safe when the process scan is degraded
# ---------------------------------------------------------------------------


def test_degraded_process_scan_reaps_nothing(tmp_path, monkeypatch):
    """If we cannot enumerate the process table, every in_progress run is
    treated as ALIVE — we never reap on uncertainty."""
    ws = _make_ws(tmp_path, "blind-20260610-000010")  # would be an orphan
    monkeypatch.setattr(
        reap_mod, "scan_live_run_workspaces", lambda: (set(), True)  # degraded
    )
    report = reap_mod.reap(ws, apply=True)
    assert report.process_scan_degraded is True
    assert report.orphan_count == 0
    assert report.reaped_count == 0
    assert _status_of(ws) == RunStatus.IN_PROGRESS


# ---------------------------------------------------------------------------
# Superseded-sibling reason annotation
# ---------------------------------------------------------------------------


def test_superseded_note_when_newer_terminal_sibling_exists(tmp_path):
    """When a LATER workspace with the same slug prefix is terminal, the orphan
    reason names it (the superseded-re-run case)."""
    prefix = "case-super-20260609"
    _make_ws(tmp_path, f"{prefix}-000000", status=RunStatus.IN_PROGRESS)  # orphan
    # newer sibling, terminal
    _make_ws(tmp_path, f"{prefix}-235959", status=RunStatus.DONE)

    verdicts, _ = reap_mod.find_orphans(tmp_path / f"{prefix}-000000")
    assert len(verdicts) == 1
    v = verdicts[0]
    assert v.is_orphan is True
    assert "superseded by terminal sibling" in v.reason
    assert f"{prefix}-235959" in v.reason


# ---------------------------------------------------------------------------
# Whole-root scan reaps only the true orphans
# ---------------------------------------------------------------------------


def test_scan_workspaces_root_reaps_only_true_orphans(tmp_path, monkeypatch):
    root = tmp_path / "workspaces"
    root.mkdir()
    orphan_a = _make_ws(root, "orphan-a-20260610-000011")  # in_progress, no lock
    orphan_b = _make_ws(root, "orphan-b-20260610-000012", lock_pid=DEAD_PID)
    live_lock = _make_ws(root, "live-lock-20260610-000013", lock_pid=os.getpid())
    live_proc = _make_ws(root, "live-proc-20260610-000014")  # protected by process
    done = _make_ws(root, "done-20260610-000015", status=RunStatus.DONE)
    halted = _make_ws(root, "halted-20260610-000016", status=RunStatus.HALTED)
    # A non-workspace child must be ignored entirely.
    (root / "lab_knowledge").mkdir()

    monkeypatch.setattr(
        reap_mod,
        "scan_live_run_workspaces",
        lambda: ({live_proc.name}, False),
    )

    report = reap_mod.reap(root, apply=True)

    # 6 workspaces scanned (the non-workspace dir is skipped).
    assert report.scanned == 6
    reaped_names = {p.name for p in report.reaped}
    assert reaped_names == {orphan_a.name, orphan_b.name}
    # The protected / terminal ones are untouched.
    assert _status_of(live_lock) == RunStatus.IN_PROGRESS
    assert _status_of(live_proc) == RunStatus.IN_PROGRESS
    assert _status_of(done) == RunStatus.DONE
    assert _status_of(halted) == RunStatus.HALTED
    # The two orphans are now halted.
    assert _status_of(orphan_a) == RunStatus.HALTED
    assert _status_of(orphan_b) == RunStatus.HALTED


# ---------------------------------------------------------------------------
# Process-scan parsing internals (the cmdline matcher)
# ---------------------------------------------------------------------------


def test_looks_like_omicairn_run_accepts_console_script_form():
    tokens = [
        "/x/.venv/bin/python",
        "/x/.venv/bin/omicairn",
        "run",
        "workspaces/foo-20260610-000017",
        "--phase",
        "all",
    ]
    assert reap_mod._looks_like_omicairn_run(tokens) is True
    assert reap_mod._basename_from_tokens(tokens) == "foo-20260610-000017"


def test_looks_like_omicairn_run_accepts_module_form():
    tokens = ["python", "-m", "omicairn", "run", "/abs/workspaces/bar-20260610-000018"]
    assert reap_mod._looks_like_omicairn_run(tokens) is True
    assert reap_mod._basename_from_tokens(tokens) == "bar-20260610-000018"


def test_looks_like_omicairn_run_rejects_other_subcommands():
    # `omicairn status` / `omicairn reap` themselves must not look like `omicairn run`.
    assert reap_mod._looks_like_omicairn_run(
        ["/x/bin/omicairn", "status", "workspaces/foo-1"]
    ) is False
    assert reap_mod._looks_like_omicairn_run(
        ["/x/bin/omicairn", "reap", "workspaces"]
    ) is False
    # An unrelated process that merely mentions "run" is not a omicairn run.
    assert reap_mod._looks_like_omicairn_run(["bash", "run", "something"]) is False


def test_basename_extraction_strips_trailing_slash():
    tokens = ["omicairn", "run", "workspaces/foo-20260610-000019/"]
    assert reap_mod._basename_from_tokens(tokens) == "foo-20260610-000019"


# ---------------------------------------------------------------------------
# count_live_runs / is_workspace_live — the concurrency-cap liveness count
# ---------------------------------------------------------------------------


def test_count_live_runs_counts_only_live_not_stale_in_progress(tmp_path):
    """THE KEY CORRECTNESS CASE for the concurrency cap: a STALE in_progress
    run (status says in_progress but NO live lock and NO live process) must
    NOT count toward the live total. Only genuinely-live runs count."""
    root = tmp_path / "workspaces"
    root.mkdir()
    # Two stale in_progress (dead) runs — must NOT count.
    _make_ws(root, "stale-a-20260623-000001")               # in_progress, no lock
    _make_ws(root, "stale-b-20260623-000002", lock_pid=DEAD_PID)  # dead lock
    # One genuinely live run (live lock = our own pid).
    _make_ws(root, "live-lock-20260623-000003", lock_pid=os.getpid())
    # Terminal runs never count.
    _make_ws(root, "done-20260623-000004", status=RunStatus.DONE)
    _make_ws(root, "halted-20260623-000005", status=RunStatus.HALTED)

    count, degraded = reap_mod.count_live_runs(root)
    assert degraded is False
    assert count == 1  # only the live-lock run


def test_count_live_runs_counts_live_process(tmp_path, monkeypatch):
    """A run with no lock but a live `omicairn run` OS process counts as live."""
    root = tmp_path / "workspaces"
    root.mkdir()
    ws = _make_ws(root, "live-proc-20260623-000006")  # in_progress, no lock
    _make_ws(root, "stale-20260623-000007")           # dead, no lock
    monkeypatch.setattr(
        reap_mod, "scan_live_run_workspaces", lambda: ({ws.name}, False)
    )
    count, degraded = reap_mod.count_live_runs(root)
    assert degraded is False
    assert count == 1  # only the live-proc run


def test_count_live_runs_excludes_named_workspace(tmp_path):
    """``exclude`` (matched by dir name) is never counted — this is how the
    cap excludes the run it is about to resume so a resume of an already-live
    workspace never self-blocks."""
    root = tmp_path / "workspaces"
    root.mkdir()
    target = _make_ws(root, "resume-me-20260623-000008", lock_pid=os.getpid())
    _make_ws(root, "other-live-20260623-000009", lock_pid=os.getpid())

    # Without exclusion both live runs count.
    assert reap_mod.count_live_runs(root)[0] == 2
    # Excluding the target drops it from its own count.
    assert reap_mod.count_live_runs(root, exclude=target)[0] == 1


def test_count_live_runs_degraded_scan_falls_back_to_lock(tmp_path, monkeypatch):
    """When process enumeration is degraded we still count live LOCK holders
    (never assume a locked run is dead) but cannot credit process-only runs."""
    root = tmp_path / "workspaces"
    root.mkdir()
    _make_ws(root, "live-lock-20260623-000010", lock_pid=os.getpid())
    _make_ws(root, "proc-only-20260623-000011")  # would be live only via process
    monkeypatch.setattr(
        reap_mod, "scan_live_run_workspaces", lambda: (set(), True)  # degraded
    )
    count, degraded = reap_mod.count_live_runs(root)
    assert degraded is True
    assert count == 1  # the live-lock run; proc-only can't be proven live


def test_count_live_runs_on_single_workspace_target(tmp_path):
    """A single-workspace target (no siblings) counts itself unless excluded."""
    root = tmp_path / "workspaces"
    root.mkdir()
    ws = _make_ws(root, "solo-20260623-000012", lock_pid=os.getpid())
    assert reap_mod.count_live_runs(ws)[0] == 1
    assert reap_mod.count_live_runs(ws, exclude=ws)[0] == 0


def test_real_process_scan_runs_without_error():
    """Smoke: the real scanner (the genuine /proc-or-ps path, NOT the autouse
    stub) returns a (set, bool) and does not raise on this host. We do not
    assert contents — the point is that the enumeration code executes cleanly
    and never crashes the reaper."""
    names, degraded = _REAL_SCAN()
    assert isinstance(names, set)
    assert isinstance(degraded, bool)
