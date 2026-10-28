---
research_mode: DISCOVERY
domain: neurodegeneration_drug_repositioning
topic_slug: parkinson-lincs-reversal

research_question: |
  Which approved or late-phase compounds in the LINCS L1000
  Phase II touchstone library produce gene-expression signatures
  that maximally reverse the PD-vs-control transcriptomic signature
  of SNpc dopaminergic neurons (Fullard 2024) — and how many of
  the top-50 reversing candidates have prior PD-drug-repositioning
  evidence in Open Targets or in published PD repurposing hit
  lists?

  LINCS L1000-based drug repositioning was pioneered by Iorio
  2010 and applied broadly across diseases (CMAP score). PD
  applications have used bulk or whole-tissue signatures — never a
  cell-type-resolved, snRNA-seq-derived SNpc-DA-neuron signature
  from a multi-region atlas. The known concern is that LINCS L1000
  cell lines are mostly cancer lines (MCF7, PC3, A375, etc.) and
  may be biologically irrelevant to neurons. This OmiCairn run tests
  exactly that.

  Hypotheses (pre-registered):

    H1 (primary — top-50 reversers overlap with prior PD evidence):
        ≥ 5 of the top-50 negatively-correlated (most reversing)
        compounds by LINCS L1000 connectivity score against the
        SNpc-DA PD-vs-ctrl signature have prior Open Targets PD
        association (overall_association_score > 0 for MONDO:0005180)
        OR have appeared in a published PD drug-repositioning hit
        list (Bayati 2024 Acta Neuro DDC paper; omicSynth Tang 2024;
        Storm 2025). Hypergeometric q < 0.01 over background of all
        L1000 small-molecule perturbagens.

    H2 (secondary — drug-class enrichment):
        The top-50 hits are enriched for known PD-relevant
        therapeutic classes — anti-inflammatory, mTOR modulators,
        calcium-channel blockers, anti-oxidants, autophagy
        inducers. Pre-registered: ≥ 2 of these 5 classes
        over-represented by Fisher's exact q < 0.05.

    H3 (secondary — cell-type-specific reversal):
        Reversal scores in DA neurons differ from reversal scores
        in microglia from the same Fullard atlas (Pearson r < 0.3
        for the two L1000 connectivity ranking lists). The
        optimal repurposing candidate is cell-type-dependent, not
        a global brain signal.

    H4 (secondary — neuronal-cell-line subset analysis):
        Restrict L1000 to its neuronal subset (NEU, NPC cell-line
        signatures only) — n = 2 cell lines, much smaller library.
        Test whether the neuronal-subset top-50 reversers have
        higher overlap with prior PD evidence (≥ 8 of 50) than
        the all-cell-lines top-50 (H1).

    H5 (secondary — sign-direction sanity check):
        Internal positive control: levodopa (DDC substrate) and
        ropinirole (D2 agonist) should appear in the *positive*
        connectivity tail (mimic PD-on-treatment state), NOT in
        the reversing tail. If they appear in the reversing tail,
        the signature direction is wrong.

  Null payoff. A null overlap (H1: 0/50 with prior PD evidence)
  means LINCS L1000 cancer-line transcriptomic signatures are too
  non-neuronal to be informative for PD — an informative
  quantification of the long-suspected limitation.

