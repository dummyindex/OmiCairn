---
research_mode: DISCOVERY
domain: neurodegeneration_scRNAseq
topic_slug: alzheimer-sst-interneuron-ei

research_question: |
  Direction d5 from the second Alzheimer's-mechanism IDEATE run
  (workspaces/ideate-alzheimer-mechanism-20260607-044616/000_ideate).

  Is the loss of somatostatin-positive (SST+) inhibitory interneurons in
  hippocampus/limbic cortex the cellular event most tightly coupled to
  the transition from presymptomatic amyloid accumulation to active tau
  spread and cognitive decline, and can this loss be quantified as a
  Braak-stage-ordered pseudobulk signal in open human snRNA-seq data?

  The lab's alzheimer-rorb-ec-vulnerability run tested excitatory RORB+
  neurons in entorhinal cortex and found the ISR trajectory refuted.
  This direction is mechanistically distinct: it focuses on INHIBITORY
  interneurons (SST+ subtype) and the E/I-imbalance mechanism (SST+ loss
  -> disinhibition / hyperexcitability -> excess amyloid release -> tau
  spread). The Mathys 2024 Nature multiregion paper identified SST+
  inhibitory neuron loss as the EARLIEST and most region-universal
  cell-type change across all six AD brain regions, preceding even
  microglial changes; the "Cerebral hyperactivation" Brain Communications
  2024 paper showed Aβ damages inhibitory neurons and that hippocampal
  hyperactivity drives further amyloid release (a vicious cycle); the
  Murdock 2024 GENUS paper linked VIP interneuron function to glymphatic
  amyloid clearance. This OmiCairn run quantifies SST+ interneuron depletion
  as a Braak-stage-ordered pseudobulk signal in open human snRNA-seq.

  Hypotheses (pre-registered, lifted from d5):

    H1 (primary — monotone SST+ depletion with Braak):
        SST+ inhibitory neuron abundance (fraction of total
        excitatory+inhibitory neurons) shows a significant monotone
        decrease with Braak stage in hippocampus/limbic cortex
        (Jonckheere-Terpstra test, p < 0.05), with the effect size
        (Cliff's delta) exceeding that of any other inhibitory subtype.
        Decision rule: per-donor SST+ fraction computed within the
        neuronal compartment; Jonckheere-Terpstra trend test across
        ordered Braak stages; report the trend p-value + Cliff's delta
        (lowest vs highest Braak bin) + 95% bootstrap CI, and the same
        Cliff's delta for every other inhibitory subtype (PVALB, VIP,
        LAMP5). H1 passes if the SST+ trend is significant (p<0.05) AND
        the SST+ Cliff's delta (with 95% CI excluding 0) is the most
        negative of all inhibitory subtypes.

    H2 (secondary — SST+ loss precedes excitatory loss):
        SST+ depletion at Braak I-II precedes measurable loss of
        excitatory neuron subtypes in the same donors, supporting
        interneuron loss as an upstream rather than downstream event.
        Decision rule: in the same donor series, locate the Braak stage
        at which SST+ fraction first drops significantly below Braak 0
        baseline and the Braak stage at which any excitatory subtype
        fraction first does so; report the Braak-stage offset with a
        bootstrap 95% CI. H2 passes if the SST+ drop stage is at least
        one Braak stage earlier than the earliest excitatory-subtype
        drop stage and the 95% CI of the offset excludes 0.

  IMPORTANT — covariate dependency: H1 requires Braak/tau staging and
  reliable inhibitory-subtype annotation (or markers to derive it: SST,
  PVALB, VIP, LAMP5); H2 additionally requires excitatory-subtype
  resolution in the same donors. EXECUTE MUST first verify these are
  present. If Braak staging is missing, H1 cannot be tested as specified
  — report that honestly and fall back to an AD-vs-control SST+ fraction
  contrast without Braak ordering. If inhibitory subtypes cannot be
  resolved, report total-inhibitory and SST-marker descriptive results
  and mark the subtype-specificity claim untestable. Do NOT impute or
  fabricate Braak stage or cell-type labels.

  A negative or partial result is fully informative. If SST+ neurons do
  not deplete monotonically, or their loss does not precede excitatory
  loss, that must be the headline rather than re-tuning cluster
  definitions until H1/H2 pass.

