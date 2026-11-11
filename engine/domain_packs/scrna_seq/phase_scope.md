## scRNA-seq guidance (scope)

Single-cell datasets reach OmiCairn in three common shapes; the SCOPE
phase profiles each differently:

1. **10x / Parse processed count matrix (.h5ad / .RDS / .mtx)** —
   record n_cells, n_genes, library batches, donor/line metadata,
   QC pre-filter status, and which integration variable lives in
   obs (e.g. `donor`, `condition`, `batch`).

2. **Multi-cohort atlas (.h5ad referencing CellxGene / HCA / HNOCA)**
   — record cell-type taxonomy depth, embedding(s) present in obsm
   (X_umap, X_harmony, X_scvi, X_scanvi, X_css, X_rss), and any
   pre-trained model file (scANVI .pt, scPoli .pt) needed for
   projection.

3. **Raw FASTQ + Cell Ranger / Parse Pipeline output** — record
   accession, nominal library count, and whether re-alignment is in
   scope (typically NO under the 120-min EXECUTE budget).

For each dataset, also record:

- assay chemistry (`10x_3prime_v3`, `parse_splitpool_wt_mega_v3`,
  `marsseq2`, `dropseq`, `smartseq2`)
- presence of cell-hashing / genotype-demultiplexing metadata
- any external atlas required for projection / scoring

The asset-probe summary (above) tells you whether the deposited
objects are reachable. For scRNA-seq specifically, watch for
truncated downloads of large RDS files (typical Seurat object: 5-15 GB);
a partial RDS cannot be readRDS-deserialised even if 80% of bytes
arrived. Note any partial download in `data_profile.md` so PLAN
can choose between (a) waiting for complete fetch in EXECUTE,
(b) preferring the per-file Zenodo URL over the monolithic tarball,
or (c) declaring `DEFER_TO_LIMITATIONS` for hypotheses that depend
on the truncated asset.
