# parkinson-asyn-propagation data sources

Multi-scale SIR + connectome model of α-synuclein spread across
the Fullard 2024 5-region atlas, validated against Braak rank-
order and Goralski LAMDA cingulate cell-type ranking.

## cellxgene_d5d0df8f_fullard

- Role: 5-region snRNA-seq atlas for per-region SNCA + cell-type fractions (Fullard 2024)
- Primary URL: https://cellxgene.cziscience.com/collections/d5d0df8f-4eee-49d8-a221-a288f50a1590
- Note: Use all 5 regions: DMNX, SNpc, GPi (or cingulate), M1, V1.
- Target path: `./datasets/cellxgene_d5d0df8f_fullard/`

## allen_human_connectivity

- Role: Allen Human Brain Connectivity Atlas projection strengths (connectome matrix)
- Primary URL: https://human.brain-map.org/
- Note: Fetch via allensdk Python client; row-normalise + log-transform projection strengths.
- Target path: `./datasets/allen_human_connectivity/`

## goralski_lamda

- Role: Cingulate spatial Lewy-burden profile for H3 cell-type prediction (Goralski 2024)
- Primary URL: https://zenodo.org/records/10729767
- Target path: `./datasets/goralski_lamda/`

## henderson_2019_mouse

- Role: Henderson 2019 mouse-atlas SIR-connectome parameter prior (H5 prior initialisation)
- Primary URL: https://www.ncbi.nlm.nih.gov/pmc/articles/PMC6372945/
- Target path: `./datasets/henderson_2019_mouse/`

_Manifest written 2026-05-21T17:08:17Z._