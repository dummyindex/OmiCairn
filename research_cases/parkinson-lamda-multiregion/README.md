# parkinson-lamda-multiregion

Cross-region generalisation test for the Goralski 2024 cortical
"LAMDA" (Lewy-Associated Molecular Dysfunction from Aggregates)
spatial-transcriptomic signature.

- **Mode:** DISCOVERY.
- **Signature source:** Goralski et al. Nat Commun 15:2642 (2024);
  Zenodo 10.5281/zenodo.10729767. Defined in cingulate cortex of
  8 PD/PDD/DLB donors via GeoMx DSP + CosMx spatial profiling.
- **Test substrate:** Fullard / N M et al. multi-region snRNA-seq
  atlas (CELLxGENE d5d0df8f-...; 100 donors, 5 regions: DMNX, GPi,
  SNpc, M1, V1; 2.1M nuclei).
- **Primary metric:** count of non-cortical (region × cell-type)
  pairs with significant LAMDA enrichment in PD at FDR<0.05 and
  Cohen's d > 0.25.
- **Expected outcome:** partial generalisation — likely M1 + SNpc
  excitatory/DA neurons replicate; DMNX + V1 do not. A clean global
  generalisation would be a paradigm-shifting "universal Lewy
  dysfunction program" headline; a complete non-generalisation
  would be a paradigm-restricting "cortex-only" finding.
- **Why this case is in the suite:** exercises OmiCairn's cross-modality
  scoring (GeoMx counts → snRNA-seq), 2-dimensional multiple-testing
  discipline (5 regions × 10 cell types = 50 tests), and held-out
  positive-control discipline (Goralski orbitofrontal donors not
  used in derivation are the same-modality positive bar). Also tests
  whether OmiCairn can run a multi-h5ad-slab integration without
  exhausting the disk budget.

Data is fetched on-the-way; nothing pre-staged. PDFs:
`claude_docs/claude_2026-05-21/parkinson_research/pdfs/goralski_2024_NatComm.pdf`,
`nm_2024_atlas_SciData.pdf`.
