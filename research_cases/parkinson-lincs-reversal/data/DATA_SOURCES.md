# parkinson-lincs-reversal data sources

LINCS L1000 connectivity-reversal scoring of Fullard SNpc-DA
PD-vs-ctrl transcriptomic signature.

## lincs_l1000

- Role: LINCS L1000 Phase II Level 5 signatures (HDF5; ~5 GB combined)
- Primary URL: https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE92742
- Alt: https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE70138
- Note: Fetch both GSE92742 (touchstone) and GSE70138 (expansion); concat with cmapPy.
- Target path: `./datasets/lincs_l1000/`

## clue_metadata

- Role: LINCS chemical metadata (MoA, ATC class, ChEMBL ID)
- Primary URL: https://clue.io/api
- Target path: `./datasets/clue_metadata/`

## cellxgene_d5d0df8f_fullard

- Role: PD-vs-ctrl signature source (Fullard 2024 SNpc + microglia pseudobulk)
- Primary URL: https://cellxgene.cziscience.com/collections/d5d0df8f-4eee-49d8-a221-a288f50a1590
- Target path: `./datasets/cellxgene_d5d0df8f_fullard/`

## opentargets_2406

- Role: Open Targets release 24.06 (PD MONDO:0005180 target scores; ~1 GB JSON)
- Primary URL: https://platform.opentargets.org/downloads
- Target path: `./datasets/opentargets_2406/`

## tang_2024_omicsynth

- Role: omicSynth Tang 2024 PD druggable target list
- Primary URL: https://www.cell.com/iscience/fulltext/S2589-0042(24)00018-1
- Target path: `./datasets/tang_2024_omicsynth/`

## bayati_2024_supp

- Role: Bayati 2024 PD-DDC biomarker supplementary
- Primary URL: https://www.ncbi.nlm.nih.gov/pmc/articles/PMC10927779/
- Target path: `./datasets/bayati_2024_supp/`

## storm_2025_pqtl

- Role: Storm 2025 protein-MR pQTL-instrumented PD-target list
- Primary URL: https://academic.oup.com/brain/article-abstract/148/8/2412/
- Target path: `./datasets/storm_2025_pqtl/`

_Manifest written 2026-05-21T17:08:17Z._