"""Tests for scripts/dedup_literature.py — the one-time maintenance helper
that converts duplicate per-workspace literature PDFs into hardlinks to the
central lab_library. Focus is the SAFETY contract (dry-run default, live-lock
skip, central never touched, idempotent)."""
from __future__ import annotations

import importlib.util
import os
import pathlib

import pytest

_SCRIPT = (pathlib.Path(__file__).resolve().parent.parent
           / "scripts" / "dedup_literature.py")


@pytest.fixture(autouse=True)
def _no_paper_lib_env(monkeypatch):
    """The repo-wide conftest sets OMICAIRN_PAPER_LIB to a tmp dir; the dedup
    script resolves central from it, which would point away from this test's
    <workspaces>/lab_library. Unset it so the script uses the default."""
    monkeypatch.delenv("OMICAIRN_PAPER_LIB", raising=False)
    monkeypatch.delenv("OMICAIRN_WORKSPACES_ROOT", raising=False)


def _load_script():
    spec = importlib.util.spec_from_file_location("dedup_literature", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


dedup = _load_script()


def _mk_central(workspaces: pathlib.Path) -> pathlib.Path:
    central = workspaces / "lab_library"
    (central / "pdfs").mkdir(parents=True)
    (central / "abstracts").mkdir(parents=True)
    (central / "pdfs" / "10.1_a.pdf").write_bytes(b"%PDF-1.4\nAAA\n")
    return central


def _mk_workspace(workspaces: pathlib.Path, name: str, *, lock_pid: int | None,
                  dup_bytes: bytes = b"%PDF-1.4\nAAA\n") -> pathlib.Path:
    ws = workspaces / name
    (ws / "literature" / "pdfs").mkdir(parents=True)
    (ws / "literature" / "pdfs" / "10.1_a.pdf").write_bytes(dup_bytes)
    if lock_pid is not None:
        (ws / ".run.lock").write_text(f"{lock_pid}\n")
    return ws


def test_dry_run_reports_but_does_not_modify(tmp_path, capsys):
    workspaces = tmp_path / "workspaces"
    central = _mk_central(workspaces)
    ws = _mk_workspace(workspaces, "run-dead", lock_pid=None)
    dup = ws / "literature" / "pdfs" / "10.1_a.pdf"
    ino_before = dup.stat().st_ino

    rc = dedup.main([str(workspaces)])  # no --apply
    assert rc == 0
    out = capsys.readouterr().out
    assert "DRY-RUN" in out
    assert "relinkable" in out
    # Not modified: still a separate inode from central.
    assert dup.stat().st_ino == ino_before
    assert dup.stat().st_ino != (central / "pdfs" / "10.1_a.pdf").stat().st_ino


def test_apply_relinks_duplicate_to_central(tmp_path):
    workspaces = tmp_path / "workspaces"
    central = _mk_central(workspaces)
    ws = _mk_workspace(workspaces, "run-dead", lock_pid=None)
    dup = ws / "literature" / "pdfs" / "10.1_a.pdf"
    central_f = central / "pdfs" / "10.1_a.pdf"

    rc = dedup.main([str(workspaces), "--apply"])
    assert rc == 0
    # Now shares the central inode (deduped) and same bytes.
    assert dup.stat().st_ino == central_f.stat().st_ino
    assert dup.read_bytes() == b"%PDF-1.4\nAAA\n"
    # Central untouched (still exists with its content).
    assert central_f.read_bytes() == b"%PDF-1.4\nAAA\n"


def test_apply_is_idempotent(tmp_path):
    workspaces = tmp_path / "workspaces"
    _mk_central(workspaces)
    _mk_workspace(workspaces, "run-dead", lock_pid=None)
    dedup.main([str(workspaces), "--apply"])
    # Second run finds it already hardlinked → nothing relinkable.
    s = _run_and_get_stats(workspaces, apply=True)
    assert s.relinked == 0
    assert s.already_linked == 1


def test_live_lock_workspace_is_skipped(tmp_path, capsys):
    """A workspace whose .run.lock holds THIS (alive) PID must be skipped —
    never relink under a live run."""
    workspaces = tmp_path / "workspaces"
    central = _mk_central(workspaces)
    ws = _mk_workspace(workspaces, "run-live", lock_pid=os.getpid())
    dup = ws / "literature" / "pdfs" / "10.1_a.pdf"
    ino_before = dup.stat().st_ino

    rc = dedup.main([str(workspaces), "--apply"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "SKIP (live run" in out
    # Untouched despite being a byte-identical duplicate.
    assert dup.stat().st_ino == ino_before
    assert dup.stat().st_ino != (central / "pdfs" / "10.1_a.pdf").stat().st_ino


def test_dead_pid_lock_is_not_skipped(tmp_path):
    """A .run.lock held by a DEAD pid must NOT block dedup (it's stale)."""
    workspaces = tmp_path / "workspaces"
    central = _mk_central(workspaces)
    # PID 0 is never a live process; _pid_alive returns False.
    ws = _mk_workspace(workspaces, "run-stale", lock_pid=999999)
    dup = ws / "literature" / "pdfs" / "10.1_a.pdf"
    rc = dedup.main([str(workspaces), "--apply"])
    assert rc == 0
    # Relinked (dead-holder lock did not protect it).
    assert dup.stat().st_ino == (central / "pdfs" / "10.1_a.pdf").stat().st_ino


def test_non_identical_files_are_left_alone(tmp_path):
    workspaces = tmp_path / "workspaces"
    central = _mk_central(workspaces)
    # Same slug, DIFFERENT bytes — must not be relinked (would corrupt data).
    ws = _mk_workspace(workspaces, "run-diff", lock_pid=None,
                       dup_bytes=b"%PDF-1.4\nDIFFERENT\n")
    dup = ws / "literature" / "pdfs" / "10.1_a.pdf"
    dedup.main([str(workspaces), "--apply"])
    assert dup.read_bytes() == b"%PDF-1.4\nDIFFERENT\n"
    assert dup.stat().st_ino != (central / "pdfs" / "10.1_a.pdf").stat().st_ino


def test_per_workspace_lab_library_copy_is_reported_not_deleted(tmp_path, capsys):
    workspaces = tmp_path / "workspaces"
    _mk_central(workspaces)
    ws = _mk_workspace(workspaces, "run-with-copy", lock_pid=None)
    # Spurious per-workspace lab_library copy (the audit bug artifact).
    (ws / "lab_library" / "pdfs").mkdir(parents=True)
    (ws / "lab_library" / "pdfs" / "x.pdf").write_bytes(b"copy")

    dedup.main([str(workspaces), "--apply"])
    out = capsys.readouterr().out
    assert "per-workspace lab_library" in out
    # Reported, but NOT deleted — deletion is the owner's call.
    assert (ws / "lab_library" / "pdfs" / "x.pdf").exists()


def test_stage_nested_literature_is_deduped(tmp_path):
    """The duplicates live under <ws>/<NNN_stage>/literature too — dedup must
    reach them, not just <ws>/literature."""
    workspaces = tmp_path / "workspaces"
    central = _mk_central(workspaces)
    ws = workspaces / "run-stage"
    (ws / "001_plan" / "literature" / "pdfs").mkdir(parents=True)
    nested = ws / "001_plan" / "literature" / "pdfs" / "10.1_a.pdf"
    nested.write_bytes(b"%PDF-1.4\nAAA\n")
    dedup.main([str(workspaces), "--apply"])
    assert nested.stat().st_ino == (central / "pdfs" / "10.1_a.pdf").stat().st_ino


def test_missing_central_errors_cleanly(tmp_path):
    workspaces = tmp_path / "workspaces"
    workspaces.mkdir()
    rc = dedup.main([str(workspaces)])
    assert rc == 2  # no central lab_library → clean error, no traceback


# --- helper ---------------------------------------------------------------

def _run_and_get_stats(workspaces: pathlib.Path, *, apply: bool):
    central = (workspaces / "lab_library").resolve()
    stats = dedup.Stats()
    for child in sorted(workspaces.iterdir()):
        if not child.is_dir() or child.name in ("lab_library", "lab_knowledge"):
            continue
        dedup.process_workspace(child, central, stats, apply, lambda *_: None)
    return stats
