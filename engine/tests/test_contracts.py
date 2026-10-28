"""Unit tests for omicairn.contracts.verify — the phase gate.

contracts.verify is the single check that decides whether a stage is
"done" or should be retried. A regression here is high-impact because
a false-pass lets a broken stage through and a false-fail spends the
per-phase iteration budget on stages that actually worked.

Previously contracts.verify was only exercised indirectly via
orchestrator dry-run tests, so boundary cases like "file exactly at
the 32-byte threshold" or ".flag allowed to be 1 byte" weren't
pinned. These tests fix that.
"""
from __future__ import annotations

import dataclasses
import pathlib

import pytest

from omicairn import contracts, phases


# ---------------------------------------------------------------------------
# Helper — minimal fake Phase
# ---------------------------------------------------------------------------

def _make_phase(*required: str) -> phases.Phase:
    return phases.Phase(
        name="synthetic",
        prompt_file="",
        required_outputs=tuple(required),
        optional_outputs=(),
        allowed_tools=(),
        timeout_seconds=60,
        description="test fixture",
    )


def _write(path: pathlib.Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


# ---------------------------------------------------------------------------
# All-present happy path
# ---------------------------------------------------------------------------

def test_all_required_files_present_passes(tmp_path):
    phase = _make_phase("summary.md", "result.json")
    _write(tmp_path / "summary.md", "A" * 64)
    _write(tmp_path / "result.json", "A" * 64)
    result = contracts.verify(phase, tmp_path)
    assert result.ok is True
    assert result.missing == []
    assert result.trivial == []
    assert result.summary() == "ok"


def test_empty_required_outputs_always_passes(tmp_path):
    """An empty required_outputs list trivially passes. This is the
    edge case for a phase that has no required files."""
    phase = _make_phase()
    result = contracts.verify(phase, tmp_path)
    assert result.ok is True


# ---------------------------------------------------------------------------
# Missing files
# ---------------------------------------------------------------------------

def test_single_missing_file_fails(tmp_path):
    phase = _make_phase("summary.md", "result.json")
    _write(tmp_path / "summary.md", "A" * 64)
    # result.json not written.
    result = contracts.verify(phase, tmp_path)
    assert result.ok is False
    assert result.missing == ["result.json"]
    assert result.trivial == []
    assert "missing: result.json" in result.summary()


def test_all_files_missing_fails(tmp_path):
    phase = _make_phase("summary.md", "result.json", "other.md")
    result = contracts.verify(phase, tmp_path)
    assert result.ok is False
    assert set(result.missing) == {"summary.md", "result.json", "other.md"}


# ---------------------------------------------------------------------------
# Trivial (< 32 bytes) non-flag files
# ---------------------------------------------------------------------------

def test_trivial_file_fails(tmp_path):
    phase = _make_phase("summary.md")
    _write(tmp_path / "summary.md", "tiny")  # 4 bytes
    result = contracts.verify(phase, tmp_path)
    assert result.ok is False
    assert result.missing == []
    assert result.trivial == ["summary.md"]
    assert "trivial" in result.summary()


def test_exactly_32_bytes_is_trivial(tmp_path):
    """The size check is strict-less-than (< 32): exactly 32 bytes
    should pass. If it starts failing, the threshold was changed."""
    phase = _make_phase("summary.md")
    _write(tmp_path / "summary.md", "A" * 32)
    result = contracts.verify(phase, tmp_path)
    assert result.ok is True, (
        "a 32-byte file must pass — the threshold is exclusive"
    )


def test_31_bytes_is_trivial(tmp_path):
    phase = _make_phase("summary.md")
    _write(tmp_path / "summary.md", "A" * 31)
    result = contracts.verify(phase, tmp_path)
    assert result.ok is False
    assert result.trivial == ["summary.md"]


# ---------------------------------------------------------------------------
# .flag files: 1 byte is fine, 0 bytes is trivial
# ---------------------------------------------------------------------------

def test_flag_file_with_one_byte_passes(tmp_path):
    """.flag files are allowed to be arbitrarily small down to 1 byte —
    the 32-byte threshold doesn't apply. A typical done.flag is '1\\n'."""
    phase = _make_phase("done.flag")
    _write(tmp_path / "done.flag", "1")  # exactly 1 byte
    result = contracts.verify(phase, tmp_path)
    assert result.ok is True
    assert result.trivial == []


def test_empty_flag_file_is_trivial(tmp_path):
    phase = _make_phase("done.flag")
    (tmp_path / "done.flag").write_text("")  # 0 bytes
    result = contracts.verify(phase, tmp_path)
    assert result.ok is False
    assert result.trivial == ["done.flag"]


def test_missing_flag_is_missing_not_trivial(tmp_path):
    phase = _make_phase("done.flag")
    result = contracts.verify(phase, tmp_path)
    assert result.missing == ["done.flag"]
    assert result.trivial == []


# ---------------------------------------------------------------------------
# Subdirectory required files
# ---------------------------------------------------------------------------

def test_nested_required_file(tmp_path):
    """phases.EXECUTE declares figures/legends.md — required_outputs
    paths must resolve relative to the stage dir, not flatten."""
    phase = _make_phase("figures/legends.md")
    _write(tmp_path / "figures" / "legends.md", "A" * 64)
    result = contracts.verify(phase, tmp_path)
    assert result.ok is True


def test_nested_required_file_missing(tmp_path):
    phase = _make_phase("figures/legends.md")
    # Create the directory but not the file — the existence check
    # resolves against the file, not the directory.
    (tmp_path / "figures").mkdir()
    result = contracts.verify(phase, tmp_path)
    assert result.missing == ["figures/legends.md"]


# ---------------------------------------------------------------------------
# Mixed missing + trivial + passing
# ---------------------------------------------------------------------------

def test_mixed_missing_and_trivial(tmp_path):
    phase = _make_phase("good.md", "tiny.md", "nothere.md")
    _write(tmp_path / "good.md", "A" * 100)
    _write(tmp_path / "tiny.md", "x")
    # nothere.md is missing
    result = contracts.verify(phase, tmp_path)
    assert result.ok is False
    assert result.missing == ["nothere.md"]
    assert result.trivial == ["tiny.md"]
    s = result.summary()
    assert "missing: nothere.md" in s
    assert "trivial" in s and "tiny.md" in s
    assert ";" in s  # both categories joined


# ---------------------------------------------------------------------------
# Integration: real phase objects against a synthetic stage dir
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "phase",
    [phases.SCOPE, phases.PLAN, phases.EXECUTE, phases.REVIEW],
)
def test_real_phase_fails_on_empty_stage_dir(phase, tmp_path):
    """Every real LLM phase must FAIL contract verification on an empty
    stage dir. This is a sanity check: a phase that accidentally had
    empty required_outputs would silently pass, which would be
    catastrophic."""
    result = contracts.verify(phase, tmp_path)
    assert result.ok is False
    assert len(result.missing) > 0, (
        f"{phase.name} has no required_outputs — empty-dir verify "
        f"would pass, which is almost certainly a bug"
    )
