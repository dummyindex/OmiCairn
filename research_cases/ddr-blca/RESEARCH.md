---
research_mode: METHOD
domain: cancer_genomics
topic_slug: ddr-blca

research_question: |
  Does a 20-gene DNA damage repair (DDR) panel predict overall
  survival in muscle-invasive bladder cancer (BLCA), validated
  across independent external cohorts?

  The hypothesis is that DDR gene expression collectively predicts
  bladder cancer prognosis because DDR pathway deficiency shapes
  tumor immunogenicity, platinum sensitivity, and immune checkpoint
  inhibitor response. Tumors with low DDR expression -- indicating
  impaired DNA repair capacity -- are expected to have higher
  mutational burden, more neoantigens, and paradoxically better
  outcomes in the immunotherapy era due to enhanced immune
  recognition. Conversely, tumors with high expression of replication
  stress response genes (CHEK1, ATR, PARP1) may indicate ongoing
  genomic instability and aggressive proliferation.

  The 20-gene DDR panel is:
  BRCA1, BRCA2, ATM, ATR, RAD51, MLH1, MSH2, MSH6, PMS2, ERCC1,
  ERCC2, XPA, XRCC1, PARP1, POLB, FANCD2, FANCA, CHEK1, CHEK2,
  TP53.

  Secondary question: if the full-panel LASSO signature fails
  external validation, do any individual DDR genes retain nominally
  significant univariate prognostic value? In particular, do mismatch
  repair genes (MLH1, MSH2) predict survival differently from
  homologous recombination genes (BRCA1, BRCA2), consistent with
  their distinct biological roles?

  This test case benefits from the IMvigor210 immunotherapy trial
  cohort (n=347) as external validation for survival analysis. The
  BCAN/HCRN cohort (n=169) has expression data but no survival data,
  so it can only serve as a reference for expression-level analyses.

data:
  location: ./research_cases/ddr-blca/data
  description: >
    Three independent bladder cancer cohorts fetched from cBioPortal
    REST API:

    1. Discovery: TCGA-BLCA (Pan-Cancer Atlas 2018) -- 407 samples with
       RNA-Seq z-scores for all 20 DDR genes. Full survival data
       (OS, DFS, PFS) and rich clinical annotations (stage, grade,
       molecular subtype, etc.).

    2. Validation 1: IMvigor210 (blca_iatlas_imvigor210_2017) -- 347
       samples with RNA-Seq z-scores for all 20 genes. OS data
       available. This is the landmark atezolizumab Phase II trial
       cohort for metastatic urothelial carcinoma, providing
       immunotherapy-treated context.

    3. Reference cohort: BCAN/HCRN (blca_bcan_hcrn_2022) -- 169 samples
       with RNA-Seq z-scores for all 20 genes. This is the Bladder
       Cancer Advocacy Network cohort (Clinton et al. Nat Commun 2022).
       WARNING: survival data (OS_STATUS, OS_MONTHS) is entirely missing
       in this cohort; it cannot be used for survival validation. It may
       be used for expression-level comparisons only.

    All gene symbols were resolved to Entrez IDs. Sample counts and
    gene availability are recorded in manifest.json.
  cohorts:
    - tcga_blca_discovery
    - blca_iatlas_imvigor210_2017
    - blca_bcan_hcrn_2022
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

# ddr-blca -- OmiCairn integration test on real data

This test case uses real multi-cohort bladder cancer data fetched
from the cBioPortal public REST API. It asks whether a DNA damage
repair (DDR) gene panel carries prognostic information in BLCA that
survives honest external validation on two independent cohorts,
including an immunotherapy trial cohort.

## Data provenance

**Discovery cohort (TCGA-BLCA):** Expression z-scores come from the
`blca_tcga_pan_can_atlas_2018` study, profile
`blca_tcga_pan_can_atlas_2018_rna_seq_v2_mrna_median_Zscores`.
Clinical data (OS_STATUS, OS_MONTHS, DFS, PFS, AJCC stage, grade,
molecular subtype, etc.) come from the same study. All 20 DDR genes
were resolved to Entrez IDs; 407 samples have expression data, 411
have clinical data.