data:
  location: ./research_cases/alzheimer-sst-interneuron-ei/data
  shared_cache: ./datasets
  description: |
    **No pre-staged raw data on disk.** Every dataset below is publicly
    deposited and MUST be fetched at run time by EXECUTE into the shared
    cache `./datasets/<accession>/`. EXECUTE MUST use REAL fetched
    public data — prefer openly-downloadable GEO supplementary files
    (processed h5ad / matrix + barcodes + features). If a dataset or a
    required covariate (Braak/tau, inhibitory-subtype annotation) is
    blocked, missing, or unparseable, report that honestly in
    `analysis.md § Limitations` and fall back to the remaining open data
    — NEVER simulate or fabricate. Acceptable reasons to defer a fetch:
    (1) HTTP 404/410 / accession removed; (2) sha256 verification fails;
    (3) `budgets.allow_web` is false. Size alone is NOT acceptable.

    All reported effects MUST carry effect size + p-value + 95% CI
    together. A p-value alone is not a result.

    **Primary OPEN dataset (candidate accession — verify):**

      - **Grubman 2019 (entorhinal cortex snRNA-seq).**
        GEO **GSE138852** — snRNA-seq of entorhinal cortex from 6 AD + 6
        control individuals; the entorhinal/limbic cortex is the first
        region to develop tau pathology and carries SST+ inhibitory
        interneurons. Candidate accession — EXECUTE to verify; report
        blocked if unavailable, do not fabricate. The entorhinal cortex
        is limbic but not hippocampus proper; treat the H1/H2 region as
        hippocampus/limbic cortex and state the region honestly.
          https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE138852
        Target path: ./datasets/GSE138852/
        NOTE: H1 needs Braak/tau staging + inhibitory-subtype resolution;
        H2 needs excitatory-subtype resolution. EXECUTE must confirm
        these exist before testing; if a covariate or annotation is
        absent, report honestly and fall back (see research_question
        above).

    **Secondary OPEN dataset (Braak-ordered atlas, candidate):**

      - **SEA-AD (Gabitto 2024).** Open multimodal AD atlas (MTG + DLPFC)
        spanning the full severity spectrum, with Braak-stage metadata
        and high-resolution inhibitory-subtype annotation (SST, PVALB,
        VIP, LAMP5). Strongest source for the H1 Braak-ordered SST+
        trend and the H2 precedence test if GSE138852 lacks the needed
        covariates. Candidate — EXECUTE to verify access; report blocked
        if unavailable, do not fabricate. Allen Brain Map portal / AWS
        open data registry / Synapse syn64410371.
          https://portal.brain-map.org/explore/seattle-alzheimers-disease
        Target path: ./datasets/seaad_syn64410371/

    **Shared dataset cache convention:** every accession is downloaded
    into `./datasets/<accession>/` with a sidecar `SOURCE.txt` recording
    URL, fetch timestamp, sha256, byte count, and the parsing tool used
    (scanpy.read_h5ad, anndata.read, scanpy.read_mtx, etc.). Subsequent
    runs check the cache first and skip re-downloads.

  cohorts: [grubman_2019_EC, seaad_2024_multiregion]
  modalities: [snRNA_seq_10x]

success_criteria:
  primary_metric: sst_interneuron_braak_depletion_trend
  baseline: inhibitory_subtype_canonical_marker_sets
  pre_registration: required
  honest_validation: required
  notes: |
    Primary success = H1 SST+ fraction shows a significant monotone
    decrease with Braak (Jonckheere-Terpstra p<0.05) AND the SST+ Cliff's
    delta (95% CI excluding 0) is the most negative of all inhibitory
    subtypes, in at least one open dataset with the required covariates.
    If no fetched dataset carries Braak staging, H1 is untestable as
    specified — report that as the headline limitation and fall back to
    AD-vs-control, not a failure to fix by imputation.

    H2 success = the Braak stage at which SST+ fraction first drops is at
    least one stage earlier than the earliest excitatory-subtype drop,
    with the bootstrap 95% CI of the offset excluding 0. If excitatory
    subtypes cannot be resolved, mark H2 untestable and report the SST+
    Braak trajectory descriptively without a precedence claim.

    All effects reported as effect size + p-value + 95% CI together.
    Partial results (e.g. SST+ loss present but precedence untestable)
    must be stated plainly in `analysis.md` and `negative_results.md`, not
    framed as success.

figures_planned:
  - id: F1
    title: "Neuronal taxonomy: inhibitory subtypes"
    panels:
      - "UMAP of neurons with SST, PVALB, VIP, LAMP5 inhibitory subtypes and excitatory subtypes labeled"
      - "Marker dot plot: SST, PVALB, VIP, LAMP5, plus pan-inhibitory (GAD1, GAD2) and excitatory (SLC17A7) markers"
      - "Per-donor inhibitory-subtype composition stacked bars, ordered by Braak stage"
  - id: F2
    title: "SST+ interneuron depletion with Braak (H1)"
    panels:
      - "Per-donor SST+ fraction across Braak stages with Jonckheere-Terpstra trend p-value"
      - "Cliff's delta (lowest vs highest Braak) + 95% CI for SST, PVALB, VIP, LAMP5"
      - "SST+ fraction vs Braak with bootstrap-CI band"
  - id: F3
    title: "SST+ loss precedes excitatory loss (H2)"
    panels:
      - "SST+ fraction trajectory across Braak with significant-drop stage marked"
      - "Earliest excitatory-subtype fraction trajectory with its drop stage marked"
      - "Braak-stage offset (SST+ drop minus excitatory drop) with bootstrap 95% CI"
  - id: F4
    title: "Covariate-availability and honest-failure panel"
    panels:
      - "Covariate audit table: Braak / inhibitory-subtype / excitatory-subtype availability per fetched dataset"
      - "Cluster-resolution sensitivity of the SST+ vs other-inhibitory split"
      - "Sensitivity to inhibitory-subtype marker-set definition"

