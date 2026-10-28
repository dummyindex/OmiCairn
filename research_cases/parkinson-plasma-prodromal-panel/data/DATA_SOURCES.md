# parkinson-plasma-prodromal-panel data sources

Independent re-analysis of the Hällqvist 2024 8-protein plasma
panel on the deposited DIA spectra. PRIDE PXD046355 is the only
data dependency that is large (~10-30 GB) — fetched on the way.
Supplementary tables are small and can be fetched as text.

## PXD046355_hallqvist

- Role: Primary spectra (Hällqvist 2024 LC-MS/MS DIA plasma)
- Primary URL: https://www.ebi.ac.uk/pride/archive/projects/PXD046355
- Content: ~250 DIA runs; 99 PD + 36 HC + 72 iRBD (18 conv / 54 non-conv) + 41 disease ctrl
- Target path: `./datasets/PXD046355_hallqvist/`

## gan_2025_UKB_supp

- Role: Cross-platform direction-concordance reference (Gan/You 2025 UKB Olink)
- Primary URL: https://pubmed.ncbi.nlm.nih.gov/39979637/
- Content: Supplementary tables: per-protein incident-PD HR + p in UKB Olink Explore 1536
- Target path: `./datasets/gan_2025_UKB_supp/`

## bayati_2024_supp

- Role: DDC sanity-check (Bayati 2024)
- Primary URL: https://pmc.ncbi.nlm.nih.gov/articles/PMC10927779/
- Content: Supplementary tables: DDC PD-vs-HC effect-size across 7 cohorts × 3 platforms
- Target path: `./datasets/bayati_2024_supp/`

## uniprot_human_canonical

- Role: DIA-NN spectral library generation
- Primary URL: https://www.uniprot.org/proteomes/UP000005640
- Content: SwissProt human canonical proteome FASTA
- Target path: `./datasets/uniprot_human_canonical/`

_Manifest written 2026-05-21T13:41:39Z._