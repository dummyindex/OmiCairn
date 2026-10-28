# How OmiCairn Works

> An end-to-end orientation map: what OmiCairn is, how a run proceeds, and
> where each subsystem lives. Each section links to the deeper document.

---

## 1. What OmiCairn is

**OmiCairn** is an accountable, self-learning multi-agent system for
hypothesis-driven biomedical multi-omics analysis that uses **Claude Code
as the agentic backbone**. Given a research question and data (a
`RESEARCH.md`), it runs a multi-phase pipeline — scoping, planning,
execution, cross-checking and analysis review — and records the work in a
per-workspace **knowledge graph** alongside the run's results, figures,
code and provenance. The field is parameterized via `RESEARCH.md` +
`domain_packs/`, never hardcoded.

OmiCairn performs analyses; it does **not** draft or format manuscripts,
by design. The deliverables of a run are analysis artifacts for an expert
reader: `results.json`, `analysis.md`, figures, code, cross-check findings,
`review_notes.md`, `negative_results.md` and the knowledge graph.

There is **no app, no UI, no database.** OmiCairn is a CLI-driven pipeline
whose artifacts are files in a workspace directory. The orchestrator
(Python, `engine/omicairn/`) is intentionally *thin*: it wires up
workspaces, launches `claude -p` sessions, and verifies their outputs.
The research reasoning lives inside the Claude Code sessions; the
orchestrator enforces contracts and gates around them.

---

## 2. The pipeline phases (as implemented)

The linear pipeline is **5 phases** in `constants.PIPELINE_ORDER` /
`phases.ORDER`. Four dispatch a single multi-turn `claude -p` agentic
session each; **CROSS_CHECK is pure Python** (no subprocess) and emits a
routing verdict. IDEATE is a **standalone** Claude-code stage — invoked on
demand, never auto-scheduled by the loop.

```
            (IDEATE — standalone front-stage, before SCOPE)
                          │ adopt direction
                          ▼
 SCOPE → PLAN → EXECUTE → CROSS_CHECK → REVIEW → DONE
 claude  claude  claude   (pure-Python)  claude
                          verdict gate   verdict: done | retry_execute |
                                         back_to_plan | back_to_scope | halt
```

| Phase | Kind | What it does | Module / prompt |
|-------|------|--------------|-----------------|
| **IDEATE** | standalone | Survey diverse sources → propose runnable research **directions** as `goal` nodes in the knowledge graph; `--adopt` promotes one to an active goal + sticky note. Not in `PIPELINE_ORDER`. | `cli.cmd_ideate`, `prompts/ideate.md` |
| **SCOPE** | claude | Understand the question, profile data, emit `goal.md`/`data_profile.md`. Confirmatory runs also emit `feasibility.json` (the SCOPE-time power gate). | `prompts/scope.md` |
| **PLAN** | claude | Literature, hypotheses, hypothesis-DAG, multiple-testing declaration, `figures_contract.json`, and the **preregistration** (`preregistration.json`) the W-gates key on. | `prompts/plan.md` |
| **EXECUTE** | claude | Write code, run it on real data, emit `results.json` + `analysis.md` + figures + `code/` (incl. `environment.lock.json`). May fetch public accessions on-the-way into the shared dataset cache. | `prompts/execute.md` |
| **CROSS_CHECK** | pure Python | Numeric sanity + the **W-gate** contract suite + preregistration sha256 drift + env-lockfile coverage. Emits `verdict.json` the orchestrator routes on (retry_execute / proceed / halt). | `omicairn/cross_check.py` |
| **REVIEW** | claude | Analysis-adequacy review: does the evidence answer the pre-registered question? Diagnoses nulls, checks coverage and baselines, triages figures-contract gaps, audits for fabrication, dispatches the `devils-advocate` subagent. Writes `review_notes.md` + `negative_results.md` and routes (`done` ends the run). | `prompts/review.md` |

Outputs land in **flat-numbered, immutable stage dirs** (`000_scope/`,
`001_plan/`, `002_execute/`, `003_cross_check/`, `004_review/`). Retries
create new dirs at the next index; prior stages are never overwritten.
`max_iters_per_phase` defaults to 100 (a safety ceiling; see
`docs/references/research-md-schema.md`), and the next retry's prompt receives
the prior verdict's feedback via `{{retry_note}}`.

Deeper: pipeline + data-flow diagrams and the orchestrator module table
are in **[ARCHITECTURE.md](ARCHITECTURE.md)**.

---

