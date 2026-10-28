# parkinson-scenic-regulons

Cross-atlas replication of de-novo pySCENIC transcription-factor
regulons inferred from PD substantia-nigra dopaminergic neurons.

- **Mode:** DISCOVERY (systems biology, GRN inference).
- **Regulon source:** Fullard 2024 SNpc slab (CELLxGENE
  d5d0df8f-...; ~12 GB).
- **Replication atlases (3):** Smajic 2022 (GSE157783),
  Wang 2024 (GSE184950), Adams 2024 (GSE193688) — zero-shot
  AUCell scoring of the Fullard regulon dictionary.
- **Reference databases:** pySCENIC cisTarget human v10 motif DB;
  Kim 2024 multi-ancestry PD GWAS (GCST90275127) for TF gene-level
  GWAS enrichment.
- **Cross-workspace input:** reads prior workspace
  `parkinson-da-vulnerability` `results.json` for AGTR1+ depletion
  scores (H3).
- **Primary metric:** number of the top-20 Fullard-DA PD-vs-ctrl
  differentially active regulons that recover same-sign log-fold-
  change in *all three* held-out atlases (binomial null p<0.01).
- **Expected outcome:** 3-8 regulons replicate, partial-replication
  driven by AGTR1+ subcluster's pYY and NR2F2 TFs (the v1
  parkinson-da-vulnerability case's H2 anchors).
- **Why this case is in the suite:** exercises OmiCairn's pipeline
  orchestration (pySCENIC + AUCell), zero-shot cross-atlas transfer
  without re-running motif enrichment, and the
  TF-regulon-vs-marker-gene distinction the v1 batch did not test.
- **Hardest OmiCairn phase:** EXECUTE — pySCENIC GRNBoost2 is
  CPU-heavy; chunk per-cell-type.

**Implementation choice:** uses pySCENIC 0.12.1 with cisTarget
human v10 databases; AUCell is the standard zero-shot scoring
function. Limma-voom on donor × regulon matrix is the per-donor
statistical model (NOT per-cell DE — the field-wide pseudobulk
discipline the v1 cases also enforce).

Data fetched on-the-way. PDFs of source papers in
`claude_docs/claude_2026-05-21/parkinson_research_v2/pdfs/`.
