---
research_mode: DISCOVERY
domain: neurodegeneration_scRNAseq
topic_slug: alzheimer-bbb-pericyte-vascular

research_question: |
  Direction d2 from the second Alzheimer's-mechanism IDEATE run
  (workspaces/ideate-alzheimer-mechanism-20260607-044616/000_ideate).

  Does pericyte-specific SMAD3 upregulation co-occur with astrocytic
  VEGFA downregulation in a spatially coherent manner across AD Braak
  stages, and does this vascular transcriptional programme precede or
  follow parenchymal amyloid deposition in human brain?

  No prior lab run has examined vascular cells. The "Gliovascular
  transcriptional perturbations" Nature Communications 2024 paper
  identified pericyte SMAD3 upregulation and astrocytic VEGFA
  downregulation as the dominant blood-brain-barrier (BBB) disruption
  signals in AD, with blood SMAD3 levels associating with AD
  neuroimaging outcomes in a separate human cohort. A companion paper
  found HIF1A/VEGFA angiogenic failure in AD endothelial cells in the
  same open dataset (GSE157827, Lau 2020), which is confirmed to contain
  pericyte, endothelial, and astrocyte populations. This OmiCairn run
  stratifies donors by Braak stage, applies per-donor pseudobulk DE to
  vascular cell types, and asks whether the SMAD3/VEGFA axis changes
  before or after amyloid threshold.

  Hypotheses (pre-registered, lifted from d2):

    H1 (primary — pericyte SMAD3 upregulation by Braak):
        Pericyte SMAD3 expression is significantly elevated (pseudobulk,
        FDR < 0.05, fold-change > 1.5) in Braak III-IV vs. Braak 0
        donors in GSE157827.
        Decision rule: per-donor pseudobulk DE within the pericyte
        compartment, Braak III-IV vs Braak 0; report log2FC +
        Benjamini-Hochberg FDR + 95% bootstrap CI for SMAD3. H1 passes
        if SMAD3 is up with fold-change > 1.5 at FDR<0.05 and the 95% CI
        excludes 0. Report astrocytic VEGFA in parallel (expected down)
        with the same statistics as a paired vascular-axis readout.

    H2 (secondary — vascular programme precedes amyloid threshold):
        SMAD3 upregulation in pericytes precedes the amyloid plaque
        density threshold by at least one Braak stage in the GSE157827
        donor series, supporting vascular dysfunction as a trigger
        rather than a consequence.
        Decision rule: order donors by Braak stage; locate the Braak
        stage at which pericyte SMAD3 first rises significantly above
        Braak 0 baseline and the Braak stage at which amyloid plaque
        density (or its closest available neuropathology proxy) first
        crosses its threshold; report the Braak-stage offset with a
        bootstrap 95% CI. H2 passes if the SMAD3-rise stage is at least
        one Braak stage earlier than the amyloid-threshold stage and the
        95% CI of the offset excludes 0.

  IMPORTANT — covariate dependency: H1 requires Braak/tau staging in the
  fetched donors; H2 additionally requires an amyloid plaque-density (or
  equivalent Aβ) neuropathology measure in the same donors. EXECUTE MUST
  first verify these are present. If Braak staging is missing, H1 cannot
  be tested as specified — report that honestly and fall back to an
  AD-vs-control pericyte SMAD3 contrast without Braak ordering. If
  amyloid neuropathology is missing, H2 (the precedence question) is
  untestable — say so and report the Braak-ordered SMAD3 trajectory
  descriptively without claiming precedence over amyloid. Do NOT impute
  or fabricate Braak stage or amyloid measures.

  A negative or partial result is fully informative. If pericyte SMAD3
  is not elevated, or its rise does not precede amyloid, that must be the
  headline rather than re-tuning cluster definitions until H1/H2 pass.

