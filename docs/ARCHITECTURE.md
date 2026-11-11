# ARCHITECTURE.md — OmiCairn System Architecture

## System Overview

OmiCairn is an automated analysis system that uses Claude Code as the
agentic backbone. Given a research question (RESEARCH.md) and data, it
produces an auditable analysis — results, figures, code, provenance,
cross-check findings, an analysis review, a negative-results record and a
knowledge graph — through five pipeline phases: four autonomous Claude Code
agent sessions plus one synthetic pure-Python verification step
(CROSS_CHECK). Field specifics flow from RESEARCH.md + `domain_packs/`, not
from code. `omicairn audit --replay-gates` replays the cross-check gate
suite over finished workspaces without LLM compute.

OmiCairn does not draft or format manuscripts, by design: there is no
writing, claim-audit, peer-review or typesetting phase. Interpreting and
reporting the analyses is left to the human expert.

There is **no app, no UI, no database**. The system is a CLI-driven
pipeline whose artifacts are files in a workspace directory.

> **Direction (2026-05-28):** the durable product is a per-workspace
> **knowledge graph** of goals, hypotheses, findings, results, and
> metadata, with each stage's analysis artifacts as its evidence. A
> workspace is becoming a multi-goal, multi-hypothesis accumulator in
> which hypotheses can be raised on the fly. The five-phase pipeline
> below is the **single-goal special case** of that model. See
> § "Target: knowledge graph as the product" and
> `docs/design-docs/dynamic-hypotheses.md` for the generalization and
> its migration path. Core beliefs 11 & 12 state the principle.

## Dependency Direction

Dependencies flow in one direction. Upstream layers never import downstream:

```
RESEARCH.md (input)
    ↓
Orchestrator (omicairn/)
    ↓
Phases (phases.py) → Contracts (contracts.py) → State (state.py)
    ↓
Claude Runner (claude_runner.py) → Prompts (prompts/*.md)
    ↓
Agent Roles (agents/*.md) — injected into Claude Code sessions
    ↓
Workspace Artifacts (output)
```

Cross-cutting concerns (literature APIs, web access, logging) enter through
the orchestrator's tool allowlists and RESEARCH.md flags, never through
direct imports in phase logic.

## Pipeline Architecture

```
┌──────────────────────────────────────────────────────────┐
│  OmiCairn Orchestrator  (Python, omicairn/)              │
│  omicairn/cli.py → omicairn/orchestrator.py              │
└──┬───────┬──────────┬─────────────┬──────────────┬───────┘
   │       │          │             │              │
   ▼       ▼          ▼             ▼              ▼
 ┌─────┐ ┌────┐ ┌─────────┐ ┌─────────────┐ ┌────────┐
 │SCOPE│→│PLAN│→│ EXECUTE │→│ CROSS_CHECK │→│ REVIEW │→ DONE
 └─────┘ └────┘ └─────────┘ └─────────────┘ └────────┘
    │      │        │          (pure            │
 claude claude   claude        Python)        claude
   -p     -p       -p                           -p
```

Four phases dispatch `claude -p` sessions; CROSS_CHECK runs in pure
Python (no subprocess) and emits a verdict that the orchestrator uses to
retry EXECUTE or proceed to REVIEW. REVIEW is an analysis-adequacy review
that ends the run (`done`) or routes it back (`retry_execute`,
`back_to_plan`, `back_to_scope`, `halt`). Each Claude-dispatched phase is
a **single multi-turn Claude Code session**, not a chain of single-turn
API calls. This is the core architectural insight that distinguishes
OmiCairn from its predecessor (AutoResearchClaw).

## Phase Specifications

| Phase | Prompt | Required Outputs | Timeout | Purpose |
|-------|--------|------------------|---------|---------|
| SCOPE | `scope.md` | `goal.md`, `data_profile.md`, `summary.md`, `done.flag` | 45 min | Understand question, profile data |
| PLAN | `plan.md` | `literature.md`, `hypotheses.md`, `experiment_plan.md`, `figures_contract.json`, `preregistration.json`, `summary.md`, `done.flag` | 45 min | Literature, hypotheses, hypothesis-DAG (W9), MTC declared (W10), figures contract |
| EXECUTE | `execute.md` | `results.json`, `analysis.md`, `figures/legends.md`, `code/main.py`, `summary.md`, `done.flag` (+ optional `locked_model.json`, `data_fetch_log.md`, `code/environment.lock.json`) | 60 min | Write code, run on real data, capture env lockfile |
| CROSS_CHECK | (synthetic) | `verdict.json`, `findings.md`, `done.flag` | — | Numeric sanity + W-gate contract enforcement (W2–W25, W32; incl. W15L), preregistration sha256 drift, env lockfile coverage |
| REVIEW | `review.md` | `review_notes.md`, `negative_results.md`, `verdict.json`, `summary.md`, `done.flag` (+ optional `counter_critique.md`, `graph_updates.json`) | 45 min | Analysis-adequacy review: diagnose nulls, coverage and baseline gaps, figures-contract triage, anti-fabrication audit, negative-results record, routing decision |

