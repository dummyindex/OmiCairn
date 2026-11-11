"""omicairn command-line interface.

Subcommands:
  init        create a workspace from a RESEARCH.md
  run         drive the pipeline (resumes automatically)
  continue    append a user note and resume a run in one step
  note        append a free-form instruction for the next phase
  steer       dispatch the steering planner with a natural-language request
  status      print STATE.md summary
  fleet       one-line-per-run status table across a workspaces root (read-only)
  literature  fetch/re-fetch literature
  paperlib    inspect / mutate the shared paper library
  inspect     list files in a stage directory
  reap        mark orphaned (dead in_progress) runs terminal (dry-run by default)
  preflight   run fast drift-lint suite over the dev repo
  ideate      brainstorm: survey sources + propose research directions (standalone)
  labkb       cross-run lab knowledge base: distill / list / search / stats
  labtheorems  lab theorem library: Lean-validated theorem store + retrieval + virtual mathematician
  paperreview  math paper review: review landmark math papers + machine-verify their theorems in Lean
  feedback    cross-run human-feedback store: list / stats / export
  project     describe/scan a folder into a portable project.json + graph
  audit       --replay-gates: replay the cross_check gate suite across runs
"""
from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys
import textwrap

from . import (
    constants,
    orchestrator,
    reap as reap_mod,
    stage as stage_mod,
    state,
    steering,
    user_notes,
    workspace,
)
from .constants import (
    CLI_PHASE_CHOICES,
    ENV_DRY_RUN,
    ENV_DRY_VERDICT,
    ENV_MAX_CONCURRENT_RUNS,
    KG_GRAPH_FILE,
    KNOWLEDGE_DIR,
    PAPER_LIB_ABSTRACTS_DIR,
    PAPER_LIB_PDFS_DIR,
    PHASE_CHOICE_ALL,
    PhaseName,
    PIPELINE_ORDER,
    dry_run_enabled,
    max_concurrent_runs,
)


# Drift-lint + static-contract test modules run by `omicairn preflight`. These
# are the fast checks that finish in well under a second and catch the
# overwhelming majority of edit-time mistakes (stale prompts, renamed
# phase outputs, orphan agent files, misaligned CLI subcommand surfaces,
# pyproject entry-point drift, settings.json token-substitution bugs,
# phase registry corruption).
#
# Anything orchestrator-ish, network-touching, or filesystem-mutating is
# deliberately excluded — those go in the full `pytest` run, not in the
# quick preflight gate.
_PREFLIGHT_TEST_MODULES: tuple[str, ...] = (
    "tests/test_agents_lint.py",
    "tests/test_cli_lint.py",
    "tests/test_domain_pack_lint.py",
    "tests/test_prompt_lint.py",
    "tests/test_pyproject_lint.py",
    "tests/test_templates_lint.py",
    "tests/test_phases.py",
    "tests/test_constants.py",
    "tests/test_contracts.py",
)


def _default_workspaces_root() -> pathlib.Path:
    return pathlib.Path.cwd() / "workspaces"


def _enable_env_flag(name: str) -> None:
    os.environ[name] = "1"


def cmd_init(args: argparse.Namespace) -> int:
    ws_root = (
        pathlib.Path(args.workspaces_root)
        if args.workspaces_root
        else _default_workspaces_root()
    )
    ws_root.mkdir(parents=True, exist_ok=True)

    rmd_path = pathlib.Path(args.from_research_md).resolve()
    if not rmd_path.exists():
        print(f"ERROR: RESEARCH.md not found: {rmd_path}", file=sys.stderr)
        return 2

    ws = workspace.init(
        args.topic_slug,
        rmd_path,
        ws_root,
        skip_literature=args.skip_literature,
    )
    print(f"initialised workspace: {ws.root}")
    print(f"  topic_slug: {ws.research_md.topic_slug}")
    print(f"  data:       {ws.research_md.data_location}")
    print(f"  model:      {ws.research_md.claude_model}")
    print(f"  bounds:     max_iters_per_phase={ws.research_md.max_iters_per_phase}, "
          f"max_backtracks={ws.research_md.max_backtracks}")
    print(f"  iter_mode:  {constants.stage_iter_mode(ws.research_md)}")
    print(f"  next: omicairn run {ws.root}")
    return 0


def _enforce_concurrency_cap(root: pathlib.Path) -> int | None:
    """HARD launch-time concurrency cap (owner directive 2026-06-23).

    Counts currently-LIVE OmiCairn runs across ``root``'s workspaces tree — using
    the SAME liveness definition reap/fleet trust (live ``.run.lock`` holder OR
    live ``omicairn run`` process; NOT the stale ``status: in_progress`` in
    STATE.md) — EXCLUDING ``root`` itself so a resume of an already-live run is
    never self-blocked. If the live count is already >= the cap
    (:func:`max_concurrent_runs`, env-overridable via ``OMICAIRN_MAX_CONCURRENT_RUNS``),
    REFUSE to launch and return a non-zero exit code; otherwise return None
    (proceed).

    Best-effort, count-then-launch (NOT a mutex): a genuinely simultaneous
    double launch can momentarily exceed the cap by the number of concurrent
    launches. That residual race is acceptable for an API-bound fleet that the
    audit loop launches serially. Below the cap, behaviour is unchanged.
    """
    cap = max_concurrent_runs()
    # Count over the workspaces ROOT (the parent), excluding THIS workspace so
    # a resume of an already-counted live run isn't rejected by its own
    # liveness. A single-workspace target (no siblings) simply counts 0 others.
    live_count, degraded = reap_mod.count_live_runs(root.parent, exclude=root)
    if live_count >= cap:
        degraded_note = (
            " (NOTE: process enumeration degraded — count reflects live "
            ".run.lock holders only and may UNDER-count)" if degraded else ""
        )
        print(
            f"ERROR: OmiCairn concurrency cap {cap} reached; {live_count} live "
            f"run(s) already in flight{degraded_note}. Refusing to launch "
            f"{root.name}. Wait for a run to finish, or raise the cap via "
            f"{ENV_MAX_CONCURRENT_RUNS} (e.g. {ENV_MAX_CONCURRENT_RUNS}="
            f"{cap + 5}). See `omicairn fleet <workspaces-root>` for the live set.",
            file=sys.stderr,
        )
        return 3
    return None


def cmd_run(args: argparse.Namespace) -> int:
    root = pathlib.Path(args.workspace).resolve()
    if not (root / "RESEARCH.md").exists():
        print(
            f"ERROR: no RESEARCH.md in {root}; did you run `omicairn init` first?",
            file=sys.stderr,
        )
        return 2
    cap_rc = _enforce_concurrency_cap(root)
    if cap_rc is not None:
        return cap_rc
    ws = workspace.load_workspace(root)

    if args.dry_run:
        _enable_env_flag(ENV_DRY_RUN)
    if args.dry_verdict:
        os.environ[ENV_DRY_VERDICT] = args.dry_verdict

    if args.add_stage:
        if args.add_stage not in PIPELINE_ORDER:
            print(
                f"ERROR: --add-stage must be one of {PIPELINE_ORDER}",
                file=sys.stderr,
            )
            return 2
        outcome = orchestrator.run_next_stage(ws, forced_phase=args.add_stage)
        outcomes = [outcome] if outcome is not None else []
    elif args.phase == PHASE_CHOICE_ALL:
        outcomes = orchestrator.run_all(
            ws,
            start=None,
            stop_after=args.stop_after,
            max_stages=args.max_stages,
        )
    else:
        outcome = orchestrator.run_next_stage(ws, forced_phase=args.phase)
        outcomes = [outcome] if outcome is not None else []

    print("\n=== OmiCairn run summary ===")
    if not outcomes:
        print("  (no stages ran — pipeline may be complete or halted)")
    for o in outcomes:
        print(
            f"  {o.stage_dir_rel:<22}  {o.status:<14}  "
            f"attempts={o.attempts}  wall={o.wall_seconds:.0f}s  "
            f"decision={o.decision or '-':<16}  {o.note}"
        )
    failed = [o for o in outcomes if o.status not in ("DONE", "HALTED")]
    return 1 if failed else 0


