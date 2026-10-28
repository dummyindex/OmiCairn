"""Unit tests for omicairn.claude_runner — dry-run and placeholder logic.

The dry-run path is the foundation of the entire test harness: every
orchestrator/workspace/CLI test sets OMICAIRN_DRY_RUN=1 and expects
claude_runner.run() to write plausible placeholder contract files
without spawning a real `claude -p` subprocess. A regression in
_placeholder_for() or _dry_run_write_contract() silently breaks ALL
downstream dry-run tests.

These tests pin the three things that matter:

1. _dry_run_enabled() and _dry_run_forced_verdict() read the canonical
   env vars with sensible truthy/falsy parsing.
2. _placeholder_for() produces the right *shape* for each known
   filename — valid JSON for .json files, forced-verdict honored for
   verdict.json, contract-passing (>32B) for .md files.
3. _dry_run_write_contract() populates every required_outputs file
   for a real phase and returns a ClaudeResult with dry_run=True.
"""
from __future__ import annotations

import json
import os
import pathlib
import time

import pytest

from omicairn import claude_runner, phases
from omicairn.constants import (
    DONE_FLAG,
    ENV_DRY_RUN,
    ENV_DRY_VERDICT,
    Decision,
    FINDINGS_FILE,
    SUMMARY_FILE,
    VERDICT_FILE,
)


# ---------------------------------------------------------------------------
# _dry_run_enabled env parsing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "val,expected",
    [
        ("1", True),
        ("true", True),
        ("yes", True),  # non-empty, not a known falsy
        ("anything", True),
        ("", False),
        ("0", False),
        ("false", False),
        ("False", False),
    ],
)
def test_dry_run_enabled_parses_env(monkeypatch, val, expected):
    monkeypatch.setenv(ENV_DRY_RUN, val)
    assert claude_runner._dry_run_enabled() is expected


def test_dry_run_enabled_unset_is_false(monkeypatch):
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)
    assert claude_runner._dry_run_enabled() is False


# ---------------------------------------------------------------------------
# _dry_run_forced_verdict
# ---------------------------------------------------------------------------

def test_forced_verdict_returns_none_when_unset(monkeypatch):
    monkeypatch.delenv(ENV_DRY_VERDICT, raising=False)
    assert claude_runner._dry_run_forced_verdict() is None


def test_forced_verdict_returns_value_when_set(monkeypatch):
    monkeypatch.setenv(ENV_DRY_VERDICT, "retry_execute")
    assert claude_runner._dry_run_forced_verdict() == "retry_execute"


def test_forced_verdict_strips_whitespace(monkeypatch):
    monkeypatch.setenv(ENV_DRY_VERDICT, "  back_to_plan  ")
    assert claude_runner._dry_run_forced_verdict() == "back_to_plan"


def test_forced_verdict_empty_string_is_none(monkeypatch):
    monkeypatch.setenv(ENV_DRY_VERDICT, "")
    assert claude_runner._dry_run_forced_verdict() is None


# ---------------------------------------------------------------------------
# _placeholder_for — filename-specific shapes
# ---------------------------------------------------------------------------

def test_placeholder_done_flag(tmp_path):
    out = claude_runner._placeholder_for(DONE_FLAG, tmp_path, "execute")
    # done.flag placeholder is just '1\n'; 2 bytes is enough because
    # contracts.verify treats .flag files as allowed down to 1 byte.
    assert out.strip() == "1"


def test_placeholder_summary_md_is_non_trivial(tmp_path):
    """SUMMARY_FILE must be >32 bytes so contracts.verify passes."""
    out = claude_runner._placeholder_for(SUMMARY_FILE, tmp_path, "execute")
    assert len(out.encode()) >= 32
    assert "execute" in out


def test_placeholder_results_json_is_valid_json_with_shape(tmp_path):
    """cross_check reads execute/results.json. The placeholder must
    parse as JSON AND have the shape cross_check expects: a
    primary_model with auc+ci_95 and a comparisons block."""
    out = claude_runner._placeholder_for("results.json", tmp_path, "execute")
    data = json.loads(out)
    assert "primary_model" in data
    assert "auc" in data["primary_model"]
    assert "ci_95" in data["primary_model"]
    assert "comparisons" in data
    assert data.get("_dry_run") is True


def test_placeholder_verdict_file_default_is_done(monkeypatch, tmp_path):
    monkeypatch.delenv(ENV_DRY_VERDICT, raising=False)
    out = claude_runner._placeholder_for(VERDICT_FILE, tmp_path, "review")
    data = json.loads(out)
    assert data["decision"] == Decision.DONE


def test_placeholder_verdict_file_honors_forced_verdict(monkeypatch, tmp_path):
    """Setting OMICAIRN_DRY_VERDICT must override the default verdict.
    This is the hook the orchestrator tests rely on to drive
    backtracking scenarios."""
    monkeypatch.setenv(ENV_DRY_VERDICT, Decision.RETRY_EXECUTE)
    out = claude_runner._placeholder_for(VERDICT_FILE, tmp_path, "cross_check")
    data = json.loads(out)
    assert data["decision"] == Decision.RETRY_EXECUTE


def test_placeholder_findings_file(tmp_path):
    out = claude_runner._placeholder_for(FINDINGS_FILE, tmp_path, "cross_check")
    assert len(out.encode()) >= 32


def test_placeholder_analysis_md_has_metrics(tmp_path):
    out = claude_runner._placeholder_for("analysis.md", tmp_path, "execute")
    # Analysis text must include the auc+delta that results.json
    # declares, since downstream tools may parse it.
    assert "AUC=0.82" in out


def test_placeholder_preregistration_json(tmp_path):
    out = claude_runner._placeholder_for("preregistration.json", tmp_path, "plan")
    data = json.loads(out)
    assert "primary_hypothesis" in data


def test_placeholder_unknown_md_extension_is_non_trivial(tmp_path):
    """Any .md file we don't have a special branch for must still
    produce a contract-passing (>32B) stub."""
    out = claude_runner._placeholder_for("some_new_file.md", tmp_path, "review")
    assert len(out.encode()) >= 32
    assert ".md" not in out[:5]  # starts with the title, not the raw filename


def test_placeholder_unknown_extension_fallback(tmp_path):
    """Fallback for any extension that isn't .md, .json, or .flag.
    Contracts check isn't strict on extension, but the placeholder
    must still produce SOME text so a future contract with a .csv
    or .txt required output doesn't trip over empty writes."""
    out = claude_runner._placeholder_for("dataset.csv", tmp_path, "execute")
    assert "dataset.csv" in out
    assert "execute" in out


def test_placeholder_unknown_json_is_valid_json(tmp_path):
    """Any .json file we don't have a special branch for must still
    produce valid JSON (so downstream parsers don't blow up)."""
    out = claude_runner._placeholder_for(
        "something_new.json", tmp_path, "plan"
    )
    data = json.loads(out)
    assert data.get("stage") == "plan"
    assert data.get("dry_run") is True


def test_placeholder_legends_md_has_figure_block(tmp_path):
    out = claude_runner._placeholder_for(
        "figures/legends.md", tmp_path, "execute"
    )
    assert "Fig" in out
    assert len(out.encode()) >= 32


# ---------------------------------------------------------------------------
# _dry_run_write_contract — full per-phase placeholder population
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "phase",
    [phases.SCOPE, phases.PLAN, phases.EXECUTE, phases.REVIEW],
)
def test_dry_run_write_contract_populates_all_required_outputs(
    phase, tmp_path
):
    """Every required_outputs file for every LLM phase must be written
    by _dry_run_write_contract, and every file must pass contracts.verify
    (no trivials, nothing missing). This is the core invariant every
    dry-run test in the suite implicitly depends on."""
    stage_dir = tmp_path / "stage"
    log_path = tmp_path / "logs" / "stage.log"
    result = claude_runner._dry_run_write_contract(
        stage_dir, phase.name, log_path
    )
    # Contract file was written with dry_run flag.
    assert result.dry_run is True
    assert result.returncode == 0
    # Every required file must now exist AND pass contracts.verify.
    from omicairn import contracts
    verify = contracts.verify(phase, stage_dir)
    assert verify.ok, (
        f"dry-run for phase {phase.name} failed contract verification: "
        f"{verify.summary()}"
    )
    # Log was also written.
    assert log_path.exists()


def test_dry_run_write_contract_log_path_parent_created(tmp_path):
    """The log path's parent dir does NOT need to exist beforehand —
    the helper must mkdir(parents=True)."""
    deep_log = tmp_path / "a" / "b" / "c" / "stage.log"
    assert not deep_log.parent.exists()
    result = claude_runner._dry_run_write_contract(
        tmp_path / "stage", phases.SCOPE.name, deep_log
    )
    assert result.returncode == 0
    assert deep_log.exists()


def test_run_dispatches_to_dry_run_when_env_set(monkeypatch, tmp_path):
    """claude_runner.run() must short-circuit to _dry_run_write_contract
    when OMICAIRN_DRY_RUN=1 AND stage_dir+phase_name are provided."""
    monkeypatch.setenv(ENV_DRY_RUN, "1")
    stage_dir = tmp_path / "stage"
    log_path = tmp_path / "logs" / "run.log"
    result = claude_runner.run(
        prompt="ignored in dry run",
        workspace_root=tmp_path,
        data_dir=tmp_path,
        allowed_tools=(),
        model="dummy",
        timeout_seconds=60,
        log_path=log_path,
        stage_dir=stage_dir,
        phase_name=phases.SCOPE.name,
    )
    assert result.dry_run is True
    assert result.returncode == 0
    # SCOPE's required_outputs must all exist.
    from omicairn import contracts
    assert contracts.verify(phases.SCOPE, stage_dir).ok
    # OMICAIRN_DRY_RUN=1 must NOT write the render-mode diagnostic files.
    assert not (stage_dir / "_rendered_prompt.md").exists()
    assert not (stage_dir / "_render_check.json").exists()


# ---------------------------------------------------------------------------
# OMICAIRN_DRY_RUN=render mode — prompt-render smoke without claude tokens
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("env_val", ["render", "RENDER", "Render"])
def test_run_render_mode_writes_rendered_prompt_and_check(
    monkeypatch, tmp_path, env_val
):
    """OMICAIRN_DRY_RUN=render must write <stage_dir>/_rendered_prompt.md
    with the fully-rendered prompt, plus _render_check.json summarising
    prompt length and unresolved-placeholder count. Case-insensitive
    on the env value. Standard contract artefacts are still written."""
    monkeypatch.setenv(ENV_DRY_RUN, env_val)
    stage_dir = tmp_path / "stage"
    log_path = tmp_path / "logs" / "run.log"
    rendered = "# Fully rendered prompt\n\nAll tokens substituted.\n"
    result = claude_runner.run(
        prompt=rendered,
        workspace_root=tmp_path,
        data_dir=tmp_path,
        allowed_tools=(),
        model="dummy",
        timeout_seconds=60,
        log_path=log_path,
        stage_dir=stage_dir,
        phase_name=phases.SCOPE.name,
    )
    assert result.dry_run is True
    assert result.returncode == 0
    # Render-mode diagnostic files exist and round-trip correctly.
    rp = stage_dir / "_rendered_prompt.md"
    assert rp.exists()
    assert rp.read_text() == rendered
    rc = stage_dir / "_render_check.json"
    assert rc.exists()
    check = json.loads(rc.read_text())
    assert check["phase"] == phases.SCOPE.name
    assert check["prompt_chars"] == len(rendered)
    assert check["unresolved_placeholder_count"] == 0
    assert check["unresolved_examples"] == []
    # Standard placeholder artefacts are still written.
    from omicairn import contracts
    assert contracts.verify(phases.SCOPE, stage_dir).ok


