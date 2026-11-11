---
research_mode: DISCOVERY
domain: neurodegeneration_scRNAseq
topic_slug: alzheimer-gwas-snatac-celltype

research_question: |
  Direction d3 from the Alzheimer's-mechanism IDEATE run
  (workspaces/ideate-alzheimer-mechanism-20260606-153912/000_ideate).

  Cell-type-resolved functional dissection of AD GWAS variants in
  microglial vs neuronal cis-regulatory elements via snATAC-seq.

  Which of the 75+ AD GWAS loci harbor causal variants within
  microglia-specific enhancers controlled by SPI1, ELF2, or RUNX1
  (vs. excitatory neuron-specific circuits), what are the effector
  genes and their direction of effect, and do these cell-type-specific
  regulatory circuits operate differently across AD disease stages in
  existing multi-omic atlases?

  AD GWAS hits are enriched in microglial enhancers (Tsai/Kellis Cell
  2023, SPI1/ELF2/RUNX1), but most of the 75+ loci remain functionally
  uncharacterized at cell-type resolution, and neuronal regulatory
  circuits may mediate risk through synaptic gene programs
  independently of microglia. This OmiCairn run re-analyses open human
  snATAC-seq (the Morabito 2021 multiome ATAC arm) against AD GWAS
  loci to ask which loci fall in microglia- vs neuron-specific
  cis-regulatory elements, identify candidate effector genes and the
  direction of effect, and test whether the microglial regulatory
  circuits are stage-dependent.

  Hypotheses (pre-registered, lifted from d3):

    H1 (primary — SPI1/microglial enhancer dominance of AD GWAS loci):
        SPI1 (PU.1) binding motif accessibility in microglia
        snATAC-seq is the single strongest predictor of causal AD GWAS
        variant activity, with at least 15 of the top 75 GWAS loci
        having their strongest chromatin-accessibility QTL (or peak
        overlap / motif disruption signal) in microglia rather than
        neurons, astrocytes, or oligodendrocytes.
        Decision rule: for each AD GWAS lead/credible-set variant,
        compute cell-type-resolved snATAC peak overlap and SPI1 motif
        accessibility per cell type; rank cell types per locus; report
        the count of loci whose strongest microglial signal exceeds
        the other cell types, with effect size + p-value + 95% CI for
        the microglia-vs-other enrichment. H1 passes if ≥15 of the top
        75 loci are microglia-dominant AND SPI1 motif accessibility is
        the top predictor (significant at p<0.05) of microglial
        variant activity.

    H2 (secondary — DAM-stage timing of microglial GWAS circuits):
        Microglial GWAS-variant regulatory circuits are most active at
        the DAM stage (mid-disease), not at homeostatic or late-disease
        stages, suggesting the therapeutic window for GWAS-linked
        microglial targets is the early-to-mid Braak period.
        Decision rule: stratify microglia by activation state
        (homeostatic / DAM / late) using marker programs and/or Braak
        stage; compute GWAS-variant-overlapping peak accessibility per
        stage; report the stage contrast (DAM vs homeostatic and DAM
        vs late) as effect size + p-value + 95% CI. H2 passes if peak
        accessibility at GWAS-overlapping microglial loci is
        significantly higher at the DAM/mid stage than at both
        homeostatic and late stages (p<0.05, 95% CI excluding 0).

  IMPORTANT — feasibility caveat (snATAC is heavy): snATAC peak-calling
  and peak-to-variant integration is substantially heavier than the
  RNA-only AD cases and depends on the snATAC arm being present and
  parseable in the fetched object, plus a usable AD GWAS variant list.
  EXECUTE MUST verify, BEFORE asserting H1/H2: (1) the snATAC fragment/
  peak matrix is fetchable and parseable; (2) a public AD GWAS
  lead/credible-set variant list is obtained (ADGC public summary
  statistics / published supplementary tables — never fabricated
  accessions or coordinates); (3) variant-to-peak overlap can actually
  be computed against a stated genome build. If peak calls cannot be
  computed, the variant overlap cannot be performed, or cell-type
  labels are absent, EXECUTE MUST report that honestly in
  analysis.md § Limitations and fall back to whatever partial analysis
  is supportable (e.g. cell-type marker-gene accessibility, or the
  RNA arm only) rather than fabricating peak/variant overlaps. Do NOT
  impute peaks, QTLs, or genotypes.

  A negative or partial result is fully informative. If AD GWAS loci
  are NOT preferentially microglia-/SPI1-resolved, or the circuits are
  not stage-dependent, that must be the headline rather than re-tuning
  peak definitions or variant sets until H1/H2 pass.

