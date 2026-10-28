"""Unit tests for omicairn.provenance (run-level reproducibility stamps).

No network, no real `omicairn run`. We exercise:
  - capture_omicairn_version field shape + git population in a real repo;
  - the not-a-git-repo / missing-git fallback (mocked) — never raises,
    version falls back to "unknown" or the package version;
  - dirty-tree detection (mocked porcelain);
  - record_invocation per-invocation append semantics + file creation +
    STATE.md stamping;
  - read_latest tolerating missing files / blank+malformed lines.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from omicairn import provenance, state
from omicairn.constants import (
    CODE_PROVENANCE_SCHEMA_VERSION,
    CODE_PROVENANCE_STAMP,
    CodeProvenanceKey,
    CodeProvenanceMode,
    PROVENANCE_LOG,
    PROVENANCE_SCHEMA_VERSION,
)


_EXPECTED_FIELDS = {
    "version",
    "recorded_at",
    "git_commit",
    "git_commit_short",
    "git_branch",
    "git_dirty",
    "git_diff_hash",
    "omicairn_version",
    "python_version",
    "repo_root",
}


def test_capture_returns_expected_fields():
    entry = provenance.capture_omicairn_version()
    assert _EXPECTED_FIELDS <= set(entry)
    assert entry["version"] == PROVENANCE_SCHEMA_VERSION
    # recorded_at is a UTC ISO8601 string with an offset.
    assert "T" in entry["recorded_at"]
    assert entry["recorded_at"].endswith("+00:00")
    # python_version is always available.
    assert entry["python_version"]


def test_capture_populates_git_fields_in_a_repo():
    """The OmiCairn source tree IS a git repo, so the real capture must
    yield non-"unknown" commit fields resolved FROM THE PACKAGE (not cwd)."""
    entry = provenance.capture_omicairn_version()
    assert entry["git_commit"] != "unknown"
    assert len(entry["git_commit"]) >= 7
    assert entry["git_commit_short"] != "unknown"
    assert entry["git_branch"] != "unknown"
    # repo_root resolves from the package file, not the test cwd.
    assert entry["repo_root"].endswith("engine")
    # dirty flag is a real bool (the working tree may or may not be dirty).
    assert isinstance(entry["git_dirty"], bool)


def test_capture_not_a_git_repo_falls_back(monkeypatch):
    """When git returns nothing (no repo / no binary), all git fields
    fall back to 'unknown' and capture does NOT raise."""
    monkeypatch.setattr(provenance, "_git", lambda *a, **k: None)
    entry = provenance.capture_omicairn_version()
    assert entry["git_commit"] == "unknown"
    assert entry["git_commit_short"] == "unknown"
    assert entry["git_branch"] == "unknown"
    # No porcelain output -> dirty is unknown (None), diff_hash None.
    assert entry["git_dirty"] is None
    assert entry["git_diff_hash"] is None
    # python_version still populated; omicairn_version never raises.
    assert entry["python_version"]
    assert entry["omicairn_version"]  # "unknown" or a real version string


def test_capture_missing_git_binary(monkeypatch):
    """A missing git binary (OSError from subprocess) is swallowed."""
    def _boom(*a, **k):
        raise FileNotFoundError("git: command not found")

    monkeypatch.setattr(provenance.subprocess, "run", _boom)
    entry = provenance.capture_omicairn_version()  # must not raise
    assert entry["git_commit"] == "unknown"
    assert entry["git_dirty"] is None


def test_capture_dirty_tree_sets_flag_and_hash(monkeypatch):
    """A non-empty porcelain output -> dirty=True with a diff_hash."""
    def _fake_git(args, cwd):
        if args[:2] == ["rev-parse", "HEAD"]:
            return "a" * 40
        if args == ["rev-parse", "--short", "HEAD"]:
            return "aaaaaaa"
        if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
            return "main"
        if args == ["status", "--porcelain"]:
            return " M omicairn/provenance.py"
        if args == ["diff", "HEAD"]:
            return "diff --git ..."
        return None

    monkeypatch.setattr(provenance, "_git", _fake_git)
    entry = provenance.capture_omicairn_version()
    assert entry["git_dirty"] is True
    assert entry["git_diff_hash"] is not None
    assert len(entry["git_diff_hash"]) == 12


def test_capture_clean_tree(monkeypatch):
    def _fake_git(args, cwd):
        if args == ["status", "--porcelain"]:
            return ""  # clean
        if args[:1] == ["rev-parse"]:
            return "deadbeef"
        return None

    monkeypatch.setattr(provenance, "_git", _fake_git)
    entry = provenance.capture_omicairn_version()
    assert entry["git_dirty"] is False
    assert entry["git_diff_hash"] is None


def test_capture_detached_head(monkeypatch):
    def _fake_git(args, cwd):
        if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
            return "HEAD"  # detached
        if args[:1] == ["rev-parse"]:
            return "cafe"
        if args == ["status", "--porcelain"]:
            return ""
        return None

    monkeypatch.setattr(provenance, "_git", _fake_git)
    entry = provenance.capture_omicairn_version()
    assert entry["git_branch"] == "DETACHED"


def test_record_invocation_creates_file_and_appends(tmp_path: pathlib.Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    log = ws / PROVENANCE_LOG
    assert not log.exists()

    e1 = provenance.record_invocation(ws, command="init")
    assert log.exists()
    assert e1["command"] == "init"

    # A SECOND invocation appends a new line — per-invocation semantics,
    # not overwrite.
    provenance.record_invocation(ws, command="run")
    lines = [l for l in log.read_text().splitlines() if l.strip()]
    assert len(lines) == 2
    commands = [json.loads(l)["command"] for l in lines]
    assert commands == ["init", "run"]


def test_record_invocation_stamps_state(tmp_path: pathlib.Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    # Seed a real STATE.md so the stamp has somewhere to land.
    s = state.initial_state("topic-x")
    state.write(ws / "STATE.md", s)

    entry = provenance.record_invocation(ws, command="run")

    s2 = state.read(ws / "STATE.md")
    assert "omicairn_version" in s2
    stamp = s2["omicairn_version"]
    assert stamp["command"] == "run"
    assert stamp["git_commit"] == entry["git_commit"]
    assert stamp["recorded_at"] == entry["recorded_at"]
    # started_at / updated_at are preserved and distinct from the stamp.
    assert "started_at" in s2
    assert "updated_at" in s2


def test_record_invocation_no_state_md_is_noop_safe(tmp_path: pathlib.Path):
    """When STATE.md is absent, record_invocation still appends the JSONL
    entry and does not raise."""
    ws = tmp_path / "ws"
    ws.mkdir()
    entry = provenance.record_invocation(ws, command="ideate")
    assert (ws / PROVENANCE_LOG).exists()
    assert entry["command"] == "ideate"
    assert not (ws / "STATE.md").exists()


def test_read_latest_returns_last_entry(tmp_path: pathlib.Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    provenance.record_invocation(ws, command="init")
    provenance.record_invocation(ws, command="run")
    latest = provenance.read_latest(ws)
    assert latest is not None
    assert latest["command"] == "run"


def test_read_latest_missing_file(tmp_path: pathlib.Path):
    assert provenance.read_latest(tmp_path / "nope") is None


def test_read_latest_tolerates_blank_and_malformed_lines(
    tmp_path: pathlib.Path,
):
    ws = tmp_path / "ws"
    ws.mkdir()
    log = ws / PROVENANCE_LOG
    log.write_text(
        json.dumps({"command": "init", "git_commit": "x"}) + "\n"
        "\n"
        "{not valid json\n"
        + json.dumps({"command": "run", "git_commit": "y"}) + "\n"
        "   \n"
    )
    latest = provenance.read_latest(ws)
    assert latest is not None
    assert latest["command"] == "run"


def test_state_stamp_formatting():
    entry = {
        "git_commit_short": "abc1234",
        "git_branch": "main",
        "git_dirty": True,
        "git_diff_hash": "deadbeef0011",
        "omicairn_version": "0.1.0",
        "python_version": "3.11.15",
        "recorded_at": "2026-06-07T00:00:00+00:00",
    }
    line = provenance._format_state_stamp(entry)
    assert "abc1234" in line
    assert "main" in line
    assert "dirty(deadbeef0011)" in line
    assert "0.1.0" in line


# ---------------------------------------------------------------------------
# write_stage_stamp — per-stage engine-SHA provenance (phase 1, additive)
# ---------------------------------------------------------------------------


def test_write_stage_stamp_records_engine_sha(tmp_path: pathlib.Path):
    stage_dir = tmp_path / "006_execute"
    stamp = provenance.write_stage_stamp(stage_dir)
    assert stamp is not None
    out = stage_dir / CODE_PROVENANCE_STAMP
    assert out.exists()
    on_disk = json.loads(out.read_text())
    assert on_disk[CodeProvenanceKey.SCHEMA_VERSION] == CODE_PROVENANCE_SCHEMA_VERSION
    # The engine snapshot carries the live OmiCairn git commit (we run from a repo).
    engine = on_disk[CodeProvenanceKey.ENGINE]
    assert engine["git_commit"] != "unknown"
    assert len(engine["git_commit"]) >= 7
    assert on_disk[CodeProvenanceKey.RECORDED_AT] == engine["recorded_at"]


def test_write_stage_stamp_defaults_to_git_pinned(tmp_path: pathlib.Path):
    stamp = provenance.write_stage_stamp(tmp_path / "002_execute")
    assert stamp is not None
    assert stamp[CodeProvenanceKey.MODE] == CodeProvenanceMode.GIT_PINNED


def test_write_stage_stamp_accepts_snapshot_mode(tmp_path: pathlib.Path):
    stamp = provenance.write_stage_stamp(
        tmp_path / "003_execute", mode=CodeProvenanceMode.SNAPSHOT_COPY
    )
    assert stamp is not None
    assert stamp[CodeProvenanceKey.MODE] == CodeProvenanceMode.SNAPSHOT_COPY


def test_write_stage_stamp_unknown_mode_falls_back_to_default(
    tmp_path: pathlib.Path,
):
    stamp = provenance.write_stage_stamp(tmp_path / "004_execute", mode="bogus")
    assert stamp is not None
    assert stamp[CodeProvenanceKey.MODE] == CodeProvenanceMode.DEFAULT


def test_write_stage_stamp_creates_missing_dir(tmp_path: pathlib.Path):
    # Stamp a dir that does not exist yet — write_stage_stamp mkdirs it.
    stage_dir = tmp_path / "nested" / "005_execute"
    stamp = provenance.write_stage_stamp(stage_dir)
    assert stamp is not None
    assert (stage_dir / CODE_PROVENANCE_STAMP).exists()