def cmd_note(args: argparse.Namespace) -> int:
    """Append a free-form user note for the next phase to consume.

    Notes are stored in ``<workspace>/knowledge/user_notes.md`` and
    injected into the next phase prompt via the ``{{user_notes}}``
    token. They do NOT override contract checks — they are advisory
    to the LLM only. See ``docs/DESIGN.md`` for the full semantics.

    Scope:
      - default (``sticky``): note persists and is visible to every
        subsequent run of the matching phase until the user edits or
        removes it from ``user_notes.md``.
      - ``--once``: note moves to
        ``knowledge/user_notes_consumed.md`` after the next matching
        phase finishes successfully.
    """
    root = pathlib.Path(args.workspace).resolve()
    if not (root / "RESEARCH.md").exists():
        print(
            f"ERROR: no RESEARCH.md in {root}; did you run `omicairn init` first?",
            file=sys.stderr,
        )
        return 2
    body = args.message.strip()
    if not body:
        print("ERROR: empty note body; pass a non-empty message", file=sys.stderr)
        return 2
    try:
        n = user_notes.append_note(
            root,
            body=body,
            phase=args.phase,
            once=args.once,
        )
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    # ADDITIVE durability mirror: a user note is consumed in-prompt (and
    # `--once` notes are moved to the consumed log), so the steering signal
    # is otherwise ephemeral. Also record it as a TYPED, LINKED record in the
    # cross-run human-feedback store so it survives as annotation. Best-effort
    # — never breaks the primary user_notes.md write. The targeted phase rides
    # along as a `phase:<name>` tag; `stage_dir` auto-resolves to the stage the
    # user was looking at when they left the note.
    from . import human_feedback
    scope = "once" if args.once else "sticky"
    tags = [f"scope:{scope}"]
    if args.phase:
        tags.append(f"phase:{args.phase}")
    human_feedback.record_from_workspace(root, kind="note", text=body, tags=tags)
    print(
        f"note added: phase={n.phase} scope={scope} ts={n.timestamp}\n"
        f"  file: {user_notes.notes_path(root)}\n"
        "  WARNING: notes are not redacted; avoid pasting secrets."
    )
    return 0


def cmd_continue(args: argparse.Namespace) -> int:
    """Optionally add a user note and then invoke `omicairn run`.

    Thin wrapper: ``omicairn continue ws --note "..." --phase execute`` is
    equivalent to ``omicairn note ws "..." --phase execute`` followed by
    ``omicairn run ws --phase execute`` — one command for the common resume
    flow.
    """
    root = pathlib.Path(args.workspace).resolve()
    if not (root / "RESEARCH.md").exists():
        print(
            f"ERROR: no RESEARCH.md in {root}; did you run `omicairn init` first?",
            file=sys.stderr,
        )
        return 2
    if args.note:
        try:
            user_notes.append_note(
                root,
                body=args.note,
                phase=None if args.phase == PHASE_CHOICE_ALL else args.phase,
                once=args.once,
            )
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
    # Delegate to cmd_run with a compatible namespace.
    run_args = argparse.Namespace(
        workspace=str(root),
        phase=args.phase,
        add_stage=None,
        stop_after=args.stop_after,
        max_stages=args.max_stages,
        dry_run=args.dry_run,
        dry_verdict=args.dry_verdict,
    )
    return cmd_run(run_args)


def cmd_steer(args: argparse.Namespace) -> int:
    """Steer the workspace via natural-language input.

    Dispatches the steering planner (a Claude Code session running
    `prompts/steering_planner.md`) which decomposes the user request
    into a structured `steering_plan.json`. The orchestrator then
    consults that plan on the next `decide_next_phase` and runs each
    step as a normal phase session.

    Slice 1 of the steering-mode rollout (parser-audit-style typed
    contract; verbs are emergent, not enumerated).
    """
    root = pathlib.Path(args.workspace).resolve()
    if not (root / "RESEARCH.md").exists():
        print(
            f"ERROR: no RESEARCH.md in {root}; did you run `omicairn init` first?",
            file=sys.stderr,
        )
        return 2
    request = (args.request or "").strip()
    if not request:
        print("ERROR: empty steering request", file=sys.stderr)
        return 2
    # Dispatch the planner. We piggyback on the existing
    # _run_llm_stage path with a synthetic "steering_plan" phase.
    # Implementation detail: orchestrator.run_steering_planner takes
    # the request and produces knowledge/steering_plan.json.
    try:
        rc = orchestrator.run_steering_planner(root, request=request)
    except Exception as e:  # pragma: no cover — surface to user
        print(f"ERROR: steering planner failed: {e}", file=sys.stderr)
        return 1
    if rc != 0:
        print(
            f"steering planner exited rc={rc}; no plan written",
            file=sys.stderr,
        )
        return rc
    plan = steering.read_plan(root)
    if plan is None:
        print(
            f"ERROR: planner ran but no steering_plan.json found at "
            f"{steering.plan_path(root)}",
            file=sys.stderr,
        )
        return 1
    errs = steering.validate_plan(plan)
    if errs:
        print(
            "ERROR: planner produced an invalid plan:\n  "
            + "\n  ".join(errs),
            file=sys.stderr,
        )
        return 1
    # ADDITIVE durability mirror: the steering request is captured in
    # steering_log.jsonl + steering_plan.json, but only PER-WORKSPACE. Also
    # record it as a TYPED, LINKED record in the cross-run human-feedback
    # store so every human steer accrues as queryable annotation/supervision
    # signal across runs. Best-effort — never breaks the steer command.
    from . import human_feedback
    human_feedback.record_from_workspace(
        root, kind="steer", text=request,
        tags=[f"steps:{len(plan.steps)}"],
    )
    print(
        f"steering plan written: {steering.plan_path(root)}\n"
        f"  request:        {plan.user_request[:80]}...\n"
        f"  steps:          {len(plan.steps)}\n"
        f"  on_step_failure: {plan.on_step_failure}\n"
        "  next: run `omicairn run <ws>` to dispatch the plan, OR\n"
        "        edit knowledge/steering_plan.json by hand first."
    )
    return 0


