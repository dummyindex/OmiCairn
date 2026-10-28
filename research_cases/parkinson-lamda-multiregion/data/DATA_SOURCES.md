# parkinson-lamda-multiregion data sources

Goralski 2024 LAMDA signature → Fullard 2024 multi-region atlas.
Nothing pre-staged; EXECUTE fetches everything on the way.

## zenodo_10729767_goralski

- Role: Signature derivation (LAMDA gene module + GeoMx/CosMx counts)
- Content: Goralski 2024 cingulate cortex spatial transcriptomic atlas; ~9,941 CosMx cells; 8 PD/PDD/DLB donors + PFF mouse
- Primary URL: https://zenodo.org/records/10729767
- Companion code: https://zenodo.org/records/10732492 (notebook)
- Target path: `./datasets/zenodo_10729767_goralski/`

## cellxgene_d5d0df8f_fullard

- Role: Test substrate (Fullard 2024 multi-region atlas; 5 regions: DMNX/GPi/SNpc/M1/V1)
- Content: 100 donors (75 PD + 25 ctrl); 2,096,155 nuclei
- Primary URL: https://cellxgene.cziscience.com/collections/d5d0df8f-4eee-49d8-a221-a288f50a1590
- Companion code: https://github.com/DiseaseNeuroGenomics/AMP-PD_SciData
- Target path: `./datasets/cellxgene_d5d0df8f_fullard/`

## allen_brain_ref

- Role: Cross-region cell-type harmonisation reference
- Content: Hodge 2019 + Bakken 2021 human cortex + Linnarsson 2023 subcortex marker panels
- Primary URL: https://celltypes.brain-map.org/api/v2/well_known_file_download/
- Target path: `./datasets/allen_brain_ref/`

## msigdb

- Role: Functional sub-program annotation (Reactome + Hallmark)
- Content: MSigDB v2023.2.Hs
- Primary URL: https://www.gsea-msigdb.org/gsea/msigdb/human/
- Target path: `./datasets/msigdb/`

_Manifest written 2026-05-21T13:41:39Z._