data:
  location: ./research_cases/parkinson-lincs-reversal/data
  shared_cache: ./datasets
  description: |
    **No pre-staged raw data on disk.** Fetch into
    `./datasets/<accession>/`. Total ~20 GB.

    **LINCS L1000:**

      - **L1000 Phase II Level 5 signatures** (GSE92742 + GSE70138;
        ~5 GB combined; HDF5).
          https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE92742
          https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE70138
        Target path: ./datasets/lincs_l1000/

      - **LINCS chemical metadata** via clue.io public API
        (compound MoA, ATC class, ChEMBL ID).
          https://clue.io/api
        Target path: ./datasets/clue_metadata/

    **snRNA-seq signature source:**

      - **Fullard 2024 SNpc slab + microglia** (CELLxGENE
        d5d0df8f-...; ~14 GB filtered).
        Target path: ./datasets/cellxgene_d5d0df8f_fullard/

    **PD prior-evidence references:**

      - **Open Targets platform dump** (release 24.06; PD target
        scores via API; ~1 GB).
          https://platform.opentargets.org/
        Target path: ./datasets/opentargets_2406/

      - **omicSynth Tang 2024 supplementary**
        (iScience 27:108953; PD druggable target list).
        Target path: ./datasets/tang_2024_omicsynth/

      - **Bayati 2024 PD-DDC supplementary**
        (Acta Neuropathol 147:52, PMC10927779).
        Target path: ./datasets/bayati_2024_supp/

      - **Storm 2025 protein-MR supplementary**
        (Brain 148:2412).
        Target path: ./datasets/storm_2025_pqtl/

    **Internal control compound list (H5):** levodopa, ropinirole,
    pramipexole, rasagiline — fetched from clue.io by name.

  cohorts: [fullard_2024_SNpc_microglia]
  modalities: [snRNA_seq_10x, lincs_l1000_connectivity,
               drug_target_associations]

success_criteria:
  primary_metric: top50_PD_evidence_overlap_count
  baseline: random_drug_list_expectation
  pre_registration: required
  honest_validation: required
  notes: |
    Primary success = ≥ 5 of the top-50 LINCS L1000 PD-reversing
    compounds have prior Open Targets or published PD-repositioning
    evidence. Hypergeometric q < 0.01 vs. random sampling of L1000
    library.

    Honest-failure: 0/50 overlap = LINCS L1000 cancer-line
    signatures are non-neuronal noise for PD; informative
    quantification.

    H5 internal control: levodopa + ropinirole must NOT be in the
    reversing tail. If they are, the signature direction is wrong
    and H1 results are uninterpretable until that is fixed.

figures_planned:
  - id: F1
    title: "Fullard SNpc DA PD-vs-ctrl signature → L1000-landmark restriction"
    panels:
      - "Volcano plot: Fullard SNpc DA PD-vs-ctrl DEGs (full transcriptome)"
      - "Signature on L1000 978-landmark-gene subset (rank-list bar)"
      - "Per-donor signature score boxplot (PD vs. ctrl)"
      - "Top-30 up- and down-regulated genes table"
  - id: F2
    title: "LINCS L1000 connectivity scoring (H1)"
    panels:
      - "Distribution of connectivity scores across L1000 small-molecule perturbagens"
      - "Top-50 reversers ranked barplot (with compound names, MoA labels)"
      - "Hypergeometric overlap: top-50 ∩ Open Targets PD evidence (q-value annotation)"
      - "Cumulative-distribution plot of overlap vs. top-N (N = 10, 25, 50, 100, 200)"
  - id: F3
    title: "Drug-class enrichment (H2)"
    panels:
      - "Top-50 ATC class distribution barplot"
      - "Fisher's enrichment per PD-relevant class (5 pre-registered classes)"
      - "Per-class compound list + MoA annotation"
      - "Comparison: top-50 reversing vs. top-50 mimicking class distributions"
  - id: F4
    title: "Cell-type-specific reversal (H3) + neuronal-subset test (H4)"
    panels:
      - "Microglia signature → L1000 reversal ranks, scatter vs. DA-neuron ranks"
      - "Pearson r between DA and microglia ranking lists"
      - "Neuronal-cell-line-only top-50: overlap with PD evidence vs. all-cell-line top-50"
      - "Per-cell-line top-5 reversers (NEU vs. NPC vs. MCF7 vs. PC3 vs. A375)"
  - id: F5
    title: "Internal positive controls (H5) + honest-failure panel"
    panels:
      - "Levodopa + ropinirole + pramipexole connectivity score positions (expected in mimicking tail)"
      - "Rasagiline + selegiline (MAO-B inhibitors) connectivity positions"
      - "Sign-direction sanity panel: if PD-known drugs appear in reversing tail, flag"
      - "Permutation null: top-50 from randomised query signature, overlap distribution"

