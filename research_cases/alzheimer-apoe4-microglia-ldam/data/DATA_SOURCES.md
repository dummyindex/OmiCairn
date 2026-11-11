# alzheimer-apoe4-microglia-ldam data sources

IDEATE direction d2: APOE4 DAM→LDAM microglial shift and tau.
Nothing is pre-staged on disk; EXECUTE fetches all datasets below into
the shared cache `./datasets/<accession>/` on the way. EXECUTE MUST use
REAL fetched public data — prefer openly-downloadable GEO supplementary
files; if blocked/missing, report honestly and fall back, never
simulate.

## Datasets to fetch

### GSE174367 (primary, OPEN)

- Role: primary PFC reference (Morabito 2021, snRNA + snATAC multiome).
  Use the snRNA arm for microglial composition.
- Region: prefrontal cortex
- Modality: 10x multiome (snRNA-seq + snATAC-seq)
- Primary URL: https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE174367
- Target path: `./datasets/GSE174367/`
- NOTE: H1 needs APOE genotype; H2 needs tau/Braak (+ ideally Aβ).
  EXECUTE must confirm these covariates exist in the fetched object
  BEFORE testing H1/H2. If a covariate is missing, report honestly and
  fall back (do NOT impute genotype or neuropathology).

### SEA-AD (secondary, OPEN — genotype + neuropathology)

- Role: open multimodal atlas with APOE genotype, snRNA-seq, and
  quantitative neuropathology (Gabitto 2024); strongest source for the
  H1 genotype contrast and H2 tau-vs-amyloid decomposition if GSE174367
  lacks the needed covariates.
- Primary URL: https://portal.brain-map.org/explore/seattle-alzheimers-disease
- Alt / companion: Synapse syn64410371 / AWS open data registry
- Target path: `./datasets/seaad_syn64410371/`

### Gerrits 2021 (optional tertiary, check availability)

- Role: human microglia subtypes (homeostatic / amyloid-DAM / tauopathy
  microglia), Acta Neuropathologica. Check the paper's
  data-availability statement at fetch time; use only if openly
  downloadable.
- Target path: `./datasets/gerrits_2021/`

## Fetch convention

- All accessions land under `<shared_cache>/<name>/`.
- Each fetched file gets a `SOURCE.txt` sidecar recording URL,
  timestamp, sha256, byte count, and parser tool.
- See `engine/prompts/execute.md` § *On-the-way data fetching*
  for the canonical protocol.

_Manifest authored from IDEATE direction d2._
