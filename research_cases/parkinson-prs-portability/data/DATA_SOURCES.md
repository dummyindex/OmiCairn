# parkinson-prs-portability data sources

Multi-ancestry PD PRS portability via 1000 Genomes + public
sumstats. No UKB, no GP2 raw access.

## GCST90275127_kim

- Role: Kim 2024 multi-ancestry PD GWAS sumstats (primary)
- Primary URL: https://www.ebi.ac.uk/gwas/studies/GCST90275127
- Target path: `./datasets/GCST90275127_kim/`

## GCST009325_nalls

- Role: Nalls 2019 EUR-only PD GWAS sumstats (H4 head-to-head)
- Primary URL: https://www.ebi.ac.uk/gwas/studies/GCST009325
- Target path: `./datasets/GCST009325_nalls/`

## 1000_genomes_phase3_v5

- Role: Individual-genotype substrate (2,504 individuals across 26 sub-populations)
- Primary URL: https://www.internationalgenome.org/data-portal/data-collection/phase-3
- Note: VCF → Plink2 binary; QC: MAF>0.01, HWE>1e-6, missingness<0.02.
- Target path: `./datasets/1000_genomes_phase3_v5/`

## ldpred_funct

- Role: LDpred-funct PRS construction tool + LD reference matrices
- Primary URL: https://github.com/carlaml/LDpred-funct
- Target path: `./datasets/ldpred_funct/`

## storm_2025_pqtl

- Role: Storm 2025 pQTL-instrumented PD-target list (H3 proteinMR overlay)
- Primary URL: https://academic.oup.com/brain/article-abstract/148/8/2412/
- Target path: `./datasets/storm_2025_pqtl/`

## saffie_awad_2025_supp

- Role: Saffie-Awad 2025 per-ancestry AUC benchmarks (Supp Table 3)
- Primary URL: https://www.nature.com/articles/s41531-025-
- Note: Exact DOI / npj Parkinson's volume to be confirmed during READ; provisional.
- Target path: `./datasets/saffie_awad_2025_supp/`

_Manifest written 2026-05-21T17:08:18Z._