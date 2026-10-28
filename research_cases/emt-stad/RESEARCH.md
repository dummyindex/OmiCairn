---
research_mode: METHOD
domain: cancer_genomics
topic_slug: emt-stad

research_question: |
  Does a 20-gene epithelial-mesenchymal transition (EMT) panel predict
  overall survival in gastric adenocarcinoma (STAD), validated on an
  independent held-out cohort?

  The hypothesis is that EMT-related genes collectively predict gastric
  cancer prognosis because EMT drives invasion, metastasis, and therapy
  resistance in gastric tumors. Tumors with a strong mesenchymal
  phenotype — marked by upregulation of EMT transcription factors
  (SNAI1/2, TWIST1, ZEB1/2), mesenchymal markers (VIM, CDH2, FN1),
  and extracellular matrix remodeling genes (MMP2, SERPINE1, COL1A1) —
  are expected to have worse outcomes. This is biologically grounded:
  TCGA's molecular classification of gastric cancer identified a
  genomically stable (GS) subtype enriched for EMT gene expression and
  diffuse histology, which carries worse prognosis than the
  chromosomal instability (CIN) subtype.

  The 20-gene EMT panel is:
  SNAI1, SNAI2, TWIST1, ZEB1, ZEB2, VIM, CDH2, FN1, ACTA2,
  SERPINE1, MMP2, MMP9, COL1A1, COL3A1, SPARC, TGFB1, TGFB2,
  WNT5A, THBS2, PLOD2.

  Secondary question: if the full-panel LASSO signature fails
  hold-out validation, do any individual panel members retain
  nominally significant univariate prognostic value, and is the
  direction of effect consistent with the mechanistic hypothesis
  (mesenchymal markers as risk, epithelial maintenance genes as
  protective)?

  This test case is designed to have a higher probability of yielding
  a positive result than other test cases, because multiple published
  studies (Xu et al. 2021, Zhang et al. 2021, He et al. 2022) have
  demonstrated that EMT gene signatures validate across independent
  gastric cancer cohorts, with hazard ratios consistently >1.5 for
  high-EMT groups.

data:
  location: ./research_cases/emt-stad/data
  description: >
    TCGA-STAD (Pan-Cancer Atlas 2018) expression z-scores and clinical/
    survival data fetched from cBioPortal REST API.  No external gastric
    cancer validation cohort with sufficient RNA-seq profiles was
    available on cBioPortal (stad_oncosg_2018 had only 11 expression
    samples).  A 70/30 random split (seed=42) of TCGA-STAD is used:
    289 discovery, 123 validation.  Sample lists in
    discovery_samples.txt and validation_samples.txt.

    All 20 EMT genes were successfully resolved to Entrez IDs.
    412 samples have expression data, 440 have clinical data.
    Full survival data (OS, DFS, PFS) and rich clinical annotations
    (stage, grade, Lauren classification) are available.
  cohorts:
    - tcga_stad_discovery
    - tcga_stad_validation_split
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

# emt-stad -- OmiCairn integration test on real data

This test case uses real TCGA-STAD (gastric adenocarcinoma) data
fetched from the cBioPortal public REST API. It asks whether an EMT
(epithelial-mesenchymal transition) gene panel carries prognostic
information in STAD that survives honest hold-out validation.

## Data provenance

**Discovery cohort (TCGA-STAD):** Expression z-scores come from the
`stad_tcga_pan_can_atlas_2018` study, profile
`stad_tcga_pan_can_atlas_2018_rna_seq_v2_mrna_median_Zscores`.
Clinical data (OS_STATUS, OS_MONTHS, DFS, PFS, AJCC stage, Lauren
classification, etc.) come from the same study. All 20 EMT genes were
resolved to Entrez IDs; 412 samples have expression data, 440 have
clinical data.

**Validation:** The OncoSG 2018 gastric cancer study
(stad_oncosg_2018) had only 11 RNA-Seq expression samples on
cBioPortal, insufficient for validation. A 70/30 random split
(seed=42) of TCGA-STAD is used instead: 289 discovery samples and
123 validation samples.

## Biological rationale

EMT is a fundamental process in cancer metastasis where epithelial
cells acquire mesenchymal properties -- increased motility,
invasiveness, and resistance to apoptosis. In gastric cancer
specifically:

- The TCGA molecular classification identified the Genomically Stable
  (GS) subtype, enriched for diffuse histology and EMT gene
  expression. GS tumors have the worst prognosis among TCGA subtypes.
- SERPINE1 (PAI-1), a key EMT effector, has been independently
  validated as a gastric cancer prognostic biomarker.
- TGF-beta signaling (TGFB1, TGFB2) is a master regulator of EMT
  and is frequently activated in advanced gastric cancer.
- Collagen remodeling genes (COL1A1, COL3A1, SPARC) create the
  desmoplastic stroma characteristic of aggressive gastric tumors.

Multiple published studies have validated EMT gene signatures across
independent gastric cancer cohorts with hazard ratios >1.5 for
high-EMT groups, making this a good candidate for a **positive
result** test case.

## What OmiCairn should do

1. Profile the TCGA-STAD discovery set and identify the 20-gene
   panel columns.
2. Design a pre-registered LASSO-Cox experiment with an honest
   clinical-stage-only baseline.
3. Fit on the 289-sample discovery set, lock coefficients with
   SHA-256, evaluate once on the 123-sample hold-out set.
4. Explore whether Lauren classification (diffuse vs intestinal)
   interacts with the EMT signature.
5. Produce an honest analysis that calls a positive result positive
   or a negative result negative.
6. Report every number in `analysis.md` and `results.json`, traceable
   to the code that produced it, and record any negative result in
   `negative_results.md`.

## Expected difficulty

EMT is among the most robust biological programs in cancer biology,
with strong prior evidence in gastric cancer. The held-out validation
design using a random split from the same cohort avoids
platform/batch effects but provides a weaker test of generalizability
than truly independent external validation.

Key challenges for OmiCairn:
- Stage and Lauren classification are strong confounders; the EMT
  signature must add prognostic value beyond these clinical variables.
- Some EMT genes (e.g., VIM, FN1) may be driven by stromal content
  rather than tumor-intrinsic EMT.
- The panel spans transcription factors (SNAI1/2, TWIST1, ZEB1/2)
  and downstream effectors (MMP2, SERPINE1) which may have different
  prognostic directions.