def test_run_render_mode_detects_unresolved_placeholders(
    monkeypatch, tmp_path
):
    """Surviving ``{{token}}`` markers are surfaced in
    unresolved_placeholder_count + unresolved_examples — the whole
    point of the render-mode smoke step is to catch this class of
    prompt-template wiring bug at zero token cost.
    """
    monkeypatch.setenv(ENV_DRY_RUN, "render")
    stage_dir = tmp_path / "stage"
    log_path = tmp_path / "logs" / "run.log"
    rendered = (
        "# Prompt with bugs\n"
        "{{unresolved_one}} text {{unresolved_two}} more {{unresolved_one}}\n"
    )
    claude_runner.run(
        prompt=rendered,
        workspace_root=tmp_path,
        data_dir=tmp_path,
        allowed_tools=(),
        model="dummy",
        timeout_seconds=60,
        log_path=log_path,
        stage_dir=stage_dir,
        phase_name=phases.SCOPE.name,
    )
    check = json.loads((stage_dir / "_render_check.json").read_text())
    # 3 total occurrences (one duplicate of unresolved_one).
    assert check["unresolved_placeholder_count"] == 3
    # Examples are deduped but ordered by first appearance.
    assert check["unresolved_examples"] == [
        "{{unresolved_one}}", "{{unresolved_two}}",
    ]


def test_dry_run_mode_unset_returns_none(monkeypatch):
    """_dry_run_mode() returns None when the env var is falsy."""
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)
    assert claude_runner._dry_run_mode() is None


def test_dry_run_mode_returns_lowercased_value(monkeypatch):
    monkeypatch.setenv(ENV_DRY_RUN, "Render")
    assert claude_runner._dry_run_mode() == "render"
    monkeypatch.setenv(ENV_DRY_RUN, "  1  ")
    assert claude_runner._dry_run_mode() == "1"


# ---------------------------------------------------------------------------
# run() — real subprocess path (monkeypatched)
# ---------------------------------------------------------------------------
#
# Lines 187-242 of claude_runner.py handle the actual `claude -p`
# subprocess invocation. Running the real binary is not viable
# (CI has no `claude`, cost, latency), so these tests monkeypatch
# `subprocess.run` to simulate success, timeout, and FileNotFoundError
# outcomes. This pins:
#
#   1. cmd list construction (exact flag ordering claude-code expects)
#   2. --add-dir duplication when data_dir is outside workspace_root
#   3. optional append_system_prompt / max_budget_usd injection
#   4. log header shape and cmd echo
#   5. TimeoutExpired → returncode=-1, timed_out=True
#   6. FileNotFoundError (claude CLI missing) → returncode=-2


def _fake_proc(returncode: int = 0):
    class _Fake:
        def __init__(self, rc: int):
            self.returncode = rc

    return _Fake(returncode)


def test_run_real_path_builds_cmd_and_returns_success(
    monkeypatch, tmp_path
):
    """Non-dry-run path: subprocess.run is monkeypatched to return
    returncode=0. Assert the cmd list the runner built has every
    flag claude-code expects, and the ClaudeResult reflects success.
    Also passes stage_dir so the `# stage_dir:` log line is exercised."""
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        captured["kwargs"] = kwargs
        return _fake_proc(returncode=0)

    monkeypatch.setattr(claude_runner.subprocess, "run", fake_run)
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()
    data_dir = workspace_root / "data"
    data_dir.mkdir()
    stage_dir = workspace_root / "000_scope"
    log_path = tmp_path / "logs" / "stage.log"

    result = claude_runner.run(
        prompt="do the thing",
        workspace_root=workspace_root,
        data_dir=data_dir,
        allowed_tools=("Read", "Write"),
        model="claude-sonnet-4-6",
        timeout_seconds=120,
        log_path=log_path,
        stage_dir=stage_dir,
    )

    assert result.returncode == 0
    assert result.timed_out is False
    assert result.dry_run is False
    assert result.stdout_path == log_path
    # cmd construction: starts with claude --print (the prompt arrives on
    # STDIN, not as a positional/`-p` arg, so a huge prompt can't overflow
    # ARG_MAX), has --add-dir, --model, --allowedTools with both tools inlined.
    cmd = captured["cmd"]
    assert cmd[0] == "claude"
    assert "--print" in cmd
    # The prompt MUST NOT appear as an argv element.
    assert "do the thing" not in cmd
    # It is fed via an open stdin file handle instead.
    assert captured["kwargs"].get("stdin") is not None
    assert "--add-dir" in cmd
    assert str(workspace_root) in cmd
    # On the default (Anthropic API) backend the model id reaches the CLI
    # unchanged (see normalize_model_id for the Bedrock-only rewrite).
    assert "--model" in cmd
    assert "claude-sonnet-4-6" in cmd
    assert "--allowedTools" in cmd
    assert "Read" in cmd and "Write" in cmd
    assert "--permission-mode" in cmd
    # data_dir is inside workspace_root, so no second --add-dir is added.
    assert cmd.count("--add-dir") == 1
    # Log header written, including the stage_dir breadcrumb.
    log_text = log_path.read_text()
    assert "# OmiCairn phase launch" in log_text
    assert "# cwd:" in log_text
    assert "# stage_dir:" in log_text
    assert str(stage_dir) in log_text
    assert "# --- begin claude output ---" in log_text


def test_run_real_path_adds_second_add_dir_when_data_outside_workspace(
    monkeypatch, tmp_path
):
    """When data_dir is not under workspace_root, the runner must add
    a second --add-dir so claude can read the data. This is the only
    way external datasets reach the subprocess sandbox."""
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        return _fake_proc(returncode=0)

    monkeypatch.setattr(claude_runner.subprocess, "run", fake_run)
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()
    data_dir = tmp_path / "external_data"  # NOT under workspace_root
    data_dir.mkdir()

    claude_runner.run(
        prompt="x",
        workspace_root=workspace_root,
        data_dir=data_dir,
        allowed_tools=(),
        model="m",
        timeout_seconds=60,
        log_path=tmp_path / "log.txt",
    )
    cmd = captured["cmd"]
    assert cmd.count("--add-dir") == 2
    assert str(data_dir) in cmd


def test_run_real_path_appends_system_prompt_and_budget(
    monkeypatch, tmp_path
):
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        return _fake_proc(returncode=0)

    monkeypatch.setattr(claude_runner.subprocess, "run", fake_run)
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()
    stage_dir = workspace_root / "000_scope"

    claude_runner.run(
        prompt="x",
        workspace_root=workspace_root,
        data_dir=workspace_root,
        allowed_tools=(),
        model="m",
        timeout_seconds=60,
        log_path=tmp_path / "log.txt",
        append_system_prompt="extra instructions",
        max_budget_usd=2.5,
        stage_dir=stage_dir,
    )
    cmd = captured["cmd"]
    # The append-system-prompt is delivered via a FILE (off argv), so the
    # large text can't overflow ARG_MAX. The file path is on argv; the text
    # itself is NOT.
    assert "--append-system-prompt-file" in cmd
    assert "--append-system-prompt" not in cmd
    assert "extra instructions" not in cmd
    idx = cmd.index("--append-system-prompt-file")
    sp_path = pathlib.Path(cmd[idx + 1])
    # The file lives in the stage dir while the run is in flight; the
    # cleanup finally unlinks it afterwards, so assert on the captured cmd
    # path shape + the constant rather than the (now-removed) file.
    assert sp_path.name == claude_runner.APPEND_SYSTEM_PROMPT_FILENAME
    assert "--max-budget-usd" in cmd
    idx = cmd.index("--max-budget-usd")
    assert cmd[idx + 1] == "2.5"


def test_run_huge_prompt_routed_via_stdin_not_argv(monkeypatch, tmp_path):
    """Regression for the Errno 7 crash (audit 2026-06-27): a
    prompt LARGER than ARG_MAX must NOT be placed on argv (which would make
    execve raise `OSError: [Errno 7] Argument list too long`). Instead it is
    streamed to `claude --print` on stdin. We mock subprocess.run to behave
    like a real execve: if the assembled argv exceeds ARG_MAX it raises the
    same OSError the kernel would, so the test FAILS pre-fix and PASSES once
    the prompt is off argv. We then assert the prompt is fed via stdin and
    that the assembled argv stays tiny."""
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)

    # A prompt bigger than any plausible ARG_MAX (5 MiB; host ARG_MAX is
    # ~2.6 MiB), plus a large appended system prompt.
    huge_prompt = "X" * (5 * 1024 * 1024)
    huge_sysprompt = "S" * (2 * 1024 * 1024)
    captured: dict = {}

    try:
        arg_max = os.sysconf("SC_ARG_MAX")
    except (ValueError, OSError, AttributeError):
        arg_max = 128 * 1024

    def fake_run(cmd, **kwargs):
        # Faithfully mimic execve's ARG_MAX accounting: sum of arg bytes +
        # NUL terminators. If we ever put a huge arg on argv this raises the
        # exact OSError the orchestrator died on.
        argv_bytes = sum(len(a.encode("utf-8")) + 1 for a in cmd)
        if argv_bytes > arg_max:
            raise OSError(7, "Argument list too long", "claude")
        captured["cmd"] = list(cmd)
        captured["kwargs"] = kwargs
        captured["argv_bytes"] = argv_bytes
        return _fake_proc(returncode=0)

    monkeypatch.setattr(claude_runner.subprocess, "run", fake_run)
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()
    stage_dir = workspace_root / "043_review"

    result = claude_runner.run(
        prompt=huge_prompt,
        workspace_root=workspace_root,
        data_dir=workspace_root,
        allowed_tools=("Read", "Write"),
        model="m",
        timeout_seconds=60,
        log_path=tmp_path / "log.txt",
        append_system_prompt=huge_sysprompt,
        stage_dir=stage_dir,
    )

    # No Errno 7: the run completed cleanly.
    assert result.returncode == 0
    assert result.timed_out is False
    # Neither the huge prompt nor the huge system prompt is on argv.
    cmd = captured["cmd"]
    assert huge_prompt not in cmd
    assert huge_sysprompt not in cmd
    # argv stays tiny (well under the safe ARG_MAX fraction) regardless of
    # prompt size.
    assert captured["argv_bytes"] < claude_runner._argv_safe_limit()
    # The prompt is delivered via an open stdin file handle.
    assert captured["kwargs"].get("stdin") is not None
    # The system prompt is delivered via the file flag (path on argv only).
    assert claude_runner.CLAUDE_FLAG_APPEND_SYSTEM_PROMPT_FILE in cmd


def test_run_omits_budget_flag_when_unlimited(monkeypatch, tmp_path):
    """Unlimited (max_budget_usd=None, the post-2026-06-27 default) must NOT
    place --max-budget-usd on the `claude` argv. With the flag absent, the
    `claude -p` subprocess has no per-phase spend cap and never self-halts on
    budget — this is the code path that makes "default unlimited" mean "no
    halt". Mirrors test_run_real_path_appends_system_prompt_and_budget for the
    None case."""
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        return _fake_proc(returncode=0)

    monkeypatch.setattr(claude_runner.subprocess, "run", fake_run)
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()

    # Default call path: max_budget_usd defaults to None (unlimited).
    claude_runner.run(
        prompt="x",
        workspace_root=workspace_root,
        data_dir=workspace_root,
        allowed_tools=(),
        model="m",
        timeout_seconds=60,
        log_path=tmp_path / "log.txt",
    )
    assert "--max-budget-usd" not in captured["cmd"]

    # Explicit None is also treated as unlimited (flag omitted).
    claude_runner.run(
        prompt="x",
        workspace_root=workspace_root,
        data_dir=workspace_root,
        allowed_tools=(),
        model="m",
        timeout_seconds=60,
        log_path=tmp_path / "log2.txt",
        max_budget_usd=None,
    )
    assert "--max-budget-usd" not in captured["cmd"]


