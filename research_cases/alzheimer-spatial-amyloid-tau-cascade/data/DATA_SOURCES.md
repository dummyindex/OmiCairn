# alzheimer-spatial-amyloid-tau-cascade data sources

IDEATE direction d6: multi-region spatial transcriptomics of the
amyloid→tau propagation cascade ("pre-tau watershed").
Nothing is pre-staged on disk; EXECUTE fetches all datasets below into
the shared cache `./datasets/<accession>/` on the way. EXECUTE MUST use
REAL fetched public data — prefer openly-downloadable supplementary
files; if blocked/application-gated/missing, report honestly and fall
back, never simulate spatial spots or pathology annotations.

## Datasets to fetch

### SEA-AD spatial transcriptomics (primary; candidate — ACCESS UNCERTAIN)

- Role: enabling multi-region spatial resource for the pre-tau-watershed
  / DAM-halo analysis (Gabitto 2024).
- candidate — EXECUTE to verify open availability; report blocked if
  unavailable, do not fabricate.
- ACCESS CAVEAT: IDEATE flagged SEA-AD spatial as partly OPEN but
  PORTAL-GATED, with uncertain coverage of the full EC–hippocampus–PFC
  axis. EXECUTE MUST confirm (a) the spatial data is openly downloadable
  (NOT application-gated) and (b) which regions are covered, BEFORE any
  spatial analysis. If gated / unavailable / insufficient region
  coverage → report BLOCKED or fall back to the snRNA-seq cohorts below.
  Do NOT assume access; do NOT fabricate spots, coordinates, or
  pathology annotations.
- Primary URL: https://portal.brain-map.org/explore/seattle-alzheimers-disease
- Alt / companion: Synapse syn64410371 / AWS open data registry
- Target path: `./datasets/seaad_spatial/`

### GSE147528 (open snRNA-seq fallback — regional-transcription part)

- Role: EC + SFG snRNA-seq across Braak stages (Leng 2021). Use for the
  region/stage component of H1 (DAM-marker elevation by region and
  pathology stage) and the H2 intermediate-reactive astrocyte signature.
- Region: entorhinal cortex + superior frontal gyrus
- Modality: 10x snRNA-seq
- Primary URL: https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE147528
- Target path: `./datasets/GSE147528/`

### GSE138852 (open snRNA-seq fallback — EC astrocytes / signature reference)

- Role: EC snRNA-seq (Grubman 2019). Second open EC cohort for the
  Braak-0 EC astrocyte separability test (H2) and a cell-type signature
  reference for spatial deconvolution if spatial data is available.
- Region: entorhinal cortex
- Modality: 10x snRNA-seq
- Primary URL: https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE138852
- Target path: `./datasets/GSE138852/`

### Chen 2023 Visium PFC (optional supporting spatial; candidate — verify)

- Role: proof-of-concept Visium across PFC in early/late AD (medRxiv).
- candidate — EXECUTE to verify open availability; report blocked if
  unavailable, do not fabricate.
- Target path: `./datasets/chen_2023_visium_pfc/`

## Fetch convention

- All accessions land under `<shared_cache>/<name>/`.
- Each fetched file gets a `SOURCE.txt` sidecar recording URL,
  timestamp, sha256, byte count, region, and parser tool
  (squidpy / scanpy.read_visium / scanpy.read_h5ad / anndata.read).
- See `engine/prompts/execute.md` § *On-the-way data fetching*
  for the canonical protocol.

_Manifest authored from IDEATE direction d6._
