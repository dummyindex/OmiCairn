---
research_mode: CONFIRMATORY
domain: neurodegeneration_snRNAseq
topic_slug: neurodeg-microglia-convergence

research_question: |
  Extension of two prior OmiCairn lab-KB findings. (1) A
  Jonckheere–Terpstra test on the SEA-AD Microglia-PVM MTG atlas
  (CellxGene collection 1ca90a2d) found the LDAM module score
  (LGALS3, CD9, GPNMB, NPC2) significantly elevated in APOE4 carriers
  vs non-carriers (JT=993.0, p=0.006), while the DAM2 score (TREM2,
  LPL, CST7) showed no significant carrier shift; a follow-up tau
  regression was directionally positive but null (β_tau=+0.031,
  p=0.143, 95% CI [−0.007, +0.069]). (2) In AD prefrontal cortex
  (GSE157827, 21 donors) the most-disrupted ligand–receptor pairs were
  ECM–integrin interactions (LAMA2→ITGAV/ITGA6, COL4A2→ITGB8;
  microglia/endothelial→astrocyte; log2FC ≈ −2.0). Both findings were
  observed within Alzheimer's disease alone. The open AD/DLB/PDD
  prefrontal-cortex atlas GSE303823 (Brase/Harari-style cohort;
  processed object on Zenodo 11107597) independently reports a shared,
  myelin-enriched microglial state with increased MSR1-mediated
  phagocytosis across all three dementia subtypes.

  This run asks whether the APOE4-linked lipid-droplet microglial
  program (LDAM) is a SHARED cross-disease feature of dementia or an
  AD-specific one, and whether the ECM–integrin disruption is, by
  contrast, AD-specific. Primary discovery cohort: GSE303823 (snRNA-seq
  PFC; NCI control n=4, AD n=4, DLB n=6, PDD n=6; ~20 donors, the
  processed `rna_list.rds` on Zenodo 11107597). Orthogonal AD-arm
  replication: SEA-AD on CellxGene (open; MTG + DLPFC; the
  Microglia-PVM object under collection 1ca90a2d, plus the full atlas).
  Held-out blind external validation: SEA-AD DLPFC (A9) — never used to
  define the microglial states or fit any model.

  Hypotheses (pre-registered):

    H1 (primary — shared vs AD-specific LDAM elevation across 3 diseases):
        The LDAM module score (LGALS3, CD9, GPNMB, NPC2) within the
        microglial compartment is elevated in EACH of AD, DLB and PDD
        relative to NCI controls in GSE303823, i.e. it is a SHARED
        dementia feature rather than AD-restricted.
        Decision rule: per-donor pseudobulk LDAM module score in
        microglia; for each disease vs NCI report Hedges' g effect size
        + p-value + 95% bootstrap CI, with Benjamini–Hochberg FDR
        across the 3 contrasts. H1 (shared) passes if all 3 diseases
        show a positive elevation with FDR<0.05 and 95% CI excluding 0.
        It is DECISIVELY classed AD-specific (the competing model) if
        only AD is significant and DLB/PDD CIs include 0. A partial
        pattern (e.g. AD+DLB but not PDD) is reported verbatim as
        partial sharing — NOT rounded up to "shared".

    H2 (secondary — orthogonal method corroboration):
        The shared/AD-specific LDAM pattern in H1 is reproduced by an
        independent quantification: (a) compositional shift of an
        unsupervised LDAM-like microglial subcluster (Leiden, scored
        post-hoc by the LDAM markers) tested with a
        Dirichlet/scCODA-style per-donor proportion model, and
        (b) SCENIC/regulon or AUCell module activity for the same
        gene set. H2 corroborates H1 if the sign and the
        shared-vs-specific call agree across at least 2 of the 3
        diseases under both orthogonal methods.

    H3 (secondary — held-out blind external validation, AD arm only):
        A signature locked from GSE303823 (the per-gene LDAM weights
        defining the microglial program) is scored, WITHOUT refitting,
        on the held-out SEA-AD DLPFC (A9) atlas to test whether it
        separates high- vs low-pathology AD donors. Report AUC + 1000×
        bootstrap 95% CI. The lock file (gene list + weights + cluster
        definition) is frozen before touching SEA-AD DLPFC. A null AUC
        (CI spanning 0.5) is an informative, reportable outcome.

    H4 (secondary — APOE4-specificity of the lipid-droplet sub-program):
        IF APOE genotype / APOE4-carrier status is present in GSE303823
        and/or SEA-AD donors, the lipid-droplet sub-program (the
        LGALS3/GPNMB-high axis) is enriched specifically in APOE4
        carriers, replicating the prior JT=993.0, p=0.006 SEA-AD
        finding in an independent cohort. Decision rule: carrier vs
        non-carrier Jonckheere–Terpstra (or Mann–Whitney) on the
        per-donor sub-program score, with effect size + p + 95% CI.
        COVARIATE DEPENDENCY: if APOE status is absent, H4 is
        UNTESTABLE — report that honestly and fall back to a
        genotype-agnostic description. Do NOT impute genotype.

    H5 (secondary — NEGATIVE CONTROL: ECM–integrin disruption is
        AD-specific, not shared):
        The ECM–integrin ligand–receptor disruption observed in AD PFC
        (LAMA2→ITGAV/ITGA6, COL4A2→ITGB8; microglia/endothelial→
        astrocyte) is AD-specific and is NOT shared by DLB or PDD.
        Decision rule: per-donor pseudobulk expression of the ECM
        ligands (LAMA2, COL4A2, and the integrin receptors) and/or a
        CellChat-style L-R disruption score, each disease vs NCI;
        report effect size + p + 95% CI. This is a pre-registered
        DISSOCIATION test: H5 passes (supporting the convergence story)
        if AD shows the disruption while DLB/PDD do NOT. If the ECM
        disruption is ALSO shared across diseases, that is an equally
        informative negative-control failure and must be reported as
        the headline finding, not buried.

    H6 (secondary — honest-failure / sensitivity decomposition):
        Decompose the H1 LDAM effect into nuisance vs real-biology
        layers: (i) microglial-proportion differences across disease
        (does the score shift just track more microglia?),
        (ii) post-mortem-interval / age / sex confounds (covariate-
        adjusted re-fit), (iii) Leiden-resolution sweep and
        marker-set perturbation (swap to the expanded LDAM set adding
        APOE/CLU, per the prior sensitivity finding). State explicitly
        that resolution-instability or a proportion-driven artifact is
        itself the headline if it dominates.

  A negative or partial result is fully informative. If the LDAM
  elevation turns out AD-specific (not shared), or the ECM negative
  control fails (disruption is shared after all), or H3 validation is
  null, THAT is the headline result. The analysis must NOT be retuned
  (re-clustering, marker-set cherry-picking) until H1 reads "shared".

