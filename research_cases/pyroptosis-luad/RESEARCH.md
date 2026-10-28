---
research_mode: METHOD
domain: cancer_genomics
topic_slug: pyroptosis-luad

research_question: |
  Does a pyroptosis gene panel predict overall survival in lung
  adenocarcinoma (LUAD), validated across independent external cohorts?
  The hypothesis is that pyroptosis-related genes collectively predict
  LUAD prognosis because pyroptosis — a pro-inflammatory form of
  programmed cell death — shapes the tumour immune microenvironment,
  and its dysregulation may mark aggressive or immunologically cold
  tumours.

  The 16-gene pyroptosis panel is:
  SCAF11, NOD1, NLRP2, NLRP1, GPX4, CASP8, AIM2, GSDMD, GSDME,
  CASP1, CASP3, CASP4, IL1B, IL18, NLRP3, PYCARD.

  Secondary question: if the full-panel LASSO signature fails
  external validation, do any individual panel members retain
  nominally significant univariate prognostic value, and is the
  direction of effect consistent with the mechanistic hypothesis
  (e.g., pro-pyroptosis genes as protective via immune activation,
  anti-pyroptosis genes as risk)?

data:
  location: ./research_cases/pyroptosis-luad/data
  description: >
    Three independent LUAD cohorts fetched from cBioPortal REST API:

    1. Discovery: TCGA-LUAD (Pan-Cancer Atlas 2018) — 510 samples with
       RNA-Seq z-scores for all 16 pyroptosis genes. Full survival data
       (OS, DFS, PFS) and rich clinical annotations (stage, age, sex,
       smoking history, etc.).

    2. Validation 1: OncoSG LUAD 2020 (luad_oncosg_2020) — 169 samples
       with RNA-Seq z-scores for all 16 genes. OS data available. This is
       an independent Singaporean cohort providing geographic diversity.

    3. Validation 2: CAS LUAD 2020 (luad_cas_2020) — 51 samples with
       FPKM z-scores for 12 of 16 genes (missing SCAF11, NOD1, AIM2,
       GSDMD). OS and DFS data available. This is a smaller Chinese
       Academy of Sciences cohort.

    All gene symbols were resolved to Entrez IDs. Sample counts and
    gene availability are recorded in manifest.json.
  cohorts:
    - tcga_luad_discovery
    - luad_oncosg_2020
    - luad_cas_2020
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

# pyroptosis-luad — OmiCairn integration test on real data

This test case uses real multi-cohort LUAD data fetched from the
cBioPortal public REST API.  It asks whether a pyroptosis-related
gene panel carries prognostic information in lung adenocarcinoma
that survives honest external validation on two independent cohorts.

## Data provenance

**Discovery cohort (TCGA-LUAD):** Expression z-scores come from the
`luad_tcga_pan_can_atlas_2018` study, profile
`luad_tcga_pan_can_atlas_2018_rna_seq_v2_mrna_median_Zscores`.
Clinical data (OS_STATUS, OS_MONTHS, DFS, PFS, AJCC stage, grade,
smoking history, etc.) come from the same study.  All 16 pyroptosis
genes were successfully resolved to Entrez IDs; 510 samples have
expression data, 566 have clinical data.

**Validation 1 (OncoSG 2020):** Expression z-scores from
`luad_oncosg_2020_rna_seq_v2_mrna_median_Zscores`.  169 samples
with all 16 genes and OS survival data.  This is an independent
Singaporean cohort providing geographic and ethnic diversity.

**Validation 2 (CAS 2020):** Expression z-scores from
`luad_cas_2020_mrna_seq_fpkm_all_sample_Zscores`.  51 samples with
12 of 16 genes (SCAF11, NOD1, AIM2, GSDMD not available in this
dataset).  OS and DFS data available.  This is a smaller Chinese
Academy of Sciences cohort.

## What OmiCairn should do

The purpose is to verify that OmiCairn can, autonomously:

1. Profile the TCGA-LUAD discovery cohort and identify the 16-gene
   panel columns.
2. Design a pre-registered LASSO-Cox experiment with an honest
   clinical-stage-only baseline.
3. Fit on the 510-sample TCGA discovery set, lock coefficients with
   SHA-256, evaluate once on OncoSG (primary validation, n=169) and
   once on CAS (secondary validation, n=51).
4. Handle the gene missingness in CAS 2020 appropriately (restrict
   to the 12-gene intersection or use imputation, document the
   choice).
5. Produce an honest analysis that calls a negative result a
   negative result (if the signature fails in external validation,
   say so).
6. Report every number in `analysis.md` and `results.json`, traceable
   to the code that produced it, and record any negative result in
   `negative_results.md`.

## Expected difficulty

Pyroptosis gene signatures in LUAD have been reported in the
literature (e.g., Ye et al. 2021, Lin et al. 2022), but many of
those studies used the same TCGA data for both training and testing,
or used only internal cross-validation, inflating performance.
Honest external validation on OncoSG and CAS should yield a more
conservative result.

Key challenges for OmiCairn:
- The OncoSG cohort (n=169) provides reasonable statistical power for
  validation, but CAS (n=51) is small and has 4 missing genes.
- The gene panel spans both executors (GSDMD, GSDME, caspases) and
  sensors/inflammasome components (NLRP1/2/3, AIM2, PYCARD), which
  may have heterogeneous prognostic effects.
- Stage is a strong confounder in LUAD prognosis; the signature must
  add information beyond stage alone.

A well-functioning OmiCairn should converge on the correct conclusion
either way and produce a methodologically sound analysis.

This is an **expensive** test — a full run will take >1 hour of
wall clock.  Do not run it until smoke-tiny is clean end-to-end.
