---
research_mode: METHOD
domain: cancer_genomics
topic_slug: brca-honest-validation

research_question: |
  Does a 17-gene mitoxyperilysis prognostic signature, learned by
  LASSO-Cox on TCGA-BRCA, generalise to METABRIC under honest
  external validation? Specifically: does the locked-coefficient
  signature's concordance index on METABRIC exceed both 0.60 and
  the Venet (2011) tumor-grade-only baseline, with a 95% CI
  excluding the grade-only floor?

  Secondary question: if the full-panel LASSO signature fails
  external validation, do any individual panel members retain
  nominally significant univariate prognostic value in the basal
  and claudin-low METABRIC stratum, and is the direction of effect
  consistent with the mechanistic hypothesis (innate-immune /
  pyroptosis receptors as protective)?

  The 17-gene panel is:
  MTOR, RPTOR, RICTOR, BAX, BAK1, BID, RHOA, NLRP3, TNFRSF1A,
  TNFRSF1B, MYD88, NFE2L2, TLR2, TLR4, TLR7, TLR8, NINJ1.

data:
  location: ./test-research-runs/researchclaw_run/data
  description: "Pre-fetched TCGA-BRCA and METABRIC cBioPortal exports: expression, clinical, survival"
  cohorts: [tcga_brca, metabric]
  modalities: [transcriptomic, clinical]

success_criteria:
  primary_metric: concordance_index
  baseline: venet_grade_only
  pre_registration: required
  honest_validation: required

budgets:
  max_phase_wall_minutes: 55
  max_total_wall_minutes: 300
  allow_web: true
  claude_model: claude-sonnet-4-6
  max_phase_budget_usd: 12.0

auto_approve: true
---

# brca-honest-validation — OmiCairn integration test on real data

This test case reuses the real TCGA-BRCA and METABRIC data that was
pre-fetched for the AutoResearchClaw run. It is the canonical
integration test for OmiCairn: a real biomedical question, real
data, a known-difficult signal (see `mitoxy_brca_result.md` memory).

The purpose is to verify that OmiCairn can, autonomously:

1. Profile both cohorts and identify the 17-gene panel columns.
2. Design a pre-registered LASSO-Cox experiment with an honest Venet
   baseline.
3. Fit on TCGA, lock coefficients with SHA-256, evaluate once on
   METABRIC.
4. Produce an honest analysis that calls a negative result a
   negative result (if the signature fails externally, say so).
5. Report every number in `analysis.md` and `results.json`, traceable
   to the code that produced it, and record any negative result in
   `negative_results.md`.

We already know from the prior manual run what the "right" answer
looks like: the full-panel LASSO fails external transfer
(c ≈ 0.51 on METABRIC vs a grade baseline at 0.566) but three
panel members (TNFRSF1B, NLRP3, TLR7) retain protective univariate
HRs inside the basal/claudin-low stratum. A well-functioning OmiCairn
should converge on a version of this result on its own.

This is an **expensive** test — a full run will take >1 hour of
wall clock. Do not run it until smoke-tiny is clean end-to-end.
