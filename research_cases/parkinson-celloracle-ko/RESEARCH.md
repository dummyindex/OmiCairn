---
research_mode: HYBRID
domain: neurodegeneration_in_silico_perturbation
topic_slug: parkinson-celloracle-ko

research_question: |
  Does CellOracle's in-silico knock-out (KO) of the top PD-risk
  genes from the Kim 2024 multi-ancestry GWAS — LRRK2, GBA, SNCA,
  VPS13C, GAK, MAPT, BST1, CTSB, DDC, SCARB2 (10 genes ranked by
  fine-mapping posterior probability) — in Fullard 2024 SNpc
  dopaminergic neurons produce predicted cell-state vector shifts
  that align with the observed PD-vs-control DA state shift?

  The CellOracle framework (Kamimoto et al. 2023 Nature) lets one
  combine a scATAC-derived base-GRN with snRNA-seq to predict
  counter-factual cell-state shifts in response to TF / target
  perturbation. The mechanistic-content claim — that the inferred
  GRN encodes enough biology to predict disease-state shifts —
  has not been formally tested in human PD with public data.

  Hypotheses (pre-registered):

    H1 (primary — KO-vs-disease alignment):
        The mean cosine similarity between the CellOracle-predicted
        post-KO state-shift vector (averaged across the 10 GWAS
        genes) and the observed PD-minus-control SNpc DA centroid
        shift is > 0.30, with a permutation-null p < 0.01
        (10,000 random gene-label permutations).

    H2 (secondary — fine-mapping PIP correlation):
        Per-gene KO cosine alignment with PD-minus-control shift
        correlates positively (Spearman ρ > 0.4, p < 0.05) with
        the gene's posterior probability of causal association
        from Kim 2024 fine-mapping (Supp Table S5).

    H3 (secondary — LRRK2-KO target enrichment):
        The CellOracle-predicted top-100 up-regulated targets of
        LRRK2-KO are enriched (q < 0.05 by hypergeometric) for
        the Hemedan 2024 Boolean-PD "prodromal-cluster" regulatory-
        module gene list.

    H4 (secondary — SNCA protective + HBB null control):
        SNCA-KO simulation predicts a *protective* shift (cosine
        with PD-vs-ctrl vector > 0 with permutation p < 0.05),
        consistent with α-syn loss-of-function reducing
        aggregation; control gene HBB (red-blood-cell marker,
        irrelevant) predicts a null shift (|cosine| < 0.1).

    H5 (secondary — base-GRN robustness):
        Repeat the analysis with the published CellOracle midbrain
        reference base-GRN (Zenodo 7755357) vs. the Adams-2024-
        derived ATAC base-GRN. The H1 cosine-similarity changes by
        ≤ 0.1 between the two base-GRN sources (network-source
        sensitivity test).

  Null payoff. A null cosine alignment (H1 ≤ 0.15) means GWAS-
  implicated genes do not in-silico recapitulate PD cellular
  phenotypes — sharpening the long-standing field critique that
  risk-variant biology is not cell-autonomous and may be glia-
  mediated.

data:
  location: ./research_cases/parkinson-celloracle-ko/data
  shared_cache: ./datasets
  description: |
    **No pre-staged raw data on disk.** Fetch into
    `./datasets/<accession>/`.

    **Primary atlases:**

      - **Fullard 2024 SNpc slab** (CELLxGENE d5d0df8f-...; ~12 GB)
        — observed PD-vs-control DA centroid shift and CellOracle
        snRNA-seq input.
        Target path: ./datasets/cellxgene_d5d0df8f_fullard/

      - **Adams 2024 SN Multiome** (GEO GSE193688; ~10 GB) — ATAC
        fraction for atlas-specific base-GRN construction.
        Target path: ./datasets/GSE193688/

    **Reference base-GRN + GWAS:**

      - **CellOracle midbrain reference base-GRN**
        (Zenodo 10.5281/zenodo.7755357; ~500 MB).
          https://zenodo.org/records/7755357
        Target path: ./datasets/celloracle_basegrn/

      - **Kim 2024 PD multi-ancestry GWAS sumstats + Supp Table S5
        fine-mapping** (GWAS Catalog GCST90275127; ~250 MB).
        Target path: ./datasets/GCST90275127_kim/

      - **Hemedan 2024 PD Boolean-model regulatory modules**
        (GitLab `gitlab.lcsb.uni.lu/lcsb-biocore/publications/
        hemedan23-boolean-modelling-of-pd`; ~50 MB).
        Target path: ./datasets/hemedan_2024_boolean/

  cohorts: [fullard_2024_SNpc, adams_2024_SN_multiome]
  modalities: [snRNA_seq_10x, snATAC_seq_10x_multiome,
               gwas_summary_statistics, boolean_network_model]