data:
  location: ./research_cases/alzheimer-gwas-snatac-celltype/data
  shared_cache: ./datasets
  description: |
    **No pre-staged raw data on disk.** Every dataset below is publicly
    deposited and MUST be fetched at run time by EXECUTE into the shared
    cache `./datasets/<accession>/`. EXECUTE MUST use REAL fetched
    public data — prefer openly-downloadable GEO supplementary files
    (processed h5ad / ArchR / Signac objects, fragment files, peak
    matrices). If a dataset, the snATAC arm, the AD GWAS variant list,
    or a required cell-type label is blocked, missing, or unparseable,
    report that honestly in `analysis.md § Limitations` and fall back
    to the remaining open data — NEVER simulate or fabricate. Acceptable
    reasons to defer a fetch: (1) HTTP 404/410 / accession removed;
    (2) sha256 verification fails; (3) `budgets.allow_web` is false.
    Size alone is NOT acceptable.

    All reported effects MUST carry effect size + p-value + 95% CI
    together. A p-value alone is not a result.

    **Primary OPEN dataset:**

      - **Morabito 2021 (PFC multiome — has snATAC).**
        GEO **GSE174367** — snRNA-seq + snATAC-seq (multiome) of
        prefrontal cortex from AD and control donors. Openly
        downloadable at NCBI GEO. Use the **snATAC arm** for the
        cell-type-resolved cis-regulatory / peak–variant analysis;
        the snRNA arm provides matched cell-type labels.
          https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE174367
        Target path: ./datasets/GSE174367/
        NOTE: snATAC peak–variant integration is heavier than RNA-only
        analysis. EXECUTE must confirm the snATAC fragment/peak matrix
        is present and parseable before testing H1/H2; if peak calls or
        variant overlap cannot be computed, report honestly and fall
        back (see research_question above).

    **AD GWAS variant list (OPEN — required for the overlap):**

      - **ADGC public AD GWAS summary statistics / published
        lead-variant tables.** Obtain the top AD GWAS loci
        (lead/credible-set variants) from public ADGC resources
        (adgc.nih.gov) and/or the published supplementary tables of the
        Bellenguez/Nalls-class meta-analyses cited by IDEATE
        (e.g. the all-cause/vascular dementia meta-analysis and the
        multi-ancestry 56,241-individual meta-analysis). Record the
        exact source URL and genome build in SOURCE.txt. NEVER
        fabricate variant rsIDs or coordinates — if no public variant
        list can be fetched, H1/H2 are untestable; say so and fall back
        to a descriptive cell-type accessibility analysis.

    **Secondary OPEN dataset (candidate — verify):**

      - **SEA-AD snATAC (Gabitto 2024).** Open multimodal AD atlas
        spanning the full severity spectrum, including snATAC-seq, with
        APOE genotype and quantitative neuropathology — the strongest
        source for the H2 stage contrast if GSE174367's snATAC arm is
        insufficient. candidate — EXECUTE to verify open availability;
        report blocked if unavailable, do not fabricate. Allen Brain
        Map portal / AWS open data registry / Synapse syn64410371.
          https://portal.brain-map.org/explore/seattle-alzheimers-disease
        Target path: ./datasets/seaad_syn64410371/

    **Optional tertiary (candidate — verify):**

      - **Science Advances 2024 three-region multiome (Li 2024).**
        PFC/EC/hippocampus snRNA + snATAC in sporadic early-onset AD
        (PMC11326379). candidate — EXECUTE to verify open availability;
        report blocked if unavailable, do not fabricate.
        Target path: ./datasets/sciadv_2024_multiregion/

      - **ENCODE SCREEN cCRE database** (public, encodeproject.org/SCREEN)
        — reference candidate cis-regulatory elements for annotating
        variant–peak overlaps. Use only if openly fetchable.
        Target path: ./datasets/encode_screen_ccre/

    **Shared dataset cache convention:** every accession is downloaded
    into `./datasets/<accession>/` with a sidecar `SOURCE.txt` recording
    URL, fetch timestamp, sha256, byte count, genome build, and the
    parsing tool used (snapatac2, ArchR, signac, scanpy.read_h5ad,
    anndata.read, scanpy.read_mtx, etc.). Subsequent runs check the
    cache first and skip re-downloads.

  cohorts: [morabito_2021_PFC, seaad_2024_multiregion, sciadv_2024_multiregion]
  modalities: [snATAC_seq_10x, snRNA_seq_10x_multiome]

