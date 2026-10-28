# alzheimer-ligrec-braak-breakdown data sources

IDEATE direction d5: ligand-receptor communication breakdown across
Braak stage. Nothing is pre-staged on disk; EXECUTE fetches all
datasets below into the shared cache `./datasets/<accession>/` on the
way. EXECUTE MUST use REAL fetched public data — prefer
openly-downloadable GEO supplementary files; if blocked/missing, report
honestly and fall back, never simulate.

## Datasets to fetch

### GSE157827 (primary, OPEN)

- Role: primary PFC reference (Lau 2020, snRNA-seq). Sufficient for a
  first-pass L-R disruption analysis (H1) and GWAS enrichment (H2).
- Region: prefrontal cortex
- Nuclei: 169,496
- Primary URL: https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE157827
- Target path: `./datasets/GSE157827/`
- NOTE: Braak-stage / severity metadata is required to regress L-R
  scores on Braak (H1). If only binary AD-vs-control labels exist,
  report the monotone-Braak claim untestable and fall back to an
  AD-vs-control contrast.

### SEA-AD (secondary, OPEN — multi-region + neuropathology)

- Role: open multimodal atlas across multiple regions and the full
  severity spectrum (Gabitto 2024); used for the Braak trajectory (H1)
  and the exploratory multi-region tau-spread direction
  (EC→hippocampus→PFC) if regions harmonise.
- Primary URL: https://portal.brain-map.org/explore/seattle-alzheimers-disease
- Alt / companion: Synapse syn64410371 / AWS open data registry
- Target path: `./datasets/seaad_syn64410371/`

### Mathys 2019 (ACCESS-CONTROLLED — do NOT fetch / do NOT depend on)

- Role: aspirational-only secondary for a future iteration. ROSMAP PFC
  snRNA-seq, Synapse syn3219045 — ACCESS-CONTROLLED via the AD
  Knowledge Portal (requires a data-use application). EXECUTE must NOT
  attempt to fetch it and must NOT block on it. The case must not
  depend on it.
- Pointer: https://www.synapse.org/#!Synapse:syn3219045

## Reference gene sets

- Top-20 AD GWAS loci (BIN1, CLU, CR1, MS4A, PICALM, TREM2, ...) for
  the H2 enrichment test. Lift from a published AD GWAS meta-analysis
  (e.g. Bellenguez 2022 / Nalls 2024) and record source + locus list in
  analysis.md. Target path: `./datasets/ad_gwas_loci/`
- L-R inference reference: CellChatDB / NicheNet ligand-receptor
  databases (open). Record version + source in analysis.md.

## Fetch convention

- All accessions land under `<shared_cache>/<name>/`.
- Each fetched file gets a `SOURCE.txt` sidecar recording URL,
  timestamp, sha256, byte count, and parser tool.
- See `engine/prompts/execute.md` § *On-the-way data fetching*
  for the canonical protocol.

_Manifest authored from IDEATE direction d5._
