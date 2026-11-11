"""Tests for the cross-corpus gate-suite replay (`omicairn audit --replay-gates`).

Mirrors the test_cross_check.py / test_cli.py fixture style: build minimal
on-disk workspace shells in a tmp dir and assert the replay surfaces the
right PASS / WARN / n/a cells. The replay must REUSE the canonical gate
functions (no reimplementation) and must NEVER crash on a missing artifact.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from omicairn import gate_replay
from omicairn.cli import _audit_replay_gates
from omicairn.constants import (
    DecisiveArmPowerGate,
    PreregCompletenessGate,
    PreregField,
)


# A Lean source that trips the linear-rearrangement substance layer — the
# live example-study-d6 `chk` catch: `hpart` is the goal merely transposed across
# `=`, closed by `linarith`, deriving nothing (verbatim from test_lean_verify).
_LINARITH_THEATER_SRC = (
    "theorem chk (rMR rMS rSR rpart g : ℝ) "
    "(hpart : rpart * g = rMR - rMS * rSR) :\n"
    "    rMR = rpart * g + rMS * rSR := by linarith [hpart]\n"
)

# A substantive Lean source (nlinarith combining two hyps into a quadratic
# bound) — must stay CLEAN.
_SUBSTANTIVE_SRC = (
    "theorem bound (a b : ℝ) (h1 : a ≤ b) (h2 : 0 ≤ a) :\n"
    "  a * a ≤ b * b := by nlinarith [h1, h2]\n"
)


def _make_workspace(
    root: pathlib.Path,
    *,
    results: dict | None = None,
    prereg: dict | None = None,
    lean_sources: dict[str, str] | None = None,
    finalized: bool = True,
) -> pathlib.Path:
    """Build a finalized-looking workspace shell.

    Lays out RESEARCH.md at the root, 001_plan (with an optional
    preregistration.json), 002_execute (results.json + optional
    lean/<name>.lean), and — when `finalized` — done.flag on the execute
    stage plus a done 003_cross_check after it (the replay's "finalized"
    signal). Any artifact passed as None is omitted, exercising the n/a
    paths.
    """
    root.mkdir(parents=True, exist_ok=True)
    (root / "RESEARCH.md").write_text("# fixture\nresearch_question: q\n")

    plan = root / "001_plan"
    plan.mkdir(exist_ok=True)
    if prereg is not None:
        (plan / "preregistration.json").write_text(json.dumps(prereg))

    execute = root / "002_execute"
    execute.mkdir(exist_ok=True)
    if results is not None:
        (execute / "results.json").write_text(json.dumps(results))
    if lean_sources:
        lean_dir = execute / "lean"
        lean_dir.mkdir(exist_ok=True)
        for name, src in lean_sources.items():
            (lean_dir / name).write_text(src)

    if finalized:
        (execute / "done.flag").write_text("1\n")
        cc = root / "003_cross_check"
        cc.mkdir(exist_ok=True)
        (cc / "done.flag").write_text("1\n")
    return root


def test_clean_workspace_all_pass(tmp_path: pathlib.Path):
    """A workspace whose plan declares load-bearing/discriminating arms that
    all resolved, with a substantive proof, is all PASS (no WARN/ERR)."""
    ws = _make_workspace(
        tmp_path / "clean-ws",
        prereg={
            "load_bearing_arm": "delta_a_vs_b",
            "discriminating_arm": "delta_a_vs_b",
            "central_question": {"discriminating_arm_resolution": "resolved"},
            # All confirmatory declarations present → W21 silent PASS.
            # decision_rule_structured lives per-hypothesis (the W20 schema).
            "hypotheses": [
                {
                    "id": "H1",
                    "decision_rule_structured": {
                        "metric": "comparisons.delta_a_vs_b.delta",
                        "op": ">=",
                        "threshold": 0.02,
                        "ci_excludes": 0,
                    },
                }
            ],
        },
        results={
            "primary_model": {"auc": 0.82, "ci_95": [0.7, 0.9]},
            "comparisons": {
                "delta_a_vs_b": {"delta": 0.3, "ci_95": [0.1, 0.5]},
            },
            "central_question": {"discriminating_arm_resolution": "resolved"},
        },
        lean_sources={"thm.lean": _SUBSTANTIVE_SRC},
    )
    replay = gate_replay.replay_workspace(ws)
    bad = [g for g in replay.gates if g.status in ("warn", "error")]
    assert not bad, f"expected all PASS, got findings: {[(g.gate, g.status) for g in bad]}"
    assert replay.total_warnings == 0
    assert replay.total_errors == 0


def test_w21_confirmatory_missing_fields_warns_naming_them(tmp_path: pathlib.Path):
    """W21: a CONFIRMATORY run (it emitted a preregistration.json) whose plan
    declares NONE of the PreregField.ALL fields WARNs, naming every missing
    field by its canonical constant — surfacing the vacuous pass that
    W13/W17/W20 each silently allow (audit S1)."""
    ws = _make_workspace(
        tmp_path / "incomplete-prereg-ws",
        # Confirmatory (prereg present) but declares none of the fields.
        prereg={"primary_hypothesis": "h", "primary_metric": "auc"},
        results={"primary_model": {"auc": 0.8, "ci_95": [0.7, 0.9]}},
    )
    by_name = {g.gate: g for g in gate_replay.replay_workspace(ws).gates}
    w21 = by_name[PreregCompletenessGate.GATE_ID]
    assert w21.status == PreregCompletenessGate.SEVERITY  # "warn"
    assert len(w21.warnings) == 1
    msg = w21.warnings[0]
    # Names the gate id and EVERY missing field, by canonical constant.
    assert PreregCompletenessGate.GATE_ID in msg
    for field in PreregField.ALL:
        assert field in msg, f"{field} not named in {msg!r}"
    assert f"missing {len(PreregField.ALL)} of the {len(PreregField.ALL)}" in msg


def test_w21_confirmatory_all_fields_present_passes(tmp_path: pathlib.Path):
    """W21: a confirmatory run that declares ALL fields (the two top-level
    + a per-hypothesis decision_rule_structured) is a silent PASS —
    a no-op when complete."""
    ws = _make_workspace(
        tmp_path / "complete-prereg-ws",
        prereg={
            PreregField.LOAD_BEARING_ARM: "delta_a_vs_b",
            PreregField.DISCRIMINATING_ARM: "delta_c_vs_b",
            PreregField.HYPOTHESES: [
                {
                    "id": "H1",
                    PreregField.DECISION_RULE_STRUCTURED: {
                        "metric": "comparisons.delta_a_vs_b.delta",
                        "op": ">=",
                        "threshold": 0.02,
                    },
                }
            ],
        },
        results={"primary_model": {"auc": 0.8, "ci_95": [0.7, 0.9]}},
    )
    by_name = {g.gate: g for g in gate_replay.replay_workspace(ws).gates}
    w21 = by_name[PreregCompletenessGate.GATE_ID]
    assert w21.status == "pass", w21
    assert w21.warnings == []


def test_w21_exploratory_no_prereg_does_not_fire(tmp_path: pathlib.Path):
    """W21: an EXPLORATORY / descriptive run emits NO preregistration.json,
    so W21 must NOT fire (silent PASS) — never penalising hypothesis-
    generating work. Same back-compat as the 33 historical runs."""
    ws = _make_workspace(
        tmp_path / "exploratory-ws",
        prereg=None,  # no preregistration.json → not a confirmatory run
        results={"primary_model": {"auc": 0.8, "ci_95": [0.7, 0.9]}},
    )
    by_name = {g.gate: g for g in gate_replay.replay_workspace(ws).gates}
    w21 = by_name[PreregCompletenessGate.GATE_ID]
    assert w21.status == "pass", w21
    assert w21.warnings == []


def test_w23_constant_decisive_input_warns(tmp_path: pathlib.Path):
    """W23: a confirmatory run whose pre-registered discriminating_arm RAN but
    whose decisive panel numerator is CONSTANT across all units (the posttrain-
    regime c==1 coverage defect) WARNs — the decisive test had zero power."""
    ws = _make_workspace(
        tmp_path / "degenerate-arm-ws",
        prereg={
            "load_bearing_arm": "delta_headline_vs_null",
            "discriminating_arm": "delta_regimeprobe_vs_behavioural",
        },
        results={
            "primary_model": {"auc": 0.82, "ci_95": [0.70, 0.90]},
            "comparisons": {
                "delta_headline_vs_null": {"delta": 0.12, "ci_95": [0.05, 0.19]},
                "delta_regimeprobe_vs_behavioural": {
                    "delta": 0.0, "ci_95": [-0.4, 0.2],
                },
            },
            "central_question": {"discriminating_arm_resolution": "scoped"},
            # Regime panel: numerator `c` constant (==1), denominator `d` varies.
            "panel": [
                {"model": f"m{i}", "c": 1, "d": 3 + i * 4, "c_over_d": 1 / (3 + i * 4)}
                for i in range(10)
            ],
        },
    )
    by_name = {g.gate: g for g in gate_replay.replay_workspace(ws).gates}
    w23 = by_name[DecisiveArmPowerGate.GATE_ID]
    assert w23.status == DecisiveArmPowerGate.SEVERITY  # "warn"
    assert len(w23.warnings) == 1, w23.warnings
    assert "delta_regimeprobe_vs_behavioural" in w23.warnings[0]
    assert "CONSTANT" in w23.warnings[0]


def test_w23_powered_arm_is_pass(tmp_path: pathlib.Path):
    """W23: a confirmatory run whose discriminating arm fired with a varying
    decisive input (real power) is a silent PASS."""
    ws = _make_workspace(
        tmp_path / "powered-arm-ws",
        prereg={
            "load_bearing_arm": "delta_headline_vs_null",
            "discriminating_arm": "delta_regimeprobe_vs_behavioural",
        },
        results={
            "primary_model": {"auc": 0.82, "ci_95": [0.70, 0.90]},
            "comparisons": {
                "delta_headline_vs_null": {"delta": 0.12, "ci_95": [0.05, 0.19]},
                "delta_regimeprobe_vs_behavioural": {
                    "delta": 0.01, "ci_95": [-0.05, 0.07],
                },
            },
            "central_question": {"discriminating_arm_resolution": "scoped"},
            "panel": [
                {"model": f"m{i}", "c": i + 1, "d": 10} for i in range(10)
            ],
        },
    )
    by_name = {g.gate: g for g in gate_replay.replay_workspace(ws).gates}
    w23 = by_name[DecisiveArmPowerGate.GATE_ID]
    assert w23.status == "pass", w23
    assert w23.warnings == []


def test_linarith_theater_lean_surfaces_substance_warn(tmp_path: pathlib.Path):
    """A run carrying a linarith-rearrangement proof-theater .lean trips
    the lean_substance gate (WARN), reusing lean_verify.substance_warnings."""
    ws = _make_workspace(
        tmp_path / "theater-ws",
        results={"primary_model": {"auc": 0.8, "ci_95": [0.7, 0.9]}},
        lean_sources={"chk.lean": _LINARITH_THEATER_SRC},
    )
    replay = gate_replay.replay_workspace(ws)
    by_name = {g.gate: g for g in replay.gates}
    sub = by_name["lean_substance"]
    assert sub.status == "warn", sub
    assert sub.warnings, "expected a substance warning"
    assert "chk.lean" in sub.warnings[0]
    assert "WARN(1)" == sub.cell()


def test_missing_artifacts_is_na_not_crash(tmp_path: pathlib.Path):
    """A workspace with no results.json / no lean reports n/a for every
    artifact-dependent gate — never crashes."""
    ws = _make_workspace(
        tmp_path / "bare-ws",
        results=None,
        lean_sources=None,
    )
    replay = gate_replay.replay_workspace(ws)  # must not raise
    by_name = {g.gate: g for g in replay.gates}
    assert "W19" not in by_name
    for gate in ("lean_substance", "W13", "W14", "W15", "W16", "W17", "W18"):
        assert by_name[gate].status == "n/a", (gate, by_name[gate])
        assert by_name[gate].na_reason
        assert by_name[gate].cell() == "n/a"
    assert not replay.has_finding


def test_json_output_shape(tmp_path: pathlib.Path):
    """`--json` output carries the documented machine-readable shape."""
    ws = _make_workspace(
        tmp_path / "json-ws",
        results={"primary_model": {"auc": 0.8, "ci_95": [0.7, 0.9]}},
        lean_sources={"chk.lean": _LINARITH_THEATER_SRC},
    )
    doc = gate_replay.to_json([gate_replay.replay_workspace(ws)])
    assert doc["gates"] == list(gate_replay.GATE_NAMES)
    assert doc["summary"]["workspaces"] == 1
    assert doc["summary"]["total_warnings"] >= 1
    assert doc["summary"]["total_errors"] == 0
    wsd = doc["workspaces"][0]
    assert wsd["workspace"] == "json-ws"
    gate_blocks = {g["gate"]: g for g in wsd["gates"]}
    assert set(gate_blocks) == set(gate_replay.GATE_NAMES)
    for g in gate_blocks.values():
        assert g["status"] in ("pass", "warn", "error", "n/a")
        assert isinstance(g["warnings"], list)
        assert isinstance(g["errors"], list)
    assert gate_blocks["lean_substance"]["status"] == "warn"


def test_discover_skips_non_workspace_dirs(tmp_path: pathlib.Path):
    """discover_workspaces skips lab_knowledge/ etc. and anything without a
    RESEARCH.md, and finds the finalized ones."""
    _make_workspace(tmp_path / "real-a", results={"primary_model": {}})
    _make_workspace(tmp_path / "real-b", results={"primary_model": {}})
    # Non-run dirs: must be skipped.
    (tmp_path / "lab_knowledge").mkdir()
    (tmp_path / "lab_library").mkdir()
    # A dir with no RESEARCH.md and no finalized stages: skipped.
    (tmp_path / "not-a-run").mkdir()
    (tmp_path / "not-a-run" / "random.txt").write_text("x")
    found = {p.name for p in gate_replay.discover_workspaces(tmp_path)}
    assert found == {"real-a", "real-b"}


def test_unfinalized_workspace_not_discovered(tmp_path: pathlib.Path):
    """A workspace with a RESEARCH.md but no done EXECUTE+CROSS_CHECK pair
    is NOT finalized → not discovered."""
    ws = tmp_path / "in-progress"
    ws.mkdir()
    (ws / "RESEARCH.md").write_text("# q\n")
    (ws / "000_scope").mkdir()
    (ws / "001_plan").mkdir()
    assert gate_replay.discover_workspaces(tmp_path) == []
    assert not gate_replay.is_finalized_workspace(ws)


def test_finalized_requires_done_execute_results_and_later_cross_check(
        tmp_path: pathlib.Path):
    """Finalized is keyed on a done EXECUTE carrying results.json AND a done
    CROSS_CHECK after it — not on any write/typeset artifact."""
    ok = _make_workspace(tmp_path / "ok", results={"primary_model": {}})
    assert gate_replay.is_finalized_workspace(ok)

    no_results = _make_workspace(tmp_path / "no-results", results=None)
    assert not gate_replay.is_finalized_workspace(no_results)

    no_cc = _make_workspace(tmp_path / "no-cc", results={"primary_model": {}},
                            finalized=False)
    (no_cc / "002_execute" / "done.flag").write_text("1\n")
    assert not gate_replay.is_finalized_workspace(no_cc)

    # A cross_check that PRECEDES the latest done execute (a re-run whose
    # check never completed) does not count.
    stale = _make_workspace(tmp_path / "stale", results={"primary_model": {}})
    rerun = stale / "004_execute"
    rerun.mkdir()
    (rerun / "results.json").write_text("{}")
    (rerun / "done.flag").write_text("1\n")
    assert not gate_replay.is_finalized_workspace(stale)

    assert {p.name for p in gate_replay.discover_workspaces(tmp_path)} == {"ok"}


def test_legacy_paper_stage_dirs_neither_finalize_nor_crash(tmp_path: pathlib.Path):
    """A paper-era workspace with NNN_write / NNN_typeset dirs but no done
    CROSS_CHECK is not finalized; with one, legacy dirs are simply ignored."""
    ws = _make_workspace(tmp_path / "legacy", results={"primary_model": {}},
                         finalized=False)
    for legacy in ("004_write", "005_claim_audit", "011_typeset"):
        d = ws / legacy
        d.mkdir()
        (d / "done.flag").write_text("1\n")
    (ws / "004_write" / "draft.md").write_text("# T\n")
    assert not gate_replay.is_finalized_workspace(ws)
    (ws / "002_execute" / "done.flag").write_text("1\n")
    cc = ws / "003_cross_check"
    cc.mkdir()
    (cc / "done.flag").write_text("1\n")
    assert gate_replay.is_finalized_workspace(ws)
    gate_replay.replay_workspace(ws)  # must not raise


def test_lean_scan_excludes_vendored_lake_packages(tmp_path: pathlib.Path):
    """Vendored Lake/Mathlib dependency sources (.lake/packages/...) under a
    stage must NOT be replayed — only the workspace's own lean/*.lean."""
    ws = _make_workspace(
        tmp_path / "vendored-ws",
        results={"primary_model": {}},
        lean_sources={"own.lean": _SUBSTANTIVE_SRC},
    )
    # Plant a vendored theater source that WOULD warn if scanned.
    vendored = ws / "002_execute" / "lean" / ".lake" / "packages" / "dep"
    vendored.mkdir(parents=True)
    (vendored / "vendor.lean").write_text(_LINARITH_THEATER_SRC)
    files = {p.name for p in gate_replay._resolve_lean_files(ws)}
    assert files == {"own.lean"}
    sub = {g.gate: g for g in gate_replay.replay_workspace(ws).gates}["lean_substance"]
    assert sub.status == "pass"


