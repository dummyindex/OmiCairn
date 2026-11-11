---
research_mode: DISCOVERY
domain: neurodegeneration_spatial_transcriptomics
topic_slug: parkinson-lamda-multiregion

research_question: |
  Goralski, T.M., Henderson, M.X. et al. (Nat Commun 15:2642, 2024;
  DOI 10.1038/s41467-024-47027-8; PMC10966039) defined the "Lewy-
  Associated Molecular Dysfunction from Aggregates" (LAMDA) spatial-
  transcriptomic signature in PD/PDD/DLB *cingulate* cortex —
  8 donors, NanoString GeoMx DSP whole-transcriptome + CosMx single-
  cell-resolved spatial profiling — and replicated the program in
  α-synuclein PFF-injected mouse cortex. They showed that
  pSyn-positive excitatory cortical neurons converge on a conserved
  dysfunction program (synaptic loss, mitochondrial dysfunction,
  proteostasis collapse, vesicle-trafficking failure).

  Goralski explicitly framed the result as cortically defined. It
  has *never* been tested whether LAMDA is detectable, in the same
  cell types or in different cell types, in *non-cortical*
  Lewy-pathology regions of Parkinson's brain — i.e., across the
  Braak progression from peripheral (DMNX), through midbrain (SNpc,
  GPi) to cortical (M1, V1) regions in the same donors.

  The Fullard / N M et al. multi-region snRNA-seq atlas (Sci Data
  11:1274, 2024; CELLxGENE collection d5d0df8f-4eee-49d8-a221-
  a288f50a1590) is the ideal test substrate: 100 donors (75 PD +
  25 control), 5 regions in 71 of those donors (DMNX, GPi, SNpc,
  M1, V1), 2.1M nuclei total. Cingulate cortex is NOT part of the
  Fullard atlas — meaning the Goralski signature can be tested on
  fully independent tissue (positive-control cross-tissue spec
  test) and on regions with different Braak progression stages.

  Hypotheses (pre-registered):

    H1 (primary — LAMDA generalises to non-cortical regions):
        Score the published Goralski LAMDA gene module (extracted
        from Goralski Supplementary Data 1 — top-200 upregulated
        genes in cortical pSyn+ neurons; Zenodo
        10.5281/zenodo.10729767) as a per-cell module score
        across all 5 regions × all ~10 major cell types of the
        Fullard atlas. Per-(region × cell-type) PD-vs-control mean
        score difference is tested with linear mixed-effects model
        (donor as random intercept; sex + age fixed). After BH
        correction over the 50 tests, at least 3 NON-CORTICAL
        (region × cell-type) combinations show positive
        differential LAMDA enrichment at FDR < 0.05, with effect-
        size > 0.25 SD (Cohen's d).

    H2 (secondary — Braak-stage tracking):
        The LAMDA-enrichment gradient across regions in PD follows
        Braak progression: DMNX (Braak 1) > SNpc (Braak 3) > GPi
        (Braak 4) > M1 (Braak 5/6) > V1 (Braak 6), with monotonic
        ranking test (Spearman ρ on region-ordered mean LAMDA
        scores) significant at p < 0.05.
        (NB: this direction is the *opposite* of cortical-Lewy
        gradients; we test it explicitly because Goralski's LAMDA
        is derived from cortex, where it is presumed late. The
        null hypothesis is no monotonic gradient.)

    H3 (secondary — excitatory-neuron specificity replicates):
        Within each region's neuronal compartment, the LAMDA
        module score is significantly higher in excitatory
        neurons than inhibitory neurons (in cortical regions M1,
        V1), AND in glutamatergic projection neurons compared to
        local interneurons in subcortical regions (DMNX, SNpc,
        GPi). Wilcoxon test, BH-corrected per region.

    H4 (secondary — gene-program decomposition):
        Goralski LAMDA is a coarse 200-gene composite. We
        decompose into 5 functional sub-programs (synaptic /
        mitochondrial / proteostasis / vesicle / immune) per the
        Goralski-published functional clustering. We test whether
        the sub-programs dissociate across regions: do all 5 move
        together in cortex, but only some sub-programs activate
        in DMNX or SNpc? This dissociation pattern is the
        biological discovery target.

    H5 (secondary — direction-specific validation in cingulate
         held-out donor subset):
        Goralski has a subset of donors with both cingulate (their
        derivation tissue) and orbitofrontal data not used in the
        original LAMDA derivation. We hold out the orbitofrontal
        subset from Goralski's deposited CosMx data as a positive
        control: LAMDA should re-replicate at p < 0.001 in that
        tissue, providing a same-modality / same-cohort sanity
        bar.

  A non-generalisation of LAMDA outside cortex would itself be a
  major scientific finding — it would mean the program is
  cortex-restricted, contradicting common assumptions that
  Lewy-pathology gene programs are tissue-agnostic. OmiCairn must
  report any null finding with the same emphasis as a positive one.

data:
  location: ./research_cases/parkinson-lamda-multiregion/data
  shared_cache: ./datasets
  description: |
    **No pre-staged raw data on disk.** Fetch all datasets via
    EXECUTE phase data-fetch protocol into `./datasets/<accession>/`.

    **Primary datasets:**

      - **Goralski / Henderson 2024 LAMDA spatial atlas (signature
        derivation + held-out positive control).**
        Zenodo dataset: 10.5281/zenodo.10729767
          https://zenodo.org/records/10729767
        Companion notebook: 10.5281/zenodo.10732492
          https://zenodo.org/records/10732492
        Content: GeoMx DSP whole-transcriptome counts per ROI,
        CosMx single-cell xy-coords + counts, Supplementary
        Data 1 (LAMDA gene list, 200 genes) and 5–11 (sub-program
        annotations, mouse PFF replication). 8 human donors
        (PD/PDD/DLB; cingulate); ~9,941 CosMx cells.
        Target path: ./datasets/zenodo_10729767_goralski/

        Raw seq host: ASAP CRN — not used here (we use the
        already-processed Zenodo deposit).

      - **Fullard / N M 2024 multi-region snRNA-seq atlas
        (test substrate).**
        CELLxGENE collection (h5ad direct-fetchable, no login):
          https://cellxgene.cziscience.com/collections/d5d0df8f-4eee-49d8-a221-a288f50a1590
        Content: 100 donors (75 PD + 25 ctrl), 5 regions (DMNX,
        GPi, SNpc, primary motor cortex M1, primary visual cortex
        V1), 2,096,155 nuclei. 71 donors have all 5 regions.
        Companion code:
          https://github.com/DiseaseNeuroGenomics/AMP-PD_SciData
        Target path: ./datasets/cellxgene_d5d0df8f_fullard/
        Fetch per-region h5ad slabs; total ~ 30-50 GB.

      - **Reference cell-type marker panel (independent of both):**
        Allen Brain Atlas reference taxonomy for human cortex +
        subcortex (Hodge 2019 + Bakken 2021 cortex; published
        marker gene CSVs):
          https://celltypes.brain-map.org/api/v2/well_known_file_download/
        Used to harmonise cluster labels across Fullard's 5
        regions (their annotations differ across regions).
        Target path: ./datasets/allen_brain_ref/

      - **MSigDB Hallmark + Reactome (for sub-program
        functional annotation in H4):**
          https://www.gsea-msigdb.org/gsea/msigdb/human/
        Target path: ./datasets/msigdb/

  cohorts:
    [goralski_2024_cingulate_derivation,
     goralski_2024_orbitofrontal_held_out,
     fullard_2024_DMNX, fullard_2024_GPi, fullard_2024_SNpc,
     fullard_2024_M1, fullard_2024_V1]
  modalities: [snRNA_seq_10x, geomx_dsp_wta,
               cosmx_single_cell_spatial]

success_criteria:
  primary_metric: noncortical_lamda_enrichment_count
  baseline: cortical_only_lamda_derivation
  pre_registration: required
  honest_validation: required
  notes: |
    Primary success = ≥ 3 non-cortical (region × cell-type) pairs
    with significant positive LAMDA enrichment at FDR<0.05 AND
    Cohen's d > 0.25.

    Out-of-scope success: showing that LAMDA fully replicates in M1
    cortex of the Fullard atlas as a same-modality (cortex-to-cortex)
    positive control. This is necessary but insufficient — the test
    fails (no headline) if only cortex regions reach significance.

    Honest-null report: if H1 fails (LAMDA does not generalise),
    H4's sub-program dissociation analysis must still produce an
    informative finding ("LAMDA's synaptic component generalises;
    its mitochondrial component does not"), or the run is reported
    as a clean null.

figures_planned:
  - id: F1
    title: "Cross-region cell-type harmonisation of the Fullard atlas"
    panels:
      - "UMAP per region (DMNX, GPi, SNpc, M1, V1) colored by Allen-aligned cell type"
      - "Heatmap: % cell-type composition per region per PD/ctrl status (donors as rows pooled)"
      - "Sankey: Fullard region-specific labels → Allen consensus labels (cortex BICAN aligned + Linnarsson subcortex aligned)"
      - "Per-donor representation: # cell types recovered per region per donor (QC; identify drop-outs)"
  - id: F2
    title: "LAMDA enrichment across 5 regions × cell types (H1)"
    panels:
      - "Heatmap: LAMDA module score per (region × cell-type), PD-vs-ctrl differential, color = signed log10(FDR), size = Cohen's d"
      - "Per-cell-type forest plot: log fold-change in LAMDA score across regions, with donor-level random-effect CIs"
      - "Region-stratified Wilcoxon volcano: y = -log10(FDR), x = effect-size, each dot = one cell type"
      - "Cross-cell-type concordance: do the cell types that 'fire LAMDA' overlap with Goralski's pSyn+ excitatory cells (Jaccard)"
  - id: F3
    title: "Braak-stage gradient testing (H2)"
    panels:
      - "Region-ranked boxplots of LAMDA score in DA-or-major-projection-neuron compartment: DMNX→SNpc→GPi→M1→V1"
      - "Spearman ρ test with permutation null (Braak order vs. LAMDA), with point estimates and CIs"
      - "Per-donor heatmap: rows = donors (PD only), cols = regions ordered by Braak; cell = donor's mean LAMDA score; row-sorted by overall LAMDA"
      - "Anti-gradient check: do controls show ANY gradient (should not)?"
  - id: F4
    title: "Excitatory-vs-inhibitory specificity (H3)"
    panels:
      - "Per-region boxplot: LAMDA score in excitatory vs. inhibitory vs. modulatory neurons, Wilcoxon p"
      - "Per-region UMAP colored by LAMDA score, with excitatory contour"
      - "Glutamatergic projection vs. local interneuron contrast in DMNX, SNpc, GPi"
      - "Concordance: Goralski-published excitatory specificity (Kim cosmx clusters) vs. our Fullard inference"
  - id: F5
    title: "Sub-program decomposition (H4)"
    panels:
      - "Per-(region × cell-type) decomposition: stacked-bar of each LAMDA sub-program's contribution to total score (synaptic / mito / proteostasis / vesicle / immune)"
      - "Sub-program effect-size matrix: rows = (region × cell-type), cols = sub-program, color = Cohen's d"
      - "Pairwise sub-program correlation matrix per region (which sub-programs co-fire?)"
      - "Reactome / KEGG over-representation per sub-program × region with FDR-annotated heatmap"
  - id: F6
    title: "Orbitofrontal held-out validation (H5) + sensitivity"
    panels:
      - "Goralski orbitofrontal LAMDA score PD vs. ctrl, Wilcoxon p < 0.001 expected (positive control)"
      - "Sub-sampling sensitivity: LAMDA enrichment with 50% / 75% / 90% donor-subsampling, per region"
      - "Cell-count threshold sensitivity: requires ≥50 / ≥100 / ≥200 cells per (donor × region × type) for inclusion"
      - "Comparison against shuffled-label null: LAMDA module score with permuted PD/ctrl labels (should NOT enrich)"

figures_not_in_scope:
  - "Wet-lab validation of region-specific LAMDA targets (mouse, organoid)"
  - "Re-alignment of GeoMx ROIs from raw scans (use Goralski's deposited count matrices)"
  - "snATAC-seq integration of LAMDA targets — accessibility data not in Fullard atlas"
  - "Cingulate cortex cell-type re-derivation from Fullard (cingulate not in atlas)"
  - "Lewy-body burden quantification from histology (image data not in deposits)"

budgets:
  max_phase_wall_minutes: 240
  max_total_wall_minutes: 1440
  allow_web: true
  claude_model: claude-sonnet-4-6
  max_phase_budget_usd: 28.0

auto_approve: true
---

# parkinson-lamda-multiregion — Generalisation of cortical LAMDA across PD Braak regions

## Source materials

- Goralski, T.M., Meyerdirk, L., Breton, L., et al.
  *Spatial transcriptomics reveals molecular dysfunction associated
  with cortical Lewy pathology.* Nature Communications 15:2642
  (2024). DOI: 10.1038/s41467-024-47027-8. PMC10966039.
- N M, P., Fullard, J.F., Clarence, T., et al.
  *A multi-region single nucleus transcriptomic atlas of Parkinson's
  disease.* Scientific Data 11:1274 (2024). PMC11585549.
- Hodge, R.D., et al. *Conserved cell types with divergent features
  in human versus mouse cortex.* Nature 573 (2019). (Allen reference.)

PDFs in `claude_docs/claude_2026-05-21/parkinson_research/pdfs/`
(goralski_2024_NatComm.pdf, nm_2024_atlas_SciData.pdf).

## Why this matters for OmiCairn

This is a textbook *generalisation test*: a signature defined in
one tissue, scored in a completely independent atlas covering
adjacent and distant tissues, asking whether the program is
universal (cellular dysfunction in PD-Lewy regions) or
tissue-restricted (cortex only). The answer is biologically
interpretable either way and there is no published version of this
test.

OmiCairn challenges exercised:

1. **Cross-modality scoring** (GeoMx counts → snRNA-seq scoring).
2. **Region × cell-type 2D enrichment testing** with proper
   multiple-testing correction.
3. **Honest-null reporting**: most likely outcome is partial
   replication (cortical M1 + some SNpc neurons enrich; deep
   subcortex does not). OmiCairn must not bury the partial nature.
4. **Held-out positive control (H5)** as a sanity bar — without
   it, a null could be interpreted as a methodology failure rather
   than a biological finding.

## Known prior results (cross-check ground truth)

| Claim | Reference |
|---|---|
| LAMDA: 200 genes upregulated in pSyn+ cortical excitatory neurons | Goralski Supp Data 1 |
| 5 functional sub-programs (synaptic, mito, proteostasis, vesicle, immune) | Goralski Fig. 2 |
| LAMDA replicates in α-syn PFF mouse cortex | Goralski Fig. 6 |
| Fullard atlas: 5 regions, 71 donors with full coverage | Fullard Sci Data 11:1274 |

## Out of scope (for this iteration)

- Wet-lab validation in iPSC, mouse, organoid.
- Cingulate re-analysis in Fullard atlas (not in collection).
- Histology-paired Lewy-body burden correlation.
- snATAC-seq integration (Fullard is RNA only).
- LRRK2 / GBA1 stratification.
