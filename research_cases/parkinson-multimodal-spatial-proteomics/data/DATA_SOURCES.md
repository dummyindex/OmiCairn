# parkinson-multimodal-spatial-proteomics data sources

Three-modality MOFA+ joint embedding of PD SN: snRNA + Visium
(Martirosyan 2024) + GeoMx (Ma 2025) + LC-MS/MS proteomics
(Zhu 2024). Pre-registered pairwise-vs-3-modal ablation.

## GSE243639

- Role: snRNA-seq + Visium (Martirosyan 2024 PD SN)
- Primary URL: https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE243639
- Target path: `./datasets/GSE243639/`

## ma_2025_geomx

- Role: GeoMx spatial (Ma 2025 SN). Confirm exact NCBI accession in Data Availability.
- Primary URL: https://www.ncbi.nlm.nih.gov/pmc/articles/PMC12322057/
- Fallback: https://zenodo.org/records/10729767
- Note: If Ma 2025 GeoMx unreachable, use Goralski Zenodo 10.5281/zenodo.10729767 cingulate GeoMx as fallback.
- Target path: `./datasets/ma_2025_geomx/`

## PXD030142_zhu

- Role: Zhu 2024 DLPFC LC-MS/MS proteomics
- Primary URL: https://www.ebi.ac.uk/pride/archive/projects/PXD030142
- Target path: `./datasets/PXD030142_zhu/`

## goralski_lamda

- Role: Goralski LAMDA cortical signature gene list (H3 enrichment reference)
- Primary URL: https://zenodo.org/records/10729767
- Target path: `./datasets/goralski_lamda/`

## hemedan_2024_boolean

- Role: Hemedan 2024 Boolean-PD key-regulator gene list (H3 enrichment reference)
- Primary URL: https://gitlab.lcsb.uni.lu/lcsb-biocore/publications/hemedan23-boolean-modelling-of-pd
- Target path: `./datasets/hemedan_2024_boolean/`

_Manifest written 2026-05-21T17:08:17Z._