data:
  location: ./research_cases/neurodeg-microglia-convergence/data
  shared_cache: ./datasets
  description: |
    **No pre-staged raw data on disk.** Every dataset below is publicly
    deposited and MUST be fetched at run time by EXECUTE into the shared
    cache `./datasets/<accession>/`. Use REAL fetched public data only.
    If a dataset or a required covariate (disease group, APOE status,
    Braak/Thal, PMI) is blocked, missing, or unparseable, report
    honestly in `analysis.md § Limitations` and fall back to the
    remaining open data — NEVER simulate or fabricate. Acceptable
    reasons to defer a fetch: (1) HTTP 404/410 / accession removed;
    (2) sha256 mismatch; (3) `allow_web` false. **Size alone is NOT a
    reason to skip — and the FINAL/benchmark analysis MUST use the FULL
    dataset (no subsampling; subsampling is for smoke/dev only).** If
    full-data analysis cannot finish within the EXECUTE wall budget on
    CPU, the agent must request more CPU/compute or extend the budget
    rather than subsample or skip. These analyses are CPU-only (pseudobulk
    DE, compositional models, AUCell, regulon activity); no GPU is
    required.

    All reported effects MUST carry effect size + p-value + 95% CI
    together. A p-value alone is not a result.

    **Primary OPEN discovery cohort (cross-disease):**

      - **GSE303823 — AD/DLB/PDD/control PFC snRNA-seq.** Postmortem
        prefrontal cortex, single-nucleus RNA-seq; NCI control n=4,
        AD n=4, DLB n=6, PDD n=6 (≈20 donors). The paper's own headline
        is a shared myelin-enriched microglial state + increased
        MSR1-mediated phagocytosis across dementia subtypes.
        Availability VERIFIED 2026-06-07: GEO page HTTP 200; the
        processed Seurat object `rna_list.rds` (1.2 GB) is openly
        downloadable from Zenodo record 11107597 under CC-BY 4.0.
          https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE303823
          https://zenodo.org/records/11107597
        Target path: ./datasets/GSE303823/
        Preferred fetch: the Zenodo `rna_list.rds` (convert to AnnData
        via anndata2ri / sceasy, or analyse in R). Fall back to GEO
        supplementary matrices if the RDS fails to parse.

    **Orthogonal AD-arm replication cohort (OPEN):**

      - **SEA-AD (Gabitto 2024) — open multimodal AD atlas.** Use the
        snRNA-seq MTG arm for the AD-arm replication of H1/H2 and the
        APOE4-specificity test (H4). The Microglia-PVM MTG object was
        used in the prior lab-KB finding (CellxGene collection
        1ca90a2d). Availability: open via CellxGene and the AWS Open
        Data Registry `registry.opendata.aws/allen-sea-ad-atlas/`.
          https://cellxgene.cziscience.com/collections/1ca90a2d
          https://registry.opendata.aws/allen-sea-ad-atlas/
        Target path: ./datasets/seaad_mtg/
        NOTE: SEA-AD encodes APOE4 carrier status (Y/N/Reference), not
        fine APOE genotype — sufficient for the carrier-level H4 test,
        NOT for an APOE4/4-vs-3/3 contrast. EXECUTE must resolve the
        current CellxGene UUID at fetch time (do not assume a stale id).

    **Held-out external-validation cohort (NOT used to define states):**

      - **SEA-AD DLPFC (A9).** The prefrontal (A9) arm of SEA-AD, held
        out entirely from clustering/marker-locking; used ONLY for the
        H3 blind external validation of the GSE303823-locked LDAM
        signature. Same open access route as above.
        Target path: ./datasets/seaad_dlpfc_a9/

    **Gated / excluded (flagged, not used):**

      - **AMP-AD / ROSMAP raw (Synapse, application-gated)** — do NOT
        use raw controlled-access objects; only openly-redistributed
        processed atlases above. No hypothesis depends on gated data.
      - **GSE157827 raw** — the prior ECM finding's cohort is referenced
        for the H5 negative-control comparison context only; the
        cross-disease H5 dissociation is tested WITHIN GSE303823 so it
        does not depend on re-fetching GSE157827.

    **Shared dataset cache convention:** every accession lands under
    `./datasets/<name>/` with a `SOURCE.txt` sidecar recording URL,
    fetch timestamp, sha256, byte count, and parser tool
    (scanpy.read_h5ad, anndata.read, sceasy/anndata2ri for the RDS,
    cellxgene-census, etc.). Re-runs check the cache first.

  cohorts: [gse303823_pfc_AD_DLB_PDD, seaad_mtg, seaad_dlpfc_a9_heldout]
  modalities: [snRNA_seq_10x]

