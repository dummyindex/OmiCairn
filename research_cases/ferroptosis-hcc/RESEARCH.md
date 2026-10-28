---
research_mode: METHOD
domain: cancer_genomics
topic_slug: ferroptosis-hcc

research_question: |
  Does a 20-gene ferroptosis-related prognostic signature, learned by
  LASSO-Cox on TCGA-LIHC, generalise under honest pre-registered
  external validation? The hypothesis is that ferroptosis
  susceptibility genes collectively predict HCC prognosis because
  ferroptosis resistance is a hallmark of aggressive hepatocellular
  carcinoma.

  The 20-gene ferroptosis panel is:
  GPX4, SLC7A11, ACSL4, TFRC, LPCAT3, NCOA4, FTH1, FTL, SLC3A2,
  HMOX1, NFS1, CISD1, CS, ACSF2, DPP4, CDKN1A, FANCD2, CHAC1,
  EMC2, SAT1.

  Secondary question: if the full-panel LASSO signature fails
  validation, do any individual panel members retain nominally
  significant univariate prognostic value, and is the direction of
  effect consistent with the mechanistic hypothesis (anti-ferroptosis
  genes as risk, pro-ferroptosis genes as protective)?

data:
  location: ./research_cases/ferroptosis-hcc/data
  description: >
    TCGA-LIHC (Pan-Cancer Atlas 2018) expression z-scores and clinical/
    survival data fetched from cBioPortal REST API.  No external HCC
    validation cohort with RNA-seq profiles was available on cBioPortal
    (hcc_inserm_fr_2015 and lihc_amc_prv exist but lack mRNA z-score
    profiles; lihc_riken_jp_2020 not found).  A 70/30 random split
    (seed=42) of TCGA-LIHC is used instead: 257 discovery, 109
    validation.  Sample lists in discovery_samples.txt and
    validation_samples.txt.
  cohorts: [tcga_lihc_discovery, tcga_lihc_validation]
  modalities: [transcriptomic, clinical]

success_criteria:
  primary_metric: concordance_index
  baseline: stage_only
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

# ferroptosis-hcc — OmiCairn integration test on real data

This test case uses real TCGA-LIHC (hepatocellular carcinoma) data
fetched from the cBioPortal public REST API.  It asks whether a
ferroptosis-related gene panel carries prognostic information in HCC
that survives honest hold-out validation.

## Data provenance

Expression z-scores come from the `lihc_tcga_pan_can_atlas_2018`
study, profile `lihc_tcga_pan_can_atlas_2018_rna_seq_v2_mrna_median_Zscores`.
Clinical data (OS_STATUS, OS_MONTHS, AJCC stage, grade, etc.) come
from the same study.  All 20 ferroptosis genes were successfully
resolved to Entrez IDs; 366 samples have both expression and survival
data.

No suitable external HCC validation cohort with mRNA z-score profiles
was found on cBioPortal.  Therefore the 366 TCGA-LIHC samples are
split 70/30 (random, seed=42) into 257 discovery and 109 validation
samples.  The split is pre-specified and frozen in text files.

## What OmiCairn should do

The purpose is to verify that OmiCairn can, autonomously:

1. Profile the TCGA-LIHC cohort and identify the 20-gene panel columns.
2. Design a pre-registered LASSO-Cox experiment with an honest
   clinical-stage-only baseline.
3. Fit on the 257-sample discovery split, lock coefficients with
   SHA-256, evaluate once on the 109-sample validation split.
4. Produce an honest analysis that calls a negative result a
   negative result (if the signature fails in validation, say so).
5. Report every number in `analysis.md` and `results.json`, traceable
   to the code that produced it, and record any negative result in
   `negative_results.md`.

## Expected difficulty

Ferroptosis gene signatures in HCC have been reported in the
literature (e.g., Liang et al. 2020, Liu et al. 2020), but many of
those studies used the same TCGA data for both training and testing,
inflating performance.  An honest hold-out within TCGA-LIHC should
yield a more conservative result.  With n=109 validation and a
20-gene panel, the signature may or may not outperform stage alone.
A well-functioning OmiCairn should converge on the correct conclusion
either way.

This is an **expensive** test — a full run will take >1 hour of
wall clock.  Do not run it until smoke-tiny is clean end-to-end.
