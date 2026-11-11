---
research_mode: METHOD
domain: neurodegeneration_foundation_model_benchmark
topic_slug: parkinson-foundation-embedding

research_question: |
  Do zero-shot Geneformer (95M-cell variant) and scGPT embeddings
  of Fullard 2024 SNpc dopaminergic-neuron nuclei separate PD from
  control donors better than a baseline log-CPM-PCA-LR pipeline at
  the donor level — and which foundation model wins?

  Cui et al. 2024 (scGPT, Nat Methods 21:1470) and Theodoris et al.
  2023+2024 (Geneformer, Nature 618:616 + 95M update) made strong
  claims about disease-stratification utility of foundation-model
  embeddings on cell atlases. Boiarsky 2024 and Kedzierska 2024
  have begun to publish benchmarks showing zero-shot foundation
  models are often *worse* than baseline log-CPM-PCA-LR in disease
  contexts. PD has not been placed on that benchmark map. This
  OmiCairn run does so.

  Hypotheses (pre-registered):

    H1 (primary — donor-level classification, foundation model vs.
         baseline):
        Donor-level leave-one-out (LOO) AUC for PD-vs-control
        classification using donor-mean foundation-model embedding
        + logistic regression is > 0.75 for at least one of
        Geneformer or scGPT, AND that AUC is > the log-CPM-PCA-LR
        baseline AUC by ΔAUC ≥ 0.05, with DeLong test p < 0.05.

    H2 (secondary — AGTR1+ subcluster recovery without supervision):
        Embedding-derived nearest-neighbour graph (Leiden γ = 0.6)
        in PD donors recovers an AGTR1+ vs. AGTR1- subcluster
        partition with Adjusted Rand Index ≥ 0.4 against the v1
        `parkinson-da-vulnerability` workspace labels.

    H3 (secondary — transfer-learning benefit):
        Geneformer fine-tuned for 1 epoch on Kamath A1 PD-vs-ctrl
        labels (GSE178265) generalises to Wang (GSE184950) and
        Adams (GSE193688) atlases with held-out AUC ≥ 0.7 in each,
        vs. ≤ 0.6 for the same architecture trained from scratch
        on the same fine-tune set.

    H4 (secondary — embedding-space robustness):
        Repeat H1 with three subsampling levels (5K / 25K / 50K
        nuclei per donor average); the ΔAUC vs. baseline must be
        stable (within ±0.05) across the three subsamples,
        otherwise the foundation-model advantage is sample-size
        dependent and that must be reported.

    H5 (secondary — cross-atlas zero-shot generalisation):
        Foundation-model embeddings trained on Fullard alone
        achieve PD-vs-ctrl AUC ≥ 0.65 when scored zero-shot on
        Wang and Adams (no fine-tuning). Below this floor, the
        embedding is Fullard-specific rather than generalising.

  Null payoff. If neither Geneformer nor scGPT beats log-CPM-PCA-LR
  (ΔAUC < 0.05), the field's optimism about single-cell foundation
  models for neurodegenerative disease is overstated for PD —
  matching the Boiarsky 2024 + Kedzierska 2024 findings in other
  diseases, and placing PD on the benchmark map.

