---
research_mode: METHOD
domain: pd_gwas_prs_portability
topic_slug: parkinson-prs-portability

research_question: |
  When a polygenic risk score (PRS) is built from the Kim 2024
  multi-ancestry PD GWAS (GCST90275127) via LDpred-funct on a
  1000-Genomes LD reference, does its expected liability-scale R²
  in 1000 Genomes individuals stratified by Saffie-Awad-2025-
  style super-population ancestry-clusters (EUR, EAS, AMR, AFR,
  AJ) match the Saffie-Awad-2025-reported AUC values within ±0.05
  per ancestry — purely using public sumstats + 1000 Genomes
  individual genotypes (no UK Biobank, no GP2 individual access)?

  Saffie-Awad 2025 trained 105 PRSs across 7 ancestries on GP2
  R9 individual data and reported AUC by ancestry (EUR 0.621,
  EAS 0.578, AAC 0.591, AJ 0.67, AFR 0.583, AMR 0.639, CAS 0.612).
  Reproducing those AUCs requires GP2 access. This proposal asks
  the methodological question: how close can a *summary-statistics-
  only + 1000-Genomes-only* re-implementation get? The answer is
  itself an informative methodological contribution about what
  portability benchmarks are possible without IRB-gated cohort
  access.

  Hypotheses (pre-registered):

    H1 (primary — EUR portability matches Saffie-Awad):
        PRS liability-scale R² in 1000 Genomes EUR individuals
        (CEU+GBR+FIN+IBS+TSI, n ≈ 503) is within ±0.05 of the
        liability-R² corresponding to Saffie-Awad 2025's
        Model 2 EUR AUC of 0.621 (translated via Lee 2012
        formula given EUR PD prevalence 0.3%).

    H2 (secondary — ancestry-decay ratio):
        Ratio of AUC(EAS) / AUC(EUR) is within ±0.1 of the
        Saffie-Awad-reported 0.578 / 0.621 = 0.93. (Ancestry-
        decay must reproduce roughly, not exactly.)

    H3 (secondary — protein-MR PRS overlay):
        Adding the Storm 2025 protein-MR pQTL-instrumented gene-
        set as a fixed-effect overlay (PROteinMR-PRS) increases
        EUR liability-scale R² by ΔR² ≥ 0.01 vs. SNP-only PRS.
        Methodological test of whether protein-MR adds
        incremental information.

    H4 (secondary — multi-ancestry GWAS does not hurt EUR):
        The Nalls 2019 EUR-only PRS (GCST009325) does NOT
        significantly outperform the Kim 2024 multi-ancestry
        PRS in the EUR 1000 Genomes stratum (DeLong p > 0.1).
        Tests the field concern that multi-ancestry GWAS hurts
        EUR-stratum performance.

    H5 (secondary — bootstrap CI honesty):
        Per-ancestry bootstrap 95% CI is reported alongside point
        estimates. For ancestries with n < 200 in 1000 Genomes
        (e.g., AMR sub-populations), CI width > 0.15 is the
        expected outcome; this must be reported in `analysis.md` and
        `negative_results.md`, not buried.

  Null payoff. If LD-mismatch and small 1000 Genomes per-ancestry
  sample sizes inflate error bars beyond ±0.05 (H1), the
  Wang & Visscher 2024 concern about summary-only PRS portability
  benchmarking is confirmed, and the run would put a quantitative
  number on it. Either outcome is informative.

