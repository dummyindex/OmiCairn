# alzheimer-bbb-pericyte-vascular data sources

IDEATE direction d2: pericyte SMAD3 / astrocyte VEGFA BBB collapse as an
early AD trigger. Nothing is pre-staged on disk; EXECUTE fetches all
datasets below into the shared cache `./datasets/<accession>/` on the
way. EXECUTE MUST use REAL fetched public data — prefer
openly-downloadable GEO supplementary files; if blocked/missing, report
honestly and fall back, never simulate.

## Datasets to fetch

### GSE157827 (primary, OPEN)

- Role: primary PFC reference (Lau 2020, snRNA-seq, ~170,000 nuclei);
  confirmed to contain pericyte, endothelial, and astrocyte populations
  (n=12 AD + 9 control). This is the dataset in which the gliovascular
  SMAD3/VEGFA axis and HIF1A/VEGFA endothelial angiogenic failure were
  originally reported.
- Region: prefrontal cortex
- Modality: 10x snRNA-seq
- Primary URL: https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE157827
- Target path: `./datasets/GSE157827/`
- NOTE: H1 needs Braak/tau staging; H2 additionally needs an amyloid
  plaque-density (or equivalent Aβ) measure. EXECUTE must confirm these
  columns exist BEFORE testing. If a covariate is missing, report
  honestly and fall back (do NOT impute Braak stage or amyloid measures).

### SEA-AD (optional secondary — spatial, candidate)

- Role: spatial localisation of vascular structures (MERFISH), with
  quantitative neuropathology and Braak metadata (Gabitto 2024).
- Candidate — EXECUTE to verify; report blocked if unavailable, do not
  fabricate.
- Primary URL: https://portal.brain-map.org/explore/seattle-alzheimers-disease
- Alt / companion: Synapse syn64410371 / AWS open data registry
- Target path: `./datasets/seaad_syn64410371/`

## Fetch convention

- All accessions land under `<shared_cache>/<name>/`.
- Each fetched file gets a `SOURCE.txt` sidecar recording URL,
  timestamp, sha256, byte count, and parser tool.
- See `engine/prompts/execute.md` § *On-the-way data fetching*
  for the canonical protocol.

_Manifest authored from IDEATE direction d2._
