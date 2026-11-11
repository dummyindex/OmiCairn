---
research_mode: DISCOVERY
domain: neurodegeneration_scRNAseq
topic_slug: alzheimer-sex-apoe4-mito

research_question: |
  Direction d6 from the second Alzheimer's-mechanism IDEATE run
  (workspaces/ideate-alzheimer-mechanism-20260607-044616/000_ideate).

  Do female APOE4 carriers show a distinct and more severe mitochondrial
  bioenergetic gene expression failure in excitatory neurons compared to
  male APOE4 carriers and female APOE3/3 individuals, and does this
  sex × genotype interaction operate at the epigenomic (chromatin) level
  with female-specific APOE4-associated CRE closures around mitochondrial
  gene regulatory regions?

  No prior lab run has addressed sex differences in AD, yet ~two thirds
  of AD patients are female and female APOE4 carriers carry ~1.5x higher
  risk than males with the same genotype. A 2025 paper links female
  APOE4 to accelerated tau spread and greater mitochondrial bioenergetic
  failure; Huang's group and others report that female APOE4 carriers
  show greater mitochondrial energy-production failure and failure of
  adaptive bioenergetic reprogramming. This direction is distinct from
  the lab's microglial APOE4 work. This OmiCairn run quantifies the
  sex × APOE4 interaction at single-cell resolution in excitatory
  neurons using open snRNA-seq stratified by sex and APOE genotype, and
  tests for a female-specific accessibility deficit at OXPHOS gene
  regulatory regions in the open multiome ATAC arm.

  Hypotheses (pre-registered, lifted from d6):

    H1 (primary — sex × APOE4 × Braak OXPHOS interaction):
        In pseudobulk snRNA-seq of excitatory neurons, a 3-way
        interaction (sex × APOE4 × Braak stage) explains a significant
        fraction of variance in mitochondrial oxidative-phosphorylation
        gene expression (OXPHOS module score), with female APOE4
        carriers showing the steepest decline across Braak stages.
        Decision rule: per-donor excitatory-neuron OXPHOS module score
        regressed on sex × APOE4 × Braak (with main effects and lower
        interactions); report the 3-way interaction coefficient +
        p-value + 95% CI and a likelihood-ratio test of the interaction
        term, plus the per-group (female-APOE4 vs others) OXPHOS-vs-Braak
        slope with 95% CI. H1 passes if the 3-way interaction is
        significant (p<0.05, 95% CI excluding 0) AND the female-APOE4
        OXPHOS-vs-Braak slope is the steepest decline among the four
        sex × genotype groups.

    H2 (secondary — female-specific OXPHOS CRE closure):
        Female APOE4 carriers show significantly reduced chromatin
        accessibility at OXPHOS gene promoters and enhancers (measured in
        GSE174367 snATAC-seq) compared to male APOE4 carriers and female
        APOE3/3, with enrichment for estrogen response element (ERE)
        disruption.
        Decision rule: per-donor pseudobulk differential accessibility on
        excitatory-neuron snATAC peaks at OXPHOS gene promoters/enhancers,
        contrasting female-APOE4 vs (male-APOE4 and female-APOE3/3);
        report the accessibility log2FC + FDR + 95% CI, and a
        motif-enrichment test (e.g. chromVAR / HOMER) for the estrogen
        response element (ESR1/ERE) in the differentially accessible
        peaks with enrichment statistic + p-value + 95% CI. H2 passes if
        OXPHOS-region accessibility is significantly reduced in female
        APOE4 (FDR<0.05, 95% CI excluding 0) AND the ERE motif is
        significantly enriched in the closed peaks.

  IMPORTANT — covariate dependency: H1 requires SEX, APOE genotype, and
  Braak/tau metadata in the same donors, with enough donors per
  sex × genotype cell for a stable interaction term; H2 additionally
  requires a usable excitatory-neuron snATAC arm. EXECUTE MUST first
  verify these are present and that group sizes are adequate. If SEX or
  APOE genotype is missing, the interaction cannot be tested — report
  that honestly and fall back to whichever main-effect contrast the
  metadata supports (e.g. APOE4 vs APOE3 OXPHOS without the sex split),
  flagged as not the pre-registered interaction. If group cells are too
  small for a stable 3-way term, report that and fall back to a
  2-way (sex × APOE4) contrast at matched Braak. If no usable snATAC arm
  is fetched, H2 is untestable — report that and restrict to the H1
  transcriptional axis. Do NOT impute or fabricate sex, genotype,
  neuropathology, or peaks.

  A negative or partial result is fully informative. If female APOE4 does
  not show a steeper OXPHOS decline, or no ERE-linked accessibility
  deficit appears, that must be the headline rather than re-tuning module
  definitions or group thresholds until H1/H2 pass.

