# alzheimer-sst-interneuron-ei data sources

IDEATE direction d5: hippocampal/limbic SST+ inhibitory interneuron loss
as the earliest driver of E/I imbalance. Nothing is pre-staged on disk;
EXECUTE fetches all datasets below into the shared cache
`./datasets/<accession>/` on the way. EXECUTE MUST use REAL fetched
public data — prefer openly-downloadable GEO supplementary files; if
blocked/missing, report honestly and fall back, never simulate.

## Datasets to fetch

### GSE138852 (primary, OPEN — candidate accession)

- Role: primary entorhinal/limbic cortex reference (Grubman 2019,
  snRNA-seq, 6 AD + 6 control); the entorhinal cortex is the first
  region to develop tau pathology and carries SST+ interneurons.
- Candidate accession — EXECUTE to verify; report blocked if
  unavailable, do not fabricate.
- Region: entorhinal cortex (limbic, not hippocampus proper — state
  honestly)
- Modality: 10x snRNA-seq
- Primary URL: https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE138852
- Target path: `./datasets/GSE138852/`
- NOTE: H1 needs Braak/tau staging + inhibitory-subtype resolution (SST,
  PVALB, VIP, LAMP5); H2 needs excitatory-subtype resolution. EXECUTE
  must confirm these exist BEFORE testing. If a covariate or annotation
  is missing, report honestly and fall back (do NOT impute Braak stage
  or cell-type labels).

### SEA-AD (secondary, OPEN — Braak-ordered atlas, candidate)

- Role: Braak-ordered open atlas (MTG + DLPFC) with high-resolution
  inhibitory-subtype annotation; strongest source for the H1
  Braak-ordered SST+ trend and the H2 precedence test if GSE138852 lacks
  the needed covariates (Gabitto 2024).
- Candidate — EXECUTE to verify; report blocked if unavailable, do not
  fabricate.
- Primary URL: https://portal.brain-map.org/explore/seattle-alzheimers-disease
- Alt / companion: Synapse syn64410371 / AWS open data registry
- Target path: `./datasets/seaad_syn64410371/`

## Note on access-controlled sources (not fetched)

The strongest primary evidence for SST+ loss (Mathys 2024 multiregion,
6 brain regions including hippocampus) is deposited on Synapse and is
ACCESS-CONTROLLED — it requires an approved data-access application and
is therefore NOT a fetch target for this case. This case routes the
analysis through OPEN data only.

## Fetch convention

- All accessions land under `<shared_cache>/<name>/`.
- Each fetched file gets a `SOURCE.txt` sidecar recording URL,
  timestamp, sha256, byte count, and parser tool.
- See `engine/prompts/execute.md` § *On-the-way data fetching*
  for the canonical protocol.

_Manifest authored from IDEATE direction d5._
