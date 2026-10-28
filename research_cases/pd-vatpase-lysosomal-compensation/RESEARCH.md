---
research_mode: CONFIRMATORY
domain: parkinsons_disease_transcriptomics
topic_slug: pd-vatpase-lysosomal-compensation

research_question: |
  Confirmatory replication of a self-generated OmiCairn finding.
  In a prior OmiCairn run on LRRK2 G2019S isogenic iPSC-derived dopaminergic
  (iPSC-DA) neurons (NanoString Human Neuropathology panel), nine
  CLEAR-network V-ATPase subunit genes — ATP6V0C, ATP6V0D1, ATP6V0E1,
  ATP6V0E2, ATP6V1A, ATP6V1D, ATP6V1E1, ATP6V1G2, ATP6V1H — were
  PREDOMINANTLY UPregulated in LRRK2 G2019S vs isogenic corrected
  controls (8/9 up at the donor level, mean log2FC = +0.485), and the
  direction was not comparison-specific (also +0.452, 7/9 up vs an
  independent healthy control). We interpret this as a COMPENSATORY
  transcriptional response: failing lysosomal acidification in
  PD-genotype DA neurons drives TFEB/CLEAR-network upregulation of the
  V-ATPase proton pump. The original cohort was small (NanoString,
  donor-level permutation power-limited), so the direction is a
  SUPPORTED but under-powered lab-KB hypothesis, not an established fact.

  This run PRE-REGISTERS THE OBSERVED DIRECTION (V-ATPase UP) and tests
  it in INDEPENDENT cohorts and modalities. Cohorts: (1) GSE167335 —
  the LRRK2 G2019S isogenic iPSC-DA NanoString panel (independent
  replication of the exact original modality, where the panel covers the
  V-ATPase subunits it measures); (2) ArrayExpress E-MTAB-7303 (Lang
  2020) — iPSC-DA neurons from GBA-N370S PD patients vs controls vs
  idiopathic PD, bulk + single-cell RNA-seq, the GBA1-mutant arm; and
  (3) Smajić GSE157783 — postmortem human substantia-nigra/midbrain
  snRNA-seq (idiopathic PD vs control), the postmortem in-vivo arm and
  the blind held-out validation cohort. This pre-registers a clear
  honesty guard: we are testing — NOT assuming — that the compensatory
  UP-direction replicates; a clean reversal or null in independent data
  is the headline.

  Hypotheses (pre-registered — directions are TESTED, not assumed):

    H1 (primary — independent LRRK2 replication of V-ATPase UP):
        In GSE167335 (LRRK2 G2019S isogenic iPSC-DA), the 9 measured
        CLEAR-network V-ATPase subunits are UPregulated in LRRK2 G2019S
        relative to isogenic corrected controls, replicating the prior
        observed +0.485 mean log2FC direction.
        Decision rule: donor-level (NOT replicate-level — collapse
        differentiation replicates to donor means to avoid
        pseudo-replication, exactly as the prior finding required) mean
        log2FC across the V-ATPase set, with a donor-label permutation
        test (report the minimum achievable permutation p given n) +
        per-gene 95% bootstrap CI. H1 REPLICATES if the mean direction
        is positive AND ≥6/9 subunits are individually up with the
        donor-level permutation p at its achievable floor. A reversal
        (net down) or a null is reported verbatim as failure-to-replicate.

    H2 (secondary — GBA1-mutant direction in an independent modality):
        The compensatory V-ATPase UP-direction generalises from LRRK2
        to the OTHER major lysosomal-membrane PD gene, GBA1. In
        E-MTAB-7303 (GBA-N370S iPSC-DA vs control), the V-ATPase /
        CLEAR-network module is UPregulated in GBA-N370S DA neurons.
        Decision rule: per-sample pseudobulk (single-cell arm) and bulk
        DE for the V-ATPase module; report module-level effect size +
        FDR + 95% CI. H2 supports the compensation model if GBA-N370S
        shows positive module shift (FDR<0.05, CI excluding 0). The
        competing model (V-ATPase DOWN with failing lysosome) is
        equally reportable; do NOT tune toward UP.

    H3 (secondary — postmortem-SN direction + held-out blind validation):
        A V-ATPase / CLEAR-network module score, LOCKED from the
        iPSC-DA arms (the 9-gene set + any weights), is scored WITHOUT
        refitting on the held-out Smajić GSE157783 postmortem SN/midbrain
        atlas (idiopathic PD vs control), restricted to the dopaminergic
        compartment. Report the PD-vs-control module effect + 95% CI and,
        as a classification readout, AUC + 1000× bootstrap CI for
        separating PD from control donors on the module score. The lock
        file is frozen before touching GSE157783. A null/opposite
        postmortem direction is informative (iPSC compensation may not
        survive to end-stage postmortem tissue) and is reported as such.

    H4 (secondary — DA-neuron selectivity):
        The V-ATPase compensatory shift is SELECTIVE for the
        dopaminergic compartment rather than a global neuronal/glial
        effect. In the cohorts with multiple cell types (E-MTAB-7303
        single-cell; GSE157783 SN), compare the V-ATPase module shift in
        DA neurons vs non-DA neurons vs glia. Decision rule: per-donor
        module score × cell-class interaction; report the DA-vs-other
        contrast effect + p + 95% CI. H4 supports selectivity if the DA
        shift significantly exceeds the non-DA shift. A non-selective
        (global) shift is an informative alternative and reported plainly.

    H5 (secondary — honest-failure / sensitivity + power decomposition):
        Decompose the replication into: (i) the achievable
        donor-label-permutation power floor at each cohort's n (the
        original limitation — state the minimum p reachable and whether
        the cohort can even support a significant call); (ii) sensitivity
        to the V-ATPase gene-set definition (the 9-gene CLEAR subset vs a
        broader TFEB/CLEAR target set vs V0-only vs V1-only); (iii)
        normalisation / batch sensitivity (NanoString housekeeping panel
        vs bulk vs single-cell pseudobulk). State explicitly that an
        under-powered null (cannot reject) is NOT the same as a
        replicated reversal, and report which cohorts are merely
        under-powered vs genuinely contradictory.

  A negative or partial result is fully informative. If independent
  cohorts show V-ATPase DOWNregulation (true lysosomal failure rather
  than compensation), or only an under-powered null, THAT is the
  headline — the self-generated compensatory-UP hypothesis must be
  reported as not-replicated rather than retuned (gene-set
  cherry-picking, replicate-level inflation) until it reads UP.

