# parkinson-gut-brain-axis

Signature-level alignment between the Wallen 2022 PD-gut-microbiome
shotgun-metagenomic signature and the Fullard 2024 multi-region
snRNA-seq atlas's DMNX (Braak stage 1 vagal target) transcriptome.

- **Mode:** DISCOVERY.
- **Microbiome source:** Wallen Z.D. et al. Nat Commun 13:6958 (2022).
  SRA PRJNA834801 + Zenodo 7246185. 490 PD + 234 ctrl fecal shotgun.
- **Brain source:** Fullard / N M 2024 multi-region atlas (CELLxGENE
  d5d0df8f-...), DMNX slab as primary test substrate; SNpc + V1 as
  spatial-control regions.
- **Bridging sources:** Storm 2025 MR-causal proteins; Hällqvist 2024
  plasma 8-panel (PRIDE PXD046355).
- **Primary metric:** shared-KEGG-module count between the top-100
  Wallen-bacterial-pathway list and the top-100 Fullard-DMNX-Reactome-
  enriched modules, vs. hypergeometric null.
- **Expected outcome:** marginal, possibly null. The cohorts do not
  overlap individually, so the test is fundamentally signature-level
  rather than causal. A clear positive (≥15 shared modules) would be
  the first published signature-level gut-brain alignment in PD; a
  null would be a public service to a field prone to overcalling.
- **Why this case is in the suite:** exercises OmiCairn's discipline
  *against* incorrect tests (per-individual mediation is impossible
  here and OmiCairn must refuse it), in favour of bridging tests
  (pathway alignment + Mendelian randomisation triangulation +
  pre-registered negative controls). Multi-omics integration test.

Data fetched on-the-way; nothing pre-staged. PDFs:
`wallen_2022_NatComm.pdf`, `nm_2024_atlas_SciData.pdf`,
`hallqvist_2024_NatComm.pdf` under `claude_docs/claude_2026-05-21/parkinson_research/pdfs/`.