data:
  location: ./research_cases/alzheimer-sex-apoe4-mito/data
  shared_cache: ./datasets
  description: |
    **No pre-staged raw data on disk.** Every dataset below is publicly
    deposited and MUST be fetched at run time by EXECUTE into the shared
    cache `./datasets/<accession>/`. EXECUTE MUST use REAL fetched
    public data — prefer openly-downloadable GEO supplementary files
    (processed h5ad / matrix + barcodes + features). If a dataset or a
    required covariate (SEX, APOE genotype, Braak/tau) is blocked,
    missing, or unparseable, report that honestly in
    `analysis.md § Limitations` and fall back to the remaining open data
    — NEVER simulate or fabricate. Acceptable reasons to defer a fetch:
    (1) HTTP 404/410 / accession removed; (2) sha256 verification fails;
    (3) `budgets.allow_web` is false. Size alone is NOT acceptable.

    All reported effects MUST carry effect size + p-value + 95% CI
    together. A p-value alone is not a result.

    **Primary OPEN dataset (multiome — RNA + ATAC):**

      - **Morabito 2021 (PFC multiome).**
        GEO **GSE174367** — snRNA-seq + snATAC-seq (191,890 nuclei) of
        prefrontal cortex from AD and control donors. Openly
        downloadable at NCBI GEO. Use the snRNA arm for the H1 OXPHOS
        interaction and the snATAC arm for the H2 OXPHOS-region
        accessibility + ERE motif test.
          https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE174367
        Target path: ./datasets/GSE174367/
        NOTE: H1 needs SEX + APOE genotype + Braak/tau; H2 needs a usable
        excitatory-neuron snATAC arm. EXECUTE must confirm these columns/
        arm exist and that sex × genotype group sizes are adequate BEFORE
        testing; if a covariate or the ATAC arm is absent, or groups are
        too small, report honestly and fall back (see research_question
        above). Sex metadata availability is not guaranteed — check.

    **Optional secondary (sex + neuropathology, candidate):**

      - **SEA-AD (Gabitto 2024).** Open multimodal AD atlas with SEX,
        APOE genotype, snRNA-seq, and quantitative neuropathology
        spanning the severity spectrum; strongest source for a
        well-powered sex × genotype OXPHOS interaction if GSE174367 lacks
        sex metadata or has too few donors per group. Candidate —
        EXECUTE to verify access; report blocked if unavailable, do not
        fabricate. Allen Brain Map portal / AWS open data registry /
        Synapse syn64410371.
          https://portal.brain-map.org/explore/seattle-alzheimers-disease
        Target path: ./datasets/seaad_syn64410371/

    **Shared dataset cache convention:** every accession is downloaded
    into `./datasets/<accession>/` with a sidecar `SOURCE.txt` recording
    URL, fetch timestamp, sha256, byte count, and the parsing tool used
    (scanpy.read_h5ad, anndata.read, scanpy.read_mtx, snapatac2, etc.).
    Subsequent runs check the cache first and skip re-downloads.

  cohorts: [morabito_2021_PFC, seaad_2024_multiregion]
  modalities: [snRNA_seq_10x, snRNA_seq_10x_multiome, snATAC_seq_10x]

success_criteria:
  primary_metric: female_apoe4_excitatory_oxphos_interaction
  baseline: oxphos_canonical_module_marker_set
  pre_registration: required
  honest_validation: required
  notes: |
    Primary success = H1 sex × APOE4 × Braak 3-way interaction on
    excitatory-neuron OXPHOS module score is significant (p<0.05, 95% CI
    excluding 0) AND the female-APOE4 OXPHOS-vs-Braak slope is the
    steepest decline among the four sex × genotype groups, in at least
    one open dataset with adequate group sizes. If SEX or APOE genotype
    metadata is missing, or groups are too small, H1 is untestable as
    specified — report that as the headline limitation and fall back to
    the supported lower-order contrast, not a failure to fix by
    imputation.

    H2 success = excitatory-neuron OXPHOS-region accessibility is
    significantly reduced in female APOE4 (FDR<0.05, 95% CI excluding 0)
    AND the ERE motif is significantly enriched in the closed peaks. If
    no usable snATAC arm is fetched, mark H2 untestable and report the H1
    transcriptional axis only.

    All effects reported as effect size + p-value + 95% CI together.
    Partial results (e.g. sex effect present but no ERE accessibility
    signal) must be stated plainly in `analysis.md` and
    `negative_results.md`, not framed as success.