data:
  location: ./research_cases/parkinson-foundation-embedding/data
  shared_cache: ./datasets
  description: |
    **No pre-staged raw data on disk.** Fetch into
    `./datasets/<accession>/`. Total ~37 GB.

    **Foundation-model weights:**

      - **Geneformer 95M variant** (HuggingFace
        `ctheodoris/Geneformer-V2-95M`; ~2 GB).
          https://huggingface.co/ctheodoris/Geneformer
        Target path: ./datasets/geneformer_95m/

      - **scGPT human-pan weights** (HuggingFace
        `bowang-lab/scGPT-human` or scGPT GitHub release;
        ~3 GB).
          https://github.com/bowang-lab/scGPT
        Target path: ./datasets/scgpt_human/

    **Atlases:**

      - **Fullard 2024 SNpc slab** (CELLxGENE d5d0df8f-...;
        ~12 GB) — primary embedding source.
        Target path: ./datasets/cellxgene_d5d0df8f_fullard/

      - **Kamath 2022 SNpc** (GEO GSE178265; ~8 GB) — for
        fine-tuning in H3.
        Target path: ./datasets/GSE178265/

      - **Wang 2024 SNpc** (GEO GSE184950; ~6 GB) — held-out
        transfer test.
        Target path: ./datasets/GSE184950/

      - **Adams 2024 SN Multiome** (GEO GSE193688; ~5 GB,
        RNA arm only) — held-out transfer test.
        Target path: ./datasets/GSE193688/

    **Cross-workspace input:** prior workspace `results.json`
    from `parkinson-da-vulnerability` for AGTR1+ subcluster
    labels (H2). If absent, H2 is reported as inapplicable.

    **Benchmark baseline:** scEval (Boiarsky 2024) for the log-
    CPM-PCA-LR pipeline. Public via the scEval GitHub.

  cohorts: [fullard_2024_SNpc, kamath_2022_SNpc, wang_2024_SNpc,
            adams_2024_SN_multiome]
  modalities: [snRNA_seq_10x, single_cell_foundation_model_embedding,
               supervised_classification]

success_criteria:
  primary_metric: foundation_model_vs_baseline_AUC_gain
  baseline: log_CPM_PCA_LR_scEval
  pre_registration: required
  honest_validation: required
  notes: |
    Primary success = at least one of Geneformer / scGPT achieves
    donor-LOO AUC > 0.75 AND ΔAUC ≥ 0.05 over the scEval log-CPM-
    PCA-LR baseline (DeLong p < 0.05).

    Honest-failure: ΔAUC < 0.05 = Tier-A negative; PD is on the
    Boiarsky/Kedzierska "foundation models don't help" list.

    Subsample sensitivity (H4) requires stability across 5K / 25K /
    50K nuclei subsamples. If unstable, the report headline must
    say "advantage is sample-size dependent".

    Zero-shot generalisation (H5) AUC ≥ 0.65 on Wang/Adams is the
    minimal cross-atlas portability bar.

figures_planned:
  - id: F1
    title: "Foundation-model embedding pipeline + QC"
    panels:
      - "Per-nucleus embedding dimension distribution (Geneformer CLS, scGPT mean-pooled)"
      - "Per-donor mean embedding heatmap (rows = donors, cols = top-50 PCs of mean embedding)"
      - "GPU memory + inference-time profile per atlas"
      - "Coverage check: cell-types embedded vs. cell-types observed (per atlas)"
  - id: F2
    title: "Donor-level PD-vs-ctrl classification (H1)"
    panels:
      - "LOO ROC curves: Geneformer + scGPT + log-CPM-PCA-LR baseline (3 curves) on Fullard"
      - "ΔAUC bar plot vs. baseline with DeLong test p-value annotation"
      - "Per-donor predicted probability boxplot (PD vs. ctrl) for the winning model"
      - "Calibration curve at the winning model"
  - id: F3
    title: "Unsupervised AGTR1+ subcluster recovery (H2)"
    panels:
      - "UMAP of foundation-model embedding for PD donors, colored by Leiden cluster"
      - "AGTR1+ cluster identification (highest AGTR1 mean) — boundary overlay"
      - "Adjusted Rand Index vs. v1 parkinson-da-vulnerability AGTR1+ labels (bar)"
      - "Confusion matrix: embedding-Leiden × prior-workspace AGTR1+ labels"
  - id: F4
    title: "Transfer-learning benefit (H3) + zero-shot generalisation (H5)"
    panels:
      - "Fine-tuned vs. from-scratch AUC bar on Wang + Adams held-out (H3)"
      - "Zero-shot (no fine-tune) AUC on Wang + Adams (H5)"
      - "Cross-atlas confusion: which atlases generalise best"
      - "Training-time / epoch curve for the fine-tune"
  - id: F5
    title: "Subsample sensitivity (H4) + honest-failure panel"
    panels:
      - "ΔAUC vs. baseline as function of nuclei-per-donor subsample (5K / 25K / 50K)"
      - "Permutation null: randomised PD/ctrl labels embedding-LR AUC distribution"
      - "Per-foundation-model contribution: ablation of CLS-only vs. mean-pool"
      - "Honest-failure ribbon: where on the Boiarsky/Kedzierska benchmark map does PD land?"