figures_not_in_scope:
  - "Wet-lab compound validation"
  - "Re-processing of L1000 raw level-3 → level-5 (use deposited level-5)"
  - "In-vivo PD model compound testing"
  - "Combination-therapy connectivity (single compounds only)"

budgets:
  max_phase_wall_minutes: 240
  max_total_wall_minutes: 1440
  allow_web: true
  claude_model: claude-sonnet-4-6
  max_phase_budget_usd: 25.0

auto_approve: true
---

# parkinson-lincs-reversal — LINCS L1000 connectivity reversal of PD snRNA-seq signatures

## Source materials

- Subramanian, A., Narayan, R., Corsello, S.M., et al. *A next
  generation connectivity map: L1000 platform and the first
  1,000,000 profiles.* Cell 171:1437 (2017). LINCS L1000.
- Iorio, F., Bosotti, R., Scacheri, E., et al. *Discovery of drug
  mode of action and drug repositioning from transcriptional
  responses.* PNAS 107:14621 (2010). Connectivity score method.
- N M, P., Fullard, J.F., Clarence, T., et al. Sci Data 11:1274
  (2024). Fullard atlas.
- Tang, A., Strauss, M., et al. *omicSynth — multi-omic
  druggable target catalogue across neurodegenerative diseases.*
  iScience 27:108953 (2024).
- Bayati et al. Acta Neuropathol 147:52 (2024). PD-DDC biomarker.
- Storm, C.S., Kia, D.A., Almramhi, M.M., et al. *Genome-wide
  determination of drug targets for Parkinson's disease via
  Mendelian-randomisation on plasma proteome.* Brain 148:2412
  (2025).

## Why this matters for OmiCairn

This is the v2 batch's only drug-repositioning case. It exercises
OmiCairn's ability to:

1. **Cross-reference public databases at scale** (LINCS L1000 +
   Open Targets + omicSynth + clue.io).
2. **Pre-register internal positive controls** (levodopa,
   ropinirole — H5) so signature-direction errors are caught.
3. **Cell-type-resolved drug repositioning** — the novel angle vs.
   prior PD repositioning work that used bulk signatures.
4. **Honestly quantify the cancer-cell-line limitation** of LINCS
   L1000 for neuronal diseases (H4 neuronal-subset test).

## Known prior results (cross-check)

| Claim | Reference |
|---|---|
| LINCS L1000 Phase II: ~30K small-molecule perturbagens, ~80 cell lines | Subramanian 2017 |
| Connectivity score recovers known MoA in 70% of test cases | Iorio 2010; cmap.org |
| Open Targets PD-association score available for ~2000 targets | Open Targets release 24.06 |
| omicSynth lists ~150 PD druggable targets across modalities | Tang 2024 |

## Out of scope

- Wet-lab compound testing.
- L1000 raw-spectra re-processing (use level-5 signatures only).
- In-vivo PD model studies.
- Drug-combination connectivity.

## Implementation choices

- PD signature: Fullard SNpc DA pseudobulk PD-vs-ctrl, restricted to
  L1000 978-landmark genes (apples-to-apples comparable).
- Connectivity score via `gseapy.ssgsea` in "norm-2018" mode OR
  `cmappy.connectivity` (the standard CMAP score).
- Open Targets PD evidence: filter `disease.id == "MONDO_0005180"`,
  evidence type "drug.target.association".
- Drug-class enrichment: ChEMBL ATC code via Open Targets
  pre-computed mapping.
- Internal positive controls (levodopa, ropinirole, pramipexole)
  are pre-registered in H5 — non-negotiable.
