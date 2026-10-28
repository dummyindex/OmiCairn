---
research_mode: DISCOVERY
domain: neurodegeneration_neuroimmunology_scRNAseq
topic_slug: parkinson-tcell-foothold

research_question: |
  Ma, J., Al-Dalahmah, O., Goldman, J.E., Sulzer, D., Sims, P.A.
  et al. (Nat Commun s41467-025-62478-3, 2025; PMC12322057) reported
  spatially resolved adaptive immunity in Parkinson's disease
  substantia nigra: combined snRNA-seq + 10x Visium + scTCR-seq
  across 44 donors (13 PD + 15 ctrl SN; 10 PD + 8 ctrl cingulate;
  265,284 nuclei + 10 donors with Visium) identified clonally
  expanded CD8+ T cells whose TCRs share homology with α-synuclein
  peptides and which spatially co-localise with CD44+ reactive
  astrocytes in PD SN.

  The Ma 2025 result depends on rare TCR-seq data, which the
  Kamath 2022 (GSE178265), Wang 2024 (GSE184950), Adams 2024
  (GSE193688) and Fullard 2024 multi-region atlases do not have.
  The question is whether the transcriptomic *footprint* of the
  Ma-2025 T-cell signature (the genes co-expressed in the clonally
  expanded CD8+ population) can be detected in those four
  independent atlases, in a deconvolution-inferred T-cell or
  lymphocyte cluster, without TCR ground truth.

  Hypotheses (pre-registered):

    H1 (primary — transcriptomic footprint replicates in
         independent PD snRNA-seq atlases):
        The Ma 2025-defined T-cell signature (extracted from
        Supplementary Data of PMC12322057 — the top-50
        differentially expressed genes in the clonally expanded
        CD8+ population vs. all other immune nuclei in PD SN) is
        scored per cell in T-cell / lymphocyte clusters of:
          - Kamath 2022 (GSE178265) SNpc + caudate
          - Wang 2024 (GSE184950) SNpc
          - Adams 2024 (GSE193688) SN
          - Fullard 2024 SNpc + M1 (CELLxGENE d5d0df8f-...)
        and tested for PD-vs-control per-donor pseudobulk
        differential at FDR<0.1. At least 3 of 4 atlases show
        log2FC > 0 in the T-cell-module score in the PD direction.

    H2 (secondary — region specificity within multi-region atlas):
        Within the Fullard atlas (the only multi-region atlas in
        the panel), the T-cell-module signal is significantly
        higher in SNpc + cingulate-like cortical regions than in
        DMNX (Braak 1, vagal-driven, no expected adaptive immune
        component) or V1 (Braak 6, late-stage, no expected
        ongoing immune infiltration). Pre-registered ranking:
        SNpc > M1 > V1 ≈ DMNX. Spearman ρ on rank vs. signature
        score > 0.5, permutation p < 0.05.

    H3 (secondary — T-cell-module × DA-loss covariation):
        At the donor level, in Kamath / Wang / Adams / Fullard
        atlases, the T-cell-module pseudobulk score is positively
        correlated with the AGTR1+ DA-loss score (computed
        identically to parkinson-da-vulnerability proposal):
        donors with more T-cell signal also have more vulnerable-
        DA loss. Spearman ρ > 0.3, permutation p < 0.05 in ≥ 2
        of 4 atlases.

    H4 (secondary — astrocyte co-localisation in spatial slabs):
        Where Visium-spatial data is available (Martirosyan A5,
        Ma 2025 itself), spatial spots scoring high for the
        T-cell module are *also* enriched for CD44+ / GFAP-high
        reactive astrocyte markers (Ma 2025's reported
        co-localisation), with Spearman ρ > 0.3 on per-spot
        z-scored module scores.

    H5 (secondary — non-α-syn-homology bound — negative control):
        We pre-register a non-PD-relevant T-cell sub-signature
        (a generic exhaustion module: PDCD1, HAVCR2, LAG3, TOX,
        TIGIT) as a negative control. The exhaustion module
        should NOT show preferential PD enrichment (this signal
        is canonical to tumor immunology and PD T cells are not
        exhausted in the same way). A positive enrichment of the
        exhaustion module would suggest the H1 signal is a
        non-specific "T cell present" signal rather than a
        PD-specific clonally-expanded program.

  A null result on H1 is informative: it would mean that without
  TCR-seq ground truth, the clonal-expansion signal in Ma 2025 is
  not detectable from gene expression alone — calling into question
  any retrospective inference of adaptive immunity from existing
  PD snRNA-seq atlases.