success_criteria:
  primary_metric: mean_KO_cosine_alignment_with_disease_shift
  baseline: random_gene_KO_permutation_null
  pre_registration: required
  honest_validation: required
  notes: |
    Primary success = mean cosine across 10 GWAS-gene KOs > 0.30
    with permutation p < 0.01. Negative cosine = anti-aligned
    (KO state pushes *away* from PD state, biologically odd if
    the gene is risk-conferring).

    Internal positive control = SNCA (expected protective per H4);
    internal negative control = HBB (expected null).

    Network-source sensitivity (H5) ≤ 0.1 swing between
    Adams-derived vs. published midbrain base-GRN. If the swing
    is large, the headline must reflect base-GRN dependence rather
    than KO effect alone.

figures_planned:
  - id: F1
    title: "Base-GRN construction + Fullard SNpc DA centroid shift"
    panels:
      - "Adams 2024 ATAC peak distribution per cell type (QC)"
      - "Base-GRN edge count per TF (top-50 TFs by out-degree)"
      - "Fullard SNpc DA PCA: PD vs. ctrl centroid arrow; 95% bootstrap CI ellipses"
      - "Per-donor centroid shift contributing variance breakdown"
  - id: F2
    title: "Per-gene CellOracle KO simulation results (H1, H2)"
    panels:
      - "Per-gene cosine alignment barplot (10 GWAS genes + SNCA + HBB controls), with permutation-null distribution"
      - "Spearman scatter: KO cosine alignment vs. Kim 2024 fine-mapping PIP"
      - "Vector-field UMAP overlay for top-3 aligned KOs (LRRK2, GBA, SNCA)"
      - "Δ-expression heatmap: top-20 perturbed genes per KO × 10 KO conditions"
  - id: F3
    title: "LRRK2-KO target Hemedan module enrichment (H3)"
    panels:
      - "Hypergeometric enrichment q-value bar: LRRK2-KO top-100 vs. Hemedan 2024 modules (prodromal, motor, SWEDD)"
      - "Module-overlap Venn: LRRK2-KO predicted targets ∩ Hemedan prodromal"
      - "Gene-set rank-enrichment plot (GSEA-style) for the prodromal module"
      - "Per-target Δ-expression boxplot, prodromal genes only"
  - id: F4
    title: "Internal controls (H4): SNCA protective + HBB null"
    panels:
      - "SNCA-KO state shift vs. PD-vs-ctrl: cosine + signed direction (expected protective)"
      - "HBB-KO state shift cosine (expected ≈ 0)"
      - "100-random-gene KO control distribution overlay"
      - "Per-cell type sensitivity: SNCA-KO applied to AGTR1+ vs. AGTR1- DA cells (state-divergence test)"
  - id: F5
    title: "Network-source sensitivity (H5) + honest-failure panel"
    panels:
      - "H1 cosine: Adams-base-GRN vs. midbrain-reference base-GRN scatter"
      - "Per-KO concordance heatmap across two base-GRNs"
      - "Ablation: KO with first-order targets only vs. full propagation (50 steps)"
      - "Top-3 most discordant KOs across base-GRNs (interpretation: GRN-source artifact)"

