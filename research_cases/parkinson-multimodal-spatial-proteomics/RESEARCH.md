---
research_mode: DISCOVERY
domain: neurodegeneration_multimodal_integration
topic_slug: parkinson-multimodal-spatial-proteomics

research_question: |
  Does a MOFA+ (Multi-Omics-Factor-Analysis) joint embedding of
  three modalities of PD substantia-nigra data — Martirosyan 2024
  snRNA-seq + Visium (GSE243639), Ma 2025 GeoMx spatial
  (PMC12322057), and Zhu 2024 LC-MS/MS DLPFC proteomics
  (PRIDE PXD030142) — reveal factor(s) that ONLY appear when all
  three modalities are jointly modelled, and that are unrecoverable
  from any pairwise integration?

  Multi-modal integration has become a default expectation in PD
  research, but the value-add of going from 2-modal to 3-modal is
  rarely tested with a clean ablation. Pairwise integrations in PD
  exist: snRNA × Visium (Martirosyan 2024); snRNA × proteomics
  (Zhu 2024); spatial × CosMx (Goralski 2024). Joint 3-modal MOFA+
  on PD-SN has not been published. This OmiCairn run pre-registers the
  ablation that asks whether the third modality is necessary or
  decorative.

  Hypotheses (pre-registered):

    H1 (primary — all-modalities factor exists):
        ≥ 1 MOFA+ factor explains > 5% variance in EACH of the
        three modalities simultaneously, AND is associated with PD
        diagnosis (per-donor-group Wilcoxon p < 0.05). Pre-
        registered name: "all-modalities factor".

    H2 (secondary — proteomics necessity ablation):
        Removing the proteomics modality and re-fitting MOFA+
        produces a closest-matching factor (max absolute loading
        correlation with the H1 factor) whose explained variance
        in the spatial modality drops by ≥ 30 percentage points.
        Proteomics is NECESSARY for the joint factor, not
        nice-to-have.

    H3 (secondary — top-loaded gene enrichment):
        Top-100 loaded genes of the all-modalities factor are
        enriched (q < 0.05 hypergeometric) for the union of:
        Goralski 2024 LAMDA cortical signature genes, AND Hemedan
        2024 Boolean-PD "key-regulator" gene list.

    H4 (secondary — modality-balance check):
        No single modality contributes > 60% of the explained
        variance summed across the H1 factor (otherwise the
        "all-modalities" label is misleading).

    H5 (secondary — pairwise comparison):
        Repeat the analysis with three pairwise MOFA+ models —
        (snRNA + Visium), (snRNA + proteomics), (Visium +
        proteomics). Test whether any pairwise model recovers a
        factor with ≥ 0.7 absolute loading correlation to the
        H1 3-modal factor. If yes, the 3-modal claim is weaker
        than headline. If no, 3-modal is necessary.

  Null payoff. A null all-modalities factor (H1: no factor >5%
  variance in all three modalities, or no PD association) means
  PD SN biology is dominated by the strongest single modality
  (probably snRNA-seq) and multi-omics doesn't add. Canonical
  ablation answer the field has been asking.