data:
  location: ./research_cases/parkinson-prs-portability/data
  shared_cache: ./datasets
  description: |
    **No pre-staged raw data on disk.** Fetch into
    `./datasets/<accession>/`. Total ~12 GB.

    **GWAS sumstats (sub-1 GB combined):**

      - **Kim 2024 multi-ancestry PD sumstats** GWAS Catalog
        GCST90275127.
          https://www.ebi.ac.uk/gwas/studies/GCST90275127
        Target path: ./datasets/GCST90275127_kim/

      - **Nalls 2019 EUR-only PD sumstats** GWAS Catalog
        GCST009325.
          https://www.ebi.ac.uk/gwas/studies/GCST009325
        Target path: ./datasets/GCST009325_nalls/

    **Individual-genotype substrate:**

      - **1000 Genomes Phase 3 v5 genotypes** (IGSR public;
        ~30 GB VCF → ~10 GB Plink2 binary).
          https://www.internationalgenome.org/
        Target path: ./datasets/1000_genomes_phase3_v5/

    **PRS tooling:**

      - **LDpred-funct** + LD reference matrices (public).
          https://github.com/carlaml/LDpred-funct
        Target path: ./datasets/ldpred_funct/

    **Cross-resource references:**

      - **Storm 2025 protein-MR supplementary** (Brain 148:2412,
        Supp Table S2 lists pQTL-instrumented genes).
        Target path: ./datasets/storm_2025_pqtl/

      - **Saffie-Awad 2025 supplementary** (npj Parkinson's 11;
        Supp Table 3 lists per-ancestry AUC benchmarks).
        Target path: ./datasets/saffie_awad_2025_supp/

  cohorts:
    [1000_genomes_phase3_5_super_populations,
     saffie_awad_2025_published_AUCs]
  modalities: [gwas_summary_statistics,
               1000_genomes_individual_genotypes,
               pqtl_supplementary_tables]

success_criteria:
  primary_metric: prs_liability_R2_per_ancestry_vs_saffie_awad
  baseline: random_snp_set_prs
  pre_registration: required
  honest_validation: required
  notes: |
    Primary success = EUR liability-R² within ±0.05 of
    Saffie-Awad 2025 Model 2 EUR AUC (translated via Lee 2012).

    Honest-failure: deviation > ±0.05 = LD-mismatch + 1000G
    small-n inflates error bars beyond reproducibility; that is
    the informative upper bound.

    Ancestry-decay ratio (H2) within ±0.1 — relaxed bound
    because per-ancestry n is small in 1000G.

    Bootstrap CI honesty (H5): per-ancestry CI > 0.15 expected
    for n<200 sub-populations; report it up front in `analysis.md`.

figures_planned:
  - id: F1
    title: "1000 Genomes individual-genotype QC + ancestry-cluster assignment"
    panels:
      - "Per-super-population sample size bar (CEU/GBR/FIN/IBS/TSI/CHB/CHS/JPT/YRI/etc.)"
      - "Saffie-Awad-style ancestry-cluster mapping: which 1000G sub-populations → which Saffie-Awad cluster"
      - "MAF distribution per ancestry"
      - "QC pipeline summary (MAF > 0.01, HWE > 1e-6, missingness < 0.02)"
  - id: F2
    title: "LDpred-funct PRS construction + per-ancestry scoring (H1)"
    panels:
      - "LDpred-funct posterior beta distribution across SNPs"
      - "Per-ancestry liability-R² point estimate + bootstrap CI"
      - "Saffie-Awad benchmark overlay per ancestry"
      - "Per-ancestry deviation: our R² - (Saffie-Awad Lee-2012-translated R²)"
  - id: F3
    title: "Ancestry-decay ratios (H2) + Nalls vs. Kim head-to-head (H4)"
    panels:
      - "AUC(non-EUR) / AUC(EUR) ratio per ancestry (our estimate vs. Saffie-Awad)"
      - "Multi-ancestry Kim 2024 vs. EUR-only Nalls 2019 PRS comparison in EUR stratum"
      - "DeLong test p-value annotation"
      - "Effect-size shrinkage: per-SNP comparison Kim vs. Nalls"
  - id: F4
    title: "Protein-MR PRS overlay (H3)"
    panels:
      - "Storm 2025 pQTL-instrumented gene list overlay onto Kim 2024 sumstats"
      - "PROteinMR-PRS construction schematic"
      - "ΔR² (SNP-only vs. SNP+proteinMR) in EUR stratum"
      - "Per-protein contribution to ΔR²"
  - id: F5
    title: "Honest CI panel (H5) + methodological-bound statement"
    panels:
      - "Per-ancestry bootstrap CI width vs. n (sample size)"
      - "If CI > 0.15 for AMR / small populations: explicit annotation"
      - "Simulated power curve: minimum n required for ±0.05 R² CI"
      - "Statement panel: which ancestries can be benchmarked summary-only, which need raw access"

figures_not_in_scope:
  - "UK Biobank individual-level analysis"
  - "GP2 individual-level analysis"
  - "Wet-lab variant validation"
  - "Functional annotation beyond LDpred-funct's built-in priors"

