# alzheimer-gwas-snatac-celltype data sources

IDEATE direction d3: cell-type-resolved functional dissection of AD GWAS
variants in microglial vs neuronal cis-regulatory elements via snATAC-seq.
Nothing is pre-staged on disk; EXECUTE fetches all datasets below into
the shared cache `./datasets/<accession>/` on the way. EXECUTE MUST use
REAL fetched public data — prefer openly-downloadable GEO supplementary
files; if blocked/missing, report honestly and fall back, never simulate.

## Datasets to fetch

### GSE174367 (primary, OPEN — has snATAC)

- Role: primary multiome reference (Morabito 2021, snRNA + snATAC, PFC).
  Use the **snATAC arm** for the cell-type-resolved peak–variant
  analysis; the snRNA arm supplies matched cell-type labels.
- Region: prefrontal cortex
- Modality: 10x multiome (snRNA-seq + snATAC-seq)
- Primary URL: https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE174367
- Target path: `./datasets/GSE174367/`
- NOTE: snATAC peak–variant integration is heavier than RNA-only work.
  EXECUTE must confirm the snATAC fragment/peak matrix is present and
  parseable BEFORE testing H1/H2. If peak calls or variant overlap
  cannot be computed, report honestly and fall back (do NOT fabricate
  peaks, QTLs, or genotypes).

### AD GWAS variant list (OPEN — required for the overlap)

- Role: top AD GWAS loci (lead / credible-set variants) to overlap with
  cell-type-resolved snATAC peaks.
- Source: ADGC public AD GWAS summary statistics (adgc.nih.gov) and/or
  published supplementary lead-variant tables of the AD GWAS
  meta-analyses cited by IDEATE (all-cause/vascular dementia
  meta-analysis; multi-ancestry 56,241-individual meta-analysis).
- NOTE: record the exact source URL and genome build in SOURCE.txt.
  NEVER fabricate variant rsIDs or coordinates. If no public variant
  list can be fetched, H1/H2 are untestable; say so and fall back.

### SEA-AD snATAC (secondary; candidate — verify)

- Role: open multimodal atlas with snATAC-seq + APOE genotype +
  quantitative neuropathology (Gabitto 2024); strongest source for the
  H2 stage contrast if GSE174367's snATAC arm is insufficient.
- candidate — EXECUTE to verify open availability; report blocked if
  unavailable, do not fabricate.
- Primary URL: https://portal.brain-map.org/explore/seattle-alzheimers-disease
- Alt / companion: Synapse syn64410371 / AWS open data registry
- Target path: `./datasets/seaad_syn64410371/`

### Science Advances 2024 three-region multiome (optional; candidate — verify)

- Role: PFC/EC/hippocampus snRNA + snATAC, sporadic early-onset AD
  (Li 2024, PMC11326379).
- candidate — EXECUTE to verify open availability; report blocked if
  unavailable, do not fabricate.
- Target path: `./datasets/sciadv_2024_multiregion/`

### ENCODE SCREEN cCRE (optional reference; candidate — verify)

- Role: reference candidate cis-regulatory elements for annotating
  variant–peak overlaps.
- Primary URL: https://screen.encodeproject.org/
- candidate — use only if openly fetchable.
- Target path: `./datasets/encode_screen_ccre/`

## Fetch convention

- All accessions land under `<shared_cache>/<name>/`.
- Each fetched file gets a `SOURCE.txt` sidecar recording URL,
  timestamp, sha256, byte count, genome build, and parser tool
  (snapatac2 / ArchR / signac / scanpy.read_h5ad / anndata.read).
- See `engine/prompts/execute.md` § *On-the-way data fetching*
  for the canonical protocol.

_Manifest authored from IDEATE direction d3._