**Validation 1 (IMvigor210):** Expression z-scores from
`blca_iatlas_imvigor210_2017_rna_seq_mrna_median_Zscores`. 347
samples with all 20 genes and OS survival data. This is the landmark
atezolizumab (anti-PD-L1) Phase II trial for metastatic urothelial
carcinoma (Mariathasan et al. Nature 2018). All patients received
immunotherapy, providing a complementary clinical context to the
treatment-heterogeneous TCGA cohort.

**Validation 2 (BCAN/HCRN):** Expression z-scores from
`blca_bcan_hcrn_2022_rna_seq_v2_mrna_median_Zscores`. 169 samples
with all 20 genes. This is the Bladder Cancer Advocacy Network
cohort (Clinton et al. Nat Commun 2022), with comprehensive
genomic and transcriptomic profiling of urothelial carcinoma.

## Biological rationale

DNA damage repair pathways are central to bladder cancer biology and
therapeutic response:

- Bladder cancer has one of the highest mutational burdens among
  solid tumors, reflecting frequent DDR pathway alterations.
- ERCC2 mutations predict response to cisplatin-based neoadjuvant
  chemotherapy in MIBC (Van Allen et al. Cancer Discov 2014).
- DDR gene mutations (ATM, BRCA1/2, FANCD2) are associated with
  improved response to immune checkpoint blockade (Teo et al. J
  Immunother Cancer 2019).
- Mismatch repair deficiency (MLH1, MSH2, MSH6, PMS2) leads to
  microsatellite instability and higher neoantigen load, predicting
  immunotherapy benefit.
- PARP1 and homologous recombination genes (BRCA1/2, RAD51) are
  therapeutic targets; PARP inhibitors are being explored in
  DDR-deficient bladder cancer.
- TP53 is the most frequently mutated gene in bladder cancer (~50%),
  and its expression level reflects both wild-type function and
  mutant protein accumulation.

The availability of the IMvigor210 immunotherapy trial cohort as
external validation is particularly valuable -- it allows testing
whether DDR gene expression predicts outcomes specifically in the
immunotherapy setting, where DDR deficiency theory predicts better
outcomes.

## What OmiCairn should do

1. Profile the TCGA-BLCA discovery cohort and identify the 20-gene
   DDR panel columns.
2. Design a pre-registered LASSO-Cox experiment with an honest
   clinical-stage-only baseline.
3. Fit on the 407-sample TCGA discovery set, lock coefficients with
   SHA-256, evaluate once on IMvigor210 (primary validation, n=347)
   and once on BCAN/HCRN (secondary validation, n=169).
4. Handle the different clinical contexts: TCGA has mixed treatments,
   IMvigor210 is pure immunotherapy, BCAN/HCRN has comprehensive
   genomics. Document how treatment heterogeneity is addressed.
5. Explore molecular subtype (luminal vs basal) as a potential
   interaction variable.
6. Produce an honest analysis whose every number in `analysis.md` and
   `results.json` traces back to the code that produced it.

## Expected difficulty

DDR pathway biology in bladder cancer is well-established, with
strong mechanistic links to both chemotherapy and immunotherapy
response. The two external validation cohorts (347 + 169 samples)
with all 20 genes available provide strong power for validation.

Key challenges for OmiCairn:
- The TCGA discovery and IMvigor210 validation have different
  treatment contexts (mixed vs pure immunotherapy), which may
  affect the prognostic direction of DDR genes.
- TP53 expression is complex: high expression often indicates
  mutant protein accumulation (loss of function), not gain of
  function. OmiCairn needs to handle this nuance.
- The DDR panel spans functionally distinct repair pathways (HR,
  MMR, NER, BER, FA); genes from different pathways may have
  opposing prognostic effects.
- Stage remains a strong confounder; the DDR signature must add
  value beyond clinical staging.
