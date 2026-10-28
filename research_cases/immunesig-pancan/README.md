# immunesig-pancan — Blind replication test case

## Purpose

This is a **blind-replication** test case. The goal is not to produce
a novel finding but to see whether OmiCairn can independently reproduce a
known high-impact result from public data alone.

## Target paper

**Thorsson et al. (2018).** "The Immune Landscape of Human Tumors."
*Immunity* 48(4):812-830.e14. DOI: 10.1016/j.immuni.2018.03.023

This paper defined six immune subtypes (C1-C6) across 33 TCGA cancer
types and showed that immune subtype is a strong independent prognostic
factor. Key quantitative results:

- C3 (Inflammatory) has best overall survival across most cancers
- C2 (IFN-gamma dominant) has worst survival in several cancers
- Immune subtype adds prognostic power beyond tumor stage (HR range 1.3-2.8)
- Wound-healing signature (C1) vs. inflammatory (C3) HR ~1.8 pan-cancer

## Why this paper

1. **Public data**: all data from TCGA Pan-Cancer Atlas, freely
   available via cBioPortal and GDC
2. **Clear quantitative results**: survival HRs, Kaplan-Meier
   separations, and immune subtype classifications are precisely reported
3. **High citation count**: >4000 citations, landmark paper in
   tumor immunology
4. **Gene-signature approach**: immune subtypes defined by gene
   expression signatures — directly compatible with OmiCairn's METHOD mode
5. **Multiple validation angles**: can be validated across individual
   cancer types (TCGA-BRCA, TCGA-LUAD, etc.) using leave-one-out
6. **Known positive result**: the effect is real and strong — failure
   to replicate indicates a OmiCairn bug, not a biology problem

## Replication scope

OmiCairn should be able to:
1. Profile immune-related gene expression across a pan-cancer cohort
2. Identify prognostic immune signatures (not necessarily the same
   C1-C6 subtypes, but similar survival stratification)
3. Show that immune gene expression signatures predict survival
   with HR > 1.3 in at least 3 cancer types
4. Produce honest external validation (train on subset of cancers,
   test on held-out cancers)

## Expected outcome

**Positive.** The immune-survival association is one of the most
robust findings in cancer genomics. OmiCairn should be able to detect it
independently. The key question is whether OmiCairn's signature matches
Thorsson et al.'s characterisation or finds a different but equally
valid immune-prognostic axis.

## Data requirements

- TCGA Pan-Cancer Atlas RNA-seq (33 cancer types, ~10,000 samples)
- Clinical/survival data (OS, PFI)
- Gene list: ~400 immune-related genes (from ImmPort, immunedeconv, etc.)

Data will be fetched via `fetch_data.py` from cBioPortal.

## Success criteria

| Metric | Threshold | Thorsson et al. reference |
|--------|-----------|--------------------------|
| Pan-cancer immune signature HR | > 1.3 | ~1.8 (C1 vs C3) |
| Per-cancer-type replication | >= 3 cancers with significant HR | 10+ cancers |
| Honest validation (held-out cancers) | c-index > 0.55 | Not directly reported |