success_criteria:
  primary_metric: microglia_snatac_gwas_variant_enrichment
  baseline: celltype_resolved_peak_overlap_vs_neuron_astro_oligo
  pre_registration: required
  honest_validation: required
  notes: |
    Primary success = H1 reaches significance with the correct sign:
    ≥15 of the top 75 AD GWAS loci are microglia-dominant for snATAC
    peak overlap / SPI1 motif accessibility relative to neurons,
    astrocytes, and oligodendrocytes, AND SPI1 motif accessibility is
    the top predictor (p<0.05) of microglial variant activity, in at
    least one atlas with a parseable snATAC arm and a fetched public AD
    GWAS variant list. If the snATAC arm cannot be parsed, peak calls
    cannot be computed, or no public variant list is obtained, H1 is
    untestable — report that as the headline limitation, not a failure
    to fix by imputation.

    H2 success = GWAS-variant-overlapping microglial peak accessibility
    is significantly higher at the DAM/mid stage than at both
    homeostatic and late stages (p<0.05, 95% CI excluding 0). If
    activation-state / Braak-stage labels are missing, report the
    composition descriptively and mark H2 untestable.

    All effects reported as effect size + p-value + 95% CI together.
    Partial results (e.g. microglial enrichment present but no stage
    dependence) must be stated plainly in `analysis.md` and
    `negative_results.md`, not framed as success.

figures_planned:
  - id: F1
    title: "Cell-type-resolved chromatin landscape"
    panels:
      - "UMAP of snATAC nuclei colored by cell type (microglia, excitatory neuron, astrocyte, oligodendrocyte)"
      - "Pseudobulk accessibility tracks at canonical microglial (SPI1, CSF1R) vs neuronal (RBFOX3, SLC17A7) loci"
      - "Per-cell-type TF motif accessibility heatmap (SPI1/PU.1, ELF2, RUNX1)"
  - id: F2
    title: "AD GWAS variants in cell-type-specific cis-regulatory elements (H1)"
    panels:
      - "Per-locus heatmap: cell-type-resolved peak overlap / motif disruption for the top 75 AD GWAS loci"
      - "Count of microglia-dominant vs neuron-dominant loci with 95% CI"
      - "SPI1 motif accessibility as predictor of microglial variant activity: effect size + 95% CI"
  - id: F3
    title: "Stage dependence of microglial GWAS circuits (H2)"
    panels:
      - "GWAS-overlapping microglial peak accessibility across homeostatic / DAM / late stages"
      - "Stage-contrast forest plot (DAM vs homeostatic; DAM vs late) with 95% CI"
      - "Candidate effector-gene peak-to-gene links by stage"
  - id: F4
    title: "Feasibility and honest-failure panel"
    panels:
      - "Audit table: snATAC arm parseability / peak-call availability / GWAS variant list / genome build per fetched atlas"
      - "Variant-to-peak overlap coverage (loci computable vs blocked)"
      - "Sensitivity to peak-calling parameters and credible-set definition"

figures_not_in_scope:
  - "Raw FASTQ / fragment re-alignment (processed objects suffice)"
  - "Experimental QTL mapping / wet-lab reporter assays"
  - "De novo GWAS — uses published public AD GWAS loci only"

