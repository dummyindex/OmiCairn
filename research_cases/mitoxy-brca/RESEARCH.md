---
research_mode: CONFIRMATORY
domain: cancer_genomics
topic_slug: mitoxy-brca

research_question: |
  The "Mitoxyperilysis" hypothesis posits that breast cancer progression
  is driven by a hybrid programmed-cell-death programme coupling mTOR
  signalling (MTOR, RPTOR, RICTOR), mitochondrial outer-membrane
  permeabilisation (BAX, BAK1, BID), inflammasome activation
  (NLRP3, NINJ1), TNF/TLR innate-immune signalling (TNFRSF1A,
  TNFRSF1B, MYD88, TLR2, TLR4, TLR7, TLR8), and the NRF2 redox axis
  (NFE2L2), with RHOA as a GTPase modulator. The 17 canonical
  panel members (MTORC1 and MTORC2 are excluded because they are
  protein complexes, not gene symbols) define the Mitoxyperilysis
  gene panel evaluated here.

  The primary question is whether a LASSO-Cox prognostic signature
  trained on the 17 Mitoxyperilysis genes in TCGA-BRCA survives
  honest external validation on METABRIC. Specifically:

    H1 (primary): the locked TCGA-trained signature achieves a
        Harrell concordance index > 0.60 on METABRIC, with a 95%
        bootstrap CI whose lower bound exceeds 0.55.

    H2 (secondary, subtype): in the METABRIC basal-like and
        claudin-low stratum, at least one of {NLRP3, TLR7,
        TNFRSF1B, MYD88} retains a univariate protective
        association with overall survival (HR < 1, p < 0.05)
        after multiple-testing correction.

    H3 (secondary, mechanism): in the pooled cohort, the three
        Mitoxyperilysis modules (mTOR: MTOR/RPTOR/RICTOR; MOMP:
        BAX/BAK1/BID; TLR/innate: TLR2/4/7/8, MYD88, TNFRSF1A/B)
        differ significantly in their prognostic contribution,
        consistent with innate-immune dominance.

    H4 (secondary, immune microenvironment): high Mitoxyperilysis
        risk score correlates with an immunosuppressive
        microenvironment (Treg / M2 / MDSC enrichment, CD8 / NK /
        M1 depletion via ssGSEA) and elevated checkpoint
        expression (PD-1, PD-L1, CTLA-4, TIM-3, LAG-3, TIGIT) in
        METABRIC.

    H5 (secondary, PCD crosstalk): the Mitoxyperilysis risk score
        is significantly correlated with established programmed-
        cell-death module scores (ferroptosis, pyroptosis,
        necroptosis, apoptosis, cuproptosis) via Spearman
        correlation, and high-risk patients show enrichment of
        mTOR / ROS / inflammatory pathways via fgsea.

  A negative result on H1 combined with a positive result on H2-H5
  would be a scientifically important finding: canonical multi-gene
  prognostic signatures fail to generalise across cohorts, yet
  individual innate-immune members retain biologically meaningful
  prognostic value, AND the signature captures interpretable
  immune-microenvironment and PCD-crosstalk biology even when its
  raw discrimination is modest. OmiCairn must report this honestly
  rather than massage the signature until H1 passes.

data:
  location: ./test-research-runs/researchclaw_run/data
  description: |
    Pre-fetched cBioPortal exports from the TCGA-BRCA PanCancer
    Atlas 2018 study and the METABRIC study. Four CSVs:
      - tcga_brca_expression_zscores.csv  (1,082 samples × 17 genes)
      - tcga_brca_clinical.csv            (1,084 samples × 59 cols)
      - metabric_expression_zscores.csv   (1,980 samples × 17 genes)
      - metabric_clinical.csv             (2,509 samples × 38 cols)
    Each expression file has a `sampleId` column plus one column per
    gene in the 17-gene Mitoxyperilysis panel. Clinical files have
    `sampleId`, `OS_MONTHS`, `OS_STATUS` (TCGA uses "0:LIVING" /
    "1:DECEASED"; METABRIC uses "0:LIVING" / "1:DECEASED"), plus
    subtype/grade/stage columns. A manifest.json documents the
    source study IDs and entrez IDs.

    External gene sets for ssGSEA / PCD-module scoring (Treg, M2,
    MDSC, CD8, NK, M1; ferroptosis, pyroptosis, necroptosis,
    apoptosis, cuproptosis; mTOR / ROS / inflammation) are NOT on
    disk — EXECUTE must fetch them from MSigDB / Thorsson 2018 /
    KEGG / Reactome via web tools (allow_web: true).
  cohorts: [tcga_brca, metabric]
  modalities: [transcriptomic, clinical]

