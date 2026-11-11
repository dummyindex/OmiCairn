# angiogenesis-crc: Angiogenesis Gene Signature in Colorectal Cancer

## Overview

This test case evaluates whether a 20-gene angiogenesis panel predicts overall
survival in colorectal cancer (CRC), with honest external validation on two
independent cohorts. Tumor angiogenesis is clinically validated in CRC --
anti-VEGF therapy (bevacizumab) is standard-of-care for metastatic CRC -- and
angiogenic gene expression profiles correlate with treatment response and
prognosis.

## Gene Panel (20 genes)

| Category                  | Genes                                    |
|---------------------------|------------------------------------------|
| Core angiogenic factors   | VEGFA, VEGFB, VEGFC, PGF, PDGFB         |
| Receptors                 | FLT1 (VEGFR1), KDR (VEGFR2), FLT4 (VEGFR3) |
| Hypoxia regulators        | HIF1A, EPAS1 (HIF2A)                    |
| Angiopoietin axis         | ANGPT1, ANGPT2, TEK (TIE2)              |
| ECM / remodeling          | MMP14, TIMP1, VCAN, SPP1                |
| Anti-angiogenic / pathway | THBS1, CXCL12, NRP1                     |

## Data

- **Discovery**: TCGA-COADREAD Pan-Cancer Atlas (592 expression, 594 clinical)
- **Validation 1**: Sidra-LUMC AC-ICAM (coad_silu_2022) -- 348 samples, all 20 genes, OS data
- **Validation 2**: CPTAC Colon Cancer (coad_cptac_2019) -- 97 samples, 6 genes at protein level

This is the strongest test case in terms of data: large discovery (n=592),
large external validation (n=348) with all 20 genes, and cross-platform
protein-level secondary validation.

## Why This Test Case

- Bevacizumab (anti-VEGF) is a proven CRC therapy, validating angiogenesis biology
- The Sidra-LUMC cohort provides unusually strong external validation
  (348 samples with all 20 genes and OS data)
- CPTAC provides cross-platform (protein-level) validation
- CMS4 (mesenchymal) CRC subtype is high in angiogenic genes and has worst prognosis
- Good candidate for a **positive result** test case

## Data Provenance

All data fetched from cBioPortal public REST API via `fetch_data.py`.
Source studies:
- Discovery: `coadread_tcga_pan_can_atlas_2018`
- Validation 1: `coad_silu_2022` (Dienstmann et al. Nat Med 2023)
- Validation 2: `coad_cptac_2019` (Vasaikar et al. Cell 2019)

## Running

```bash
conda run -n omicairn python fetch_data.py
```

This will re-fetch all data from cBioPortal and regenerate the data/ directory.
