# Feasibility / SCOPE-power gate

> Summary stub. The authoritative implementation is `engine/omicairn/feasibility.py`
> (schema, applicability, routing) and `engine/prompts/scope.md` (the power
> judgment). This doc records *what* the gate does and *why*; it is referenced from
> `omicairn/feasibility.py`, `omicairn/research_md.py`, `omicairn/phases.py`, and `omicairn/constants.py`.

## Context

OmiCairn once ran a pre-registered confirmatory replication on 52 and 27 cells against an
original cohort of 22,048, faithfully producing an underpowered null whose true cause was
its own data-handling — instead of catching the infeasibility upstream at data-profiling.
A real PI catches that in minutes. The feasibility gate is the mechanical half of that fix.

## Decision

Run a **SCOPE-time** (not CROSS_CHECK) statistical-power / data-feasibility rail.

- SCOPE emits `feasibility.json` (`FEASIBILITY_FILE`) profiling, per planned analysis unit,
  the **actually extractable N** vs the reference effect's requirement, plus an overall
  verdict and a recommended remediation.
- The **power judgment is the model's** (prompt-driven, `prompts/scope.md`). The harness only
  (1) defines + validates the JSON-only schema, (2) decides whether the gate *applies* to the
  run, and (3) maps the model's `recommended_action` onto an orchestrator routing `Decision`.
- Verdicts: `feasible` → proceed; `underpowered` → `back_to_scope`/`back_to_plan`;
  `needs_data` → route back — all **before** PLAN locks a pre-registration.

## Applicability

Default `applies_to: confirmatory` (METHOD mode or a required pre-registration). Exploratory
runs are exempt, so they are never pushed to over-profile. A missing or invalid
`feasibility.json` is **warn-only** and never blocks; legacy SCOPE stages contract-pass
unchanged.

## Neutrality (core belief 13, non-negotiable)

The gate keys **only** on statistical power / data feasibility, **never** on the hypothesised
direction or sign of any result. A well-powered NULL passes cleanly. The gate's job is to stop
under-powered runs, not to chase positives.

## See also

- `engine/omicairn/feasibility.py` — schema, applicability, routing
- `engine/prompts/scope.md` — the power-judgment prompt
- `FEASIBILITY_FILE`, `FeasibilityVerdict`, `FEASIBILITY_*` in `omicairn/constants.py`
- Routed in `orchestrator._process_feasibility_gate`