data:
  location: ./research_cases/parkinson-tcell-foothold/data
  shared_cache: ./datasets
  description: |
    **No pre-staged raw data on disk.** All datasets fetched into
    `./datasets/<accession>/` per the on-the-way protocol.

    **Primary datasets:**

      - **Ma 2025 PD SN + cingulate (signature derivation).**
        Nature Communications data availability section
        (PMC12322057). Accessions to confirm during fetch from the
        deposit:
          - bioRxiv preprint v3 with full code:
            https://www.biorxiv.org/content/10.1101/2024.01.08.574736v3
          - PMC: https://pmc.ncbi.nlm.nih.gov/articles/PMC12322057/
          - Likely GEO / Synapse accession is referenced in the
            paper's "Data Availability" — EXECUTE must read this
            section and fetch accordingly. If the accession is
            absent or unreachable, fall back to the supplementary
            T-cell-module gene list which IS deposited as
            Supplementary Data file with the paper.
        Target path: ./datasets/ma_2025_SN_Tcell/

      - **Kamath 2022 SNpc + caudate** — GEO GSE178265 /
        SCP1768. Target path: ./datasets/GSE178265/
      - **Wang 2024 SNpc** — GEO GSE184950. Target path:
        ./datasets/GSE184950/
      - **Adams 2024 SN multiome (RNA arm)** — GEO GSE193688.
        Target path: ./datasets/GSE193688/
      - **Fullard 2024 multi-region (SNpc + M1 + V1 + DMNX)** —
        CELLxGENE d5d0df8f-... Target path:
        ./datasets/cellxgene_d5d0df8f_fullard/
      - **Martirosyan 2024 (Visium for H4)** — GEO GSE243639
        Visium portion. Target path: ./datasets/GSE243639/

      **Reference databases:**
      - **MSigDB Hallmark + Reactome immune programs** for the
        exhaustion module (H5):
          https://www.gsea-msigdb.org/gsea/msigdb/human/
        Target path: ./datasets/msigdb/

  cohorts:
    [ma_2025_signature_derivation,
     kamath_2022_SNpc,
     wang_2024_SNpc,
     adams_2024_SN,
     fullard_2024_multi_region,
     martirosyan_2024_visium]
  modalities: [snRNA_seq_10x, scTCR_seq, visium_spatial]

success_criteria:
  primary_metric: tcell_signature_replication_rate
  baseline: shuffled_pd_label_null
  pre_registration: required
  honest_validation: required
  notes: |
    Primary success = ≥ 3 of 4 PD snRNA-seq atlases show
    PD-vs-ctrl log2FC > 0 in the T-cell-module score at FDR < 0.1
    on donor-pseudobulk.

    Honest-failure: < 3 atlases replicate = the Ma 2025 T-cell
    signature is detectable only with TCR-seq ground truth, not
    from gene expression alone. This is biologically informative
    and must be reported as the headline.

    Negative-control specificity (H5): if a generic exhaustion
    module enriches under H1 logic, the H1 signal is suspect.

figures_planned:
  - id: F1
    title: "Ma 2025 T-cell signature definition + decomposition"
    panels:
      - "UMAP of Ma 2025 immune cells colored by clonally expanded vs. non-expanded"
      - "Top-50 T-cell signature genes (volcano) and functional annotation (cytotoxicity, granzymes, IFN-γ, MHC-I)"
      - "Sub-signature decomposition: cytotoxic core, IFN-γ response, MHC-I, exhaustion (negative control), antigen-presentation"
      - "Sanity check: signature score in Ma 2025 self — PD > ctrl in T-cell cluster (positive-control bar)"
  - id: F2
    title: "T-cell-module replication across 4 independent atlases (H1)"
    panels:
      - "Per-atlas T-cell / lymphocyte cluster identification — UMAP highlight + CD3D/CD3E/CD8A marker plot"
      - "Per-donor pseudobulk T-cell-module score boxplots PD vs. ctrl × atlas (Kamath / Wang / Adams / Fullard)"
      - "Forest plot of effect-size + FDR per atlas"
      - "Concordance: log2FC Ma-defined vs. atlas-derived, scatter with r"
  - id: F3
    title: "Region specificity in Fullard atlas (H2)"
    panels:
      - "Per-region (DMNX / SNpc / GPi / M1 / V1) T-cell-module pseudobulk score, PD vs. ctrl"
      - "Pre-registered rank correlation: pre-stated order SNpc > M1 > V1 ≈ DMNX, vs. observed"
      - "Per-donor heatmap: rows = donors (PD only), cols = regions, color = T-cell module score"
      - "Negative-region control: V1 (Braak 6) should show no PD-vs-ctrl T-cell signal — confirm or report deviation"
  - id: F4
    title: "T-cell × DA-loss covariation (H3)"
    panels:
      - "Per-donor scatter: T-cell-module score (x) vs. AGTR1+ DA depletion score (y) per atlas"
      - "Spearman ρ with bootstrap CI per atlas"
      - "Mixed-effects model: atlas as random effect, T-cell ~ DA-loss + age + sex"
      - "Sensitivity to outlier donors via leave-one-out ρ"
  - id: F5
    title: "Spatial T-cell × astrocyte co-localisation (H4)"
    panels:
      - "Visium spots in Martirosyan + Ma 2025 colored by T-cell module + CD44+ astrocyte module overlay"
      - "Per-spot Spearman ρ (T-cell × astrocyte) with permutation null"
      - "Spatial clustering: T-cell-high vs. astrocyte-high spots — neighbourhood enrichment"
      - "PD-vs-ctrl spot-level signature gain map"
  - id: F6
    title: "Negative-control exhaustion module (H5) + sensitivity"
    panels:
      - "Exhaustion module (PDCD1 / HAVCR2 / LAG3 / TOX / TIGIT) PD-vs-ctrl per atlas — should NOT enrich"
      - "Pseudobulk vs. per-cell DE comparison — which gives the H1 signal? (pseudobulk preferred to avoid inflated p)"
      - "Cell-number requirement sensitivity: ≥ 30 / 100 / 300 T-cells per donor — H1 robust to threshold"
      - "Subsampling sensitivity: random 50% donors per atlas — H1 stable?"

