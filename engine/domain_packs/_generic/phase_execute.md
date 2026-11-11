## Generic guidance (execute)

- Never simulate data. Real data or honest failure.
- Never peek at the held-out / validation partition before
  lockdown. One shot.
- Every number in `analysis.md` must be copied directly from
  `results.json`. Do not re-bootstrap, re-permute, or re-fit
  anything while writing the analysis.
- Report effect sizes AND 95% CIs AND p-values together; a point
  estimate without an uncertainty interval is incomplete.
- Print metric lines as `metric_name: value` at the end of
  `main.py` so they are grep-able in `run.log`.
- Permutation / bootstrap p-values below the resolution floor are
  reported as `p<1/B`, not `p=1/B`.