def test_run_real_path_timeout_sets_timed_out_and_negative_rc(
    monkeypatch, tmp_path
):
    """subprocess.TimeoutExpired must be caught and reported as
    returncode=-1, timed_out=True. _run_llm_stage depends on this
    exact contract to route the stage into the TIMEOUT error branch."""
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)

    def fake_run(cmd, **kwargs):
        raise claude_runner.subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout", 0))

    monkeypatch.setattr(claude_runner.subprocess, "run", fake_run)
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()

    result = claude_runner.run(
        prompt="x",
        workspace_root=workspace_root,
        data_dir=workspace_root,
        allowed_tools=(),
        model="m",
        timeout_seconds=1,
        log_path=tmp_path / "log.txt",
    )
    assert result.returncode == -1
    assert result.timed_out is True
    assert result.wall_seconds >= 0.0


def test_run_real_path_missing_claude_cli_sets_rc_minus_2(
    monkeypatch, tmp_path
):
    """FileNotFoundError (claude binary not on PATH) must be caught
    and reported as returncode=-2, NOT re-raised. _run_llm_stage
    maps this to CLI_MISSING and halts the run."""
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)

    def fake_run(cmd, **kwargs):
        raise FileNotFoundError("No such file or directory: 'claude'")

    monkeypatch.setattr(claude_runner.subprocess, "run", fake_run)
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()
    log_path = tmp_path / "log.txt"

    result = claude_runner.run(
        prompt="x",
        workspace_root=workspace_root,
        data_dir=workspace_root,
        allowed_tools=(),
        model="m",
        timeout_seconds=60,
        log_path=log_path,
    )
    assert result.returncode == -2
    assert result.timed_out is False
    # The error must be appended to the log so post-mortem debugging
    # can find "why did this halt CLI_MISSING".
    log_text = log_path.read_text()
    assert "claude CLI not found" in log_text


# ---------------------------------------------------------------------------
# Transient failure detection + retry logic
# ---------------------------------------------------------------------------

def test_is_transient_failure_detects_429(tmp_path):
    log = tmp_path / "log.txt"
    log.write_text("Error: 429 Too Many Requests\n")
    assert claude_runner._is_transient_failure(log, returncode=1) is True


def test_is_transient_failure_detects_rate_limit(tmp_path):
    log = tmp_path / "log.txt"
    log.write_text("anthropic.RateLimitError: rate_limit_error\n")
    assert claude_runner._is_transient_failure(log, returncode=1) is True


def test_is_transient_failure_ignores_normal_exit(tmp_path):
    """A non-zero exit with no transient pattern is a genuine failure."""
    log = tmp_path / "log.txt"
    log.write_text("Error: invalid tool call\n")
    assert claude_runner._is_transient_failure(log, returncode=1) is False


def test_is_transient_failure_ignores_timeout(tmp_path):
    """returncode -1 (timeout) is not transient — the orchestrator has
    its own timeout handling."""
    log = tmp_path / "log.txt"
    log.write_text("429 Too Many Requests\n")
    assert claude_runner._is_transient_failure(log, returncode=-1) is False


def test_is_transient_failure_ignores_cli_missing(tmp_path):
    """returncode -2 (CLI missing) is not transient."""
    log = tmp_path / "log.txt"
    log.write_text("429\n")
    assert claude_runner._is_transient_failure(log, returncode=-2) is False


def test_is_transient_failure_missing_log_returns_false(tmp_path):
    """If the log file doesn't exist, fail safe → not transient."""
    log = tmp_path / "nonexistent.log"
    assert claude_runner._is_transient_failure(log, returncode=1) is False


def test_is_transient_failure_ignores_preamble_timestamp_false_positive(tmp_path):
    """REGRESSION (latent flake, 2026-06-10): the launch preamble carries
    `# ts: <time.time()>` whose digits can incidentally contain a transient
    token like "502"/"503"/"429", and the cmd dump echoes the workspace path
    + verbatim prompt. A genuinely non-transient failure (`tool not allowed`)
    must NOT be misclassified as transient just because the PREAMBLE contains
    such a substring — only the post-sentinel region is scanned. Here the ts
    contains "502" and the prompt quotes "rate limit", yet the real output is
    a hard error → not transient."""
    log = tmp_path / "log.txt"
    log.write_text(
        "# OmiCairn phase launch\n"
        "# ts: 1781114050.4502997\n"          # contains '502'
        "# cmd:\n#   [2] explain the 429 rate limit policy\n"  # benign prompt echo
        f"{claude_runner._LOG_OUTPUT_SENTINEL}\n"
        "Error: tool not allowed\n"            # the ACTUAL, non-transient output
    )
    assert claude_runner._is_transient_failure(log, returncode=1) is False


def test_is_transient_failure_detects_pattern_after_sentinel(tmp_path):
    """The post-sentinel region IS still scanned — a real transient error in
    the actual output (after the sentinel) is detected even with a clean
    preamble."""
    log = tmp_path / "log.txt"
    log.write_text(
        "# OmiCairn phase launch\n# ts: 1700000000.0\n"
        f"{claude_runner._LOG_OUTPUT_SENTINEL}\n"
        "anthropic.InternalServerError: 503 Service Unavailable\n"
    )
    assert claude_runner._is_transient_failure(log, returncode=1) is True


def test_max_retries_default(monkeypatch):
    monkeypatch.delenv(claude_runner.ENV_MAX_RETRIES, raising=False)
    assert claude_runner._max_retries() == claude_runner._DEFAULT_MAX_RETRIES


def test_max_retries_from_env(monkeypatch):
    monkeypatch.setenv(claude_runner.ENV_MAX_RETRIES, "5")
    assert claude_runner._max_retries() == 5


def test_max_retries_invalid_env_falls_back(monkeypatch):
    monkeypatch.setenv(claude_runner.ENV_MAX_RETRIES, "not_a_number")
    assert claude_runner._max_retries() == claude_runner._DEFAULT_MAX_RETRIES


def test_max_retries_negative_clamped_to_zero(monkeypatch):
    monkeypatch.setenv(claude_runner.ENV_MAX_RETRIES, "-3")
    assert claude_runner._max_retries() == 0


def test_run_retries_on_transient_429_then_succeeds(monkeypatch, tmp_path):
    """Subprocess fails twice with a 429 pattern in output, then succeeds
    on the third attempt. Verify retries=2 and final rc=0."""
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)
    monkeypatch.setenv(claude_runner.ENV_MAX_RETRIES, "2")

    call_count = 0

    def fake_run(cmd, **kwargs):
        nonlocal call_count
        call_count += 1
        stdout = kwargs.get("stdout")
        if call_count <= 2:
            if stdout:
                stdout.write("Error: 429 Too Many Requests\n")
            return _fake_proc(returncode=1)
        return _fake_proc(returncode=0)

    monkeypatch.setattr(claude_runner.subprocess, "run", fake_run)
    monkeypatch.setattr(claude_runner.time, "sleep", lambda _: None)
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()
    log_path = tmp_path / "log.txt"

    result = claude_runner.run(
        prompt="x",
        workspace_root=workspace_root,
        data_dir=workspace_root,
        allowed_tools=(),
        model="m",
        timeout_seconds=60,
        log_path=log_path,
    )
    assert result.returncode == 0
    assert result.retries == 2
    assert call_count == 3
    # Log should contain retry markers.
    log_text = log_path.read_text()
    assert "retry attempt" in log_text
    assert "transient failure detected" in log_text


def test_run_does_not_retry_on_non_transient_failure(monkeypatch, tmp_path):
    """A non-zero exit with no transient pattern should not be retried."""
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)
    monkeypatch.setenv(claude_runner.ENV_MAX_RETRIES, "2")

    call_count = 0

    def fake_run(cmd, **kwargs):
        nonlocal call_count
        call_count += 1
        stdout = kwargs.get("stdout")
        if stdout:
            stdout.write("Error: tool not allowed\n")
        return _fake_proc(returncode=1)

    monkeypatch.setattr(claude_runner.subprocess, "run", fake_run)
    monkeypatch.setattr(claude_runner.time, "sleep", lambda _: None)
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()

    result = claude_runner.run(
        prompt="x",
        workspace_root=workspace_root,
        data_dir=workspace_root,
        allowed_tools=(),
        model="m",
        timeout_seconds=60,
        log_path=tmp_path / "log.txt",
    )
    assert result.returncode == 1
    assert result.retries == 0
    assert call_count == 1


def test_run_retries_exhausted_returns_last_failure(monkeypatch, tmp_path):
    """When all retry attempts are transient failures, the last failure's
    returncode is returned with retries=max_retries."""
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)
    monkeypatch.setenv(claude_runner.ENV_MAX_RETRIES, "2")

    call_count = 0

    def fake_run(cmd, **kwargs):
        nonlocal call_count
        call_count += 1
        stdout = kwargs.get("stdout")
        if stdout:
            stdout.write("Error: 429 rate limit exceeded\n")
        return _fake_proc(returncode=1)

    monkeypatch.setattr(claude_runner.subprocess, "run", fake_run)
    monkeypatch.setattr(claude_runner.time, "sleep", lambda _: None)
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()

    result = claude_runner.run(
        prompt="x",
        workspace_root=workspace_root,
        data_dir=workspace_root,
        allowed_tools=(),
        model="m",
        timeout_seconds=60,
        log_path=tmp_path / "log.txt",
    )
    assert result.returncode == 1
    assert result.retries == 2
    assert call_count == 3


def test_run_no_retry_on_timeout(monkeypatch, tmp_path):
    """Timeouts are not retried — the orchestrator handles them."""
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)
    monkeypatch.setenv(claude_runner.ENV_MAX_RETRIES, "2")

    call_count = 0

    def fake_run(cmd, **kwargs):
        nonlocal call_count
        call_count += 1
        raise claude_runner.subprocess.TimeoutExpired(
            cmd=cmd, timeout=kwargs.get("timeout", 0)
        )

    monkeypatch.setattr(claude_runner.subprocess, "run", fake_run)
    monkeypatch.setattr(claude_runner.time, "sleep", lambda _: None)
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()

    result = claude_runner.run(
        prompt="x",
        workspace_root=workspace_root,
        data_dir=workspace_root,
        allowed_tools=(),
        model="m",
        timeout_seconds=1,
        log_path=tmp_path / "log.txt",
    )
    assert result.returncode == -1
    assert result.timed_out is True
    assert result.retries == 0
    assert call_count == 1


def test_run_zero_retries_skips_retry_logic(monkeypatch, tmp_path):
    """OMICAIRN_MAX_RETRIES=0 disables retries entirely — even transient
    failures return immediately."""
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)
    monkeypatch.setenv(claude_runner.ENV_MAX_RETRIES, "0")

    call_count = 0

    def fake_run(cmd, **kwargs):
        nonlocal call_count
        call_count += 1
        stdout = kwargs.get("stdout")
        if stdout:
            stdout.write("Error: 429 rate limit\n")
        return _fake_proc(returncode=1)

    monkeypatch.setattr(claude_runner.subprocess, "run", fake_run)
    monkeypatch.setattr(claude_runner.time, "sleep", lambda _: None)
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()

    result = claude_runner.run(
        prompt="x",
        workspace_root=workspace_root,
        data_dir=workspace_root,
        allowed_tools=(),
        model="m",
        timeout_seconds=60,
        log_path=tmp_path / "log.txt",
    )
    assert result.returncode == 1
    assert result.retries == 0
    assert call_count == 1


# ---------------------------------------------------------------------------
# Claude Code session-cap detection (account-level 5-hour limit)
# ---------------------------------------------------------------------------

