# parkinson-multimodal-ppmi-trajectory

GPLVM-based 1-D disease-time inference from publicly-summarised
longitudinal PPMI endpoints (CSF α-syn-SAA, Olink, MDS-UPDRS-III,
free-water dMRI, LRRK2-stratified curves) — no individual-level
data, no DUA.

- **Mode:** HYBRID (trajectory inference, methodological).
- **Inputs (6 supplements, all public PMC):**
  - Russo 2025 Lancet Neurology (CSF SAA Vmax/Tmax/AUC)
  - Bayati 2024 Acta Neuropathologica (Olink + CSF + plasma)
  - Hällqvist 2024 Nat Commun (8-protein panel by visit)
  - Burciu 2023 Frontiers Neurology (free-water dMRI)
  - Simuni 2024 Movement Disorders (LRRK2-G2019S SAA-stratified)
  - Marek 2018 + downstream (MDS-UPDRS-III group means)
- **Total data:** ~200 MB. The smallest v2 footprint.
- **Model:** GPLVM (`GPy.models.GPLVM`) with 1-D latent + ARD
  kernel; fallback to `sklearn.FactorAnalysis` if GPy unavailable.
- **Primary metric:** Spearman ρ ≥ 0.6 between inferred latent
  disease-time and published time-from-diagnosis over n ≥ 50
  visit-aggregated points.
- **Expected outcome:** uncertain. Summary tables encode less than
  individual-level data — the question is *how much less*. A null
  H1 (ρ < 0.4) is itself an informative upper bound on summary-
  table-only PD trajectory work.
- **Why this case is in the suite:** the v2 batch's only
  longitudinal-trajectory case AND the only one that stays
  inside summary-table territory. Tests OmiCairn's ability to
  ingest scientific PDFs / XLSX as primary data, handle a
  tiny heterogeneous dataset, and pre-register a quantitative
  upper bound on a research modality's information content.
- **Hardest OmiCairn phase:** PLAN / READ — sparse heterogeneous data
  forces precise table-by-table sourcing in the steering plan.

**Implementation choice:** manual TSV conversion of 6
supplementary tables before EXECUTE starts (cache in
`data/manual/`). Image-only PDFs are flagged in H5 audit, not
converted by hand.

Data fetched on-the-way (~200 MB total).
