## scRNA-seq guidance (execute)

### Canonical pathway priors (use to interpret regulon / DEG results, NEVER as ground truth without literature backing)

These are textbook morphogen-signalling axes commonly recovered in
scRNA-seq screens; reference them in `analysis.md` only when your
own results match them, and tag every claim with a literature
citation:

- **SHH (ventral)**: NKX2-1, NKX6-2, GLI1, GLI2, FOXA2, HESX1
- **RA (posteriorising)**: HOXB3, HOXB7, HOXB8, HOXA2, FOXA2
- **FGF8 (telencephalic / retinal)**: NFIC, SALL4, POU3F3, EMX1, FEZF1
- **BMP4 (dorsalising)**: MSX2, PAX6, BARHL1, VSX2 (with caveats)
- **WNT (CHIR ↑)**: TCF7L2, SP8 (caudalisation; neural crest at high doses)
- **XAV939 (WNT ↓)**: telencephalic / forebrain enrichment

### Bootstrap unit discipline

For composition / proportion CIs, choose the resample unit that
matches the experimental unit, NOT the cell. Default for a
condition-screen design:
- Resample at the (condition × line) level for cross-line / cross-
  condition tests.
- Resample at the (donor × batch) level for reproducibility tests.
- Resample at the (cell) level ONLY for within-condition analyses
  with very large n_cells.

State the unit explicitly in `experiment_plan.md § Clustering of
observations` so W7 / cross_check can verify the choice.

### Standard tooling versions to pin

Record exact versions in `code/environment.lock.json` (Tier 1.3)
and `code/renv.lock` (R-side). At time of writing, the conventional
combo is:

- scanpy ≥ 1.10
- anndata ≥ 0.10
- pertpy ≥ 0.9 (for MMD / E-distance)
- pySCENIC 0.12.x with clustered v10 motif DB
- Seurat 5.x or 4.4.x (specify which)
- harmonypy or harmony (R) — specify
- glmnet (R) for AP/DV elastic-net scoring on Braun-style atlases

### Common failure modes to log as lessons

These have been observed in past OmiCairn scRNA runs; if you encounter
them, append to `knowledge/lessons.jsonl` with phase=execute:

- Truncated RDS cannot be readRDS-deserialised (50% on disk fails).
- tar.gz partial download is still extractable for head members.
- ArrayExpress per-file URLs sometimes 404 even when the BioStudies
  landing page lists them — use the FTP mirror as fallback.
- ST3 is Fisher enrichment, not compositional ground truth (don't
  expect Fig. 1j source data inside the supplementary tables).
- pySCENIC GRNBoost2 on >30k cells × >25k genes × >1.5k TFs takes
  4-8 CPU-hr; budget accordingly or use 20-run consensus instead
  of the published 100.
