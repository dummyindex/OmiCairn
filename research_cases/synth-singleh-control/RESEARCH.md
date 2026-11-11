---
research_mode: DISCOVERY
domain: synthetic_smoke_test
topic_slug: synth-singleh-control

research_question: |
  SMOKE / DEMONSTRATION ONLY — not for publication. On the shared
  deterministic synthetic dataset (synth-dynamic-demo/data), is
  `feature1` prognostic for the binary `outcome` (held-out AUC > 0.6,
  95% CI excluding 0.5)?

  This is the SINGLE-HYPOTHESIS CONTROL arm of the dynamic-hypotheses
  demonstration (docs/design-docs/dynamic-hypotheses.md §5). It
  declares exactly one hypothesis and DISABLES on-the-fly hypothesis
  registration, so it represents the legacy "one headline claim per
  run" behavior. By construction it cannot capture the secondary
  feature2×group-B interaction the data also contains — that is the
  point of the comparison.

goals:
  - id: g1
    question: "Is feature1 prognostic for outcome?"
    hypotheses:
      - "H1: feature1 is prognostic for outcome (held-out AUC > 0.6)"

# Single-hypothesis control: the orchestrator passes max_on_the_fly=0,
# so no exploratory hypotheses are registered even if EXECUTE surfaces
# them.
allow_on_the_fly_hypotheses: false

data:
  location: ./research_cases/synth-dynamic-demo/data
  description: "Shared synthetic 4-feature + group binary classification, 120 train / 80 holdout"
  cohorts: [train, holdout]
  modalities: [synthetic_tabular]

success_criteria:
  primary_metric: auc
  baseline: random_null
  pre_registration: required
  honest_validation: required
---

# synth-singleh-control — single-hypothesis control arm

Not a real research case. See `docs/design-docs/dynamic-hypotheses.md`
§5 and the sibling `synth-dynamic-h` case. Orchestration knobs live in
`omicairn_config.yaml`.