budgets:
  max_phase_wall_minutes: 240
  max_total_wall_minutes: 1440
  allow_web: true
  claude_model: claude-sonnet-4-6
  max_phase_budget_usd: 25.0

auto_approve: true
---

# parkinson-prs-portability — Multi-ancestry PD PRS portability via 1000 Genomes (no UKB, no GP2)

## Source materials

- Kim, J.J., Bandres-Ciga, S., Reynolds, R.H., et al. *Multi-
  ancestry genome-wide meta-analysis in Parkinson's disease.* Nat
  Genet 56:27 (2024). GWAS Catalog GCST90275127.
- Nalls, M.A., Blauwendraat, C., Vallerga, C.L., et al.
  *Identification of novel risk loci, causal insights, and heritable
  risk for Parkinson's disease.* Lancet Neurol 18:1091 (2019).
  GWAS Catalog GCST009325.
- Saffie-Awad, P., Solle, J., Daida, K., et al. *Insights into
  ancestral diversity in Parkinson's disease risk.* npj
  Parkinson's Disease 11 (2025).
- Storm, C.S., Kia, D.A., Almramhi, M.M., et al. *Genome-wide
  determination of drug targets for Parkinson's disease via
  Mendelian-randomisation.* Brain 148:2412 (2025).
- 1000 Genomes Project Consortium. Phase 3 reference panel.
- Pasaniuc, B., Price, A.L. *Dissecting the genetics of complex
  traits using summary association statistics.* Nat Rev Genet
  18:117 (2017). LDpred-funct.
- Lee, S.H., Goddard, M.E., Wray, N.R., Visscher, P.M. *A better
  coefficient of determination for genetic profile analysis.*
  Genet Epidemiol 36:214 (2012). Liability-scale R² formula.

## Why this matters for OmiCairn

This is the v2 batch's only GWAS / PRS case, and the only one
that explicitly skirts IRB-gated cohort access. It exercises
OmiCairn's ability to:

1. **Chain Plink2 + LDpred-funct in a real GWAS workflow** —
   outside the snRNA / GEM comfort zone.
2. **Use published AUC benchmarks as the *expected* value** rather
   than computing AUC on the 1000G individuals directly (which
   have no PD labels).
3. **Pre-register honest CI reporting** (H5) — small-n
   ancestries will have wide CIs, and `analysis.md` must say so.
4. **Test the "multi-ancestry hurts EUR" field concern** (H4)
   cleanly.

## Known prior results (cross-check)

| Claim | Reference |
|---|---|
| Kim 2024: 49,049 PD cases × 4 ancestries multi-ancestry GWAS | Kim 2024 |
| Saffie-Awad 2025: EUR 0.621, EAS 0.578, AJ 0.67, AFR 0.583, AMR 0.639, CAS 0.612 AUCs | Saffie-Awad 2025 Supp Table 3 |
| 1000G Phase 3: 2,504 individuals across 26 sub-populations | 1000G consortium |
| LDpred-funct outperforms LDpred1 on functional-annotation tasks | Pasaniuc & Price 2017 |
| Wang & Visscher 2024: summary-only PRS portability suffers LD-inflation | Wang & Visscher 2024 |

## Out of scope

- UK Biobank individual-level analysis.
- GP2 individual-level access.
- Wet-lab variant validation.
- Functional fine-mapping beyond LDpred-funct's priors.
- AJ ancestry (no 1000G equivalent; documented as limitation).

## Implementation choices

- 1000 Genomes Phase 3 v5 with standard QC: MAF > 0.01,
  HWE > 1e-6, missingness < 0.02.
- Super-population mapping to Saffie-Awad ancestry clusters:
  EUR ← CEU, GBR, FIN, IBS, TSI; EAS ← CHB, CHS, JPT;
  AFR ← YRI, LWK, ESN, GWD, MSL; AMR ← MXL, PUR, CLM, PEL;
  SAS proxies CAS. AJ has no 1000G equivalent — limitation
  documented.
- Liability-scale R² via Lee 2012 formula with EUR PD prevalence
  0.3% (standard PD lifetime risk).
- Bootstrap CI = 1000 resamples within each ancestry stratum.
- DeLong test for H4 head-to-head comparison.
