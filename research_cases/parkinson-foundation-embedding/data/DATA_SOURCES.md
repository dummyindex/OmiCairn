# parkinson-foundation-embedding data sources

Geneformer + scGPT vs. log-CPM-PCA-LR baseline on PD-vs-ctrl
donor-level classification of Fullard SNpc DA nuclei.

## geneformer_95m

- Role: Geneformer 95M-cell weights (V2; primary foundation model)
- Primary URL: https://huggingface.co/ctheodoris/Geneformer
- Note: Use ctheodoris/Geneformer-V2-95M variant; fp16 inference + chunked batches.
- Target path: `./datasets/geneformer_95m/`

## scgpt_human

- Role: scGPT human-pan weights (second foundation model)
- Primary URL: https://github.com/bowang-lab/scGPT
- Target path: `./datasets/scgpt_human/`

## cellxgene_d5d0df8f_fullard

- Role: Primary embedding source (Fullard 2024 SNpc slab)
- Primary URL: https://cellxgene.cziscience.com/collections/d5d0df8f-4eee-49d8-a221-a288f50a1590
- Target path: `./datasets/cellxgene_d5d0df8f_fullard/`

## GSE178265

- Role: Fine-tune label source (Kamath 2022 SNpc)
- Primary URL: https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE178265
- Target path: `./datasets/GSE178265/`

## GSE184950

- Role: Held-out transfer atlas (Wang 2024 SNpc)
- Primary URL: https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE184950
- Target path: `./datasets/GSE184950/`

## GSE193688

- Role: Held-out transfer atlas (Adams 2024 SN; RNA arm)
- Primary URL: https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE193688
- Target path: `./datasets/GSE193688/`

## scEval_benchmark

- Role: log-CPM-PCA-LR baseline harness (Boiarsky 2024) for matched comparison
- Primary URL: https://github.com/raphael-group/scEval
- Target path: `./datasets/scEval_benchmark/`

_Manifest written 2026-05-21T17:08:17Z._