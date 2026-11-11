---
research_mode: HYBRID
domain: pd_trajectory_summary_table_inference
topic_slug: parkinson-multimodal-ppmi-trajectory

research_question: |
  Using only the publicly summarised longitudinal endpoints from
  PPMI — CSF α-syn-SAA Vmax/Tmax/AUC group means (Russo 2025 Lancet
  Neurology supplement), Olink Explore top-50-protein medians
  (Bayati 2024 Acta Neuropathologica supplement), motor MDS-UPDRS-
  III group-mean curves (Marek 2018 + downstream PPMI publications),
  free-water dMRI per-visit means (Burciu 2023 Frontiers Neurology
  supplement), LRRK2-G2019S SAA-stratified curves (Simuni 2024
  Movement Disorders supplement) — can a Gaussian-process latent-
  variable model (GPLVM) infer a 1-D disease-time axis whose
  ordering recapitulates clinically known PD progression milestones?

  PPMI raw individual-level data are under a Data Use Agreement
  (DUA). Trajectory inference papers that re-derive from raw data
  cannot be reproduced without the DUA. This proposal asks the
  inverse methodological question: *how much disease-trajectory
  reconstruction is possible from supplementary-table summary
  statistics alone?* If the answer is "a lot", this is a
  reproducibility win and a template for PPMI-DUA-free research.
  If "very little", it puts a quantitative upper bound on
  summary-table-only PD work and justifies the DUA application.

  Hypotheses (pre-registered):

    H1 (primary — disease-time correlates with time-from-diagnosis):
        The GPLVM-inferred 1-D latent disease-time axis correlates
        with published per-visit time-from-diagnosis at the
        cohort-summary level with Spearman ρ > 0.6 (n ≥ 50
        visit-aggregated points across baseline / Y1 / Y2 / Y3 /
        Y5 timepoints × multiple cohort-subgroup strata).

    H2 (secondary — modality leave-one-out generalisation):
        Free-water dMRI (Burciu 2023) is held out; GPLVM trained
        on SAA + Olink + MDS-UPDRS only recovers free-water dMRI
        rank-order with ρ ≥ 0.4. (Tests that disease-time is
        modality-general, not modality-specific.)

    H3 (secondary — LRRK2 leftward-shift):
        LRRK2-G2019S carriers (Simuni 2024) have a leftward-
        shifted inferred disease-time distribution vs. sporadic
        PD at matched clinical-stage (Cohen's d > 0.5,
        two-sample Welch t-test p < 0.05). Consistent with the
        SAA-negative LRRK2 subtype being molecularly distinct.

    H4 (secondary — modality-specific weight check):
        Report per-modality GPLVM loading magnitude on the
        primary latent factor. No single modality should
        contribute > 70% of the loading. If one does, the
        "multi-modal" claim is misleading and that modality
        alone would do.

    H5 (secondary — supplementary-table fetchability audit):
        Of the 6 cited supplements (Russo, Bayati, Hällqvist,
        Burciu, Simuni, Marek), how many are (a) machine-
        readable (TSV / XLSX), (b) PDF-table only, (c) image
        only / not fetchable? This number itself is an informative
        methodological finding about PD trajectory work
        reproducibility.

  Null payoff. If summary tables don't encode enough information
  to reconstruct disease time (H1 ρ < 0.4), the PPMI raw-data DUA
  application is justified — and the proposal would put a hard
  number on what summary-table-only PD trajectory work can
  deliver.

