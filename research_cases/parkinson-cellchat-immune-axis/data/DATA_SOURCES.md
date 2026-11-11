# parkinson-cellchat-immune-axis data sources

LIANA-py consensus L-R inference on Hong 2025 PBMC vs. Ma 2025
brain SN immune axis; pre-registered direction-specificity control.

## GSE223138

- Role: Peripheral scRNA-seq (Hong 2025 PD PBMC)
- Primary URL: https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE223138
- Target path: `./datasets/GSE223138/`

## ma_2025_SN_Tcell

- Role: Brain snRNA + scTCR + Visium (Ma 2025 PD SN)
- Primary URL: https://www.ncbi.nlm.nih.gov/pmc/articles/PMC12322057/
- Note: Confirm exact GEO / Synapse accession in Data Availability statement. Fallback: drop brain CD8+T to prior using only Ma 2025 Fig. 3 L-R list.
- Target path: `./datasets/ma_2025_SN_Tcell/`

## cellxgene_d5d0df8f_fullard

- Role: Brain microglia substrate (Fullard 2024 SN microglia slab)
- Primary URL: https://cellxgene.cziscience.com/collections/d5d0df8f-4eee-49d8-a221-a288f50a1590
- Target path: `./datasets/cellxgene_d5d0df8f_fullard/`

## cellchatdb_v2

- Role: CellChatDB v2 L-R interaction list (consumed via LIANA-py)
- Primary URL: https://github.com/jinworks/CellChat
- Target path: `./datasets/cellchatdb_v2/`

## liana_py

- Role: LIANA-py consensus L-R inference (CellPhoneDB + CellChat + NATMI + Connectome + scTalk + NicheNet)
- Primary URL: https://github.com/saezlab/liana-py
- Target path: `./datasets/liana_py/`

## hong_2025_supp

- Role: Hong 2025 Supp Table 2 (28 MR-implicated immune genes)
- Primary URL: https://www.nature.com/articles/s41531-025-
- Note: Exact DOI / npj Parkinson's volume to be confirmed during READ; provisional.
- Target path: `./datasets/hong_2025_supp/`

## onek1k_eqtl

- Role: OneK1K reference eQTL (Yazar 2022; PBMC cell-type calibration)
- Primary URL: https://onek1k.org/
- Target path: `./datasets/onek1k_eqtl/`

_Manifest written 2026-05-21T17:08:18Z._