def test_lean_scan_uses_only_freshest_redeepened_stage(tmp_path: pathlib.Path):
    """A run that RE-DEEPENED its proof leaves stale proof-theater in an
    EARLIER stage and the substantive proof in a LATER stage. The replay must
    scan ONLY the freshest formalization stage (the SHIPPED proof) — the stale
    earlier theater must NOT be re-flagged.

    This is the exact example-study-d7 / fair-erasure scenario: d7 carries the old
    monotonicity tautology in 036_execute/lean and the substantive
    Cauchy-Schwarz bound in 042_execute/lean; the run ships the
    latter, so replaying the earlier theater is a false positive against work
    the run already fixed."""
    ws = tmp_path / "redeepened-ws"
    ws.mkdir()
    (ws / "RESEARCH.md").write_text("# fixture\nresearch_question: q\n")
    # EARLIER stage: the superseded proof-theater (would WARN if scanned).
    old = ws / "036_execute" / "lean"
    old.mkdir(parents=True)
    (old / "theater.lean").write_text(_LINARITH_THEATER_SRC)
    # LATER stage: the re-deepened substantive proof (clean).
    new = ws / "042_execute" / "lean"
    new.mkdir(parents=True)
    (new / "bound.lean").write_text(_SUBSTANTIVE_SRC)
    (ws / "042_execute" / "results.json").write_text("{}")
    (ws / "042_execute" / "done.flag").write_text("1\n")
    (ws / "043_cross_check").mkdir()
    (ws / "043_cross_check" / "done.flag").write_text("1\n")
    assert gate_replay.is_finalized_workspace(ws)

    files = {p.name for p in gate_replay._resolve_lean_files(ws)}
    assert files == {"bound.lean"}, (
        "replay must scan ONLY the freshest formalization stage, not the "
        f"superseded earlier theater; got {files}")
    sub = {g.gate: g for g in gate_replay.replay_workspace(ws).gates}["lean_substance"]
    assert sub.status == "pass", sub
    assert sub.warnings == [], (
        "the stale earlier-stage theater must NOT be flagged after re-deepen")