def test_detect_session_cap_returns_none_for_clean_log(tmp_path):
    log = tmp_path / "log.txt"
    log.write_text("normal output, no limit reached\n")
    assert claude_runner.detect_session_cap(log) is None


def test_detect_session_cap_returns_none_for_missing_file(tmp_path):
    assert claude_runner.detect_session_cap(tmp_path / "missing.log") is None


def test_detect_session_cap_parses_reset_time_and_tz(tmp_path):
    log = tmp_path / "log.txt"
    log.write_text(
        "# --- begin claude output ---\n"
        "You've hit your limit \u00b7 resets 6am (Etc/UTC)\n"
    )
    cap = claude_runner.detect_session_cap(log)
    assert cap is not None
    assert cap["reset_text"].lower() == "6am"
    assert cap["reset_tz"] == "Etc/UTC"


def test_detect_session_cap_matches_session_limit_phrasing(tmp_path):
    """REGRESSION: the CLI emits "...hit your SESSION limit..." — the
    extra word broke the bare "hit your limit" trigger, so detection
    returned None and auto-resume silently never engaged. The parkinson
    2026-05 runs hard-failed in 2s instead of sleeping to reset.
    This is the EXACT observed line."""
    log = tmp_path / "log.txt"
    log.write_text(
        "# --- begin claude output ---\n"
        "You've hit your session limit · resets 7:40am (Etc/UTC)\n"
    )
    cap = claude_runner.detect_session_cap(log)
    assert cap is not None, "session-limit phrasing must be detected"
    assert cap["reset_text"].lower() == "7:40am"
    assert cap["reset_tz"] == "Etc/UTC"
    # and the reset must parse to a concrete UTC datetime for auto-resume
    assert claude_runner._parse_session_reset(cap) is not None


def test_detect_session_cap_returns_dict_with_nones_when_format_unknown(tmp_path):
    """Pattern matched but reset hint absent — still report the cap."""
    log = tmp_path / "log.txt"
    log.write_text("Claude usage limit reached. Try again later.\n")
    cap = claude_runner.detect_session_cap(log)
    assert cap == {
        "reset_text": None,
        "reset_tz": None,
        "reset_raw_line": "Claude usage limit reached. Try again later.",
    }


def test_run_marks_session_cap_when_cli_exits_zero_with_limit_message(
    monkeypatch, tmp_path
):
    """Real-world failure mode: Claude Code prints the cap message and
    exits 0, leaving the runner with returncode=0 and no contract files.
    The runner must flag this so the orchestrator can route to
    SESSION_LIMIT instead of CONTRACT_FAIL."""
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)

    def fake_run(cmd, **kwargs):
        stdout = kwargs.get("stdout")
        if stdout:
            stdout.write(
                "You've hit your limit \u00b7 resets 6am (Etc/UTC)\n"
            )
        return _fake_proc(returncode=0)

    monkeypatch.setattr(claude_runner.subprocess, "run", fake_run)
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()

    result = claude_runner.run(
        prompt="x",
        workspace_root=workspace_root,
        data_dir=workspace_root,
        allowed_tools=(),
        model="m",
        timeout_seconds=60,
        log_path=tmp_path / "log.txt",
    )
    assert result.returncode == 0
    assert result.session_cap is not None
    assert result.session_cap["reset_text"].lower() == "6am"
    assert result.session_cap["reset_tz"] == "Etc/UTC"


def test_run_session_cap_is_none_for_normal_success(monkeypatch, tmp_path):
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)

    def fake_run(cmd, **kwargs):
        stdout = kwargs.get("stdout")
        if stdout:
            stdout.write("done\n")
        return _fake_proc(returncode=0)

    monkeypatch.setattr(claude_runner.subprocess, "run", fake_run)
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()

    result = claude_runner.run(
        prompt="x",
        workspace_root=workspace_root,
        data_dir=workspace_root,
        allowed_tools=(),
        model="m",
        timeout_seconds=60,
        log_path=tmp_path / "log.txt",
    )
    assert result.returncode == 0
    assert result.session_cap is None


def test_run_session_cap_not_set_on_timeout(monkeypatch, tmp_path):
    """Timeout path skips cap detection — a partial log that happens to
    contain the words shouldn't produce a misleading SESSION_LIMIT."""
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)
    import subprocess as _sp

    def fake_run(cmd, **kwargs):
        raise _sp.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout", 1))

    monkeypatch.setattr(claude_runner.subprocess, "run", fake_run)
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()

    result = claude_runner.run(
        prompt="x",
        workspace_root=workspace_root,
        data_dir=workspace_root,
        allowed_tools=(),
        model="m",
        timeout_seconds=1,
        log_path=tmp_path / "log.txt",
    )
    assert result.timed_out is True
    assert result.session_cap is None


# ---------------------------------------------------------------------------
# .env loading (Tier-fix 2026-04-29)
# ---------------------------------------------------------------------------

def test_dotenv_parser_handles_quoted_export_and_comments():
    """Trivial KEY=value lines, with `export` prefix, quoted values,
    and inline `# ...` comments — all should parse cleanly."""
    text = (
        "# top-of-file comment\n"
        "PLAIN=value1\n"
        "export EXPORTED=value2\n"
        '  QUOTED_DOUBLE = "with spaces"  \n'
        "QUOTED_SINGLE='also spaces'\n"
        "WITH_INLINE=tightvalue # inline comment\n"
        "EMPTY=\n"
        "\n"
        "BAD line without equals\n"
        "12NOT_AN_IDENT=skip-me\n"
    )
    parsed = claude_runner._parse_dotenv_text(text)
    assert parsed == {
        "PLAIN": "value1",
        "EXPORTED": "value2",
        "QUOTED_DOUBLE": "with spaces",
        "QUOTED_SINGLE": "also spaces",
        "WITH_INLINE": "tightvalue",
        "EMPTY": "",
    }


def test_dotenv_loaded_into_subprocess_env(tmp_path, monkeypatch):
    """Run path: when a `.env` exists, its keys are merged into the
    subprocess `env` dict that fake_run captures via os.environ.copy()."""
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv(claude_runner._DOTENV_PATH_OVERRIDE_ENV, raising=False)

    ws = tmp_path / "workspaces" / "case-20260429"
    ws.mkdir(parents=True)
    repo_root = tmp_path
    (repo_root / ".env").write_text(
        "CLAUDE_CODE_OAUTH_TOKEN=oauth-from-dotenv\n"
        "ANOTHER_KEY=hello\n"
    )

    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["env"] = dict(kwargs.get("env") or {})
        return _fake_proc(returncode=0)

    monkeypatch.setattr(claude_runner.subprocess, "run", fake_run)

    claude_runner.run(
        prompt="x",
        workspace_root=ws,
        data_dir=ws,
        allowed_tools=(),
        model="m",
        timeout_seconds=60,
        log_path=tmp_path / "log.txt",
    )

    assert captured["env"].get("CLAUDE_CODE_OAUTH_TOKEN") == "oauth-from-dotenv"
    assert captured["env"].get("ANOTHER_KEY") == "hello"


def test_env_hf_loaded_into_subprocess_env(tmp_path, monkeypatch):
    """A sibling `.env.hf` (dedicated HF-creds file) is loaded with the
    same fallback semantics as `.env`, so HF_TOKEN propagates into the
    subprocess env (and thence the agent forwards it to the remote job)."""
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv(claude_runner._DOTENV_PATH_OVERRIDE_ENV, raising=False)

    ws = tmp_path / "workspaces" / "case-20260616"
    ws.mkdir(parents=True)
    (tmp_path / ".env.hf").write_text("HF_TOKEN=hf_fake_test_value\n")

    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["env"] = dict(kwargs.get("env") or {})
        return _fake_proc(returncode=0)

    monkeypatch.setattr(claude_runner.subprocess, "run", fake_run)
    claude_runner.run(
        prompt="x", workspace_root=ws, data_dir=ws, allowed_tools=(),
        model="m", timeout_seconds=60, log_path=tmp_path / "log.txt",
    )
    assert captured["env"].get("HF_TOKEN") == "hf_fake_test_value"


def test_dotenv_does_not_overwrite_existing_env(tmp_path, monkeypatch):
    """If the calling environment already has CLAUDE_CODE_OAUTH_TOKEN
    set, the .env value must NOT clobber it (matching standard dotenv
    semantics)."""
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)
    monkeypatch.delenv(claude_runner._DOTENV_PATH_OVERRIDE_ENV, raising=False)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "live-from-shell")

    ws = tmp_path / "workspaces" / "case-x"
    ws.mkdir(parents=True)
    (tmp_path / ".env").write_text(
        "CLAUDE_CODE_OAUTH_TOKEN=stale-from-dotenv\n"
    )

    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["env"] = dict(kwargs.get("env") or {})
        return _fake_proc(returncode=0)

    monkeypatch.setattr(claude_runner.subprocess, "run", fake_run)

    claude_runner.run(
        prompt="x",
        workspace_root=ws,
        data_dir=ws,
        allowed_tools=(),
        model="m",
        timeout_seconds=60,
        log_path=tmp_path / "log.txt",
    )

    assert captured["env"].get("CLAUDE_CODE_OAUTH_TOKEN") == "live-from-shell"


def test_dotenv_explicit_override_path_wins(tmp_path, monkeypatch):
    """OMICAIRN_DOTENV env var overrides the search list."""
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)

    ws = tmp_path / "ws"
    ws.mkdir()
    nearby = tmp_path / ".env"
    nearby.write_text("CLAUDE_CODE_OAUTH_TOKEN=from-tmp-env\n")
    explicit = tmp_path / "alt" / "alternate.env"
    explicit.parent.mkdir()
    explicit.write_text("CLAUDE_CODE_OAUTH_TOKEN=from-explicit\n")
    monkeypatch.setenv(claude_runner._DOTENV_PATH_OVERRIDE_ENV, str(explicit))

    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["env"] = dict(kwargs.get("env") or {})
        return _fake_proc(returncode=0)

    monkeypatch.setattr(claude_runner.subprocess, "run", fake_run)

    claude_runner.run(
        prompt="x",
        workspace_root=ws,
        data_dir=ws,
        allowed_tools=(),
        model="m",
        timeout_seconds=60,
        log_path=tmp_path / "log.txt",
    )

    assert captured["env"].get("CLAUDE_CODE_OAUTH_TOKEN") == "from-explicit"


def test_dotenv_no_file_no_error(tmp_path, monkeypatch):
    """When no .env exists anywhere on the search path, run still
    succeeds (the loader silently no-ops)."""
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)
    monkeypatch.delenv(claude_runner._DOTENV_PATH_OVERRIDE_ENV, raising=False)

    ws = tmp_path / "lonely_ws"
    ws.mkdir()

    def fake_run(cmd, **kwargs):
        return _fake_proc(returncode=0)

    monkeypatch.setattr(claude_runner.subprocess, "run", fake_run)

    result = claude_runner.run(
        prompt="x",
        workspace_root=ws,
        data_dir=ws,
        allowed_tools=(),
        model="m",
        timeout_seconds=60,
        log_path=tmp_path / "log.txt",
    )
    assert result.returncode == 0


# ---------------------------------------------------------------------------
# Auto-resume on session cap (Tier-fix 2026-04-29)
# ---------------------------------------------------------------------------

def test_parse_session_reset_clock_with_iana_tz():
    cap = {"reset_text": "9:40pm", "reset_tz": "Etc/UTC", "reset_raw_line": ""}
    dt = claude_runner._parse_session_reset(cap)
    assert dt is not None
    # Always returned in UTC.
    assert dt.tzinfo is not None
    # Hour wraps to 21 in the given zone; just assert it's far in the future or near.
    delta = (dt - claude_runner.dt_module.datetime.now(claude_runner.dt_module.timezone.utc)).total_seconds()
    assert -3600 < delta < 25 * 3600