success_criteria:
  primary_metric: shared_vs_ad_specific_ldam_microglial_elevation
  baseline: nci_control_microglial_ldam_module_score
  pre_registration: required
  honest_validation: required
  notes: |
    Primary success = a clean, pre-registered CALL on whether the LDAM
    microglial elevation is SHARED across AD/DLB/PDD or AD-SPECIFIC,
    with per-disease Hedges' g + FDR + 95% CI (H1), corroborated by an
    orthogonal compositional/regulon method (H2). "Success" is reaching
    a defensible call — a clean AD-specific result is just as much a
    success as a clean shared result. The ECM–integrin negative control
    (H5) must dissociate from the microglial program for the
    convergence story to hold; if it does not, that is the headline.

    H3 success = the GSE303823-locked LDAM signature transfers to the
    held-out SEA-AD DLPFC (A9) with AUC 95% CI excluding 0.5; a null
    transfer is reported plainly. H4 is conditional on APOE status
    being present; if absent, mark untestable — do NOT impute.

    All effects reported as effect size + p-value + 95% CI together.
    Partial results (e.g. shared in AD+DLB but not PDD, or a null
    held-out AUC) must be stated plainly in `analysis.md` and
    `negative_results.md`, NOT framed as success. Literature-review
    coverage is SOFT — comprehensive but no hard count; thin coverage of
    the genuinely sparse DLB/PDD microglial literature is acceptable if
    disclosed.

