---
research_mode: CONFIRMATORY
domain: prodromal_pd_proteomics
topic_slug: parkinson-plasma-prodromal-panel

research_question: |
  Hällqvist J., Bartl M., Dakna M., ..., Mollenhauer B.,
  Trenkwalder C. (Nat Commun 15:4759, 2024; DOI 10.1038/s41467-024-
  48961-3; PMC11189460; PRIDE PXD046355) reported that an 8-protein
  plasma panel (Granulin/GRN, MASP2, HSPA5/BiP, PTGDS, ICAM1,
  complement C3, DKK3, SERPING1) distinguishes individuals with
  isolated REM-sleep behaviour disorder (iRBD) who will convert to
  Parkinson's disease within 7 years from those who will not, with
  area under the curve up to 1.0 in their internal cohort.

  The result is rare in PD biomarker literature: a small,
  interpretable panel measured by LC-MS/MS DIA in real plasma from
  a longitudinal prodromal cohort (DeNoPa + Oxford Discovery). It
  has not been formally replicated on the deposited MS spectra by
  an independent analysis pipeline, and the cross-platform
  generalisability (LC-MS/MS → other quantification) has not been
  established with deposited public data only.

  This OmiCairn run formally tests:

    H1 (primary — re-analysis of the deposited MS spectra
         replicates the 8-protein panel's predictive performance):
        Re-processing PXD046355 spectra with a from-scratch
        DIA-NN / Spectronaut-equivalent pipeline (DIA-NN 1.9.2 or
        Skyline) → protein-level intensities → log-transform +
        quantile normalisation → 5-fold cross-validated logistic
        regression with the 8 published proteins as features,
        labels = iRBD-converter vs. iRBD-non-converter at 7-year
        follow-up. Primary success = 5-fold CV AUC ≥ 0.85 (the
        published Hällqvist 7-year AUC is ≈ 1.0; we allow margin
        for spectra → intensity pipeline divergence).

    H2 (secondary — individual-protein hazard ratios reproduce):
        Each of the 8 proteins, modeled univariately in a Cox
        proportional-hazards model on time-to-conversion, shows
        the same sign of hazard ratio as published (Hällqvist
        Supplementary Table 3). Concordance metric = 8/8 sign
        agreement; loose acceptance = ≥ 6/8.

    H3 (secondary — panel beats clinical-only baseline):
        Adding the 8-protein panel to a clinical-baseline Cox
        model (age + sex + MoCA + UPSIT) improves time-dependent
        AUC at 5 years by ΔAUC > 0.05 with paired bootstrap CI
        excluding 0. The clinical baseline serves as the
        Venet-style floor.

    H4 (secondary — out-of-cohort signature alignment to UK
         Biobank Olink):
        Of the 8 panel proteins, 6 are on the UK Biobank Olink
        Explore 1536 panel. We test whether the directionality
        (HR sign) reported for these 6 proteins in the Gan/You
        2025 UKB Olink incident-PD analysis (Nat Aging 5:642)
        matches the Hällqvist signs. Concordance = 6/6 expected;
        loose acceptance ≥ 4/6 with binomial p < 0.05 vs.
        chance 0.5.

    H5 (secondary — sub-panel decomposition):
        Decompose the 8-panel into 3 functional sub-panels:
          (a) inflammation / complement: ICAM1, C3, SERPING1
          (b) proteostasis: HSPA5, GRN
          (c) lipid / cell signaling: PTGDS, DKK3, MASP2
        Test whether any single sub-panel achieves AUC > 0.75 on
        its own — i.e., is the predictive signal carried by one
        of three functional axes or distributed?

  A negative-leaning result on H1 (cross-pipeline AUC < 0.7) would
  be a major methodological caveat for the field and would imply
  that quantitative biomarker panels are sensitive to MS pipeline
  choice — itself an informative finding given the rapid uptake of
  the Hällqvist panel in trial design.

data:
  location: ./research_cases/parkinson-plasma-prodromal-panel/data
  shared_cache: ./datasets
  description: |
    **No pre-staged raw data on disk.** Fetch into
    `./datasets/<accession>/`.

    **Primary datasets:**

      - **Hällqvist 2024 PRIDE deposit (signature derivation +
        spectra for re-analysis).**
        PRIDE PXD046355 (full LC-MS/MS DIA spectra).
          https://www.ebi.ac.uk/pride/archive/projects/PXD046355
        Content: ~250 plasma DIA runs across 99 PD + 36 HC +
        72 iRBD (18 converters + 54 non-converters at 7-year) +
        41 disease-control plasma samples. Companion
        Supplementary tables published with paper: 8-panel
        proteins, per-protein HRs, full DE list.
        Target path: ./datasets/PXD046355_hallqvist/

      - **Reference spectral libraries (DIA-NN deep-learning
        prediction):** generate with DIA-NN 1.9.2 from public
        SwissProt human FASTA; no separate download needed.

      - **Gan / You 2025 UKB Olink incident-PD supplement
        (cross-platform aspirational comparison for H4).**
        Nature Aging 5:642 supplementary tables include the
        per-protein HR + p for the 38-protein PD-incident panel.
        We use the supplementary tables only; we do NOT attempt
        to access UKB Olink individual-level data.
        PMC: PMC11968858 (search by PMID 39979637).
        Target path: ./datasets/gan_2025_UKB_supp/

      - **Bayati 2024 DDC paper supplement (DDC sanity check).**
        Acta Neuropathologica 147:52. PMC10927779.
        Used to test that DDC (the canonical PD biofluid marker)
        is recovered from PXD046355 — sanity check that the
        re-processing pipeline is not broken.
        Target path: ./datasets/bayati_2024_supp/

      - **PRIDE / PeptideAtlas search:** for the Hällqvist
        spectra to match, the DIA-NN library uses SwissProt human
        canonical proteome (UP000005640). Reference proteome:
          https://www.uniprot.org/proteomes/UP000005640
        Target path: ./datasets/uniprot_human_canonical/

  cohorts:
    [hallqvist_2024_denopa_oxford_pxd046355,
     gan_2025_UKB_supp_panel_alignment,
     bayati_2024_DDC_sanity_check]
  modalities: [lcms_dia_plasma_proteomics,
               summary_statistics_olink_supp_tables]

success_criteria:
  primary_metric: replicated_panel_auc
  baseline: clinical_only_cox_model
  pre_registration: required
  honest_validation: required
  notes: |
    Primary success = 5-fold CV AUC ≥ 0.85 on PXD046355 spectra
    re-processed in-house, using the locked 8-protein panel from
    Hällqvist Supp Table 3.

    Honest-failure: AUC < 0.7 = pipeline-divergence headline; this
    is a Tier-A negative finding and would imply that the published
    AUC depends sensitively on the original DIA-NN configuration.

    Cross-platform alignment (H4): 6/6 direction concordance with
    UKB Olink Gan 2025 is the gold standard. ≥ 4/6 with binomial
    p<0.05 is the loose acceptance.

    PPMI Project 196 (CSF + plasma SomaScan) is the natural
    out-of-PRIDE validation — but it is application-gated. The
    proposal explicitly does NOT depend on PPMI access; if OmiCairn
    has access at run time, H1 should additionally be tested on
    PPMI Project 196 plasma baseline.

figures_planned:
  - id: F1
    title: "PXD046355 raw → protein-intensity QC + reproduction of Hällqvist landscape"
    panels:
      - "Per-run total ion chromatogram + identification rate (DIA-NN output)"
      - "Per-protein CV across technical replicates (target: median CV < 20%)"
      - "Top-50 differentially abundant proteins (PD vs. HC and converter vs. non-converter) volcano — compare to Hällqvist Fig. 2"
      - "Reproduction concordance: our protein-level log2FC vs. Hällqvist Supplementary Table 1 (r and 95% CI)"
  - id: F2
    title: "8-protein panel re-validation (H1)"
    panels:
      - "5-fold CV ROC curve: locked 8-panel logistic regression on iRBD-converter vs. non-converter"
      - "Time-dependent AUC: 1, 3, 5, 7-year horizons"
      - "Per-fold prediction calibration"
      - "Performance breakdown: panel-only AUC vs. clinical-only AUC vs. combined"
  - id: F3
    title: "Per-protein hazard ratio reproduction (H2)"
    panels:
      - "Forest plot: 8 univariate HR vs. published reference, with sign-concordance annotation"
      - "Cox-model per-protein coefficient comparison (our re-derived vs. published)"
      - "Kaplan-Meier per-protein, dichotomised at median, log-rank p"
      - "Restricted cubic-spline plot per protein × HR (non-linearity check)"
  - id: F4
    title: "Sub-panel decomposition (H5)"
    panels:
      - "ROC per sub-panel (inflammation / proteostasis / lipid-signaling) separately"
      - "Variance-explained breakdown: how much of the panel signal is in each sub-panel"
      - "Shapley-value contribution of each protein to the full panel AUC"
      - "Sub-panel dropout sensitivity (leave-one-protein-out AUC)"
  - id: F5
    title: "Cross-platform alignment to UK Biobank Olink (H4)"
    panels:
      - "Side-by-side HR direction table: Hällqvist 8 (LC-MS) vs. Gan 2025 UKB Olink (where present)"
      - "Sign-concordance bar chart + binomial-test p"
      - "Per-protein effect-size scatter (LC-MS log2FC vs. Olink log HR)"
      - "Aggregate score: 6 overlapping proteins, composite z-score in published UKB Olink supplement"
  - id: F6
    title: "DDC sanity + sensitivity"
    panels:
      - "DDC protein abundance reproduction in PXD046355 — must be elevated in PD vs. HC (Bayati's headline finding)"
      - "Multiple-imputation sensitivity for missing values in DIA-NN output (10% / 25% / 40% missing thresholds)"
      - "Library-prediction sensitivity: DIA-NN library-free vs. predicted-library AUC"
      - "Sex-stratified panel AUC (PD has sex skew; check for collapse in subgroup)"

figures_not_in_scope:
  - "PPMI Project 196 individual-level analysis (application-gated; results table only if access)"
  - "Wet-lab follow-up validation of any protein"
  - "Plasma α-synuclein SAA replication (separate proposal)"
  - "CSF-vs-plasma cross-fluid comparison (PXD046355 is plasma only)"
  - "Functional pathway-level enrichment beyond the 8-protein panel"

budgets:
  max_phase_wall_minutes: 240
  max_total_wall_minutes: 1440
  allow_web: true
  claude_model: claude-sonnet-4-6
  max_phase_budget_usd: 25.0

auto_approve: true
---

# parkinson-plasma-prodromal-panel — Independent re-analysis of the Hällqvist 8-protein 7-year prodromal-PD plasma panel

## Source materials

- Hällqvist, J., Bartl, M., Dakna, M., ..., Mollenhauer, B.,
  Trenkwalder, C. *Plasma proteomics identify biomarkers predicting
  Parkinson's disease up to 7 years before symptom onset.* Nature
  Communications 15:4759 (2024). DOI: 10.1038/s41467-024-48961-3.
  PMC11189460.
- Gan, Y.-H., Ma, L.-Z., Zhang, Y., et al. *Large-scale proteomic
  analyses of incident Parkinson's disease reveal new
  pathophysiological insights and potential biomarkers.* Nature
  Aging 5:642 (2025). PMID 39979637.
- Bayati, A., Bauer, C., Bashir, A., ..., Mollenhauer, B.,
  Greicius, M.D., Wyss-Coray, T. *Comprehensive proteomics of CSF,
  plasma, and urine identify DDC and other biomarkers of early
  Parkinson's disease.* Acta Neuropathologica 147:52 (2024).
  PMC10927779.

PDFs in `claude_docs/claude_2026-05-21/parkinson_research/pdfs/`
(hallqvist_2024_NatComm.pdf, bayati_2024_ActaNeuro.pdf).

## Why this matters for OmiCairn

This is the proteomic counterpart to the brca-honest-validation
test case: a published panel, a public MS spectrum deposit, an
independent supplementary table for external alignment, and a
clearly published baseline (clinical-only). It exercises OmiCairn's
ability to:

1. **Process raw mass-spectrometry spectra** (DIA-NN) — outside
   the snRNA-seq / GWAS comfort zone the prior test cases
   focused on.
2. **Pre-register a locked panel and not retrain** during
   validation.
3. **Cross-platform replication** via published supplementary
   tables (Olink) when individual-level UKB data are gated.
4. **Sub-panel decomposition** (H5) without falling into the
   "throw all features at it" trap.

## Known prior results (cross-check)

| Claim | Reference |
|---|---|
| 8-protein plasma panel AUC up to 1.0 on 7-year iRBD prodromal | Hällqvist 2024 Fig. 4 |
| DDC consistently up in CSF / urine of de-novo PD | Bayati 2024 |
| 38-protein UKB Olink panel for incident-PD, AUC 0.887 (5-yr), 0.802 (PPMI ext) | Gan 2025 |
| 6 of Hällqvist's 8 proteins are on UKB Olink Explore 1536 | Cross-reference of panels |

## Out of scope

- UKB Olink individual-level analysis.
- PPMI Project 196 individual-level analysis.
- CSF and urine proteomics (we're plasma-only in PXD046355).
- α-synuclein SAA cross-modality (separate proposal).
- Single-cell / spatial proteomics integration.
