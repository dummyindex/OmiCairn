## Survival-prognostic guidance (plan)

### Canonical partitions and baselines

- The training / discovery partition is the DISCOVERY cohort;
  the held-out / validation partition is the EXTERNAL VALIDATION
  cohort. The external cohort is touched exactly once after
  lockdown.
- Paired-Δ statistics in this domain are typically ΔAUC, Δc-index,
  or Δconcordance — draw the SAME bootstrap indices for primary
  and baseline, compute per-resample differences, then percentile
  the delta distribution.
- Field-standard adversarial baselines include the Venet-style
  single-variable baseline and random-feature nulls drawn from
  the same distribution as the candidate signature.

### Canonical subgroups

- Typical stratum definitions for breast / solid-tumour survival
  work include subtype (basal + claudin-low), receptor status
  (ER-negative), and driver-mutation (BRAF-mutant, KRAS-mutant,
  EGFR+) strata. Name the subgroup explicitly in
  `primary_model.<stratum_name>` (e.g.,
  `primary_model.basal_claudin_low`).
- **Stratum-trained confirmation model.** If any secondary
  hypothesis tests a subgroup effect (basal/claudin-low,
  ER-negative, BRAF-mutant, etc.), the plan MUST include an
  exploratory/confirmatory stratum-trained model: fit the same
  model family as the primary (e.g., LASSO-Cox) on the DISCOVERY
  cohort restricted to the stratum, lock it with its own SHA-256
  hash to `locked_model_stratum.json`, then evaluate ONCE on the
  VALIDATION cohort restricted to the same stratum. Emit results
  under `primary_model.stratum_trained_<name>` with `c_index`,
  `ci_95`, `n`, `events`, plus a paired ΔC vs the pan-cohort
  primary restricted to the same stratum. Mark **exploratory**
  when the discovery stratum has fewer than 100 events,
  **confirmatory** when ≥ 100. Power-calc threshold for marking
  a stratum hypothesis exploratory-vs-confirmatory is 1,000
  events for the cohort as a whole. This is the single most
  important analysis to include when the primary model may fail
  cross-cohort validation due to subtype heterogeneity — it
  converts a pure falsification into a mechanistic explanation.

### Calibration and competitor models

- **Calibration alongside discrimination.** When the primary
  metric is concordance or AUC (rank-based), at least one
  calibration measure (calibration curve, ICI, or Brier score)
  must be specified for survival models, especially when the
  discovery and validation cohorts have substantially different
  event rates. Discrimination-only validation is incomplete in
  this field.
- **Competitor models.** If the primary model is LASSO-Cox, the
  plan must include at least one penalty-family competitor
  (elastic-net or ridge) AND one non-penalty alternative
  (e.g., random survival forest, gradient-boosted survival).

### Cross-platform batch sensitivity

- When discovery and validation cohorts come from different
  platforms (RNA-seq → microarray, Affymetrix → Illumina),
  prescribe rank-normalization (per-gene percentile rank within
  cohort) as a sensitivity analysis. Emit
  `primary_model.batch_corrected.*` + paired
  `comparisons.delta_batch_corrected_vs_raw`.
