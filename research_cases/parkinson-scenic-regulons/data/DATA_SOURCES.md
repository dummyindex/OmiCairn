# parkinson-scenic-regulons data sources

Cross-atlas replication of pySCENIC TF-regulons inferred from
Fullard 2024 SNpc DA neurons; zero-shot AUCell scoring on
Smajic / Wang / Adams held-out atlases.

## cellxgene_d5d0df8f_fullard

- Role: regulon source (Fullard 2024 multi-region atlas; use SNpc slab)
- Primary URL: https://cellxgene.cziscience.com/collections/d5d0df8f-4eee-49d8-a221-a288f50a1590
- Donors: 100 (75 PD + 25 ctrl)
- Target path: `./datasets/cellxgene_d5d0df8f_fullard/`

## GSE157783

- Role: replication atlas 1 (Smajic 2022 SNpc snRNA-seq)
- Primary URL: https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE157783
- Target path: `./datasets/GSE157783/`

## GSE184950

- Role: replication atlas 2 (Wang 2024 SNpc)
- Primary URL: https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE184950
- Target path: `./datasets/GSE184950/`

## GSE193688

- Role: replication atlas 3 (Adams 2024 SN — RNA arm)
- Primary URL: https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE193688
- Target path: `./datasets/GSE193688/`

## scenic_resources

- Role: pySCENIC motif + cisTarget databases (human v10)
- Primary URL: https://resources.aertslab.org/cistarget/
- Note: Fetch hg38__refseq-r80__500bp_up_and_100bp_down_tss.mc9nr.feather + hg38__refseq-r80__10kb_up_and_down_tss.mc9nr.feather + motifs-v10nr_clust-nr.hgnc-m0.001-o0.0.tbl
- Target path: `./datasets/scenic_resources/`

## GCST90275127_kim

- Role: Kim 2024 PD multi-ancestry GWAS sumstats + fine-mapping table
- Primary URL: https://www.ebi.ac.uk/gwas/studies/GCST90275127
- Target path: `./datasets/GCST90275127_kim/`

_Manifest written 2026-05-21T17:08:16Z._