def test_parse_session_reset_relative_form():
    cap = {"reset_text": "2h 14m", "reset_tz": "", "reset_raw_line": ""}
    dt = claude_runner._parse_session_reset(cap)
    assert dt is not None
    delta = (dt - claude_runner.dt_module.datetime.now(claude_runner.dt_module.timezone.utc)).total_seconds()
    # 2h14m ≈ 8040s
    assert 7900 < delta < 8200


def test_parse_session_reset_iso():
    cap = {"reset_text": "2026-12-31T23:59:00Z", "reset_tz": "", "reset_raw_line": ""}
    dt = claude_runner._parse_session_reset(cap)
    assert dt is not None
    assert dt.year == 2026 and dt.month == 12 and dt.day == 31


def test_parse_session_reset_unparsable_returns_none():
    cap = {"reset_text": "soon", "reset_tz": "", "reset_raw_line": ""}
    assert claude_runner._parse_session_reset(cap) is None


def test_parse_session_reset_handles_abbreviated_tz():
    cap = {"reset_text": "11:00am", "reset_tz": "PT", "reset_raw_line": ""}
    dt = claude_runner._parse_session_reset(cap)
    assert dt is not None
    assert dt.tzinfo is not None


def test_auto_resume_disabled_via_env(monkeypatch):
    monkeypatch.setenv(claude_runner._AUTO_RESUME_ENV, "0")
    assert claude_runner._auto_resume_enabled() is False
    monkeypatch.setenv(claude_runner._AUTO_RESUME_ENV, "false")
    assert claude_runner._auto_resume_enabled() is False
    monkeypatch.setenv(claude_runner._AUTO_RESUME_ENV, "")
    assert claude_runner._auto_resume_enabled() is True
    monkeypatch.delenv(claude_runner._AUTO_RESUME_ENV, raising=False)
    assert claude_runner._auto_resume_enabled() is True


# ---------------------------------------------------------------------------
# Model-id normalization (Bedrock inference-profile form)
# ---------------------------------------------------------------------------
def test_normalize_is_noop_on_default_backend(monkeypatch):
    monkeypatch.delenv(claude_runner.ENV_NO_MODEL_NORMALIZE, raising=False)
    monkeypatch.delenv(claude_runner.ENV_USE_BEDROCK, raising=False)
    assert claude_runner.normalize_model_id("claude-opus-4-8") == "claude-opus-4-8"


def test_normalize_bare_anthropic_ids_get_global_prefix(monkeypatch):
    monkeypatch.delenv(claude_runner.ENV_NO_MODEL_NORMALIZE, raising=False)
    monkeypatch.setenv(claude_runner.ENV_USE_BEDROCK, "1")
    assert (claude_runner.normalize_model_id("claude-sonnet-4-6")
            == "global.anthropic.claude-sonnet-4-6")
    assert (claude_runner.normalize_model_id("claude-opus-4-8")
            == "global.anthropic.claude-opus-4-8")


def test_normalize_passes_through_already_qualified(monkeypatch):
    monkeypatch.delenv(claude_runner.ENV_NO_MODEL_NORMALIZE, raising=False)
    monkeypatch.setenv(claude_runner.ENV_USE_BEDROCK, "1")
    for mid in (
        "global.anthropic.claude-opus-4-8",
        "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        "eu.anthropic.claude-sonnet-4-6",
        "anthropic.claude-3-5-sonnet-20241022-v2:0",
        "arn:aws:bedrock:<YOUR_AWS_REGION>::foundation-model/x",
    ):
        assert claude_runner.normalize_model_id(mid) == mid


def test_normalize_opt_out_env(monkeypatch):
    monkeypatch.setenv(claude_runner.ENV_USE_BEDROCK, "1")
    monkeypatch.setenv(claude_runner.ENV_NO_MODEL_NORMALIZE, "1")
    assert claude_runner.normalize_model_id("claude-opus-4-8") == "claude-opus-4-8"


def test_normalize_handles_empty_and_whitespace(monkeypatch):
    monkeypatch.delenv(claude_runner.ENV_NO_MODEL_NORMALIZE, raising=False)
    monkeypatch.setenv(claude_runner.ENV_USE_BEDROCK, "1")
    assert claude_runner.normalize_model_id("") == ""
    assert (claude_runner.normalize_model_id("  claude-opus-4-8  ")
            == "global.anthropic.claude-opus-4-8")


# ---------------------------------------------------------------------------
# Inactivity watchdog (_run_with_watchdogs / _inactivity_timeout_s)
# ---------------------------------------------------------------------------
def test_inactivity_timeout_env_parsing(monkeypatch):
    monkeypatch.delenv(claude_runner.ENV_INACTIVITY_TIMEOUT, raising=False)
    assert claude_runner._inactivity_timeout_s() == claude_runner._DEFAULT_INACTIVITY_TIMEOUT_S
    monkeypatch.setenv(claude_runner.ENV_INACTIVITY_TIMEOUT, "120")
    assert claude_runner._inactivity_timeout_s() == 120.0
    monkeypatch.setenv(claude_runner.ENV_INACTIVITY_TIMEOUT, "0")
    assert claude_runner._inactivity_timeout_s() == 0.0
    monkeypatch.setenv(claude_runner.ENV_INACTIVITY_TIMEOUT, "junk")
    assert claude_runner._inactivity_timeout_s() == claude_runner._DEFAULT_INACTIVITY_TIMEOUT_S


def test_watchdog_disabled_runs_normally(tmp_path):
    """inactivity_timeout=0 → plain run; a quick command succeeds."""
    log = tmp_path / "out.log"
    with open(log, "w") as lf:
        rc, to = claude_runner._run_with_watchdogs(
            ["/bin/sh", "-c", "printf done"], cwd=str(tmp_path), env=dict(os.environ),
            log_file=lf, log_path=log, wall_timeout_s=30, inactivity_timeout_s=0)
    assert rc == 0 and to is False
    assert "done" in log.read_text()


def test_watchdog_lets_active_output_through(tmp_path):
    """A command that keeps emitting output is NOT killed by a short
    inactivity window (it stays active)."""
    log = tmp_path / "out.log"
    with open(log, "w") as lf:
        rc, to = claude_runner._run_with_watchdogs(
            ["/bin/sh", "-c", "for i in 1 2 3; do printf x; sleep 0.2; done"],
            cwd=str(tmp_path), env=dict(os.environ), log_file=lf, log_path=log,
            wall_timeout_s=30, inactivity_timeout_s=5)
    assert rc == 0 and to is False


def test_watchdog_kills_hung_silent_process(tmp_path):
    """A process that produces NO output for longer than the inactivity
    window is killed and reported as timed_out (so the orchestrator
    retries instead of blocking)."""
    log = tmp_path / "out.log"
    with open(log, "w") as lf:
        rc, to = claude_runner._run_with_watchdogs(
            ["/bin/sh", "-c", "sleep 60"],  # silent, long
            cwd=str(tmp_path), env=dict(os.environ), log_file=lf, log_path=log,
            wall_timeout_s=120, inactivity_timeout_s=1, poll_s=0.5)
    assert to is True
    assert "WATCHDOG" in log.read_text()


# ---------------------------------------------------------------------------
# Heartbeat liveness (audit 2026-06-10): a HEALTHILY-blocked long EXECUTE
# turn waiting on a remote/foreground GPU job emits NOTHING locally — it
# looks identical to a hung turn. The watchdog must treat a freshly-touched
# heartbeat file as liveness so it can't false-kill it, while STILL killing
# when BOTH the log AND the heartbeat are stale past the timeout.
# ---------------------------------------------------------------------------

def test_heartbeat_path_prefers_stage_dir_then_cwd(tmp_path):
    stage = tmp_path / "003_execute"
    assert (claude_runner._heartbeat_path(stage, tmp_path)
            == stage / claude_runner.HEARTBEAT_FILENAME)
    # No stage dir → cwd fallback sentinel.
    assert (claude_runner._heartbeat_path(None, tmp_path)
            == tmp_path / claude_runner.HEARTBEAT_FALLBACK_FILENAME)


def test_watchdog_not_killed_when_heartbeat_fresh_though_log_stale(tmp_path):
    """The whole point of FIX 1: a turn that emits NO local-log output but
    keeps `touch`ing its heartbeat (i.e. it is polling a remote GPU job)
    must NOT be killed, even with a short inactivity window. Here the
    subprocess writes nothing to the log yet touches the heartbeat every
    0.2s for ~2s — well past the 1s window — then exits 0. A log-only
    watchdog would have killed it; the heartbeat keeps it alive."""
    log = tmp_path / "out.log"
    hb = tmp_path / "003_execute" / claude_runner.HEARTBEAT_FILENAME
    hb.parent.mkdir(parents=True)
    # Silent on stdout (log never grows); only the heartbeat ticks.
    script = (
        f'i=0; while [ "$i" -lt 10 ]; do touch "{hb}"; sleep 0.2; '
        'i=$((i+1)); done'
    )
    with open(log, "w") as lf:
        rc, to = claude_runner._run_with_watchdogs(
            ["/bin/sh", "-c", script],
            cwd=str(tmp_path), env=dict(os.environ), log_file=lf, log_path=log,
            wall_timeout_s=30, inactivity_timeout_s=1, poll_s=0.3,
            heartbeat_path=hb)
    assert to is False, "fresh heartbeat must keep a log-silent turn alive"
    assert rc == 0
    assert "WATCHDOG" not in log.read_text()


def test_watchdog_kills_when_both_log_and_heartbeat_stale(tmp_path):
    """The negative half: when the turn emits no log output AND never
    touches the heartbeat (genuinely hung), the watchdog still kills it
    past the inactivity window and reports timed_out. The heartbeat file
    here is never created, so its mtime stays None throughout. No stage_dir
    is passed, so the file-write signal is inactive too."""
    log = tmp_path / "out.log"
    hb = tmp_path / "003_execute" / claude_runner.HEARTBEAT_FILENAME  # never touched
    with open(log, "w") as lf:
        rc, to = claude_runner._run_with_watchdogs(
            ["/bin/sh", "-c", "sleep 60"],  # silent AND no heartbeat
            cwd=str(tmp_path), env=dict(os.environ), log_file=lf, log_path=log,
            wall_timeout_s=120, inactivity_timeout_s=1, poll_s=0.5,
            heartbeat_path=hb)
    assert to is True
    assert "WATCHDOG" in log.read_text()


# ---------------------------------------------------------------------------
# File-write liveness (audit 2026-06-11): a pure-LOCAL EXECUTE turn (CPU
# scoring / data-prep, NO remote GPU job) never enters the heartbeat poll
# loop AND buffers stdout under --output-format text, so neither the log nor
# the heartbeat advances while it is plainly alive writing code/results into
# the stage dir. A SAR-Gate EXECUTE wrote 1041 LOC then got false-killed at
# 2700s. The watchdog must treat a fresh stage-dir file write as liveness,
# while STILL killing when log AND heartbeat AND file writes are all stale.
# ---------------------------------------------------------------------------

def test_newest_file_mtime_none_for_missing_or_none(tmp_path):
    """None root, or a dir that doesn't exist, → None (signal inactive)."""
    assert claude_runner._newest_file_mtime(None) is None
    assert claude_runner._newest_file_mtime(tmp_path / "nope") is None