def cmd_feedback(args: argparse.Namespace) -> int:
    """Inspect / export the cross-run HUMAN-FEEDBACK store — the typed,
    linked record of every human steer / note / correction / approval /
    rejection / rating, aggregated across runs (beside lab_knowledge/).

    Actions:
      omicairn feedback list  [--kind K] [--workspace W] [--tag T] [--limit N]
      omicairn feedback stats
      omicairn feedback export <out.csv> [--kind K] [--workspace W]
    Read-only (list/stats/export); records are written by `omicairn steer`,
    `omicairn note`, and the orchestrator — this surface is for review/mining.
    """
    from . import human_feedback as hf

    action = getattr(args, "fb_action", None) or "list"
    if action == "stats":
        store = hf.load()
        from collections import Counter
        by_kind = Counter(r.get("kind", "?") for r in store.values())
        by_ws = Counter(r.get("run_topic_slug") or r.get("workspace") or "?"
                        for r in store.values())
        print(f"human-feedback store: {len(store)} records")
        print("  by kind:      " + ", ".join(f"{k}={n}" for k, n in by_kind.most_common()))
        print("  by run/topic: " + ", ".join(f"{k}={n}" for k, n in by_ws.most_common(10)))
        return 0
    if action == "export":
        out = pathlib.Path(args.out).resolve()
        n = hf.export(out, kind=getattr(args, "kind", None),
                      workspace=getattr(args, "workspace", None))
        print(f"exported {n} record(s) -> {out}")
        return 0
    # default: list
    rows = hf.query(
        kind=getattr(args, "kind", None),
        workspace=getattr(args, "workspace", None),
        tag=getattr(args, "tag", None),
        limit=int(getattr(args, "limit", 50) or 50),
    )
    if not rows:
        print("human-feedback store: no matching records.")
        return 0
    for r in rows:
        loc = r.get("stage_dir") or "-"
        art = r.get("target_artifact")
        arts = f" @{art}" if art else ""
        tags = (" [" + ",".join(r.get("tags", [])) + "]") if r.get("tags") else ""
        print(f"  {r.get('recorded_at', '?')[:19]}  {r.get('kind', '?'):<10} "
              f"{(r.get('run_topic_slug') or r.get('workspace') or '?')}/{loc}{arts}{tags}")
        print(f"      {str(r.get('text', ''))[:140]}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    root = pathlib.Path(args.workspace).resolve()
    s = state.read(root / "STATE.md")
    # next_stage_index is computed from the filesystem rather than read
    # from the state dict so it always matches what the orchestrator
    # will actually use for the next stage (it enumerates stage dirs).
    next_idx = stage_mod.next_stage_index(root)
    print(f"topic_slug:     {s.get('topic_slug')}")
    print(f"status:         {s.get('status')}")
    print(f"current_stage:  {s.get('current_stage')}")
    print(f"next_stage_idx: {next_idx}")
    print(f"started:        {s.get('started_at')}")
    print(f"updated:        {s.get('updated_at')}")
    # OmiCairn system version/commit/dirty for the most recent invocation —
    # the reproducibility stamp (distinct from started/updated which
    # track the run's wall-clock lifecycle, not the code version).
    from . import provenance
    fv = s.get("omicairn_version") or provenance.read_latest(root)
    if fv:
        dirty = fv.get("git_dirty")
        dirty_str = (
            "dirty" if dirty is True else "clean" if dirty is False else "unknown"
        )
        print(
            f"omicairn_version:   commit {fv.get('git_commit_short')} "
            f"on {fv.get('git_branch')} ({dirty_str}); "
            f"pkg {fv.get('omicairn_version')}; recorded {fv.get('recorded_at')}"
        )
    if s.get("halted_reason"):
        print(f"halted_reason:  {s['halted_reason']}")
    if s.get("paused_reason"):
        print(f"paused_reason:  {s['paused_reason']}")
    print()
    print("iteration_counts:")
    for name in PIPELINE_ORDER:
        n = s.get("iteration_counts", {}).get(name, 0)
        print(f"  {name:<12}  {n}")
    bounds = s.get("bounds", {})
    print()
    print(f"bounds:")
    print(f"  max_iters_per_phase: {bounds.get('max_iters_per_phase')}")
    print(f"  max_backtracks:      {bounds.get('max_backtracks')}")
    print(f"  backtracks_used:     {s.get('backtracks_used', 0)}")
    print()
    print("stages on disk:")
    print(stage_mod.render_stage_listing(root))
    note_lines = user_notes.status_lines(root)
    if note_lines:
        print()
        print("user notes:")
        for ln in note_lines:
            print(ln)
    return 0


def cmd_fleet(args: argparse.Namespace) -> int:
    """Fleet status: one read-only line per run across a workspaces root.

    Automates the manual `ps` / `nvidia-smi` / newest-stage-dir sweep an
    operator does every cycle when several runs are in flight. Reuses the
    `omicairn reap` liveness scanners (process + .run.lock) but PRINTS instead of
    mutating. Defaults to the standard workspaces root; pass a path to scan a
    different root (or a single workspace).
    """
    from . import fleet as fleet_mod

    target = (
        pathlib.Path(args.path).resolve()
        if args.path
        else _default_workspaces_root()
    )
    if not target.exists():
        print(
            f"ERROR: no such path {target} (pass a workspaces root or a "
            f"workspace dir; default is ./workspaces)",
            file=sys.stderr,
        )
        return 2
    rows, degraded = fleet_mod.scan_fleet(target)
    print(fleet_mod.render_table(rows, degraded))
    return 0


def cmd_literature(args: argparse.Namespace) -> int:
    root = pathlib.Path(args.workspace).resolve()
    if not (root / "RESEARCH.md").exists():
        print(
            f"ERROR: no RESEARCH.md in {root}; did you run `omicairn init` first?",
            file=sys.stderr,
        )
        return 2
    ws = workspace.load_workspace(root)
    from . import literature
    lit_dir = root / "literature"
    lit_dir.mkdir(parents=True, exist_ok=True)
    for subdir in (PAPER_LIB_ABSTRACTS_DIR, PAPER_LIB_PDFS_DIR, "fulltext"):
        (lit_dir / subdir).mkdir(exist_ok=True)
    papers = literature.fetch(
        research_md=ws.research_md,
        output_dir=lit_dir,
        max_papers=args.max_papers,
    )
    print(f"Done: {len(papers)} papers fetched to {lit_dir}")
    return 0


def _preflight_check_remote(ssh_host: str) -> None:
    """OPTIONAL, WARN-only: probe an ssh alias for reachability and print a
    clear one-line diagnostic. Never raises, never changes the preflight
    exit code — a remote box being down must not fail a local lint.

    A missing/rotated ssh alias otherwise makes EVERY GPU run targeting that
    host fail silently at the `ssh` call mid-EXECUTE, with no OmiCairn-level
    signal; this surfaces it before a run is dispatched."""
    from . import compute_resources as cr

    reachable, detail = cr.probe_ssh_host(ssh_host)
    if reachable is True:
        print(f"omicairn preflight: ssh {ssh_host} OK")
    elif reachable is False:
        print(
            f"omicairn preflight: WARNING ssh {ssh_host} UNREACHABLE — GPU runs "
            f"targeting this host will fail at EXECUTE. Detail: {detail}",
            file=sys.stderr,
        )
    else:  # None — undetermined / skipped
        print(f"omicairn preflight: ssh {ssh_host} reachability undetermined ({detail})")


def cmd_preflight(args: argparse.Namespace) -> int:
    """Run the fast drift-lint + static-contract test suite.

    Intended as a quick (<2s) developer gate after editing prompts,
    agent files, phase contracts, or CLI subcommand wiring. It invokes
    `python -m pytest` as a subprocess against a curated module list
    — no workspace required, no network, no claude subprocess.

    The preflight suite needs the dev repo layout (tests/ + the lint
    modules present on disk). When OmiCairn is installed from a wheel
    into site-packages there is no `tests/` directory and preflight
    cannot run; in that case it exits with a clear message rather
    than a confusing pytest error.

    With ``--check-remote <ssh_host>`` it ALSO runs an opt-in ssh
    reachability probe (WARN-only — see _preflight_check_remote). The
    drift-lint exit code is unaffected: with no flag, behaviour is
    identical to before.
    """
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    tests_dir = repo_root / "tests"
    if not tests_dir.is_dir():
        print(
            f"ERROR: `omicairn preflight` requires the dev repo layout "
            f"(tests/ under {repo_root}); got a wheel install or a "
            f"stripped checkout. Run from the OmiCairn source tree.",
            file=sys.stderr,
        )
        return 2

    missing = [
        m for m in _PREFLIGHT_TEST_MODULES if not (repo_root / m).exists()
    ]
    if missing:
        print(
            f"ERROR: preflight test modules missing from {repo_root}: "
            f"{missing}. Either the file list in omicairn/cli.py is stale "
            f"or the tests/ directory has been trimmed.",
            file=sys.stderr,
        )
        return 2

    test_paths = [str(repo_root / m) for m in _PREFLIGHT_TEST_MODULES]
    pytest_cmd = [sys.executable, "-m", "pytest", *test_paths]
    if args.verbose:
        pytest_cmd.append("-v")
    else:
        pytest_cmd.extend(["-q", "--tb=line", "--no-header"])

    print(
        f"omicairn preflight: {len(test_paths)} suites, "
        f"repo_root={repo_root}"
    )
    rc = subprocess.call(pytest_cmd, cwd=str(repo_root))
    if rc == 0:
        print("omicairn preflight: OK")
    else:
        print(
            f"omicairn preflight: FAIL (pytest exit {rc})",
            file=sys.stderr,
        )
    # OPTIONAL, additive: ssh-reachability probe. WARN-only — the remote
    # check NEVER perturbs the drift-lint exit code (an offline GPU box must
    # not fail a local lint). With no --check-remote flag this is a no-op.
    if getattr(args, "check_remote", None):
        _preflight_check_remote(args.check_remote)
    return rc


def cmd_paperlib(args: argparse.Namespace) -> int:
    """Inspect / mutate the shared lab literature library.

    The library is a filesystem-only store (the literature analogue of
    the lab knowledge base). This subcommand is the human-facing surface:
    list / search / stats / get / add / convert. The library root is
    resolved by paper_library.resolve_root: `--root` > $OMICAIRN_PAPER_LIB >
    `<cwd>/workspaces/lab_library` (CLI default).
    """
    from . import paper_library
    from .constants import PAPER_LIB_PDFS_DIR
    from .literature import Paper

    # Thread the resolved root through every action so list/search/get/add/
    # stats/convert all act on the SAME visible store.
    root = paper_library.resolve_root(explicit=getattr(args, "root", None))

    if args.action == "stats":
        s = paper_library.stats(root=root)
        print(f"Lab literature library at {root}")
        for k, v in s.items():
            print(f"  {k}: {v}")
        return 0

    if args.action == "convert":
        # Convert library PDFs that lack markdown. `--all` does the whole
        # library; otherwise a single DOI/PMID `target` is required.
        lib = paper_library.ensure_library(root)
        targets: list[dict] = []
        manifest = paper_library._read_manifest(lib)
        if getattr(args, "all", False):
            targets = list(manifest.values())
        elif args.target:
            entry = paper_library.get(args.target, root=root)
            if entry is None:
                print(f"not found: {args.target}", file=sys.stderr)
                return 1
            targets = [entry]
        else:
            print("ERROR: `omicairn paperlib convert` needs a DOI/PMID or --all",
                  file=sys.stderr)
            return 2
        converted = skipped = no_pdf = 0
        for entry in targets:
            slug = paper_library.slug_for(entry)
            pdf = lib / PAPER_LIB_PDFS_DIR / f"{slug}.pdf"
            if not pdf.exists():
                no_pdf += 1
                continue
            # add(convert=True) is idempotent — only converts when markdown
            # is missing, and records md_path in the manifest.
            before = paper_library.get_markdown(slug, root=root)
            paper_library.add(entry, root=root, convert=True,
                              workspace="cli", source="cli_convert")
            after = paper_library.get_markdown(slug, root=root)
            if after and not before:
                converted += 1
            else:
                skipped += 1
        print(f"converted: {converted}  already-had-markdown: {skipped}  "
              f"no-pdf: {no_pdf}  (root {root})")
        return 0

    if args.action == "list":
        manifest = paper_library._read_manifest(paper_library.ensure_library(root))
        for i, p in enumerate(sorted(manifest.values(),
                                     key=lambda p: (p.get("year") or 0), reverse=True)):
            if args.limit and i >= args.limit:
                break
            year = p.get("year") or "?"
            title = (p.get("title") or "")[:70]
            doi = p.get("doi") or p.get("pmid") or "no-id"
            print(f"  [{year}] {doi}  {title}")
        return 0

    if args.action == "search":
        hits = paper_library.search(args.target, root=root, limit=args.limit)
        for p in hits:
            year = p.get("year") or "?"
            doi = p.get("doi") or p.get("pmid") or "no-id"
            title = (p.get("title") or "")[:70]
            print(f"  [{year}] {doi}  {title}")
        if not hits:
            print(f"(no matches for {args.target!r})")
        return 0

    if args.action == "get":
        if not args.target:
            print("ERROR: `omicairn paperlib get` requires a DOI or PMID", file=sys.stderr)
            return 2
        p = paper_library.get(args.target, root=root)
        if p is None:
            print(f"not found: {args.target}", file=sys.stderr)
            return 1
        import json
        print(json.dumps(p, indent=2, ensure_ascii=False))
        return 0

    if args.action == "add":
        meta = Paper(
            doi=args.doi, pmid=args.pmid, title=args.title or "",
            authors=args.authors or "", year=args.year,
            journal=args.journal or "", abstract=args.abstract or "",
        )
        stored = paper_library.add(
            meta, root=root,
            workspace=args.workspace or "cli",
            source="cli_add",
        )
        print(f"added/merged: {stored.get('doi') or stored.get('pmid') or stored.get('title')}")
        return 0

    if args.action == "get-markdown":
        # Retrieve a library paper's converted full-text markdown. Accepts
        # a DOI, PMID, or a raw library slug.
        if not args.target:
            print("ERROR: `omicairn paperlib get-markdown` requires a DOI/PMID/slug",
                  file=sys.stderr)
            return 2
        md = paper_library.get_markdown(args.target, root=root)
        if md is None:
            print(f"no markdown for: {args.target}", file=sys.stderr)
            return 1
        print(md)
        return 0

    print(f"ERROR: unknown paperlib action {args.action!r}", file=sys.stderr)
    return 2


def cmd_labkb(args: argparse.Namespace) -> int:
    """Inspect / build the cross-workspace LAB knowledge base — the lab's
    compounding memory distilled from prior runs' knowledge graphs,
    URI-indexed back to where each result lives.

      omicairn labkb distill <ws> [--uri-base s3://bucket/prefix]
      omicairn labkb list [--kind hypothesis|finding|dataset|direction]
      omicairn labkb search "<text>"
      omicairn labkb stats
      omicairn labkb compact [--apply]   # fold-and-rewrite the op-log (DRY-RUN default)
    """
    from . import lab_knowledge
    explicit = getattr(args, "root", None)
    if args.action == "compact":
        lab_root = lab_knowledge.resolve_root(explicit=explicit)
        try:
            r = lab_knowledge.compact(root=lab_root, apply=args.apply)
        except (RuntimeError, TimeoutError) as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)
            return 1
        print(f"Lab KB at {r['root']}: {r['raw_lines']} raw lines fold to "
              f"{r['folded_keys']} keys ({r['redundancy']}x redundancy)")
        if r["already_compact"]:
            print("  already compact — nothing to do.")
        elif not args.apply:
            print(f"  DRY RUN — would rewrite to {r['folded_keys']} lines "
                  f"(identical folded state). Re-run with --apply.")
        else:
            print(f"  COMPACTED to {r.get('raw_lines_after')} lines; folded "
                  f"state verified identical. backup: {r['backup_path']}")
        return 0
    if args.action == "distill":
        ws_root = pathlib.Path(args.target).resolve()
        if not (ws_root / "RESEARCH.md").exists():
            print(f"ERROR: not a workspace (no RESEARCH.md): {ws_root}",
                  file=sys.stderr)
            return 2
        # Default lab root = the distilled workspace's own workspaces root
        # (sibling `lab_knowledge/`), matching what `omicairn run` writes to.
        lab_root = lab_knowledge.resolve_root(
            explicit=explicit, workspaces_root=ws_root.parent)
        slug = ""
        try:
            slug = workspace.load_workspace(ws_root).research_md.topic_slug
        except Exception:
            pass
        counts = lab_knowledge.distill_workspace(
            ws_root, topic_slug=slug, uri_base=args.uri_base, root=lab_root)
        print(f"Distilled {ws_root.name} into the lab KB at {lab_root}: {counts}")
        if args.uri_base:
            print(f"  evidence indexed under: {args.uri_base}")
        return 0
    lab_root = lab_knowledge.resolve_root(explicit=explicit)
    if args.action == "stats":
        s = lab_knowledge.stats(root=lab_root)
        print(f"Lab knowledge base at {lab_root}")
        for k, v in s.items():
            print(f"  {k}: {v}")
        return 0
    if args.action == "list":
        for e in lab_knowledge.query(kind=args.kind, limit=args.limit,
                                     root=lab_root):
            st = f"[{e.get('status')}] " if e.get("status") else ""
            print(f"  {e['kind']:10} {st}{e['title'][:70]}  ({e['workspace']})")
        return 0
    if args.action == "search":
        for e in lab_knowledge.query(text=args.target, limit=args.limit,
                                     root=lab_root):
            uri = (e.get("evidence_uris") or [e.get("source_uri", "")])[0]
            print(f"  {e['kind']:10} {e['title'][:60]}  -> {uri}")
        return 0
    print(f"ERROR: unknown labkb action {args.action!r}", file=sys.stderr)
    return 2


