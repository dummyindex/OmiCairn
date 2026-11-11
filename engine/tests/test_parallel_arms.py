"""Tests for omicairn.parallel_arms — the within-EXECUTE parallel-arm contract
checker (W32).

No network, no subprocess for the pure-Python paths; the CLI is exercised
via ``runpy``/``subprocess`` at the end. Every validate_manifest invariant
(disjoint writes, distinct seeds, GPU serialization, reserved-host routing, +
the structural bounds) has a positive and a negative case; validate_recorded
is exercised for each advisory finding; record_manifest for shape +
best-effort; the CLI for exit-code + stdout.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile

import pytest

from omicairn import parallel_arms as pa
from omicairn.constants import (
    ARM_MANIFEST_FILE,
    ARM_PROVENANCE_FRAGMENT,
    ARM_PROVENANCE_LOG,
    DEFAULT_GPU_SLOTS,
    EVENTS_LOG,
    GPU_ROUTED_HOSTS,
    MAX_IN_STAGE_PARALLEL,
    RESERVED_ARM_HOST,
    PARALLEL_ARMS_SCHEMA_VERSION,
    PARALLEL_ARMS_STAMP_FILE,
    PROVENANCE_LOG,
    RESERVED_STAGE_OUTPUT_NAMES,
    ParallelArmIndependenceGate,
)

_GPU_HOST = GPU_ROUTED_HOSTS[0]

_GATE = ParallelArmIndependenceGate.GATE_ID


def _arm(arm_id, seed, output_dir, gpu=False, host=None):
    d = {"arm_id": arm_id, "seed": seed, "output_dir": output_dir, "gpu": gpu}
    if host is not None:
        d["host"] = host
    return d


# ---------------------------------------------------------------------------
# validate_manifest — happy path + the four hard invariants
# ---------------------------------------------------------------------------

def test_happy_path_four_cpu_arms():
    arms = [_arm(c, i, f"arms/{c}") for i, c in enumerate("abcd")]
    v = pa.validate_manifest({"version": 1, "arms": arms, "max_parallel": 4, "gpu_slots": 1})
    assert v.ok, v.errors
    assert len(v.normalized) == 4
    assert len(v.provenance["arms"]) == 4
    assert v.provenance["gate"] == _GATE
    assert v.provenance["verdict_ok"] is True


def test_duplicate_seed_rejected():
    v = pa.validate_manifest([_arm("a", 1, "arms/a"), _arm("b", 1, "arms/b")])
    assert not v.ok
    assert any("seed" in e for e in v.errors)


def test_missing_seed_rejected():
    v = pa.validate_manifest([{"arm_id": "a", "output_dir": "arms/a"}])
    assert not v.ok
    assert any("seed" in e for e in v.errors)


def test_none_seed_rejected():
    v = pa.validate_manifest([_arm("a", None, "arms/a")])
    assert not v.ok
    assert any("seed" in e for e in v.errors)


def test_non_int_seed_rejected():
    v = pa.validate_manifest([_arm("a", "one", "arms/a")])
    assert not v.ok
    assert any("int" in e for e in v.errors)


def test_bool_seed_rejected_not_treated_as_int():
    # bool is an int subclass — must be rejected explicitly.
    v = pa.validate_manifest([_arm("a", True, "arms/a")])
    assert not v.ok
    assert any("int" in e for e in v.errors)


def test_identical_output_dir_rejected():
    v = pa.validate_manifest([_arm("a", 1, "arms/x"), _arm("b", 2, "arms/x")])
    assert not v.ok
    assert any("overlapping output dirs" in e for e in v.errors)


def test_nested_output_dir_rejected():
    v = pa.validate_manifest([_arm("a", 1, "arms/a"), _arm("b", 2, "arms/a/b")])
    assert not v.ok
    assert any("overlapping" in e for e in v.errors)


def test_absolute_output_dir_rejected():
    v = pa.validate_manifest([_arm("a", 1, "/tmp/x")])
    assert not v.ok
    assert any("absolute" in e for e in v.errors)


def test_dotdot_escape_output_dir_rejected():
    v = pa.validate_manifest([_arm("a", 1, "../escape")])
    assert not v.ok
    assert any(".." in e for e in v.errors)


@pytest.mark.parametrize("reserved", ["results.json", "analysis.md", "code", "figures", "done.flag"])
def test_reserved_file_collision_rejected(reserved):
    v = pa.validate_manifest([_arm("a", 1, reserved)])
    assert not v.ok
    assert any("reserved" in e for e in v.errors)


def test_reserved_subdir_prefix_collision_rejected():
    # An arm writing under code/ or figures/ would race the canonical subdirs.
    v = pa.validate_manifest([_arm("a", 1, "code/arm_a")])
    assert not v.ok
    assert any("reserved" in e for e in v.errors)


def test_two_gpu_arms_over_gpu_slots_rejected():
    v = pa.validate_manifest(
        [_arm("a", 1, "arms/a", gpu=True), _arm("b", 2, "arms/b", gpu=True)]
    )
    assert not v.ok
    assert any("GPU" in e and "serialize" in e.lower() for e in v.errors)


def test_two_gpu_arms_with_gpu_slots_2_ok():
    v = pa.validate_manifest(
        [_arm("a", 1, "arms/a", gpu=True), _arm("b", 2, "arms/b", gpu=True)],
        gpu_slots=2,
    )
    assert v.ok, v.errors


def test_one_gpu_three_cpu_gpu_slots_1_ok():
    arms = [_arm("g", 0, "arms/g", gpu=True)] + [_arm(c, i + 1, f"arms/{c}") for i, c in enumerate("abc")]
    v = pa.validate_manifest(arms)  # default gpu_slots=1
    assert v.ok, v.errors


def test_empty_manifest_rejected():
    v = pa.validate_manifest([])
    assert not v.ok
    assert any("no arms" in e for e in v.errors)


def test_duplicate_arm_id_rejected():
    v = pa.validate_manifest([_arm("a", 1, "arms/a"), _arm("a", 2, "arms/b")])
    assert not v.ok
    assert any("duplicate arm_id" in e for e in v.errors)


def test_max_parallel_over_cap_rejected():
    arms = [_arm("a", 1, "arms/a")]
    v = pa.validate_manifest({"arms": arms, "max_parallel": MAX_IN_STAGE_PARALLEL + 1})
    assert not v.ok
    assert any("max_parallel" in e for e in v.errors)


def test_reserved_host_rejected():
    v = pa.validate_manifest([_arm("a", 1, "arms/a", host=RESERVED_ARM_HOST)])
    assert not v.ok
    assert any(RESERVED_ARM_HOST in e for e in v.errors)


def test_non_reserved_cpu_host_ok():
    # A non-GPU, non-reserved host (a CPU box) is fine.
    v = pa.validate_manifest([_arm("a", 1, "arms/a", host="cpu-host-2")])
    assert v.ok, v.errors


# ---------------------------------------------------------------------------
# ISSUE 1 — the concurrency-critical SHARED LOGS are reserved output names
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "shared_log",
    [PROVENANCE_LOG, EVENTS_LOG, ARM_MANIFEST_FILE, PARALLEL_ARMS_STAMP_FILE, ARM_PROVENANCE_LOG],
)
def test_arm_naming_shared_workspace_log_rejected(shared_log):
    # provenance.jsonl / events.jsonl are the workspace-level append-only logs
    # the orchestrator/other stages race-append to; the W32 control files must
    # also be off-limits. An arm naming any as its output_dir must be REJECTED.
    v = pa.validate_manifest([_arm("a", 1, shared_log)])
    assert not v.ok
    assert any("reserved" in e for e in v.errors), v.errors


def test_shared_logs_are_in_reserved_names():
    # Belt-and-braces: the constants tuple itself carries the shared logs.
    for name in (PROVENANCE_LOG, EVENTS_LOG, ARM_MANIFEST_FILE,
                 PARALLEL_ARMS_STAMP_FILE, ARM_PROVENANCE_LOG):
        assert name in RESERVED_STAGE_OUTPUT_NAMES


# ---------------------------------------------------------------------------
# ISSUE 3 — gpu_slots is NOT self-authorizing (manifest-declared value ignored)
# ---------------------------------------------------------------------------

def test_manifest_declared_gpu_slots_does_not_self_authorize():
    # 8 GPU arms + a self-declared gpu_slots:8 must NOT pass: the manifest
    # cannot authorize its own concurrent-GPU budget (clamped to default 1).
    arms = [_arm(c, i, f"arms/{c}", gpu=True) for i, c in enumerate("abcdefgh")]
    v = pa.validate_manifest({"arms": arms, "max_parallel": 8, "gpu_slots": 8})
    assert not v.ok
    assert any("exceed gpu_slots=1" in e for e in v.errors), v.errors
    assert any("clamping to DEFAULT_GPU_SLOTS" in w for w in v.warnings), v.warnings


def test_manifest_gpu_slots_999_ignored():
    v = pa.validate_manifest(
        {"arms": [_arm("a", 1, "arms/a", gpu=True), _arm("b", 2, "arms/b", gpu=True)],
         "gpu_slots": 999}
    )
    assert not v.ok, "a self-declared gpu_slots must not raise the budget"


def test_external_gpu_slots_still_permits_concurrency():
    # The ONLY legitimate way to raise the budget: an EXTERNAL gpu_slots arg.
    v = pa.validate_manifest(
        {"arms": [_arm("a", 1, "arms/a", gpu=True), _arm("b", 2, "arms/b", gpu=True)],
         "gpu_slots": 1},
        gpu_slots=2,
    )
    assert v.ok, v.errors
    assert v.provenance["gpu_slots_externally_supplied"] is True


def test_cli_default_clamps_manifest_gpu_slots():
    with tempfile.TemporaryDirectory() as t:
        p = pathlib.Path(t) / "selfauth.json"
        p.write_text(json.dumps({
            "arms": [_arm("a", 1, "arms/a", gpu=True), _arm("b", 2, "arms/b", gpu=True)],
            "gpu_slots": 8,
        }))
        # CLI passes NO --gpu-slots → manifest's gpu_slots:8 is ignored → FAIL.
        proc = subprocess.run(
            [sys.executable, "-m", "omicairn.parallel_arms", "validate", str(p)],
            capture_output=True, text=True,
        )
        assert proc.returncode == 1, proc.stdout
        assert "FAIL" in proc.stdout


# ---------------------------------------------------------------------------
# ISSUE 4 — GPU work cannot escape the gate by omitting the gpu flag on a
#           GPU-routed host
# ---------------------------------------------------------------------------

def test_gpu_host_arm_without_gpu_flag_rejected():
    # gpu:false but routed to a known GPU host — must be rejected (mislabel).
    v = pa.validate_manifest([_arm("a", 1, "arms/a", gpu=False, host=_GPU_HOST)])
    assert not v.ok
    assert any("gpu=True" in e for e in v.errors), v.errors


def test_many_gpu_host_arms_without_flag_do_not_bypass():
    # 8 arms gpu:false on the GPU host, default gpu_slots=1 — the exact bypass
    # the reviewer flagged. Must FAIL (both the mislabel reject AND the count).
    arms = [_arm(c, i, f"arms/{c}", gpu=False, host=_GPU_HOST) for i, c in enumerate("abcdefgh")]
    v = pa.validate_manifest({"arms": arms, "max_parallel": 8})
    assert not v.ok
    assert any("gpu=True" in e for e in v.errors), v.errors


def test_gpu_host_arm_with_gpu_flag_counted_against_slots():
    # Correctly-labeled: 2 GPU-host arms marked gpu:true, default slots=1 → the
    # concurrent-GPU count fires (they are counted).
    v = pa.validate_manifest(
        [_arm("a", 1, "arms/a", gpu=True, host=_GPU_HOST),
         _arm("b", 2, "arms/b", gpu=True, host=_GPU_HOST)]
    )
    assert not v.ok
    assert any("GPU arm(s)" in e and "exceed gpu_slots" in e for e in v.errors), v.errors


def test_single_gpu_host_arm_marked_ok():
    v = pa.validate_manifest([_arm("a", 1, "arms/a", gpu=True, host=_GPU_HOST)])
    assert v.ok, v.errors


# ---------------------------------------------------------------------------
# load_manifest — accepts list, dict, path; best-effort on malformed/absent
# ---------------------------------------------------------------------------

def test_accepts_list_of_dicts():
    v = pa.validate_manifest([_arm("a", 1, "arms/a")])
    assert v.ok


def test_accepts_path_arg():
    with tempfile.TemporaryDirectory() as t:
        p = pathlib.Path(t) / "m.json"
        p.write_text(json.dumps({"arms": [_arm("a", 1, "arms/a")]}))
        v = pa.validate_manifest(p)
        assert v.ok, v.errors


def test_absent_json_path_fails_no_raise():
    v = pa.validate_manifest("/no/such/manifest.json")
    assert not v.ok
    assert any("not found" in e for e in v.errors)


def test_malformed_json_fails_no_raise():
    with tempfile.TemporaryDirectory() as t:
        p = pathlib.Path(t) / "m.json"
        p.write_text("{not valid json")
        v = pa.validate_manifest(p)
        assert not v.ok
        assert any("does not parse" in e for e in v.errors)


def test_dict_missing_arms_key_fails():
    v = pa.validate_manifest({"version": 1})
    assert not v.ok


# ---------------------------------------------------------------------------
# validate_recorded — advisory (WARN-only) post-hoc re-check
# ---------------------------------------------------------------------------

def _write_provenance(stage: pathlib.Path, records):
    """Write one per-arm provenance FRAGMENT into each arm's own dir
    (arms/<arm_id>/arm_provenance.json) — the race-free per-writer layout the
    module now reads (NOT a shared append-only jsonl)."""
    for r in records:
        arm_id = r.get("arm_id", "?")
        d = stage / "arms" / arm_id
        d.mkdir(parents=True, exist_ok=True)
        (d / ARM_PROVENANCE_FRAGMENT).write_text(json.dumps(r))


def test_recorded_clean_ok_no_warnings():
    with tempfile.TemporaryDirectory() as t:
        stage = pathlib.Path(t)
        for c in "ab":
            d = stage / "arms" / c
            d.mkdir(parents=True)
            (d / "frag.json").write_text("{}")
        _write_provenance(stage, [
            {"arm_id": "a", "seed": 1, "output_dir": "arms/a"},
            {"arm_id": "b", "seed": 2, "output_dir": "arms/b"},
        ])
        r = pa.validate_recorded(stage)
        assert r.ok
        assert r.warnings == []


def test_recorded_same_dir_warns():
    with tempfile.TemporaryDirectory() as t:
        stage = pathlib.Path(t)
        d = stage / "arms" / "a"
        d.mkdir(parents=True)
        (d / "frag.json").write_text("{}")
        _write_provenance(stage, [
            {"arm_id": "a", "seed": 1, "output_dir": "arms/a"},
            {"arm_id": "b", "seed": 2, "output_dir": "arms/a"},
        ])
        r = pa.validate_recorded(stage)
        assert r.ok  # advisory — never flips ok
        assert any("materialized dir" in w for w in r.warnings)


def test_recorded_duplicate_seed_warns():
    with tempfile.TemporaryDirectory() as t:
        stage = pathlib.Path(t)
        for c in "ab":
            d = stage / "arms" / c
            d.mkdir(parents=True)
            (d / "frag.json").write_text("{}")
        _write_provenance(stage, [
            {"arm_id": "a", "seed": 7, "output_dir": "arms/a"},
            {"arm_id": "b", "seed": 7, "output_dir": "arms/b"},
        ])
        r = pa.validate_recorded(stage)
        assert r.ok
        assert any("seed 7" in w for w in r.warnings)


def test_recorded_empty_arm_dir_warns():
    with tempfile.TemporaryDirectory() as t:
        stage = pathlib.Path(t)
        (stage / "arms" / "a").mkdir(parents=True)
        (stage / "arms" / "a" / "frag.json").write_text("{}")
        # arm b's fragment references an output_dir absent on disk (a FAILED
        # arm) — the fragment itself lives in arms/b but declares its results
        # dir as arms/bmissing which was never created.
        _write_provenance(stage, [
            {"arm_id": "a", "seed": 1, "output_dir": "arms/a"},
            {"arm_id": "b", "seed": 2, "output_dir": "arms/bmissing"},
        ])
        r = pa.validate_recorded(stage)
        assert r.ok
        assert any("FAILED arm" in w for w in r.warnings)


def test_recorded_overlapping_gpu_windows_warns():
    with tempfile.TemporaryDirectory() as t:
        stage = pathlib.Path(t)
        for c in "ab":
            d = stage / "arms" / c
            d.mkdir(parents=True)
            (d / "frag.json").write_text("{}")
        _write_provenance(stage, [
            {"arm_id": "a", "seed": 1, "output_dir": "arms/a", "gpu": True,
             "launched_at": "2026-07-01T00:00:00+00:00", "finished_at": "2026-07-01T01:00:00+00:00"},
            {"arm_id": "b", "seed": 2, "output_dir": "arms/b", "gpu": True,
             "launched_at": "2026-07-01T00:30:00+00:00", "finished_at": "2026-07-01T01:30:00+00:00"},
        ])
        r = pa.validate_recorded(stage)
        assert r.ok
        assert any("overlapping launch windows" in w for w in r.warnings)


def test_recorded_same_gpu_devices_warns():
    with tempfile.TemporaryDirectory() as t:
        stage = pathlib.Path(t)
        for c in "ab":
            d = stage / "arms" / c
            d.mkdir(parents=True)
            (d / "frag.json").write_text("{}")
        _write_provenance(stage, [
            {"arm_id": "a", "seed": 1, "output_dir": "arms/a", "gpu": True,
             "launched_at": "2026-07-01T00:00:00+00:00", "finished_at": "2026-07-01T00:10:00+00:00",
             "gpu_devices": [0]},
            {"arm_id": "b", "seed": 2, "output_dir": "arms/b", "gpu": True,
             "launched_at": "2026-07-01T01:00:00+00:00", "finished_at": "2026-07-01T01:10:00+00:00",
             "gpu_devices": [0]},
        ])
        r = pa.validate_recorded(stage)
        assert r.ok
        assert any("gpu_devices" in w for w in r.warnings)


def test_recorded_absent_log_is_noop():
    with tempfile.TemporaryDirectory() as t:
        r = pa.validate_recorded(pathlib.Path(t))
        assert r.ok
        assert r.warnings == []


# ---------------------------------------------------------------------------
# ISSUE 2 — per-arm provenance FRAGMENTS (race-free), not a shared append-log
# ---------------------------------------------------------------------------

def test_recorded_reads_union_of_per_arm_fragments():
    # Two arms each write their OWN arms/<id>/arm_provenance.json; the re-check
    # reads the union and finds them clean (distinct seeds, distinct dirs).
    with tempfile.TemporaryDirectory() as t:
        stage = pathlib.Path(t)
        _write_provenance(stage, [
            {"arm_id": "a", "seed": 1, "output_dir": "arms/a"},
            {"arm_id": "b", "seed": 2, "output_dir": "arms/b"},
        ])
        recs = pa._read_provenance_records(stage)
        assert recs is not None and len(recs) == 2
        r = pa.validate_recorded(stage)
        assert r.ok
        assert r.warnings == []


def test_recorded_malformed_single_fragment_does_not_drop_others():
    # A torn/partial fragment for ONE arm must not silently take down the
    # others — it is skipped (surfaced as a failed arm via empty-dir logic),
    # and the well-formed sibling is still read.
    with tempfile.TemporaryDirectory() as t:
        stage = pathlib.Path(t)
        _write_provenance(stage, [{"arm_id": "a", "seed": 1, "output_dir": "arms/a"}])
        bad = stage / "arms" / "b"
        bad.mkdir(parents=True)
        (bad / ARM_PROVENANCE_FRAGMENT).write_text('{"arm_id": "b", "seed":')  # torn
        recs = pa._read_provenance_records(stage)
        # Fragment b is skipped (malformed), a is kept — but the log is present.
        assert recs is not None
        assert any(r.get("arm_id") == "a" for r in recs)
        assert all(r.get("arm_id") != "b" for r in recs)


def test_no_shared_jsonl_append_log_written():
    # The engine must NOT create a shared arm_provenance.jsonl append-log.
    with tempfile.TemporaryDirectory() as t:
        stage = pathlib.Path(t) / "stage"
        m = pa.load_manifest({"arms": [_arm("a", 1, "arms/a")]})
        v = pa.validate_manifest(m, stage_dir=stage)
        pa.record_manifest(stage, m, v)
        assert not (stage / ARM_PROVENANCE_LOG).exists()


# ---------------------------------------------------------------------------
# record_manifest — shape + best-effort
# ---------------------------------------------------------------------------

def test_record_manifest_writes_stamp_and_provenance():
    with tempfile.TemporaryDirectory() as t:
        stage = pathlib.Path(t) / "stage"
        m = pa.load_manifest({"version": 1, "arms": [_arm("a", 1, "arms/a")], "gpu_slots": 1})
        v = pa.validate_manifest(m, stage_dir=stage)
        stamp = pa.record_manifest(stage, m, v)
        assert stamp["schema_version"] == PARALLEL_ARMS_SCHEMA_VERSION
        assert stamp["arm_count"] == 1
        assert stamp["verdict_ok"] is True
        assert (stage / PARALLEL_ARMS_STAMP_FILE).exists()
        prov = (stage / PROVENANCE_LOG).read_text().strip()
        assert '"command": "parallel_arms"' in prov


def test_record_manifest_best_effort_on_bad_dir(monkeypatch):
    # A dir that cannot be created (parent is a file) must not raise.
    with tempfile.TemporaryDirectory() as t:
        blocker = pathlib.Path(t) / "afile"
        blocker.write_text("x")
        stage = blocker / "sub"  # mkdir will fail (parent is a file)
        m = pa.load_manifest({"arms": [_arm("a", 1, "arms/a")]})
        v = pa.validate_manifest(m)
        stamp = pa.record_manifest(stage, m, v)  # must not raise
        assert stamp["arm_count"] == 1


# ---------------------------------------------------------------------------
# human_summary + CLI
# ---------------------------------------------------------------------------

def test_human_summary_ok_and_fail():
    ok = pa.validate_manifest([_arm("a", 1, "arms/a")])
    assert ok.human_summary().startswith("OK")
    bad = pa.validate_manifest([_arm("a", 1, "arms/a"), _arm("b", 1, "arms/b")])
    assert bad.human_summary().startswith("FAIL")


def test_cli_validate_good_exits_zero():
    with tempfile.TemporaryDirectory() as t:
        p = pathlib.Path(t) / "good.json"
        p.write_text(json.dumps({"arms": [_arm("a", 1, "arms/a"), _arm("b", 2, "arms/b")]}))
        proc = subprocess.run(
            [sys.executable, "-m", "omicairn.parallel_arms", "validate", str(p)],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert "OK" in proc.stdout


def test_cli_validate_bad_exits_nonzero():
    with tempfile.TemporaryDirectory() as t:
        p = pathlib.Path(t) / "bad.json"
        p.write_text(json.dumps({"arms": [_arm("a", 1, "arms/a"), _arm("b", 1, "arms/a")]}))
        proc = subprocess.run(
            [sys.executable, "-m", "omicairn.parallel_arms", "validate", str(p)],
            capture_output=True, text=True,
        )
        assert proc.returncode == 1
        assert "FAIL" in proc.stdout


def test_cli_gpu_slots_flag():
    with tempfile.TemporaryDirectory() as t:
        p = pathlib.Path(t) / "gpu.json"
        p.write_text(json.dumps({
            "arms": [_arm("a", 1, "arms/a", gpu=True), _arm("b", 2, "arms/b", gpu=True)]
        }))
        # default gpu_slots=1 → fail
        r1 = subprocess.run(
            [sys.executable, "-m", "omicairn.parallel_arms", "validate", str(p)],
            capture_output=True, text=True,
        )
        assert r1.returncode == 1
        # --gpu-slots 2 → ok
        r2 = subprocess.run(
            [sys.executable, "-m", "omicairn.parallel_arms", "validate", str(p), "--gpu-slots", "2"],
            capture_output=True, text=True,
        )
        assert r2.returncode == 0, r2.stdout + r2.stderr
