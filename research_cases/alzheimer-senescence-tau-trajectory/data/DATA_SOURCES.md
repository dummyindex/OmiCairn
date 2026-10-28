# alzheimer-senescence-tau-trajectory data sources

IDEATE direction d4: p21/SASP senescence pseudotime trajectory in
tau-bearing entorhinal-cortex neurons.
Nothing is pre-staged on disk; EXECUTE fetches all datasets below into
the shared cache `./datasets/<accession>/` on the way. EXECUTE MUST use
REAL fetched public data — prefer openly-downloadable GEO supplementary
files; if blocked/missing, report honestly and fall back, never simulate.

## Datasets to fetch

### GSE138852 (primary, OPEN)

- Role: open entorhinal-cortex snRNA-seq cohort (Grubman 2019);
  supplies RORB+ neurons and matched microglia for H2.
- Region: entorhinal cortex
- Modality: 10x snRNA-seq
- Primary URL: https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE138852
- Target path: `./datasets/GSE138852/`

### GSE147528 (primary, OPEN — Braak-staged)

- Role: EC + SFG snRNA-seq across Braak stages (Leng 2021); core
  resource for the H1 stage contrast (Braak II–III vs 0–I) and the
  trans-entorhinal EC→SFG spread question.
- Region: entorhinal cortex + superior frontal gyrus
- Modality: 10x snRNA-seq
- Primary URL: https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE147528
- Target path: `./datasets/GSE147528/`
- NOTE: H1 needs Braak stage + a resolvable RORB+ EC excitatory
  population. EXECUTE must confirm these BEFORE testing H1. If Braak
  staging is absent, report honestly and fall back to a pseudotime-
  ordered senescence-module description (do NOT impute Braak stage,
  cell-type identity, or cross-sample matches).

### SEA-AD (secondary; candidate — verify)

- Role: open multimodal atlas with neurons + microglia across the AD
  continuum and quantitative neuropathology (Gabitto 2024); strengthens
  the stage contrast and the neuron–microglia link.
- candidate — EXECUTE to verify open availability; report blocked if
  unavailable, do not fabricate.
- Primary URL: https://portal.brain-map.org/explore/seattle-alzheimers-disease
- Alt / companion: Synapse syn64410371 / AWS open data registry
- Target path: `./datasets/seaad_syn64410371/`

## Fetch convention

- All accessions land under `<shared_cache>/<name>/`.
- Each fetched file gets a `SOURCE.txt` sidecar recording URL,
  timestamp, sha256, byte count, and parser tool.
- See `engine/prompts/execute.md` § *On-the-way data fetching*
  for the canonical protocol.

_Manifest authored from IDEATE direction d4._