success_criteria:
  primary_metric: concordance_index
  baseline: venet_grade_only
  pre_registration: required
  honest_validation: required
  notes: |
    Venet-2011 single-variable floor: a Cox model using only tumor
    grade (METABRIC: `GRADE`; TCGA: derived from `AJCC_PATHOLOGIC_TUMOR_STAGE`).
    The primary signature must beat this floor by a margin that
    cannot be explained by sampling variability alone (paired
    bootstrap ΔCI > 0 with 95% CI excluding 0).

figures_planned:
  - id: F1
    title: "17-gene Mitoxyperilysis panel expression landscape in TCGA-BRCA"
    panels:
      - "Heatmap: 17 genes × samples, row-Z, columns stratified by PAM50 subtype"
      - "Volcano: log2 fold-change (tumor vs. matched normal where available) vs. -log10 FDR"
      - "Boxplots: BID, NINJ1, TLR4, MYD88 by TNBC vs. non-TNBC with Wilcoxon p-values"
      - "Pathway enrichment bubble plot (mTOR signaling / ROS metabolism / innate-immune activation) via fgsea or hypergeometric over MSigDB Hallmark + KEGG; x=NES, y=pathway, size=set size, color=-log10(FDR)"
  - id: F2
    title: "LASSO-Cox signature construction on TCGA-BRCA"
    panels:
      - "Univariate Cox forest plot for all 17 genes"
      - "LASSO coefficient path (c-index vs λ)"
      - "Final signature coefficient table with HRs and 95% CIs"
      - "Training-cohort risk-score distribution by PAM50 subtype"
  - id: F3
    title: "Honest external validation on METABRIC"
    panels:
      - "Kaplan-Meier: METABRIC stratified by locked-signature median cutoff"
      - "Time-dependent ROC: 1/3/5-year AUCs on METABRIC"
      - "Calibration plot: predicted vs. observed 5-year survival"
      - "Per-subtype KM (Basal/Claudin-low/LumA/LumB/Her2)"
  - id: F4
    title: "Clinicopathological association and independent prognostic value"
    panels:
      - "Risk score vs. Grade/Stage/Subtype (Kruskal-Wallis)"
      - "Multivariate Cox: signature + age + stage + grade + subtype"
      - "Nomogram integrating signature + age + stage + grade + subtype, with calibration plots at 3- and 5-year horizons (rms-style nomogram via lifelines / scikit-survival multivariable Cox)"
      - "Decision curve analysis at 3/5-year horizons"
  - id: F5
    title: "Risk score association with the tumor immune microenvironment"
    panels:
      - "ssGSEA immune-cell infiltration boxplots: Treg, M2-macrophage, MDSC up in high-risk; CD8+ T, NK, M1-macrophage up in low-risk (gene sets from MSigDB / Thorsson 2018)"
      - "Immune-checkpoint expression boxplots high vs low risk: PD-1 (PDCD1), PD-L1 (CD274), CTLA-4, TIM-3 (HAVCR2), LAG-3, TIGIT"
      - "Correlation heatmap: Mitoxyperilysis risk score (rows) vs immune cell fractions and checkpoint genes (cols), Spearman ρ with FDR annotation"
      - "Boxplots of representative innate-immune drivers (TLR4, MYD88, NLRP3) in high vs low-risk strata, with Wilcoxon p"
  - id: F6
    title: "Mitoxyperilysis crosstalk with other programmed-cell-death pathways"
    panels:
      - "Spearman correlation heatmap: Mitoxyperilysis risk score vs ferroptosis, pyroptosis, necroptosis, apoptosis, cuproptosis module scores (gene sets: WikiPathways / MSigDB / Tang 2019)"
      - "PCD module score boxplots high vs low-risk strata, all five PCD pathways, Wilcoxon"
      - "Shared-gene network diagram: nodes = panel + PCD genes (e.g., BID participates in apoptosis + ferroptosis; NRF2 in ferroptosis + ROS), edges = shared pathway membership"
      - "fgsea / clusterProfiler GSEA: high-risk-vs-low-risk ranked gene list against MSigDB Hallmark mTOR_signaling, reactive_oxygen_species_pathway, inflammatory_response"

