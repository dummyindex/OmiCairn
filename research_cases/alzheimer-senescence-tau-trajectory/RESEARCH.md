---
research_mode: DISCOVERY
domain: neurodegeneration_scRNAseq
topic_slug: alzheimer-senescence-tau-trajectory

research_question: |
  Direction d4 from the Alzheimer's-mechanism IDEATE run
  (workspaces/ideate-alzheimer-mechanism-20260606-153912/000_ideate).

  p21/SASP senescence pseudotime trajectory in tau-bearing
  entorhinal-cortex neurons: pseudotime mapping of the NFT-to-death
  pathway.

  Do neurons accumulating phospho-tau in EC layer II transition through
  a cellular senescence intermediate — defined by p21 upregulation,
  CDKN2A elevation, SASP gene expression (IL-6, CXCL10, MMP3), and
  cell-cycle gene re-expression — before death, and does this
  senescence signature predict adjacent microglial activation and
  trans-entorhinal tau spread to superior frontal gyrus using existing
  Braak-staged snRNA-seq data?

  The senescence–tau nexus is now well supported (Gaikwad 2024; Zhang
  2024) but has not been tested computationally on Braak-staged human
  snRNA-seq. The Leng 2021 atlas (EC + SFG across Braak stages) is
  ideally structured for a pseudotime trajectory within RORB+ neurons,
  and Grubman 2019 (EC) supplies an open second EC cohort. This OmiCairn
  run tests whether a p21/SASP senescence module is upregulated in
  vulnerable EC neurons before frank cell loss, and whether neuronal
  SASP tracks microglial activation in the same brains — potentially
  unifying Herrup's cell-cycle-re-entry theory with the tau-SASP
  literature.

  Hypotheses (pre-registered, lifted from d4):

    H1 (primary — p21/SASP senescence module in vulnerable EC neurons):
        In Braak stage II–III EC tissue, RORB+ excitatory neurons show
        a statistically significant upregulation of p21 (CDKN1A),
        CDKN2A, CXCL10, IL-6, and MMP3 relative to Braak stage 0–I
        RORB+ neurons, consistent with a SASP-secreting senescence
        state that precedes frank cell loss.
        Decision rule: per-donor pseudobulk DE within RORB+ EC neurons,
        Braak II–III vs Braak 0–I; report log2FC + Benjamini–Hochberg
        FDR + 95% bootstrap CI per gene, plus a senescence/SASP module
        score contrast. H1 passes if ≥3 of the 5 senescence/SASP genes
        (CDKN1A, CDKN2A, CXCL10, IL6, MMP3) are up (FDR<0.05) with a
        positive module-score shift in early/mid-Braak RORB+ neurons.

    H2 (secondary — neuronal SASP tracks microglial activation):
        The SASP gene expression level in RORB+ neurons at Braak II–III
        correlates positively (r > 0.4) with the DAM/activated
        microglial cluster proportion in the same brain sample,
        supporting that neuronal SASP drives microglial activation and
        propagates the inflammatory feed-forward loop.
        Decision rule: per-donor neuronal SASP module score vs per-donor
        DAM/activated-microglia proportion (same sample); report
        Pearson/Spearman r + p-value + 95% CI. H2 passes if r > 0.4 and
        significant (p<0.05, 95% CI excluding 0).

  IMPORTANT — covariate / cohort dependency: H1 requires Braak-stage
  metadata and a resolvable RORB+ EC excitatory-neuron population in the
  same donors; H2 additionally requires microglia in the same samples to
  estimate DAM/activated proportion. EXECUTE MUST first verify these are
  present in the fetched objects. If Braak staging is missing, H1 cannot
  be stage-contrasted — report that honestly and fall back to a
  pseudotime-ordered senescence-module description within RORB+ neurons
  (no stage labels). If a sample lacks microglia (e.g. a neuron-enriched
  object), H2 cannot be tested in that cohort — say so. Do NOT impute
  Braak stage, cell-type identity, or cross-sample matches.

  A negative or partial result is fully informative. If RORB+ EC neurons
  do NOT upregulate the senescence/SASP module before cell loss, or
  neuronal SASP does not track microglial activation, that must be the
  headline rather than re-tuning the senescence gene set or trajectory
  until H1/H2 pass.