All outputs are written inside flat-numbered stage directories
(`000_scope/`, `001_plan/`, `002_execute/`, `003_cross_check/`,
`004_review/`).
Retries create new stage dirs at the next index — prior stages are immutable.

`max_iters_per_phase` defaults to 100, a non-binding safety ceiling in
the default `dynamic` iteration mode (see
`docs/references/research-md-schema.md`). With
the retry_note plumbing fix that now passes prior verdict feedback
into the next stage's prompt, successive retries actually carry
actionable guidance and converge toward a clean cross-check and an
adequate analysis rather than repeating the same failed attempt.

### Standalone: IDEATE stage (brainstorm front-stage)

IDEATE is a **standalone front-stage** run *before* SCOPE, when the user
has a research **purpose** but not yet a concrete question. It is invoked
via `omicairn ideate <workspace>` and is **not** in `constants.PIPELINE_ORDER`
or `phases.ORDER` (it mirrors the off-pipeline FREE phase: a claude-code
phase the linear loop never auto-schedules).

What it does:
1. Surveys a **diverse** source set — papers + high-impact blogs +
   stated positions from academia / professors / Nobel-class laureates /
   philosophers / industry (provenance tagged via `SourceType`). Needs
   `allow_web: true`.
2. Writes them as `source` nodes in the knowledge graph and proposes
   research **directions** = `goal` nodes (`extra.kind="direction"`,
   `adopted=False`) with seed hypotheses (held in `extra` until adopted)
   and `cites` edges to the motivating sources — so every proposal is
   traceable to its provenance.
3. Emits `directions.json` (folded into the graph by
   `orchestrator._ingest_ideation_stage`) + a human-readable `sources.md`.

Steering / extensibility (how it triggers the downstream pipeline):
- `omicairn ideate <ws> --list` prints the proposed directions.
- `omicairn ideate <ws> --adopt <direction_id>` promotes a direction to an
  active goal (materializes its seed hypotheses as pre-registered
  hypothesis nodes) **and** writes a sticky user note. SCOPE/PLAN/EXECUTE
  read the adopted direction via BOTH the `{{user_notes}}` channel and
  the `{{knowledge_graph_projection}}` token (in scope.md/plan.md/execute.md),
  and proceed as a normal run. This is the IDEATE→SCOPE handoff, verified
  by `tests/test_ideate_e2e.py::test_adopt_steers_downstream_scope`.

Bibliography bridge (IDEATE → `literature/`): IDEATE's surveyed `source`
nodes are mapped to `Paper` entries and merged into the workspace's
`literature/papers.json` (+ `abstracts/`, + `LITERATURE_INDEX.md`, +
best-effort PDF download from source URLs) by
`literature.export_sources_to_literature`. This runs as a metadata-only
bridge in `orchestrator._ingest_ideation_stage` (every ideate pass) and
with PDF download in `cmd_ideate` when web is enabled, plus a re-sync on
`--adopt`. It closes the two-stores seam: the downstream PLAN literature
review inherits IDEATE's bibliography instead
of starting from an empty `literature/`. Merge is dedup-by-title/doi, so
it never clobbers a corpus a real `omicairn literature` fetch produced.

## Data Flow

```
Input:
  RESEARCH.md          — research question, domain, data pointers, config
  data/                — real datasets (CSV, TSV, etc.)

Per-stage outputs (flat-numbered, cumulative in workspace):
  000_scope/           — goal.md, data_profile.md
  001_plan/            — literature.md, hypotheses.md, experiment_plan.md,
                         figures_contract.json, preregistration.json
  002_execute/         — results.json, analysis.md, figures/, code/
                         (incl. code/environment.lock.json, Tier 1.3)
  003_cross_check/     — verdict.json, findings.md (synthetic, pure Python)
  004_review/          — review_notes.md, negative_results.md, verdict.json
                         (+ counter_critique.md, graph_updates.json)

  (retries create 005_execute/, 006_cross_check/, etc. — never overwritten)

State tracking:
  STATE.md             — YAML front-matter tracking phase status + attempts
  logs/                — per-stage Claude session logs
  knowledge/           — graph.jsonl (knowledge graph), journal.md,
                         lessons.jsonl (cross-stage learning)
  literature/          — papers.json, abstracts/, pdfs/ (fetched references)

Project-wide (sibling of workspaces/, shared across all runs):
  datasets/            — on-the-way dataset cache populated by EXECUTE;
                         keyed by <accession_or_slug>/; rendered to
                         prompts as {{shared_cache_dir}}. Override path
                         via RESEARCH.md data.shared_cache.

Each phase reads prior-stage outputs via {{stage_listing}} and
{{latest_<phase>_dir}} tokens to build on earlier work.
```