data:
  location: ./research_cases/pd-vatpase-lysosomal-compensation/data
  shared_cache: ./datasets
  description: |
    **No pre-staged raw data on disk.** Every dataset below is publicly
    deposited and MUST be fetched at run time by EXECUTE into the shared
    cache `./datasets/<accession>/`. Use REAL fetched public data only.
    If a dataset or a required covariate (genotype, PD/control status,
    cell-type labels) is blocked, missing, or unparseable, report
    honestly in `analysis.md § Limitations` and fall back to the
    remaining open data — NEVER simulate or fabricate. Acceptable
    reasons to defer a fetch: (1) HTTP 404/410 / accession removed;
    (2) sha256 mismatch; (3) `allow_web` false. **Size alone is NOT a
    reason to skip — and the FINAL/benchmark analysis MUST use the FULL
    dataset (no subsampling; subsampling is for smoke/dev only).** If
    full-data analysis cannot finish within the EXECUTE wall budget on
    CPU, request more CPU/budget rather than subsample or skip. All
    analyses here are CPU-only (NanoString DE, bulk + pseudobulk DE,
    module scoring, AUCell); no GPU is required.

    All reported effects MUST carry effect size + p-value + 95% CI
    together. For the small NanoString cohort, report the MINIMUM
    achievable donor-label-permutation p given n alongside the effect —
    an under-powered null must be labelled as such, never as a reversal.

    **Primary OPEN cohort (independent LRRK2 replication):**

      - **GSE167335 — LRRK2 G2019S isogenic iPSC-DA NanoString panel.**
        iPSC-derived dopaminergic neurons: control, LRRK2 G2019S PD, and
        gene-edited isogenic corrected lines; NanoString nCounter Human
        Neuropathology panel; 10 samples (1 healthy + 2 PD + 2 corrected
        donors × 2–3 differentiations). Same modality as the original
        finding — a true independent replication where the panel covers
        the measured V-ATPase subunits. Availability VERIFIED 2026-06-07
        (GEO HTTP 200).
          https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE167335
        Target path: ./datasets/GSE167335/
        NOTE: targeted panel — it may not measure all 9 CLEAR V-ATPase
        subunits. EXECUTE must verify which subunits are on the panel
        and test the measured subset; report coverage honestly (do NOT
        impute missing subunits).

    **Secondary OPEN cohort (GBA1-mutant arm, independent modality):**

      - **ArrayExpress E-MTAB-7303 (Lang 2020) — iPSC-DA, GBA-N370S.**
        iPSC-derived dopaminergic neurons from GBA-N370S PD patients,
        controls, and idiopathic PD; BOTH single-cell and bulk RNA-seq;
        13 iPSC lines (6 control, 4 GBA-N370S, 4 idiopathic PD). Open
        CC-BY. Availability 2026-06-07: the Lang 2020 Cell Reports paper
        states deposition at E-MTAB-7303 (CC-BY); the BioStudies
        endpoint returned a transient HTTP 500 at check time (server-side,
        NOT a 404 — the legacy ArrayExpress URL 302-redirects, confirming
        the accession is registered). EXECUTE must RETRY at fetch time;
        if E-MTAB-7303 is genuinely unreachable (sustained 404/410),
        report honestly and proceed with H1+H3 (LRRK2 + postmortem) only.
          https://www.ebi.ac.uk/biostudies/arrayexpress/studies/E-MTAB-7303
        Target path: ./datasets/E-MTAB-7303/
        Use the single-cell arm for H4 DA-selectivity and the bulk arm
        for the H2 module DE.

    **Held-out external-validation cohort (postmortem; NOT used to lock
    the module):**

      - **GSE157783 (Smajić 2022) — postmortem SN/midbrain snRNA-seq.**
        Postmortem human midbrain (substantia nigra) single-nucleus
        RNA-seq: 5 idiopathic PD + 6 controls (11 donors). Used ONLY for
        the H3 blind validation of the iPSC-locked V-ATPase module and
        for H4 DA-selectivity in vivo; never used to define the module.
        Availability VERIFIED 2026-06-07 (GEO HTTP 200).
          https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE157783
        Target path: ./datasets/GSE157783/

    **Gated / excluded (flagged, not used):**

      - **Kamath 2022 substantia-nigra atlas (dbGaP-controlled)** — the
        deepest DA-neuron SN atlas, but application-gated. Do NOT
        require it; GSE157783 is the open postmortem SN substitute. No
        hypothesis depends on Kamath.

    **Shared dataset cache convention:** every accession lands under
    `./datasets/<name>/` with a `SOURCE.txt` sidecar recording URL,
    fetch timestamp, sha256, byte count, and parser tool
    (NanoString CSV parser, scanpy.read_h5ad, anndata.read,
    cellxgene/AE downloader, salmon/featureCounts matrix for bulk).
    Re-runs check the cache first.

  cohorts: [gse167335_lrrk2_ipsc_da, e_mtab_7303_gba_ipsc_da, gse157783_postmortem_sn_heldout]
  modalities: [nanostring_ncounter, bulk_rna_seq, snRNA_seq_10x]

