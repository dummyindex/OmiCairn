---
research_mode: DISCOVERY
domain: neurodegeneration_GRN_scRNAseq
topic_slug: parkinson-scenic-regulons

research_question: |
  Which transcription-factor (TF) regulons, inferred de novo by
  pySCENIC from the Fullard 2024 multi-region snRNA-seq atlas
  (CELLxGENE d5d0df8f-...) on substantia-nigra pars compacta (SNpc)
  dopaminergic (DA) neurons, are differentially active between
  Parkinson's-disease (PD) and age-matched control donors — and do
  those regulon-activity shifts replicate in the independent
  Smajic / Wang / Adams SNpc atlases when the same regulon
  dictionary is scored zero-shot via AUCell?

  Selective-vulnerability biology in PD has so far been read at the
  cluster + marker-gene resolution (Kamath 2022 AGTR1+; Wang 2024
  RIT2+/CALB1-). The TF-network layer above the markers has not
  been formally tested for *cross-atlas reproducibility* — i.e., is
  the dysregulation a coherent regulon shift, or only an apparent
  marker-gene shift that scrambles when the network is recomputed
  from a different atlas? This OmiCairn run pre-registers that test.

  Hypotheses (pre-registered):

    H1 (primary — regulon replication across three independent
         atlases):
        Let R = the top-20 SNpc-DA PD-vs-control differentially
        active regulons in the Fullard atlas (FDR < 0.05, limma-
        voom on the donor × regulon-AUCell matrix with age + sex +
        post-mortem interval covariates). At least 3 of those 20
        regulons recover a same-sign log-fold-change in regulon-
        AUCell score in *all three* held-out atlases (Smajic
        GSE157783, Wang GSE184950, Adams GSE193688), with a
        binomial test p < 0.01 against a null of 0.5 sign-agreement
        for each regulon × atlas combination.

    H2 (secondary — TF gene-level GWAS enrichment):
        The PD-up-regulated regulon set (FDR < 0.05 in Fullard,
        same-sign in ≥ 2 of 3 replication atlases) is enriched by
        hypergeometric test (q < 0.05) for TFs whose genes carry
        PD-risk fine-mapped variants in the Kim 2024 multi-ancestry
        GWAS (GCST90275127 Supplementary Table S5).

    H3 (secondary — alignment with AGTR1+ depletion):
        Per-donor AUCell scores of the consensus PD-up regulons
        rank-correlate (Spearman ρ > 0.3, p < 0.05) with the
        AGTR1+ DA depletion score derived in the v1
        `parkinson-da-vulnerability` workspace (read from prior
        workspace `results.json`), at the donor level inside
        Fullard SNpc.

    H4 (secondary — donor-level classification):
        Leave-one-atlas-out donor-level random forest using only
        the top-50 SNpc DA regulon AUCell scores as features
        achieves an AUC ≥ 0.70 for PD-vs-control classification
        (95% bootstrap CI excluding 0.5).

    H5 (secondary — clustering-resolution robustness):
        Re-run the top-level Leiden clustering at γ ∈ {0.4, 0.8,
        1.2}; the H1 same-sign replication count varies by ≤ 1
        regulon across the three resolutions (the dominant failure
        mode the v1 parkinson-da-vulnerability case logged).

  Null payoff. If consensus regulons fail to replicate across
  atlases (H1 = 0/20 same-sign in ≥ 2 atlases), the field's
  TF-level mechanistic claim is at the cluster-resolution layer,
  not the regulon-network layer. That is an informative Tier-A
  negative.