figures_planned:
  - id: F1
    title: "Cross-disease microglial taxonomy (AD vs DLB vs PDD vs control)"
    panels:
      - "Integrated UMAP of microglia across GSE303823 + SEA-AD, colored by subcluster and by disease"
      - "Marker dot plot: LDAM (LGALS3, CD9, GPNMB, NPC2), DAM2 (TREM2, LPL, CST7), and the GSE303823 myelin-microglia / MSR1 program across subclusters"
      - "Per-donor microglial state composition stacked bars, grouped by disease (NCI/AD/DLB/PDD)"
  - id: F2
    title: "Shared vs AD-specific LDAM elevation (H1)"
    panels:
      - "Forest plot: per-disease Hedges' g + 95% CI for the LDAM module score vs NCI (AD, DLB, PDD side by side) — the central shared-vs-specific figure"
      - "Per-donor LDAM module-score violins by disease with FDR annotation"
  - id: F3
    title: "Orthogonal-method corroboration (H2)"
    panels:
      - "scCODA-style per-donor LDAM-subcluster proportion shift by disease"
      - "AUCell / SCENIC regulon activity for the LDAM program by disease (sign + shared-vs-specific call vs H1)"
  - id: F4
    title: "Held-out blind validation + APOE4-specificity (H3, H4)"
    panels:
      - "ROC of the GSE303823-locked LDAM signature on held-out SEA-AD DLPFC (A9), AUC + bootstrap CI"
      - "APOE4-carrier vs non-carrier lipid-droplet sub-program score (replicating JT=993.0, p=0.006) if APOE status present; else covariate-availability note"
  - id: F5
    title: "Negative control: ECM–integrin is AD-specific (H5)"
    panels:
      - "Per-disease effect (g + 95% CI) for LAMA2/COL4A2 + integrin receptors vs NCI — dissociation panel (AD shifts, DLB/PDD do not, if the story holds)"
      - "Side-by-side: microglial LDAM (shared axis) vs ECM–integrin (AD-specific axis) across the 3 diseases"
  - id: F6
    title: "Honest-failure / sensitivity-analysis panel (H6)"
    panels:
      - "LDAM effect before vs after adjusting for microglial proportion, PMI, age, sex"
      - "Leiden-resolution sweep + marker-set perturbation (canonical vs expanded LDAM set adding APOE/CLU) — stability of the shared-vs-specific call"
      - "Covariate-availability audit table per fetched atlas (disease group, APOE status, Braak/Thal, PMI)"

figures_not_in_scope:
  - "Raw FASTQ re-alignment (processed objects suffice)"
  - "snATAC / multiome regulatory analysis (RNA arm only here)"
  - "Wet-lab / iPSC / mouse lipid-droplet validation"
  - "Application-gated AMP-AD/ROSMAP raw arms"

