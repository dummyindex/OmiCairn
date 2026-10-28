# RESEARCH.md Schema Reference

`RESEARCH.md` is the **single human-authored input file** for a OmiCairn run.
It encodes the research question, data pointers, success criteria, and
resource budgets. The orchestrator parses it via `engine/omicairn/research_md.py`.

## Format

A YAML document. May optionally be wrapped in Markdown front-matter
(`---` fences) — the parser strips the fences before parsing the YAML body.

## Required fields

| Field | Type | Description |
|-------|------|-------------|
| `topic_slug` | string | Short identifier used as workspace prefix (e.g., `ferroptosis-hcc`) |
| `research_question` | string | Free-text research question the OmiCairn run will answer |
| `data.location` | path | Absolute or home-relative path to the `data/` directory. Supports `$ENV_VAR` and `~` expansion |

## Optional fields (with defaults)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `research_mode` | `METHOD` / `DISCOVERY` / `HYBRID` | `METHOD` | Shapes phase prompts |
| `domain` | string | `general` | Research domain label (free-text, e.g., `cancer_genomics`) |
| `data.description` | string | `""` | Free-text description of the datasets |
| `data.cohorts` | list[string] | `[]` | Named cohorts (e.g., `["TCGA-LIHC", "METABRIC"]`) |
| `data.modalities` | list[string] | `[]` | Data modalities (e.g., `["RNA-seq", "clinical"]`) |
| `data.shared_cache` | path \| null | `null` | Absolute path to a project-wide dataset cache shared across OmiCairn runs. When null, defaults to `<parent-of-workspaces-root>/datasets/`. Rendered to prompts as `{{shared_cache_dir}}`; EXECUTE fetches on-the-way public data into `<shared_cache_dir>/<accession_or_slug>/`. |
| `success_criteria.primary_metric` | string | `unspecified` | Headline metric (e.g., `c_index`, `AUC`) |
| `success_criteria.baseline` | string \| null | `null` | Baseline to beat (e.g., `"clinical-only Cox model"`) |
| `success_criteria.pre_registration` | `required` / `optional` | `optional` | If `required`, PLAN must produce `preregistration.json` |
| `success_criteria.honest_validation` | `required` / `optional` | `optional` | If `required`, forbids retraining on test set |
| `budgets.max_phase_wall_minutes` | int | `60` | Soft per-phase wall time budget |
| `budgets.max_total_wall_minutes` | int | `180` | Soft total wall time budget |
| `budgets.max_phase_budget_usd` | float \| null | `null` (unlimited) | Claude API spend cap per phase. **Default unlimited** per owner directive 2026-06-27 ("set cap to unlimited for ALL of our research runs"): when unset, the runner omits `--max-budget-usd` and no phase self-halts on spend. Set a number to re-impose a finite per-phase cap. Single source: `DEFAULT_RUN_BUDGET_USD` in `constants.py`. |
| `budgets.allow_web` | bool | `true` | If `false`, strips `WebFetch`/`WebSearch` from all phase allowlists |
| `budgets.claude_model` | string | `claude-opus-4-7` | Model ID passed to `claude -p --model`. Test cases override to `claude-sonnet-4-6` for cheaper iteration during development. |
| `auto_approve` | bool | `true` | If `false`, agent must request human approval before risky actions |
| `bounds.max_iters_per_phase` | int | `100` | Per-phase iteration ceiling. **Default raised 5 → 100** per owner directive 2026-07-01 ("by default don't put any limit on those agents; let agents unleash their power" — #133). In `dynamic` iter-mode (default) this is a **non-binding safety ceiling**, not a science-shaping cap: a healthy phase is terminated by its real stop-condition (cross_check proceed / review done) and only a genuine zero-progress spin (see `stage_iter_mode`) is halted. Pin a small number to enforce a hard deterministic cap. Single source: `DEFAULT_MAX_ITERS_PER_PHASE` in `constants.py`. The reconcile-upward seam bumps an in-flight run's stale cap to the config value on relaunch (never lowers). |
| `budgets.stage_iter_mode` | string | `dynamic` | `dynamic` (default) demotes `max_iters_per_phase` to a non-binding safety ceiling + activates the zero-progress spin-catcher (`ZERO_PROGRESS_STALL_LIMIT` = 3 consecutive re-opening iterations emitting an identical verdict fingerprint → halt as non-convergence). `hard` keeps the configured cap as an unconditional deterministic terminator. Env `OMICAIRN_STAGE_ITER_MODE` overrides per-invocation; unrecognised values degrade to `dynamic`. Sibling axis to `budgets.stage_time_limit_mode` (time vs iterations); the two are orthogonal and both default `dynamic`. Single source: `constants.stage_iter_mode`. |
| `bounds.max_backtracks` | int | `5` | Bound on `back_to_plan` / `back_to_scope` cross-phase loops |

## Minimal example

```yaml
topic_slug: smoke-tiny
research_question: |
  Does feature X predict outcome Y in the smoke-tiny synthetic dataset?
data:
  location: ~/OmiCairn/research_cases/smoke-tiny/data
```

## Realistic example

```yaml
research_mode: METHOD
domain: cancer_genomics
topic_slug: brca-honest-validation
research_question: |
  Does a 17-gene prognostic signature learned by LASSO-Cox on TCGA-BRCA
  generalise to METABRIC under honest external validation?
data:
  location: ~/OmiCairn/research_cases/brca-honest-validation/data
  description: TCGA-BRCA discovery + METABRIC external validation
  cohorts: [TCGA-BRCA, METABRIC]
  modalities: [RNA-seq, clinical]
success_criteria:
  primary_metric: c_index
  baseline: clinical-only Cox model
  pre_registration: required
  honest_validation: required
budgets:
  max_phase_wall_minutes: 45
  max_total_wall_minutes: 240
  allow_web: true
  claude_model: claude-sonnet-4-6
  max_phase_budget_usd: 15.0
```

## Validation

`research_md.load()` raises `ValueError` if any required field is missing.
Unknown fields are preserved in the `raw` dict on the parsed object but
are not used by the orchestrator. The parser is intentionally lenient so
that domain-specific extensions (e.g., cohort-specific filters) can be
added without schema changes.

## Data fetching

When the experiment needs inputs that are NOT already present under
`data.location`, the EXECUTE phase can fetch them on-the-way into the
shared dataset cache. This keeps workspaces small, survives re-runs,
and lets neighbouring workspaces under the same project root reuse
the download instead of re-fetching.

**Cache location.** Either `data.shared_cache` (absolute path) in
RESEARCH.md, or — when omitted — `<parent-of-workspaces-root>/datasets/`
by default. The token `{{shared_cache_dir}}` in all phase prompts
resolves to this path.

**Fetch protocol.** Domain-agnostic; fully described in
`engine/prompts/execute.md` § *On-the-way data fetching*.
EXECUTE uses Python stdlib (`urllib.request.urlretrieve`,
`hashlib.sha256`), writes to `<name>.part` then renames atomically,
verifies sha256 where an upstream hash is published, and appends one
line per fetched file to `<stage_dir>/data_fetch_log.md`.

**Gate.** No fetching happens when `budgets.allow_web` is `false` —
missing inputs are listed instead under the plan's
`## Figures deferred` subsection and the analysis falls back to
what is locally available.

**Informational warning.** `cross_check` parses `data_fetch_log.md`
and surfaces any recorded failure (HTTP 4xx/5xx, exception trace,
literal `FAILED`) as an informational warning so REVIEW can verify
the gap is disclosed under `analysis.md § Limitations` and recorded in
`negative_results.md`. A failed
fetch never flips the execute verdict to `retry_execute` — it is
treated as a legitimate scientific limitation to be disclosed
honestly, not a code-level bug.

## Deprecated fields

`target_venue`, `paper_author` and `citation_gate` belonged to the
removed manuscript phases. They are ignored with a `FutureWarning` when
present, as is `depth.citation_target` in `omicairn_config.yaml`; delete
them from your cases.

## See also

- Parser implementation: `engine/omicairn/research_md.py`
- Usage in orchestrator: `engine/omicairn/orchestrator.py` (`_render_prompt`)
- Phase definitions: `engine/omicairn/phases.py`