figures_not_in_scope:
  - "Raw FASTQ re-alignment (processed objects suffice)"
  - "Electrophysiological E/I-balance measurement — wet-lab"
  - "Gamma-stimulation / GENUS intervention — wet-lab"

budgets:
  max_phase_wall_minutes: 240
  max_total_wall_minutes: 360
  allow_web: true
  claude_model: claude-sonnet-4-6
  max_phase_budget_usd: 25.0

auto_approve: true
---

# alzheimer-sst-interneuron-ei — SST+ interneuron loss and E/I imbalance (IDEATE d5)

## Source materials

Transcribed faithfully from IDEATE direction **d5**
(`workspaces/ideate-alzheimer-mechanism-20260607-044616/000_ideate/directions.json`).
Anchor and supporting literature lifted from the direction's `cites`:

- Mathys H. et al. *Single-cell multiregion dissection of Alzheimer's
  disease.* Nature (2024). (SST+ inhibitory neuron loss earliest,
  region-universal AD change; primary data Synapse / ACCESS-CONTROLLED.)
- NIH Research Matters summary of Mathys 2024 (SST+ loss earliest
  change).
- Gabitto M.I., Travaglini K.J. et al. *Integrated multimodal cell atlas
  of Alzheimer's disease.* Nature Neuroscience (2024). SEA-AD (OPEN).
- *Cerebral hyperactivation across the Alzheimer's disease pathological
  cascade.* Brain Communications (2024). (Aβ damages inhibitory neurons;
  E/I imbalance; vicious cycle.)
- *Multisensory gamma stimulation promotes glymphatic clearance of
  amyloid* (Murdock et al., Nature 2024). (VIP interneurons mediate
  vascular regulation.)
- Li-Huei Tsai (MIT): gamma oscillations, GENUS, glymphatic clearance.
- Keren-Shaul H. et al. *A unique microglia type associated with
  restricting development of Alzheimer's disease.* Cell (2017).
- Grubman A. et al. *A single-cell atlas of entorhinal cortex from
  individuals with Alzheimer's disease.* (2019). Candidate GEO
  GSE138852 (entorhinal cortex; OPEN candidate — verify accession).

## Why this matters for OmiCairn

SST+ inhibitory interneuron loss is reported as the earliest,
region-universal cell-type change in AD, central to the E/I-imbalance /
hyperexcitability mechanism that drives further amyloid release. The
lab's prior neuronal-vulnerability run probed excitatory RORB+ neurons;
this case turns to inhibitory interneurons. The strongest primary
evidence (Mathys 2024) sits on access-controlled Synapse, so this case
deliberately routes the analysis through OPEN data — Grubman 2019
(candidate GSE138852) and the open SEA-AD atlas — and asks OmiCairn to
quantify SST+ depletion as a Braak-ordered signal and test whether it
precedes excitatory loss. The answer is informative either way, and the
case stresses honest handling of a candidate accession, missing Braak
metadata, and limited subtype resolution.

OmiCairn challenges this exercises:

1. **Compositional Braak-trend analysis** (Jonckheere-Terpstra) on a
   single inhibitory subtype, benchmarked against all other inhibitory
   subtypes.
2. **Per-donor fraction estimation** within the neuronal compartment.
3. **Cross-sectional precedence inference** (SST+ vs excitatory drop) with
   explicit uncertainty.
4. **Honest covariate/annotation auditing** — H1 needs Braak +
   inhibitory subtypes; H2 needs excitatory subtypes; the agent must
   mark them untestable and fall back rather than impute.

## Critical correctness rules (EXECUTE must obey)

- Use REAL fetched public data only. Prefer openly-downloadable GEO
  supplementary files. If blocked/missing → say so in Limitations and
  fall back; NEVER simulate.
- Report effect size + p-value + 95% CI together for every claim.
- Audit covariate availability (Braak/tau) and cell-type annotation
  (inhibitory + excitatory subtypes) BEFORE asserting H1/H2 results; if
  missing, mark the hypothesis untestable and fall back to a descriptive
  analysis.
- Precedence (H2) is inferred from cross-sectional Braak ordering, not
  longitudinal data — state this limitation explicitly; do not overclaim
  causation.
- GSE138852 is entorhinal/limbic cortex, not hippocampus proper, and is
  a candidate accession — state the region honestly and verify the
  accession at fetch time. Never fabricate accessions; SEA-AD is also a
  candidate — report blocked if unavailable.

## Out of scope (for this iteration)

- Raw FASTQ re-alignment.
- Electrophysiological E/I-balance measurement.
- Gamma-stimulation / GENUS intervention.
