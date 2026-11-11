## Generic guidance (plan)

- Pre-register the primary hypothesis, the decision rule, and the
  stopping rule ("held-out / validation partition touched exactly
  once after lockdown") before writing any code.
- Include at least one adversarial baseline — a baseline the
  primary model should beat trivially if it is doing anything at
  all. A model that beats no baseline has not demonstrated
  anything.
- A paired Δ statistic (primary − baseline) with a 95% CI and
  directional p-value is required whenever a baseline is
  specified. Compute the paired Δ CI by drawing the SAME resample
  indices (bootstrap / jackknife / permutation) for primary and
  baseline; a plain subtraction of point estimates is not
  sufficient.
- Commit to a figure count in the plan. EXECUTE should not be
  improvising figure scope from nothing.
