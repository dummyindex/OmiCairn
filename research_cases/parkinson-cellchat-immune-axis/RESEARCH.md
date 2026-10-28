---
research_mode: DISCOVERY
domain: neurodegeneration_cell_communication
topic_slug: parkinson-cellchat-immune-axis

research_question: |
  Do LIANA-py (CellChat-equivalent consensus) inferred ligand-
  receptor (L-R) communications between Hong 2025 peripheral-blood
  CD8+ T cells (GEO GSE223138) and Fullard brain microglia mirror —
  at the L-R pair level — the brain-internal CD8+T → microglia
  communications inferred by Ma 2025 (PMC12322057), thus
  identifying a "blood-as-foreshadow-of-brain" set of peripheral
  biomarker ligands?

  Hong 2025 (npj Parkinson's 11) reported 28 immune-cell-specific
  PD risk genes from OneK1K + IPDGC + GSE223138 multi-omic
  integration. Ma 2025 (Nat Commun 16) characterised brain-side
  CD8+ T-cell × microglia communications via SN scRNA + scTCR-seq.
  The two cell-type-communication signatures have not been
  compared at the L-R pair level. If peripheral L-Rs echo brain
  L-Rs, the peripheral signal could be used as a foreshadow blood
  biomarker; if they diverge, peripheral-blood-as-PD-biomarker
  biology is decoupled from the brain-immune axis. Either
  outcome is informative with strong biomarker implications.

  Hypotheses (pre-registered):

    H1 (primary — L-R pair concordance):
        ≥ 5 ligand-receptor pairs are inferred as PD-up-regulated
        in BOTH the peripheral CD8+T → blood-monocyte direction
        (Hong 2025) AND the brain CD8+T → microglia direction
        (Ma 2025), with concordant sign of dysregulation.
        Permutation null p < 0.01 (10,000 random L-R subsets).

    H2 (secondary — Hong-MR-gene enrichment):
        The shared L-R pairs are enriched (q < 0.05
        hypergeometric) for ligands or receptors whose genes
        appear on the Hong 2025 MR-implicated immune-gene list
        (FDFT1, CTSB, ARSA, HLA-DQA1, and 24 others reported in
        Hong 2025 Table 2).

    H3 (secondary — peripheral-foreshadow correlation):
        A "peripheral CD8+T signature score" computed from Hong
        2025 PBMC data using the 28 MR-implicated immune genes
        rank-correlates (Spearman ρ > 0.3, nominal p < 0.05)
        with the v1 `parkinson-tcell-foothold` per-donor brain
        T-cell-module score at the disease-stage level (early vs.
        late PD).

    H4 (secondary — direction-specific dropout):
        Inferring CD8+T → monocyte (PBMC) and microglia → CD8+T
        (brain) — the reverse-direction pairs — should NOT show
        the same H1 enrichment (control: PD signals are in the
        CD8+T → myeloid direction, not the reverse).

    H5 (secondary — method-consensus robustness):
        Repeat the L-R inference with three methods (CellPhoneDB,
        NATMI, scTalk) and confirm the H1 shared-pair count is
        within ±2 of the LIANA consensus result. (LIANA-py
        provides this consensus natively.)

  Null payoff. If peripheral and brain L-R signatures diverge
  (H1: 0-3 shared pairs), peripheral-blood-as-PD-biomarker biology
  is decoupled from the brain-immune axis — informative, with
  strong implications for blood-test development.

data:
  location: ./research_cases/parkinson-cellchat-immune-axis/data
  shared_cache: ./datasets
  description: |
    **No pre-staged raw data on disk.** Fetch into
    `./datasets/<accession>/`. Total ~24 GB.

    **Peripheral scRNA-seq:**

      - **Hong 2025 PBMC scRNA-seq** (GEO GSE223138; ~2 GB).
          https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE223138
        Target path: ./datasets/GSE223138/

    **Brain scRNA-seq + spatial:**

      - **Ma 2025 PD SN snRNA + scTCR + Visium** via NCBI /
        PMC12322057 (~15 GB raw → ~3 GB filtered).
          https://www.ncbi.nlm.nih.gov/pmc/articles/PMC12322057/
        Target path: ./datasets/ma_2025_SN_Tcell/
        Note: Data Availability statement gives exact GEO /
        Synapse ID. Fallback: Fullard SN microglia + Hong PBMC
        only, drop the brain CD8+T Ma 2025 component to a
        *prior* rather than a *data input* (per v2 risk register).

      - **Fullard 2024 SN microglia slab** (CELLxGENE
        d5d0df8f-..., microglia subset only; ~3 GB filtered).
        Target path: ./datasets/cellxgene_d5d0df8f_fullard/

    **L-R inference resources:**

      - **CellChatDB v2** (R package shipping table) — fetch
        the L-R interaction list via the `cellchat` R package
        or the LIANA-py equivalent.
        Target path: ./datasets/cellchatdb_v2/

      - **LIANA-py** (Dimitrov 2024 Nat Commun) — consensus L-R
        inference combining CellPhoneDB, CellChat, NATMI,
        Connectome, scTalk, NicheNet.
        Target path: ./datasets/liana_py/

    **Cross-resource references:**

      - **Hong 2025 Supp Table 2** — 28 MR-implicated immune
        genes.
        Target path: ./datasets/hong_2025_supp/

      - **OneK1K reference eQTL** (Yazar 2022; ~1 GB) — for
        cell-type calibration consistency between PBMC datasets.
          https://onek1k.org/
        Target path: ./datasets/onek1k_eqtl/

    **Cross-workspace input:** v1 `parkinson-tcell-foothold`
    workspace `results.json` provides per-donor brain T-cell-
    module scores for H3. If absent, H3 is reported as
    inapplicable.

  cohorts: [hong_2025_pbmc, ma_2025_SN_brain_immune,
            fullard_2024_SN_microglia]
  modalities: [scRNA_seq_PBMC, snRNA_seq_brain, scTCR_seq,
               ligand_receptor_inference,
               mendelian_randomisation_supp_tables]

success_criteria:
  primary_metric: shared_PD_up_LR_pair_count
  baseline: permutation_null_random_LR_subset
  pre_registration: required
  honest_validation: required
  notes: |
    Primary success = ≥ 5 PD-up-regulated L-R pairs shared
    between Hong 2025 (PBMC CD8+T → monocyte) and Ma 2025 (brain
    CD8+T → microglia), permutation null p < 0.01.

    Honest-failure: 0-3 shared pairs = peripheral and brain immune
    axes are decoupled. Informative as a strong blood-test-
    development caveat.

    Direction-specificity control (H4): the reverse-direction L-R
    inference must NOT show the same enrichment, otherwise H1 is
    direction-non-specific.

    Method-consensus (H5): LIANA-py provides per-method ranks; the
    ±2 shared-pair-count tolerance is the methodological robustness
    bar.

figures_planned:
  - id: F1
    title: "PBMC + brain immune cell-type calibration"
    panels:
      - "Hong 2025 PBMC UMAP, color = cell type (CD8+T, NK, monocyte, B, etc.)"
      - "Ma 2025 SN brain UMAP, color = cell type (CD8+T, microglia, DA, astrocyte, etc.)"
      - "Cell-type-annotation concordance with OneK1K reference"
      - "Per-donor cell counts: PBMC vs. brain CD8+T"
  - id: F2
    title: "PBMC CD8+T → monocyte L-R inference (LIANA-py)"
    panels:
      - "Top-100 L-R pairs by LIANA consensus score (PD-up rank)"
      - "Per-method (CellChat / CellPhoneDB / NATMI / scTalk) rank correlation"
      - "PD-vs-ctrl differential L-R volcano"
      - "Top-20 L-R network visualisation"
  - id: F3
    title: "Brain CD8+T → microglia L-R inference (Ma 2025)"
    panels:
      - "Top-100 L-R pairs in brain CD8+T → microglia (LIANA consensus)"
      - "PD-vs-ctrl differential L-R volcano (brain)"
      - "Cell-type cross-talk diagram (CD8+T, microglia, astrocyte, DA)"
      - "Per-region (SN vs. cingulate) L-R rank comparison"
  - id: F4
    title: "PBMC ∩ brain shared L-R pairs (H1, H2)"
    panels:
      - "Shared-pair Venn (PBMC top-100 ∩ brain top-100)"
      - "Concordance scatter: PBMC PD-up vs. brain PD-up L-R rank"
      - "Hong 2025 MR-gene enrichment among shared pairs (q-value)"
      - "Permutation null: random L-R subset shared-pair distribution"
  - id: F5
    title: "Direction-specificity (H4) + peripheral-foreshadow (H3) + honest panel"
    panels:
      - "Reverse-direction L-R: monocyte → CD8+T (PBMC) and microglia → CD8+T (brain), shared-pair count"
      - "Peripheral signature score (28 MR genes, PBMC) vs. brain T-cell-module score (cross-workspace H3)"
      - "Method-consensus robustness ribbon (H5): per-method shared-pair counts"
      - "Honest-failure ribbon: signature-divergent outcome decision panel"

figures_not_in_scope:
  - "Wet-lab L-R blocking validation"
  - "CITE-seq protein-level confirmation"
  - "TCR-repertoire analysis beyond cell-type assignment"
  - "Non-CD8 T-cell subsets (CD4, Tregs, gdT) as primary focus"

budgets:
  max_phase_wall_minutes: 240
  max_total_wall_minutes: 1440
  allow_web: true
  claude_model: claude-sonnet-4-6
  max_phase_budget_usd: 25.0

auto_approve: true
---

# parkinson-cellchat-immune-axis — Peripheral-immune to brain-microglia L-R signaling axis in PD

## Source materials

- Hong, X., Zhou, Y., et al. *Peripheral immune cell-specific
  genes in Parkinson's disease uncovered by multi-omics with
  therapeutic implications.* npj Parkinson's Disease 11 (2025).
  GEO GSE223138 + IPDGC GWAS + OneK1K MR.
- Ma, J., Patel, S., et al. *Spatial and single-nuclei
  transcriptomic landscape of the Parkinson's disease substantia
  nigra implicates clonally expanded CD8+ T cells with
  α-syn-homologous T-cell receptors.* Nat Commun 16 (2025).
  PMC12322057.
- N M, P., Fullard, J.F., Clarence, T., et al. Sci Data 11:1274
  (2024). Fullard SN microglia slab.
- Dimitrov, D., Türei, D., Garrido-Rodriguez, M., et al. *Comparison
  of methods and resources for cell-cell communication inference
  from single-cell RNA-Seq data.* Nat Commun 13:3224 (2022).
  LIANA-py.
- Jin, S., Guerrero-Juarez, C.F., Zhang, L., et al. *Inference and
  analysis of cell-cell communication using CellChat.* Nat Commun
  12:1088 (2021). CellChat.
- Yazar, S., Alquicira-Hernandez, J., Wing, K., et al. *Single-cell
  eQTL mapping identifies cell type-specific genetic control of
  autoimmune disease.* Science 376:eabf3041 (2022). OneK1K.

## Why this matters for OmiCairn

This case completes the peripheral × brain immune axis story
started by v1's `parkinson-tcell-foothold`. It exercises OmiCairn's
ability to:

1. **Run a Python L-R inference pipeline** (LIANA-py) outside
   the R CellChat ecosystem.
2. **Compare two cohort-disjoint L-R inference outputs** at the
   L-R pair level (not the cell-type or gene level).
3. **Cross-workspace coordination** (H3 reads v1 parkinson-tcell-
   foothold workspace).
4. **Direction-specificity as a control** (H4) — the kind of
   pre-registered negative control that distinguishes real
   communication-axis findings from method artifacts.

## Known prior results (cross-check)

| Claim | Reference |
|---|---|
| Hong 2025: 28 immune-cell-specific PD risk genes (MR-implicated) | Hong 2025 Table 2 |
| Ma 2025: clonally expanded brain CD8+T cells with α-syn-homologous TCRs | Ma 2025 Fig. 3 |
| LIANA-py outperforms single-method L-R inference on benchmarks | Dimitrov 2022; Dimitrov 2024 |
| OneK1K provides cell-type-specific eQTL for 14 PBMC types | Yazar 2022 |

## Out of scope

- Wet-lab L-R blocking validation.
- CITE-seq protein-level confirmation.
- TCR-repertoire analysis beyond cell-type assignment.
- Non-CD8 T-cell subsets as primary focus.

## Implementation choices

- LIANA-py 0.4.1 (Dimitrov 2024) as primary L-R inference (Python-
  native; consensus across CellChat / CellPhoneDB / NATMI /
  Connectome / scTalk / NicheNet).
- LIANA-py output is the per-method rank list + consensus rank;
  use consensus for H1 with ±2 tolerance from per-method results
  for H5 robustness.
- Mendelian-randomisation gene enrichment uses the 28 Hong-2025
  genes against L-R-overlapping ligands+receptors via
  hypergeometric (background = all CellChatDB L-R pairs).
- Peripheral signature score (H3): `scanpy.tl.score_genes` over the
  28 MR genes, averaged per donor in Hong's PBMC data.
- Fallback (per v2 risk register): if Ma 2025 brain data not
  fetchable, drop H1 brain-side to a prior using only Ma 2025 Fig.
  3 published L-R names, and document in Limitations.