data:
  location: ./research_cases/parkinson-multimodal-ppmi-trajectory/data
  shared_cache: ./datasets
  description: |
    **No pre-staged raw data on disk.** Fetch into
    `./datasets/<accession>/`. Total ≈ 200 MB — by far the
    smallest v2 footprint.

    **PPMI summary-table sources (all public-access PMC):**

      - **Russo 2025 Lancet Neurology supplement.**
        CSF α-syn-SAA Vmax / Tmax / AUC group means by cohort
        × visit × LRRK2 status.
          DOI 10.1016/S1474-4422(25)00094-2 (provisional).
        Target path: ./datasets/russo_2025_supp/

      - **Bayati 2024 Acta Neuropathologica supplement.**
        Olink + CSF + plasma + urine protein medians by visit
        and cohort.
          PMC10927779.
        Target path: ./datasets/bayati_2024_supp/

      - **Hällqvist 2024 PRIDE PXD046355 supplementary tables.**
        8-protein panel per-visit means.
          PMC11189460.
        Target path: ./datasets/hallqvist_2024_supp/
        Note: PRIDE PXD046355 is not needed; supplementary tables
        suffice for this proposal.

      - **Burciu 2023 Frontiers Neurology supplement.**
        Per-visit free-water dMRI group means.
          Frontiers Neurology open access.
        Target path: ./datasets/burciu_2023_supp/

      - **Simuni 2024 Movement Disorders supplement.**
        LRRK2-G2019S SAA-stratified group-mean curves.
        Target path: ./datasets/simuni_2024_supp/

      - **Marek 2018 PPMI baseline + downstream publications**
        MDS-UPDRS-III group-mean curves at Y1, Y3, Y5.
          Ann Clin Transl Neurol 5:1460.
        Target path: ./datasets/marek_2018_supp/

  cohorts:
    [ppmi_summary_baseline_y1_y3_y5,
     lrrk2_g2019s_simuni_2024]
  modalities: [csf_saa_summary, olink_protein_summary,
               clinical_mds_updrs_summary, freewater_dmri_summary,
               lrrk2_subgroup_summary]

success_criteria:
  primary_metric: spearman_rho_disease_time_vs_time_from_dx
  baseline: random_visit_order_null
  pre_registration: required
  honest_validation: required
  notes: |
    Primary success = GPLVM-inferred disease-time correlates with
    published time-from-diagnosis at Spearman ρ > 0.6 over n ≥ 50
    visit-aggregated points.

    Honest-failure: ρ < 0.4 = summary tables don't encode enough
    information to reconstruct disease time; informative upper
    bound on summary-table-only PD trajectory work.

    LRRK2 shift (H3) needs Cohen's d > 0.5; small effect size at
    this n-per-subgroup is the expected outcome.

    Fetchability audit (H5) is itself an output — must be reported
    even if H1 fails.

figures_planned:
  - id: F1
    title: "Supplementary-table fetchability audit + visit × measurement matrix"
    panels:
      - "Fetchability table: 6 supplements × {machine-readable / PDF-table / image / unreachable}"
      - "Visit × measurement coverage heatmap (rows = study, cols = visit, color = modality count)"
      - "Per-modality time-series traces (overlaid): SAA, Olink, MDS-UPDRS, free-water dMRI"
      - "Cohort-subgroup distribution: sporadic PD vs. LRRK2 vs. SAA+ vs. SAA-"
  - id: F2
    title: "GPLVM 1-D latent disease-time inference (H1)"
    panels:
      - "Inferred latent disease-time vs. time-from-diagnosis scatter (color = modality)"
      - "Spearman ρ point estimate + bootstrap 95% CI"
      - "Per-modality residual: which modality's points deviate most from the GPLVM latent"
      - "GP posterior uncertainty band per modality"
  - id: F3
    title: "Cross-modality generalisation (H2)"
    panels:
      - "Leave-modality-out ρ: SAA-out, Olink-out, MDS-UPDRS-out, dMRI-out (bar)"
      - "Held-out free-water dMRI ρ ≥ 0.4 test panel"
      - "Predicted vs. observed rank scatter for held-out modality"
      - "Per-modality information content (loading magnitude on latent)"
  - id: F4
    title: "LRRK2 leftward-shift (H3) + modality-weight check (H4)"
    panels:
      - "Disease-time histogram: LRRK2-G2019S vs. sporadic PD overlay"
      - "Cohen's d + Welch t-test p-value annotation"
      - "Per-modality loading magnitude on the 1-D latent (bar) — flag if any > 70%"
      - "Sensitivity: GPLVM with each modality dropped — ρ change"
  - id: F5
    title: "Honest-failure panel + methodological-bound statement"
    panels:
      - "Random visit-order permutation null: ρ distribution"
      - "Quantitative upper bound: summary-table-only ρ vs. (hypothetical) raw-data ρ from published PPMI re-analyses"
      - "Per-supplementary-table information loss (text-only vs. tabular)"
      - "If H1 fails: explicit DUA-justification rationale plot"