data:
  location: ./research_cases/parkinson-scenic-regulons/data
  shared_cache: ./datasets
  description: |
    **No pre-staged raw data on disk.** Every dataset below is
    publicly deposited and MUST be fetched by phase sessions into
    the shared cache `./datasets/<accession>/`. Acceptable reasons
    to defer a fetch, documented in `analysis.md § Limitations`:
    (1) HTTP 404/410 / accession removed; (2) sha256 verification
    fails; (3) `budgets.allow_web` is false. Size alone is not an
    acceptable reason.

    **Primary atlases:**

      - **Fullard 2024 multi-region atlas (regulon source).**
        CELLxGENE collection d5d0df8f-4eee-49d8-a221-a288f50a1590,
        SNpc slab only (~12 GB anndata after subsetting).
          https://cellxgene.cziscience.com/collections/d5d0df8f-4eee-49d8-a221-a288f50a1590
        Target path: ./datasets/cellxgene_d5d0df8f_fullard/

      - **Smajic 2022 SNpc snRNA-seq (replication atlas 1).**
        GEO GSE157783 (~3 GB).
          https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE157783
        Target path: ./datasets/GSE157783/

      - **Wang 2024 SNpc (replication atlas 2).**
        GEO GSE184950 (~6 GB raw → ~2 GB filtered).
          https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE184950
        Target path: ./datasets/GSE184950/

      - **Adams 2024 SN Multiome (replication atlas 3, RNA arm).**
        GEO GSE193688 (~5 GB).
          https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE193688
        Target path: ./datasets/GSE193688/

    **Reference databases:**

      - **pySCENIC motif + cisTarget databases** (human v10,
        ~6 GB; public).
          https://resources.aertslab.org/cistarget/
        Target path: ./datasets/scenic_resources/

      - **Kim 2024 PD multi-ancestry GWAS sumstats + fine-mapping.**
        GWAS Catalog GCST90275127 (~250 MB).
          https://www.ebi.ac.uk/gwas/studies/GCST90275127
        Target path: ./datasets/GCST90275127_kim/

    **Cross-workspace input:** prior workspace `results.json` from
    `parkinson-da-vulnerability` provides per-donor AGTR1+
    depletion scores for H3. If not present, H3 is reported as
    inapplicable.

  cohorts:
    [fullard_2024_SNpc, smajic_2022_SNpc, wang_2024_SNpc,
     adams_2024_SN_multiome]
  modalities: [snRNA_seq_10x, snRNA_seq_10x_multiome,
               gwas_summary_statistics]

success_criteria:
  primary_metric: regulon_replication_count_top20
  baseline: random_shuffled_regulon_dictionary
  pre_registration: required
  honest_validation: required
  notes: |
    Primary success = ≥ 3 of the top-20 Fullard-derived PD-vs-ctrl
    DA regulons recover same-sign log-fold-change in *all three*
    held-out atlases. Binomial p < 0.01 against null = 0.5 sign
    agreement.

    Honest-failure: 0/20 same-sign in any atlas = Tier-A negative;
    means the regulon-network layer doesn't replicate, the
    cluster-marker layer does. Must be reported as headline.

    Robustness ribbon (H5): the Leiden-resolution swing across
    γ ∈ {0.4, 0.8, 1.2} must be reported even if H1 passes — the
    v1 parkinson-da-vulnerability docs log this as the dominant
    failure mode.

figures_planned:
  - id: F1
    title: "pySCENIC regulon discovery in Fullard SNpc DA neurons"
    panels:
      - "Number of regulons recovered per cell-type per donor (boxplot, PD vs. ctrl)"
      - "Top-20 differentially active regulons (limma-voom volcano: log2FC AUCell vs. -log10 FDR)"
      - "Heatmap: top-20 regulons × donor, color = AUCell z-score, annotated PD/ctrl + sex + age"
      - "Network plot of top-3 PD-up regulons (TF + target gene edges)"
  - id: F2
    title: "Zero-shot regulon replication across atlases (H1)"
    panels:
      - "Per-regulon × atlas log2FC heatmap (rows = top-20 regulons, cols = Fullard/Smajic/Wang/Adams), color = signed log2FC, asterisks for FDR<0.05"
      - "Forest plot: each top-20 regulon's effect size with 95% bootstrap CI, four atlases overlaid"
      - "Same-sign concordance bar: how many of top-20 are same-sign in each held-out atlas"
      - "Cumulative binomial p-value plot vs. number of regulons required to call replication"
  - id: F3
    title: "GWAS-TF enrichment (H2) and AGTR1+ alignment (H3)"
    panels:
      - "Hypergeometric over-representation barplot: PD-up regulon TF gene set vs. Kim 2024 fine-mapped genes (q values)"
      - "Spearman scatter: per-donor consensus-PD-up regulon AUCell vs. AGTR1+ depletion score from prior workspace"
      - "Per-TF risk-variant column annotation table (TFs ranked by PIP)"
      - "Sankey: TF → target gene → PD-risk variant"
  - id: F4
    title: "Donor-level classification (H4)"
    panels:
      - "Leave-one-atlas-out ROC curves (4 atlases, 4 curves) for top-50 regulon RF classifier"
      - "Per-fold AUC barplot vs. log-CPM-PCA-LR baseline"
      - "Feature importance: top-10 regulon contributions to classification"
      - "Calibration curve at the pooled prediction level"
  - id: F5
    title: "Robustness ribbon (H5) + honest failure panel"
    panels:
      - "Replication-count vs. Leiden γ ∈ {0.4, 0.8, 1.2} ribbon plot"
      - "Per-cell-type sensitivity: same H1 in SNpc DA vs. excitatory vs. microglia (negative-control cell types)"
      - "Permutation null: random-shuffled regulon dictionary applied to held-out atlas; AUCell distribution overlay"
      - "Down-sampling sensitivity: same H1 with 50% / 25% of Fullard donors"

