# parkinson-multimodal-ppmi-trajectory data sources

GPLVM-based 1-D disease-time inference from publicly-summarised
longitudinal PPMI endpoints. No individual-level data, no DUA.
Total ~200 MB.

## russo_2025_supp

- Role: Russo 2025 Lancet Neurology CSF SAA Vmax/Tmax/AUC group means
- Primary URL: https://www.thelancet.com/journals/laneur/
- Note: Exact DOI to be confirmed during READ; provisional 10.1016/S1474-4422(25)00094-2.
- Target path: `./datasets/russo_2025_supp/`

## bayati_2024_supp

- Role: Bayati 2024 Acta Neuropathologica Olink + CSF + plasma + urine protein medians
- Primary URL: https://www.ncbi.nlm.nih.gov/pmc/articles/PMC10927779/
- Target path: `./datasets/bayati_2024_supp/`

## hallqvist_2024_supp

- Role: Hällqvist 2024 Nat Commun 8-protein plasma panel by visit (supp tables only)
- Primary URL: https://www.ncbi.nlm.nih.gov/pmc/articles/PMC11189460/
- Target path: `./datasets/hallqvist_2024_supp/`

## burciu_2023_supp

- Role: Burciu 2023 Frontiers Neurology per-visit free-water dMRI group means
- Primary URL: https://www.frontiersin.org/journals/neurology
- Target path: `./datasets/burciu_2023_supp/`

## simuni_2024_supp

- Role: Simuni 2024 Movement Disorders LRRK2-G2019S SAA-stratified group-mean curves
- Primary URL: https://movementdisorders.onlinelibrary.wiley.com/journal/15318257
- Target path: `./datasets/simuni_2024_supp/`

## marek_2018_supp

- Role: Marek 2018 + downstream PPMI baseline + longitudinal MDS-UPDRS-III group means
- Primary URL: https://onlinelibrary.wiley.com/doi/10.1002/acn3.644
- Target path: `./datasets/marek_2018_supp/`

## Fetchability audit (H5)

EXECUTE must classify each supplement as one of:
- (a) machine-readable TSV / XLSX
- (b) PDF-table only (requires `tabula-py` or similar)
- (c) image-only / not fetchable

_Manifest written 2026-05-21T17:08:17Z._