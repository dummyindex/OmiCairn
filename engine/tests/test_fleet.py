"""Tests for omicairn.fleet — the cross-run, read-only fleet-status view.

``fleet`` is the automation of the operator's manual `ps`/`nvidia-smi`/
newest-stage sweep, so the tests pin the signals that sweep produced by hand:

  - one row per workspace under a root (and non-workspace dirs skipped);
  - status / current_stage / iteration_counts read from STATE.md;
  - LIVE iff a live .run.lock OR a live `omicairn run` process references the ws
    (reusing reap's scanner), and "dead?" for an in_progress run with neither;
  - last-activity age derived from the newest real-progress write;
  - GPU host/session surfaced from job_marker.json;
  - the latest ETA/progress line surfaced from a stage run.log;
  - the table renders, sorts live-first, and shows the degraded-scan caveat;
  - it NEVER mutates a workspace (read-only is the whole contract).
"""
from __future__ import annotations

import json
import os
import pathlib
import time

import pytest

from omicairn import fleet as fleet_mod
from omicairn import reap as reap_mod
from omicairn import state as state_mod
from omicairn.constants import RunStatus


DEAD_PID = 999999


def _make_ws(
    parent: pathlib.Path,
    name: str,
    status: str = RunStatus.IN_PROGRESS,
    current_stage: str | None = None,
    iters: dict[str, int] | None = None,
    lock_pid: int | None = None,
) -> pathlib.Path:
    ws = parent / name
    ws.mkdir(parents=True)
    s = state_mod.initial_state(topic_slug=name)
    if current_stage is not None:
        s["current_stage"] = current_stage
    if iters:
        s["iteration_counts"].update(iters)
    state_mod.set_status(
        s, status, reason="seed" if status == RunStatus.HALTED else None
    )
    state_mod.write(ws / "STATE.md", s)
    if lock_pid is not None:
        (ws / fleet_mod.RUN_LOCK_NAME).write_text(f"{lock_pid}\n")
    return ws


@pytest.fixture(autouse=True)
def _no_live_processes(monkeypatch):
    """Default: stub the (shared, from reap) process scan to report NO live
    runs, NOT degraded. Tests override to inject a live process / degraded
    scan. Never depends on the real host process table."""
    monkeypatch.setattr(
        reap_mod, "scan_live_run_workspaces", lambda: (set(), False)
    )


# ---------------------------------------------------------------------------
# Row building — the core signals
# ---------------------------------------------------------------------------


def test_row_reads_state_fields(tmp_path):
    _make_ws(
        tmp_path, "topic-a-20260101-000000",
        status=RunStatus.IN_PROGRESS,
        current_stage="002_execute",
        iters={"scope": 1, "plan": 1, "execute": 2},
    )
    rows, degraded = fleet_mod.scan_fleet(tmp_path)
    assert degraded is False
    assert len(rows) == 1
    r = rows[0]
    assert r.topic_slug == "topic-a-20260101-000000"
    assert r.status == RunStatus.IN_PROGRESS
    assert r.current_stage == "002_execute"
    assert r.iteration_counts["execute"] == 2
    assert "e2" in r.iters_compact


def test_non_workspace_dirs_skipped(tmp_path):
    _make_ws(tmp_path, "real-20260101-000000")
    (tmp_path / "lab_knowledge").mkdir()           # not a workspace
    (tmp_path / "literature").mkdir()              # not a workspace
    (tmp_path / "loose_file.txt").write_text("x")  # not even a dir
    rows, _ = fleet_mod.scan_fleet(tmp_path)
    assert [r.name for r in rows] == ["real-20260101-000000"]


def test_single_workspace_target(tmp_path):
    ws = _make_ws(tmp_path, "solo-20260101-000000")
    rows, _ = fleet_mod.scan_fleet(ws)  # point AT the workspace, not the root
    assert len(rows) == 1
    assert rows[0].name == "solo-20260101-000000"


def test_empty_root(tmp_path):
    rows, _ = fleet_mod.scan_fleet(tmp_path)
    assert rows == []
    assert "no OmiCairn workspaces" in fleet_mod.render_table(rows, False)


def test_nonexistent_target_yields_no_rows(tmp_path):
    rows, _ = fleet_mod.scan_fleet(tmp_path / "does-not-exist")
    assert rows == []


# ---------------------------------------------------------------------------
# Liveness — reuse of reap's scanner
# ---------------------------------------------------------------------------