budgets:
  max_phase_wall_minutes: 240
  max_total_wall_minutes: 360
  allow_web: true
  claude_model: claude-sonnet-4-6
  max_phase_budget_usd: 25.0

auto_approve: true
---

# alzheimer-gwas-snatac-celltype — cell-type dissection of AD GWAS variants (IDEATE d3)

## Source materials

Transcribed faithfully from IDEATE direction **d3**
(`workspaces/ideate-alzheimer-mechanism-20260606-153912/000_ideate/directions.json`).
Anchor and supporting literature lifted from the direction's `cites`:

- Xiong X., James B.T., Boix C.A. et al. (Tsai LH, Kellis M). *Epigenomic
  dissection of Alzheimer's disease pinpoints causal variants and reveals
  epigenome erosion.* Cell (2023). (AD risk loci enriched in microglial
  enhancers for SPI1, ELF2, RUNX1.)
- Morabito S. et al. *Single-nucleus chromatin accessibility and
  transcriptomic characterization of Alzheimer's disease.* Nature
  Genetics (2021). GEO GSE174367 (snRNA + snATAC multiome, PFC).
- Fujita M. et al. *Multi-omic cell type-level inference of mechanisms
  underlying non-coding genetic variants for Alzheimer's disease.* (2024;
  PMC11710177).
- Nalls M.A. et al. *A genome-wide association meta-analysis of all-cause
  and vascular dementia.* Alzheimer's & Dementia (2024).
- Kunkle B.W. et al. (ADGC). *Multi-ancestry genome-wide meta-analysis of
  56,241 individuals identifies novel risk loci for Alzheimer's disease.*
  (2023).
- Li Q. et al. *Single-nucleus multiomics reveals the disrupted
  regulatory programs in three brain regions of sporadic early-onset
  Alzheimer's disease.* Science Advances (2024; PMC11326379).
- Gabitto M.I. et al. *SEA-AD: A multimodal cellular atlas and resource
  for Alzheimer's disease.* Nature Aging (2024).

## Why this matters for OmiCairn

AD GWAS has delivered 75+ loci, but the field's central mechanistic
question — which act through microglia vs neurons, via which effector
genes, and when during the disease — is largely unanswered at
cell-type resolution. This case asks OmiCairn to integrate open human
snATAC-seq with published AD GWAS variants and to resolve the
microglia-vs-neuron regulatory dichotomy directly. The answer is
informative either way, and the case deliberately stresses honest
handling of the heavier snATAC peak–variant integration.

OmiCairn challenges this exercises:

1. **snATAC peak-calling + peak-to-variant overlap** against a public
   AD GWAS variant list with a stated genome build.
2. **Cell-type-resolved TF-motif accessibility** (SPI1/PU.1, ELF2,
   RUNX1) per cell type.
3. **Stage-stratified accessibility** (homeostatic / DAM / late) for H2.
4. **Honest feasibility auditing** — H1/H2 are untestable if the snATAC
   arm cannot be parsed, peak calls fail, or no public variant list is
   fetched; the agent must say so rather than fabricate peaks/QTLs.

## Critical correctness rules (EXECUTE must obey)

- Use REAL fetched public data only. Prefer openly-downloadable GEO
  supplementary files. If blocked/missing → say so in Limitations and
  fall back; NEVER simulate.
- Never fabricate variant rsIDs, coordinates, peaks, or QTLs. The AD
  GWAS variant list MUST come from a fetched public source with the
  URL and genome build recorded.
- Report effect size + p-value + 95% CI together for every claim.
- Audit feasibility (snATAC parseability, peak calls, variant overlap,
  cell-type labels) BEFORE asserting H1/H2; if any is missing, mark the
  hypothesis untestable and fall back to a supportable descriptive
  analysis.

## Out of scope (for this iteration)

- Raw FASTQ / fragment re-alignment.
- De novo GWAS (uses published public AD loci only).
- Experimental QTL mapping or wet-lab reporter validation.
