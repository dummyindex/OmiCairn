"""CLI smoke tests — argparse surface and subcommand wiring.

These tests call `omicairn.cli.main()` directly with a monkeypatched
`sys.argv`. That is fast (no subprocess spawn) while still exercising
the real argparse surface, so drift between CLI flag names / choice
lists / subcommand dispatch is caught at edit time rather than the
first time someone types `omicairn run --dry_run` and gets an argparse
error in production.

All tests run under OMICAIRN_DRY_RUN=1 so `omicairn run` never invokes a real
claude subprocess. Literature fetching is also suppressed via
--skip-literature.
"""
from __future__ import annotations

import pathlib
import tempfile
from typing import Iterator

import pytest

from omicairn import cli
from omicairn.constants import ENV_DRY_RUN


RESEARCH_TEMPLATE = """\
---
research_mode: METHOD
domain: synthetic_smoke_test
topic_slug: pytest-cli
research_question: |
  Does the CLI dispatch work end-to-end under dry-run?
data:
  location: {data_dir}
  description: synthetic pytest fixture
  cohorts: [a]
  modalities: [tabular]
success_criteria:
  primary_metric: auc
  baseline: null_model
bounds:
  max_iters_per_phase: 3
  max_backtracks: 5
budgets:
  max_phase_wall_minutes: 5
  allow_web: false
  claude_model: claude-sonnet-4-6
  max_phase_budget_usd: 1.0
auto_approve: true
---

# pytest CLI fixture
"""


@pytest.fixture
def tmp_tree() -> Iterator[tuple[pathlib.Path, pathlib.Path, pathlib.Path]]:
    """Returns (tmpdir, research_md_path, workspaces_root)."""
    with tempfile.TemporaryDirectory() as td:
        tmpdir = pathlib.Path(td)
        data_dir = tmpdir / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "dummy.csv").write_text("x,y\n1,1\n")
        rmd_path = tmpdir / "RESEARCH.md"
        rmd_path.write_text(RESEARCH_TEMPLATE.format(data_dir=data_dir))
        ws_root = tmpdir / "workspaces"
        ws_root.mkdir()
        yield tmpdir, rmd_path, ws_root


def _run_cli(monkeypatch, argv: list[str]) -> int:
    """Call cli.main() with the given argv (minus the prog name)."""
    monkeypatch.setattr("sys.argv", ["omicairn", *argv])
    return cli.main()


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

def test_cli_init_creates_workspace(monkeypatch, tmp_tree, capsys):
    _tmpdir, rmd_path, ws_root = tmp_tree
    rc = _run_cli(
        monkeypatch,
        [
            "init",
            "pytest-cli",
            "--from-research-md",
            str(rmd_path),
            "--workspaces-root",
            str(ws_root),
            "--skip-literature",
        ],
    )
    assert rc == 0
    # workspace.init() suffixes the slug with a YYYYMMDD-HHMMSS stamp.
    # Find the created dir rather than hardcoding the name.
    created = sorted(ws_root.glob("pytest-cli-*"))
    assert len(created) == 1, f"expected 1 workspace dir, got {created}"
    ws_dir = created[0]
    assert (ws_dir / "RESEARCH.md").exists()
    assert (ws_dir / "STATE.md").exists()
    out = capsys.readouterr().out
    assert "initialised workspace" in out


def test_cli_init_missing_research_md_returns_error(
    monkeypatch, tmp_tree, capsys
):
    _tmpdir, _rmd_path, ws_root = tmp_tree
    rc = _run_cli(
        monkeypatch,
        [
            "init",
            "pytest-cli",
            "--from-research-md",
            "/definitely/not/a/real/file.md",
            "--workspaces-root",
            str(ws_root),
            "--skip-literature",
        ],
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "RESEARCH.md not found" in err


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

def _init_workspace(monkeypatch, tmp_tree) -> pathlib.Path:
    """Helper: scaffold a workspace via the CLI so run/status/inspect
    can exercise it. Returns the resolved workspace dir (not the
    workspaces_root)."""
    _tmpdir, rmd_path, ws_root = tmp_tree
    _run_cli(
        monkeypatch,
        [
            "init",
            "pytest-cli",
            "--from-research-md",
            str(rmd_path),
            "--workspaces-root",
            str(ws_root),
            "--skip-literature",
        ],
    )
    # workspace.init() suffixes the slug with a timestamp.
    created = sorted(ws_root.glob("pytest-cli-*"))
    assert len(created) == 1, f"expected 1 workspace dir, got {created}"
    return created[0]


def test_cli_run_dry_run_reaches_done(monkeypatch, tmp_tree, capsys):
    """Full happy path: `omicairn run --dry-run` reaches DONE in 5 stages,
    ending at the analysis REVIEW."""
    ws_dir = _init_workspace(monkeypatch, tmp_tree)
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)
    capsys.readouterr()  # drain init output

    rc = _run_cli(
        monkeypatch,
        ["run", str(ws_dir), "--dry-run", "--max-stages", "20"],
    )
    assert rc == 0, capsys.readouterr()
    out = capsys.readouterr().out
    assert "OmiCairn run summary" in out
    # Five phases each produce a DONE stage on the happy path.
    assert out.count("DONE") >= 5
    assert "004_review" in out
    assert "_write" not in out


