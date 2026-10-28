# parkinson-tcell-foothold

Cross-atlas replication of the Ma 2025 PD-SN α-synuclein-reactive
CD8+ T-cell transcriptomic signature, scored on snRNA-seq atlases
that lack paired TCR-seq.

- **Mode:** DISCOVERY (transcriptomic-footprint inference).
- **Signature source:** Ma J. et al. Nat Commun 16 s41467-025-62478-3
  (2025); PMC12322057. snRNA-seq + 10x Visium + scTCR-seq across
  44 donors of PD SN + cingulate.
- **Test atlases (4):** Kamath (GSE178265), Wang (GSE184950),
  Adams (GSE193688), Fullard / N M (CELLxGENE d5d0df8f-...).
- **Spatial validation:** Martirosyan Visium (GSE243639) + Ma's
  own Visium portion.
- **Primary metric:** number of independent atlases (of 4) showing
  PD-vs-ctrl log2FC > 0 in the donor-pseudobulk T-cell-module score
  at FDR<0.1.
- **Expected outcome:** 2-3 of 4 replicate. The cleanest replication
  is expected in Fullard (largest n) and partial in Kamath (smaller
  n, but high-quality immune-cluster recovery).
- **Why this case is in the suite:** exercises OmiCairn's pseudobulk-vs-
  per-cell DE discipline (the field-wide methodological pitfall),
  cross-atlas module scoring with different label conventions, and
  pre-registered negative-control modules (generic T-cell exhaustion
  module H5).

Data fetched on-the-way. PDFs of source papers in
`claude_docs/claude_2026-05-21/parkinson_research/pdfs/`.