success_criteria:
  primary_metric: vatpase_clear_network_direction_replication
  baseline: prior_finding_lrrk2_isogenic_mean_log2fc_plus_0.485
  pre_registration: required
  honest_validation: required
  notes: |
    Primary success = a clear, pre-registered CALL on whether the
    compensatory V-ATPase UP-direction REPLICATES in an independent
    LRRK2 cohort (H1), with donor-level mean log2FC + the achievable
    permutation-p floor + per-gene 95% CI. Because this is confirmatory
    of a self-generated direction, "success" means a defensible
    replicate/not-replicate verdict — a clean failure-to-replicate
    (reversal or genuine null) is just as much a success as a positive
    replication and must be the headline if found.

    H2 success = the GBA1 arm shows the same UP-direction module shift
    (FDR<0.05, CI excluding 0). H3 success = the iPSC-locked module
    transfers to the held-out postmortem SN with a CI-supported PD-vs-
    control effect and/or AUC CI excluding 0.5; a null postmortem
    transfer is reported plainly (iPSC compensation may not persist to
    end-stage tissue). H4 success = a DA-selective shift exceeding non-DA
    cell types; a global shift is an informative alternative.

    CRITICAL honesty rule: distinguish an UNDER-POWERED null (cannot
    reject given small n — state the minimum achievable permutation p)
    from a REPLICATED REVERSAL (significant down). These are different
    conclusions and must not be conflated. All effects reported as
    effect size + p + 95% CI together. Literature/citation target is
    SOFT — comprehensive but no hard count.