def test_newest_file_mtime_tracks_newest_nested_write(tmp_path):
    """Returns the newest mtime across a nested tree, and that value
    advances when a deeper file is (re-)written."""
    stage = tmp_path / "005_execute"
    (stage / "code").mkdir(parents=True)
    old = stage / "code" / "a.py"
    old.write_text("print(1)\n")
    os.utime(old, (1_000_000.0, 1_000_000.0))  # pin to an old mtime
    first = claude_runner._newest_file_mtime(stage)
    assert first == 1_000_000.0
    # A newer nested write must move the signal forward.
    newer = stage / "code" / "b.py"
    newer.write_text("print(2)\n")
    os.utime(newer, (2_000_000.0, 2_000_000.0))
    assert claude_runner._newest_file_mtime(stage) == 2_000_000.0


def test_newest_file_mtime_ignores_timer_driven_observability_files(tmp_path):
    """beacon-masks-watchdog (audit 2026-06-17): the file-write liveness walk
    must NOT count timer-driven observability/liveness files as REAL stage-dir
    progress. A freshly-touched events.jsonl (the inactivity watchdog's own
    EXECUTE_PROGRESS beacon re-touches it) or a freshly-touched heartbeat must
    not advance the file-write signal — only a genuine artifact write does.

    events.jsonl normally lives at the workspace root (a SIBLING of the stage
    dir), but we place the excluded names directly under the walked tree here
    to pin the exclusion itself (defense-in-depth against any future layout
    change that puts them under stage_dir)."""
    stage = tmp_path / "005_execute"
    stage.mkdir(parents=True)
    real = stage / "results.json"
    real.write_text("{}\n")
    os.utime(real, (1_000_000.0, 1_000_000.0))  # the only REAL artifact
    # Drop the excluded basenames in MUCH newer than the real artifact.
    for name in (
        claude_runner.EVENTS_LOG,
        claude_runner.HEARTBEAT_FILENAME,
        claude_runner.HEARTBEAT_FALLBACK_FILENAME,
    ):
        p = stage / name
        p.write_text("x\n")
        os.utime(p, (9_000_000.0, 9_000_000.0))  # far newer
    # The signal must reflect the real artifact (1e6), NOT the fresher beacon/
    # heartbeat files (9e6) — otherwise a beacon masks a frozen stage.
    assert claude_runner._newest_file_mtime(stage) == 1_000_000.0


def test_watchdog_kills_beacon_only_frozen_turn(tmp_path):
    """beacon-masks-watchdog (audit 2026-06-17), the headline regression: a
    stage whose inner agent is FROZEN — it appends NOTHING to its log, never
    touches the heartbeat, writes NO file under the stage dir — but whose
    periodic EXECUTE_PROGRESS beacon keeps re-touching <ws>/events.jsonl, MUST
    still be killed by the inactivity watchdog. Before the fix the beacon's
    events.jsonl touch was mistaken for progress and the watchdog never fired
    (a d1 stage sat 44 min frozen).

    We simulate the frozen-inner-agent + live-beacon shape directly: the
    subprocess only sleeps (no log/heartbeat/stage-dir writes), and we stub the
    beacon emitter to keep appending to <ws>/events.jsonl every poll. The
    inactivity window is short, so a correct watchdog kills the turn despite
    the fresh events.jsonl."""
    ws = tmp_path / "ws"
    stage = ws / "047_review"
    stage.mkdir(parents=True)
    log = ws / "out.log"
    events = ws / "events.jsonl"

    import omicairn.claude_runner as cr

    # Replace the real beacon with one that ALWAYS writes a fresh events.jsonl
    # line — the exact masking behaviour. It lives at the ws root (the real
    # layout), a sibling of the stage dir.
    def _beacon_touch(*_a, **_k):
        with open(events, "a") as f:
            f.write('{"kind":"execute_progress","ts":"now"}\n')

    orig_every = cr._BEACON_EVERY_N_POLLS
    orig_emit = cr._emit_progress_beacon
    cr._BEACON_EVERY_N_POLLS = 1  # beacon every poll → events.jsonl stays fresh
    cr._emit_progress_beacon = _beacon_touch
    try:
        # Frozen inner agent: just sleeps; writes no log, heartbeat, or stage
        # file. poll_s=0.2, inactivity=1.5s → must be killed at ~1.5s even
        # though the beacon keeps events.jsonl fresh every 0.2s.
        with open(log, "w") as lf:
            rc, to = claude_runner._run_with_watchdogs(
                ["/bin/sh", "-c", "sleep 30"],
                cwd=str(ws), env=dict(os.environ), log_file=lf, log_path=log,
                wall_timeout_s=60, inactivity_timeout_s=1.5, poll_s=0.2,
                stage_dir=stage)
    finally:
        cr._BEACON_EVERY_N_POLLS = orig_every
        cr._emit_progress_beacon = orig_emit
    assert to is True and rc == -1, (
        "a frozen turn must be killed even though its beacon kept "
        "events.jsonl fresh (beacon-masks-watchdog regression)"
    )
    # The beacon really WAS firing (proving the masking shape was present).
    assert events.exists() and events.read_text().strip(), (
        "the stubbed beacon should have written events.jsonl"
    )


def test_watchdog_does_not_kill_slow_but_writing_turn(tmp_path):
    """False-KILL guard (sibling of e060078/450306c/ed8af4e): a legitimately
    SLOW stage that is making REAL progress — writing files under the stage
    dir, just infrequently — must NOT be killed even past the bare-beacon
    interval. This is the boundary the beacon-masks-watchdog fix must NOT
    regress: real stage-dir writes still reset the inactivity clock.

    The subprocess writes a file every ~0.8s (slower than the 1.0s inactivity
    window would tolerate between unrelated polls, but each write resets the
    clock), for several seconds. A correct watchdog keeps it alive."""
    ws = tmp_path / "ws"
    stage = ws / "002_execute"
    stage.mkdir(parents=True)
    log = ws / "out.log"
    # Write a fresh stage-dir file every ~0.8s for ~4s, then exit cleanly.
    script = (
        f'i=0; while [ "$i" -lt 5 ]; do printf x > "{stage}/r$i.json"; '
        'sleep 0.8; i=$((i+1)); done'
    )
    with open(log, "w") as lf:
        rc, to = claude_runner._run_with_watchdogs(
            ["/bin/sh", "-c", script],
            cwd=str(ws), env=dict(os.environ), log_file=lf, log_path=log,
            wall_timeout_s=30, inactivity_timeout_s=1.0, poll_s=0.2,
            stage_dir=stage)
    assert to is False and rc == 0, (
        "a slow-but-writing turn must NOT be false-killed — real stage-dir "
        "writes reset the inactivity clock"
    )


def test_watchdog_not_killed_when_files_written_though_log_and_hb_stale(tmp_path):
    """The whole point of the 2026-06-11 fix: a pure-local EXECUTE turn that
    writes NOTHING to stdout and never touches the heartbeat, but DOES keep
    writing files into the stage dir, must NOT be killed. Here the subprocess
    creates a new file under stage_dir every 0.2s for ~2s — well past the 1s
    window — while the log stays empty and no heartbeat is given. A
    log+heartbeat-only watchdog would have killed it; the file-write signal
    keeps it alive. This is the exact d1-SAR-Gate scenario."""
    log = tmp_path / "out.log"
    stage = tmp_path / "005_execute" / "code"
    stage.mkdir(parents=True)
    # Silent on stdout (log never grows), no heartbeat path at all; only
    # new files appear under the stage dir.
    script = (
        f'i=0; while [ "$i" -lt 10 ]; do printf x > "{stage}/f$i.py"; '
        'sleep 0.2; i=$((i+1)); done'
    )
    with open(log, "w") as lf:
        rc, to = claude_runner._run_with_watchdogs(
            ["/bin/sh", "-c", script],
            cwd=str(tmp_path), env=dict(os.environ), log_file=lf, log_path=log,
            wall_timeout_s=30, inactivity_timeout_s=1, poll_s=0.3,
            heartbeat_path=None, stage_dir=tmp_path / "005_execute")
    assert to is False, "fresh stage-dir file writes must keep a silent turn alive"
    assert rc == 0
    assert "WATCHDOG" not in log.read_text()


def test_watchdog_kills_when_log_hb_and_files_all_stale(tmp_path):
    """The negative half of the file-write signal: when the turn emits no
    log, touches no heartbeat, AND writes no files under a (pre-existing,
    static) stage dir, the watchdog still kills it. The stage dir has one
    file written BEFORE the run starts; its mtime never changes, so the
    file-write signal stays constant and does not mask the hang."""
    log = tmp_path / "out.log"
    stage = tmp_path / "005_execute"
    stage.mkdir(parents=True)
    (stage / "preexisting.py").write_text("# written before the run\n")
    with open(log, "w") as lf:
        rc, to = claude_runner._run_with_watchdogs(
            ["/bin/sh", "-c", "sleep 60"],  # silent, no hb, no new writes
            cwd=str(tmp_path), env=dict(os.environ), log_file=lf, log_path=log,
            wall_timeout_s=120, inactivity_timeout_s=1, poll_s=0.5,
            heartbeat_path=None, stage_dir=stage)
    assert to is True
    assert "WATCHDOG" in log.read_text()


# ---------------------------------------------------------------------------
# Bounded-teardown watchdog override (slot-hang fix, audit 2026-06-13). A
# best-effort agent that finishes its work but HANGS in teardown (un-reaped
# MCP-server grandchildren keep the `claude -p` parent from exiting) must
# not pin a concurrency slot for the full wall budget. A caller can FORCE
# the Popen + process-group teardown
# watchdog via `inactivity_timeout_override`, even when the global
# OMICAIRN_INACTIVITY_TIMEOUT_S is unset.
# ---------------------------------------------------------------------------
def test_inactivity_override_takes_precedence_over_env(monkeypatch):
    """A non-None override beats the env var (set OR unset); None falls
    back to the env-driven default so normal stages are unaffected."""
    monkeypatch.delenv(claude_runner.ENV_INACTIVITY_TIMEOUT, raising=False)
    # env unset → override wins.
    assert claude_runner._inactivity_timeout_s(override=600.0) == 600.0
    # env set → override STILL wins (the tail opts in regardless).
    monkeypatch.setenv(claude_runner.ENV_INACTIVITY_TIMEOUT, "30")
    assert claude_runner._inactivity_timeout_s(override=600.0) == 600.0
    # None override → env-driven (normal-stage behaviour preserved).
    assert claude_runner._inactivity_timeout_s(override=None) == 30.0
    # Negative override is clamped to 0 (watchdog disabled), not crashy.
    assert claude_runner._inactivity_timeout_s(override=-5.0) == 0.0


# ---------------------------------------------------------------------------
# Inactivity-timeout FLOOR (GPU/multimodal EXECUTE; d3 wm-dynamics-decouple
# false-kill incident). The orchestrator passes GPU_INACTIVITY_FLOOR_SECONDS
# as `floor_s`; _inactivity_timeout_s raises an already-active watchdog to
# max(configured, floor) — never lowering, never enabling a disabled or
# explicitly-overridden watchdog.
# ---------------------------------------------------------------------------
def test_inactivity_floor_raises_active_watchdog_below_floor(monkeypatch):
    """Configured 2700 + floor 5400 → raised to 5400 (the d3 case)."""
    monkeypatch.setenv(claude_runner.ENV_INACTIVITY_TIMEOUT, "2700")
    assert claude_runner._inactivity_timeout_s(floor_s=5400.0) == 5400.0


def test_inactivity_floor_does_not_lower_explicit_higher_value(monkeypatch):
    """A configured value ABOVE the floor is kept (max, never lowered)."""
    monkeypatch.setenv(claude_runner.ENV_INACTIVITY_TIMEOUT, "9000")
    assert claude_runner._inactivity_timeout_s(floor_s=5400.0) == 9000.0