def test_live_when_lock_holder_alive(tmp_path):
    # Hold the lock with OUR pid (definitely alive).
    _make_ws(tmp_path, "alive-20260101-000000", lock_pid=os.getpid())
    rows, _ = fleet_mod.scan_fleet(tmp_path)
    assert rows[0].live is True
    assert rows[0].lock_pid == os.getpid()


def test_dead_when_inprogress_no_lock_no_process(tmp_path):
    _make_ws(tmp_path, "orphan-20260101-000000")  # no lock, no live process
    rows, _ = fleet_mod.scan_fleet(tmp_path)
    assert rows[0].live is False


def test_live_when_process_references_ws(tmp_path, monkeypatch):
    name = "procalive-20260101-000000"
    _make_ws(tmp_path, name)  # no lock
    monkeypatch.setattr(
        reap_mod, "scan_live_run_workspaces", lambda: ({name}, False)
    )
    rows, _ = fleet_mod.scan_fleet(tmp_path)
    assert rows[0].live is True


def test_dead_lock_pid_is_not_live(tmp_path):
    _make_ws(tmp_path, "deadlock-20260101-000000", lock_pid=DEAD_PID)
    rows, _ = fleet_mod.scan_fleet(tmp_path)
    assert rows[0].live is False
    assert rows[0].lock_pid == DEAD_PID  # surfaced even though dead


def test_degraded_scan_falls_back_to_lock_only(tmp_path, monkeypatch):
    name = "degraded-20260101-000000"
    _make_ws(tmp_path, name)  # no lock
    monkeypatch.setattr(
        reap_mod, "scan_live_run_workspaces", lambda: (set(), True)
    )
    rows, degraded = fleet_mod.scan_fleet(tmp_path)
    assert degraded is True
    # No lock + degraded process scan → cannot prove live → not live.
    assert rows[0].live is False
    table = fleet_mod.render_table(rows, degraded)
    assert "could not enumerate processes" in table


def test_terminal_run_live_marker_is_dot(tmp_path):
    _make_ws(tmp_path, "done-20260101-000000", status=RunStatus.DONE)
    rows, _ = fleet_mod.scan_fleet(tmp_path)
    assert fleet_mod._live_marker(rows[0]) == "·"


# ---------------------------------------------------------------------------
# Last-activity age
# ---------------------------------------------------------------------------


def test_activity_age_reflects_recent_write(tmp_path):
    ws = _make_ws(tmp_path, "fresh-20260101-000000")
    # A just-written events.jsonl is recent activity.
    (ws / "events.jsonl").write_text('{"kind":"x","ts":"now"}\n')
    rows, _ = fleet_mod.scan_fleet(tmp_path)
    assert rows[0].last_activity_age_s is not None
    assert rows[0].last_activity_age_s < 60  # written seconds ago


def test_activity_age_ignores_run_lock(tmp_path):
    """A re-touched .run.lock must NOT count as activity (it's liveness
    bookkeeping, not progress). We make the lock the newest file and assert
    the age reflects the older real artifacts instead."""
    ws = _make_ws(tmp_path, "lockonly-20260101-000000")
    old = time.time() - 3600
    events = ws / "events.jsonl"
    events.write_text('{"kind":"x"}\n')
    os.utime(events, (old, old))
    os.utime(ws / "STATE.md", (old, old))
    # lock is brand-new (now)
    (ws / fleet_mod.RUN_LOCK_NAME).write_text(f"{os.getpid()}\n")
    rows, _ = fleet_mod.scan_fleet(tmp_path)
    # Age should be ~1h (the old events/STATE), not ~0 (the fresh lock).
    assert rows[0].last_activity_age_s is not None
    assert rows[0].last_activity_age_s > 1800


def test_activity_age_counts_stage_dir_contents(tmp_path):
    ws = _make_ws(tmp_path, "staged-20260101-000000")
    # Age the top-level files; write a fresh file INSIDE a stage dir.
    old = time.time() - 7200
    os.utime(ws / "STATE.md", (old, old))
    stage = ws / "002_execute"
    stage.mkdir()
    (stage / "results.json").write_text("{}")  # fresh
    rows, _ = fleet_mod.scan_fleet(tmp_path)
    # The fresh results.json inside the stage dir should dominate the age.
    assert rows[0].last_activity_age_s is not None
    assert rows[0].last_activity_age_s < 60


