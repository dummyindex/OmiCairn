# parkinson-metabolic-flux-sn data sources

Constraint-based metabolic flux modelling of Fullard 2024 PD
SNpc DA-neuron pseudobulk via iMAT + TIMBR + E-Flux.

## cellxgene_d5d0df8f_fullard

- Role: snRNA-seq input for DA + microglia pseudobulk (Fullard 2024 SNpc slab)
- Primary URL: https://cellxgene.cziscience.com/collections/d5d0df8f-4eee-49d8-a221-a288f50a1590
- Target path: `./datasets/cellxgene_d5d0df8f_fullard/`

## recon3d

- Role: Recon3D human GEM SBML (primary metabolic model)
- Primary URL: https://github.com/SBRG/Recon3D
- Target path: `./datasets/recon3d/`

## human_gem

- Role: Human-GEM SBML (cross-validation metabolic model)
- Primary URL: https://github.com/SysBioChalmers/Human-GEM
- Target path: `./datasets/human_gem/`

## idopaneuro1

- Role: iDopaNeuro1 DA-neuron-context model (Preciat 2021); Zenodo TBD
- Primary URL: https://doi.org/10.1101/2021.06.30.450562
- Note: If Zenodo link unreachable, fall back to Recon3D+iMAT and document in Limitations.
- Target path: `./datasets/idopaneuro1/`

## timbr

- Role: TIMBR Python implementation (Yamamori 2024)
- Primary URL: https://github.com/marinmas/TIMBR
- Target path: `./datasets/timbr/`

## shao_2024_metabolomics

- Role: Shao 2024 npj-PD metabolomics meta-analysis Supp Table 1 (PD-altered metabolites)
- Primary URL: https://www.nature.com/articles/s41531-024-00732-z
- Target path: `./datasets/shao_2024_metabolomics/`

## supandi_2018_cortex

- Role: Supandi 2018 cortical-bulk Lsei-FBA flux predictions (Supp Tables 1-3)
- Primary URL: https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0203687
- Target path: `./datasets/supandi_2018_cortex/`

## hong_2025_pdiron

- Role: Hong 2025 PD-iron supplementary table (iron metabolism markers)
- Primary URL: https://pubmed.ncbi.nlm.nih.gov/?term=Hong+Parkinson+iron+2025
- Note: Exact PMC ID to be confirmed during READ; provisional.
- Target path: `./datasets/hong_2025_pdiron/`

_Manifest written 2026-05-21T17:08:17Z._