## 3. The W-gate roster (as shipped)

The W-gates are the structural cross-check gates the pipeline enforces
between stages (`omicairn/cross_check.py`). The **authoritative enumeration**
is `engine/omicairn/cross_check.py` — this is a summary.
Replay the corpus-replayable subset over any finalized workspace with
`omicairn audit --replay-gates [--all]`.

**ERROR-level (can block / route back):**

- **W13** — load-bearing-arm complete (the headline experiment actually ran).
- **W15** — load-bearing-arm provenance clean (no salvage-tagged headline).
- **W16** — no degenerate metric (AUC=0.5 / collapsed-CI) reported as a load-bearing result.
- **W17** — discriminating-arm resolution: the arm that adjudicates the central question is run, not recorded as "resolved" while deferred.
- **W18** — Lean proof-substance ENFORCED (reads `lean_verify.substance_warnings`; ERROR on an elementary headline theorem).
- **W20** — decision-rule HONORED: structured prereg rule vs `results.json`; ERROR only when a hypothesis is marked `supported` while its rule is violated.
- **W32** — parallel-arm contract re-validation (manifest invariants for within-EXECUTE parallel arms).

**WARN-only / advisory (never block progression to REVIEW):**

- **W14** — shared-protocol baseline awareness.
- **W15L** — latest-execute-stage provenance (multi-execute cross-stage fracture W13/W15 can't see).
- **W21** — prereg-completeness (names which of the four prereg fields a confirmatory plan omits).
- **W23** — decisive-arm zero-power (the `discriminating_arm` ran but had no power).
- **W24** — construct-validity (a load-bearing LEARNED/SURROGATE axis whose validity stat FAILS).
- **W25** — effect-clears-own-MDE (the load-bearing/headline effect clears the declared MDE).

**Notes / gotchas:**

- **W2–W10** are the lighter analysis-quality checks: W2 (pre-hoc power with a numeric MDE) and W5 (literal H1-decision annotation) are biomedical conventions that `_generic` opts out of; W7 (stratified sensitivity), W8 (viz-data contract), W9 (hypothesis-DAG + `fallback_if_blocked`) and W10 (multiple-testing correction declared) are always-on. Unlisted numbers (W1, W3, W4, W6, W11, W12, W19, W22, W26–W31) are unallocated or retired; do not reuse them.
- The replayed set is exactly `gate_replay.GATE_NAMES`: `lean_substance` + W13–W18, W20, W21, W23, W24, W25.
- **M3b / lean-substance** (`omicairn/lean_verify.py`) flags proof-theater (`trivial_pattern`, `elementary_theorem`, `linear_rearrangement`) — a compiled proof is necessary, not sufficient. W18 is its enforcement layer.
- **feasibility / SCOPE-power gate** (`omicairn/feasibility.py`) is upstream of CROSS_CHECK: it routes confirmatory underpowered runs `back_to_scope`/`back_to_plan` BEFORE PLAN locks a prereg. Neutral on result direction; warn-only when absent/invalid.

---

## 4. Key subsystems

- **Knowledge graph (the durable product).** Each workspace owns
  `knowledge/graph.jsonl` (append-only op-log; node types `goal` /
  `hypothesis` / `finding` / `result_ref` / `dataset_ref` / `metadata`).
  EXECUTE and REVIEW feed it; the 5-phase pipeline is the single-goal
  special case. Spec: `docs/design-docs/dynamic-hypotheses.md`; lab-wide
  distillation: `docs/design-docs/lab-knowledge-architecture.md`.
- **Negative results as a first-class output.** Every REVIEW stage writes
  `negative_results.md`: refuted, inconclusive and untestable hypotheses
  and dropped, deferred, failed or off-target arms, each with evidence
  paths. An honest null is a result, not a failure to be hidden.
- **Lab knowledge base + literature library.** Cross-run learning
  (`omicairn labkb`) and a cross-workspace paper/PDF/markdown cache of the
  literature a run reads (`omicairn paperlib`, default
  `<workspaces_root>/lab_library/`). See
  `docs/design-docs/lab-literature-library.md`.
- **Math-lab / `lab_theorems`** (optional) — a Lean-validated
  theorem library + retrieval + "virtual mathematician". Every entry's
  proof is checked sorry-free by the **local** Lean toolchain (validate,
  don't trust). CLI: **`omicairn labtheorems {list,search,retrieve,stats,graph,check,index,show}`**
  (the verb is `labtheorems`, not `mathlab`). The store is created on
  first use; module `omicairn/lab_theorems.py`. Not needed for biomedical runs.
- **Math-paper formalization** (optional) — formalize + Lean-proof-check the
  core theorems of landmark *published* math papers. This is Lean tooling,
  not manuscript review. CLI:
  **`omicairn paperreview {list,show,stats,review,index,graph,add}`**;
  module `omicairn/paper_review.py`; design
  `engine/docs/design-docs/math-paper-review.md`.
- **Serendipity-promotion** (creativity intake-valve, F1) — a post-run,
  distill-time loop that promotes supported+surprising on-the-fly
  hypotheses to proposed IDEATE directions. **OFF by default** behind
  `OMICAIRN_ENABLE_SERENDIPITY`; router untouched
  (`omicairn/lab_knowledge.promote_serendipitous_directions`).

---

## 5. How to run / steer / monitor

Setup (from repo root): `source .venv-omicairn/bin/activate` then
`cd engine && pip install -e .`. Claude Code authenticates as usual (an
existing `claude` login, `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN`
in `.env`, which is gitignored).

CLI verbs are defined in `engine/omicairn/cli.py`. The full list:

```
init  run  note  continue  steer  status  fleet  literature  paperlib
preflight  ideate  labkb  labtheorems  paperreview  feedback  project
inspect  reap  audit
```

Most common:

```bash
omicairn init  <slug> --from-research-md PATH [--workspaces-root DIR] [--skip-literature]
omicairn run   <ws> [--phase {all,scope,plan,execute,cross_check,review}]
omicairn status  <ws>                  # STATE.md summary
omicairn fleet [<root>]                # one-line-per-run table across a workspaces root (read-only)
omicairn inspect <ws> --phase <name>   # list a phase's output files
omicairn audit <ws> [--replay-gates]   # replay the cross-check gates (no LLM); --all for the cross-corpus sweep
omicairn reap <root|ws> [--apply]      # mark orphaned in_progress runs HALTED (dry-run default; never deletes)
```

**Steering is two steps** — a frequent trap:
`omicairn steer <ws> "<request>"` only **PLANS** the change (writes a steering
plan); `omicairn run <ws>` then **EXECUTES** it. Steer alone does nothing.
Always verify the generated plan is a canonical pipeline
(`execute → cross_check → review`) before running — the plan-gen LLM can
emit non-canonical pipelines. Steering steps name a pipeline phase
(usually scope / plan / execute / review) or an ad-hoc `free` step; a plan
naming an unknown phase halts cleanly, and `free` steps are instructed not
to draft manuscript text.

**IDEATE → SCOPE handoff:** `omicairn ideate <ws> --list` shows proposed
directions; `omicairn ideate <ws> --adopt <id>` promotes one to an active goal
and writes a sticky note that SCOPE/PLAN/EXECUTE read; then `omicairn run`.

---

## 6. Compute and operations

- **Compute catalog.** Each run resolves a named compute resource from the
  nearest `COMPUTATION_RESOURCES.md` above the research case (a local
  `default-local` entry is used when none is found). Remote GPU execution
  over SSH is supported (`compute.ssh_host`); check reachability with
  `omicairn preflight --check-remote <ssh-alias>`. Schema:
  `docs/references/computation-resources-schema.md`; a template is in
  `COMPUTATION_RESOURCES.example.md`.
- **Concurrency cap:** at most 25 concurrently-live runs per workspaces
  tree, enforced at launch (`constants.MAX_CONCURRENT_RUNS`, override via
  `OMICAIRN_MAX_CONCURRENT_RUNS`).
- **Long runs:** launch `omicairn run` detached from your terminal (for
  example with `setsid`/`nohup`) so a closed shell does not stop the driver;
  `omicairn status`/`omicairn fleet` report progress.

---

## 7. Where else to look

| If you need… | Read |
|--------------|------|
| Pipeline + data-flow diagrams, module table | **[ARCHITECTURE.md](ARCHITECTURE.md)** |
| RESEARCH.md input schema | **[references/research-md-schema.md](references/research-md-schema.md)** |
| Per-case configuration (`omicairn_config.yaml`) | **[references/omicairn-config-schema.md](references/omicairn-config-schema.md)** |
| Compute catalog schema | **[references/computation-resources-schema.md](references/computation-resources-schema.md)** |
| Design philosophy + core beliefs | **[DESIGN.md](DESIGN.md)** + `design-docs/core-beliefs.md` |
| Security notes | **[SECURITY.md](SECURITY.md)** |
