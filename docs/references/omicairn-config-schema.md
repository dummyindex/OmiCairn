# `omicairn_config.yaml` schema reference

`omicairn_config.yaml` is the **per-case orchestration config**. It lives
alongside `RESEARCH.md` in a test case directory (or production case
directory) and overlays its values onto the matching `RESEARCH.md`
fields at workspace-init time.

## Why a separate file?

- `RESEARCH.md` is the **semantic research brief**: research question,
  hypotheses (H1–H5), pre-registration thresholds, planned figures,
  data location/cohorts/modalities, success criteria. This is
  human-authored, slow-changing, and read by the OmiCairn agent as part
  of every phase prompt.
- `omicairn_config.yaml` is the **operational orchestration config**:
  per-phase wall budgets, retry caps, claude model, allow_web,
  auto_approve, shared dataset cache. This is machine-tunable,
  fast-changing, and consumed by the orchestrator (never by the
  agent).

Separating the two has three concrete benefits:

1. **Per-phase wall budgets** become expressible (e.g. EXECUTE = 6h,
   SCOPE = 1h). Previously a single `max_phase_wall_minutes` applied
   to every phase identically — the parkinson 15-case suite lost the
   `asyn-propagation` run because EXECUTE retried into a 4h ceiling
   that was reasonable for SCOPE/PLAN but tight for heavy single-cell
   work.
2. **The agent doesn't read operational config it shouldn't act on.**
   max_phase_budget_usd, max_iters_per_phase, etc. are orchestrator
   contracts, not scientific commitments.
3. **Re-tuning runs without editing the brief.** Want to give a case
   more EXECUTE time? Edit omicairn_config.yaml; RESEARCH.md stays
   immutable.

## Backward compatibility

`omicairn_config.yaml` is optional. When absent, all orchestration
values come from `RESEARCH.md`'s `budgets:` / `bounds:` /
`auto_approve:` / `data.shared_cache:` blocks (the historical layout).
When present, its values OVERRIDE the matching `RESEARCH.md` fields.
Keys not set in `omicairn_config.yaml` fall back to `RESEARCH.md`.

The only new field with no `RESEARCH.md` counterpart is
`budgets.max_phase_wall_minutes_by_phase`.

## Schema

```yaml
budgets:
  # Case-wide per-phase wall ceiling, in minutes. Applies to any
  # phase NOT listed in max_phase_wall_minutes_by_phase below.
  max_phase_wall_minutes: 240

  # Total pipeline wall ceiling, in minutes. _run_all_locked breaks
  # the outer stage loop once cumulative wall has exceeded this.
  # Set 0 / negative to disable.
  max_total_wall_minutes: 1440

  # NEW: stratified per-phase wall budgets, in minutes. Override the
  # case-wide value for the named phase only. Recognised phase names
  # are the lowercase pipeline phase identifiers:
  #   scope, plan, execute, cross_check, review
  # Phases not listed inherit `max_phase_wall_minutes`.
  max_phase_wall_minutes_by_phase:
    execute: 360    # 6 h for heavy single-cell / multi-modal work
    review: 60

  # Claude API spend cap per phase, in USD.
  max_phase_budget_usd: 25.0

  # Whether WebFetch + WebSearch are in the per-phase tool allowlist.
  # Cases that rely on accession fetches need this true.
  allow_web: true

  # Claude model identifier (passed to `claude -p --model`).
  # Default when omitted: claude-opus-4-8 (latest, most
  # capable). Test cases may pin claude-sonnet-4-6 for cheaper iteration.
  claude_model: claude-opus-4-8

bounds:
  # Per-phase retry cap. Counts CONTRACT_FAIL retries (§3.1) and any
  # other within-phase iterations. Default 5.
  max_iters_per_phase: 5

  # Bound on cross-phase back_to_plan / back_to_scope loops. Default 5.
  max_backtracks: 5

# Auto-proceed flag. Currently dormant; reserved for future
# human-confirm gates (e.g. SCOPE-then-confirm before EXECUTE).
auto_approve: true

# Shared dataset cache override. Absolute or relative to omicairn_config.yaml.
# When omitted, defaults to <parent-of-workspaces-root>/datasets/.
shared_cache: ./datasets

# OPTIONAL compute-resource declaration. Names a hardware envelope from the
# repo-root COMPUTATION_RESOURCES.md catalog. Resolved at init and rendered
# into EXECUTE/PLAN as {{compute_resources}}. Omit to default to the
# CPU-only `default-local` resource. Full schema + resolution semantics:
# docs/references/computation-resources-schema.md.
compute:
  resource: gpu-host-1
  # optional inline overrides (rarely needed):
  # max_runtime_hours: 12
```

The `compute:` block and `budgets.max_phase_wall_minutes_by_phase` are the
two fields with no historical RESEARCH.md counterpart (though `compute:`
may also be placed in RESEARCH.md as a fallback, like budgets/bounds).

## How it works

At workspace-init time, `research_md.load(path)`:

1. Parses `RESEARCH.md` as YAML (handles markdown front-matter).
2. Looks for a sibling `omicairn_config.yaml` in the same directory.
3. If present, parses it and overlays each present key onto the
   matching `RESEARCH.md` field.
4. Returns a `ResearchMd` dataclass with the merged values.

The orchestrator's `_run_llm_stage` looks up the per-phase budget
as: `max_phase_wall_minutes_by_phase.get(phase_name)` →
`max_phase_wall_minutes` → `phase.timeout_seconds` (built-in default).

## Migration

For an existing test case:

1. Inspect the `budgets:` / `bounds:` / `auto_approve:` /
   `data.shared_cache:` blocks of its `RESEARCH.md`.
2. Write a `omicairn_config.yaml` next to `RESEARCH.md` with the same
   values (verbatim — no semantic change is intended by the
   migration itself).
3. Optionally add `max_phase_wall_minutes_by_phase` to tune
   per-phase budgets.
4. Leave `RESEARCH.md` unchanged (the same values now serve as
   fallbacks).

Validation:

```bash
/path/to/omicairn/python -c "
from omicairn.research_md import load
rm = load('research_cases/<slug>/RESEARCH.md')
print(rm.max_phase_wall_minutes_by_phase)
print(rm.max_phase_wall_minutes, rm.max_total_wall_minutes)
"
```

## See also

- Parser: `engine/omicairn/research_md.py`
- Orchestrator hook: `engine/omicairn/orchestrator.py::_run_llm_stage`
- Tests: `engine/tests/test_omicairn_config_yaml.py` and
  `tests/test_orchestrator_error_paths.py::test_per_phase_budget_overrides_case_wide_via_omicairn_config_yaml`
- RESEARCH.md schema (semantic brief): `docs/references/research-md-schema.md`
