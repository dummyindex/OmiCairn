# OmiCairn analysis-rigor rules

These are standing rules for every analysis OmiCairn runs. They are not
specific to one phase or one run — they encode what expert-grade rigor
means for a biomedical computational study or for an interp / fairness /
world-model / safety study. PLAN
and EXECUTE both load this file; REVIEW scrutinises against it. They are
strong expectations, not (with the noted exceptions) hard contract
failures: the cross-check guard for subsampling is warning-level by
design, and the depth targets are advisory.

## R1 — Subsampling is for testing only, never for the final result

Subsampling / downsampling the data (e.g. `sc.pp.subsample`,
`df.sample(n=...)`, `df.sample(frac=...)`, "subset to N cells", "randomly
selected N rows") is permitted ONLY for:

- smoke tests,
- dev / quick-iteration loops,
- small-model / sanity checks before scaling up.

The FINAL analysis a run reports MUST run on the FULL dataset.

- **For method BENCHMARKING this is absolute.** Subsampling biases method
  comparisons — a method that is cheap on a subsample may lose on full
  data, and vice versa. Benchmark numbers on a subsample are invalid and
  may NOT be reported as the comparison result.
- If full-data is computationally infeasible within the wall/CPU budget,
  you must NOT silently subsample to fit. Instead:
  1. pick a more efficient method/implementation (chunking, sparse ops,
     a faster estimator, GPU-free approximations with stated error
     bounds), OR
  2. emit a **resource request** (see R3) and proceed with the best
     honest partial you can, flagging the limitation.
- A subsampled result may NEVER be presented as the final / benchmark
  result without **explicit, prominent disclosure** in `analysis.md`
  (results and limitations) and `results.json` (state the subsample N,
  the full N, and why).

A cross-check guard scans the EXECUTE stage for subsampling signals and
warns REVIEW to verify the headline/benchmark numbers ran on full data.
The guard is soft (warning-only); honesty is on you.

## R2 — Breadth AND depth are both expected

For a biomedical computational study, thoroughness has two axes and you
are expected to deliver both:

- **Breadth (multi-dataset / validation).** Use ALL datasets named in
  RESEARCH.md, not just the primary one. Where >=2 relevant cohorts
  exist, replicate the primary finding cross-cohort / on the independent
  dataset. A single-cohort result with sibling cohorts left untouched
  reads as cherry-picking to an expert reader.
- **Depth (orthogonal analyses).** Support each major claim with
  orthogonal / complementary methods, not a single test. Add sensitivity
  and robustness analyses (alternate preprocessing, alternate model
  family, leave-one-out, parameter sweeps) so the result is shown not to
  hinge on one arbitrary choice.

These are strong expectations surfaced to REVIEW, not hard contract
failures. The OPTIONAL `depth:` block in `omicairn_config.yaml`
(`min_datasets`, `min_independent_analyses`, `require_cross_cohort`,
`min_figures`) sets advisory targets per case.

## R3 — Estimate runtime/memory first; request resources, don't degrade

Before launching heavy compute, ESTIMATE the analysis's wall time and
peak memory (rough is fine — based on n, feature count, #bootstraps,
model cost). If it will not finish honestly within the budget:

- Do NOT degrade the science to fit (no silent subsampling, no skipping
  the held-out evaluation, no dropping a planned benchmark).
- Either (a) choose a more efficient strategy, OR (b) emit an explicit,
  prominent **resource request**: write `RESOURCE_REQUEST.md` to the
  EXECUTE stage directory and note it in `summary.md` / `analysis.md`,
  asking the user for more wall time / CPU / a GPU machine, and proceed
  with the best honest partial you can while flagging the gap.

`RESOURCE_REQUEST.md` should name: the analysis that needs more
resources, the estimate (e.g. "~9h on 8 CPUs / needs ~64 GB / needs a
GPU"), what you ran instead this iteration, and exactly what you need to
finish it properly.

Note on hardware: CPU-heavy work runs on the local machine; **a GPU is
NOT available locally and must be requested** via `RESOURCE_REQUEST.md`.
Tie this to R1: do not subsample the final analysis just to beat the
clock — request resources instead.

## R4 — Neutrality: report the effect the data shows

Report the effect the data shows, positive or null, with identical
rigor. Neither outcome is preferable; do not under-state a real effect
any more than you would over-state one. A clean, adequately-powered null
is a valid scientific finding and must be reported plainly; a real effect
must not be hedged into invisibility. (All anti-fabrication rules still
apply: every number traces to `results.json`; never invent data.)

## R5 — Do NOT serialize our own artifacts as Python pickle

Pickle is an inconsistent, fragile, unsafe serialization format: it is
version-brittle across library/Python versions, executes arbitrary code
on load, and carries no stable schema. It is sometimes used by
well-known scientific work (e.g. **celltypist**) authored by researchers
who lack a deep ML/CS understanding of pickle's problems — which is
common; the format's popularity is not evidence it is safe.

- **PERMITTED (input we're forced to accept).** Loading / consuming a
  pickle ONLY when REPRODUCING someone else's published result that
  ships its artifacts as pickle (e.g. running celltypist to reproduce
  their numbers). Pickle is acceptable as an INPUT from external work.
- **FORBIDDEN (our own outputs).** Serializing OUR OWN results, models,
  or intermediate artifacts as pickle (`pickle.dump`, `joblib.dump`,
  `np.save(..., allow_pickle=True)`, `df.to_pickle`, `*.pkl`). For our
  own outputs use durable, inspectable, schema-bearing formats: JSON for
  structured results (the existing `results.json` convention — Golden
  Principle 8), CSV/Parquet for tabular, npz/safetensors for
  arrays/tensors/weights.
- **ESCAPE HATCH.** The forbidden-for-our-own rule may be revisited
  IF/WHEN pickle is proven fixed and adopted as safe by the ML community
  — until then, do not use it for our artifacts.

REVIEW / code-reviewer scrutinise our own `code/*.py` for pickle writes;
consuming an external pickle to reproduce published numbers is fine and
should not be flagged.
