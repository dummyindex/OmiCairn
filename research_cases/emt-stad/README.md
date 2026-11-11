# emt-stad: EMT Gene Signature in Gastric Adenocarcinoma

## Overview

This test case evaluates whether a 20-gene epithelial-mesenchymal transition
(EMT) panel predicts overall survival in gastric adenocarcinoma (STAD). EMT is
a fundamental biological process driving cancer invasion and metastasis, and
TCGA's genomically stable (GS) subtype of gastric cancer is defined by EMT
gene enrichment and carries the worst prognosis.

## Gene Panel (20 genes)

| Category               | Genes                               |
|------------------------|-------------------------------------|
| EMT transcription factors | SNAI1, SNAI2, TWIST1, ZEB1, ZEB2 |
| Mesenchymal markers    | VIM, CDH2, FN1, ACTA2               |
| EMT effectors / ECM    | SERPINE1, MMP2, MMP9, COL1A1, COL3A1, SPARC |
| Signaling              | TGFB1, TGFB2, WNT5A                 |
| Gastric-specific       | THBS2, PLOD2                         |

## Data

- **Discovery**: TCGA-STAD Pan-Cancer Atlas (412 expression, 440 clinical)
- **Validation**: 70/30 random split (seed=42): 289 discovery / 123 validation

The OncoSG gastric cancer study (stad_oncosg_2018) had only 11 RNA-Seq
expression samples on cBioPortal, insufficient for validation. A held-out
split was used instead.

## Why This Test Case

- EMT is among the most robust biological programs in cancer biology
- Multiple published studies validated EMT signatures in gastric cancer
  with hazard ratios >1.5 (Xu et al. 2021, Zhang et al. 2021, He et al. 2022)
- Good candidate for a **positive result** test case
- Tests OmiCairn on a well-powered single-cohort split design

## Data Provenance

All data fetched from cBioPortal public REST API via `fetch_data.py`.
Source study: `stad_tcga_pan_can_atlas_2018`.

## Running

```bash
conda run -n omicairn python fetch_data.py
```

This will re-fetch all data from cBioPortal and regenerate the data/ directory.
