# alzheimer-sex-apoe4-mito data sources

IDEATE direction d6: sex × APOE4 interaction in neurons; mitochondrial-
bioenergetic failure as a female-specific vulnerability mechanism.
Nothing is pre-staged on disk; EXECUTE fetches all datasets below into
the shared cache `./datasets/<accession>/` on the way. EXECUTE MUST use
REAL fetched public data — prefer openly-downloadable GEO supplementary
files; if blocked/missing, report honestly and fall back, never simulate.

## Datasets to fetch

### GSE174367 (primary, OPEN — multiome RNA + ATAC)

- Role: primary PFC multiome reference (Morabito 2021, snRNA + snATAC,
  191,890 nuclei). Use the snRNA arm for H1 (excitatory-neuron OXPHOS
  sex × APOE4 × Braak interaction) and the snATAC arm for H2 (OXPHOS-
  region accessibility + ERE motif test).
- Region: prefrontal cortex
- Modality: 10x multiome (snRNA-seq + snATAC-seq)
- Primary URL: https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE174367
- Target path: `./datasets/GSE174367/`
- NOTE: H1 needs SEX + APOE genotype + Braak/tau, with adequate donor
  counts per sex × genotype cell; H2 needs a usable excitatory-neuron
  snATAC arm. EXECUTE must confirm these exist and that group sizes are
  adequate BEFORE testing. Sex metadata availability is NOT guaranteed —
  check. If a covariate or the ATAC arm is missing, or a group is
  underpowered, report honestly and fall back to the supported
  lower-order contrast (do NOT impute sex, genotype, neuropathology, or
  peaks).

### SEA-AD (optional secondary — sex + neuropathology, candidate)

- Role: better-powered open atlas with SEX, APOE genotype, snRNA-seq,
  and quantitative neuropathology across the severity spectrum
  (Gabitto 2024); strongest source for a well-powered sex × genotype
  OXPHOS interaction if GSE174367 lacks sex metadata or has too few
  donors per group.
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

_Manifest authored from IDEATE direction d6._