def test_inactivity_floor_does_not_enable_disabled_watchdog(monkeypatch):
    """A disabled (0) watchdog stays disabled — the floor only RAISES an
    already-active watchdog, it never silently turns one on."""
    monkeypatch.setenv(claude_runner.ENV_INACTIVITY_TIMEOUT, "0")
    assert claude_runner._inactivity_timeout_s(floor_s=5400.0) == 0.0
    monkeypatch.delenv(claude_runner.ENV_INACTIVITY_TIMEOUT, raising=False)
    # Default is 0.0 (disabled) → floor must not enable it.
    assert claude_runner._inactivity_timeout_s(floor_s=5400.0) == \
        claude_runner._DEFAULT_INACTIVITY_TIMEOUT_S


def test_inactivity_floor_none_is_no_op(monkeypatch):
    """floor_s=None (the non-GPU / CPU/theory path) leaves the resolved value
    exactly as the env/default would give — CPU/theory runs are untouched."""
    monkeypatch.setenv(claude_runner.ENV_INACTIVITY_TIMEOUT, "2700")
    assert claude_runner._inactivity_timeout_s(floor_s=None) == 2700.0


def test_inactivity_override_beats_floor(monkeypatch):
    """An explicit override is authoritative and is NOT floored (a tail
    invocation that opted into a precise value keeps it)."""
    monkeypatch.setenv(claude_runner.ENV_INACTIVITY_TIMEOUT, "2700")
    assert claude_runner._inactivity_timeout_s(
        override=600.0, floor_s=5400.0
    ) == 600.0


def test_watchdog_reaps_whole_process_group_not_just_direct_child(tmp_path):
    """Root-cause regression: the hung agent's child outlives the parent.

    Simulate the d7 teardown hang — the `claude -p` parent's MCP-server
    grandchild stays alive and blocks exit. The shell here backgrounds a
    long ``sleep`` (the grandchild) in the SAME process group, then the
    foreground parent goes idle. The Popen watchdog runs the tree under
    ``start_new_session`` and ``_kill_tree`` SIGTERMs the whole GROUP, so
    BOTH the parent and the backgrounded grandchild die. A teardown that
    only killed the direct child would leak the grandchild."""
    log = tmp_path / "out.log"
    # Background child writes its PID, then sleeps far past the test.
    pidfile = tmp_path / "child.pid"
    script = (
        f"sleep 120 & echo $! > {pidfile}; "
        "sleep 120"  # parent goes silent → watchdog fires
    )
    with open(log, "w") as lf:
        rc, to = claude_runner._run_with_watchdogs(
            ["/bin/sh", "-c", script],
            cwd=str(tmp_path), env=dict(os.environ), log_file=lf, log_path=log,
            wall_timeout_s=120, inactivity_timeout_s=1, poll_s=0.3,
            heartbeat_path=None, stage_dir=None,
        )
    assert to is True
    assert "WATCHDOG" in log.read_text()
    # Give the SIGTERM a moment to propagate through the group.
    time.sleep(0.5)
    child_pid = int(pidfile.read_text().strip())
    # The backgrounded grandchild must be gone (killpg reaped the group).
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)  # signal 0 = liveness probe


def test_run_with_override_kills_hung_tree_when_env_unset(monkeypatch, tmp_path):
    """End-to-end through run(): with OMICAIRN_INACTIVITY_TIMEOUT_S UNSET, a
    positive ``inactivity_timeout_override`` must route to the bounded
    Popen watchdog (NOT the bare subprocess.run OFF path) and kill a
    never-exiting agent — returning timed_out instead of blocking for the
    full wall budget. We swap the built `claude ...` cmd for a silent
    `sleep` so no real CLI is needed."""
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)
    monkeypatch.delenv(claude_runner.ENV_INACTIVITY_TIMEOUT, raising=False)

    real_watchdog = claude_runner._run_with_watchdogs

    def patched_watchdog(cmd, **kwargs):
        # Replace the (unrunnable) claude cmd with a silent long sleep so
        # the watchdog has something real to kill; everything else (the
        # resolved inactivity_timeout_s, wall_timeout, Popen path) is the
        # production code under test.
        return real_watchdog(["/bin/sh", "-c", "sleep 120"], **kwargs)

    monkeypatch.setattr(claude_runner, "_run_with_watchdogs", patched_watchdog)
    # If the override were ignored, _inactivity_timeout_s() would resolve
    # to 0 (env unset) and the OFF path would block on subprocess.run for
    # the full wall_timeout. With the override, the watchdog fires at ~1s.
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()
    result = claude_runner.run(
        prompt="x",
        workspace_root=workspace_root,
        data_dir=workspace_root,
        allowed_tools=(),
        model="m",
        timeout_seconds=120,
        log_path=tmp_path / "log.txt",
        inactivity_timeout_override=1.0,
    )
    assert result.timed_out is True
    assert result.returncode == -1
    # Sanity: it returned in well under the 120s wall budget. The runner's
    # default poll interval is 15 s, so a 1 s inactivity window is detected
    # within ~2 polls (~30 s) — bounded to a small multiple of the poll
    # interval, NOT the full wall budget (which is the bug being fixed).
    assert result.wall_seconds < 60


# ---------------------------------------------------------------------------
# EXECUTE_PROGRESS beacon (audit 2026-06-14). A long EXECUTE turn mid-LLM-call
# (0 CPU, idle socket, no file write) is externally INDISTINGUISHABLE from a
# hang by process inspection. The watchdog already computes the liveness that
# tells them apart (log/heartbeat/file growth) but keeps it private; the
# beacon surfaces it to events.jsonl on a ~N*poll_s cadence so an external
# auditor can read "alive, last output 40s ago" vs. "hung". Best-effort
# only: it never changes when the watchdog kills, and an append_event failure
# must never propagate.
# ---------------------------------------------------------------------------
def test_should_beacon_cadence():
    """Beacons land on every N-th poll, not every poll (no spam)."""
    # Default cadence: only multiples of _BEACON_EVERY_N_POLLS.
    n = claude_runner._BEACON_EVERY_N_POLLS
    assert claude_runner._should_beacon(0) is False  # no poll completed yet
    assert claude_runner._should_beacon(n) is True
    assert claude_runner._should_beacon(n - 1) is False
    assert claude_runner._should_beacon(2 * n) is True
    # Explicit every_n: beacon on multiples of 3.
    assert [claude_runner._should_beacon(i, every_n=3) for i in range(1, 7)] == [
        False, False, True, False, False, True,
    ]
    # every_n <= 0 disables beaconing entirely (defensive).
    assert claude_runner._should_beacon(5, every_n=0) is False
    assert claude_runner._should_beacon(5, every_n=-1) is False


def test_which_signal_advanced_names_the_live_signal():
    """The beacon records WHICH liveness signal advanced — the field that
    tells an auditor why a turn is considered alive (streaming output vs.
    GPU-poll heartbeat vs. local file writes). 'none' when none advanced."""
    assert claude_runner._which_signal_advanced(True, False, False) == "log"
    assert claude_runner._which_signal_advanced(False, True, False) == "heartbeat"
    assert claude_runner._which_signal_advanced(False, False, True) == "file"
    assert claude_runner._which_signal_advanced(True, True, True) == "log,heartbeat,file"
    assert claude_runner._which_signal_advanced(False, False, False) == "none"


def test_emit_progress_beacon_writes_execute_progress_event(tmp_path):
    """The beacon emits a well-formed EXECUTE_PROGRESS event into the
    workspace's events.jsonl carrying exactly the slow-but-alive-vs-hung
    fields: stage name, elapsed, since-last-growth, and the live signal."""
    ws = tmp_path / "ws"
    stage = ws / "002_execute"
    stage.mkdir(parents=True)
    claude_runner._emit_progress_beacon(
        ws,
        stage_dir=stage,
        elapsed_s=123.45,
        since_growth_s=41.2,
        last_signal="file",
        inactivity_timeout_s=2700.0,
    )
    events = (ws / "events.jsonl").read_text().strip().splitlines()
    assert len(events) == 1
    rec = json.loads(events[0])
    assert rec["kind"] == claude_runner.EventKind.EXECUTE_PROGRESS
    assert rec["kind"] == "execute_progress"  # canonical value pinned
    assert rec["stage"] == "002_execute"
    assert rec["elapsed_s"] == 123.5
    assert rec["since_last_growth_s"] == 41.2
    assert rec["last_signal"] == "file"
    assert rec["inactivity_timeout_s"] == 2700.0
    assert "ts" in rec  # append_event auto-fills it


def test_emit_progress_beacon_no_workspace_root_is_noop(tmp_path):
    """No workspace_root (e.g. the steering-planner one-off, no stage dir)
    → the beacon is silently skipped, no file written."""
    claude_runner._emit_progress_beacon(
        None, stage_dir=None, elapsed_s=1.0, since_growth_s=1.0,
        last_signal="none", inactivity_timeout_s=10.0,
    )
    assert not (tmp_path / "events.jsonl").exists()


def test_emit_progress_beacon_swallows_append_event_failure(tmp_path, monkeypatch):
    """A beacon is best-effort observability: an append_event failure (disk
    full, a kindless-event-guard regression, ...) must NOT propagate — it
    would otherwise crash/kill an entirely-healthy run. The emit swallows
    everything."""
    def boom(*_a, **_k):
        raise OSError("simulated disk-full on events.jsonl")

    monkeypatch.setattr(claude_runner.stage_mod, "append_event", boom)
    # Must NOT raise.
    claude_runner._emit_progress_beacon(
        tmp_path, stage_dir=tmp_path / "002_execute", elapsed_s=1.0,
        since_growth_s=1.0, last_signal="log", inactivity_timeout_s=10.0,
    )


# ---------------------------------------------------------------------------
# Worker-CPU liveness probe (audit 2026-06-17, omicairn-inner-agent-spawn-freeze).
# OmiCairn spawns the agent as a 3-level tree (shim → launcher → Node worker);
# the shim PID we Popen sits at 0% CPU the WHOLE turn while the grandchild
# burns CPU. An auditor probing the shim PID alone sees 0% CPU + 0 jiffies +
# (output buffered) 0 log growth → mistakes a healthy long turn for a frozen
# one (the false-"freeze" that got healthy mid-turn agents repeatedly killed).
# The probe sums jiffies over the WHOLE descendant tree so the beacon can
# prove the worker is alive. These tests pin: (a) the tree probe sees a
# grandchild's CPU the shim PID misses, (b) graceful None on a dead/missing
# PID, (c) the beacon carries the CPU fields, (d) CPU is OBSERVABILITY ONLY —
# it never enters the kill predicate (a CPU-quiet-but-otherwise-live turn
# must not be killed by a missing CPU signal).
# ---------------------------------------------------------------------------
def test_tree_cpu_jiffies_sums_descendant_cpu_shim_misses(tmp_path):
    """The headline probe: a child process that burns CPU is counted by the
    WHOLE-TREE probe on its PARENT even though the parent itself is idle —
    the exact shim(0%)→worker(busy) shape OmiCairn spawns. We model it with a
    real parent whose child spins, then assert the tree sum on the PARENT
    advances across two reads while the parent's OWN jiffies stay ~flat."""
    import subprocess
    import sys
    import time

    # Parent sleeps (idle, ~0 CPU); child spins (burns CPU). Probing the
    # PARENT pid must see the child's jiffies via the descendant walk.
    parent = subprocess.Popen(
        [sys.executable, "-c",
         "import subprocess,sys,time;"
         "c=subprocess.Popen([sys.executable,'-c','"
         "import time\\nt=time.time()\\nwhile time.time()-t<3.0: pass'"
         "]);"
         "time.sleep(4); c.wait()"],
    )
    try:
        # Let the grandchild start spinning.
        time.sleep(0.5)
        own_a = claude_runner._proc_cpu_jiffies(parent.pid)
        tree_a = claude_runner._tree_cpu_jiffies(parent.pid)
        time.sleep(1.5)
        own_b = claude_runner._proc_cpu_jiffies(parent.pid)
        tree_b = claude_runner._tree_cpu_jiffies(parent.pid)
    finally:
        parent.wait()

    assert tree_a is not None and tree_b is not None
    # The descendant tree's CPU advanced (the spinning child) ...
    assert tree_b > tree_a, "tree-CPU probe must see the busy descendant"
    # ... while the idle parent's OWN jiffies barely moved — i.e. probing the
    # spawned PID alone would have missed the worker (the false-freeze).
    assert own_a is not None and own_b is not None
    assert (own_b - own_a) < (tree_b - tree_a), (
        "the parent's own CPU must lag the tree total — proving the busy "
        "worker is a descendant the shim-only probe misses"
    )