data:
  location: ./research_cases/alzheimer-bbb-pericyte-vascular/data
  shared_cache: ./datasets
  description: |
    **No pre-staged raw data on disk.** Every dataset below is publicly
    deposited and MUST be fetched at run time by EXECUTE into the shared
    cache `./datasets/<accession>/`. EXECUTE MUST use REAL fetched
    public data — prefer openly-downloadable GEO supplementary files
    (processed h5ad / matrix + barcodes + features). If a dataset or a
    required covariate (Braak/tau, amyloid plaque density) is blocked,
    missing, or unparseable, report that honestly in
    `analysis.md § Limitations` and fall back to the remaining open data
    — NEVER simulate or fabricate. Acceptable reasons to defer a fetch:
    (1) HTTP 404/410 / accession removed; (2) sha256 verification fails;
    (3) `budgets.allow_web` is false. Size alone is NOT acceptable.

    All reported effects MUST carry effect size + p-value + 95% CI
    together. A p-value alone is not a result.

    **Primary OPEN dataset:**

      - **Lau 2020 (PFC snRNA-seq).**
        GEO **GSE157827** — ~170,000 nuclei, prefrontal cortex, AD vs
        control; confirmed to contain pericyte, endothelial, and
        astrocyte populations (n=12 AD + 9 control). Openly downloadable
        at NCBI GEO. This is the dataset in which the gliovascular SMAD3/
        VEGFA axis and HIF1A/VEGFA endothelial angiogenic failure were
        originally reported.
          https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE157827
        Target path: ./datasets/GSE157827/
        NOTE: H1 needs Braak/tau staging; H2 additionally needs an
        amyloid plaque-density (or equivalent Aβ) measure. EXECUTE must
        confirm these columns exist in the fetched object before testing;
        if a covariate is absent, report honestly and fall back (see
        research_question above).

    **Optional secondary (spatial localisation):**

      - **SEA-AD (Gabitto 2024).** Open multimodal AD atlas with MERFISH
        spatial data that can localise vascular structures in spatial
        context, with quantitative neuropathology and Braak metadata.
        Candidate — EXECUTE to verify access; report blocked if
        unavailable, do not fabricate. Allen Brain Map portal / AWS open
        data registry / Synapse syn64410371.
          https://portal.brain-map.org/explore/seattle-alzheimers-disease
        Target path: ./datasets/seaad_syn64410371/

    **Shared dataset cache convention:** every accession is downloaded
    into `./datasets/<accession>/` with a sidecar `SOURCE.txt` recording
    URL, fetch timestamp, sha256, byte count, and the parsing tool used
    (scanpy.read_h5ad, anndata.read, scanpy.read_mtx, etc.). Subsequent
    runs check the cache first and skip re-downloads.

  cohorts: [lau_2020_PFC, seaad_2024_multiregion]
  modalities: [snRNA_seq_10x]

success_criteria:
  primary_metric: pericyte_smad3_braak_upregulation
  baseline: vascular_celltype_canonical_marker_sets
  pre_registration: required
  honest_validation: required
  notes: |
    Primary success = H1 pericyte SMAD3 is up with fold-change > 1.5 at
    FDR<0.05 (95% CI excluding 0) in Braak III-IV vs Braak 0 in
    GSE157827, with astrocytic VEGFA reported in parallel. If Braak
    staging is missing, H1 is untestable as specified — report that as
    the headline limitation and fall back to AD-vs-control, not a failure
    to fix by imputation.

    H2 success = the Braak stage at which pericyte SMAD3 first rises is
    at least one stage earlier than the amyloid-threshold stage, with the
    bootstrap 95% CI of the offset excluding 0. If amyloid neuropathology
    is missing, mark H2 untestable and report the Braak-ordered SMAD3
    trajectory descriptively without a precedence claim.

    All effects reported as effect size + p-value + 95% CI together.
    Partial results (e.g. SMAD3 elevated but precedence untestable) must
    be stated plainly in `analysis.md` and `negative_results.md`, not
    framed as success.

figures_planned:
  - id: F1
    title: "Vascular taxonomy: pericytes, endothelial cells, astrocytes"
    panels:
      - "UMAP of all cells with pericyte, endothelial, and astrocyte compartments labeled"
      - "Marker dot plot: pericyte (PDGFRB, RGS5), endothelial (CLDN5, FLT1), astrocyte (AQP4, GFAP) plus SMAD3 and VEGFA"
      - "Per-donor pericyte and astrocyte fractions, grouped by Braak stage"
  - id: F2
    title: "Pericyte SMAD3 upregulation by Braak (H1)"
    panels:
      - "Per-donor pseudobulk forest plot: SMAD3 log2FC + 95% CI, Braak III-IV vs Braak 0"
      - "Paired vascular axis: pericyte SMAD3 (up) and astrocyte VEGFA (down) log2FC + 95% CI"
      - "Volcano: pericyte DE Braak III-IV vs Braak 0 with SMAD3 annotated"
  - id: F3
    title: "Vascular programme vs amyloid threshold (H2)"
    panels:
      - "Pericyte SMAD3 trajectory across Braak stages with the significant-rise stage marked"
      - "Amyloid plaque-density trajectory across Braak stages with the threshold stage marked"
      - "Braak-stage offset (SMAD3-rise minus amyloid-threshold) with bootstrap 95% CI"
  - id: F4
    title: "Covariate-availability and honest-failure panel"
    panels:
      - "Covariate audit table: Braak / amyloid plaque density / vascular-cell availability per fetched atlas"
      - "Cluster-resolution sensitivity of the pericyte vs endothelial split"
      - "Sensitivity to vascular marker-set definition"