figures_not_in_scope:
  - "Multi-target combined-KO (combinatorial blow-up; single-gene only)"
  - "GPU memory profiling (default CellOracle is CPU-friendly)"
  - "Wet-lab CRISPR-KO validation"
  - "Non-DA cell type as primary KO target"

budgets:
  max_phase_wall_minutes: 240
  max_total_wall_minutes: 1440
  allow_web: true
  claude_model: claude-sonnet-4-6
  max_phase_budget_usd: 25.0

auto_approve: true
---

# parkinson-celloracle-ko — In-silico KO of PD GWAS genes predicts DA vulnerability via CellOracle

## Source materials

- Kamimoto, K., Stringa, B., Hoffmann, C.M., et al. *Dissecting cell
  identity via network inference and in silico gene perturbation.*
  Nature 614:742 (2023). CellOracle method.
- Kim, J.J. et al. *Multi-ancestry genome-wide meta-analysis in
  Parkinson's disease.* Nat Genet 56:27 (2024). GCST90275127.
- N M, P., Fullard, J.F., Clarence, T., et al. *A multi-region
  single nucleus transcriptomic atlas of Parkinson's disease.* Sci
  Data 11:1274 (2024). CELLxGENE d5d0df8f-... .
- Adams, L., Song, M.K., Yuen, S., Tanaka, Y., Kim, Y.S. *A single-
  nuclei paired multiomic analysis of the human midbrain...* Nat
  Aging 4:364 (2024). GSE193688.
- Hemedan, A., Satagopam, V., Ostaszewski, M. *Cohort-specific
  Boolean models highlight different regulatory modules during
  Parkinson's disease progression.* iScience 27:110956 (2024).

## Why this matters for OmiCairn

The v1 batch only described observed states. This case extends to
*counterfactual* predictions: what does the GRN say should happen
when we delete a PD-risk gene? It exercises OmiCairn's ability to:

1. **Couple ATAC + RNA in a base-GRN** (CellOracle's bridge step).
2. **Run a 10-gene KO sweep** while reusing the base-GRN to
   amortise cost.
3. **Build internal positive + negative controls** (SNCA + HBB)
   into the analysis design rather than retrofit them later.
4. **Sensitivity-test the GRN source** (H5) — a known
   CellOracle failure mode.

## Known prior results (cross-check)

| Claim | Reference |
|---|---|
| CellOracle KO simulations recover known TF effects in mouse hematopoiesis | Kamimoto 2023 Fig. 3 |
| Kim 2024 PIP top-10 PD-risk genes: LRRK2, GBA, SNCA, VPS13C, GAK, MAPT, BST1, CTSB, DDC, SCARB2 | Kim 2024 Supp Table S5 |
| SNCA loss-of-function reduces α-syn aggregation | classical PD biology (e.g., Kim 2018; Burré 2018) |
| Adams 2024 ATAC sparse but interpretable in DA + glia | Adams 2024 Fig. 1 |

## Out of scope

- Combinatorial multi-gene KO (10! / pair scaling is prohibitive).
- ATAC-only re-analysis (we use ATAC for base-GRN edges; RNA for cell-state).
- Wet-lab CRISPR-KO confirmation.
- Non-SNpc cell types (microglia KO is a sister proposal candidate).

## Implementation choices

- Build the base-GRN once from Adams 2024 Multiome ATAC + RNA, then
  re-use across all 10 KO simulations.
- Use CellOracle's `simulate_shift` with 50 propagation steps;
  default downstream analysis recovers a `delta_X` matrix in
  expression space per cell.
- Compute per-cell-state shift vectors as the mean `delta_X` across
  PD-vulnerable DA cells (AGTR1+, RIT2+/CALB1- per v1 parkinson-da-
  vulnerability labels), compare to observed PD-minus-ctrl centroid
  in PCA space.
- SNCA-KO and HBB-KO controls are pre-registered in the YAML — no
  post-hoc cherry-picking.