def test_cli_run_missing_workspace_returns_error(
    monkeypatch, tmp_tree, capsys
):
    _tmpdir, _rmd_path, _ws_root = tmp_tree
    # A directory that exists but has no RESEARCH.md.
    empty_dir = _tmpdir / "empty_workspace"
    empty_dir.mkdir()
    rc = _run_cli(monkeypatch, ["run", str(empty_dir), "--dry-run"])
    assert rc == 2
    assert "no RESEARCH.md" in capsys.readouterr().err


def test_cli_run_dry_verdict_retry_execute_halts(
    monkeypatch, tmp_tree, capsys
):
    """`--dry-verdict retry_execute` must drive the orchestrator into
    the halt path. This covers the dry_verdict CLI arg wiring, not just
    the underlying orchestrator logic."""
    ws_dir = _init_workspace(monkeypatch, tmp_tree)
    capsys.readouterr()
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)

    rc = _run_cli(
        monkeypatch,
        [
            "run",
            str(ws_dir),
            "--dry-run",
            "--dry-verdict",
            "retry_execute",
            "--max-stages",
            "30",
        ],
    )
    # Halt returns non-zero (anything not in DONE|HALTED is failed; halt
    # itself returns 0 because cmd_run only counts stages not in those
    # two statuses).
    assert rc == 0, capsys.readouterr()
    out = capsys.readouterr().out
    assert "HALTED" in out


# ---------------------------------------------------------------------------
# concurrency cap (owner directive 2026-06-23) — HARD launch-time gate
# ---------------------------------------------------------------------------

from omicairn.constants import ENV_MAX_CONCURRENT_RUNS, MAX_CONCURRENT_RUNS  # noqa: E402


def test_cli_run_below_cap_launches(monkeypatch, tmp_tree, capsys):
    """Under the cap the run proceeds unchanged (here to DONE under dry-run)."""
    ws_dir = _init_workspace(monkeypatch, tmp_tree)
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)
    capsys.readouterr()
    # Inject a live count well below the cap.
    monkeypatch.setattr(cli.reap_mod, "count_live_runs", lambda *a, **k: (3, False))
    rc = _run_cli(monkeypatch, ["run", str(ws_dir), "--dry-run", "--max-stages", "20"])
    assert rc == 0, capsys.readouterr()
    assert "OmiCairn run summary" in capsys.readouterr().out


def test_cli_run_at_cap_refuses_with_nonzero_exit(monkeypatch, tmp_tree, capsys):
    """At/over the cap, `omicairn run` REFUSES with a clear message + non-zero exit
    and never enters the orchestrator."""
    ws_dir = _init_workspace(monkeypatch, tmp_tree)
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)
    monkeypatch.delenv(ENV_MAX_CONCURRENT_RUNS, raising=False)
    capsys.readouterr()
    # Exactly at the default cap.
    monkeypatch.setattr(
        cli.reap_mod, "count_live_runs",
        lambda *a, **k: (MAX_CONCURRENT_RUNS, False),
    )
    rc = _run_cli(monkeypatch, ["run", str(ws_dir), "--dry-run"])
    assert rc == 3
    err = capsys.readouterr().err
    assert "concurrency cap" in err
    assert str(MAX_CONCURRENT_RUNS) in err
    assert ENV_MAX_CONCURRENT_RUNS in err  # tells the user how to raise it
    # It refused BEFORE running any stage → no run summary printed.
    assert "OmiCairn run summary" not in err


def test_cli_run_cap_env_override_raises_ceiling(monkeypatch, tmp_tree, capsys):
    """OMICAIRN_MAX_CONCURRENT_RUNS raises the ceiling: a count that would be
    refused at the default cap is allowed when the override is higher."""
    ws_dir = _init_workspace(monkeypatch, tmp_tree)
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)
    capsys.readouterr()
    # Live count == default cap (would refuse) but override lifts the ceiling.
    monkeypatch.setattr(
        cli.reap_mod, "count_live_runs",
        lambda *a, **k: (MAX_CONCURRENT_RUNS, False),
    )
    monkeypatch.setenv(ENV_MAX_CONCURRENT_RUNS, str(MAX_CONCURRENT_RUNS + 5))
    rc = _run_cli(monkeypatch, ["run", str(ws_dir), "--dry-run", "--max-stages", "20"])
    assert rc == 0, capsys.readouterr()
    assert "OmiCairn run summary" in capsys.readouterr().out