data:
  location: ./research_cases/parkinson-multimodal-spatial-proteomics/data
  shared_cache: ./datasets
  description: |
    **No pre-staged raw data on disk.** Fetch into
    `./datasets/<accession>/`. Total ~18 GB.

    **snRNA-seq + Visium:**

      - **Martirosyan 2024 GEO GSE243639** (~10 GB; snRNA + Visium).
          https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE243639
        Target path: ./datasets/GSE243639/

    **GeoMx spatial:**

      - **Ma 2025 GeoMx data** via NCBI / PMC12322057 deposit
        (link in Data Availability statement); ~5 GB.
        Target path: ./datasets/ma_2025_geomx/
        Note: if Ma 2025 GeoMx not fetchable, fall back to
        Goralski 2024 Zenodo 10.5281/zenodo.10729767 cingulate
        GeoMx + CosMx (different region but same modality).

    **Proteomics:**

      - **Zhu 2024 DLPFC LC-MS/MS proteomics** PRIDE PXD030142
        (~3 GB).
          https://www.ebi.ac.uk/pride/archive/projects/PXD030142
        Target path: ./datasets/PXD030142_zhu/

    **Reference databases (enrichment in H3):**

      - **Goralski LAMDA** Zenodo 10.5281/zenodo.10729767.
        Target path: ./datasets/goralski_lamda/

      - **Hemedan 2024 Boolean-PD key-regulator gene list.**
        GitLab `gitlab.lcsb.uni.lu/lcsb-biocore/publications/
        hemedan23-boolean-modelling-of-pd`.
        Target path: ./datasets/hemedan_2024_boolean/

    **Spatial deconvolution reference:**

      - **cell2location** Python package (public PyPI).
        Used to deconvolute Visium / GeoMx spots into per-spot
        per-cell-type abundances using Martirosyan snRNA-seq as
        reference profiles.

  cohorts:
    [martirosyan_2024_snrna_visium,
     ma_2025_geomx_or_goralski_fallback,
     zhu_2024_dlpfc_proteomics]
  modalities: [snRNA_seq_10x, visium_spatial, geomx_spatial,
               lc_ms_ms_proteomics]

success_criteria:
  primary_metric: all_modalities_factor_count_and_PD_association
  baseline: best_pairwise_MOFA_model
  pre_registration: required
  honest_validation: required
  notes: |
    Primary success = at least 1 MOFA+ factor explains >5%
    variance in ALL three modalities AND is PD-associated
    (Wilcoxon p < 0.05).

    Honest-failure: no all-modalities factor exists, OR best
    pairwise MOFA+ factor recovers the same biology with
    correlation ≥ 0.7 (H5). Both outcomes are informative
    ablations.

    Modality balance (H4): no modality > 60% explained-variance
    contribution. Otherwise headline must say "X-modality-dominant
    factor with the other two adding noise".

figures_planned:
  - id: F1
    title: "Per-modality QC + per-donor pseudobulk feature matrix"
    panels:
      - "snRNA-seq Martirosyan: PD vs. ctrl UMAP, cell-type pseudobulk size"
      - "Visium + GeoMx: cell2location deconvolution per-spot per-cell-type"
      - "Zhu 2024 proteomics: per-donor protein-intensity matrix QC"
      - "Common-axis representation: 3 modalities × patient-group axis"
  - id: F2
    title: "MOFA+ 3-modal joint embedding (H1)"
    panels:
      - "Explained variance per modality per factor heatmap (top-20 factors)"
      - "All-modalities factor candidates: % variance in each modality bar"
      - "PD association test per factor (Wilcoxon p), top-20 factors ranked"
      - "Per-factor donor-group projection scatter"
  - id: F3
    title: "Proteomics-necessity ablation (H2) + modality balance (H4)"
    panels:
      - "Ablation: drop proteomics, refit MOFA+, identify closest factor"
      - "Δ-explained-variance per modality after proteomics removed"
      - "Modality-balance pie: % explained variance per modality for H1 factor"
      - "If H4 fails: which modality dominates and what does the factor really represent"
  - id: F4
    title: "Top-loaded gene enrichment (H3) + biological interpretation"
    panels:
      - "Top-100 loaded genes of H1 factor: barplot of loading magnitude"
      - "Hypergeometric enrichment vs. LAMDA + Hemedan key-regulator union (q-value)"
      - "GO biological-process top terms (q < 0.05)"
      - "Per-cell-type loading: which cell types contribute most to the factor"
  - id: F5
    title: "Pairwise vs. 3-modal comparison (H5) + honest-failure panel"
    panels:
      - "Loading correlation: H1 (3-modal) factor vs. best pairwise factor across 3 pairings"
      - "Per-pairwise-model AUC for PD-vs-ctrl donor classification"
      - "Decision matrix: 3-modal necessary vs. pairwise-sufficient"
      - "Permutation null: shuffled donor-group labels, MOFA+ refit; H1 factor existence rate"

