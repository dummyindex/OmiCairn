# parkinson-prs-portability

Multi-ancestry PD PRS portability benchmark using only public
sumstats + 1000 Genomes individual genotypes — no UKB, no GP2.
Reproduces Saffie-Awad 2025-reported per-ancestry AUCs as
*expected values* via Lee 2012 liability-scale R² translation.

- **Mode:** METHOD (GWAS / PRS methodology).
- **GWAS sumstats:** Kim 2024 multi-ancestry (GCST90275127);
  Nalls 2019 EUR-only (GCST009325) for H4 head-to-head.
- **Individual-genotype substrate:** 1000 Genomes Phase 3 v5
  (~30 GB VCF → ~10 GB Plink2 binary).
- **PRS tool:** LDpred-funct with 1000 Genomes EUR LD reference.
- **Reference resources:** Storm 2025 pQTL Supp Table S2 (H3
  proteinMR overlay); Saffie-Awad 2025 Supp Table 3 (per-ancestry
  AUC benchmark targets).
- **Primary metric:** EUR liability-scale R² within ±0.05 of
  Saffie-Awad 2025 EUR Model 2 AUC of 0.621 (Lee 2012
  translated).
- **Expected outcome:** EUR within ±0.05; non-EUR ancestries
  likely outside the bound due to LD-mismatch + small 1000G
  per-ancestry sample sizes (Wang & Visscher 2024 concern). A
  miss is itself informative as a methodological upper bound.
- **Why this case is in the suite:** the v2 batch's only GWAS /
  PRS case. Tests OmiCairn's ability to chain Plink2 + LDpred-funct
  outside the snRNA-seq comfort zone, use published AUCs as
  expected values (no PD labels in 1000G), and pre-register
  honest CI reporting at small per-ancestry n.
- **Hardest OmiCairn phase:** EXECUTE — LDpred-funct + Plink2
  chaining; full pipeline benchmarks.

**Implementation choice:** Lee 2012 formula translates AUC to
liability-scale R² assuming PD lifetime prevalence 0.3% (EUR
standard). Bootstrap 1000 resamples per ancestry. AJ ancestry has
no 1000G equivalent — documented as limitation, not skipped
silently.

Data fetched on-the-way (~12 GB total).