## Agent Layer

Sub-agents are role-specialized Claude Code instances spawned within
phase sessions. They are **not** separate processes — they are Claude
Code's `Agent` tool calls within a parent session.

```
Phase Session (claude -p)
  ├── reads: CLAUDE.md, RESEARCH.md, STATE.md, prior-phase outputs
  ├── uses tools: Read, Write, Edit, Bash(python3), Grep, Glob, ...
  └── spawns sub-agents (Agent tool):
       # auto — phase prompt explicitly dispatches:
       ├── literature-fetcher      (PLAN, EXECUTE — background)
       ├── code-reviewer           (EXECUTE — Tier 3.2)
       ├── compute-scheduling      (EXECUTE — long or GPU jobs)
       ├── math-formalization      (PLAN / EXECUTE — Lean claims)
       └── devils-advocate         (REVIEW — independent counter-critique
                                    of the analysis)

       # opt — spec available but not auto-dispatched (phase prompt
       # covers the work inline; the phase agent MAY invoke them
       # via Task on its own judgment):
       data-profiler, literature-scout, experiment-designer,
       analyst, code-writer, pi-advisor, steering-planner
```

Agent specs live in `engine/agents/*.md`. Each spec defines the
agent's role, constraints, expected outputs, and failure modes.
Sub-agent specifications live in `engine/agents/`.

## Orchestrator Internals

The orchestrator is intentionally **thin** — the Python codebase only
wires up workspaces, launches Claude sessions, and verifies outputs.
All research intelligence lives inside the Claude Code sessions.

| Module | Responsibility |
|--------|----------------|
| `cli.py` | CLI (init, run, note, continue, steer, status, fleet, literature, paperlib, preflight, ideate, labkb, labtheorems, paperreview, feedback, project, inspect, reap, audit) via argparse |
| `orchestrator.py` | Phase loop, verdict routing, cross-check dispatch, retry_note plumbing, knowledge-graph ingest |
| `phases.py` | Phase registry, tool allowlists, timeouts |
| `contracts.py` | Post-phase file existence + size verification |
| `constants.py` | Centralized constants (PhaseName, Decision, tokens, env vars) |
| `stage.py` | Stage dir management (StageRef, list_stages, latest_stage_of) |
| `state.py` | STATE.md read/write with YAML front-matter |
| `verdict.py` | Verdict data model (Verdict, write/read, retry/proceed/halt) |
| `workspace.py` | Workspace initialization from RESEARCH.md (also runs Tier-1.2 asset_probe) |
| `claude_runner.py` | Subprocess wrapper for `claude -p` with transient-failure retry, .env loader, session-cap auto-resume |
| `asset_probe.py` | HTTP-HEAD pre-flight for URLs/DOIs in RESEARCH.md (Tier 1.2) |
| `env_capture.py` | Capture active Python env into `code/environment.lock.json` (Tier 1.3) |
| `research_md.py` | RESEARCH.md parser (see `docs/references/research-md-schema.md`) |
| `compute_resources.py` | Compute-resource catalog parser + per-case resolution; renders `{{compute_resources}}` for EXECUTE/PLAN (see `docs/references/computation-resources-schema.md`) |
| `cross_check.py` | Semantic cross-artifact consistency + W-gate contract enforcement (W2–W25, W32; incl. W15L); `check_execute` |
| `gate_replay.py` | Read-only replay of the gate suite over finished workspaces (`omicairn audit --replay-gates`) |
| `domain_pack.py` | Domain pack loading (profile.yaml, per-phase guidance) |
| `literature.py` | Literature search API integration |
| `paper_library.py` | Lab literature library: cross-workspace paper cache (metadata + PDFs + converted markdown), visible `<workspaces_root>/lab_library/` default + `shared_lessons.jsonl` promotion store (Tier 3.3). See `docs/design-docs/lab-literature-library.md` |
| `user_notes.py` | `omicairn note` / `omicairn continue` once-and-persistent user instruction queues |
| `__init__.py` | Package init (version) |
| `__main__.py` | `python -m omicairn` entry point |

