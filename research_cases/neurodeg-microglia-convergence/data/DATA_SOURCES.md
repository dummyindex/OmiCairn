# neurodeg-microglia-convergence data sources

Extension of two lab-KB findings (APOE4→LDAM JT p=0.006; AD ECM–integrin
disruption) into a cross-disease (AD vs DLB vs PDD) microglial-convergence
test. Nothing is pre-staged on disk; EXECUTE fetches everything below into
the shared cache `./datasets/<accession>/` on the way. EXECUTE MUST use
REAL fetched public data — if blocked/missing, report honestly and fall
back, never simulate. FINAL analysis uses the FULL data (no subsampling).
CPU-only; no GPU required.

Availability checked 2026-06-07 (`curl -sI` + WebFetch on the accession
pages); results recorded per dataset.

## Datasets to fetch

### GSE303823 / Zenodo 11107597 (PRIMARY discovery cohort, OPEN — VERIFIED)

- Role: cross-disease discovery cohort. Postmortem prefrontal cortex
  snRNA-seq; NCI control n=4, AD n=4, DLB n=6, PDD n=6 (≈20 donors).
  Paper's own headline: shared myelin-enriched microglia + increased
  MSR1-mediated phagocytosis across dementia subtypes.
- Modality: 10x single-nucleus RNA-seq
- Availability: VERIFIED — GEO page HTTP 200; processed Seurat object
  `rna_list.rds` (1.2 GB) openly downloadable on Zenodo 11107597 under
  CC-BY 4.0.
- URLs:
  - https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE303823
  - https://zenodo.org/records/11107597
- Target path: `./datasets/GSE303823/`
- Fetch: prefer the Zenodo `rna_list.rds` (convert via sceasy/anndata2ri
  or analyse in R); fall back to GEO supplementary matrices if it fails
  to parse. Confirm disease-group labels before testing H1/H5.

### SEA-AD MTG (ORTHOGONAL AD-arm replication, OPEN — VERIFIED open atlas)

- Role: AD-arm replication of H1/H2 + the APOE4-specificity test (H4).
  The Microglia-PVM MTG object underlies the prior lab-KB JT finding
  (CellxGene collection 1ca90a2d).
- Modality: 10x single-nucleus RNA-seq (MTG)
- Availability: open via CellxGene + AWS Open Data Registry.
- URLs:
  - https://cellxgene.cziscience.com/collections/1ca90a2d
  - https://registry.opendata.aws/allen-sea-ad-atlas/
- Target path: `./datasets/seaad_mtg/`
- NOTE: SEA-AD encodes APOE4 carrier status (Y/N/Reference), NOT fine
  APOE genotype — supports the carrier-level H4 test, not an
  APOE4/4-vs-3/3 contrast. Resolve the current CellxGene UUID at fetch
  time (do not assume a stale id).

### SEA-AD DLPFC / A9 (HELD-OUT external validation, OPEN)

- Role: blind external validation for H3 ONLY. The prefrontal (A9) arm,
  held out entirely from clustering / marker-locking; the
  GSE303823-locked LDAM signature is scored here without refitting.
- Modality: 10x single-nucleus RNA-seq (DLPFC / A9)
- Availability: open, same routes as SEA-AD MTG above.
- Target path: `./datasets/seaad_dlpfc_a9/`

## Gated / excluded (flagged, NOT used)

- AMP-AD / ROSMAP raw (Synapse, application-gated): do NOT use raw
  controlled objects; only openly-redistributed processed atlases above.
  No hypothesis depends on gated data.
- GSE157827 raw: referenced for H5 negative-control context only; the
  cross-disease ECM dissociation is tested WITHIN GSE303823 so it does
  not require re-fetching GSE157827.

## Fetch convention

- All accessions land under `<shared_cache>/<name>/`.
- Each fetched file gets a `SOURCE.txt` sidecar recording URL, timestamp,
  sha256, byte count, and parser tool (scanpy.read_h5ad, anndata.read,
  sceasy/anndata2ri for the RDS, cellxgene-census).
- See `engine/prompts/execute.md` § *On-the-way data fetching*.

_Manifest authored as an extension of lab-KB findings (APOE4→LDAM;
AD ECM–integrin), 2026-06-07._
