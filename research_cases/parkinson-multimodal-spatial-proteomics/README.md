# parkinson-multimodal-spatial-proteomics

Three-modality MOFA+ joint embedding of PD substantia-nigra:
Martirosyan 2024 snRNA-seq + Visium (GSE243639), Ma 2025 GeoMx
(PMC12322057), and Zhu 2024 DLPFC LC-MS/MS proteomics
(PXD030142). Pre-registered H5 pairwise-vs-3-modal ablation.

- **Mode:** DISCOVERY (multi-modal integration ablation).
- **snRNA + Visium:** Martirosyan 2024 GSE243639 (~10 GB).
- **GeoMx:** Ma 2025 PMC12322057 deposit; fallback to Goralski
  Zenodo 10.5281/zenodo.10729767 cingulate GeoMx if not
  fetchable.
- **Proteomics:** Zhu 2024 DLPFC LC-MS/MS PRIDE PXD030142
  (~3 GB).
- **Reference gene lists (H3 enrichment):** Goralski LAMDA +
  Hemedan 2024 Boolean-PD key-regulator union.
- **Primary metric:** ≥ 1 MOFA+ factor explains > 5% variance in
  *each* of the three modalities AND is PD-associated
  (Wilcoxon p < 0.05).
- **Expected outcome:** uncertain — pairwise MOFA+ may recover
  most of the biology, in which case H5 will show no 3-modal
  necessity. Both H1 outcomes are informative.
- **Why this case is in the suite:** the v2 batch's cleanest test
  of the "is multi-modal really worth it" question that the field
  asks repeatedly. Exercises OmiCairn's ability to coordinate
  cell2location spot deconvolution, MOFA+ fitting with patient-
  group-shared axis (no donor overlap), and pre-registered
  ablation against pairwise alternatives.
- **Hardest OmiCairn phase:** EXECUTE — modality alignment (gene ↔
  spot ↔ peptide) is fiddly; cell2location is GPU-friendly but
  memory-heavy.

**Implementation choice:** patient-group as MOFA+ shared axis
(no donor-ID alignment). cell2location with Martirosyan snRNA as
reference profile for Visium + GeoMx deconvolution. mofapy2 0.7.1
pinned per v2 risk register.

Data fetched on-the-way (~18 GB total).
