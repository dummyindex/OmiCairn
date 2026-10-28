# parkinson-foundation-embedding

Zero-shot + 1-epoch-fine-tuned benchmark of Geneformer (95M) and
scGPT against log-CPM-PCA-LR baseline (scEval) on PD-vs-control
donor-level classification of Fullard SNpc DA nuclei, with
held-out Wang + Adams transfer test.

- **Mode:** METHOD (deep-learning benchmark).
- **Foundation models:** Geneformer 95M
  (`ctheodoris/Geneformer-V2-95M`), scGPT human-pan
  (`bowang-lab/scGPT`).
- **Atlases:** Fullard 2024 SNpc slab (primary embedding source);
  Kamath 2022 (fine-tune labels); Wang 2024 + Adams 2024
  (held-out transfer).
- **Baseline:** scEval (Boiarsky 2024) log-CPM-PCA-LR pipeline
  — apples-to-apples with published foundation-model benchmarks.
- **Cross-workspace input:** v1 `parkinson-da-vulnerability`
  AGTR1+ subcluster labels for H2 (unsupervised subtype
  rediscovery).
- **Primary metric:** at least one of {Geneformer, scGPT}
  achieves donor-LOO AUC > 0.75 AND ΔAUC ≥ 0.05 over baseline
  with DeLong p < 0.05.
- **Expected outcome:** uncertain — Boiarsky/Kedzierska have
  shown foundation models often lose to PCA baselines on
  disease tasks. A null here puts PD on that benchmark map; a
  positive would be a counter-finding worth publishing.
- **Why this case is in the suite:** the only deep-learning v2
  case. Tests OmiCairn's ability to orchestrate large transformer
  inference (fp16, chunked) inside a workstation GPU budget and
  to honestly report on a methodological benchmark with
  pre-registered null framing.
- **Hardest OmiCairn phase:** EXECUTE — GPU memory; fp16 + chunked
  inference; 50K-nuclei subsample cap if needed.

**Implementation choice:** scEval baseline keeps the comparison
matched to the published foundation-model literature. Donor-mean
of CLS tokens; LOO logistic regression; DeLong AUC test.

Data fetched on-the-way (~37 GB total).
