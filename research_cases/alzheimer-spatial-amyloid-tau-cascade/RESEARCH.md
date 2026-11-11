---
research_mode: DISCOVERY
domain: neurodegeneration_scRNAseq
topic_slug: alzheimer-spatial-amyloid-tau-cascade

research_question: |
  Direction d6 from the Alzheimer's-mechanism IDEATE run
  (workspaces/ideate-alzheimer-mechanism-20260606-153912/000_ideate).

  Multi-region spatial transcriptomics of the amyloid→tau propagation
  cascade: identifying transcriptional watersheds ("pre-tau
  watersheds") preceding tau arrival.

  Can spatially resolved gene expression patterns in entorhinal cortex,
  hippocampus, and prefrontal cortex — measured by Visium or MERFISH —
  identify cell-type-specific 'pre-tau' transcriptional states (in
  neurons, astrocytes, and microglia) that systematically precede tau
  pathology arrival in each region, allowing prediction of the Braak
  progression vector from spatial molecular data alone?

  Holtzman established the temporal biomarker sequence and Leng 2021
  showed region-specific transcriptional changes, but the spatial
  molecular landscape immediately BEFORE tau arrives in each region —
  the 'pre-tau watershed' — is unknown. This OmiCairn run uses multi-region
  spatial transcriptomics (with cell-type deconvolution against matched
  snRNA-seq signatures) to ask whether pre-tau transcriptional states
  are spatially coherent and predictive of the Braak propagation
  vector.

  Hypotheses (pre-registered, lifted from d6):

    H1 (primary — spatial DAM activation zone precedes tangles):
        Spatial microglial expression of TREM2, LPL, and SPP1 (DAM
        markers) is significantly elevated in a 200–500 µm halo around
        tau-sparse but amyloid-dense spots compared to regions with
        neither pathology, defining a spatial 'DAM activation zone'
        that precedes tangle formation and could serve as a leading
        indicator of impending neuronal tau accumulation.
        Decision rule: classify spots/cells by local pathology
        (amyloid-dense/tau-sparse vs neither) using spatial pathology
        annotation or an amyloid/tau transcriptional proxy; compute the
        DAM-marker (TREM2, LPL, SPP1) module score in the 200–500 µm
        halo vs the neither-pathology background; report effect size +
        p-value + 95% CI. H1 passes if the DAM module is significantly
        elevated in the amyloid-dense/tau-sparse halo (p<0.05, 95% CI
        excluding 0) with the correct sign.

    H2 (secondary — pre-tau reactive astrocyte states in Braak 0 EC):
        Pre-tau astrocyte states in entorhinal cortex (Braak 0 brains)
        are transcriptionally distinguishable from naive astrocytes and
        resemble intermediate-reactive states described in Leng 2021,
        suggesting astrocytic reactive state transitions precede tangle
        formation and may be a cause rather than a consequence of EC
        layer-II neuron vulnerability.
        Decision rule: in Braak 0 EC, test whether astrocytes are
        transcriptionally separable from naive astrocytes (cluster
        separation / classifier AUC) and whether they score positively
        on the Leng 2021 intermediate-reactive astrocyte signature;
        report effect size (AUC / module-score shift) + p-value + 95%
        CI. H2 passes if Braak-0 EC astrocytes are significantly
        separable AND score positively on the intermediate-reactive
        signature (p<0.05, 95% CI excluding 0).

  IMPORTANT — DATA-ACCESS caveat (d6 leans on SEA-AD spatial):
  This direction's enabling resource is SEA-AD spatial transcriptomics.
  IDEATE flagged SEA-AD spatial as partly OPEN but PORTAL-GATED, and
  flagged that its spatial coverage of the full EC–hippocampus–PFC axis
  is uncertain. EXECUTE MUST NOT assume access. Before any spatial
  analysis, EXECUTE MUST: (1) verify SEA-AD spatial data is openly
  downloadable (not application-gated) at the portal / AWS open-data
  registry; (2) verify which brain regions are actually covered. If
  SEA-AD spatial is unavailable, application-gated, or does not cover
  the needed regions, EXECUTE MUST report that explicitly — either as
  BLOCKED, or by falling back to the snRNA-seq fallback below for the
  regional-transcription part — and MUST NOT fabricate spatial spots,
  coordinates, or pathology annotations.

  Fallback (clearly OPEN snRNA-seq, regional-transcription part only):
  if spatial data is unavailable, use **GSE147528** (Leng 2021, EC +
  SFG, Braak-staged) and/or **GSE138852** (Grubman 2019, EC) to test
  the regional / cell-state components of H1 (DAM-marker elevation by
  region and pathology stage) and H2 (Braak-0 EC astrocyte separability
  vs the Leng intermediate-reactive signature). The halo/spatial-zone
  part of H1 is only testable with spatial data; if only snRNA-seq is
  available, report the spatial sub-claim as untestable and test the
  region/stage component instead. Do NOT simulate spatial structure.

  A negative or partial result is fully informative. If no spatial DAM
  activation zone precedes tangles, or Braak-0 EC astrocytes are not
  separable, that must be the headline rather than re-tuning halo radii
  or signatures until H1/H2 pass.

data:
  location: ./research_cases/alzheimer-spatial-amyloid-tau-cascade/data
  shared_cache: ./datasets
  description: |
    **No pre-staged raw data on disk.** Every dataset below is publicly
    deposited and MUST be fetched at run time by EXECUTE into the shared
    cache `./datasets/<accession>/`. EXECUTE MUST use REAL fetched
    public data — prefer openly-downloadable supplementary files
    (Visium / MERFISH objects; processed h5ad / matrix for snRNA-seq).
    If a dataset, the spatial modality, or a required covariate (region,
    Braak stage, pathology annotation) is blocked, application-gated,
    missing, or unparseable, report that honestly in
    `analysis.md § Limitations` and fall back to the remaining open data
    — NEVER simulate or fabricate. Acceptable reasons to defer a fetch:
    (1) HTTP 404/410 / accession removed; (2) sha256 verification fails;
    (3) data is application-gated / not openly downloadable;
    (4) `budgets.allow_web` is false. Size alone is NOT acceptable.

    All reported effects MUST carry effect size + p-value + 95% CI
    together. A p-value alone is not a result.

    **Primary candidate dataset (spatial — ACCESS UNCERTAIN, verify):**

      - **SEA-AD spatial transcriptomics (Gabitto 2024).** Multi-region
        spatial data is the enabling resource for the pre-tau-watershed
        / halo analysis. candidate — EXECUTE to verify open
        availability; report blocked if unavailable, do not fabricate.
        IDEATE flagged this as partly OPEN but PORTAL-GATED, with
        uncertain coverage of the full EC–hippocampus–PFC axis. EXECUTE
        MUST confirm (a) the spatial data is openly downloadable (not
        application-gated) and (b) which regions are covered, BEFORE any
        spatial analysis. If gated/unavailable/insufficient coverage →
        report BLOCKED or fall back to the snRNA-seq cohorts below; do
        NOT assume access. Allen Brain Map portal / AWS open data
        registry / Synapse syn64410371.
          https://portal.brain-map.org/explore/seattle-alzheimers-disease
        Target path: ./datasets/seaad_spatial/

    **Open snRNA-seq fallback (clearly OPEN — regional-transcription part):**

      - **Leng 2021 (EC + SFG, Braak-staged).**
        GEO **GSE147528** — snRNA-seq of EC + SFG across Braak stages.
        Use for the region/stage component of H1 (DAM-marker elevation
        by region and pathology stage) and the H2 intermediate-reactive
        astrocyte signature. Openly downloadable at NCBI GEO.
          https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE147528
        Target path: ./datasets/GSE147528/

      - **Grubman 2019 (entorhinal cortex).**
        GEO **GSE138852** — snRNA-seq of EC; second open EC cohort for
        the Braak-0 EC astrocyte separability test (H2) and a cell-type
        signature reference for spatial deconvolution if spatial data is
        available. Openly downloadable at NCBI GEO.
          https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE138852
        Target path: ./datasets/GSE138852/

    **Optional supporting spatial reference (candidate — verify):**

      - **Chen 2023 spatial transcriptomics (Visium PFC, medRxiv).**
        Proof-of-concept Visium across PFC in early/late AD. candidate —
        EXECUTE to verify open availability; report blocked if
        unavailable, do not fabricate.
        Target path: ./datasets/chen_2023_visium_pfc/

    **Shared dataset cache convention:** every accession is downloaded
    into `./datasets/<accession>/` with a sidecar `SOURCE.txt` recording
    URL, fetch timestamp, sha256, byte count, region, and the parsing
    tool used (squidpy, scanpy.read_h5ad, scanpy.read_visium,
    anndata.read, scanpy.read_mtx, etc.). Subsequent runs check the
    cache first and skip re-downloads.

  cohorts: [seaad_spatial_multiregion, leng_2021_EC_SFG, grubman_2019_EC]
  modalities: [spatial_visium, spatial_merfish, snRNA_seq_10x]

success_criteria:
  primary_metric: pretau_dam_activation_zone_halo
  baseline: neither_pathology_background
  pre_registration: required
  honest_validation: required
  notes: |
    Primary success = H1 reaches significance with the correct sign:
    the DAM module (TREM2, LPL, SPP1) is elevated in the 200–500 µm halo
    around amyloid-dense/tau-sparse spots vs neither-pathology
    background (p<0.05, 95% CI excluding 0), using REAL fetched spatial
    data. If SEA-AD spatial (and any other spatial source) is
    unavailable / application-gated / lacks region coverage, the spatial
    halo sub-claim is untestable — report that as the headline
    limitation (BLOCKED) and report the region/stage component on the
    open snRNA-seq fallback instead, not a fabricated spatial result.

    H2 success = Braak-0 EC astrocytes are significantly separable from
    naive astrocytes AND score positively on the Leng 2021
    intermediate-reactive astrocyte signature (p<0.05, 95% CI excluding
    0). If Braak-0 EC astrocytes or the required covariates are missing,
    report descriptively and mark H2 untestable.

    All effects reported as effect size + p-value + 95% CI together.
    Partial results (e.g. region/stage component testable on snRNA-seq
    but spatial halo BLOCKED) must be stated plainly in `analysis.md` and
    `negative_results.md`, not framed as success.

figures_planned:
  - id: F1
    title: "Multi-region spatial / regional cell-type landscape"
    panels:
      - "Spatial map (if available) of EC / hippocampus / PFC colored by deconvolved cell type"
      - "Pathology annotation overlay: amyloid-dense / tau-dense / neither (or transcriptional proxy)"
      - "Region × Braak-stage cell-type composition summary"
  - id: F2
    title: "Spatial DAM activation zone preceding tangles (H1)"
    panels:
      - "DAM module (TREM2, LPL, SPP1) score vs distance from amyloid-dense/tau-sparse spots (halo profile)"
      - "Halo (200–500 µm) vs neither-pathology background: effect size + 95% CI"
      - "Region/stage component on snRNA-seq fallback (DAM-marker elevation by region and stage)"
  - id: F3
    title: "Pre-tau reactive astrocyte states in Braak-0 EC (H2)"
    panels:
      - "Braak-0 EC astrocyte vs naive astrocyte separability (classifier AUC + 95% CI)"
      - "Leng 2021 intermediate-reactive astrocyte signature score in Braak-0 EC astrocytes"
      - "Astrocyte reactive-state continuum across Braak stages"
  - id: F4
    title: "Data-access and honest-failure panel"
    panels:
      - "Access/coverage audit table: SEA-AD spatial openness + region coverage + fallback datasets fetched"
      - "Spot/cell pathology-annotation availability (or proxy) per dataset"
      - "Sensitivity to halo radius and the DAM / reactive-astrocyte signatures"

figures_not_in_scope:
  - "Raw FASTQ / spatial image re-processing (processed objects suffice)"
  - "De novo amyloid/tau immunohistochemistry — wet-lab"
  - "Application-gated SEA-AD spatial data if not openly downloadable (report BLOCKED instead)"

budgets:
  max_phase_wall_minutes: 240
  max_total_wall_minutes: 360
  allow_web: true
  claude_model: claude-sonnet-4-6
  max_phase_budget_usd: 25.0

auto_approve: true
---

# alzheimer-spatial-amyloid-tau-cascade — pre-tau watershed (IDEATE d6)

## Source materials

Transcribed faithfully from IDEATE direction **d6**
(`workspaces/ideate-alzheimer-mechanism-20260606-153912/000_ideate/directions.json`).
Anchor and supporting literature lifted from the direction's `cites`:

- Gabitto M.I. et al. *SEA-AD: A multimodal cellular atlas and resource
  for Alzheimer's disease.* Nature Aging (2024). Spatial transcriptomics
  across multiple regions — partly OPEN but portal-gated; coverage of the
  full EC–hippocampus–PFC axis uncertain (EXECUTE to verify).
- Chen J. et al. *Spatial transcriptomic patterns underlying regional
  vulnerability to amyloid-β and tau pathologies.* medRxiv (2023).
  (Visium PFC proof-of-concept.)
- Li Q. et al. *Single-nucleus multiomics reveals the disrupted
  regulatory programs in three brain regions of sporadic early-onset
  Alzheimer's disease.* Science Advances (2024; PMC11326379).
- Leng K. et al. *Molecular characterization of selectively vulnerable
  neurons in Alzheimer's disease.* Nature Neuroscience (2021). EC + SFG,
  Braak-staged. GEO GSE147528. (Intermediate-reactive astrocyte states.)
- Grubman A. et al. *A single-cell atlas of entorhinal cortex from
  individuals with Alzheimer's disease.* Nature Neuroscience (2019).
  GEO GSE138852.
- Lin et al. *Astrocyte transcriptomic changes along the spatiotemporal
  progression of Alzheimer's disease.* (2024).
- David Holtzman position: tau spread timing is the key mediator of
  cognitive decline.

## Why this matters for OmiCairn

The propagation order amyloid→tau is central to AD, yet the molecular
state of a region just before tau arrives — the 'pre-tau watershed' —
is unmapped. This case asks OmiCairn to test whether a spatial DAM
activation zone precedes tangles and whether Braak-0 EC astrocytes are
already in an intermediate-reactive state. It is also the case that most
stresses honest data-access handling: the enabling spatial resource is
portal-gated with uncertain coverage, so OmiCairn must verify openness and
fall back (or report BLOCKED) rather than assume access.

OmiCairn challenges this exercises:

1. **Data-access verification** of a portal-gated spatial resource, with
   a clearly-open snRNA-seq fallback for the regional component.
2. **Spatial halo analysis** (DAM module vs distance from
   amyloid-dense/tau-sparse spots), if spatial data is open.
3. **Astrocyte-state separability** in Braak-0 EC against an external
   intermediate-reactive signature (H2).
4. **Honest failure** — the spatial halo sub-claim is untestable
   without open spatial data; the agent must report BLOCKED and fall
   back to the region/stage component, not fabricate spots.

## Critical correctness rules (EXECUTE must obey)

- Use REAL fetched public data only. Prefer openly-downloadable
  supplementary files. If blocked / application-gated / missing → say so
  in Limitations and fall back; NEVER simulate spatial spots,
  coordinates, or pathology annotations.
- SEA-AD spatial is a CANDIDATE: verify open availability AND region
  coverage before use. If gated/unavailable, report BLOCKED or fall
  back to GSE147528 / GSE138852 for the regional-transcription part.
- Report effect size + p-value + 95% CI together for every claim.
- Audit access + covariate availability (region, Braak stage, pathology
  annotation) BEFORE asserting H1/H2; if missing, mark the relevant
  sub-claim untestable and fall back.

## Out of scope (for this iteration)

- Raw FASTQ / spatial image re-processing.
- De novo amyloid/tau immunohistochemistry.
- Application-gated SEA-AD spatial data if not openly downloadable
  (report BLOCKED instead of attempting controlled access).