figures_not_in_scope:
  - "Full re-running of pySCENIC GRNBoost2 on each held-out atlas (cost-prohibitive; zero-shot AUCell only)"
  - "ATAC-based base-GRN refinement (separate proposal: parkinson-celloracle-ko)"
  - "Wet-lab validation of any TF"
  - "Cell-type beyond DA neurons (microglia, astrocyte regulons noted but not formally tested)"

budgets:
  max_phase_wall_minutes: 240
  max_total_wall_minutes: 1440
  allow_web: true
  claude_model: claude-sonnet-4-6
  max_phase_budget_usd: 25.0

auto_approve: true
---

# parkinson-scenic-regulons — Cross-atlas replication of pySCENIC transcription-factor regulons of PD vulnerability

## Source materials

- N M, P., Fullard, J.F., Clarence, T., et al. *A multi-region single
  nucleus transcriptomic atlas of Parkinson's disease.* Sci Data
  11:1274 (2024). CELLxGENE d5d0df8f-... .
- Smajic, S., Prada-Medina, C.A., Landoulsi, Z., et al. *Single-cell
  sequencing of human midbrain reveals glial activation and a
  Parkinson-specific neuronal state.* Brain 145:964 (2022).
  GEO GSE157783.
- Wang, Q., Wang, M., Choi, I., et al. *Molecular profiling of human
  substantia nigra...* Sci Adv 10:eadi8287 (2024). GEO GSE184950.
- Adams, L., Song, M.K., Yuen, S., Tanaka, Y., Kim, Y.S. *A single-
  nuclei paired multiomic analysis of the human midbrain reveals
  age- and Parkinson's disease-associated glial changes.* Nat Aging
  4:364 (2024). GEO GSE193688.
- Aibar, S., González-Blas, C.B., Moerman, T., et al. *SCENIC:
  single-cell regulatory network inference and clustering.* Nat
  Methods 14:1083 (2017); van de Sande et al. *A scalable SCENIC
  workflow for single-cell gene regulatory network analysis.* Nat
  Protoc 15:2247 (2020) (pySCENIC tool).
- Kim, J.J., Bandres-Ciga, S., Reynolds, R.H., et al. *Multi-ancestry
  genome-wide meta-analysis in Parkinson's disease.* Nat Genet
  56:27 (2024). GWAS Catalog GCST90275127.

## Why this matters for OmiCairn

The v1 batch tested PD biology at the cluster + marker-gene layer.
This case tests the layer *above* that — TF-regulon networks — and
asks whether dysregulation is a coherent network shift or just a
marker-gene shift. Different mechanism, different statistics,
different null.

OmiCairn challenges this exercises:

1. **Heavy pipeline orchestration**: pySCENIC GRNBoost2 + AUCell on
   ~30K SNpc DA nuclei (chunk per-cell-type to stay in memory).
2. **Zero-shot transfer**: build regulons in one atlas, score them
   in three held-out atlases without re-running motif enrichment.
3. **Cross-workspace coordination** (H3): read prior workspace
   `results.json` rather than re-running the v1 case.
4. **Robustness reporting** (H5): pre-emptive Leiden-γ swing, the
   field-level methodological pitfall the v1 case flagged.

## Known prior results (cross-check)

| Claim | Reference |
|---|---|
| pySCENIC recovers ~300-500 robust regulons per midbrain dataset | van de Sande 2020 Nat Protoc |
| TF-level vs marker-level conclusions can disagree | Aibar 2017 (general); Kamath 2022 (PD-specific marker work) |
| Kim 2024 fine-mapping table lists 78 high-confidence PD-risk genes | Kim 2024 Supp Table S5 |

## Out of scope (for this iteration)

- Full pySCENIC re-runs on Smajic/Wang/Adams (AUCell-only zero-shot transfer).
- ATAC-based base-GRN refinement (sister case: parkinson-celloracle-ko).
- Wet-lab TF validation.
- Non-DA cell types as primary analysis target.

## Implementation choices

- Restrict pySCENIC GRNBoost2 co-expression module to within-cell-
  type DA nuclei (~30K nuclei across Fullard PD + ctrl donors), then
  AUCell-score the held-out atlases using the pre-built regulon
  dictionary. Avoids re-running motif enrichment per atlas.
- Donor-level statistics use limma-voom on the regulon-AUCell ×
  donor matrix with PD-status as test variable, age + sex + PMI as
  covariates.
- Anchor SNCA, LRRK2, GBA, AGTR1 TFs in the regulon prior so
  benchmarks vs. expected biology are meaningful.