def cmd_labtheorems(args: argparse.Namespace) -> int:
    """Lab theorem library — the Lean-VALIDATED theorem store + retrieval seam
    + virtual mathematician. Every entry's proof was checked sorry-free by the
    LOCAL Lean toolchain (validate, don't trust). See omicairn.lab_theorems.

      omicairn labtheorems list [--level <band>] [--status verified|unverified|refuted]
      omicairn labtheorems search "<text>"
      omicairn labtheorems retrieve "<goal/keywords>"  # EXECUTE-stage retrieval seam
      omicairn labtheorems stats
      omicairn labtheorems graph              # print the prerequisite DAG edges
      omicairn labtheorems check              # curriculum-monotonicity invariant
      omicairn labtheorems index              # render the markdown index
      omicairn labtheorems show <name>        # one theorem's full record + verdict
    """
    from . import lab_theorems as ml
    from .constants import TheoremStatus
    root = getattr(args, "root", None)
    lab_root = ml.resolve_root(explicit=root)
    action = args.action
    if action == "stats":
        s = ml.stats(root=lab_root)
        print(f"Lab theorem library at {lab_root}")
        for k, v in s.items():
            print(f"  {k}: {v}")
        return 0
    if action == "retrieve":
        # EXECUTE-stage retrieval seam: given a goal/keyword query, return the
        # most relevant VERIFIED library theorems WITH their compiled Lean
        # source, so a theory-phase agent (which has Bash) can query the
        # library before cold-proving and import the verified lemmas as
        # scaffolding. Headless-robust: a plain CLI read, no runtime deps.
        vm = ml.VirtualMathematician(root=lab_root)
        hits = vm.retrieve(args.target or "", k=args.limit, min_overlap=1)
        if not hits:
            print(f"# no verified library theorems matched: {args.target!r}")
            return 0
        print(f"# {len(hits)} verified theorem(s) relevant to: {args.target!r}")
        print("# import these compiled lemmas instead of re-deriving them.\n")
        for e in hits:
            print(f"## {e.name}  [{e.curriculum_level}]  — {e.statement}")
            if e.citation:
                print(f"#  source: {e.citation}")
            if e.prerequisites:
                print(f"#  prereqs: {', '.join(e.prerequisites)}")
            print("```lean")
            print(e.lean_source.rstrip())
            print("```\n")
        return 0
    if action in ("list", "search"):
        entries = ml.query(
            text=(args.target or None) if action == "search" else None,
            level=getattr(args, "level", None),
            status=getattr(args, "status", None),
            limit=args.limit, root=lab_root,
        )
        for e in entries:
            mark = {TheoremStatus.VERIFIED: "OK",
                    TheoremStatus.VERIFIED_TRIVIAL: "~~",
                    TheoremStatus.UNVERIFIED: "??",
                    TheoremStatus.REFUTED: "XX"}.get(e.status, "??")
            print(f"  [{mark}] {e.curriculum_level:11} {e.name:28} {e.statement}")
        return 0
    if action == "graph":
        g = ml.build_graph(root=lab_root)
        print(f"{len(g.nodes)} theorems, {len(g.edges)} depends_on edges:")
        for e in sorted(g.edges.values(), key=lambda x: x.src):
            print(f"  {e.src.split(':',1)[1]}  --depends_on-->  {e.dst.split(':',1)[1]}")
        return 0
    if action == "check":
        v = ml.check_curriculum_monotonic(root=lab_root)
        if not v:
            print("curriculum-monotonicity: CLEAN")
            return 0
        print("curriculum-monotonicity VIOLATIONS:")
        for line in v:
            print(f"  - {line}")
        return 1
    if action == "index":
        print(ml.render_index_md(root=lab_root))
        return 0
    if action == "show":
        e = ml.get(args.target, root=lab_root)
        if e is None:
            print(f"ERROR: no theorem named {args.target!r}", file=sys.stderr)
            return 2
        print(f"name:        {e.name}")
        print(f"statement:   {e.statement}")
        print(f"level:       {e.curriculum_level}")
        print(f"status:      {e.status}  (compiled={e.compiled}, "
              f"sorry_free={e.sorry_free})")
        print(f"prereqs:     {e.prerequisites}")
        print(f"tactics:     {e.tactics}")
        print(f"theorems:    {e.theorems_checked}")
        print(f"citation:    {e.citation}")
        print(f"substance:   {e.substance_warnings or 'no warnings'}")
        print(f"lean_file:   {e.lean_file}")
        print(f"\nlean source:\n{e.lean_source}")
        return 0
    print(f"ERROR: unknown labtheorems action {action!r}", file=sys.stderr)
    return 2