## Verification Layer

Two layers of post-phase verification:

1. **Contract verification** (`contracts.py`): Required files exist and
   are non-trivial (>32 bytes). Binary pass/fail → retry on failure.

2. **Cross-check verification** (`cross_check.py`): Semantic consistency
   across artifacts (e.g., results.json numbers match analysis.md claims).
   Runs after EXECUTE as the synthetic CROSS_CHECK stage that emits a
   routing verdict. REVIEW stages are verified by their file contract;
   REVIEW itself reads the cross-check findings and judges whether the
   analysis is adequate.

Failed verification triggers retry with diagnostic feedback now also
threaded into the next attempt's prompt via `{{retry_note}}` (Phase A,
2026-04-30) — the prior verdict's `proposed_fix`, `target_file_hints`,
and related hints are surfaced as a markdown block so the
retrying agent has structured guidance instead of starting over blind.

## Domain Pack Knobs

`domain_packs/<name>/profile.yaml` is the per-domain extension point for
`cross_check.py`. All keys are flat lists of strings (the tiny YAML-lite
parser does not support nested dicts). Values merge onto generic
defaults. Bundled packs: `_generic` (always-on default), `survival_prognostic`,
`scrna_seq` (Tier 3.4):

| Key | Consumed by | Effect |
|-----|-------------|--------|
| `baseline_name_hints` | `baseline_name_hints(domain)` | Extra substrings that classify a results.json condition as a baseline |
| `ci_skip_paths` | `ci_skip_patterns(domain)` | Dotted paths skipped by the CI-walk |
| `metric_bounds_0_1` / `metric_bounds_neg1_1` / `metric_bounds_nonneg` | `metric_bounds_*(domain)` | Extra metric names allowlisted for the corresponding numeric range |
| `enforce_execute_checks` | `enabled_execute_checks(domain)` | Subset of `{w2, w5, w32}` to run during CROSS_CHECK (W32 is on by default) |

W2 and W5 are biomedical analysis conventions (pre-hoc power with numeric
MDE, literal H1-decision annotation); `_generic` opts out by default to
avoid false-positives on non-biomedical work. W7 (stratified
sensitivity), W8 (viz-data contract), W9 (hypothesis-DAG +
fallback_if_blocked, Tier 2.1) and W10 (multiple-testing correction
declared, Tier 2.2) are always-on because they silent-skip when their
triggers are absent.

### Figures contract (PLAN-authored, REVIEW-triaged)

`001_plan/figures_contract.json` is the structured figure manifest
PLAN emits alongside its prose plan. Schema: `figures[]` (each with
`id`, `title`, `required`, `panels`, `output_paths`, `results_tables`)
plus `deferred[]`. EXECUTE reads it as a production target; it writes
every produced figure to the declared `output_paths`, and for any
planned figure it could not produce it documents the reason under
`## Limitations and deviations from the figures contract` in
`analysis.md`.

The contract is **soft** by design — a reference target, not a hard
gate. Science can legitimately fail to produce a planned figure
(method failure, absent data, infeasibility under budget), and
`analysis.md` should disclose that honestly rather than fabricate it.
The judgement of whether a gap is a disclosed limitation, an undisclosed
limitation, or a producible-but-skipped figure belongs to the REVIEW
phase (`prompts/review.md`, "Figures-contract triage"), which reads the
plan, the execute outputs and the cross-check findings together.
CROSS_CHECK surfaces each gap as an
informational warning via `_check_figures_contract`; it never flips
the execute verdict to `retry_execute` on figure-contract gaps
alone. Only REVIEW can decide that a gap is producible-but-skipped
and emit a `retry_execute` verdict with a specific `proposed_fix`.

### Shared dataset cache (on-the-way fetching)

Experiments sometimes need inputs that are not pre-staged under
`RESEARCH.md: data.location` (public accessions, reference gene
sets, pre-trained weights). Instead of forcing the human to fetch
these before `omicairn init`, OmiCairn supports **on-the-way fetching**:
EXECUTE pulls them from canonical public URLs into a shared
dataset cache at `<project-root>/datasets/<accession_or_slug>/`.

