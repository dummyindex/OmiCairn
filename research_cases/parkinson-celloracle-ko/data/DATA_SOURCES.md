# parkinson-celloracle-ko data sources

In-silico KO of Kim 2024 top-10 PD-risk genes via CellOracle, 
scored against observed Fullard SNpc DA PD-vs-ctrl shift.

## cellxgene_d5d0df8f_fullard

- Role: snRNA-seq input + observed PD-vs-ctrl DA centroid shift (Fullard 2024 SNpc slab)
- Primary URL: https://cellxgene.cziscience.com/collections/d5d0df8f-4eee-49d8-a221-a288f50a1590
- Target path: `./datasets/cellxgene_d5d0df8f_fullard/`

## GSE193688

- Role: Multiome ATAC fraction for atlas-specific base-GRN (Adams 2024)
- Primary URL: https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE193688
- Target path: `./datasets/GSE193688/`

## celloracle_basegrn

- Role: CellOracle midbrain reference base-GRN (H5 sensitivity baseline)
- Primary URL: https://zenodo.org/records/7755357
- Target path: `./datasets/celloracle_basegrn/`

## GCST90275127_kim

- Role: Kim 2024 multi-ancestry PD GWAS + fine-mapping Supp Table S5
- Primary URL: https://www.ebi.ac.uk/gwas/studies/GCST90275127
- Note: Supp Table S5 lists posterior probabilities of causal association for top genes.
- Target path: `./datasets/GCST90275127_kim/`

## hemedan_2024_boolean

- Role: Hemedan 2024 Boolean PD prodromal-cluster module gene list (H3 enrichment)
- Primary URL: https://gitlab.lcsb.uni.lu/lcsb-biocore/publications/hemedan23-boolean-modelling-of-pd
- Target path: `./datasets/hemedan_2024_boolean/`

_Manifest written 2026-05-21T17:08:17Z._