def cmd_paperreview(args: argparse.Namespace) -> int:
    """Math paper review + formalization — REVIEW landmark math papers and
    machine-verify their core theorems against the local Lean/Mathlib
    toolchain (validate, don't trust). A `verified` claim is sorry-free AND
    axiom-clean AND substance-gate-pass; verified claims are linked into the
    lab_theorems store. See omicairn.paper_review + docs/design-docs/math-paper-review.md.

      omicairn paperreview list [--era <band>] [--verdict <v>]
      omicairn paperreview show <paper_id>
      omicairn paperreview stats
      omicairn paperreview review <paper_id>   # re-run verification on recorded claims
      omicairn paperreview index | graph
      omicairn paperreview add <paper_id> [--title ... --author ... --year ... --era ...]
    """
    from . import paper_review as pr
    from .constants import ClaimVerdict
    root = getattr(args, "root", None)
    pr_root = pr.resolve_root(explicit=root)
    action = args.action
    if action == "stats":
        s = pr.stats(root=pr_root)
        print(f"Math paper review store at {pr_root}")
        for k, v in s.items():
            print(f"  {k}: {v}")
        return 0
    if action == "list":
        papers = pr.load_papers(root=pr_root)
        era = getattr(args, "era", None)
        vfilter = getattr(args, "verdict", None)
        mark = {ClaimVerdict.VERIFIED: "OK", ClaimVerdict.FORMALIZED_STMT_ONLY: "~~",
                ClaimVerdict.FAILED: "XX", ClaimVerdict.DEFERRED: ".."}
        for p in sorted(papers.values(), key=lambda x: x.paper_id):
            if era is not None and p.era != era:
                continue
            cl = pr.claims_for(p.paper_id, root=pr_root)
            if vfilter is not None:
                cl = [c for c in cl if c.verdict == vfilter]
                if not cl:
                    continue
            print(f"  [{p.era:12}] {p.paper_id:22} {p.title} — {p.author} ({p.year})")
            for c in cl:
                m = mark.get(c.verdict, "..")
                print(f"      [{m}] {c.informal_statement}")
        return 0
    if action == "show":
        p = pr.get_paper(args.target, root=pr_root)
        if p is None:
            print(f"ERROR: no paper {args.target!r}", file=sys.stderr)
            return 2
        print(f"paper_id:  {p.paper_id}")
        print(f"title:     {p.title}")
        print(f"author:    {p.author}   year: {p.year}   era: {p.era}")
        print(f"citation:  {p.citation}")
        for c in pr.claims_for(p.paper_id, root=pr_root):
            print(f"\n  claim: {c.claim_id}")
            print(f"    statement: {c.informal_statement}")
            print(f"    verdict:   {c.verdict}  (axiom_clean={c.axiom_clean})")
            if c.axioms:
                print(f"    axioms:    {c.axioms}")
            if c.linked_theorem:
                print(f"    linked:    {c.linked_theorem} (lab_theorems)")
            if c.defer_reason:
                print(f"    deferred:  {c.defer_reason}")
            if c.substance_warnings:
                print(f"    substance: {len(c.substance_warnings)} warning(s)")
            if c.lean_proof:
                print(f"    lean:\n{c.lean_proof}")
        return 0
    if action == "review":
        # Verify against the reusable Mathlib Lake project (.lean-mathlib-omicairn).
        # mathlib_workdir() locates it (env override or upward search) — the
        # heavy gitignored checkout is NOT in a worktree, so set
        # $OMICAIRN_MATHLIB_PROJECT_ROOT when reviewing from one.
        wd = pr.mathlib_workdir()
        if wd is None:
            print("WARN: no .lean-mathlib-omicairn project found; set "
                  "$OMICAIRN_MATHLIB_PROJECT_ROOT. Verdicts will be honest, not "
                  "verified.", file=sys.stderr)
        # Linked theorems go to a paper-review-owned lab_theorems store, never
        # the shared lab_theorems/store/.
        lab_root = pr_root.parent / "linked_theorems"
        rev = pr.Reviewer(workdir=wd, root=pr_root, lab_root=lab_root)
        res = rev.review_paper(args.target)
        print(f"reviewed {args.target}: {res.note}")
        return 0
    if action == "index":
        print(pr.render_index_md(root=pr_root))
        return 0
    if action == "graph":
        g = pr.build_graph(root=pr_root)
        print(f"{len(g.nodes)} nodes, {len(g.edges)} edges:")
        for e in sorted(g.edges.values(), key=lambda x: x.src):
            print(f"  {e.src}  --{e.rel}-->  {e.dst}")
        return 0
    if action == "add":
        if not args.target:
            print("ERROR: add requires a <paper_id>", file=sys.stderr)
            return 2
        p = pr.add_paper(
            args.target, title=getattr(args, "title", "") or "",
            author=getattr(args, "author", "") or "",
            year=getattr(args, "year", "") or "",
            era=getattr(args, "era", None) or "modern",
            citation=getattr(args, "citation", "") or "",
            root=pr_root)
        print(f"added paper {p.paper_id}")
        return 0
    print(f"ERROR: unknown paperreview action {action!r}", file=sys.stderr)
    return 2


def cmd_project(args: argparse.Namespace) -> int:
    """Project Descriptor Protocol — make any folder OmiCairn-interoperable.

      omicairn project describe <folder> [--out DIR]
          Deterministic. Emit project.json (+ PROJECT.md) for a OmiCairn
          workspace from its knowledge graph + RESEARCH.md.

      omicairn project scan <folder> [--out DIR] [--distill] [--model M]
          General. A OmiCairn workspace is described deterministically; a
          FOREIGN folder (manual / other-agent / collaborator) is scanned
          by an agent that infers the descriptor + a knowledge graph, with
          findings/hypotheses marked verified=false (re-derive before trust).
          --distill folds the result into the cross-run lab KB.
    """
    from . import project_descriptor as pdesc
    folder = pathlib.Path(args.target).resolve()
    if not folder.is_dir():
        print(f"ERROR: no such folder: {folder}", file=sys.stderr)
        return 2
    out = pathlib.Path(args.out).resolve() if args.out else folder
    is_omicairn = ((folder / "RESEARCH.md").exists()
               or (folder / KNOWLEDGE_DIR / KG_GRAPH_FILE).exists())

    # Deterministic path: OmiCairn workspaces (and explicit `describe`).
    if args.action == "describe" or (args.action == "scan" and is_omicairn):
        if args.action == "describe" and not is_omicairn:
            print("ERROR: `describe` is deterministic and needs a OmiCairn "
                  "workspace; use `scan` for foreign folders.", file=sys.stderr)
            return 2
        d = pdesc.describe_workspace(folder)
        path = pdesc.write_descriptor(out, d)
        counts = {k: len(v) for k, v in d.knowledge.items()}
        print(f"Wrote {path}  (origin={d.origin}, knowledge={counts})")
        if getattr(args, "distill", False):
            _project_distill(out)
        return 0

    # Foreign folder → agent scanner.
    from . import claude_runner
    from .workspace import PROMPTS_DIR
    from . import phases as _phases
    prompt = (PROMPTS_DIR / "project_scan.md").read_text(encoding="utf-8")
    prompt = (prompt.replace("{{scan_target}}", str(folder))
                    .replace("{{scan_out}}", str(out)))
    out.mkdir(parents=True, exist_ok=True)
    print(f"Scanning foreign folder {folder} with an agent "
          f"(model={args.model}) …")
    claude_runner.run(
        prompt=prompt, workspace_root=out, data_dir=folder,
        allowed_tools=_phases._DEFAULT_TOOLS, model=args.model,
        timeout_seconds=args.timeout, log_path=out / "project_scan.log",
        stage_dir=out, phase_name="project_scan",
    )
    d = pdesc.read_descriptor(out)
    if d is None:
        print(f"ERROR: scan produced no valid {out}/project.json "
              f"(see {out}/project_scan.log)", file=sys.stderr)
        return 1
    errs = pdesc.validate(d.to_dict())
    if errs:
        print("ERROR: scanned project.json failed schema validation:",
              file=sys.stderr)
        for e in errs:
            print(f"  - {e}", file=sys.stderr)
        return 1
    counts = pdesc.ingest_descriptor_to_graph(out, d, mark_unverified=True)
    # Re-render PROJECT.md from the validated descriptor (one-way view).
    pdesc.write_descriptor(out, d)
    print(f"Scanned {folder.name} (origin={d.origin}). Ingested {counts} "
          f"into {out}/knowledge/graph.jsonl (foreign claims verified=false).")
    if getattr(args, "distill", False):
        _project_distill(out)
    return 0


