# alzheimer-oligo-apoe4-myelin data sources

IDEATE direction d1: APOE4 oligodendrocyte cholesterol/myelin failure as
an early AD driver. Nothing is pre-staged on disk; EXECUTE fetches all
datasets below into the shared cache `./datasets/<accession>/` on the
way. EXECUTE MUST use REAL fetched public data — prefer
openly-downloadable GEO supplementary files; if blocked/missing, report
honestly and fall back, never simulate.

## Datasets to fetch

### GSE174367 (primary, OPEN — multiome RNA + ATAC)

- Role: primary PFC multiome reference (Morabito 2021, snRNA + snATAC,
  191,890 nuclei). Use the snRNA arm for H1 (oligodendrocyte cholesterol
  biosynthesis) and the snATAC arm for H2 (CLU/APOE locus accessibility
  + SREBF1/NKX2-2 motifs).
- Region: prefrontal cortex
- Modality: 10x multiome (snRNA-seq + snATAC-seq)
- Primary URL: https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE174367
- Target path: `./datasets/GSE174367/`
- NOTE: H1 needs APOE genotype + Braak/tau; the Braak 0-II restriction
  needs Braak staging; H2 needs a usable oligodendrocyte snATAC arm.
  EXECUTE must confirm these exist BEFORE testing. If a covariate or the
  ATAC arm is missing, report honestly and fall back (do NOT impute
  genotype, neuropathology, or peaks).

### GSE157827 (secondary, OPEN — transcriptional replication)

- Role: replication cohort for H1 (Lau 2020, snRNA-seq, ~170,000 nuclei,
  PFC); includes oligodendrocyte transcriptional changes.
- Region: prefrontal cortex
- Modality: 10x snRNA-seq
- Primary URL: https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE157827
- Target path: `./datasets/GSE157827/`

### SEA-AD (optional tertiary — spatial, candidate)

- Role: spatial localisation of myelin lesions near plaques (MERFISH),
  with APOE genotype + quantitative neuropathology (Gabitto 2024).
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

_Manifest authored from IDEATE direction d1._