figures_planned:
  - id: F1
    title: "V-ATPase / CLEAR-network module across cohorts and modalities"
    panels:
      - "Heatmap of the 9 CLEAR V-ATPase subunits across GSE167335 / E-MTAB-7303 / GSE157783, per genotype/disease group"
      - "Coverage table: which subunits are measured in each modality (NanoString panel vs bulk vs single-cell)"
  - id: F2
    title: "Independent LRRK2 replication of the compensatory UP-direction (H1)"
    panels:
      - "Donor-level forest plot: per-gene log2FC + 95% CI, LRRK2 G2019S vs isogenic corrected, with the prior +0.485 direction marked"
      - "Donor-label permutation null distribution + observed mean log2FC + the achievable minimum-p annotation"
  - id: F3
    title: "GBA1-mutant generalisation (H2)"
    panels:
      - "V-ATPase module effect (g + 95% CI) GBA-N370S vs control, bulk and single-cell pseudobulk side by side"
      - "Volcano of the GBA1 DE with the V-ATPase/CLEAR set annotated"
  - id: F4
    title: "Held-out postmortem-SN blind validation + DA-selectivity (H3, H4)"
    panels:
      - "iPSC-locked V-ATPase module score: PD vs control in held-out GSE157783 DA neurons, effect + 95% CI; ROC + AUC bootstrap CI"
      - "DA vs non-DA vs glia module shift (selectivity) across E-MTAB-7303 and GSE157783"
  - id: F5
    title: "Honest-failure / sensitivity + power-decomposition panel (H5)"
    panels:
      - "Achievable donor-label-permutation power floor vs n per cohort (which cohorts can even reject)"
      - "Gene-set sensitivity: 9-gene CLEAR subset vs broader TFEB/CLEAR set vs V0-only vs V1-only"
      - "Normalisation/batch sensitivity (NanoString housekeeping vs bulk vs pseudobulk) — stability of the direction call"

figures_not_in_scope:
  - "Raw FASTQ re-alignment (processed matrices / objects suffice)"
  - "Wet-lab lysosomal-pH / V-ATPase-assembly validation (transcriptional surrogate only)"
  - "dbGaP-gated Kamath SN arm"
  - "Drug-perturbation (rapamycin/acidic-nanoparticle) rescue experiments"

budgets:
  max_phase_wall_minutes: 240
  max_total_wall_minutes: 480
  allow_web: true
  claude_model: claude-sonnet-4-6
  max_phase_budget_usd: 25.0

auto_approve: true
---