def test_activity_age_ignores_beacon_only_events_jsonl(tmp_path):
    """beacon-masks-watchdog (audit 2026-06-17): a freshly-touched
    events.jsonl that contains ONLY EXECUTE_PROGRESS beacons must NOT count as
    activity. The watchdog's periodic beacon re-touches events.jsonl every
    ~75s even while the stage's inner agent is FROZEN; counting its raw file
    mtime let the run mask its own stall (a d1 stage sat 44 min frozen
    yet looked "active"). We age every real artifact to ~1h ago and write a
    BRAND-NEW beacon-only events.jsonl — the age must reflect the old
    artifacts (~1h), NOT the fresh beacon."""
    from omicairn.constants import EventKind

    ws = _make_ws(tmp_path, "frozen-20260101-000000")
    old = time.time() - 3600
    os.utime(ws / "STATE.md", (old, old))
    stage = ws / "047_review"
    stage.mkdir()
    (stage / "prior.json").write_text("{}")
    os.utime(stage, (old, old))
    os.utime(stage / "prior.json", (old, old))
    # Fresh events.jsonl, but ONLY beacons (the masking shape).
    events = ws / "events.jsonl"
    events.write_text(
        json.dumps({"kind": EventKind.EXECUTE_PROGRESS, "ts": "now"}) + "\n"
    )
    rows, _ = fleet_mod.scan_fleet(tmp_path)
    assert rows[0].last_activity_age_s is not None
    # The beacon must NOT reset the age — it should reflect the ~1h-old work.
    assert rows[0].last_activity_age_s > 1800, (
        "a beacon-only events.jsonl must not make a frozen run look active"
    )


def test_activity_age_counts_real_non_beacon_event(tmp_path):
    """Control / false-positive guard: a REAL non-beacon event (e.g.
    stage_done) IS progress and DOES keep the run looking active, even though
    bare beacons do not. This guards against the fix over-discounting real
    events."""
    from omicairn.constants import EventKind

    ws = _make_ws(tmp_path, "working-20260101-000000")
    old = time.time() - 3600
    os.utime(ws / "STATE.md", (old, old))
    # A real, fresh stage_done event (append_event auto-fills a current ts).
    from omicairn import stage as stage_mod

    stage_mod.append_event(
        ws, {"kind": EventKind.STAGE_DONE, "stage": "046_execute"}
    )
    rows, _ = fleet_mod.scan_fleet(tmp_path)
    assert rows[0].last_activity_age_s is not None
    assert rows[0].last_activity_age_s < 60, (
        "a fresh real progress event must keep the run looking active"
    )


def test_fmt_age_buckets():
    assert fleet_mod._fmt_age(None) == "-"
    assert fleet_mod._fmt_age(5) == "5s"
    assert fleet_mod._fmt_age(120) == "2m"
    assert fleet_mod._fmt_age(3 * 3600 + 12 * 60).endswith("m")
    assert fleet_mod._fmt_age(3 * 86400) == "3d"


# ---------------------------------------------------------------------------
# job_marker.json (GPU/host) surfacing
# ---------------------------------------------------------------------------


def test_job_marker_host_surfaced(tmp_path):
    ws = _make_ws(tmp_path, "gpu-20260101-000000")
    stage = ws / "002_execute"
    stage.mkdir()
    (stage / "job_marker.json").write_text(json.dumps({
        "host": "gpu-host-1",
        "remote_results_path": "gpu-host-1:/scratch/results.json",
        "pid_or_session": "tmux:d5run",
        "status": "launched",
    }))
    rows, _ = fleet_mod.scan_fleet(tmp_path)
    r = rows[0]
    assert r.host == "gpu-host-1"
    assert r.gpu_session == "tmux:d5run"
    assert r.job_status == "launched"
    table = fleet_mod.render_table(rows, False)
    assert "gpu-host-1" in table
    assert "tmux:d5run" in table


def test_latest_job_marker_wins(tmp_path):
    ws = _make_ws(tmp_path, "twomarkers-20260101-000000")
    s1 = ws / "002_execute"
    s1.mkdir()
    (s1 / "job_marker.json").write_text(json.dumps({"host": "old-host"}))
    s2 = ws / "004_execute"
    s2.mkdir()
    (s2 / "job_marker.json").write_text(json.dumps({"host": "new-host"}))
    rows, _ = fleet_mod.scan_fleet(tmp_path)
    assert rows[0].host == "new-host"  # newest stage's marker


def test_no_marker_means_no_host(tmp_path):
    _make_ws(tmp_path, "nomarker-20260101-000000")
    rows, _ = fleet_mod.scan_fleet(tmp_path)
    assert rows[0].host is None
    assert rows[0].gpu_session is None