figures_not_in_scope:
  - "Raw FASTQ re-alignment (processed objects suffice)"
  - "Plasma / blood SMAD3 protein measurement — separate cohort, wet-lab"
  - "iPSC pericyte or mouse BBB validation — wet-lab"

budgets:
  max_phase_wall_minutes: 240
  max_total_wall_minutes: 360
  allow_web: true
  claude_model: claude-sonnet-4-6
  max_phase_budget_usd: 25.0

auto_approve: true
---

# alzheimer-bbb-pericyte-vascular — pericyte SMAD3 / astrocyte VEGFA BBB collapse (IDEATE d2)

## Source materials

Transcribed faithfully from IDEATE direction **d2**
(`workspaces/ideate-alzheimer-mechanism-20260607-044616/000_ideate/directions.json`).
Anchor and supporting literature lifted from the direction's `cites`:

- *Gliovascular transcriptional perturbations in Alzheimer's disease
  reveal molecular mechanisms of blood brain barrier dysfunction.*
  Nature Communications (2024). (Pericyte SMAD3 up, astrocyte VEGFA
  down; blood SMAD3 associates with AD neuroimaging.)
- *A single nuclear transcriptomic characterisation of mechanisms
  responsible for impaired angiogenesis and blood-brain barrier function
  in Alzheimer's disease.* (2024). (HIF1A/VEGFA endothelial failure in
  GSE157827.)
- Lau S.F. et al. *Single-nucleus transcriptome analysis.* (2020). GEO
  GSE157827 (PFC; pericytes + endothelial cells + astrocytes; n=12 AD +
  9 control).
- *Cell type-specific roles of APOE4 in Alzheimer disease.* Nature
  Reviews Neuroscience (2024).
- Gabitto M.I., Travaglini K.J. et al. *Integrated multimodal cell atlas
  of Alzheimer's disease.* Nature Neuroscience (2024). SEA-AD.

## Why this matters for OmiCairn

BBB and vascular dysfunction are increasingly viewed as early, possibly
upstream events in AD, yet no prior lab run has touched vascular cells.
This case asks OmiCairn to test the named gliovascular axis — pericyte SMAD3
up, astrocyte VEGFA down — directly in open human snRNA-seq, and to ask
the sharper causal-ordering question: does the vascular programme move
before amyloid? The answer is informative either way, and the case
deliberately stresses honest handling of missing Braak and amyloid
covariates and the limits of inferring temporal precedence from
cross-sectional Braak ordering.

OmiCairn challenges this exercises:

1. **Braak-stratified contrast** within the pericyte compartment, with a
   paired astrocyte VEGFA readout.
2. **Per-donor pseudobulk DE** rather than per-cell DE.
3. **Cross-sectional precedence inference** (Braak-stage offset between
   SMAD3 rise and amyloid threshold) with explicit uncertainty.
4. **Honest covariate auditing** — H1 needs Braak; H2 needs amyloid
   neuropathology; the agent must mark them untestable and fall back
   rather than impute.

## Critical correctness rules (EXECUTE must obey)

- Use REAL fetched public data only. Prefer openly-downloadable GEO
  supplementary files. If blocked/missing → say so in Limitations and
  fall back; NEVER simulate.
- Report effect size + p-value + 95% CI together for every claim.
- Audit covariate availability (Braak/tau, amyloid plaque density)
  BEFORE asserting H1/H2 results; if a covariate is missing, mark the
  hypothesis untestable and fall back to a descriptive analysis.
- Precedence (H2) is inferred from cross-sectional Braak ordering, not
  longitudinal data — state this limitation explicitly; do not overclaim
  causation.
- Never fabricate accessions. SEA-AD is a candidate — verify at fetch
  time; report blocked if unavailable.

## Out of scope (for this iteration)

- Raw FASTQ re-alignment.
- Plasma / blood SMAD3 protein measurement.
- iPSC pericyte or mouse BBB validation.
