# ddr-blca: DNA Damage Repair Gene Signature in Bladder Cancer

## Overview

This test case evaluates whether a 20-gene DNA damage repair (DDR) panel
predicts overall survival in muscle-invasive bladder cancer (BLCA), with
honest external validation on two independent cohorts including the
landmark IMvigor210 immunotherapy trial. DDR pathway deficiency shapes
tumor immunogenicity, platinum sensitivity, and immune checkpoint
inhibitor response in bladder cancer.

## Gene Panel (20 genes)

| Category                    | Genes                              |
|-----------------------------|-------------------------------------|
| Homologous recombination    | BRCA1, BRCA2, ATM, ATR, RAD51      |
| Mismatch repair             | MLH1, MSH2, MSH6, PMS2             |
| Nucleotide excision repair  | ERCC1, ERCC2, XPA                   |
| Base excision repair        | XRCC1, PARP1, POLB                  |
| Fanconi anemia              | FANCD2, FANCA                       |
| Checkpoint / signaling      | CHEK1, CHEK2, TP53                  |

## Data

- **Discovery**: TCGA-BLCA Pan-Cancer Atlas (407 expression, 411 clinical)
- **Validation 1**: IMvigor210 atezolizumab trial (blca_iatlas_imvigor210_2017) -- 347 samples, all 20 genes, OS data
- **Validation 2**: BCAN/HCRN urothelial carcinoma (blca_bcan_hcrn_2022) -- 169 samples, all 20 genes

This test case has the best external validation among all test cases:
two independent cohorts totaling 516 samples, both with all 20 genes
available.

## Why This Test Case

- Bladder cancer has one of the highest mutational burdens, reflecting DDR deficiency
- ERCC2 mutations predict cisplatin response (Van Allen et al. 2014)
- DDR mutations predict immunotherapy benefit (Teo et al. 2019)
- IMvigor210 is a landmark immunotherapy trial providing treatment-specific context
- Tests OmiCairn on cross-context validation (mixed treatment vs immunotherapy-only)
- Good candidate for a **positive result** test case

## Data Provenance

All data fetched from cBioPortal public REST API via `fetch_data.py`.
Source studies:
- Discovery: `blca_tcga_pan_can_atlas_2018`
- Validation 1: `blca_iatlas_imvigor210_2017` (Mariathasan et al. Nature 2018)
- Validation 2: `blca_bcan_hcrn_2022` (Clinton et al. Nat Commun 2022)

## Running

```bash
conda run -n omicairn python fetch_data.py
```

This will re-fetch all data from cBioPortal and regenerate the data/ directory.
