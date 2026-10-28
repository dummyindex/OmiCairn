# parkinson-celloracle-ko

In-silico knockout of the top 10 PD-risk GWAS genes (Kim 2024) in
Fullard 2024 SNpc dopaminergic neurons via CellOracle, with SNCA
protective + HBB null internal controls.

- **Mode:** HYBRID (METHOD + DISCOVERY).
- **Atlases:** Fullard 2024 SNpc (CELLxGENE d5d0df8f-...) for
  snRNA-seq + observed PD-vs-ctrl shift; Adams 2024 (GSE193688)
  Multiome ATAC for base-GRN construction.
- **Reference base-GRN:** CellOracle midbrain reference at
  Zenodo 10.5281/zenodo.7755357 (used in H5 sensitivity check).
- **GWAS:** Kim 2024 multi-ancestry sumstats + fine-mapping
  (GCST90275127) — top-10 PIP genes drive the KO panel.
- **Boolean prior:** Hemedan 2024 PD prodromal-cluster regulatory
  module (H3 enrichment target).
- **Primary metric:** mean cosine alignment between predicted
  post-KO state shift and observed PD-minus-ctrl centroid shift
  (target > 0.30, permutation p < 0.01).
- **Expected outcome:** mean cosine 0.2-0.4, with LRRK2 and GBA
  high, MAPT and VPS13C lower; SNCA protective + HBB null. A null
  H1 (cosine ≤ 0.15) is an informative Tier-A negative.
- **Why this case is in the suite:** the only counterfactual /
  perturbation case in v1 + v2 batches. Exercises OmiCairn's ability
  to chain ATAC base-GRN → snRNA cell-state → in-silico KO sweep
  while pre-registering internal positive + negative controls.
- **Hardest OmiCairn phase:** EXECUTE (base-GRN construction is
  moderately I/O heavy; KO sweep is GPU-friendly).

**Implementation choice:** CellOracle 0.18.0 with Adams 2024 ATAC
base-GRN as primary, midbrain reference (Zenodo 7755357) as
sensitivity control. SNCA + HBB internal controls are
pre-registered in the YAML.

Data fetched on-the-way. PDFs of source papers in
`claude_docs/claude_2026-05-21/parkinson_research_v2/pdfs/`.