budgets:
  max_phase_wall_minutes: 300
  max_total_wall_minutes: 600
  allow_web: true
  claude_model: claude-sonnet-4-6
  max_phase_budget_usd: 25.0

auto_approve: true
---

# neurodeg-microglia-convergence — shared microglial LDAM state across AD, DLB, PDD

## Source materials

Provenance: **extension of two prior OmiCairn lab-KB findings**
(novelty-checked against the roster — no existing case tests cross-disease
AD/DLB/PDD microglial convergence; the closest, `alzheimer-apoe4-microglia-ldam`,
is AD-only and this case explicitly generalises it).

Compounded lab-KB findings (faithfully transcribed):

- LDAM module score significantly elevated in APOE4 carriers on SEA-AD
  Microglia-PVM MTG (Jonckheere–Terpstra JT=993.0, p=0.006); DAM2 no
  significant carrier shift; tau regression directionally positive but
  null (β_tau=+0.031, p=0.143, 95% CI [−0.007, +0.069]).
- ECM–integrin ligand–receptor disruption dominant in AD PFC
  (GSE157827, 21 donors): LAMA2→ITGAV/ITGA6, COL4A2→ITGB8;
  microglia/endothelial→astrocyte; log2FC ≈ −2.0.

Anchor + supporting literature (cite faithfully; soft target):

- Keren-Shaul H. et al. *A unique microglia type associated with
  restricting development of Alzheimer's disease.* Cell (2017).
  (DAM definition.)
- Marschallinger J. et al. *Lipid-droplet-accumulating microglia (LDAM)
  represent a dysfunctional and proinflammatory state in the aging
  brain.* Nature Neuroscience (2020). (LDAM definition.)
- GSE303823 / Zenodo 11107597 — *Single-nucleus transcriptomics reveals
  a distinct (myelin-enriched) microglial state and increased
  MSR1-mediated phagocytosis as common features across dementia
  subtypes* (2024). Primary cross-disease cohort.
- Gabitto M.I. et al. *SEA-AD: a multimodal cellular atlas and resource
  for Alzheimer's disease.* Nature Neuroscience/Aging (2024). OPEN
  (CellxGene; AWS Open Data).
- Smajić S. et al. and the broader DLB/PDD microglial literature
  (acknowledge genuine sparsity of DLB/PDD-specific microglial
  transcriptomics).

## Why this matters for OmiCairn

Whether neurodegenerative diseases converge on a shared microglial
dysfunction state, or each has a private one, is a central open question
and is informative either way. This case compounds the lab's own AD-only
findings into a cross-disease test, and bakes in a hard honesty guard: a
pre-registered NEGATIVE CONTROL (ECM–integrin should be AD-specific) that
can fail and become the headline. It exercises multi-disease integration,
per-donor pseudobulk + compositional modelling, orthogonal regulon
corroboration, pre-locked held-out external validation, and honest
covariate auditing (APOE status presence).

## Known prior results (cross-check ground truth)

| Claim | Reference |
|---|---|
| LDAM score ↑ in APOE4 carriers, JT=993.0, p=0.006 | lab-KB finding (SEA-AD Microglia-PVM MTG, collection 1ca90a2d) |
| DAM2 score: no significant carrier shift | lab-KB finding (same) |
| Tau regression on LDAM/DAM2 ratio: β=+0.031, p=0.143, CI [−0.007,+0.069] (null) | lab-KB finding |
| ECM–integrin L-R disruption dominant in AD PFC (LAMA2/COL4A2, log2FC≈−2.0) | lab-KB finding (GSE157827, 21 donors) |
| Shared myelin-enriched microglia + MSR1↑ across AD/DLB/PDD | GSE303823 paper (Zenodo 11107597) |

## Out of scope (for this iteration)

- Raw FASTQ re-alignment.
- snATAC / multiome regulatory arms (RNA only).
- iPSC microglia or mouse lipid-droplet wet-lab validation.
- Application-gated AMP-AD/ROSMAP raw objects.
