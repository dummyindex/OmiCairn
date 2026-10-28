---
research_mode: DISCOVERY
domain: neurodegeneration_scRNAseq
topic_slug: alzheimer-apoe4-microglia-ldam

research_question: |
  Direction d2 from the Alzheimer's-mechanism IDEATE run
  (workspaces/ideate-alzheimer-mechanism-20260606-153912/000_ideate).

  Do APOE4 homozygous and heterozygous carriers in existing human
  snRNA-seq atlases show a statistically significant shift away from
  disease-associated microglia (DAM) and toward lipid-droplet-
  accumulating microglia (LDAM), and does this LDAM/DAM ratio correlate
  with excess tau pathology burden beyond what is explained by Aβ
  accumulation alone?

  APOE4 is the strongest genetic risk factor for AD; recent work
  converges on lipid metabolism and microglial dysfunction as its
  mechanistic core. TREM2-dependent DAM is the protective microglial
  state for plaque clearance; LDAM is a dysfunctional, proinflammatory
  state that fails at phagocytosis. This OmiCairn run re-analyses open
  human snRNA-seq by APOE genotype, computes DAM vs LDAM microglial
  composition, and tests whether the LDAM/DAM balance tracks tau
  burden (Braak / tangle count) independently of amyloid load.

  Hypotheses (pre-registered, lifted from d2):

    H1 (primary — genotype-driven LDAM/DAM marker shift):
        APOE4/4 carriers show significantly higher LDAM marker
        expression (LGALS3, CD9, GPNMB, NPC2) and lower DAM2 marker
        expression (TREM2, LPL, CST7) in microglia compared to
        APOE3/3 carriers at matched Braak stage, consistent with a
        lipid-driven microglial dysfunction.
        Decision rule: per-donor pseudobulk DE within the microglia
        compartment, APOE4/4 vs APOE3/3 stratified to matched Braak;
        report log2FC + Benjamini–Hochberg FDR + 95% bootstrap CI per
        gene. H1 passes if ≥2 of the 4 LDAM markers are up (FDR<0.05)
        AND ≥2 of the 3 DAM2 markers are down (FDR<0.05).

    H2 (secondary — LDAM/DAM ratio vs tau beyond amyloid):
        The ratio of LDAM to DAM microglial cells correlates
        positively with neurofibrillary-tangle burden (not just Aβ
        plaque count), suggesting APOE4-driven microglial dysfunction
        accelerates tau pathology independently of amyloid load.
        Decision rule: per-donor LDAM/DAM ratio regressed on a tau
        index (Braak stage and/or tangle count) with Aβ load as a
        covariate; report the partial regression coefficient for tau
        + p-value + 95% CI. H2 passes if the tau coefficient is
        positive and significant (p<0.05, 95% CI excluding 0) after
        adjusting for Aβ.

  IMPORTANT — covariate dependency: H1 requires APOE genotype metadata;
  H2 requires tau/Braak (and ideally Aβ) metadata in the same donors.
  EXECUTE MUST first verify these covariates are present in the fetched
  object. If APOE genotype is missing, H1 cannot be tested — report
  that honestly and fall back to a Braak-stratified DAM-vs-LDAM
  description without the genotype contrast. If tau/Aβ neuropathology
  is missing, H2 cannot be tested — say so and report the LDAM/DAM
  composition descriptively. Do NOT impute or fabricate genotype or
  neuropathology.

  A negative or partial result is fully informative. If APOE4 does not
  shift the LDAM/DAM balance, or the shift does not track tau, that
  must be the headline rather than re-tuning cluster definitions until
  H1/H2 pass.