def _project_distill(folder: pathlib.Path) -> None:
    """Fold a scanned/described project's graph into the cross-run lab KB."""
    from . import lab_knowledge
    root = lab_knowledge.resolve_root(workspaces_root=folder.parent)
    counts = lab_knowledge.distill_workspace(folder, root=root)
    print(f"  distilled into lab KB at {root}: {counts}")


def cmd_ideate(args: argparse.Namespace) -> int:
    """Brainstorm front-stage: survey the landscape and propose research
    directions, OR list/adopt previously-proposed directions.

    Standalone — runs before SCOPE, when the user has a research purpose
    but not a concrete question. Three modes:
      omicairn ideate <ws>               run the IDEATE stage (survey + propose)
      omicairn ideate <ws> --list        print the proposed directions
      omicairn ideate <ws> --adopt <id>  promote a direction to active goal +
                                     steer the downstream pipeline at it
    """
    from . import knowledge_graph as kg
    root = pathlib.Path(args.workspace).resolve()
    if not (root / "RESEARCH.md").exists():
        print(
            f"ERROR: no RESEARCH.md in {root}; run `omicairn init` first "
            f"(its research_question is your research PURPOSE for ideation)",
            file=sys.stderr,
        )
        return 2
    ws = workspace.load_workspace(root)

    # Record the OmiCairn system version + datetime for this standalone
    # `ideate` invocation (per-invocation reproducibility provenance).
    # Only on a real run, not the read-only --list / --adopt modes.
    if not args.list and not args.adopt:
        from . import provenance
        provenance.record_invocation(root, command="ideate")

    # --list: just show what's already proposed.
    if args.list:
        print(kg.render_directions_md(root))
        return 0

    # --adopt: promote a proposed direction and steer the pipeline at it.
    if args.adopt:
        try:
            goal = kg.adopt_direction(root, args.adopt)
        except (KeyError, ValueError) as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
        label = goal.extra.get("label", goal.title)
        seeds = goal.extra.get("seed_hypotheses", [])
        # Steer the downstream pipeline via the proven user-notes channel:
        # a sticky, all-phase note SCOPE/PLAN/EXECUTE will read verbatim.
        note_body = (
            f"ADOPTED RESEARCH DIRECTION (from IDEATE): {label}\n"
            f"Question: {goal.title}\n"
            f"Rationale: {goal.extra.get('rationale', '')}\n"
            f"Seed hypotheses to pursue:\n"
            + "\n".join(f"  - {h}" for h in seeds)
            + "\n\nTreat this as the active research goal: SCOPE should "
            "frame the question around it, PLAN should pre-register these "
            "seed hypotheses, EXECUTE should test them (and may raise more "
            "on the fly)."
        )
        user_notes.append_note(root, note_body, phase=None, once=False)
        # Ensure the surveyed bibliography is in literature/ for the
        # downstream pipeline (idempotent re-sync; metadata always, PDFs
        # when web is on). Closes the IDEATE→downstream bibliography seam.
        orchestrator._export_ideate_sources_to_literature(
            ws, download=(ws.research_md.allow_web and not dry_run_enabled()))
        print(f"Adopted direction: {label}")
        print(f"  goal: {goal.id}")
        print(f"  materialized {len(seeds)} seed hypotheses as pre-registered")
        print("  wrote a sticky steering note for the downstream pipeline.")
        print("  synced IDEATE sources into literature/ for PLAN.")
        print(f"  next: omicairn run {root}")
        return 0

    # Default: run the IDEATE stage. Retry on a transient CONTRACT_FAIL
    # (empty claude output / rate-limit blip) up to max_iters_per_phase —
    # `omicairn ideate` is standalone so it doesn't get the pipeline loop's
    # §3.1 retry; without this, a single empty-output blip leaves an
    # empty ideate stage and the user must notice + re-run by hand
    # (observed on a real 50-source run). Only CONTRACT_FAIL retries;
    # TIMEOUT / SESSION_LIMIT / HALTED are terminal.
    max_iters = int(ws.research_md.max_iters_per_phase)
    outcome = None
    for attempt in range(1, max_iters + 1):
        outcome = orchestrator.run_next_stage(ws, forced_phase=PhaseName.IDEATE)
        if outcome is None:
            print("ERROR: could not run IDEATE (run already done/halted?)",
                  file=sys.stderr)
            return 2
        if outcome.status != "CONTRACT_FAIL" or attempt >= max_iters:
            break
        print(f"  IDEATE attempt {attempt}/{max_iters} hit a transient "
              f"CONTRACT_FAIL; retrying...", file=sys.stderr)
    print("\n=== ideate summary ===")
    print(f"  workspace: {root}")
    print(f"  stage:     {outcome.stage_dir_rel}")
    print(f"  status:    {outcome.status}")
    print(f"  note:      {outcome.note}")
    if outcome.status == "DONE":
        # Bridge surveyed sources into literature/ WITH PDF download when
        # web is enabled (the metadata bridge already ran in the ingest
        # hook). This is IDEATE "directly downloading researched papers
        # into the literature folder" — contributing to the downstream
        # literature-review stage.
        if ws.research_md.allow_web and not dry_run_enabled():
            orchestrator._export_ideate_sources_to_literature(ws, download=True)
            lit = root / "literature" / "papers.json"
            n = 0
            if lit.exists():
                try:
                    import json as _json
                    n = len(_json.loads(lit.read_text()))
                except (OSError, ValueError):
                    n = 0
            print(f"  literature/: {n} sources bridged for the downstream "
                  f"pipeline (papers.json + abstracts + best-effort PDFs)")
        print("\n" + kg.render_directions_md(root))
        print("\nAdopt one with:  omicairn ideate "
              f"{root} --adopt <direction_id>")
    return 0 if outcome.status == "DONE" else 1


def cmd_inspect(args: argparse.Namespace) -> int:
    root = pathlib.Path(args.workspace).resolve()
    if args.stage:
        # Direct stage dir name (e.g. 003_execute).
        sdir = root / args.stage
        if not sdir.exists():
            print(f"ERROR: no stage directory {sdir}", file=sys.stderr)
            return 2
        print(f"== contents of {sdir} ==")
        for p in stage_mod.iter_stage_artifacts(sdir):
            sz = p.stat().st_size
            print(f"  {p.relative_to(root)}  ({sz} B)")
        return 0
    if args.phase:
        # Latest done stage of the given phase.
        latest = stage_mod.latest_stage_of(root, args.phase)
        if latest is None:
            print(
                f"ERROR: no done stage of phase {args.phase!r} in {root}",
                file=sys.stderr,
            )
            return 2
        print(f"== contents of {latest.path} ==")
        for p in stage_mod.iter_stage_artifacts(latest.path):
            sz = p.stat().st_size
            print(f"  {p.relative_to(root)}  ({sz} B)")
        return 0
    # Default: list all stages.
    print(stage_mod.render_stage_listing(root))
    return 0


