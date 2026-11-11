---
research_mode: METHOD
domain: tumor_immunology
topic_slug: immunesig-pancan

research_question: |
  Do immune-related gene expression signatures predict overall survival
  across human cancers? Using a curated panel of immune-related genes
  and pan-cancer RNA-seq data from TCGA, build a prognostic signature
  that stratifies patients by immune-driven survival risk. Validate
  across held-out cancer types to establish generalisability.

  This is a blind-replication study: the immune-survival association
  is well established (Thorsson et al., Immunity 2018). The goal is
  to independently recover the signal from public data.

data:
  location: ./research_cases/immunesig-pancan/data
  description: >
    Pan-cancer TCGA RNA-seq (33 cancer types, ~10,000 samples) with
    clinical/survival data. Expression restricted to ~400 immune-related
    genes from the ImmPort database.
  cohorts: [TCGA-PanCancer-discovery, TCGA-PanCancer-validation]
  modalities: [RNA-seq, clinical]

success_criteria:
  primary_metric: c_index
  baseline: "clinical-only Cox model (age + stage)"
  pre_registration: required
  honest_validation: required

budgets:
  max_phase_wall_minutes: 60
  max_total_wall_minutes: 300
  allow_web: true
  claude_model: claude-sonnet-4-6
  max_phase_budget_usd: 20.0

auto_approve: true
---

# immunesig-pancan — Immune signature pan-cancer prognostic study

Blind-replication test case targeting Thorsson et al. (2018) immune
landscape findings. OmiCairn should independently recover immune-driven
survival stratification from TCGA pan-cancer data.

See README.md for full replication rationale and success criteria.