data:
  location: ./research_cases/alzheimer-apoe4-microglia-ldam/data
  shared_cache: ./datasets
  description: |
    **No pre-staged raw data on disk.** Every dataset below is publicly
    deposited and MUST be fetched at run time by EXECUTE into the shared
    cache `./datasets/<accession>/`. EXECUTE MUST use REAL fetched
    public data — prefer openly-downloadable GEO supplementary files
    (processed h5ad / matrix + barcodes + features). If a dataset or a
    required covariate (APOE genotype, Braak/tau, Aβ) is blocked,
    missing, or unparseable, report that honestly in
    `analysis.md § Limitations` and fall back to the remaining open
    data — NEVER simulate or fabricate. Acceptable reasons to defer a
    fetch: (1) HTTP 404/410 / accession removed; (2) sha256
    verification fails; (3) `budgets.allow_web` is false. Size alone is
    NOT acceptable.

    All reported effects MUST carry effect size + p-value + 95% CI
    together. A p-value alone is not a result.

    **Primary OPEN dataset:**

      - **Morabito 2021 (PFC multiome reference).**
        GEO **GSE174367** — snRNA-seq + snATAC-seq (multiome) of
        prefrontal cortex from AD and control donors. Openly
        downloadable at NCBI GEO. Use the snRNA arm for the microglial
        composition analysis.
          https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE174367
        Target path: ./datasets/GSE174367/
        NOTE: APOE genotype + tau/Braak metadata must be present for
        H1/H2. EXECUTE must confirm these columns exist in the fetched
        object before testing H1/H2; if a covariate is absent, report
        honestly and fall back (see research_question above).

    **Secondary OPEN dataset (genotype + neuropathology):**

      - **SEA-AD (Gabitto 2024).** Open multimodal AD atlas spanning
        the full severity spectrum, with APOE genotype, snRNA-seq, and
        quantitative neuropathology (tangle / plaque counts). This is
        the strongest source for the H1 genotype contrast and the H2
        tau-vs-amyloid decomposition if GSE174367 lacks the required
        covariates. Allen Brain Map portal / AWS open data registry /
        Synapse syn64410371.
          https://portal.brain-map.org/explore/seattle-alzheimers-disease
        Target path: ./datasets/seaad_syn64410371/

    **Optional tertiary (open, human microglia subtypes):**

      - **Gerrits 2021** (Acta Neuropathologica) — human occipital
        cortex microglia subtypes (homeostatic / amyloid-DAM /
        tauopathy microglia). Check the paper's data-availability
        statement at fetch time; use only if openly downloadable.
        Target path: ./datasets/gerrits_2021/

    **Shared dataset cache convention:** every accession is downloaded
    into `./datasets/<accession>/` with a sidecar `SOURCE.txt` recording
    URL, fetch timestamp, sha256, byte count, and the parsing tool used
    (scanpy.read_h5ad, anndata.read, scanpy.read_mtx, etc.). Subsequent
    runs check the cache first and skip re-downloads.

  cohorts: [morabito_2021_PFC, seaad_2024_multiregion, gerrits_2021_occipital]
  modalities: [snRNA_seq_10x, snRNA_seq_10x_multiome]

success_criteria:
  primary_metric: apoe4_ldam_dam_marker_shift
  baseline: dam2_vs_ldam_canonical_marker_sets
  pre_registration: required
  honest_validation: required
  notes: |
    Primary success = H1 marker shift reaches FDR<0.05 with the correct
    sign (≥2 LDAM markers up, ≥2 DAM2 markers down) in APOE4/4 vs
    APOE3/3 microglia at matched Braak in at least one atlas with
    genotype metadata. If no fetched atlas carries APOE genotype, H1 is
    untestable — report that as the headline limitation, not a failure
    to fix by imputation.

    H2 success = LDAM/DAM ratio shows a positive, significant
    association with tau burden after adjusting for Aβ (p<0.05, 95% CI
    excluding 0). If neuropathology covariates are missing, report the
    LDAM/DAM composition descriptively and mark H2 untestable.

    All effects reported as effect size + p-value + 95% CI together.
    Partial results (e.g. genotype shift present but no tau link) must
    be stated plainly in `analysis.md` and `negative_results.md`, not
    framed as success.

figures_planned:
  - id: F1
    title: "Microglial taxonomy: DAM vs LDAM states"
    panels:
      - "UMAP of microglia colored by subcluster; DAM2 and LDAM states labeled"
      - "Marker dot plot: TREM2, LPL, CST7 (DAM2) and LGALS3, CD9, GPNMB, NPC2 (LDAM) across microglial subclusters"
      - "Per-donor microglial state composition stacked bars, grouped by APOE genotype"
  - id: F2
    title: "APOE4-driven LDAM/DAM marker shift (H1)"
    panels:
      - "Per-donor pseudobulk forest plot: log2FC + 95% CI for LDAM and DAM2 panels, APOE4/4 vs APOE3/3 at matched Braak"
      - "Volcano: microglial DE APOE4/4 vs APOE3/3 with the H1 panel annotated"
      - "Genotype-dose trend (3/3 vs 3/4 vs 4/4) for the LDAM and DAM2 module scores"
  - id: F3
    title: "LDAM/DAM ratio vs tau beyond amyloid (H2)"
    panels:
      - "Per-donor LDAM/DAM ratio vs Braak / tangle count; partial-regression coefficient + 95% CI"
      - "Same vs Aβ plaque load (to show the amyloid axis separately)"
      - "Partial-regression plot: tau coefficient after adjusting for Aβ"
  - id: F4
    title: "Covariate-availability and honest-failure panel"
    panels:
      - "Covariate audit table: APOE genotype / Braak / tangle / plaque availability per fetched atlas"
      - "Cluster-resolution sensitivity of the DAM vs LDAM split"
      - "Sensitivity to LDAM/DAM marker-set definition"