The cache lives **outside** any individual workspace so neighbouring
runs reuse the same downloads. Path override: `data.shared_cache`
(absolute) in RESEARCH.md. Rendered as `{{shared_cache_dir}}` into
SCOPE/PLAN/EXECUTE prompts.

Mechanism (domain-agnostic, in `prompts/execute.md` § *On-the-way
data fetching*): Python stdlib `urllib.request.urlretrieve` →
sha256 verify → atomic rename from `<name>.part` → append-only
`<stage_dir>/data_fetch_log.md`. Gated by `budgets.allow_web`
(skip entirely when `false`). Failures are documented under
`analysis.md § Limitations`, never fabricated; `cross_check`
surfaces them as informational warnings via `_check_data_fetch`
but never flips the execute verdict.

The specific accessions/URLs to fetch are declared by PLAN in
`experiment_plan.md § Data fetch plan` based on the research
brief — the infrastructure itself is domain-agnostic.

## Target: knowledge graph as the product

> **Status: target architecture, partially built.** This section
> describes where OmiCairn is going (core beliefs 11 & 12). The pipeline
> documented above is the **single-goal special case**.
> The spec lives in `docs/design-docs/dynamic-hypotheses.md`; this is
> the architecture summary.
>
> For the **end-to-end dataflow diagram** (one run + the cross-run
> compounding loop) and how the per-run graph distills into the
> **lab knowledge base** that feeds future runs, see
> `docs/design-docs/lab-knowledge-architecture.md` (keep it in sync when
> the pipeline or knowledge stores change). For the **lab literature
> library** (cross-run paper/PDF/markdown sharing — the literature
> analogue of the lab KB), see
> `docs/design-docs/lab-literature-library.md`.

**The object that accumulates is a knowledge graph.** Each
workspace owns a graph persisted at `<ws>/knowledge/graph.jsonl` (an
append-only op-log of node/edge upserts, folded to current state on
read — same JSONL discipline as `events.jsonl`, `provenance.jsonl`,
`shared_lessons.jsonl`; no database).

Node types:

| Node | Holds | Written by |
|------|-------|-----------|
| `goal` | a research goal (a workspace has ≥1) | SCOPE / RESEARCH.md `goals[]` |
| `hypothesis` | a testable statement under a goal; lifecycle + origin | PLAN (preregistered) **and** EXECUTE/REVIEW (on-the-fly, via `graph_updates.json`) |
| `finding` | an observation grounded in ≥1 evidence_path | EXECUTE, REVIEW |
| `result_ref` | pointer into a `results.json` key + value | EXECUTE |
| `dataset_ref` | accession / path / version of an input | SCOPE / EXECUTE |
| `metadata` | wet-lab / provenance / environment record | any phase |

Edge types: `tests` (hypothesis→result), `supports` / `refutes`
(evidence→hypothesis), `derived_from` (node→node), `raises`
(finding→hypothesis, the on-the-fly path), `cites` (node→paper DOI).

**Hypothesis lifecycle.** Each `hypothesis` node carries `origin`
(`preregistered` | `on_the_fly`), `status` (`proposed` → `testing` →
`supported` / `refuted` / `inconclusive` / `superseded`), and a
`posthoc` flag. On-the-fly hypotheses are recorded with the same rigor
as pre-registered ones and explicitly flagged post-hoc so
multiple-testing accounting stays honest (W10 reads the post-hoc count).

**Phases read slices of the graph.** Prompts receive a projection of
the graph (`{{knowledge_graph_projection}}`: goals, hypotheses and their
current status with evidence) so each phase builds on what is already
known. One run can validate or refute many hypotheses across goals
without re-running the science that settled earlier ones.

**Single-goal pipeline = special case.** A classic RESEARCH.md with one
`research_question` and a fixed `hypotheses.md` maps to: one `goal`
node, N `preregistered` `hypothesis` nodes, zero on-the-fly additions.
No behavior changes for existing cases; the graph is written alongside
the current artifacts and is purely additive.

## Key Design Decisions

1. **Multi-turn sessions over single-turn calls**
2. **Contract-driven phase boundaries** — Phases are decoupled; the only coupling is through file artifacts
3. **Tool allowlists per phase** — web tools are filtered out of every phase when RESEARCH.md sets `allow_web: false`
4. **Domain-agnostic infrastructure** — All domain knowledge flows from RESEARCH.md, never hardcoded
5. **Workspace isolation** — Each run gets its own directory; no shared mutable state between runs. *(Evolving: a workspace stays isolated, but now accumulates a multi-goal/multi-hypothesis knowledge graph rather than a single fixed question — see § "Target: knowledge graph as the product".)*