figures_not_in_scope:
  - "Wet-lab proteomic validation"
  - "Single-cell proteomics (not yet available for PD-SN at scale)"
  - "GeoMx raw probe-level re-analysis (use deposited pseudo-counts)"
  - "Cross-region joint MOFA (we restrict to SN-region modalities)"

budgets:
  max_phase_wall_minutes: 240
  max_total_wall_minutes: 1440
  allow_web: true
  claude_model: claude-sonnet-4-6
  max_phase_budget_usd: 25.0

auto_approve: true
---

# parkinson-multimodal-spatial-proteomics — Joint MOFA+ embedding of PD snRNA-seq + Visium/GeoMx + LC-MS/MS

## Source materials

- Martirosyan, A., Ansari, R., Pestana, F., et al. *Unravelling
  cell type-specific responses to Parkinson's disease at single
  cell resolution.* Mol Neurodegen 19:7 (2024). GEO GSE243639.
- Ma, J., Patel, S., et al. *Spatial and single-nuclei
  transcriptomic landscape of the Parkinson's disease substantia
  nigra implicates clonally expanded CD8+ T cells with
  α-syn-homologous T-cell receptors.* Nat Commun 16 (2025).
  PMC12322057.
- Zhu, Y., Chen, X., et al. *DLPFC LC-MS/MS proteomics across PD
  vs. control donors.* PRIDE PXD030142 (2024). [Author / journal
  details to be confirmed during READ.]
- Argelaguet, R., Velten, B., Arnol, D., et al. *MOFA+: a
  statistical framework for comprehensive integration of multi-
  modal single-cell data.* Genome Biol 21:111 (2020).
- Goralski et al. Nat Commun 15:2642 (2024). LAMDA.
- Hemedan et al. iScience 27:110956 (2024). Boolean-PD modules.
- Kleshchevnikov, V., Shmatko, A., et al. *Cell2location maps
  fine-grained cell types in spatial transcriptomics.* Nat
  Biotechnol 40:661 (2022).

## Why this matters for OmiCairn

This case tests an ablation question the multi-modal-integration
field rarely answers cleanly: *is the third modality necessary?*
OmiCairn exercises:

1. **Three-modality MOFA+ with non-overlapping donors** — the
   shared axis is patient-group (PD vs. ctrl), not patient-ID.
2. **cell2location spot deconvolution** to make Visium / GeoMx
   inter-operable with snRNA-seq features.
3. **Pre-registered ablation** (H2) — drop the proteomics
   modality and quantify the Δ-explained-variance.
4. **Comparison to best pairwise model** (H5) — answers "is
   3-modal really better than 2-modal" cleanly.

## Known prior results (cross-check)

| Claim | Reference |
|---|---|
| MOFA+ recovers joint factors when feature spaces overlap | Argelaguet 2020 |
| Martirosyan snRNA × Visium reveals cell-type-specific PD changes | Martirosyan 2024 |
| Zhu 2024 DLPFC proteomics: hundreds of PD-altered proteins | Zhu 2024 |
| cell2location accurate for Visium when reference snRNA matches tissue | Kleshchevnikov 2022 |

## Out of scope

- Wet-lab proteomic validation.
- Single-cell proteomics (not yet PD-SN scale).
- Raw GeoMx probe-level re-analysis (deposited pseudo-counts only).
- Cross-region joint MOFA (SN-only).

## Implementation choices

- Patient-group as shared MOFA+ axis (no donor matching feasible
  across cohorts).
- cell2location deconvolution: Martirosyan snRNA → Visium / GeoMx
  spot per-cell-type abundance, then per-donor aggregate.
- MOFA+ via `mofapy2` 0.7.1 (per v2 risk register's pinned
  package version).
- Per-modality variance threshold for H1 = 5% in EACH modality
  (the canonical MOFA+ joint-factor criterion).
- Fallback: if Ma 2025 GeoMx not fetchable, use Goralski 2024
  Zenodo cingulate GeoMx — same modality, different region;
  document in Limitations.