def test_cli_run_resume_of_live_run_is_not_self_blocked(monkeypatch, tmp_tree, capsys):
    """Resuming an already-live run must not be rejected by its own liveness:
    the cap excludes the target workspace from the count. We assert the
    enforcement passes `exclude=<this workspace>` so the count never includes
    the run being (re)launched."""
    ws_dir = _init_workspace(monkeypatch, tmp_tree)
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)
    capsys.readouterr()

    seen = {}

    def _spy(target, exclude=None):
        seen["target"] = target
        seen["exclude"] = exclude
        # Even if the target itself were live, excluding it keeps OTHERS < cap.
        return (MAX_CONCURRENT_RUNS - 1, False)

    monkeypatch.setattr(cli.reap_mod, "count_live_runs", _spy)
    rc = _run_cli(monkeypatch, ["run", str(ws_dir), "--dry-run", "--max-stages", "20"])
    assert rc == 0, capsys.readouterr()
    # The cap counted over the PARENT root, excluding THIS workspace by path.
    assert seen["exclude"] == ws_dir
    assert seen["target"] == ws_dir.parent


def test_cli_fleet_shows_cap_headroom(monkeypatch, tmp_tree, capsys):
    """`omicairn fleet` surfaces the `N/CAP live` headroom so the loop sees it."""
    from omicairn import reap as reap_mod
    monkeypatch.setattr(reap_mod, "scan_live_run_workspaces", lambda: (set(), False))
    monkeypatch.delenv(ENV_MAX_CONCURRENT_RUNS, raising=False)
    _init_workspace(monkeypatch, tmp_tree)
    _tmpdir, _rmd, ws_root = tmp_tree
    capsys.readouterr()
    rc = _run_cli(monkeypatch, ["fleet", str(ws_root)])
    assert rc == 0
    out = capsys.readouterr().out
    assert f"/{MAX_CONCURRENT_RUNS} live" in out  # e.g. "0/25 live (cap, ...)"


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

def test_cli_status_on_fresh_workspace(monkeypatch, tmp_tree, capsys):
    ws_dir = _init_workspace(monkeypatch, tmp_tree)
    capsys.readouterr()
    rc = _run_cli(monkeypatch, ["status", str(ws_dir)])
    assert rc == 0
    out = capsys.readouterr().out
    # All the top-level fields the user expects to see.
    assert "topic_slug" in out
    assert "status" in out
    assert "iteration_counts" in out
    assert "bounds" in out
    assert "max_iters_per_phase" in out
    # Every phase name must appear in the iteration_counts block.
    for name in ("scope", "plan", "execute", "cross_check", "review"):
        assert name in out


# ---------------------------------------------------------------------------
# fleet
# ---------------------------------------------------------------------------

