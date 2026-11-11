---
research_mode: DISCOVERY
domain: synthetic_smoke_test
topic_slug: synth-dynamic-h

research_question: |
  SMOKE / DEMONSTRATION ONLY — not for publication. On the shared
  deterministic synthetic dataset (synth-dynamic-demo/data), what
  features are prognostic for the binary `outcome`, and does any
  predictor's effect depend on subgroup (`group`)?

  This is the DYNAMIC-HYPOTHESES arm of the demonstration
  (docs/design-docs/dynamic-hypotheses.md §5). It declares the same
  primary hypothesis as the control, plus a second discovery goal, and
  ENABLES on-the-fly hypothesis registration. The dataset contains a
  real secondary signal — feature2 is prognostic only within group B —
  that an honest discovery analysis should surface as a NEW hypothesis
  during EXECUTE, test, and record in the knowledge graph. The result is
  the whole graph (multiple tested hypotheses), not a single headline
  claim.

goals:
  - id: g1
    question: "Is feature1 prognostic for outcome?"
    hypotheses:
      - "H1: feature1 is prognostic for outcome (held-out AUC > 0.6)"
  - id: g2
    question: "What else drives outcome, and is any effect subgroup-dependent?"
    hypotheses:
      - "H2: feature2 is prognostic for outcome overall"

# The lever under test: ON. EXECUTE may register up to 5 on-the-fly
# hypotheses (e.g. the expected 'feature2 is prognostic in group B
# only' interaction) grounded in on-disk evidence.
allow_on_the_fly_hypotheses: true
max_on_the_fly_hypotheses: 5

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

# synth-dynamic-h — dynamic-hypotheses arm

Not a real research case. See `docs/design-docs/dynamic-hypotheses.md`
§5 and the sibling `synth-singleh-control` case. Orchestration knobs
live in `omicairn_config.yaml`.

Expected divergence from the control: this workspace's knowledge graph
should end with more hypotheses tested (the H2 overall effect is weak
because feature2's signal is diluted across groups; the on-the-fly
"feature2 in group B only" hypothesis is the real find), at least one
`on_the_fly` hypothesis, and more findings — without violating the
honesty rule (every supported hypothesis cites on-disk evidence).