figures_not_in_scope:
  - "Raw FASTQ re-alignment (processed objects suffice)"
  - "snATAC regulatory analysis of the Morabito multiome ATAC arm (RNA arm only here; possible follow-up)"
  - "iPSC microglia / mouse lipid-droplet validation — wet-lab"

budgets:
  max_phase_wall_minutes: 240
  max_total_wall_minutes: 360
  allow_web: true
  claude_model: claude-sonnet-4-6
  max_phase_budget_usd: 25.0

auto_approve: true
---

# alzheimer-apoe4-microglia-ldam — APOE4 DAM→LDAM shift and tau (IDEATE d2)

## Source materials

Transcribed faithfully from IDEATE direction **d2**
(`workspaces/ideate-alzheimer-mechanism-20260606-153912/000_ideate/directions.json`).
Anchor and supporting literature lifted from the direction's `cites`:

- Morabito S. et al. *Single-nucleus chromatin accessibility and
  transcriptomic characterization of Alzheimer's disease.* Nature
  Genetics (2021). GEO GSE174367 (snRNA + snATAC multiome, PFC).
- Keren-Shaul H., Spinrad A., Weiner A. et al. *A unique microglia type
  associated with restricting development of Alzheimer's disease.*
  Cell (2017). (Original DAM definition.)
- Butovsky O., Weiner H.L. *Microglia, TREM2, and Neurodegeneration.*
  (2024).
- Gerrits E. et al. *Distinct amyloid-β and tau-associated microglia
  profiles in Alzheimer's disease.* Acta Neuropathologica (2021).
- Gabitto M.I. et al. *SEA-AD: A multimodal cellular atlas and resource
  for Alzheimer's disease.* Nature Aging (2024). OPEN — Synapse
  syn64410371.
- *APOE4-driven lipid metabolic dysregulation in Alzheimer's disease*
  (2025); APOE4 NIA/ADSP Consortium Working Group (2024).

## Why this matters for OmiCairn

APOE4 is the dominant common-variant risk factor for AD, and the
field's most mechanistically promising hypothesis is that it acts
through microglial lipid handling — pushing protective DAM toward
dysfunctional, lipid-laden LDAM. This case asks OmiCairn to test that
shift directly in open human snRNA-seq and to ask the sharper question:
does the LDAM/DAM imbalance track tau beyond amyloid? The answer is
informative either way, and the case deliberately stresses honest
handling of missing covariates.

OmiCairn challenges this exercises:

1. **Genotype-stratified, Braak-matched contrast** within the
   microglial compartment.
2. **Per-donor pseudobulk DE** rather than per-cell DE.
3. **Covariate-conditioned regression** (tau adjusted for Aβ) for H2.
4. **Honest covariate auditing** — H1/H2 are untestable if APOE
   genotype or neuropathology metadata are absent; the agent must say
   so rather than impute.

## Critical correctness rules (EXECUTE must obey)

- Use REAL fetched public data only. Prefer openly-downloadable GEO
  supplementary files. If blocked/missing → say so in Limitations and
  fall back; NEVER simulate.
- Report effect size + p-value + 95% CI together for every claim.
- Audit covariate availability (APOE genotype, Braak/tau, Aβ) BEFORE
  asserting H1/H2 results; if a covariate is missing, mark the
  hypothesis untestable and fall back to a descriptive analysis.

## Out of scope (for this iteration)

- Raw FASTQ re-alignment.
- snATAC / multiome regulatory analysis of the ATAC arm (RNA only).
- iPSC microglia or mouse lipid-droplet validation.
- Direct lipidomics (transcriptional surrogate of lipid state only).