def test_cli_replay_gates_all_table(tmp_path: pathlib.Path, capsys):
    """End-to-end through _audit_replay_gates: --all over a tmp root prints
    the table and returns 0 (read-only audit surface)."""
    _make_workspace(tmp_path / "ws-1", results={"primary_model": {}})
    _make_workspace(
        tmp_path / "ws-2",
        results={"primary_model": {}},
        lean_sources={"chk.lean": _LINARITH_THEATER_SRC},
    )
    import argparse
    args = argparse.Namespace(
        replay_gates=True, all=True,
        workspaces_root=str(tmp_path), workspace=None, as_json=False,
    )
    rc = _audit_replay_gates(args)
    out = capsys.readouterr().out
    assert rc == 0
    assert "ws-1" in out and "ws-2" in out
    assert "lean_substance" in out
    assert "summary:" in out
    assert "2 workspace(s)" in out


def test_cli_replay_gates_explicit_workspace_json(tmp_path: pathlib.Path, capsys):
    """--replay-gates with an explicit workspace + --json prints parseable
    JSON and returns 0."""
    ws = _make_workspace(tmp_path / "solo", results={"primary_model": {}})
    import argparse
    args = argparse.Namespace(
        replay_gates=True, all=False,
        workspaces_root=None, workspace=str(ws), as_json=True,
    )
    rc = _audit_replay_gates(args)
    out = capsys.readouterr().out
    assert rc == 0
    doc = json.loads(out)
    assert doc["summary"]["workspaces"] == 1
    assert doc["workspaces"][0]["workspace"] == "solo"