# pd-vatpase-lysosomal-compensation — replicating self-generated V-ATPase compensatory UP

## Source materials

Provenance: **confirmatory replication of a self-generated lab-KB
finding** (the GBA1-LRRK2 lysosomal run). Novelty-checked against the
roster — no existing case tests CLEAR-network V-ATPase compensation
across independent LRRK2/GBA1/postmortem cohorts; this is a deliberate
cross-cohort confirmation of the lab's own direction.

Compounded lab-KB findings (faithfully transcribed):

- Nine CLEAR-network V-ATPase subunits (ATP6V0C, ATP6V0D1, ATP6V0E1,
  ATP6V0E2, ATP6V1A, ATP6V1D, ATP6V1E1, ATP6V1G2, ATP6V1H)
  predominantly UPregulated in LRRK2 G2019S iPSC-DA neurons vs isogenic
  corrected controls.
- Donor-level (replicates collapsed to donor means): 8/9 up, mean
  log2FC = +0.485; permutation power limited by n.
- Not comparison-specific: vs an independent healthy control, +0.452,
  7/9 up.
- Lab-KB direction: GBA1 (Sidransky 2009 NEJM) and LRRK2 (Zimprich 2004)
  both converge on the lysosomal membrane.

Anchor + supporting literature (cite faithfully; soft target):

- Sidransky E. et al. *Multicenter analysis of glucocerebrosidase
  mutations in Parkinson's disease.* NEJM (2009).
- Zimprich A. et al. *Mutations in LRRK2 cause autosomal-dominant
  parkinsonism.* Neuron (2004).
- Sardiello M. et al. *A gene network regulating lysosomal biogenesis
  and function (CLEAR / TFEB).* Science (2009).
- Lang C. et al. *Single-cell transcriptomics of Parkinson's disease
  human in-vitro models reveals dopamine-neuron-specific stress
  responses.* Cell Reports (2020). ArrayExpress E-MTAB-7303 (OPEN,
  CC-BY) — GBA-N370S iPSC-DA arm.
- Smajić S. et al. *Single-cell sequencing of human midbrain reveals
  glial activation and a Parkinson-specific neuronal state.* Brain
  (2022). GEO GSE157783 (OPEN) — postmortem SN.
- GSE167335 — LRRK2 G2019S isogenic iPSC-DA NanoString neuropathology
  panel (OPEN).

## Why this matters for OmiCairn

Self-replication is the strongest test of an autonomous research system:
can OmiCairn take its OWN under-powered finding and either confirm it in
independent cohorts/modalities or honestly retract it? The compensatory
V-ATPase UP-direction is mechanistically important (TFEB/CLEAR rescue of
lysosomal acidification) but was observed in a tiny NanoString cohort.
This case exercises cross-modality replication (NanoString → bulk →
single-cell → postmortem), pre-locked held-out validation, DA-cell
selectivity testing, and — critically — the discipline to separate an
under-powered null from a true reversal.

## Known prior results (cross-check ground truth)

| Claim | Reference |
|---|---|
| 9 CLEAR V-ATPase subunits predominantly UP in LRRK2 G2019S iPSC-DA | lab-KB finding (NanoString isogenic) |
| Donor-level: 8/9 up, mean log2FC = +0.485 | lab-KB finding |
| Not comparison-specific: +0.452, 7/9 up vs independent control | lab-KB finding |
| Permutation power limited by n (cannot reach significance at this n) | lab-KB finding (the limitation this run addresses) |
| GBA1 + LRRK2 both converge on the lysosomal membrane | lab-KB direction (Sidransky 2009; Zimprich 2004) |

## Out of scope (for this iteration)

- Raw FASTQ re-alignment.
- Wet-lab lysosomal-pH / V-ATPase-assembly (FLIM-FRET) validation.
- dbGaP-gated Kamath SN atlas.
- Drug-rescue (rapamycin / acidic-nanoparticle) perturbation experiments.
