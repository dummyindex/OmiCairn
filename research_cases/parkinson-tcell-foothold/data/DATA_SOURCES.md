# parkinson-tcell-foothold data sources

Cross-atlas replication of Ma 2025 PD-SN T-cell transcriptomic
signature on 4 atlases that lack paired TCR-seq.

## ma_2025_SN_Tcell

- Role: Signature derivation: clonally expanded CD8+ T cell transcriptomic module (Ma 2025)
- Primary URL: https://pmc.ncbi.nlm.nih.gov/articles/PMC12322057/
- Preprint: https://www.biorxiv.org/content/10.1101/2024.01.08.574736v3
- Content: 44 donors / 265,284 nuclei + Visium + scTCR-seq across PD SN + cingulate
- Note: EXECUTE must read Data Availability of PMC12322057 to confirm GEO/Synapse ID. Falls back to Supplementary Data T-cell gene module if accession unreachable.
- Target path: `./datasets/ma_2025_SN_Tcell/`

## GSE178265

- Role: replication atlas 1 (Kamath 2022 SNpc + caudate)
- Primary URL: https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE178265
- Target path: `./datasets/GSE178265/`

## GSE184950

- Role: replication atlas 2 (Wang 2024 SNpc)
- Primary URL: https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE184950
- Target path: `./datasets/GSE184950/`

## GSE193688

- Role: replication atlas 3 (Adams 2024 SN — RNA arm)
- Primary URL: https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE193688
- Target path: `./datasets/GSE193688/`

## cellxgene_d5d0df8f_fullard

- Role: replication atlas 4 + H2 region-specificity test (Fullard 5-region atlas)
- Primary URL: https://cellxgene.cziscience.com/collections/d5d0df8f-4eee-49d8-a221-a288f50a1590
- Target path: `./datasets/cellxgene_d5d0df8f_fullard/`

## GSE243639

- Role: H4 spatial validation (Martirosyan 2024 Visium portion)
- Primary URL: https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE243639
- Target path: `./datasets/GSE243639/`

## msigdb

- Role: H5 exhaustion-module negative control gene list
- Primary URL: https://www.gsea-msigdb.org/gsea/msigdb/human/
- Target path: `./datasets/msigdb/`

_Manifest written 2026-05-21T13:41:39Z._