def test_cli_replay_gates_no_targets_is_rc2(tmp_path: pathlib.Path, capsys):
    """--replay-gates without --all and without a workspace is a bad
    invocation (rc 2)."""
    import argparse
    args = argparse.Namespace(
        replay_gates=True, all=False,
        workspaces_root=None, workspace=None, as_json=False,
    )
    rc = _audit_replay_gates(args)
    assert rc == 2


def test_replay_is_read_only(tmp_path: pathlib.Path):
    """The replay must not mutate any workspace artifact — snapshot the dir
    tree (paths + sizes + mtimes) before and after."""
    ws = _make_workspace(
        tmp_path / "ro-ws",
        prereg={"load_bearing_arm": "delta_a_vs_b"},
        results={"comparisons": {"delta_a_vs_b": {"delta": 0.3, "ci_95": [0.1, 0.5]}}},
        lean_sources={"chk.lean": _LINARITH_THEATER_SRC},
    )

    def snapshot() -> dict:
        return {
            str(p.relative_to(ws)): (p.stat().st_size, p.stat().st_mtime_ns)
            for p in sorted(ws.rglob("*")) if p.is_file()
        }

    before = snapshot()
    gate_replay.replay_workspace(ws)
    gate_replay.to_json([gate_replay.replay_workspace(ws)])
    assert snapshot() == before, "replay mutated workspace artifacts"