figures_not_in_scope:
  - "scTCR-seq re-clonotyping (we use Ma's deposited results)"
  - "Wet-lab α-syn peptide MHC-I binding"
  - "Mouse PFF + TCR sequencing experiment"
  - "T-cell × astrocyte interactome at cell-pair resolution (deconvolution only)"
  - "Treatment response (steroid / immunomodulator) prediction"

budgets:
  max_phase_wall_minutes: 240
  max_total_wall_minutes: 1440
  allow_web: true
  claude_model: claude-sonnet-4-6
  max_phase_budget_usd: 25.0

auto_approve: true
---

# parkinson-tcell-foothold — Transcriptomic footprint of α-synuclein-reactive CD8+ T cells across PD snRNA-seq atlases

## Source materials

- Ma, J., Al-Dalahmah, O., Goldman, J.E., Sulzer, D., Sims, P.A.,
  et al. *The spatial landscape of glial pathology and T cell
  response in Parkinson's disease substantia nigra.* Nature
  Communications 16, s41467-025-62478-3 (2025). PMC12322057.
- Kamath T. et al. *Single-cell genomic profiling of human dopamine
  neurons.* Nat Neurosci 25:588 (2022).
- Wang Q. et al. *Molecular profiling of human substantia nigra.*
  Sci Adv 10:eadi8287 (2024).
- Adams L., Song M.K. et al. *Single-nuclei paired multiomic
  analysis of the human midbrain.* Nat Aging 4:364 (2024).
- N M P., Fullard J.F., et al. *Multi-region single nucleus
  transcriptomic atlas of Parkinson's disease.* Sci Data 11:1274
  (2024).
- Martirosyan A. et al. *Unravelling cell type-specific responses
  to Parkinson's disease at single cell resolution.* Mol Neurodegen
  19:7 (2024).

PDFs in `claude_docs/claude_2026-05-21/parkinson_research/pdfs/`
(ma_2025_NatComm.pdf, kamath_2022_NatNeuro.pdf, wang_2024_SciAdv.pdf,
nm_2024_atlas_SciData.pdf).

## Why this matters for OmiCairn

This proposal exercises *cross-modality inference* — can a
signature defined with TCR-seq be detected in datasets that lack
TCR-seq? The answer matters scientifically (is adaptive immunity
in PD invisible without TCR ground truth?) and methodologically
(can OmiCairn robustly score modules across atlases with different
QC, cell-type labels, and resolutions?).

OmiCairn challenges:

1. **Module-score harmonisation across atlases** with different
   normalisations.
2. **Pseudobulk-vs-per-cell DE discipline** — per-cell DE inflates
   significance; OmiCairn must default to pseudobulk and only fall
   back to per-cell when justified.
3. **Negative-control pre-registration (H5)** — most "T-cell
   infiltration in disease" papers don't pre-register negative
   controls; OmiCairn must.
4. **Cross-modality spatial validation (H4)** — Visium-spot module
   correlation, separate from snRNA-seq module score.

## Known prior results (cross-check)

| Claim | Reference |
|---|---|
| Clonally expanded CD8+ T cells with α-syn-homologous TCRs in PD SN | Ma 2025 |
| Spatial co-localisation with CD44+ reactive astrocytes | Ma 2025 Fig. 4 |
| T-cell signature absent from Kamath / Wang / Adams atlas headlines | Inferred (TCR-seq missing) |

## Out of scope

- TCR re-clonotyping from raw FASTQ (use Ma's deposited clonotypes).
- HLA-typing of donors (donor-level genotypes not always available).
- Functional immunology experiments.
- Treatment-response prediction.
- iPSC-derived T-cell experiments.