data:
  location: ./research_cases/alzheimer-senescence-tau-trajectory/data
  shared_cache: ./datasets
  description: |
    **No pre-staged raw data on disk.** Every dataset below is publicly
    deposited and MUST be fetched at run time by EXECUTE into the shared
    cache `./datasets/<accession>/`. EXECUTE MUST use REAL fetched
    public data — prefer openly-downloadable GEO supplementary files
    (processed h5ad / matrix + barcodes + features). If a dataset or a
    required covariate (Braak stage, RORB+ neuron identity, microglia)
    is blocked, missing, or unparseable, report that honestly in
    `analysis.md § Limitations` and fall back to the remaining open
    data — NEVER simulate or fabricate. Acceptable reasons to defer a
    fetch: (1) HTTP 404/410 / accession removed; (2) sha256
    verification fails; (3) `budgets.allow_web` is false. Size alone is
    NOT acceptable.

    All reported effects MUST carry effect size + p-value + 95% CI
    together. A p-value alone is not a result.

    **Primary OPEN datasets:**

      - **Grubman 2019 (entorhinal cortex).**
        GEO **GSE138852** — snRNA-seq of entorhinal cortex from AD and
        control postmortem brains (includes neurons, microglia,
        astrocytes). Openly downloadable at NCBI GEO. Supplies an open
        EC cohort for the RORB+ neuron senescence analysis and matched
        microglia for H2.
          https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE138852
        Target path: ./datasets/GSE138852/

      - **Leng 2021 (EC + SFG, Braak-staged).**
        GEO **GSE147528** — snRNA-seq of entorhinal cortex and superior
        frontal gyrus across Braak stages (Leng et al., Nature
        Neuroscience 2021). The Braak-staged structure is the core
        resource for the H1 stage contrast (Braak II–III vs 0–I) and
        the trans-entorhinal EC→SFG spread question.
          https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE147528
        Target path: ./datasets/GSE147528/
        NOTE: H1 needs Braak stage + a resolvable RORB+ EC excitatory
        population. EXECUTE must confirm these before testing H1; if
        Braak staging is absent, report honestly and fall back to a
        pseudotime-ordered description (see research_question above).

    **Secondary OPEN dataset (candidate — verify):**

      - **SEA-AD (Gabitto 2024).** Open multimodal AD atlas spanning the
        full severity spectrum, with neurons and microglia across the
        AD continuum and quantitative neuropathology — strengthens the
        stage contrast and the neuron–microglia link if the primary
        cohorts are underpowered. candidate — EXECUTE to verify open
        availability; report blocked if unavailable, do not fabricate.
        Allen Brain Map portal / AWS open data registry / Synapse
        syn64410371.
          https://portal.brain-map.org/explore/seattle-alzheimers-disease
        Target path: ./datasets/seaad_syn64410371/

    **Shared dataset cache convention:** every accession is downloaded
    into `./datasets/<accession>/` with a sidecar `SOURCE.txt` recording
    URL, fetch timestamp, sha256, byte count, and the parsing tool used
    (scanpy.read_h5ad, anndata.read, scanpy.read_mtx, etc.). Subsequent
    runs check the cache first and skip re-downloads.

  cohorts: [grubman_2019_EC, leng_2021_EC_SFG, seaad_2024_multiregion]
  modalities: [snRNA_seq_10x]

success_criteria:
  primary_metric: rorb_neuron_senescence_sasp_module_shift
  baseline: braak_0_1_rorb_neuron_baseline
  pre_registration: required
  honest_validation: required
  notes: |
    Primary success = H1 module shift reaches FDR<0.05 with the correct
    sign (≥3 of CDKN1A/CDKN2A/CXCL10/IL6/MMP3 up, positive senescence
    module-score shift) in Braak II–III vs 0–I RORB+ EC neurons in at
    least one Braak-staged cohort. If no fetched cohort carries Braak
    staging, H1 cannot be stage-contrasted — report that as the headline
    limitation and fall back to a pseudotime-ordered senescence-module
    description, not a failure to fix by imputing stage.

    H2 success = neuronal SASP module score shows a positive correlation
    (r > 0.4) with DAM/activated-microglia proportion in the same
    samples (p<0.05, 95% CI excluding 0). If samples lack microglia or
    sample matching is impossible, report descriptively and mark H2
    untestable.

    All effects reported as effect size + p-value + 95% CI together.
    Partial results (e.g. senescence module up but no microglial link)
    must be stated plainly in `analysis.md` and `negative_results.md`, not
    framed as success.