figures_not_in_scope:
  - "PPMI raw individual-level data (DUA-gated; not used)"
  - "Wet-lab biomarker validation"
  - "GP hyperparameter MCMC posterior inference"
  - "OCT / RBD / other PPMI modalities not in cited supplements"

budgets:
  max_phase_wall_minutes: 240
  max_total_wall_minutes: 1440
  allow_web: true
  claude_model: claude-sonnet-4-6
  max_phase_budget_usd: 25.0

auto_approve: true
---

# parkinson-multimodal-ppmi-trajectory — Summary-table-only GPLVM disease-time inference

## Source materials

- Russo, M.J., Concha-Marambio, L., Tarutani, A., et al. *Plasma
  and CSF α-synuclein seed amplification assay results in PPMI:
  the long-term Vmax / Tmax / AUC study.* Lancet Neurology (2025).
- Bayati, A., Bauer, C., Bashir, A., et al. *Comprehensive
  proteomics of CSF, plasma, and urine identify DDC and other
  biomarkers of early PD.* Acta Neuropathologica 147:52 (2024).
- Hällqvist, J., Bartl, M., Dakna, M., et al. *Plasma proteomics
  identify biomarkers predicting Parkinson's disease up to 7
  years before symptom onset.* Nat Commun 15:4759 (2024).
- Burciu, R.G., Ofori, E., Vaillancourt, D.E. *Imaging
  microstructure in Parkinson's disease across the spectrum from
  prodromal to advanced stages.* Frontiers Neurology (2023).
- Simuni, T., Chahine, L.M., Poston, K., et al. *A biological
  definition of Parkinson's disease (LRRK2-G2019S SAA-stratified
  longitudinal follow-up).* Movement Disorders (2024).
- Marek, K., Chowdhury, S., Siderowf, A., et al. *The
  Parkinson's Progression Markers Initiative: cohort design,
  baseline, and longitudinal characteristics.* Ann Clin Transl
  Neurol 5:1460 (2018).

## Why this matters for OmiCairn

This is the v2 batch's only longitudinal-trajectory case AND the
only case that explicitly stays inside supplementary-table
territory (no individual-level data). It exercises OmiCairn's
ability to:

1. **Read scientific PDFs / XLSX supplementary tables** as
   primary data sources — a skill the v1 batch did not stress.
2. **Handle a tiny, heterogeneous dataset** (200 MB) where the
   methodological discipline is *not* about scale but about
   what is encodable.
3. **Pre-register a quantitative upper bound** on a research
   modality's information content (H5 audit).
4. **GPLVM with low-data regularisation** — the dominant
   failure mode the v2 risk register flags as PLAN-phase work.

## Known prior results (cross-check)

| Claim | Reference |
|---|---|
| PPMI shows progressive SAA Vmax decline over Y0-Y5 in sporadic PD | Russo 2025 |
| 8-plasma-protein panel predicts iRBD-to-PD conversion AUC ≈ 1.0 | Hällqvist 2024 |
| Free-water dMRI increases monotonically with PD progression | Burciu 2023 |
| LRRK2-G2019S carriers often SAA-negative; molecularly distinct subtype | Simuni 2024 |
| MDS-UPDRS-III progresses ≈ 2-3 points/year in early PD | Marek 2018 |

## Out of scope

- PPMI raw individual-level data (DUA-gated).
- Wet-lab biomarker validation.
- MCMC posterior inference on GP hyperparameters.
- Non-cited PPMI modalities (OCT, RBD, dopamine PET).

## Implementation choices

- Manual TSV-conversion of 6 supplementary tables before EXECUTE
  starts; cache in `data/manual/`. PDF supplements that are
  images are flagged in H5 fetchability audit, not converted.
- GPLVM via `GPy.models.GPLVM` with 1-D latent; ARD kernel; 50
  optimisation steps; if GPy is unavailable, fall back to
  `sklearn.decomposition.FactorAnalysis` with the same
  visit-mean matrix (and report this in Limitations).
- Per-visit summary "datapoint" = mean ± SD at (baseline, Y1, Y2,
  Y3, Y5) × cohort-subgroup; standardise each column before fit.
- LRRK2 shift (H3) is computed at matched clinical-stage (MDS-
  UPDRS-III binned) to control for stage confounding.
