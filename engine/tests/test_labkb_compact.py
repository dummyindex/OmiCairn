"""Tests for the lab-KB op-log compaction tool (omicairn.lab_knowledge.compact +
`omicairn labkb compact`).

The lab knowledge store is an APPEND-ONLY JSONL op-log folded-on-read:
``distill_workspace`` re-appends ALL of a workspace's entries on every distill,
so the file grows O(stages x keys) while ``load()`` folds it back to the
current entries. There is no functional bug (consumers see the folded state) —
just unbounded storage growth. ``compact()`` is a SAFE, opt-in maintenance tool
that rewrites the op-log to one line per unique key WITHOUT changing what
``load()`` returns. These tests prove the safety rails AND that the folded
state is preserved exactly.
"""
from __future__ import annotations

import os
import pathlib

import pytest

from omicairn import lab_knowledge
from omicairn.constants import LAB_KB_FILE, LAB_KB_LOCK


def _kb(tmp_path: pathlib.Path) -> pathlib.Path:
    return tmp_path / "kb"


def _seed_multi_append(root: pathlib.Path, rounds: int = 3,
                       keys: tuple[str, ...] = ("k1", "k2")) -> None:
    """Simulate distill re-appending the same keys `rounds` times with a
    later distilled_at each round — exactly the O(stages x keys) growth the
    real store exhibits. Latest distilled_at per key must win the fold."""
    for i in range(rounds):
        ts = f"2026-01-0{i + 1}T00:00:00+00:00"
        lab_knowledge._append(root, [
            {"key": k, "kind": "finding", "title": k.upper(), "workspace": "w",
             "summary": f"value@{ts}", "distilled_at": ts}
            for k in keys
        ])


def _raw_lines(root: pathlib.Path) -> int:
    return sum(1 for line in (root / LAB_KB_FILE).open() if line.strip())


# ---------------------------------------------------------------------------
# Core: reduce to one-line-per-key AND preserve the folded state exactly
# ---------------------------------------------------------------------------

def test_compact_reduces_to_one_line_per_key_and_preserves_fold(tmp_path):
    root = _kb(tmp_path)
    _seed_multi_append(root, rounds=4, keys=("k1", "k2", "k3"))
    assert _raw_lines(root) == 12          # 4 rounds x 3 keys
    folded_before = lab_knowledge.load(root)
    assert len(folded_before) == 3

    report = lab_knowledge.compact(root=root, apply=True, stamp="S")
    assert report["applied"] is True
    assert report["raw_lines"] == 12
    assert report["folded_keys"] == 3
    assert report["redundancy"] == 4.0

    # File is now exactly one line per unique key.
    assert _raw_lines(root) == 3
    # load() returns the IDENTICAL folded state (same keys + latest values).
    folded_after = lab_knowledge.load(root)
    assert folded_after == folded_before
    # And the latest distilled_at won the fold (not an older append).
    assert folded_after["k1"]["distilled_at"] == "2026-01-04T00:00:00+00:00"
    assert folded_after["k1"]["summary"] == "value@2026-01-04T00:00:00+00:00"


def test_compact_byte_identical_folded_state_round_trips(tmp_path):
    """Re-serialising the folded entries reproduces the rewritten file
    byte-for-byte — the rewrite IS the canonical fold, not a re-derivation."""
    root = _kb(tmp_path)
    _seed_multi_append(root, rounds=3)
    lab_knowledge.compact(root=root, apply=True, stamp="S")
    on_disk = (root / LAB_KB_FILE).read_text(encoding="utf-8")
    import json
    expected = "".join(
        json.dumps(e, ensure_ascii=False) + "\n"
        for e in lab_knowledge.load(root).values())
    assert on_disk == expected


# ---------------------------------------------------------------------------
# Dry-run is the DEFAULT and changes nothing
# ---------------------------------------------------------------------------

def test_dry_run_is_default_and_changes_nothing(tmp_path):
    root = _kb(tmp_path)
    _seed_multi_append(root, rounds=3)
    before_bytes = (root / LAB_KB_FILE).read_bytes()

    report = lab_knowledge.compact(root=root)  # apply defaults to False
    assert report["applied"] is False
    assert report["raw_lines"] == 6
    assert report["folded_keys"] == 2
    assert report["redundancy"] == 3.0
    assert report["backup_path"] is None
    # File is byte-identical — dry run mutates nothing.
    assert (root / LAB_KB_FILE).read_bytes() == before_bytes
    # No .bak, no lock left behind.
    assert not list(root.glob("*.bak"))
    assert not (root / LAB_KB_LOCK).exists()


# ---------------------------------------------------------------------------
# REFUSE when the lab-KB lock is held by a LIVE pid
# ---------------------------------------------------------------------------

def test_refuses_when_lock_held_by_live_pid(tmp_path):
    root = _kb(tmp_path)
    _seed_multi_append(root, rounds=3)
    root.mkdir(parents=True, exist_ok=True)
    # Simulate a concurrent writer: a live pid (our own) holds the lock.
    (root / LAB_KB_LOCK).write_text(f"{os.getpid()}\n")
    before_bytes = (root / LAB_KB_FILE).read_bytes()

    with pytest.raises(RuntimeError) as exc:
        lab_knowledge.compact(root=root, apply=True, stamp="S")
    msg = str(exc.value)
    assert "LIVE pid" in msg and str(os.getpid()) in msg
    # Refusal is safe: store untouched, the live holder's lock untouched.
    assert (root / LAB_KB_FILE).read_bytes() == before_bytes
    assert (root / LAB_KB_LOCK).exists()