def test_cli_fleet_over_workspaces_root(monkeypatch, tmp_tree, capsys):
    """`omicairn fleet <root>` prints a one-line-per-run table. Stub the (shared,
    from reap) process scan so the test never depends on the host process
    table."""
    from omicairn import reap as reap_mod
    monkeypatch.setattr(reap_mod, "scan_live_run_workspaces", lambda: (set(), False))
    ws_dir = _init_workspace(monkeypatch, tmp_tree)
    _tmpdir, _rmd, ws_root = tmp_tree
    capsys.readouterr()
    rc = _run_cli(monkeypatch, ["fleet", str(ws_root)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "RUN" in out and "STATUS" in out and "LIVE" in out
    assert ws_dir.name in out
    assert "run(s):" in out


def test_cli_fleet_on_single_workspace(monkeypatch, tmp_tree, capsys):
    from omicairn import reap as reap_mod
    monkeypatch.setattr(reap_mod, "scan_live_run_workspaces", lambda: (set(), False))
    ws_dir = _init_workspace(monkeypatch, tmp_tree)
    capsys.readouterr()
    rc = _run_cli(monkeypatch, ["fleet", str(ws_dir)])  # point AT the ws
    assert rc == 0
    assert ws_dir.name in capsys.readouterr().out


def test_cli_fleet_nonexistent_path_returns_error(monkeypatch, tmp_tree, capsys):
    _tmpdir, _rmd, ws_root = tmp_tree
    capsys.readouterr()
    rc = _run_cli(monkeypatch, ["fleet", str(ws_root / "nope")])
    assert rc == 2
    assert "no such path" in capsys.readouterr().err


def test_cli_fleet_empty_root(monkeypatch, tmp_tree, capsys):
    from omicairn import reap as reap_mod
    monkeypatch.setattr(reap_mod, "scan_live_run_workspaces", lambda: (set(), False))
    _tmpdir, _rmd, ws_root = tmp_tree  # ws_root exists but is empty
    capsys.readouterr()
    rc = _run_cli(monkeypatch, ["fleet", str(ws_root)])
    assert rc == 0
    assert "no OmiCairn workspaces" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# inspect
# ---------------------------------------------------------------------------

def test_cli_inspect_on_fresh_workspace(monkeypatch, tmp_tree, capsys):
    ws_dir = _init_workspace(monkeypatch, tmp_tree)
    capsys.readouterr()
    rc = _run_cli(monkeypatch, ["inspect", str(ws_dir)])
    assert rc == 0
    # No stages yet, but the command should still return cleanly —
    # render_stage_listing handles the empty case.


def test_cli_inspect_nonexistent_stage_returns_error(
    monkeypatch, tmp_tree, capsys
):
    ws_dir = _init_workspace(monkeypatch, tmp_tree)
    capsys.readouterr()
    rc = _run_cli(
        monkeypatch,
        ["inspect", str(ws_dir), "--stage", "999_nonexistent"],
    )
    assert rc == 2
    assert "no stage directory" in capsys.readouterr().err


def test_cli_inspect_phase_flag_returns_latest_done_stage(
    monkeypatch, tmp_tree, capsys
):
    """`inspect --phase scope` must print the contents of the latest
    done scope stage. This pins the --phase branch of cmd_inspect
    (lines 182-194 in omicairn/cli.py); the --stage branch is already
    covered above, but --phase had no test, so a rename or routing
    bug in latest_stage_of would only surface at runtime."""
    ws_dir = _init_workspace(monkeypatch, tmp_tree)
    capsys.readouterr()
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)
    # Produce at least one done scope stage via dry-run.
    rc = _run_cli(
        monkeypatch,
        [
            "run",
            str(ws_dir),
            "--add-stage",
            "scope",
            "--dry-run",
            "--max-stages",
            "2",
        ],
    )
    assert rc == 0, capsys.readouterr()
    capsys.readouterr()

    rc = _run_cli(monkeypatch, ["inspect", str(ws_dir), "--phase", "scope"])
    assert rc == 0
    out = capsys.readouterr().out
    # Must name the stage dir and must list at least one artifact with size.
    assert "000_scope" in out
    assert " B)" in out


def test_cli_inspect_phase_flag_with_no_done_stage_returns_error(
    monkeypatch, tmp_tree, capsys
):
    """`inspect --phase plan` on a fresh workspace must fail with a
    clear error, not crash or silently print nothing. Covers the
    error branch at cli.py:185-190."""
    ws_dir = _init_workspace(monkeypatch, tmp_tree)
    capsys.readouterr()
    rc = _run_cli(monkeypatch, ["inspect", str(ws_dir), "--phase", "plan"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "no done stage" in err
    assert "'plan'" in err


# ---------------------------------------------------------------------------
# status — halted_reason branch
# ---------------------------------------------------------------------------

def test_cli_status_shows_halted_reason_when_halted(
    monkeypatch, tmp_tree, capsys
):
    """`omicairn status` must surface halted_reason when the run is HALTED.
    Previously only the happy-path status output was tested, so a
    regression that dropped the halted_reason print line (cli.py:128)
    would not be caught. Drive a HALT via --dry-verdict halt on
    cross_check, then read status."""
    ws_dir = _init_workspace(monkeypatch, tmp_tree)
    capsys.readouterr()
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)

    rc = _run_cli(
        monkeypatch,
        [
            "run",
            str(ws_dir),
            "--dry-run",
            "--dry-verdict",
            "halt",
            "--max-stages",
            "20",
        ],
    )
    assert rc == 0, capsys.readouterr()
    capsys.readouterr()

    rc = _run_cli(monkeypatch, ["status", str(ws_dir)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "status:" in out
    assert "HALTED" in out or "halted" in out
    assert "halted_reason:" in out


# ---------------------------------------------------------------------------
# argparse choice-list drift
# ---------------------------------------------------------------------------
#
# These tests pin the CLI's argparse choice lists so renames/typos are
# caught at edit time. argparse exits SystemExit(2) on an invalid choice
# — we catch the exception and assert its code.

def test_cli_run_rejects_unknown_phase(monkeypatch, tmp_tree):
    ws_dir = _init_workspace(monkeypatch, tmp_tree)
    with pytest.raises(SystemExit) as exc_info:
        _run_cli(
            monkeypatch,
            ["run", str(ws_dir), "--phase", "not_a_phase"],
        )
    assert exc_info.value.code == 2


def test_cli_run_rejects_unknown_add_stage(monkeypatch, tmp_tree):
    ws_dir = _init_workspace(monkeypatch, tmp_tree)
    with pytest.raises(SystemExit) as exc_info:
        _run_cli(
            monkeypatch,
            ["run", str(ws_dir), "--add-stage", "finalise"],
        )
    assert exc_info.value.code == 2


def test_cmd_run_defensive_add_stage_check_rejects_unknown(
    monkeypatch, tmp_tree, capsys
):
    """cli.py L82-87: the defensive `if args.add_stage not in
    PIPELINE_ORDER` check. Today argparse's `choices=` already
    rejects unknown phases before cmd_run ever sees them, so the
    defensive branch is unreachable via CLI. But if someone later
    drops the `choices=` argument (or a caller constructs the
    Namespace manually, as the orchestrator-integration test does),
    the defensive check is the safety net. Pin it by calling cmd_run
    directly with a synthetic Namespace.
    """
    import argparse
    ws_dir = _init_workspace(monkeypatch, tmp_tree)

    args = argparse.Namespace(
        workspace=str(ws_dir),
        dry_run=True,
        dry_verdict=None,
        add_stage="finalise",  # not in PIPELINE_ORDER
        phase="all",
        stop_after=None,
        max_stages=None,
    )
    rc = cli.cmd_run(args)
    assert rc == 2
    err = capsys.readouterr().err
    assert "--add-stage must be one of" in err


def test_cli_run_accepts_all_real_phases(monkeypatch, tmp_tree):
    """argparse must accept every real phase name for --add-stage.
    Drift here would be caught by choice-list mismatch with
    PIPELINE_ORDER."""
    ws_dir = _init_workspace(monkeypatch, tmp_tree)
    for phase in ("scope", "plan", "execute", "cross_check", "review"):
        # We don't actually execute the stage — just prove argparse
        # lets the flag through. Use --dry-run + --add-stage; the
        # orchestrator runs exactly one stage and returns.
        rc = _run_cli(
            monkeypatch,
            [
                "run",
                str(ws_dir),
                "--add-stage",
                phase,
                "--dry-run",
                "--max-stages",
                "2",
            ],
        )
        assert rc == 0, f"--add-stage {phase} returned rc={rc}"


def test_cli_missing_subcommand_fails(monkeypatch):
    """`omicairn` alone (no subcommand) must fail, not silently no-op."""
    with pytest.raises(SystemExit) as exc_info:
        _run_cli(monkeypatch, [])
    assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# init — default workspaces root
# ---------------------------------------------------------------------------

def test_cli_init_uses_default_workspaces_root_when_unset(
    monkeypatch, tmp_tree, capsys
):
    """Line 30 of cli.py: `_default_workspaces_root()` — when
    `--workspaces-root` is not passed, cli should fall back to
    `./workspaces/` relative to cwd. We chdir to tmp so the default
    path lands in the sandbox, then verify the workspace was created
    under `./workspaces/`."""
    tmpdir, rmd_path, _ws_root = tmp_tree
    monkeypatch.chdir(tmpdir)
    rc = _run_cli(
        monkeypatch,
        [
            "init",
            "pytest-default-root",
            "--from-research-md",
            str(rmd_path),
            "--skip-literature",
        ],
    )
    assert rc == 0, capsys.readouterr()
    default_root = tmpdir / "workspaces"
    assert default_root.is_dir()
    # workspace.init() uses topic_slug from RESEARCH.md (pytest-cli),
    # not the positional CLI arg, plus a timestamp suffix.
    created = sorted(default_root.glob("pytest-cli-*"))
    assert len(created) == 1


# ---------------------------------------------------------------------------
# run — single-phase (non-all) branch
# ---------------------------------------------------------------------------

def test_cli_run_on_halted_workspace_prints_no_stages_ran(
    monkeypatch, tmp_tree, capsys
):
    """Line 103 of cli.py: the "(no stages ran — pipeline may be
    complete or halted)" branch. Drive the workspace to HALTED via
    --dry-verdict halt, then invoke `omicairn run` again — the
    orchestrator should refuse to run more stages and cmd_run must
    print the no-stages line instead of crashing or silently
    exiting."""
    ws_dir = _init_workspace(monkeypatch, tmp_tree)
    capsys.readouterr()
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)

    rc = _run_cli(
        monkeypatch,
        [
            "run",
            str(ws_dir),
            "--dry-run",
            "--dry-verdict",
            "halt",
            "--max-stages",
            "20",
        ],
    )
    assert rc == 0, capsys.readouterr()
    capsys.readouterr()

    # Now a second run on the halted workspace must produce no
    # outcomes and take the empty-branch print.
    monkeypatch.delenv("OMICAIRN_DRY_VERDICT", raising=False)
    rc = _run_cli(
        monkeypatch,
        ["run", str(ws_dir), "--dry-run", "--max-stages", "5"],
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "no stages ran" in out


def test_cli_run_single_phase_branch(monkeypatch, tmp_tree, capsys):
    """Lines 97-99 of cli.py: the `else:` branch of cmd_run, hit when
    --phase is set to an actual phase name (not the default 'all').
    Previously only --add-stage and `--phase all` paths were covered,
    so a routing bug in the single-phase forwarder would have gone
    unnoticed. Use --phase scope --dry-run to run exactly one scope
    stage through this branch."""
    ws_dir = _init_workspace(monkeypatch, tmp_tree)
    capsys.readouterr()
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)
    rc = _run_cli(
        monkeypatch,
        [
            "run",
            str(ws_dir),
            "--phase",
            "scope",
            "--dry-run",
            "--max-stages",
            "2",
        ],
    )
    assert rc == 0, capsys.readouterr()
    out = capsys.readouterr().out
    assert "OmiCairn run summary" in out
    assert "000_scope" in out
    assert "DONE" in out


# ---------------------------------------------------------------------------
# literature subcommand
# ---------------------------------------------------------------------------

def test_cli_literature_missing_research_md(monkeypatch, tmp_tree, capsys):
    """cmd_literature error branch (cli.py:148-153): pointing at a dir
    with no RESEARCH.md must print a clear error and return rc=2, not
    crash inside workspace.load_workspace."""
    tmpdir, _rmd_path, _ws_root = tmp_tree
    empty_dir = tmpdir / "no_research_here"
    empty_dir.mkdir()
    rc = _run_cli(monkeypatch, ["literature", str(empty_dir)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "no RESEARCH.md" in err


def test_cli_literature_fetches_and_reports_count(
    monkeypatch, tmp_tree, capsys
):
    """cmd_literature happy path (cli.py:154-166): wires a real
    workspace, stubs `literature.fetch` so no network I/O happens, and
    asserts the literature dirs are created and the "Done: N papers"
    line is printed with the correct count."""
    ws_dir = _init_workspace(monkeypatch, tmp_tree)
    capsys.readouterr()

    # Stub out the real fetcher.
    from omicairn import literature as lit_mod

    calls: list[dict] = []

    def fake_fetch(*, research_md, output_dir, max_papers):
        calls.append(
            {
                "topic": research_md.topic_slug,
                "output_dir": output_dir,
                "max_papers": max_papers,
            }
        )
        # Return a list of 3 fake paper dicts — cmd_literature only
        # cares about len().
        return [{"title": f"paper {i}"} for i in range(3)]

    monkeypatch.setattr(lit_mod, "fetch", fake_fetch)

    rc = _run_cli(
        monkeypatch, ["literature", str(ws_dir), "--max-papers", "7"]
    )
    assert rc == 0, capsys.readouterr()
    out = capsys.readouterr().out
    assert "Done: 3 papers fetched" in out
    # Dirs must exist on disk. cmd_literature resolves the workspace
    # path, which on macOS rewrites /var → /private/var, so resolve
    # on our side too before comparing.
    lit_dir = (ws_dir / "literature").resolve()
    assert lit_dir.is_dir()
    for subdir in ("abstracts", "pdfs", "fulltext"):
        assert (lit_dir / subdir).is_dir()
    # fetch() was called with the CLI's max_papers and our workspace.
    assert len(calls) == 1
    assert calls[0]["max_papers"] == 7
    assert calls[0]["output_dir"].resolve() == lit_dir
    assert calls[0]["topic"] == "pytest-cli"


# ---------------------------------------------------------------------------
# inspect --stage success branch
# ---------------------------------------------------------------------------

def test_cli_inspect_stage_flag_lists_artifacts(
    monkeypatch, tmp_tree, capsys
):
    """`inspect --stage 000_scope` success branch (cli.py:177-181).
    The error branch (missing stage) is already covered; this pins
    the positive branch — listing files with size annotations — so
    a rename of iter_stage_artifacts or the " B)" format string
    would be caught at edit time."""
    ws_dir = _init_workspace(monkeypatch, tmp_tree)
    capsys.readouterr()
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)
    rc = _run_cli(
        monkeypatch,
        [
            "run",
            str(ws_dir),
            "--add-stage",
            "scope",
            "--dry-run",
            "--max-stages",
            "2",
        ],
    )
    assert rc == 0, capsys.readouterr()
    capsys.readouterr()

    rc = _run_cli(
        monkeypatch, ["inspect", str(ws_dir), "--stage", "000_scope"]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "== contents of" in out
    assert "000_scope" in out
    assert " B)" in out  # the size-suffix format


# ---------------------------------------------------------------------------
# preflight subcommand
# ---------------------------------------------------------------------------
#
# `omicairn preflight` is the developer-gate sibling of `pytest` — it runs a
# curated, subprocess-pytest call over the fast static lint/contract
# modules. We mock subprocess.call so the tests don't nest a pytest
# inside pytest (which works, but is slow and noisy).


def test_cli_preflight_happy_path(monkeypatch, capsys):
    """cmd_preflight subprocess exit 0 must return 0 and print OK."""
    recorded: dict = {}

    def fake_call(cmd, cwd=None):
        recorded["cmd"] = cmd
        recorded["cwd"] = cwd
        return 0

    monkeypatch.setattr("omicairn.cli.subprocess.call", fake_call)
    rc = _run_cli(monkeypatch, ["preflight"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "omicairn preflight: OK" in out
    # Every curated module must be on the pytest command line.
    cmdline = " ".join(recorded["cmd"])
    for module in cli._PREFLIGHT_TEST_MODULES:
        assert module in cmdline, f"{module} missing from pytest cmd"
    # Default mode uses -q; verbose mode uses -v. We're in default mode.
    assert "-q" in recorded["cmd"]
    assert "-v" not in recorded["cmd"]


def test_cli_preflight_verbose_forwards_dash_v(monkeypatch, capsys):
    """`omicairn preflight --verbose` must pass -v to pytest and NOT -q."""
    recorded: dict = {}

    def fake_call(cmd, cwd=None):
        recorded["cmd"] = cmd
        return 0

    monkeypatch.setattr("omicairn.cli.subprocess.call", fake_call)
    rc = _run_cli(monkeypatch, ["preflight", "--verbose"])
    assert rc == 0
    assert "-v" in recorded["cmd"]
    assert "-q" not in recorded["cmd"]


def test_cli_preflight_failure_path(monkeypatch, capsys):
    """cmd_preflight must propagate pytest's non-zero exit code and
    print a FAIL banner on stderr. Rc=2 is picked to distinguish from
    the argparse rc=2 error path."""
    monkeypatch.setattr("omicairn.cli.subprocess.call", lambda *a, **kw: 2)
    rc = _run_cli(monkeypatch, ["preflight"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "omicairn preflight: FAIL" in err
    assert "pytest exit 2" in err


def test_cli_preflight_missing_tests_dir(monkeypatch, capsys, tmp_path):
    """When tests/ is not on disk (wheel install / stripped checkout)
    preflight must fail loudly rather than running pytest and printing
    a confusing error. We monkeypatch the `repo_root` discovery to
    point at tmp_path (no tests/ under it)."""
    # Make pathlib.Path(__file__).resolve().parents[1] return tmp_path
    # by patching the resolved path that cmd_preflight sees. Easier:
    # monkeypatch cli.__file__ to a stub under tmp_path so parents[1]
    # lands in tmp_path.
    stub_pkg = tmp_path / "stub_omicairn"
    stub_pkg.mkdir()
    (stub_pkg / "cli.py").write_text("# stub")
    monkeypatch.setattr(cli, "__file__", str(stub_pkg / "cli.py"))
    # Ensure subprocess.call is never reached — if it is, the test
    # will blow up with an unexpected extra call.
    called = {"n": 0}

    def should_not_be_called(*a, **kw):
        called["n"] += 1
        return 0

    monkeypatch.setattr("omicairn.cli.subprocess.call", should_not_be_called)
    rc = _run_cli(monkeypatch, ["preflight"])
    assert rc == 2
    assert called["n"] == 0
    err = capsys.readouterr().err
    assert "dev repo layout" in err


def test_cli_preflight_missing_test_module(monkeypatch, capsys, tmp_path):
    """If the curated module list references a file that doesn't exist
    on disk (someone deleted tests/test_phases.py without updating
    _PREFLIGHT_TEST_MODULES), preflight must catch it before
    subprocess-calling pytest with a phantom path."""
    # Stage a stub repo layout: tests/ exists but is empty.
    stub_pkg = tmp_path / "omicairn_stub"
    stub_pkg.mkdir()
    (stub_pkg / "cli.py").write_text("# stub")
    (tmp_path / "tests").mkdir()  # exists but empty
    monkeypatch.setattr(cli, "__file__", str(stub_pkg / "cli.py"))

    called = {"n": 0}
    monkeypatch.setattr(
        "omicairn.cli.subprocess.call", lambda *a, **kw: called.__setitem__("n", called["n"] + 1) or 0
    )
    rc = _run_cli(monkeypatch, ["preflight"])
    assert rc == 2
    assert called["n"] == 0
    err = capsys.readouterr().err
    assert "preflight test modules missing" in err


# ---------------------------------------------------------------------------
# preflight --check-remote (opt-in ssh reachability probe; WARN-only)
# ---------------------------------------------------------------------------

def test_cli_preflight_no_flag_does_not_probe(monkeypatch, capsys):
    """With NO --check-remote, behaviour is identical to before: the rc is
    the drift-lint rc and probe_ssh_host is NEVER called."""
    monkeypatch.setattr("omicairn.cli.subprocess.call", lambda *a, **kw: 0)

    def should_not_probe(*a, **kw):
        raise AssertionError("probe_ssh_host called without --check-remote")

    monkeypatch.setattr(
        "omicairn.compute_resources.probe_ssh_host", should_not_probe)
    rc = _run_cli(monkeypatch, ["preflight"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "omicairn preflight: OK" in out
    # No remote diagnostic line emitted (the probe was never invoked; the
    # only "ssh" substring in output is the worktree path on the repo_root
    # line, so assert on the diagnostic phrasing instead).
    assert "ssh gpu-host-1" not in out
    assert "reachability" not in out
    assert "UNREACHABLE" not in out


def test_cli_preflight_check_remote_reachable(monkeypatch, capsys):
    """--check-remote with a reachable host prints the OK diagnostic and
    leaves the (passing) drift-lint rc untouched."""
    monkeypatch.setattr("omicairn.cli.subprocess.call", lambda *a, **kw: 0)
    monkeypatch.setattr(
        "omicairn.compute_resources.probe_ssh_host",
        lambda host: (True, f"ssh {host} OK"))
    rc = _run_cli(monkeypatch, ["preflight", "--check-remote", "gpu-host-1"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "omicairn preflight: ssh gpu-host-1 OK" in out


def test_cli_preflight_check_remote_unreachable_is_warn_only(monkeypatch, capsys):
    """A definitively-unreachable host prints a WARNING on stderr but does
    NOT change the (passing) drift-lint exit code — an offline GPU box must
    not fail a local lint."""
    monkeypatch.setattr("omicairn.cli.subprocess.call", lambda *a, **kw: 0)
    monkeypatch.setattr(
        "omicairn.compute_resources.probe_ssh_host",
        lambda host: (False, "Could not resolve hostname"))
    rc = _run_cli(monkeypatch, ["preflight", "--check-remote", "bogus-host"])
    assert rc == 0  # WARN-only: lint rc unchanged
    err = capsys.readouterr().err
    assert "WARNING ssh bogus-host UNREACHABLE" in err
    assert "fail at EXECUTE" in err
    assert "Could not resolve hostname" in err


def test_cli_preflight_check_remote_undetermined_is_quiet(monkeypatch, capsys):
    """An undetermined/skipped probe (None) prints a quiet note, not a
    warning, and never touches the rc."""
    monkeypatch.setattr("omicairn.cli.subprocess.call", lambda *a, **kw: 0)
    monkeypatch.setattr(
        "omicairn.compute_resources.probe_ssh_host",
        lambda host: (None, "probe skipped (OMICAIRN_NO_SSH_PROBE set)"))
    rc = _run_cli(monkeypatch, ["preflight", "--check-remote", "gpu-host-1"])
    assert rc == 0
    out = capsys.readouterr().out
    err = capsys.readouterr().err
    assert "reachability undetermined" in out
    assert "WARNING" not in out and "WARNING" not in err


def test_cli_preflight_check_remote_does_not_rescue_lint_failure(monkeypatch, capsys):
    """The remote check is purely additive — a reachable host does NOT mask
    a failing drift-lint suite (the lint rc still propagates)."""
    monkeypatch.setattr("omicairn.cli.subprocess.call", lambda *a, **kw: 2)
    monkeypatch.setattr(
        "omicairn.compute_resources.probe_ssh_host",
        lambda host: (True, f"ssh {host} OK"))
    rc = _run_cli(monkeypatch, ["preflight", "--check-remote", "gpu-host-1"])
    assert rc == 2  # lint failure still wins
    captured = capsys.readouterr()
    assert "omicairn preflight: FAIL" in captured.err
    assert "ssh gpu-host-1 OK" in captured.out


# ---------------------------------------------------------------------------
# Paper-writing removal: the CLI exposes no manuscript surface
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("argv", [
    ["typeset", "/tmp/ws"],
    ["review", "/tmp/paper.md"],
    ["export", "/tmp/ws"],
    ["reservoir", "list"],
    ["paperlib", "reserve", "10.1/x"],
    ["paperlib", "reservoir"],
    ["audit", "/tmp/ws", "--draft", "/tmp/draft.md"],
])
def test_cli_rejects_removed_paper_commands(monkeypatch, capsys, argv):
    with pytest.raises(SystemExit) as exc:
        _run_cli(monkeypatch, argv)
    assert exc.value.code == 2


@pytest.mark.parametrize("phase", ["write", "claim_audit", "peer_review", "typeset"])
def test_cli_run_rejects_removed_phases(monkeypatch, tmp_tree, phase):
    ws_dir = _init_workspace(monkeypatch, tmp_tree)
    with pytest.raises(SystemExit) as exc:
        _run_cli(monkeypatch, ["run", str(ws_dir), "--add-stage", phase,
                               "--dry-run"])
    assert exc.value.code == 2


def test_cli_help_lists_no_paper_commands(monkeypatch, capsys):
    with pytest.raises(SystemExit):
        _run_cli(monkeypatch, ["--help"])
    out = capsys.readouterr().out
    for gone in ("typeset", "reservoir", "export     ", "cover letter",
                 "venue"):
        assert gone not in out, f"--help still mentions {gone!r}"
    assert "paperreview" in out
    assert "audit" in out


# ---------------------------------------------------------------------------
# __main__ module entry point
# ---------------------------------------------------------------------------

def test_main_module_invokes_cli_main(monkeypatch, capsys):
    """omicairn/__main__.py is the `python -m omicairn` entrypoint. It just
    calls `main()` and raises SystemExit with its return code. We run
    it in-process via runpy so import errors or a rename of cli.main
    would be caught here instead of only at real `python -m omicairn`
    invocation."""
    import runpy

    # `omicairn --help` — argparse prints help and exits 0. We pass no
    # subcommand via argv tweak; `omicairn` alone exits 2 which is also
    # fine for coverage of the raise SystemExit line.
    monkeypatch.setattr("sys.argv", ["omicairn", "--help"])
    with pytest.raises(SystemExit) as exc_info:
        runpy.run_module("omicairn", run_name="__main__")
    # --help causes argparse to SystemExit(0).
    assert exc_info.value.code == 0