figures_planned:
  - id: F1
    title: "Excitatory-neuron OXPHOS program by sex and genotype"
    panels:
      - "UMAP of neurons with excitatory subtypes labeled"
      - "OXPHOS module score per excitatory neuron, split by sex × APOE genotype"
      - "Donor counts per sex × genotype × Braak cell (power audit)"
  - id: F2
    title: "Sex × APOE4 × Braak OXPHOS interaction (H1)"
    panels:
      - "Per-donor OXPHOS module score vs Braak, four lines (female/male × APOE4/APOE3) with slope 95% CIs"
      - "3-way interaction coefficient + 95% CI and likelihood-ratio test"
      - "Female-APOE4 vs others OXPHOS slope contrast with 95% CI"
  - id: F3
    title: "Female-specific OXPHOS CRE closure (H2)"
    panels:
      - "Excitatory-neuron snATAC accessibility at OXPHOS promoters/enhancers, female-APOE4 vs others, log2FC + 95% CI"
      - "Motif-enrichment bar plot for ESR1/ERE in differentially closed peaks, enrichment + 95% CI"
      - "Accessibility track at a representative OXPHOS gene regulatory region by group"
  - id: F4
    title: "Covariate-availability and honest-failure panel"
    panels:
      - "Covariate audit table: SEX / APOE genotype / Braak / snATAC-arm availability + group sizes per fetched atlas"
      - "Sensitivity of the interaction to OXPHOS module-set definition"
      - "Sensitivity of the interaction to Braak binning and group-size thresholds"

figures_not_in_scope:
  - "Raw FASTQ re-alignment (processed objects suffice)"
  - "Direct mitochondrial respirometry / Seahorse assays — wet-lab"
  - "Hormone / estrogen intervention or iPSC neuron validation — wet-lab"

budgets:
  max_phase_wall_minutes: 240
  max_total_wall_minutes: 360
  allow_web: true
  claude_model: claude-sonnet-4-6
  max_phase_budget_usd: 25.0

auto_approve: true
---

# alzheimer-sex-apoe4-mito — sex × APOE4 mitochondrial failure in neurons (IDEATE d6)

## Source materials

Transcribed faithfully from IDEATE direction **d6**
(`workspaces/ideate-alzheimer-mechanism-20260607-044616/000_ideate/directions.json`).
Anchor and supporting literature lifted from the direction's `cites`:

- *APOE ε4 allele drives female-specific Alzheimer's disease progression
  via vascular dysfunction and tau spreading* (2025). (Female APOE4:
  accelerated tau spread, greater mitochondrial bioenergetic failure.)
- *Cell type-specific roles of APOE4 in Alzheimer disease.* Nature
  Reviews Neuroscience (2024).
- Yadong Huang (Gladstone): APOE4 cell-type-specific pathology; female
  biology amplifies APOE4-linked neurodegeneration (2024 Sobrato Prize).
- Gabitto M.I., Travaglini K.J. et al. *Integrated multimodal cell atlas
  of Alzheimer's disease.* Nature Neuroscience (2024). SEA-AD.
- Morabito S. et al. *Single-nucleus chromatin accessibility and
  transcriptomic characterization of Alzheimer's disease.* Nature
  Genetics (2021). GEO GSE174367 (snRNA + snATAC multiome, PFC).

## Why this matters for OmiCairn

Sex is one of the strongest unexplored modifiers of AD risk, and the
female × APOE4 interaction — converging on neuronal mitochondrial
bioenergetic failure — is mechanistically promising but absent from the
lab's prior work. This case asks OmiCairn to test the sex × APOE4 × Braak
interaction on excitatory-neuron OXPHOS directly in open human snRNA-seq,
and to ask whether the female-specific deficit is written in chromatin
at OXPHOS regulatory regions (with an estrogen-response-element motif
test). The answer is informative either way, and the case deliberately
stresses honest handling of missing sex metadata, underpowered
sex × genotype cells, and a missing snATAC arm.

OmiCairn challenges this exercises:

1. **Three-way interaction modelling** (sex × genotype × stage) on a
   module score, with explicit power auditing of group sizes.
2. **Per-donor pseudobulk** module scoring rather than per-cell.
3. **snATAC differential accessibility + motif enrichment** at OXPHOS
   regulatory regions for H2.
4. **Honest covariate/power auditing** — H1 needs sex + genotype + Braak
   and adequate group sizes; H2 needs a usable snATAC arm; the agent
   must mark them untestable and fall back to the supported lower-order
   contrast rather than impute.

## Critical correctness rules (EXECUTE must obey)

- Use REAL fetched public data only. Prefer openly-downloadable GEO
  supplementary files. If blocked/missing → say so in Limitations and
  fall back; NEVER simulate.
- Report effect size + p-value + 95% CI together for every claim.
- Audit covariate availability (SEX, APOE genotype, Braak/tau), snATAC-
  arm availability, and per-group donor counts BEFORE asserting H1/H2
  results; if a covariate or modality is missing or a group is
  underpowered, mark the relevant hypothesis untestable and fall back to
  the supported lower-order contrast or a descriptive analysis.
- Never fabricate accessions. SEA-AD is a candidate — verify at fetch
  time; report blocked if unavailable.

## Out of scope (for this iteration)

- Raw FASTQ re-alignment.
- Direct mitochondrial respirometry / Seahorse assays.
- Hormone / estrogen intervention or iPSC neuron validation.