figures_not_in_scope:
  - id: F7
    reason: "scRNA-seq data (GSE176078, GSE161529) not on local disk; web fetch of raw FASTQ/h5ad is infeasible within budget"
  - id: F8
    reason: "Visium spatial transcriptomics data (Wu et al. Zenodo, GSE210616) not on local disk; web fetch of raw spatial data is infeasible within budget"

budgets:
  max_phase_wall_minutes: 90
  max_total_wall_minutes: 600
  allow_web: true
  claude_model: claude-sonnet-4-6
  max_phase_budget_usd: 25.0

auto_approve: true
---

# Mitoxyperilysis 17-gene signature in breast cancer

## Source materials

This test case is built from two user-provided Word documents
(from the original study brief):

- `Mitoxyperilysis-breast-cancer.docx` — the research plan describing the
  Mitoxyperilysis hypothesis, the 19-gene panel, and 8 planned
  analysis figures.
- `Mitoxyperilysis-breast-cancer_dataset-search-report.docx` — the dataset
  inventory: 37 public datasets spanning bulk RNA-seq, scRNA-seq,
  and spatial transcriptomics.

The plan in those documents is aspirational (a full multi-omics
integration). This test scopes the first iteration to
what is **available locally** (TCGA-BRCA and METABRIC bulk
expression + clinical) plus what can be **fetched on demand** via
allow_web=true (gene sets from MSigDB / Thorsson 2018 / KEGG /
Reactome). Figures 7 and 8 (scRNA-seq and spatial transcriptomics)
remain out of scope because the underlying raw datasets are too
large to fetch within budget.

## Why this matters for OmiCairn

This is the first test case that asks OmiCairn to produce a real
biomedical analysis with:

1. Multi-cohort honest external validation.
2. Substantive biological follow-up analyses.
3. Honest reporting of a known-difficult negative-leaning result
   (prior manual runs show the full LASSO signature fails
   cross-cohort at c ≈ 0.51, but individual innate-immune genes
   survive stratified analysis).
4. Web-grounded mechanistic figures (F5 immune microenvironment,
   F6 PCD crosstalk, F1 Panel D pathway-enrichment bubble) that
   exercise the EXECUTE phase's allow_web path.

A well-functioning OmiCairn should converge on the honest result
rather than massage the signature until it passes H1. The REVIEW
phase must call a negative primary result a negative result (and record
it in `negative_results.md`), and direct further analysis at the
subgroup / mechanism / immune-microenvironment / PCD-crosstalk findings
that *do* replicate.

## Known prior results (for OmiCairn validation, NOT to be copied into results)

From the manual manifest + README of the pre-fetched data:

| Setup                          | c-index |
|--------------------------------|---------|
| TCGA in-sample                 | 0.6405  |
| TCGA 5-fold CV (out-of-sample) | 0.5492 ± 0.044 |
| METABRIC in-sample             | 0.5572  |
| TCGA → METABRIC cross-cohort   | 0.5106  |

Top individual TCGA Cox predictors: MYD88 (β=−0.14, p=0.048),
TNFRSF1B (β=−0.12, p=0.12), TLR7 (β=+0.09, p=0.24). OmiCairn should
find these on its own; if its numbers disagree with these by >5%
without a methodological explanation, that is a cross-check
failure.

## Out of scope (for this iteration)

- scRNA-seq heterogeneity (Fig 7 in the colleague's plan)
- Spatial transcriptomics (Fig 8 in the colleague's plan)
- Chemotherapy response prediction (GSE25055/65)
- Immunotherapy response (Bassez 2021 / Zhang 2021 scRNA)

These should be added as separate test cases once the required
data is fetched.
