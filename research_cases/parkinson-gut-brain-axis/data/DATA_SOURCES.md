# parkinson-gut-brain-axis data sources

Cohort-bridging multi-omics integration test. No individual
donor overlap between Wallen 2022 (fecal) and Fullard 2024 (brain).
Tests SIGNATURE-LEVEL alignment via pathway-bridging, not
per-individual mediation.

## wallen_2022_zenodo

- Role: PD gut shotgun metagenomic post-QC + MetaCyc pathway tables
- Primary URL: https://zenodo.org/records/7246185
- Content: Wallen 2022; 490 PD + 234 ctrl; NovaSeq 6000 ~40M reads/sample
- Companion code: https://github.com/zackmwallen/PD_metagenomics_2022
- Alt: SRA PRJNA834801 (raw FASTQ, ~3-5 TB; NOT fetched)
- Target path: `./datasets/wallen_2022_zenodo/`

## cellxgene_d5d0df8f_fullard

- Role: Fullard 2024 multi-region atlas — DMNX slab is primary; SNpc + V1 controls
- Primary URL: https://cellxgene.cziscience.com/collections/d5d0df8f-4eee-49d8-a221-a288f50a1590
- Content: 100 donors (75 PD + 25 ctrl); DMNX ~ 200-400k nuclei
- Companion code: https://github.com/DiseaseNeuroGenomics/AMP-PD_SciData
- Target path: `./datasets/cellxgene_d5d0df8f_fullard/`

## storm_2025_mr_proteins

- Role: Protein-MR causal proteins (H4 triangulation)
- Primary URL: https://github.com/catherinestorm/MR_proteins_neurodegeneration
- Content: Storm 2025 supplementary MR table; UKB Olink × Nalls 2019
- Target path: `./datasets/storm_2025_mr_proteins/`

## PXD046355_hallqvist

- Role: Hällqvist 2024 plasma proteomic 8-panel (H4 triangulation)
- Primary URL: https://www.ebi.ac.uk/pride/archive/projects/PXD046355
- Content: ~250 LC-MS/MS DIA runs across 99 PD + 36 HC + 72 iRBD + 41 disease-ctrl
- Target path: `./datasets/PXD046355_hallqvist/`

## kegg

- Role: KEGG human + bacterial pathway maps for H1 bridging
- Primary URL: https://rest.kegg.jp/
- Target path: `./datasets/kegg/`

## reactome

- Role: Reactome v90 GMT for Fullard DMNX gene-pathway enrichment
- Primary URL: https://reactome.org/download-data
- Target path: `./datasets/reactome/`

_Manifest written 2026-05-21T13:41:39Z._