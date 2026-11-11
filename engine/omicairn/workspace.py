"""Workspace initialisation.

`omicairn init <topic-slug>` copies templates + phase subagents into a
fresh workspace directory under ``workspaces/`` and seeds STATE.md.

Layout of a fresh workspace (flat-numbered-stages model)::

    workspaces/<slug>-<timestamp>/
    ├── RESEARCH.md              # copied verbatim from the case
    ├── CLAUDE.md                # rendered from templates/CLAUDE.md
    ├── STATE.md                 # YAML front-matter + summary
    ├── events.jsonl             # append-only event log
    ├── knowledge/
    │   ├── journal.md
    │   ├── lessons.jsonl
    │   └── domain_notes.md
    ├── logs/                    # per-phase phase.log copies
    ├── literature/              # optional pre-fetched papers
    │   ├── abstracts/
    │   ├── pdfs/
    │   └── fulltext/
    └── .claude/
        ├── settings.json
        └── agents/              # phase subagent .md files

Stage directories (``000_scope``, ``001_plan``, …) are NOT pre-created.
Each is created by the orchestrator the moment it runs a stage, so
``ls workspace/`` is a one-glance audit of attempts.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import pathlib
import shutil

from . import research_md, stage, state
from .constants import (
    EVENTS_LOG,
    EventKind,
    KNOWLEDGE_DIR,
    NO_MANUSCRIPT_RULE_MARKER,
    PAPER_LIB_ABSTRACTS_DIR,
    PAPER_LIB_PDFS_DIR,
    REMOVED_AGENT_NAMES,
    dry_run_enabled,
)


PACKAGE_ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATES_DIR = PACKAGE_ROOT / "templates"
PROMPTS_DIR = PACKAGE_ROOT / "prompts"
AGENTS_DIR = PACKAGE_ROOT / "agents"


@dataclasses.dataclass
class Workspace:
    root: pathlib.Path
    research_md: research_md.ResearchMd

    @property
    def state_path(self) -> pathlib.Path:
        return self.root / "STATE.md"

    @property
    def claude_md_path(self) -> pathlib.Path:
        return self.root / "CLAUDE.md"

    @property
    def research_md_path(self) -> pathlib.Path:
        return self.root / "RESEARCH.md"

    @property
    def events_log_path(self) -> pathlib.Path:
        return self.root / EVENTS_LOG

    @property
    def knowledge_dir(self) -> pathlib.Path:
        return self.root / KNOWLEDGE_DIR

    @property
    def logs_dir(self) -> pathlib.Path:
        return self.root / "logs"

    def log_for(self, stage_name: str, attempt: int) -> pathlib.Path:
        ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        return self.logs_dir / f"stage_{stage_name}_a{attempt}_{ts}.log"

    @property
    def data_dir(self) -> pathlib.Path:
        return self.research_md.data_location

    @property
    def shared_cache_dir(self) -> pathlib.Path:
        """Project-wide dataset cache shared across OmiCairn runs.

        Resolution order:
        1. ``data.shared_cache`` override in RESEARCH.md (absolute path).
        2. Default: ``<project_root>/datasets/`` where ``project_root``
           is the grandparent of the workspace root (i.e. parent of the
           ``workspaces/`` dir). This keeps the cache alongside
           ``workspaces/`` so every workspace under the same root shares
           it.
        """
        override = self.research_md.shared_cache_override
        if override is not None:
            return override
        return self.root.parent.parent / "datasets"


def _render_template(src: pathlib.Path, dst: pathlib.Path, ctx: dict) -> None:
    text = src.read_text()
    for k, v in ctx.items():
        text = text.replace("{{" + k + "}}", str(v))
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(text)


def _template_ctx(rmd: research_md.ResearchMd) -> dict:
    ctx = {
        "topic_slug": rmd.topic_slug,
        "research_mode": rmd.research_mode,
        "domain": rmd.domain,
        "data_location": str(rmd.data_location),
        "research_question": rmd.research_question.strip(),
    }
    return ctx


def _copy_agents(root: pathlib.Path) -> None:
    agents_out = root / ".claude" / "agents"
    agents_out.mkdir(parents=True, exist_ok=True)
    if AGENTS_DIR.exists():
        for src in AGENTS_DIR.glob("*.md"):
            shutil.copy2(src, agents_out / src.name)


def sync_workspace_assets(ws: "Workspace") -> list[str]:
    """Bring a resumed workspace's agents and CLAUDE.md up to the current
    release: drop removed manuscript-pipeline subagents, refresh shipped
    agent files, and re-render CLAUDE.md when it predates the
    no-manuscript rule. Returns the removed agent file names."""
    agents_out = ws.root / ".claude" / "agents"
    removed: list[str] = []
    for name in REMOVED_AGENT_NAMES:
        p = agents_out / f"{name}.md"
        if p.exists():
            p.unlink()
            removed.append(p.name)
    _copy_agents(ws.root)
    claude_md = ws.claude_md_path
    if (not claude_md.exists()
            or NO_MANUSCRIPT_RULE_MARKER not in claude_md.read_text()):
        _render_template(
            TEMPLATES_DIR / "CLAUDE.md", claude_md,
            _template_ctx(ws.research_md),
        )
    return removed


def init(
    topic_slug: str,
    research_md_path: pathlib.Path,
    workspaces_root: pathlib.Path,
    skip_literature: bool = False,
) -> Workspace:
    rmd = research_md.load(research_md_path)
    # The RESEARCH.md topic_slug is authoritative.
    topic_slug = rmd.topic_slug

    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    root = (workspaces_root / f"{topic_slug}-{ts}").resolve()
    root.mkdir(parents=True, exist_ok=False)

    # Scaffold directories. Stage directories are created per-invocation
    # by the orchestrator and are NOT pre-created here.
    (root / "logs").mkdir(parents=True, exist_ok=True)
    (root / "literature" / PAPER_LIB_ABSTRACTS_DIR).mkdir(parents=True, exist_ok=True)
    (root / "literature" / PAPER_LIB_PDFS_DIR).mkdir(parents=True, exist_ok=True)
    (root / "literature" / "fulltext").mkdir(parents=True, exist_ok=True)

    # Shared dataset cache (project-wide, sibling of workspaces/). Created
    # here so the first workspace under a fresh root seeds it; subsequent
    # workspaces reuse it. Override path comes from RESEARCH.md
    # data.shared_cache (absolute).
    override = rmd.shared_cache_override
    shared_cache = override if override is not None else root.parent.parent / "datasets"
    shared_cache.mkdir(parents=True, exist_ok=True)

    stage.ensure_knowledge_scaffold(root)

    # Copy RESEARCH.md into the workspace verbatim.
    shutil.copy2(research_md_path, root / "RESEARCH.md")

    # Copy sibling omicairn_config.yaml (orchestration overlay) when
    # present. Without this copy the overlay would be parsed at init
    # time and then silently dropped on every subsequent `omicairn run`,
    # because `load_workspace` re-parses RESEARCH.md from the
    # workspace dir and `research_md.load()` looks for the overlay
    # in the same dir. Caught by code reviewer 2026-05-23 on
    # parkinson-paperless §3.4 commit 17fdc98.
    omicairn_config_src = research_md_path.parent / "omicairn_config.yaml"
    if omicairn_config_src.exists():
        shutil.copy2(omicairn_config_src, root / "omicairn_config.yaml")

    # Render CLAUDE.md + settings.json from templates.
    ctx = _template_ctx(rmd)
    _render_template(TEMPLATES_DIR / "CLAUDE.md", root / "CLAUDE.md", ctx)
    _render_template(
        TEMPLATES_DIR / "settings.json",
        root / ".claude" / "settings.json",
        ctx,
    )

    # Copy all subagent definitions.
    _copy_agents(root)

    # Initialise STATE.md with the new flat-stage schema and the
    # bounds block from RESEARCH.md.
    s = state.initial_state(
        rmd.topic_slug,
        max_iters_per_phase=rmd.max_iters_per_phase,
        max_backtracks=rmd.max_backtracks,
    )
    state.write(root / "STATE.md", s)

    # Write an init event to events.jsonl.
    stage.append_event(root, {
        "kind": EventKind.WORKSPACE_INIT,
        "topic_slug": rmd.topic_slug,
        "research_mode": rmd.research_mode,
        "domain": rmd.domain,
    })

    # Record the OmiCairn system version + datetime for this `init`
    # invocation (per-invocation reproducibility provenance). Appends
    # to provenance.jsonl + stamps STATE.md. Best-effort.
    from . import provenance
    provenance.record_invocation(root, command="init")

    # Asset-probe pre-flight. Scans RESEARCH.md for URLs/DOIs and
    # writes asset_probe.json into the workspace root. Best-effort,
    # network-bounded, suppressed under dry-run. Even when web is
    # disallowed we still emit the file (with status=skipped_no_web)
    # so downstream phases can render a deterministic summary.
    dry_run = dry_run_enabled()
    if not dry_run:
        try:
            from . import asset_probe
            probe_results = asset_probe.probe_research_md(
                research_md_path,
                allow_web=rmd.allow_web,
            )
            asset_probe.write_probe_json(root, probe_results)
        except Exception as e:
            # Probe failure is non-fatal — the pipeline can still run.
            (root / "asset_probe.json").write_text(
                '{"version": 1, "assets": [], "error": '
                + json.dumps(str(e)) + "}\n"
            )

    # Optional literature pre-fetch (best-effort, non-blocking).
    # Suppressed under dry-run so the development loop never hits the
    # network on a bare `omicairn init && omicairn run --dry-run` cycle.
    if not skip_literature and not dry_run:
        try:
            from . import literature
            literature.fetch(
                research_md=rmd,
                output_dir=root / "literature",
                max_papers=50,
                timeout_seconds=300,
            )
        except Exception as e:
            (root / "literature" / "README.md").write_text(
                f"# Literature Fetch\n\nFetch failed: {e}\n\n"
                "Phases will rely on model knowledge and WebSearch.\n"
            )
    elif dry_run:
        (root / "literature" / "README.md").write_text(
            "# Literature Fetch\n\nSkipped: OMICAIRN_DRY_RUN is set.\n"
        )

    return Workspace(root=root, research_md=rmd)


def load_workspace(root: pathlib.Path) -> Workspace:
    rmd = research_md.load(root / "RESEARCH.md")
    return Workspace(root=root.resolve(), research_md=rmd)
