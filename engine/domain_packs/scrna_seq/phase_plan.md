## scRNA-seq guidance (plan)

### Method-parity checklist

When reproducing a published scRNA-seq paper, declare in
`experiment_plan.md § Methods deviations` how you will match each
load-bearing methodological choice:

1. **Integration method**: name the algorithm (Harmony / RSS-CSS /
   scVI / scANVI / fastMNN / Seurat CCA / RPCA). For reproductions,
   match the source paper exactly. Substitutions are legitimate but
   must be quantified (cluster-agreement loss, ARI, NMI vs published).
2. **Cell-cycle regression**: regress S.Score / G2M.Score? Drop PCs
   correlated with cell cycle? Specify the threshold.
3. **QC thresholds**: per-assay defaults differ:
   - 10x 3' v3: nGene > 1000 typical, % mito < 10
   - Parse split-pool v3 (MG100): nUMI > 750 < 20000, % mito < 10
   - Smart-seq2: nGene > 5000, very different exonic-intronic ratio
4. **Cluster resolution** (Leiden / Louvain): name the value
   explicitly (e.g. resolution=0.8); reproductions often miss the
   exact cluster count by ±1-2 even with faithful methods —
   acknowledge this in the decision rule.
5. **Hashtag / SNP demultiplexing**: name the tool
   (Seurat HTODemux, vireo, demuxlet, freemuxlet, cellsnp-lite +
   vireo) and the positive_quantile / cluster threshold.
6. **Regulon inference (SCENIC)**: pySCENIC version, motif DB
   (clustered v9 vs v10 vs custom), GRNBoost2 vs GENIE3, AUCell
   threshold, edge weight cutoff.

### Hypothesis-DAG patterns specific to scRNA-seq

Common dependency cascades — each `hypotheses[i].depends_on`
declaration should reflect them honestly:

- A composition-based H usually depends on
  `dataset:<accession>` AND `code:<author_repo>` (the author's
  cluster-annotation script).
- A regulon H depends on the above PLUS
  `compute:scenic_motif_db_v10` AND ~4-8 CPU-hr GRNBoost2.
- An atlas-projection H depends on the above PLUS
  `model:<braun_2023_scanvi | hnoca_scpoli | other>`.
- A cross-line / cross-batch H depends on metadata being
  documented in obs (NIM/MNIM mappings, batch IDs); declare
  `dataset:<accession>:metadata` so the asset probe can flag a
  bare-counts upload that lacks the splits column.

### Power section

For composition concordance: "MAE_pp (cluster, condition) ≤ X pp
detectable with ≥80% power at n=N cells" is the standard form. If
the deposit is too small to detect a 10pp difference at 80%
power, say so and weaken the threshold.

For distance-metric reproducibility (KLD / MMD / E-distance): the
expected effect size from the paper, the n_conditions, and a
permutation-test sketch (B=500 permutations gives a resolution
floor of p<0.002).

### Figures budget

Plan one multi-panel analysis figure per hypothesis or evidence
axis — typically: re-derived atlas, enrichment / contrast vs the
source study, regulon / TF analysis (where applicable),
reproducibility-screen distance hierarchy, atlas projection /
cross-line consistency, and a method-parity check (or
microfluidic-vs-organoid panel). An optional study-design schematic
is declared with `kind: "schematic"`.