def test_tree_cpu_jiffies_graceful_on_dead_or_none_pid():
    """A None root, or a PID that does not exist, yields None — the CPU
    signal is simply absent (the other three liveness signals still apply).
    Never raises."""
    assert claude_runner._tree_cpu_jiffies(None) is None
    # A PID extremely unlikely to exist.
    assert claude_runner._tree_cpu_jiffies(2_147_480_000) is None


def test_proc_cpu_jiffies_handles_comm_with_spaces(tmp_path, monkeypatch):
    """The /proc/<pid>/stat parser splits after the LAST ')' so a process
    whose comm contains spaces/parens (e.g. '(claude -p)') does not corrupt
    the utime/stime field offsets."""
    fake_stat = b"42 (claude -p (worker)) S 1 42 42 0 -1 0 0 0 0 0 100 250 0 0 20 0 8 0\n"
    real_open = open

    def fake_open(path, *a, **k):
        if str(path) == "/proc/42/stat":
            import io
            return io.BytesIO(fake_stat)
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", fake_open)
    # utime=100, stime=250 → 350 (correctly located past the messy comm).
    assert claude_runner._proc_cpu_jiffies(42) == 350


def test_emit_progress_beacon_carries_worker_cpu_fields(tmp_path):
    """When the CPU probe succeeds, the beacon surfaces worker_cpu_jiffies +
    worker_cpu_advanced so an auditor sees the worker is alive even when
    log/heartbeat/file are quiet."""
    ws = tmp_path / "ws"
    stage = ws / "002_execute"
    stage.mkdir(parents=True)
    claude_runner._emit_progress_beacon(
        ws, stage_dir=stage, elapsed_s=10.0, since_growth_s=8.0,
        last_signal="none", inactivity_timeout_s=2700.0,
        worker_cpu_jiffies=12345, worker_cpu_advanced=True,
    )
    rec = json.loads((ws / "events.jsonl").read_text().strip())
    assert rec["worker_cpu_jiffies"] == 12345
    assert rec["worker_cpu_advanced"] is True


def test_emit_progress_beacon_omits_cpu_fields_when_probe_unavailable(tmp_path):
    """On a platform with no /proc the probe returns None; the beacon then
    OMITS the CPU fields entirely (stays a valid EXECUTE_PROGRESS event) —
    backward-compatible with consumers that don't know the new fields."""
    ws = tmp_path / "ws"
    (ws / "002_execute").mkdir(parents=True)
    claude_runner._emit_progress_beacon(
        ws, stage_dir=ws / "002_execute", elapsed_s=1.0, since_growth_s=1.0,
        last_signal="none", inactivity_timeout_s=10.0,
        worker_cpu_jiffies=None, worker_cpu_advanced=None,
    )
    rec = json.loads((ws / "events.jsonl").read_text().strip())
    assert "worker_cpu_jiffies" not in rec
    assert "worker_cpu_advanced" not in rec
    assert rec["kind"] == claude_runner.EventKind.EXECUTE_PROGRESS


def test_cpu_quiet_turn_with_file_writes_is_not_killed(tmp_path):
    """OBSERVABILITY-ONLY guarantee: CPU is NOT in the kill predicate. A turn
    that is CPU-quiet (e.g. blocked on a remote GPU job → ~0% local CPU) but
    keeps writing files into the stage dir must NOT be killed — the existing
    file-write signal keeps it alive. This pins that adding the CPU beacon
    field did not regress the (unchanged) kill logic."""
    import threading
    import time

    ws = tmp_path / "ws"
    stage = ws / "002_execute"
    stage.mkdir(parents=True)
    log_path = stage / "stage.log"
    cr = claude_runner

    # A child that sleeps quietly (no CPU, no stdout). A background thread
    # writes a fresh file into the stage dir each poll → file-write liveness
    # keeps it alive despite zero CPU/log growth.
    stop = threading.Event()

    def _writer():
        i = 0
        while not stop.is_set():
            (stage / f"shard_{i}.json").write_text(str(i))
            i += 1
            time.sleep(0.15)

    t = threading.Thread(target=_writer, daemon=True)
    t.start()
    try:
        with log_path.open("w") as lf:
            rc, timed_out = cr._run_with_watchdogs(
                ["sleep", "2"],
                cwd=str(ws), env=dict(os.environ), log_file=lf,
                log_path=log_path,
                wall_timeout_s=30,
                inactivity_timeout_s=0.5,   # tiny → would kill a TRULY idle turn
                poll_s=0.2,
                stage_dir=stage,
            )
    finally:
        stop.set()
        t.join(timeout=2)

    assert rc == 0 and timed_out is False, (
        "a CPU-quiet turn that keeps WRITING FILES must not be killed — "
        "CPU is observability-only, file-write liveness governs the kill"
    )


def test_watchdog_emits_periodic_beacon_for_live_turn(tmp_path):
    """End-to-end: drive the REAL watchdog with a stage_dir under a workspace
    root and a small cadence-driving poll interval. A turn that stays alive
    (keeps writing files under the stage dir) for several polls must land
    EXECUTE_PROGRESS beacons in <ws>/events.jsonl on the N-th-poll cadence,
    WITHOUT being killed. stage_dir.parent is the workspace root (that is the
    real layout — stage dirs sit directly under the workspace)."""
    ws = tmp_path / "ws"
    stage = ws / "002_execute"
    stage.mkdir(parents=True)
    log = ws / "out.log"
    # Force a beacon every 2 polls so a short run produces several.
    import omicairn.claude_runner as cr
    orig_every = cr._BEACON_EVERY_N_POLLS
    cr._BEACON_EVERY_N_POLLS = 2
    try:
        # Alive: writes a new file under the stage dir every 0.1s for ~2.5s,
        # then exits 0. Poll every 0.2s → ~12 polls → ~6 beacons.
        script = (
            f'i=0; while [ "$i" -lt 25 ]; do printf x > "{stage}/f$i.dat"; '
            'sleep 0.1; i=$((i+1)); done'
        )
        with open(log, "w") as lf:
            rc, to = claude_runner._run_with_watchdogs(
                ["/bin/sh", "-c", script],
                cwd=str(ws), env=dict(os.environ), log_file=lf, log_path=log,
                wall_timeout_s=30, inactivity_timeout_s=5, poll_s=0.2,
                stage_dir=stage)
    finally:
        cr._BEACON_EVERY_N_POLLS = orig_every
    assert to is False and rc == 0, "a file-writing turn must not be killed"
    events_path = ws / "events.jsonl"
    assert events_path.exists(), "live turn should have emitted beacons"
    beacons = [
        json.loads(line)
        for line in events_path.read_text().strip().splitlines()
        if json.loads(line).get("kind") == claude_runner.EventKind.EXECUTE_PROGRESS
    ]
    assert len(beacons) >= 2, f"expected periodic beacons, got {len(beacons)}"
    last = beacons[-1]
    assert last["stage"] == "002_execute"
    # The turn was writing files, so the live signal must reflect 'file'.
    assert "file" in last["last_signal"]
    # Elapsed climbs monotonically across beacons.
    elapsed = [b["elapsed_s"] for b in beacons]
    assert elapsed == sorted(elapsed)


def test_watchdog_beacon_failure_does_not_kill_run(tmp_path, monkeypatch):
    """If append_event blows up mid-run, the watchdog must keep running and
    return the turn's real outcome (success) — a broken beacon never kills a
    healthy run. This pins the best-effort contract end-to-end."""
    ws = tmp_path / "ws"
    stage = ws / "002_execute"
    stage.mkdir(parents=True)
    log = ws / "out.log"

    def boom(*_a, **_k):
        raise OSError("simulated events.jsonl failure")

    monkeypatch.setattr(claude_runner.stage_mod, "append_event", boom)
    import omicairn.claude_runner as cr
    orig_every = cr._BEACON_EVERY_N_POLLS
    cr._BEACON_EVERY_N_POLLS = 1  # beacon every poll → fail immediately
    try:
        script = (
            f'i=0; while [ "$i" -lt 12 ]; do printf x > "{stage}/f$i.dat"; '
            'sleep 0.1; i=$((i+1)); done'
        )
        with open(log, "w") as lf:
            rc, to = claude_runner._run_with_watchdogs(
                ["/bin/sh", "-c", script],
                cwd=str(ws), env=dict(os.environ), log_file=lf, log_path=log,
                wall_timeout_s=30, inactivity_timeout_s=5, poll_s=0.2,
                stage_dir=stage)
    finally:
        cr._BEACON_EVERY_N_POLLS = orig_every
    assert rc == 0 and to is False, "a failing beacon must not kill the run"


def test_beacon_last_signal_reflects_none_when_quiet(tmp_path):
    """FIX 2 (d3 false-kill audit): when a turn goes quiet (no log/heartbeat/
    file growth this poll), the beacon's `last_signal` must report the ACTUAL
    current state ("none") — NOT a stale-reassuring name (e.g. "file") from a
    much earlier poll while since_last_growth_s climbs. The process writes one
    file early (so an early poll sees "file"), then goes silent long enough
    for several quiet beacons but NOT long enough to be killed (inactivity
    window > the silent span). The LAST beacon must show last_signal "none"
    and a since_last_growth_s that climbed above 0."""
    ws = tmp_path / "ws"
    stage = ws / "002_execute"
    stage.mkdir(parents=True)
    log = ws / "out.log"
    import omicairn.claude_runner as cr
    orig_every = cr._BEACON_EVERY_N_POLLS
    cr._BEACON_EVERY_N_POLLS = 1  # beacon every poll → capture the quiet ones
    try:
        # Write one file in the first ~0.1s, then sit silent for ~2s. With
        # poll_s=0.2 and inactivity=10s the turn is NOT killed; later polls
        # see no growth → last_signal must flip to "none".
        script = f'printf x > "{stage}/early.dat"; sleep 2'
        with open(log, "w") as lf:
            rc, to = claude_runner._run_with_watchdogs(
                ["/bin/sh", "-c", script],
                cwd=str(ws), env=dict(os.environ), log_file=lf, log_path=log,
                wall_timeout_s=30, inactivity_timeout_s=10, poll_s=0.2,
                stage_dir=stage)
    finally:
        cr._BEACON_EVERY_N_POLLS = orig_every
    assert to is False, "inactivity window > silent span → must NOT be killed"
    events_path = ws / "events.jsonl"
    assert events_path.exists()
    beacons = [
        json.loads(line)
        for line in events_path.read_text().strip().splitlines()
        if json.loads(line).get("kind") == claude_runner.EventKind.EXECUTE_PROGRESS
    ]
    assert beacons, "expected beacons from the quiet turn"
    last = beacons[-1]
    # The turn was silent by the end → last_signal honestly reports "none",
    # not the stale "file" from the first poll.
    assert last["last_signal"] == "none", (
        f"quiet beacon must report 'none', got {last['last_signal']!r}"
    )
    # And the staleness is honestly climbing (not reset to ~0).
    assert last["since_last_growth_s"] > 0