def test_corrupt_job_marker_is_tolerated(tmp_path):
    ws = _make_ws(tmp_path, "badmarker-20260101-000000")
    stage = ws / "002_execute"
    stage.mkdir()
    (stage / "job_marker.json").write_text("{ not valid json")
    rows, _ = fleet_mod.scan_fleet(tmp_path)  # must not raise
    assert rows[0].host is None


# ---------------------------------------------------------------------------
# ETA / progress-line surfacing
# ---------------------------------------------------------------------------


def test_latest_eta_note_surfaced(tmp_path):
    ws = _make_ws(tmp_path, "eta-20260101-000000")
    stage = ws / "002_execute"
    stage.mkdir()
    (stage / "run.log").write_text(
        "loading model...\n"
        "100/420 items (56s/item, eta 225min)\n"
        "180/420 items (56s/item, eta 168min)\n"
    )
    rows, _ = fleet_mod.scan_fleet(tmp_path)
    assert rows[0].latest_eta_note is not None
    assert "180/420" in rows[0].latest_eta_note  # the LATEST matching line
    table = fleet_mod.render_table(rows, False)
    assert "latest progress" in table


def test_no_eta_note_when_log_has_none(tmp_path):
    ws = _make_ws(tmp_path, "noeta-20260101-000000")
    stage = ws / "002_execute"
    stage.mkdir()
    (stage / "run.log").write_text("just some output\nno progress lines here\n")
    rows, _ = fleet_mod.scan_fleet(tmp_path)
    assert rows[0].latest_eta_note is None


# ---------------------------------------------------------------------------
# Rendering + sorting
# ---------------------------------------------------------------------------


def test_table_has_header_and_one_line_per_run(tmp_path):
    _make_ws(tmp_path, "aaa-20260101-000000", current_stage="000_scope")
    _make_ws(tmp_path, "bbb-20260101-000000", status=RunStatus.DONE)
    rows, deg = fleet_mod.scan_fleet(tmp_path)
    table = fleet_mod.render_table(rows, deg)
    assert "RUN" in table and "STATUS" in table and "LIVE" in table
    assert "aaa-20260101-000000" in table
    assert "bbb-20260101-000000" in table
    # Footer summarises counts.
    assert "run(s):" in table


def test_live_runs_sorted_first(tmp_path):
    # A dead in_progress run and a live (lock-held) run.
    _make_ws(tmp_path, "zzz-dead-20260101-000000")
    _make_ws(tmp_path, "aaa-live-20260101-000000", lock_pid=os.getpid())
    rows, _ = fleet_mod.scan_fleet(tmp_path)
    # Live first, regardless of name sort.
    assert rows[0].live is True
    assert rows[0].name == "aaa-live-20260101-000000"


def test_dead_inprogress_triggers_reap_hint(tmp_path):
    _make_ws(tmp_path, "orphan-20260101-000000")  # in_progress, no live process
    rows, deg = fleet_mod.scan_fleet(tmp_path)
    table = fleet_mod.render_table(rows, deg)
    assert "omicairn reap" in table


def test_halted_reason_surfaced_in_table(tmp_path):
    _make_ws(tmp_path, "halt-20260101-000000", status=RunStatus.HALTED)
    rows, deg = fleet_mod.scan_fleet(tmp_path)
    table = fleet_mod.render_table(rows, deg)
    assert "halted:" in table


# ---------------------------------------------------------------------------
# Read-only contract
# ---------------------------------------------------------------------------


def test_scan_never_mutates_state(tmp_path):
    ws = _make_ws(tmp_path, "immutable-20260101-000000")
    before = (ws / "STATE.md").read_text()
    fleet_mod.scan_fleet(tmp_path)
    fleet_mod.render_table(*fleet_mod.scan_fleet(tmp_path))
    after = (ws / "STATE.md").read_text()
    assert before == after
    # No events appended either.
    assert not (ws / "events.jsonl").exists()


def test_corrupt_state_does_not_abort_sweep(tmp_path):
    # One good ws, one with a corrupt STATE.md.
    _make_ws(tmp_path, "good-20260101-000000")
    bad = tmp_path / "bad-20260101-000000"
    bad.mkdir()
    (bad / "STATE.md").write_text("this is not valid front-matter")
    rows, _ = fleet_mod.scan_fleet(tmp_path)
    names = {r.name for r in rows}
    assert "good-20260101-000000" in names
    assert "bad-20260101-000000" in names  # still listed
    bad_row = next(r for r in rows if r.name == "bad-20260101-000000")
    assert bad_row.status == "unknown"