figures_planned:
  - id: F1
    title: "EC neuronal taxonomy and the RORB+ vulnerable population"
    panels:
      - "UMAP of EC nuclei colored by cell type; RORB+ excitatory-neuron population labeled"
      - "Marker dot plot: RORB and EC layer-II identity markers across neuronal subclusters"
      - "Per-donor RORB+ neuron abundance by Braak stage (cell-loss readout)"
  - id: F2
    title: "p21/SASP senescence module in RORB+ EC neurons (H1)"
    panels:
      - "Per-donor pseudobulk forest plot: log2FC + 95% CI for CDKN1A, CDKN2A, CXCL10, IL6, MMP3 (Braak II–III vs 0–I)"
      - "Senescence/SASP module score across Braak stages in RORB+ neurons"
      - "Pseudotime trajectory of RORB+ neurons with senescence-module score overlaid"
  - id: F3
    title: "Neuronal SASP vs microglial activation (H2)"
    panels:
      - "Per-donor neuronal SASP module score vs DAM/activated-microglia proportion; r + 95% CI"
      - "DAM/activated-microglia proportion across Braak stages"
      - "EC→SFG comparison of the senescence module (trans-entorhinal spread context)"
  - id: F4
    title: "Covariate-availability and honest-failure panel"
    panels:
      - "Covariate audit table: Braak stage / RORB+ identity / microglia presence per fetched cohort"
      - "Sensitivity to the senescence/SASP gene-set definition"
      - "Cluster-resolution sensitivity of the RORB+ neuron split"

figures_not_in_scope:
  - "Raw FASTQ re-alignment (processed objects suffice)"
  - "Direct phospho-tau immunostaining / proteomics — wet-lab"
  - "Senolytic drug-response modeling — wet-lab"

budgets:
  max_phase_wall_minutes: 240
  max_total_wall_minutes: 360
  allow_web: true
  claude_model: claude-sonnet-4-6
  max_phase_budget_usd: 25.0

auto_approve: true
---

# alzheimer-senescence-tau-trajectory — p21/SASP senescence in EC RORB+ neurons (IDEATE d4)

## Source materials

Transcribed faithfully from IDEATE direction **d4**
(`workspaces/ideate-alzheimer-mechanism-20260606-153912/000_ideate/directions.json`).
Anchor and supporting literature lifted from the direction's `cites`:

- Gaikwad S. et al. *Senescence, brain inflammation, and oligomeric tau
  drive cognitive decline in Alzheimer's disease.* Alzheimer's &
  Dementia (2024). (p53/p21-driven senescence; tau oligomers→SASP loop.)
- Zhang P. et al. *Senescent brain cell types in Alzheimer's disease:
  Pathological mechanisms and therapeutic opportunities.*
  Neurotherapeutics (2024).
- Leng K. et al. *Molecular characterization of selectively vulnerable
  neurons in Alzheimer's disease.* Nature Neuroscience (2021). EC + SFG
  across Braak stages; RORB+ EC layer-II neurons selectively vulnerable.
  GEO GSE147528.
- Grubman A. et al. *A single-cell atlas of entorhinal cortex from
  individuals with Alzheimer's disease.* Nature Neuroscience (2019).
  GEO GSE138852 (open EC snRNA-seq).
- Gabitto M.I. et al. *SEA-AD: A multimodal cellular atlas and resource
  for Alzheimer's disease.* Nature Aging (2024).
- Karl Herrup position (via ALZFORUM): neuronal cell-cycle re-entry and
  age-related injury are early events in vulnerable neurons.

## Why this matters for OmiCairn

If tau-bearing EC neurons pass through a senescence intermediate before
death, that would unify Herrup's cell-cycle-re-entry theory with the
tau-SASP literature and nominate senescence as a therapeutic node
upstream of cell loss. This case asks OmiCairn to test that directly in
open, Braak-staged human EC snRNA-seq, and to ask the sharper question:
does neuronal SASP track microglial activation in the same brains? The
answer is informative either way, and the case stresses honest handling
of missing Braak staging and missing microglia.

OmiCairn challenges this exercises:

1. **Stage-contrasted pseudobulk DE** within a resolved RORB+ EC
   neuron population (Braak II–III vs 0–I).
2. **Pseudotime trajectory** ordering of the senescence/SASP module.
3. **Cross-cell-type, same-sample correlation** (neuronal SASP vs
   DAM/activated-microglia proportion) for H2.
4. **Honest covariate auditing** — H1/H2 are untestable if Braak
   staging, RORB+ identity, or microglia are absent; the agent must say
   so rather than impute.

## Critical correctness rules (EXECUTE must obey)

- Use REAL fetched public data only. Prefer openly-downloadable GEO
  supplementary files. If blocked/missing → say so in Limitations and
  fall back; NEVER simulate.
- Report effect size + p-value + 95% CI together for every claim.
- Audit covariate availability (Braak stage, RORB+ identity, microglia
  presence) BEFORE asserting H1/H2; if a covariate is missing, mark the
  hypothesis untestable and fall back to a descriptive / pseudotime
  analysis. Do not impute Braak stage or cross-sample matches.

## Out of scope (for this iteration)

- Raw FASTQ re-alignment.
- Direct phospho-tau immunostaining or proteomics (transcriptional
  surrogate of the senescence/SASP state only).
- Senolytic drug-response modeling.
