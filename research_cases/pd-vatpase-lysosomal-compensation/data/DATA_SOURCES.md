# pd-vatpase-lysosomal-compensation data sources

Confirmatory replication of a self-generated lab-KB finding: CLEAR-network
V-ATPase UPregulation as a compensatory response in PD-genotype DA neurons.
We pre-register the OBSERVED UP-direction and test it in INDEPENDENT
cohorts/modalities. Nothing is pre-staged on disk; EXECUTE fetches
everything below into the shared cache `./datasets/<accession>/` on the way.
EXECUTE MUST use REAL fetched public data — if blocked/missing, report
honestly and fall back, never simulate. FINAL analysis uses the FULL data
(no subsampling). CPU-only; no GPU required.

Availability checked 2026-06-07 (`curl -sI` + WebFetch); results recorded
per dataset.

## Datasets to fetch

### GSE167335 (PRIMARY — independent LRRK2 replication, OPEN — VERIFIED)

- Role: independent replication of the original modality. iPSC-derived
  dopaminergic neurons — control, LRRK2 G2019S PD, gene-edited isogenic
  corrected lines.
- Modality: NanoString nCounter Human Neuropathology panel (targeted).
- Samples: 10 (1 healthy + 2 PD + 2 corrected donors × 2–3
  differentiations).
- Availability: VERIFIED — GEO HTTP 200.
- URL: https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE167335
- Target path: `./datasets/GSE167335/`
- NOTE: targeted panel — may not measure all 9 CLEAR V-ATPase subunits.
  EXECUTE must verify panel coverage and test the measured subset;
  collapse differentiation replicates to DONOR means before testing
  (avoid pseudo-replication); report the achievable permutation-p floor.

### E-MTAB-7303 (SECONDARY — GBA1-mutant arm, OPEN CC-BY — VERIFIED w/ caveat)

- Role: GBA1-mutant generalisation in an independent modality. iPSC-DA
  neurons from GBA-N370S PD patients, controls, and idiopathic PD.
- Modality: BOTH single-cell and bulk RNA-seq.
- Lines: 13 iPSC lines (6 control, 4 GBA-N370S, 4 idiopathic PD).
- Availability: the Lang 2020 Cell Reports paper states deposition at
  ArrayExpress E-MTAB-7303 under CC-BY. At check time the BioStudies
  endpoint returned a TRANSIENT HTTP 500 (server-side, NOT a 404 — the
  legacy ArrayExpress URL 302-redirects, confirming the accession is
  registered). RETRY at fetch time; if genuinely unreachable (sustained
  404/410), report honestly and proceed with H1+H3 (LRRK2 + postmortem).
- URL: https://www.ebi.ac.uk/biostudies/arrayexpress/studies/E-MTAB-7303
- Target path: `./datasets/E-MTAB-7303/`
- Use single-cell arm for H4 DA-selectivity; bulk arm for H2 module DE.

### GSE157783 (HELD-OUT validation — postmortem SN, OPEN — VERIFIED)

- Role: blind external validation. Postmortem human midbrain (substantia
  nigra) snRNA-seq, idiopathic PD vs control (Smajić 2022). Used ONLY for
  H3 (score the iPSC-locked V-ATPase module without refitting) and H4
  (in-vivo DA-selectivity); never used to define the module.
- Modality: 10x single-nucleus RNA-seq.
- Samples: 5 idiopathic PD + 6 controls (11 donors).
- Availability: VERIFIED — GEO HTTP 200.
- URL: https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE157783
- Target path: `./datasets/GSE157783/`

## Gated / excluded (flagged, NOT used)

- Kamath 2022 substantia-nigra atlas (dbGaP-controlled): the deepest DA
  SN atlas but application-gated. Do NOT require it; GSE157783 is the open
  postmortem SN substitute. No hypothesis depends on Kamath.

## A note on the original proposal accession

The proposal listed GSE249888 for the iPSC-DA arm. Verification on
2026-06-07 showed GSE249888 is a PROSTATE-CANCER / ZMYND8 RNA-seq study —
NOT iPSC-DA Parkinson's. It was therefore replaced (not fabricated) with
the verified open GBA-N370S iPSC-DA dataset E-MTAB-7303 (Lang 2020,
CC-BY), which better serves the GBA1-mutant arm (H2) anyway.

## Fetch convention

- All accessions land under `<shared_cache>/<name>/`.
- Each fetched file gets a `SOURCE.txt` sidecar recording URL, timestamp,
  sha256, byte count, and parser tool (NanoString CSV parser,
  scanpy.read_h5ad, anndata.read, AE/cellxgene downloader,
  salmon/featureCounts for bulk).
- See `engine/prompts/execute.md` § *On-the-way data fetching*.

_Manifest authored as a confirmatory self-replication of the lab-KB
V-ATPase compensatory-UP finding, 2026-06-07._
