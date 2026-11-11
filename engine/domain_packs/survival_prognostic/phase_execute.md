## Survival-prognostic guidance (execute)

- **LASSO-Cox 1-SE rule.** The 1-SE rule picks the MOST regularized
  model (largest alpha / sparsest) whose CV score is within 1 SE of
  the best. Index direction matters: if `alphas` is sorted descending
  (as in scikit-survival's `CoxnetSurvivalAnalysis.alphas_`), the
  most-regularized candidate is the FIRST qualifying index, NOT the
  last. Getting this backwards produces an unregularized model that
  retains all features.

- **Subtype-stratified primary mirror.** If the plan defines a
  secondary hypothesis on a stratum, compute the primary metric
  within that stratum and emit it as `primary_model.<stratum>` with
  its own `ci_95` and a paired ΔCI vs the baseline restricted to the
  stratum.

- **Stratum-trained confirmation model (W7).** Whenever the plan or
  RESEARCH.md defines a secondary hypothesis on a subgroup, fit a
  stratum-trained confirmation model. Procedure:
  (1) restrict DISCOVERY to the stratum;
  (2) fit the same model family as the primary;
  (3) lock coefficients to `locked_model_<stratum>.json` with its own
      SHA-256 hash;
  (4) load VALIDATION, restrict to the stratum, evaluate ONCE.
  Emit `primary_model.stratum_trained_<name>` with `c_index`,
  `ci_95`, `n`, `events`, and a paired ΔC vs the pan-cohort primary
  restricted to the same stratum
  (`comparisons.delta_stratum_trained_<name>_vs_primary_in_stratum`).
  **Stratum gating thresholds (defaults for this pack):**
  `min_stratum_n = 80`, `min_stratum_events = 30`. If either floor is
  violated on the discovery partition, skip the model and set
  `primary_model.stratum_trained_<name>.skipped = true` with a
  `reason` (e.g., `"discovery events=18 < min 30"`) — do NOT emit
  NaN comparisons. The cross-check W7 validator enforces that
  `primary_model.stratum_trained_<name>` exists whenever this pack's
  execute guidance is loaded and the plan has committed to a
  stratum-confirmation analysis.

- **Permutation B ≥ 1,000.** After running permutation null, compute
  `np.std(permuted_c_indices)`. If SD is 0 or all permuted values are
  identical, the implementation is BUGGY (likely shuffling risk
  scores instead of survival labels). A correct permutation of
  survival labels on n≥50 subjects produces SD ≈ 0.02–0.08. If
  degenerate: set permutation results to `null`, log the distribution
  summary, and do NOT report a p_directional.

- **Calibration alongside discrimination.** Compute at least one
  calibration measure (calibration curve, ICI, Brier-at-5y) and emit
  `primary_model.calibration.{ici, brier_at_5y, ...}`. pandas API
  note: use `np.abs(array)` or `series.abs()`, NOT `index.abs()`
  (`Index` has no `.abs()`). Wrap calibration in try/except and log
  the exact error message on failure.

- **Cross-platform batch sensitivity.** When discovery and validation
  cohorts are on different platforms, run rank-normalisation per
  cohort before applying locked coefficients to validation. Emit
  `primary_model.batch_corrected.{c_index, ci_95, n, events,
  method: "rank_norm" | "combat"}` + paired
  `comparisons.delta_batch_corrected_vs_raw`. If platforms are
  identical, set `batch_corrected.skipped = true` with a reason.

- **Family-wide multiple-testing correction.** For univariate Cox
  tables, report BOTH within-set BH p-values AND family-wide
  Bonferroni- and BH-corrected p-values across ALL tests.

- **Proportional-hazards diagnostic.** Whenever a Cox PH model is
  fit, run a Schoenfeld residual test and emit
  `primary_model.ph_assumption.{global_p, per_var, n_violations}`.
  lifelines API has changed across versions — try IN THIS ORDER:
  (1) `cph.check_assumptions(df, p_value_threshold=0.05)` (v0.27+)
  (2) `cph.check_assumptions(df)` (no kwarg)
  (3) `proportional_hazard_test(cph, df)` from
      `lifelines.statistics` (v0.25+)
  Do NOT call `check_proportional_hazard_assumptions()` — removed in
  ≥0.27. If all methods fail, set values to null AND log the error.
  When validation events < 50, caveat in `analysis.md` that the
  Schoenfeld test has near-zero power.

- **Module sanity check.** If the model has group structure
  (correlated genes/features), report each module's univariate
  C-index AND the average sign of its multivariate coefficients. If
  they disagree (module C-index < 0.50 but positive avg coefficient),
  surface as a "multicollinearity sign-redistribution" diagnostic.
