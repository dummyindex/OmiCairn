---
research_mode: METHOD
domain: cancer_genomics
topic_slug: angiogenesis-crc

research_question: |
  Does a 20-gene angiogenesis panel predict overall survival in
  colorectal cancer (CRC), validated across independent external
  cohorts?

  The hypothesis is that angiogenesis-related genes collectively
  predict CRC prognosis because tumor angiogenesis -- the formation
  of new blood vessels -- is essential for CRC growth and metastasis,
  and its dysregulation marks aggressive tumors with poor outcomes.
  This is clinically grounded: anti-angiogenic therapy (bevacizumab)
  is a standard treatment for metastatic CRC, and angiogenic gene
  expression profiles correlate with bevacizumab response. Tumors
  with high expression of pro-angiogenic factors (VEGFA, ANGPT2,
  HIF1A) and stromal remodeling genes (TIMP1, SPP1, VCAN) are
  expected to have worse outcomes due to enhanced vascular supply
  facilitating metastasis and an immunosuppressive microenvironment.

  The 20-gene angiogenesis panel is:
  VEGFA, VEGFB, VEGFC, PGF, PDGFB, FLT1, KDR, FLT4, HIF1A, EPAS1,
  ANGPT1, ANGPT2, TEK, MMP14, TIMP1, VCAN, SPP1, THBS1, CXCL12,
  NRP1.

  Secondary question: if the full-panel LASSO signature fails
  external validation, do any individual panel members retain
  nominally significant univariate prognostic value? In particular,
  do anti-angiogenic genes (THBS1) show a protective effect and
  pro-angiogenic genes (VEGFA, ANGPT2) show a risk effect?

data:
  location: ./research_cases/angiogenesis-crc/data
  description: >
    Three independent colorectal cancer cohorts fetched from cBioPortal
    REST API:

    1. Discovery: TCGA-COADREAD (Pan-Cancer Atlas 2018) -- 592 samples
       with RNA-Seq z-scores for all 20 angiogenesis genes. Full survival
       data (OS, DFS, PFS) and rich clinical annotations (stage, MSI
       status, KRAS mutation, etc.).

    2. Validation 1: Sidra-LUMC AC-ICAM Colon Cancer (coad_silu_2022)
       -- 348 samples with RNA-Seq z-scores for all 20 genes. OS and
       PFS data available. This is an independent cohort from
       Sidra Medicine/Leiden with comprehensive genomic, transcriptomic,
       and immune characterization (Dienstmann et al. Nat Med 2023).

    3. Validation 2: CPTAC Colon Cancer (coad_cptac_2019) -- 97 samples
       with protein-level quantification z-scores for 6 of 20 genes
       (MMP14, TIMP1, VCAN, THBS1, CXCL12, NRP1). OS and DFS data
       available. This provides cross-platform validation at the
       protein level.

    All gene symbols were resolved to Entrez IDs. Sample counts and
    gene availability are recorded in manifest.json.
  cohorts:
    - tcga_coadread_discovery
    - coad_silu_2022
    - coad_cptac_2019
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

# angiogenesis-crc -- OmiCairn integration test on real data

This test case uses real multi-cohort colorectal cancer data fetched
from the cBioPortal public REST API. It asks whether an
angiogenesis-related gene panel carries prognostic information in CRC
that survives honest external validation on two independent cohorts.

## Data provenance

**Discovery cohort (TCGA-COADREAD):** Expression z-scores come from
the `coadread_tcga_pan_can_atlas_2018` study, profile
`coadread_tcga_pan_can_atlas_2018_rna_seq_v2_mrna_median_Zscores`.
Clinical data (OS_STATUS, OS_MONTHS, DFS, PFS, AJCC stage, MSI
status, KRAS/BRAF mutation, etc.) come from the same study. All 20
angiogenesis genes were resolved to Entrez IDs; 592 samples have
expression data, 594 have clinical data.

