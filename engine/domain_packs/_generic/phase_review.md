## Generic guidance (review)

- Judge the analysis, not prose: does the evidence decide each
  pre-registered hypothesis by its own decision rule?
- `results.json` is authoritative. A number in `analysis.md` that
  disagrees with it is a gap to route back, never to gloss over.
- A well-powered, thorough null is an adequate result. Do not route
  back to chase a positive.
- Every CROSS_CHECK ERROR is a blocking gap; every WARN needs a
  one-line disposition in `review_notes.md`.
- Before asking for more analysis, check `RESOURCE_REQUEST.md` against
  the compute envelope: a retry that cannot run on this host is waste.
- Record every refuted / inconclusive hypothesis and every dropped or
  failed arm in `negative_results.md`, with evidence paths.
