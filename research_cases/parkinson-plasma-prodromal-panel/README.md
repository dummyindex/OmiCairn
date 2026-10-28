# parkinson-plasma-prodromal-panel

Independent re-analysis of the Hällqvist 2024 8-protein plasma
prodromal-PD panel on deposited LC-MS/MS DIA spectra.

- **Mode:** CONFIRMATORY (cross-pipeline replication).
- **Spectra source:** Hällqvist J. et al. Nat Commun 15:4759 (2024);
  PRIDE PXD046355. ~250 plasma DIA runs across 99 PD + 36 HC +
  72 iRBD (18 converters + 54 non-converters at 7-year follow-up)
  + 41 disease controls.
- **Panel under test (8 proteins):** Granulin/GRN, MASP2, HSPA5/BiP,
  PTGDS, ICAM1, complement C3, DKK3, SERPING1.
- **Cross-platform reference:** Gan / You 2025 UKB Olink incident-PD
  supplementary tables (Nat Aging 5:642) — used for direction-
  concordance only; no individual-level UKB Olink data required.
- **Sanity check:** Bayati 2024 DDC paper (PMC10927779) — DDC should
  reproduce as PD-elevated in PXD046355 re-processing.
- **Primary metric:** 5-fold CV AUC of the locked 8-panel logistic
  regression on iRBD-converter vs. non-converter, on re-processed
  PXD046355 spectra. Primary success = AUC ≥ 0.85.
- **Expected outcome:** AUC 0.80-0.95 (lower than published 1.0 due
  to pipeline divergence). Partial generalisation to UKB Olink at
  6/6 direction concordance is achievable.
- **Why this case is in the suite:** exercises OmiCairn's
  mass-spectrometry processing (DIA-NN) — outside the
  snRNA-seq/GWAS comfort zone. Tests panel-locking discipline (no
  retraining during validation), sub-panel decomposition (H5), and
  the ability to use published supplementary tables as
  cross-platform external references when individual-level data is
  gated.

Data fetched on-the-way (PRIDE PXD046355 is ~10-30 GB of raw spectra).
PDFs: `hallqvist_2024_NatComm.pdf`, `bayati_2024_ActaNeuro.pdf` in
`claude_docs/claude_2026-05-21/parkinson_research/pdfs/`.