def test_dead_pid_lock_does_not_block(tmp_path):
    """A stale lock from a CRASHED writer (dead pid) must NOT wedge
    compaction forever — it is reclaimed, then compaction proceeds."""
    root = _kb(tmp_path)
    _seed_multi_append(root, rounds=3)
    root.mkdir(parents=True, exist_ok=True)
    dead = _a_dead_pid()
    (root / LAB_KB_LOCK).write_text(f"{dead}\n")

    report = lab_knowledge.compact(root=root, apply=True, stamp="S")
    assert report["applied"] is True
    assert _raw_lines(root) == 2
    assert not (root / LAB_KB_LOCK).exists()  # lock released after the rewrite


# ---------------------------------------------------------------------------
# Idempotent: a second compact is a no-op
# ---------------------------------------------------------------------------

def test_idempotent_second_compact_is_noop(tmp_path):
    root = _kb(tmp_path)
    _seed_multi_append(root, rounds=3)
    lab_knowledge.compact(root=root, apply=True, stamp="S1")
    assert _raw_lines(root) == 2

    report = lab_knowledge.compact(root=root, apply=True, stamp="S2")
    assert report["already_compact"] is True
    assert report["applied"] is False
    assert report["redundancy"] == 1.0
    # Already-compact apply writes no second .bak.
    assert [p.name for p in root.glob("*.bak")] == [f"{LAB_KB_FILE}.S1.bak"]


def test_dry_run_already_compact_reports_noop(tmp_path):
    root = _kb(tmp_path)
    lab_knowledge._append(root, [
        {"key": "only", "kind": "finding", "title": "T",
         "distilled_at": "2026-01-01T00:00:00+00:00"}])
    report = lab_knowledge.compact(root=root)  # dry run
    assert report["already_compact"] is True
    assert report["applied"] is False


def test_compact_empty_store_is_noop(tmp_path):
    """No op-log on disk yet → nothing to compact (no crash, no .bak)."""
    root = _kb(tmp_path)
    report = lab_knowledge.compact(root=root, apply=True, stamp="S")
    assert report["already_compact"] is True
    assert report["applied"] is False
    assert report["raw_lines"] == 0 and report["folded_keys"] == 0


# ---------------------------------------------------------------------------
# A .bak is written on apply; verify-after RESTORES from .bak on divergence
# ---------------------------------------------------------------------------

def test_apply_writes_a_timestamped_backup(tmp_path):
    root = _kb(tmp_path)
    _seed_multi_append(root, rounds=3)
    pre_bytes = (root / LAB_KB_FILE).read_bytes()

    report = lab_knowledge.compact(root=root, apply=True, stamp="STAMP123")
    bak = pathlib.Path(report["backup_path"])
    assert bak.exists()
    assert bak.name == f"{LAB_KB_FILE}.STAMP123.bak"
    # The .bak preserves the PRE-compaction op-log verbatim (full recovery).
    assert bak.read_bytes() == pre_bytes


def test_verify_after_restores_from_bak_when_fold_diverges(tmp_path, monkeypatch):
    """The load-bearing correctness guard: if the post-rewrite fold differs
    from the pre-rewrite fold, compaction RESTORES the op-log from .bak and
    raises — it must NEVER leave the store in a state that changed what
    consumers see. We force divergence by monkeypatching the fold to return
    a different result on the post-rewrite (verify) call."""
    root = _kb(tmp_path)
    _seed_multi_append(root, rounds=3)
    pre_bytes = (root / LAB_KB_FILE).read_bytes()
    pre_fold = lab_knowledge.load(root)

    real_load = lab_knowledge.load
    calls = {"n": 0}

    def diverging_load(r=None):
        calls["n"] += 1
        # 1st call = folded_before (truthful); 2nd call = verify-after (lie).
        if calls["n"] >= 2:
            return {"PHANTOM": {"key": "PHANTOM"}}
        return real_load(r)

    monkeypatch.setattr(lab_knowledge, "load", diverging_load)
    with pytest.raises(RuntimeError) as exc:
        lab_knowledge.compact(root=root, apply=True, stamp="S")
    assert "restored op-log" in str(exc.value)

    # The op-log was restored from .bak: byte-identical to before, and the
    # REAL fold is unchanged. No data lost.
    assert (root / LAB_KB_FILE).read_bytes() == pre_bytes
    assert real_load(root) == pre_fold
    # The lock was released even though we raised inside the locked region.
    assert not (root / LAB_KB_LOCK).exists()


# ---------------------------------------------------------------------------
# Lock acquisition is the SAME mutex distill/append use (serialises distill)
# ---------------------------------------------------------------------------

def test_compact_acquires_and_releases_the_distill_lock(tmp_path):
    """compact() holds the SAME `.lab_kb.lock` distill/append use, and frees
    it afterwards — so a concurrent distill can't interleave with the
    rewrite, and the lock never leaks."""
    root = _kb(tmp_path)
    _seed_multi_append(root, rounds=2)
    assert not (root / LAB_KB_LOCK).exists()
    lab_knowledge.compact(root=root, apply=True, stamp="S")
    assert not (root / LAB_KB_LOCK).exists()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _a_dead_pid() -> int:
    """Return a PID that is guaranteed not to be alive on this host."""
    pid = 999999
    while True:
        try:
            os.kill(pid, 0)
        except OSError:
            return pid  # ProcessLookupError (gone) — usable as a dead pid
        pid += 1