def cmd_reap(args: argparse.Namespace) -> int:
    """Mark ORPHANED runs terminal so they stop polluting audit sweeps.

    A run is *orphaned* when STATE.md says ``in_progress`` but it has no live
    ``.run.lock`` AND no live ``omicairn run`` OS process — an abandoned plan
    duplicate, a superseded re-run, or a process killed mid-stage. OmiCairn only
    has DONE/HALTED as terminal states, so such a run shows as a phantom
    active run forever. This marks each confirmed orphan HALTED (reversible —
    the owner can ``omicairn run`` again) and journals a ``reaped_orphan`` event.

    SAFETY:
      - DRY-RUN IS THE DEFAULT. Without ``--apply`` nothing is mutated; we
        only print what WOULD be reaped.
      - NO files are ever deleted — deletion is the owner's call.
      - A done/halted run, a live lock, or a live process all veto reaping.
      - If process liveness can't be determined, every run is treated as
        ALIVE (fail safe).

    ``<target>`` may be a single workspace dir OR a workspaces-root (every
    child workspace is scanned).
    """
    from . import reap as reap_mod

    target = pathlib.Path(args.target).resolve()
    if not target.is_dir():
        print(f"ERROR: no such directory: {target}", file=sys.stderr)
        return 1

    report = reap_mod.reap(target, apply=args.apply)

    verb = "reaped" if args.apply else "would reap"
    print(f"== omicairn reap ({'APPLY' if args.apply else 'DRY-RUN'}) over {target} ==")
    if report.process_scan_degraded:
        print(
            "  WARNING: could not enumerate OS processes (no /proc, ps "
            "unavailable). Failing safe: every in_progress run treated as "
            "ALIVE, nothing reaped.",
            file=sys.stderr,
        )
    if not report.orphans:
        print(f"  scanned {report.scanned} workspace(s); no orphaned runs found.")
        return 0
    print(f"  scanned {report.scanned} workspace(s); {report.orphan_count} orphaned:")
    for v in report.orphans:
        print(f"    [{verb}] {v.workspace.name}  (status={v.status})")
        print(f"             why: {v.reason}")
    if not args.apply:
        print(
            f"\n  DRY-RUN — nothing changed. Re-run with --apply to mark these "
            f"{report.orphan_count} run(s) HALTED."
        )
    else:
        print(f"\n  marked {report.reaped_count} run(s) HALTED (no files deleted).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="omicairn",
        description="OmiCairn: automated research with Claude Code as backbone",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
            Subcommands:
              init        create a workspace from a RESEARCH.md
              run         drive the pipeline (resumes automatically)
              continue    optionally add a user note and resume the run
              note        append a free-form instruction for the next phase
              status      print STATE.md summary
              fleet       one-line-per-run status table over a workspaces root
              literature  fetch/re-fetch literature for a workspace
              paperlib    inspect / mutate the shared paper library
              inspect     list files in a stage directory
              reap        mark orphaned (dead in_progress) runs terminal (dry-run by default)
              preflight   run fast drift-lint suite over the dev repo
              ideate      brainstorm: survey sources + propose directions (standalone)
              labkb       cross-run lab knowledge base (distill/list/search/stats)
              labtheorems  lab theorem library: Lean-validated theorems + retrieval + virtual mathematician
              paperreview  math paper review: review landmark papers + machine-verify their theorems in Lean
              feedback    cross-run human-feedback store (list/stats/export)
              project     describe/scan a folder into a portable project.json + graph
              audit       --replay-gates: replay the cross_check gate suite across runs
              steer       create a human-steering plan for a workspace
        """),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="initialise a workspace")
    p_init.add_argument("topic_slug")
    p_init.add_argument("--from-research-md", required=True)
    p_init.add_argument("--workspaces-root", default=None)
    p_init.add_argument(
        "--skip-literature",
        action="store_true",
        help="skip pre-fetching literature during init",
    )
    p_init.set_defaults(func=cmd_init)

    p_run = sub.add_parser("run", help="run OmiCairn stages (resumes automatically)")
    p_run.add_argument("workspace")
    p_run.add_argument(
        "--phase",
        default=PHASE_CHOICE_ALL,
        choices=CLI_PHASE_CHOICES,
        help="run a single phase (one stage) or 'all' to drive the loop",
    )
    p_run.add_argument(
        "--add-stage",
        default=None,
        choices=list(PIPELINE_ORDER),
        help="manually insert one stage of the given phase and stop",
    )
    p_run.add_argument(
        "--stop-after",
        default=None,
        choices=list(PIPELINE_ORDER),
        help="stop after the first done stage of this phase",
    )
    p_run.add_argument(
        "--max-stages",
        type=int,
        default=64,
        help="safety belt: refuse to run more than N stages per invocation",
    )
    p_run.add_argument(
        "--dry-run",
        action="store_true",
        help=f"enable OmiCairn dry-run (set {ENV_DRY_RUN}=1): stub out claude calls",
    )
    p_run.add_argument(
        "--dry-verdict",
        default=None,
        help=(
            f"force the next verdict-emitting stage to return this decision "
            f"(sets {ENV_DRY_VERDICT})"
        ),
    )
    p_run.set_defaults(func=cmd_run)

    p_note = sub.add_parser("note", help="append a free-form user note for the next phase to consume")
    p_note.add_argument("workspace")
    p_note.add_argument("message", help="note body; free-form markdown")
    p_note.add_argument(
        "--phase",
        default=None,
        choices=list(PIPELINE_ORDER),
        help="scope the note to a single phase (default: visible to all)",
    )
    p_note.add_argument(
        "--once",
        action="store_true",
        help="move note to consumed log after the next matching phase succeeds",
    )
    p_note.set_defaults(func=cmd_note)

    p_cont = sub.add_parser("continue", help="optionally append a user note, then resume the run")
    p_cont.add_argument("workspace")
    p_cont.add_argument(
        "--phase",
        default=PHASE_CHOICE_ALL,
        choices=CLI_PHASE_CHOICES,
        help="phase to run next (default: all — continue the pipeline)",
    )
    p_cont.add_argument(
        "--note",
        default=None,
        help="free-form note added before running (same semantics as `omicairn note`)",
    )
    p_cont.add_argument(
        "--once",
        action="store_true",
        help="treat --note as single-shot (consumed on next matching phase)",
    )
    p_cont.add_argument(
        "--stop-after",
        default=None,
        choices=list(PIPELINE_ORDER),
    )
    p_cont.add_argument("--max-stages", type=int, default=64)
    p_cont.add_argument("--dry-run", action="store_true")
    p_cont.add_argument("--dry-verdict", default=None)
    p_cont.set_defaults(func=cmd_continue)

    p_steer = sub.add_parser("steer", help="dispatch the steering planner with a natural-language request")
    p_steer.add_argument("workspace")
    p_steer.add_argument(
        "request",
        help="natural-language steering request (single quoted string)",
    )
    p_steer.set_defaults(func=cmd_steer)

    p_status = sub.add_parser("status", help="show run state")
    p_status.add_argument("workspace")
    p_status.set_defaults(func=cmd_status)

    p_fleet = sub.add_parser("fleet", help="one-line-per-run status table across a workspaces root (read-only)")
    p_fleet.add_argument(
        "path",
        nargs="?",
        default=None,
        help="workspaces root or a single workspace (default: ./workspaces)",
    )
    p_fleet.set_defaults(func=cmd_fleet)

    p_lit = sub.add_parser("literature", help="fetch/re-fetch literature")
    p_lit.add_argument("workspace")
    p_lit.add_argument("--max-papers", type=int, default=50)
    p_lit.set_defaults(func=cmd_literature)

    p_paperlib = sub.add_parser("paperlib", help="inspect / mutate the shared lab literature library")
    p_paperlib.add_argument(
        "action",
        choices=["list", "search", "get", "add", "stats", "convert",
                 "get-markdown"])
    p_paperlib.add_argument("target", nargs="?", default="",
                            help="query (search) / DOI/PMID (get, convert, "
                                 "get-markdown)")
    p_paperlib.add_argument("--root", default=None,
                            help="library root dir (overrides $OMICAIRN_PAPER_LIB)")
    p_paperlib.add_argument("--all", action="store_true",
                            help="for action=convert: convert all library PDFs")
    p_paperlib.add_argument("--limit", type=int, default=20)
    p_paperlib.add_argument("--doi", default=None, help="for action=add")
    p_paperlib.add_argument("--pmid", default=None, help="for action=add")
    p_paperlib.add_argument("--title", default=None, help="for action=add")
    p_paperlib.add_argument("--authors", default=None, help="for action=add")
    p_paperlib.add_argument("--year", type=int, default=None, help="for action=add")
    p_paperlib.add_argument("--journal", default=None, help="for action=add")
    p_paperlib.add_argument("--abstract", default=None, help="for action=add")
    p_paperlib.add_argument("--workspace", default=None, help="provenance tag")
    p_paperlib.set_defaults(func=cmd_paperlib)

    p_preflight = sub.add_parser("preflight", help="run fast drift-lint suite over the dev repo")
    p_preflight.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="run pytest in verbose mode (per-test output)",
    )
    p_preflight.add_argument(
        "--check-remote",
        metavar="SSH_HOST",
        default=None,
        help=(
            "ALSO probe an ssh alias for reachability (a missing/rotated "
            "alias makes GPU runs fail silently mid-EXECUTE). WARN-only — "
            "never changes the preflight exit code."
        ),
    )
    p_preflight.set_defaults(func=cmd_preflight)

    p_ideate = sub.add_parser("ideate", help="brainstorm front-stage: survey sources + propose research directions (run before SCOPE); --list / --adopt to steer")
    p_ideate.add_argument("workspace")
    p_ideate.add_argument(
        "--list", action="store_true",
        help="print proposed directions from the knowledge graph and exit",
    )
    p_ideate.add_argument(
        "--adopt", default=None, metavar="DIRECTION_ID",
        help="promote a proposed direction to the active research goal and "
             "write a steering note for the downstream pipeline",
    )
    p_ideate.set_defaults(func=cmd_ideate)

    p_labkb = sub.add_parser("labkb", help="cross-run lab knowledge base: distill / list / search / stats / compact")
    p_labkb.add_argument("action",
                         choices=["distill", "list", "search", "stats", "compact"])
    p_labkb.add_argument("target", nargs="?", default="",
                         help="workspace dir (distill) or query text (search)")
    p_labkb.add_argument("--apply", action="store_true",
                         help="compact: actually rewrite the op-log "
                              "(DRY-RUN is the default; refuses if a live "
                              "OmiCairn run holds the lab-KB lock).")
    p_labkb.add_argument("--kind", default=None,
                         choices=["hypothesis", "finding", "dataset", "direction"],
                         help="filter for list")
    p_labkb.add_argument("--uri-base", default=None,
                         help="URI prefix RECORDED for results (e.g. s3://bucket/omicairn/run); "
                              "default indexes local file:// paths. NOTE: this only records "
                              "the URI — OmiCairn does NOT upload anything (storage stays local; "
                              "S3 upload is a future bonus, currently not implemented).")
    p_labkb.add_argument("--limit", type=int, default=50)
    p_labkb.add_argument("--root", default=None,
                         help="lab knowledge base directory. Overrides "
                              "$OMICAIRN_LAB_KB and the default "
                              "<workspaces_root>/lab_knowledge/.")
    p_labkb.set_defaults(func=cmd_labkb)

    p_labthm = sub.add_parser("labtheorems",
        help="lab theorem library: Lean-validated theorem store + retrieval "
             "seam + virtual mathematician "
             "(list/search/retrieve/stats/graph/check/index/show)")
    p_labthm.add_argument(
        "action",
        choices=["list", "search", "retrieve", "stats", "graph", "check",
                 "index", "show"])
    p_labthm.add_argument("target", nargs="?", default="",
                          help="query text (search/retrieve) or theorem name (show)")
    p_labthm.add_argument(
        "--level", default=None,
        choices=["elementary", "junior_high", "high_school", "undergrad",
                 "graduate", "phd"],
        help="filter by curriculum band (list)")
    p_labthm.add_argument(
        "--status", default=None,
        choices=["verified", "verified_trivial", "unverified", "refuted"],
        help="filter by validation status (list)")
    p_labthm.add_argument("--limit", type=int, default=100)
    p_labthm.add_argument(
        "--root", default=None,
        help="theorem library store directory. Overrides $OMICAIRN_LAB_THEOREMS "
             "and the default <repo>/lab_theorems/store/.")
    p_labthm.set_defaults(func=cmd_labtheorems)

    p_paperrev = sub.add_parser("paperreview",
        help="math paper review + formalization: review landmark math papers "
             "and machine-verify their core theorems in Lean "
             "(list/show/stats/review/index/graph/add)")
    p_paperrev.add_argument(
        "action",
        choices=["list", "show", "stats", "review", "index", "graph", "add"])
    p_paperrev.add_argument("target", nargs="?", default="",
                            help="paper_id (show/review/add)")
    p_paperrev.add_argument(
        "--era", default=None,
        choices=["antiquity", "medieval", "renaissance", "early_modern",
                 "19th_century", "20th_century", "modern"],
        help="filter by era (list) / era of a new paper (add)")
    p_paperrev.add_argument(
        "--verdict", default=None,
        choices=["verified", "formalized_stmt_only", "failed", "deferred"],
        help="filter by claim verdict (list)")
    p_paperrev.add_argument("--title", default="", help="paper title (add)")
    p_paperrev.add_argument("--author", default="", help="paper author (add)")
    p_paperrev.add_argument("--year", default="", help="paper year (add)")
    p_paperrev.add_argument("--citation", default="", help="source citation (add)")
    p_paperrev.add_argument(
        "--root", default=None,
        help="paper-review store directory. Overrides $OMICAIRN_PAPER_REVIEW and "
             "the default <repo>/lab_theorems/paper_review/store/.")
    p_paperrev.set_defaults(func=cmd_paperreview)

    p_feedback = sub.add_parser("feedback", help="cross-run human-feedback store: list / stats / export (typed, linked steer/note/correction records)")
    p_feedback.add_argument("fb_action", nargs="?", default="list",
                            choices=["list", "stats", "export"])
    p_feedback.add_argument("out", nargs="?", default="",
                            help="export: output CSV path")
    p_feedback.add_argument("--kind", default=None,
                            choices=["steer", "note", "correction",
                                     "approval", "rejection", "rating"],
                            help="filter by feedback kind")
    p_feedback.add_argument("--workspace", default=None,
                            help="filter by workspace / run-topic-slug")
    p_feedback.add_argument("--tag", default=None, help="filter by tag")
    p_feedback.add_argument("--limit", type=int, default=50)
    p_feedback.set_defaults(func=cmd_feedback)

    p_project = sub.add_parser("project", help=(
        "project descriptor protocol: describe/scan a folder into a "
        "portable project.json + knowledge graph"))
    p_project.add_argument("action", choices=["describe", "scan"])
    p_project.add_argument("target", help="project folder")
    p_project.add_argument("--out", default=None,
                           help="where to write project.json (default: the "
                                "target folder)")
    p_project.add_argument("--distill", action="store_true",
                           help="also fold the result into the cross-run lab KB")
    p_project.add_argument("--model", default="claude-sonnet-4-6",
                           help="model for the foreign-folder scanner agent")
    p_project.add_argument("--timeout", type=int, default=1800,
                           help="scanner agent timeout (seconds)")
    p_project.set_defaults(func=cmd_project)

    p_inspect = sub.add_parser("inspect", help="list stage outputs")
    p_inspect.add_argument("workspace")
    p_inspect.add_argument(
        "--phase",
        default=None,
        choices=list(PIPELINE_ORDER),
        help="show the latest done stage of this phase",
    )
    p_inspect.add_argument(
        "--stage",
        default=None,
        help="show a specific stage directory name (e.g. 003_execute)",
    )
    p_inspect.set_defaults(func=cmd_inspect)

    p_reap = sub.add_parser("reap", help="mark orphaned (dead in_progress) runs terminal; dry-run by default")
    p_reap.add_argument(
        "target",
        help="a single workspace dir OR a workspaces-root (scans all children)",
    )
    p_reap.add_argument(
        "--apply",
        "--yes",
        dest="apply",
        action="store_true",
        help="actually mark orphans HALTED (DEFAULT is a dry run that mutates "
             "nothing). No files are ever deleted.",
    )
    p_reap.set_defaults(func=cmd_reap)

    p_audit = sub.add_parser("audit", help="--replay-gates: replay the cross_check gate suite over one workspace or a whole corpus (read-only)")
    p_audit.add_argument(
        "workspace",
        nargs="?",
        default=None,
        help="workspace directory (omit when using --all)",
    )
    # Cross-corpus gate-suite replay (read-only): replays the importable
    # cross_check gate suite (Lean substance + W13–W25) across MANY finalized
    # workspaces so a newly-merged gate can be regression-tested on real
    # data and surface any latent defect it now catches.
    p_audit.add_argument(
        "--replay-gates", action="store_true", dest="replay_gates",
        help="replay the cross_check gate suite (Lean substance + W13–W25) "
             "over finalized workspaces; read-only, prints a per-workspace "
             "table. Combine with --all or pass explicit workspace(s). "
             "This is the only audit mode (the flag is optional).",
    )
    p_audit.add_argument(
        "--all", action="store_true",
        help="for --replay-gates: sweep EVERY finalized workspace under the "
             "workspaces root (default ./workspaces; override --workspaces-root)",
    )
    p_audit.add_argument(
        "--workspaces-root", default=None,
        help="for --replay-gates --all: the workspaces root to discover "
             "finalized runs under (default: ./workspaces)",
    )
    p_audit.add_argument(
        "--json", action="store_true", dest="as_json",
        help="for --replay-gates: emit machine-readable JSON instead of the "
             "table (the audit cron parses this)",
    )
    p_audit.set_defaults(func=cmd_audit)

    args = parser.parse_args()
    return args.func(args)


def cmd_audit(args: argparse.Namespace) -> int:
    """`omicairn audit [--replay-gates] (--all | <workspace>)`: replay the
    deterministic cross_check gate suite over finalized runs."""
    return _audit_replay_gates(args)


def _audit_replay_gates(args: argparse.Namespace) -> int:
    """Cross-corpus GATE-SUITE replay (read-only).

    Replays the importable cross_check gate suite (Lean substance +
    W13–W25) over finalized workspaces, REUSING the canonical gate
    functions + artifact loaders (no gate logic is reimplemented). The
    target set is either every finalized workspace under the workspaces
    root (`--all`) or the explicit workspace path(s) the caller passes
    positionally (the lone `workspace` arg). Prints a per-workspace table
    (or `--json`). NEVER mutates any workspace artifact — output is stdout
    only.

    Returns 0 always when the sweep ran (gate WARNs/ERRORs are reported,
    not turned into a non-zero exit — this is an audit surface, not a CI
    block), 2 on a bad invocation (no targets resolved).
    """
    import json as _json

    from . import gate_replay

    if args.all:
        ws_root = (
            pathlib.Path(args.workspaces_root).resolve()
            if args.workspaces_root
            else _default_workspaces_root()
        )
        if not ws_root.is_dir():
            print(
                f"audit --replay-gates --all: no such workspaces root: "
                f"{ws_root} (pass --workspaces-root)",
                file=sys.stderr,
            )
            return 2
        roots = gate_replay.discover_workspaces(ws_root)
        if not roots:
            print(
                f"audit --replay-gates --all: no finalized workspaces under "
                f"{ws_root}",
                file=sys.stderr,
            )
            return 2
    else:
        if not args.workspace:
            print(
                "audit --replay-gates: pass --all OR an explicit workspace "
                "path.",
                file=sys.stderr,
            )
            return 2
        root = pathlib.Path(args.workspace).resolve()
        if not root.is_dir():
            print(
                f"audit --replay-gates: workspace not found: {root}",
                file=sys.stderr,
            )
            return 2
        roots = [root]

    replays = gate_replay.replay_corpus(roots)
    if args.as_json:
        print(_json.dumps(gate_replay.to_json(replays), indent=2))
    else:
        print(gate_replay.render_table(replays))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