figures_not_in_scope:
  - "Foundation-model pre-training from scratch (use published weights only)"
  - "Wet-lab single-cell validation"
  - "Comparison to non-foundation deep models (CPA, scVI, etc.)"
  - "Cell-type-level classification (donor-level only is the primary endpoint)"

budgets:
  max_phase_wall_minutes: 240
  max_total_wall_minutes: 1440
  allow_web: true
  claude_model: claude-sonnet-4-6
  max_phase_budget_usd: 25.0

auto_approve: true
---

# parkinson-foundation-embedding — Geneformer + scGPT vs. baseline on PD SNpc donor classification

## Source materials

- Theodoris, C.V., Xiao, L., Chopra, A., et al. *Transfer learning
  enables predictions in network biology.* Nature 618:616 (2023).
  Geneformer.
- Cui, H., Wang, C., Maan, H., et al. *scGPT: toward building a
  foundation model for single-cell multi-omics using generative
  AI.* Nature Methods 21:1470 (2024). scGPT.
- Boiarsky, R., Kedzierska, K.Z., et al. *Benchmarking single-cell
  foundation models for disease applications.* (2024). scEval +
  baseline harness.
- Kedzierska, K.Z., et al. *Assessing the limits of zero-shot
  foundation models in single-cell biology.* (2024).
- N M, P., Fullard, J.F., Clarence, T., et al. Sci Data 11:1274
  (2024). Fullard atlas.
- Kamath et al. Nat Neurosci 25:588 (2022); Wang et al. Sci Adv
  10:eadi8287 (2024); Adams et al. Nat Aging 4:364 (2024).
  Atlas-cohort sources.

## Why this matters for OmiCairn

This is the v2 batch's only deep-learning case, and the only one
that places PD on the foundation-model-disease-benchmark map. It
exercises OmiCairn's ability to:

1. **Stand up two large transformer pipelines** (Geneformer +
   scGPT) with fp16 chunked inference inside a workstation GPU
   budget.
2. **Use the scEval baseline harness** so the apples-to-apples
   comparison is reproducible.
3. **Pre-register null-result framing** — Boiarsky/Kedzierska
   already showed foundation models can lose to baselines, so
   the H1 ≥ 0.05 ΔAUC bar is realistic.
4. **Cross-workspace coordination** (H2 reads v1 AGTR1+ labels
   rather than re-running clustering).

## Known prior results (cross-check)

| Claim | Reference |
|---|---|
| Geneformer 95M beats 30M on disease discrimination (CMV, COVID) | Theodoris 2024 update |
| scGPT zero-shot underperforms log-CPM-PCA in many disease contexts | Boiarsky 2024 |
| Kedzierska 2024 finds foundation models worse than PCA on 4/6 cancer datasets | Kedzierska 2024 |
| AGTR1+ DA subcluster discovered in Kamath 2022 (cluster Aut1) | Kamath 2022 Fig. 2 |

## Out of scope

- Foundation-model pre-training (use published weights only).
- Wet-lab validation.
- Non-foundation deep model comparison (scVI, CPA).
- Per-cell-level classification (donor-level only).

## Implementation choices

- Geneformer 95M (`ctheodoris/Geneformer-V2-95M`) — fp16, chunked
  inference, 50K-nucleus subsample cap if needed.
- scGPT human-pan weights, mean-pool embedding.
- scEval (Boiarsky 2024) provides log-CPM-PCA-LR baseline —
  matched protocol with published benchmarks.
- Donor-level mean of cell-level CLS tokens; LOO logistic
  regression; DeLong test for AUC comparison.
- 1-epoch fine-tune at lr=5e-5, batch_size=32 (Geneformer 2024
  tutorial defaults).