**Validation 1 (Sidra-LUMC AC-ICAM 2022):** Expression z-scores from
`coad_silu_2022_rna_seq_mrna_median_Zscores`. 348 samples with all
20 genes available. **NOTE: Survival data (OS_STATUS, OS_MONTHS) is
entirely missing (0/348 non-null).** This cohort cannot be used for
prognostic survival validation. Expression data is complete and may
be used for expression-level consistency checks (e.g., correlation
of risk score distribution). This is an independent cohort from
Sidra Medicine and Leiden University Medical Center.

**Validation 2 (CPTAC 2019):** Protein-level quantification z-scores
from `coad_cptac_2019_protein_quantification_zscores`. 97 samples
with 6 of 20 genes available at protein level (MMP14, TIMP1, VCAN,
THBS1, CXCL12, NRP1). OS and DFS data available. This provides a
cross-platform validation at the protein level, testing whether the
transcriptomic signature has protein-level correlates.

## Biological rationale

Angiogenesis is a hallmark of cancer and is particularly relevant in
colorectal cancer:

- Anti-angiogenic therapy (bevacizumab targeting VEGFA) is a
  cornerstone of metastatic CRC treatment, demonstrating the
  clinical importance of angiogenic signaling in this cancer type.
- The VEGF family (VEGFA, VEGFB, VEGFC, PGF) and their receptors
  (FLT1/VEGFR1, KDR/VEGFR2, FLT4/VEGFR3) drive tumor
  neovascularization.
- Hypoxia-inducible factors (HIF1A, EPAS1/HIF2A) are master
  regulators of angiogenesis activated in hypoxic tumor cores.
- The angiopoietin-TIE2 axis (ANGPT1, ANGPT2, TEK) modulates
  vessel maturation; ANGPT2 destabilizes vessels and promotes
  metastasis.
- Stromal/ECM genes (TIMP1, SPP1, VCAN, MMP14) remodel the
  extracellular matrix to facilitate vessel sprouting.
- THBS1 (thrombospondin-1) is an endogenous anti-angiogenic factor;
  its loss may mark more aggressive tumors.

The Sidra-LUMC cohort (348 samples with all 20 genes) is an
unusually strong validation resource, making this test case likely
to yield a **positive result** if the underlying biology holds.

## What OmiCairn should do

1. Profile the TCGA-COADREAD discovery cohort and identify the
   20-gene panel columns.
2. Design a pre-registered LASSO-Cox experiment with an honest
   clinical-stage-only baseline.
3. Fit on the 592-sample TCGA discovery set, lock coefficients with
   SHA-256, evaluate once on Sidra-LUMC (primary validation, n=348)
   and once on CPTAC (secondary, protein-level validation, n=97).
4. Handle the gene missingness in CPTAC appropriately (restrict to
   the 6-gene intersection or use only Sidra-LUMC as primary
   validation, document the choice).
5. Explore MSI status as a potential interaction variable (MSI-high
   CRC has distinct biology from MSS CRC).
6. Produce an honest analysis whose every number in `analysis.md` and
   `results.json` traces back to the code that produced it.

## Expected difficulty

Angiogenesis signatures in CRC have strong published support, and the
large discovery cohort (n=592) with a well-powered external
validation (n=348) gives OmiCairn the best chance of detecting a real
signal.

Key challenges for OmiCairn:
- Stage is a dominant confounder in CRC prognosis; the angiogenesis
  signature must add information beyond stage alone.
- MSI status and CMS (consensus molecular subtype) classification
  interact with angiogenesis; CMS4 (mesenchymal) is high in
  angiogenic and stromal genes but also has the worst prognosis.
- The CPTAC cohort has only 6 genes at protein level, so cross-
  platform validation will be limited.
- Some genes (TIMP1, SPP1) are also inflammatory markers, so the
  signature may capture immune rather than purely angiogenic effects.
